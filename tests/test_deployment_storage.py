"""Storage-aware deployment retention, preflight and recovery regressions.

All tests use disposable directories. systemd, GitHub and the network are never
touched; the harness only records service actions and substitutes release archives.
"""

import errno
import json
import os
import shutil
import sqlite3
import subprocess
import tarfile
import time
from pathlib import Path

import pytest

from hnefatafl.deployment import (
    Deployment,
    DeploymentStepError,
    FilesystemInfo,
    StorageError,
    atomic_json,
    checksum,
    normalized_config,
)


def archive(path, entries):
    import io

    with tarfile.open(path, "w:gz") as bundle:
        for name, content in entries.items():
            member = tarfile.TarInfo(name)
            member.size = len(content)
            bundle.addfile(member, io.BytesIO(content))


class FakeGitHub:
    def __init__(self, source=None, failure=None):
        self.source = source
        self.failure = failure
        self.calls = []

    def download(self, release, destination):
        self.calls.append(dict(release))
        if self.failure:
            raise self.failure
        destination.write_bytes(self.source.read_bytes())
        return "sha256:fixture"


class Harness(Deployment):
    def __init__(self, root, config=None):
        settings = {"port": 8000, "public_url": "https://example.test"}
        settings.update(config or {})
        super().__init__(
            settings,
            base=root / "opt",
            data=root / "data",
            etc=root / "etc",
            unit_file=root / "unit.service",
        )
        self.actions = []
        self.probe_failure = None
        self.run_failure = None
        self.filesystem_override = None
        self.releases.mkdir(parents=True)
        self.data.mkdir()
        self.etc.mkdir()
        self.backups.mkdir(mode=0o700)
        self.backups.chmod(0o700)
        (self.data / "models").mkdir()
        (self.data / "models/weights").write_bytes(b"retained-model")
        (self.data / "jobs").mkdir()
        (self.data / "jobs/job.log").write_text("training history\n")
        (self.etc / "application.env").write_text(
            "HNEFATAFL_ENV=production\n"
            "HNEFATAFL_PUBLIC_URL=https://example.test\n"
            "HNEFATAFL_PORT=8000\n"
            "HNEFATAFL_ALLOWED_HOSTS=example.test\n"
            "HNEFATAFL_TRUSTED_PROXIES=10.0.0.2\n"
        )

    def release(self, version, identifier="abcd", prepared_at=None, payload_size=32):
        path = self.releases / f"{version}-{identifier}"
        (path / "deployment").mkdir(parents=True, exist_ok=True)
        (path / "deployment/hnefatafl.service").write_text("service for " + version)
        (path / "payload.bin").write_bytes(b"x" * payload_size)
        atomic_json(
            path / ".release.json",
            {
                "version": version,
                "sha256": "sha256:" + "0" * 64,
                "prepared_at": prepared_at if prepared_at is not None else time.time(),
                "size_bytes": payload_size,
            },
        )
        return path

    def activate(self, path):
        self.current.unlink(missing_ok=True)
        self.current.symlink_to(path)

    def set_state(self, version, target, previous=None, backup=None, **extra):
        atomic_json(
            self.state,
            {
                "version": version,
                "target": str(target) if target else None,
                "previous": str(previous) if previous else None,
                "backup": str(backup) if backup else None,
                "phase": "committed",
                **extra,
            },
        )

    def make_backup(self, name, *, complete=True, invalid=False, old=0, payload=b"backup-data"):
        path = self.backups / name
        (path / "data").mkdir(parents=True)
        path.chmod(0o700)
        (path / "config").mkdir()
        (path / "data/sentinel").write_bytes(payload)
        manifest = {
            str(file.relative_to(path)): checksum(file)
            for file in path.rglob("*")
            if file.is_file()
        }
        if invalid:
            # Simulate a manifest that is present but does not verify.
            atomic_json(path / "manifest.json", {"data/sentinel": "0" * 64})
        elif complete:
            atomic_json(path / "manifest.json", manifest)
        when = time.time() - old
        os.utime(path, (when, when))
        return path

    def service(self, action):
        self.actions.append(action)

    def run(self, args, **kwargs):
        self.actions.append([str(value) for value in args])
        if self.run_failure is not None:
            raise self.run_failure

    def probe(self):
        self.actions.append("probe")
        if self.probe_failure:
            raise self.probe_failure

    def filesystem_info(self, path):
        if self.filesystem_override is not None:
            return self.filesystem_override(path)
        return super().filesystem_info(path)


@pytest.fixture
def harness(tmp_path):
    return Harness(tmp_path)


def names(path):
    return sorted(item.name for item in path.iterdir())


def test_default_retention_applies_to_existing_configurations(harness):
    assert harness.retention == {"releases": 3, "backups": 3}
    assert normalized_config({"repository": "owner/repo"})["retention"] == {
        "releases": 3,
        "backups": 3,
    }
    custom = normalized_config(
        {
            "retention": {"releases": 2, "backups": 5},
            "storage": {"headroom_mib": 512},
            "recovery": {"readiness_attempts": 4},
        }
    )
    assert custom["retention"] == {"releases": 2, "backups": 5}
    assert custom["storage"]["headroom_mib"] == 512
    assert custom["storage"]["snapshot_margin_percent"] == 15
    assert custom["recovery"]["readiness_attempts"] == 4
    with pytest.raises(ValueError, match="retention.releases"):
        normalized_config({"retention": {"releases": 0}})
    with pytest.raises(ValueError, match="retention must be an object"):
        normalized_config({"retention": "three"})
    with pytest.raises(ValueError, match="snapshot_margin_percent"):
        normalized_config({"storage": {"snapshot_margin_percent": 101}})


