"""Optional online learning-rate adaptation (HyperController).

These tests cover the controller in isolation (timing, bounds, serialization and
numerical behaviour) and one end-to-end training path that proves the controller
runs inside the real training loop, survives a checkpoint round-trip and never
rebuilds the optimizer. The algorithm's honest signal is the completed-game score
against one fixed opponent; see docs/designs/online-hyperparameter-adaptation.md.
"""

from __future__ import annotations

import json
import math

import pytest
import torch

from hnefatafl.hyper_adaptation import (
    MAX_CANDIDATES_ITEMS,
    MAX_CANDIDATES_TEXT,
    MAX_CONTEXTS,
    MAX_EVENTS,
    HyperController,
    format_candidates,
    observation_from_evaluation,
    parse_candidates,
    resolve_controller,
    sanitize_inactive_adaptation,
)
from hnefatafl.models import load_checkpoint
from hnefatafl.training import _set_optimizer_learning_rate, normalize_config, train, tune


def smoke_config(tmp_path, **changes):
    return {
        "preset": "smoke",
        "ruleset": "brandubh",
        "device": "cpu",
        "seed": 0,
        "output_dir": str(tmp_path / "training"),
        "registry_dir": str(tmp_path / "models"),
        **changes,
    }


def adapted_config(tmp_path, **changes):
    options = {
        "iterations": 4,
        "evaluation_games": 4,
        "evaluation_frequency": 1,
        "adaptation": "hypercontroller",
        "adaptation_lr_candidates": "0.0003,0.001,0.003",
        "adaptation_cadence": 1,
        "adaptation_warmup": 1,
        "adaptation_min_dwell": 1,
        "adaptation_min_trials": 1,
        "adaptation_lag": 1,
        "learning_rate": 0.001,
    }
    options.update(changes)
    return smoke_config(tmp_path, **options)


def resumed_adapted_config(tmp_path, **changes):
    """An adapted resume that does not explicitly override the learning rate.

    The API merges the saved inputs into the request, so an unchanged resume
    carries the saved value. Omitting the key here models that; passing it would
    be an explicit override.
    """
    config = adapted_config(tmp_path, **changes)
    config.pop("learning_rate", None)
    return config


def make_controller(**changes) -> HyperController:
    options = {
        "candidates": [0.0003, 0.001, 0.003],
        "lag": 1,
        "cadence": 1,
        "warmup": 1,
        "min_dwell": 1,
        "min_trials": 1,
        "initial_index": 1,
        "seed": 7,
        "evaluation_identity": "shield_bearer:test",
    }
    options.update(changes)
    return HyperController(**options)


# ── Configuration ────────────────────────────────────────────────────────────


def test_adaptation_defaults_off_and_backward_compatible(tmp_path):
    config = normalize_config(smoke_config(tmp_path))
    assert config["adaptation"] == "off"
    assert config["adaptation_lr_candidates"] == "0.0003,0.001,0.003"
    result = train(smoke_config(tmp_path))
    _, payload = load_checkpoint(result["checkpoint"])
    # A job that never enables adaptation stores no controller state at all.
    assert "adaptation_state" not in payload["extra"]


@pytest.mark.parametrize(
    "changes",
    [
        {"adaptation": "pbt"},
        {"adaptation_lr_candidates": "0.001"},
        {"adaptation_lr_candidates": "0.001,0.001"},
        {"adaptation_lr_candidates": "0.001,0"},
        {"adaptation_lr_candidates": "0.001,2"},
        {"adaptation_lr_candidates": "0.001,abc"},
        {"adaptation_cadence": 0},
        {"adaptation_warmup": -1},
        {"adaptation_min_dwell": 0},
        {"adaptation_lag": 4},
        {"adaptation_min_trials": 101},
    ],
)
def test_adaptation_rejects_invalid_inputs(tmp_path, changes):
    with pytest.raises(ValueError):
        normalize_config(adapted_config(tmp_path, **changes))


@pytest.mark.parametrize(
    "mode",
    [True, False, None, 1, 0.5, ["off"], {"mode": "off"}, "pbt", "OFF", ""],
)
def test_adaptation_rejects_invalid_modes(tmp_path, mode):
    # The mode selector must never be silently coerced to off.
    with pytest.raises(ValueError, match="adaptation must be off or hypercontroller"):
        normalize_config({**smoke_config(tmp_path), "adaptation": mode})


def test_adaptation_mode_absent_defaults_off(tmp_path):
    # Legacy configurations that omit the field entirely still default to off.
    config = normalize_config(smoke_config(tmp_path))
    assert config["adaptation"] == "off"
    assert config["adaptation_lr_candidates"] == "0.0003,0.001,0.003"


def test_adaptation_off_still_sanitizes_malformed_subordinate_fields(tmp_path):
    config = normalize_config(
        {
            **smoke_config(tmp_path),
            "adaptation": "off",
            "adaptation_lr_candidates": "0.001,garbage",
            "adaptation_cadence": 0,
        }
    )
    assert config["adaptation"] == "off"
    assert config["adaptation_lr_candidates"] == "0.0003,0.001,0.003"
    assert config["adaptation_cadence"] == 1


def test_adaptation_allows_champion_and_requires_informative_games(tmp_path):
    # A changing Champion is a supported objective: the controller detects each
    # promotion from the selection decision and restarts its opponent-specific
    # state, so it is no longer rejected as an incompatible opponent.
    config = normalize_config(adapted_config(tmp_path, evaluation_opponent="champion"))
    assert config["adaptation"] == "hypercontroller"
    assert config["evaluation_opponent"] == "champion"
    with pytest.raises(ValueError, match="at least 4"):
        normalize_config(adapted_config(tmp_path, evaluation_games=2))


def test_adaptation_requires_the_configured_rate_to_be_a_candidate(tmp_path):
    with pytest.raises(ValueError, match="one of the adaptation_lr_candidates"):
        normalize_config(adapted_config(tmp_path, learning_rate=0.002))


def test_adaptation_off_ignores_inactive_controller_settings(tmp_path):
    # Enable, enter invalid candidates, then disable: the inactive controller
    # settings must not block the run and must not rewrite unrelated settings.
    # Unsafe inactive values are replaced with bounded defaults, not persisted.
    config = normalize_config(
        smoke_config(
            tmp_path,
            adaptation="off",
            adaptation_lr_candidates="0.001,garbage",
            adaptation_cadence=0,
            learning_rate=0.0007,
        )
    )
    assert config["adaptation"] == "off"
    assert config["learning_rate"] == 0.0007
    assert config["adaptation_lr_candidates"] == "0.0003,0.001,0.003"
    assert config["adaptation_cadence"] == 1


def test_disabling_adaptation_after_an_invalid_edit_still_trains(tmp_path):
    torch.set_num_threads(1)
    torch.manual_seed(0)
    result = train(
        smoke_config(
            tmp_path,
            iterations=1,
            adaptation="off",
            adaptation_lr_candidates="0.001,garbage",
        )
    )
    assert result["status"] == "completed"
    _, payload = load_checkpoint(result["checkpoint"])
    assert "adaptation_state" not in payload["extra"]


def test_partial_resume_rejects_invalid_candidates_before_loading(tmp_path):
    # Independently invalid controller input is still rejected before a
    # checkpoint is read, even though the cross-field checks are deferred.
    with pytest.raises(ValueError, match="must contain numbers"):
        train(
            {
                "preset": "smoke",
                "ruleset": "brandubh",
                "device": "cpu",
                "seed": 0,
                "output_dir": str(tmp_path / "out"),
                "resume": str(tmp_path / "missing.pt"),
                "adaptation": "hypercontroller",
                "adaptation_lr_candidates": "0.001,garbage",
            }
        )


def test_adaptation_state_size_limit_is_derived_and_enforced(tmp_path):
    # 8 candidates and lag 3 produce 8**4 = 4096 contexts, above the 512 limit.
    with pytest.raises(ValueError, match="too many controller contexts"):
        normalize_config(
            adapted_config(
                tmp_path,
                adaptation_lag=3,
                adaptation_lr_candidates="0.0001,0.0002,0.0003,0.0004,0.001,0.002,0.003,0.01",
                learning_rate=0.001,
            )
        )


def test_tune_rejects_adaptation(tmp_path):
    with pytest.raises(ValueError, match="Disable automatic hyperparameter updates"):
        tune(
            {
                "output_dir": str(tmp_path / "tuning"),
                "trials": 1,
                "training": adapted_config(tmp_path),
            }
        )


def test_candidate_parsing_is_sorted_unique_and_bounded():
    assert parse_candidates("0.003, 0.0003,0.001,0.001") == [0.0003, 0.001, 0.003]
    assert parse_candidates(["0.001", 0.0003]) == [0.0003, 0.001]


