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
    tune,
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
    assert later["stage"] == "self_play"
    assert later["phase_progress"] == {"positions": 3}
    # No iteration is active, so none is timed and none is invented.
    for key in ("active_iteration", "iteration_started_at", "iteration_elapsed"):
        assert key not in later
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


def test_batched_evaluation_reports_the_same_partial_tallies(tmp_path):
    """The batched path `main` made the default must keep the counters live."""
    config = tiny_config(tmp_path, evaluation_games=4, evaluation_mode="batched")
    model = PolicyValueNet("brandubh", 4, 0).eval()
    sink = RecordingSink()
    result = evaluate_model(model, config, monitor=sink)
    assert sink.counters["evaluation_total"] == 4
    assert sink.counters["evaluation_games"] == 4
    assert sink.counters["evaluation_opponent"] == result["opponent"]
    assert sink.counters["evaluation_wins"] == result["wins"]
    assert sink.counters["evaluation_draws"] == result["draws"]
    assert sink.counters["evaluation_losses"] == result["losses"]
    assert sink.counters["evaluation_plies"] > 0

    # Monitoring must not change the recorded outcome of the batched path.
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
    # Wait for the phase counters the blocked game established, then check they
    # stay put: only the timings may move while no work completes.
    deadline = time.monotonic() + 15
    first = read_json(heartbeat_path)
    while not first["phase_progress"].get("games_active") and time.monotonic() < deadline:
        time.sleep(0.02)
        first = read_json(heartbeat_path)
    assert first["phase_progress"].get("games_active") == 1
    time.sleep(2.5)
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
    time.sleep(2.5)
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
    time.sleep(2.5)
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


# --- initialization monitoring ----------------------------------------------


def resumable_checkpoint(tmp_path: Path) -> tuple[str, Path]:
    """One real completed run whose full last.pt can be resumed."""
    config = tiny_config(tmp_path, games=1, iterations=1)
    result = train(config)
    assert result["status"] == "completed"
    return result["checkpoint"], Path(config["output_dir"])


def wait_for_stage(output: Path, stage: str, *, deadline: float = 30.0) -> dict:
    """Wait until the bounded heartbeat reports the requested initialization stage."""
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        snapshot = read_heartbeat(output)
        if snapshot is not None and snapshot.get("stage") == stage:
            return snapshot
        time.sleep(0.05)
    raise AssertionError(f"the {stage} stage was never published")


def run_train(config: dict, results: list, *, should_stop=None) -> threading.Thread:
    def run():
        try:
            results.append(train(config, should_stop=should_stop))
        except Exception as error:  # pragma: no cover - surfaced by the assertions
            results.append(error)

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    return worker


def test_heartbeat_covers_slow_checkpoint_loading(tmp_path, monkeypatch):
    checkpoint, _ = resumable_checkpoint(tmp_path)
    entered = threading.Event()
    released = threading.Event()
    real_load = training_module.load_checkpoint

    def blocking_load(path):
        entered.set()
        assert released.wait(30)
        return real_load(path)

    monkeypatch.setattr(training_module, "load_checkpoint", blocking_load)
    config = tiny_config(
        tmp_path, games=1, iterations=2, output_dir=str(tmp_path / "resumed"), resume=checkpoint
    )
    output = Path(config["output_dir"])
    results: list = []
    worker = run_train(config, results)
    assert entered.wait(30)

    # No model, optimizer or device exists yet, but the job already reports.
    first = wait_for_stage(output, "loading_checkpoint")
    assert first["total_iterations"] == 2 and first["revision"] >= 1
    assert publication_age(output) is not None and publication_age(output) < 5
    progress = read_json(output / "progress.json")
    assert progress["status"] == "running" and progress["stage"] == "loading_checkpoint"
    time.sleep(2.5)
    second = read_heartbeat(output)
    assert second["stage"] == "loading_checkpoint"
    assert second["phase_elapsed"] >= first["phase_elapsed"] + 1.0
    assert second["duration"] >= first["duration"] + 1.0
    assert second["updated_at"] != first["updated_at"]
    assert second["revision"] == first["revision"]

    released.set()
    worker.join(timeout=180)
    assert not worker.is_alive()
    assert results and results[0]["status"] == "completed"
    assert read_json(output / "progress.json")["stage"] == "completed"


def test_heartbeat_covers_slow_model_initialization(tmp_path, monkeypatch):
    entered = threading.Event()
    released = threading.Event()
    real_model = training_module.PolicyValueNet

    def blocking_model(*args, **kwargs):
        entered.set()
        assert released.wait(30)
        return real_model(*args, **kwargs)

    monkeypatch.setattr(training_module, "PolicyValueNet", blocking_model)
    config = tiny_config(tmp_path, games=1, iterations=1)
    output = Path(config["output_dir"])
    results: list = []
    worker = run_train(config, results)
    assert entered.wait(30)

    first = wait_for_stage(output, "initializing")
    time.sleep(2.5)
    second = read_heartbeat(output)
    assert second["stage"] == "initializing"
    assert second["phase_elapsed"] >= first["phase_elapsed"] + 1.0
    assert second["duration"] >= first["duration"] + 1.0

    released.set()
    worker.join(timeout=180)
    assert not worker.is_alive()
    assert results and results[0]["status"] == "completed"


