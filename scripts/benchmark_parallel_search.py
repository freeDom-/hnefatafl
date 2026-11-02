"""Alternating-pair A/B collection for the parallel-search worker pool.

Runs the existing optimization harness as fresh subprocesses, alternating arm
order inside every pair, discarding one warm-up sample per process and retaining
all measured samples. Use it to reproduce the reported CPU and GPU numbers; the
harness itself still does the measuring.

    python scripts/benchmark_parallel_search.py --baseline-source work/main-source \\
        --scenarios selfplay evaluation --output work/parallel-search-cpu.json
    python scripts/benchmark_parallel_search.py --device cuda --baseline-source work/main-source \\
        --scenarios gpu-selfplay --output work/parallel-search-gpu.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import time


SMALL_SELF_PLAY = {
    "games": 4,
    "max_game_plies": 8,
    "mcts_iterations": 32,
    "self_play_concurrency": 4,
    "inference_batch_size": 4,
}
SMALL_EVALUATION = {
    "evaluation_games": 8,
    "evaluation_iterations": 32,
    "evaluation_max_plies": 16,
    "evaluation_concurrency": 8,
    "evaluation_batch_size": 8,
}
LARGE_SELF_PLAY = {
    "games": 12,
    "max_game_plies": 16,
    "mcts_iterations": 48,
    "self_play_concurrency": 12,
    "inference_batch_size": 12,
}
LARGE_EVALUATION = {
    "evaluation_games": 12,
    "evaluation_iterations": 48,
    "evaluation_max_plies": 16,
    "evaluation_concurrency": 12,
    "evaluation_batch_size": 12,
}
GPU_SELF_PLAY = {
    "games": 64,
    "max_game_plies": 150,
    "mcts_iterations": 128,
    "self_play_concurrency": 64,
    "inference_batch_size": 64,
}
GPU_EVALUATION = {
    "evaluation_games": 64,
    "evaluation_iterations": 64,
    "evaluation_max_plies": 32,
    "evaluation_concurrency": 64,
    "evaluation_batch_size": 64,
}

#: Scenario -> (harness feature, extra harness arguments, candidate feature).
SCENARIOS = {
    "selfplay": ("selfplay", SMALL_SELF_PLAY, "workers-selfplay"),
    "evaluation": ("evaluation", SMALL_EVALUATION, "workers"),
    "selfplay-large": ("selfplay", LARGE_SELF_PLAY, "workers-selfplay"),
    "evaluation-large": ("evaluation", LARGE_EVALUATION, "workers"),
    "gpu-selfplay": ("selfplay", GPU_SELF_PLAY, "workers-selfplay"),
    "gpu-evaluation": ("evaluation", GPU_EVALUATION, "workers"),
}

#: Worker counts measured for every candidate scenario.
WORKER_ARMS = (0, 1, 2, 4)


def budget_arguments(budgets: dict) -> list[str]:
    arguments = []
    for key, value in budgets.items():
        arguments += ["--" + key.replace("_", "-"), str(value)]
    return arguments


def arm_command(
    python: str,
    harness: Path,
    source: Path,
    variant: str,
    feature: str,
    ruleset: str,
    budgets: dict,
    device: str,
    workers: int | None,
    output: Path,
    champion: bool,
    flush_seconds: float | None,
    evaluation_mode: str | None,
) -> list[str]:
    command = [
        python,
        str(harness),
        "--feature",
        feature,
        "--variant",
        variant,
        "--source",
        str(source),
        "--ruleset",
        ruleset,
        "--device",
        device,
        "--repeats",
        "1",
        "--output",
        str(output),
        *budget_arguments(budgets),
    ]
    if workers is not None:
        command += ["--workers", str(workers)]
        if flush_seconds is not None:
            command += ["--flush-seconds", str(flush_seconds)]
    if evaluation_mode:
        command += ["--evaluation-mode", evaluation_mode]
    if champion:
        command.append("--champion")
    return command


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-source", type=Path, required=True)
    parser.add_argument(
        "--candidate-source", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument(
        "--scenarios", nargs="+", choices=sorted(SCENARIOS), default=["selfplay", "evaluation"]
    )
    parser.add_argument(
        "--rulesets", nargs="+", choices=["brandubh", "tablut", "copenhagen"], default=["brandubh"]
    )
    parser.add_argument("--device", choices=["cpu", "cuda"], default="cpu")
    parser.add_argument("--pairs", type=int, default=3)
    parser.add_argument("--workers", nargs="+", type=int, default=list(WORKER_ARMS))
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--work", type=Path, default=Path("work/parallel-search"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--reuse",
        action="store_true",
        help="aggregate the existing --work samples instead of running the harness again",
    )
    parser.add_argument(
        "--skip-main",
        action="store_true",
        help="omit the extracted-baseline arm when a 0-worker arm already reproduces it",
    )
    parser.add_argument("--champion", action="store_true")
    parser.add_argument(
        "--flush-seconds",
        type=float,
        default=None,
        help="merge window for candidate arms that use parent-owned inference",
    )
    parser.add_argument(
        "--evaluation-mode",
        choices=["sequential", "batched", "auto"],
        default=None,
        help="evaluation_mode for every arm, e.g. batched to compare batched evaluators",
    )
    args = parser.parse_args()
    harness = Path(__file__).resolve().parent / "benchmark_training_optimization.py"
    args.work.mkdir(parents=True, exist_ok=True)
    baseline_source = args.baseline_source.resolve()
    candidate_source = args.candidate_source.resolve()
    arms: list[tuple[str, Path, str, int | None]] = [
        *([] if args.skip_main else [("main", baseline_source, "baseline", None)]),
        *[
            (f"workers{workers}", candidate_source, "candidate", workers)
            for workers in args.workers
        ],
    ]
    results: list[dict] = []
    for scenario in args.scenarios:
        baseline_feature, budgets, candidate_feature = SCENARIOS[scenario]
        for ruleset in args.rulesets:
            for pair in range(1, args.pairs + 1):
                order = list(arms)
                if pair % 2 == 0:
                    order.reverse()
                for label, source, variant, workers in order:
                    feature = baseline_feature if variant == "baseline" else candidate_feature
                    stem = f"{scenario}-{ruleset}-pair{pair}-{label}"
                    json_path = args.work / f"{stem}.json"
                    log_path = args.work / f"{stem}.log"
                    command = arm_command(
                        args.python,
                        harness,
                        source,
                        variant,
                        feature,
                        ruleset,
                        budgets,
                        args.device,
                        workers,
                        json_path,
                        args.champion,
                        args.flush_seconds,
                        args.evaluation_mode,
                    )
                    if args.reuse and json_path.exists():
                        print(f"[{time.strftime('%F %T')}] {stem} (reused)", flush=True)
                    else:
                        print(f"[{time.strftime('%F %T')}] {stem}", flush=True)
                        with log_path.open("w") as log:
                            completed = subprocess.run(
                                command, stdout=log, stderr=subprocess.STDOUT, check=False
                            )
                        if completed.returncode or not json_path.exists():
                            print(f"  FAILED: see {log_path}", file=sys.stderr, flush=True)
                            results.append(
                                {
                                    "scenario": scenario,
                                    "ruleset": ruleset,
                                    "pair": pair,
                                    "arm": label,
                                    "failed": True,
                                }
                            )
                            continue
                    payload = json.loads(json_path.read_text())
                    sample = payload["samples"][0]
                    results.append(
                        {
                            "scenario": scenario,
                            "ruleset": ruleset,
                            "pair": pair,
                            "arm": label,
                            "workers": workers,
                            "seconds": sample["seconds"],
                            "work_sha256": sample["work_sha256"],
                            "counters": sample["counters"],
                            "warmup_seconds": payload["warmup_seconds"],
                            "source": str(source),
                        }
                    )
    groups: dict[tuple, list[dict]] = {}
    for row in results:
        groups.setdefault((row["scenario"], row["ruleset"], row["arm"]), []).append(row)
    summary = []
    for (scenario, ruleset, arm), samples in sorted(groups.items()):
        samples = [row for row in samples if "seconds" in row]
        if not samples:
            continue
        reference = [
            row
            for row in results
            if row["scenario"] == scenario
            and row["ruleset"] == ruleset
            and row["arm"] == "main"
            and "seconds" in row
        ]
        entry = {
            "scenario": scenario,
            "ruleset": ruleset,
            "arm": arm,
            "median_seconds": statistics.median(row["seconds"] for row in samples),
            "samples": [row["seconds"] for row in samples],
            "work_hashes": sorted({row["work_sha256"] for row in samples}),
        }
        if reference:
            entry["speedup_vs_main"] = (
                statistics.median(row["seconds"] for row in reference) / entry["median_seconds"]
            )
        summary.append(entry)
    payload = {
        "protocol": (
            "Alternating arm order inside every independent pair, one fresh benchmark process "
            "per arm, one discarded warm-up sample per process, all measured samples retained. "
            "Median seconds per arm; speedup is the main-baseline median divided by the arm median."
        ),
        "device": args.device,
        "champion": args.champion,
        "pairs": args.pairs,
        "environment": {
            "platform": platform.platform(),
            "python": sys.version,
        },
        "summary": summary,
        "runs": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n")
    for entry in summary:
        speedup = entry.get("speedup_vs_main")
        suffix = f" speedup {speedup:.3f}x" if speedup else ""
        print(
            f"{entry['scenario']:18s} {entry['ruleset']:11s} {entry['arm']:9s} "
            f"median {entry['median_seconds']:.4f}s{suffix}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
