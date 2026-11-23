"""Run the deterministic browser-client lifecycle checks under Node.

The real worker is exercised by the opt-in browser verifiers. This test uses a
fake Worker to deterministically prove the generation guards, timer cleanup,
cancellation, download admission and provider reporting that were fixed in
response to review. It skips when Node is unavailable.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

SPIKE = Path(__file__).resolve().parents[1] / "spikes/browser_offline"
NODE = shutil.which("node")


def _node_version() -> tuple[int, int]:
    result = subprocess.run([NODE, "--version"], capture_output=True, text=True, check=True)
    major, minor = result.stdout.strip().lstrip("v").split(".")[:2]
    return int(major), int(minor)


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_neural_and_jarl_lifecycle_unit_checks():
    if _node_version() < (18, 0):
        pytest.skip("node >= 18 is required for the client unit checks")
    result = subprocess.run(
        [NODE, str(SPIKE / "test_browser_lifecycle.mjs")],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "57/57 lifecycle checks passed" in result.stdout


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_provider_placement_never_reports_wasm_as_gpu():
    if _node_version() < (18, 0):
        pytest.skip("node >= 18 is required")
    script = (
        "const p=require(process.argv[1]);"
        "console.log(JSON.stringify(["
        "p.placementFor('wasm',null),"
        "p.placementFor('webgpu',null),"
        "p.placementFor('webgpu',['webgpu']),"
        "p.placementFor('webgpu',['wasm'])]))"
    )
    result = subprocess.run(
        [NODE, "-e", script, str(SPIKE / "neural-provider.js")],
        capture_output=True,
        text=True,
        check=True,
    )
    import json

    placements = json.loads(result.stdout)
    assert placements == [
        "wasm",
        "webgpu-session-unverified",
        "webgpu-session-unverified",
        "cpu-fallback",
    ]
