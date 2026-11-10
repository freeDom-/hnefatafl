"""Recorded effective configuration remains portable without inventing history."""

from copy import deepcopy
import json
from pathlib import Path

import pytest
from auth_support import AuthenticatedClient as TestClient
from test_job_archives import export, job_record, upload
from test_training import smoke_config

from hnefatafl import training
from hnefatafl.models import load_checkpoint, save_checkpoint
from hnefatafl.server import create_app
from hnefatafl.remote_training import clean
from hnefatafl.remote_worker import safe_progress
from hnefatafl.training_parameters import (
    PARAMETER_FIELDS,
    parameter_snapshot,
    restore_parameter_history,
    validated_parameters,
)


def snapshots(history):
    return [row for row in history if "training_parameters" in row]


def test_normalized_parameters_recorded_once_in_full_checkpoint_local_progress_and_result(tmp_path):
    publications = []
    result = training.train(
        smoke_config(tmp_path, iterations=3, weight_decay=0, cpu_threads=1),
        progress=lambda report: publications.append(deepcopy(report)),
    )
    history = result["history"]
    event = snapshots(history)
    assert len(event) == 1 and event[0]["iteration"] == 1
    expected = parameter_snapshot(training.normalize_config(smoke_config(tmp_path, weight_decay=0)))
    assert event[0]["training_parameters"] == expected
    assert set(expected) == {"version", *PARAMETER_FIELDS}
    assert expected["weight_decay"] == 0
    assert [row["training_parameters_iteration"] for row in history] == [1, 1, 1]
    for filename in ("progress.json", "result.json"):
        assert json.loads((tmp_path / "training" / filename).read_text())["history"] == history
    _, checkpoint = load_checkpoint(result["checkpoint"])
    assert checkpoint["extra"]["history"] == history
    assert publications[-1]["history"] == history
    assert all(len(snapshots(report["history"])) <= 1 for report in publications)


def test_resume_records_changed_and_unchanged_execution_boundaries_without_rewriting_source(
    tmp_path,
):
    first = training.train(smoke_config(tmp_path / "first", iterations=2, weight_decay=0))
    original = Path(first["checkpoint"]).read_bytes()
    changed = training.train(
        {
            "resume": first["checkpoint"],
            "output_dir": str(tmp_path / "changed"),
            "iterations": 4,
            "batch_size": 16,
            "mcts_iterations": 4,
            "weight_decay": 0.03,
        }
    )
    unchanged = training.train(
        {
            "resume": changed["checkpoint"],
            "output_dir": str(tmp_path / "unchanged"),
            "iterations": 6,
        }
    )
    history = unchanged["history"]
    assert history[:2] == first["history"]
    assert [row["iteration"] for row in snapshots(history)] == [1, 3, 5]
    assert [row["training_parameters_iteration"] for row in history] == [1, 1, 3, 3, 5, 5]
    assert history[2]["training_parameters"]["batch_size"] == 16
    assert history[2]["training_parameters"]["mcts_iterations"] == 4
    assert history[2]["training_parameters"]["weight_decay"] == 0.03
    assert history[4]["training_parameters"] == history[2]["training_parameters"]
    assert Path(first["checkpoint"]).read_bytes() == original


def test_parameters_come_from_effective_optimizer_groups_after_resume(tmp_path):
    first = training.train(smoke_config(tmp_path / "first"))
    model, payload = load_checkpoint(first["checkpoint"])
    # Old metadata can disagree with restored parameter groups. No explicit
    # override: the trainer must publish what its optimizer actually uses.
    payload["optimizer_state"]["param_groups"][0]["weight_decay"] = 0.027
    stale = tmp_path / "stale.pt"
    save_checkpoint(stale, model, payload["metadata"], extra=payload["extra"])
    import torch

    edited = torch.load(stale, weights_only=True)
    edited["optimizer_state"] = payload["optimizer_state"]
    torch.save(edited, stale)
    resumed = training.train(
        {"resume": str(stale), "output_dir": str(tmp_path / "new"), "iterations": 2}
    )
    assert resumed["history"][-1]["training_parameters"]["weight_decay"] == 0.027


