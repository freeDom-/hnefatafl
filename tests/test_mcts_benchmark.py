"""Native neural-MCTS parity harness and benchmark comparison contracts.

These run the native half of the comparison suite and the single comparison
implementation. They do not need Chromium, ORT Web or a staged model: browser
gates are the opt-in tests in ``test_browser_mcts_validation.py``.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SPIKE = Path(__file__).resolve().parents[1] / "spikes/browser_offline"
if str(SPIKE) not in sys.path:
    sys.path.insert(0, str(SPIKE))

import mcts_benchmark  # noqa: E402
import mcts_compare  # noqa: E402


def _native(tmp_path: Path, name: str, *extra: str, limit: str = "1") -> dict:
    output = tmp_path / name
    result = subprocess.run(
        [
            sys.executable,
            str(SPIKE / "native_mcts_parity.py"),
            "--level",
            "a",
            "--iterations",
            "16",
            "--limit",
            limit,
            "--output",
            str(output),
            *extra,
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(output.read_text())


def test_deterministic_parity_runs_are_exactly_reproducible(tmp_path):
    first = _native(tmp_path, "a.json")
    second = _native(tmp_path, "b.json")
    report = mcts_compare.compare(first, second, require_best_move=True)
    assert report["ok"] is True, report["failures"]
    assert report["parity"] is True
    assert report["exact_cases"] == report["positions"] == 3


def test_deterministic_parity_detects_a_divergent_evaluation(tmp_path):
    first = _native(tmp_path, "a.json")
    second = _native(tmp_path, "b.json")
    # Simulate a browser that answered with a different prior distribution.
    record = second["results"][0]
    record["search"]["root_visits"][0]["share"] = 0.0
    report = mcts_compare.compare(first, second, require_best_move=False)
    assert report["ok"] is False
    assert any("deterministic search payload differs" in failure for failure in report["failures"])


def test_parity_comparator_requires_matching_workload_and_coverage(tmp_path):
    first = _native(tmp_path, "a.json")
    second = _native(tmp_path, "b.json")
    second["workload_digest"] = "0" * 64
    second["positions"] = second["positions"][:-1]
    report = mcts_compare.compare(first, second, require_best_move=False)
    failures = " ".join(report["failures"])
    assert "workload digest differs" in failures
    assert "position coverage differs" in failures


def test_parity_comparator_rejects_illegal_best_moves(tmp_path):
    first = _native(tmp_path, "a.json")
    second = _native(tmp_path, "b.json")
    for record in second["results"]:
        if record["analysis"]["source"] == "neural_search":
            record["analysis"]["best_move"] = {"source": 99, "target": 99}
            break
    report = mcts_compare.compare(first, second, require_best_move=False)
    assert any("not legal" in failure for failure in report["failures"])


def test_benchmark_digest_normalises_json_numeric_types():
    positions = [{"state": {"ruleset": "brandubh", "board": [], "turn": "attackers"}}]
    cases = [
        {"mode": "fixed_time", "budget": 1},
        {"mode": "fixed_simulations", "budget": 128.0},
    ]
    first = mcts_benchmark.digest_workload(
        positions, cases, seed=7, exploration=1.4, model_sha256="a" * 64
    )
    second = mcts_benchmark.digest_workload(
        positions, cases, seed=7.0, exploration=1.4, model_sha256="a" * 64
    )
    assert first == second
    other = mcts_benchmark.digest_workload(
        positions,
        [{"mode": "fixed_time", "budget": 2}, cases[1]],
        seed=7,
        exploration=1.4,
        model_sha256="a" * 64,
    )
    assert other != first


def _benchmark_report(runtime: str, *, completed: int, sps: float, digest: str) -> dict:
    records = [
        {
            "runtime": runtime,
            "ruleset": "brandubh",
            "position": "brandubh-opening",
            "mode": "fixed_simulations",
            "budget": 128,
            "ok": True,
            "requested_simulations": 128,
            "completed_simulations": completed,
            "elapsed_ms": completed / sps * 1000,
            "completion_reason": "simulations",
            "deadline_cutoff": False,
            "neural_requests": 129,
            "prediction_reuses": 3,
            "root_children": 8,
        }
    ]
    return {
        "kind": "mcts-benchmark",
        "runtime": runtime,
        "engine_identity": {"sha256": "e" * 64},
        "workload_digest": digest,
        "records": records,
        "summary": mcts_benchmark.summarize(records),
    }


def test_benchmark_comparison_only_reports_equal_work_ratios():
    native = _benchmark_report("native-cpython", completed=128, sps=1000.0, digest="d" * 64)
    browser = _benchmark_report("browser-pyodide", completed=128, sps=250.0, digest="d" * 64)
    report = mcts_benchmark.compare_reports(native, browser)
    assert report["ok"] is True
    assert report["equal_work_cases"] == 1
    assert report["median_browser_to_native_ratio"] == pytest.approx(0.25)

    unequal = _benchmark_report("browser-pyodide", completed=64, sps=250.0, digest="d" * 64)
    report = mcts_benchmark.compare_reports(native, unequal)
    assert report["equal_work_cases"] == 0
    assert report["median_browser_to_native_ratio"] is None


def test_incomplete_fixed_work_browser_runs_fail_the_comparison():
    native = _benchmark_report("native-cpython", completed=128, sps=1000.0, digest="d" * 64)
    browser = _benchmark_report("browser-pyodide", completed=96, sps=250.0, digest="d" * 64)
    report = mcts_benchmark.compare_reports(native, browser)
    assert report["ok"] is False
    assert any("completed 96 of 128" in failure for failure in report["failures"])