def test_heartbeat_covers_slow_optimizer_state_restoration(tmp_path, monkeypatch):
    checkpoint, _ = resumable_checkpoint(tmp_path)
    entered = threading.Event()
    released = threading.Event()
    real_restore = torch.optim.AdamW.load_state_dict

    def blocking_restore(self, state_dict):
        entered.set()
        assert released.wait(30)
        return real_restore(self, state_dict)

    monkeypatch.setattr(torch.optim.AdamW, "load_state_dict", blocking_restore)
    config = tiny_config(
        tmp_path, games=1, iterations=2, output_dir=str(tmp_path / "resumed"), resume=checkpoint
    )
    output = Path(config["output_dir"])
    results: list = []
    worker = run_train(config, results)
    assert entered.wait(30)

    first = wait_for_stage(output, "restoring_state")
    time.sleep(2.5)
    second = read_heartbeat(output)
    assert second["stage"] == "restoring_state"
    assert second["phase_elapsed"] >= first["phase_elapsed"] + 1.0

    released.set()
    worker.join(timeout=180)
    assert not worker.is_alive()
    assert results and results[0]["status"] == "completed"


def test_heartbeat_covers_slow_inference_backend_initialization(tmp_path, monkeypatch):
    entered = threading.Event()
    released = threading.Event()
    real_create = training_module.create_inference

    def blocking_create(model, config):
        entered.set()
        assert released.wait(30)
        return real_create(model, config)

    monkeypatch.setattr(training_module, "create_inference", blocking_create)
    config = tiny_config(tmp_path, games=1, iterations=1)
    output = Path(config["output_dir"])
    results: list = []
    worker = run_train(config, results)
    assert entered.wait(30)

    first = wait_for_stage(output, "initializing_inference")
    # The model and its restored settings are already visible while the backend
    # compiles, so the dashboard is never blank during a cold start.
    assert read_json(output / "progress.json")["status"] == "running"
    time.sleep(2.5)
    second = read_heartbeat(output)
    assert second["stage"] == "initializing_inference"
    assert second["phase_elapsed"] >= first["phase_elapsed"] + 1.0

    released.set()
    worker.join(timeout=180)
    assert not worker.is_alive()
    assert results and results[0]["status"] == "completed"


def test_initialization_reports_the_stage_sequence_before_self_play(tmp_path):
    checkpoint, _ = resumable_checkpoint(tmp_path)
    config = tiny_config(
        tmp_path, games=1, iterations=2, output_dir=str(tmp_path / "resumed"), resume=checkpoint
    )
    stages: list[str] = []

    def record(state):
        stage = state.get("stage")
        if not stages or stages[-1] != stage:
            stages.append(stage)

    result = train(config, record)
    assert result["status"] == "completed"
    assert stages[:4] == [
        "starting",
        "loading_checkpoint",
        "restoring_state",
        "initializing",
    ]
    assert "initializing_inference" in stages
    assert "self_play" in stages
    # Every initialization stage precedes the first self-play game.
    assert stages.index("initializing_inference") < stages.index("self_play")


def test_initialization_failure_reports_a_terminal_failed_job(tmp_path, monkeypatch):
    def failing_load(path):
        raise RuntimeError("checkpoint is unreadable")

    monkeypatch.setattr(training_module, "load_checkpoint", failing_load)
    config = tiny_config(
        tmp_path, games=1, iterations=1, output_dir=str(tmp_path / "resumed"), resume="missing.pt"
    )
    output = Path(config["output_dir"])
    with pytest.raises(RuntimeError, match="checkpoint is unreadable"):
        train(config)

    final = read_json(output / "progress.json")
    assert final["status"] == "failed" and final["stage"] == "failed"
    assert final["error"] == "checkpoint is unreadable"
    # No heartbeat may arrive after the terminal publication.
    settled = read_heartbeat(output)
    time.sleep(1.2)
    assert read_heartbeat(output) == settled


def test_initialization_failure_without_a_checkpoint_still_ends_failed(tmp_path, monkeypatch):
    def failing_model(*args, **kwargs):
        raise RuntimeError("the model could not be created")

    monkeypatch.setattr(training_module, "PolicyValueNet", failing_model)
    config = tiny_config(tmp_path, games=1, iterations=1)
    output = Path(config["output_dir"])
    with pytest.raises(RuntimeError, match="the model could not be created"):
        train(config)

    final = read_json(output / "progress.json")
    assert final["status"] == "failed" and final["stage"] == "failed"
    assert final["error"] == "the model could not be created"
    # No result file pretends a checkpoint was written.
    assert not (output / "result.json").exists()


def test_cancellation_before_the_first_iteration_keeps_the_restored_checkpoint(tmp_path):
    checkpoint, _ = resumable_checkpoint(tmp_path)
    config = tiny_config(
        tmp_path, games=1, iterations=2, output_dir=str(tmp_path / "resumed"), resume=checkpoint
    )
    output = Path(config["output_dir"])
    stages: list[str] = []

    def record(state):
        stage = state.get("stage")
        if not stages or stages[-1] != stage:
            stages.append(stage)

    result = train(config, record, should_stop=lambda: True)
    assert result["status"] == "cancelled"
    # The restored state is checkpointed, so the source run stays resumable.
    assert Path(result["checkpoint"]).is_file()
    assert "restoring_state" in stages
    assert "self_play" not in stages
    final = read_json(output / "progress.json")
    assert final["status"] == "cancelled"
    settled = read_heartbeat(output)
    time.sleep(1.2)
    assert read_heartbeat(output) == settled


# --- publication identity ----------------------------------------------------


def run_state(run: str, revision: int, *, trial: int | None = None, **changes) -> dict:
    state = {"run": run, "revision": revision, "status": "running", "stage": "self_play"}
    if trial is not None:
        state["trial"] = trial
    state.update(changes)
    return state


def test_monitor_publishes_one_identity_per_run():
    first = TrainingMonitor(lambda **flags: None, clock=lambda: 0.0, wall=lambda: 0.0)
    second = TrainingMonitor(lambda **flags: None, clock=lambda: 0.0, wall=lambda: 0.0)
    run = first.snapshot()["run"]
    assert run and second.snapshot()["run"] != run
    # The identity never changes for the life of one run.
    first.published()
    assert first.snapshot()["run"] == run
    assert heartbeat_projection(first.snapshot())["run"] == run
    # An explicit identity keeps tests and recovery deterministic.
    pinned = TrainingMonitor(lambda **flags: None, run="fixed")
    assert pinned.snapshot()["run"] == "fixed"


