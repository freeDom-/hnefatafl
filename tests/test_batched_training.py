"""CPU self-play scheduling, preserved learning state and aggregate diagnostics."""

from dataclasses import replace
import importlib
import math

import pytest
import torch

from hnefatafl.engine import State, legal_moves, new_game
from hnefatafl.models import PolicyValueNet, load_checkpoint
from hnefatafl.training import generate_self_play, normalize_config, self_play, train


@pytest.fixture(autouse=True)
def single_cpu_thread():
    previous = torch.get_num_threads()
    torch.set_num_threads(1)
    yield
    torch.set_num_threads(previous)


def tiny_config(tmp_path, **changes):
    return normalize_config(
        {
            "preset": "smoke",
            "device": "cpu",
            "seed": 3,
            "cpu_threads": 1,
            "games": 3,
            "max_game_plies": 2,
            "evaluation_max_plies": 2,
            "mcts_time_limit": 5,
            "self_play_mode": "batched",
            "self_play_concurrency": 3,
            "inference_batch_size": 2,
            "output_dir": str(tmp_path / "training"),
            "registry_dir": str(tmp_path / "models"),
            **changes,
        }
    )


def state_from_input(encoded):
    board = []
    for index in range(49):
        row, col = divmod(index, 7)
        board.append(
            next((piece for plane, piece in enumerate("ADK") if encoded[plane, row, col]), ".")
        )
    return State("brandubh", tuple(board), "defenders" if encoded[3, 0, 0] else "attackers")


def assert_examples(examples):
    for example in examples:
        state = state_from_input(example["input"])
        expected = {move.source * 49 + move.target for move in legal_moves(state)}
        assert set(example["legal"]) == expected
        assert set(example["actions"]) <= expected
        assert len(example["actions"]) == len(example["probabilities"])
        assert sum(example["probabilities"]) == pytest.approx(1)
        assert all(0 <= value <= 1 for value in example["probabilities"])
        assert -1 <= example["value"] <= 1


def signature(games):
    return [
        (
            [
                (
                    row["input"].tolist(),
                    row["actions"],
                    row["probabilities"],
                    row["legal"],
                    row["value"],
                )
                for row in examples
            ],
            {
                key: result[key]
                for key in ("winner", "outcome", "reason", "bootstrap_value", "plies")
            },
        )
        for examples, result in games
    ]


@pytest.mark.parametrize("mode", ["sequential", "batched"])
def test_seeded_cpu_modes_generate_each_game_once_and_preserve_targets(tmp_path, mode):
    torch.manual_seed(7)
    model = PolicyValueNet("brandubh", 4, 0).eval()
    config = tiny_config(tmp_path, self_play_mode=mode)
    calls = []
    games, performance = generate_self_play(
        model, config, seed=19, on_game=lambda index, examples, result: calls.append(index)
    )
    repeated, _ = generate_self_play(model, config, seed=19)
    assert signature(games) == signature(repeated)
    if mode == "sequential":
        reference = [self_play(model, config, seed=19 + index) for index in range(config["games"])]
        assert signature(games) == signature(reference)
    assert calls == [0, 1, 2]
    assert len(games) == performance["games"] == 3
    for examples, result in games:
        assert len(examples) == result["plies"] == 2
        assert result["outcome"] == "truncated"
        assert result["winner"] is None
        assert all(row["value"] == result["bootstrap_value"] for row in examples)
        assert_examples(examples)
    assert performance["self_play_mode"] == mode
    assert performance["self_play_positions"] == 6
    assert performance["mcts_simulations"] == 6 * config["mcts_iterations"]
    assert performance["average_game_length"] == 2
    assert performance["completed_games"] == performance["drawn_games"] == 0
    assert performance["truncated_games"] == 3
    wall = performance["self_play_wall_seconds"]
    assert wall > 0
    assert performance["games_per_hour"] == pytest.approx(3 * 3600 / wall)
    assert performance["positions_per_second"] == pytest.approx(6 / wall)
    assert performance["simulations_per_second"] == pytest.approx(
        performance["mcts_simulations"] / wall
    )
    assert performance["neural_evaluations_per_second"] == pytest.approx(
        performance["neural_evaluations"] / wall
    )
    batches = performance["inference_batches"]
    assert batches > 0
    assert performance["inference_batch_size_average"] == pytest.approx(
        performance["neural_evaluations"] / batches
    )
    assert performance["inference_batch_latency_seconds"] == pytest.approx(
        performance["inference_time_seconds"] / batches
    )
    assert performance["cpu_search_seconds"] >= 0
    assert performance["search_traversal_wall_seconds"] >= 0
    if mode == "batched":
        assert 1 < performance["inference_batch_size_max"] <= config["inference_batch_size"]
    else:
        assert (
            performance["inference_batch_size_min"] == performance["inference_batch_size_max"] == 1
        )


