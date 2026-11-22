"""Actual-browser independent-search inference batching."""

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
def test_independent_searches_share_batched_inference(tmp_path):
    output = tmp_path / "batching.json"
    result = subprocess.run(
        [
            NODE,
            str(SPIKE / "test_independent_batching.mjs"),
            STAGE,
            "--provider",
            "wasm",
            "--simulations",
            "32",
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
    assert report["batching"]["batches"] >= 1
    assert report["batching"]["observed_max_batch"] >= 2
    assert all(entry["completed"] == 32 for entry in report["results"])
