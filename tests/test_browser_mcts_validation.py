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
import sys
from pathlib import Path

import pytest

SPIKE = Path(__file__).resolve().parents[1] / "spikes/browser_offline"
if str(SPIKE) not in sys.path:
    sys.path.insert(0, str(SPIKE))
import mcts_benchmark  # noqa: E402

NODE = shutil.which("node")
NEURAL_DIR = os.environ.get("SPIKE_NEURAL_DIR")


def _run(script: str, *args: str, timeout: int = 900) -> subprocess.CompletedProcess:
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
    assert len(report["checks"]) == 32
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
    comparison = report["comparison"]
    assert comparison["acceptance"] is True
    assert comparison["execution_validity"]["ok"] is True
    assert comparison["deterministic_parity"]["ok"] is True
    assert (
        comparison["deterministic_parity"]["exact_cases"]
        == comparison["deterministic_parity"]["positions"]
    )
    assert comparison["workload_completion"]["complete"] is True


def test_extended_native_browser_benchmark_workloads_are_compatible(tmp_path):
    model_export = os.environ.get("SPIKE_MODEL_EXPORT")
    if not model_export or not Path(model_export).is_dir():
        pytest.skip("SPIKE_MODEL_EXPORT validated export is not set")
    native = tmp_path / "native-extended.json"
    result = subprocess.run(
        [
            sys.executable,
            str(SPIKE / "native_mcts_benchmark.py"),
            "--model-export",
            str(model_export),
            "--evaluator",
            "onnx",
            "--extended",
            "--simulations",
            "2",
            "--time-budgets",
            "0.1",
            "--repetitions",
            "1",
            "--warmup",
            "0",
            "--output",
            str(native),
        ],
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    browser = tmp_path / "browser-extended.json"
    result = _run(
        "mcts_benchmark.mjs",
        NEURAL_DIR,
        "--provider",
        "wasm",
        "--extended",
        "--simulations",
        "2",
        "--time-budgets",
        "0.1",
        "--repetitions",
        "1",
        "--warmup",
        "0",
        "--native",
        str(native),
        "--output",
        str(browser),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    comparison = json.loads((tmp_path / "browser-extended-comparison.json").read_text())
    assert comparison["acceptance"] is True, comparison["failures"]
    native_report = json.loads(native.read_text())
    browser_report = json.loads(browser.read_text())
    for report in (native_report, browser_report):
        workload = report["workload"]
        assert workload["repetitions"] == 1
        assert workload["warmup"] == 0
        assert {entry.get("kind") for entry in workload["positions"]} == set(
            mcts_benchmark.POSITION_KINDS
        )
