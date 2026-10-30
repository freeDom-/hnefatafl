"""Compare monitoring API cost on identical synthetic saved jobs and workers.

Run baseline/candidate sources in alternating fresh processes without other
benchmarks or tests. No real job, model, worker credential or GPU is used.
"""

import argparse
import hashlib
import json
from pathlib import Path
import platform
import statistics
import sys
import tempfile
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(args.source.resolve()))
    sys.path.insert(1, str(Path(__file__).resolve().parents[1] / "tests"))
    from auth_support import AuthenticatedClient
    from hnefatafl.server import create_app

    with tempfile.TemporaryDirectory(prefix="tafl-monitoring-bench-") as temporary:
        with AuthenticatedClient(create_app(temporary)) as client:
            store = client.app.state.store
            history = [
                {
                    "iteration": index,
                    "policy_loss": 0.2,
                    "performance": {"simulations_per_second": 1500, "self_play_wall_seconds": 20},
                    "self_play": [{"plies": 64, "simulations": 8192}] * 8,
                }
                for index in range(120)
            ]
            for index in range(80):
                store.save_job(
                    {
                        "id": f"{index:032x}",
                        "created_at": "2026-10-30T10:00:00Z",
                        "kind": "train",
                        "execution": "remote",
                        "status": "completed",
                        "config": {
                            "ruleset": "brandubh",
                            "model_name": "Benchmark job",
                            "iterations": 120,
                        },
                        "progress": {
                            "iteration": 120,
                            "history": history,
                            "metrics": {"policy_loss": 0.2},
                        },
                        "result": {"history": history, "status": "completed"},
                        "logs": "Worker progress\n" * 500,
                    }
                )
            for index in range(4):
                store.create_worker(
                    {
                        "id": f"{index + 1000:032x}",
                        "name": f"Worker {index}",
                        "capacity": 1,
                        "created_at": "2026-10-30T10:00:00Z",
                        "capabilities": {},
                        "last_seen": None,
                        "revoked_at": None,
                    },
                    f"synthetic-token-hash-{index}",
                )
            results = {}
            for path in ("/api/training/jobs?summary=true", "/api/training/workers"):
                samples, sizes, fingerprints = [], [], []
                for repetition in range(6):
                    start = time.perf_counter()
                    response = client.get(path)
                    elapsed = time.perf_counter() - start
                    assert response.status_code == 200, response.text[:100]
                    # Hash only shared navigator/worker meanings, outside timing.
                    if "/jobs" in path:
                        visible = [
                            {
                                "id": job["id"],
                                "status": job["status"],
                                "config": job["config"],
                                "iteration": job["progress"]["iteration"],
                                "policy_loss": job["progress"]["metrics"]["policy_loss"],
                            }
                            for job in response.json()
                        ]
                    else:
                        visible = [
                            {key: row[key] for key in ("id", "name", "status", "active_jobs")}
                            for row in response.json()
                        ]
                    if repetition:
                        samples.append(elapsed)
                        sizes.append(len(response.content))
                        fingerprints.append(
                            hashlib.sha256(json.dumps(visible, sort_keys=True).encode()).hexdigest()
                        )
                results[path] = {
                    "median_seconds": statistics.median(samples),
                    "seconds": samples,
                    "response_bytes": sizes,
                    "work_sha256": fingerprints,
                }
            payload = {
                "jobs": 80,
                "history_points_per_job": 120,
                "workers": 4,
                "warmup_requests": 1,
                "measured_requests": 5,
                "environment": {"platform": platform.platform(), "python": sys.version},
                "sources": {
                    str(path.relative_to(args.source)): hashlib.sha256(
                        path.read_bytes()
                    ).hexdigest()
                    for path in sorted((args.source / "hnefatafl").glob("*.py"))
                },
                "results": results,
            }
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(payload, indent=2) + "\n")
            print(
                json.dumps(
                    {
                        path: {
                            "median_seconds": row["median_seconds"],
                            "bytes": row["response_bytes"][0],
                        }
                        for path, row in results.items()
                    }
                ),
                flush=True,
            )


if __name__ == "__main__":
    main()
