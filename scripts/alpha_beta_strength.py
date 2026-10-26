#!/usr/bin/env python3
"""Equal-time strength comparison for two alpha-beta search configurations.

Reads the browser tournament-import format (for example
``configs/alpha-beta-search-comparison.json``) and plays a balanced series,
alternating sides. Both opponents use their configured time budget; the report
records wins, draws, losses, the score with a normal-approximation 95% interval
and the mean completed search depth per opponent.

Usage::

    .venv/bin/python scripts/alpha_beta_strength.py \
        --config configs/alpha-beta-search-comparison.json \
        --output work/alpha-beta-strength.json
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hnefatafl.ai import choose_move  # noqa: E402
from hnefatafl.engine import apply_move, new_game  # noqa: E402


def _normal_interval(scores: list[float]) -> dict:
    count = len(scores)
    if count == 0:
        return {"low": 0.0, "high": 0.0, "standard_error": 0.0}
    mean = statistics.fmean(scores)
    if count == 1:
        return {"low": mean, "high": mean, "standard_error": 0.0}
    standard_error = statistics.stdev(scores) / math.sqrt(count)
    return {
        "low": mean - 1.96 * standard_error,
        "high": mean + 1.96 * standard_error,
        "standard_error": standard_error,
    }


def _play(ruleset: str, attacker: dict, defender: dict, *, seed: int, max_plies: int) -> dict:
    state = new_game(ruleset)
    depths = {"attackers": [], "defenders": []}
    started = time.perf_counter()
    ply = 0
    while not state.winner and ply < max_plies:
        config = dict(attacker if state.turn == "attackers" else defender)
        config["seed"] = seed + ply
        diagnostics: dict = {}
        move = choose_move(state, config, diagnostics=diagnostics)
        depth = diagnostics.get("completed_depth")
        if depth is not None:
            depths[state.turn].append(depth)
        state = apply_move(state, move)
        ply += 1
    return {
        "winner": state.winner,
        "reason": state.reason or "experiment_ply_limit",
        "plies": ply,
        "seconds": time.perf_counter() - started,
        "depths": depths,
    }


def run(configuration: dict, *, games: int | None = None) -> dict:
    players = [dict(player["config"]) for player in configuration["players"]]
    if len(players) != 2:
        raise ValueError("The strength comparison expects exactly two opponents")
    ruleset = configuration["rulesets"][0]
    game_count = int(configuration.get("games", 2) if games is None else games)
    if game_count % 2:
        raise ValueError("Use an even game count to balance sides")
    seed = int(configuration.get("seed", 0))
    max_plies = int(configuration.get("max_plies", 300))
    records: list[dict] = []
    scores = [0.0, 0.0]
    wins = [0, 0]
    draws = 0
    unfinished = 0
    all_depths = [[], []]
    started = time.perf_counter()
    for game in range(game_count):
        first, second = (0, 1) if game % 2 == 0 else (1, 0)
        result = _play(
            ruleset,
            players[first],
            players[second],
            seed=seed + game,
            max_plies=max_plies,
        )
        winner = result["winner"]
        if winner == "draw":
            draws += 1
            scores[0] += 0.5
            scores[1] += 0.5
        elif winner is None:
            unfinished += 1
        else:
            winner_index = first if winner == "attackers" else second
            wins[winner_index] += 1
            scores[winner_index] += 1
        all_depths[0].extend(
            result["depths"]["attackers"] if first == 0 else result["depths"]["defenders"]
        )
        all_depths[1].extend(
            result["depths"]["defenders"] if first == 0 else result["depths"]["attackers"]
        )
        records.append(
            {
                "game": game,
                "attacker": first,
                "defender": second,
                "winner": winner,
                "reason": result["reason"],
                "plies": result["plies"],
                "seconds": result["seconds"],
            }
        )
    completed = game_count - unfinished
    player0_scores: list[float] = []
    completed_totals = [0.0, 0.0]
    for record in records:
        if record["winner"] is None:
            continue
        if record["winner"] == "draw":
            player0_scores.append(0.5)
            completed_totals[0] += 0.5
            completed_totals[1] += 0.5
        elif _won(record, 0):
            player0_scores.append(1.0)
            completed_totals[0] += 1
        else:
            player0_scores.append(0.0)
            completed_totals[1] += 1
    completed_score = [value / completed for value in completed_totals] if completed else [0.0, 0.0]
    return {
        "config": configuration.get("name", "alpha-beta comparison"),
        "ruleset": ruleset,
        "games": game_count,
        "seed": seed,
        "max_plies": max_plies,
        "time_limit": players[0].get("time_limit"),
        "depth_cap": players[0].get("depth"),
        "completed": completed,
        "unfinished": unfinished,
        "wins": wins,
        "draws": draws,
        "losses": [wins[1], wins[0]],
        "score_all_games": [
            scores[0] / game_count if game_count else 0.0,
            scores[1] / game_count if game_count else 0.0,
        ],
        "score_completed": completed_score,
        "completed_interval_player0": _normal_interval(player0_scores),
        "mean_depth": [statistics.fmean(depths) if depths else 0.0 for depths in all_depths],
        "total_seconds": time.perf_counter() - started,
        "games_detail": records,
    }


def _won(record: dict, player: int) -> bool:
    if record["winner"] == "attackers":
        return record["attacker"] == player
    return record["defender"] == player


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / "configs" / "alpha-beta-search-comparison.json",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--games", type=int, help="Override the configured game count")
    arguments = parser.parse_args(argv)
    configuration = json.loads(arguments.config.read_text(encoding="utf-8"))
    report = run(configuration, games=arguments.games)
    text = json.dumps(report, indent=2, sort_keys=True)
    if arguments.output:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(text + "\n", encoding="utf-8")
    interval = report["completed_interval_player0"]
    print(
        f"{report['config']}: {report['games']} games on {report['ruleset']} "
        f"(time_limit={report['time_limit']}s, depth_cap={report['depth_cap']})"
    )
    print(
        f"  player 0: {report['wins'][0]}W {report['draws']}D {report['losses'][0]}L "
        f"score {report['score_completed'][0]:.3f} "
        f"[{interval['low']:.3f}, {interval['high']:.3f}] "
        f"mean depth {report['mean_depth'][0]:.2f}"
    )
    print(
        f"  player 1: {report['wins'][1]}W {report['draws']}D {report['losses'][1]}L "
        f"score {report['score_completed'][1]:.3f} "
        f"mean depth {report['mean_depth'][1]:.2f}"
    )
    print(f"  unfinished {report['unfinished']}, total {report['total_seconds']:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
