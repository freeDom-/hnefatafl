"""Loaded only inside the spike's Pyodide worker; no server imports."""

import json
import sys
import time

from hnefatafl.ai import choose_move
from hnefatafl.engine import Move, State, apply_move, legal_moves, new_game


def snapshot(state):
    return {"state": state.to_dict(), "legal_moves": [m.to_dict() for m in legal_moves(state)]}


def dispatch(payload):
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
