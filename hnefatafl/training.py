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
from typing import Callable

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
from .game_statistics import checkpoint_cumulative, self_play_statistics
from .training_evaluation import EvaluationSelection, evaluate_model, frozen_model
from .training_monitoring import (
    HEARTBEAT_FILENAME,
    ProgressSink,
    TrainingMonitor,
    heartbeat_projection,
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
    "output_dir": "data/training",
    "model_name": "Self-play learner",
    "save_all_best": False,
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


def normalize_config(configuration: dict) -> dict:
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
        "cpu_threads": (1, 64),
        "self_play_concurrency": (1, 1024),
        "inference_batch_size": (1, 4096),
        "inference_cache_capacity": (1, 65536),
    }
    for key, (low, high) in integer_bounds.items():
        if type(config[key]) is not int or not low <= config[key] <= high:
            raise ValueError(f"{key} must be an integer between {low} and {high}")
    if config["self_play_mode"] not in ("auto", "sequential", "batched"):
        raise ValueError("self_play_mode must be auto, sequential or batched")
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
    if config["optimizer"] not in ("adamw", "adam", "sgd"):
        raise ValueError("optimizer must be adamw, adam, or sgd")
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
                monitor=monitor,
            )
            performance.update(refresh)
            return games, performance
        finally:
            owned.close()

    from .self_play import SelfPlayMetrics, batched_self_play

    device = next(model.parameters()).device
    mode = config["self_play_mode"]
    if mode == "auto":
        mode = (
            "batched"
            if device.type == "cuda" or config.get("inference_backend") == "openvino"
            else "sequential"
        )
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
    if config["optimizer"] == "sgd":
        return torch.optim.SGD(model.parameters(), momentum=config["momentum"], **arguments)
    if config["optimizer"] == "adam":
        return torch.optim.Adam(model.parameters(), **arguments)
    return torch.optim.AdamW(model.parameters(), **arguments)


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
    model.train()
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
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=5.0)
            scaler.step(optimizer)
            scaler.update()
            policy_losses.append(float(policy_loss.detach()))
            value_losses.append(float(value_loss.detach()))
            updates += 1
            if monitor is not None:
                # Completed-step counter plus the running means of the losses the
                # dashboard shows; the authoritative means are still computed
                # from the same lists after the phase.
                loss_sum += policy_losses[-1]
                value_sum += value_losses[-1]
                monitor.tick(optimizer_steps=1)
                monitor.counts(policy_loss=loss_sum / updates, value_loss=value_sum / updates)
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
) -> dict:
    """Train from scratch or resume to the configured *total* iteration count.

    `resume` names a safe checkpoint. Saved optimizer, replay, random-generator
    state and iteration are restored; ruleset and network architecture cannot
    change. A callback can monitor progress or raise Optuna TrialPruned.
    """
    # CLI resumes inherit saved settings too; only explicitly supplied inputs override them.
    saved_inputs = {}
    resume_model = None
    source: dict = {}
    if configuration.get("resume"):
        if "output_dir" not in configuration:
            raise ValueError(
                "Resume requires a new output_dir to preserve the source training files"
            )
        resume_model, source = load_checkpoint(configuration["resume"])
        saved_inputs = source["metadata"].get("training_config", {})
        if "training_symmetry_mode" not in saved_inputs:
            # Checkpoints written before D4 augmentation existed omitted this
            # setting. Keep their historical OFF behavior even though new jobs
            # now default to random D4; an explicit override still wins below.
            saved_inputs = {**saved_inputs, "training_symmetry_mode": "off"}
    config = normalize_config({**saved_inputs, **configuration})
    output = Path(config["output_dir"]).resolve()
    if config.get("resume") and output / "last.pt" == Path(config["resume"]).resolve():
        raise ValueError("Resume output_dir must preserve the source checkpoint path")
    output.mkdir(parents=True, exist_ok=True)
    torch.set_num_threads(config["cpu_threads"])
    device = resolve_device(config["device"])
    torch.manual_seed(config["seed"])
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(config["seed"])
    rng = random.Random(config["seed"])
    # Dedicated stream: OFF never draws from it, and neither replay shuffling nor
    # self-play scheduling can shift augmentation choices (or vice versa).
    augment_rng = augmentation_rng(config["seed"], 0)
    model = (
        resume_model
        if resume_model is not None
        else PolicyValueNet(config["ruleset"], config["channels"], config["blocks"])
    ).to(device)
    optimizer = _make_optimizer(model, config)
    replay: list[dict] = []
    completed = job_games = 0
    baseline: int | None = 0
    best_score = -1.0
    best_snapshot: dict | None = None
    metrics: dict = {}
    history: list[dict] = []
    best_models: list[dict] = []
    resume_payload = None
    saved_config: dict = {}
    extra: dict = {}
    if config.get("resume"):
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
        optimizer = _make_optimizer(model, config)
        if payload.get("optimizer_state") is None:
            raise ValueError("This checkpoint has no optimizer state and cannot resume training")
        optimizer.load_state_dict(payload["optimizer_state"])
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
        history = extra.get("history", [])
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
    checkpoint = output / "last.pt"
    best_checkpoint = output / "best.pt"
    selection = EvaluationSelection(extra, saved_config, config, metrics.get("evaluation"))
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
    selected = selection.selected_snapshot() or best_snapshot
    if selected:
        save_checkpoint(best_checkpoint, frozen_model(model, selected), selected["metadata"])
    mixed = bool(config["mixed_precision"] and device.type == "cuda")
    scaler = torch.amp.GradScaler("cuda", enabled=mixed)
    if resume_payload and resume_payload["extra"].get("grad_scaler"):
        scaler.load_state_dict(resume_payload["extra"]["grad_scaler"])
    started = time.monotonic()
    publish_lock = threading.Lock()
    state = {
        "status": "running",
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
    }
    _write_json(output / "config.json", config)

    def write_snapshot(heartbeat: bool, changes: dict) -> None:
        """Serialize one monitoring snapshot and publish it.

        ``heartbeat`` republishes the current state with refreshed timings from
        the monitor thread. It writes only the bounded heartbeat projection
        (never the retained history) and skips the GPU query, so no CUDA call
        runs off the training thread.
        """
        with publish_lock:
            state.update(changes)
            if not heartbeat:
                monitor.published()
            state.update(monitor.snapshot())
            state["self_play_games"] = self_play_statistics(job_games, baseline)
            state["games_generated"] = state["self_play_games"]["cumulative"]
            state["duration"] = time.monotonic() - started
            if heartbeat:
                _write_json(output / HEARTBEAT_FILENAME, heartbeat_projection(state))
            else:
                if device.type == "cuda":
                    state["gpu_memory_allocated"] = torch.cuda.memory_allocated(device)
                _write_json(output / "progress.json", state)
            if progress:
                progress(dict(state))

    monitor = TrainingMonitor(
        lambda **flags: write_snapshot(bool(flags.get("heartbeat")), {}), phase="initializing"
    )

    def publish(**changes):
        """Publish a real progress event from the training thread."""
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
        extra = {
            "iteration": completed,
            "games_generated": self_play_statistics(job_games, baseline)["cumulative"],
            "replay": replay,
            "best_score": best_score,
            "best_snapshot": best_snapshot,
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
        save_checkpoint(path, model, current_metadata(), optimizer, extra)

    backend = None
    monitor.start()
    try:
        publish()
        initialization_started = time.monotonic()
        backend = create_inference(model, config)
        initialization_seconds = time.monotonic() - initialization_started
        first_iteration = completed + 1
        publish(**backend.info, inference_initialization_seconds=initialization_seconds)
        for iteration in range(completed + 1, config["iterations"] + 1):
            if should_stop and should_stop():
                raise InterruptedError("Training was cancelled")
            iteration_start = time.monotonic()

            def commit_game(index: int, examples: list[dict], result: dict) -> None:
                nonlocal replay, job_games
                replay.extend(examples)
                replay = replay[-config["replay_buffer_size"] :]
                job_games += 1

            # Device/graph preparation can block for seconds on a cold backend
            # (TorchScript/OpenVINO compile); report it as its own phase.
            monitor.phase("preparing", iteration=iteration)
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
            if evaluation is not None and updates > 0:
                if should_stop and should_stop():
                    raise InterruptedError("Evaluation was cancelled")
                record_improvement = selection.accept(model, current_metadata(), evaluation, config)
                if selection.identity == legacy_identity and record_improvement:
                    best_score = evaluation["score"]
                    best_snapshot = selection.selected_snapshot()
            state["evaluation_selection"] = selection.summary()
            point = {
                "iteration": iteration,
                "policy_loss": metrics["policy_loss"],
                "value_loss": metrics["value_loss"],
                "replay_positions": metrics["replay_positions"],
                "iteration_duration": metrics["iteration_duration"],
                "performance": dict(performance),
            }
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
            history.append(point)
            # Model selection, checkpoint writes and registry publication are a
            # real phase: they can take seconds and must show as such.
            monitor.phase("checkpoint", iteration=iteration)
            artifact_started = time.monotonic()
            if record_improvement:
                assert evaluation is not None
                selected = selection.selected_snapshot()
                assert selected is not None
                save_checkpoint(
                    best_checkpoint, frozen_model(model, selected), selected["metadata"]
                )
                if config["save_all_best"]:
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
                metrics=metrics,
                performance=performance,
            )
    except InterruptedError:
        if completed and (not history or history[-1]["iteration"] < completed):
            history.append(
                {
                    "iteration": completed,
                    "policy_loss": metrics["policy_loss"],
                    "value_loss": metrics["value_loss"],
                    "performance": metrics["performance"],
                    "evaluation_status": "cancelled",
                }
            )
        checkpoint_now(checkpoint)
        monitor.phase("cancelled", iteration=completed)
        publish(
            status="cancelled",
            iteration=completed,
            checkpoint=str(checkpoint),
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
        if completed and (not history or history[-1]["iteration"] < completed):
            history.append(
                {
                    "iteration": completed,
                    "policy_loss": metrics["policy_loss"],
                    "value_loss": metrics["value_loss"],
                    "performance": metrics["performance"],
                    "evaluation_status": status,
                }
            )
        checkpoint_now(checkpoint)
        monitor.phase(status, iteration=completed)
        publish(
            status=status,
            error=str(error),
            iteration=completed,
            checkpoint=str(checkpoint),
        )
        raise
    finally:
        # The terminal publication is the last state any consumer may see; stop
        # the heartbeat so no later snapshot can revive a running status.
        monitor.stop()
        if backend is not None:
            backend.close()
    monitor.phase("finalizing", iteration=completed)
    publish()
    if not checkpoint.exists():
        checkpoint_now(checkpoint)
    if not best_checkpoint.exists():
        raise ValueError("Training completed without a best-model snapshot")
    registry_root = Path(config.get("registry_dir", output.parent / "models"))
    inference_checkpoint = export_checkpoint(best_checkpoint, output / "inference.pt")
    registered = ModelRegistry(registry_root).register(
        inference_checkpoint, name=config["model_name"]
    )
    monitor.phase("completed", iteration=completed)
    publish(
        status="completed",
        checkpoint=str(checkpoint),
        model=registered,
        best_checkpoint=str(best_checkpoint),
        inference_checkpoint=str(inference_checkpoint),
    )
    result = {**state, "output_dir": str(output), "iterations": completed}
    _write_json(output / "result.json", result)
    return result


def tune(
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
    study = optuna.create_study(
        study_name=configuration.get("study_name", "hnefatafl"),
        storage=storage,
        direction="maximize",
        load_if_exists=True,
        sampler=optuna.samplers.TPESampler(seed=seed),
        pruner=optuna.pruners.MedianPruner(n_startup_trials=2, n_warmup_steps=1),
    )
    search_space = configuration.get(
        "search_space",
        {
            "learning_rate": {"type": "float", "low": 0.0001, "high": 0.01, "log": True},
            "exploration": {"type": "float", "low": 0.5, "high": 2.0},
            "channels": {"type": "categorical", "choices": [8, 16, 32]},
        },
    )
    base = configuration.get(
        "training", {"preset": "smoke", "ruleset": configuration.get("ruleset", "brandubh")}
    )
    if base.get("evaluation_opponent", "shield_bearer") != "shield_bearer":
        raise ValueError("Optuna requires the comparable fixed Shield bearer objective")
    if any(name.startswith("evaluation_") or name.startswith("champion_") for name in search_space):
        raise ValueError("Optuna cannot tune evaluation protocol settings")
    # Self-play time may be tuned independently, but evaluation uses a fixed deadline.
    base = {
        **base,
        "evaluation_time_limit": base.get(
            "evaluation_time_limit", base.get("mcts_time_limit", 10.0)
        ),
    }
    latest = {
        "status": "running",
        "completed_trials": len(study.trials),
        "trials_requested": trials,
    }
    # The inner training heartbeat republishes through this report callback, so
    # the outer progress file is refreshed from another thread than the trial's.
    tune_lock = threading.Lock()

    def objective(trial):
        if should_stop and should_stop():
            study.stop()
            raise optuna.TrialPruned("Tuning cancelled")
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
        config = {
            **base,
            **parameters,
            "seed": seed + trial.number,
            "output_dir": str(output / f"trial-{trial.number}"),
        }

        latest_revision = None

        def report(state):
            nonlocal latest_revision
            revision = state.get("revision")
            heartbeat = revision == latest_revision
            with tune_lock:
                latest.update(trial=trial.number, revision=revision, trial_progress=state)
                if heartbeat:
                    # Liveness refresh: keep the retained outer snapshot intact and
                    # publish only the bounded trial projection.
                    _write_json(
                        output / HEARTBEAT_FILENAME,
                        {
                            "status": latest["status"],
                            "completed_trials": latest["completed_trials"],
                            "trials_requested": latest["trials_requested"],
                            "trial": trial.number,
                            "revision": revision,
                            "trial_progress": heartbeat_projection(state),
                        },
                    )
                else:
                    _write_json(output / "progress.json", latest)
                if progress:
                    progress(dict(latest))
            if heartbeat:
                return
            latest_revision = revision
            evaluation = state.get("metrics", {}).get("evaluation")
            if state.get("stage") == "iteration_complete" and evaluation:
                trial.report(evaluation["score"], step=state["iteration"])
                if trial.should_prune():
                    raise optuna.TrialPruned("Playing score below study median")

        result = train(config, report, should_stop)
        if result["status"] == "cancelled":
            study.stop()
            raise optuna.TrialPruned("Tuning cancelled")
        evaluation = result["metrics"]["evaluation"]
        trial.set_user_attr("checkpoint", result["best_checkpoint"])
        trial.set_user_attr("evaluation", evaluation)
        trial.set_user_attr("training_config", normalize_config(config))
        return float(evaluation["score"])

    study.optimize(objective, n_trials=trials)
    completed_trials = [
        trial for trial in study.trials if trial.state == optuna.trial.TrialState.COMPLETE
    ]
    cancelled = bool(should_stop and should_stop())
    result = {
        "status": "cancelled" if cancelled else "completed",
        "study_name": study.study_name,
        "storage": storage,
        "completed_trials": len(completed_trials),
        "trials": [
            {
                "number": trial.number,
                "state": trial.state.name,
                "score": trial.value,
                "parameters": trial.params,
            }
            for trial in study.trials
        ],
    }
    if completed_trials:
        best = study.best_trial
        result.update(
            best_score=best.value,
            best_parameters=best.params,
            best_checkpoint=best.user_attrs.get("checkpoint"),
            best_configuration=best.user_attrs.get("training_config"),
        )
        _write_json(output / "best-config.json", result["best_configuration"])
    _write_json(output / "result.json", result)
    _write_json(output / "progress.json", result)
    if progress:
        progress(result)
    return result


__all__ = [
    "train",
    "tune",
    "self_play",
    "evaluate_model",
    "normalize_config",
    "PRESETS",
    "device_info",
]
