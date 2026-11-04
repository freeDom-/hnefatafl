"""Optional online learning-rate adaptation for self-play training.

This is a conservative, single-hyperparameter (learning rate) implementation of
HyperController (Gornet, Kantaros, Sinopoli, arXiv:2504.19382). HyperController
models the hyperparameter objective as an unknown Linear Gaussian Dynamical
System and learns a small linear predictor of the next objective value from a
short window of recent observations, conditioned on the recent sequence of
chosen values. It adapts inside one continuing training trajectory and needs no
population orchestration or restarts, which is why it fits a single self-play
job better than PBT/PB2/Hyperband.

The implementation here deliberately differs from the paper and the reference
code (github.com/jongornet14/HyperController) in a few ways that are documented
in ``docs/designs/online-hyperparameter-adaptation.md``:

* Only one hyperparameter (the learning rate) is adapted, over a small explicit
  candidate set, so no discretization of a continuous space is needed.
* Exploration is explicit (each candidate is tried ``adaptation_min_trials``
  times, then ties break toward the lowest index). The reference leaves its
  exploration perturbation commented out, and a pure greedy rule can freeze on
  the first learned context.
* The objective is the completed-game score against one fixed, versioned
  opponent. Unfinished games are excluded rather than scored as losses, and the
  observation is only supplied at evaluation boundaries. This is not the same
  quantity the promotion rule optimizes.
* All state is JSON-serializable primitive values, bounded in size and stored
  inside the existing tensor-only training checkpoint.

The controller never touches the network architecture, the optimizer's identity
or its momentum buffers: it only writes ``param_groups[*]["lr"]`` at iteration
boundaries.
"""

from __future__ import annotations

import math
import random
import time
from typing import Any

#: The only accepted values of the ``adaptation`` training option.
ALGORITHMS = ("off", "hypercontroller")

#: Version of the serialized controller state.
STATE_VERSION = 1

#: Bounds that keep the learned state small and the decisions cheap.
MIN_CANDIDATES = 2
MAX_CANDIDATES = 8
MAX_LAG = 3
#: ``candidates ** (lag + 1)`` contexts is the hard upper bound on learned
#: predictors; the derived limit below keeps every accepted configuration well
#: inside the checkpoint size budget. Lazy allocation means a run only ever
#: stores the contexts it actually visits.
MAX_CONTEXTS = 512
MAX_EVENTS = 100
MAX_REWARD_HISTORY = 64
RIDGE = 1e-6

#: Default candidate set: roughly half, equal to and triple the shipped 0.001.
DEFAULT_CANDIDATES = "0.0003,0.001,0.003"

ADAPTATION_FIELDS = (
    "adaptation",
    "adaptation_lr_candidates",
    "adaptation_cadence",
    "adaptation_warmup",
    "adaptation_min_dwell",
    "adaptation_lag",
    "adaptation_min_trials",
)


def adaptation_defaults() -> dict[str, Any]:
    """The default adaptation inputs, merged into the training defaults."""
    return {
        "adaptation": "off",
        "adaptation_lr_candidates": DEFAULT_CANDIDATES,
        "adaptation_cadence": 1,
        "adaptation_warmup": 2,
        "adaptation_min_dwell": 1,
        "adaptation_lag": 1,
        "adaptation_min_trials": 1,
    }


def format_candidates(values: list[float]) -> str:
    """Canonical comma-separated form, stable across a save/load round-trip."""
    return ",".join(f"{value:.10g}" for value in values)


