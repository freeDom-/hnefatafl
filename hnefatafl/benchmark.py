"""Small, reproducible self-play comparisons without optimizer or registry writes."""

from __future__ import annotations

import hashlib
import json
import random
import statistics
import time
from typing import Callable

import torch

from .engine import State, apply_move, legal_moves, new_game
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
    repetitions = supplied.pop("repetitions", 3)
    cache_modes = supplied.pop("cache_modes", ["off", "exact"])
    workload_batches = supplied.pop("inference_workload_batches", 16)
    if type(repetitions) is not int or not 1 <= repetitions <= 20:
        raise ValueError("repetitions must be an integer between 1 and 20")
    if not isinstance(cache_modes, list) or cache_modes not in (["off"], ["off", "exact"]):
        raise ValueError("cache_modes must be [off] or [off, exact]")
    if type(workload_batches) is not int or not 1 <= workload_batches <= 256:
        raise ValueError("inference_workload_batches must be between 1 and 256")
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
    workloads: list[dict] = []
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
            for repetition in range(repetitions):
                rows = {}
                order = cache_modes if repetition % 2 == 0 else list(reversed(cache_modes))
                for cache_mode in order:
                    for mode in ("sequential", "batched"):
                        if should_stop and should_stop():
                            raise InterruptedError("Benchmark was cancelled")
                        selected = {
                            **config,
                            "device": device,
                            "self_play_mode": mode,
                            "inference_cache_mode": cache_mode,
                        }
                        if device == "cuda":
                            torch.cuda.reset_peak_memory_stats()
                            torch.cuda.synchronize()
                        cpu_started = time.process_time()
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
                        cpu_elapsed = time.process_time() - cpu_started
                        positions = sum(len(examples) for examples, _ in games)
                        # Compare actual seeded trajectories/search targets. Bootstrap
                        # float values may differ by FP32 kernel rounding with batch shape.
                        trajectory = hashlib.sha256()
                        for examples, result in games:
                            for example in examples:
                                trajectory.update(example["input"].numpy().tobytes())
                                trajectory.update(
                                    json.dumps(
                                        [example["actions"], example["probabilities"]]
                                    ).encode()
                                )
                            trajectory.update(
                                json.dumps(
                                    [result["winner"], result["reason"], result["simulations"]]
                                ).encode()
                            )
                        row = {
                            **performance,
                            "device": device,
                            "mode": mode,
                            "cache_mode": cache_mode,
                            "repetition": repetition,
                            "status": "completed",
                            "total_runtime_seconds": elapsed,
                            "total_cpu_seconds": cpu_elapsed,
                            "games_per_hour": len(games) * 3600 / elapsed,
                            "positions_per_second": positions / elapsed,
                            "simulations_per_second": performance["mcts_simulations"] / elapsed,
                            "neural_evaluations_per_second": performance["neural_evaluations"]
                            / elapsed,
                            "requested_simulations": positions * config["mcts_iterations"],
                            "trajectory_sha256": trajectory.hexdigest(),
                            "network_evaluations_eliminated": performance["inference_requests"]
                            - performance["neural_evaluations"],
                        }
                        if device == "cuda":
                            row.update(
                                gpu_peak_allocated_bytes=torch.cuda.max_memory_allocated(),
                                gpu_peak_reserved_bytes=torch.cuda.max_memory_reserved(),
                            )
                        rows[(mode, cache_mode)] = row
                baseline = rows[("sequential", "off")]
                for (mode, cache_mode), row in rows.items():
                    off = rows[(mode, "off")]
                    row.update(
                        speedup_vs_sequential=baseline["total_runtime_seconds"]
                        / row["total_runtime_seconds"],
                        speedup_vs_cache_off=off["total_runtime_seconds"]
                        / row["total_runtime_seconds"],
                        work_matches_sequential=all(
                            row[key] == baseline[key]
                            for key in (
                                "self_play_positions",
                                "mcts_simulations",
                                "inference_requests",
                            )
                        ),
                        seeded_behavior_matches_cache_off=row["trajectory_sha256"]
                        == off["trajectory_sha256"],
                    )
                scenarios.extend(
                    rows[(mode, cache_mode)]
                    for cache_mode in cache_modes
                    for mode in ("sequential", "batched")
                )
                workloads.extend(
                    _inference_workloads(
                        model, config, order, workload_batches, batch, repetition, should_stop
                    )
                )
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
                "inference_cache_capacity",
                "mixed_precision",
            )
        },
        "inference_precision": "float32",
        "torch_version": str(torch.__version__),
        "cuda_version": torch.version.cuda,
        "timing": "Synchronized wall time, excluding model initialization and warm-up",
        "cuda_available": cuda_available,
        "scenarios": scenarios,
        "inference_workloads": workloads,
        "repetitions": repetitions,
        "inference_workload_batches": workload_batches,
        "cache_modes": cache_modes,
        "summary": [
            {
                "device": device,
                "mode": mode,
                "cache_mode": cache_mode,
                "median_runtime_seconds": statistics.median(
                    row["total_runtime_seconds"]
                    for row in scenarios
                    if row["status"] == "completed"
                    and (row["device"], row["mode"], row["cache_mode"])
                    == (device, mode, cache_mode)
                ),
                "median_speedup_vs_cache_off": statistics.median(
                    row["speedup_vs_cache_off"]
                    for row in scenarios
                    if row["status"] == "completed"
                    and (row["device"], row["mode"], row["cache_mode"])
                    == (device, mode, cache_mode)
                ),
            }
            for device in ("cpu", "cuda")
            if device == "cpu" or cuda_available
            for mode in ("sequential", "batched")
            for cache_mode in cache_modes
        ],
        "notes": [
            "Rates use the measured scenario wall time; speedup compares modes on the same device.",
            "Compare actual work too: terminal root wins can use fewer than the requested simulations.",
            "Different trajectories or deadline cutoffs can change completed work; a single small run is not a general performance claim.",
        ],
    }


