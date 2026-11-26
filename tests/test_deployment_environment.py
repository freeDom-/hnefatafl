"""Real virtual-environment publication test (no mocked dependency installs).

Creates an actual virtual environment with a console script and uvicorn, then
verifies the published release at its permanent path. A control experiment shows
that renaming a prepared environment leaves generated entry points pointing at the
old location, which is why preparation happens at the final path.
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from hnefatafl.deployment import Deployment, DeploymentError

REPO = Path(__file__).resolve().parents[1]
UV = shutil.which("uv")


requires_uv = pytest.mark.skipif(UV is None, reason="uv is required for the real venv test")


def write_project(root: Path):
    root.mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        """
[build-system]
requires = ["setuptools>=77"]
build-backend = "setuptools.build_meta"

[project]
name = "hnefatafl"
version = "1.0.0"
requires-python = ">=3.11"
dependencies = ["uvicorn>=0.30,<1"]

[project.scripts]
hnefatafl = "hnefatafl.cli:main"
""".lstrip()
    )
    package = root / "hnefatafl"
    package.mkdir()
    (package / "__init__.py").write_text("__version__ = '1.0.0'\n")
    (package / "accounts.py").write_text(
        """
import argparse


def main(argv=None):
    parser = argparse.ArgumentParser(prog="hnefatafl.accounts")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("migrate")
    args = parser.parse_args(argv)
    if args.command == "migrate":
        print("migration-ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
""".lstrip()
    )
    (package / "server.py").write_text("app = object()\n")
    (package / "cli.py").write_text(
        """
def main():
    print("hnefatafl-console-ok")
""".lstrip()
    )


def install(root: Path):
    subprocess.run(
        [UV, "venv", str(root / ".venv")],
        check=True,
        capture_output=True,
        text=True,
    )
    result = subprocess.run(
        [
            UV,
            "pip",
            "install",
            "--python",
            str(root / ".venv/bin/python"),
            str(root),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if result.returncode != 0:
        pytest.skip("uv could not install the fixture project offline: " + result.stderr[-400:])
    return root / ".venv/bin/python"


def environment(tmp_path):
    deployment = Deployment(
        {"port": 8000, "public_url": "https://example.test"},
        base=tmp_path / "opt",
        data=tmp_path / "data",
        etc=tmp_path / "etc",
        unit_file=tmp_path / "unit.service",
        backups=tmp_path / "backups",
    )
    deployment.releases.mkdir(parents=True)
    deployment.data.mkdir()
    deployment.etc.mkdir()
    return deployment


@requires_uv
def test_published_release_keeps_functional_entry_points(tmp_path):
    deployment = environment(tmp_path)
    target = deployment.releases / "1.0.0-abcdef12"
    write_project(target)
    python = install(target)

    assert (target / ".venv/bin/hnefatafl").is_file()
    shebang = (target / ".venv/bin/hnefatafl").read_text().splitlines()[0]
    assert str(target) in shebang
    deployment._verify_release_environment(target)

    console = subprocess.run(
        [str(target / ".venv/bin/hnefatafl")],
        capture_output=True,
        text=True,
        timeout=60,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert console.returncode == 0, console.stderr
    assert "hnefatafl-console-ok" in console.stdout
    migration = subprocess.run(
        [str(python), "-m", "hnefatafl.accounts", "migrate"],
        cwd=target,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert migration.returncode == 0 and "migration-ok" in migration.stdout
    uvicorn = subprocess.run(
        [str(python), "-m", "uvicorn", "--version"],
        cwd=target,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert uvicorn.returncode == 0


@requires_uv
def test_renaming_a_prepared_environment_would_break_entry_points(tmp_path):
    deployment = environment(tmp_path)
    staging = deployment.releases / ".staging-release-0123456789abcdef"
    final = deployment.releases / "1.0.0-abcdef12"
    write_project(staging)
    install(staging)
    os.rename(staging, final)
    content = (final / ".venv/bin/hnefatafl").read_text()
    # The generated entry point still references the staging path; this is the
    # relocation failure the preparation-at-final-path strategy avoids.
    assert str(staging) in content
    with pytest.raises((DeploymentError, subprocess.SubprocessError, OSError)):
        deployment._verify_release_environment(final)


@pytest.fixture
def privileged_deployment_root():
    import pwd
    import tempfile

    if os.geteuid() != 0:
        pytest.skip("requires real root and a separate service account")
    uid = pwd.getpwnam("nobody").pw_uid
    gid = pwd.getpwnam("nobody").pw_gid
    assert uid != 0
    # The installed launcher requires every ancestor to be root-controlled.
    with tempfile.TemporaryDirectory(dir="/var/lib", prefix="hnefatafl-test-") as path:
        root = Path(path)
        root.chmod(0o755)
        yield root, uid, gid


def test_privileged_preparation_imports_the_real_application_after_sealing(
    privileged_deployment_root,
):
    import json
    import stat
    import sys
    import sysconfig
    import tarfile
    import tomllib

    from test_deployment_storage import FakeGitHub

    root, uid, gid = privileged_deployment_root
    native_systemd = os.environ.get("HNEFATAFL_TEST_SYSTEMD") == "1"
    production = root / "data"
    active_file = production / "live-sentinel"
    build_python = Path(sys._base_executable)
    packages = Path(sysconfig.get_path("purelib"))
    if native_systemd:
        # A hosted runner's virtual environment can live below a private home.
        packages = shutil.copytree(packages, root / "dependencies", symlinks=True)
        subprocess.run(["chmod", "-R", "a+rX", str(packages)], check=True)

    class PrivilegedDeployment(Deployment):
        def _data_account(self):
            return uid, gid

        def _run_isolated_preparation(self, command, cwd, environment, uid, gid):
            if native_systemd:
                return super()._run_isolated_preparation(command, cwd, environment, uid, gid)
            # The Debian container test exercises real filesystem ownership and
            # the native CI step exercises the systemd mount namespace as well.
            subprocess.run(
                command,
                cwd=cwd,
                env=environment,
                check=True,
                user=uid,
                group=gid,
                extra_groups=[],
            )

        def _stop_staging_units(self, path):
            if native_systemd:
                super()._stop_staging_units(path)

        def _verify_release_environment(self, target, environment=None):
            assert environment is not None
            mode = target.stat().st_mode
            assert stat.S_ISDIR(mode) and target.stat().st_uid == 0 and not mode & 0o022
            probe = """
import json, os, sys
from pathlib import Path
from hnefatafl.server import app
root = Path(os.environ["HNEFATAFL_DATA_DIR"])
assert app.state.data_dir == root
assert sys.prefix == str(Path.cwd() / ".venv")
assert os.geteuid() == int(sys.argv[1])
assert not os.access(Path.cwd(), os.W_OK)
assert all((root / name).exists() for name in ("hnefatafl.sqlite3", "jobs", "models"))
if sys.argv[3] == 'True':
    assert not os.access(sys.argv[2], os.R_OK)
    assert not os.access(sys.argv[2], os.W_OK)
(root / "verified.json").write_text(json.dumps({"uid": os.geteuid()}))
"""
            self.run(
                [target / ".venv/bin/python", "-c", probe, uid, active_file, native_systemd],
                cwd=target,
                user=True,
                extra_env=environment,
                preparation=True,
            )
            assert json.loads(
                (Path(environment["HNEFATAFL_DATA_DIR"]) / "verified.json").read_text()
            ) == {"uid": uid}
            return super()._verify_release_environment(target, environment)

    deployment = PrivilegedDeployment(
        {"storage": {"release_estimate_mib": 128, "cache_headroom_mib": 0, "headroom_mib": 0}},
        base=root / "opt",
        data=production,
        etc=root / "etc",
        unit_file=root / "hnefatafl-fixture.service",
    )
    deployment.releases.mkdir(parents=True)
    deployment.etc.mkdir()
    production.mkdir()
    active_file.write_text("persistent state")
    os.chown(production, uid, gid)
    os.chown(active_file, uid, gid)
    assert (
        subprocess.run(["/usr/bin/test", "-w", str(active_file)], user=uid, group=gid).returncode
        == 0
    )
    before = {path.name: (path.stat().st_ino, path.read_bytes()) for path in production.iterdir()}

    bin_path = deployment.base / "tools/bin"
    bin_path.mkdir(parents=True)
    (bin_path / "uv").write_text(
        f"""#!{build_python}
import os, sysconfig, venv
from pathlib import Path
assert os.geteuid() == {uid}
assert Path(os.environ['TMPDIR']).is_dir()
assert Path(os.environ['HOME']).is_dir()
assert Path(os.environ['UV_CACHE_DIR']).is_dir()
venv.create('.venv', with_pip=False, symlinks=True)
purelib = Path(sysconfig.get_path('purelib', vars={{'base': str(Path('.venv').resolve())}}))
(purelib / 'fixture-dependencies.pth').write_text({str(packages) + chr(10)!r})
"""
    )
    (bin_path / "uv").chmod(0o755)
    (bin_path / "npm").write_text(
        f"""#!{build_python}
import os, sys
from pathlib import Path
assert os.geteuid() == {uid}
assert Path(os.environ['npm_config_cache']).is_dir()
if 'build' in sys.argv:
    Path('frontend/dist').mkdir()
    Path('frontend/dist/index.html').write_text('<!doctype html><title>Preparation fixture</title>')
"""
    )
    (bin_path / "npm").chmod(0o755)

    source = root / "release.tar.gz"
    with tarfile.open(source, "w:gz") as archive:
        for name in (
            "pyproject.toml",
            "uv.lock",
            "frontend/package-lock.json",
            "tests/conftest.py",
            "tests/record_fixtures.py",
            "tests/test_security.py",
            "deployment/hnefatafl.service",
            "docs/API_PERMISSIONS.json",
            "hnefatafl",
        ):
            archive.add(
                REPO / name,
                arcname=name,
                filter=lambda member: None if "__pycache__" in Path(member.name).parts else member,
            )
    tag = tomllib.loads((REPO / "pyproject.toml").read_text())["project"]["version"]
    release = deployment.prepare(FakeGitHub(source), {"tag_name": tag})
    assert deployment._require_complete_release(release)["version"] == tag
    assert (release / "frontend/dist/index.html").is_file()
    assert not (release / "data").exists()
    assert not any(release.rglob("*.sqlite3"))
    assert not list(deployment.temporary_root.iterdir())
    assert before == {
        path.name: (path.stat().st_ino, path.read_bytes()) for path in production.iterdir()
    }
    verify_installed_startup_guard(deployment, release, root, uid, gid, native_systemd)


def test_privileged_legacy_cleanup_preview_matches_execution(privileged_deployment_root, capsys):
    import json
    import time

    from test_deployment_storage import Harness

    root, uid, gid = privileged_deployment_root

    class PrivilegedHarness(Harness):
        def _data_account(self):
            return uid, gid

    updater = PrivilegedHarness(root)
    os.chown(updater.data, uid, gid)
    updater.legacy_backups.mkdir(mode=0o700)
    assert updater.legacy_backups.stat().st_uid == 0
    versions = ("0.36", "0.37", "0.39", "0.40", "0.41.1", "0.42")
    releases = [
        updater.release(version, f"000{i}", prepared_at=i) for i, version in enumerate(versions)
    ]
    active, rollback = releases[-1], releases[-2]
    updater.activate(active)
    legacy_snapshots = []
    for i in range(1, 8):
        name = f"20261126T05{i:02d}00-{i:08x}" if i != 2 else "20261126T052818-d3c3c782"
        backup = updater.make_backup(name, complete=i != 2, old=7200 - i)
        target = updater.legacy_backups / backup.name
        backup.rename(target)
        legacy_snapshots.append(target)
    incomplete = legacy_snapshots[1]
    with (incomplete / "data/sentinel").open("rb+") as data:
        data.truncate(195 * 1024**2)
    now = time.time()
    os.utime(incomplete, (now - 7200, now - 7200))
    protected = legacy_snapshots[0]
    protected_inode = protected.stat().st_ino
    updater.set_state("0.42", active, previous=rollback, backup=protected)
    before = updater.state.read_bytes()
    from hnefatafl.deployment import FilesystemInfo

    updater.filesystem_override = lambda path: FilesystemInfo(1, Path(path), 16 * 1024**2, 32)
    preview = updater.cleanup(dry_run=True)
    assert not preview.errors
    assert updater.state.read_bytes() == before
    assert sorted(updater.legacy_backups.iterdir()) == sorted(legacy_snapshots)
    assert not list(updater.backups.iterdir())
    assert len([entry for entry in preview.entries if entry.action == "move"]) == 6
    assert not any(entry.status == "invalid" for entry in preview.entries)
    assert any(entry.path == incomplete and entry.action == "remove" for entry in preview.entries)
    assert preview.reclaimable_bytes >= 195 * 1024**2

    applied = updater.cleanup()
    assert not applied.errors
    assert active.is_dir() and rollback.is_dir()
    assert len(list(updater.releases.iterdir())) == 3
    assert len(list(updater.backups.iterdir())) == 3
    assert not incomplete.exists()
    assert not updater.legacy_backups.exists()
    assert json.loads(updater.state.read_text())["backup"] == str(updater.backups / protected.name)
    assert (updater.backups / protected.name).stat().st_ino == protected_inode
    assert {entry.path.name for entry in preview.entries if entry.action == "remove"} == {
        entry.path.name for entry in applied.removed
    }
    assert "Moved verified legacy snapshot" in capsys.readouterr().out
    assert (updater.data / "models/weights").read_bytes() == b"retained-model"


def verify_installed_startup_guard(deployment, release, root, uid, gid, native_systemd):
    import json
    import secrets
    import socket
    import sys
    import time

    from hnefatafl.deployment import atomic_json, install_reviewed_updater

    commands = root / "bin"
    utilities = root / "utilities"
    commands.mkdir()
    lock = root / "update.lock"
    atomic_json(deployment.etc / "deployment.json", deployment.config)
    install_reviewed_updater(REPO, deployment.etc, utilities, commands, lock)
    deployment.activate(release)
    atomic_json(
        deployment.state,
        {"version": release.name.split("-")[0], "target": str(release), "phase": "committed"},
    )
    journal = {
        "abi": 3,
        "operation": "manual-backup",
        "phase": "prepared",
        "transaction": "1" * 32,
        "snapshot_name": "20261126T050000-0001",
        "snapshot_id": "2" * 32,
        "service_was_running": True,
    }
    deployment.record(journal)
    guard = root / "guard.py"
    guard.write_text(
        f"""
import functools, runpy, sys
from pathlib import Path

root = Path({str(root)!r})
load = runpy.run_path
launcher = load(str(root / 'utilities/deployment.py'))

def fixture_generation(path):
    generation = load(path)
    namespace = generation['guard_service_start'].__globals__
    cls = namespace['Deployment']
    cls._data_account = lambda self: ({uid}, {gid})
    namespace['_production_layout'] = lambda: {{'config_dir': root / 'etc'}}
    namespace['Deployment'] = functools.partial(
        cls, base=root / 'opt', data=root / 'data', etc=root / 'etc',
    )
    return generation

runpy.run_path = fixture_generation
sys.argv = [sys.argv[0], '--guard-data-root']
raise SystemExit(launcher['launch'](root / 'update.lock'))
"""
    )
    guard_command = [sys._base_executable, "-I", "-B", str(guard)]
    subprocess.run(guard_command, check=True)
    deployment.record({**journal, "abi": 999})
    rejected = subprocess.run(guard_command, capture_output=True, text=True)
    assert rejected.returncode and "unsupported updater ABI" in rejected.stderr
    deployment.record(journal)
    if not native_systemd:
        return

    unit = f"hnefatafl-review-fixture-{secrets.token_hex(6)}.service"
    health_socket = deployment.data / "fixture.sock"
    command = [
        "systemd-run",
        "--quiet",
        "--collect",
        f"--uid={uid}",
        f"--gid={gid}",
        f"--working-directory={release}",
        "--property=Type=exec",
        f"--property=ExecStartPre=+{' '.join(guard_command)}",
        f"--setenv=HNEFATAFL_DATA_DIR={deployment.data}",
        "--setenv=PYTHONDONTWRITEBYTECODE=1",
        "--setenv=OMP_NUM_THREADS=2",
        "--setenv=MKL_NUM_THREADS=2",
        str(release / ".venv/bin/python"),
        "-m",
        "uvicorn",
        "hnefatafl.server:app",
        "--uds",
        str(health_socket),
    ]
    try:
        subprocess.run([*command[:1], f"--unit={unit}", *command[1:]], check=True)
        for _ in range(100):
            try:
                with socket.socket(socket.AF_UNIX) as client:
                    client.settimeout(2)
                    client.connect(str(health_socket))
                    client.sendall(b"GET /api/health HTTP/1.0\r\nHost: localhost\r\n\r\n")
                    with client.makefile("rb") as response:
                        assert b"200 OK" in response.readline()
                        while response.readline().strip():
                            pass
                        assert json.loads(response.read())["status"] == "ok"
                break
            except (FileNotFoundError, ConnectionRefusedError):
                time.sleep(0.3)
        else:
            subprocess.run(["journalctl", "--no-pager", "-n", "80", "-u", unit], check=False)
            pytest.fail("the real systemd service did not become ready")
    finally:
        subprocess.run(["systemctl", "stop", unit], check=False)
