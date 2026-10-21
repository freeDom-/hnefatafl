import json
import subprocess
import sys
from pathlib import Path

import pytest
import torch

from hnefatafl.ai import choose_move, play_game
from hnefatafl.engine import Move, legal_moves, new_game, replay
from hnefatafl.models import ModelRegistry, PolicyValueNet, load_checkpoint
from hnefatafl.training import evaluate_model, normalize_config, train, tune
from hnefatafl.training_presets import training_presets


def smoke_config(tmp_path, **changes):
    return {
        "preset": "smoke",
        "device": "cpu",
        "seed": 0,
        "output_dir": str(tmp_path / "training"),
        "registry_dir": str(tmp_path / "models"),
        **changes,
    }


@pytest.mark.parametrize("ruleset", ["brandubh", "tablut", "copenhagen"])
def test_actual_selfplay_update_checkpoint_reload_registry_and_legal_ai(tmp_path, ruleset):
    torch.set_num_threads(1)
    torch.manual_seed(0)
    initial = PolicyValueNet(ruleset, channels=4, blocks=0)
    before = {key: value.clone() for key, value in initial.state_dict().items()}
    progress = []
    result = train(smoke_config(tmp_path, ruleset=ruleset), progress.append)
    assert result["status"] == "completed"
    assert result["metrics"]["updates"] > 0
    assert result["games_generated"] == 1
    assert result["metrics"]["replay_positions"] > 0
    assert result["metrics"]["policy_loss"] > 0
    model, payload = load_checkpoint(result["checkpoint"])
    assert any(not torch.equal(before[key], value) for key, value in model.state_dict().items())
    assert payload["optimizer_state"]
    assert payload["extra"]["iteration"] == 1
    assert len(payload["extra"]["replay"]) > 0
    assert payload["extra"]["torch_rng"].dtype == torch.uint8
    registry = ModelRegistry(tmp_path / "models")
    assert registry.get(result["model"]["id"])["ruleset"] == ruleset
    imported = ModelRegistry(tmp_path / "imported").import_model(
        result["checkpoint"], "Imported learner"
    )
    state = new_game(ruleset)
    assert choose_move(
        state, {"algorithm": "neural", "model_path": imported["path"], "iterations": 2}
    ) in legal_moves(state)
    evaluation = result["metrics"]["evaluation"]
    assert (
        evaluation["wins"] + evaluation["losses"] + evaluation["draws"] + evaluation["truncated"]
        == 2
    )
    assert [row["candidate_side"] for row in evaluation["games"]] == ["attackers", "defenders"]
    assert any(row["stage"] == "self_play" for row in progress)
    assert json.loads((tmp_path / "training" / "result.json").read_text())["status"] == "completed"


@pytest.mark.parametrize("ruleset", ["brandubh", "tablut", "copenhagen"])
def test_trained_registered_model_plays_complete_game_to_engine_terminal(tmp_path, ruleset):
    result = train(smoke_config(tmp_path, ruleset=ruleset))
    played = play_game(
        ruleset,
        {
            "algorithm": "neural",
            "model_path": result["model"]["path"],
            "iterations": 2,
            "rollout_depth": 0,
        },
        {"algorithm": "baseline"},
        seed=4,
        max_plies=400,
    )
    assert played["outcome"] == "completed", played
    assert played["reason"] != "experiment_ply_limit"
    reconstructed = replay(ruleset, [Move(**move) for move in played["moves"]])
    assert reconstructed.winner == played["winner"]


def test_checkpoint_resume_matches_uninterrupted_training_weights(tmp_path):
    first = train(smoke_config(tmp_path / "split"))
    resumed = train(smoke_config(tmp_path / "resumed", iterations=2, resume=first["checkpoint"]))
    full = train(smoke_config(tmp_path / "full", iterations=2))
    resumed_model, payload = load_checkpoint(resumed["checkpoint"])
    full_model, _ = load_checkpoint(full["checkpoint"])
    assert resumed["iteration"] == 2
    assert resumed["games_generated"] == 2
    assert payload["extra"]["iteration"] == 2
    for key, value in resumed_model.state_dict().items():
        torch.testing.assert_close(value, full_model.state_dict()[key], rtol=0, atol=0)


