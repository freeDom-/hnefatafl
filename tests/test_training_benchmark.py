"""Full-iteration benchmark keeps its CPU baseline usable without a GPU."""

import json

import pytest

from hnefatafl.training_benchmark import run_training_benchmark


def test_complete_training_benchmark_and_explicit_gpu_skip(tmp_path, monkeypatch):
    import hnefatafl.training_benchmark as module

    monkeypatch.setattr(module, "openvino_capabilities", lambda: {})
    report = run_training_benchmark(
        {
            "training": {
                "preset": "smoke",
                "games": 2,
                "max_game_plies": 2,
                "evaluation_max_plies": 2,
                "cpu_threads": 1,
            },
            "repetitions": 1,
            "warmup_iterations": 0,
            "measured_iterations": 1,
            "output_dir": str(tmp_path / "benchmark"),
        }
    )
    cpu, gpu = report["scenarios"]
    assert cpu["status"] == "completed" and gpu["status"] == "skipped"
    perf = cpu["iterations"][0]
    assert perf["optimization_seconds"] > 0
    assert perf["iteration_wall_seconds"] >= perf["iteration_compute_seconds"]
    assert perf["artifact_persistence_seconds"] > 0
    assert perf["iteration_games_per_hour"] == pytest.approx(
        perf["games"] * 3600 / perf["iteration_wall_seconds"]
    )
    assert perf["inference_batch_distribution"]
    assert report["summary"]["pytorch"]["mean_iteration_wall_seconds"] > 0
    assert "openvino" not in report["summary"]
    assert (
        json.loads((tmp_path / "benchmark" / "benchmark.json").read_text())["scenarios"][1][
            "status"
        ]
        == "skipped"
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"repetitions": 0},
        {"warmup_iterations": -1},
        {"measured_iterations": False},
        {"training": {"resume": "weights.pt"}},
        {"unknown": 1},
    ],
)
def test_invalid_full_training_benchmark(changes):
    with pytest.raises(ValueError):
        run_training_benchmark(changes)
