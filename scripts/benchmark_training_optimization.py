"""Isolated fixed-work A/B samples for the training optimization branches.

Run each source in a fresh process; alternate order, discard one warm-up and
retain every sample. --source selects an extracted baseline hnefatafl package.
No model registration or persistent training checkpoints are created.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import platform
import random
import sys
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--feature",
        choices=[
            "evaluation",
            "engine",
            "selfplay",
            "workers",
            "workers-selfplay",
            "replay",
            "jarl",
        ],
        required=True,
    )
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--variant", choices=["baseline", "candidate"], required=True)
    parser.add_argument(
        "--ruleset", default="brandubh", choices=["brandubh", "tablut", "copenhagen"]
    )
    parser.add_argument("--device", default="cpu", choices=["cpu", "cuda"])
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.source.resolve()))
    import torch
    from hnefatafl.ai import choose_move
    from hnefatafl.engine import apply_move, legal_moves, new_game
    from hnefatafl.models import PolicyValueNet, encode_state
    from hnefatafl.training import generate_self_play, normalize_config, optimize_replay
    from hnefatafl.training_evaluation import evaluate_model

    torch.set_num_threads(1)
    torch.manual_seed(42)
    config = normalize_config(
        {
            "ruleset": args.ruleset,
            "device": args.device,
            "cpu_threads": 1,
            "games": 4,
            "max_game_plies": 8,
            "mcts_iterations": 32,
            "mcts_time_limit": 300,
            "evaluation_games": 8,
            "evaluation_iterations": 32,
            "evaluation_max_plies": 16,
            "evaluation_time_limit": 300,
            "evaluation_jarl_time_limit": 300,
            "mixed_precision": False,
            "self_play_mode": "batched",
            "self_play_concurrency": 4,
            "inference_batch_size": 4,
            "training_symmetry_mode": "random_d4",
        }
    )
    if args.variant == "candidate":
        if args.feature == "evaluation":
            config.update(
                evaluation_mode="batched", evaluation_concurrency=8, evaluation_batch_size=8
            )
        if args.feature.startswith("workers"):
            config.update(search_workers=2, cpu_threads=1)
    elif args.feature == "evaluation":
        config["evaluation_mode"] = "sequential"
    model = (
        PolicyValueNet(args.ruleset, config["channels"], config["blocks"]).to(args.device).eval()
    )
    rng = random.Random(17)
    state = new_game(args.ruleset)
    positions = []
    for ply in range(32):
        if state.winner:
            break
        if ply % 8 == 0:
            positions.append(state)
        state = apply_move(state, rng.choice(legal_moves(state)))
    replay = []
    for state in positions:
        moves = legal_moves(state)
        ids = [m.source * state.size**2 + m.target for m in moves]
        replay.append(
            {
                "input": encode_state(state),
                "actions": ids,
                "legal": ids,
                "probabilities": [1 / len(ids)] * len(ids),
                "value": 0.0,
            }
        )
    replay = (replay * (512 // len(replay) + 1))[:512]
    weights = {key: value.detach().clone() for key, value in model.state_dict().items()}
    pool = None
    if args.variant == "candidate" and args.feature.startswith("workers"):
        from hnefatafl.parallel_search import SearchPool

        pool = SearchPool(2)
    options = {"search_pool": pool} if pool is not None else {}
    samples = []
    warmup_seconds = None
    for repetition in range(args.repeats + 1):
        model.load_state_dict(weights)
        model.eval()
        torch.manual_seed(42)
        if args.device == "cuda":
            torch.cuda.synchronize()
        started = time.perf_counter()
        if args.feature in ("evaluation", "workers"):
            result = evaluate_model(model, config, **options)
            work = [
                {
                    key: row[key]
                    for key in ("pair", "seed", "candidate_side", "winner", "plies", "reason")
                }
                for row in result["games"]
            ]
            counters = result.get("performance", {})
        elif args.feature in ("selfplay", "workers-selfplay"):
            games, counters = generate_self_play(model, config, seed=42, **options)
            work = [
                (
                    tuple(
                        (row["input"].tolist(), row["actions"], row["probabilities"])
                        for row in examples
                    ),
                    result["winner"],
                    result["plies"],
                    result["simulations"],
                )
                for examples, result in games
            ]
        elif args.feature == "engine":
            work = []
            for _ in range(8):
                for state in positions:
                    for move in legal_moves(state):
                        child = apply_move(state, move)
                        work.append((child.board, child.winner, child.reason))
            counters = {"successors": len(work)}
        elif args.feature == "replay":
            optimizer = torch.optim.AdamW(model.parameters(), lr=config["learning_rate"])
            scaler = torch.amp.GradScaler("cuda", enabled=False)
            result = optimize_replay(
                model,
                optimizer,
                scaler,
                replay,
                {**config, "epochs": 2},
                torch.device(args.device),
                False,
                random.Random(9),
                random.Random(10),
            )
            work = {"updates": result["updates"], "presented": result["presented"]}
            counters = {
                **work,
                "policy_loss": sum(result["policy_losses"]) / len(result["policy_losses"]),
            }
        else:
            work = []
            counters = {"successors": 0, "evaluations": 0, "nodes": 0}
            for state in positions:
                details = {}
                move = choose_move(
                    state,
                    {
                        "algorithm": "alpha_beta",
                        "depth": 3 if args.ruleset == "brandubh" else 2,
                        "time_limit": 300,
                        "seed": 23,
                    },
                    diagnostics=details,
                )
                work.append((details["value"], details["completed_depth"]))
                assert move in legal_moves(state)
                for key in counters:
                    counters[key] += details.get(key, 0)
        if args.device == "cuda":
            torch.cuda.synchronize()
        elapsed = time.perf_counter() - started
        if not repetition:
            warmup_seconds = elapsed
        if repetition:
            samples.append(
                {
                    "seconds": elapsed,
                    "work_sha256": hashlib.sha256(
                        json.dumps(work, sort_keys=True).encode()
                    ).hexdigest(),
                    "counters": counters,
                }
            )
    if pool is not None:
        pool.close()
    sources = {
        str(path.relative_to(args.source)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted((args.source / "hnefatafl").glob("*.py"))
    }
    payload = {
        "feature": args.feature,
        "variant": args.variant,
        "ruleset": args.ruleset,
        "device": args.device,
        "configuration": config,
        "samples": samples,
        "warmup_seconds": warmup_seconds,
        "environment": {
            "platform": platform.platform(),
            "python": sys.version,
            "torch": torch.__version__,
            "cuda_available": torch.cuda.is_available(),
            "threads": torch.get_num_threads(),
        },
        "sources": sources,
        "notes": "One discarded warm-up. Fixed weights/seeds and generous deadlines; unfinished games remain unfinished. These are performance diagnostics, not playing-strength evidence.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n")
    print(
        json.dumps(
            {
                "feature": args.feature,
                "variant": args.variant,
                "ruleset": args.ruleset,
                "samples": [s["seconds"] for s in samples],
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
