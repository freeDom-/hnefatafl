"""Conditional CUDA acceptance checks; see docs/SCHEDULEFREE_CUDA.md.

These run real CUDA FP32/AMP jobs. Promotion alone uses the existing deterministic
paired-score fixture, so the checks assert lifecycle correctness, not strength.
"""

from copy import deepcopy
import os

import pytest
import torch

from hnefatafl import training
from hnefatafl.ai import choose_move
from hnefatafl.engine import legal_moves, new_game
from hnefatafl.models import PolicyValueNet, load_checkpoint
from hnefatafl.training_evaluation import EvaluationSelection, frozen_model
from hnefatafl.training_optimizers import OptimizerLifecycle
from test_schedulefree import config as base_config
from test_training_evaluation import match

pytestmark = pytest.mark.skipif(not torch.cuda.is_available(), reason="CUDA hardware unavailable")


@pytest.fixture(params=[False, True], ids=["fp32", "amp"])
def cuda_precision(request):
    if os.environ.get("CUBLAS_WORKSPACE_CONFIG") not in {":4096:8", ":16:8"}:
        pytest.fail("Set CUBLAS_WORKSPACE_CONFIG=:4096:8 before starting CUDA validation")
    deterministic = torch.are_deterministic_algorithms_enabled()
    warn_only = torch.is_deterministic_algorithms_warn_only_enabled()
    precision = torch.get_float32_matmul_precision()
    cudnn_flags = (
        torch.backends.cudnn.benchmark,
        torch.backends.cudnn.deterministic,
        torch.backends.cudnn.allow_tf32,
    )
    matmul_tf32 = torch.backends.cuda.matmul.allow_tf32
    try:
        torch.use_deterministic_algorithms(True)
        torch.set_float32_matmul_precision("highest")
        torch.backends.cudnn.benchmark = False
        torch.backends.cudnn.deterministic = True
        torch.backends.cudnn.allow_tf32 = False
        torch.backends.cuda.matmul.allow_tf32 = False
        yield request.param
    finally:
        torch.use_deterministic_algorithms(deterministic, warn_only=warn_only)
        torch.set_float32_matmul_precision(precision)
        (
            torch.backends.cudnn.benchmark,
            torch.backends.cudnn.deterministic,
            torch.backends.cudnn.allow_tf32,
        ) = cudnn_flags
        torch.backends.cuda.matmul.allow_tf32 = matmul_tf32


def settings(tmp_path, mixed, **changes):
    return base_config(
        tmp_path,
        **{
            "device": "cuda",
            "mixed_precision": mixed,
            "epochs": 4,
            "batch_size": 2,
            "games": 2,
            "self_play_mode": "batched",
            "evaluation_mode": "batched",
            "schedulefree_warmup_steps": 32,
            **changes,
        },
    )


def assert_state(actual, expected, mixed, *, history=False):
    rtol, atol = (1e-5, 1e-6) if mixed else (1e-6, 1e-7)
    if isinstance(expected, torch.Tensor):
        torch.testing.assert_close(
            actual.detach().cpu(), expected.detach().cpu(), rtol=rtol, atol=atol
        )
    elif isinstance(expected, dict):
        keys = {
            key
            for key in expected
            if not history
            or (
                key not in {"performance", "training_parameters", "training_parameters_iteration"}
                and "duration" not in key
                and not key.endswith("seconds")
            )
        }
        if history:
            assert keys <= actual.keys()
        else:
            assert actual.keys() == expected.keys()
        for key in keys:
            assert_state(actual[key], expected[key], mixed, history=history)
    elif isinstance(expected, (list, tuple)):
        assert len(actual) == len(expected)
        for left, right in zip(actual, expected):
            assert_state(left, right, mixed, history=history)
    elif history and isinstance(expected, float):
        assert actual == pytest.approx(expected, rel=rtol, abs=atol)
    else:
        assert actual == expected


def weights(model):
    return {key: value.detach().cpu().clone() for key, value in model.state_dict().items()}


