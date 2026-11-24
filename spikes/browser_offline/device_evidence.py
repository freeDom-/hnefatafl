"""Validate physical browser/WebGPU evidence records.

A successful WebGPU-requested session is not GPU-only execution unless placement
evidence proves it. Unavailable devices are recorded as blocked, never as passes.

Acceptance is derived for every mandatory platform/provider combination:

* structural validity - each measured record is complete and internally coherent;
* measurement acceptance - every mandatory combination is measured and its
  requested provider is honoured (an explicit CPU fallback is a valid measurement
  but does not satisfy a WebGPU combination);
* verified-GPU acceptance - every mandatory WebGPU combination carries independent
  per-operator placement evidence covering a declared operator inventory.

The pinned ORT Web runtime exposes no supported per-operator placement diagnostic,
so verified-GPU acceptance remains blocked even for a complete synthetic matrix.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

SCHEMA = 2
PLATFORMS = (
    "windows-chrome",
    "windows-edge",
    "linux-chromium",
    "linux-firefox",
    "android-chrome",
    "safari",
    "other",
)
REQUIRED_MATRIX = {
    ("windows-chrome", "wasm"),
    ("windows-chrome", "webgpu"),
    ("windows-edge", "wasm"),
    ("windows-edge", "webgpu"),
    ("linux-chromium", "wasm"),
    ("linux-firefox", "wasm"),
    ("android-chrome", "wasm"),
    ("android-chrome", "webgpu"),
    ("safari", "wasm"),
    ("safari", "webgpu"),
}
PLACEMENTS = (
    "wasm",
    "cpu-fallback",
    "webgpu-session-unverified",
    "webgpu-mixed",
    "webgpu-session-verified",
)
WEBGPU_SESSION_PLACEMENTS = ("webgpu-session-unverified", "webgpu-mixed", "webgpu-session-verified")
PROVIDERS = ("wasm", "webgpu")


def _hex64(value):
    return (
        isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)
    )


def _positive_finite(value):
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
        and value > 0
    )


def _positive_int(value):
    return type(value) is int and value > 0


def _identifier(value) -> bool:
    return isinstance(value, str) and bool(value)


def _object(value, label, failures):
    if isinstance(value, dict):
        return value
    failures.append(f"{label} must be an object")
    return {}


def _gpu_evidence(record: dict, index: int) -> tuple[bool, list[str]]:
    failures: list[str] = []
    evidence = _object(record.get("gpu_evidence"), f"record {index} gpu_evidence", failures)
    if not evidence:
        return False, failures
    if evidence.get("evidence_source") != "ort-web-per-operator-v1":
        failures.append(f"record {index} GPU evidence source is unsupported")
    if evidence.get("gpu_execution_verified") is not True:
        failures.append(f"record {index} GPU evidence is not verified")
    if evidence.get("all_operators_covered") is not True:
        failures.append(f"record {index} GPU evidence does not claim full operator coverage")
    session = _object(evidence.get("session"), f"record {index} GPU session", failures)
    for field in ("model_sha256", "onnx_sha256"):
        if session.get(field) != record.get(field):
            failures.append(f"record {index} GPU session {field} does not match the record")
    for field in ("runtime", "workload", "architecture"):
        if session.get(field) != record.get(field):
            failures.append(f"record {index} GPU session {field} does not match the record")
    if session.get("provider_requested") != "webgpu":
        failures.append(f"record {index} GPU session did not request WebGPU")
    inventory = evidence.get("operator_inventory")
    placements = evidence.get("per_operator_placement")
    if not isinstance(inventory, list) or not inventory:
        failures.append(f"record {index} GPU evidence lacks an independent operator inventory")
        inventory = []
    elif any(not _identifier(name) for name in inventory):
        failures.append(f"record {index} operator inventory contains malformed identifiers")
        inventory = [name for name in inventory if _identifier(name)]
    if len(set(inventory)) != len(inventory):
        failures.append(f"record {index} operator inventory contains duplicates")
    if not isinstance(placements, list) or not placements:
        failures.append(f"record {index} GPU evidence lacks per-operator placement")
        placements = []
    observed = []
    for entry in placements:
        if not isinstance(entry, dict):
            failures.append(f"record {index} per-operator placement is malformed")
            continue
        if entry.get("provider") != "webgpu":
            failures.append(f"record {index} per-operator placement is not WebGPU")
        if not _identifier(entry.get("operator")):
            failures.append(f"record {index} per-operator placement has no operator name")
            continue
        observed.append(entry["operator"])
    if len(set(observed)) != len(observed):
        failures.append(f"record {index} per-operator placement contains duplicates")
    if inventory and set(observed) != set(inventory):
        failures.append(f"record {index} per-operator placement does not cover the inventory")
    if inventory and len(observed) != len(inventory):
        failures.append(f"record {index} per-operator placement count does not match inventory")
    return (not failures and bool(inventory)), failures


def _record_failures(record: dict, index: int) -> tuple[list[str], dict]:
    failures: list[str] = []
    gate = {
        "measurement_ok": False,
        "webgpu_session": False,
        "verified_gpu": False,
    }
    platform = record.get("platform")
    if platform not in PLATFORMS:
        failures.append(f"record {index} has an unknown platform")
    if record.get("status") not in ("blocked", "measured"):
        failures.append(f"record {index} has an invalid status")
        return failures, gate
    if record.get("status") == "blocked":
        if not _identifier(record.get("blocked_reason")):
            failures.append(f"blocked record {index} needs a reason")
        if record.get("provider_requested") not in PROVIDERS:
            failures.append(f"blocked record {index} has an invalid requested provider")
        return failures, gate
    for field in ("browser_version", "os", "cpu", "runtime", "architecture", "workload"):
        if not _identifier(record.get(field)):
            failures.append(f"record {index} lacks {field}")
    if not _positive_int(record.get("ram_bytes")):
        failures.append(f"record {index} has invalid ram_bytes")
    if not _hex64(record.get("model_sha256")) or not _hex64(record.get("onnx_sha256")):
        failures.append(f"record {index} lacks model/ONNX digests")
    if not _positive_int(record.get("mcts_budget")):
        failures.append(f"record {index} lacks an MCTS budget")
    if record.get("cancellation_recovery") is not True:
        failures.append(f"record {index} lacks cancellation/recovery evidence")
    raw_recovery = record.get("recovery_evidence")
    if not isinstance(raw_recovery, dict):
        failures.append(f"record {index} lacks cancellation/recovery evidence")
        recovery = {}
    else:
        recovery = raw_recovery
        if (
            recovery.get("cancel_acknowledged") is not True
            or recovery.get("restart_completed") is not True
            or recovery.get("post_cancel_inference") is not True
        ):
            failures.append(f"record {index} cancellation/recovery evidence is incomplete")
    requested = record.get("provider_requested")
    observed = record.get("execution_placement")
    if requested not in PROVIDERS:
        failures.append(f"record {index} has an invalid requested provider")
    if observed not in PLACEMENTS:
        failures.append(f"record {index} has an invalid observed placement")
    verified_claim = record.get("gpu_execution_verified")
    if not isinstance(verified_claim, bool):
        failures.append(f"record {index} gpu_execution_verified must be a boolean")
        verified_claim = False
    if requested == "wasm":
        if observed != "wasm":
            failures.append(f"record {index} requested WASM but did not execute on WASM")
        if verified_claim:
            failures.append(f"record {index} cannot claim GPU execution for a WASM request")
    elif requested == "webgpu":
        if observed == "wasm":
            failures.append(f"record {index} silently fell back from WebGPU to WASM")
        elif observed == "cpu-fallback":
            # An explicit CPU fallback is measurable but never satisfies WebGPU.
            pass
        elif observed in WEBGPU_SESSION_PLACEMENTS:
            gate["webgpu_session"] = True
        if verified_claim and observed != "webgpu-session-verified":
            failures.append(f"record {index} claims GPU execution for non-verified placement")
    if observed == "webgpu-session-verified":
        if requested != "webgpu":
            failures.append(f"record {index} verified GPU placement without a WebGPU request")
        if verified_claim is not True:
            failures.append(f"record {index} verified GPU placement without the verified flag")
        verified, evidence_failures = _gpu_evidence(record, index)
        failures.extend(evidence_failures)
        gate["verified_gpu"] = verified and not evidence_failures
    if observed in ("wasm", "cpu-fallback") and verified_claim:
        failures.append(f"record {index} cannot claim GPU execution for {observed}")
    for field in (
        "cold_init_ms",
        "warm_init_ms",
        "first_inference_ms",
        "warm_inference_ms",
        "mcts_simulations_per_second",
    ):
        if not _positive_finite(record.get(field)):
            failures.append(f"record {index} has an invalid {field}")
    gate["measurement_ok"] = not failures
    return failures, gate


def _read_json(path: Path) -> dict:
    try:
        raw = path.read_text()
    except OSError as error:
        raise ValueError(f"cannot read device evidence {path.name}: {error}") from None
    try:
        value = json.loads(raw)
    except (ValueError, TypeError) as error:
        raise ValueError(f"device evidence {path.name} is not valid JSON: {error}") from None
    if not isinstance(value, dict):
        raise ValueError(f"device evidence {path.name} must be an object")
    return value


def validate_report(report: dict) -> dict:
    failures: list[str] = []

    def fail(message):
        failures.append(message)

    if not isinstance(report, dict):
        return {
            "ok": False,
            "structural_ok": False,
            "measurement_acceptance": False,
            "webgpu_session_acceptance": False,
            "verified_gpu_acceptance": False,
            "acceptance": False,
            "failures": ["report must be an object"],
            "records": 0,
            "blocked": 0,
        }
    if report.get("kind") != "browser-device-evidence" or report.get("schema") not in (
        SCHEMA,
        SCHEMA - 1,
    ):
        fail("unsupported device evidence schema")
    records = report.get("records")
    if not isinstance(records, list) or not records:
        fail("records must be a non-empty list")
        records = []
    required = report.get("required_platforms")
    if required is not None:
        if (
            not isinstance(required, list)
            or not required
            or any(platform not in PLATFORMS for platform in required)
            or len(set(required)) != len(required)
        ):
            fail("required_platforms is malformed")
            required = []
        observed_platforms = {
            record.get("platform")
            for record in records
            if isinstance(record, dict) and isinstance(record.get("platform"), str)
        }
        for platform in required:
            if platform not in observed_platforms:
                fail(f"required platform {platform} has no blocked or measured record")
    seen: set[tuple] = set()
    gates: dict[tuple, dict] = {}
    blocked_count = 0
    measured_count = 0
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            fail(f"record {index} is not an object")
            continue
        platform = record.get("platform")
        provider = record.get("provider_requested")
        if not isinstance(platform, str) or not isinstance(provider, str):
            fail(f"record {index} has malformed platform/provider identifiers")
            continue
        key = (platform, provider)
        if key in seen:
            fail(f"record {index} duplicates the {platform}/{provider} matrix entry")
            continue
        seen.add(key)
        record_failures, gate = _record_failures(record, index)
        failures.extend(record_failures)
        gates[key] = gate
        if record.get("status") == "blocked":
            blocked_count += 1
        elif record.get("status") == "measured":
            measured_count += 1
    for platform, provider in sorted(REQUIRED_MATRIX):
        if (platform, provider) not in seen:
            fail(f"missing mandatory device/provider entry {platform}/{provider}")
    structural_ok = not failures
    measurement_complete = structural_ok and all(
        (platform, provider) in seen
        and gates.get((platform, provider), {}).get("measurement_ok") is True
        and (
            provider == "wasm"
            or gates.get((platform, provider), {}).get("webgpu_session") is True
        )
        for platform, provider in REQUIRED_MATRIX
    )
    webgpu_complete = measurement_complete and all(
        gates.get((platform, provider), {}).get("webgpu_session") is True
        for platform, provider in REQUIRED_MATRIX
        if provider == "webgpu"
    )
    verified_gpu = webgpu_complete and all(
        gates.get((platform, provider), {}).get("verified_gpu") is True
        for platform, provider in REQUIRED_MATRIX
        if provider == "webgpu"
    )
    acceptance = bool(structural_ok and measurement_complete and verified_gpu)
    if report.get("acceptance") is True and not acceptance:
        failures.append("submitted acceptance contradicts computed device acceptance")
        structural_ok = False
        measurement_complete = False
        webgpu_complete = False
        verified_gpu = False
        acceptance = False
    verified_gpu_reason = None
    if not verified_gpu:
        verified_gpu_reason = (
            "verified GPU execution requires independent per-operator placement evidence "
            "for every mandatory WebGPU entry"
        )
    return {
        "ok": structural_ok,
        "structural_ok": structural_ok,
        "measurement_acceptance": bool(measurement_complete),
        "webgpu_session_acceptance": bool(webgpu_complete),
        "verified_gpu_acceptance": bool(verified_gpu),
        "verified_gpu_blocked_reason": verified_gpu_reason,
        "acceptance": acceptance,
        "failures": failures,
        "records": len(records),
        "measured": measured_count,
        "blocked": blocked_count,
        "matrix": {
            f"{platform}/{provider}": gates.get((platform, provider), {}).get("measurement_ok", False)
            for platform, provider in sorted(REQUIRED_MATRIX)
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        report = _read_json(args.report)
    except ValueError as error:
        result = {
            "ok": False,
            "structural_ok": False,
            "acceptance": False,
            "failures": [str(error)],
        }
        if args.output:
            args.output.write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result, indent=2))
        return 1
    result = validate_report(report)
    if args.output:
        args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    if not result["structural_ok"]:
        return 1
    return 0 if result["acceptance"] else 2


if __name__ == "__main__":
    sys.exit(main())
