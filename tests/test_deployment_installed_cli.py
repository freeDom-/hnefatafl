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
    (root / ".deployment-test-layout").write_text("disposable\n")
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
    snapshot = deployment.snapshot()

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


def run_cli(installed, root, bin_directory, *arguments):
    env = {
        **os.environ,
        "PATH": str(bin_directory) + ":/usr/bin:/bin",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    return subprocess.run(
        [sys.executable, str(installed / "deployment.py"), *arguments, "--layout-root", str(root)],
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


def test_installed_cli_refuses_layout_without_test_marker(tmp_path):
    root, _release, _snapshot, _database, installed, bin_directory = build_layout(tmp_path)
    (root / ".deployment-test-layout").unlink()
    result = run_cli(installed, root, bin_directory, "--recover")
    assert result.returncode != 0
    assert "marker is missing" in result.stderr


def test_installed_cli_rollback_verifies_snapshot_before_service_stop(tmp_path):
    """A corrupted snapshot must be rejected by the installed CLI before stopping."""
    root, release, snapshot, database, installed, bin_directory = build_layout(tmp_path)
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
