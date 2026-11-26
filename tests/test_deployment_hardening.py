"""Regression tests for the storage-aware deployment review findings.

Everything runs against disposable directories. systemd, GitHub and production
paths are never touched.
"""

import errno
import json
import os
import shutil
import time
from pathlib import Path

import pytest

from hnefatafl.deployment import (
    DeploymentError,
    PrivilegedFilesystem,
    StorageError,
    atomic_json,
    checksum,
    scan_usage,
)
from test_deployment_storage import Harness, names

REPO = Path(__file__).resolve().parents[1]


def valid_harness(tmp_path, config=None):
    harness = Harness(tmp_path, config)
    active = harness.release("2.0.0", "0001", prepared_at=20)
    harness.activate(active)
    harness.set_state("2.0.0", active)
    return harness, active


# ---------------------------------------------------------------------------
# F01 - privileged filesystem containment
# ---------------------------------------------------------------------------


def test_symlinked_managed_release_root_is_refused_without_deleting(tmp_path):
    harness, active = valid_harness(tmp_path)
    outside = tmp_path / "outside"
    planted = outside / "0.9.0-0001"
    planted.mkdir(parents=True)
    (planted / "payload").write_text("must survive")
    atomic_json(planted / ".release.json", {"version": "0.9.0", "prepared_at": 1})
    old = time.time() - 7200
    os.utime(planted, (old, old))
    for index in range(2, 6):
        duplicate = outside / f"0.{index}.0-000{index}"
        duplicate.mkdir()
        (duplicate / "payload").write_text("must survive")
        atomic_json(duplicate / ".release.json", {"version": f"0.{index}.0", "prepared_at": index})
        os.utime(duplicate, (old, old))
    shutil.rmtree(harness.releases)
    harness.releases.symlink_to(outside, target_is_directory=True)

    report = harness.cleanup(dry_run=False)

    assert (outside / "0.9.0-0001/payload").read_text() == "must survive"
    assert report.removed == []
    assert report.errors


def test_artifact_identity_change_is_refused_before_deletion(tmp_path):
    harness, active = valid_harness(tmp_path)
    extra = harness.release("1.0.0", "0002", prepared_at=1)
    with pytest.raises(DeploymentError, match="identity changed|owned by uid"):
        harness._safe_remove(
            harness.releases,
            extra,
            identity=(0, 0),
        )
    assert extra.exists()


def test_safe_remove_cannot_reach_outside_the_managed_root(tmp_path):
    harness, _active = valid_harness(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret").write_text("keep")
    with pytest.raises(DeploymentError, match="outside the managed"):
        harness._safe_remove(harness.releases, outside)
    assert (outside / "secret").exists()


def write_mountinfo(path, points):
    lines = []
    for index, point in enumerate(points, start=1):
        lines.append(f"{100 + index} 1 0:{index} / {point} rw,relatime - ext4 /dev/fixture rw")
    path.write_text("\n".join(lines) + "\n")
    return path


def test_same_filesystem_nested_bind_mount_is_refused(tmp_path):
    harness, _active = valid_harness(tmp_path)
    target = harness.release("1.0.0", "0002", prepared_at=1)
    nested = target / "mounted"
    nested.mkdir()
    mountinfo = write_mountinfo(tmp_path / "mountinfo", [str(nested)])
    harness.fs = PrivilegedFilesystem(mountinfo=mountinfo)
    with pytest.raises(DeploymentError, match="mount"):
        harness._safe_remove(
            harness.releases,
            target,
            identity=harness.fs.identity_of(target),
        )
    assert target.exists() and nested.exists()


def test_file_mount_is_refused(tmp_path):
    harness, _active = valid_harness(tmp_path)
    target = harness.release("1.0.0", "0002", prepared_at=1)
    mounted_file = target / "payload.bin"
    mountinfo = write_mountinfo(tmp_path / "mountinfo", [str(mounted_file)])
    harness.fs = PrivilegedFilesystem(mountinfo=mountinfo)
    with pytest.raises(DeploymentError, match="mount"):
        harness._safe_remove(
            harness.releases,
            target,
            identity=harness.fs.identity_of(target),
        )
    assert target.exists()


def test_mount_table_inspection_failure_fails_closed(tmp_path):
    filesystem = PrivilegedFilesystem(mountinfo=tmp_path / "missing-mountinfo")
    with pytest.raises(DeploymentError, match="mount table"):
        filesystem.mount_points()


def test_managed_root_under_untrusted_writable_ancestor_is_refused(tmp_path):
    shared = tmp_path / "shared"
    shared.mkdir()
    shared.chmod(0o777)
    managed = shared / "releases"
    managed.mkdir()
    filesystem = PrivilegedFilesystem()
    with pytest.raises(DeploymentError, match="writable by another"):
        filesystem.validate_root(
            managed,
            owner=os.geteuid(),
            description="release directory",
        )


def test_staging_shaped_directories_without_markers_are_retained(tmp_path):
    harness, _active = valid_harness(tmp_path)
    unmarked = harness.releases / ".staging-release-0123456789abcdef"
    unmarked.mkdir()
    (unmarked / "data").write_text("unknown")
    marked = harness.releases / ".staging-release-fedcba9876543210"
    marked.mkdir()
    harness.fs.write_staging_marker(marked, "release")
    old = time.time() - 7200
    os.utime(unmarked, (old, old))
    os.utime(marked, (old, old))

    report = harness.cleanup(dry_run=False)

    assert unmarked.exists()
    assert not marked.exists()
    assert any("operation marker" in entry.reason for entry in report.skipped)


def test_legacy_backup_directory_is_relocated_to_root_owned_sibling(tmp_path):
    harness, active = valid_harness(tmp_path)
    legacy = harness.data / "backups"
    legacy.mkdir(mode=0o700)
    legacy.chmod(0o700)
    backup = harness.make_backup("20260101T000000-0001", payload=b"legacy")
    os.rename(backup, legacy / backup.name)
    harness.backups.rmdir()
    harness.set_state("2.0.0", active, backup=legacy / backup.name)

    harness.ensure_layout()

    relocated = harness.backups / backup.name
    assert relocated.is_dir()
    assert not legacy.exists()
    state = json.loads(harness.state.read_text())
    assert state["backup"] == str(relocated)
    assert (relocated / "data/sentinel").read_bytes() == b"legacy"


def test_compromised_legacy_backup_directory_is_not_trusted(tmp_path):
    harness, _active = valid_harness(tmp_path)
    legacy = harness.data / "backups"
    legacy.symlink_to(tmp_path / "elsewhere", target_is_directory=True)
    with pytest.raises(DeploymentError, match="not a real directory"):
        harness.ensure_layout()


# ---------------------------------------------------------------------------
# F07 - storage and inode estimation
# ---------------------------------------------------------------------------


def test_usage_counts_directories_and_allocation_overhead(tmp_path):
    root = tmp_path / "tree"
    root.mkdir()
    for index in range(4):
        directory = root / f"d{index}"
        directory.mkdir()
        (directory / "small").write_bytes(b"x")
    usage = scan_usage(root)
    # root + four subdirectories consume inodes.
    assert usage.inodes >= 9
    assert usage.directories >= 5
    allocated = sum(
        max(path.stat().st_size, getattr(path.stat(), "st_blocks", 0) * 512)
        for path in root.rglob("*")
        if path.is_file()
    )
    assert usage.bytes > allocated


def test_scan_failure_is_not_silently_ignored(tmp_path, monkeypatch):
    root = tmp_path / "tree"
    root.mkdir()
    (root / "file").write_text("x")
    original = os.scandir

    def failing(path):
        if Path(path) == root:
            raise OSError(errno.EACCES, os.strerror(errno.EACCES), str(path))
        return original(path)

    monkeypatch.setattr(os, "scandir", failing)
    with pytest.raises(DeploymentError, match="cannot be estimated safely"):
        scan_usage(root)


def test_requirements_are_attributed_to_their_real_filesystems(tmp_path):
    harness, active = valid_harness(tmp_path)
    assert harness.snapshot_requirement().path == harness.backups
    backup = harness.make_backup("20260101T000000-0001")
    assert harness.restore_requirement(backup).path == harness.data.parent
    assert harness.updater_requirement(0)[0].path == harness.updater_root
    requirements = harness._prepare_requirements(1024)
    assert {requirement.path for requirement in requirements} == {
        harness.releases,
        harness.temporary_root,
        harness.etc,
    }
    assert {item.path for item in harness._deployment_requirements()} >= {
        harness.base,
        harness.data,
        harness.data.parent,
        harness.backups,
        harness.etc,
    }


def test_peak_inode_estimate_is_persisted_and_reused(tmp_path):
    harness, active = valid_harness(tmp_path)
    metadata = json.loads((active / ".release.json").read_text())
    metadata["peak_inodes"] = 123456
    metadata["peak_bytes"] = 8 * 1024**2
    atomic_json(active / ".release.json", metadata)
    harness._release_size_cache = {}
    measured, inodes = harness._release_history_estimate()
    assert measured >= 8 * 1024**2
    assert inodes >= 123456


def test_inode_reporting_unsupported_is_handled_conservatively(tmp_path):
    harness, _active = valid_harness(tmp_path)
    from hnefatafl.deployment import FilesystemInfo

    harness.filesystem_override = lambda path: FilesystemInfo(
        1, Path(path), available_bytes=10 * 1024**3, available_inodes=0, inode_reporting=False
    )
    harness.check_storage(harness._deployment_requirements(), phase="test", service_state="running")
    harness.filesystem_override = lambda path: FilesystemInfo(
        1, Path(path), available_bytes=1024, available_inodes=0, inode_reporting=False
    )
    with pytest.raises(StorageError, match="inode reporting unsupported"):
        harness.check_storage(
            harness._deployment_requirements(), phase="test", service_state="running"
        )


# ---------------------------------------------------------------------------
# F08 - metadata validation and refused-deployment cleanup
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "journal",
    [
        {"phase": "exploded", "target": None, "previous": None, "backup": None},
        {"phase": "prepared", "target": "/etc/passwd"},
        {"phase": "prepared", "previous": "../../etc"},
        {"phase": "prepared", "backup": "/etc/shadow"},
        {"phase": "prepared", "migration_started": "yes"},
        {"phase": "prepared", "prior_state": {"phase": "committed", "target": "/tmp/x"}},
        {"phase": "prepared", "restore": {"phase": "published", "data": "/tmp/x"}},
    ],
)
def test_syntactically_valid_but_invalid_journal_blocks_deletion(tmp_path, journal):
    harness, active = valid_harness(tmp_path)
    old = harness.release("1.0.0", "0009", prepared_at=1)
    harness.activate(active)
    harness.set_state("2.0.0", active)
    atomic_json(harness.journal, journal)
    before = set(names(harness.releases))
    report = harness.cleanup(dry_run=False)
    assert set(names(harness.releases)) == before
    assert report.refused
    assert report.removed == []
    assert old.exists()


