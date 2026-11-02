"""Real spawned actors, decoupled concurrency, routing and failure cleanup."""

from copy import deepcopy
from contextlib import ExitStack
import gc
import random
import warnings

import pytest
import torch

from hnefatafl.engine import Move, apply_move, legal_moves, new_game
from hnefatafl.models import PolicyValueNet, encode_state, load_checkpoint
from hnefatafl.parallel_search import (
    PROGRESS_DELTAS,
    _ActorProgress,
    SearchPool,
    plan_batches,
)
from hnefatafl.training import (
    evaluation_uses_pool,
    generate_self_play,
    normalize_config,
    self_play_execution_mode,
    self_play_uses_pool,
    train,
)
from hnefatafl.training_evaluation import evaluate_model


@pytest.fixture(autouse=True)
def threads():
    old = torch.get_num_threads()
    torch.set_num_threads(1)
    yield
    torch.set_num_threads(old)


def settings(**changes):
    return normalize_config(
        {
            "preset": "smoke",
            "device": "cpu",
            "games": 4,
            "max_game_plies": 6,
            "mcts_iterations": 8,
            "mcts_time_limit": 300,
            "self_play_mode": "batched",
            "self_play_concurrency": 4,
            "evaluation_time_limit": 300,
            "evaluation_max_plies": 6,
            "evaluation_iterations": 8,
            "cpu_threads": 1,
            **changes,
        }
    )


def assert_games_equal(actual, expected):
    assert len(actual) == len(expected)
    for (rows, outcome), (references, result) in zip(actual, expected):
        assert {key: outcome[key] for key in ("winner", "plies", "simulations", "outcome")} == {
            key: result[key] for key in ("winner", "plies", "simulations", "outcome")
        }
        for row, reference in zip(rows, references):
            assert torch.equal(row["input"], reference["input"])
            assert row["actions"] == reference["actions"]
            assert row["probabilities"] == reference["probabilities"]
            assert row["legal"] == reference["legal"]
            assert row["value"] == pytest.approx(reference["value"], abs=1e-6)


