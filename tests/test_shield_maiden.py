"""Explicit royal tactics, rules geometry and compatibility for the one-ply agent."""

from dataclasses import replace
import random
import subprocess
import sys

import pytest

from hnefatafl.ai import ai_profiles, analyze_position, choose_move, evaluate
from hnefatafl.engine import RULESETS, State, apply_move, legal_moves, new_game
from hnefatafl.identities import agent_identity, effective_ai_config
from hnefatafl.shield_maiden import capture_threat, escape_routes, geometry


def position(ruleset, pieces, turn="defenders"):
    n = RULESETS[ruleset].size
    board = ["."] * (n * n)
    for (r, c), piece in pieces.items():
        board[r * n + c] = piece
    return State(ruleset, tuple(board), turn)


def selected(state, seed=7):
    return apply_move(state, choose_move(state, {"algorithm": "shield_maiden", "seed": seed}))


def royal_replies(state, winner):
    return [m for m in legal_moves(state) if apply_move(state, m).winner == winner]


@pytest.mark.parametrize("ruleset", RULESETS)
def test_immediate_legal_king_escape_dominates_material(ruleset):
    state = position(ruleset, {(0, 2): "K", (2, 3): "D", (3, 3): "A", (5, 3): "D"})
    assert selected(state).winner == "defenders"
    report = analyze_position(state, {"algorithm": "shield_maiden"})
    assert report["value"] == 1 and report["completed_depth"] == 1


@pytest.mark.parametrize("ruleset", ["brandubh", "tablut"])
def test_capture_weak_king_on_throne(ruleset):
    c = RULESETS[ruleset].size // 2
    state = position(
        ruleset, {(c, c): "K", (c, c - 1): "A", (c - 2, c + 1): "A", (c + 2, c): "D"}, "attackers"
    )
    assert selected(state).reason == "king_captured"


def test_copenhagen_capture_three_plus_empty_throne():
    state = position(
        "copenhagen", {(5, 6): "K", (4, 6): "A", (6, 6): "A", (2, 7): "A", (8, 8): "D"}, "attackers"
    )
    assert selected(state).reason == "king_captured"


@pytest.mark.parametrize("ruleset", RULESETS)
def test_block_immediate_escape_instead_of_taking_material(ruleset):
    # One escape corridor. The king's other edge ray is blocked by his own man.
    state = position(
        ruleset,
        {(0, 2): "K", (0, 3): "D", (2, 1): "A", (4, 3): "A", (4, 2): "D", (4, 0): "A"},
        "attackers",
    )
    after = selected(state)
    assert after.winner == "attackers" or not royal_replies(after, "defenders")


@pytest.mark.parametrize("ruleset", ["brandubh", "tablut", "copenhagen"])
def test_prevent_one_move_king_capture(ruleset):
    pieces = {(3, 3): "K", (3, 2): "A", (1, 4): "A", (5, 5): "D"}
    if ruleset == "copenhagen":
        pieces.update({(2, 3): "A", (4, 3): "A"})
    state = position(ruleset, pieces)
    assert royal_replies(replace(state, turn="attackers"), "attackers")
    after = selected(state)
    assert after.winner == "defenders" or not royal_replies(after, "attackers")


def test_capture_exposed_enemy_with_safe_material_gain():
    state = position(
        "brandubh",
        {
            (3, 3): "K",
            (2, 3): "D",
            (4, 3): "D",
            (3, 2): "D",
            (3, 4): "D",
            (1, 1): "A",
            (1, 0): "D",
            (0, 3): "D",
            (6, 3): "A",
            (3, 0): "A",
            (3, 6): "A",
        },
    )
    assert selected(state).board.count("A") < state.board.count("A")


def test_copenhagen_uses_engine_shieldwall_material_and_king_immunity():
    state = position(
        "copenhagen",
        {
            (0, 2): "D",
            (0, 3): "K",
            (0, 4): "D",
            (0, 1): "A",
            (0, 5): "A",
            (1, 2): "A",
            (1, 3): "A",
            (3, 4): "A",
            (0, 7): "A",
        },
        "attackers",
    )
    after = selected(state)
    assert after.board.count("D") == 0
    assert after.board.count("K") == 1


def test_open_and_blocked_corner_corridors():
    open_state = position("brandubh", {(2, 2): "K", (3, 3): "A"})
    blocked = position(
        "brandubh", {(2, 2): "K", (3, 3): "A", (1, 2): "A", (2, 1): "A", (2, 3): "A", (3, 2): "A"}
    )
    geo = geometry("brandubh")
    assert escape_routes(open_state.board, 16, "brandubh", geo)[1] > 0
    assert escape_routes(blocked.board, 16, "brandubh", geo) == (0, 0, 0)


@pytest.mark.parametrize("ruleset", RULESETS)
def test_geometry_threats_match_engine_royal_captures_on_explicit_positions(ruleset):
    pieces = {(3, 3): "K", (3, 2): "A", (1, 4): "A", (5, 5): "D"}
    if ruleset == "copenhagen":
        pieces.update({(2, 3): "A", (4, 3): "A"})
    state = position(ruleset, pieces, "attackers")
    geo = geometry(ruleset)
    for move in legal_moves(replace(state, turn="defenders")):
        child = apply_move(replace(state, turn="defenders"), move)
        if child.winner:
            continue
        expected = any(apply_move(child, m).reason == "king_captured" for m in legal_moves(child))
        assert capture_threat(child.board, child.board.index("K"), ruleset, geo) == expected


