"""Mandatory staged-source integrity contract for the search worker."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

SPIKE = Path(__file__).resolve().parents[1] / "spikes/browser_offline"
NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_search_asset_integrity_checks():
    result = subprocess.run(
        [NODE, str(SPIKE / "test_search_assets.mjs")],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "21/21 asset checks passed" in result.stdout
