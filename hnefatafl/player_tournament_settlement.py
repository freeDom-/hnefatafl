"""Durable, bounded tournament settlement independent of browser lifetimes."""

import logging
import threading
import time
from datetime import timedelta

from .online_core import DeploymentLocked, micros

LOGGER = logging.getLogger(__name__)

RECOVERY_RETRY_SECONDS = 1.0
RECOVERY_RETRY_MAX_SECONDS = 60.0


class TournamentSettlement:
    def __init__(self, core):
        self.core = core
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None
        self._recovery_lock = threading.Lock()
        self._recovered = threading.Event()
        self._recovery_deferred = False
        self._retry_delay = RECOVERY_RETRY_SECONDS
        self._next_recovery_at = 0.0

    @property
    def recovered(self) -> bool:
        """Whether startup reconciliation completed in this worker lifecycle."""
        return self._recovered.is_set()

    def recover(self) -> bool:
        """Run the one startup reconciliation, deferring only on deployment.

        The updater holds the exclusive gate across activation and readiness.
        Starting the API must not fail in that window, so expected lock
        contention leaves the durable work untouched for the background worker
        to retry after the updater commits. Database, integrity and programming
        errors are not expected contention and must still propagate.
        """
        with self._recovery_lock:
            try:
                with self.core._write() as c:
                    c.execute(
                        """INSERT INTO player_tournament_settlement_queue(tournament_id,due_at)
                        SELECT DISTINCT m.tournament_id,? FROM player_tournament_legs l
                        JOIN player_tournament_matches m ON m.id=l.match_id
                        JOIN player_tournaments t ON t.id=m.tournament_id
                        JOIN online_games g ON g.id=l.game_id
                        WHERE t.status IN ('running','complete') AND t.review_state='ok'
                        AND g.result_revision IS NOT NULL
                        AND (l.result_revision IS NULL OR l.result_revision<>g.result_revision)
                        ON CONFLICT(tournament_id) DO NOTHING""",
                        (micros(self.core._now()),),
                    )
            except DeploymentLocked:
                if not self._recovery_deferred:
                    LOGGER.info(
                        "Tournament settlement startup reconciliation deferred during deployment"
                    )
                self._recovery_deferred = True
                return False
            self._recovered.set()
            self._retry_delay = RECOVERY_RETRY_SECONDS
            self._next_recovery_at = 0.0
            if self._recovery_deferred:
                self._recovery_deferred = False
                LOGGER.info("Tournament settlement startup reconciliation completed")
            return True

    def _recover_if_needed(self) -> bool:
        """Bounded retry for a deferred or failed startup reconciliation."""
        if self._recovered.is_set():
            return True
        if time.monotonic() < self._next_recovery_at:
            return False
        try:
            if self.recover():
                return True
        except Exception as error:
            # The retry is durable work, not a swallowed failure: keep the
            # recovery incomplete, log only its category and back off.
            delay = min(max(RECOVERY_RETRY_SECONDS, self._retry_delay), RECOVERY_RETRY_MAX_SECONDS)
            self._retry_delay = min(delay * 2, RECOVERY_RETRY_MAX_SECONDS)
            self._next_recovery_at = time.monotonic() + delay
            LOGGER.warning(
                "Tournament settlement startup reconciliation failed (%s); retrying",
                type(error).__name__,
            )
            return False
        self._next_recovery_at = time.monotonic() + RECOVERY_RETRY_SECONDS
        return False

    def drain(self, limit=20):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Settlement batch must be between 1 and 100")
        now = self.core._now()
        with self.core.store.connection() as c:
            identifiers = [
                row[0]
                for row in c.execute(
                    "SELECT tournament_id FROM player_tournament_settlement_queue WHERE due_at<=? ORDER BY due_at,tournament_id LIMIT ?",
                    (micros(now), limit),
                )
            ]
        completed = 0
        for identifier in identifiers:
            if self.stop.is_set():
                break
            try:
                # Serialize consumers and all bracket/online writes. Delete only
                # after successful advancement, in the same transaction.
                with self.core._write() as c:
                    now = self.core._now()
                    queued = c.execute(
                        "SELECT 1 FROM player_tournament_settlement_queue WHERE tournament_id=? AND due_at<=?",
                        (identifier, micros(now)),
                    ).fetchone()
                    if queued is None:
                        continue
                    tournament = self.core._load(c, identifier)
                    if tournament["status"] in ("running", "complete"):
                        self.core.settle_committed(c, identifier, None, now)
                    c.execute(
                        "DELETE FROM player_tournament_settlement_queue WHERE tournament_id=?",
                        (identifier,),
                    )
                    self.core._audit(c, identifier, None, "automatic_settlement", now)
                    completed += 1
            except DeploymentLocked:
                # Expected deployment contention: keep durable work for retry
                # and stop this batch without recording a failed settlement.
                break
            except Exception:
                # No account data, admin notes or exception payload in logs or
                # durable status. Keep work for retry across restart.
                LOGGER.warning("Tournament settlement deferred")
                try:
                    with self.core._write() as c:
                        row = c.execute(
                            "SELECT attempts FROM player_tournament_settlement_queue WHERE tournament_id=?",
                            (identifier,),
                        ).fetchone()
                        if row:
                            delay = min(300, 2 ** min(row[0] + 1, 8))
                            c.execute(
                                "UPDATE player_tournament_settlement_queue SET attempts=attempts+1,due_at=?,last_error='settlement_failed' WHERE tournament_id=?",
                                (micros(now + timedelta(seconds=delay)), identifier),
                            )
                except DeploymentLocked:
                    pass
                except Exception:
                    LOGGER.warning("Tournament settlement retry could not be recorded")
        return completed

    def start_due(self, limit=20):
        """Consume indexed, durable starts, rechecking eligibility under the writer lock."""
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Start batch must be between 1 and 100")
        with self.core.store.connection() as c:
            identifiers = [
                row[0]
                for row in c.execute(
                    """SELECT id FROM player_tournaments INDEXED BY player_tournaments_start_due WHERE status='registration'
                AND start_due_at IS NOT NULL AND start_due_at<=? ORDER BY start_due_at,id LIMIT ?""",
                    (micros(self.core._now()), limit),
                )
            ]
        completed = 0
        for identifier in identifiers:
            if self.stop.is_set():
                break
            try:
                with self.core._write() as c:
                    now = self.core._now()  # Sample after locking, like manual starts.
                    row = self.core._load(c, identifier)
                    if (
                        row["status"] != "registration"
                        or row["start_due_at"] is None
                        or row["start_due_at"] > micros(now)
                    ):
                        continue
                    count = c.execute(
                        "SELECT COUNT(*) FROM player_tournament_entrants WHERE tournament_id=? AND status='registered'",
                        (identifier,),
                    ).fetchone()[0]
                    if count < row["capacity"] or count < 2:
                        # A missed start remains consumed until the creator reschedules.
                        # Unfilled competitions do not become periodic scanning work.
                        c.execute(
                            "UPDATE player_tournaments SET start_due_at=NULL WHERE id=?",
                            (identifier,),
                        )
                        continue
                    self.core._start(c, row, now)
                    completed += 1
            except DeploymentLocked:
                break
            except Exception:
                LOGGER.warning("Tournament scheduled start deferred")
                try:
                    with self.core._write() as c:
                        row = self.core._load(c, identifier)
                        if row["status"] == "registration" and row["start_due_at"] is not None:
                            delay = min(300, 2 ** min(row["start_attempts"] + 1, 8))
                            c.execute(
                                """UPDATE player_tournaments SET start_attempts=start_attempts+1,
                            start_due_at=?,start_error='start_failed' WHERE id=?""",
                                (micros(self.core._now() + timedelta(seconds=delay)), identifier),
                            )
                except DeploymentLocked:
                    pass
                except Exception:
                    LOGGER.warning("Tournament scheduled start retry could not be recorded")
        return completed

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop.clear()

        def run():
            while not self.stop.wait(RECOVERY_RETRY_SECONDS):
                if not self._recover_if_needed():
                    continue
                try:
                    self.start_due()
                    self.drain()
                except DeploymentLocked:
                    # A new activation may start after recovery. Normal polling
                    # resumes automatically when the exclusive gate is released.
                    continue
                except Exception:
                    LOGGER.warning("Tournament settlement worker deferred")

        self.thread = threading.Thread(target=run, name="player-tournament-settlement", daemon=True)
        self.thread.start()

    def shutdown(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=17)