def test_release_retention_keeps_active_rollback_and_deterministic_newest(harness):
    releases = [
        harness.release(version, f"{index:04x}", prepared_at=1000 + index)
        for index, version in enumerate(("0.1.0", "0.2.0", "0.3.0", "0.4.0", "0.5.0"))
    ]
    harness.activate(releases[-1])
    harness.set_state("0.5.0", releases[-1], previous=releases[-2])
    report = harness.cleanup(dry_run=False)
    assert names(harness.releases) == [
        "0.3.0-0002",
        "0.4.0-0003",
        "0.5.0-0004",
    ]
    assert report.reclaimable_bytes > 0
    assert any(entry.action == "removed" for entry in report.removed)
    # A second pass is idempotent.
    second = harness.cleanup(dry_run=False)
    assert second.removed == []
    assert names(harness.releases) == [
        "0.3.0-0002",
        "0.4.0-0003",
        "0.5.0-0004",
    ]


def test_duplicate_versions_do_not_consume_retention_slots(harness):
    active = harness.release("2.0.0", "0001", prepared_at=5000)
    previous = harness.release("1.0.0", "0002", prepared_at=4000)
    old_duplicate = harness.release("1.0.0", "0003", prepared_at=3000)
    new_duplicate = harness.release("1.0.0", "0004", prepared_at=4500)
    other = harness.release("0.9.0", "0005", prepared_at=2000)
    harness.activate(active)
    harness.set_state("2.0.0", active, previous=previous)
    harness.cleanup(dry_run=False)
    assert new_duplicate.name not in names(harness.releases)
    assert old_duplicate.name not in names(harness.releases)
    assert {previous.name, active.name, other.name} <= set(names(harness.releases))
    assert len(names(harness.releases)) == 3
    # Protected duplicate is never removed because an unprotected same-version
    # directory happens to be newer.
    assert previous.exists()


def test_configured_retention_and_protected_exception_are_reported(tmp_path):
    harness = Harness(tmp_path, {"retention": {"releases": 1, "backups": 1}})
    active = harness.release("2.0.0", prepared_at=2)
    previous = harness.release("1.0.0", prepared_at=1)
    harness.activate(active)
    harness.set_state("2.0.0", active, previous=previous)
    report = harness.cleanup(dry_run=False)
    assert active.exists() and previous.exists()
    assert "retention exception" in report.exception_reason
    assert "protected" in report.exception_reason


def test_backup_retention_shared_and_respects_rollback_reference(harness):
    backups = [
        harness.make_backup(f"2026010{index}T000000-{index:04x}", old=10 - index)
        for index in range(1, 6)
    ]
    protected = backups[0]
    harness.set_state("1.0.0", harness.release("1.0.0"), backup=protected)
    report = harness.cleanup(dry_run=False)
    remaining = names(harness.backups)
    assert protected.name in remaining
    assert len(remaining) == 3
    assert backups[-1].name in remaining and backups[-2].name in remaining
    assert backups[1].name not in remaining
    assert any(
        entry.reason == "required by the committed rollback state" for entry in report.retained
    )


def test_incomplete_and_invalid_backups_are_handled_conservatively(harness):
    old_incomplete = harness.make_backup("20260101T000000-0001", complete=False, old=7200)
    recent_incomplete = harness.make_backup("20260102T000000-0002", complete=False, old=0)
    invalid = harness.make_backup("20260103T000000-0003", invalid=True, old=7200)
    valid = harness.make_backup("20260104T000000-0004", old=60)
    harness.set_state("1.0.0", harness.release("1.0.0"), backup=valid)
    report = harness.cleanup(dry_run=False)
    assert old_incomplete.name not in names(harness.backups)
    assert recent_incomplete.name in names(harness.backups)
    assert invalid.name in names(harness.backups)
    assert valid.name in names(harness.backups)
    assert any("recent" in entry.reason for entry in report.retained)
    assert any("manual inspection" in entry.reason for entry in report.retained)


def test_incomplete_release_and_unknown_directories_are_conservative(harness):
    incomplete = harness.releases / "0.1.0-0001"
    incomplete.mkdir()
    (incomplete / "partial").write_bytes(b"x")
    harness.fs.write_staging_marker(incomplete, "release")
    old = time.time() - 7200
    os.utime(incomplete, (old, old))
    unmarked = harness.releases / "0.3.0-0004"
    unmarked.mkdir()
    (unmarked / "keep").write_text("unrelated directory")
    os.utime(unmarked, (old, old))
    unknown = harness.releases / "notes"
    unknown.mkdir()
    (unknown / "keep").write_text("operator notes")
    outside = harness.base / "outside"
    outside.mkdir()
    (outside / "secret").write_text("do not delete")
    symlink = harness.releases / "0.2.0-0002"
    symlink.symlink_to(outside, target_is_directory=True)
    active = harness.release("1.0.0", "0003")
    harness.activate(active)
    harness.set_state("1.0.0", active)
    report = harness.cleanup(dry_run=False)
    assert not incomplete.exists()
    assert (unmarked / "keep").read_text() == "unrelated directory"
    assert unknown.exists() and (unknown / "keep").exists()
    assert outside.exists() and (outside / "secret").exists()
    assert symlink.is_symlink()
    assert any(entry.action == "skip" for entry in report.skipped)


def test_dry_run_performs_no_mutation_and_does_not_stop_service(harness):
    releases = [
        harness.release(version, f"{index:04x}", prepared_at=index)
        for index, version in enumerate(("0.1.0", "0.2.0", "0.3.0", "0.4.0", "0.5.0"))
    ]
    harness.activate(releases[-1])
    harness.set_state("0.5.0", releases[-1])
    before = {path.name: path.stat().st_mtime_ns for path in harness.releases.iterdir()}
    actions = list(harness.actions)
    report = harness.cleanup(dry_run=True, include_caches=True)
    after = {path.name: path.stat().st_mtime_ns for path in harness.releases.iterdir()}
    assert before == after
    assert harness.actions == actions
    assert report.reclaimable_bytes > 0
    assert any(entry.action == "remove" for entry in report.entries)


