"""Malformed evidence must produce structured failures, never exceptions."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for path in (str(ROOT), str(ROOT / "spikes/browser_offline"), str(ROOT / "tests")):
    if path not in sys.path:
        sys.path.insert(0, path)

import competitive_validation as cv  # noqa: E402
import device_evidence as de  # noqa: E402
import memory_validation as mv  # noqa: E402
import tournament  # noqa: E402
from test_competitive_validation import _valid as competitive_report  # noqa: E402
from test_device_evidence import _measured as device_report  # noqa: E402
from test_memory_validation import _report as memory_report  # noqa: E402
from test_tournament import _report as tournament_report  # noqa: E402


@pytest.mark.parametrize(
    "path,value",
    [
        (("parity",), []),
        (("model",), "bad"),
        (("benchmarks",), {}),
        (("benchmarks", 0), []),
        (("playing_strength",), []),
        (("model", "onnx_sha256"), "z" * 64),
    ],
)
def test_competitive_malformed_fields_fail_safely(path, value):
    report = competitive_report()
    target = report
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    result = cv.validate_report(report)
    assert result["ok"] is False
    assert result["failures"]


@pytest.mark.parametrize(
    "path,value",
    [
        (("settings",), []),
        (("agents",), []),
        (("summary",), []),
        (("games", 0, "opening_state"), []),
        (("games", 0, "moves"), "bad"),
        (("openings",), [None]),
    ],
)
def test_tournament_malformed_fields_fail_safely(path, value):
    report = tournament_report()
    target = report
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    result = tournament.validate_report(report)
    assert result["ok"] is False
    assert result["failures"]


@pytest.mark.parametrize(
    "path,value",
    [
        (("budgets",), []),
        (("samples",), {}),
        (("samples", 0), []),
        (("samples", 0, "timestamp_ms"), "bad"),
        (("warmup_samples",), True),
        (("duration_ms",), "600000"),
    ],
)
def test_memory_malformed_fields_fail_safely(path, value):
    report = memory_report()
    target = report
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    result = mv.validate_report(report)
    assert result["ok"] is False


@pytest.mark.parametrize(
    "path,value",
    [
        (("records",), {}),
        (("records", 0), []),
        (("records", 0, "model_sha256"), "z" * 64),
        (("records", 0, "ram_bytes"), True),
        (("required_platforms",), [None]),
    ],
)
def test_device_malformed_fields_fail_safely(path, value):
    report = device_report()
    target = report
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    result = de.validate_report(report)
    assert result["ok"] is False