def test_heartbeat_is_only_applied_to_the_run_that_published_it():
    progress = run_state("run-b", 5, duration=10.0)
    # Same revision, different run: an earlier trial, worker or reused directory.
    assert overlay_heartbeat(progress, run_state("run-a", 5, duration=99.0)) == progress
    # Same run, stale revision: a delayed publication of the same run.
    assert overlay_heartbeat(progress, run_state("run-b", 4, duration=99.0)) == progress
    # Same run and revision: a legitimate liveness refresh.
    merged = overlay_heartbeat(progress, run_state("run-b", 5, duration=12.5))
    assert merged["duration"] == 12.5 and merged["run"] == "run-b"


def test_heartbeat_without_an_identity_falls_back_to_revision():
    # Historical files written before identities existed keep working.
    legacy_progress = {"status": "running", "revision": 3, "duration": 5.0}
    legacy_heartbeat = {"revision": 3, "duration": 7.0}
    assert overlay_heartbeat(legacy_progress, legacy_heartbeat)["duration"] == 7.0
    # A half-identified pair is ambiguous and is never applied.
    assert overlay_heartbeat(run_state("run-a", 3), legacy_heartbeat) == run_state("run-a", 3)
    assert (
        overlay_heartbeat(legacy_progress, run_state("run-a", 3, duration=9.0)) == legacy_progress
    )


def test_a_trial_transition_cannot_overlay_the_previous_trial():
    # Trial 1 reached revision 5 and left both outer files behind.
    stale = {
        "status": "running",
        "completed_trials": 0,
        "trials_requested": 2,
        "trial": 1,
        "revision": 5,
        "run": "trial-1",
        "trial_progress": run_state("trial-1", 5, duration=99.0),
    }
    # Trial 2 starts and reaches the same revision before its first heartbeat,
    # which is exactly when the stale file is still on disk.
    current = {
        **stale,
        "trial": 2,
        "run": "trial-2",
        "trial_progress": run_state("trial-2", 5, duration=1.0),
    }
    assert overlay_heartbeat(current, stale) == current
    # Its own heartbeat still refreshes it.
    refreshed = {**current, "trial_progress": run_state("trial-2", 5, duration=3.0)}
    assert overlay_heartbeat(current, refreshed)["trial_progress"]["duration"] == 3.0
    # An earlier trial is also rejected when the nested identity differs.
    nested_stale = {
        "trial": 2,
        "revision": 5,
        "run": "trial-2",
        "trial_progress": run_state("trial-1", 5, duration=99.0),
    }
    assert overlay_heartbeat(current, nested_stale)["trial_progress"]["run"] == "trial-2"


def test_heartbeat_never_revives_a_terminal_state_of_another_run():
    finished = {
        "status": "completed",
        "revision": 9,
        "run": "run-a",
        "duration": 40.0,
        "history": [1],
    }
    # Another run (a new trial, a reused directory or a restarted worker) can
    # never revive it, and neither can a delayed publication of the same run.
    assert overlay_heartbeat(finished, run_state("run-b", 9, status="running")) == finished
    assert overlay_heartbeat(finished, run_state("run-a", 8, status="running")) == finished
    # A refresh of the same generation keeps the terminal status it carries.
    assert (
        overlay_heartbeat(finished, run_state("run-a", 9, status="completed"))["status"]
        == "completed"
    )


def test_tuning_files_carry_the_run_identity_of_the_active_trial(tmp_path, monkeypatch):
    import optuna

    optuna.logging.set_verbosity(optuna.logging.WARNING)

    class QuickMonitor(training_module.TrainingMonitor):
        """A smoke trial finishes in well under a second; tick faster to observe it."""

        def __init__(self, *args, **kwargs):
            kwargs.setdefault("interval", 0.05)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(training_module, "TrainingMonitor", QuickMonitor)
    output = tmp_path / "tuning"
    snapshots: list[dict] = []
    result = tune(
        {
            "output_dir": str(output),
            "study_name": "identity",
            "trials": 1,
            "seed": 0,
            "search_space": {"channels": {"type": "categorical", "choices": [4]}},
            "training": tiny_config(
                tmp_path,
                ruleset="brandubh",
                preset="smoke",
                games=1,
                iterations=1,
                channels=4,
                blocks=0,
            ),
        },
        snapshots.append,
    )
    assert result["status"] == "completed"
    # A trial publication carries the trial's own identity, both outside and
    # inside the nested snapshot; a study publication carries the study's.
    trials = [state for state in snapshots if "duration" in state.get("trial_progress", {})]
    # Study publications carry the study's identity; the terminal result does not.
    studies = [
        state
        for state in snapshots
        if state.get("run") and "duration" not in state.get("trial_progress", {})
    ]
    assert trials and studies
    for state in trials:
        assert state["run"] and state["run"] == state["trial_progress"]["run"]
    study_runs = {state["run"] for state in studies}
    assert study_runs and not study_runs & {state["run"] for state in trials}
    # A study publication keeps the last trial's data but not its live timing.
    for state in studies:
        nested = state.get("trial_progress")
        if nested:
            assert "stage" not in nested and "duration" not in nested

    # A heartbeat from another producer or run can never overlay a retained
    # snapshot, while the same producer still refreshes it.
    active = trials[-1]
    trial_heartbeat = {
        "run": active["run"],
        "revision": active["revision"],
        "trial": active["trial"],
        "trial_progress": active["trial_progress"],
    }
    merged = overlay_heartbeat(active, trial_heartbeat)
    assert merged["trial_progress"]["run"] == active["run"]
    foreign = {
        **trial_heartbeat,
        "run": "other-run",
        "trial_progress": {**active["trial_progress"], "run": "other-run"},
    }
    assert overlay_heartbeat(active, foreign) == active
    # The study's own heartbeat, even at the same revision, is another producer.
    study_heartbeat = {
        "run": studies[-1]["run"],
        "revision": active["revision"],
        "stage": "between_trials",
        "duration": 900.0,
    }
    assert overlay_heartbeat(active, study_heartbeat) == active
    # A study heartbeat never drops the last trial's retained data.
    study_progress = studies[-1]
    if "trial_progress" in study_progress:
        assert "stage" not in study_progress["trial_progress"]
        assert (
            overlay_heartbeat(
                study_progress, {**study_heartbeat, "revision": study_progress["revision"]}
            )["trial_progress"]
            == study_progress["trial_progress"]
        )


