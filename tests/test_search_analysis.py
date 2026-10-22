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


def test_neural_analysis_uses_searched_values_from_validated_checkpoint(tmp_path):
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
    assert result["source"] == "neural_search"
    assert result["value"] == pytest.approx(0.6)
    assert result["simulations"] == 3
    assert Move(**result["best_move"]) in legal_moves(state)


def test_profile_depth_defaults_and_explicit_overrides_match_play_and_analysis(monkeypatch):
    profiles = ai.ai_profiles()
    profile = next(profile for profile in profiles if profile["id"] == "alpha_beta")
    profile["parameters"] = {"depth": 1, "time_limit": 15}
    monkeypatch.setattr(ai, "ai_profiles", lambda: profiles)
    state = new_game("brandubh")
    choices = []
    for explicit, expected_depth in (({}, 1), ({"depth": 2}, 2)):
        configuration = {"profile_id": "alpha_beta", "seed": 0, **explicit}
        diagnostics = {}
        selected = ai.choose_move(state, configuration, diagnostics=diagnostics)
        report = ai.analyze_position(state, configuration)
        assert diagnostics["completed_depth"] == report["completed_depth"] == expected_depth
        assert selected.to_dict() == report["best_move"]
        assert report["value"] == diagnostics["value"]
        assert configuration == {"profile_id": "alpha_beta", "seed": 0, **explicit}
        choices.append(selected)
    assert choices[0] != choices[1]


@pytest.mark.parametrize("blocks", [0, 3])
def test_neural_search_loads_saved_network_depth_and_reports_discovered_win(tmp_path, blocks):
    model = PolicyValueNet("brandubh", channels=4, blocks=blocks)
    with torch.no_grad():
        for parameter in model.parameters():
            parameter.zero_()
        model.value_head[-2].bias.fill_(math.atanh(-0.6))
    path = save_checkpoint(tmp_path / "agent.pt", model, {})
    board = ["."] * 49
    board[1], board[24], board[8], board[48] = "K", "D", "A", "A"
    state = State("brandubh", tuple(board), turn="defenders")
    config = {"algorithm": "neural", "model_path": str(path), "iterations": 3, "seed": 8}
    assert ai.policy_value(state, str(path))[1] == pytest.approx(-0.6)
    assert ai._loaded_model(str(path)).blocks == blocks
    report = ai.analyze_position(state, config)
    selected = ai.choose_move(state, config)
    assert apply_move(state, selected).winner == "defenders"
    assert selected.to_dict() == report["best_move"]
    assert report["source"] == "neural_search" and report["value"] == 1


def test_neural_analysis_returns_completed_search_average_instead_of_unsearched_root(monkeypatch):
    state = new_game("brandubh")
    seen = []

    def policy_value(position, *_args):
        seen.append(position.ply)
        moves = legal_moves(position)
        return {move: 1 / len(moves) for move in moves}, -0.5 if position.ply == 0 else 0.6

    monkeypatch.setattr(ai, "policy_value", policy_value)
    configuration = {
        "algorithm": "neural",
        "_model": object(),
        "iterations": 7,
        "time_limit": 15,
        "seed": 8,
    }
    report = ai.analyze_position(state, configuration)
    assert report["simulations"] == 7
    assert report["value"] == pytest.approx(0.6)
    assert report["source"] == "neural_search"
    assert len([ply for ply in seen if ply > 0]) >= 7


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


def test_named_search_profiles_have_fixed_play_budgets():
    profiles = {profile["id"]: profile["parameters"] for profile in ai.ai_profiles()}
    assert profiles["alpha_beta"] == {"depth": 5, "time_limit": 2.0}
    assert profiles["mcts"] == {
        "iterations": 5000,
        "temperature": 0.0,
        "exploration": 1.4,
        "rollout_depth": 8,
        "time_limit": 2.0,
    }


