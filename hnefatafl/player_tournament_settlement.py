"""Durable, bounded tournament settlement independent of browser lifetimes."""

import logging
import threading
from datetime import timedelta

from .online_core import micros

LOGGER = logging.getLogger(__name__)


class TournamentSettlement:
    def __init__(self, core):
        self.core = core
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None

    def recover(self):
        """One startup reconciliation for pre-worker results or restored snapshots."""
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
                except Exception:
                    LOGGER.warning("Tournament settlement retry could not be recorded")
        return completed

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stop.clear()

        def run():
            while not self.stop.wait(1):
                try:
                    self.drain()
                except Exception:
                    LOGGER.warning("Tournament settlement worker deferred")

        self.thread = threading.Thread(target=run, name="player-tournament-settlement", daemon=True)
        self.thread.start()

    def shutdown(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=17)
