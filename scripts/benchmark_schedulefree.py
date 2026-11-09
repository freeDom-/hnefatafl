"""Reproducible, isolated CPU Brandubh optimizer/state-size comparison.

Run: .venv/bin/python scripts/benchmark_schedulefree.py --output work/schedulefree.json
Each optimizer/architecture runs in a fresh process. This measures runtime and
memory, not playing strength. No model is published to the registry.
"""

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import platform
import random
import resource
import statistics
import subprocess
import sys
import tempfile
import time

import torch

from hnefatafl.models import PolicyValueNet, load_checkpoint, save_checkpoint
from hnefatafl.training import (
    _make_optimizer,
    evaluate_model,
    generate_self_play,
    normalize_config,
    optimize_replay,
)
from hnefatafl.training_evaluation import snapshot
from hnefatafl.training_optimizers import OptimizerLifecycle


def case(optimizer_name, channels, blocks):
    cfg = normalize_config(
        {
            "preset": "smoke",
            "optimizer": optimizer_name,
            "channels": channels,
            "blocks": blocks,
            "device": "cpu",
            "cpu_threads": 2,
            "games": 2,
            "max_game_plies": 4,
            "evaluation_max_plies": 2,
            "mcts_time_limit": 5,
            "evaluation_time_limit": 5,
            "training_symmetry_mode": "off",
            "epochs": 2,
            "batch_size": 32,
            "schedulefree_warmup_steps": 32,
        }
    )
    torch.set_num_threads(2)
    torch.manual_seed(0)
    model = PolicyValueNet("brandubh", channels, blocks)
    optimizer = _make_optimizer(model, cfg)
    lifecycle = OptimizerLifecycle(optimizer)
    scaler = torch.amp.GradScaler("cuda", enabled=False)
    # Warm the model's kernels without changing weights, optimizer or RNG.
    with torch.inference_mode():
        model(torch.zeros(32, 6, 7, 7))
    iteration_started = time.perf_counter()
    generation_started = time.perf_counter()
    generated, work = generate_self_play(model, cfg, seed=100000)
    self_play_seconds = time.perf_counter() - generation_started
    examples = [example for game, _ in generated for example in game]
    replay = [deepcopy(examples[i % len(examples)]) for i in range(256)]
    optimization = []
    updates = 0
    for repetition in range(3):
        started = time.perf_counter()
        result = optimize_replay(
            model,
            optimizer,
            scaler,
            replay,
            cfg,
            torch.device("cpu"),
            False,
            random.Random(123),
            random.Random(456),
        )
        # Ordinary optimizers leave this to the caller, as in train().
        model.eval()
        optimization.append(time.perf_counter() - started)
        updates += result["updates"]
        if repetition == 0:
            started = time.perf_counter()
            evaluation = evaluate_model(model, cfg)
            evaluation_seconds = time.perf_counter() - started
            iteration_seconds = time.perf_counter() - iteration_started
    started = time.perf_counter()
    for _ in range(100):
        lifecycle.train()
        lifecycle.inference()
    switching_pair_seconds = (time.perf_counter() - started) / 100
    # Representative recommended Brandubh capacity, with independently stored
    # tensors/lists, not repeated references that would undercount serialization.
    checkpoint_replay = [deepcopy(examples[i % len(examples)]) for i in range(8000)]
    metadata = {"training_config": cfg, "version": 1, "metrics": {"evaluation": evaluation}}
    selected = snapshot(model, metadata)
    with tempfile.TemporaryDirectory(dir="work") as directory:
        path = Path(directory) / "last.pt"
        started = time.perf_counter()
        save_checkpoint(
            path,
            model,
            metadata,
            optimizer,
            {
                "iteration": 1,
                "replay": checkpoint_replay,
                "best_snapshot": selected,
                "evaluation_state": {"benchmarks": {"benchmark": {"snapshot": selected}}},
                "python_rng": random.Random(0).getstate(),
                "torch_rng": torch.get_rng_state(),
                "cuda_rng": [],
                "grad_scaler": scaler.state_dict(),
                "history": [],
            },
        )
        save_seconds = time.perf_counter() - started
        size = path.stat().st_size
        started = time.perf_counter()
        restored, payload = load_checkpoint(path)
        restored_optimizer = _make_optimizer(restored, cfg)
        restored_optimizer.load_state_dict(payload["optimizer_state"])
        OptimizerLifecycle(restored_optimizer).inference()
        resume_seconds = time.perf_counter() - started
    state_bytes = sum(
        v.numel() * v.element_size()
        for state in optimizer.state.values()
        for v in state.values()
        if isinstance(v, torch.Tensor)
    )
    return {
        "optimizer": optimizer_name,
        "channels": channels,
        "blocks": blocks,
        "parameters": sum(p.numel() for p in model.parameters()),
        "updates": updates,
        "replay_positions": 256,
        "checkpoint_replay_positions": 8000,
        "batch_size": 32,
        "epochs_per_repetition": 2,
        "repetitions": 3,
        "optimization_seconds": optimization,
        "optimization_median_seconds": statistics.median(optimization),
        "iteration_seconds": iteration_seconds,
        "self_play_seconds": self_play_seconds,
        "evaluation_seconds": evaluation_seconds,
        "self_play_positions": len(examples),
        "train_eval_pair_seconds": switching_pair_seconds,
        "optimizer_tensor_bytes": state_bytes,
        "peak_cpu_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
        "peak_gpu_bytes": None,
        "checkpoint_bytes": size,
        "checkpoint_save_seconds": save_seconds,
        "resume_seconds": resume_seconds,
        "fits_128_mib": size < 128 * 1024 * 1024,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="work/schedulefree-benchmark.json")
    parser.add_argument("--case", nargs=3)
    args = parser.parse_args()
    Path("work").mkdir(exist_ok=True)
    if args.case:
        print(json.dumps(case(args.case[0], int(args.case[1]), int(args.case[2]))))
        return
    results = []
    for channels, blocks in [(4, 0), (32, 2), (64, 4), (128, 8)]:
        for optimizer in ["adamw", "adamw_schedulefree"]:
            completed = subprocess.run(
                [sys.executable, __file__, "--case", optimizer, str(channels), str(blocks)],
                check=True,
                capture_output=True,
                text=True,
                env={**os.environ, "OMP_NUM_THREADS": "2", "MKL_NUM_THREADS": "2"},
            )
            results.append(json.loads(completed.stdout))
            print(f"{optimizer} {channels}/{blocks} completed", flush=True)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    cpu_model = next(
        line.split(":", 1)[1].strip()
        for line in Path("/proc/cpuinfo").read_text().splitlines()
        if line.startswith("model name")
    )
    output.write_text(
        json.dumps(
            {
                "python": platform.python_version(),
                "torch": torch.__version__,
                "platform": platform.platform(),
                "device": "cpu",
                "cpu_threads": 2,
                "cpu_model": cpu_model,
                "cuda_available": torch.cuda.is_available(),
                "mps_available": torch.backends.mps.is_available(),
                "cases": results,
            },
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