def test_changed_effective_configuration_within_execution_emits_another_sparse_snapshot(
    tmp_path, monkeypatch
):
    original = training.generate_self_play
    calls = 0

    def generate(model, config, **kwargs):
        nonlocal calls
        calls += 1
        result = original(model, config, **kwargs)
        if calls == 1:
            # Only the next iteration's configuration changes. The first
            # snapshot must still describe the configuration captured at start.
            config["mcts_iterations"] = 3
        return result

    monkeypatch.setattr(training, "generate_self_play", generate)
    result = training.train(smoke_config(tmp_path, iterations=3))
    assert [row["iteration"] for row in snapshots(result["history"])] == [1, 2]
    assert result["history"][0]["training_parameters"]["mcts_iterations"] == 2
    assert result["history"][1]["training_parameters"]["mcts_iterations"] == 3
    assert result["history"][2]["training_parameters_iteration"] == 2


@pytest.mark.parametrize("failure", [InterruptedError, RuntimeError])
def test_evaluation_cancellation_or_failure_preserves_completed_provenance_once_and_resumes(
    tmp_path, monkeypatch, failure
):
    evaluate = training.evaluate_model

    def interrupted(*args, **kwargs):
        raise failure("stop during evaluation")

    monkeypatch.setattr(training, "evaluate_model", interrupted)
    config = smoke_config(tmp_path / "stopped", weight_decay=0)
    if failure is InterruptedError:
        stopped = training.train(config)
        assert stopped["status"] == "cancelled"
    else:
        with pytest.raises(RuntimeError, match="stop during evaluation"):
            training.train(config)
    checkpoint = tmp_path / "stopped" / "training" / "last.pt"
    _, payload = load_checkpoint(checkpoint)
    history = payload["extra"]["history"]
    assert len(history) == 1
    assert history[0]["training_parameters"]["weight_decay"] == 0
    assert history[0]["training_parameters_iteration"] == 1
    assert history[0]["learning_rate"] == history[0]["effective_learning_rate"] == 0.001
    assert history[0]["updates"] > 0
    monkeypatch.setattr(training, "evaluate_model", evaluate)
    resumed = training.train(
        smoke_config(tmp_path / "resumed", iterations=2, resume=str(checkpoint))
    )
    assert [row["iteration"] for row in snapshots(resumed["history"])] == [1, 2]
    assert resumed["history"][0] == history[0]


def test_cancel_before_first_completed_iteration_does_not_create_a_parameter_event(tmp_path):
    first = training.train(smoke_config(tmp_path / "first"))
    cancelled = training.train(
        smoke_config(
            tmp_path / "cancelled", iterations=2, resume=first["checkpoint"], batch_size=16
        ),
        should_stop=lambda: True,
    )
    assert cancelled["status"] == "cancelled"
    assert cancelled["history"] == first["history"]
    continued = training.train(
        {
            "resume": cancelled["checkpoint"],
            "output_dir": str(tmp_path / "continued"),
            "iterations": 3,
        }
    )
    assert [row["iteration"] for row in snapshots(continued["history"])] == [1, 2]
    assert continued["history"][1]["training_parameters"]["batch_size"] == 16


def test_legacy_checkpoint_remains_resumable_and_earlier_parameters_stay_unknown(tmp_path):
    first = training.train(smoke_config(tmp_path / "first"))
    import torch

    payload = torch.load(first["checkpoint"], weights_only=True)
    for row in payload["extra"]["history"]:
        row.pop("training_parameters")
        row.pop("training_parameters_iteration")
    legacy = tmp_path / "legacy.pt"
    torch.save(payload, legacy)
    original = legacy.read_bytes()
    resumed = training.train(
        smoke_config(tmp_path / "resumed", iterations=3, resume=str(legacy), batch_size=16)
    )
    assert "training_parameters" not in resumed["history"][0]
    assert "training_parameters_iteration" not in resumed["history"][0]
    assert [row["iteration"] for row in snapshots(resumed["history"])] == [2]
    assert resumed["history"][1]["training_parameters"]["batch_size"] == 16
    assert resumed["history"][2]["training_parameters_iteration"] == 2
    assert legacy.read_bytes() == original


@pytest.mark.parametrize(
    "value",
    [None, [], "bad", {"version": 2, "batch_size": 128}, {"version": True, "batch_size": 128}],
)
def test_malformed_or_future_optional_events_are_explicit_unknown_boundaries(value):
    assert validated_parameters(value) == {"version": 1}
    source = [{"iteration": 1, "training_parameters": value}]
    assert restore_parameter_history(source) == [
        {"iteration": 1, "training_parameters": {"version": 1}}
    ]
    assert source[0]["training_parameters"] == value


