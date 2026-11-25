"""Bounded readiness handshake for sustained-workload owner runs.

Waits for the driver's ready document while checking process liveness. On timeout,
early exit, malformed JSON or identity mismatch it terminates the owned workload
and returns a structured nonzero status instead of waiting indefinitely.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from pathlib import Path


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


def _alive(pid: int) -> bool:
    stat = Path(f"/proc/{pid}/stat")
    if stat.is_file():
        try:
            text = stat.read_text()
            fields = text[text.rfind(")") + 2 :].split()
            if fields and fields[0] == "Z":
                return False
        except OSError:
            pass
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _terminate(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, OSError):
        return
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if not _alive(pid):
            return
        time.sleep(0.05)
    try:
        os.kill(pid, signal.SIGKILL)
    except (ProcessLookupError, OSError):
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ready", type=Path, required=True)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--session", required=True)
    parser.add_argument("--model-sha256", required=True)
    parser.add_argument("--mode", required=True)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    deadline = time.monotonic() + args.timeout
    result: dict = {"ok": False, "failures": []}
    while True:
        if not _alive(args.pid):
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
            result["failures"].append(
                f"workload did not become ready within {args.timeout:g}s"
            )
            break
        time.sleep(0.1)
    if not result["ok"]:
        _terminate(args.pid)
        result["workload_terminated"] = not _alive(args.pid)
        print(json.dumps(result, indent=2))
        return 3
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
