"""Native/browser neural MCTS benchmark definitions, aggregation and comparison.

Workloads reuse the deterministic positions from ``fixtures.jarl_positions`` so
the native and browser harnesses measure identical states, and report actual
completed work instead of nominal budgets. Fixed-simulation runs must complete
their budget; fixed-time runs deliberately complete different work on different
runtimes and are never compared as if equal.

This module imports only the pure engine and is used by the native benchmark, the
browser report comparator and the tests.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import sys
from pathlib import Path

SPIKE = Path(__file__).resolve().parent
ROOT = SPIKE.parents[1]
for path in (str(SPIKE), str(ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

import mcts_workload  # noqa: E402
from hnefatafl.mcts_driver import MAX_SIMULATIONS  # noqa: E402

SIMULATION_BUDGETS = (128, 256, 512, 1024, 2048, 4096)
TIME_BUDGETS = (0.25, 0.5, 1.0, 2.0, 5.0, 10.0)
POSITION_KINDS = ("opening", "midgame", "endgame", "tactical", "repetition")
MODES = ("fixed_simulations", "fixed_time")
# A bounded quick workload for development; the extended matrix is for owner
# hardware and must be selected explicitly.
QUICK = {
    "kinds": ("opening", "tactical"),
    "simulations": [128, 256],
    "time_budgets": [0.5, 1.0],
    "repetitions": 1,
    "warmup": 1,
}
EXTENDED = {
    "kinds": POSITION_KINDS,
    "simulations": list(SIMULATION_BUDGETS),
    "time_budgets": list(TIME_BUDGETS),
    "repetitions": 3,
    "warmup": 1,
}


def select_positions(rulesets: list[str], kinds: tuple[str, ...] = POSITION_KINDS) -> list[dict]:
    # Imported lazily so the browser search worker can import this module for the
    # canonical workload digest without staging the fixture generator.
    from fixtures import jarl_positions

    return [
        case for case in jarl_positions() if case["ruleset"] in rulesets and case["kind"] in kinds
    ]


def cases_for(modes: list[str], simulations: list[int], time_budgets: list[float]) -> list[dict]:
    cases = []
    for mode in modes:
        budgets = simulations if mode == "fixed_simulations" else time_budgets
        for budget in budgets:
            cases.append({"mode": mode, "budget": budget})
    return cases


def digest_workload(
    positions: list[dict],
    cases: list[dict],
    *,
    seed: int,
    exploration: float,
    model_sha256: str | None,
) -> str:
    """Canonical benchmark workload identity shared by both harnesses."""
    payload = {
        "kind": "mcts-benchmark",
        "schema": 1,
        "positions": [entry["state"] if "state" in entry else entry for entry in positions],
        # Normalise numeric types so a JSON `1` and a Python `1.0` describe the
        # same fixed-time budget and produce the same digest.
        "cases": [
            {
                "mode": item["mode"],
                "budget": float(item["budget"])
                if item["mode"] == "fixed_time"
                else int(item["budget"]),
            }
            for item in cases
        ],
        "seed": int(seed),
        "exploration": float(exploration),
        "model_onnx_sha256": model_sha256,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def settings_for(mode: str, budget: float, *, seed: int, exploration: float) -> dict:
    if mode == "fixed_simulations":
        return {
            "iterations": int(budget),
            "time_limit": 10.0,
            "seed": seed,
            "exploration": exploration,
        }
    if mode == "fixed_time":
        return {
            "iterations": MAX_SIMULATIONS,
            "time_limit": float(budget),
            "seed": seed,
            "exploration": exploration,
        }
    raise ValueError(f"Unknown benchmark mode {mode!r}")


def case_key(record: dict) -> tuple:
    return (
        record["ruleset"],
        record["position"],
        record["mode"],
        record["budget"],
    )


def _is_int(value) -> bool:
    return type(value) is int


def _is_number(value) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float))


def _finite(value) -> bool:
    return _is_number(value) and math.isfinite(float(value))


def _hex64(value) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def expected_keys(report: dict) -> list[tuple]:
    """Independently derive every measured record key from the workload."""
    workload = report.get("workload")
    if not isinstance(workload, dict):
        raise ValueError("report has no workload definition")
    try:
        validated = mcts_workload.validate_workload(workload)
    except ValueError as error:
        raise ValueError(str(error)) from None
    positions = validated["positions"]
    names = []
    for entry in positions:
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
            raise ValueError("workload position entry is malformed")
        names.append(entry["name"])
    if len(set(names)) != len(names):
        raise ValueError("workload position names are not unique")
    cases = workload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("workload has no cases")
    repetitions = workload.get("repetitions")
    if not _is_int(repetitions) or repetitions < 1:
        raise ValueError("workload repetitions must be a positive integer")
    keys = []
    for position in positions:
        for case in cases:
            mode = case.get("mode")
            budget = case.get("budget")
            if mode not in MODES:
                raise ValueError(f"unsupported benchmark mode {mode!r}")
            if mode == "fixed_simulations":
                if not _is_int(budget) or budget < 1:
                    raise ValueError("fixed-simulation budget must be a positive integer")
            elif not _finite(budget) or budget <= 0:
                raise ValueError("fixed-time budget must be a positive number")
            for repetition in range(repetitions):
                keys.append((position["ruleset"], position["name"], mode, budget, repetition))
    return keys


def _validate_record(record: dict, workload: dict, position_rulesets: dict) -> list[str]:
    failures: list[str] = []
    name = record.get("position")
    mode = record.get("mode")
    budget = record.get("budget")
    position = position_rulesets.get(name)
    if position is None:
        return [f"{name}: result does not belong to a workload position"]
    if record.get("ruleset") != position:
        failures.append(f"{name}: result ruleset does not match the workload position")
    settings = settings_for(
        mode, budget, seed=workload.get("seed"), exploration=workload.get("exploration")
    )
    requested = settings["iterations"]
    if record.get("requested_simulations") != requested:
        failures.append(f"{name}/{mode}/{budget}: requested simulations were redefined")
    completed = record.get("completed_simulations")
    if not _is_int(completed) or completed < 0:
        failures.append(
            f"{name}/{mode}/{budget}: completed simulations must be a non-negative integer"
        )
        completed = None
    if completed is not None and completed > requested:
        failures.append(f"{name}/{mode}/{budget}: completed simulations exceed the budget")
    elapsed = record.get("elapsed_ms")
    if not _finite(elapsed) or elapsed <= 0:
        failures.append(f"{name}/{mode}/{budget}: elapsed time must be finite and positive")
    reason = record.get("completion_reason")
    if reason not in (
        "simulations",
        "deadline",
        "immediate_win",
        "terminal",
        "insufficient_budget",
    ):
        failures.append(f"{name}/{mode}/{budget}: unexpected completion reason {reason!r}")
    if not isinstance(record.get("deadline_cutoff"), bool):
        failures.append(f"{name}/{mode}/{budget}: deadline_cutoff must be a boolean")
    for field in ("neural_requests", "prediction_reuses", "root_children"):
        if not _is_int(record.get(field)) or record[field] < 0:
            failures.append(f"{name}/{mode}/{budget}: {field} must be a non-negative integer")
    if mode == "fixed_simulations":
        if reason == "immediate_win":
            if completed not in (None, 0):
                failures.append(f"{name}: immediate win claimed completed simulations")
        elif completed != requested:
            failures.append(
                f"{name}/{mode}/{budget}: fixed-work search completed {completed} of {requested}"
            )
        if record.get("deadline_cutoff"):
            failures.append(f"{name}/{mode}/{budget}: fixed-work search was deadline limited")
    return failures


def validate_report(report: dict, side: str) -> dict:
    """Validate one benchmark report and every raw measured record."""
    failures: list[str] = []

    def fail(message: str) -> None:
        failures.append(f"{side}: {message}")

    if not isinstance(report, dict):
        return {"failures": [f"{side}: report is not an object"]}
    if report.get("kind") != "mcts-benchmark":
        fail("kind is not mcts-benchmark")
    if report.get("schema") != 1:
        fail(f"unsupported schema {report.get('schema')!r}")
    if not isinstance(report.get("runtime"), str) or not report["runtime"]:
        fail("runtime is missing")
    identity = report.get("engine_identity")
    if not isinstance(identity, dict) or not _hex64(identity.get("sha256")):
        fail("engine source identity is missing or malformed")
    if not _hex64(report.get("workload_digest")):
        fail("workload digest is missing or malformed")
    workload = report.get("workload")
    if not isinstance(workload, dict):
        fail("workload definition is missing")
        workload = {}
    model = report.get("model")
    if not isinstance(model, dict) or not _hex64(model.get("onnx_sha256")):
        fail("model identity is missing or malformed")
    report_failures = report.get("failures")
    if not isinstance(report_failures, list) or any(
        not isinstance(item, str) for item in report_failures
    ):
        fail("failures must be a list of strings")
    elif report_failures:
        fail("the report itself records failures")
    try:
        keys = expected_keys(report)
    except ValueError as error:
        fail(str(error))
        keys = []
    position_rulesets = {}
    for entry in workload.get("positions") or []:
        if isinstance(entry, dict) and isinstance(entry.get("name"), str):
            position_rulesets[entry["name"]] = entry.get("ruleset")
    expected = set(keys)
    records = report.get("records")
    if not isinstance(records, list):
        fail("records must be a list")
        records = []
    seen = set()
    for record in records:
        if not isinstance(record, dict):
            fail("record is not an object")
            continue
        key = (
            record.get("ruleset"),
            record.get("position"),
            record.get("mode"),
            record.get("budget"),
            record.get("repetition"),
        )
        if key in seen:
            fail(
                f"{record.get('position')}/{record.get('mode')}/{record.get('budget')}: duplicate measured repetition"
            )
            continue
        seen.add(key)
        if key not in expected:
            fail(
                f"{record.get('position')}/{record.get('mode')}/{record.get('budget')}: unexpected measured record"
            )
            continue
        if record.get("ok") is not True:
            fail(
                f"{record.get('position')}/{record.get('mode')}/{record.get('budget')}: repetition failed"
            )
            continue
        failures.extend(
            f"{side}: {failure}"
            for failure in _validate_record(record, workload, position_rulesets)
        )
    missing = sorted(expected - seen)
    if missing:
        fail(f"{len(missing)} expected measured repetition(s) are missing")
    return {
        "failures": failures,
        "records": records,
        "workload": workload,
        "model": model,
        "identity": identity.get("sha256") if isinstance(identity, dict) else None,
    }


def summarize(records: list[dict]) -> dict:
    """Aggregate validated records; failures are reported, never hidden."""
    groups: dict[tuple, list[dict]] = {}
    for record in records:
        groups.setdefault(case_key(record), []).append(record)
    rows = []
    for key, entries in sorted(groups.items()):
        valid = [entry for entry in entries if entry.get("ok")]
        completed = [entry["completed_simulations"] for entry in valid]
        elapsed = [entry["elapsed_ms"] for entry in valid]
        rates = [
            entry["completed_simulations"] / (entry["elapsed_ms"] / 1000.0)
            for entry in valid
            if entry["elapsed_ms"] > 0
        ]
        row = {
            "ruleset": key[0],
            "position": key[1],
            "mode": key[2],
            "budget": key[3],
            "runtime": entries[0].get("runtime"),
            "repetitions": len(entries),
            "valid_repetitions": len(valid),
            "failed_repetitions": len(entries) - len(valid),
            "requested_simulations": entries[0].get("requested_simulations"),
            "completed_median": statistics.median(completed) if completed else None,
            "completed_min": min(completed) if completed else None,
            "elapsed_median_ms": statistics.median(elapsed) if elapsed else None,
            "simulations_per_second": statistics.median(rates) if rates else None,
            "completion_reason": sorted({entry.get("completion_reason") for entry in valid}),
            "deadline_cutoffs": sum(1 for entry in valid if entry.get("deadline_cutoff")),
            "neural_requests_median": statistics.median(
                [entry["neural_requests"] for entry in valid]
            )
            if valid
            else None,
            "prediction_reuses_median": statistics.median(
                [entry["prediction_reuses"] for entry in valid]
            )
            if valid
            else None,
            "root_children_median": statistics.median([entry["root_children"] for entry in valid])
            if valid
            else None,
        }
        for field in (
            "neural_wait_ms",
            "python_action_ms",
            "traversal_ms",
            "encode_ms",
            "legal_generation_ms",
            "unattributed_ms",
            "inference_ms",
            "policy_extract_ms",
            "serialize_ms",
            "parse_ms",
            "response_bytes",
            "deadline_overshoot_ms",
        ):
            values = [entry[field] for entry in valid if entry.get(field) is not None]
            row[field] = statistics.median(values) if values else None
        rows.append(row)
    return {
        "rows": rows,
        "valid_records": sum(len(group) for group in groups.values())
        - sum(len(group) - len([e for e in group if e.get("ok")]) for group in groups.values()),
        "failed_records": sum(
            1 for group in groups.values() for entry in group if not entry.get("ok")
        ),
        "fixed_simulations_complete": all(
            row["valid_repetitions"] == row["repetitions"]
            and row["completed_median"] == row["requested_simulations"]
            for row in rows
            if row["mode"] == "fixed_simulations"
        ),
    }


def compare_reports(native: dict, browser: dict) -> dict:
    """Validate raw measured records, then compare equal-work results."""
    native_validation = validate_report(native, "native")
    browser_validation = validate_report(browser, "browser")
    failures = list(native_validation["failures"]) + list(browser_validation["failures"])
    notes: list[str] = []
    if native_validation.get("identity") != browser_validation.get("identity"):
        failures.append("engine source identity differs between the runs")
    if native.get("workload_digest") != browser.get("workload_digest"):
        failures.append("workload digest differs between the runs")
    native_workload = native_validation.get("workload") or {}
    browser_workload = browser_validation.get("workload") or {}
    for field in (
        "positions",
        "cases",
        "seed",
        "exploration",
        "repetitions",
        "warmup",
        "selection",
    ):
        if native_workload.get(field) != browser_workload.get(field):
            failures.append(f"workload {field} differs between the runs")
    native_model = (native_validation.get("model") or {}).get("onnx_sha256")
    browser_model = (browser_validation.get("model") or {}).get("onnx_sha256")
    if native_model != browser_model:
        failures.append("model digest differs between the runs")
    native_records = native_validation.get("records") or []
    browser_records = browser_validation.get("records") or []
    native_summary = summarize(native_records)
    browser_summary = summarize(browser_records)
    rows = []
    equal_work = 0
    for key in sorted(
        {case_key(record) for record in native_records}
        | {case_key(record) for record in browser_records}
    ):
        left = [record for record in native_records if case_key(record) == key and record.get("ok")]
        right = [
            record for record in browser_records if case_key(record) == key and record.get("ok")
        ]
        entry = {
            "ruleset": key[0],
            "position": key[1],
            "mode": key[2],
            "budget": key[3],
            "native_repetitions": len(left),
            "browser_repetitions": len(right),
        }
        if not left or not right:
            entry["ok"] = False
            rows.append(entry)
            continue
        same_work = all(
            record["completed_simulations"] == left[0]["completed_simulations"]
            and record["neural_requests"] == left[0]["neural_requests"]
            for record in right
        ) and all(
            record["completed_simulations"] == left[0]["completed_simulations"]
            and record["neural_requests"] == left[0]["neural_requests"]
            for record in left
        )
        entry["equal_work"] = same_work
        entry["native_completed"] = statistics.median(
            record["completed_simulations"] for record in left
        )
        entry["browser_completed"] = statistics.median(
            record["completed_simulations"] for record in right
        )
        entry["native_sps"] = statistics.median(
            record["completed_simulations"] / (record["elapsed_ms"] / 1000.0) for record in left
        )
        entry["browser_sps"] = statistics.median(
            record["completed_simulations"] / (record["elapsed_ms"] / 1000.0) for record in right
        )
        entry["native_latency_ms"] = statistics.median(record["elapsed_ms"] for record in left)
        entry["browser_latency_ms"] = statistics.median(record["elapsed_ms"] for record in right)
        entry["completion_reasons"] = sorted(
            {record["completion_reason"] for record in left + right}
        )
        if same_work:
            equal_work += 1
            if entry["native_sps"] and entry["browser_sps"]:
                entry["browser_to_native_ratio"] = entry["browser_sps"] / entry["native_sps"]
            if key[2] == "fixed_time":
                notes.append(f"{key[1]}/{key[3]}s: equal completed work in a fixed-time case")
        elif key[2] == "fixed_time":
            notes.append(
                f"{key[1]}/{key[3]}s: fixed-time runs completed different work "
                f"({entry['native_completed']} vs {entry['browser_completed']}); "
                "no throughput ratio is reported"
            )
        rows.append(entry)
    ratios = [
        row["browser_to_native_ratio"]
        for row in rows
        if row.get("equal_work") and row.get("browser_to_native_ratio") is not None
    ]
    fixed_work_incomplete = [
        failure for failure in failures if "fixed-work search completed" in failure
    ]
    report = {
        "kind": "mcts-benchmark-comparison",
        "schema": 2,
        "failures": failures,
        "notes": notes,
        "execution_validity": {
            "native": not native_validation["failures"],
            "browser": not browser_validation["failures"],
            "native_records": native_summary["valid_records"],
            "browser_records": browser_summary["valid_records"],
            "failed_records": native_summary["failed_records"] + browser_summary["failed_records"],
        },
        "workload_completion": {
            "native_fixed_work_complete": native_summary["fixed_simulations_complete"]
            and not any("native" in failure for failure in fixed_work_incomplete),
            "browser_fixed_work_complete": browser_summary["fixed_simulations_complete"]
            and not any("browser" in failure for failure in fixed_work_incomplete),
        },
        "performance": {
            "equal_work_cases": equal_work,
            "cases": rows,
            "median_browser_to_native_ratio": statistics.median(ratios) if ratios else None,
            "note": (
                "Equal completed simulations and neural evaluations are required "
                "before a latency or throughput ratio is reported."
            ),
        },
        "summary": {"native": native_summary, "browser": browser_summary},
        "aspirational_target": {
            "simulations": 1024,
            "seconds": 2.0,
            "simulations_per_second": 512,
            "note": "aspirational only; not a merge gate or a claim",
        },
        "authoritative": False,
    }
    report["acceptance"] = not failures
    report["ok"] = report["acceptance"]
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("native", type=Path)
    parser.add_argument("browser", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = compare_reports(
        json.loads(args.native.read_text()), json.loads(args.browser.read_text())
    )
    text = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text)
    print(
        json.dumps(
            {
                "ok": report["ok"],
                "equal_work_cases": report["performance"]["equal_work_cases"],
                "median_browser_to_native_ratio": report["performance"][
                    "median_browser_to_native_ratio"
                ],
                "failures": report["failures"],
                "notes": report["notes"],
            },
            indent=2,
        )
    )
    sys.exit(0 if report["ok"] else 1)


if __name__ == "__main__":
    main()
