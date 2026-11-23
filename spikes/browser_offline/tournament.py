"""Role-balanced paired-opening tournament harness and strict report validator.

The runner is agent-agnostic: native Jarl/neural agents, a browser agent adapter,
or deterministic test agents implement ``choose(state, settings, seed) -> Move``.
Rules, repetition history, legal-move validation and terminal outcomes always use
the shared Python engine. Experimental results are non-authoritative.
"""

from __future__ import annotations

import argparse
import json
import random
import signal
import sys
import threading
from pathlib import Path
from typing import Any, Callable

SPIKE = Path(__file__).resolve().parent
ROOT = SPIKE.parents[1]
for path in (str(SPIKE), str(ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

from hnefatafl.engine import Move, State, apply_move, legal_moves  # noqa: E402

SCHEMA = 1
AGENT = Callable[[State, dict, int], Move]
SIDES = ("attackers", "defenders")
DEFAULT_AGENT_TIMEOUT = 30.0


class AgentTimeoutError(TimeoutError):
    """Raised when an agent exceeds its hard per-move execution deadline."""


class _deadline:
    """Hard per-call deadline using SIGALRM in the main process.

    Browser/process adapters must additionally enforce their own subprocess
    timeout; this guard catches in-process blocking agents on Unix platforms.
    """

    def __init__(self, seconds: float):
        if not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or seconds <= 0:
            raise ValueError("agent timeout must be positive")
        self.seconds = float(seconds)
        self.previous = None
        self.enabled = False

    def __enter__(self):
        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError("hard agent deadlines require the main thread")
        if not hasattr(signal, "setitimer") or not hasattr(signal, "SIGALRM"):
            raise RuntimeError("hard agent deadlines require SIGALRM support")
        self.previous = signal.getsignal(signal.SIGALRM)
        signal.signal(signal.SIGALRM, self._alarm)
        signal.setitimer(signal.ITIMER_REAL, self.seconds)
        self.enabled = True
        return self

    def _alarm(self, _signum, _frame):
        raise AgentTimeoutError(f"agent exceeded {self.seconds:g}s deadline")

    def __exit__(self, exc_type, exc, tb):
        if self.enabled:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, self.previous)
        return False


def _move(value: Any) -> Move:
    if isinstance(value, Move):
        return value
    if isinstance(value, dict):
        return Move(value["source"], value["target"])
    raise ValueError("agent returned a malformed move")


def play_game(
    attacker: AGENT,
    defender: AGENT,
    opening: dict,
    *,
    ruleset: str,
    simulations: int,
    time_limit: float,
    seed: int,
    max_plies: int = 200,
    agent_timeout: float = DEFAULT_AGENT_TIMEOUT,
) -> dict:
    state = State.from_dict(opening["state"])
    if state.ruleset != ruleset:
        raise ValueError(f"opening ruleset {state.ruleset!r} does not match {ruleset!r}")
    if state.winner is not None:
        raise ValueError("tournament openings must be non-terminal")
    settings = {
        "algorithm": "mcts",
        "iterations": simulations,
        "time_limit": time_limit,
        "exploration": 1.4,
        "seed": seed,
    }
    game = {
        "opening": opening["name"],
        "opening_state": opening["state"],
        "ruleset": ruleset,
        "attacker_agent": None,
        "defender_agent": None,
        "winner": None,
        "reason": None,
        "plies": 0,
        "moves": [],
        "completed": False,
        "error": None,
        "seed": seed,
        "settings": dict(settings),
        "agent_timeout_s": float(agent_timeout),
    }
    try:
        while state.winner is None and state.ply < max_plies:
            agent = attacker if state.turn == "attackers" else defender
            with _deadline(agent_timeout):
                move = _move(agent(state, settings, seed + state.ply))
            if move not in legal_moves(state):
                raise ValueError(f"agent selected an illegal move {move.to_dict()}")
            state = apply_move(state, move)
            game["moves"].append(move.to_dict())
        game["plies"] = state.ply
        game["winner"] = state.winner
        game["reason"] = state.reason or ("move_limit" if state.winner is None else None)
        game["completed"] = state.winner is not None
    except Exception as error:  # noqa: BLE001 - one failed game must not abort the report
        game["error"] = f"{type(error).__name__}: {error}"
        game["plies"] = state.ply
        game["winner"] = None
        game["completed"] = False
        game["reason"] = "execution_error"
    return game


def run_paired_openings(
    agent_a: AGENT,
    agent_b: AGENT,
    openings: list[dict],
    *,
    labels: tuple[str, str],
    ruleset: str,
    simulations: int,
    time_limit: float,
    seed: int = 7,
    max_plies: int = 200,
    agent_timeout: float = DEFAULT_AGENT_TIMEOUT,
) -> dict:
    games = []
    for pair, opening in enumerate(openings):
        for swap in (False, True):
            a_side = "defenders" if swap else "attackers"
            attacker, defender = (agent_b, agent_a) if swap else (agent_a, agent_b)
            game = play_game(
                attacker,
                defender,
                opening,
                ruleset=ruleset,
                simulations=simulations,
                time_limit=time_limit,
                seed=seed + pair * 100 + int(swap),
                max_plies=max_plies,
                agent_timeout=agent_timeout,
            )
            game.update(
                {
                    "game_id": f"pair-{pair}-{'ba' if swap else 'ab'}",
                    "pair": pair,
                    "agent_a": labels[0],
                    "agent_b": labels[1],
                    "agent_a_side": a_side,
                    "agent_b_side": "attackers" if a_side == "defenders" else "defenders",
                    "attacker_agent": labels[1] if swap else labels[0],
                    "defender_agent": labels[0] if swap else labels[1],
                }
            )
            games.append(game)
    report = {
        "kind": "browser-analysis-tournament",
        "schema": SCHEMA,
        "ruleset": ruleset,
        "agents": {"a": labels[0], "b": labels[1]},
        "settings": {
            "simulations": simulations,
            "time_limit": time_limit,
            "seed": seed,
            "max_plies": max_plies,
            "agent_timeout_s": float(agent_timeout),
        },
        "openings": [entry["name"] for entry in openings],
        "games": games,
        "summary": summarize(games, labels, seed=seed),
        "authoritative": False,
    }
    report["ok"] = not validate_report(report)["failures"]
    return report


def _agent_score(game: dict, agent: str, side: str) -> float | None:
    if not game.get("completed") or game.get("winner") is None:
        return None
    if game["winner"] == "draw":
        return 0.5
    return 1.0 if game["winner"] == side else 0.0


def summarize(games: list[dict], labels: tuple[str, str], *, seed: int = 7) -> dict:
    a, b = labels
    result = {
        "games": len(games),
        "completed": 0,
        "draws": 0,
        "truncated": 0,
        "execution_errors": 0,
        "attacker_wins": 0,
        "defender_wins": 0,
        "by_agent": {
            a: {"wins": 0, "losses": 0, "draws": 0, "score": 0.0},
            b: {"wins": 0, "losses": 0, "draws": 0, "score": 0.0},
        },
        "by_role": {side: {"wins": 0, "losses": 0, "draws": 0} for side in SIDES},
        "average_plies": None,
        "paired_score": None,
        "paired_score_ci95": None,
        "pairs": 0,
    }
    pair_scores: dict[int, dict[str, list[float]]] = {}
    plies = []
    for game in games:
        result["execution_errors"] += int(bool(game.get("error")))
        if not game.get("completed"):
            if not game.get("error"):
                result["truncated"] += 1
            continue
        result["completed"] += 1
        plies.append(game["plies"])
        if game["winner"] == "draw":
            result["draws"] += 1
        elif game["winner"] == "attackers":
            result["attacker_wins"] += 1
        elif game["winner"] == "defenders":
            result["defender_wins"] += 1
        side = game["agent_a_side"]
        score_a = _agent_score(game, a, side)
        if score_a is None:
            continue
        score_b = 1.0 - score_a
        for agent, score, agent_side in (
            (a, score_a, side),
            (b, score_b, game["agent_b_side"]),
        ):
            if score == 0.5:
                result["by_agent"][agent]["draws"] += 1
                result["by_role"][agent_side]["draws"] += 1
            elif score == 1.0:
                result["by_agent"][agent]["wins"] += 1
                result["by_role"][agent_side]["wins"] += 1
            else:
                result["by_agent"][agent]["losses"] += 1
                result["by_role"][agent_side]["losses"] += 1
            result["by_agent"][agent]["score"] += score
        pair_scores.setdefault(game["pair"], {"a": [], "b": []})
        pair_scores[game["pair"]]["a"].append(score_a)
        pair_scores[game["pair"]]["b"].append(score_b)
    if plies:
        result["average_plies"] = sum(plies) / len(plies)
    paired = [
        sum(scores["a"]) / len(scores["a"])
        for scores in pair_scores.values()
        if len(scores["a"]) >= 2
    ]
    result["pairs"] = len(paired)
    if paired:
        result["paired_score"] = sum(paired) / len(paired)
        rng = random.Random(seed)
        bootstrap = []
        for _ in range(2000):
            sample = [paired[rng.randrange(len(paired))] for _ in paired]
            bootstrap.append(sum(sample) / len(sample))
        bootstrap.sort()
        result["paired_score_ci95"] = [
            bootstrap[int(0.025 * (len(bootstrap) - 1))],
            bootstrap[int(0.975 * (len(bootstrap) - 1))],
        ]
    return result


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _replay(opening: State, moves: list[dict]) -> State:
    state = opening
    for raw in moves:
        move = _move(raw)
        if move not in legal_moves(state):
            raise ValueError(f"illegal move {move.to_dict()} in replay")
        state = apply_move(state, move)
    return state


def validate_report(report: dict) -> dict:
    failures: list[str] = []

    def fail(message: str) -> None:
        failures.append(message)

    if not isinstance(report, dict):
        return {"ok": False, "failures": ["report must be an object"]}
    if report.get("kind") != "browser-analysis-tournament" or report.get("schema") != SCHEMA:
        fail("unsupported tournament report schema")
    openings = report.get("openings")
    games = report.get("games")
    if not isinstance(openings, list) or not openings:
        fail("openings must be a non-empty list")
        openings = []
    if not isinstance(games, list):
        fail("games must be a list")
        games = []
    # Opening states are recorded in every game snapshot for replay validation.
    opening_states: dict[str, dict] = {}
    for game in games:
        if not isinstance(game, dict):
            continue
        name = game.get("opening")
        raw = game.get("opening_state")
        if not isinstance(raw, dict):
            fail(f"{game.get('game_id')}: opening_state must be an object")
            continue
        try:
            current = State.from_dict(raw).to_dict()
        except (ValueError, KeyError, TypeError) as error:
            fail(f"{game.get('game_id')}: invalid opening_state: {error}")
            continue
        if name in opening_states and opening_states[name] != current:
            fail(f"{game.get('game_id')}: paired opening state/history differs")
        opening_states[name] = current
    for game in games:
        if not isinstance(game, dict):
            fail("game entry must be an object")
            continue
        opening_name = game.get("opening")
        if opening_name not in openings:
            fail(f"{game.get('game_id')}: unknown opening {opening_name!r}")
        if game.get("ruleset") != report.get("ruleset"):
            fail(f"{game.get('game_id')}: ruleset mismatch")
        if game.get("agent_a") != (report.get("agents") or {}).get("a"):
            fail(f"{game.get('game_id')}: agent A identity mismatch")
        if game.get("agent_b") != (report.get("agents") or {}).get("b"):
            fail(f"{game.get('game_id')}: agent B identity mismatch")
        if {game.get("agent_a_side"), game.get("agent_b_side")} != set(SIDES):
            fail(f"{game.get('game_id')}: role assignment is invalid")
        if game.get("agent_a_side") == "attackers" and game.get("attacker_agent") != game.get(
            "agent_a"
        ):
            fail(f"{game.get('game_id')}: attacker assignment contradicts roles")
        if game.get("agent_a_side") == "defenders" and game.get("defender_agent") != game.get(
            "agent_a"
        ):
            fail(f"{game.get('game_id')}: defender assignment contradicts roles")
        if not isinstance(game.get("moves"), list):
            fail(f"{game.get('game_id')}: moves must be a list")
            continue
        game_id = game.get("game_id")
        if isinstance(game_id, str):
            parts = game_id.split("-")
            if (
                len(parts) != 3
                or parts[0] != "pair"
                or not parts[1].isdigit()
                or parts[2] not in ("ab", "ba")
            ):
                fail(f"{game_id}: malformed game identifier")
            elif game.get("pair") != int(parts[1]):
                fail(f"{game_id}: pair index contradicts identifier")
            elif parts[2] == "ba" and game.get("agent_a_side") != "defenders":
                fail(f"{game_id}: swapped leg role assignment is inconsistent")
            elif parts[2] == "ab" and game.get("agent_a_side") != "attackers":
                fail(f"{game_id}: original leg role assignment is inconsistent")
        expected_move_count = len(game["moves"])
        if game.get("plies") != expected_move_count:
            fail(f"{game.get('game_id')}: ply count does not match move count")
        if game.get("error") is not None:
            if game.get("completed") or game.get("winner") is not None:
                fail(f"{game.get('game_id')}: error game claims completion")
            continue
        try:
            state = _replay(State.from_dict(game["opening_state"]), game["moves"])
        except (ValueError, KeyError, TypeError) as error:
            fail(f"{game.get('game_id')}: {error}")
        else:
            if (state.winner is not None) != bool(game.get("completed")):
                fail(f"{game.get('game_id')}: completion state does not match replay")
            if state.winner != game.get("winner"):
                fail(f"{game.get('game_id')}: winner does not match replay")
            if state.ply != game.get("plies"):
                fail(f"{game.get('game_id')}: ply count does not match replay")
            if state.winner is not None and game.get("reason") != (state.reason or "terminal"):
                fail(f"{game.get('game_id')}: terminal reason does not match replay")
        if game.get("completed") and game.get("winner") not in ("attackers", "defenders", "draw"):
            fail(f"{game.get('game_id')}: completed game has an invalid winner")
    ids = [game.get("game_id") for game in games if isinstance(game, dict)]
    if len(set(ids)) != len(ids):
        fail("duplicate game identifiers")
    expected_pairs = {
        f"pair-{index}-{leg}" for index in range(len(openings)) for leg in ("ab", "ba")
    }
    if set(ids) != expected_pairs:
        fail("game identifiers do not cover every opening pair and role swap exactly once")
    settings = report.get("settings") or {}
    timeout = settings.get("agent_timeout_s")
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
        fail("settings.agent_timeout_s must be positive")
    summary = report.get("summary") or {}
    if summary.get("games") != len(games):
        fail("summary game count does not match games")
    completed = sum(1 for game in games if isinstance(game, dict) and game.get("completed"))
    if summary.get("completed") != completed:
        fail("summary completed count does not match games")
    labels = (
        (report.get("agents") or {}).get("a"),
        (report.get("agents") or {}).get("b"),
    )
    if all(isinstance(label, str) for label in labels):
        recomputed = summarize(games, labels, seed=(report.get("settings") or {}).get("seed", 7))
        if _canonical(recomputed) != _canonical(summary):
            fail("summary statistics do not match independently recomputed results")
    if report.get("structural_only") is True:
        if (report.get("playing_strength") or {}).get("status") != "blocked":
            fail("structural tournament evidence must mark playing strength blocked")
    result = {
        "kind": "browser-analysis-tournament-validation",
        "ok": not failures,
        "failures": failures,
        "games": len(games),
        "pairs": len(openings),
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = json.loads(args.report.read_text())
    validated = validate_report(report)
    if args.output:
        args.output.write_text(json.dumps(validated, indent=2) + "\n")
    print(json.dumps(validated, indent=2))
    sys.exit(0 if validated["ok"] else 1)


if __name__ == "__main__":
    main()
