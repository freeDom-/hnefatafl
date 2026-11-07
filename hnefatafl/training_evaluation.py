"""Single-opponent training evaluation and portable, immutable selection state.

Promotion uses a one-sided Hoeffding bound on independent, side-swapped seed
pairs, not on individual games. This deliberately conservative rule needs more
than a small apparent lead and rejects any unfinished match. It is evidence for
this search protocol, not a claim of strength against every possible opponent.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
import time
from typing import TYPE_CHECKING, Callable

from .ai import choose_move
from .engine import apply_move, new_game
from .models import PolicyValueNet

if TYPE_CHECKING:
    from .training_monitoring import ProgressSink


def evaluation_context(config: dict) -> dict:
    """All effective comparison settings; irrelevant controls do not split series."""
    opponent = config.get("evaluation_opponent", "shield_bearer")
    seconds = config.get("evaluation_time_limit", config["mcts_time_limit"])
    neural = {
        "algorithm": "neural",
        "search_wrapper": "mcts",
        "iterations": config["evaluation_iterations"],
        "time_limit": seconds,
        "rollout_depth": 0,
        "exploration": 1.4,
        "temperature": 0.0,
        "dirichlet_fraction": 0.0,
    }
    neural_opponent = opponent in ("champion", "frozen_model")
    opponent_config = (
        dict(neural)
        if neural_opponent
        else {
            "algorithm": "alpha_beta"
            if opponent == "jarl"
            else "shield_maiden"
            if opponent == "shield_maiden"
            else "baseline",
            **({"heuristic_version": 1} if opponent == "shield_maiden" else {}),
            "time_limit": config.get("evaluation_jarl_time_limit", 2.0)
            if opponent == "jarl"
            else seconds,
            **({"depth": config.get("evaluation_jarl_depth", 5)} if opponent == "jarl" else {}),
        }
    )
    from .evaluation_play import evaluation_backend

    context = {
        "protocol_version": 1,
        "ruleset": config["ruleset"],
        "rules_version": 1,
        "opponent": opponent,
        "opponent_config": opponent_config,
        "candidate_config": neural,
        "games": config["evaluation_games"],
        "max_plies": config["evaluation_max_plies"],
        "seed": config["seed"] + 1000000,
        "scoring": "wins_plus_half_rules_draws_truncated_zero",
        "pairing": "same_seed_swapped_sides",
        # Retain historical PyTorch identities; an alternate numerical backend
        # starts a distinct comparison series.
        **({"inference_backend": "openvino"} if evaluation_backend(config) == "openvino" else {}),
    }
    # A fixed neural opponent is pinned by its registry identity so two different
    # frozen models never share a comparison series or benchmark slot.
    frozen_model_id = config.get("evaluation_frozen_model_id")
    if frozen_model_id:
        context["frozen_model_id"] = str(frozen_model_id)
    return context


def context_identity(context: dict) -> str:
    digest = hashlib.sha256(json.dumps(context, sort_keys=True).encode()).hexdigest()[:16]
    return f"{context['opponent']}:{digest}"


def snapshot(model: PolicyValueNet, metadata: dict) -> dict:
    return {
        "state_dict": {
            key: value.detach().cpu().clone() for key, value in model.state_dict().items()
        },
        "metadata": deepcopy(metadata),
    }


def frozen_model(model: PolicyValueNet, saved: dict) -> PolicyValueNet:
    frozen = deepcopy(model).eval()
    frozen.load_state_dict(saved["state_dict"], strict=True)
    frozen.requires_grad_(False)
    return frozen


def champion_identity(champion: dict | None) -> str | None:
    """A stable, bounded identity for one Champion regime.

    The identity is derived from persisted metadata (the promotion iteration, the
    provisional flag and the provenance), so it is deterministic across a resume
    and needs no model hashing. The controller stores it to verify that restored
    observations belong to the same Champion; a promotion produces a new value.
    Untrusted persisted metadata is coerced to a bounded value so a malformed
    checkpoint cannot produce an oversized identity.
    """
    if not champion:
        return None
    identity = champion.get("identity")
    if isinstance(identity, str) and 0 < len(identity) <= 200:
        return identity
    iteration = champion.get("iteration", 0)
    if type(iteration) is not int or iteration < 0:
        iteration = 0
    state = "provisional" if champion.get("provisional") else "promoted"
    source = champion.get("source", "unknown")
    if not isinstance(source, str) or not source or len(source) > 64:
        source = "unknown"
    return f"champion-{iteration}-{state}-{source}"


def _summary(games: list[dict]) -> dict:
    wins = sum(game["winner"] == game["candidate_side"] for game in games)
    draws = sum(game["winner"] == "draw" for game in games)
    truncated = sum(game["winner"] is None for game in games)
    return {
        "wins": wins,
        "losses": len(games) - wins - draws - truncated,
        "draws": draws,
        "truncated": truncated,
        "game_count": len(games),
        "score": (wins + draws / 2) / len(games) if games else 0.0,
    }


def promotion_decision(evaluation: dict, config: dict) -> dict:
    games = evaluation["games"]
    pairs = [games[index : index + 2] for index in range(0, len(games), 2)]
    valid = bool(pairs) and all(
        len(pair) == 2
        and [game["candidate_side"] for game in pair] == ["attackers", "defenders"]
        and all(game["winner"] is not None for game in pair)
        for pair in pairs
    )
    count = len(pairs)
    confidence = config["champion_confidence"]
    lower = (
        max(
            0.0,
            evaluation["score"] - math.sqrt(math.log(1 / (1 - confidence)) / (2 * count)),
        )
        if count
        else 0.0
    )
    enough = count >= config["champion_min_pairs"]
    promoted = valid and enough and lower > 0.5 + config["champion_min_improvement"]
    return {
        "promoted": promoted,
        "method": "paired_hoeffding_one_sided",
        "pairs": count,
        "confidence": confidence,
        "score_lower_bound": lower,
        "required_score": 0.5 + config["champion_min_improvement"],
        "reason": "improvement"
        if promoted
        else (
            "unfinished_pairs"
            if not valid
            else "insufficient_pairs"
            if not enough
            else "inconclusive"
        ),
    }


def evaluate_model(
    model: PolicyValueNet,
    config: dict,
    *,
    previous: PolicyValueNet | None = None,
    seed: int | None = None,
    should_stop: Callable[[], bool] | None = None,
    search_pool=None,
    monitor: ProgressSink | None = None,
) -> dict:
    """Evaluate exactly the selected opponent with equal neural budgets per side."""
    context = evaluation_context(config)
    neural_opponent = context["opponent"] in ("champion", "frozen_model")
    if neural_opponent and previous is None:
        raise ValueError("A neural opponent requires a compatible opponent model")
    if not neural_opponent and previous is not None:
        raise ValueError("A neural incumbent is only used for a neural opponent")
    if previous is not None and previous.ruleset != model.ruleset:
        raise ValueError(
            "Opponent ruleset and board/action representation must match the candidate"
        )
    if previous is not None:
        # Each model keeps its own architecture, inference backend and cache.
        previous.to(next(model.parameters()).device)
    seed = context["seed"] if seed is None else seed
    outcomes: list[dict] = []
    wins = draws = losses = 0
    model.eval()
    if previous is not None:
        previous.eval()
    # Imported lazily because training imports this module at import time.
    from .training import evaluation_uses_pool

    if evaluation_uses_pool(config):
        parallel_started = time.monotonic()
        from .parallel_search import parallel_evaluation

        outcomes, performance = parallel_evaluation(
            model,
            config,
            context,
            seed=seed,
            previous=previous,
            should_stop=should_stop,
            search_pool=search_pool,
            monitor=monitor,
        )
        return {
            **_summary(outcomes),
            "opponent": context["opponent"],
            "opponent_config": context["opponent_config"],
            "context": context,
            "opponent_identity": context_identity(context),
            "candidate_as_attacker": _summary(outcomes[::2]),
            "candidate_as_defender": _summary(outcomes[1::2]),
            "games": outcomes,
            "duration": time.monotonic() - parallel_started,
            "seed": seed,
            "performance": performance,
        }
    started = time.monotonic()
    if monitor is not None:
        monitor.counts(
            evaluation_opponent=context["opponent"],
            evaluation_total=config["evaluation_games"],
            evaluation_games=0,
            evaluation_wins=0,
            evaluation_draws=0,
            evaluation_losses=0,
        )
    from .evaluation_play import batched_evaluation, evaluation_backend

    backend = evaluation_backend(config)
    mode = config.get("evaluation_mode", "auto")
    batched = (
        mode == "batched"
        or backend == "openvino"
        or (mode == "auto" and next(model.parameters()).device.type == "cuda")
    )
    if batched:
        outcomes, performance = batched_evaluation(
            model,
            config,
            context,
            previous=previous,
            seed=seed,
            should_stop=should_stop,
            monitor=monitor,
        )
        return {
            **_summary(outcomes),
            "opponent": context["opponent"],
            "opponent_config": context["opponent_config"],
            "context": context,
            "opponent_identity": context_identity(context),
            "candidate_as_attacker": _summary(outcomes[::2]),
            "candidate_as_defender": _summary(outcomes[1::2]),
            "games": outcomes,
            "duration": time.monotonic() - started,
            "seed": seed,
            "performance": performance,
        }
    for game in range(config["evaluation_games"]):
        state = new_game(config["ruleset"])
        candidate_side = "attackers" if game % 2 == 0 else "defenders"
        pair_seed = seed + 1000 * (game // 2)
        for ply in range(config["evaluation_max_plies"]):
            if should_stop and should_stop():
                raise InterruptedError("Evaluation was cancelled")
            if state.winner:
                break
            candidate = state.turn == candidate_side
            ai_config = dict(
                context["candidate_config"] if candidate else context["opponent_config"]
            )
            if candidate or previous is not None:
                ai_config["_model"] = model if candidate else previous
            ai_config["seed"] = pair_seed + ply
            state = apply_move(state, choose_move(state, ai_config, should_stop=should_stop))
            if monitor is not None:
                # One cheap update per searched position keeps long games live.
                monitor.tick(evaluation_plies=1)
        outcomes.append(
            {
                "pair": game // 2,
                "seed": pair_seed,
                "candidate_side": candidate_side,
                "winner": state.winner,
                "outcome": "completed" if state.winner else "truncated",
                "score": 0.5 if state.winner == "draw" else float(state.winner == candidate_side),
                "plies": state.ply,
                "reason": state.reason or "evaluation_ply_limit",
            }
        )
        if monitor is not None:
            # Running, clearly partial tallies; the final result still comes from
            # _summary() over the same outcomes. Truncated games count as neither
            # a win nor a loss, matching the final scoring.
            winner = state.winner
            wins += int(winner == candidate_side)
            draws += int(winner == "draw")
            losses += int(winner not in (None, "draw") and winner != candidate_side)
            monitor.tick(evaluation_games=1)
            monitor.counts(
                evaluation_wins=wins,
                evaluation_draws=draws,
                evaluation_losses=losses,
            )
    return {
        **_summary(outcomes),
        "opponent": context["opponent"],
        "opponent_config": context["opponent_config"],
        "context": context,
        "opponent_identity": context_identity(context),
        "candidate_as_attacker": _summary(outcomes[::2]),
        "candidate_as_defender": _summary(outcomes[1::2]),
        "games": outcomes,
        "duration": time.monotonic() - started,
        "seed": seed,
    }


class EvaluationSelection:
    """Versioned state stored inside the existing tensor-only full checkpoint."""

    def __init__(
        self, extra: dict, saved_config: dict, config: dict, legacy_evaluation: dict | None = None
    ):
        self.data = deepcopy(extra.get("evaluation_state", {}))
        if self.data and self.data.get("version") != 1:
            raise ValueError("Unsupported training evaluation state version")
        if not self.data:
            self.data = {"version": 1, "benchmarks": {}, "champion": None, "promotions": []}
            legacy = extra.get("best_snapshot")
            if legacy:
                context = evaluation_context(
                    {
                        **config,
                        **saved_config,
                        "evaluation_time_limit": saved_config.get(
                            "evaluation_time_limit",
                            saved_config.get("mcts_time_limit", config["mcts_time_limit"]),
                        ),
                        "evaluation_opponent": "shield_bearer",
                    }
                )
                # Older jobs varied seeds by iteration and did not use swapped
                # seed pairs. Retain that result, but do not invent comparable
                # observations under the new protocol.
                context.update(
                    protocol_version=0, pairing="legacy_independent_games", seed="iteration_derived"
                )
                identity = context_identity(context)
                self.data["legacy_identity"] = identity
                self.data["benchmarks"][identity] = {
                    "score": extra.get("best_score", -1),
                    "iteration": legacy["metadata"].get("version", 0),
                    "snapshot": deepcopy(legacy),
                    "context": context,
                    "legacy": True,
                }
                if legacy_evaluation:
                    self.data["last_evaluation"] = {
                        **deepcopy(legacy_evaluation),
                        "iteration": extra.get("iteration", 0),
                        "opponent": "shield_bearer",
                        "opponent_identity": identity,
                        "context": context,
                        "opponent_config": context["opponent_config"],
                    }
        self.previous_identity = self.data.get("active_identity")
        self.context = evaluation_context(config)
        self.identity = context_identity(self.context)
        self.data["active_identity"] = self.identity

    def initialize_champion(
        self, model: PolicyValueNet, metadata: dict, legacy: dict | None
    ) -> None:
        if self.data["champion"] is None:
            old = self.data["benchmarks"].get(self.previous_identity, {})
            saved = old.get("snapshot") or legacy or snapshot(model, metadata)
            champion = {
                "snapshot": deepcopy(saved),
                "iteration": saved["metadata"].get("version", 0),
                "provisional": True,
                "source": "saved_best" if old or legacy else "initial_weights",
                # The champion's own score, so the selected checkpoint and the
                # reported objective always describe the same model state.
                "score": float(old.get("score", -1.0)),
            }
            champion["identity"] = champion_identity(champion)
            self.data["champion"] = champion

    def accept(self, model: PolicyValueNet, metadata: dict, evaluation: dict, config: dict) -> bool:
        iteration = metadata["version"]
        # Normalize even external/test evaluators to the effective protocol.
        evaluation.update(
            iteration=iteration,
            opponent=self.context["opponent"],
            opponent_config=self.context["opponent_config"],
            context=self.context,
            opponent_identity=self.identity,
        )
        improved = False
        if self.context["opponent"] == "champion":
            champion = self.data["champion"]
            evaluation["champion_iteration"] = champion["iteration"]
            evaluation["opponent_identity"] += f":incumbent-{champion['iteration']}"
            evaluation.update(promotion_decision(evaluation, config))
            if evaluation["promoted"]:
                improved = True
                champion = {
                    "snapshot": snapshot(model, metadata),
                    "iteration": iteration,
                    "provisional": False,
                    "source": "head_to_head",
                    "score": float(evaluation["score"]),
                }
                champion["identity"] = champion_identity(champion)
                self.data["champion"] = champion
                self.data["promotions"].append(deepcopy(evaluation))
        else:
            incumbent = self.data["benchmarks"].get(self.identity)
            improved = incumbent is None or evaluation["score"] > incumbent["score"]
            evaluation["benchmark_improved"] = improved
            evaluation["benchmark_saturated"] = evaluation["score"] >= 0.95
            # A useful uncertainty indicator even though fixed selection is strict-score based.
            count = config["evaluation_games"] // 2
            evaluation["score_lower_bound"] = max(
                0.0, evaluation["score"] - math.sqrt(math.log(20) / (2 * count))
            )
            if improved:
                self.data["benchmarks"][self.identity] = {
                    "score": evaluation["score"],
                    "iteration": iteration,
                    "snapshot": snapshot(model, metadata),
                    "context": self.context,
                }
        self.data["last_evaluation"] = deepcopy(evaluation)
        return improved

    def champion_identity(self) -> str | None:
        """Identity of the active Champion, or ``None`` for a fixed opponent."""
        if self.context["opponent"] != "champion":
            return None
        return champion_identity(self.data.get("champion"))

    def selected_snapshot(self) -> dict | None:
        if self.context["opponent"] == "champion":
            return self.data["champion"]["snapshot"]
        return self.data["benchmarks"].get(self.identity, {}).get("snapshot")

    def selected_score(self) -> float:
        """Score of the checkpoint ``selected_snapshot`` returns, or -1 if none.

        This is the only score a caller may associate with the selected
        checkpoint: a fixed benchmark's recorded score (inherited from a resumed
        checkpoint when no candidate improved) or a champion's promotion score.
        """
        if self.context["opponent"] == "champion":
            champion = self.data["champion"]
            return float(champion.get("score", -1.0)) if champion else -1.0
        entry = self.data["benchmarks"].get(self.identity)
        return float(entry["score"]) if entry else -1.0

    def selected_iteration(self) -> int | None:
        """Iteration that produced the checkpoint ``selected_snapshot`` returns."""
        if self.context["opponent"] == "champion":
            champion = self.data["champion"]
            return int(champion["iteration"]) if champion else None
        entry = self.data["benchmarks"].get(self.identity)
        return int(entry["iteration"]) if entry else None

    def selected_source(self, initial: dict | None) -> str:
        """Whether the selected checkpoint is inherited or a new candidate.

        ``initial`` is the snapshot selected before the first optimization
        iteration. A resumed checkpoint with a historical benchmark therefore
        stays ``inherited`` until a candidate actually replaces it.
        """
        current = self.selected_snapshot()
        if current is None:
            return "none"
        return "inherited" if current is initial else "candidate"

    def summary(self) -> dict:
        champion = self.data["champion"]
        return {
            "opponent": self.context["opponent"],
            "opponent_identity": self.identity,
            "context": self.context,
            "champion_iteration": champion["iteration"] if champion else None,
            "champion_provisional": champion["provisional"] if champion else None,
            "champion_identity": champion_identity(champion),
            "benchmarks": [
                {key: value for key, value in entry.items() if key != "snapshot"}
                | {"opponent_identity": identity}
                for identity, entry in self.data["benchmarks"].items()
            ],
            "last_evaluation": self.data.get("last_evaluation"),
        }
