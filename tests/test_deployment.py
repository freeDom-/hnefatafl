"""Release selection and failure injection use disposable data; never systemd."""

import http.client
import io
import json
import socket
import subprocess
import tarfile
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError, URLError

import pytest

from hnefatafl import deployment as deployment_module
from hnefatafl.auth import Auth, SecuritySettings
from hnefatafl.deployment import (
    Deployment,
    DeploymentStepError,
    GitHub,
    ReadinessError,
    SafeRedirect,
    active_jobs,
    atomic_json,
    extract,
    repository,
    version_key,
    select_release,
)
from hnefatafl.storage import SCHEMA_VERSION, Store


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
            ("1.9.0", False, False, "date"),
            ("1.10.0", False, False, "date"),
            ("2.0.0", True, False, "date"),
            ("3.0.0", False, True, "date"),
            ("4.0.0", False, False, None),
            ("2.0.0-rc1", False, False, "date"),
            ("v9.0.0", False, False, "date"),
        ]
    ]
    monkeypatch.setattr(github, "fetch", lambda *a, **k: json.dumps(values).encode())
    assert [r["tag_name"] for r in github.releases()] == ["1.10.0", "1.9.0"]
    for invalid in ("main", "../../etc", "01.0.0", "v1.2.3", "1.2.3-rc1"):
        with pytest.raises(ValueError):
            version_key(invalid)
    assert version_key("1.10.0") > version_key("1.9.0")
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
        github.download({"tag_name": "1.0.0"}, tmp_path / "archive")
    release = {
        "tag_name": "1.0.0",
        "assets": [
            {
                "id": 1,
                "name": "hnefatafl-1.0.0.tar.gz",
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
        self.probe_failures = []
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
        self.old = self.release("1.0.0")
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
        if self.probe_failures:
            self.probe_failures.pop(0)
            raise ReadinessError("synthetic readiness failure")
        if self.failure == "health":
            self.failure = None
            raise ValueError("health failed")


@pytest.fixture
def deployment(tmp_path):
    return DisposableDeployment(tmp_path)


@pytest.mark.parametrize("failure", ["migration", "health", "interrupt"])
def test_failed_or_interrupted_activation_recovers_matching_data_and_release(deployment, failure):
    target = deployment.release("2.0.0")
    deployment.failure = failure
    with pytest.raises((subprocess.CalledProcessError, ValueError, KeyboardInterrupt)):
        deployment.deploy(target)
    assert deployment.current.resolve() == deployment.old
    assert (deployment.data / "models/weights").read_bytes() == b"retained-model"
    assert deployment.auth.account(deployment.admin["id"])["username"] == "Odin"
    assert json.loads(deployment.journal.read_text())["phase"] == "recovered"
    assert "start" in deployment.actions


def test_success_rollback_preserves_new_account_security_state_and_invalidates_sessions(deployment):
    target = deployment.release("2.0.0")
    deployment.deploy(target)
    assert deployment.current.resolve() == target
    assert json.loads(deployment.state.read_text())["version"] == "2.0.0"
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


def test_restore_preserves_live_verification_and_limits_but_revokes_tokens_and_mail(deployment):
    identifier = deployment.admin["id"]
    with deployment.store.connection() as c:
        c.execute(
            "INSERT INTO account_tokens VALUES(?,'reset','old-digest',1,'old-address-digest',1,9999999999,NULL)",
            (identifier,),
        )
        c.execute(
            "INSERT INTO account_mail(account_id,purpose,generation,email_version,address_hash,status,created_at,next_attempt) VALUES(?,'reset','old-generation',1,'old-address-digest','pending',1,1)",
            (identifier,),
        )
    backup = deployment.snapshot()
    with deployment.store.connection() as c:
        c.execute("UPDATE accounts SET email_verified_at=12345 WHERE id=?", (identifier,))
        c.execute(
            "UPDATE accounts SET date_of_birth='1990-03-15',city='Still private',profile_private_fields='[\"city\"]' WHERE id=?",
            (identifier,),
        )
        c.execute("INSERT INTO account_limits VALUES('hashes',12345,99)")
    deployment.restore(backup)
    with deployment.store.connection() as c:
        assert tuple(
            c.execute(
                "SELECT date_of_birth,city,profile_private_fields FROM accounts WHERE id=?",
                (identifier,),
            ).fetchone()
        ) == ("1990-03-15", "Still private", '["city"]')
        assert (
            c.execute(
                "SELECT email_verified_at FROM accounts WHERE id=?", (identifier,)
            ).fetchone()[0]
            == 12345
        )
        assert (
            c.execute("SELECT attempts FROM account_limits WHERE key='hashes'").fetchone()[0] == 99
        )
        assert c.execute("SELECT COUNT(*) FROM account_tokens").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM account_mail").fetchone()[0] == 0


def test_schema14_profile_backup_requires_manual_upgrade_after_schema15_transition(deployment):
    from hnefatafl.storage import Store

    with deployment.store.connection() as c:
        c.execute("ALTER TABLE accounts DROP COLUMN profile_private_fields")
        c.execute("ALTER TABLE accounts DROP COLUMN date_of_birth")
        c.execute("UPDATE accounts SET city='Legacy private',show_city=0")
        c.execute("PRAGMA user_version=14")
    backup = deployment.snapshot()
    Store(deployment.store.path)
    with pytest.raises(ValueError, match="Account schema changed"):
        deployment.restore(backup)
    with deployment.store.connection() as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert tuple(
            c.execute("SELECT city,profile_private_fields,date_of_birth FROM accounts").fetchone()
        ) == ("Legacy private", '["city"]', None)


def test_cross_account_schema_restore_refuses_to_replace_live_security(deployment):
    import sqlite3

    backup = deployment.snapshot()
    live = deployment.data / "hnefatafl.sqlite3"
    with sqlite3.connect(live) as c:
        c.execute("ALTER TABLE accounts ADD COLUMN future_security_state TEXT")
    with pytest.raises(ValueError, match="Account schema changed"):
        deployment.restore(backup)
    assert deployment.auth.account(deployment.admin["id"])["role"] == "ADMIN"


def test_restore_retains_live_online_results_return_games_and_pauses_timeout_gate(deployment):
    from datetime import datetime, timezone
    from uuid import uuid4
    import sqlite3

    from hnefatafl.auth import digest
    from hnefatafl.online_core import OnlineCore
    from hnefatafl.online_settings import OnlineSettings

    backup = deployment.snapshot()
    other = deployment.auth.create_account(
        "Raven", "raven@example.test", "online-deployment-password"
    )
    with deployment.store.connection() as c:
        c.execute("UPDATE accounts SET email_verified_at=123")
    _, first_token, _ = deployment.auth.login("Odin", "deployment-fixture-password", "first")
    _, second_token, _ = deployment.auth.login("Raven", "online-deployment-password", "second")
    core = OnlineCore(
        deployment.auth,
        OnlineSettings(enabled=True, timeout_policy_approved=True),
        lambda: datetime.now(timezone.utc),
    )
    game = core.create(digest(first_token), "brandubh", "attackers", 60, uuid4().hex, "sequential")[
        "game"
    ]
    core.mutate(digest(second_token), game["id"], 0, "join")
    finished = core.mutate(digest(first_token), game["id"], 1, "concede")["game"]
    core.set_gate(digest(first_token), 0, True, "Disposable approved outage fixture")
    # Schema-17 progression belongs to the retained live online authority: a
    # confirmed award, its entitlement and the selected portrait are player state
    # a compatible restore must keep instead of rewinding to the snapshot.
    with deployment.store.connection() as c:
        c.execute(
            "INSERT INTO achievement_awards(account_id,achievement_id,awarded_at,catalog_version) "
            "VALUES(?,?,?,?)",
            (deployment.admin["id"], "first-rated-win", 456, 1),
        )
        c.execute(
            "INSERT INTO account_portraits(account_id,portrait_id,granted_at,source_achievement_id) "
            "VALUES(?,?,?,?)",
            (deployment.admin["id"], "shieldmaiden", 456, "first-rated-win"),
        )
        c.execute(
            "INSERT INTO account_portrait_selection(account_id,portrait_id,updated_at) "
            "VALUES(?,?,?)",
            (deployment.admin["id"], "shieldmaiden", 456),
        )
    # Published portraits are current profile metadata, not a game-version
    # snapshot. Capture the projection after the legitimate live selection.
    finished = core.read(digest(first_token), game["id"])
    assert finished["participants"]["attackers"]["portrait_id"] == "shieldmaiden"
    tables = (
        "online_matches",
        "online_games",
        "online_moves",
        "online_results",
        "online_void_audit",
        "achievement_awards",
        "account_portraits",
        "account_portrait_selection",
    )
    with deployment.store.connection() as c:
        before = {
            table: c.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall() for table in tables
        }
        before = {table: [tuple(row) for row in rows] for table, rows in before.items()}
    deployment.auth.update_account(
        deployment.admin["id"], password="new-online-deployment-password"
    )
    deployment.restore(backup)
    assert deployment.auth.account(other["id"])["email_verified_at"] == 123
    assert (
        deployment.auth.session(first_token) is None
        and deployment.auth.session(second_token) is None
    )
    with deployment.store.connection() as c:
        after = {
            table: [tuple(row) for row in c.execute(f"SELECT * FROM {table} ORDER BY 1")]
            for table in tables
        }
        assert before == after
        assert tuple(c.execute("SELECT enabled,version FROM online_timeout_gate").fetchone()) == (
            0,
            2,
        )
        assert (
            c.execute(
                "SELECT source FROM online_gate_audit ORDER BY version DESC LIMIT 1"
            ).fetchone()[0]
            == "restore"
        )
        assert c.execute("SELECT COUNT(*) FROM online_games").fetchone()[0] == 2
        assert c.execute("SELECT COUNT(*) FROM online_results").fetchone()[0] == 1
        for table in ("online_results", "online_gate_audit"):
            with pytest.raises(sqlite3.IntegrityError):
                c.execute(f"DELETE FROM {table}")
    _, new_session, _ = deployment.auth.login(
        "Odin", "new-online-deployment-password", "after-restore"
    )
    recovered = core.read(digest(new_session), game["id"])
    assert recovered == finished


def test_restore_retains_new_official_league_results_and_pending_settlement(deployment):
    from datetime import datetime, timezone
    from uuid import uuid4

    from hnefatafl.auth import digest
    from hnefatafl.league_migrations import LEAGUE_TABLES
    from hnefatafl.leagues import Leagues
    from hnefatafl.online_core import OnlineCore
    from hnefatafl.online_settings import OnlineSettings

    backup = deployment.snapshot()
    opponent = deployment.auth.create_account(
        "Raven", "raven@example.test", "online-deployment-password"
    )
    with deployment.store.connection() as c:
        c.execute("UPDATE accounts SET email_verified_at=123")
    _, admin_token, _ = deployment.auth.login("Odin", "deployment-fixture-password", "first")
    _, other_token, _ = deployment.auth.login("Raven", "online-deployment-password", "second")
    credentials = {
        deployment.admin["id"]: digest(admin_token),
        opponent["id"]: digest(other_token),
    }
    core = OnlineCore(
        deployment.auth,
        OnlineSettings(enabled=True, creation_policy_approved=True, timeout_policy_approved=True),
        lambda: datetime(2026, 11, 24, tzinfo=timezone.utc),
    )
    league = Leagues(core)
    admin = credentials[deployment.admin["id"]]
    league.join(admin, "brandubh", str(uuid4()))
    matched = league.join(credentials[opponent["id"]], "brandubh", str(uuid4()))
    game = core.read(admin, matched["game_id"])
    loser = game["participants"][game["position"]["turn"]]["account_id"]
    core.mutate(credentials[loser], game["id"], game["version"], "concede")

    retained = (*LEAGUE_TABLES, "rating_events", "online_results")
    with deployment.store.connection() as c:
        before = {
            table: [tuple(row) for row in c.execute(f"SELECT * FROM {table} ORDER BY 1,2")]
            for table in retained
        }
        assert before["league_settlement_queue"]
        assert before["league_games"]
        assert before["rating_events"]

    deployment.restore(backup)
    with deployment.store.connection() as c:
        assert before == {
            table: [tuple(row) for row in c.execute(f"SELECT * FROM {table} ORDER BY 1,2")]
            for table in retained
        }
        assert not c.execute("PRAGMA foreign_key_check").fetchall()
        assert c.execute("SELECT enabled FROM online_timeout_gate").fetchone()[0] == 0
    assert deployment.auth.session(admin_token) is None
    assert deployment.auth.session(other_token) is None
    assert Leagues(core).drain()["settled"] == 1
    with deployment.store.connection() as c:
        assert c.execute("SELECT sum(points) FROM league_standings").fetchone()[0] == 3
        assert c.execute("SELECT count(*) FROM rating_events").fetchone()[0] == 1


def test_online_schema_downgrade_restore_refuses_live_root_replacement(deployment):
    from hnefatafl.online_migrations import ONLINE_TABLES

    with deployment.store.connection() as c:
        for table in reversed(ONLINE_TABLES):
            c.execute(f"DROP TABLE {table}")
        c.execute("PRAGMA user_version=9")
    backup = deployment.snapshot()
    Store(deployment.store.path)
    with pytest.raises(ValueError, match="Online schema changed"):
        deployment.restore(backup)
    with deployment.store.connection() as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert c.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 1


def test_snapshot_only_recovery_also_revokes_sessions_and_audits_timeout_pause(deployment):
    import sqlite3

    _, token, _ = deployment.auth.login("Odin", "deployment-fixture-password", "snapshot")
    with deployment.store.connection() as c:
        c.execute("UPDATE online_timeout_gate SET enabled=1")
    backup = deployment.snapshot()
    deployment.store.path.unlink()
    deployment.restore(backup)
    assert deployment.auth.session(token) is None
    with sqlite3.connect(deployment.store.path) as c:
        assert c.execute("SELECT enabled FROM online_timeout_gate").fetchone()[0] == 0
        assert (
            c.execute(
                "SELECT source FROM online_gate_audit ORDER BY version DESC LIMIT 1"
            ).fetchone()[0]
            == "restore"
        )
        assert c.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0


def test_snapshot_without_database_cannot_replace_live_authority(deployment):
    held = deployment.data.parent / "held-live.sqlite3"
    deployment.store.path.rename(held)
    backup = deployment.snapshot()
    held.rename(deployment.store.path)
    with pytest.raises(ValueError, match="Snapshot has no database"):
        deployment.restore(backup)
    assert deployment.auth.account(deployment.admin["id"])["active"] == 1
    with deployment.store.connection() as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


@pytest.mark.parametrize("status", ["running", "queued", "cancelling"])
def test_active_training_prevents_service_interruption(deployment, status):
    deployment.store.save_job({"id": "active", "created_at": "now", "status": status})
    assert active_jobs(deployment.data)
    with pytest.raises(ValueError, match="Active/queued"):
        deployment.deploy(deployment.release("2.0.0"))
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
        deployment.prepare(SimpleNamespace(download=download), {"tag_name": "2.0.0"})
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
    target = deployment.release("2.0.0")
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
    target = deployment.release("2.0.0")
    deployment.activate(target)
    atomic_json(deployment.state, {"version": "2.0.0"})
    atomic_json(
        deployment.journal,
        {"phase": "activated", "previous": str(deployment.old), "backup": str(backup)},
    )
    deployment.recover()
    assert deployment.current.resolve() == deployment.old
    assert json.loads(deployment.state.read_text())["version"] == "1.0.0"


def test_no_release_current_and_explicit_stable_selection():
    releases = [{"tag_name": "2.0.0"}, {"tag_name": "1.10.0"}]
    with pytest.raises(ValueError, match="No published stable"):
        select_release([], None)
    assert select_release(releases, "2.0.0")[1] is False
    assert select_release(releases, "1.10.0")[1] is True
    assert select_release(releases, None, "1.10.0")[0]["tag_name"] == "1.10.0"
    with pytest.raises(ValueError, match="not a published stable"):
        select_release(releases, None, "3.0.0")


class ProbeResponse:
    def __init__(self, body, status=200):
        self.body, self.status = body, status

    def read(self, limit=-1):
        return self.body if limit < 0 else self.body[:limit]

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


@pytest.fixture
def readiness(tmp_path):
    deployment = Deployment(
        {"port": 8000, "public_url": "https://example.test"},
        base=tmp_path / "opt",
        data=tmp_path / "data",
        etc=tmp_path / "etc",
        unit_file=tmp_path / "unit.service",
    )
    deployment.base.mkdir(parents=True)
    deployment.data.mkdir()
    deployment.etc.mkdir()
    target = tmp_path / "release"
    target.mkdir()
    atomic_json(target / ".release.json", {"version": "0.41.1"})
    atomic_json(
        deployment.journal,
        {"phase": "activated", "previous": None, "target": str(target), "backup": None},
    )
    deployment.activate(target)
    return deployment


def failing_readiness(deployment, monkeypatch, failure):
    monkeypatch.setattr(deployment_module.time, "sleep", lambda _: None)
    monkeypatch.setattr(
        deployment, "service_status", lambda: "failed (failed), result=exit-code, exit=1"
    )

    def fetch(*_args, **_kwargs):
        raise failure

    monkeypatch.setattr(deployment, "fetch_url", fetch)
    with pytest.raises(ReadinessError) as error:
        deployment._probe_health("http://127.0.0.1:8000", "example.test")
    return str(error.value)


@pytest.mark.parametrize(
    ("failure", "category"),
    [
        (URLError(ConnectionRefusedError(111, "refused")), "connection_refused"),
        (URLError(socket.timeout("timed out")), "connection_timeout"),
        (URLError(OSError("socket failed")), "connection_error"),
        (TimeoutError("timed out"), "connection_timeout"),
    ],
)
def test_readiness_reports_sanitized_transport_categories(
    readiness, monkeypatch, failure, category
):
    message = failing_readiness(readiness, monkeypatch, failure)
    assert category in message
    assert "Failure phase: readiness verification" in message
    assert "Deployment phase: activated" in message
    assert "Original target: 0.41.1" in message
    assert "currently probed release: 0.41.1" in message
    assert "Automatic recovery: not evaluated" in message
    assert "Attempts: 30" in message
    assert "Service state: failed (failed), result=exit-code, exit=1" in message
    assert "journalctl -u hnefatafl" in message
    assert str(readiness.journal) in message


def test_readiness_reports_unexpected_status_without_private_response_body(readiness, monkeypatch):
    private_body = b"private-response-body session=secret-token"
    message = failing_readiness(
        readiness,
        monkeypatch,
        HTTPError(
            "http://127.0.0.1:8000/api/health",
            503,
            "unavailable",
            {},
            io.BytesIO(private_body),
        ),
    )
    assert "Probe category: http_status; last HTTP status: 503" in message
    assert private_body.decode() not in message


@pytest.mark.parametrize(
    ("body", "category"),
    [
        (b"not-json", "invalid_json"),
        (b"[]", "unexpected_health_response"),
        (b'{"status":"degraded"}', "unexpected_health_response"),
        (b"x" * (deployment_module.READINESS_BODY_LIMIT + 1), "invalid_response"),
    ],
)
def test_readiness_reports_invalid_or_unexpected_health_bodies(
    readiness, monkeypatch, body, category
):
    monkeypatch.setattr(deployment_module.time, "sleep", lambda _: None)
    monkeypatch.setattr(readiness, "service_status", lambda: "inactive (dead)")
    monkeypatch.setattr(readiness, "fetch_url", lambda *_args, **_kwargs: ProbeResponse(body))
    with pytest.raises(ReadinessError) as error:
        readiness._probe_health("http://127.0.0.1:8000", "example.test")
    message = str(error.value)
    assert category in message
    assert body.decode(errors="ignore") not in message


def test_readiness_succeeds_after_transient_failures(readiness, monkeypatch):
    responses = [
        URLError(ConnectionRefusedError(111, "refused")),
        HTTPError("http://127.0.0.1:8000/api/health", 503, "unavailable", {}, None),
        ProbeResponse(b'{"status":"ok"}'),
    ]
    calls = []

    def fetch(*_args, **_kwargs):
        calls.append(1)
        value = responses.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

    monkeypatch.setattr(deployment_module.time, "sleep", lambda _: None)
    monkeypatch.setattr(readiness, "fetch_url", fetch)
    readiness._probe_health("http://127.0.0.1:8000", "example.test")
    assert len(calls) == 3


def test_readiness_failure_reports_successful_automatic_recovery(deployment):
    deployment.probe_failures = ["readiness"]
    with pytest.raises(ReadinessError) as error:
        deployment.deploy(deployment.release("2.0.0"))
    message = str(error.value)
    assert "synthetic readiness failure" in message
    assert "Automatic recovery: succeeded" in message
    assert deployment.current.resolve() == deployment.old
    assert json.loads(deployment.journal.read_text())["phase"] == "recovered"


def test_readiness_failure_reports_failed_automatic_recovery(deployment):
    deployment.probe_failures = ["readiness", "readiness"]
    with pytest.raises(ReadinessError) as error:
        deployment.deploy(deployment.release("2.0.0"))
    message = str(error.value)
    assert "Automatic recovery: failed" in message
    assert "Automatic recovery: succeeded" not in message
    assert deployment.current.resolve() == deployment.old
    assert (deployment.data / "models/weights").read_bytes() == b"retained-model"


class BrokenResponse(ProbeResponse):
    def __init__(self, error):
        super().__init__(b"")
        self.error = error

    def read(self, limit=-1):
        raise self.error


def test_health_retries_truncated_and_protocol_errors_then_succeeds(readiness, monkeypatch):
    responses = [
        BrokenResponse(http.client.IncompleteRead(b"partial")),
        http.client.HTTPException("invalid protocol"),
        ProbeResponse(b'{"status":"ok"}'),
    ]
    calls = []

    def fetch(*_args, **_kwargs):
        calls.append(1)
        value = responses.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value

    monkeypatch.setattr(deployment_module.time, "sleep", lambda _: None)
    monkeypatch.setattr(readiness, "fetch_url", fetch)
    readiness._probe_health("http://127.0.0.1:8000", "example.test")
    assert len(calls) == 3


@pytest.mark.parametrize(
    ("probe", "failure", "category"),
    [
        ("authenticated", http.client.IncompleteRead(b"partial"), "truncated_response"),
        ("authenticated", http.client.HTTPException("protocol"), "http_protocol_error"),
        ("anonymous rejection", http.client.IncompleteRead(b"partial"), "truncated_response"),
        ("anonymous rejection", http.client.HTTPException("protocol"), "http_protocol_error"),
    ],
)
def test_authenticated_and_anonymous_protocol_failures_are_sanitized(
    readiness, monkeypatch, probe, failure, category
):
    monkeypatch.setattr(readiness, "service_status", lambda: "failed (failed)")
    monkeypatch.setattr(
        readiness,
        "fetch_url",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(failure),
    )
    with pytest.raises(ReadinessError) as error:
        if probe == "authenticated":
            readiness._probe_authentication("http://127.0.0.1:8000", "example.test", "token")
        else:
            readiness._probe_anonymous_rejection("http://127.0.0.1:8000", "example.test")
    message = str(error.value)
    assert category in message
    assert "partial" not in message and "invalid protocol" not in message


def test_diagnostics_distinguish_original_target_and_currently_probed_release(deployment):
    target = deployment.release("0.41.1")
    atomic_json(
        deployment.journal,
        {"phase": "activated", "previous": None, "target": str(target), "backup": None},
    )
    deployment.activate(target)
    message = str(
        deployment._readiness_error("health", "synthetic", attempts=1, elapsed=0.1, status=None)
    )
    assert "Original target: 0.41.1" in message
    assert "currently probed release: 0.41.1" in message
    deployment.activate(deployment.old)
    deployment._diagnostic_failure_phase = "readiness verification during rollback"
    deployment._diagnostic_recovery_target = "1.0.0"
    message = str(
        deployment._readiness_error("health", "synthetic", attempts=1, elapsed=0.1, status=None)
    )
    assert "Original target: 0.41.1" in message
    assert "currently probed release: 1.0.0" in message
    assert "recovery target: 1.0.0" in message
    assert "Failure phase: readiness verification during rollback" in message


def test_diagnostics_tolerate_missing_or_malformed_release_metadata(deployment):
    deployment.journal.write_text("{not-json")
    deployment.state.write_text("[]")
    target = deployment.release("0.41.1")
    (target / ".release.json").write_text("not-json")
    deployment.activate(target)
    context = deployment._deployment_diagnostics()
    assert context["phase"] == "unknown"
    assert context["target"] == "unknown"
    assert context["current"] == "unknown"
    assert context["installed"] == "unknown"
    message = str(
        deployment._readiness_error("health", "synthetic", attempts=1, elapsed=0.1, status=None)
    )
    assert "not-json" not in message


def test_explicit_rollback_rejects_known_incompatible_release_before_change(deployment):
    incompatible = deployment.release("0.41.0")
    atomic_json(deployment.state, {"version": "0.41.0", "previous": str(incompatible)})
    deployment.actions.clear()
    with pytest.raises(ValueError, match="0.41.0") as error:
        deployment.recover(rollback=True)
    assert "No snapshot restore" in str(error.value)
    assert deployment.actions == []
    assert deployment.current.resolve() == deployment.old
    assert (deployment.data / "models/weights").read_bytes() == b"retained-model"


def test_interrupted_recovery_rejects_known_incompatible_previous_release(deployment):
    incompatible = deployment.release("0.41.0")
    target = deployment.release("0.41.1")
    atomic_json(
        deployment.journal,
        {
            "phase": "activated",
            "previous": str(incompatible),
            "target": str(target),
            "backup": None,
        },
    )
    deployment.actions.clear()
    with pytest.raises(ValueError, match="0.41.0"):
        deployment.recover()
    assert deployment.actions == []
    assert deployment.current.resolve() == deployment.old
    assert (deployment.data / "models/weights").read_bytes() == b"retained-model"


def test_automatic_recovery_refuses_known_incompatible_previous_release(deployment):
    incompatible = deployment.release("0.41.0")
    deployment.current.unlink()
    deployment.activate(incompatible)
    atomic_json(deployment.state, {"version": "0.41.0"})
    target = deployment.release("0.41.1")
    deployment.probe_failures = ["readiness"]
    deployment.actions.clear()
    with pytest.raises(ReadinessError) as error:
        deployment.deploy(target)
    message = str(error.value)
    assert "Automatic recovery: failed during unsupported rollback policy check" in message
    assert "0.41.0" in message
    assert deployment.current.resolve() == target
    assert json.loads(deployment.journal.read_text())["phase"] == "activated"
    assert json.loads(deployment.state.read_text())["version"] == "0.41.0"
    assert (deployment.data / "models/weights").read_bytes() == b"retained-model"


def test_rollback_policy_allows_040_and_corrected_releases(deployment):
    for version in ("0.40.0", "0.41.1", "2.0.0"):
        deployment._ensure_rollback_target_supported(deployment.release(version), action="rollback")


def test_deployment_step_diagnostics_identify_snapshot_failure(deployment, monkeypatch):
    def fail_snapshot():
        raise OSError("snapshot failed")

    monkeypatch.setattr(deployment, "snapshot", fail_snapshot)
    with pytest.raises(DeploymentStepError) as error:
        deployment.deploy(deployment.release("2.0.0"))
    message = str(error.value)
    assert "pre-deployment snapshot" in message
    assert "automatic recovery succeeded" in message
    assert deployment.current.resolve() == deployment.old


def test_recovery_step_diagnostics_identify_snapshot_restore_failure(deployment, monkeypatch):
    from hnefatafl.deployment import DeploymentError

    def fail_restore(_backup):
        raise DeploymentError("snapshot restore failed")

    deployment.probe_failures = ["readiness"]
    monkeypatch.setattr(deployment, "restore", fail_restore)
    with pytest.raises(ReadinessError) as error:
        deployment.deploy(deployment.release("2.0.0"))
    message = str(error.value)
    assert "Automatic recovery: failed during snapshot restoration" in message
    assert "snapshot restore failed" in message


def test_recovery_preserves_actionable_schema_refusal_detail(deployment, monkeypatch):
    from hnefatafl.deployment import DeploymentError

    def fail_restore(_backup):
        raise DeploymentError("Online schema changed; manual recovery is required")

    deployment.probe_failures = ["readiness"]
    monkeypatch.setattr(deployment, "restore", fail_restore)
    with pytest.raises(ReadinessError) as error:
        deployment.deploy(deployment.release("2.0.0"))
    assert "Online schema changed; manual recovery is required" in str(error.value)


@pytest.mark.parametrize(
    ("failure", "category"),
    [
        (ValueError("password=secret-token"), "ValueError"),
        (RuntimeError("cookie=private-session"), "RuntimeError"),
        (OSError("token=github-secret"), "OSError"),
    ],
)
def test_recovery_diagnostics_do_not_expose_unexpected_payloads(
    deployment, monkeypatch, failure, category
):
    def fail_restore(_backup):
        raise failure

    deployment.probe_failures = ["readiness"]
    monkeypatch.setattr(deployment, "restore", fail_restore)
    with pytest.raises(ReadinessError) as error:
        deployment.deploy(deployment.release("2.0.0"))
    message = str(error.value)
    assert category in message
    for secret in ("secret-token", "private-session", "github-secret"):
        assert secret not in message


def test_corrected_release_rollback_with_matching_schemas(deployment):
    old = deployment.release("0.41.1")
    deployment.current.unlink()
    deployment.activate(old)
    atomic_json(deployment.state, {"version": "0.41.1"})
    target = deployment.release("0.41.2")
    deployment.deploy(target)
    assert deployment.current.resolve() == target
    deployment.recover(rollback=True)
    assert deployment.current.resolve() == old
    assert json.loads(deployment.state.read_text())["version"] == "0.41.1"
    assert (deployment.data / "models/weights").read_bytes() == b"retained-model"
