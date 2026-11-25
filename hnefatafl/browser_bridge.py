"""Production Pyodide bridge for authoritative-engine local analysis.

Only the bounded analysis protocol is exposed. The browser spike's benchmark,
fixture and parity actions are deliberately absent: production artifacts never
ship development-only test hooks.

Protocol (JSON string in, JSON string out):

* ``{"action": "info"}`` — engine identity, local analysis bounds.
* ``{"action": "position", "state"}`` — authoritative snapshot and legal moves.
* ``{"action": "analysis", "state", "config"}`` — bounded classical analysis.
* ``{"action": "move", "state", "move"}`` — validate and apply one legal move.

Results are advisory. Nothing here mutates authoritative server state.
"""

from __future__ import annotations

import hashlib
import json
import math
import sys
import time

from hnefatafl.engine import Move, State, apply_move, legal_moves

BRIDGE_PROTOCOL = 1
MAX_BRIDGE_BYTES = 512 * 1024
MAX_ANALYSIS_TIME = 3.0

ANALYSIS_RANGES = {
    "depth": (1, 5),
    "iterations": (1, 4096),
    "rollout_depth": (0, 50),
    "seed": (0, 2**32 - 1),
    "exploration": (0, 10),
    "temperature": (0, 5),
}
ANALYSIS_INTEGER_FIELDS = ("depth", "iterations", "rollout_depth", "seed")
ANALYSIS_FIELDS = frozenset(
    {
        "algorithm",
        "depth",
        "time_limit",
        "seed",
        "iterations",
        "rollout_depth",
        "exploration",
        "temperature",
        "heuristic",
        "search",
    }
)


def snapshot(state: State) -> dict:
    return {"state": state.to_dict(), "legal_moves": [m.to_dict() for m in legal_moves(state)]}


def analysis_config(raw: object) -> dict:
    """Validate explicit bounded local-analysis settings; models are never loaded.

    Production callers must name the algorithm: a missing algorithm is rejected
    rather than silently falling back to the baseline agent. Jarl is the explicit
    ``alpha_beta`` configuration supplied by the runtime manager.
    """
    if not isinstance(raw, dict) or set(raw) - ANALYSIS_FIELDS:
        raise ValueError("Unsupported local analysis settings; no model loading")
    if "algorithm" not in raw:
        raise ValueError("An explicit analysis algorithm is required")
    config = {"algorithm": "alpha_beta", "time_limit": 0.1, "seed": 7, **raw}
    if config["algorithm"] not in {"alpha_beta", "mcts"}:
        raise ValueError("Only explicit alpha_beta or classical mcts analysis is supported")
    if "heuristic" in config and config["heuristic"] not in ("shield_bearer", "shield_maiden"):
        raise ValueError("Unsupported search heuristic")
    for name, (low, high) in ANALYSIS_RANGES.items():
        if name not in config:
            continue
        value = config[name]
        if name in ANALYSIS_INTEGER_FIELDS and type(value) is not int:
            raise ValueError(f"Invalid bounded {name}")
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not low <= value <= high
        ):
            raise ValueError(f"Invalid bounded {name}")
    seconds = config["time_limit"]
    if (
        type(seconds) not in (int, float)
        or not math.isfinite(seconds)
        or not 0.01 <= seconds <= MAX_ANALYSIS_TIME
    ):
        raise ValueError(f"Local search time must be between 0.01 and {MAX_ANALYSIS_TIME} seconds")
    if "search" in config:
        from hnefatafl.ai import clean_search_config

        config["search"] = clean_search_config(config["search"])
    if config["algorithm"] == "mcts":
        config.setdefault("iterations", 32)
        config.setdefault("rollout_depth", 4)
    return config


def local_analysis(request: dict) -> dict:
    from hnefatafl.ai import analyze_position

    state = State.from_dict(request["state"])
    config = analysis_config(request.get("config", {}))
    before = state.to_dict()
    identity = json.dumps(
        {"state": before, "settings": config, "protocol": 1},
        sort_keys=True,
        separators=(",", ":"),
    )
    start = time.perf_counter()
    analysis = analyze_position(state, config)
    if before != state.to_dict():
        raise ValueError("Analysis must not mutate the position")
    return {
        "schema": 1,
        "backend": "local-python",
        "ruleset": state.ruleset,
        "position_key": hashlib.sha256(identity.encode()).hexdigest(),
        "settings": config,
        "analysis": analysis,
        "elapsed_ms": (time.perf_counter() - start) * 1000,
        "authoritative": False,
    }


from hnefatafl.browser_engine_identity import engine_identity  # noqa: E402


def info() -> dict:
    return {
        "protocol": BRIDGE_PROTOCOL,
        "python": sys.version,
        "engine_identity": engine_identity(),
        "bounds": {
            "max_analysis_time": MAX_ANALYSIS_TIME,
            "max_payload_bytes": MAX_BRIDGE_BYTES,
            **{name: list(bounds) for name, bounds in ANALYSIS_RANGES.items()},
        },
        "authoritative": False,
    }


def dispatch(payload: str) -> str:
    """Bounded production bridge dispatch; malformed input never raises."""
    started = time.perf_counter()
    try:
        if not isinstance(payload, str):
            raise ValueError("Request must be a serialized JSON string")
        if len(payload.encode("utf-8")) > MAX_BRIDGE_BYTES:
            raise ValueError("Bridge request exceeds 512 KiB")
        request = json.loads(payload)
        if not isinstance(request, dict):
            raise ValueError("Request must be an object")
        action = request.get("action")
        if action == "info":
            return json.dumps({"ok": True, "info": info()})
        if action == "position":
            state = State.from_dict(request["state"])
            return json.dumps(
                {
                    "ok": True,
                    "position": snapshot(state),
                    "action_ms": (time.perf_counter() - started) * 1000,
                }
            )
        if action == "analysis":
            result = local_analysis(request)
            result["action_ms"] = (time.perf_counter() - started) * 1000
            return json.dumps({"ok": True, "event": result})
        if action == "move":
            state = State.from_dict(request["state"])
            move = Move(**request["move"])
            if move not in legal_moves(state):
                raise ValueError("Illegal move")
            moved = apply_move(state, move)
            return json.dumps(
                {
                    "ok": True,
                    "position": snapshot(moved),
                    "move": move.to_dict(),
                    "action_ms": (time.perf_counter() - started) * 1000,
                }
            )
        return json.dumps(
            {"ok": False, "code": "UNKNOWN_ACTION", "message": f"Unknown bridge action: {action!r}"}
        )
    except (KeyError, TypeError, ValueError) as error:
        return json.dumps({"ok": False, "code": "INVALID_REQUEST", "message": str(error)})
    except MemoryError:
        return json.dumps(
            {"ok": False, "code": "RESOURCE_EXHAUSTED", "message": "Analysis ran out of memory"}
        )
    except Exception as error:  # noqa: BLE001 - never cross the worker boundary raw
        return json.dumps(
            {"ok": False, "code": "ANALYSIS_FAILED", "message": f"{type(error).__name__}: {error}"}
        )
