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
    expected_model = (model or {}).get("onnx_sha256")
    for gate in REQUIRED_PARITY:
        entry = parity.get(gate)
        if not isinstance(entry, dict):
            fail(f"missing parity evidence for {gate}")
            continue
        if gate in ("level_a", "level_b"):
            comparison = entry.get("comparison")
            if not isinstance(comparison, dict):
                fail(f"{gate}: no comparator evidence")
                continue
            if comparison.get("level") != ("a" if gate == "level_a" else "b"):
                fail(f"{gate}: comparison level mismatch")
            if comparison.get("acceptance") is not True:
                fail(f"{gate}: comparator did not accept")
            if (comparison.get("execution_validity") or {}).get("ok") is not True:
                fail(f"{gate}: execution validity failed")
            if (comparison.get("workload_completion") or {}).get("complete") is not True:
                fail(f"{gate}: workload did not complete")
            if (comparison.get("workload") or {}).get("identity_verified") is not True:
                fail(f"{gate}: workload identity was not verified")
            numerical = comparison.get("numerical_comparison") or {}
            deterministic = comparison.get("deterministic_parity") or {}
            if gate == "level_a" and deterministic.get("ok") is not True:
                fail("level_a: deterministic parity failed")
            if gate == "level_b":
                if numerical.get("best_move_parity") is not True:
                    fail("level_b: best-move parity failed")
                if numerical.get("positions", 0) < 1:
                    fail("level_b: no numerical comparison positions")
            if entry.get("model_sha256") not in (None, expected_model):
                fail(f"{gate}: model digest mismatch")
        else:
            fixed = entry.get("fixed")
            timed = entry.get("fixed_time")
            for label, document in (("fixed", fixed), ("fixed_time", timed)):
                if not isinstance(document, dict):
                    fail(f"direct_relay: missing {label} evidence")
                    continue
                if document.get("gate") != "direct-relay-compact":
                    fail(f"direct_relay/{label}: wrong gate")
                if document.get("ok") is not True or document.get("collector_ok") is not True:
                    fail(f"direct_relay/{label}: collector or acceptance failed")
                if document.get("coverage_ok") is not True or document.get("cases_ok") is not True:
                    fail(f"direct_relay/{label}: coverage/case failure")
                if not isinstance(document.get("cases"), list) or not document["cases"]:
                    fail(f"direct_relay/{label}: no cases")
            if (
                not isinstance(entry.get("model_sha256"), str)
                or entry.get("model_sha256") != expected_model
            ):
                fail("direct_relay: model digest mismatch")
    benchmarks = report.get("benchmarks")
    if not isinstance(benchmarks, list) or not benchmarks:
        fail("validated report lacks benchmark records")
    else:
        modes = set()
        for index, record in enumerate(benchmarks):
            if not isinstance(record, dict):
                fail(f"benchmark {index} is not an object")
                continue
            mode = record.get("mode")
            if mode not in ("fixed_simulations", "fixed_time"):
                fail(f"benchmark {index} has an unsupported mode")
                continue
            modes.add(mode)
            if record.get("ok") is not True:
                fail(f"benchmark {index} failed")
            if record.get("model_sha256") != expected_model:
                fail(f"benchmark {index} model identity mismatch")
            if record.get("provider") not in ("wasm", "webgpu"):
                fail(f"benchmark {index} provider is missing")
            if (
                not isinstance(record.get("completed_simulations"), int)
                or record.get("completed_simulations") <= 0
            ):
                fail(f"benchmark {index} has no completed work")
            if mode == "fixed_simulations":
                if record.get("completed_simulations") != record.get("requested_simulations"):
                    fail(f"benchmark {index} did not complete fixed work")
            elif not isinstance(record.get("completion_reason"), str):
                fail(f"benchmark {index} lacks completion reason")
                if not isinstance(record.get("elapsed_ms"), (int, float)):
                    fail(f"benchmark {index} lacks elapsed time")
        if modes != {"fixed_simulations", "fixed_time"}:
            fail("benchmarks must cover fixed simulations and fixed time")
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
    if args.validate or args.report:
        source = args.validate or args.report
        report = json.loads(source.read_text())
        result = validate_report(report)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result, indent=2))
        if not result["ok"]:
            sys.exit(1)
        sys.exit(0 if report.get("status") == "validated" else 2)
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
    # Blocked generation is not successful acceptance; use exit 2 so callers
    # cannot confuse an explicit resource gap with a validated gate.
    sys.exit(2 if report.get("status") == "blocked" else 0)


if __name__ == "__main__":
    main()
