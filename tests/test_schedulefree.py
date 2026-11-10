"""Schedule-Free numerical, inference, checkpoint and continuation contracts.

CPU continuation uses rtol=1e-6, atol=1e-7 for tensors (mode interpolation can
round); scalar optimizer state/counters and non-timing history are exact.
"""

from copy import deepcopy
import json
import random
import sys

import pytest
import torch
from schedulefree import AdamWScheduleFree

from hnefatafl import training
from hnefatafl.ai import choose_move
from hnefatafl.engine import legal_moves, new_game
from hnefatafl.models import PolicyValueNet, load_checkpoint, save_checkpoint
from hnefatafl.training import _make_optimizer, normalize_config, optimize_replay, train, tune
from hnefatafl.training_optimizers import OptimizerLifecycle, schedule_free_available


def config(tmp_path, **changes):
    return {
        "preset": "smoke",
        "optimizer": "adamw_schedulefree",
        "schedulefree_warmup_steps": 13,
        "device": "cpu",
        "cpu_threads": 1,
        "mcts_time_limit": 5,
        "evaluation_time_limit": 5,
        "max_game_plies": 4,
        "evaluation_max_plies": 2,
        "iterations": 3,
        "output_dir": str(tmp_path / "run"),
        "registry_dir": str(tmp_path / "models"),
        **changes,
    }


def same(actual, expected):
    if isinstance(expected, torch.Tensor):
        torch.testing.assert_close(actual, expected, rtol=1e-6, atol=1e-7)
    elif isinstance(expected, dict):
        assert actual.keys() == expected.keys()
        for key in expected:
            same(actual[key], expected[key])
    elif isinstance(expected, (list, tuple)):
        assert len(actual) == len(expected)
        for left, right in zip(actual, expected):
            same(left, right)
    else:
        assert actual == expected


@pytest.mark.parametrize(
    "name,kind",
    [
        ("adamw", torch.optim.AdamW),
        ("adam", torch.optim.Adam),
        ("sgd", torch.optim.SGD),
        ("adamw_schedulefree", AdamWScheduleFree),
    ],
)
def test_factory_preserves_classes_and_configured_rates(tmp_path, name, kind):
    cfg = normalize_config(config(tmp_path, optimizer=name, learning_rate=0.0037))
    optimizer = _make_optimizer(torch.nn.Linear(2, 1), cfg)
    assert type(optimizer) is kind
    assert optimizer.param_groups[0]["lr"] == 0.0037
    assert optimizer.param_groups[0]["weight_decay"] == cfg["weight_decay"]
    if name == "adamw_schedulefree":
        group = optimizer.param_groups[0]
        assert group["warmup_steps"] == 13
        assert group["betas"] == (0.9, 0.999)
        assert group["eps"] == 1e-8
        assert group["r"] == 0 and group["weight_lr_power"] == 2
    else:
        before = deepcopy(optimizer.state_dict())
        lifecycle = OptimizerLifecycle(optimizer)
        lifecycle.train()
        lifecycle.inference()
        same(optimizer.state_dict(), before)
    assert normalize_config({})["optimizer"] == "adamw"
    assert normalize_config({})["schedulefree_warmup_steps"] == 0


@pytest.mark.parametrize("value", [-1, 1000001, 1.5, True, "12", None])
def test_warmup_validation(tmp_path, value):
    with pytest.raises(ValueError, match="schedulefree_warmup_steps must be an integer"):
        normalize_config(config(tmp_path, schedulefree_warmup_steps=value))


def test_invalid_optimizer_and_hypercontroller_rejected(tmp_path):
    with pytest.raises(ValueError, match="optimizer must be"):
        normalize_config(config(tmp_path, optimizer="unknown"))
    with pytest.raises(ValueError, match="HyperController is not supported yet"):
        normalize_config(config(tmp_path, adaptation="hypercontroller"))


def test_optional_dependency_and_real_capability_probe(tmp_path, monkeypatch):
    assert schedule_free_available()
    monkeypatch.setitem(sys.modules, "schedulefree", None)
    assert not schedule_free_available()
    with pytest.raises(ValueError, match=r"install hnefatafl\[train\]"):
        _make_optimizer(torch.nn.Linear(2, 1), normalize_config(config(tmp_path)))
    assert type(_make_optimizer(torch.nn.Linear(2, 1), normalize_config({}))) is torch.optim.AdamW


