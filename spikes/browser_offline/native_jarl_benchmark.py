"""Native CPython Jarl benchmark; mirrors the browser benchmark workload.

Runs the shared ``jarl_benchmark`` definitions directly (no Pyodide) and writes a
machine-readable report. The browser verifier reads the same serialized positions
and produces a comparable report, and ``compare_jarl_benchmarks.py`` merges them.

Example:
    .venv/bin/python spikes/browser_offline/native_jarl_benchmark.py \
        --suite standard --output /tmp/jarl-native.json
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

SPIKE = Path(__file__).resolve().parent
ROOT = SPIKE.parents[1]
for path in (str(SPIKE), str(ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

import jarl_benchmark as jb  # noqa: E402
from fixtures import jarl_positions  # noqa: E402
from hnefatafl.engine import State  # noqa: E402

SUITES = {
    "quick": {"configs": ["quick"], "repetitions": 1, "warmup": 1},
    "standard": {
        "configs": ["quick", "interactive", "standard"],
        "repetitions": 3,
        "warmup": 1,
    },
    "extended": {
        "configs": ["quick", "interactive", "standard", "deep", "extended"],
        "repetitions": 5,
        "warmup": 1,
    },
}


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = fraction * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def summarise(records: list[dict]) -> list[dict]:
    groups: dict[tuple[str, str], list[dict]] = {}
    for record in records:
        groups.setdefault((record["config"], record["ruleset"]), []).append(record)
    summary = []
    for (config, ruleset), rows in sorted(groups.items()):
        measured = [row for row in rows if row.get("phase") == "measured"]
        elapsed = [row["elapsed_ms"] for row in measured]
        depths = [row["completed_depth"] for row in measured if row["completed_depth"]]
        nodes = [row["nodes"] for row in measured if isinstance(row["nodes"], int)]
        errors = sum(1 for row in measured if row.get("error"))
        summary.append(
            {
                "config": config,
                "ruleset": ruleset,
                "samples": len(measured),
                "errors": errors,
                "elapsed_ms_median": statistics.median(elapsed) if elapsed else None,
                "elapsed_ms_p90": percentile(elapsed, 0.9),
                "completed_depth_median": statistics.median(depths) if depths else None,
                "nodes_median": statistics.median(nodes) if nodes else None,
            }
        )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=sorted(SUITES), default="quick")
    parser.add_argument("--configs", nargs="*", help="Override the suite configuration list")
    parser.add_argument("--mode", choices=["fixed_time", "fixed_work"], default="fixed_time")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--repetitions", type=int, default=None)
    parser.add_argument("--warmup", type=int, default=None)
    parser.add_argument("--positions", type=Path, help="Serialized positions JSON")
    parser.add_argument("--output", type=Path, default=ROOT / "work/jarl-native.json")
    args = parser.parse_args()

    suite = SUITES[args.suite]
    configs = args.configs or suite["configs"]
    repetitions = args.repetitions if args.repetitions is not None else suite["repetitions"]
    warmup = args.warmup if args.warmup is not None else suite["warmup"]
    positions = json.loads(args.positions.read_text()) if args.positions else jarl_positions()
    plan = jb.benchmark_plan()

    records: list[dict] = []
    started = time.perf_counter()
    for case in positions:
        state = State.from_dict(case["state"])
        for name in configs:
            for _ in range(warmup):
                record = jb.run_case(state, name, mode=args.mode, seed=args.seed)
                record.update(position=case["name"], kind=case.get("kind"), phase="warmup")
                records.append(record)
            for repetition in range(repetitions):
                record = jb.run_case(state, name, mode=args.mode, seed=args.seed)
                record.update(
                    position=case["name"],
                    kind=case.get("kind"),
                    phase="measured",
                    repetition=repetition,
                )
                records.append(record)
    elapsed = time.perf_counter() - started

    report = {
        "kind": "native-jarl-benchmark",
        "generated": datetime.now(timezone.utc).isoformat(),
        "environment": {
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "processor": platform.processor(),
            "cpu_count": __import__("os").cpu_count(),
        },
        "plan": plan,
        "suite": args.suite,
        "configs": configs,
        "mode": args.mode,
        "seed": args.seed,
        "repetitions": repetitions,
        "warmup": warmup,
        "positions": len(positions),
        "wall_clock_seconds": elapsed,
        "records": records,
        "summary": summarise(records),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"output": str(args.output), "records": len(records)}, indent=2))


if __name__ == "__main__":
    main()
