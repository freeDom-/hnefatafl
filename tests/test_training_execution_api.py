"""Self-play execution settings survive API queuing and legacy checkpoint review."""

import random

import pytest
import torch
from auth_support import AuthenticatedClient as TestClient

from hnefatafl.models import PolicyValueNet, save_checkpoint
from hnefatafl.server import create_app
from hnefatafl.training import normalize_config


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "hnefatafl.server.application_metadata",
        lambda: {"package_version": "0.26.0", "git_revision": None},
    )
    with TestClient(create_app(tmp_path / "server")) as session:
        yield session


def test_execution_settings_default_and_propagate_to_remote_claim(client):
    worker = client.post(
        "/api/training/workers", json={"name": "Batch worker", "capacity": 2}
    ).json()
    response = client.post(
        "/api/training/jobs",
        json={"execution": "remote", "preset": "smoke", "device": "cpu"},
    )
    assert response.status_code == 201, response.text
    defaults = response.json()["config"]
    assert (
        defaults["self_play_mode"],
        defaults["self_play_concurrency"],
        defaults["inference_batch_size"],
    ) == ("auto", 16, 32)
    settings = {
        "self_play_mode": "batched",
        "self_play_concurrency": 7,
        "inference_batch_size": 12,
        "inference_cache_mode": "exact",
        "inference_cache_capacity": 128,
    }
    response = client.post(
        "/api/training/jobs",
        json={"execution": "remote", "preset": "smoke", "device": "cpu", **settings},
    )
    assert response.status_code == 201, response.text
    queued = response.json()
    assert queued["config"]["batch_size"] == 8
    for key, value in settings.items():
        assert queued["config"][key] == value
    headers = {"Authorization": "Bearer " + worker["token"]}
    assert (
        client.post(
            "/api/worker/heartbeat",
            headers=headers,
            json={
                "capabilities": {
                    "package_version": client.app.version,
                    "inference_cache_modes": ["off", "exact"],
                }
            },
        ).status_code
        == 200
    )
    claimed = client.post(
        "/api/worker/claim", headers=headers, json={"request_id": "batch-claim"}
    ).json()["job"]
    # Queue order is unchanged; claim the default job first.
    assert claimed["config"]["self_play_mode"] == "auto"
    claimed = client.post(
        "/api/worker/claim", headers=headers, json={"request_id": "batch-claim-2"}
    ).json()["job"]
    for key, value in settings.items():
        assert claimed["config"][key] == value


@pytest.mark.parametrize(
    "changes",
    [
        {"inference_cache_mode": "symmetry"},
        {"inference_cache_mode": True},
        {"inference_cache_capacity": 0},
        {"inference_cache_capacity": 65537},
        {"inference_cache_capacity": False},
        {"self_play_mode": "invalid"},
        {"self_play_mode": True},
        {"self_play_concurrency": 0},
        {"self_play_concurrency": 1025},
        {"self_play_concurrency": True},
        {"inference_batch_size": 0},
        {"inference_batch_size": 4097},
        {"inference_batch_size": 1.5},
    ],
)
def test_execution_parameters_use_common_backend_validation(client, changes):
    response = client.post(
        "/api/training/jobs", json={"execution": "remote", "preset": "smoke", **changes}
    )
    assert response.status_code == 400, response.text
    assert client.get("/api/training/jobs").json() == []


def test_legacy_checkpoint_resume_prefills_defaults_and_accepts_execution_overrides(client):
    identifier = "b" * 32
    directory = client.app.state.data_dir / "jobs" / identifier
    directory.mkdir()
    config = normalize_config({"preset": "smoke", "seed": 37, "device": "cpu"})
    for key in (
        "self_play_mode",
        "self_play_concurrency",
        "inference_batch_size",
        "inference_cache_mode",
        "inference_cache_capacity",
        "inference_backend",
        "inference_device",
    ):
        config.pop(key, None)
    model = PolicyValueNet("brandubh", 4, 0)
    optimizer = torch.optim.AdamW(model.parameters(), lr=config["learning_rate"])
    save_checkpoint(
        directory / "last.pt",
        model,
        {"training_config": config, "version": 1},
        optimizer,
        {
            "iteration": 1,
            "replay": [],
            "python_rng": random.Random(37).getstate(),
            "torch_rng": torch.get_rng_state(),
        },
    )
    client.app.state.store.save_job(
        {
            "id": identifier,
            "kind": "train",
            "status": "completed",
            "config": config,
            "created_at": "now",
            "progress": {"iteration": 1},
        }
    )
    route = f"/api/training/jobs/{identifier}/resume"
    response = client.get(route)
    assert response.status_code == 200, response.text
    saved = response.json()["config"]
    assert (
        saved["self_play_mode"],
        saved["self_play_concurrency"],
        saved["inference_batch_size"],
    ) == ("auto", 16, 32)
    assert saved["inference_cache_mode"] == "off"
    assert saved["inference_cache_capacity"] == 4096
    assert saved["inference_backend"] == "pytorch"
    assert saved["inference_device"] == "auto"
    overrides = {
        "inference_cache_mode": "exact",
        "inference_cache_capacity": 512,
        "inference_backend": "openvino",
        "inference_device": "GPU",
        "self_play_mode": "batched",
        "self_play_concurrency": 3,
        "inference_batch_size": 6,
    }
    response = client.post(route, json={"iterations": 2, "execution": "remote", **overrides})
    assert response.status_code == 201, response.text
    resumed = response.json()["config"]
    for key, value in overrides.items():
        assert resumed[key] == value
    for key in ("ruleset", "channels", "blocks", "seed", "optimizer"):
        assert resumed[key] == config[key]


def test_workers_without_cache_extension_keep_default_jobs_and_cannot_claim_exact_jobs(client):
    worker = client.post("/api/training/workers", json={"name": "Legacy", "capacity": 1}).json()
    headers = {"Authorization": "Bearer " + worker["token"]}
    client.post(
        "/api/worker/heartbeat",
        headers=headers,
        json={"capabilities": {"package_version": client.app.version}},
    )
    exact = client.post(
        "/api/training/jobs",
        json={"execution": "remote", "preset": "smoke", "inference_cache_mode": "exact"},
    ).json()
    default = client.post(
        "/api/training/jobs", json={"execution": "remote", "preset": "smoke"}
    ).json()
    claimed = client.post(
        "/api/worker/claim", headers=headers, json={"request_id": "legacy"}
    ).json()["job"]
    assert claimed["id"] == default["id"]
    assert "inference_cache_mode" not in claimed["config"]
    assert "inference_cache_capacity" not in claimed["config"]
    assert client.get(f"/api/training/jobs/{exact['id']}").json()["status"] == "queued"