def test_batched_truncation_bootstraps_from_defender_value_head(tmp_path):
    model = PolicyValueNet("brandubh", 4, 0).eval()
    with torch.no_grad():
        for parameter in model.value_head.parameters():
            parameter.zero_()
        model.value_head[-2].bias.fill_(math.atanh(0.25))
    games, _ = generate_self_play(model, tiny_config(tmp_path), seed=5)
    assert all(result["outcome"] == "truncated" for _, result in games)
    for examples, result in games:
        assert result["bootstrap_value"] == pytest.approx(0.25)
        assert all(row["value"] == pytest.approx(0.25) for row in examples)


def test_scheduler_replaces_finished_games_and_commits_in_seed_order(tmp_path, monkeypatch):
    scheduler = importlib.import_module("hnefatafl.self_play")
    initial = new_game("brandubh")
    terminal = replace(initial, winner="draw", reason="test_draw")
    starts = iter([initial, terminal, terminal, initial, terminal])
    monkeypatch.setattr(scheduler, "new_game", lambda ruleset: next(starts))
    model = PolicyValueNet("brandubh", 4, 0).eval()
    committed = []
    games, performance = generate_self_play(
        model,
        tiny_config(tmp_path, games=5, self_play_concurrency=2),
        seed=31,
        on_game=lambda index, examples, result: committed.append(index),
    )
    assert len(games) == performance["games"] == 5
    assert committed == list(range(5))
    assert [result["outcome"] for _, result in games] == [
        "truncated",
        "completed",
        "completed",
        "truncated",
        "completed",
    ]
    assert performance["completed_games"] == performance["drawn_games"] == 3
    assert performance["truncated_games"] == 2
    assert performance["self_play_positions"] == 4
    assert performance["inference_batch_size_max"] <= 2


def test_terminal_games_never_request_neural_inference(tmp_path, monkeypatch):
    scheduler = importlib.import_module("hnefatafl.self_play")
    terminal = replace(new_game("brandubh"), winner="draw", reason="test_draw")
    monkeypatch.setattr(scheduler, "new_game", lambda ruleset: terminal)
    model = PolicyValueNet("brandubh", 4, 0).eval()
    monkeypatch.setattr(
        model, "forward", lambda inputs: pytest.fail("Terminal state reached network")
    )
    games, performance = generate_self_play(
        model, tiny_config(tmp_path, games=5, self_play_concurrency=20), seed=11
    )
    assert len(games) == 5
    assert all(not examples and result["winner"] == "draw" for examples, result in games)
    assert performance["neural_evaluations"] == performance["inference_batches"] == 0
    assert performance["self_play_positions"] == performance["mcts_simulations"] == 0