def parse_candidates(value: Any) -> list[float]:
    """Parse and validate the candidate list from its scalar transport form."""
    if isinstance(value, (list, tuple)):
        items: list[Any] = list(value)
    elif isinstance(value, str):
        items = [item.strip() for item in value.split(",")]
    else:
        raise ValueError(
            "adaptation_lr_candidates must be a comma-separated list of learning rates"
        )
    candidates: list[float] = []
    for item in items:
        if item == "":
            continue
        if isinstance(item, bool):
            raise ValueError("adaptation_lr_candidates must contain numbers")
        try:
            number = float(item)
        except (TypeError, ValueError) as error:
            raise ValueError("adaptation_lr_candidates must contain numbers") from error
        if not math.isfinite(number) or not 1e-7 <= number <= 1.0:
            raise ValueError("adaptation_lr_candidates must be between 1e-7 and 1")
        if number not in candidates:
            candidates.append(number)
    if not MIN_CANDIDATES <= len(candidates) <= MAX_CANDIDATES:
        raise ValueError(
            f"adaptation_lr_candidates must contain between {MIN_CANDIDATES} and "
            f"{MAX_CANDIDATES} distinct learning rates"
        )
    candidates.sort()
    return candidates


def normalize_adaptation(full_config: dict) -> dict:
    """Validate the adaptation inputs and return their canonical scalar values.

    ``adaptation_lr_candidates`` stays a scalar string in the normalized config
    so the value transports like every other training parameter; the controller
    parses it once when it is constructed.
    """
    values = {
        **adaptation_defaults(),
        **{key: full_config[key] for key in ADAPTATION_FIELDS if key in full_config},
    }
    algorithm = values["adaptation"]
    if algorithm not in ALGORITHMS:
        raise ValueError("adaptation must be off or hypercontroller")
    candidates = parse_candidates(values["adaptation_lr_candidates"])
    for key, low, high in (
        ("adaptation_cadence", 1, 10000),
        ("adaptation_warmup", 0, 10000),
        ("adaptation_min_dwell", 1, 10000),
        ("adaptation_lag", 1, MAX_LAG),
        ("adaptation_min_trials", 0, 100),
    ):
        value = values[key]
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f"{key} must be an integer between {low} and {high}")
    lag = values["adaptation_lag"]
    if len(candidates) ** (lag + 1) > MAX_CONTEXTS:
        raise ValueError(
            "adaptation_lr_candidates and adaptation_lag produce too many controller "
            f"contexts (limit {MAX_CONTEXTS}); use fewer candidates or a smaller lag"
        )
    if algorithm != "off":
        if full_config.get("evaluation_opponent", "shield_bearer") == "champion":
            raise ValueError(
                "adaptation requires a fixed evaluation opponent; Champion moves and "
                "makes observations incomparable"
            )
        if int(full_config.get("evaluation_games", 2)) < 4:
            raise ValueError(
                "adaptation requires evaluation_games of at least 4 so a score is informative"
            )
        learning_rate = float(full_config.get("learning_rate", 0.001))
        if learning_rate not in candidates:
            raise ValueError(
                "learning_rate must be one of the adaptation_lr_candidates while "
                "adaptation is enabled"
            )
    normalized = dict(values)
    normalized["adaptation_lr_candidates"] = format_candidates(candidates)
    return normalized


def candidates_from_config(config: dict) -> list[float]:
    return parse_candidates(config.get("adaptation_lr_candidates", DEFAULT_CANDIDATES))


def observation_from_evaluation(evaluation: Any) -> tuple[float | None, dict[str, Any]]:
    """Turn one evaluation into a bounded scalar observation plus its provenance.

    Completed games contribute; rules draws count half; unfinished (truncated)
    games are excluded instead of being scored as losses. The promotion rule uses
    a different, one-sided bound, so this is an honest but separate signal. An
    evaluation with no completed game yields ``None`` (a missing observation).
    """
    if not isinstance(evaluation, dict):
        return None, {}
    games = evaluation.get("games")
    if not isinstance(games, list) or not games:
        return None, {}
    wins = draws = losses = truncated = 0
    for game in games:
        if not isinstance(game, dict):
            continue
        winner = game.get("winner")
        side = game.get("candidate_side")
        if winner is None:
            truncated += 1
        elif winner == "draw":
            draws += 1
        elif winner == side:
            wins += 1
        else:
            losses += 1
    completed = wins + draws + losses
    provenance = {
        "opponent": evaluation.get("opponent"),
        "opponent_identity": evaluation.get("opponent_identity"),
        "iteration": evaluation.get("iteration"),
        "completed_games": completed,
        "truncated_games": truncated,
        "wins": wins,
        "draws": draws,
        "losses": losses,
    }
    if completed == 0:
        return None, provenance
    score = (wins + 0.5 * draws) / completed
    provenance["score"] = score
    return score, provenance