def test_invalid_state_or_unfinished_transaction_blocks_destructive_cleanup(harness):
    releases = [
        harness.release(version, f"{index:04x}", prepared_at=index)
        for index, version in enumerate(("0.1.0", "0.2.0", "0.3.0", "0.4.0"))
    ]
    harness.activate(releases[-1])
    harness.state.write_text("{invalid-json")
    report = harness.cleanup(dry_run=False)
    assert len(names(harness.releases)) == 4
    assert report.exception_reason
    assert report.removed == []
    atomic_json(
        harness.journal,
        {"phase": "migrating", "previous": str(releases[-2]), "target": str(releases[-1])},
    )
    harness.state.unlink()
    report = harness.cleanup(dry_run=False)
    assert len(names(harness.releases)) == 4
    assert "unfinished" in report.exception_reason


def test_storage_preflight_refuses_low_bytes_before_preparation(harness):
    github = FakeGitHub()
    harness.filesystem_override = lambda path: FilesystemInfo(
        1, Path(path), available_bytes=10 * 1024**2, available_inodes=100000
    )
    with pytest.raises(StorageError) as error:
        harness.prepare(github, {"tag_name": "2.0.0"})
    assert github.calls == []
    assert "Insufficient storage for release preparation" in str(error.value)
    assert "Service: running" in str(error.value)
    assert "Persistent data: unchanged" in str(error.value)
    assert not any(path.name.startswith(".staging-release-") for path in harness.releases.iterdir())


def test_storage_preflight_reports_inode_exhaustion_and_separate_mounts(harness):
    harness.filesystem_override = lambda path: FilesystemInfo(
        1 if str(path).startswith(str(harness.base)) else 2,
        Path(path),
        available_bytes=10 * 1024**3,
        available_inodes=12,
    )
    with pytest.raises(StorageError) as error:
        harness.preflight_prepare(0)
    message = str(error.value)
    assert "inodes" in message
    assert error.value.required_inodes > error.value.available_inodes
    # The data filesystem on a different device is evaluated independently.
    harness.filesystem_override = lambda path: FilesystemInfo(
        1 if str(path).startswith(str(harness.base)) else 2,
        Path(path),
        available_bytes=10 * 1024**3,
        available_inodes=100000,
    )
    assert harness._prepare_requirements(0)


def test_insufficient_space_after_preparation_refuses_before_stopping(harness):
    target = harness.release("2.0.0", "0001")
    old = harness.release("1.0.0", "0002")
    harness.activate(old)
    harness.set_state("1.0.0", old)
    harness.filesystem_override = lambda path: FilesystemInfo(
        1, Path(path), available_bytes=1024, available_inodes=100000
    )
    with pytest.raises(StorageError):
        harness.deploy(target)
    assert "stop" not in harness.actions
    assert harness.current.resolve() == old
    assert (harness.data / "models/weights").read_bytes() == b"retained-model"


@pytest.mark.parametrize(
    ("failure", "category"),
    [
        (OSError(errno.ENOSPC, os.strerror(errno.ENOSPC)), "ENOSPC"),
        (OSError(errno.EACCES, os.strerror(errno.EACCES)), "EACCES"),
        (
            shutil.Error(
                [
                    (
                        "/source",
                        "/destination",
                        OSError(errno.EIO, os.strerror(errno.EIO)),
                    )
                ]
            ),
            "shutil.Error",
        ),
    ],
)
def test_snapshot_storage_failures_report_actionable_primary_error(
    harness, monkeypatch, failure, category
):
    old = harness.release("1.0.0", "0001")
    target = harness.release("2.0.0", "0002")
    harness.activate(old)
    harness.set_state("1.0.0", old)

    def fail_snapshot(**_kwargs):
        raise failure

    monkeypatch.setattr(harness, "snapshot", fail_snapshot)
    with pytest.raises(DeploymentStepError) as error:
        harness.deploy(target)
    message = str(error.value)
    assert "pre-deployment snapshot" in message
    assert category in message
    assert "automatic recovery succeeded" in message
    assert harness.current.resolve() == old
    assert "stop" in harness.actions and "start" in harness.actions


def test_failed_snapshot_cleans_staging_and_does_not_restore_unrelated_backup(harness):
    old = harness.release("1.0.0", "0001")
    harness.activate(old)
    harness.set_state("1.0.0", old)
    unrelated = harness.make_backup("20250101T000000-0001", old=7200, payload=b"unrelated")
    (harness.data / "badlink").symlink_to("/etc/passwd")
    with pytest.raises(ValueError):
        harness.snapshot()
    assert unrelated.exists()
    assert (unrelated / "data/sentinel").read_bytes() == b"unrelated"
    assert not any(path.name.startswith(".staging-backup-") for path in harness.backups.iterdir())
    assert (harness.data / "models/weights").read_bytes() == b"retained-model"


def test_cleanup_failure_does_not_invalidate_successful_deployment(harness, monkeypatch):
    old = harness.release("1.0.0", "0001")
    target = harness.release("2.0.0", "0002")
    harness.activate(old)
    harness.set_state("1.0.0", old)

    def fail_cleanup(**_kwargs):
        raise OSError(errno.EIO, os.strerror(errno.EIO))

    monkeypatch.setattr(harness, "cleanup", fail_cleanup)
    harness.deploy(target)
    assert harness.current.resolve() == target
    assert json.loads(harness.state.read_text())["version"] == "2.0.0"
    assert json.loads(harness.journal.read_text())["phase"] == "committed"


