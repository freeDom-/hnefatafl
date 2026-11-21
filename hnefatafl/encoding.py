"""PyTorch-independent six-plane position encoding.

One implementation is shared by native training/inference and the browser search
worker, so the browser never loads PyTorch merely to encode a position and the
two encoders cannot drift apart.

The encoding is the established ``six-plane-v1`` representation:

1. attacker pieces,
2. defender pieces,
3. the king,
4. a defender-to-move indicator,
5. the throne square,
6. restricted corner squares (every ruleset except Tablut, which escapes on any
   edge and has no restricted corners).

Values are exactly ``0``/``1`` bytes, converted to FP32 for the network input
``[batch, 6, size, size]``. Actions are indexed ``source * size**2 + target`` in
``legal_moves()`` order.

The six-plane representation deliberately does **not** contain repetition
history, remaining material outside the board, or the ruleset identity beyond
the size/corner conventions. It is therefore an identity for neural *input*, not
an identity for a completed search result.
"""

from __future__ import annotations

from .engine import Move, State

BOARD_SIZES = {"brandubh": 7, "tablut": 9, "copenhagen": 11}
ENCODER_VERSION = "six-plane-v1"
PLANE_COUNT = 6


def board_size(state: State) -> int:
    """Validate that a state can be encoded and return its board size."""
    size = state.size
    if state.ruleset not in BOARD_SIZES or size != BOARD_SIZES[state.ruleset]:
        raise ValueError("Unsupported ruleset or incompatible board dimensions")
    if len(state.board) != size * size or any(piece not in ".ADK" for piece in state.board):
        raise ValueError("Invalid board encoding")
    if state.turn not in ("attackers", "defenders"):
        raise ValueError("Invalid side to move")
    return size


def encode_planes(state: State) -> bytes:
    """Return the six-plane input as ``6 * size * size`` bytes of 0/1."""
    size = board_size(state)
    area = size * size
    planes = bytearray(PLANE_COUNT * area)
    for index, piece in enumerate(state.board):
        if piece == "A":
            planes[index] = 1
        elif piece == "D":
            planes[area + index] = 1
        elif piece == "K":
            planes[2 * area + index] = 1
    if state.turn == "defenders":
        planes[3 * area : 4 * area] = b"\x01" * area
    center = (size // 2) * size + size // 2
    planes[4 * area + center] = 1
    # Tablut escapes on any edge and does not use restricted corner squares.
    if state.ruleset != "tablut":
        for row, column in ((0, 0), (0, size - 1), (size - 1, 0), (size - 1, size - 1)):
            planes[5 * area + row * size + column] = 1
    return bytes(planes)


def plane_values(state: State) -> tuple[float, ...]:
    """Return the six-plane input as the float values expected by the network."""
    return tuple(float(value) for value in encode_planes(state))


def action_index(move: Move, size: int) -> int:
    """Return the policy index of a legal move."""
    return move.source * (size * size) + move.target


def legal_action_indices(state: State, moves: list[Move]) -> list[int]:
    """Return legal action indices in the exact order of ``moves``."""
    area = state.size * state.size
    return [move.source * area + move.target for move in moves]
