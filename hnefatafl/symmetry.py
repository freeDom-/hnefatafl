"""D4 geometry for square tafl boards; no neural or persistence dependencies.

Transforms map source coordinates to destination coordinates. Reflections first
mirror left/right, then rotate clockwise. Canonical board keys deliberately have
a different type/tag from full, repetition-safe state keys. No search uses these
keys yet, and neural predictions must not be shared across orientations.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import IntEnum
from functools import lru_cache
from math import isqrt

from .engine import Move, State


class Symmetry(IntEnum):
    IDENTITY = 0
    ROTATE_90 = 1
    ROTATE_180 = 2
    ROTATE_270 = 3
    REFLECT = 4
    REFLECT_ROTATE_90 = 5
    REFLECT_ROTATE_180 = 6
    REFLECT_ROTATE_270 = 7


@lru_cache(maxsize=32)
def coordinate_map(size: int, symmetry: Symmetry) -> tuple[int, ...]:
    """Return the immutable source-to-destination permutation (clockwise)."""
    if type(size) is not int or size < 1:
        raise ValueError("Board size must be a positive integer")
    symmetry = Symmetry(symmetry)
    result = []
    for index in range(size * size):
        row, col = divmod(index, size)
        if symmetry.value >= 4:
            col = size - 1 - col
        for _ in range(symmetry.value % 4):
            row, col = col, size - 1 - row
        result.append(row * size + col)
    return tuple(result)


def inverse(symmetry: Symmetry) -> Symmetry:
    symmetry = Symmetry(symmetry)
    return symmetry if symmetry.value >= 4 else Symmetry(-symmetry.value % 4)


def transform_index(index: int, size: int, symmetry: Symmetry) -> int:
    if type(index) is not int or not 0 <= index < size * size:
        raise ValueError("Square index is outside the board")
    return coordinate_map(size, symmetry)[index]


def transform_move(move: Move, size: int, symmetry: Symmetry) -> Move:
    return Move(
        transform_index(move.source, size, symmetry), transform_index(move.target, size, symmetry)
    )


def transform_board(board: tuple[str, ...], symmetry: Symmetry) -> tuple[str, ...]:
    size = isqrt(len(board))
    if size < 1 or size * size != len(board):
        raise ValueError("Board must be a nonempty square")
    result = ["."] * len(board)
    for source, target in enumerate(coordinate_map(size, symmetry)):
        result[target] = board[source]
    return tuple(result)


def _transform_history(entry: str, size: int, symmetry: Symmetry) -> str:
    turn, separator, board = entry.partition(":")
    if (
        separator != ":"
        or turn not in ("attackers", "defenders")
        or len(board) != size * size
        or any(piece not in ".ADK" for piece in board)
    ):
        raise ValueError("Invalid repetition history entry")
    return turn + ":" + "".join(transform_board(tuple(board), symmetry))


def transform_state(state: State, symmetry: Symmetry) -> State:
    """Transform the entire history in one common orientation, retaining metadata."""
    return replace(
        state,
        board=transform_board(state.board, symmetry),
        history=tuple(_transform_history(entry, state.size, symmetry) for entry in state.history),
    )


@dataclass(frozen=True, order=True)
class BoardKey:
    """Board geometry only: NEVER a repetition-safe game/search key."""

    board: tuple[str, ...]


@dataclass(frozen=True)
class StateKey:
    """Full history-aware state identity, distinct from BoardKey."""

    ruleset: str
    board: tuple[str, ...]
    turn: str
    ply: int
    winner: str | None
    reason: str | None
    history: tuple[str, ...]


def canonical_board(board: tuple[str, ...]) -> tuple[BoardKey, Symmetry]:
    """Return least board and the transform to it; ties use enum order."""
    return min((BoardKey(transform_board(board, symmetry)), symmetry) for symmetry in Symmetry)


def canonical_state_key(state: State) -> tuple[StateKey, Symmetry]:
    """Canonicalize full histories globally, breaking board ties using history.

    Canonicalizing each historical board separately would erase relative
    orientations and could change future repetition outcomes.
    """
    candidates = [(transform_state(state, symmetry), symmetry) for symmetry in Symmetry]
    canonical, symmetry = min(
        candidates, key=lambda item: (item[0].board, item[0].history, item[1])
    )
    return StateKey(
        canonical.ruleset,
        canonical.board,
        canonical.turn,
        canonical.ply,
        canonical.winner,
        canonical.reason,
        canonical.history,
    ), symmetry
