"""Validate sustained browser-analysis memory profiles without inventing metrics.

Unavailable measurements must be ``null`` with an explanatory reason. RSS is never
substituted with JS heap or WASM capacity. Budgets are declared in the report
before samples are evaluated.

Three outcomes are reported separately:

* ``structural_ok`` - the profile is internally consistent and interpretable;
* ``measurement_ok`` - every applicable mandatory metric has adequate temporal
  coverage and usable observations;
* ``acceptance_ok`` - the sustained process-memory profile is accepted.

Schema 3 separates the warmup and acceptance windows explicitly:
``warmup_ms`` is excluded and ``duration_ms`` is the required post-warmup window.
Schema 2 reports remain readable using index-based ``warmup_samples``.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

SPIKE = Path(__file__).resolve().parent
SCHEMA = 3
PROFILES = (
    "sustained-10m",
    "sustained-30m",
    "cancel-restart",
    "model-reload",
    "worker-replacement",
    "independent-batching",
)
SUSTAINED_PROFILES = ("sustained-10m", "sustained-30m")
METRICS = ("process_rss_bytes", "js_heap_used_bytes", "wasm_heap_capacity_bytes")
BUDGET_METRICS = (
    ("process_rss_bytes", "max_process_rss_bytes"),
    ("js_heap_used_bytes", "max_js_heap_used_bytes"),
    ("cache_bytes", "max_cache_bytes"),
)
MIN_DURATION = {
    "sustained-10m": 600.0,
    "sustained-30m": 1800.0,
}
MIN_SAMPLES = 10
MIN_WARMUP_SAMPLES = 1
MIN_RSS_COVERAGE = 0.8
MIN_METRIC_COVERAGE = 0.8
MIN_USABLE_RSS = 5
MIN_USABLE_METRIC = 5
MIN_ACTIVE_FRACTION = 0.25
WINDOW_TOLERANCE_MS = 1000.0
PROFILE_EVENTS = {
    "cancel-restart": ("cancellations", 1),
    "model-reload": ("reloads", 1),
    "worker-replacement": ("worker_replacements", 1),
    "independent-batching": ("batches", 1),
}
LIFECYCLE_PROFILES = ("cancel-restart", "model-reload", "worker-replacement")


def _number(value, allow_none=True):
    if value is None:
        return allow_none
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def _positive_int(value) -> bool:
    return type(value) is int and value > 0


def _reason_map(value, failures: list[str]) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        failures.append("unavailable_metrics must be an object")
        return {}
    reasons = {}
    for key, reason in value.items():
        if not isinstance(key, str) or not key:
            failures.append("unavailable_metrics keys must be non-empty strings")
            continue
        if not isinstance(reason, str) or not reason:
            failures.append(f"unavailable_metrics.{key} must be a non-empty reason")
            continue
        reasons[key] = reason
    return reasons


def _coverage(
    metric: str,
    measured: list[dict],
    *,
    min_usable: int,
    min_coverage: float,
    max_gap_ms: float,
    reason: str | None,
    failures: list[str],
    partial: list[str],
    window_start_ms: float | None = None,
    window_end_ms: float | None = None,
) -> dict:
    points = []
    for sample in measured:
        value = sample.get(metric)
        timestamp = sample.get("timestamp_ms")
        if _number(value, allow_none=False) and _number(timestamp, allow_none=False):
            points.append((float(timestamp), value))
    coverage = len(points) / len(measured) if measured else 0.0
    gaps = [
        points[index][0] - points[index - 1][0] for index in range(1, len(points))
    ]
    boundary_gaps = []
    if points and window_start_ms is not None:
        boundary_gaps.append(max(0.0, points[0][0] - window_start_ms))
    if points and window_end_ms is not None:
        boundary_gaps.append(max(0.0, window_end_ms - points[-1][0]))
    all_gaps = gaps + boundary_gaps
    longest_gap = max(all_gaps) if all_gaps else None
    info = {
        "usable": len(points),
        "of": len(measured),
        "coverage": coverage,
        "longest_usable_gap_ms": longest_gap,
    }
    if not points:
        if reason:
            partial.append(f"{metric}: {reason}")
        else:
            failures.append(f"{metric} has no usable observations and no explanatory reason")
        return info
    deficient = len(points) < min_usable or coverage < min_coverage
    gapped = longest_gap is not None and longest_gap > max_gap_ms
    if deficient or gapped:
        message = (
            f"{metric} coverage is insufficient ({len(points)}/{len(measured)} samples)"
            if deficient
            else f"{metric} has a {longest_gap:.0f} ms gap between usable observations"
        )
        if reason:
            partial.append(f"{message}: {reason}")
        else:
            failures.append(message)
    return info


def validate_report(report: dict) -> dict:
    failures: list[str] = []

    def fail(message):
        failures.append(message)

    if not isinstance(report, dict):
        return {
            "ok": False,
            "structural_ok": False,
            "measurement_ok": False,
            "acceptance_ok": False,
            "acceptance": False,
            "status": "invalid",
            "failures": ["report must be an object"],
            "blocked_reasons": [],
        }
    schema = report.get("schema")
    if report.get("kind") != "browser-analysis-memory-profile" or schema not in (
        SCHEMA,
        SCHEMA - 1,
        2,
    ):
        fail("unsupported memory profile schema")
        schema = 0
    profile = report.get("profile")
    if profile not in PROFILES:
        fail("unknown memory profile")
    status = report.get("status")
    if status not in ("measured", "blocked"):
        fail("status must be measured or blocked")
    if status == "blocked":
        reasons = report.get("blocked_reasons")
        if (
            not isinstance(reasons, list)
            or not reasons
            or any(not isinstance(reason, str) or not reason for reason in reasons)
        ):
            fail("blocked profile needs non-empty reasons")
        return {
            "ok": not failures,
            "structural_ok": not failures,
            "measurement_ok": False,
            "acceptance_ok": False,
            "acceptance": False,
            "status": "blocked" if not failures else "invalid",
            "failures": failures,
            "blocked_reasons": report.get("blocked_reasons")
            if isinstance(report.get("blocked_reasons"), list)
            else [],
        }
    required_duration = MIN_DURATION.get(profile, 0.0) if isinstance(profile, str) else 0.0
    duration_ms = report.get("duration_ms")
    if not _number(duration_ms, allow_none=False) or duration_ms <= 0:
        fail("measured profile needs positive actual duration_ms")
        duration_ms = 0
    elif required_duration and duration_ms < required_duration * 1000.0:
        fail("measured profile duration does not cover the requested acceptance window")
    unavailable = _reason_map(report.get("unavailable_metrics"), failures)
    budgets = report.get("budgets")
    if not isinstance(budgets, dict):
        fail("budgets must be declared before evaluation")
        budgets = {}
    for field in ("max_process_rss_bytes", "max_js_heap_used_bytes", "max_cache_bytes"):
        if not _number(budgets.get(field), allow_none=False) or budgets.get(field) <= 0:
            fail(f"invalid budget {field}")
    max_workers = budgets.get("max_workers")
    if not _positive_int(max_workers):
        fail("invalid budget max_workers")
    max_wasm = budgets.get("max_wasm_heap_capacity_bytes")
    if max_wasm is not None and (not _number(max_wasm, allow_none=False) or max_wasm <= 0):
        fail("invalid budget max_wasm_heap_capacity_bytes")
        max_wasm = None
    features = report.get("features")
    if features is not None and not isinstance(features, dict):
        fail("features must be an object")
        features = {}
    features = features or {}
    cache_feature = features.get("inference_cache")
    samples = report.get("samples")
    if not isinstance(samples, list) or len(samples) < MIN_SAMPLES:
        fail(f"measured profile needs at least {MIN_SAMPLES} samples")
        samples = [] if not isinstance(samples, list) else samples
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
        for counter in (
            "cache_entries",
            "cache_bytes",
            "workers",
            "os_processes",
            "completed_requests",
        ):
            value = sample.get(counter)
            if value is None:
                continue
            if type(value) is not int or value < 0:
                fail(f"sample {index} {counter} is invalid")
        for metric in METRICS:
            value = sample.get(metric)
            if _number(value, allow_none=False) and value < 0:
                fail(f"sample {index} {metric} is negative")
        workers = sample.get("workers")
        if type(workers) is int and not isinstance(workers, bool):
            if workers < 1:
                fail(f"sample {index} reports no active browser worker")
            elif _positive_int(max_workers) and workers > max_workers:
                fail(f"sample {index} exceeds the declared worker budget")
        cache_bytes = sample.get("cache_bytes")
        if _positive_int(budgets.get("max_cache_bytes")) and _number(
            cache_bytes, allow_none=False
        ):
            if cache_bytes > budgets["max_cache_bytes"]:
                fail(f"sample {index} cache_bytes exceeds the declared budget")
        wasm = sample.get("wasm_heap_capacity_bytes")
        if max_wasm is not None and _number(wasm, allow_none=False) and wasm > max_wasm:
            fail(f"sample {index} wasm_heap_capacity_bytes exceeds the declared budget")
        if sample.get("process_rss_bytes") is None and not sample.get("rss_unavailable_reason"):
            fail(f"sample {index} must explain unavailable RSS")
    # Separate the warmup window from the acceptance window. Schema 3 uses explicit
    # timestamps; schema 2 uses the historical warmup sample count.
    if schema >= 3:
        warmup_ms = report.get("warmup_ms")
        if not _number(warmup_ms, allow_none=False) or warmup_ms < 0:
            fail("schema 3 requires a non-negative warmup_ms")
            warmup_ms = 0.0
        warmup_samples = sum(
            1
            for sample in samples
            if isinstance(sample, dict)
            and _number(sample.get("timestamp_ms"), allow_none=False)
            and float(sample["timestamp_ms"]) < float(warmup_ms)
        )
        if warmup_samples < MIN_WARMUP_SAMPLES:
            fail("warmup_ms must exclude at least one measured warmup sample")
        measured = [
            sample
            for sample in samples
            if isinstance(sample, dict)
            and _number(sample.get("timestamp_ms"), allow_none=False)
            and float(sample["timestamp_ms"]) >= float(warmup_ms)
        ]
        window_start = float(warmup_ms)
    else:
        warmup = report.get("warmup_samples", 1)
        if type(warmup) is not int or warmup < MIN_WARMUP_SAMPLES:
            fail("warmup_samples must be a positive integer")
            warmup = 0
        if samples and warmup >= len(samples) - 1:
            fail("warmup leaves insufficient post-warmup measurements")
        measured = [sample for sample in samples[warmup:] if isinstance(sample, dict)]
        warmup_samples = warmup
        first_ts = measured[0].get("timestamp_ms") if measured else 0
        window_start = float(first_ts) if _number(first_ts, allow_none=False) else 0.0
        warmup_ms = window_start
    if len(measured) < 2:
        fail("insufficient post-warmup measurements")
    observed_span = 0.0
    window_ms = required_duration * 1000.0 if required_duration else float(duration_ms or 0.0)
    if measured:
        first_ts = measured[0].get("timestamp_ms")
        last_ts = measured[-1].get("timestamp_ms")
        if _number(first_ts, allow_none=False) and _number(last_ts, allow_none=False):
            observed_span = float(last_ts) - float(first_ts)
        max_gap = max(1000.0, window_ms / 10.0) if window_ms else 60_000.0
        gaps = []
        for index in range(1, len(measured)):
            left = measured[index - 1].get("timestamp_ms")
            right = measured[index].get("timestamp_ms")
            if _number(left, allow_none=False) and _number(right, allow_none=False):
                gaps.append(float(right) - float(left))
        if any(gap <= 0 for gap in gaps):
            fail("sample timestamps are not strictly increasing")
        if any(gap > max_gap for gap in gaps):
            fail("sampling gap is too large for the acceptance window")
        if window_ms:
            window_end = window_start + window_ms
            if observed_span < window_ms - WINDOW_TOLERANCE_MS:
                fail("sample timestamps do not cover the requested sustained window")
            if schema >= 3:
                if _number(first_ts, allow_none=False) and float(first_ts) > (
                    window_start + max_gap
                ):
                    fail("acceptance window starts after the declared warmup boundary")
                if _number(last_ts, allow_none=False) and float(last_ts) < (
                    window_end - max_gap
                ):
                    fail("acceptance window ends before the requested measurement window")
        progress = [sample.get("completed_requests") for sample in measured]
        if any(type(value) is not int for value in progress):
            fail("every post-warmup sample must report completed_requests")
            progress = []
        elif any(progress[index] < progress[index - 1] for index in range(1, len(progress))):
            fail("completed-request counters are non-monotonic")
            progress = []
        elif profile in SUSTAINED_PROFILES:
            active = sum(
                1 for index in range(1, len(progress)) if progress[index] > progress[index - 1]
            )
            minimum_active = max(2, math.ceil((len(progress) - 1) * MIN_ACTIVE_FRACTION))
            if active < minimum_active:
                fail("sustained profile shows only brief workload activity")
            half = len(progress) // 2
            first_half = progress[half] > progress[0]
            second_half = progress[-1] > progress[half]
            if not (first_half and second_half):
                fail("sustained workload progress is not distributed across the window")
    else:
        max_gap = 60_000.0
    events = report.get("events")
    if events is not None and not isinstance(events, dict):
        fail("events must be an object")
        events = {}
    events = events or {}
    for name, value in events.items():
        if not isinstance(name, str) or not name:
            fail("event names must be non-empty strings")
        elif type(value) is not int or value < 0:
            fail(f"event {name} must be a non-negative integer")
    required_event = PROFILE_EVENTS.get(profile) if isinstance(profile, str) else None
    if required_event is not None:
        name, minimum = required_event
        value = events.get(name)
        if type(value) is not int or value < minimum:
            fail(f"profile {profile} requires at least {minimum} observed {name}")
        if schema >= 3 and report.get("events_source") != "telemetry":
            fail("lifecycle events must be observed from live telemetry, not declared manually")
    if profile in LIFECYCLE_PROFILES:
        workers = [
            sample.get("workers") for sample in measured if type(sample.get("workers")) is int
        ]
        if workers and min(workers) < 1:
            fail("lifecycle profile reports a period without an active worker")
    partial_reasons: list[str] = []
    if schema >= 3:
        identity = (report.get("telemetry") or {}).get("identity") if isinstance(
            report.get("telemetry"), dict
        ) else None
        for index, sample in enumerate(measured):
            telemetry = sample.get("telemetry")
            if telemetry is None:
                if sample.get("telemetry_unavailable_reason"):
                    partial_reasons.append(
                        f"sample {index} has no usable live telemetry: "
                        f"{sample['telemetry_unavailable_reason']}"
                    )
                else:
                    fail(f"sample {index} has no live telemetry identity")
                continue
            if not isinstance(telemetry, dict) or any(
                not isinstance(telemetry.get(field), str) or not telemetry.get(field)
                for field in ("session_id", "model_sha256", "workload")
            ):
                fail(f"sample {index} telemetry identity is incomplete")
                continue
            if isinstance(identity, dict) and any(
                telemetry.get(field) != identity.get(field)
                for field in ("session_id", "model_sha256", "workload")
            ):
                fail(f"sample {index} telemetry identity is inconsistent")
        sources = {
            sample.get("completed_requests_source") for sample in measured
        }
        if sources != {"telemetry"}:
            partial_reasons.append(
                "workload progress is not exclusively from the live telemetry session"
            )
        consistency = report.get("progress_consistency")
        if not isinstance(consistency, dict) or consistency.get("verified") is not True:
            partial_reasons.append(
                "progress-file/telemetry consistency was not verified for acceptance"
            )
    coverage: dict[str, dict] = {}
    coverage_window_start = window_start
    coverage_window_end = window_start + window_ms
    coverage["process_rss_bytes"] = _coverage(
        "process_rss_bytes",
        measured,
        min_usable=MIN_USABLE_RSS,
        min_coverage=MIN_RSS_COVERAGE,
        max_gap_ms=max_gap,
        reason=unavailable.get("process_rss_bytes")
        or report.get("rss_acceptance_unavailable_reason"),
        failures=failures,
        partial=partial_reasons,
        window_start_ms=coverage_window_start,
        window_end_ms=coverage_window_end,
    )
    observed_rss = coverage["process_rss_bytes"]["usable"] > 0
    js_reason = unavailable.get("js_heap_used_bytes") or report.get(
        "js_heap_acceptance_unavailable_reason"
    )
    if js_reason is None and features.get("js_heap") is False:
        js_reason = "JS heap measurement was explicitly disabled for this profile"
    coverage["js_heap_used_bytes"] = _coverage(
        "js_heap_used_bytes",
        measured,
        min_usable=MIN_USABLE_METRIC,
        min_coverage=MIN_METRIC_COVERAGE,
        max_gap_ms=max_gap,
        reason=js_reason,
        failures=failures,
        partial=partial_reasons,
        window_start_ms=coverage_window_start,
        window_end_ms=coverage_window_end,
    )
    cache_reason = unavailable.get("cache_bytes") or unavailable.get("cache_entries")
    cache_values = any(
        _number(sample.get("cache_bytes"), allow_none=False) for sample in measured
    )
    cache_applicable = cache_feature is True or cache_values
    if cache_feature is False and not cache_values:
        coverage["cache_bytes"] = {
            "usable": 0,
            "of": len(measured),
            "coverage": 0.0,
            "not_applicable": True,
            "reason": "inference cache was explicitly disabled",
        }
        coverage["cache_entries"] = coverage["cache_bytes"]
    else:
        coverage["cache_bytes"] = _coverage(
            "cache_bytes",
            measured,
            min_usable=MIN_USABLE_METRIC,
            min_coverage=MIN_METRIC_COVERAGE,
            max_gap_ms=max_gap,
            reason=cache_reason,
            failures=failures,
            partial=partial_reasons,
            window_start_ms=coverage_window_start,
            window_end_ms=coverage_window_end,
        )
        coverage["cache_entries"] = _coverage(
            "cache_entries",
            measured,
            min_usable=MIN_USABLE_METRIC,
            min_coverage=MIN_METRIC_COVERAGE,
            max_gap_ms=max_gap,
            reason=cache_reason,
            failures=failures,
            partial=partial_reasons,
            window_start_ms=coverage_window_start,
            window_end_ms=coverage_window_end,
        )
    wasm_values = any(
        _number(sample.get("wasm_heap_capacity_bytes"), allow_none=False) for sample in measured
    )
    wasm_applicable = max_wasm is not None or wasm_values
    wasm_reason = unavailable.get("wasm_heap_capacity_bytes")
    if not wasm_applicable and wasm_reason is None:
        fail("WASM heap capacity needs a budget, observations or an explanatory reason")
    elif not wasm_applicable and wasm_reason:
        partial_reasons.append(f"wasm_heap_capacity_bytes: {wasm_reason}")
    elif wasm_applicable:
        coverage["wasm_heap_capacity_bytes"] = _coverage(
            "wasm_heap_capacity_bytes",
            measured,
            min_usable=MIN_USABLE_METRIC,
            min_coverage=MIN_METRIC_COVERAGE,
            max_gap_ms=max_gap,
            reason=wasm_reason,
            failures=failures,
            partial=partial_reasons,
            window_start_ms=coverage_window_start,
            window_end_ms=coverage_window_end,
        )
    coverage["workers"] = _coverage(
        "workers",
        measured,
        min_usable=MIN_USABLE_METRIC,
        min_coverage=MIN_METRIC_COVERAGE,
        max_gap_ms=max_gap,
        reason=unavailable.get("workers") or "browser worker telemetry was not reported",
        failures=failures,
        partial=partial_reasons,
        window_start_ms=coverage_window_start,
        window_end_ms=coverage_window_end,
    )
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
        if not _number(budget, allow_none=False) or budget <= 0:
            fail(f"positive {dict(BUDGET_METRICS).get(metric)} budget is required")
            continue
        if max(values) > budget:
            fail(f"{metric} exceeded declared budget")
        baseline = values[0]
        growth = max(values) - baseline
        threshold = max(1024 * 1024, baseline * 0.25)
        if growth > threshold:
            fail(f"{metric} grew by more than the sustained-memory growth threshold")
    if samples and duration_ms:
        last = samples[-1].get("timestamp_ms") if isinstance(samples[-1], dict) else None
        if (
            _number(last, allow_none=False)
            and float(last) > float(warmup_ms) + duration_ms + WINDOW_TOLERANCE_MS
        ):
            fail("sample timestamps exceed reported duration after warmup")
    structural_ok = not failures
    measurement_ok = (
        structural_ok
        and not partial_reasons
        and coverage["process_rss_bytes"]["coverage"] >= MIN_RSS_COVERAGE
        and coverage["js_heap_used_bytes"]["coverage"] >= MIN_METRIC_COVERAGE
        and coverage["workers"]["coverage"] >= MIN_METRIC_COVERAGE
    )
    acceptance_ok = measurement_ok and observed_rss
    if not observed_rss and structural_ok:
        failures.append("process-memory acceptance is blocked without observable RSS")
        measurement_ok = False
        acceptance_ok = False
    if partial_reasons:
        failures.extend(f"partial measurement: {reason}" for reason in partial_reasons)
    outcome = "invalid" if not structural_ok else ("accepted" if acceptance_ok else "partial")
    return {
        "ok": structural_ok,
        "structural_ok": structural_ok,
        "measurement_ok": measurement_ok,
        "acceptance_ok": acceptance_ok,
        "acceptance": acceptance_ok,
        "status": outcome,
        "failures": failures,
        "blocked_reasons": []
        if acceptance_ok
        else ["sustained process-memory acceptance blocked"],
        "samples": len(samples),
        "warmup_samples": warmup_samples,
        "warmup_ms": warmup_ms,
        "window_start_ms": window_start,
        "window_end_ms": window_start + (duration_ms or 0),
        "observed_window_ms": observed_span,
        "coverage": coverage,
        "partial_reasons": partial_reasons,
    }


def _read_json(path: Path) -> dict:
    try:
        raw = path.read_text()
    except OSError as error:
        raise ValueError(f"cannot read memory profile {path.name}: {error}") from None
    try:
        value = json.loads(raw)
    except (ValueError, TypeError) as error:
        raise ValueError(f"memory profile {path.name} is not valid JSON: {error}") from None
    if not isinstance(value, dict):
        raise ValueError(f"memory profile {path.name} must be an object")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        report = _read_json(args.report)
    except ValueError as error:
        result = {
            "ok": False,
            "structural_ok": False,
            "acceptance_ok": False,
            "acceptance": False,
            "status": "invalid",
            "failures": [str(error)],
        }
        if args.output:
            args.output.write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result, indent=2))
        return 1
    result = validate_report(report)
    if args.output:
        args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    if not result["structural_ok"]:
        return 1
    return 0 if result["acceptance_ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
