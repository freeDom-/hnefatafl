"""Smoke-benchmark the optional online learning-rate controller.

This measures, on CPU with a tiny budget, three equal-budget strategies:

* ``fixed`` — one learning rate for the whole run;
* ``scheduler`` — an inexpensive two-phase step schedule (the rate is halved at
  the midpoint) realised through the existing resume path, not a shipped option;
* ``hypercontroller`` — the optional online controller.

It reports wall time, gradient work, cumulative and final evaluation work, the
effective learning-rate trajectory and the controller's own cost separately from
the evaluation cost. It is a **smoke benchmark, not a strength experiment**: the
evaluation score is the controller's feedback signal, not a held-out strength
evaluation, and a handful of short runs cannot support a strength claim.

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

MODES = ("fixed", "scheduler", "hypercontroller")
STARTING_RATE = 0.001


def base_config(root: Path, *, seed: int, iterations: int, evaluation_games: int) -> dict:
    return {
        "preset": "smoke",
        "ruleset": "brandubh",
        "device": "cpu",
        "seed": seed,
        "iterations": iterations,
        "evaluation_games": evaluation_games,
        "evaluation_frequency": 1,
        "learning_rate": STARTING_RATE,
        "output_dir": str(root / "training"),
        "registry_dir": str(root / "models"),
    }


def controller_config(root: Path, *, seed: int, iterations: int, evaluation_games: int) -> dict:
    config = base_config(root, seed=seed, iterations=iterations, evaluation_games=evaluation_games)
    config.update(
        adaptation="hypercontroller",
        adaptation_lr_candidates="0.0003,0.001,0.003",
        adaptation_cadence=1,
        adaptation_warmup=1,
        adaptation_min_dwell=1,
        adaptation_min_trials=1,
        adaptation_lag=1,
    )
    return config


def summarize(phases: list[tuple[dict, float, int]], *, extra: dict | None = None) -> dict:
    """Aggregate one or more training phases without double-counting a resume.

    Each phase is ``(result, wall_seconds, first_new_iteration)``. A resumed
    phase restores the earlier history, so only the points from
    ``first_new_iteration`` onward count toward that phase's cost.
    """
    last = phases[-1][0]
    cumulative_games = 0
    evaluated_iterations = 0
    for point in last["history"]:
        evaluation = point.get("evaluation")
        if isinstance(evaluation, dict):
            evaluated_iterations += 1
            cumulative_games += int(evaluation.get("game_count", 0))
    evaluation_seconds = 0.0
    adaptation_seconds = 0.0
    for result, _, first_new in phases:
        for point in result["history"]:
            if point.get("iteration", 0) < first_new:
                continue
            performance = point.get("performance", {})
            evaluation_seconds += float(performance.get("evaluation_seconds", 0.0))
            adaptation_seconds += float(performance.get("adaptation_seconds", 0.0))
    final_evaluation = last["metrics"].get("evaluation", {})
    cumulative_updates = sum(int(point.get("updates", 0)) for point in last["history"])
    summary = {
        "wall_seconds": sum(wall for _, wall, _ in phases),
        "updates": cumulative_updates,
        "evaluated_iterations": evaluated_iterations,
        "cumulative_evaluation_games": cumulative_games,
        "final_evaluation_games": int(final_evaluation.get("game_count", 0)),
        "final_evaluation_score": final_evaluation.get("score"),
        "evaluation_seconds": evaluation_seconds,
        "adaptation_seconds": adaptation_seconds,
        "learning_rate_trajectory": [point.get("learning_rate") for point in last["history"]],
    }
    summary.update(extra or {})
    return summary


def run_fixed(root: Path, *, seed: int, iterations: int, evaluation_games: int) -> dict:
    config = base_config(
        root / f"fixed-{seed}", seed=seed, iterations=iterations, evaluation_games=evaluation_games
    )
    started = time.perf_counter()
    result = train(config)
    return summarize([(result, time.perf_counter() - started, 1)])


def run_hypercontroller(root: Path, *, seed: int, iterations: int, evaluation_games: int) -> dict:
    config = controller_config(
        root / f"hypercontroller-{seed}",
        seed=seed,
        iterations=iterations,
        evaluation_games=evaluation_games,
    )
    started = time.perf_counter()
    result = train(config)
    return summarize(
        [(result, time.perf_counter() - started, 1)],
        extra={"adaptation_changes": result.get("adaptation", {}).get("changes")},
    )


def run_scheduler(root: Path, *, seed: int, iterations: int, evaluation_games: int) -> dict:
    """A two-phase step schedule: half the iterations at each of two rates.

    This is the inexpensive baseline the design record compares against. It is
    realised with the existing resume path (not a shipped training option), so it
    pays the resume handover once and its two phases are reported together. The
    resume boundary forces one extra evaluation, so the scheduler is charged for
    more evaluation work than the single-phase modes; that is reported, not hidden.
    """
    midpoint = max(1, iterations // 2)
    first = base_config(
        root / f"scheduler-{seed}" / "phase1",
        seed=seed,
        iterations=midpoint,
        evaluation_games=evaluation_games,
    )
    started = time.perf_counter()
    first_result = train(first)
    first_wall = time.perf_counter() - started
    second = base_config(
        root / f"scheduler-{seed}" / "phase2",
        seed=seed,
        iterations=iterations,
        evaluation_games=evaluation_games,
    )
    second.update(learning_rate=STARTING_RATE / 2, resume=first_result["checkpoint"])
    started = time.perf_counter()
    second_result = train(second)
    second_wall = time.perf_counter() - started
    return summarize(
        [
            (first_result, first_wall, 1),
            (second_result, second_wall, midpoint + 1),
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", type=int, default=3)
    parser.add_argument("--iterations", type=int, default=4)
    parser.add_argument("--evaluation-games", type=int, default=4)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    runners = {
        "fixed": run_fixed,
        "scheduler": run_scheduler,
        "hypercontroller": run_hypercontroller,
    }
    records: list[dict] = []
    failures: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="hnefatafl-adaptation-") as directory:
        root = Path(directory)
        for seed in range(args.seeds):
            for mode in MODES:
                try:
                    summary = runners[mode](
                        root,
                        seed=seed,
                        iterations=args.iterations,
                        evaluation_games=args.evaluation_games,
                    )
                    records.append({"mode": mode, "seed": seed, "status": "completed", **summary})
                except Exception as error:  # noqa: BLE001 - recorded, never swallowed
                    failures.append({"mode": mode, "seed": seed, "error": repr(error)})
    summary = {}
    for mode in MODES:
        rows = [record for record in records if record["mode"] == mode]
        if not rows:
            continue
        summary[mode] = {
            "runs": len(rows),
            "median_wall_seconds": statistics.median(row["wall_seconds"] for row in rows),
            "median_updates": statistics.median(row["updates"] for row in rows),
            "median_cumulative_evaluation_games": statistics.median(
                row["cumulative_evaluation_games"] for row in rows
            ),
            "median_evaluation_seconds": statistics.median(
                row["evaluation_seconds"] for row in rows
            ),
            "median_adaptation_seconds": statistics.median(
                row["adaptation_seconds"] for row in rows
            ),
        }
    report = {
        "kind": "smoke",
        "note": (
            "Tiny CPU budget; the evaluation score is the controller feedback signal, "
            "not a held-out strength evaluation. Not a strength experiment."
        ),
        "held_out_strength_evaluation": "not executed",
        "environment": {"python": platform.python_version(), "platform": platform.platform()},
        "parameters": {
            "seeds": args.seeds,
            "iterations": args.iterations,
            "evaluation_games_per_iteration": args.evaluation_games,
            "starting_learning_rate": STARTING_RATE,
        },
        "runs": records,
        "failures": failures,
        "summary": summary,
    }
    text = json.dumps(report, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
