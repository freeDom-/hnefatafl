"""Remote training crosses the actual worker/server boundary without shared files."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient

from hnefatafl.models import load_checkpoint
from hnefatafl.server import create_app


PROJECT = Path(__file__).resolve().parents[1]


def worker(client, name, capacity=1):
    response = client.post("/api/training/workers", json={"name": name, "capacity": capacity})
    assert response.status_code == 201, response.text
    created = response.json()
    return created["worker"], {"Authorization": "Bearer " + created["token"]}


def queued(client, name, **changes):
    response = client.post(
        "/api/training/jobs",
        json={
            "execution": "remote",
            "preset": "smoke",
            "ruleset": "brandubh",
            "model_name": name,
            "device": "cpu",
            "cpu_threads": 1,
            "max_game_plies": 2,
            "evaluation_max_plies": 2,
            **changes,
        },
    )
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["status"] == "queued"
    return result


def claim(client, headers):
    response = client.post("/api/worker/claim", headers=headers, json={"request_id": uuid4().hex})
    assert response.status_code == 200, response.text
    return response.json()["job"]


def report(client, headers, lease, sequence=1, **changes):
    return client.post(
        f"/api/worker/jobs/{lease['id']}/progress",
        headers=headers,
        json={
            "lease_token": lease["lease_token"],
            "sequence": sequence,
            "progress": {
                "stage": "iteration_complete",
                "iteration": 1,
                "total_iterations": 3,
                "history": [{"iteration": 1, "policy_loss": 1.25, "value_loss": 0.5}],
                **changes,
            },
        },
    )


def test_independent_workers_keep_claims_progress_and_leases_across_server_restart(tmp_path):
    root = tmp_path / "server"
    with TestClient(create_app(root)) as client:
        first_worker, first_headers = worker(client, "Cloud raven")
        second_worker, second_headers = worker(client, "Cloud wolf")
        first = queued(client, "Ulf remote", worker_id=first_worker["id"], iterations=3)
        second = queued(client, "Freydis remote", worker_id=second_worker["id"], iterations=3)
        with ThreadPoolExecutor(max_workers=2) as pool:
            claims = list(
                pool.map(lambda headers: claim(client, headers), [first_headers, second_headers])
            )
        first_lease, second_lease = claims
        assert [entry["id"] for entry in claims] == [first["id"], second["id"]]
        assert claim(client, first_headers) is None
        assert claim(client, second_headers) is None
        for headers, lease in zip((first_headers, second_headers), claims):
            assert report(client, headers, lease).status_code == 200
        # A valid worker token cannot report for a different worker's claim.
        assert report(client, second_headers, first_lease).status_code in {403, 409}
        public = client.get("/api/training/jobs").json()
        assert all(entry["status"] == "running" for entry in public)
        assert all(entry["progress"]["history"][0]["policy_loss"] == 1.25 for entry in public)
        assert "lease_token" not in json.dumps(public)
        assert "token" not in json.dumps(client.get("/api/training/workers").json())

    with TestClient(create_app(root)) as restarted:
        retained = restarted.get(f"/api/training/jobs/{first['id']}").json()
        assert retained["status"] == "running"
        assert retained["progress"]["iteration"] == 1
        assert (
            report(restarted, first_headers, first_lease, sequence=2, iteration=2).status_code
            == 200
        )
        cancelled = restarted.post(f"/api/training/jobs/{first['id']}/cancel", json={})
        assert cancelled.status_code == 200
        notice = report(restarted, first_headers, first_lease, sequence=3, iteration=2)
        assert notice.status_code == 200 and notice.json()["cancel_requested"]
        finished = restarted.post(
            f"/api/worker/jobs/{first['id']}/finish",
            headers=first_headers,
            json={"lease_token": first_lease["lease_token"], "status": "cancelled"},
        )
        assert finished.status_code == 200, finished.text
        assert restarted.get(f"/api/training/jobs/{first['id']}").json()["status"] == "cancelled"
        assert restarted.get(f"/api/training/jobs/{second['id']}").json()["status"] == "running"
        assert second_lease["lease_token"] != first_lease["lease_token"]


def test_expired_worker_cannot_rewrite_retained_progress_or_complete_a_stale_job(
    tmp_path, monkeypatch
):
    from hnefatafl import remote_training

    with TestClient(create_app(tmp_path / "server")) as client:
        _, headers = worker(client, "Lost connection")
        job = queued(client, "Retain this progress")
        lease = claim(client, headers)
        assert report(client, headers, lease).status_code == 200
        old_time = remote_training.timestamp()
        monkeypatch.setattr(remote_training, "timestamp", lambda: old_time + 1000)
        expired = client.get(f"/api/training/jobs/{job['id']}").json()
        assert expired["status"] == "failed", expired
        assert expired["progress"]["status"] == "failed"
        assert expired["progress"]["iteration"] == 1
        assert report(client, headers, lease, sequence=2, iteration=999).status_code in {403, 409}
        late_finish = client.post(
            f"/api/worker/jobs/{job['id']}/finish",
            headers=headers,
            json={"lease_token": lease["lease_token"], "status": "completed"},
        )
        assert late_finish.status_code in {403, 409}
        assert client.get(f"/api/training/jobs/{job['id']}").json()["progress"]["iteration"] == 1


@contextmanager
def live_server(directory):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    environment = {
        **os.environ,
        "HNEFATAFL_DATA_DIR": str(directory),
        "OMP_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
    }
    log = directory.parent / "server.log"
    with log.open("wb") as output:
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "hnefatafl.server:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=PROJECT,
            env=environment,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=20) as client:
                deadline = time.monotonic() + 20
                while True:
                    try:
                        if client.get("/api/health").status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    assert process.poll() is None, log.read_text()
                    assert time.monotonic() < deadline, log.read_text()
                    time.sleep(0.05)
                yield client, f"http://127.0.0.1:{port}"
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)


def wait_completed(client, identifier, worker_process, logfile, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/training/jobs/{identifier}")
        assert response.status_code == 200, response.text
        job = response.json()
        assert job["status"] not in {"failed", "cancelled"}, (job, logfile.read_text())
        if job["status"] == "completed":
            return job
        assert worker_process.poll() is None, logfile.read_text()
        time.sleep(0.1)
    raise AssertionError(("Real remote training did not complete", logfile.read_text()))


def test_real_remote_worker_trains_two_models_concurrently_uploads_and_resumes(tmp_path):
    server_data = tmp_path / "server"
    worker_data = tmp_path / "cloud"
    logfile = tmp_path / "worker.log"
    with live_server(server_data) as (client, url):
        remote, headers = worker(client, "Two-slot CPU cloud", capacity=2)
        first = queued(
            client, "Cloud Ulf", seed=17, worker_id=remote["id"], optimizer="sgd", momentum=0.7
        )
        second = queued(
            client,
            "Cloud Freydis",
            seed=23,
            worker_id=remote["id"],
            evaluation_opponent="shield_maiden",
        )
        environment = {
            **os.environ,
            "HNEFATAFL_WORKER_TOKEN": headers["Authorization"][7:],
            "OMP_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
        }
        with logfile.open("wb") as output:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "hnefatafl.cli",
                    "worker",
                    "--server",
                    url,
                    "--work-dir",
                    str(worker_data),
                    "--concurrency",
                    "2",
                    "--max-jobs",
                    "3",
                ],
                cwd=PROJECT,
                env=environment,
                stdout=output,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            try:
                # Both claims are live before any expensive torch import/training finishes.
                deadline = time.monotonic() + 30
                while True:
                    statuses = [
                        client.get(f"/api/training/jobs/{job['id']}").json()["status"]
                        for job in (first, second)
                    ]
                    if statuses == ["running", "running"]:
                        break
                    assert time.monotonic() < deadline and process.poll() is None, (
                        statuses,
                        logfile.read_text(),
                    )
                    time.sleep(0.05)
                first_done = wait_completed(client, first["id"], process, logfile)
                second_done = wait_completed(client, second["id"], process, logfile)
                assert first_done["progress"]["history"] and second_done["progress"]["history"]
                maiden_evaluation = second_done["progress"]["metrics"]["evaluation"]
                assert maiden_evaluation["opponent"] == "shield_maiden"
                assert maiden_evaluation["opponent_config"]["algorithm"] == "shield_maiden"
                assert maiden_evaluation["opponent_config"]["heuristic_version"] == 1
                models = client.get("/api/models").json()
                selected = next(model for model in models if model["name"] == "Cloud Ulf")
                assert {model["name"] for model in models} == {"Cloud Ulf", "Cloud Freydis"}
                assert all(model["metrics"]["updates"] > 0 for model in models)
                checkpoint = server_data / "jobs" / first["id"] / "last.pt"
                _, payload = load_checkpoint(checkpoint)
                assert payload["optimizer_state"] is not None
                assert payload["extra"]["replay"]
                resumed_response = client.post(
                    f"/api/training/jobs/{first['id']}/resume",
                    json={
                        "iterations": 2,
                        "learning_rate": 0.0002,
                        "weight_decay": 0.02,
                        "momentum": 0.4,
                        "evaluation_opponent": "champion",
                        "evaluation_frequency": 10,
                    },
                )
                assert resumed_response.status_code == 201, resumed_response.text
                resumed = wait_completed(client, resumed_response.json()["id"], process, logfile)
                assert [point["iteration"] for point in resumed["progress"]["history"]] == [1, 2]
                _, resumed_checkpoint = load_checkpoint(
                    server_data / "jobs" / resumed["id"] / "last.pt"
                )
                group = resumed_checkpoint["optimizer_state"]["param_groups"][0]
                assert (group["lr"], group["weight_decay"], group["momentum"]) == (
                    0.0002,
                    0.02,
                    0.4,
                )
                assert resumed_checkpoint["metadata"]["training_config"]["seed"] == 17
                assert resumed_checkpoint["extra"]["replay"]
                assert resumed["progress"]["metrics"]["updates"] > 0
                assert resumed["progress"]["metrics"]["evaluation"]["opponent"] == "champion"
                assert resumed_checkpoint["extra"]["evaluation_state"]["champion"]["iteration"] == 1
                assert "previous_evaluation" not in resumed["progress"]["metrics"]
                for worker_log in worker_data.rglob("worker.log"):
                    assert worker_log.stat().st_size < 100_000
                    assert '"history"' not in worker_log.read_text()

                process.wait(timeout=30)
                assert process.returncode == 0, logfile.read_text()
                # The server owns imported copies; inference needs no cloud filesystem.
                assert Path(selected["path"]).is_relative_to(server_data)
                import shutil

                shutil.rmtree(worker_data)
                game = client.post(
                    "/api/games",
                    json={
                        "ruleset": "brandubh",
                        "mode": "ai-ai",
                        "players": {
                            side: {
                                "kind": "ai",
                                "profile_id": "neural",
                                "config": {
                                    "model_id": selected["id"],
                                    "iterations": 1,
                                    "time_limit": 0.1,
                                },
                            }
                            for side in ("attackers", "defenders")
                        },
                    },
                )
                assert game.status_code == 201, game.text
                move = client.post(
                    f"/api/games/{game.json()['id']}/ai-move",
                    json={"version": game.json()["version"]},
                )
                assert move.status_code == 200 and move.json()["ply"] == 1, move.text
                assert headers["Authorization"][7:] not in logfile.read_text()
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=20)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait(timeout=5)
