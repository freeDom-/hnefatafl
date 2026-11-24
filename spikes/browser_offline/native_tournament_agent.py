"""Line-protocol native tournament agent for ``tournament.py --run``.

Speaks the persistent process protocol (version 2)::

    handshake: {"kind": "handshake", "protocol": 2, "agent": {...}}
    request:   {"id": "...", "state": {...}, "settings": {...}, "seed": 7}
    response:  {"id": "...", "move": {...}, "evidence": {...}}

The shared Python engine and the production ``hnefatafl.ai`` presets perform the
search. The handshake reports the actual engine identity, kind, runtime, provider,
algorithm and supported knobs; every response reports the effective settings,
seed, completed work/depth, completion reason and elapsed time so the tournament
validator can bind declared configurations to observed execution.

``--engine scripted`` / ``scripted-last`` select the first/last legal move and are
for structural workflow checks only (never playing-strength evidence).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

SPIKE = Path(__file__).resolve().parent
ROOT = SPIKE.parents[1]
for path in (str(SPIKE), str(ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

import native_mcts_parity  # noqa: E402
from hnefatafl.ai import choose_move  # noqa: E402
from hnefatafl.engine import State, legal_moves  # noqa: E402

PROTOCOL_VERSION = 2
DEFAULT_KIND = {
    "scripted": "scripted",
    "scripted-last": "scripted",
    "jarl": "native-jarl",
    "seer": "native-seer",
}
ALGORITHM = {
    "scripted": "scripted",
    "scripted-last": "scripted",
    "jarl": "alpha_beta",
    "seer": "mcts",
}


def _search(state: State, settings: dict, seed: int, engine: str, depth: int):
    started = time.perf_counter()
    if engine in ("scripted", "scripted-last"):
        moves = legal_moves(state)
        move = moves[0] if engine == "scripted" else moves[-1]
        return move, {
            "completed_simulations": None,
            "depth": None,
            "completion_reason": "completed",
        }
    time_limit = float(settings.get("time_limit", 2.0))
    diagnostics: dict = {}
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
    move = choose_move(state, config, diagnostics=diagnostics)
    if engine == "jarl":
        return move, {
            "completed_simulations": None,
            "depth": int(diagnostics.get("completed_depth", depth)),
            "completion_reason": "completed",
        }
    completed = int(diagnostics.get("simulations", 0))
    requested = int(settings.get("iterations", 128))
    if diagnostics.get("immediate_win_shortcuts"):
        reason = "immediate_win"
    elif completed < requested:
        reason = "deadline"
    else:
        reason = "simulations"
    return move, {
        "completed_simulations": completed,
        "depth": None,
        "completion_reason": reason,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--engine",
        choices=("scripted", "scripted-last", "jarl", "seer"),
        default="scripted",
    )
    parser.add_argument("--kind", choices=("scripted", "native-jarl", "native-seer"))
    parser.add_argument("--depth", type=int, default=5)
    args = parser.parse_args()
    kind = args.kind or DEFAULT_KIND[args.engine]
    algorithm = ALGORITHM[args.engine]
    handshake = {
        "kind": "handshake",
        "protocol": PROTOCOL_VERSION,
        "agent": {
            "protocol": PROTOCOL_VERSION,
            "kind": kind,
            "runtime": "native-cpython",
            "provider": "cpu",
            "algorithm": algorithm,
            "engine_sha256": native_mcts_parity.engine_identity()["sha256"],
            "settings_support": {
                "iterations": args.engine == "seer",
                "time_limit": True,
                "depth": args.engine == "jarl",
            },
        },
    }
    if args.engine == "jarl":
        handshake["agent"]["depth"] = args.depth
    print(json.dumps(handshake, separators=(",", ":")), flush=True)
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
            started = time.perf_counter()
            move, result = _search(state, settings, seed, args.engine, args.depth)
            elapsed_ms = (time.perf_counter() - started) * 1000.0
        except Exception as error:  # noqa: BLE001 - report the failure on stderr and exit 2
            print(f"native_tournament_agent: {type(error).__name__}: {error}", file=sys.stderr)
            return 2
        evidence = {
            "settings": {
                "iterations": settings.get("iterations"),
                "time_limit": settings.get("time_limit"),
                "exploration": settings.get("exploration"),
                "seed": seed,
            },
            "seed": seed,
            "algorithm": algorithm,
            "provider": "cpu",
            "model_sha256": None,
            "elapsed_ms": elapsed_ms,
            **result,
        }
        print(
            json.dumps(
                {"id": request_id, "move": move.to_dict(), "evidence": evidence},
                separators=(",", ":"),
            ),
            flush=True,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
