"""Reproducible same-host latency, side-paired strength and evaluation benchmarks.

No tensors are imported unless training_evaluation is requested. Latency order
alternates to reduce warm-up/order bias. Strength uses seed pairs with swapped
roles and three opening lengths. Unfinished games always earn zero points.
"""

from __future__ import annotations

from collections import Counter
import hashlib
import json
import math
import platform
from pathlib import Path
import random
import statistics
import time
from typing import Callable

from .ai import choose_move
from .engine import RULESETS, State, apply_move, legal_moves, new_game
from .identities import effective_ai_config


DEFAULTS = {
    "seed": 50000,
    "games": {"brandubh": 128, "tablut": 64, "copenhagen": 64},
    "max_plies": 300,
    "time_limit": 1.0,
    "latency_repeats": 7,
    "self_games": 8,
    "jarl_games": 8,
    "jarl_depth": 2,
    "jarl_time_limit": 0.05,
    "training_evaluation": True,
    "evaluation_games": 8,
    "evaluation_repeats": 3,
    "evaluation_iterations": 4,
    "evaluation_max_plies": 200,
}


def distribution(values: list[float]) -> dict:
    ordered = sorted(values)
    index = (len(ordered) - 1) * 0.95
    low = math.floor(index)
    return {
        "count": len(values),
        "median_ms": statistics.median(values) * 1000,
        "p95_ms": (ordered[low] + (index - low) * (ordered[math.ceil(index)] - ordered[low]))
        * 1000,
        "mean_ms": statistics.mean(values) * 1000,
    }


def corpus(seed: int) -> list[tuple[str, State]]:
    positions = []
    for ruleset in RULESETS:
        for game in range(3):
            state = new_game(ruleset)
            last = state
            for ply in range(65):
                if state.winner:
                    break
                last = state
                if ply in (0, 8, 16, 32, 64):
                    positions.append((f"{ruleset}/game-{game}/ply-{ply}", state))
                move = choose_move(
                    state, {"algorithm": "baseline", "seed": seed + 1000 * game + ply}
                )
                state = apply_move(state, move)
            positions.append((f"{ruleset}/game-{game}/last", last))
        n = RULESETS[ruleset].size
        # Sparse, constrained endgames plus threats that opening play may miss.
        board = ["."] * (n * n)
        board[n + 2], board[n + 1], board[3 * n + 3], board[4 * n + 2] = "K", "A", "A", "D"
        for turn in ("attackers", "defenders"):
            positions.append((f"{ruleset}/sparse-{turn}", State(ruleset, tuple(board), turn)))
        board = ["."] * (n * n)
        board[2], board[n + 3], board[3 * n + 3], board[4 * n + 3] = "K", "A", "D", "A"
        for turn in ("attackers", "defenders"):
            positions.append(
                (f"{ruleset}/escape-threat-{turn}", State(ruleset, tuple(board), turn))
            )
        board = ["."] * (n * n)
        for cell, piece in (
            (n + 1, "K"),
            (1, "A"),
            (n, "A"),
            (n + 2, "A"),
            (2 * n + 1, "D"),
            (2 * n + 2, "D"),
            (4 * n + 3, "A"),
        ):
            board[cell] = piece
        for turn in ("attackers", "defenders"):
            positions.append(
                (f"{ruleset}/constrained-king-{turn}", State(ruleset, tuple(board), turn))
            )
    return positions


def summary(games: list[dict]) -> dict:
    count = len(games)
    wins = sum(g["winner"] == g["candidate_side"] for g in games)
    draws = sum(g["winner"] == "draw" for g in games)
    unfinished = sum(g["winner"] is None for g in games)
    return {
        "games": count,
        "wins": wins,
        "losses": count - wins - draws - unfinished,
        "draws": draws,
        "unfinished": unfinished,
        "score": (wins + draws / 2) / count if count else None,
        "average_plies": statistics.mean(g["plies"] for g in games) if count else None,
    }


def paired_interval(games: list[dict]) -> dict:
    """Seed-pair bootstrap, resampling the independent pairs, not individual roles."""
    values = [
        (float(g["winner"] == g["candidate_side"]) if g["winner"] != "draw" else 0.5) for g in games
    ]
    pairs = [statistics.mean(values[i : i + 2]) for i in range(0, len(values), 2)]
    rng = random.Random(2701)
    draws = sorted(statistics.mean(rng.choices(pairs, k=len(pairs))) for _ in range(5000))
    return {
        "method": "percentile_bootstrap_seed_pairs",
        "pairs": len(pairs),
        "confidence": 0.95,
        "lower": draws[125],
        "upper": draws[4874],
    }


def opening(ruleset: str, seed: int, plies: int) -> State:
    state = new_game(ruleset)
    rng = random.Random(seed)
    for _ in range(plies):
        choices = legal_moves(state)
        rng.shuffle(choices)
        # Opening perturbations are legal and nonterminal, shared by each pair.
        for move in choices:
            child = apply_move(state, move)
            if child.winner is None:
                state = child
                break
        else:
            break
    return state


