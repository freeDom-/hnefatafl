"""Manual backup, rollback preflight and interruption-safe restore regressions."""

import json
import os
import shutil
import sqlite3
import time
from pathlib import Path

import pytest

from deployment_fixtures import legacy_snapshot

from hnefatafl.deployment import (
    DeploymentError,
    DeploymentStepError,
    FilesystemInfo,
    ReadinessError,
    StorageError,
    atomic_json,
)
from test_deployment_storage import Harness, names


def recovery_harness(tmp_path):
    harness = Harness(tmp_path)
    old = harness.release("1.0.0", "0001", prepared_at=10)
    target = harness.release("2.0.0", "0002", prepared_at=20)
    harness.activate(old)
    harness.set_state("1.0.0", old)
    return harness, old, target


def with_database(harness, value="original"):
    database = harness.data / "hnefatafl.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE IF NOT EXISTS persistent(value TEXT)")
        connection.execute("DELETE FROM persistent")
        connection.execute("INSERT INTO persistent VALUES(?)", (value,))
        connection.commit()
    return database


def live_value(harness):
    with sqlite3.connect(harness.data / "hnefatafl.sqlite3") as connection:
        return connection.execute("SELECT value FROM persistent").fetchone()[0]


# ---------------------------------------------------------------------------
# F02 - manual backups
# ---------------------------------------------------------------------------


def test_manual_backup_refuses_insufficient_storage_without_stopping_service(tmp_path):
    harness, _old, _target = recovery_harness(tmp_path)
    harness._service_is_active = lambda: True
    harness.filesystem_override = lambda path: FilesystemInfo(
        1, Path(path), available_bytes=1024, available_inodes=100
    )
    with pytest.raises(StorageError):
        harness.backup()
    assert "stop" not in harness.actions
    assert not any(entry.name.startswith("2026") for entry in harness.backups.iterdir())


def test_manual_backup_checks_the_backup_filesystem_only(tmp_path):
    harness, _old, _target = recovery_harness(tmp_path)
    harness._service_is_active = lambda: True

    def filesystem(path):
        path = Path(path)
        if str(path).startswith(str(harness.backups)):
            return FilesystemInfo(9, path, available_bytes=1024, available_inodes=10)
        return FilesystemInfo(1, path, 100 * 1024**3, 100000)

    harness.filesystem_override = filesystem
    with pytest.raises(StorageError) as error:
        harness.backup()
    assert error.value.filesystem.startswith(str(harness.backups))
    assert "stop" not in harness.actions


def test_manual_backup_snapshot_failure_removes_only_staging_and_restarts(tmp_path, monkeypatch):
    harness, _old, _target = recovery_harness(tmp_path)
    harness._service_is_active = lambda: True
    preserved = harness.make_backup("20260101T000000-0001", payload=b"preserved")

    def fail(**_kwargs):
        raise OSError(28, os.strerror(28))

    monkeypatch.setattr(harness, "snapshot", fail)
    with pytest.raises(DeploymentError) as error:
        harness.backup()
    message = str(error.value)
    assert "Manual backup failed" in message
    assert "previously verified backups were preserved" in message
    assert "ENOSPC" in message
    assert preserved.exists()
    assert (preserved / "data/sentinel").read_bytes() == b"preserved"
    assert "stop" in harness.actions and "start" in harness.actions
    assert "probe" in harness.actions


def test_manual_backup_recovery_failure_is_reported_separately(tmp_path, monkeypatch):
    harness, _old, _target = recovery_harness(tmp_path)
    harness._service_is_active = lambda: True

    def fail(**_kwargs):
        raise OSError(5, os.strerror(5))

    monkeypatch.setattr(harness, "snapshot", fail)
    harness.probe_failure = ReadinessError("synthetic readiness failure")
    with pytest.raises(DeploymentError) as error:
        harness.backup()
    message = str(error.value)
    assert "Manual backup failed" in message
    assert "automatic service recovery also failed" in message
    assert "synthetic readiness failure" in message


def test_manual_backup_applies_retention_and_keeps_the_new_backup(tmp_path):
    harness, _old, _target = recovery_harness(tmp_path)
    harness._service_is_active = lambda: True
    for index in range(5):
        harness.make_backup(f"2026010{index + 1}T000000-{index + 1:04x}", old=100 + index)
    created = harness.backup()
    assert created.exists()
    backups = names(harness.backups)
    assert created.name in backups
    assert len(backups) <= 3


def test_manual_backup_with_protected_history_reports_exception(tmp_path):
    harness, old, _target = recovery_harness(tmp_path)
    harness._service_is_active = lambda: True
    protected = [
        harness.make_backup(f"2026010{index + 1}T000000-{index + 1:04x}") for index in range(2)
    ]
    harness.set_state("1.0.0", old, backup=protected[0])
    created = harness.backup()
    assert created.exists()
    assert protected[0].exists()
    assert all(path.exists() for path in protected)


