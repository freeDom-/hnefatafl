"""Measure the cost of remote training monitoring between worker and server.

Remote workers republish a job's snapshot at a bounded cadence so intermediate
counters reach the server while a long phase runs. That cadence must not
retransmit a retained history, metrics or log tail that did not change, and the
server must not rewrite a large retained row for a liveness refresh alone.

Three measurements, all on CPU and independent of a GPU:

* the worker's report: payload bytes and serialization time for a first
  snapshot, a repeated unchanged snapshot (the pre-optimization behavior) and a
  bounded heartbeat refresh;
* the projection of each at the configured cadence: bytes and requests per
  minute for a representative history;
* the server's persistence: sanitizing a report, merging it over the retained
  snapshot, and writing the job row.

Run in a fresh process without other benchmarks or tests.
"""

import argparse
import json
import platform
import statistics
import sys
import tempfile
import threading
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from hnefatafl.remote_training import clean  # noqa: E402
from hnefatafl.remote_worker import JobRunner, Lease, PROGRESS_REPORT_SECONDS  # noqa: E402
from hnefatafl.storage import Store  # noqa: E402

JOB_ID = "a" * 32
LEASE_TOKEN = "b" * 40
TOKEN = "c" * 40


class ReplyAPI:
    token = TOKEN
    timeout = 0.1

    def redact(self, message, lease_token=""):
        return message

    def request(self, route, payload=None, **kwargs):
        return {"lease_seconds": 120, "cancel_requested": False}


def lease() -> Lease:
    return Lease(
        {
            "id": JOB_ID,
            "kind": "train",
            "config": {"ruleset": "brandubh", "model_name": "Remote Ulf"},
            "lease_token": LEASE_TOKEN,
            "resume_available": False,
        },
        10,
        120,
    )


def history_row(iteration: int) -> dict:
    """One retained history point, close to what training actually records."""
    return {
        "iteration": iteration,
        "policy_loss": 2.5 / (iteration + 1),
        "value_loss": 0.7 / (iteration + 1),
        "replay_positions": min(iteration * 600, 20000),
        "evaluation_status": "completed",
        "performance": {
            "self_play_seconds": 120.5,
            "optimization_seconds": 40.25,
            "evaluation_seconds": 42.0,
            "iteration_wall_seconds": 260.0,
            "positions_per_second": 92.4,
            "simulations_per_second": 4210.5,
        },
    }


def child_directory(root: Path, iterations: int) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    history = [history_row(index) for index in range(iterations)]
    (root / "progress.json").write_text(
        json.dumps(
            {
                "run": "benchmark-run",
                "revision": 2,
                "status": "running",
                "stage": "self_play",
                "iteration": iterations,
                "total_iterations": iterations + 10,
                "duration": 1200.0,
                "history": history,
                "metrics": {
                    "policy_loss": 0.4,
                    "value_loss": 0.2,
                    "self_play": [{"plies": 40, "winner": "defenders"} for _ in range(iterations)],
                    "evaluation": {"games": [{"winner": "defenders"} for _ in range(20)]},
                },
                "best_models": [{"iteration": 1, "model_id": "d" * 24}],
            }
        )
    )
    (root / "heartbeat.json").write_text(
        json.dumps(
            {
                "run": "benchmark-run",
                "revision": 2,
                "status": "running",
                "stage": "self_play",
                "iteration": iterations,
                "total_iterations": iterations + 10,
                "duration": 1205.0,
                "phase_elapsed": 300.0,
                "last_progress_seconds": 4.0,
                "phase_progress": {"games_completed": 3, "games_total": 24, "games_active": 5},
            }
        )
    )
    return root


def payload_bytes(payload: dict) -> int:
    return len(json.dumps(payload, allow_nan=False).encode())


def timed(function, repeats: int) -> dict:
    """Median wall time of one repeatable operation."""
    samples = []
    for _ in range(repeats):
        started = time.perf_counter()
        function()
        samples.append(time.perf_counter() - started)
    return {"median_seconds": statistics.median(samples), "samples": samples}


