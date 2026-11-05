"""CPU integration regressions for persistent, checkpoint-based tuning."""

from pathlib import Path
import hashlib
import json
import sqlite3

import optuna
import pytest
import torch
from auth_support import AuthenticatedClient as TestClient

from hnefatafl import training
from hnefatafl.models import ModelRegistry, PolicyValueNet, load_checkpoint, save_checkpoint
from hnefatafl.server import create_app
from hnefatafl.training_evaluation import evaluate_model
from hnefatafl.training_identity import checkpoint_identity, content_digest
from test_training_evaluation import match


def small(**changes):
    return dict(
        preset="smoke",
        device="cpu",
        cpu_threads=1,
        seed=19,
        mcts_time_limit=5,
        evaluation_time_limit=5,
        max_game_plies=2,
        evaluation_max_plies=2,
        **changes,
    )


def study_config(tmp_path, **changes):
    return dict(
        trials=1,
        study_name="Reliable",
        seed=13,
        search_space={"learning_rate": {"type": "categorical", "choices": [0.001]}},
        training=small(),
        output_dir=str(tmp_path / "study"),
        **changes,
    )


def test_distinct_checkpoint_contents_and_fixed_workloads_cannot_mix(tmp_path):
    source = training.train({**small(), "output_dir": str(tmp_path / "source")})
    config = study_config(tmp_path)
    config["training"].update(resume=source["checkpoint"], iterations=2)
    first = training.tune(config)
    path = Path(source["checkpoint"])
    _, payload = load_checkpoint(path)
    original = path.read_bytes()
    # Same iteration, seed and optimizer, different trained weights.
    changed = tmp_path / "changed.pt"
    next(iter(payload["state_dict"].values())).add_(0.1)
    torch.save(payload, changed)
    with pytest.raises(ValueError, match="source"):
        training.tune({**config, "training": {**config["training"], "resume": str(changed)}})
    for name, value in [
        ("iterations", 3),
        ("games", 2),
        ("epochs", 2),
        ("max_game_plies", 4),
        ("training_symmetry_mode", "off"),
    ]:
        with pytest.raises(ValueError, match=f"training.{name}"):
            training.tune({**config, "training": {**config["training"], name: value}})
    assert path.read_bytes() == original
    study = optuna.load_study(study_name="Reliable", storage=first["storage"])
    assert len(study.trials) == 1
    # A cosmetic change and additional trials remain compatible.
    result = training.tune({**config, "training": {**config["training"], "model_name": "Renamed"}})
    assert result["completed_trials"] == 2


def test_source_identity_cache_invalidates_after_checkpoint_replacement(tmp_path, monkeypatch):
    result = training.train({**small(), "output_dir": str(tmp_path / "source")})
    path = Path(result["checkpoint"])
    _, payload = load_checkpoint(path)
    first = checkpoint_identity(path, payload)
    monkeypatch.setattr(
        "hnefatafl.training_identity.source_identity", lambda payload: pytest.fail("cache missed")
    )
    assert checkpoint_identity(path, payload) == first
    monkeypatch.undo()
    payload["optimizer_state"]["param_groups"][0]["lr"] = 0.005
    torch.save(payload, path)
    second = checkpoint_identity(path, payload)
    assert first["weights_content"] == second["weights_content"]
    assert first["checkpoint_content"] != second["checkpoint_content"]


@pytest.mark.parametrize("identifiable", [False, True])
def test_legacy_studies_require_reliable_historical_identity(tmp_path, identifiable):
    config = study_config(tmp_path)
    result = training.tune(config)
    path = Path(result["storage"][10:])
    with sqlite3.connect(path) as db:
        db.execute("DELETE FROM study_user_attributes WHERE key='hnefatafl_identity'")
        if not identifiable:
            db.execute("DELETE FROM trial_user_attributes WHERE key='experiment_identity'")
    if identifiable:
        assert training.tune(config)["completed_trials"] == 2
    else:
        original = json.loads((tmp_path / "study" / "result.json").read_text())
        with pytest.raises(ValueError, match="read-only inspection and export"):
            training.tune(config)
        assert json.loads((tmp_path / "study" / "result.json").read_text()) == original
        assert len(optuna.load_study(study_name="Reliable", storage=result["storage"]).trials) == 1


