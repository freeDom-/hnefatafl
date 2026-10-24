"""Version gates apply to new assignments, never to an existing valid lease."""

from concurrent.futures import ThreadPoolExecutor
import json
from types import SimpleNamespace

from auth_support import AuthenticatedClient
from fastapi.testclient import TestClient
import pytest

from hnefatafl import server, versioning
from test_remote_api import claim, finish, queue, report, upload, worker

from test_security import account, login

pytest_plugins = ["test_remote_api"]

BUILD = {"package_version": "0.26.0", "git_revision": "a" * 40}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "application_metadata", lambda: dict(BUILD))
    with AuthenticatedClient(server.create_app(tmp_path / "server")) as session:
        yield session


def heartbeat(client, headers, version="0.26.0", **capabilities):
    return client.post(
        "/api/worker/heartbeat",
        headers=headers,
        json={"capabilities": {"package_version": version, **capabilities}},
    )


def override(client, identifier, enabled=True):
    return client.patch(
        f"/api/training/workers/{identifier}/version-override", json={"enabled": enabled}
    )


@pytest.mark.parametrize(
    "version,eligible,code",
    [
        ("0.26.0", True, "compatible"),
        ("1.26.0", False, "application_version_mismatch"),
        ("0.25.0", False, "application_version_mismatch"),
        ("0.26.1", False, "application_version_mismatch"),
        ("0.26.0+different", False, "application_version_mismatch"),
        (None, False, "unknown_application_version"),
        ("", False, "unknown_application_version"),
        ("unknown", False, "unknown_application_version"),
        ("0+local", False, "unknown_application_version"),
        ("0.26", False, "unknown_application_version"),
        ("v0.26.0", False, "unknown_application_version"),
        (26, False, "unknown_application_version"),
        ({"version": "0.26.0"}, False, "unknown_application_version"),
    ],
)
def test_exact_versions_connect_but_only_matching_workers_claim(client, version, eligible, code):
    registered, headers = worker(client, connected=False)
    response = heartbeat(client, headers, version)
    assert response.status_code == 200
    remote = response.json()["worker"]
    assert remote["status"] == "online"
    assert remote["compatibility"]["eligible"] is eligible
    assert remote["compatibility"]["code"] == code
    job = queue(client)
    lease = claim(client, headers)
    assert bool(lease) is eligible
    if eligible:
        saved = client.get(f"/api/training/jobs/{job['id']}").json()
        assert saved["progress"]["status"] == "running"
        assert saved["software"]["worker"] == {"package_version": version, "git_revision": None}
        assert saved["software"]["server"] == BUILD
    else:
        pending = client.get(f"/api/training/jobs/{job['id']}").json()
        assert pending["status"] == "queued"
        assert pending["progress"]["status"] == "queued"
        assert pending["progress"]["queue_reason_code"] == "application_version"
        explicit = client.post(
            "/api/training/jobs",
            json={"execution": "remote", "worker_id": registered["id"], "preset": "smoke"},
        )
        assert explicit.status_code == 409
        assert remote["compatibility"]["reason"] in explicit.text
        assert "0.26.0" in explicit.text


@pytest.mark.parametrize(
    "revision,eligible", [(None, True), ("a" * 40, True), ("b" * 40, False), ("unknown", True)]
)
def test_optional_reliable_git_revisions(client, revision, eligible):
    registered, headers = worker(client)
    remote = heartbeat(client, headers, git_revision=revision).json()["worker"]
    assert remote["compatibility"]["eligible"] is eligible
    if not eligible:
        assert remote["compatibility"]["code"] == "git_revision_mismatch"
        assert override(client, registered["id"]).json()["compatibility"]["override_active"]
    queue(client)
    assert claim(client, headers)


def test_automatic_queue_reasons_hardware_capacity_and_version(client):
    old, old_headers = worker(client)
    heartbeat(client, old_headers, "0.25.0", cuda_available=True)
    compatible, current_headers = worker(client)
    job = queue(client, device="cuda")
    assert claim(client, old_headers) is None
    assert claim(client, current_headers) is None
    assert (
        client.get(f"/api/training/jobs/{job['id']}").json()["progress"]["queue_reason_code"]
        == "application_version"
    )
    heartbeat(client, old_headers, "0.25.0", cuda_available=False)
    assert (
        client.get(f"/api/training/jobs/{job['id']}").json()["progress"]["queue_reason_code"]
        == "hardware"
    )
    heartbeat(client, current_headers, cuda_available=True)
    assert claim(client, current_headers)["id"] == job["id"]
    waiting = queue(client)
    assert claim(client, current_headers, "full") is None
    assert (
        client.get(f"/api/training/jobs/{waiting['id']}").json()["progress"]["queue_reason_code"]
        == "capacity"
    )
    assert compatible["id"] != old["id"]