def test_no_iteration_timing_before_an_iteration_is_entered():
    clock = [0.0]
    monitor = TrainingMonitor(
        lambda **flags: None, phase="starting", clock=lambda: clock[0], wall=lambda: 0.0
    )
    assert "iteration_elapsed" not in monitor.snapshot()

    # Every initialization stage advances its own timer and the job timer, and
    # none of them starts or publishes an iteration duration.
    for phase in (
        "loading_checkpoint",
        "initializing",
        "restoring_state",
        "initializing_inference",
    ):
        monitor.phase(phase)
        started = monitor.snapshot()
        assert started["stage"] == phase
        assert started["phase_elapsed"] == 0.0
        clock[0] += 60.0
        later = monitor.snapshot()
        # The phase timer advances; no iteration timer appears beside it.
        assert later["phase_elapsed"] == pytest.approx(60.0)
        for key in ("active_iteration", "iteration_started_at", "iteration_elapsed"):
            assert key not in started, phase
            assert key not in later, phase

    # Entering the first iteration starts its timer at zero, not at the job start.
    clock[0] = 300.0
    monitor.phase("preparing", iteration=1)
    entered = monitor.snapshot()
    assert entered["active_iteration"] == 1
    assert entered["iteration_elapsed"] == 0.0
    clock[0] += 5.0
    assert monitor.snapshot()["iteration_elapsed"] == pytest.approx(5.0)


def test_initialization_publishes_no_iteration_timing(tmp_path, monkeypatch):
    """A slow checkpoint load is initialization, not iteration work."""
    checkpoint, _ = resumable_checkpoint(tmp_path)
    entered = threading.Event()
    released = threading.Event()
    real_load = training_module.load_checkpoint

    def blocking_load(path):
        entered.set()
        assert released.wait(30)
        return real_load(path)

    monkeypatch.setattr(training_module, "load_checkpoint", blocking_load)
    config = tiny_config(
        tmp_path, games=1, iterations=2, output_dir=str(tmp_path / "resumed"), resume=checkpoint
    )
    output = Path(config["output_dir"])
    results: list = []
    worker = run_train(config, results)
    assert entered.wait(30)

    first = wait_for_stage(output, "loading_checkpoint")
    # The job and phase timers advance while the checkpoint loads, and no
    # iteration is timed until one actually starts.
    assert first["duration"] >= 0.0 and first["phase_elapsed"] >= 0.0
    assert "iteration_elapsed" not in first
    assert "active_iteration" not in first
    progress = read_json(output / "progress.json")
    assert progress["status"] == "running" and "iteration_elapsed" not in progress
    time.sleep(1.2)
    second = read_heartbeat(output)
    assert second["phase_elapsed"] >= first["phase_elapsed"]
    assert "iteration_elapsed" not in second

    released.set()
    worker.join(timeout=180)
    assert not worker.is_alive()
    assert results and results[0]["status"] == "completed"
    # The iteration timer exists once an iteration ran.
    final = read_json(output / "progress.json")
    assert final["active_iteration"] == 2
    assert final["iteration_elapsed"] >= 0.0


def test_tuning_study_duration_never_resets_between_trials(tmp_path):
    """Two real trials: the study clock spans them while each trial clock restarts."""
    import optuna

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    output = tmp_path / "tuning"
    snapshots: list[dict] = []
    result = tune(
        {
            "output_dir": str(output),
            "study_name": "duration",
            "trials": 2,
            "seed": 0,
            "search_space": {"channels": {"type": "categorical", "choices": [4, 8]}},
            "training": tiny_config(
                tmp_path, ruleset="brandubh", preset="smoke", games=1, iterations=1, blocks=0
            ),
        },
        snapshots.append,
    )
    assert result["status"] == "completed" and result["completed_trials"] == 2

    # The study clock only moves forwards, including across the trial boundary.
    study_durations = [state["duration"] for state in snapshots if "duration" in state]
    assert study_durations == sorted(study_durations)
    per_trial: dict[int, list[float]] = {}
    for state in snapshots:
        if state.get("trial_progress") and "duration" in state:
            per_trial.setdefault(state["trial"], []).append(state["duration"])
    assert set(per_trial) == {0, 1}
    assert min(per_trial[1]) >= max(per_trial[0])

    # The second trial's own clock restarted instead of inheriting the study's.
    trial_durations = [
        state["trial_progress"]["duration"]
        for state in snapshots
        if state.get("trial") == 1 and "duration" in state.get("trial_progress", {})
    ]
    assert trial_durations and min(trial_durations) < max(per_trial[0])

    # The final publication carries the authoritative study duration.
    final = read_json(output / "progress.json")
    assert final["duration"] == result["duration"] >= max(per_trial[1])


# --- study-level monitoring --------------------------------------------------


def tune_config(tmp_path, **changes) -> dict:
    return {
        "output_dir": str(tmp_path / "tuning"),
        "study_name": "monitoring",
        "trials": 2,
        "seed": 0,
        "search_space": {"channels": {"type": "categorical", "choices": [4, 8]}},
        "training": tiny_config(
            tmp_path, ruleset="brandubh", preset="smoke", games=1, iterations=1, blocks=0
        ),
        **changes,
    }


