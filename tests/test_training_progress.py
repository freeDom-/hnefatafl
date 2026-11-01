"""Phase-independent progress, heartbeats and bounded monitoring publication.

The dashboard must keep advancing while one self-play game, optimization epoch
or evaluation match is still running, and must be able to tell active work from
a stalled or unresponsive worker. These tests drive real training phases with
recording sinks and a controlled clock; no GPU is required.
"""

import json
import math
import os
import random
import threading
import time
from pathlib import Path

import pytest
import torch

from hnefatafl import inference_backends
from hnefatafl import self_play as self_play_module
from hnefatafl import training as training_module
from hnefatafl.engine import legal_moves, new_game
from hnefatafl.models import PolicyValueNet, encode_state
from hnefatafl.training import (
    evaluate_model,
    generate_self_play,
    normalize_config,
    optimize_replay,
    train,
)
from hnefatafl.training_augmentation import augmentation_rng
from hnefatafl.training_monitoring import (
    TrainingMonitor,
    heartbeat_projection,
    overlay_heartbeat,
    publication_age,
    read_heartbeat,
)


@pytest.fixture(autouse=True)
def single_cpu_thread():
    previous = torch.get_num_threads()
    torch.set_num_threads(1)
    yield
    torch.set_num_threads(previous)


class RecordingSink:
    """Minimal ProgressSink capturing exactly what the dashboard consumes."""

    def __init__(self):
        self.phases = []
        self.counters = {}
        self.ticks = 0
        self.positions = 0
        self.completed = 0

    def phase(self, name, *, iteration=None):
        self.phases.append((name, iteration))
        self.counters = {}

    def tick(self, **deltas):
        self.ticks += 1
        self.positions += deltas.get("positions", 0)
        self.completed += deltas.get("games_completed", 0)
        for key, value in deltas.items():
            self.counters[key] = self.counters.get(key, 0) + value

    def counts(self, **values):
        self.counters.update(values)


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


def blocked_game_factory(entered: threading.Event, released: threading.Event):
    """Replace one game with a real example plus a unit of work the test releases."""

    def blocked_game(model, config, seed, should_stop, monitor=None):
        state = new_game(config["ruleset"])
        moves = legal_moves(state)
        area = state.size**2
        example = {
            "input": encode_state(state),
            "actions": [moves[0].source * area + moves[0].target],
            "probabilities": [1.0],
            "legal": [move.source * area + move.target for move in moves],
            "value": 0.0,
        }
        yield state
        entered.set()
        assert released.wait(30)
        return [example], {"winner": "draw", "plies": 1, "simulations": 0}

    return blocked_game


def read_json(path: Path) -> dict:
    return json.loads(path.read_text())


# --- monitor timing ----------------------------------------------------------


def test_monitor_durations_advance_while_progress_stalls():
    clock = [100.0]
    wall = [1_000.0]
    monitor = TrainingMonitor(
        lambda **flags: None,
        phase="self_play",
        clock=lambda: clock[0],
        wall=lambda: wall[0],
    )
    monitor.tick(positions=3)
    monitor.published()
    first = monitor.snapshot()

    # A long single unit of work: no counter moved, but time did.
    clock[0] += 30.0
    wall[0] += 30.0
    later = monitor.snapshot()

    assert later["phase_elapsed"] == pytest.approx(30.0)
    assert later["last_progress_seconds"] == pytest.approx(30.0)
    assert later["iteration_elapsed"] == pytest.approx(30.0)
    assert later["stage"] == "self_play"
    assert later["phase_progress"] == {"positions": 3}
    # Heartbeats keep the generation; only real progress events bump it.
    assert later["revision"] == first["revision"] == 1
    assert later["started_at"] == first["started_at"]
    assert later["updated_at"] != first["updated_at"]


