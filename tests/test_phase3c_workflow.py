"""Collect-to-validate structural integration for the Phase 3C-A platform.

These exercise the public CLIs an owner runs, not only imported helpers.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SPIKE = Path(__file__).resolve().parents[1] / "spikes/browser_offline"


def _config(tmp_path: Path, label: str) -> Path:
    path = tmp_path / f"{label}.json"
    path.write_text(
        json.dumps(
            {
                "label": label,
                "kind": "scripted",
                "runtime": "native-cpython",
                "provider": "cpu",
                "algorithm": "scripted",
                "model": None,
            }
        )
    )
    return path


def test_tournament_run_cli_collects_and_validates_a_structural_report(tmp_path):
    agent = SPIKE / "native_tournament_agent.py"
    output = tmp_path / "report.json"
    result = subprocess.run(
        [
            sys.executable,
            str(SPIKE / "tournament.py"),
            "--run",
            "--ruleset",
            "brandubh",
            "--limit",
            "1",
            "--simulations",
            "4",
            "--time-limit",
            "0.2",
            "--seed",
            "3",
            "--max-plies",
            "6",
            "--agent-timeout",
            "5",
            "--agent-a-command",
            f"{sys.executable} {agent} --engine scripted",
            "--agent-b-command",
            f"{sys.executable} {agent} --engine scripted",
            "--agent-a-config",
            str(_config(tmp_path, "scripted-a")),
            "--agent-b-config",
            str(_config(tmp_path, "scripted-b")),
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    summary = json.loads(result.stdout)
    assert summary["ok"] is True
    assert summary["structural_only"] is True
    assert summary["playing_strength_acceptance"] is False
    report = json.loads(output.read_text())
    assert report["schema"] == tournament_schema()
    assert report["playing_strength"]["status"] == "blocked"
    assert report["summary"]["games"] == 2

    validated = subprocess.run(
        [
            sys.executable,
            str(SPIKE / "tournament.py"),
            str(output),
            "--output",
            str(tmp_path / "validation.json"),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert validated.returncode == 0, validated.stdout + validated.stderr
    assert json.loads(validated.stdout)["playing_strength_acceptance"] is False


def test_tournament_run_cli_reports_malformed_openings_files(tmp_path):
    openings = tmp_path / "openings.json"
    openings.write_text("{not json")
    result = subprocess.run(
        [
            sys.executable,
            str(SPIKE / "tournament.py"),
            "--run",
            "--openings-file",
            str(openings),
            "--agent-a-command",
            f"{sys.executable} {SPIKE / 'native_tournament_agent.py'}",
            "--agent-b-command",
            f"{sys.executable} {SPIKE / 'native_tournament_agent.py'}",
            "--agent-a-config",
            str(_config(tmp_path, "scripted-a")),
            "--agent-b-config",
            str(_config(tmp_path, "scripted-b")),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert json.loads(result.stdout)["ok"] is False


def tournament_schema():
    import sys

    if str(SPIKE) not in sys.path:
        sys.path.insert(0, str(SPIKE))
    import tournament

    return tournament.SCHEMA


def _start_workload(tmp_path, *, telemetry_args=(), duration=3.0):
    import subprocess as sp

    telemetry = tmp_path / "telemetry.jsonl"
    progress = tmp_path / "progress.txt"
    progress.write_text("0")
    writer = sp.Popen(
        [
            sys.executable,
            str(Path(__file__).resolve().parent / "sustained_telemetry_writer.py"),
            "--telemetry",
            str(telemetry),
            "--progress",
            str(progress),
            "--duration",
            str(duration),
            *telemetry_args,
        ]
    )
    return writer, telemetry, progress


def _collect(tmp_path, workload_pid, telemetry, progress, **overrides):
    import subprocess as sp

    output = tmp_path / "profile.json"
    command = [
        sys.executable,
        str(SPIKE / "memory_profile_collect.py"),
        "--pid",
        str(workload_pid),
        "--profile",
        overrides.get("profile", "worker-replacement"),
        "--warmup-duration",
        str(overrides.get("warmup", 0.6)),
        "--duration",
        str(overrides.get("duration", 1.0)),
        "--interval",
        str(overrides.get("interval", 0.1)),
        "--telemetry-file",
        str(telemetry),
        "--progress-file",
        str(progress),
        "--telemetry-session-id",
        overrides.get("session", "session-a"),
        "--telemetry-model-sha256",
        "a" * 64,
        "--telemetry-workload",
        "brandubh-opening",
        "--inference-cache",
        "--output",
        str(output),
    ]
    result = sp.run(command, capture_output=True, text=True, timeout=60)
    return result, json.loads(output.read_text()) if output.exists() else None


def test_live_memory_collection_separates_warmup_and_acceptance(tmp_path):
    import sys as _sys

    writer, telemetry, progress = _start_workload(tmp_path)
    try:
        result, report = _collect(
            tmp_path,
            writer.pid,
            telemetry,
            progress,
            warmup=0.6,
            duration=1.0,
            interval=0.1,
        )
    finally:
        writer.terminate()
        writer.wait(timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    assert report["schema"] == 3
    assert report["warmup_ms"] == 600
    assert report["duration_ms"] == 1000
    assert report["total_duration_ms"] >= 1500
    measured = [s for s in report["samples"] if s["timestamp_ms"] >= 600]
    assert len(measured) >= 8
    assert measured[-1]["timestamp_ms"] - measured[0]["timestamp_ms"] >= 850
    assert measured[-1]["timestamp_ms"] >= 1500
    with_telemetry = [s for s in measured if s.get("telemetry")]
    assert with_telemetry
    for sample in with_telemetry:
        assert sample["telemetry"]["session_id"] == "session-a"
        assert sample["workers"] == 3
        assert sample["os_processes"] >= 1
        assert sample["os_processes"] != sample["workers"]
        assert sample["telemetry_lag_ms"] < 2000
    assert report["events"]["worker_replacements"] >= 1
    assert report["events_source"] == "telemetry"
    assert report["features"]["inference_cache"] is True

    if str(SPIKE) not in _sys.path:
        _sys.path.insert(0, str(SPIKE))
    import memory_validation as mv

    validation = mv.validate_report(report)
    assert validation["structural_ok"] is True, validation["failures"]
    assert validation["acceptance_ok"] is True, validation["partial_reasons"]


def test_collector_detects_telemetry_identity_change(tmp_path):
    writer, telemetry, progress = _start_workload(tmp_path, telemetry_args=("--identity-change",))
    try:
        result, report = _collect(
            tmp_path,
            writer.pid,
            telemetry,
            progress,
            warmup=0.3,
            duration=0.8,
        )
    finally:
        writer.terminate()
        writer.wait(timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    assert report["telemetry"]["identity_mismatch"] >= 1
    measured = [s for s in report["samples"] if s["timestamp_ms"] >= 300]
    assert any(s.get("workers") is None for s in measured)
    assert any(
        "identity" in (s.get("telemetry_unavailable_reason") or "")
        or "telemetry" in (s.get("telemetry_unavailable_reason") or "")
        for s in measured
    )
    import sys as _sys

    if str(SPIKE) not in _sys.path:
        _sys.path.insert(0, str(SPIKE))
    import memory_validation as mv

    validation = mv.validate_report(report)
    assert validation["acceptance_ok"] is False
    assert any("telemetry" in failure for failure in validation["failures"])


def test_collector_reports_process_disappearance_as_blocked(tmp_path):
    import subprocess as sp
    import time

    process = sp.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    progress = tmp_path / "progress.txt"
    progress.write_text("0")
    telemetry = tmp_path / "telemetry.jsonl"
    telemetry.write_text("")
    output = tmp_path / "profile.json"
    command = [
        sys.executable,
        str(SPIKE / "memory_profile_collect.py"),
        "--pid",
        str(process.pid),
        "--profile",
        "worker-replacement",
        "--warmup-duration",
        "0.2",
        "--duration",
        "2.0",
        "--interval",
        "0.05",
        "--telemetry-file",
        str(telemetry),
        "--progress-file",
        str(progress),
        "--output",
        str(output),
    ]
    collector = sp.Popen(command, stdout=sp.PIPE, stderr=sp.PIPE, text=True)
    time.sleep(1.2)
    process.terminate()
    process.wait(timeout=10)
    stdout, stderr = collector.communicate(timeout=30)
    assert collector.returncode == 2, stdout + stderr
    report = json.loads(output.read_text())
    assert report["status"] == "blocked"
    assert any("disappeared" in reason for reason in report["blocked_reasons"])


def test_collector_detects_stale_and_missing_telemetry(tmp_path):
    import time

    if str(SPIKE) not in sys.path:
        sys.path.insert(0, str(SPIKE))
    import memory_profile_collect as mpc

    telemetry = tmp_path / "telemetry.jsonl"
    telemetry.write_text(
        json.dumps(
            {
                "timestamp_ms": (time.time() - 60) * 1000.0,
                "session_id": "s",
                "model_sha256": "a" * 64,
                "workload": "w",
                "workers": 2,
            }
        )
        + "\n"
    )
    stream = mpc.TelemetryStream(telemetry, stale_after_ms=1000)
    record, reason = stream.latest(time.time() * 1000.0)
    assert record is None
    assert "stale" in reason

    missing = mpc.TelemetryStream(tmp_path / "missing.jsonl", stale_after_ms=1000)
    record, reason = missing.latest(time.time() * 1000.0)
    assert record is None
    assert "no usable telemetry" in reason


def test_collector_detects_root_process_identity_replacement(monkeypatch):
    import os
    from types import SimpleNamespace

    if str(SPIKE) not in sys.path:
        sys.path.insert(0, str(SPIKE))
    import memory_profile_collect as mpc

    calls = {"count": 0}

    def identity(pid):
        calls["count"] += 1
        return {
            "pid": pid,
            "start_time": str(calls["count"] // 2),
            "cmdline_sha256": "a" * 64,
        }

    monkeypatch.setattr(mpc, "_process_identity", identity)
    monkeypatch.setattr(
        mpc,
        "_process_tree",
        lambda pid: ({pid: {"rss": 1024 * 1024, "ppid": 0}}, None),
    )
    args = SimpleNamespace(
        pid=os.getpid(),
        profile="worker-replacement",
        warmup_duration=0.0,
        duration=0.5,
        interval=0.05,
        progress_file=None,
        telemetry_file=None,
        telemetry_session_id=None,
        telemetry_model_sha256=None,
        telemetry_workload=None,
        telemetry_stale_ms=1000.0,
        inference_cache=False,
        max_process_rss_bytes=1024**3,
        max_js_heap_bytes=1024**3,
        max_cache_bytes=1024**3,
        max_workers=8,
        max_wasm_heap_bytes=None,
    )
    report = mpc.collect(args)
    assert report["status"] == "blocked"
    assert any("identity changed" in reason for reason in report["blocked_reasons"])


def test_telemetry_missing_identity_fields_after_establishment(tmp_path):
    writer, telemetry, progress = _start_workload(
        tmp_path, telemetry_args=("--drop-identity-after", "8"), duration=2.0
    )
    try:
        result, report = _collect(
            tmp_path,
            writer.pid,
            telemetry,
            progress,
            warmup=0.4,
            duration=0.8,
        )
    finally:
        writer.terminate()
        writer.wait(timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    assert report["telemetry"]["identity_mismatch"] >= 1
    measured = [s for s in report["samples"] if s["timestamp_ms"] >= 400]
    assert any(s.get("completed_requests_source") == "progress-file" for s in measured)
    assert report["progress_consistency"]["verified"] is False

    if str(SPIKE) not in sys.path:
        sys.path.insert(0, str(SPIKE))
    import memory_validation as mv

    validation = mv.validate_report(report)
    assert validation["acceptance_ok"] is False


def test_telemetry_progress_from_mismatched_session_is_not_accepted(tmp_path):
    writer, telemetry, progress = _start_workload(
        tmp_path, telemetry_args=("--identity-change", "--identity-change-at", "5"), duration=2.0
    )
    try:
        result, report = _collect(
            tmp_path,
            writer.pid,
            telemetry,
            progress,
            warmup=0.3,
            duration=0.8,
        )
    finally:
        writer.terminate()
        writer.wait(timeout=10)
    assert result.returncode == 0
    assert report["telemetry"]["identity_mismatch"] >= 1
    assert report["progress_consistency"]["verified"] is False
    import memory_validation as mv

    validation = mv.validate_report(report)
    assert validation["acceptance_ok"] is False


def test_lifecycle_events_are_restricted_to_the_acceptance_window(tmp_path):
    writer, telemetry, progress = _start_workload(
        tmp_path, telemetry_args=("--events-stop-at", "2"), duration=2.5
    )
    try:
        result, report = _collect(
            tmp_path,
            writer.pid,
            telemetry,
            progress,
            warmup=0.7,
            duration=0.8,
        )
    finally:
        writer.terminate()
        writer.wait(timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    # Events incremented only during warmup; the acceptance window observed none.
    assert report["events"].get("worker_replacements", 0) == 0
    import memory_validation as mv

    validation = mv.validate_report(report)
    assert validation["structural_ok"] is False
    assert any("worker_replacements" in failure for failure in validation["failures"])


def test_progress_counter_reset_blocks_acceptance(tmp_path):
    writer, telemetry, progress = _start_workload(
        tmp_path, telemetry_args=("--progress-reset-at", "8"), duration=2.5
    )
    try:
        result, report = _collect(
            tmp_path,
            writer.pid,
            telemetry,
            progress,
            warmup=0.3,
            duration=1.0,
        )
    finally:
        writer.terminate()
        writer.wait(timeout=10)
    assert result.returncode == 2
    assert report["status"] == "blocked"
    assert any("counter reset" in reason for reason in report["blocked_reasons"])


def test_long_telemetry_stream_retains_bounded_history(tmp_path):
    import json
    import time

    if str(SPIKE) not in sys.path:
        sys.path.insert(0, str(SPIKE))
    import memory_profile_collect as mpc

    telemetry = tmp_path / "long.jsonl"
    with telemetry.open("w", encoding="utf-8") as handle:
        for index in range(5000):
            handle.write(
                json.dumps(
                    {
                        "timestamp_ms": time.time() * 1000.0 + index,
                        "session_id": "s",
                        "model_sha256": "a" * 64,
                        "workload": "w",
                        "completed_requests": index,
                        "workers": 2,
                    }
                )
                + "\n"
            )
    stream = mpc.TelemetryStream(telemetry, stale_after_ms=5000)
    stream.poll()
    diagnostics = stream.diagnostics()
    assert diagnostics["records"] <= diagnostics["history_limit"]
    assert diagnostics["history_limit"] == mpc.TELEMETRY_HISTORY_LIMIT
    assert diagnostics["malformed"] == 0
    record, reason = stream.latest(time.time() * 1000.0 + 5000)
    assert record is not None
    assert reason is None


def test_lifecycle_events_survive_history_eviction(tmp_path):
    import json
    import time

    if str(SPIKE) not in sys.path:
        sys.path.insert(0, str(SPIKE))
    import memory_profile_collect as mpc

    telemetry = tmp_path / "long-events.jsonl"
    base = time.time() * 1000.0
    with telemetry.open("w", encoding="utf-8") as handle:
        for index in range(5000):
            handle.write(
                json.dumps(
                    {
                        "timestamp_ms": base + index,
                        "session_id": "s",
                        "model_sha256": "a" * 64,
                        "workload": "w",
                        "workers": 2,
                        "completed_requests": index,
                        "events": {
                            "worker_replacements": (1 if index >= 100 else 0)
                            + (1 if index >= 4500 else 0)
                        },
                    }
                )
                + "\n"
            )
    stream = mpc.TelemetryStream(telemetry, stale_after_ms=10000)
    stream.set_acceptance_window(base, base + 5000)
    stream.poll()
    assert len(stream.records) <= mpc.TELEMETRY_HISTORY_LIMIT
    assert stream.events_in_window(base, base + 5000)["worker_replacements"] == 2
    assert stream.event_resets == 0


def test_acceptance_window_events_ignore_warmup_and_event_resets(tmp_path):
    import json
    import time

    if str(SPIKE) not in sys.path:
        sys.path.insert(0, str(SPIKE))
    import memory_profile_collect as mpc

    telemetry = tmp_path / "window-events.jsonl"
    base = time.time() * 1000.0
    records = [
        (base + 100, 5),
        (base + 900, 5),
        (base + 1100, 5),
        (base + 1200, 6),
        (base + 1300, 8),
        (base + 1400, 2),
        (base + 1500, 3),
    ]
    with telemetry.open("w", encoding="utf-8") as handle:
        for timestamp, value in records:
            handle.write(
                json.dumps(
                    {
                        "timestamp_ms": timestamp,
                        "session_id": "s",
                        "model_sha256": "a" * 64,
                        "workload": "w",
                        "workers": 2,
                        "completed_requests": 0,
                        "events": {"worker_replacements": value},
                    }
                )
                + "\n"
            )
    stream = mpc.TelemetryStream(telemetry, stale_after_ms=10000)
    stream.set_acceptance_window(base + 1000, base + 2000)
    stream.poll()
    # Warmup record is excluded; 5->6 adds 1, 6->8 adds 2, reset to 2 adds
    # nothing, and 2->3 adds 1.
    assert stream.events_in_window(base + 1000, base + 2000)["worker_replacements"] == 4
    assert stream.event_resets == 1


def test_initial_mismatched_record_does_not_block_later_expected_session(tmp_path):
    import json
    import time

    if str(SPIKE) not in sys.path:
        sys.path.insert(0, str(SPIKE))
    import memory_profile_collect as mpc

    telemetry = tmp_path / "mismatch-then-valid.jsonl"
    base = time.time() * 1000.0
    with telemetry.open("w", encoding="utf-8") as handle:
        handle.write(
            json.dumps(
                {
                    "timestamp_ms": base,
                    "session_id": "wrong-session",
                    "model_sha256": "b" * 64,
                    "workload": "w",
                    "workers": 1,
                }
            )
            + "\n"
        )
        handle.write(
            json.dumps(
                {
                    "timestamp_ms": base + 100,
                    "session_id": "right-session",
                    "model_sha256": "a" * 64,
                    "workload": "w",
                    "workers": 2,
                }
            )
            + "\n"
        )
    stream = mpc.TelemetryStream(
        telemetry,
        stale_after_ms=10000,
        expected={
            "session_id": "right-session",
            "model_sha256": "a" * 64,
            "workload": "w",
        },
    )
    stream.poll()
    record, reason = stream.latest(base + 200)
    assert record is not None, reason
    assert record["session_id"] == "right-session"
    assert stream.identity["session_id"] == "right-session"
    assert stream.identity_mismatch == 1
