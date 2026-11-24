"""Sustained memory profile validator: coverage, progress and acceptance."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SPIKE = Path(__file__).resolve().parents[1] / "spikes/browser_offline"
if str(SPIKE) not in sys.path:
    sys.path.insert(0, str(SPIKE))

import memory_validation as mv  # noqa: E402


def _report(profile="sustained-10m", samples=12, duration_ms=600000):
    report = {
        "kind": "browser-analysis-memory-profile",
        "schema": 2,
        "profile": profile,
        "status": "measured",
        "duration_ms": duration_ms,
        "budgets": {
            "max_process_rss_bytes": 512 * 1024 * 1024,
            "max_js_heap_used_bytes": 256 * 1024 * 1024,
            "max_cache_bytes": 16 * 1024 * 1024,
            "max_wasm_heap_capacity_bytes": 64 * 1024 * 1024,
            "max_workers": 2,
        },
        "warmup_samples": 1,
        "samples": [
            {
                "timestamp_ms": index * 60000,
                "process_rss_bytes": 100 * 1024 * 1024 + index * 1024,
                "js_heap_used_bytes": 20 * 1024 * 1024,
                "wasm_heap_capacity_bytes": 32 * 1024 * 1024,
                "cache_entries": 10,
                "cache_bytes": 1000,
                "workers": 2,
                "completed_requests": index,
            }
            for index in range(samples)
        ],
    }
    if profile == "cancel-restart":
        report["events"] = {"cancellations": 2}
    if profile == "model-reload":
        report["events"] = {"reloads": 1}
    if profile == "worker-replacement":
        report["events"] = {"worker_replacements": 1}
    return report


def test_valid_measured_profile_is_accepted():
    result = mv.validate_report(_report())
    assert result["structural_ok"] is True, result["failures"]
    assert result["measurement_ok"] is True
    assert result["acceptance_ok"] is True
    assert result["status"] == "accepted"


def test_blocked_profile_is_explicit_and_cannot_be_accepted():
    report = {
        "kind": "browser-analysis-memory-profile",
        "schema": 2,
        "profile": "sustained-30m",
        "status": "blocked",
        "blocked_reasons": ["no 30-minute window"],
    }
    result = mv.validate_report(report)
    assert result["structural_ok"] is True
    assert result["acceptance_ok"] is False
    assert result["status"] == "blocked"


def test_blocked_profile_cli_exit_code_is_not_success(tmp_path):
    report = {
        "kind": "browser-analysis-memory-profile",
        "schema": 2,
        "profile": "sustained-30m",
        "status": "blocked",
        "blocked_reasons": ["no 30-minute window"],
    }
    path = tmp_path / "blocked.json"
    path.write_text(json.dumps(report))
    result = subprocess.run(
        [sys.executable, str(SPIKE / "memory_validation.py"), str(path)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 2
    assert json.loads(result.stdout)["acceptance_ok"] is False


def test_one_valid_rss_sample_is_insufficient_for_acceptance():
    report = _report()
    for index, sample in enumerate(report["samples"]):
        sample["process_rss_bytes"] = None
        sample["rss_unavailable_reason"] = "process metrics were not exposed"
    report["samples"][5]["process_rss_bytes"] = 100 * 1024 * 1024
    report["unavailable_metrics"] = {
        "process_rss_bytes": "only one process RSS observation was exposed"
    }
    result = mv.validate_report(report)
    assert result["structural_ok"] is True, result["failures"]
    assert result["acceptance_ok"] is False
    assert any("RSS" in reason for reason in result["partial_reasons"])


def test_missing_js_heap_and_cache_series_require_explanation():
    report = _report()
    for sample in report["samples"]:
        sample.pop("js_heap_used_bytes")
        sample.pop("cache_bytes")
        sample.pop("cache_entries")
    result = mv.validate_report(report)
    assert result["structural_ok"] is False
    assert any("js_heap_used_bytes" in failure for failure in result["failures"])

    report = _report()
    for sample in report["samples"]:
        sample.pop("js_heap_used_bytes")
        sample.pop("cache_bytes")
        sample.pop("cache_entries")
    report["unavailable_metrics"] = {
        "js_heap_used_bytes": "performance.memory is unavailable in headless Chromium",
        "cache_bytes": "inference cache was not enabled",
    }
    result = mv.validate_report(report)
    assert result["structural_ok"] is True
    assert result["measurement_ok"] is False
    assert result["acceptance_ok"] is False


def test_unavailable_rss_must_be_explained_and_not_zero_substituted():
    report = _report()
    for sample in report["samples"]:
        sample["process_rss_bytes"] = None
    result = mv.validate_report(report)
    assert result["structural_ok"] is False
    assert any("RSS" in failure for failure in result["failures"])
    for sample in report["samples"]:
        sample["rss_unavailable_reason"] = "playwright did not expose process RSS"
    report["rss_acceptance_unavailable_reason"] = "process RSS not exposed by harness"
    structural = mv.validate_report(report)
    assert structural["structural_ok"] is True
    assert structural["acceptance_ok"] is False
    report.pop("rss_acceptance_unavailable_reason")
    assert mv.validate_report(report)["structural_ok"] is False


def test_excessive_metric_gaps_are_rejected():
    report = _report()
    base = report["samples"][5]["timestamp_ms"]
    for index in range(6, len(report["samples"])):
        report["samples"][index]["timestamp_ms"] = base + 120000 + (index - 6) * 60000
    result = mv.validate_report(report)
    assert result["structural_ok"] is False
    assert any("gap" in failure for failure in result["failures"])


def test_idle_workload_is_rejected_even_with_a_brief_activity_event():
    report = _report()
    for index, sample in enumerate(report["samples"]):
        sample["completed_requests"] = 1 if index >= 1 else 0
    result = mv.validate_report(report)
    assert result["structural_ok"] is False
    assert any("brief workload activity" in failure for failure in result["failures"])

    report = _report()
    for sample in report["samples"]:
        sample["completed_requests"] = 0
    result = mv.validate_report(report)
    assert result["structural_ok"] is False


def test_activity_confined_to_one_half_is_rejected():
    report = _report()
    for index, sample in enumerate(report["samples"]):
        sample["completed_requests"] = index if index <= 6 else 6
    result = mv.validate_report(report)
    assert result["structural_ok"] is False
    assert any("not distributed" in failure for failure in result["failures"])


def test_zero_baseline_growth_is_evaluated():
    report = _report()
    report["samples"][0]["process_rss_bytes"] = 0
    for index, sample in enumerate(report["samples"]):
        sample["process_rss_bytes"] = index * 400 * 1024
    result = mv.validate_report(report)
    assert result["structural_ok"] is False
    assert any("grew" in failure for failure in result["failures"])


def test_worker_and_cache_budgets_are_enforced():
    report = _report()
    report["samples"][4]["workers"] = 9
    result = mv.validate_report(report)
    assert result["structural_ok"] is False
    assert any("worker budget" in failure for failure in result["failures"])

    report = _report()
    report["samples"][4]["cache_bytes"] = 32 * 1024 * 1024
    result = mv.validate_report(report)
    assert result["structural_ok"] is False
    assert any("cache_bytes exceeds" in failure for failure in result["failures"])

    report = _report()
    report["samples"][4]["wasm_heap_capacity_bytes"] = 128 * 1024 * 1024
    result = mv.validate_report(report)
    assert result["structural_ok"] is False
    assert any("wasm_heap_capacity_bytes exceeds" in failure for failure in result["failures"])


def test_profile_specific_lifecycle_requirements_are_enforced():
    report = _report("worker-replacement")
    assert mv.validate_report(report)["structural_ok"] is True
    report.pop("events")
    result = mv.validate_report(report)
    assert result["structural_ok"] is False
    assert any("worker_replacements" in failure for failure in result["failures"])


def test_growth_and_budget_are_rejected():
    report = _report()
    report["samples"][-1]["process_rss_bytes"] = 600 * 1024 * 1024
    result = mv.validate_report(report)
    assert result["ok"] is False
    assert any("budget" in failure or "grew" in failure for failure in result["failures"])


def test_timestamps_and_counters_are_validated():
    report = _report()
    report["samples"][2]["timestamp_ms"] = report["samples"][1]["timestamp_ms"]
    report["samples"][3]["cache_entries"] = -1
    result = mv.validate_report(report)
    assert result["ok"] is False
    assert any("strictly increasing" in failure for failure in result["failures"])
    assert any("cache_entries" in failure for failure in result["failures"])

    for invalid in (True, "17", float("nan"), float("inf")):
        report = _report()
        report["samples"][3]["process_rss_bytes"] = invalid
        assert mv.validate_report(report)["ok"] is False, invalid


def test_short_duration_and_insufficient_warmup_are_rejected():
    report = _report()
    report["duration_ms"] = 300000
    assert mv.validate_report(report)["ok"] is False

    report = _report()
    report["warmup_samples"] = len(report["samples"])
    assert mv.validate_report(report)["ok"] is False


def test_positive_observable_budgets_are_required():
    report = _report()
    report["budgets"]["max_js_heap_used_bytes"] = None
    assert mv.validate_report(report)["ok"] is False

    report = _report()
    report["budgets"].pop("max_workers")
    assert mv.validate_report(report)["ok"] is False


def test_malformed_reports_are_structured(tmp_path):
    raw = tmp_path / "raw.json"
    raw.write_text("[1, 2, 3]")
    result = subprocess.run(
        [sys.executable, str(SPIKE / "memory_validation.py"), str(raw)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert json.loads(result.stdout)["ok"] is False

    for malformed in (
        {
            "kind": "browser-analysis-memory-profile",
            "schema": 2,
            "profile": [],
            "status": "measured",
        },
        {
            "kind": "browser-analysis-memory-profile",
            "schema": 2,
            "profile": "sustained-10m",
            "status": "measured",
            "budgets": [],
            "samples": [],
        },
        {
            "kind": "browser-analysis-memory-profile",
            "schema": 2,
            "profile": "sustained-10m",
            "status": "measured",
            "samples": [{"timestamp_ms": 0, "process_rss_bytes": "big", "workers": True}],
        },
    ):
        assert mv.validate_report(malformed)["ok"] is False


def _schema3_report(**overrides):
    report = {
        "kind": "browser-analysis-memory-profile",
        "schema": 3,
        "profile": "cancel-restart",
        "status": "measured",
        "warmup_ms": 60000,
        "duration_ms": 120000,
        "total_duration_ms": 180000,
        "budgets": {
            "max_process_rss_bytes": 512 * 1024 * 1024,
            "max_js_heap_used_bytes": 256 * 1024 * 1024,
            "max_cache_bytes": 16 * 1024 * 1024,
            "max_wasm_heap_capacity_bytes": 64 * 1024 * 1024,
            "max_workers": 8,
        },
        "features": {"inference_cache": True},
        "events": {"cancellations": 1},
        "events_source": "telemetry",
        "samples": [
            {
                "timestamp_ms": index * 12000,
                "process_rss_bytes": 100 * 1024 * 1024 + index * 1024,
                "js_heap_used_bytes": 20 * 1024 * 1024,
                "wasm_heap_capacity_bytes": 32 * 1024 * 1024,
                "cache_entries": 10,
                "cache_bytes": 1000,
                "workers": 3,
                "os_processes": 9,
                "completed_requests": index,
            }
            for index in range(16)
        ],
    }
    report.update(overrides)
    return report


def test_warmup_window_is_excluded_by_timestamp():
    report = _schema3_report()
    assert mv.validate_report(report)["acceptance_ok"] is True
    # Only the warmup samples carry RSS: the acceptance window is unobservable.
    for sample in report["samples"]:
        if sample["timestamp_ms"] >= 60000:
            sample["process_rss_bytes"] = None
            sample["rss_unavailable_reason"] = "not sampled after warmup"
    report["unavailable_metrics"] = {"process_rss_bytes": "warmup-only RSS"}
    result = mv.validate_report(report)
    assert result["structural_ok"] is True
    assert result["acceptance_ok"] is False


def test_acceptance_window_must_be_complete():
    report = _schema3_report()
    for index, sample in enumerate(report["samples"]):
        sample["timestamp_ms"] = index * 7000
        sample["completed_requests"] = index
    result = mv.validate_report(report)
    assert result["structural_ok"] is False
    assert any(
        "ends before" in failure or "do not cover" in failure for failure in result["failures"]
    )

    report = _schema3_report()
    report["duration_ms"] = 160000
    assert mv.validate_report(report)["structural_ok"] is False


def test_per_metric_coverage_and_longest_gap_are_enforced():
    report = _schema3_report()
    for index, sample in enumerate(report["samples"]):
        if index not in (2, 10):
            sample.pop("js_heap_used_bytes")
    result = mv.validate_report(report)
    assert result["structural_ok"] is False
    assert any("js_heap_used_bytes coverage" in failure for failure in result["failures"])

    report = _schema3_report()
    for index, sample in enumerate(report["samples"]):
        if index not in (2, 10):
            sample.pop("js_heap_used_bytes")
    report["unavailable_metrics"] = {"js_heap_used_bytes": "sparse browser sampling"}
    result = mv.validate_report(report)
    assert result["structural_ok"] is True
    assert result["measurement_ok"] is False
    assert result["acceptance_ok"] is False

    report = _schema3_report()
    report["samples"] = report["samples"][:14]
    for index, sample in enumerate(report["samples"]):
        sample["timestamp_ms"] = [
            0,
            12000,
            24000,
            36000,
            48000,
            60000,
            72000,
            84000,
            96000,
            120000,
            132000,
            144000,
            156000,
            180000,
        ][index]
    result = mv.validate_report(report)
    assert result["structural_ok"] is False
    assert any("gap between usable observations" in failure for failure in result["failures"])


def test_worker_cache_and_wasm_coverage_policies():
    report = _schema3_report()
    for index, sample in enumerate(report["samples"]):
        if index not in (1, 9):
            sample.pop("workers")
    report["unavailable_metrics"] = {"workers": "worker telemetry was sparse"}
    result = mv.validate_report(report)
    assert result["measurement_ok"] is False
    assert any("workers coverage" in reason for reason in result["partial_reasons"])

    report = _schema3_report(features={"inference_cache": True})
    for sample in report["samples"]:
        sample.pop("cache_bytes")
        sample.pop("cache_entries")
    assert mv.validate_report(report)["structural_ok"] is False
    report["unavailable_metrics"] = {"cache_bytes": "cache counters unavailable"}
    result = mv.validate_report(report)
    assert result["structural_ok"] is True
    assert result["acceptance_ok"] is False

    report = _schema3_report()
    for sample in report["samples"]:
        sample.pop("wasm_heap_capacity_bytes")
    assert mv.validate_report(report)["structural_ok"] is False
    report["unavailable_metrics"] = {"wasm_heap_capacity_bytes": "not exposed"}
    assert mv.validate_report(report)["acceptance_ok"] is False


def test_lifecycle_events_must_be_observed_from_telemetry():
    report = _schema3_report()
    report["events_source"] = "manual"
    result = mv.validate_report(report)
    assert result["structural_ok"] is False
    assert any("live telemetry" in failure for failure in result["failures"])