def played_moves(examples, config, seed):
    """Reconstruct each played move from its recorded policy target.

    The generator draws one move per ply from a per-game RNG seeded with
    ``seed + game``, so an exact reconstruction also proves the search preserved
    that game's RNG stream and never consumed another game's draws.
    """
    rng = random.Random(seed)
    moves = []
    for ply, row in enumerate(examples):
        options = list(row["actions"])
        if not options:
            break
        temperature = config["temperature"] if ply < 16 else max(0.1, config["temperature"] / 4)
        weights = [probability ** (1 / temperature) for probability in row["probabilities"]]
        action = options[rng.choices(range(len(options)), weights=weights, k=1)[0]]
        area = row["input"].shape[-1] ** 2
        moves.append(Move(action // area, action % area))
    return moves


def game_digests(games):
    return [
        (
            tuple(
                (row["input"].tolist(), tuple(row["actions"]), tuple(row["probabilities"]))
                for row in examples
            ),
            result["winner"],
            result["plies"],
        )
        for examples, result in games
    ]


@pytest.mark.parametrize("cache", ["off", "exact"])
def test_spawned_games_match_reference_and_commit_in_order(cache):
    torch.manual_seed(41)
    model = PolicyValueNet("brandubh", 4, 0).eval()
    config = settings(inference_cache_mode=cache)
    expected, _ = generate_self_play(model, config, seed=17)
    commits = []
    actual, metrics = generate_self_play(
        model,
        {**config, "search_workers": 2},
        seed=17,
        on_game=lambda index, *_: commits.append(index),
    )
    assert_games_equal(actual, expected)
    assert commits == [0, 1, 2, 3]
    assert metrics["search_workers"] == 2
    assert metrics["inference_location"] == "actors"
    assert metrics["neural_evaluations"] > 0
    assert metrics["search_traversal_wall_seconds"] is None


def test_persistent_pool_rebinds_updated_weights_and_parent_inference():
    torch.manual_seed(42)
    model = PolicyValueNet("brandubh", 4, 0).eval()
    config = settings(games=2, max_game_plies=4)

    class ParentBackend:
        def __init__(self):
            self.model = model
            self.device = torch.device("cpu")
            self.info = {}
            self.calls = 0

        def forward(self, batch):
            self.calls += 1
            return model(batch)

    with SearchPool(2) as pool:
        for backend in (None, ParentBackend()):
            with torch.no_grad():
                next(model.parameters()).add_(0.02)
            expected, _ = generate_self_play(model, config, seed=19)
            actual, metrics = generate_self_play(
                model, {**config, "search_workers": 2}, seed=19, search_pool=pool, backend=backend
            )
            assert_games_equal(actual, expected)
            if backend is not None:
                assert backend.calls > 0
                assert metrics["inference_location"] == "parent"
        assert not pool.closed
    assert all(not process.is_alive() for process in pool.processes)


def test_worker_count_bounds_cpu_parallelism_not_game_concurrency():
    torch.manual_seed(48)
    model = PolicyValueNet("brandubh", 4, 0).eval()
    config = settings(
        games=8,
        max_game_plies=10,
        mcts_iterations=4,
        self_play_concurrency=8,
        inference_batch_size=8,
        search_workers=2,
    )
    sizes = []

    class Recording:
        def __init__(self):
            self.model = model
            self.device = torch.device("cpu")
            self.info = {}

        def forward(self, batch):
            sizes.append(int(batch.shape[0]))
            return model(batch)

    expected, _ = generate_self_play(model, config, seed=29)
    with SearchPool(2) as pool:
        actual, metrics = generate_self_play(
            model, config, seed=29, search_pool=pool, backend=Recording()
        )
    assert_games_equal(actual, expected)
    assert metrics["inference_location"] == "parent"
    assert metrics["search_worker_count"] == 2
    assert metrics["search_concurrency"] == 8
    assert metrics["search_games_per_worker"] == 4
    # Two workers still advance four games each, so a single request carries
    # more than one game and the old two-game ceiling cannot come back.
    assert max(sizes) >= 4
    assert metrics["inference_batch_size_max"] >= 4
    assert metrics["inference_batch_size_average"] > 2


def test_planned_batches_never_mix_up_requesters():
    groups = {"a": ["a0", "a1", "a2", "a3", "a4"], "b": ["b0", "b1", "b2"], "c": ["c0"]}
    remaining = {key: list(states) for key, states in groups.items()}
    covered: dict[str, list[str]] = {key: [] for key in groups}
    # Deliberately not the order the requests were issued in.
    order = ["c", "a", "b"]
    for batch, spans in plan_batches(groups, order, 4):
        assert 0 < len(batch) <= 4
        assert sum(size for _, size in spans) == len(batch)
        offset = 0
        for key, size in spans:
            piece = batch[offset : offset + size]
            assert piece == remaining[key][:size]
            remaining[key] = remaining[key][size:]
            covered[key].extend(piece)
            offset += size
    assert covered == groups
    assert all(not states for states in remaining.values())


@pytest.mark.parametrize("ruleset", ["brandubh", "tablut", "copenhagen"])
def test_recorded_targets_replay_as_legal_trajectories(ruleset):
    torch.manual_seed(49)
    model = PolicyValueNet(ruleset, 4, 0).eval()
    config = settings(ruleset=ruleset, games=4, max_game_plies=6)
    games, metrics = generate_self_play(model, {**config, "search_workers": 2}, seed=13)
    assert metrics["inference_location"] == "actors"
    assert metrics["search_deadline_cutoffs"] == 0
    for index, (examples, result) in enumerate(games):
        state = new_game(ruleset)
        for ply, move in enumerate(played_moves(examples, config, 13 + index)):
            assert move in legal_moves(state)
            assert torch.equal(examples[ply]["input"], encode_state(state))
            state = apply_move(state, move)
        assert result["plies"] == len(examples)
        assert (result["winner"] is None) == (state.winner is None)
        assert result["reason"] in ("training_ply_limit", state.reason)


def test_repeated_parallel_runs_are_identical():
    torch.manual_seed(53)
    model = PolicyValueNet("brandubh", 4, 0).eval()
    config = settings(games=6, max_game_plies=8, mcts_iterations=6, self_play_concurrency=6)
    digests = [
        game_digests(generate_self_play(model, {**config, "search_workers": 2}, seed=31)[0])
        for _ in range(3)
    ]
    assert digests[0] == digests[1] == digests[2]


def test_replay_window_bounds_uncommitted_games():
    torch.manual_seed(54)
    model = PolicyValueNet("brandubh", 4, 0).eval()
    config = settings(
        games=12,
        max_game_plies=6,
        mcts_iterations=4,
        self_play_concurrency=2,
        inference_batch_size=2,
    )
    commits = []
    uncommitted = []

    def on_game(index, *_):
        commits.append(index)

    def progress(info):
        # Games received but not yet committed must stay inside the window.
        uncommitted.append(info["game"] - len(commits))

    games, metrics = generate_self_play(
        model,
        {**config, "search_workers": 2},
        seed=37,
        retain_examples=False,
        on_game=on_game,
        progress=progress,
    )
    assert commits == list(range(12))
    assert metrics["search_replay_window"] == 4
    assert metrics["search_concurrency"] == 2
    # The pool may start at most ``window`` games ahead of the committed prefix.
    assert max(uncommitted) <= 4
    # No examples are requested, but every game still reports its outcome.
    assert all(examples == [] for examples, _ in games)
    assert all(result["outcome"] in ("completed", "truncated") for _, result in games)
    assert all(result["plies"] > 0 for _, result in games)


def test_uneven_games_finish_and_commit_in_order():
    torch.manual_seed(55)
    model = PolicyValueNet("brandubh", 4, 0).eval()
    config = settings(
        games=8,
        max_game_plies=40,
        mcts_iterations=2,
        self_play_concurrency=3,
        inference_batch_size=3,
    )
    commits = []
    games, metrics = generate_self_play(
        model,
        {**config, "search_workers": 2},
        seed=41,
        on_game=lambda index, *_: commits.append(index),
    )
    assert commits == list(range(8))
    assert len({result["plies"] for _, result in games}) > 1
    assert metrics["search_deadline_cutoffs"] == 0
    assert metrics["games"] == 8


def test_multi_worker_completion_routes_each_result_to_its_own_game():
    """A swapped or mis-grouped reply must change the trajectory, so equality proves routing."""
    torch.manual_seed(60)
    model = PolicyValueNet("brandubh", 4, 0).eval()
    config = settings(
        self_play_mode="batched",
        games=8,
        max_game_plies=8,
        mcts_iterations=4,
        self_play_concurrency=8,
        inference_batch_size=3,
        search_workers=4,
    )

    class Biased:
        """State-dependent outputs, so a mis-routed reply cannot stay invisible."""

        def __init__(self):
            self.model = model
            self.device = torch.device("cpu")
            self.info = {}

        def forward(self, batch):
            logits, values = self.model(batch)
            return logits + batch.sum(dim=(1, 2, 3)).unsqueeze(1) * 0.25, values

    expected, _ = generate_self_play(
        model, {**config, "search_workers": 0}, seed=61, backend=Biased()
    )
    with SearchPool(4) as pool:
        actual, metrics = generate_self_play(
            model, config, seed=61, search_pool=pool, backend=Biased()
        )
    assert_games_equal(actual, expected)
    assert metrics["inference_location"] == "parent"
    assert metrics["search_worker_count"] == 4
    assert metrics["inference_batch_size_max"] <= 3


def test_zero_merge_window_still_completes_every_game():
    """Without any merge window the parent must simply forward smaller batches."""
    torch.manual_seed(62)
    model = PolicyValueNet("brandubh", 4, 0).eval()
    config = settings(
        self_play_mode="batched",
        games=6,
        max_game_plies=6,
        mcts_iterations=4,
        self_play_concurrency=6,
        inference_batch_size=6,
        search_workers=2,
    )

    class Plain:
        def __init__(self):
            self.model = model
            self.device = torch.device("cpu")
            self.info = {}

        def forward(self, batch):
            return model(batch)

    expected, _ = generate_self_play(
        model, {**config, "search_workers": 0}, seed=67, backend=Plain()
    )
    with SearchPool(2, flush_seconds=0.0) as pool:
        actual, metrics = generate_self_play(
            model, config, seed=67, search_pool=pool, backend=Plain()
        )
    assert_games_equal(actual, expected)
    assert metrics["search_flush_seconds"] == 0.0
    assert metrics["inference_batch_size_max"] <= 6


def test_parallel_self_play_reports_pool_metrics():
    torch.manual_seed(63)
    model = PolicyValueNet("brandubh", 4, 0).eval()
    config = settings(games=4, max_game_plies=4, search_workers=2)
    _, metrics = generate_self_play(model, config, seed=71)
    for key in (
        "self_play_mode",
        "self_play_concurrency",
        "search_workers",
        "search_worker_count",
        "search_concurrency",
        "search_games_per_worker",
        "search_replay_window",
        "search_flush_seconds",
        "search_initialization_seconds",
        "inference_location",
        "models",
        "inference_batch_size_average",
        "neural_evaluations",
        "mcts_simulations",
        "search_deadline_cutoffs",
    ):
        assert key in metrics, key
    assert metrics["self_play_mode"] == "parallel"
    assert metrics["self_play_concurrency"] == 4
    assert metrics["search_workers"] == 2
    assert metrics["search_worker_count"] == 2
    assert metrics["search_concurrency"] == 4
    assert metrics["inference_location"] == "actors"
    assert len(metrics["models"]) == 1
    assert metrics["neural_evaluations"] > 0


@pytest.mark.parametrize("opponent", ["shield_bearer", "shield_maiden", "jarl", "champion"])
def test_parallel_evaluation_retains_paired_seeds_and_models(opponent):
    torch.manual_seed(43)
    model = PolicyValueNet("brandubh", 4, 0).eval()
    previous = deepcopy(model) if opponent == "champion" else None
    if previous is not None:
        with torch.no_grad():
            next(previous.parameters()).add_(0.1)
    config = settings(
        evaluation_opponent=opponent,
        evaluation_games=4,
        evaluation_jarl_depth=1,
        evaluation_jarl_time_limit=300,
    )
    expected = evaluate_model(model, config, previous=previous)
    actual = evaluate_model(model, {**config, "search_workers": 2}, previous=previous)
    assert [
        {key: row[key] for key in expected["games"][index]}
        for index, row in enumerate(actual["games"])
    ] == expected["games"]
    assert actual["opponent_identity"] == expected["opponent_identity"]
    assert actual["performance"]["mode"] == "parallel"


@pytest.mark.parametrize("cache", ["off", "exact"])
def test_champion_evaluation_keeps_model_queues_and_caches_separate(cache):
    torch.manual_seed(56)
    model = PolicyValueNet("brandubh", 4, 0).eval()
    previous = deepcopy(model)
    with torch.no_grad():
        next(previous.parameters()).add_(0.1)
    config = settings(
        evaluation_opponent="champion",
        evaluation_mode="batched",
        evaluation_games=4,
        evaluation_concurrency=4,
        evaluation_batch_size=4,
        inference_cache_mode=cache,
    )
    expected = evaluate_model(model, config, previous=previous)
    actual = evaluate_model(model, {**config, "search_workers": 2}, previous=previous)
    assert [
        {key: row[key] for key in expected["games"][index]}
        for index, row in enumerate(actual["games"])
    ] == expected["games"]
    metrics = actual["performance"]["models"]
    assert len(metrics) == 2
    assert all(entry["neural_evaluations"] > 0 for entry in metrics)
    if cache == "exact":
        assert all(entry["inference_cache_entries"] > 0 for entry in metrics)
    else:
        assert all(entry["inference_cache_entries"] == 0 for entry in metrics)


def test_actor_local_and_parent_inference_agree():
    torch.manual_seed(57)
    model = PolicyValueNet("brandubh", 4, 0).eval()
    config = settings(games=4, max_game_plies=6, inference_cache_mode="exact", search_workers=2)
    reference, _ = generate_self_play(model, {**config, "search_workers": 0}, seed=43)
    with SearchPool(2, inference="actors") as actors:
        local, local_metrics = generate_self_play(model, config, seed=43, search_pool=actors)
    with SearchPool(2, inference="parent") as parent:
        remote, remote_metrics = generate_self_play(model, config, seed=43, search_pool=parent)
    assert local_metrics["inference_location"] == "actors"
    assert remote_metrics["inference_location"] == "parent"
    assert_games_equal(local, reference)
    assert_games_equal(remote, reference)


def test_cancellation_and_crash_release_all_processes():
    model = PolicyValueNet("brandubh", 4, 0).eval()
    config = settings(search_workers=2)
    stopped = False

    def progress(info):
        nonlocal stopped
        if info["game"]:
            stopped = True

    pool = SearchPool(2)
    with pytest.raises(InterruptedError):
        generate_self_play(
            model, config, seed=23, progress=progress, should_stop=lambda: stopped, search_pool=pool
        )
    assert pool.closed and all(not process.is_alive() for process in pool.processes)
    pool = SearchPool(2)
    # Workers are created on demand, so ask for the two this phase can use.
    assert pool.processes == [] and pool._ensure(2) == 2
    pool.processes[0].terminate()
    pool.processes[0].join()
    with pytest.raises(RuntimeError):
        list(pool.games("self_play", model, config, seed=23))
    assert pool.closed and all(not process.is_alive() for process in pool.processes)


def test_actor_exception_surfaces_and_releases_pool():
    model = PolicyValueNet("brandubh", 4, 0).eval()
    pool = SearchPool(2)
    with pytest.raises(RuntimeError, match="failed"):
        list(pool.games("evaluation", model, settings(), seed=5, context={}))
    assert pool.closed and all(not process.is_alive() for process in pool.processes)


def test_more_workers_than_games_still_completes():
    torch.manual_seed(58)
    model = PolicyValueNet("brandubh", 4, 0).eval()
    config = settings(games=2, max_game_plies=4, self_play_concurrency=2)
    expected, _ = generate_self_play(model, config, seed=47)
    actual, metrics = generate_self_play(model, {**config, "search_workers": 8}, seed=47)
    assert_games_equal(actual, expected)
    assert metrics["search_worker_count"] == 2


class RecordingMonitor:
    """Minimal ProgressSink that records the counters the pool reports."""

    def __init__(self):
        self.phases: list[str] = []
        self.events: list[tuple[str, dict]] = []
        self.counters: dict[str, float] = {}

    def phase(self, name, *, iteration=None):
        self.phases.append(name)

    def tick(self, **deltas):
        self.events.append(("tick", dict(deltas)))
        for key, value in deltas.items():
            self.counters[key] = self.counters.get(key, 0) + value

    def counts(self, **values):
        self.events.append(("counts", dict(values)))
        self.counters.update(values)

    def peak_active(self) -> float:
        return max(
            (values.get("games_active", 0) for kind, values in self.events if kind == "counts"),
            default=0,
        )

    def first_event(self, key: str) -> int | None:
        for position, (kind, values) in enumerate(self.events):
            if kind == "tick" and key in values:
                return position
        return None


class RecordingConnection:
    def __init__(self):
        self.sent: list[tuple] = []

    def send(self, message):
        self.sent.append(message)


def test_actor_progress_batches_messages_and_stays_silent_when_disabled():
    connection = RecordingConnection()
    progress = _ActorProgress(connection, True)
    for _ in range(1000):
        progress.tick(positions=1, simulations=8)
    progress.flush()
    progress.flush()  # A second flush with nothing new must not resend.
    assert connection.sent, "enabled progress must report"
    assert len(connection.sent) <= 9000 // PROGRESS_DELTAS + 5, "progress must be batched"
    assert sum(message[2].get("positions", 0) for message in connection.sent) == 1000
    assert sum(message[2].get("simulations", 0) for message in connection.sent) == 8000
    assert [message[1] for message in connection.sent] == list(range(1, len(connection.sent) + 1))
    assert all(message[0] == "progress" for message in connection.sent)

    silent = RecordingConnection()
    disabled = _ActorProgress(silent, False)
    for _ in range(100):
        disabled.tick(positions=1)
    disabled.note_active(4)
    disabled.flush()
    assert silent.sent == []


def test_parallel_self_play_reports_exact_progress_without_double_counting():
    torch.manual_seed(90)
    model = PolicyValueNet("brandubh", 4, 0).eval()
    config = settings(
        games=6,
        max_game_plies=8,
        mcts_iterations=4,
        self_play_concurrency=3,
        inference_batch_size=3,
        search_workers=2,
    )
    monitor = RecordingMonitor()
    games, _ = generate_self_play(model, config, seed=97, monitor=monitor)
    assert monitor.counters["games_total"] == 6
    assert monitor.counters["games_completed"] == 6
    # Positions and simulations arrive incrementally from the actors, so the
    # completion must not add them a second time.
    assert monitor.counters["positions"] == sum(result["plies"] for _, result in games)
    assert monitor.counters["simulations"] == sum(result["simulations"] for _, result in games)
    assert monitor.counters["games_active"] == 0


def test_progress_arrives_before_the_first_game_finishes():
    torch.manual_seed(91)
    model = PolicyValueNet("brandubh", 4, 0).eval()
    config = settings(
        games=2,
        max_game_plies=30,
        mcts_iterations=16,
        self_play_concurrency=2,
        inference_batch_size=2,
        search_workers=2,
    )
    monitor = RecordingMonitor()
    generate_self_play(model, config, seed=101, monitor=monitor)
    first_positions = monitor.first_event("positions")
    first_simulation = monitor.first_event("simulations")
    first_completion = monitor.first_event("games_completed")
    assert first_positions is not None and first_simulation is not None
    assert first_completion is not None
    # A long game must advance the dashboard long before it ends.
    assert first_positions < first_completion
    assert first_simulation < first_completion


def test_progress_survives_multiple_granted_slices_per_worker():
    """Each granted run restarts the actor's sequence, so nothing may be dropped."""
    torch.manual_seed(99)
    model = PolicyValueNet("brandubh", 4, 0).eval()
    config = settings(
        games=12,
        max_game_plies=6,
        mcts_iterations=4,
        self_play_concurrency=2,
        inference_batch_size=2,
        search_workers=2,
    )
    monitor = RecordingMonitor()
    games, metrics = generate_self_play(
        model, config, seed=119, retain_examples=False, monitor=monitor
    )
    assert metrics["search_replay_window"] == 4
    assert metrics["search_worker_count"] == 2
    assert metrics["search_progress_messages"] > 0
    assert monitor.counters["games_completed"] == 12
    assert monitor.counters["positions"] == sum(result["plies"] for _, result in games)
    assert monitor.counters["simulations"] == sum(result["simulations"] for _, result in games)
    assert monitor.counters["games_active"] == 0


@pytest.mark.parametrize("workers,concurrency", [(1, 4), (2, 4)])
def test_active_games_reflect_concurrent_contexts(workers, concurrency):
    torch.manual_seed(92)
    model = PolicyValueNet("brandubh", 4, 0).eval()
    config = settings(
        games=4,
        max_game_plies=8,
        mcts_iterations=4,
        self_play_concurrency=concurrency,
        inference_batch_size=concurrency,
        search_workers=workers,
    )
    monitor = RecordingMonitor()
    generate_self_play(model, config, seed=103, monitor=monitor)
    assert monitor.peak_active() == concurrency
    assert monitor.counters["games_active"] == 0


def test_monitoring_does_not_change_self_play_results():
    torch.manual_seed(93)
    model = PolicyValueNet("brandubh", 4, 0).eval()
    config = settings(
        games=4,
        max_game_plies=8,
        mcts_iterations=4,
        self_play_concurrency=2,
        inference_batch_size=2,
        search_workers=2,
    )
    expected, _ = generate_self_play(model, config, seed=107)
    actual, _ = generate_self_play(model, config, seed=107, monitor=RecordingMonitor())
    assert_games_equal(actual, expected)


def test_pool_reuse_keeps_progress_monotonic():
    torch.manual_seed(94)
    model = PolicyValueNet("brandubh", 4, 0).eval()
    config = settings(
        games=3,
        max_game_plies=6,
        mcts_iterations=4,
        self_play_concurrency=2,
        inference_batch_size=2,
        search_workers=2,
    )
    monitor = RecordingMonitor()
    with SearchPool(2) as pool:
        first, _ = generate_self_play(model, config, seed=109, search_pool=pool, monitor=monitor)
        after_first = dict(monitor.counters)
        second, _ = generate_self_play(model, config, seed=111, search_pool=pool, monitor=monitor)
    assert after_first["games_completed"] == 3
    assert monitor.counters["games_completed"] == 6
    assert monitor.counters["positions"] == sum(result["plies"] for _, result in (*first, *second))
    assert monitor.counters["games_active"] == 0


def test_cancelled_phase_does_not_leave_active_games():
    torch.manual_seed(95)
    model = PolicyValueNet("brandubh", 4, 0).eval()
    config = settings(
        games=12,
        max_game_plies=6,
        mcts_iterations=4,
        self_play_concurrency=3,
        inference_batch_size=3,
        search_workers=2,
    )
    monitor = RecordingMonitor()
    stopped = False

    def progress(info):
        nonlocal stopped
        if info["game"] >= 2:
            stopped = True

    with pytest.raises(InterruptedError):
        generate_self_play(
            model,
            config,
            seed=113,
            monitor=monitor,
            progress=progress,
            should_stop=lambda: stopped,
        )
    assert monitor.counters["games_completed"] >= 1
    assert monitor.counters["games_active"] == 0


def test_training_reports_live_parallel_self_play_progress(tmp_path):
    torch.manual_seed(96)
    records: list[dict] = []
    config = settings(
        search_workers=2,
        games=4,
        max_game_plies=4,
        mcts_iterations=2,
        self_play_concurrency=2,
        inference_batch_size=2,
        evaluation_games=2,
        evaluation_max_plies=2,
        evaluation_iterations=2,
        iterations=1,
        output_dir=str(tmp_path / "live"),
        registry_dir=str(tmp_path / "models"),
    )
    train(config, progress=lambda info: records.append(info))
    self_play = [
        record["phase_progress"]
        for record in records
        if record.get("stage") == "self_play" and record.get("phase_progress")
    ]
    assert self_play, "the self-play phase must publish live counters"
    final = self_play[-1]
    assert final["games_total"] == 4
    assert final["games_completed"] == 4
    assert final["positions"] > 0
    assert final["simulations"] > 0


@pytest.mark.parametrize(
    "configured,self_play_concurrency,evaluation_concurrency,expected",
    [
        (32, 2, 2, (2, 2)),
        (8, 1, 4, (1, 4)),
        (4, 64, 64, (4, 4)),
        (4, 1, 1, (1, 1)),
        (8, 3, 6, (3, 6)),
    ],
)
def test_only_useful_workers_are_created(
    configured, self_play_concurrency, evaluation_concurrency, expected
):
    torch.manual_seed(97)
    model = PolicyValueNet("brandubh", 4, 0).eval()
    self_play_config = settings(
        search_workers=configured,
        games=8,
        max_game_plies=2,
        mcts_iterations=1,
        self_play_concurrency=self_play_concurrency,
        inference_batch_size=2,
    )
    evaluation_config = settings(
        search_workers=configured,
        evaluation_games=8,
        evaluation_max_plies=2,
        evaluation_iterations=1,
        evaluation_concurrency=evaluation_concurrency,
        evaluation_batch_size=2,
    )
    with SearchPool(configured) as pool:
        assert pool.processes == [], "constructing a pool must not start processes"
        generate_self_play(model, self_play_config, seed=115, search_pool=pool)
        assert len(pool.processes) == expected[0]
        created = [id(process) for process in pool.processes]
        evaluate_model(model, evaluation_config, search_pool=pool)
        assert len(pool.processes) == expected[1]
        # Workers are reused, never recreated, when a later phase needs more.
        assert created == [id(process) for process in pool.processes][: len(created)]
        assert all(process.is_alive() for process in pool.processes)
        assert len(pool.processes) <= configured
    assert all(not process.is_alive() for process in pool.processes)


def test_sequential_modes_never_start_pool_processes():
    torch.manual_seed(98)
    model = PolicyValueNet("brandubh", 4, 0).eval()
    config = settings(
        self_play_mode="sequential",
        search_workers=4,
        games=2,
        max_game_plies=4,
        mcts_iterations=2,
    )
    with SearchPool(4) as pool:
        games, _ = generate_self_play(model, config, seed=117, search_pool=pool)
        assert len(games) == 2
        assert pool.processes == []
        assert pool.initialization_seconds == 0.0


def test_pool_growth_is_capped_and_closed_on_shutdown():
    pool = SearchPool(3)
    assert pool._ensure(2) == 2
    assert pool._ensure(1) == 2, "an existing pool never shrinks"
    assert pool._ensure(3) == 3
    assert pool._ensure(9) == 3, "the configured maximum is a hard cap"
    assert len(pool.connections) == 3
    pool.close()
    assert pool.closed
    assert all(not process.is_alive() for process in pool.processes)
    with pytest.raises(RuntimeError):
        pool._ensure(1)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"workers": 0},
        {"workers": 33},
        {"workers": True},
        {"inference": "gpu"},
        {"flush_seconds": -1.0},
        {"flush_seconds": 1.0},
    ],
)
def test_pool_option_validation(kwargs):
    workers = kwargs.pop("workers", 2)
    with pytest.raises(ValueError):
        SearchPool(workers, **kwargs)


