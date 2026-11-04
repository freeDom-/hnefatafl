"""Optional online learning-rate adaptation (HyperController).

These tests cover the controller in isolation (timing, bounds, serialization and
numerical behaviour) and one end-to-end training path that proves the controller
runs inside the real training loop, survives a checkpoint round-trip and never
rebuilds the optimizer. The algorithm's honest signal is the completed-game score
against one fixed opponent; see docs/designs/online-hyperparameter-adaptation.md.
"""

from __future__ import annotations

import math

import pytest
import torch

from hnefatafl.hyper_adaptation import (
    MAX_CONTEXTS,
    MAX_EVENTS,
    HyperController,
    observation_from_evaluation,
    parse_candidates,
    resolve_controller,
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


def test_adaptation_requires_fixed_opponent_and_informative_games(tmp_path):
    with pytest.raises(ValueError, match="fixed evaluation opponent"):
        normalize_config(adapted_config(tmp_path, evaluation_opponent="champion"))
    with pytest.raises(ValueError, match="at least 4"):
        normalize_config(adapted_config(tmp_path, evaluation_games=2))


def test_adaptation_requires_the_configured_rate_to_be_a_candidate(tmp_path):
    with pytest.raises(ValueError, match="one of the adaptation_lr_candidates"):
        normalize_config(adapted_config(tmp_path, learning_rate=0.002))


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
    warm = make_controller(warmup=3, cadence=1, min_dwell=1, min_trials=0)
    assert [warm.observe(iteration=i + 1, score=0.5) for i in range(2)][-1]["skipped"] == "warmup"
    assert warm.observe(iteration=3, score=0.5)["skipped"] is None

    # Cadence: a decision is made once every two observations.
    paced = make_controller(warmup=0, cadence=2, min_dwell=1, min_trials=0)
    assert paced.observe(iteration=1, score=0.5)["skipped"] == "cadence"
    assert paced.observe(iteration=2, score=0.5)["skipped"] is None
    assert paced.observe(iteration=3, score=0.5)["skipped"] == "cadence"
    assert paced.observe(iteration=4, score=0.5)["skipped"] is None

    # Dwell: after a change, the next decision is held until the dwell elapses.
    dwell = make_controller(warmup=0, cadence=1, min_dwell=3, min_trials=0)
    assert dwell.observe(iteration=1, score=0.0)["skipped"] == "dwell"
    assert dwell.observe(iteration=2, score=0.0)["skipped"] == "dwell"
    changed = dwell.observe(iteration=3, score=0.0)
    assert changed["skipped"] is None
    assert dwell.observe(iteration=4, score=0.0)["skipped"] == "dwell"


def test_missing_and_nonfinite_observations_never_advance_counters():
    controller = make_controller(warmup=1, min_trials=0)
    for bad in (None, float("nan"), float("inf")):
        event = controller.observe(iteration=1, score=bad)
        assert event["skipped"] == "missing_observation"
        assert controller.observations == 0
    assert controller.observe(iteration=2, score=0.5)["skipped"] is None
    assert controller.observations == 1


def test_delayed_observation_is_credited_to_the_active_action():
    controller = make_controller(warmup=0, cadence=1, min_dwell=1, min_trials=0)
    controller.observe(iteration=1, score=0.0)  # action = initial index 1
    # The credited action is the index active for the interval just scored.
    assert controller.actions[-1] == 1
    if controller.effective_index != 1:
        controller.observe(iteration=2, score=0.0)
        assert controller.actions[-1] == controller.effective_index


def test_state_growth_is_bounded_and_serialization_is_finite():
    controller = make_controller(lag=2, min_trials=0, cadence=1)
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
    controller = make_controller(lag=1, min_trials=0)
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
        adapted_config(
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


def test_controller_is_deterministic_across_split_and_resume(tmp_path):
    torch.set_num_threads(1)
    torch.manual_seed(0)
    full = train(adapted_config(tmp_path / "full", iterations=4))
    torch.manual_seed(0)
    first = train(adapted_config(tmp_path / "first", iterations=2))
    resumed = train(adapted_config(tmp_path / "resumed", iterations=4, resume=first["checkpoint"]))
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


# ── Remote-worker compatibility ──────────────────────────────────────────────


def test_remote_worker_without_capability_is_not_offered_adapted_jobs(tmp_path):
    from hnefatafl.remote_training import worker_compatible

    config = normalize_config(adapted_config(tmp_path))
    capable = {"hyper_adaptation": True, "training_symmetry_modes": ["off", "random_d4"]}
    assert not worker_compatible(config, {})
    assert not worker_compatible(config, {"hyper_adaptation": False})
    assert worker_compatible(config, capable)
    # An admin version override cannot bypass the capability gate.
    assert not worker_compatible(
        config, {**capable, "hyper_adaptation": False, "version_override": True}
    )


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


def test_api_rejects_incompatible_adaptation_with_a_named_reason(client):
    response = client.post(
        "/api/training/jobs",
        json={
            "execution": "remote",
            "preset": "smoke",
            "adaptation": "hypercontroller",
            "evaluation_games": 4,
            "evaluation_opponent": "champion",
        },
    )
    assert response.status_code == 400, response.text
    assert "fixed evaluation opponent" in response.json()["detail"]


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