def test_training_loop_counts_only_successful_scaler_updates(tmp_path):
    cfg = normalize_config(config(tmp_path, batch_size=2))
    model = PolicyValueNet("brandubh", 4, 0)
    replay, _ = training.self_play(model, cfg, seed=0)
    optimizer = _make_optimizer(model, cfg)
    scaler = torch.amp.GradScaler("cpu", init_scale=8)
    calls = 0

    def nonfinite_once(gradient):
        nonlocal calls
        calls += 1
        return torch.full_like(gradient, float("inf")) if calls == 1 else gradient

    handle = next(model.parameters()).register_hook(nonfinite_once)
    # CPU autocast exercises the real GradScaler skip path in ordinary CI;
    # the application enables mixed precision only on CUDA.
    result = optimize_replay(
        model,
        optimizer,
        scaler,
        replay,
        cfg,
        torch.device("cpu"),
        True,
        random.Random(0),
        random.Random(1),
    )
    handle.remove()
    assert result["updates"] == optimizer.param_groups[0]["k"] == 1
    assert optimizer.param_groups[0]["scheduled_lr"] == cfg["learning_rate"] / 13
    assert all(torch.isfinite(p).all() for p in model.parameters())


def test_parameter_sequences_match_official_optimizer_and_switch_idempotently():
    model = torch.nn.Linear(2, 1, bias=False)
    reference = deepcopy(model)
    optimizer = AdamWScheduleFree(model.parameters(), lr=0.02, warmup_steps=6)
    official = AdamWScheduleFree(reference.parameters(), lr=0.02, warmup_steps=6)
    lifecycle = OptimizerLifecycle(optimizer)
    for index in range(4):
        with lifecycle.training_parameters(model):
            official.train()
            lifecycle.train()
            same(model.state_dict(), reference.state_dict())
            for net, opt in [(model, optimizer), (reference, official)]:
                opt.zero_grad()
                (net(torch.tensor([[index + 1.0, 0.5]])) ** 2).sum().backward()
                opt.step()
            trained = deepcopy(model.state_dict())
        official.eval()
        same(model.state_dict(), reference.state_dict())
        evaluated = deepcopy(model.state_dict())
        lifecycle.inference()
        same(model.state_dict(), evaluated)
    assert not torch.equal(trained["weight"], evaluated["weight"])
    before = deepcopy(optimizer.state_dict())
    lifecycle.set_base_learning_rate(0.007)
    before["param_groups"][0]["lr"] = 0.007
    same(optimizer.state_dict(), before)
    for value in [0.0, float("nan"), float("inf"), -0.01, 2]:
        with pytest.raises(ValueError, match="learning_rate"):
            lifecycle.set_base_learning_rate(value)


def test_failure_restores_evaluation_parameters_and_save_is_safe(tmp_path):
    model = PolicyValueNet("brandubh", 4, 0)
    optimizer = AdamWScheduleFree(model.parameters(), lr=0.01)
    lifecycle = OptimizerLifecycle(optimizer)
    with pytest.raises(RuntimeError, match="failure"):
        with lifecycle.training_parameters(model):
            for parameter in model.parameters():
                parameter.grad = torch.ones_like(parameter)
            optimizer.step()
            raise RuntimeError("failure")
    assert not optimizer.param_groups[0]["train_mode"]
    evaluated = deepcopy(model.state_dict())
    lifecycle.train()
    # save_checkpoint itself enforces the sequence, including direct callers.
    path = save_checkpoint(tmp_path / "safe.pt", model, {}, optimizer)
    loaded, payload = load_checkpoint(path)
    same(loaded.state_dict(), evaluated)
    same(payload["optimizer_state"], optimizer.state_dict())
    assert payload["optimizer_state"]["param_groups"][0]["train_mode"] is False
    assert torch.load(path, weights_only=True)["format_version"] == 1


