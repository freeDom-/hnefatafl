"""Shared legal-policy inference and aggregate self-play activity measurements."""

from __future__ import annotations

from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field
import time
from typing import Iterator

import torch

from .engine import Move, State, legal_moves
from .models import PolicyValueNet, encode_state


@dataclass
class InferenceMetrics:
    """Host timings accumulate activity, rather than the enclosing phase wall time.

    CUDA event collection is optional. Events are resolved together at a phase
    boundary, never synchronized inside individual search simulations.
    """

    track_cuda: bool = False
    batches: int = 0
    evaluations: int = 0
    batch_minimum: int = 0
    batch_maximum: int = 0
    host_seconds: float = 0.0
    cpu_seconds: float = 0.0
    cuda_seconds: float = 0.0
    _events: list[tuple] = field(default_factory=list, repr=False)
    _cuda_recorded: bool = field(default=False, repr=False)

    @contextmanager
    def measure(self, size: int, *, device="cpu") -> Iterator[None]:
        if size <= 0:
            yield
            return
        started = time.perf_counter()
        cpu_started = time.process_time()
        try:
            yield
        finally:
            self.host_seconds += time.perf_counter() - started
            self.cpu_seconds += time.process_time() - cpu_started
            self.batches += 1
            self.evaluations += size
            self.batch_minimum = min(self.batch_minimum or size, size)
            self.batch_maximum = max(self.batch_maximum, size)

    @contextmanager
    def cuda_activity(self, device) -> Iterator[None]:
        """Optionally measure GPU stream work, excluding CPU encoding/decoding."""
        if not self.track_cuda or torch.device(device).type != "cuda":
            yield
            return
        stream = torch.cuda.current_stream(device)
        start = torch.cuda.Event(enable_timing=True)
        end = torch.cuda.Event(enable_timing=True)
        start.record(stream)
        try:
            yield
        finally:
            end.record(stream)
            self._events.append((start, end))
            self._cuda_recorded = True

    def snapshot(self, *, finalize_cuda: bool = False) -> dict:
        if finalize_cuda and self._events:
            # All events are from the one shared model's stream. Waiting for its
            # final event also completes the preceding event pairs.
            self._events[-1][1].synchronize()
            self.cuda_seconds += sum(start.elapsed_time(end) for start, end in self._events) / 1000
            self._events.clear()
        result = {
            "inference_batches": self.batches,
            "neural_evaluations": self.evaluations,
            "inference_batch_size_average": self.evaluations / self.batches
            if self.batches
            else 0.0,
            "inference_batch_size_min": self.batch_minimum,
            "inference_batch_size_max": self.batch_maximum,
            "inference_time_seconds": self.host_seconds,
            "inference_cpu_seconds": self.cpu_seconds,
            "inference_batch_latency_seconds": self.host_seconds / self.batches
            if self.batches
            else 0.0,
        }
        if self._cuda_recorded:
            result["inference_cuda_seconds"] = self.cuda_seconds
        return result


def policy_value_batch(
    states: list[State],
    model: PolicyValueNet,
    *,
    mixed_precision: bool = False,
    metrics: InferenceMetrics | None = None,
) -> list[tuple[dict[Move, float], float]]:
    """Evaluate legal priors and defender-relative values in one shared forward.

    Terminal positions never enter the network. Legal-action indexing and
    softmax stay on the model device; only one compact policy/value tensor is
    transferred back for the whole batch.
    """
    from .ai import evaluate

    results: list[tuple[dict[Move, float], float]] = [({}, 0.0) for _ in states]
    active: list[tuple[int, State, list[Move]]] = []
    for index, state in enumerate(states):
        moves = legal_moves(state)
        if state.winner or not moves:
            results[index] = ({}, evaluate(state))
        else:
            if state.ruleset != model.ruleset:
                raise ValueError("Model ruleset does not match this game")
            active.append((index, state, moves))
    if not active:
        return results
    device = next(model.parameters()).device
    measurement = (
        metrics.measure(len(active), device=device) if metrics is not None else nullcontext()
    )
    with measurement, torch.inference_mode():
        encoded = torch.stack([encode_state(state) for _, state, _ in active])
        width = max(len(moves) for _, _, moves in active)
        action_ids = torch.full((len(active), width), -1, dtype=torch.long)
        for row, (_, state, moves) in enumerate(active):
            action_ids[row, : len(moves)] = torch.tensor(
                [move.source * state.size**2 + move.target for move in moves], dtype=torch.long
            )
        activity = metrics.cuda_activity(device) if metrics is not None else nullcontext()
        with activity:
            encoded = encoded.to(device)
            action_ids = action_ids.to(device)
            autocast = (
                torch.autocast(device_type="cuda")
                if mixed_precision and device.type == "cuda"
                else nullcontext()
            )
            with autocast:
                logits, values = model(encoded)
            legal_logits = logits.float().gather(1, action_ids.clamp_min(0))
            legal_logits.masked_fill_(action_ids < 0, -torch.inf)
            probabilities = torch.softmax(legal_logits, dim=1)
            packed_output = torch.cat((probabilities, values.float().reshape(-1, 1)), dim=1)
        packed = packed_output.cpu().tolist()
        for (index, _, moves), row in zip(active, packed):
            results[index] = (dict(zip(moves, row[: len(moves)])), float(row[-1]))
    return results