def test_cuda_training_resume_batched_averages_and_exports(tmp_path, monkeypatch, cuda_precision):
    mixed = cuda_precision
    factory, generate, optimize, evaluate = (
        training._make_optimizer,
        training.generate_self_play,
        training.optimize_replay,
        training.evaluate_model,
    )
    active = []
    averaged = {}
    evaluated = {}

    def create(model, config):
        optimizer = factory(model, config)
        active[:] = [optimizer]
        return optimizer

    def generate_averages(model, config, **kwargs):
        assert all(not group["train_mode"] for group in active[0].param_groups)
        if config["output_dir"] in averaged:
            assert_state(model.state_dict(), averaged[config["output_dir"]], mixed)
        return generate(model, config, **kwargs)

    def optimize_then_average(model, optimizer, scaler, replay, config, *args, **kwargs):
        result = optimize(model, optimizer, scaler, replay, config, *args, **kwargs)
        assert not optimizer.param_groups[0]["train_mode"]
        averaged[config["output_dir"]] = weights(model)
        return result

    def evaluate_averages(model, config, **kwargs):
        assert all(not group["train_mode"] for group in active[0].param_groups)
        assert_state(model.state_dict(), averaged[config["output_dir"]], mixed)
        evaluated.setdefault(config["output_dir"], []).append(weights(model))
        return evaluate(model, config, **kwargs)

    monkeypatch.setattr(training, "_make_optimizer", create)
    monkeypatch.setattr(training, "generate_self_play", generate_averages)
    monkeypatch.setattr(training, "optimize_replay", optimize_then_average)
    monkeypatch.setattr(training, "evaluate_model", evaluate_averages)
    whole = training.train(settings(tmp_path / "whole", mixed))
    stop = False

    def completed_first_iteration(state):
        nonlocal stop
        if state["stage"] == "iteration_complete" and state["iteration"] == 1:
            stop = True

    first = training.train(
        settings(tmp_path / "first", mixed), completed_first_iteration, lambda: stop
    )
    assert first["status"] == "cancelled"
    continued = training.train(
        {"resume": first["checkpoint"], "iterations": 3, "output_dir": str(tmp_path / "continued")}
    )
    whole_model, whole_state = load_checkpoint(whole["checkpoint"])
    resumed_model, resumed = load_checkpoint(continued["checkpoint"])
    assert_state(resumed_model.state_dict(), whole_model.state_dict(), mixed)
    assert_state(resumed["optimizer_state"], whole_state["optimizer_state"], mixed)
    for key in ["replay", "python_rng", "augmentation_rng", "torch_rng", "cuda_rng", "grad_scaler"]:
        assert_state(resumed["extra"][key], whole_state["extra"][key], mixed)
    assert_state(continued["history"], whole["history"], mixed, history=True)
    whole_events = [row for row in whole["history"] if "training_parameters" in row]
    resumed_events = [row for row in continued["history"] if "training_parameters" in row]
    assert [row["iteration"] for row in whole_events] == [1]
    assert [row["iteration"] for row in resumed_events] == [1, 2]
    assert all(
        row["training_parameters"] == whole_events[0]["training_parameters"]
        for row in resumed_events
    )
    group = resumed["optimizer_state"]["param_groups"][0]
    assert group["k"] == sum(point["updates"] for point in continued["history"]) > 0
    assert group["warmup_steps"] == 32 and group["train_mode"] is False
    assert group["scheduled_lr"] == group["lr"] * min(1, group["k"] / 32)
    initial = evaluated[whole["output_dir"]][0]
    assert any(not torch.equal(initial[k], value) for k, value in whole_model.state_dict().items())
    for path in [whole["best_checkpoint"], whole["inference_checkpoint"], whole["model"]["path"]]:
        exported, payload = load_checkpoint(path)
        assert_state(
            exported.state_dict(),
            evaluated[whole["output_dir"]][payload["metadata"]["version"] - 1],
            mixed,
        )
        assert payload["optimizer_state"] is None
        assert choose_move(
            new_game("brandubh"), {"algorithm": "neural", "model_path": path, "iterations": 1}
        ) in legal_moves(new_game("brandubh"))
    # Exercise the real CUDA Champion evaluator as well as the controlled
    # promotion test below. Evaluation must leave both frozen weights unchanged.
    candidate = whole_model.cuda()
    incumbent = frozen_model(candidate, {"state_dict": initial})
    before_candidate, before_incumbent = weights(candidate), weights(incumbent)
    result = evaluate(
        candidate,
        training.normalize_config(
            settings(tmp_path / "champion", mixed, evaluation_opponent="champion")
        ),
        previous=incumbent,
    )
    assert result["opponent"] == "champion"
    assert_state(weights(candidate), before_candidate, mixed)
    assert_state(weights(incumbent), before_incumbent, mixed)


