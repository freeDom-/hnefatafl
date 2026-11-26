"""End-to-end recovery through the actual installed standalone deployment CLI.

The test installs ``deployment.py`` and its root-owned restore support package in a
disposable layout, then executes that file as the CLI with a real SQLite database.
It does not import the repository copy for the restore itself.
"""

import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest


from deployment_fixtures import legacy_snapshot

from hnefatafl.deployment import Deployment, atomic_json

REPO = Path(__file__).resolve().parents[1]
SUPPORT = (
    "online_migrations",
    "league_migrations",
    "league_policy",
    "league_recovery_migrations",
    "rating",
)
CONFIG = {"port": 8000, "public_url": "https://example.test"}


def build_database(path, secret="snapshot-secret"):
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE persistent(value TEXT)")
        connection.execute("INSERT INTO persistent VALUES('at-snapshot')")
        connection.execute("CREATE TABLE accounts(id TEXT PRIMARY KEY, secret TEXT)")
        connection.execute("INSERT INTO accounts VALUES('odin', ?)", (secret,))
        connection.execute("CREATE TABLE sessions(token TEXT)")
        connection.commit()


def build_layout(tmp_path):
    root = tmp_path / "layout"
    root.mkdir()
    base = root / "opt"
    data = root / "data"
    etc = root / "etc"
    backups = root / "backups"
    installed = root / "usr/lib/hnefatafl"
    for path in (base / "releases", data, etc, backups, installed / "updater_support"):
        path.mkdir(parents=True)
    backups.chmod(0o700)
    release = base / "releases/1.0.0-0001"
    (release / "deployment").mkdir(parents=True)
    (release / "deployment/hnefatafl.service").write_text("unit\n")
    atomic_json(release / ".release.json", {"version": "1.0.0", "prepared_at": 1})
    (base / "current").symlink_to(release)
    (etc / "application.env").write_text(
        "HNEFATAFL_ENV=production\nHNEFATAFL_PUBLIC_URL=https://example.test\nHNEFATAFL_PORT=8000\n"
    )
    (etc / "deployment.json").write_text(json.dumps(CONFIG) + "\n")
    os.chmod(etc / "deployment.json", 0o600)
    atomic_json(
        etc / "installed.json",
        {
            "version": "1.0.0",
            "target": str(release),
            "previous": None,
            "backup": None,
            "phase": "committed",
        },
    )
    database = data / "hnefatafl.sqlite3"
    build_database(database)
    (data / "models").mkdir()
    (data / "models/weights").write_bytes(b"snapshot-model")

    deployment = Deployment(
        CONFIG,
        base=base,
        data=data,
        etc=etc,
        unit_file=root / "unit.service",
        backups=backups,
        updater_root=installed,
    )
    snapshot = legacy_snapshot(deployment)

    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE persistent SET value='newer-live-state'")
        connection.execute("UPDATE accounts SET secret='newer-password-hash'")
        connection.execute("INSERT INTO sessions VALUES('live-session')")
        connection.commit()
    (data / "models/weights").write_bytes(b"newer-model")
    atomic_json(
        etc / "transaction.json",
        {
            "phase": "migrating",
            "previous": None,
            "target": str(release),
            "backup": str(snapshot),
            "service_stopped": True,
            "migration_started": True,
            "data_modified": True,
            "restore": None,
        },
    )

    shutil.copyfile(REPO / "hnefatafl/deployment.py", installed / "deployment.py")
    for name in SUPPORT:
        shutil.copyfile(REPO / f"hnefatafl/{name}.py", installed / "updater_support" / f"{name}.py")
    bin_directory = root / "bin"
    bin_directory.mkdir()
    systemctl = bin_directory / "systemctl"
    systemctl.write_text('#!/bin/sh\ncase "$1" in\n  is-active) exit 1 ;;\n  *) exit 0 ;;\nesac\n')
    systemctl.chmod(0o755)
    return root, release, snapshot, database, installed, bin_directory


