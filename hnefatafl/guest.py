"""Device-only guest sessions. No HTTP, database, account or online adapter.

Only a bounded accepted move sequence is imported. Every position/result is
reconstructed by the same engine used by account and online games.
"""

from __future__ import annotations

import json
from typing import Any

from .ai import choose_move
from .engine import RULESETS, Move, State, apply_move, legal_moves, new_game

MAX_BYTES = 262144
MAX_MOVES = 4096
FIELDS = {"schema", "engine_version", "ruleset", "ruleset_version", "human_side", "moves", "cursor"}


class GuestError(ValueError):
    pass


def validate_record(value: Any, engine_version: str) -> tuple[dict, State]:
    if not isinstance(value, dict) or set(value) != FIELDS:
        raise GuestError("Invalid guest save format.")
    try:
        size = len(json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode())
    except (TypeError, ValueError, OverflowError):
        raise GuestError("Invalid guest save format.") from None
    if size > MAX_BYTES:
        raise GuestError("Guest save exceeds the storage limit.")
    if type(value["schema"]) is not int or value["schema"] != 1:
        raise GuestError("Unsupported guest save version.")
    if value["engine_version"] != engine_version:
        raise GuestError("This guest save requires a different engine version.")
    rule = value["ruleset"]
    if not isinstance(rule, str) or rule not in RULESETS:
        raise GuestError("Unsupported guest ruleset.")
    if value["ruleset_version"] != RULESETS[rule].version:
        raise GuestError("Unsupported guest rules version.")
    if value["human_side"] not in ("attackers", "defenders"):
        raise GuestError("Invalid guest side.")
    moves, cursor = value["moves"], value["cursor"]
    if not isinstance(moves, list) or len(moves) > MAX_MOVES:
        raise GuestError("Guest save exceeds the move limit.")
    if type(cursor) is not int or not 0 <= cursor <= len(moves):
        raise GuestError("Invalid guest replay cursor.")
    state = new_game(rule)
    accepted = []
    for move in moves:
        if not isinstance(move, dict) or set(move) != {"source", "target"}:
            raise GuestError("Invalid guest move history.")
        if any(type(move[k]) is not int or not 0 <= move[k] < state.size**2 for k in move):
            raise GuestError("Invalid guest move coordinates.")
        try:
            state = apply_move(state, Move(move["source"], move["target"]))
        except ValueError:
            raise GuestError("The saved guest move history is not legal.") from None
        accepted.append(dict(move))
    return {**value, "moves": accepted}, state


class GuestSession:
    def __init__(self, engine_version: str):
        self.engine_version = engine_version
        self.record: dict | None = None
        self.state: State | None = None

    def restore(self, record: Any) -> dict:
        # Publish only after the entire line validates; failure keeps the old game.
        candidate, state = validate_record(record, self.engine_version)
        self.record, self.state = candidate, state
        return self.snapshot()

    def start(self, rule: str, human_side: str) -> dict:
        if not isinstance(rule, str) or rule not in RULESETS:
            raise GuestError("Unsupported guest ruleset.")
        return self.restore(
            {
                "schema": 1,
                "engine_version": self.engine_version,
                "ruleset": rule,
                "ruleset_version": RULESETS[rule].version,
                "human_side": human_side,
                "moves": [],
                "cursor": 0,
            }
        )

    def snapshot(self) -> dict:
        if self.record is None or self.state is None:
            raise GuestError("No guest game is loaded.")
        view = self.state
        if self.record["cursor"] != len(self.record["moves"]):
            view = new_game(self.record["ruleset"])
            for move in self.record["moves"][: self.record["cursor"]]:
                view = apply_move(view, Move(**move))
        return {
            "record": {**self.record, "moves": [dict(m) for m in self.record["moves"]]},
            "position": {k: v for k, v in view.to_dict().items() if k != "history"},
            "size": view.size,
            "legal_moves": [m.to_dict() for m in legal_moves(view)],
            "live_winner": self.state.winner,
            "live_reason": self.state.reason,
        }

    def play(self, *, computer: bool, source=None, target=None) -> dict:
        if self.record is None or self.state is None:
            raise GuestError("No guest game is loaded.")
        if self.record["cursor"] != len(self.record["moves"]):
            raise GuestError("Return to the current guest position before playing.")
        if self.state.winner:
            raise GuestError("This guest game is finished.")
        if computer == (self.state.turn == self.record["human_side"]):
            raise GuestError("It is the other player's turn.")
        if computer:
            move = choose_move(self.state, {"algorithm": "baseline", "seed": 7, "time_limit": 0.1})
        else:
            if any(type(v) is not int or not 0 <= v < self.state.size**2 for v in (source, target)):
                raise GuestError("Invalid guest move coordinates.")
            move = Move(source, target)
        after = apply_move(self.state, move)
        self.state = after
        self.record = {
            **self.record,
            "moves": [*self.record["moves"], move.to_dict()],
            "cursor": len(self.record["moves"]) + 1,
        }
        # The storage cap is not a game rule: an in-memory long game can continue.
        return self.snapshot()

    def seek(self, cursor: int) -> dict:
        if (
            self.record is None
            or type(cursor) is not int
            or not 0 <= cursor <= len(self.record["moves"])
        ):
            raise GuestError("Invalid guest replay cursor.")
        self.record = {**self.record, "cursor": cursor}
        return self.snapshot()
