"""Benchmark protocol: alternating pairs, paired overhead and report structure.

The benchmark script is not part of the installed package, so it is loaded from
its path. Only its pure orchestration helpers are exercised here; the real
search is covered by the parallel-search tests.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
import torch

from hnefatafl.models import PolicyValueNet

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "benchmark_evaluation_progress.py"


@pytest.fixture(scope="module")
def benchmark():
    spec = importlib.util.spec_from_file_location("benchmark_evaluation_progress", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def sample(benchmark, mode, seconds, messages, batches=100):
    return {
        "workers": 2,
        "monitor": mode,
        "seconds": seconds,
        "work_sha256": "fixed",
        "counters": {
            "search_progress_messages": messages,
            "inference_batches": batches,
            "neural_evaluations": batches * 4,
        },
        "latency": None,
    }


def test_alternating_order_reverses_the_first_arm_every_pair(benchmark):
    assert [benchmark.alternating_order(pair) for pair in range(4)] == [
        ("off", "on"),
        ("on", "off"),
        ("off", "on"),
        ("on", "off"),
    ]


def test_pair_overhead_reports_absolute_and_relative_cost(benchmark):
    overhead = benchmark.pair_overhead(10.0, 11.0)
    assert overhead["absolute_difference_seconds"] == pytest.approx(1.0)
    assert overhead["percentage_overhead"] == pytest.approx(10.0)
    # A zero baseline cannot produce a percentage, but the absolute still counts.
    zero = benchmark.pair_overhead(0.0, 1.0)
    assert zero["absolute_difference_seconds"] == pytest.approx(1.0)
    assert zero["percentage_overhead"] is None


def test_run_paired_arms_warms_both_modes_and_measures_equal_samples(benchmark):
    warmups: list[str] = []
    calls: list[tuple[str, int]] = []

    def warmup(mode):
        warmups.append(mode)

    def measure(mode, pair):
        calls.append((mode, pair))
        return sample(
            benchmark,
            mode,
            seconds=10.0 if mode == "off" else 11.0,
            messages=0 if mode == "off" else 25,
        )

    records, samples = benchmark.run_paired_arms("evaluation", 2, 3, warmup, measure)
    # Both arms are warmed and the warm-ups are excluded from the measured runs.
    assert warmups == ["off", "on"]
    assert len(calls) == 6
    assert len(records) == 3 and len(samples) == 6
    assert [record["order"] for record in records] == [
        ["off", "on"],
        ["on", "off"],
        ["off", "on"],
    ]
    for record in records:
        assert record["absolute_difference_seconds"] == pytest.approx(1.0)
        assert record["percentage_overhead"] == pytest.approx(10.0)
    # Every pair contributes exactly one sample per arm.
    for mode in ("off", "on"):
        assert sum(1 for value in samples if value["monitor"] == mode) == 3


def test_summarize_runs_groups_arms_and_computes_messages_per_batch(benchmark):
    results = [
        sample(benchmark, "off", 10.0, 0),
        sample(benchmark, "on", 11.0, 25),
        sample(benchmark, "on", 12.0, 75),
    ]
    summary = benchmark.summarize_runs(results)
    by_arm = {(entry["workers"], entry["monitor"]): entry for entry in summary}
    assert by_arm[(2, "off")]["messages_per_batch"] == [0.0]
    assert by_arm[(2, "on")]["messages_per_batch"] == pytest.approx([0.25, 0.75])
    assert by_arm[(2, "on")]["forward_passes"] == [100, 100]
    assert by_arm[(2, "on")]["neural_evaluations"] == [400, 400]
    assert by_arm[(2, "on")]["work_hashes"] == ["fixed"]


def test_summarize_pairs_reports_median_overhead(benchmark):
    records = [
        {
            "kind": "evaluation",
            "workers": 2,
            "absolute_difference_seconds": 1.0,
            "percentage_overhead": 10.0,
        },
        {
            "kind": "evaluation",
            "workers": 2,
            "absolute_difference_seconds": 3.0,
            "percentage_overhead": 20.0,
        },
        {
            "kind": "evaluation",
            "workers": 4,
            "absolute_difference_seconds": -1.0,
            "percentage_overhead": None,
        },
    ]
    summary = benchmark.summarize_pairs(records)
    by_workers = {entry["workers"]: entry for entry in summary}
    assert by_workers[2]["pairs"] == 2
    assert by_workers[2]["median_absolute_difference_seconds"] == pytest.approx(2.0)
    assert by_workers[2]["median_percentage_overhead"] == pytest.approx(15.0)
    assert by_workers[4]["median_absolute_difference_seconds"] == pytest.approx(-1.0)
    assert by_workers[4]["median_percentage_overhead"] is None


def test_reset_model_restores_the_fixed_snapshot(benchmark):
    torch.manual_seed(7)
    model = PolicyValueNet("brandubh", 4, 0)
    weights = {key: value.detach().clone() for key, value in model.state_dict().items()}
    with torch.no_grad():
        for parameter in model.parameters():
            parameter.add_(1.0)
    benchmark.reset_model(model, weights, 424242)
    for key, value in model.state_dict().items():
        assert torch.equal(value, weights[key])
    # No optimizer is constructed or stepped anywhere in the benchmark.
    source = SCRIPT.read_text()
    assert "torch.optim" not in source
    assert "optimizer." not in source
    assert "optimizer =" not in source


def test_benchmark_report_is_valid_json(benchmark):
    results = [
        sample(benchmark, "off", 10.0, 0),
        sample(benchmark, "on", 11.0, 25),
    ]
    records, _ = benchmark.run_paired_arms(
        "evaluation",
        2,
        1,
        lambda mode: None,
        lambda mode, pair: sample(benchmark, mode, 10.0 if mode == "off" else 11.0, 0),
    )
    payload = {
        "protocol": "paired",
        "summary": benchmark.summarize_runs(results),
        "paired_summary": benchmark.summarize_pairs(records),
        "pairs": records,
        "runs": results,
    }
    restored = json.loads(json.dumps(payload))
    assert restored["paired_summary"][0]["pairs"] == 1
    assert restored["summary"][0]["monitor"] in ("off", "on")