def test_old_v1_fingerprint_is_not_upgraded_from_requested_settings(tmp_path):
    config = study_config(tmp_path)
    result = training.tune(config)
    study = optuna.load_study(study_name="Reliable", storage=result["storage"])
    study.set_user_attr("hnefatafl_identity", {"version": 1, "mode": "scratch"})
    with sqlite3.connect(result["storage"][10:]) as db:
        db.execute("DELETE FROM trial_user_attributes WHERE key='experiment_identity'")
    with pytest.raises(ValueError, match="Historical study compatibility"):
        training.tune(config)
    assert study.user_attrs["hnefatafl_identity"]["version"] == 1


def test_new_candidates_have_distinct_objectives_below_inherited_benchmark(tmp_path, monkeypatch):
    scores = iter([1.0, 0.25, 0.5])
    monkeypatch.setattr(
        training, "evaluate_model", lambda *args, **kwargs: match([next(scores)] * 2)
    )
    source = training.train({**small(), "output_dir": str(tmp_path / "source")})
    original = hashlib.sha256(Path(source["checkpoint"]).read_bytes()).hexdigest()
    config = study_config(tmp_path)
    config["trials"] = 2
    config["training"].update(resume=source["checkpoint"], iterations=2)
    result = training.tune(config)
    assert [trial["score"] for trial in result["trials"]] == [0.25, 0.5]
    assert [trial["source_best_score"] for trial in result["trials"]] == [1, 1]
    assert [trial["overall_selected_score"] for trial in result["trials"]] == [1, 1]
    assert result["best_trial"] == 1 and result["best_source"] == "candidate"
    objective_model, objective = load_checkpoint(result["best_checkpoint"])
    assert objective["metadata"]["version"] == result["best_iteration"] == 2
    assert objective["extra"]["candidate_score"] == result["best_score"] == 0.5
    assert objective["optimizer_state"] and objective["extra"]["replay"]
    inherited, _ = load_checkpoint(tmp_path / "study" / "trial-1" / "best.pt")
    assert content_digest(objective_model.state_dict()) != content_digest(inherited.state_dict())
    assert hashlib.sha256(Path(source["checkpoint"]).read_bytes()).hexdigest() == original


@pytest.mark.parametrize("mode,workers", [("sequential", 0), ("batched", 0), ("batched", 1)])
def test_different_neural_architectures_evaluate_and_cancel(mode, workers):
    torch.set_num_threads(1)
    candidate = PolicyValueNet("brandubh", 4, 0)
    opponent = PolicyValueNet("brandubh", 8, 1)
    config = {
        **training.normalize_config(small()),
        "evaluation_opponent": "frozen_model",
        "evaluation_frozen_model_id": "a" * 24,
        "evaluation_mode": mode,
        "search_workers": workers,
    }
    result = evaluate_model(candidate, config, previous=opponent)
    assert len(result["games"]) == 2
    assert candidate.channels == 4 and opponent.channels == 8
    assert next(candidate.parameters()).device == next(opponent.parameters()).device
    with pytest.raises(InterruptedError):
        evaluate_model(candidate, config, previous=opponent, should_stop=lambda: True)
    with pytest.raises(ValueError, match="ruleset"):
        evaluate_model(candidate, config, previous=PolicyValueNet("tablut", 8, 1))


def test_architecture_search_against_larger_frozen_model_stays_out_of_registry(tmp_path):
    registry_dir = tmp_path / "models"
    path = tmp_path / "opponent.pt"
    save_checkpoint(path, PolicyValueNet("brandubh", 8, 1), {"name": "Fixed larger network"})
    registry = ModelRegistry(registry_dir)
    opponent = registry.register(path)
    config = study_config(tmp_path)
    config.update(
        trials=16, opponents=[{"opponent": "frozen_model", "model_id": opponent["id"], "weight": 1}]
    )
    config["search_space"] = {
        "channels": {"type": "categorical", "choices": [4]},
        "blocks": {"type": "categorical", "choices": [0, 1]},
    }
    config["training"].update(registry_dir=str(registry_dir), save_all_best=True)
    result = training.tune(config)
    assert result["completed_trials"] == 16
    assert [model["id"] for model in registry.list()] == [opponent["id"]]
    for trial in range(16):
        _, full = load_checkpoint(tmp_path / "study" / f"trial-{trial}" / "last.pt")
        assert full["optimizer_state"] and full["extra"]["replay"]
        assert (tmp_path / "study" / f"trial-{trial}" / "objective.pt").is_file()
        assert not (tmp_path / "study" / f"trial-{trial}" / "inference.pt").exists()
    # Explicit winning-checkpoint continuation is ordinary training and registers.
    continued = training.train(
        {
            **result["best_configuration"],
            "resume": result["best_trial_checkpoint"],
            "iterations": result["best_iteration"] + 1,
            "output_dir": str(tmp_path / "continued"),
        }
    )
    assert continued["status"] == "completed" and len(registry.list()) == 2


