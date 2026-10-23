"""Crash-isolated, repeated complete-iteration CPU/OpenVINO comparisons."""

from __future__ import annotations

import json
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import time
from typing import Any, Callable
from uuid import uuid4

from .inference_backends import openvino_capabilities, supports_openvino
from .models import device_info
from .training import normalize_config


def run_training_benchmark(
    configuration: dict, *, should_stop: Callable[[], bool] | None = None
) -> dict:
    allowed = {
        "training",
        "repetitions",
        "warmup_iterations",
        "measured_iterations",
        "inference_device",
        "output_dir",
    }
    if set(configuration) - allowed:
        raise ValueError("Unknown full-training benchmark settings")
    repetitions = configuration.get("repetitions", 3)
    warmup = configuration.get("warmup_iterations", 1)
    measured = configuration.get("measured_iterations", 3)
    for name, value, low in (
        ("repetitions", repetitions, 1),
        ("warmup_iterations", warmup, 0),
        ("measured_iterations", measured, 1),
    ):
        if type(value) is not int or not low <= value <= 100:
            raise ValueError(f"{name} must be an integer between {low} and 100")
    supplied = configuration.get("training", {})
    if not isinstance(supplied, dict):
        raise ValueError("training must be an object")
    if supplied.get("resume"):
        raise ValueError("Use seeded initial weights; this benchmark does not resume checkpoints")
    # Equal architecture, seeded initial weights, optimization and search work.
    config = normalize_config(
        {
            "preset": "smoke",
            **supplied,
            "device": "cpu",
            "mixed_precision": False,
            "self_play_mode": "batched",
            "iterations": warmup + measured,
            "inference_backend": "pytorch",
            "inference_device": "auto",
        }
    )
    target = configuration.get("inference_device", "GPU")
    normalize_config({**config, "inference_backend": "openvino", "inference_device": target})
    directory = Path(
        configuration.get("output_dir", f"work/training-benchmark-{uuid4().hex}")
    ).resolve()
    directory.mkdir(parents=True, exist_ok=False)
    capabilities = openvino_capabilities()
    scenarios: list[dict[str, Any]] = []
    for repetition in range(repetitions):
        # Alternate order to reduce systematic temperature/order effects.
        for backend in ("pytorch", "openvino") if repetition % 2 == 0 else ("openvino", "pytorch"):
            if backend == "openvino" and not supports_openvino(capabilities, target):
                scenarios.append(
                    {
                        "backend": backend,
                        "repetition": repetition,
                        "status": "skipped",
                        "reason": f"Operational OpenVINO {target} unavailable",
                        "capabilities": capabilities,
                    }
                )
                continue
            if should_stop and should_stop():
                raise InterruptedError("Training benchmark cancelled")
            run_dir = directory / f"{backend}-{repetition}"
            run_dir.mkdir()
            selected = {
                **config,
                "output_dir": str(run_dir),
                "registry_dir": str(run_dir / "models"),
                "inference_backend": backend,
                "inference_device": target if backend == "openvino" else "auto",
            }
            path = run_dir / "input.json"
            path.write_text(json.dumps(selected, indent=2))
            started = time.perf_counter()
            with (run_dir / "process.log").open("w") as log:
                process = subprocess.Popen(
                    [sys.executable, "-m", "hnefatafl.cli", "train", "--config", str(path)],
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )
                try:
                    while process.poll() is None:
                        if should_stop and should_stop():
                            process.terminate()
                            raise InterruptedError("Training benchmark cancelled")
                        try:
                            process.wait(timeout=0.2)
                        except subprocess.TimeoutExpired:
                            pass
                finally:
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait()
            elapsed = time.perf_counter() - started
            if process.returncode:
                raise RuntimeError(
                    f"{backend} benchmark exited {process.returncode}; inspect {run_dir / 'process.log'}"
                )
            result = json.loads((run_dir / "result.json").read_text())
            history = result["history"][warmup:]
            # Final point's progress includes its own artifact write; checkpoint
            # history necessarily predates that write. Keep the distinction explicit.
            rows = [dict(point["performance"]) for point in history]
            events = []
            for line in (run_dir / "process.log").read_text().splitlines():
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if isinstance(event, dict) and event.get("stage") == "iteration_complete":
                    events.append(event["performance"])
            for row, event in zip(rows, events[warmup:]):
                row.update(event)
            if len(events) != warmup + measured:
                raise ValueError("Benchmark is missing complete iteration timing events")
            for row in rows:
                row["iteration_games_per_hour"] = (
                    row["games"] * 3600 / row["iteration_wall_seconds"]
                )
            scenarios.append(
                {
                    "backend": backend,
                    "repetition": repetition,
                    "status": "completed",
                    "process_wall_seconds": elapsed,
                    "warmup_iterations": warmup,
                    "iterations": rows,
                    "checkpoint": result["checkpoint"],
                    "measured_compute_seconds_mean": statistics.mean(
                        row["iteration_compute_seconds"] for row in rows
                    ),
                    "final_iteration_wall_seconds": rows[-1]["iteration_wall_seconds"],
                    "measured_wall_seconds_mean": statistics.mean(
                        row["iteration_wall_seconds"] for row in rows
                    ),
                }
            )
    summaries: dict[str, Any] = {}
    for backend in ("pytorch", "openvino"):
        values = [
            row["measured_compute_seconds_mean"]
            for row in scenarios
            if row["backend"] == backend and row["status"] == "completed"
        ]
        if values:
            summaries[backend] = {
                "mean_iteration_compute_seconds": statistics.mean(values),
                "repetition_means": values,
                "mean_iteration_wall_seconds": statistics.mean(
                    row["measured_wall_seconds_mean"]
                    for row in scenarios
                    if row["backend"] == backend and row["status"] == "completed"
                ),
            }
    if len(summaries) == 2:
        summaries["openvino"]["compute_speedup_vs_pytorch"] = (
            summaries["pytorch"]["mean_iteration_compute_seconds"]
            / summaries["openvino"]["mean_iteration_compute_seconds"]
        )
    if len(summaries) == 2:
        summaries["openvino"]["wall_speedup_vs_pytorch"] = (
            summaries["pytorch"]["mean_iteration_wall_seconds"]
            / summaries["openvino"]["mean_iteration_wall_seconds"]
        )
    report = {
        "configuration": config,
        "environment": {"platform": platform.platform(), "python": sys.version, **device_info()},
        "scenarios": scenarios,
        "summary": summaries,
        "timing": "Compute includes refresh, self-play, optimization and evaluation. Each iteration wall includes persistence. Process wall additionally includes startup, warm-up iterations and registration.",
        "notes": [
            "Compare completed simulations, cutoff counts and actual batch distributions alongside time.",
            "Neural differences can change trajectories; these timings establish no universal speedup.",
        ],
    }
    (directory / "benchmark.json").write_text(json.dumps(report, indent=2, allow_nan=False))
    return report
