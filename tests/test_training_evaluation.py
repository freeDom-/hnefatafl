"""Single evaluators, immutable selection and iteration-100 checkpoint migration."""

from copy import deepcopy
import hashlib
import json

import pytest
import torch

from hnefatafl import training
from hnefatafl import training_evaluation as evaluation
from hnefatafl.engine import legal_moves
from hnefatafl.models import PolicyValueNet, load_checkpoint, save_checkpoint
from hnefatafl.training import compact_progress, normalize_config, train, tune


def config(tmp_path, **changes):
    return {
        "preset": "smoke",
        "device": "cpu",
        "cpu_threads": 1,
        "mcts_time_limit": 5,
        "evaluation_time_limit": 5,
        "max_game_plies": 2,
        "evaluation_max_plies": 2,
        "output_dir": str(tmp_path / "run"),
        "registry_dir": str(tmp_path / "models"),
        **changes,
    }


def match(scores, *, truncated=False):
    games = []
    for index, score in enumerate(scores):
        side = "attackers" if index % 2 == 0 else "defenders"
        winner = (
            side
            if score == 1
            else "draw"
            if score == 0.5
            else "defenders"
            if side == "attackers"
            else "attackers"
        )
        games.append(
            {"candidate_side": side, "winner": None if truncated else winner, "score": score}
        )
    return {
        "score": sum(scores) / len(scores),
        "games": games,
        "wins": scores.count(1),
        "losses": scores.count(0),
        "draws": scores.count(0.5),
        "truncated": len(scores) if truncated else 0,
    }


@pytest.mark.parametrize(
    "opponent,algorithm",
    [("shield_bearer", "baseline"), ("jarl", "alpha_beta"), ("champion", "neural")],
)
def test_only_selected_effective_opponent_and_paired_seeds(
    tmp_path, monkeypatch, opponent, algorithm
):
    torch.set_num_threads(1)
    model = PolicyValueNet("brandubh", 4, 0)
    incumbent = deepcopy(model) if opponent == "champion" else None
    calls = []

    def choose(state, settings, **kwargs):
        calls.append(dict(settings))
        return legal_moves(state)[0]

    monkeypatch.setattr(evaluation, "choose_move", choose)
    settings = normalize_config(
        config(
            tmp_path,
            evaluation_opponent=opponent,
            evaluation_jarl_depth=3,
            evaluation_jarl_time_limit=0.3,
        )
    )
    result = evaluation.evaluate_model(model, settings, previous=incumbent)
    assert len(calls) == 4
    opponents = [item for item in calls if item.get("_model") is not model]
    assert all(item["algorithm"] == algorithm for item in opponents)
    if opponent == "jarl":
        assert all(item["depth"] == 3 and item["time_limit"] == 0.3 for item in opponents)
    if incumbent is not None:
        assert all(item["_model"] is incumbent and item["iterations"] == 2 for item in opponents)
    assert [item["seed"] for item in calls] == [1000000, 1000001, 1000000, 1000001]
    assert result["opponent_identity"].startswith(opponent + ":")
    assert (
        result["candidate_as_attacker"]["game_count"]
        == result["candidate_as_defender"]["game_count"]
        == 1
    )
    assert result["truncated"] == 2 and result["draws"] == 0


def test_fixed_ties_and_separate_benchmark_configurations(tmp_path):
    model = PolicyValueNet("brandubh", 4, 0)
    cfg = normalize_config(config(tmp_path))
    state = evaluation.EvaluationSelection({}, {}, cfg)
    assert state.accept(model, {"version": 1}, match([1, 0]), cfg)
    saved = deepcopy(state.selected_snapshot())
    with torch.no_grad():
        next(model.parameters()).add_(1)
    assert not state.accept(model, {"version": 2}, match([1, 0]), cfg)
    assert state.selected_snapshot()["metadata"]["version"] == 1
    for key, tensor in saved["state_dict"].items():
        assert torch.equal(tensor, state.selected_snapshot()["state_dict"][key])
    other = normalize_config({**cfg, "evaluation_opponent": "jarl"})
    switched = evaluation.EvaluationSelection({"evaluation_state": state.data}, cfg, other)
    assert switched.identity != state.identity
    assert switched.accept(model, {"version": 3}, match([0, 0]), other)
    assert len(switched.data["benchmarks"]) == 2
    assert switched.data["benchmarks"][state.identity]["score"] == 0.5
    irrelevant = {**cfg, "evaluation_jarl_depth": 7, "evaluation_frequency": 23}
    assert evaluation.context_identity(evaluation.evaluation_context(irrelevant)) == state.identity
    changed_budget = {**cfg, "evaluation_iterations": 3}
    assert (
        evaluation.context_identity(evaluation.evaluation_context(changed_budget)) != state.identity
    )


