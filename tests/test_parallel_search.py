"""Real spawned actors, decoupled concurrency, routing and failure cleanup."""

from copy import deepcopy
import random

import pytest
import torch

from hnefatafl.engine import Move, apply_move, legal_moves, new_game
from hnefatafl.models import PolicyValueNet, encode_state, load_checkpoint
from hnefatafl.parallel_search import SearchPool, plan_batches
from hnefatafl.training import generate_self_play, normalize_config, train
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
            "self_play_mode": "sequential",
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
    seen = []
    games, metrics = generate_self_play(
        model,
        {**config, "search_workers": 2},
        seed=37,
        retain_examples=False,
        on_game=lambda index, *_: commits.append(index),
        progress=lambda info: seen.append(info["game"]),
    )
    assert commits == list(range(12))
    assert [result["plies"] for _, result in games] == [result["plies"] for _, result in games]
    assert metrics["search_replay_window"] == 4
    assert metrics["search_concurrency"] == 2
    assert max(seen) == 12


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
        _, owned, snapshots, _, actors = pool._open_phase(
            "self_play", [model], config, None, 2, 2, 2
        )
        assert snapshots == [] and actors is False and owned == []
    _, metrics = generate_self_play(model, config, seed=17)
    assert metrics["inference_location"] == "parent"
    assert metrics["neural_evaluations"] > 0
