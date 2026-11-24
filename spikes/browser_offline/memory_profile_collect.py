"""Collect a sustained-memory profile JSON document from a live process tree.

This is the owner-side collection step for ``memory_validation.py``. It samples
real process RSS from the browser process tree at a fixed interval while a
workload advances a completed-request counter. Metrics the harness cannot observe
(JS heap, WASM heap capacity, cache counters) are emitted as ``null`` with an
explicit reason; they are never substituted with another metric.

Linux reads ``/proc`` directly. Windows/macOS use ``psutil`` when installed and
otherwise write an explicit blocked profile. This tool only collects; acceptance
is decided by the validator.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path


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


def _process_tree(pid: int) -> dict[int, dict]:
    """Return pid -> {rss, ppid} for the root process and its descendants."""
    if Path("/proc").is_dir():
        entries = {}
        for name in os.listdir("/proc"):
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
                child_pid for child_pid, info in entries.items() if info.get("ppid") == current
            )
        return selected
    try:
        import psutil
    except ImportError:
        return {}
    try:
        root = psutil.Process(pid)
        processes = [root, *root.children(recursive=True)]
    except Exception:  # noqa: BLE001 - unavailable process tree is reported as null
        return {}
    return {
        process.pid: {"rss": process.memory_info().rss, "ppid": process.ppid()}
        for process in processes
    }


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


def _read_optional_series(path: Path | None) -> list[int | float | None]:
    if path is None:
        return []
    values = []
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return []
    for line in lines:
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            values.append(value.get("value"))
        else:
            values.append(value)
    return values


def collect(args) -> dict:
    root_pid = args.pid
    unavailable = {}
    if args.js_heap_file is None:
        unavailable["js_heap_used_bytes"] = (
            "performance.memory was not exported by the browser workload"
        )
    if args.wasm_heap_file is None:
        unavailable["wasm_heap_capacity_bytes"] = (
            "ORT Web does not expose WASM heap capacity to the harness"
        )
    budgets = {
        "max_process_rss_bytes": args.max_process_rss_bytes,
        "max_js_heap_used_bytes": args.max_js_heap_bytes,
        "max_cache_bytes": args.max_cache_bytes,
        "max_workers": args.max_workers,
    }
    if args.max_wasm_heap_bytes is not None:
        budgets["max_wasm_heap_capacity_bytes"] = args.max_wasm_heap_bytes
    js_series = _read_optional_series(args.js_heap_file)
    wasm_series = _read_optional_series(args.wasm_heap_file)
    cache_series = _read_optional_series(args.cache_bytes_file)
    samples = []
    started = time.monotonic()
    deadline = started + args.duration
    index = 0
    while True:
        tree = _process_tree(root_pid)
        rss_values = [info["rss"] for info in tree.values() if isinstance(info.get("rss"), int)]
        rss = sum(rss_values) if rss_values else None
        sample = {
            "timestamp_ms": round((time.monotonic() - started) * 1000.0, 3),
            "process_rss_bytes": rss,
            "js_heap_used_bytes": js_series[index] if index < len(js_series) else None,
            "wasm_heap_capacity_bytes": (wasm_series[index] if index < len(wasm_series) else None),
            "workers": max(1, len(tree)) if tree else 0,
            "completed_requests": _read_progress(args.progress_file),
        }
        if index < len(cache_series):
            sample["cache_bytes"] = cache_series[index]
        if rss is None:
            sample["rss_unavailable_reason"] = (
                "the requested process or its /proc entry is unavailable"
            )
        samples.append(sample)
        index += 1
        now = time.monotonic()
        if now >= deadline:
            break
        time.sleep(min(args.interval, max(0.0, deadline - now)))
    duration_ms = (time.monotonic() - started) * 1000.0
    events = json.loads(args.events_json) if args.events_json else {}
    report = {
        "kind": "browser-analysis-memory-profile",
        "schema": 2,
        "profile": args.profile,
        "status": "measured",
        "duration_ms": duration_ms,
        "budgets": budgets,
        "features": {"inference_cache": args.cache_bytes_file is not None},
        "warmup_samples": args.warmup_samples,
        "events": events,
        "unavailable_metrics": unavailable,
        "samples": samples,
        "authoritative": False,
    }
    if not samples:
        report["status"] = "blocked"
        report["blocked_reasons"] = ["no samples were collected"]
    elif not any(isinstance(sample.get("process_rss_bytes"), int) for sample in samples):
        report["status"] = "blocked"
        report["blocked_reasons"] = [
            "no process RSS observations were available for the requested process tree "
            "(check --pid, /proc availability or install psutil)"
        ]
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
    parser.add_argument("--duration", type=float, required=True, help="seconds")
    parser.add_argument("--interval", type=float, default=1.0, help="seconds")
    parser.add_argument("--warmup-samples", type=int, default=1)
    parser.add_argument("--progress-file", type=Path)
    parser.add_argument("--js-heap-file", type=Path)
    parser.add_argument("--wasm-heap-file", type=Path)
    parser.add_argument("--cache-bytes-file", type=Path)
    parser.add_argument("--events-json", default="{}")
    parser.add_argument("--max-process-rss-bytes", type=int, default=1024**3)
    parser.add_argument("--max-js-heap-bytes", type=int, default=512 * 1024**2)
    parser.add_argument("--max-cache-bytes", type=int, default=64 * 1024**2)
    parser.add_argument("--max-workers", type=int, default=32)
    parser.add_argument("--max-wasm-heap-bytes", type=int)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.duration <= 0 or args.interval <= 0:
        parser.error("--duration and --interval must be positive")
    report = collect(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"samples": len(report["samples"]), "output": str(args.output)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
