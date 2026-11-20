"""Request-side model identity contract tests for the neural experiment.

Pure Node checks for the shared identity module; no browser or ORT runtime is
required. Skips when Node is unavailable.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

SPIKE = Path(__file__).resolve().parents[1] / "spikes/browser_offline"
NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_request_model_identity_contract():
    result = subprocess.run(
        [NODE, str(SPIKE / "test_neural_identity.mjs")],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "7/7 identity checks passed" in result.stdout
