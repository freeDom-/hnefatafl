"""Fail-closed validation of an owner-supplied competitive checkpoint.

The tool never invents weights. It validates the existing export provenance,
then consumes native/browser parity and benchmark reports produced by the
established harnesses. Missing checkpoints, exports or required reports produce a
blocked report rather than a pass.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

SPIKE = Path(__file__).resolve().parent
ROOT = SPIKE.parents[1]
for path in (str(SPIKE), str(ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

SCHEMA = 1
LEVELS = ("structural-smoke", "representative-architecture", "competitive-trained")
REQUIRED_PARITY = ("level_a", "level_b", "direct_relay")


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def blocked_report(reason: str, *, ruleset: str | None = None) -> dict:
    return {
        "kind": "browser-competitive-validation",
        "schema": SCHEMA,
        "status": "blocked",
        "acceptance": False,
        "validation_level": None,
        "ruleset": ruleset,
        "blocked_reasons": [reason],
        "model": None,
        "parity": {},
        "benchmarks": [],
        "playing_strength": {"status": "blocked", "reason": "no tournament evidence"},
        "authoritative": False,
    }


def model_provenance(export_dir: Path, checkpoint: Path | None = None) -> dict:
    import prepare_neural
    from hnefatafl.models import load_checkpoint, weights_fingerprint

    export = prepare_neural.validate_export_dir(export_dir, allow_unvalidated=False)
    sidecar = export["sidecar"]
    source = sidecar.get("source") or {}
    fingerprint = source.get("weights_sha256")
    if not isinstance(fingerprint, str) or len(fingerprint) != 64:
        raise ValueError("export does not record a source-weight fingerprint")
    if checkpoint is not None:
        loaded, metadata = load_checkpoint(checkpoint.resolve(), device="cpu")
        actual = weights_fingerprint(loaded)
        if actual != fingerprint:
            raise ValueError("checkpoint weights do not match the exported model")
        if metadata.get("ruleset", sidecar["ruleset"]) != sidecar["ruleset"]:
            raise ValueError("checkpoint ruleset does not match the exported model")
    onnx = export_dir / "model.onnx"
    return {
        "ruleset": sidecar["ruleset"],
        "size": sidecar["size"],
        "architecture": {
            "channels": sidecar["model_config"].get("channels"),
            "blocks": sidecar["model_config"].get("blocks"),
        },
        "encoder_version": source.get("encoder_version"),
        "source_weights_sha256": fingerprint,
        "onnx_sha256": _sha256_file(onnx),
        "onnx_bytes": onnx.stat().st_size,
        "validated_export": True,
        "checkpoint_supplied": checkpoint is not None,
    }


def validate_report(report: dict) -> dict:
    failures: list[str] = []

    def fail(message: str) -> None:
        failures.append(message)

    if not isinstance(report, dict):
        return {"ok": False, "failures": ["report must be an object"]}
    if report.get("kind") != "browser-competitive-validation" or report.get("schema") != SCHEMA:
        fail("unsupported competitive validation report schema")
    if report.get("authoritative") is not False:
        fail("competitive validation must be non-authoritative")
    status = report.get("status")
    if status not in ("validated", "blocked"):
        fail("status must be validated or blocked")
    if status == "blocked":
        reasons = report.get("blocked_reasons")
        if (
            not isinstance(reasons, list)
            or not reasons
            or any(not isinstance(reason, str) or not reason for reason in reasons)
        ):
            fail("blocked report must list non-empty reasons")
        if report.get("acceptance") is not False:
            fail("blocked report cannot claim acceptance")
        if report.get("validation_level") is not None:
            fail("blocked report cannot claim a validation level")
        return {"ok": not failures, "failures": failures, "status": status}
    level = report.get("validation_level")
    if level not in LEVELS:
        fail("validated report needs a recognized validation level")
    if report.get("acceptance") is not True:
        fail("validated report must set acceptance true")
    model = report.get("model")
    if not isinstance(model, dict):
        fail("validated report lacks model provenance")
    else:
        for field in ("ruleset", "size", "encoder_version"):
            if model.get(field) in (None, ""):
                fail(f"model provenance lacks {field}")
        for field in ("source_weights_sha256", "onnx_sha256"):
            value = model.get(field)
            if not isinstance(value, str) or len(value) != 64:
                fail(f"model provenance has an invalid {field}")
        if model.get("validated_export") is not True:
            fail("model export is not validated")
        if level == "competitive-trained" and model.get("checkpoint_supplied") is not True:
            fail("competitive-trained validation requires the source checkpoint fingerprint")
    parity = report.get("parity") or {}
    for gate in REQUIRED_PARITY:
        entry = parity.get(gate)
        if not isinstance(entry, dict) or entry.get("ok") is not True:
            fail(f"missing or failed parity gate {gate}")
    benchmarks = report.get("benchmarks")
    if not isinstance(benchmarks, list) or not benchmarks:
        fail("validated report lacks benchmark records")
    else:
        for index, record in enumerate(benchmarks):
            if not isinstance(record, dict):
                fail(f"benchmark {index} is not an object")
                continue
            if record.get("completed_simulations") is None or record.get("ok") is not True:
                fail(f"benchmark {index} has no valid completed work")
            if record.get("model_sha256") != (model or {}).get("onnx_sha256"):
                fail(f"benchmark {index} model identity mismatch")
    strength = report.get("playing_strength") or {}
    if strength.get("status") not in ("not-run", "blocked", "measured"):
        fail("playing strength status is invalid")
    return {"ok": not failures, "failures": failures, "status": status}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--export-dir", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--validate", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.validate:
        report = json.loads(args.validate.read_text())
        result = validate_report(report)
        print(json.dumps(result, indent=2))
        sys.exit(0 if result["ok"] else 1)
    if not args.checkpoint:
        report = blocked_report("owner-supplied competitive checkpoint is unavailable")
    elif not args.export_dir:
        report = blocked_report("validated ONNX export directory is unavailable")
    else:
        try:
            report = {
                **blocked_report(
                    "native/browser parity and benchmark reports have not been supplied"
                ),
                "model": model_provenance(args.export_dir, args.checkpoint),
                "blocked_reasons": [
                    "run native/browser Level A, Level B and direct/relay parity",
                    "run fixed-work and fixed-time MCTS benchmarks",
                    "run a role-balanced playing-strength tournament",
                ],
            }
        except Exception as error:  # noqa: BLE001 - fail closed with a useful reason
            report = blocked_report(f"model provenance validation failed: {error}")
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    sys.exit(0 if report.get("status") == "blocked" else 0)


if __name__ == "__main__":
    main()
