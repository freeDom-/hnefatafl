"""Analysis exposes actual search values while retaining legal seeded play."""

from dataclasses import replace
import math

import pytest
import torch

from hnefatafl import ai
from hnefatafl.engine import Move, State, apply_move, legal_moves, new_game
from hnefatafl.models import PolicyValueNet, save_checkpoint


@pytest.mark.parametrize("ruleset", ["brandubh", "tablut", "copenhagen"])
@pytest.mark.parametrize("algorithm", ["baseline", "alpha_beta", "mcts"])
def test_analysis_is_bounded_legal_and_does_not_mutate_position_or_configuration(
    ruleset, algorithm
):
    state = new_game(ruleset)
    original = state.to_dict()
    config = {
        "algorithm": algorithm,
        "depth": 1,
        "iterations": 4,
        "rollout_depth": 1,
        "time_limit": 1,
        "temperature": 0,
        "seed": 53,
    }
    result = ai.analyze_position(state, config)
    assert -1 <= result["value"] <= 1
    assert result["source"] == ("mcts_rollout" if algorithm == "mcts" else "heuristic")
    move = Move(**result["best_move"])
    assert move in legal_moves(state)
    assert move == ai.choose_move(state, config)
    assert state.to_dict() == original
    assert config["temperature"] == 0
    if algorithm == "mcts":
        assert result["simulations"] == 4
    else:
        assert result["completed_depth"] == 1


@pytest.mark.parametrize("turn", ["attackers", "defenders"])
def test_baseline_value_is_selected_child_evaluation_in_defender_perspective(turn):
    state = replace(new_game("brandubh"), turn=turn)
    result = ai.analyze_position(state, {"algorithm": "baseline", "time_limit": 1})
    after = apply_move(state, Move(**result["best_move"]))
    assert result["value"] == ai.evaluate(after)
    values = [ai.evaluate(apply_move(state, move)) for move in legal_moves(state)]
    assert result["value"] == (max(values) if turn == "defenders" else min(values))


def test_alpha_beta_keeps_last_completed_search_value_if_deeper_iteration_expires(monkeypatch):
    state = new_game("brandubh")
    config = {"algorithm": "alpha_beta", "depth": 1, "time_limit": 10, "seed": 9}
    expected = ai.analyze_position(state, config)
    ordered = ai._ordered
    root_calls = 0

    def expiry_at_second_root(position, moves, rng):
        nonlocal root_calls
        if position is state:
            root_calls += 1
            if root_calls == 2:
                raise ai._Deadline
        return ordered(position, moves, rng)

    monkeypatch.setattr(ai, "_ordered", expiry_at_second_root)
    result = ai.analyze_position(state, {**config, "depth": 4})
    assert root_calls == 2
    assert result == expected


def test_alpha_beta_reports_root_evaluator_if_no_depth_can_finish(monkeypatch):
    state = new_game("brandubh")

    def expired(*_args):
        raise ai._Deadline

    monkeypatch.setattr(ai, "_ordered", expired)
    result = ai.analyze_position(state, {"algorithm": "alpha_beta"})
    assert result["completed_depth"] == 0
    assert result["value"] == ai.evaluate(state)
    assert Move(**result["best_move"]) in legal_moves(state)


@pytest.mark.parametrize("algorithm", ["baseline", "alpha_beta", "mcts"])
def test_known_escape_is_recognized_by_each_agents_analysis(algorithm):
    board = ["."] * 49
    board[1], board[24], board[8], board[48] = "K", "D", "A", "A"
    state = State("brandubh", tuple(board), turn="defenders")
    result = ai.analyze_position(
        state, {"algorithm": algorithm, "depth": 1, "iterations": 1, "time_limit": 1}
    )
    assert result["value"] == 1
    assert apply_move(state, Move(**result["best_move"])).winner == "defenders"


@pytest.mark.parametrize("winner,value", [("attackers", -1), ("defenders", 1), ("draw", 0)])
def test_terminal_analysis_is_exact_and_never_attempts_to_load_a_model(winner, value):
    state = replace(new_game("brandubh"), winner=winner)
    result = ai.analyze_position(
        state, {"algorithm": "neural", "model_path": "/missing/deleted-model.pt"}
    )
    assert result == {"value": value, "source": "terminal", "best_move": None}


def test_neural_analysis_uses_actual_root_value_head_from_validated_checkpoint(tmp_path):
    model = PolicyValueNet("brandubh", channels=4, blocks=0)
    with torch.no_grad():
        for parameter in model.parameters():
            parameter.zero_()
        model.value_head[-2].bias.fill_(math.atanh(0.6))
    path = save_checkpoint(tmp_path / "agent.pt", model, {})
    state = new_game("brandubh")
    result = ai.analyze_position(
        state,
        {
            "algorithm": "neural",
            "model_path": str(path),
            "iterations": 3,
            "time_limit": 10,
            "seed": 17,
        },
    )
    assert result["source"] == "neural_value"
    assert result["value"] == pytest.approx(0.6)
    assert result["simulations"] == 3
    assert Move(**result["best_move"]) in legal_moves(state)


def test_search_suggestion_ignores_play_sampling_temperature_without_mutating_it():
    state = new_game("brandubh")
    config = {
        "algorithm": "mcts",
        "iterations": 8,
        "rollout_depth": 1,
        "time_limit": 10,
        "seed": 31,
        "temperature": 2.0,
    }
    result = ai.analyze_position(state, config)
    expected = ai.choose_move(state, {**config, "temperature": 0})
    assert result["best_move"] == expected.to_dict()
    assert config["temperature"] == 2.0
