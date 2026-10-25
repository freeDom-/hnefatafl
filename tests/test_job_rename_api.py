"""Training-job display-name rename: persistence, validation and authorization.

Renaming changes display metadata only; stable IDs, paths, checkpoints,
parameters, worker assignments and execution state must be untouched.
"""

import pytest
from fastapi.testclient import TestClient as RawClient
from auth_support import AuthenticatedClient as TestClient

from hnefatafl.server import create_app

PASSWORD = "only-a-test-password-123"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "hnefatafl.server.application_metadata",
        lambda: {"package_version": "0.30.0", "git_revision": None},
    )
    with TestClient(create_app(tmp_path / "server")) as session:
        yield session


def queued_job(client, **overrides):
    response = client.post(
        "/api/training/jobs",
        json={"execution": "remote", "preset": "smoke", "device": "cpu", **overrides},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_rename_persists_and_preserves_execution_metadata(client):
    created = queued_job(client)
    job_id = created["id"]
    assert not created.get("name")
    renamed = client.patch(f"/api/training/jobs/{job_id}", json={"name": "  Raven campaign  "})
    assert renamed.status_code == 200, renamed.text
    body = renamed.json()
    assert body["name"] == "Raven campaign"
    # Display metadata only: identity, configuration, status and worker target.
    assert body["id"] == job_id
    assert body["config"] == created["config"]
    assert body["status"] == created["status"]
    assert body["execution"] == created["execution"]
    assert body.get("target_worker_id") == created.get("target_worker_id")
    # Persisted and visible in the collection without restarting anything.
    assert client.get(f"/api/training/jobs/{job_id}").json()["name"] == "Raven campaign"
    listed = next(job for job in client.get("/api/training/jobs").json() if job["id"] == job_id)
    assert listed["name"] == "Raven campaign"
    # A later rename replaces the display name without duplicating anything.
    again = client.patch(f"/api/training/jobs/{job_id}", json={"name": "Second name"}).json()
    assert again["name"] == "Second name"
    assert again["id"] == job_id


@pytest.mark.parametrize("name", ["", "   ", "\t\n", "x" * 101])
def test_rename_rejects_invalid_names_and_keeps_the_previous_one(client, name):
    created = queued_job(client)
    job_id = created["id"]
    assert client.patch(f"/api/training/jobs/{job_id}", json={"name": "Kept"}).status_code == 200
    rejected = client.patch(f"/api/training/jobs/{job_id}", json={"name": name})
    assert rejected.status_code in {400, 422}
    assert client.get(f"/api/training/jobs/{job_id}").json()["name"] == "Kept"


def test_rename_unknown_job_returns_not_found(client):
    response = client.patch("/api/training/jobs/" + "0" * 32, json={"name": "Ghost"})
    assert response.status_code == 404


def test_rename_requires_authentication_and_training_permission(tmp_path):
    app = create_app(tmp_path / "auth")
    app.state.auth.create_account("Raven", "raven@example.test", PASSWORD, "USER")
    with RawClient(app) as anonymous:
        assert (
            anonymous.patch("/api/training/jobs/" + "0" * 32, json={"name": "Nope"}).status_code
            == 401
        )
    with RawClient(app) as user:
        login = user.post(
            "/api/auth/login",
            json={"identity": "Raven", "password": PASSWORD},
            headers={"Origin": "http://testserver"},
        )
        assert login.status_code == 200, login.text
        user.headers.update(
            {"Origin": "http://testserver", "X-CSRF-Token": login.json()["csrf_token"]}
        )
        assert (
            user.patch("/api/training/jobs/" + "0" * 32, json={"name": "Nope"}).status_code == 403
        )