def test_successful_deployment_applies_retention_and_protects_rollback(harness):
    releases = [
        harness.release(version, f"{index:04x}", prepared_at=index)
        for index, version in enumerate(("0.1.0", "0.2.0", "0.3.0", "0.4.0"))
    ]
    old = releases[-1]
    harness.activate(old)
    harness.set_state("0.4.0", old)
    target = harness.release("0.5.0", "0005", prepared_at=50)
    harness.deploy(target)
    remaining = names(harness.releases)
    assert len(remaining) <= 3
    assert target.name in remaining and old.name in remaining
    state = json.loads(harness.state.read_text())
    assert state["version"] == "0.5.0"
    assert state["previous"] == str(old)
    assert Path(state["backup"]).exists()
    # Rollback still works after retention pruning.
    harness.recover(rollback=True)
    assert harness.current.resolve() == old
    assert old.exists()
    assert (harness.data / "models/weights").read_bytes() == b"retained-model"


def test_recovery_before_migration_does_not_restore_data_and_preserves_state(harness, monkeypatch):
    old = harness.release("1.0.0", "0001")
    target = harness.release("2.0.0", "0002")
    harness.activate(old)
    snapshot = harness.make_backup("20260101T000000-0001", old=7200, payload=b"snapshot")
    original_state = {
        "version": "1.0.0",
        "target": str(old),
        "previous": str(harness.release("0.9.0", "0003")),
        "backup": str(snapshot),
        "phase": "committed",
    }
    atomic_json(harness.state, original_state)
    atomic_json(
        harness.journal,
        {
            "phase": "prepared",
            "previous": str(old),
            "target": str(target),
            "backup": str(snapshot),
            "migration_started": False,
        },
    )

    def forbidden_restore(_backup, **_kwargs):
        raise AssertionError("pre-migration recovery must not restore persistent data")

    monkeypatch.setattr(harness, "restore", forbidden_restore)
    harness.recover()
    assert (harness.data / "models/weights").read_bytes() == b"retained-model"
    assert json.loads(harness.state.read_text()) == original_state
    assert json.loads(harness.journal.read_text())["phase"] == "recovered"
    assert harness.current.resolve() == old
    assert "start" in harness.actions


def test_migrating_recovery_restores_snapshot_and_prior_rollback_metadata(harness):
    old = harness.release("1.0.0", "0001")
    target = harness.release("2.0.0", "0002")
    harness.activate(old)
    snapshot = harness.make_backup("20260101T000000-0001", payload=b"old-data")
    older = harness.release("0.9.0", "0003")
    prior_state = {
        "version": "1.0.0",
        "target": str(old),
        "previous": str(older),
        "backup": str(snapshot),
        "phase": "committed",
    }
    atomic_json(harness.state, {"version": "2.0.0", "target": str(target)})
    atomic_json(
        harness.journal,
        {
            "phase": "migrating",
            "previous": str(old),
            "target": str(target),
            "backup": str(snapshot),
            "migration_started": True,
            "prior_state": prior_state,
        },
    )
    (harness.data / "models/weights").write_bytes(b"newer-data")
    harness.recover()
    assert (harness.data / "sentinel").read_bytes() == b"old-data"
    assert json.loads(harness.state.read_text()) == prior_state
    assert json.loads(harness.journal.read_text())["phase"] == "recovered"
    assert harness.current.resolve() == old


def test_recovery_removes_orphan_staging_artifacts(harness):
    old = harness.release("1.0.0", "0001")
    harness.activate(old)
    harness.set_state("1.0.0", old)
    staging_release = harness.releases / ".staging-release-0123456789abcdef"
    staging_release.mkdir()
    harness.fs.write_staging_marker(staging_release, "release")
    staging_backup = harness.backups / ".staging-backup-0123456789abcdef"
    staging_backup.mkdir()
    harness.fs.write_staging_marker(staging_backup, "backup")
    download = harness.base / "download-abcdefgh"
    download.mkdir()
    harness.fs.write_staging_marker(download, "download")
    atomic_json(
        harness.journal,
        {"phase": "prepared", "previous": str(old), "target": str(old), "backup": None},
    )
    harness.recover()
    assert not staging_release.exists()
    assert not staging_backup.exists()
    assert not download.exists()


def test_recovery_protected_artifacts_exceeding_limit_are_not_deleted(harness):
    active = harness.release("4.0.0", "0001")
    previous = harness.release("3.0.0", "0002")
    prior_target = harness.release("2.0.0", "0003")
    prior_previous = harness.release("1.0.0", "0004")
    harness.activate(active)
    prior_backup = harness.make_backup("20260101T000000-0001")
    harness.set_state(
        "4.0.0",
        active,
        previous=previous,
        backup=prior_backup,
        prior_state={
            "version": "2.0.0",
            "target": str(prior_target),
            "previous": str(prior_previous),
            "backup": str(prior_backup),
        },
    )
    atomic_json(harness.journal, {"phase": "recovered"})
    report = harness.cleanup(dry_run=False)
    for path in (active, previous, prior_target, prior_previous, prior_backup):
        assert path.exists()
    assert "retention exception" in report.exception_reason


def test_cleanup_does_not_delete_unrecognized_backups(harness):
    unknown = harness.backups / "operator-archive"
    unknown.mkdir()
    (unknown / "important").write_text("manual backup")
    report = harness.cleanup(dry_run=False)
    assert unknown.exists()
    assert any(entry.reason.startswith("unrecognized backup") for entry in report.skipped)


