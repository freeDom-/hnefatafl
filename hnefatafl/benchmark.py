"""Small, reproducible self-play comparisons without optimizer or registry writes."""

from __future__ import annotations

import time
from typing import Callable

import torch

from .engine import new_game
from .models import PolicyValueNet


def run_benchmark(
    configuration: dict | None = None, *, should_stop: Callable[[], bool] | None = None
) -> dict:
    """Compare identical seeded weights and budgets on each available device.

    Timings include complete game generation and a CUDA completion boundary,
    excluding model creation/warm-up. Both inference modes use float32 so the
    comparison measures batching rather than a change in precision.
    """
    from .inference import policy_value_batch
    from .training import generate_self_play, normalize_config

    supplied = dict(configuration or {})
    if supplied.get("resume"):
        raise ValueError("The self-play benchmark does not resume training checkpoints")
    config = normalize_config(
        {
            "preset": "smoke",
            "ruleset": "brandubh",
            "games": 4,
            "mcts_iterations": 4,
            "max_game_plies": 8,
            "seed": 17,
            **supplied,
            # Search work, not a short wall deadline, should determine the result.
            "mcts_time_limit": 300.0,
            "mixed_precision": False,
        }
    )
    if should_stop and should_stop():
        raise InterruptedError("Benchmark was cancelled")
    cuda_available = torch.cuda.is_available()
    previous_threads = torch.get_num_threads()
    scenarios: list[dict] = []
    cpu_weights: dict[str, torch.Tensor]
    try:
        torch.set_num_threads(config["cpu_threads"])
        # Creating a benchmark must not consume a caller's random-generator state.
        with torch.random.fork_rng(devices=[]):
            torch.random.default_generator.manual_seed(config["seed"])
            initial = PolicyValueNet(config["ruleset"], config["channels"], config["blocks"])
            cpu_weights = {
                key: value.detach().clone() for key, value in initial.state_dict().items()
            }
        del initial
        for device in ("cpu", "cuda"):
            if device == "cuda" and not cuda_available:
                scenarios.extend(
                    {
                        "device": device,
                        "mode": mode,
                        "status": "skipped",
                        "reason": "CUDA unavailable",
                    }
                    for mode in ("sequential", "batched")
                )
                continue
            if should_stop and should_stop():
                raise InterruptedError("Benchmark was cancelled")
            with torch.random.fork_rng(devices=[]):
                model = PolicyValueNet(config["ruleset"], config["channels"], config["blocks"])
            model.load_state_dict(cpu_weights)
            model = model.to(device).eval()
            # Warm both relevant shapes, without including setup in either mode.
            position = new_game(config["ruleset"])
            batch = min(
                config["games"], config["self_play_concurrency"], config["inference_batch_size"]
            )
            for size in sorted({1, batch}):
                policy_value_batch([position] * size, model, mixed_precision=False)
            if device == "cuda":
                torch.cuda.synchronize()
            baseline_seconds = 0.0
            baseline_work: tuple[int, int, int] | None = None
            for mode in ("sequential", "batched"):
                if should_stop and should_stop():
                    raise InterruptedError("Benchmark was cancelled")
                selected = {**config, "device": device, "self_play_mode": mode}
                if device == "cuda":
                    torch.cuda.reset_peak_memory_stats()
                    torch.cuda.synchronize()
                started = time.perf_counter()
                games, performance = generate_self_play(
                    model,
                    selected,
                    seed=config["seed"],
                    should_stop=should_stop,
                    measure_cuda=device == "cuda",
                )
                if device == "cuda":
                    torch.cuda.synchronize()
                elapsed = time.perf_counter() - started
                positions = sum(len(examples) for examples, _ in games)
                work = (
                    positions,
                    performance["mcts_simulations"],
                    performance["neural_evaluations"],
                )
                if mode == "sequential":
                    baseline_seconds, baseline_work = elapsed, work
                result = {
                    **performance,
                    "device": device,
                    "mode": mode,
                    "status": "completed",
                    "total_runtime_seconds": elapsed,
                    "games_per_hour": len(games) * 3600 / elapsed,
                    "positions_per_second": positions / elapsed,
                    "simulations_per_second": performance["mcts_simulations"] / elapsed,
                    "neural_evaluations_per_second": performance["neural_evaluations"] / elapsed,
                    "requested_simulations": positions * config["mcts_iterations"],
                    "speedup_vs_sequential": baseline_seconds / elapsed,
                    "work_matches_sequential": work == baseline_work,
                }
                if device == "cuda":
                    result.update(
                        gpu_peak_allocated_bytes=torch.cuda.max_memory_allocated(),
                        gpu_peak_reserved_bytes=torch.cuda.max_memory_reserved(),
                    )
                scenarios.append(result)
            del model
    finally:
        torch.set_num_threads(previous_threads)
    return {
        "configuration": {
            key: config[key]
            for key in (
                "ruleset",
                "channels",
                "blocks",
                "seed",
                "games",
                "max_game_plies",
                "mcts_iterations",
                "mcts_time_limit",
                "exploration",
                "temperature",
                "dirichlet_alpha",
                "dirichlet_fraction",
                "cpu_threads",
                "self_play_concurrency",
                "inference_batch_size",
                "mixed_precision",
            )
        },
        "inference_precision": "float32",
        "torch_version": str(torch.__version__),
        "cuda_version": torch.version.cuda,
        "timing": "Synchronized wall time, excluding model initialization and warm-up",
        "cuda_available": cuda_available,
        "scenarios": scenarios,
        "notes": [
            "Rates use the measured scenario wall time; speedup compares modes on the same device.",
            "Compare actual work too: terminal root wins can use fewer than the requested simulations.",
            "Different trajectories or deadline cutoffs can change completed work; a single small run is not a general performance claim.",
        ],
    }
