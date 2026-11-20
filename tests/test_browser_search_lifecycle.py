"""Run the deterministic browser neural-MCTS coordinator checks under Node."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

SPIKE = Path(__file__).resolve().parents[1] / "spikes/browser_offline"
NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_search_coordinator_lifecycle_checks():
    result = subprocess.run(
        [NODE, str(SPIKE / "test_search_lifecycle.mjs")],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "23/23 search checks passed" in result.stdout