def test_build_output_pruning_keeps_frontend_distribution_and_frozen_venv(tmp_path):
    harness = Harness(tmp_path)
    target = harness.release("1.0.0")
    node_modules = target / "frontend/node_modules/pkg"
    node_modules.mkdir(parents=True)
    (node_modules / "index.js").write_text("x" * 4096)
    (target / "frontend/dist").mkdir()
    (target / "frontend/dist/index.html").write_text("<html></html>")
    (target / ".pytest_cache").mkdir()
    (target / ".pytest_cache/state").write_text("cache")
    venv = target / ".venv/bin"
    venv.mkdir(parents=True)
    (venv / "python").write_text("binary")
    harness._prune_build_outputs(target)
    assert not (target / "frontend/node_modules").exists()
    assert not (target / ".pytest_cache").exists()
    assert (target / "frontend/dist/index.html").exists()
    assert (target / ".venv/bin/python").exists()


def updater_archive(path, *, abi=3):
    repo = Path(__file__).resolve().parents[1]
    entries = {
        "pyproject.toml": b'[project]\nversion="0.43"\n',
        "hnefatafl/deployment.py": (repo / "hnefatafl/deployment.py")
        .read_bytes()
        .replace(b"UPDATER_ABI = 3", f"UPDATER_ABI = {abi}".encode()),
        "scripts/linux/launcher.py": (repo / "scripts/linux/launcher.py").read_bytes(),
        "scripts/linux/update.sh": (repo / "scripts/linux/update.sh").read_bytes(),
        "scripts/linux/backup.sh": (repo / "scripts/linux/backup.sh").read_bytes(),
    }
    for name in (
        "online_migrations",
        "league_migrations",
        "league_policy",
        "league_recovery_migrations",
        "rating",
    ):
        entries[f"hnefatafl/{name}.py"] = (repo / f"hnefatafl/{name}.py").read_bytes()
    archive(path, entries)
    return path


def configure_updater(harness, tmp_path):
    harness.manage_updater = True
    harness.updater_root = tmp_path / "usr/lib/hnefatafl"
    harness.updater_module = harness.updater_root / "deployment.py"
    harness.updater_support = harness.updater_root / "updater_support"
    harness.updater_commands = (
        tmp_path / "usr/bin/hnefatafl-update",
        tmp_path / "usr/bin/hnefatafl-backup",
    )
    harness.updater_state = tmp_path / "etc/updater.json"
    harness.updater_root.parent.mkdir(parents=True, exist_ok=True)
    harness.updater_commands[0].parent.parent.mkdir(parents=True, exist_ok=True)


def test_installed_updater_refresh_is_atomic_and_preserves_configuration(tmp_path):
    harness = Harness(tmp_path)
    configure_updater(harness, tmp_path)
    harness.release("2.0.0")
    source = updater_archive(tmp_path / "source.tar.gz")
    staging = harness._stage_updater_payload(source, "2.0.0", "sha256:" + "a" * 64)
    plan = harness._updater_publication_plan(staging)
    config_before = (harness.etc / "application.env").read_bytes()
    harness._publish_updater_plan(plan, tag="2.0.0")
    active = json.loads((harness.updater_root / "active.json").read_text())
    assert active["abi"] == 3
    generation = harness.updater_root / "generations" / active["generation"]
    assert (generation / "deployment.py").read_bytes() == (
        staging / "hnefatafl/deployment.py"
    ).read_bytes()
    assert (
        harness.updater_module.read_bytes() == (staging / "scripts/linux/launcher.py").read_bytes()
    )
    assert harness.updater_commands[0].stat().st_mode & 0o111
    assert (generation / "updater_support/online_migrations.py").is_file()
    assert (harness.etc / "application.env").read_bytes() == config_before
    # A second publication of the same verified set is idempotent.
    harness._publish_updater_plan(plan, tag="2.0.0")
    assert harness.updater_module.exists()
    assert not list(harness.updater_root.glob("*.new-*"))
    assert not list(harness.updater_root.glob("*.previous-*"))


def test_refresh_updater_command_installs_only_verified_utilities(tmp_path):
    harness = Harness(tmp_path)
    configure_updater(harness, tmp_path)
    source = updater_archive(tmp_path / "source.tar.gz")
    github = FakeGitHub(source)
    harness.refresh_updater(github, {"tag_name": "0.43", "assets": []})
    assert harness.updater_module.is_file()
    assert list(harness.updater_root.glob("generations/*/updater_support/online_migrations.py"))
    assert names(harness.releases) == []
    assert github.calls


def test_updater_downgrade_is_refused(tmp_path):
    harness = Harness(tmp_path)
    configure_updater(harness, tmp_path)
    harness.updater_root.mkdir(parents=True)
    atomic_json(harness.updater_state, {"schema": 1, "abi": 2, "version": "0.43"})
    old = updater_archive(tmp_path / "old.tar.gz", abi=1)
    with pytest.raises(ValueError, match="downgrade"):
        harness._stage_updater_payload(old, "0.42", "sha256:" + "b" * 64)


def test_prepare_failure_removes_staging_and_preserves_active_release(harness, monkeypatch):
    old = harness.release("1.0.0", "0001")
    harness.activate(old)
    harness.set_state("1.0.0", old)
    source = harness.base / "release.tar.gz"
    archive(
        source,
        {
            "pyproject.toml": b'[project]\nversion="2.0.0"\n',
            "uv.lock": b"",
            "frontend/package-lock.json": b"{}",
            "hnefatafl/auth.py": b"",
            "tests/test_security.py": b"",
            "deployment/hnefatafl.service": b"unit",
        },
    )
    harness.run_failure = subprocess.CalledProcessError(1, "uv")
    with pytest.raises(subprocess.CalledProcessError):
        harness.prepare(FakeGitHub(source), {"tag_name": "2.0.0"})
    assert harness.current.resolve() == old
    assert (harness.data / "models/weights").read_bytes() == b"retained-model"
    assert not any(path.name.startswith(".staging-release-") for path in harness.releases.iterdir())