def test_phase_change_resets_phase_time_but_not_the_iteration():
    clock = [0.0]
    monitor = TrainingMonitor(lambda **flags: None, clock=lambda: clock[0], wall=lambda: 0.0)
    monitor.phase("self_play", iteration=4)
    monitor.tick(positions=5)
    clock[0] = 20.0
    monitor.phase("optimization", iteration=4)
    current = monitor.snapshot()
    assert current["phase_elapsed"] == 0.0
    assert current["phase_progress"] == {}
    assert current["iteration_elapsed"] == pytest.approx(20.0)

    clock[0] = 25.0
    monitor.phase("self_play", iteration=5)
    assert monitor.snapshot()["iteration_elapsed"] == 0.0


def test_heartbeat_thread_republishes_independently_and_stops_on_demand():
    calls = []
    monitor = TrainingMonitor(lambda **flags: calls.append(flags.get("heartbeat")), interval=0.01)
    monitor.start()
    deadline = time.monotonic() + 5
    while len(calls) < 3 and time.monotonic() < deadline:
        time.sleep(0.01)
    monitor.stop()
    assert len(calls) >= 3
    assert all(flag is True for flag in calls)
    stopped = len(calls)
    time.sleep(0.05)
    assert len(calls) == stopped


def test_heartbeat_projection_is_bounded_and_ignores_other_generations():
    state = {
        "status": "running",
        "stage": "self_play",
        "revision": 4,
        "duration": 12.0,
        "history": [{"iteration": 1}] * 1000,
        "metrics": {"self_play": [{"plies": 4}] * 1000},
        "evaluation_selection": {"benchmarks": {}},
    }
    projection = heartbeat_projection(state)
    assert projection["stage"] == "self_play" and projection["duration"] == 12.0
    for key in ("history", "metrics", "evaluation_selection"):
        assert key not in projection

    progress = {"status": "completed", "revision": 9, "duration": 40.0, "history": [1]}
    assert overlay_heartbeat(progress, projection) == progress
    matching = {**projection, "revision": 9, "duration": 44.0}
    merged = overlay_heartbeat(progress, matching)
    assert merged["duration"] == 44.0 and merged["history"] == [1]
    assert overlay_heartbeat(progress, None) == progress
    assert overlay_heartbeat(progress, {"revision": "9"}) == progress


def test_heartbeat_merges_the_running_trial_of_a_tuning_job():
    progress = {
        "status": "running",
        "revision": 3,
        "trial": 1,
        "trial_progress": {"revision": 3, "stage": "self_play", "history": [{"iteration": 1}]},
    }
    heartbeat = {
        "revision": 3,
        "trial": 1,
        "trial_progress": {"revision": 3, "stage": "evaluation", "phase_elapsed": 5.0},
    }
    merged = overlay_heartbeat(progress, heartbeat)
    assert merged["trial_progress"]["stage"] == "evaluation"
    assert merged["trial_progress"]["history"] == [{"iteration": 1}]


def test_read_heartbeat_tolerates_missing_and_corrupt_files(tmp_path):
    assert read_heartbeat(tmp_path) is None
    (tmp_path / "heartbeat.json").write_text("{not json")
    assert read_heartbeat(tmp_path) is None
    (tmp_path / "heartbeat.json").write_text(json.dumps({"revision": 1}))
    assert read_heartbeat(tmp_path) == {"revision": 1}


# --- self-play ---------------------------------------------------------------


@pytest.mark.parametrize("mode", ["batched", "sequential"])
def test_self_play_reports_intermediate_counters_without_double_counting(tmp_path, mode):
    sink = RecordingSink()
    model = PolicyValueNet("brandubh", 4, 0).eval()
    games, performance = generate_self_play(
        model,
        tiny_config(tmp_path, games=4, self_play_mode=mode, self_play_concurrency=2),
        seed=23,
        retain_examples=False,
        monitor=sink,
    )
    assert len(games) == 4
    assert sink.counters["games_total"] == 4
    assert sink.counters["games_completed"] == 4
    assert sink.completed == 4
    assert sink.counters["games_active"] == 0
    assert sink.counters["positions"] == sink.positions >= 4
    assert performance["games"] == 4