def worker_report(root: Path, *, repeats: int) -> dict:
    """First snapshot, repeated unchanged snapshot and bounded refresh."""
    runner = JobRunner(ReplyAPI(), lease(), root, threading.Event())
    first, history_end, _ = runner._progress_report()
    runner.history_sent, runner.sent_revision = history_end, 2
    bounded, _, _ = runner._progress_report()
    # The pre-optimization behavior: the whole retained snapshot every time.
    runner.sent_revision = None
    repeated, _, _ = runner._progress_report()
    runner.sent_revision = 2

    def measure(report: dict) -> dict:
        """Payload size and the serialization the worker performs when posting."""
        measured = timed(lambda: json.dumps(report, allow_nan=False), repeats)
        measured["bytes"] = payload_bytes(report)
        return measured

    return {
        "first_snapshot": measure(first),
        "repeated_unchanged_snapshot": measure(repeated),
        "bounded_heartbeat": measure(bounded),
    }


def server_cost(root: Path, root_dir: Path, *, repeats: int) -> dict:
    """Sanitize, merge and persist a report the way the progress endpoint does."""
    store = Store(root_dir / "store")
    job = {
        "id": JOB_ID,
        "kind": "train",
        "execution": "remote",
        "status": "running",
        "config": {"ruleset": "brandubh"},
        "progress": {},
        "created_at": "2026-01-01T00:00:00+00:00",
        "lease_token": LEASE_TOKEN,
    }
    runner = JobRunner(ReplyAPI(), lease(), root, threading.Event())
    full, history_end, _ = runner._progress_report()
    runner.history_sent, runner.sent_revision = history_end, 2
    bounded, _, _ = runner._progress_report()

    def persist(report: dict) -> dict:
        """Sanitize, merge and persist exactly what the progress endpoint does."""

        def write():
            sanitized = clean(report["progress"])
            retained = job["progress"] if isinstance(job["progress"], dict) else {}
            job["progress"] = {**retained, **sanitized}
            store.save_job(job)

        return timed(write, repeats)

    full_cost = persist(full)
    bounded_cost = persist(bounded)
    return {
        "full_snapshot": full_cost,
        "bounded_heartbeat": bounded_cost,
        "stored_row_bytes": len(json.dumps(job, allow_nan=False)),
    }


def per_minute(measurement: dict) -> dict:
    cadence = PROGRESS_REPORT_SECONDS
    reports = 60.0 / cadence
    return {
        "reports_per_minute": reports,
        "payload_bytes_per_minute": measurement["bytes"] * reports,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=5)
    args = parser.parse_args()

    report: dict = {
        "environment": {
            "platform": platform.platform(),
            "python": platform.python_version(),
            "cpu_count": __import__("os").cpu_count(),
        },
        "cadence_seconds": PROGRESS_REPORT_SECONDS,
        "workloads": {},
    }
    with tempfile.TemporaryDirectory(prefix="hnefatafl-monitoring-") as temporary:
        base = Path(temporary)
        for name, iterations in (("small", 10), ("medium", 200), ("large", 2000)):
            root = child_directory(base / name / "job", iterations)
            worker = worker_report(root, repeats=args.repeats)
            server = server_cost(root, base / name, repeats=args.repeats)
            report["workloads"][name] = {
                "iterations": iterations,
                "worker": worker,
                "server": server,
                "projected": {
                    "repeated_unchanged_snapshot": per_minute(
                        worker["repeated_unchanged_snapshot"]
                    ),
                    "bounded_heartbeat": per_minute(worker["bounded_heartbeat"]),
                },
            }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    for name, workload in report["workloads"].items():
        worker = workload["worker"]
        projected = workload["projected"]
        print(
            f"{name:>7}: retained={worker['repeated_unchanged_snapshot']['bytes']:>9} B  "
            f"bounded={worker['bounded_heartbeat']['bytes']:>6} B  "
            f"per minute {projected['repeated_unchanged_snapshot']['payload_bytes_per_minute']:>10.0f} B"
            f" -> {projected['bounded_heartbeat']['payload_bytes_per_minute']:>8.0f} B  "
            f"server full={workload['server']['full_snapshot']['median_seconds'] * 1e3:.2f} ms"
            f" bounded={workload['server']['bounded_heartbeat']['median_seconds'] * 1e3:.2f} ms"
        )


if __name__ == "__main__":
    main()