def test_snapshot_whitelist_rejects_credentials_coercions_invalid_values_and_unbounded_fields():
    snapshot = validated_parameters(
        {
            "version": 1,
            "batch_size": True,
            "epochs": "4",
            "mcts_iterations": 1.5,
            "mcts_time_limit": float("nan"),
            "games": -1,
            "replay_buffer_size": 100000000,
            "weight_decay": 0,
            "temperature": float("inf"),
            "exploration": 1.4,
            "password": "secret",
            "token": "private",
            "output_dir": "/private/path",
        }
    )
    assert snapshot == {"version": 1, "weight_decay": 0, "exploration": 1.4}


def test_remote_projections_keep_optional_parameter_events_strictly_whitelisted():
    report = {
        "history": [
            {
                "iteration": 1,
                "training_parameters": {
                    "version": 1,
                    "batch_size": 64,
                    "weight_decay": 0,
                    "private_path": "/private/data",
                    "worker_credential": "secret",
                    "extra_config": {"foo": "bar"},
                },
                "training_parameters_iteration": 1,
            }
        ]
    }
    expected = {"version": 1, "batch_size": 64, "weight_decay": 0}
    for project in (clean, safe_progress):
        result = project(report)
        assert result["history"][0]["training_parameters"] == expected
        assert result["history"][0]["training_parameters_iteration"] == 1


def test_malformed_optional_checkpoint_data_is_sanitized_on_resume(tmp_path):
    import torch

    first = training.train(smoke_config(tmp_path / "first"))
    payload = torch.load(first["checkpoint"], weights_only=True)
    payload["extra"]["history"][0]["training_parameters"] = {
        "version": 1,
        "batch_size": "64",
        "token": "private",
    }
    payload["extra"]["history"][0]["training_parameters_iteration"] = "bad"
    malformed = tmp_path / "malformed.pt"
    torch.save(payload, malformed)
    resumed = training.train(
        smoke_config(tmp_path / "resumed", iterations=2, resume=str(malformed))
    )
    assert resumed["history"][0]["training_parameters"] == {"version": 1}
    assert resumed["history"][0]["training_parameters_iteration"] is None
    assert len(snapshots(resumed["history"])) == 2


def test_long_lineage_events_roundtrip_in_archive_reports_and_full_checkpoint(tmp_path):
    import torch

    fixture = json.loads(
        (Path(__file__).parent / "fixtures/training_parameter_lineage.json").read_text()
    )
    events = {row["iteration"]: row["training_parameters"] for row in fixture["segments"]}
    reference = 1
    history = []
    for iteration in range(1, fixture["iterations"] + 1):
        point = {"iteration": iteration, "policy_loss": 1 / iteration, "learning_rate": 0.0001}
        if iteration in events:
            point["training_parameters"] = events[iteration]
            reference = iteration
        point["training_parameters_iteration"] = reference
        history.append(point)
    assert len(snapshots(history)) == 3
    with TestClient(create_app(tmp_path / "source")) as source:
        job, directory = job_record(
            source, progress={"history": history}, result={"history": history}
        )
        result = training.train(smoke_config(tmp_path / "seed"))
        payload = torch.load(result["checkpoint"], weights_only=True)
        payload["extra"]["history"] = history
        payload["extra"]["iteration"] = 1500
        payload["metadata"]["version"] = 1500
        torch.save(payload, directory / "last.pt")
        for name in ("progress.json", "result.json"):
            (directory / name).write_text(json.dumps({"history": history}))
        content = export(source, job)
    with TestClient(create_app(tmp_path / "destination")) as destination:
        response = upload(destination, content)
        assert response.status_code == 201, response.text
        restored = response.json()
        assert restored["progress"]["history"] == restored["result"]["history"] == history
        restored_checkpoint = destination.app.state.data_dir / "jobs" / restored["id"] / "last.pt"
        _, saved = load_checkpoint(restored_checkpoint)
        assert saved["extra"]["history"] == history
        continued = training.train(
            {
                "resume": str(restored_checkpoint),
                "output_dir": str(tmp_path / "continued"),
                "iterations": 1501,
            }
        )
        assert continued["history"][:1500] == history
        assert continued["history"][-1]["training_parameters_iteration"] == 1501
