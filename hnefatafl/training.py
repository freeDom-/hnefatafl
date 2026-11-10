"""Headless self-play training, checkpoint resumption, evaluation and Optuna.

The web server launches this module's CLI in a separate process. No training
threads or CUDA contexts are shared with the gameplay server.
"""

from __future__ import annotations

import json
import math
import os
import random
import threading
import time
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

import torch
from torch.nn import functional as F

from .ai import mcts_policy, policy_value
from .inference import ExactInferenceCache
from .engine import apply_move, legal_moves, new_game
from .models import (
    ModelRegistry,
    PolicyValueNet,
    device_info,
    encode_state,
    export_checkpoint,
    load_checkpoint,
    resolve_device,
    save_checkpoint,
)
from .inference_backends import InferenceBackend, create_inference, normalize_inference
from .training_augmentation import (
    AugmentationStats,
    augmentation_rng,
    collate_batch,
    sample_symmetries,
    validate_mode,
)
from .training_presets import RECOMMENDED
from .training_parameters import parameter_snapshot, restore_parameter_history
from .training_optimizers import (
    OptimizerLifecycle,
    SCHEDULE_FREE,
    SCHEDULE_FREE_ADAPTATION_ERROR,
    SCHEDULE_FREE_DEPENDENCY_ERROR,
)
from .game_statistics import checkpoint_cumulative, self_play_statistics
from .hyper_adaptation import (
    ADAPTATION_EVENT_TAIL,
    adaptation_defaults,
    normalize_adaptation,
    observation_from_evaluation,
    resolve_controller,
)
from .training_evaluation import (
    EvaluationSelection,
    evaluate_model,
    evaluation_context,
    frozen_model,
)
from .training_monitoring import (
    HEARTBEAT_FILENAME,
    ProgressSink,
    TrainingMonitor,
    heartbeat_projection,
    without_live_timing,
)


DEFAULTS = {
    "ruleset": "brandubh",
    "preset": "recommended",
    "channels": 16,
    "blocks": 1,
    "games": 4,
    "iterations": 5,
    "epochs": 2,
    "batch_size": 32,
    "learning_rate": 0.001,
    "optimizer": "adamw",
    "schedulefree_warmup_steps": 0,
    "mcts_iterations": 24,
    "mcts_time_limit": 10.0,
    "self_play_mode": "auto",
    "self_play_concurrency": 16,
    "inference_batch_size": 32,
    "inference_cache_mode": "off",
    "inference_cache_capacity": 4096,
    "exploration": 1.4,
    "temperature": 1.0,
    "dirichlet_alpha": 0.3,
    "dirichlet_fraction": 0.25,
    "replay_buffer_size": 5000,
    "weight_decay": 0.0001,
    "momentum": 0.9,
    "training_symmetry_mode": "random_d4",
    "checkpoint_frequency": 1,
    "evaluation_opponent": "shield_bearer",
    "evaluation_mode": "auto",
    "evaluation_concurrency": 16,
    "evaluation_batch_size": 32,
    "evaluation_inference_backend": "pytorch",
    "evaluation_frequency": 1,
    "evaluation_time_limit": 10.0,
    "evaluation_jarl_depth": 5,
    "evaluation_jarl_time_limit": 2.0,
    "champion_confidence": 0.95,
    "champion_min_pairs": 12,
    "champion_min_improvement": 0.0,
    "evaluation_games": 2,
    "evaluation_iterations": 16,
    "evaluation_max_plies": 150,
    "max_game_plies": 150,
    "seed": 0,
    "device": "auto",
    "inference_backend": "pytorch",
    "inference_device": "auto",
    "mixed_precision": True,
    "cpu_threads": 2,
    "search_workers": 0,
    "output_dir": "data/training",
    "model_name": "Self-play learner",
    "save_all_best": False,
    # Optional online learning-rate adaptation (off by default).
    **adaptation_defaults(),
}

PRESETS: dict[str, dict] = {
    "recommended": {},
    "smoke": {
        "channels": 4,
        "blocks": 0,
        "games": 1,
        "iterations": 1,
        "epochs": 1,
        "batch_size": 8,
        "mcts_iterations": 2,
        "mcts_time_limit": 0.1,
        "evaluation_iterations": 2,
        "max_game_plies": 12,
        "evaluation_max_plies": 12,
        "replay_buffer_size": 64,
    },
    "beginner": {},
    "balanced": {
        "channels": 32,
        "blocks": 2,
        "games": 16,
        "iterations": 20,
        "epochs": 4,
        "batch_size": 64,
        "mcts_iterations": 100,
        "replay_buffer_size": 10000,
        "max_game_plies": 300,
    },
}


def normalize_config(configuration: dict, *, resolved: bool = True) -> dict:
    unknown = set(configuration) - set(DEFAULTS) - {"registry_dir", "resume"}
    if unknown:
        raise ValueError(f"Unknown training parameters: {', '.join(sorted(unknown))}")
    preset = configuration.get("preset", DEFAULTS["preset"])
    if not isinstance(preset, str) or preset not in PRESETS:
        raise ValueError("Unknown training preset")
    ruleset = configuration.get("ruleset", DEFAULTS["ruleset"])
    if not isinstance(ruleset, str) or ruleset not in RECOMMENDED:
        raise ValueError("Unknown training ruleset")
    plan = RECOMMENDED[ruleset] if preset == "recommended" else {}
    config = {**DEFAULTS, **PRESETS[preset], **plan, **configuration}
    config["preset"] = preset
    if "evaluation_time_limit" not in configuration:
        config["evaluation_time_limit"] = config["mcts_time_limit"]
    if config["ruleset"] not in ("brandubh", "tablut", "copenhagen"):
        raise ValueError("Unknown training ruleset")
    integer_bounds = {
        "channels": (4, 128),
        "blocks": (0, 8),
        "games": (1, 10000),
        "iterations": (1, 100000),
        "epochs": (1, 100),
        "batch_size": (1, 2048),
        "mcts_iterations": (1, 100000),
        "evaluation_iterations": (1, 100000),
        "max_game_plies": (2, 5000),
        "evaluation_max_plies": (2, 5000),
        "replay_buffer_size": (1, 20000),
        "checkpoint_frequency": (1, 10000),
        "evaluation_frequency": (1, 10000),
        "evaluation_jarl_depth": (1, 8),
        "champion_min_pairs": (2, 5000),
        "evaluation_games": (2, 10000),
        "evaluation_concurrency": (1, 1024),
        "evaluation_batch_size": (1, 4096),
        "cpu_threads": (1, 64),
        "search_workers": (0, 32),
        "self_play_concurrency": (1, 1024),
        "inference_batch_size": (1, 4096),
        "inference_cache_capacity": (1, 65536),
        "schedulefree_warmup_steps": (0, 1000000),
    }
    for key, (low, high) in integer_bounds.items():
        if type(config[key]) is not int or not low <= config[key] <= high:
            raise ValueError(f"{key} must be an integer between {low} and {high}")
    if config["self_play_mode"] not in ("auto", "sequential", "batched"):
        raise ValueError("self_play_mode must be auto, sequential or batched")
    if config["evaluation_mode"] not in ("auto", "sequential", "batched"):
        raise ValueError("evaluation_mode must be auto, sequential or batched")
    if config["evaluation_inference_backend"] not in ("pytorch", "training"):
        raise ValueError("evaluation_inference_backend must be pytorch or training")
    if config["evaluation_opponent"] not in ("shield_bearer", "shield_maiden", "jarl", "champion"):
        raise ValueError(
            "evaluation_opponent must be shield_bearer, shield_maiden, jarl or champion"
        )
    if config["evaluation_games"] % 2:
        raise ValueError("evaluation_games must be even so the model plays both sides")
    for key, minimum, maximum in (
        ("learning_rate", 1e-7, 1.0),
        ("weight_decay", 0.0, 1.0),
        ("momentum", 0.0, 1.0),
        ("exploration", 0.0, 20.0),
        ("temperature", 0.01, 10.0),
        ("dirichlet_alpha", 0.001, 100.0),
        ("dirichlet_fraction", 0.0, 1.0),
        ("mcts_time_limit", 0.001, 300.0),
        ("evaluation_time_limit", 0.001, 300.0),
        ("evaluation_jarl_time_limit", 0.001, 300.0),
        ("champion_confidence", 0.8, 0.9999),
        ("champion_min_improvement", 0.0, 0.49),
    ):
        value = float(config[key])
        if not math.isfinite(value) or not minimum <= value <= maximum:
            raise ValueError(f"{key} must be between {minimum} and {maximum}")
        config[key] = value
    if config["optimizer"] not in ("adamw", "adam", "sgd", SCHEDULE_FREE):
        raise ValueError("optimizer must be adamw, adam, sgd, or adamw_schedulefree")
    if config["optimizer"] == SCHEDULE_FREE and config["adaptation"] != "off":
        raise ValueError(SCHEDULE_FREE_ADAPTATION_ERROR)
    if type(config["save_all_best"]) is not bool:
        raise ValueError("save_all_best must be a boolean")
    if type(config["seed"]) is not int or not 0 <= config["seed"] < 2**32:
        raise ValueError("seed must be an unsigned 32-bit integer")
    if not isinstance(config["output_dir"], str) or not config["output_dir"]:
        raise ValueError("output_dir must be a nonempty path")
    if not isinstance(config["model_name"], str) or not 1 <= len(config["model_name"]) <= 100:
        raise ValueError("model_name must contain 1–100 characters")
    if config["inference_cache_mode"] not in ("off", "exact"):
        raise ValueError("inference_cache_mode must be off or exact")
    validate_mode(config["training_symmetry_mode"])
    config.update(normalize_adaptation(config, resolved=resolved))
    normalize_inference(config)
    return config


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def compact_progress(state: dict) -> dict:
    """Bound each stdout event independently of history, games, models and replay.

    Full callbacks and atomic progress/result JSON remain the polling contract.
    Local and remote subprocess logs use this projection only.
    """
    if isinstance(state.get("trial_progress"), dict):
        return {"trial": state.get("trial"), **compact_progress(state["trial_progress"])}
    result = {
        key: state[key]
        for key in (
            "status",
            "stage",
            "iteration",
            "total_iterations",
            "games_generated",
            "self_play_games",
            "game",
            "epoch",
            "policy_loss",
            "value_loss",
            "duration",
            "error",
            "checkpoint",
            "completed_trials",
            "trials_requested",
            "best_score",
            "phase_started_at",
            "phase_elapsed",
            "iteration_started_at",
            "iteration_elapsed",
            "started_at",
            "updated_at",
            "last_progress_at",
            "last_progress_seconds",
            "revision",
            "phase_progress",
            "adaptation",
            "optimizer",
        )
        if key in state
    }
    metrics = state.get("metrics", {})
    for key in ("policy_loss", "value_loss", "updates", "replay_positions"):
        if key in metrics:
            result[key] = metrics[key]
    evaluation = metrics.get("evaluation")
    if evaluation:
        result["evaluation"] = {
            key: evaluation[key]
            for key in (
                "iteration",
                "opponent",
                "opponent_identity",
                "score",
                "wins",
                "losses",
                "draws",
                "truncated",
                "duration",
                "promoted",
                "reason",
                "score_lower_bound",
                "champion_iteration",
            )
            if key in evaluation
        }
    performance = state.get("performance", metrics.get("performance"))
    if performance:
        result["performance"] = {
            key: value
            for key, value in performance.items()
            if type(value) in (int, float, str, bool)
        }
    # A short decision tail for subprocess logs; the full bounded history stays in
    # the retained progress file so a one-line log never grows with the run.
    events = state.get("adaptation_events")
    if isinstance(events, list) and events:
        result["adaptation_events"] = events[-5:]
    return result


