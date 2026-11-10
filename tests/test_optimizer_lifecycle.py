"""An upstream in-place mode switch cannot always be rolled back safely."""

from copy import deepcopy
import json

import pytest
import torch
from schedulefree import AdamWScheduleFree

from hnefatafl import training
from hnefatafl.models import PolicyValueNet, save_checkpoint
from hnefatafl.training_optimizers import OptimizerLifecycle
from test_schedulefree import config, same


def initialized():
    model = PolicyValueNet("brandubh", 4, 0)
    optimizer = AdamWScheduleFree(model.parameters(), lr=0.01)
    lifecycle = OptimizerLifecycle(optimizer)
    for _ in range(2):
        with lifecycle.training_parameters(model):
            for parameter in model.parameters():
                parameter.grad = torch.ones_like(parameter)
            optimizer.step()
    return model, optimizer, lifecycle


@pytest.mark.parametrize("partially_switched", [False, True])
def test_entry_failure_preserves_exception_and_blocks_unprovable_recovery(
    tmp_path, monkeypatch, partially_switched
):
    model, optimizer, lifecycle = initialized()
    before = deepcopy(model.state_dict())
    checkpoint = save_checkpoint(tmp_path / "valid.pt", model, {}, optimizer)
    saved_bytes = checkpoint.read_bytes()
    error = ValueError("upstream train failure")
    evaluation_calls = []

    def failed_entry():
        if partially_switched:
            group = optimizer.param_groups[0]
            parameter = group["params"][0]
            with torch.no_grad():
                parameter.lerp_(optimizer.state[parameter]["z"], 1 - group["betas"][0])
        raise error

    monkeypatch.setattr(optimizer, "train", failed_entry)
    monkeypatch.setattr(optimizer, "eval", lambda: evaluation_calls.append(True))
    with pytest.raises(ValueError) as raised:
        with lifecycle.training_parameters(model):
            pytest.fail("The training body must not run after failed mode entry")
    assert raised.value is error
    assert not model.training
    assert evaluation_calls == []  # train_mode may still be False despite changed weights.
    assert optimizer.param_groups[0]["train_mode"] is False
    assert "cannot be guaranteed" in " ".join(error.__notes__)
    assert OptimizerLifecycle(optimizer).mode_failure is not None
    if partially_switched:
        assert any(not torch.equal(before[k], v) for k, v in model.state_dict().items())
    else:
        same(model.state_dict(), before)
    # New wrappers and direct saves must also refuse the live unsafe state.
    for operation in (
        lifecycle.train,
        lifecycle.inference,
        lambda: lifecycle.set_base_learning_rate(0.001),
        lambda: save_checkpoint(checkpoint, model, {}, optimizer),
    ):
        with pytest.raises(RuntimeError, match="checkpoint writing is blocked"):
            operation()
    assert checkpoint.read_bytes() == saved_bytes
    assert "_hnefatafl_mode_failure" not in optimizer.state_dict()


@pytest.mark.parametrize("body_fails", [False, True])
def test_evaluation_cleanup_does_not_mask_the_primary_failure(monkeypatch, body_fails):
    model, optimizer, lifecycle = initialized()
    primary = ValueError("gradient update failed")
    cleanup = RuntimeError("optimizer eval failed")

    def failed_eval():
        raise cleanup

    monkeypatch.setattr(optimizer, "eval", failed_eval)
    with pytest.raises((ValueError, RuntimeError)) as raised:
        with lifecycle.training_parameters(model):
            if body_fails:
                raise primary
    assert raised.value is (primary if body_fails else cleanup)
    assert not model.training
    assert lifecycle.mode_failure is not None
    if body_fails:
        assert "optimizer eval failed" in " ".join(primary.__notes__)
        assert "checkpoint writing is blocked" in " ".join(primary.__notes__)