def test_resume_preserves_portable_best_when_later_iterations_are_weaker(tmp_path, monkeypatch):
    # Controlled nonmonotonic scores isolate best-model bookkeeping from play.
    scores = iter([1.0, 0.0, 0.0])

    def evaluate(model, config, **kwargs):
        score = 0.0 if kwargs.get("previous") is not None else next(scores)
        return {
            "opponent": "previous_model" if kwargs.get("previous") is not None else "baseline",
            "score": score,
            "wins": int(2 * score),
            "losses": int(2 * (1 - score)),
            "draws": 0,
            "truncated": 0,
            "games": [],
        }

    monkeypatch.setattr("hnefatafl.training.evaluate_model", evaluate)
    first = train(smoke_config(tmp_path / "first", iterations=2))
    original_best, original_payload = load_checkpoint(first["best_checkpoint"])
    assert original_payload["metadata"]["version"] == 1
    assert original_payload["optimizer_state"] is None
    _, latest_payload = load_checkpoint(first["checkpoint"])
    assert latest_payload["metadata"]["version"] == 2
    assert latest_payload["extra"]["best_score"] == 1
    assert latest_payload["extra"]["best_snapshot"]["metadata"]["version"] == 1
    # Simulate moving only last.pt to a new host; no sibling best.pt is read.
    portable = tmp_path / "portable-last.pt"
    portable.write_bytes(Path(first["checkpoint"]).read_bytes())
    Path(first["best_checkpoint"]).unlink()
    resumed = train(smoke_config(tmp_path / "resumed", iterations=3, resume=str(portable)))
    restored_best, best_payload = load_checkpoint(resumed["best_checkpoint"])
    registered_best, registered_payload = load_checkpoint(resumed["model"]["path"])
    assert best_payload["metadata"]["version"] == registered_payload["metadata"]["version"] == 1
    assert "previous_evaluation" in resumed["metrics"]
    for key, value in original_best.state_dict().items():
        torch.testing.assert_close(restored_best.state_dict()[key], value, rtol=0, atol=0)
        torch.testing.assert_close(registered_best.state_dict()[key], value, rtol=0, atol=0)


def test_resume_rejects_incompatible_architecture_or_completed_target(tmp_path):
    first = train(smoke_config(tmp_path))
    with pytest.raises(ValueError, match="architecture"):
        train(
            smoke_config(tmp_path / "other", channels=8, resume=first["checkpoint"], iterations=2)
        )
    with pytest.raises(ValueError, match="exceed"):
        train(smoke_config(tmp_path / "same", resume=first["checkpoint"]))


def test_cancellation_saves_safe_resumable_checkpoint(tmp_path):
    result = train(smoke_config(tmp_path), should_stop=lambda: True)
    assert result["status"] == "cancelled"
    _, payload = load_checkpoint(result["checkpoint"])
    assert payload["extra"]["iteration"] == 0
    resumed = train(smoke_config(tmp_path / "resumed", resume=result["checkpoint"]))
    assert resumed["status"] == "completed"


def test_evaluation_keeps_unfinished_games_separate_from_draws(tmp_path):
    torch.set_num_threads(1)
    model = PolicyValueNet("brandubh", channels=4, blocks=0)
    config = normalize_config(smoke_config(tmp_path, evaluation_max_plies=2))
    result = evaluate_model(model, config)
    assert result["truncated"] == 2
    assert result["draws"] == result["wins"] == result["losses"] == 0
    assert result["score"] == 0
    assert all(row["winner"] is None for row in result["games"])


def test_optuna_real_trials_resume_study_and_save_best_config(tmp_path):
    config = {
        "output_dir": str(tmp_path / "study"),
        "study_name": "smoke",
        "trials": 2,
        "seed": 0,
        "training": {"preset": "smoke", "device": "cpu"},
        "search_space": {"learning_rate": {"type": "float", "low": 0.001, "high": 0.002}},
    }
    first = tune(config)
    assert first["status"] == "completed"
    assert first["completed_trials"] == 2
    assert len(first["trials"]) == 2
    assert first["best_configuration"]["learning_rate"] in [
        trial["parameters"]["learning_rate"] for trial in first["trials"]
    ]
    best = json.loads((tmp_path / "study" / "best-config.json").read_text())
    assert best["learning_rate"] == first["best_parameters"]["learning_rate"]
    second = tune({**config, "trials": 1})
    assert second["completed_trials"] == 3
    assert len(second["trials"]) == 3
    assert all(trial["state"] == "COMPLETE" for trial in second["trials"])


