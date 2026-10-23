"""Authenticated remote leases coordinate independent training jobs and artifacts."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import random

from auth_support import AuthenticatedClient as TestClient
import pytest
import torch

from hnefatafl.models import PolicyValueNet, export_checkpoint, save_checkpoint
from hnefatafl.server import create_app
from hnefatafl.training import normalize_config


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "server")) as session:
        yield session


def worker(client, capacity=1):
    response = client.post(
        "/api/training/workers", json={"name": "Cloud raven", "capacity": capacity}
    )
    assert response.status_code == 201, response.text
    value = response.json()
    return value["worker"], {"Authorization": "Bearer " + value["token"]}


def queue(client, **changes):
    response = client.post(
        "/api/training/jobs",
        json={
            "execution": "remote",
            "preset": "smoke",
            "ruleset": "brandubh",
            "model_name": "Cloud raven",
            "device": "cpu",
            **changes,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def claim(client, headers, request_id="claim-1"):
    response = client.post("/api/worker/claim", headers=headers, json={"request_id": request_id})
    assert response.status_code == 200, response.text
    return response.json()["job"]


def report(client, headers, lease, sequence=0, **changes):
    return client.post(
        f"/api/worker/jobs/{lease['id']}/progress",
        headers=headers,
        json={
            "lease_token": lease["lease_token"],
            "sequence": sequence,
            "progress": {"iteration": sequence, **changes},
            "logs": "latest tail",
        },
    )


@pytest.fixture
def artifacts(tmp_path):
    config = normalize_config(
        {"preset": "smoke", "ruleset": "brandubh", "model_name": "Cloud raven"}
    )
    model = PolicyValueNet("brandubh", 4, 0)
    optimizer = torch.optim.AdamW(model.parameters())
    checkpoint = save_checkpoint(
        tmp_path / "full.pt",
        model,
        {"name": "Cloud raven", "version": 1, "training_config": config},
        optimizer,
        {
            "iteration": 1,
            "replay": [],
            "python_rng": random.Random(17).getstate(),
            "torch_rng": torch.get_rng_state(),
        },
    )
    return checkpoint, export_checkpoint(checkpoint, tmp_path / "inference.pt")


def upload(client, headers, lease, kind, path):
    return client.post(
        f"/api/worker/jobs/{lease['id']}/artifacts/{kind}",
        headers=headers,
        data={"lease_token": lease["lease_token"]},
        files={"file": ("artifact.pt", path.read_bytes())},
    )


def finish(client, headers, lease, status="completed"):
    return client.post(
        f"/api/worker/jobs/{lease['id']}/finish",
        headers=headers,
        json={"lease_token": lease["lease_token"], "status": status},
    )


def test_worker_credentials_are_hashed_scoped_and_shown_only_once(client):
    remote, headers = worker(client)
    token = headers["Authorization"][7:]
    public = client.get("/api/training/workers").json()
    assert public[0]["status"] == "offline"
    assert token not in json.dumps(public)
    with client.app.state.store.connection() as db:
        stored = db.execute("SELECT token_hash,data FROM training_workers").fetchone()
    assert stored["token_hash"] == hashlib.sha256(token.encode()).hexdigest()
    assert token not in stored["data"]
    assert client.post("/api/worker/claim", json={}).status_code == 401
    assert (
        client.post(
            "/api/worker/claim", headers={"Authorization": "Bearer wrong"}, json={}
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/worker/heartbeat",
            headers=headers,
            json={"capabilities": {"protocol_version": 99}},
        ).status_code
        == 409
    )
    response = client.post(
        "/api/worker/heartbeat",
        headers=headers,
        json={"capabilities": {"protocol_version": 1, "cuda_available": True}},
    )
    assert response.json()["worker"]["id"] == remote["id"]
    assert response.json()["worker"]["status"] == "online"


def test_claim_retries_are_idempotent_and_capacity_is_atomic(client):
    _, headers = worker(client, capacity=2)
    jobs = [queue(client), queue(client), queue(client)]
    first = claim(client, headers)
    assert claim(client, headers) == first
    with ThreadPoolExecutor(max_workers=2) as pool:
        claimed = list(pool.map(lambda key: claim(client, headers, key), ["second", "third"]))
    assert sum(value is not None for value in claimed) == 1
    assert len({first["id"], *(value["id"] for value in claimed if value)}) == 2
    assert len(jobs) == 3
    assert "lease_hash" not in json.dumps(client.get("/api/training/jobs").json())
    assert "lease_token" not in json.dumps(client.get("/api/training/jobs").json())


def test_remote_gpu_jobs_do_not_require_local_gpu_and_skip_cpu_workers(client):
    _, cpu = worker(client)
    _, gpu = worker(client)
    gpu_job = queue(client, device="cuda")
    cpu_job = queue(client)
    assert claim(client, cpu)["id"] == cpu_job["id"]
    client.post(
        "/api/worker/heartbeat", headers=gpu, json={"capabilities": {"cuda_available": True}}
    )
    assert claim(client, gpu)["id"] == gpu_job["id"]
    assert (
        client.post(
            "/api/training/jobs", json={"execution": "remote", "device": "bogus"}
        ).status_code
        == 400
    )
    assert (
        client.post("/api/training/jobs", json={"execution": "remote", "kind": "tune"}).status_code
        == 400
    )


def test_progress_sequences_keep_latest_snapshot_and_do_not_duplicate_log_tails(client):
    _, headers = worker(client)
    queue(client)
    lease = claim(client, headers)
    assert (
        report(
            client, headers, lease, sequence=2, checkpoint="/private/file", token="secret"
        ).status_code
        == 200
    )
    assert report(client, headers, lease, sequence=2, iteration=999).status_code == 200
    assert report(client, headers, lease, sequence=1).status_code == 409
    assert report(client, headers, lease, sequence=3).status_code == 200
    saved = client.get(f"/api/training/jobs/{lease['id']}").json()
    assert saved["logs"] == "latest tail"
    assert saved["progress"]["iteration"] == 3
    assert "checkpoint" not in saved["progress"] and "token" not in saved["progress"]
    assert (
        client.post(
            f"/api/worker/jobs/{lease['id']}/progress",
            headers={**headers, "Content-Type": "application/json"},
            content=json.dumps(
                {
                    "lease_token": lease["lease_token"],
                    "sequence": 4,
                    "progress": {"policy_loss": float("nan")},
                }
            ),
        ).status_code
        == 400
    )


def test_queued_cancel_is_immediate_and_revocation_rejects_active_leases(client):
    remote, headers = worker(client)
    job = queue(client, worker_id=remote["id"])
    cancelled = client.post(f"/api/training/jobs/{job['id']}/cancel", json={})
    assert cancelled.json()["status"] == "cancelled"
    assert client.delete(f"/api/training/jobs/{job['id']}").status_code == 200
    active = queue(client, worker_id=remote["id"])
    lease = claim(client, headers)
    pending = queue(client, worker_id=remote["id"])
    assert client.delete(f"/api/training/workers/{remote['id']}").status_code == 200
    assert report(client, headers, lease).status_code == 403
    assert client.get(f"/api/training/jobs/{active['id']}").json()["status"] == "failed"
    assert client.get(f"/api/training/jobs/{pending['id']}").json()["status"] == "cancelled"


def test_artifacts_are_validated_and_completion_requires_owned_full_state(
    client, artifacts, tmp_path, monkeypatch
):
    _, headers = worker(client)
    queue(client)
    lease = claim(client, headers)
    full, inference = artifacts
    assert report(client, headers, lease).status_code == 200
    wrong = save_checkpoint(tmp_path / "wrong.pt", PolicyValueNet("tablut", 4, 0), {})
    assert upload(client, headers, lease, "inference", wrong).status_code == 400
    assert upload(client, headers, lease, "checkpoint", inference).status_code == 400
    assert finish(client, headers, lease).status_code == 409
    assert upload(client, headers, lease, "checkpoint", full).status_code == 200
    first = upload(client, headers, lease, "inference", inference)
    assert first.status_code == 200, first.text
    assert upload(client, headers, lease, "inference", inference).json() == first.json()
    done = finish(client, headers, lease)
    assert done.status_code == 200, done.text
    assert done.json()["progress"]["status"] == "completed"
    assert finish(client, headers, lease).json() == done.json()
    assert len(client.get("/api/models").json()) == 1
    assert report(client, headers, lease, sequence=99).status_code == 409
    # Cancellation wins even if the worker has already decided to report success.
    queue(client)
    race = claim(client, headers, "cancel-finish-race")
    for kind, path in zip(("checkpoint", "inference"), artifacts):
        assert upload(client, headers, race, kind, path).status_code == 200
    client.post(f"/api/training/jobs/{race['id']}/cancel", json={})
    canonical = finish(client, headers, race)
    assert canonical.status_code == 200 and canonical.json()["status"] == "cancelled"
    assert finish(client, headers, race).json() == canonical.json()
    # A worker must upload the target iteration, not just an earlier full state.
    queue(client, iterations=2)
    partial = claim(client, headers, "incomplete-final-checkpoint")
    for kind, path in zip(("checkpoint", "inference"), artifacts):
        assert upload(client, headers, partial, kind, path).status_code == 200
    assert finish(client, headers, partial).status_code == 409
    client.post(f"/api/training/jobs/{partial['id']}/cancel", json={})
    assert finish(client, headers, partial, "cancelled").status_code == 200
    queue(client)
    invalid = claim(client, headers, "invalid-artifacts")
    corrupt = tmp_path / "corrupt.pt"
    corrupt.write_bytes(b"invalid checkpoint")
    assert upload(client, headers, invalid, "inference", corrupt).status_code == 400
    monkeypatch.setattr("hnefatafl.models.MAX_CHECKPOINT_BYTES", 8)
    assert upload(client, headers, invalid, "checkpoint", corrupt).status_code == 413
    assert not list((client.app.state.data_dir / ".uploads").iterdir())


def test_remote_resume_copies_checkpoint_before_source_training_data_is_deleted(
    client, artifacts, monkeypatch
):
    remote, headers = worker(client)
    job = queue(client, worker_id=remote["id"])
    lease = claim(client, headers)
    for kind, path in zip(("checkpoint", "inference"), artifacts):
        assert upload(client, headers, lease, kind, path).status_code == 200
    assert finish(client, headers, lease).status_code == 200
    next_job = client.post(
        f"/api/training/jobs/{job['id']}/resume",
        json={"iterations": 2, "learning_rate": 0.0002, "weight_decay": 0.02},
    )
    assert next_job.status_code == 201, next_job.text
    assert next_job.json()["execution"] == "remote"
    assert next_job.json()["target_worker_id"] == remote["id"]
    any_worker = client.post(
        f"/api/training/jobs/{job['id']}/resume", json={"iterations": 2, "execution": "remote"}
    )
    assert any_worker.status_code == 201, any_worker.text
    assert any_worker.json()["target_worker_id"] is None

    class CompletedProcess:
        pid = 0
        returncode = 0

        def poll(self):
            return 0

    monkeypatch.setattr(
        "hnefatafl.server.subprocess.Popen", lambda *args, **kwargs: CompletedProcess()
    )
    local = client.post(
        f"/api/training/jobs/{job['id']}/resume", json={"iterations": 2, "execution": "local"}
    )
    assert local.status_code == 201, local.text
    assert local.json()["execution"] == "local" and "worker_id" not in local.json()
    assert client.delete(f"/api/training/jobs/{job['id']}").status_code == 200
    resumed = claim(client, headers, "resume-next")
    assert resumed["resume_available"]
    assert resumed["config"]["learning_rate"] == 0.0002
    assert resumed["config"]["weight_decay"] == 0.02
    assert resumed["config"]["seed"] == job["config"]["seed"]
    assert resumed["config"]["channels"] == job["config"]["channels"]
    downloaded = client.get(
        f"/api/worker/jobs/{resumed['id']}/resume",
        headers={**headers, "X-Lease-Token": resumed["lease_token"]},
    )
    assert downloaded.status_code == 200
    assert downloaded.content == artifacts[0].read_bytes()
    assert "resume" not in resumed["config"]


def test_incremental_history_keeps_all_rows_and_duplicate_reports_are_idempotent(client):
    _, headers = worker(client)
    queue(client, iterations=3000)
    lease = claim(client, headers)
    route = f"/api/worker/jobs/{lease['id']}/progress"
    rows = [{"iteration": index, "policy_loss": 1.2} for index in range(3000)]
    for sequence, offset in enumerate(range(0, len(rows), 500)):
        payload = {
            "lease_token": lease["lease_token"],
            "sequence": sequence,
            "history_offset": offset,
            "progress": {"history": rows[offset : offset + 500]},
        }
        assert client.post(route, headers=headers, json=payload).status_code == 200
        assert client.post(route, headers=headers, json=payload).status_code == 200
    saved = client.get(f"/api/training/jobs/{lease['id']}").json()
    assert saved["progress"]["history"] == rows
    # Recovery from an earlier supervisor cursor must not erase retained suffixes.
    assert (
        client.post(
            route,
            headers=headers,
            json={
                "lease_token": lease["lease_token"],
                "sequence": 10,
                "history_offset": 0,
                "progress": {"history": rows[:10]},
            },
        ).status_code
        == 200
    )
    assert client.get(f"/api/training/jobs/{lease['id']}").json()["progress"]["history"] == rows
    assert (
        client.post(
            route,
            headers=headers,
            json={
                "lease_token": lease["lease_token"],
                "sequence": 11,
                "history_offset": 3001,
                "progress": {"history": []},
            },
        ).status_code
        == 409
    )
