"""Shared legal-policy inference and aggregate self-play activity measurements."""

from __future__ import annotations

from collections import OrderedDict
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field
import time
from typing import Iterator

import torch

from .engine import Move, State, legal_moves
from .models import PolicyValueNet, encode_state
from .inference_backends import InferenceBackend


# Compact immutable legal policy only; never dense logits, GPU tensors or root noise.
Evaluation = tuple[tuple[tuple[Move, float], ...], float]
InputKey = tuple[str, tuple[str, ...], str]


class ExactInferenceCache:
    """Bounded cache owned by one self-play generation, never a global model cache.

    bind() also detects normal in-place optimizer/load_state_dict updates and
    parameter replacement. Backend refresh has an explicit generation token.
    Call clear() after any external mutation bypassing PyTorch version counters
    (for example .data writes); managed training always creates a fresh cache.
    Consumers receive fresh dictionaries, including duplicate requests.
    """

    def __init__(self, capacity: int = 4096):
        if type(capacity) is not int or not 1 <= capacity <= 65536:
            raise ValueError("Cache capacity must be an integer between 1 and 65536")
        self.capacity = capacity
        self.entries: OrderedDict[InputKey, Evaluation] = OrderedDict()
        self._context: tuple | None = None

    def clear(self) -> None:
        self.entries.clear()
        self._context = None

    def bind(
        self, model: PolicyValueNet, backend: InferenceBackend | None, mixed_precision: bool
    ) -> None:
        if backend is not None and backend.model is not model:
            raise ValueError("Inference backend belongs to a different model")
        if model.training:
            raise ValueError("Exact inference caching requires model.eval()")
        context = (
            model,
            backend,
            getattr(backend, "generation", None),
            str(backend.device) if backend is not None else None,
            model.ruleset,
            mixed_precision,
            torch.get_float32_matmul_precision(),
            torch.backends.cudnn.allow_tf32,
            torch.is_autocast_enabled("cpu"),
            torch.get_autocast_dtype("cpu"),
            torch.is_autocast_enabled("cuda"),
            torch.get_autocast_dtype("cuda"),
            tuple(
                (id(tensor), tensor._version, str(tensor.device), tensor.dtype)
                for tensor in (*model.parameters(), *model.buffers())
            ),
        )
        if self._context != context:
            self.entries.clear()
            self._context = context

    def get(self, key: InputKey) -> Evaluation | None:
        result = self.entries.get(key)
        if result is not None:
            self.entries.move_to_end(key)
        return result

    def put(self, key: InputKey, result: tuple[dict[Move, float], float]) -> None:
        self.entries[key] = (tuple(result[0].items()), result[1])
        self.entries.move_to_end(key)
        if len(self.entries) > self.capacity:
            self.entries.popitem(last=False)


@dataclass
class InferenceMetrics:
    """Host timings accumulate activity, rather than the enclosing phase wall time.

    CUDA event collection is optional. Events are resolved together at a phase
    boundary, never synchronized inside individual search simulations.
    """

    track_cuda: bool = False
    requests: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    duplicates: int = 0
    cache_entries: int = 0
    cache_peak_entries: int = 0
    cache_lookup_seconds: float = 0.0
    batches: int = 0
    evaluations: int = 0
    batch_minimum: int = 0
    batch_maximum: int = 0
    host_seconds: float = 0.0
    cpu_seconds: float = 0.0
    cuda_seconds: float = 0.0
    batch_histogram: dict[int, int] = field(default_factory=dict)
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
            self.batch_histogram[size] = self.batch_histogram.get(size, 0) + 1
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
            "inference_batch_distribution": {
                str(k): v for k, v in sorted(self.batch_histogram.items())
            },
            "inference_requests": self.requests,
            "inference_cache_hits": self.cache_hits,
            "inference_cache_misses": self.cache_misses,
            "inference_batch_duplicates": self.duplicates,
            "inference_cache_hit_rate": self.cache_hits / (self.cache_hits + self.cache_misses)
            if self.cache_hits + self.cache_misses
            else 0.0,
            "inference_cache_entries": self.cache_entries,
            "inference_cache_peak_entries": self.cache_peak_entries,
            "inference_cache_lookup_seconds": self.cache_lookup_seconds,
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
    backend: InferenceBackend | None = None,
    cache: ExactInferenceCache | None = None,
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
    if metrics is not None:
        metrics.requests += len(active)
    if not active:
        return results
    # Validate all requests before mutating cache state or running a forward pass.
    recipients: dict[int, list[int]] = {}
    keys: dict[int, InputKey] = {}
    if cache is not None:
        started = time.perf_counter()
        cache.bind(model, backend, mixed_precision)
        distinct: dict[InputKey, int] = {}
        uncached = []
        for index, state, moves in active:
            key = (state.ruleset, state.board, state.turn)
            if key in distinct:
                recipients[distinct[key]].append(index)
                if metrics is not None:
                    metrics.duplicates += 1
                continue
            distinct[key] = index
            recipients[index] = [index]
            keys[index] = key
            cached = cache.get(key)
            if cached is None:
                uncached.append((index, state, moves))
                if metrics is not None:
                    metrics.cache_misses += 1
            else:
                results[index] = (dict(cached[0]), cached[1])
                if metrics is not None:
                    metrics.cache_hits += 1
        active = uncached
        if metrics is not None:
            metrics.cache_lookup_seconds += time.perf_counter() - started
    elif metrics is not None:
        metrics.cache_misses += len(active)
    device = backend.device if backend is not None else next(model.parameters()).device
    measurement = (
        metrics.measure(len(active), device=device) if metrics is not None else nullcontext()
    )
    if active:
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
                    logits, values = (
                        backend.forward(encoded) if backend is not None else model(encoded)
                    )
                legal_logits = logits.float().gather(1, action_ids.clamp_min(0))
                legal_logits.masked_fill_(action_ids < 0, -torch.inf)
                probabilities = torch.softmax(legal_logits, dim=1)
                packed_output = torch.cat((probabilities, values.float().reshape(-1, 1)), dim=1)
            packed = packed_output.cpu().tolist()
            for (index, _, moves), row in zip(active, packed):
                results[index] = (dict(zip(moves, row[: len(moves)])), float(row[-1]))
    if cache is not None:
        for index, _, _ in active:
            cache.put(keys[index], results[index])
        for index, targets in recipients.items():
            policy, value = results[index]
            for target in targets:
                results[target] = (dict(policy), value)
        if metrics is not None:
            metrics.cache_entries = len(cache.entries)
            metrics.cache_peak_entries = max(metrics.cache_peak_entries, len(cache.entries))
    return results
