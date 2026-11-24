"""Simulated live browser telemetry writer for memory collector tests."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--telemetry", type=Path, required=True)
    parser.add_argument("--progress", type=Path, required=True)
    parser.add_argument("--duration", type=float, default=3.0)
    parser.add_argument("--interval", type=float, default=0.1)
    parser.add_argument("--session-id", default="session-a")
    parser.add_argument("--model-sha256", default="a" * 64)
    parser.add_argument("--workload", default="brandubh-opening")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--stale-first", action="store_true")
    parser.add_argument("--identity-change", action="store_true")
    parser.add_argument("--identity-change-at", type=int, default=8)
    args = parser.parse_args()
    started = time.monotonic()
    completed = 0
    with args.telemetry.open("w", encoding="utf-8") as telemetry:
        if args.stale_first:
            telemetry.write(
                json.dumps(
                    {
                        "timestamp_ms": (time.time() - 60) * 1000.0,
                        "session_id": args.session_id,
                        "model_sha256": args.model_sha256,
                        "workload": args.workload,
                        "workers": args.workers,
                    }
                )
                + "\n"
            )
            telemetry.write("not-json\n")
            telemetry.flush()
        index = 0
        while time.monotonic() - started < args.duration:
            index += 1
            completed += 1
            session = (
                "session-b"
                if args.identity_change and index > args.identity_change_at
                else args.session_id
            )
            record = {
                "timestamp_ms": time.time() * 1000.0,
                "session_id": session,
                "model_sha256": args.model_sha256,
                "workload": args.workload,
                "workers": args.workers,
                "js_heap_used_bytes": 20 * 1024 * 1024 + index * 128,
                "cache_entries": 10 + index,
                "cache_bytes": 4096 + index * 16,
                "wasm_heap_capacity_bytes": 32 * 1024 * 1024,
                "events": {
                    "worker_replacements": 1 + (index // 5),
                    "cancellations": index // 10,
                },
            }
            telemetry.write(json.dumps(record) + "\n")
            telemetry.flush()
            args.progress.write_text(str(completed))
            time.sleep(args.interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
