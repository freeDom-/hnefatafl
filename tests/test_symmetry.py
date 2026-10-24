"""Full-state geometry, including history orientation and special adjudications."""

from dataclasses import replace
import random
import subprocess
import sys

import pytest

from hnefatafl.engine import RULESETS, Move, State, apply_move, legal_moves, new_game
from hnefatafl.symmetry import (
    BoardKey,
    Symmetry,
    canonical_board,
    canonical_state_key,
    coordinate_map,
    inverse,
    transform_board,
    transform_index,
    transform_move,
    transform_state,
)
from test_engine import position


@pytest.mark.parametrize("rule", RULESETS)
@pytest.mark.parametrize("symmetry", Symmetry)
def test_initial_boards_inverse_and_coordinates(rule, symmetry):
    state = new_game(rule)
    assert transform_state(state, symmetry) == state
    assert canonical_board(state.board)[1] == Symmetry.IDENTITY
    assert transform_state(transform_state(state, symmetry), inverse(symmetry)) == state
    mapping = coordinate_map(state.size, symmetry)
    assert sorted(mapping) == list(range(state.size**2))
    assert coordinate_map(state.size, symmetry) is mapping
    for index in range(state.size**2):
        assert transform_index(mapping[index], state.size, inverse(symmetry)) == index
    assert transform_index(0, state.size, Symmetry.ROTATE_90) == state.size - 1
    assert transform_index(0, state.size, Symmetry.REFLECT) == state.size - 1


def assert_commutes(state, move, symmetry):
    transformed = transform_state(state, symmetry)
    transformed_move = transform_move(move, state.size, symmetry)
    assert transform_move(transformed_move, state.size, inverse(symmetry)) == move
    assert set(legal_moves(transformed)) == {
        transform_move(option, state.size, symmetry) for option in legal_moves(state)
    }
    result = apply_move(state, move)
    observed = apply_move(transformed, transformed_move)
    assert observed == transform_state(result, symmetry)
    assert transform_state(observed, inverse(symmetry)) == result
    assert canonical_state_key(observed)[0] == canonical_state_key(result)[0]
    assert canonical_board(observed.board)[0] == canonical_board(result.board)[0]
    assert (observed.winner, observed.reason) == (result.winner, result.reason)
    assert State.from_dict(observed.to_dict()) == observed
    return result


@pytest.mark.parametrize("rule", RULESETS)
@pytest.mark.parametrize("seed", [7, 73, 192])
def test_random_legal_game_histories(rule, seed):
    state = new_game(rule)
    rng = random.Random(seed)
    for _ in range(24):
        if state.winner:
            break
        move = rng.choice(legal_moves(state))
        for symmetry in Symmetry:
            result = assert_commutes(state, move, symmetry)
        state = result
    assert len(state.history) == state.ply + 1


