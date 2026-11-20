"""Real lifespan/file-lock integration for the production updater sequence.

These tests use disposable data and an in-process TestClient transport. They do
not touch systemd, real releases or production data.
"""

import json
import sqlite3
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlparse

import pytest
from fastapi.testclient import TestClient

from hnefatafl.auth import Auth, SecuritySettings
from hnefatafl.deployment import Deployment, ReadinessError, atomic_json
from hnefatafl.online_settings import OnlineSettings
from hnefatafl.server import create_app
from hnefatafl.storage import Store

PASSWORD = "deployment-lifecycle-password"
POLICY = OnlineSettings(
    enabled=True,
    creation_policy_approved=True,
    retain_seat_without_verification=True,
    timeout_policy_approved=True,
)


class LiveResponse:
    def __init__(self, response):
        self.response, self.status = response, response.status_code

    def read(self, limit=-1):
        content = self.response.content
        return content if limit < 0 else content[:limit]

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class LiveDeployment(Deployment):
    """Updater sequence with real data/backup/lifespan and fake systemd."""

    def __init__(self, root: Path):
        super().__init__(
            {"port": 8000, "public_url": "https://example.test"},
            base=root / "opt",
            data=root / "data",
            etc=root / "etc",
            unit_file=root / "unit.service",
        )
        self.actions = []
        self.client_context = None
        self.client = None
        self.app = None
        self.fail_probe_version = None
        self.releases.mkdir(parents=True)
        self.data.mkdir()
        self.etc.mkdir()
        (self.etc / "application.env").write_text(
            "HNEFATAFL_ENV=production\n"
            "HNEFATAFL_PUBLIC_URL=https://example.test\n"
            "HNEFATAFL_PORT=8000\n"
            "HNEFATAFL_ALLOWED_HOSTS=example.test,testserver\n"
            "HNEFATAFL_TRUSTED_PROXIES=10.0.0.2\n"
        )

    def release(self, tag: str) -> Path:
        target = self.releases / tag
        (target / "deployment").mkdir(parents=True)
        (target / "deployment/hnefatafl.service").write_text("service for " + tag)
        atomic_json(target / ".release.json", {"version": tag})
        return target

    def run(self, args, **kwargs):
        command = [str(value) for value in args]
        self.actions.append(command)
        if "migrate" in command:
            Store(self.data / "hnefatafl.sqlite3")
        elif "init-admin" in command:
            store = Store(self.data / "hnefatafl.sqlite3")
            auth = Auth(store, SecuritySettings())
            with store.connection() as c:
                existing = c.execute("SELECT 1 FROM accounts WHERE role='ADMIN'").fetchone()
            if not existing:
                auth.create_account("Odin", "odin@example.test", PASSWORD, "ADMIN", bootstrap=True)

    def service(self, action):
        self.actions.append("service:" + action)
        if action == "stop":
            self.stop_application()
        elif action == "start":
            self.start_application()

    def start_application(self):
        if self.client is not None:
            return
        self.app = create_app(
            self.data,
            online_settings=POLICY,
            online_background=True,
            account_background=False,
        )
        self.client_context = TestClient(self.app, base_url=self.config["public_url"])
        self.client = self.client_context.__enter__()

    def stop_application(self):
        if self.client_context is None:
            return
        try:
            self.client_context.__exit__(None, None, None)
        finally:
            self.client_context = None
            self.client = None
            self.app = None

    def fetch_url(self, request, *, timeout):
        assert self.client is not None
        parsed = urlparse(request.full_url)
        response = self.client.request(
            request.get_method(), parsed.path, headers=dict(request.header_items())
        )
        if response.status_code >= 400:
            raise HTTPError(
                request.full_url,
                response.status_code,
                "readiness probe failed",
                response.headers,
                None,
            )
        return LiveResponse(response)

    def probe(self):
        if self.fail_probe_version:
            current = self.current.resolve() if self.current.is_symlink() else None
            if current is not None:
                metadata = json.loads((current / ".release.json").read_text())
                if metadata.get("version") == self.fail_probe_version:
                    raise ReadinessError("synthetic target readiness failure")
        return super().probe()


