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
import fcntl
import json
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime
from uuid import uuid4

from . import lifecycle
from .account_policy import resolve_accounts
from .engine import RULESETS
from .online_core import OnlineCore, OnlineError, instant, micros, stamp
from .security_context import allowed, online_eligible

UNRESOLVED = object()

MAX_ENTRANTS = 64
MAX_MATCH_LEGS = 5
MAX_AUDIT_PAGE = 100
MAX_LIST_PAGE = 50
MAX_MATCH_PAGE = 200

BRACKET_ORDER = {"upper": 0, "lower": 1, "grand_final": 2, "reset": 3}


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


def encode_cursor(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> dict:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode()))
    except (ValueError, TypeError):
        raise OnlineError(422, "Invalid pagination cursor.") from None
    if not isinstance(payload, dict):
        raise OnlineError(422, "Invalid pagination cursor.")
    return payload


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

    @contextmanager
    def _write(self):
        with self.auth.gate_path.open("rb") as gate:
            try:
                fcntl.flock(gate, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                raise OnlineError(503, "Deployment in progress. Please retry shortly.") from None
            with self.store.connection() as c:
                c.execute("PRAGMA foreign_keys=ON")
                c.execute("BEGIN IMMEDIATE")
                yield c

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
        match_legs: int,
        time_per_move: int,
        registration_deadline: datetime | None = None,
    ) -> dict:
        name = name.strip()
        if not name or len(name) > 120:
            raise OnlineError(422, "A tournament name of 1 to 120 characters is required.")
        if ruleset not in RULESETS:
            raise OnlineError(422, "A supported ruleset is required.")
        if type(capacity) is not int or not 2 <= capacity <= MAX_ENTRANTS:
            raise OnlineError(422, f"Capacity must be an integer from 2 to {MAX_ENTRANTS}.")
        if type(match_legs) is not int or not 1 <= match_legs <= MAX_MATCH_LEGS:
            raise OnlineError(422, f"Match legs must be an integer from 1 to {MAX_MATCH_LEGS}.")
        if type(time_per_move) is not int or not 60 <= time_per_move <= 1209600:
            raise OnlineError(422, "A time limit of 60 to 1209600 seconds is required.")
        deadline = None
        if registration_deadline is not None:
            if registration_deadline.tzinfo is None or registration_deadline.utcoffset() is None:
                raise OnlineError(422, "The registration deadline must include a timezone.")
            deadline = micros(registration_deadline)
        with self._write() as c:
            account = self._identity(c, credential, require="tournaments")
            now = self._now()
            identifier = "ptournament-" + uuid4().hex
            c.execute(
                "INSERT INTO player_tournaments(id,name,ruleset,format,status,review_state,capacity,match_legs,time_per_move,registration_deadline,created_by,created_at,version) VALUES(?,?,?,'double_elimination','registration','ok',?,?,?,?,?,?,0)",
                (
                    identifier,
                    name,
                    ruleset,
                    capacity,
                    match_legs,
                    time_per_move,
                    deadline,
                    account["id"],
                    micros(now),
                ),
            )
            self._audit(
                c,
                identifier,
                account["id"],
                "created",
                now,
                details={"capacity": capacity, "ruleset": ruleset, "match_legs": match_legs},
            )
            return self._detail(c, identifier)

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
            if (
                row["registration_deadline"] is not None
                and micros(now) >= row["registration_deadline"]
            ):
                raise OnlineError(409, "The registration deadline has passed.")
            existing = c.execute(
                "SELECT * FROM player_tournament_entrants WHERE tournament_id=? AND account_id=?",
                (identifier, account["id"]),
            ).fetchone()
            if existing is not None and existing["status"] == "registered":
                return self._detail(c, identifier)
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
            self._audit(c, identifier, account["id"], "registered", now)
            return self._detail(c, identifier)

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
            self._audit(c, identifier, account["id"], "withdrawn", now)
            return self._detail(c, identifier)

    # -- administration ---------------------------------------------------

    def start(self, credential: str, identifier: str, version: int, seeds=None) -> dict:
        with self._write() as c:
            account = self._identity(c, credential, require="tournaments")
            row = self._load(c, identifier)
            self._version(row, version)
            if row["status"] != "registration":
                raise OnlineError(409, "Only a tournament in registration can be started.")
            registered = [
                entry["account_id"]
                for entry in c.execute(
                    "SELECT account_id FROM player_tournament_entrants WHERE tournament_id=? AND status='registered' ORDER BY registered_at,account_id",
                    (identifier,),
                )
            ]
            if len(registered) < 2:
                raise OnlineError(409, "At least two registered entrants are required.")
            if seeds is None:
                ordered = registered
            else:
                if (
                    len(set(seeds)) != len(seeds)
                    or sorted(seeds) != sorted(registered)
                    or any(not isinstance(seed, str) for seed in seeds)
                ):
                    raise OnlineError(
                        422, "Seeding must list every registered entrant exactly once."
                    )
                ordered = list(seeds)
            now = self._now()
            for index, account_id in enumerate(ordered, start=1):
                c.execute(
                    "UPDATE player_tournament_entrants SET seed=? WHERE tournament_id=? AND account_id=?",
                    (index, identifier, account_id),
                )
            size = power_of_two_at_least(len(ordered))
            c.execute(
                "UPDATE player_tournaments SET status='running',started_at=?,bracket_size=?,version=version+1 WHERE id=? AND version=?",
                (micros(now), size, identifier, version),
            )
            if c.execute("SELECT changes()").fetchone()[0] != 1:
                raise OnlineError(409, "This tournament changed. Reload before retrying.")
            self._build_bracket(c, identifier, size, now)
            self._audit(
                c,
                identifier,
                account["id"],
                "started",
                now,
                details={"entrants": len(ordered), "bracket_size": size},
            )
            self._advance(c, identifier, now)
            return self._detail(c, identifier)

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
            return self._detail(c, identifier)

    def settle(self, credential: str, identifier: str) -> dict:
        with self._write() as c:
            self._identity(c, credential)
            row = self._load(c, identifier)
            if row["status"] not in ("running", "complete"):
                return self._detail(c, identifier)
            now = self._now()
            self._advance(c, identifier, now)
            return self._detail(c, identifier)

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
            if match["status"] == "disputed":
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
                "SELECT game_id FROM player_tournament_legs WHERE match_id=?", (match_id,)
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
            return self._detail(c, identifier)

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
            # Deterministic match identities keep valid legs attached; only the
            # derived participants/results and any bracket reset are discarded.
            c.execute(
                "DELETE FROM player_tournament_legs WHERE match_id IN (SELECT id FROM player_tournament_matches WHERE tournament_id=? AND bracket='reset')",
                (identifier,),
            )
            c.execute(
                "DELETE FROM player_tournament_matches WHERE tournament_id=? AND bracket='reset'",
                (identifier,),
            )
            c.execute(
                "UPDATE player_tournament_matches SET status='pending',participant_a=NULL,participant_b=NULL,winner=NULL,loser=NULL,decided_by=NULL,settled_at=NULL WHERE tournament_id=?",
                (identifier,),
            )
            c.execute(
                "UPDATE player_tournaments SET review_state='ok',version=version+1 WHERE id=? AND version=?",
                (identifier, version),
            )
            if c.execute("SELECT changes()").fetchone()[0] != 1:
                raise OnlineError(409, "This tournament changed. Reload before retrying.")
            self._audit(c, identifier, account["id"], "rebuild", now, note=note)
            self._advance(c, identifier, now)
            return self._detail(c, identifier)

    # -- bracket construction --------------------------------------------

    def _build_bracket(self, c, identifier: str, size: int, now: datetime) -> None:
        order = seed_order(size)
        at = micros(now)

        def new(bracket, round_, slot, source_a, source_b):
            match_id = f"{identifier}:{bracket}:{round_}:{slot}"
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

    # -- advancement ------------------------------------------------------

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
        if feeder["status"] not in ("bye", "complete"):
            return UNRESOLVED
        return feeder["winner"] if kind == "winner" else feeder["loser"]

    def _resolve_sources(self, c, ordered, row):
        if row["bracket"] == "reset":
            return row["participant_a"], row["participant_b"]
        first = self._resolve(c, ordered, row["source_a"])
        if first is UNRESOLVED:
            return None
        second = self._resolve(c, ordered, row["source_b"])
        if second is UNRESOLVED:
            return None
        return first, second

    def _prepare_legs(self, c, identifier: str, match, now: datetime) -> None:
        participants = {match["participant_a"], match["participant_b"]}
        for leg in c.execute(
            "SELECT * FROM player_tournament_legs WHERE match_id=? ORDER BY leg",
            (match["id"],),
        ).fetchall():
            game = c.execute(
                "SELECT attackers_id,defenders_id FROM online_games WHERE id=?", (leg["game_id"],)
            ).fetchone()
            if game is None or {game["attackers_id"], game["defenders_id"]} != participants:
                c.execute(
                    "DELETE FROM player_tournament_legs WHERE match_id=? AND leg=?",
                    (match["id"], leg["leg"]),
                )
        if c.execute(
            "SELECT 1 FROM player_tournament_legs WHERE match_id=?", (match["id"],)
        ).fetchone():
            return
        tournament = self._load(c, identifier)
        for index in range(1, tournament["match_legs"] + 1):
            side = "attackers" if index % 2 == 1 else "defenders"
            game_id = self.online.seat_game(
                c,
                creator_id=match["participant_a"],
                opponent_id=match["participant_b"],
                ruleset=tournament["ruleset"],
                creator_side=side,
                control=lifecycle.TimeControl(tournament["time_per_move"], "per_move", 0),
                now=now,
                request_id=f"tournament:{match['id']}:{index}:{uuid4().hex}",
            )
            attackers = match["participant_a"] if side == "attackers" else match["participant_b"]
            defenders = match["participant_b"] if side == "attackers" else match["participant_a"]
            c.execute(
                "INSERT INTO player_tournament_legs(match_id,leg,game_id,attackers_id,defenders_id,created_at) VALUES(?,?,?,?,?,?)",
                (match["id"], index, game_id, attackers, defenders, micros(now)),
            )

    def _settle_legs(self, c, match, now: datetime) -> bool:
        changed = False
        for leg in c.execute(
            "SELECT * FROM player_tournament_legs WHERE match_id=? ORDER BY leg",
            (match["id"],),
        ).fetchall():
            outcome, revision = None, None
            game = c.execute(
                "SELECT status,result_revision FROM online_games WHERE id=?", (leg["game_id"],)
            ).fetchone()
            if (
                game is not None
                and game["status"] in ("completed", "voided")
                and game["result_revision"] is not None
            ):
                revision = game["result_revision"]
                saved = c.execute(
                    "SELECT payload FROM online_results WHERE game_id=? AND revision=?",
                    (leg["game_id"], revision),
                ).fetchone()
                winner = json.loads(saved[0]).get("winner") if saved is not None else None
                if game["status"] == "voided" or winner is None:
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
            "INSERT INTO player_tournament_matches(id,tournament_id,bracket,round,slot,status,participant_a,participant_b,source_a,source_b,created_at) VALUES(?,?,'reset',1,0,'ready',?,?,?,?,?)",
            (
                reset_id,
                identifier,
                final["participant_a"],
                final["participant_b"],
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

    def _advance(self, c, identifier: str, now: datetime) -> None:
        if self._load(c, identifier)["review_state"] != "ok":
            # Bracket-wide freeze: a disputed tournament creates no new games or
            # downstream matches and does not advance any branch, including
            # unaffected ones, until a rebuild re-derives it.
            return
        seeds = {
            entry["account_id"]: entry["seed"]
            for entry in c.execute(
                "SELECT account_id,seed FROM player_tournament_entrants WHERE tournament_id=?",
                (identifier,),
            )
        }
        assigned = [value for value in seeds.values() if value is not None]
        ordered: list[str] = [""] * (max(assigned) if assigned else 0)
        for account_id, seed in seeds.items():
            if seed is not None:
                ordered[seed - 1] = account_id
        total = c.execute(
            "SELECT COUNT(*) FROM player_tournament_matches WHERE tournament_id=?", (identifier,)
        ).fetchone()[0]
        changed, guard = True, 0
        while changed and guard <= 2 * total + 16:
            changed = False
            guard += 1
            rows = c.execute(
                "SELECT * FROM player_tournament_matches WHERE tournament_id=? ORDER BY CASE bracket WHEN 'upper' THEN 0 WHEN 'lower' THEN 1 WHEN 'grand_final' THEN 2 ELSE 3 END, round, slot",
                (identifier,),
            ).fetchall()
            for row in rows:
                if row["status"] == "complete":
                    # An administrative decision is independent of the recorded
                    # legs, so settling them never reopens it.
                    if self._settle_legs(c, row, now) and row["decided_by"] != "admin_override":
                        self._dispute(c, identifier, row, now)
                        # Freeze the whole bracket now that review is required.
                        return
                    continue
                if row["status"] in ("bye", "disputed"):
                    continue
                resolution = self._resolve_sources(c, ordered, row)
                if resolution is None:
                    continue
                first, second = resolution
                if (first, second) != (row["participant_a"], row["participant_b"]):
                    c.execute(
                        "UPDATE player_tournament_matches SET participant_a=?,participant_b=? WHERE id=?",
                        (first, second, row["id"]),
                    )
                    row = c.execute(
                        "SELECT * FROM player_tournament_matches WHERE id=?", (row["id"],)
                    ).fetchone()
                    changed = True
                if first is None or second is None:
                    c.execute(
                        "UPDATE player_tournament_matches SET status='bye',winner=?,loser=NULL,decided_by='bye',settled_at=? WHERE id=?",
                        (first or second, micros(now), row["id"]),
                    )
                    changed = True
                    continue
                if row["status"] == "pending":
                    c.execute(
                        "UPDATE player_tournament_matches SET status='ready' WHERE id=?",
                        (row["id"],),
                    )
                    changed = True
                    continue
                self._prepare_legs(c, identifier, row, now)
                if self._settle_legs(c, row, now):
                    changed = True
                legs = c.execute(
                    "SELECT * FROM player_tournament_legs WHERE match_id=? ORDER BY leg",
                    (row["id"],),
                ).fetchall()
                if legs and all(leg["outcome"] is not None for leg in legs):
                    self._decide_from_legs(c, identifier, row, legs, seeds, now)
                    changed = True
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
    def _public(accounts: dict, account_id: str | None):
        if not account_id:
            return None
        return {"account_id": account_id, **accounts.get(account_id, {"name": ""})}

    def _summary(self, c, row, accounts=None) -> dict:
        entrants = c.execute(
            "SELECT COUNT(*) FROM player_tournament_entrants WHERE tournament_id=? AND status='registered'",
            (row["id"],),
        ).fetchone()[0]
        if accounts is None:
            accounts = resolve_accounts(c, [row["official_winner"]])
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
            "bracket_size": row["bracket_size"],
            "entrant_count": entrants,
            "version": row["version"],
            "created_at": stamp(instant(row["created_at"])),
            "started_at": stamp(instant(row["started_at"])) if row["started_at"] else None,
            "completed_at": stamp(instant(row["completed_at"])) if row["completed_at"] else None,
            "registration_deadline": stamp(instant(row["registration_deadline"]))
            if row["registration_deadline"] is not None
            else None,
            "official_winner": self._public(accounts, row["official_winner"]),
        }

    def _detail(self, c, identifier: str) -> dict:
        row = self._load(c, identifier)
        entrants = c.execute(
            "SELECT * FROM player_tournament_entrants WHERE tournament_id=? AND status='registered' ORDER BY seed,registered_at,account_id",
            (identifier,),
        ).fetchall()
        accounts = resolve_accounts(
            c, [row["official_winner"], *[e["account_id"] for e in entrants]]
        )
        detail = self._summary(c, row, accounts)
        detail["entrants"] = [
            {
                "account_id": entry["account_id"],
                "seed": entry["seed"],
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

    def listing(self, credential: str, *, status=None, limit=20, cursor=None) -> dict:
        if status is not None and status not in (
            "registration",
            "running",
            "complete",
            "cancelled",
        ):
            raise OnlineError(422, "Unknown tournament status.")
        if type(limit) is not int or not 1 <= limit <= MAX_LIST_PAGE:
            raise OnlineError(422, "Invalid pagination.")
        with self.store.connection() as c:
            c.execute("BEGIN")
            self._identity(c, credential)
            clauses, params = [], []
            if status is not None:
                clauses.append("status=?")
                params.append(status)
            if cursor is not None:
                decoded = decode_cursor(cursor)
                created, identifier = decoded.get("t"), decoded.get("i")
                if not isinstance(created, int) or not isinstance(identifier, str):
                    raise OnlineError(422, "Invalid pagination cursor.")
                clauses.append("(created_at,id) < (?,?)")
                params.extend([created, identifier])
            where = "WHERE " + " AND ".join(clauses) if clauses else ""
            rows = [
                self._load(c, row[0])
                for row in c.execute(
                    f"SELECT id FROM player_tournaments {where} ORDER BY created_at DESC,id DESC LIMIT ?",
                    (*params, limit + 1),
                )
            ]
            has_more = len(rows) > limit
            rows = rows[:limit]
            accounts = resolve_accounts(c, [row["official_winner"] for row in rows])
            items = [self._summary(c, row, accounts) for row in rows]
            next_cursor = (
                encode_cursor({"t": rows[-1]["created_at"], "i": rows[-1]["id"]})
                if has_more and rows
                else None
            )
            return {"items": items, "next_cursor": next_cursor, "limit": limit}

    def detail(self, credential: str, identifier: str) -> dict:
        with self.store.connection() as c:
            c.execute("BEGIN")
            self._identity(c, credential)
            return self._detail(c, identifier)

    @staticmethod
    def _games(c, identifier: str) -> dict[str, str]:
        return {
            row["id"]: row["status"]
            for row in c.execute(
                "SELECT g.id,g.status FROM online_games g WHERE g.id IN (SELECT game_id FROM player_tournament_legs l JOIN player_tournament_matches m ON m.id=l.match_id WHERE m.tournament_id=?)",
                (identifier,),
            )
        }

    def _match_projection(self, c, match, accounts, games) -> dict:
        legs = c.execute(
            "SELECT * FROM player_tournament_legs WHERE match_id=? ORDER BY leg", (match["id"],)
        ).fetchall()
        return {
            "id": match["id"],
            "bracket": match["bracket"],
            "round": match["round"],
            "slot": match["slot"],
            "status": match["status"],
            "decided_by": match["decided_by"],
            "participants": {
                "a": self._public(accounts, match["participant_a"]),
                "b": self._public(accounts, match["participant_b"]),
            },
            "winner": self._public(accounts, match["winner"]),
            "loser": self._public(accounts, match["loser"]),
            "legs": [
                {
                    "leg": leg["leg"],
                    "game_id": leg["game_id"],
                    "game_status": games.get(leg["game_id"]),
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
        accounts = resolve_accounts(
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
        games = self._games(c, identifier)
        groups: dict[str, dict[int, list[dict]]] = {"upper": {}, "lower": {}}
        final = reset = None
        for match in matches:
            projection = self._match_projection(c, match, accounts, games)
            if match["bracket"] in ("upper", "lower"):
                groups[match["bracket"]].setdefault(match["round"], []).append(projection)
            elif match["bracket"] == "grand_final":
                final = projection
            else:
                reset = projection
        return {
            "tournament_id": identifier,
            "status": row["status"],
            "review_state": row["review_state"],
            "bracket_size": row["bracket_size"],
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
            self._load(c, identifier)
            params: list = [identifier]
            clause = ""
            if cursor is not None:
                decoded = decode_cursor(cursor)
                last = decoded.get("i")
                if not isinstance(last, str):
                    raise OnlineError(422, "Invalid pagination cursor.")
                clause = " AND id>?"
                params.append(last)
            rows = c.execute(
                f"SELECT * FROM player_tournament_matches WHERE tournament_id=?{clause} ORDER BY id LIMIT ?",
                (*params, limit + 1),
            ).fetchall()
            has_more = len(rows) > limit
            rows = rows[:limit]
            accounts = resolve_accounts(
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
            games = self._games(c, identifier)
            items = [self._match_projection(c, match, accounts, games) for match in rows]
            next_cursor = encode_cursor({"i": rows[-1]["id"]}) if has_more and rows else None
            return {"items": items, "next_cursor": next_cursor, "limit": limit}

    def match(self, credential: str, identifier: str, match_id: str) -> dict:
        with self.store.connection() as c:
            c.execute("BEGIN")
            self._identity(c, credential)
            self._load(c, identifier)
            row = c.execute(
                "SELECT * FROM player_tournament_matches WHERE id=? AND tournament_id=?",
                (match_id, identifier),
            ).fetchone()
            if row is None:
                raise OnlineError(404, "Tournament match not found.")
            accounts = resolve_accounts(
                c,
                [row["participant_a"], row["participant_b"], row["winner"], row["loser"]],
            )
            return self._match_projection(c, row, accounts, self._games(c, identifier))

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