def test_cleanup_never_touches_live_sqlite_or_persistent_data(harness):
    database = harness.data / "hnefatafl.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE persistent(value TEXT)")
        connection.execute("INSERT INTO persistent VALUES('game')")
        connection.commit()
    harness.cleanup(dry_run=False, include_caches=True)
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT value FROM persistent").fetchone()[0] == "game"
    assert (harness.data / "models/weights").read_bytes() == b"retained-model"
    assert (harness.data / "jobs/job.log").exists()


def test_existing_041_and_042_release_layouts_are_recognized(harness):
    old = harness.release("0.41.1", "1a2b", prepared_at=10)
    current = harness.release("0.42", "3c4d", prepared_at=20)
    harness.activate(current)
    harness.set_state("0.42", current, previous=old)
    report = harness.cleanup(dry_run=True)
    assert old.exists() and current.exists()
    assert any(entry.version == "0.41.1" for entry in report.entries)
    assert any(entry.version == "0.42" for entry in report.entries)
    assert report.reclaimable_bytes == 0


def test_prepare_publishes_atomically_and_removes_build_only_files(harness):
    staging_paths = []

    def run(args, **kwargs):
        harness.actions.append([str(value) for value in args])
        cwd = kwargs.get("cwd")
        if cwd is not None:
            staging_paths.append(Path(cwd))
            if "ci" in [str(value) for value in args]:
                modules = Path(cwd) / "frontend/node_modules/build-only"
                modules.mkdir(parents=True)
                (modules / "payload.bin").write_bytes(b"n" * 65536)
            if "build" in [str(value) for value in args]:
                distribution = Path(cwd) / "frontend/dist"
                distribution.mkdir(parents=True)
                (distribution / "index.html").write_text("<html></html>")
            if str(args[0]).endswith(".venv/bin/python") or "uv" in str(args[0]):
                venv = Path(cwd) / ".venv/bin"
                venv.mkdir(parents=True, exist_ok=True)
                python = venv / "python"
                python.write_text("#!/bin/sh\nexit 0\n")
                python.chmod(0o755)
                (venv / "activate").write_text(f"VIRTUAL_ENV={cwd}/.venv\n")
                uvicorn = venv / "uvicorn"
                uvicorn.write_text(f"#!{python}\n")
                uvicorn.chmod(0o755)

    harness.run = run
    source = harness.base / "release.tar.gz"
    archive(
        source,
        {
            "pyproject.toml": b'[project]\nversion="2.1.0"\n',
            "uv.lock": b"",
            "frontend/package-lock.json": b"{}",
            "hnefatafl/auth.py": b"",
            "tests/test_security.py": b"",
            "deployment/hnefatafl.service": b"unit",
        },
    )
    target = harness.prepare(FakeGitHub(source), {"tag_name": "2.1.0"})
    assert target.parent == harness.releases
    assert not any(path.name.startswith(".staging-release-") for path in harness.releases.iterdir())
    assert (target / "frontend/dist/index.html").exists()
    assert not (target / "frontend/node_modules").exists()
    metadata = json.loads((target / ".release.json").read_text())
    assert metadata["version"] == "2.1.0"
    assert metadata["peak_bytes"] > metadata["size_bytes"] > 0
    assert metadata["prepared_at"] > 0
    # The environment is built at its permanent path (not renamed into place),
    # so generated entry points keep stable absolute paths.
    assert staging_paths and all(path == target for path in staging_paths)
    assert metadata["peak_inodes"] > 0
    assert metadata["size_inodes"] > metadata["file_count"]


def test_installer_refresh_only_path_installs_utility_without_application_update():
    root = Path(__file__).resolve().parents[1]
    script = (root / "scripts/linux/install.sh").read_text()
    assert "--refresh-updater-only" in script
    assert "install_deployment_utilities" in script
    assert "/usr/local/lib/hnefatafl" in script
    # Companion restore modules are published with the utility set so the
    # standalone CLI can restore SQLite snapshots without a source checkout.
    assert 'installer["install_reviewed_updater"]' in script
    assert "/run/lock/hnefatafl-update.lock" in script
    # Configuration, state and transaction files are never copied over by the
    # updater refresh.
    assert (
        "/etc/hnefatafl/deployment.json"
        not in script.split("install_deployment_utilities()", 1)[1].split("}", 1)[0]
    )


def test_rollback_without_verified_snapshot_is_refused_before_service_change(harness):
    old = harness.release("1.0.0", "0001")
    current = harness.release("2.0.0", "0002")
    harness.activate(current)
    harness.set_state("2.0.0", current, previous=old, backup=None)
    actions = list(harness.actions)
    with pytest.raises(ValueError, match="No verified pre-deployment snapshot"):
        harness.recover(rollback=True)
    assert harness.actions == actions
    assert harness.current.resolve() == current
    assert (harness.data / "models/weights").read_bytes() == b"retained-model"


