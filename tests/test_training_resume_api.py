"""Reviewed UI resumption uses a server-owned full checkpoint and isolated outputs."""

import hashlib
import time

import pytest
import torch
from auth_support import AuthenticatedClient as TestClient

from hnefatafl.models import PolicyValueNet, load_checkpoint, save_checkpoint
from hnefatafl.server import create_app
from hnefatafl.training import train


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "server")) as session:
        yield session


def completed(client, response):
    assert response.status_code == 201, response.text
    identifier = response.json()["id"]
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        job = client.get(f"/api/training/jobs/{identifier}").json()
        if job["status"] not in {"running", "cancelling"}:
            assert job["status"] == "completed", job
            return job
        time.sleep(0.05)
    raise AssertionError("Real smoke training did not finish")


def test_reviewed_job_resume_retains_full_state_and_separate_outputs(client, tmp_path):
    config = {
        "preset": "smoke",
        "ruleset": "brandubh",
        "model_name": "Resume witness",
        "seed": 17,
        "device": "cpu",
        # Compare RNG/optimizer resumption with complete two-visit searches,
        # rather than different wall-clock cutoffs under shared CI load.
        "mcts_time_limit": 5,
        "cpu_threads": 1,
        "max_game_plies": 6,
        "evaluation_max_plies": 2,
        "save_all_best": True,
    }
    first = completed(client, client.post("/api/training/jobs", json=config))
    root = client.app.state.data_dir
    original = root / "jobs" / first["id"] / "last.pt"
    digest = hashlib.sha256(original.read_bytes()).hexdigest()
    route = f"/api/training/jobs/{first['id']}/resume"
    info = client.get(route).json()
    assert info["can_resume"] is True
    assert info["completed_iteration"] == 1
    assert info["checkpoint"] == f"{first['id']}:last.pt"
    assert (info["learning_rate"], info["weight_decay"], info["momentum"]) == (
        0.001,
        0.0001,
        0.9,
    )
    assert (info["ruleset"], info["channels"], info["blocks"], info["optimizer"]) == (
        "brandubh",
        4,
        0,
        "adamw",
    )
    assert info["seed"] == info["config"]["seed"] == 17
    for field, value in first["config"].items():
        if field not in {"output_dir", "registry_dir", "resume"}:
            assert info["config"][field] == value, field
    for body in (
        {"iterations": 1},
        {"iterations": True},
        *(
            {"iterations": 2, name: value}
            for name, value in {
                "channels": 128,
                "blocks": 1,
                "ruleset": "tablut",
                "seed": 18,
                "optimizer": "adam",
                "resume": "last.pt",
                "output_dir": "elsewhere",
            }.items()
        ),
    ):
        assert client.post(route, json=body).status_code == 400
    assert len(client.get("/api/training/jobs").json()) == 1
    resumed = completed(client, client.post(route, json={"iterations": 2}))
    assert resumed["id"] != first["id"]
    assert resumed["progress"]["iteration"] == 2
    assert [point["iteration"] for point in resumed["progress"]["history"]] == [1, 2]
    assert resumed["config"]["seed"] == 17
    assert resumed["config"]["save_all_best"] is True
    assert hashlib.sha256(original.read_bytes()).hexdigest() == digest
    resumed_model, payload = load_checkpoint(root / "jobs" / resumed["id"] / "last.pt")
    assert payload["optimizer_state"] is not None
    assert payload["extra"]["replay"]
    assert payload["extra"]["python_rng"] and payload["extra"]["torch_rng"].numel()
    uninterrupted = train(
        {
            **config,
            "iterations": 2,
            "output_dir": str(tmp_path / "uninterrupted"),
            "registry_dir": str(tmp_path / "uninterrupted-models"),
        }
    )
    full_model, _ = load_checkpoint(uninterrupted["checkpoint"])
    for key, value in resumed_model.state_dict().items():
        assert torch.equal(value, full_model.state_dict()[key]), key


