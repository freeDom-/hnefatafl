"""FP32 parity uses atol=3e-5, rtol=1e-4 across framework kernels.

Portable integration uses the diagnostic CPU plugin. GPU tests explicitly skip
without operational Intel hardware; CPU results establish no GPU speed claim.
"""

from dataclasses import replace
import json
import sys

import pytest
import torch

from hnefatafl.engine import apply_move, legal_moves, new_game
from hnefatafl.inference import policy_value_batch
from hnefatafl.inference_backends import (
    OpenVINOInference,
    create_inference,
    openvino_capabilities,
    supports_openvino,
)
from hnefatafl.models import PolicyValueNet, encode_state, load_checkpoint, save_checkpoint
from hnefatafl.training import generate_self_play, normalize_config, self_play, train


@pytest.fixture(
    scope="module", params=[("brandubh", 32, 2), ("tablut", 48, 3), ("copenhagen", 64, 4)]
)
def pair(request):
    pytest.importorskip(
        "openvino", reason="Install the optional openvino extra for numerical parity"
    )
    previous = torch.get_num_threads()
    torch.set_num_threads(2)
    torch.manual_seed(17)
    ruleset, channels, blocks = request.param
    model = PolicyValueNet(ruleset, channels, blocks).eval()
    backend = OpenVINOInference(
        model,
        {
            "inference_device": "CPU",
            "games": 32,
            "inference_batch_size": 32,
            "self_play_concurrency": 32,
        },
    )
    backend.refresh()
    yield model, backend
    backend.close()
    torch.set_num_threads(previous)


def positions(ruleset):
    state = new_game(ruleset)
    states = [state, replace(state, turn="defenders"), replace(state, turn="attackers")]
    for index in range(10):
        if state.winner:
            break
        moves = legal_moves(state)
        state = apply_move(state, moves[(index * 7) % len(moves)])
        states.append(state)
    return states


@pytest.mark.parametrize("batch_size", [1, 8, 16, 32, 3, 19, 1])
def test_fp32_logits_and_legal_semantics(pair, batch_size):
    model, backend = pair
    pool = positions(model.ruleset)
    states = [pool[index % len(pool)] for index in range(batch_size)]
    encoded = torch.stack([encode_state(state) for state in states])
    with torch.inference_mode():
        expected = model(encoded)
        actual = backend.forward(encoded)
        repeated = backend.forward(encoded)
    for reference, observed, again in zip(expected, actual, repeated):
        assert observed.shape == reference.shape
        assert torch.isfinite(observed).all()
        torch.testing.assert_close(observed, reference, atol=3e-5, rtol=1e-4)
        torch.testing.assert_close(observed, again, atol=1e-7, rtol=1e-6)
    reference = policy_value_batch(states, model)
    observed = policy_value_batch(states, model, backend=backend)
    for state, (priors, value), (expected_priors, expected_value) in zip(
        states, observed, reference
    ):
        assert list(priors) == legal_moves(state)
        assert sum(priors.values()) == pytest.approx(1, abs=1e-6)
        assert all(0 <= probability <= 1 for probability in priors.values())
        assert list(priors.values()) == pytest.approx(
            list(expected_priors.values()), abs=3e-5, rel=1e-4
        )
        assert value == pytest.approx(expected_value, abs=3e-5, rel=1e-4)


def test_terminal_and_immediate_win(pair):
    model, backend = pair
    start = new_game(model.ruleset)
    terminal = [replace(start, winner=winner) for winner in ("attackers", "defenders", "draw")]
    assert policy_value_batch(terminal, model, backend=backend) == [
        ({}, -1.0),
        ({}, 1.0),
        ({}, 0.0),
    ]
    board = ["."] * start.size**2
    board[start.size + 1 if model.ruleset == "tablut" else 1] = "K"
    board[3 * start.size + 3] = "A"
    board[2 * start.size + 2] = "D"
    winning = replace(start, board=tuple(board), turn="defenders")
    assert any(apply_move(winning, move).winner == "defenders" for move in legal_moves(winning))
    actual = policy_value_batch([winning], model, backend=backend)[0]
    expected = policy_value_batch([winning], model)[0]
    assert actual[0] == pytest.approx(expected[0], abs=3e-5)
    assert actual[1] == pytest.approx(expected[1], abs=3e-5)