@pytest.mark.parametrize(
    "scores,truncated,promotes",
    [
        ([1, 0] * 24, False, False),  # pronounced defender-side advantage: tied pairs
        ([1] * 26 + [0] * 22, False, False),  # 54.2% of 48 is inconclusive
        ([0] * 48, False, False),
        ([0.5] * 48, False, False),
        ([1] * 48, True, False),
        ([1] * 2, False, False),
        ([1] * 48, False, True),
    ],
)
def test_conservative_paired_champion_decision(tmp_path, scores, truncated, promotes):
    cfg = normalize_config(config(tmp_path, evaluation_opponent="champion"))
    decision = evaluation.promotion_decision(match(scores, truncated=truncated), cfg)
    assert decision["promoted"] is promotes
    assert decision["pairs"] == len(scores) // 2


def test_champion_is_immutable_and_promotion_history_portable(tmp_path):
    model = PolicyValueNet("brandubh", 4, 0)
    cfg = normalize_config(config(tmp_path, evaluation_opponent="champion"))
    state = evaluation.EvaluationSelection({}, {}, cfg)
    state.initialize_champion(model, {"version": 0}, None)
    assert state.data["champion"]["provisional"]
    result = match([1] * 48)
    assert state.accept(model, {"version": 1}, result, cfg)
    saved = deepcopy(state.selected_snapshot())
    with torch.no_grad():
        next(model.parameters()).add_(3)
    assert not state.accept(model, {"version": 2}, match([0] * 48), cfg)
    for key, tensor in saved["state_dict"].items():
        assert torch.equal(state.selected_snapshot()["state_dict"][key], tensor)
    restored = evaluation.EvaluationSelection({"evaluation_state": state.data}, cfg, cfg)
    assert restored.data["champion"]["iteration"] == 1
    assert len(restored.data["promotions"]) == 1
    assert result["opponent_identity"].endswith(":incumbent-0")
    strict = {**cfg, "champion_confidence": 0.9999, "champion_min_improvement": 0.49}
    assert not evaluation.promotion_decision(match([1] * 48), strict)["promoted"]


def test_schedule_only_selected_evaluator_missing_points_and_final(tmp_path, monkeypatch):
    calls = []

    def evaluated(model, cfg, **kwargs):
        calls.append((cfg["evaluation_opponent"], kwargs["previous"]))
        return match([1, 0])

    monkeypatch.setattr(training, "evaluate_model", evaluated)
    result = train(
        config(
            tmp_path,
            iterations=5,
            evaluation_frequency=3,
            checkpoint_frequency=1,
            evaluation_opponent="jarl",
        )
    )
    assert calls == [("jarl", None), ("jarl", None)]
    assert [row["iteration"] for row in result["history"] if "evaluation_score" in row] == [3, 5]
    assert all("evaluation_score" not in result["history"][index] for index in [0, 1, 3])
    assert result["evaluation_selection"]["last_evaluation"]["iteration"] == 5
    _, payload = load_checkpoint(result["checkpoint"])
    assert payload["extra"]["best_snapshot"] is None
    assert payload["extra"]["best_score"] == -1  # no Jarl score in legacy baseline field
    assert "previous_evaluation" not in result["metrics"]


def legacy_100(tmp_path):
    first = train(config(tmp_path / "original"))
    model, payload = load_checkpoint(first["checkpoint"])
    extra = payload["extra"]
    extra.pop("evaluation_state")
    extra.pop("training_state_version")
    extra["iteration"] = 100
    extra["best_snapshot"]["metadata"]["version"] = 80
    extra["history"] = [
        {"iteration": 100, "policy_loss": 0.9, "value_loss": 0.1, "evaluation_score": 0.75}
    ]
    extra["best_score"] = 0.75
    cfg = payload["metadata"]["training_config"]
    for key in [
        "evaluation_opponent",
        "evaluation_time_limit",
        "evaluation_jarl_depth",
        "evaluation_jarl_time_limit",
        "champion_confidence",
        "champion_min_pairs",
        "champion_min_improvement",
    ]:
        cfg.pop(key)
    cfg["iterations"] = 100
    payload["metadata"]["version"] = 100
    optimizer = training._make_optimizer(model, cfg)
    optimizer.load_state_dict(payload["optimizer_state"])
    path = tmp_path / "legacy-last.pt"
    save_checkpoint(path, model, payload["metadata"], optimizer, extra)
    return path, payload