# ---------------------------------------------------------------------------
# F03 - verify snapshots before interrupting a healthy service
# ---------------------------------------------------------------------------


def test_rollback_corrupted_snapshot_is_refused_before_service_change(tmp_path):
    harness, old, current = recovery_harness(tmp_path)
    harness.activate(current)
    harness.set_state("2.0.0", current, previous=old)
    backup = harness.make_backup("20260101T000000-0001")
    with_database(harness)
    atomic_json(
        harness.state,
        {"version": "2.0.0", "target": str(current), "previous": str(old), "backup": str(backup)},
    )
    (backup / "data/sentinel").write_bytes(b"corrupted")
    actions = list(harness.actions)
    with pytest.raises(DeploymentError, match="snapshot verification failed"):
        harness.recover(rollback=True)
    assert harness.actions == actions
    assert harness.current.resolve() == current


def test_rollback_missing_snapshot_is_refused_before_service_change(tmp_path):
    harness, old, current = recovery_harness(tmp_path)
    harness.activate(current)
    backup = harness.make_backup("20260101T000000-0001")
    shutil.rmtree(backup)
    harness.set_state("2.0.0", current, previous=old, backup=backup)
    actions = list(harness.actions)
    with pytest.raises(DeploymentError, match="(does not exist|Cannot inspect)"):
        harness.recover(rollback=True)
    assert harness.actions == actions
    assert harness.current.resolve() == current


def test_rollback_snapshot_from_different_release_is_refused(tmp_path):
    harness, old, current = recovery_harness(tmp_path)
    other = harness.release("0.9.0", "0003", prepared_at=1)
    harness.activate(old)
    snapshot = harness.snapshot()
    harness.activate(current)
    atomic_json(
        harness.state,
        {
            "version": "2.0.0",
            "target": str(current),
            "previous": str(other),
            "backup": str(snapshot),
        },
    )
    actions = list(harness.actions)
    with pytest.raises(DeploymentError, match="created for release"):
        harness.recover(rollback=True)
    assert harness.actions == actions
    assert harness.current.resolve() == current


def test_legacy_snapshot_without_association_is_accepted_with_warning(tmp_path, capsys):
    harness, old, current = recovery_harness(tmp_path)
    harness.activate(current)
    legacy = harness.make_backup("20260101T000000-0001", payload=b"legacy")
    harness.set_state("2.0.0", current, previous=old, backup=legacy)
    harness.recover(rollback=True)
    assert harness.current.resolve() == old
    assert (harness.data / "sentinel").read_bytes() == b"legacy"
    assert "legacy metadata" in capsys.readouterr().out


def test_restore_storage_shortage_refuses_before_stopping(tmp_path):
    harness, old, current = recovery_harness(tmp_path)
    harness.activate(old)
    snapshot = legacy_snapshot(harness)
    harness.activate(current)
    atomic_json(
        harness.state,
        {"version": "2.0.0", "target": str(current), "previous": str(old), "backup": str(snapshot)},
    )
    harness._service_is_active = lambda: True
    harness.filesystem_override = lambda path: FilesystemInfo(
        1, Path(path), available_bytes=1024, available_inodes=100
    )
    actions = list(harness.actions)
    with pytest.raises(StorageError):
        harness.recover(rollback=True)
    assert harness.actions == actions
    assert harness.current.resolve() == current


# ---------------------------------------------------------------------------
# F06 - interruption-safe restore and rollback
# ---------------------------------------------------------------------------


def interrupted_recovery_journal(harness, old, target, snapshot, database):
    state = {
        "version": "1.0.0",
        "target": str(old),
        "previous": None,
        "backup": None,
        "phase": "committed",
    }
    atomic_json(harness.state, state)
    atomic_json(
        harness.journal,
        {
            "phase": "migrating",
            "previous": str(old),
            "target": str(target),
            "backup": str(snapshot),
            "prior_state": state,
            "service_stopped": True,
            "migration_started": True,
            "data_modified": True,
            "restore": None,
        },
    )


