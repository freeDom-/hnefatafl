"""Deterministic controller-level benchmark for Champion-aware adaptation.

This is **not** a playing-strength experiment. It drives the HyperController with
a synthetic evaluation stream whose true best learning rate changes at Champion
promotions, and compares three strategies at equal evaluation budget:

* ``fixed`` — one learning rate for the whole run.
* ``reset`` — the original behavior: the controller is rebuilt (learned
  predictors, history and counters cleared) at every promotion.
* ``persistent`` — the corrected behavior: promotions are idempotent transitions
  that keep the learned predictors, dwell timers and effective rate.

The synthetic score is a deterministic function of the true best rate plus seeded
noise, so the comparison measures how quickly each strategy tracks the true best
rate, its controller CPU overhead, its state size and how often it changes the
rate. It says nothing about gameplay strength; a strength claim would need a
matched held-out evaluation, which this benchmark does not run.

Usage:

    OMP_NUM_THREADS=1 .venv/bin/python scripts/benchmark_champion_adaptation.py \
        --output docs/benchmarks/champion-adaptation.json
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import time
from pathlib import Path

from hnefatafl.hyper_adaptation import (
    MAX_EVENTS,
    MAX_REWARD_HISTORY,
    HyperController,
)

CANDIDATES = [0.0003, 0.001, 0.003]
GAMES = 4
NOISE = 0.08


def true_best_index(evaluation: int, promotion_every: int) -> int:
    """A slowly changing optimum, so a strategy must re-learn after promotions.

    The optimum alternates between the extreme candidates, so a fixed rate is
    never optimal and only an adaptive strategy can track it.
    """
    generation = evaluation // promotion_every
    return (0, 2)[generation % 2]


def synthetic_score(index: int, best: int, rng: random.Random) -> float:
    distance = abs(index - best)
    base = 0.75 - 0.2 * distance
    return max(0.0, min(1.0, base + rng.gauss(0.0, NOISE)))


def _controller(seed: int) -> HyperController:
    return HyperController(
        candidates=CANDIDATES,
        lag=1,
        cadence=1,
        warmup=1,
        min_dwell=1,
        min_trials=1,
        initial_index=1,
        seed=seed,
        evaluation_identity="champion:benchmark",
        active_champion_identity="champion-0-benchmark",
        min_dwell_iterations=0,
    )


def run(
    mode: str,
    *,
    seed: int = 0,
    evaluations: int = 400,
    promotion_every: int = 10,
) -> dict:
    if mode not in ("fixed", "reset", "persistent"):
        raise ValueError("mode must be fixed, reset or persistent")
    rng = random.Random(seed)
    controller = None if mode == "fixed" else _controller(seed + 7)
    effective_index = 1
    changes = 0
    regret = 0.0
    controller_seconds = 0.0
    generations = 0
    for evaluation in range(evaluations):
        best = true_best_index(evaluation, promotion_every)
        # A fixed strategy always uses the initial rate; the controller uses the
        # rate it selected for this interval.
        if controller is not None:
            effective_index = controller.effective_index
        score = synthetic_score(effective_index, best, rng)
        oracle = synthetic_score(best, best, rng)
        regret += oracle - score
        if controller is None:
            continue
        promotion = None
        if evaluation and evaluation % promotion_every == 0:
            generations += 1
            promotion = {
                "iteration": evaluation,
                "champion_iteration": evaluation - promotion_every,
                "opponent_identity": f"champion:benchmark:incumbent-{evaluation}",
                "active_champion_identity": f"champion-{evaluation}-benchmark",
            }
        started = time.perf_counter()
        event = controller.observe(
            iteration=evaluation,
            score=score,
            provenance={"completed_games": GAMES},
            promotion=promotion,
        )
        controller_seconds += time.perf_counter() - started
        if event.get("changed"):
            changes += 1
        if mode == "reset" and promotion is not None:
            # Emulate the original destructive reset: keep only the effective rate
            # and rebuild every learned structure.
            preserved = controller.effective_index
            controller = _controller(seed + 7 + generations)
            controller.set_effective_index(preserved, iteration=evaluation)
    return {
        "mode": mode,
        "seed": seed,
        "evaluations": evaluations,
        "generations": generations,
        "mean_regret": regret / evaluations,
        "changes": changes,
        "controller_seconds": controller_seconds,
        "contexts": len(controller.contexts) if controller else 0,
        "events": len(controller.events) if controller else 0,
        "reward_history": len(controller.rewards) if controller else 0,
        "final_index": effective_index,
    }


def summarize(rows: list[dict]) -> dict:
    by_mode: dict[str, list[dict]] = {}
    for row in rows:
        by_mode.setdefault(row["mode"], []).append(row)
    summary = {}
    for mode, entries in by_mode.items():
        summary[mode] = {
            "median_mean_regret": statistics.median(row["mean_regret"] for row in entries),
            "median_changes": statistics.median(row["changes"] for row in entries),
            "median_controller_seconds": statistics.median(
                row["controller_seconds"] for row in entries
            ),
            "median_contexts": statistics.median(row["contexts"] for row in entries),
            "median_events": statistics.median(row["events"] for row in entries),
            "median_reward_history": statistics.median(row["reward_history"] for row in entries),
        }
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--seeds", type=int, default=5)
    parser.add_argument("--evaluations", type=int, default=400)
    parser.add_argument("--promotion-every", type=int, default=10)
    arguments = parser.parse_args()
    rows = [
        run(
            mode,
            seed=seed,
            evaluations=arguments.evaluations,
            promotion_every=arguments.promotion_every,
        )
        for seed in range(arguments.seeds)
        for mode in ("fixed", "reset", "persistent")
    ]
    report = {
        "kind": "synthetic-controller-benchmark",
        "held_out_strength_evaluation": "not executed",
        "games_per_evaluation": GAMES,
        "noise": NOISE,
        "evaluations": arguments.evaluations,
        "promotion_every": arguments.promotion_every,
        "bounds": {"max_events": MAX_EVENTS, "max_reward_history": MAX_REWARD_HISTORY},
        "rows": rows,
        "summary": summarize(rows),
    }
    text = json.dumps(report, indent=2, allow_nan=False)
    if arguments.output:
        arguments.output.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