def test_invalid_state_target_outside_release_root_blocks_deletion(tmp_path):
    harness, active = valid_harness(tmp_path)
    harness.release("1.0.0", "0009", prepared_at=1)
    atomic_json(
        harness.state,
        {"version": "2.0.0", "target": str(tmp_path / "outside" / "2.0.0")},
    )
    report = harness.cleanup(dry_run=False)
    assert report.refused
    assert report.removed == []
    assert len(names(harness.releases)) >= 2


def test_legacy_state_and_journal_without_new_fields_remain_valid(tmp_path):
    harness, active = valid_harness(tmp_path)
    atomic_json(harness.state, {"version": "2.0.0"})
    atomic_json(
        harness.journal,
        {"phase": "committed", "previous": str(active), "target": str(active), "backup": None},
    )
    report = harness.cleanup(dry_run=False)
    assert not report.refused
    assert report.removed == []


def test_repeated_refused_deployments_do_not_accumulate_releases(tmp_path, monkeypatch):
    harness, active = valid_harness(tmp_path)
    previous = harness.release("1.9.0", "0002", prepared_at=10)
    harness.set_state("2.0.0", active, previous=previous)

    def refuse(_target):
        raise ValueError("synthetic production configuration mismatch")

    monkeypatch.setattr(harness, "validate_configuration", refuse)
    for index in range(5):
        target = harness.release(f"3.{index}.0", f"{index + 10:04x}", prepared_at=100 + index)
        with pytest.raises(ValueError):
            harness.deploy(target)
        assert len(names(harness.releases)) <= 3
    assert active.exists() and previous.exists()


def test_failed_configuration_leaves_active_release_and_data_intact(tmp_path, monkeypatch):
    harness, active = valid_harness(tmp_path)
    target = harness.release("3.0.0", "0003", prepared_at=100)

    def refuse(_target):
        raise ValueError("invalid application.env")

    monkeypatch.setattr(harness, "validate_configuration", refuse)
    with pytest.raises(ValueError):
        harness.deploy(target)
    assert harness.current.resolve() == active
    assert (harness.data / "models/weights").read_bytes() == b"retained-model"
    assert "stop" not in harness.actions


# ---------------------------------------------------------------------------
# F10 - retention reporting
# ---------------------------------------------------------------------------


def test_legacy_deployed_release_is_not_labelled_never_activated(tmp_path):
    harness = Harness(tmp_path)
    legacy = harness.release("0.42", "0001", prepared_at=10)
    harness.activate(legacy)
    atomic_json(harness.state, {"version": "0.42"})
    entry = next(item for item in harness._release_entries() if item["path"] == legacy)
    assert entry["kind"] == "complete"
    assert entry["deployed"] is True
    assert entry["legacy_deployed"] is True
    assert "never activated" not in entry["description"]
    assert "deployed" in entry["description"]