@pytest.mark.parametrize(
    "phase", ["prepared", "staged", "stopping", "data-renamed", "published", "ownership-set"]
)
def test_restore_interrupted_after_each_transition_is_recoverable(tmp_path, phase):
    harness, old, target = recovery_harness(tmp_path)
    database = with_database(harness, "snapshot-state")
    snapshot = legacy_snapshot(harness)
    with_database(harness, "newer-live-state")
    (harness.data / "jobs/newer.log").write_text("newer data")
    interrupted_recovery_journal(harness, old, target, snapshot, database)

    original_record = harness.record
    fired = {"value": False}

    def interrupting_record(value):
        restore = value.get("restore") if isinstance(value, dict) else None
        if not fired["value"] and isinstance(restore, dict) and restore.get("phase") == phase:
            fired["value"] = True
            raise KeyboardInterrupt("synthetic interruption")
        return original_record(value)

    harness.record = interrupting_record
    try:
        harness.recover()
    except (KeyboardInterrupt, DeploymentStepError, DeploymentError, StorageError):
        pass
    harness.record = original_record
    if phase != "prepared":
        assert fired["value"], "the requested interruption point was not reached"

    harness.recover()

    assert json.loads(harness.journal.read_text())["phase"] == "recovered"
    assert harness.current.resolve() == old
    assert live_value(harness) == "snapshot-state"
    assert not (harness.data / "jobs/newer.log").exists()
    retained = [
        path for path in harness.data.parent.iterdir() if path.name.startswith(".retained-")
    ]
    assert retained, "previous persistent data must be retained for manual recovery"
    assert (retained[0] / "jobs/newer.log").read_text() == "newer data"


@pytest.mark.parametrize("phase", ["staged", "stopping", "data-renamed", "published"])
def test_rollback_interrupted_after_each_transition_is_recoverable(tmp_path, phase):
    harness, old, current = recovery_harness(tmp_path)
    with_database(harness, "snapshot-state")
    snapshot = legacy_snapshot(harness)
    with_database(harness, "newer-live-state")
    harness.activate(current)
    atomic_json(
        harness.state,
        {
            "version": "2.0.0",
            "target": str(current),
            "previous": str(old),
            "backup": str(snapshot),
            "phase": "committed",
        },
    )
    original_record = harness.record
    fired = {"value": False}

    def interrupting_record(value):
        restore = value.get("restore") if isinstance(value, dict) else None
        if not fired["value"] and isinstance(restore, dict) and restore.get("phase") == phase:
            fired["value"] = True
            raise KeyboardInterrupt("synthetic interruption")
        return original_record(value)

    harness.record = interrupting_record
    try:
        harness.recover(rollback=True)
    except (KeyboardInterrupt, DeploymentStepError, DeploymentError, StorageError):
        pass
    harness.record = original_record
    assert fired["value"], "the requested interruption point was not reached"

    harness.recover()

    assert harness.current.resolve() == old
    assert live_value(harness) == "snapshot-state"
    state = json.loads(harness.state.read_text())
    assert state["version"] == "1.0.0"
    assert state["previous"] is None and state["backup"] is None


def test_retained_persistent_data_is_never_pruned(tmp_path):
    harness, old, _current = recovery_harness(tmp_path)
    retained = harness.data.parent / ".retained-0123456789abcdef"
    retained.mkdir()
    (retained / "models").mkdir()
    (retained / "models/weights").write_bytes(b"must survive")
    report = harness.cleanup(dry_run=False)
    assert retained.exists()
    assert (retained / "models/weights").read_bytes() == b"must survive"
    assert any(entry.category == "retained" for entry in report.entries)


def test_abandoned_restore_staging_requires_a_valid_marker(tmp_path):
    harness, old, _current = recovery_harness(tmp_path)
    unmarked = harness.data.parent / ".restore-aaaaaaaaaaaaaaaa"
    unmarked.mkdir()
    marked = harness.data.parent / ".restore-bbbbbbbbbbbbbbbb"
    marked.mkdir()
    atomic_json(
        marked / ".restore.json",
        {"schema": 1, "transaction": "bbbbbbbb", "backup": "x", "created_at": time.time()},
    )
    old_time = time.time() - 7200
    os.utime(unmarked, (old_time, old_time))
    os.utime(marked, (old_time, old_time))
    report = harness.cleanup(dry_run=False)
    assert unmarked.exists()
    assert not marked.exists()
    assert any(entry.category == "restore-staging" for entry in report.retained)


def test_restore_staging_referenced_by_the_journal_is_protected(tmp_path):
    harness, old, target = recovery_harness(tmp_path)
    with_database(harness)
    snapshot = legacy_snapshot(harness)
    staging = harness.data.parent / ".restore-cccccccc"
    staging.mkdir()
    atomic_json(
        staging / ".restore.json",
        {
            "schema": 1,
            "transaction": "cccccccc",
            "backup": str(snapshot),
            "created_at": time.time(),
        },
    )
    old_time = time.time() - 9000
    os.utime(staging, (old_time, old_time))
    atomic_json(
        harness.journal,
        {
            "phase": "migrating",
            "previous": str(old),
            "target": str(target),
            "backup": str(snapshot),
            "migration_started": True,
            "restore": {
                "phase": "staged",
                "transaction": "cccccccc",
                "backup": str(snapshot),
                "data": str(harness.data),
                "staging": str(staging),
                "retained": str(harness.data.parent / ".retained-cccccccc"),
            },
        },
    )
    report = harness.cleanup(dry_run=False, allow_unfinished=True)
    assert staging.exists()
    assert any("unfinished transaction journal" in entry.reason for entry in report.retained)


