"""Measure archive log sanitization and real training during imports/exports.

Run separately from tests/builds. The baseline tree must be a git archive of the
reviewed commit. Timing workers and traced-memory workers are separate processes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import platform
import resource
import shutil
import subprocess
import sys
import time
import tracemalloc

ROOT = Path(__file__).resolve().parents[1]


class Sink:
    def __init__(self):
        self.bytes = self.maximum_write = 0
        self.digest = hashlib.sha256()
        self.tail = ""
        self.leaked = False

    def write(self, text):
        data = text.encode()
        self.bytes += len(data)
        self.maximum_write = max(self.maximum_write, len(data))
        self.digest.update(data)
        probe = self.tail + text
        self.leaked |= "UNIQUE_SECRET_END" in probe or "battery staple" in probe
        self.tail = probe[-32:]


def stream(args):
    from hnefatafl import job_archives

    sink = Sink()
    with args.source.open() as source:
        if args.trace:
            tracemalloc.start()
        started = time.perf_counter()
        if args.implementation == "before":
            while chunk := source.readline(65536):
                sink.write(job_archives.clean_text(chunk))
        else:
            job_archives.clean_log(source, sink)
        elapsed = time.perf_counter() - started
        peak = tracemalloc.get_traced_memory()[1] if args.trace else None
        if args.trace:
            tracemalloc.stop()
    return {
        "seconds": elapsed,
        "source_bytes": args.source.stat().st_size,
        "output_bytes": sink.bytes,
        "maximum_write_bytes": sink.maximum_write,
        "output_sha256": sink.digest.hexdigest(),
        "leaked_synthetic_secret": sink.leaked,
        "peak_traced_bytes": peak,
        "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        * (1 if sys.platform == "darwin" else 1024),
    }


def text_case(args):
    from hnefatafl.job_archives import clean_text

    value = args.source.read_text()
    if args.trace:
        tracemalloc.start()
    started = time.perf_counter()
    cleaned = clean_text(value)
    elapsed = time.perf_counter() - started
    peak = tracemalloc.get_traced_memory()[1] if args.trace else None
    if args.trace:
        tracemalloc.stop()
    sink = Sink()
    sink.write(cleaned)
    return {
        "seconds": elapsed,
        "source_bytes": args.source.stat().st_size,
        "output_bytes": sink.bytes,
        "output_sha256": sink.digest.hexdigest(),
        "leaked_synthetic_secret": sink.leaked,
        "peak_traced_bytes": peak,
        "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        * (1 if sys.platform == "darwin" else 1024),
    }


def export(args):
    from hnefatafl.job_archives import export_job
    from hnefatafl.models import ModelRegistry

    job = json.loads(args.job.read_text())
    registry = ModelRegistry(args.job.parent / "models")
    args.gate.mkdir(parents=True)
    (args.gate / "ready").touch()
    wait_ready(args.gate / "go")
    started = time.perf_counter()
    export_job(job, args.job.parent / "jobs" / job["id"], registry, args.archive_output, "0.36.0")
    return {
        "seconds": time.perf_counter() - started,
        "archive_bytes": args.archive_output.stat().st_size,
        "sha256": hashlib.sha256(args.archive_output.read_bytes()).hexdigest(),
    }


def wait_ready(path, processes=()):
    deadline = time.monotonic() + 60
    while not path.exists():
        if time.monotonic() > deadline or any(p.poll() is not None for p in processes):
            raise RuntimeError(f"Benchmark barrier did not become ready: {path}")
        time.sleep(0.01)


def command(args, implementation, *extra):
    return [
        sys.executable,
        str(Path(__file__).resolve()),
        "--root",
        str(args.root),
        "--baseline-tree",
        str(args.baseline_tree),
        "--implementation",
        implementation,
        *map(str, extra),
    ]


def fixture(path, kind, size):
    patterns = {
        "plain": "x" * 1024,
        "ordinary": "iteration=1000 policy_loss=0.25 worker alive\n",
        "secret_word": "password",
        "quoted_lines": 'password="correct horse battery staple" done\n',
        "quoted_value": "x" * 1024,
    }
    pattern = patterns[kind].encode()
    block = (pattern * (65536 // len(pattern) + 1))[:65536]
    # A multiple of the pattern keeps repeated diagnostic lines well formed.
    if kind in {"ordinary", "quoted_lines"}:
        block = pattern * (65536 // len(pattern))
    remaining = size * 1024 * 1024
    with path.open("wb") as output:
        if kind == "quoted_value":
            output.write(b'password="correct horse ')
        while remaining:
            part = block[:remaining]
            output.write(part)
            remaining -= len(part)
        output.write(b'UNIQUE_SECRET_END" ordinary after\n' if kind == "quoted_value" else b"\n")


def training_export(args, root, implementation, job, archive):
    import benchmark_job_import_review as imports

    gate = root / "training"
    process = subprocess.Popen(
        imports.worker_command(args, gate, "training", "after"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    exporting = None
    try:
        wait_ready(gate / "ready", (process,))
        if implementation != "control":
            export_gate = root / "export"
            exporting = subprocess.Popen(
                command(
                    args,
                    implementation,
                    "--worker",
                    "export",
                    "--job",
                    job,
                    "--gate",
                    export_gate,
                    "--archive-output",
                    archive,
                ),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            wait_ready(export_gate / "ready", (process, exporting))
            (export_gate / "go").touch()
        (gate / "go").touch()
        stdout, stderr = process.communicate(timeout=90)
        assert process.returncode == 0, stderr
        result = {"training": json.loads(stdout.splitlines()[-1])}
        if exporting:
            stdout, stderr = exporting.communicate(timeout=90)
            assert exporting.returncode == 0, stderr
            result["export"] = json.loads(stdout.splitlines()[-1])
        return result
    finally:
        for child in (process, exporting):
            if child is not None and child.poll() is None:
                child.kill()
                child.wait()


def training_cases(args, data):
    import benchmark_job_import_review as imports
    from hnefatafl.job_archives import export_job
    from hnefatafl.models import ModelRegistry

    directory = args.root / "training-fixture"
    info = imports.fixture(directory, "ordinary", args.baseline_tree)
    source = directory / "jobs" / ("a" * 32)
    shutil.copyfile(args.root / "fixtures/plain-8.log", source / "worker.log")
    report = json.loads((source / "progress.json").read_text())
    job = {
        "id": "a" * 32,
        "kind": "train",
        "status": "completed",
        "created_at": "2026-01-02T03:04:05+00:00",
        "config": json.loads((source / "config.json").read_text()),
        "progress": report,
        "result": report,
        "model_id": report["model"]["id"],
    }
    job_path = directory / "benchmark-job.json"
    job_path.write_text(json.dumps(job))
    archive = directory / "with-log.tar.gz"
    export_job(job, source, ModelRegistry(directory / "models"), archive, "0.36.0")
    data["training_fixture"] = {**info, "worker_log_bytes": (source / "worker.log").stat().st_size}
    for operation in ("export", "import"):
        for repetition in range(args.training_repetitions):
            order = (
                ("control", "before", "after")
                if repetition % 2 == 0
                else ("after", "before", "control")
            )
            hashes = []
            for implementation in order:
                root = args.root / f"training-{operation}-{implementation}-{repetition}"
                result = (
                    training_export(
                        args, root, implementation, job_path, directory / f"{implementation}.tar.gz"
                    )
                    if operation == "export"
                    else imports.training_case(args, root, implementation, archive)
                )
                row = {
                    "operation": operation,
                    "implementation": implementation,
                    "repetition": repetition,
                    **result,
                }
                data["training"].append(row)
                if operation == "export" and implementation != "control":
                    hashes.append(result["export"]["sha256"])
                args.output.write_text(json.dumps(data, indent=2) + "\n")
                print(json.dumps(row), flush=True)
                shutil.rmtree(root)
            if hashes:
                assert len(set(hashes)) == 1, "Nonsecret export bytes unexpectedly changed"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--baseline-tree", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--training-repetitions", type=int, default=0)
    parser.add_argument("--port", type=int, default=9865)
    parser.add_argument("--worker", choices=["stream", "text", "export"])
    parser.add_argument("--implementation", choices=["before", "after"], default="after")
    parser.add_argument("--source", type=Path)
    parser.add_argument("--trace", action="store_true")
    parser.add_argument("--job", type=Path)
    parser.add_argument("--gate", type=Path)
    parser.add_argument("--archive-output", type=Path)
    args = parser.parse_args()
    args.root, args.baseline_tree = args.root.resolve(), args.baseline_tree.resolve()
    sys.path.insert(0, str(args.baseline_tree if args.implementation == "before" else ROOT))
    if args.worker:
        case = {"stream": stream, "text": text_case, "export": export}[args.worker]
        print(json.dumps(case(args)))
        return
    args.root.mkdir(parents=True)
    (args.root / "fixtures").mkdir()
    data = {
        "baseline": "6b31d9f",
        "python": platform.python_version(),
        "platform": platform.platform(),
        "samples": [],
        "memory": [],
        "training": [],
    }
    for kind in ("plain", "ordinary", "secret_word", "quoted_value", "quoted_lines"):
        for size in (1, 8, 64):
            source = args.root / "fixtures" / f"{kind}-{size}.log"
            fixture(source, kind, size)
            for repetition in range(args.repetitions):
                outputs = []
                for implementation in (
                    ("before", "after") if repetition % 2 == 0 else ("after", "before")
                ):
                    completed = subprocess.run(
                        command(args, implementation, "--worker", "stream", "--source", source),
                        check=True,
                        capture_output=True,
                        text=True,
                        timeout=180,
                    )
                    row = {
                        "mode": "stream",
                        "kind": kind,
                        "mib": size,
                        "implementation": implementation,
                        "repetition": repetition,
                        **json.loads(completed.stdout),
                    }
                    data["samples"].append(row)
                    outputs.append(row["output_sha256"])
                    if implementation == "after":
                        assert not row["leaked_synthetic_secret"]
                    args.output.write_text(json.dumps(data, indent=2) + "\n")
                    print(json.dumps(row), flush=True)
                if kind in {"plain", "ordinary", "secret_word"}:
                    assert len(set(outputs)) == 1
            if kind in {"plain", "quoted_value"}:
                completed = subprocess.run(
                    command(args, "after", "--worker", "stream", "--source", source, "--trace"),
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=180,
                )
                data["memory"].append(
                    {"mode": "stream", "kind": kind, "mib": size, **json.loads(completed.stdout)}
                )
                args.output.write_text(json.dumps(data, indent=2) + "\n")
    for kind in ("plain", "secret_word", "quoted_value"):
        for size in (1, 8, 64):
            source = args.root / "fixtures" / f"{kind}-{size}.log"
            for repetition in range(args.repetitions):
                for implementation in (
                    ("before", "after") if repetition % 2 == 0 else ("after", "before")
                ):
                    completed = subprocess.run(
                        command(args, implementation, "--worker", "text", "--source", source),
                        check=True,
                        capture_output=True,
                        text=True,
                        timeout=180,
                    )
                    row = {
                        "mode": "text",
                        "kind": kind,
                        "mib": size,
                        "implementation": implementation,
                        "repetition": repetition,
                        **json.loads(completed.stdout),
                    }
                    if implementation == "after":
                        assert not row["leaked_synthetic_secret"]
                    data["samples"].append(row)
                    args.output.write_text(json.dumps(data, indent=2) + "\n")
                    print(json.dumps(row), flush=True)
            completed = subprocess.run(
                command(args, "after", "--worker", "text", "--source", source, "--trace"),
                check=True,
                capture_output=True,
                text=True,
                timeout=180,
            )
            data["memory"].append(
                {"mode": "text", "kind": kind, "mib": size, **json.loads(completed.stdout)}
            )
            args.output.write_text(json.dumps(data, indent=2) + "\n")
    if args.training_repetitions:
        training_cases(args, data)


if __name__ == "__main__":
    main()
