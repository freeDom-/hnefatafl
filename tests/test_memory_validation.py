"""Sustained memory profile validator."""

from __future__ import annotations

import sys
from pathlib import Path

SPIKE = Path(__file__).resolve().parents[1] / "spikes/browser_offline"
if str(SPIKE) not in sys.path:
    sys.path.insert(0, str(SPIKE))

import memory_validation as mv  # noqa: E402


def _report():
    return {
        "kind": "browser-analysis-memory-profile",
        "schema": 1,
        "profile": "sustained-10m",
        "status": "measured",
        "budgets": {
            "max_process_rss_bytes": 512 * 1024 * 1024,
            "max_js_heap_used_bytes": 256 * 1024 * 1024,
            "max_cache_bytes": 16 * 1024 * 1024,
        },
        "warmup_samples": 1,
        "samples": [
            {
                "timestamp_ms": index * 1000,
                "process_rss_bytes": 100 * 1024 * 1024 + index * 1024,
                "js_heap_used_bytes": 20 * 1024 * 1024,
                "wasm_heap_capacity_bytes": 32 * 1024 * 1024,
                "cache_entries": 10,
                "cache_bytes": 1000,
                "workers": 2,
                "completed_requests": index,
            }
            for index in range(4)
        ],
    }


def test_valid_measured_profile():
    assert mv.validate_report(_report())["ok"] is True


def test_blocked_profile_is_explicit():
    report = {
        "kind": "browser-analysis-memory-profile",
        "schema": 1,
        "profile": "sustained-30m",
        "status": "blocked",
        "blocked_reasons": ["no 30-minute window"],
    }
    assert mv.validate_report(report)["ok"] is True


def test_unavailable_rss_must_be_explained_and_not_zero_substituted():
    report = _report()
    report["samples"][0]["process_rss_bytes"] = None
    result = mv.validate_report(report)
    assert result["ok"] is False
    assert any("RSS" in failure for failure in result["failures"])
    report["samples"][0]["rss_unavailable_reason"] = "playwright did not expose process RSS"
    assert mv.validate_report(report)["ok"] is True


def test_growth_and_budget_are_rejected():
    report = _report()
    report["samples"][-1]["process_rss_bytes"] = 600 * 1024 * 1024
    result = mv.validate_report(report)
    assert result["ok"] is False
    assert any("budget" in failure or "grew" in failure for failure in result["failures"])


def test_timestamps_and_counters_are_validated():
    report = _report()
    report["samples"][2]["timestamp_ms"] = 0
    report["samples"][3]["cache_entries"] = -1
    result = mv.validate_report(report)
    assert result["ok"] is False
    assert any("backwards" in failure for failure in result["failures"])
    assert any("cache_entries" in failure for failure in result["failures"])
