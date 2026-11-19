"""Canonical comparison of two benchmark reports (native vs browser).

This module is the single source of truth for deciding whether two benchmark
runs are comparable and whether the fixed-work results agree. Both the test
suite and the command-line comparator use it, so there is exactly one
implementation of the rules.

A normalised report (see ``jarl_benchmark.build_report``) carries an ``identity``
block (workload digest, effective configurations, mode, seed, engine identity)
and a flat list of per-case ``records`` with a ``status`` classification.

Comparison distinguishes:

* ``failures`` -- mandatory. A non-empty list fails the gate (non-zero exit).
  Fixed-work correctness, workload/identity agreement and case coverage live
  here.
* ``warnings`` -- informational only. Fixed-time best-move and value differences
  are warnings because two runtimes complete different amounts of work inside
  the same wall-clock budget.
"""

from __future__ import annotations

import math
from typing import Any

VALUE_TOLERANCE = 1e-9
REQUIRED_IDENTITY_FIELDS = (
    "protocol",
    "workload_digest",
    "mode",
    "seed",
    "engine_identity",
    "effective_configs",
)
VALID_FIXED_TIME_STATUSES = ("completed", "deadline_limited", "terminal")


def _by_key(records: list[dict[str, Any]]) -> dict[tuple, dict[str, Any]]:
    """Index measured records by (position, config, ruleset).

    Warm-up records and non-measured phases are ignored. The first measurement of
    a key is retained; repeated samples are compared separately where callers
    keep every repetition.
    """
    index: dict[tuple, dict[str, Any]] = {}
    for record in records:
        if record.get("phase", "measured") != "measured":
            continue
        key = (record.get("position"), record.get("config"), record.get("ruleset"))
        index.setdefault(key, record)
    return index


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _case_failures(
    expected: dict[str, Any], actual: dict[str, Any], *, fixed_work: bool
) -> list[str]:
    """Return mandatory failure messages for one (position, config) case."""
    label = f"{expected.get('position')}/{expected.get('config')}"
    expected_status = expected.get("status")
    actual_status = actual.get("status")

    if expected_status == "terminal":
        if actual_status != "terminal":
            return [f"{label}: expected terminal, got {actual_status!r}"]
        failures = []
        if actual.get("best_move") is not None:
            failures.append(f"{label}: terminal best_move must be null")
        if not _finite(actual.get("value")) or not -1 <= actual["value"] <= 1:
            failures.append(f"{label}: terminal value is not finite in [-1, 1]")
        return failures

    if expected_status in ("deadline_limited", "incomplete"):
        # A fixed-work case that cannot finish inside the configured safety limit
        # is reported explicitly, not treated as a completed correctness match.
        # Both runtimes must agree it did not finish, and it must not have
        # crashed; its move/value are not compared as a fixed-work result.
        if actual_status in ("deadline_limited", "incomplete"):
            return []
        if actual_status in ("error", "unsupported"):
            return [
                f"{label}: expected a bounded {expected_status} case, got "
                f"{actual_status}: {actual.get('error')}"
            ]
        return [f"{label}: expected {expected_status}, got a completed search"]
    if expected_status in ("error", "unsupported"):
        if actual_status != expected_status:
            return [f"{label}: expected {expected_status!r}, got {actual_status!r}"]
        if not actual.get("error"):
            return [f"{label}: {expected_status} case must report an explicit error"]
        # The error message is environment specific; only presence is mandatory.
        return []

    if actual_status in ("error", "unsupported"):
        return [f"{label}: unexpected {actual_status}: {actual.get('error')}"]

    failures = []
    if not _finite(actual.get("value")) or not -1 <= actual["value"] <= 1:
        failures.append(f"{label}: value is not finite in [-1, 1]")
    if actual.get("best_move") is None:
        failures.append(f"{label}: non-terminal search returned no best move")

    if fixed_work:
        if actual_status != "completed":
            failures.append(f"{label}: fixed-work search did not complete ({actual_status!r})")
        if expected.get("completed_depth") != actual.get("completed_depth"):
            failures.append(
                f"{label}: completed depth {actual.get('completed_depth')} != "
                f"{expected.get('completed_depth')}"
            )
        if expected.get("best_move") != actual.get("best_move"):
            failures.append(
                f"{label}: best move {actual.get('best_move')} != {expected.get('best_move')}"
            )
        if (
            _finite(actual.get("value"))
            and abs(actual["value"] - expected["value"]) > VALUE_TOLERANCE
        ):
            failures.append(f"{label}: value {actual.get('value')} != {expected.get('value')}")
    elif actual_status not in VALID_FIXED_TIME_STATUSES:
        failures.append(f"{label}: fixed-time search produced no valid result ({actual_status!r})")
    return failures


