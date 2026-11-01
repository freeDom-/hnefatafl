"""Lazy priority groups retain eager ordering, minimax and seeded ties."""

from copy import deepcopy
import random

import pytest

from hnefatafl import ai
from hnefatafl.engine import legal_moves, new_game
from test_alpha_beta_search import CORRECTNESS_POSITIONS


def counters():
    return {
        "successors": 0,
        "ordered_moves": 0,
        "successor_seconds": 0.0,
        "lazy_successors_skipped": 0,
    }


@pytest.mark.parametrize("rule", ["brandubh", "tablut", "copenhagen"])
@pytest.mark.parametrize("strategy", ai._ORDERING_STRATEGIES)
@pytest.mark.parametrize("maiden", [False, True])
@pytest.mark.parametrize("remaining", [2, 4])
def test_complete_lazy_order_equals_eager_including_scores_and_rng(
    rule, strategy, maiden, remaining
):
    state = new_game(rule)
    moves = legal_moves(state)
    tt_move, killers = moves[-1], (moves[1], moves[3])
    history = {moves[4]: 8, moves[7]: 8, moves[9]: 4}
    eager_rng, lazy_rng = random.Random(23), random.Random(23)
    settings = ai._search_settings({"_ordering": strategy}, False)
    expected = ai._order_children(
        ai._ordered(state, moves.copy(), eager_rng, maiden=maiden),
        strategy=strategy,
        remaining=remaining,
        tt_move=tt_move,
        pv_move=None,
        killers=killers,
        history=history,
    )
    metrics = counters()
    actual = list(
        ai._lazy_ordered(
            state,
            moves.copy(),
            lazy_rng,
            settings,
            remaining,
            tt_move,
            killers,
            history,
            maiden,
            lambda: None,
            metrics,
            True,
        )
    )
    assert actual == expected
    assert lazy_rng.getstate() == eager_rng.getstate()
    assert metrics["successors"] == len(moves)
    assert metrics["lazy_successors_skipped"] == 0


def test_cutoff_does_not_construct_unrequested_children():
    state = new_game("brandubh")
    moves = legal_moves(state)
    metrics = counters()
    stream = ai._lazy_ordered(
        state,
        moves.copy(),
        random.Random(7),
        ai._search_settings({}, False),
        3,
        moves[-1],
        (),
        {},
        True,
        lambda: None,
        metrics,
        False,
    )
    assert next(stream)[0] == moves[-1]
    stream.close()
    assert metrics["successors"] == 1
    assert metrics["lazy_successors_skipped"] == len(moves) - 1


def test_recursion_cannot_reorder_suspended_priority_groups():
    state = new_game("brandubh")
    moves = legal_moves(state)
    history = {moves[4]: 8, moves[7]: 4}
    eager_rng, lazy_rng = random.Random(23), random.Random(23)
    expected = ai._order_children(
        ai._ordered(state, moves.copy(), eager_rng, maiden=True),
        strategy="full",
        remaining=3,
        tt_move=moves[-1],
        pv_move=None,
        killers=(),
        history=history,
    )
    stream = ai._lazy_ordered(
        state,
        moves.copy(),
        lazy_rng,
        ai._search_settings({}, False),
        3,
        moves[-1],
        (),
        history,
        True,
        lambda: None,
        counters(),
        False,
    )
    assert next(stream) == expected[0]
    history[moves[9]] = 100000
    eager_rng.random()
    lazy_rng.random()
    assert list(stream) == expected[1:]
    assert lazy_rng.getstate() == eager_rng.getstate()


@pytest.mark.parametrize("case", CORRECTNESS_POSITIONS[:7], ids=lambda case: case[0])
@pytest.mark.parametrize(
    "search",
    [{}, {"transposition": False}, {"symmetry_transposition": True, "symmetry_pruning": True}],
)
def test_fixed_depth_matches_eager_move_value_and_node_count(case, search):
    _, state, depth = case
    config = {
        "algorithm": "alpha_beta",
        "depth": depth,
        "time_limit": 300,
        "seed": 19,
        "search": deepcopy(search),
    }
    eager, actual = {}, {}
    expected = ai.choose_move(state, {**config, "_lazy_successors": False}, diagnostics=eager)
    move = ai.choose_move(state, config, diagnostics=actual)
    assert move == expected
    assert actual["value"] == eager["value"]
    assert actual["completed_depth"] == eager["completed_depth"] == depth
    assert actual["nodes"] == eager["nodes"]
    assert actual["successors"] <= eager["successors"]


def test_deadline_and_cancellation_keep_legal_search_contract():
    state = new_game("brandubh")
    move = ai.choose_move(state, {"algorithm": "alpha_beta", "depth": 8, "time_limit": 0.001})
    assert move in legal_moves(state)
    with pytest.raises(InterruptedError):
        ai.choose_move(state, {"algorithm": "alpha_beta"}, should_stop=lambda: True)


@pytest.mark.parametrize("value", [0, "yes", None])
def test_internal_ablation_flag_requires_boolean(value):
    with pytest.raises(ValueError):
        ai.choose_move(new_game("brandubh"), {"algorithm": "alpha_beta", "_lazy_successors": value})
