"""Final-session evidence reconciliation for schema-3 memory profiles."""

from __future__ import annotations

import sys
from pathlib import Path

SPIKE = Path(__file__).resolve().parents[1] / "spikes/browser_offline"
if str(SPIKE) not in sys.path:
    sys.path.insert(0, str(SPIKE))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import memory_validation as mv  # noqa: E402
from test_phase3c_workflow import _collect, _start_workload  # noqa: E402

A = "a" * 64
B = "b" * 64


def _collect_with(tmp_path, telemetry_args=(), profile="worker-replacement", **overrides):
    writer, telemetry, progress = _start_workload(
        tmp_path, telemetry_args=telemetry_args, duration=3.0
    )
    try:
        result, report = _collect(
            tmp_path,
            writer.pid,
            telemetry,
            progress,
            profile=profile,
            warmup=0.3,
            duration=0.8,
            replacement_model=B,
            **overrides,
        )
    finally:
        writer.terminate()
        writer.wait(timeout=10)
    return result, report


def test_valid_continued_work_is_reconciled_with_final_session(tmp_path):
    result, report = _collect_with(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    workload = report["workload_telemetry"]
    acceptance = workload["acceptance_window"]["counters"]
    final = workload["final_session"]
    assert final["workload_complete"] is True
    assert final["workload_exit_status"] == 0
    assert final["cleanup_complete"] is True
    assert final["counters"]["successful_searches"] >= acceptance["successful_searches"]
    observation = workload["process_observation"]
    assert observation["actual_process_exit_status"] == 0
    assert observation["actual_process_exit_status_source"] == "parent-wait"
    validation = mv.validate_report(report)
    assert validation["structural_ok"] is True, validation["failures"]
    assert validation["acceptance_ok"] is True, validation["partial_reasons"]


def test_failure_after_last_sample_is_not_accepted(tmp_path):
    result, report = _collect_with(tmp_path, telemetry_args=("--final-workload-failures", "1"))
    assert result.returncode == 0
    validation = mv.validate_report(report)
    assert validation["acceptance_ok"] is False
    assert any("after the last" in reason for reason in validation["partial_reasons"])


def test_missing_final_exit_status_is_not_accepted(tmp_path):
    result, report = _collect_with(tmp_path, telemetry_args=("--omit-final-exit",))
    assert result.returncode == 0
    assert report["workload_telemetry"]["final_session"]["workload_exit_status"] is None
    validation = mv.validate_report(report)
    assert validation["acceptance_ok"] is False
    assert any("exit status is missing" in reason for reason in validation["partial_reasons"])


def test_inconsistent_final_summary_is_invalid(tmp_path):
    result, report = _collect_with(tmp_path, telemetry_args=("--final-successful-offset", "1"))
    assert result.returncode == 0
    validation = mv.validate_report(report)
    assert validation["structural_ok"] is False
    assert any("successful searches" in failure for failure in validation["failures"])


def test_failed_cleanup_is_not_accepted(tmp_path):
    result, report = _collect_with(tmp_path, telemetry_args=("--cleanup-complete", "false"))
    assert result.returncode == 0
    validation = mv.validate_report(report)
    assert validation["acceptance_ok"] is False
    assert any("cleanup" in reason.lower() for reason in validation["partial_reasons"])


def test_final_model_transition_after_last_sample_is_recorded(tmp_path):
    result, report = _collect_with(
        tmp_path,
        telemetry_args=("--final-transition-to", B),
        profile="model-replacement",
    )
    assert result.returncode == 0
    workload = report["workload_telemetry"]
    assert workload["model_transitions"][-1]["to"] == B
    assert workload["model_transition_history"]["total"] == 1
    assert workload["final_session"]["model_sha256"] == B
    assert workload["final_session"]["after_last_sample"] is True
    validation = mv.validate_report(report)
    # The transition is recorded, but it is not silently credited as an
    # acceptance-window replacement event.
    assert not any("timeline" in failure for failure in validation["failures"])
    assert validation["acceptance_ok"] is False
    assert (
        any(
            "model replacement" in failure or "model replacement" in reason
            for failure, reason in [
                *((item, "") for item in validation["failures"]),
                *((item, "") for item in validation["partial_reasons"]),
            ]
        )
        or validation["structural_ok"] is False
    )


def test_missing_final_counter_is_invalid(tmp_path):
    result, report = _collect_with(tmp_path)
    assert result.returncode == 0
    report["workload_telemetry"]["final_session"]["counters"].pop("multi_request_batches")
    validation = mv.validate_report(report)
    assert validation["structural_ok"] is False
    assert any("multi_request_batches" in failure for failure in validation["failures"])


def test_final_counter_below_acceptance_window_is_invalid(tmp_path):
    result, report = _collect_with(tmp_path)
    assert result.returncode == 0
    final = report["workload_telemetry"]["final_session"]["counters"]
    acceptance = report["workload_telemetry"]["acceptance_window"]["counters"]
    final["successful_searches"] = max(0, acceptance["successful_searches"] - 1)
    validation = mv.validate_report(report)
    assert validation["structural_ok"] is False
    assert any("below the acceptance" in failure for failure in validation["failures"])
