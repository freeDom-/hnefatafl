"""Measure real import HTTP responses, UI loading and training against a saved tree.

Each measured server is a fresh process. Fixture setup and frontend builds are
excluded. See docs/benchmarks/job-archive-second-review.md for reproduction/limits.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "benchmark-only-password-123"


def fixture(root, kind, baseline_tree):
    sys.path.insert(0, str(ROOT))
    from hnefatafl.job_archives import export_job
    from hnefatafl.models import ModelRegistry
    from hnefatafl.training import train

    directory = root / "jobs" / ("a" * 32)
    directory.mkdir(parents=True)
    result = train(
        {
            "preset": "smoke",
            "seed": 17,
            "device": "cpu",
            "cpu_threads": 1,
            "max_game_plies": 2,
            "evaluation_max_plies": 2,
            "output_dir": str(directory),
            "registry_dir": str(root / "models"),
        }
    )
    point = result["history"][0]
    if kind == "large_history":
        point = {**point, "diagnostic_timings": [1 / (i + 3) for i in range(1024)]}
    elif kind == "large_document":
        point = {**point, "diagnostic_note": "x " * 32768}
    report = {
        **result,
        "iteration": 1000,
        "active_iteration": 1000,
        "total_iterations": 1000,
        "history": [{**point, "iteration": i + 1} for i in range(1000)],
    }
    job = {
        "id": "a" * 32,
        "kind": "train",
        "status": "completed",
        "created_at": "2026-01-02T03:04:05+00:00",
        "config": json.loads((directory / "config.json").read_text()),
        "progress": report,
        "result": report,
        "model_id": result["model"]["id"],
    }
    for name in ("progress.json", "result.json"):
        (directory / name).write_text(json.dumps(report, indent=2))
    output = root / "fixture.tar.gz"
    export_job(job, directory, ModelRegistry(root / "models"), output, "0.36.0")
    spec = importlib.util.spec_from_file_location(
        "hnefatafl._import_review_baseline", baseline_tree / "hnefatafl/job_archives.py"
    )
    old = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = old
    spec.loader.exec_module(old)
    previous = root / "before.tar.gz"
    old.export_job(job, directory, ModelRegistry(root / "models"), previous, "0.36.0")
    assert output.read_bytes() == previous.read_bytes(), "Export bytes unexpectedly changed"
    return {
        "archive": str(output),
        "archive_bytes": output.stat().st_size,
        "source_report_bytes": (directory / "progress.json").stat().st_size,
        "iterations": 1000,
        "before_after_export_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
    }


def serve(args):
    os.environ["HNEFATAFL_DATA_DIR"] = str(args.root / "module-default")
    temporary = args.root / "temporary"
    temporary.mkdir(parents=True, exist_ok=True)
    import tempfile

    tempfile.tempdir = str(temporary)
    from hnefatafl.server import create_app
    import uvicorn

    app = create_app(args.root / "data", account_background=False, online_background=False)
    app.state.auth.create_account(
        "Benchmark", "archive@example.test", PASSWORD, "ADMIN", bootstrap=True
    )
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="error")


def training(args):
    from hnefatafl.training import train

    gate = args.root
    gate.mkdir(parents=True, exist_ok=True)
    started = None

    def progress(report):
        nonlocal started
        if started is None and len(report.get("history", [])) >= 4:
            (gate / "ready").touch()
            deadline = time.monotonic() + 60
            while not (gate / "go").exists():
                if time.monotonic() > deadline:
                    raise TimeoutError("Training benchmark barrier timed out")
                time.sleep(0.01)
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
    print(
        json.dumps(
            {
                "seconds": elapsed,
                "iterations": len(result["history"]) - 4,
                "iterations_per_second": (len(result["history"]) - 4) / elapsed,
            }
        )
    )


def rss(pid):
    text = Path(f"/proc/{pid}/status").read_text()
    return (
        int(next(line.split()[1] for line in text.splitlines() if line.startswith("VmHWM:"))) * 1024
    )


def worker_command(args, root, mode, implementation):
    return [
        sys.executable,
        __file__,
        "--worker",
        mode,
        "--root",
        str(root),
        "--baseline-tree",
        str(args.baseline_tree),
        "--implementation",
        implementation,
        "--port",
        str(args.port),
    ]


@contextlib.contextmanager
def server(args, root, implementation):
    import httpx

    root.mkdir(parents=True)
    with (root / "server.log").open("w") as log:
        process = subprocess.Popen(
            worker_command(args, root, "serve", implementation),
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            origin = f"http://127.0.0.1:{args.port}"
            with httpx.Client(base_url=origin, timeout=300) as client:
                deadline = time.monotonic() + 30
                while True:
                    try:
                        if client.get("/api/health").status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    if process.poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError((root / "server.log").read_text())
                    time.sleep(0.05)
                login = client.post(
                    "/api/auth/login",
                    json={"identity": "Benchmark", "password": PASSWORD},
                    headers={"Origin": origin},
                )
                assert login.status_code == 200, login.text
                session = login.json()
                client.headers.update({"Origin": origin, "X-CSRF-Token": session["csrf_token"]})
                yield process, client, session
        finally:
            process.terminate()
            try:
                process.wait(10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def post_import(client, archive):
    started = time.perf_counter()
    with Path(archive).open("rb") as handle:
        response = client.post(
            "/api/training/jobs/import?summary=true", files={"file": ("job.tar.gz", handle)}
        )
    elapsed = time.perf_counter() - started
    assert response.status_code == 201, response.text[:1000]
    return {
        "seconds": elapsed,
        "response_bytes": len(response.content),
        "summary": b'"summary":true' in response.content,
    }


def api_case(args, root, implementation, archive):
    with server(args, root, implementation) as (process, client, _):
        result = post_import(client, archive)
        result["peak_server_rss_bytes"] = rss(process.pid)
        return result


def browser_case(args, root, implementation, archive):
    with server(args, root, implementation) as (process, client, session):
        config = {
            "origin": str(client.base_url).rstrip("/"),
            "archive": archive,
            "cookies": dict(client.cookies),
            "csrf": session["csrf_token"],
            "account": session["account"]["id"],
            "chromium": args.chromium,
        }
        completed = subprocess.run(
            ["node", str(ROOT / "scripts/benchmark_job_import_ui.mjs")],
            input=json.dumps(config),
            capture_output=True,
            text=True,
            timeout=300,
        )
        if completed.returncode:
            raise RuntimeError(completed.stderr)
        result = json.loads(completed.stdout.splitlines()[-1])
        result["peak_server_rss_bytes"] = rss(process.pid)
        return result


def training_case(args, root, implementation, archive):
    gate = root / "gate"
    gate.mkdir(parents=True)
    process = subprocess.Popen(
        worker_command(args, gate, "training", "after"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 30
        while not (gate / "ready").exists():
            if process.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError("Training warmup failed: " + process.communicate()[1])
            time.sleep(0.01)
        if implementation == "control":
            (gate / "go").touch()
            imported = None
        else:
            with server(args, root / "server", implementation) as (_, client, _):
                (gate / "go").touch()
                imported = post_import(client, archive)
        stdout, stderr = process.communicate(timeout=60)
        assert process.returncode == 0, stderr
        return {"training": json.loads(stdout.splitlines()[-1]), "import": imported}
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--baseline-tree", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--training-repetitions", type=int, default=3)
    parser.add_argument("--port", type=int, default=9865)
    parser.add_argument("--chromium", default="/usr/bin/chromium")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--worker", choices=["serve", "training"])
    parser.add_argument("--implementation", choices=["before", "after"], default="after")
    args = parser.parse_args()
    args.root = args.root.resolve()
    args.baseline_tree = args.baseline_tree.resolve()
    sys.path.insert(0, str(args.baseline_tree if args.implementation == "before" else ROOT))
    if args.worker:
        serve(args) if args.worker == "serve" else training(args)
        return
    args.root.mkdir(parents=True, exist_ok=args.resume)
    result = (
        json.loads(args.output.read_text())
        if args.resume
        else {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "cpu_count": os.cpu_count(),
            "repetitions": args.repetitions,
            "fixtures": {},
            "samples": [],
            "concurrent_training": [],
        }
    )
    for kind in ("ordinary", "large_history", "large_document"):
        info = result["fixtures"].get(kind) or fixture(args.root / kind, kind, args.baseline_tree)
        result["fixtures"][kind] = info
        args.output.write_text(json.dumps(result, indent=2) + "\n")
        for repetition in range(args.repetitions):
            for implementation in (
                ("before", "after") if repetition % 2 == 0 else ("after", "before")
            ):
                if any(
                    row["kind"] == kind
                    and row["implementation"] == implementation
                    and row["repetition"] == repetition
                    for row in result["samples"]
                ):
                    continue
                entry = {"kind": kind, "implementation": implementation, "repetition": repetition}
                for name, case in (("api", api_case), ("browser", browser_case)):
                    root = args.root / f"{kind}-{implementation}-{repetition}-{name}"
                    if args.resume and root.exists():
                        shutil.rmtree(root)
                    entry[name] = case(args, root, implementation, info["archive"])
                    shutil.rmtree(root)
                result["samples"].append(entry)
                args.output.write_text(json.dumps(result, indent=2) + "\n")
                print(json.dumps(entry), flush=True)
    for repetition in range(args.training_repetitions):
        kinds = (
            ("control", "before", "after")
            if repetition % 2 == 0
            else ("after", "before", "control")
        )
        for implementation in kinds:
            if any(
                row["implementation"] == implementation and row["repetition"] == repetition
                for row in result["concurrent_training"]
            ):
                continue
            root = args.root / f"training-{implementation}-{repetition}"
            entry = {
                "implementation": implementation,
                "repetition": repetition,
                **training_case(
                    args, root, implementation, result["fixtures"]["ordinary"]["archive"]
                ),
            }
            result["concurrent_training"].append(entry)
            args.output.write_text(json.dumps(result, indent=2) + "\n")
            print(json.dumps(entry), flush=True)
            shutil.rmtree(root)


if __name__ == "__main__":
    main()