def test_legacy_iteration100_switch_preserves_exact_learning_state_and_updates(tmp_path):
    path, source = legacy_100(tmp_path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    stopped = train(
        {
            "resume": str(path),
            "iterations": 102,
            "evaluation_opponent": "champion",
            "output_dir": str(tmp_path / "stopped"),
        },
        should_stop=lambda: True,
    )
    _, checkpoint = load_checkpoint(stopped["checkpoint"])
    for key in (
        "iteration",
        "games_generated",
        "python_rng",
        "history",
        "best_score",
        "best_models",
    ):
        assert checkpoint["extra"][key] == source["extra"][key]
    assert torch.equal(checkpoint["extra"]["torch_rng"], source["extra"]["torch_rng"])
    assert len(checkpoint["extra"]["replay"]) == len(source["extra"]["replay"])
    for a, b in zip(checkpoint["extra"]["replay"], source["extra"]["replay"]):
        assert torch.equal(a["input"], b["input"])
        assert a["value"] == b["value"]
    for parameter, values in source["optimizer_state"]["state"].items():
        for key, value in values.items():
            assert torch.equal(checkpoint["optimizer_state"]["state"][parameter][key], value)
    selection = checkpoint["extra"]["evaluation_state"]
    assert selection["champion"]["iteration"] == 80
    assert selection["champion"]["provisional"]
    assert selection["promotions"] == []
    assert len(selection["benchmarks"]) == 1
    continued = train(
        {
            "resume": stopped["checkpoint"],
            "iterations": 102,
            "output_dir": str(tmp_path / "continued"),
        }
    )
    model, final = load_checkpoint(continued["checkpoint"])
    assert final["extra"]["iteration"] == 102
    assert [row["iteration"] for row in final["extra"]["history"]] == [100, 101, 102]
    assert continued["metrics"]["updates"] > 0
    assert final["extra"]["best_score"] == 0.75
    assert final["extra"]["best_snapshot"]["metadata"]["version"] == 80
    for parameter, values in source["optimizer_state"]["state"].items():
        assert final["optimizer_state"]["state"][parameter]["step"] > values["step"]
    assert any(
        not torch.equal(value, source["state_dict"][key])
        for key, value in model.state_dict().items()
    )
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
    assert continued["evaluation_selection"]["champion_iteration"] == 80
    assert all(row["evaluation"]["opponent"] == "champion" for row in continued["history"][1:])
    assert final["extra"]["python_rng"] != source["extra"]["python_rng"]


@pytest.mark.parametrize("failure", [InterruptedError, RuntimeError])
def test_cancelled_or_failed_evaluation_preserves_incumbent_and_completed_updates(
    tmp_path, monkeypatch, failure
):
    first = train(config(tmp_path / "first"))
    _, source = load_checkpoint(first["checkpoint"])

    def fail(*args, **kwargs):
        raise failure("evaluation stopped")

    monkeypatch.setattr(training, "evaluate_model", fail)
    cfg = config(
        tmp_path / "resumed",
        resume=first["checkpoint"],
        iterations=2,
        evaluation_opponent="champion",
    )
    if failure is RuntimeError:
        with pytest.raises(RuntimeError):
            train(cfg)
        path = tmp_path / "resumed" / "run" / "last.pt"
    else:
        result = train(cfg)
        assert result["status"] == "cancelled"
        path = result["checkpoint"]
    _, payload = load_checkpoint(path)
    assert payload["extra"]["iteration"] == 2
    champion = payload["extra"]["evaluation_state"]["champion"]
    for key, value in source["extra"]["best_snapshot"]["state_dict"].items():
        assert torch.equal(champion["snapshot"]["state_dict"][key], value)
    assert payload["extra"]["evaluation_state"]["promotions"] == []


def test_compact_logs_do_not_grow_with_accumulated_history(tmp_path):
    event = {
        "stage": "self_play",
        "iteration": 100,
        "history": [{"games": ["x" * 10000]}] * 100,
        "best_models": ["x" * 10000] * 100,
        "metrics": {"evaluation": {**match([1, 0]), "opponent": "jarl"}},
    }
    compact = compact_progress(event)
    assert "history" not in compact and "games" not in compact["evaluation"]
    assert len(json.dumps(compact)) < 300
    # 100 iterations with 32 progress callbacks each stay bounded by new events.
    assert (
        sum(
            len(json.dumps(compact_progress({**event, "iteration": iteration})))
            for iteration in range(100)
            for _ in range(32)
        )
        < 1_000_000
    )
    assert compact_progress({"trial": 2, "trial_progress": event})["iteration"] == 100


@pytest.mark.parametrize(
    "changes",
    [
        {"evaluation_opponent": "seer"},
        {"evaluation_jarl_depth": 0},
        {"evaluation_jarl_depth": 9},
        {"evaluation_time_limit": 0},
        {"evaluation_jarl_time_limit": float("inf")},
        {"champion_confidence": 1},
        {"champion_min_pairs": 1},
        {"champion_min_improvement": -0.1},
    ],
)
def test_validate_new_evaluation_inputs(tmp_path, changes):
    with pytest.raises(ValueError):
        normalize_config(config(tmp_path, **changes))


@pytest.mark.parametrize("opponent", ["jarl", "champion"])
def test_optuna_never_uses_moving_or_changed_objective(tmp_path, opponent):
    with pytest.raises(ValueError, match="fixed Shield bearer"):
        tune(
            {
                "output_dir": str(tmp_path),
                "training": config(tmp_path, evaluation_opponent=opponent),
            }
        )


def test_promoted_champion_survives_checkpoint_and_rejected_resume(tmp_path, monkeypatch):
    calls = []

    def evaluated(model, cfg, **kwargs):
        calls.append(kwargs["previous"])
        return match([1] * 48 if len(calls) == 1 else [0] * 48)

    monkeypatch.setattr(training, "evaluate_model", evaluated)
    first = train(config(tmp_path / "first", evaluation_opponent="champion", evaluation_games=48))
    champion, saved = load_checkpoint(first["best_checkpoint"])
    assert saved["metadata"]["version"] == 1
    second = train(
        config(
            tmp_path / "second",
            iterations=2,
            resume=first["checkpoint"],
            evaluation_opponent="champion",
            evaluation_games=48,
        )
    )
    _, payload = load_checkpoint(second["checkpoint"])
    state = payload["extra"]["evaluation_state"]
    assert state["champion"]["iteration"] == 1
    assert len(state["promotions"]) == 1
    assert len(calls) == 2
    assert all(not parameter.requires_grad for parameter in calls[-1].parameters())
    for key, tensor in champion.state_dict().items():
        assert torch.equal(state["champion"]["snapshot"]["state_dict"][key], tensor)
    assert [point["evaluation"]["promoted"] for point in second["history"]] == [True, False]


def test_rules_draws_truncated_games_and_defender_bias_are_distinct():
    games = [
        {"candidate_side": "attackers", "winner": "defenders"},
        {"candidate_side": "defenders", "winner": "defenders"},
        {"candidate_side": "attackers", "winner": "draw"},
        {"candidate_side": "defenders", "winner": None},
    ]
    summary = evaluation._summary(games)
    assert (summary["wins"], summary["losses"], summary["draws"], summary["truncated"]) == (
        1,
        1,
        1,
        1,
    )
    assert summary["score"] == 0.375
    assert evaluation._summary(games[::2])["score"] == 0.25
    assert evaluation._summary(games[1::2])["score"] == 0.5


def test_resume_never_rewrites_source_directory_or_inherits_its_output(tmp_path):
    first = train(config(tmp_path))
    path = tmp_path / "run" / "last.pt"
    original = path.read_bytes()
    with pytest.raises(ValueError, match="new output_dir"):
        train({"resume": first["checkpoint"], "iterations": 2})
    with pytest.raises(ValueError, match="preserve the source checkpoint"):
        train({"resume": first["checkpoint"], "iterations": 2, "output_dir": str(path.parent)})
    assert path.read_bytes() == original


def test_cancelled_selfplay_reports_checkpoint_iteration_not_active_iteration(tmp_path):
    stop = False

    def progress(state):
        nonlocal stop
        if state["stage"] == "self_play":
            stop = True

    result = train(config(tmp_path), progress=progress, should_stop=lambda: stop)
    _, checkpoint = load_checkpoint(result["checkpoint"])
    assert result["status"] == "cancelled"
    assert result["iteration"] == checkpoint["extra"]["iteration"] == 0
    assert result["history"] == []
