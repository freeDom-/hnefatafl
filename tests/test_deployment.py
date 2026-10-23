"""Release selection and failure injection use disposable data; never systemd."""

import io
import json
import subprocess
import tarfile
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest

from hnefatafl.auth import Auth, SecuritySettings
from hnefatafl.deployment import (
    Deployment,
    GitHub,
    SafeRedirect,
    active_jobs,
    atomic_json,
    extract,
    repository,
    version_key,
    select_release,
)
from hnefatafl.storage import Store


def archive(path, entries):
    with tarfile.open(path, "w:gz") as bundle:
        for name, content in entries.items():
            member = tarfile.TarInfo(name)
            member.size = len(content)
            bundle.addfile(member, io.BytesIO(content))


def test_stable_release_selection_pagination_and_versions(monkeypatch):
    github = GitHub("owner/repository")
    values = [
        {"tag_name": tag, "draft": draft, "prerelease": pre, "published_at": published}
        for tag, draft, pre, published in [
            ("v1.9.0", False, False, "date"),
            ("v1.10.0", False, False, "date"),
            ("v2.0.0", True, False, "date"),
            ("v3.0.0", False, True, "date"),
            ("v4.0.0", False, False, None),
            ("v2.0.0-rc1", False, False, "date"),
        ]
    ]
    monkeypatch.setattr(github, "fetch", lambda *a, **k: json.dumps(values).encode())
    assert [r["tag_name"] for r in github.releases()] == ["v1.10.0", "v1.9.0"]
    for invalid in ("main", "../../etc", "v01.0.0", "1.2.3", "v1.2.3-rc1"):
        with pytest.raises(ValueError):
            version_key(invalid)
    assert version_key("v1.10.0") > version_key("v1.9.0")
    assert repository("https://github.com/owner/repo.git") == "owner/repo"
    with pytest.raises(ValueError):
        repository("https://token@evil.test/repo")


def test_release_asset_requires_integrity_and_does_not_leak_token(tmp_path, monkeypatch):
    github = GitHub("owner/repo", "secret-fixture-token")
    github.opener = SimpleNamespace(
        open=lambda *a, **k: (_ for _ in ()).throw(
            HTTPError("https://api.github.com", 401, "secret-fixture-token", {}, None)
        )
    )
    with pytest.raises(ValueError, match="Invalid GitHub credential") as error:
        github.fetch("https://api.github.com/repos/owner/repo/releases")
    assert "secret-fixture-token" not in str(error.value)
    with pytest.raises(ValueError, match="must publish"):
        github.download({"tag_name": "v1.0.0"}, tmp_path / "archive")
    release = {
        "tag_name": "v1.0.0",
        "assets": [
            {
                "id": 1,
                "name": "hnefatafl-v1.0.0.tar.gz",
                "state": "uploaded",
                "digest": "sha256:" + "0" * 64,
            }
        ],
    }
    monkeypatch.setattr(github, "fetch", lambda *a, **k: b"corrupt")
    with pytest.raises(ValueError, match="checksum mismatch"):
        github.download(release, tmp_path / "archive")


def test_redirect_never_forwards_authorization_to_asset_host():
    from urllib.request import Request

    request = Request("https://api.github.com/asset", headers={"Authorization": "Bearer secret"})
    redirected = SafeRedirect().redirect_request(
        request, None, 302, "Found", {}, "https://release-assets.githubusercontent.com/file"
    )
    assert not redirected.has_header("Authorization")
    for target in ("http://api.github.com/file", "https://evil.test/file"):
        with pytest.raises(ValueError):
            SafeRedirect().redirect_request(request, None, 302, "Found", {}, target)


@pytest.mark.parametrize("name", ["../escape", "/etc/escape", "safe/../../escape"])
def test_archive_paths_and_links_are_rejected(tmp_path, name):
    file = tmp_path / "archive.tar.gz"
    archive(file, {name: b"unsafe"})
    with pytest.raises(ValueError):
        extract(file, tmp_path / "extracted")
    with tarfile.open(file, "w:gz") as bundle:
        member = tarfile.TarInfo("link")
        member.type = tarfile.SYMTYPE
        member.linkname = "/etc"
        bundle.addfile(member)
    with pytest.raises(ValueError):
        extract(file, tmp_path / "extracted")


