"""Cooperative, single-model self-play with one pending inference per game."""

from __future__ import annotations

import random
import time
from collections import deque
from typing import Callable, Generator

import torch

from .ai import mcts_context
from .engine import Move, State, apply_move, legal_moves, new_game
from .inference import InferenceMetrics, policy_value_batch
from .models import PolicyValueNet, encode_state


GameResult = tuple[list[dict], dict]
Evaluation = tuple[dict[Move, float], float]


class SelfPlayMetrics:
    """Aggregate counters; wall time and CPU activity are deliberately separate."""

    def __init__(
        self, model: PolicyValueNet, mode: str, concurrency: int, *, measure_cuda: bool = False
    ):
        self.device = next(model.parameters()).device
        self.inference = InferenceMetrics(track_cuda=measure_cuda and self.device.type == "cuda")
        self.started = time.monotonic()
        self.mode = mode
        self.concurrency = concurrency
        self.games = self.positions = self.simulations = 0
        self.completed = self.drawn = self.truncated = 0
        self.cpu_search_seconds = 0.0
        self.search_traversal_wall_seconds = 0.0
        if self.device.type == "cuda":
            torch.cuda.reset_peak_memory_stats(self.device)

    def record(self, result: dict) -> None:
        self.games += 1
        self.positions += result["plies"]
        self.simulations += result.get("simulations", 0)
        self.completed += int(result["winner"] is not None)
        self.drawn += int(result["winner"] == "draw")
        self.truncated += int(result["winner"] is None)

    def snapshot(self, *, finalize_cuda: bool = False) -> dict:
        inference = self.inference.snapshot(finalize_cuda=finalize_cuda)
        duration = max(time.monotonic() - self.started, 1e-12)
        result = {
            "self_play_mode": self.mode,
            "self_play_concurrency": self.concurrency,
            "self_play_wall_seconds": duration,
            "games": self.games,
            "self_play_positions": self.positions,
            "mcts_simulations": self.simulations,
            "games_per_hour": self.games * 3600 / duration,
            "positions_per_second": self.positions / duration,
            "simulations_per_second": self.simulations / duration,
            "average_game_length": self.positions / self.games if self.games else 0.0,
            "completed_games": self.completed,
            "drawn_games": self.drawn,
            "truncated_games": self.truncated,
            "cpu_search_seconds": self.cpu_search_seconds,
            "search_traversal_wall_seconds": self.search_traversal_wall_seconds,
            **inference,
            "neural_evaluations_per_second": inference["neural_evaluations"] / duration,
        }
        if self.device.type == "cuda":
            result.update(
                gpu_peak_allocated_bytes=torch.cuda.max_memory_allocated(self.device),
                gpu_peak_reserved_bytes=torch.cuda.max_memory_reserved(self.device),
            )
        return result


def _game_context(
    model: PolicyValueNet,
    config: dict,
    seed: int,
    should_stop: Callable[[], bool] | None,
) -> Generator[State, Evaluation, GameResult]:
    rng = random.Random(seed)
    state = new_game(config["ruleset"])
    examples: list[dict] = []
    simulations = 0
    started = time.monotonic()
    for ply in range(config["max_game_plies"]):
        if should_stop and should_stop():
            raise InterruptedError("Training was cancelled")
        if state.winner:
            break
        moves = legal_moves(state)
        search_config = {
            "_model": model,
            "iterations": config["mcts_iterations"],
            "time_limit": config["mcts_time_limit"],
            "seed": seed + ply,
            "exploration": config["exploration"],
            "rollout_depth": 0,
            "dirichlet_alpha": config["dirichlet_alpha"],
            "dirichlet_fraction": config["dirichlet_fraction"],
        }
        details: dict = {}
        policy = yield from mcts_context(
            state, search_config, diagnostics=details, should_stop=should_stop
        )
        simulations += details.get("simulations", 0)
        area = state.size**2
        examples.append(
            {
                "input": encode_state(state),
                "actions": [move.source * area + move.target for move in policy],
                "probabilities": list(policy.values()),
                "legal": [move.source * area + move.target for move in moves],
                "value": 0.0,
            }
        )
        temperature = config["temperature"] if ply < 16 else max(0.1, config["temperature"] / 4)
        options = list(policy)
        weights = [policy[move] ** (1 / temperature) for move in options]
        state = apply_move(state, rng.choices(options, weights=weights, k=1)[0])
    winner = state.winner
    if winner is not None:
        value = {"attackers": -1.0, "defenders": 1.0, "draw": 0.0}[winner]
    else:
        # An unfinished game is bootstrapped, never relabeled a rule draw.
        _, value = yield state
    for example in examples:
        example["value"] = value
    return examples, {
        "winner": winner,
        "outcome": "completed" if winner else "truncated",
        "reason": state.reason or "training_ply_limit",
        "bootstrap_value": value if winner is None else None,
        "plies": len(examples),
        "simulations": simulations,
        "duration": time.monotonic() - started,
    }