def test_accumulated_release_and_backup_history_is_bounded(harness):
    versions = ("0.36.0", "0.37.0", "0.38.0", "0.39.0", "0.40.0", "0.41.1")
    releases = [
        harness.release(version, f"{index:04x}", prepared_at=index, payload_size=4096)
        for index, version in enumerate(versions)
    ]
    backups = [
        harness.make_backup(
            f"2026010{index}T000000-{index:04x}",
            complete=index != 2,
            old=7200 if index == 2 else 100 - index,
            payload=b"b" * 4096,
        )
        for index in range(1, 8)
    ]
    active, previous = releases[-1], releases[-2]
    protected = backups[-3]
    harness.activate(active)
    harness.set_state("0.41.1", active, previous=previous, backup=protected)
    report = harness.cleanup(dry_run=False)
    assert active.exists() and previous.exists()
    assert protected.exists()
    assert len(names(harness.releases)) <= 3
    assert len(names(harness.backups)) <= 3
    # The old incomplete snapshot is removed; no live/persistent data is touched.
    assert not (harness.backups / "20260102T000000-0002").exists()
    assert (harness.data / "models/weights").read_bytes() == b"retained-model"
    assert (harness.data / "jobs/job.log").exists()
    assert report.reclaimable_bytes > 0


@pytest.mark.parametrize("separate_filesystems", [False, True])
def test_snapshot_fits_but_automatic_recovery_requires_more_space(harness, separate_filesystems):
    target = harness.release("2.0.0", "0001")
    old = harness.release("1.0.0", "0002")
    harness.activate(old)
    harness.set_state("1.0.0", old)
    with sqlite3.connect(harness.data / "hnefatafl.sqlite3") as database:
        database.execute("CREATE TABLE history(payload BLOB)")
        database.execute("INSERT INTO history VALUES(zeroblob(?))", (48 * 1024**2,))
    with (harness.data / "jobs/job.log").open("rb+") as history:
        history.truncate(192 * 1024**2)

    backup_requirement = harness.snapshot_requirement()
    free = backup_requirement.bytes + 32 * 1024**2
    harness.storage_settings["headroom_mib"] = 0

    def filesystem(path):
        path = Path(path)
        device = 2 if separate_filesystems and path == harness.backups else 1
        return FilesystemInfo(device, path, free, 100000)

    harness.filesystem_override = filesystem
    harness.check_storage([backup_requirement], phase="standalone snapshot")
    with pytest.raises(StorageError, match="restore candidate, private live SQLite authority"):
        harness.deploy(target)
    assert harness.actions == []
    assert harness.current.resolve() == old
    assert (harness.data / "jobs/job.log").stat().st_size == 192 * 1024**2
    assert not harness.journal.exists()


def test_storage_recheck_after_shutdown_before_snapshot(harness, monkeypatch):
    old = harness.release("1.0.0", "0001")
    target = harness.release("2.0.0", "0002")
    harness.activate(old)
    harness.set_state("1.0.0", old)

    def filesystem(path):
        available = 1 if harness.actions and harness.actions[-1] == "stop" else 10 * 1024**3
        return FilesystemInfo(1, Path(path), available, 100000)

    harness.filesystem_override = filesystem
    monkeypatch.setattr(harness, "snapshot", lambda **kw: pytest.fail("unexpected snapshot"))
    with pytest.raises(DeploymentStepError, match="post-stop storage preflight"):
        harness.deploy(target)
    assert harness.current.resolve() == old
    assert harness.actions.count("start") == 1
    assert not list(harness.data.parent.glob(".retained-*"))


def test_failed_first_install_preserves_written_data_in_recovery_tree(harness, monkeypatch):
    target = harness.release("1.0.0", "0001")
    original = (harness.data / "models/weights").read_bytes()
    run = harness.run

    def fail_init(args, **kwargs):
        if "init-admin" in args:
            (harness.data / "models/install-output").write_bytes(b"created by failed installation")
            raise OSError("fixture initialization failure")
        return run(args, **kwargs)

    monkeypatch.setattr(harness, "run", fail_init)
    with pytest.raises(DeploymentStepError, match="initial administrator provisioning"):
        harness.deploy(target, initialize=True)
    assert not harness.current.exists()
    assert (harness.data / "models/weights").read_bytes() == original
    assert not (harness.data / "models/install-output").exists()
    (retained,) = harness.data.parent.glob(".retained-*")
    assert (retained / "models/install-output").read_bytes() == b"created by failed installation"
    assert json.loads(harness.journal.read_text())["phase"] == "recovered"


@pytest.mark.parametrize("limit", [1, 2, 3])
def test_storage_reclamation_during_updater_refresh_protects_the_exact_target(tmp_path, limit):
    harness = Harness(tmp_path, {"retention": {"releases": limit, "backups": limit}})
    configure_updater(harness, tmp_path)
    old = harness.release("1.0.0", "0001", prepared_at=4)
    rollback = harness.release("0.99.0", "0002", prepared_at=3)
    target = harness.release("0.43", "0003", prepared_at=1)
    duplicate = harness.release("0.43", "0004", prepared_at=5)
    harness.activate(old)
    backup = harness.make_backup("20260101T000000-0001")
    harness.set_state("1.0.0", old, previous=rollback, backup=backup)
    source = updater_archive(tmp_path / "source.tar.gz")
    digest = "sha256:" + "c" * 64
    marker = target / ".release.json"
    atomic_json(marker, {**json.loads(marker.read_text()), "sha256": digest})
    harness._stage_updater_payload(source, "0.43", digest)

    def filesystem(path):
        path = Path(path)
        if path == harness.updater_root:
            for protected in (target, old, rollback, backup):
                assert protected.is_dir()
            return FilesystemInfo(2, path, 0 if duplicate.exists() else 10 * 1024**3, 100000)
        return FilesystemInfo(1, path, 10 * 1024**3, 100000)

    harness.filesystem_override = filesystem
    harness.deploy(target)
    assert harness.current.resolve() == target
    assert harness._require_complete_release(target)["sha256"] == digest
    assert not duplicate.exists()
    assert old.is_dir()
    assert json.loads((harness.updater_root / "active.json").read_text())["abi"] == 3


