"""Canonical comparison of two benchmark reports (native vs browser).

This module is the single source of truth for deciding whether two benchmark
runs are comparable, whether every expected measurement was produced, whether
each result is structurally valid, and at which completed work deterministic
parity is actually established.

A normalised report (see ``jarl_benchmark.build_report``) carries an ``identity``
block (workload digest and per-position contracts, effective configurations,
mode, seed, declared repetitions/warmup, engine identity) and a flat list of
per-case ``records`` with a ``status`` classification and repetition index.

The comparison separates three dimensions that must never be conflated:

* **execution** -- the reported result is structurally valid: finite value,
  legal move, correct terminal outcome, coherent status/depth.
* **parity** -- at *equal completed work*, the two runtimes return the same move
  and defender value.
* **acceptance** -- every expected case completed the configured depth on both
  sides, so the fixed-work target is fully met.

Mandatory ``failures`` (non-zero exit for a gate) cover identity, coverage,
duplicates, record-level identity, execution validity and parity at equal work.
``unresolved`` lists acceptance targets that are honestly incomplete (for
example a fixed-work case limited at depth 4 on both runtimes). An unresolved
target never silently becomes a passing completed-work claim.
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
    "repetitions",
    "warmup",
    "engine_identity",
    "effective_configs",
)
WORKLOAD_FIELDS = ("protocol", "digest", "mode", "seed", "configs", "effective_configs", "cases")
CASE_FIELDS = (
    "name",
    "kind",
    "ruleset",
    "position_digest",
    "terminal",
    "winner",
    "legal_moves",
)
STATUS_VALUES = ("completed", "terminal", "deadline_limited", "unsupported", "error", "incomplete")
TERMINAL_VALUES = {"defenders": 1.0, "attackers": -1.0, "draw": 0.0}


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _move_key(move: Any) -> tuple | None:
    if not isinstance(move, dict):
        return None
    source, target = move.get("source"), move.get("target")
    if type(source) is not int or type(target) is not int:
        return None
    return (source, target)


def _index_records(
    records: list[dict[str, Any]], *, side: str, failures: list[str]
) -> tuple[dict[tuple, dict[str, Any]], list[dict[str, Any]]]:
    """Split records into measured and warm-up maps keyed with their repetition.

    A duplicate measured identity is a mandatory failure: silently keeping one
    copy would let a bad later repetition hide behind a good first one.
    """
    measured: dict[tuple, dict[str, Any]] = {}
    warmup: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            failures.append(f"{side}: malformed record entry")
            continue
        phase = record.get("phase", "measured")
        if phase == "warmup":
            warmup.append(record)
            continue
        if phase != "measured":
            failures.append(f"{side}: unsupported record phase {phase!r}")
            continue
        key = (
            record.get("position"),
            record.get("config"),
            record.get("ruleset"),
            record.get("repetition"),
        )
        if key in measured:
            failures.append(f"{side}: duplicate measured record {_label(key)}")
            continue
        measured[key] = record
    return measured, warmup


def _label(key: tuple) -> str:
    return f"{key[0]}/{key[1]}/{key[2]}/rep{key[3]}"


def _identity_failures(side: str, identity: Any) -> list[str]:
    """Require every mandatory identity field to be present and non-empty."""
    failures: list[str] = []
    if not isinstance(identity, dict):
        return [f"{side}: missing identity block"]
    for name in REQUIRED_IDENTITY_FIELDS:
        if name not in identity or identity[name] is None:
            failures.append(f"{side}: missing mandatory identity field {name!r}")
    for name in ("repetitions", "warmup"):
        value = identity.get(name)
        if value is not None and (type(value) is not int or value < 0):
            failures.append(f"{side}: identity.{name} must be a non-negative integer")
    engine = identity.get("engine_identity")
    if isinstance(engine, dict):
        if not engine.get("sha256"):
            failures.append(f"{side}: engine identity is unavailable")
    elif engine is not None:
        failures.append(f"{side}: engine identity is malformed")
    effective = identity.get("effective_configs")
    if effective is not None and (not isinstance(effective, dict) or not effective):
        failures.append(f"{side}: effective_configs must be a non-empty object")
    workload = identity.get("workload")
    if workload is not None:
        if not isinstance(workload, dict):
            failures.append(f"{side}: workload identity is malformed")
        else:
            for field in WORKLOAD_FIELDS:
                if field not in workload:
                    failures.append(f"{side}: workload identity missing {field!r}")
            cases = workload.get("cases")
            if not isinstance(cases, list) or not cases:
                failures.append(f"{side}: workload has no cases")
            else:
                for case in cases:
                    missing = [field for field in CASE_FIELDS if field not in case]
                    if missing:
                        failures.append(
                            f"{side}: workload case {case.get('name')!r} missing {missing}"
                        )
    return failures


def _expected_keys(identity: dict[str, Any], *, side: str, failures: list[str]) -> set[tuple]:
    """Derive the complete expected measured coverage from the workload alone.

    This is deliberately independent of the records a report happens to contain,
    so both reports omitting the same position is still detected.
    """
    workload = identity.get("workload") or {}
    cases = workload.get("cases") or []
    configs = workload.get("configs")
    repetitions = identity.get("repetitions")
    if not isinstance(configs, list) or not configs:
        failures.append(f"{side}: workload declares no configurations")
        return set()
    if type(repetitions) is not int or repetitions < 1:
        failures.append(f"{side}: identity.repetitions must be a positive integer")
        return set()
    expected: set[tuple] = set()
    for case in cases:
        for config in configs:
            for repetition in range(repetitions):
                expected.add((case.get("name"), config, case.get("ruleset"), repetition))
    if not expected:
        failures.append(f"{side}: expected coverage is empty")
    return expected


def _case_contract_map(identity: dict[str, Any]) -> dict[str, dict[str, Any]]:
    workload = identity.get("workload") or {}
    return {case.get("name"): case for case in workload.get("cases") or []}


def _record_identity_failures(
    record: dict[str, Any],
    *,
    side: str,
    key: tuple,
    identity: dict[str, Any],
    contracts: dict[str, dict[str, Any]],
) -> list[str]:
    """Validate that a record actually belongs to the declared workload."""
    problems: list[str] = []
    label = f"{side}: {_label(key)}"
    name, config, ruleset, _repetition = key
    contract = contracts.get(name)
    if contract is None:
        return [f"{label}: position is not part of the declared workload"]
    if record.get("ruleset") != ruleset or contract.get("ruleset") != ruleset:
        problems.append(f"{label}: ruleset mismatch")
    if record.get("position_hash") != contract.get("position_digest"):
        problems.append(f"{label}: record position hash does not match the workload")
    if record.get("mode") != identity.get("mode"):
        problems.append(f"{label}: record mode {record.get('mode')!r} != {identity.get('mode')!r}")
    if record.get("seed") != identity.get("seed"):
        problems.append(f"{label}: record seed {record.get('seed')!r} != {identity.get('seed')!r}")
    effective = (identity.get("effective_configs") or {}).get(config)
    if effective is None:
        problems.append(f"{label}: configuration is not part of the workload")
    elif record.get("effective_config_digest") != _digest(effective):
        problems.append(f"{label}: effective settings digest does not match the workload")
    return problems


def _execution_failures(
    record: dict[str, Any],
    *,
    side: str,
    key: tuple,
    contract: dict[str, Any],
    known_unsupported: set[str],
    expected_depth: int | None,
    expected_time_limit_ms: float | None,
) -> list[str]:
    """Validate one result's structure, independent of the other runtime.

    The required depth and cooperative deadline come from the validated effective
    configuration, never from the record itself, so a record cannot redefine the
    work it was asked to perform.
    """
    problems: list[str] = []
    label = f"{side}: {_label(key)}"
    status = record.get("status")
    if status not in STATUS_VALUES:
        return [f"{label}: unknown status {status!r}"]

    recorded_depth = record.get("expected_depth")
    if expected_depth is not None and recorded_depth != expected_depth:
        problems.append(
            f"{label}: record expected depth {recorded_depth!r} != effective "
            f"configuration depth {expected_depth!r}"
        )
    recorded_budget = record.get("time_limit_ms")
    if (
        expected_time_limit_ms is not None
        and recorded_budget is not None
        and (not _finite(recorded_budget) or abs(recorded_budget - expected_time_limit_ms) > 1e-6)
    ):
        problems.append(
            f"{label}: record budget {recorded_budget!r} ms != effective "
            f"configuration budget {expected_time_limit_ms!r} ms"
        )

    for counter in ("nodes", "evaluations", "cutoffs", "successors"):
        value = record.get(counter)
        if value is not None and (type(value) is not int or value < 0):
            problems.append(f"{label}: malformed work counter {counter}={value!r}")

    if status == "unsupported":
        if key[1] not in known_unsupported:
            problems.append(f"{label}: unexpected unsupported configuration")
        if not record.get("error"):
            problems.append(f"{label}: unsupported case must report an explicit error")
        if record.get("best_move") is not None:
            problems.append(f"{label}: unsupported case must not return a move")
        return problems
    if status == "error":
        problems.append(f"{label}: execution error: {record.get('error') or 'unspecified'}")
        return problems

    if status == "terminal":
        expected_value = contract.get("terminal_value")
        if not contract.get("terminal"):
            problems.append(f"{label}: reported terminal for a non-terminal workload position")
            return problems
        if record.get("best_move") is not None:
            problems.append(f"{label}: terminal best_move must be null")
        if not _finite(record.get("value")) or record.get("value") != expected_value:
            problems.append(
                f"{label}: terminal value {record.get('value')!r} != "
                f"expected defender-relative {expected_value!r}"
            )
        if record.get("completed_depth") not in (None, 0):
            problems.append(f"{label}: terminal search reported a completed depth")
        return problems

    if contract.get("terminal"):
        problems.append(f"{label}: non-terminal result for a terminal workload position")
        return problems

    value = record.get("value")
    if not _finite(value) or not -1 <= value <= 1:
        problems.append(f"{label}: value is not finite in [-1, 1]")
    legal = {_move_key(move) for move in contract.get("legal_moves") or []}
    move = record.get("best_move")
    if move is None:
        problems.append(f"{label}: non-terminal search returned no best move")
    elif _move_key(move) is None:
        problems.append(f"{label}: best move is malformed")
    elif _move_key(move) not in legal:
        problems.append(f"{label}: best move {move} is not legal in the workload position")

    if expected_depth is None:
        problems.append(f"{label}: effective configuration declares no search depth")
    completed = record.get("completed_depth")
    interrupted = record.get("interrupted_depth")
    if completed is not None and (type(completed) is not int or completed < 0):
        problems.append(f"{label}: malformed completed depth {completed!r}")
    if interrupted is not None and (type(interrupted) is not int or interrupted < 1):
        problems.append(f"{label}: invalid interrupted depth {interrupted!r}")

    if status == "completed":
        if interrupted is not None:
            problems.append(f"{label}: completed case also reports an interrupted depth")
        if not isinstance(completed, int) or completed != expected_depth:
            problems.append(
                f"{label}: completed depth {completed!r} != requested {expected_depth!r}"
            )
    elif status == "deadline_limited":
        if interrupted is None:
            problems.append(f"{label}: deadline-limited case has no interrupted depth")
        if not isinstance(completed, int):
            problems.append(f"{label}: deadline-limited case has no completed depth")
        elif expected_depth is not None and completed >= expected_depth:
            problems.append(f"{label}: deadline-limited case claims the requested depth")
    elif status == "incomplete":
        if not isinstance(completed, int):
            problems.append(f"{label}: incomplete case has no completed depth")
        elif interrupted is None and expected_depth is not None and completed >= expected_depth:
            problems.append(f"{label}: incomplete case reached the requested depth")
    return problems


def _digest(value: Any) -> str:
    import jarl_benchmark

    return jarl_benchmark.canonical_config_digest(value)


def _effective_depth(record: dict[str, Any]) -> int | None:
    completed = record.get("completed_depth")
    if type(completed) is not int:
        return None
    return completed


def _parity_applicable(
    expected_row: dict[str, Any], actual_row: dict[str, Any]
) -> tuple[bool, dict[str, Any]]:
    """Whether two results can be checked for deterministic parity.

    Deterministic parity is available whenever both runtimes performed a valid
    search and completed the **same depth**. Node/evaluation counters must not
    suppress this check: differing counters are a performance signal, not a
    reason to skip verifying a deterministic result.
    """
    for row in (expected_row, actual_row):
        if row.get("status") in ("terminal", "unsupported", "error"):
            return False, {"reason": "non-search result"}
    expected_depth = _effective_depth(expected_row)
    actual_depth = _effective_depth(actual_row)
    if expected_depth is None or actual_depth is None or expected_depth < 1 or actual_depth < 1:
        return False, {"reason": "no completed work"}
    if expected_depth != actual_depth:
        return False, {"reason": "different completed depth"}
    return True, {"completed_depth": expected_depth}


def _work_equivalent(
    expected_row: dict[str, Any], actual_row: dict[str, Any]
) -> tuple[bool, dict[str, Any]]:
    """Whether two results represent genuinely equivalent computational work.

    This governs equal-work latency/throughput statistics only. It requires the
    same completed depth and interruption state, and *measured* node/evaluation
    counters that agree. Unavailable counters mean equivalence cannot be claimed.
    """
    applicable, detail = _parity_applicable(expected_row, actual_row)
    if not applicable:
        return False, detail
    expected_interrupted = expected_row.get("interrupted_depth") is not None
    actual_interrupted = actual_row.get("interrupted_depth") is not None
    if expected_interrupted != actual_interrupted:
        return False, {"reason": "different interruption state"}
    counters: dict[str, bool] = {}
    for name in ("nodes", "evaluations"):
        left, right = expected_row.get(name), actual_row.get(name)
        if type(left) is int and type(right) is int:
            counters[name] = left == right
        elif left is None and right is None:
            counters[name] = False
        else:
            return False, {"reason": f"inconsistent {name} counters"}
    if any(value is False for value in counters.values()):
        return False, {"reason": "different or unavailable work counters", "counters": counters}
    return True, {"counters": counters, **detail}


def compare(expected: dict[str, Any], actual: dict[str, Any]) -> dict[str, Any]:
    """Compare an actual report against a reference report."""
    failures: list[str] = []
    warnings: list[str] = []
    unresolved: list[dict[str, Any]] = []

    expected_identity = expected.get("identity") if isinstance(expected, dict) else None
    actual_identity = actual.get("identity") if isinstance(actual, dict) else None
    failures.extend(_identity_failures("expected", expected_identity))
    failures.extend(_identity_failures("actual", actual_identity))
    if failures:
        return _empty_result(expected_identity, failures, warnings, unresolved)

    assert isinstance(expected_identity, dict) and isinstance(actual_identity, dict)
    for name in REQUIRED_IDENTITY_FIELDS:
        if expected_identity.get(name) != actual_identity.get(name):
            failures.append(
                f"identity.{name} mismatch: {actual_identity.get(name)!r} != "
                f"{expected_identity.get(name)!r}"
            )
    if expected_identity.get("workload", {}).get("digest") != actual_identity.get(
        "workload", {}
    ).get("digest"):
        failures.append("workload identity digest mismatch")

    fixed_work = expected_identity.get("mode") == "fixed_work"
    contracts = _case_contract_map(expected_identity)
    known_unsupported = set((expected.get("plan") or {}).get("unsupported") or {})
    if known_unsupported:
        # The reference plan is authoritative; both reports must agree on it.
        if (expected.get("plan") or {}).get("unsupported") != (actual.get("plan") or {}).get(
            "unsupported"
        ):
            failures.append("benchmark plan unsupported-configuration report mismatch")

    expected_measured, expected_warmup = _index_records(
        expected.get("records", []), side="expected", failures=failures
    )
    actual_measured, actual_warmup = _index_records(
        actual.get("records", []), side="actual", failures=failures
    )
    required = _expected_keys(expected_identity, side="expected", failures=failures)
    required_actual = _expected_keys(actual_identity, side="actual", failures=failures)
    required |= required_actual

    missing_expected = sorted(required - set(expected_measured), key=str)
    missing_actual = sorted(required - set(actual_measured), key=str)
    if missing_expected:
        failures.append(
            f"expected report missing {len(missing_expected)} case(s): "
            f"{[_label(key) for key in missing_expected[:5]]}"
        )
    if missing_actual:
        failures.append(
            f"actual report missing {len(missing_actual)} case(s): "
            f"{[_label(key) for key in missing_actual[:5]]}"
        )
    extra_actual = sorted(set(actual_measured) - required, key=str)
    if extra_actual:
        warnings.append(f"actual report has {len(extra_actual)} unexpected case(s)")
    _validate_warmup(expected_identity, expected_warmup, "expected", warnings, len(required))
    _validate_warmup(actual_identity, actual_warmup, "actual", warnings, len(required))

    for key, record in sorted(expected_measured.items(), key=lambda item: str(item[0])):
        failures.extend(
            _record_identity_failures(
                record,
                side="expected",
                key=key,
                identity=expected_identity,
                contracts=contracts,
            )
        )
    for key, record in sorted(actual_measured.items(), key=lambda item: str(item[0])):
        failures.extend(
            _record_identity_failures(
                record,
                side="actual",
                key=key,
                identity=actual_identity,
                contracts=contracts,
            )
        )

    rows: list[dict[str, Any]] = []
    parity_cases = 0
    parity_ok = 0
    acceptance_cases = 0
    acceptance_ok = 0
    equal_work_ratios: list[float] = []
    node_rates: list[dict[str, float | None]] = []
    status_counts = {"expected": {}, "actual": {}}
    effective = expected_identity.get("effective_configs") or {}

    for key in sorted(set(expected_measured) & set(actual_measured), key=str):
        expected_row = expected_measured[key]
        actual_row = actual_measured[key]
        contract = contracts.get(key[0], {})
        config = effective.get(key[1]) or {}
        expected_depth = config.get("depth") if type(config.get("depth")) is int else None
        time_limit = config.get("time_limit")
        expected_time_limit_ms = float(time_limit) * 1000 if _finite(time_limit) else None

        execution_problems = _execution_failures(
            expected_row,
            side="expected",
            key=key,
            contract=contract,
            known_unsupported=known_unsupported,
            expected_depth=expected_depth,
            expected_time_limit_ms=expected_time_limit_ms,
        )
        execution_problems += _execution_failures(
            actual_row,
            side="actual",
            key=key,
            contract=contract,
            known_unsupported=known_unsupported,
            expected_depth=expected_depth,
            expected_time_limit_ms=expected_time_limit_ms,
        )
        failures.extend(execution_problems)
        status_counts["expected"][str(expected_row.get("status"))] = (
            status_counts["expected"].get(str(expected_row.get("status")), 0) + 1
        )
        status_counts["actual"][str(actual_row.get("status"))] = (
            status_counts["actual"].get(str(actual_row.get("status")), 0) + 1
        )

        row: dict[str, Any] = {
            "position": key[0],
            "config": key[1],
            "ruleset": key[2],
            "repetition": key[3],
            "expected_status": expected_row.get("status"),
            "actual_status": actual_row.get("status"),
            "terminal": bool(contract.get("terminal")),
            "configured_depth": expected_depth,
            "expected_completed_depth": expected_row.get("completed_depth"),
            "actual_completed_depth": actual_row.get("completed_depth"),
            "expected_nodes": expected_row.get("nodes"),
            "actual_nodes": actual_row.get("nodes"),
            "expected_evaluations": expected_row.get("evaluations"),
            "actual_evaluations": actual_row.get("evaluations"),
            "expected_elapsed_ms": expected_row.get("elapsed_ms"),
            "actual_elapsed_ms": actual_row.get("elapsed_ms"),
            "execution_valid": not execution_problems,
            "problems": list(execution_problems),
            "acceptance_incomplete": None,
        }

        if contract.get("terminal"):
            acceptance_cases += 1
            if not execution_problems:
                acceptance_ok += 1
            else:
                unresolved.append(
                    {
                        "position": key[0],
                        "config": key[1],
                        "repetition": key[3],
                        "reason": "terminal result invalid",
                    }
                )
            rows.append(row)
            continue

        if expected_row.get("status") == "unsupported" or actual_row.get("status") == "unsupported":
            acceptance_cases += 1
            row["acceptance_incomplete"] = True
            unresolved.append(
                {
                    "position": key[0],
                    "config": key[1],
                    "repetition": key[3],
                    "reason": "unsupported configuration",
                }
            )
            rows.append(row)
            continue
        if expected_row.get("status") == "error" or actual_row.get("status") == "error":
            acceptance_cases += 1
            row["acceptance_incomplete"] = True
            failures.append(f"{_label(key)}: unexpected execution error")
            unresolved.append(
                {
                    "position": key[0],
                    "config": key[1],
                    "repetition": key[3],
                    "reason": "execution error",
                }
            )
            rows.append(row)
            continue

        # Deterministic parity is independent of computational-work counters.
        parity_applicable, parity_detail = _parity_applicable(expected_row, actual_row)
        row["parity_applicable"] = parity_applicable
        row["parity_detail"] = parity_detail
        parity_ok_case = False
        if parity_applicable:
            parity_cases += 1
            move_match = expected_row.get("best_move") == actual_row.get("best_move")
            value_match = _values_agree(expected_row.get("value"), actual_row.get("value"))
            depth_match = expected_row.get("completed_depth") == actual_row.get("completed_depth")
            row["move_match"] = move_match
            row["value_match"] = value_match
            row["depth_match"] = depth_match
            if not (move_match and value_match and depth_match):
                failures.append(
                    f"{_label(key)}: deterministic parity differs "
                    f"(move={move_match}, value={value_match}, depth={depth_match})"
                )
            else:
                parity_ok += 1
                parity_ok_case = True

        # Computational-work equivalence governs performance ratios only.
        equal_work, work_detail = _work_equivalent(expected_row, actual_row)
        row["equal_work"] = equal_work
        row["work_detail"] = work_detail
        if equal_work:
            ratio = _ratio(expected_row.get("elapsed_ms"), actual_row.get("elapsed_ms"))
            row["latency_ratio"] = ratio
            if ratio is not None:
                equal_work_ratios.append(ratio)
            node_rates.append(_node_rates(expected_row, actual_row))
        elif not fixed_work and not parity_applicable:
            warnings.append(
                f"{_label(key)}: fixed-time results are not comparable "
                f"({work_detail.get('reason')})"
            )

        completed_both = (
            expected_depth is not None
            and expected_row.get("status") == "completed"
            and actual_row.get("status") == "completed"
            and expected_row.get("completed_depth") == expected_depth
            and actual_row.get("completed_depth") == expected_depth
        )
        acceptance_cases += 1
        if completed_both and parity_ok_case and not execution_problems:
            acceptance_ok += 1
            row["acceptance_incomplete"] = False
        else:
            row["acceptance_incomplete"] = True
            if not completed_both:
                reason = (
                    "asymmetric completion"
                    if (expected_row.get("status") == "completed")
                    != (actual_row.get("status") == "completed")
                    else f"limited at depth {expected_row.get('completed_depth')}"
                )
            else:
                reason = "deterministic parity not established"
            unresolved.append(
                {
                    "position": key[0],
                    "config": key[1],
                    "repetition": key[3],
                    "reason": reason,
                    "configured_depth": expected_depth,
                    "expected_completed_depth": expected_row.get("completed_depth"),
                    "actual_completed_depth": actual_row.get("completed_depth"),
                }
            )
        rows.append(row)

    if not expected_measured or not actual_measured:
        failures.append("comparison report contains no measured records")

    if failures:
        # A mandatory failure can never coexist with acceptance completion.
        acceptance_complete = False
    elif fixed_work:
        acceptance_complete = (
            bool(required) and acceptance_cases > 0 and acceptance_ok == acceptance_cases
        )
    else:
        # A fixed-time run collects performance evidence; completion is not the
        # acceptance target. It is complete when every compared case executed
        # validly and coverage was complete.
        acceptance_complete = (
            bool(required)
            and acceptance_cases > 0
            and all(row["execution_valid"] for row in rows)
            and not missing_expected
            and not missing_actual
        )
    return {
        "mode": expected_identity.get("mode"),
        "fixed_work": fixed_work,
        "workload_digest": expected_identity.get("workload_digest"),
        "expected_cases": len(required),
        "compared_cases": len(set(expected_measured) & set(actual_measured)),
        "execution_valid_cases": sum(1 for row in rows if row["execution_valid"]),
        "parity_cases": parity_cases,
        "parity_established": parity_ok,
        "acceptance_cases": acceptance_cases,
        "acceptance_complete": acceptance_complete,
        "acceptance_ok_cases": acceptance_ok,
        "unresolved": unresolved,
        "status_counts": status_counts,
        "equal_work_latency_ratio_median": _median(equal_work_ratios),
        "equal_work_latency_ratio_samples": len(equal_work_ratios),
        "equal_work_nodes_per_second": _median_metric(node_rates, "expected_nodes_per_second"),
        "equal_work_actual_nodes_per_second": _median_metric(node_rates, "actual_nodes_per_second"),
        "rows": rows,
        "failures": failures,
        "warnings": warnings,
        "ok": not failures,
    }


def _empty_result(
    identity: Any, failures: list[str], warnings: list[str], unresolved: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "mode": (identity or {}).get("mode") if isinstance(identity, dict) else None,
        "fixed_work": (identity or {}).get("mode") == "fixed_work"
        if isinstance(identity, dict)
        else False,
        "workload_digest": (identity or {}).get("workload_digest")
        if isinstance(identity, dict)
        else None,
        "expected_cases": 0,
        "compared_cases": 0,
        "execution_valid_cases": 0,
        "parity_cases": 0,
        "parity_established": 0,
        "acceptance_cases": 0,
        "acceptance_complete": False,
        "acceptance_ok_cases": 0,
        "unresolved": unresolved,
        "status_counts": {"expected": {}, "actual": {}},
        "equal_work_latency_ratio_median": None,
        "equal_work_latency_ratio_samples": 0,
        "equal_work_nodes_per_second": None,
        "equal_work_actual_nodes_per_second": None,
        "rows": [],
        "failures": failures,
        "warnings": warnings,
        "ok": False,
    }


def _validate_warmup(
    identity: dict[str, Any],
    warmup: list[dict[str, Any]],
    side: str,
    warnings: list[str],
    measured_cases: int,
) -> None:
    """Warm-up records are distinct from measured records and never counted."""
    declared = identity.get("warmup")
    if type(declared) is not int:
        return
    seen: set[tuple] = set()
    for record in warmup:
        key = (
            record.get("position"),
            record.get("config"),
            record.get("ruleset"),
            record.get("repetition"),
        )
        if key in seen:
            warnings.append(f"{side}: duplicate warm-up record {_label(key)}")
        seen.add(key)
    expected = (measured_cases // max(1, identity.get("repetitions") or 1)) * declared
    if declared == 0:
        if warmup:
            warnings.append(f"{side}: warm-up records present despite declaring none")
    elif not warmup:
        warnings.append(f"{side}: no warm-up records despite declaring {declared}")
    elif expected and len(seen) != expected:
        warnings.append(f"{side}: {len(seen)} warm-up records for an expected {expected}")
    for record in warmup:
        if record.get("phase") != "warmup":
            warnings.append(f"{side}: warm-up record missing its phase marker")


def _node_rates(
    expected_row: dict[str, Any], actual_row: dict[str, Any]
) -> dict[str, float | None]:
    def rate(row: dict[str, Any]) -> float | None:
        nodes = row.get("nodes")
        elapsed = row.get("elapsed_ms")
        if type(nodes) is int and _finite(elapsed) and elapsed > 0:
            return nodes / (elapsed / 1000)
        return None

    return {
        "expected_nodes_per_second": rate(expected_row),
        "actual_nodes_per_second": rate(actual_row),
    }


def _median_metric(samples: list[dict[str, float | None]], name: str) -> float | None:
    return _median([sample[name] for sample in samples if sample.get(name) is not None])


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
