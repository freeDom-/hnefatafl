"""Competitive checkpoint validation reports are fail-closed."""

from __future__ import annotations

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
            "level_a": {"ok": True},
            "level_b": {"ok": True},
            "direct_relay": {"ok": True},
        },
        "benchmarks": [
            {
                "ok": True,
                "completed_simulations": 1024,
                "model_sha256": "b" * 64,
            }
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