@pytest.mark.parametrize(
    "changes",
    [
        {"games": 0},
        {"batch_size": -1},
        {"evaluation_games": 1},
        {"learning_rate": float("nan")},
        {"optimizer": "invalid"},
        {"mcts_iterations": 0},
        {"unknown": True},
        {"save_all_best": "true"},
    ],
)
def test_training_configuration_rejects_invalid_parameters(tmp_path, changes):
    with pytest.raises(ValueError):
        normalize_config(smoke_config(tmp_path, **changes))


def test_cli_real_training_smoke(tmp_path):
    config = tmp_path / "cli.json"
    config.write_text(json.dumps(smoke_config(tmp_path)))
    process = subprocess.run(
        [sys.executable, "-m", "hnefatafl.cli", "train", "--config", str(config)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert process.returncode == 0, process.stderr
    result = json.loads((tmp_path / "training" / "result.json").read_text())
    assert result["status"] == "completed"
    assert result["metrics"]["updates"] > 0


@pytest.mark.parametrize("ruleset,size", [("brandubh", 7), ("tablut", 9), ("copenhagen", 11)])
def test_recommended_defaults_cover_board_and_keep_explicit_overrides(tmp_path, ruleset, size):
    config = normalize_config({"ruleset": ruleset})
    assert config["preset"] == "recommended"
    assert 3 + 4 * config["blocks"] >= size
    assert config["evaluation_games"] >= 12 and config["evaluation_games"] % 2 == 0
    assert config["evaluation_max_plies"] >= config["max_game_plies"] >= 200
    assert config["save_all_best"] is False
    overridden = normalize_config({"ruleset": ruleset, "channels": 8, "games": 3})
    assert overridden["channels"] == 8 and overridden["games"] == 3
    smoke = normalize_config(smoke_config(tmp_path, ruleset=ruleset))
    assert smoke["channels"] == 4 and smoke["iterations"] == 1
    assert smoke["max_game_plies"] == 12
    plans = training_presets()
    next(row for row in plans if row["ruleset"] == ruleset)["config"]["channels"] = 99
    assert (
        next(row for row in training_presets() if row["ruleset"] == ruleset)["config"]["channels"]
        == config["channels"]
    )


def test_best_model_retention_and_history_survive_resume(tmp_path, monkeypatch):
    scores = iter([0.25, 0.5, 0.5, 0.75])

    def evaluated(model, config, **kwargs):
        score = 0.0 if kwargs.get("previous") is not None else next(scores)
        return {
            "opponent": "previous_model" if kwargs.get("previous") is not None else "baseline",
            "score": score,
            "wins": int(4 * score),
            "losses": int(4 * (1 - score)),
            "draws": 0,
            "truncated": 0,
            "games": [],
        }

    monkeypatch.setattr("hnefatafl.training.evaluate_model", evaluated)
    observed = []
    first = train(
        smoke_config(tmp_path / "first", iterations=3, save_all_best=True),
        progress=lambda state: observed.append(
            (state["status"], state.get("stage"), len(state["history"]))
        ),
    )
    assert ("running", "iteration_complete", 1) in observed
    assert ("running", "iteration_complete", 2) in observed
    assert [row["iteration"] for row in first["best_models"]] == [1, 2]
    assert [row["evaluation_score"] for row in first["history"]] == [0.25, 0.5, 0.5]
    assert all(row["policy_loss"] > 0 and row["value_loss"] >= 0 for row in first["history"])
    for row in first["best_models"]:
        _, payload = load_checkpoint(row["checkpoint"])
        assert payload["metadata"]["version"] == row["iteration"]
        assert payload["metadata"]["evaluation"]["score"] == row["score"]
        assert ModelRegistry(tmp_path / "first" / "models").get(row["model_id"])
    resumed = train(
        smoke_config(
            tmp_path / "resumed", iterations=4, save_all_best=True, resume=first["checkpoint"]
        )
    )
    assert [row["iteration"] for row in resumed["history"]] == [1, 2, 3, 4]
    assert [row["iteration"] for row in resumed["best_models"]] == [1, 2, 4]
    _, payload = load_checkpoint(resumed["checkpoint"])
    assert payload["extra"]["history"] == resumed["history"]
    assert payload["extra"]["best_models"] == resumed["best_models"]
    assert first["best_models"][0]["model_id"] != first["best_models"][1]["model_id"]