def run_tune(config: dict, results: list, *, should_stop=None) -> threading.Thread:
    def run():
        try:
            results.append(tune(config, None, should_stop))
        except Exception as error:  # pragma: no cover - surfaced by the assertions
            results.append(error)

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    return worker


def test_slow_study_initialization_keeps_the_heartbeat_alive(tmp_path, monkeypatch):
    import optuna

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    entered = threading.Event()
    released = threading.Event()
    real_create = optuna.create_study

    def blocking_create(*args, **kwargs):
        entered.set()
        assert released.wait(30)
        return real_create(*args, **kwargs)

    monkeypatch.setattr(optuna, "create_study", blocking_create)
    config = tune_config(tmp_path)
    output = Path(config["output_dir"])
    results: list = []
    worker = run_tune(config, results)
    assert entered.wait(30)

    # Loading the study database is a study-level phase with its own heartbeat.
    first = wait_for_stage(output, "initializing_study")
    assert "trial" not in first and "trial_progress" not in first
    assert not first.get("phase_progress")
    time.sleep(2.5)
    second = read_heartbeat(output)
    assert second["stage"] == "initializing_study"
    assert second["phase_elapsed"] >= first["phase_elapsed"] + 1.0
    assert second["duration"] >= first["duration"] + 1.0
    assert second["revision"] == first["revision"]
    # The retained progress file names the same study-level phase.
    progress = read_json(output / "progress.json")
    assert progress["status"] == "running" and progress["stage"] == "initializing_study"

    released.set()
    worker.join(timeout=300)
    assert not worker.is_alive()
    assert results and results[0]["status"] == "completed"


def test_a_slow_transition_between_trials_keeps_the_heartbeat_alive(tmp_path, monkeypatch):
    import optuna

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    entered = threading.Event()
    released = threading.Event()
    asks = []
    real_ask = optuna.study.Study.ask

    def blocking_ask(study, *args, **kwargs):
        asks.append(1)
        if len(asks) >= 2:
            # Between the first trial and the second: Optuna bookkeeping, no trial.
            entered.set()
            assert released.wait(60)
        return real_ask(study, *args, **kwargs)

    monkeypatch.setattr(optuna.study.Study, "ask", blocking_ask)
    config = tune_config(tmp_path)
    output = Path(config["output_dir"])
    results: list = []
    worker = run_tune(config, results)
    assert entered.wait(60)

    first = wait_for_stage(output, "between_trials")
    # The completed trial is still recorded, but no trial is presented as running.
    retained = read_json(output / "progress.json")
    assert retained["completed_trials"] == 1
    assert "trial_progress" in retained and "duration" not in retained["trial_progress"]
    assert "active_iteration" not in retained and "iteration_elapsed" not in retained
    assert not retained.get("phase_progress")
    time.sleep(2.5)
    second = read_heartbeat(output)
    assert second["stage"] == "between_trials"
    assert second["phase_elapsed"] >= first["phase_elapsed"] + 1.0
    assert second["duration"] >= first["duration"] + 1.0
    # The retained and bounded files describe the same study-level state, so a
    # stale trial heartbeat can never overwrite it.
    assert retained["stage"] == "between_trials"
    merged = overlay_heartbeat(retained, second)
    assert merged["stage"] == "between_trials" and merged["completed_trials"] == 1

    released.set()
    worker.join(timeout=300)
    assert not worker.is_alive()
    assert results and results[0]["status"] == "completed"
    assert results[0]["completed_trials"] == 2


def test_slow_study_finalization_keeps_the_heartbeat_alive(tmp_path, monkeypatch):
    import optuna

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    entered = threading.Event()
    released = threading.Event()
    real_best_trial = optuna.study.Study.best_trial

    def blocking_best_trial(self):
        # Aggregating the study's results is part of the finalization.
        if not entered.is_set():
            entered.set()
            assert released.wait(60)
        return real_best_trial.fget(self)

    monkeypatch.setattr(optuna.study.Study, "best_trial", property(blocking_best_trial))
    config = tune_config(tmp_path, trials=1)
    results: list = []
    worker = run_tune(config, results)
    output = Path(config["output_dir"])
    assert entered.wait(120)

    first = wait_for_stage(output, "finalizing_study")
    time.sleep(2.5)
    second = read_heartbeat(output)
    assert second["stage"] == "finalizing_study"
    assert second["phase_elapsed"] >= first["phase_elapsed"] + 1.0
    assert second["duration"] >= first["duration"] + 1.0

    released.set()
    worker.join(timeout=300)
    assert not worker.is_alive()
    result = results[0]
    assert result["status"] == "completed"
    # Both terminal files carry the authoritative study duration, which covers the
    # blocked finalization, and no heartbeat arrives afterwards.
    final = read_json(output / "progress.json")
    stored = read_json(output / "result.json")
    assert final["duration"] == result["duration"] == stored["duration"]
    assert final["duration"] >= second["duration"]
    settled = read_heartbeat(output)
    time.sleep(1.2)
    assert read_heartbeat(output) == settled