def test_self_play_counters_survive_a_cancelled_game(tmp_path):
    sink = RecordingSink()
    model = PolicyValueNet("brandubh", 4, 0).eval()
    with pytest.raises(InterruptedError):
        generate_self_play(
            model,
            tiny_config(tmp_path, games=4),
            seed=23,
            should_stop=lambda: sink.ticks > 1,
            monitor=sink,
        )
    # Cancellation never reports more finished games than were started, and the
    # counters stay consistent with the aggregate sink.
    assert 0 <= sink.completed <= 4
    assert sink.positions >= sink.completed
    assert sink.counters["positions"] == sink.positions
    assert sink.counters.get("games_completed", 0) == sink.completed
    assert all(value >= 0 for value in sink.counters.values() if isinstance(value, (int, float)))


# --- optimization ------------------------------------------------------------


def test_optimization_reports_steps_without_changing_results(tmp_path):
    config = tiny_config(tmp_path, games=2, epochs=2, batch_size=2)
    torch.manual_seed(0)
    learner = PolicyValueNet("brandubh", 4, 0).eval()
    games, _ = generate_self_play(learner, config, seed=7, retain_examples=True)
    replay = [example for examples, _ in games for example in examples]
    assert replay

    def run(model, monitor=None):
        return optimize_replay(
            model,
            torch.optim.AdamW(model.parameters(), lr=0.001),
            torch.amp.GradScaler("cuda", enabled=False),
            replay,
            config,
            torch.device("cpu"),
            False,
            random.Random(5),
            augmentation_rng(5, 0),
            monitor=monitor,
        )

    sink = RecordingSink()
    torch.manual_seed(11)
    baseline = run(PolicyValueNet("brandubh", 4, 0))
    torch.manual_seed(11)
    monitored = run(PolicyValueNet("brandubh", 4, 0), sink)

    assert baseline["policy_losses"] == monitored["policy_losses"]
    assert baseline["value_losses"] == monitored["value_losses"]
    assert sink.counters["optimizer_total_steps"] == config["epochs"] * math.ceil(
        len(replay) / config["batch_size"]
    )
    assert sink.counters["optimizer_steps"] == monitored["updates"]
    assert sink.counters["epoch"] == config["epochs"]
    assert sink.counters["policy_loss"] == pytest.approx(
        sum(monitored["policy_losses"]) / monitored["updates"]
    )
    assert sink.counters["value_loss"] == pytest.approx(
        sum(monitored["value_losses"]) / monitored["updates"]
    )


# --- evaluation --------------------------------------------------------------


def test_evaluation_reports_partial_tallies_matching_the_final_result(tmp_path, monkeypatch):
    moves = []

    def first_legal_move(state, _config, **_kwargs):
        from hnefatafl.engine import legal_moves

        move = legal_moves(state)[0]
        moves.append(move)
        return move

    monkeypatch.setattr("hnefatafl.training_evaluation.choose_move", first_legal_move)
    config = tiny_config(tmp_path, evaluation_games=4)
    model = PolicyValueNet("brandubh", 4, 0).eval()
    sink = RecordingSink()
    result = evaluate_model(model, config, monitor=sink)
    assert sink.counters["evaluation_total"] == 4
    assert sink.counters["evaluation_games"] == 4
    assert sink.counters["evaluation_opponent"] == result["opponent"]
    assert sink.counters["evaluation_wins"] == result["wins"]
    assert sink.counters["evaluation_draws"] == result["draws"]
    assert sink.counters["evaluation_losses"] == result["losses"]
    assert sink.counters["evaluation_plies"] >= 4

    # Monitoring must not change the recorded outcome.
    moves.clear()
    plain = evaluate_model(PolicyValueNet("brandubh", 4, 0).eval(), config)
    assert {key: plain[key] for key in ("wins", "losses", "draws", "score")} == {
        key: result[key] for key in ("wins", "losses", "draws", "score")
    }


# --- integration: a blocked phase keeps publishing ---------------------------


