"""Reproducible Phase 3 evidence: D4 consistency, augmentation cost and learning.

Four independent measurements, each reported per ruleset:

* ``consistency``: how far a network is from D4-equivariant on fixed positions.
* ``throughput``: OFF vs RANDOM_D4 optimizer cost on one frozen replay and
  identical starting weights.
* ``learning``: matched OFF/RANDOM_D4 training on one frozen replay, scored on
  *held-out* positions (original orientation and all eight orientations).
* ``strength``: paired side-swapped games between independently trained models.

Nothing here changes inference: predictions are never shared across
orientations, and small consistency gaps are measurements, not equivalences.
"""

from __future__ import annotations

import math
import platform
import random
import time
from copy import deepcopy
from typing import Any, Callable

import torch
from torch.nn import functional as F

from .engine import RULESETS, apply_move, legal_moves, new_game
from .models import PolicyValueNet, encode_state
from .symmetry import Symmetry, inverse, transform_state
from .training import generate_self_play, normalize_config, optimize_replay, train
from .training_augmentation import collate_batch, transform_example

TIE_TOLERANCE = 1e-6


def fixed_positions(ruleset: str, count: int, seed: int = 0) -> list:
    """Unfinished positions from seeded legal play plus the real opening position.

    Random play reaches captures, restricted-square tactics and asymmetric
    boards the symmetric initial position never shows.
    """
    rng = random.Random(f"symmetry-benchmark:{ruleset}:{seed}")
    positions = [new_game(ruleset)]
    while len(positions) < count:
        state = new_game(ruleset)
        for _ in range(rng.randrange(4, 70)):
            moves = legal_moves(state)
            if state.winner or not moves:
                break
            state = apply_move(state, rng.choice(moves))
        if not state.winner and legal_moves(state):
            positions.append(state)
    return positions


@torch.inference_mode()
def _legal_policy(
    model: PolicyValueNet, states: list
) -> tuple[list[dict[int, float]], list[float]]:
    inputs = torch.stack([encode_state(state) for state in states])
    logits, values = model(inputs)
    area = states[0].size ** 2
    policies = []
    for row, state in enumerate(states):
        ids = [m.source * area + m.target for m in legal_moves(state)]
        probabilities = torch.softmax(logits[row, ids], dim=0).tolist()
        policies.append(dict(zip(ids, probabilities)))
    return policies, values.reshape(-1).tolist()


def consistency(model: PolicyValueNet, states: list) -> dict:
    """Compare P with every T(P) after mapping the policy back to P's orientation."""
    from .training_augmentation import action_map

    model.eval()
    size = states[0].size
    base_policy, base_value = _legal_policy(model, states)
    per_symmetry = []
    total_tv = total_value = max_value = 0.0
    disagreements = comparisons = 0
    for symmetry in Symmetry:
        if symmetry == Symmetry.IDENTITY:
            continue
        moved = [transform_state(state, symmetry) for state in states]
        policy, value = _legal_policy(model, moved)
        back = action_map(size)[inverse(symmetry)]
        tv_sum = value_sum = 0.0
        for index in range(len(states)):
            mapped = {int(back[action]): p for action, p in policy[index].items()}
            if set(mapped) != set(base_policy[index]):  # impossible if rules are D4-safe
                raise AssertionError("Transformed legal moves do not map back to the original")
            tv = 0.5 * sum(abs(mapped[a] - p) for a, p in base_policy[index].items())
            difference = abs(value[index] - base_value[index])
            tv_sum += tv
            value_sum += difference
            max_value = max(max_value, difference)
            first = sorted(base_policy[index].values(), reverse=True)
            second = sorted(mapped.values(), reverse=True)
            # A top-move disagreement only counts when neither side is a near-tie.
            clear = (
                len(first) > 1
                and first[0] - first[1] > TIE_TOLERANCE
                and second[0] - second[1] > TIE_TOLERANCE
            )
            if clear:
                comparisons += 1
                best_base = max(base_policy[index].items(), key=lambda item: item[1])[0]
                best_mapped = max(mapped.items(), key=lambda item: item[1])[0]
                disagreements += best_base != best_mapped
        count = len(states)
        total_tv += tv_sum
        total_value += value_sum
        per_symmetry.append(
            {
                "symmetry": symmetry.name,
                "mean_policy_total_variation": tv_sum / count,
                "mean_value_difference": value_sum / count,
            }
        )
    samples = len(states) * (len(Symmetry) - 1)
    return {
        "positions": len(states),
        "mean_policy_total_variation": total_tv / samples,
        "mean_value_difference": total_value / samples,
        "max_value_difference": max_value,
        "top_move_disagreement": disagreements / comparisons if comparisons else None,
        "top_move_comparisons": comparisons,
        "per_symmetry": per_symmetry,
    }


