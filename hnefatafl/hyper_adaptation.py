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
* The objective is the completed-game score against the selected, versioned
evaluation opponent. Unfinished games are excluded rather than scored as losses,
and the observation is only supplied at evaluation boundaries. This is not the
same quantity the promotion rule optimizes. When the opponent is the evolving
Champion, a promotion is an **idempotent transition**, never a destructive
reset: the learned predictors, reward/action history and counters are shared
across Champions and survive the boundary, while the per-Champion evaluation
state (identity, promotion iteration, local baseline, calibration offset and
uncertainty) is refreshed for the new incumbent.
* Raw win rates against different Champions are never compared. Every
  observation is a *within-Champion improvement* over the first score seen after
  that Champion was installed; the first post-promotion evaluation only
  establishes that local baseline and never trains a predictor. This keeps the
  shared predictors on a comparable scale while preserving the dwell and
  warm-up counters across the boundary.
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
#: exploration per (candidate, context) and records the learned/coverage counters;
#: version 3 added the bounded Champion-promotion record; version 4 adds the
#: active-Champion regime identity that the learned observations belong to;
#: version 5 separates the persistent global controller state from the
#: per-Champion evaluation state, keeps the learned predictors across promotions
#: and scores within-Champion improvement instead of raw win rates. Version 6
#: corrects the reward semantics: fixed-opponent jobs keep the historical
#: absolute-score reward (and their compatible predictors), while only an
#: evolving Champion uses baseline-relative improvement, weighted by completed
#: evaluation games and gated by a confidence margin. Earlier versions are
#: migrated on load; unknown versions reset only the controller state.
STATE_VERSION = 6
COMPATIBLE_STATE_VERSIONS = (1, 2, 3, 4, 5, 6)
#: Pseudo-count of the cross-Champion prior when shrinking a Champion's local
#: calibration offset. One prior observation means a new Champion starts at the
#: global mean and moves toward its own mean as evidence accumulates.
PRIOR_WEIGHT = 1.0
#: Neutral score the first Champion baseline is shrunk toward, so one unusually
#: high or low evaluation is never treated as a reliable reference.
NEUTRAL_SCORE = 0.5
#: Conservative uncertainty (in score units) used before a Champion has enough
#: observations to estimate the spread of its improvements.
DEFAULT_UNCERTAINTY = 0.5
#: The best candidate's predicted improvement must exceed the current rate's by
#: at least this many standard errors before the rate may change. This hysteresis
#: keeps marginal, noisy differences from moving the learning rate.
HYSTERESIS = 1.0
#: Cap on the per-observation predictor weight derived from completed games.
MAX_GAME_WEIGHT = 64.0
#: Minimum number of comparable (post-baseline) Champion observations before the
#: controller may change the rate, whether by exploitation or by exploration.
EXPLORATION_MIN_EVIDENCE = 2
#: Minimum number of observations between two exploratory rate changes. This is
#: an additional limit on top of ``adaptation_min_dwell`` so forced coverage can
#: never oscillate the rate on consecutive decisions.
EXPLORATION_DWELL = 2
#: Weak-prior variance of one game's score, used to regularize a score standard
#: error so a degenerate sample (all wins, all draws) is never treated as certain.
SCORE_SE_PRIOR_VAR = 0.25
#: Pseudo-count of that weak prior.
SCORE_SE_PSEUDO = 1.0
#: The two reward representations a serialized state may use.
REWARD_SEMANTICS = ("absolute", "baseline_relative")
#: Bound on the per-Champion raw-score telemetry kept for monitoring.
MAX_SCORE_HISTORY = 64
#: Maximum plausible per-evaluation score standard error (a single game).
MAX_SCORE_SE = 0.5

#: Bounds that keep the learned state small and the decisions cheap.
MIN_CANDIDATES = 2
MAX_CANDIDATES = 8
MAX_LAG = 3
#: Longest scalar candidate transport accepted before it is split. A valid list
#: of at most eight exact floats is far shorter; the cap rejects oversized
#: untrusted input before any allocation or conversion happens.
MAX_CANDIDATES_TEXT = 512
#: Bounds for the optional iteration-based minimum dwell. Zero disables the
#: extra gate; the observation-based ``adaptation_min_dwell`` still applies.
MAX_MIN_DWELL_ITERATIONS = 1000000
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
        "baseline",
        "exploratory",
        "margin",
        "weight",
        "observations",
        "seconds",
        # Champion-promotion transition marker (state versions 3 and 5).
        "promotion_reset",
        "transition",
        "preserved_learning_rate",
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
SKIP_REASONS = frozenset(
    {
        "warmup",
        "cadence",
        "dwell",
        "dwell_iterations",
        "fallback",
        "missing_observation",
        "baseline",
        "uncertain",
        "insufficient_evidence",
        "promotion_reset",
        "promotion_transition",
        "promotion_duplicate",
    }
)
#: Longest accepted provenance string (opponent name or identity).
MAX_PROVENANCE_TEXT = 200
#: Accepted fields of the bounded Champion-promotion transition record.
PROMOTION_KEYS = frozenset(
    {"iteration", "champion_iteration", "opponent_identity", "preserved_learning_rate"}
)
#: Accepted fields of the bounded per-Champion evaluation state.
CHAMPION_KEYS = frozenset(
    {
        "identity",
        "promotion_iteration",
        "protocol",
        "baseline_score",
        "baseline_games",
        "baseline_se",
        "effective_baseline",
        "baseline_iteration",
        "observations",
        "count",
        "mean",
        "m2",
        "offset",
        "uncertainty",
        "score_se_sum_sq",
        "scores",
        "last_score",
    }
)
#: Accepted fields of the cross-Champion improvement prior.
PRIOR_KEYS = frozenset({"mean", "m2", "count"})

#: Default candidate set: roughly half, equal to and triple the shipped 0.001.
DEFAULT_CANDIDATES = "0.0003,0.001,0.003"

ADAPTATION_FIELDS = (
    "adaptation",
    "adaptation_lr_candidates",
    "adaptation_cadence",
    "adaptation_warmup",
    "adaptation_min_dwell",
    "adaptation_min_dwell_iterations",
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
        "adaptation_min_dwell_iterations": 0,
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
        if isinstance(item, str) and len(item) > MAX_CANDIDATES_TEXT:
            # A list/tuple element is not covered by the whole-transport length
            # check; bound it before float() so an oversized element cannot
            # consume unbounded time or memory during conversion.
            raise ValueError(
                "adaptation_lr_candidates elements must be at most "
                f"{MAX_CANDIDATES_TEXT} characters"
            )
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
        ("adaptation_min_dwell_iterations", 0, MAX_MIN_DWELL_ITERATIONS),
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
        ("adaptation_min_dwell_iterations", 0, MAX_MIN_DWELL_ITERATIONS),
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


def _validate_promotion(value: Any, candidates: list[float]) -> dict[str, Any] | None:
    """Validate one bounded Champion-promotion reset record."""
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) - PROMOTION_KEYS:
        raise ValueError("Controller promotion record is invalid")
    iteration = value.get("iteration")
    if type(iteration) is not int or iteration < 0:
        raise ValueError("Controller promotion iteration is invalid")
    champion_iteration = value.get("champion_iteration")
    if champion_iteration is not None and (
        type(champion_iteration) is not int or champion_iteration < 0
    ):
        raise ValueError("Controller promotion champion iteration is invalid")
    identity = value.get("opponent_identity")
    if identity is not None and (
        not isinstance(identity, str) or len(identity) > MAX_PROVENANCE_TEXT
    ):
        raise ValueError("Controller promotion identity is invalid")
    preserved = _finite_float(value.get("preserved_learning_rate"))
    if preserved not in candidates:
        raise ValueError("Controller promotion learning rate is not a candidate")
    return {
        "iteration": iteration,
        "champion_iteration": champion_iteration,
        "opponent_identity": identity,
        "preserved_learning_rate": preserved,
    }


