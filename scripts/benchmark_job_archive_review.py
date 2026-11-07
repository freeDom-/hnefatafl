"""Compare PR #58's snapshot/admission resource use with a previous source tree.

Use --baseline-tree with a local `git archive c85f3d4 hnefatafl pyproject.toml`
checkout. Each case runs in a fresh process. Catalogs reuse one safe toy checkpoint
through hard links to model historical duplicate entries without large binaries.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[1]


def snapshot_case(args):
    import torch
    from hnefatafl import job_archives
    from hnefatafl.models import ModelRegistry, PolicyValueNet, save_checkpoint

    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    registry = ModelRegistry(root / "models")
    torch.set_num_threads(1)
    torch.manual_seed(17)
    seed = registry.import_model(
        save_checkpoint(
            root / "seed.pt", PolicyValueNet("brandubh", 4, 0), {"name": "Catalog seed"}
        )
    )
    template = registry._read_index()[0]
    entries = []
    for index in range(args.catalog):
        identifier = f"{index + 1:024x}"
        os.link(seed["path"], registry.root / f"{identifier}.pt")
        entries.append({**template, "id": identifier, "name": f"Historical duplicate {index}"})
    registry._write_index(entries)
    directory = root / "jobs" / ("a" * 32)
    directory.mkdir(parents=True)
    (directory / "history.json").write_text('{"history":[{"iteration":1}]}')
    job = {
        "id": "a" * 32,
        "kind": "train",
        "status": "completed",
        "created_at": "2026-01-02T03:04:05+00:00",
        "config": {"preset": "smoke", "ruleset": "brandubh"},
        "model_id": entries[0]["id"],
        "progress": {"best_models": [{"model_id": entries[1]["id"]}]},
    }
    counters = {"links": 0, "stats": 0, "opens": 0, "copies": 0, "copied_bytes": 0}
    link, stat, opened, copied = os.link, os.stat, Path.open, shutil.copyfile
    held = False

    def count_link(src, dst, *a, **kw):
        if held:
            counters["links"] += 1
        return link(src, dst, *a, **kw)

    def count_stat(path, *a, **kw):
        if held:
            counters["stats"] += 1
        return stat(path, *a, **kw)

    def count_open(path, *a, **kw):
        if held:
            counters["opens"] += 1
        return opened(path, *a, **kw)

    def count_copy(src, dst, *a, **kw):
        if held:
            counters["copies"] += 1
            counters["copied_bytes"] += stat(src).st_size
        return copied(src, dst, *a, **kw)

    os.link, os.stat, Path.open, shutil.copyfile = count_link, count_stat, count_open, count_copy
    try:
        # The API normally places snapshots on the data filesystem. /tmp is a
        # different device in the benchmark environment for the cross-device case.
        parent = Path(tempfile.gettempdir()) if args.cross_device else root
        with tempfile.TemporaryDirectory(prefix="snapshot-benchmark-", dir=parent) as tmp:
            held = True
            started = time.perf_counter()
            snapshot = job_archives.snapshot_job(job, directory, registry, Path(tmp))
            lock_seconds = time.perf_counter() - started
            held = False
            context = (
                snapshot if hasattr(snapshot, "__enter__") else contextlib.nullcontext(snapshot)
            )
            with context:
                started = time.perf_counter()
                job_archives.export_snapshot(snapshot, root / "job.tar.gz", "0.36.0")
                unlocked_seconds = time.perf_counter() - started
        result = {
            "catalog": args.catalog,
            "referenced_models": 2,
            "cross_device": args.cross_device,
            "different_devices": root.stat().st_dev != parent.stat().st_dev,
            "global_lock_seconds": lock_seconds,
            "unlocked_export_seconds": unlocked_seconds,
            "filesystem_operations_under_global_locks": counters,
        }
    finally:
        held = False
        os.link, os.stat, Path.open, shutil.copyfile = link, stat, opened, copied
    return result


def admission_case(args):
    os.environ["HNEFATAFL_DATA_DIR"] = str(args.root / "module-default")
    sys.path.insert(0, str(ROOT / "tests"))
    from auth_support import AuthenticatedClient
    from hnefatafl.server import create_app
    from hnefatafl import job_archives
    import starlette.formparsers

    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    temporary = root / "temporary"
    temporary.mkdir()
    tempfile.tempdir = str(temporary)
    spooled = starlette.formparsers.SpooledTemporaryFile
    files = []

    def capture(*a, **kw):
        file = spooled(*a, **kw)
        files.append(file)
        return file

    starlette.formparsers.SpooledTemporaryFile = capture
    entered, release = threading.Event(), threading.Event()
    original = job_archives.extract

    def pause(*a):
        entered.set()
        if not release.wait(20):
            raise RuntimeError("Benchmark barrier timed out")
        raise job_archives.ArchiveError("Deliberately malformed benchmark upload")

    with AuthenticatedClient(
        create_app(root / "server", account_background=False, online_background=False)
    ) as client:
        payload = b"x" * (2 * 1024 * 1024)

        def upload(_):
            started = time.perf_counter()
            response = client.post(
                "/api/training/jobs/import",
                files={"file": ("job.tar.gz", payload, "application/gzip")},
            )
            return {"status": response.status_code, "seconds": time.perf_counter() - started}

        job_archives.extract = pause
        with concurrent.futures.ThreadPoolExecutor(12) as pool:
            first = pool.submit(upload, 0)
            try:
                assert entered.wait(10)
                waiting = [pool.submit(upload, i) for i in range(8)]
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    staged = list(temporary.glob("hnefatafl-upload-*/job.archive"))
                    if len(staged) == 9 or all(future.done() for future in waiting):
                        break
                    time.sleep(0.01)
                started = time.perf_counter()
                ordinary = pool.submit(client.get, "/api/training/jobs?summary=true").result(5)
                ordinary_seconds = time.perf_counter() - started
                result = {
                    "parallel_uploads": 9,
                    "staged_uploads": len(staged),
                    "staged_upload_bytes": sum(path.stat().st_size for path in staged),
                    "spooled_uploads": len(files),
                    "spooled_upload_bytes": sum(
                        os.fstat(file.fileno()).st_size for file in files if not file.closed
                    ),
                    "completed_before_release": sum(future.done() for future in waiting),
                    "ordinary_request_status": ordinary.status_code,
                    "ordinary_request_seconds": ordinary_seconds,
                }
            finally:
                release.set()
            result["responses"] = [first.result(10), *[future.result(10) for future in waiting]]
        assert all(file.closed for file in files)
        assert not list(temporary.glob("hnefatafl-upload-*"))
        result["remaining_temporary_uploads"] = 0
    job_archives.extract = original
    return result


def descriptor_case(args):
    import errno
    import resource
    from hnefatafl import job_archives
    from hnefatafl.models import ModelRegistry

    root = args.root.resolve()
    directory = root / "jobs" / ("a" * 32)
    (directory / "best").mkdir(parents=True)
    for index in range(args.artifacts):
        (directory / "best" / f"iteration-{index:06d}.pt").write_bytes(f"artifact-{index}".encode())
    registry = ModelRegistry(root / "models")
    target = root / "snapshot"
    original_link, original_copy = os.link, shutil.copyfileobj
    attempts = 0
    protected = True

    def cross_device(source, destination, *a, **kw):
        nonlocal attempts
        if Path(destination).is_relative_to(target):
            attempts += 1
            raise OSError(errno.EXDEV, "simulated cross-device target")
        return original_link(source, destination, *a, **kw)

    def copy(source, destination, *a, **kw):
        assert not protected, "File copying ran under lifecycle locks"
        return original_copy(source, destination, *a, **kw)

    os.link, shutil.copyfileobj = cross_device, copy
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    resource.setrlimit(resource.RLIMIT_NOFILE, (min(args.fd_limit, hard), hard))
    initial = len(os.listdir("/proc/self/fd"))
    job = {
        "id": "a" * 32,
        "kind": "train",
        "status": "completed",
        "config": {"preset": "smoke"},
        "created_at": "2026-01-02T03:04:05+00:00",
    }
    started = time.perf_counter()
    try:
        with job_archives.snapshot_job(job, directory, registry, target) as snapshot:
            captured = len(os.listdir("/proc/self/fd"))
            locked_seconds = time.perf_counter() - started
            protected = False
            shutil.rmtree(directory)
            started = time.perf_counter()
            snapshot.materialize()
            copy_seconds = time.perf_counter() - started
            assert len(list((target / "best").iterdir())) == args.artifacts
            assert (
                target / "best" / f"iteration-{args.artifacts - 1:06d}.pt"
            ).read_bytes() == f"artifact-{args.artifacts - 1}".encode()
        result = {
            "status": "ok",
            "initial_fds": initial,
            "snapshot_fds": captured,
            "global_lock_seconds": locked_seconds,
            "unlocked_copy_seconds": copy_seconds,
        }
    except OSError as error:
        result = {
            "status": "error",
            "errno": error.errno,
            "failed_artifact": attempts,
            "seconds_until_failure": time.perf_counter() - started,
        }
    finally:
        protected = False
        os.link, shutil.copyfileobj = original_link, original_copy
        resource.setrlimit(resource.RLIMIT_NOFILE, (soft, hard))
    assert len(os.listdir("/proc/self/fd")) == initial
    assert not list(root.rglob(".archive-pins-*"))
    return {"artifacts": args.artifacts, "descriptor_limit": args.fd_limit, **result}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--baseline-tree", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--implementation", choices=["before", "after"], default="after")
    parser.add_argument(
        "--case", choices=["snapshot", "admission", "descriptors"], default="snapshot"
    )
    parser.add_argument("--descriptors", action="store_true")
    parser.add_argument("--fd-limit", type=int, default=64)
    parser.add_argument("--artifacts", type=int, default=1200)
    parser.add_argument("--catalog", type=int, default=2)
    parser.add_argument("--cross-device", action="store_true")
    args = parser.parse_args()
    if args.worker:
        sys.path.insert(
            0, str(args.baseline_tree.resolve() if args.implementation == "before" else ROOT)
        )
        result = {
            "snapshot": snapshot_case,
            "admission": admission_case,
            "descriptors": descriptor_case,
        }[args.case](args)
        print(json.dumps(result))
        return
    args.root.mkdir(parents=True, exist_ok=False)
    rows = []
    cases = [("snapshot", count, False, 0, 0) for count in (2, 1000, 10000)] + [
        ("snapshot", 1000, True, 0, 0),
        ("admission", 2, False, 0, 0),
    ]
    if args.descriptors:
        cases += [
            ("descriptors", 2, True, count, limit)
            for count, limit in [(300, 512), (1200, 64), (1200, 2048)]
        ]
    for case, catalog, cross_device, artifacts, fd_limit in cases:
        for repetition in range(args.repetitions):
            for implementation in (
                ("before", "after") if repetition % 2 == 0 else ("after", "before")
            ):
                path = (
                    args.root
                    / f"{case}-{catalog}-{cross_device}-{artifacts}-{fd_limit}-{implementation}-{repetition}"
                )
                command = [
                    sys.executable,
                    __file__,
                    "--worker",
                    "--baseline-tree",
                    str(args.baseline_tree),
                    "--root",
                    str(path),
                    "--output",
                    str(args.output),
                    "--implementation",
                    implementation,
                    "--case",
                    case,
                    "--catalog",
                    str(catalog),
                ]
                if cross_device:
                    command.append("--cross-device")
                if case == "descriptors":
                    command.extend(["--artifacts", str(artifacts), "--fd-limit", str(fd_limit)])
                completed = subprocess.run(command, capture_output=True, text=True, check=True)
                entry = {
                    "case": case,
                    "implementation": implementation,
                    "repetition": repetition,
                    **json.loads(completed.stdout.splitlines()[-1]),
                }
                print(json.dumps(entry), flush=True)
                rows.append(entry)
                shutil.rmtree(path)
    args.output.write_text(json.dumps({"samples": rows}, indent=2) + "\n")


if __name__ == "__main__":
    main()