def heldout_loss(model: PolicyValueNet, examples: list[dict], orientations: bool) -> dict:
    """Policy/value loss on positions never used to optimize the model."""
    model.eval()
    chosen = list(Symmetry) if orientations else [Symmetry.IDENTITY]
    policy_total = value_total = 0.0
    count = 0
    with torch.inference_mode():
        for symmetry in chosen:
            for start in range(0, len(examples), 256):
                batch = [transform_example(e, symmetry) for e in examples[start : start + 256]]
                inputs, target, mask, values = collate_batch(batch, "cpu")
                logits, predicted = model(inputs)
                logits = logits.masked_fill(~mask, -1e9)
                policy_total += float(-(target * F.log_softmax(logits, dim=1)).sum(dim=1).sum())
                value_total += float(((predicted.float() - values) ** 2).sum())
                count += len(batch)
    return {
        "policy_loss": policy_total / count,
        "value_loss": value_total / count,
        "samples": count,
    }


def _rss_bytes() -> int:
    with open("/proc/self/status", encoding="ascii") as status:
        for line in status:
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    return 0


def _replay(config: dict, seed: int, games: int, model: PolicyValueNet) -> list[dict]:
    run = {**config, "games": games, "self_play_mode": "sequential"}
    generated, _ = generate_self_play(model, run, seed=seed)
    return [example for examples, _ in generated for example in examples]


def _optimize(
    model: PolicyValueNet,
    replay: list[dict],
    config: dict,
    mode: str,
    seed: int,
    device: torch.device,
) -> dict:
    from .training_augmentation import augmentation_rng

    settings = {**config, "training_symmetry_mode": mode}
    optimizer = torch.optim.AdamW(
        model.parameters(), lr=settings["learning_rate"], weight_decay=settings["weight_decay"]
    )
    mixed = bool(settings["mixed_precision"] and device.type == "cuda")
    scaler = torch.amp.GradScaler("cuda", enabled=mixed)
    shuffle, augment = random.Random(seed), augmentation_rng(seed, 0)
    if device.type == "cuda":
        torch.cuda.reset_peak_memory_stats(device)
        torch.cuda.synchronize(device)
    started = time.perf_counter()
    result = optimize_replay(
        model, optimizer, scaler, replay, settings, device, mixed, shuffle, augment
    )
    if device.type == "cuda":
        torch.cuda.synchronize(device)
    seconds = time.perf_counter() - started
    stats = result["augmentation"]
    payload = {
        "mode": mode,
        "optimization_seconds": seconds,
        "updates": result["updates"],
        "samples": result["presented"],
        "updates_per_second": result["updates"] / seconds,
        "samples_per_second": result["presented"] / seconds,
        "augmentation_seconds": stats.seconds,
        "augmentation_share": stats.seconds / seconds,
        "transform_counts": list(stats.counts),
        "final_policy_loss": result["policy_losses"][-1],
        "final_value_loss": result["value_losses"][-1],
        "process_rss_bytes": _rss_bytes(),
    }
    if device.type == "cuda":
        payload["gpu_peak_allocated_bytes"] = torch.cuda.max_memory_allocated(device)
    return payload