def run_cli(installed, root, bin_directory, *arguments, interrupt=None):
    env = {
        **os.environ,
        "PATH": str(bin_directory) + ":/usr/bin:/bin",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    if interrupt:
        env["DISPOSABLE_UPDATER_INTERRUPTION"] = interrupt
    bootstrap = """
import os, runpy, signal, sys
from pathlib import Path
root = Path(sys.argv[2])
module = runpy.run_path(sys.argv[1])
point = os.getenv('DISPOSABLE_UPDATER_INTERRUPTION')
if point:
    deployment = module['Deployment']
    def stop(): os.kill(os.getpid(), signal.SIGKILL)
    def probe(self):
        assert (root/'running').is_file()
        with (root/'actions').open('a') as log: log.write('probe\\n')
    deployment._recovery_probe = probe
    rename, replace, unlink, record = os.rename, os.replace, os.unlink, deployment.record
    def renaming(old, new, **kwargs):
        rename(old, new, **kwargs)
        if point == 'gate-moved' and old == '.deployment-gate': stop()
        if point == 'old-data-renamed' and old == root/'data': stop()
        if point == 'manual-backup-published' and Path(new).parent == root/'backups': stop()
    def replacing(old, new, **kwargs):
        replace(old, new, **kwargs)
        if point == 'installed-state' and new == root/'etc/installed.json': stop()
    def unlinking(path, **kwargs):
        unlink(path, **kwargs)
        if point == 'marker-unlinked' and path == '.restore.json': stop()
    def recording(self, value):
        record(self, value)
        if point == 'journal-committed' and value.get('phase') == 'recovered': stop()
    os.rename, os.replace, os.unlink = renaming, replacing, unlinking
    deployment.record = recording
raise SystemExit(module['main'](sys.argv[3:], layout={
    "config_dir": root / "etc", "base": root / "opt", "data": root / "data",
    "etc": root / "etc", "unit_file": root / "unit.service", "backups": root / "backups",
    "lock": root / "update.lock", "updater_root": Path(sys.argv[1]).parent,
}))
"""
    return subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            bootstrap,
            str(installed / "deployment.py"),
            str(root),
            *arguments,
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )


def test_installed_cli_restores_sqlite_snapshot_without_source_checkout(tmp_path):
    root, release, snapshot, database, installed, bin_directory = build_layout(tmp_path)
    result = run_cli(installed, root, bin_directory, "--recover")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Restored pre-deployment state" in result.stdout
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT value FROM persistent").fetchone()[0] == "at-snapshot"
        # Account security changes made after the snapshot remain authoritative.
        assert (
            connection.execute("SELECT secret FROM accounts WHERE id='odin'").fetchone()[0]
            == "newer-password-hash"
        )
        # Saved sessions are revoked by the restore.
        assert connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
    assert (root / "data/models/weights").read_bytes() == b"snapshot-model"
    retained = [path for path in root.iterdir() if path.name.startswith(".retained-")]
    assert retained
    assert (retained[0] / "models/weights").read_bytes() == b"newer-model"
    journal = json.loads((root / "etc/transaction.json").read_text())
    assert journal["phase"] == "recovered"
    assert journal["restore"] is None


def test_installed_cli_has_no_production_layout_override(tmp_path):
    root, _release, _snapshot, _database, installed, bin_directory = build_layout(tmp_path)
    result = run_cli(installed, root, bin_directory, "--recover", "--layout-root", str(root))
    assert result.returncode == 2
    assert "unrecognized arguments: --layout-root" in result.stderr


def test_installed_cli_rollback_verifies_snapshot_before_service_stop(tmp_path):
    """A corrupted snapshot must be rejected by the installed CLI before stopping."""
    root, release, snapshot, database, installed, bin_directory = build_layout(tmp_path)
    (root / "etc/transaction.json").unlink()
    (snapshot / "data/persistent").write_text("x")
    (snapshot / "data/persistent").unlink()
    (snapshot / "data/models/weights").write_bytes(b"corrupted")
    # Rewrite the transaction as an explicit rollback with a previous release.
    previous = root / "opt/releases/0.9.0-0002"
    (previous / "deployment").mkdir(parents=True)
    (previous / "deployment/hnefatafl.service").write_text("unit\n")
    atomic_json(previous / ".release.json", {"version": "0.9.0"})
    atomic_json(
        root / "etc/installed.json",
        {
            "version": "1.0.0",
            "target": str(release),
            "previous": str(previous),
            "backup": str(snapshot),
            "phase": "committed",
        },
    )
    result = run_cli(installed, root, bin_directory, "--rollback")
    assert result.returncode != 0
    assert "snapshot verification failed" in result.stderr
    with sqlite3.connect(database) as connection:
        assert (
            connection.execute("SELECT value FROM persistent").fetchone()[0] == "newer-live-state"
        )
    assert (root / "opt/current").resolve() == release


def install_service_shim(root, bin_directory):
    systemctl = bin_directory / "systemctl"
    systemctl.write_text(
        f'#!/bin/sh\nroot="{root}"\n'
        'case "$1" in\n'
        '  is-active) test -f "$root/running" ;;\n'
        '  start) touch "$root/running"; echo start >> "$root/actions" ;;\n'
        '  stop) rm -f "$root/running"; echo stop >> "$root/actions" ;;\n'
        "  *) exit 0 ;;\nesac\n"
    )
    (root / "running").touch()


