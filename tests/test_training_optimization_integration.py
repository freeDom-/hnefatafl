"""Cross-feature acceptance; activates only after all five feature PRs combine."""

import importlib.util
import random

import pytest

import torch

from hnefatafl import ai, engine
from hnefatafl.inference import ExactInferenceCache, policy_value_batch
from hnefatafl.models import PolicyValueNet, encode_state, load_checkpoint
from hnefatafl.training import normalize_config, optimize_replay, train
from hnefatafl.training_evaluation import evaluate_model, frozen_model, snapshot

for module in ("evaluation_play", "parallel_search", "replay_pipeline"):
    if importlib.util.find_spec(f"hnefatafl.{module}") is None:
        pytest.skip(
            "Requires the combined training optimization implementations", allow_module_level=True
        )


if not hasattr(ai, "_lazy_ordered") or not hasattr(engine, "has_legal_move"):
    pytest.skip("Requires all five training optimization implementations", allow_module_level=True)


@pytest.fixture(autouse=True)
def single_thread():
    old = torch.get_num_threads()
    torch.set_num_threads(1)
    yield
    torch.set_num_threads(old)


def config(tmp_path, **changes):
    return {
        "preset": "smoke",
        "device": "cpu",
        "cpu_threads": 1,
        "seed": 41,
        "games": 2,
        "max_game_plies": 2,
        "evaluation_max_plies": 2,
        "mcts_time_limit": 30,
        "evaluation_time_limit": 30,
        "search_workers": 2,
        "self_play_concurrency": 2,
        "evaluation_mode": "batched",
        "evaluation_concurrency": 2,
        "evaluation_batch_size": 3,
        "replay_pipeline": "packed",
        "evaluation_opponent": "champion",
        "evaluation_games": 4,
        "inference_cache_mode": "exact",
        "registry_dir": str(tmp_path / "models"),
        "output_dir": str(tmp_path / "first"),
        **changes,
    }


def test_combined_actor_training_packed_replay_and_champion_resume(tmp_path):
    settings = config(tmp_path)
    first = train(settings)
    resumed = train(
        {
            **settings,
            "iterations": 2,
            "resume": first["checkpoint"],
            "output_dir": str(tmp_path / "resumed"),
        }
    )
    full = train({**settings, "iterations": 2, "output_dir": str(tmp_path / "full")})
    _, left = load_checkpoint(resumed["checkpoint"])
    _, right = load_checkpoint(full["checkpoint"])
    assert all(
        torch.equal(value, right["state_dict"][key]) for key, value in left["state_dict"].items()
    )
    assert left["extra"]["augmentation_rng"] == right["extra"]["augmentation_rng"]
    assert left["metadata"]["self_play_games"]["cumulative"] == 4
    assert resumed["metrics"]["performance"]["replay_pipeline"] == "packed"
    assert resumed["metrics"]["evaluation"]["performance"]["mode"] == "parallel"
    assert resumed["metrics"]["updates"] > 0


def test_packed_update_invalidates_exact_cache_before_batched_champion_evaluation():
    torch.manual_seed(43)
    model = PolicyValueNet("brandubh", 4, 0).eval()
    incumbent = frozen_model(model, snapshot(model, {"version": 0}))
    state = engine.new_game("brandubh")
    cache = ExactInferenceCache(64)
    policy_value_batch([state], model, cache=cache)
    moves = engine.legal_moves(state)
    ids = [move.source * state.size**2 + move.target for move in moves]
    replay = [
        {
            "input": encode_state(state),
            "actions": ids[:4],
            "legal": ids,
            "probabilities": [0.25] * 4,
            "value": 1.0,
        }
    ]
    settings = normalize_config(
        {
            "preset": "smoke",
            "training_symmetry_mode": "off",
            "replay_pipeline": "packed",
            "evaluation_mode": "batched",
            "evaluation_opponent": "champion",
            "evaluation_max_plies": 2,
            "evaluation_time_limit": 30,
            "inference_cache_mode": "exact",
        }
    )
    optimize_replay(
        model,
        torch.optim.AdamW(model.parameters(), lr=0.01),
        torch.amp.GradScaler("cuda", enabled=False),
        replay,
        settings,
        torch.device("cpu"),
        False,
        random.Random(9),
        random.Random(10),
    )
    model.eval()
    actual = policy_value_batch([state], model, cache=cache)[0]
    expected = policy_value_batch([state], model)[0]
    assert actual[0] == pytest.approx(expected[0]) and actual[1] == pytest.approx(expected[1])
    result = evaluate_model(model, settings, previous=incumbent)
    assert result["performance"]["mode"] == "batched"
    assert len(result["performance"]["models"]) == 2


def test_lazy_jarl_pairs_match_serial_batched_and_process_schedulers():
    torch.manual_seed(42)
    model = PolicyValueNet("brandubh", 4, 0).eval()
    settings = normalize_config(
        {
            "preset": "smoke",
            "evaluation_opponent": "jarl",
            "evaluation_jarl_depth": 2,
            "evaluation_jarl_time_limit": 300,
            "evaluation_time_limit": 300,
            "evaluation_max_plies": 4,
            "evaluation_iterations": 4,
        }
    )
    reference = evaluate_model(model, {**settings, "evaluation_mode": "sequential"})
    for mode, workers in (("batched", 0), ("auto", 2)):
        actual = evaluate_model(
            model, {**settings, "evaluation_mode": mode, "search_workers": workers}
        )
        assert [
            {key: row[key] for key in reference["games"][index]}
            for index, row in enumerate(actual["games"])
        ] == reference["games"]
        assert actual["opponent_identity"] == reference["opponent_identity"]


def test_openvino_cpu_post_update_evaluation_with_actors_and_packed_replay(tmp_path):
    pytest.importorskip("openvino")
    from hnefatafl.inference_backends import openvino_capabilities, supports_openvino

    if not supports_openvino(openvino_capabilities(), "CPU"):
        pytest.skip("Operational OpenVINO CPU plugin unavailable")
    result = train(
        config(
            tmp_path,
            inference_backend="openvino",
            inference_device="CPU",
            evaluation_inference_backend="training",
            evaluation_opponent="shield_bearer",
            evaluation_games=2,
        )
    )
    assert result["metrics"]["updates"] > 0
    metrics = result["metrics"]["performance"]
    assert metrics["evaluation_inference_backend"] == "openvino"
    assert metrics["evaluation_inference_device"] == "CPU"
    assert result["metrics"]["evaluation"]["context"]["inference_backend"] == "openvino"
    assert result["metrics"]["evaluation"]["performance"]["inference_location"] == "parent"