def test_report_distinguishes_apparent_and_actual_reclamation(tmp_path):
    harness, active = valid_harness(tmp_path)
    for index in range(4):
        harness.release(f"1.{index}.0", f"{index + 20:04x}", prepared_at=index)
    report = harness.cleanup(dry_run=False)
    assert report.reclaimable_bytes > 0
    assert report.reclaimed_bytes >= 0
    assert report.removed
    assert report.duration_seconds >= 0
    assert report.status_counts


def test_hardlink_aware_reclamation_is_reported(tmp_path):
    harness, active = valid_harness(tmp_path)
    target = harness.release("1.5.0", "0031", prepared_at=1)
    first = target / "shared-a"
    first.write_bytes(b"x" * 65536)
    os.link(first, target / "shared-b")
    for index in range(3):
        harness.release(f"1.{index + 1}.0", f"{index + 40:04x}", prepared_at=index + 2)
    original = harness._path_key(str(active))
    report = harness.cleanup(dry_run=True)
    entry = next(item for item in report.entries if item.path == target)
    assert entry.action == "remove"
    assert entry.hardlink_bytes > 0
    assert original
    assert target.exists()


def test_dry_run_does_not_truncate_the_deployment_lock(tmp_path):
    from hnefatafl.deployment import _open_deployment_lock

    lock = tmp_path / "update.lock"
    lock.write_text("previous-owner-evidence\n")
    lock.chmod(0o600)
    descriptor = _open_deployment_lock(lock)
    try:
        assert lock.read_text() == "previous-owner-evidence\n"
    finally:
        os.close(descriptor)


def test_concurrent_deployment_lock_is_exclusive(tmp_path):
    import fcntl

    from hnefatafl.deployment import _open_deployment_lock

    lock = tmp_path / "update.lock"
    first = _open_deployment_lock(lock)
    second = _open_deployment_lock(lock)
    try:
        fcntl.flock(first, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            fcntl.flock(second, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(second)
        os.close(first)


# ---------------------------------------------------------------------------
# F05 - trusted, serialized, recoverable updater delivery
# ---------------------------------------------------------------------------


def updater_setup(tmp_path):
    from test_deployment_storage import configure_updater, updater_archive

    harness = Harness(tmp_path)
    configure_updater(harness, tmp_path)
    archive_path = updater_archive(tmp_path / "source.tar.gz")
    staging = harness._stage_updater_payload(archive_path, "2.0.0", "sha256:" + "a" * 64)
    plan = harness._updater_publication_plan(staging)
    return harness, staging, plan


def test_updater_staging_modification_is_detected(tmp_path):
    harness, staging, _plan = updater_setup(tmp_path)
    target = staging / "hnefatafl/online_migrations.py"
    target.write_text(target.read_text() + "\n# modified after staging\n")
    with pytest.raises(DeploymentError, match="changed after staging"):
        harness._updater_publication_plan(staging)


@pytest.mark.parametrize("point", ["before-pointer", "after-pointer"])
def test_updater_generation_survives_process_death_at_atomic_switch(tmp_path, point):
    from deployment_fixtures import fault_process

    harness, _staging, plan = updater_setup(tmp_path)
    harness._publish_updater_plan(plan, tag="2.0.0")
    previous = harness._active_updater()["generation"]
    result = fault_process(
        """
import os, signal, sys
from pathlib import Path
from hnefatafl.deployment import Deployment
root, staging = map(Path, sys.argv[1:3])
point = sys.argv[3]
updater = Deployment({}, base=root/'opt', data=root/'data', etc=root/'etc', updater_root=root/'usr/lib/hnefatafl')
updater.updater_commands = (root/'usr/bin/hnefatafl-update', root/'usr/bin/hnefatafl-backup')
payload = updater._updater_publication_plan(staging)
replace = os.replace

def interrupted(source, destination):
    if Path(destination).name == 'active.json':
        if point == 'after-pointer':
            replace(source, destination)
        os.kill(os.getpid(), signal.SIGKILL)
    return replace(source, destination)

os.replace = interrupted
updater._publish_updater_plan(payload, tag='2.0.1')
""",
        tmp_path,
        _staging,
        point,
    )
    assert result.returncode == -9, result.stdout + result.stderr
    active = harness._active_updater()
    generations = harness.updater_root / "generations"
    manifest = harness._verify_generation(generations / active["generation"])
    assert manifest["version"] == ("2.0.0" if point == "before-pointer" else "2.0.1")
    assert harness._verify_generation(generations / previous)["version"] == "2.0.0"
    harness._publish_updater_plan(plan, tag="2.0.1")
    assert harness._active_updater()["previous"] == previous
    assert not (harness.updater_root / ".refresh.json").exists()


def test_legacy_replacement_killed_before_journal_update_and_failed_rollback_can_resume(
    tmp_path, monkeypatch
):
    from deployment_fixtures import fault_process
    import hnefatafl.deployment as module

    harness, _, _ = updater_setup(tmp_path)
    destination = harness.updater_module
    previous = destination.with_name("deployment.py.previous-abc123")
    temporary = destination.with_name("deployment.py.new-abc123")
    destination.write_bytes(b"# previous deployment\n")
    previous.write_bytes(destination.read_bytes())
    temporary.write_bytes(b"# candidate\n")
    journal = harness.updater_root / ".refresh.json"
    atomic_json(
        journal,
        {
            "schema": 1,
            "phase": "prepared",
            "transaction": "abc123",
            "entries": [
                {
                    "destination": str(destination),
                    "temporary": str(temporary),
                    "previous": str(previous),
                    "had_previous": True,
                    "replaced": False,
                }
            ],
        },
    )
    result = fault_process(
        "import os, signal, sys; os.replace(*sys.argv[1:]); os.kill(os.getpid(), signal.SIGKILL)",
        temporary,
        destination,
    )
    assert result.returncode == -9
    assert destination.read_bytes() == b"# candidate\n"
    original = module.atomic_bytes

    def failed(path, content, mode=0o600):
        if path == destination:
            raise OSError(errno.EIO, os.strerror(errno.EIO))
        original(path, content, mode)

    with monkeypatch.context() as patch:
        patch.setattr(module, "atomic_bytes", failed)
        with pytest.raises(OSError, match="Input/output error"):
            harness.recover_updater_refresh()
    assert journal.exists() and previous.exists()
    harness.recover_updater_refresh()
    assert destination.read_bytes() == b"# previous deployment\n"
    assert not journal.exists() and not previous.exists()


# ---------------------------------------------------------------------------
# F09 - cleanup I/O is bounded and cached within an operation
# ---------------------------------------------------------------------------


def test_large_backup_cleanup_hashes_once_per_verified_snapshot(tmp_path):
    harness, _active = valid_harness(tmp_path)
    payload = b"x" * (4 * 1024**2)
    for index in range(3):
        backup = harness.make_backup(f"2026010{index + 1}T000000-{index + 1:04x}", payload=payload)
        for small in range(200):
            (backup / "data" / f"small-{small}").write_bytes(b"y" * 256)
        manifest = {
            str(path.relative_to(backup)): checksum(path)
            for path in backup.rglob("*")
            if path.is_file() and path.name != "manifest.json"
        }
        atomic_json(backup / "manifest.json", manifest)
    first = harness.cleanup(dry_run=True)
    second = harness.cleanup(dry_run=True)
    assert first.verified_backups == 3
    assert second.verified_backups == 0
    assert second.manifest_bytes_hashed == 0
    assert first.manifest_bytes_hashed >= 3 * len(payload)
    assert first.duration_seconds >= 0


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "fifo"])
def test_write_gate_rejects_untrusted_entries_without_changing_files(tmp_path, kind):
    harness, _ = valid_harness(tmp_path)
    gate = harness.data / ".deployment-gate"
    separate = tmp_path / "separate"
    separate.write_bytes(b"intact")
    separate.chmod(0o600)
    original = separate.stat()
    if kind == "symlink":
        gate.symlink_to(separate)
    elif kind == "hardlink":
        os.link(separate, gate)
    else:
        os.mkfifo(gate, 0o600)
    with pytest.raises(DeploymentError, match="Unsafe or replaced deployment gate"):
        with harness.write_gate():
            pytest.fail("unsafe gate entered the protected section")
    assert separate.read_bytes() == b"intact"
    assert separate.stat().st_mode == original.st_mode


