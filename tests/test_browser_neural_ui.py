"""Actual-browser UI interaction tests for the standalone neural experiment.

These run only when a staged neural directory is supplied, because they need the
pinned ORT Web assets and Chromium. Set ``SPIKE_NEURAL_DIR`` to the output of
``prepare_neural.py``; ordinary CI skips them rather than downloading a runtime.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

SPIKE = Path(__file__).resolve().parents[1] / "spikes/browser_offline"
NODE = shutil.which("node")
NEURAL_DIR = os.environ.get("SPIKE_NEURAL_DIR")


@pytest.mark.skipif(NODE is None, reason="node is not installed")
@pytest.mark.skipif(not NEURAL_DIR, reason="SPIKE_NEURAL_DIR is not set")
def test_standalone_neural_ui_from_clean_state(tmp_path):
    result = subprocess.run(
        [
            NODE,
            str(SPIKE / "test_neural_ui.mjs"),
            NEURAL_DIR,
            "--output",
            str(tmp_path / "ui.json"),
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((tmp_path / "ui.json").read_text())
    assert report["ok"] is True
    assert len(report["checks"]) == 21
    assert all(check["ok"] for check in report["checks"])
