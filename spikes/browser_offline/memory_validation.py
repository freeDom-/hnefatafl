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
import hashlib
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
    "model-replacement",
    "client-disposal",
    "cache-churn",
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
    "model-replacement": ("model_replacements", 1),
    "client-disposal": ("client_disposals", 1),
    "cache-churn": ("cache_evictions", 1),
    "independent-batching": ("batch_successful_rounds", 2),
}
PROFILE_MODES = {
    "sustained-10m": "repeated",
    "sustained-30m": "repeated",
    "cancel-restart": "cancel-restart",
    "model-reload": "model-reload",
    "worker-replacement": "worker-replacement",
    "independent-batching": "batching",
    "model-replacement": "model-replacement",
    "client-disposal": "client-disposal",
    "cache-churn": "cache-churn",
}
LIFECYCLE_PROFILES = (
    "cancel-restart",
    "model-reload",
    "worker-replacement",
    "model-replacement",
    "client-disposal",
    "cache-churn",
    "independent-batching",
)


def _number(value, allow_none=True):
    if value is None:
        return allow_none
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def _positive_int(value) -> bool:
    return type(value) is int and value > 0


def _non_negative_int(value) -> bool:
    return type(value) is int and value >= 0


WORKLOAD_COUNTERS = (
    "completed_requests",
    "successful_searches",
    "workload_failures",
    "neural_searches",
    "completed_simulations",
    "neural_inferences",
    "no_inference_shortcuts",
    "client_disposals",
    "model_replacements",
    "cache_evictions",
    "cache_invalidation_failures",
    "cleanup_failures",
    "batches",
    "multi_request_batches",
    "batch_rounds",
    "batch_successful_rounds",
    "batch_counter_resets",
    "observed_max_batch",
    "peak_search_workers",
    "peak_inference_workers",
    "peak_total_workers",
    "batch_elapsed_ms_total",
    "batch_max_elapsed_ms",
    "batch_search_elapsed_ms_total",
    "batch_search_max_elapsed_ms",
)
FLOAT_WORKLOAD_COUNTERS = (
    "batch_elapsed_ms_total",
    "batch_max_elapsed_ms",
    "batch_search_elapsed_ms_total",
    "batch_search_max_elapsed_ms",
)


