"""Measure parallel evaluation progress traffic and monitoring latency.

Run each source in its own process; ``--source`` selects an extracted baseline
package the same way the optimization and parallel-search benchmarks do. The
script builds fixed candidate and Champion snapshots from one seed, so every arm
compares identical weights and every arm can be repeated.

It reports the per-phase progress-message count, the inference batches and
neural evaluations the messages are meant to summarise, wall time and the
observed monitoring gaps. It is a diagnostic for progress reporting, not
playing-strength evidence.

    python scripts/benchmark_evaluation_progress.py --workers 2 4 \
        --monitor on off --repeats 3 --output work/eval-progress.json
    python scripts/benchmark_evaluation_progress.py --source work/main-source \
        --output work/eval-progress-before.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import platform
import statistics
import sys
import time


class LatencySink:
    """Minimal ProgressSink that timestamps the counter updates it receives."""

    def __init__(self):
        self.started = time.monotonic()
        self.ticks: list[float] = []
        self.counters: dict[str, float] = {}

    def phase(self, name, *, iteration=None):
        pass

    def tick(self, **deltas):
        self.ticks.append(time.monotonic())
        for key, value in deltas.items():
            self.counters[key] = self.counters.get(key, 0) + value

    def counts(self, **values):
        self.counters.update(values)

    def latency(self) -> dict:
        if not self.ticks:
            return {"first_seconds": None, "max_gap_seconds": None, "events": 0}
        gaps = [b - a for a, b in zip(self.ticks, self.ticks[1:])]
        return {
            "first_seconds": self.ticks[0] - self.started,
            "last_seconds": self.ticks[-1] - self.started,
            "max_gap_seconds": max(gaps) if gaps else None,
            "median_gap_seconds": statistics.median(gaps) if gaps else None,
            "events": len(self.ticks),
        }


def work_digest(result: dict) -> str:
    payload = {
        "summary": {
            key: result[key]
            for key in ("wins", "losses", "draws", "truncated", "score", "game_count")
        },
        "attacker": result["candidate_as_attacker"]["score"],
        "defender": result["candidate_as_defender"]["score"],
        "games": [
            {
                key: row[key]
                for key in ("pair", "seed", "candidate_side", "winner", "plies", "reason")
            }
            for row in result["games"]
        ],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def flatten_models(performance: dict) -> dict:
    models = performance.get("models") or []
    batches = sum(entry.get("inference_batches", 0) for entry in models)
    evaluations = sum(entry.get("neural_evaluations", 0) for entry in models)
    return {
        "inference_batches": batches,
        "neural_evaluations": evaluations,
        "inference_batch_size_average": evaluations / batches if batches else 0.0,
    }


def run_phase(model, previous, config, *, monitor, pool):
    from hnefatafl.training_evaluation import evaluate_model

    sink = LatencySink() if monitor else None
    started = time.perf_counter()
    result = evaluate_model(model, config, previous=previous, monitor=sink, search_pool=pool)
    seconds = time.perf_counter() - started
    performance = result["performance"]
    counters = {
        **{key: value for key, value in performance.items() if not isinstance(value, list)},
        **flatten_models(performance),
        "wins": result["wins"],
        "losses": result["losses"],
        "draws": result["draws"],
        "truncated": result["truncated"],
        "candidate_score": result["score"],
        "candidate_as_attacker_score": result["candidate_as_attacker"]["score"],
        "candidate_as_defender_score": result["candidate_as_defender"]["score"],
    }
    return {
        "seconds": seconds,
        "work_sha256": work_digest(result),
        "counters": counters,
        "latency": sink.latency() if sink is not None else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--workers", nargs="+", type=int, default=[2, 4])
    parser.add_argument("--monitor", nargs="+", choices=["on", "off"], default=["on", "off"])
    parser.add_argument("--ruleset", default="brandubh", choices=["brandubh", "tablut"])
    parser.add_argument("--device", default="cpu", choices=["cpu", "cuda"])
    parser.add_argument("--channels", type=int, default=32)
    parser.add_argument("--blocks", type=int, default=2)
    parser.add_argument("--games", type=int, default=16)
    parser.add_argument("--iterations", type=int, default=64)
    parser.add_argument("--max-plies", type=int, default=32)
    parser.add_argument("--concurrency", type=int, default=16)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--opponent", default="champion", choices=["champion", "shield_bearer"])
    parser.add_argument("--seed", type=int, default=424242)
    parser.add_argument(
        "--self-play",
        action="store_true",
        help="also measure a self-play phase to detect monitoring regressions",
    )
    args = parser.parse_args()
    sys.path.insert(0, str(args.source.resolve()))
    import torch
    from hnefatafl.models import PolicyValueNet
    from hnefatafl.training import generate_self_play, normalize_config
    from hnefatafl.parallel_search import SearchPool

    torch.set_num_threads(1)
    config = normalize_config(
        {
            "ruleset": args.ruleset,
            "device": args.device,
            "cpu_threads": 1,
            "channels": args.channels,
            "blocks": args.blocks,
            "mixed_precision": False,
            "self_play_mode": "batched",
            "self_play_concurrency": args.concurrency,
            "inference_batch_size": args.batch_size,
            "inference_cache_mode": "off",
            "games": args.games,
            "max_game_plies": args.max_plies,
            "mcts_iterations": args.iterations,
            "mcts_time_limit": 300,
            "evaluation_games": args.games,
            "evaluation_iterations": args.iterations,
            "evaluation_max_plies": args.max_plies,
            "evaluation_time_limit": 300,
            "evaluation_concurrency": args.concurrency,
            "evaluation_batch_size": args.batch_size,
            "evaluation_opponent": args.opponent,
            "evaluation_mode": "batched",
        }
    )
    # Fixed snapshots: the candidate and the Champion differ by a fixed offset,
    # so every arm evaluates exactly the same two models.
    torch.manual_seed(args.seed)
    model = (
        PolicyValueNet(args.ruleset, config["channels"], config["blocks"]).to(args.device).eval()
    )
    previous = None
    if args.opponent == "champion":
        previous = PolicyValueNet(args.ruleset, config["channels"], config["blocks"])
        previous.load_state_dict(model.state_dict())
        with torch.no_grad():
            for parameter in previous.parameters():
                parameter.add_(0.01)
        previous = previous.to(args.device).eval()
    weights = {key: value.detach().clone() for key, value in model.state_dict().items()}

    arms = [(workers, monitor) for workers in args.workers for monitor in args.monitor]
    results: list[dict] = []
    for workers, monitor in arms:
        pool = SearchPool(workers) if workers else None
        try:
            for repetition in range(args.repeats + 1):
                model.load_state_dict(weights)
                model.eval()
                torch.manual_seed(args.seed)
                sample = run_phase(
                    model,
                    previous,
                    {**config, "search_workers": workers},
                    monitor=monitor == "on",
                    pool=pool,
                )
                sample.update(workers=workers, monitor=monitor, repetition=repetition)
                if repetition:
                    results.append(sample)
        finally:
            if pool is not None:
                pool.close()
    if args.self_play:
        for workers, monitor in arms:
            pool = SearchPool(workers) if workers else None
            try:
                for repetition in range(args.repeats + 1):
                    model.load_state_dict(weights)
                    model.eval()
                    torch.manual_seed(args.seed)
                    sink = LatencySink() if monitor == "on" else None
                    started = time.perf_counter()
                    _, performance = generate_self_play(
                        model,
                        {**config, "search_workers": workers},
                        seed=args.seed,
                        monitor=sink,
                        search_pool=pool,
                    )
                    seconds = time.perf_counter() - started
                    if repetition:
                        results.append(
                            {
                                "kind": "self_play",
                                "workers": workers,
                                "monitor": monitor,
                                "repetition": repetition,
                                "seconds": seconds,
                                "counters": {
                                    **{
                                        key: value
                                        for key, value in performance.items()
                                        if not isinstance(value, list)
                                    },
                                    **flatten_models(performance),
                                },
                                "latency": sink.latency() if sink is not None else None,
                            }
                        )
            finally:
                if pool is not None:
                    pool.close()

    groups: dict[tuple, list[dict]] = {}
    for row in results:
        groups.setdefault(
            (row.get("kind", "evaluation"), row["workers"], row["monitor"]), []
        ).append(row)
    summary = []
    for (kind, workers, monitor), samples in sorted(groups.items()):
        messages = [row["counters"].get("search_progress_messages", 0) for row in samples]
        batches = [row["counters"].get("inference_batches", 0) for row in samples]
        entry = {
            "kind": kind,
            "workers": workers,
            "monitor": monitor,
            "samples": [row["seconds"] for row in samples],
            "median_seconds": statistics.median(row["seconds"] for row in samples),
            "progress_messages": messages,
            "inference_batches": batches,
            "work_hashes": sorted({row["work_sha256"] for row in samples if "work_sha256" in row}),
            "latency": [row["latency"] for row in samples if row["latency"]],
        }
        summary.append(entry)
    payload = {
        "protocol": (
            "One discarded warm-up sample per arm; the rest retained. Fixed weights and seeds. "
            "Progress messages are the parent-applied count; latency is measured in the parent "
            "sink and is wall-clock, so it is only meaningful on an unloaded machine."
        ),
        "source": str(args.source.resolve()),
        "configuration": config,
        "environment": {"platform": platform.platform(), "python": sys.version},
        "summary": summary,
        "runs": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n")
    for entry in summary:
        median_batches = (
            statistics.median(entry["inference_batches"]) if entry["inference_batches"] else 0
        )
        median_messages = (
            statistics.median(entry["progress_messages"]) if entry["progress_messages"] else 0
        )
        ratio = median_messages / median_batches if median_batches else 0.0
        print(
            f"{entry['kind']:10s} workers={entry['workers']} monitor={entry['monitor']:3s} "
            f"median={entry['median_seconds']:.2f}s messages={entry['progress_messages']} "
            f"batches={entry['inference_batches']} msg/batch={ratio:.3f}",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