@pytest.mark.parametrize("rule", RULESETS)
@pytest.mark.parametrize("symmetry", Symmetry)
def test_recorded_threefold_history_and_terminal_metadata(rule, symmetry):
    n = RULESETS[rule].size
    state = position(rule, {(n // 2, n // 2): "K", (1, 1): "A", (n - 2, n - 2): "D"})
    cycle = [
        ((1, 1), (1, 2)),
        ((n - 2, n - 2), (n - 2, n - 3)),
        ((1, 2), (1, 1)),
        ((n - 2, n - 3), (n - 2, n - 2)),
    ]
    for source, target in cycle * 2:
        state = assert_commutes(
            state, Move(source[0] * n + source[1], target[0] * n + target[1]), symmetry
        )
    assert state.reason == "threefold_repetition"
    assert state.winner == ("attackers" if rule == "copenhagen" else "draw")
    assert legal_moves(transform_state(state, symmetry)) == []


@pytest.mark.parametrize("rule", RULESETS)
@pytest.mark.parametrize("symmetry", Symmetry)
def test_captures_and_escape(rule, symmetry):
    state = position(
        rule,
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
    )
    result = assert_commutes(state, Move(5 * state.size + 2, 2 * state.size + 2), symmetry)
    assert result.board.count("D") == 0
    state = position(rule, {(0, 2): "K", (4, 4): "A"}, "defenders")
    assert assert_commutes(state, Move(2, 0), symmetry).winner == "defenders"


@pytest.mark.parametrize("symmetry", Symmetry)
@pytest.mark.parametrize(
    "case",
    ["shieldwall", "king_shieldwall", "encirclement", "fort", "breakable_fort", "king_capture"],
)
def test_copenhagen_special_cases(case, symmetry):
    if case == "shieldwall":
        pieces = {
            (0, 3): "D",
            (0, 4): "D",
            (0, 5): "D",
            (0, 6): "A",
            (1, 3): "A",
            (1, 4): "A",
            (1, 5): "A",
            (5, 5): "K",
            (9, 9): "D",
            (3, 2): "A",
        }
        source, target, winner, reason = (3, 2), (0, 2), None, None
    elif case == "king_shieldwall":
        pieces = {(0, 1): "D", (0, 2): "K", (1, 1): "A", (1, 2): "A", (4, 3): "A", (9, 9): "D"}
        source, target, winner, reason = (4, 3), (0, 3), None, None
    elif case == "encirclement":
        pieces = {(5, 5): "K", (5, 6): "D"}
        for col in range(3, 9):
            pieces[(3, col)] = pieces[(8, col)] = "A"
        for row in range(4, 8):
            pieces[(row, 3)] = pieces[(row, 8)] = "A"
        del pieces[(3, 5)]
        pieces[(1, 5)] = "A"
        source, target, winner, reason = (1, 5), (3, 5), "attackers", "encirclement"
    elif case in ("fort", "breakable_fort"):
        pieces = {(10, 3): "D", (10, 4): "K", (10, 6): "D", (9, 4): "D", (1, 1): "A"}
        if case == "fort":
            pieces[(9, 5)] = "D"
        source, target = (1, 1), (1, 2)
        winner, reason = ("defenders", "edge_fort") if case == "fort" else (None, None)
    else:
        pieces = {(5, 5): "K", (4, 5): "A", (5, 4): "A", (5, 6): "A", (8, 5): "A"}
        source, target, winner, reason = (8, 5), (6, 5), "attackers", "king_captured"
    state = position("copenhagen", pieces)
    result = assert_commutes(
        state, Move(source[0] * 11 + source[1], target[0] * 11 + target[1]), symmetry
    )
    assert (result.winner, result.reason) == (winner, reason)
    if case == "shieldwall":
        assert all(result.board[index] == "." for index in (3, 4, 5))
    elif case == "king_shieldwall":
        assert result.board[1:3] == (".", "K")


def test_full_key_distinguishes_history_metadata_and_global_orientation():
    state = new_game("brandubh")
    asymmetric = apply_move(state, legal_moves(state)[0])
    historic = asymmetric.history[-1]
    a = replace(state, history=(historic, *state.history), ply=2)
    b = replace(state, history=(historic, historic, *state.history), ply=2)
    assert isinstance(canonical_board(state.board)[0], BoardKey)
    assert canonical_board(a.board) == canonical_board(b.board)
    assert canonical_state_key(a)[0] != canonical_state_key(b)[0]
    # The current board is symmetric, so history must break its canonical tie.
    for symmetry in Symmetry:
        assert canonical_state_key(transform_state(a, symmetry))[0] == canonical_state_key(a)[0]
    for changes in ({"ply": 4}, {"turn": "defenders"}, {"winner": "draw", "reason": "agreement"}):
        assert canonical_state_key(replace(a, **changes))[0] != canonical_state_key(a)[0]
    rotated = transform_state(asymmetric, Symmetry.ROTATE_90).history[-1]
    inconsistent = replace(a, history=(historic, rotated))
    consistent = replace(a, history=(historic, historic))
    assert canonical_state_key(inconsistent)[0] != canonical_state_key(consistent)[0]


def test_validation_and_dependency_boundary():
    for board in ((), (".", ".")):
        with pytest.raises(ValueError, match="square"):
            transform_board(board, Symmetry.IDENTITY)
    with pytest.raises(ValueError):
        transform_index(-1, 7, Symmetry.IDENTITY)
    with pytest.raises(ValueError):
        coordinate_map(0, Symmetry.IDENTITY)
    with pytest.raises(ValueError):
        transform_state(replace(new_game(), history=("broken",)), Symmetry.IDENTITY)
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import hnefatafl.symmetry; assert 'torch' not in sys.modules; assert 'fastapi' not in sys.modules",
        ],
        check=True,
    )
