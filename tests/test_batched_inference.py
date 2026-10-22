"""Batched inference retains legal priors and values without terminal GPU work."""

from dataclasses import replace
from types import SimpleNamespace

import pytest
import torch

from hnefatafl.ai import policy_value
from hnefatafl.engine import apply_move, legal_moves, new_game
from hnefatafl.inference import InferenceMetrics, policy_value_batch
from hnefatafl.models import PolicyValueNet


@pytest.mark.parametrize("ruleset", ["brandubh", "tablut", "copenhagen"])
def test_batch_matches_individual_inference_with_ragged_legal_masks(ruleset):
    torch.manual_seed(17)
    model = PolicyValueNet(ruleset, channels=4, blocks=1).eval()
    state = new_game(ruleset)
    states = [state, apply_move(state, legal_moves(state)[0])]
    metrics = InferenceMetrics()
    batched = policy_value_batch(states, model, metrics=metrics)
    for position, (priors, value) in zip(states, batched):
        reference, expected_value = policy_value(position, model=model)
        assert set(priors) == set(legal_moves(position))
        assert priors == pytest.approx(reference, rel=1e-5, abs=1e-7)
        assert sum(priors.values()) == pytest.approx(1)
        assert value == pytest.approx(expected_value, rel=1e-5, abs=1e-7)
    measured = metrics.snapshot(finalize_cuda=True)
    assert measured["inference_batches"] == 1
    assert measured["neural_evaluations"] == 2
    assert measured["inference_batch_size_average"] == 2
    assert measured["inference_batch_size_min"] == measured["inference_batch_size_max"] == 2
    assert measured["inference_time_seconds"] > 0
    assert measured["inference_cpu_seconds"] > 0


def test_terminals_are_removed_before_one_shared_forward_and_cpu_avoids_cuda(monkeypatch):
    model = PolicyValueNet("brandubh", 4, 0).eval()
    state = new_game("brandubh")
    terminal = replace(state, winner="defenders")
    sizes = []
    forward = model.forward

    def record(batch):
        sizes.append(batch.shape[0])
        return forward(batch)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("CPU inference must not initialize CUDA or autocast")

    monkeypatch.setattr(model, "forward", record)
    monkeypatch.setattr(torch, "autocast", forbidden)
    monkeypatch.setattr(torch.cuda, "Event", forbidden)
    monkeypatch.setattr(torch.cuda, "synchronize", forbidden)
    metrics = InferenceMetrics(track_cuda=True)
    result = policy_value_batch(
        [terminal, state, terminal, state], model, mixed_precision=True, metrics=metrics
    )
    assert sizes == [2]
    assert result[0] == result[2] == ({}, 1)
    assert result[1] == result[3]
    assert policy_value_batch([terminal], model, metrics=metrics) == [({}, 1)]
    assert policy_value_batch([], model, metrics=metrics) == []
    assert sizes == [2]
    assert metrics.snapshot(finalize_cuda=True)["neural_evaluations"] == 2


def test_batch_rejects_incompatible_live_positions_before_forward(monkeypatch):
    model = PolicyValueNet("brandubh", 4, 0)

    def forbidden(*_args):
        raise AssertionError("Compatibility validation must precede GPU work")

    monkeypatch.setattr(model, "forward", forbidden)
    with pytest.raises(ValueError, match="ruleset"):
        policy_value_batch([new_game("brandubh"), new_game("tablut")], model)


def test_aggregate_metrics_use_activity_timers_without_per_batch_cuda_sync(monkeypatch):
    wall = iter([10.0, 10.2, 20.0, 20.4])
    cpu = iter([1.0, 1.3, 5.0, 5.6])
    monkeypatch.setattr(
        "hnefatafl.inference.time",
        SimpleNamespace(perf_counter=lambda: next(wall), process_time=lambda: next(cpu)),
    )
    metrics = InferenceMetrics()
    with metrics.measure(2):
        pass
    with metrics.measure(1):
        pass
    result = metrics.snapshot()
    assert result["inference_batches"] == 2
    assert result["neural_evaluations"] == 3
    assert result["inference_batch_size_average"] == 1.5
    assert result["inference_batch_size_min"] == 1
    assert result["inference_batch_size_max"] == 2
    assert result["inference_time_seconds"] == pytest.approx(0.6)
    assert result["inference_cpu_seconds"] == pytest.approx(0.9)
    assert result["inference_batch_latency_seconds"] == pytest.approx(0.3)


def test_cuda_events_use_model_device_stream_and_finalize_only_at_boundary(monkeypatch):
    stream = object()
    selected_devices = []
    recorded = []
    synchronized = []

    def current_stream(device):
        selected_devices.append(str(device))
        return stream

    class Event:
        def __init__(self, *, enable_timing):
            assert enable_timing

        def record(self, selected):
            assert selected is stream
            recorded.append(self)

        def synchronize(self):
            synchronized.append(self)

        def elapsed_time(self, other):
            return 2.0

    monkeypatch.setattr(torch.cuda, "current_stream", current_stream)
    monkeypatch.setattr(torch.cuda, "Event", Event)
    metrics = InferenceMetrics(track_cuda=True)
    for _ in range(2):
        with metrics.cuda_activity(torch.device("cuda:3")):
            pass
    assert selected_devices == ["cuda:3", "cuda:3"]
    assert len(recorded) == 4
    metrics.snapshot()
    assert not synchronized
    assert metrics.snapshot(finalize_cuda=True)["inference_cuda_seconds"] == 0.004
    assert synchronized == [recorded[-1]]
    assert metrics.snapshot(finalize_cuda=True)["inference_cuda_seconds"] == 0.004
    assert len(synchronized) == 1


@pytest.mark.skipif(not torch.cuda.is_available(), reason="CUDA hardware unavailable")
def test_cuda_batch_and_aggregate_event_timing():
    model = PolicyValueNet("brandubh", 4, 0).cuda().eval()
    state = new_game("brandubh")
    metrics = InferenceMetrics(track_cuda=True)
    result = policy_value_batch([state, state], model, mixed_precision=True, metrics=metrics)
    assert result[0] == result[1]
    assert sum(result[0][0].values()) == pytest.approx(1)
    assert -1 <= result[0][1] <= 1
    measured = metrics.snapshot(finalize_cuda=True)
    assert measured["inference_cuda_seconds"] > 0
    assert not metrics._events
