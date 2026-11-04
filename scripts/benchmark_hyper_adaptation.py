"""Smoke-benchmark the optional online learning-rate controller.

This measures, on CPU with a tiny budget, the difference between a fixed learning
rate and the HyperController: wall time, gradient work, evaluation games, the
effective learning-rate trajectory and the controller's own cost. It is a smoke
benchmark, **not** a strength experiment: a handful of short runs cannot support a
claim about play strength, and the fixed-opponent score saturates quickly.

Run in a fresh process without other benchmarks or tests:

    OMP_NUM_THREADS=1 .venv/bin/python scripts/benchmark_hyper_adaptation.py \
        --output docs/benchmarks/online-hyperparameter-adaptation.json
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hnefatafl.training import train  # noqa: E402


def run_one(root: Path, *, seed: int, mode: str, iterations: int, evaluation_games: int) -> dict:
    config = {
        "preset": "smoke",
        "ruleset": "brandubh",
        "device": "cpu",
        "seed": seed,
        "iterations": iterations,
        "evaluation_games": evaluation_games,
        "evaluation_frequency": 1,
        "learning_rate": 0.001,
        "output_dir": str(root / f"{mode}-{seed}" / "training"),
        "registry_dir": str(root / f"{mode}-{seed}" / "models"),
    }
    if mode == "hypercontroller":
        config.update(
            adaptation="hypercontroller",
            adaptation_lr_candidates="0.0003,0.001,0.003",
            adaptation_cadence=1,
            adaptation_warmup=1,
            adaptation_min_dwell=1,
            adaptation_min_trials=1,
            adaptation_lag=1,
        )
    started = time.perf_counter()
    result = train(config)
    wall_seconds = time.perf_counter() - started
    history = result["history"]
    performance = [point.get("performance", {}) for point in history]
    evaluation_seconds = sum(float(item.get("evaluation_seconds", 0.0)) for item in performance)
    adaptation_seconds = sum(float(item.get("adaptation_seconds", 0.0)) for item in performance)
    evaluation = result["metrics"].get("evaluation", {})
    return {
        "mode": mode,
        "seed": seed,
        "wall_seconds": wall_seconds,
        "updates": result["metrics"].get("updates", 0),
        "evaluation_games": evaluation.get("game_count", 0),
        "evaluation_score": evaluation.get("score"),
        "evaluation_seconds": evaluation_seconds,
        "adaptation_seconds": adaptation_seconds,
        "learning_rate_trajectory": [point.get("learning_rate") for point in history],
        "adaptation_changes": (
            result.get("adaptation", {}).get("changes") if mode == "hypercontroller" else None
        ),
    }


def summarize(records: list[dict]) -> dict:
    summary = {}
    for mode in ("fixed", "hypercontroller"):
        rows = [record for record in records if record["mode"] == mode]
        if not rows:
            continue
        summary[mode] = {
            "runs": len(rows),
            "median_wall_seconds": statistics.median(row["wall_seconds"] for row in rows),
            "median_updates": statistics.median(row["updates"] for row in rows),
            "evaluation_games": rows[0]["evaluation_games"],
            "median_evaluation_seconds": statistics.median(
                row["evaluation_seconds"] for row in rows
            ),
            "median_adaptation_seconds": statistics.median(
                row["adaptation_seconds"] for row in rows
            ),
        }
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", type=int, default=3)
    parser.add_argument("--iterations", type=int, default=4)
    parser.add_argument("--evaluation-games", type=int, default=4)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    records = []
    with tempfile.TemporaryDirectory(prefix="hnefatafl-adaptation-") as directory:
        root = Path(directory)
        for seed in range(args.seeds):
            for mode in ("fixed", "hypercontroller"):
                records.append(
                    run_one(
                        root,
                        seed=seed,
                        mode=mode,
                        iterations=args.iterations,
                        evaluation_games=args.evaluation_games,
                    )
                )
    report = {
        "kind": "smoke",
        "note": "Tiny CPU budget; not a strength experiment.",
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
        },
        "parameters": {
            "seeds": args.seeds,
            "iterations": args.iterations,
            "evaluation_games": args.evaluation_games,
        },
        "runs": records,
        "summary": summarize(records),
    }
    text = json.dumps(report, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
