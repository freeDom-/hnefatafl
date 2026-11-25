"""Actual-browser verification of the production browser-analysis runtime.

Opt-in: requires a production frontend build, a staged runtime and Chromium.

    HNEFATAFL_BROWSER_ANALYSIS_BROWSER=1 \
    HNEFATAFL_BROWSER_ANALYSIS_DATA=/path/to/data \
    .venv/bin/python -m pytest tests/test_browser_analysis_browser.py -q

The test starts the real FastAPI application (one uvicorn worker) with the real
production CSP, then drives the built frontend in Chromium. It never touches a
deployed server or real operator data.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "frontend" / "dist"
SCRIPT = ROOT / "scripts" / "verify_browser_analysis.mjs"
PASSWORD = "browser-analysis-browser-test-1"

pytestmark = pytest.mark.skipif(
    os.environ.get("HNEFATAFL_BROWSER_ANALYSIS_BROWSER") != "1",
    reason="set HNEFATAFL_BROWSER_ANALYSIS_BROWSER=1 for the actual-browser gate",
)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _prepare_account(data: Path) -> None:
    from hnefatafl.auth import Auth, SecuritySettings
    from hnefatafl.storage import Store

    store = Store(data / "hnefatafl.sqlite3")
    auth = Auth(store, SecuritySettings(production=False))
    import sqlite3

    try:
        auth.create_account("Browser", "browser@example.test", PASSWORD, "USER")
    except (ValueError, sqlite3.IntegrityError):
        # A repeated local run reuses the disposable account.
        pass


def _wait_for_health(url: str, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url + "/api/health", timeout=2) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.2)
    raise RuntimeError("The application did not become healthy")


def test_production_runtime_initializes_under_the_real_csp(tmp_path):
    data = Path(os.environ["HNEFATAFL_BROWSER_ANALYSIS_DATA"]).resolve()
    assert DIST.is_dir(), "build the production frontend first"
    assert (data / "browser_runtime" / "current").is_symlink(), "stage and activate a runtime"
    _prepare_account(data)

    from hnefatafl.engine import new_game

    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps(new_game("brandubh").to_dict()))
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    environment = {
        **os.environ,
        "HNEFATAFL_DATA_DIR": str(data),
        "HNEFATAFL_ENV": "development",
        "HNEFATAFL_BROWSER_ANALYSIS_ENABLED": "1",
        "PYTHONPATH": str(ROOT),
    }
    server = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "hnefatafl.server:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--workers",
            "1",
        ],
        cwd=ROOT,
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _wait_for_health(base)
        report = tmp_path / "browser-report.json"
        completed = subprocess.run(
            [
                "node",
                str(SCRIPT),
                "--base-url",
                base,
                "--dist",
                str(DIST),
                "--state",
                str(state_path),
                "--identity",
                "Browser",
                "--password",
                PASSWORD,
                "--output",
                str(report),
            ],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert completed.returncode == 0, completed.stdout + "\n" + completed.stderr
        payload = json.loads(report.read_text())
        assert payload["ok"] is True
        check_names = {entry["name"] for entry in payload["checks"]}
        assert "runtime initializes" in check_names
        assert "CSP allows WASM without broad eval" in check_names
        assert payload["requests"]["modelArtifacts"] == 0
        assert payload["runtime"]["cycles"]["coldInitMs"] > 0
    finally:
        server.terminate()
        try:
            server.wait(timeout=15)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)


def test_deployment_is_disabled_without_the_feature_flag(tmp_path):
    """The same production build must start and work with the runtime absent."""
    data = tmp_path / "data"
    data.mkdir()
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    environment = {
        **os.environ,
        "HNEFATAFL_DATA_DIR": str(data),
        "HNEFATAFL_ENV": "development",
        "PYTHONPATH": str(ROOT),
    }
    server = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "hnefatafl.server:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--workers",
            "1",
        ],
        cwd=ROOT,
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _wait_for_health(base)
        with urllib.request.urlopen(base + "/login", timeout=5) as response:
            assert response.status == 200
            header = response.headers.get("Content-Security-Policy", "")
        assert "'wasm-unsafe-eval'" not in header
        with urllib.request.urlopen(base + "/api/health", timeout=5) as response:
            assert json.loads(response.read()) == {"status": "ok"}
        import httpx

        response = httpx.get(
            base + "/browser-analysis/runtime/0/runtime/pyodide.js",
            headers={"Accept": "text/html"},
            follow_redirects=False,
        )
        assert response.status_code in (307, 401, 404)
        assert "wasm-unsafe-eval" not in response.headers.get("content-security-policy", "")
    finally:
        server.terminate()
        try:
            server.wait(timeout=15)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)
