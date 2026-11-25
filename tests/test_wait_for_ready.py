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


def test_invalid_pid_and_timeout_are_rejected_before_signalling(tmp_path):
    ready = tmp_path / "ready.json"
    ready.write_text("{}")
    for pid, timeout in ((0, "1"), (-5, "1"), (123, "nan"), (123, "inf"), (123, "0")):
        result = subprocess.run(
            [
                sys.executable,
                str(HELPER),
                "--ready",
                str(ready),
                "--pid",
                str(pid),
                "--timeout",
                timeout,
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
        assert result.returncode == 2
        assert "Traceback" not in result.stderr


def test_recycled_pid_identity_is_never_signalled(monkeypatch):
    if str(SPIKE) not in sys.path:
        sys.path.insert(0, str(SPIKE))
    import wait_for_ready as ready

    original = {
        "pid": 4242,
        "start_time": "100",
        "cmdline_sha256": "a" * 64,
    }
    recycled = {
        "pid": 4242,
        "start_time": "200",
        "cmdline_sha256": "b" * 64,
    }
    calls = {"identity": 0}

    def identity(pid):
        calls["identity"] += 1
        return original if calls["identity"] == 1 else recycled

    signals = []
    monkeypatch.setattr(ready, "_process_identity", identity)
    monkeypatch.setattr(ready, "_descendants", lambda pid: [])
    monkeypatch.setattr(ready.os, "kill", lambda pid, sig: signals.append((pid, sig)))
    if hasattr(ready.os, "pidfd_open"):
        monkeypatch.setattr(
            ready.os, "pidfd_open", lambda pid: (_ for _ in ()).throw(OSError("unavailable"))
        )
    result = ready._terminate(4242, original)
    assert signals == []
    assert result["terminated"] is False
    assert any("identity changed" in failure for failure in result["cleanup_failures"])


def test_owned_descendants_are_cleaned_up(tmp_path):
    import time

    ready = tmp_path / "ready.json"
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); time.sleep(30)",
        ]
    )
    try:
        time.sleep(0.3)
        proc = subprocess.run(
            [
                sys.executable,
                str(HELPER),
                "--ready",
                str(ready),
                "--pid",
                str(child.pid),
                "--timeout",
                "0.2",
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
        assert proc.returncode == 3
        payload = json.loads(proc.stdout)
        assert payload["workload_terminated"] is True
        assert payload["cleanup_failures"] == []
    finally:
        child.terminate()
        child.wait(timeout=10)


def test_terminate_only_cleans_owned_workload():
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    result = subprocess.run(
        [
            sys.executable,
            str(HELPER),
            "--pid",
            str(process.pid),
            "--terminate-only",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    try:
        process.wait(timeout=10)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["workload_terminated"] is True
