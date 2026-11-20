"""Opt-in browser gates for browser-side neural MCTS.

These need a staged experimental directory (``SPIKE_NEURAL_DIR``, produced by
``prepare_neural.py``) and Chromium, so ordinary CI skips them. The deterministic
search-driver and comparison logic is covered by ``test_mcts_driver.py`` and
``test_mcts_benchmark.py``.
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


def _run(script: str, *args: str, timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(
        [NODE, str(SPIKE / script), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


pytestmark = [
    pytest.mark.skipif(NODE is None, reason="node is not installed"),
    pytest.mark.skipif(not NEURAL_DIR, reason="SPIKE_NEURAL_DIR is not set"),
]


def test_analysis_ui_jarl_and_neural_mcts(tmp_path):
    output = tmp_path / "mcts-ui.json"
    result = _run("test_mcts_ui.mjs", NEURAL_DIR, "--output", str(output))
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(output.read_text())
    assert report["ok"] is True
    assert len(report["checks"]) == 9
    assert all(check["ok"] for check in report["checks"])


def test_browser_level_a_parity_is_exact(tmp_path):
    native = tmp_path / "native-a.json"
    result = subprocess.run(
        [
            str(Path(".venv/bin/python").resolve()),
            str(SPIKE / "native_mcts_parity.py"),
            "--level",
            "a",
            "--iterations",
            "32",
            "--limit",
            "1",
            "--output",
            str(native),
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    output = tmp_path / "browser-a.json"
    result = _run(
        "mcts_parity.mjs",
        NEURAL_DIR,
        "--native",
        str(native),
        "--level",
        "a",
        "--output",
        str(output),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(output.read_text())
    assert report["ok"] is True
    assert report["comparison"]["parity"] is True
    assert report["comparison"]["exact_cases"] == report["comparison"]["positions"]