def match(
    state: State,
    candidate: dict,
    opponent: dict,
    side: str,
    seed: int,
    max_plies: int,
    should_stop=None,
) -> dict:
    started = time.perf_counter()
    durations: dict[str, list[float]] = {"candidate": [], "opponent": []}
    moves = []
    initial_ply = state.ply
    for ply in range(max_plies):
        if should_stop and should_stop():
            raise InterruptedError("Heuristic benchmark cancelled")
        if state.winner:
            break
        is_candidate = state.turn == side
        config = candidate if is_candidate else opponent
        turn_started = time.perf_counter()
        move = choose_move(state, {**config, "seed": seed + ply}, should_stop=should_stop)
        durations["candidate" if is_candidate else "opponent"].append(
            time.perf_counter() - turn_started
        )
        moves.append(move.to_dict())
        state = apply_move(state, move)
    return {
        "ruleset": state.ruleset,
        "candidate_side": side,
        "seed": seed,
        "opening_plies": initial_ply,
        "winner": state.winner,
        "reason": state.reason or "benchmark_ply_limit",
        "plies": len(moves),
        "wall_seconds": time.perf_counter() - started,
        "candidate_decision_seconds": sum(durations["candidate"]),
        "opponent_decision_seconds": sum(durations["opponent"]),
        "moves_sha256": hashlib.sha256(json.dumps(moves, sort_keys=True).encode()).hexdigest(),
    }


