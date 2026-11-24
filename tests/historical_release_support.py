"""Disposable helpers for genuine historical-release deployment tests."""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import tarfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
REF_040 = "0.40.0"
REF_041 = "0.41.0"
REF_SCHEMA20 = "refs/historical/schema20"
EXPECTED = {
    REF_040: "21c512f8105a803f00d9efe54c50fb1906e71b0d",
    REF_041: "ca1a640fe6b3971cf7f7764083eee19d1598a19a",
    REF_SCHEMA20: "4bbae495593f3b2e6487027bb5254ddfbc28f9ab",
}
REQUIRED = os.environ.get("HNEFATAFL_CROSS_VERSION_REQUIRED") == "1"
FRONTEND_DIST = REPO_ROOT / "frontend/dist"


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=REPO_ROOT, check=True, capture_output=True, text=True)


def require_inputs() -> None:
    missing = []
    for ref, expected in EXPECTED.items():
        try:
            actual = git("rev-parse", "--verify", f"{ref}^{{commit}}").stdout.strip()
        except subprocess.CalledProcessError:
            actual = None
        if actual != expected:
            missing.append(f"{ref} ({expected}, found {actual or 'missing'})")
    if not FRONTEND_DIST.joinpath("index.html").is_file():
        missing.append("frontend/dist/index.html (run npm --prefix frontend run build)")
    if missing:
        message = "Cross-version inputs unavailable: " + "; ".join(missing)
        if REQUIRED:
            raise AssertionError(message)
        import pytest

        pytest.skip(message)


def extract(ref: str, destination: Path) -> Path:
    expected = EXPECTED.get(ref)
    actual = git("rev-parse", "--verify", f"{ref}^{{commit}}").stdout.strip()
    if expected is not None and actual != expected:
        raise AssertionError(f"Refusing untrusted historical source {ref}: {actual}")
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    archive = subprocess.run(
        ["git", "archive", "--format=tar", ref],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    )
    with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as bundle:
        bundle.extractall(destination, filter="data")
    return destination


def package_version(tree: Path) -> str:
    import tomllib

    return tomllib.loads((tree / "pyproject.toml").read_text())["project"]["version"]


def schema_version(tree: Path) -> int:
    text = (tree / "hnefatafl/online_migrations.py").read_text()
    for line in text.splitlines():
        if line.startswith("SCHEMA_VERSION = "):
            return int(line.split("=", 1)[1].strip())
    raise AssertionError("No SCHEMA_VERSION in historical source")


def prepare_release(tree: Path, version: str, *, frontend_dist: Path | None = None) -> Path:
    metadata = {"version": version, "sha256": "disposable-fixture"}
    (tree / ".release.json").write_text(json.dumps(metadata, sort_keys=True) + "\n")
    venv = tree / ".venv"
    if venv.is_symlink() or venv.is_file():
        venv.unlink()
    elif venv.exists():
        shutil.rmtree(venv)
    venv.symlink_to(Path(sys.prefix), target_is_directory=True)
    if frontend_dist is not None:
        target = tree / "frontend/dist"
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(frontend_dist, target)
    return tree


def make_target_release(destination: Path) -> Path:
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    for name in ("hnefatafl", "deployment"):
        shutil.copytree(
            REPO_ROOT / name,
            destination / name,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    (destination / "docs").mkdir()
    shutil.copytree(REPO_ROOT / "docs/rules", destination / "docs/rules")
    for name in ("pyproject.toml", "uv.lock"):
        shutil.copy2(REPO_ROOT / name, destination / name)
    shutil.copytree(FRONTEND_DIST, destination / "frontend/dist")
    return prepare_release(destination, "0.41.1")


def run_historical_script(
    script: Path,
    tree: Path,
    *args: str,
    timeout: int = 180,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": str(tree), "PYTHONDONTWRITEBYTECODE": "1"}
    try:
        return subprocess.run(
            [sys.executable, str(script), *args],
            cwd=cwd or tree,
            env=env,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.CalledProcessError as error:
        raise AssertionError(
            f"Historical script {script.name} {args[0]} failed:\n"
            f"{error.stdout[-4000:]}\n{error.stderr[-4000:]}"
        ) from error


def fixture(script: Path, tree: Path, mode: str, data: Path, manifest: Path) -> None:
    run_historical_script(
        script,
        tree,
        mode,
        "--data",
        str(data),
        "--manifest",
        str(manifest),
        timeout=120,
    )


def _identity(pid: int) -> dict:
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return {"pid": pid, "start": fields[19], "pgrp": int(fields[2])}
    except (OSError, IndexError, ValueError):
        return {"pid": pid}


def process_gone(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    return False


def _terminate_pid_file(pid_file: Path) -> None:
    try:
        payload = json.loads(pid_file.read_text())
        pid = int(payload["pid"])
    except (OSError, ValueError, TypeError, KeyError):
        pid_file.unlink(missing_ok=True)
        return
    # Never signal a stale/reused PID. The harness records the start time and
    # process-group identity with the PID.
    if _identity(pid) != payload:
        pid_file.unlink(missing_ok=True)
        return
    try:
        if payload.get("pgrp") == pid:
            os.killpg(pid, signal.SIGTERM)
        else:
            os.kill(pid, signal.SIGTERM)
        for _ in range(50):
            if process_gone(pid):
                return
            import time

            time.sleep(0.1)
        if payload.get("pgrp") == pid:
            os.killpg(pid, signal.SIGKILL)
        else:
            os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    finally:
        pid_file.unlink(missing_ok=True)


def run_updater_harness(
    harness: Path, tree: Path, config: Path, result: Path, *, timeout: int = 240
) -> dict:
    env = {**os.environ, "PYTHONPATH": str(tree), "PYTHONDONTWRITEBYTECODE": "1"}
    pid_file = Path(json.loads(config.read_text())["log"]).with_suffix(".pid")
    process = subprocess.Popen(
        [sys.executable, str(harness), str(config)],
        cwd=tree,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    try:
        output, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.communicate()
        _terminate_pid_file(pid_file)
        raise AssertionError("Historical updater harness timed out") from None
    finally:
        _terminate_pid_file(pid_file)
    if process.returncode != 0:
        raise AssertionError(f"Historical harness failed: {output[-4000:]}")
    if not result.is_file():
        raise AssertionError(f"Historical harness wrote no result: {output[-4000:]}")
    return json.loads(result.read_text())


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def release_module_path(release: Path) -> Path:
    env = {
        key: value for key, value in os.environ.items() if key not in {"PYTHONPATH", "PYTHONHOME"}
    }
    result = subprocess.run(
        [
            str(release / ".venv/bin/python"),
            "-c",
            "import hnefatafl; print(hnefatafl.__file__)",
        ],
        cwd=release,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return Path(result.stdout.strip()).resolve()


def run_target_script(
    script: Path,
    target: Path,
    data: Path,
    env_file: Path,
    *args: str,
    timeout: int = 120,
) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        **load_env(env_file),
        "HNEFATAFL_DATA_DIR": str(data),
        "PYTHONPATH": str(target),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    env.pop("PYTHONHOME", None)
    return subprocess.run(
        [str(target / ".venv/bin/python"), str(script), *args],
        cwd=target,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def load_env(path: Path) -> dict[str, str]:
    values = {}
    if path.is_file():
        for line in path.read_text().splitlines():
            if not line.strip() or line.startswith("#"):
                continue
            name, separator, value = line.partition("=")
            if separator:
                values[name] = value
    return values
