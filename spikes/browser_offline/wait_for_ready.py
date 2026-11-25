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
import tempfile
import time
from pathlib import Path

from memory_profile_collect import _process_identity

OWNERSHIP_KIND = "sustained-workload-ownership"
OWNERSHIP_SCHEMA = 1


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


def _ownership_document(
    pid: int, identity: dict, session: str, model_sha256: str, mode: str
) -> dict:
    return {
        "kind": OWNERSHIP_KIND,
        "schema": OWNERSHIP_SCHEMA,
        "pid": pid,
        "identity": {
            "pid": identity.get("pid"),
            "start_time": identity.get("start_time"),
            "cmdline_sha256": identity.get("cmdline_sha256"),
        },
        "session_id": session,
        "model_sha256": model_sha256,
        "mode": mode,
        "captured_at_ms": time.time() * 1000.0,
    }


def _read_ownership(path: Path | None) -> tuple[dict | None, str | None]:
    if path is None:
        return None, "ownership file is required for cleanup"
    try:
        value = json.loads(path.read_text())
    except OSError as error:
        return None, f"cannot read ownership file: {error}"
    except ValueError:
        return None, "ownership file is not valid JSON"
    if not isinstance(value, dict):
        return None, "ownership document is not an object"
    if value.get("kind") != OWNERSHIP_KIND or value.get("schema") != OWNERSHIP_SCHEMA:
        return None, "ownership document kind/schema is invalid"
    pid = value.get("pid")
    if type(pid) is not int or pid <= 0:
        return None, "ownership pid is invalid"
    identity = value.get("identity")
    if not isinstance(identity, dict):
        return None, "ownership identity is missing"
    if (
        identity.get("pid") != pid
        or not isinstance(identity.get("start_time"), str)
        or not identity.get("start_time")
        or not isinstance(identity.get("cmdline_sha256"), str)
        or len(identity.get("cmdline_sha256", "")) != 64
        or any(
            character not in "0123456789abcdef" for character in identity.get("cmdline_sha256", "")
        )
    ):
        return None, "ownership identity is malformed"
    for field in ("session_id", "model_sha256", "mode"):
        if not isinstance(value.get(field), str) or not value.get(field):
            return None, f"ownership {field} is invalid"
    if len(value["model_sha256"]) != 64 or any(
        character not in "0123456789abcdef" for character in value["model_sha256"]
    ):
        return None, "ownership model fingerprint is invalid"
    captured = value.get("captured_at_ms")
    if (
        isinstance(captured, bool)
        or not isinstance(captured, (int, float))
        or not math.isfinite(float(captured))
        or captured < 0
    ):
        return None, "ownership capture time is invalid"
    return value, None


def _write_ownership(path: Path, document: dict) -> str | None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(document, handle)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except BaseException:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise
    except OSError as error:
        return f"cannot persist workload ownership: {error}"
    return None


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


def _descendants(pid: int) -> list[tuple[int, dict, int]]:
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
                owned.append((child.pid, identity, child.ppid()))
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
                    selected.append((child_pid, identity, ppid))
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


def _child_relationship_valid(child_pid: int, expected_parent: int) -> bool:
    if not Path("/proc").is_dir():
        try:
            import psutil
        except ImportError:
            return False
        try:
            return psutil.Process(child_pid).ppid() == expected_parent
        except Exception:  # noqa: BLE001 - unavailable relationship is ambiguous
            return False
    try:
        stat_text = Path(f"/proc/{child_pid}/stat").read_text()
        tail = stat_text[stat_text.rfind(")") + 2 :].split()
        return tail[1].isdigit() and int(tail[1]) == expected_parent
    except (OSError, IndexError, ValueError):
        return False


