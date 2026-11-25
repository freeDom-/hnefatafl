"""Bounded readiness helper for sustained owner workflows."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SPIKE = Path(__file__).resolve().parents[1] / "spikes/browser_offline"
HELPER = SPIKE / "wait_for_ready.py"
SESSION = "ready-test"
MODEL = "a" * 64


def _run_ready(pid, ready, timeout=5.0):
    return subprocess.run(
        [
            sys.executable,
            str(HELPER),
            "--ready",
            str(ready),
            "--pid",
            str(pid),
            "--timeout",
            str(timeout),
            "--session",
            SESSION,
            "--model-sha256",
            MODEL,
            "--mode",
            "repeated",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_ready_document_success(tmp_path):
    ready = tmp_path / "ready.json"
    writer = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import json,pathlib,time\n"
                "time.sleep(0.2)\n"
                f"pathlib.Path({str(ready)!r}).write_text(json.dumps({{'ready':True,"
                f"'session':{SESSION!r},'model_sha256':{MODEL!r},'mode':'repeated',"
                "'positions':[{'name':'opening'}]}))\n"
                "time.sleep(2)\n"
            ),
        ]
    )
    try:
        result = _run_ready(writer.pid, ready)
    finally:
        writer.terminate()
        writer.wait(timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["ready"]["session"] == SESSION


def test_early_exit_and_timeout_fail_without_hanging(tmp_path):
    exited = subprocess.Popen([sys.executable, "-c", "pass"])
    exited.wait(timeout=10)
    ready = tmp_path / "missing.json"
    result = _run_ready(exited.pid, ready, timeout=1.0)
    assert result.returncode == 3
    assert "exited" in json.loads(result.stdout)["failures"][0]

    sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        result = _run_ready(sleeper.pid, ready, timeout=0.3)
    finally:
        sleeper.wait(timeout=10)
    assert result.returncode == 3
    payload = json.loads(result.stdout)
    assert any("within" in failure for failure in payload["failures"])
    assert payload["workload_terminated"] is True


def test_malformed_or_identity_mismatched_ready_is_rejected(tmp_path):
    ready = tmp_path / "ready.json"
    ready.write_text("not-json")
    sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        result = _run_ready(sleeper.pid, ready, timeout=0.2)
    finally:
        sleeper.wait(timeout=10)
    assert result.returncode == 3
