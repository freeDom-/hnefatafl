"""Validate physical browser/WebGPU evidence records.

A successful WebGPU-requested session is not GPU-only execution unless placement
evidence proves it. Unavailable devices are recorded as blocked, never as passes.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

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
PLACEMENTS = (
    "wasm",
    "cpu-fallback",
    "webgpu-session-unverified",
    "webgpu-mixed",
    "webgpu-session-verified",
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
        for field in ("browser_version", "os", "cpu", "ram_bytes"):
            if record.get(field) in (None, ""):
                fail(f"record {index} lacks {field}")
        requested = record.get("provider_requested")
        observed = record.get("execution_placement")
        if requested not in ("wasm", "webgpu"):
            fail(f"record {index} has an invalid requested provider")
        if observed not in PLACEMENTS:
            fail(f"record {index} has an invalid observed placement")
        if requested == "webgpu" and observed == "wasm":
            fail(f"record {index} silently fell back from WebGPU to WASM")
        if (
            observed == "webgpu-session-verified"
            and record.get("gpu_execution_verified") is not True
        ):
            fail(f"record {index} claims verified GPU execution without evidence")
        for field in ("cold_init_ms", "warm_init_ms", "mcts_simulations_per_second"):
            if not isinstance(record.get(field), (int, float)) or isinstance(
                record.get(field), bool
            ):
                fail(f"record {index} has an invalid {field}")
    return {
        "ok": not failures,
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