def test_resume_interprets_recorded_optimizer_mode_before_inference(tmp_path):
    first = train(config(tmp_path / "source", iterations=1))
    model, saved = load_checkpoint(first["checkpoint"])
    evaluated = deepcopy(model.state_dict())
    optimizer = _make_optimizer(model, saved["metadata"]["training_config"])
    optimizer.load_state_dict(saved["optimizer_state"])
    optimizer.train()
    saved["state_dict"] = model.state_dict()
    saved["optimizer_state"] = optimizer.state_dict()
    legacy_train_mode = tmp_path / "recorded-train-mode.pt"
    torch.save(saved, legacy_train_mode)

    def stop_before_update(state):
        if state["stage"] == "self_play":
            raise InterruptedError()

    result = train(
        {
            "resume": str(legacy_train_mode),
            "iterations": 2,
            "output_dir": str(tmp_path / "continued"),
        },
        stop_before_update,
    )
    restored, payload = load_checkpoint(result["checkpoint"])
    same(restored.state_dict(), evaluated)
    assert payload["optimizer_state"]["param_groups"][0]["k"] == 1
    assert payload["optimizer_state"]["param_groups"][0]["train_mode"] is False


@pytest.mark.parametrize("mode", ["sequential", "batched"])
def test_real_brandubh_training_inference_sequences_and_exports(tmp_path, monkeypatch, mode):
    factory, generation, evaluation = (
        training._make_optimizer,
        training.generate_self_play,
        training.evaluate_model,
    )
    active = []
    evaluated = []

    def create(model, cfg):
        opt = factory(model, cfg)
        active[:] = [opt]
        return opt

    def assert_inference(model):
        opt = active[0]
        assert all(not group["train_mode"] for group in opt.param_groups)
        expected = deepcopy(model.state_dict())
        # A full round-trip stays within FP32 interpolation error, and exposes
        # an accidental train flag even when initial x == y.
        opt.train()
        opt.eval()
        same(model.state_dict(), expected)

    def generate(model, *args, **kwargs):
        assert_inference(model)
        return generation(model, *args, **kwargs)

    def evaluate(model, *args, **kwargs):
        assert_inference(model)
        evaluated.append(deepcopy(model.state_dict()))
        return evaluation(model, *args, **kwargs)

    monkeypatch.setattr(training, "_make_optimizer", create)
    monkeypatch.setattr(training, "generate_self_play", generate)
    monkeypatch.setattr(training, "evaluate_model", evaluate)
    cfg = config(tmp_path, self_play_mode=mode, evaluation_mode=mode, games=2)
    torch.manual_seed(0)
    before = deepcopy(PolicyValueNet("brandubh", 4, 0).state_dict())
    result = train(cfg)
    assert result["status"] == "completed" and len(evaluated) == 3
    model, payload = load_checkpoint(result["checkpoint"])
    assert any(not torch.equal(before[k], v) for k, v in model.state_dict().items())
    same(model.state_dict(), evaluated[-1])
    group = payload["optimizer_state"]["param_groups"][0]
    assert group["k"] == sum(row["updates"] for row in result["history"]) > 0
    assert group["train_mode"] is False
    for path in [
        result["best_checkpoint"],
        result["inference_checkpoint"],
        result["model"]["path"],
    ]:
        exported, data = load_checkpoint(path)
        same(exported.state_dict(), evaluated[data["metadata"]["version"] - 1])
        assert data["optimizer_state"] is None
        assert choose_move(
            new_game("brandubh"), {"algorithm": "neural", "model_path": path, "iterations": 1}
        ) in legal_moves(new_game("brandubh"))
    assert torch.isfinite(torch.tensor(result["metrics"]["policy_loss"]))
    diagnostics = result["metrics"]["optimizer"]
    assert diagnostics["completed_steps"] == group["k"]
    assert diagnostics["warmup_learning_rate"] == group["scheduled_lr"]
    assert len(json.dumps(training.compact_progress(result), allow_nan=False)) < 16000


@pytest.mark.parametrize("opponent", ["shield_bearer", "shield_maiden", "jarl", "champion"])
def test_all_evaluation_opponents(tmp_path, opponent):
    result = train(
        config(tmp_path, iterations=1, evaluation_opponent=opponent, evaluation_jarl_depth=1)
    )
    assert result["metrics"]["evaluation"]["opponent"] == opponent
    assert result["metrics"]["updates"] > 0


def test_parallel_self_play_and_evaluation_keep_inference_parameters(tmp_path):
    result = train(
        config(
            tmp_path,
            iterations=2,
            games=2,
            search_workers=1,
            self_play_mode="batched",
            evaluation_mode="batched",
        )
    )
    _, saved = load_checkpoint(result["checkpoint"])
    assert result["status"] == "completed"
    assert saved["optimizer_state"]["param_groups"][0]["train_mode"] is False
    assert saved["optimizer_state"]["param_groups"][0]["k"] == 3