def test_write_gate_detects_entry_replacement_after_open(tmp_path, monkeypatch):
    import fcntl

    harness, _ = valid_harness(tmp_path)
    gate = harness.data / ".deployment-gate"
    gate.touch(mode=0o600)
    replacement = tmp_path / "replacement"
    replacement.write_bytes(b"intact")
    replacement.chmod(0o600)
    flock = fcntl.flock

    def replace_while_locking(descriptor, operation):
        flock(descriptor, operation)
        os.replace(replacement, gate)

    monkeypatch.setattr(fcntl, "flock", replace_while_locking)
    with pytest.raises(DeploymentError, match="replaced deployment gate"):
        with harness.write_gate():
            pytest.fail("the name no longer identifies the lock inode")
    assert gate.read_bytes() == b"intact"
    assert gate.stat().st_mode & 0o777 == 0o600


def test_write_gate_rejects_mounted_entry(tmp_path):
    harness, _ = valid_harness(tmp_path)
    gate = harness.data / ".deployment-gate"
    gate.touch()
    harness.fs = PrivilegedFilesystem(
        mountinfo=write_mountinfo(tmp_path / "mountinfo", [str(gate)])
    )
    with pytest.raises(DeploymentError, match="deployment gate is a mount point"):
        with harness.write_gate():
            pytest.fail("a mounted lock must be refused")


@pytest.mark.parametrize(
    "point", ["before-intent", "after-intent", "after-rename", "after-state", "after-transaction"]
)
def test_legacy_backup_migration_resumes_after_abrupt_transition(tmp_path, point):
    from deployment_fixtures import fault_process

    harness, active = valid_harness(tmp_path)
    previous = harness.release("1.0.0", "0002")
    legacy = harness.legacy_backups
    legacy.mkdir(mode=0o700)
    backup = harness.make_backup("20260101T000000-0001", payload=b"original backup")
    backup.rename(legacy / backup.name)
    inode = (legacy / backup.name).stat().st_ino
    harness.set_state("2.0.0", active, previous=previous, backup=legacy / backup.name)
    atomic_json(
        harness.journal,
        {
            "phase": "committed",
            "target": str(active),
            "previous": str(previous),
            "backup": str(legacy / backup.name),
        },
    )
    script = """
import os, signal, sys
from pathlib import Path
import hnefatafl.deployment as module
root = Path(sys.argv[1]); point = sys.argv[2]
updater = module.Deployment({}, base=root/'opt', data=root/'data', etc=root/'etc')
record = module.atomic_json
rename = os.rename

def stop():
    os.kill(os.getpid(), signal.SIGKILL)

def write(path, value):
    if path == updater.migration_journal and point == 'before-intent':
        stop()
    record(path, value)
    if ((path == updater.migration_journal and point == 'after-intent')
        or (path == updater.state and point == 'after-state')
        or (path == updater.journal and point == 'after-transaction')):
        stop()

def move(old, new, **descriptors):
    rename(old, new, **descriptors)
    if point == 'after-rename':
        stop()

module.atomic_json = write
os.rename = move
updater.ensure_layout()
"""
    interrupted = fault_process(script, tmp_path, point)
    assert interrupted.returncode == -9, interrupted.stdout + interrupted.stderr
    resumed = fault_process(script, tmp_path, "resume")
    assert resumed.returncode == 0, resumed.stdout + resumed.stderr
    assert not legacy.exists() and not harness.migration_journal.exists()
    assert backup.stat().st_ino == inode
    assert (backup / "data/sentinel").read_bytes() == b"original backup"
    assert json.loads(harness.state.read_text())["backup"] == str(backup)
    assert json.loads(harness.journal.read_text())["backup"] == str(backup)


def test_legacy_migration_keeps_conflicting_and_unverified_artifacts(tmp_path):
    harness, active = valid_harness(tmp_path)
    backup = harness.make_backup("20260101T000000-0001", payload=b"legacy")
    legacy = harness.legacy_backups
    legacy.mkdir(mode=0o700)
    backup.rename(legacy / backup.name)
    harness.make_backup(backup.name, payload=b"distinct destination")
    broken = harness.make_backup("20260102T000000-0002", invalid=True)
    broken.rename(legacy / broken.name)
    harness.set_state("2.0.0", active, backup=legacy / backup.name)
    harness.ensure_layout()
    recorded = Path(json.loads(harness.state.read_text())["backup"])
    assert recorded != backup and recorded.parent == harness.backups
    assert (recorded / "data/sentinel").read_bytes() == b"legacy"
    assert (backup / "data/sentinel").read_bytes() == b"distinct destination"
    assert (legacy / broken.name).is_dir()


