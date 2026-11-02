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
            "jarl-evaluation",
        ],
        required=True,
    )
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--variant", choices=["baseline", "candidate"], required=True)
    parser.add_argument(
        "--ruleset", default="brandubh", choices=["brandubh", "tablut", "copenhagen"]
    )
    parser.add_argument("--device", default="cpu", choices=["cpu", "cuda"])
    parser.add_argument("--replay-pipeline", choices=["auto", "packed", "legacy"], default="auto")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    # Workload budgets default to the small CPU protocol so previously recorded
    # hashes stay reproducible; GPU runs pass the larger documented budgets.
    parser.add_argument("--games", type=int, default=4)
    parser.add_argument("--max-game-plies", type=int, default=8)
    parser.add_argument("--mcts-iterations", type=int, default=32)
    parser.add_argument("--self-play-concurrency", type=int, default=4)
    parser.add_argument("--inference-batch-size", type=int, default=4)
    parser.add_argument("--inference-cache", choices=["off", "exact"], default="off")
    parser.add_argument("--evaluation-games", type=int, default=8)
    parser.add_argument("--evaluation-iterations", type=int, default=32)
    parser.add_argument("--evaluation-max-plies", type=int, default=16)
    parser.add_argument("--evaluation-concurrency", type=int, default=None)
    parser.add_argument("--evaluation-batch-size", type=int, default=None)
    parser.add_argument(
        "--evaluation-mode",
        choices=["auto", "sequential", "batched"],
        default=None,
        help="override evaluation_mode, e.g. batched to measure main's batched evaluator",
    )
    parser.add_argument("--replay-positions", type=int, default=512)
    parser.add_argument("--replay-epochs", type=int, default=2)
    parser.add_argument(
        "--workers",
        type=int,
        default=2,
        help="search_workers for the candidate; 0 measures the in-process scheduler",
    )
    parser.add_argument(
        "--search-inference",
        choices=["auto", "actors", "parent"],
        default="auto",
        help="where a worker pool evaluates CPU leaves",
    )
    parser.add_argument(
        "--flush-seconds",
        type=float,
        default=None,
        help="override the pool's bounded merge window (parent inference only)",
    )
    parser.add_argument(
        "--champion",
        action="store_true",
        help="evaluate against a distinct frozen Champion model instead of the candidate",
    )
    parser.add_argument(
        "--monitor",
        action="store_true",
        help="attach a live progress sink, as a real training job does",
    )
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
            "games": args.games,
            "max_game_plies": args.max_game_plies,
            "mcts_iterations": args.mcts_iterations,
            "mcts_time_limit": 300,
            "evaluation_games": args.evaluation_games,
            "evaluation_iterations": args.evaluation_iterations,
            "evaluation_max_plies": args.evaluation_max_plies,
            "evaluation_time_limit": 300,
            "evaluation_jarl_time_limit": 300,
            "mixed_precision": False,
            "self_play_mode": "batched",
            "self_play_concurrency": args.self_play_concurrency,
            "inference_batch_size": args.inference_batch_size,
            "inference_cache_mode": args.inference_cache,
            "training_symmetry_mode": "random_d4",
        }
    )
    if args.variant == "candidate":
        required = {
            "evaluation": "evaluation_mode",
            "workers": "search_workers",
            "workers-selfplay": "search_workers",
            "replay": "replay_pipeline",
        }.get(args.feature)
        if required is not None and required not in config:
            parser.error(
                "Check out the corresponding optimization branch before measuring a candidate"
            )
        if args.feature == "engine":
            import hnefatafl.engine as engine_module

            if not hasattr(engine_module, "has_legal_move"):
                parser.error(
                    "Check out the engine optimization branch before measuring a candidate"
                )
        if args.feature in ("jarl", "jarl-evaluation"):
            import hnefatafl.ai as ai_module

            if not hasattr(ai_module, "_lazy_ordered"):
                parser.error("Check out the Jarl optimization branch before measuring a candidate")
    if args.feature == "jarl-evaluation":
        config.update(
            evaluation_opponent="jarl",
            evaluation_games=4,
            evaluation_max_plies=4,
            evaluation_iterations=8,
            evaluation_jarl_depth=2,
            evaluation_mode="sequential",
        )
    if args.variant == "candidate":
        if args.feature == "evaluation":
            config["evaluation_mode"] = "batched"
        if args.feature == "replay":
            config["replay_pipeline"] = args.replay_pipeline
        if args.feature.startswith("workers"):
            config.update(search_workers=args.workers, cpu_threads=1)
    elif args.feature == "evaluation":
        config["evaluation_mode"] = "sequential"
    # Explicit budgets and mode override every arm so a comparison never mixes
    # concurrency or batch limits between baseline and candidate.
    if args.evaluation_concurrency is not None:
        config["evaluation_concurrency"] = args.evaluation_concurrency
    if args.evaluation_batch_size is not None:
        config["evaluation_batch_size"] = args.evaluation_batch_size
    if args.evaluation_mode is not None:
        config["evaluation_mode"] = args.evaluation_mode
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
    replay = (replay * (args.replay_positions // len(replay) + 1))[: args.replay_positions]
    weights = {key: value.detach().clone() for key, value in model.state_dict().items()}
    pool = None
    if args.variant == "candidate" and args.feature.startswith("workers") and args.workers:
        from hnefatafl.parallel_search import SearchPool

        overrides = {} if args.flush_seconds is None else {"flush_seconds": args.flush_seconds}
        pool = SearchPool(args.workers, inference=args.search_inference, **overrides)
    options = {"search_pool": pool} if pool is not None else {}
    sink = None
    if args.monitor:
        from hnefatafl.training_monitoring import TrainingMonitor

        # A real job publishes from the training thread; the writer here is a
        # no-op so the sample measures the compute path, not file writes.
        sink = TrainingMonitor(lambda **flags: None, phase="benchmark")
        sink.start()
        options["monitor"] = sink
    if args.feature in ("evaluation", "workers") and args.champion:
        torch.manual_seed(314159)
        options["previous"] = (
            PolicyValueNet(args.ruleset, config["channels"], config["blocks"])
            .to(args.device)
            .eval()
        )
        config["evaluation_opponent"] = "champion"
    samples = []
    warmup_seconds = None
    for repetition in range(args.repeats + 1):
        model.load_state_dict(weights)
        model.eval()
        torch.manual_seed(42)
        if sink is not None:
            sink.phase("benchmark")
        if args.device == "cuda":
            torch.cuda.synchronize()
        started = time.perf_counter()
        if args.feature in ("evaluation", "workers", "jarl-evaluation"):
            import hnefatafl.training_evaluation as evaluation_module

            original = evaluation_module.choose_move
            trace = {
                "jarl_successors": 0,
                "jarl_nodes": 0,
                "jarl_evaluations": 0,
                "jarl_lazy_successors_skipped": 0,
                "jarl_interruptions": 0,
            }
            decisions = []

            def measured_choice(state, settings, **kwargs):
                details = kwargs.pop("diagnostics", {})
                move = original(state, settings, diagnostics=details, **kwargs)
                decisions.append(
                    (
                        state.board,
                        state.turn,
                        move.source,
                        move.target,
                        details.get("value"),
                        details.get("completed_depth"),
                    )
                )
                if settings["algorithm"] == "alpha_beta":
                    for key in ("successors", "nodes", "evaluations", "lazy_successors_skipped"):
                        trace["jarl_" + key] += details.get(key, 0)
                    trace["jarl_interruptions"] += int("interrupted_depth" in details)
                return move

            if args.feature == "jarl-evaluation":
                evaluation_module.choose_move = measured_choice
            try:
                result = evaluate_model(model, config, **options)
            finally:
                evaluation_module.choose_move = original
            work = [
                {
                    key: row[key]
                    for key in ("pair", "seed", "candidate_side", "winner", "plies", "reason")
                }
                for row in result["games"]
            ]
            counters = {
                **result.get("performance", {}),
                "wins": result["wins"],
                "losses": result["losses"],
                "draws": result["draws"],
                "truncated": result["truncated"],
                "candidate_score": result["score"],
                "candidate_as_attacker_score": result["candidate_as_attacker"]["score"],
                "candidate_as_defender_score": result["candidate_as_defender"]["score"],
                "mcts_simulations": sum(row.get("simulations", 0) for row in result["games"]),
                "search_deadline_cutoffs": sum(
                    row.get("search_deadline_cutoffs", 0) for row in result["games"]
                ),
                "evaluation_mode": config.get("evaluation_mode"),
            }
            per_model = counters.get("models") or []
            if per_model:
                # Flatten the per-model queues so evaluation arms are comparable.
                batches = sum(entry["inference_batches"] for entry in per_model)
                evaluations = sum(entry["neural_evaluations"] for entry in per_model)
                counters.update(
                    inference_batches=batches,
                    neural_evaluations=evaluations,
                    inference_batch_size_average=evaluations / batches if batches else 0.0,
                    inference_batch_size_max=max(
                        entry["inference_batch_size_max"] for entry in per_model
                    ),
                )
            if args.feature == "jarl-evaluation":
                work = {"games": work, "decisions": decisions}
                counters.update(trace)
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
            optimizer = torch.optim.AdamW(
                model.parameters(), lr=config["learning_rate"], weight_decay=config["weight_decay"]
            )
            scaler = torch.amp.GradScaler("cuda", enabled=False)
            result = optimize_replay(
                model,
                optimizer,
                scaler,
                replay,
                {**config, "epochs": args.replay_epochs},
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
            counters = {"successors": 0, "evaluations": 0, "nodes": 0, "lazy_successors_skipped": 0}
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
                work.append(
                    (details["value"], details["completed_depth"], move.source, move.target)
                )
                assert move in legal_moves(state)
                for key in counters:
                    counters[key] += details.get(key, 0)
        if args.device == "cuda":
            torch.cuda.synchronize()
        elapsed = time.perf_counter() - started
        if args.device == "cuda":
            counters.setdefault("gpu_peak_allocated_bytes", torch.cuda.max_memory_allocated())
            counters.setdefault("gpu_peak_reserved_bytes", torch.cuda.max_memory_reserved())
        if sink is not None:
            counters.update(
                {
                    f"monitor_{key}": value
                    for key, value in sink.snapshot()["phase_progress"].items()
                }
            )
        if args.feature == "replay":
            digest = hashlib.sha256()
            for key, value in sorted(model.state_dict().items()):
                digest.update(key.encode())
                digest.update(value.detach().cpu().contiguous().numpy().tobytes())
            work["weights_sha256"] = digest.hexdigest()
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
    if sink is not None:
        sink.stop()
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
        "options": {
            "workers": args.workers,
            "search_inference": args.search_inference,
            "flush_seconds": args.flush_seconds,
            "champion": args.champion,
            "repeats": args.repeats,
        },
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