@pytest.mark.parametrize(
    "changes",
    [
        {"self_play_mode": "parallel"},
        {"self_play_mode": None},
        {"self_play_concurrency": 0},
        {"self_play_concurrency": 1025},
        {"self_play_concurrency": True},
        {"self_play_concurrency": 1.5},
        {"inference_batch_size": 0},
        {"inference_batch_size": 4097},
        {"inference_batch_size": "2"},
        {"inference_batch_size": False},
    ],
)
def test_execution_configuration_rejects_invalid_values(tmp_path, changes):
    with pytest.raises(ValueError):
        tiny_config(tmp_path, **changes)


def test_legacy_defaults_and_auto_use_sequential_on_cpu(tmp_path):
    config = normalize_config({"preset": "smoke", "device": "cpu"})
    assert config["self_play_mode"] == "auto"
    assert config["self_play_concurrency"] == 16
    assert config["inference_batch_size"] == 32
    model = PolicyValueNet("brandubh", 4, 0).eval()
    auto, performance = generate_self_play(
        model, tiny_config(tmp_path, self_play_mode="auto"), seed=13
    )
    explicit, _ = generate_self_play(
        model, tiny_config(tmp_path, self_play_mode="sequential"), seed=13
    )
    assert performance["self_play_mode"] == "sequential"
    assert signature(auto) == signature(explicit)


def test_cancellation_interrupts_pending_batched_search(tmp_path, monkeypatch):
    scheduler = importlib.import_module("hnefatafl.self_play")
    inference = scheduler.policy_value_batch
    stopped = False

    def stop_after_batch(*args, **kwargs):
        nonlocal stopped
        result = inference(*args, **kwargs)
        stopped = True
        return result

    monkeypatch.setattr(scheduler, "policy_value_batch", stop_after_batch)
    model = PolicyValueNet("brandubh", 4, 0).eval()
    with pytest.raises(InterruptedError):
        generate_self_play(model, tiny_config(tmp_path), seed=3, should_stop=lambda: stopped)
    assert stopped


def test_cpu_batched_training_updates_cancels_and_resumes_with_saved_state(tmp_path):
    config = tiny_config(tmp_path / "first", games=2)
    torch.manual_seed(config["seed"])
    initial = PolicyValueNet("brandubh", 4, 0)
    before = {name: value.clone() for name, value in initial.state_dict().items()}
    progress = []
    first = train(config, progress.append)
    assert first["status"] == "completed"
    assert first["games_generated"] == 2
    assert first["metrics"]["updates"] > 0
    model, source = load_checkpoint(first["checkpoint"])
    assert any(not torch.equal(before[name], value) for name, value in model.state_dict().items())
    assert source["extra"]["replay"] and source["optimizer_state"]["state"]
    performance = first["metrics"]["performance"]
    assert performance["self_play_mode"] == "batched"
    assert performance["games"] == 2
    assert performance["optimization_seconds"] > 0
    assert performance["evaluation_seconds"] > 0
    assert performance["iteration_wall_seconds"] >= performance["self_play_wall_seconds"]
    history_performance = first["history"][0]["performance"]
    assert (
        history_performance["iteration_compute_seconds"] == performance["iteration_compute_seconds"]
    )
    assert history_performance["iteration_wall_seconds"] <= performance["iteration_wall_seconds"]
    assert performance["artifact_persistence_seconds"] > 0
    assert source["extra"]["history"] == first["history"]
    assert any(row.get("performance") for row in progress if row["stage"] == "self_play")

    stopped = train(
        tiny_config(tmp_path / "stopped", games=2, iterations=2, resume=first["checkpoint"]),
        should_stop=lambda: True,
    )
    assert stopped["status"] == "cancelled"
    stopped_model, snapshot = load_checkpoint(stopped["checkpoint"])
    for name, value in model.state_dict().items():
        assert torch.equal(value, stopped_model.state_dict()[name])
    for key in ("history", "python_rng", "iteration", "games_generated", "best_score"):
        assert snapshot["extra"][key] == source["extra"][key]
    assert torch.equal(snapshot["extra"]["torch_rng"], source["extra"]["torch_rng"])
    assert len(snapshot["extra"]["replay"]) == len(source["extra"]["replay"])
    for parameter, buffers in source["optimizer_state"]["state"].items():
        for name, value in buffers.items():
            assert torch.equal(value, snapshot["optimizer_state"]["state"][parameter][name])
    resumed = train(
        tiny_config(
            tmp_path / "resumed",
            games=2,
            iterations=2,
            resume=stopped["checkpoint"],
            self_play_mode="sequential",
            self_play_concurrency=1,
            inference_batch_size=1,
        )
    )
    assert resumed["status"] == "completed"
    assert resumed["iteration"] == 2
    assert resumed["games_generated"] == 4
    assert [point["iteration"] for point in resumed["history"]] == [1, 2]
    assert resumed["history"][0]["performance"]["self_play_mode"] == "batched"
    assert resumed["history"][1]["performance"]["self_play_mode"] == "sequential"
    _, payload = load_checkpoint(resumed["checkpoint"])
    assert payload["metadata"]["training_config"]["self_play_mode"] == "sequential"
    assert model.config == stopped_model.config