def run_heuristic_benchmark(
    configuration: dict | None = None,
    *,
    should_stop: Callable[[], bool] | None = None,
    report: Callable[[dict], None] | None = None,
) -> dict:
    config = {**DEFAULTS, **(configuration or {})}
    if set(config) - set(DEFAULTS):
        raise ValueError("Unknown heuristic benchmark parameter")
    for count in [
        *config["games"].values(),
        config["self_games"],
        config["jarl_games"],
        config["evaluation_games"],
    ]:
        if type(count) is not int or count < 2 or count % 2:
            raise ValueError("Benchmark game counts must be positive even integers")
    if not 1 <= config["latency_repeats"] <= 100 or not 2 <= config["max_plies"] <= 5000:
        raise ValueError("Invalid benchmark repeat or ply budget")
    seed = config["seed"]
    profiles = {
        name: {"algorithm": name, "time_limit": config["time_limit"]}
        for name in ("baseline", "shield_maiden")
    }
    positions = corpus(seed + 100000)
    result: dict = {
        "config": config,
        "profiles": {name: effective_ai_config(profile) for name, profile in profiles.items()},
        "host": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "processor": platform.processor(),
            "device": "CPU",
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
                "shield_maiden.py",
                "training_evaluation.py",
                "heuristic_benchmark.py",
            )
        },
        "latency": {},
        "latency_by_ruleset": {},
        "strength": {},
        "complete_games": {},
        "jarl": {},
        "training_evaluation": {},
    }
    latency: dict[str, dict[str, list]] = {name: {"times": [], "moves": []} for name in profiles}
    for ruleset in config["games"]:
        rows = []
        ruleset_latency: dict[str, dict[str, list]] = {
            name: {"times": [], "moves": []} for name in profiles
        }
        for label, state in positions:
            if state.ruleset != ruleset:
                continue
            # Warm both paths before measuring.
            for profile in profiles.values():
                choose_move(state, profile, should_stop=should_stop)
            samples: dict[str, dict[str, list]] = {
                name: {"times": [], "moves": []} for name in profiles
            }
            for repeat in range(config["latency_repeats"]):
                for name in list(profiles) if repeat % 2 == 0 else list(reversed(profiles)):
                    details: dict = {}
                    started = time.perf_counter()
                    choose_move(
                        state,
                        {**profiles[name], "seed": seed},
                        diagnostics=details,
                        should_stop=should_stop,
                    )
                    elapsed = time.perf_counter() - started
                    samples[name]["times"].append(elapsed)
                    samples[name]["moves"].append(details["searched_moves"])
                    latency[name]["times"].append(elapsed)
                    latency[name]["moves"].append(details["searched_moves"])
                    ruleset_latency[name]["times"].append(elapsed)
                    ruleset_latency[name]["moves"].append(details["searched_moves"])
            rows.append(
                {
                    "position": label,
                    "legal_moves": len(legal_moves(state)),
                    "agents": {
                        name: {
                            **distribution(s["times"]),
                            "evaluated_moves": sum(s["moves"]),
                            "positions_per_second": sum(s["moves"]) / sum(s["times"]),
                        }
                        for name, s in samples.items()
                    },
                }
            )
        result["latency"][ruleset] = rows
        result["latency_by_ruleset"][ruleset] = {
            name: {
                **distribution(s["times"]),
                "evaluated_moves": sum(s["moves"]),
                "positions_per_second": sum(s["moves"]) / sum(s["times"]),
            }
            for name, s in ruleset_latency.items()
        }
        games = []
        for game in range(config["games"][ruleset]):
            pair = game // 2
            pair_seed = seed + pair * 1009
            state = opening(ruleset, pair_seed, (pair % 3) * 4)
            games.append(
                match(
                    state,
                    profiles["shield_maiden"],
                    profiles["baseline"],
                    "attackers" if game % 2 == 0 else "defenders",
                    pair_seed,
                    config["max_plies"],
                    should_stop,
                )
            )
            if report:
                report(
                    {"stage": "strength", "ruleset": ruleset, "games": len(games), **summary(games)}
                )
        result["strength"][ruleset] = {
            **summary(games),
            "as_attacker": summary(games[::2]),
            "as_defender": summary(games[1::2]),
            "score_interval": paired_interval(games),
            "records": games,
        }
        result["complete_games"][ruleset] = {}
        for name, profile in profiles.items():
            runs = [
                match(
                    new_game(ruleset),
                    profile,
                    profile,
                    "attackers",
                    seed + game * 1009,
                    config["max_plies"],
                    should_stop,
                )
                for game in range(config["self_games"])
            ]
            wall = sum(g["wall_seconds"] for g in runs)
            result["complete_games"][ruleset][name] = {
                **summary(runs),
                "wall_seconds": wall,
                "games_per_hour": len(runs) * 3600 / wall,
                "reasons": dict(Counter(g["reason"] for g in runs)),
                "records": runs,
            }
        jarl = {
            "algorithm": "alpha_beta",
            "depth": config["jarl_depth"],
            "time_limit": config["jarl_time_limit"],
        }
        sanity = [
            match(
                opening(ruleset, seed + 200000 + (g // 2) * 1009, ((g // 2) % 3) * 4),
                profiles["shield_maiden"],
                jarl,
                "attackers" if g % 2 == 0 else "defenders",
                seed + 200000 + (g // 2) * 1009,
                config["max_plies"],
                should_stop,
            )
            for g in range(config["jarl_games"])
        ]
        result["jarl"][ruleset] = {
            **summary(sanity),
            "opponent_config": jarl,
            "as_attacker": summary(sanity[::2]),
            "as_defender": summary(sanity[1::2]),
            "records": sanity,
        }
    result["latency_summary"] = {
        name: {
            **distribution(s["times"]),
            "evaluated_moves": sum(s["moves"]),
            "positions_per_second": sum(s["moves"]) / sum(s["times"]),
        }
        for name, s in latency.items()
    }
    result["latency_mean_ratio"] = (
        result["latency_summary"]["shield_maiden"]["mean_ms"]
        / result["latency_summary"]["baseline"]["mean_ms"]
    )
    if config["training_evaluation"]:
        import torch
        from .models import PolicyValueNet
        from .training import normalize_config
        from .training_evaluation import evaluate_model

        torch.set_num_threads(1)
        result["training_environment"] = {
            "torch": torch.__version__,
            "cpu_threads": 1,
            "model": {"channels": 4, "blocks": 0, "initialization_seed": seed},
            "training_updates": 0,
        }
        for ruleset in config["games"]:
            torch.manual_seed(seed)
            model = PolicyValueNet(ruleset, channels=4, blocks=0).eval()
            measurements = []
            # Warm inference before either timed evaluation; alternate order.
            from .ai import policy_value

            policy_value(new_game(ruleset), model=model)
            for repeat in range(config["evaluation_repeats"]):
                evaluation_runs = {}
                opponents = (
                    ("shield_bearer", "shield_maiden")
                    if repeat % 2 == 0
                    else ("shield_maiden", "shield_bearer")
                )
                for opponent in opponents:
                    cfg = normalize_config(
                        {
                            "preset": "smoke",
                            "ruleset": ruleset,
                            "seed": seed,
                            "evaluation_opponent": opponent,
                            "evaluation_games": config["evaluation_games"],
                            "evaluation_iterations": config["evaluation_iterations"],
                            "evaluation_time_limit": config["time_limit"],
                            "evaluation_max_plies": config["evaluation_max_plies"],
                        }
                    )
                    evaluation_runs[opponent] = evaluate_model(model, cfg, should_stop=should_stop)
                measurements.append(evaluation_runs)
            means = {
                opponent: statistics.mean(m[opponent]["duration"] for m in measurements)
                for opponent in ("shield_bearer", "shield_maiden")
            }
            result["training_evaluation"][ruleset] = {
                "runs": measurements[0],
                "measurements": measurements,
                "mean_seconds": means,
                "wall_ratio": means["shield_maiden"] / means["shield_bearer"],
            }
    return result