def test_override_persistence_audit_disable_and_revocation(client):
    registered, headers = worker(client, capacity=2)
    heartbeat(client, headers, "0.25.0", git_revision="b" * 40)
    enabled = override(client, registered["id"]).json()
    assert enabled["compatibility"]["code"] == "override_active"
    assert enabled["version_override_changed_by"]
    with AuthenticatedClient(server.create_app(client.app.state.data_dir)) as restarted:
        persisted = restarted.get("/api/training/workers").json()[0]
        assert persisted["compatibility"]["override_active"]
        queue(restarted)
        lease = claim(restarted, headers)
        assert restarted.get(f"/api/training/jobs/{lease['id']}").json()["software"][
            "version_override_active"
        ]
        disabled = override(restarted, registered["id"], False).json()
        assert not disabled["compatibility"]["eligible"]
        assert [event["enabled"] for event in disabled["version_override_history"]] == [True, False]
        queue(restarted)
        assert claim(restarted, headers, "after-disable") is None
        assert claim(restarted, headers) == lease  # Existing idempotent claim stays valid.
        assert report(restarted, headers, lease).status_code == 200
        override(restarted, registered["id"])
        restarted.delete(f"/api/training/workers/{registered['id']}")
        assert restarted.post("/api/worker/claim", headers=headers, json={}).status_code == 403
        assert override(restarted, registered["id"]).status_code == 404


@pytest.mark.parametrize("protocol", [2, "1", True, None])
def test_overrides_never_bypass_protocol(client, protocol):
    registered, headers = worker(client)
    override(client, registered["id"])
    response = heartbeat(client, headers, "0.25.0", protocol_version=protocol)
    assert response.status_code == 409
    remote = client.get("/api/training/workers").json()[0]
    assert remote["status"] == "online"
    assert remote["compatibility"]["code"] == "incompatible_protocol"
    assert not remote["compatibility"]["eligible"]
    job = queue(client)
    assert (
        client.get(f"/api/training/jobs/{job['id']}").json()["progress"]["queue_reason_code"]
        == "protocol"
    )
    assert client.post("/api/worker/claim", headers=headers, json={}).status_code == 409


def test_override_never_supplies_unknown_versions_or_hardware(client):
    registered, headers = worker(client)
    override(client, registered["id"])
    heartbeat(client, headers, None)
    queue(client)
    assert claim(client, headers) is None
    heartbeat(client, headers, "0.25.0", inference_cache_modes=["off"])
    queue(client, device="cuda")
    queue(client, inference_cache_mode="exact")
    # Consume the ordinary job, but not the hardware/cache jobs.
    assert claim(client, headers)
    assert finish(client, headers, claim(client, headers), "failed").status_code == 200
    assert claim(client, headers, "hardware") is None
    assert (
        client.post(
            "/api/training/jobs",
            json={"execution": "remote", "worker_id": registered["id"], "device": "cuda"},
        ).status_code
        == 400
    )


def test_legacy_empty_heartbeat_does_not_retain_previous_version(client):
    registered, headers = worker(client)
    saved = client.app.state.store.get_worker(registered["id"])
    saved.pop("version_override_enabled", None)
    client.app.state.store.save_worker(saved)
    assert client.post("/api/worker/heartbeat", headers=headers, json={}).status_code == 200
    queue(client)
    assert claim(client, headers) is None
    assert heartbeat(client, headers).json()["worker"]["id"] == registered["id"]
    assert claim(client, headers)


def test_override_requires_admin_session_csrf_and_strict_boolean(client):
    registered, headers = worker(client)
    path = f"/api/training/workers/{registered['id']}/version-override"
    with TestClient(client.app) as anonymous:
        assert anonymous.patch(path, headers=headers, json={"enabled": True}).status_code == 401
        account(client.app, "USER")
        login(anonymous)
        assert anonymous.patch(path, json={"enabled": True}).status_code == 403
    assert (
        client.patch(path, headers={"X-CSRF-Token": "wrong"}, json={"enabled": True}).status_code
        == 403
    )
    for value in [1, "true", None]:
        assert client.patch(path, json={"enabled": value}).status_code == 422
    heartbeat(client, headers, "0.25.0", version_override_enabled=True)
    assert not client.get("/api/training/workers").json()[0]["compatibility"]["eligible"]
    assert override(client, registered["id"]).status_code == 200


