"""Measure portable archives against a saved pre-change module, without training changes.

Run from the repository root (see docs/benchmarks/job-archives.md). Fixtures retain
real smoke-training checkpoints; long histories repeat its measured iteration and
are synthetic, not evidence of 1,000 optimizer updates. Each measurement gets a
fresh process so peak RSS can be compared. No optional benchmark dependency.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import platform
import resource
import shutil
import statistics
import tempfile
import threading
import subprocess
import sys
import time

# Direct script invocation also works without an editable install.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hnefatafl import job_archives
from hnefatafl.models import ModelRegistry
from hnefatafl.storage import Store
from hnefatafl.training import train


def baseline(path):
    spec = importlib.util.spec_from_file_location("hnefatafl._archive_baseline", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class TemporaryDisk:
    """Sample new temporary inodes; hard links to source files allocate no copy.

    Reports allocated file blocks (excluding directory entries), sampled every
    10 ms. Final export archives count as temporary download outputs; published
    import directories do not. Both sides receive the same observation overhead.
    """

    def __init__(self, root):
        self.root = root
        self.initial = set()
        for path in root.rglob("*"):
            if path.is_file():
                stat = path.stat()
                self.initial.add((stat.st_dev, stat.st_ino))
        self.peak = 0
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.sample_loop, daemon=True)

    def sample(self):
        seen = set()
        total = 0
        for path in self.root.rglob("*"):
            temporary = (
                path.suffix == ".archive"
                or path.name.endswith(("-journal", "-wal", "-shm"))
                or any(
                    part.startswith(
                        (
                            "job-export-",
                            "job-snapshot-",
                            ".job-import-",
                            ".job-snapshot-",
                            "hnefatafl-upload-",
                        )
                    )
                    for part in path.parts
                )
            )
            if not temporary:
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            identity = (stat.st_dev, stat.st_ino)
            if path.is_file() and identity not in self.initial and identity not in seen:
                total += stat.st_blocks * 512
                seen.add(identity)
        self.peak = max(self.peak, total)

    def sample_loop(self):
        while not self.stop.wait(0.01):
            self.sample()

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.stop.set()
        self.thread.join()
        self.sample()


def measured(action, disk_root=None):
    meter = TemporaryDisk(disk_root) if disk_root is not None else None
    if meter:
        meter.__enter__()
    cpu = time.process_time()
    started = time.perf_counter()
    try:
        value = action()
        metrics = {
            "seconds": time.perf_counter() - started,
            "cpu_seconds": time.process_time() - cpu,
            "peak_rss_bytes": peak_rss(),
        }
    finally:
        if meter:
            meter.__exit__()
    if meter:
        metrics["peak_temporary_disk_bytes"] = meter.peak
    return value, metrics


def peak_rss():
    # Linux rusage can inherit the parent's pre-exec high-water mark. VmHWM
    # measures this executable's actual peak, including imports/fixture loading.
    status = Path("/proc/self/status")
    if status.exists():
        for line in status.read_text().splitlines():
            if line.startswith("VmHWM:"):
                return int(line.split()[1]) * 1024
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024


def wait_gate(root, name):
    (root / (name + ".ready")).touch()
    deadline = time.monotonic() + 60
    while not (root / "go").exists():
        if time.monotonic() > deadline:
            raise TimeoutError("Benchmark coordination timed out")
        time.sleep(0.01)


def worker(args):
    root = args.root.resolve()
    source = root / "source"
    disk_root = root if args.measure_disk else None
    if disk_root:
        temporary = root / "temporary"
        temporary.mkdir(exist_ok=True)
        tempfile.tempdir = str(temporary)
    module = job_archives if args.kind == "tar" else baseline(args.baseline)
    if args.kind == "zip-relaxed":
        # Only for a size/time comparison when the shipped baseline refuses 1,000
        # iterations. The strict ZIP failure is measured separately and retained.
        module.MAX_JSON_BYTES = module.MAX_FILE_BYTES
    job_archives.GZIP_LEVEL = args.level
    if args.operation == "training":
        gate = root / args.gate
        started = None

        def progress(report):
            nonlocal started
            if started is None and len(report.get("history", [])) >= 4:
                wait_gate(gate, "training")
                started = time.perf_counter()

        result = train(
            {
                "preset": "smoke",
                "seed": 17,
                "iterations": 44,
                "device": "cpu",
                "cpu_threads": 1,
                "max_game_plies": 4,
                "evaluation_max_plies": 2,
                "mcts_time_limit": 300,
                "output_dir": str(gate / "training"),
                "registry_dir": str(gate / "models"),
            },
            progress=progress,
        )
        elapsed = time.perf_counter() - started
        history = result["history"][4:]
        print(
            json.dumps(
                {
                    "seconds": elapsed,
                    "iterations_per_second": len(history) / elapsed,
                    "self_play_games_per_hour": 3600 * len(history) / elapsed,
                    "iteration_compute_seconds": statistics.mean(
                        p["iteration_duration"] for p in history
                    ),
                    "iterations": len(history),
                }
            )
        )
        return
    job = json.loads((source / "job.json").read_text())
    archive = root / args.archive
    try:
        if args.operation == "export":
            if args.gate:
                wait_gate(root / args.gate, "export")
            _, metrics = measured(
                lambda: module.export_job(
                    job,
                    source / "jobs" / job["id"],
                    ModelRegistry(source / "models"),
                    archive,
                    "0.36.0",
                ),
                disk_root=disk_root,
            )
            metrics["archive_bytes"] = archive.stat().st_size
        else:
            destination = root / (args.archive + "-import")
            destination.mkdir(exist_ok=True)
            (destination / "jobs").mkdir(exist_ok=True)
            store = Store(destination / "hnefatafl.sqlite3")
            _, metrics = measured(
                lambda: module.import_job(
                    archive,
                    destination,
                    store,
                    resume_info=lambda job, directory: {"can_resume": False},
                ),
                disk_root=disk_root,
            )
        metrics["status"] = "ok"
    except (ValueError, OSError) as exc:
        metrics = {"status": "error", "error": str(exc)}
    print(json.dumps(metrics))


def invocation(args, **values):
    command = [
        sys.executable,
        __file__,
        "--worker",
        "--root",
        str(args.root),
        "--baseline",
        str(args.baseline),
    ]
    if args.measure_disk:
        command.append("--measure-disk")
    for name, value in values.items():
        command.extend(["--" + name.replace("_", "-"), str(value)])
    return command


def run(args, **values):
    result = subprocess.run(invocation(args, **values), capture_output=True, text=True, check=True)
    return json.loads(result.stdout.splitlines()[-1])


def main(args):
    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    source = root / "source"
    if source.exists():
        raise ValueError("Choose a fresh benchmark directory")
    job_id = "a" * 32
    directory = source / "jobs" / job_id
    template = train(
        {
            "preset": "smoke",
            "seed": 17,
            "device": "cpu",
            "cpu_threads": 1,
            "max_game_plies": 4,
            "evaluation_max_plies": 2,
            "mcts_time_limit": 300,
            "output_dir": str(directory),
            "registry_dir": str(source / "models"),
        }
    )
    point = template["history"][0]
    if args.large_history:
        point = {**point, "diagnostic_timings": [1 / (i + 3) for i in range(1024)]}
    result = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "cpu_count": os.cpu_count(),
        "repetitions": args.repetitions,
        "fixture": "real 1-iteration CPU smoke checkpoints; repeated synthetic metric histories",
        "archives": [],
        "concurrent_training": [],
    }
    for count in args.iteration_counts:
        report = {
            **template,
            "iteration": count,
            "history": [{**point, "iteration": i + 1} for i in range(count)],
        }
        job = {
            "id": job_id,
            "kind": "train",
            "status": "completed",
            "created_at": "2026-01-02T03:04:05+00:00",
            "config": json.loads((directory / "config.json").read_text()),
            "progress": report,
            "result": report,
            "model_id": report["model"]["id"],
        }
        (source / "job.json").write_text(json.dumps(job))
        for name in ("progress.json", "result.json"):
            (directory / name).write_text(json.dumps(report, indent=2))
        cases = (
            (("pr", 1), ("tar", 1))
            if args.baseline_format == "tar"
            else (("zip", 0), ("zip-relaxed", 0), ("tar", 1), ("tar", 3), ("tar", 6))
        )
        for kind, level in cases:
            samples = []
            for repetition in range(args.repetitions):
                name = f"{kind}-{level}-{count}-{repetition}.archive"
                exported = run(args, kind=kind, level=level, operation="export", archive=name)
                imported = (
                    run(args, kind=kind, operation="import", archive=name)
                    if exported["status"] == "ok"
                    else None
                )
                samples.append({"export": exported, "import": imported})
                print(f"{count} iterations {kind} level {level}: {exported}", flush=True)
                shutil.rmtree(root / (name + "-import"), ignore_errors=True)
                (root / name).unlink(missing_ok=True)
            result["archives"].append(
                {"iterations": count, "kind": kind, "gzip_level": level, "samples": samples}
            )
    for repetition in range(args.training_repetitions):
        # Alternate the order of control and export scenarios to reduce drift.
        comparison = "pr" if args.baseline_format == "tar" else "zip-relaxed"
        kinds = (
            ("control", "tar", comparison)
            if repetition % 2 == 0
            else (comparison, "tar", "control")
        )
        for kind in kinds:
            name = f"training-{repetition}-{kind}"
            gate = root / name
            gate.mkdir()
            training = subprocess.Popen(
                invocation(args, operation="training", gate=name),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            exporting = None
            if kind != "control":
                exporting = subprocess.Popen(
                    invocation(
                        args,
                        operation="export",
                        kind=kind,
                        level=1,
                        gate=name,
                        archive=name + ".archive",
                    ),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
            deadline = time.monotonic() + 60
            while (
                not (gate / "training.ready").exists()
                or exporting is not None
                and not (gate / "export.ready").exists()
            ):
                if (
                    training.poll() is not None
                    or exporting is not None
                    and exporting.poll() is not None
                    or time.monotonic() > deadline
                ):
                    raise RuntimeError("Benchmark worker did not reach its barrier")
                time.sleep(0.05)
            (gate / "go").touch()
            stdout, stderr = training.communicate(timeout=120)
            if training.returncode:
                raise RuntimeError(stderr)
            entry = {
                "kind": kind,
                "repetition": repetition,
                "training": json.loads(stdout.splitlines()[-1]),
            }
            if exporting is not None:
                stdout, stderr = exporting.communicate(timeout=120)
                if exporting.returncode:
                    raise RuntimeError(stderr)
                entry["export"] = json.loads(stdout.splitlines()[-1])
            result["concurrent_training"].append(entry)
            print(entry, flush=True)
            shutil.rmtree(gate)
            (root / (name + ".archive")).unlink(missing_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("work/job-archives.json"))
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--training-repetitions", type=int, default=3)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--kind", default="tar", choices=["tar", "zip", "zip-relaxed", "pr"])
    parser.add_argument("--level", type=int, default=1)
    parser.add_argument("--operation", default="export", choices=["export", "import", "training"])
    parser.add_argument("--archive", default="job.archive")
    parser.add_argument("--gate", default="")
    parser.add_argument("--baseline-format", choices=["zip", "tar"], default="zip")
    parser.add_argument("--iteration-counts", nargs="+", type=int, default=[1, 100, 1000])
    parser.add_argument("--large-history", action="store_true")
    parser.add_argument("--measure-disk", action="store_true")
    arguments = parser.parse_args()
    worker(arguments) if arguments.worker else main(arguments)
