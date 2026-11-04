"""Deterministic CPU tests for the Optuna tuning workflow.

These tests replace the expensive trainer with a stub that emits progress the
same way the real one does. That isolates the study bookkeeping: seed handling,
the objective policy (best evaluated checkpoint, not the final iteration),
opponent selection, failure budgets, the weighted aggregate and the held-out
verification stage.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hnefatafl.job_archives import ArchiveError
from hnefatafl.job_studies import search_space_config, tuning_config
from hnefatafl.training import tune


def training(**changes) -> dict:
    return {
        "preset": "smoke",
        "ruleset": "brandubh",
        "device": "cpu",
        "cpu_threads": 1,
        "games": 1,
        "iterations": 2,
        "max_game_plies": 2,
        "evaluation_max_plies": 2,
        "evaluation_games": 2,
        **changes,
    }


class StubTrainer:
    """Emit deterministic progress and a best/last checkpoint per trial."""

    def __init__(self, scores, *, fail_on=(), evaluation=None):
        self.scores = scores
        self.fail_on = set(fail_on)
        self.evaluation = evaluation or {}
        self.calls: list[dict] = []

    def __call__(self, config, progress=None, should_stop=None):
        self.calls.append(dict(config))
        trial_number = int(Path(config["output_dir"]).name.split("-")[1])
        if trial_number in self.fail_on:
            raise RuntimeError(f"trial {trial_number} failed on purpose")
        output = Path(config["output_dir"])
        output.mkdir(parents=True, exist_ok=True)
        best = output / "best.pt"
        best.write_bytes(b"stub")
        last = output / "last.pt"
        last.write_bytes(b"stub")
        scores = self.scores(config)
        final = None
        for iteration, score in enumerate(scores, start=1):
            evaluation = {
                "score": score,
                "opponent": config.get("evaluation_opponent", "shield_bearer"),
                "opponent_identity": "stub:identity",
                "wins": int(round(score * 2)),
                "losses": 2 - int(round(score * 2)),
                "draws": 0,
                "truncated": 0,
                **self.evaluation,
            }
            final = evaluation
            if progress is not None:
                progress(
                    {
                        "status": "running",
                        "stage": "iteration_complete",
                        "iteration": iteration,
                        "revision": iteration,
                        "run": f"trial-{trial_number}",
                        "metrics": {"evaluation": evaluation},
                    }
                )
        return {
            "status": "completed",
            "metrics": {"evaluation": final},
            "best_checkpoint": str(best),
            "checkpoint": str(last),
        }


@pytest.fixture
def stub(monkeypatch):
    def install(scores, **kwargs):
        trainer = StubTrainer(scores, **kwargs)
        monkeypatch.setattr("hnefatafl.training.train", trainer)
        return trainer

    return install


def test_objective_is_the_best_checkpoint_not_the_final_iteration(tmp_path, stub):
    stub(lambda config: [0.9, 0.4])
    result = tune(
        {
            "trials": 1,
            "study_name": "best-checkpoint",
            "seed": 5,
            "search_space": {"learning_rate": {"type": "categorical", "choices": [0.001]}},
            "training": training(),
            "output_dir": str(tmp_path / "study"),
        }
    )
    assert result["status"] == "completed"
    assert result["best_score"] == pytest.approx(0.9)
    assert result["best_iteration"] == 1
    assert result["trials"][0]["final_score"] == pytest.approx(0.4)
    assert result["trials"][0]["best_score"] == pytest.approx(0.9)


def test_selectable_evaluation_opponent_reaches_the_trainer(tmp_path, stub):
    trainer = stub(lambda config: [0.6])
    result = tune(
        {
            "trials": 1,
            "study_name": "jarl-objective",
            "seed": 0,
            "search_space": {"exploration": {"type": "float", "low": 1.0, "high": 1.0}},
            "training": training(evaluation_opponent="jarl", evaluation_jarl_depth=2),
            "output_dir": str(tmp_path / "study"),
        }
    )
    assert result["status"] == "completed"
    assert trainer.calls[0]["evaluation_opponent"] == "jarl"
    assert trainer.calls[0]["evaluation_jarl_depth"] == 2


def test_resume_seed_is_kept_and_fresh_trials_draw_separate_seeds(tmp_path, stub, monkeypatch):
    trainer = stub(lambda config: [0.5])
    monkeypatch.setattr(
        "hnefatafl.training.load_checkpoint",
        lambda path: (
            None,
            {"metadata": {"training_config": {"seed": 99}}},
        ),
    )
    result = tune(
        {
            "trials": 2,
            "study_name": "resume-seed",
            "seed": 7,
            "search_space": {"learning_rate": {"type": "categorical", "choices": [0.001]}},
            "training": training(resume=str(tmp_path / "source.pt")),
            "output_dir": str(tmp_path / "study"),
        }
    )
    assert result["status"] == "completed"
    assert {call["seed"] for call in trainer.calls} == {99}
    assert result["source_seed"] == 99
    assert result["sampler_seed"] == 7


def test_failure_budget_stops_a_failing_study(tmp_path, stub):
    stub(lambda config: [0.5], fail_on=range(10))
    result = tune(
        {
            "trials": 10,
            "max_failures": 2,
            "study_name": "failures",
            "seed": 0,
            "search_space": {"learning_rate": {"type": "categorical", "choices": [0.001]}},
            "training": training(),
            "output_dir": str(tmp_path / "study"),
        }
    )
    assert result["status"] == "completed"
    assert result["completed_trials"] == 0
    assert result["failed_trials"] == 2


def test_weighted_multi_opponent_aggregate_objective(tmp_path, stub, monkeypatch):
    stub(lambda config: [0.4])

    class DummyModel:
        pass

    monkeypatch.setattr("hnefatafl.training.load_checkpoint", lambda path: (DummyModel(), {}))
    seen: list[str] = []

    def fake_evaluate(model, config, **kwargs):
        opponent = config["evaluation_opponent"]
        seen.append(opponent)
        score = {"shield_bearer": 1.0, "shield_maiden": 0.0, "jarl": 0.5}[opponent]
        return {
            "score": score,
            "wins": 0,
            "losses": 0,
            "draws": 0,
            "truncated": 0,
            "opponent": opponent,
            "opponent_identity": f"stub:{opponent}",
        }

    monkeypatch.setattr("hnefatafl.training.evaluate_model", fake_evaluate)
    result = tune(
        {
            "trials": 1,
            "study_name": "aggregate",
            "seed": 0,
            "search_space": {"learning_rate": {"type": "categorical", "choices": [0.001]}},
            "training": training(),
            "opponents": [
                {"opponent": "shield_bearer", "weight": 0.5},
                {"opponent": "shield_maiden", "weight": 0.25},
                {"opponent": "jarl", "weight": 0.25},
            ],
            "output_dir": str(tmp_path / "study"),
        }
    )
    # (0.5*1.0 + 0.25*0.0 + 0.25*0.5) / 1.0 = 0.625
    assert result["best_score"] == pytest.approx(0.625)
    assert set(seen) == {"shield_bearer", "shield_maiden", "jarl"}
    assert result["trials"][0]["opponents"]["jarl"]["score"] == pytest.approx(0.5)


def test_held_out_verification_records_an_independent_score(tmp_path, stub, monkeypatch):
    stub(lambda config: [0.3])
    monkeypatch.setattr("hnefatafl.training.load_checkpoint", lambda path: (object(), {}))
    monkeypatch.setattr(
        "hnefatafl.training.evaluate_model",
        lambda model, config, **kwargs: {
            "score": 0.75,
            "wins": 3,
            "losses": 1,
            "draws": 0,
            "truncated": 0,
            "opponent": config["evaluation_opponent"],
            "opponent_identity": "stub:verify",
            "seed": config["seed"],
        },
    )
    result = tune(
        {
            "trials": 1,
            "study_name": "verify",
            "seed": 0,
            "search_space": {"learning_rate": {"type": "categorical", "choices": [0.001]}},
            "training": training(),
            "verification": {"games": 4, "seed": 123, "opponent": "jarl"},
            "output_dir": str(tmp_path / "study"),
        }
    )
    assert result["verification_result"]["score"] == pytest.approx(0.75)
    assert result["verification_result"]["opponent"] == "jarl"
    assert result["verification_result"]["seed"] == 123
    assert result["verification_result"]["games"] == 4


def test_result_json_and_best_config_are_persisted(tmp_path, stub):
    stub(lambda config: [0.7])
    output = tmp_path / "study"
    result = tune(
        {
            "trials": 1,
            "study_name": "persisted",
            "seed": 0,
            "search_space": {"learning_rate": {"type": "categorical", "choices": [0.002]}},
            "training": training(),
            "output_dir": str(output),
        }
    )
    persisted = json.loads((output / "result.json").read_text())
    assert persisted["best_trial"] == result["best_trial"]
    assert persisted["trials"] == result["trials"]
    best_config = json.loads((output / "best-config.json").read_text())
    assert best_config["learning_rate"] == 0.002


# --- search-space contract ---------------------------------------------------


def test_search_space_contract_accepts_every_documented_kind():
    space = search_space_config(
        {
            "learning_rate": {"type": "float", "low": 1e-5, "high": 1e-2, "log": True},
            "exploration": {"type": "float", "low": 0.5, "high": 2.0},
            "batch_size": {"type": "int", "low": 8, "high": 64, "step": 8},
            "channels": {"type": "categorical", "choices": [8, 16, 32]},
        }
    )
    assert space["learning_rate"]["log"] is True
    assert space["batch_size"]["step"] == 8
    assert space["channels"]["choices"] == [8, 16, 32]


@pytest.mark.parametrize(
    "space",
    [
        {},
        {"unknown": {"type": "float", "low": 0.0, "high": 1.0}},
        {"learning_rate": {"type": "float", "low": 0.0, "high": 1.0, "log": True}},
        {"batch_size": {"type": "int", "low": 1.5, "high": 4.0}},
        {"batch_size": {"type": "int", "low": 1, "high": 4, "step": 0}},
        {"batch_size": {"type": "int", "low": 1, "high": 4, "log": True, "step": 2}},
        {"learning_rate": {"type": "float", "low": 0.1, "high": 0.2, "step": 0.1}},
        {"channels": {"type": "categorical", "choices": []}},
        {"channels": {"type": "categorical", "choices": [True, 1]}},
        {"learning_rate": {"type": "gaussian", "low": 0.0, "high": 1.0}},
        {"learning_rate": {"type": [], "low": 0.0, "high": 1.0}},
    ],
)
def test_search_space_contract_rejects_invalid_spaces(space):
    with pytest.raises(ArchiveError):
        search_space_config(space)


# --- portable tuning configuration -------------------------------------------


def test_tuning_config_accepts_optional_study_settings():
    result = tuning_config(
        {
            "trials": 4,
            "study_name": "portable",
            "seed": 3,
            "search_space": {"learning_rate": {"type": "float", "low": 1e-4, "high": 1e-2}},
            "training": {"preset": "smoke", "ruleset": "brandubh"},
            "opponents": [
                {"opponent": "shield_bearer", "weight": 0.5},
                {"opponent": "jarl", "weight": 0.5},
            ],
            "verification": {"games": 8, "seed": 42, "opponent": "shield_maiden"},
            "sampler": {"n_startup_trials": 5, "multivariate": True},
            "pruner": {"n_startup_trials": 2, "n_warmup_steps": 1},
            "max_failures": 2,
        }
    )
    assert [entry["opponent"] for entry in result["opponents"]] == ["shield_bearer", "jarl"]
    assert result["verification"] == {"games": 8, "seed": 42, "opponent": "shield_maiden"}
    assert result["sampler"]["multivariate"] is True
    assert result["max_failures"] == 2


@pytest.mark.parametrize(
    "changes",
    [
        {"opponents": [{"opponent": "champion", "weight": 1}]},
        {"opponents": [{"opponent": "jarl", "weight": 0}]},
        {"opponents": [{"opponent": "jarl", "weight": 2}]},
        {"verification": {"games": 7}},
        {"verification": {"games": 4, "opponent": "champion"}},
        {"sampler": {"n_startup_trials": -1}},
        {"pruner": {"unknown": 1}},
        {"max_failures": 0},
    ],
)
def test_tuning_config_rejects_invalid_study_settings(changes):
    payload = {
        "trials": 4,
        "study_name": "portable",
        "seed": 3,
        "search_space": {"learning_rate": {"type": "float", "low": 1e-4, "high": 1e-2}},
        "training": {"preset": "smoke", "ruleset": "brandubh"},
        **changes,
    }
    with pytest.raises(ArchiveError):
        tuning_config(payload)