@pytest.mark.parametrize(
    "point", ["complete", "copy-verified", "destination-renamed", "source-removed"]
)
def test_legacy_backup_migration_crosses_real_filesystem_boundary(tmp_path, point):
    import tempfile

    from deployment_fixtures import fault_process

    harness, active = valid_harness(
        tmp_path, {"storage": {"headroom_mib": 0, "minimum_free_inodes": 0}}
    )
    devshm = Path("/dev/shm")
    if not devshm.is_dir() or devshm.stat().st_dev == tmp_path.stat().st_dev:
        pytest.skip("a separate disposable filesystem is unavailable")
    try:
        workspace = tempfile.TemporaryDirectory(dir=devshm, prefix="hnefatafl-migration-")
    except OSError as error:
        pytest.skip(f"a disposable filesystem is unavailable: {error.strerror}")
    with workspace as directory:
        backup = harness.make_backup("20260101T000000-0001", payload=b"copy across filesystems")
        legacy = harness.legacy_backups
        legacy.mkdir(mode=0o700)
        backup.rename(legacy / backup.name)
        harness.backups = Path(directory) / "new-backups"
        harness.set_state("2.0.0", active, backup=legacy / backup.name)
        script = """
import os, signal, sys
from pathlib import Path
from hnefatafl.deployment import Deployment
root, backups = map(Path, sys.argv[1:3])
point = sys.argv[3]
config = {'storage': {'headroom_mib': 0, 'minimum_free_inodes': 0}}
updater = Deployment(config, base=root/'opt', data=root/'data', etc=root/'etc', backups=backups)
copy, rename, rmdir = updater._copy_verified_snapshot, os.rename, os.rmdir

def stop(): os.kill(os.getpid(), signal.SIGKILL)

def copying(*args, **kwargs):
    copy(*args, **kwargs)
    if point == 'copy-verified': stop()

def publishing(old, new, **kwargs):
    rename(old, new, **kwargs)
    if point == 'destination-renamed' and str(old).startswith('.staging-backup-'): stop()

def removing(path, **kwargs):
    rmdir(path, **kwargs)
    if point == 'source-removed' and path == 'backups': stop()

updater._copy_verified_snapshot = copying
os.rename, os.rmdir = publishing, removing
updater.ensure_layout()
"""
        if point != "complete":
            interrupted = fault_process(script, tmp_path, harness.backups, point)
            assert interrupted.returncode == -9, interrupted.stdout + interrupted.stderr
        resumed = fault_process(script, tmp_path, harness.backups, "complete")
        assert resumed.returncode == 0, resumed.stdout + resumed.stderr
        destination = harness.backups / backup.name
        assert not legacy.exists() and not harness.migration_journal.exists()
        assert destination.stat().st_dev != tmp_path.stat().st_dev
        assert (destination / "data/sentinel").read_bytes() == b"copy across filesystems"
        assert json.loads(harness.state.read_text())["backup"] == str(destination)


def test_empty_installed_state_cannot_authorize_destructive_cleanup(tmp_path):
    harness, active = valid_harness(tmp_path)
    for minor in range(4):
        harness.release(f"1.{minor}.0", f"{minor:04x}")
    atomic_json(harness.state, {})
    report = harness.cleanup(dry_run=False)
    assert report.refused and not report.removed
    assert "no valid version" in report.exception_reason
    assert active.is_dir()


@pytest.mark.parametrize("phase", ["prepared", "migrating", "activated", "committing"])
def test_current_transaction_requires_operation_and_phase_fields(tmp_path, phase):
    harness, active = valid_harness(tmp_path)
    valid, reason = harness.validate_transaction_metadata(
        {
            "abi": 3,
            "operation": "update",
            "transaction": "1" * 32,
            "phase": phase,
            "target": str(active),
        }
    )
    assert not valid and reason


def test_reviewed_installer_repairs_damaged_generation_without_loading_it(tmp_path):
    from hnefatafl.deployment import install_reviewed_updater

    harness, _staging, plan = updater_setup(tmp_path)
    harness._publish_updater_plan(plan, tag="2.0.0")
    harness._publish_updater_plan(plan, tag="2.0.1")
    active = harness._active_updater()
    bad = harness.updater_root / "generations" / active["generation"] / "deployment.py"
    bad.chmod(0o600)
    bad.write_text("broken generation\n")
    with pytest.raises(DeploymentError, match="did not verify"):
        harness._active_updater()
    install_reviewed_updater(
        REPO,
        harness.etc,
        harness.updater_root,
        harness.updater_commands[0].parent,
        tmp_path / "lock",
    )
    repaired = harness._active_updater()
    assert repaired["generation"] != active["generation"]
    assert repaired["previous"] == active["previous"]
    assert bad.read_text() == "broken generation\n"


@pytest.mark.parametrize(
    "point",
    ["staging-created", "marker-removed", "manifest-being-written", "snapshot-verified"],
)
def test_killed_snapshot_staging_is_recognized_without_an_inner_marker(tmp_path, point):
    from deployment_fixtures import fault_process

    harness, active = valid_harness(tmp_path)
    backup = harness.make_backup("20260101T000000-0001")
    harness.set_state("2.0.0", active, backup=backup)
    unrelated = harness.backups / ".staging-backup-1111111111111111"
    unrelated.mkdir()
    (unrelated / "retained").write_text("unrelated")
    source = """
import os, signal, sys
from pathlib import Path
from hnefatafl import deployment

root, point = Path(sys.argv[1]), sys.argv[2]
updater = deployment.Deployment({}, base=root/'opt', data=root/'data', etc=root/'etc')
mkdir = os.mkdir
remove = updater._remove_staging_marker
atomic = deployment.atomic_json
verify = updater.verify_snapshot

def kill(): os.kill(os.getpid(), signal.SIGKILL)

def creating(path, *args, **kwargs):
    mkdir(path, *args, **kwargs)
    if point == 'staging-created' and kwargs.get('dir_fd') is not None and str(path).startswith('.staging-backup-'):
        kill()

def removing(path):
    remove(path)
    if point == 'marker-removed': kill()

def writing(path, value):
    if point == 'manifest-being-written' and path.name == 'manifest.json':
        path.write_text('{"partial":')
        kill()
    atomic(path, value)

def verifying(path, *args, **kwargs):
    verify(path, *args, **kwargs)
    if point == 'snapshot-verified': kill()

os.mkdir = creating
updater._remove_staging_marker = removing
updater.verify_snapshot = verifying
deployment.atomic_json = writing
updater.snapshot()
"""
    killed = fault_process(source, tmp_path, point)
    assert killed.returncode == -9, killed.stdout + killed.stderr
    (staging,) = set(harness.backups.glob(".staging-backup-*")) - {unrelated}
    assert harness._staging_intent(staging).is_file()
    assert not (staging / ".staging.json").exists()
    from hnefatafl.deployment import Deployment

    fresh = Deployment({}, base=harness.base, data=harness.data, etc=harness.etc)
    preview = fresh.cleanup(dry_run=True)
    assert any(entry.path == staging and entry.action == "remove" for entry in preview.entries)
    assert staging.is_dir()
    cleaned = fresh.cleanup()
    assert any(entry.path == staging for entry in cleaned.removed)
    assert not staging.exists()
    assert not harness._staging_intent(staging).exists()
    assert backup.is_dir()
    assert (unrelated / "retained").read_text() == "unrelated"