def test_timing_and_heartbeat_advance_while_a_phase_makes_no_progress(tmp_path, monkeypatch):
    released = threading.Event()
    entered = threading.Event()
    monkeypatch.setattr(self_play_module, "_game_context", blocked_game_factory(entered, released))
    config = tiny_config(tmp_path, games=1, iterations=1)
    output = Path(config["output_dir"])
    snapshots = []
    failure = []

    def run():
        try:
            train(config, snapshots.append)
        except Exception as error:  # pragma: no cover - surfaced by the assertion below
            failure.append(error)

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    assert entered.wait(30)

    heartbeat_path = output / "heartbeat.json"
    deadline = time.monotonic() + 15
    while not heartbeat_path.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert heartbeat_path.exists(), "the blocked phase published no heartbeat"

    progress_before = read_json(output / "progress.json")
    progress_mtime = (output / "progress.json").stat().st_mtime_ns
    first = read_json(heartbeat_path)
    time.sleep(1.5)
    second = read_json(heartbeat_path)

    assert second["phase_elapsed"] >= first["phase_elapsed"] + 1.0
    assert second["updated_at"] != first["updated_at"]
    assert second["last_progress_seconds"] >= first["last_progress_seconds"] + 1.0
    # No computational progress happened, and the retained history was untouched.
    assert second["phase_progress"] == first["phase_progress"]
    assert second["revision"] == first["revision"]
    assert (output / "progress.json").stat().st_mtime_ns == progress_mtime
    assert read_json(output / "progress.json") == progress_before
    # The bounded heartbeat file must not carry the retained history.
    assert "history" not in second

    released.set()
    worker.join(timeout=120)
    assert not worker.is_alive()
    assert failure == []

    result = read_json(output / "progress.json")
    assert result["status"] == "completed"
    assert result["revision"] > second["revision"]
    # A heartbeat from an earlier generation can never revive a terminal state.
    assert overlay_heartbeat(result, read_json(heartbeat_path)) == result
    assert snapshots[-1]["status"] == "completed"
    assert snapshots[-1]["stage"] == "completed"


def test_terminal_job_stops_publishing_and_keeps_its_duration(tmp_path, monkeypatch):
    released = threading.Event()
    entered = threading.Event()
    monkeypatch.setattr(self_play_module, "_game_context", blocked_game_factory(entered, released))
    config = tiny_config(tmp_path, games=1, iterations=1)
    output = Path(config["output_dir"])
    worker = threading.Thread(target=lambda: train(config), daemon=True)
    worker.start()
    assert entered.wait(30)
    heartbeat_path = output / "heartbeat.json"
    deadline = time.monotonic() + 15
    while not heartbeat_path.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert heartbeat_path.exists()
    released.set()
    worker.join(timeout=120)
    assert not worker.is_alive()

    final = read_json(output / "progress.json")
    heartbeat = read_json(heartbeat_path)
    assert final["status"] == "completed" and final["stage"] == "completed"
    assert final["duration"] >= heartbeat["duration"]
    time.sleep(1.2)
    # Completed jobs stop accumulating active duration.
    assert read_json(heartbeat_path) == heartbeat
    assert read_json(output / "progress.json") == final


def test_evaluation_phase_keeps_publishing_while_a_game_blocks(tmp_path, monkeypatch):
    entered = threading.Event()
    released = threading.Event()

    def blocking_move(state, _config, **_kwargs):
        entered.set()
        assert released.wait(30)
        return legal_moves(state)[0]

    monkeypatch.setattr("hnefatafl.training_evaluation.choose_move", blocking_move)
    config = tiny_config(
        tmp_path, games=1, iterations=1, evaluation_games=2, evaluation_frequency=1
    )
    output = Path(config["output_dir"])
    worker = threading.Thread(target=lambda: train(config), daemon=True)
    worker.start()
    assert entered.wait(30)

    deadline = time.monotonic() + 15
    first = None
    while first is None and time.monotonic() < deadline:
        snapshot = read_heartbeat(output)
        if snapshot is not None and snapshot.get("stage") == "evaluation":
            first = snapshot
        else:
            time.sleep(0.05)
    assert first is not None, "the blocked evaluation published no heartbeat"
    time.sleep(1.5)
    second = read_heartbeat(output)
    assert second["stage"] == "evaluation"
    assert second["phase_elapsed"] >= first["phase_elapsed"] + 1.0
    assert second["last_progress_seconds"] >= first["last_progress_seconds"] + 1.0
    # The first evaluation game is still unfinished, so its tally has not moved.
    assert second["phase_progress"]["evaluation_games"] == 0
    assert second["revision"] == first["revision"]

    released.set()
    worker.join(timeout=120)
    assert not worker.is_alive()
    result = read_json(output / "progress.json")
    assert result["status"] == "completed"
    assert result["metrics"]["evaluation"]["game_count"] == 2


