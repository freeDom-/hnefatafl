"""Deterministic accounting for the synthetic Champion-adaptation benchmark.

The benchmark drives the controller with a synthetic evaluation stream, so these
tests pin its accounting and the qualitative properties (a persistent transition
retains more evidence and changes the rate less often than a destructive reset)
without making any playing-strength claim.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from hnefatafl.hyper_adaptation import MAX_CONTEXTS

ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "benchmark_champion_adaptation", ROOT / "scripts" / "benchmark_champion_adaptation.py"
)
assert _SPEC is not None and _SPEC.loader is not None
benchmark = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(benchmark)


def test_fixed_strategy_never_changes_and_uses_no_controller():
    row = benchmark.run("fixed", seed=0, evaluations=100, promotion_every=20)
    assert row["changes"] == 0
    assert row["controller_seconds"] == 0.0
    assert row["contexts"] == 0
    assert row["events"] == 0
    assert row["reward_history"] == 0


def test_persistent_transition_retains_more_evidence_than_a_reset():
    persistent = benchmark.run("persistent", seed=0, evaluations=400, promotion_every=20)
    reset = benchmark.run("reset", seed=0, evaluations=400, promotion_every=20)
    # The reset rebuilds the controller at every promotion, so it loses the long
    # reward history and churns the rate far more often.
    assert persistent["reward_history"] == benchmark.MAX_REWARD_HISTORY
    assert reset["reward_history"] < persistent["reward_history"]
    assert persistent["events"] == benchmark.MAX_EVENTS
    assert reset["events"] < persistent["events"]
    assert persistent["changes"] < reset["changes"]
    assert persistent["generations"] == reset["generations"] == 19


def test_summary_reports_every_mode_and_stays_bounded():
    rows = [
        benchmark.run(mode, seed=seed, evaluations=60, promotion_every=10)
        for seed in range(2)
        for mode in ("fixed", "reset", "persistent")
    ]
    summary = benchmark.summarize(rows)
    assert set(summary) == {"fixed", "reset", "persistent"}
    for entry in summary.values():
        assert entry["median_controller_seconds"] >= 0
        assert entry["median_contexts"] <= MAX_CONTEXTS
        assert entry["median_events"] <= benchmark.MAX_EVENTS
        assert entry["median_reward_history"] <= benchmark.MAX_REWARD_HISTORY


def test_controller_overhead_is_small_against_the_evaluation_budget():
    row = benchmark.run("persistent", seed=0, evaluations=200, promotion_every=20)
    # The controller is a small pure-Python solve per evaluation; this is a loose
    # bound that still catches an accidental per-observation blow-up.
    assert row["controller_seconds"] < 1.0
    assert row["mean_regret"] == pytest.approx(row["mean_regret"])