@pytest.mark.parametrize("point", ["download", "updater-payload", "prepare-failure"])
def test_killed_preparation_removes_abandoned_download_and_pending_payload(tmp_path, point):
    from deployment_fixtures import fault_process
    from hnefatafl.deployment import Deployment
    import tarfile

    from test_deployment_storage import archive, updater_archive

    harness, active = valid_harness(tmp_path)
    source = updater_archive(tmp_path / "candidate.tar.gz")
    if point == "prepare-failure":
        with tarfile.open(source) as bundle:
            entries = {member.name: bundle.extractfile(member).read() for member in bundle}
        for name in (
            "uv.lock",
            "frontend/package-lock.json",
            "hnefatafl/auth.py",
            "tests/test_security.py",
            "deployment/hnefatafl.service",
        ):
            entries[name] = b"placeholder"
        archive(source, entries)
    (tmp_path / "usr").mkdir()
    script = """
import os, shutil, signal, sys
from pathlib import Path
from types import SimpleNamespace
from hnefatafl.deployment import Deployment

root, source = map(Path, sys.argv[1:3])
point = sys.argv[3]
updater = Deployment(
    {"storage": {"headroom_mib": 0, "cache_headroom_mib": 0, "release_estimate_mib": 128}},
    base=root/'opt', data=root/'data', etc=root/'etc', updater_root=root/'usr/lib',
)
updater.manage_updater = True
updater.updater_commands = (root/'usr/bin/hnefatafl-update', root/'usr/bin/hnefatafl-backup')
stage = updater._stage_updater_payload

def kill(): os.kill(os.getpid(), signal.SIGKILL)

def download(release, destination):
    if point == 'download':
        with destination.open('wb') as output:
            output.write(b'partial release archive')
            output.flush()
            os.fsync(output.fileno())
        kill()
    shutil.copyfile(source, destination)
    return 'sha256:' + 'a'*64

def staging(*args, **kwargs):
    value = stage(*args, **kwargs)
    if point == 'updater-payload': kill()
    return value

updater._stage_updater_payload = staging
if point == 'prepare-failure':
    discard = updater._discard_staging
    def fail_build(*args, **kwargs):
        (root/'build-failed').touch()
        raise OSError("failed build")
    def interrupted_cleanup(path, **kwargs):
        if path.parent == updater.releases: kill()
        return discard(path, **kwargs)
    updater.run = fail_build
    updater._discard_staging = interrupted_cleanup
updater.prepare(SimpleNamespace(download=download), {'tag_name': '0.43'})
"""
    killed = fault_process(script, tmp_path, source, point)
    assert killed.returncode == -9, killed.stdout + killed.stderr
    (workspace,) = harness.temporary_root.glob(".staging-prepare-*")
    if point == "prepare-failure":
        assert (tmp_path / "build-failed").exists()
        assert not (workspace / "release.tar.gz").is_file()
    else:
        assert (workspace / "release.tar.gz").is_file()
    utility_root = tmp_path / "usr/lib"
    assert bool(list(utility_root.glob(".pending-*"))) == (point != "download")
    unrelated = harness.temporary_root / ".staging-prepare-0000000000000000"
    unrelated.mkdir()
    fresh = Deployment(
        {}, base=harness.base, data=harness.data, etc=harness.etc, updater_root=utility_root
    )
    fresh._remove_orphan_staging()
    assert list(harness.releases.iterdir()) == [active]
    assert list(harness.temporary_root.iterdir()) == [unrelated]
    assert not list(utility_root.glob(".pending-*"))


@pytest.mark.parametrize(
    ("abi", "protocol", "failure"),
    [(3, 3, "Unsupported launcher protocol"), (5, 1, "Unsupported updater ABI")],
)
def test_unsupported_updater_transitions_leave_installed_files_unchanged(
    tmp_path, abi, protocol, failure
):
    harness, _staging, original = updater_setup(tmp_path)
    harness._publish_updater_plan(original, tag="2.0.0")
    installed = [
        harness.updater_module,
        *harness.updater_commands,
        harness.updater_root / "active.json",
    ]
    previous = {path: path.read_bytes() for path in installed}
    payload = {
        **original,
        "scripts/linux/launcher.py": original["scripts/linux/launcher.py"].replace(
            b"LAUNCHER_PROTOCOL = 1", f"LAUNCHER_PROTOCOL = {protocol}".encode()
        ),
        "hnefatafl/deployment.py": original["hnefatafl/deployment.py"].replace(
            b"UPDATER_ABI = 3", f"UPDATER_ABI = {abi}".encode()
        ),
    }
    with pytest.raises(DeploymentError, match=failure):
        harness._publish_updater_plan(payload, tag="2.0.1")
    assert previous == {path: path.read_bytes() for path in installed}


def test_abi_successor_accepts_predecessor_journal_in_startup_guard(tmp_path):
    import runpy

    harness, _staging, plan = updater_setup(tmp_path)
    target = harness.release("2.0.0")
    harness.activate(target)
    harness.set_state("2.0.0", target)
    harness._publish_updater_plan(plan, tag="2.0.0")
    prior = harness._active_updater()["generation"]
    journal = {
        "abi": 3,
        "phase": "prepared",
        "operation": "manual-backup",
        "transaction": "1" * 32,
        "snapshot_name": "20261126T050000-0001",
        "snapshot_id": "2" * 32,
        "service_was_running": True,
    }
    harness.record(journal)
    advanced = {
        **plan,
        "hnefatafl/deployment.py": plan["hnefatafl/deployment.py"].replace(
            b"UPDATER_ABI = 3", b"UPDATER_ABI = 4"
        ),
    }
    harness._publish_updater_plan(advanced, tag="2.0.1")
    active = harness._active_updater()
    assert active["abi"] == 4 and active["previous"] == prior
    candidate = harness.updater_root / "generations" / active["generation"] / "deployment.py"
    current = runpy.run_path(str(candidate))["Deployment"](
        harness.config,
        base=harness.base,
        data=harness.data,
        etc=harness.etc,
        updater_root=harness.updater_root,
    )
    current._authorize_service_start()
    assert json.loads(harness.journal.read_text()) == journal


def test_interrupted_compatible_launcher_replacement_can_resume(tmp_path):
    from deployment_fixtures import fault_process

    harness, staging, plan = updater_setup(tmp_path)
    harness._publish_updater_plan(plan, tag="2.0.0")
    previous = harness._active_updater()
    source = """
import os, signal, sys
from pathlib import Path
from hnefatafl.deployment import Deployment
root, staging = map(Path, sys.argv[1:3])
updater = Deployment({}, base=root/'opt', data=root/'data', etc=root/'etc', updater_root=root/'usr/lib/hnefatafl')
updater.updater_commands = (root/'usr/bin/hnefatafl-update', root/'usr/bin/hnefatafl-backup')
plan = updater._updater_publication_plan(staging)
plan['scripts/linux/launcher.py'] = plan['scripts/linux/launcher.py'].replace(
    b'LAUNCHER_PROTOCOL = 1', b'LAUNCHER_PROTOCOL = 2',
)
replace = os.replace

def replacing(old, new):
    replace(old, new)
    if new == updater.updater_module:
        os.kill(os.getpid(), signal.SIGKILL)

os.replace = replacing
updater._publish_updater_plan(plan, tag='2.0.1')
"""
    killed = fault_process(source, tmp_path, staging)
    assert killed.returncode == -9, killed.stdout + killed.stderr
    plan["scripts/linux/launcher.py"] = plan["scripts/linux/launcher.py"].replace(
        b"LAUNCHER_PROTOCOL = 1", b"LAUNCHER_PROTOCOL = 2"
    )
    assert harness.updater_module.read_bytes() == plan["scripts/linux/launcher.py"]
    assert harness._active_updater() == previous
    harness._publish_updater_plan(plan, tag="2.0.1")
    active = harness._active_updater()
    assert active["previous"] == previous["generation"]
    generation = harness.updater_root / "generations" / active["generation"]
    assert harness._verify_generation(generation)["launcher_protocol"] == 2