def _validate_champion_identity(value: Any) -> str | None:
    """Validate the bounded identity of the Champion the learned state belongs to."""
    if value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > MAX_PROVENANCE_TEXT:
        raise ValueError("Controller active Champion identity is invalid")
    return value


def _validate_optional_iteration(value: Any) -> int | None:
    """Validate an optional non-negative iteration marker."""
    if value is None:
        return None
    if type(value) is not int or value < 0:
        raise ValueError("Controller iteration marker is invalid")
    return value


def _validate_prior(value: Any, *, required: bool = False) -> dict[str, Any]:
    """Validate the bounded cross-Champion improvement prior.

    ``required`` is set for the current schema, which always serializes the
    prior; a missing prior in a current-schema state is malformed rather than a
    silent default. Historical schemas without a prior keep the neutral default.
    """
    if value is None:
        if required:
            raise ValueError("Controller improvement prior is missing")
        return {"mean": 0.0, "m2": 0.0, "count": 0}
    if not isinstance(value, dict) or set(value) != PRIOR_KEYS:
        raise ValueError("Controller improvement prior is invalid")
    mean = _finite_float(value.get("mean", 0.0))
    if not -1.0 <= mean <= 1.0:
        raise ValueError("Controller improvement prior mean is invalid")
    m2 = _finite_float(value.get("m2", 0.0))
    if m2 < 0:
        raise ValueError("Controller improvement prior variance is invalid")
    count = _validate_counter(value.get("count", 0), "prior")
    if count == 0 and (m2 != 0.0 or mean != 0.0):
        raise ValueError("Controller improvement prior is inconsistent")
    return {"mean": mean, "m2": m2, "count": count}