def self_play(
    model: PolicyValueNet,
    config: dict,
    *,
    seed: int,
    should_stop: Callable[[], bool] | None = None,
    inference_metrics=None,
    backend: InferenceBackend | None = None,
    cache: ExactInferenceCache | None = None,
    monitor: ProgressSink | None = None,
) -> tuple[list[dict], dict]:
    """Generate legal MCTS policy targets and final defender-perspective returns."""
    if backend is None and config.get("inference_backend") == "openvino":
        owned = create_inference(model, config)
        try:
            owned.refresh()
            return self_play(
                model,
                config,
                seed=seed,
                should_stop=should_stop,
                inference_metrics=inference_metrics,
                backend=owned,
                cache=cache,
                monitor=monitor,
            )
        finally:
            owned.close()
    if cache is None and config.get("inference_cache_mode") == "exact":
        cache = ExactInferenceCache(config["inference_cache_capacity"])
    rng = random.Random(seed)
    state = new_game(config["ruleset"])
    examples = []
    model.eval()
    started = time.monotonic()
    simulations = shortcuts = cutoffs = 0
    neural_requests = prediction_reuses = 0
    for ply in range(config["max_game_plies"]):
        if should_stop and should_stop():
            raise InterruptedError("Training was cancelled")
        if state.winner:
            break
        moves = legal_moves(state)
        search_config = {
            "_model": model,
            "_inference_cache": cache,
            "_mixed_precision": bool(config["mixed_precision"])
            and next(model.parameters()).device.type == "cuda",
            "_inference_backend": backend
            if config.get("inference_backend") == "openvino"
            else None,
            "iterations": config["mcts_iterations"],
            "time_limit": config["mcts_time_limit"],
            "seed": seed + ply,
            "exploration": config["exploration"],
            "rollout_depth": 0,
            "dirichlet_alpha": config["dirichlet_alpha"],
            "dirichlet_fraction": config["dirichlet_fraction"],
        }
        details: dict = {}
        policy = mcts_policy(
            state,
            search_config,
            diagnostics=details,
            should_stop=should_stop,
            inference_metrics=inference_metrics,
        )
        simulations += details.get("simulations", 0)
        shortcuts += details.get("immediate_win_shortcuts", 0)
        cutoffs += details.get("deadline_cutoffs", 0)
        neural_requests += details.get("mcts_neural_requests", 0)
        prediction_reuses += details.get("mcts_node_prediction_reuses", 0)
        if monitor is not None:
            monitor.tick(positions=1, simulations=details.get("simulations", 0))
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
        options = list(policy)
        # Exploration decreases after the opening, retaining seeded reproducibility.
        temperature = config["temperature"] if ply < 16 else max(0.1, config["temperature"] / 4)
        weights = [policy[move] ** (1 / temperature) for move in options]
        move = rng.choices(options, weights=weights, k=1)[0]
        state = apply_move(state, move)
    winner = state.winner
    # Truncation is not a rules draw. Bootstrap from the current value network.
    if winner is not None:
        value = {"attackers": -1.0, "defenders": 1.0, "draw": 0.0}[winner]
    elif (
        cache is not None
        or (config["mixed_precision"] and next(model.parameters()).is_cuda)
        or (backend is not None and config.get("inference_backend") == "openvino")
    ):
        from .inference import policy_value_batch

        value = policy_value_batch(
            [state],
            model,
            mixed_precision=bool(config["mixed_precision"]),
            metrics=inference_metrics,
            backend=backend,
            cache=cache,
        )[0][1]
    elif inference_metrics is not None and legal_moves(state):
        inference_metrics.requests += 1
        inference_metrics.cache_misses += 1
        with inference_metrics.measure(1, device=next(model.parameters()).device):
            value = policy_value(state, model=model)[1]
    else:
        value = policy_value(state, model=model)[1]
    for example in examples:
        example["value"] = value
    return examples, {
        "winner": winner,
        "outcome": "completed" if winner else "truncated",
        "reason": state.reason or "training_ply_limit",
        "bootstrap_value": value if winner is None else None,
        "plies": len(examples),
        "simulations": simulations,
        "requested_simulations": len(examples) * config["mcts_iterations"],
        "immediate_win_shortcuts": shortcuts,
        "search_deadline_cutoffs": cutoffs,
        "mcts_neural_requests": neural_requests,
        "mcts_node_prediction_reuses": prediction_reuses,
        "duration": time.monotonic() - started,
    }


def self_play_execution_mode(config: dict, device) -> str:
    """Resolve ``self_play_mode`` to ``sequential`` or ``batched``.

    ``auto`` batches on CUDA and with the OpenVINO backend and stays sequential
    on CPU/MPS. Worker precedence is separate: an explicit ``sequential`` phase
    never uses the pool, while ``batched`` and ``auto`` use it whenever
    ``search_workers`` is positive.
    """
    mode = config["self_play_mode"]
    if mode != "auto":
        return mode
    return (
        "batched"
        if device.type == "cuda" or config.get("inference_backend") == "openvino"
        else "sequential"
    )


def self_play_uses_pool(config: dict) -> bool:
    """Whether self-play generation should run on the worker pool."""
    return bool(config.get("search_workers", 0)) and config["self_play_mode"] != "sequential"


def evaluation_uses_pool(config: dict) -> bool:
    """Whether model evaluation should run on the worker pool."""
    return (
        bool(config.get("search_workers", 0))
        and config.get("evaluation_mode", "auto") != "sequential"
    )


def generate_self_play(
    model: PolicyValueNet,
    config: dict,
    *,
    seed: int,
    should_stop: Callable[[], bool] | None = None,
    progress: Callable[[dict], None] | None = None,
    on_game: Callable[[int, list[dict], dict], None] | None = None,
    measure_cuda: bool = False,
    retain_examples: bool = True,
    backend: InferenceBackend | None = None,
    search_pool=None,
    monitor: ProgressSink | None = None,
) -> tuple[list[tuple[list[dict], dict]], dict]:
    """Generate an iteration's games, retaining a sequential reference path.

    Every game uses the original seed + game-index derivation. Ordered replay
    callbacks are committed as soon as a completed prefix becomes available.
    CUDA activity events are opt-in for benchmarks; ordinary training records
    host inference timing and allocator peaks without per-batch event overhead.
    """
    # Standalone generation/benchmarks must honor explicit backend selection too.
    if backend is None and config.get("inference_backend") == "openvino":
        owned = create_inference(model, config)
        try:
            refresh = owned.refresh()
            games, performance = generate_self_play(
                model,
                config,
                seed=seed,
                should_stop=should_stop,
                progress=progress,
                on_game=on_game,
                measure_cuda=measure_cuda,
                retain_examples=retain_examples,
                backend=owned,
                search_pool=search_pool,
                monitor=monitor,
            )
            performance.update(refresh)
            return games, performance
        finally:
            owned.close()

    if self_play_uses_pool(config):
        from .parallel_search import parallel_self_play

        return parallel_self_play(
            model,
            config,
            seed=seed,
            should_stop=should_stop,
            progress=progress,
            on_game=on_game,
            measure_cuda=measure_cuda,
            retain_examples=retain_examples,
            backend=backend,
            search_pool=search_pool,
            monitor=monitor,
        )

    from .self_play import SelfPlayMetrics, batched_self_play

    device = next(model.parameters()).device
    mode = self_play_execution_mode(config, device)
    concurrency = min(config["games"], config["self_play_concurrency"]) if mode == "batched" else 1
    cache = (
        ExactInferenceCache(config["inference_cache_capacity"])
        if config.get("inference_cache_mode") == "exact"
        else None
    )
    metrics = SelfPlayMetrics(model, mode, concurrency, measure_cuda=measure_cuda)
    metrics.backend_info["inference_cache_mode"] = config.get("inference_cache_mode", "off")
    if backend is not None:
        metrics.backend_info.update(backend.info)
    if mode == "batched":
        if progress:
            progress({"game": 0, "performance": metrics.snapshot()})
        games = batched_self_play(
            model,
            config,
            seed=seed,
            metrics=metrics,
            should_stop=should_stop,
            progress=progress,
            on_game=on_game,
            retain_examples=retain_examples,
            backend=backend,
            cache=cache,
            monitor=monitor,
        )
    else:
        games = []
        if monitor is not None:
            monitor.counts(games_total=config["games"], games_active=1)
        for index in range(config["games"]):
            if should_stop and should_stop():
                raise InterruptedError("Training was cancelled")
            if progress:
                progress({"game": index + 1, "performance": metrics.snapshot()})
            wall_started = time.monotonic()
            cpu_started = time.process_time()
            inference_wall = metrics.inference.host_seconds
            inference_cpu = metrics.inference.cpu_seconds
            examples, result = self_play(
                model,
                config,
                seed=seed + index,
                should_stop=should_stop,
                inference_metrics=metrics.inference,
                backend=backend,
                cache=cache,
                monitor=monitor,
            )
            metrics.cpu_search_seconds += max(
                0.0,
                time.process_time() - cpu_started - (metrics.inference.cpu_seconds - inference_cpu),
            )
            metrics.search_traversal_wall_seconds += max(
                0.0,
                time.monotonic() - wall_started - (metrics.inference.host_seconds - inference_wall),
            )
            games.append((examples if retain_examples else [], result))
            metrics.record(result)
            if monitor is not None:
                # One completion per game; the next game starts immediately.
                monitor.tick(games_completed=1)
                monitor.counts(games_active=0 if index + 1 == config["games"] else 1)
            if on_game:
                on_game(index, examples, result)
            if progress:
                progress({"game": index + 1, "performance": metrics.snapshot()})
    return games, metrics.snapshot(finalize_cuda=measure_cuda)


def _make_optimizer(model: PolicyValueNet, config: dict):
    arguments = {"lr": config["learning_rate"], "weight_decay": config["weight_decay"]}
    if config["optimizer"] == SCHEDULE_FREE:
        try:
            from schedulefree import AdamWScheduleFree
        except ImportError as error:
            raise ValueError(SCHEDULE_FREE_DEPENDENCY_ERROR) from error
        # 1.4.1 has no inner momentum (equivalent to the newer API's default 0).
        # Use the official implementation and its remaining published defaults.
        return AdamWScheduleFree(
            model.parameters(), warmup_steps=config["schedulefree_warmup_steps"], **arguments
        )
    if config["optimizer"] == "sgd":
        return torch.optim.SGD(model.parameters(), momentum=config["momentum"], **arguments)
    if config["optimizer"] == "adam":
        return torch.optim.Adam(model.parameters(), **arguments)
    return torch.optim.AdamW(model.parameters(), **arguments)


def _set_optimizer_learning_rate(optimizer: torch.optim.Optimizer, value: float) -> None:
    """Update the learning rate in place, preserving parameter groups and moments.

    Rebuilding the optimizer would discard accumulated Adam moments and SGD
    momentum, so adaptation only writes the existing parameter groups.
    """
    OptimizerLifecycle(optimizer).set_base_learning_rate(value)


