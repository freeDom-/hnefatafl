"""Bind parent-observed process completion to a collected memory profile.

The collector is not the workload parent, so it cannot observe the real exit
status. After ``wait``, the owner runs this small finalizer with the actual exit
status, the workload summary and the persisted ownership document. It reconciles
the final telemetry snapshot with the summary and writes completion evidence into
a copy of the profile. No parent-side evidence is fabricated.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import tempfile
import time
from pathlib import Path

from memory_profile_collect import FLOAT_WORKLOAD_FIELDS, WORKLOAD_FIELDS
from wait_for_ready import _read_ownership

PROFILE_MODES = {
    "sustained-10m": "repeated",
    "sustained-30m": "repeated",
    "cancel-restart": "cancel-restart",
    "model-reload": "model-reload",
    "worker-replacement": "worker-replacement",
    "independent-batching": "batching",
    "model-replacement": "model-replacement",
    "client-disposal": "client-disposal",
    "cache-churn": "cache-churn",
}


def _read(path: Path, label: str) -> dict:
    try:
        value = json.loads(path.read_text())
    except OSError as error:
        raise ValueError(f"cannot read {label}: {error}") from None
    except ValueError as error:
        raise ValueError(f"{label} is not valid JSON: {error}") from None
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _counter_ok(field: str, value) -> bool:
    if field in FLOAT_WORKLOAD_FIELDS:
        return (
            not isinstance(value, bool)
            and isinstance(value, (int, float))
            and math.isfinite(float(value))
            and value >= 0
        )
    return type(value) is int and value >= 0


def reconcile(
    profile: dict,
    summary: dict,
    ownership: dict,
    exit_status: int,
    *,
    summary_sha256: str,
) -> tuple[dict, list[str]]:
    failures: list[str] = []
    profile_name = profile.get("profile")
    mode = PROFILE_MODES.get(profile_name)
    if mode is None:
        failures.append("profile has no known workload mode")
    final = profile.get("workload_telemetry", {}).get("final_session")
    if not isinstance(final, dict):
        failures.append("profile has no final workload session")
        final = {}
    expected = profile.get("expected_identity")
    if not isinstance(expected, dict):
        failures.append("profile has no expected identity")
        expected = {}
    identity = profile.get("root_process", {}).get("identity")
    if not isinstance(identity, dict):
        failures.append("profile has no root process identity")
        identity = {}
    if ownership.get("session_id") != expected.get("session_id"):
        failures.append("ownership session does not match the profile")
    if ownership.get("mode") != mode:
        failures.append("ownership mode does not match the profile")
    if ownership.get("model_sha256") != expected.get("model_sha256"):
        failures.append("ownership original model does not match the profile")
    if ownership.get("identity") != identity or ownership.get("pid") != profile.get(
        "root_process", {}
    ).get("pid"):
        failures.append("ownership process identity does not match the profile")
    if summary.get("kind") != "browser-sustained-workload":
        failures.append("workload summary kind is invalid")
    if summary.get("workload_complete") is not True:
        failures.append("workload summary does not report completion")
    if summary.get("cleanup_complete") is not True:
        failures.append("workload summary does not report cleanup completion")
    for field, expected_value in (
        ("session_id", expected.get("session_id")),
        ("workload", expected.get("workload")),
        ("mode", mode),
        ("model_sha256", final.get("model_sha256")),
    ):
        if summary.get(field) != expected_value:
            failures.append(f"workload summary {field} does not match the profile")
    summary_counters = summary.get("counters")
    final_counters = final.get("counters")
    if not isinstance(summary_counters, dict):
        failures.append("workload summary counters are missing")
        summary_counters = {}
    if not isinstance(final_counters, dict):
        failures.append("final telemetry counters are missing")
        final_counters = {}
    for field in WORKLOAD_FIELDS:
        if not _counter_ok(field, final_counters.get(field)):
            failures.append(f"final telemetry counter {field} is invalid")
        if not _counter_ok(field, summary_counters.get(field)):
            failures.append(f"workload summary counter {field} is invalid")
        elif field in final_counters and summary_counters[field] != final_counters[field]:
            failures.append(f"workload summary counter {field} does not match final telemetry")
    if final.get("workload_complete") is not True:
        failures.append("final telemetry does not report completion")
    if final.get("cleanup_complete") is not True:
        failures.append("final telemetry does not report cleanup completion")
    if type(exit_status) is not int or exit_status < 0:
        failures.append("actual process exit status is invalid")
    reported = final.get("workload_exit_status")
    if type(reported) is not int or reported < 0:
        failures.append("reported workload exit status is invalid")
    consistent = type(exit_status) is int and reported == exit_status
    if not consistent:
        failures.append("actual process exit status contradicts the workload summary")
    return (
        {
            "kind": "browser-sustained-workload-completion",
            "schema": 1,
            "actual_process_exit_status": exit_status,
            "reported_exit_status": reported,
            "exit_status_consistent": consistent,
            "pid": ownership.get("pid"),
            "process_identity": ownership.get("identity"),
            "session_id": expected.get("session_id"),
            "workload": expected.get("workload"),
            "profile": profile_name,
            "mode": mode,
            "model_sha256": final.get("model_sha256"),
            "summary_sha256": summary_sha256,
            "summary_reconciled": True,
            "finalized_at_ms": time.time() * 1000.0,
            "source": "parent-wait",
        },
        failures,
    )


def _write(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2)
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--workload-summary", type=Path, required=True)
    parser.add_argument("--ownership-file", type=Path, required=True)
    parser.add_argument("--exit-status", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        profile = _read(args.profile, "memory profile")
        try:
            summary_bytes = args.workload_summary.read_bytes()
        except OSError as error:
            raise ValueError(f"cannot read workload summary: {error}") from None
        summary = _read(args.workload_summary, "workload summary")
        ownership, ownership_error = _read_ownership(args.ownership_file)
        if ownership_error:
            raise ValueError(ownership_error)
        assert ownership is not None
        evidence, failures = reconcile(
            profile,
            summary,
            ownership,
            args.exit_status,
            summary_sha256=hashlib.sha256(summary_bytes).hexdigest(),
        )
        if failures:
            print(json.dumps({"ok": False, "failures": failures}, indent=2))
            return 2
        result = dict(profile)
        result["completion_evidence"] = evidence
        process_observation = result.setdefault("workload_telemetry", {}).setdefault(
            "process_observation", {}
        )
        process_observation["actual_process_exit_status"] = args.exit_status
        process_observation["actual_process_exit_status_source"] = "parent-wait"
        final = result["workload_telemetry"].get("final_session")
        if isinstance(final, dict):
            final["actual_process_exit_status"] = args.exit_status
            final["actual_process_exit_status_source"] = "parent-wait"
        _write(args.output, result)
    except (OSError, ValueError) as error:
        print(json.dumps({"ok": False, "failures": [str(error)]}, indent=2))
        return 2
    print(json.dumps({"ok": True, "output": str(args.output), "exit_status": args.exit_status}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
