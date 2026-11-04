"""Measure parallel evaluation progress traffic and monitoring latency.

Run each source in its own process; ``--source`` selects an extracted baseline
package the same way the optimization and parallel-search benchmarks do. The
script builds fixed candidate and Champion snapshots from one seed, so every arm
compares identical weights.

Monitoring on and off are measured as alternating pairs on one warmed worker
pool per worker count: both modes are warmed once and discarded, then each pair
runs one off and one on sample in an order that reverses every pair. This keeps
CPU frequency scaling, host load and pool startup from favouring one arm. The
report retains per-arm absolute times and adds paired absolute/percentage
overhead.

    python scripts/benchmark_evaluation_progress.py --workers 2 4 \
        --repeats 3 --output work/eval-progress.json
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


def alternating_order(pair: int) -> tuple[str, str]:
    """Monitoring modes for one pair, reversing which arm runs first.

    ``pair`` is zero-based: even pairs run monitoring off first, odd pairs run it
    on first. Both arms are always measured with the same budgets.
    """
    return ("off", "on") if pair % 2 == 0 else ("on", "off")


def pair_overhead(off_seconds: float, on_seconds: float) -> dict:
    """Absolute and relative monitoring cost of one paired measurement."""
    absolute = on_seconds - off_seconds
    return {
        "absolute_difference_seconds": absolute,
        "percentage_overhead": (absolute / off_seconds * 100.0) if off_seconds else None,
    }


def run_paired_arms(kind: str, workers: int, pairs: int, warmup, measure):
    """Warm both modes, then measure alternating off/on pairs.

    ``warmup(mode)`` runs one discarded sample and is excluded from the report.
    ``measure(mode, pair)`` returns one measured sample dict with at least
    ``seconds``, ``counters``, ``work_sha256`` and ``latency``. Returns the pair
    records and the flat measured samples (one off and one on per pair).
    """
    for mode in ("off", "on"):
        warmup(mode)
    records: list[dict] = []
    samples: list[dict] = []
    for pair in range(pairs):
        order = alternating_order(pair)
        measured = {mode: measure(mode, pair) for mode in order}
        samples.extend(measured[mode] for mode in order)
        records.append(
            {
                "kind": kind,
                "workers": workers,
                "pair": pair + 1,
                "order": list(order),
                "off": measured["off"],
                "on": measured["on"],
                **pair_overhead(measured["off"]["seconds"], measured["on"]["seconds"]),
            }
        )
    return records, samples


def summarize_runs(results: list[dict]) -> list[dict]:
    """Per-arm absolute timings and traffic, grouped by kind/workers/monitor."""
    groups: dict[tuple, list[dict]] = {}
    for row in results:
        groups.setdefault(
            (row.get("kind", "evaluation"), row["workers"], row["monitor"]), []
        ).append(row)
    summary = []
    for (kind, workers, monitor), samples in sorted(groups.items()):
        messages = [row["counters"].get("search_progress_messages", 0) for row in samples]
        batches = [row["counters"].get("inference_batches", 0) for row in samples]
        evaluations = [row["counters"].get("neural_evaluations", 0) for row in samples]
        summary.append(
            {
                "kind": kind,
                "workers": workers,
                "monitor": monitor,
                "samples": [row["seconds"] for row in samples],
                "median_seconds": statistics.median(row["seconds"] for row in samples),
                "progress_messages": messages,
                "inference_batches": batches,
                "forward_passes": batches,
                "neural_evaluations": evaluations,
                "messages_per_batch": [
                    message / batch if batch else 0.0 for message, batch in zip(messages, batches)
                ],
                "work_hashes": sorted(
                    {row["work_sha256"] for row in samples if row.get("work_sha256")}
                ),
                "latency": [row["latency"] for row in samples if row.get("latency")],
            }
        )
    return summary


def summarize_pairs(pair_records: list[dict]) -> list[dict]:
    """Paired monitoring overhead per kind and worker count."""
    groups: dict[tuple, list[dict]] = {}
    for record in pair_records:
        groups.setdefault((record["kind"], record["workers"]), []).append(record)
    summary = []
    for (kind, workers), records in sorted(groups.items()):
        absolutes = [record["absolute_difference_seconds"] for record in records]
        percentages = [
            record["percentage_overhead"]
            for record in records
            if record["percentage_overhead"] is not None
        ]
        summary.append(
            {
                "kind": kind,
                "workers": workers,
                "pairs": len(records),
                "median_absolute_difference_seconds": statistics.median(absolutes),
                "median_percentage_overhead": statistics.median(percentages)
                if percentages
                else None,
                "absolute_differences_seconds": absolutes,
                "percentage_overheads": percentages,
            }
        )
    return summary


def reset_model(model, weights: dict, seed: int) -> None:
    """Reload the fixed snapshot so every arm starts from identical weights.

    No optimizer is created or stepped anywhere in this benchmark, so this is
    the only state the measurement loop touches.
    """
    import torch

    model.load_state_dict(weights)
    model.eval()
    torch.manual_seed(seed)


def self_play_digest(games: list) -> str:
    payload = [
        {
            "winner": result["winner"],
            "plies": result["plies"],
            "simulations": result["simulations"],
        }
        for _, result in games
    ]
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


class LatencySink:
    """Minimal ProgressSink that timestamps the counter updates it receives."""

    def __init__(self):
        self.started = time.monotonic()
        self.ticks: list[float] = []
        self.first_active_seconds: float | None = None
        self.counters: dict[str, float] = {}

    def phase(self, name, *, iteration=None):
        pass

    def tick(self, **deltas):
        self.ticks.append(time.monotonic())
        for key, value in deltas.items():
            self.counters[key] = self.counters.get(key, 0) + value

    def counts(self, **values):
        if self.first_active_seconds is None and values.get("games_active"):
            self.first_active_seconds = time.monotonic() - self.started
        self.counters.update(values)

    def latency(self) -> dict:
        if not self.ticks:
            return {
                "first_seconds": None,
                "max_gap_seconds": None,
                "events": 0,
                "first_active_seconds": self.first_active_seconds,
            }
        gaps = [b - a for a, b in zip(self.ticks, self.ticks[1:])]
        return {
            "first_seconds": self.ticks[0] - self.started,
            "last_seconds": self.ticks[-1] - self.started,
            "max_gap_seconds": max(gaps) if gaps else None,
            "median_gap_seconds": statistics.median(gaps) if gaps else None,
            "events": len(self.ticks),
            "first_active_seconds": self.first_active_seconds,
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


def run_evaluation_phase(model, previous, config, *, monitor, pool):
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
    parser.add_argument(
        "--repeats",
        type=int,
        default=3,
        help="measured off/on pairs per worker count (two samples per pair)",
    )
    parser.add_argument("--workers", nargs="+", type=int, default=[2, 4])
    parser.add_argument(
        "--monitor",
        nargs="+",
        choices=["on", "off"],
        default=["on", "off"],
        help="must include both on and off; the paired protocol measures both",
    )
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
    if set(args.monitor) != {"on", "off"}:
        parser.error("the paired protocol requires both --monitor on and off")
    sys.path.insert(0, str(args.source.resolve()))
    import torch
    from hnefatafl.models import PolicyValueNet
    from hnefatafl.parallel_search import SearchPool
    from hnefatafl.training import generate_self_play, normalize_config

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

    pair_records: list[dict] = []
    results: list[dict] = []
    # One pool per worker count is created once, warmed with both monitoring
    # modes and reused for every measured pair, so process startup is identical
    # for both arms and excluded from the timed samples.
    for workers in args.workers:
        pool = SearchPool(workers) if workers else None
        try:

            def warmup(mode, workers=workers, pool=pool):
                reset_model(model, weights, args.seed)
                run_evaluation_phase(
                    model,
                    previous,
                    {**config, "search_workers": workers},
                    monitor=mode == "on",
                    pool=pool,
                )

            def measure(mode, pair, workers=workers, pool=pool):
                reset_model(model, weights, args.seed)
                sample = run_evaluation_phase(
                    model,
                    previous,
                    {**config, "search_workers": workers},
                    monitor=mode == "on",
                    pool=pool,
                )
                sample.update(workers=workers, monitor=mode, repetition=pair)
                return sample

            records, samples = run_paired_arms("evaluation", workers, args.repeats, warmup, measure)
            pair_records.extend(records)
            results.extend(samples)
        finally:
            if pool is not None:
                pool.close()
    if args.self_play:
        for workers in args.workers:
            pool = SearchPool(workers) if workers else None
            try:

                def warmup(mode, workers=workers, pool=pool):
                    reset_model(model, weights, args.seed)
                    generate_self_play(
                        model,
                        {**config, "search_workers": workers},
                        seed=args.seed,
                        monitor=LatencySink() if mode == "on" else None,
                        search_pool=pool,
                    )

                def measure(mode, pair, workers=workers, pool=pool):
                    reset_model(model, weights, args.seed)
                    sink = LatencySink() if mode == "on" else None
                    started = time.perf_counter()
                    games, performance = generate_self_play(
                        model,
                        {**config, "search_workers": workers},
                        seed=args.seed,
                        monitor=sink,
                        search_pool=pool,
                    )
                    seconds = time.perf_counter() - started
                    return {
                        "kind": "self_play",
                        "workers": workers,
                        "monitor": mode,
                        "repetition": pair,
                        "seconds": seconds,
                        "work_sha256": self_play_digest(games),
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

                records, samples = run_paired_arms(
                    "self_play", workers, args.repeats, warmup, measure
                )
                pair_records.extend(records)
                results.extend(samples)
            finally:
                if pool is not None:
                    pool.close()

    payload = {
        "protocol": (
            "Alternating paired A/B per worker count: both monitoring modes warmed once and "
            "discarded, then one off and one on sample per pair with the first arm reversed every "
            "pair. Fixed weights and seeds. Progress messages are the parent-applied count; "
            "latency is measured in the parent sink and is wall-clock, so it is only meaningful "
            "on an unloaded machine."
        ),
        "pool_lifecycle": (
            "One SearchPool per worker count, created once, warmed with both monitoring modes "
            "and reused for every measured pair. Timed samples are warm steady-state phases and "
            "exclude process startup."
        ),
        "source": str(args.source.resolve()),
        "configuration": config,
        "environment": {"platform": platform.platform(), "python": sys.version},
        "summary": summarize_runs(results),
        "paired_summary": summarize_pairs(pair_records),
        "pairs": pair_records,
        "runs": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n")
    for entry in payload["summary"]:
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
    for entry in payload["paired_summary"]:
        percentage = entry["median_percentage_overhead"]
        suffix = f"{percentage:+.2f}%" if percentage is not None else "n/a"
        print(
            f"{entry['kind']:10s} workers={entry['workers']} pairs={entry['pairs']} "
            f"median monitoring overhead {entry['median_absolute_difference_seconds']:+.3f}s "
            f"({suffix})",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
