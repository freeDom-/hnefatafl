"""Cross-validator malformed-input handling for the Phase 3C-A platform.

Every public validator and CLI must return structured diagnostics instead of
raising on malformed nested containers, identifiers or numeric values.
"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

SPIKE = Path(__file__).resolve().parents[1] / "spikes/browser_offline"
if str(SPIKE) not in sys.path:
    sys.path.insert(0, str(SPIKE))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import competitive_validation as cv  # noqa: E402
import device_evidence as de  # noqa: E402
import memory_validation as mv  # noqa: E402
import tournament as tr  # noqa: E402
from test_competitive_validation import _valid as competitive_report  # noqa: E402
from test_device_evidence import _matrix as device_report  # noqa: E402
from test_memory_validation import _report as memory_report  # noqa: E402
from test_tournament import _report as tournament_report  # noqa: E402

WEIRD = (None, [], {}, "x", 1, True, 1.5, [1], {"a": 1}, float("nan"), -1)


def _set_path(value, path, replacement):
    if not path:
        return replacement
    target = copy.deepcopy(value)
    cursor = target
    for key in path[:-1]:
        cursor = cursor[key]
    cursor[path[-1]] = replacement
    return target


def _paths(value, prefix=()):
    yield prefix
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _paths(child, prefix + (key,))
    elif isinstance(value, list):
        for index, child in enumerate(value[:3]):
            yield from _paths(child, prefix + (index,))


@pytest.mark.parametrize(
    "validator,base",
    [
        (cv.validate_report, competitive_report()),
        (mv.validate_report, memory_report()),
        (de.validate_report, device_report()),
        (tr.validate_report, tournament_report()),
    ],
)
def test_nested_mutation_never_raises(validator, base):
    for path in list(_paths(base)):
        for replacement in WEIRD:
            candidate = _set_path(base, path, replacement)
            result = validator(candidate)
            assert isinstance(result, dict)
            assert "ok" in result


def test_identifiers_are_type_checked_before_use():
    report = tournament_report()
    report["games"][0]["game_id"] = {}
    result = tr.validate_report(report)
    assert result["ok"] is False
    assert any("game identifier" in failure for failure in result["failures"])

    report = memory_report()
    report["profile"] = ["sustained-10m"]
    assert mv.validate_report(report)["ok"] is False

    report = device_report()
    report["records"][0]["provider_requested"] = ["wasm"]
    assert de.validate_report(report)["structural_ok"] is False

    report = competitive_report()
    report["parity"]["level_b"]["comparison"]["numerical_comparison"] = "positions"
    assert cv.validate_report(report)["ok"] is False


def test_boolean_and_nonfinite_measurements_are_rejected_consistently():
    report = memory_report()
    report["samples"][3]["completed_requests"] = True
    assert mv.validate_report(report)["ok"] is False

    report = tournament_report()
    report["settings"]["seed"] = True
    assert tr.validate_report(report)["ok"] is False

    report = competitive_report()
    report["parity"]["level_b"]["comparison"]["numerical_comparison"]["positions"] = float("inf")
    assert cv.validate_report(report)["ok"] is False


def test_malformed_tournament_cli_file_is_structured(tmp_path):
    source = tmp_path / "report.json"
    source.write_text("[1, 2, 3]")
    result = subprocess.run(
        [sys.executable, str(SPIKE / "tournament.py"), str(source)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert json.loads(result.stdout)["ok"] is False

    source.write_text("{not json")
    result = subprocess.run(
        [sys.executable, str(SPIKE / "tournament.py"), str(source)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 1
    assert "Traceback" not in result.stderr


def test_malformed_evidence_manifest_is_structured(tmp_path):
    export = tmp_path / "export"
    export.mkdir()
    (export / "model.onnx").write_bytes(b"not a model")
    checkpoint = tmp_path / "checkpoint.pt"
    checkpoint.write_bytes(b"not a checkpoint")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"kind": "browser-competitive-evidence"}))
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
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 2
    assert "Traceback" not in result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "blocked"
    assert str(checkpoint) not in result.stdout
    assert "model" in payload["blocked_reasons"][0]