def test_snapshot_reports_the_active_iteration_immediately():
    monitor = TrainingMonitor(
        lambda **flags: None, phase="initializing", clock=lambda: 0.0, wall=lambda: 0.0
    )
    # No iteration has been entered yet; the recorded iteration stays untouched.
    assert "active_iteration" not in monitor.snapshot()

    monitor.phase("self_play", iteration=42)
    assert monitor.snapshot()["active_iteration"] == 42
    # A phase change inside the same iteration keeps the active iteration.
    monitor.phase("optimization", iteration=42)
    assert monitor.snapshot()["active_iteration"] == 42
    # Entering the next iteration publishes it before any work happens.
    monitor.phase("preparing", iteration=43)
    assert monitor.snapshot()["active_iteration"] == 43


def test_publication_age_uses_the_newest_local_publication(tmp_path):
    assert publication_age(tmp_path) is None
    now = time.time()
    progress = tmp_path / "progress.json"
    progress.write_text("{}")
    heartbeat = tmp_path / "heartbeat.json"
    heartbeat.write_text("{}")
    os.utime(progress, (now - 600, now - 600))
    os.utime(heartbeat, (now - 4, now - 4))
    assert publication_age(tmp_path, now=now) == pytest.approx(4, abs=0.5)
    # A stalled heartbeat never hides a fresher retained progress file.
    os.utime(heartbeat, (now - 900, now - 900))
    assert publication_age(tmp_path, now=now) == pytest.approx(600, abs=0.5)


def test_batched_self_play_reports_scheduled_games_before_the_first_batch(tmp_path, monkeypatch):
    sink = RecordingSink()
    observed = []
    real_batch = self_play_module.policy_value_batch

    def recording_batch(*args, **kwargs):
        # Sampled exactly when the first inference batch begins: the scheduled
        # games are already active, so a long batch must not report zero.
        observed.append(sink.counters.get("games_active"))
        return real_batch(*args, **kwargs)

    monkeypatch.setattr(self_play_module, "policy_value_batch", recording_batch)
    model = PolicyValueNet("brandubh", 4, 0).eval()
    games, _ = generate_self_play(
        model,
        tiny_config(
            tmp_path,
            games=4,
            self_play_mode="batched",
            self_play_concurrency=3,
            inference_batch_size=2,
        ),
        seed=23,
        retain_examples=False,
        monitor=sink,
    )
    assert len(games) == 4
    assert observed and observed[0] == 3
    assert all(value is not None and value >= 1 for value in observed)
    assert sink.counters["games_completed"] == 4
    assert sink.counters["games_active"] == 0


