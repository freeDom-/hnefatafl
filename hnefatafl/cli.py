"""User-facing headless commands; computational jobs run outside the API worker."""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import threading
from pathlib import Path
from typing import Any


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tafl", description="Hnefatafl AI, training and model tools"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("train", "tune", "tournament"):
        command = sub.add_parser(name)
        command.add_argument("--config", required=True, help="JSON configuration file")
        if name == "train":
            command.add_argument("--resume", help="Safe checkpoint to resume")
    sub.add_parser("devices", help="Show PyTorch and optional OpenVINO availability")
    full_benchmark = sub.add_parser(
        "benchmark-training", help="Compare complete CPU/OpenVINO training iterations"
    )
    full_benchmark.add_argument("--config", required=True, help="Full-training benchmark JSON")
    full_benchmark.add_argument("--output", help="Save measured JSON evidence")
    symmetry = sub.add_parser(
        "benchmark-symmetry", help="Measure D4 augmentation cost, consistency and learning"
    )
    symmetry.add_argument("--config", help="Optional symmetry benchmark JSON")
    symmetry.add_argument("--output", help="Save measured JSON evidence")
    benchmark = sub.add_parser("benchmark", help="Compare sequential and batched self-play")
    benchmark.add_argument("--config", help="Optional training JSON with benchmark budgets")
    benchmark.add_argument("--output", help="Also save the JSON comparison to this file")
    heuristics = sub.add_parser(
        "benchmark-heuristics", help="Compare Shield maiden strength and speed"
    )
    heuristics.add_argument("--config", help="Optional heuristic benchmark JSON")
    heuristics.add_argument("--output", help="Save measured JSON evidence")
    worker = sub.add_parser("worker", help="Run leased training jobs from a remote server")
    worker.add_argument("--server", required=True, help="Server HTTP(S) URL")
    worker.add_argument(
        "--work-dir", default="data/remote-worker", help="Owned local job directory"
    )
    worker.add_argument(
        "--concurrency", type=int, default=1, help="Parallel training subprocesses (1–16)"
    )
    worker.add_argument("--once", action="store_true", help="Exit when the queue becomes idle")
    worker.add_argument("--max-jobs", type=int, help="Exit after accepting this many jobs")
    models = sub.add_parser("models", help="List, import or export safe model checkpoints")
    models.add_argument("--registry", default="data/models")
    models.add_argument("--import", dest="import_path")
    models.add_argument("--name")
    models.add_argument("--export", dest="export_id")
    models.add_argument("--output")
    args = parser.parse_args(argv)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    try:
        result: Any
        if args.command in (
            "benchmark",
            "benchmark-heuristics",
            "benchmark-training",
            "benchmark-symmetry",
        ):
            config = (
                json.loads(Path(args.config).read_text(encoding="utf-8")) if args.config else {}
            )
            if not isinstance(config, dict):
                raise ValueError("Configuration must be a JSON object")
            if args.command == "benchmark-training":
                from .training_benchmark import run_training_benchmark

                result = run_training_benchmark(config, should_stop=stop.is_set)
            elif args.command == "benchmark-symmetry":
                from .symmetry_benchmark import run_symmetry_benchmark

                result = run_symmetry_benchmark(config, should_stop=stop.is_set)
            elif args.command == "benchmark-heuristics":
                from .heuristic_benchmark import run_heuristic_benchmark

                result = run_heuristic_benchmark(config, should_stop=stop.is_set)
            else:
                from .benchmark import run_benchmark

                if args.output and isinstance(config.get("checkpoint"), str):
                    source, target = Path(config["checkpoint"]), Path(args.output)
                    if source.resolve() == target.resolve() or (
                        source.exists() and target.exists() and source.samefile(target)
                    ):
                        raise ValueError("Benchmark output must not overwrite its checkpoint")
                result = run_benchmark(config, should_stop=stop.is_set)
            if args.output:
                target = Path(args.output)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(
                    json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8"
                )
        elif args.command == "worker":
            from .remote_worker import run_worker

            token = os.environ.get("HNEFATAFL_WORKER_TOKEN", "")
            if not token:
                raise ValueError(
                    "Set HNEFATAFL_WORKER_TOKEN to the server-issued worker credential"
                )
            result = run_worker(
                args.server,
                token,
                args.work_dir,
                concurrency=args.concurrency,
                once=args.once,
                max_jobs=args.max_jobs,
                should_stop=stop,
            )
        elif args.command == "devices":
            from .models import device_info

            result = device_info()
        elif args.command == "models":
            import shutil
            from .models import ModelRegistry

            registry = ModelRegistry(args.registry)
            if args.import_path:
                result = registry.import_model(args.import_path, args.name)
            elif args.export_id:
                if not args.output:
                    raise ValueError("--output is required for export")
                source = registry.get(args.export_id)["path"]
                target = Path(args.output)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
                result = {"exported": str(target), "model_id": args.export_id}
            else:
                result = registry.list()
        else:
            config = json.loads(Path(args.config).read_text(encoding="utf-8"))
            if not isinstance(config, dict):
                raise ValueError("Configuration must be a JSON object")
            if args.command == "tournament":
                from .ai import tournament

                result = tournament(config, should_stop=stop.is_set)
            else:
                from .training import compact_progress, train, tune

                def report(progress):
                    print(json.dumps(compact_progress(progress), allow_nan=False), flush=True)

                if args.command == "train":
                    if args.resume:
                        config["resume"] = args.resume
                    result = train(config, report, stop.is_set)
                else:
                    result = tune(config, report, stop.is_set)
        if args.command in ("train", "tune"):
            result = compact_progress(result)
        print(json.dumps(result, indent=2, allow_nan=False), flush=True)
        return 0
    except Exception as error:
        print(json.dumps({"status": "failed", "error": str(error)}), file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