def _solve(matrix: list[list[float]], vector: list[float]) -> list[float] | None:
    """Solve a small dense system with partial pivoting, in plain Python.

    ``lag`` is at most :data:`MAX_LAG`, so this is a handful of arithmetic
    operations and needs no numerical dependency. The controller runs on the CPU
    and never queries CUDA.
    """
    size = len(vector)
    augmented = [list(matrix[row]) + [vector[row]] for row in range(size)]
    for column in range(size):
        pivot = max(range(column, size), key=lambda row: abs(augmented[row][column]))
        if abs(augmented[pivot][column]) < 1e-12:
            return None
        augmented[column], augmented[pivot] = augmented[pivot], augmented[column]
        inverse = 1.0 / augmented[column][column]
        for row in range(size):
            if row == column:
                continue
            factor = augmented[row][column] * inverse
            if factor:
                for index in range(column, size + 1):
                    augmented[row][index] -= factor * augmented[column][index]
    return [augmented[row][size] / augmented[row][row] for row in range(size)]


def _rng_state(rng: random.Random) -> list[Any]:
    version, state, gauss = rng.getstate()
    return [version, list(state), gauss]


def _restore_rng(state: Any, seed: int) -> random.Random:
    rng = random.Random(seed)
    try:
        version, values, gauss = state
        rng.setstate((int(version), tuple(int(value) for value in values), gauss))
    except (TypeError, ValueError, IndexError):
        pass
    return rng