def test_defaults_and_validation():
    for device in ("cpu", "cuda", "mps", "auto"):
        config = normalize_config({"preset": "smoke", "device": device})
        assert config["inference_backend"] == "pytorch"
        assert config["inference_device"] == "auto"
    for changes in (
        {"inference_backend": "unknown"},
        {"inference_backend": "openvino", "device": "cuda"},
        {"inference_backend": "openvino", "inference_device": "AUTO:GPU,CPU"},
        {"inference_backend": "openvino", "inference_device": 1},
        {"inference_backend": "pytorch", "device": "cpu", "inference_device": "GPU"},
    ):
        with pytest.raises(ValueError):
            normalize_config(changes)


def test_missing_installation(monkeypatch):
    monkeypatch.setitem(sys.modules, "openvino", None)
    model = PolicyValueNet("brandubh", 4, 0)
    config = normalize_config({"preset": "smoke", "device": "cpu", "inference_backend": "openvino"})
    with pytest.raises(ValueError, match="uv sync --extra train --extra openvino"):
        create_inference(model, config)
    with pytest.raises(ValueError, match="uv sync --extra train --extra openvino"):
        self_play(model, config, seed=17)
    assert (
        create_inference(model, {"inference_backend": "pytorch"}).info["inference_backend"]
        == "pytorch"
    )


def test_probe_failure_is_bounded_and_cached(monkeypatch):
    import hnefatafl.inference_backends as module

    calls = []
    monkeypatch.setattr(module, "version", lambda _: "test")

    def crash(*args, **kwargs):
        calls.append(1)
        return type("Process", (), {"returncode": -11, "stderr": "native crash"})()

    monkeypatch.setattr(module.subprocess, "run", crash)
    openvino_capabilities.cache_clear()
    try:
        result = openvino_capabilities()
        assert result["inference_backends"] == ["pytorch"]
        assert "native crash" in result["openvino_error"]
        assert openvino_capabilities() == result
        assert len(calls) == 1
    finally:
        openvino_capabilities.cache_clear()


def test_missing_gpu(monkeypatch):
    ov = pytest.importorskip("openvino")
    monkeypatch.setattr(ov, "Core", lambda: type("Core", (), {"available_devices": ["CPU"]})())
    with pytest.raises(ValueError, match="GPU unavailable"):
        OpenVINOInference(PolicyValueNet("brandubh", 4, 0), normalize_config({"preset": "smoke"}))


def test_device_matching():
    capabilities = {
        "inference_backends": ["pytorch", "openvino"],
        "openvino_devices": [{"id": "GPU.0", "available": True}, {"id": "CPU", "available": True}],
    }
    for target in ("GPU", "GPU.0", "auto", "CPU"):
        assert supports_openvino(capabilities, target)
    assert not supports_openvino(capabilities, "GPU.1")
    assert not supports_openvino({}, "GPU")
    assert not supports_openvino({**capabilities, "inference_backends": ["pytorch"]}, "GPU")


def tiny(tmp_path, **changes):
    return normalize_config(
        {
            "preset": "smoke",
            "device": "cpu",
            "cpu_threads": 2,
            "games": 3,
            "iterations": 2,
            "max_game_plies": 2,
            "evaluation_max_plies": 2,
            "mcts_time_limit": 30,
            "inference_backend": "openvino",
            "inference_device": "CPU",
            "output_dir": str(tmp_path / "training"),
            "registry_dir": str(tmp_path / "models"),
            **changes,
        }
    )


def test_refresh_checkpoint_training_resume_and_failure(tmp_path, monkeypatch):
    pytest.importorskip("openvino")
    config = tiny(tmp_path)
    refreshed = []
    original = OpenVINOInference.refresh

    def refresh(self):
        timing = original(self)
        encoded = encode_state(new_game("brandubh")).unsqueeze(0)
        with torch.inference_mode():
            torch.testing.assert_close(
                self.forward(encoded)[1], self.model(encoded)[1], atol=3e-5, rtol=1e-4
            )
        refreshed.append(self.model.value_head[-2].weight.detach().clone())
        return timing

    monkeypatch.setattr(OpenVINOInference, "refresh", refresh)
    result = train(config)
    assert result["status"] == "completed"
    assert len(refreshed) == 2
    assert not torch.equal(refreshed[0], refreshed[1])
    perf = result["metrics"]["performance"]
    assert perf["self_play_mode"] == "batched"
    assert perf["inference_refresh_count"] == 2
    assert perf["inference_refresh_seconds"] >= perf["inference_compilation_seconds"] > 0
    assert perf["inference_backend"] == "openvino"
    assert "inference_cuda_seconds" not in perf
    assert result["metrics"]["updates"] > 0
    _, saved = load_checkpoint(result["checkpoint"])
    assert saved["optimizer_state"] and saved["extra"]["replay"]
    assert saved["metadata"]["training_config"]["inference_backend"] == "openvino"
    # Full checkpoints retain only PyTorch tensors/primitives, safe model registration worked.
    assert result["model"]["id"]
    resumed = train(
        {"resume": result["checkpoint"], "output_dir": str(tmp_path / "resumed"), "iterations": 3}
    )
    assert resumed["iteration"] == 3 and len(resumed["history"]) == 3
    cpu = train(
        {
            "resume": resumed["checkpoint"],
            "output_dir": str(tmp_path / "cpu"),
            "iterations": 4,
            "inference_backend": "pytorch",
            "inference_device": "auto",
        }
    )
    assert cpu["iteration"] == 4
    assert cpu["metrics"]["performance"]["inference_backend"] == "pytorch"
    source = (tmp_path / "cpu" / "last.pt").read_bytes()

    def broken(self):
        raise RuntimeError("conversion failed")

    monkeypatch.setattr(OpenVINOInference, "refresh", broken)
    with pytest.raises(RuntimeError, match="conversion failed"):
        train(
            {
                "resume": cpu["checkpoint"],
                "output_dir": str(tmp_path / "failed"),
                "iterations": 5,
                "inference_backend": "openvino",
                "inference_device": "CPU",
            }
        )
    assert (tmp_path / "cpu" / "last.pt").read_bytes() == source
    _, failure = load_checkpoint(tmp_path / "failed" / "last.pt")
    assert failure["extra"]["iteration"] == 4