def _inference_workloads(model, config, cache_modes, batches, batch_size, repetition, should_stop):
    """Controlled duplicate-heavy and unique exact-input workloads; no MCTS."""
    from .inference import ExactInferenceCache, InferenceMetrics, policy_value_batch

    device = next(model.parameters()).device
    # Pool creation is excluded from timing, identical across cache modes.
    rng = random.Random(config["seed"])
    pool: list[State] = []
    seen = set()
    state = new_game(config["ruleset"])
    while len(pool) < batches * batch_size:
        if should_stop and should_stop():
            raise InterruptedError("Benchmark was cancelled")
        key = (state.board, state.turn)
        if not state.winner and key not in seen:
            seen.add(key)
            pool.append(state)
        moves = legal_moves(state)
        state = apply_move(state, rng.choice(moves)) if moves else new_game(config["ruleset"])
    rows = []
    for workload in ("duplicate-heavy", "unique"):
        for mode in cache_modes:
            cache = (
                ExactInferenceCache(config["inference_cache_capacity"]) if mode == "exact" else None
            )
            metrics = InferenceMetrics(track_cuda=device.type == "cuda")
            if device.type == "cuda":
                torch.cuda.reset_peak_memory_stats()
                torch.cuda.synchronize()
            cpu_started = time.process_time()
            started = time.perf_counter()
            for index in range(batches):
                if should_stop and should_stop():
                    raise InterruptedError("Benchmark was cancelled")
                states = (
                    [pool[0]] * batch_size
                    if workload == "duplicate-heavy"
                    else pool[index * batch_size : (index + 1) * batch_size]
                )
                policy_value_batch(states, model, metrics=metrics, cache=cache)
            measured = metrics.snapshot(finalize_cuda=True)
            row = {
                **measured,
                "device": str(device),
                "workload": workload,
                "cache_mode": mode,
                "repetition": repetition,
                "total_runtime_seconds": time.perf_counter() - started,
                "total_cpu_seconds": time.process_time() - cpu_started,
            }
            if device.type == "cuda":
                row.update(
                    gpu_peak_allocated_bytes=torch.cuda.max_memory_allocated(),
                    gpu_peak_reserved_bytes=torch.cuda.max_memory_reserved(),
                )
            rows.append(row)
    return rows