def test_cuda_nonfinite_gradients_and_successful_step_counters(tmp_path, cuda_precision):
    import random

    mixed = cuda_precision
    config = training.normalize_config(settings(tmp_path, mixed, epochs=1, games=1))
    model = PolicyValueNet("brandubh", 4, 0).cuda()
    replay, _ = training.self_play(model, config, seed=0)
    optimizer = training._make_optimizer(model, config)
    scaler = torch.amp.GradScaler("cuda", enabled=mixed, init_scale=32)
    before = weights(model)
    calls = 0

    def nonfinite_once(gradient):
        nonlocal calls
        calls += 1
        return torch.full_like(gradient, float("inf")) if calls == 1 else gradient

    handle = next(model.parameters()).register_hook(nonfinite_once)

    def optimize():
        return training.optimize_replay(
            model,
            optimizer,
            scaler,
            replay,
            config,
            torch.device("cuda"),
            mixed,
            random.Random(0),
            random.Random(1),
        )

    try:
        if mixed:
            result = optimize()
            assert result["updates"] == optimizer.param_groups[0]["k"] == 1
            assert scaler.get_scale() == 16
            assert optimizer.param_groups[0]["scheduled_lr"] == config["learning_rate"] / 32
        else:
            with pytest.raises(ValueError, match="Non-finite training gradients"):
                optimize()
            assert optimizer.param_groups[0]["k"] == 0
            assert_state(weights(model), before, mixed)
    finally:
        handle.remove()
    assert not optimizer.param_groups[0]["train_mode"]
    assert OptimizerLifecycle(optimizer).mode_failure is None
    assert all(torch.isfinite(p).all() for p in model.parameters())


def test_cuda_champion_promotion_keeps_optimizer_and_frozen_weights(
    tmp_path, monkeypatch, cuda_precision
):
    mixed = cuda_precision
    factory, accept = training._make_optimizer, EvaluationSelection.accept
    active, candidates, incumbents = [], [], []

    def create(model, config):
        optimizer = factory(model, config)
        active[:] = [optimizer]
        return optimizer

    def evaluate(model, config, previous=None, **kwargs):
        assert not active[0].param_groups[0]["train_mode"]
        candidates.append(weights(model))
        incumbents.append(previous)
        return match([1] * 48)

    def promote(selection, model, metadata, evaluation, config):
        before = deepcopy(active[0].state_dict())
        result = accept(selection, model, metadata, evaluation, config)
        assert_state(active[0].state_dict(), before, mixed)
        return result

    monkeypatch.setattr(training, "_make_optimizer", create)
    monkeypatch.setattr(training, "evaluate_model", evaluate)
    monkeypatch.setattr(EvaluationSelection, "accept", promote)
    result = training.train(
        settings(tmp_path, mixed, evaluation_opponent="champion", evaluation_games=48)
    )
    assert all(point["evaluation"]["promoted"] for point in result["history"])
    for index in [1, 2]:
        assert_state(incumbents[index].state_dict(), candidates[index - 1], mixed)
        assert all(not p.requires_grad for p in incumbents[index].parameters())
    _, state = load_checkpoint(result["checkpoint"])
    assert state["optimizer_state"]["param_groups"][0]["k"] == sum(
        point["updates"] for point in result["history"]
    )
    assert_state(
        state["extra"]["evaluation_state"]["champion"]["snapshot"]["state_dict"],
        candidates[-1],
        mixed,
    )
