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
    parser.add_argument("--events-stop-at", type=int)
    parser.add_argument("--progress-reset-at", type=int)
    parser.add_argument("--drop-identity-after", type=int)
    parser.add_argument("--workload-failures", type=int, default=0)
    parser.add_argument("--transition-at", type=int)
    parser.add_argument("--transition-back-at", type=int)
    parser.add_argument("--to-model-sha256", default="b" * 64)
    parser.add_argument("--client-disposals", type=int, default=0)
    parser.add_argument("--cache-evictions", type=int, default=0)
    parser.add_argument("--batches", type=int, default=0)
    parser.add_argument("--batch-successful-rounds", type=int, default=0)
    parser.add_argument("--observed-max-batch", type=int, default=0)
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
            event_index = index
            if args.events_stop_at is not None:
                event_index = min(event_index, args.events_stop_at)
            reported_progress = completed
            if args.progress_reset_at is not None and index >= args.progress_reset_at:
                reported_progress = 0
            active_model = args.model_sha256
            if args.transition_at is not None and index >= args.transition_at:
                active_model = args.to_model_sha256
            if args.transition_back_at is not None and index >= args.transition_back_at:
                active_model = args.model_sha256
            transition = None
            model_replacements = 0
            if args.transition_at is not None and index >= args.transition_at:
                model_replacements = 1
            if args.transition_back_at is not None and index >= args.transition_back_at:
                model_replacements = 2
            if args.transition_at is not None and index == args.transition_at:
                transition = {
                    "from": args.model_sha256,
                    "to": args.to_model_sha256,
                    "sequence": 1,
                    "timestamp_ms": time.time() * 1000.0,
                    "recovered": True,
                    "cache_invalidated": True,
                }
                pass
            elif args.transition_back_at is not None and index == args.transition_back_at:
                transition = {
                    "from": args.to_model_sha256,
                    "to": args.model_sha256,
                    "sequence": 2,
                    "timestamp_ms": time.time() * 1000.0,
                    "recovered": True,
                    "cache_invalidated": True,
                }
                pass
            record = {
                "timestamp_ms": time.time() * 1000.0,
                "workers": args.workers,
                "js_heap_used_bytes": 20 * 1024 * 1024 + index * 128,
                "cache_entries": 10 + index,
                "cache_bytes": 4096 + index * 16,
                "wasm_heap_capacity_bytes": 32 * 1024 * 1024,
                "completed_requests": reported_progress,
                "successful_searches": completed,
                "workload_failures": args.workload_failures if index > 5 else 0,
                "neural_searches": completed,
                "completed_simulations": completed * 4,
                "neural_inferences": completed * 4,
                "no_inference_shortcuts": 0,
                "client_disposals": (args.client_disposals if index > 5 else 0),
                "model_replacements": model_replacements,
                "cache_evictions": args.cache_evictions if index > 5 else 0,
                "batches": args.batches if index > 5 else 0,
                "batch_rounds": args.batch_successful_rounds,
                "batch_successful_rounds": args.batch_successful_rounds,
                "observed_max_batch": args.observed_max_batch,
                "cache_invalidation_failures": 0,
                "peak_search_workers": 2,
                "peak_inference_workers": 1,
                "peak_total_workers": 3,
                "workload_complete": True,
                "workload_exit_status": 0,
                "provider_placements": ["wasm"],
                "completion_reasons": {"simulations": completed},
                "events": {
                    "worker_replacements": 1 + (event_index // 5),
                    "cancellations": event_index // 10,
                    "model_replacements": model_replacements,
                    "client_disposals": (args.client_disposals if index > 5 else 0),
                    "cache_evictions": args.cache_evictions if index > 5 else 0,
                    "batches": args.batches if index > 5 else 0,
                    "batch_successful_rounds": args.batch_successful_rounds,
                },
            }
            if transition is not None:
                record["model_transition"] = transition
            drop_identity = (
                args.drop_identity_after is not None and index > args.drop_identity_after
            )
            if not drop_identity:
                record["session_id"] = session
                record["model_sha256"] = active_model
                record["workload"] = args.workload
            telemetry.write(json.dumps(record) + "\n")
            telemetry.flush()
            args.progress.write_text(str(completed))
            time.sleep(args.interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