@pytest.mark.parametrize(
    "failure", [InterruptedError("cancelled verification"), RuntimeError("failed verification")]
)
def test_verification_persists_optimization_before_any_cancellable_work(
    tmp_path, monkeypatch, failure
):
    real_evaluate = training.evaluate_model
    config = study_config(tmp_path)
    config["verification"] = {"seed": 1024, "games": 2}

    def evaluate(model, settings, **kwargs):
        if settings["seed"] == 1024:
            persisted = json.loads((tmp_path / "study" / "result.json").read_text())
            assert persisted["completed_trials"] == 1 and persisted["best_configuration"]
            assert persisted["verification_status"] == "running"
            assert Path(persisted["best_checkpoint"]).is_file()
            raise failure
        return real_evaluate(model, settings, **kwargs)

    monkeypatch.setattr(training, "evaluate_model", evaluate)
    result = training.tune(config)
    assert result["status"] == "completed" and result["completed_trials"] == 1
    assert result["verification_status"] == (
        "cancelled" if isinstance(failure, InterruptedError) else "failed"
    )
    for name in ["progress.json", "result.json"]:
        assert json.loads((tmp_path / "study" / name).read_text()) == result
    assert (tmp_path / "study" / "best-config.json").is_file()


def test_api_rejects_workload_and_ambiguous_legacy_before_spawning(tmp_path, monkeypatch):
    with TestClient(create_app(tmp_path / "server")) as client:
        root = client.app.state.data_dir
        identifier = "a" * 32
        directory = root / "jobs" / identifier
        config = study_config(tmp_path)
        config["output_dir"] = str(directory)
        result = training.tune(config)
        client.app.state.store.save_job(
            {
                "id": identifier,
                "kind": "tune",
                "status": "completed",
                "created_at": "2026-01-01",
                "config": config,
                "progress": result,
                "result": result,
            }
        )
        monkeypatch.setattr(
            "hnefatafl.server.subprocess.Popen",
            lambda *args, **kwargs: pytest.fail("must reject before spawning"),
        )
        response = client.post(
            "/api/training/jobs",
            json={"kind": "tune", "resume_study": identifier, "training": {"iterations": 2}},
        )
        assert response.status_code == 400 and "training.iterations" in response.text
        with sqlite3.connect(result["storage"][10:]) as db:
            db.execute("DELETE FROM study_user_attributes WHERE key='hnefatafl_identity'")
            db.execute("DELETE FROM trial_user_attributes WHERE key='experiment_identity'")
        response = client.post(
            "/api/training/jobs", json={"kind": "tune", "resume_study": identifier}
        )
        assert response.status_code == 400 and "read-only" in response.text
        assert len(client.app.state.store.jobs()) == 1


def test_backend_enforces_active_frozen_model_dependency(tmp_path):
    with TestClient(create_app(tmp_path / "server")) as client:
        root = client.app.state.data_dir
        path = tmp_path / "frozen.pt"
        save_checkpoint(path, PolicyValueNet("brandubh", 4, 0), {"name": "Protected"})
        model = ModelRegistry(root / "models").register(path)
        response = client.post(
            "/api/training/jobs",
            json={
                "kind": "tune",
                "trials": 100,
                "training": small(),
                "search_space": {"learning_rate": {"type": "categorical", "choices": [0.001]}},
                "opponents": [{"opponent": "frozen_model", "model_id": model["id"], "weight": 1}],
            },
        )
        assert response.status_code == 201, response.text
        job = response.json()
        route = f"/api/models/{model['id']}"
        assert client.get(route + "/usage").json()["running_optimizations"][0]["id"] == job["id"]
        assert client.delete(route).status_code == 409
        client.post(f"/api/training/jobs/{job['id']}/cancel")
        import time

        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            state = client.get(f"/api/training/jobs/{job['id']}").json()
            if state["status"] not in {"running", "cancelling"}:
                break
            time.sleep(0.05)
        assert client.delete(route).status_code == 200