def test_a_pruned_trial_returns_to_study_level_monitoring(tmp_path, monkeypatch):
    import optuna

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    pruned = []
    real_prune = optuna.trial.Trial.should_prune

    def prune_first_trial(trial):
        if trial.number == 0 and not pruned:
            pruned.append(1)
            return True
        return real_prune(trial)

    monkeypatch.setattr(optuna.trial.Trial, "should_prune", prune_first_trial)
    config = tune_config(tmp_path, trials=2)
    output = Path(config["output_dir"])
    snapshots: list[dict] = []
    result = tune(config, snapshots.append)
    assert result["status"] == "completed" and result["completed_trials"] == 1
    assert pruned, "the first trial was not pruned"
    assert [trial["state"] for trial in result["trials"]] == ["PRUNED", "COMPLETE"]
    # A pruned trial leaves the study-level monitoring in charge of the next one.
    between = [
        state
        for state in snapshots
        if state.get("stage") == "between_trials"
        and "duration" not in state.get("trial_progress", {})
    ]
    assert between
    # A trial publication names the trial's own phase in the outer snapshot too.
    trials = [state for state in snapshots if "duration" in state.get("trial_progress", {})]
    assert trials and all(state.get("stage") != "between_trials" for state in trials)
    assert read_json(output / "progress.json")["status"] == "completed"


def test_cancellation_during_the_study_load_publishes_a_cancelled_result(tmp_path, monkeypatch):
    import optuna

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    entered = threading.Event()
    released = threading.Event()
    stopped = {"value": False}
    real_create = optuna.create_study

    def blocking_create(*args, **kwargs):
        entered.set()
        assert released.wait(30)
        stopped["value"] = True
        return real_create(*args, **kwargs)

    monkeypatch.setattr(optuna, "create_study", blocking_create)
    config = tune_config(tmp_path)
    output = Path(config["output_dir"])
    results: list = []
    worker = run_tune(config, results, should_stop=lambda: stopped["value"])
    assert entered.wait(30)
    released.set()
    worker.join(timeout=300)
    assert not worker.is_alive()

    result = results[0]
    # Cancellation is honored at the safe point after the load, without touching
    # the study database, and the terminal state is published.
    assert result["status"] == "cancelled" and result["completed_trials"] == 0
    assert read_json(output / "progress.json")["status"] == "cancelled"
    assert read_json(output / "result.json")["status"] == "cancelled"
    settled = read_heartbeat(output)
    time.sleep(1.2)
    assert read_heartbeat(output) == settled


def test_a_study_failure_publishes_a_terminal_failed_state(tmp_path, monkeypatch):
    import optuna

    optuna.logging.set_verbosity(optuna.logging.WARNING)

    def failing_create(*args, **kwargs):
        raise RuntimeError("the study database is unavailable")

    monkeypatch.setattr(optuna, "create_study", failing_create)
    config = tune_config(tmp_path)
    output = Path(config["output_dir"])
    with pytest.raises(RuntimeError, match="study database is unavailable"):
        tune(config)

    progress = read_json(output / "progress.json")
    assert progress["status"] == "failed" and progress["stage"] == "failed"
    assert progress["error"] == "the study database is unavailable"
    # No heartbeat may arrive after the terminal publication.
    settled = read_heartbeat(output)
    time.sleep(1.2)
    assert read_heartbeat(output) == settled


def test_tuning_publications_never_mix_study_and_trial_identities(tmp_path):
    """Sample the retained and bounded files while a real study runs."""
    import optuna

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    config = tune_config(tmp_path, trials=2)
    output = Path(config["output_dir"])
    results: list = []
    worker = run_tune(config, results)
    samples: list[tuple[dict, dict | None]] = []
    deadline = time.monotonic() + 60
    while worker.is_alive() and time.monotonic() < deadline:
        if (output / "progress.json").exists():
            samples.append((read_json(output / "progress.json"), read_heartbeat(output)))
        time.sleep(0.02)
    worker.join(timeout=300)
    assert not worker.is_alive() and results and results[0]["status"] == "completed"
    assert len(samples) > 5

    for progress, heartbeat in samples:
        merged = overlay_heartbeat(progress, heartbeat)
        # A heartbeat is either applied to the snapshot it belongs to or ignored;
        # it never brings another producer's identity into the snapshot.
        if merged is not progress:
            assert merged.get("run") == progress.get("run")
            assert merged.get("trial") == progress.get("trial")


def wait_for_heartbeat_advance(
    output: Path, *, seconds: float = 1.5, deadline: float = 60.0
) -> dict:
    """Wait until the bounded heartbeat's elapsed time advanced by ``seconds``."""
    first = read_heartbeat(output)
    assert first is not None and "duration" in first
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        snapshot = read_heartbeat(output)
        if snapshot and snapshot.get("duration", 0) >= first["duration"] + seconds:
            return snapshot
        time.sleep(0.05)
    raise AssertionError("the heartbeat did not keep advancing")


def block_study_trials(optuna, monkeypatch, *, at: int, before=None):
    """Block (or fail) one `study.trials` read, deterministically by ordinal.

    The first read is the study load; the second is the handoff that follows the
    first trial, which is the one that must not delay the heartbeat.
    """
    reads: list[int] = []
    real = optuna.study.Study.trials

    def trials(self):
        reads.append(1)
        if len(reads) == at:
            if before is not None:
                before()
            return real.fget(self)
        return real.fget(self)

    monkeypatch.setattr(optuna.study.Study, "trials", property(trials))
    return reads


