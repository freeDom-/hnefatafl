"""Competitive checkpoint validation is fail-closed and independently derived."""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

SPIKE = Path(__file__).resolve().parents[1] / "spikes/browser_offline"
if str(SPIKE) not in sys.path:
    sys.path.insert(0, str(SPIKE))

import competitive_validation as cv  # noqa: E402

ROOT = SPIKE.parents[1]
GENUINE = ROOT / "docs/validation"


def _model(**overrides):
    model = {
        "ruleset": "brandubh",
        "size": 7,
        "architecture": {"channels": 32, "blocks": 2},
        "encoder_version": "six-plane-v1",
        "source_weights_sha256": "a" * 64,
        "onnx_sha256": "b" * 64,
        "checkpoint_sha256": "d" * 64,
        "validated_export": True,
        "checkpoint_supplied": True,
    }
    model.update(overrides)
    return model


def _benchmarks():
    return [
        {
            "ok": True,
            "mode": "fixed_simulations",
            "completed_simulations": 1024,
            "requested_simulations": 1024,
            "provider": "wasm",
            "model_sha256": "b" * 64,
            "runtime": runtime,
            "elapsed_ms": 500,
        }
        for runtime in ("native-cpython", "browser-pyodide")
    ] + [
        {
            "ok": True,
            "mode": "fixed_time",
            "completed_simulations": 90,
            "completion_reason": "deadline",
            "elapsed_ms": 500,
            "provider": "wasm",
            "model_sha256": "b" * 64,
            "runtime": runtime,
        }
        for runtime in ("native-cpython", "browser-pyodide")
    ]


def _valid():
    return {
        "kind": "browser-competitive-validation",
        "schema": 2,
        "status": "validated",
        "acceptance": True,
        "competitive_acceptance": True,
        "validation_level": "competitive-trained",
        "ruleset": "brandubh",
        "blocked_reasons": [],
        "model": _model(),
        "parity": {
            "level_a": {
                "model_sha256": "b" * 64,
                "comparison": {
                    "level": "a",
                    "acceptance": True,
                    "execution_validity": {"ok": True},
                    "workload_completion": {"complete": True},
                    "workload": {"identity_verified": True},
                    "deterministic_parity": {"ok": True},
                    "numerical_comparison": {},
                },
            },
            "level_b": {
                "model_sha256": "b" * 64,
                "comparison": {
                    "level": "b",
                    "acceptance": True,
                    "execution_validity": {"ok": True},
                    "workload_completion": {"complete": True},
                    "workload": {"identity_verified": True},
                    "deterministic_parity": {},
                    "numerical_comparison": {"best_move_parity": True, "positions": 7},
                },
            },
            "direct_relay": {
                "model_sha256": "b" * 64,
                "fixed": {
                    "gate": "direct-relay-compact",
                    "ok": True,
                    "collector_ok": True,
                    "coverage_ok": True,
                    "cases_ok": True,
                    "cases": [{"ok": True}],
                },
                "fixed_time": {
                    "gate": "direct-relay-compact",
                    "ok": True,
                    "collector_ok": True,
                    "coverage_ok": True,
                    "cases_ok": True,
                    "cases": [{"ok": True}],
                },
            },
        },
        "benchmarks": _benchmarks(),
        "playing_strength": {"status": "not-run"},
        "authoritative": False,
    }


def test_inspected_valid_report_is_structural_inspection_only():
    result = cv.validate_report(_valid())
    assert result["ok"] is True
    assert result["verification"] == cv.INSPECTION
    assert result["independent_verification"] is False
    assert result["acceptance"] is False
    assert result["competitive_acceptance"] is False
    assert result["report_claims_competitive_acceptance"] is True


def test_structural_report_cannot_claim_competitive_acceptance():
    report = _valid()
    report["validation_level"] = "structural-smoke"
    report["status"] = "structural-validated"
    report["acceptance"] = True
    result = cv.validate_report(report)
    assert result["ok"] is False
    assert result["competitive_acceptance"] is False


def test_blocked_report_is_explicit_and_not_acceptance():
    report = cv.blocked_report("checkpoint unavailable", ruleset="brandubh")
    assert report["status"] == "blocked"
    assert report["acceptance"] is False
    assert report["competitive_acceptance"] is False
    assert cv.validate_report(report)["ok"] is True


def test_inspection_rejects_missing_gates_identity_and_benchmark_coverage():
    report = _valid()
    report["parity"].pop("direct_relay")
    assert cv.validate_report(report)["ok"] is False

    report = _valid()
    report["parity"]["level_b"]["model_sha256"] = "c" * 64
    assert cv.validate_report(report)["ok"] is False

    report = _valid()
    report["model"]["source_weights_sha256"] = "bad"
    assert cv.validate_report(report)["ok"] is False

    report = _valid()
    report["benchmarks"] = [
        record for record in report["benchmarks"] if record["runtime"].startswith("browser")
    ]
    assert cv.validate_report(report)["ok"] is False

    report = _valid()
    report["benchmarks"] = [
        record for record in report["benchmarks"] if record["mode"] == "fixed_time"
    ]
    assert cv.validate_report(report)["ok"] is False

    report = _valid()
    report["parity"]["level_b"]["comparison"]["level"] = "a"
    assert cv.validate_report(report)["ok"] is False


