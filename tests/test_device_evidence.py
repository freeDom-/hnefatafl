"""Physical-device/WebGPU evidence validation."""

from __future__ import annotations

import sys
from pathlib import Path

SPIKE = Path(__file__).resolve().parents[1] / "spikes/browser_offline"
if str(SPIKE) not in sys.path:
    sys.path.insert(0, str(SPIKE))

import device_evidence as de  # noqa: E402


def test_blocked_device_records_are_explicit():
    report = {
        "kind": "browser-device-evidence",
        "schema": 1,
        "records": [
            {
                "platform": "android-chrome",
                "status": "blocked",
                "blocked_reason": "no physical device",
            }
        ],
    }
    assert de.validate_report(report)["ok"] is True


def test_webgpu_cannot_be_claimed_as_verified_without_evidence():
    report = {
        "kind": "browser-device-evidence",
        "schema": 1,
        "records": [
            {
                "platform": "windows-chrome",
                "status": "measured",
                "browser_version": "x",
                "os": "Windows 11",
                "cpu": "test",
                "ram_bytes": 1,
                "provider_requested": "webgpu",
                "execution_placement": "webgpu-session-verified",
                "gpu_execution_verified": False,
                "cold_init_ms": 1,
                "warm_init_ms": 1,
                "mcts_simulations_per_second": 1,
            }
        ],
    }
    result = de.validate_report(report)
    assert result["ok"] is False
    assert any("verified GPU" in failure for failure in result["failures"])


def test_webgpu_wasm_fallback_is_not_accepted_as_gpu():
    report = {
        "kind": "browser-device-evidence",
        "schema": 1,
        "records": [
            {
                "platform": "linux-chromium",
                "status": "measured",
                "browser_version": "x",
                "os": "Linux",
                "cpu": "test",
                "ram_bytes": 1,
                "provider_requested": "webgpu",
                "execution_placement": "wasm",
                "gpu_execution_verified": False,
                "cold_init_ms": 1,
                "warm_init_ms": 1,
                "mcts_simulations_per_second": 1,
            }
        ],
    }
    assert de.validate_report(report)["ok"] is False


def _measured(**overrides):
    record = {
        "platform": "linux-chromium",
        "status": "measured",
        "browser_version": "1",
        "os": "Linux",
        "cpu": "CPU",
        "ram_bytes": 8 * 1024**3,
        "architecture": "brandubh-32x2",
        "model_sha256": "a" * 64,
        "onnx_sha256": "b" * 64,
        "workload": "brandubh-opening",
        "provider_requested": "wasm",
        "execution_placement": "wasm",
        "gpu_execution_verified": False,
        "cold_init_ms": 100,
        "warm_init_ms": 10,
        "first_inference_ms": 50,
        "warm_inference_ms": 5,
        "mcts_simulations_per_second": 100,
    }
    record.update(overrides)
    return {
        "kind": "browser-device-evidence",
        "schema": 1,
        "acceptance": False,
        "required_platforms": ["linux-chromium"],
        "records": [record],
    }


def test_complete_measured_device_evidence_is_structurally_valid():
    assert de.validate_report(_measured())["ok"] is True


def test_gpu_verified_requires_per_operator_evidence():
    report = _measured(
        provider_requested="webgpu",
        execution_placement="webgpu-session-verified",
        gpu_execution_verified=True,
    )
    assert de.validate_report(report)["ok"] is False
    report["records"][0]["gpu_evidence"] = {
        "gpu_execution_verified": True,
        "per_operator_placement": ["webgpu"],
    }
    assert de.validate_report(report)["ok"] is True


def test_negative_or_nonfinite_measurements_are_rejected():
    for field, value in (
        ("mcts_simulations_per_second", -1),
        ("cold_init_ms", float("nan")),
        ("warm_inference_ms", 0),
    ):
        assert de.validate_report(_measured(**{field: value}))["ok"] is False


def test_required_platform_and_acceptance_blocked_are_enforced():
    report = _measured()
    report["required_platforms"].append("android-chrome")
    assert de.validate_report(report)["ok"] is False
    report["records"].append(
        {"platform": "android-chrome", "status": "blocked", "blocked_reason": "no device"}
    )
    assert de.validate_report(report)["ok"] is True
    report["acceptance"] = True
    assert de.validate_report(report)["ok"] is False
