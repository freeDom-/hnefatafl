"""Reproducible alpha-beta search-optimization benchmark.

This command exercises the real production search path (``choose_move`` with the
``alpha_beta`` algorithm) rather than a simplified reimplementation. It compares
the original search, an ordinary bounded transposition table, D4-canonical
transpositions, full-state symmetry move pruning and improved move ordering.

Fixed-depth scenarios must preserve the reference minimax value. A separate
independent exhaustive minimax oracle validates feasible positions. Fixed-time
scenarios compare last completed depth and decision latency under equal budgets.
All timings are single-process host measurements; canonicalization overhead is
reported separately from the search counters.
"""

from __future__ import annotations

import hashlib
import json
import platform
from pathlib import Path
import random
import statistics
import time
import tracemalloc
from typing import Callable

from .ai import choose_move, evaluate
from .engine import RULESETS, Move, State, apply_move, legal_moves, new_game
from .shield_maiden import DecisionContext, normalise


DEFAULTS = {
    "seed": 20260101,
    "depth": {"brandubh": 3, "tablut": 3, "copenhagen": 2},
    "time_limit": 0.25,
    "repetitions": 3,
    "oracle_depth": 2,
    "oracle_node_budget": 200_000,
    "table_capacity": 200_000,
    "warmup": True,
    "scenarios": None,
}

# Feature ablation. ``legacy`` keeps the original exact-value cache search as the
# reference; every other scenario shares the production code path. The
# ``ordering_*`` scenarios isolate each move-ordering priority behind the
# benchmark-only ``_ordering`` hook (never part of a saved search identity).
SCENARIOS: dict[str, dict] = {
    "legacy": {"legacy": True},
    "transposition": {"transposition": True, "move_ordering": False},
    "ordering": {"transposition": True, "move_ordering": True},
    "ordering_static": {"transposition": True, "move_ordering": True, "_ordering": "static"},
    "ordering_pv": {"transposition": True, "move_ordering": True, "_ordering": "pv"},
    "ordering_tt": {"transposition": True, "move_ordering": True, "_ordering": "tt"},
    "ordering_killer": {"transposition": True, "move_ordering": True, "_ordering": "killer"},
    "ordering_history": {"transposition": True, "move_ordering": True, "_ordering": "history"},
    "ordering_depth": {"transposition": True, "move_ordering": True, "_ordering": "depth"},
    "symmetry_transposition": {
        "transposition": True,
        "symmetry_transposition": True,
        "move_ordering": True,
    },
    "symmetry_pruning": {
        "transposition": True,
        "symmetry_pruning": True,
        "move_ordering": True,
    },
    "all": {
        "transposition": True,
        "symmetry_transposition": True,
        "symmetry_pruning": True,
        "move_ordering": True,
    },
}

# The production ablation keeps the original six scenarios. A dedicated
# ordering comparison selects the ``ordering_*`` names through ``scenarios``.
DEFAULT_SCENARIOS = (
    "legacy",
    "transposition",
    "ordering",
    "symmetry_transposition",
    "symmetry_pruning",
    "all",
)


def _position(ruleset: str, pieces: dict[tuple[int, int], str], turn: str) -> State:
    n = RULESETS[ruleset].size
    board = ["."] * (n * n)
    for (row, col), piece in pieces.items():
        board[row * n + col] = piece
    return State(ruleset, tuple(board), turn)


def _random_game_states(ruleset: str, seed: int, plies: int, marks: set[int]) -> list[State]:
    state = new_game(ruleset)
    rng = random.Random(seed)
    captured: list[State] = []
    for ply in range(plies):
        if state.winner or not legal_moves(state):
            break
        if ply in marks:
            captured.append(state)
        moves = legal_moves(state)
        state = apply_move(state, rng.choice(moves))
    return captured


def _repetition_states(ruleset: str) -> list[State]:
    """Build a genuine two-sided shuffle with growing repetition history."""
    n = RULESETS[ruleset].size
    center = n // 2
    state = _position(
        ruleset,
        {
            (center, center): "K",
            (1, 1): "A",
            (n - 2, n - 2): "D",
        },
        # The cycle below starts with the attacker, independent of the
        # ruleset's normal first player, because this is a hand-built position.
        "attackers",
    )
    cycle = [
        ((1, 1), (1, 2)),
        ((n - 2, n - 2), (n - 2, n - 3)),
        ((1, 2), (1, 1)),
        ((n - 2, n - 3), (n - 2, n - 2)),
    ]
    captured: list[State] = []
    for index, (source, target) in enumerate(cycle * 2):
        if state.winner or not legal_moves(state):
            break
        if index in (3, 5):
            captured.append(state)
        state = apply_move(state, Move(source[0] * n + source[1], target[0] * n + target[1]))
    return captured


