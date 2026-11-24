"""Actual-browser direct MessageChannel ownership and cancellation."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

SPIKE = Path(__file__).resolve().parents[1] / "spikes/browser_offline"
NODE = shutil.which("node")
STAGE = os.environ.get("SPIKE_NEURAL_DIR")


@pytest.mark.skipif(NODE is None, reason="node is not installed")
@pytest.mark.skipif(not STAGE, reason="SPIKE_NEURAL_DIR is not set")
def test_second_direct_search_is_rejected_and_replacement_recovers(tmp_path):
    output = tmp_path / "direct.json"
    result = subprocess.run(
        [
            NODE,
            str(SPIKE / "test_direct_ownership.mjs"),
            STAGE,
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(output.read_text())
    assert report["ok"] is True, report["failures"]
    assert report["checks"][0]["secondError"]["code"] == "DIRECT_BUSY"
    assert report["checks"][0]["replacement_completed"] == 64
