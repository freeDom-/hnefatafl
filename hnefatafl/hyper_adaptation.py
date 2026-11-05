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

#: Version of the serialized controller state. Version 1 stored the same learned
#: contexts but gated exploration on global per-candidate counts; version 2 gates
#: exploration per (candidate, context) and records the learned/coverage counters.
#: Version 1 is migrated on load; unknown versions reset only the controller state.
STATE_VERSION = 2
COMPATIBLE_STATE_VERSIONS = (1, 2)

#: Bounds that keep the learned state small and the decisions cheap.
MIN_CANDIDATES = 2
MAX_CANDIDATES = 8
MAX_LAG = 3
#: Longest scalar candidate transport accepted before it is split. A valid list
#: of at most eight exact floats is far shorter; the cap rejects oversized
#: untrusted input before any allocation or conversion happens.
MAX_CANDIDATES_TEXT = 512
#: Most entries accepted from a list/tuple transport before conversion. The
#: distinct-rate limit is 2..8, but duplicate and empty entries are still allowed
#: within this bound so reasonable existing configurations keep working.
MAX_CANDIDATES_ITEMS = 256
#: ``candidates ** (lag + 1)`` contexts is the hard upper bound on learned
#: predictors; the derived limit below keeps every accepted configuration well
#: inside the checkpoint size budget. Lazy allocation means a run only ever
#: stores the contexts it actually visits.
MAX_CONTEXTS = 512
MAX_EVENTS = 100
#: Number of decision events retained in the published progress snapshot. The
#: controller keeps up to :data:`MAX_EVENTS`; the training loop exposes this tail
#: so the persisted progress stays bounded and never grows with the run.
ADAPTATION_EVENT_TAIL = 50
MAX_REWARD_HISTORY = 64
RIDGE = 1e-6

#: Accepted decision-event fields. The v2 schema is the primary one; the v1
#: ``previous_index``/``effective_index``/``effective_learning_rate`` fields are
#: accepted for checkpoints written by the earlier state version and are validated
#: the same way. Any other field is rejected as unexpected nested data.
EVENT_KEYS = frozenset(
    {
        "iteration",
        "observation",
        "provenance",
        "attributed_learning_rate",
        "previous_learning_rate",
        "selected_learning_rate",
        "selected_index",
        "changed",
        "skipped",
        "observations",
        "seconds",
        # Documented v1 fields.
        "previous_index",
        "effective_index",
        "effective_learning_rate",
    }
)
PROVENANCE_KEYS = frozenset(
    {
        "opponent",
        "opponent_identity",
        "iteration",
        "completed_games",
        "truncated_games",
        "wins",
        "draws",
        "losses",
        "score",
    }
)
SKIP_REASONS = frozenset({"warmup", "cadence", "dwell", "fallback", "missing_observation"})
#: Longest accepted provenance string (opponent name or identity).
MAX_PROVENANCE_TEXT = 200

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
    """Canonical comma-separated form that round-trips every finite float exactly.

    ``repr`` is the shortest decimal string that parses back to the identical
    double, so two distinct accepted candidates never collapse. Fixed-precision
    formatting (for example ``.10g``) could merge values and change the candidate
    set during normalization, breaking the configured-rate membership check.
    """
    return ",".join(repr(value) for value in values)


def parse_candidates(value: Any) -> list[float]:
    """Parse and validate the candidate list from its scalar transport form.

    The transport size is bounded before splitting or conversion so arbitrarily
    large untrusted input is rejected with a controlled ``ValueError`` instead of
    consuming unbounded time or memory. Duplicate and empty entries remain
    allowed; the distinct-rate limit is still enforced.
    """
    if isinstance(value, (list, tuple)):
        if len(value) > MAX_CANDIDATES_ITEMS:
            raise ValueError(
                f"adaptation_lr_candidates must contain at most {MAX_CANDIDATES_ITEMS} entries"
            )
        items: list[Any] = list(value)
    elif isinstance(value, str):
        if len(value) > MAX_CANDIDATES_TEXT:
            raise ValueError(
                f"adaptation_lr_candidates must be at most {MAX_CANDIDATES_TEXT} characters"
            )
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
        except (TypeError, ValueError, OverflowError) as error:
            # An arbitrarily large integer raises OverflowError; reject it with
            # the same controlled validation error as other malformed entries.
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


