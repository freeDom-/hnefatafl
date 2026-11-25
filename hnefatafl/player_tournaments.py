"""Double-elimination player tournaments over authoritative online games.

This is deliberately separate from the AI test tournaments in ``tournaments.py``:
it never reads or writes the workshop ``tournaments`` table, so workshop model
comparisons and player competitions cannot be confused.

Bracket state is *derived* from settled authoritative online results. Each
tournament match is a series of ``match_legs`` side-swapped online games; a
participant is eliminated after two match losses. Advancement is idempotent
because every match has a deterministic identity and the whole bracket is
re-derived from the recorded leg outcomes inside one SQLite write transaction.
When an authoritative result is corrected or voided after it was settled, the
affected match is marked disputed and the bracket stops advancing until an
administrator rebuilds it.
"""

from __future__ import annotations

import base64
import json
import secrets
import sqlite3
import time
import unicodedata
from datetime import datetime
from uuid import uuid4

from . import lifecycle
from .account_policy import BIDI_CONTROLS, resolve_accounts
from .engine import RULESETS
from .online_core import OnlineCore, OnlineError, instant, micros, stamp
from .security_context import allowed, online_eligible

UNRESOLVED = object()

MAX_ENTRANTS = 64
MAX_AUDIT_PAGE = 100
MAX_LIST_PAGE = 50
MAX_MATCH_PAGE = 200
MAX_CURSOR_LENGTH = 256
MAX_ACTIVE_CREATED = 3
MAX_DAILY_CREATED = 5

DISPLACED_GAME_NOTE = "Tournament bracket rebuild detached this game."

BRACKET_ORDER = {"upper": 0, "lower": 1, "grand_final": 2, "reset": 3}
TERMINAL_MATCHES = ("complete", "bye")


def power_of_two_at_least(value: int) -> int:
    size = 2
    while size < value:
        size *= 2
    return size


def seed_order(size: int) -> list[int]:
    """Canonical bracket slot order: slots 0/1 play 1-vs-size, and so on."""
    order = [1, 2]
    while len(order) < size:
        order = [
            value
            for pair in zip(order, [len(order) * 2 + 1 - other for other in order])
            for value in pair
        ]
    return order


def randomized_seed_order(registered: list[str]) -> list[str]:
    """Return a uniformly random permutation of the registered account ids.

    The caller maps the permutation to consecutive seeds 1..N. Client-supplied
    orderings never reach this function, so registration order, account ids and
    ratings cannot influence the draw.
    """
    ordered = list(registered)
    secrets.SystemRandom().shuffle(ordered)
    return ordered


