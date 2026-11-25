"""Bounded Node regression suite for the sustained workload driver."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

SPIKE = Path(__file__).resolve().parents[1] / "spikes/browser_offline"


def test_sustained_workload_core_and_optional_browser():
    if shutil.which("node") is None:
        pytest.skip("node is unavailable")
    result = subprocess.run(
        ["node", str(SPIKE / "test_sustained_workload.mjs")],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert '"failed": []' in result.stdout