def sanitize_inactive_adaptation(values: dict) -> dict:
    """Bound controller settings that are inert while adaptation is off.

    Adaptation-off jobs must never be blocked by irrelevant controller input, but
    the persisted configuration still has to be finite, JSON-serializable and
    small. Invalid, oversized or non-scalar inactive values are replaced with the
    shared defaults instead of being retained or rejected. ``learning_rate`` and
    every other unrelated setting are never touched.
    """
    defaults = adaptation_defaults()
    sanitized: dict[str, Any] = {"adaptation": "off"}
    raw_candidates = values.get("adaptation_lr_candidates")
    if isinstance(raw_candidates, str) and len(raw_candidates) <= MAX_CANDIDATES_TEXT:
        try:
            candidates = parse_candidates(raw_candidates)
        except ValueError:
            candidates = []
        sanitized["adaptation_lr_candidates"] = (
            format_candidates(candidates) if candidates else defaults["adaptation_lr_candidates"]
        )
    else:
        sanitized["adaptation_lr_candidates"] = defaults["adaptation_lr_candidates"]
    for key, low, high in (
        ("adaptation_cadence", 1, 10000),
        ("adaptation_warmup", 0, 10000),
        ("adaptation_min_dwell", 1, 10000),
        ("adaptation_lag", 1, MAX_LAG),
        ("adaptation_min_trials", 1, 100),
    ):
        value = values.get(key)
        if type(value) is int and low <= value <= high:
            sanitized[key] = value
        else:
            sanitized[key] = defaults[key]
    return sanitized


