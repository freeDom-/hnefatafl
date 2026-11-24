"""Physical-device/WebGPU evidence: matrix uniqueness and provider acceptance."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

SPIKE = Path(__file__).resolve().parents[1] / "spikes/browser_offline"
if str(SPIKE) not in sys.path:
    sys.path.insert(0, str(SPIKE))

import device_evidence as de  # noqa: E402

MODEL = "a" * 64
ONNX = "b" * 64


def _gpu_evidence(**overrides):
    evidence = {
        "evidence_source": "ort-web-per-operator-v1",
        "operator_inventory_source": "runtime-instrumentation",
        "placement_source": "runtime-instrumentation",
        "graph_optimization": {"level": "extended", "fusion_applied": True},
        "all_operators_covered": True,
        "gpu_execution_verified": True,
        "operator_inventory": ["Conv", "MatMul", "Relu"],
        "session": {
            "model_sha256": MODEL,
            "onnx_sha256": ONNX,
            "runtime": "ort-web-1.22",
            "browser_version": "1.0",
            "workload": "brandubh-opening",
            "architecture": "brandubh-32x2",
            "session_id": "session-1",
            "provider_requested": "webgpu",
        },
        "per_operator_placement": [
            {"operator": "Conv", "provider": "webgpu"},
            {"operator": "MatMul", "provider": "webgpu"},
            {"operator": "Relu", "provider": "webgpu"},
        ],
    }
    evidence.update(overrides)
    return evidence


def _record(platform, provider, **overrides):
    placement = "wasm" if provider == "wasm" else "webgpu-session-unverified"
    record = {
        "platform": platform,
        "status": "measured",
        "browser_version": "1.0",
        "os": "TestOS",
        "cpu": "Test CPU",
        "ram_bytes": 8 * 1024**3,
        "runtime": "ort-web-1.22",
        "device_origin": "physical",
        "session_id": "session-1",
        "architecture": "brandubh-32x2",
        "model_sha256": MODEL,
        "onnx_sha256": ONNX,
        "workload": "brandubh-opening",
        "mcts_budget": 128,
        "cancellation_recovery": True,
        "recovery_evidence": {
            "cancel_acknowledged": True,
            "restart_completed": True,
            "post_cancel_inference": True,
            "cancel_requested_at_ms": 1000,
            "cancel_acknowledged_at_ms": 1050,
            "restart_completed_at_ms": 1800,
            "post_cancel_inference_at_ms": 2200,
        },
        "provider_requested": provider,
        "execution_placement": placement,
        "gpu_execution_verified": False,
        "cold_init_ms": 100,
        "warm_init_ms": 10,
        "first_inference_ms": 50,
        "warm_inference_ms": 5,
        "mcts_simulations_per_second": 100,
    }
    record.update(overrides)
    return record


def _matrix(**webgpu_overrides):
    records = []
    for platform, provider in sorted(de.REQUIRED_MATRIX):
        overrides = webgpu_overrides if provider == "webgpu" else {}
        records.append(_record(platform, provider, **overrides))
    return {
        "kind": "browser-device-evidence",
        "schema": 2,
        "acceptance": False,
        "required_platforms": sorted({platform for platform, _ in de.REQUIRED_MATRIX}),
        "records": records,
    }


def _failures(report):
    return de.validate_report(report)["failures"]


def test_blocked_device_records_are_explicit():
    report = {
        "kind": "browser-device-evidence",
        "schema": 2,
        "records": [
            {
                "platform": platform,
                "provider_requested": provider,
                "status": "blocked",
                "blocked_reason": "no physical device",
            }
            for platform, provider in sorted(de.REQUIRED_MATRIX)
        ],
    }
    result = de.validate_report(report)
    assert result["structural_ok"] is True
    assert result["measurement_acceptance"] is False
    assert result["acceptance"] is False


def test_complete_measured_matrix_remains_unverified_for_gpu_execution():
    report = _matrix()
    result = de.validate_report(report)
    assert result["structural_ok"] is True, result["failures"]
    assert result["measurement_acceptance"] is True
    assert result["webgpu_session_acceptance"] is True
    assert result["verified_gpu_acceptance"] is False
    assert result["acceptance"] is False
    assert result["verified_gpu_blocked_reason"]


def test_complete_matrix_with_cpu_fallback_cannot_satisfy_webgpu():
    report = _matrix()
    for record in report["records"]:
        if record["provider_requested"] == "webgpu":
            record["execution_placement"] = "cpu-fallback"
    result = de.validate_report(report)
    assert result["structural_ok"] is True
    assert result["webgpu_session_acceptance"] is False
    assert result["measurement_acceptance"] is False
    assert result["acceptance"] is False


def test_verified_gpu_requires_supported_runtime_instrumentation():
    record = _record(
        "windows-chrome",
        "webgpu",
        execution_placement="webgpu-session-verified",
        gpu_execution_verified=True,
        gpu_evidence=_gpu_evidence(),
    )
    failures, gate = de._record_failures(record, 0)
    assert any("pinned runtime" in failure for failure in failures)
    assert gate["verified_gpu"] is False

    # Even a future registered source cannot turn manually supplied matching lists
    # into acceptance unless the runtime contract is registered by code.
    de.register_gpu_evidence_source("ort-web-per-operator-v1", "ort-web")
    try:
        failures, gate = de._record_failures(record, 0)
        assert failures == [], failures
        assert gate["verified_gpu"] is True
        incomplete = copy.deepcopy(record)
        incomplete["gpu_evidence"]["per_operator_placement"] = [
            {"operator": "Conv", "provider": "webgpu"}
        ]
        failures, gate = de._record_failures(incomplete, 0)
        assert any("does not cover the inventory" in failure for failure in failures)
        assert gate["verified_gpu"] is False
    finally:
        de.SUPPORTED_GPU_EVIDENCE_SOURCES.clear()


def test_webgpu_verified_claim_without_evidence_is_rejected():
    report = _matrix()
    report["records"][0] = _record(
        "windows-chrome",
        "webgpu",
        execution_placement="webgpu-session-verified",
        gpu_execution_verified=True,
    )
    result = de.validate_report(report)
    assert result["structural_ok"] is False
    assert any(
        "gpu_evidence" in failure or "GPU evidence" in failure for failure in result["failures"]
    )


def test_contradictory_provider_and_placement_combinations_are_rejected():
    cases = [
        {"provider_requested": "wasm", "execution_placement": "webgpu-session-unverified"},
        {
            "provider_requested": "wasm",
            "execution_placement": "wasm",
            "gpu_execution_verified": True,
        },
        {
            "provider_requested": "webgpu",
            "execution_placement": "webgpu-mixed",
            "gpu_execution_verified": True,
        },
        {
            "provider_requested": "webgpu",
            "execution_placement": "cpu-fallback",
            "gpu_execution_verified": True,
        },
        {"provider_requested": "webgpu", "execution_placement": "wasm"},
    ]
    for case in cases:
        record = _record("linux-chromium", case.pop("provider_requested"), **case)
        failures, _ = de._record_failures(record, 0)
        assert failures, case


def test_mixed_webgpu_cannot_satisfy_verified_gpu_gate():
    record = _record(
        "windows-chrome",
        "webgpu",
        execution_placement="webgpu-mixed",
        gpu_execution_verified=False,
    )
    failures, gate = de._record_failures(record, 0)
    assert failures == []
    assert gate["webgpu_session"] is True
    assert gate["verified_gpu"] is False


def test_session_model_runtime_workload_and_architecture_identity_are_verified():
    record = _record(
        "windows-chrome",
        "webgpu",
        execution_placement="webgpu-session-verified",
        gpu_execution_verified=True,
        gpu_evidence=_gpu_evidence(),
    )
    for field, value in (
        ("model_sha256", "c" * 64),
        ("onnx_sha256", "c" * 64),
        ("runtime", "ort-web-other"),
        ("workload", "other-workload"),
        ("architecture", "brandubh-4x0"),
    ):
        mutated = copy.deepcopy(record)
        mutated["gpu_evidence"]["session"][field] = value
        failures, _ = de._record_failures(mutated, 0)
        assert any(field in failure for failure in failures), field

    mutated = copy.deepcopy(record)
    mutated["gpu_evidence"]["session"]["provider_requested"] = "wasm"
    failures, _ = de._record_failures(mutated, 0)
    assert any("did not request WebGPU" in failure for failure in failures)


def test_cancellation_recovery_evidence_is_required():
    record = _record(
        "windows-chrome",
        "webgpu",
        recovery_evidence={
            "cancel_acknowledged": True,
            "restart_completed": False,
            "post_cancel_inference": True,
        },
    )
    failures, _ = de._record_failures(record, 0)
    assert any("cancellation/recovery" in failure for failure in failures)

    record = _record("windows-chrome", "webgpu")
    record.pop("recovery_evidence")
    failures, _ = de._record_failures(record, 0)
    assert any("cancellation/recovery" in failure for failure in failures)


def test_duplicate_matrix_entries_are_rejected():
    report = _matrix()
    report["records"].append(copy.deepcopy(report["records"][0]))
    assert any("duplicates" in failure for failure in _failures(report))


def test_optional_additional_devices_are_allowed_but_validated():
    report = _matrix()
    report["records"].append(_record("other", "wasm"))
    assert de.validate_report(report)["structural_ok"] is True

    report["records"].append(_record("other", "wasm", cpu=""))
    assert de.validate_report(report)["structural_ok"] is False


def test_required_platform_and_acceptance_claims_are_enforced():
    report = _matrix()
    report["records"] = [
        record
        for record in report["records"]
        if not (record["platform"] == "android-chrome" and record["provider_requested"] == "wasm")
    ]
    result = de.validate_report(report)
    assert result["structural_ok"] is False
    report["records"].append(
        {
            "platform": "android-chrome",
            "provider_requested": "wasm",
            "status": "blocked",
            "blocked_reason": "no device",
        }
    )
    result = de.validate_report(report)
    assert result["structural_ok"] is True
    assert result["measurement_acceptance"] is False
    report["acceptance"] = True
    assert de.validate_report(report)["acceptance"] is False


def test_negative_or_nonfinite_measurements_are_rejected():
    for field, value in (
        ("mcts_simulations_per_second", -1),
        ("cold_init_ms", float("nan")),
        ("warm_inference_ms", 0),
        ("ram_bytes", True),
        ("mcts_budget", True),
    ):
        record = _record("linux-chromium", "wasm", **{field: value})
        failures, _ = de._record_failures(record, 0)
        assert failures, field


def test_device_cli_distinguishes_invalid_and_blocked_acceptance(tmp_path):
    report = _matrix()
    path = tmp_path / "matrix.json"
    path.write_text(json.dumps(report))
    result = subprocess.run(
        [sys.executable, str(SPIKE / "device_evidence.py"), str(path)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 2
    assert json.loads(result.stdout)["structural_ok"] is True
    assert json.loads(result.stdout)["acceptance"] is False

    report["records"][0]["platform"] = "not-a-platform"
    path.write_text(json.dumps(report))
    result = subprocess.run(
        [sys.executable, str(SPIKE / "device_evidence.py"), str(path)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 1

    path.write_text("{not json")
    result = subprocess.run(
        [sys.executable, str(SPIKE / "device_evidence.py"), str(path)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 1
    assert "Traceback" not in result.stderr


def test_emulated_devices_cannot_satisfy_physical_acceptance():
    report = _matrix()
    for record in report["records"]:
        record["device_origin"] = "emulated"
    result = de.validate_report(report)
    assert result["structural_ok"] is True
    assert result["measurement_acceptance"] is True
    assert result["physical_device_acceptance"] is False
    assert result["acceptance"] is False
    assert result["emulated_records"] == len(de.REQUIRED_MATRIX)


def test_verified_gpu_claims_require_observed_cancellation_timestamps():
    record = _record(
        "windows-chrome",
        "webgpu",
        execution_placement="webgpu-session-verified",
        gpu_execution_verified=True,
        gpu_evidence=_gpu_evidence(),
    )
    record["recovery_evidence"].pop("restart_completed_at_ms")
    failures, _ = de._record_failures(record, 0)
    assert any("observed timestamps" in failure for failure in failures)

    record["recovery_evidence"]["restart_completed_at_ms"] = 900
    failures, _ = de._record_failures(record, 0)
    assert any("not ordered" in failure for failure in failures)