def test_two_active_jobs_finish_across_server_upgrade(client, monkeypatch, artifacts):
    leases = []
    for _ in range(2):
        registered, headers = worker(client, capacity=2)
        queue(client, worker_id=registered["id"])
        leases.append((headers, claim(client, headers)))
    queue(client)
    monkeypatch.setattr(
        server,
        "application_metadata",
        lambda: {"package_version": "0.27.0", "git_revision": "b" * 40},
    )
    with AuthenticatedClient(server.create_app(client.app.state.data_dir)) as upgraded:
        for headers, lease in leases:
            assert report(upgraded, headers, lease).status_code == 200
            assert claim(upgraded, headers) == lease
            assert claim(upgraded, headers, "new-after-upgrade") is None
            assert report(upgraded, headers, {**lease, "lease_token": "wrong"}).status_code == 403
            for kind, path in zip(("checkpoint", "inference"), artifacts):
                assert upload(upgraded, headers, lease, kind, path).status_code == 200
            assert finish(upgraded, headers, lease).status_code == 200
            assert claim(upgraded, headers, "after-finish") is None
            saved = upgraded.get(f"/api/training/jobs/{lease['id']}").json()
            assert saved["software"]["server"] == BUILD
            assert saved["software"]["worker"]["package_version"] == "0.26.0"
        assert len(upgraded.get("/api/training/workers").json()) == 2
        headers = leases[0][0]
        assert heartbeat(upgraded, headers, "0.27.0").json()["worker"]["compatibility"]["eligible"]
        assert claim(upgraded, headers, "updated-with-same-token")


def test_heartbeat_claim_race_preserves_claimed_version_snapshot(client):
    registered, headers = worker(client, capacity=16)
    for _ in range(8):
        queue(client)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda index: (
                    heartbeat(client, headers, "0.25.0")
                    if index == 0
                    else client.post(
                        "/api/worker/claim", headers=headers, json={"request_id": "racing"}
                    )
                ),
                range(2),
            )
        )
    assert all(response.status_code == 200 for response in results)
    for job in client.app.state.store.jobs():
        if job["status"] == "running":
            assert job["software"]["worker"]["package_version"] == "0.26.0"
    assert claim(client, headers, "after-heartbeat") is None
    assert not client.app.state.store.get_worker(registered["id"])["version_override_enabled"]


@pytest.mark.parametrize("editable,expected", [(False, "a" * 40), (True, None)])
def test_installed_identity_uses_metadata_without_git(monkeypatch, editable, expected):
    monkeypatch.setattr(versioning, "version", lambda _: "0.26.0")
    direct = {"dir_info": {"editable": editable}, "vcs_info": {"vcs": "git", "commit_id": "a" * 40}}
    monkeypatch.setattr(
        versioning,
        "distribution",
        lambda _: SimpleNamespace(read_text=lambda _: json.dumps(direct)),
    )
    assert versioning.application_metadata() == {
        "package_version": "0.26.0",
        "git_revision": expected,
    }


def test_matching_release_accepts_missing_server_revision(client, monkeypatch):
    _, headers = worker(client)
    heartbeat(client, headers, git_revision="b" * 40)
    monkeypatch.setattr(server, "application_metadata", lambda: {**BUILD, "git_revision": None})
    with AuthenticatedClient(server.create_app(client.app.state.data_dir)) as restarted:
        queue(restarted)
        assert claim(restarted, headers)


def test_unknown_server_identity_cannot_authorize_new_jobs(client, monkeypatch):
    registered, headers = worker(client)
    override(client, registered["id"])
    monkeypatch.setattr(
        server, "application_metadata", lambda: {"package_version": "0+local", "git_revision": None}
    )
    with AuthenticatedClient(server.create_app(client.app.state.data_dir)) as restarted:
        queue(restarted)
        assert claim(restarted, headers) is None
        assert (
            restarted.get("/api/training/workers").json()[0]["compatibility"]["code"]
            == "unknown_application_version"
        )


def test_worker_reports_installed_identity_in_heartbeat(tmp_path, monkeypatch):
    from hnefatafl import remote_worker
    from test_remote_worker import FastStop, ReplyAPI, supervisor

    reports = []

    class API(ReplyAPI):
        def request(self, route, payload=None, **kwargs):
            if route.endswith("/heartbeat"):
                reports.append(payload["capabilities"])
                return {"worker": {"capacity": 1}, "heartbeat_interval": 10}
            return {"job": None}

    monkeypatch.setattr(versioning, "application_metadata", lambda: dict(BUILD))
    supervisor(monkeypatch, API(), None)
    remote_worker.run_worker(
        "https://example.invalid",
        "fixture-token",
        tmp_path,
        once=True,
        should_stop=FastStop(),
        emit=lambda _: None,
    )
    assert reports == [{"protocol_version": 1, **BUILD}]
