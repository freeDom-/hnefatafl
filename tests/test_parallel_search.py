"""Real spawned actors, immutable weights, bounded replay and failure cleanup."""

from copy import deepcopy

import pytest
import torch

from hnefatafl.models import PolicyValueNet, load_checkpoint
from hnefatafl.parallel_search import SearchPool
from hnefatafl.training import generate_self_play, normalize_config, train
from hnefatafl.training_evaluation import evaluate_model


@pytest.fixture(autouse=True)
def threads():
    old = torch.get_num_threads()
    torch.set_num_threads(1)
    yield
    torch.set_num_threads(old)


def settings(**changes):
    return normalize_config(
        {
            "preset": "smoke",
            "device": "cpu",
            "games": 4,
            "max_game_plies": 6,
            "mcts_iterations": 8,
            "mcts_time_limit": 300,
            "self_play_mode": "sequential",
            "self_play_concurrency": 4,
            "evaluation_time_limit": 300,
            "evaluation_max_plies": 6,
            "evaluation_iterations": 8,
            "cpu_threads": 1,
            **changes,
        }
    )


def assert_games_equal(actual, expected):
    assert len(actual) == len(expected)
    for (rows, outcome), (references, result) in zip(actual, expected):
        assert {key: outcome[key] for key in ("winner", "plies", "simulations", "outcome")} == {
            key: result[key] for key in ("winner", "plies", "simulations", "outcome")
        }
        for row, reference in zip(rows, references):
            assert torch.equal(row["input"], reference["input"])
            assert row["actions"] == reference["actions"]
            assert row["probabilities"] == reference["probabilities"]
            assert row["legal"] == reference["legal"]
            assert row["value"] == pytest.approx(reference["value"], abs=1e-6)


@pytest.mark.parametrize("cache", ["off", "exact"])
def test_spawned_games_match_reference_and_commit_in_order(cache):
    torch.manual_seed(41)
    model = PolicyValueNet("brandubh", 4, 0).eval()
    config = settings(inference_cache_mode=cache)
    expected, _ = generate_self_play(model, config, seed=17)
    commits = []
    actual, metrics = generate_self_play(
        model,
        {**config, "search_workers": 2},
        seed=17,
        on_game=lambda index, *_: commits.append(index),
    )
    assert_games_equal(actual, expected)
    assert commits == [0, 1, 2, 3]
    assert metrics["search_workers"] == 2
    assert metrics["inference_location"] == "actors"
    assert metrics["neural_evaluations"] > 0
    assert metrics["search_traversal_wall_seconds"] is None


def test_persistent_pool_rebinds_updated_weights_and_parent_inference():
    torch.manual_seed(42)
    model = PolicyValueNet("brandubh", 4, 0).eval()
    config = settings(games=2, max_game_plies=4)

    class ParentBackend:
        def __init__(self):
            self.model = model
            self.device = torch.device("cpu")
            self.info = {}
            self.calls = 0

        def forward(self, batch):
            self.calls += 1
            return model(batch)

    with SearchPool(2) as pool:
        for backend in (None, ParentBackend()):
            with torch.no_grad():
                next(model.parameters()).add_(0.02)
            expected, _ = generate_self_play(model, config, seed=19)
            actual, metrics = generate_self_play(
                model, {**config, "search_workers": 2}, seed=19, search_pool=pool, backend=backend
            )
            assert_games_equal(actual, expected)
            if backend is not None:
                assert backend.calls > 0
                assert metrics["inference_location"] == "parent"
        assert not pool.closed
    assert all(not process.is_alive() for process in pool.processes)


@pytest.mark.parametrize("opponent", ["shield_bearer", "shield_maiden", "jarl", "champion"])
def test_parallel_evaluation_retains_paired_seeds_and_models(opponent):
    torch.manual_seed(43)
    model = PolicyValueNet("brandubh", 4, 0).eval()
    previous = deepcopy(model) if opponent == "champion" else None
    if previous is not None:
        with torch.no_grad():
            next(previous.parameters()).add_(0.1)
    config = settings(
        evaluation_opponent=opponent,
        evaluation_games=4,
        evaluation_jarl_depth=1,
        evaluation_jarl_time_limit=300,
    )
    expected = evaluate_model(model, config, previous=previous)
    actual = evaluate_model(model, {**config, "search_workers": 2}, previous=previous)
    assert [
        {key: row[key] for key in expected["games"][index]}
        for index, row in enumerate(actual["games"])
    ] == expected["games"]
    assert actual["opponent_identity"] == expected["opponent_identity"]
    assert actual["performance"]["mode"] == "parallel"


def test_cancellation_and_crash_release_all_processes():
    model = PolicyValueNet("brandubh", 4, 0).eval()
    config = settings(search_workers=2)
    stopped = False

    def progress(info):
        nonlocal stopped
        if info["game"]:
            stopped = True

    pool = SearchPool(2)
    with pytest.raises(InterruptedError):
        generate_self_play(
            model, config, seed=23, progress=progress, should_stop=lambda: stopped, search_pool=pool
        )
    assert pool.closed and all(not process.is_alive() for process in pool.processes)
    pool = SearchPool(2)
    pool.processes[0].terminate()
    pool.processes[0].join()
    with pytest.raises(RuntimeError):
        list(pool.games("self_play", model, config, seed=23))
    assert pool.closed and all(not process.is_alive() for process in pool.processes)


def test_parallel_real_update_and_full_state_resume(tmp_path):
    config = settings(
        search_workers=2,
        games=2,
        max_game_plies=2,
        evaluation_max_plies=2,
        output_dir=str(tmp_path / "first"),
        registry_dir=str(tmp_path / "models"),
    )
    first = train(config)
    assert first["metrics"]["updates"] > 0
    resumed = train(
        {
            **config,
            "resume": first["checkpoint"],
            "iterations": 2,
            "output_dir": str(tmp_path / "resumed"),
        }
    )
    _, saved = load_checkpoint(resumed["checkpoint"])
    assert saved["extra"]["iteration"] == 2 and saved["optimizer_state"]
    assert saved["metadata"]["training_config"]["search_workers"] == 2
    uninterrupted = train({**config, "iterations": 2, "output_dir": str(tmp_path / "full")})
    _, full = load_checkpoint(uninterrupted["checkpoint"])
    assert all(
        torch.equal(tensor, full["state_dict"][key]) for key, tensor in saved["state_dict"].items()
    )


@pytest.mark.parametrize("value", [-1, 33, True, 1.5])
def test_worker_count_validation(value):
    with pytest.raises(ValueError):
        normalize_config({"search_workers": value})


def test_legacy_workers_can_claim_disabled_but_not_parallel_jobs():
    from hnefatafl.remote_training import worker_compatible

    config = settings(training_symmetry_mode="off")
    assert worker_compatible(config, {})
    assert not worker_compatible({**config, "search_workers": 2}, {})
    assert worker_compatible({**config, "search_workers": 2}, {"parallel_search": True})


@pytest.mark.skipif(not torch.cuda.is_available(), reason="CUDA unavailable")
def test_cuda_models_stay_in_parent():
    model = PolicyValueNet("brandubh", 4, 0).cuda().eval()
    _, metrics = generate_self_play(
        model, settings(search_workers=2, games=2, max_game_plies=2), seed=17
    )
    assert metrics["inference_location"] == "parent"
    assert metrics["neural_evaluations"] > 0