def test_safe_sandwich_edge_immunity_and_nonhostile_brandubh_throne():
    for ruleset in RULESETS:
        state = position(ruleset, {(3, 3): "K", (3, 2): "A", (3, 4): "A"})
        assert not capture_threat(state.board, state.board.index("K"), ruleset, geometry(ruleset))
    state = position("copenhagen", {(0, 3): "K", (0, 2): "A", (0, 4): "A", (1, 3): "A"})
    assert not capture_threat(state.board, 3, "copenhagen", geometry("copenhagen"))
    state = position("brandubh", {(3, 2): "K", (1, 1): "A"})
    assert not capture_threat(state.board, 23, "brandubh", geometry("brandubh"))


@pytest.mark.parametrize("ruleset", RULESETS)
def test_seeded_legal_immutable_deadline_and_diagnostics(ruleset, monkeypatch):
    state = new_game(ruleset)
    original = state.to_dict()
    config = {"algorithm": "shield_maiden", "seed": 42}
    details = {}
    first = choose_move(state, config, diagnostics=details)
    assert first == choose_move(state, config)
    assert first in legal_moves(state) and state.to_dict() == original
    assert 0 < details["searched_moves"] <= len(legal_moves(state))
    assert details["completed_depth"] == 1 and -1 <= details["value"] <= 1
    monkeypatch.setattr("hnefatafl.shield_maiden.time.monotonic", lambda: float("inf"))
    bounded = {}
    assert choose_move(state, config, diagnostics=bounded) in legal_moves(state)
    assert bounded["searched_moves"] == 1
    assert bounded["completed_depth"] == 0


def test_cancellation_is_cooperative_and_no_torch_dependency():
    state = new_game("copenhagen")
    calls = 0

    def stop():
        nonlocal calls
        calls += 1
        return calls > 3

    with pytest.raises(InterruptedError):
        choose_move(state, {"algorithm": "shield_maiden"}, should_stop=stop)
    subprocess.run(
        [
            sys.executable,
            "-c",
            "from hnefatafl.ai import choose_move; from hnefatafl.engine import new_game; import sys; choose_move(new_game('brandubh'), {'algorithm':'shield_maiden'}); assert 'torch' not in sys.modules",
        ],
        check=True,
    )


def test_baseline_algorithm_and_seeded_results_remain_unchanged():
    for ruleset in RULESETS:
        state = new_game(ruleset)
        moves = legal_moves(state)
        random.Random(23).shuffle(moves)
        sign = 1 if state.turn == "defenders" else -1
        expected = max(moves, key=lambda m: sign * evaluate(apply_move(state, m)))
        assert choose_move(state, {"algorithm": "baseline", "seed": 23}) == expected
    baseline = {"kind": "ai", "config": {"algorithm": "baseline", "seed": 3}}
    maiden = {"kind": "ai", "config": {"algorithm": "shield_maiden", "seed": 3}}
    assert agent_identity(baseline) != agent_identity(maiden)
    assert agent_identity(maiden) == agent_identity(
        {**maiden, "config": {**maiden["config"], "seed": 200}}
    )
    assert (
        next(p for p in ai_profiles() if p["id"] == "shield_maiden")["difficulty"] == "Intermediate"
    )
    assert effective_ai_config({"algorithm": "baseline"}) == {
        "algorithm": "baseline",
        "seed": 0,
        "time_limit": 2.0,
        "evaluation": "heuristic",
    }


def test_avoids_a_capture_that_sacrifices_a_more_valuable_defender():
    state = position(
        "brandubh",
        {
            (3, 3): "K",
            (2, 3): "D",
            (4, 3): "D",
            (3, 2): "D",
            (3, 4): "D",
            (1, 0): "D",
            (1, 1): "A",
            (0, 2): "A",
            (4, 2): "A",
            (3, 0): "A",
            (3, 6): "A",
        },
    )
    after = selected(state)
    assert after.board.count("A") == state.board.count("A")


@pytest.mark.parametrize("ruleset", RULESETS)
def test_cached_routes_equal_fresh_features_for_every_candidate(ruleset):
    from hnefatafl.shield_maiden import DecisionContext

    state = new_game(ruleset)
    rng = random.Random(109)
    for _ in range(8):
        if state.winner:
            break
        context = DecisionContext(state)
        for move in legal_moves(state):
            child = apply_move(state, move)
            cached = context.score(child, move)
            cells = context.route_cells
            context.route_cells = set(range(state.size**2))
            assert context.score(child, move) == cached
            context.route_cells = cells
        state = apply_move(state, rng.choice(legal_moves(state)))


@pytest.mark.parametrize("ruleset", RULESETS)
def test_open_endgame_route_cache_matches_uncached(ruleset):
    from hnefatafl.shield_maiden import DecisionContext

    state = position(ruleset, {(2, 2): "K", (3, 4): "A", (4, 1): "D", (5, 5): "A"})
    context = DecisionContext(state)
    for move in legal_moves(state):
        child = apply_move(state, move)
        cached = context.score(child, move)
        cells = context.route_cells
        context.route_cells = set(range(state.size**2))
        assert context.score(child, move) == cached
        context.route_cells = cells