def wilson(successes: float, total: int, z: float = 1.96) -> list[float]:
    if not total:
        return [0.0, 1.0]
    p = successes / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return [max(0.0, centre - margin), min(1.0, centre + margin)]


def run_symmetry_benchmark(
    configuration: dict, *, should_stop: Callable[[], bool] | None = None
) -> dict:
    """Run the configured experiments. See docs/benchmarks/symmetry-phase3.md."""
    allowed = {
        "rulesets",
        "training",
        "replay_games",
        "heldout_games",
        "epochs",
        "repetitions",
        "consistency_positions",
        "throughput_repetitions",
        "strength_iterations",
        "strength_games",
        "strength_replicates",
        "device",
        "seed",
        "sections",
    }
    if set(configuration) - allowed:
        raise ValueError("Unknown symmetry benchmark settings")
    rulesets = configuration.get("rulesets", ["brandubh", "tablut", "copenhagen"])
    if any(r not in RULESETS for r in rulesets):
        raise ValueError("Unknown ruleset")
    sections = set(
        configuration.get("sections", ["consistency", "throughput", "learning", "strength"])
    )
    device = torch.device(
        "cuda"
        if configuration.get("device", "cpu") == "cuda" and torch.cuda.is_available()
        else "cpu"
    )
    if configuration.get("device", "cpu") == "cuda" and device.type != "cuda":
        raise ValueError("CUDA was requested but is unavailable; no CUDA numbers were produced")
    seed = int(configuration.get("seed", 0))
    report: dict[str, Any] = {
        "environment": {
            "platform": platform.platform(),
            "torch": torch.__version__,
            "device": str(device),
            "cuda_device": torch.cuda.get_device_name(device) if device.type == "cuda" else None,
            "cpu_threads": torch.get_num_threads(),
        },
        "configuration": deepcopy(configuration),
        "rulesets": {},
    }
    for ruleset in rulesets:
        training = {
            "preset": "smoke",
            "ruleset": ruleset,
            "channels": 16,
            "blocks": 1,
            "mcts_iterations": 16,
            "mcts_time_limit": 30,
            "max_game_plies": 60,
            "batch_size": 32,
            "epochs": configuration.get("epochs", 10),
            "device": "cpu",
            "mixed_precision": False,
            "cpu_threads": torch.get_num_threads(),
            **configuration.get("training", {}),
            "seed": seed,
        }
        base = normalize_config(training)
        entry: dict[str, Any] = {"model": {"channels": base["channels"], "blocks": base["blocks"]}}
        torch.manual_seed(seed)
        reference = PolicyValueNet(ruleset, base["channels"], base["blocks"])
        # Replay and held-out data come from different seeds and different games.
        replay = heldout = None
        if sections & {"throughput", "learning"}:
            replay = _replay(base, seed + 11, configuration.get("replay_games", 6), reference)
            heldout = _replay(base, seed + 90001, configuration.get("heldout_games", 3), reference)
            entry["data"] = {"replay_positions": len(replay), "heldout_positions": len(heldout)}
        if should_stop and should_stop():
            raise InterruptedError("Symmetry benchmark cancelled")
        states = fixed_positions(ruleset, configuration.get("consistency_positions", 64), seed)
        if "consistency" in sections:
            entry["consistency_untrained"] = consistency(reference, states)
        if "throughput" in sections and replay:
            runs = []
            for repetition in range(configuration.get("throughput_repetitions", 3) + 1):
                order = ("off", "random_d4") if repetition % 2 == 0 else ("random_d4", "off")
                for mode in order:
                    model = deepcopy(reference).to(device)
                    result = _optimize(model, replay, base, mode, seed, device)
                    result["repetition"] = repetition - 1  # repetition -1 is warm-up
                    runs.append(result)
            runs = [r for r in runs if r["repetition"] >= 0]
            summary = {}
            for mode in ("off", "random_d4"):
                chosen = [r for r in runs if r["mode"] == mode]
                summary[mode] = {
                    key: sorted(r[key] for r in chosen)[len(chosen) // 2]
                    for key in (
                        "optimization_seconds",
                        "updates_per_second",
                        "samples_per_second",
                        "augmentation_seconds",
                        "augmentation_share",
                    )
                }
            summary["overhead_ratio"] = (
                summary["random_d4"]["optimization_seconds"]
                / summary["off"]["optimization_seconds"]
            )
            entry["throughput"] = {"runs": runs, "median": summary}
        if "learning" in sections and replay and heldout:
            learned = []
            for replicate in range(configuration.get("repetitions", 3)):
                torch.manual_seed(seed + 1000 + replicate)
                initial = PolicyValueNet(ruleset, base["channels"], base["blocks"])
                row: dict[str, Any] = {"replicate": replicate}
                for mode in ("off", "random_d4"):
                    model = deepcopy(initial).to(device)
                    row[mode] = {
                        "training": _optimize(model, replay, base, mode, seed + replicate, device)
                    }
                    model = model.cpu()
                    row[mode]["heldout_original"] = heldout_loss(model, heldout, False)
                    row[mode]["heldout_all_orientations"] = heldout_loss(model, heldout, True)
                    row[mode]["training_set_original"] = heldout_loss(model, replay, False)
                    row[mode]["consistency"] = consistency(model, states)
                learned.append(row)
                if should_stop and should_stop():
                    raise InterruptedError("Symmetry benchmark cancelled")
            entry["learning"] = learned
        if "strength" in sections:
            entry["strength"] = _strength(base, configuration, seed)
        report["rulesets"][ruleset] = entry
    return report


def _strength(base: dict, configuration: dict, seed: int) -> dict:
    """Independent OFF/RANDOM_D4 trainings, then side-swapped matched-seed games."""
    from .training_evaluation import evaluate_model

    games = configuration.get("strength_games", 8)
    iterations = configuration.get("strength_iterations", 3)
    replicates = configuration.get("strength_replicates", 1)
    import tempfile
    from pathlib import Path

    from .models import load_checkpoint

    totals = {"wins": 0, "losses": 0, "draws": 0, "unfinished": 0, "games": 0}
    detail = []
    for replicate in range(replicates):
        with tempfile.TemporaryDirectory() as directory:
            models = {}
            for mode in ("off", "random_d4"):
                result = train(
                    {
                        **base,
                        "iterations": iterations,
                        "training_symmetry_mode": mode,
                        "seed": seed + replicate,
                        "evaluation_games": 2,
                        "output_dir": str(Path(directory) / mode),
                        "registry_dir": str(Path(directory) / "models"),
                    }
                )
                models[mode], _ = load_checkpoint(result["checkpoint"])
            evaluation = evaluate_model(
                models["random_d4"],
                {
                    **base,
                    "evaluation_opponent": "champion",
                    "evaluation_games": games,
                    "evaluation_iterations": 16,
                    "evaluation_max_plies": 120,
                },
                previous=models["off"],
                seed=seed + 777 + replicate,
            )
        summary = {
            "wins": evaluation["wins"],
            "losses": evaluation["losses"],
            "draws": evaluation["draws"],
            "unfinished": evaluation["truncated"],
            "games": evaluation["game_count"],
        }
        detail.append({"replicate": replicate, **summary})
        for key, value in summary.items():
            totals[key] += value
    decided = totals["wins"] + totals["losses"] + totals["draws"]
    score = totals["wins"] + totals["draws"] / 2
    return {
        "treatment": "random_d4",
        "baseline": "off",
        "training_iterations": iterations,
        **totals,
        "score_among_completed": score / decided if decided else None,
        "score_wilson_95": wilson(score, decided),
        "replicates": detail,
        "note": "Functional smoke evidence; a small budget cannot establish strength.",
    }
