"""Independent cooperative searches retain the synchronous PUCT semantics."""

from dataclasses import replace

import pytest

from hnefatafl.ai import mcts_context, mcts_policy, policy_value
from hnefatafl.engine import State, apply_move, legal_moves, new_game
from hnefatafl.inference import InferenceMetrics
from hnefatafl.models import PolicyValueNet


def drive(context, model=None):
    requested = []
    try:
        pending = next(context)
        while True:
            requested.append(pending)
            pending = context.send(policy_value(pending, model=model))
    except StopIteration as done:
        return done.value, requested


@pytest.mark.parametrize("neural", [False, True])
def test_context_matches_reference_search_and_retains_completed_budget(neural):
    state = new_game("brandubh")
    model = PolicyValueNet("brandubh", 4, 0).eval() if neural else None
    config = {
        "iterations": 12,
        "time_limit": 30,
        "seed": 41,
        "rollout_depth": 2,
        "dirichlet_fraction": 0.25,
        "dirichlet_alpha": 0.3,
    }
    if model is not None:
        config["_model"] = model
    diagnostics = {}
    actual, requested = drive(mcts_context(state, config, diagnostics=diagnostics), model)
    expected = mcts_policy(state, config)
    assert actual == expected
    assert diagnostics["simulations"] == 12
    assert requested and all(not position.winner for position in requested)
    assert all(move in legal_moves(state) for move in actual)
    assert sum(actual.values()) == pytest.approx(1)


def test_interleaved_searches_have_independent_rng_and_dependent_simulations():
    state = new_game("brandubh")
    configs = [
        {"iterations": count, "seed": seed, "time_limit": 30, "rollout_depth": 1}
        for count, seed in ((5, 2), (9, 10))
    ]
    diagnostics = [{}, {}]
    contexts = [
        mcts_context(state, config, diagnostics=details)
        for config, details in zip(configs, diagnostics)
    ]
    pending = [next(context) for context in contexts]
    completed = {}
    # Yielding a request has performed no dependent simulation or backpropagation.
    assert diagnostics == [{}, {}]
    for index in (1, 0, 0, 1) * 20:
        if index in completed:
            continue
        try:
            pending[index] = contexts[index].send(policy_value(pending[index]))
        except StopIteration as done:
            completed[index] = done.value
    assert len(completed) == 2
    for index, config in enumerate(configs):
        assert completed[index] == mcts_policy(state, config)
        assert diagnostics[index]["simulations"] == config["iterations"]


def test_context_uses_exact_terminal_return_without_terminal_inference():
    board = ["."] * 49
    board[1], board[24], board[8], board[48] = "K", "D", "A", "A"
    state = State("brandubh", tuple(board), turn="defenders")
    diagnostics = {}
    policy, requested = drive(mcts_context(state, {"iterations": 1}, diagnostics=diagnostics))
    assert requested == [state]
    assert len(policy) == 1
    assert apply_move(state, next(iter(policy))).winner == "defenders"
    assert diagnostics["value"] == 1 and diagnostics["simulations"] == 0
    with pytest.raises(ValueError, match="terminal"):
        next(mcts_context(replace(state, winner="defenders")))


def test_context_cancellation_does_not_advance_outstanding_simulation():
    state = new_game("brandubh")
    cancelled = False
    context = mcts_context(state, {"iterations": 8}, should_stop=lambda: cancelled)
    pending = next(context)
    cancelled = True
    with pytest.raises(InterruptedError, match="cancelled"):
        context.send(policy_value(pending))
    with pytest.raises(InterruptedError, match="cancelled"):
        mcts_policy(state, should_stop=lambda: True)


def test_sequential_driver_collects_inference_metrics_without_changing_policy():
    state = new_game("brandubh")
    model = PolicyValueNet("brandubh", 4, 0).eval()
    config = {"iterations": 4, "time_limit": 30, "seed": 29, "_model": model}
    metrics = InferenceMetrics()
    actual = mcts_policy(state, config, inference_metrics=metrics)
    assert actual == mcts_policy(state, config)
    measured = metrics.snapshot()
    assert measured["inference_batches"] >= 5
    assert measured["inference_batch_size_min"] == measured["inference_batch_size_max"] == 1
