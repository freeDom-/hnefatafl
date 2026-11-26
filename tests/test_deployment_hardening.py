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
    assert harness.updater_requirement(0)[0].path == harness.updater_root.parent
    requirements = harness._prepare_requirements(1024)
    assert {requirement.path for requirement in requirements} >= {
        harness.releases,
        harness.base,
        harness.data / "cache",
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


def test_legacy_backup_migration_crosses_real_filesystem_boundary(tmp_path):
    import tempfile

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
        harness.ensure_layout()
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