@pytest.mark.parametrize(
    "change,error",
    [
        ("missing-field", "Invalid versioned snapshot"),
        ("missing-file", "metadata is missing"),
        ("empty", "Malformed legacy snapshot"),
        ("wrong-transaction", "does not match the recorded association"),
        ("wrong-digest", "archive digest disagrees"),
        ("wrong-release-directory", "does not match the rollback directory"),
        ("wrong-purpose", "purpose is not suitable"),
        ("malformed", "Invalid snapshot association metadata"),
    ],
)
def test_current_snapshot_association_is_enforced_before_downtime(tmp_path, change, error):
    from hnefatafl.deployment import checksum

    harness, old, target = recovery_harness(tmp_path)
    with_database(harness, "pre-deployment")
    harness.deploy(target)
    state = json.loads(harness.state.read_text())
    backup = Path(state["backup"])
    association_path = backup / "snapshot.json"
    association = json.loads(association_path.read_text())
    journal = json.loads(harness.journal.read_text())
    assert association == state["backup_association"] == journal["backup_association"]
    assert association["snapshot"] == journal["snapshot_id"]
    assert association["transaction"] == journal["transaction"]
    manifest = json.loads((backup / "manifest.json").read_text())
    if change == "missing-field":
        del association["database_schema"]
    elif change == "empty":
        association = {}
    elif change == "wrong-transaction":
        association["transaction"] = "f" * 32
    elif change == "wrong-digest":
        association["release_sha256"] = "sha256:" + "f" * 64
    elif change == "wrong-release-directory":
        association["release"] = str(harness.release("1.0.0", "abcd"))
    elif change == "wrong-purpose":
        association["purpose"] = "manual"
    if change == "missing-file":
        association_path.unlink()
        del manifest["snapshot.json"]
    else:
        if change == "malformed":
            association_path.write_text("{not a JSON object")
        else:
            atomic_json(association_path, association)
        manifest["snapshot.json"] = checksum(association_path)
    atomic_json(backup / "manifest.json", manifest)
    before = harness.journal.read_bytes()
    harness.actions.clear()
    with pytest.raises(DeploymentError, match=error):
        harness.recover(rollback=True)
    assert harness.current.resolve() == target and harness.actions == []
    assert harness.journal.read_bytes() == before
    assert old.is_dir() and backup.is_dir()
    assert live_value(harness) == "pre-deployment"


def test_explicit_rollback_preserves_the_older_valid_rollback_reference(tmp_path):
    harness, first, second = recovery_harness(tmp_path)
    with_database(harness, "first")
    harness.deploy(second)
    second_state = json.loads(harness.state.read_text())
    with_database(harness, "second")
    third = harness.release("3.0.0", "0003")
    harness.deploy(third)
    with_database(harness, "third")
    harness.recover(rollback=True)
    assert harness.current.resolve() == second
    assert json.loads(harness.state.read_text()) == second_state
    assert Path(second_state["backup"]).is_dir()
    assert second_state["previous"] == str(first)
    assert live_value(harness) == "second"


@pytest.mark.parametrize("mounted", ["root", "nested"])
def test_mounted_persistent_data_refuses_rollback_before_downtime(tmp_path, mounted):
    from hnefatafl.deployment import PrivilegedFilesystem
    from test_deployment_hardening import write_mountinfo

    harness, old, target = recovery_harness(tmp_path)
    snapshot = legacy_snapshot(harness)
    harness.activate(target)
    harness.set_state("2.0.0", target, previous=old, backup=snapshot)
    mount = harness.data if mounted == "root" else harness.data / "models"
    harness.fs = PrivilegedFilesystem(
        mountinfo=write_mountinfo(tmp_path / "mountinfo", [str(mount)])
    )
    with pytest.raises(DeploymentError, match="unmounted persistent data directory"):
        harness.recover(rollback=True)
    assert harness.actions == [] and harness.current.resolve() == target
    assert not harness.journal.exists()


def test_startup_guard_requires_durable_activation_and_authoritative_data(tmp_path):
    harness, old, target = recovery_harness(tmp_path)
    with_database(harness)
    harness.deploy(target)
    harness._authorize_service_start()
    transaction = json.loads(harness.journal.read_text())
    transaction["phase"] = "activating"
    atomic_json(harness.journal, transaction)
    with pytest.raises(DeploymentError, match="startup is deferred"):
        harness._authorize_service_start()
    transaction["phase"] = "activated"
    atomic_json(harness.journal, transaction)
    harness._authorize_service_start()
    harness.activate(old)
    with pytest.raises(DeploymentError, match="startup is deferred"):
        harness._authorize_service_start()
