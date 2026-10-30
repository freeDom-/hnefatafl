"""Packed sparse replay and bounded CUDA minibatch prefetch.

RNG and augmentation counters commit only when a batch is consumed. Lookahead
uses a private RNG, so cancellation does not consume an unpresented transform.
"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
import random
import time

import torch

from .training_augmentation import (
    AugmentationStats,
    _size_of,
    _square_source,
    action_map,
    sample_symmetries,
)


class PackedReplay:
    def __init__(self, replay: list[dict]):
        if not replay:
            raise ValueError("Replay must contain examples")
        self.inputs = torch.stack([row["input"] for row in replay])
        self.size = _size_of(self.inputs)
        self.values = torch.tensor([row["value"] for row in replay], dtype=torch.float32)
        self.actions = torch.tensor(
            [a for row in replay for a in row["actions"]], dtype=torch.int64
        )
        self.probabilities = torch.tensor(
            [p for row in replay for p in row["probabilities"]], dtype=torch.float32
        )
        self.legal = torch.tensor([a for row in replay for a in row["legal"]], dtype=torch.int64)
        if any(len(row["actions"]) != len(row["probabilities"]) for row in replay):
            raise ValueError("Each policy action requires exactly one probability")
        for ids in (self.actions, self.legal):
            if ((ids < 0) | (ids >= self.size**4)).any():
                raise ValueError("Training example contains an action outside the board")
        self.action_offsets = torch.tensor(
            [0] + [len(row["actions"]) for row in replay], dtype=torch.int64
        ).cumsum(0)
        self.legal_offsets = torch.tensor(
            [0] + [len(row["legal"]) for row in replay], dtype=torch.int64
        ).cumsum(0)

    @staticmethod
    def _indices(offsets, selected):
        starts = offsets[selected]
        counts = offsets[selected + 1] - starts
        rows = torch.arange(len(selected)).repeat_interleave(counts)
        relative = torch.arange(int(counts.sum())) - (counts.cumsum(0) - counts)[rows]
        return rows, starts[rows] + relative

    def collate(self, indices: list[int], symmetries=None, stats=None):
        selected = torch.tensor(indices, dtype=torch.int64)
        inputs = self.inputs.index_select(0, selected)
        action_rows, action_indices = self._indices(self.action_offsets, selected)
        legal_rows, legal_indices = self._indices(self.legal_offsets, selected)
        actions = self.actions[action_indices]
        probabilities = self.probabilities[action_indices]
        legal = self.legal[legal_indices]
        if symmetries is not None:
            started = time.perf_counter()
            chosen = torch.tensor(symmetries, dtype=torch.int64)
            mapping = action_map(self.size)
            actions = mapping[chosen[action_rows], actions]
            legal = mapping[chosen[legal_rows], legal]
            source = _square_source(self.size)[chosen]
            flat = inputs.flatten(2)
            inputs = flat.gather(2, source.unsqueeze(1).expand_as(flat)).reshape(inputs.shape)
            if stats is not None:
                stats.seconds += time.perf_counter() - started
                for symmetry in symmetries:
                    stats.counts[symmetry] += 1
        width = self.size**4
        target = torch.zeros((len(selected), width), dtype=torch.float32)
        mask = torch.zeros((len(selected), width), dtype=torch.bool)
        target[action_rows, actions] = probabilities
        mask[legal_rows, legal] = True
        return inputs, target, mask, self.values[selected]


class ReplayBatches:
    def __init__(self, replay: PackedReplay, device: torch.device):
        self.replay = replay
        self.device = device
        self.pool = ThreadPoolExecutor(max_workers=1) if device.type == "cuda" else None
        self.stream = torch.cuda.Stream(device=device) if self.pool else None

    def close(self):
        if self.pool is not None:
            self.pool.shutdown(wait=True, cancel_futures=True)
        if self.stream is not None:
            self.stream.synchronize()

    def epoch(self, order, batch_size, rng, augmenting, stats):
        if self.pool is None:
            for start in range(0, len(order), batch_size):
                indices = order[start : start + batch_size]
                symmetries = sample_symmetries(rng, len(indices)) if augmenting else None
                yield tuple(
                    tensor.to(self.device)
                    for tensor in self.replay.collate(indices, symmetries, stats)
                )
            return
        if not order:
            return
        pool = self.pool
        transfer_stream = self.stream
        assert pool is not None and transfer_stream is not None
        private_rng = random.Random(0)
        private_rng.setstate(rng.getstate())
        batches = iter(
            order[start : start + batch_size] for start in range(0, len(order), batch_size)
        )

        def prepare(indices):
            local = AugmentationStats()
            symmetries = sample_symmetries(private_rng, len(indices)) if augmenting else None
            tensors = self.replay.collate(indices, symmetries, local)
            return tuple(tensor.pin_memory() for tensor in tensors), local, private_rng.getstate()

        future: Future | None = pool.submit(prepare, next(batches))

        def load():
            nonlocal future
            assert future is not None
            host, local, state = future.result()
            with torch.cuda.stream(transfer_stream):
                tensors = tuple(tensor.to(self.device, non_blocking=True) for tensor in host)
            indices = next(batches, None)
            future = pool.submit(prepare, indices) if indices is not None else None
            return tensors, local, state

        current = load()
        while current is not None:
            tensors, local, state = current
            stream = torch.cuda.current_stream(self.device)
            stream.wait_stream(transfer_stream)
            for tensor in tensors:
                tensor.record_stream(stream)
            following = load() if future is not None else None
            if augmenting:
                rng.setstate(state)
            stats.seconds += local.seconds
            for index, count in enumerate(local.counts):
                stats.counts[index] += count
            yield tensors
            current = following