def test_parse_candidates_bounds_oversized_input():
    # A string is rejected by length before it is split into entries.
    with pytest.raises(ValueError, match="at most .* characters"):
        parse_candidates("0.001," * (MAX_CANDIDATES_TEXT // 2 + 1))
    # A list is rejected by item count before any conversion.
    with pytest.raises(ValueError, match="at most .* entries"):
        parse_candidates([0.001] * (MAX_CANDIDATES_ITEMS + 1))
    # Duplicates and empty entries within the bounds still deduplicate.
    assert parse_candidates([0.001] * 200 + [0.002]) == [0.001, 0.002]
    assert parse_candidates("0.001," * 80 + "0.002") == [0.001, 0.002]
    assert parse_candidates(",,,,0.001,,,,0.002,,,,") == [0.001, 0.002]


def test_parse_candidates_bounds_oversized_list_elements():
    oversized = "9" * (MAX_CANDIDATES_TEXT + 1)
    # A list or tuple element is bounded before float() conversion, so an
    # oversized element cannot consume unbounded time or memory.
    for value in ([oversized, 0.001], (oversized, 0.001)):
        with pytest.raises(ValueError, match="elements must be at most .* characters"):
            parse_candidates(value)
    # Exactly the permitted length passes the size guard and is then rejected as
    # an out-of-range value, not by the element bound.
    with pytest.raises(ValueError, match="between 1e-7 and 1"):
        parse_candidates(["9" * MAX_CANDIDATES_TEXT, 0.001])
    # Valid numeric, alternate-syntax and near-identical entries still parse.
    assert parse_candidates(["0.001", 0.002, "0.003"]) == [0.001, 0.002, 0.003]
    assert parse_candidates(("1e-3", "0.0_002", "0.003")) == [0.0002, 0.001, 0.003]
    assert parse_candidates(["0.00012345678912", "0.00012345678913", 0.001]) == [
        0.00012345678912,
        0.00012345678913,
        0.001,
    ]


def test_parse_candidates_rejects_overflow_and_non_finite():
    # An arbitrarily large integer raises OverflowError inside float(); it must
    # become the same controlled validation error as any other bad entry.
    with pytest.raises(ValueError, match="must contain numbers"):
        parse_candidates([10**400, 0.001])
    for value in ("0.001,inf", "0.001,nan", "0.001,1e400"):
        with pytest.raises(ValueError, match="between 1e-7 and 1"):
            parse_candidates(value)


def test_parse_candidates_keeps_exact_and_alternate_syntax():
    close = [0.00012345678912, 0.00012345678913, 0.001]
    assert parse_candidates(format_candidates(close)) == close
    # Scientific notation and Python-compatible underscores remain supported.
    assert parse_candidates("1e-3,0.0_002,0.003") == [0.0002, 0.001, 0.003]


def test_oversized_or_overflowing_candidates_rejected_before_training(tmp_path):
    # normalize_config runs before any checkpoint load or output directory write,
    # so an invalid candidate transport never reaches expensive setup.
    with pytest.raises(ValueError, match="adaptation_lr_candidates"):
        train(adapted_config(tmp_path, adaptation_lr_candidates="0.001," * 1000))
    with pytest.raises(ValueError, match="adaptation_lr_candidates"):
        train(adapted_config(tmp_path, adaptation_lr_candidates=[10**400, 0.001]))


def test_candidate_serialization_round_trips_exactly():
    # Values that fixed-precision formatting collapsed must stay distinct.
    close = [0.00012345678912, 0.00012345678913, 0.001]
    text = format_candidates(close)
    assert parse_candidates(text) == close
    assert len(parse_candidates(text)) == 3
    # Boundaries, scientific notation and irrational-looking rates stay exact.
    for values in (
        [1e-7, 1.0],
        [1e-7, 3e-7, 1.0],
        [0.3333333333333333, 0.6666666666666666, 1.0],
    ):
        assert parse_candidates(format_candidates(values)) == values
    # Duplicate detection happens before formatting and survives it.
    deduplicated = parse_candidates("0.001,1e-3,0.002")
    assert deduplicated == [0.001, 0.002]
    assert parse_candidates(format_candidates(deduplicated)) == deduplicated


def test_normalization_preserves_candidate_identity_and_membership(tmp_path):
    config = adapted_config(
        tmp_path,
        adaptation_lr_candidates="0.00012345678912,0.00012345678913,0.001",
        learning_rate=0.00012345678913,
    )
    normalized = normalize_config(config)
    candidates = parse_candidates(normalized["adaptation_lr_candidates"])
    assert candidates == [0.00012345678912, 0.00012345678913, 0.001]
    assert normalized["learning_rate"] in candidates
    # Serialization is idempotent, so configuration, checkpoint and controller
    # all see the identical candidate set.
    again = normalize_config(normalized)
    assert again["adaptation_lr_candidates"] == normalized["adaptation_lr_candidates"]
    controller = resolve_controller(
        again, saved_state=None, evaluation_identity="shield_bearer:x", seed=1, resume=False
    )
    assert controller is not None and controller.candidates == candidates


def test_inactive_adaptation_values_are_sanitized(tmp_path):
    base = smoke_config(tmp_path)
    unsafe = (
        "0.001,garbage",
        float("inf"),
        float("nan"),
        ["0.001", "0.002"],
        {"candidates": 1},
        True,
        None,
        "0.001," * 100000,
    )
    for value in unsafe:
        config = normalize_config({**base, "adaptation": "off", "adaptation_lr_candidates": value})
        assert config["adaptation"] == "off"
        assert config["adaptation_lr_candidates"] == "0.0003,0.001,0.003"
        # Unrelated settings are never touched by sanitizing inactive fields.
        assert config["learning_rate"] == 0.001
        json.dumps(config, allow_nan=False)
    for value in (0, 10001, "abc", None, float("inf"), True):
        config = normalize_config({**base, "adaptation": "off", "adaptation_cadence": value})
        assert config["adaptation_cadence"] == 1
    # A valid inactive list is preserved exactly rather than reset.
    preserved = normalize_config(
        {
            **base,
            "adaptation": "off",
            "adaptation_lr_candidates": "0.00012345678912,0.00012345678913,0.001",
        }
    )
    assert preserved["adaptation_lr_candidates"] == "0.00012345678912,0.00012345678913,0.001"


def test_sanitize_inactive_adaptation_bounds_every_field():
    sanitized = sanitize_inactive_adaptation(
        {
            "adaptation": "off",
            "adaptation_lr_candidates": float("inf"),
            "adaptation_cadence": 10**9,
            "adaptation_warmup": -5,
            "adaptation_min_dwell": 0,
            "adaptation_lag": 99,
            "adaptation_min_trials": None,
        }
    )
    assert sanitized == {
        "adaptation": "off",
        "adaptation_lr_candidates": "0.0003,0.001,0.003",
        "adaptation_cadence": 1,
        "adaptation_warmup": 2,
        "adaptation_min_dwell": 1,
        "adaptation_lag": 1,
        "adaptation_min_trials": 1,
    }


def test_reenabling_after_inactive_sanitization_validates_strictly(tmp_path):
    config = normalize_config(
        {**smoke_config(tmp_path), "adaptation": "off", "adaptation_lr_candidates": "0.001,garbage"}
    )
    assert config["adaptation_lr_candidates"] == "0.0003,0.001,0.003"
    # The sanitized defaults can be enabled; an unsupported mode is rejected
    # instead of being silently treated as inactive.
    enabled = normalize_config({**config, "adaptation": "hypercontroller", "evaluation_games": 4})
    assert enabled["adaptation"] == "hypercontroller"
    with pytest.raises(ValueError, match="adaptation must be off or hypercontroller"):
        normalize_config({**config, "adaptation": None})


# ── Observation signal ───────────────────────────────────────────────────────


def test_observation_excludes_unfinished_games_and_counts_draws_half():
    evaluation = {
        "opponent": "shield_bearer",
        "opponent_identity": "shield_bearer:abc",
        "iteration": 3,
        "games": [
            {"winner": "attackers", "candidate_side": "attackers"},
            {"winner": None, "candidate_side": "defenders"},
            {"winner": "draw", "candidate_side": "attackers"},
            {"winner": "defenders", "candidate_side": "attackers"},
        ],
    }
    score, provenance = observation_from_evaluation(evaluation)
    assert score == pytest.approx((1 + 0.5) / 3)
    assert provenance["truncated_games"] == 1
    assert provenance["completed_games"] == 3


def test_observation_is_missing_when_every_game_is_unfinished():
    score, provenance = observation_from_evaluation(
        {"games": [{"winner": None, "candidate_side": "attackers"}]}
    )
    assert score is None
    assert provenance["completed_games"] == 0
    assert observation_from_evaluation(None) == (None, {})


# ── Controller timing and safety ─────────────────────────────────────────────


def test_warmup_cadence_and_dwell_gate_decisions():
    # Warm-up: no decision before the configured number of observations.
    warm = make_controller(warmup=3, cadence=1, min_dwell=1, min_trials=1)
    assert [warm.observe(iteration=i + 1, score=0.5) for i in range(2)][-1]["skipped"] == "warmup"
    assert warm.observe(iteration=3, score=0.5)["skipped"] is None

    # Cadence: a decision is made once every two observations.
    paced = make_controller(warmup=0, cadence=2, min_dwell=1, min_trials=1)
    assert paced.observe(iteration=1, score=0.5)["skipped"] == "cadence"
    assert paced.observe(iteration=2, score=0.5)["skipped"] is None
    assert paced.observe(iteration=3, score=0.5)["skipped"] == "cadence"
    assert paced.observe(iteration=4, score=0.5)["skipped"] is None

    # Dwell: after a change, the next decision is held until the dwell elapses.
    dwell = make_controller(warmup=0, cadence=1, min_dwell=3, min_trials=1)
    assert dwell.observe(iteration=1, score=0.0)["skipped"] == "dwell"
    assert dwell.observe(iteration=2, score=0.0)["skipped"] == "dwell"
    changed = dwell.observe(iteration=3, score=0.0)
    assert changed["skipped"] is None
    assert dwell.observe(iteration=4, score=0.0)["skipped"] == "dwell"


def test_missing_and_nonfinite_observations_never_advance_counters():
    controller = make_controller(warmup=1, min_trials=1)
    for bad in (None, float("nan"), float("inf")):
        event = controller.observe(iteration=1, score=bad)
        assert event["skipped"] == "missing_observation"
        assert controller.observations == 0
    assert controller.observe(iteration=2, score=0.5)["skipped"] is None
    assert controller.observations == 1


def test_delayed_observation_is_credited_to_the_active_action():
    controller = make_controller(warmup=0, cadence=1, min_dwell=1, min_trials=1)
    controller.observe(iteration=1, score=0.0)  # action = initial index 1
    # The credited action is the index active for the interval just scored.
    assert controller.actions[-1] == 1
    if controller.effective_index != 1:
        controller.observe(iteration=2, score=0.0)
        assert controller.actions[-1] == controller.effective_index


def test_state_growth_is_bounded_and_serialization_is_finite():
    controller = make_controller(lag=2, min_trials=1, cadence=1)
    for step in range(400):
        controller.observe(iteration=step, score=(step % 5) / 4)
    state = controller.state_dict()
    assert len(controller.contexts) <= MAX_CONTEXTS
    assert len(controller.events) <= MAX_EVENTS
    # Everything stored is JSON-finite: no NaN or infinity can reach the checkpoint.
    for entry in state["contexts"].values():
        for row in entry["V"]:
            assert all(math.isfinite(value) for value in row)
        assert all(math.isfinite(value) for value in entry["b"])
    assert 0 <= state["effective_index"] < len(state["candidates"])


def test_controller_survives_degenerate_rewards():
    controller = make_controller(lag=1, min_trials=1)
    for step in range(20):
        controller.observe(iteration=step, score=0.0)
    # Zero regressors keep the solve well conditioned through the ridge term.
    assert math.isfinite(controller.effective_learning_rate)
    restored = HyperController.from_state(
        controller.state_dict(),
        candidates=controller.candidates,
        lag=controller.lag,
        cadence=controller.cadence,
        warmup=controller.warmup,
        min_dwell=controller.min_dwell,
        min_trials=controller.min_trials,
        initial_index=1,
        seed=7,
        evaluation_identity=controller.evaluation_identity,
    )
    assert restored.reset_reason is None
    assert restored.effective_index == controller.effective_index
    assert restored.observations == controller.observations


# ── Serialization, reset and resume ──────────────────────────────────────────


def test_from_state_resets_when_the_learned_state_is_incomparable():
    controller = make_controller()
    for step in range(3):
        controller.observe(iteration=step, score=0.5)
    state = controller.state_dict()

    changed_candidates = HyperController.from_state(
        state,
        candidates=[0.0001, 0.0002, 0.0003],
        lag=1,
        cadence=1,
        warmup=1,
        min_dwell=1,
        min_trials=1,
        initial_index=1,
        seed=7,
        evaluation_identity="shield_bearer:test",
    )
    assert changed_candidates.reset_reason == "candidates_changed"
    assert changed_candidates.observations == 0

    changed_evaluation = HyperController.from_state(
        state,
        candidates=[0.0003, 0.001, 0.003],
        lag=1,
        cadence=1,
        warmup=1,
        min_dwell=1,
        min_trials=1,
        initial_index=1,
        seed=7,
        evaluation_identity="jarl:other",
    )
    assert changed_evaluation.reset_reason == "evaluation_changed"

    unreadable = HyperController.from_state(
        {"version": 999},
        candidates=[0.0003, 0.001, 0.003],
        lag=1,
        cadence=1,
        warmup=1,
        min_dwell=1,
        min_trials=1,
        initial_index=1,
        seed=7,
        evaluation_identity="shield_bearer:test",
    )
    assert unreadable.reset_reason == "unsupported_state"


def test_resolve_controller_is_none_when_off_and_restores_when_on(tmp_path):
    config = normalize_config(smoke_config(tmp_path, adaptation="off"))
    assert (
        resolve_controller(config, saved_state=None, evaluation_identity="x", seed=1, resume=False)
        is None
    )
    enabled = normalize_config(adapted_config(tmp_path))
    controller = resolve_controller(
        enabled, saved_state=None, evaluation_identity="x", seed=1, resume=False
    )
    assert controller is not None
    assert controller.effective_learning_rate == 0.001


# ── Optimizer state ──────────────────────────────────────────────────────────


def test_learning_rate_update_preserves_optimizer_moments():
    from hnefatafl.models import PolicyValueNet

    model = PolicyValueNet("brandubh", channels=4, blocks=0)
    optimizer = torch.optim.AdamW(model.parameters(), lr=0.001)
    loss = sum(parameter.sum() for parameter in model.parameters())
    loss.backward()
    optimizer.step()
    before = {
        id(parameter): (
            optimizer.state[parameter]["exp_avg"].clone(),
            optimizer.state[parameter]["exp_avg_sq"].clone(),
        )
        for group in optimizer.param_groups
        for parameter in group["params"]
    }
    _set_optimizer_learning_rate(optimizer, 0.003)
    assert optimizer.param_groups[0]["lr"] == 0.003
    for group in optimizer.param_groups:
        for parameter in group["params"]:
            average, squared = before[id(parameter)]
            assert torch.equal(optimizer.state[parameter]["exp_avg"], average)
            assert torch.equal(optimizer.state[parameter]["exp_avg_sq"], squared)


# ── End-to-end training and resume ───────────────────────────────────────────


def test_training_records_effective_rate_and_state(tmp_path):
    torch.set_num_threads(1)
    torch.manual_seed(0)
    result = train(adapted_config(tmp_path))
    assert result["status"] == "completed"
    adaptation = result["adaptation"]
    assert adaptation["algorithm"] == "hypercontroller"
    assert adaptation["observations"] == 4
    assert adaptation["effective_learning_rate"] in (0.0003, 0.001, 0.003)
    _, payload = load_checkpoint(result["checkpoint"])
    state = payload["extra"]["adaptation_state"]
    assert state["observations"] == 4
    assert all(
        row["learning_rate"] in (0.0003, 0.001, 0.003) for row in payload["extra"]["history"]
    )
    # The controller's decisions are recorded compactly for monitoring.
    assert result["adaptation_events"]
    assert "observation" in result["adaptation_events"][-1]


def test_resume_continues_the_controller_and_preserves_effective_rate(tmp_path):
    torch.set_num_threads(1)
    torch.manual_seed(0)
    first = train(adapted_config(tmp_path / "first", iterations=2))
    _, first_payload = load_checkpoint(first["checkpoint"])
    first_state = first_payload["extra"]["adaptation_state"]
    resumed = train(
        resumed_adapted_config(
            tmp_path / "resumed",
            iterations=4,
            resume=first["checkpoint"],
        )
    )
    assert resumed["status"] == "completed"
    _, payload = load_checkpoint(resumed["checkpoint"])
    state = payload["extra"]["adaptation_state"]
    # Continuity: the observation counter and effective rate carry over instead of
    # restarting, and the saved evaluation identity is retained.
    assert state["observations"] == 4
    assert state["effective_index"] == first_state["effective_index"]
    assert state["evaluation_identity"] == first_state["evaluation_identity"]


def _complete_match() -> dict:
    """A deterministic two-game evaluation with no truncated games."""
    return {
        "score": 0.75,
        "wins": 1,
        "losses": 0,
        "draws": 1,
        "truncated": 0,
        "games": [
            {"candidate_side": "attackers", "winner": "attackers", "score": 1.0},
            {"candidate_side": "defenders", "winner": "draw", "score": 0.5},
        ],
    }


def test_champion_evaluation_runs_without_a_promotion(tmp_path, monkeypatch):
    from hnefatafl import training as training_module
    from hnefatafl import training_evaluation as evaluation_module

    original = evaluation_module.promotion_decision
    evaluations: list[str] = []

    def counted(model, config, **kwargs):
        evaluations.append(config["evaluation_opponent"])
        return _complete_match()

    monkeypatch.setattr(training_module, "evaluate_model", counted)
    monkeypatch.setattr(
        evaluation_module,
        "promotion_decision",
        lambda evaluation, config: {**original(evaluation, config), "promoted": False},
    )
    torch.set_num_threads(1)
    torch.manual_seed(0)
    result = train(
        adapted_config(
            tmp_path,
            iterations=4,
            evaluation_games=4,
            evaluation_frequency=1,
            evaluation_opponent="champion",
            champion_min_pairs=2,
        )
    )
    assert result["status"] == "completed"
    _, payload = load_checkpoint(result["checkpoint"])
    state = payload["extra"]["adaptation_state"]
    # A Champion evaluation series is a supported controller objective, and the
    # controller adds no evaluation of its own: one per configured frequency.
    assert state["observations"] == 4
    assert evaluations == ["champion"] * 4
    assert state["promotion_resets"] == 0
    assert state["last_promotion"] is None
    assert state["evaluation_identity"].startswith("champion:")
    # The provisional incumbent is tracked explicitly, so a resume can verify
    # the controller's observations belong to the same Champion.
    assert state["active_champion_identity"].startswith("champion-0-provisional-")
    assert (
        payload["extra"]["evaluation_state"]["champion"]["identity"]
        == state["active_champion_identity"]
    )


def test_champion_promotion_resets_controller_and_survives_resume(tmp_path, monkeypatch):
    from hnefatafl import training as training_module
    from hnefatafl import training_evaluation as evaluation_module

    original = evaluation_module.promotion_decision

    def promote_at_two(evaluation, config):
        decision = original(evaluation, config)
        if evaluation.get("iteration") == 2:
            return {**decision, "promoted": True, "reason": "improvement"}
        return {**decision, "promoted": False}

    monkeypatch.setattr(
        training_module, "evaluate_model", lambda model, config, **kwargs: _complete_match()
    )
    monkeypatch.setattr(evaluation_module, "promotion_decision", promote_at_two)
    torch.set_num_threads(1)
    torch.manual_seed(0)
    options = {
        "iterations": 4,
        "evaluation_games": 4,
        "evaluation_frequency": 1,
        "evaluation_opponent": "champion",
        "champion_min_pairs": 2,
    }
    first = train(adapted_config(tmp_path / "first", **options))
    assert first["status"] == "completed"
    _, payload = load_checkpoint(first["checkpoint"])
    state = payload["extra"]["adaptation_state"]
    # Exactly one reset for the one promotion, and the new regime restarted.
    assert state["version"] == 4
    assert state["promotion_resets"] == 1
    assert state["last_promotion"]["iteration"] == 2
    assert state["last_promotion"]["champion_iteration"] == 0
    assert state["last_promotion"]["preserved_learning_rate"] in (0.0003, 0.001, 0.003)
    assert state["observations"] == 2
    assert state["reset_reason"] == "opponent_promoted"
    # The promotion reset is visible in the bounded event history and telemetry,
    # and is distinguishable from a config or invalid-state reset.
    assert any(event.get("promotion_reset") for event in state["events"])
    assert first["adaptation"]["promotion_resets"] == 1
    assert first["adaptation"]["last_promotion"]["iteration"] == 2
    # Resume preserves the post-promotion regime instead of resetting again.
    resumed = train(
        resumed_adapted_config(
            tmp_path / "resumed", resume=first["checkpoint"], **{**options, "iterations": 6}
        )
    )
    assert resumed["status"] == "completed"
    _, resumed_payload = load_checkpoint(resumed["checkpoint"])
    resumed_state = resumed_payload["extra"]["adaptation_state"]
    assert resumed_state["promotion_resets"] == 1
    # ``reset_reason`` is transient (it names the reset that happened during this
    # load); the durable promotion telemetry is the counter and the record.
    assert resumed_state["reset_reason"] is None
    assert resumed_state["last_promotion"] == state["last_promotion"]
    assert resumed_state["observations"] == state["observations"] + 2


def test_close_candidates_survive_checkpoint_and_resume_without_drift(tmp_path):
    torch.set_num_threads(1)
    torch.manual_seed(0)
    candidates = "0.00012345678912,0.00012345678913,0.001"
    first = train(
        adapted_config(
            tmp_path / "first",
            iterations=2,
            adaptation_lr_candidates=candidates,
            learning_rate=0.00012345678913,
        )
    )
    _, first_payload = load_checkpoint(first["checkpoint"])
    saved = first_payload["metadata"]["training_config"]
    exact = [0.00012345678912, 0.00012345678913, 0.001]
    assert parse_candidates(saved["adaptation_lr_candidates"]) == exact
    assert saved["learning_rate"] in exact
    state = first_payload["extra"]["adaptation_state"]
    assert state["candidates"] == exact
    resumed = train(
        resumed_adapted_config(
            tmp_path / "resumed",
            iterations=4,
            resume=first["checkpoint"],
            adaptation_lr_candidates=candidates,
        )
    )
    resumed_state = load_checkpoint(resumed["checkpoint"])[1]["extra"]["adaptation_state"]
    # No candidate drift: the controller resumed its learned state rather than
    # resetting as if the candidate set had changed.
    assert resumed_state["candidates"] == exact
    assert resumed_state["reset_reason"] is None
    assert resumed_state["observations"] == state["observations"] + 2


def test_controller_is_deterministic_across_split_and_resume(tmp_path):
    torch.set_num_threads(1)
    torch.manual_seed(0)
    full = train(adapted_config(tmp_path / "full", iterations=4))
    torch.manual_seed(0)
    first = train(adapted_config(tmp_path / "first", iterations=2))
    resumed = train(
        resumed_adapted_config(tmp_path / "resumed", iterations=4, resume=first["checkpoint"])
    )
    full_state = load_checkpoint(full["checkpoint"])[1]["extra"]["adaptation_state"]
    resumed_state = load_checkpoint(resumed["checkpoint"])[1]["extra"]["adaptation_state"]
    # The controller's dedicated RNG stream is checkpointed, so a split run makes
    # the same decisions as the uninterrupted run.
    assert resumed_state["rewards"] == full_state["rewards"]
    assert resumed_state["actions"] == full_state["actions"]
    assert resumed_state["effective_index"] == full_state["effective_index"]
    assert resumed_state["counts"] == full_state["counts"]


def test_resume_can_disable_adaptation_and_drops_its_state(tmp_path):
    torch.set_num_threads(1)
    torch.manual_seed(0)
    first = train(adapted_config(tmp_path / "first", iterations=2))
    resumed = train(
        smoke_config(
            tmp_path / "resumed",
            iterations=3,
            resume=first["checkpoint"],
            adaptation="off",
        )
    )
    _, payload = load_checkpoint(resumed["checkpoint"])
    assert "adaptation_state" not in payload["extra"]


def test_enabling_adaptation_on_an_old_checkpoint_starts_fresh(tmp_path):
    torch.set_num_threads(1)
    torch.manual_seed(0)
    plain = train(smoke_config(tmp_path / "plain", iterations=2))
    _, payload = load_checkpoint(plain["checkpoint"])
    assert "adaptation_state" not in payload["extra"]
    resumed = train(
        adapted_config(
            tmp_path / "resumed",
            iterations=4,
            resume=plain["checkpoint"],
            evaluation_games=4,
        )
    )
    state = load_checkpoint(resumed["checkpoint"])[1]["extra"]["adaptation_state"]
    # The pre-adaptation iterations are not invented as controller observations.
    assert state["observations"] == 2


def test_partial_resume_inherits_evaluation_settings_before_adaptation(tmp_path):
    torch.set_num_threads(1)
    torch.manual_seed(0)
    # The checkpoint's evaluation settings allow a controller, but the partial
    # resume does not repeat them; they must be inherited before validation.
    plain = train(
        smoke_config(
            tmp_path / "plain",
            iterations=2,
            evaluation_games=4,
            evaluation_frequency=1,
        )
    )
    resumed = train(
        {
            "preset": "smoke",
            "ruleset": "brandubh",
            "device": "cpu",
            "seed": 0,
            "output_dir": str(tmp_path / "resumed"),
            "iterations": 3,
            "resume": plain["checkpoint"],
            "adaptation": "hypercontroller",
            "adaptation_lr_candidates": "0.0003,0.001,0.003",
        }
    )
    assert resumed["status"] == "completed"
    assert resumed["adaptation"]["algorithm"] == "hypercontroller"
    state = load_checkpoint(resumed["checkpoint"])[1]["extra"]["adaptation_state"]
    # Only the resumed iteration is a fresh controller observation.
    assert state["observations"] == 1


def test_partial_resume_with_invalid_effective_settings_is_rejected(tmp_path):
    torch.set_num_threads(1)
    torch.manual_seed(0)
    # The checkpoint itself has only two evaluation games, so enabling adaptation
    # is invalid once the effective configuration is known.
    plain = train(smoke_config(tmp_path / "plain", iterations=1, evaluation_games=2))
    with pytest.raises(ValueError, match="at least 4"):
        train(
            {
                "preset": "smoke",
                "ruleset": "brandubh",
                "device": "cpu",
                "seed": 0,
                "output_dir": str(tmp_path / "resumed"),
                "iterations": 2,
                "resume": plain["checkpoint"],
                "adaptation": "hypercontroller",
                "adaptation_lr_candidates": "0.0003,0.001,0.003",
            }
        )


# ── Remote-worker compatibility ──────────────────────────────────────────────


def test_remote_worker_without_capability_is_not_offered_adapted_jobs(tmp_path):
    from hnefatafl.remote_training import worker_compatible, worker_incompatibility

    config = normalize_config(adapted_config(tmp_path))
    capable = {"hyper_adaptation": True, "training_symmetry_modes": ["off", "random_d4"]}
    assert not worker_compatible(config, {})
    assert not worker_compatible(config, {"hyper_adaptation": False})
    assert worker_compatible(config, capable)
    # The reason names the missing controller capability instead of blaming the
    # inference backend or device.
    assert worker_incompatibility(config, {}) == "automatic hyperparameter updates"
    assert worker_incompatibility(config, capable) is None
    # An admin version override cannot bypass the capability gate.
    assert not worker_compatible(
        config, {**capable, "hyper_adaptation": False, "version_override": True}
    )


def test_remote_worker_champion_capability_gates_champion_adaptation(tmp_path):
    from hnefatafl.remote_training import worker_compatible, worker_incompatibility

    fixed = normalize_config(adapted_config(tmp_path))
    champion = normalize_config(adapted_config(tmp_path, evaluation_opponent="champion"))
    base = {"hyper_adaptation": True, "training_symmetry_modes": ["off", "random_d4"]}
    champion_capable = {**base, "hyper_adaptation_champion": True}
    # Fixed-opponent adapted jobs stay compatible with older workers, including
    # ones that never advertise the Champion capability.
    assert worker_compatible(fixed, base)
    assert worker_compatible(fixed, champion_capable)
    # Champion adapted jobs require the narrower capability.
    assert not worker_compatible(champion, base)
    assert (
        worker_incompatibility(champion, base)
        == "automatic hyperparameter updates with a Champion opponent"
    )
    assert worker_compatible(champion, champion_capable)
    assert worker_incompatibility(champion, champion_capable) is None
    # A worker with no adaptation support is still rejected for both.
    assert worker_incompatibility(champion, {}) == "automatic hyperparameter updates"
    assert worker_incompatibility(fixed, {}) == "automatic hyperparameter updates"
    # An administrator version override cannot bypass the missing capability.
    assert not worker_compatible(champion, {**base, "version_override": True})


def test_remote_field_set_matches_the_server_and_includes_adaptation():
    from hnefatafl import remote_training, server

    assert remote_training.TRAINING_FIELDS == server.TRAINING_FIELDS
    assert "adaptation" in server.TRAINING_FIELDS
    assert "adaptation_lr_candidates" in server.TRAINING_FIELDS


@pytest.fixture
def client(tmp_path, monkeypatch):
    from auth_support import AuthenticatedClient

    from hnefatafl.server import create_app

    monkeypatch.setattr(
        "hnefatafl.server.application_metadata",
        lambda: {"package_version": "0.26.0", "git_revision": None},
    )
    with AuthenticatedClient(create_app(tmp_path / "server")) as session:
        yield session


def test_api_accepts_adapted_champion_and_rejects_uninformative_games(client):
    # Champion evaluation is a supported objective for the controller: each
    # promotion is signalled from the selection decision, so the API no longer
    # rejects the combination. The remaining informative-games rule still holds.
    champion = client.post(
        "/api/training/jobs",
        json={
            "execution": "remote",
            "preset": "smoke",
            "adaptation": "hypercontroller",
            "evaluation_games": 4,
            "evaluation_opponent": "champion",
        },
    )
    assert champion.status_code == 201, champion.text
    assert champion.json()["config"]["evaluation_opponent"] == "champion"
    assert champion.json()["config"]["adaptation"] == "hypercontroller"
    uninformative = client.post(
        "/api/training/jobs",
        json={
            "execution": "remote",
            "preset": "smoke",
            "adaptation": "hypercontroller",
            "evaluation_games": 2,
            "evaluation_opponent": "shield_bearer",
        },
    )
    assert uninformative.status_code == 400, uninformative.text
    assert "at least 4" in uninformative.json()["detail"]


@pytest.mark.parametrize("mode", [True, False, None, 1, ["off"], {"mode": "off"}, "pbt"])
def test_api_rejects_invalid_adaptation_modes(client, mode):
    response = client.post(
        "/api/training/jobs",
        json={"execution": "remote", "preset": "smoke", "device": "cpu", "adaptation": mode},
    )
    assert response.status_code == 400, response.text
    assert "adaptation must be off or hypercontroller" in response.text
    # No job was started for an invalid mode.
    assert client.get("/api/training/jobs").json() == []


def test_api_rejects_oversized_and_overflowing_candidates(client):
    base = {
        "execution": "remote",
        "preset": "smoke",
        "device": "cpu",
        "adaptation": "hypercontroller",
        "evaluation_games": 4,
    }
    oversized = client.post(
        "/api/training/jobs", json={**base, "adaptation_lr_candidates": "0.001," * 1000}
    )
    assert oversized.status_code == 400, oversized.text
    assert "adaptation_lr_candidates" in oversized.text
    # A JSON integer too large for float() must be a controlled 400, not a 500.
    overflow = client.post(
        "/api/training/jobs", json={**base, "adaptation_lr_candidates": [10**400, 0.001]}
    )
    assert overflow.status_code == 400, overflow.text
    assert "adaptation_lr_candidates" in overflow.text
    # An oversized individual list element is rejected before conversion, and no
    # job is created for the invalid request.
    element = client.post(
        "/api/training/jobs",
        json={
            **base,
            "adaptation_lr_candidates": ["9" * (MAX_CANDIDATES_TEXT + 1), 0.001],
        },
    )
    assert element.status_code == 400, element.text
    assert "adaptation_lr_candidates" in element.text
    assert client.get("/api/training/jobs").json() == []


def test_api_accepts_and_queues_a_valid_adaptation_job(client):
    response = client.post(
        "/api/training/jobs",
        json={
            "execution": "remote",
            "preset": "smoke",
            "device": "cpu",
            "adaptation": "hypercontroller",
            "evaluation_games": 4,
            "learning_rate": 0.001,
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["config"]["adaptation"] == "hypercontroller"


def test_api_sanitizes_inactive_adaptation_values(client, monkeypatch):
    # A JSON body with 1e309 decodes to inf; adaptation off must still accept it
    # and persist a finite, bounded configuration instead of failing later.
    raw = (
        '{"execution":"remote","preset":"smoke","device":"cpu",'
        '"adaptation":"off","adaptation_lr_candidates":1e309,"adaptation_cadence":0}'
    )
    response = client.post(
        "/api/training/jobs", content=raw, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 201, response.text
    remote_config = response.json()["config"]
    assert remote_config["adaptation"] == "off"
    assert remote_config["adaptation_lr_candidates"] == "0.0003,0.001,0.003"
    assert remote_config["adaptation_cadence"] == 1
    json.dumps(remote_config, allow_nan=False)

    class CompletedProcess:
        pid = 0
        returncode = 0

        def poll(self):
            return 0

    monkeypatch.setattr(
        "hnefatafl.server.subprocess.Popen", lambda *args, **kwargs: CompletedProcess()
    )
    local = client.post(
        "/api/training/jobs",
        json={
            "preset": "smoke",
            "device": "cpu",
            "adaptation": "off",
            "adaptation_lr_candidates": ["0.001", "0.002"],
            "adaptation_warmup": None,
        },
    )
    assert local.status_code == 201, local.text
    local_config = local.json()["config"]
    assert local_config["adaptation_lr_candidates"] == "0.0003,0.001,0.003"
    assert local_config["adaptation_warmup"] == 2
    json.dumps(local_config, allow_nan=False)


# ── Per-context exploration and delayed-feedback attribution ─────────────────


def _trained_controller(**changes) -> HyperController:
    controller = make_controller(**changes)
    for step in range(120):
        action = controller.effective_index
        # Candidate 2 is the genuinely better choice; the others look promising
        # until every context has actually been observed.
        controller.observe(iteration=step, score=1.0 if action == 2 else 0.5)
    return controller


def test_exploration_covers_every_candidate_in_every_context():
    controller = _trained_controller(lag=1, min_trials=1)
    # With lag 1 there are candidates^2 = 9 reachable (action, context) pairs;
    # per-context coverage must visit all of them, unlike a global count.
    assert len(controller.contexts) == 9
    assert controller.coverage_pulls >= 9
    # Once coverage is complete the genuinely better candidate is exploited.
    assert controller.effective_index == 2
    assert set(controller.actions[-10:]) == {2}


def test_new_context_after_earlier_coverage_is_still_explored():
    controller = make_controller(lag=1, min_trials=1, cadence=1, warmup=0)
    for step in range(120):
        action = controller.effective_index
        controller.observe(iteration=step, score=1.0 if action == 2 else 0.5)
    # Every context, including the ones first reached late, was explored.
    for key, entry in controller.contexts.items():
        assert entry["n"] >= 1, key
    assert len(controller.contexts) == 9


def test_history_insufficiency_does_not_satisfy_coverage():
    controller = make_controller(lag=2, min_trials=1, cadence=1, warmup=0)
    controller.observe(iteration=1, score=0.5)
    controller.observe(iteration=2, score=0.5)
    # Two observations cannot form a two-step window, so nothing was learned and
    # no context exists yet; coverage cannot be satisfied by those pulls.
    assert controller.observations == 2
    assert controller.learned == 0
    assert controller.contexts == {}
    controller.observe(iteration=3, score=0.5)
    assert controller.learned == 1
    assert len(controller.contexts) == 1


def test_missing_observations_do_not_advance_coverage_or_learning():
    controller = make_controller(lag=1, min_trials=1)
    for bad in (None, float("nan"), float("inf"), float("-inf")):
        event = controller.observe(iteration=1, score=bad)
        assert event["skipped"] == "missing_observation"
    assert controller.observations == 0
    assert controller.learned == 0
    assert controller.contexts == {}
    assert controller.coverage_pulls == 0


def test_delayed_feedback_is_credited_to_the_active_rate():
    controller = make_controller(lag=1, min_trials=1, cadence=1, warmup=0)
    credited = []
    for step in range(30):
        action = controller.effective_index
        credited.append(action)
        event = controller.observe(iteration=step, score=0.5)
        # The observation is attributed to the rate that produced it, never to
        # the rate the same call just selected.
        assert event["attributed_learning_rate"] == controller.candidates[action]
    # The recorded action history matches the rates that were active per interval.
    assert controller.actions == credited


def test_round_trip_preserves_coverage_learning_and_attribution():
    controller = _trained_controller(lag=1, min_trials=1)
    state = controller.state_dict()
    restored = HyperController.from_state(
        state,
        candidates=controller.candidates,
        lag=controller.lag,
        cadence=controller.cadence,
        warmup=controller.warmup,
        min_dwell=controller.min_dwell,
        min_trials=controller.min_trials,
        initial_index=1,
        seed=7,
        evaluation_identity=controller.evaluation_identity,
    )
    assert restored.reset_reason is None
    assert restored.learned == controller.learned
    assert restored.coverage_pulls == controller.coverage_pulls
    assert restored.actions == controller.actions
    assert restored.contexts == controller.contexts
    # The next decision, driven by the restored RNG, matches the original.
    before = controller.observe(iteration=500, score=0.5)
    after = restored.observe(iteration=500, score=0.5)
    assert before["selected_learning_rate"] == after["selected_learning_rate"]
    assert before["changed"] == after["changed"]


# ── Champion promotion boundaries ────────────────────────────────────────────


def promotion(
    iteration: int,
    *,
    champion_iteration: int = 0,
    identity: str | None = None,
    active_identity: str | None = None,
):
    """The selection signal a Champion promotion hands to the controller."""
    return {
        "iteration": iteration,
        "champion_iteration": champion_iteration,
        "opponent_identity": identity or f"champion:test:incumbent-{champion_iteration}",
        "active_champion_identity": active_identity
        if active_identity is not None
        else f"champion-{iteration}-promoted-head_to_head",
    }


def test_champion_promotion_preserves_rate_resets_state_and_restarts_warmup():
    controller = make_controller(warmup=2, cadence=1, min_dwell=1, min_trials=1)
    controller.observe(iteration=1, score=0.5)
    controller.observe(iteration=2, score=0.6)
    rate = controller.effective_learning_rate
    assert controller.observations == 2
    event = controller.observe(iteration=3, score=0.7, promotion=promotion(3))
    # The promoting observation belongs to the outgoing regime and never changes
    # the rate; the reset preserves the rate that trained the promoting interval.
    assert event["skipped"] == "promotion_reset"
    assert event["promotion_reset"] is True
    assert event["changed"] is False
    assert event["preserved_learning_rate"] == rate
    assert event["attributed_learning_rate"] == rate
    assert controller.effective_learning_rate == rate
    assert controller.candidates[controller.effective_index] == rate
    # Opponent-specific learning is discarded so the new Champion starts clean.
    assert controller.contexts == {}
    assert controller.rewards == []
    assert controller.actions == []
    assert controller.counts == [0, 0, 0]
    assert controller.observations == 0
    assert controller.learned == 0
    assert controller.decisions == 0
    assert controller.promotion_resets == 1
    assert controller.reset_reason == "opponent_promoted"
    assert controller.last_promotion == {
        "iteration": 3,
        "champion_iteration": 0,
        "opponent_identity": "champion:test:incumbent-0",
        "preserved_learning_rate": rate,
    }
    # Warm-up restarts against the new Champion instead of deciding immediately.
    assert controller.observe(iteration=4, score=0.9)["skipped"] == "warmup"


def test_new_regime_ignores_observations_from_the_previous_champion():
    controller = make_controller(warmup=0, cadence=1, min_dwell=1, min_trials=1)
    for step in range(6):
        controller.observe(iteration=step, score=0.9)
    assert controller.contexts  # Learned predictors exist for the old Champion.
    controller.observe(iteration=6, score=0.9, promotion=promotion(6, champion_iteration=5))
    assert controller.contexts == {} and controller.rewards == []
    controller.observe(iteration=7, score=0.4)
    assert controller.observations == 1
    assert controller.rewards == [0.4]
    assert len(controller.actions) == 1


def test_observations_without_promotion_never_reset_the_regime():
    controller = make_controller(warmup=1)
    for step in range(3):
        controller.observe(iteration=step, score=0.5)
    controller.observe(iteration=3, score=None)
    assert controller.promotion_resets == 0
    assert controller.last_promotion is None
    assert controller.reset_reason is None
    assert controller.observations == 3


def test_repeated_promotions_reset_once_per_transition_and_are_idempotent():
    controller = make_controller(warmup=0, cadence=1, min_dwell=1, min_trials=1)
    controller.observe(iteration=1, score=0.5)
    controller.observe(iteration=2, score=0.6, promotion=promotion(2, champion_iteration=0))
    controller.observe(iteration=3, score=0.6)
    controller.observe(iteration=4, score=0.7, promotion=promotion(4, champion_iteration=2))
    assert controller.promotion_resets == 2
    assert controller.last_promotion["iteration"] == 4
    assert controller.last_promotion["champion_iteration"] == 2
    # Replaying the same promotion signal never resets the new regime twice.
    controller.observe(iteration=4, score=0.7, promotion=promotion(4, champion_iteration=2))
    assert controller.promotion_resets == 2


def test_replayed_promotion_is_inert_and_does_not_contaminate_the_new_regime():
    controller = make_controller(warmup=0, cadence=1, min_dwell=1, min_trials=1)
    controller.observe(iteration=1, score=0.5)
    controller.observe(iteration=2, score=0.9, promotion=promotion(2, champion_iteration=0))
    # The fresh regime starts empty; capture every piece of its state.
    before = controller.state_dict()
    before_rng = controller.rng.getstate()
    assert before["observations"] == 0
    event = controller.observe(iteration=2, score=0.1, promotion=promotion(2, champion_iteration=0))
    # A replayed promotion is a no-op: the old Champion's score is not ingested,
    # no counter advances, the rate is unchanged, the RNG does not move and no
    # decision event is recorded.
    assert event["skipped"] == "promotion_duplicate"
    assert event["changed"] is False
    assert event["observation"] is None
    assert controller.state_dict() == before
    assert controller.rng.getstate() == before_rng
    assert all(item.get("skipped") != "promotion_duplicate" for item in controller.events)
    assert controller.effective_index == before["effective_index"]
    assert controller.promotion_resets == 1


def test_replayed_promotion_after_restore_is_inert():
    controller = make_controller(warmup=0, cadence=1, min_dwell=1, min_trials=1)
    controller.observe(iteration=1, score=0.5)
    controller.observe(iteration=2, score=0.9, promotion=promotion(2, champion_iteration=0))
    state = controller.state_dict()
    restored = HyperController.from_state(
        state,
        candidates=controller.candidates,
        lag=controller.lag,
        cadence=controller.cadence,
        warmup=controller.warmup,
        min_dwell=controller.min_dwell,
        min_trials=controller.min_trials,
        initial_index=1,
        seed=7,
        evaluation_identity=controller.evaluation_identity,
        active_champion_identity=state["active_champion_identity"],
    )
    before = restored.state_dict()
    before_rng = restored.rng.getstate()
    event = restored.observe(iteration=2, score=0.1, promotion=promotion(2, champion_iteration=0))
    assert event["skipped"] == "promotion_duplicate"
    assert restored.state_dict() == before
    assert restored.rng.getstate() == before_rng
    assert restored.observations == 0


def test_stale_promotion_replay_cannot_reset_a_later_regime():
    controller = make_controller(warmup=0, cadence=1, min_dwell=1, min_trials=1)
    controller.observe(iteration=1, score=0.5)
    # Promotion A -> B, then B -> C.
    controller.observe(iteration=2, score=0.6, promotion=promotion(2, champion_iteration=0))
    controller.observe(iteration=3, score=0.6)
    controller.observe(iteration=4, score=0.7, promotion=promotion(4, champion_iteration=2))
    controller.observe(iteration=5, score=0.5)
    assert controller.active_champion_identity == "champion-4-promoted-head_to_head"
    assert controller.promotion_resets == 2
    before = controller.state_dict()
    before_rng = controller.rng.getstate()
    # Replaying the older A -> B event must not move the controller back to B.
    event = controller.observe(iteration=2, score=0.9, promotion=promotion(2, champion_iteration=0))
    assert event["skipped"] == "promotion_duplicate"
    assert event["changed"] is False
    assert controller.state_dict() == before
    assert controller.rng.getstate() == before_rng
    assert controller.active_champion_identity == "champion-4-promoted-head_to_head"
    assert controller.effective_index == before["effective_index"]
    assert controller.promotion_resets == 2
    # A genuinely later promotion C -> D still works.
    new_event = controller.observe(
        iteration=6, score=0.8, promotion=promotion(6, champion_iteration=4)
    )
    assert new_event["skipped"] == "promotion_reset"
    assert new_event["promotion_reset"] is True
    assert controller.active_champion_identity == "champion-6-promoted-head_to_head"
    assert controller.promotion_resets == 3
    assert controller.observations == 0


def test_stale_promotion_replay_after_restore_cannot_reset_a_later_regime():
    controller = make_controller(warmup=0, cadence=1, min_dwell=1, min_trials=1)
    controller.observe(iteration=1, score=0.5)
    controller.observe(iteration=2, score=0.6, promotion=promotion(2, champion_iteration=0))
    controller.observe(iteration=3, score=0.6)
    controller.observe(iteration=4, score=0.7, promotion=promotion(4, champion_iteration=2))
    state = controller.state_dict()
    restored = HyperController.from_state(
        state,
        candidates=controller.candidates,
        lag=controller.lag,
        cadence=controller.cadence,
        warmup=controller.warmup,
        min_dwell=controller.min_dwell,
        min_trials=controller.min_trials,
        initial_index=1,
        seed=7,
        evaluation_identity=controller.evaluation_identity,
        active_champion_identity=state["active_champion_identity"],
    )
    before = restored.state_dict()
    before_rng = restored.rng.getstate()
    event = restored.observe(iteration=2, score=0.9, promotion=promotion(2, champion_iteration=0))
    assert event["skipped"] == "promotion_duplicate"
    assert restored.state_dict() == before
    assert restored.rng.getstate() == before_rng
    assert restored.active_champion_identity == "champion-4-promoted-head_to_head"
    assert restored.promotion_resets == 2


def test_from_state_resets_learned_state_when_the_active_champion_changes():
    controller = make_controller(
        warmup=0,
        cadence=1,
        min_dwell=1,
        min_trials=1,
        active_champion_identity="champion-5-promoted-head_to_head",
    )
    for step in range(4):
        controller.observe(iteration=step, score=0.5)
    assert controller.contexts
    state = controller.state_dict()
    restored = HyperController.from_state(
        state,
        candidates=controller.candidates,
        lag=controller.lag,
        cadence=controller.cadence,
        warmup=controller.warmup,
        min_dwell=controller.min_dwell,
        min_trials=controller.min_trials,
        initial_index=1,
        seed=7,
        evaluation_identity=controller.evaluation_identity,
        active_champion_identity="champion-9-promoted-head_to_head",
    )
    assert restored.reset_reason == "champion_changed"
    assert restored.contexts == {}
    assert restored.rewards == []
    assert restored.actions == []
    assert restored.observations == 0
    assert restored.learned == 0
    # The effective rate is preserved so the optimizer keeps the checkpoint rate.
    assert restored.effective_index == state["effective_index"]
    assert restored.effective_learning_rate == controller.effective_learning_rate
    assert restored.active_champion_identity == "champion-9-promoted-head_to_head"


def test_from_state_conservatively_resets_legacy_champion_state():
    controller = make_controller(
        warmup=0,
        cadence=1,
        min_dwell=1,
        min_trials=1,
        active_champion_identity="champion-5-promoted-head_to_head",
    )
    for step in range(4):
        controller.observe(iteration=step, score=0.5)
    state = controller.state_dict()
    # A version-3 state predates the active-regime identity, so its learned
    # history cannot be proven to belong to the restored Champion.
    state["version"] = 3
    state.pop("active_champion_identity")
    restored = HyperController.from_state(
        state,
        candidates=controller.candidates,
        lag=controller.lag,
        cadence=controller.cadence,
        warmup=controller.warmup,
        min_dwell=controller.min_dwell,
        min_trials=controller.min_trials,
        initial_index=1,
        seed=7,
        evaluation_identity=controller.evaluation_identity,
        active_champion_identity="champion-5-promoted-head_to_head",
    )
    assert restored.reset_reason == "champion_identity_unknown"
    assert restored.contexts == {}
    assert restored.observations == 0
    assert restored.effective_index == state["effective_index"]
    assert restored.active_champion_identity == "champion-5-promoted-head_to_head"


def test_fixed_opponent_state_still_loads_without_a_champion_identity():
    controller = _trained_controller(lag=1, min_trials=1)
    state = controller.state_dict()
    assert state["active_champion_identity"] is None
    restored = _restore(state)
    assert restored.reset_reason is None
    assert restored.contexts == controller.contexts
    assert restored.observations == controller.observations
    assert restored.active_champion_identity is None


def test_from_state_rejects_malformed_active_champion_identity():
    for value in ("", 5, [], "x" * 5000):
        state = _valid_state()
        state["active_champion_identity"] = value
        restored = _restore(state)
        assert restored.reset_reason == "unreadable_state", value
        assert restored.contexts == {}
        assert restored.active_champion_identity is None


def test_resolve_controller_resets_only_the_controller_on_champion_mismatch(tmp_path):
    config = normalize_config(adapted_config(tmp_path, evaluation_opponent="champion"))
    candidates = [0.0003, 0.001, 0.003]
    original = HyperController(
        candidates=candidates,
        lag=1,
        cadence=1,
        warmup=1,
        min_dwell=1,
        min_trials=1,
        initial_index=2,
        seed=7,
        evaluation_identity="champion:test",
        active_champion_identity="champion-4-promoted-head_to_head",
    )
    original.observe(iteration=1, score=0.5)
    state = original.state_dict()
    # A matching identity restores the learned state and the selected rate.
    matching = resolve_controller(
        config,
        saved_state=state,
        evaluation_identity="champion:test",
        active_champion_identity="champion-4-promoted-head_to_head",
        seed=7,
        resume=True,
    )
    assert matching.reset_reason is None
    assert matching.contexts == original.contexts
    assert matching.effective_index == original.effective_index
    # A different Champion keeps the effective rate but discards the observations;
    # the optimizer and its moments live outside the controller and are untouched.
    mismatched = resolve_controller(
        config,
        saved_state=state,
        evaluation_identity="champion:test",
        active_champion_identity="champion-8-promoted-head_to_head",
        seed=7,
        resume=True,
    )
    assert mismatched.reset_reason == "champion_changed"
    assert mismatched.contexts == {}
    assert mismatched.rewards == []
    assert mismatched.observations == 0
    assert mismatched.effective_index == original.effective_index
    assert mismatched.active_champion_identity == "champion-8-promoted-head_to_head"
    # An explicit learning-rate override still wins over the preserved rate.
    overridden = resolve_controller(
        config,
        saved_state=state,
        evaluation_identity="champion:test",
        active_champion_identity="champion-8-promoted-head_to_head",
        seed=7,
        resume=True,
        override_learning_rate=0.0003,
    )
    assert overridden.reset_reason == "champion_changed"
    assert overridden.effective_learning_rate == 0.0003
    assert overridden.override_learning_rate == 0.0003


def test_promotion_telemetry_round_trips_through_state():
    controller = make_controller(warmup=0, cadence=1, min_dwell=1, min_trials=1)
    controller.observe(iteration=1, score=0.5)
    controller.observe(iteration=2, score=0.6, promotion=promotion(2, champion_iteration=0))
    summary = controller.summary()
    assert summary["promotion_resets"] == 1
    assert summary["last_promotion"]["iteration"] == 2
    assert summary["last_promotion"]["champion_iteration"] == 0
    assert summary["last_event"]["promotion_reset"] is True
    state = controller.state_dict()
    assert state["version"] == 4
    assert state["promotion_resets"] == 1
    assert state["active_champion_identity"] == "champion-2-promoted-head_to_head"
    assert state["last_promotion"]["preserved_learning_rate"] in controller.candidates
    restored = HyperController.from_state(
        state,
        candidates=controller.candidates,
        lag=controller.lag,
        cadence=controller.cadence,
        warmup=controller.warmup,
        min_dwell=controller.min_dwell,
        min_trials=controller.min_trials,
        initial_index=1,
        seed=7,
        evaluation_identity=controller.evaluation_identity,
        active_champion_identity=state["active_champion_identity"],
    )
    assert restored.reset_reason is None
    assert restored.promotion_resets == 1
    assert restored.active_champion_identity == state["active_champion_identity"]
    assert restored.last_promotion == controller.last_promotion
    assert restored.events[-1]["promotion_reset"] is True


def test_version_two_state_without_promotion_fields_restores_cleanly():
    controller = _trained_controller(lag=1, min_trials=1)
    state = controller.state_dict()
    state["version"] = 2
    state.pop("promotion_resets")
    state.pop("last_promotion")
    state["events"] = [
        {key: value for key, value in event.items() if key != "promotion_reset"}
        for event in state["events"]
    ]
    restored = _restore(state)
    assert restored.reset_reason is None
    assert restored.migrated_from == 2
    assert restored.promotion_resets == 0
    assert restored.last_promotion is None
    assert restored.learned == controller.learned


def test_from_state_rejects_a_malformed_promotion_record():
    valid = {
        "iteration": 4,
        "champion_iteration": 2,
        "opponent_identity": "champion:test:incumbent-2",
        "preserved_learning_rate": 0.001,
    }
    for mutate in (
        lambda s: s.update(promotion_resets=1, last_promotion=None),
        lambda s: s.update(promotion_resets=0, last_promotion=dict(valid)),
        lambda s: s.update(promotion_resets=1, last_promotion={**valid, "iteration": -1}),
        lambda s: s.update(
            promotion_resets=1, last_promotion={**valid, "preserved_learning_rate": 0.002}
        ),
        lambda s: s.update(promotion_resets=1, last_promotion={**valid, "unexpected": 1}),
        lambda s: s.update(
            promotion_resets=1, last_promotion={**valid, "opponent_identity": "x" * 5000}
        ),
    ):
        state = _valid_state()
        mutate(state)
        restored = _restore(state)
        assert restored.reset_reason == "unreadable_state"
        # A rejected promotion record resets only the controller and leaks nothing.
        assert restored.promotion_resets == 0
        assert restored.last_promotion is None


# ── Atomic controller-state validation ───────────────────────────────────────


def _valid_state(**changes) -> dict:
    controller = _trained_controller(lag=1, min_trials=1)
    state = controller.state_dict()
    state.update(changes)
    return state


def _restore(state: dict, **changes) -> HyperController:
    options = {
        "candidates": [0.0003, 0.001, 0.003],
        "lag": 1,
        "cadence": 1,
        "warmup": 1,
        "min_dwell": 1,
        "min_trials": 1,
        "initial_index": 1,
        "seed": 7,
        "evaluation_identity": "shield_bearer:test",
    }
    options.update(changes)
    return HyperController.from_state(state, **options)


def test_from_state_rejects_non_finite_numbers():
    for mutate in (
        lambda s: s["contexts"]["0:1"]["V"][0].__setitem__(0, float("nan")),
        lambda s: s["contexts"]["0:1"]["b"].__setitem__(0, float("inf")),
        lambda s: s["rewards"].__setitem__(0, float("-inf")),
    ):
        state = _valid_state()
        mutate(state)
        restored = _restore(state)
        assert restored.reset_reason == "unreadable_state"
        assert restored.contexts == {}


def test_from_state_rejects_excessive_contexts():
    state = _valid_state()
    entry = next(iter(state["contexts"].values()))
    state["contexts"] = {f"0:{index}": dict(entry) for index in range(MAX_CONTEXTS + 1)}
    restored = _restore(state)
    assert restored.reset_reason == "unreadable_state"


def test_from_state_rejects_malformed_context_keys():
    for key in ("not-a-key", "9:0", "0:", "0:a", "0:0,1", "0:0,0,0"):
        state = _valid_state()
        state["contexts"] = {key: next(iter(state["contexts"].values()))}
        restored = _restore(state)
        assert restored.reset_reason == "unreadable_state", key


def test_from_state_rejects_wrong_matrix_dimensions():
    for matrix in ([[1.0, 0.0]], [[1.0], [0.0]], []):
        state = _valid_state()
        state["contexts"]["0:1"]["V"] = matrix
        restored = _restore(state)
        assert restored.reset_reason == "unreadable_state"


def test_from_state_rejects_non_positive_definite_matrices():
    controller = _trained_controller(lag=2, min_trials=1)
    state = controller.state_dict()
    key = next(iter(state["contexts"]))
    # Symmetric with a positive diagonal but an indefinite 2x2 matrix.
    state["contexts"][key]["V"] = [[1.0, 2.0], [2.0, 1.0]]
    restored = _restore(state, lag=2)
    assert restored.reset_reason == "unreadable_state"


def test_from_state_rejects_invalid_counts_and_counters():
    for key, value in (
        ("n", -1),
        ("n", 1.5),
        ("n", True),
    ):
        state = _valid_state()
        state["contexts"]["0:1"][key] = value
        assert _restore(state).reset_reason == "unreadable_state", (key, value)
    for key, value in (
        ("observations", -1),
        ("learned", -5),
        ("decisions", 1.5),
        ("changes", True),
        ("coverage_pulls", -1),
        ("counts", [1, -1, 0]),
        ("effective_index", 9),
        ("effective_index", 1.5),
        ("last_decision_observation", 10_000),
    ):
        state = _valid_state()
        state[key] = value
        assert _restore(state).reset_reason == "unreadable_state", (key, value)


def test_from_state_rejects_incompatible_versions():
    state = _valid_state(version=99)
    assert _restore(state).reset_reason == "unsupported_state"
    assert _restore(_valid_state(algorithm="pbt")).reset_reason == "unsupported_state"
    assert _restore([]).reset_reason is None  # empty state is a fresh controller


def test_version_one_state_is_migrated_in_place():
    state = _valid_state()
    state["version"] = 1
    state.pop("learned")
    state.pop("coverage_pulls")
    restored = _restore(state)
    assert restored.reset_reason is None
    assert restored.migrated_from == 1
    assert restored.contexts == _valid_state()["contexts"]


def test_rejected_state_cannot_leak_into_telemetry_or_a_checkpoint():
    state = _valid_state()
    # The contexts validate but a later field is corrupt: adoption must be atomic.
    state["observations"] = -1
    restored = _restore(state)
    assert restored.reset_reason == "unreadable_state"
    assert restored.contexts == {}
    assert restored.rewards == []
    assert restored.effective_index == 1
    summary = restored.summary()
    assert summary["reset_reason"] == "unreadable_state"
    assert summary["contexts"] == 0
    # The fresh state serializes cleanly, so the malformed state never reaches a
    # later checkpoint.
    assert restored.state_dict()["version"] == 4
    assert restored.state_dict()["contexts"] == {}


# ── Learning-rate reporting ──────────────────────────────────────────────────


def test_history_attributes_each_iteration_to_the_rate_that_trained_it(tmp_path):
    torch.set_num_threads(1)
    torch.manual_seed(0)
    result = train(adapted_config(tmp_path))
    history = load_checkpoint(result["checkpoint"])[1]["extra"]["history"]
    # Iteration i+1 trains with the rate selected at the end of iteration i.
    for previous, point in zip(history, history[1:]):
        assert point["learning_rate"] == previous["effective_learning_rate"]
    assert history[0]["learning_rate"] == 0.001
    # At least one boundary changed the rate, so the two fields are genuinely
    # distinct and the reported loss is not associated with the next rate.
    assert any(point["learning_rate"] != point["effective_learning_rate"] for point in history)
    assert result["adaptation"]["effective_learning_rate"] == history[-1]["effective_learning_rate"]


# ── Bounded decision history ─────────────────────────────────────────────────


def test_training_result_exposes_a_bounded_decision_history(tmp_path):
    torch.set_num_threads(1)
    torch.manual_seed(0)
    result = train(adapted_config(tmp_path))
    events = result["adaptation_events"]
    assert 0 < len(events) <= 50
    last = events[-1]
    assert {"iteration", "observation", "previous_learning_rate", "selected_learning_rate"} <= set(
        last
    )
    assert last["attributed_learning_rate"] == last["previous_learning_rate"]


def test_compact_progress_carries_only_a_small_event_tail():
    from hnefatafl.training import compact_progress

    state = {
        "status": "running",
        "adaptation": {"effective_learning_rate": 0.003},
        "adaptation_events": [{"iteration": index} for index in range(50)],
    }
    compact = compact_progress(state)
    assert compact["adaptation"]["effective_learning_rate"] == 0.003
    assert len(compact["adaptation_events"]) == 5
    assert compact["adaptation_events"][-1]["iteration"] == 49


def test_remote_clean_truncates_an_oversized_event_list():
    from hnefatafl.remote_training import clean

    cleaned = clean({"adaptation_events": [{"iteration": index} for index in range(3000)]})
    assert len(cleaned["adaptation_events"]) == 2000
    assert cleaned["adaptation_events"][-1]["iteration"] == 2999


# ── RNG restoration ──────────────────────────────────────────────────────────


def test_from_state_rejects_malformed_rng():
    for rng in (
        "not-a-state",
        [],
        [3],
        [3, "bad", None],
        [3, [1, 2, 3], None],
        [3, list(range(624)), None],
        ["3", list(range(625)), None],
    ):
        state = _valid_state()
        state["rng"] = rng
        restored = _restore(state)
        assert restored.reset_reason == "unreadable_state", rng
        assert restored.contexts == {}
        assert restored.rewards == []


def test_from_state_rejects_absent_rng():
    state = _valid_state()
    state.pop("rng")
    restored = _restore(state)
    # A missing stream is never silently replaced while keeping learned state.
    assert restored.reset_reason == "rng_absent"
    assert restored.contexts == {}


def test_supported_legacy_state_restores_its_rng():
    controller = _trained_controller(lag=1, min_trials=1)
    state = controller.state_dict()
    state["version"] = 1
    state.pop("learned")
    state.pop("coverage_pulls")
    restored = _restore(state)
    assert restored.reset_reason is None
    assert restored.migrated_from == 1
    # The saved stream is restored, so the next decision matches the original.
    before = controller.observe(iteration=500, score=0.5)
    after = restored.observe(iteration=500, score=0.5)
    assert before["selected_learning_rate"] == after["selected_learning_rate"]


def test_resume_with_malformed_controller_rng_resets_only_the_controller(tmp_path):
    torch.set_num_threads(1)
    torch.manual_seed(0)
    first = train(adapted_config(tmp_path / "first", iterations=2))
    _, payload = load_checkpoint(first["checkpoint"])
    # Keep the learned state valid but corrupt the saved stream.
    payload["extra"]["adaptation_state"]["rng"] = "not-a-rng-state"
    corrupted = tmp_path / "corrupted.pt"
    torch.save(payload, corrupted)
    resumed = train(adapted_config(tmp_path / "resumed", iterations=4, resume=str(corrupted)))
    assert resumed["status"] == "completed"
    assert resumed["adaptation"]["reset_reason"] == "unreadable_state"
    # The malformed state was not adopted: the controller restarted its counters
    # and re-learned only during the two resumed iterations.
    _, after = load_checkpoint(resumed["checkpoint"])
    assert after["optimizer_state"] is not None
    assert after["extra"]["replay"]
    assert after["extra"]["adaptation_state"]["observations"] == 2


# ── Event payload validation ─────────────────────────────────────────────────


def test_from_state_rejects_malformed_event_payloads():
    mutations = [
        lambda events: events[0].__setitem__("observation", float("nan")),
        lambda events: events[0].__setitem__("observation", float("inf")),
        lambda events: events[0].__setitem__("observation", "0.5"),
        lambda events: events[0].__setitem__("iteration", -1),
        lambda events: events[0].__setitem__("changed", "yes"),
        lambda events: events[0].__setitem__("selected_learning_rate", 0.999),
        lambda events: events[0].__setitem__("skipped", "made-up"),
        lambda events: events[0].__setitem__("unexpected", {"nested": {"deep": [1, 2, 3]}}),
        lambda events: events[0].__setitem__("provenance", {"unexpected": 1}),
        lambda events: events[0]["provenance"].__setitem__("score", 2.0),
        lambda events: events[0]["provenance"].__setitem__("wins", -1),
        lambda events: events[0]["provenance"].__setitem__("opponent", "x" * 500),
    ]
    for mutate in mutations:
        state = _valid_state()
        mutate(state["events"])
        restored = _restore(state)
        assert restored.reset_reason == "unreadable_state"


def test_from_state_accepts_valid_historical_events():
    state = _valid_state()
    # A v1-style event (previous/effective index and rate) alongside a v2 event.
    state["events"] = [
        {
            "iteration": 3,
            "observation": 0.5,
            "provenance": {"opponent": "shield_bearer", "completed_games": 4, "wins": 2},
            "changed": True,
            "skipped": None,
            "previous_index": 1,
            "effective_index": 2,
            "effective_learning_rate": 0.003,
            "observations": 3,
            "seconds": 0.0001,
        },
        {
            "iteration": 4,
            "observation": None,
            "provenance": {},
            "attributed_learning_rate": 0.003,
            "previous_learning_rate": 0.003,
            "selected_learning_rate": 0.003,
            "selected_index": 2,
            "changed": False,
            "skipped": "missing_observation",
            "observations": 4,
            "seconds": 0.0002,
        },
    ]
    restored = _restore(state)
    assert restored.reset_reason is None
    assert len(restored.events) == 2
    # Accepted telemetry is JSON-serializable, so it can be published and stored.
    json.dumps(restored.state_dict(), allow_nan=False)
    json.dumps(restored.summary(), allow_nan=False)


def test_from_state_rejects_oversized_event_history():
    state = _valid_state()
    state["events"] = [dict(state["events"][0]) for _ in range(MAX_EVENTS + 1)]
    assert _restore(state).reset_reason == "unreadable_state"


# ── Resume learning-rate semantics ───────────────────────────────────────────


def test_resume_preserves_the_selected_rate_without_an_override(tmp_path):
    torch.set_num_threads(1)
    torch.manual_seed(0)
    first = train(adapted_config(tmp_path / "first", iterations=2))
    first_state = load_checkpoint(first["checkpoint"])[1]["extra"]["adaptation_state"]
    resumed = train(
        resumed_adapted_config(tmp_path / "resumed", iterations=4, resume=first["checkpoint"])
    )
    history = load_checkpoint(resumed["checkpoint"])[1]["extra"]["history"]
    # The first resumed iteration trains with the rate the checkpoint selected.
    assert history[2]["learning_rate"] == first_state["candidates"][first_state["effective_index"]]
    assert resumed["adaptation"]["override_learning_rate"] is None


def test_resume_honours_an_explicit_learning_rate_override(tmp_path):
    torch.set_num_threads(1)
    torch.manual_seed(0)
    # Warm-up is effectively disabled so the first phase keeps the starting rate,
    # making the override unambiguous.
    first = train(adapted_config(tmp_path / "first", iterations=2, adaptation_warmup=1000))
    resumed = train(
        adapted_config(
            tmp_path / "resumed",
            iterations=4,
            adaptation_warmup=1000,
            resume=first["checkpoint"],
            learning_rate=0.003,
        )
    )
    adaptation = resumed["adaptation"]
    assert adaptation["effective_learning_rate"] == 0.003
    assert adaptation["override_learning_rate"] == 0.003
    state = load_checkpoint(resumed["checkpoint"])[1]["extra"]["adaptation_state"]
    # The override moved the effective rate; the learned state was preserved.
    assert state["observations"] == 4
    assert state["contexts"]
