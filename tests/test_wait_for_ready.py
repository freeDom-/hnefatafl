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


def _ownership_file(tmp_path, pid):
    if str(SPIKE) not in sys.path:
        sys.path.insert(0, str(SPIKE))
    import wait_for_ready as ready

    identity = ready._process_identity(pid)
    assert identity is not None
    document = ready._ownership_document(pid, identity, SESSION, MODEL, "repeated")
    path = tmp_path / "ownership.json"
    assert ready._write_ownership(path, document) is None
    return path


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


def test_terminate_only_cleans_owned_workload(tmp_path):
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    ownership = _ownership_file(tmp_path, process.pid)
    result = subprocess.run(
        [
            sys.executable,
            str(HELPER),
            "--terminate-only",
            "--ownership-file",
            str(ownership),
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


def test_terminate_only_after_original_exit_does_not_adopt_pid(tmp_path):
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    ownership = _ownership_file(tmp_path, process.pid)
    process.terminate()
    process.wait(timeout=10)
    for _ in range(2):
        result = subprocess.run(
            [
                sys.executable,
                str(HELPER),
                "--terminate-only",
                "--ownership-file",
                str(ownership),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert json.loads(result.stdout)["workload_terminated"] is True


def test_terminate_only_requires_valid_persisted_ownership(tmp_path):
    for content in (None, "not-json", "[]", '{"kind":"wrong"}', "{}"):
        path = tmp_path / "ownership.json"
        if content is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(content)
        result = subprocess.run(
            [
                sys.executable,
                str(HELPER),
                "--terminate-only",
                "--ownership-file",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 3
        assert "ownership" in result.stdout.lower()


def test_terminate_only_rejects_contradictory_pid_and_identity(tmp_path):
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    ownership = _ownership_file(tmp_path, process.pid)
    try:
        result = subprocess.run(
            [
                sys.executable,
                str(HELPER),
                "--terminate-only",
                "--ownership-file",
                str(ownership),
                "--pid",
                str(process.pid + 1),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 3
        assert "contradicts" in result.stdout
        assert process.poll() is None
    finally:
        process.terminate()
        process.wait(timeout=10)


def test_malformed_ownership_is_never_signalled(tmp_path, monkeypatch):
    if str(SPIKE) not in sys.path:
        sys.path.insert(0, str(SPIKE))
    import wait_for_ready as ready

    identity = {"pid": 9999, "start_time": "10", "cmdline_sha256": "a" * 64}
    calls = []
    monkeypatch.setattr(ready, "_process_identity", lambda pid: identity)
    monkeypatch.setattr(ready, "_signal_verified", lambda *args: calls.append(args) or True)
    path = tmp_path / "ownership.json"
    path.write_text('{"kind":"sustained-workload-ownership","schema":1,"pid":9999}')
    document, error = ready._read_ownership(path)
    assert document is None
    assert error
    assert calls == []


def test_descendant_identity_reuse_is_reported_not_signalled(monkeypatch):
    if str(SPIKE) not in sys.path:
        sys.path.insert(0, str(SPIKE))
    import wait_for_ready as ready

    root = {"pid": 100, "start_time": "10", "cmdline_sha256": "a" * 64}
    child = {"pid": 101, "start_time": "11", "cmdline_sha256": "b" * 64}
    child_reused = {"pid": 101, "start_time": "99", "cmdline_sha256": "c" * 64}
    current = {"root": root, "child": child}

    def identity(pid):
        return current["root"] if pid == 100 else current["child"]

    signals = []
    monkeypatch.setattr(ready, "_process_identity", identity)
    monkeypatch.setattr(ready, "_descendants", lambda pid: [(101, child, 100)])
    monkeypatch.setattr(ready, "_child_relationship_valid", lambda pid, parent: True)
    monkeypatch.setattr(ready.os, "kill", lambda pid, sig: signals.append((pid, sig)))
    if hasattr(ready.os, "pidfd_open"):
        monkeypatch.setattr(
            ready.os,
            "pidfd_open",
            lambda pid: (_ for _ in ()).throw(OSError("unavailable")),
        )
    current["child"] = child_reused
    result = ready._terminate(100, root)
    assert all(pid != 101 for pid, _ in signals)
    assert any(
        "descendant 101 identity changed" in failure for failure in result["cleanup_failures"]
    )
