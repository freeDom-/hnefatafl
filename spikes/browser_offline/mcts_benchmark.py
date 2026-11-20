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
import statistics
import sys
from pathlib import Path

SPIKE = Path(__file__).resolve().parent
ROOT = SPIKE.parents[1]
for path in (str(SPIKE), str(ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

from hnefatafl.mcts_driver import MAX_SIMULATIONS  # noqa: E402

SIMULATION_BUDGETS = (128, 256, 512, 1024, 2048, 4096)
TIME_BUDGETS = (0.25, 0.5, 1.0, 2.0, 5.0, 10.0)
POSITION_KINDS = ("opening", "midgame", "endgame", "tactical")
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


def summarize(records: list[dict]) -> dict:
    """Aggregate measured records without inventing missing metrics."""
    groups: dict[tuple, list[dict]] = {}
    for record in records:
        groups.setdefault(case_key(record), []).append(record)
    rows = []
    for key, entries in sorted(groups.items()):
        runtime = entries[0].get("runtime")
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
            "runtime": runtime,
            "repetitions": len(entries),
            "valid_repetitions": len(valid),
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
            "neural_wait_ms_median",
            "python_action_ms_median",
            "traversal_ms_median",
            "encode_ms_median",
            "legal_generation_ms_median",
            "unattributed_ms_median",
            "inference_ms_median",
            "deadline_overshoot_ms_median",
        ):
            values = [entry[field] for entry in valid if entry.get(field) is not None]
            row[field] = statistics.median(values) if values else None
        rows.append(row)
    completed_pairs = [
        (row["budget"], row["completed_median"])
        for row in rows
        if row["mode"] == "fixed_simulations"
    ]
    return {
        "rows": rows,
        "fixed_simulations_complete": all(
            row["completed_median"] == row["requested_simulations"]
            for row in rows
            if row["mode"] == "fixed_simulations"
        ),
        "fixed_simulations_cases": len(completed_pairs),
    }


def compare_reports(native: dict, browser: dict) -> dict:
    """Compare fixed-work equal-work results and report fixed-time separately."""
    failures: list[str] = []
    notes: list[str] = []
    summary_native = native.get("summary") or summarize(native.get("records", []))
    summary_browser = browser.get("summary") or summarize(browser.get("records", []))
    native_rows = {case_key(row): row for row in summary_native["rows"]}
    browser_rows = {case_key(row): row for row in summary_browser["rows"]}
    missing = sorted(set(native_rows) - set(browser_rows))
    extra = sorted(set(browser_rows) - set(native_rows))
    if missing:
        failures.append(f"browser report is missing {len(missing)} workload case(s)")
    if extra:
        failures.append(f"browser report has {len(extra)} unexpected workload case(s)")
    if native.get("engine_identity", {}).get("sha256") != browser.get("engine_identity", {}).get(
        "sha256"
    ):
        failures.append("engine source identity differs between the runs")
    if native.get("workload_digest") != browser.get("workload_digest"):
        failures.append("workload digest differs between the runs")
    rows = []
    for key in sorted(set(native_rows) & set(browser_rows)):
        left, right = native_rows[key], browser_rows[key]
        entry = {
            "ruleset": key[0],
            "position": key[1],
            "mode": key[2],
            "budget": key[3],
            "native_completed_median": left["completed_median"],
            "browser_completed_median": right["completed_median"],
            "native_sps": left["simulations_per_second"],
            "browser_sps": right["simulations_per_second"],
            "equal_work": left["completed_median"] == right["completed_median"],
        }
        if (
            entry["equal_work"]
            and left["simulations_per_second"]
            and right["simulations_per_second"]
        ):
            entry["browser_to_native_ratio"] = (
                right["simulations_per_second"] / left["simulations_per_second"]
            )
        rows.append(entry)
    equal = [row for row in rows if row["equal_work"] and row.get("browser_to_native_ratio")]
    ratios = [row["browser_to_native_ratio"] for row in equal]
    if not equal:
        notes.append("no equal-work cases; no throughput ratio is reported")
    fixed_work_incomplete = [
        row
        for row in summary_browser["rows"]
        if row["mode"] == "fixed_simulations"
        and row["completed_median"] != row["requested_simulations"]
    ]
    for row in fixed_work_incomplete:
        failures.append(
            f"{row['ruleset']}/{row['position']}/{row['budget']}: browser completed "
            f"{row['completed_median']} of {row['requested_simulations']} simulations"
        )
    report = {
        "kind": "mcts-benchmark-comparison",
        "schema": 1,
        "failures": failures,
        "notes": notes,
        "cases": rows,
        "equal_work_cases": len(equal),
        "median_browser_to_native_ratio": statistics.median(ratios) if ratios else None,
        "aspirational_target": {
            "simulations": 1024,
            "seconds": 2.0,
            "simulations_per_second": 512,
            "note": "aspirational only; not a merge gate or a claim",
        },
        "authoritative": False,
    }
    report["ok"] = not failures
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
                "equal_work_cases": report["equal_work_cases"],
                "median_browser_to_native_ratio": report["median_browser_to_native_ratio"],
                "failures": report["failures"],
                "notes": report["notes"],
            },
            indent=2,
        )
    )
    for row in report["cases"]:
        print(json.dumps(row, sort_keys=True))
    sys.exit(0 if report["ok"] else 1)


if __name__ == "__main__":
    main()