def normalize_adaptation(full_config: dict, *, resolved: bool = True) -> dict:
    """Validate the adaptation inputs and return their canonical scalar values.

    ``adaptation_lr_candidates`` stays a scalar string in the normalized config
    so the value transports like every other training parameter; the controller
    parses it once when it is constructed.

    When ``resolved`` is false, the cross-field checks that depend on settings
    inherited from a resumed checkpoint (the evaluation opponent, the evaluation
    game count and the configured learning rate) are deferred until the effective
    configuration is known. The controller fields themselves are still validated
    whenever adaptation is enabled, so independently invalid input is rejected
    before any checkpoint is loaded. With adaptation off the controller settings
    are inert and never block a run; they are still bounded and made JSON-safe so
    an unsafe inactive value cannot fail a later write.
    """
    values = {
        **adaptation_defaults(),
        **{key: full_config[key] for key in ADAPTATION_FIELDS if key in full_config},
    }
    algorithm = values["adaptation"]
    # The mode selector is semantically important: reject any value that is not
    # exactly the two supported modes instead of silently treating it as off.
    if algorithm not in ALGORITHMS:
        raise ValueError("adaptation must be off or hypercontroller")
    if algorithm == "off":
        # Inactive controller settings are inert: they never block a run or
        # rewrite an unrelated setting such as the ordinary learning rate. They
        # are still sanitized so persisted configuration stays finite, bounded
        # and JSON-serializable.
        return sanitize_inactive_adaptation(values)
    candidates = parse_candidates(values["adaptation_lr_candidates"])
    for key, low, high in (
        ("adaptation_cadence", 1, 10000),
        ("adaptation_warmup", 0, 10000),
        ("adaptation_min_dwell", 1, 10000),
        ("adaptation_lag", 1, MAX_LAG),
        ("adaptation_min_trials", 1, 100),
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
    if resolved:
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
    """Restore an explicit saved RNG stream, raising on malformed input.

    A missing or malformed stream is never silently replaced: the caller resets
    the controller instead, so learned predictors are not adopted with a different
    exploration stream than the one they were learned under. The optional
    ``seed`` only seeds the fresh generator that is then overwritten by the saved
    state; it is not a fallback.
    """
    if not isinstance(state, (list, tuple)) or len(state) != 3:
        raise ValueError("Controller RNG state must be a three-element sequence")
    version, values, gauss = state
    if type(version) is not int:
        raise ValueError("Controller RNG version is malformed")
    if not isinstance(values, (list, tuple)) or not values:
        raise ValueError("Controller RNG values are malformed")
    if gauss is not None and not isinstance(gauss, (list, tuple)):
        raise ValueError("Controller RNG Gaussian state is malformed")
    rng = random.Random(seed)
    try:
        rng.setstate((version, tuple(int(value) for value in values), gauss))
    except (TypeError, ValueError, IndexError) as error:
        raise ValueError("Controller RNG state is malformed") from error
    return rng


def _finite_float(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Controller state contains a non-numeric value")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Controller state contains a non-finite number")
    return number


def _is_positive_definite(matrix: list[list[float]]) -> bool:
    """Cholesky test for the small ridge systems this controller inverts."""
    size = len(matrix)
    lower = [[0.0] * size for _ in range(size)]
    for row in range(size):
        for column in range(row + 1):
            total = matrix[row][column] - sum(
                lower[row][index] * lower[column][index] for index in range(column)
            )
            if row == column:
                if total <= 0:
                    return False
                lower[row][row] = math.sqrt(total)
            else:
                if lower[column][column] == 0:
                    return False
                lower[row][column] = total / lower[column][column]
    return True


def _validate_contexts(value: Any, lag: int, candidate_count: int) -> dict[str, dict[str, Any]]:
    """Validate the whole learned-context map, rejecting anything malformed."""
    if not isinstance(value, dict):
        raise ValueError("Controller contexts must be an object")
    if len(value) > MAX_CONTEXTS:
        raise ValueError(f"Controller state exceeds the {MAX_CONTEXTS}-context limit")
    contexts: dict[str, dict[str, Any]] = {}
    for key, entry in value.items():
        if not isinstance(key, str):
            raise ValueError("Controller context key must be a string")
        action_text, separator, context_text = key.partition(":")
        if not separator or not action_text.isdigit() or not context_text:
            raise ValueError("Controller context key is malformed")
        action = int(action_text)
        if not 0 <= action < candidate_count:
            raise ValueError("Controller context key names an illegal action")
        parts = context_text.split(",")
        if len(parts) != lag or any(not part.isdigit() for part in parts):
            raise ValueError("Controller context key has the wrong shape")
        context = [int(part) for part in parts]
        if any(not 0 <= item < candidate_count for item in context):
            raise ValueError("Controller context key names an illegal action")
        if not isinstance(entry, dict):
            raise ValueError("Controller context entry must be an object")
        matrix = entry.get("V")
        vector = entry.get("b")
        if not isinstance(matrix, list) or len(matrix) != lag:
            raise ValueError("Controller context matrix has the wrong shape")
        validated_matrix: list[list[float]] = []
        for row in matrix:
            if not isinstance(row, list) or len(row) != lag:
                raise ValueError("Controller context matrix has the wrong shape")
            validated_matrix.append([_finite_float(item) for item in row])
        for row in range(lag):
            if validated_matrix[row][row] <= 0:
                raise ValueError("Controller context matrix is not positive definite")
            for column in range(row):
                if abs(validated_matrix[row][column] - validated_matrix[column][row]) > 1e-9:
                    raise ValueError("Controller context matrix is not symmetric")
        if not _is_positive_definite(validated_matrix):
            raise ValueError("Controller context matrix is not positive definite")
        if not isinstance(vector, list) or len(vector) != lag:
            raise ValueError("Controller context vector has the wrong shape")
        count = entry.get("n")
        if type(count) is not int or count < 0:
            raise ValueError("Controller context count is invalid")
        contexts[key] = {
            "V": validated_matrix,
            "b": [_finite_float(item) for item in vector],
            "n": count,
        }
    return contexts


def _validate_number_list(value: Any, limit: int, name: str) -> list[float]:
    if not isinstance(value, list) or len(value) > limit:
        raise ValueError(f"Controller {name} history is invalid")
    return [_finite_float(item) for item in value]


def _validate_action_list(value: Any, candidate_count: int, limit: int) -> list[int]:
    if not isinstance(value, list) or len(value) > limit:
        raise ValueError("Controller action history is invalid")
    actions: list[int] = []
    for item in value:
        if type(item) is not int or not 0 <= item < candidate_count:
            raise ValueError("Controller action history names an illegal action")
        actions.append(item)
    return actions


def _validate_count_list(value: Any, candidate_count: int) -> list[int]:
    if not isinstance(value, list) or len(value) != candidate_count:
        raise ValueError("Controller candidate counts are invalid")
    counts: list[int] = []
    for item in value:
        if type(item) is not int or item < 0:
            raise ValueError("Controller candidate counts are invalid")
        counts.append(item)
    return counts


def _validate_counter(value: Any, name: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"Controller {name} counter is invalid")
    return value


def _validate_index(value: Any, candidate_count: int) -> int:
    if type(value) is not int or not 0 <= value < candidate_count:
        raise ValueError("Controller effective index is invalid")
    return value


def _validate_provenance(value: Any) -> dict[str, Any]:
    """Validate one event's provenance, rejecting unexpected nested data."""
    if not isinstance(value, dict):
        raise ValueError("Controller event provenance must be an object")
    if set(value) - PROVENANCE_KEYS:
        raise ValueError("Controller event provenance has an unexpected field")
    validated: dict[str, Any] = {}
    for key, item in value.items():
        if key in ("opponent", "opponent_identity"):
            if item is not None and (not isinstance(item, str) or len(item) > MAX_PROVENANCE_TEXT):
                raise ValueError("Controller event provenance text is invalid")
            validated[key] = item
        elif key == "iteration":
            if item is not None and (type(item) is not int or item < 0):
                raise ValueError("Controller event provenance iteration is invalid")
            validated[key] = item
        elif key == "score":
            if item is not None:
                score = _finite_float(item)
                if not 0.0 <= score <= 1.0:
                    raise ValueError("Controller event provenance score is invalid")
                validated[key] = score
            else:
                validated[key] = None
        else:
            if type(item) is not int or item < 0:
                raise ValueError("Controller event provenance count is invalid")
            validated[key] = item
    return validated


def _validate_event(event: Any, candidates: list[float]) -> dict[str, Any]:
    """Validate one bounded decision event against the documented schema."""
    if not isinstance(event, dict):
        raise ValueError("Controller event must be an object")
    if set(event) - EVENT_KEYS:
        raise ValueError("Controller event has an unexpected field")
    validated: dict[str, Any] = {}
    iteration = event.get("iteration")
    if type(iteration) is not int or iteration < 0:
        raise ValueError("Controller event iteration is invalid")
    validated["iteration"] = iteration
    observation = event.get("observation")
    if observation is not None:
        observation = _finite_float(observation)
        if not 0.0 <= observation <= 1.0:
            raise ValueError("Controller event observation is invalid")
    validated["observation"] = observation
    validated["provenance"] = _validate_provenance(event.get("provenance", {}))
    for key in (
        "attributed_learning_rate",
        "previous_learning_rate",
        "selected_learning_rate",
        "effective_learning_rate",
    ):
        if key in event:
            rate = _finite_float(event[key])
            if rate not in candidates:
                raise ValueError("Controller event learning rate is not a candidate")
            validated[key] = rate
    for key in ("selected_index", "previous_index", "effective_index"):
        if key in event:
            validated[key] = _validate_index(event[key], len(candidates))
    changed = event.get("changed", False)
    if type(changed) is not bool:
        raise ValueError("Controller event change flag is invalid")
    validated["changed"] = changed
    skipped = event.get("skipped")
    if skipped is not None and skipped not in SKIP_REASONS:
        raise ValueError("Controller event skip reason is invalid")
    validated["skipped"] = skipped
    if "observations" in event:
        validated["observations"] = _validate_counter(event["observations"], "event observation")
    if "seconds" in event:
        seconds = _finite_float(event["seconds"])
        if seconds < 0:
            raise ValueError("Controller event duration is invalid")
        validated["seconds"] = seconds
    return validated


def _validate_events(value: Any, candidates: list[float]) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > MAX_EVENTS:
        raise ValueError("Controller event history is invalid")
    return [_validate_event(event, candidates) for event in value]


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
        if lag < 1 or cadence < 1 or min_dwell < 1 or min_trials < 1:
            raise ValueError("Invalid controller schedule")
        if len(candidates) ** (lag + 1) > MAX_CONTEXTS:
            raise ValueError("Candidate and lag combination exceeds the context limit")
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
        # Observations attributed per candidate (telemetry only). Exploration is
        # gated on the per-context count in ``contexts[...]["n"]`` instead.
        self.counts = [0] * len(self.candidates)
        # Keyed by "candidate|context..." with the same ``lag`` shape for every
        # entry, so the state-size bound is ``candidates ** (lag + 1)``.
        self.contexts: dict[str, dict[str, Any]] = {}
        self.observations = 0
        self.learned = 0
        self.decisions = 0
        self.changes = 0
        self.coverage_pulls = 0
        self.last_decision_observation = 0
        self.last_change_observation = 0
        self.limit_hits = 0
        self.events: list[dict[str, Any]] = []
        self.reset_reason: str | None = None
        self.migrated_from: int | None = None
        #: Set when a resume explicitly overrode the effective rate, so the
        #: override is visible in monitoring rather than silent.
        self.override_learning_rate: float | None = None

    # -- public interface -------------------------------------------------

    @property
    def effective_learning_rate(self) -> float:
        return self.candidates[self.effective_index]

    def set_effective_index(self, index: int) -> None:
        """Adopt an externally requested effective rate (a resume override).

        The learned predictors and counters are kept: the override only changes
        the rate applied from the next interval, and the dwell timer is restarted
        so the override is not immediately replaced. There is no pending feedback
        to reconcile because the checkpoint is written after each decision.
        """
        if not 0 <= index < len(self.candidates):
            raise ValueError("Effective index is out of range")
        self.effective_index = index
        self.last_change_observation = self.observations
        self.override_learning_rate = self.candidates[index]

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
            "learned": self.learned,
            "decisions": self.decisions,
            "changes": self.changes,
            "coverage_pulls": self.coverage_pulls,
            "last_decision_observation": self.last_decision_observation,
            "last_change_observation": self.last_change_observation,
            "evaluation_identity": self.evaluation_identity,
            "contexts": len(self.contexts),
            "limit_hits": self.limit_hits,
            "reset_reason": self.reset_reason,
            "migrated_from": self.migrated_from,
            "override_learning_rate": self.override_learning_rate,
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
                "attributed_learning_rate": self.effective_learning_rate,
                "previous_learning_rate": self.effective_learning_rate,
                "selected_learning_rate": self.effective_learning_rate,
                "changed": False,
                "skipped": "missing_observation",
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
            if self._learn():
                # Only an observation that actually updated a predictor counts as
                # learned; warm-up observations cannot satisfy coverage.
                self.learned += 1
        except (ValueError, TypeError, ZeroDivisionError):
            # A malformed window must never abort training; skip this update.
            pass
        event = self._decide(iteration, reward, provenance)
        self._record_event(event, started)
        return event

    def _decide(self, iteration: int, reward: float, provenance: dict[str, Any]) -> dict[str, Any]:
        previous_index = self.effective_index
        previous_rate = self.candidates[previous_index]
        event: dict[str, Any] = {
            "iteration": iteration,
            "observation": round(reward, 6),
            "provenance": provenance,
            # The rate that produced the observation and is still effective until
            # a decision changes it.
            "attributed_learning_rate": previous_rate,
            "previous_learning_rate": previous_rate,
            "selected_learning_rate": previous_rate,
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
        event["selected_learning_rate"] = self.candidates[chosen]
        if chosen != previous_index:
            self.effective_index = chosen
            self.last_change_observation = self.observations
            self.changes += 1
            event["changed"] = True
        event["effective_learning_rate"] = self.effective_learning_rate
        return event

    def _select(self) -> int:
        size = len(self.candidates)
        if len(self.rewards) < self.lag:
            # Too little history to form a context window; explore uniformly.
            self.coverage_pulls += 1
            return self.rng.randrange(size)
        context = tuple(self.actions[-self.lag :])
        window = self.rewards[-self.lag :]
        # Coverage is per (candidate, context): a predictor that has never seen a
        # context cannot be compared with one that has, and a positive prediction
        # elsewhere must not hide it. The number of contexts is bounded by the
        # state limit, so the forced-exploration burden is bounded too.
        untried = [
            index for index in range(size) if self._context_count(index, context) < self.min_trials
        ]
        if untried:
            self.coverage_pulls += 1
            return untried[self.rng.randrange(len(untried))]
        best_index = 0
        best_value: float | None = None
        best_count = 0
        for index in range(size):
            value = self._prediction(index, context, window)
            count = self._context_count(index, context)
            if best_value is None or value > best_value + 1e-12:
                best_index, best_value, best_count = index, value, count
            elif abs(value - best_value) <= 1e-12 and count < best_count:
                best_index, best_count = index, count
        return best_index

    def _context_count(self, action: int, context: tuple[int, ...]) -> int:
        entry = self.contexts.get(self._key(action, context))
        return int(entry["n"]) if entry is not None else 0

    def _learn(self) -> bool:
        if len(self.rewards) < self.lag + 1:
            return False
        target = len(self.rewards) - 1
        context = tuple(self.actions[target - self.lag : target])
        window = self.rewards[target - self.lag : target]
        action = self.actions[target]
        if len(context) != self.lag or len(window) != self.lag:
            return False
        return self._update(action, context, window, self.rewards[target])

    def _update(
        self, action: int, context: tuple[int, ...], window: list[float], reward: float
    ) -> bool:
        key = self._key(action, context)
        entry = self.contexts.get(key)
        if entry is None:
            if len(self.contexts) >= MAX_CONTEXTS:
                self.limit_hits += 1
                return False
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
        return True

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
            "learned": self.learned,
            "decisions": self.decisions,
            "changes": self.changes,
            "coverage_pulls": self.coverage_pulls,
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
        version = state.get("version")
        if version not in COMPATIBLE_STATE_VERSIONS or state.get("algorithm") != "hypercontroller":
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
        if "rng" not in state:
            # A state without a saved stream cannot be resumed with the same
            # exploration sequence. Reset explicitly instead of adopting learned
            # predictors under a silently different RNG.
            controller.reset_reason = "rng_absent"
            return controller
        # Validate every field into locals first; the controller adopts nothing
        # until all of them pass, so a partially valid state can never leak into
        # telemetry or a later checkpoint. A malformed state resets only the
        # controller: the model, optimizer and replay are untouched.
        try:
            contexts = _validate_contexts(state.get("contexts"), lag, len(candidates))
            rewards = _validate_number_list(state.get("rewards"), MAX_REWARD_HISTORY, "reward")
            actions = _validate_action_list(
                state.get("actions"), len(candidates), MAX_REWARD_HISTORY
            )
            if len(rewards) != len(actions):
                raise ValueError("Controller reward and action histories disagree")
            counts = _validate_count_list(state.get("counts"), len(candidates))
            effective_index = _validate_index(
                state.get("effective_index", initial_index), len(candidates)
            )
            observations = _validate_counter(state.get("observations", 0), "observation")
            learned = _validate_counter(state.get("learned", 0), "learned")
            decisions = _validate_counter(state.get("decisions", 0), "decision")
            changes = _validate_counter(state.get("changes", 0), "change")
            coverage_pulls = _validate_counter(state.get("coverage_pulls", 0), "coverage")
            last_decision = _validate_counter(
                state.get("last_decision_observation", 0), "last-decision"
            )
            last_change = _validate_counter(state.get("last_change_observation", 0), "last-change")
            limit_hits = _validate_counter(state.get("limit_hits", 0), "limit-hit")
            if last_decision > observations or last_change > observations:
                raise ValueError("Controller observation counters are inconsistent")
            events = _validate_events(state.get("events", []), candidates)
            rng = _restore_rng(state["rng"], seed)
        except (KeyError, TypeError, ValueError, IndexError, OverflowError):
            controller.reset_reason = "unreadable_state"
            return controller
        controller.contexts = contexts
        controller.rewards = rewards
        controller.actions = actions
        controller.counts = counts
        controller.effective_index = effective_index
        controller.observations = observations
        controller.learned = learned
        controller.decisions = decisions
        controller.changes = changes
        controller.coverage_pulls = coverage_pulls
        controller.last_decision_observation = last_decision
        controller.last_change_observation = last_change
        controller.limit_hits = limit_hits
        controller.events = events
        controller.rng = rng
        if version == 1:
            # Version 1 stored the same learned contexts; only the exploration
            # gate changed, so it is migrated in place rather than reset.
            controller.migrated_from = 1
        return controller


def resolve_controller(
    config: dict,
    *,
    saved_state: Any,
    evaluation_identity: str,
    seed: int,
    resume: bool,
    override_learning_rate: float | None = None,
) -> HyperController | None:
    """Build the controller for a run, or ``None`` when adaptation is off.

    A resume whose evaluation identity differs from the saved one resets the
    learned state inside :meth:`HyperController.from_state`, so observations from
    a different objective are never mixed. ``override_learning_rate`` is a rate
    the caller explicitly requested for a resume; it is honoured by moving the
    restored controller's effective rate (keeping the learned state) so the
    override is never silently discarded.
    """
    if config.get("adaptation", "off") == "off":
        return None
    candidates = candidates_from_config(config)
    learning_rate = float(config.get("learning_rate", candidates[0]))
    initial_index = candidates.index(learning_rate) if learning_rate in candidates else 0
    if resume and isinstance(saved_state, dict) and saved_state:
        controller = HyperController.from_state(
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
        if override_learning_rate is not None and controller.reset_reason is None:
            # The rate must already be a candidate: normalize_config enforces it.
            controller.set_effective_index(candidates.index(override_learning_rate))
        return controller
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