@pytest.mark.parametrize(
    "point",
    ["old-data-renamed", "gate-moved", "installed-state", "journal-committed", "marker-unlinked"],
)
def test_killed_explicit_rollback_resumes_with_documented_recover_command(tmp_path, point):
    root, previous, snapshot, database, installed, bin_directory = build_layout(tmp_path)
    (root / "etc/transaction.json").unlink()
    current = root / "opt/releases/2.0.0-0002"
    (current / "deployment").mkdir(parents=True)
    (current / "deployment/hnefatafl.service").write_text("unit\n")
    atomic_json(current / ".release.json", {"version": "2.0.0"})
    (root / "opt/current").unlink()
    (root / "opt/current").symlink_to(current)
    atomic_json(
        root / "etc/installed.json",
        {
            "version": "2.0.0",
            "target": str(current),
            "previous": str(previous),
            "backup": str(snapshot),
            "phase": "committed",
        },
    )
    gate = root / "data/.deployment-gate"
    gate.touch(mode=0o600)
    inode = gate.stat().st_ino
    install_service_shim(root, bin_directory)
    interrupted = run_cli(installed, root, bin_directory, "--rollback", interrupt=point)
    assert interrupted.returncode == -9, interrupted.stdout + interrupted.stderr
    before = (root / "etc/transaction.json").read_bytes()
    assert json.loads(before)["operation"] == "rollback"
    rejected = run_cli(installed, root, bin_directory, "--rollback", interrupt="resume")
    assert rejected.returncode == 1
    assert "--recover" in rejected.stderr
    assert (root / "etc/transaction.json").read_bytes() == before
    resumed = run_cli(installed, root, bin_directory, "--recover", interrupt="resume")
    assert resumed.returncode == 0, resumed.stdout + resumed.stderr
    assert (root / "opt/current").resolve() == previous
    state = json.loads((root / "etc/installed.json").read_text())
    assert (state["target"], state["version"], state["previous"], state["backup"]) == (
        str(previous),
        "1.0.0",
        None,
        None,
    )
    assert gate.stat().st_ino == inode
    assert not (root / "data/.restore.json").exists()
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT value FROM persistent").fetchone()[0] == "at-snapshot"
        assert (
            connection.execute("SELECT secret FROM accounts").fetchone()[0] == "newer-password-hash"
        )
    assert len(list(root.glob(".retained-*"))) == 1
    completion = json.loads((root / "etc/transaction.json").read_text())
    assert completion["phase"] == "recovered" and completion["restore"] is None
    assert (root / "running").is_file()
    actions = (root / "actions").read_text()
    assert "stop\n" in actions and "start\nprobe\n" in actions
    second = run_cli(installed, root, bin_directory, "--recover", interrupt="resume")
    assert second.returncode == 0, second.stdout + second.stderr
    assert (root / "actions").read_text() == actions


@pytest.mark.parametrize("arguments", [("--check",), ("--list",), ("--cleanup", "--dry-run")])
def test_read_only_cli_does_not_recover_or_create_lock(tmp_path, arguments):
    root, _release, _snapshot, _database, installed, bin_directory = build_layout(tmp_path)
    refresh = installed / ".refresh.json"
    atomic_json(refresh, {"schema": 1, "phase": "prepared", "entries": []})
    legacy = root / "data/backups"
    legacy.mkdir(mode=0o700)
    (legacy / "retained-legacy").write_text("retained")
    before = {
        path.relative_to(root): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and not path.is_symlink()
    }
    result = run_cli(installed, root, bin_directory, *arguments)
    assert result.returncode == 1 and "--recover" in result.stderr
    assert before == {
        path.relative_to(root): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and not path.is_symlink()
    }
    assert not (root / "update.lock").exists()


def test_clean_dry_run_preserves_legacy_layout_and_missing_lock(tmp_path):
    root, _release, snapshot, _database, installed, bin_directory = build_layout(tmp_path)
    (root / "etc/transaction.json").unlink()
    legacy = root / "data/backups"
    legacy.mkdir(mode=0o700)
    snapshot.rename(legacy / snapshot.name)
    result = run_cli(installed, root, bin_directory, "--cleanup", "--dry-run")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (legacy / snapshot.name).is_dir()
    assert not (root / "etc/backup-migration.json").exists()
    assert not (root / "update.lock").exists()


def test_killed_manual_backup_recovers_service_and_keeps_published_snapshot(tmp_path):
    root, _release, _snapshot, database, installed, bin_directory = build_layout(tmp_path)
    (root / "etc/transaction.json").unlink()
    install_service_shim(root, bin_directory)
    interrupted = run_cli(
        installed, root, bin_directory, "--backup", interrupt="manual-backup-published"
    )
    assert interrupted.returncode == -9, interrupted.stdout + interrupted.stderr
    transaction = json.loads((root / "etc/transaction.json").read_text())
    assert transaction["phase"] == "snapshot" and transaction["service_was_running"]
    assert not (root / "running").exists()
    backup = root / "backups" / transaction["snapshot_name"]
    assert backup.is_dir()
    with sqlite3.connect(backup / "data" / database.name) as connection:
        assert (
            connection.execute("SELECT value FROM persistent").fetchone()[0] == "newer-live-state"
        )
    result = run_cli(installed, root, bin_directory, "--recover", interrupt="resume")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (root / "running").is_file()
    assert (root / "actions").read_text() == "stop\nstart\nprobe\n"
    assert backup.is_dir()
    assert json.loads((root / "etc/transaction.json").read_text())["phase"] == "recovered"
