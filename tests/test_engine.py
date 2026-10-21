"""Source-specific examples and adversarial cases for every tafl ruleset."""

from dataclasses import replace
import json
import random

import pytest

from hnefatafl.engine import Move, RULESETS, State, apply_move, legal_moves, new_game, replay


def position(rule, pieces, turn="attackers"):
    size = RULESETS[rule].size
    board = ["."] * (size * size)
    for (row, col), piece in pieces.items():
        board[row * size + col] = piece
    return State(rule, tuple(board), turn)


def move(state, source, target):
    return apply_move(state, Move(source[0] * state.size + source[1], target[0] * state.size + target[1]))


@pytest.mark.parametrize("rule,counts,turn", [
    ("tablut", (16, 8, 1), "defenders"),
    ("brandubh", (8, 4, 1), "attackers"),
    ("copenhagen", (24, 12, 1), "attackers"),
])
def test_initial_board_and_side_match_cited_sources(rule, counts, turn):
    state = new_game(rule)
    assert tuple(state.board.count(piece) for piece in "ADK") == counts
    assert state.turn == turn
    assert state.board[state.size * state.size // 2] == "K"
    assert legal_moves(state)
    assert len(state.history) == 1


@pytest.mark.parametrize("rule", RULESETS)
def test_illegal_moves_rejected_without_mutating_state(rule):
    state = new_game(rule)
    before = state.to_dict()
    for illegal in (Move(-1, 0), Move(0, 1000), Move(0, 0), Move(True, 1), Move(state.size * state.size // 2, 0)):
        with pytest.raises(ValueError, match="Illegal"):
            apply_move(state, illegal)
    assert state.to_dict() == before


@pytest.mark.parametrize("rule", RULESETS)
def test_rook_moves_cannot_jump_piece_or_move_diagonally(rule):
    state = position(rule, {(2, 2): "A", (2, 4): "D", (4, 4): "K"})
    for target in ((3, 3), (2, 5), (2, 4)):
        with pytest.raises(ValueError):
            move(state, (2, 2), target)
    result = move(state, (2, 2), (2, 3))
    assert result.board[2 * state.size + 3] == "A"
    assert state.board[2 * state.size + 2] == "A"
    assert result.turn == "defenders"


@pytest.mark.parametrize("rule", RULESETS)
def test_three_simultaneous_sandwich_captures(rule):
    state = position(rule, {
        (5, 2): "A", (2, 1): "D", (2, 3): "D", (1, 2): "D",
        (2, 0): "A", (2, 4): "A", (0, 2): "A", (4, 4): "K",
    })
    result = move(state, (5, 2), (2, 2))
    assert result.board.count("D") == 0


@pytest.mark.parametrize("rule", RULESETS)
def test_moving_into_sandwich_does_not_self_capture(rule):
    state = position(rule, {(2, 1): "A", (2, 3): "A", (5, 2): "D", (4, 4): "K"}, "defenders")
    result = move(state, (5, 2), (2, 2))
    assert result.board[2 * state.size + 2] == "D"


@pytest.mark.parametrize("rule", RULESETS)
def test_king_participates_in_normal_capture(rule):
    state = position(rule, {(2, 1): "K", (2, 2): "A", (5, 3): "D", (0, 4): "A"}, "defenders")
    result = move(state, (5, 3), (2, 3))
    assert result.board[2 * state.size + 2] == "."


def test_tablut_market_leaflet_defenders_first_and_unrestricted_throne():
    assert new_game("tablut").turn == "defenders"
    state = position("tablut", {(4, 2): "A", (6, 6): "K", (1, 1): "D"})
    result = move(state, (4, 2), (4, 4))
    assert result.board[40] == "A"
    assert move(state, (4, 2), (4, 6)).board[42] == "A"


def test_tablut_market_leaflet_empty_center_is_not_hostile():
    state = position("tablut", {(4, 3): "D", (4, 0): "A", (6, 6): "K"})
    result = move(state, (4, 0), (4, 2))
    assert result.board[39] == "D"


@pytest.mark.parametrize("rule", ["tablut", "brandubh"])
def test_market_tablut_and_brandubh_king_captured_with_two_attackers(rule):
    center = RULESETS[rule].size // 2
    state = position(rule, {(center, center): "K", (center, center - 1): "A", (center + 2, center + 1): "A"})
    result = move(state, (center + 2, center + 1), (center, center + 1))
    assert result.winner == "attackers"
    assert result.reason == "king_captured"
    assert "K" not in result.board
    assert legal_moves(result) == []


def test_tablut_king_escapes_on_noncorner_edge():
    state = position("tablut", {(2, 2): "K", (7, 7): "A"}, "defenders")
    result = move(state, (2, 2), (0, 2))
    assert (result.winner, result.reason) == ("defenders", "king_escaped")


@pytest.mark.parametrize("rule", ["brandubh", "copenhagen"])
def test_restricted_throne_may_be_crossed_but_not_occupied_by_soldier(rule):
    center = RULESETS[rule].size // 2
    state = position(rule, {(center, center - 2): "A", (1, 1): "K"})
    with pytest.raises(ValueError):
        move(state, (center, center - 2), (center, center))
    result = move(state, (center, center - 2), (center, center + 2))
    assert result.board[center * state.size + center + 2] == "A"


@pytest.mark.parametrize("rule", ["brandubh", "copenhagen"])
def test_king_may_reenter_throne(rule):
    center = RULESETS[rule].size // 2
    state = position(rule, {(center, center - 2): "K", (1, 1): "A"}, "defenders")
    assert move(state, (center, center - 2), (center, center)).board[center * state.size + center] == "K"


@pytest.mark.parametrize("rule", ["brandubh", "copenhagen"])
def test_corner_restriction_escape_and_hostility(rule):
    state = position(rule, {(0, 1): "D", (2, 2): "A", (4, 4): "K"})
    result = move(state, (2, 2), (0, 2))
    assert result.board[1] == "."
    state = position(rule, {(0, 2): "A", (4, 4): "K"})
    with pytest.raises(ValueError):
        move(state, (0, 2), (0, 0))
    state = position(rule, {(0, 2): "K", (4, 4): "A"}, "defenders")
    assert move(state, (0, 2), (0, 0)).winner == "defenders"


def test_brandubh_center_explicitly_not_hostile():
    state = position("brandubh", {(3, 2): "D", (3, 0): "A", (5, 5): "K"})
    result = move(state, (3, 0), (3, 1))
    assert result.board[23] == "D"


def test_brandubh_edge_is_not_escape():
    state = position("brandubh", {(2, 2): "K", (5, 5): "A"}, "defenders")
    assert move(state, (2, 2), (0, 2)).winner is None


def test_copenhagen_king_on_throne_requires_four_attackers():
    state = position("copenhagen", {(5, 5): "K", (4, 5): "A", (5, 4): "A", (5, 6): "A", (8, 5): "A"})
    result = move(state, (8, 5), (6, 5))
    assert result.winner == "attackers" and result.reason == "king_captured"


def test_copenhagen_king_adjacent_throne_requires_three_attackers():
    state = position("copenhagen", {(5, 4): "K", (4, 4): "A", (5, 3): "A", (8, 4): "A"})
    result = move(state, (8, 4), (6, 4))
    assert result.winner == "attackers" and result.reason == "king_captured"


def test_copenhagen_two_sided_king_capture_does_not_work():
    state = position("copenhagen", {(4, 4): "K", (4, 3): "A", (8, 5): "A", (9, 9): "D"})
    result = move(state, (8, 5), (4, 5))
    assert result.winner is None and "K" in result.board


def test_copenhagen_king_cannot_be_captured_against_edge():
    state = position("copenhagen", {(0, 4): "K", (0, 3): "A", (0, 5): "A", (4, 4): "A", (9, 9): "D"})
    result = move(state, (4, 4), (1, 4))
    assert "K" in result.board and result.winner is None


@pytest.mark.parametrize("occupied", [False, True])
def test_copenhagen_empty_throne_hostile_but_king_protects_adjacent_defender(occupied):
    pieces = {(5, 4): "D", (5, 0): "A", ((5, 5) if occupied else (8, 8)): "K"}
    state = position("copenhagen", pieces)
    result = move(state, (5, 0), (5, 3))
    assert result.board[59] == ("D" if occupied else ".")


@pytest.mark.parametrize("completion", ["bracket", "back"])
def test_copenhagen_shieldwall_captures_group(completion):
    pieces = {(0, 3): "D", (0, 4): "D", (0, 5): "D", (0, 6): "A", (1, 3): "A", (1, 4): "A", (1, 5): "A", (5, 5): "K", (9, 9): "D"}
    if completion == "bracket":
        pieces[(3, 2)] = "A"
        source, target = (3, 2), (0, 2)
    else:
        pieces[(0, 2)] = "A"
        del pieces[(1, 4)]
        pieces[(3, 4)] = "A"
        source, target = (3, 4), (1, 4)
    result = move(position("copenhagen", pieces), source, target)
    assert all(result.board[c] == "." for c in (3, 4, 5))


def test_copenhagen_shieldwall_can_use_corner_and_king_survives():
    state = position("copenhagen", {(0, 1): "D", (0, 2): "K", (1, 1): "A", (1, 2): "A", (4, 3): "A", (9, 9): "D"})
    result = move(state, (4, 3), (0, 3))
    assert result.board[1] == "."
    assert result.board[2] == "K"


def test_copenhagen_shieldwall_requires_every_backing_square():
    state = position("copenhagen", {(0, 3): "D", (0, 4): "D", (0, 5): "D", (0, 6): "A", (1, 3): "A", (1, 5): "A", (5, 5): "K", (3, 2): "A"})
    result = move(state, (3, 2), (0, 2))
    assert all(result.board[c] == "D" for c in (3, 4, 5))


def test_copenhagen_encirclement_requires_all_defenders_inside_ring():
    pieces = {(5, 5): "K", (5, 6): "D"}
    for col in range(3, 9):
        pieces[(3, col)] = pieces[(8, col)] = "A"
    for row in range(4, 8):
        pieces[(row, 3)] = pieces[(row, 8)] = "A"
    del pieces[(3, 5)]
    pieces[(1, 5)] = "A"
    enclosed = move(position("copenhagen", pieces), (1, 5), (3, 5))
    assert (enclosed.winner, enclosed.reason) == ("attackers", "encirclement")
    pieces[(1, 1)] = "D"
    assert move(position("copenhagen", pieces), (1, 5), (3, 5)).winner is None


@pytest.mark.parametrize("fort", [1, 2])
def test_copenhagen_documented_exit_fort_examples(fort):
    if fort == 1:
        pieces = {(10, 3): "D", (10, 4): "K", (10, 6): "D", (9, 4): "D", (9, 5): "D"}
    else:
        pieces = {(10, 4): "D", (10, 5): "K", (10, 6): "D", (9, 4): "D", (9, 6): "D", (8, 4): "D", (8, 5): "D"}
    pieces[(1, 1)] = "A"
    result = move(position("copenhagen", pieces), (1, 1), (1, 2))
    assert (result.winner, result.reason) == ("defenders", "edge_fort")


def test_copenhagen_breakable_exit_fort_is_not_a_win():
    # The missing upper-right wall lets attackers enter the king's pocket.
    pieces = {(10, 3): "D", (10, 4): "K", (10, 6): "D", (9, 4): "D", (1, 1): "A"}
    assert move(position("copenhagen", pieces), (1, 1), (1, 2)).winner is None


def test_copenhagen_sealed_but_capturable_wall_is_not_exit_fort():
    # This pocket is fully enclosed, but the lower wall man (2,4) can be
    # sandwiched horizontally from the exterior, opening the pocket.
    pieces = {(0, 4): "K", (0, 3): "D", (0, 5): "D", (1, 3): "D", (1, 5): "D", (2, 4): "D", (8, 8): "A"}
    assert move(position("copenhagen", pieces), (8, 8), (8, 9)).winner is None


def test_copenhagen_thick_but_peelable_wall_is_not_exit_fort():
    # The outside wall initially hides the weak inner wall. The structural
    # recognizer must continue after peeling it, rather than call this a fort.
    pieces = {(0, 4): "K", (0, 3): "D", (0, 5): "D", (1, 3): "D", (1, 5): "D", (2, 4): "D", (3, 4): "D", (8, 8): "A"}
    assert move(position("copenhagen", pieces), (8, 8), (8, 9)).winner is None


def test_copenhagen_immobile_king_is_not_an_exit_fort():
    pieces = {(10, 3): "D", (10, 4): "K", (10, 5): "D", (9, 4): "D", (1, 1): "A"}
    assert move(position("copenhagen", pieces), (1, 1), (1, 2)).winner is None


@pytest.mark.parametrize("rule,expected", [("tablut", "draw"), ("brandubh", "draw"), ("copenhagen", "attackers")])
def test_threefold_positions_include_side_to_move(rule, expected):
    n = RULESETS[rule].size
    center = n // 2
    state = position(rule, {(center, center): "K", (1, 1): "A", (n - 2, n - 2): "D"})
    cycle = [((1, 1), (1, 2)), ((n - 2, n - 2), (n - 2, n - 3)), ((1, 2), (1, 1)), ((n - 2, n - 3), (n - 2, n - 2))]
    for repeat in range(2):
        for source, target in cycle:
            state = move(state, source, target)
    assert (state.winner, state.reason) == (expected, "threefold_repetition")


@pytest.mark.parametrize("rule", RULESETS)
def test_serialization_and_replay_are_deterministic(rule):
    state = new_game(rule)
    moves = []
    rng = random.Random(73)
    for _ in range(20):
        if state.winner:
            break
        selected = rng.choice(legal_moves(state))
        moves.append(selected.to_dict())
        state = apply_move(state, selected)
    assert State.from_dict(json.loads(json.dumps(state.to_dict()))) == state
    assert replay(rule, moves) == state
    with pytest.raises(ValueError):
        replay(rule, [{"source": 10000, "target": 0}])


def test_invalid_serialized_states_fail_safely():
    valid = new_game("brandubh").to_dict()
    for changes in ({"board": ["."]}, {"turn": "spectator"}, {"winner": "nobody"}, {"ply": -1}, {"history": [42]}, {"ruleset": "chess"}):
        with pytest.raises(ValueError):
            State.from_dict(valid | changes)
    with pytest.raises(ValueError):
        State.from_dict({})


@pytest.mark.parametrize("rule,winner", [("brandubh", "draw"), ("tablut", "draw"), ("copenhagen", "defenders")])
def test_no_moves_uses_variant_specific_outcome(rule, winner):
    state = position(rule, {(2, 1): "K", (2, 2): "A", (5, 3): "D"}, "defenders")
    result = move(state, (5, 3), (2, 3))
    assert (result.winner, result.reason) == (winner, "no_legal_moves")


@pytest.mark.parametrize("rule", RULESETS)
def test_terminal_state_cannot_accept_moves(rule):
    state = replace(new_game(rule), winner="draw", reason="agreement")
    assert legal_moves(state) == []
    with pytest.raises(ValueError):
        apply_move(state, Move(0, 1))
