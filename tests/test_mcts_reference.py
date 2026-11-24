"""Independent native reference checks for the browser neural MCTS driver.

Level A/B compare the same new driver across two runtimes, so they cannot detect a
defect shared by both. These tests drive the production ``mcts_context`` and
``mcts_policy`` paths directly and require the new ``NeuralSearch`` driver to
reproduce them exactly for deterministic evaluations. Trained-model numerical
differences remain covered by the separate Level B suite.
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
from pathlib import Path

import pytest

SPIKE = Path(__file__).resolve().parents[1] / "spikes/browser_offline"
if str(SPIKE) not in sys.path:
    sys.path.insert(0, str(SPIKE))

import mcts_vectors  # noqa: E402
from hnefatafl import ai  # noqa: E402
from hnefatafl.engine import Move, State, apply_move, legal_moves, new_game  # noqa: E402
from hnefatafl.encoding import encode_planes, legal_action_indices  # noqa: E402
from hnefatafl.mcts_driver import (  # noqa: E402
    NeuralSearch,
    neural_input_identity,
    validate_search_settings,
)

SETTINGS = {"iterations": 40, "time_limit": 10.0, "seed": 11, "exploration": 1.4}


def _model(state: State) -> dict:
    return {"sha256": "a" * 64, "ruleset": state.ruleset, "size": state.size}


def _tree_rows(root) -> list:
    rows = []

    def visit(node, path):
        rows.append([path, node.visits, node.total, node.prior])
        for move, child in node.children.items():
            visit(child, path + [[move.source, move.target]])

    visit(root, [])
    return rows


def _digest(rows) -> str:
    return hashlib.sha256(json.dumps(rows).encode()).hexdigest()


def _reference(state: State, settings: dict) -> dict:
    """Drive production ``mcts_context`` directly with the deterministic evaluator."""
    diagnostics: dict = {}
    context = ai.mcts_context(state, settings, diagnostics=diagnostics, neural=True)
    trace = []
    index = 0
    try:
        pending = next(context)
        while True:
            requested = State.from_dict(pending.to_dict())
            priors, value = mcts_vectors.evaluate(requested)
            moves = legal_moves(requested)
            trace.append(
                {
                    "request_index": index,
                    "position_digest": neural_input_identity(
                        requested,
                        encode_planes(requested),
                        legal_action_indices(requested, moves),
                    ),
                    "legal_count": len(moves),
                    "value": value,
                }
            )
            index += 1
            try:
                pending = context.send((dict(zip(moves, priors)), value))
            except StopIteration as finished:
                return {
                    "diagnostics": diagnostics,
                    "policy": finished.value,
                    "trace": trace,
                }
    finally:
        context.close()


@pytest.mark.parametrize("ruleset", ["brandubh", "tablut", "copenhagen"])
def test_driver_reproduces_production_generator_exactly(ruleset, monkeypatch):
    state = new_game(ruleset)
    settings = validate_search_settings(SETTINGS)
    nodes: list = []
    factory = ai._Node

    def capture(*args, **kwargs):
        node = factory(*args, **kwargs)
        nodes.append(node)
        return node

    monkeypatch.setattr(ai, "_Node", capture)
    reference = _reference(state, dict(settings))
    reference_tree = _digest(_tree_rows(nodes[0]))
    nodes.clear()

    event = mcts_vectors.drive(state, settings, _model(state), mcts_vectors.evaluate, trace=True)
    driven_tree = _digest(_tree_rows(nodes[0]))
    search = event["search"]
    assert driven_tree == reference_tree
    assert search["trace"] == reference["trace"]
    assert search["trace_length"] == len(reference["trace"])
    assert search["completed_simulations"] == reference["diagnostics"]["simulations"]
    assert search["search_value"] == reference["diagnostics"]["value"]
    assert search["neural_requests"] == reference["diagnostics"]["mcts_neural_requests"]
    assert search["prediction_reuses"] == reference["diagnostics"]["mcts_node_prediction_reuses"]
    expected = sorted(
        ((move.source, move.target, share) for move, share in reference["policy"].items()),
        key=lambda row: (-row[2], row[0], row[1]),
    )
    observed = [(row["source"], row["target"], row["share"]) for row in search["root_visits"]]
    assert observed == expected
    best = max(reference["policy"], key=lambda move: reference["policy"][move])
    assert event["analysis"]["best_move"] == best.to_dict()


@pytest.mark.parametrize("ruleset", ["brandubh", "tablut", "copenhagen"])
def test_best_move_and_value_match_production_policy(ruleset):
    state = new_game(ruleset)
    settings = validate_search_settings(SETTINGS)
    reference = _reference(state, dict(settings))
    event = mcts_vectors.run_search(state.to_dict(), SETTINGS, trace=False)
    expected_move = max(reference["policy"], key=lambda move: reference["policy"][move])
    assert event["analysis"]["best_move"] == expected_move.to_dict()
    assert event["search"]["best_move"] == {
        "source": expected_move.source,
        "target": expected_move.target,
    }
    assert event["analysis"]["simulations"] == reference["diagnostics"]["simulations"]
    assert event["analysis"]["value"] == reference["diagnostics"]["value"]


def _immediate_win_positions() -> list[State]:
    size = 7
    board = ["."] * (size * size)
    board[0 * size + 1] = "K"
    board[0 * size + 3] = "A"
    board[3 * size + 3] = "D"
    board[5 * size + 5] = "A"
    attacker = State("brandubh", tuple(board), "attackers")
    board = ["."] * (size * size)
    board[0 * size + 2] = "K"
    board[4 * size + 4] = "A"
    defender = State("brandubh", tuple(board), "defenders")
    return [attacker, defender]


@pytest.mark.parametrize("state", _immediate_win_positions())
def test_immediate_win_shortcut_matches_production_policy(state):
    settings = validate_search_settings(SETTINGS)
    reference = _reference(state, dict(settings))
    expected_move = max(reference["policy"], key=lambda move: reference["policy"][move])
    assert reference["diagnostics"]["immediate_win_shortcuts"] == 1
    event = mcts_vectors.run_search(state.to_dict(), SETTINGS, trace=False)
    assert event["search"]["completion_reason"] == "immediate_win"
    assert event["analysis"]["best_move"] == expected_move.to_dict()
    assert event["analysis"]["value"] == reference["diagnostics"]["value"]
    assert event["search"]["completed_simulations"] == 0


def test_terminal_analysis_matches_production_result():
    escape = State(
        "brandubh",
        tuple("K" if index == 2 else "A" if index == 24 else "." for index in range(49)),
        "defenders",
    )
    terminal = apply_move(escape, Move(2, 0))
    assert terminal.winner == "defenders"
    production = ai.analyze_position(terminal, {"algorithm": "mcts", "iterations": 8})
    event = mcts_vectors.run_search(terminal.to_dict(), {"iterations": 8, "time_limit": 10})
    assert production == event["analysis"]
    assert event["search"]["neural_requests"] == 0


def test_driver_matches_production_mcts_policy_with_a_real_model():
    torch = pytest.importorskip("torch")
    from hnefatafl.models import PolicyValueNet, encode_state

    torch.manual_seed(7)
    model = PolicyValueNet(ruleset="brandubh", channels=4, blocks=0)
    model.eval()
    state = new_game("brandubh")
    settings = {"iterations": 24, "time_limit": 10.0, "seed": 3, "exploration": 1.4}

    # Production path: mcts_policy -> policy_value -> the real network.
    diagnostics: dict = {}
    production = ai.mcts_policy(state, {**settings, "_model": model}, diagnostics=diagnostics)

    # New driver: the same network evaluated through the external evaluator seam.
    def evaluator(requested: State):
        with torch.inference_mode():
            logits, value = model(encode_state(requested).unsqueeze(0))
        moves = legal_moves(requested)
        area = requested.size**2
        ids = [move.source * area + move.target for move in moves]
        probabilities = torch.softmax(logits[0, ids], dim=0).tolist()
        return probabilities, float(value.reshape(-1)[0])

    event = mcts_vectors.drive(state, settings, _model(state), evaluator, trace=False)
    assert event["kind"] == "complete"
    assert event["search"]["completed_simulations"] == diagnostics["simulations"]
    assert event["search"]["search_value"] == pytest.approx(diagnostics["value"], abs=1e-12)
    assert event["search"]["neural_requests"] == diagnostics["mcts_neural_requests"]
    assert event["search"]["prediction_reuses"] == diagnostics["mcts_node_prediction_reuses"]
    ranked = sorted(production.items(), key=lambda item: (-item[1], item[0]))
    observed = [(row["source"], row["target"]) for row in event["search"]["root_visits"]]
    assert observed == [(move.source, move.target) for move, _ in ranked]
    for (move, share), row in zip(ranked, event["search"]["root_visits"]):
        assert row["share"] == pytest.approx(share, abs=1e-12)
    best = ranked[0][0]
    assert event["analysis"]["best_move"] == best.to_dict()


def test_cancellation_does_not_report_deadline_and_retains_completed_work():
    state = new_game("tablut")
    calls = {"count": 0}

    def should_stop():
        calls["count"] += 1
        return calls["count"] > 6

    search = NeuralSearch(
        state,
        validate_search_settings(SETTINGS),
        _model(state),
        should_stop=should_stop,
    )
    event = search.start()
    while event["kind"] == "evaluate":
        request = event["request"]
        priors = [1.0 / len(request["legal"])] * len(request["legal"])
        event = search.resume(
            {
                "request_index": request["request_index"],
                "legal": request["legal"],
                "priors": priors,
                "value": 0.0,
            }
        )
    assert event["kind"] == "cancelled"
    assert event["reason"] == "cancelled"
    assert event["search"]["deadline_cutoff"] is False
    assert event["search"]["completed_simulations"] >= 0
    search.close()


def test_completed_simulation_accounting_is_exact():
    state = new_game("brandubh")
    for budget in (1, 8, 33):
        event = mcts_vectors.run_search(
            state.to_dict(), {"iterations": budget, "time_limit": 10, "seed": 5}
        )
        assert event["search"]["completed_simulations"] == budget
        assert event["analysis"]["simulations"] == budget
        assert sum(row["visits"] for row in event["search"]["root_visits"]) == budget
        assert math.isclose(
            sum(row["share"] for row in event["search"]["root_visits"]), 1.0, abs_tol=1e-12
        )