def test_model_cleanup_cannot_mask_an_optimization_failure(monkeypatch):
    model, optimizer, lifecycle = initialized()
    primary = ValueError("gradient update failed")

    def failed_eval():
        raise RuntimeError("model eval failed")

    monkeypatch.setattr(model, "eval", failed_eval)
    with pytest.raises(ValueError) as raised:
        with lifecycle.training_parameters(model):
            raise primary
    assert raised.value is primary
    assert "model eval failed" in " ".join(primary.__notes__)
    assert lifecycle.mode_failure is not None


@pytest.mark.parametrize("optimizer_class", [torch.optim.AdamW, torch.optim.Adam, torch.optim.SGD])
def test_ordinary_optimizer_context_retains_existing_modes(optimizer_class):
    model = torch.nn.Linear(2, 1).eval()
    optimizer = optimizer_class(model.parameters(), lr=0.01)
    lifecycle = OptimizerLifecycle(optimizer)
    before = deepcopy(optimizer.state_dict())
    error = RuntimeError("ordinary failure")
    with pytest.raises(RuntimeError) as raised:
        with lifecycle.training_parameters(model):
            assert model.training
            raise error
    assert raised.value is error
    assert model.training  # Ordinary callers have always owned model.eval().
    assert lifecycle.mode_failure is None
    same(optimizer.state_dict(), before)


@pytest.mark.parametrize("transition", ["train", "eval"])
def test_training_failure_does_not_write_an_unsafe_checkpoint(tmp_path, monkeypatch, transition):
    factory = training._make_optimizer
    original = RuntimeError(f"upstream {transition} failure")

    def create(model, cfg):
        optimizer = factory(model, cfg)

        def fail():
            raise original

        if transition == "train":
            optimizer.train = fail
        else:
            actual_eval = optimizer.eval

            def fail_after_training():
                if optimizer.param_groups[0]["train_mode"]:
                    raise original
                actual_eval()

            optimizer.eval = fail_after_training
        return optimizer

    monkeypatch.setattr(training, "_make_optimizer", create)
    with pytest.raises(RuntimeError) as raised:
        training.train(config(tmp_path, iterations=1))
    assert raised.value is original
    assert not (tmp_path / "run" / "last.pt").exists()
    assert not (tmp_path / "run" / "inference.pt").exists()
    progress = json.loads((tmp_path / "run" / "progress.json").read_text())
    assert progress["status"] == "failed"
    assert progress["checkpoint"] is None
    assert "checkpoint writing is blocked" in progress["error"]


def test_mode_failure_retains_the_previous_completed_checkpoint(tmp_path, monkeypatch):
    factory = training._make_optimizer
    original = RuntimeError("second mode entry failed")
    saved_bytes = []

    def create(model, cfg):
        optimizer = factory(model, cfg)
        real_train = optimizer.train
        entries = 0

        def enter():
            nonlocal entries
            entries += 1
            if entries == 2:
                # Simulate the upstream loop changing a parameter before its
                # group flag can be committed, after an earlier valid iteration.
                parameter = optimizer.param_groups[0]["params"][0]
                with torch.no_grad():
                    parameter.add_(1)
                raise original
            real_train()

        optimizer.train = enter
        return optimizer

    def progress(state):
        if state["stage"] == "iteration_complete" and state["iteration"] == 1:
            saved_bytes[:] = [(tmp_path / "run" / "last.pt").read_bytes()]

    monkeypatch.setattr(training, "_make_optimizer", create)
    with pytest.raises(RuntimeError) as raised:
        training.train(config(tmp_path, iterations=2), progress)
    assert raised.value is original
    assert (tmp_path / "run" / "last.pt").read_bytes() == saved_bytes[0]
    # The transient failure marker is not carried into a fresh resumed optimizer.
    from hnefatafl.models import load_checkpoint

    model, payload = load_checkpoint(tmp_path / "run" / "last.pt")
    optimizer = factory(model, payload["metadata"]["training_config"])
    optimizer.load_state_dict(payload["optimizer_state"])
    assert OptimizerLifecycle(optimizer).mode_failure is None
    assert not optimizer.param_groups[0]["train_mode"]