def test_inspection_rejects_bare_success_flags():
    report = _valid()
    report["parity"] = {
        "level_a": {"ok": True},
        "level_b": {"ok": True},
        "direct_relay": {"ok": True},
    }
    result = cv.validate_report(report)
    assert result["ok"] is False
    assert any("comparator" in failure or "evidence" in failure for failure in result["failures"])


def test_validate_cli_never_claims_verified_acceptance(tmp_path):
    source = tmp_path / "valid.json"
    source.write_text(json.dumps(_valid()))
    output = tmp_path / "validated.json"
    result = subprocess.run(
        [
            sys.executable,
            str(SPIKE / "competitive_validation.py"),
            "--validate",
            str(source),
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(output.read_text())
    assert payload["verification"] == cv.INSPECTION
    assert payload["acceptance"] is False
    assert payload["competitive_acceptance"] is False


def test_validate_cli_returns_one_for_invalid_and_malformed_files(tmp_path):
    invalid = _valid()
    invalid["benchmarks"] = []
    corrupted = tmp_path / "invalid.json"
    corrupted.write_text(json.dumps(invalid))
    result = subprocess.run(
        [sys.executable, str(SPIKE / "competitive_validation.py"), "--validate", str(corrupted)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 1
    assert "Traceback" not in result.stderr

    malformed = tmp_path / "malformed.json"
    malformed.write_text("{not json")
    result = subprocess.run(
        [sys.executable, str(SPIKE / "competitive_validation.py"), "--validate", str(malformed)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert json.loads(result.stdout)["ok"] is False


def test_blocked_cli_path_returns_two(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            str(SPIKE / "competitive_validation.py"),
            "--checkpoint",
            str(tmp_path / "missing.pt"),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 2
    assert json.loads(result.stdout)["status"] == "blocked"


def test_genuine_browser_level_b_evidence_passes_orchestration_gate():
    """Use committed genuine native/browser observations, not a synthetic summary."""
    native = GENUINE / "mcts-parity-native-b-brandubh-r4.json"
    browser = GENUINE / "mcts-parity-browser-b-brandubh-r4.json"
    provenance = {
        "ruleset": "brandubh",
        "size": 7,
        "source_weights_sha256": "028517cc28bbb6f8715fef7751c70a4dd4ea0164ef9b835aa8389b0ea17bcb48",
    }
    entry, failures = cv._validate_parity_gate(
        "level_b",
        {"native": native, "browser": browser},
        "512ae93b9900f918438510b089b59ca21701decc5149d17ab277ca67aa8e6f2c",
        provenance,
        require_independent_runtime=True,
    )
    assert failures == [], failures
    assert entry["comparison"]["acceptance"] is True
    assert entry["comparison"]["numerical_comparison"]["best_move_parity"] is True

    entry, failures = cv._validate_parity_gate(
        "level_b",
        {"native": native, "browser": native},
        "512ae93b9900f918438510b089b59ca21701decc5149d17ab277ca67aa8e6f2c",
        provenance,
        require_independent_runtime=True,
    )
    assert entry["runtime"]["independent"] is False
    assert any("browser runtime" in failure for failure in failures)
    assert any("share one runtime" in failure for failure in failures)


def test_level_gate_rejects_a_different_validation_level():
    level_a_native = GENUINE / "mcts-parity-native-a-r4.json"
    level_a_browser = GENUINE / "mcts-parity-browser-a-r4.json"
    _, failures = cv._validate_parity_gate(
        "level_b",
        {"native": level_a_native, "browser": level_a_browser},
        "b" * 64,
        {"ruleset": "brandubh", "size": 7, "source_weights_sha256": "a" * 64},
        require_independent_runtime=True,
    )
    assert any("not a Level B report" in failure for failure in failures)


# ---------------------------------------------------------------------------
# End-to-end CLI integration using a real disposable smoke checkpoint/export.
# ---------------------------------------------------------------------------


def _smoke_pipeline(tmp_path: Path) -> tuple[Path, Path, Path]:
    pytest.importorskip("onnx")
    pytest.importorskip("onnxruntime")
    checkpoint = tmp_path / "smoke.pt"
    export = tmp_path / "export"
    subprocess.run(
        [
            sys.executable,
            str(SPIKE / "smoke_checkpoint.py"),
            "--ruleset",
            "brandubh",
            "--channels",
            "4",
            "--blocks",
            "0",
            "--output",
            str(checkpoint),
        ],
        check=True,
        capture_output=True,
        timeout=180,
    )
    subprocess.run(
        [sys.executable, str(SPIKE / "onnx_export.py"), str(checkpoint), str(export)],
        check=True,
        capture_output=True,
        timeout=300,
    )
    native = tmp_path / "native-a.json"
    subprocess.run(
        [
            sys.executable,
            str(SPIKE / "native_mcts_parity.py"),
            "--level",
            "a",
            "--iterations",
            "8",
            "--limit",
            "1",
            "--output",
            str(native),
        ],
        check=True,
        capture_output=True,
        timeout=180,
    )
    return checkpoint, export, native


def _structural_manifest(export: Path, checkpoint: Path, native: Path) -> dict:
    sidecar = json.loads((export / "model.json").read_text())
    architecture = sidecar["model_config"]
    provenance = {
        "training_status": "structural-smoke",
        "checkpoint_sha256": hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
        "onnx_sha256": hashlib.sha256((export / "model.onnx").read_bytes()).hexdigest(),
        "source_weights_sha256": sidecar["source"]["weights_sha256"],
        "ruleset": sidecar["ruleset"],
        "architecture": {
            "channels": architecture["channels"],
            "blocks": architecture["blocks"],
        },
        "encoder_version": sidecar["source"]["encoder_version"],
    }
    return {
        "kind": "browser-competitive-evidence",
        "schema": 2,
        "validation_level": "structural-smoke",
        "acceptance_policy": "structural",
        "provenance": provenance,
        "evidence": {
            "level_a": {"native": native.name, "browser": native.name},
        },
    }


def test_competitive_validation_cli_integration_with_real_export(tmp_path):
    checkpoint, export, native = _smoke_pipeline(tmp_path)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(_structural_manifest(export, checkpoint, native)))
    output = tmp_path / "validation.json"
    result = subprocess.run(
        [
            sys.executable,
            str(SPIKE / "competitive_validation.py"),
            "--checkpoint",
            str(checkpoint),
            "--export-dir",
            str(export),
            "--report",
            str(manifest),
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Traceback" not in result.stderr
    assert "NameError" not in result.stdout
    report = json.loads(output.read_text())
    assert report["status"] == "structural-validated"
    assert report["structural_ok"] is True
    assert report["acceptance"] is False
    assert report["competitive_acceptance"] is False
    assert report["verification"] == cv.OWNERSHIP
    assert report["independent_verification"] is True
    assert report["validation_level"] == "structural-smoke"
    assert report["parity"]["level_a"]["comparison"]["level"] == "a"
    assert report["parity"]["level_a"]["comparison"]["deterministic_parity"]["ok"] is True
    assert report["model"]["checkpoint_supplied"] is True
    assert report["model"]["source_weights_sha256"] == report["provenance"]["source_weights_sha256"]
    assert str(checkpoint) not in output.read_text()

    # Generated reports remain compatible with the documented validation workflow.
    inspection = subprocess.run(
        [
            sys.executable,
            str(SPIKE / "competitive_validation.py"),
            "--validate",
            str(output),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert inspection.returncode == 0, inspection.stdout + inspection.stderr
    inspected = json.loads(inspection.stdout)
    assert inspected["ok"] is True
    assert inspected["acceptance"] is False

    # A structural label cannot be upgraded to competitive acceptance without
    # genuinely independent evidence and a matching competitive provenance.
    upgraded = _structural_manifest(export, checkpoint, native)
    upgraded["validation_level"] = "competitive-trained"
    upgraded["acceptance_policy"] = "competitive"
    upgraded["provenance"]["training_status"] = "competitive-trained"
    upgraded_path = tmp_path / "upgraded.json"
    upgraded_path.write_text(json.dumps(upgraded))
    result = subprocess.run(
        [
            sys.executable,
            str(SPIKE / "competitive_validation.py"),
            "--checkpoint",
            str(checkpoint),
            "--export-dir",
            str(export),
            "--report",
            str(upgraded_path),
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 1
    upgraded_report = json.loads(result.stdout)
    assert upgraded_report["status"] == "invalid"
    assert upgraded_report["acceptance"] is False
    assert upgraded_report["competitive_acceptance"] is False
    assert any("level_b" in failure for failure in upgraded_report["failures"])

    # Structural evidence still cannot use the competitive acceptance policy.
    policy_mismatch = _structural_manifest(export, checkpoint, native)
    policy_mismatch["acceptance_policy"] = "competitive"
    mismatch_path = tmp_path / "policy.json"
    mismatch_path.write_text(json.dumps(policy_mismatch))
    result = subprocess.run(
        [
            sys.executable,
            str(SPIKE / "competitive_validation.py"),
            "--checkpoint",
            str(checkpoint),
            "--export-dir",
            str(export),
            "--report",
            str(mismatch_path),
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 1
    assert json.loads(result.stdout)["status"] == "invalid"


def test_evidence_manifest_binds_provenance_and_checkpoint(tmp_path):
    checkpoint, export, native = _smoke_pipeline(tmp_path)
    manifest = _structural_manifest(export, checkpoint, native)
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    assert (
        cv.validate_evidence_manifest(checkpoint, export, path)["status"] == "structural-validated"
    )

    mutated = copy.deepcopy(manifest)
    mutated["provenance"]["source_weights_sha256"] = "c" * 64
    path.write_text(json.dumps(mutated))
    result = cv.validate_evidence_manifest(checkpoint, export, path)
    assert result["status"] == "invalid"
    assert any("source_weights_sha256" in failure for failure in result["failures"])

    mutated = copy.deepcopy(manifest)
    mutated["provenance"]["checkpoint_sha256"] = "c" * 64
    path.write_text(json.dumps(mutated))
    result = cv.validate_evidence_manifest(checkpoint, export, path)
    assert result["status"] == "invalid"
    assert any("checkpoint digest" in failure for failure in result["failures"])

    mutated = copy.deepcopy(manifest)
    mutated.pop("provenance")
    path.write_text(json.dumps(mutated))
    result = cv.validate_evidence_manifest(checkpoint, export, path)
    assert result["status"] == "invalid"
    assert any("provenance" in failure for failure in result["failures"])


def test_manifest_evidence_files_must_be_well_formed(tmp_path):
    checkpoint, export, native = _smoke_pipeline(tmp_path)
    manifest = _structural_manifest(export, checkpoint, native)
    path = tmp_path / "manifest.json"
    manifest["evidence"]["level_a"]["browser"] = "../outside.json"
    path.write_text(json.dumps(manifest))
    result = cv.validate_evidence_manifest(checkpoint, export, path)
    assert result["status"] == "invalid"
    assert any("missing" in failure for failure in result["failures"])

    manifest = _structural_manifest(export, checkpoint, native)
    broken = tmp_path / "broken.json"
    broken.write_text("{not json")
    manifest["evidence"]["level_a"]["browser"] = broken.name
    path.write_text(json.dumps(manifest))
    result = cv.validate_evidence_manifest(checkpoint, export, path)
    assert result["status"] == "invalid"
    assert any("not valid JSON" in failure for failure in result["failures"])


def test_genuine_multi_ruleset_direct_relay_evidence_is_validated():
    documents = {
        "fixed": GENUINE / "mcts-parity-direct-relay-compact-phase3b.json",
        "fixed_time": GENUINE / "mcts-parity-direct-relay-compact-time-phase3b.json",
    }
    provenance = {"ruleset": "brandubh", "size": 7}
    validated, failures = cv._validate_direct_relay(
        documents, "71cd1bf62b6da27f70bbc06c41ab7e2055b630979fab68a563328da132ea7b78", provenance
    )
    assert failures == [], failures
    assert set(validated) == {"fixed", "fixed_time"}
    assert validated["fixed"]["mode"] == "fixed_simulations"
    assert validated["fixed_time"]["mode"] == "fixed_time"

    _, failures = cv._validate_direct_relay(
        {"fixed": documents["fixed"]},
        "71cd1bf62b6da27f70bbc06c41ab7e2055b630979fab68a563328da132ea7b78",
        provenance,
    )
    assert any("fixed time" in failure for failure in failures)

    _, failures = cv._validate_direct_relay(documents, "c" * 64, provenance)
    assert any("model digest" in failure for failure in failures)


def test_genuine_native_and_browser_benchmark_evidence_is_validated():
    paths = [
        GENUINE / "mcts-benchmark-native-quick-r4.json",
        GENUINE / "mcts-benchmark-browser-quick-r4.json",
    ]
    provenance = {
        "ruleset": "brandubh",
        "size": 7,
        "source_weights_sha256": "028517cc28bbb6f8715fef7751c70a4dd4ea0164ef9b835aa8389b0ea17bcb48",
    }
    records, failures = cv._validate_benchmark_evidence(
        paths,
        "512ae93b9900f918438510b089b59ca21701decc5149d17ab277ca67aa8e6f2c",
        provenance,
    )
    assert failures == [], failures
    assert {record["runtime"] for record in records} == {"native-cpython", "browser-pyodide"}
    assert {record["mode"] for record in records} == {"fixed_simulations", "fixed_time"}

    _, failures = cv._validate_benchmark_evidence(
        [paths[0], paths[0]],
        "512ae93b9900f918438510b089b59ca21701decc5149d17ab277ca67aa8e6f2c",
        provenance,
    )
    assert any("native and browser" in failure for failure in failures)
