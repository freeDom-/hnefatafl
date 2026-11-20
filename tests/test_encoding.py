"""Exact parity for the shared PyTorch-independent six-plane encoder.

The browser search worker imports :mod:`hnefatafl.encoding` and must never load
PyTorch to encode a position. These tests pin the encoded values to the native
FP32 tensor used by training and inference across every supported ruleset, both
sides to move and the position kinds that matter for search.
"""

from __future__ import annotations

import subprocess
import sys

import pytest
import torch

from hnefatafl.encoding import (
    BOARD_SIZES,
    ENCODER_VERSION,
    PLANE_COUNT,
    action_index,
    board_size,
    encode_planes,
    legal_action_indices,
    plane_values,
)
from hnefatafl.engine import RULESETS, Move, State, apply_move, legal_moves, new_game
from hnefatafl.models import encode_state


def _state(ruleset: str, pieces: dict, turn: str, **kwargs) -> State:
    size = RULESETS[ruleset].size
    board = ["."] * (size * size)
    for (row, column), piece in pieces.items():
        board[row * size + column] = piece
    return State(ruleset, tuple(board), turn, **kwargs)


def _positions() -> list[tuple[str, State]]:
    cases: list[tuple[str, State]] = []
    for ruleset, rules in RULESETS.items():
        size = rules.size
        center = size // 2
        opening = new_game(ruleset)
        cases.append((f"{ruleset}-opening-{opening.turn}", opening))
        cases.append(
            (f"{ruleset}-opening-defender-turn", State(ruleset, opening.board, "defenders"))
        )
        midgame = opening
        for step in range(6):
            moves = legal_moves(midgame)
            midgame = apply_move(midgame, moves[step % len(moves)])
        cases.append((f"{ruleset}-midgame", midgame))
        cases.append(
            (
                f"{ruleset}-asymmetric",
                _state(
                    ruleset,
                    {
                        (center, center): "K",
                        (center, center - 1): "D",
                        (center - 1, center): "D",
                        (0, center): "A",
                        (size - 1, center): "A",
                        (center, 0): "A",
                        (center + 1, center + 1): "A",
                    },
                    "attackers",
                ),
            )
        )
        cases.append(
            (
                f"{ruleset}-asymmetric-defenders",
                _state(
                    ruleset,
                    {
                        (center, center): "K",
                        (center, center - 1): "D",
                        (center - 1, center): "D",
                        (0, center): "A",
                        (center, 0): "A",
                        (center + 1, center + 1): "A",
                    },
                    "defenders",
                ),
            )
        )
        cases.append(
            (
                f"{ruleset}-tactical",
                _state(
                    ruleset,
                    {
                        (5, 2): "A",
                        (2, 1): "D",
                        (2, 3): "D",
                        (1, 2): "D",
                        (2, 0): "A",
                        (2, 4): "A",
                        (0, 2): "A",
                        (4, 4): "K",
                    },
                    "attackers",
                ),
            )
        )
        escape = _state(ruleset, {(0, 2): "K", (4, 4): "A"}, "defenders")
        cases.append((f"{ruleset}-king-escape", escape))
        terminal = apply_move(escape, Move(2, 0))
        assert terminal.winner == "defenders"
        cases.append((f"{ruleset}-terminal", terminal))
        fresh = new_game(ruleset)
        move = legal_moves(fresh)[0]
        key = apply_move(fresh, move).history[-1]
        repeated = State(ruleset, fresh.board, fresh.turn, history=(key, key))
        cases.append((f"{ruleset}-repetition", repeated))
    return cases


CASES = _positions()


@pytest.mark.parametrize("name,state", CASES, ids=[case[0] for case in CASES])
def test_byte_planes_match_native_tensor(name, state):
    planes = encode_planes(state)
    size = BOARD_SIZES[state.ruleset]
    assert len(planes) == PLANE_COUNT * size * size
    assert set(planes) <= {0, 1}
    tensor = encode_state(state)
    assert tensor.shape == (PLANE_COUNT, size, size)
    assert tensor.dtype == torch.float32
    assert tensor.device.type == "cpu"
    # Exact equality: the shared encoder is the source of the tensor's values.
    assert torch.equal(
        tensor, torch.tensor(list(planes), dtype=torch.float32).reshape(6, size, size)
    )
    assert plane_values(state) == tuple(float(value) for value in planes)


@pytest.mark.parametrize("name,state", CASES, ids=[case[0] for case in CASES])
def test_plane_semantics(name, state):
    size = BOARD_SIZES[state.ruleset]
    area = size * size
    planes = encode_planes(state)
    for index, piece in enumerate(state.board):
        assert planes[index] == int(piece == "A")
        assert planes[area + index] == int(piece == "D")
        assert planes[2 * area + index] == int(piece == "K")
    assert all(planes[3 * area + index] == int(state.turn == "defenders") for index in range(area))
    center = (size // 2) * size + size // 2
    assert planes[4 * area + center] == 1
    assert sum(planes[4 * area : 5 * area]) == 1
    corner_plane = planes[5 * area : 6 * area]
    if state.ruleset == "tablut":
        # Tablut escapes on any edge, so there are no restricted corners.
        assert sum(corner_plane) == 0
    else:
        corners = {0, size - 1, size * (size - 1), size * size - 1}
        assert {index for index, value in enumerate(corner_plane) if value} == corners


def test_repetition_history_does_not_change_the_encoded_input():
    fresh = new_game("brandubh")
    repeated = State("brandubh", fresh.board, fresh.turn, history=("a" * 8, "a" * 8))
    assert encode_planes(fresh) == encode_planes(repeated)
    # The encoded input is not a search-result identity: the positions differ.
    assert fresh != repeated


def test_action_index_matches_policy_layout_and_legal_order():
    for ruleset in RULESETS:
        state = new_game(ruleset)
        size = RULESETS[ruleset].size
        moves = legal_moves(state)
        indices = legal_action_indices(state, moves)
        assert indices == [move.source * size**2 + move.target for move in moves]
        assert all(0 <= index < size**4 for index in indices)
        for move, index in zip(moves, indices):
            assert action_index(move, size) == index


def test_unsupported_positions_are_rejected_with_the_native_messages():
    valid = new_game("brandubh")
    bad_turn = State("brandubh", valid.board, valid.turn)
    object.__setattr__(bad_turn, "turn", "spectators")  # bypass dataclass validation
    with pytest.raises(ValueError, match="Invalid side to move"):
        encode_planes(bad_turn)
    bad_board = State("brandubh", valid.board, valid.turn)
    object.__setattr__(bad_board, "board", valid.board[:-1] + ("X",))
    with pytest.raises(ValueError, match="Invalid board encoding"):
        encode_planes(bad_board)
    truncated = State("brandubh", valid.board, valid.turn)
    object.__setattr__(truncated, "board", valid.board[:-1])
    # A truncated board fails the length check before the ruleset/size guard;
    # both are the native encoder's messages.
    with pytest.raises(ValueError, match="Invalid board encoding"):
        board_size(truncated)


def test_encoding_module_is_importable_without_torch():

    code = (
        "import sys; sys.modules['torch'] = None; "
        "from hnefatafl.encoding import encode_planes, ENCODER_VERSION; "
        "from hnefatafl.engine import new_game; "
        "assert len(encode_planes(new_game('tablut'))) == 6 * 9 * 9; "
        "print(ENCODER_VERSION)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ENCODER_VERSION