def test_alpha_beta_publishes_completed_depths_without_restarting_search(monkeypatch):
    state = new_game("brandubh")
    configuration = {"algorithm": "alpha_beta", "depth": 2, "time_limit": 15, "seed": 9}
    root_calls = 0
    ordered = ai._ordered

    def count_root(position, moves, rng):
        nonlocal root_calls
        if position is state:
            root_calls += 1
        return ordered(position, moves, rng)

    monkeypatch.setattr(ai, "_ordered", count_root)
    snapshots = []
    final = ai.analyze_position(state, configuration, progress=snapshots.append)
    assert root_calls == 2
    assert [snapshot["completed_depth"] for snapshot in snapshots] == [1, 2]
    assert snapshots[-1] == final
    assert all(Move(**snapshot["best_move"]) in legal_moves(state) for snapshot in snapshots)
    snapshots[-1]["best_move"]["source"] = -1
    assert final["best_move"]["source"] >= 0


@pytest.mark.parametrize("algorithm", ["baseline", "alpha_beta", "mcts"])
def test_actual_move_diagnostics_are_reusable_without_any_second_search(algorithm, monkeypatch):
    state = new_game("brandubh")
    config = {
        "algorithm": algorithm,
        "depth": 1,
        "iterations": 4,
        "rollout_depth": 1,
        "time_limit": 15,
        "temperature": 1.5,
        "seed": 33,
    }
    diagnostics = {}
    snapshots = []
    selected = ai.choose_move(state, config, diagnostics=diagnostics, progress=snapshots.append)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Converting retained search data must not evaluate or search")

    monkeypatch.setattr(ai, "choose_move", forbidden)
    monkeypatch.setattr(ai, "evaluate", forbidden)
    monkeypatch.setattr(ai, "policy_value", forbidden)
    report = ai.analysis_from_search(state, config, selected, diagnostics)
    assert report["value"] == diagnostics["value"]
    assert report["best_move"] == selected.to_dict() == diagnostics["best_move"]
    assert snapshots[-1] == report
    assert config["temperature"] == 1.5


def test_mcts_publishes_intermediate_and_final_values_of_the_same_search():
    state = new_game("brandubh")
    snapshots = []
    final = ai.analyze_position(
        state,
        {"algorithm": "mcts", "iterations": 8, "rollout_depth": 0, "time_limit": 15},
        progress=snapshots.append,
    )
    assert snapshots[0]["simulations"] == 1
    assert snapshots[-1]["simulations"] == 8
    assert len(snapshots) >= 2
    assert [item["simulations"] for item in snapshots] == sorted(
        {item["simulations"] for item in snapshots}
    )
    assert snapshots[-1] == final
    assert all(snapshot["source"] == "mcts_rollout" for snapshot in snapshots)


def test_direct_mcts_policy_observer_identifies_its_search_source():
    state = new_game("brandubh")
    snapshots = []
    probabilities = ai.mcts_policy(
        state, {"iterations": 2, "rollout_depth": 0}, progress=snapshots.append
    )
    assert snapshots[-1]["source"] == "mcts_rollout"
    assert snapshots[-1]["best_move"] == max(probabilities, key=probabilities.get).to_dict()


def test_background_search_can_be_cancelled_by_its_progress_observer():
    state = new_game("brandubh")
    observations = []

    def cancel(snapshot):
        observations.append(snapshot["completed_depth"])
        raise InterruptedError("Analysis settings changed")

    with pytest.raises(InterruptedError, match="Analysis settings changed"):
        ai.analyze_position(
            state,
            {"algorithm": "alpha_beta", "depth": 5, "time_limit": 15},
            progress=cancel,
        )
    assert observations == [1]


def test_expired_alpha_beta_still_publishes_legal_fallback(monkeypatch):
    def expired(*_args):
        raise ai._Deadline

    monkeypatch.setattr(ai, "_ordered", expired)
    snapshots = []
    result = ai.analyze_position(
        new_game("brandubh"), {"algorithm": "alpha_beta"}, progress=snapshots.append
    )
    assert snapshots == [result]
    assert result["completed_depth"] == 0
