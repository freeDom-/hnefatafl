"""Bounded, reproducible computer players independent of HTTP and persistence.

All evaluations use the defender's perspective. Search never mutates a state.
The neural evaluator is optional: ordinary play does not import PyTorch.
"""

from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, field
from functools import lru_cache
from itertools import combinations
from typing import Any, Callable

from .engine import Move, State, apply_move, legal_moves, new_game


def ai_profiles() -> list[dict[str, Any]]:
    common = ["tablut", "brandubh", "copenhagen"]
    return [
        {
            "id": "baseline",
            "name": "Shield bearer",
            "algorithm": "baseline",
            "difficulty": "Beginner",
            "parameters": {"time_limit": 1.0},
            "supported_rulesets": common,
        },
        {
            "id": "alpha_beta",
            "name": "Jarl",
            "algorithm": "alpha_beta",
            "difficulty": "Configurable",
            "parameters": {"depth": 2, "time_limit": 2.0},
            "supported_rulesets": common,
        },
        {
            "id": "mcts",
            "name": "Seer",
            "algorithm": "mcts",
            "difficulty": "Configurable",
            "parameters": {
                "iterations": 80,
                "time_limit": 2.0,
                "exploration": 1.4,
                "rollout_depth": 8,
            },
            "supported_rulesets": common,
        },
        {
            "id": "neural",
            "name": "Trained model",
            "algorithm": "neural",
            "difficulty": "Model dependent",
            "parameters": {"iterations": 40, "time_limit": 2.0},
            "supported_rulesets": common,
        },
    ]


def evaluate(state: State) -> float:
    """Rules-aware static score in [-1, 1], positive for defenders."""
    if state.winner:
        return {"defenders": 1.0, "attackers": -1.0, "draw": 0.0}[state.winner]
    n = state.size
    try:
        king = state.board.index("K")
    except ValueError:
        return -1.0
    row, col = divmod(king, n)
    attackers = state.board.count("A")
    defenders = state.board.count("D")
    initial_attackers = {"brandubh": 8, "tablut": 16, "copenhagen": 24}[state.ruleset]
    # Escaping to an edge is valuable in Tablut; the other variants use corners.
    distance = (
        min(row, col, n - 1 - row, n - 1 - col)
        if state.ruleset == "tablut"
        else min(row + col, row + n - 1 - col, n - 1 - row + col, 2 * (n - 1) - row - col)
    )
    escape = 1.0 - distance / (n - 1)
    adjacent_attackers = 0
    free_rays = 0
    for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        rr, cc = row + dr, col + dc
        if 0 <= rr < n and 0 <= cc < n and state.board[rr * n + cc] == "A":
            adjacent_attackers += 1
        ray_clear = True
        while 0 <= rr < n and 0 <= cc < n:
            if state.board[rr * n + cc] != ".":
                ray_clear = False
                break
            rr += dr
            cc += dc
        free_rays += int(ray_clear)
    material = (defenders * 2 - attackers) / initial_attackers
    return math.tanh(
        0.9 * material + 0.9 * escape - 0.45 * adjacent_attackers + 0.12 * free_rays - 0.45
    )


@lru_cache(maxsize=8)
def _loaded_model(path: str):
    from .models import load_checkpoint

    return load_checkpoint(path, device="cpu")[0]


def policy_value(
    state: State, model_path: str | None = None, model=None
) -> tuple[dict[Move, float], float]:
    """Return legal priors and a defender-perspective value."""
    moves = legal_moves(state)
    if not moves:
        return {}, evaluate(state)
    if model_path is None and model is None:
        return {move: 1 / len(moves) for move in moves}, evaluate(state)
    import torch
    from .models import encode_state

    model = model if model is not None else _loaded_model(str(model_path))
    if model.ruleset != state.ruleset:
        raise ValueError("Model ruleset does not match this game")
    with torch.inference_mode():
        logits, value = model(encode_state(state).unsqueeze(0).to(next(model.parameters()).device))
        action_ids = [m.source * (state.size**2) + m.target for m in moves]
        probabilities = torch.softmax(logits[0, action_ids], dim=0).tolist()
    return dict(zip(moves, probabilities)), float(value.reshape(-1)[0])