def batched_self_play(
    model: PolicyValueNet,
    config: dict,
    *,
    seed: int,
    metrics: SelfPlayMetrics,
    should_stop: Callable[[], bool] | None = None,
    progress: Callable[[dict], None] | None = None,
    on_game: Callable[[int, list[dict], dict], None] | None = None,
    retain_examples: bool = True,
) -> list[GameResult]:
    """Interleave independent searches, replacing finished games immediately.

    Results and replay callbacks remain in game-index order even when different
    games finish at different times. No simulation advances before its own
    inference result has been supplied.
    """
    count = config["games"]
    concurrency = min(count, config["self_play_concurrency"])
    batch_size = config["inference_batch_size"]
    pending: deque[tuple[int, Generator[State, Evaluation, GameResult], State]] = deque()
    completed: dict[int, GameResult] = {}
    started = flushed = 0
    model.eval()

    def cancelled() -> None:
        if should_stop and should_stop():
            raise InterruptedError("Training was cancelled")

    def advance(index, context, evaluation=None):
        cancelled()
        wall_started = time.monotonic()
        cpu_started = time.process_time()
        try:
            state = next(context) if evaluation is None else context.send(evaluation)
        except StopIteration as stopped:
            completed[index] = stopped.value
            metrics.record(stopped.value[1])
            return None
        finally:
            metrics.cpu_search_seconds += time.process_time() - cpu_started
            metrics.search_traversal_wall_seconds += time.monotonic() - wall_started
        return index, context, state

    def add_game():
        nonlocal started
        index = started
        started += 1
        return advance(index, _game_context(model, config, seed + index, should_stop))

    def flush_completed():
        nonlocal flushed
        while flushed in completed:
            examples, result = completed[flushed]
            if on_game:
                on_game(flushed, examples, result)
            if not retain_examples:
                completed[flushed] = ([], result)
            flushed += 1

    def fill_slots():
        # A long early game must not retain an iteration's worth of finished
        # examples behind it. Training applies bounded backpressure until its
        # ordered replay prefix advances; collection callers already retain all
        # examples explicitly and do not need this extra window.
        window = 2 * concurrency if not retain_examples else count
        while len(pending) < concurrency and started < count and started - flushed < window:
            request = add_game()
            if request is not None:
                pending.append(request)
            else:
                flush_completed()
                if progress:
                    progress({"game": metrics.games, "performance": metrics.snapshot()})

    fill_slots()
    while pending:
        cancelled()
        requests = [pending.popleft() for _ in range(min(batch_size, len(pending)))]
        evaluations = policy_value_batch(
            [request[2] for request in requests],
            model,
            mixed_precision=bool(config["mixed_precision"]),
            metrics=metrics.inference,
        )
        for (index, context, _), evaluation in zip(requests, evaluations):
            request = advance(index, context, evaluation)
            if request is not None:
                pending.append(request)
            flush_completed()
            if request is None and progress:
                progress({"game": metrics.games, "performance": metrics.snapshot()})
        fill_slots()
    return [completed[index] for index in range(count)]
