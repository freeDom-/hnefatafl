"""Loaded only inside the spike's Pyodide worker; no server imports."""

import hashlib
import json
import math
import sys
import time

from hnefatafl.ai import analyze_position, choose_move, clean_search_config
from hnefatafl.engine import Move, State, apply_move, legal_moves, new_game

# Experimental classical-analysis bounds. These are deliberately wider than the
# original feasibility limits but still explicit and finite. The engine itself
# caps alpha-beta depth at 8; a larger request is rejected with a clear message
# rather than silently reduced.
ANALYSIS_RANGES = {
    "depth": (1, 8),
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
MAX_ANALYSIS_TIME = 10.0


def snapshot(state):
    return {"state": state.to_dict(), "legal_moves": [m.to_dict() for m in legal_moves(state)]}


def dispatch(payload):
    if not isinstance(payload, str):
        raise ValueError("Spike request must be a serialized JSON string")
    if len(payload.encode("utf-8")) > 512 * 1024:
        raise ValueError("Spike input exceeds 512 KiB")
    request = json.loads(payload)
    action = request["action"]
    if action == "info":
        return json.dumps(
            {
                "python": sys.version,
                "strategy": "baseline",
                "time_limit": 0.1,
                "max_analysis_time": MAX_ANALYSIS_TIME,
                "max_depth": ANALYSIS_RANGES["depth"][1],
                "max_iterations": ANALYSIS_RANGES["iterations"][1],
            }
        )
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
    if action == "jarl_plan":
        from jarl_benchmark import benchmark_plan

        return json.dumps(benchmark_plan())
    if action == "jarl_identity":
        return json.dumps(jarl_identity(request))
    if action == "jarl_benchmark":
        return json.dumps(run_jarl_benchmark(request))
    if action == "jarl_case":
        return json.dumps(run_jarl_case(request))
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
    if not isinstance(raw, dict) or set(raw) - ANALYSIS_FIELDS:
        raise ValueError("Unsupported local analysis settings; no model loading")
    config = {"algorithm": "baseline", "time_limit": 0.1, "seed": 7, **raw}
    if config["algorithm"] not in {"baseline", "alpha_beta", "mcts"}:
        raise ValueError("Only classical local analysis is supported")
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
        config["search"] = clean_search_config(config["search"])
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


def jarl_identity(request):
    """Return the workload identity for a benchmark request without searching.

    The client uses this to record the exact workload digest and effective
    configuration in its report, so a report from a different workload or a
    different engine revision is never compared as if it were equivalent.
    """
    from jarl_benchmark import engine_identity, workload_identity

    positions = request.get("positions")
    configs = request.get("configs")
    if not isinstance(positions, list) or not 1 <= len(positions) <= 64:
        raise ValueError("Benchmark positions must be a list of 1 to 64 entries")
    if not isinstance(configs, list) or not 1 <= len(configs) <= 8:
        raise ValueError("Benchmark configs must be a list of 1 to 8 names")
    if not all(isinstance(name, str) for name in configs):
        raise ValueError("Benchmark config names must be strings")
    names = [case.get("name") for case in positions]
    if len(set(names)) != len(names):
        raise ValueError("Benchmark position names must be unique")
    mode = request.get("mode", "fixed_time")
    if mode not in ("fixed_time", "fixed_work"):
        raise ValueError("Benchmark mode must be fixed_time or fixed_work")
    seed = request.get("seed", 7)
    if type(seed) is not int or not 0 <= seed <= 2**32 - 1:
        raise ValueError("Invalid benchmark seed")
    workload = workload_identity(positions, configs, mode=mode, seed=seed)
    return {
        "protocol": workload["protocol"],
        "workload": workload,
        "workload_digest": workload["digest"],
        "mode": mode,
        "seed": seed,
        "engine_identity": engine_identity(),
        "effective_configs": workload["effective_configs"],
    }


def run_jarl_benchmark(request):
    """Run the shared Jarl benchmark matrix inside the worker.

    The client supplies the same serialized positions a native CPython run
    reads, so both sides compare identical workloads. The response is a bounded
    list of flat records; the plan reports any engine-constrained configuration.
    """
    from jarl_benchmark import run_suite

    positions = request.get("positions")
    configs = request.get("configs")
    if not isinstance(positions, list) or not 1 <= len(positions) <= 64:
        raise ValueError("Benchmark positions must be a list of 1 to 64 entries")
    if not isinstance(configs, list) or not 1 <= len(configs) <= 8:
        raise ValueError("Benchmark configs must be a list of 1 to 8 names")
    if not all(isinstance(name, str) for name in configs):
        raise ValueError("Benchmark config names must be strings")
    mode = request.get("mode", "fixed_time")
    if mode not in ("fixed_time", "fixed_work"):
        raise ValueError("Benchmark mode must be fixed_time or fixed_work")
    seed = request.get("seed", 7)
    if type(seed) is not int or not 0 <= seed <= 2**32 - 1:
        raise ValueError("Invalid benchmark seed")
    names = [case.get("name") for case in positions]
    if len(set(names)) != len(names):
        raise ValueError("Benchmark position names must be unique")
    return run_suite(positions, configs, mode=mode, seed=seed)


def run_jarl_case(request):
    """Run exactly one (position, config) pair.

    A single request per case keeps every search independently bounded: a slow
    or failing case cannot discard records already returned for earlier cases,
    and the client can apply a per-case watchdog instead of one oversized batch
    deadline.
    """
    from jarl_benchmark import position_digest, run_case

    positions = request.get("positions")
    configs = request.get("configs")
    if not isinstance(positions, list) or len(positions) != 1:
        raise ValueError("jarl_case requires exactly one position")
    if not isinstance(configs, list) or len(configs) != 1:
        raise ValueError("jarl_case requires exactly one config")
    entry = positions[0]
    if not isinstance(entry, dict) or "state" not in entry:
        raise ValueError("jarl_case position needs a state")
    mode = request.get("mode", "fixed_time")
    if mode not in ("fixed_time", "fixed_work"):
        raise ValueError("Benchmark mode must be fixed_time or fixed_work")
    seed = request.get("seed", 7)
    if type(seed) is not int or not 0 <= seed <= 2**32 - 1:
        raise ValueError("Invalid benchmark seed")
    state = State.from_dict(entry["state"])
    return run_case(
        state,
        configs[0],
        mode=mode,
        seed=seed,
        position=entry.get("name"),
        kind=entry.get("kind"),
        position_hash=position_digest(entry),
    )
