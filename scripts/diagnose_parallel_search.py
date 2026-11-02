"""Locate the first divergence between repeated self-play runs.

Repeated runs of one deterministic configuration must produce identical games.
When they do not, this script reports which game and ply first differ, the move
each run actually played there, and the per-run activity counters that explain
the difference.

Run each source in its own process; the harness never imports two sources at
once. ``--source`` selects an extracted baseline/PR package the same way the
optimization benchmark does.

    python scripts/diagnose_parallel_search.py --repeat 3 --workers 2
    python scripts/diagnose_parallel_search.py --source work/pr39-source --workers 2
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import random
import sys
import time


def ply_digest(row: dict) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "input": row["input"].tolist(),
                "actions": list(row["actions"]),
                "probabilities": list(row["probabilities"]),
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()


def replay_moves(examples: list[dict], config: dict, seed: int) -> list[list[int]]:
    """Reconstruct the moves the game generator must have played.

    The generator draws one move per ply from a per-game RNG seeded with
    ``seed + game``, so the exact sequence is recoverable from the recorded
    policy targets alone. A mismatch means RNG consumption or move selection
    changed, independently of any neural difference.
    """
    rng = random.Random(seed)
    moves: list[list[int]] = []
    for ply, row in enumerate(examples):
        options = list(row["actions"])
        if not options:
            break
        area = row["input"].shape[-1] ** 2
        temperature = config["temperature"] if ply < 16 else max(0.1, config["temperature"] / 4)
        weights = [probability ** (1 / temperature) for probability in row["probabilities"]]
        action = options[rng.choices(range(len(options)), weights=weights, k=1)[0]]
        moves.append([action // area, action % area])
    return moves


def compare(reference: dict, other: dict) -> dict:
    """Report the first game/ply whose recorded example differs."""
    for game, (left, right) in enumerate(zip(reference["games"], other["games"])):
        if left["plies"] != right["plies"]:
            return {
                "identical": False,
                "game": game,
                "ply": min(left["plies"], right["plies"]),
                "kind": "ply_count",
                "reference_plies": left["plies"],
                "candidate_plies": right["plies"],
            }
        for ply, (a, b) in enumerate(zip(left["plies_digests"], right["plies_digests"])):
            if a != b:
                return {
                    "identical": False,
                    "game": game,
                    "ply": ply,
                    "kind": "position_or_policy",
                    "reference_move": left["moves"][ply] if ply < len(left["moves"]) else None,
                    "candidate_move": right["moves"][ply] if ply < len(right["moves"]) else None,
                    "reference_outcome": left["outcome"],
                    "candidate_outcome": right["outcome"],
                }
        if left["outcome"] != right["outcome"] or left["reason"] != right["reason"]:
            return {
                "identical": False,
                "game": game,
                "ply": left["plies"],
                "kind": "outcome",
                "reference_outcome": left["outcome"],
                "candidate_outcome": right["outcome"],
            }
    return {"identical": True}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument(
        "--ruleset", default="brandubh", choices=["brandubh", "tablut", "copenhagen"]
    )
    parser.add_argument("--device", default="cpu", choices=["cpu", "cuda"])
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--games", type=int, default=8)
    parser.add_argument("--max-game-plies", type=int, default=24)
    parser.add_argument("--mcts-iterations", type=int, default=32)
    parser.add_argument("--self-play-concurrency", type=int, default=8)
    parser.add_argument("--inference-batch-size", type=int, default=8)
    parser.add_argument("--inference-cache", choices=["off", "exact"], default="off")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    sys.path.insert(0, str(args.source.resolve()))
    import torch
    from hnefatafl.models import PolicyValueNet
    from hnefatafl.training import generate_self_play, normalize_config

    torch.set_num_threads(1)
    config = normalize_config(
        {
            "ruleset": args.ruleset,
            "device": args.device,
            "cpu_threads": 1,
            "games": args.games,
            "max_game_plies": args.max_game_plies,
            "mcts_iterations": args.mcts_iterations,
            "mcts_time_limit": 300,
            "self_play_mode": "batched",
            "self_play_concurrency": args.self_play_concurrency,
            "inference_batch_size": args.inference_batch_size,
            "inference_cache_mode": args.inference_cache,
            "mixed_precision": False,
        }
    )
    torch.manual_seed(11)
    weights = {
        key: value.detach().clone()
        for key, value in PolicyValueNet(args.ruleset, config["channels"], config["blocks"])
        .state_dict()
        .items()
    }
    pool = None
    if args.workers:
        from hnefatafl.parallel_search import SearchPool

        pool = SearchPool(args.workers)
    runs: list[dict] = []
    try:
        for repetition in range(args.repeat):
            model = (
                PolicyValueNet(args.ruleset, config["channels"], config["blocks"])
                .to(args.device)
                .eval()
            )
            model.load_state_dict(weights)
            model.eval()
            started = time.perf_counter()
            games, performance = generate_self_play(
                model, {**config, "search_workers": args.workers}, seed=args.seed, search_pool=pool
            )
            seconds = time.perf_counter() - started
            runs.append(
                {
                    "run": repetition,
                    "seconds": seconds,
                    "games": [
                        {
                            "index": index,
                            "seed": args.seed + index,
                            "moves": replay_moves(examples, config, args.seed + index),
                            "plies": result["plies"],
                            "plies_digests": [ply_digest(row) for row in examples],
                            "outcome": result["winner"],
                            "reason": result["reason"],
                            "simulations": result.get("simulations"),
                            "deadline_cutoffs": result.get("search_deadline_cutoffs", 0),
                        }
                        for index, (examples, result) in enumerate(games)
                    ],
                    "counters": {
                        key: performance[key]
                        for key in (
                            "self_play_mode",
                            "self_play_concurrency",
                            "mcts_simulations",
                            "neural_evaluations",
                            "inference_batches",
                            "inference_batch_size_average",
                            "inference_batch_size_max",
                            "search_deadline_cutoffs",
                            "search_workers",
                            "inference_location",
                        )
                        if key in performance
                    },
                }
            )
    finally:
        if pool is not None:
            pool.close()
    payload = {
        "source": str(args.source.resolve()),
        "workers": args.workers,
        "ruleset": args.ruleset,
        "device": args.device,
        "seed": args.seed,
        "configuration": config,
        "runs": runs,
        "comparisons": [{"run": run["run"], **compare(runs[0], run)} for run in runs[1:]],
    }
    for run in runs:
        print(
            f"run {run['run']}: {run['seconds']:.3f}s "
            f"games={[game['plies'] for game in run['games']]} "
            f"counters={run['counters']}"
        )
    for comparison in payload["comparisons"]:
        print(f"run {comparison['run']} vs run 0: {json.dumps(comparison)}")
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
