"""Versioned, model-independent legal tournament openings.

SHA-256 choices over sorted legal moves avoid process hashes and RNG-version
dependencies. Even 2/4/6-ply openings keep the ruleset's original side to move.
The policy preserves material, avoids repeated positions and rejects immediate
winning continuations. It samples variety, without claiming balanced positions.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass

from .engine import Move, State, apply_move, legal_moves, new_game
from .symmetry import BoardKey, canonical_board

OPENING_VERSION = 1
MAX_ATTEMPTS = 32


def opening_seed(seed: int, ruleset: str, pair: tuple[int, int], comparison: int) -> int:
    payload = json.dumps(
        ["hnefatafl-opening", OPENING_VERSION, seed, ruleset, list(pair), comparison],
        separators=(",", ":"),
    ).encode("ascii")
    # At most 53 bits, exactly representable in JSON/JavaScript as well as Python.
    return int.from_bytes(hashlib.sha256(payload).digest()[:6], "big")


@dataclass(frozen=True)
class Opening:
    seed: int
    moves: tuple[Move, ...]
    state: State
    unique: bool


def generate_opening(
    ruleset: str,
    seed: int,
    seen: set[BoardKey],
    should_stop: Callable[[], bool] | None = None,
) -> Opening:
    """Bounded quiet playouts; retain full engine states including repetition.

    Deduplicate final boards up to D4 per battlefield. If the bounded search
    exhausts unique candidates, retain a valid duplicate and mark it explicitly.
    Never silently fall back to an unsuitable or terminal position.
    """
    fallback: Opening | None = None

    def check() -> None:
        if should_stop and should_stop():
            raise InterruptedError("Tournament stopped during opening generation")

    def choice(attempt: int, ply: int, index: int) -> bytes:
        return hashlib.sha256(f"opening-v1:{seed}:{attempt}:{ply}:{index}".encode("ascii")).digest()

    for attempt in range(MAX_ATTEMPTS):
        check()
        state = new_game(ruleset)
        moves: list[Move] = []
        length = (2, 4, 6)[choice(attempt, -1, 0)[0] % 3]
        for ply in range(length):
            check()
            candidates = sorted(legal_moves(state))
            # Hash ordering defines a stable random permutation, independent of
            # set/dict iteration order and the Python random module.
            candidates.sort(
                key=lambda move: choice(attempt, ply, move.source * len(state.board) + move.target)
            )
            for move in candidates:
                check()
                following = apply_move(state, move)
                if following.winner or following.board.count(".") != state.board.count("."):
                    continue
                if following.history[-1] in state.history:
                    continue
                state = following
                moves.append(move)
                break
            else:
                break
        if len(moves) != length:
            continue
        continuations = sorted(legal_moves(state))
        if not continuations:
            continue
        suitable = True
        for move in continuations:
            check()
            if apply_move(state, move).winner == state.turn:
                suitable = False
                break
        if not suitable:
            continue
        key, _ = canonical_board(state.board)
        opening = Opening(seed, tuple(moves), state, key not in seen)
        if opening.unique:
            seen.add(key)
            return opening
        fallback = opening
    if fallback is not None:
        return fallback
    raise ValueError("Could not generate a suitable legal opening within the bounded attempts")