def test_heartbeat_keeps_running_through_model_finalization(tmp_path, monkeypatch):
    entered = threading.Event()
    released = threading.Event()
    real_export = training_module.export_checkpoint

    def blocking_export(*args, **kwargs):
        entered.set()
        assert released.wait(30)
        return real_export(*args, **kwargs)

    monkeypatch.setattr(training_module, "export_checkpoint", blocking_export)
    config = tiny_config(tmp_path, games=1, iterations=1)
    output = Path(config["output_dir"])
    failure = []

    def run():
        try:
            train(config)
        except Exception as error:  # pragma: no cover - surfaced below
            failure.append(error)

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    assert entered.wait(60)
    heartbeat_path = output / "heartbeat.json"
    deadline = time.monotonic() + 15
    first = None
    while first is None and time.monotonic() < deadline:
        snapshot = read_heartbeat(output)
        if snapshot is not None and snapshot.get("stage") == "finalizing":
            first = snapshot
        else:
            time.sleep(0.05)
    assert first is not None, "finalization published no heartbeat"
    time.sleep(1.5)
    second = read_json(heartbeat_path)
    # The heartbeat stays live while export blocks: timings advance, the phase is
    # finalizing and the publication generation has not moved.
    assert second["stage"] == "finalizing"
    assert second["phase_elapsed"] >= first["phase_elapsed"] + 1.0
    assert second["duration"] >= first["duration"] + 1.0
    assert second["updated_at"] != first["updated_at"]
    assert second["revision"] == first["revision"]

    released.set()
    worker.join(timeout=120)
    assert not worker.is_alive() and failure == []
    final = read_json(output / "progress.json")
    assert final["status"] == "completed" and final["stage"] == "completed"
    assert final["active_iteration"] == 1
    # Monitor shutdown is clean: no heartbeat arrives after the terminal state.
    settled = read_json(heartbeat_path)
    time.sleep(1.2)
    assert read_json(heartbeat_path) == settled


def test_finalization_failure_reports_a_failed_job_and_stops_the_heartbeat(tmp_path, monkeypatch):
    def failing_export(*args, **kwargs):
        # Slow enough that the heartbeat keeps publishing while it runs.
        time.sleep(1.3)
        raise RuntimeError("model export failed")

    monkeypatch.setattr(training_module, "export_checkpoint", failing_export)
    config = tiny_config(tmp_path, games=1, iterations=1)
    output = Path(config["output_dir"])
    with pytest.raises(RuntimeError, match="model export failed"):
        train(config)

    final = read_json(output / "progress.json")
    # A failure during finalization is a failed job, never a stalled "finalizing".
    assert final["status"] == "failed" and final["stage"] == "failed"
    assert "model export failed" in final["error"]
    # A heartbeat published before the terminal failure cannot overwrite it.
    assert overlay_heartbeat(final, read_heartbeat(output)) == final
    # Monitor shutdown is clean: nothing is published after the failure.
    settled = read_heartbeat(output)
    time.sleep(1.2)
    assert read_heartbeat(output) == settled


def test_progress_reports_the_new_iteration_before_its_first_game_starts(tmp_path, monkeypatch):
    entered = threading.Event()
    released = threading.Event()
    refreshes = []

    def blocking_refresh(backend):
        refreshes.append(1)
        if len(refreshes) >= 2:
            # Iteration 2's preparation: no substantive publication has happened
            # yet, so only the monitor knows which iteration is active.
            entered.set()
            assert released.wait(30)
        return real_refresh(backend)

    real_refresh = inference_backends.TorchInference.refresh
    monkeypatch.setattr(inference_backends.TorchInference, "refresh", blocking_refresh)
    config = tiny_config(tmp_path, games=1, iterations=2)
    output = Path(config["output_dir"])
    failure = []

    def run():
        try:
            train(config)
        except Exception as error:  # pragma: no cover - surfaced below
            failure.append(error)

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    assert entered.wait(60)

    deadline = time.monotonic() + 15
    merged = None
    while merged is None and time.monotonic() < deadline:
        snapshot = overlay_heartbeat(read_json(output / "progress.json"), read_heartbeat(output))
        if snapshot.get("active_iteration") == 2:
            merged = snapshot
        else:
            time.sleep(0.05)
    assert merged is not None, "iteration 2 was never published as active"
    # The retained iteration is still the last completed one (1); the live
    # iteration is already 2 while its first game has not started.
    assert merged["iteration"] == 1
    assert merged["stage"] == "preparing"
    assert merged["active_iteration"] == 2

    released.set()
    worker.join(timeout=120)
    assert not worker.is_alive() and failure == []
    result = read_json(output / "progress.json")
    assert result["status"] == "completed"
    assert result["active_iteration"] == 2
