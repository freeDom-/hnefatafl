"""Reviewed UI resumption uses a server-owned full checkpoint and isolated outputs."""

import hashlib
import time

import pytest
import torch
from fastapi.testclient import TestClient

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
    deadline = time.monotonic() + 30
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
    assert (info["ruleset"], info["channels"], info["blocks"], info["optimizer"]) == (
        "brandubh",
        4,
        0,
        "adamw",
    )
    for body in ({"iterations": 1}, {"iterations": True}, {"iterations": 2, "channels": 128}):
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


@pytest.mark.parametrize("checkpoint", ["missing", "corrupt", "inference"])
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
