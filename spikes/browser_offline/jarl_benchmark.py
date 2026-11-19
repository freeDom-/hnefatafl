"""Shared Jarl/classical benchmark definitions for native CPython and Pyodide.

One module defines the benchmark positions, the named configurations and the
reported metrics so a native CPython run and a browser Pyodide run measure the
same workload. It imports only the pure engine and search modules and is staged
verbatim into the isolated worker by ``prepare.py``. It never loads a neural
model or an HTTP/database module.
"""

from __future__ import annotations

import time
from typing import Any

from hnefatafl.ai import analyze_position

# Production Jarl is the ``alpha_beta`` catalog profile: depth 5, 2 second
# cooperative deadline, Shield maiden evaluation and the optimized search
# defaults. ``standard`` reproduces that preset exactly. The other rows are
# benchmark workloads, not production defaults.
#
# The engine's internal alpha-beta depth cap is 8 (``hnefatafl/ai.py``). The
# public configuration validator caps depth at 5. ``deep`` therefore exercises
# the internal cap through the experimental benchmark interface; ``extended``
# requests depth 10 and is reported as an explicit engine constraint instead of
# being silently reduced.
BENCHMARK_CONFIGS: dict[str, dict[str, float | int]] = {
    "quick": {"depth": 3, "time_limit": 0.25},
    "interactive": {"depth": 5, "time_limit": 1.0},
    "standard": {"depth": 5, "time_limit": 2.0},
    "deep": {"depth": 8, "time_limit": 5.0},
    "extended": {"depth": 10, "time_limit": 10.0},
}
ENGINE_MAX_DEPTH = 8
PRODUCTION_JARL = {"depth": 5, "time_limit": 2.0}
# A fixed-work search must complete the configured depth; the deadline only
# guards against a pathological position. It stays far below the client hard
# watchdog and is reported per case when it is actually reached.
FIXED_WORK_DEADLINE_SECONDS = 60.0
# Production-equivalent search settings are the engine defaults. Listing them
# explicitly makes the browser/native contract reviewable.
PRODUCTION_SEARCH = {
    "legacy": False,
    "transposition": True,
    "move_ordering": True,
    "symmetry_transposition": False,
    "symmetry_pruning": False,
    "table_capacity": 100_000,
}


def benchmark_plan() -> dict[str, Any]:
    """Describe the supported benchmark matrix without running a search."""
    return {
        "configs": BENCHMARK_CONFIGS,
        "production_jarl": PRODUCTION_JARL,
        "engine_max_depth": ENGINE_MAX_DEPTH,
        "unsupported": {
            name: "engine alpha-beta depth cap is 8"
            for name, row in BENCHMARK_CONFIGS.items()
            if row["depth"] > ENGINE_MAX_DEPTH
        },
        "modes": ["fixed_work", "fixed_time"],
        "search": PRODUCTION_SEARCH,
    }


def resolve_config(name: str, *, mode: str, search: dict | None = None) -> dict[str, Any]:
    """Return the effective engine configuration for one benchmark row.

    ``fixed_work`` raises the deadline so the configured depth is actually
    reached; ``fixed_time`` keeps the user-facing time budget. Depth 10 is
    rejected by the engine itself and surfaces as a normal search error.
    """
    if name not in BENCHMARK_CONFIGS:
        raise ValueError(f"Unknown benchmark configuration: {name}")
    if mode not in ("fixed_work", "fixed_time"):
        raise ValueError("Benchmark mode must be fixed_work or fixed_time")
    row = BENCHMARK_CONFIGS[name]
    time_limit = FIXED_WORK_DEADLINE_SECONDS if mode == "fixed_work" else row["time_limit"]
    return {
        "algorithm": "alpha_beta",
        "depth": row["depth"],
        "time_limit": time_limit,
        "heuristic": "shield_maiden",
        "search": dict(PRODUCTION_SEARCH if search is None else search),
    }


def run_case(
    state,
    name: str,
    *,
    mode: str = "fixed_time",
    seed: int = 7,
    search: dict | None = None,
) -> dict[str, Any]:
    """Run one bounded search and return a flat, machine-readable record.

    The position is never mutated. A search error (for example the unsupported
    depth-10 row) is captured per case rather than aborting the whole suite.
    """
    config = resolve_config(name, mode=mode, search=search)
    config["seed"] = seed
    budget_ms = float(config["time_limit"]) * 1000
    started = time.perf_counter()
    try:
        analysis = analyze_position(state, config)
        error = None
    except Exception as exc:  # noqa: BLE001 - reported per case, not swallowed
        analysis = {}
        error = f"{type(exc).__name__}: {exc}"
    elapsed_ms = (time.perf_counter() - started) * 1000
    nodes = analysis.get("nodes")
    evaluations = analysis.get("evaluations")
    lookups = analysis.get("transposition_lookups")
    hits = analysis.get("transposition_hits")
    hit_rate = (hits / lookups) if isinstance(lookups, int) and lookups else None
    completed = analysis.get("completed_depth")
    interrupted = analysis.get("interrupted_depth")
    return {
        "config": name,
        "mode": mode,
        "ruleset": state.ruleset,
        "seed": seed,
        "elapsed_ms": elapsed_ms,
        "time_limit_ms": budget_ms,
        "deadline_overshoot_ms": (elapsed_ms - budget_ms) if mode == "fixed_time" else None,
        "best_move": analysis.get("best_move"),
        "value": analysis.get("value"),
        "source": analysis.get("source"),
        "completed_depth": completed,
        "interrupted_depth": interrupted,
        "nodes": nodes,
        "evaluations": evaluations,
        "cutoffs": analysis.get("cutoffs"),
        "successors": analysis.get("successors"),
        "transposition_lookups": lookups,
        "transposition_hits": hits,
        "transposition_hit_rate": hit_rate,
        "nodes_per_second": analysis.get("nodes_per_second"),
        "evaluations_per_second": (
            evaluations / (elapsed_ms / 1000)
            if isinstance(evaluations, int) and elapsed_ms
            else None
        ),
        "deadline_limited": interrupted is not None,
        "error": error,
    }


def run_suite(
    positions: list[dict[str, Any]],
    configs: list[str],
    *,
    mode: str = "fixed_time",
    seed: int = 7,
) -> dict[str, Any]:
    """Run every (position, configuration) pair and return records plus plan."""
    from hnefatafl.engine import State

    records: list[dict[str, Any]] = []
    for case in positions:
        state = State.from_dict(case["state"])
        for name in configs:
            record = run_case(state, name, mode=mode, seed=seed)
            record["position"] = case["name"]
            record["kind"] = case.get("kind")
            records.append(record)
    return {"plan": benchmark_plan(), "mode": mode, "seed": seed, "records": records}