def test_explicit_sequential_and_cancellation(tmp_path):
    pytest.importorskip("openvino")
    config = tiny(tmp_path, self_play_mode="sequential")
    model = PolicyValueNet("brandubh", 4, 0)
    games, perf = generate_self_play(model, config, seed=17)
    assert len(games) == 3 and perf["self_play_mode"] == "sequential"
    assert perf["inference_batch_size_max"] == 1
    stages = []
    result = train(
        config,
        progress=lambda state: stages.append(state["stage"]),
        should_stop=lambda: "self_play" in stages,
    )
    assert result["status"] == "cancelled"
    load_checkpoint(result["checkpoint"])


def test_saved_weights_and_stale_snapshot(tmp_path):
    pytest.importorskip("openvino")
    torch.set_num_threads(2)
    model = PolicyValueNet("brandubh", 4, 0).eval()
    path = save_checkpoint(tmp_path / "saved.pt", model, {})
    restored, _ = load_checkpoint(path)
    backend = create_inference(restored, tiny(tmp_path))
    backend.refresh()
    encoded = encode_state(new_game("brandubh")).unsqueeze(0)
    old = backend.forward(encoded)[1].clone()
    with torch.no_grad():
        restored.value_head[-2].bias.add_(0.7)
    assert torch.equal(backend.forward(encoded)[1], old)
    backend.refresh()
    with torch.inference_mode():
        new = backend.forward(encoded)[1]
        assert not torch.allclose(new, old)
        torch.testing.assert_close(new, restored(encoded)[1], atol=3e-5, rtol=1e-4)
    backend.close()
    with pytest.raises(RuntimeError, match="refreshed"):
        backend.forward(encoded)


def test_intel_gpu_hardware(tmp_path):
    capabilities = openvino_capabilities()
    if not supports_openvino(capabilities, "GPU"):
        pytest.skip("Operational Intel OpenVINO GPU unavailable on this host")
    config = tiny(tmp_path, inference_device="GPU", iterations=1)
    result = train(config)
    assert result["status"] == "completed"
    assert result["inference_device"] == "GPU"
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize(
    "ruleset,channels,blocks", [("brandubh", 32, 2), ("tablut", 48, 3), ("copenhagen", 64, 4)]
)
def test_intel_gpu_fp32_parity(ruleset, channels, blocks):
    if not supports_openvino(openvino_capabilities(), "GPU"):
        pytest.skip("Operational Intel OpenVINO GPU unavailable for hardware FP32 parity")
    torch.manual_seed(19)
    model = PolicyValueNet(ruleset, channels, blocks).eval()
    backend = create_inference(
        model,
        {
            "inference_backend": "openvino",
            "inference_device": "GPU",
            "games": 32,
            "self_play_concurrency": 32,
            "inference_batch_size": 32,
        },
    )
    try:
        backend.refresh()
        pool = positions(ruleset)
        for size in (1, 8, 16, 32, 3, 19):
            batch = torch.stack([encode_state(pool[index % len(pool)]) for index in range(size)])
            with torch.inference_mode():
                expected = model(batch)
                observed = backend.forward(batch)
            for reference, actual in zip(expected, observed):
                torch.testing.assert_close(actual, reference, atol=3e-5, rtol=1e-4)
    finally:
        backend.close()