class DisposableDeployment(Deployment):
    def __init__(self, root):
        super().__init__(
            {"port": 8000, "public_url": "https://example.test"},
            base=root / "opt",
            data=root / "data",
            etc=root / "etc",
            unit_file=root / "unit.service",
        )
        self.actions = []
        self.failure = None
        self.releases.mkdir(parents=True)
        self.data.mkdir()
        self.etc.mkdir()
        self.store = Store(self.data / "hnefatafl.sqlite3")
        self.auth = Auth(self.store, SecuritySettings())
        self.admin = self.auth.create_account(
            "Odin", "odin@example.test", "deployment-fixture-password", "ADMIN", bootstrap=True
        )
        (self.data / "models").mkdir()
        (self.data / "models/weights").write_bytes(b"retained-model")
        (self.etc / "application.env").write_text(
            "HNEFATAFL_ENV=production\nHNEFATAFL_PUBLIC_URL=https://example.test\nHNEFATAFL_PORT=8000\nHNEFATAFL_ALLOWED_HOSTS=example.test\nHNEFATAFL_TRUSTED_PROXIES=10.0.0.2\n"
        )
        self.old = self.release("v1.0.0")
        self.activate(self.old)

    def release(self, tag):
        target = self.releases / tag
        (target / "deployment").mkdir(parents=True)
        (target / "deployment/hnefatafl.service").write_text("service for " + tag)
        atomic_json(target / ".release.json", {"version": tag})
        return target

    def service(self, action):
        self.actions.append(action)

    def run(self, args, **kwargs):
        self.actions.append([str(a) for a in args])
        if "migrate" in args and self.failure in {"migration", "interrupt"}:
            if self.failure == "interrupt":
                raise KeyboardInterrupt()
            raise subprocess.CalledProcessError(1, "migration")

    def probe(self):
        import fcntl

        with (self.data / ".deployment-gate").open("rb") as gate:
            with pytest.raises(BlockingIOError):
                fcntl.flock(gate, fcntl.LOCK_SH | fcntl.LOCK_NB)
        self.actions.append("probe")
        if self.failure == "health":
            self.failure = None
            raise ValueError("health failed")


@pytest.fixture
def deployment(tmp_path):
    return DisposableDeployment(tmp_path)


@pytest.mark.parametrize("failure", ["migration", "health", "interrupt"])
def test_failed_or_interrupted_activation_recovers_matching_data_and_release(deployment, failure):
    target = deployment.release("v2.0.0")
    deployment.failure = failure
    with pytest.raises((subprocess.CalledProcessError, ValueError, KeyboardInterrupt)):
        deployment.deploy(target)
    assert deployment.current.resolve() == deployment.old
    assert (deployment.data / "models/weights").read_bytes() == b"retained-model"
    assert deployment.auth.account(deployment.admin["id"])["username"] == "Odin"
    assert json.loads(deployment.journal.read_text())["phase"] == "recovered"
    assert "start" in deployment.actions


def test_success_rollback_preserves_new_account_security_state_and_invalidates_sessions(deployment):
    target = deployment.release("v2.0.0")
    deployment.deploy(target)
    assert deployment.current.resolve() == target
    assert json.loads(deployment.state.read_text())["version"] == "v2.0.0"
    original = deployment.auth.account(deployment.admin["id"])["password_hash"]
    deployment.auth.update_account(
        deployment.admin["id"], password="new-deployment-fixture-password"
    )
    changed = deployment.auth.account(deployment.admin["id"])["password_hash"]
    assert changed != original
    _, token, _ = deployment.auth.login("Odin", "new-deployment-fixture-password", "loopback")
    deployment.recover(rollback=True)
    assert deployment.current.resolve() == deployment.old
    assert deployment.auth.account(deployment.admin["id"])["password_hash"] == changed
    assert deployment.auth.session(token) is None
    assert (deployment.data / "models/weights").read_bytes() == b"retained-model"


def test_backup_verification_corruption_and_preservation(deployment):
    import gc

    backup = deployment.snapshot()
    gc.collect()
    manifest = json.loads((backup / "manifest.json").read_text())
    assert not any(name.endswith(("-wal", "-shm")) for name in manifest)
    deployment.verify_snapshot(backup)
    (backup / "data/models/weights").write_bytes(b"corrupted")
    with pytest.raises(ValueError, match="verification"):
        deployment.restore(backup)
    assert (deployment.data / "models/weights").read_bytes() == b"retained-model"


