"""Benchmark accounting, seed pairing, budgets and cooperative cancellation."""

import pytest

from hnefatafl import heuristic_benchmark as benchmark
from hnefatafl.engine import new_game


def test_latency_and_seed_pair_accounting():
    assert benchmark.distribution([0.001, 0.002, 0.003])["median_ms"] == 2
    games = [
        {"winner": "attackers", "candidate_side": "attackers", "plies": 2},
        {"winner": None, "candidate_side": "defenders", "plies": 2},
    ]
    summary = benchmark.summary(games)
    assert summary["wins"] == 1 and summary["unfinished"] == 1 and summary["draws"] == 0
    assert summary["score"] == 0.5
    assert benchmark.paired_interval(games)["pairs"] == 1


def test_reproducible_real_benchmark_and_distinct_truncations(monkeypatch):
    state = new_game("brandubh")
    monkeypatch.setattr(benchmark, "corpus", lambda _seed: [("opening", state)])
    result = benchmark.run_heuristic_benchmark(
        {
            "games": {"brandubh": 2},
            "max_plies": 2,
            "self_games": 2,
            "jarl_games": 2,
            "training_evaluation": False,
            "latency_repeats": 1,
        }
    )
    records = result["strength"]["brandubh"]["records"]
    assert [g["candidate_side"] for g in records] == ["attackers", "defenders"]
    assert records[0]["seed"] == records[1]["seed"]
    assert result["strength"]["brandubh"]["unfinished"] == 2
    assert all(v["evaluated_moves"] > 0 for v in result["latency_summary"].values())
    assert result["profiles"]["shield_maiden"]["heuristic_version"] == 1
    with pytest.raises(InterruptedError):
        benchmark.run_heuristic_benchmark({"games": {"brandubh": 2}}, should_stop=lambda: True)


def test_same_opening_for_both_roles_and_budget_validation():
    state = benchmark.opening("tablut", 42, 8)
    assert state == benchmark.opening("tablut", 42, 8) and state.winner is None
    assert state.ply == 8
    with pytest.raises(ValueError, match="even"):
        benchmark.run_heuristic_benchmark({"games": {"brandubh": 3}})