def test_the_study_owns_the_heartbeat_while_a_trial_is_booked(tmp_path, monkeypatch):
    """Ownership moves before the objective's post-training Optuna work, and the
    completed count follows Optuna's committed state, not `train` returning."""
    import optuna

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    entered = threading.Event()
    released = threading.Event()
    real_set = optuna.trial.Trial.set_user_attr

    def blocking_set(self, key, value):
        # The first attribute write is real post-trial bookkeeping: the trial has
        # trained, but Optuna has not committed its state yet.
        if not entered.is_set():
            entered.set()
            assert released.wait(120)
        return real_set(self, key, value)

    monkeypatch.setattr(optuna.trial.Trial, "set_user_attr", blocking_set)
    config = tune_config(tmp_path, trials=2)
    output = Path(config["output_dir"])
    results: list = []
    worker = run_tune(config, results)
    assert entered.wait(120)

    # The study owns the heartbeat while that write is blocked, and the finished
    # trial is not counted yet because Optuna has not recorded it as complete.
    first = wait_for_stage(output, "between_trials")
    assert read_json(output / "progress.json")["completed_trials"] == 0
    advanced = wait_for_heartbeat_advance(output, seconds=1.5)
    assert advanced["stage"] == "between_trials"
    assert advanced["duration"] >= first["duration"] + 1.0

    # The finished trial is not presented as running and its stale heartbeat cannot
    # overwrite the study's snapshot.
    retained = read_json(output / "progress.json")
    assert "duration" not in retained["trial_progress"]
    assert "active_iteration" not in retained["trial_progress"]
    stale = {
        **advanced,
        "run": retained["trial_progress"]["run"],
        "revision": advanced["revision"],
    }
    assert overlay_heartbeat(retained, stale) == retained

    released.set()
    worker.join(timeout=300)
    assert not worker.is_alive()
    result = results[0]
    # The next trial begins normally and the study finishes with both trials counted
    # from Optuna's own committed states.
    assert result["status"] == "completed" and result["completed_trials"] == 2
    assert [trial["state"] for trial in result["trials"]] == ["COMPLETE", "COMPLETE"]


def test_two_trials_report_continuous_monitoring_and_counts(tmp_path):
    """Across a whole study the clocks, counters and trial data stay coherent."""
    import optuna

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    config = tune_config(tmp_path, trials=2)
    snapshots: list[dict] = []
    result = tune(config, snapshots.append)
    assert result["status"] == "completed" and result["completed_trials"] == 2
    assert [trial["number"] for trial in result["trials"]] == [0, 1]
    assert [trial["state"] for trial in result["trials"]] == ["COMPLETE", "COMPLETE"]

    # The study clock and the completed-trial counter only ever advance.
    study_durations = [state["duration"] for state in snapshots]
    assert study_durations == sorted(study_durations)
    counts = [state["completed_trials"] for state in snapshots if "completed_trials" in state]
    assert counts == sorted(counts) and max(counts) == 2
    assert 1 in counts, "the first finished trial was never counted"
    assert not [state for state in snapshots if state.get("stage") == "between_trials"][0][
        "trial_progress"
    ].get("duration"), "a finished trial kept its live duration"

    # Each trial reports its own numbers, its own clock and its own identity.
    trials = [state for state in snapshots if "duration" in state.get("trial_progress", {})]
    assert {state["trial"] for state in trials} == {0, 1}
    first_trial = [state["trial_progress"]["duration"] for state in trials if state["trial"] == 0]
    second_trial = [state["trial_progress"]["duration"] for state in trials if state["trial"] == 1]
    assert min(second_trial) < max(first_trial)
    for state in trials:
        assert state["run"] == state["trial_progress"]["run"]
    assert {state["run"] for state in trials if state["trial"] == 0} != {
        state["run"] for state in trials if state["trial"] == 1
    }

    # The last trial's retained data survives the handoff back to the study.
    between = [state for state in snapshots if state.get("stage") == "between_trials"]
    assert between
    projected = between[-1]["trial_progress"]
    assert "history" in projected and "metrics" in projected
    assert "stage" not in projected and "phase_progress" not in projected


def test_a_pruned_trial_is_never_counted_as_completed(tmp_path, monkeypatch):
    """A pruned trial leaves the counter alone and the next trial unaffected."""
    import optuna

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    pruned = []
    real_prune = optuna.trial.Trial.should_prune

    def prune_first_trial(trial):
        if trial.number == 0 and not pruned:
            pruned.append(1)
            return True
        return real_prune(trial)

    monkeypatch.setattr(optuna.trial.Trial, "should_prune", prune_first_trial)
    config = tune_config(tmp_path, trials=2)
    output = Path(config["output_dir"])
    snapshots: list[dict] = []
    result = tune(config, snapshots.append)
    assert pruned and result["completed_trials"] == 1
    assert [trial["state"] for trial in result["trials"]] == ["PRUNED", "COMPLETE"]
    counts = [state["completed_trials"] for state in snapshots if "completed_trials" in state]
    assert counts == sorted(counts)
    assert 0 in counts and max(counts) == 1
    # The second trial is reported as the study's, not as the pruned one.
    assert {state["trial"] for state in snapshots if "trial" in state} == {0, 1}
    assert read_json(output / "progress.json")["status"] == "completed"


def test_cancellation_between_trials_ends_the_study_safely(tmp_path, monkeypatch):
    """A stop requested during a trial cancels cleanly and leaves the study usable."""
    import optuna

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    real_train = training_module.train
    stopped = {"value": False}

    def cancelling_train(config, progress, should_stop):
        result = real_train(config, progress, should_stop)
        # The stop arrives while the first trial is finishing.
        stopped["value"] = True
        return result

    monkeypatch.setattr(training_module, "train", cancelling_train)
    config = tune_config(tmp_path, trials=3)
    output = Path(config["output_dir"])
    results: list = []
    worker = run_tune(config, results, should_stop=lambda: stopped["value"])
    worker.join(timeout=600)
    assert not worker.is_alive()
    result = results[0]
    assert result["status"] == "cancelled"
    # The trial that finished is counted; the cancelled one is Optuna's PRUNED.
    assert result["completed_trials"] == 1
    assert read_json(output / "progress.json")["status"] == "cancelled"
    assert read_json(output / "result.json")["status"] == "cancelled"
    settled = read_heartbeat(output)
    time.sleep(1.2)
    assert read_heartbeat(output) == settled

    # The study database stays consistent: the same study can be resumed.
    again = tune({**config, "trials": 1})
    assert again["status"] == "completed" and again["completed_trials"] == 2