@pytest.mark.parametrize("constrained", ["temporary bytes", "temporary inodes", "updater bytes"])
def test_preparation_checks_separate_temporary_and_updater_filesystems(tmp_path, constrained):
    harness = Harness(tmp_path)
    configure_updater(harness, tmp_path)
    devices = {harness.temporary_root: 2, harness.updater_root: 3}

    def filesystem(path):
        path = Path(path)
        device = devices.get(path, 1)
        byte_limited = (device, constrained) in ((2, "temporary bytes"), (3, "updater bytes"))
        inode_limited = device == 2 and constrained == "temporary inodes"
        return FilesystemInfo(
            device,
            path,
            16 * 1024**2 if byte_limited else 20 * 1024**3,
            1 if inode_limited else 100000,
        )

    harness.filesystem_override = filesystem
    with pytest.raises(StorageError) as error:
        harness.preflight_prepare(32 * 1024**2)
    expected = harness.updater_root if constrained.startswith("updater") else harness.temporary_root
    assert error.value.filesystem == str(expected)
    assert not harness.actions


def test_same_filesystem_preparation_accounts_for_the_archive_once(harness):
    mib = 1024**2
    archive_bytes = 2048 * mib
    requirements = harness._prepare_requirements(archive_bytes)
    assert sum(item.bytes for item in requirements if "download" in item.label) == archive_bytes
    common = [item for item in requirements if item.stage is None]
    expected_bytes = sum(item.bytes for item in common) + archive_bytes
    expected_bytes += harness.storage_settings["headroom_mib"] * mib
    limits = [expected_bytes, expected_bytes - 1]
    harness.filesystem_override = lambda path: FilesystemInfo(1, Path(path), limits[0], 100000)
    harness.check_storage(requirements, phase="shared release and download filesystem")
    limits.pop(0)
    with pytest.raises(StorageError) as error:
        harness.check_storage(requirements, phase="shared release and download filesystem")
    assert error.value.required_bytes == expected_bytes


def test_0411_to_042_low_disk_scenario_refuses_before_service_stop(harness):
    """Regression for the reported 0.41.1 to 0.42 snapshot/storage failure."""
    releases = [
        harness.release(version, f"{index:04x}", prepared_at=index, payload_size=8192)
        for index, version in enumerate(
            ("0.36.0", "0.37.0", "0.38.0", "0.39.0", "0.40.0", "0.41.1")
        )
    ]
    active, previous = releases[-1], releases[-2]
    harness.activate(active)
    for index in range(1, 8):
        harness.make_backup(
            f"2026010{index}T000000-{index:04x}",
            complete=index != 2,
            old=7200 if index == 2 else 100 - index,
            payload=b"b" * 8192,
        )
    reference_backup = harness.backups / "20260105T000000-0005"
    harness.set_state("0.41.1", active, previous=previous, backup=reference_backup)
    target = harness.release("0.42", "00ff", prepared_at=100, payload_size=8192)
    harness.filesystem_override = lambda path: FilesystemInfo(
        1, Path(path), available_bytes=136 * 1024**2, available_inodes=100
    )
    with pytest.raises(StorageError) as error:
        harness.deploy(target)
    message = str(error.value)
    assert "stop" not in harness.actions and "start" not in harness.actions
    assert harness.current.resolve() == active
    assert (harness.data / "models/weights").read_bytes() == b"retained-model"
    assert "Update refused before stopping the service." in message
    assert "hnefatafl-update --cleanup --dry-run" in message
    assert "Proxmox thin pool" in message


def test_unmanifested_backup_content_is_invalid_and_retained(harness):
    backup = harness.make_backup("20260101T000000-0001", old=7200)
    (backup / "data/extra").write_text("not covered by the manifest")
    harness.set_state("1.0.0", harness.release("1.0.0"))
    report = harness.cleanup(dry_run=False)
    assert backup.exists()
    assert any("manual inspection" in entry.reason for entry in report.retained)


def test_invalid_updater_source_is_not_installed(tmp_path):
    harness = Harness(tmp_path)
    configure_updater(harness, tmp_path)
    harness.updater_module.parent.mkdir(parents=True)
    harness.updater_module.write_text("# original\n")
    repo = Path(__file__).resolve().parents[1]
    source = tmp_path / "broken.tar.gz"
    archive(
        source,
        {
            "hnefatafl/deployment.py": b"def broken(:\n",
            "scripts/linux/launcher.py": (repo / "scripts/linux/launcher.py").read_bytes(),
            "scripts/linux/update.sh": b"#!/bin/sh\nexit 0\n",
            "scripts/linux/backup.sh": b"#!/bin/sh\nexit 0\n",
            **{
                f"hnefatafl/{name}.py": (repo / f"hnefatafl/{name}.py").read_bytes()
                for name in (
                    "online_migrations",
                    "league_migrations",
                    "league_policy",
                    "league_recovery_migrations",
                    "rating",
                )
            },
        },
    )
    with pytest.raises(ValueError, match="invalid deployment utility"):
        harness._stage_updater_payload(source, "2.0.0", "sha256:" + "c" * 64)
    assert harness.updater_module.read_text() == "# original\n"
    assert not harness.updater_commands[0].exists()


def test_preparation_reuses_recorded_temporary_peak(harness):
    release = harness.release("0.42")
    marker = release / ".release.json"
    peak = 2 * 1024**3
    atomic_json(
        marker,
        {
            **json.loads(marker.read_text()),
            "temporary_peak_bytes": peak,
            "temporary_peak_inodes": 60300,
        },
    )
    workspace = next(
        item
        for item in harness._prepare_requirements(0)
        if item.path == harness.temporary_root and item.stage == "build"
    )
    assert workspace.bytes == peak * 115 // 100
    assert workspace.inodes == 60300 * 115 // 100