def optimize_replay(
    model: PolicyValueNet,
    optimizer: torch.optim.Optimizer,
    scaler,
    replay: list[dict],
    config: dict,
    device: torch.device,
    mixed: bool,
    rng: random.Random,
    augment_rng: random.Random,
    *,
    should_stop: Callable[[], bool] | None = None,
    on_epoch: Callable[[int], None] | None = None,
    monitor: ProgressSink | None = None,
) -> dict:
    """One iteration's optimizer updates over shuffled replay minibatches.

    Replay is never modified. ``rng`` shuffles; ``augment_rng`` is consulted only
    in ``random_d4`` mode (one uniform D4 draw per presented example), so OFF
    consumes no extra random values and keeps its exact baseline stream.
    """
    lifecycle = OptimizerLifecycle(optimizer)
    policy_losses: list[float] = []
    value_losses: list[float] = []
    updates = presented = 0
    loss_sum = value_sum = 0.0
    augmenting = config["training_symmetry_mode"] == "random_d4"
    augmentation = AugmentationStats()
    batches_per_epoch = math.ceil(len(replay) / config["batch_size"]) if replay else 0
    if monitor is not None:
        monitor.counts(
            optimizer_total_steps=config["epochs"] * batches_per_epoch,
            epoch=0,
        )
    with lifecycle.training_parameters(model):
        for epoch in range(config["epochs"]):
            order = list(range(len(replay)))
            rng.shuffle(order)
            for start in range(0, len(order), config["batch_size"]):
                if should_stop and should_stop():
                    raise InterruptedError("Training was cancelled")
                batch = [replay[index] for index in order[start : start + config["batch_size"]]]
                symmetries = sample_symmetries(augment_rng, len(batch)) if augmenting else None
                inputs, target_policy, legal_mask, values = collate_batch(
                    batch, device, symmetries, augmentation
                )
                presented += len(batch)
                optimizer.zero_grad(set_to_none=True)
                with torch.autocast(device_type=device.type, enabled=mixed):
                    logits, predicted = model(inputs)
                    logits = logits.float().masked_fill(~legal_mask, -1e9)
                    policy_loss = -(target_policy * F.log_softmax(logits, dim=1)).sum(dim=1).mean()
                    value_loss = F.mse_loss(predicted.float(), values)
                    loss = policy_loss + value_loss
                if not torch.isfinite(loss):
                    raise ValueError("Non-finite training loss; reduce learning rate")
                scaler.scale(loss).backward()
                scaler.unscale_(optimizer)
                norm = torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=5.0)
                if lifecycle.schedule_free and not mixed and not torch.isfinite(norm):
                    raise ValueError("Non-finite training gradients; reduce learning rate")
                steps_before = lifecycle.completed_steps() if lifecycle.schedule_free else 0
                scaler.step(optimizer)
                scaler.update()
                policy_losses.append(float(policy_loss.detach()))
                value_losses.append(float(value_loss.detach()))
                completed_update = (
                    lifecycle.completed_steps() > steps_before if lifecycle.schedule_free else True
                )
                updates += int(completed_update)
                if monitor is not None:
                    # Completed-step counter plus the running means of the losses the
                    # dashboard shows; the authoritative means are still computed
                    # from the same lists after the phase.
                    loss_sum += policy_losses[-1]
                    value_sum += value_losses[-1]
                    monitor.tick(optimizer_steps=int(completed_update))
                    attempts = len(policy_losses)
                    monitor.counts(policy_loss=loss_sum / attempts, value_loss=value_sum / attempts)
            if monitor is not None:
                monitor.counts(epoch=epoch + 1)
            if on_epoch:
                on_epoch(epoch + 1)
    return {
        "policy_losses": policy_losses,
        "value_losses": value_losses,
        "updates": updates,
        "presented": presented,
        "augmentation": augmentation,
    }


