"""Cooperative evaluation with independent queues for each frozen model."""

from __future__ import annotations

import time
from typing import Callable, Generator

from .ai import choose_move, mcts_context
from .cooperative import CooperativeSearch
from .engine import State, apply_move, new_game
from .inference import ExactInferenceCache, InferenceMetrics, policy_value_batch
from .inference_backends import create_inference


def evaluation_backend(config: dict) -> str:
    return (
        config.get("inference_backend", "pytorch")
        if config.get("evaluation_inference_backend", "pytorch") == "training"
        else "pytorch"
    )


def game_context(
    index: int, config: dict, context: dict, models: list, seed: int, should_stop, *, neural=None
) -> Generator[tuple[int, State], tuple[dict, float], dict]:
    """A game owns its RNG/search state; only neural leaves cross the boundary.

    ``neural`` forces external leaf evaluation for callers that supply priors and
    values from outside the process (``models`` is then only a placeholder list
    whose length selects whether the opponent also searches neurally).
    """
    state = new_game(config["ruleset"])
    side = "attackers" if index % 2 == 0 else "defenders"
    pair_seed = seed + 1000 * (index // 2)
    simulations = cutoffs = 0
    for ply in range(config["evaluation_max_plies"]):
        if should_stop and should_stop():
            raise InterruptedError("Evaluation was cancelled")
        if state.winner:
            break
        candidate = state.turn == side
        settings = dict(context["candidate_config"] if candidate else context["opponent_config"])
        settings["seed"] = pair_seed + ply
        details: dict = {}
        if candidate or len(models) == 2:
            model_index = 0 if candidate else 1
            if neural is None:
                settings["_model"] = models[model_index]
            search = mcts_context(
                state, settings, diagnostics=details, should_stop=should_stop, neural=neural
            )
            try:
                pending = next(search)
                while True:
                    result = yield model_index, pending
                    pending = search.send(result)
            except StopIteration as finished:
                policy = finished.value
            finally:
                search.close()
            move = max(policy, key=lambda action: policy[action])
        else:
            move = choose_move(state, settings, diagnostics=details, should_stop=should_stop)
        simulations += details.get("simulations", 0)
        cutoffs += details.get("deadline_cutoffs", 0)
        state = apply_move(state, move)
    return {
        "pair": index // 2,
        "seed": pair_seed,
        "candidate_side": side,
        "winner": state.winner,
        "outcome": "completed" if state.winner else "truncated",
        "score": 0.5 if state.winner == "draw" else float(state.winner == side),
        "plies": state.ply,
        "reason": state.reason or "evaluation_ply_limit",
        "simulations": simulations,
        "search_deadline_cutoffs": cutoffs,
    }


def batched_evaluation(
    model,
    config: dict,
    context: dict,
    *,
    previous=None,
    seed: int,
    should_stop: Callable | None,
    monitor=None,
) -> tuple[list[dict], dict]:
    models = [model] + ([previous] if previous is not None else [])
    concurrency = min(config["evaluation_games"], config.get("evaluation_concurrency", 16))
    if config.get("evaluation_mode") == "sequential":
        concurrency = 1
    metrics = [InferenceMetrics() for _ in models]
    caches = [
        ExactInferenceCache(config["inference_cache_capacity"])
        if config.get("inference_cache_mode") == "exact"
        else None
        for _ in models
    ]
    backends: list = []
    outcomes: dict[int, dict] = {}
    started = 0
    refresh_seconds = 0.0

    def evaluate(identity, states):
        if monitor is not None:
            # One cheap update per searched position keeps long games live.
            monitor.tick(evaluation_plies=len(states))
        return policy_value_batch(
            states,
            models[identity],
            mixed_precision=False,
            metrics=metrics[identity],
            backend=backends[identity],
            cache=caches[identity],
        )

    wins = draws = losses = 0

    def completed_game(index, result):
        """Record the outcome and keep the dashboard's running tallies live."""
        nonlocal wins, draws, losses
        outcomes[index] = result
        if monitor is None:
            return
        side = result["candidate_side"]
        winner = result["winner"]
        # Truncated games count as neither a win nor a loss, matching the scoring.
        wins += int(winner == side)
        draws += int(winner == "draw")
        losses += int(winner not in (None, "draw") and winner != side)
        monitor.tick(evaluation_games=1)
        monitor.counts(evaluation_wins=wins, evaluation_draws=draws, evaluation_losses=losses)

    search = CooperativeSearch(
        evaluate,
        identities=len(models),
        batch_size=config.get("evaluation_batch_size", 32),
        should_stop=should_stop,
        on_complete=completed_game,
    )

    def fill():
        nonlocal started
        while len(search) < concurrency and started < config["evaluation_games"]:
            index = started
            started += 1
            search.start(index, game_context(index, config, context, models, seed, should_stop))

    try:
        for selected in models:
            backend = None
            if evaluation_backend(config) == "openvino":
                backend = create_inference(
                    selected,
                    {
                        **config,
                        "games": config["evaluation_games"],
                        "self_play_concurrency": concurrency,
                        "inference_batch_size": config.get("evaluation_batch_size", 32),
                    },
                )
            backends.append(backend)
            if backend is not None:
                # Self-play's graph predates optimization. Never reuse it here.
                before = time.monotonic()
                backend.refresh()
                refresh_seconds += time.monotonic() - before
        search.run(fill)
    finally:
        search.close()
        for backend in backends:
            if backend is not None:
                backend.close()
    return [outcomes[index] for index in range(config["evaluation_games"])], {
        "mode": "batched" if concurrency > 1 else "sequential",
        "concurrency": concurrency,
        "inference_backend": evaluation_backend(config),
        "precision": "float32",
        "backend_refresh_seconds": refresh_seconds,
        "models": [metric.snapshot() for metric in metrics],
    }