def _validate_champion_state(
    value: Any, protocol: str, *, strict: bool = False
) -> dict[str, Any] | None:
    """Validate the per-Champion evaluation state, rejecting malformed entries.

    The block records which Champion the learned observations belong to, the
    local baseline that turns raw scores into within-Champion improvements, the
    regularized evaluation uncertainty and the shrunk calibration offset used for
    telemetry. It never stores model tensors or arbitrary objects.

    ``strict`` is set for the current schema, which always serializes every field
    with a documented meaning. A strict block must carry exactly the documented
    fields (no missing protocol or uncertainty metadata), so a corrupted current
    state cannot silently fall back to defaults. Historical schemas keep the
    lenient path and supply version-specific defaults instead.
    """
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) - CHAMPION_KEYS:
        raise ValueError("Controller Champion state is invalid")
    if strict and set(value) != CHAMPION_KEYS:
        raise ValueError("Controller Champion state is incomplete")
    identity = _validate_champion_identity(value.get("identity"))
    promotion_iteration = _validate_optional_iteration(value.get("promotion_iteration"))
    saved_protocol: Any
    if "protocol" not in value:
        if strict:
            raise ValueError("Controller Champion protocol is missing")
        saved_protocol = protocol
    else:
        saved_protocol = value.get("protocol")
    if not isinstance(saved_protocol, str) or len(saved_protocol) > MAX_PROVENANCE_TEXT:
        raise ValueError("Controller Champion protocol is invalid")
    baseline_score = value.get("baseline_score")
    if baseline_score is not None:
        baseline_score = _finite_float(baseline_score)
        if not 0.0 <= baseline_score <= 1.0:
            raise ValueError("Controller Champion baseline score is invalid")
    baseline_games = value.get("baseline_games", 0)
    if type(baseline_games) is not int or baseline_games < 0:
        raise ValueError("Controller Champion baseline game count is invalid")
    baseline_se = value.get("baseline_se", 0.0)
    baseline_se = _finite_float(baseline_se)
    if not 0.0 <= baseline_se <= MAX_SCORE_SE:
        raise ValueError("Controller Champion baseline uncertainty is invalid")
    effective_baseline = value.get("effective_baseline")
    if effective_baseline is not None:
        effective_baseline = _finite_float(effective_baseline)
        if not 0.0 <= effective_baseline <= 1.0:
            raise ValueError("Controller Champion effective baseline is invalid")
    if baseline_score is None and (baseline_games != 0 or effective_baseline is not None):
        raise ValueError("Controller Champion baseline metadata is inconsistent")
    baseline_iteration = _validate_optional_iteration(value.get("baseline_iteration"))
    observations = _validate_counter(value.get("observations", 0), "Champion observation")
    count = _validate_counter(value.get("count", 0), "Champion improvement")
    mean = _finite_float(value.get("mean", 0.0))
    if not -1.0 <= mean <= 1.0:
        raise ValueError("Controller Champion improvement mean is invalid")
    m2 = _finite_float(value.get("m2", 0.0))
    if m2 < 0:
        raise ValueError("Controller Champion improvement variance is invalid")
    offset = _finite_float(value.get("offset", 0.0))
    if not -1.0 <= offset <= 1.0:
        raise ValueError("Controller Champion offset is invalid")
    uncertainty = _finite_float(value.get("uncertainty", 0.0))
    if not 0.0 <= uncertainty <= 1.0:
        raise ValueError("Controller Champion uncertainty is invalid")
    score_se_sum_sq = _finite_float(value.get("score_se_sum_sq", 0.0))
    if score_se_sum_sq < 0:
        raise ValueError("Controller Champion score uncertainty is invalid")
    scores = _validate_number_list(value.get("scores", []), MAX_SCORE_HISTORY, "Champion score")
    if any(not 0.0 <= score <= 1.0 for score in scores):
        raise ValueError("Controller Champion score history is invalid")
    last_score = value.get("last_score")
    if last_score is not None:
        last_score = _finite_float(last_score)
        if not 0.0 <= last_score <= 1.0:
            raise ValueError("Controller Champion last score is invalid")
    # Cross-field consistency: the number of improvements cannot exceed the
    # number of regime observations, a baseline observation is required before
    # any improvement can exist, and the uncertainty accumulators agree with the
    # improvement count.
    minimum = count + (1 if baseline_score is not None else 0)
    if observations < minimum:
        raise ValueError("Controller Champion counters are inconsistent")
    if count == 0 and (m2 != 0.0 or mean != 0.0 or score_se_sum_sq != 0.0):
        raise ValueError("Controller Champion improvement statistics are inconsistent")
    if strict and count > 0 and score_se_sum_sq <= 0.0:
        raise ValueError("Controller Champion score uncertainty is inconsistent")
    if strict and count > 0 and baseline_score is not None and baseline_se <= 0.0:
        # A scored baseline always carries a positive regularized uncertainty.
        raise ValueError("Controller Champion baseline uncertainty is inconsistent")
    return {
        "identity": identity,
        "promotion_iteration": promotion_iteration,
        "protocol": saved_protocol,
        "baseline_score": baseline_score,
        "baseline_games": baseline_games,
        "baseline_se": baseline_se,
        "effective_baseline": effective_baseline,
        "baseline_iteration": baseline_iteration,
        "observations": observations,
        "count": count,
        "mean": mean,
        "m2": m2,
        "offset": offset,
        "uncertainty": uncertainty,
        "score_se_sum_sq": score_se_sum_sq,
        "scores": scores,
        "last_score": last_score,
    }


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
        "preserved_learning_rate",
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
    promotion_reset = event.get("promotion_reset", False)
    if type(promotion_reset) is not bool:
        raise ValueError("Controller event promotion flag is invalid")
    validated["promotion_reset"] = promotion_reset
    transition = event.get("transition", False)
    if type(transition) is not bool:
        raise ValueError("Controller event transition flag is invalid")
    validated["transition"] = transition
    baseline = event.get("baseline", False)
    if type(baseline) is not bool:
        raise ValueError("Controller event baseline flag is invalid")
    validated["baseline"] = baseline
    exploratory = event.get("exploratory", False)
    if type(exploratory) is not bool:
        raise ValueError("Controller event exploration flag is invalid")
    validated["exploratory"] = exploratory
    for key in ("margin", "weight"):
        if key in event:
            number = _finite_float(event[key])
            if number < 0:
                raise ValueError("Controller event numeric field is invalid")
            validated[key] = number
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

    Two feedback representations are supported, chosen by the evaluation opponent:

    * **Absolute** (fixed opponent). The completed-game score is directly
      comparable for the whole run, so the historical reward representation and
      its compatible predictors are preserved unchanged.
    * **Baseline-relative** (evolving Champion). Raw scores against different
      Champions are never compared: every observation is the improvement over a
      shrunk local baseline established by the first evaluation after a Champion
      was installed. Predictor updates are weighted by completed games and a rate
      change needs a confidence margin, so a noisy baseline or a small sample
      cannot move the rate on its own.

    State is split in two:

    * The **persistent global state** (candidate set, effective rate, learned
      predictors, reward/action/weight history, dwell counters, lifetime
      statistics and the dedicated RNG stream) describes the single continuing
      trajectory. It is never discarded at a Champion promotion.
    * The **per-Champion evaluation state** (identity, promotion iteration,
      evaluation protocol, local baseline, raw-score telemetry, shrunk calibration
      offset and uncertainty) describes the incumbent the current observations are
      compared against. It is refreshed when a new Champion is installed.
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
        active_champion_identity: str | None = None,
        min_dwell_iterations: int = 0,
    ) -> None:
        if not MIN_CANDIDATES <= len(candidates) <= MAX_CANDIDATES:
            raise ValueError("Invalid candidate count")
        if not 0 <= initial_index < len(candidates):
            raise ValueError("Initial candidate index is out of range")
        if lag < 1 or cadence < 1 or min_dwell < 1 or min_trials < 1:
            raise ValueError("Invalid controller schedule")
        if type(min_dwell_iterations) is not int or not 0 <= min_dwell_iterations:
            raise ValueError("Invalid iteration dwell")
        if len(candidates) ** (lag + 1) > MAX_CONTEXTS:
            raise ValueError("Candidate and lag combination exceeds the context limit")
        self.candidates = list(candidates)
        self.lag = lag
        self.cadence = cadence
        self.warmup = warmup
        self.min_dwell = min_dwell
        self.min_dwell_iterations = min_dwell_iterations
        self.min_trials = min_trials
        self.evaluation_identity = evaluation_identity
        #: An evolving Champion scores baseline-relative improvement; a fixed
        #: opponent keeps the historical absolute score. The two representations
        #: are never mixed in one predictor.
        self.champion_aware = active_champion_identity is not None
        self.effective_index = initial_index
        self.rng = random.Random(seed)
        #: Shared, persistent predictor history. It survives Champion promotions
        #: so knowledge transfers across regimes. ``weights`` is the per-observation
        #: evidence weight (completed games) for the baseline-relative mode.
        self.rewards: list[float] = []
        self.actions: list[int] = []
        self.weights: list[float] = []
        # Observations attributed per candidate (lifetime telemetry). Exploration
        # is gated on the per-context count in ``contexts[...]["n"]`` instead.
        self.counts = [0] * len(self.candidates)
        # Keyed by "candidate|context..." with the same ``lag`` shape for every
        # entry, so the state-size bound is ``candidates ** (lag + 1)``.
        self.contexts: dict[str, dict[str, Any]] = {}
        #: Lifetime valid observations (baselines included). Never reset at a
        #: promotion, so warm-up and cadence continue across the boundary.
        self.observations = 0
        self.learned = 0
        self.decisions = 0
        self.changes = 0
        self.coverage_pulls = 0
        self.last_decision_observation = 0
        self.last_change_observation = 0
        #: Training iteration of the last effective-rate change, for the optional
        #: iteration-based minimum dwell. ``None`` with ``changes == 0`` means no
        #: change has happened yet, so the dwell is already satisfied.
        self.last_change_iteration: int | None = None
        #: Observation count of the last exploratory rate change, so forced
        #: coverage cannot oscillate the rate on consecutive decisions.
        self.last_exploration_observation = 0
        #: Most recent training iteration seen, for the ``iterations_since_change``
        #: monitoring metric.
        self.last_iteration: int | None = None
        self.limit_hits = 0
        self.events: list[dict[str, Any]] = []
        self.reset_reason: str | None = None
        self.migrated_from: int | None = None
        #: Set when a resume explicitly overrode the effective rate, so the
        #: override is visible in monitoring rather than silent.
        self.override_learning_rate: float | None = None
        #: Cross-Champion improvement prior. Retained as diagnostic telemetry: a
        #: constant offset cannot change the ranking of candidates, so it is not
        #: used to select the rate.
        self.prior: dict[str, Any] = {"mean": 0.0, "m2": 0.0, "count": 0}
        #: Per-Champion evaluation state. Always present; ``identity`` is ``None``
        #: for a fixed-opponent run, which is a single implicit regime.
        self.champion: dict[str, Any] = self._fresh_champion(active_champion_identity, None)
        #: Lifetime count of applied Champion transitions (never a reset).
        self.transitions = 0
        self.last_promotion: dict[str, Any] | None = None

    # -- public interface -------------------------------------------------

    @property
    def effective_learning_rate(self) -> float:
        return self.candidates[self.effective_index]

    @property
    def active_champion_identity(self) -> str | None:
        """Identity of the Champion the current observations belong to."""
        return self.champion["identity"] if self.champion else None

    @property
    def promotion_resets(self) -> int:
        """Backward-compatible alias for the lifetime transition count."""
        return self.transitions

    @property
    def reward_semantics(self) -> str:
        return "baseline_relative" if self.champion_aware else "absolute"

    def _fresh_champion(
        self, identity: str | None, promotion_iteration: int | None
    ) -> dict[str, Any]:
        """A clean per-Champion evaluation block, seeded from the shared prior."""
        return {
            "identity": identity,
            "promotion_iteration": promotion_iteration,
            "protocol": self.evaluation_identity,
            "baseline_score": None,
            "baseline_games": 0,
            "baseline_se": 0.0,
            "effective_baseline": None,
            "baseline_iteration": None,
            "observations": 0,
            "count": 0,
            "mean": 0.0,
            "m2": 0.0,
            "offset": float(self.prior["mean"]),
            "uncertainty": 0.0,
            "score_se_sum_sq": 0.0,
            "scores": [],
            "last_score": None,
        }

    def set_effective_index(self, index: int, iteration: int | None = None) -> None:
        """Adopt an externally requested effective rate (a resume override).

        The learned predictors and counters are kept: the override only changes
        the rate applied from the next interval, and the dwell timer is restarted
        from ``iteration`` so the override is not immediately replaced. When the
        requested index is already effective, nothing is restarted. If the
        iteration is unknown and a change is actually made, the iteration dwell is
        started conservatively at the first subsequent observed iteration.
        """
        if not 0 <= index < len(self.candidates):
            raise ValueError("Effective index is out of range")
        if index == self.effective_index:
            # The requested rate is already effective: no state transition and no
            # dwell restart, only the visible record of the explicit request.
            self.override_learning_rate = self.candidates[index]
            return
        self.effective_index = index
        self.last_change_observation = self.observations
        self.last_change_iteration = iteration
        self.override_learning_rate = self.candidates[index]

    def _calibration_state(self) -> str:
        """Explicit Champion calibration state for monitoring and decisions.

        ``awaiting_evaluation`` means no post-promotion evaluation has arrived;
        ``baseline_established`` means only the baseline score exists;
        ``collecting`` means comparable observations exist but not yet enough for a
        decision; ``evidence_available`` means the controller may act on them. A
        fixed-opponent run reports ``not_applicable``.
        """
        if not self.champion_aware:
            return "not_applicable"
        champion = self.champion
        if champion["baseline_score"] is None:
            return "awaiting_evaluation"
        if champion["count"] == 0:
            return "baseline_established"
        if champion["count"] < EXPLORATION_MIN_EVIDENCE:
            return "collecting"
        return "evidence_available"

    def summary(self) -> dict[str, Any]:
        """A bounded live projection for progress files and monitoring."""
        champion = self.champion
        iterations_since_change = None
        if self.last_iteration is not None and self.last_change_iteration is not None:
            iterations_since_change = max(0, self.last_iteration - self.last_change_iteration)
        calibration_state = self._calibration_state()
        return {
            "algorithm": "hypercontroller",
            "state_version": STATE_VERSION,
            "reward_semantics": self.reward_semantics,
            "champion_aware": self.champion_aware,
            "candidates": list(self.candidates),
            "lag": self.lag,
            "cadence": self.cadence,
            "warmup": self.warmup,
            "min_dwell": self.min_dwell,
            "min_dwell_iterations": self.min_dwell_iterations,
            "min_trials": self.min_trials,
            "effective_index": self.effective_index,
            "effective_learning_rate": self.effective_learning_rate,
            # ``observations`` is the lifetime count and is never reset at a
            # promotion; ``regime_observations`` is the current Champion's count.
            "observations": self.observations,
            "regime_observations": champion["observations"] if champion else 0,
            "warmup_complete": self.observations >= self.warmup,
            "calibration_state": calibration_state,
            "evidence_sufficient": calibration_state == "evidence_available",
            "active_champion_identity": self.active_champion_identity,
            "champion": (
                {key: value for key, value in champion.items() if key != "scores"}
                if champion
                else None
            ),
            "prior": dict(self.prior),
            "learned": self.learned,
            "decisions": self.decisions,
            "changes": self.changes,
            "coverage_pulls": self.coverage_pulls,
            "last_decision_observation": self.last_decision_observation,
            "last_change_observation": self.last_change_observation,
            "last_change_iteration": self.last_change_iteration,
            "last_exploration_observation": self.last_exploration_observation,
            "last_iteration": self.last_iteration,
            "iterations_since_change": iterations_since_change,
            "evaluation_identity": self.evaluation_identity,
            "contexts": len(self.contexts),
            "limit_hits": self.limit_hits,
            "reset_reason": self.reset_reason,
            "migrated_from": self.migrated_from,
            "override_learning_rate": self.override_learning_rate,
            "transitions": self.transitions,
            "promotion_resets": self.transitions,
            "last_promotion": dict(self.last_promotion) if self.last_promotion else None,
            "last_event": self.events[-1] if self.events else None,
        }

    def observe(
        self,
        *,
        iteration: int,
        score: float | None,
        provenance: dict[str, Any] | None = None,
        promotion: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Ingest the observation for the interval that just finished.

        The action credited is the learning rate that was active during that
        interval. A missing or non-finite score is recorded but never advances a
        counter, so warm-up, cadence and dwell cannot be satisfied by noise.

        Three situations are distinguished:

        * A **learning observation** feeds the predictor and may drive a decision.
        * A **baseline-only observation** (the first evaluation after a Champion
          was installed) records the local baseline but never triggers a decision,
          moves the decision counters or advances the RNG.
        * A **missing observation** is recorded and changes nothing.

        ``promotion`` carries the :class:`EvaluationSelection` signal that a new
        Champion replaced the incumbent. The promoting observation still belongs
        to the outgoing Champion, so it is ingested there but never changes the
        learning rate; afterwards the controller *transitions* to the new
        Champion without discarding the learned predictors or the dwell counters.
        A promotion signal that was already applied is an inert duplicate: it
        never ingests the score or advances any counter.
        """
        started = time.monotonic()
        provenance = dict(provenance or {})
        if promotion is not None and self._already_promoted(promotion):
            # An already-processed promotion boundary is a no-op. Re-ingesting the
            # old Champion's score would contaminate the new regime's history and
            # could advance its warm-up or trigger a rate change, so nothing is
            # updated (not even the monitoring iteration) and no event is recorded.
            return {
                "iteration": iteration,
                "observation": None,
                "provenance": provenance,
                "attributed_learning_rate": self.effective_learning_rate,
                "previous_learning_rate": self.effective_learning_rate,
                "selected_learning_rate": self.effective_learning_rate,
                "changed": False,
                "skipped": "promotion_duplicate",
                "observations": self.observations,
            }
        if type(iteration) is int and iteration >= 0:
            self.last_iteration = iteration
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
            if promotion is not None:
                # A promotion is signalled independently of the score; apply the
                # transition even when this evaluation produced no observation.
                self._transition(promotion, event)
            self._record_event(event, started)
            return event
        raw = min(1.0, max(0.0, float(score)))
        reward, baseline = self._ingest(iteration, raw, provenance)
        if baseline:
            # A baseline-only observation must not independently trigger a rate
            # selection: it carries no comparable evidence yet.
            event = self._baseline_event(iteration, raw, provenance)
        else:
            event = self._decide(iteration, raw, provenance, promotion)
        if promotion is not None:
            self._transition(promotion, event)
        self._record_event(event, started)
        return event

    def _baseline_event(
        self, iteration: int, observation: float, provenance: dict[str, Any]
    ) -> dict[str, Any]:
        """The event for a baseline-only evaluation: no decision, no RNG."""
        rate = self.effective_learning_rate
        return {
            "iteration": iteration,
            "observation": round(observation, 6),
            "provenance": provenance,
            "attributed_learning_rate": rate,
            "previous_learning_rate": rate,
            "selected_learning_rate": rate,
            "changed": False,
            "skipped": "baseline",
            "baseline": True,
            "observations": self.observations,
        }

    def _observation_weight(self, provenance: dict[str, Any]) -> float:
        """Evidence weight of one observation, from its completed-game count."""
        games = provenance.get("completed_games")
        if type(games) is not int or games < 1:
            return 1.0
        return float(min(games, MAX_GAME_WEIGHT))

    @staticmethod
    def _score_se(provenance: dict[str, Any]) -> float:
        """Regularized standard error of one evaluation's completed-game score.

        The per-game score is 1 for a win, 0.5 for a draw and 0 for a loss, so its
        variance is computed exactly from the outcome counts. A weak prior variance
        (``SCORE_SE_PRIOR_VAR`` with a pseudo-count of one game) keeps a degenerate
        sample -- all wins or all draws -- from being treated as certain. This is a
        per-evaluation sampling uncertainty, not a confidence interval for the
        Champion's strength: the pairing and side balance of the protocol mean the
        games are not independent draws, which the weak prior and the fixed margin
        acknowledge rather than model exactly.
        """
        games = provenance.get("completed_games")
        if type(games) is not int or games < 1:
            return DEFAULT_UNCERTAINTY
        wins = provenance.get("wins")
        draws = provenance.get("draws")
        losses = provenance.get("losses")
        wins = wins if type(wins) is int and wins >= 0 else 0
        draws = draws if type(draws) is int and draws >= 0 else 0
        losses = losses if type(losses) is int and losses >= 0 else 0
        total = wins + draws + losses
        if total <= 0:
            total = games
        score = (wins + 0.5 * draws) / total
        second = (wins + 0.25 * draws) / total
        variance = max(0.0, second - score * score)
        regularized = (total * variance + SCORE_SE_PRIOR_VAR) / (total + SCORE_SE_PSEUDO)
        return min(MAX_SCORE_SE, math.sqrt(regularized / total))

    def _shrunk_baseline(self, score: float, games: int) -> float:
        """Shrink the first Champion score toward a neutral reference.

        A single evaluation is a noisy reference for a whole generation. Weighting
        it by its completed-game count against a neutral prior means an unusually
        high or low one-game baseline cannot dominate the improvements that follow.
        """
        weight = max(0, games)
        return (PRIOR_WEIGHT * NEUTRAL_SCORE + weight * score) / (PRIOR_WEIGHT + weight)

    def _ingest(
        self, iteration: int, raw: float, provenance: dict[str, Any]
    ) -> tuple[float | None, bool]:
        """Record one raw score and return ``(reward, baseline_only)``.

        A fixed opponent keeps the absolute score as the reward. A Champion uses
        the improvement over its shrunk local baseline; the first observation
        establishes that baseline and returns ``(None, True)``.
        """
        champion = self.champion
        self.observations += 1
        champion["observations"] += 1
        champion["last_score"] = raw
        champion["scores"].append(raw)
        if len(champion["scores"]) > MAX_SCORE_HISTORY:
            champion["scores"] = champion["scores"][-MAX_SCORE_HISTORY:]
        weight = self._observation_weight(provenance) if self.champion_aware else 1.0
        if not self.champion_aware:
            reward = raw
        else:
            if champion["baseline_score"] is None:
                champion["baseline_score"] = raw
                champion["baseline_games"] = int(provenance.get("completed_games") or 0)
                champion["baseline_se"] = self._score_se(provenance)
                champion["effective_baseline"] = self._shrunk_baseline(
                    raw, champion["baseline_games"]
                )
                champion["baseline_iteration"] = iteration
                return None, True
            reference = champion["effective_baseline"]
            if reference is None:
                reference = champion["baseline_score"]
            reward = min(1.0, max(-1.0, raw - float(reference)))
            # Update the Champion's Welford statistics and its shrunk offset. The
            # offset is diagnostic: a constant cannot change the candidate ranking.
            count = champion["count"] + 1
            delta = reward - champion["mean"]
            mean = champion["mean"] + delta / count
            m2 = champion["m2"] + delta * (reward - mean)
            champion["count"] = count
            champion["mean"] = mean
            champion["m2"] = m2
            champion["offset"] = self._shrunk_offset(mean, count)
            # The decision uncertainty combines the evaluation sampling error of
            # each improvement with the once-shared baseline error, so repeated
            # identical small evaluations never look certain.
            champion["score_se_sum_sq"] += self._score_se(provenance) ** 2
            champion["uncertainty"] = math.sqrt(
                champion["baseline_se"] ** 2 + champion["score_se_sum_sq"] / (count * count)
            )
            self._update_prior(reward)
        action = self.effective_index
        self.rewards.append(reward)
        self.actions.append(action)
        self.weights.append(weight)
        self.counts[action] += 1
        if len(self.rewards) > MAX_REWARD_HISTORY:
            self.rewards = self.rewards[-MAX_REWARD_HISTORY:]
            self.actions = self.actions[-MAX_REWARD_HISTORY:]
            self.weights = self.weights[-MAX_REWARD_HISTORY:]
        try:
            if self._learn():
                # Only an observation that actually updated a predictor counts as
                # learned; warm-up observations cannot satisfy coverage.
                self.learned += 1
        except (ValueError, TypeError, ZeroDivisionError):
            # A malformed window must never abort training; skip this update.
            pass
        return reward, False

    def _shrunk_offset(self, mean: float, count: int) -> float:
        """Shrink a Champion's mean improvement toward the cross-Champion prior."""
        prior_mean = float(self.prior["mean"])
        return (PRIOR_WEIGHT * prior_mean + count * mean) / (PRIOR_WEIGHT + count)

    def _update_prior(self, improvement: float) -> None:
        """Fold one improvement into the cross-Champion prior (diagnostic)."""
        count = self.prior["count"] + 1
        delta = improvement - self.prior["mean"]
        mean = self.prior["mean"] + delta / count
        m2 = self.prior["m2"] + delta * (improvement - mean)
        self.prior = {"mean": mean, "m2": m2, "count": count}

    def _already_promoted(self, promotion: dict[str, Any]) -> bool:
        """Whether this promotion signal was already applied to the controller.

        Promotion iterations strictly increase, so an event at or before the most
        recently applied promotion is a duplicate or a stale replay and must never
        move the controller to a stale regime. That ordering check rejects an
        older promotion replayed after later ones, which a last-event-only
        comparison would miss. The identity check covers a replayed event whose
        iteration is unavailable; it is never used to reject a valid later
        promotion, which has a strictly greater iteration.
        """
        iteration = promotion.get("iteration")
        last_iteration = self.last_promotion.get("iteration") if self.last_promotion else None
        if type(iteration) is int and type(last_iteration) is int and iteration <= last_iteration:
            return True
        promoted_identity = promotion.get("active_champion_identity")
        if isinstance(promoted_identity, str) and promoted_identity:
            return self.active_champion_identity == promoted_identity
        return False

    def _transition(self, promotion: dict[str, Any], event: dict[str, Any]) -> None:
        """Move to a new Champion without discarding persistent learning.

        This is an idempotent *transition*, not a reset. The effective rate, the
        shared predictors, the reward/action/weight history, the lifetime counters,
        the RNG stream and the dwell timers all survive; only the per-Champion
        evaluation block is refreshed, so the next evaluation establishes the new
        Champion's local baseline. Warm-up is not restarted, which is what keeps
        dwell periods continuous across promotions.
        """
        preserved = self.effective_learning_rate
        self.transitions += 1
        self.reset_reason = "opponent_promoted"
        promoted_identity = promotion.get("active_champion_identity")
        new_identity = promoted_identity if isinstance(promoted_identity, str) else None
        promotion_iteration = promotion.get("iteration")
        self.champion = self._fresh_champion(
            new_identity,
            promotion_iteration if type(promotion_iteration) is int else None,
        )
        champion_iteration = promotion.get("champion_iteration")
        identity = promotion.get("opponent_identity")
        self.last_promotion = {
            "iteration": promotion.get("iteration"),
            "champion_iteration": champion_iteration if type(champion_iteration) is int else None,
            "opponent_identity": identity if isinstance(identity, str) else None,
            "preserved_learning_rate": preserved,
        }
        event["promotion_reset"] = True
        event["transition"] = True
        event["preserved_learning_rate"] = preserved

    def _decide(
        self,
        iteration: int,
        observation: float,
        provenance: dict[str, Any],
        promotion: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        previous_index = self.effective_index
        previous_rate = self.candidates[previous_index]
        event: dict[str, Any] = {
            "iteration": iteration,
            "observation": round(observation, 6),
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
        if promotion is not None:
            # The promotion boundary never changes the rate. The observation is
            # still attributed to the outgoing Champion; the transition then
            # installs the new Champion without touching the effective rate.
            event["skipped"] = "promotion_transition"
            return event
        if self.observations < self.warmup:
            event["skipped"] = "warmup"
            return event
        if self.observations - self.last_decision_observation < self.cadence:
            event["skipped"] = "cadence"
            return event
        if self.observations - self.last_change_observation < self.min_dwell:
            event["skipped"] = "dwell"
            return event
        if not self._iteration_dwell_ok(iteration):
            event["skipped"] = "dwell_iterations"
            return event
        self.last_decision_observation = self.observations
        self.decisions += 1
        try:
            chosen, status = self._select()
        except (ValueError, TypeError, ZeroDivisionError):
            event["skipped"] = "fallback"
            return event
        event["selected_index"] = chosen
        event["selected_learning_rate"] = self.candidates[chosen]
        event["exploratory"] = status == "explore"
        if status == "uncertain":
            # The evidence does not justify a change; the current rate is kept.
            event["skipped"] = "uncertain"
        elif status == "insufficient":
            event["skipped"] = "insufficient_evidence"
        if chosen != previous_index:
            self.effective_index = chosen
            self.last_change_observation = self.observations
            self.last_change_iteration = iteration
            self.changes += 1
            event["changed"] = True
            if status == "explore":
                # Start the exploration cooldown only when a change happened, so a
                # coverage pull that keeps the current rate does not consume it.
                self.last_exploration_observation = self.observations
        event["effective_learning_rate"] = self.effective_learning_rate
        return event

    def _iteration_dwell_ok(self, iteration: int) -> bool:
        """Whether the optional iteration-based minimum dwell has elapsed.

        When the last change's iteration is unknown but a change did happen
        (legacy state or an override without iteration metadata), the clock starts
        conservatively at the first observed iteration instead of being treated as
        already satisfied.
        """
        if self.min_dwell_iterations <= 0:
            return True
        if self.last_change_iteration is None:
            if self.changes > 0:
                self.last_change_iteration = iteration
                return False
            return True
        return iteration - self.last_change_iteration >= self.min_dwell_iterations

    def _decision_uncertainty(self) -> float:
        """Regularized standard error of the current Champion's mean improvement.

        The estimate combines the per-evaluation sampling error of every
        improvement with the once-shared baseline error (see :meth:`_score_se`),
        so repeated identical small evaluations keep a meaningful uncertainty
        instead of collapsing to a numerical floor.
        """
        champion = self.champion
        if champion["count"] < 1:
            return DEFAULT_UNCERTAINTY
        value = float(champion["uncertainty"])
        return value if value > 0.0 else DEFAULT_UNCERTAINTY

    def _log_distance(self, first: int, second: int) -> float:
        """Multiplicative distance between two candidate learning rates.

        Candidate-list position is not rate distance: adjacent candidates can
        still span orders of magnitude. The absolute log-ratio is the natural
        scale-invariant distance between two positive rates.
        """
        return abs(math.log(self.candidates[first]) - math.log(self.candidates[second]))

    def _nearest_candidate(
        self, current: int, eligible: list[int], *, least_observed_first: bool = False
    ) -> int:
        """The eligible candidate closest to the current rate, deterministically.

        Ties break toward the lower index, so no RNG is consumed. When
        ``least_observed_first`` is set (the no-context exploration path), the
        least-observed candidates are preferred first and the log-distance only
        breaks ties, so every candidate is eventually explored without an
        unbounded random walk. The distance is always the tie-breaker, so a far
        candidate is only chosen when no closer one is eligible.
        """
        if least_observed_first:
            return min(
                eligible,
                key=lambda index: (
                    self.counts[index],
                    round(self._log_distance(current, index), 9),
                    index,
                ),
            )
        return min(
            eligible,
            key=lambda index: (round(self._log_distance(current, index), 9), index),
        )

    def _select(self) -> tuple[int, str]:
        """Choose the next candidate index and the reason for the choice.

        Returns ``(index, status)`` where ``status`` is one of ``"explore"``
        (a coverage pull for an unobserved candidate/context), ``"exploit"`` (an
        evidence-based choice), ``"uncertain"`` (the evidence is too weak to move
        the rate) or ``"insufficient"`` (too few comparable observations to act
        at all). Fixed-opponent selection is the historical greedy rule;
        Champion-aware selection gates every automatic change on comparable
        evidence, applies the exploration cooldown, explores the multiplicatively
        nearest candidate and applies the confidence margin.
        """
        size = len(self.candidates)
        current = self.effective_index
        # The minimum comparable-evidence requirement applies to every automatic
        # Champion change, exploitation included. A new Champion's first
        # comparable evaluation cannot move the rate even when the shared
        # predictor strongly favours another candidate. No RNG or exploration
        # counter is consumed while this gate blocks.
        if self.champion_aware and self.champion["count"] < EXPLORATION_MIN_EVIDENCE:
            return current, "insufficient"
        if len(self.rewards) < self.lag:
            # Too little history to form a context window. Fixed opponents keep
            # their historical uniform exploration; a Champion explores the
            # least-observed candidate closest to the current rate instead of
            # jumping at random.
            if not self.champion_aware:
                self.coverage_pulls += 1
                return self.rng.randrange(size), "explore"
            if self.observations - self.last_exploration_observation < EXPLORATION_DWELL:
                return current, "uncertain"
            eligible = [index for index in range(size) if index != current]
            if not eligible:
                return current, "exploit"
            self.coverage_pulls += 1
            return self._nearest_candidate(current, eligible, least_observed_first=True), "explore"
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
            if not self.champion_aware:
                self.coverage_pulls += 1
                return untried[self.rng.randrange(len(untried))], "explore"
            if self.observations - self.last_exploration_observation < EXPLORATION_DWELL:
                return current, "uncertain"
            self.coverage_pulls += 1
            # Prefer the multiplicatively nearest unobserved candidate so
            # exploration never jumps between extreme rates; ties break toward
            # the lower index, which is deterministic and needs no RNG.
            others = [index for index in untried if index != current]
            if not others:
                return current, "exploit"
            return self._nearest_candidate(current, others), "explore"
        values = [self._prediction(index, context, window) for index in range(size)]
        counts = [self._context_count(index, context) for index in range(size)]
        best_index = 0
        for index in range(1, size):
            if values[index] > values[best_index] + 1e-12:
                best_index = index
            elif (
                abs(values[index] - values[best_index]) <= 1e-12
                and counts[index] < counts[best_index]
            ):
                best_index = index
        if not self.champion_aware:
            return best_index, "exploit"
        if best_index == current:
            return current, "exploit"
        margin = HYSTERESIS * self._decision_uncertainty()
        if values[best_index] - values[current] <= margin:
            return current, "uncertain"
        return best_index, "exploit"

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
        weight = self.weights[target] if target < len(self.weights) else 1.0
        return self._update(action, context, window, self.rewards[target], weight)

    def _update(
        self,
        action: int,
        context: tuple[int, ...],
        window: list[float],
        reward: float,
        weight: float = 1.0,
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
        weight = max(0.0, float(weight))
        matrix = entry["V"]
        vector = entry["b"]
        for row in range(self.lag):
            for column in range(self.lag):
                matrix[row][column] += weight * window[row] * window[column]
            vector[row] += weight * reward * window[row]
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
        champion = self.champion
        return {
            "version": STATE_VERSION,
            "algorithm": "hypercontroller",
            "reward_semantics": self.reward_semantics,
            "candidates": list(self.candidates),
            "lag": self.lag,
            "cadence": self.cadence,
            "warmup": self.warmup,
            "min_dwell": self.min_dwell,
            "min_dwell_iterations": self.min_dwell_iterations,
            "min_trials": self.min_trials,
            "evaluation_identity": self.evaluation_identity,
            # Convenience alias for the Champion block's identity, kept so a
            # reader that only knows the version-4 layout still finds it.
            "active_champion_identity": self.active_champion_identity,
            "champion": (
                {
                    key: (list(value) if key == "scores" else value)
                    for key, value in champion.items()
                }
                if champion
                else None
            ),
            "prior": dict(self.prior),
            "effective_index": self.effective_index,
            "observations": self.observations,
            "learned": self.learned,
            "decisions": self.decisions,
            "changes": self.changes,
            "coverage_pulls": self.coverage_pulls,
            "last_decision_observation": self.last_decision_observation,
            "last_change_observation": self.last_change_observation,
            "last_change_iteration": self.last_change_iteration,
            "last_exploration_observation": self.last_exploration_observation,
            "last_iteration": self.last_iteration,
            "counts": list(self.counts),
            "rewards": list(self.rewards),
            "actions": list(self.actions),
            "weights": list(self.weights),
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
            "transitions": self.transitions,
            "last_promotion": dict(self.last_promotion) if self.last_promotion else None,
        }

    def _reset_champion_state(
        self,
        *,
        identity: str | None,
        reason: str,
    ) -> "HyperController":
        """Refresh only the per-Champion evaluation block.

        The learned predictors, shared history, lifetime counters, effective rate,
        RNG stream and dwell timers are all preserved. Used when the restored
        Champion cannot be proven to be the one the per-Champion statistics were
        collected against; the global learning remains valid because it is
        expressed as within-Champion improvement.
        """
        self.champion = self._fresh_champion(identity, None)
        self.reset_reason = reason
        return self

    def _discard_learned_state(self, *, identity: str | None, reason: str) -> "HyperController":
        """Drop predictors whose reward representation no longer applies.

        Used only during migration, when a saved state was trained on a different
        reward representation (absolute scores versus baseline-relative
        improvement). The effective rate, optimizer state, lifetime counters, dwell
        timers, per-candidate telemetry and the RNG stream are preserved, so
        training continues from the same rate with a clean calibrated history.
        """
        self.contexts = {}
        self.rewards = []
        self.actions = []
        self.weights = []
        self.coverage_pulls = 0
        self.learned = 0
        self.champion = self._fresh_champion(identity, None)
        self.reset_reason = reason
        return self

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
        active_champion_identity: str | None = None,
        min_dwell_iterations: int = 0,
        current_iteration: int | None = None,
    ) -> "HyperController":
        """Restore a controller, migrating older schemas and refreshing regimes.

        A saved candidate set, lag or evaluation identity that no longer matches
        cannot be reused, so the whole learned state resets with an explicit
        reason. A changed reward representation (an absolute-score state resumed
        into a baseline-relative Champion, or the reverse) also discards the
        learned predictors, because a predictor must never mix incompatible
        rewards. A changed active Champion alone refreshes only the per-Champion
        block, because the shared observations are already within-Champion
        improvements. In every case the model, optimizer and replay are untouched.
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
            active_champion_identity=active_champion_identity,
            min_dwell_iterations=min_dwell_iterations,
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
            last_change_iteration = _validate_optional_iteration(state.get("last_change_iteration"))
            last_exploration = _validate_counter(
                state.get("last_exploration_observation", 0), "last-exploration"
            )
            last_iteration = _validate_optional_iteration(state.get("last_iteration"))
            limit_hits = _validate_counter(state.get("limit_hits", 0), "limit-hit")
            # Version 4 stored the transition count as ``promotion_resets``.
            transitions = _validate_counter(
                state.get("transitions", state.get("promotion_resets", 0)), "transition"
            )
            last_promotion = _validate_promotion(state.get("last_promotion"), candidates)
            if bool(transitions) != bool(last_promotion):
                raise ValueError("Controller transition counters are inconsistent")
            if last_decision > observations or last_change > observations:
                raise ValueError("Controller observation counters are inconsistent")
            events = _validate_events(state.get("events", []), candidates)
            rng = _restore_rng(state["rng"], seed)
            if version >= 6:
                # The current schema requires the complete structure.
                semantics = state.get("reward_semantics")
                if semantics not in REWARD_SEMANTICS:
                    raise ValueError("Controller reward semantics are invalid")
                weights = _validate_number_list(state.get("weights"), MAX_REWARD_HISTORY, "weight")
                if len(weights) != len(rewards):
                    raise ValueError("Controller reward and weight histories disagree")
                if any(not 1.0 <= item <= MAX_GAME_WEIGHT for item in weights):
                    raise ValueError("Controller observation weight is invalid")
                champion = _validate_champion_state(
                    state.get("champion"), evaluation_identity, strict=True
                )
                if champion is None:
                    raise ValueError("Controller version-6 state requires a Champion block")
                if champion["protocol"] != evaluation_identity:
                    raise ValueError("Controller Champion protocol does not match the evaluation")
                prior = _validate_prior(state.get("prior"), required=True)
                saved_active = _validate_champion_identity(state.get("active_champion_identity"))
                if saved_active != champion["identity"]:
                    raise ValueError("Controller Champion identity is inconsistent")
            else:
                # Historical schema. Version 1-4 used absolute scores; version 5
                # used baseline-relative improvement for every opponent.
                semantics = "absolute" if version <= 4 else "baseline_relative"
                weights = [1.0] * len(rewards)
                champion = (
                    _validate_champion_state(state.get("champion"), evaluation_identity)
                    if version == 5
                    else None
                )
                prior = (
                    _validate_prior(state.get("prior"))
                    if version == 5
                    else {"mean": 0.0, "m2": 0.0, "count": 0}
                )
                saved_active = _validate_champion_identity(state.get("active_champion_identity"))
                if champion is not None:
                    if (
                        champion["effective_baseline"] is None
                        and champion["baseline_score"] is not None
                    ):
                        champion["effective_baseline"] = champion["baseline_score"]
                    if champion["protocol"] != evaluation_identity:
                        raise ValueError(
                            "Controller Champion protocol does not match the evaluation"
                        )
                    if saved_active != champion["identity"]:
                        raise ValueError("Controller Champion identity is inconsistent")
                    # Version 5 estimated uncertainty from the between-observation
                    # variance, which could collapse to zero. Replace it with the
                    # conservative regularized estimate used by the current
                    # schema, so a migrated Champion is never treated as certain.
                    champion["baseline_se"] = (
                        DEFAULT_UNCERTAINTY if champion["baseline_score"] is not None else 0.0
                    )
                    champion["score_se_sum_sq"] = champion["count"] * DEFAULT_UNCERTAINTY**2
                    if champion["count"] > 0:
                        champion["uncertainty"] = math.sqrt(
                            champion["baseline_se"] ** 2
                            + champion["score_se_sum_sq"] / (champion["count"] ** 2)
                        )
                elif version == 5:
                    # Version 5 always carried a Champion block; a state without
                    # one is malformed, not a genuine legacy schema.
                    raise ValueError("Controller version-5 state requires a Champion block")
        except (KeyError, TypeError, ValueError, IndexError, OverflowError):
            controller.reset_reason = "unreadable_state"
            return controller
        saved_identity = champion["identity"] if champion is not None else saved_active
        # Adopt the validated state, then reconcile semantics and the Champion.
        controller.contexts = contexts
        controller.rewards = rewards
        controller.actions = actions
        controller.weights = weights
        controller.counts = counts
        controller.effective_index = effective_index
        controller.observations = observations
        controller.learned = learned
        controller.decisions = decisions
        controller.changes = changes
        controller.coverage_pulls = coverage_pulls
        controller.last_decision_observation = last_decision
        controller.last_change_observation = last_change
        controller.last_change_iteration = last_change_iteration
        controller.last_exploration_observation = last_exploration
        controller.last_iteration = last_iteration
        controller.limit_hits = limit_hits
        controller.events = events
        controller.rng = rng
        controller.transitions = transitions
        controller.last_promotion = last_promotion
        controller.prior = prior
        if champion is None:
            champion = controller._fresh_champion(saved_identity, None)
            champion["observations"] = observations
            champion["offset"] = float(prior["mean"])
        controller.champion = champion
        if version < 6:
            controller.migrated_from = version
        if semantics != controller.reward_semantics:
            # The saved predictors were trained on a different reward
            # representation and must never be mixed with the current one. Keep
            # the effective rate, optimizer state, lifetime counters and dwell.
            controller._discard_learned_state(
                identity=active_champion_identity, reason="reward_semantics_changed"
            )
        elif saved_identity != active_champion_identity:
            # Same reward representation but a different Champion for the
            # per-Champion statistics. Refresh only that block: the shared
            # predictors stay valid.
            controller._reset_champion_state(
                identity=active_champion_identity, reason="champion_changed"
            )
        if (
            version < 6
            and controller.min_dwell_iterations > 0
            and controller.last_change_iteration is None
            and controller.changes > 0
        ):
            # A legacy state has no iteration metadata for its last change. Start
            # the conservative dwell clock at the resume iteration.
            controller.last_change_iteration = current_iteration
        return controller


def resolve_controller(
    config: dict,
    *,
    saved_state: Any,
    evaluation_identity: str,
    seed: int,
    resume: bool,
    override_learning_rate: float | None = None,
    active_champion_identity: str | None = None,
    current_iteration: int | None = None,
) -> HyperController | None:
    """Build the controller for a run, or ``None`` when adaptation is off.

    A resume whose evaluation identity differs from the saved one resets the
    learned state inside :meth:`HyperController.from_state`, so observations from
    a different objective are never mixed. ``active_champion_identity`` names the
    Champion the restored observations must belong to; a mismatch refreshes only
    the per-Champion evaluation block while the shared predictors are preserved.
    ``current_iteration`` is the resume iteration, used to start the conservative
    iteration dwell after an explicit override or a legacy migration.
    ``override_learning_rate`` is a rate the caller explicitly requested for a
    resume; it is honoured by moving the restored controller's effective rate
    (keeping the learned state) so the override is never silently discarded.
    """
    if config.get("adaptation", "off") == "off":
        return None
    candidates = candidates_from_config(config)
    learning_rate = float(config.get("learning_rate", candidates[0]))
    initial_index = candidates.index(learning_rate) if learning_rate in candidates else 0
    min_dwell_iterations = int(config.get("adaptation_min_dwell_iterations", 0))
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
            active_champion_identity=active_champion_identity,
            min_dwell_iterations=min_dwell_iterations,
            current_iteration=current_iteration,
        )
        if override_learning_rate is not None and override_learning_rate in candidates:
            # The rate must already be a candidate: normalize_config enforces it.
            # An explicit override is honoured even after a regime refresh, so a
            # user request is never silently discarded.
            controller.set_effective_index(
                candidates.index(override_learning_rate), iteration=current_iteration
            )
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
        active_champion_identity=active_champion_identity,
        min_dwell_iterations=min_dwell_iterations,
    )