@pytest.fixture
def lifecycle(tmp_path, monkeypatch):
    project = tmp_path / "project"
    build = project / "frontend/dist"
    build.mkdir(parents=True)
    (build / "index.html").write_text("<!doctype html><title>Hnefatafl</title>")
    monkeypatch.setattr("hnefatafl.server.PROJECT_ROOT", project)
    monkeypatch.setenv("HNEFATAFL_ENV", "production")
    monkeypatch.setenv("HNEFATAFL_PUBLIC_URL", "https://example.test")
    monkeypatch.setenv("HNEFATAFL_ALLOWED_HOSTS", "example.test,testserver")
    monkeypatch.setenv("HNEFATAFL_TRUSTED_PROXIES", "10.0.0.2")
    deployment = LiveDeployment(tmp_path)
    yield deployment
    deployment.stop_application()


def bootstrap(deployment, *, version="0.41.0", schema=None):
    store = Store(deployment.data / "hnefatafl.sqlite3")
    auth = Auth(store, SecuritySettings())
    admin = auth.create_account("Odin", "odin@example.test", PASSWORD, "ADMIN", bootstrap=True)
    (deployment.data / "models").mkdir(exist_ok=True)
    (deployment.data / "models/weights").write_bytes(b"retained-model")
    old = deployment.release(version)
    deployment.activate(old)
    atomic_json(deployment.state, {"version": version})
    if schema is not None:
        with sqlite3.connect(store.path) as c:
            c.execute(f"PRAGMA user_version={int(schema)}")
    return admin, old


def test_live_fresh_install_starts_and_probes_while_gate_is_held(lifecycle):
    target = lifecycle.release("0.41.1")
    lifecycle.deploy(target, initialize=True)
    assert lifecycle.current.resolve() == target
    assert json.loads(lifecycle.state.read_text())["version"] == "0.41.1"
    assert json.loads(lifecycle.journal.read_text())["phase"] == "committed"
    assert "service:start" in lifecycle.actions
    with sqlite3.connect(lifecycle.data / "hnefatafl.sqlite3") as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == 21
        assert c.execute("SELECT COUNT(*) FROM accounts WHERE role='ADMIN'").fetchone()[0] == 1


def test_live_fast_in_process_update_start_probe_commit_and_restart(lifecycle):
    # Fast lifecycle simulation: every release directory executes the current
    # checkout. Authentic schema-20/historical-release coverage lives in
    # tests/test_deployment_cross_version.py and uses real subprocesses.
    admin, old = bootstrap(lifecycle, schema=None)
    target = lifecycle.release("0.41.1")
    lifecycle.deploy(target)
    assert lifecycle.current.resolve() == target
    assert json.loads(lifecycle.state.read_text())["version"] == "0.41.1"
    with sqlite3.connect(lifecycle.data / "hnefatafl.sqlite3") as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == 21
        assert (
            c.execute("SELECT username FROM accounts WHERE id=?", (admin["id"],)).fetchone()[0]
            == "Odin"
        )
    assert (lifecycle.data / "models/weights").read_bytes() == b"retained-model"
    # Ordinary restart of the committed schema-21 installation must also start,
    # synchronously reconcile with no deployment lock and pass readiness.
    lifecycle.service("stop")
    lifecycle.service("start")
    lifecycle.probe()
    assert "service:stop" in lifecycle.actions and "service:start" in lifecycle.actions


def test_live_failed_activation_restores_previous_release_and_data(lifecycle):
    _, old = bootstrap(lifecycle, version="0.40.0")
    target = lifecycle.release("0.41.1")
    lifecycle.fail_probe_version = "0.41.1"
    with pytest.raises(ReadinessError, match="synthetic target readiness failure"):
        lifecycle.deploy(target)
    assert lifecycle.current.resolve() == old
    assert (lifecycle.data / "models/weights").read_bytes() == b"retained-model"
    assert json.loads(lifecycle.journal.read_text())["phase"] == "recovered"
    assert lifecycle.actions.count("service:start") >= 2


def test_live_interrupted_activation_explicit_recovery(lifecycle):
    _, old = bootstrap(lifecycle, version="0.40.0")
    backup = lifecycle.snapshot()
    target = lifecycle.release("0.41.1")
    lifecycle.activate(target)
    atomic_json(
        lifecycle.journal,
        {"phase": "activated", "previous": str(old), "target": str(target), "backup": str(backup)},
    )
    lifecycle.service("start")
    try:
        lifecycle.recover()
    finally:
        lifecycle.stop_application()
    assert lifecycle.current.resolve() == old
    assert json.loads(lifecycle.state.read_text())["version"] == "0.40.0"
    assert (lifecycle.data / "models/weights").read_bytes() == b"retained-model"
