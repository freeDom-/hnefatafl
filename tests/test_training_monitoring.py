"""Polling stays small without changing retained jobs or lease reconciliation."""

import json
from pathlib import Path

import pytest
from auth_support import AuthenticatedClient

from hnefatafl.server import create_app


@pytest.fixture
def client(tmp_path):
    with AuthenticatedClient(create_app(tmp_path / "server")) as session:
        yield session


def saved_job(identifier, *, execution="remote", status="completed"):
    history = [
        {"iteration": index, "policy_loss": 0.2, "payload": "archived_only" * 100}
        for index in range(100)
    ]
    return {
        "id": identifier,
        "kind": "train",
        "status": status,
        "execution": execution,
        "created_at": "2026-10-30T10:00:00Z",
        "config": {"model_name": "Saved strategist", "ruleset": "brandubh", "iterations": 100},
        "lease_hash": "private-lease-hash",
        "lease_expires": 10**12,
        "progress": {
            "iteration": 100,
            "history": history,
            "metrics": {"policy_loss": 0.2, "self_play": [{"plies": 4}] * 50},
        },
        "result": {"status": status, "history": history, "model": {"id": "a" * 24}},
        "logs": "worker log\n" * 1000,
    }


def test_compact_list_retains_full_detail_and_does_not_mutate_saved_job(client):
    store = client.app.state.store
    job = saved_job("a" * 32)
    store.save_job(job)
    original = store.get_job(job["id"])
    compact = client.get("/api/training/jobs?summary=true")
    full = client.get(f"/api/training/jobs/{job['id']}")
    assert compact.status_code == full.status_code == 200
    item = compact.json()[0]
    assert item["summary"] is True
    assert item["progress"]["iteration"] == 100
    assert item["progress"]["metrics"]["policy_loss"] == 0.2
    assert item["model_id"] == "a" * 24
    for key in ("logs", "result", "lease_hash"):
        assert key not in item
    assert "history" not in item["progress"]
    assert "self_play" not in item["progress"]["metrics"]
    assert full.json()["progress"]["history"] == job["progress"]["history"]
    assert len(compact.content) < len(full.content) / 50
    assert store.get_job(job["id"]) == original
    assert (
        client.get("/api/training/jobs").json()[0]["progress"]["history"]
        == job["progress"]["history"]
    )


def test_archived_local_summaries_do_not_read_all_progress_results_and_logs(client, monkeypatch):
    job = saved_job("b" * 32, execution="local")
    store = client.app.state.store
    store.save_job(job)
    directory = client.app.state.data_dir / "jobs" / job["id"]
    directory.mkdir()
    for name in ("progress.json", "result.json", "worker.log"):
        (directory / name).write_text("large archived artifact")
    original_open = Path.open

    def guarded(path, *args, **kwargs):
        if path.parent == directory and path.name in {"progress.json", "result.json", "worker.log"}:
            raise AssertionError("Listing archived jobs must not load their artifacts")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded)
    response = client.get("/api/training/jobs?summary=true")
    assert response.status_code == 200
    assert response.json()[0]["progress"]["iteration"] == 100


def test_live_local_summary_reads_fresh_progress_without_writing_the_job_on_every_poll(
    client, monkeypatch
):
    job = saved_job("c" * 32, execution="local", status="running")
    job["pid"] = 424242
    store = client.app.state.store
    store.save_job(job)
    directory = client.app.state.data_dir / "jobs" / job["id"]
    directory.mkdir()
    (directory / "progress.json").write_text(json.dumps({"iteration": 101, "history": []}))
    original = Path.read_bytes

    def command(path):
        if str(path) == "/proc/424242/cmdline":
            return b"hnefatafl.cli\0" + str(directory / "config.json").encode()
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", command)

    def forbidden(*args):
        raise AssertionError("Unchanged live status must not rewrite the database")

    monkeypatch.setattr(store, "save_job", forbidden)
    response = client.get("/api/training/jobs?summary=true")
    assert response.status_code == 200
    assert response.json()[0]["progress"]["iteration"] == 101
    assert store.get_job(job["id"])["progress"]["iteration"] == 100


def test_active_lease_queries_and_worker_counts_do_not_decode_archived_histories(
    client, monkeypatch
):
    store = client.app.state.store
    archived = saved_job("d" * 32)
    store.save_job(archived)
    for index, status in enumerate(("running", "cancelling", "queued")):
        job = saved_job(str(index) * 32, status=status)
        job["progress"] = {}
        job.pop("result")
        job["worker_id"] = "worker-one"
        store.save_job(job)
    original = json.loads

    def decode(value, *args, **kwargs):
        if isinstance(value, str) and "archived_only" in value:
            raise AssertionError("Active lease queries must not decode archived jobs")
        return original(value, *args, **kwargs)

    monkeypatch.setattr(json, "loads", decode)
    assert len(store.jobs(statuses=("running", "cancelling"))) == 2
    assert store.active_worker_jobs() == {"worker-one": 2}


def test_summary_recovers_a_late_local_result_once_without_losing_history(client, monkeypatch):
    job = saved_job("f" * 32, execution="local")
    result = job.pop("result")
    result["self_play_games"] = {"version": 1, "job": 8, "cumulative": 8}
    store = client.app.state.store
    store.save_job(job)
    directory = client.app.state.data_dir / "jobs" / job["id"]
    directory.mkdir()
    artifact = directory / "result.json"
    artifact.write_text(json.dumps(result))
    item = client.get("/api/training/jobs?summary=true").json()[0]
    assert item["model_id"] == "a" * 24
    assert item["progress"]["self_play_games"]["job"] == 8
    retained = store.get_job(job["id"])
    assert retained["progress"]["history"] == job["progress"]["history"]
    assert retained["result"] == result
    original = Path.read_text

    def guard(path, *args, **kwargs):
        if path == artifact:
            raise AssertionError("A recovered terminal artifact should be cached in the row")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guard)
    assert client.get("/api/training/jobs?summary=true").status_code == 200


def test_summary_preserves_result_only_counters_and_current_progress_precedence(client):
    job = saved_job("e" * 32)
    job["result"]["self_play_games"] = {"version": 1, "job": 7, "cumulative": 18}
    job["result"]["games_generated"] = 18
    client.app.state.store.save_job(job)
    item = client.get("/api/training/jobs?summary=true").json()[0]
    assert item["progress"]["self_play_games"] == {"version": 1, "job": 7, "cumulative": 18}
    assert item["progress"]["games_generated"] == 18
    job["progress"]["games_generated"] = 20
    client.app.state.store.save_job(job)
    assert (
        client.get("/api/training/jobs?summary=true").json()[0]["progress"]["games_generated"] == 20
    )
