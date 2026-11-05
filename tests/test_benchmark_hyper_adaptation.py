"""Deterministic accounting for the online-adaptation smoke benchmark.

The scheduler baseline is realised as two training phases joined by the resume
path, so the aggregate must count each iteration's evaluation exactly once. These
tests pin that accounting with synthetic phase results and one real, tiny
scheduler run; they do not measure strength or efficiency.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "benchmark_hyper_adaptation", ROOT / "scripts" / "benchmark_hyper_adaptation.py"
)
assert _SPEC is not None and _SPEC.loader is not None
benchmark = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(benchmark)


def _point(iteration: int, *, games: int, evaluation_seconds: float, rate: float) -> dict:
    return {
        "iteration": iteration,
        "learning_rate": rate,
        "updates": 4,
        "evaluation": {"game_count": games, "score": 0.5},
        "performance": {"evaluation_seconds": evaluation_seconds, "adaptation_seconds": 0.0},
    }


def test_summarize_counts_each_iteration_once_across_a_resume():
    phase_one = {
        "history": [
            _point(1, games=4, evaluation_seconds=0.1, rate=0.001),
            _point(2, games=4, evaluation_seconds=0.1, rate=0.001),
        ],
        "metrics": {"evaluation": {"game_count": 4, "score": 0.5}},
    }
    # The resumed phase restores iterations 1-2 and adds 3-4. Its restored points
    # carry the first phase's cost and must not be counted again.
    phase_two = {
        "history": [
            _point(1, games=4, evaluation_seconds=0.1, rate=0.001),
            _point(2, games=4, evaluation_seconds=0.1, rate=0.001),
            _point(3, games=4, evaluation_seconds=0.2, rate=0.0005),
            _point(4, games=4, evaluation_seconds=0.2, rate=0.0005),
        ],
        "metrics": {"evaluation": {"game_count": 4, "score": 0.5}},
    }
    summary = benchmark.summarize([(phase_one, 1.0, 1), (phase_two, 2.0, 3)])
    assert summary["cumulative_evaluation_games"] == 16
    assert summary["evaluated_iterations"] == 4
    assert summary["final_evaluation_games"] == 4
    assert summary["evaluation_seconds"] == pytest.approx(0.6)
    assert summary["updates"] == 16
    assert summary["wall_seconds"] == pytest.approx(3.0)
    assert summary["learning_rate_trajectory"] == [0.001, 0.001, 0.0005, 0.0005]


def test_summarize_reports_final_evaluation_separately_from_cumulative():
    result = {
        "history": [
            _point(1, games=2, evaluation_seconds=0.05, rate=0.001),
            _point(2, games=2, evaluation_seconds=0.05, rate=0.001),
            _point(3, games=2, evaluation_seconds=0.05, rate=0.001),
        ],
        "metrics": {"evaluation": {"game_count": 2, "score": 0.25}},
    }
    summary = benchmark.summarize([(result, 0.5, 1)])
    assert summary["cumulative_evaluation_games"] == 6
    assert summary["final_evaluation_games"] == 2
    assert summary["evaluation_seconds"] == pytest.approx(0.15)


def test_real_scheduler_accounts_for_every_evaluation(tmp_path):
    summary = benchmark.run_scheduler(tmp_path, seed=0, iterations=2, evaluation_games=4)
    # Two iterations, one evaluation of four games each: the resume boundary must
    # not lose or double an evaluation.
    assert summary["cumulative_evaluation_games"] == 8
    assert summary["evaluated_iterations"] == 2
    assert summary["final_evaluation_games"] == 4
    assert len(summary["learning_rate_trajectory"]) == 2
    assert summary["updates"] > 0
