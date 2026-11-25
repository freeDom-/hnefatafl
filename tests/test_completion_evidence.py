"""Parent-observed completion evidence and final-summary reconciliation."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SPIKE = Path(__file__).resolve().parents[1] / "spikes/browser_offline"
if str(SPIKE) not in sys.path:
    sys.path.insert(0, str(SPIKE))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import memory_validation as mv  # noqa: E402
from test_memory_validation import _schema3_report  # noqa: E402

FINALIZER = SPIKE / "finalize_workload_evidence.py"


def _parts(report=None):
    report = report or _schema3_report(profile="cancel-restart")
    final = report["workload_telemetry"]["final_session"]
    expected = report["expected_identity"]
    root = report["root_process"]
    summary = {
        "kind": "browser-sustained-workload",
        "schema": 2,
        "session_id": expected["session_id"],
        "mode": "cancel-restart",
        "runtime": "browser-pyodide",
        "model_sha256": final["model_sha256"],
        "workload": expected["workload"],
        "workload_complete": True,
        "cleanup_complete": True,
        "counters": dict(final["counters"]),
    }
    ownership = {
        "kind": "sustained-workload-ownership",
        "schema": 1,
        "pid": root["pid"],
        "identity": dict(root["identity"]),
        "session_id": expected["session_id"],
        "model_sha256": expected["model_sha256"],
        "mode": "cancel-restart",
        "captured_at_ms": 1.0,
    }
    return report, summary, ownership


def _attach(report, evidence, actual=0):
    report["completion_evidence"] = evidence
    report["workload_telemetry"]["process_observation"]["actual_process_exit_status"] = actual
    report["workload_telemetry"]["final_session"]["actual_process_exit_status"] = actual


def _cli(tmp_path, report, summary, ownership, *extra, output=None):
    profile_path = tmp_path / "profile.json"
    summary_path = tmp_path / "summary.json"
    ownership_path = tmp_path / "ownership.json"
    profile_path.write_text(json.dumps(report))
    summary_path.write_text(json.dumps(summary))
    ownership_path.write_text(json.dumps(ownership))
    output = output or tmp_path / "final.json"
    return subprocess.run(
        [
            sys.executable,
            str(FINALIZER),
            "--profile",
            str(profile_path),
            "--workload-summary",
            str(summary_path),
            "--ownership-file",
            str(ownership_path),
            *extra,
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    ), output


def test_successful_actual_and_reported_exit_status(tmp_path):
    report, summary, ownership = _parts()
    result, output = _cli(tmp_path, report, summary, ownership, "--exit-status", "0")
    assert result.returncode == 0, result.stdout + result.stderr
    finalized = json.loads(output.read_text())
    evidence = finalized["completion_evidence"]
    assert evidence["actual_process_exit_status"] == 0
    assert evidence["reported_exit_status"] == 0
    assert evidence["exit_status_consistent"] is True
    assert evidence["summary_reconciled"] is True
    assert len(evidence["summary_sha256"]) == 64
    assert mv.validate_report(finalized)["acceptance_ok"] is True


def test_missing_actual_process_exit_status_is_partial():
    report, _, _ = _parts()
    report.pop("completion_evidence")
    validation = mv.validate_report(report)
    assert validation["acceptance_ok"] is False
    assert any("parent-observed" in reason for reason in validation["partial_reasons"])


def test_nonzero_actual_exit_contradicts_successful_final_telemetry(tmp_path):
    report, summary, ownership = _parts()
    result, output = _cli(tmp_path, report, summary, ownership, "--exit-status", "1")
    assert result.returncode == 2
    assert not output.exists()
    assert "contradicts" in result.stdout


def test_missing_final_summary_file_is_rejected(tmp_path):
    report, summary, ownership = _parts()
    summary_path = tmp_path / "summary.json"
    result, output = _cli(tmp_path, report, summary, ownership, "--exit-status", "0")
    assert result.returncode == 0
    # The helper created the file; remove it and invoke the CLI directly.
    summary_path.unlink()
    profile_path = tmp_path / "profile.json"
    ownership_path = tmp_path / "ownership.json"
    profile_path.write_text(json.dumps(report))
    ownership_path.write_text(json.dumps(ownership))
    missing = subprocess.run(
        [
            sys.executable,
            str(FINALIZER),
            "--profile",
            str(profile_path),
            "--workload-summary",
            str(summary_path),
            "--ownership-file",
            str(ownership_path),
            "--exit-status",
            "0",
            "--output",
            str(tmp_path / "missing-final.json"),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert missing.returncode == 2
    assert "cannot read workload summary" in missing.stdout


def test_inconsistent_final_summary_is_rejected(tmp_path):
    report, summary, ownership = _parts()
    summary["counters"]["successful_searches"] += 1
    result, output = _cli(tmp_path, report, summary, ownership, "--exit-status", "0")
    assert result.returncode == 2
    assert not output.exists()
    assert "does not match final telemetry" in result.stdout


def test_final_summary_write_failure_is_reported(tmp_path):
    report, summary, ownership = _parts()
    parent = tmp_path / "not-a-directory"
    parent.write_text("file")
    result, output = _cli(
        tmp_path,
        report,
        summary,
        ownership,
        "--exit-status",
        "0",
        output=parent / "final.json",
    )
    assert result.returncode == 2
    assert not output.exists()
    assert (
        "cannot" in result.stdout
        or "Not a directory" in result.stdout
        or "File exists" in result.stdout
    )


def test_legitimate_work_after_measurement_window_is_reconciled(tmp_path):
    report, summary, ownership = _parts()
    final = report["workload_telemetry"]["final_session"]
    final["counters"]["completed_requests"] += 5
    final["counters"]["successful_searches"] += 5
    final["counters"]["neural_searches"] += 5
    summary["counters"] = dict(final["counters"])
    result, output = _cli(tmp_path, report, summary, ownership, "--exit-status", "0")
    assert result.returncode == 0, result.stdout + result.stderr
    assert mv.validate_report(json.loads(output.read_text()))["acceptance_ok"] is True
