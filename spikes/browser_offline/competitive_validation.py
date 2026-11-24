"""Fail-closed validation of an owner-supplied competitive checkpoint.

The tool never invents weights. It validates the existing export provenance,
then consumes native/browser parity and benchmark reports produced by the
established harnesses. Missing checkpoints, exports or required reports produce a
blocked report rather than a pass.

Two layers are deliberately distinct:

* ``--validate`` inspects an already-written validation report. It checks the
  report's own structure and internal consistency and reports
  ``verification: report-inspection``. It never turns a synthetic summary into
  independently verified acceptance.
* ``--checkpoint ... --export-dir ... --report MANIFEST`` independently verifies
  the checkpoint/export identity and re-runs every supplied observation through
  the real comparator/benchmark/path-parity validators. Only this path can
  report ``verification: independently-verified-evidence`` and acceptance.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any

SPIKE = Path(__file__).resolve().parent
ROOT = SPIKE.parents[1]
for path in (str(SPIKE), str(ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

SCHEMA = 2
LEVELS = ("structural-smoke", "representative-architecture", "competitive-trained")
ACCEPTING_LEVELS = ("representative-architecture", "competitive-trained")
REQUIRED_PARITY = ("level_a", "level_b", "direct_relay")
GATES = {"level_a": "a", "level_b": "b"}
POLICIES = ("structural", "competitive")
COMPETITIVE_ARCHITECTURE = {"channels": 32, "blocks": 2}
COMPETITIVE_BENCHMARK_POLICY = {
    "kinds": {"opening", "midgame", "endgame", "tactical", "repetition"},
    "simulation_budgets": {128, 256, 512, 1024},
    "time_budgets": {0.5, 1.0, 2.0, 5.0},
    "repetitions": 2,
    "warmup": 1,
}
NATIVE_RUNTIME_PREFIX = "native"
BROWSER_RUNTIME_PREFIX = "browser"
OWNERSHIP = "independently-verified-evidence"
INSPECTION = "report-inspection"


def _hex64(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _positive_int(value: Any) -> bool:
    return type(value) is int and value > 0


def _positive_finite(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
        and value > 0
    )


def _object(value: Any, label: str, failures: list[str]) -> dict:
    if isinstance(value, dict):
        return value
    failures.append(f"{label} must be an object")
    return {}


def _field(value: Any, key: str, default: Any = None) -> Any:
    return value.get(key, default) if isinstance(value, dict) else default


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json(path: Path) -> dict:
    """Read one evidence document and return a structured failure on bad input."""
    try:
        raw = path.read_text()
    except OSError as error:
        raise ValueError(f"cannot read evidence {path.name}: {error}") from None
    try:
        value = json.loads(raw)
    except (ValueError, TypeError) as error:
        raise ValueError(f"evidence {path.name} is not valid JSON: {error}") from None
    if not isinstance(value, dict):
        raise ValueError(f"evidence {path.name} must be an object")
    return value


def blocked_report(reason: str, *, ruleset: str | None = None) -> dict:
    return {
        "kind": "browser-competitive-validation",
        "schema": SCHEMA,
        "status": "blocked",
        "acceptance": False,
        "competitive_acceptance": False,
        "independent_verification": False,
        "verification": INSPECTION,
        "validation_level": None,
        "ruleset": ruleset,
        "blocked_reasons": [reason],
        "failures": [],
        "model": None,
        "provenance": None,
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
    if not _hex64(fingerprint):
        raise ValueError("export does not record a valid source-weight fingerprint")
    onnx = export_dir / "model.onnx"
    onnx_digest = _sha256_file(onnx)
    input_contract = sidecar.get("input") or {}
    shape = input_contract.get("shape")
    size = shape[-1] if isinstance(shape, list) and len(shape) >= 2 else sidecar.get("size")
    model_config = sidecar.get("model_config")
    if not isinstance(model_config, dict):
        raise ValueError("export sidecar lacks model configuration")
    architecture = {
        "channels": model_config.get("channels"),
        "blocks": model_config.get("blocks"),
    }
    if not _positive_int(architecture["channels"]) or not (
        type(architecture["blocks"]) is int and architecture["blocks"] >= 0
    ):
        raise ValueError("export architecture is malformed")
    encoder = source.get("encoder_version") or input_contract.get("encoding")
    if not isinstance(encoder, str) or not encoder:
        raise ValueError("export does not record an encoder version")
    if not _positive_int(size):
        raise ValueError("export does not record a valid board size")
    checkpoint_digest = None
    metadata_digest = None
    metadata_summary = None
    structural_smoke = False
    if checkpoint is not None:
        try:
            checkpoint_bytes = checkpoint.read_bytes()
        except OSError as error:
            raise ValueError(f"cannot read checkpoint: {error}") from None
        checkpoint_digest = hashlib.sha256(checkpoint_bytes).hexdigest()
        loaded, payload = load_checkpoint(checkpoint.resolve(), device="cpu")
        actual = weights_fingerprint(loaded)
        if actual != fingerprint:
            raise ValueError("checkpoint weights do not match the exported model")
        raw_metadata = payload.get("metadata") if isinstance(payload, dict) else None
        metadata = raw_metadata if isinstance(raw_metadata, dict) else {}
        if metadata.get("ruleset", sidecar["ruleset"]) != sidecar["ruleset"]:
            raise ValueError("checkpoint ruleset does not match the exported model")
        try:
            canonical_metadata = json.dumps(
                metadata, sort_keys=True, separators=(",", ":"), allow_nan=False
            )
        except (TypeError, ValueError) as error:
            raise ValueError(f"checkpoint metadata is not canonicalisable: {error}") from None
        metadata_digest = hashlib.sha256(canonical_metadata.encode()).hexdigest()
        descriptor = " ".join(
            str(metadata.get(field) or "") for field in ("name", "note", "description")
        ).lower()
        structural_smoke = "smoke" in descriptor or "structural" in descriptor
        metadata_summary = {
            "keys": sorted(key for key in metadata if isinstance(key, str)),
            "name": metadata.get("name"),
            "note": metadata.get("note"),
            "created_at": metadata.get("created_at"),
            "version": metadata.get("version"),
        }
    return {
        "ruleset": sidecar["ruleset"],
        "size": size,
        "architecture": architecture,
        "encoder_version": encoder,
        "source_weights_sha256": fingerprint,
        "onnx_sha256": onnx_digest,
        "onnx_bytes": onnx.stat().st_size,
        "checkpoint_sha256": checkpoint_digest,
        "checkpoint_metadata_sha256": metadata_digest,
        "checkpoint_metadata": metadata_summary,
        "structural_smoke_checkpoint": structural_smoke,
        "training_classification": (
            "structural-smoke" if structural_smoke else "unclassified-owner-weights"
        ),
        "validated_export": True,
        "checkpoint_supplied": checkpoint is not None,
    }


def validate_report(report: dict) -> dict:
    """Structural inspection of a previously written validation report.

    Inspection never constitutes independent verification. ``acceptance`` in the
    result is always false; ``report_claims_acceptance`` records what the
    inspected document claimed so consumers can distinguish the two.
    """
    failures: list[str] = []

    def fail(message: str) -> None:
        failures.append(message)

    if not isinstance(report, dict):
        return {
            "ok": False,
            "failures": ["report must be an object"],
            "verification": INSPECTION,
            "independent_verification": False,
            "acceptance": False,
            "competitive_acceptance": False,
        }
    if report.get("kind") != "browser-competitive-validation" or report.get("schema") not in (
        SCHEMA,
        SCHEMA - 1,
    ):
        fail("unsupported competitive validation report schema")
    if report.get("authoritative") is not False:
        fail("competitive validation must be non-authoritative")
    status = report.get("status")
    if status not in ("validated", "structural-validated", "blocked", "invalid"):
        fail("status must be validated, structural-validated, blocked or invalid")
    if status in ("blocked", "invalid"):
        reasons = report.get("blocked_reasons") if status == "blocked" else report.get("failures")
        if not isinstance(reasons, list) or not reasons:
            fail(f"{status} report must list reasons")
        elif any(not isinstance(reason, str) or not reason for reason in reasons):
            fail(f"{status} report reasons must be non-empty strings")
        if report.get("acceptance") not in (False, None):
            fail(f"{status} report cannot claim acceptance")
        if report.get("competitive_acceptance") not in (False, None):
            fail(f"{status} report cannot claim competitive acceptance")
        if status == "blocked" and report.get("validation_level") is not None:
            fail("blocked report cannot claim a validation level")
        return {
            "ok": not failures,
            "failures": failures,
            "status": status,
            "verification": INSPECTION,
            "independent_verification": False,
            "acceptance": False,
            "competitive_acceptance": False,
        }
    structural = status == "structural-validated"
    level = report.get("validation_level")
    if level not in LEVELS:
        fail("validated report needs a recognized validation level")
        level = None
    if structural and level not in (None, "structural-smoke"):
        fail("structural validation is only valid for structural-smoke evidence")
    if not structural and level == "structural-smoke":
        fail("structural-smoke evidence must be reported as structural-validated")
    if report.get("acceptance") is not True and not structural:
        fail("validated report must set acceptance true")
    if structural and report.get("acceptance") is not False:
        fail("structural validation must not claim acceptance")
    claimed_competitive = level == "competitive-trained" and not structural
    if report.get("competitive_acceptance") is True and not claimed_competitive:
        fail("competitive acceptance requires competitive-trained evidence")
    model = report.get("model")
    if not isinstance(model, dict):
        fail("validated report lacks model provenance")
        model = {}
    else:
        for field in ("ruleset", "encoder_version"):
            if not isinstance(model.get(field), str) or not model[field]:
                fail(f"model provenance lacks {field}")
        if not _positive_int(model.get("size")):
            fail("model provenance lacks a valid size")
        for field in ("source_weights_sha256", "onnx_sha256"):
            if not _hex64(model.get(field)):
                fail(f"model provenance has an invalid {field}")
        architecture = _object(model.get("architecture"), "model architecture", failures)
        if not _positive_int(architecture.get("channels")):
            fail("model provenance has invalid architecture channels")
        if type(architecture.get("blocks")) is not int:
            fail("model provenance has invalid architecture blocks")
        if model.get("validated_export") is not True:
            fail("model export is not validated")
        if level == "competitive-trained":
            if model.get("checkpoint_supplied") is not True:
                fail("competitive-trained validation requires the source checkpoint fingerprint")
            if not _hex64(model.get("checkpoint_sha256")):
                fail("competitive-trained validation requires the checkpoint digest")
    parity = report.get("parity")
    if not isinstance(parity, dict):
        fail("parity must be an object")
        parity = {}
    expected_model = model.get("onnx_sha256")
    for gate in REQUIRED_PARITY:
        if structural and gate != "level_a":
            continue
        entry = parity.get(gate)
        if not isinstance(entry, dict):
            fail(f"missing parity evidence for {gate}")
            continue
        if gate in GATES:
            comparison = entry.get("comparison")
            if not isinstance(comparison, dict):
                fail(f"{gate}: no comparator evidence")
                continue
            if comparison.get("level") != GATES[gate]:
                fail(f"{gate}: comparison level mismatch")
            if comparison.get("acceptance") is not True:
                fail(f"{gate}: comparator did not accept")
            if _field(_field(comparison, "execution_validity"), "ok") is not True:
                fail(f"{gate}: execution validity failed")
            if _field(_field(comparison, "workload_completion"), "complete") is not True:
                fail(f"{gate}: workload did not complete")
            if _field(_field(comparison, "workload"), "identity_verified") is not True:
                fail(f"{gate}: workload identity was not verified")
            numerical = _object(
                comparison.get("numerical_comparison"), f"{gate} numerical comparison", failures
            )
            deterministic = _object(
                comparison.get("deterministic_parity"), f"{gate} deterministic parity", failures
            )
            if gate == "level_a" and deterministic.get("ok") is not True:
                fail("level_a: deterministic parity failed")
            if gate == "level_b":
                if numerical.get("best_move_parity") is not True:
                    fail("level_b: best-move parity failed")
                positions = numerical.get("positions")
                if not _positive_int(positions):
                    fail("level_b: no numerical comparison positions")
            if gate == "level_b" and entry.get("model_sha256") != expected_model:
                fail("level_b: model digest is missing or mismatched")
            elif gate == "level_a" and entry.get("model_sha256") not in (None, expected_model):
                fail("level_a: model digest mismatch")
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
            if entry.get("model_sha256") != expected_model:
                fail("direct_relay: model digest mismatch")
    benchmarks = report.get("benchmarks")
    if not isinstance(benchmarks, list) or not benchmarks:
        if not structural:
            fail("validated report lacks benchmark records")
    else:
        modes = set()
        runtimes = set()
        for index, record in enumerate(benchmarks):
            if not isinstance(record, dict):
                fail(f"benchmark {index} is not an object")
                continue
            mode = record.get("mode")
            if mode not in ("fixed_simulations", "fixed_time"):
                fail(f"benchmark {index} has an unsupported mode")
                continue
            modes.add(mode)
            runtime = record.get("runtime")
            if isinstance(runtime, str):
                runtimes.add(runtime.split("-", 1)[0])
            if record.get("ok") is not True:
                fail(f"benchmark {index} failed")
            if record.get("model_sha256") != expected_model:
                fail(f"benchmark {index} model identity mismatch")
            if record.get("provider") not in ("wasm", "webgpu"):
                fail(f"benchmark {index} provider is missing")
            if not _positive_int(record.get("completed_simulations")):
                fail(f"benchmark {index} has no completed work")
            if mode == "fixed_simulations":
                if record.get("completed_simulations") != record.get("requested_simulations"):
                    fail(f"benchmark {index} did not complete fixed work")
            elif not isinstance(record.get("completion_reason"), str):
                fail(f"benchmark {index} lacks completion reason")
            elapsed = record.get("elapsed_ms")
            if not _positive_finite(elapsed):
                fail(f"benchmark {index} lacks positive finite elapsed time")
        if not structural and modes != {"fixed_simulations", "fixed_time"}:
            fail("benchmarks must cover fixed simulations and fixed time")
        if not structural and runtimes != {"native", "browser"}:
            fail("benchmarks must include native and browser runtime records")
    strength = report.get("playing_strength")
    if not isinstance(strength, dict):
        fail("playing_strength must be an object")
        strength = {}
    if strength.get("status") not in ("not-run", "blocked", "measured"):
        fail("playing strength status is invalid")
    return {
        "ok": not failures,
        "failures": failures,
        "status": status,
        "verification": INSPECTION,
        "independent_verification": False,
        "acceptance": False,
        "competitive_acceptance": False,
        "report_claims_acceptance": report.get("acceptance") is True,
        "report_claims_competitive_acceptance": report.get("competitive_acceptance") is True,
    }


def _load_manifest(path: Path) -> dict:
    manifest = _read_json(path)
    if manifest.get("kind") != "browser-competitive-evidence" or manifest.get("schema") != SCHEMA:
        raise ValueError("unsupported competitive evidence manifest schema")
    level = manifest.get("validation_level")
    if level not in LEVELS:
        raise ValueError("evidence manifest has an unsupported validation level")
    policy = manifest.get("acceptance_policy")
    if policy not in POLICIES:
        raise ValueError("evidence manifest has an unsupported acceptance policy")
    if level == "competitive-trained" and policy != "competitive":
        raise ValueError("competitive-trained evidence requires the competitive acceptance policy")
    if level != "competitive-trained" and policy != "structural":
        raise ValueError(f"{level} evidence cannot use the competitive acceptance policy")
    provenance = manifest.get("provenance")
    if not isinstance(provenance, dict):
        raise ValueError("evidence manifest lacks explicit provenance")
    if provenance.get("training_status") != level:
        raise ValueError("provenance training status contradicts the manifest validation level")
    for field in (
        "checkpoint_sha256",
        "onnx_sha256",
        "source_weights_sha256",
    ):
        if not _hex64(provenance.get(field)):
            raise ValueError(f"provenance {field} is not a hexadecimal digest")
    if not isinstance(provenance.get("ruleset"), str) or not provenance["ruleset"]:
        raise ValueError("provenance ruleset is missing")
    if not isinstance(provenance.get("encoder_version"), str) or not provenance["encoder_version"]:
        raise ValueError("provenance encoder version is missing")
    architecture = provenance.get("architecture")
    if not isinstance(architecture, dict):
        raise ValueError("provenance architecture is missing")
    if not _positive_int(architecture.get("channels")) or not (
        type(architecture.get("blocks")) is int and architecture["blocks"] >= 0
    ):
        raise ValueError("provenance architecture is malformed")
    if level == "competitive-trained":
        training = provenance.get("training")
        if not isinstance(training, dict):
            raise ValueError("competitive evidence requires explicit training provenance")
        if training.get("classification") != "competitive-trained":
            raise ValueError("training provenance does not classify competitive training")
        if not _hex64(training.get("checkpoint_metadata_sha256")):
            raise ValueError("training provenance lacks the checkpoint metadata digest")
        attestation = training.get("attestation")
        if not isinstance(attestation, dict):
            raise ValueError("competitive training provenance requires an owner attestation")
        if attestation.get("source") != "owner":
            raise ValueError("training attestation must identify the owner as its source")
        if not isinstance(attestation.get("statement"), str) or not attestation["statement"]:
            raise ValueError("training attestation requires a statement")
        if not isinstance(attestation.get("recorded_at"), str) or not attestation["recorded_at"]:
            raise ValueError("training attestation requires a recorded_at value")
    evidence = manifest.get("evidence")
    if not isinstance(evidence, dict):
        raise ValueError("evidence manifest lacks evidence paths")
    resolved: dict[str, Any] = {}
    for key in ("level_a", "level_b", "direct_relay"):
        if key not in evidence:
            continue
        entry = evidence[key]
        if not isinstance(entry, dict):
            raise ValueError(f"{key} evidence must be an object")
        resolved[key] = {}
        for name, raw in entry.items():
            if not isinstance(name, str) or not name:
                raise ValueError(f"{key} evidence name is malformed")
            if not isinstance(raw, str) or not raw:
                raise ValueError(f"{key}.{name} path is malformed")
            candidate = (path.parent / raw).resolve()
            if not candidate.is_file():
                raise ValueError(f"{key}.{name} evidence file is missing")
            resolved[key][name] = candidate
    benchmarks = evidence.get("benchmarks")
    if benchmarks is not None:
        if not isinstance(benchmarks, list) or not benchmarks:
            raise ValueError("benchmarks evidence must be a non-empty list")
        resolved["benchmarks"] = []
        for raw in benchmarks:
            if not isinstance(raw, str) or not raw:
                raise ValueError("benchmark evidence path is malformed")
            candidate = (path.parent / raw).resolve()
            if not candidate.is_file():
                raise ValueError("benchmark evidence file is missing")
            resolved["benchmarks"].append(candidate)
    return {"level": level, "policy": policy, "provenance": provenance, "paths": resolved}


def _bind_provenance(
    provenance: dict,
    declared: dict,
    *,
    level: str,
) -> list[str]:
    failures = []
    for field in ("ruleset", "encoder_version", "source_weights_sha256", "onnx_sha256"):
        if declared.get(field) != provenance.get(field):
            failures.append(f"manifest provenance {field} does not match the validated export")
    if declared.get("architecture") != provenance.get("architecture"):
        failures.append("manifest provenance architecture does not match the validated export")
    if provenance.get("checkpoint_sha256") is None:
        failures.append("manifest provenance requires an independently verified checkpoint")
    elif declared.get("checkpoint_sha256") != provenance.get("checkpoint_sha256"):
        failures.append("manifest checkpoint digest does not match the supplied checkpoint")
    if level == "competitive-trained":
        training = declared.get("training")
        if not isinstance(training, dict):
            failures.append("competitive evidence lacks training provenance")
        else:
            if training.get("checkpoint_metadata_sha256") != provenance.get(
                "checkpoint_metadata_sha256"
            ):
                failures.append(
                    "training provenance metadata digest does not match the checkpoint"
                )
        if provenance.get("structural_smoke_checkpoint"):
            failures.append(
                "a known structural smoke checkpoint cannot be classified as competitive-trained"
            )
        architecture = provenance.get("architecture") or {}
        if (
            architecture.get("channels", 0) < COMPETITIVE_ARCHITECTURE["channels"]
            or architecture.get("blocks", 0) < COMPETITIVE_ARCHITECTURE["blocks"]
        ):
            failures.append(
                "competitive-trained evidence requires at least 32 channels and two residual blocks"
            )
    return failures


def _runtime_kind(report: dict) -> str | None:
    runtime = report.get("runtime")
    if not isinstance(runtime, str) or not runtime:
        return None
    prefix = runtime.split("-", 1)[0]
    if prefix == NATIVE_RUNTIME_PREFIX:
        return "native"
    if prefix == BROWSER_RUNTIME_PREFIX:
        return "browser"
    return None


def _validate_parity_gate(
    gate: str,
    paths: dict[str, Path],
    model_digest: str,
    provenance: dict,
    *,
    require_independent_runtime: bool,
) -> tuple[dict | None, list[str]]:
    import mcts_compare

    failures: list[str] = []
    for name in ("native", "browser"):
        if name not in paths:
            failures.append(f"{gate} evidence needs native and browser reports")
    if failures:
        return None, failures
    native = _read_json(paths["native"])
    browser = _read_json(paths["browser"])
    expected_level = GATES[gate]
    for label, document in (("native", native), ("browser", browser)):
        if document.get("level") != expected_level:
            failures.append(
                f"{gate}/{label}: evidence is not a Level {expected_level.upper()} report"
            )
    native_kind = _runtime_kind(native)
    browser_kind = _runtime_kind(browser)
    if native_kind is None or browser_kind is None:
        failures.append(f"{gate}: evidence is missing a recognizable runtime identity")
    if require_independent_runtime:
        if native_kind != "native":
            failures.append(f"{gate}: native evidence does not declare a native runtime")
        if browser_kind != "browser":
            failures.append(f"{gate}: browser evidence does not declare a browser runtime")
        if native.get("runtime") == browser.get("runtime"):
            failures.append(f"{gate}: native and browser evidence share one runtime")
    comparison = mcts_compare.compare(native, browser, require_best_move=gate == "level_b")
    entry = {
        "model_sha256": model_digest,
        "runtime": {
            "native": native.get("runtime"),
            "browser": browser.get("runtime"),
            "independent": native_kind == "native" and browser_kind == "browser",
        },
        "comparison": comparison,
    }
    if comparison.get("acceptance") is not True:
        failures.append(f"{gate} comparison did not accept")
    if gate == "level_b":
        for label, document in (("native", native), ("browser", browser)):
            model_meta = document.get("model_meta")
            if not isinstance(model_meta, dict):
                failures.append(f"{gate}/{label}: model metadata is missing")
                continue
            if model_meta.get("onnx_sha256") != model_digest:
                failures.append(f"{gate}/{label}: model digest does not match the export")
            if model_meta.get("ruleset") != provenance["ruleset"]:
                failures.append(f"{gate}/{label}: model ruleset does not match the export")
            if model_meta.get("size") != provenance["size"]:
                failures.append(f"{gate}/{label}: model size does not match the export")
            if model_meta.get("weights_sha256") != provenance["source_weights_sha256"]:
                failures.append(
                    f"{gate}/{label}: source-weight fingerprint does not match the export"
                )
    return entry, failures


def _validate_direct_relay(
    documents: dict[str, Path],
    model_digest: str,
    provenance: dict,
) -> tuple[dict, list[str]]:
    import path_parity

    failures: list[str] = []
    validated: dict[str, Any] = {}
    modes_seen: set[str] = set()
    for name, path in documents.items():
        if name == "fixed":
            expected_mode = "fixed_simulations"
        elif name.startswith("fixed_time"):
            expected_mode = "fixed_time"
        else:
            failures.append(f"direct_relay evidence {name!r} has no recognized execution mode")
            continue
        document = _read_json(path)
        modes_seen.add(expected_mode)
        result = path_parity.validate_report(document)
        validated[name] = document
        if not result["ok"]:
            failures.append(f"direct_relay {name} failed independent validation")
        if document.get("gate") != "direct-relay-compact":
            failures.append(f"direct_relay {name} used the wrong gate")
        if document.get("mode") != expected_mode:
            failures.append(f"direct_relay {name} used the wrong execution mode")
        expected_rulesets = document.get("expected_rulesets")
        if (
            not isinstance(expected_rulesets, list)
            or provenance["ruleset"] not in expected_rulesets
        ):
            failures.append(f"direct_relay {name} does not cover the validated model ruleset")
        models = document.get("models")
        model_entry = next(
            (
                entry
                for entry in models or []
                if isinstance(entry, dict) and entry.get("ruleset") == provenance["ruleset"]
            ),
            None,
        )
        if model_entry is None:
            failures.append(f"direct_relay {name} lacks model coverage for the export")
        elif model_entry.get("sha256") != model_digest:
            failures.append(f"direct_relay {name} model digest does not match the export")
        elif model_entry.get("size") != provenance["size"]:
            failures.append(f"direct_relay {name} model size does not match the export")
        models_by_ruleset = {
            entry.get("ruleset"): entry
            for entry in models or []
            if isinstance(entry, dict) and isinstance(entry.get("ruleset"), str)
        }
        cases = document.get("cases")
        if not isinstance(cases, list) or not cases:
            failures.append(f"direct_relay {name} contains no cases")
            continue
        for case in cases:
            if not isinstance(case, dict):
                failures.append(f"direct_relay {name} contains a malformed case")
                break
            case_ruleset = case.get("ruleset")
            expected_model = models_by_ruleset.get(case_ruleset)
            if expected_model is None:
                failures.append(f"direct_relay {name} case has no declared model coverage")
                break
            if (
                case_ruleset == provenance["ruleset"]
                and expected_model.get("sha256") != model_digest
            ):
                failures.append(f"direct_relay {name} case model mismatch")
                break
            search = (
                ((case.get("compact") or {}).get("search") or {})
                if isinstance(case.get("compact"), dict)
                else {}
            )
            model = search.get("model") if isinstance(search, dict) else None
            if not isinstance(model, dict) or model.get("sha256") != expected_model.get("sha256"):
                failures.append(f"direct_relay {name} case model mismatch")
                break
    if modes_seen != {"fixed_simulations", "fixed_time"}:
        failures.append("direct_relay evidence must cover fixed simulations and fixed time")
    return validated, failures


def _benchmark_policy_failures(document: dict, level: str) -> list[str]:
    import mcts_benchmark

    failures: list[str] = []
    workload = document.get("workload") if isinstance(document.get("workload"), dict) else {}
    positions = [
        entry for entry in workload.get("positions") or [] if isinstance(entry, dict)
    ]
    cases = [entry for entry in workload.get("cases") or [] if isinstance(entry, dict)]
    kinds = {entry.get("kind") for entry in positions}
    simulations = {
        entry.get("budget")
        for entry in cases
        if entry.get("mode") == "fixed_simulations"
    }
    times = {
        entry.get("budget") for entry in cases if entry.get("mode") == "fixed_time"
    }
    repetitions = workload.get("repetitions")
    warmup = workload.get("warmup")
    policy = COMPETITIVE_BENCHMARK_POLICY
    if level == "competitive-trained":
        missing_kinds = policy["kinds"] - kinds
        if missing_kinds:
            failures.append(
                "competitive benchmark policy requires position kinds "
                + ", ".join(sorted(missing_kinds))
            )
        missing_simulations = policy["simulation_budgets"] - simulations
        if missing_simulations:
            failures.append(
                "competitive benchmark policy requires fixed-work budgets "
                + ", ".join(str(value) for value in sorted(missing_simulations))
            )
        missing_times = policy["time_budgets"] - times
        if missing_times:
            failures.append(
                "competitive benchmark policy requires fixed-time budgets "
                + ", ".join(str(value) for value in sorted(missing_times))
            )
        if type(repetitions) is not int or repetitions < policy["repetitions"]:
            failures.append(
                f"competitive benchmark policy requires at least {policy['repetitions']} repetitions"
            )
        if type(warmup) is not int or warmup < policy["warmup"]:
            failures.append(
                f"competitive benchmark policy requires at least {policy['warmup']} warmup run"
            )
    return failures


def _validate_benchmark_evidence(
    paths: list[Path],
    model_digest: str,
    provenance: dict,
    *,
    level: str,
) -> tuple[list[dict], list[str]]:
    import mcts_benchmark

    failures: list[str] = []
    records: list[dict] = []
    coverage: dict[str, set[str]] = {}
    documents: dict[str, list[dict]] = {"native": [], "browser": []}
    for path in paths:
        document = _read_json(path)
        side = _runtime_kind(document)
        if side is None:
            failures.append(f"benchmark evidence {path.name} has no recognized runtime")
            continue
        result = mcts_benchmark.validate_report(document, side)
        if result["failures"]:
            failures.append(
                f"benchmark evidence {path.name} failed validation: {result['failures'][0]}"
            )
            continue
        root_model = document.get("model") if isinstance(document.get("model"), dict) else {}
        if root_model.get("onnx_sha256") != model_digest:
            failures.append(
                f"benchmark evidence {path.name} model digest does not match the export"
            )
        meta = root_model.get("meta") if isinstance(root_model.get("meta"), dict) else {}
        if side == "native" and meta:
            if meta.get("weights_sha256") != provenance["source_weights_sha256"]:
                failures.append(
                    f"benchmark evidence {path.name} source-weight fingerprint mismatch"
                )
            if meta.get("ruleset") != provenance["ruleset"]:
                failures.append(f"benchmark evidence {path.name} model ruleset mismatch")
            if meta.get("size") != provenance["size"]:
                failures.append(f"benchmark evidence {path.name} model size mismatch")
        elif side == "browser":
            if root_model.get("ruleset") != provenance["ruleset"]:
                failures.append(f"benchmark evidence {path.name} model ruleset mismatch")
            if root_model.get("size") != provenance["size"]:
                failures.append(f"benchmark evidence {path.name} model size mismatch")
        failures.extend(
            f"benchmark evidence {path.name}: {message}"
            for message in _benchmark_policy_failures(document, level)
        )
        documents[side].append(document)
        modes = coverage.setdefault(side, set())
        rulesets: set[str] = set()
        for record in result["records"]:
            modes.add(record.get("mode"))
            rulesets.add(record.get("ruleset"))
            records.append({**record, "runtime": document.get("runtime")})
        if provenance["ruleset"] not in rulesets:
            failures.append(f"benchmark evidence {path.name} does not cover the model ruleset")
    if set(coverage) != {"native", "browser"}:
        failures.append("benchmark evidence must include native and browser reports")
    for side, modes in coverage.items():
        if modes != {"fixed_simulations", "fixed_time"}:
            failures.append(f"{side} benchmark evidence must cover fixed work and fixed time")
    if level != "structural-smoke" and (
        len(documents["native"]) != 1 or len(documents["browser"]) != 1
    ):
        failures.append(
            "validated benchmark evidence requires exactly one native and one browser report"
        )
    if documents["native"] and documents["browser"]:
        comparison = mcts_benchmark.compare_reports(
            documents["native"][0], documents["browser"][0]
        )
        if comparison.get("acceptance") is not True:
            failures.append(
                "native and browser benchmark workloads or effective settings are incompatible"
            )
            failures.extend(
                f"benchmark comparison: {message}"
                for message in (comparison.get("failures") or [])[:3]
            )
    return records, failures


def validate_evidence_manifest(
    checkpoint: Path,
    export_dir: Path,
    manifest_path: Path,
) -> dict:
    """Independently validate checkpoint/export and all supplied observations."""
    provenance_full = None
    try:
        provenance_full = model_provenance(export_dir, checkpoint)
    except (SystemExit, ValueError, OSError, TypeError) as error:
        message = str(error).replace(str(checkpoint), "<checkpoint>")
        return blocked_report(f"model provenance validation failed: {message}")
    try:
        manifest = _load_manifest(manifest_path)
    except ValueError as error:
        return {
            **blocked_report(str(error)),
            "status": "invalid",
            "failures": [str(error)],
            "model": provenance_full,
        }
    level = manifest["level"]
    policy = manifest["policy"]
    provenance = manifest["provenance"]
    paths = manifest["paths"]
    failures = _bind_provenance(provenance_full, provenance, level=level)
    require_independent_runtime = level != "structural-smoke"
    result = {
        "kind": "browser-competitive-validation",
        "schema": SCHEMA,
        "status": "invalid",
        "structural_ok": False,
        "acceptance": False,
        "competitive_acceptance": False,
        "independent_verification": True,
        "verification": OWNERSHIP,
        "validation_level": level,
        "acceptance_policy": policy,
        "ruleset": provenance_full["ruleset"],
        "blocked_reasons": [],
        "failures": failures,
        "model": provenance_full,
        "provenance": {
            "training_status": provenance["training_status"],
            "classification_source": (
                "owner-attestation"
                if level == "competitive-trained"
                else "structural-declaration"
            ),
            "training_facts_independently_verified": False,
            "checkpoint_sha256": provenance["checkpoint_sha256"],
            "onnx_sha256": provenance["onnx_sha256"],
            "source_weights_sha256": provenance["source_weights_sha256"],
            "checkpoint_metadata_sha256": provenance.get("checkpoint_metadata_sha256"),
            "checkpoint_metadata": provenance_full.get("checkpoint_metadata"),
            "structural_smoke_checkpoint": provenance_full.get("structural_smoke_checkpoint"),
            "ruleset": provenance["ruleset"],
            "architecture": provenance["architecture"],
            "encoder_version": provenance["encoder_version"],
        },
        "parity": {},
        "benchmarks": [],
        "playing_strength": {"status": "not-run"},
        "authoritative": False,
    }
    model_digest = provenance_full["onnx_sha256"]
    if level == "structural-smoke":
        # Structural evidence always re-validates every supplied gate; Level A is
        # mandatory and the remaining gates are optional.
        gates = [gate for gate in REQUIRED_PARITY if gate in paths]
        if "level_a" not in gates:
            failures.append("missing level_a evidence")
    else:
        gates = list(REQUIRED_PARITY)
    for gate in gates:
        if gate not in paths:
            failures.append(f"missing {gate} evidence")
            continue
        if gate in GATES:
            try:
                entry, gate_failures = _validate_parity_gate(
                    gate,
                    paths[gate],
                    model_digest,
                    provenance_full,
                    require_independent_runtime=require_independent_runtime,
                )
            except ValueError as error:
                failures.append(f"{gate}: {error}")
                continue
            failures.extend(gate_failures)
            if entry is not None:
                result["parity"][gate] = entry
            continue
        try:
            documents, gate_failures = _validate_direct_relay(
                paths[gate], model_digest, provenance_full
            )
        except ValueError as error:
            failures.append(f"{gate}: {error}")
            continue
        failures.extend(gate_failures)
        result["parity"]["direct_relay"] = {
            "model_sha256": model_digest,
            **documents,
        }
    if level != "structural-smoke":
        benchmark_paths = paths.get("benchmarks")
        if not benchmark_paths:
            failures.append("missing benchmark evidence")
        else:
            try:
                records, benchmark_failures = _validate_benchmark_evidence(
                    benchmark_paths, model_digest, provenance_full, level=level
                )
            except ValueError as error:
                failures.append(f"benchmarks: {error}")
            else:
                failures.extend(benchmark_failures)
                result["benchmarks"] = records
    elif paths.get("benchmarks"):
        try:
            records, benchmark_failures = _validate_benchmark_evidence(
                paths["benchmarks"], model_digest, provenance_full, level=level
            )
        except ValueError as error:
            failures.append(f"benchmarks: {error}")
        else:
            failures.extend(benchmark_failures)
            result["benchmarks"] = records
    result["failures"] = failures
    if failures:
        result["status"] = "invalid"
        return result
    result["structural_ok"] = True
    if level == "structural-smoke":
        result["status"] = "structural-validated"
        result["acceptance"] = False
        result["competitive_acceptance"] = False
    else:
        result["status"] = "validated"
        result["acceptance"] = True
        result["competitive_acceptance"] = level == "competitive-trained"
    return result


def _write(path: Path | None, payload: dict) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n")


def _emit(payload: dict) -> None:
    print(json.dumps(payload, indent=2))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--export-dir", type=Path)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--validate", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.report:
        if not args.checkpoint or not args.export_dir:
            report = blocked_report(
                "checkpoint, export-dir and evidence manifest are required for competitive validation"
            )
        else:
            report = validate_evidence_manifest(args.checkpoint, args.export_dir, args.report)
        _write(args.output, report)
        _emit(report)
        status = report.get("status")
        if status == "blocked":
            return 2
        if status in ("validated", "structural-validated"):
            return 0
        return 1
    if args.validate:
        try:
            report = _read_json(args.validate)
        except ValueError as error:
            result = {
                "ok": False,
                "failures": [str(error)],
                "verification": INSPECTION,
                "independent_verification": False,
                "acceptance": False,
                "competitive_acceptance": False,
            }
            _write(args.output, result)
            _emit(result)
            return 1
        result = validate_report(report)
        _write(args.output, result)
        _emit(result)
        return 0 if result["ok"] else 1
    if not args.checkpoint:
        report = blocked_report("owner-supplied competitive checkpoint is unavailable")
    elif not args.export_dir:
        report = blocked_report("validated ONNX export directory is unavailable")
    else:
        try:
            provenance = model_provenance(args.export_dir, args.checkpoint)
        except (SystemExit, ValueError, OSError, TypeError) as error:
            message = str(error).replace(str(args.checkpoint), "<checkpoint>")
            report = blocked_report(f"model provenance validation failed: {message}")
        else:
            report = {
                **blocked_report(
                    "native/browser parity and benchmark reports have not been supplied"
                ),
                "model": provenance,
                "ruleset": provenance["ruleset"],
                "blocked_reasons": [
                    "run native/browser Level A, Level B and direct/relay parity",
                    "run fixed-work and fixed-time MCTS benchmarks",
                    "run a role-balanced playing-strength tournament",
                ],
            }
    _write(args.output, report)
    _emit(report)
    # Blocked generation is not successful acceptance; use exit 2 so callers
    # cannot confuse an explicit resource gap with a validated gate.
    return 2 if report.get("status") == "blocked" else 0


if __name__ == "__main__":
    sys.exit(main())
