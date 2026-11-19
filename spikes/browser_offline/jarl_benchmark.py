"""Shared Jarl/classical benchmark definitions for native CPython and Pyodide.

One module defines the benchmark positions, the named configurations, workload
identity and the reported per-case metrics so a native CPython run and a browser
Pyodide run measure the same workload. It imports only the pure engine and search
modules and is staged verbatim into the isolated worker by ``prepare.py``. It
never loads a neural model or an HTTP/database module.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import time
from typing import Any

from hnefatafl.ai import analyze_position
from hnefatafl.engine import State, legal_moves

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
# guards against a pathological position. It is bounded well below the client
# hard watchdog and is reported per case when it is actually reached.
FIXED_WORK_DEADLINE_SECONDS = 60.0
# Largest single-case watchdog the browser client may grant a fixed-work case.
FIXED_WORK_WATCHDOG_MS = 75_000
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
# Benchmark modes. ``fixed_work`` must complete the configured depth and is the
# only mode with a mandatory correctness gate; ``fixed_time`` deliberately
# completes different work on different runtimes.
MODES = ("fixed_work", "fixed_time")
# Case classifications. A terminal position is not a failed search and a
# deadline-limited search is not a completed one.
COMPLETED = "completed"
TERMINAL = "terminal"
DEADLINE_LIMITED = "deadline_limited"
UNSUPPORTED = "unsupported"
ERROR = "error"
INCOMPLETE = "incomplete"


def benchmark_plan() -> dict[str, Any]:
    """Describe the supported benchmark matrix without running a search."""
    return {
        "configs": BENCHMARK_CONFIGS,
        "production_jarl": PRODUCTION_JARL,
        "engine_max_depth": ENGINE_MAX_DEPTH,
        "fixed_work_deadline_seconds": FIXED_WORK_DEADLINE_SECONDS,
        "fixed_work_watchdog_ms": FIXED_WORK_WATCHDOG_MS,
        "unsupported": {
            name: "engine alpha-beta depth cap is 8"
            for name, row in BENCHMARK_CONFIGS.items()
            if row["depth"] > ENGINE_MAX_DEPTH
        },
        "modes": list(MODES),
        "search": PRODUCTION_SEARCH,
    }


def engine_identity() -> dict[str, object]:
    """Hash the staged tafl search sources so two runs share an engine version.

    The hash is computed from the actual loaded source, so a native checkout and
    a Pyodide run built from the same files compare equal without hard-coding a
    version string. If a runtime cannot expose module source the identity is
    reported as unavailable and the comparator refuses the comparison.
    """
    from hnefatafl import ai, engine, shield_maiden, symmetry

    modules = (engine, ai, shield_maiden, symmetry)
    digest = hashlib.sha256()
    names = []
    try:
        for module in modules:
            names.append(module.__name__)
            digest.update(module.__name__.encode())
            digest.update(b"\0")
            digest.update(inspect.getsource(module).encode())
            digest.update(b"\0")
    except (OSError, TypeError, IndexError):
        return {"modules": names, "sha256": None, "reason": "source unavailable"}
    return {"modules": names, "sha256": digest.hexdigest()}


def build_report(
    *,
    kind: str,
    runtime: str,
    environment: dict[str, Any],
    positions: list[dict[str, Any]],
    configs: list[str],
    mode: str,
    seed: int,
    records: list[dict[str, Any]],
    search: dict | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble a normalised report with a comparable identity block.

    Both the native and the browser runners call this so their reports carry the
    same ``identity`` shape. The workload digest covers the ordered serialized
    positions (including repetition history) and the complete effective
    configuration for every row, so two different workloads can never be
    compared as if they were the same.
    """
    workload = workload_identity(positions, configs, mode=mode, seed=seed, search=search)
    identity = {
        "protocol": workload["protocol"],
        "workload": workload,
        "workload_digest": workload["digest"],
        "mode": mode,
        "seed": seed,
        "engine_identity": engine_identity(),
        "effective_configs": workload["effective_configs"],
        "required_cases": [
            {"position": c.get("position"), "config": c.get("config"), "kind": c.get("kind")}
            for c in records
        ],
    }
    report = {
        "schema": 2,
        "kind": kind,
        "runtime": runtime,
        "environment": environment,
        "plan": benchmark_plan(),
        "identity": identity,
        "records": records,
    }
    if extra:
        report.update(extra)
    return report


def resolve_config(name: str, *, mode: str, search: dict | None = None) -> dict[str, Any]:
    """Return the effective engine configuration for one benchmark row.

    ``fixed_work`` raises the deadline so the configured depth is actually
    reached; ``fixed_time`` keeps the user-facing time budget. Depth 10 is
    rejected by the engine itself and surfaces as a normal search error.
    """
    if name not in BENCHMARK_CONFIGS:
        raise ValueError(f"Unknown benchmark configuration: {name}")
    if mode not in MODES:
        raise ValueError(f"Benchmark mode must be one of {MODES}")
    row = BENCHMARK_CONFIGS[name]
    time_limit = FIXED_WORK_DEADLINE_SECONDS if mode == "fixed_work" else row["time_limit"]
    return {
        "algorithm": "alpha_beta",
        "depth": row["depth"],
        "time_limit": time_limit,
        "heuristic": "shield_maiden",
        "search": dict(PRODUCTION_SEARCH if search is None else search),
    }


def effective_configs(
    configs: list[str], *, mode: str, search: dict | None = None
) -> dict[str, dict[str, Any]]:
    """Return the complete effective configuration for every requested row."""
    return {name: resolve_config(name, mode=mode, search=search) for name in configs}


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def position_digest(case: dict[str, Any]) -> str:
    """Digest one serialized position including its full repetition history."""
    state = case.get("state")
    return hashlib.sha256(_canonical(state).encode()).hexdigest()


