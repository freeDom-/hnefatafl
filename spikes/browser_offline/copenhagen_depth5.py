"""Reassess the three unresolved Copenhagen depth-5 Jarl positions.

Native CPython measurements are taken with the existing Jarl analyzer and a
larger, explicit bounded deadline. Browser execution remains an owner command
through verify_jarl.mjs; this script does not fabricate browser evidence.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

SPIKE = Path(__file__).resolve().parent
ROOT = SPIKE.parents[1]
for path in (str(SPIKE), str(ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

import jarl_benchmark as jb  # noqa: E402
from fixtures import jarl_positions  # noqa: E402
from hnefatafl.engine import State  # noqa: E402

CASES = ("opening", "midgame", "repetition")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--budgets", default="5,10,30", help="seconds")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    budgets = [float(value) for value in args.budgets.split(",") if value]
    positions = [
        entry
        for entry in jarl_positions()
        if entry["ruleset"] == "copenhagen" and entry["kind"] in CASES
    ]
    records = []
    for position in positions:
        state = State.from_dict(position["state"])
        for budget in budgets:
            config = jb.resolve_config("standard", mode="fixed_time")
            config["time_limit"] = budget
            config["seed"] = 7
            started = time.perf_counter()
            try:
                analysis = jb.analyze_position(state, config)
                error = None
            except Exception as exc:  # noqa: BLE001 - reported per case
                analysis = {}
                error = f"{type(exc).__name__}: {exc}"
            elapsed = (time.perf_counter() - started) * 1000
            records.append(
                {
                    "position": position["name"],
                    "kind": position["kind"],
                    "time_limit_s": budget,
                    "elapsed_ms": elapsed,
                    "completed_depth": analysis.get("completed_depth"),
                    "interrupted_depth": analysis.get("interrupted_depth"),
                    "completed": analysis.get("completed_depth") == 5,
                    "deadline_limited": analysis.get("interrupted_depth") is not None,
                    "error": error,
                }
            )
    report = {
        "kind": "copenhagen-depth5-reassessment",
        "schema": 1,
        "runtime": "native-cpython",
        "environment": {
            "python": sys.version.split()[0],
            "implementation": sys.implementation.name,
        },
        "requested_depth": 5,
        "budgets_seconds": budgets,
        "records": records,
        "browser_status": "blocked: owner must run verify_jarl.mjs on a staged runtime",
        "authoritative": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