def test_reviewed_resume_updates_optimizer_groups_with_validated_hyperparameters(client):
    first = completed(
        client,
        client.post(
            "/api/training/jobs",
            json={
                "preset": "smoke",
                "device": "cpu",
                "optimizer": "sgd",
                "learning_rate": 0.002,
                "weight_decay": 0.02,
                "momentum": 0.7,
                "seed": 29,
            },
        ),
    )
    route = f"/api/training/jobs/{first['id']}/resume"
    root = client.app.state.data_dir
    checkpoint_path = root / "jobs" / first["id"] / "last.pt"
    _, old_checkpoint = load_checkpoint(checkpoint_path)
    # Earlier versions had no momentum metadata and could record stale
    # optimizer values after resuming. Prefill from the effective parameter group.
    old_checkpoint["metadata"]["training_config"].pop("momentum")
    # Legacy missing fields fall back to the job's original inputs, rather than
    # today's new-job plan. Full saved values remain authoritative otherwise.
    old_checkpoint["metadata"]["training_config"].pop("batch_size")
    old_checkpoint["metadata"]["training_config"]["learning_rate"] = 0.001
    old_checkpoint["metadata"]["training_config"]["weight_decay"] = 0.0001
    torch.save(old_checkpoint, checkpoint_path)
    info = client.get(route).json()
    assert (info["learning_rate"], info["weight_decay"], info["momentum"]) == (0.002, 0.02, 0.7)
    assert info["config"]["batch_size"] == first["config"]["batch_size"] == 8
    assert info["config"]["learning_rate"] == 0.002
    for changes in (
        {"learning_rate": 0},
        {"learning_rate": True},
        {"weight_decay": -1},
        {"weight_decay": "0.1"},
        {"momentum": 1.01},
        {"momentum": None},
        {"optimizer": "adamw"},
        {"batch_size": 0},
        {"epochs": 101},
        {"evaluation_games": 3},
        {"device": "invalid"},
        {"mixed_precision": "false"},
        {"mcts_time_limit": True},
    ):
        response = client.post(route, json={"iterations": 2, **changes})
        assert response.status_code == 400, response.text
    assert len(client.get("/api/training/jobs").json()) == 1
    changes = {
        "learning_rate": 0.0004,
        "weight_decay": 0.005,
        "momentum": 0.4,
        "preset": "beginner",
        "model_name": "Reviewed continuation",
        "games": 2,
        "batch_size": 2,
        "epochs": 2,
        "mcts_iterations": 1,
        "mcts_time_limit": 0.2,
        "exploration": 0.7,
        "temperature": 0.8,
        "dirichlet_alpha": 0.5,
        "dirichlet_fraction": 0.1,
        "replay_buffer_size": 32,
        "checkpoint_frequency": 2,
        "evaluation_frequency": 2,
        "evaluation_games": 4,
        "evaluation_iterations": 1,
        "evaluation_max_plies": 2,
        "max_game_plies": 4,
        "device": "cpu",
        "mixed_precision": False,
        "cpu_threads": 1,
        "save_all_best": True,
    }
    resumed = completed(client, client.post(route, json={"iterations": 2, **changes}))
    assert resumed["config"]["seed"] == 29
    assert resumed["config"]["optimizer"] == "sgd"
    assert [point["iteration"] for point in resumed["progress"]["history"]] == [1, 2]
    _, checkpoint = load_checkpoint(root / "jobs" / resumed["id"] / "last.pt")
    group = checkpoint["optimizer_state"]["param_groups"][0]
    assert (group["lr"], group["weight_decay"], group["momentum"]) == (0.0004, 0.005, 0.4)
    for name, value in changes.items():
        assert checkpoint["metadata"]["training_config"][name] == value
    assert checkpoint["extra"]["replay"]
    assert resumed["progress"]["metrics"]["updates"] > 0
    assert checkpoint["extra"]["games_generated"] == 3
    assert len(checkpoint["extra"]["history"]) == 2
    assert len(checkpoint["extra"]["replay"]) <= 32
    assert checkpoint["metadata"]["training_config"]["channels"] == 4
    assert checkpoint["metadata"]["training_config"]["blocks"] == 0
    assert resumed["progress"]["metrics"]["updates"] == (
        2 * ((len(checkpoint["extra"]["replay"]) + 1) // 2)
    )


@pytest.mark.parametrize(
    "checkpoint",
    [
        "missing",
        "corrupt",
        "inference",
        "malformed_optimizer",
        "malformed_groups",
        "malformed_group",
    ],
)
def test_resume_metadata_honestly_rejects_nonresumable_files(client, checkpoint):
    identifier = "a" * 32
    directory = client.app.state.data_dir / "jobs" / identifier
    directory.mkdir()
    client.app.state.store.save_job(
        {
            "id": identifier,
            "kind": "train",
            "status": "completed",
            "created_at": "now",
            "config": {"preset": "smoke", "ruleset": "brandubh"},
            "progress": {},
        }
    )
    if checkpoint == "corrupt":
        (directory / "last.pt").write_bytes(b"not a tensor checkpoint")
    elif checkpoint == "inference":
        save_checkpoint(
            directory / "last.pt", PolicyValueNet("brandubh", 4, 0), {"name": "Inference"}
        )
    elif checkpoint.startswith("malformed_"):
        path = save_checkpoint(
            directory / "last.pt",
            PolicyValueNet("brandubh", 4, 0),
            {"training_config": {"optimizer": "sgd"}},
            extra={"iteration": 1, "replay": [], "python_rng": [], "torch_rng": torch.tensor([])},
        )
        _, payload = load_checkpoint(path)
        payload["optimizer_state"] = {
            "malformed_optimizer": [],
            "malformed_groups": {"param_groups": "invalid"},
            "malformed_group": {"param_groups": ["invalid"]},
        }[checkpoint]
        torch.save(payload, path)
    route = f"/api/training/jobs/{identifier}/resume"
    response = client.get(route)
    assert response.status_code == 200
    assert response.json()["can_resume"] is False
    assert response.json()["reason"]
    assert client.post(route, json={"iterations": 2}).status_code == 409
    assert len(client.get("/api/training/jobs").json()) == 1


def test_resume_rejects_running_source_without_spawning_a_second_worker(client):
    launched = client.post(
        "/api/training/jobs", json={"preset": "smoke", "iterations": 1000, "device": "cpu"}
    )
    assert launched.status_code == 201
    identifier = launched.json()["id"]
    route = f"/api/training/jobs/{identifier}/resume"
    info = client.get(route).json()
    assert info["can_resume"] is False
    assert "Stop this training job" in info["reason"]
    assert client.post(route, json={"iterations": 1001}).status_code == 409
    assert len(client.get("/api/training/jobs").json()) == 1


def test_resume_route_changes_only_evaluation_context(client):
    first = completed(
        client,
        client.post(
            "/api/training/jobs",
            json={
                "preset": "smoke",
                "device": "cpu",
                "cpu_threads": 1,
                "max_game_plies": 2,
                "evaluation_max_plies": 2,
            },
        ),
    )
    route = f"/api/training/jobs/{first['id']}/resume"
    second = completed(
        client,
        client.post(
            route,
            json={
                "iterations": 2,
                "evaluation_opponent": "jarl",
                "evaluation_jarl_depth": 1,
                "evaluation_jarl_time_limit": 0.01,
                "evaluation_frequency": 5,
            },
        ),
    )
    assert second["progress"]["metrics"]["evaluation"]["opponent"] == "jarl"
    assert len(second["progress"]["evaluation_selection"]["benchmarks"]) == 2
    assert [row["evaluation_opponent"] for row in second["progress"]["history"]] == [
        "shield_bearer",
        "jarl",
    ]
    third = completed(
        client,
        client.post(
            f"/api/training/jobs/{second['id']}/resume",
            json={
                "iterations": 3,
                "evaluation_opponent": "champion",
            },
        ),
    )
    assert third["progress"]["evaluation_selection"]["champion_iteration"] == 2
    assert third["progress"]["metrics"]["evaluation"]["opponent"] == "champion"
    root = client.app.state.data_dir
    _, payload = load_checkpoint(root / "jobs" / third["id"] / "last.pt")
    assert payload["extra"]["iteration"] == 3
    assert len(payload["extra"]["evaluation_state"]["benchmarks"]) == 2
    assert payload["extra"]["best_snapshot"]["metadata"]["version"] == 1
    assert first["model_id"] in {model["id"] for model in client.get("/api/models").json()}


def test_resume_switches_to_maiden_without_resetting_learning_state(client):
    first = completed(
        client,
        client.post(
            "/api/training/jobs",
            json={
                "preset": "smoke",
                "device": "cpu",
                "cpu_threads": 1,
                "max_game_plies": 2,
                "evaluation_max_plies": 2,
            },
        ),
    )
    second = completed(
        client,
        client.post(
            f"/api/training/jobs/{first['id']}/resume",
            json={"iterations": 2, "evaluation_opponent": "shield_maiden"},
        ),
    )
    result = second["progress"]
    assert result["metrics"]["updates"] > 0
    assert result["metrics"]["evaluation"]["opponent"] == "shield_maiden"
    assert result["metrics"]["evaluation"]["opponent_config"]["heuristic_version"] == 1
    assert [row["evaluation_opponent"] for row in result["history"]] == [
        "shield_bearer",
        "shield_maiden",
    ]
    assert len(result["evaluation_selection"]["benchmarks"]) == 2
    root = client.app.state.data_dir
    _, before = load_checkpoint(root / "jobs" / first["id"] / "last.pt")
    _, after = load_checkpoint(root / "jobs" / second["id"] / "last.pt")
    assert after["extra"]["iteration"] == 2
    assert len(after["extra"]["replay"]) > len(before["extra"]["replay"])
    for parameter, values in before["optimizer_state"]["state"].items():
        assert after["optimizer_state"]["state"][parameter]["step"] > values["step"]
    assert after["extra"]["best_snapshot"]["metadata"]["version"] == 1
