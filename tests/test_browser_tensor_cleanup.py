"""Deterministic output-tensor cleanup tests for the neural worker helpers.

The real worker uses importScripts/ORT and cannot be imported into Node, so the
validation/extraction/disposal helpers live in ``neural-tensors.js`` and are
exercised with instrumented tensor objects. Skips when Node is unavailable.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

SPIKE = Path(__file__).resolve().parents[1] / "spikes/browser_offline"
NODE = shutil.which("node")


def _node_major() -> int:
    result = subprocess.run([NODE, "--version"], capture_output=True, text=True, check=True)
    return int(result.stdout.strip().lstrip("v").split(".")[0])


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_output_tensors_are_released_on_every_failure_path():
    if _node_major() < 18:
        pytest.skip("node >= 18 is required for the tensor-cleanup checks")
    result = subprocess.run(
        [NODE, str(SPIKE / "test_neural_tensors.mjs")],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "7/7 tensor-cleanup checks passed" in result.stdout
