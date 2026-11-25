"""Server-owned queue and bounded automatic processing for official online leagues."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
import logging
from math import ceil
import sqlite3
from threading import Event
from time import monotonic
from typing import NamedTuple, TypedDict
from uuid import UUID, uuid4

from . import league_policy as policy
from . import lifecycle, rating
from .engine import RULESETS
from .league_migrations import RATING_BUCKET
from .league_results import settle_result
from .online_core import DeploymentLocked, OnlineCore, OnlineError, instant, micros, stamp
from .security_context import online_eligible, online_ineligibility

LOGGER = logging.getLogger(__name__)
MATCH_STARTERS = 8
MATCH_OPPONENTS = 8
MATCH_TRANSACTIONS = 16
MATCH_CYCLE_SECONDS = 0.060
MATCH_YIELD_SECONDS = 0.003
MATCH_LOCK_WAIT_MS = 20
MATCH_MAX_BACKOFF_SECONDS = 8
ELIGIBILITY_SCAN = 128
WINDOW_INTERVAL_US = policy.RATING_WINDOW_INTERVAL_SECONDS * 1_000_000


class OpponentPage(NamedTuple):
    matched: bool
    more: bool = False
    window_at: int | None = None


class MatchPage(NamedTuple):
    matched: bool = False
    more_starters: bool = False
    more_opponents: bool = False
    window_at: int | None = None
    unavailable: bool = False


@dataclass
class MatchSweep:
    cutoff: int | None = None
    matched: bool = False
    more_opponents: bool = False
    window_at: int | None = None
    failures: int = 0
    retry_after: float = 0.0


def queue_eligibility_page(c, after: int, limit: int) -> list[sqlite3.Row]:
    return c.execute(
        """SELECT q.rowid AS position,q.account_id,q.request_id,q.ruleset,q.joined_at,q.expires_at,
            a.active,a.role,a.email_verified_at
        FROM league_queue q JOIN accounts a ON a.id=q.account_id
        WHERE q.rowid>? ORDER BY q.rowid LIMIT ?""",
        (after, limit),
    ).fetchall()


def _busy(error: sqlite3.OperationalError) -> bool:
    return getattr(error, "sqlite_errorcode", 0) & 255 == sqlite3.SQLITE_BUSY


class LeagueWork(TypedDict):
    settled: int
    expired: int
    reconciled: int
    matched: int
    deferred: bool
    settlement_retries: int


class Leagues:
    def __init__(self, online: OnlineCore):
        self.online = online
        self.store = online.store
        self._settlement_after: tuple[int, str] | None = None
        self._eligibility_after = 0
        self._match_sweeps: dict[str, MatchSweep] = {}
        self._matching_version: int | None = None
        self._next_ruleset = 0

    @staticmethod
    def _ruleset(ruleset: str) -> None:
        if ruleset not in RULESETS:
            raise OnlineError(422, "A supported league ruleset is required.")

    @staticmethod
    def _request_id(request_id: str) -> str:
        try:
            return str(UUID(request_id))
        except (TypeError, ValueError, AttributeError):
            raise OnlineError(422, "A league request UUID is required.") from None

    @staticmethod
    def _season(c, now: datetime) -> policy.Season:
        season = policy.season_for(now)
        c.execute(
            "INSERT OR IGNORE INTO league_seasons(id,starts_at,ends_at,grace_ends_at,policy_version) VALUES(?,?,?,?,?)",
            (
                season.identifier,
                micros(season.start),
                micros(season.end),
                micros(season.grace_end),
                policy.POLICY_VERSION,
            ),
        )
        return season

    @staticmethod
    def _cancel(c, account_id: str, status: str, request_id: str | None = None) -> bool:
        entry = c.execute(
            "SELECT request_id FROM league_queue WHERE account_id=?", (account_id,)
        ).fetchone()
        if entry is None or (request_id is not None and entry[0] != request_id):
            return False
        c.execute(
            """UPDATE league_requests SET status=?
            WHERE account_id=? AND entry_id=? AND status='queued'""",
            (status, account_id, entry[0]),
        )
        c.execute("DELETE FROM league_queue WHERE account_id=?", (account_id,))
        return True

    def _admission_enabled(self) -> bool:
        return bool(self.online.settings.enabled and self.online.settings.creation_policy_approved)

    def _clocks_enabled(self, c, now: datetime) -> bool:
        try:
            return self.online._gate_enabled(c, now)
        except OnlineError as error:
            if error.status != 503:
                raise
            return False

    @staticmethod
    def _queue_entry(c, account_id: str):
        return c.execute("SELECT * FROM league_queue WHERE account_id=?", (account_id,)).fetchone()

    @staticmethod
    def _latest_request(c, account_id: str):
        return c.execute(
            """SELECT * FROM league_requests INDEXED BY league_requests_actor
            WHERE account_id=? ORDER BY rowid DESC LIMIT 1""",
            (account_id,),
        ).fetchone()

    @staticmethod
    def _active_game(c, account_id: str):
        return c.execute(
            """SELECT a.game_id,l.ruleset FROM league_active_players a
            JOIN league_games l ON l.game_id=a.game_id JOIN online_games g ON g.id=a.game_id
            WHERE a.account_id=? AND g.status='active'""",
            (account_id,),
        ).fetchone()

    @staticmethod
    def _queue_times(entry, now: datetime) -> dict:
        if entry["joined_at"] is None:
            return {"joined_at": None, "expires_at": None, "rating_window": None}
        return {
            "joined_at": stamp(instant(entry["joined_at"])),
            "expires_at": stamp(instant(entry["expires_at"])),
            "rating_window": policy.rating_window((micros(now) - entry["joined_at"]) / 1e6),
        }

    def queue_snapshot(
        self, c, actor: dict, now: datetime, request: sqlite3.Row | None = None
    ) -> dict:
        """Project live state and any selected historical request in one snapshot."""
        account_id = actor["id"]
        latest = self._latest_request(c, account_id)
        active = self._active_game(c, account_id)
        active_game = dict(active) if active is not None else None
        eligible = online_eligible(actor)
        entry = self._queue_entry(c, account_id)
        queue = None
        if entry is not None:
            status = "queued"
            if entry["expires_at"] <= micros(now):
                status = "expired"
            elif not eligible:
                status = "cancelled"
            queue = {
                "status": status,
                "ruleset": entry["ruleset"],
                "request_id": entry["request_id"],
                **self._queue_times(entry, now),
            }
        context = {
            "queue": queue,
            "active_game": active_game,
            "join_generation": latest["id"] if latest is not None else None,
            "eligible": eligible,
            "enabled": self._admission_enabled(),
            "paused": bool(
                c.execute("SELECT paused FROM league_settings WHERE id=1").fetchone()[0]
            ),
            "clocks_enabled": self._clocks_enabled(c, now),
            "server_time": stamp(now),
        }
        if request is None and queue is None and active_game is None:
            # On reload keep the latest expiry/cancellation visible. Historical
            # game links belong to explicitly targeted request outcomes.
            if latest is not None and latest["status"] in {"expired", "cancelled"}:
                request = latest
        if request is not None:
            status = request["status"]
            if status == "queued":
                status = (
                    queue["status"]
                    if queue is not None and request["entry_id"] == queue["request_id"]
                    else "cancelled"
                )
            outcome = {
                "status": status,
                "ruleset": request["ruleset"],
                "request_id": request["id"],
                **self._queue_times(request, now),
            }
            if request["game_id"] is not None:
                outcome["game_id"] = request["game_id"]
            return {**context, **outcome}
        if active_game is not None:
            return {**context, "status": "matched", **active_game}
        return {**context, **(queue or {"status": "idle"})}

    def queue_status(self, credential: str) -> dict:
        with self.store.connection() as c:
            c.execute("BEGIN")
            actor = self.online._identity(c, credential)
            return self.queue_snapshot(c, actor, self.online._now())

    @staticmethod
    def _check_request_quota(c, account_id: str, now: datetime) -> None:
        day = micros(now.replace(hour=0, minute=0, second=0, microsecond=0))
        minute = micros(now - timedelta(seconds=policy.REQUEST_WINDOW_SECONDS))
        requests = c.execute(
            """SELECT sum(created_at>=?),sum(created_at>?)
            FROM league_requests WHERE account_id=? AND created_at>=?""",
            (day, minute, account_id, min(day, minute)),
        ).fetchone()
        if (requests[0] or 0) >= policy.REQUESTS_PER_UTC_DAY or (
            requests[1] or 0
        ) >= policy.REQUESTS_PER_MINUTE:
            raise OnlineError(429, "League queue request limit reached. Try again later.")

    def join(
        self,
        credential: str,
        ruleset: str,
        request_id: str,
        *,
        expected_generation: str | None = None,
        guarded: bool = False,
    ) -> dict:
        self._ruleset(ruleset)
        request_id = self._request_id(request_id)
        with self.online._write() as c:
            actor = self.online._identity(c, credential)
            account_id = actor["id"]
            now = self.online._now()
            at = micros(now)
            previous = c.execute(
                "SELECT * FROM league_requests WHERE id=?", (request_id,)
            ).fetchone()
            if previous is not None:
                if previous["account_id"] != account_id or previous["ruleset"] != ruleset:
                    raise OnlineError(409, "The request cannot be reused for this queue entry.")
                return self.queue_snapshot(c, actor, now, previous)
            if guarded:
                latest = self._latest_request(c, account_id)
                if expected_generation != (latest["id"] if latest is not None else None):
                    raise OnlineError(409, "The league queue generation has changed.")
            if not online_eligible(actor):
                raise OnlineError(403, "An active account with a verified email is required.")
            self.online._admission()
            if self._active_game(c, account_id) is not None:
                raise OnlineError(409, "Finish your current league game before queuing again.")
            if c.execute("SELECT paused FROM league_settings WHERE id=1").fetchone()[0]:
                raise OnlineError(503, "League matchmaking is paused.")
            if not self._clocks_enabled(c, now):
                raise OnlineError(503, "Online clocks are paused; league matching is unavailable.")
            queued = self._queue_entry(c, account_id)
            if queued is not None and queued["expires_at"] <= at:
                self._cancel(c, account_id, "expired", queued["request_id"])
                queued = None
            if queued is not None and queued["ruleset"] != ruleset:
                raise OnlineError(409, "Cancel your current league queue before changing ruleset.")
            self._check_request_quota(c, account_id, now)
            if queued is not None:
                entry_id, joined_at, expires_at = (
                    queued["request_id"],
                    queued["joined_at"],
                    queued["expires_at"],
                )
            else:
                season = self._season(c, now)
                entry_id, joined_at = request_id, at
                expires_at = min(micros(season.end), at + policy.QUEUE_SECONDS * 1000000)
            c.execute(
                """INSERT INTO league_requests(id,account_id,ruleset,created_at,status,entry_id,joined_at,expires_at)
                VALUES(?,?,?,?,'queued',?,?,?)""",
                (request_id, account_id, ruleset, at, entry_id, joined_at, expires_at),
            )
            if queued is None:
                # Reviewed preview builds retained finished seats until the
                # worker settled the result. An authoritative inactive seat
                # must not block a new entry while that work is pending.
                c.execute("DELETE FROM league_active_players WHERE account_id=?", (account_id,))
                current_rating = self.online.ratings._state(c, account_id, ruleset)[0]
                c.execute(
                    """INSERT INTO league_queue(account_id,request_id,ruleset,joined_at,expires_at,rating)
                    VALUES(?,?,?,?,?,?)""",
                    (account_id, request_id, ruleset, joined_at, expires_at, current_rating),
                )
            self._match(c, now, ruleset)
            request = c.execute(
                "SELECT * FROM league_requests WHERE id=?", (request_id,)
            ).fetchone()
            return self.queue_snapshot(c, actor, now, request)

    def cancel(self, credential: str, request_id: str) -> dict:
        request_id = self._request_id(request_id)
        with self.online._write() as c:
            actor = self.online._identity(c, credential)
            now = self.online._now()
            request = c.execute(
                "SELECT * FROM league_requests WHERE id=? AND account_id=?",
                (request_id, actor["id"]),
            ).fetchone()
            if request is None:
                raise OnlineError(404, "League queue request not found.")
            return self._cancel_request(c, actor, now, request)

    def _cancel_request(self, c, actor: dict, now: datetime, request: sqlite3.Row) -> dict:
        if request["status"] == "queued":
            status = "expired" if request["expires_at"] <= micros(now) else "cancelled"
            self._cancel(c, actor["id"], status, request["entry_id"])
            request = c.execute(
                "SELECT * FROM league_requests WHERE id=?", (request["id"],)
            ).fetchone()
        return self.queue_snapshot(c, actor, now, request)

    def discard(self, credential: str, ruleset: str, request_id: str, generation: str | None):
        """Resolve an uncertain join under the same writer lock as admission."""
        self._ruleset(ruleset)
        request_id = self._request_id(request_id)
        with self.online._write() as c:
            actor = self.online._identity(c, credential)
            account_id, now = actor["id"], self.online._now()
            request = c.execute(
                "SELECT * FROM league_requests WHERE id=?", (request_id,)
            ).fetchone()
            if request is not None:
                if request["account_id"] != account_id or request["ruleset"] != ruleset:
                    raise OnlineError(409, "The request cannot be reused for this queue entry.")
                return self._cancel_request(c, actor, now, request)
            latest = self._latest_request(c, account_id)
            if generation == (latest["id"] if latest is not None else None):
                self._check_request_quota(c, account_id, now)
                # Record the original UUID as terminal and advance the generation.
                # A late join is now an exact replay, and guarded sibling intents
                # from the same generation cannot start a queue either.
                c.execute(
                    """INSERT INTO league_requests(id,account_id,ruleset,created_at,status,entry_id)
                    VALUES(?,?,?,?,'cancelled',?)""",
                    (request_id, account_id, ruleset, micros(now), request_id),
                )
            return self.queue_snapshot(c, actor, now)

    def _valid_entry(self, c, account_id: str, ruleset: str, at: int, starts_at: int):
        # The rating index is a discovery hint. Re-read both actual ratings,
        # eligibility and active seats under the same lock used to commit a game.
        row = c.execute(
            """SELECT q.*,a.active,a.email_verified_at,a.role,
            COALESCE(r.rating,?) AS live_rating,
            EXISTS(SELECT 1 FROM league_active_players p JOIN online_games g ON g.id=p.game_id
                WHERE p.account_id=q.account_id AND g.status='active') AS playing
            FROM league_queue q JOIN accounts a ON a.id=q.account_id
            LEFT JOIN player_ratings r ON r.account_id=q.account_id AND r.pool=q.ruleset
            WHERE q.account_id=? AND q.ruleset=?""",
            (rating.INITIAL_RATING, account_id, ruleset),
        ).fetchone()
        if row is None:
            return None
        if row["expires_at"] <= at or row["joined_at"] < starts_at:
            self._cancel(c, account_id, "expired", row["request_id"])
            return None
        if not online_eligible(dict(row)) or row["playing"]:
            self._cancel(c, account_id, "cancelled", row["request_id"])
            return None
        return row if row["joined_at"] <= at else None

    @staticmethod
    def _opponent_page(c, first, window: int) -> list[sqlite3.Row]:
        account_id = first["account_id"]
        after = (first["joined_at"], account_id)
        if first["opponent_joined_at"] is not None:
            after = (first["opponent_joined_at"], first["opponent_account"])
        bucket_size = policy.RATING_WINDOW_INITIAL
        first_bucket = int((first["live_rating"] - window) / bucket_size)
        last_bucket = int((first["live_rating"] + window) / bucket_size)
        rows = []
        for bucket in range(first_bucket, last_bucket + 1):
            rows.extend(
                c.execute(
                    f"""SELECT joined_at,account_id FROM league_queue INDEXED BY league_queue_rating_age
                    WHERE ruleset=? AND {RATING_BUCKET}=? AND (joined_at,account_id)>(?,?)
                    ORDER BY joined_at,account_id LIMIT ?""",
                    (first["ruleset"], bucket, *after, MATCH_OPPONENTS + 1),
                ).fetchall()
            )
        return sorted(rows, key=lambda row: (row["joined_at"], row["account_id"]))

    @staticmethod
    def _first_attacks(c, pair: tuple[str, str], ruleset: str) -> bool:
        balances = dict(
            c.execute(
                "SELECT account_id,balance FROM league_side_balance WHERE account_id IN (?,?) AND ruleset=?",
                (*pair, ruleset),
            )
        )
        first, second = (balances.get(account, 0) for account in pair)
        # Giving the attack to the lower balance minimizes the combined squared
        # imbalance. On a tie, alternate the pair's last committed assignment.
        if first != second:
            return first < second
        previous = c.execute(
            """SELECT first_attacked FROM league_side_pairs
            WHERE first_account=? AND second_account=? AND ruleset=?""",
            (*pair, ruleset),
        ).fetchone()
        if previous is not None:
            return not previous[0]
        return bool(sha256(f"{ruleset}:{pair[0]}:{pair[1]}".encode()).digest()[0] & 1)

    def _seat(self, c, first, second, now: datetime) -> None:
        a, b = sorted((first["account_id"], second["account_id"]))
        pair = (a, b)
        ruleset = first["ruleset"]
        first_attacks = self._first_attacks(c, pair, ruleset)
        season = self._season(c, now)
        game_id = self.online.seat_game(
            c,
            creator_id=a,
            opponent_id=b,
            ruleset=ruleset,
            creator_side="attackers" if first_attacks else "defenders",
            control=lifecycle.TimeControl(policy.CLOCK_SECONDS, "total", policy.INCREMENT_SECONDS),
            now=now,
            request_id=str(uuid4()),
            rated=True,
        )
        c.execute(
            """INSERT INTO league_games(game_id,season_id,ruleset,first_account,second_account,paired_at)
            VALUES(?,?,?,?,?,?)""",
            (game_id, season.identifier, ruleset, *pair, micros(now)),
        )
        c.execute(
            """INSERT INTO league_side_pairs VALUES(?,?,?,?)
            ON CONFLICT(first_account,second_account,ruleset) DO UPDATE SET
            first_attacked=excluded.first_attacked""",
            (*pair, ruleset, int(first_attacks)),
        )
        c.executemany(
            """INSERT INTO league_side_balance VALUES(?,?,?)
            ON CONFLICT(account_id,ruleset) DO UPDATE SET balance=balance+excluded.balance""",
            ((a, ruleset, 1 if first_attacks else -1), (b, ruleset, -1 if first_attacks else 1)),
        )
        for entry in (first, second):
            account_id = entry["account_id"]
            c.execute("INSERT INTO league_active_players VALUES(?,?)", (account_id, game_id))
            c.execute(
                """UPDATE league_requests SET status='matched',game_id=?
                WHERE account_id=? AND entry_id=? AND status='queued'""",
                (game_id, account_id, entry["request_id"]),
            )
            c.execute("DELETE FROM league_queue WHERE account_id=?", (account_id,))

    def _match(self, c, now: datetime, ruleset: str, cutoff: int | None = None) -> MatchPage:
        paused = c.execute("SELECT paused FROM league_settings WHERE id=1").fetchone()[0]
        if paused or not self._admission_enabled() or not self._clocks_enabled(c, now):
            return MatchPage(unavailable=True)
        at = micros(now)
        starts_at = micros(policy.season_for(now).start)
        day = micros(now.replace(hour=0, minute=0, second=0, microsecond=0))
        cursor = c.execute(
            "SELECT joined_at,account_id FROM league_match_cursor WHERE ruleset=?", (ruleset,)
        ).fetchone()
        rows = c.execute(
            """SELECT joined_at,account_id FROM league_queue WHERE ruleset=?
            AND (joined_at,account_id)>(?,?) AND joined_at<=?
            ORDER BY joined_at,account_id LIMIT ?""",
            (
                ruleset,
                *(tuple(cursor) if cursor else (starts_at - 1, "")),
                min(cutoff, at) if cutoff is not None else at,
                MATCH_STARTERS + 1,
            ),
        ).fetchall()
        if not rows and cursor is not None:
            c.execute("DELETE FROM league_match_cursor WHERE ruleset=?", (ruleset,))
        more_opponents = False
        window_at: int | None = None
        for i, starter in enumerate(rows[:MATCH_STARTERS]):
            first = self._valid_entry(c, starter["account_id"], ruleset, at, starts_at)
            if first is None:
                continue
            opponents = self._match_from(c, first, now, at, starts_at, day)
            if opponents.matched:
                more = i + 1 < len(rows)
                self._save_match_cursor(c, ruleset, starter, more)
                return MatchPage(True, more, more_opponents, window_at)
            more_opponents |= opponents.more
            if opponents.window_at is not None:
                window_at = min(window_at or opponents.window_at, opponents.window_at)
        more = len(rows) > MATCH_STARTERS
        if rows:
            self._save_match_cursor(c, ruleset, rows[min(MATCH_STARTERS, len(rows)) - 1], more)
        return MatchPage(False, more, more_opponents, window_at)

    @staticmethod
    def _save_match_cursor(c, ruleset: str, starter, more: bool) -> None:
        if more:
            c.execute(
                """INSERT INTO league_match_cursor VALUES(?,?,?)
                ON CONFLICT(ruleset) DO UPDATE SET joined_at=excluded.joined_at,account_id=excluded.account_id""",
                (ruleset, starter["joined_at"], starter["account_id"]),
            )
        else:
            c.execute("DELETE FROM league_match_cursor WHERE ruleset=?", (ruleset,))

    def _match_from(
        self, c, first, now: datetime, at: int, starts_at: int, day: int
    ) -> OpponentPage:
        first_wait = (at - first["joined_at"]) / 1e6
        window = policy.rating_window(first_wait)
        candidates = self._opponent_page(c, first, window)
        window_at = None
        if window < policy.RATING_WINDOW_MAX:
            # A wider range can expose candidates outside the indexed buckets
            # visited here, even if this page contains no opponents.
            boundary = at - (at - first["joined_at"]) % WINDOW_INTERVAL_US + WINDOW_INTERVAL_US
            if boundary < first["expires_at"]:
                window_at = boundary
        for candidate in candidates[:MATCH_OPPONENTS]:
            second = self._valid_entry(c, candidate["account_id"], first["ruleset"], at, starts_at)
            if second is None:
                continue
            if not policy.compatible_ratings(
                first["live_rating"],
                second["live_rating"],
                first_wait,
                (at - second["joined_at"]) / 1e6,
            ):
                gap = abs(first["live_rating"] - second["live_rating"])
                if gap <= policy.RATING_WINDOW_MAX:
                    steps = ceil((gap - policy.RATING_WINDOW_INITIAL) / policy.RATING_WINDOW_STEP)
                    ready = (
                        max(first["joined_at"], second["joined_at"]) + steps * WINDOW_INTERVAL_US
                    )
                    if ready < min(first["expires_at"], second["expires_at"]):
                        window_at = min(window_at or ready, ready)
                continue
            pair = sorted((first["account_id"], second["account_id"]))
            if c.execute(
                """SELECT 1 FROM league_games WHERE first_account=? AND second_account=?
                AND paired_at>=? AND paired_at<? LIMIT 1 OFFSET ?""",
                (*pair, day, day + 86400 * 1000000, policy.PAIR_DAILY_LIMIT - 1),
            ).fetchone():
                continue
            self._seat(c, first, second, now)
            return OpponentPage(True)
        more = len(candidates) > MATCH_OPPONENTS
        if more:
            last = candidates[MATCH_OPPONENTS - 1]
            next_cursor = (last["joined_at"], last["account_id"])
        else:
            next_cursor = (None, None)
        if next_cursor != (first["opponent_joined_at"], first["opponent_account"]):
            c.execute(
                "UPDATE league_queue SET opponent_joined_at=?,opponent_account=? WHERE account_id=?",
                (*next_cursor, first["account_id"]),
            )
        return OpponentPage(False, more, window_at)

    def settle(self, c, game_id: str, now: datetime) -> bool:
        return settle_result(c, game_id, now)

    def _defer_settlement(self, game_id: str, attempted: sqlite3.Row) -> None:
        # Do not defer a newer result revision or another worker's newer retry.
        with self.online._write() as c:
            attempts = min(attempted["attempts"] + 1, 20)
            delay = min(300, 2 ** min(attempts, 8))
            c.execute(
                """UPDATE league_settlement_queue SET attempts=?,due_at=?,last_error='accounting_deferred'
                WHERE game_id=? AND due_at=? AND attempts=? AND EXISTS(
                    SELECT 1 FROM online_games WHERE id=? AND result_revision IS ?
                ) AND NOT EXISTS(
                    SELECT 1 FROM league_rebuild_jobs WHERE season_id=league_settlement_queue.season_id
                )""",
                (
                    attempts,
                    micros(self.online._now() + timedelta(seconds=delay)),
                    game_id,
                    attempted["due_at"],
                    attempted["attempts"],
                    game_id,
                    attempted["result_revision"],
                ),
            )

    def _drain_settlements(self, limit: int, stop: Event | None) -> tuple[int, int, bool]:
        with self.store.connection() as c:
            due = c.execute(
                """SELECT q.due_at,q.game_id,j.season_id IS NOT NULL AS rebuilding
                FROM league_settlement_queue q LEFT JOIN league_rebuild_jobs j ON j.season_id=q.season_id
                WHERE q.due_at<=? AND (q.due_at,q.game_id)>(?,?) ORDER BY q.due_at,q.game_id LIMIT ?""",
                (micros(self.online._now()), *(self._settlement_after or (-(2**63), "")), limit),
            ).fetchall()
        completed = retries = 0
        for row in due:
            if stop is not None and stop.is_set():
                return completed, retries, False
            if row["rebuilding"]:
                continue
            game_id = row["game_id"]
            attempted = None
            try:
                with self.online._write() as c:
                    now = self.online._now()
                    attempted = c.execute(
                        """SELECT q.due_at,q.attempts,g.result_revision
                        FROM league_settlement_queue q JOIN online_games g ON g.id=q.game_id
                        WHERE q.game_id=? AND q.due_at<=? AND NOT EXISTS(
                            SELECT 1 FROM league_rebuild_jobs WHERE season_id=q.season_id
                        )""",
                        (game_id, micros(now)),
                    ).fetchone()
                    if attempted is None:
                        continue
                    self.settle(c, game_id, now)
                    c.execute("DELETE FROM league_settlement_queue WHERE game_id=?", (game_id,))
                completed += 1
            except DeploymentLocked:
                return completed, retries, True
            except Exception:
                retries += 1
                LOGGER.warning("League settlement deferred")
                if attempted is not None:
                    try:
                        self._defer_settlement(game_id, attempted)
                    except DeploymentLocked:
                        return completed, retries, True
                    except Exception:
                        LOGGER.warning("League settlement retry could not be recorded")
        self._settlement_after = (
            (due[-1]["due_at"], due[-1]["game_id"]) if len(due) == limit else None
        )
        return completed, retries, False

    def _worker_rulesets(self, c, now: datetime) -> list[str]:
        rulesets = []
        for ruleset in sorted(RULESETS):
            entries = c.execute(
                "SELECT 1 FROM league_queue WHERE ruleset=? LIMIT 2",
                (ruleset,),
            ).fetchall()
            if len(entries) > 1:
                rulesets.append(ruleset)
        if not rulesets:
            self._match_sweeps.clear()
            return []
        paused, version = c.execute(
            "SELECT paused,version FROM league_settings WHERE id=1"
        ).fetchone()
        enabled = not paused and self._admission_enabled() and self._clocks_enabled(c, now)
        if not enabled or version != self._matching_version:
            self._match_sweeps.clear()
        self._matching_version = version
        return rulesets if enabled else []

    def _match_backlog(self, queued: list[str], stop: Event, result: LeagueWork) -> None:
        """Use a bounded burst of short writers, with a handoff between commits."""
        if not queued:
            return
        ordered = sorted(RULESETS)
        offset = self._next_ruleset
        self._next_ruleset = (offset + 1) % len(ordered)
        started = monotonic()
        pending = deque(
            ruleset
            for ruleset in ordered[offset:] + ordered[:offset]
            if ruleset in queued
            and (
                ruleset not in self._match_sweeps
                or self._match_sweeps[ruleset].retry_after <= started
            )
        )
        for _ in range(MATCH_TRANSACTIONS):
            if not pending or stop.is_set() or monotonic() - started >= MATCH_CYCLE_SECONDS:
                break
            ruleset = pending.popleft()
            sweep = self._match_sweeps.setdefault(ruleset, MatchSweep())
            try:
                with self.online._write(wait_ms=MATCH_LOCK_WAIT_MS) as c:
                    now = self.online._now()
                    # Arrivals can be opponents immediately. Fix a starter
                    # horizon for this sweep so churn cannot delay wrap forever.
                    at = micros(now)
                    cutoff = sweep.cutoff if sweep.cutoff is not None else at
                    page = self._match(c, now, ruleset, cutoff)
            except sqlite3.OperationalError as error:
                if not _busy(error):
                    raise
                # The transaction made no progress. Resume at the next tick.
                break
            if page.unavailable:
                self._match_sweeps.pop(ruleset, None)
                break
            if page.matched:
                result["matched"] += 1
                sweep.matched = True
                sweep.failures = 0
            sweep.more_opponents |= page.more_opponents
            if page.window_at is not None:
                sweep.window_at = min(sweep.window_at or page.window_at, page.window_at)
            if page.more_starters:
                sweep.cutoff = cutoff
                pending.append(ruleset)
            else:
                # Give the next ruleset a turn before a wrap. A saved opponent
                # page is unfinished discovery even at the end of the starters.
                delay = 0.0
                if not sweep.matched and not sweep.more_opponents:
                    if sweep.window_at is None:
                        sweep.failures = min(sweep.failures + 1, 4)
                        delay = min(2 ** (sweep.failures - 1), MATCH_MAX_BACKOFF_SECONDS)
                    else:
                        sweep.failures = 0
                        delay = min(MATCH_MAX_BACKOFF_SECONDS, max(0, (sweep.window_at - at) / 1e6))
                self._match_sweeps[ruleset] = MatchSweep(
                    failures=sweep.failures, retry_after=monotonic() + delay
                )
            if pending and stop.wait(MATCH_YIELD_SECONDS):
                break

    def _eligibility_candidates(self, c, limit: int) -> tuple[list[sqlite3.Row], int]:
        page = queue_eligibility_page(c, self._eligibility_after, ELIGIBILITY_SCAN + 1)
        candidates = []
        visited = 0
        for row in page[:ELIGIBILITY_SCAN]:
            visited += 1
            if online_ineligibility(dict(row)) is not None:
                candidates.append(row)
                if len(candidates) == limit:
                    break
        after = page[visited - 1]["position"] if visited < len(page) else 0
        return candidates, after

    def _reconcile_eligibility(self, candidates: list[sqlite3.Row], wait_ms: int | None) -> int:
        if not candidates:
            return 0
        with self.online._write(wait_ms=wait_ms) as c:
            at = micros(self.online._now())
            request_ids = [row["request_id"] for row in candidates]
            placeholders = ",".join("?" for _ in request_ids)
            rows = c.execute(
                f"""SELECT q.account_id,q.request_id,q.expires_at,a.active,a.role,a.email_verified_at
                FROM league_queue q JOIN accounts a ON a.id=q.account_id
                WHERE q.request_id IN ({placeholders})""",
                request_ids,
            ).fetchall()
            return sum(
                self._cancel(
                    c,
                    row["account_id"],
                    "expired" if row["expires_at"] <= at else "cancelled",
                    row["request_id"],
                )
                for row in rows
                if online_ineligibility(dict(row)) is not None
            )

    def drain(self, limit: int = 20, *, stop: Event | None = None) -> LeagueWork:
        """Process bounded durable results, expiry and queue work."""
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("League settlement batch must be between 1 and 100")
        completed, retries, deferred = self._drain_settlements(limit, stop)
        result: LeagueWork = {
            "settled": completed,
            "expired": 0,
            "reconciled": 0,
            "matched": 0,
            "deferred": deferred,
            "settlement_retries": retries,
        }
        if deferred or (stop is not None and stop.is_set()):
            return result

        now = self.online._now()
        with self.store.connection() as c:
            expired = c.execute(
                "SELECT 1 FROM league_queue WHERE expires_at<=? LIMIT 1", (micros(now),)
            ).fetchone()
            candidates, eligibility_after = self._eligibility_candidates(c, limit)
            queued = (
                self._worker_rulesets(c, now)
                if stop is not None
                else [
                    ruleset
                    for ruleset in sorted(RULESETS)
                    if c.execute(
                        "SELECT 1 FROM league_queue WHERE ruleset=? LIMIT 1", (ruleset,)
                    ).fetchone()
                ]
            )
        if stop is not None and stop.is_set():
            return result
        wait_ms = MATCH_LOCK_WAIT_MS if stop is not None else None
        try:
            if expired:
                with self.online._write(wait_ms=wait_ms) as c:
                    rows = c.execute(
                        """SELECT account_id,request_id FROM league_queue WHERE expires_at<=?
                        ORDER BY expires_at,account_id LIMIT ?""",
                        (micros(self.online._now()), limit),
                    ).fetchall()
                    for row in rows:
                        self._cancel(c, row["account_id"], "expired", row["request_id"])
                result["expired"] = len(rows)
            if stop is not None and stop.is_set():
                return result
            result["reconciled"] = self._reconcile_eligibility(candidates, wait_ms)
            self._eligibility_after = eligibility_after
            if stop is not None:
                self._match_backlog(queued, stop, result)
            else:
                for ruleset in queued:
                    with self.online._write() as c:
                        page = self._match(c, self.online._now(), ruleset)
                    result["matched"] += int(page.matched)
        except DeploymentLocked:
            result["deferred"] = True
        except sqlite3.OperationalError as error:
            if stop is None or not _busy(error):
                raise
        return result