def _hex_digest(value) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


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
    gaps = [points[index][0] - points[index - 1][0] for index in range(1, len(points))]
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
            "search_workers",
            "inference_workers",
            "os_processes",
            "completed_requests",
            *(field for field in WORKLOAD_COUNTERS if field not in FLOAT_WORKLOAD_COUNTERS),
        ):
            value = sample.get(counter)
            if value is None:
                continue
            if not _non_negative_int(value):
                fail(f"sample {index} {counter} is invalid")
        for counter in FLOAT_WORKLOAD_COUNTERS:
            value = sample.get(counter)
            if value is None:
                continue
            if not _number(value, allow_none=False) or value < 0:
                fail(f"sample {index} {counter} is invalid")
        if "workload_complete" in sample and not isinstance(sample.get("workload_complete"), bool):
            fail(f"sample {index} workload_complete is invalid")
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
        if _positive_int(budgets.get("max_cache_bytes")) and _number(cache_bytes, allow_none=False):
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
                if _number(last_ts, allow_none=False) and float(last_ts) < (window_end - max_gap):
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
        telemetry = (
            (report.get("telemetry") or {}).get("identity")
            if isinstance(report.get("telemetry"), dict)
            else None
        )
        expected = report.get("expected_identity")
        if not isinstance(expected, dict):
            expected = {}
        if not _hex_digest(expected.get("model_sha256")):
            fail("expected original model identity is invalid")
            expected = {}
        for field in ("session_id", "workload"):
            if not isinstance(expected.get(field), str) or not expected.get(field):
                fail(f"expected identity {field} is invalid")
        approved = report.get("approved_model_sha256")
        if approved is None:
            approved = []
        if not isinstance(approved, list) or any(not _hex_digest(item) for item in approved):
            fail("approved model fingerprints must be a list of SHA-256 digests")
            approved = []
        if profile == "model-replacement":
            if len(set(approved)) < 2:
                fail("model-replacement requires at least two approved model fingerprints")
            if expected.get("model_sha256") not in approved:
                fail("the original model identity is not approved")
        elif approved:
            if set(approved) != {expected.get("model_sha256")}:
                fail("ordinary profiles cannot approve model transitions")
        workload = report.get("workload_telemetry")
        if not isinstance(workload, dict):
            partial_reasons.append("workload failure/work accounting telemetry is missing")
            workload = {}
        for field in WORKLOAD_COUNTERS:
            if field not in workload:
                continue
            value = workload[field]
            if field in FLOAT_WORKLOAD_COUNTERS:
                if not _number(value, allow_none=False) or value < 0:
                    fail(f"workload_telemetry.{field} must be a finite non-negative number")
            elif not _non_negative_int(value):
                fail(f"workload_telemetry.{field} must be a non-negative integer")
        transitions = workload.get("model_transitions", [])
        if not isinstance(transitions, list):
            fail("workload model_transitions must be a list")
            transitions = []
        history = workload.get("model_transition_history")
        if profile == "model-replacement" and not isinstance(history, dict):
            fail("model-replacement requires bounded transition-history metadata")
        previous_model = expected.get("model_sha256")
        first_retained_sequence = 1
        if isinstance(history, dict):
            total = history.get("total")
            retained = history.get("retained")
            limit = history.get("limit")
            truncated = history.get("truncated")
            if not _non_negative_int(total) or not _non_negative_int(retained):
                fail("model transition history counts are invalid")
                total = 0
                retained = 0
            if not _positive_int(limit):
                fail("model transition history limit is invalid")
            if not isinstance(truncated, bool):
                fail("model transition history truncation flag is invalid")
                truncated = False
            if retained != len(transitions):
                fail("model transition history retained count does not match entries")
            if truncated != (total > retained):
                fail("model transition history truncation flag is inconsistent")
            if total > 0 and truncated:
                first_sequence = history.get("first_retained_sequence")
                first_from = history.get("first_retained_from")
                if not _positive_int(first_sequence) or not _hex_digest(first_from):
                    fail("truncated model transition history lacks a bounded anchor")
                else:
                    first_retained_sequence = first_sequence
                    previous_model = first_from
            if _positive_int(first_retained_sequence) and transitions:
                first = transitions[0]
                if isinstance(first, dict) and first.get("sequence") != first_retained_sequence:
                    fail("model transition history anchor does not match retained entries")
            last_sequence = history.get("last_sequence")
            if total == 0:
                if last_sequence is not None:
                    fail("empty model transition history has a sequence")
            elif last_sequence != total:
                fail("model transition history last sequence is inconsistent")
        elif transitions:
            fail("model transitions require bounded history metadata")
        if profile != "model-replacement" and transitions:
            fail("model transitions are only authorized for model-replacement profiles")
        if profile == "model-replacement" and not transitions:
            partial_reasons.append("model replacement recorded no verified transition")
        chain_previous = previous_model
        previous_timestamp = 0.0
        for index, transition in enumerate(transitions):
            if not isinstance(transition, dict):
                fail("model transition entry is malformed")
                break
            sequence = transition.get("sequence")
            source = transition.get("from")
            destination = transition.get("to")
            recovered = transition.get("recovered")
            cache_invalidated = transition.get("cache_invalidated")
            timestamp = transition.get("timestamp_ms")
            if (
                not _positive_int(sequence)
                or sequence != first_retained_sequence + index
                or not _hex_digest(source)
                or not _hex_digest(destination)
                or source != chain_previous
                or source == destination
                or destination not in approved
                or recovered is not True
                or not _number(timestamp, allow_none=False)
                or timestamp < previous_timestamp
                or not isinstance(cache_invalidated, (bool, type(None)))
            ):
                fail("model transition chain is inconsistent")
                break
            if features.get("inference_cache") is True and cache_invalidated is not True:
                partial_reasons.append("model transition did not verify cache invalidation")
            chain_previous = destination
            previous_timestamp = timestamp
        if isinstance(history, dict):
            if history.get("final_model") != chain_previous:
                fail("transition history final model does not match the retained chain")
            last_retained = (
                transitions[-1].get("sequence")
                if transitions and isinstance(transitions[-1], dict)
                else None
            )
            if history.get("last_sequence") != last_retained:
                if history.get("total") != 0 or transitions:
                    fail("transition history sequence does not match retained entries")
            retained_digest = hashlib.sha256()
            for entry in transitions:
                if isinstance(entry, dict):
                    retained_digest.update(
                        json.dumps(entry, sort_keys=True, separators=(",", ":")).encode("utf-8")
                    )
            if history.get("retained_chain_digest_sha256") != retained_digest.hexdigest():
                fail("retained model transition digest is inconsistent")
            if history.get("chain_digest_sha256") is not None and not _hex_digest(
                history.get("chain_digest_sha256")
            ):
                fail("model transition chain digest is malformed")

        history_truncated = bool(isinstance(history, dict) and history.get("truncated"))
        first_transition_timestamp = (
            float(transitions[0]["timestamp_ms"])
            if transitions
            and isinstance(transitions[0], dict)
            and _number(transitions[0].get("timestamp_ms"), allow_none=False)
            else None
        )
        sample_anchors = report.get("sample_timeline")
        anchors_by_timestamp = {}
        anchor_sequences = []
        if sample_anchors is None:
            if history_truncated:
                partial_reasons.append(
                    "sample-aligned model timeline evidence is missing for evicted history"
                )
            sample_anchors = []
        elif not isinstance(sample_anchors, list):
            fail("sample_timeline must be a list")
            sample_anchors = []
        else:
            for anchor_index, anchor in enumerate(sample_anchors):
                if not isinstance(anchor, dict):
                    fail(f"sample timeline anchor {anchor_index} is malformed")
                    break
                anchor_model = anchor.get("model_sha256")
                anchor_timestamp = anchor.get("telemetry_timestamp_ms")
                anchor_sequence = anchor.get("sequence")
                if (
                    not _hex_digest(anchor_model)
                    or not _number(anchor_timestamp, allow_none=False)
                    or not _non_negative_int(anchor_sequence)
                    or anchor_model not in approved
                ):
                    fail(f"sample timeline anchor {anchor_index} is malformed")
                    break
                key = float(anchor_timestamp)
                if key in anchors_by_timestamp:
                    fail(f"sample timeline anchor {anchor_index} duplicates a timestamp")
                    break
                anchors_by_timestamp[key] = anchor
                anchor_sequences.append(anchor_sequence)
            if any(
                anchor_sequences[index] < anchor_sequences[index - 1]
                for index in range(1, len(anchor_sequences))
            ):
                fail("sample timeline transition sequences are non-monotonic")
            limit = report.get("sample_timeline_limit")
            if limit is not None and (not _positive_int(limit) or len(sample_anchors) > limit):
                fail("sample timeline limit is invalid")

        def model_at(timestamp: float) -> str | None:
            if not transitions:
                return expected.get("model_sha256")
            if history_truncated and first_transition_timestamp is not None:
                if timestamp < first_transition_timestamp:
                    return None
            current = expected.get("model_sha256")
            for entry in transitions:
                if (
                    isinstance(entry, dict)
                    and _number(entry.get("timestamp_ms"), allow_none=False)
                    and float(entry["timestamp_ms"]) <= timestamp
                ):
                    current = entry.get("to")
                else:
                    break
            return current

        unverified_historical_interval = False
        for index, sample in enumerate(measured):
            telemetry_identity = sample.get("telemetry")
            if telemetry_identity is None:
                if sample.get("telemetry_unavailable_reason"):
                    partial_reasons.append(
                        f"sample {index} has no usable live telemetry: "
                        f"{sample['telemetry_unavailable_reason']}"
                    )
                else:
                    fail(f"sample {index} has no live telemetry identity")
                continue
            if not isinstance(telemetry_identity, dict) or any(
                not isinstance(telemetry_identity.get(field), str)
                or not telemetry_identity.get(field)
                for field in ("session_id", "model_sha256", "workload")
            ):
                fail(f"sample {index} telemetry identity is incomplete")
                continue
            if isinstance(telemetry, dict):
                for field in ("session_id", "workload"):
                    if telemetry_identity.get(field) != telemetry.get(field):
                        fail(f"sample {index} telemetry identity is inconsistent")
            if telemetry_identity.get("model_sha256") not in approved:
                fail(f"sample {index} model identity is not approved")
            timeline_timestamp = (
                telemetry_identity.get("timestamp_ms")
                if _number(telemetry_identity.get("timestamp_ms"), allow_none=False)
                else sample["timestamp_ms"]
            )
            timeline_timestamp = float(timeline_timestamp)
            anchor = anchors_by_timestamp.get(timeline_timestamp)
            if anchor is not None:
                if anchor.get("model_sha256") != telemetry_identity.get("model_sha256"):
                    fail(f"sample {index} model identity contradicts its sample timeline anchor")
                if transitions:
                    before_retained = bool(
                        history_truncated
                        and first_transition_timestamp is not None
                        and timeline_timestamp < first_transition_timestamp
                    )
                    if before_retained:
                        if anchor.get("sequence") >= first_retained_sequence:
                            fail(
                                f"sample {index} transition sequence contradicts the "
                                "retained-history anchor"
                            )
                    else:
                        retained_before = sum(
                            1
                            for entry in transitions
                            if isinstance(entry, dict)
                            and _number(entry.get("timestamp_ms"), allow_none=False)
                            and float(entry["timestamp_ms"]) <= timeline_timestamp
                        )
                        expected_sequence = (
                            first_retained_sequence - 1 + retained_before
                            if history_truncated
                            else retained_before
                        )
                        if anchor.get("sequence") != expected_sequence:
                            fail(f"sample {index} transition sequence contradicts its anchor")
            sample_model = model_at(timeline_timestamp)
            if sample_model is None:
                unverified_historical_interval = True
                if anchor is None:
                    partial_reasons.append(
                        "samples precede the retained transition history and lack "
                        "sample-aligned timeline evidence"
                    )
            elif telemetry_identity.get("model_sha256") != sample_model:
                fail(f"sample {index} model identity contradicts the transition timeline")
        if unverified_historical_interval:
            partial_reasons.append(
                "model identities before the retained transition history are "
                "sample-anchored but the evicted interval is not independently verifiable"
            )
        sources = {sample.get("completed_requests_source") for sample in measured}
        if sources != {"telemetry"}:
            partial_reasons.append(
                "workload progress is not exclusively from the live telemetry session"
            )
        consistency = report.get("progress_consistency")
        if not isinstance(consistency, dict) or consistency.get("verified") is not True:
            partial_reasons.append(
                "progress-file/telemetry consistency was not verified for acceptance"
            )
        acceptance = workload.get("acceptance_window")
        acceptance_counters = {}
        acceptance_failures = 0
        if not isinstance(acceptance, dict):
            fail("acceptance-window workload statistics are missing")
        else:
            acceptance_counters = acceptance.get("counters")
            if not isinstance(acceptance_counters, dict):
                fail("acceptance-window counters are missing")
                acceptance_counters = {}
            for field in WORKLOAD_COUNTERS:
                if field not in acceptance_counters:
                    continue
                value = acceptance_counters[field]
                valid = (
                    _number(value, allow_none=False) and value >= 0
                    if field in FLOAT_WORKLOAD_COUNTERS
                    else _non_negative_int(value)
                )
                if not valid:
                    fail(f"acceptance-window {field} is invalid")
            for field in WORKLOAD_COUNTERS:
                sample_values = [
                    sample[field]
                    for sample in measured
                    if (
                        _number(sample.get(field), allow_none=False) and sample.get(field) >= 0
                        if field in FLOAT_WORKLOAD_COUNTERS
                        else _non_negative_int(sample.get(field))
                    )
                ]
                sample_max = max(sample_values, default=0)
                if field in acceptance_counters and acceptance_counters[field] != sample_max:
                    fail(f"acceptance-window {field} does not match the samples")
            acceptance_failures = acceptance_counters.get("workload_failures", 0)
            if not _non_negative_int(acceptance_failures):
                fail("acceptance-window workload failures are invalid")
                acceptance_failures = 0
            placements = acceptance.get("provider_placements")
            if not isinstance(placements, list) or any(
                not isinstance(item, str) or not item for item in placements
            ):
                fail("acceptance-window provider placements are malformed")
            elif not placements:
                partial_reasons.append("workload telemetry lacks observed provider placements")
        final_session = workload.get("final_session")
        final_counters = {}
        final_model = None
        if not isinstance(final_session, dict):
            partial_reasons.append("final workload summary is missing")
        else:
            if final_session.get("profile") not in (None, profile):
                fail("final workload profile does not match the memory profile")
            for field in ("session_id", "workload"):
                if final_session.get(field) != expected.get(field):
                    fail(f"final workload {field} does not match the expected identity")
            final_model = final_session.get("model_sha256")
            if final_model not in approved:
                fail("final workload model identity is not approved")
            complete = final_session.get("workload_complete")
            if not isinstance(complete, bool):
                fail("final workload completion flag is invalid")
            elif complete is not True:
                partial_reasons.append("final workload summary was not completed")
            exit_status = final_session.get("workload_exit_status")
            if exit_status is None:
                partial_reasons.append("final workload exit status is missing")
            elif not _non_negative_int(exit_status):
                fail("final workload exit status is invalid")
            elif exit_status != 0:
                partial_reasons.append(f"workload exit status {exit_status} is not successful")
            cleanup_complete = final_session.get("cleanup_complete")
            if not isinstance(cleanup_complete, bool):
                fail("final workload cleanup flag is invalid")
            elif cleanup_complete is not True:
                partial_reasons.append("final workload cleanup was not verified")
            final_counters = final_session.get("counters")
            if not isinstance(final_counters, dict):
                fail("final workload counters are missing")
                final_counters = {}
            for field in WORKLOAD_COUNTERS:
                value = final_counters.get(field)
                valid = (
                    _number(value, allow_none=False) and value >= 0
                    if field in FLOAT_WORKLOAD_COUNTERS
                    else _non_negative_int(value)
                )
                if not valid:
                    fail(f"final workload counter {field} is missing or invalid")
            if final_counters:
                for field in WORKLOAD_COUNTERS:
                    acceptance_value = acceptance_counters.get(field)
                    final_value = final_counters.get(field)
                    if field in FLOAT_WORKLOAD_COUNTERS:
                        comparable = _number(acceptance_value, allow_none=False) and _number(
                            final_value, allow_none=False
                        )
                    else:
                        comparable = _non_negative_int(acceptance_value) and _non_negative_int(
                            final_value
                        )
                    if comparable and final_value < acceptance_value:
                        fail(f"final workload {field} is below the acceptance-window maximum")
                if _non_negative_int(final_counters.get("workload_failures")) and (
                    final_counters["workload_failures"] > acceptance_failures
                ):
                    partial_reasons.append(
                        "workload failures occurred after the last acceptance-window sample"
                    )
                if (
                    _non_negative_int(final_counters.get("workload_failures"))
                    and final_counters["workload_failures"] > 0
                ):
                    partial_reasons.append(
                        f"workload recorded {final_counters['workload_failures']} failed search(es)"
                    )
                if (
                    _non_negative_int(final_counters.get("successful_searches"))
                    and final_counters["successful_searches"] < 1
                ):
                    partial_reasons.append("workload recorded no successful searches")
                if (
                    _non_negative_int(final_counters.get("neural_searches"))
                    and final_counters["neural_searches"] < 1
                ):
                    partial_reasons.append("workload recorded no neural-analysis searches")
                if (
                    _non_negative_int(final_counters.get("cache_invalidation_failures"))
                    and final_counters["cache_invalidation_failures"] > 0
                ):
                    partial_reasons.append(
                        "inference cache was not invalidated by model replacement"
                    )
                if (
                    _non_negative_int(final_counters.get("cleanup_failures"))
                    and final_counters["cleanup_failures"] > 0
                ):
                    partial_reasons.append("workload reported workload/coordinator cleanup failure")
                successful = final_counters.get("successful_searches")
                neural = final_counters.get("neural_searches")
                shortcuts = final_counters.get("no_inference_shortcuts")
                if (
                    _non_negative_int(successful)
                    and _non_negative_int(neural)
                    and _non_negative_int(shortcuts)
                    and successful != neural + shortcuts
                ):
                    fail("final workload successful searches do not match neural/shortcut counts")
                if (
                    _non_negative_int(final_counters.get("neural_inferences"))
                    and _non_negative_int(neural)
                    and final_counters["neural_inferences"] < neural
                ):
                    fail("final neural inference count is below the neural search count")
            final_transition_model = (
                model_at(float(final_session["recorded_at_ms"]))
                if _number(final_session.get("recorded_at_ms"), allow_none=False)
                else None
            )
            if final_transition_model is not None and final_model != final_transition_model:
                fail("final model identity contradicts the transition timeline")
            if profile == "model-replacement":
                total_transitions = (
                    history.get("total", 0) if isinstance(history, dict) else len(transitions)
                )
                if final_counters.get("model_replacements") != total_transitions:
                    fail("final model replacement count does not match transition history")
            elif final_counters.get("model_replacements") not in (None, 0):
                fail("ordinary profile recorded model replacements")
            placements = final_session.get("provider_placements")
            if not isinstance(placements, list) or any(
                not isinstance(item, str) or not item for item in placements
            ):
                fail("final provider placements are malformed")
            elif (
                _non_negative_int(final_counters.get("neural_searches"))
                and final_counters["neural_searches"] > 0
                and not placements
            ):
                partial_reasons.append("final workload lacks observed provider placements")
        process_observation = workload.get("process_observation")
        if not isinstance(process_observation, dict):
            partial_reasons.append("process termination evidence is missing")
        else:
            premature = process_observation.get("premature_termination")
            if not isinstance(premature, bool):
                fail("process premature_termination flag is invalid")
            elif premature:
                partial_reasons.append("workload process terminated before final evidence")
            alive = process_observation.get("root_alive_at_end")
            if not isinstance(alive, bool):
                fail("process root_alive_at_end flag is invalid")
            elif alive:
                partial_reasons.append("workload process remained alive after final evidence")
            if process_observation.get("final_cleanup_incomplete") is True:
                partial_reasons.append("workload cleanup was incomplete")
        completion = report.get("completion_evidence")
        if not isinstance(completion, dict):
            partial_reasons.append("actual parent-observed process completion evidence is missing")
        else:
            for field, expected_value in (
                ("session_id", expected.get("session_id")),
                ("workload", expected.get("workload")),
                ("profile", profile),
                (
                    "mode",
                    PROFILE_MODES.get(profile) if isinstance(profile, str) else None,
                ),
                ("model_sha256", final_model),
            ):
                if completion.get(field) != expected_value:
                    fail(f"completion evidence {field} does not match the profile")
            root_process = report.get("root_process")
            if not isinstance(root_process, dict):
                fail("root process evidence is missing")
                root_process = {}
            identity = root_process.get("identity")
            if completion.get("process_identity") != identity:
                fail("completion evidence process identity does not match the profile")
            if completion.get("pid") != root_process.get("pid"):
                fail("completion evidence pid does not match the profile")
            if completion.get("summary_reconciled") is not True:
                fail("completion evidence final summary was not reconciled")
            if not _hex_digest(completion.get("summary_sha256")):
                fail("completion evidence summary digest is invalid")
            actual = completion.get("actual_process_exit_status")
            reported = completion.get("reported_exit_status")
            if not _non_negative_int(actual):
                fail("actual process exit status is invalid")
            elif actual != 0:
                partial_reasons.append(f"actual process exit status {actual} is not successful")
            if not _non_negative_int(reported):
                fail("reported process exit status is invalid")
            if completion.get("exit_status_consistent") is not True or actual != reported:
                fail("actual and reported process exit statuses contradict")
            if isinstance(process_observation, dict):
                if process_observation.get("actual_process_exit_status") != actual:
                    fail("process observation does not match actual completion evidence")
            if (
                isinstance(final_session, dict)
                and final_session.get("actual_process_exit_status") != actual
            ):
                fail("final session does not match actual completion evidence")
        if isinstance(transitions, list) and profile == "model-replacement" and history:
            if history.get("final_model") != workload.get("model_sha256"):
                fail("final workload model identity does not match the transition history")
        if profile == "independent-batching":
            if (
                _non_negative_int(final_counters.get("batch_successful_rounds"))
                and final_counters["batch_successful_rounds"] < 2
            ):
                partial_reasons.append("insufficient successful multi-context batching rounds")
            if (
                _non_negative_int(final_counters.get("multi_request_batches"))
                and final_counters["multi_request_batches"] < 2
            ):
                partial_reasons.append("batching did not observe multiple multi-request batches")
            if (
                _non_negative_int(final_counters.get("observed_max_batch"))
                and final_counters["observed_max_batch"] < 2
            ):
                partial_reasons.append("batching never reached two concurrent contexts")
        peak_total = workload.get("peak_total_workers")
        if _non_negative_int(peak_total):
            if _positive_int(max_workers) and peak_total > max_workers:
                fail("observed peak worker usage exceeds the declared worker budget")
            peak_search = workload.get("peak_search_workers", 0)
            peak_inference = workload.get("peak_inference_workers", 0)
            if not _non_negative_int(peak_search) or not _non_negative_int(peak_inference):
                fail("peak worker counters are invalid")
            elif (
                peak_total < max(peak_search, peak_inference)
                or peak_total > peak_search + peak_inference
            ):
                fail("peak worker totals are inconsistent")
        if profile in SUSTAINED_PROFILES:
            neural_series = [
                sample.get("neural_searches")
                for sample in measured
                if _non_negative_int(sample.get("neural_searches"))
            ]
            if len(neural_series) != len(measured):
                partial_reasons.append("neural search activity telemetry is missing")
            elif any(
                neural_series[index] < neural_series[index - 1]
                for index in range(1, len(neural_series))
            ):
                fail("neural search activity counters are non-monotonic")
            elif len(neural_series) >= 2 and neural_series[-1] <= neural_series[0]:
                partial_reasons.append(
                    "neural search activity did not progress inside the acceptance window"
                )
            else:
                half = len(neural_series) // 2
                if not (
                    neural_series[half] > neural_series[0]
                    and neural_series[-1] > neural_series[half]
                ):
                    partial_reasons.append(
                        "neural search activity is not distributed across the window"
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
    cache_values = any(_number(sample.get("cache_bytes"), allow_none=False) for sample in measured)
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
        if metric == "cache_bytes":
            # Cache bytes may legitimately fill the bounded cache during a
            # sustained profile; the declared maximum cache budget is the limit.
            continue
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
        "blocked_reasons": [] if acceptance_ok else ["sustained process-memory acceptance blocked"],
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