def test_reviewed_installer_repairs_a_damaged_stable_launcher(tmp_path):
    from hnefatafl.deployment import install_reviewed_updater

    harness, _staging, plan = updater_setup(tmp_path)
    harness._publish_updater_plan(plan, tag="2.0.0")
    previous = harness._active_updater()
    harness.updater_module.chmod(0o600)
    harness.updater_module.write_text("not a valid Python launcher!")
    with pytest.raises(DeploymentError, match="reviewed installer"):
        harness._publish_updater_plan(plan, tag="2.0.1")
    assert harness._active_updater() == previous
    install_reviewed_updater(
        REPO,
        harness.etc,
        harness.updater_root,
        harness.updater_commands[0].parent,
        tmp_path / "lock",
    )
    assert harness.updater_module.read_bytes() == plan["scripts/linux/launcher.py"]
    assert harness._active_updater()["previous"] == previous["generation"]


def test_legacy_pending_updater_is_removed_only_with_a_verified_payload(tmp_path):
    harness, staging, _plan = updater_setup(tmp_path)
    harness._staging_intent(staging).unlink()
    unknown = harness.updater_root / ".pending-1234567812345678"
    unknown.mkdir(mode=0o700)
    (unknown / "keep").write_bytes(b"untrusted")
    preview = harness.cleanup(dry_run=True)
    assert any(entry.path == staging and entry.action == "remove" for entry in preview.entries)
    assert any(entry.path == unknown and entry.action == "retain" for entry in preview.entries)
    assert staging.exists()
    harness.cleanup()
    assert not staging.exists()
    assert (unknown / "keep").read_bytes() == b"untrusted"


@pytest.mark.parametrize("fault", ["none", "interrupted", "copy", "verify", "no-capacity"])
def test_cross_filesystem_migration_only_copies_retained_history(tmp_path, monkeypatch, fault):
    import tempfile

    from deployment_fixtures import fault_process
    from hnefatafl.deployment import FilesystemInfo

    harness, active = valid_harness(
        tmp_path, {"storage": {"headroom_mib": 0, "minimum_free_inodes": 0}}
    )
    shared = Path("/dev/shm")
    if not shared.is_dir() or shared.stat().st_dev == tmp_path.stat().st_dev:
        pytest.skip("requires a separate disposable backup filesystem")
    with tempfile.TemporaryDirectory(dir=shared, prefix="hnefatafl-retention-") as directory:
        legacy = harness.legacy_backups
        legacy.mkdir(mode=0o700)
        payload = b"L" * (256 * 1024)
        sources = []
        for index in range(1, 8):
            snapshot = harness.make_backup(
                f"2026010{index}T000000-{index:04x}", old=200 - index, payload=payload
            )
            source = legacy / snapshot.name
            snapshot.rename(source)
            sources.append(source)
        protected = sources[0]
        invalid = legacy / "20260108T000000-dead"
        (invalid / "data").mkdir(parents=True)
        (invalid / "config").mkdir()
        invalid.chmod(0o700)
        atomic_json(invalid / "manifest.json", {"data/missing": "0" * 64})
        harness.backups = Path(directory) / "backups"
        collision = harness.make_backup(protected.name, old=0, payload=b"C" * len(payload))
        expired = harness.make_backup("20250101T000000-ffff", old=400, payload=b"X" * len(payload))
        harness.set_state("2.0.0", active, backup=protected)
        inode = protected.stat().st_ino
        capacity = 945 * 1024

        def capacity_at(path):
            usage = scan_usage(harness.backups)
            free = max(0, capacity - usage.allocated_bytes)
            return FilesystemInfo(harness.backups.stat().st_dev, Path(path), free, 2048)

        harness.filesystem_override = capacity_at
        plan = harness.cleanup(dry_run=True)
        copies = {entry.path for entry in plan.entries if entry.action == "copy"}
        pruned = {entry.path for entry in plan.entries if entry.action == "remove"}
        assert copies == {protected, sources[-1]}
        assert pruned == {*sources[1:-1], expired}
        assert any(entry.path == invalid and entry.status == "invalid" for entry in plan.retained)
        assert all(source.exists() for source in sources)
        assert expired.exists()
        assert not harness.migration_journal.exists()
        assert len(payload) * 3 < capacity < len(payload) * 7

        if fault == "no-capacity":
            capacity = 100 * 1024
            with pytest.raises(StorageError, match="retained legacy snapshot copies"):
                harness.ensure_layout()
            assert all(source.exists() for source in sources)
            assert expired.exists()
            assert not harness.migration_journal.exists()
            return
        if fault == "interrupted":
            script = """
import os, signal, sys
from pathlib import Path
from hnefatafl import deployment
root, backups = map(Path, sys.argv[1:3])
capacity = int(sys.argv[3])
updater = deployment.Deployment(
    {'storage': {'headroom_mib': 0, 'minimum_free_inodes': 0}},
    base=root/'opt', data=root/'data', etc=root/'etc', backups=backups,
)
updater.filesystem_info = lambda path: deployment.FilesystemInfo(
    backups.stat().st_dev, path, max(0, capacity-deployment.scan_usage(backups).allocated_bytes), 2048,
)
record = deployment.atomic_json
def interrupted(path, value):
    record(path, value)
    if path == updater.migration_journal and value['entries'][0]['phase'] == 'copied':
        os.kill(os.getpid(), signal.SIGKILL)
deployment.atomic_json = interrupted
updater.ensure_layout()
"""
            result = fault_process(script, tmp_path, harness.backups, capacity)
            assert result.returncode == -9, result.stdout + result.stderr
        elif fault in {"copy", "verify"}:
            original_copy = harness._copy_verified_snapshot
            original_verify = harness.verify_snapshot

            def copy(source, staging, **kwargs):
                if fault == "copy" and source.name == protected.name:
                    raise OSError(errno.EIO, "fixture copy failure")
                return original_copy(source, staging, **kwargs)

            def verify(path, **kwargs):
                if fault == "verify" and path.name.startswith(".staging-backup-"):
                    raise DeploymentError("fixture destination verification failure")
                return original_verify(path, **kwargs)

            with monkeypatch.context() as isolated:
                isolated.setattr(harness, "_copy_verified_snapshot", copy)
                isolated.setattr(harness, "verify_snapshot", verify)
                with pytest.raises((OSError, DeploymentError), match="fixture"):
                    harness.ensure_layout()
        if fault != "none":
            pending = json.loads(harness.migration_journal.read_text())
            assert pending["schema"] == 2
            assert len([entry for entry in pending["entries"] if entry["phase"] == "prune"]) == 5
            assert protected.stat().st_ino == inode
            assert json.loads(harness.state.read_text())["backup"] == str(protected)
            assert all(source.exists() for source in sources)
            assert collision.exists()

        harness.ensure_layout()
        assert not harness.migration_journal.exists()
        assert not any(source.exists() for source in sources)
        assert not expired.exists()
        assert invalid.is_dir()
        state = json.loads(harness.state.read_text())
        mapped = Path(state["backup"])
        assert mapped.parent == harness.backups and mapped != collision
        assert (mapped / "data/sentinel").read_bytes() == payload
        assert (collision / "data/sentinel").read_bytes() == b"C" * len(payload)
        assert {path.name for path in harness.backups.iterdir()} == {
            mapped.name,
            collision.name,
            sources[-1].name,
        }
        assert harness.cleanup().removed == []


