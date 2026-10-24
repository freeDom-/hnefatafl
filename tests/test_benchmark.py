"""Small real comparisons report measured work and skip unavailable devices."""

import json

import pytest
import torch

from hnefatafl import cli
from hnefatafl.benchmark import run_benchmark


def test_cpu_comparison_uses_common_budgets_and_actual_work_without_training_writes(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    before_rng = torch.get_rng_state().clone()
    before_threads = torch.get_num_threads()
    unused_output = tmp_path / "not-created"
    result = run_benchmark(
        {
            "cache_modes": ["off"],
            "repetitions": 1,
            "inference_workload_batches": 2,
            "games": 3,
            "mcts_iterations": 2,
            "max_game_plies": 4,
            "self_play_concurrency": 3,
            "inference_batch_size": 3,
            "mcts_time_limit": 0.001,
            "exploration": 0.7,
            "temperature": 0.9,
            "dirichlet_alpha": 0.2,
            "dirichlet_fraction": 0.1,
            "output_dir": str(unused_output),
        }
    )
    assert torch.equal(before_rng, torch.get_rng_state())
    assert torch.get_num_threads() == before_threads
    assert not unused_output.exists()
    assert not result["cuda_available"]
    assert result["inference_precision"] == "float32"
    assert result["configuration"]["mcts_time_limit"] == 300
    assert result["configuration"]["mixed_precision"] is False
    assert {
        key: result["configuration"][key]
        for key in ("exploration", "temperature", "dirichlet_alpha", "dirichlet_fraction")
    } == {"exploration": 0.7, "temperature": 0.9, "dirichlet_alpha": 0.2, "dirichlet_fraction": 0.1}
    assert result["torch_version"] == str(torch.__version__)
    sequential, batched, *unavailable = result["scenarios"]
    assert sequential["mode"] == "sequential" and batched["mode"] == "batched"
    for scenario in (sequential, batched):
        assert scenario["status"] == "completed"
        assert scenario["device"] == "cpu"
        assert scenario["games"] == 3
        elapsed = scenario["total_runtime_seconds"]
        assert elapsed > 0
        assert scenario["games_per_hour"] == pytest.approx(3 * 3600 / elapsed)
        assert scenario["positions_per_second"] == pytest.approx(
            scenario["self_play_positions"] / elapsed
        )
        assert scenario["requested_simulations"] == scenario["self_play_positions"] * 2
        assert 0 < scenario["mcts_simulations"] <= scenario["requested_simulations"]
        assert scenario["simulations_per_second"] == pytest.approx(
            scenario["mcts_simulations"] / elapsed
        )
        assert scenario["neural_evaluations_per_second"] == pytest.approx(
            scenario["neural_evaluations"] / elapsed
        )
        assert scenario["inference_batches"] > 0
        assert (
            1
            <= scenario["inference_batch_size_min"]
            <= scenario["inference_batch_size_average"]
            <= scenario["inference_batch_size_max"]
            <= 3
        )
        assert scenario["completed_games"] + scenario["truncated_games"] == 3
    assert sequential["speedup_vs_sequential"] == 1
    assert batched["speedup_vs_sequential"] == pytest.approx(
        sequential["total_runtime_seconds"] / batched["total_runtime_seconds"]
    )
    assert sequential["inference_batch_size_max"] == 1
    assert batched["inference_batch_size_max"] == 3
    assert [(row["device"], row["mode"], row["status"]) for row in unavailable] == [
        ("cuda", "sequential", "skipped"),
        ("cuda", "batched", "skipped"),
    ]
    json.dumps(result, allow_nan=False)


def test_benchmark_cancellation_and_checkpoint_rejection():
    with pytest.raises(InterruptedError, match="cancelled"):
        run_benchmark(should_stop=lambda: True)
    with pytest.raises(ValueError, match="does not resume"):
        run_benchmark({"resume": "untrusted.pt"})


def test_benchmark_cli_optional_config_and_explicit_output(tmp_path, monkeypatch, capsys):
    from hnefatafl import benchmark

    expected = {"scenarios": [{"device": "cuda", "status": "skipped"}]}
    received = []

    def compare(config, *, should_stop):
        received.append(config)
        assert callable(should_stop) and not should_stop()
        return expected

    monkeypatch.setattr(benchmark, "run_benchmark", compare)
    monkeypatch.setattr(cli.signal, "signal", lambda *_: None)
    config = tmp_path / "benchmark.json"
    config.write_text(json.dumps({"games": 3}))
    destination = tmp_path / "results" / "benchmark.json"
    assert cli.main(["benchmark", "--config", str(config), "--output", str(destination)]) == 0
    assert received == [{"games": 3}]
    assert json.loads(capsys.readouterr().out) == json.loads(destination.read_text()) == expected
    assert cli.main(["benchmark"]) == 0
    assert received == [{"games": 3}, {}]


def test_benchmark_cli_rejects_non_object_configuration(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli.signal, "signal", lambda *_: None)
    config = tmp_path / "invalid.json"
    config.write_text("[]")
    assert cli.main(["benchmark", "--config", str(config)]) == 1
    assert json.loads(capsys.readouterr().err)["error"] == "Configuration must be a JSON object"


def test_cache_comparison_repeats_matched_work_and_controlled_inputs():
    result = run_benchmark(
        {
            "games": 2,
            "max_game_plies": 4,
            "mcts_iterations": 3,
            "repetitions": 2,
            "inference_workload_batches": 2,
        }
    )
    rows = [
        row
        for row in result["scenarios"]
        if row["status"] == "completed" and row["device"] == "cpu"
    ]
    assert len(rows) == 8
    assert all(
        row["seeded_behavior_matches_cache_off"] and row["work_matches_sequential"] for row in rows
    )
    for row in rows:
        assert (
            row["inference_requests"]
            == row["neural_evaluations"] + row["network_evaluations_eliminated"]
        )
        assert row["total_cpu_seconds"] > 0
        if row["cache_mode"] == "exact":
            assert row["network_evaluations_eliminated"] > 0
            assert row["inference_cache_lookup_seconds"] > 0
    workloads = [
        row
        for row in result["inference_workloads"]
        if row["device"] == "cpu" and row["cache_mode"] == "exact"
    ]
    assert len(workloads) == 4
    for row in workloads:
        assert row["neural_evaluations"] == (1 if row["workload"] == "duplicate-heavy" else 4)


@pytest.mark.parametrize(
    "changes",
    [
        {"repetitions": 0},
        {"repetitions": True},
        {"cache_modes": ["symmetry"]},
        {"inference_workload_batches": 0},
    ],
)
def test_invalid_comparison_budget(changes):
    with pytest.raises(ValueError):
        run_benchmark(changes)
