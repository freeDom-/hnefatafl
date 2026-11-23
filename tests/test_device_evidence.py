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