@pytest.mark.parametrize("invocation", ["cleanup", "orphan"])
def test_preparation_cleanup_waits_for_all_units_in_each_operation(
    tmp_path, monkeypatch, invocation
):
    import subprocess

    from hnefatafl import deployment
    from test_deployment_storage import configure_updater

    harness, _ = valid_harness(tmp_path)
    configure_updater(harness, tmp_path)
    harness.updater_root.mkdir(parents=True)
    monkeypatch.setattr(
        harness, "_data_account", lambda: (harness.privileged_uid + 1, os.getegid())
    )
    artifacts = {}
    running = set()
    unit_names = {}
    for digit in "1234":
        token = digit * 16
        if digit == "4":
            workspace = harness.temporary_root / (".staging-prepare-" + token)
            harness._begin_staging(workspace, "prepare", 0o711)
            association = {}
        else:
            workspace = harness._temporary_workspace("prepare", preparation_id=token)
            association = {"preparation_id": token}
        release = harness.releases / ("1.0.0-" + token[:8])
        pending = harness.updater_root / (".pending-" + token)
        harness._begin_staging(release, "release", **association)
        harness._begin_staging(pending, "pending", **association)
        unit = f"hnefatafl-prepare-{token}-abcd1234.service"
        unit_names[digit] = unit
        running.add(unit)
        if digit != "4":
            atomic_json(workspace / ".units.json", {"units": [unit]})
        (workspace / ".units-allowed").touch()
        artifacts[digit] = (workspace, release, pending)

    unrelated = "hnefatafl-prepare-" + "3" * 16 + "-ffffffff.service"
    running.add(unrelated)
    failures = {"1": "stop", "2": "inspect", "4": "still-active"}
    calls = []

    def list_units(args, **kwargs):
        assert args[:2] == ["systemctl", "list-units"]
        calls.append(("list", args[-1]))
        if failures.get("2") == "inspect" and "2" * 16 in args[-1]:
            raise subprocess.CalledProcessError(1, args)
        lines = [f"{name} loaded active running Fixture" for name in sorted(running)]
        return subprocess.CompletedProcess(args, 0, stdout="\n".join(lines))

    def stop(args, **kwargs):
        assert args[:2] == ["systemctl", "stop"]
        calls.append(("stop", *args[2:]))
        assert unrelated not in args
        if failures.get("1") == "stop" and unit_names["1"] in args:
            raise subprocess.CalledProcessError(1, args)
        for name in args[2:]:
            if not (failures.get("4") == "still-active" and name == unit_names["4"]):
                running.discard(name)

    monkeypatch.setattr(deployment.subprocess, "run", list_units)
    monkeypatch.setattr(harness, "run", stop)
    preview = harness.cleanup(dry_run=True)
    assert not calls and not preview.errors
    assert all((paths[0] / ".units-allowed").exists() for paths in artifacts.values())

    if invocation == "cleanup":
        result = harness.cleanup()
        assert not result.cleanup_complete
        assert any("Cannot confirm termination" in item for item in result.errors)
    else:
        harness._remove_orphan_staging()
    assert all(path.exists() for digit in "124" for path in artifacts[digit])
    assert not any(path.exists() for path in artifacts["3"])
    assert unrelated in running
    assert all(not (artifacts[digit][0] / ".units-allowed").exists() for digit in "124")

    failures.clear()
    running.discard(unit_names["1"])
    harness._remove_orphan_staging()
    assert not any(path.exists() for paths in artifacts.values() for path in paths)
    assert running == {unrelated}
    harness._remove_orphan_staging()
    assert running == {unrelated}


def test_changed_candidate_wrappers_are_refused_even_by_reviewed_repair(tmp_path):
    harness, _staging, plan = updater_setup(tmp_path)
    harness._publish_updater_plan(plan, tag="0.43")
    changed = {**plan, "scripts/linux/backup.sh": b"#!/bin/bash\nexit 0\n"}
    before = (harness.updater_root / "active.json").read_bytes()
    for repair in (False, True):
        with pytest.raises(DeploymentError, match="Unsupported maintenance wrapper"):
            harness._publish_updater_plan(changed, tag="0.43", repair=repair)
    assert (harness.updater_root / "active.json").read_bytes() == before
    assert harness.updater_commands[1].read_bytes() == plan["scripts/linux/backup.sh"]


def test_reviewed_wrapper_repair_is_resumable_after_first_replacement(tmp_path):
    from deployment_fixtures import fault_process
    from hnefatafl.deployment import install_reviewed_updater

    harness, _staging, plan = updater_setup(tmp_path)
    harness._publish_updater_plan(plan, tag="0.43")
    update, backup = harness.updater_commands
    update.write_bytes(plan["scripts/linux/update.sh"].replace(b" -I -B ", b" "))
    backup.write_text("damaged wrapper\n")
    backup.chmod(0o644)
    before = harness._active_updater()
    with pytest.raises(DeploymentError, match="reviewed installer"):
        harness._publish_updater_plan(plan, tag="0.43")
    assert harness._active_updater() == before
    result = fault_process(
        """
import os, signal, sys
from pathlib import Path
from hnefatafl.deployment import install_reviewed_updater
source, root, utilities, commands = map(Path, sys.argv[1:])
replace = os.replace
def replacing(old, new):
    replace(old, new)
    if Path(new) == commands / 'hnefatafl-update':
        os.kill(os.getpid(), signal.SIGKILL)
os.replace = replacing
install_reviewed_updater(source, root/'etc', utilities, commands, root/'lock')
""",
        REPO,
        tmp_path,
        harness.updater_root,
        update.parent,
    )
    assert result.returncode == -9, result.stdout + result.stderr
    assert update.read_bytes() == plan["scripts/linux/update.sh"]
    assert update.stat().st_mode & 0o111
    assert backup.read_text() == "damaged wrapper\n"
    assert harness._active_updater()["previous"] == before["generation"]
    install_reviewed_updater(
        REPO, harness.etc, harness.updater_root, update.parent, tmp_path / "lock"
    )
    assert backup.read_bytes() == plan["scripts/linux/backup.sh"]
    assert backup.stat().st_mode & 0o111
    assert update.read_bytes() == plan["scripts/linux/update.sh"]
