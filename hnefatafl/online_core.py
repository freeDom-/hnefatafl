"""Authoritative online lifecycle, serialized by SQLite rather than Python locks.

Every identity is an existing session digest, never a caller-supplied account ID.
Engine moves, seats, deadlines and result revisions commit in one transaction.
"""

from contextlib import contextmanager
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
import fcntl
import json
import logging
import sqlite3
import secrets
import threading
import time
from uuid import UUID, uuid4

from . import lifecycle
from .engine import RULESETS, Move, State, apply_move, new_game
from .online_settings import OnlineSettings
from .security_context import allowed, online_eligible

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
LOGGER = logging.getLogger(__name__)


class OnlineError(ValueError):
    def __init__(self, status: int, message: str):
        self.status = status
        super().__init__(message)


def micros(moment: datetime) -> int:
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise OnlineError(503, "Authoritative time is unavailable.")
    delta = moment.astimezone(timezone.utc) - EPOCH
    return (delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds


def instant(value: int) -> datetime:
    return EPOCH + timedelta(microseconds=value)


def stamp(moment: datetime | None) -> str | None:
    return (
        moment.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
        if moment is not None
        else None
    )


def result_payload(result: lifecycle.Result) -> dict:
    payload = asdict(result)
    for field in ("started_at", "completed_at", "revised_at"):
        payload[field] = stamp(payload[field])
    return payload


def stored_result(payload: str) -> lifecycle.Result:
    data = json.loads(payload)
    for field in ("started_at", "completed_at", "revised_at"):
        data[field] = datetime.fromisoformat(data[field])
    return lifecycle.Result(**data)


def safe_position(position: State) -> dict:
    return {key: value for key, value in position.to_dict().items() if key != "history"}


class OnlineCore:
    def __init__(self, auth, settings: OnlineSettings, clock=None):
        self.auth, self.store, self.settings = auth, auth.store, settings
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None

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

    def _now(self):
        # Called only after acquiring the writer lock and reloading authority.
        return instant(micros(self.clock()))

    def _identity(self, c, credential: str, *, admission=False, admin=False) -> dict:
        row = c.execute(
            "SELECT a.* FROM accounts a JOIN sessions s ON a.id=s.account_id WHERE s.token_hash=? AND s.expires>?",
            (credential, time.time()),
        ).fetchone()
        account = dict(row) if row else None
        if not allowed(account, "play"):
            raise OnlineError(401, "A current authenticated session is required.")
        assert account is not None
        if admin and not allowed(account, "accounts"):
            raise OnlineError(403, "Administrator permission is required.")
        if admission and not online_eligible(account):
            raise OnlineError(403, "An active account with a verified email is required.")
        return account

    def _load(self, c, identifier: str) -> tuple[sqlite3.Row, lifecycle.Game]:
        row = c.execute("SELECT * FROM online_games WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise OnlineError(404, "Online game not found.")
        rules = RULESETS.get(row["ruleset"])
        if rules is None or row["ruleset_version"] != rules.version:
            raise OnlineError(503, "This game's pinned rules version is unsupported.")
        try:
            result = None
            if row["result_revision"] is not None:
                saved = c.execute(
                    "SELECT payload FROM online_results WHERE game_id=? AND revision=?",
                    (identifier, row["result_revision"]),
                ).fetchone()
                if saved is None:
                    raise ValueError("Missing result")
                result = stored_result(saved[0])
            position = State.from_dict(json.loads(row["position"]))
            if position.ruleset != row["ruleset"]:
                raise ValueError("Mismatched rules")
            game = lifecycle.Game(
                id=identifier,
                creator=row["creator_id"],
                creator_side=row["creator_side"] or "attackers",
                control=lifecycle.TimeControl(row["time_per_move"]),
                position=position,
                updated_at=instant(row["updated_at"]),
                status=lifecycle.Status(row["status"]),
                opponent=row["defenders_id"]
                if row["creator_side"] == "attackers"
                else row["attackers_id"],
                started_at=instant(row["started_at"]) if row["started_at"] is not None else None,
                deadline=instant(row["deadline"]) if row["deadline"] is not None else None,
                result=result,
            )
        except (ValueError, TypeError, KeyError, OverflowError):
            raise OnlineError(409, "Stored online state requires operator review.") from None
        return row, game

    @staticmethod
    def _version(row, version: int):
        if type(version) is not int or version < 0:
            raise OnlineError(422, "An integer expected version is required.")
        if row["version"] != version:
            raise OnlineError(409, "This game changed. Reload before retrying.")

    def _admission(self):
        if not self.settings.enabled or not self.settings.creation_policy_approved:
            raise OnlineError(503, "Online game admission is disabled pending policy approval.")

    def _creation_denial(self, c, account_id, now):
        # Index-backed scans stop at the configured cap, including when upgrading
        # a database that already exceeds the new limits. No new limiter keys.
        if c.execute(
            "SELECT 1 FROM online_games WHERE creator_id=? AND status='waiting' LIMIT 1 OFFSET ?",
            (account_id, self.settings.waiting_limit - 1),
        ).fetchone():
            reason = "waiting_limit"
        elif c.execute(
            "SELECT 1 FROM online_matches WHERE creator_id=? AND created_at>? LIMIT 1 OFFSET ?",
            (
                account_id,
                micros(now) - self.settings.creation_window_seconds * 1000000,
                self.settings.creation_limit - 1,
            ),
        ).fetchone():
            reason = "creation_rate"
        else:
            return None
        metrics = c.execute(
            "SELECT * FROM online_creation_metrics WHERE reason=?", (reason,)
        ).fetchone()
        at = micros(now)
        log_due = metrics["last_logged_at"] is None or at >= metrics["last_logged_at"] + 60000000
        c.execute(
            "UPDATE online_creation_metrics SET denials=denials+1,last_denied_at=?,last_logged_at=? WHERE reason=?",
            (at, at if log_due else metrics["last_logged_at"], reason),
        )
        return reason, metrics["denials"] + 1, log_due

    def create(
        self,
        credential: str,
        ruleset: str,
        side: str,
        seconds: int,
        request_id: str,
        mode: str = "single",
    ) -> dict:
        self._admission()
        try:
            control = lifecycle.TimeControl(seconds)
            request_id = str(UUID(request_id))
        except (ValueError, TypeError, AttributeError):
            raise OnlineError(
                422, "A valid duration and creation request UUID are required."
            ) from None
        if (
            ruleset not in RULESETS
            or side not in {"attackers", "defenders", "random"}
            or mode not in {"single", "simultaneous", "sequential"}
        ):
            raise OnlineError(422, "A supported ruleset and side are required.")
        with self._write() as c:
            account = self._identity(c, credential, admission=True)
            if c.execute(
                "SELECT 1 FROM online_games WHERE creator_id=? AND creation_request=?",
                (account["id"], request_id),
            ).fetchone():
                raise OnlineError(409, "Creation request already committed. Reload your games.")
            now = self._now()
            denial = self._creation_denial(c, account["id"], now)
            response = None
            if denial is None:
                game = lifecycle.waiting(
                    "online-" + uuid4().hex,
                    lifecycle.Actor(account["id"]),
                    ruleset,
                    side if side != "random" else "attackers",
                    now,
                    control,
                )
                match_id = "match-" + uuid4().hex
                c.execute(
                    "INSERT INTO online_matches VALUES(?,?,NULL,?,?,?,?,?,?,NULL,NULL)",
                    (
                        match_id,
                        account["id"],
                        side,
                        mode,
                        ruleset,
                        RULESETS[ruleset].version,
                        seconds,
                        micros(now),
                    ),
                )
                self._insert_game(
                    c,
                    game,
                    request_id,
                    match_id,
                    1,
                    now,
                    assigned_side=None if side == "random" else side,
                )
                response = self._projection(c, *self._load(c, game.id))
        # Commit denial metrics without committing any game. Log only after that
        # commit, at most once per reason/minute across connections and restarts.
        if denial is not None:
            reason, total, log_due = denial
            if log_due:
                LOGGER.warning("Online creation denied: %s; cumulative denials=%d", reason, total)
            raise OnlineError(
                429,
                "Too many waiting games. Wait for an opponent or cancel a waiting game."
                if reason == "waiting_limit"
                else "Game creation rate limit reached. Try again later.",
            )
        assert response is not None
        return {"game": response, "transition": "created", "action_accepted": True}

    def monitoring(self, credential: str) -> dict:
        with self.store.connection() as c:
            c.execute("BEGIN")
            self._identity(c, credential, admin=True)
            page_size = c.execute("PRAGMA page_size").fetchone()[0]
            pages = c.execute("PRAGMA page_count").fetchone()[0]
            free = c.execute("PRAGMA freelist_count").fetchone()[0]
            counts = {status.value: 0 for status in lifecycle.Status}
            counts.update(
                dict(
                    c.execute("SELECT status,COUNT(*) FROM online_games GROUP BY status").fetchall()
                )
            )
            denials = {
                row["reason"]: {
                    "count": row["denials"],
                    "last_denied_at": stamp(instant(row["last_denied_at"]))
                    if row["last_denied_at"] is not None
                    else None,
                }
                for row in c.execute("SELECT * FROM online_creation_metrics")
            }
            totals = {
                name: c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for name, table in (
                    ("matches", "online_matches"),
                    ("games", "online_games"),
                    ("accepted_moves", "online_moves"),
                    ("result_revisions", "online_results"),
                )
            }

        def file_bytes(path):
            try:
                return path.stat().st_size
            except FileNotFoundError:
                return 0

        return {
            "limits": {
                "waiting_per_account": self.settings.waiting_limit,
                "creations_per_account": self.settings.creation_limit,
                "window_seconds": self.settings.creation_window_seconds,
            },
            "denied_creations": denials,
            "online": {**totals, "by_status": counts},
            "database": {
                "allocated_bytes": pages * page_size,
                "used_bytes": (pages - free) * page_size,
                "free_bytes": free * page_size,
                "main_file_bytes": file_bytes(self.store.path),
                "wal_bytes": file_bytes(self.store.path.with_name(self.store.path.name + "-wal")),
            },
        }

    def _insert_game(self, c, game, request_id, match_id, leg, now, *, assigned_side):
        c.execute(
            "INSERT INTO online_games(id,creation_request,match_id,leg,creator_id,creator_side,attackers_id,defenders_id,ruleset,ruleset_version,time_per_move,status,version,created_at,updated_at,started_at,deadline,position) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                game.id,
                request_id,
                match_id,
                leg,
                game.creator,
                assigned_side,
                (game.creator if assigned_side == "attackers" else game.opponent)
                if assigned_side
                else None,
                (game.creator if assigned_side == "defenders" else game.opponent)
                if assigned_side
                else None,
                game.position.ruleset,
                RULESETS[game.position.ruleset].version,
                game.control.seconds,
                game.status.value,
                1 if game.started_at else 0,
                micros(now),
                micros(now),
                micros(game.started_at) if game.started_at else None,
                micros(game.deadline) if game.deadline else None,
                json.dumps(game.position.to_dict()),
            ),
        )

    def _return_game(self, c, row, game, now):
        if row["leg"] != 1:
            return
        match = c.execute("SELECT * FROM online_matches WHERE id=?", (row["match_id"],)).fetchone()
        due = (
            match["mode"] == "simultaneous"
            and row["status"] == "waiting"
            and game.status == lifecycle.Status.ACTIVE
        ) or (match["mode"] == "sequential" and game.status == lifecycle.Status.COMPLETED)
        if (
            not due
            or c.execute(
                "SELECT 1 FROM online_games WHERE match_id=? AND leg=2", (row["match_id"],)
            ).fetchone()
        ):
            return
        side = "defenders" if game.creator_side == "attackers" else "attackers"
        return_game = lifecycle.waiting(
            "online-" + uuid4().hex,
            lifecycle.Actor(game.creator),
            game.position.ruleset,
            side,
            now,
            game.control,
        )
        return_game = lifecycle.join(return_game, lifecycle.Actor(game.opponent), now).game
        self._insert_game(
            c,
            return_game,
            row["creation_request"] + "/return",
            row["match_id"],
            2,
            now,
            assigned_side=side,
        )

    def _gate_enabled(self, c, now) -> bool:
        gate = c.execute("SELECT * FROM online_timeout_gate WHERE id=1").fetchone()
        if gate is None or micros(now) < gate["updated_at"]:
            raise OnlineError(503, "Timeout control requires operator clock/state review.")
        return bool(gate["enabled"] and self.settings.timeout_policy_approved)

    def _expire(self, c, game, now) -> lifecycle.Transition:
        # The pure contract detects backwards time even when no expiry is due.
        if now < game.updated_at:
            raise OnlineError(503, "Clock moved backwards; operator review is required.")
        if game.status == lifecycle.Status.ACTIVE and now >= game.deadline:
            if not self._gate_enabled(c, now):
                raise OnlineError(409, "Deadline reached; timeout finalization is paused.")
            return lifecycle.expire(game, now)
        return lifecycle.Transition(game)

    def _persist(self, c, row, transition, now, move=None, actor=None, note=None):
        game = transition.game
        next_version = row["version"] + 1
        c.execute(
            "UPDATE online_games SET creator_side=?,attackers_id=?,defenders_id=?,status=?,version=?,updated_at=?,started_at=?,deadline=?,completed_at=?,position=?,result_revision=? WHERE id=? AND version=?",
            (
                game.creator_side if row["creator_side"] is not None or game.started_at else None,
                (game.creator if game.creator_side == "attackers" else game.opponent)
                if row["creator_side"] is not None or game.started_at
                else None,
                (game.creator if game.creator_side == "defenders" else game.opponent)
                if row["creator_side"] is not None or game.started_at
                else None,
                game.status.value,
                next_version,
                micros(game.updated_at),
                micros(game.started_at) if game.started_at else None,
                micros(game.deadline) if game.deadline else None,
                micros(game.result.completed_at) if game.result else None,
                json.dumps(game.position.to_dict()),
                game.result.revision if game.result else None,
                game.id,
                row["version"],
            ),
        )
        if c.execute("SELECT changes()").fetchone()[0] != 1:
            raise OnlineError(409, "This game changed. Reload before retrying.")
        if move is not None:
            c.execute(
                "INSERT INTO online_moves VALUES(?,?,?,?,?,?)",
                (game.id, game.position.ply, move.source, move.target, micros(now), next_version),
            )
        for result in transition.effects:
            c.execute(
                "INSERT INTO online_results VALUES(?,?,?,?,?,?,?,?)",
                (
                    game.id,
                    result.revision,
                    result.supersedes,
                    int(result.competitive_eligible),
                    result.abandoned_account_id,
                    micros(result.completed_at),
                    micros(result.revised_at),
                    json.dumps(result_payload(result)),
                ),
            )
        self._return_game(c, row, game, now)
        if actor is not None:
            c.execute(
                "INSERT INTO online_void_audit VALUES(?,?,?,?,?,?)",
                (game.id, actor, micros(now), note, row["result_revision"], next_version),
            )

    def mutate(
        self,
        credential: str,
        identifier: str,
        version: int,
        action: str,
        *,
        source=None,
        target=None,
        note=None,
    ) -> dict:
        if action not in {"join", "cancel", "move", "concede", "void"}:
            raise OnlineError(422, "Unknown online action.")
        if action == "join":
            self._admission()
        with self._write() as c:
            account = self._identity(
                c, credential, admission=action == "join", admin=action == "void"
            )
            row, game = self._load(c, identifier)
            actor = lifecycle.Actor(account["id"], allowed(account, "accounts"))
            if action in {"move", "concede"} and account["id"] not in {game.creator, game.opponent}:
                raise OnlineError(403, "Only a participant may act.")
            if action == "cancel" and account["id"] != game.creator:
                raise OnlineError(403, "Only the creator may cancel.")
            self._version(row, version)
            now = self._now()
            if action in {"move", "concede"}:
                if game.status != lifecycle.Status.ACTIVE:
                    raise OnlineError(409, "Gameplay requires an active game.")
                if account["id"] != game.account_for(game.position.turn):
                    raise OnlineError(403, "Only the player to move may act.")
                if (
                    account["email_verified_at"] is None
                    and self.settings.retain_seat_without_verification is not True
                ):
                    raise OnlineError(
                        503, "Existing-seat verification policy requires operator review."
                    )
            if now < game.updated_at:
                raise OnlineError(503, "Clock moved backwards; operator review is required.")
            if action == "void" and (
                not isinstance(note, str) or not 1 <= len(note.strip()) <= 1000
            ):
                raise OnlineError(422, "A private administrative justification is required.")
            try:
                accepted_move = None
                transition_name = action
                if action == "join":
                    creator = c.execute(
                        "SELECT * FROM accounts WHERE id=?", (game.creator,)
                    ).fetchone()
                    if not creator or not online_eligible(dict(creator)):
                        raise OnlineError(403, "Both participants must be eligible at activation.")
                    if row["leg"] != 1:
                        raise OnlineError(409, "Return games use the admitted match participants.")
                    match = c.execute(
                        "SELECT * FROM online_matches WHERE id=?", (row["match_id"],)
                    ).fetchone()
                    chosen_side = match["side_selection"]
                    if chosen_side == "random":
                        chosen_side = secrets.choice(("attackers", "defenders"))
                    transition = lifecycle.join(replace(game, creator_side=chosen_side), actor, now)
                    c.execute(
                        "UPDATE online_matches SET opponent_id=?,first_creator_side=?,activated_at=? WHERE id=?",
                        (actor.account_id, chosen_side, micros(now), row["match_id"]),
                    )
                elif action == "cancel":
                    transition = lifecycle.cancel(game, actor, now)
                elif action == "void":
                    transition = lifecycle.void(game, actor, now, note.strip())
                else:
                    transition = self._expire(c, game, now)
                    if transition.effects:
                        transition_name = "timeout"
                    elif action == "concede":
                        transition = lifecycle.concede(game, actor, now)
                    else:
                        if type(source) is not int or type(target) is not int:
                            raise OnlineError(422, "Integer move coordinates are required.")
                        accepted_move = Move(source, target)
                        after = apply_move(game.position, accepted_move)
                        transition = lifecycle.accept_move(game, actor, after, now)
            except lifecycle.LifecycleError:
                raise OnlineError(409, "Action is not valid for the current game state.") from None
            except (ValueError, IndexError) as error:
                if isinstance(error, OnlineError):
                    raise
                raise OnlineError(422, "The move is not legal.") from None
            if transition.game is game:
                transition_name = "none"
            else:
                self._persist(
                    c,
                    row,
                    transition,
                    now,
                    accepted_move,
                    account["id"] if action == "void" else None,
                    note.strip() if action == "void" else None,
                )
            response = self._projection(c, *self._load(c, identifier))
        return {
            "game": response,
            "transition": transition_name,
            "action_accepted": transition_name == action,
        }

    def _projection(self, c, row, game) -> dict:
        match = c.execute("SELECT * FROM online_matches WHERE id=?", (row["match_id"],)).fetchone()
        participants = {}
        for side in ("attackers", "defenders"):
            identifier = row[side + "_id"]
            account = (
                c.execute(
                    "SELECT username,display_name FROM accounts WHERE id=?", (identifier,)
                ).fetchone()
                if identifier
                else None
            )
            participants[side] = (
                {
                    "account_id": identifier,
                    "name": str(account["display_name"] or account["username"])[:80],
                }
                if account
                else None
            )
        result = None
        if game.result:
            result = result_payload(game.result)
            for private in ("correction_note", "corrected_by"):
                result.pop(private)
            result.update(rated_eligible=False, rated_reason="policy_not_approved")
        return {
            "id": game.id,
            "category": "online",
            "creator_id": game.creator,
            "creator_side": row["creator_side"],
            "match": {
                "id": match["id"],
                "mode": match["mode"],
                "side_selection": match["side_selection"],
                "first_creator_side": match["first_creator_side"],
                "leg": row["leg"],
                "games": [
                    dict(r)
                    for r in c.execute(
                        "SELECT id,leg,status,version FROM online_games WHERE match_id=? ORDER BY leg",
                        (match["id"],),
                    )
                ],
            },
            "status": game.status.value,
            "version": row["version"],
            "ruleset": row["ruleset"],
            "ruleset_version": row["ruleset_version"],
            "time_per_move": game.control.seconds,
            "rated_intent": False,
            "created_at": stamp(instant(row["created_at"])),
            "updated_at": stamp(game.updated_at),
            "started_at": stamp(game.started_at),
            "deadline": stamp(game.deadline),
            "completed_at": stamp(game.result.completed_at) if game.result else None,
            "participants": participants,
            "position": safe_position(game.position),
            "moves": [
                {
                    "ply": r["ply"],
                    "source": r["source"],
                    "target": r["target"],
                    "accepted_at": stamp(instant(r["accepted_at"])),
                }
                for r in c.execute(
                    "SELECT * FROM online_moves WHERE game_id=? ORDER BY ply", (game.id,)
                )
            ],
            "result": result,
        }

    @staticmethod
    def _participant(c, account_id, identifier):
        if not c.execute(
            """SELECT 1 FROM online_games g JOIN online_matches m ON m.id=g.match_id
               WHERE g.id=? AND (g.creator_id=? OR g.attackers_id=? OR g.defenders_id=? OR m.opponent_id=?)""",
            (identifier, account_id, account_id, account_id, account_id),
        ).fetchone():
            raise OnlineError(404, "Online game not found.")

    def read(self, credential: str, identifier: str, *, participant_only=False) -> dict:
        with self.store.connection() as c:
            c.execute("BEGIN")
            account = self._identity(c, credential)
            if participant_only:
                self._participant(c, account["id"], identifier)
            return self._projection(c, *self._load(c, identifier))

    def availability(self, credential: str) -> dict:
        """Own admission eligibility/configuration, independent of existing seats."""
        with self.store.connection() as c:
            c.execute("BEGIN")
            account = self._identity(c, credential)
            return {
                "enabled": bool(self.settings.enabled and self.settings.creation_policy_approved),
                "eligible": online_eligible(account),
            }

    def listing(self, credential: str, limit=25, offset=0, *, mine=False) -> dict:
        if (
            type(limit) is not int
            or not 1 <= limit <= 100
            or type(offset) is not int
            or not 0 <= offset <= 100000
        ):
            raise OnlineError(422, "Invalid pagination.")
        with self.store.connection() as c:
            c.execute("BEGIN")
            account = self._identity(c, credential)
            condition = "WHERE creator_id=? OR attackers_id=? OR defenders_id=?" if mine else ""
            params = (account["id"],) * 3 if mine else ()
            rows = c.execute(
                f"SELECT id FROM online_games {condition} ORDER BY created_at DESC,id LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ).fetchall()
            return {
                "games": [self._projection(c, *self._load(c, row[0])) for row in rows],
                "limit": limit,
                "offset": offset,
            }

    def replay(self, credential: str, identifier: str, *, participant_only=False) -> dict:
        with self.store.connection() as c:
            c.execute("BEGIN")
            account = self._identity(c, credential)
            if participant_only:
                self._participant(c, account["id"], identifier)
            return self._replay(c, identifier)

    def _replay(self, c, identifier):
        row, game = self._load(c, identifier)
        state = new_game(row["ruleset"])
        states = [safe_position(state)]
        try:
            for move in c.execute(
                "SELECT source,target FROM online_moves WHERE game_id=? ORDER BY ply",
                (identifier,),
            ):
                state = apply_move(state, Move(*move))
                states.append(safe_position(state))
        except (ValueError, IndexError):
            raise OnlineError(409, "Stored online history requires operator review.") from None
        if state != game.position:
            raise OnlineError(409, "Stored online history requires operator review.")
        return {"game": self._projection(c, row, game), "validated": True, "states": states}

    def statistics(self, credential: str) -> dict:
        with self.store.connection() as c:
            c.execute("BEGIN")
            account = self._identity(c, credential)
            return self._statistics(c, account["id"])

    @staticmethod
    def _statistics(c, account_id):
        totals = c.execute(
            """WITH effective AS (
              SELECT json_extract(r.payload,'$.winner') AS winner,
                CASE WHEN g.attackers_id=? THEN 'attackers' ELSE 'defenders' END AS side,
                r.abandoned_account_id
              FROM online_games g JOIN online_results r ON r.game_id=g.id AND r.revision=g.result_revision
              WHERE g.status='completed' AND r.competitive_eligible=1 AND (g.attackers_id=? OR g.defenders_id=?)
            ) SELECT count(*) AS played,
              coalesce(sum(winner=side),0) AS wins,
              coalesce(sum(winner<>'draw' AND winner<>side),0) AS losses,
              coalesce(sum(winner='draw'),0) AS draws,
              coalesce(sum(abandoned_account_id=?),0) AS abandoned_games FROM effective""",
            (account_id, account_id, account_id, account_id),
        ).fetchone()
        return {"category": "online", **dict(totals)}

    def gate(self, credential: str) -> dict:
        with self.store.connection() as c:
            c.execute("BEGIN")
            self._identity(c, credential, admin=True)
            gate = dict(c.execute("SELECT * FROM online_timeout_gate WHERE id=1").fetchone())
            gate.update(
                effective_enabled=bool(gate["enabled"] and self.settings.timeout_policy_approved),
                policy_approved=self.settings.timeout_policy_approved,
            )
            gate["updated_at"] = stamp(instant(gate["updated_at"]))
            gate["audit"] = [
                {**dict(r), "at": stamp(instant(r["at"]))}
                for r in c.execute("SELECT * FROM online_gate_audit ORDER BY version DESC LIMIT 50")
            ]
            return gate

    def set_gate(self, credential: str, version: int, enabled: bool, note: str) -> dict:
        if (
            type(enabled) is not bool
            or not isinstance(note, str)
            or not 1 <= len(note.strip()) <= 1000
        ):
            raise OnlineError(
                422, "A gate state and private operational justification are required."
            )
        with self._write() as c:
            account = self._identity(c, credential, admin=True)
            if enabled and not self.settings.timeout_policy_approved:
                raise OnlineError(
                    503, "Production timeout policy approval is required before resuming."
                )
            gate = c.execute("SELECT * FROM online_timeout_gate WHERE id=1").fetchone()
            self._version(gate, version)
            now = self._now()
            if micros(now) < gate["updated_at"]:
                raise OnlineError(503, "Clock moved backwards; operator review is required.")
            c.execute(
                "UPDATE online_timeout_gate SET enabled=?,version=version+1,updated_at=? WHERE id=1",
                (int(enabled), micros(now)),
            )
            c.execute(
                "INSERT INTO online_gate_audit VALUES(?,?,?,?,?,?)",
                (version + 1, int(enabled), micros(now), account["id"], "admin", note.strip()),
            )
        return self.gate(credential)

    def sweep(self) -> dict:
        now = micros(self.clock())  # Candidate selection only, rechecked after lock below.
        with self.store.connection() as c:
            rows = c.execute(
                "SELECT id FROM online_games WHERE status='active' AND deadline<=? ORDER BY deadline,id LIMIT ?",
                (now, self.settings.sweep_batch),
            ).fetchall()
        counts = {"examined": len(rows), "finalized": 0, "blocked": 0}
        for row in rows:
            try:
                with self._write() as c:
                    current, game = self._load(c, row[0])
                    accepted_at = self._now()
                    transition = self._expire(c, game, accepted_at)
                    if transition.effects:
                        self._persist(c, current, transition, accepted_at)
                        counts["finalized"] += 1
            except OnlineError:
                counts["blocked"] += 1
        return counts

    def start(self):
        self.stop.clear()

        def run():
            while not self.stop.wait(self.settings.sweep_seconds):
                try:
                    self.sweep()
                except Exception:
                    # No account IDs, private audit notes or exception payloads in logs.
                    pass

        self.thread = threading.Thread(target=run, name="online-expiry", daemon=True)
        self.thread.start()

    def shutdown(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=17)
