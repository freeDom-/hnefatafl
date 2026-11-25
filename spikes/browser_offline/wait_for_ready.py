"""Bounded readiness handshake for sustained-workload owner runs.

Waits for the driver's ready document while checking process liveness. On timeout,
early exit, malformed JSON or identity mismatch it terminates the owned workload
and returns a structured nonzero status instead of waiting indefinitely.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import signal
import sys
import time
from pathlib import Path

from memory_profile_collect import _process_identity


def _read_json(path: Path) -> dict | None:
    try:
        raw = path.read_text()
    except OSError:
        return None
    try:
        value = json.loads(raw)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _same_instance(current: dict | None, identity: dict) -> bool:
    return (
        isinstance(current, dict)
        and current.get("pid") == identity.get("pid")
        and current.get("start_time") == identity.get("start_time")
    )


def _alive(pid: int, identity: dict | None = None) -> bool:
    current = _process_identity(pid)
    if current is None:
        return False
    if identity is not None and not _same_instance(current, identity):
        return False
    return True


def _descendants(pid: int) -> list[tuple[int, dict]]:
    """Snapshot owned descendants before signalling their parent."""
    if not Path("/proc").is_dir():
        try:
            import psutil
        except ImportError:
            return []
        try:
            root = psutil.Process(pid)
            children = root.children(recursive=True)
        except Exception:  # noqa: BLE001 - unavailable descendants are reported
            return []
        owned = []
        for child in children:
            identity = _process_identity(child.pid)
            if identity is not None:
                owned.append((child.pid, identity))
        return owned
    entries = {}
    try:
        names = os.listdir("/proc")
    except OSError:
        return []
    for name in names:
        if not name.isdigit():
            continue
        try:
            stat_text = Path(f"/proc/{name}/stat").read_text()
            tail = stat_text[stat_text.rfind(")") + 2 :].split()
            entries[int(name)] = int(tail[1])
        except (OSError, IndexError, ValueError):
            continue
    selected = []
    pending = [pid]
    seen = set()
    while pending:
        current = pending.pop()
        for child_pid, ppid in entries.items():
            if ppid == current and child_pid not in seen:
                seen.add(child_pid)
                pending.append(child_pid)
                identity = _process_identity(child_pid)
                if identity is not None:
                    selected.append((child_pid, identity))
    return selected


def _signal_verified(pid: int, identity: dict, signum: int) -> bool:
    """Signal only the exact process instance captured by the caller."""
    if not _same_instance(_process_identity(pid), identity):
        return False
    if hasattr(os, "pidfd_open") and hasattr(signal, "pidfd_send_signal"):
        try:
            descriptor = os.pidfd_open(pid)
        except (OSError, ProcessLookupError):
            return False
        try:
            if not _same_instance(_process_identity(pid), identity):
                return False
            signal.pidfd_send_signal(descriptor, signum)
            return True
        except (ProcessLookupError, OSError):
            return False
        finally:
            try:
                os.close(descriptor)
            except OSError:
                pass
    try:
        os.kill(pid, signum)
    except (ProcessLookupError, OSError):
        return False
    return True


def _terminate(pid: int, identity: dict) -> dict:
    """Terminate the verified workload root and snapshot-owned descendants."""
    result = {"terminated": False, "cleanup_failures": []}
    if type(pid) is not int or pid <= 0:
        result["cleanup_failures"].append("refusing to signal a nonpositive pid")
        return result
    if not isinstance(identity, dict) or not _same_instance(_process_identity(pid), identity):
        result["cleanup_failures"].append("workload identity changed before cleanup")
        return result
    descendants = _descendants(pid)
    for child_pid, child_identity in descendants:
        _signal_verified(child_pid, child_identity, signal.SIGTERM)
    _signal_verified(pid, identity, signal.SIGTERM)
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if not _alive(pid, identity) and not any(
            _alive(child_pid, child_identity) for child_pid, child_identity in descendants
        ):
            break
        time.sleep(0.05)
    for child_pid, child_identity in descendants:
        if _alive(child_pid, child_identity):
            _signal_verified(child_pid, child_identity, signal.SIGKILL)
    if _alive(pid, identity):
        _signal_verified(pid, identity, signal.SIGKILL)
    current = _process_identity(pid)
    if current is not None and not _same_instance(current, identity):
        result["cleanup_failures"].append(
            "workload process identity changed during cleanup; replacement was not signalled"
        )
    result["terminated"] = current is None
    if result["terminated"] is False and _same_instance(current, identity):
        result["cleanup_failures"].append("workload did not terminate within the grace period")
    remaining = [
        child_pid for child_pid, child_identity in descendants if _alive(child_pid, child_identity)
    ]
    if remaining:
        result["cleanup_failures"].append(
            f"{len(remaining)} owned descendant process(es) remain alive"
        )
    return result


def _readiness_failure(result: dict, identity: dict, pid: int) -> None:
    cleanup = _terminate(pid, identity)
    result["workload_terminated"] = cleanup["terminated"]
    result["cleanup_failures"] = cleanup["cleanup_failures"]
    print(json.dumps(result, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ready", type=Path)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--session")
    parser.add_argument("--model-sha256")
    parser.add_argument("--mode")
    parser.add_argument(
        "--terminate-only",
        action="store_true",
        help="safely terminate the captured owned process without waiting for readiness",
    )
    args = parser.parse_args()
    if not args.terminate_only and not all((args.session, args.model_sha256, args.mode)):
        parser.error("--session, --model-sha256 and --mode are required")
    if args.pid <= 0:
        parser.error("--pid must be a positive process id")
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout must be finite and positive")
    identity = _process_identity(args.pid)
    if args.terminate_only:
        if identity is None:
            print(
                json.dumps(
                    {"ok": True, "workload_terminated": True, "cleanup_failures": []}, indent=2
                )
            )
            return 0
        cleanup = _terminate(args.pid, identity)
        print(
            json.dumps(
                {
                    "ok": cleanup["terminated"] and not cleanup["cleanup_failures"],
                    "workload_terminated": cleanup["terminated"],
                    "cleanup_failures": cleanup["cleanup_failures"],
                },
                indent=2,
            )
        )
        return 0 if cleanup["terminated"] and not cleanup["cleanup_failures"] else 3
    if identity is None:
        print(
            json.dumps(
                {
                    "ok": False,
                    "failures": [
                        "workload process exited before readiness; identity is unavailable"
                    ],
                    "workload_terminated": False,
                    "cleanup_failures": ["refusing to signal an unverified process"],
                },
                indent=2,
            )
        )
        return 3
    deadline = time.monotonic() + args.timeout
    result: dict = {"ok": False, "failures": []}
    try:
        while True:
            if not _same_instance(_process_identity(args.pid), identity):
                result["failures"].append("workload process identity changed before readiness")
                break
            if not _alive(args.pid, identity):
                result["failures"].append("workload process exited before readiness")
                break
            document = _read_json(args.ready)
            if document is not None:
                if document.get("ready") is not True:
                    result["failures"].append("ready document is not ready=true")
                elif document.get("session") != args.session:
                    result["failures"].append("ready session identity mismatch")
                elif document.get("model_sha256") != args.model_sha256:
                    result["failures"].append("ready model identity mismatch")
                elif document.get("mode") != args.mode:
                    result["failures"].append("ready workload mode mismatch")
                elif not isinstance(document.get("positions"), list) or not document["positions"]:
                    result["failures"].append("ready document has no analysis positions")
                else:
                    result["ok"] = True
                    result["ready"] = document
                    break
            if time.monotonic() >= deadline:
                result["failures"].append(f"workload did not become ready within {args.timeout:g}s")
                break
            time.sleep(0.1)
    except KeyboardInterrupt:
        result["failures"].append("readiness check interrupted")
    if not result["ok"]:
        _readiness_failure(result, identity, args.pid)
        return 3
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