def corpus(seed: int) -> list[tuple[str, State]]:
    """Deterministic positions covering all three rulesets and search hazards."""
    positions: list[tuple[str, State]] = []
    for ruleset in RULESETS:
        positions.append((f"{ruleset}/opening", new_game(ruleset)))
        for index, state in enumerate(
            _random_game_states(ruleset, seed + 101 * len(ruleset), 48, {4, 10, 16, 24, 40})
        ):
            positions.append((f"{ruleset}/midgame-{index}", state))
        for index, state in enumerate(_repetition_states(ruleset)):
            positions.append((f"{ruleset}/repetition-{index}", state))
        n = RULESETS[ruleset].size
        # Immediate royal escape: the king is one orthogonal step from its goal.
        # Brandubh/Copenhagen escape to a corner, Tablut to any edge.
        king = (1, n // 2) if ruleset == "tablut" else (1, 0)
        positions.append(
            (
                f"{ruleset}/immediate-escape",
                _position(
                    ruleset,
                    {king: "K", (n - 2, n - 2): "D", (n - 3, n - 2): "A", (n - 2, n - 3): "A"},
                    "defenders",
                ),
            )
        )
        # King capture threat with a clear escape square.
        positions.append(
            (
                f"{ruleset}/king-threat",
                _position(
                    ruleset,
                    {
                        (n // 2, n // 2): "K",
                        (n // 2 - 1, n // 2): "A",
                        (n // 2, n // 2 - 1): "A",
                        (n // 2 + 1, n // 2): "A",
                        (n // 2, n // 2 + 1): "A",
                        (n - 2, n - 2): "D",
                    },
                    "defenders",
                ),
            )
        )
        # Restricted-square tactic: a defender blocked by the corner/throne.
        positions.append(
            (
                f"{ruleset}/restricted-square",
                _position(
                    ruleset,
                    {(n - 1, 1): "D", (n - 2, 1): "A", (n - 1, 2): "A", (1, 1): "K"},
                    "defenders",
                ),
            )
        )
        # Attacker-side mirror for alternating maximizing/minimizing coverage.
        positions.append(
            (
                f"{ruleset}/attacker-turn",
                _position(
                    ruleset,
                    {(1, 1): "K", (n - 2, n - 2): "D", (2, 2): "A", (3, 3): "A"},
                    "attackers",
                ),
            )
        )
    # Copenhagen-specific rules surfaces.
    positions.extend(
        [
            (
                "copenhagen/shieldwall",
                _position(
                    "copenhagen",
                    {
                        (0, 3): "D",
                        (0, 4): "D",
                        (0, 5): "D",
                        (0, 6): "A",
                        (1, 3): "A",
                        (1, 4): "A",
                        (1, 5): "A",
                        (5, 5): "K",
                        (9, 9): "D",
                        (3, 2): "A",
                    },
                    "attackers",
                ),
            ),
            (
                "copenhagen/encirclement",
                _position(
                    "copenhagen",
                    {
                        **{(3, col): "A" for col in range(3, 9) if col != 5},
                        **{(8, col): "A" for col in range(3, 9)},
                        **{(row, 3): "A" for row in range(4, 8)},
                        **{(row, 8): "A" for row in range(4, 8)},
                        (1, 5): "A",
                        (5, 5): "K",
                        (5, 6): "D",
                    },
                    "attackers",
                ),
            ),
            (
                "copenhagen/edge-fort",
                _position(
                    "copenhagen",
                    {
                        (10, 3): "D",
                        (10, 4): "K",
                        (10, 6): "D",
                        (9, 4): "D",
                        (9, 5): "D",
                        (1, 1): "A",
                    },
                    "attackers",
                ),
            ),
        ]
    )
    return [(label, state) for label, state in positions if not state.winner and legal_moves(state)]


def _minimax(
    state: State, depth: int, budget: list[int], incoming: float | None = None
) -> float | None:
    if budget[0] <= 0:
        return None
    budget[0] -= 1
    if state.winner:
        return evaluate(state)
    if depth == 0:
        return incoming if incoming is not None else evaluate(state)
    moves = legal_moves(state)
    if not moves:
        return -1.0 if state.turn == "defenders" else 1.0
    context = DecisionContext(state)
    values: list[float] = []
    for move in moves:
        child = apply_move(state, move)
        value = _minimax(child, depth - 1, budget, normalise(context.score(child, move)))
        if value is None:
            return None
        values.append(value)
    return max(values) if state.turn == "defenders" else min(values)


def oracle_value(state: State, depth: int, node_budget: int) -> float | None:
    return _minimax(state, depth, [node_budget])


def _distribution(values: list[float]) -> dict:
    ordered = sorted(values)
    return {
        "count": len(values),
        "median": statistics.median(values),
        "min": ordered[0],
        "max": ordered[-1],
    }


def _run(
    state: State,
    scenario: dict,
    *,
    depth: int,
    time_limit: float,
    seed: int,
    table_capacity: int,
    measure_memory: bool = False,
) -> dict:
    search = {key: value for key, value in scenario.items() if key != "_ordering"}
    config = {
        "algorithm": "alpha_beta",
        "depth": depth,
        "time_limit": time_limit,
        "seed": seed,
        "search": {**search, "table_capacity": table_capacity},
    }
    if "_ordering" in scenario:
        config["_ordering"] = scenario["_ordering"]
    # Enable the per-node timing breakdown; integer counters are always kept.
    config["_profile"] = True
    details: dict = {}
    if measure_memory:
        tracemalloc.start()
        tracemalloc.reset_peak()
    started = time.perf_counter()
    move = choose_move(state, config, diagnostics=details)
    elapsed = time.perf_counter() - started
    peak = None
    if measure_memory:
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
    return {
        "move": move.to_dict(),
        "legal": move in legal_moves(state),
        "value": details.get("value"),
        "completed_depth": details.get("completed_depth"),
        "nodes": details.get("nodes"),
        "evaluations": details.get("evaluations"),
        "cutoffs": details.get("cutoffs"),
        "transposition_lookups": details.get("transposition_lookups"),
        "transposition_successful_lookups": details.get("transposition_successful_lookups"),
        "transposition_exact_hits": details.get("transposition_exact_hits"),
        "transposition_bound_hits": details.get("transposition_bound_hits"),
        "transposition_cutoffs": details.get("transposition_cutoffs"),
        "transposition_cross_orientation_hits": details.get("transposition_cross_orientation_hits"),
        "transposition_peak_entries": details.get("transposition_peak_entries"),
        "transposition_depth_compatible": details.get("transposition_depth_compatible"),
        "transposition_board_history_divergence": details.get(
            "transposition_board_history_divergence"
        ),
        "symmetry_hits": details.get("symmetry_hits"),
        "symmetry_moves_skipped": details.get("symmetry_moves_skipped"),
        "successors": details.get("successors"),
        "ordered_moves": details.get("ordered_moves"),
        "chosen_from_completed_iteration": details.get("chosen_from_completed_iteration"),
        "interrupted_depth": details.get("interrupted_depth"),
        "interrupted_seconds": details.get("interrupted_seconds"),
        "discarded_nodes": details.get("discarded_nodes"),
        "canonicalization_seconds": details.get("canonicalization_seconds"),
        "stabilizer_seconds": details.get("stabilizer_seconds"),
        "pruning_seconds": details.get("pruning_seconds"),
        "successor_seconds": details.get("successor_seconds"),
        "sort_seconds": details.get("sort_seconds"),
        "transposition_seconds": details.get("transposition_seconds"),
        "wall_seconds": elapsed,
        "peak_traced_bytes": peak,
    }


def _aggregate(rows: list[dict]) -> dict:
    numeric = (
        "nodes",
        "evaluations",
        "cutoffs",
        "transposition_lookups",
        "transposition_successful_lookups",
        "transposition_exact_hits",
        "transposition_bound_hits",
        "transposition_cutoffs",
        "transposition_cross_orientation_hits",
        "transposition_peak_entries",
        "transposition_depth_compatible",
        "transposition_board_history_divergence",
        "symmetry_hits",
        "symmetry_moves_skipped",
        "successors",
        "ordered_moves",
        "interrupted_seconds",
        "discarded_nodes",
        "canonicalization_seconds",
        "stabilizer_seconds",
        "pruning_seconds",
        "successor_seconds",
        "sort_seconds",
        "transposition_seconds",
        "wall_seconds",
        "peak_traced_bytes",
    )
    result: dict = {"runs": len(rows)}
    for key in numeric:
        values = [row[key] for row in rows if row.get(key) is not None]
        if values:
            result[key] = _distribution([float(value) for value in values])
    depths = [row["completed_depth"] for row in rows if row.get("completed_depth") is not None]
    if depths:
        result["completed_depth"] = {
            "median": statistics.median(depths),
            "min": min(depths),
            "max": max(depths),
        }
    result["legal"] = all(row["legal"] for row in rows)
    return result


def _scenario_order(names: list[str], repetition: int) -> list[str]:
    return names if repetition % 2 == 0 else list(reversed(names))


def run_alpha_beta_benchmark(
    configuration: dict | None = None,
    *,
    should_stop: Callable[[], bool] | None = None,
    report: Callable[[dict], None] | None = None,
) -> dict:
    config = {**DEFAULTS, **(configuration or {})}
    unknown = set(config) - set(DEFAULTS)
    if unknown:
        raise ValueError(f"Unknown alpha-beta benchmark parameter: {sorted(unknown)[0]}")
    for ruleset, depth in config["depth"].items():
        if ruleset not in RULESETS:
            raise ValueError("Benchmark depth names an unknown ruleset")
        if type(depth) is not int or not 1 <= depth <= 6:
            raise ValueError("Benchmark depths must be integers between 1 and 6")
    if not 1 <= config["repetitions"] <= 20:
        raise ValueError("Benchmark repetitions must be between 1 and 20")
    if not 0 < config["time_limit"] <= 60:
        raise ValueError("Benchmark time limit must be positive and at most 60 seconds")
    if type(config["oracle_depth"]) is not int or not 1 <= config["oracle_depth"] <= 4:
        raise ValueError("Benchmark oracle depth must be between 1 and 4")
    seed = int(config["seed"])
    positions = corpus(seed + 7000)
    requested = config["scenarios"]
    if requested is None:
        names = list(DEFAULT_SCENARIOS)
    elif isinstance(requested, list) and requested:
        unknown_scenarios = [name for name in requested if name not in SCENARIOS]
        if unknown_scenarios:
            raise ValueError(f"Unknown benchmark scenario: {unknown_scenarios[0]}")
        names = list(dict.fromkeys(requested))
    else:
        raise ValueError("Benchmark scenarios must be a nonempty list")
    result: dict = {
        "config": config,
        "scenarios": {name: SCENARIOS[name] for name in names},
        "host": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "processor": platform.processor(),
            "device": "CPU",
            "math_threads": 2,
        },
        "corpus_sha256": hashlib.sha256(
            json.dumps(
                [(label, state.to_dict()) for label, state in positions], sort_keys=True
            ).encode()
        ).hexdigest(),
        "source_sha256": {
            name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
            for name in (
                "ai.py",
                "engine.py",
                "symmetry.py",
                "shield_maiden.py",
                "alpha_beta_benchmark.py",
            )
        },
        "fixed_depth": {"positions": [], "summary": {}},
        "fixed_time": {"positions": [], "summary": {}},
        "oracle": {"positions": [], "summary": {}},
    }
    if config["warmup"]:
        warm = new_game("brandubh")
        for name in names:
            _run(
                warm,
                SCENARIOS[name],
                depth=1,
                time_limit=1.0,
                seed=seed,
                table_capacity=10_000,
            )

    depth_rows: dict[str, list[dict]] = {name: [] for name in names}
    for label, state in positions:
        if should_stop and should_stop():
            raise InterruptedError("Alpha-beta benchmark cancelled")
        depth = config["depth"][state.ruleset]
        samples: dict[str, list[dict]] = {name: [] for name in names}
        for repetition in range(config["repetitions"]):
            for name in _scenario_order(names, repetition):
                samples[name].append(
                    _run(
                        state,
                        SCENARIOS[name],
                        depth=depth,
                        time_limit=60.0,
                        seed=seed,
                        table_capacity=config["table_capacity"],
                    )
                )
        # One memory-traced run per scenario, outside the timed repetitions.
        memory: dict[str, int | None] = {}
        for name in names:
            memory[name] = _run(
                state,
                SCENARIOS[name],
                depth=depth,
                time_limit=60.0,
                seed=seed,
                table_capacity=config["table_capacity"],
                measure_memory=True,
            )["peak_traced_bytes"]
        result["fixed_depth"]["positions"].append(
            {
                "position": label,
                "ruleset": state.ruleset,
                "legal_moves": len(legal_moves(state)),
                "scenarios": {
                    name: {
                        **_aggregate(samples[name]),
                        "peak_traced_bytes": memory[name],
                        "values": [row["value"] for row in samples[name]],
                        "moves": [row["move"] for row in samples[name]],
                    }
                    for name in names
                },
            }
        )
        for name in names:
            depth_rows[name].extend(samples[name])
        if report:
            report(
                {
                    "stage": "fixed_depth",
                    "position": label,
                    "done": len(result["fixed_depth"]["positions"]),
                    "total": len(positions),
                }
            )

    reference_values: dict[str, float] = {}
    # The legacy exact-value search is the reference when present. A dedicated
    # ordering comparison that omits legacy falls back to its first scenario,
    # which is still an exact minimax search at the completed depth.
    reference_name = "legacy" if "legacy" in names else names[0]
    for entry in result["fixed_depth"]["positions"]:
        reference_values[entry["position"]] = entry["scenarios"][reference_name]["values"][0]
    mismatches = []
    for entry in result["fixed_depth"]["positions"]:
        expected = reference_values[entry["position"]]
        for name in names:
            for value in entry["scenarios"][name]["values"]:
                if abs(value - expected) > 1e-9:
                    mismatches.append(
                        {
                            "position": entry["position"],
                            "scenario": name,
                            "expected": expected,
                            "value": value,
                        }
                    )
    result["fixed_depth"]["summary"] = {
        "scenarios": {name: _aggregate(rows) for name, rows in depth_rows.items()},
        "minimax_mismatches": mismatches,
        "reference": f"{reference_name} value at the same completed depth",
    }

    # Independent exhaustive oracle at a feasible depth.
    by_label = {label: state for label, state in positions}
    oracle_records: list[dict] = []
    for entry in result["fixed_depth"]["positions"]:
        state = by_label[entry["position"]]
        oracle_expected = oracle_value(state, config["oracle_depth"], config["oracle_node_budget"])
        if oracle_expected is None:
            continue
        record: dict = {
            "position": entry["position"],
            "oracle_value": oracle_expected,
            "scenarios": {},
        }
        for name in names:
            sample = _run(
                state,
                SCENARIOS[name],
                depth=config["oracle_depth"],
                time_limit=60.0,
                seed=seed,
                table_capacity=config["table_capacity"],
            )
            chosen = sample["move"]
            chosen_value = _minimax(
                apply_move(state, Move(**chosen)),
                config["oracle_depth"] - 1,
                [config["oracle_node_budget"]],
            )
            record["scenarios"][name] = {
                "value": sample["value"],
                "optimal": chosen_value is not None and abs(chosen_value - oracle_expected) < 1e-9,
                "legal": sample["legal"],
            }
        oracle_records.append(record)
    result["oracle"]["positions"] = oracle_records
    result["oracle"]["summary"] = {
        name: {
            "positions": sum(
                1 for record in oracle_records if record["scenarios"][name]["optimal"]
            ),
            "value_matches": sum(
                1
                for record in oracle_records
                if abs(record["scenarios"][name]["value"] - record["oracle_value"]) < 1e-9
            ),
            "legal": all(record["scenarios"][name]["legal"] for record in oracle_records),
        }
        for name in names
    }

    time_rows: dict[str, list[dict]] = {name: [] for name in names}
    for label, state in positions:
        if should_stop and should_stop():
            raise InterruptedError("Alpha-beta benchmark cancelled")
        depth = config["depth"][state.ruleset]
        time_samples: dict[str, list[dict]] = {name: [] for name in names}
        for repetition in range(config["repetitions"]):
            for name in _scenario_order(names, repetition):
                time_samples[name].append(
                    _run(
                        state,
                        SCENARIOS[name],
                        depth=depth,
                        time_limit=config["time_limit"],
                        seed=seed,
                        table_capacity=config["table_capacity"],
                    )
                )
        result["fixed_time"]["positions"].append(
            {
                "position": label,
                "ruleset": state.ruleset,
                "scenarios": {
                    name: {
                        **_aggregate(time_samples[name]),
                        "values": [row["value"] for row in time_samples[name]],
                    }
                    for name in names
                },
            }
        )
        for name in names:
            time_rows[name].extend(time_samples[name])
    result["fixed_time"]["summary"] = {name: _aggregate(rows) for name, rows in time_rows.items()}
    return result