def _ordered(state: State, moves: list[Move], rng: random.Random) -> list[tuple[Move, State]]:
    # Terminal results first; material and king progression next. Stable seeded ties.
    rng.shuffle(moves)
    sign = 1 if state.turn == "defenders" else -1
    children = [(move, apply_move(state, move)) for move in moves]
    children.sort(key=lambda pair: sign * evaluate(pair[1]), reverse=True)
    return children


class _Deadline(Exception):
    pass


def _alpha_beta(
    state: State,
    config: dict,
    rng: random.Random,
    deadline: float,
    *,
    diagnostics: dict | None = None,
) -> Move:
    depth = int(config.get("depth", 2))
    if not 1 <= depth <= 8:
        raise ValueError("Search depth must be between 1 and 8")
    root_moves = legal_moves(state)
    best_move = rng.choice(root_moves)
    cache: dict[tuple, float] = {}
    model_path = config.get("model_path")
    model = config.get("_model")
    if diagnostics is not None:
        diagnostics.update(
            value=policy_value(state, model_path, model)[1]
            if model_path or model is not None
            else evaluate(state),
            completed_depth=0,
        )

    def search(position: State, remaining: int, alpha: float, beta: float) -> float:
        if time.monotonic() >= deadline:
            raise _Deadline
        if position.winner:
            return evaluate(position)
        if remaining == 0:
            return (
                policy_value(position, model_path, model)[1]
                if model_path or model is not None
                else evaluate(position)
            )
        # Include repetition context: identical boards can have different outcomes.
        key = (position.board, position.turn, position.history, remaining)
        if key in cache:
            return cache[key]
        moves = legal_moves(position)
        if not moves:
            return -1.0 if position.turn == "defenders" else 1.0
        maximizing = position.turn == "defenders"
        value = -math.inf if maximizing else math.inf
        fully_searched = True
        for _, child in _ordered(position, moves, rng):
            score = search(child, remaining - 1, alpha, beta)
            value = max(value, score) if maximizing else min(value, score)
            if maximizing:
                alpha = max(alpha, value)
            else:
                beta = min(beta, value)
            if beta <= alpha:
                fully_searched = False
                break
        if fully_searched:
            cache[key] = value
        return value

    # Iterative deepening retains the last completed result on deadline expiry.
    sign = 1 if state.turn == "defenders" else -1
    for current_depth in range(1, depth + 1):
        iteration_best = best_move
        score_best = -math.inf
        try:
            ordered = _ordered(state, list(root_moves), rng)
            for move, child in ordered:
                score = sign * search(child, current_depth - 1, -math.inf, math.inf)
                if score > score_best:
                    score_best, iteration_best = score, move
        except _Deadline:
            break
        best_move = iteration_best
        if diagnostics is not None:
            diagnostics.update(value=sign * score_best, completed_depth=current_depth)
    return best_move


@dataclass
class _Node:
    state: State
    prior: float = 1.0
    visits: int = 0
    total: float = 0.0
    children: dict[Move, "_Node"] = field(default_factory=dict)


