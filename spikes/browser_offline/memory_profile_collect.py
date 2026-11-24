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
from pathlib import Path

CLEANUP_GRACE_MS = 1000.0
TELEMETRY_FUTURE_TOLERANCE_MS = 100.0


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
        self.records: list[dict] = []
        self.malformed = 0
        self.identity_mismatch = 0
        self.last_error: str | None = None
        self.identity: dict | None = None
        self.last_rejected_timestamp: float | None = None
        self._event_first: dict[str, int] = {}
        self._event_last: dict[str, int] = {}

    def _accept_identity(self, record: dict) -> bool:
        identity = {
            key: record.get(key)
            for key in ("session_id", "model_sha256", "workload")
            if record.get(key) is not None
        }
        if self.identity is None and identity:
            self.identity = identity
        for key, expected in self.expected.items():
            if record.get(key) != expected:
                self.identity_mismatch += 1
                self.last_error = f"telemetry {key} does not match the expected identity"
                self.last_rejected_timestamp = record.get("timestamp_ms")
                return False
        if self.identity is not None:
            for key, value in self.identity.items():
                if key in record and record.get(key) != value:
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
        record = {**record, "timestamp_ms": float(timestamp)}
        if not self._accept_identity(record):
            return
        events = record.get("events")
        if isinstance(events, dict):
            for name, value in events.items():
                if isinstance(name, str) and _non_negative_int(value):
                    self._event_first.setdefault(name, value)
                    self._event_last[name] = value
                else:
                    self.malformed += 1
                    self.last_error = "telemetry events are malformed"
                    return
        self.records.append(record)
        self.records.sort(key=lambda item: item["timestamp_ms"])

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
        candidates = [
            record
            for record in self.records
            if record["timestamp_ms"] <= now_ms + TELEMETRY_FUTURE_TOLERANCE_MS
        ]
        if not candidates:
            if self.last_error:
                return None, f"no usable telemetry ({self.last_error})"
            return None, "no live telemetry has been appended yet"
        record = candidates[-1]
        age = now_ms - record["timestamp_ms"]
        if age > self.stale_after_ms:
            return None, f"telemetry is stale by {age:.0f} ms"
        return record, None

    def observed_events(self) -> dict[str, int]:
        return {
            name: max(0, self._event_last.get(name, 0) - self._event_first.get(name, 0))
            for name in self._event_last
        }

    def diagnostics(self) -> dict:
        return {
            "records": len(self.records),
            "malformed": self.malformed,
            "identity_mismatch": self.identity_mismatch,
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
        "wasm_heap_capacity_bytes",
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
    telemetry = TelemetryStream(
        args.telemetry_file,
        stale_after_ms=args.telemetry_stale_ms,
        expected=expected,
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
        sample = {
            "timestamp_ms": round((now - started) * 1000.0, 3),
            "wall_time_ms": round(wall_ms, 3),
            "process_rss_bytes": sum(rss_values) if rss_values else None,
            "os_processes": len(tree) if tree else None,
            "completed_requests": _read_progress(args.progress_file),
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
    events = telemetry.observed_events()
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