def test_openvino_refresh_uses_averaged_weights(tmp_path):
    pytest.importorskip("openvino")
    result = train(
        config(
            tmp_path,
            iterations=2,
            inference_backend="openvino",
            inference_device="CPU",
            evaluation_inference_backend="training",
        )
    )
    assert result["status"] == "completed"
    assert result["metrics"]["updates"] > 0


def test_completed_iteration_continuation_preserves_full_state_and_history(tmp_path):
    uninterrupted = train(config(tmp_path / "whole"))
    first = train(config(tmp_path / "split", iterations=1))
    continued = train(
        {"resume": first["checkpoint"], "iterations": 3, "output_dir": str(tmp_path / "continued")}
    )
    whole_model, whole = load_checkpoint(uninterrupted["checkpoint"])
    split_model, split = load_checkpoint(continued["checkpoint"])
    same(split_model.state_dict(), whole_model.state_dict())
    same(split["optimizer_state"], whole["optimizer_state"])
    for name in ["replay", "python_rng", "torch_rng", "augmentation_rng", "grad_scaler"]:
        same(split["extra"][name], whole["extra"][name])
    group = split["optimizer_state"]["param_groups"][0]
    assert group["k"] == 4 and group["warmup_steps"] == 13
    assert group["weight_sum"] > 0 and group["lr_max"] > 0

    # Runtime/timing measurements are intentionally excluded; every learning
    # and evaluation field, including paired seeds and scores, must match.
    def learning(value):
        if isinstance(value, dict):
            return {
                k: learning(v)
                for k, v in value.items()
                if k != "performance" and "duration" not in k and not k.endswith("seconds")
            }
        if isinstance(value, list):
            return [learning(v) for v in value]
        return value

    same(learning(continued["history"]), learning(uninterrupted["history"]))


def test_resume_overrides_preserve_averages_and_warmup_is_immutable(tmp_path):
    first = train(config(tmp_path / "source", iterations=1))
    _, payload = load_checkpoint(first["checkpoint"])

    def progress(state):
        if state["stage"] == "self_play":
            raise InterruptedError()

    result = train(
        {
            "resume": first["checkpoint"],
            "iterations": 2,
            "learning_rate": 0.003,
            "weight_decay": 0.002,
            "output_dir": str(tmp_path / "override"),
        },
        progress,
    )
    _, resumed = load_checkpoint(result["checkpoint"])
    before = payload["optimizer_state"]
    before["param_groups"][0].update(lr=0.003, weight_decay=0.002)
    same(resumed["optimizer_state"], before)
    for changes, message in [
        ({"optimizer": "adamw"}, "optimizer must match"),
        ({"schedulefree_warmup_steps": 2}, "warmup_steps cannot change"),
    ]:
        with pytest.raises(ValueError, match=message):
            train(
                {
                    "resume": first["checkpoint"],
                    "iterations": 2,
                    "output_dir": str(tmp_path / "invalid"),
                    **changes,
                }
            )


@pytest.mark.parametrize("failure", [InterruptedError, RuntimeError])
def test_partial_optimization_failure_checkpoint_contains_averaged_weights(
    tmp_path, monkeypatch, failure
):
    original = training.optimize_replay

    def fail(*args, **kwargs):
        def on_epoch(epoch):
            raise failure("stop after actual updates")

        kwargs["on_epoch"] = on_epoch
        return original(*args, **kwargs)

    monkeypatch.setattr(training, "optimize_replay", fail)
    cfg = config(tmp_path)
    if failure is RuntimeError:
        with pytest.raises(RuntimeError):
            train(cfg)
    else:
        assert train(cfg)["status"] == "cancelled"
    _, saved = load_checkpoint(tmp_path / "run" / "last.pt")
    assert saved["optimizer_state"]["param_groups"][0]["k"] > 0
    assert not saved["optimizer_state"]["param_groups"][0]["train_mode"]


