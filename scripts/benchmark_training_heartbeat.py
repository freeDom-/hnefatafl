"""Measure the added cost of live training monitoring.

Three isolated measurements, all on CPU and independent of a GPU:

* the hot-path counter update that self-play, optimization and evaluation use
  once per position or per optimizer batch;
* the bounded heartbeat write, compared with the retained progress snapshot that
  contains the full iteration history;
* the instrumentation delta of one fixed self-play/optimization workload with
  and without a monitor attached.

Run in a fresh process without other benchmarks or tests.
"""

import argparse
import json
from pathlib import Path
import platform
import random
import statistics
import sys
import tempfile
import time

import torch


class CountingSink:
    """Same surface the training loop uses; no locks or I/O of its own."""

    def __init__(self):
        self.counters: dict[str, float] = {}

    def phase(self, name, *, iteration=None):
        self.counters = {}

    def tick(self, **deltas):
        counters = self.counters
        for key, value in deltas.items():
            counters[key] = counters.get(key, 0) + value

    def counts(self, **values):
        self.counters.update(values)


def timed(function, repeats):
    samples = []
    for _ in range(repeats):
        started = time.perf_counter()
        function()
        samples.append(time.perf_counter() - started)
    return {"median_seconds": statistics.median(samples), "samples": samples}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=5)
    args = parser.parse_args()
    sys.path.insert(0, str(args.source.resolve()))

    from hnefatafl.models import PolicyValueNet
    from hnefatafl.training import (
        _write_json,
        generate_self_play,
        normalize_config,
        optimize_replay,
    )
    from hnefatafl.training_augmentation import augmentation_rng
    from hnefatafl.training_monitoring import heartbeat_projection

    torch.set_num_threads(1)
    report: dict = {
        "environment": {
            "python": platform.python_version(),
            "torch": torch.__version__,
            "platform": platform.platform(),
            "threads": torch.get_num_threads(),
        },
    }

    with tempfile.TemporaryDirectory(prefix="tafl-heartbeat-bench-") as temporary:
        root = Path(temporary)
        config = normalize_config(
            {
                "preset": "smoke",
                "device": "cpu",
                "seed": 3,
                "cpu_threads": 1,
                "games": 4,
                "max_game_plies": 8,
                "mcts_time_limit": 5,
                "self_play_mode": "batched",
                "self_play_concurrency": 4,
                "inference_batch_size": 4,
                "output_dir": str(root / "training"),
                "registry_dir": str(root / "models"),
            }
        )
        torch.manual_seed(0)
        model = PolicyValueNet("brandubh", config["channels"], config["blocks"]).eval()
        games, _ = generate_self_play(model, config, seed=7, retain_examples=True)
        replay = [example for examples, _ in games for example in examples]

        # 1. Hot path: one counter update per searched position / batch.
        sink = CountingSink()
        calls = 200_000
        started = time.perf_counter()
        for _ in range(calls):
            sink.tick(positions=1, simulations=24)
        elapsed = time.perf_counter() - started
        report["hot_path"] = {
            "calls": calls,
            "total_seconds": elapsed,
            "nanoseconds_per_call": elapsed / calls * 1e9,
        }

        # 2. Publication: bounded heartbeat against the retained snapshot.
        state = {
            "status": "running",
            "stage": "self_play",
            "iteration": 120,
            "total_iterations": 200,
            "duration": 12_345.6,
            "revision": 400,
            "phase_elapsed": 42.5,
            "last_progress_seconds": 0.2,
            "phase_progress": {"games_completed": 18, "games_total": 32, "positions": 4820},
            "history": [
                {
                    "iteration": index,
                    "policy_loss": 0.2,
                    "value_loss": 0.1,
                    "performance": {"simulations_per_second": 4200, "self_play_wall_seconds": 240},
                    "evaluation": {"score": 0.5, "games": [{"reason": "ply_limit"}] * 12},
                }
                for index in range(120)
            ],
            "metrics": {"policy_loss": 0.2, "self_play": [{"plies": 64}] * 32},
        }
        heartbeat_path = root / "heartbeat.json"
        progress_path = root / "progress.json"
        report["publication"] = {
            "heartbeat": {
                **timed(
                    lambda: _write_json(heartbeat_path, heartbeat_projection(state)),
                    args.repeats,
                ),
                "bytes": heartbeat_path.stat().st_size,
            },
            "progress": {
                **timed(lambda: _write_json(progress_path, state), args.repeats),
                "bytes": progress_path.stat().st_size,
            },
        }
        report["publication"]["heartbeat_bytes_per_second"] = (
            report["publication"]["heartbeat"]["bytes"] / 1.0
        )
        report["publication"]["progress_bytes_per_second"] = (
            report["publication"]["progress"]["bytes"] / 1.0
        )

        # 3. Instrumentation delta: paired, alternating runs of one fixed workload
        #    so drift cancels. The first pair is discarded as warm-up.
        def self_play_workload(monitor):
            return generate_self_play(
                PolicyValueNet("brandubh", config["channels"], config["blocks"]).eval(),
                {**config, "games": 8, "max_game_plies": 16, "self_play_concurrency": 4},
                seed=13,
                retain_examples=False,
                monitor=monitor,
            )

        def optimization_workload(monitor):
            torch.manual_seed(11)
            learner = PolicyValueNet("brandubh", config["channels"], config["blocks"])
            return optimize_replay(
                learner,
                torch.optim.AdamW(learner.parameters(), lr=0.001),
                torch.amp.GradScaler("cuda", enabled=False),
                replay,
                config,
                torch.device("cpu"),
                False,
                random.Random(5),
                augmentation_rng(5, 0),
                monitor=monitor,
            )

        def paired(workload):
            pairs = []
            for _ in range(args.repeats + 1):
                started = time.perf_counter()
                workload(None)
                baseline = time.perf_counter() - started
                started = time.perf_counter()
                sink = CountingSink()
                workload(sink)
                pairs.append(
                    {
                        "baseline_seconds": baseline,
                        "monitored_seconds": time.perf_counter() - started,
                        "monitor_calls": sink.counters.get("positions", 0)
                        or sink.counters.get("optimizer_steps", 0)
                        or 1,
                    }
                )
            measured = pairs[1:]
            deltas = sorted(
                pair["monitored_seconds"] - pair["baseline_seconds"] for pair in measured
            )
            calls = statistics.median(pair["monitor_calls"] for pair in measured)
            noise = (deltas[-1] - deltas[0]) / 2
            return {
                "pairs": measured,
                "discarded_warmup_pairs": 1,
                "median_baseline_seconds": statistics.median(
                    pair["baseline_seconds"] for pair in measured
                ),
                "median_monitored_seconds": statistics.median(
                    pair["monitored_seconds"] for pair in measured
                ),
                "median_delta_seconds": statistics.median(deltas),
                "delta_range_seconds": [deltas[0], deltas[-1]],
                # Run-to-run spread of the workload itself. A delta inside this
                # band is not a measurement of the instrumentation.
                "noise_seconds": noise,
                "within_noise": abs(statistics.median(deltas)) <= noise,
                "monitor_calls_per_run": calls,
                "attributed_nanoseconds_per_call": statistics.median(deltas) / calls * 1e9,
            }

        report["self_play_workload"] = paired(self_play_workload)
        report["optimization_workload"] = paired(optimization_workload)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report["publication"], indent=2))
    print(json.dumps(report["hot_path"], indent=2))
    print(json.dumps(report["optimization_workload"], indent=2))


if __name__ == "__main__":
    main()
