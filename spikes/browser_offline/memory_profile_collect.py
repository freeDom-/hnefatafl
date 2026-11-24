"""Collect a sustained-memory profile from a live browser process tree.

This is the owner-side collection step for ``memory_validation.py``.

Measurement windows are explicit: ``--warmup-duration`` is completed first and
excluded, then ``--duration`` seconds of acceptance-window samples are collected.
Live browser telemetry is consumed incrementally from a JSONL file written by the
workload; values are associated by Unix-epoch timestamp and session/model/workload
identity rather than array position. Stale, missing, malformed or identity-
mismatched telemetry leaves the affected metric ``null`` with an explanatory
reason.

OS process RSS is sampled from the process tree root. The root's start time and
command line are checked on every sample to detect PID reuse or replacement.
Browser worker counts, cache counters, JS heap and WASM capacity come from actual
runtime telemetry, not from OS process counts.

Telemetry record format (one JSON object per line, appended live)::

    {"timestamp_ms": 1700000000123, "session_id": "...", "model_sha256": "...",
     "workload": "brandubh-opening", "workers": 4, "js_heap_used_bytes": 123,
     "cache_entries": 10, "cache_bytes": 4096, "wasm_heap_capacity_bytes": 65536,
     "events": {"worker_replacements": 1, "cancellations": 0, "reloads": 0,
                "batches": 0}}

``events`` are cumulative counters; the collector reports observed deltas.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import time
from collections import deque
from pathlib import Path

CLEANUP_GRACE_MS = 1000.0
TELEMETRY_FUTURE_TOLERANCE_MS = 100.0
TELEMETRY_HISTORY_LIMIT = 4096
IDENTITY_FIELDS = ("session_id", "model_sha256", "workload")
WORKLOAD_FIELDS = (
    "successful_searches",
    "workload_failures",
    "neural_searches",
    "completed_simulations",
    "neural_inferences",
    "no_inference_shortcuts",
)


def _finite_number(value) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
    )


def _non_negative_int(value) -> bool:
    return type(value) is int and value >= 0


def _read_proc(pid: int) -> dict | None:
    try:
        text = Path(f"/proc/{pid}/status").read_text()
    except OSError:
        return None
    rss = None
    ppid = None
    for line in text.splitlines():
        if line.startswith("VmRSS:"):
            try:
                rss = int(line.split()[1]) * 1024
            except (IndexError, ValueError):
                rss = None
        elif line.startswith("PPid:"):
            try:
                ppid = int(line.split()[1])
            except (IndexError, ValueError):
                ppid = None
    return {"rss": rss, "ppid": ppid}


def _process_identity(pid: int) -> dict | None:
    """Return a PID-reuse-resistant identity for the root process."""
    if Path("/proc").is_dir():
        try:
            stat_text = Path(f"/proc/{pid}/stat").read_text()
        except OSError:
            return None
        try:
            tail = stat_text[stat_text.rfind(")") + 2 :].split()
            start_time = tail[19]
            cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
        except (IndexError, OSError):
            return None
        return {
            "pid": pid,
            "start_time": start_time,
            "cmdline_sha256": hashlib.sha256(cmdline).hexdigest(),
        }
    try:
        import psutil
    except ImportError:
        return None
    try:
        process = psutil.Process(pid)
        return {
            "pid": pid,
            "start_time": str(process.create_time()),
            "cmdline_sha256": hashlib.sha256(
                "\0".join(process.cmdline()).encode("utf-8", "replace")
            ).hexdigest(),
        }
    except Exception:  # noqa: BLE001 - unavailable identity is reported by the caller
        return None


def _process_tree(pid: int) -> tuple[dict[int, dict], str | None]:
    """Return pid -> {rss, ppid} for the root and descendants."""
    if Path("/proc").is_dir():
        entries = {}
        try:
            names = os.listdir("/proc")
        except OSError as error:
            return {}, f"cannot list /proc: {error}"
        for name in names:
            if name.isdigit():
                info = _read_proc(int(name))
                if info is not None:
                    entries[int(name)] = info
        selected = {}
        pending = {pid}
        while pending:
            current = pending.pop()
            if current in selected or current not in entries:
                continue
            selected[current] = entries[current]
            pending.update(
                child_pid
                for child_pid, info in entries.items()
                if info.get("ppid") == current
            )
        return selected, None
    try:
        import psutil
    except ImportError:
        return {}, "psutil is required for process sampling on this platform"
    try:
        root = psutil.Process(pid)
        processes = [root, *root.children(recursive=True)]
        return {
            process.pid: {"rss": process.memory_info().rss, "ppid": process.ppid()}
            for process in processes
        }, None
    except Exception as error:  # noqa: BLE001 - access errors are reported, not raised
        return {}, f"psutil process access failed: {error}"


def _read_progress(path: Path | None) -> int:
    if path is None:
        return 0
    try:
        raw = path.read_text().strip()
    except OSError:
        return 0
    if not raw:
        return 0
    try:
        if raw.startswith("{"):
            value = json.loads(raw).get("completed_requests", 0)
        else:
            value = int(raw.splitlines()[-1])
    except (ValueError, TypeError, AttributeError):
        return 0
    return value if type(value) is int and value >= 0 else 0


class TelemetryStream:
    """Incrementally consume live JSONL telemetry and associate by timestamp."""

    def __init__(
        self,
        path: Path | None,
        *,
        stale_after_ms: float,
        expected: dict | None = None,
    ):
        self.path = path
        self.stale_after_ms = stale_after_ms
        self.expected = {
            key: value for key, value in (expected or {}).items() if value is not None
        }
        self.offset = 0
        self.remainder = ""
        # Bounded history is sufficient for timestamp alignment and diagnostics.
        self.records: deque[dict] = deque(maxlen=TELEMETRY_HISTORY_LIMIT)
        self.malformed = 0
        self.identity_mismatch = 0
        self.last_error: str | None = None
        self.identity: dict | None = None
        self.last_rejected_timestamp: float | None = None
        self.progress_resets = 0
        self.last_progress: int | None = None
        self.acceptance_start_ms: float | None = None
        self.acceptance_end_ms: float | None = None
        self.window_event_observed: dict[str, int] = {}
        self.window_event_last: dict[str, int] = {}
        self.event_resets = 0

    def set_acceptance_window(self, start_ms: float, end_ms: float) -> None:
        self.acceptance_start_ms = float(start_ms)
        self.acceptance_end_ms = float(end_ms)

    def _update_event_window(self, record: dict) -> None:
        if self.acceptance_start_ms is None or self.acceptance_end_ms is None:
            return
        timestamp = record["timestamp_ms"]
        if not self.acceptance_start_ms <= timestamp <= self.acceptance_end_ms:
            return
        events = record.get("events")
        if not isinstance(events, dict):
            return
        for name, value in events.items():
            if not _non_negative_int(value):
                continue
            if name not in self.window_event_last:
                self.window_event_observed[name] = 0
                self.window_event_last[name] = value
                continue
            if value < self.window_event_last[name]:
                # A counter reset is not an increment: keep prior observed events
                # and restart from the new value without counting the drop.
                self.event_resets += 1
                self.window_event_last[name] = value
                continue
            self.window_event_observed[name] += value - self.window_event_last[name]
            self.window_event_last[name] = value

    def _accept_identity(self, record: dict) -> bool:
        missing = [field for field in IDENTITY_FIELDS if record.get(field) is None]
        if missing:
            self.identity_mismatch += 1
            self.last_error = (
                "telemetry is missing established identity fields: " + ", ".join(missing)
            )
            self.last_rejected_timestamp = record.get("timestamp_ms")
            return False
        identity = {field: record.get(field) for field in IDENTITY_FIELDS}
        for key, expected in self.expected.items():
            if record.get(key) != expected:
                self.identity_mismatch += 1
                self.last_error = f"telemetry {key} does not match the expected identity"
                self.last_rejected_timestamp = record.get("timestamp_ms")
                return False
        if self.identity is None:
            self.identity = identity
        else:
            for key, value in self.identity.items():
                if record.get(key) != value:
                    self.identity_mismatch += 1
                    self.last_error = f"telemetry {key} changed during collection"
                    self.last_rejected_timestamp = record.get("timestamp_ms")
                    return False
        return True

    def _record(self, record: dict) -> None:
        timestamp = record.get("timestamp_ms")
        if not _finite_number(timestamp) or timestamp < 0:
            self.malformed += 1
            self.last_error = "telemetry timestamp_ms is invalid"
            return
        timestamp = float(timestamp)
        if self.records and timestamp < self.records[-1]["timestamp_ms"]:
            # Do not sort the complete history: reject out-of-order live records.
            self.malformed += 1
            self.last_error = "telemetry timestamps are not monotonic"
            return
        record = {**record, "timestamp_ms": timestamp}
        if not self._accept_identity(record):
            return
        events = record.get("events")
        if events is not None:
            if not isinstance(events, dict) or any(
                not isinstance(name, str) or not _non_negative_int(value)
                for name, value in events.items()
            ):
                self.malformed += 1
                self.last_error = "telemetry events are malformed"
                return
        progress = record.get("completed_requests")
        if progress is not None and not _non_negative_int(progress):
            self.malformed += 1
            self.last_error = "telemetry completed_requests is malformed"
            return
        if _non_negative_int(progress):
            if self.last_progress is not None and progress < self.last_progress:
                self.progress_resets += 1
                self.last_error = "telemetry completed_requests counter reset"
            self.last_progress = progress
        for field in WORKLOAD_FIELDS:
            if field in record and not _non_negative_int(record[field]):
                self.malformed += 1
                self.last_error = f"telemetry {field} is malformed"
                return
        self.records.append(record)
        if (
            self.last_rejected_timestamp is not None
            and timestamp >= self.last_rejected_timestamp
        ):
            self.last_rejected_timestamp = None
        self._update_event_window(record)

    def poll(self) -> None:
        if self.path is None:
            return
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                handle.seek(self.offset)
                chunk = handle.read()
                self.offset = handle.tell()
        except OSError as error:
            self.last_error = f"cannot read telemetry: {error}"
            return
        self.remainder += chunk
        while "\n" in self.remainder:
            line, self.remainder = self.remainder.split("\n", 1)
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except ValueError:
                self.malformed += 1
                self.last_error = "telemetry line is not valid JSON"
                continue
            if not isinstance(record, dict):
                self.malformed += 1
                self.last_error = "telemetry line is not an object"
                continue
            self._record(record)

    def latest(self, now_ms: float) -> tuple[dict | None, str | None]:
        self.poll()
        if self.last_rejected_timestamp is not None and (
            not self.records
            or self.last_rejected_timestamp > self.records[-1]["timestamp_ms"]
        ):
            return None, f"telemetry identity mismatch ({self.last_error})"
        record = None
        for candidate in reversed(self.records):
            if candidate["timestamp_ms"] <= now_ms + TELEMETRY_FUTURE_TOLERANCE_MS:
                record = candidate
                break
        if record is None:
            if self.last_error:
                return None, f"no usable telemetry ({self.last_error})"
            return None, "no live telemetry has been appended yet"
        age = now_ms - record["timestamp_ms"]
        if age > self.stale_after_ms:
            return None, f"telemetry is stale by {age:.0f} ms"
        return record, None

    def events_in_window(self, start_ms: float, end_ms: float) -> dict[str, int]:
        """Return acceptance-window event deltas independent of history eviction."""
        if self.acceptance_start_ms != start_ms or self.acceptance_end_ms != end_ms:
            self.set_acceptance_window(start_ms, end_ms)
        return dict(self.window_event_observed)

    def diagnostics(self) -> dict:
        return {
            "records": len(self.records),
            "history_limit": TELEMETRY_HISTORY_LIMIT,
            "malformed": self.malformed,
            "identity_mismatch": self.identity_mismatch,
            "progress_resets": self.progress_resets,
            "event_resets": self.event_resets,
            "last_error": self.last_error,
            "identity": self.identity,
        }


def _telemetry_sample(record: dict | None) -> dict:
    if record is None:
        return {}
    sample = {}
    for field in (
        "workers",
        "js_heap_used_bytes",
        "cache_entries",
        "cache_bytes",
        "cache_evictions",
        "wasm_heap_capacity_bytes",
        *WORKLOAD_FIELDS,
    ):
        if field not in record:
            continue
        value = record[field]
        if _non_negative_int(value):
            sample[field] = value
    return sample


def collect(args) -> dict:
    expected = {
        "session_id": args.telemetry_session_id,
        "model_sha256": args.telemetry_model_sha256,
        "workload": args.telemetry_workload,
    }
    collection_wall_start = time.time() * 1000.0
    telemetry = TelemetryStream(
        args.telemetry_file,
        stale_after_ms=args.telemetry_stale_ms,
        expected=expected,
    )
    telemetry.set_acceptance_window(
        collection_wall_start + args.warmup_duration * 1000.0,
        collection_wall_start
        + (args.warmup_duration + args.duration) * 1000.0,
    )
    budgets = {
        "max_process_rss_bytes": args.max_process_rss_bytes,
        "max_js_heap_used_bytes": args.max_js_heap_bytes,
        "max_cache_bytes": args.max_cache_bytes,
        "max_workers": args.max_workers,
    }
    if args.max_wasm_heap_bytes is not None:
        budgets["max_wasm_heap_capacity_bytes"] = args.max_wasm_heap_bytes
    root_identity = _process_identity(args.pid)
    blocked_reasons = []
    if root_identity is None:
        blocked_reasons.append("the requested root process identity is unavailable")
    samples = []
    started = time.monotonic()
    warmup_seconds = args.warmup_duration
    acceptance_seconds = args.duration
    acceptance_end = started + warmup_seconds + acceptance_seconds
    sample_errors: list[str] = []
    previous_progress: int | None = None
    progress_reset_detected = False
    progress_mismatches = 0
    telemetry_progress_samples = 0
    progress_file_samples = 0
    workload_telemetry: dict = {}
    provider_placements: set[str] = set()
    completion_reasons: dict[str, int] = {}
    while True:
        now = time.monotonic()
        wall_ms = time.time() * 1000.0
        tree, tree_error = _process_tree(args.pid)
        rss_values = [
            info["rss"] for info in tree.values() if isinstance(info.get("rss"), int)
        ]
        identity = _process_identity(args.pid)
        identity_problem = None
        if root_identity is not None and identity is None:
            identity_problem = "the root process disappeared during collection"
        elif root_identity is not None and identity != root_identity:
            identity_problem = "the root process identity changed (PID reuse or replacement)"
        if identity_problem:
            blocked_reasons.append(identity_problem)
        record, telemetry_reason = telemetry.latest(wall_ms)
        file_progress = _read_progress(args.progress_file)
        telemetry_progress = (record or {}).get("completed_requests")
        if _non_negative_int(telemetry_progress):
            progress = telemetry_progress
            progress_source = "telemetry"
            telemetry_progress_samples += 1
            if args.progress_file is not None and file_progress != telemetry_progress:
                progress_mismatches += 1
        else:
            progress = file_progress
            progress_source = "progress-file"
            progress_file_samples += 1
        if previous_progress is not None and progress < previous_progress:
            progress_reset_detected = True
        previous_progress = progress
        sample = {
            "timestamp_ms": round((now - started) * 1000.0, 3),
            "wall_time_ms": round(wall_ms, 3),
            "process_rss_bytes": sum(rss_values) if rss_values else None,
            "os_processes": len(tree) if tree else None,
            "completed_requests": progress,
            "completed_requests_source": progress_source,
        }
        if not rss_values:
            sample["rss_unavailable_reason"] = tree_error or (
                "the root process tree has no RSS observation"
            )
        if "workers" not in _telemetry_sample(record or {}):
            # Worker count is browser-worker telemetry, never the OS process count.
            sample["workers"] = None
            sample["workers_unavailable_reason"] = (
                telemetry_reason or "telemetry did not report browser workers"
            )
        sample.update(_telemetry_sample(record))
        if record is not None:
            for field in WORKLOAD_FIELDS:
                if _non_negative_int(record.get(field)):
                    workload_telemetry[field] = max(
                        workload_telemetry.get(field, 0), record[field]
                    )
            placements = record.get("provider_placements")
            if isinstance(placements, list):
                provider_placements.update(
                    item for item in placements if isinstance(item, str) and item
                )
            reasons = record.get("completion_reasons")
            if isinstance(reasons, dict):
                for name, value in reasons.items():
                    if isinstance(name, str) and _non_negative_int(value):
                        completion_reasons[name] = max(
                            completion_reasons.get(name, 0), value
                        )
            sample["telemetry"] = {
                key: record.get(key)
                for key in ("timestamp_ms", "session_id", "model_sha256", "workload")
                if record.get(key) is not None
            }
            sample["telemetry_lag_ms"] = round(wall_ms - record["timestamp_ms"], 3)
        elif telemetry_reason:
            sample["telemetry_unavailable_reason"] = telemetry_reason
        samples.append(sample)
        if identity_problem:
            break
        now = time.monotonic()
        if now >= acceptance_end:
            break
        time.sleep(min(args.interval, max(0.0, acceptance_end - now)))
    total_duration_ms = (time.monotonic() - started) * 1000.0
    if not samples:
        blocked_reasons.append("no samples were collected")
    elif not any(isinstance(sample.get("process_rss_bytes"), int) for sample in samples):
        blocked_reasons.append(
            "no process RSS observations were available for the requested process tree "
            "(check --pid, /proc availability or install psutil)"
        )
    for error in sample_errors:
        blocked_reasons.append(error)
    unavailable = {}
    if not any(
        isinstance(sample.get("js_heap_used_bytes"), int) for sample in samples
    ):
        unavailable["js_heap_used_bytes"] = (
            "browser telemetry did not report performance.memory heap usage"
        )
    if not any(isinstance(sample.get("workers"), int) for sample in samples):
        unavailable["workers"] = "browser telemetry did not report live worker counts"
    if not any(
        isinstance(sample.get("wasm_heap_capacity_bytes"), int) for sample in samples
    ):
        unavailable["wasm_heap_capacity_bytes"] = (
            "the pinned ORT Web runtime does not expose WASM heap capacity"
        )
    if not any(isinstance(sample.get("cache_bytes"), int) for sample in samples):
        unavailable["cache_bytes"] = "inference-cache telemetry was not reported"
    if telemetry.identity_mismatch or telemetry.malformed:
        interrupted = (
            "live telemetry was interrupted by identity-mismatched or malformed records"
        )
        for metric in (
            "js_heap_used_bytes",
            "cache_bytes",
            "wasm_heap_capacity_bytes",
            "workers",
        ):
            unavailable.setdefault(metric, interrupted)
    if telemetry.progress_resets or progress_reset_detected:
        blocked_reasons.append(
            "the completed-request counter reset during collection; acceptance is blocked"
        )
        unavailable.setdefault(
            "workers", "progress counter reset invalidates workload-progress evidence"
        )
    if telemetry.event_resets:
        blocked_reasons.append(
            "a lifecycle-event counter reset during the acceptance window; event "
            "evidence is incomplete"
        )
    acceptance_start_wall = collection_wall_start + warmup_seconds * 1000.0
    acceptance_end_wall = acceptance_start_wall + acceptance_seconds * 1000.0
    events = telemetry.events_in_window(acceptance_start_wall, acceptance_end_wall)
    measured_samples = [
        sample for sample in samples if sample["timestamp_ms"] >= warmup_seconds * 1000.0
    ]
    measured_telemetry = sum(
        1
        for sample in measured_samples
        if sample.get("completed_requests_source") == "telemetry"
    )
    progress_consistency = {
        "verified": (
            progress_mismatches == 0
            and bool(measured_samples)
            and measured_telemetry == len(measured_samples)
        ),
        "file_mismatches": progress_mismatches,
        "telemetry_samples": telemetry_progress_samples,
        "progress_file_samples": progress_file_samples,
        "measured_telemetry_samples": measured_telemetry,
        "measured_samples": len(measured_samples),
    }
    report = {
        "kind": "browser-analysis-memory-profile",
        "schema": 3,
        "profile": args.profile,
        "status": "blocked" if blocked_reasons else "measured",
        "warmup_ms": warmup_seconds * 1000.0,
        "duration_ms": acceptance_seconds * 1000.0,
        "total_duration_ms": total_duration_ms,
        "budgets": budgets,
        "features": {"inference_cache": args.inference_cache},
        "events": events,
        "events_source": "telemetry",
        "workload_telemetry": {
            **workload_telemetry,
            "provider_placements": sorted(provider_placements),
            "completion_reasons": completion_reasons,
        },
        "progress_consistency": progress_consistency,
        "telemetry": telemetry.diagnostics(),
        "unavailable_metrics": unavailable,
        "root_process": {
            "pid": args.pid,
            "identity": root_identity,
        },
        "samples": samples,
        "authoritative": False,
    }
    if blocked_reasons:
        report["blocked_reasons"] = blocked_reasons
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int, required=True, help="root browser process id")
    parser.add_argument(
        "--profile",
        default="sustained-10m",
        choices=(
            "sustained-10m",
            "sustained-30m",
            "cancel-restart",
            "model-reload",
            "worker-replacement",
            "independent-batching",
        ),
    )
    parser.add_argument(
        "--warmup-duration",
        type=float,
        default=60.0,
        help="seconds completed before the acceptance window starts (excluded)",
    )
    parser.add_argument(
        "--duration",
        type=float,
        required=True,
        help="required post-warmup acceptance window in seconds",
    )
    parser.add_argument("--interval", type=float, default=1.0, help="seconds")
    parser.add_argument("--progress-file", type=Path)
    parser.add_argument(
        "--telemetry-file",
        type=Path,
        help="live JSONL telemetry appended by the browser workload",
    )
    parser.add_argument("--telemetry-session-id")
    parser.add_argument("--telemetry-model-sha256")
    parser.add_argument("--telemetry-workload")
    parser.add_argument("--telemetry-stale-ms", type=float, default=5000.0)
    parser.add_argument(
        "--inference-cache",
        action="store_true",
        help="declare that the inference cache is enabled for this profile",
    )
    parser.add_argument("--events-json", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--max-process-rss-bytes", type=int, default=1024**3)
    parser.add_argument("--max-js-heap-bytes", type=int, default=512 * 1024**2)
    parser.add_argument("--max-cache-bytes", type=int, default=64 * 1024**2)
    parser.add_argument("--max-workers", type=int, default=32)
    parser.add_argument("--max-wasm-heap-bytes", type=int)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.duration <= 0 or args.interval <= 0 or args.warmup_duration < 0:
        parser.error("--duration and --interval must be positive; warmup cannot be negative")
    if args.telemetry_stale_ms <= 0:
        parser.error("--telemetry-stale-ms must be positive")
    if args.events_json is not None:
        parser.error(
            "--events-json is no longer accepted: lifecycle events must come from live telemetry"
        )
    report = collect(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                "samples": len(report["samples"]),
                "status": report["status"],
                "events": report["events"],
                "telemetry": report["telemetry"],
                "output": str(args.output),
            }
        )
    )
    return 0 if report["status"] == "measured" else 2


if __name__ == "__main__":
    sys.exit(main())