def compare(expected: dict[str, Any], actual: dict[str, Any]) -> dict[str, Any]:
    """Compare an actual report against a reference report.

    Mandatory failures (always non-zero exit for a gate): identity mismatch,
    missing required cases, execution errors and every fixed-work divergence.
    Warnings (informational): extra cases and fixed-time best-move/value/depth
    differences, which are expected when two runtimes complete different work.
    The result is ``ok`` only when there are no mandatory failures.
    """
    failures: list[str] = []
    warnings: list[str] = []

    expected_identity = expected.get("identity", {})
    actual_identity = actual.get("identity", {})
    for name in REQUIRED_IDENTITY_FIELDS:
        if expected_identity.get(name) != actual_identity.get(name):
            failures.append(
                f"identity.{name} mismatch: {actual_identity.get(name)!r} != "
                f"{expected_identity.get(name)!r}"
            )

    fixed_work = expected_identity.get("mode") == "fixed_work"
    expected_cases = _by_key(expected.get("records", []))
    actual_cases = _by_key(actual.get("records", []))
    missing = sorted(set(expected_cases) - set(actual_cases), key=str)
    extra = sorted(set(actual_cases) - set(expected_cases), key=str)
    if missing:
        failures.append(
            f"missing {len(missing)} required case(s): {[str(key) for key in missing[:5]]}"
        )
    if extra:
        warnings.append(f"{len(extra)} unexpected extra case(s) reported")

    rows: list[dict[str, Any]] = []
    comparable = 0
    limited = 0
    agreement = {"move": 0, "value": 0, "depth": 0}
    for key in sorted(set(expected_cases) & set(actual_cases), key=str):
        expected_row = expected_cases[key]
        actual_row = actual_cases[key]
        case_failures = _case_failures(expected_row, actual_row, fixed_work=fixed_work)
        expected_status = expected_row.get("status")
        if expected_status in ("deadline_limited", "incomplete"):
            limited += 1
            failures.extend(case_failures)
            rows.append(
                {
                    "position": key[0],
                    "config": key[1],
                    "ruleset": key[2],
                    "expected_status": expected_status,
                    "actual_status": actual_row.get("status"),
                    "limited": True,
                    "problems": case_failures,
                }
            )
            continue
        comparable += 1
        move_match = expected_row.get("best_move") == actual_row.get("best_move")
        value_match = _values_agree(expected_row.get("value"), actual_row.get("value"))
        depth_match = expected_row.get("completed_depth") == actual_row.get("completed_depth")
        agreement["move"] += int(move_match)
        agreement["value"] += int(value_match)
        agreement["depth"] += int(depth_match)
        failures.extend(case_failures)
        if not fixed_work and (not move_match or not value_match or not depth_match):
            warnings.append(
                f"{key[0]}/{key[1]}: fixed-time results differ "
                f"(move={move_match}, value={value_match}, depth={depth_match})"
            )
        rows.append(
            {
                "position": key[0],
                "config": key[1],
                "ruleset": key[2],
                "expected_status": expected_row.get("status"),
                "actual_status": actual_row.get("status"),
                "move_match": move_match,
                "value_match": value_match,
                "depth_match": depth_match,
                "expected_completed_depth": expected_row.get("completed_depth"),
                "actual_completed_depth": actual_row.get("completed_depth"),
                "expected_elapsed_ms": expected_row.get("elapsed_ms"),
                "actual_elapsed_ms": actual_row.get("elapsed_ms"),
                "latency_ratio": _ratio(
                    expected_row.get("elapsed_ms"), actual_row.get("elapsed_ms")
                ),
                "same_completed_work": depth_match,
                "problems": case_failures,
            }
        )

    equal_work_ratios = [
        row["latency_ratio"]
        for row in rows
        if row.get("same_completed_work") and row.get("latency_ratio") is not None
    ]
    return {
        "mode": expected_identity.get("mode"),
        "fixed_work": fixed_work,
        "workload_digest": expected_identity.get("workload_digest"),
        "comparable_cases": comparable,
        "limited_cases": limited,
        "move_agreement": agreement["move"] / comparable if comparable else None,
        "value_agreement": agreement["value"] / comparable if comparable else None,
        "depth_agreement": agreement["depth"] / comparable if comparable else None,
        "equal_work_latency_ratio_median": _median(equal_work_ratios),
        "rows": rows,
        "failures": failures,
        "warnings": warnings,
        "ok": not failures,
    }


def _values_agree(expected_value: Any, actual_value: Any) -> bool:
    return (
        _finite(expected_value)
        and _finite(actual_value)
        and abs(expected_value - actual_value) <= VALUE_TOLERANCE
    )


def _ratio(expected_ms: Any, actual_ms: Any) -> float | None:
    if not _finite(expected_ms) or expected_ms <= 0 or not _finite(actual_ms):
        return None
    return actual_ms / expected_ms


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2
