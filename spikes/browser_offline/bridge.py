"""Loaded only inside the spike's Pyodide worker; no server imports."""

import hashlib
import json
import math
import sys
import time

from hnefatafl.ai import analyze_position, choose_move
from hnefatafl.engine import Move, State, apply_move, legal_moves, new_game


def snapshot(state):
    return {"state": state.to_dict(), "legal_moves": [m.to_dict() for m in legal_moves(state)]}


def dispatch(payload):
    if len(payload.encode()) > 512 * 1024:
        raise ValueError("Spike input exceeds 512 KiB")
    request = json.loads(payload)
    action = request["action"]
    if action == "info":
        return json.dumps({"python": sys.version, "strategy": "baseline", "time_limit": 0.1})
    if action == "probe":
        start = time.perf_counter()
        while time.perf_counter() - start < 0.5:
            pass
        return json.dumps({"worker_busy_ms": (time.perf_counter() - start) * 1000})
    if action == "fixtures":
        for case in request["vectors"]:
            before = State.from_dict(case["before"]["state"])
            assert snapshot(before) == case["before"], case["name"] + " before"
            moved = apply_move(before, Move(**case["move"]))
            assert snapshot(moved) == case["after"], case["name"] + " after"
        return json.dumps({"passed": len(request["vectors"])})
    if action == "analysis":
        return json.dumps(local_analysis(request))
    if action == "restore":
        return json.dumps(snapshot(State.from_dict(request["state"])))
    if action == "new":
        return json.dumps(snapshot(new_game(request["ruleset"])))
    state = State.from_dict(request["state"])
    if action == "move":
        return json.dumps(snapshot(apply_move(state, Move(**request["move"]))))
    if action == "ai":
        start = time.perf_counter()
        # Only the existing bounded baseline; never neural models or package loading.
        move = choose_move(state, {"algorithm": "baseline", "seed": 7, "time_limit": 0.1})
        elapsed = (time.perf_counter() - start) * 1000
        return json.dumps(
            {**snapshot(apply_move(state, move)), "move": move.to_dict(), "ai_ms": elapsed}
        )
    raise ValueError("Unknown spike action")


def analysis_config(raw):
    if not isinstance(raw, dict) or set(raw) - {
        "algorithm",
        "depth",
        "time_limit",
        "seed",
        "iterations",
        "rollout_depth",
    }:
        raise ValueError("Unsupported local analysis settings; no model loading")
    config = {"algorithm": "baseline", "time_limit": 0.1, "seed": 7, **raw}
    if config["algorithm"] not in {"baseline", "alpha_beta", "mcts"}:
        raise ValueError("Only classical local analysis is supported")
    for name, low, high in (
        ("depth", 1, 5),
        ("iterations", 1, 128),
        ("rollout_depth", 0, 8),
        ("seed", 0, 2**32 - 1),
    ):
        if name in config and (type(config[name]) is not int or not low <= config[name] <= high):
            raise ValueError(f"Invalid bounded {name}")
    seconds = config["time_limit"]
    if (
        type(seconds) not in (int, float)
        or not math.isfinite(seconds)
        or not 0.01 <= seconds <= 0.5
    ):
        raise ValueError("Local search time must be between 0.01 and 0.5 seconds")
    # Keep classical MCTS bounded even when callers omit optional settings.
    if config["algorithm"] == "mcts":
        config.setdefault("iterations", 32)
        config.setdefault("rollout_depth", 4)
    return config


def local_analysis(request):
    state = State.from_dict(request["state"])
    config = analysis_config(request.get("config", {}))
    before = state.to_dict()
    identity = json.dumps(
        {"state": before, "settings": config, "protocol": 1}, sort_keys=True, separators=(",", ":")
    )
    start = time.perf_counter()
    analysis = analyze_position(state, config)
    assert before == state.to_dict(), "Analysis must not mutate the position"
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
