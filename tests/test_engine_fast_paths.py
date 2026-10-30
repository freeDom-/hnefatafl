"""Independent legal-ray and encirclement oracles for engine fast paths."""

from collections import deque
from dataclasses import replace
import random

import pytest

from hnefatafl import engine
from hnefatafl.engine import RULESETS, State, apply_move, has_legal_move, legal_moves, new_game


def reference_moves(state):
    if state.winner:
        return []
    rule = RULESETS[state.ruleset]
    result = []
    for source, piece in enumerate(state.board):
        if (
            (piece == "A" and state.turn != "attackers")
            or (piece in "DK" and state.turn != "defenders")
            or piece == "."
        ):
            continue
        row, col = divmod(source, state.size)
        for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            for distance in range(1, state.size):
                r, c = row + distance * dr, col + distance * dc
                if not (0 <= r < state.size and 0 <= c < state.size):
                    break
                target = r * state.size + c
                if state.board[target] != ".":
                    break
                corner = r in (0, state.size - 1) and c in (0, state.size - 1)
                throne = r == c == state.size // 2
                if piece == "K" or not (
                    (rule.restricted_corners and corner) or (rule.restricted_throne and throne)
                ):
                    result.append((source, target))
    return result


@pytest.mark.parametrize("rule", RULESETS)
def test_legal_order_and_existence_match_independent_rays(rule):
    rng = random.Random(19)
    state = new_game(rule)
    for _ in range(100):
        expected = reference_moves(state)
        actual = legal_moves(state)
        assert [(move.source, move.target) for move in actual] == expected
        assert has_legal_move(state) == bool(expected)
        if state.winner:
            break
        state = apply_move(state, rng.choice(actual))
    assert not has_legal_move(replace(state, winner="draw"))


@pytest.mark.parametrize("rule", RULESETS)
def test_existence_allocates_no_moves_and_handles_immobility(rule, monkeypatch):
    state = new_game(rule)

    def forbidden(*args):
        raise AssertionError("Existence queries must not allocate Move objects")

    monkeypatch.setattr(engine, "Move", forbidden)
    assert has_legal_move(state)
    board = ["A"] * state.size**2
    board[len(board) // 2] = "K"
    assert not has_legal_move(State(rule, tuple(board), "defenders"))


def reference_encircled(board, size):
    defenders = [i for i, piece in enumerate(board) if piece in "DK"]
    if not defenders:
        return False
    for source in defenders:
        queue = deque([source])
        seen = {source}
        while queue:
            cell = queue.popleft()
            row, col = divmod(cell, size)
            if row in (0, size - 1) or col in (0, size - 1):
                return False
            for nxt in (cell - size, cell + size, cell - 1, cell + 1):
                if nxt not in seen and board[nxt] != "A":
                    seen.add(nxt)
                    queue.append(nxt)
    return True


def test_encirclement_early_exit_matches_defender_to_edge_oracle():
    rng = random.Random(42)
    for _ in range(300):
        board = rng.choices(".ADK", weights=(4, 7, 1, 0.2), k=121)
        assert engine._encircled(board, 11) == reference_encircled(board, 11)


def test_geometry_is_cached_and_immutable():
    for size in (7, 9, 11):
        assert engine._rays(size) is engine._rays(size)
        assert isinstance(engine._neighbors(0, size), tuple)
        assert engine._corners(size) is engine._corners(size)