def test_sqlite_study_lease_prevents_concurrent_invocations(tmp_path):
    from hnefatafl.training_identity import study_lease

    config = study_config(tmp_path)
    with study_lease(config):
        with pytest.raises(ValueError, match="already running"):
            training.tune(config)
    assert training.tune(config)["completed_trials"] == 1


def test_exploratory_importance_requires_enough_variable_observations():
    from types import SimpleNamespace

    trials = [
        SimpleNamespace(params={"learning_rate": value / 1000}, value=value / 30)
        for value in range(30)
    ]
    assert "values" not in training.parameter_importance(trials[:19])
    result = training.parameter_importance(trials)
    assert result["method"] == "binned_variance" and result["samples"] == 30
    assert result["values"] == {"learning_rate": 1}
    for trial in trials:
        trial.value = 0
    assert "values" not in training.parameter_importance(trials)


def test_replaced_source_is_rejected_before_a_trial_updates(tmp_path):
    source = training.train({**small(), "output_dir": str(tmp_path / "source")})
    path = Path(source["checkpoint"])
    _, payload = load_checkpoint(path)
    expected = checkpoint_identity(path, payload)
    next(iter(payload["state_dict"].values())).add_(0.1)
    torch.save(payload, path)
    with pytest.raises(ValueError, match="source checkpoint content changed"):
        training.train(
            {
                **small(),
                "resume": str(path),
                "iterations": 2,
                "output_dir": str(tmp_path / "rejected"),
            },
            trial_mode=True,
            expected_source=expected,
        )
    assert not (tmp_path / "rejected" / "last.pt").exists()


def test_api_does_not_ignore_a_changed_explicit_source_token(tmp_path, monkeypatch):
    with TestClient(create_app(tmp_path / "server")) as client:
        root = client.app.state.data_dir / "jobs"
        source_id, other_id, study_id = "1" * 32, "2" * 32, "3" * 32
        source = training.train({**small(), "output_dir": str(root / source_id)})
        _, payload = load_checkpoint(source["checkpoint"])
        other = root / other_id
        other.mkdir()
        next(iter(payload["state_dict"].values())).add_(0.1)
        torch.save(payload, other / "last.pt")
        config = study_config(tmp_path)
        config.update(output_dir=str(root / study_id), source_checkpoint=f"{source_id}:last.pt")
        config["training"].update(resume=source["checkpoint"], iterations=2)
        result = training.tune(config)
        client.app.state.store.save_job(
            {
                "id": study_id,
                "kind": "tune",
                "status": "completed",
                "created_at": "2026-01-01",
                "config": config,
                "progress": result,
                "result": result,
            }
        )
        monkeypatch.setattr(
            "hnefatafl.server.subprocess.Popen",
            lambda *args, **kwargs: pytest.fail("incompatible source must fail before launch"),
        )
        response = client.post(
            "/api/training/jobs",
            json={
                "kind": "tune",
                "resume_study": study_id,
                "source_checkpoint": f"{other_id}:last.pt",
            },
        )
        assert response.status_code == 400 and "source.checkpoint_content" in response.text


def test_checkpoint_tuning_inherits_effective_optimizer_settings_from_legacy_metadata(tmp_path):
    source = training.train({**small(), "output_dir": str(tmp_path / "source")})
    path = Path(source["checkpoint"])
    _, payload = load_checkpoint(path)
    payload["metadata"]["training_config"]["weight_decay"] = 0.7
    torch.save(payload, path)
    config = study_config(tmp_path)
    config["training"] = {"preset": "smoke", "resume": str(path), "iterations": 2}
    result = training.tune(config)
    _, continued = load_checkpoint(result["best_trial_checkpoint"])
    assert continued["optimizer_state"]["param_groups"][0]["weight_decay"] == 0.0001
    assert result["source_configuration"]["weight_decay"] == 0.0001
