"""Signed-out public online discovery; read-only, no schema, cached analysis only.

The additive ``/api/public/online/*`` namespace exposes only online games under
the approved public visibility policy. It never reads single-player or local
records, never mutates a game, never exposes live assistance and never generates
analysis. The whole namespace is gated by ``OnlineSettings.public_discovery_enabled``
and bounded per connecting IP.
"""

from __future__ import annotations

import math
import sqlite3
import threading
import time
from datetime import datetime
from typing import Literal

from fastapi import HTTPException, Path, Query, Request, Response

from .account_policy import attributed_name, case_fold, public_identity
from .auth import PUBLIC_CACHE
from .engine import Move, RULESETS, apply_move, new_game
from .online_core import OnlineCore, OnlineError, instant, safe_position, stamp
from .personal import context_key, page

Rule = Literal["brandubh", "tablut", "copenhagen"]
Status = Literal["waiting", "active", "completed", "cancelled", "voided"]
Outcome = Literal["attackers", "defenders", "draw"]

# Completed reads are immutable between rare corrections, so a short shared
# window is safe; the security boundary grants ``PUBLIC_CACHE`` only to this
# completed read and forces ``no-store`` on live reads, the index and errors.

# Only public attribution, the game's own start rating, the effective winner
# delta and the current pool rating. Never email, account/session ids, private
# profile fields or unpublished names. ``portrait_id`` is a reserved contract
# key: portrait storage ships with schema 17 (B4) and is never client supplied.
# ``rated`` and ``winner_delta`` are published only when the effective result
# revision applied a rating event; a completed rated match with an ineligible or
# unverified participant carries no applied event and is published unrated.
_BASE = """
SELECT g.id, g.updated_at AS order_at, g.created_at, g.updated_at, g.completed_at,
  g.ruleset, g.ruleset_version, g.status, g.leg, g.attackers_id, g.defenders_id,
  m.mode, m.rated, r.revision, r.competitive_eligible,
  json_extract(r.payload,'$.winner') AS winner,
  json_extract(r.payload,'$.termination_reason') AS reason,
  public_account_name(a.username,a.display_name,a.profile_private_fields) AS attacker_name,
  public_account_name(d.username,d.display_name,d.profile_private_fields) AS defender_name,
  a.username AS attacker_username, d.username AS defender_username,
  (SELECT s.rating FROM online_game_start_ratings s
     WHERE s.game_id=g.id AND s.account_id=g.attackers_id) AS attacker_start,
  (SELECT s.rating FROM online_game_start_ratings s
     WHERE s.game_id=g.id AND s.account_id=g.defenders_id) AS defender_start,
  (SELECT pr.rating FROM player_ratings pr
     WHERE pr.account_id=g.attackers_id AND pr.pool=g.ruleset) AS attacker_current,
  (SELECT pr.rating FROM player_ratings pr
     WHERE pr.account_id=g.defenders_id AND pr.pool=g.ruleset) AS defender_current,
  (SELECT ra.delta FROM rating_applications ra
     WHERE ra.game_id=g.id AND ra.account_id = CASE json_extract(r.payload,'$.winner')
       WHEN 'attackers' THEN g.attackers_id WHEN 'defenders' THEN g.defenders_id END) AS winner_delta,
  EXISTS(SELECT 1 FROM rating_events e
     WHERE e.game_id=g.id AND e.state='applied' AND e.effective_revision=r.revision) AS rating_applied
FROM online_games g
JOIN online_matches m ON m.id=g.match_id
LEFT JOIN online_results r ON r.game_id=g.id AND r.revision=g.result_revision
LEFT JOIN accounts a ON a.id=g.attackers_id
LEFT JOIN accounts d ON d.id=g.defenders_id
"""

# Public analysis projection. A stored report is internal: each evaluation entry
# carries the effective search configuration (including model filesystem paths
# and seeds), reserved identity runes and search diagnostics. Only the fields
# below are copied into a signed-out response, and each copy is both allowlisted
# and type-checked, so neither a future stored field nor a malformed value under
# an approved key can leak by default. The stored report itself is never
# modified, and no public request recomputes or writes analysis.
#
# ``_OMIT`` marks "do not publish this key at all", which is distinct from a
# legitimately null public value. A stored value of the wrong type is dropped
# rather than copied, so a nested object, array or exception string stored under
# an approved scalar key never reaches the response.
_OMIT = object()
_MAX_TEXT = 256
_MAX_TIMESTAMP = 64
_MAX_COUNT = 1_000_000
_SIDES = ("attackers", "defenders")
_MOVE_FIELDS = ("source", "target")


