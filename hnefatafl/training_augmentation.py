"""D4 augmentation of neural training examples (Phase 3).

A replay example is a *sparse* description of one position: an encoded
``[6, n, n]`` input, MCTS visit probabilities over flattened ``source * n² +
target`` actions, the legal actions and a defender-relative value. Applying a
D4 transform T to the position permutes squares by ``symmetry.coordinate_map``.
Consequently

* every input plane is permuted by the same square permutation (the side-to-move,
  throne and corner planes are D4-invariant and map onto themselves),
* every action ``(s, t)`` becomes ``(T(s), T(t))`` and keeps its probability,
* the legal set is mapped elementwise, and
* the defender-relative value is unchanged (the sides never swap).

This module is pure tensor/list logic: no game rules, optimizer, checkpoint or
inference code, and it never builds a game state. It is training augmentation
only; it does not make neural predictions interchangeable across orientations.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Sequence

import torch

from .symmetry import Symmetry, coordinate_map, inverse

SYMMETRY_MODES = ("off", "random_d4")
TRANSFORM_COUNT = len(Symmetry)
_GEOMETRIC = frozenset({"input", "actions", "probabilities", "legal", "value"})


def validate_mode(mode: Any) -> str:
    if type(mode) is not str or mode not in SYMMETRY_MODES:
        raise ValueError("training_symmetry_mode must be off or random_d4")
    return mode


def augmentation_rng(seed: int, completed: int) -> random.Random:
    """Deterministic dedicated stream, independent of replay shuffling/self-play.

    Fresh runs use ``completed == 0``. Checkpoints that predate the feature (or
    were written by an OFF job) have no saved stream; a resume derives one from
    the unchanged job seed and completed iteration, so it is reproducible.
    """
    return random.Random(f"hnefatafl-training-augmentation:{seed}:{completed}")


@lru_cache(maxsize=8)
def _square_source(size: int) -> torch.Tensor:
    """``[8, n²]``: for transform t and destination square d, the source square."""
    table = torch.empty((TRANSFORM_COUNT, size * size), dtype=torch.int64)
    for symmetry in Symmetry:
        destination = torch.tensor(coordinate_map(size, symmetry), dtype=torch.int64)
        table[symmetry, destination] = torch.arange(size * size, dtype=torch.int64)
    return table


@lru_cache(maxsize=8)
def action_map(size: int) -> torch.Tensor:
    """``[8, n⁴]``: new flattened action id for transform t and original id."""
    area = size * size
    actions = torch.arange(area * area, dtype=torch.int64)
    table = torch.empty((TRANSFORM_COUNT, area * area), dtype=torch.int64)
    for symmetry in Symmetry:
        squares = torch.tensor(coordinate_map(size, symmetry), dtype=torch.int64)
        table[symmetry] = squares[actions // area] * area + squares[actions % area]
    return table


def _size_of(encoded: torch.Tensor) -> int:
    if encoded.dim() < 3 or encoded.shape[-1] != encoded.shape[-2]:
        raise ValueError("Encoded input must end with a square board")
    return int(encoded.shape[-1])


def transform_input(encoded: torch.Tensor, symmetry: Symmetry) -> torch.Tensor:
    """Return a new transformed ``[..., C, n, n]`` tensor; the argument is untouched."""
    size = _size_of(encoded)
    source = _square_source(size)[Symmetry(symmetry)]
    flat = encoded.reshape(*encoded.shape[:-2], size * size)
    return flat.index_select(-1, source).reshape(encoded.shape)


def transform_actions(actions: Sequence[int], size: int, symmetry: Symmetry) -> list[int]:
    """Map flattened action ids, preserving order (so pairing with weights holds)."""
    limit = size**4
    mapping = action_map(size)[Symmetry(symmetry)].tolist()
    result = []
    for action in actions:
        if type(action) is not int or not 0 <= action < limit:
            raise ValueError("Action identifier is outside the board")
        result.append(mapping[action])
    return result


def transform_example(example: dict, symmetry: Symmetry) -> dict:
    """Return an independently owned transformed copy of one replay example.

    Parallel ``actions``/``probabilities`` stay paired and in order, ``legal`` is
    mapped elementwise, ``value`` is untouched and any other metadata is carried
    over unchanged (it is not geometric).
    """
    symmetry = Symmetry(symmetry)
    size = _size_of(example["input"])
    result = {key: value for key, value in example.items() if key not in _GEOMETRIC}
    result["input"] = transform_input(example["input"], symmetry)
    result["actions"] = transform_actions(example["actions"], size, symmetry)
    result["probabilities"] = list(example["probabilities"])
    result["legal"] = transform_actions(example["legal"], size, symmetry)
    result["value"] = example["value"]
    return result


def invert_example(example: dict, symmetry: Symmetry) -> dict:
    """Undo ``transform_example(…, symmetry)``."""
    return transform_example(example, inverse(symmetry))


def sample_symmetries(rng: random.Random, count: int) -> list[int]:
    """Uniform independent draws over all eight transforms, including identity."""
    return [rng.randrange(TRANSFORM_COUNT) for _ in range(count)]


@dataclass
class AugmentationStats:
    """Compact counters; ``exposures`` are optimizer presentations, not replay rows."""

    counts: list[int] = field(default_factory=lambda: [0] * TRANSFORM_COUNT)
    seconds: float = 0.0

    @property
    def exposures(self) -> int:
        return sum(self.counts)

    def summary(self, mode: str, optimization_seconds: float) -> dict:
        return {
            "mode": mode,
            "exposures": self.exposures,
            "identity": self.counts[Symmetry.IDENTITY],
            "transform_counts": list(self.counts),
            "seconds": self.seconds,
            "optimization_share": self.seconds / optimization_seconds
            if optimization_seconds > 0
            else 0.0,
        }


def collate_batch(
    batch: Sequence[dict],
    device: torch.device | str,
    symmetries: Sequence[int] | None = None,
    stats: AugmentationStats | None = None,
) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
    """Build inputs, dense policy targets, legal masks and values for a minibatch.

    ``symmetries`` assigns one D4 transform per row; ``None`` is the unaugmented
    baseline and performs no transformation work. All geometry is applied to host
    tensors/index lists *before* the single device transfer, so augmentation adds
    no device kernels or extra host/device copies. Replay rows are not mutated.
    """
    inputs = torch.stack([row["input"] for row in batch])
    size = _size_of(inputs)
    area4 = size**4
    count = len(batch)
    actions = torch.tensor([a for row in batch for a in row["actions"]], dtype=torch.int64)
    probabilities = torch.tensor(
        [p for row in batch for p in row["probabilities"]], dtype=torch.float32
    )
    legal = torch.tensor([a for row in batch for a in row["legal"]], dtype=torch.int64)
    rows = torch.arange(count, dtype=torch.int64)
    action_rows = rows.repeat_interleave(
        torch.tensor([len(row["actions"]) for row in batch], dtype=torch.int64)
    )
    legal_rows = rows.repeat_interleave(
        torch.tensor([len(row["legal"]) for row in batch], dtype=torch.int64)
    )
    if len(actions) != len(probabilities):
        raise ValueError("Each policy action requires exactly one probability")
    if ((actions < 0) | (actions >= area4)).any() or ((legal < 0) | (legal >= area4)).any():
        raise ValueError("Training example contains an action outside the board")
    if symmetries is not None:
        started = time.perf_counter()
        chosen = torch.tensor(list(symmetries), dtype=torch.int64)
        if chosen.shape != (count,) or ((chosen < 0) | (chosen >= TRANSFORM_COUNT)).any():
            raise ValueError("One valid D4 transform is required per training example")
        mapping = action_map(size)
        actions = mapping[chosen[action_rows], actions]
        legal = mapping[chosen[legal_rows], legal]
        source = _square_source(size)[chosen]
        flat = inputs.reshape(count, inputs.shape[1], size * size)
        inputs = flat.gather(2, source.unsqueeze(1).expand_as(flat)).reshape(inputs.shape)
        if stats is not None:
            stats.seconds += time.perf_counter() - started
            for symmetry in chosen.tolist():
                stats.counts[symmetry] += 1
    target = torch.zeros(count * area4, dtype=torch.float32)
    target[action_rows * area4 + actions] = probabilities
    mask = torch.zeros(count * area4, dtype=torch.bool)
    mask[legal_rows * area4 + legal] = True
    values = torch.tensor([row["value"] for row in batch], dtype=torch.float32)
    return (
        inputs.to(device),
        target.reshape(count, area4).to(device),
        mask.reshape(count, area4).to(device),
        values.to(device),
    )
