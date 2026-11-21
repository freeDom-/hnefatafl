"""Focused native encoding performance comparison.

The shared PyTorch-free encoder replaced a tensor-assignment implementation. This
tool compares the previous path (``torch.zeros`` plus per-piece indexing) with the
current shared encoder for representative positions and batches across all three
rulesets, separating position encoding (byte planes) from tensor materialisation.

    .venv/bin/python spikes/browser_offline/encoding_benchmark.py --repetitions 5

Measurements are single-machine observations, not universal guarantees; they exist
to detect a material native regression, not to optimize training.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

SPIKE = Path(__file__).resolve().parent
ROOT = SPIKE.parents[1]
for path in (str(SPIKE), str(ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

from hnefatafl.encoding import encode_planes  # noqa: E402
from hnefatafl.engine import new_game  # noqa: E402
from hnefatafl.models import encode_state  # noqa: E402

RULESETS = ("brandubh", "tablut", "copenhagen")


def previous_encoder(state):
    """The pre-Phase-2 tensor-assignment encoder, kept only for comparison."""
    import torch

    size = state.size
    encoded = torch.zeros((6, size, size), dtype=torch.float32)
    for index, piece in enumerate(state.board):
        if piece in "ADK":
            encoded["ADK".index(piece), index // size, index % size] = 1.0
    if state.turn == "defenders":
        encoded[3].fill_(1.0)
    encoded[4, size // 2, size // 2] = 1.0
    if state.ruleset != "tablut":
        for row, column in ((0, 0), (0, size - 1), (size - 1, 0), (size - 1, size - 1)):
            encoded[5, row, column] = 1.0
    return encoded


def measure(operation, positions, repetitions: int) -> float:
    for position in positions:
        operation(position)
    samples = []
    for _ in range(repetitions):
        started = time.perf_counter()
        for position in positions:
            operation(position)
        samples.append((time.perf_counter() - started) / len(positions) * 1e6)
    return statistics.median(samples)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--positions", type=int, default=8)
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--batch", type=int, default=32)
    args = parser.parse_args()
    import torch

    report = {"positions": args.positions, "repetitions": args.repetitions, "rulesets": {}}
    for ruleset in RULESETS:
        positions = [new_game(ruleset) for _ in range(args.positions)]
        batch = [positions[index % len(positions)] for index in range(args.batch)]
        planes = measure(encode_planes, positions, args.repetitions)
        previous = measure(previous_encoder, positions, args.repetitions)
        current = measure(encode_state, positions, args.repetitions)
        stacked = measure(
            lambda state: torch.stack([encode_state(state)] * args.batch),
            positions[:1],
            max(1, args.repetitions // 2),
        )
        assert all(
            torch.equal(previous_encoder(state), encode_state(state)) for state in positions
        ), "the shared encoder must reproduce the previous tensor exactly"
        report["rulesets"][ruleset] = {
            "previous_tensor_assignment_us": round(previous, 2),
            "shared_encoder_us": round(current, 2),
            "plane_encoding_only_us": round(planes, 2),
            "tensor_materialisation_us": round(max(0.0, current - planes), 2),
            "batched_stack_us": round(stacked, 2),
            "speedup": round(previous / current, 2) if current else None,
            "exact_tensor_parity": True,
            "batch_positions": len(batch),
        }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