def test_champion_promotion_freezes_evaluated_weights_and_keeps_optimizer(tmp_path, monkeypatch):
    evaluated, incumbents, states = [], [], []
    factory = training._make_optimizer
    active = []

    def create(model, cfg):
        opt = factory(model, cfg)
        active[:] = [opt]
        return opt

    def evaluate(model, cfg, previous=None, **kwargs):
        assert not active[0].param_groups[0]["train_mode"]
        evaluated.append(deepcopy(model.state_dict()))
        incumbents.append(previous)
        states.append(deepcopy(active[0].state_dict()))
        return {
            "score": 1.0,
            "wins": 48,
            "losses": 0,
            "draws": 0,
            "truncated": 0,
            "games": [
                {"candidate_side": side, "winner": side, "score": 1.0}
                for _ in range(24)
                for side in ["attackers", "defenders"]
            ],
        }

    monkeypatch.setattr(training, "_make_optimizer", create)
    monkeypatch.setattr(training, "evaluate_model", evaluate)
    result = train(config(tmp_path, evaluation_opponent="champion", evaluation_games=48))
    assert all(row["evaluation"]["promoted"] for row in result["history"])
    assert [state["param_groups"][0]["k"] for state in states] == [1, 2, 4]
    same(incumbents[1].state_dict(), evaluated[0])
    same(incumbents[2].state_dict(), evaluated[1])
    assert all(not p.requires_grad for model in incumbents for p in model.parameters())
    model, saved = load_checkpoint(result["checkpoint"])
    same(saved["optimizer_state"], states[-1])
    same(saved["extra"]["evaluation_state"]["champion"]["snapshot"]["state_dict"], evaluated[-1])


def test_tuning_uses_isolated_resumable_schedulefree_trials(tmp_path):
    result = tune(
        {
            "trials": 2,
            "training": config(tmp_path, iterations=1),
            "output_dir": str(tmp_path / "study"),
            "search_space": {
                "learning_rate": {"type": "float", "low": 0.001, "high": 0.002},
                "weight_decay": {"type": "float", "low": 0, "high": 0.001},
            },
        }
    )
    assert result["status"] == "completed"
    checkpoints = list((tmp_path / "study").glob("trial-*/last.pt"))
    assert len(checkpoints) == 2
    for path in checkpoints:
        _, saved = load_checkpoint(path)
        group = saved["optimizer_state"]["param_groups"][0]
        assert group["k"] == 1 and group["train_mode"] is False


@pytest.mark.parametrize("name", ["adamw", "adam", "sgd", "adamw_schedulefree"])
def test_study_identity_keeps_legacy_optimizers_independent_of_new_inactive_settings(
    tmp_path, name
):
    cfg = normalize_config(config(tmp_path, optimizer=name))
    identity = training.study_identity(
        resuming=False,
        resume_payload=None,
        evaluation_base=cfg,
        search_space={},
        opponents=[],
        sampler={},
        pruner={},
    )
    if name == "adamw_schedulefree":
        assert identity["training"]["schedulefree_warmup_steps"] == 13
    else:
        assert "schedulefree_warmup_steps" not in identity["training"]
        legacy = {key: value for key, value in cfg.items() if key != "schedulefree_warmup_steps"}
        assert (
            training.study_identity(
                resuming=False,
                resume_payload=None,
                evaluation_base=legacy,
                search_space={},
                opponents=[],
                sampler={},
                pruner={},
            )
            == identity
        )


@pytest.mark.parametrize(
    "device",
    [
        "cpu",
        pytest.param(
            "cuda",
            marks=pytest.mark.skipif(
                not torch.cuda.is_available(), reason="CUDA hardware unavailable"
            ),
        ),
    ],
)
def test_amp_success_and_gradscaler_skip_preserve_warmup(device):
    model = torch.nn.Linear(2, 1).to(device)
    optimizer = AdamWScheduleFree(model.parameters(), lr=0.01, warmup_steps=8)
    lifecycle = OptimizerLifecycle(optimizer)
    scaler = torch.amp.GradScaler(device)
    for finite in [True, False, True]:
        with lifecycle.training_parameters(model):
            optimizer.zero_grad()
            with torch.autocast(device):
                loss = model(torch.ones(2, 2, device=device)).square().mean()
            scaler.scale(loss).backward()
            if not finite:
                next(model.parameters()).grad.fill_(float("inf"))
            scaler.unscale_(optimizer)
            torch.nn.utils.clip_grad_norm_(model.parameters(), 5)
            before = deepcopy(optimizer.state_dict())
            scaler.step(optimizer)
            scaler.update()
            if not finite:
                same(optimizer.state_dict(), before)
    assert lifecycle.completed_steps() == 2
    assert optimizer.param_groups[0]["scheduled_lr"] == 0.01 * 2 / 8