@pytest.mark.parametrize("status", ["running", "queued", "cancelling"])
def test_active_training_prevents_service_interruption(deployment, status):
    deployment.store.save_job({"id": "active", "created_at": "now", "status": status})
    assert active_jobs(deployment.data)
    with pytest.raises(ValueError, match="Active/queued"):
        deployment.deploy(deployment.release("v2.0.0"))
    assert deployment.actions == []
    assert deployment.current.resolve() == deployment.old


@pytest.mark.parametrize(
    "failure", [FileNotFoundError("missing dependency"), subprocess.CalledProcessError(1, "build")]
)
def test_failed_build_never_changes_running_release_or_data(
    deployment, tmp_path, monkeypatch, failure
):
    entries = {
        name: b"placeholder"
        for name in (
            "uv.lock",
            "frontend/package-lock.json",
            "hnefatafl/auth.py",
            "tests/test_security.py",
            "deployment/hnefatafl.service",
        )
    }
    entries["pyproject.toml"] = b'[project]\nversion="2.0.0"\n'
    file = tmp_path / "archive.tar.gz"
    archive(file, entries)

    def download(release, destination):
        destination.write_bytes(file.read_bytes())
        return "sha256:fixture"

    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(deployment, "run", fail)
    with pytest.raises((FileNotFoundError, subprocess.CalledProcessError)):
        deployment.prepare(SimpleNamespace(download=download), {"tag_name": "v2.0.0"})
    assert deployment.current.resolve() == deployment.old
    assert deployment.actions == []
    assert (deployment.data / "models/weights").read_bytes() == b"retained-model"


def test_exclusive_update_lock(tmp_path):
    import fcntl

    with (
        (tmp_path / "update.lock").open("w") as first,
        (tmp_path / "update.lock").open("w") as second,
    ):
        fcntl.flock(first, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            fcntl.flock(second, fcntl.LOCK_EX | fcntl.LOCK_NB)


def test_shell_syntax_and_deployment_files():
    root = Path(__file__).resolve().parents[1]
    for script in [*root.glob("scripts/linux/*.sh"), *root.glob("scripts/proxmox/*/*.sh")]:
        subprocess.run(["bash", "-n", script], check=True)
    unit = (root / "deployment/hnefatafl.service").read_text()
    assert "User=hnefatafl" in unit and "--workers 1" in unit and "--reload" not in unit


def test_fresh_activation_and_recover_of_committed_release_is_noop(deployment):
    deployment.current.unlink()
    target = deployment.release("v2.0.0")
    deployment.deploy(target, initialize=True)
    assert deployment.current.resolve() == target
    assert any(isinstance(action, list) and "init-admin" in action for action in deployment.actions)
    assert json.loads(deployment.state.read_text())["previous"] is None
    before = list(deployment.actions)
    deployment.recover()
    assert deployment.actions == before
    assert deployment.current.resolve() == target


def test_interrupted_recovery_records_restored_version(deployment):
    backup = deployment.snapshot()
    target = deployment.release("v2.0.0")
    deployment.activate(target)
    atomic_json(deployment.state, {"version": "v2.0.0"})
    atomic_json(
        deployment.journal,
        {"phase": "activated", "previous": str(deployment.old), "backup": str(backup)},
    )
    deployment.recover()
    assert deployment.current.resolve() == deployment.old
    assert json.loads(deployment.state.read_text())["version"] == "v1.0.0"


def test_no_release_current_and_explicit_stable_selection():
    releases = [{"tag_name": "v2.0.0"}, {"tag_name": "v1.10.0"}]
    with pytest.raises(ValueError, match="No published stable"):
        select_release([], None)
    assert select_release(releases, "v2.0.0")[1] is False
    assert select_release(releases, "v1.10.0")[1] is True
    assert select_release(releases, None, "v1.10.0")[0]["tag_name"] == "v1.10.0"
    with pytest.raises(ValueError, match="not a published stable"):
        select_release(releases, None, "v3.0.0")
