"""Actual-browser full-logit vs legal-only neural MCTS parity.

Opt-in because it needs staged ONNX assets and Chromium. Set
``SPIKE_NEURAL_DIR`` for one model stage, optionally
``SPIKE_NEURAL_DIR_TABLUT`` and ``SPIKE_NEURAL_DIR_COPENHAGEN`` for the full
three-ruleset matrix. Ordinary CI skips rather than downloading runtimes.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from hnefatafl.engine import Move, State, legal_moves

SPIKE = Path(__file__).resolve().parents[1] / "spikes/browser_offline"
NODE = shutil.which("node")
STAGES = [
    os.environ.get(name)
    for name in ("SPIKE_NEURAL_DIR", "SPIKE_NEURAL_DIR_TABLUT", "SPIKE_NEURAL_DIR_COPENHAGEN")
]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
@pytest.mark.skipif(not any(STAGES), reason="neural staging directories are not configured")
def test_full_and_compact_search_paths_produce_equivalent_results(tmp_path):
    roots = [root for root in STAGES if root]
    output = tmp_path / "path-parity.json"
    result = subprocess.run(
        [
            NODE,
            str(SPIKE / "test_search_path_parity.mjs"),
            *roots,
            "--provider",
            "wasm",
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
    assert report["cases"]
    for case in report["cases"]:
        assert case["ok"] is True, case
        state = State.from_dict(case["state"])
        if state.winner is None:
            for label in ("full", "compact"):
                move = case[f"{label}_move"]
                assert move is not None, (case["position"], label)
                assert Move(move["source"], move["target"]) in legal_moves(state), (
                    case["position"],
                    label,
                )
        if case["terminal"]:
            continue
        assert case["full"]["search"]["coordinator"]["inference_payload_mode"] == "full-logits"
        assert case["compact"]["search"]["coordinator"]["inference_payload_mode"] == "legal-only"


@pytest.mark.skipif(NODE is None, reason="node is not installed")
@pytest.mark.skipif(not any(STAGES), reason="neural staging directories are not configured")
def test_browser_collector_error_after_cases_is_not_accepted(tmp_path):
    root = next(root for root in STAGES if root)
    output = tmp_path / "path-parity-error.json"
    result = subprocess.run(
        [
            NODE,
            str(SPIKE / "test_search_path_parity.mjs"),
            root,
            "--provider",
            "wasm",
            "--inject-page-error",
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode != 0, result.stdout + result.stderr
    report = json.loads(output.read_text())
    assert report["ok"] is False
    assert report["collector_ok"] is False
    assert report["cases_ok"] is True
    assert all(case["ok"] for case in report["cases"])
    assert any("injected page error after final case" in failure for failure in report["failures"])


@pytest.mark.skipif(NODE is None, reason="node is not installed")
@pytest.mark.skipif(not any(STAGES), reason="neural staging directories are not configured")
def test_direct_worker_transport_preserves_full_compact_parity(tmp_path):
    root = next(root for root in STAGES if root)
    output = tmp_path / "path-parity-direct.json"
    result = subprocess.run(
        [
            NODE,
            str(SPIKE / "test_search_path_parity.mjs"),
            root,
            "--provider",
            "wasm",
            "--transport",
            "direct",
            "--simulations",
            "32",
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(output.read_text())
    assert report["ok"] is True, report["failures"]
    assert report["transport"] == "direct"
    assert all(case["ok"] for case in report["cases"])
