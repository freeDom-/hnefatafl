"""Catalog labels and training artifacts have independent stable lifecycles."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
from pathlib import Path
import threading
import time

import pytest
from auth_support import AuthenticatedClient as TestClient

from hnefatafl.models import ModelRegistry, PolicyValueNet, load_checkpoint, save_checkpoint
from hnefatafl.server import create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "server")) as session:
        yield session


def saved_job(client, identifier="a" * 32, **changes):
    directory = client.app.state.data_dir / "jobs" / identifier
    directory.mkdir(exist_ok=True)
    (directory / "worker.log").write_text("Retained worker log")
    job = {
        "id": identifier,
        "status": "completed",
        "kind": "train",
        "created_at": "now",
        "config": {"preset": "smoke", "ruleset": "brandubh"},
        **changes,
    }
    client.app.state.store.save_job(job)
    return job, directory


def wait_stopped(client, identifier):
    deadline = time.monotonic() + 35
    while time.monotonic() < deadline:
        response = client.get(f"/api/training/jobs/{identifier}")
        assert response.status_code == 200, response.text
        job = response.json()
        if job["status"] not in {"running", "cancelling"}:
            return job
        time.sleep(0.05)
    pytest.fail("Training worker did not stop")


def model_game(client, model):
    response = client.post(
        "/api/games",
        json={
            "ruleset": "brandubh",
            "mode": "ai-ai",
            "players": {
                side: {
                    "name": model["name"],
                    "profile_id": "neural",
                    "config": {"model_id": model["id"], "iterations": 1, "time_limit": 0.1},
                }
                for side in ("attackers", "defenders")
            },
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_rename_changes_only_catalog_label_and_preserves_reimport_and_history(client, tmp_path):
    source = save_checkpoint(
        tmp_path / "source.pt", PolicyValueNet("brandubh", 4, 0), {"name": "Ulf"}
    )
    registry = ModelRegistry(client.app.state.data_dir / "models")
    original = registry.register(source)
    before = client.get("/api/models").json()[0]
    game = model_game(client, before)
    payload_before = client.app.state.store.get_game(game["id"])
    digest = hashlib.sha256(Path(original["path"]).read_bytes()).hexdigest()
    renamed = client.patch(f"/api/models/{original['id']}", json={"name": "  Ulf the Wise  "})
    assert renamed.status_code == 200, renamed.text
    after = renamed.json()
    assert after["name"] == "Ulf the Wise"
    assert after["id"] == before["id"] and after["rune"] == before["rune"]
    assert hashlib.sha256(Path(after["path"]).read_bytes()).hexdigest() == digest
    assert load_checkpoint(after["path"])[1]["metadata"]["name"] == "Ulf"
    assert client.app.state.store.get_game(game["id"]) == payload_before
    assert (
        client.get(f"/api/games/{game['id']}").json()["players"]["attackers"]["model_name"] == "Ulf"
    )
    assert registry.import_model(source)["name"] == "Ulf the Wise"
    exported = client.get(f"/api/models/{original['id']}/export")
    assert hashlib.sha256(exported.content).hexdigest() == digest
    assert client.get("/api/models").json()[0]["name"] == "Ulf the Wise"


@pytest.mark.parametrize(
    "body", [{"name": ""}, {"name": " "}, {"name": "x" * 101}, {"name": "Ulf", "id": "bad"}]
)
def test_rename_rejects_invalid_names_without_changing_catalog(client, tmp_path, body):
    registry = ModelRegistry(client.app.state.data_dir / "models")
    source = save_checkpoint(tmp_path / "source.pt", PolicyValueNet("brandubh", 4, 0), {})
    model = registry.register(source, "Ulf")
    before = registry.index_path.read_bytes()
    response = client.patch(f"/api/models/{model['id']}", json=body)
    assert response.status_code in {400, 422}
    assert registry.index_path.read_bytes() == before
    assert client.patch(f"/api/models/{'0' * 24}", json={"name": "Missing"}).status_code == 404


def test_rename_retains_catalog_and_weights_if_atomic_index_write_fails(
    client, tmp_path, monkeypatch
):
    registry = ModelRegistry(client.app.state.data_dir / "models")
    source = save_checkpoint(tmp_path / "source.pt", PolicyValueNet("brandubh", 4, 0), {})
    model = registry.register(source, "Ulf")
    before = registry.index_path.read_bytes()

    def fail_write(self, entries):
        raise OSError("Disk full")

    monkeypatch.setattr(ModelRegistry, "_write_index", fail_write)
    response = client.patch(f"/api/models/{model['id']}", json={"name": "Freydis"})
    assert response.status_code == 500
    assert registry.index_path.read_bytes() == before
    assert Path(model["path"]).read_bytes() == source.read_bytes()


def test_deleting_real_training_keeps_models_history_and_legal_neural_play(client):
    response = client.post(
        "/api/training/jobs",
        json={"preset": "smoke", "ruleset": "brandubh", "device": "cpu", "model_name": "Freydis"},
    )
    assert response.status_code == 201, response.text
    source = wait_stopped(client, response.json()["id"])
    assert source["status"] == "completed", source
    model = client.get("/api/models").json()[0]
    game = model_game(client, model)
    stored = client.app.state.store.get_game(game["id"])
    registered = Path(model["path"])
    original = registered.read_bytes()
    root = client.app.state.data_dir
    source_directory = root / "jobs" / source["id"]
    assert (source_directory / "last.pt").is_file()
    resumed = client.post(f"/api/training/jobs/{source['id']}/resume", json={"iterations": 1000})
    assert resumed.status_code == 201, resumed.text
    child_id = resumed.json()["id"]
    assert client.delete(f"/api/training/jobs/{child_id}").status_code == 409
    # A new worker may not have loaded its source checkpoint yet.
    assert client.delete(f"/api/training/jobs/{source['id']}").status_code == 409
    assert source_directory.exists()
    assert client.post(f"/api/training/jobs/{child_id}/cancel", json={}).status_code == 200
    child = wait_stopped(client, child_id)
    assert child["status"] == "cancelled", child
    assert client.delete(f"/api/training/jobs/{child_id}").status_code == 200
    assert client.delete(f"/api/training/jobs/{source['id']}").json() == {
        "deleted": True,
        "job_id": source["id"],
    }
    assert not source_directory.exists()
    assert client.get("/api/training/jobs").json() == []
    assert client.get(f"/api/training/jobs/{source['id']}").status_code == 404
    assert client.get(f"/api/training/jobs/{source['id']}/resume").status_code == 404
    assert registered.read_bytes() == original
    assert client.app.state.store.get_game(game["id"]) == stored
    assert model in client.get("/api/models").json()
    played = client.post(f"/api/games/{game['id']}/ai-move", json={"version": game["version"]})
    assert played.status_code == 200, played.text
    assert played.json()["ply"] == 1


@pytest.mark.parametrize("status", ["pending", "queued"])
def test_delete_blocks_jobs_that_have_not_stopped(client, status):
    job, directory = saved_job(client, status=status)
    assert client.delete(f"/api/training/jobs/{job['id']}").status_code == 409
    assert directory.exists()
    assert client.app.state.store.get_job(job["id"])


def test_delete_checks_live_worker_even_when_saved_status_is_stale(client, monkeypatch):
    job, directory = saved_job(client, pid=123456789)
    real_read = Path.read_bytes

    def read_command(path):
        if path == Path("/proc/123456789/cmdline"):
            return (
                b"python\0-m\0hnefatafl.cli\0train\0--config\0"
                + str(directory / "config.json").encode()
            )
        return real_read(path)

    monkeypatch.setattr(Path, "read_bytes", read_command)
    assert client.delete(f"/api/training/jobs/{job['id']}").status_code == 409
    assert directory.exists()
    assert client.app.state.store.get_job(job["id"])["status"] == "completed"


def test_delete_retains_shared_optimization_study_until_dependents_are_removed(client):
    source, directory = saved_job(client, kind="tune")
    database = directory / "study.sqlite3"
    database.write_bytes(b"retained study")
    dependent, other = saved_job(
        client, "b" * 32, kind="tune", config={"storage": "sqlite:///" + str(database)}
    )
    response = client.delete(f"/api/training/jobs/{source['id']}")
    assert response.status_code == 409
    assert "dependent optimization job" in response.json()["detail"]
    assert database.read_bytes() == b"retained study"
    assert client.delete(f"/api/training/jobs/{dependent['id']}").status_code == 200
    assert not other.exists()
    assert database.is_file()
    assert client.delete(f"/api/training/jobs/{source['id']}").status_code == 200
    assert not directory.exists()


def test_delete_refuses_symlinked_job_directory_but_does_not_follow_internal_links(
    client, tmp_path
):
    job, directory = saved_job(client)
    outside = tmp_path / "outside"
    outside.mkdir()
    precious = outside / "last.pt"
    precious.write_bytes(b"external checkpoint")
    (directory / "worker.log").unlink()
    directory.rmdir()
    directory.symlink_to(outside, target_is_directory=True)
    assert client.delete(f"/api/training/jobs/{job['id']}").status_code == 409
    assert precious.read_bytes() == b"external checkpoint"
    directory.unlink()
    directory.mkdir()
    (directory / "external.pt").symlink_to(precious)
    assert client.delete(f"/api/training/jobs/{job['id']}").status_code == 200
    assert precious.read_bytes() == b"external checkpoint"


def test_cleanup_failure_keeps_job_available_for_retry(client, monkeypatch):
    job, directory = saved_job(client)
    import shutil

    real_remove = shutil.rmtree

    def failed(path):
        raise OSError("Permission denied")

    monkeypatch.setattr("hnefatafl.server.shutil.rmtree", failed)
    response = client.delete(f"/api/training/jobs/{job['id']}")
    assert response.status_code == 500
    assert client.get(f"/api/training/jobs/{job['id']}").status_code == 200
    assert (directory / "worker.log").is_file()
    monkeypatch.setattr("hnefatafl.server.shutil.rmtree", real_remove)
    assert client.delete(f"/api/training/jobs/{job['id']}").status_code == 200
    assert not directory.exists()
    assert client.delete(f"/api/training/jobs/{job['id']}").status_code == 404
    assert client.delete("/api/training/jobs/not-a-valid-id").status_code == 404


def test_training_list_cannot_reinsert_a_job_during_deletion(client, monkeypatch):
    job, directory = saved_job(client)
    entered = threading.Event()
    release = threading.Event()
    store = client.app.state.store
    original = store.jobs

    def paused():
        jobs = original()
        if not entered.is_set():
            entered.set()
            assert release.wait(5)
        return jobs

    monkeypatch.setattr(store, "jobs", paused)
    with ThreadPoolExecutor(max_workers=2) as pool:
        listing = pool.submit(client.get, "/api/training/jobs")
        assert entered.wait(5)
        deletion = pool.submit(client.delete, f"/api/training/jobs/{job['id']}")
        release.set()
        # The list reconciles its snapshot while holding the same lock as
        # deletion. It returns a valid record, never a stale-read 404 or a
        # resurrected worker status after deletion.
        assert [entry["id"] for entry in listing.result().json()] == [job["id"]]
        assert deletion.result().status_code == 200
    assert not directory.exists()
    assert store.get_job(job["id"]) is None
