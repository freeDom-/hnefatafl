"""Validate sustained browser-analysis memory profiles without inventing metrics.

Unavailable measurements must be ``null`` with an explanatory note. RSS is never
substituted with JS heap or WASM capacity. Budgets are declared in the report
before samples are evaluated.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

SPIKE = Path(__file__).resolve().parent
SCHEMA = 1
PROFILES = (
    "sustained-10m",
    "sustained-30m",
    "cancel-restart",
    "model-reload",
    "worker-replacement",
    "independent-batching",
)
METRICS = ("process_rss_bytes", "js_heap_used_bytes", "wasm_heap_capacity_bytes")
MIN_DURATION = {
    "sustained-10m": 600.0,
    "sustained-30m": 1800.0,
}
MIN_SAMPLES = 10
MIN_WARMUP_SAMPLES = 1


def _number(value, allow_none=True):
    if value is None:
        return allow_none
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def validate_report(report: dict) -> dict:
    failures = []

    def fail(message):
        failures.append(message)

    if not isinstance(report, dict):
        return {"ok": False, "failures": ["report must be an object"]}
    if report.get("kind") != "browser-analysis-memory-profile" or report.get("schema") != SCHEMA:
        fail("unsupported memory profile schema")
    if report.get("profile") not in PROFILES:
        fail("unknown memory profile")
    status = report.get("status")
    if status not in ("measured", "blocked"):
        fail("status must be measured or blocked")
    if status == "blocked":
        reasons = report.get("blocked_reasons")
        if not isinstance(reasons, list) or not reasons:
            fail("blocked profile needs reasons")
        return {"ok": not failures, "failures": failures, "status": status}
    profile = report.get("profile")
    required_duration = MIN_DURATION.get(profile, 0.0)
    duration_ms = report.get("duration_ms")
    if not _number(duration_ms, allow_none=False) or duration_ms <= 0:
        fail("measured profile needs positive actual duration_ms")
        duration_ms = 0
    if required_duration and duration_ms < required_duration * 1000.0:
        fail("measured profile duration does not cover the requested acceptance window")
    budgets = report.get("budgets")
    if not isinstance(budgets, dict):
        fail("budgets must be declared before evaluation")
        budgets = {}
    for field in ("max_process_rss_bytes", "max_js_heap_used_bytes", "max_cache_bytes"):
        if field in budgets and (
            not _number(budgets[field], allow_none=False) or budgets[field] <= 0
        ):
            fail(f"invalid budget {field}")
    samples = report.get("samples")
    if not isinstance(samples, list) or len(samples) < MIN_SAMPLES:
        fail(f"measured profile needs at least {MIN_SAMPLES} samples")
        samples = []
    warmup = report.get("warmup_samples", 1)
    if isinstance(warmup, bool) or not isinstance(warmup, int) or warmup < MIN_WARMUP_SAMPLES:
        fail("warmup_samples must be a positive integer")
        warmup = 0
    if samples and warmup >= len(samples) - 1:
        fail("warmup leaves insufficient post-warmup measurements")
    previous = None
    for index, sample in enumerate(samples):
        if not isinstance(sample, dict):
            fail(f"sample {index} is not an object")
            continue
        timestamp = sample.get("timestamp_ms")
        if not _number(timestamp, allow_none=False) or timestamp < 0:
            fail(f"sample {index} timestamp is invalid")
            continue
        if previous is not None and timestamp < previous:
            fail(f"sample {index} timestamp moved backwards")
        previous = timestamp
        for metric in METRICS:
            if metric in sample and not _number(sample[metric]):
                fail(f"sample {index} {metric} is invalid")
        for counter in ("cache_entries", "cache_bytes", "workers", "completed_requests"):
            if counter in sample and (
                not isinstance(sample[counter], int)
                or isinstance(sample[counter], bool)
                or sample[counter] < 0
            ):
                fail(f"sample {index} {counter} is invalid")
        for metric in METRICS:
            if sample.get(metric) is not None and sample[metric] < 0:
                fail(f"sample {index} {metric} is negative")
        if sample.get("process_rss_bytes") is None and not sample.get("rss_unavailable_reason"):
            fail(f"sample {index} must explain unavailable RSS")
    measured = samples[warmup:]
    if len(measured) < 2:
        fail("insufficient post-warmup measurements")
    if measured:
        observed_span = measured[-1].get("timestamp_ms", 0) - measured[0].get("timestamp_ms", 0)
        if required_duration and observed_span < required_duration * 1000.0:
            fail("sample timestamps do not cover the requested sustained window")
        gaps = [
            measured[index].get("timestamp_ms", 0) - measured[index - 1].get("timestamp_ms", 0)
            for index in range(1, len(measured))
        ]
        if any(gap <= 0 for gap in gaps):
            fail("sample timestamps are not strictly increasing")
        max_gap = max(1000.0, required_duration * 1000.0 / 10.0) if required_duration else 60_000.0
        if any(gap > max_gap for gap in gaps):
            fail("sampling gap is too large for the acceptance window")
        progress = [
            sample.get("completed_requests")
            for sample in measured
            if isinstance(sample.get("completed_requests"), int)
        ]
        if len(progress) < 2 or any(
            progress[index] < progress[index - 1] for index in range(1, len(progress))
        ):
            fail("completed-request counters are missing or non-monotonic")
        elif progress[-1] <= progress[0]:
            fail("sustained profile shows no completed-request progress")
    observed_rss = any(
        _number(sample.get("process_rss_bytes"), allow_none=False) for sample in measured
    )
    if observed_rss:
        budget = budgets.get("max_process_rss_bytes")
        if not _number(budget, allow_none=False) or budget <= 0:
            fail("positive max_process_rss_bytes budget is required for observed RSS")
    elif not report.get("rss_acceptance_unavailable_reason"):
        fail("unavailable RSS needs an explicit acceptance-unavailable reason")
    for field in ("max_js_heap_used_bytes", "max_cache_bytes"):
        if not _number(budgets.get(field), allow_none=False) or budgets.get(field) <= 0:
            fail(f"positive {field} budget is required")
    if samples and duration_ms and warmup < len(samples):
        span = samples[-1].get("timestamp_ms", 0) - samples[warmup].get("timestamp_ms", 0)
        if span > duration_ms:
            fail("sample timestamps exceed reported duration")
    for metric, budget in (
        ("process_rss_bytes", budgets.get("max_process_rss_bytes")),
        ("js_heap_used_bytes", budgets.get("max_js_heap_used_bytes")),
        ("cache_bytes", budgets.get("max_cache_bytes")),
    ):
        values = [
            sample.get(metric)
            for sample in measured
            if _number(sample.get(metric), allow_none=False)
        ]
        if not values:
            continue
        if budget is not None and max(values) > budget:
            fail(f"{metric} exceeded declared budget")
        baseline = values[0]
        growth = max(values) - baseline
        if baseline > 0 and growth > max(1024 * 1024, baseline * 0.25):
            fail(f"{metric} grew by more than 25% / 1 MiB after warmup")
    structural_ok = not failures
    acceptance_unavailable = not observed_rss
    if acceptance_unavailable:
        failures.append("process-memory acceptance is blocked without observable RSS")
    acceptance_ok = structural_ok and not acceptance_unavailable
    return {
        "ok": acceptance_ok,
        "structural_ok": structural_ok,
        "acceptance_ok": acceptance_ok,
        "failures": failures,
        "status": status,
        "samples": len(samples),
        "warmup_samples": warmup,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = json.loads(args.report.read_text())
    result = validate_report(report)
    if args.output:
        args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