class HyperController:
    """Bounded online learning-rate controller with serializable state.

    The controller is deliberately inert until ``warmup`` evaluations have been
    observed. It only *selects* an index; the training loop owns writing the
    learning rate into the optimizer's parameter groups, so optimizer momentum is
    never rebuilt.
    """

    def __init__(
        self,
        *,
        candidates: list[float],
        lag: int,
        cadence: int,
        warmup: int,
        min_dwell: int,
        min_trials: int,
        initial_index: int,
        seed: int,
        evaluation_identity: str,
    ) -> None:
        if not MIN_CANDIDATES <= len(candidates) <= MAX_CANDIDATES:
            raise ValueError("Invalid candidate count")
        if not 0 <= initial_index < len(candidates):
            raise ValueError("Initial candidate index is out of range")
        self.candidates = list(candidates)
        self.lag = lag
        self.cadence = cadence
        self.warmup = warmup
        self.min_dwell = min_dwell
        self.min_trials = min_trials
        self.evaluation_identity = evaluation_identity
        self.effective_index = initial_index
        self.rng = random.Random(seed)
        self.rewards: list[float] = []
        self.actions: list[int] = []
        self.counts = [0] * len(self.candidates)
        # Keyed by "candidate|context..." with the same ``lag`` shape for every
        # entry, so the state-size bound is ``candidates ** (lag + 1)``.
        self.contexts: dict[str, dict[str, Any]] = {}
        self.observations = 0
        self.decisions = 0
        self.changes = 0
        self.last_decision_observation = 0
        self.last_change_observation = 0
        self.limit_hits = 0
        self.events: list[dict[str, Any]] = []
        self.reset_reason: str | None = None

    # -- public interface -------------------------------------------------

    @property
    def effective_learning_rate(self) -> float:
        return self.candidates[self.effective_index]

    def summary(self) -> dict[str, Any]:
        """A bounded live projection for progress files and monitoring."""
        return {
            "algorithm": "hypercontroller",
            "state_version": STATE_VERSION,
            "candidates": list(self.candidates),
            "lag": self.lag,
            "cadence": self.cadence,
            "warmup": self.warmup,
            "min_dwell": self.min_dwell,
            "min_trials": self.min_trials,
            "effective_index": self.effective_index,
            "effective_learning_rate": self.effective_learning_rate,
            "observations": self.observations,
            "decisions": self.decisions,
            "changes": self.changes,
            "last_decision_observation": self.last_decision_observation,
            "last_change_observation": self.last_change_observation,
            "evaluation_identity": self.evaluation_identity,
            "contexts": len(self.contexts),
            "limit_hits": self.limit_hits,
            "reset_reason": self.reset_reason,
            "last_event": self.events[-1] if self.events else None,
        }

    def observe(
        self, *, iteration: int, score: float | None, provenance: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Ingest the observation for the interval that just finished.

        The action credited is the learning rate that was active during that
        interval. A missing or non-finite score is recorded but never advances a
        counter, so warm-up, cadence and dwell cannot be satisfied by noise.
        """
        started = time.monotonic()
        provenance = dict(provenance or {})
        if score is None or not math.isfinite(float(score)):
            event = {
                "iteration": iteration,
                "observation": None,
                "provenance": provenance,
                "changed": False,
                "skipped": "missing_observation",
                "previous_index": self.effective_index,
                "effective_index": self.effective_index,
                "effective_learning_rate": self.effective_learning_rate,
                "observations": self.observations,
            }
            self._record_event(event, started)
            return event
        reward = min(1.0, max(0.0, float(score)))
        action = self.effective_index
        self.rewards.append(reward)
        self.actions.append(action)
        self.counts[action] += 1
        self.observations += 1
        if len(self.rewards) > MAX_REWARD_HISTORY:
            self.rewards = self.rewards[-MAX_REWARD_HISTORY:]
            self.actions = self.actions[-MAX_REWARD_HISTORY:]
        try:
            self._learn()
        except (ValueError, TypeError, ZeroDivisionError):
            # A malformed window must never abort training; skip this update.
            pass
        event = self._decide(iteration, reward, provenance)
        self._record_event(event, started)
        return event

    def _decide(self, iteration: int, reward: float, provenance: dict[str, Any]) -> dict[str, Any]:
        event: dict[str, Any] = {
            "iteration": iteration,
            "observation": round(reward, 6),
            "provenance": provenance,
            "previous_index": self.effective_index,
            "effective_index": self.effective_index,
            "effective_learning_rate": self.effective_learning_rate,
            "changed": False,
            "skipped": None,
            "observations": self.observations,
        }
        if self.observations < self.warmup:
            event["skipped"] = "warmup"
            return event
        if self.observations - self.last_decision_observation < self.cadence:
            event["skipped"] = "cadence"
            return event
        if self.observations - self.last_change_observation < self.min_dwell:
            event["skipped"] = "dwell"
            return event
        self.last_decision_observation = self.observations
        self.decisions += 1
        try:
            chosen = self._select()
        except (ValueError, TypeError, ZeroDivisionError):
            event["skipped"] = "fallback"
            return event
        event["selected_index"] = chosen
        if chosen != self.effective_index:
            self.effective_index = chosen
            self.last_change_observation = self.observations
            self.changes += 1
            event["changed"] = True
        event["effective_index"] = self.effective_index
        event["effective_learning_rate"] = self.effective_learning_rate
        return event

    def _select(self) -> int:
        size = len(self.candidates)
        untried = [index for index in range(size) if self.counts[index] < self.min_trials]
        if untried:
            return untried[self.rng.randrange(len(untried))]
        if len(self.rewards) < self.lag:
            return self.rng.randrange(size)
        context = tuple(self.actions[-self.lag :])
        window = self.rewards[-self.lag :]
        best_index, best_value = 0, None
        for index in range(size):
            value = self._prediction(index, context, window)
            if best_value is None or value > best_value:
                best_index, best_value = index, value
        return best_index

    def _learn(self) -> None:
        if len(self.rewards) < self.lag + 1:
            return
        target = len(self.rewards) - 1
        context = tuple(self.actions[target - self.lag : target])
        window = self.rewards[target - self.lag : target]
        action = self.actions[target]
        if len(context) != self.lag or len(window) != self.lag:
            return
        self._update(action, context, window, self.rewards[target])

    def _update(
        self, action: int, context: tuple[int, ...], window: list[float], reward: float
    ) -> None:
        key = self._key(action, context)
        entry = self.contexts.get(key)
        if entry is None:
            if len(self.contexts) >= MAX_CONTEXTS:
                self.limit_hits += 1
                return
            entry = {
                "V": [
                    [RIDGE if row == column else 0.0 for column in range(self.lag)]
                    for row in range(self.lag)
                ],
                "b": [0.0] * self.lag,
                "n": 0,
            }
            self.contexts[key] = entry
        matrix = entry["V"]
        vector = entry["b"]
        for row in range(self.lag):
            for column in range(self.lag):
                matrix[row][column] += window[row] * window[column]
            vector[row] += reward * window[row]
        entry["n"] += 1

    def _prediction(self, action: int, context: tuple[int, ...], window: list[float]) -> float:
        entry = self.contexts.get(self._key(action, context))
        if entry is None:
            return 0.0
        solution = _solve(entry["V"], entry["b"])
        if solution is None:
            return 0.0
        return float(sum(coefficient * value for coefficient, value in zip(solution, window)))

    def _key(self, action: int, context: tuple[int, ...]) -> str:
        return f"{action}:" + ",".join(str(value) for value in context)

    def _record_event(self, event: dict[str, Any], started: float) -> None:
        event["seconds"] = round(time.monotonic() - started, 6)
        self.events.append(event)
        if len(self.events) > MAX_EVENTS:
            self.events = self.events[-MAX_EVENTS:]

    # -- serialization ----------------------------------------------------

    def state_dict(self) -> dict[str, Any]:
        return {
            "version": STATE_VERSION,
            "algorithm": "hypercontroller",
            "candidates": list(self.candidates),
            "lag": self.lag,
            "cadence": self.cadence,
            "warmup": self.warmup,
            "min_dwell": self.min_dwell,
            "min_trials": self.min_trials,
            "evaluation_identity": self.evaluation_identity,
            "effective_index": self.effective_index,
            "observations": self.observations,
            "decisions": self.decisions,
            "changes": self.changes,
            "last_decision_observation": self.last_decision_observation,
            "last_change_observation": self.last_change_observation,
            "counts": list(self.counts),
            "rewards": list(self.rewards),
            "actions": list(self.actions),
            "contexts": {
                key: {
                    "V": [list(row) for row in entry["V"]],
                    "b": list(entry["b"]),
                    "n": entry["n"],
                }
                for key, entry in self.contexts.items()
            },
            "rng": _rng_state(self.rng),
            "events": list(self.events),
            "limit_hits": self.limit_hits,
            "reset_reason": self.reset_reason,
        }

    @classmethod
    def from_state(
        cls,
        state: Any,
        *,
        candidates: list[float],
        lag: int,
        cadence: int,
        warmup: int,
        min_dwell: int,
        min_trials: int,
        initial_index: int,
        seed: int,
        evaluation_identity: str,
    ) -> "HyperController":
        """Restore a controller, resetting when the learned state is incomparable.

        A saved candidate set, lag or evaluation identity that no longer matches
        cannot be reused: the learned predictors describe different actions or a
        different objective. In that case a fresh controller is returned with
        ``reset_reason`` set so the reset is visible in monitoring.
        """
        controller = cls(
            candidates=candidates,
            lag=lag,
            cadence=cadence,
            warmup=warmup,
            min_dwell=min_dwell,
            min_trials=min_trials,
            initial_index=initial_index,
            seed=seed,
            evaluation_identity=evaluation_identity,
        )
        if not isinstance(state, dict) or not state:
            return controller
        if state.get("version") != STATE_VERSION or state.get("algorithm") != "hypercontroller":
            controller.reset_reason = "unsupported_state"
            return controller
        saved_candidates = state.get("candidates")
        if saved_candidates != candidates:
            controller.reset_reason = "candidates_changed"
            return controller
        if state.get("lag") != lag:
            controller.reset_reason = "lag_changed"
            return controller
        if state.get("evaluation_identity") != evaluation_identity:
            controller.reset_reason = "evaluation_changed"
            return controller
        try:
            contexts: dict[str, dict[str, Any]] = {}
            for key, entry in state.get("contexts", {}).items():
                matrix = [[float(value) for value in row] for row in entry["V"]]
                vector = [float(value) for value in entry["b"]]
                if len(matrix) != lag or any(len(row) != lag for row in matrix):
                    raise ValueError("Invalid controller matrix")
                if len(vector) != lag:
                    raise ValueError("Invalid controller vector")
                contexts[str(key)] = {"V": matrix, "b": vector, "n": int(entry.get("n", 0))}
            rewards = [float(value) for value in state.get("rewards", [])][-MAX_REWARD_HISTORY:]
            actions = [int(value) for value in state.get("actions", [])][-MAX_REWARD_HISTORY:]
            if len(rewards) != len(actions) or any(
                not 0 <= value < len(candidates) for value in actions
            ):
                raise ValueError("Invalid controller history")
            counts = [int(value) for value in state.get("counts", [])]
            if len(counts) != len(candidates):
                counts = [0] * len(candidates)
            effective_index = int(state.get("effective_index", initial_index))
            if not 0 <= effective_index < len(candidates):
                raise ValueError("Invalid effective index")
            controller.contexts = contexts
            controller.rewards = rewards
            controller.actions = actions
            controller.counts = counts
            controller.effective_index = effective_index
            controller.observations = max(0, int(state.get("observations", 0)))
            controller.decisions = max(0, int(state.get("decisions", 0)))
            controller.changes = max(0, int(state.get("changes", 0)))
            controller.last_decision_observation = max(
                0, int(state.get("last_decision_observation", 0))
            )
            controller.last_change_observation = max(
                0, int(state.get("last_change_observation", 0))
            )
            controller.limit_hits = max(0, int(state.get("limit_hits", 0)))
            controller.events = [
                event for event in state.get("events", []) if isinstance(event, dict)
            ][-MAX_EVENTS:]
            controller.rng = _restore_rng(state.get("rng"), seed)
        except (KeyError, TypeError, ValueError, IndexError):
            controller.reset_reason = "unreadable_state"
            return controller
        return controller


def resolve_controller(
    config: dict,
    *,
    saved_state: Any,
    evaluation_identity: str,
    seed: int,
    resume: bool,
) -> HyperController | None:
    """Build the controller for a run, or ``None`` when adaptation is off.

    A resume whose evaluation identity differs from the saved one resets the
    learned state inside :meth:`HyperController.from_state`, so observations from
    a different objective are never mixed.
    """
    if config.get("adaptation", "off") == "off":
        return None
    candidates = candidates_from_config(config)
    learning_rate = float(config.get("learning_rate", candidates[0]))
    initial_index = candidates.index(learning_rate) if learning_rate in candidates else 0
    if resume and isinstance(saved_state, dict) and saved_state:
        return HyperController.from_state(
            saved_state,
            candidates=candidates,
            lag=int(config["adaptation_lag"]),
            cadence=int(config["adaptation_cadence"]),
            warmup=int(config["adaptation_warmup"]),
            min_dwell=int(config["adaptation_min_dwell"]),
            min_trials=int(config["adaptation_min_trials"]),
            initial_index=initial_index,
            seed=seed,
            evaluation_identity=evaluation_identity,
        )
    return HyperController(
        candidates=candidates,
        lag=int(config["adaptation_lag"]),
        cadence=int(config["adaptation_cadence"]),
        warmup=int(config["adaptation_warmup"]),
        min_dwell=int(config["adaptation_min_dwell"]),
        min_trials=int(config["adaptation_min_trials"]),
        initial_index=initial_index,
        seed=seed,
        evaluation_identity=evaluation_identity,
    )