def test_disabled_worker_count_never_creates_processes(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("search_workers=0 must keep the in-process scheduler")

    monkeypatch.setattr("hnefatafl.parallel_search.SearchPool", forbidden)
    torch.manual_seed(59)
    model = PolicyValueNet("brandubh", 4, 0).eval()
    games, metrics = generate_self_play(model, settings(self_play_mode="batched"), seed=53)
    assert len(games) == 4 and metrics["self_play_mode"] == "batched"


def test_parallel_real_update_and_full_state_resume(tmp_path):
    config = settings(
        search_workers=2,
        games=2,
        max_game_plies=2,
        evaluation_max_plies=2,
        output_dir=str(tmp_path / "first"),
        registry_dir=str(tmp_path / "models"),
    )
    first = train(config)
    assert first["metrics"]["updates"] > 0
    resumed = train(
        {
            **config,
            "resume": first["checkpoint"],
            "iterations": 2,
            "output_dir": str(tmp_path / "resumed"),
        }
    )
    _, saved = load_checkpoint(resumed["checkpoint"])
    assert saved["extra"]["iteration"] == 2 and saved["optimizer_state"]
    assert saved["metadata"]["training_config"]["search_workers"] == 2
    uninterrupted = train({**config, "iterations": 2, "output_dir": str(tmp_path / "full")})
    _, full = load_checkpoint(uninterrupted["checkpoint"])
    assert all(
        torch.equal(tensor, full["state_dict"][key]) for key, tensor in saved["state_dict"].items()
    )


@pytest.mark.parametrize("value", [-1, 33, True, 1.5])
def test_worker_count_validation(value):
    with pytest.raises(ValueError):
        normalize_config({"search_workers": value})


def test_legacy_workers_can_claim_disabled_but_not_parallel_jobs():
    from hnefatafl.remote_training import worker_compatible

    config = settings(training_symmetry_mode="off")
    assert worker_compatible(config, {})
    assert not worker_compatible({**config, "search_workers": 2}, {})
    assert worker_compatible({**config, "search_workers": 2}, {"parallel_search": True})


def test_explicit_sequential_evaluation_bypasses_process_pool(monkeypatch):
    def forbidden(*args):
        raise AssertionError("Explicit sequential evaluation must not create actors")

    monkeypatch.setattr("hnefatafl.parallel_search.SearchPool", forbidden)
    model = PolicyValueNet("brandubh", 4, 0).eval()
    config = {**settings(search_workers=2), "evaluation_mode": "sequential"}
    result = evaluate_model(model, config)
    assert result["game_count"] == 2 and "performance" not in result


@pytest.mark.skipif(not torch.cuda.is_available(), reason="CUDA unavailable")
def test_cuda_models_stay_in_parent():
    model = PolicyValueNet("brandubh", 4, 0).cuda().eval()
    config = settings(search_workers=2, games=2, max_game_plies=2)
    with SearchPool(2) as pool:
        snapshots, _, actors = pool._open_phase(
            ExitStack(), "self_play", [model], config, None, 2, 2, 2
        )[1:]
        assert snapshots == [] and actors is False
    _, metrics = generate_self_play(model, config, seed=17)
    assert metrics["inference_location"] == "parent"
    assert metrics["neural_evaluations"] > 0


class FakeBackend:
    """Parent-owned inference backend that records its lifecycle."""

    def __init__(self, model, name="backend", *, fail_refresh=False):
        self.model = model
        self.name = name
        self.device = next(model.parameters()).device
        self.info = {"backend": name}
        self.refreshes = 0
        self.closed = 0
        self.fail_refresh = fail_refresh
        self.fail_close = False

    def refresh(self):
        self.refreshes += 1
        if self.fail_refresh:
            raise RuntimeError(f"refresh failed for {self.name}")
        return {}

    def forward(self, batch):
        return self.model(batch)

    def close(self):
        self.closed += 1
        if self.fail_close:
            raise RuntimeError(f"close failed for {self.name}")


def install_backends(monkeypatch, *, fail_create=None, fail_refresh=(), on_create=None):
    """Replace OpenVINO backend creation with lifecycle-recording fakes."""
    created: list[FakeBackend] = []

    def create(model, config):
        index = len(created)
        if index == fail_create:
            raise RuntimeError(f"create failed for backend {index}")
        backend = FakeBackend(model, name=f"backend{index}", fail_refresh=index in fail_refresh)
        created.append(backend)
        if on_create is not None:
            on_create(index, backend)
        return backend

    monkeypatch.setattr("hnefatafl.inference_backends.create_inference", create)
    return created


def champion_pair(seed=56):
    torch.manual_seed(seed)
    model = PolicyValueNet("brandubh", 4, 0).eval()
    previous = deepcopy(model)
    with torch.no_grad():
        next(previous.parameters()).add_(0.1)
    return model, previous


def openvino_champion(**changes):
    return settings(
        evaluation_opponent="champion",
        evaluation_mode="batched",
        evaluation_games=4,
        evaluation_concurrency=4,
        evaluation_batch_size=4,
        evaluation_inference_backend="training",
        inference_backend="openvino",
        **changes,
    )


def test_champion_evaluation_closes_both_owned_backends(monkeypatch):
    model, previous = champion_pair()
    config = openvino_champion(search_workers=2)
    plain = {
        **config,
        "search_workers": 0,
        "evaluation_inference_backend": "pytorch",
        "inference_backend": "pytorch",
    }
    expected = evaluate_model(model, plain, previous=previous)
    created = install_backends(monkeypatch)
    actual = evaluate_model(model, config, previous=previous)
    assert [
        {key: row[key] for key in expected["games"][index]}
        for index, row in enumerate(actual["games"])
    ] == expected["games"]
    assert [backend.refreshes for backend in created] == [1, 1]
    assert [backend.closed for backend in created] == [1, 1]


def test_second_backend_creation_failure_closes_the_first(monkeypatch):
    model, previous = champion_pair()
    created = install_backends(monkeypatch, fail_create=1)
    with pytest.raises(RuntimeError, match="create failed for backend 1"):
        evaluate_model(model, openvino_champion(search_workers=2), previous=previous)
    assert len(created) == 1
    assert created[0].closed == 1


@pytest.mark.parametrize("failing", [0, 1])
def test_refresh_failure_closes_created_backends(monkeypatch, failing):
    model, previous = champion_pair()
    created = install_backends(monkeypatch, fail_refresh={failing})
    with pytest.raises(RuntimeError, match=f"refresh failed for backend{failing}"):
        evaluate_model(model, openvino_champion(search_workers=2), previous=previous)
    assert len(created) == failing + 1
    assert [backend.closed for backend in created] == [1] * (failing + 1)


def test_setup_failure_after_initialization_closes_owned_backends(monkeypatch):
    model, previous = champion_pair()
    created = install_backends(monkeypatch)

    def unavailable(*args, **kwargs):
        raise RuntimeError("metrics unavailable")

    monkeypatch.setattr("hnefatafl.parallel_search.InferenceMetrics", unavailable)
    with pytest.raises(RuntimeError, match="metrics unavailable"):
        evaluate_model(model, openvino_champion(search_workers=2), previous=previous)
    assert [backend.closed for backend in created] == [1, 1]


def test_cancellation_during_backend_initialization_closes_created_backends(monkeypatch):
    model, previous = champion_pair()
    state = {"stopped": False}
    created = install_backends(
        monkeypatch, on_create=lambda index, backend: state.__setitem__("stopped", True)
    )
    with pytest.raises(InterruptedError):
        evaluate_model(
            model,
            openvino_champion(search_workers=2),
            previous=previous,
            should_stop=lambda: state["stopped"],
        )
    assert [backend.closed for backend in created] == [1, 1]


def test_cleanup_failure_does_not_hide_the_primary_error(monkeypatch):
    model, previous = champion_pair()
    created = install_backends(
        monkeypatch,
        fail_create=1,
        on_create=lambda index, backend: setattr(backend, "fail_close", True),
    )
    with pytest.raises(RuntimeError, match="create failed for backend 1") as failure:
        evaluate_model(model, openvino_champion(search_workers=2), previous=previous)
    assert created[0].closed == 1
    assert any("close failed" in note for note in getattr(failure.value, "__notes__", []))


def test_external_backend_is_not_closed_by_the_pool():
    torch.manual_seed(64)
    model = PolicyValueNet("brandubh", 4, 0).eval()
    backend = FakeBackend(model)
    config = settings(games=2, max_game_plies=4, mcts_iterations=2, search_workers=2)
    games, metrics = generate_self_play(model, config, seed=91, backend=backend)
    assert len(games) == 2
    assert metrics["inference_location"] == "parent"
    assert backend.refreshes == 0
    assert backend.closed == 0


def test_champion_routing_distinguishes_candidate_from_incumbent(monkeypatch):
    torch.manual_seed(70)
    model = PolicyValueNet("brandubh", 4, 0).eval()
    previous = deepcopy(model)
    with torch.no_grad():
        for parameter in previous.parameters():
            parameter.mul_(0.5)
    config = openvino_champion(search_workers=2, evaluation_max_plies=20, evaluation_iterations=16)
    created = install_backends(monkeypatch)
    actual = evaluate_model(model, config, previous=previous)
    assert [backend.closed for backend in created] == [1, 1]
    plain = {
        **config,
        "search_workers": 0,
        "evaluation_inference_backend": "pytorch",
        "inference_backend": "pytorch",
    }
    expected = evaluate_model(model, plain, previous=previous)
    assert [
        {key: row[key] for key in expected["games"][index]}
        for index, row in enumerate(actual["games"])
    ] == expected["games"]
    # Swapping the two models must change the games, so the equality above cannot
    # be satisfied by routing the incumbent's requests to the candidate.
    swapped = evaluate_model(previous, plain, previous=model)
    assert swapped["games"] != expected["games"]


@pytest.mark.parametrize(
    "games,concurrency,workers",
    [(8, 1, 2), (8, 3, 2), (8, 10, 3), (64, 64, 3), (64, 64, 4), (8, 2, 8), (64, 8, 3)],
)
def test_configured_concurrency_is_a_hard_upper_bound(games, concurrency, workers):
    torch.manual_seed(71)
    model = PolicyValueNet("brandubh", 4, 0).eval()
    config = settings(
        games=games,
        max_game_plies=2,
        mcts_iterations=1,
        self_play_concurrency=concurrency,
        inference_batch_size=4,
        search_workers=workers,
    )
    expected = min(concurrency, games)
    with SearchPool(workers) as pool:
        played, metrics = generate_self_play(model, config, seed=73, search_pool=pool)
    assert len(played) == games
    capacities = metrics["search_worker_capacity"]
    peaks = metrics["search_worker_peak_active"]
    assert metrics["search_concurrency"] == expected
    assert sum(capacities) == expected
    assert len(capacities) == min(workers, expected)
    assert metrics["search_worker_count"] == len(capacities)
    assert max(peaks) > 0
    assert all(peak <= capacity for peak, capacity in zip(peaks, capacities))


def count_live_example_rows() -> int:
    # Traversing the object graph touches torch's lazy module attributes, which
    # emit an unrelated deprecation warning.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FutureWarning)
        return sum(
            1
            for obj in gc.get_objects()
            if isinstance(obj, dict)
            and "probabilities" in obj
            and "legal" in obj
            and "input" in obj
        )


def measure_peak_live_rows(games: int) -> tuple[int, int]:
    torch.manual_seed(74)
    model = PolicyValueNet("brandubh", 4, 0).eval()
    config = settings(
        games=games,
        max_game_plies=3,
        mcts_iterations=2,
        self_play_concurrency=4,
        inference_batch_size=4,
    )
    commits: list[int] = []
    peaks: list[int] = []
    games_out, _ = generate_self_play(
        model,
        {**config, "search_workers": 2},
        seed=75,
        retain_examples=False,
        on_game=lambda index, *_: commits.append(index),
        progress=lambda info: peaks.append(count_live_example_rows()),
    )
    assert commits == list(range(games))
    assert all(examples == [] for examples, _ in games_out)
    return max(peaks), games * 3


def test_committed_examples_are_released_and_retention_stays_bounded():
    small_peak, small_total = measure_peak_live_rows(16)
    large_peak, large_total = measure_peak_live_rows(96)
    assert 0 < small_peak <= small_total
    # Six times the games must not retain six times the examples.
    assert large_peak < large_total / 3
    assert large_peak <= small_peak + 16


def test_cancellation_releases_intermediate_examples():
    torch.manual_seed(76)
    model = PolicyValueNet("brandubh", 4, 0).eval()
    config = settings(
        games=32,
        max_game_plies=4,
        mcts_iterations=2,
        self_play_concurrency=4,
        inference_batch_size=4,
    )
    baseline = count_live_example_rows()
    stopped = False

    def progress(info):
        nonlocal stopped
        if info["game"] >= 4:
            stopped = True

    with pytest.raises(InterruptedError):
        generate_self_play(
            model,
            {**config, "search_workers": 2},
            seed=77,
            retain_examples=False,
            progress=progress,
            should_stop=lambda: stopped,
        )
    gc.collect()
    assert count_live_example_rows() <= baseline


def test_retained_examples_stay_complete():
    torch.manual_seed(78)
    model = PolicyValueNet("brandubh", 4, 0).eval()
    config = settings(
        games=6,
        max_game_plies=4,
        mcts_iterations=2,
        self_play_concurrency=3,
        inference_batch_size=3,
    )
    expected, _ = generate_self_play(model, {**config, "search_workers": 0}, seed=81)
    actual, _ = generate_self_play(model, {**config, "search_workers": 3}, seed=81)
    assert_games_equal(actual, expected)
    assert all(examples for examples, _ in actual)


@pytest.mark.parametrize(
    "mode,workers,expected",
    [
        ("sequential", 4, False),
        ("batched", 4, True),
        ("auto", 4, True),
        ("sequential", 0, False),
        ("batched", 0, False),
        ("auto", 0, False),
    ],
)
def test_self_play_pool_precedence(mode, workers, expected):
    assert self_play_uses_pool(settings(self_play_mode=mode, search_workers=workers)) is expected


@pytest.mark.parametrize(
    "mode,workers,expected",
    [("sequential", 4, False), ("batched", 4, True), ("auto", 4, True), ("auto", 0, False)],
)
def test_evaluation_pool_precedence(mode, workers, expected):
    assert evaluation_uses_pool(settings(evaluation_mode=mode, search_workers=workers)) is expected


class FakeDevice:
    def __init__(self, type_):
        self.type = type_


@pytest.mark.parametrize(
    "mode,backend,device,expected",
    [
        ("auto", "pytorch", "cuda", "batched"),
        ("auto", "openvino", "cpu", "batched"),
        ("auto", "pytorch", "cpu", "sequential"),
        ("auto", "pytorch", "mps", "sequential"),
        ("sequential", "openvino", "cuda", "sequential"),
        ("batched", "pytorch", "cpu", "batched"),
    ],
)
def test_auto_mode_resolution(mode, backend, device, expected):
    assert (
        self_play_execution_mode(
            {"self_play_mode": mode, "inference_backend": backend}, FakeDevice(device)
        )
        == expected
    )


@pytest.mark.parametrize("workers", [0, 2, 4])
@pytest.mark.parametrize("mode", ["sequential", "batched", "auto"])
def test_execution_mode_matches_configuration(mode, workers):
    torch.manual_seed(83)
    model = PolicyValueNet("brandubh", 4, 0).eval()
    config = settings(
        self_play_mode=mode,
        games=4,
        max_game_plies=4,
        mcts_iterations=2,
        search_workers=workers,
    )
    games, metrics = generate_self_play(model, config, seed=89)
    assert len(games) == 4
    if mode == "sequential":
        assert metrics["self_play_mode"] == "sequential"
    elif mode == "batched":
        assert metrics["self_play_mode"] == ("parallel" if workers else "batched")
    else:
        # auto batches on CUDA/OpenVINO, stays sequential on CPU, and uses the
        # pool whenever one is configured.
        assert metrics["self_play_mode"] == ("parallel" if workers else "sequential")


def test_explicit_sequential_self_play_never_uses_the_pool(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("explicit sequential self-play must not use the pool")

    monkeypatch.setattr("hnefatafl.parallel_search.parallel_self_play", forbidden)
    torch.manual_seed(84)
    model = PolicyValueNet("brandubh", 4, 0).eval()
    games, metrics = generate_self_play(
        model,
        settings(self_play_mode="sequential", search_workers=4, games=2, max_game_plies=4),
        seed=93,
    )
    assert len(games) == 2
    assert metrics["self_play_mode"] == "sequential"


def test_sequential_phases_do_not_start_workers(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError("a fully sequential configuration must not start workers")

    monkeypatch.setattr("hnefatafl.parallel_search.SearchPool", forbidden)
    result = train(
        settings(
            self_play_mode="sequential",
            evaluation_mode="sequential",
            search_workers=4,
            games=2,
            max_game_plies=2,
            evaluation_max_plies=2,
            output_dir=str(tmp_path / "sequential"),
            registry_dir=str(tmp_path / "models"),
        )
    )
    assert result["metrics"]["updates"] > 0


@pytest.mark.parametrize("mode", ["sequential", "batched", "auto"])
@pytest.mark.parametrize("workers", [0, 2])
def test_evaluation_mode_matches_configuration(mode, workers):
    torch.manual_seed(85)
    model = PolicyValueNet("brandubh", 4, 0).eval()
    config = settings(
        evaluation_mode=mode,
        evaluation_opponent="shield_bearer",
        evaluation_games=2,
        evaluation_max_plies=4,
        evaluation_iterations=2,
        search_workers=workers,
    )
    result = evaluate_model(model, config)
    assert result["game_count"] == 2
    reported = result.get("performance", {}).get("mode")
    assert (reported == "parallel") == (bool(workers) and mode != "sequential")