def test_a_failing_study_load_publishes_a_terminal_failed_state(tmp_path, monkeypatch):
    """A real storage failure while loading the study is terminal and recorded."""
    import optuna

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    failures: list[int] = []

    def fail():
        failures.append(1)
        raise RuntimeError("the study database is unavailable")

    # The first full-history read is the one that initialises the count at load.
    block_study_trials(optuna, monkeypatch, at=1, before=fail)
    config = tune_config(tmp_path, trials=1)
    output = Path(config["output_dir"])
    with pytest.raises(RuntimeError, match="the study database is unavailable"):
        tune(config)
    assert failures == [1], "the storage failure was never injected"
    progress = read_json(output / "progress.json")
    assert progress["status"] == "failed"
    assert progress["error"] == "the study database is unavailable"
    assert not (output / "result.json").exists()
    settled = read_heartbeat(output)
    time.sleep(1.2)
    assert read_heartbeat(output) == settled


def test_a_failing_terminal_aggregation_publishes_a_failed_state(tmp_path, monkeypatch):
    """A storage failure while aggregating the final result is terminal and recorded."""
    import optuna

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    failures: list[int] = []

    def fail():
        failures.append(1)
        raise RuntimeError("the study database is unavailable")

    # The load reads the history once; the aggregation is the next full read.
    block_study_trials(optuna, monkeypatch, at=2, before=fail)
    config = tune_config(tmp_path, trials=1)
    output = Path(config["output_dir"])
    with pytest.raises(RuntimeError, match="the study database is unavailable"):
        tune(config)
    assert failures == [1], "the storage failure was never injected"
    progress = read_json(output / "progress.json")
    assert progress["status"] == "failed"
    assert progress["error"] == "the study database is unavailable"


def test_an_objective_failure_is_not_counted_and_not_masked(tmp_path, monkeypatch):
    """A trial that trained but failed afterwards is Optuna's FAIL, not a completion."""
    import optuna

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    written: list[str] = []

    def failing_set(self, key, value):
        written.append(key)
        raise RuntimeError("attribute write failed")

    monkeypatch.setattr(optuna.trial.Trial, "set_user_attr", failing_set)
    config = tune_config(tmp_path, trials=1)
    output = Path(config["output_dir"])
    snapshots: list[dict] = []
    # The failing trial is recorded as FAIL and the study stops once its failure
    # budget is exhausted, instead of masking the study behind an exception.
    result = tune(config, snapshots.append)
    assert written, "the attribute write was never attempted"
    assert result["status"] == "failed"
    assert result["completed_trials"] == 0
    assert result["failed_trials"] == 1
    assert result["trials"][0]["state"] == "FAIL"
    progress = read_json(output / "progress.json")
    assert progress["status"] == "failed"
    assert all(state.get("completed_trials", 0) == 0 for state in snapshots)
    assert (output / "result.json").exists()


def test_study_and_trial_revisions_never_move_backwards(tmp_path):
    """Sampled publications keep their own run identity and revision order."""
    import optuna

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    config = tune_config(tmp_path, trials=2)
    output = Path(config["output_dir"])
    results: list = []
    worker = run_tune(config, results)
    highest: dict[str, int] = {}
    samples = 0
    deadline = time.monotonic() + 120
    while worker.is_alive() and time.monotonic() < deadline:
        progress_path = output / "progress.json"
        if progress_path.exists():
            retained = read_json(progress_path)
            heartbeat = read_heartbeat(output)
            if isinstance(retained.get("run"), str) and isinstance(retained.get("revision"), int):
                previous = highest.get(retained["run"])
                assert previous is None or retained["revision"] >= previous
                highest[retained["run"]] = retained["revision"]
            if heartbeat is not None:
                # A heartbeat is only ever applied to its own generation.
                merged = overlay_heartbeat(retained, heartbeat)
                if merged is not retained:
                    assert merged.get("run") == retained.get("run")
                    assert merged.get("revision") == heartbeat.get("revision")
            samples += 1
        time.sleep(0.01)
    worker.join(timeout=300)
    assert not worker.is_alive()
    assert results and results[0]["status"] == "completed"
    assert samples > 5
    assert len(highest) >= 3, "the study and both trials should each have a run identity"


def test_the_study_history_is_not_rescanned_for_every_trial(tmp_path, monkeypatch):
    """Bookkeeping must not re-read the whole study after each trial.

    A full-history read per trial is O(N^2) overall; the counter is updated from
    Optuna's callbacks instead, so the number of reads must not grow with the
    number of trials.
    """
    import optuna

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    reads = block_study_trials(optuna, monkeypatch, at=-1)
    short = tune(tune_config(tmp_path / "one", trials=1))
    reads_for_one = len(reads)
    reads.clear()
    long = tune(tune_config(tmp_path / "three", trials=3))
    reads_for_three = len(reads)

    assert short["completed_trials"] == 1 and long["completed_trials"] == 3
    assert reads_for_one >= 2, "the study history was never read at all"
    assert reads_for_one == reads_for_three, (
        f"the study history was re-read per trial: {reads_for_one} reads for one "
        f"trial and {reads_for_three} for three"
    )


def test_a_resumed_study_starts_from_its_persisted_completed_trials(tmp_path):
    """Resuming reports the study's own completed trials, then adds its own."""
    import optuna

    optuna.logging.set_verbosity(optuna.logging.WARNING)
    config = tune_config(tmp_path, trials=2)
    first = tune(config)
    assert first["status"] == "completed" and first["completed_trials"] == 2

    snapshots: list[dict] = []
    again = tune({**config, "trials": 1}, snapshots.append)
    assert again["status"] == "completed" and again["completed_trials"] == 3
    counts = [state["completed_trials"] for state in snapshots if "completed_trials" in state]
    assert counts == sorted(counts)
    # The persisted count is published before the resumed trial finishes, and the
    # new trial adds exactly one.
    assert 2 in counts and max(counts) == 3
