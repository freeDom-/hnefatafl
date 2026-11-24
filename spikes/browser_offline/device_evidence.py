"""Validate physical browser/WebGPU evidence records.

A successful WebGPU-requested session is not GPU-only execution unless placement
evidence proves it. Unavailable devices are recorded as blocked, never as passes.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
import math

SCHEMA = 1
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


def validate_report(report: dict) -> dict:
    failures = []

    def fail(message):
        failures.append(message)

    if not isinstance(report, dict):
        return {"ok": False, "failures": ["report must be an object"]}
    if report.get("kind") != "browser-device-evidence" or report.get("schema") != SCHEMA:
        fail("unsupported device evidence schema")
    records = report.get("records")
    if not isinstance(records, list) or not records:
        fail("records must be a non-empty list")
        records = []
    required = report.get("required_platforms")
    if required is not None:
        if not isinstance(required, list) or any(
            platform not in PLATFORMS for platform in required
        ):
            fail("required_platforms is malformed")
            required = []
        observed = {record.get("platform") for record in records if isinstance(record, dict)}
        for platform in required:
            if platform not in observed:
                fail(f"required platform {platform} has no blocked or measured record")
    observed_matrix = {
        (record.get("platform"), record.get("provider_requested"))
        for record in records
        if isinstance(record, dict)
    }
    for platform, provider in sorted(REQUIRED_MATRIX):
        if (platform, provider) not in observed_matrix:
            fail(f"missing mandatory device/provider entry {platform}/{provider}")
    if report.get("acceptance") is True and any(
        isinstance(record, dict) and record.get("status") == "blocked" for record in records
    ):
        fail("acceptance cannot be claimed while a required platform is blocked")
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            fail(f"record {index} is not an object")
            continue
        platform = record.get("platform")
        if platform not in PLATFORMS:
            fail(f"record {index} has an unknown platform")
        if record.get("status") not in ("blocked", "measured"):
            fail(f"record {index} has an invalid status")
        if record.get("status") == "blocked":
            if not isinstance(record.get("blocked_reason"), str) or not record["blocked_reason"]:
                fail(f"blocked record {index} needs a reason")
            continue
        for field in ("browser_version", "os", "cpu", "runtime"):
            if not isinstance(record.get(field), str) or not record[field]:
                fail(f"record {index} lacks {field}")
        if (
            isinstance(record.get("ram_bytes"), bool)
            or not isinstance(record.get("ram_bytes"), int)
            or record["ram_bytes"] <= 0
        ):
            fail(f"record {index} has invalid ram_bytes")
        if not isinstance(record.get("architecture"), str) or not record["architecture"]:
            fail(f"record {index} lacks architecture")
        if not _hex64(record.get("model_sha256")) or not _hex64(record.get("onnx_sha256")):
            fail(f"record {index} lacks model/ONNX digests")
        if not isinstance(record.get("workload"), str) or not record["workload"]:
            fail(f"record {index} lacks workload")
        if not isinstance(record.get("mcts_budget"), int) or record["mcts_budget"] <= 0:
            fail(f"record {index} lacks MCTS budget")
        if record.get("cancellation_recovery") is not True:
            fail(f"record {index} lacks cancellation/recovery evidence")
        if not _positive_finite(record.get("mcts_simulations_per_second")):
            fail(f"record {index} has invalid MCTS throughput")
        requested = record.get("provider_requested")
        observed = record.get("execution_placement")
        if requested not in ("wasm", "webgpu"):
            fail(f"record {index} has an invalid requested provider")
        if observed not in PLACEMENTS:
            fail(f"record {index} has an invalid observed placement")
        if requested == "webgpu" and observed == "wasm":
            fail(f"record {index} silently fell back from WebGPU to WASM")
        if observed == "webgpu-mixed" and record.get("gpu_execution_verified") is True:
            fail(f"record {index} cannot claim GPU-only acceptance for mixed execution")
        if observed == "webgpu-session-verified":
            evidence = record.get("gpu_evidence")
            placements = (
                evidence.get("per_operator_placement") if isinstance(evidence, dict) else None
            )
            session = evidence.get("session") if isinstance(evidence, dict) else None
            if (
                not isinstance(evidence, dict)
                or evidence.get("evidence_source") != "ort-web-per-operator-v1"
                or evidence.get("all_operators_covered") is not True
                or evidence.get("gpu_execution_verified") is not True
                or not isinstance(session, dict)
                or session.get("model_sha256") != record.get("model_sha256")
                or session.get("onnx_sha256") != record.get("onnx_sha256")
                or session.get("runtime") != record.get("runtime")
                or not isinstance(placements, list)
                or not placements
                or any(
                    not isinstance(item, dict)
                    or item.get("provider") != "webgpu"
                    or not isinstance(item.get("operator"), str)
                    for item in placements
                )
            ):
                fail(f"record {index} claims verified GPU execution without per-operator evidence")
        for field in (
            "cold_init_ms",
            "warm_init_ms",
            "first_inference_ms",
            "warm_inference_ms",
            "mcts_simulations_per_second",
        ):
            if not _positive_finite(record.get(field)):
                fail(f"record {index} has an invalid {field}")
    structural_ok = not failures
    blocked = any(
        isinstance(record, dict) and record.get("status") == "blocked" for record in records
    )
    measured = sum(
        1 for record in records if isinstance(record, dict) and record.get("status") == "measured"
    )
    computed_acceptance = structural_ok and not blocked and measured >= 1
    if report.get("acceptance") is True and not computed_acceptance:
        failures.append("submitted acceptance contradicts computed device acceptance")
        structural_ok = False
        computed_acceptance = False
    return {
        "ok": structural_ok,
        "structural_ok": structural_ok,
        "acceptance": computed_acceptance,
        "failures": failures,
        "records": len(records),
        "blocked": sum(
            1
            for record in records
            if isinstance(record, dict) and record.get("status") == "blocked"
        ),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = validate_report(json.loads(args.report.read_text()))
    if args.output:
        args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    sys.exit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