def _text(value):
    """Bounded string; ``None`` stays an explicit null."""
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > _MAX_TEXT:
        return _OMIT
    return value


def _count(value):
    """Bounded non-negative integer; ``bool`` is rejected as an ``int`` subclass."""
    if isinstance(value, bool) or not isinstance(value, int):
        return _OMIT
    return value if 0 <= value <= _MAX_COUNT else _OMIT


def _side(value):
    """One of the two known sides to move."""
    return value if isinstance(value, str) and value in _SIDES else _OMIT


def _number(value, *, low=None, high=None):
    """Finite number in an optional range; ``None`` stays null and ``bool`` is
    rejected as an ``int`` subclass."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return _OMIT
    try:
        finite = math.isfinite(value)
    except OverflowError:  # An integer too large to convert to a float.
        return _OMIT
    if not finite:
        return _OMIT
    if (low is not None and value < low) or (high is not None and value > high):
        return _OMIT
    return value


def _probability(value):
    """A win chance is a probability, so an out-of-range value is not published."""
    return _number(value, low=0.0, high=1.0)


def _flag(value):
    """A real boolean or an explicit null; an integer or string is not a flag."""
    if value is None:
        return None
    return value if isinstance(value, bool) else _OMIT


def _timestamp(value):
    """ISO-8601 timestamp string; ``None`` stays an explicit null."""
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > _MAX_TIMESTAMP:
        return _OMIT
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return _OMIT
    return value


_ANALYSIS_CHECKS = {
    "cursor": _count,
    "ply": _count,
    "side_to_move": _side,
    "created_at": _timestamp,
}
_EVALUATION_CHECKS = {
    "agent_id": _text,
    "role": _text,
    "name": _text,
    "profile_name": _text,
    "model_name": _text,
    "source": _text,
    "value": _number,
    "win_probability": _probability,
    "estimated": _flag,
    "calibrated": _flag,
    "unavailable_reason": _text,
}


def _copy(source: dict, checks: dict) -> dict:
    """Copy the approved keys of ``source`` whose values pass their type check."""
    projected = {}
    for key, check in checks.items():
        if key not in source:
            continue
        value = check(source[key])
        if value is not _OMIT:
            projected[key] = value
    return projected


def _copy_move(projected: dict, source: dict, key: str, *, size: int) -> None:
    """Copy a move-valued key: an explicit null stays null, a valid move is
    projected onto its two coordinates and a malformed move is omitted."""
    if key not in source:
        return
    raw = source[key]
    if raw is None:
        projected[key] = None
        return
    coordinates = public_move(raw, size=size)
    if coordinates is not None:
        projected[key] = coordinates


def public_move(value, *, size: int) -> dict | None:
    """Project a stored move onto its two validated public coordinates.

    ``size`` is the reported game's board edge. A coordinate must be a bounded
    integer square index for that board, so a partial, negative, boolean or
    out-of-range move is dropped instead of copied.
    """
    if not isinstance(value, dict):
        return None
    move = {}
    for key in _MOVE_FIELDS:
        coordinate = value.get(key)
        if isinstance(coordinate, bool) or not isinstance(coordinate, int):
            return None
        if not 0 <= coordinate < size * size:
            return None
        move[key] = coordinate
    return move


def public_evaluation(entry, *, size: int) -> dict | None:
    """Project one stored evaluation entry; unknown or malformed keys are dropped."""
    if not isinstance(entry, dict):
        return None
    projected = _copy(entry, _EVALUATION_CHECKS)
    _copy_move(projected, entry, "best_move", size=size)
    return projected


def public_analysis(report, *, size: int) -> dict | None:
    """Project a stored analysis report onto the typed public allowlist.

    ``size`` is the reported game's board edge, used to bound move coordinates.
    """
    if not isinstance(report, dict):
        return None
    projected = _copy(report, _ANALYSIS_CHECKS)
    evaluations = report.get("evaluations")
    projected["evaluations"] = [
        entry
        for entry in (
            public_evaluation(item, size=size)
            for item in (evaluations if isinstance(evaluations, list) else [])
        )
        if entry is not None
    ]
    hint = report.get("hint")
    projected["hint"] = None
    if isinstance(hint, dict):
        hint_fields = _copy(hint, {"agent_id": _text})
        _copy_move(hint_fields, hint, "move", size=size)
        projected["hint"] = hint_fields
    return projected


class PublicRateLimiter:
    """Per-IP sliding window held in process.

    The deployment runs a single API worker, so an in-memory bound needs no new
    schema and no durable write on an anonymous read path.
    """

    def __init__(self, limit: int, window_seconds: int, *, max_keys: int = 4096):
        self.limit, self.window, self.max_keys = limit, window_seconds, max_keys
        self._lock = threading.Lock()
        self._seen: dict[str, list[float]] = {}

    def admit(self, address: str) -> None:
        now = time.monotonic()
        cutoff = now - self.window
        with self._lock:
            recent = [moment for moment in self._seen.get(address, ()) if moment > cutoff]
            if len(recent) >= self.limit:
                raise OnlineError(429, "Too many public requests. Try again later.")
            recent.append(now)
            self._seen[address] = recent
            if len(self._seen) > self.max_keys:
                # Keep the most recently active addresses; stale keys expire anyway.
                ordered = sorted(self._seen.items(), key=lambda item: item[1][-1])
                self._seen = dict(ordered[-self.max_keys :])


class PublicOnline:
    def __init__(self, online: OnlineCore, *, stored_analysis):
        self.online, self.store = online, online.store
        self.stored_analysis = stored_analysis
        settings = online.settings
        self.limiter = PublicRateLimiter(
            settings.public_rate_limit, settings.public_rate_window_seconds
        )

    def _admission(self, request: Request) -> None:
        # Disabled means the namespace does not resolve; a consistent 404 also
        # avoids confirming any record through this path.
        if not self.online.settings.public_discovery_enabled:
            raise OnlineError(404, "Not found.")
        self.limiter.admit(request.client.host if request.client else "unknown")

    def listing(
        self,
        request: Request,
        *,
        limit: int,
        cursor: str | None,
        ruleset: str | None,
        status: str | None,
        outcome: str | None,
        player: str | None,
    ) -> dict:
        self._admission(request)
        filters = {
            "ruleset": ruleset,
            "status": status,
            "outcome": outcome,
            "player": player,
        }
        conditions, values = [], []
        for column, value in (("ruleset", ruleset), ("status", status), ("winner", outcome)):
            if value is not None:
                conditions.append(column + "=?")
                values.append(value)
        with self.store.connection() as c:
            c.execute("BEGIN")
            if player is not None:
                # Literal substring matching on the same public label returned
                # below; SQL wildcards are not a capability. ``case_fold`` gives
                # the documented case-insensitive contract full Unicode folding
                # instead of SQLite's ASCII-only ``lower()``.
                c.create_function("attributed_name", 2, attributed_name, deterministic=True)
                c.create_function("case_fold", 1, case_fold, deterministic=True)
                conditions.append(
                    "("
                    + " OR ".join(
                        f"instr(case_fold(coalesce({field},'')),case_fold(?))>0"
                        for field in (
                            "attributed_name(attacker_name,attacker_username)",
                            "attributed_name(defender_name,defender_username)",
                            "attacker_username",
                            "defender_username",
                        )
                    )
                    + ")"
                )
                values.extend((player,) * 4)
            rows, paging = page(
                c,
                _BASE,
                (),
                conditions,
                values,
                limit=limit,
                cursor=cursor,
                context=context_key(None, "public-online", filters),
                online=True,
                count=False,
            )
            return {
                "items": [self._item(row) for row in rows],
                "next_cursor": paging["next_cursor"],
                "limit": paging["limit"],
            }

    def detail(self, request: Request, identifier: str) -> dict:
        self._admission(request)
        with self.store.connection() as c:
            c.execute("BEGIN")
            row, game = self.online._load(c, identifier)
            return self._summary(c, row, game)

    def replay(self, request: Request, identifier: str) -> dict:
        self._admission(request)
        with self.store.connection() as c:
            c.execute("BEGIN")
            row, game = self.online._load(c, identifier)
            # A replay is completed-only; a live game has no public replay yet.
            if row["status"] != "completed":
                raise OnlineError(404, "Online replay not found.")
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
            return {
                "game": self._summary(c, row, game),
                "validated": True,
                "states": states,
                "moves": self._moves(c, row),
                # Only already-stored analysis; the reader never computes or
                # writes, and the projection is the sole public DTO.
                "analysis": public_analysis(
                    self.stored_analysis(game.id, state),
                    size=RULESETS[row["ruleset"]].size,
                ),
            }

    @staticmethod
    def _item(row) -> dict:
        def participant(identifier, name, username, start, current):
            if identifier is None:
                return None
            return {
                "display_name": name or "",
                "username": username,
                "portrait_id": None,
                "start_rating": start,
                "current_rating": current,
            }

        result = None
        if row["revision"] is not None:
            rated = (
                bool(row["rated"])
                and bool(row["competitive_eligible"])
                and bool(row["rating_applied"])
            )
            result = {
                "winner": row["winner"],
                "termination_reason": row["reason"],
                "rated": rated,
                "winner_delta": row["winner_delta"] if rated else None,
            }
        recorded_at = row["completed_at"] if row["completed_at"] is not None else row["updated_at"]
        return {
            "id": row["id"],
            "category": "online",
            "ruleset": row["ruleset"],
            "ruleset_version": row["ruleset_version"],
            "status": row["status"],
            "mode": row["mode"],
            "leg": row["leg"],
            "rated": bool(row["rated"]),
            "created_at": stamp(instant(row["created_at"])),
            "recorded_at": stamp(instant(recorded_at)),
            "participants": {
                "attackers": participant(
                    row["attackers_id"],
                    row["attacker_name"],
                    row["attacker_username"],
                    row["attacker_start"],
                    row["attacker_current"],
                ),
                "defenders": participant(
                    row["defenders_id"],
                    row["defender_name"],
                    row["defender_username"],
                    row["defender_start"],
                    row["defender_current"],
                ),
            },
            "result": result,
        }

    def _summary(self, c, row, game) -> dict:
        match = c.execute(
            "SELECT mode,rated FROM online_matches WHERE id=?", (row["match_id"],)
        ).fetchone()
        participants = {
            side: (
                self._participant(c, row[side + "_id"], row["ruleset"], game.id)
                if row[side + "_id"]
                else None
            )
            for side in ("attackers", "defenders")
        }
        completed = row["status"] == "completed"
        move_rows = (
            c.execute(
                "SELECT ply,source,target,accepted_at FROM online_moves WHERE game_id=? ORDER BY ply",
                (game.id,),
            ).fetchall()
            if completed
            else []
        )
        result = None
        if game.result is not None:
            rated = (
                bool(match["rated"])
                and bool(game.result.competitive_eligible)
                and self._rating_applied(c, game.id, game.result.revision)
            )
            result = {
                "winner": game.result.winner,
                "termination_reason": game.result.termination_reason,
                "rated": rated,
                "winner_delta": self._winner_delta(c, game.id, game.result.winner, rated),
            }
        start = new_game(row["ruleset"])
        terminal_king_square = None
        if (
            game.result is not None
            and game.result.winner == "attackers"
            and game.result.termination_reason == "king_captured"
            and "K" not in game.position.board
        ):
            # Display-only terminal overlay, derived from recorded moves.
            terminal_king_square = OnlineCore._terminal_king_square(row["ruleset"], move_rows)
        return {
            "id": game.id,
            "category": "online",
            "ruleset": row["ruleset"],
            "ruleset_version": row["ruleset_version"],
            "status": row["status"],
            "mode": match["mode"],
            "leg": row["leg"],
            "rated": bool(match["rated"]),
            "time_per_move": game.control.seconds,
            "time_control": game.control.mode,
            "increment_seconds": game.control.increment_seconds,
            "created_at": stamp(instant(row["created_at"])),
            "updated_at": stamp(game.updated_at),
            "started_at": stamp(game.started_at),
            "deadline": stamp(game.deadline),
            "completed_at": stamp(game.result.completed_at) if game.result else None,
            "participants": participants,
            "position": safe_position(game.position),
            "terminal_king_square": terminal_king_square,
            "captured": {
                "attackers": start.board.count("A") - game.position.board.count("A"),
                "defenders": sum(
                    start.board.count(p) - game.position.board.count(p) for p in ("D", "K")
                ),
            },
            # Live games never expose the move list; the current position is the
            # read-only spectator view.
            "moves": self._move_payload(row["ruleset"], move_rows) if completed else None,
            "result": result,
        }

    def _participant(self, c, account_id, ruleset, game_id) -> dict:
        row = c.execute(
            "SELECT id,username,display_name,profile_private_fields FROM accounts WHERE id=?",
            (account_id,),
        ).fetchone()
        identity = public_identity(row)
        started = c.execute(
            "SELECT rating FROM online_game_start_ratings WHERE game_id=? AND account_id=?",
            (game_id, account_id),
        ).fetchone()
        current = c.execute(
            "SELECT rating FROM player_ratings WHERE account_id=? AND pool=?",
            (account_id, ruleset),
        ).fetchone()
        return {
            "display_name": identity["name"],
            "username": identity.get("username"),
            # Reserved until portrait storage ships (schema 17, B4).
            "portrait_id": None,
            "start_rating": started["rating"] if started else None,
            "current_rating": current["rating"] if current else None,
        }

    @staticmethod
    def _rating_applied(c, game_id, revision) -> bool:
        """True only when the effective result revision applied a rating event."""
        row = c.execute(
            "SELECT state,effective_revision FROM rating_events WHERE game_id=?", (game_id,)
        ).fetchone()
        return bool(
            row is not None and row["state"] == "applied" and row["effective_revision"] == revision
        )

    @staticmethod
    def _winner_delta(c, game_id, winner, rated):
        if not rated or winner not in ("attackers", "defenders"):
            return None
        side = "attackers_id" if winner == "attackers" else "defenders_id"
        row = c.execute(
            "SELECT ra.delta FROM rating_applications ra JOIN online_games g ON g.id=ra.game_id "
            f"WHERE ra.game_id=? AND ra.account_id=g.{side}",
            (game_id,),
        ).fetchone()
        return row["delta"] if row else None

    def _moves(self, c, row) -> list[dict]:
        move_rows = c.execute(
            "SELECT ply,source,target,accepted_at FROM online_moves WHERE game_id=? ORDER BY ply",
            (row["id"],),
        ).fetchall()
        return self._move_payload(row["ruleset"], move_rows)

    @staticmethod
    def _move_payload(ruleset, move_rows) -> list[dict]:
        first = RULESETS[ruleset].first_player
        second = "defenders" if first == "attackers" else "attackers"
        return [
            {
                "ply": row["ply"],
                "source": row["source"],
                "target": row["target"],
                "accepted_at": stamp(instant(row["accepted_at"])),
                "side": first if index % 2 == 0 else second,
            }
            for index, row in enumerate(move_rows)
        ]


def attach_public(app, online, *, stored_analysis):
    service = PublicOnline(online, stored_analysis=stored_analysis)
    app.state.public_online = service

    def execute(action):
        try:
            return action()
        except OnlineError as error:
            raise HTTPException(error.status, str(error)) from None
        except (sqlite3.Error, ValueError, KeyError, IndexError, TypeError):
            raise HTTPException(503, "Public online data is unavailable. Retry shortly.") from None

    @app.get("/api/public/online/games")
    def listing(
        request: Request,
        limit: int = Query(default=25, ge=1, le=100),
        cursor: str | None = Query(default=None, min_length=1, max_length=512),
        ruleset: Rule | None = None,
        status: Status | None = None,
        outcome: Outcome | None = None,
        # 80 display-name + 64 username code points + " (@" and ")".
        player: str | None = Query(default=None, min_length=1, max_length=148),
    ):
        return execute(
            lambda: service.listing(
                request,
                limit=limit,
                cursor=cursor,
                ruleset=ruleset,
                status=status,
                outcome=outcome,
                player=player,
            )
        )

    @app.get("/api/public/online/games/{game_id}")
    def detail(
        request: Request,
        response: Response,
        game_id: str = Path(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$"),
    ):
        payload = execute(lambda: service.detail(request, game_id))
        if payload["status"] == "completed":
            response.headers["Cache-Control"] = PUBLIC_CACHE
        return payload

    @app.get("/api/public/online/games/{game_id}/replay")
    def replay(
        request: Request,
        response: Response,
        game_id: str = Path(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$"),
    ):
        payload = execute(lambda: service.replay(request, game_id))
        response.headers["Cache-Control"] = PUBLIC_CACHE
        return payload

    return service
