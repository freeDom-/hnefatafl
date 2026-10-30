"""Evaluation scheduling preserves paired protocols and model ownership."""

from copy import deepcopy

import pytest
import torch

from hnefatafl import evaluation_play
from hnefatafl.models import PolicyValueNet
from hnefatafl.training import normalize_config, train
from hnefatafl.training_evaluation import context_identity, evaluate_model, evaluation_context


@pytest.fixture(autouse=True)
def threads():
    old = torch.get_num_threads()
    torch.set_num_threads(1)
    yield
    torch.set_num_threads(old)


@pytest.mark.parametrize("opponent", ["shield_bearer", "shield_maiden", "jarl", "champion"])
def test_scheduling_retains_games_pairs_and_frozen_model_routing(opponent, monkeypatch):
    torch.manual_seed(41)
    model = PolicyValueNet("brandubh", 4, 0).eval()
    previous = deepcopy(model) if opponent == "champion" else None
    if previous is not None:
        with torch.no_grad():
            next(previous.parameters()).add_(0.1)
    config = normalize_config(
        {
            "preset": "smoke",
            "evaluation_opponent": opponent,
            "evaluation_games": 4,
            "evaluation_max_plies": 8,
            "evaluation_iterations": 8,
            "evaluation_time_limit": 30,
            "evaluation_jarl_depth": 1,
            "evaluation_jarl_time_limit": 30,
            "evaluation_concurrency": 4,
            "evaluation_batch_size": 3,
        }
    )
    expected = evaluate_model(model, {**config, "evaluation_mode": "sequential"}, previous=previous)
    calls = []
    forward = evaluation_play.policy_value_batch

    def record(states, selected, **kwargs):
        assert selected is model or selected is previous
        assert kwargs["mixed_precision"] is False
        calls.append((selected, len(states)))
        return forward(states, selected, **kwargs)

    monkeypatch.setattr(evaluation_play, "policy_value_batch", record)
    actual = evaluate_model(model, {**config, "evaluation_mode": "batched"}, previous=previous)
    assert [
        {k: row[k] for k in expected["games"][i]} for i, row in enumerate(actual["games"])
    ] == expected["games"]
    assert actual["opponent_identity"] == expected["opponent_identity"]
    assert actual["score"] == expected["score"]
    assert max(size for _, size in calls) > 1
    if previous is not None:
        assert any(selected is previous for selected, _ in calls)
    assert all(row["search_deadline_cutoffs"] == 0 for row in actual["games"])


def test_cancelled_contexts_release_searches(monkeypatch):
    model = PolicyValueNet("brandubh", 4, 0).eval()
    config = normalize_config({"preset": "smoke", "evaluation_mode": "batched"})
    cancelled = False
    forward = evaluation_play.policy_value_batch

    def stop_after_forward(*args, **kwargs):
        nonlocal cancelled
        result = forward(*args, **kwargs)
        cancelled = True
        return result

    monkeypatch.setattr(evaluation_play, "policy_value_batch", stop_after_forward)
    with pytest.raises(InterruptedError):
        evaluate_model(model, config, should_stop=lambda: cancelled)


def test_openvino_refreshes_each_post_update_model_and_closes_on_failure(monkeypatch):
    from hnefatafl.inference_backends import TorchInference

    model = PolicyValueNet("brandubh", 4, 0).eval()
    previous = deepcopy(model)
    backends = []

    class FrozenBackend(TorchInference):
        def refresh(self):
            self.frozen = deepcopy(self.model)
            self.refreshed = True
            return {}

        def forward(self, batch):
            assert self.refreshed
            return self.frozen(batch)

        def close(self):
            self.closed = True

    def factory(selected, settings):
        assert settings["games"] == 2
        backend = FrozenBackend(selected, settings)
        backends.append(backend)
        return backend

    monkeypatch.setattr(evaluation_play, "create_inference", factory)
    cfg = normalize_config(
        {
            "preset": "smoke",
            "device": "cpu",
            "inference_backend": "openvino",
            "evaluation_inference_backend": "training",
            "evaluation_opponent": "champion",
            "evaluation_max_plies": 2,
            "evaluation_time_limit": 30,
        }
    )
    with torch.no_grad():
        next(model.parameters()).add_(0.25)
    result = evaluate_model(model, cfg, previous=previous)
    assert result["performance"]["inference_backend"] == "openvino"
    assert len(backends) == 2 and all(item.closed for item in backends)
    assert torch.equal(next(backends[0].frozen.parameters()), next(model.parameters()))
    assert torch.equal(next(backends[1].frozen.parameters()), next(previous.parameters()))
    backends.clear()
    with pytest.raises(InterruptedError):
        evaluate_model(model, cfg, previous=previous, should_stop=lambda: True)
    assert len(backends) == 2 and all(item.closed for item in backends)


def test_backend_separates_protocol_but_scheduling_does_not():
    config = normalize_config({"preset": "smoke"})
    identity = context_identity(evaluation_context(config))
    assert identity == context_identity(
        evaluation_context({**config, "evaluation_mode": "batched"})
    )
    assert identity != context_identity(
        evaluation_context(
            {**config, "inference_backend": "openvino", "evaluation_inference_backend": "training"}
        )
    )


def test_real_updates_resume_and_batched_evaluation(tmp_path):
    config = {
        "preset": "smoke",
        "evaluation_mode": "batched",
        "max_game_plies": 2,
        "evaluation_max_plies": 2,
        "output_dir": str(tmp_path / "first"),
        "registry_dir": str(tmp_path / "models"),
    }
    first = train(config)
    assert first["metrics"]["updates"] > 0
    resumed = train(
        {
            **config,
            "iterations": 2,
            "resume": first["checkpoint"],
            "output_dir": str(tmp_path / "resumed"),
        }
    )
    assert resumed["iterations"] == 2
    assert resumed["metrics"]["evaluation"]["performance"]["mode"] == "batched"


@pytest.mark.parametrize(
    "key,value",
    [
        ("evaluation_mode", "unknown"),
        ("evaluation_batch_size", 0),
        ("evaluation_concurrency", True),
        ("evaluation_inference_backend", "cuda"),
    ],
)
def test_reject_invalid_execution_settings(key, value):
    with pytest.raises(ValueError):
        normalize_config({key: value})


@pytest.mark.skipif(not torch.cuda.is_available(), reason="CUDA unavailable")
def test_cuda_auto_batches_both_models():
    model = PolicyValueNet("brandubh", 4, 0).cuda().eval()
    cfg = normalize_config(
        {
            "preset": "smoke",
            "evaluation_opponent": "champion",
            "evaluation_max_plies": 2,
            "evaluation_time_limit": 30,
        }
    )
    result = evaluate_model(model, cfg, previous=deepcopy(model))
    assert result["performance"]["mode"] == "batched"
    assert all(item["neural_evaluations"] for item in result["performance"]["models"])
