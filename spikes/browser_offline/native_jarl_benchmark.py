"""Native CPython Jarl benchmark; mirrors the browser benchmark workload.

Runs the shared ``jarl_benchmark`` definitions directly (no Pyodide) and writes a
normalised, machine-readable report that ``jarl_compare`` can compare with a
browser report. Each case is measured independently; a single ``wall_clock``
total is recorded at report level and never attached to an individual case.

Example:
    .venv/bin/python spikes/browser_offline/native_jarl_benchmark.py \
        --suite standard --mode fixed_work --output /tmp/jarl-native.json
"""

from __future__ import annotations

import argparse
import json
import platform
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
    "standard": {"configs": ["quick", "interactive", "standard"], "repetitions": 3, "warmup": 1},
    "extended": {
        "configs": ["quick", "interactive", "standard", "deep", "extended"],
        "repetitions": 5,
        "warmup": 1,
    },
}


def _environment() -> dict:
    return {
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "implementation": platform.python_implementation(),
        "processor": platform.processor() or None,
        "cpu_count": __import__("os").cpu_count(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=sorted(SUITES), default="quick")
    parser.add_argument("--configs", nargs="*", help="Override the suite configuration list")
    parser.add_argument("--mode", choices=list(jb.MODES), default="fixed_time")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--repetitions", type=int, default=None)
    parser.add_argument("--warmup", type=int, default=None)
    parser.add_argument("--positions", type=Path, help="Serialized positions JSON")
    parser.add_argument("--output", type=Path, default=ROOT / "work/jarl-native.json")
    args = parser.parse_args()

    if args.repetitions is not None and args.repetitions < 1:
        parser.error("--repetitions must be at least 1")
    if args.warmup is not None and args.warmup < 0:
        parser.error("--warmup must be non-negative")

    suite = SUITES[args.suite]
    configs = args.configs or suite["configs"]
    unknown = [name for name in configs if name not in jb.BENCHMARK_CONFIGS]
    if unknown:
        parser.error(f"unknown benchmark configuration(s): {unknown}")
    repetitions = args.repetitions if args.repetitions is not None else suite["repetitions"]
    warmup = args.warmup if args.warmup is not None else suite["warmup"]
    positions = json.loads(args.positions.read_text()) if args.positions else jarl_positions()
    names = [case.get("name") for case in positions]
    if len(set(names)) != len(names):
        parser.error("position names must be unique")

    records: list[dict] = []
    started = time.perf_counter()
    for case in positions:
        state = State.from_dict(case["state"])
        digest = jb.position_digest(case)
        for name in configs:
            for _ in range(warmup):
                record = jb.run_case(
                    state,
                    name,
                    mode=args.mode,
                    seed=args.seed,
                    position=case.get("name"),
                    kind=case.get("kind"),
                    position_hash=digest,
                )
                record["phase"] = "warmup"
                records.append(record)
            for repetition in range(repetitions):
                record = jb.run_case(
                    state,
                    name,
                    mode=args.mode,
                    seed=args.seed,
                    position=case.get("name"),
                    kind=case.get("kind"),
                    position_hash=digest,
                )
                record["phase"] = "measured"
                record["repetition"] = repetition
                records.append(record)
    wall_clock = time.perf_counter() - started

    report = jb.build_report(
        kind="native-jarl-benchmark",
        runtime="cpython",
        environment=_environment(),
        positions=positions,
        configs=configs,
        mode=args.mode,
        seed=args.seed,
        repetitions=repetitions,
        warmup=warmup,
        records=records,
        extra={
            "generated": datetime.now(timezone.utc).isoformat(),
            "suite": args.suite,
            "repetitions": repetitions,
            "warmup": warmup,
            "batch_wall_clock_seconds": wall_clock,
        },
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"output": str(args.output), "records": len(records)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