def encode_cursor(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    cursor = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    if len(cursor) > MAX_CURSOR_LENGTH:
        # Generated cursors must stay decodable; a longer payload is a bug, not input.
        raise OnlineError(500, "Pagination cursor exceeds the supported length.")
    return cursor


def decode_cursor(cursor: str) -> dict:
    if not isinstance(cursor, str) or not 0 < len(cursor) <= MAX_CURSOR_LENGTH:
        raise OnlineError(422, "Invalid pagination cursor.")
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        raw = base64.b64decode(padded.encode(), altchars=b"-_", validate=True)
        payload = json.loads(raw)
    except (ValueError, TypeError):
        raise OnlineError(422, "Invalid pagination cursor.") from None
    if not isinstance(payload, dict):
        raise OnlineError(422, "Invalid pagination cursor.")
    return payload


def listing_cursor(cursor: str, status: str | None, ruleset=None, phase=None) -> tuple[int, str]:
    """Decode a listing cursor, bound to the requested discovery filters."""
    payload = decode_cursor(cursor)
    created, identifier = payload.get("t"), payload.get("i")
    if (
        payload.get("k") != "listing"
        or payload.get("s") != status
        or payload.get("r") != ruleset
        or payload.get("p") != phase
        or type(created) is not int
        or not isinstance(identifier, str)
        or not identifier
    ):
        raise OnlineError(422, "Invalid pagination cursor.")
    return created, identifier


def match_cursor(cursor: str, identifier: str) -> str:
    """Decode a match cursor, bound to the requested tournament."""
    payload = decode_cursor(cursor)
    last = payload.get("i")
    if (
        payload.get("k") != "matches"
        or payload.get("t") != identifier
        or not isinstance(last, str)
        or not last
    ):
        raise OnlineError(422, "Invalid pagination cursor.")
    return last


def _rounds(bracket_size: int) -> int:
    return bracket_size.bit_length() - 1


def _lower_rounds(bracket_size: int) -> list[tuple[str, int, int | None]]:
    """Lower-bracket plan: (kind, match count, upper round it drops from)."""
    upper = _rounds(bracket_size)
    plan: list[tuple[str, int, int | None]] = []
    if upper < 2:
        return plan
    plan.append(("from_ub1", bracket_size // 4, 1))
    survivors = bracket_size // 4
    for drop in range(2, upper + 1):
        plan.append(("drop", survivors, drop))
        if drop < upper:
            plan.append(("consol", survivors // 2, None))
            survivors //= 2
    return plan


class PlayerTournaments:
    def __init__(self, auth, online: OnlineCore, clock=None):
        self.auth, self.store, self.online = auth, auth.store, online
        self.clock = clock or online.clock

    def _write(self):
        return self.online._write()

    def _now(self) -> datetime:
        return instant(micros(self.clock()))

    def _identity(self, c, credential: str, *, require: str | None = None) -> dict:
        row = c.execute(
            "SELECT a.* FROM accounts a JOIN sessions s ON a.id=s.account_id WHERE s.token_hash=? AND s.expires>?",
            (credential, time.time()),
        ).fetchone()
        account = dict(row) if row else None
        if not allowed(account, "play"):
            raise OnlineError(401, "A current authenticated session is required.")
        if require is not None and not allowed(account, require):
            raise OnlineError(403, "Administrator permission is required.")
        assert account is not None
        return account

    @staticmethod
    def _version(row, version: int) -> None:
        if type(version) is not int or version < 0:
            raise OnlineError(422, "An integer expected version is required.")
        if row["version"] != version:
            raise OnlineError(409, "This tournament changed. Reload before retrying.")

    @staticmethod
    def _bump(c, identifier: str, version: int) -> None:
        c.execute(
            "UPDATE player_tournaments SET version=version+1 WHERE id=? AND version=?",
            (identifier, version),
        )
        if c.execute("SELECT changes()").fetchone()[0] != 1:
            raise OnlineError(409, "This tournament changed. Reload before retrying.")

    def _load(self, c, identifier: str) -> sqlite3.Row:
        row = c.execute("SELECT * FROM player_tournaments WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise OnlineError(404, "Player tournament not found.")
        return row

    def _audit(
        self, c, identifier, actor_id, action, now, *, match_id=None, note=None, details=None
    ):
        c.execute(
            "INSERT INTO player_tournament_audit(tournament_id,actor_id,action,match_id,note,details,at) VALUES(?,?,?,?,?,?,?)",
            (
                identifier,
                actor_id,
                action,
                match_id,
                note,
                json.dumps(details) if details is not None else None,
                micros(now),
            ),
        )

    # -- registration -----------------------------------------------------

    def create(
        self,
        credential: str,
        *,
        name: str,
        ruleset: str,
        capacity: int,
        match_legs: int | None = None,
        time_per_move: int = 259200,
        match_mode: str | None = None,
        time_control: str = "per_move",
        increment_seconds: int = 0,
        scheduled_start_at: datetime | None = None,
    ) -> dict:
        name = name.strip()
        if (
            not name
            or len(name) > 120
            or any(ch in BIDI_CONTROLS or unicodedata.category(ch).startswith("C") for ch in name)
        ):
            raise OnlineError(422, "A tournament name of 1 to 120 characters is required.")
        if ruleset not in RULESETS:
            raise OnlineError(422, "A supported ruleset is required.")
        if type(capacity) is not int or not 2 <= capacity <= MAX_ENTRANTS:
            raise OnlineError(422, f"Capacity must be an integer from 2 to {MAX_ENTRANTS}.")
        if match_mode not in ("sequential", "simultaneous") or match_legs is not None:
            raise OnlineError(
                422,
                "New tournaments require sequential or simultaneous mode and exactly two games.",
            )
        try:
            lifecycle.TimeControl(time_per_move, time_control, increment_seconds)
        except lifecycle.LifecycleError as error:
            raise OnlineError(422, str(error)) from None
        scheduled = None
        if scheduled_start_at is not None:
            if scheduled_start_at.tzinfo is None or scheduled_start_at.utcoffset() is None:
                raise OnlineError(422, "The planned start must include a timezone.")
            scheduled = micros(scheduled_start_at)
        with self._write() as c:
            account = self._identity(c, credential)
            now = self._now()
            if not (allowed(account, "tournaments") or online_eligible(account)):
                raise OnlineError(403, "Tournament creation requires an active, verified account.")
            if not self._creation_available(c, account, now):
                raise OnlineError(429, "Tournament creation limit reached. Try again later.")
            if scheduled is not None and scheduled <= micros(now):
                raise OnlineError(422, "The planned start must be in the future.")
            identifier = "ptournament-" + uuid4().hex
            c.execute(
                "INSERT INTO player_tournaments(id,name,ruleset,format,status,review_state,capacity,match_legs,time_per_move,created_by,created_at,version) VALUES(?,?,?,'double_elimination','registration','ok',?,?,?,?,?,0)",
                (
                    identifier,
                    name,
                    ruleset,
                    capacity,
                    2,
                    time_per_move,
                    account["id"],
                    micros(now),
                ),
            )
            c.execute(
                "UPDATE player_tournaments SET scheduled_start_at=?,start_due_at=?,match_mode=?,time_control=?,increment_seconds=? WHERE id=?",
                (scheduled, scheduled, match_mode, time_control, increment_seconds, identifier),
            )
            self._audit(
                c,
                identifier,
                account["id"],
                "created",
                now,
                details={
                    "capacity": capacity,
                    "ruleset": ruleset,
                    "match_legs": 2,
                    "match_mode": match_mode,
                },
            )
            return self._detail(c, identifier, account)

    def _creation_available(self, c, account, now):
        if allowed(account, "tournaments"):
            return True
        active, recent = c.execute(
            """SELECT COALESCE(SUM(status IN ('registration','running')),0),
            COALESCE(SUM(created_at>?),0) FROM player_tournaments WHERE created_by=?""",
            (micros(now) - 86400 * 1000000, account["id"]),
        ).fetchone()
        return active < MAX_ACTIVE_CREATED and recent < MAX_DAILY_CREATED

    @staticmethod
    def _creator_must_wait(row, count, now):
        return (
            row["scheduled_start_at"] is not None
            and micros(now) < row["scheduled_start_at"]
            and count >= row["capacity"]
        )

    def _capabilities(self, c, account, row=None, *, entrants=(), now=None):
        admin = allowed(account, "tournaments")
        eligible = online_eligible(account)
        now = now if now is not None else self._now()
        registration = row is not None and row["status"] == "registration"
        creator = row is not None and eligible and row["created_by"] == account["id"]
        return {
            "can_create": (admin or eligible) and self._creation_available(c, account, now),
            "can_start": bool(
                registration
                and len(entrants) >= 2
                and (admin or (creator and not self._creator_must_wait(row, len(entrants), now)))
                and all(online_eligible(entry) for entry in entrants)
            ),
            "can_reschedule": registration and (admin or creator),
            "can_moderate": admin,
        }

    @staticmethod
    def _consume_schedule(c, identifier: str) -> None:
        c.execute(
            """UPDATE player_tournaments SET start_due_at=NULL,start_attempts=0,start_error=NULL
            WHERE id=? AND start_due_at IS NOT NULL""",
            (identifier,),
        )

    def register(self, credential: str, identifier: str) -> dict:
        with self._write() as c:
            account = self._identity(c, credential)
            row = self._load(c, identifier)
            if row["status"] != "registration":
                raise OnlineError(409, "Registration for this tournament is closed.")
            if not online_eligible(account):
                raise OnlineError(
                    403, "Tournament registration requires an active, verified account."
                )
            now = self._now()
            existing = c.execute(
                "SELECT * FROM player_tournament_entrants WHERE tournament_id=? AND account_id=?",
                (identifier, account["id"]),
            ).fetchone()
            if existing is not None and existing["status"] == "registered":
                return self._detail(c, identifier, account)
            registered = c.execute(
                "SELECT COUNT(*) FROM player_tournament_entrants WHERE tournament_id=? AND status='registered'",
                (identifier,),
            ).fetchone()[0]
            if registered >= row["capacity"]:
                raise OnlineError(409, "This tournament is full.")
            if existing is None:
                c.execute(
                    "INSERT INTO player_tournament_entrants(tournament_id,account_id,seed,status,registered_at) VALUES(?,?,NULL,'registered',?)",
                    (identifier, account["id"], micros(now)),
                )
            else:
                c.execute(
                    "UPDATE player_tournament_entrants SET status='registered',withdrawn_at=NULL,registered_at=? WHERE tournament_id=? AND account_id=?",
                    (micros(now), identifier, account["id"]),
                )
            self._bump(c, identifier, row["version"])
            # Missing the planned start requires a new schedule or a manual start.
            # Do not re-arm a consumed start when a later entrant fills the cup.
            if row["scheduled_start_at"] is not None and micros(now) > row["scheduled_start_at"]:
                self._consume_schedule(c, identifier)
            self._audit(c, identifier, account["id"], "registered", now)
            return self._detail(c, identifier, account)

    def withdraw(self, credential: str, identifier: str) -> dict:
        with self._write() as c:
            account = self._identity(c, credential)
            row = self._load(c, identifier)
            if row["status"] != "registration":
                raise OnlineError(409, "Registration for this tournament is closed.")
            existing = c.execute(
                "SELECT * FROM player_tournament_entrants WHERE tournament_id=? AND account_id=?",
                (identifier, account["id"]),
            ).fetchone()
            if existing is None or existing["status"] != "registered":
                raise OnlineError(409, "You are not registered for this tournament.")
            now = self._now()
            c.execute(
                "UPDATE player_tournament_entrants SET status='withdrawn',withdrawn_at=? WHERE tournament_id=? AND account_id=?",
                (micros(now), identifier, account["id"]),
            )
            if row["scheduled_start_at"] is not None and micros(now) >= row["scheduled_start_at"]:
                self._consume_schedule(c, identifier)
            self._audit(c, identifier, account["id"], "withdrawn", now)
            self._bump(c, identifier, row["version"])
            return self._detail(c, identifier, account)

    # -- administration ---------------------------------------------------

    def reschedule(
        self, credential: str, identifier: str, version: int, scheduled_start_at: datetime
    ) -> dict:
        if scheduled_start_at.tzinfo is None or scheduled_start_at.utcoffset() is None:
            raise OnlineError(422, "The planned start must include a timezone.")
        scheduled = micros(scheduled_start_at)
        with self._write() as c:
            account = self._identity(c, credential)
            row = self._load(c, identifier)
            if not allowed(account, "tournaments") and (
                row["created_by"] != account["id"] or not online_eligible(account)
            ):
                raise OnlineError(
                    403, "Only the creator or an administrator may reschedule this tournament."
                )
            self._version(row, version)
            if row["status"] != "registration":
                raise OnlineError(409, "Only a tournament in registration can be rescheduled.")
            now = self._now()
            if scheduled <= micros(now):
                raise OnlineError(422, "The planned start must be in the future.")
            c.execute(
                """UPDATE player_tournaments SET scheduled_start_at=?,start_due_at=?,
                schedule_version=schedule_version+1,start_attempts=0,start_error=NULL WHERE id=?""",
                (scheduled, scheduled, identifier),
            )
            self._bump(c, identifier, version)
            self._audit(
                c,
                identifier,
                account["id"],
                "rescheduled",
                now,
                details={"scheduled_start_at": stamp(instant(scheduled))},
            )
            return self._detail(c, identifier, account)

    def start(self, credential: str, identifier: str, version: int) -> dict:
        with self._write() as c:
            account = self._identity(c, credential)
            row = self._load(c, identifier)
            admin = allowed(account, "tournaments")
            if not admin and (row["created_by"] != account["id"] or not online_eligible(account)):
                raise OnlineError(
                    403, "Only the creator or an administrator may start this tournament."
                )
            self._version(row, version)
            self._start(
                c,
                row,
                self._now(),
                actor_id=account["id"],
                source="administrator" if admin else "creator",
            )
            return self._detail(c, identifier, account)

    def _start(self, c, row, now, *, actor_id=None, source="automatic"):
        """Shared start under BEGIN IMMEDIATE; every game and seed commits together."""
        identifier = row["id"]
        if row["status"] != "registration":
            raise OnlineError(409, "Only a tournament in registration can be started.")
        entrants = c.execute(
            """SELECT a.* FROM player_tournament_entrants e JOIN accounts a ON a.id=e.account_id
            WHERE e.tournament_id=? AND e.status='registered' ORDER BY e.registered_at,e.account_id""",
            (identifier,),
        ).fetchall()
        registered = [entry["id"] for entry in entrants]
        if source == "automatic" and (
            row["scheduled_start_at"] is None
            or row["scheduled_start_at"] > micros(now)
            or row["start_due_at"] is None
            or row["start_due_at"] > micros(now)
            or len(registered) != row["capacity"]
        ):
            raise OnlineError(409, "This tournament is not due to start automatically.")
        if len(registered) < 2:
            raise OnlineError(409, "At least two registered entrants are required.")
        if any(not online_eligible(dict(entry)) for entry in entrants):
            raise OnlineError(
                409, "All entrants must have active, verified accounts before starting."
            )
        if source == "creator" and self._creator_must_wait(row, len(registered), now):
            raise OnlineError(
                409,
                "A full tournament must wait for its planned start. An administrator may override it.",
            )
        # Every start mechanism uses the same secure server-side draw. The draw
        # happens inside the start transaction, so a rolled-back start leaves no
        # seeds behind and concurrent starters cannot commit different draws.
        ordered = randomized_seed_order(registered)
        for index, account_id in enumerate(ordered, start=1):
            c.execute(
                "UPDATE player_tournament_entrants SET seed=? WHERE tournament_id=? AND account_id=?",
                (index, identifier, account_id),
            )
        size = power_of_two_at_least(len(ordered))
        progression = (
            "synchronized"
            if row["match_mode"] in ("sequential", "simultaneous") and row["match_legs"] == 2
            else "independent"
        )
        c.execute(
            """UPDATE player_tournaments SET status='running',started_at=?,bracket_size=?,
                  progression_policy=?,start_due_at=NULL,start_attempts=0,start_error=NULL,
                  version=version+1 WHERE id=? AND version=? AND status='registration'""",
            (micros(now), size, progression, identifier, row["version"]),
        )
        if c.execute("SELECT changes()").fetchone()[0] != 1:
            raise OnlineError(409, "This tournament changed. Reload before retrying.")
        self._build_bracket(c, identifier, size, now)
        self._audit(
            c,
            identifier,
            actor_id,
            "started",
            now,
            details={"entrants": len(ordered), "bracket_size": size, "source": source},
        )
        self._advance(c, identifier, now)

    def cancel(self, credential: str, identifier: str, version: int) -> dict:
        with self._write() as c:
            account = self._identity(c, credential, require="tournaments")
            row = self._load(c, identifier)
            self._version(row, version)
            if row["status"] not in ("registration", "running"):
                raise OnlineError(409, "This tournament can no longer be cancelled.")
            now = self._now()
            # Retire outstanding legs through the authoritative online core so a
            # cancelled competition has no playable, settleable or expirable game
            # left. Completed legs and their results stay as history; already
            # voided legs are skipped so a leg is never voided twice.
            actor = lifecycle.Actor(account["id"], True)
            void_note = "Tournament cancelled by administrator."
            voided = 0
            outstanding = c.execute(
                """SELECT l.game_id FROM player_tournament_legs l
                   JOIN player_tournament_matches m ON m.id=l.match_id
                   JOIN online_games g ON g.id=l.game_id
                   WHERE m.tournament_id=? AND g.status IN ('waiting','active')""",
                (identifier,),
            ).fetchall()
            for leg in outstanding:
                if self.online.void_game(c, leg["game_id"], actor, void_note, now):
                    voided += 1
            c.execute(
                "UPDATE player_tournaments SET status='cancelled',version=version+1 WHERE id=? AND version=?",
                (identifier, version),
            )
            if c.execute("SELECT changes()").fetchone()[0] != 1:
                raise OnlineError(409, "This tournament changed. Reload before retrying.")
            self._audit(
                c, identifier, account["id"], "cancelled", now, details={"voided_games": voided}
            )
            return self._detail(c, identifier, account)

    def settle_committed(self, c, identifier: str, now: datetime):
        """Advance and invalidate reviewed snapshots only when bracket data changes."""
        version = self._load(c, identifier)["version"]
        before = c.total_changes
        self._advance(c, identifier, now)
        if c.total_changes != before and self._load(c, identifier)["version"] == version:
            self._bump(c, identifier, version)

    def settle(self, credential: str, identifier: str) -> dict:
        # Compatibility for older clients: ordinary requests are cheap reads.
        # Durable outbox work is consumed by the worker, not by public polling.
        with self.store.connection() as c:
            c.execute("BEGIN")
            account = self._identity(c, credential)
            self._load(c, identifier)
            if (
                not allowed(account, "tournaments")
                or not c.execute(
                    "SELECT 1 FROM player_tournament_settlement_queue WHERE tournament_id=?",
                    (identifier,),
                ).fetchone()
            ):
                return self._detail(c, identifier, account)
        with self._write() as c:
            account = self._identity(c, credential, require="tournaments")
            row = self._load(c, identifier)
            if (
                row["status"] in ("running", "complete")
                and c.execute(
                    "SELECT 1 FROM player_tournament_settlement_queue WHERE tournament_id=?",
                    (identifier,),
                ).fetchone()
            ):
                self.settle_committed(c, identifier, self._now())
                c.execute(
                    "DELETE FROM player_tournament_settlement_queue WHERE tournament_id=?",
                    (identifier,),
                )
            return self._detail(c, identifier, account)

    def override(
        self, credential: str, identifier: str, match_id: str, version: int, winner: str, note: str
    ) -> dict:
        note = note.strip()
        if not note or len(note) > 1000:
            raise OnlineError(422, "A private administrative justification is required.")
        with self._write() as c:
            account = self._identity(c, credential, require="tournaments")
            row = self._load(c, identifier)
            self._version(row, version)
            if row["status"] != "running":
                raise OnlineError(409, "Only a running tournament can be moderated.")
            match = c.execute(
                "SELECT * FROM player_tournament_matches WHERE id=? AND tournament_id=?",
                (match_id, identifier),
            ).fetchone()
            if match is None:
                raise OnlineError(404, "Tournament match not found.")
            if row["review_state"] == "disputed":
                raise OnlineError(409, "Rebuild the bracket before overriding a disputed match.")
            if match["status"] != "ready":
                raise OnlineError(409, "Only a ready match can be overridden.")
            if winner not in (match["participant_a"], match["participant_b"]):
                raise OnlineError(422, "The winner must be one of the match participants.")
            now = self._now()
            # Retire any outstanding games so no live game outlives the decision
            # and no later completion can contradict the override.
            actor = lifecycle.Actor(account["id"], True)
            void_note = ("Tournament match override: " + note)[:1000]
            for leg in c.execute(
                """SELECT l.game_id FROM player_tournament_legs l
                JOIN online_games g ON g.id=l.game_id
                WHERE l.match_id=? AND g.status IN ('waiting','active')""",
                (match_id,),
            ).fetchall():
                self.online.void_game(c, leg["game_id"], actor, void_note, now)
            loser = (
                match["participant_b"]
                if winner == match["participant_a"]
                else match["participant_a"]
            )
            c.execute(
                "UPDATE player_tournament_matches SET status='complete',winner=?,loser=?,decided_by='admin_override',settled_at=? WHERE id=?",
                (winner, loser, micros(now), match_id),
            )
            if match["bracket"] == "grand_final" and winner == match["participant_b"]:
                self._ensure_reset(c, identifier, match, now)
            self._bump(c, identifier, version)
            self._audit(
                c,
                identifier,
                account["id"],
                "override",
                now,
                match_id=match_id,
                note=note,
                details={"winner": winner},
            )
            self._advance(c, identifier, now)
            return self._detail(c, identifier, account)

    def rebuild(self, credential: str, identifier: str, version: int, note: str) -> dict:
        note = note.strip()
        if not note or len(note) > 1000:
            raise OnlineError(422, "A private administrative justification is required.")
        with self._write() as c:
            account = self._identity(c, credential, require="tournaments")
            row = self._load(c, identifier)
            self._version(row, version)
            if row["status"] != "running":
                raise OnlineError(409, "Only a running tournament can be rebuilt.")
            now = self._now()
            retired = self._reconcile_bracket(c, row, account["id"], now)
            c.execute(
                "UPDATE player_tournaments SET review_state='ok',version=version+1 WHERE id=? AND version=?",
                (identifier, version),
            )
            if c.execute("SELECT changes()").fetchone()[0] != 1:
                raise OnlineError(409, "This tournament changed. Reload before retrying.")
            self._advance(c, identifier, now)
            self._audit(
                c,
                identifier,
                account["id"],
                "rebuild",
                now,
                note=note,
                details={"retired_games": retired},
            )
            return self._detail(c, identifier, account)

    # -- bracket construction --------------------------------------------

    def _build_bracket(
        self, c, identifier: str, size: int, now: datetime, *, preview=False
    ) -> list[dict]:
        order = seed_order(size)
        at = micros(now)
        preview_matches: list[dict] = []

        def new(bracket, round_, slot, source_a, source_b):
            match_id = f"{identifier}:{bracket}:{round_}:{slot}"
            if preview:
                preview_matches.append(
                    {
                        "id": match_id,
                        "bracket": bracket,
                        "round": round_,
                        "slot": slot,
                        "source_a": source_a,
                        "source_b": source_b,
                        "status": "pending",
                        "participant_a": None,
                        "participant_b": None,
                        "winner": None,
                        "loser": None,
                        "decided_by": None,
                        "created_at": at,
                        "settled_at": None,
                    }
                )
                return match_id
            c.execute(
                "INSERT INTO player_tournament_matches(id,tournament_id,bracket,round,slot,status,source_a,source_b,created_at) VALUES(?,?,?,?,?,'pending',?,?,?)",
                (match_id, identifier, bracket, round_, slot, source_a, source_b, at),
            )
            return match_id

        upper: dict[tuple[int, int], str] = {}
        for round_ in range(1, _rounds(size) + 1):
            for slot in range(size >> round_):
                if round_ == 1:
                    source_a = f"seed:{order[2 * slot]}"
                    source_b = f"seed:{order[2 * slot + 1]}"
                else:
                    source_a = f"winner:{upper[(round_ - 1, 2 * slot)]}"
                    source_b = f"winner:{upper[(round_ - 1, 2 * slot + 1)]}"
                upper[(round_, slot)] = new("upper", round_, slot, source_a, source_b)

        plan = _lower_rounds(size)
        lower: dict[tuple[int, int], str] = {}
        for index, (kind, count, upper_round) in enumerate(plan, start=1):
            for slot in range(count):
                if kind == "from_ub1":
                    source_a = f"loser:{upper[(1, 2 * slot)]}"
                    source_b = f"loser:{upper[(1, 2 * slot + 1)]}"
                elif kind == "drop":
                    assert upper_round is not None
                    source_a = f"winner:{lower[(index - 1, slot)]}"
                    source_b = f"loser:{upper[(upper_round, count - 1 - slot)]}"
                else:
                    source_a = f"winner:{lower[(index - 1, 2 * slot)]}"
                    source_b = f"winner:{lower[(index - 1, 2 * slot + 1)]}"
                lower[(index, slot)] = new("lower", index, slot, source_a, source_b)

        if _rounds(size) == 1:
            final = new(
                "grand_final",
                1,
                0,
                f"winner:{upper[(1, 0)]}",
                f"loser:{upper[(1, 0)]}",
            )
        else:
            final = new(
                "grand_final",
                1,
                0,
                f"winner:{upper[(_rounds(size), 0)]}",
                f"winner:{lower[(len(plan), 0)]}",
            )

        if preview:
            return preview_matches

        for round_ in range(1, _rounds(size) + 1):
            for slot in range(size >> round_):
                if round_ < _rounds(size):
                    winner_target = (upper[(round_ + 1, slot // 2)], "a" if slot % 2 == 0 else "b")
                else:
                    winner_target = (final, "a")
                if round_ == 1 and _rounds(size) >= 2:
                    loser_target = (lower[(1, slot // 2)], "a" if slot % 2 == 0 else "b")
                elif round_ == 1:
                    loser_target = (final, "b")
                else:
                    drop_round = 2 * round_ - 2
                    loser_target = (lower[(drop_round, (size >> round_) - 1 - slot)], "b")
                c.execute(
                    "UPDATE player_tournament_matches SET next_winner_match=?,next_winner_slot=?,next_loser_match=?,next_loser_slot=? WHERE id=?",
                    (*winner_target, *loser_target, upper[(round_, slot)]),
                )

        for index, (kind, count, _upper_round) in enumerate(plan, start=1):
            for slot in range(count):
                if index == len(plan):
                    winner_target = (final, "b")
                elif plan[index][0] == "drop":
                    winner_target = (lower[(index + 1, slot)], "a")
                else:
                    winner_target = (
                        lower[(index + 1, slot // 2)],
                        "a" if slot % 2 == 0 else "b",
                    )
                c.execute(
                    "UPDATE player_tournament_matches SET next_winner_match=?,next_winner_slot=? WHERE id=?",
                    (*winner_target, lower[(index, slot)]),
                )

        return preview_matches

    # -- advancement ------------------------------------------------------

    @staticmethod
    def _global_round(match, size: int) -> int:
        """Upper round N and lower round N-1 share a global round."""
        bracket = match["bracket"]
        if bracket == "upper":
            return match["round"]
        if bracket == "lower":
            return match["round"] + 1
        final_round = 2 * _rounds(size)
        return final_round + (bracket == "reset")

    @staticmethod
    def _ordered_matches(c, identifier: str) -> list[sqlite3.Row]:
        return sorted(
            c.execute(
                "SELECT * FROM player_tournament_matches WHERE tournament_id=?", (identifier,)
            ),
            key=lambda row: (BRACKET_ORDER[row["bracket"]], row["round"], row["slot"]),
        )

    @staticmethod
    def _seeded_entrants(c, identifier: str) -> tuple[list[str], dict[str, int]]:
        seeds = {
            entry["account_id"]: entry["seed"]
            for entry in c.execute(
                "SELECT account_id,seed FROM player_tournament_entrants WHERE tournament_id=? AND seed IS NOT NULL ORDER BY seed",
                (identifier,),
            )
        }
        return list(seeds), seeds

    def _resolve(self, c, ordered: list[str], source: str):
        kind, _, value = source.partition(":")
        if kind == "seed":
            seed = int(value)
            return ordered[seed - 1] if seed <= len(ordered) else None
        feeder = c.execute(
            "SELECT status,winner,loser FROM player_tournament_matches WHERE id=?", (value,)
        ).fetchone()
        if feeder is None:
            raise OnlineError(409, "Stored tournament state requires operator review.")
        if feeder["status"] not in TERMINAL_MATCHES:
            return UNRESOLVED
        return feeder["winner"] if kind == "winner" else feeder["loser"]

    def _resolve_sources(self, c, ordered, row):
        if row["bracket"] == "reset":
            final = c.execute(
                "SELECT * FROM player_tournament_matches WHERE id=?",
                (row["source_a"].partition(":")[2],),
            ).fetchone()
            if final is not None and final["status"] == "complete":
                if final["winner"] == final["participant_b"]:
                    return final["participant_a"], final["participant_b"]
            return None
        first = self._resolve(c, ordered, row["source_a"])
        if first is UNRESOLVED:
            return None
        second = self._resolve(c, ordered, row["source_b"])
        if second is UNRESOLVED:
            return None
        return first, second

    @staticmethod
    def _leg_seats(participants, leg: int):
        return participants if leg % 2 == 1 else participants[::-1]

    def _reconcile_legs(self, c, match, participants, tournament, actor, now: datetime) -> int:
        """Keep proven role assignments and retire displaced live games before commit."""
        retired = 0
        finished_legs: set[int] = set()
        for leg in c.execute(
            """SELECT l.*,g.attackers_id AS game_attackers,g.defenders_id AS game_defenders,g.status AS game_status
            FROM player_tournament_legs l LEFT JOIN online_games g ON g.id=l.game_id WHERE l.match_id=? ORDER BY l.leg""",
            (match["id"],),
        ).fetchall():
            index, status = leg["leg"], leg["game_status"]
            seats = self._leg_seats(participants, index) if participants else None
            live = status in ("waiting", "active")
            if (
                seats is not None
                and None not in seats
                and index <= tournament["match_legs"]
                and seats == (leg["game_attackers"], leg["game_defenders"])
                and seats == (leg["attackers_id"], leg["defenders_id"])
                and (
                    not live
                    or tournament["match_mode"] != "sequential"
                    or index == 1
                    or index - 1 in finished_legs
                )
            ):
                if status in ("completed", "voided"):
                    finished_legs.add(index)
                continue
            c.execute(
                "DELETE FROM player_tournament_legs WHERE match_id=? AND leg=?",
                (match["id"], index),
            )
            if live and self.online.void_game(c, leg["game_id"], actor, DISPLACED_GAME_NOTE, now):
                retired += 1
        return retired

    def _prepare_legs(self, c, tournament, match, now: datetime) -> None:
        statuses = dict(
            c.execute(
                """SELECT l.leg,g.status FROM player_tournament_legs l
                JOIN online_games g ON g.id=l.game_id WHERE l.match_id=?""",
                (match["id"],),
            )
        )
        participants = (match["participant_a"], match["participant_b"])
        for index in range(1, tournament["match_legs"] + 1):
            if index in statuses:
                continue
            if tournament["match_mode"] == "sequential" and index > 1:
                if statuses.get(index - 1) not in ("completed", "voided"):
                    break
            side = "attackers" if index % 2 == 1 else "defenders"
            game_id = self.online.seat_game(
                c,
                creator_id=match["participant_a"],
                opponent_id=match["participant_b"],
                ruleset=tournament["ruleset"],
                creator_side=side,
                control=lifecycle.TimeControl(
                    tournament["time_per_move"],
                    tournament["time_control"],
                    tournament["increment_seconds"],
                ),
                now=now,
                request_id=f"tournament:{match['id']}:{index}:{uuid4().hex}",
            )
            c.execute(
                "INSERT INTO player_tournament_legs(match_id,leg,game_id,attackers_id,defenders_id,created_at) VALUES(?,?,?,?,?,?)",
                (match["id"], index, game_id, *self._leg_seats(participants, index), micros(now)),
            )

    def _settle_legs(self, c, match, now: datetime) -> bool:
        changed = False
        for leg in c.execute(
            """SELECT l.*,g.status AS game_status,g.result_revision AS effective_revision,r.payload
            FROM player_tournament_legs l LEFT JOIN online_games g ON g.id=l.game_id
            LEFT JOIN online_results r ON r.game_id=g.id AND r.revision=g.result_revision
            WHERE l.match_id=? ORDER BY l.leg""",
            (match["id"],),
        ).fetchall():
            outcome, revision = None, None
            if (
                leg["game_status"] in ("completed", "voided")
                and leg["effective_revision"] is not None
            ):
                revision = leg["effective_revision"]
                winner = json.loads(leg["payload"]).get("winner") if leg["payload"] else None
                if leg["game_status"] == "voided" or winner is None:
                    outcome = "void"
                elif winner == "draw":
                    outcome = "draw"
                elif winner == "attackers":
                    outcome = "a" if leg["attackers_id"] == match["participant_a"] else "b"
                elif winner == "defenders":
                    outcome = "a" if leg["defenders_id"] == match["participant_a"] else "b"
            if outcome != leg["outcome"] or revision != leg["result_revision"]:
                c.execute(
                    "UPDATE player_tournament_legs SET outcome=?,result_revision=?,settled_at=? WHERE match_id=? AND leg=?",
                    (
                        outcome,
                        revision,
                        micros(now) if outcome is not None else None,
                        leg["match_id"],
                        leg["leg"],
                    ),
                )
                changed = True
        return changed

    def _decide_from_legs(self, c, identifier, match, legs, seeds, now) -> None:
        wins_a = sum(leg["outcome"] == "a" for leg in legs)
        wins_b = sum(leg["outcome"] == "b" for leg in legs)
        if wins_a > wins_b:
            winner, loser, decided_by = match["participant_a"], match["participant_b"], "play"
        elif wins_b > wins_a:
            winner, loser, decided_by = match["participant_b"], match["participant_a"], "play"
        elif match["bracket"] == "grand_final":
            # The undefeated finalist must be beaten outright to force a reset.
            winner, loser, decided_by = (
                match["participant_a"],
                match["participant_b"],
                "undefeated_advantage",
            )
        else:
            seed_a, seed_b = seeds.get(match["participant_a"]), seeds.get(match["participant_b"])
            if seed_a is None or seed_b is None or seed_a == seed_b:
                raise OnlineError(409, "Stored tournament state requires operator review.")
            higher = match["participant_a"] if seed_a < seed_b else match["participant_b"]
            lower = match["participant_b"] if seed_a < seed_b else match["participant_a"]
            winner, loser, decided_by = higher, lower, "higher_seed"
        c.execute(
            "UPDATE player_tournament_matches SET status='complete',winner=?,loser=?,decided_by=?,settled_at=? WHERE id=?",
            (winner, loser, decided_by, micros(now), match["id"]),
        )
        if match["bracket"] == "grand_final" and winner == match["participant_b"]:
            self._ensure_reset(c, identifier, match, now)

    def _ensure_reset(self, c, identifier: str, final, now: datetime) -> None:
        reset_id = f"{identifier}:reset:1:0"
        if c.execute("SELECT 1 FROM player_tournament_matches WHERE id=?", (reset_id,)).fetchone():
            return
        c.execute(
            "INSERT INTO player_tournament_matches(id,tournament_id,bracket,round,slot,status,source_a,source_b,created_at) VALUES(?,?,'reset',1,0,'pending',?,?,?)",
            (
                reset_id,
                identifier,
                f"grand_final:{final['id']}",
                f"grand_final:{final['id']}",
                micros(now),
            ),
        )

    def _dispute(self, c, identifier, match, now) -> None:
        c.execute(
            "UPDATE player_tournament_matches SET status='disputed' WHERE id=?", (match["id"],)
        )
        c.execute("UPDATE player_tournaments SET review_state='disputed' WHERE id=?", (identifier,))
        # A corrected result can invalidate an official winner, so a completed
        # tournament reopens for review instead of keeping a stale champion.
        c.execute(
            """UPDATE player_tournaments SET
                 status=CASE WHEN status='complete' THEN 'running' ELSE status END,
                 official_winner=CASE WHEN status='complete' THEN NULL ELSE official_winner END,
                 completed_at=CASE WHEN status='complete' THEN NULL ELSE completed_at END,
                 version=CASE WHEN status='complete' THEN version+1 ELSE version END
               WHERE id=?""",
            (identifier,),
        )
        self._audit(
            c,
            identifier,
            None,
            "disputed",
            now,
            match_id=match["id"],
            details={"reason": "authoritative result revision changed"},
        )

    def _record_participants(self, c, match, participants, now: datetime):
        first, second = participants
        if first is None or second is None:
            c.execute(
                """UPDATE player_tournament_matches SET participant_a=?,participant_b=?,
                   status='bye',winner=?,loser=NULL,decided_by='bye',settled_at=? WHERE id=?""",
                (first, second, first or second, micros(now), match["id"]),
            )
            return None
        if participants != (match["participant_a"], match["participant_b"]):
            c.execute(
                "UPDATE player_tournament_matches SET participant_a=?,participant_b=? WHERE id=?",
                (first, second, match["id"]),
            )
        return dict(match, participant_a=first, participant_b=second)

    def _finish_match(self, c, tournament, match, seeds, now: datetime) -> bool:
        self._settle_legs(c, match, now)
        legs = c.execute(
            "SELECT * FROM player_tournament_legs WHERE match_id=? ORDER BY leg",
            (match["id"],),
        ).fetchall()
        if len(legs) == tournament["match_legs"] and all(
            leg["outcome"] is not None for leg in legs
        ):
            self._decide_from_legs(c, tournament["id"], match, legs, seeds, now)
            return True
        return False

    def _reconcile_bracket(self, c, tournament, actor_id: str, now: datetime) -> int:
        """Re-derive proven seats and results in feeder order, without seating new games."""
        identifier = tournament["id"]
        ordered, seeds = self._seeded_entrants(c, identifier)
        actor = lifecycle.Actor(actor_id, True)
        replay_reset = c.execute(
            """SELECT 1 FROM player_tournament_matches
            WHERE tournament_id=? AND bracket='reset' AND status='disputed'""",
            (identifier,),
        ).fetchone()
        c.execute(
            """UPDATE player_tournament_matches SET status='pending',participant_a=NULL,
            participant_b=NULL,winner=NULL,loser=NULL,decided_by=NULL,settled_at=NULL
            WHERE tournament_id=?""",
            (identifier,),
        )
        retired = 0
        for match in self._ordered_matches(c, identifier):
            participants = self._resolve_sources(c, ordered, match)
            is_reset = match["bracket"] == "reset"
            # A correction to the deciding reset itself requires replay. An
            # unaffected reset can keep its original games if still required.
            retained_seats = None if is_reset and replay_reset else participants
            retired += self._reconcile_legs(c, match, retained_seats, tournament, actor, now)
            if participants is None:
                if is_reset:
                    c.execute("DELETE FROM player_tournament_matches WHERE id=?", (match["id"],))
                continue
            resolved = self._record_participants(c, match, participants, now)
            if resolved is not None:
                self._finish_match(c, tournament, resolved, seeds, now)
        return retired

    def _current_round(self, c, tournament, matches=None) -> int | None:
        if tournament["progression_policy"] != "synchronized" or tournament["status"] != "running":
            return None
        if matches is None:
            # Each bracket's earliest undecided round is enough to find the
            # barrier, including when only a page or a single match is read.
            candidates = c.execute(
                """SELECT bracket, MIN(round) AS round FROM player_tournament_matches
                WHERE tournament_id=? AND status NOT IN (?,?) GROUP BY bracket""",
                (tournament["id"], *TERMINAL_MATCHES),
            )
        else:
            candidates = (match for match in matches if match["status"] not in TERMINAL_MATCHES)
        return min(
            (self._global_round(match, tournament["bracket_size"]) for match in candidates),
            default=None,
        )

    def _advance(self, c, identifier: str, now: datetime) -> None:
        """Open eligible matches and apply authoritative results under the writer lock."""
        tournament = self._load(c, identifier)
        if tournament["review_state"] != "ok":
            return
        ordered, seeds = self._seeded_entrants(c, identifier)
        rows = self._ordered_matches(c, identifier)
        # Check corrections before opening any games, including on another
        # branch whose last result arrived in the same settlement batch.
        for match in rows:
            if match["status"] != "complete":
                continue
            if self._settle_legs(c, match, now) and match["decided_by"] != "admin_override":
                self._dispute(c, identifier, match, now)
                return
        while True:
            current_round = self._current_round(c, tournament, rows)
            decided = False
            for match in rows:
                if match["status"] not in ("pending", "ready"):
                    continue
                if (
                    current_round is not None
                    and self._global_round(match, tournament["bracket_size"]) != current_round
                ):
                    continue
                participants = self._resolve_sources(c, ordered, match)
                if participants is None:
                    continue
                resolved = self._record_participants(c, match, participants, now)
                if resolved is None:
                    decided = True
                    continue
                if match["status"] == "pending":
                    c.execute(
                        "UPDATE player_tournament_matches SET status='ready' WHERE id=?",
                        (match["id"],),
                    )
                self._prepare_legs(c, tournament, resolved, now)
                if self._finish_match(c, tournament, resolved, seeds, now):
                    decided = True
            # The next pass can only open if this one decided a match or bye.
            # Reading again also includes a reset just created by the final.
            if not decided:
                break
            rows = self._ordered_matches(c, identifier)
        self._finalize(c, identifier, now)

    def _finalize(self, c, identifier: str, now: datetime) -> None:
        row = self._load(c, identifier)
        if row["status"] != "running" or row["review_state"] != "ok":
            return
        final = c.execute(
            "SELECT * FROM player_tournament_matches WHERE tournament_id=? AND bracket='grand_final'",
            (identifier,),
        ).fetchone()
        if final is None or final["status"] != "complete":
            return
        if final["winner"] == final["participant_a"]:
            champion = final["winner"]
        else:
            reset = c.execute(
                "SELECT * FROM player_tournament_matches WHERE tournament_id=? AND bracket='reset'",
                (identifier,),
            ).fetchone()
            if reset is None or reset["status"] != "complete":
                return
            champion = reset["winner"]
        c.execute(
            "UPDATE player_tournaments SET status='complete',official_winner=?,completed_at=?,version=version+1 WHERE id=? AND status='running'",
            (champion, micros(now), identifier),
        )
        if c.execute("SELECT changes()").fetchone()[0] == 1:
            self._audit(c, identifier, None, "champion", now, details={"winner": champion})

    # -- projections ------------------------------------------------------

    @staticmethod
    def _accounts(c, identifiers):
        from .progression import selected_portrait_ids

        accounts = resolve_accounts(c, identifiers)
        portraits = selected_portrait_ids(c, accounts)
        return {key: {**value, "portrait_id": portraits[key]} for key, value in accounts.items()}

    @staticmethod
    def _public(accounts: dict, account_id: str | None):
        if not account_id:
            return None
        return {"account_id": account_id, **accounts.get(account_id, {"name": ""})}

    @staticmethod
    def _start_state(row, entrants: int, now: datetime) -> str | None:
        if row["status"] != "registration":
            return None
        planned = row["scheduled_start_at"]
        if planned is None:
            return "waiting_for_entrants" if entrants < 2 else "manual_start"
        full = entrants == row["capacity"]
        if row["start_due_at"] is None or (not full and micros(now) > planned):
            return "missed_start"
        if not full:
            return "waiting_for_entrants"
        return "waiting_for_start" if micros(now) < planned else "start_pending"

    def _summary(self, c, row, accounts=None, *, now: datetime) -> dict:
        entrants = (
            row["entrant_count"]
            if "entrant_count" in row.keys()
            else c.execute(
                "SELECT COUNT(*) FROM player_tournament_entrants WHERE tournament_id=? AND status='registered'",
                (row["id"],),
            ).fetchone()[0]
        )
        if accounts is None:
            accounts = self._accounts(c, [row["official_winner"]])
        return {
            "id": row["id"],
            "name": row["name"],
            "ruleset": row["ruleset"],
            "format": row["format"],
            "status": row["status"],
            "review_state": row["review_state"],
            "capacity": row["capacity"],
            "match_legs": row["match_legs"],
            "time_per_move": row["time_per_move"],
            "time_control": row["time_control"],
            "increment_seconds": row["increment_seconds"],
            "match_mode": row["match_mode"],
            "created_by": row["created_by"],
            "progression_policy": row["progression_policy"],
            "bracket_size": row["bracket_size"],
            "entrant_count": entrants,
            "registration_open": row["status"] == "registration" and entrants < row["capacity"],
            "start_state": self._start_state(row, entrants, now),
            "version": row["version"],
            "created_at": stamp(instant(row["created_at"])),
            "started_at": stamp(instant(row["started_at"])) if row["started_at"] else None,
            "completed_at": stamp(instant(row["completed_at"])) if row["completed_at"] else None,
            "scheduled_start_at": stamp(instant(row["scheduled_start_at"]))
            if row["scheduled_start_at"] is not None
            else None,
            # Retain the response key for older clients; deadlines no longer apply.
            "registration_deadline": None,
            "official_winner": self._public(accounts, row["official_winner"]),
        }

    def _detail(self, c, identifier: str, account=None) -> dict:
        row = self._load(c, identifier)
        entrants = c.execute(
            """SELECT e.*,a.active,a.role,a.email_verified_at FROM player_tournament_entrants e
            JOIN accounts a ON a.id=e.account_id WHERE e.tournament_id=? AND e.status='registered'
            ORDER BY e.seed,e.registered_at,e.account_id""",
            (identifier,),
        ).fetchall()
        now = self._now()
        accounts = self._accounts(c, [row["official_winner"], *[e["account_id"] for e in entrants]])
        detail = self._summary(c, row, accounts, now=now)
        if account is not None:
            detail["capabilities"] = self._capabilities(
                c, account, row, entrants=[dict(entry) for entry in entrants], now=now
            )
        losses = dict(
            c.execute(
                "SELECT loser,COUNT(*) FROM player_tournament_matches WHERE tournament_id=? AND status='complete' AND loser IS NOT NULL GROUP BY loser",
                (identifier,),
            )
        )
        detail["entrants"] = [
            {
                "account_id": entry["account_id"],
                "seed": entry["seed"],
                "losses": losses.get(entry["account_id"], 0),
                "eliminated": losses.get(entry["account_id"], 0) >= 2,
                "registered_at": stamp(instant(entry["registered_at"])),
                **accounts.get(entry["account_id"], {"name": ""}),
            }
            for entry in entrants
        ]
        detail["match_counts"] = {
            status: count
            for status, count in c.execute(
                "SELECT status,COUNT(*) FROM player_tournament_matches WHERE tournament_id=? GROUP BY status",
                (identifier,),
            )
        }
        return detail

    def listing(
        self, credential: str, *, status=None, ruleset=None, phase=None, limit=20, cursor=None
    ) -> dict:
        if status is not None and status not in (
            "registration",
            "running",
            "complete",
            "cancelled",
        ):
            raise OnlineError(422, "Unknown tournament status.")
        if ruleset is not None and ruleset not in RULESETS:
            raise OnlineError(422, "Unknown tournament ruleset.")
        if phase not in (None, "upcoming"):
            raise OnlineError(422, "Unknown tournament phase.")
        if type(limit) is not int or not 1 <= limit <= MAX_LIST_PAGE:
            raise OnlineError(422, "Invalid pagination.")
        with self.store.connection() as c:
            c.execute("BEGIN")
            account = self._identity(c, credential)
            now = self._now()
            clauses, params = [], []
            if status is not None:
                clauses.append("status=?")
                params.append(status)
            if ruleset is not None:
                clauses.append("ruleset=?")
                params.append(ruleset)
            if phase == "upcoming":
                clauses.append("status='registration' AND scheduled_start_at>?")
                params.append(micros(now))
            if cursor is not None:
                created, identifier = listing_cursor(cursor, status, ruleset, phase)
                clauses.append("(created_at,id) < (?,?)")
                params.extend([created, identifier])
            where = "WHERE " + " AND ".join(clauses) if clauses else ""
            rows = [
                row
                for row in c.execute(
                    f"""SELECT *, (SELECT COUNT(*) FROM player_tournament_entrants e
                    WHERE e.tournament_id=player_tournaments.id AND e.status='registered') AS entrant_count
                    FROM player_tournaments {where} ORDER BY created_at DESC,id DESC LIMIT ?""",
                    (*params, limit + 1),
                )
            ]
            has_more = len(rows) > limit
            rows = rows[:limit]
            accounts = self._accounts(c, [row["official_winner"] for row in rows])
            items = [self._summary(c, row, accounts, now=now) for row in rows]
            next_cursor = (
                encode_cursor(
                    {
                        "k": "listing",
                        "s": status,
                        "r": ruleset,
                        "p": phase,
                        "t": rows[-1]["created_at"],
                        "i": rows[-1]["id"],
                    }
                )
                if has_more and rows
                else None
            )
            return {
                "items": items,
                "next_cursor": next_cursor,
                "limit": limit,
                "capabilities": self._capabilities(c, account, now=now),
            }

    def detail(self, credential: str, identifier: str) -> dict:
        with self.store.connection() as c:
            c.execute("BEGIN")
            account = self._identity(c, credential)
            return self._detail(c, identifier, account)

    @staticmethod
    def _match_legs(c, matches) -> dict[str, list[sqlite3.Row]]:
        grouped: dict[str, list[sqlite3.Row]] = {}
        if matches:
            placeholders = ",".join("?" for _ in matches)
            for leg in c.execute(
                f"""SELECT l.*, g.status AS game_status FROM player_tournament_legs l
                LEFT JOIN online_games g ON g.id=l.game_id
                WHERE l.match_id IN ({placeholders}) ORDER BY l.leg""",
                [match["id"] for match in matches],
            ):
                grouped.setdefault(leg["match_id"], []).append(leg)
        return grouped

    def _match_projection(self, match, accounts, *, tournament, current_round, legs) -> dict:
        global_round = (
            self._global_round(match, tournament["bracket_size"])
            if tournament["progression_policy"] == "synchronized"
            else None
        )
        future_round = (
            match["status"] == "pending"
            and current_round is not None
            and global_round is not None
            and global_round > current_round
        )
        retained_game_state = None
        if future_round and legs:
            if any(leg["game_status"] == "active" for leg in legs):
                retained_game_state = "active"
            elif all(leg["game_status"] in ("completed", "voided") for leg in legs):
                retained_game_state = "results_held"
        pending_games = (
            max(0, tournament["match_legs"] - len(legs))
            if tournament["match_mode"] == "sequential" and match["status"] == "ready"
            else 0
        )
        return_game_state = None
        if pending_games and legs and tournament["review_state"] == "ok":
            return_game_state = (
                "awaiting_settlement"
                if legs[-1]["game_status"] in ("completed", "voided")
                else "waiting_for_game"
            )
        return {
            "id": match["id"],
            "bracket": match["bracket"],
            "round": match["round"],
            "slot": match["slot"],
            "status": match["status"],
            "decided_by": match["decided_by"],
            "source_a": match["source_a"],
            "source_b": match["source_b"],
            "participants": {
                "a": self._public(accounts, match["participant_a"]),
                "b": self._public(accounts, match["participant_b"]),
            },
            "winner": self._public(accounts, match["winner"]),
            "loser": self._public(accounts, match["loser"]),
            "global_round": global_round,
            "waiting_for_round": future_round and tournament["review_state"] == "ok",
            "retained_game_state": retained_game_state,
            "pending_games": pending_games,
            "return_game_state": return_game_state,
            "legs": [
                {
                    "leg": leg["leg"],
                    "game_id": leg["game_id"],
                    "game_status": leg["game_status"],
                    "outcome": leg["outcome"],
                    "result_revision": leg["result_revision"],
                    "settled_at": stamp(instant(leg["settled_at"])) if leg["settled_at"] else None,
                }
                for leg in legs
            ],
            "created_at": stamp(instant(match["created_at"])),
            "settled_at": stamp(instant(match["settled_at"])) if match["settled_at"] else None,
        }

    def _bracket(self, c, identifier: str) -> dict:
        row = self._load(c, identifier)
        matches = c.execute(
            "SELECT * FROM player_tournament_matches WHERE tournament_id=? ORDER BY bracket,round,slot",
            (identifier,),
        ).fetchall()
        preview = row["bracket_size"] is None and not matches
        size = power_of_two_at_least(row["capacity"]) if preview else row["bracket_size"]
        if preview:
            matches = self._build_bracket(
                c, identifier, size, instant(row["created_at"]), preview=True
            )
        current_round = self._current_round(c, row, matches)
        finished = outstanding = 0
        accounts = self._accounts(
            c,
            [
                seat
                for match in matches
                for seat in (
                    match["participant_a"],
                    match["participant_b"],
                    match["winner"],
                    match["loser"],
                )
            ],
        )
        saved_legs = {} if preview else self._match_legs(c, matches)
        groups: dict[str, dict[int, list[dict]]] = {"upper": {}, "lower": {}}
        final = reset = None
        for match in matches:
            projection = self._match_projection(
                match,
                accounts,
                tournament=row,
                current_round=current_round,
                legs=saved_legs.get(match["id"], []),
            )
            global_round = projection["global_round"]
            if current_round is not None and global_round == current_round:
                if match["status"] in TERMINAL_MATCHES:
                    finished += 1
                else:
                    outstanding += 1
            if match["bracket"] in ("upper", "lower"):
                groups[match["bracket"]].setdefault(match["round"], []).append(projection)
            elif match["bracket"] == "grand_final":
                final = projection
            else:
                reset = projection
        return {
            "tournament_id": identifier,
            "version": row["version"],
            "status": row["status"],
            "review_state": row["review_state"],
            "bracket_size": size,
            "preview": preview,
            "round_progress": {
                "number": current_round,
                "finished_matches": finished,
                "outstanding_matches": outstanding,
            }
            if current_round is not None
            else None,
            "upper": [
                {"round": round_, "matches": entries}
                for round_, entries in sorted(groups["upper"].items())
            ],
            "lower": [
                {"round": round_, "matches": entries}
                for round_, entries in sorted(groups["lower"].items())
            ],
            "final": final,
            "reset": reset,
        }

    def bracket(self, credential: str, identifier: str) -> dict:
        with self.store.connection() as c:
            c.execute("BEGIN")
            self._identity(c, credential)
            return self._bracket(c, identifier)

    def matches(self, credential: str, identifier: str, *, limit=50, cursor=None) -> dict:
        if type(limit) is not int or not 1 <= limit <= MAX_MATCH_PAGE:
            raise OnlineError(422, "Invalid pagination.")
        with self.store.connection() as c:
            c.execute("BEGIN")
            self._identity(c, credential)
            tournament = self._load(c, identifier)
            params: list = [identifier]
            clause = ""
            if cursor is not None:
                last = match_cursor(cursor, identifier)
                clause = " AND id>?"
                params.append(last)
            rows = c.execute(
                f"SELECT * FROM player_tournament_matches WHERE tournament_id=?{clause} ORDER BY id LIMIT ?",
                (*params, limit + 1),
            ).fetchall()
            has_more = len(rows) > limit
            rows = rows[:limit]
            current_round = self._current_round(c, tournament)
            accounts = self._accounts(
                c,
                [
                    seat
                    for match in rows
                    for seat in (
                        match["participant_a"],
                        match["participant_b"],
                        match["winner"],
                        match["loser"],
                    )
                ],
            )
            saved_legs = self._match_legs(c, rows)
            items = [
                self._match_projection(
                    match,
                    accounts,
                    tournament=tournament,
                    current_round=current_round,
                    legs=saved_legs.get(match["id"], []),
                )
                for match in rows
            ]
            next_cursor = (
                encode_cursor({"k": "matches", "t": identifier, "i": rows[-1]["id"]})
                if has_more and rows
                else None
            )
            return {"items": items, "next_cursor": next_cursor, "limit": limit}

    def match(self, credential: str, identifier: str, match_id: str) -> dict:
        with self.store.connection() as c:
            c.execute("BEGIN")
            self._identity(c, credential)
            tournament = self._load(c, identifier)
            row = c.execute(
                "SELECT * FROM player_tournament_matches WHERE id=? AND tournament_id=?",
                (match_id, identifier),
            ).fetchone()
            if row is None:
                raise OnlineError(404, "Tournament match not found.")
            accounts = self._accounts(
                c,
                [row["participant_a"], row["participant_b"], row["winner"], row["loser"]],
            )
            return self._match_projection(
                row,
                accounts,
                tournament=tournament,
                current_round=self._current_round(c, tournament),
                legs=self._match_legs(c, [row]).get(match_id, []),
            )

    def audit(self, credential: str, identifier: str, *, limit=50) -> dict:
        if type(limit) is not int or not 1 <= limit <= MAX_AUDIT_PAGE:
            raise OnlineError(422, "Invalid pagination.")
        with self.store.connection() as c:
            c.execute("BEGIN")
            self._identity(c, credential, require="accounts")
            self._load(c, identifier)
            entries = c.execute(
                "SELECT * FROM player_tournament_audit WHERE tournament_id=? ORDER BY id DESC LIMIT ?",
                (identifier, limit),
            ).fetchall()
            return {
                "tournament_id": identifier,
                "items": [
                    {
                        "id": entry["id"],
                        "action": entry["action"],
                        "actor_id": entry["actor_id"],
                        "match_id": entry["match_id"],
                        "note": entry["note"],
                        "details": json.loads(entry["details"]) if entry["details"] else None,
                        "at": stamp(instant(entry["at"])),
                    }
                    for entry in entries
                ],
            }
