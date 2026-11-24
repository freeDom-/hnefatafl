"""Line-protocol native tournament agent for ``tournament.py --run``.

Speaks the persistent process protocol::

    request:  {"id": "...", "state": {...}, "settings": {...}, "seed": 7}
    response: {"id": "...", "move": {"source": 0, "target": 1}}

The shared Python engine and the production ``hnefatafl.ai`` presets perform the
search; this adapter only owns transport, identity validation and one bounded
search per request. ``--engine scripted`` selects the first legal move and is for
structural workflow checks only (never playing-strength evidence).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SPIKE = Path(__file__).resolve().parent
ROOT = SPIKE.parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hnefatafl.ai import choose_move  # noqa: E402
from hnefatafl.engine import State, legal_moves  # noqa: E402


def _search(state: State, settings: dict, seed: int, engine: str, depth: int) -> dict:
    if engine == "scripted":
        return legal_moves(state)[0].to_dict()
    if engine == "scripted-last":
        return legal_moves(state)[-1].to_dict()
    time_limit = float(settings.get("time_limit", 2.0))
    if engine == "jarl":
        config = {
            "algorithm": "alpha_beta",
            "depth": depth,
            "time_limit": time_limit,
            "seed": seed,
        }
    elif engine == "seer":
        config = {
            "algorithm": "mcts",
            "iterations": int(settings.get("iterations", 128)),
            "time_limit": time_limit,
            "exploration": float(settings.get("exploration", 1.4)),
            "seed": seed,
        }
    else:  # pragma: no cover - argparse restricts the value
        raise ValueError(f"unsupported engine {engine!r}")
    return choose_move(state, config).to_dict()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--engine", choices=("scripted", "scripted-last", "jarl", "seer"), default="scripted"
    )
    parser.add_argument("--depth", type=int, default=5)
    args = parser.parse_args()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            request_id = request["id"]
            state = State.from_dict(request["state"])
            settings = request.get("settings") or {}
            seed = int(request.get("seed", settings.get("seed", 0)))
            if state.winner is not None:
                raise ValueError("cannot search a terminal position")
            move = _search(state, settings, seed, args.engine, args.depth)
        except Exception as error:  # noqa: BLE001 - report the failure as stderr and exit 2
            print(f"native_tournament_agent: {type(error).__name__}: {error}", file=sys.stderr)
            return 2
        print(json.dumps({"id": request_id, "move": move}, separators=(",", ":")), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