def mcts_policy(
    state: State, config: dict | None = None, *, diagnostics: dict | None = None
) -> dict[Move, float]:
    """PUCT/UCT tree search returning normalized root visit counts.

    Network priors and value replace rollouts when a model path is supplied.
    The stored value always has defender perspective, including at opponent nodes.
    """
    config = config or {}
    budget = int(config.get("iterations", 80))
    if not 1 <= budget <= 100_000:
        raise ValueError("MCTS iterations must be between 1 and 100000")
    exploration = float(config.get("exploration", 1.4))
    if not 0 <= exploration <= 20:
        raise ValueError("Exploration must be between 0 and 20")
    rollout_depth = int(config.get("rollout_depth", 8))
    if not 0 <= rollout_depth <= 128:
        raise ValueError("Rollout depth must be between 0 and 128")
    seconds = float(config.get("time_limit", 2.0))
    if not math.isfinite(seconds) or seconds <= 0 or seconds > 300:
        raise ValueError("Time limit must be positive and at most 300 seconds")
    rng = random.Random(config.get("seed", 0))
    model_path = config.get("model_path")
    model = config.get("_model")
    deadline = time.monotonic() + seconds
    root = _Node(state)
    root_priors, root_value = policy_value(state, model_path, model)
    if diagnostics is not None:
        diagnostics.update(value=root_value, root_value=root_value, simulations=0)
    # Never overlook a one-move win even with a tiny simulation budget.
    for move in root_priors:
        if apply_move(state, move).winner == state.turn:
            if diagnostics is not None:
                diagnostics.update(value=1.0 if state.turn == "defenders" else -1.0)
            return {move: 1.0}
    noise_fraction = float(config.get("dirichlet_fraction", 0.0))
    if not 0 <= noise_fraction <= 1:
        raise ValueError("Dirichlet fraction must be between zero and one")
    if noise_fraction and root_priors:
        alpha = float(config.get("dirichlet_alpha", 0.3))
        if alpha <= 0:
            raise ValueError("Dirichlet alpha must be positive")
        noise = [rng.gammavariate(alpha, 1) for _ in root_priors]
        total_noise = sum(noise)
        root_priors = {
            move: (1 - noise_fraction) * prior + noise_fraction * value / total_noise
            for (move, prior), value in zip(root_priors.items(), noise)
        }
    if not root_priors:
        raise ValueError("No legal moves in terminal position")
    # Lazily create states. Expanding hundreds of moves is otherwise expensive.
    unexpanded: dict[int, dict[Move, float]] = {id(root): root_priors.copy()}
    for _ in range(budget):
        if time.monotonic() >= deadline and root.visits:
            break
        node = root
        path = [node]
        while not node.state.winner:
            remaining = unexpanded.get(id(node))
            if remaining is None:
                priors, _ = policy_value(node.state, model_path, model)
                remaining = dict(priors)
                unexpanded[id(node)] = remaining
            # Progressive widening explores deeply even on an opening board
            # with hundreds of legal moves and a modest simulation budget.
            if remaining and len(node.children) < math.ceil(math.sqrt(node.visits + 1)):
                choices = list(remaining)
                # Sample priors, giving every legal move an opportunity.
                weights = [remaining[m] for m in choices]
                move = rng.choices(choices, weights=weights, k=1)[0]
                prior = remaining.pop(move)
                child = _Node(apply_move(node.state, move), prior=prior)
                node.children[move] = child
                node = child
                path.append(node)
                break
            if not node.children:
                break
            sign = 1 if node.state.turn == "defenders" else -1
            node = max(
                node.children.values(),
                key=lambda child: (
                    sign * child.total / max(1, child.visits)
                    + exploration * child.prior * math.sqrt(node.visits + 1) / (1 + child.visits)
                ),
            )
            path.append(node)
        if node.state.winner:
            value = evaluate(node.state)
        elif model_path or model is not None:
            _, value = policy_value(node.state, model_path, model)
        else:
            rollout = node.state
            for _ in range(rollout_depth):
                if time.monotonic() >= deadline or rollout.winner:
                    break
                moves = legal_moves(rollout)
                if not moves:
                    break
                rollout = apply_move(rollout, rng.choice(moves))
            value = evaluate(rollout)
        for ancestor in path:
            ancestor.visits += 1
            ancestor.total += value
    total = sum(child.visits for child in root.children.values())
    if diagnostics is not None:
        diagnostics.update(
            value=root.total / root.visits if root.visits else root_value,
            simulations=root.visits,
        )
    if not total:
        return root_priors
    return {move: child.visits / total for move, child in root.children.items()}


def _profile_configuration(config: dict | None) -> dict:
    """Apply a selected profile's defaults without replacing explicit settings.

    HTTP sessions already retain resolved configurations. Direct API, CLI and
    experiment callers can instead select only a profile; they must search with
    the same parameters as a game created through the browser.
    """
    configuration = dict(config or {})
    if "algorithm" not in configuration and configuration.get("profile_id"):
        profile = next(
            (profile for profile in ai_profiles() if profile["id"] == configuration["profile_id"]),
            None,
        )
        if profile is None:
            raise ValueError(f"Unknown AI profile: {configuration['profile_id']}")
        configuration = {
            **profile["parameters"],
            **configuration,
            "algorithm": profile["algorithm"],
        }
    return configuration


