"""Headless self-play training, checkpoint resumption, evaluation and Optuna.

The web server launches this module's CLI in a separate process. No training
threads or CUDA contexts are shared with the gameplay server.
"""

from __future__ import annotations

import json
import math
import os
import random
import time
from copy import deepcopy
from pathlib import Path
from typing import Callable

import torch
from torch.nn import functional as F

from .ai import choose_move, mcts_policy, policy_value
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
from .training_presets import RECOMMENDED


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
    "mcts_time_limit": 2.0,
    "exploration": 1.4,
    "temperature": 1.0,
    "dirichlet_alpha": 0.3,
    "dirichlet_fraction": 0.25,
    "replay_buffer_size": 5000,
    "weight_decay": 0.0001,
    "checkpoint_frequency": 1,
    "evaluation_frequency": 1,
    "evaluation_games": 2,
    "evaluation_iterations": 16,
    "evaluation_max_plies": 150,
    "max_game_plies": 150,
    "seed": 0,
    "device": "auto",
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
        "evaluation_games": (2, 10000),
        "cpu_threads": (1, 64),
    }
    for key, (low, high) in integer_bounds.items():
        if type(config[key]) is not int or not low <= config[key] <= high:
            raise ValueError(f"{key} must be an integer between {low} and {high}")
    if config["evaluation_games"] % 2:
        raise ValueError("evaluation_games must be even so the model plays both sides")
    for key, minimum, maximum in (
        ("learning_rate", 1e-7, 1.0),
        ("weight_decay", 0.0, 1.0),
        ("exploration", 0.0, 20.0),
        ("temperature", 0.01, 10.0),
        ("dirichlet_alpha", 0.001, 100.0),
        ("dirichlet_fraction", 0.0, 1.0),
        ("mcts_time_limit", 0.001, 300.0),
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
    return config


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def self_play(
    model: PolicyValueNet, config: dict, *, seed: int, should_stop: Callable[[], bool] | None = None
) -> tuple[list[dict], dict]:
    """Generate legal MCTS policy targets and final defender-perspective returns."""
    rng = random.Random(seed)
    state = new_game(config["ruleset"])
    examples = []
    model.eval()
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
        policy = mcts_policy(state, search_config)
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
    value = (
        {"attackers": -1.0, "defenders": 1.0, "draw": 0.0}[winner]
        if winner is not None
        else policy_value(state, model=model)[1]
    )
    for example in examples:
        example["value"] = value
    return examples, {
        "winner": winner,
        "outcome": "completed" if winner else "truncated",
        "reason": state.reason or "training_ply_limit",
        "bootstrap_value": value if winner is None else None,
        "plies": len(examples),
        "duration": time.monotonic() - started,
    }


def evaluate_model(
    model: PolicyValueNet,
    config: dict,
    *,
    previous: PolicyValueNet | None = None,
    seed: int | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> dict:
    """Balanced model-vs-baseline evaluation with explicit unfinished outcomes.

    The tuning score counts wins + half of *rule* draws divided by all scheduled
    games. An unfinished game earns zero; it is reported as truncated, never as
    a defeat or draw. This conservative completion-aware score cannot reward
    short budgets that merely avoid losing.
    """
    seed = config["seed"] if seed is None else seed
    outcomes = []
    model.eval()
    if previous is not None:
        previous.eval()
    started = time.monotonic()
    for game in range(config["evaluation_games"]):
        state = new_game(config["ruleset"])
        candidate_side = "attackers" if game % 2 == 0 else "defenders"
        moves = []
        for ply in range(config["evaluation_max_plies"]):
            if should_stop and should_stop():
                raise InterruptedError("Evaluation was cancelled")
            if state.winner:
                break
            candidate = state.turn == candidate_side
            opponent_model = model if candidate else previous
            ai_config = (
                {
                    "algorithm": "neural",
                    "_model": opponent_model,
                    "iterations": config["evaluation_iterations"],
                    "rollout_depth": 0,
                    "time_limit": config["mcts_time_limit"],
                    "seed": seed + 1000 * game + ply,
                }
                if opponent_model is not None
                else {
                    "algorithm": "baseline",
                    "time_limit": config["mcts_time_limit"],
                    "seed": seed + 1000 * game + ply,
                }
            )
            move = choose_move(state, ai_config)
            moves.append({"source": move.source, "target": move.target})
            state = apply_move(state, move)
        winner = state.winner
        score = 0.5 if winner == "draw" else float(winner == candidate_side)
        outcomes.append(
            {
                "candidate_side": candidate_side,
                "winner": winner,
                "outcome": "completed" if winner else "truncated",
                "score": score,
                "plies": len(moves),
                "reason": state.reason or "evaluation_ply_limit",
            }
        )
    wins = sum(game["score"] == 1 for game in outcomes)
    losses = sum(game["score"] == 0 and game["winner"] is not None for game in outcomes)
    draws = sum(game["winner"] == "draw" for game in outcomes)
    truncated = sum(game["winner"] is None for game in outcomes)
    return {
        "opponent": "previous_model" if previous is not None else "baseline",
        "wins": wins,
        "losses": losses,
        "draws": draws,
        "truncated": truncated,
        "score": (wins + draws / 2) / len(outcomes),
        "games": outcomes,
        "duration": time.monotonic() - started,
        "seed": seed,
    }


def _make_optimizer(model: PolicyValueNet, config: dict):
    arguments = {"lr": config["learning_rate"], "weight_decay": config["weight_decay"]}
    if config["optimizer"] == "sgd":
        return torch.optim.SGD(model.parameters(), momentum=0.9, **arguments)
    if config["optimizer"] == "adam":
        return torch.optim.Adam(model.parameters(), **arguments)
    return torch.optim.AdamW(model.parameters(), **arguments)


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
    config = normalize_config(configuration)
    output = Path(config["output_dir"]).resolve()
    output.mkdir(parents=True, exist_ok=True)
    torch.set_num_threads(config["cpu_threads"])
    device = resolve_device(config["device"])
    torch.manual_seed(config["seed"])
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(config["seed"])
    rng = random.Random(config["seed"])
    model = PolicyValueNet(config["ruleset"], config["channels"], config["blocks"]).to(device)
    optimizer = _make_optimizer(model, config)
    replay: list[dict] = []
    completed = games_generated = 0
    best_score = -1.0
    best_snapshot: dict | None = None
    previous = None
    metrics: dict = {}
    history: list[dict] = []
    best_models: list[dict] = []
    resume_payload = None
    if config.get("resume"):
        model, payload = load_checkpoint(config["resume"], device=str(device))
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
        optimizer = _make_optimizer(model, config)
        if payload.get("optimizer_state") is None:
            raise ValueError("This checkpoint has no optimizer state and cannot resume training")
        optimizer.load_state_dict(payload["optimizer_state"])
        # Explicit requested learning rate wins, otherwise keep the restored one.
        if "learning_rate" in configuration:
            for group in optimizer.param_groups:
                group["lr"] = config["learning_rate"]
        extra = payload.get("extra", {})
        history = extra.get("history", [])
        best_models = extra.get("best_models", [])
        completed = int(extra.get("iteration", 0))
        games_generated = int(extra.get("games_generated", 0))
        replay = extra.get("replay", [])[-config["replay_buffer_size"] :]
        best_score = float(extra.get("best_score", -1))
        best_snapshot = extra.get("best_snapshot")
        if best_score >= 0 and not best_snapshot:
            raise ValueError("Resume checkpoint is missing its portable best-model snapshot")
        if "python_rng" in extra:
            rng.setstate(extra["python_rng"])
        if "torch_rng" in extra:
            torch.set_rng_state(extra["torch_rng"].cpu())
        if device.type == "cuda" and extra.get("cuda_rng"):
            torch.cuda.set_rng_state_all(extra["cuda_rng"])
        metrics = payload["metadata"].get("metrics", {})
        if completed >= config["iterations"]:
            raise ValueError("Resume iterations must exceed the saved completed iteration")
    checkpoint = output / "last.pt"
    best_checkpoint = output / "best.pt"
    if best_snapshot:
        previous = deepcopy(model).eval()
        previous.load_state_dict(best_snapshot["state_dict"], strict=True)
        save_checkpoint(best_checkpoint, previous, best_snapshot["metadata"])
    mixed = bool(config["mixed_precision"] and device.type == "cuda")
    scaler = torch.amp.GradScaler("cuda", enabled=mixed)
    if resume_payload and resume_payload["extra"].get("grad_scaler"):
        scaler.load_state_dict(resume_payload["extra"]["grad_scaler"])
    started = time.monotonic()
    state = {
        "status": "running",
        "iteration": completed,
        "total_iterations": config["iterations"],
        "games_generated": games_generated,
        "device": str(device),
        "mixed_precision": mixed,
        "checkpoint": None,
        "metrics": metrics,
        "history": history,
        "best_models": best_models,
    }
    _write_json(output / "config.json", config)

    def publish(**changes):
        state.update(changes)
        state["duration"] = time.monotonic() - started
        if device.type == "cuda":
            state["gpu_memory_allocated"] = torch.cuda.memory_allocated(device)
        _write_json(output / "progress.json", state)
        if progress:
            progress(dict(state))

    def current_metadata():
        return {
            "name": config["model_name"],
            "version": completed,
            "training_config": config,
            "metrics": metrics,
            "evaluation": metrics.get("evaluation", {}),
        }

    def checkpoint_now(path: Path):
        extra = {
            "iteration": completed,
            "games_generated": games_generated,
            "replay": replay,
            "best_score": best_score,
            "best_snapshot": best_snapshot,
            "history": history,
            "best_models": best_models,
            "python_rng": rng.getstate(),
            "torch_rng": torch.get_rng_state(),
            "cuda_rng": torch.cuda.get_rng_state_all() if device.type == "cuda" else [],
            "grad_scaler": scaler.state_dict(),
        }
        save_checkpoint(path, model, current_metadata(), optimizer, extra)

    publish(stage="initializing")
    try:
        for iteration in range(completed + 1, config["iterations"] + 1):
            if should_stop and should_stop():
                raise InterruptedError("Training was cancelled")
            iteration_start = time.monotonic()
            generated = []
            for game in range(config["games"]):
                publish(stage="self_play", game=game + 1)
                examples, game_result = self_play(
                    model,
                    config,
                    seed=config["seed"] + iteration * 100000 + game,
                    should_stop=should_stop,
                )
                replay.extend(examples)
                replay = replay[-config["replay_buffer_size"] :]
                games_generated += 1
                generated.append(game_result)
            if not replay:
                raise ValueError("Self-play generated no training examples")
            model.train()
            policy_losses, value_losses = [], []
            updates = 0
            for epoch in range(config["epochs"]):
                order = list(range(len(replay)))
                rng.shuffle(order)
                for start in range(0, len(order), config["batch_size"]):
                    if should_stop and should_stop():
                        raise InterruptedError("Training was cancelled")
                    batch = [replay[index] for index in order[start : start + config["batch_size"]]]
                    inputs = torch.stack([row["input"] for row in batch]).to(device)
                    values = torch.tensor(
                        [row["value"] for row in batch], dtype=torch.float32, device=device
                    )
                    target_policy = torch.zeros((len(batch), model.size**4), device=device)
                    legal_mask = torch.zeros_like(target_policy, dtype=torch.bool)
                    for index, row in enumerate(batch):
                        target_policy[index, row["actions"]] = torch.tensor(
                            row["probabilities"], dtype=torch.float32, device=device
                        )
                        legal_mask[index, row["legal"]] = True
                    optimizer.zero_grad(set_to_none=True)
                    with torch.autocast(device_type=device.type, enabled=mixed):
                        logits, predicted = model(inputs)
                        logits = logits.float().masked_fill(~legal_mask, -1e9)
                        policy_loss = (
                            -(target_policy * F.log_softmax(logits, dim=1)).sum(dim=1).mean()
                        )
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
                publish(stage="optimization", epoch=epoch + 1)
            model.eval()
            metrics = {
                "policy_loss": sum(policy_losses) / len(policy_losses),
                "value_loss": sum(value_losses) / len(value_losses),
                "updates": updates,
                "self_play": generated,
                "replay_positions": len(replay),
            }
            if iteration % config["evaluation_frequency"] == 0 or iteration == config["iterations"]:
                publish(stage="evaluation")
                metrics["evaluation"] = evaluate_model(
                    model,
                    config,
                    seed=config["seed"] + iteration * 1000000,
                    should_stop=should_stop,
                )
                if previous is not None:
                    metrics["previous_evaluation"] = evaluate_model(
                        model,
                        config,
                        previous=previous,
                        seed=config["seed"] + iteration * 1000000 + 100,
                        should_stop=should_stop,
                    )
            completed = iteration
            metrics["iteration_duration"] = time.monotonic() - iteration_start
            score = metrics.get("evaluation", {}).get("score", -1)
            point = {
                "iteration": iteration,
                "policy_loss": metrics["policy_loss"],
                "value_loss": metrics["value_loss"],
                "replay_positions": metrics["replay_positions"],
                "iteration_duration": metrics["iteration_duration"],
            }
            if "evaluation" in metrics:
                evaluation = metrics["evaluation"]
                point.update(
                    evaluation_score=evaluation["score"],
                    **{
                        f"evaluation_{key}": evaluation[key]
                        for key in ("wins", "losses", "draws", "truncated")
                    },
                )
            history.append(point)
            record_improvement = score >= 0 and score > best_score
            if score >= best_score:
                best_score = score
                # Clone: CPU detach alone would still share storage with the
                # parameters that the next optimization iteration mutates.
                best_snapshot = {
                    "state_dict": {
                        key: value.detach().cpu().clone()
                        for key, value in model.state_dict().items()
                    },
                    "metadata": deepcopy(current_metadata()),
                }
                save_checkpoint(best_checkpoint, model, best_snapshot["metadata"])
                previous = deepcopy(model).eval()
                if config["save_all_best"] and record_improvement:
                    retained = output / "best" / f"iteration-{iteration:06d}.pt"
                    save_checkpoint(retained, model, best_snapshot["metadata"])
                    registry = ModelRegistry(config.get("registry_dir", output.parent / "models"))
                    registered_best = registry.register(retained, name=config["model_name"])
                    best_models.append(
                        {
                            "iteration": iteration,
                            "score": score,
                            "checkpoint": str(retained),
                            "model_id": registered_best["id"],
                        }
                    )
            if iteration % config["checkpoint_frequency"] == 0 or iteration == config["iterations"]:
                checkpoint_now(checkpoint)
            publish(
                stage="iteration_complete",
                iteration=iteration,
                games_generated=games_generated,
                policy_loss=metrics["policy_loss"],
                value_loss=metrics["value_loss"],
                checkpoint=str(checkpoint) if checkpoint.exists() else None,
                metrics=metrics,
            )
    except InterruptedError:
        checkpoint_now(checkpoint)
        publish(status="cancelled", stage="cancelled", checkpoint=str(checkpoint))
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
        publish(status=status, stage=status, error=str(error))
        raise
    if not checkpoint.exists():
        checkpoint_now(checkpoint)
    if not best_checkpoint.exists():
        raise ValueError("Training completed without a best-model snapshot")
    registry_root = Path(config.get("registry_dir", output.parent / "models"))
    inference_checkpoint = export_checkpoint(best_checkpoint, output / "inference.pt")
    registered = ModelRegistry(registry_root).register(
        inference_checkpoint, name=config["model_name"]
    )
    publish(
        status="completed",
        stage="completed",
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
    latest = {
        "status": "running",
        "completed_trials": len(study.trials),
        "trials_requested": trials,
    }

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

        def report(state):
            latest.update(trial=trial.number, trial_progress=state)
            _write_json(output / "progress.json", latest)
            if progress:
                progress(dict(latest))
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
