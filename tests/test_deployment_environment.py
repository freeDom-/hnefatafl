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


pytestmark = pytest.mark.skipif(UV is None, reason="uv is required for the real venv test")


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
