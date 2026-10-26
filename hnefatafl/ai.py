"""Bounded, reproducible computer players independent of HTTP and persistence.

All evaluations use the defender's perspective. Search never mutates a state.
The neural evaluator is optional: ordinary play does not import PyTorch.
"""

from __future__ import annotations

import math
import random
import time
from array import array
from dataclasses import dataclass, field
from functools import lru_cache
from itertools import combinations
from typing import Any, Callable, Generator

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
            "id": "shield_maiden",
            "name": "Shield maiden",
            "description": "Shield maiden uses a stronger tactical heuristic, designed for low evaluation overhead.",
            "algorithm": "shield_maiden",
            "difficulty": "Intermediate",
            "parameters": {"time_limit": 1.0, "heuristic_version": 1},
            "supported_rulesets": common,
        },
        {
            "id": "alpha_beta",
            "name": "Jarl",
            "algorithm": "alpha_beta",
            "difficulty": "Advanced",
            "parameters": {"depth": 5, "time_limit": 2.0},
            "supported_rulesets": common,
        },
        {
            "id": "mcts",
            "name": "Seer",
            "algorithm": "mcts",
            "difficulty": "Advanced",
            "parameters": {
                # The Seer preset is ruleset-independent. The 2-second ceiling
                # bounds search, with 5,000 visits as the upper cap. Per-ruleset
                # recommended plans (128/192/256 visits) are available in
                # training jobs.
                "iterations": 5000,
                "time_limit": 2.0,
                "exploration": 1.4,
                "temperature": 0.0,
                "rollout_depth": 8,
            },
            "supported_rulesets": common,
        },
        {
            "id": "neural",
            "name": "Trained model",
            "algorithm": "neural",
            "difficulty": "Model dependent",
            "parameters": {"iterations": 40, "time_limit": 10.0},
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
    progress: Callable[[dict], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> Move:
    depth = int(config.get("depth", 2))
    if not 1 <= depth <= 8:
        raise ValueError("Search depth must be between 1 and 8")
    root_moves = legal_moves(state)
    best_move = rng.choice(root_moves)
    cache: dict[tuple, float] = {}
    model_path = config.get("model_path")
    model = config.get("_model")
    if diagnostics is None and progress is not None:
        diagnostics = {}
    if diagnostics is not None:
        diagnostics.update(
            value=policy_value(state, model_path, model)[1]
            if model_path or model is not None
            else evaluate(state),
            completed_depth=0,
            best_move=best_move.to_dict(),
        )

    def search(position: State, remaining: int, alpha: float, beta: float) -> float:
        if should_stop and should_stop():
            raise InterruptedError("Search cancelled")
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
            diagnostics.update(
                value=sign * score_best,
                completed_depth=current_depth,
                best_move=best_move.to_dict(),
            )
            if progress is not None:
                progress(analysis_from_search(state, config, best_move, diagnostics))
    return best_move


@dataclass(slots=True)
class _Node:
    state: State
    prior: float = 1.0
    visits: int = 0
    total: float = 0.0
    children: dict[Move, "_Node"] = field(default_factory=dict)
    # Owned legal priors only, consumed as edges are created. No logits/tensors.
    remaining: dict[Move, float] | None = None
    value: float | None = None
    # Leaves usually have no children yet. Pack deferred policies to avoid
    # retaining a dictionary, Move objects and boxed floats for every leaf.
    pending_policy: tuple[array, array] | None = None


def _select_child(node: _Node, exploration: float) -> _Node:
    """PUCT in insertion order, retaining the reference arithmetic and first tie."""
    sign = 1 if node.state.turn == "defenders" else -1
    parent_scale = math.sqrt(node.visits + 1)
    best = None
    best_score = -math.inf
    for child in node.children.values():
        # Every selectable edge has completed at least one backup. A pending
        # child cannot be selected while its generator is waiting for inference.
        score = sign * child.total / child.visits + exploration * child.prior * parent_scale / (
            1 + child.visits
        )
        if best is None or score > best_score:
            best, best_score = child, score
    assert best is not None
    return best


def mcts_context(
    state: State,
    config: dict | None = None,
    *,
    diagnostics: dict | None = None,
    progress: Callable[[dict], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> Generator[State, tuple[dict[Move, float], float], dict[Move, float]]:
    """Cooperative PUCT search yielding one dependent evaluation at a time.

    Send legal priors and a defender-relative value for each yielded position.
    Independent searches can share inference batches; this context never advances
    a dependent simulation until its evaluation has been supplied.
    """
    config = {"algorithm": "mcts", **(config or {})}
    budget = int(config.get("iterations", 80))
    if not 1 <= budget <= 100_000:
        raise ValueError("MCTS iterations must be between 1 and 100000")
    exploration = float(config.get("exploration", 1.4))
    if not 0 <= exploration <= 20:
        raise ValueError("Exploration must be between 0 and 20")
    rollout_depth = int(config.get("rollout_depth", 8))
    if not 0 <= rollout_depth <= 128:
        raise ValueError("Rollout depth must be between 0 and 128")
    seconds = float(config.get("time_limit", 10.0))
    if not math.isfinite(seconds) or seconds <= 0 or seconds > 300:
        raise ValueError("Time limit must be positive and at most 300 seconds")
    rng = random.Random(config.get("seed", 0))
    model_path = config.get("model_path")
    model = config.get("_model")
    neural = bool(model_path) or model is not None
    deadline = time.monotonic() + seconds
    root = _Node(state)

    def check_cancelled() -> None:
        if should_stop is not None and should_stop():
            raise InterruptedError("Self-play search cancelled")

    check_cancelled()
    if state.winner or not legal_moves(state):
        raise ValueError("No legal moves in terminal position")
    root_priors, root_value = yield state
    check_cancelled()
    requests = int(neural)
    prediction_reuses = 0
    if diagnostics is None and progress is not None:
        diagnostics = {}
    if diagnostics is not None:
        diagnostics.update(
            value=root_value,
            root_value=root_value,
            simulations=0,
            mcts_neural_requests=requests,
            mcts_node_prediction_reuses=0,
        )
    # Never overlook a one-move win even with a tiny simulation budget.
    for move in root_priors:
        check_cancelled()
        if apply_move(state, move).winner == state.turn:
            if diagnostics is not None:
                diagnostics.update(
                    value=1.0 if state.turn == "defenders" else -1.0,
                    best_move=move.to_dict(),
                    immediate_win_shortcuts=1,
                )
                if progress is not None:
                    progress(analysis_from_search(state, config, move, diagnostics))
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
    root.remaining = root_priors.copy()
    root.value = root_value
    next_report = 0.0
    for _ in range(budget):
        check_cancelled()
        if time.monotonic() >= deadline and root.visits:
            if diagnostics is not None:
                diagnostics["deadline_cutoffs"] = 1
            break
        node = root
        path = [node]
        while not node.state.winner:
            check_cancelled()
            remaining = node.remaining
            if remaining is None:
                if node.pending_policy is not None:
                    actions, probabilities = node.pending_policy
                    area = node.state.size**2
                    remaining = {
                        Move(action // area, action % area): probability
                        for action, probability in zip(actions, probabilities)
                    }
                    node.pending_policy = None
                    del actions, probabilities
                    prediction_reuses += 1
                else:
                    priors, predicted_value = yield node.state
                    check_cancelled()
                    remaining = dict(priors)
                    del priors
                    node.value = predicted_value
                node.remaining = remaining
            # Progressive widening explores deeply even on an opening board
            # with hundreds of legal moves and a modest simulation budget.
            # For integer visits/edge count, k < ceil(sqrt(v + 1)) iff k*k <= v.
            if remaining and len(node.children) ** 2 <= node.visits:
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
            node = _select_child(node, exploration)
            path.append(node)
        if node.state.winner:
            value = evaluate(node.state)
        elif neural:
            if node.value is None:
                requests += 1
                priors, value = yield node.state
                check_cancelled()
                area = node.state.size**2
                # uint16 covers all current boards (largest action is 14640).
                # Doubles retain the original Python float exactly; sampling
                # order and RNG consumption are identical after unpacking.
                node.pending_policy = (
                    array("H", (move.source * area + move.target for move in priors)),
                    array("d", priors.values()),
                )
                del priors
                node.value = value
            else:
                prediction_reuses += 1
                value = node.value
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
        if diagnostics is not None:
            diagnostics.update(
                simulations=root.visits,
                mcts_neural_requests=requests,
                mcts_node_prediction_reuses=prediction_reuses,
            )
        # Bound observer overhead while making long-running searches useful live.
        if progress is not None and time.monotonic() >= next_report:
            assert diagnostics is not None
            best = max(root.children, key=lambda move: root.children[move].visits)
            diagnostics.update(
                value=root.total / root.visits,
                simulations=root.visits,
                best_move=best.to_dict(),
            )
            progress(analysis_from_search(state, config, best, diagnostics))
            next_report = time.monotonic() + 0.1
    total = sum(child.visits for child in root.children.values())
    if diagnostics is not None:
        best = (
            max(root.children, key=lambda move: root.children[move].visits)
            if total
            else max(root_priors, key=lambda move: root_priors[move])
        )
        diagnostics.update(
            value=root.total / root.visits if root.visits else root_value,
            simulations=root.visits,
            best_move=best.to_dict(),
        )
        if progress is not None:
            progress(analysis_from_search(state, config, best, diagnostics))
    if not total:
        return root_priors
    return {move: child.visits / total for move, child in root.children.items()}


def mcts_policy(
    state: State,
    config: dict | None = None,
    *,
    diagnostics: dict | None = None,
    progress: Callable[[dict], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    inference_metrics: Any = None,
) -> dict[Move, float]:
    """Drive the cooperative search synchronously with reference single inference."""
    configuration = config or {}
    model_path = configuration.get("model_path")
    model = configuration.get("_model")
    context = mcts_context(
        state, config, diagnostics=diagnostics, progress=progress, should_stop=should_stop
    )
    try:
        pending = next(context)
        while True:
            if (
                configuration.get("_inference_backend") is not None
                or configuration.get("_inference_cache") is not None
                or configuration.get("_mixed_precision", False)
            ):
                from .inference import policy_value_batch

                assert model is not None
                result = policy_value_batch(
                    [pending],
                    model,
                    mixed_precision=bool(configuration.get("_mixed_precision", False)),
                    metrics=inference_metrics,
                    backend=configuration.get("_inference_backend"),
                    cache=configuration.get("_inference_cache"),
                )[0]
            elif inference_metrics is not None and (model_path or model is not None):
                device = next(model.parameters()).device if model is not None else "cpu"
                inference_metrics.requests += 1
                inference_metrics.cache_misses += 1
                with inference_metrics.measure(1, device=device):
                    result = policy_value(pending, model_path, model)
            else:
                result = policy_value(pending, model_path, model)
            pending = context.send(result)
    except StopIteration as finished:
        return finished.value
    finally:
        context.close()


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
    state: State,
    config: dict | None = None,
    *,
    diagnostics: dict | None = None,
    progress: Callable[[dict], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> Move:
    """Search once, optionally exposing completed results to an observer.

    Progress receives independent analysis snapshots. Observer exceptions propagate,
    permitting callers to cancel a superseded background search cleanly.
    """
    config = _profile_configuration(config)
    if should_stop and should_stop():
        raise InterruptedError("Search cancelled")
    moves = legal_moves(state)
    if state.winner or not moves:
        raise ValueError("Cannot choose a move in a terminal position")
    algorithm = config.get("algorithm", config.get("profile_id", "baseline"))
    wrapper = config.get("search_wrapper", "mcts")
    if algorithm == "neural":
        if wrapper not in ("mcts", "alpha_beta", "none"):
            raise ValueError("Neural search wrapper must be mcts, alpha_beta or none")
        if not config.get("model_path") and config.get("_model") is None:
            raise ValueError("The neural opponent requires a registered model")
    rng = random.Random(config.get("seed", 0))
    # Only time-limited MCTS defaults to ten seconds; non-MCTS strategies keep
    # their historical two-second ceiling when no explicit value is supplied.
    mcts_budget = algorithm == "mcts" or (algorithm == "neural" and wrapper == "mcts")
    seconds = float(config.get("time_limit", 10.0 if mcts_budget else 2.0))
    if not math.isfinite(seconds) or not 0 < seconds <= 300:
        raise ValueError("Time limit must be positive and at most 300 seconds")
    deadline = time.monotonic() + seconds
    if diagnostics is None and progress is not None:
        diagnostics = {}
    latest: dict | None = None

    def notify(snapshot: dict) -> None:
        nonlocal latest
        if progress is not None and snapshot != latest:
            # Keep private comparison state even if an observer changes its snapshot.
            latest = {**snapshot, "best_move": dict(snapshot["best_move"])}
            progress(snapshot)

    observer = notify if progress is not None else None

    def finished(move: Move) -> Move:
        if diagnostics is not None:
            diagnostics["best_move"] = move.to_dict()
            notify(analysis_from_search(state, config, move, diagnostics))
        return move

    if algorithm == "shield_maiden":
        from .shield_maiden import VERSION, choose

        version = config.get("heuristic_version", VERSION)
        if type(version) is not int or version != VERSION:
            raise ValueError("Unsupported Shield maiden heuristic version")
        return finished(
            choose(state, moves, rng, deadline, diagnostics=diagnostics, should_stop=should_stop)
        )
    if algorithm == "baseline":
        sign = 1 if state.turn == "defenders" else -1
        rng.shuffle(moves)
        best, score = moves[0], -math.inf
        searched = 0
        for move in moves:
            if should_stop and should_stop():
                raise InterruptedError("Search cancelled")
            if time.monotonic() >= deadline and score > -math.inf:
                break
            child = apply_move(state, move)
            value = sign * evaluate(child)
            searched += 1
            if value > score:
                best, score = move, value
        if diagnostics is not None:
            diagnostics.update(value=sign * score, completed_depth=1, searched_moves=searched)
        return finished(best)
    if algorithm in ("alpha_beta", "alphabeta") or (
        algorithm == "neural" and wrapper == "alpha_beta"
    ):
        return finished(
            _alpha_beta(
                state,
                config,
                rng,
                deadline,
                diagnostics=diagnostics if diagnostics is not None or algorithm != "neural" else {},
                progress=observer,
                should_stop=should_stop,
            )
        )
    if algorithm in ("mcts", "neural"):
        if algorithm == "neural" and wrapper == "none":
            probabilities, value = policy_value(
                state, config.get("model_path"), config.get("_model")
            )
            if diagnostics is not None:
                diagnostics["value"] = value
        else:
            probabilities = mcts_policy(
                state, config, diagnostics=diagnostics, progress=observer, should_stop=should_stop
            )
        temperature = float(config.get("temperature", 0.0))
        if not math.isfinite(temperature) or not 0 <= temperature <= 10:
            raise ValueError("Temperature must be between 0 and 10")
        if temperature > 0:
            options = list(probabilities)
            # Scale before exponentiating so a low temperature cannot underflow
            # every legal policy probability to zero. Preserve historical MCTS
            # sampling arithmetic for existing seeded games.
            maximum = (
                max(probabilities.values()) if algorithm == "neural" and wrapper == "none" else 1
            )
            weights = [(probabilities[move] / maximum) ** (1 / temperature) for move in options]
            return finished(rng.choices(options, weights=weights, k=1)[0])
        return finished(max(probabilities, key=lambda move: probabilities[move]))
    raise ValueError(f"Unknown AI algorithm: {algorithm}")


def analysis_from_search(
    state: State, config: dict | None, move: Move | None, details: dict
) -> dict:
    """Build a report from actual search diagnostics without evaluating again."""
    if state.winner:
        return {"value": evaluate(state), "source": "terminal", "best_move": None}
    configuration = _profile_configuration(config)
    algorithm = configuration.get("algorithm", configuration.get("profile_id", "baseline"))
    model_backed = algorithm in {"alpha_beta", "alphabeta", "mcts", "neural"} and (
        bool(configuration.get("model_path")) or configuration.get("_model") is not None
    )
    source = (
        "neural_search" if model_backed else "mcts_rollout" if algorithm == "mcts" else "heuristic"
    )
    if model_backed and algorithm == "neural" and configuration.get("search_wrapper") == "none":
        source = "neural_value"
    value = details["value"]
    if not math.isfinite(value) or not -1 <= value <= 1:
        raise ValueError("Agent evaluation must be finite and between -1 and 1")
    result = {"value": value, "source": source, "best_move": move.to_dict() if move else None}
    for key in ("completed_depth", "simulations", "searched_moves"):
        if key in details:
            result[key] = details[key]
    return result


def analyze_position(
    state: State, config: dict | None = None, *, progress: Callable[[dict], None] | None = None
) -> dict:
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
        result = analysis_from_search(state, config, None, {})
        if progress is not None:
            progress(result.copy())
        return result
    configuration = _profile_configuration(config)
    configuration["temperature"] = 0
    details: dict = {}
    move = choose_move(state, configuration, diagnostics=details, progress=progress)
    return analysis_from_search(state, configuration, move, details)


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