@pytest.mark.parametrize("mode", ["sequential", "batched"])
def test_training_can_release_examples_after_ordered_replay_callback(tmp_path, mode):
    model = PolicyValueNet("brandubh", 4, 0).eval()
    committed = []

    def retain_in_replay(index, examples, result):
        assert_examples(examples)
        committed.append((index, len(examples), result["plies"]))

    games, performance = generate_self_play(
        model,
        tiny_config(tmp_path, self_play_mode=mode),
        seed=23,
        on_game=retain_in_replay,
        retain_examples=False,
    )
    assert committed == [(0, 2, 2), (1, 2, 2), (2, 2, 2)]
    assert all(not examples and result["plies"] == 2 for examples, result in games)
    assert performance["games"] == 3
    assert performance["self_play_positions"] == 6


def test_batched_progress_starts_before_first_inference(tmp_path):
    model = PolicyValueNet("brandubh", 4, 0).eval()
    updates = []

    def check_started(_model, _inputs):
        assert updates
        assert updates[0]["game"] == 0
        assert updates[0]["performance"]["self_play_mode"] == "batched"
        assert updates[0]["performance"]["neural_evaluations"] == 0

    hook = model.register_forward_pre_hook(check_started)
    try:
        generate_self_play(model, tiny_config(tmp_path), seed=23, progress=updates.append)
    finally:
        hook.remove()
    assert updates[-1]["game"] == 3


def test_training_backpressure_bounds_out_of_order_game_backlog(tmp_path, monkeypatch):
    scheduler = importlib.import_module("hnefatafl.self_play")
    initial = new_game("brandubh")
    started, committed = [], []
    active = maximum_active = 0
    first_finished = False

    def controlled_game(model, config, seed, should_stop, monitor=None):
        nonlocal active, maximum_active, first_finished
        index = seed - 23
        started.append(index)
        active += 1
        maximum_active = max(maximum_active, active)
        if not first_finished:
            assert len(started) <= 2 * config["self_play_concurrency"]
        try:
            for _ in range(20 if index == 0 else 1):
                yield initial
            if index == 0:
                first_finished = True
            return [], {"winner": "draw", "plies": 0, "simulations": 0}
        finally:
            active -= 1

    monkeypatch.setattr(scheduler, "_game_context", controlled_game)
    monkeypatch.setattr(
        scheduler, "policy_value_batch", lambda states, *args, **kwargs: [({}, 0)] * len(states)
    )
    model = PolicyValueNet("brandubh", 4, 0).eval()
    games, performance = generate_self_play(
        model,
        tiny_config(tmp_path, games=9, self_play_concurrency=2),
        seed=23,
        on_game=lambda index, examples, result: committed.append(index),
        retain_examples=False,
    )
    assert started == committed == list(range(9))
    assert len(games) == performance["games"] == 9
    assert maximum_active == 2
    assert active == 0