def workload_identity(
    positions: list[dict[str, Any]],
    configs: list[str],
    *,
    mode: str,
    seed: int,
    search: dict | None = None,
) -> dict[str, Any]:
    """Deterministic identity of the exact workload both runtimes must share.

    The digest covers the ordered serialized states (including repetition
    history), names, kinds, the effective engine configuration for each row and
    the mode/seed. Two reports with different digests must never be compared.
    """
    effective = effective_configs(configs, mode=mode, search=search)
    payload = {
        "protocol": 2,
        "mode": mode,
        "seed": seed,
        "configs": configs,
        "effective_configs": effective,
        "positions": [
            {
                "name": case.get("name"),
                "kind": case.get("kind"),
                "ruleset": case.get("state", {}).get("ruleset"),
                "position_digest": position_digest(case),
            }
            for case in positions
        ],
    }
    return {
        "protocol": 2,
        "digest": hashlib.sha256(_canonical(payload).encode()).hexdigest(),
        "mode": mode,
        "seed": seed,
        "configs": configs,
        "effective_configs": effective,
        "cases": [
            {
                "name": case.get("name"),
                "kind": case.get("kind"),
                "ruleset": case.get("state", {}).get("ruleset"),
                "position_digest": position_digest(case),
            }
            for case in positions
        ],
    }


def classify(record: dict[str, Any]) -> str:
    """Classify a recorded case for the correctness gate.

    Terminal positions legitimately return no best move; they are not failed
    searches. An unsupported engine configuration is reported explicitly, and a
    deadline-limited search is distinct from a completed one.
    """
    if record.get("terminal"):
        return TERMINAL
    if record.get("unsupported"):
        return UNSUPPORTED
    if record.get("error"):
        return ERROR
    expected = record.get("expected_depth")
    completed = record.get("completed_depth")
    interrupted = record.get("interrupted_depth")
    if interrupted is not None:
        return DEADLINE_LIMITED
    if not isinstance(completed, int) or not isinstance(expected, int):
        return INCOMPLETE
    if completed < expected:
        return INCOMPLETE
    return COMPLETED


def _is_unsupported(exc: Exception) -> bool:
    return isinstance(exc, ValueError) and "between 1 and 8" in str(exc)


def run_case(
    state,
    name: str,
    *,
    mode: str = "fixed_time",
    seed: int = 7,
    search: dict | None = None,
    position: str | None = None,
    kind: str | None = None,
    position_hash: str | None = None,
) -> dict[str, Any]:
    """Run one bounded search and return a flat, machine-readable record.

    The position is never mutated. A search error (for example the unsupported
    depth-10 row) is captured per case rather than aborting the whole suite.
    Per-case latency is measured only around this case; a caller must not attach
    a whole-batch duration to an individual record.
    """
    config = resolve_config(name, mode=mode, search=search)
    config["seed"] = seed
    budget_ms = float(config["time_limit"]) * 1000
    expected_depth = BENCHMARK_CONFIGS[name]["depth"]
    terminal = bool(state.winner) or not legal_moves(state)
    started = time.perf_counter()
    analysis: dict[str, Any] = {}
    error: str | None = None
    unsupported = False
    try:
        analysis = analyze_position(state, config)
    except Exception as exc:  # noqa: BLE001 - reported per case, not swallowed
        error = f"{type(exc).__name__}: {exc}"
        unsupported = _is_unsupported(exc)
    elapsed_ms = (time.perf_counter() - started) * 1000
    nodes = analysis.get("nodes")
    evaluations = analysis.get("evaluations")
    lookups = analysis.get("transposition_lookups")
    hits = analysis.get("transposition_hits")
    hit_rate = (hits / lookups) if isinstance(lookups, int) and lookups else None
    record: dict[str, Any] = {
        "config": name,
        "mode": mode,
        "ruleset": state.ruleset,
        "seed": seed,
        "position": position,
        "kind": kind,
        "position_hash": position_hash,
        "expected_depth": expected_depth,
        "terminal": terminal,
        "unsupported": unsupported,
        "elapsed_ms": elapsed_ms,
        "time_limit_ms": budget_ms,
        "deadline_overshoot_ms": (
            max(0.0, elapsed_ms - budget_ms) if mode == "fixed_time" else None
        ),
        "best_move": analysis.get("best_move"),
        "value": analysis.get("value"),
        "source": analysis.get("source"),
        "completed_depth": analysis.get("completed_depth"),
        "interrupted_depth": analysis.get("interrupted_depth"),
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
        "deadline_limited": analysis.get("interrupted_depth") is not None,
        "error": error,
    }
    record["classification"] = classify(record)
    record["status"] = record["classification"]
    return record


def run_suite(
    positions: list[dict[str, Any]],
    configs: list[str],
    *,
    mode: str = "fixed_time",
    seed: int = 7,
    search: dict | None = None,
) -> dict[str, Any]:
    """Run every (position, configuration) pair and return records plus plan."""
    records: list[dict[str, Any]] = []
    for case in positions:
        state = State.from_dict(case["state"])
        digest = position_digest(case)
        for name in configs:
            record = run_case(
                state,
                name,
                mode=mode,
                seed=seed,
                search=search,
                position=case.get("name"),
                kind=case.get("kind"),
                position_hash=digest,
            )
            records.append(record)
    return {
        "plan": benchmark_plan(),
        "mode": mode,
        "seed": seed,
        "workload": workload_identity(positions, configs, mode=mode, seed=seed, search=search),
        "engine_identity": engine_identity(),
        "records": records,
    }