def _terminate(pid: int, identity: dict) -> dict:
    """Terminate only the exact workload instance captured by the caller."""
    result = {"terminated": False, "cleanup_failures": [], "ambiguous_descendants": []}
    if type(pid) is not int or pid <= 0:
        result["cleanup_failures"].append("refusing to signal a nonpositive pid")
        return result
    if not isinstance(identity, dict):
        result["cleanup_failures"].append("refusing to signal without an expected identity")
        return result
    current = _process_identity(pid)
    if current is None:
        # The original instance is already gone. Never adopt a recycled pid.
        result["terminated"] = True
        return result
    if not _same_instance(current, identity):
        result["cleanup_failures"].append(
            "workload identity changed before cleanup; replacement was not signalled"
        )
        return result
    descendants = _descendants(pid)
    for child_pid, child_identity, child_parent in descendants:
        if not _child_relationship_valid(child_pid, child_parent):
            result["ambiguous_descendants"].append(child_pid)
            continue
        _signal_verified(child_pid, child_identity, signal.SIGTERM)
    _signal_verified(pid, identity, signal.SIGTERM)
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if not _alive(pid, identity) and not any(
            _alive(child_pid, child_identity) for child_pid, child_identity, _ in descendants
        ):
            break
        time.sleep(0.05)
    for child_pid, child_identity, child_parent in descendants:
        if not _alive(child_pid, child_identity):
            continue
        if not _child_relationship_valid(child_pid, child_parent):
            result["ambiguous_descendants"].append(child_pid)
            continue
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
    for child_pid, child_identity, _ in descendants:
        child_current = _process_identity(child_pid)
        if child_current is None:
            continue
        if not _same_instance(child_current, child_identity):
            result["cleanup_failures"].append(
                f"descendant {child_pid} identity changed; replacement was not signalled"
            )
        else:
            result["cleanup_failures"].append(f"owned descendant {child_pid} remains alive")
    if result["ambiguous_descendants"]:
        result["cleanup_failures"].append(
            f"descendant relationship could not be revalidated for "
            f"{len(set(result['ambiguous_descendants']))} process(es)"
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
    parser.add_argument("--pid", type=int)
    parser.add_argument("--ownership-file", type=Path)
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
    if not args.terminate_only and (
        args.ready is None
        or args.pid is None
        or not all((args.session, args.model_sha256, args.mode))
    ):
        parser.error("--ready, --pid, --session, --model-sha256 and --mode are required")
    if args.pid is not None and args.pid <= 0:
        parser.error("--pid must be a positive process id")
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout must be finite and positive")
    if args.terminate_only:
        document, error = _read_ownership(args.ownership_file)
        if error:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "workload_terminated": False,
                        "cleanup_failures": [error],
                    },
                    indent=2,
                )
            )
            return 3
        assert document is not None
        if args.pid is not None and args.pid != document["pid"]:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "workload_terminated": False,
                        "cleanup_failures": [
                            "cleanup pid contradicts the persisted ownership identity"
                        ],
                    },
                    indent=2,
                )
            )
            return 3
        for field, supplied in (
            ("session_id", args.session),
            ("model_sha256", args.model_sha256),
            ("mode", args.mode),
        ):
            if supplied is not None and supplied != document[field]:
                print(
                    json.dumps(
                        {
                            "ok": False,
                            "workload_terminated": False,
                            "cleanup_failures": [
                                f"cleanup {field} contradicts the persisted ownership identity"
                            ],
                        },
                        indent=2,
                    )
                )
                return 3
        cleanup = _terminate(document["pid"], document["identity"])
        print(
            json.dumps(
                {
                    "ok": cleanup["terminated"] and not cleanup["cleanup_failures"],
                    "workload_terminated": cleanup["terminated"],
                    "cleanup_failures": cleanup["cleanup_failures"],
                    "ambiguous_descendants": cleanup["ambiguous_descendants"],
                },
                indent=2,
            )
        )
        return 0 if cleanup["terminated"] and not cleanup["cleanup_failures"] else 3
    assert args.pid is not None
    identity = _process_identity(args.pid)
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
    ownership_path = args.ownership_file
    if ownership_path is None and args.ready is not None:
        ownership_path = args.ready.with_name(args.ready.name + ".owner.json")
    if ownership_path is None:
        parser.error("--ownership-file is required to persist cleanup ownership")
    ownership = _ownership_document(args.pid, identity, args.session, args.model_sha256, args.mode)
    ownership_error = _write_ownership(ownership_path, ownership)
    if ownership_error:
        _readiness_failure({"ok": False, "failures": [ownership_error]}, identity, args.pid)
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