def choose_move(
    state: State, config: dict | None = None, *, diagnostics: dict | None = None
) -> Move:
    config = _profile_configuration(config)
    moves = legal_moves(state)
    if state.winner or not moves:
        raise ValueError("Cannot choose a move in a terminal position")
    algorithm = config.get("algorithm", config.get("profile_id", "baseline"))
    rng = random.Random(config.get("seed", 0))
    seconds = float(config.get("time_limit", 2.0))
    if not math.isfinite(seconds) or not 0 < seconds <= 300:
        raise ValueError("Time limit must be positive and at most 300 seconds")
    deadline = time.monotonic() + seconds
    if algorithm == "baseline":
        sign = 1 if state.turn == "defenders" else -1
        rng.shuffle(moves)
        best, score = moves[0], -math.inf
        searched = 0
        for move in moves:
            if time.monotonic() >= deadline and score > -math.inf:
                break
            child = apply_move(state, move)
            value = sign * evaluate(child)
            searched += 1
            if value > score:
                best, score = move, value
        if diagnostics is not None:
            diagnostics.update(value=sign * score, completed_depth=1, searched_moves=searched)
        return best
    if algorithm in ("alpha_beta", "alphabeta"):
        return _alpha_beta(state, config, rng, deadline, diagnostics=diagnostics)
    if algorithm in ("mcts", "neural"):
        if algorithm == "neural" and not config.get("model_path") and config.get("_model") is None:
            raise ValueError("The neural opponent requires a registered model")
        probabilities = mcts_policy(state, config, diagnostics=diagnostics)
        temperature = float(config.get("temperature", 0.0))
        if not math.isfinite(temperature) or not 0 <= temperature <= 10:
            raise ValueError("Temperature must be between 0 and 10")
        if temperature > 0:
            options = list(probabilities)
            weights = [probabilities[move] ** (1 / temperature) for move in options]
            return rng.choices(options, weights=weights, k=1)[0]
        return max(probabilities, key=lambda move: probabilities[move])
    raise ValueError(f"Unknown AI algorithm: {algorithm}")


def analyze_position(state: State, config: dict | None = None) -> dict:
    """Expose an agent's bounded value estimate and strongest legal suggestion.

    Values retain the defender perspective used by training: +1 is a defender
    win, -1 an attacker win, and a rule draw is zero. A linear conversion to a
    percentage estimates expected score (draws half), not a calibrated frequency
    of wins. Traditional MCTS averages terminal/heuristic rollout leaves; a
    neural-search agent averages its backed-up neural/terminal leaf values. Alpha-beta
    reports its last completed minimax depth using its heuristic or neural leaf
    evaluator, falling back to its root evaluator if its deadline expires before
    completing depth one.

    Temperature affects actual play sampling, but suggestions always select the
    strongest searched option. Search and evaluation never mutate the position
    or caller configuration.
    """
    if state.winner:
        return {"value": evaluate(state), "source": "terminal", "best_move": None}
    configuration = _profile_configuration(config)
    configuration["temperature"] = 0
    details: dict = {}
    move = choose_move(state, configuration, diagnostics=details)
    algorithm = configuration.get("algorithm", configuration.get("profile_id", "baseline"))
    model_backed = algorithm in {"alpha_beta", "alphabeta", "mcts", "neural"} and (
        bool(configuration.get("model_path")) or configuration.get("_model") is not None
    )
    source = (
        "neural_search" if model_backed else "mcts_rollout" if algorithm == "mcts" else "heuristic"
    )
    value = details["value"]
    if not math.isfinite(value) or not -1 <= value <= 1:
        raise ValueError("Agent evaluation must be finite and between -1 and 1")
    result = {"value": value, "source": source, "best_move": move.to_dict()}
    for key in ("completed_depth", "simulations", "searched_moves"):
        if key in details:
            result[key] = details[key]
    return result


