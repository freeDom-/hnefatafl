"""Compare a native and a browser Jarl benchmark report.

Reads the JSON produced by ``native_jarl_benchmark.py`` and ``verify_jarl.mjs``
(or two native runs on different hosts) and reports per-case best-move and value
agreement plus latency ratios. Deterministic fixed-work searches should agree
exactly; fixed-time searches may differ because they complete different work, so
the report distinguishes the two and never presents a latency ratio as a speedup
when the completed depth differs.

Example:
    .venv/bin/python spikes/browser_offline/compare_jarl_benchmarks.py \
        /tmp/jarl-native.json /tmp/jarl-browser.json --output /tmp/jarl-compare.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def key(record: dict) -> tuple:
    return (record.get("position"), record.get("config"), record.get("ruleset"))


def measured(records: list[dict]) -> list[dict]:
    return [row for row in records if row.get("phase", "measured") == "measured"]


def first_by_key(records: list[dict]) -> dict[tuple, dict]:
    result: dict[tuple, dict] = {}
    for row in measured(records):
        result.setdefault(key(row), row)
    return result


def compare(native: dict, browser: dict) -> dict:
    native_rows = first_by_key(native["records"])
    browser_rows = first_by_key(browser["records"])
    rows = []
    move_match = value_match = comparable = 0
    for case_key, browser_row in sorted(browser_rows.items(), key=lambda item: str(item[0])):
        native_row = native_rows.get(case_key)
        if native_row is None:
            continue
        comparable += 1
        move_ok = browser_row.get("best_move") == native_row.get("best_move")
        native_value = native_row.get("value")
        browser_value = browser_row.get("value")
        value_ok = (
            native_value is not None
            and browser_value is not None
            and abs(native_value - browser_value) <= 1e-9
        )
        move_match += int(move_ok)
        value_match += int(value_ok)
        native_elapsed = native_row.get("elapsed_ms")
        browser_elapsed = browser_row.get("elapsed_ms")
        rows.append(
            {
                "position": case_key[0],
                "config": case_key[1],
                "ruleset": case_key[2],
                "mode": browser_row.get("mode"),
                "native_best_move": native_row.get("best_move"),
                "browser_best_move": browser_row.get("best_move"),
                "move_match": move_ok,
                "native_value": native_value,
                "browser_value": browser_value,
                "value_match": value_ok,
                "native_completed_depth": native_row.get("completed_depth"),
                "browser_completed_depth": browser_row.get("completed_depth"),
                "native_elapsed_ms": native_elapsed,
                "browser_elapsed_ms": browser_elapsed,
                "latency_ratio": (
                    browser_elapsed / native_elapsed
                    if native_elapsed and browser_elapsed and native_elapsed > 0
                    else None
                ),
                "same_completed_work": native_row.get("completed_depth")
                == browser_row.get("completed_depth"),
            }
        )
    return {
        "plan_match": native.get("plan", {}).get("configs")
        == browser.get("plan", {}).get("configs"),
        "comparable_cases": comparable,
        "move_agreement": move_match / comparable if comparable else None,
        "value_agreement": value_match / comparable if comparable else None,
        "rows": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("native", type=Path)
    parser.add_argument("browser", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    native = json.loads(args.native.read_text())
    browser = json.loads(args.browser.read_text())
    comparison = compare(native, browser)
    report = {
        "native": {"kind": native.get("kind"), "mode": native.get("mode")},
        "browser": {
            "kind": browser.get("kind"),
            "mode": browser.get("mode"),
            "profile": browser.get("profile"),
        },
        "comparison": comparison,
    }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                "comparable_cases": comparison["comparable_cases"],
                "move_agreement": comparison["move_agreement"],
                "value_agreement": comparison["value_agreement"],
                "plan_match": comparison["plan_match"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
