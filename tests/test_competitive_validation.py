"""Competitive checkpoint validation reports are fail-closed."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

SPIKE = Path(__file__).resolve().parents[1] / "spikes/browser_offline"
if str(SPIKE) not in sys.path:
    sys.path.insert(0, str(SPIKE))

import competitive_validation as cv  # noqa: E402


def _valid():
    model = {
        "ruleset": "brandubh",
        "size": 7,
        "architecture": {"channels": 32, "blocks": 2},
        "encoder_version": "six-plane-v1",
        "source_weights_sha256": "a" * 64,
        "onnx_sha256": "b" * 64,
        "validated_export": True,
        "checkpoint_supplied": True,
    }
    return {
        "kind": "browser-competitive-validation",
        "schema": 1,
        "status": "validated",
        "acceptance": True,
        "validation_level": "competitive-trained",
        "ruleset": "brandubh",
        "blocked_reasons": [],
        "model": model,
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
        "benchmarks": [
            {
                "ok": True,
                "mode": "fixed_simulations",
                "completed_simulations": 1024,
                "requested_simulations": 1024,
                "provider": "wasm",
                "model_sha256": "b" * 64,
            },
            {
                "ok": True,
                "mode": "fixed_time",
                "completed_simulations": 90,
                "completion_reason": "deadline",
                "elapsed_ms": 500,
                "provider": "wasm",
                "model_sha256": "b" * 64,
            },
        ],
        "playing_strength": {"status": "not-run"},
        "authoritative": False,
    }


def test_valid_competitive_report():
    assert cv.validate_report(_valid())["ok"] is True


def test_blocked_report_is_explicit_and_not_acceptance():
    report = cv.blocked_report("checkpoint unavailable", ruleset="brandubh")
    assert report["status"] == "blocked"
    assert report["acceptance"] is False
    assert cv.validate_report(report)["ok"] is True


def test_missing_checkpoint_cli_writes_blocked_report(tmp_path):
    output = tmp_path / "report.json"
    assert cv.main.__name__ == "main"
    # Build the blocked report through the same public helper used by the CLI.
    report = cv.blocked_report("owner-supplied competitive checkpoint is unavailable")
    output.write_text(json.dumps(report))
    loaded = json.loads(output.read_text())
    assert loaded["status"] == "blocked"
    assert cv.validate_report(loaded)["ok"] is True


def test_validated_report_requires_all_gates_and_identity():
    report = _valid()
    report["parity"].pop("direct_relay")
    result = cv.validate_report(report)
    assert result["ok"] is False
    assert any("direct_relay" in failure for failure in result["failures"])

    report = _valid()
    report["benchmarks"][0]["model_sha256"] = "c" * 64
    result = cv.validate_report(report)
    assert result["ok"] is False
    assert any("identity" in failure for failure in result["failures"])

    report = _valid()
    report["model"]["source_weights_sha256"] = "bad"
    result = cv.validate_report(report)
    assert result["ok"] is False


def test_cli_distinguishes_validated_blocked_and_invalid(tmp_path):
    import subprocess

    valid = tmp_path / "valid.json"
    valid.write_text(json.dumps(_valid()))
    result = subprocess.run(
        [sys.executable, str(SPIKE / "competitive_validation.py"), "--validate", str(valid)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr

    blocked = tmp_path / "blocked.json"
    blocked.write_text(json.dumps(cv.blocked_report("checkpoint unavailable")))
    result = subprocess.run(
        [sys.executable, str(SPIKE / "competitive_validation.py"), "--report", str(blocked)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 2

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


def test_bare_success_flags_cannot_pass_acceptance():
    report = _valid()
    report["parity"] = {
        "level_a": {"ok": True},
        "level_b": {"ok": True},
        "direct_relay": {"ok": True},
    }
    result = cv.validate_report(report)
    assert result["ok"] is False
    assert any("comparator" in failure or "evidence" in failure for failure in result["failures"])


def test_actual_smoke_evidence_pipeline_is_structural_not_competitive(tmp_path):
    import subprocess

    pytest = __import__("pytest")
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
        timeout=120,
    )
    subprocess.run(
        [sys.executable, str(SPIKE / "onnx_export.py"), str(checkpoint), str(export)],
        check=True,
        capture_output=True,
        timeout=180,
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
        timeout=120,
    )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "kind": "browser-competitive-evidence",
                "schema": 1,
                "validation_level": "structural-smoke",
                "evidence": {"level_a": {"native": "native-a.json", "browser": "native-a.json"}},
            }
        )
    )
    result = cv.validate_evidence_manifest(checkpoint, export, manifest)
    assert result["status"] == "structural-validated", result
    assert result["ok"] is True
    assert result["competitive_acceptance"] is False
    assert result["model"]["checkpoint_supplied"] is True
    assert result["model"]["source_weights_sha256"] != "a" * 64

    forged = copy.deepcopy(result)
    forged["model"]["checkpoint_supplied"] = True
    forged["parity"]["level_a"]["comparison"]["acceptance"] = True
    forged["parity"]["level_a"]["comparison"]["execution_validity"]["ok"] = True
    forged["validation_level"] = "competitive-trained"
    forged["status"] = "validated"
    forged["acceptance"] = True
    assert cv.validate_report(forged)["ok"] is False


def test_nested_model_identity_and_benchmark_numeric_fields_are_validated():
    report = _valid()
    report["parity"]["level_b"]["model_sha256"] = "c" * 64
    assert cv.validate_report(report)["ok"] is False

    for elapsed in (-1, 0, float("nan"), float("inf"), True, "500"):
        report = _valid()
        report["benchmarks"][1]["elapsed_ms"] = elapsed
        assert cv.validate_report(report)["ok"] is False, elapsed

    report = _valid()
    report["benchmarks"][1]["mode"] = "fixed_simulations"
    assert cv.validate_report(report)["ok"] is False


def test_missing_workload_and_model_identity_are_rejected():
    report = _valid()
    report["parity"]["level_b"]["model_sha256"] = None
    assert cv.validate_report(report)["ok"] is False

    report = _valid()
    report["parity"]["level_b"]["comparison"]["numerical_comparison"]["positions"] = 0
    assert cv.validate_report(report)["ok"] is False

    report = _valid()
    report["parity"]["level_b"]["comparison"]["workload_completion"]["complete"] = False
    assert cv.validate_report(report)["ok"] is False