def train(
    configuration: dict,
    progress: Callable[[dict], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    *,
    trial_mode: bool = False,
    evaluation_seed: int | None = None,
    expected_source: dict | None = None,
) -> dict:
    """Train from scratch or resume to the configured *total* iteration count.

    `resume` names a safe checkpoint. Saved optimizer, replay, random-generator
    state and iteration are restored; ruleset and network architecture cannot
    change. A callback can monitor progress or raise Optuna TrialPruned.
    """
    if configuration.get("resume") and "output_dir" not in configuration:
        raise ValueError("Resume requires a new output_dir to preserve the source training files")
    # Monitoring starts before any expensive initialization. Loading a
    # checkpoint, building the model and moving it to the device can take a long
    # time, and a job that is already running must never look like a dead worker.
    # The explicit inputs are validated first so an invalid configuration still
    # fails before anything is written. A resume's effective evaluation and
    # learning-rate settings are only known after its checkpoint is loaded, so
    # the configuration-dependent adaptation checks are deferred until then.
    resuming = bool(configuration.get("resume"))
    provisional = normalize_config(configuration, resolved=not resuming)
    output = Path(provisional["output_dir"]).resolve()
    if provisional.get("resume") and (output / "last.pt" == Path(provisional["resume"]).resolve()):
        raise ValueError("Resume output_dir must preserve the source checkpoint path")
    started = time.monotonic()
    publish_lock = threading.Lock()
    device: torch.device | None = None
    job_games = 0
    baseline: int | None = 0
    state: dict = {
        "status": "running",
        "total_iterations": provisional["iterations"],
        "device": str(provisional["device"]),
        "mixed_precision": bool(provisional["mixed_precision"]),
        "checkpoint": None,
        "metrics": {},
        "history": [],
        "best_models": [],
        "evaluation_selection": {},
    }

    def write_snapshot(heartbeat: bool, changes: dict) -> None:
        """Serialize one monitoring snapshot and publish it.

        ``heartbeat`` republishes the current state with refreshed timings from
        the monitor thread. It writes only the bounded heartbeat projection
        (never the retained history) and skips the GPU query, so no CUDA call
        runs off the training thread. It runs during initialization too, where
        no device, model or optimizer exists yet.
        """
        with publish_lock:
            state.update(changes)
            if heartbeat and state.get("status") not in {"running", "cancelling", "pending"}:
                # A terminal publication is final. A heartbeat that races the
                # shutdown must not extend its durations or overwrite its status.
                return
            if not heartbeat:
                monitor.published()
            state.update(monitor.snapshot())
            state["self_play_games"] = self_play_statistics(job_games, baseline)
            state["games_generated"] = state["self_play_games"]["cumulative"]
            state["duration"] = time.monotonic() - started
            if not heartbeat:
                if device is not None and device.type == "cuda":
                    state["gpu_memory_allocated"] = torch.cuda.memory_allocated(device)
                _write_json(output / "progress.json", state)
            # The bounded file always carries the current revision, so a reader can
            # tell a new publication from a liveness refresh without re-reading the
            # retained history. It stays a bounded projection in both cases.
            _write_json(output / HEARTBEAT_FILENAME, heartbeat_projection(state))
            if progress:
                progress(dict(state))

    monitor = TrainingMonitor(
        lambda **flags: write_snapshot(bool(flags.get("heartbeat")), {}), phase="starting"
    )

    def publish(**changes):
        """Publish a real progress event from the training thread."""
        if optimizer is not None:
            live_optimizer = OptimizerLifecycle(optimizer)
            if live_optimizer.schedule_free:
                changes["optimizer"] = live_optimizer.diagnostics()
        write_snapshot(False, changes)

    def current_metadata():
        return {
            "name": config["model_name"],
            "version": completed,
            "training_config": config,
            "metrics": metrics,
            "evaluation": metrics.get("evaluation", {}),
            "self_play_games": self_play_statistics(job_games, baseline),
        }

    def checkpoint_now(path: Path):
        # Only reached once the training state exists; see ``initialized``.
        assert model is not None and optimizer is not None
        assert selection is not None and scaler is not None and device is not None
        checkpoint_lifecycle = OptimizerLifecycle(optimizer)
        checkpoint_lifecycle.inference()
        if checkpoint_lifecycle.schedule_free:
            state["optimizer"] = checkpoint_lifecycle.diagnostics()
        extra = {
            "iteration": completed,
            "games_generated": self_play_statistics(job_games, baseline)["cumulative"],
            "replay": replay,
            "best_score": best_score,
            "best_snapshot": best_snapshot,
            "selected_score": selected_score,
            "selected_iteration": selected_iteration,
            "selected_source": selected_source,
            "source_best_score": source_best_score,
            "candidate_score": candidate_score,
            "candidate_iteration": candidate_iteration,
            "training_state_version": 2,
            "evaluation_state": selection.data,
            "history": history,
            "best_models": best_models,
            "python_rng": rng.getstate(),
            "augmentation_rng": augment_rng.getstate(),
            "torch_rng": torch.get_rng_state(),
            "cuda_rng": torch.cuda.get_rng_state_all() if device.type == "cuda" else [],
            "grad_scaler": scaler.state_dict(),
        }
        if controller is not None:
            extra["adaptation_state"] = controller.state_dict()
        save_checkpoint(path, model, current_metadata(), optimizer, extra)

    def stopped() -> None:
        """Honor a cancellation request once the training state can be checkpointed."""
        if should_stop and should_stop():
            raise InterruptedError("Training was cancelled")

    # Initialization state, filled in as each slow step completes. ``initialized``
    # marks the point where a checkpoint can be written and a terminal state
    # reported with full training metadata.
    config = provisional
    saved_inputs: dict = {}
    resume_model = None
    source: dict = {}
    completed = 0
    replay: list[dict] = []
    best_score = -1.0
    best_snapshot: dict | None = None
    # The selected checkpoint's own score, iteration and provenance. Unlike the
    # legacy shield_bearer ``best_score``, this tracks every opponent and is the
    # quantity the tuning objective and pruner use.
    selected_score = -1.0
    selected_iteration: int | None = None
    selected_source = "none"
    source_best_score = -1.0
    candidate_score = -1.0
    candidate_iteration = None
    metrics: dict = {}
    history: list[dict] = []
    recorded_parameters: dict | None = None
    parameter_iteration: int | None = None
    iteration_parameters: dict = {}
    interval_learning_rate: float | None = None
    best_models: list[dict] = []
    produced_best = False
    resume_payload: dict | None = None
    saved_config: dict = {}
    #: Set when a resume explicitly requested a different learning rate, so the
    #: controller can honour it instead of silently keeping the saved rate.
    explicit_learning_rate: float | None = None
    extra: dict = {}
    selection: EvaluationSelection | None = None
    scaler: torch.amp.GradScaler | None = None
    optimizer: torch.optim.Optimizer | None = None
    controller = None
    model: PolicyValueNet | None = None
    initialized = False
    backend = None
    checkpoint = output / "last.pt"
    best_checkpoint = output / "best.pt"
    search_pool = None

    def append_history(point: dict) -> None:
        """Commit provenance once, including recovery after completed optimization.

        The first completed iteration of every execution establishes its own
        settings even when they match the source. Polls/checkpoint writes never
        create events, and an incomplete iteration cannot consume the boundary.
        """
        nonlocal recorded_parameters, parameter_iteration
        if iteration_parameters and iteration_parameters != recorded_parameters:
            point["training_parameters"] = iteration_parameters
            recorded_parameters = iteration_parameters
            parameter_iteration = point["iteration"]
        if parameter_iteration is not None:
            point["training_parameters_iteration"] = parameter_iteration
        history.append(point)

    monitor.start()
    try:
        output.mkdir(parents=True, exist_ok=True)
        publish()
        if provisional.get("resume"):
            monitor.phase("loading_checkpoint")
            # Publish each initialization stage so the retained progress file
            # names it immediately, not only through the heartbeat overlay.
            publish()
            if expected_source is not None:
                from .training_identity import load_source_checkpoint

                resume_model, source, actual_identity = load_source_checkpoint(
                    provisional["resume"], load_checkpoint
                )
                if actual_identity != expected_source:
                    raise ValueError(
                        "The source checkpoint content changed after the study was created"
                    )
            else:
                resume_model, source = load_checkpoint(provisional["resume"])
            saved_inputs = source["metadata"].get("training_config", {})
            if "training_symmetry_mode" not in saved_inputs:
                # Checkpoints written before D4 augmentation existed omitted this
                # setting. Keep their historical OFF behavior even though new jobs
                # now default to random D4; an explicit override still wins below.
                saved_inputs = {**saved_inputs, "training_symmetry_mode": "off"}
            # CLI resumes inherit saved settings too; only explicit inputs override them.
            config = normalize_config({**saved_inputs, **configuration})
        # Building the model can take a while; the heartbeat reports this stage
        # without a retained publication, so no counter is published before the
        # training state it would describe exists.
        monitor.phase("initializing")
        torch.set_num_threads(config["cpu_threads"])
        device = resolve_device(config["device"])
        torch.manual_seed(config["seed"])
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(config["seed"])
        rng = random.Random(config["seed"])
        # Dedicated stream: OFF never draws from it, and neither replay shuffling
        # nor self-play scheduling can shift augmentation choices (or vice versa).
        augment_rng = augmentation_rng(config["seed"], 0)
        model = (
            resume_model
            if resume_model is not None
            else PolicyValueNet(config["ruleset"], config["channels"], config["blocks"])
        ).to(device)
        optimizer = _make_optimizer(model, config)
        if config.get("resume"):
            monitor.phase("restoring_state")
            publish()
            payload = source
            resume_payload = payload
            if model.config != {
                "ruleset": config["ruleset"],
                "channels": config["channels"],
                "blocks": config["blocks"],
            }:
                raise ValueError("Resume ruleset and architecture must match the checkpoint")
            saved_config = payload["metadata"].get("training_config", {})
            if saved_config.get("optimizer") != config["optimizer"]:
                raise ValueError("Resume optimizer must match the checkpoint")
            if "seed" in saved_config and config["seed"] != saved_config["seed"]:
                raise ValueError("Resume seed must match the checkpoint")
            # Distinguish an unchanged resume from an explicit override. The API
            # merges the saved inputs into the request, so the presence of the key
            # alone is not enough; the value must differ from the checkpoint.
            requested_learning_rate = float(config["learning_rate"])
            if requested_learning_rate != float(
                saved_config.get("learning_rate", requested_learning_rate)
            ):
                explicit_learning_rate = requested_learning_rate
            optimizer = _make_optimizer(model, config)
            if payload.get("optimizer_state") is None:
                raise ValueError(
                    "This checkpoint has no optimizer state and cannot resume training"
                )
            optimizer.load_state_dict(payload["optimizer_state"])
            lifecycle = OptimizerLifecycle(optimizer)
            if lifecycle.schedule_free:
                for group in optimizer.param_groups:
                    saved_warmup = group["warmup_steps"]
                    if config["schedulefree_warmup_steps"] != saved_warmup:
                        if group["k"] > 0:
                            raise ValueError(
                                "schedulefree_warmup_steps cannot change after optimizer updates"
                            )
                        group["warmup_steps"] = config["schedulefree_warmup_steps"]
                config["schedulefree_warmup_steps"] = optimizer.param_groups[0]["warmup_steps"]
            # A saved train-mode checkpoint must first be interpreted in its
            # recorded mode before switching to inference; never reset flags.
            lifecycle.inference()
            # Loading optimizer state restores its parameter groups too. Apply each
            # explicit override afterwards, while preserving accumulated moments and
            # momentum buffers. Record the effective values even for older snapshots
            # whose metadata did not reflect their restored optimizer settings.
            settings = {"learning_rate": "lr", "weight_decay": "weight_decay"}
            if config["optimizer"] == "sgd":
                settings["momentum"] = "momentum"
            for name, parameter in settings.items():
                for group in optimizer.param_groups:
                    if name in configuration:
                        group[parameter] = config[name]
                config[name] = float(optimizer.param_groups[0][parameter])
            extra = payload.get("extra", {})
            if extra.get("training_state_version", 1) not in (1, 2):
                raise ValueError("Unsupported training checkpoint state version")
            history = restore_parameter_history(extra.get("history", []))
            best_models = extra.get("best_models", [])
            completed = int(extra.get("iteration", 0))
            baseline = checkpoint_cumulative(payload)
            replay = extra.get("replay", [])[-config["replay_buffer_size"] :]
            best_score = float(extra.get("best_score", -1))
            best_snapshot = extra.get("best_snapshot")
            if best_score >= 0 and not best_snapshot:
                raise ValueError("Resume checkpoint is missing its portable best-model snapshot")
            if "python_rng" in extra:
                rng.setstate(extra["python_rng"])
            if "augmentation_rng" in extra:
                augment_rng.setstate(extra["augmentation_rng"])
            else:
                # Legacy checkpoints: reproducible derivation from seed and progress.
                augment_rng = augmentation_rng(config["seed"], completed)
            if "torch_rng" in extra:
                torch.set_rng_state(extra["torch_rng"].cpu())
            if device.type == "cuda" and extra.get("cuda_rng"):
                torch.cuda.set_rng_state_all(extra["cuda_rng"])
            metrics = payload["metadata"].get("metrics", {})
            if completed >= config["iterations"]:
                raise ValueError("Resume iterations must exceed the saved completed iteration")
            # The restored state is part of initialization, not a separate phase.
            monitor.phase("initializing")
            publish()
        lifecycle = OptimizerLifecycle(optimizer)
        lifecycle.inference()
        if lifecycle.schedule_free:
            state["optimizer"] = lifecycle.diagnostics()
        selection = EvaluationSelection(extra, saved_config, config, metrics.get("evaluation"))
        if evaluation_seed is not None:
            selection.context["seed"] = evaluation_seed
            from .training_evaluation import context_identity

            selection.identity = context_identity(selection.context)
            selection.data["active_identity"] = selection.identity
        # Keep legacy baseline fields in their original scale. Other benchmarks and
        # champion promotion never overwrite these portable historical values.
        legacy_identity = selection.data.get("legacy_identity")
        if legacy_identity is None and config["evaluation_opponent"] == "shield_bearer":
            legacy_identity = selection.identity
            selection.data["legacy_identity"] = legacy_identity
        if config["evaluation_opponent"] == "champion":
            selection.initialize_champion(
                model,
                {
                    "name": config["model_name"],
                    "version": completed,
                    "training_config": config,
                    "metrics": {},
                    "evaluation": {},
                    "self_play_games": self_play_statistics(job_games, baseline),
                },
                best_snapshot,
            )
        # The selected checkpoint and its reported score must describe the same
        # model state. A resumed checkpoint may already carry a historical
        # benchmark result; that inherited score stays authoritative until a
        # candidate actually replaces the selected snapshot.
        initial_selected = selection.selected_snapshot()
        source_best_score = selection.selected_score()
        selected_score = source_best_score
        selected_iteration = selection.selected_iteration()
        selected_source = "inherited" if initial_selected is not None else "none"
        selected = selection.selected_snapshot() or best_snapshot
        if selected:
            save_checkpoint(best_checkpoint, frozen_model(model, selected), selected["metadata"])
        # Optional online learning-rate adaptation. The controller owns the
        # effective rate only while it is enabled; a fresh run starts from the
        # configured learning_rate, which validation guarantees is a candidate.
        controller = resolve_controller(
            config,
            saved_state=extra.get("adaptation_state"),
            evaluation_identity=selection.identity,
            active_champion_identity=selection.champion_identity(),
            seed=config["seed"] + 900000,
            resume=bool(config.get("resume")),
            override_learning_rate=explicit_learning_rate,
            # The iteration the checkpoint completed, so an explicit override or a
            # legacy migration starts its conservative iteration dwell here.
            current_iteration=completed,
        )
        if controller is not None:
            _set_optimizer_learning_rate(optimizer, controller.effective_learning_rate)
            config["learning_rate"] = controller.effective_learning_rate
            state["adaptation"] = controller.summary()
            state["adaptation_events"] = list(controller.events[-ADAPTATION_EVENT_TAIL:])
        elif resume_payload is not None and saved_config.get("adaptation", "off") != "off":
            # Adaptation was disabled on resume. The optimizer keeps the
            # checkpoint's last effective rate; an explicit learning_rate override
            # was already applied by the normal resume path above.
            state["adaptation"] = {
                "algorithm": "off",
                "effective_learning_rate": float(optimizer.param_groups[0]["lr"]),
                "reset_reason": "disabled_on_resume",
            }
        mixed = bool(config["mixed_precision"] and device.type == "cuda")
        scaler = torch.amp.GradScaler("cuda", enabled=mixed)
        if resume_payload and resume_payload["extra"].get("grad_scaler"):
            scaler.load_state_dict(resume_payload["extra"]["grad_scaler"])
        state.update(
            {
                "iteration": completed,
                "total_iterations": config["iterations"],
                "games_generated": baseline,
                "self_play_games": self_play_statistics(job_games, baseline),
                "device": str(device),
                "mixed_precision": mixed,
                "checkpoint": None,
                "metrics": metrics,
                "history": history,
                "best_models": best_models,
                "evaluation_selection": selection.summary(),
                # The score, iteration and provenance of the selected checkpoint.
                "best_score": best_score,
                "selected_score": selected_score,
                "selected_iteration": selected_iteration,
                "selected_source": selected_source,
                "source_best_score": source_best_score,
            }
        )
        _write_json(output / "config.json", config)
        # From here a checkpoint can be written and a terminal state reported
        # with full training metadata. Publish the completed initialization so a
        # restored history and its optimizer settings are visible before the
        # first self-play game.
        initialized = True
        publish()
        stopped()
        monitor.phase("initializing_inference")
        if self_play_uses_pool(config) or evaluation_uses_pool(config):
            from .parallel_search import SearchPool

            search_pool = SearchPool(config["search_workers"])
        pool_options: dict[str, Any] = (
            {"search_pool": search_pool} if search_pool is not None else {}
        )
        initialization_started = time.monotonic()
        backend = create_inference(model, config)
        initialization_seconds = time.monotonic() - initialization_started
        first_iteration = completed + 1
        publish(**backend.info, inference_initialization_seconds=initialization_seconds)
        for iteration in range(completed + 1, config["iterations"] + 1):
            if should_stop and should_stop():
                raise InterruptedError("Training was cancelled")
            iteration_start = time.monotonic()
            # The rate set at the previous boundary is the one this iteration's
            # self-play, optimization and evaluation actually use. Capture it
            # before any adaptation decision so the reported history attributes
            # the losses to the rate that produced them.
            interval_learning_rate = lifecycle.base_learning_rate()
            iteration_parameters = parameter_snapshot(config)

            def commit_game(index: int, examples: list[dict], result: dict) -> None:
                nonlocal replay, job_games
                replay.extend(examples)
                replay = replay[-config["replay_buffer_size"] :]
                job_games += 1

            # Device/graph preparation can block for seconds on a cold backend
            # (TorchScript/OpenVINO compile); report it as its own phase.
            monitor.phase("preparing", iteration=iteration)
            lifecycle.inference()
            refresh = backend.refresh()
            if should_stop and should_stop():
                raise InterruptedError("Training was cancelled")
            monitor.phase("self_play", iteration=iteration)
            generated_games, performance = generate_self_play(
                model,
                config,
                seed=config["seed"] + iteration * 100000,
                should_stop=should_stop,
                progress=lambda info: publish(iteration=iteration, **info),
                on_game=commit_game,
                retain_examples=False,
                backend=backend,
                **pool_options,
                monitor=monitor,
            )
            performance.update(backend.info)
            performance.update(refresh)
            performance["inference_initialization_seconds"] = (
                initialization_seconds if iteration == first_iteration else 0.0
            )
            generated = [result for _, result in generated_games]
            if not replay:
                raise ValueError("Self-play generated no training examples")
            optimization_started = time.monotonic()
            monitor.phase("optimization", iteration=iteration)
            model.train()
            optimized = optimize_replay(
                model,
                optimizer,
                scaler,
                replay,
                config,
                device,
                mixed,
                rng,
                augment_rng,
                should_stop=should_stop,
                monitor=monitor,
                on_epoch=lambda epoch: publish(epoch=epoch),
            )
            policy_losses, value_losses = optimized["policy_losses"], optimized["value_losses"]
            updates, presented = optimized["updates"], optimized["presented"]
            augmentation = optimized["augmentation"]
            model.eval()
            performance["optimization_seconds"] = time.monotonic() - optimization_started
            summary = augmentation.summary(
                config["training_symmetry_mode"], performance["optimization_seconds"]
            )
            summary["training_exposures"] = presented
            performance.update(
                augmentation_mode=summary["mode"],
                training_exposures=presented,
                augmentation_exposures=summary["exposures"],
                augmentation_identity=summary["identity"],
                augmentation_seconds=summary["seconds"],
                augmentation_optimization_share=summary["optimization_share"],
            )
            try:
                import resource
                import sys

                performance["process_peak_rss_bytes"] = resource.getrusage(
                    resource.RUSAGE_SELF
                ).ru_maxrss * (1 if sys.platform == "darwin" else 1024)
            except ImportError:
                pass
            metrics = {
                "policy_loss": sum(policy_losses) / len(policy_losses),
                "value_loss": sum(value_losses) / len(value_losses),
                "updates": updates,
                "self_play": generated,
                "replay_positions": len(replay),
                "augmentation": summary,
                "performance": performance,
            }
            if lifecycle.schedule_free:
                metrics["optimizer"] = lifecycle.diagnostics()
                state["optimizer"] = metrics["optimizer"]
            # Optimization is complete even if the following evaluation is cancelled.
            # Resume must not repeat these updates merely to retry a comparison.
            completed = iteration
            evaluation_started = time.monotonic()
            if iteration % config["evaluation_frequency"] == 0 or iteration == config["iterations"]:
                monitor.phase("evaluation", iteration=iteration)
                publish(metrics=metrics)
                incumbent = (
                    frozen_model(model, selection.data["champion"]["snapshot"])
                    if config["evaluation_opponent"] == "champion"
                    else None
                )
                metrics["evaluation"] = evaluate_model(
                    model,
                    config,
                    previous=incumbent,
                    # Fixed suite seeds ensure like-for-like benchmark comparisons.
                    # Champion rounds use fresh paired seeds for each candidate.
                    seed=selection.context["seed"]
                    + (iteration * 1000000 if incumbent is not None else 0),
                    should_stop=should_stop,
                    **pool_options,
                    monitor=monitor,
                )
            performance["evaluation_seconds"] = time.monotonic() - evaluation_started
            if device.type == "cuda":
                performance.update(
                    gpu_peak_allocated_bytes=torch.cuda.max_memory_allocated(device),
                    gpu_peak_reserved_bytes=torch.cuda.max_memory_reserved(device),
                )
            completed = iteration
            metrics["iteration_duration"] = time.monotonic() - iteration_start
            performance["iteration_compute_seconds"] = metrics["iteration_duration"]
            performance["iteration_wall_seconds"] = metrics["iteration_duration"]
            evaluation = metrics.get("evaluation")
            record_improvement = False
            candidate_improvement = False
            if evaluation is not None and updates > 0:
                if should_stop and should_stop():
                    raise InterruptedError("Evaluation was cancelled")
                candidate_improvement = trial_mode and evaluation["score"] > candidate_score
                if candidate_improvement:
                    candidate_score = float(evaluation["score"])
                    candidate_iteration = iteration
                record_improvement = selection.accept(model, current_metadata(), evaluation, config)
                produced_best = produced_best or record_improvement
                # Always track the score and iteration of the checkpoint the
                # trainer actually keeps, including an inherited benchmark when
                # no candidate improved. This keeps the tuning objective and the
                # saved best checkpoint describing the same weights.
                selected_score = selection.selected_score()
                selected_iteration = selection.selected_iteration()
                selected_source = selection.selected_source(initial_selected)
                if selection.identity == legacy_identity and record_improvement:
                    best_score = evaluation["score"]
                    best_snapshot = selection.selected_snapshot()
            state["evaluation_selection"] = selection.summary()
            if controller is not None and evaluation is not None and updates > 0:
                # Adapt only at an evaluation boundary, after the just-finished
                # interval is scored. The credited action is the rate that was
                # active during that interval; a changed rate applies from the
                # next iteration. No extra evaluation is performed here.
                adaptation_started = time.monotonic()
                observation, provenance = observation_from_evaluation(evaluation)
                # A Champion promotion is an idempotent transition, not a reset:
                # the promoting observation belongs to the outgoing incumbent, and
                # the controller keeps its learned predictors, dwell counters and
                # effective rate while refreshing only the per-Champion baseline.
                # It is signalled from the selection decision rather than inferred
                # from the score.
                promotion = None
                if config["evaluation_opponent"] == "champion" and evaluation.get("promoted"):
                    promotion = {
                        "iteration": iteration,
                        "champion_iteration": evaluation.get("champion_iteration"),
                        "opponent_identity": evaluation.get("opponent_identity"),
                        # The regime the next observations belong to. Read after
                        # accept() has installed the promoted Champion.
                        "active_champion_identity": selection.champion_identity(),
                    }
                decision = controller.observe(
                    iteration=iteration,
                    score=observation,
                    provenance=provenance,
                    promotion=promotion,
                )
                if decision.get("changed"):
                    _set_optimizer_learning_rate(optimizer, controller.effective_learning_rate)
                    config["learning_rate"] = controller.effective_learning_rate
                performance["adaptation_seconds"] = time.monotonic() - adaptation_started
                state["adaptation"] = controller.summary()
                state["adaptation_events"] = list(controller.events[-ADAPTATION_EVENT_TAIL:])
            point = {
                "iteration": iteration,
                "policy_loss": metrics["policy_loss"],
                "value_loss": metrics["value_loss"],
                "replay_positions": metrics["replay_positions"],
                "updates": metrics["updates"],
                "iteration_duration": metrics["iteration_duration"],
                # The rate this iteration actually trained with.
                "learning_rate": interval_learning_rate,
                # The rate in effect after this iteration's decision, i.e. the one
                # the next interval will use. Equal to ``learning_rate`` when
                # adaptation is off or no change was selected.
                "effective_learning_rate": float(optimizer.param_groups[0]["lr"]),
                "performance": dict(performance),
            }
            if lifecycle.schedule_free:
                point["optimizer"] = lifecycle.diagnostics()
            if "evaluation" in metrics:
                evaluation = metrics["evaluation"]
                point.update(
                    evaluation=deepcopy(evaluation),
                    evaluation_opponent=evaluation["opponent"],
                    opponent_identity=evaluation["opponent_identity"],
                    evaluation_score=evaluation["score"],
                    **{
                        f"evaluation_{key}": evaluation[key]
                        for key in ("wins", "losses", "draws", "truncated")
                    },
                )
            append_history(point)
            # Model selection, checkpoint writes and registry publication are a
            # real phase: they can take seconds and must show as such.
            monitor.phase("checkpoint", iteration=iteration)
            artifact_started = time.monotonic()
            if candidate_improvement:
                checkpoint_now(output / "objective.pt")
            if record_improvement:
                assert evaluation is not None
                selected = selection.selected_snapshot()
                assert selected is not None
                save_checkpoint(
                    best_checkpoint, frozen_model(model, selected), selected["metadata"]
                )
                if config["save_all_best"] and not trial_mode:
                    retained = output / "best" / f"iteration-{iteration:06d}.pt"
                    save_checkpoint(retained, frozen_model(model, selected), selected["metadata"])
                    registry = ModelRegistry(config.get("registry_dir", output.parent / "models"))
                    registered_best = registry.register(retained, name=config["model_name"])
                    best_models.append(
                        {
                            "iteration": iteration,
                            "score": evaluation["score"],
                            "opponent_identity": evaluation["opponent_identity"],
                            "checkpoint": str(retained),
                            "model_id": registered_best["id"],
                        }
                    )
            if iteration % config["checkpoint_frequency"] == 0 or iteration == config["iterations"]:
                checkpoint_now(checkpoint)
            # Preserve exactly the history captured in the checkpoint. Final
            # progress/result metrics can additionally include its own write,
            # which the checkpoint's timing snapshot necessarily predates.
            performance = {
                **performance,
                "artifact_persistence_seconds": time.monotonic() - artifact_started,
                "iteration_wall_seconds": time.monotonic() - iteration_start,
            }
            metrics["performance"] = performance
            monitor.phase("iteration_complete", iteration=iteration)
            publish(
                iteration=iteration,
                policy_loss=metrics["policy_loss"],
                value_loss=metrics["value_loss"],
                checkpoint=str(checkpoint) if checkpoint.exists() else None,
                best_score=best_score,
                selected_score=selected_score,
                selected_iteration=selected_iteration,
                selected_source=selected_source,
                candidate_score=candidate_score,
                candidate_iteration=candidate_iteration,
                metrics=metrics,
                performance=performance,
            )

        # Finalization is a real phase: checkpoint verification, model export and
        # registration can take seconds, so the heartbeat must keep running until
        # the terminal publication has been written. An export or registration
        # failure surfaces through the handler below as a failed job rather than
        # leaving a stalled "finalizing" state.
        monitor.phase("finalizing", iteration=completed)
        publish()
        if not checkpoint.exists():
            checkpoint_now(checkpoint)
        if not best_checkpoint.exists():
            raise ValueError("Training completed without a best-model snapshot")
        registry_root = Path(config.get("registry_dir", output.parent / "models"))
        inference_checkpoint = None
        registered = None
        model_reused = False
        if not trial_mode:
            inference_checkpoint = export_checkpoint(best_checkpoint, output / "inference.pt")
            registered = ModelRegistry(registry_root).register(
                inference_checkpoint, name=config["model_name"]
            )
            # ``reused`` is a transient registration signal, not catalog metadata.
            model_reused = bool(registered.pop("reused", False))
        monitor.phase("completed", iteration=completed)
        publish(
            status="completed",
            checkpoint=str(checkpoint),
            model=registered,
            model_reused=model_reused,
            produced_best=produced_best,
            best_checkpoint=str(best_checkpoint),
            inference_checkpoint=str(inference_checkpoint) if inference_checkpoint else None,
            candidate_score=candidate_score,
            candidate_iteration=candidate_iteration,
            objective_checkpoint=str(output / "objective.pt")
            if candidate_iteration is not None
            else None,
            artifact_policy="trial_local" if trial_mode else "registered",
        )
        result = {**state, "output_dir": str(output), "iterations": completed}
        _write_json(output / "result.json", result)
        return result
    except InterruptedError as error:
        if not initialized:
            # Cancelled before any training state existed: report the terminal
            # state without pretending a checkpoint was written.
            monitor.phase("cancelled")
            publish(status="cancelled")
            result = {**state, "output_dir": str(output), "model": None, "best_checkpoint": None}
            _write_json(output / "result.json", result)
            return result
        assert optimizer is not None
        if completed and (not history or history[-1]["iteration"] < completed):
            append_history(
                {
                    "iteration": completed,
                    "policy_loss": metrics["policy_loss"],
                    "value_loss": metrics["value_loss"],
                    "replay_positions": metrics.get("replay_positions"),
                    "updates": metrics.get("updates"),
                    "learning_rate": interval_learning_rate,
                    "effective_learning_rate": float(optimizer.param_groups[0]["lr"])
                    if interval_learning_rate is not None
                    else None,
                    "performance": metrics.get("performance"),
                    "evaluation_status": "cancelled",
                }
            )
        mode_failure = OptimizerLifecycle(optimizer).mode_failure
        if mode_failure is None:
            checkpoint_now(checkpoint)
        else:
            state["error"] = f"{error}; {mode_failure}"
        monitor.phase("cancelled", iteration=completed)
        publish(
            status="cancelled",
            iteration=completed,
            checkpoint=str(checkpoint) if checkpoint.exists() else None,
        )
        result = {
            **state,
            "output_dir": str(output),
            "model": None,
            "best_checkpoint": str(best_checkpoint) if best_checkpoint.exists() else None,
        }
        _write_json(output / "result.json", result)
        return result
    except Exception as error:
        # TrialPruned also preserves the most recent completed checkpoint.
        status = "pruned" if type(error).__name__ == "TrialPruned" else "failed"
        mode_failure = OptimizerLifecycle(optimizer).mode_failure if optimizer is not None else None
        error_message = f"{error}; {mode_failure}" if mode_failure else str(error)
        if not initialized:
            # A failure during initialization has no checkpoint to preserve, but
            # it must still end as a named terminal state instead of a job that
            # looks like it is still starting.
            monitor.phase(status)
            publish(status=status, error=error_message)
            raise
        assert optimizer is not None
        if completed and (not history or history[-1]["iteration"] < completed):
            append_history(
                {
                    "iteration": completed,
                    "policy_loss": metrics["policy_loss"],
                    "value_loss": metrics["value_loss"],
                    "replay_positions": metrics.get("replay_positions"),
                    "updates": metrics.get("updates"),
                    "learning_rate": interval_learning_rate,
                    "effective_learning_rate": float(optimizer.param_groups[0]["lr"])
                    if interval_learning_rate is not None
                    else None,
                    "performance": metrics.get("performance"),
                    "evaluation_status": status,
                }
            )
        if mode_failure is None:
            checkpoint_now(checkpoint)
        monitor.phase(status, iteration=completed)
        publish(
            status=status,
            error=error_message,
            iteration=completed,
            checkpoint=str(checkpoint) if checkpoint.exists() else None,
        )
        raise
    finally:
        # The terminal publication is the last state any consumer may see; stop
        # the heartbeat so no later snapshot can revive a running status.
        monitor.stop()
        if backend is not None:
            backend.close()
        if search_pool is not None:
            search_pool.close()


def study_identity(
    *,
    resuming: bool,
    resume_payload: dict | None,
    evaluation_base: dict,
    search_space: dict,
    opponents: list[dict],
    sampler: dict,
    pruner: dict,
    source_reference: dict | None = None,
) -> dict:
    """Canonical fingerprint of everything that defines what a study optimizes.

    Two configurations with different identities must never share a study
    database: their trial scores would not be comparable. The trial budget and
    failure budget are deliberately excluded so a study can be extended without
    changing its identity.
    """
    from .training_identity import OPERATIONAL_TRAINING_FIELDS, checkpoint_identity

    source = source_reference or (
        checkpoint_identity(evaluation_base["resume"], resume_payload)
        if resuming and resume_payload
        else None
    )
    fixed = {
        key: value
        for key, value in evaluation_base.items()
        if key not in OPERATIONAL_TRAINING_FIELDS and key not in search_space
    }
    # This setting has no effect on legacy optimizers. Including a new default
    # would invalidate every pre-feature AdamW/Adam/SGD study identity.
    if evaluation_base["optimizer"] != SCHEDULE_FREE:
        fixed.pop("schedulefree_warmup_steps", None)
    import optuna
    from .models import ARCHITECTURE, FORMAT_VERSION

    protocols = []
    for index, entry in enumerate(opponents):
        opponent_config = {
            **evaluation_base,
            "evaluation_opponent": entry["opponent"],
            "seed": evaluation_base["seed"] + 2_000_000 + index,
        }
        if entry["opponent"] == "frozen_model":
            opponent_config["evaluation_frozen_model_id"] = entry["model_id"]
        protocols.append(
            {"weight": entry["weight"], "context": evaluation_context(opponent_config)}
        )
    return {
        "version": 2,
        "mode": "checkpoint" if resuming else "scratch",
        "source": source,
        "training": fixed,
        "search_space": search_space,
        "opponents": opponents,
        "evaluation": evaluation_context(evaluation_base),
        "weighted_evaluation": protocols,
        "runtime": {
            "torch": str(torch.__version__),
            "optuna": optuna.__version__,
            "architecture_format": ARCHITECTURE,
            "checkpoint_format": FORMAT_VERSION,
            "resolved_device": str(resolve_device(evaluation_base["device"])),
            "deterministic_algorithms": torch.are_deterministic_algorithms_enabled(),
            "float32_matmul_precision": torch.get_float32_matmul_precision(),
            "cudnn_deterministic": torch.backends.cudnn.deterministic,
            "cudnn_benchmark": torch.backends.cudnn.benchmark,
        },
        "sampler": {"type": "TPE", "n_startup_trials": 3, **sampler},
        "pruner": {
            "type": "Median",
            "n_startup_trials": 3,
            "n_warmup_steps": 1,
            "interval_steps": 1,
            **pruner,
        },
        "objective": "best_new_candidate_benchmark_then_weighted_aggregate_v2",
        "seed_policy": "source_rng_for_checkpoint;training_seed_plus_trial_for_scratch;fixed_evaluation_seed",
    }


def identity_differences(stored: object, requested: object, prefix: str = "") -> list[str]:
    from .training_identity import differences

    return differences(stored, requested, prefix)


def parameter_importance(trials) -> dict:
    """Bounded descriptive importance, computed once at study finalization.

    Quantile bins avoid an optional scientific stack. This is an exploratory
    univariate association, not fANOVA, a confidence interval or causal evidence.
    """
    if len(trials) < 20:
        return {"method": "binned_variance", "samples": len(trials), "minimum": 20}
    parameters = set.intersection(*(set(trial.params) for trial in trials))
    values = {}
    scores = [float(trial.value) for trial in trials]
    mean = sum(scores) / len(scores)
    variance = sum((score - mean) ** 2 for score in scores)
    if variance <= 0:
        return {"method": "binned_variance", "samples": len(trials), "minimum": 20}
    for name in sorted(parameters):
        ordered = sorted(trials, key=lambda trial: trial.params[name])
        unique = sorted({trial.params[name] for trial in ordered})
        if len(unique) < 2:
            continue
        cuts = sorted({ordered[len(ordered) * index // 4].params[name] for index in range(1, 4)})
        groups: dict[int, list[float]] = {}
        for trial in ordered:
            bucket = sum(trial.params[name] >= cut for cut in cuts)
            groups.setdefault(bucket, []).append(float(trial.value))
        if len(groups) < 2 or any(len(group) < 3 for group in groups.values()):
            continue
        values[name] = (
            sum(len(group) * (sum(group) / len(group) - mean) ** 2 for group in groups.values())
            / variance
        )
    total = sum(values.values())
    return {
        "method": "binned_variance",
        "samples": len(trials),
        "minimum": 20,
        **({"values": {key: value / total for key, value in values.items()}} if total > 0 else {}),
    }


def requested_study_identity(configuration: dict) -> dict:
    """Shared server/worker preflight. No writes to the Optuna database."""
    from .job_studies import (
        DEFAULT_SEARCH_SPACE,
        search_space_config,
        opponents_config,
        sampler_config,
        pruner_config,
    )
    from .training_identity import content_digest

    base = normalize_config(configuration.get("training", {"preset": "smoke"}))
    payload = None
    source_reference = None
    if base.get("resume"):
        from .training_identity import load_source_checkpoint, source_configuration

        _, payload, source_reference = load_source_checkpoint(base["resume"], load_checkpoint)
        base = normalize_config({**source_configuration(payload), **configuration["training"]})
        base["seed"] = (
            payload.get("metadata", {}).get("training_config", {}).get("seed", base["seed"])
        )
    opponents = opponents_config(configuration.get("opponents"))
    frozen = {}
    for entry in opponents:
        if entry["opponent"] != "frozen_model":
            continue
        record = ModelRegistry(base.get("registry_dir", "data/models")).get(entry["model_id"])
        model, _ = load_checkpoint(record["path"])
        if model.ruleset != base["ruleset"]:
            raise ValueError(
                f"Frozen model {entry['model_id']} ruleset does not match training.ruleset"
            )
        frozen[entry["model_id"]] = content_digest(model.state_dict())
    identity = study_identity(
        resuming=payload is not None,
        resume_payload=payload,
        evaluation_base=base,
        search_space=search_space_config(configuration.get("search_space") or DEFAULT_SEARCH_SPACE),
        opponents=opponents,
        sampler=sampler_config(configuration.get("sampler")),
        pruner=pruner_config(configuration.get("pruner")),
        source_reference=source_reference,
    )
    identity["sampler"]["seed"] = configuration.get("seed", 0)
    identity["frozen_models"] = frozen
    return identity


def tune(
    configuration: dict,
    progress: Callable[[dict], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> dict:
    """Run one serialized, identity-checked optimization invocation."""
    from .training_identity import study_lease

    with study_lease(configuration):
        return _tune(configuration, progress, should_stop)


def _tune(
    configuration: dict,
    progress: Callable[[dict], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> dict:
    """Resume an Optuna study and rank trials by balanced playing score, not loss."""
    import optuna

    output = Path(configuration.get("output_dir", "data/tuning")).resolve()
    output.mkdir(parents=True, exist_ok=True)
    seed = int(configuration.get("seed", 0))
    trials = int(configuration.get("trials", 4))
    if not 1 <= trials <= 10000:
        raise ValueError("trials must be between 1 and 10000")
    storage = configuration.get("storage", f"sqlite:///{output / 'study.sqlite3'}")
    from .job_studies import (
        opponents_config,
        pruner_config,
        sampler_config,
        search_space_config,
        verification_config,
    )

    # One validator for every caller: the CLI, the HTTP API and job archives
    # all accept exactly float, log-float, int, int-step and categorical. An
    # absent or empty space falls back to the documented default.
    from .job_studies import DEFAULT_SEARCH_SPACE

    search_space = search_space_config(configuration.get("search_space") or DEFAULT_SEARCH_SPACE)
    raw_training = configuration.get(
        "training", {"preset": "smoke", "ruleset": configuration.get("ruleset", "brandubh")}
    )
    if any(name.startswith("evaluation_") or name.startswith("champion_") for name in search_space):
        raise ValueError("Optuna cannot tune evaluation protocol settings")
    # Normalize the common configuration once, so preset defaults (including the
    # search deadline) are identical whether the caller passed a partial or an
    # already-normalized training configuration.
    base = normalize_config({**raw_training, "output_dir": str(output)})
    if base.get("adaptation", "off") != "off":
        # The study varies the learning rate across trials; a controller adapting
        # it inside a trial would compete with the study's own search.
        raise ValueError("Disable automatic hyperparameter updates while tuning with Optuna")
    opponents = opponents_config(configuration.get("opponents"))
    verification = verification_config(configuration.get("verification"))
    sampler_options = sampler_config(configuration.get("sampler"))
    pruner_options = pruner_config(configuration.get("pruner"))
    max_failures = int(configuration.get("max_failures", min(3, trials)))
    if not 1 <= max_failures <= trials:
        raise ValueError("max_failures must be between 1 and the trial budget")
    # The sampler seed and the model seed are distinct settings. Resuming a
    # checkpoint keeps its saved model seed so the immutable resume check
    # passes; otherwise every trial draws a deterministic per-trial seed.
    resuming = bool(base.get("resume"))
    source_seed = int(base["seed"])
    resume_payload: dict | None = None
    source_reference = None
    if resuming:
        from .training_identity import load_source_checkpoint, source_configuration

        resume_model, resume_payload, source_reference = load_source_checkpoint(
            base["resume"], load_checkpoint
        )
        saved_inputs = source_configuration(resume_payload)
        source_seed = int(saved_inputs.get("seed", source_seed))
        base = normalize_config(
            {**saved_inputs, **raw_training, "output_dir": str(output), "seed": source_seed}
        )
    if base.get("evaluation_opponent", "shield_bearer") == "champion":
        raise ValueError(
            "Tuning cannot use the evolving Champion as its objective; select a fixed "
            "opponent (shield_bearer, shield_maiden, jarl) or a frozen neural model."
        )
    if resuming:
        # Checkpoint-based tuning must be validated before any expensive trial.
        assert resume_model is not None and resume_payload is not None
        completed_iteration = int(resume_payload.get("extra", {}).get("iteration", 0))
        target_iterations = base.get("iterations")
        if type(target_iterations) is not int or target_iterations <= completed_iteration:
            raise ValueError(
                "Checkpoint-based tuning needs training.iterations greater than the "
                f"checkpoint's completed iteration ({completed_iteration}); set it to "
                f"{completed_iteration} + additional tuning iterations."
            )
        if target_iterations > 100000:
            raise ValueError("iterations must not exceed 100000")
        # Architecture is fixed by the checkpoint and cannot be sampled.
        for name in ("channels", "blocks"):
            if name in search_space:
                raise ValueError(
                    f"{name} is fixed by the source checkpoint and cannot be tuned "
                    "while resuming it"
                )
            expected = resume_model.config.get(name)
            requested = base.get(name)
            if requested is not None and requested != expected:
                raise ValueError(f"{name} must match the source checkpoint ({expected})")
        if base.get("ruleset") is not None and base["ruleset"] != resume_model.ruleset:
            raise ValueError(f"ruleset must match the source checkpoint ({resume_model.ruleset})")
    # The optional aggregate and held-out stages evaluate a saved checkpoint
    # outside a trial; ``base`` is already normalized above.
    evaluation_base = {**base, "seed": source_seed}
    # A frozen neural opponent is loaded once and pinned for the whole study, so
    # every trial is scored against the same artifact and a missing or
    # incompatible model fails before the first trial starts.
    frozen_models: dict[str, PolicyValueNet] = {}
    frozen_identities: dict[str, str] = {}
    for entry in opponents:
        if entry["opponent"] != "frozen_model":
            continue
        model_id = entry["model_id"]
        if model_id in frozen_models:
            continue
        registry_root = evaluation_base.get("registry_dir", output.parent / "models")
        record = ModelRegistry(registry_root).get(model_id)
        frozen, _ = load_checkpoint(record["path"])
        if frozen.ruleset != evaluation_base["ruleset"]:
            raise ValueError(
                f"Frozen model {model_id} is for {frozen.ruleset}, not {evaluation_base['ruleset']}"
            )
        frozen.eval()
        frozen.requires_grad_(False)
        frozen_models[model_id] = frozen
        from .training_identity import content_digest

        frozen_identities[model_id] = content_digest(frozen.state_dict())
    identity = study_identity(
        resuming=resuming,
        resume_payload=resume_payload,
        evaluation_base=evaluation_base,
        search_space=search_space,
        opponents=opponents,
        sampler=sampler_options,
        pruner=pruner_options,
        source_reference=source_reference,
    )
    identity["sampler"]["seed"] = seed
    identity["frozen_models"] = frozen_identities
    # The study's own elapsed time is measured here, on the monotonic clock, so it
    # spans every trial, the preparation between them and the finalization instead
    # of restarting with each trial's monitor.
    study_started = time.monotonic()
    tune_lock = threading.Lock()
    latest: dict = {
        "status": "running",
        "completed_trials": 0,
        "trials_requested": trials,
        "duration": 0.0,
    }
    # A running trial's own monitor publishes the job's files while it trains; the
    # study's monitor covers study creation, parameter sampling, the bookkeeping
    # between trials and the finalization, so the heartbeat never stops. Only one of
    # them publishes at a time and each writes its progress and heartbeat pair under
    # one lock, so a reader never sees a mismatched pair.
    active_trial: dict = {"running": False, "state": None}

    def write_study_snapshot(heartbeat: bool, changes: dict) -> None:
        """Publish the study's own state; a running trial publishes instead."""
        with tune_lock:
            latest.update(changes)
            if heartbeat and (active_trial["running"] or latest.get("status") != "running"):
                # The trial's monitor owns the files while it trains, and a terminal
                # publication is final: no later heartbeat may revive it.
                return
            latest["duration"] = time.monotonic() - study_started
            if not heartbeat:
                study_monitor.published()
            latest.update(study_monitor.snapshot())
            state = active_trial["state"]
            if state is not None:
                # The last trial keeps its data (history, metrics, model list) but
                # not the live stage or timers of a trial that is no longer running.
                latest["trial_progress"] = without_live_timing(state)
            if heartbeat:
                _write_json(output / HEARTBEAT_FILENAME, heartbeat_projection(latest))
            else:
                _write_json(output / "progress.json", latest)
            if progress:
                progress(dict(latest))

    study_monitor = TrainingMonitor(
        lambda **flags: write_study_snapshot(bool(flags.get("heartbeat")), {}),
        phase="initializing_study",
    )

    def publish_study(**changes) -> None:
        """Publish a real study-level progress event."""
        write_study_snapshot(False, changes)

    def count_completed(study) -> int:
        """Trials the study has already recorded as complete.

        Reading the whole study is O(N) on persistent storage, so this runs only at
        the lifecycle boundaries (loading a study and aggregating its final result)
        and never once per trial.
        """
        return sum(1 for trial in study.trials if trial.state == optuna.trial.TrialState.COMPLETE)

    failure_streak = {"count": 0}

    def on_trial_finished(study, trial) -> None:
        """Count a trial once Optuna has committed its final state.

        Optuna calls this after a trial's state is stored, which is the only
        authoritative moment: an objective that trained successfully but then failed
        (a missing evaluation, an attribute write that raises) is recorded as
        ``FAIL`` and is not a completed trial. Counting here keeps the per-trial
        bookkeeping O(1) instead of re-reading the whole study. A run of failures
        stops the study before the whole budget is wasted.
        """
        if trial.state == optuna.trial.TrialState.COMPLETE:
            failure_streak["count"] = 0
            with tune_lock:
                latest["completed_trials"] = latest.get("completed_trials", 0) + 1
        elif trial.state == optuna.trial.TrialState.FAIL:
            failure_streak["count"] += 1
            with tune_lock:
                latest["failed_trials"] = latest.get("failed_trials", 0) + 1
            if failure_streak["count"] >= max_failures:
                study.stop()
        else:
            return
        publish_study()

    def run_trials(study) -> None:
        def score_opponents(result) -> dict | None:
            """Weighted score of a trial's best checkpoint over the fixed set."""
            if not opponents or not result.get("best_checkpoint"):
                return None
            model, _ = load_checkpoint(result["best_checkpoint"])
            model.to(resolve_device(evaluation_base["device"]))
            total = sum(entry["weight"] for entry in opponents)
            weighted = 0.0
            per_opponent: dict = {}
            for index, entry in enumerate(opponents):
                opponent = entry["opponent"]
                eval_config = {
                    **evaluation_base,
                    "evaluation_opponent": opponent,
                    # Distinct, deterministic per-opponent seeds keep the
                    # aggregate reproducible without reusing a trial seed.
                    "seed": source_seed + 2_000_000 + index,
                }
                if opponent == "frozen_model":
                    eval_config["evaluation_frozen_model_id"] = entry["model_id"]
                    evaluation = evaluate_model(
                        model,
                        eval_config,
                        previous=frozen_models[entry["model_id"]],
                        should_stop=should_stop,
                    )
                else:
                    evaluation = evaluate_model(model, eval_config, should_stop=should_stop)
                key = f"{opponent}:{entry['model_id']}" if opponent == "frozen_model" else opponent
                per_opponent[key] = {
                    key: evaluation[key]
                    for key in (
                        "score",
                        "wins",
                        "losses",
                        "draws",
                        "truncated",
                        "opponent_identity",
                    )
                }
                weighted += entry["weight"] * evaluation["score"]
            return {"score": weighted / total, "opponents": per_opponent}

        def objective(trial):
            trial.set_user_attr("experiment_identity", identity)
            trial.set_user_attr("artifact_owner", str(output))
            if should_stop and should_stop():
                study.stop()
                raise optuna.TrialPruned("Tuning cancelled")
            # Sampling parameters writes to the Optuna database, so it stays under
            # the study's own heartbeat until the trial's monitor takes over.
            study_monitor.phase("preparing_trial")
            parameters = {}
            for name, distribution in search_space.items():
                kind = distribution.get("type", "float")
                if kind == "categorical":
                    parameters[name] = trial.suggest_categorical(name, distribution["choices"])
                elif kind == "int":
                    parameters[name] = trial.suggest_int(
                        name,
                        int(distribution["low"]),
                        int(distribution["high"]),
                        step=int(distribution.get("step", 1)),
                        log=bool(distribution.get("log", False)),
                    )
                elif kind == "float":
                    parameters[name] = trial.suggest_float(
                        name,
                        float(distribution["low"]),
                        float(distribution["high"]),
                        log=bool(distribution.get("log", False)),
                    )
                else:
                    raise ValueError("Unknown search-space distribution")
            # Resuming a shared checkpoint restores its random-generator state, so
            # its seed is immutable; a fresh trial draws its own deterministic seed.
            trial_seed = source_seed if resuming else source_seed + trial.number
            config = {
                **base,
                **parameters,
                "seed": trial_seed,
                "output_dir": str(output / f"trial-{trial.number}"),
            }

            latest_revision = None

            def report(state):
                nonlocal latest_revision
                revision = state.get("revision")
                heartbeat = revision == latest_revision
                # Each trial is a new training run whose revisions restart, so the
                # outer files carry the identity of the run that produced them. A
                # heartbeat from an earlier trial can then never overlay this one.
                run = state.get("run")
                duration = time.monotonic() - study_started
                with tune_lock:
                    active_trial["state"] = state
                    latest.update(
                        trial=trial.number,
                        revision=revision,
                        run=run,
                        duration=duration,
                        # The retained outer snapshot names the phase the trial is
                        # in, so it never keeps describing the study's bookkeeping.
                        stage=state.get("stage"),
                        phase_started_at=state.get("phase_started_at"),
                        phase_elapsed=state.get("phase_elapsed"),
                        trial_progress=state,
                    )
                    if not heartbeat:
                        # A real progress event keeps the retained outer snapshot.
                        _write_json(output / "progress.json", latest)
                    # The bounded file always carries the current trial, revision
                    # and the study's own elapsed time, so a long trial cannot
                    # freeze it.
                    _write_json(
                        output / HEARTBEAT_FILENAME,
                        {
                            "status": latest["status"],
                            "completed_trials": latest["completed_trials"],
                            "trials_requested": latest["trials_requested"],
                            "trial": trial.number,
                            "revision": revision,
                            "run": run,
                            "duration": duration,
                            "trial_progress": heartbeat_projection(state),
                        },
                    )
                    if progress:
                        progress(dict(latest))
                if heartbeat:
                    return
                latest_revision = revision
                evaluation = state.get("metrics", {}).get("evaluation")
                if state.get("stage") == "iteration_complete" and evaluation:
                    # The selected checkpoint's own score, so the pruner ranks the
                    # same quantity the objective returns. It is monotonic for a
                    # fixed benchmark, so a trial that dips before recovering is
                    # not discarded.
                    selected_score = state.get("candidate_score", state.get("selected_score"))
                    if not opponents and selected_score is not None and selected_score >= 0:
                        trial.report(float(selected_score), step=state["iteration"])
                        if trial.should_prune():
                            raise optuna.TrialPruned("Selected checkpoint below study median")

            # Handing the files to the trial and taking them back are serialized
            # against the study's own heartbeat, so neither publisher can overwrite
            # the other's snapshot with a stale stage.
            with tune_lock:
                active_trial["running"] = True
            try:
                result = train(
                    config,
                    report,
                    should_stop,
                    trial_mode=True,
                    evaluation_seed=evaluation_context(evaluation_base)["seed"],
                    expected_source=identity["source"],
                )
            except optuna.TrialPruned:
                raise
            except Exception as error:
                if should_stop and should_stop():
                    study.stop()
                    raise optuna.TrialPruned("Tuning cancelled") from error
                # Record the failure for the result and let Optuna mark the trial
                # FAIL; the study continues until the failure budget is exhausted.
                trial.set_user_attr("error", str(error))
                raise
            finally:
                # The trial's monitor has stopped and the study's heartbeat is
                # suppressed while a trial is marked running, so ownership returns to
                # the study here. Nothing in this handover touches Optuna storage:
                # the completed-trial count is updated by the study's own callback
                # once Optuna has committed the trial's state, so no bookkeeping can
                # delay the heartbeat or mask the exception that ended the trial.
                study_monitor.phase("between_trials")
                with tune_lock:
                    active_trial["running"] = False
                publish_study()
                last_path = output / f"trial-{trial.number}" / "last.pt"
                if last_path.is_file():
                    trial.set_user_attr("last_checkpoint", str(last_path))
            if result["status"] == "cancelled":
                study.stop()
                raise optuna.TrialPruned("Tuning cancelled")
            objective_checkpoint = result.get("objective_checkpoint") or result.get(
                "best_checkpoint"
            )
            objective_score = result.get("candidate_score", result.get("selected_score"))
            objective_iteration = result.get(
                "candidate_iteration", result.get("selected_iteration")
            )
            if "candidate_score" in result and (
                objective_score is None
                or objective_score < 0
                or not result.get("objective_checkpoint")
            ):
                raise ValueError("Trial completed without a newly evaluated candidate checkpoint")
            evaluation = result["metrics"].get("evaluation")
            if evaluation:
                trial.set_user_attr("evaluation", evaluation)
            trial.set_user_attr("training_config", normalize_config(config))
            trial.set_user_attr("checkpoint", objective_checkpoint)
            trial.set_user_attr(
                "trial_checkpoint", result.get("objective_checkpoint") or result.get("checkpoint")
            )
            trial.set_user_attr("last_checkpoint", result.get("checkpoint"))
            trial.set_user_attr("best_iteration", objective_iteration)
            trial.set_user_attr("best_score", objective_score)
            trial.set_user_attr("best_source", "candidate")
            trial.set_user_attr("overall_selected_score", result.get("selected_score"))
            trial.set_user_attr("source_best_score", result.get("source_best_score"))
            trial.set_user_attr("final_score", evaluation["score"] if evaluation else None)
            aggregate = score_opponents({**result, "best_checkpoint": objective_checkpoint})
            if aggregate is not None:
                trial.set_user_attr("opponents", aggregate["opponents"])
                trial.set_user_attr("aggregate_score", aggregate["score"])
                return float(aggregate["score"])
            selected_score = objective_score
            if selected_score is None or selected_score < 0:
                raise ValueError("Trial completed without a selected checkpoint evaluation")
            return float(selected_score)

        # The callback runs after each trial's state is stored, so the completed
        # counter follows Optuna's own record instead of the objective returning.
        # ``catch`` marks a failing trial FAIL and keeps the study alive; the
        # callback stops it once the failure budget is exhausted.
        study.optimize(
            objective,
            n_trials=trials,
            callbacks=[on_trial_finished],
            catch=(Exception,),
        )

    def verify_best(best) -> dict | None:
        """Held-out evaluation of the winning trial's best checkpoint.

        The verification seed is never used by a trial, so the score is an
        independent check rather than a re-read of the study objective.
        """
        if not verification:
            return None
        checkpoint = best.user_attrs.get("checkpoint")
        if not checkpoint:
            return None
        model, _ = load_checkpoint(checkpoint)
        model.to(resolve_device(evaluation_base["device"]))
        opponent = verification.get("opponent", "shield_bearer")
        games = verification.get("games", evaluation_base["evaluation_games"])
        held_out_seed = verification.get("seed", (seed + 7_000_000) % 2**32)
        evaluation = evaluate_model(
            model,
            {
                **evaluation_base,
                "evaluation_opponent": opponent,
                "evaluation_games": games,
                "seed": held_out_seed,
            },
            should_stop=should_stop,
        )
        return {
            "opponent": evaluation["opponent"],
            "opponent_identity": evaluation["opponent_identity"],
            "seed": evaluation["seed"],
            "games": games,
            "score": evaluation["score"],
            "wins": evaluation["wins"],
            "losses": evaluation["losses"],
            "draws": evaluation["draws"],
            "truncated": evaluation["truncated"],
            "best_trial": best.number,
        }

    def finish_study(study) -> dict:
        from .job_studies import trial_report, winner_report

        # Aggregating results and persisting them can take a while; the heartbeat
        # keeps running until the terminal publication is written.
        study_monitor.phase("finalizing_study")
        publish_study()
        completed_trials = [
            trial for trial in study.trials if trial.state == optuna.trial.TrialState.COMPLETE
        ]
        failed_trials = [
            trial for trial in study.trials if trial.state == optuna.trial.TrialState.FAIL
        ]
        cancelled = bool(should_stop and should_stop())
        result = {
            "status": "cancelled" if cancelled else "completed",
            "study_name": study.study_name,
            "storage": storage,
            # Measured as late as possible, once the aggregation is done, so the
            # authoritative duration covers the whole finalization.
            "duration": 0.0,
            "completed_trials": len(completed_trials),
            "failed_trials": len(failed_trials),
            # The sampler seed and the model seed are recorded separately so a
            # later comparison can tell which randomness a result used.
            "sampler_seed": seed,
            "source_seed": source_seed,
            "opponents": opponents,
            "verification": verification,
            "verification_status": "not_requested" if not verification else "skipped_no_winner",
            "parameter_importance": parameter_importance(completed_trials),
            "experiment_identity": identity,
            "objective_policy": identity["objective"],
            "frozen_models": frozen_identities,
            "source_configuration": source_configuration(resume_payload)
            if resume_payload
            else None,
            "trials": [trial_report(trial) for trial in study.trials],
        }
        if completed_trials:
            best = study.best_trial
            result.update(winner_report(best))
            _write_json(output / "best-config.json", result["best_configuration"])
            result["verification_status"] = "pending" if verification else "not_requested"
            result["duration"] = time.monotonic() - study_started
            _write_json(output / "result.json", result)
            if verification:
                if cancelled:
                    result["verification_status"] = "cancelled"
                else:
                    study_monitor.phase("verification")
                    result["verification_status"] = "running"
                    _write_json(output / "result.json", result)
                    publish_study(**{**result, "status": "running"})
                    try:
                        result["verification_result"] = verify_best(best)
                        result["verification_status"] = "completed"
                    except InterruptedError as error:
                        result["verification_status"] = "cancelled"
                        result["verification_error"] = str(error)
                    except Exception as error:
                        result["verification_status"] = "failed"
                        result["verification_error"] = str(error)
        elif not cancelled:
            # No successful trial is an unsuccessful optimization, not a
            # completed study with a winner.
            result["status"] = "failed"
            result["error"] = (
                "No trial completed successfully; inspect the failed trials for their errors."
            )
        # Nothing slow happens past this point: the heartbeat stops, the duration is
        # measured at the terminal moment, and both terminal files are written from
        # one state, so the published duration never falls behind the last
        # heartbeat and no later heartbeat can revive the finished study.
        study_monitor.stop()
        result["duration"] = time.monotonic() - study_started
        latest.update(result)
        _write_json(output / "result.json", result)
        _write_json(output / "progress.json", result)
        if progress:
            progress(result)
        return result

    study_monitor.start()
    try:
        publish_study()
        study_name = configuration.get("study_name", "hnefatafl")
        if not isinstance(study_name, str) or not 1 <= len(study_name) <= 100:
            raise ValueError("study_name must contain 1–100 characters")
        study = optuna.create_study(
            study_name=study_name,
            storage=storage,
            direction="maximize",
            load_if_exists=True,
            # The documented default is three random startup trials. An explicit
            # sampler setting still wins, and a resumed study keeps its stored
            # identity because the sampler is part of that identity.
            sampler=optuna.samplers.TPESampler(
                seed=seed, **{"n_startup_trials": 3, **sampler_options}
            ),
            pruner=optuna.pruners.MedianPruner(
                n_startup_trials=pruner_options.get("n_startup_trials", 3),
                n_warmup_steps=pruner_options.get("n_warmup_steps", 1),
                interval_steps=pruner_options.get("interval_steps", 1),
            ),
        )
        from .training_identity import established_identity, LEGACY_MESSAGE

        stored_identity = established_identity(
            study.user_attrs.get("hnefatafl_identity"),
            [trial.user_attrs.get("experiment_identity") for trial in study.trials],
        )
        if stored_identity is None and study.trials:
            raise ValueError(LEGACY_MESSAGE)
        if stored_identity is not None and stored_identity != identity:
            differences = identity_differences(stored_identity, identity)
            raise ValueError(
                "The saved study was created with an incompatible experiment identity: "
                + ", ".join(differences)
            )
        study.set_user_attr("hnefatafl_identity", identity)
        if len(study.trials) + trials > 10000:
            raise ValueError("A study may contain at most 10000 trials; start a new study")
        latest["completed_trials"] = count_completed(study)
        publish_study()
        # Cancellation during the study load is honored at this safe point: the
        # database transaction is never interrupted, and the result is published.
        if not (should_stop and should_stop()):
            run_trials(study)
        return finish_study(study)
    except Exception as error:
        # A study failure is terminal, with the error recorded, and no heartbeat
        # may revive it afterwards.
        study_monitor.phase("failed")
        publish_study(status="failed", error=str(error))
        raise
    finally:
        # Exception-safe cleanup: the heartbeat thread is joined once, after the
        # terminal publication on every path.
        study_monitor.stop()


__all__ = [
    "train",
    "tune",
    "self_play",
    "evaluate_model",
    "normalize_config",
    "PRESETS",
    "device_info",
]