def play_game(
    ruleset: str,
    attackers: dict | None = None,
    defenders: dict | None = None,
    *,
    seed: int = 0,
    max_plies: int = 300,
    should_stop: Callable[[], bool] | None = None,
) -> dict:
    """Play an experiment, distinguishing unfinished cap runs from rule draws."""
    if not 1 <= max_plies <= 5000:
        raise ValueError("max_plies must be between 1 and 5000")
    state = new_game(ruleset)
    moves, durations = [], []
    for ply in range(max_plies):
        if should_stop and should_stop():
            raise InterruptedError("Tournament stopped during the current game")
        if state.winner:
            break
        config = dict((attackers if state.turn == "attackers" else defenders) or {})
        config["seed"] = seed + ply
        started = time.monotonic()
        move = choose_move(state, config)
        durations.append(time.monotonic() - started)
        moves.append({"source": move.source, "target": move.target})
        state = apply_move(state, move)
    return {
        "ruleset": ruleset,
        "winner": state.winner,
        "outcome": "completed" if state.winner else "truncated",
        "reason": state.reason or "experiment_ply_limit",
        "plies": len(moves),
        "moves": moves,
        "average_decision_time": sum(durations) / max(1, len(durations)),
        "total_decision_time": sum(durations),
        "state": state.to_dict(),
    }


def tournament(
    config: dict,
    report: Callable[[dict], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> dict:
    profiles = config.get(
        "players", [{"algorithm": "baseline"}, {"algorithm": "mcts", "iterations": 20}]
    )
    if not isinstance(profiles, list) or not 2 <= len(profiles) <= 4:
        raise ValueError("A tournament compares between two and four profiles")
    games_per_ruleset = int(config.get("games", 2))
    if not 2 <= games_per_ruleset <= 10000 or games_per_ruleset % 2:
        raise ValueError("Use an even number of games (2–10000) to alternate sides fairly")
    results: list[dict] = []
    scores = [0.0] * len(profiles)
    standings = [
        dict(player=index, wins=0, losses=0, draws=0, truncated=0, games=0, score=0.0)
        for index in range(len(profiles))
    ]
    by_ruleset: dict = {}

    def snapshot() -> dict:
        for index, standing in enumerate(standings):
            standing["score"] = scores[index]
        return {
            "players": profiles,
            "scores": scores,
            "standings": standings,
            "by_ruleset": by_ruleset,
            "games": results,
            "truncated": sum(r["winner"] is None for r in results),
            "average_game_length": sum(r["plies"] for r in results) / max(1, len(results)),
            "average_decision_time": sum(r["total_decision_time"] for r in results)
            / max(1, sum(r["plies"] for r in results)),
            "seed": config.get("seed", 0),
        }

    for ruleset in config.get("rulesets", ["tablut", "brandubh", "copenhagen"]):
        by_ruleset[ruleset] = {
            "wins": [0] * len(profiles),
            "draws": 0,
            "truncated": 0,
            "games": 0,
        }
        for first, second in combinations(range(len(profiles)), 2):
            for game in range(games_per_ruleset):
                if should_stop and should_stop():
                    return snapshot()
                attacker, defender = (first, second) if game % 2 == 0 else (second, first)
                # Preserve existing two-opponent comparisons; larger round robins
                # use a distinct derived seed for every scheduled match.
                game_seed = game if len(profiles) == 2 else len(results)
                try:
                    result = play_game(
                        ruleset,
                        profiles[attacker],
                        profiles[defender],
                        seed=int(config.get("seed", 0)) + game_seed,
                        max_plies=int(config.get("max_plies", 300)),
                        should_stop=should_stop,
                    )
                except InterruptedError:
                    return snapshot()
                result["attacker_player"] = attacker
                result["defender_player"] = defender
                for player in (attacker, defender):
                    standings[player]["games"] += 1
                by_ruleset[ruleset]["games"] += 1
                if result["winner"] == "draw":
                    for player in (attacker, defender):
                        scores[player] += 0.5
                        standings[player]["draws"] += 1
                    by_ruleset[ruleset]["draws"] += 1
                elif result["winner"] is not None:
                    winner, loser = (
                        (attacker, defender)
                        if result["winner"] == "attackers"
                        else (defender, attacker)
                    )
                    scores[winner] += 1
                    standings[winner]["wins"] += 1
                    standings[loser]["losses"] += 1
                    by_ruleset[ruleset]["wins"][winner] += 1
                else:
                    for player in (attacker, defender):
                        standings[player]["truncated"] += 1
                    by_ruleset[ruleset]["truncated"] += 1
                results.append(result)
                if report:
                    report(snapshot())
    return snapshot()
