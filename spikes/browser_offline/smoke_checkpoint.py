"""Create a tiny *structural* smoke checkpoint for experimental validation.

This is not a trained competitive model and no playing strength is claimed. It
performs a small number of gradient steps against deterministic synthetic targets
(uniform legal policy, defender-relative value 0) purely so the export, parity,
browser inference and browser neural-MCTS pipelines can be exercised without
owner-supplied weights. Competitive checkpoints must be supplied privately.

    .venv/bin/python spikes/browser_offline/smoke_checkpoint.py \\
        --ruleset brandubh --output /tmp/smoke-brandubh.pt

The written checkpoint contains tensors only and is loaded by the existing
validated checkpoint loader.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SPIKE = Path(__file__).resolve().parent
ROOT = SPIKE.parents[1]
for path in (str(SPIKE), str(ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

from hnefatafl.engine import apply_move, legal_moves, new_game  # noqa: E402
from hnefatafl.models import PolicyValueNet, encode_state, save_checkpoint  # noqa: E402


def positions(ruleset: str, count: int) -> list:
    import torch

    states = []
    state = new_game(ruleset)
    states.append(state)
    rng = torch.Generator().manual_seed(1234)
    while len(states) < count:
        moves = legal_moves(state)
        if state.winner or not moves:
            state = new_game(ruleset)
            continue
        index = int(torch.randint(0, len(moves), (1,), generator=rng).item())
        state = apply_move(state, moves[index])
        if not state.winner:
            states.append(state)
    return states


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ruleset", choices=["brandubh", "tablut", "copenhagen"], default="brandubh"
    )
    parser.add_argument("--channels", type=int, default=4)
    parser.add_argument("--blocks", type=int, default=0)
    parser.add_argument("--steps", type=int, default=12)
    parser.add_argument("--seed", type=int, default=11)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if ROOT in output.parents and not output.is_relative_to(ROOT / "work"):
        parser.error("Write smoke checkpoints inside work/ or outside the repository")

    import torch

    torch.manual_seed(args.seed)
    model = PolicyValueNet(ruleset=args.ruleset, channels=args.channels, blocks=args.blocks)
    samples = positions(args.ruleset, 24)
    optimizer = torch.optim.Adam(model.parameters(), lr=1e-3)
    for step in range(args.steps):
        batch = torch.stack(
            [encode_state(samples[(step * 3 + index) % len(samples)]) for index in range(4)]
        )
        logits, value = model(batch)
        policy_target = torch.zeros_like(logits)
        value_target = torch.zeros_like(value)
        for row, state in enumerate(
            [samples[(step * 3 + index) % len(samples)] for index in range(4)]
        ):
            moves = legal_moves(state)
            if not moves:
                continue
            area = state.size**2
            ids = [move.source * area + move.target for move in moves]
            policy_target[row, ids] = 1.0 / len(ids)
        log_probabilities = torch.log_softmax(logits, dim=1)
        policy_loss = -(policy_target * log_probabilities).sum(dim=1).mean()
        value_loss = torch.nn.functional.mse_loss(value.reshape(-1), value_target)
        loss = policy_loss + value_loss
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()
    output.parent.mkdir(parents=True, exist_ok=True)
    save_checkpoint(
        output,
        model,
        {
            "ruleset": args.ruleset,
            "name": f"structural-smoke-{args.channels}x{args.blocks}",
            "note": "minimal synthetic training; structural validation only",
        },
    )
    print(
        {
            "output": str(output),
            "ruleset": args.ruleset,
            "channels": args.channels,
            "blocks": args.blocks,
            "steps": args.steps,
            "structural_only": True,
        }
    )


if __name__ == "__main__":
    main()
