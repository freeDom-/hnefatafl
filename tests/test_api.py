"""API contracts, durable replay and optimistic concurrency using real game rules."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
from auth_support import AuthenticatedClient as TestClient

from hnefatafl.engine import State
from hnefatafl.server import create_app
from hnefatafl.storage import ConflictError, Store


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as session:
        yield session


def create(client, **configuration):
    response = client.post("/api/games", json=configuration)
    assert response.status_code == 201, response.text
    return response.json()


def move(client, game, selected=None):
    response = client.post(
        f"/api/games/{game['id']}/moves",
        json={**(selected or game["legal_moves"][0]), "version": game["version"]},
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize(
    "ruleset,size,turn",
    [("tablut", 9, "defenders"), ("brandubh", 7, "attackers"), ("copenhagen", 11, "attackers")],
)
def test_gameplay_legal_illegal_moves_and_optimistic_version(client, ruleset, size, turn):
    game = create(client, ruleset=ruleset)
    assert len(game["board"]) == size**2
    assert game["turn"] == turn
    assert game["legal_moves"]
    original = game
    illegal = client.post(
        f"/api/games/{game['id']}/moves", json={"source": 0, "target": size**2 + 1, "version": 0}
    )
    assert illegal.status_code == 400
    assert client.get(f"/api/games/{game['id']}").json()["version"] == 0
    game = move(client, game)
    assert game["ply"] == 1 and game["version"] == 1
    source = original["legal_moves"][0]["source"]
    assert game["moves"][0]["notation"].startswith(
        f"{chr(97 + source % size)}{size - source // size} → "
    )
    assert game["turn"] != turn
    stale = client.post(
        f"/api/games/{game['id']}/moves", json={**original["legal_moves"][0], "version": 0}
    )
    assert stale.status_code == 409
    assert client.get(f"/api/games/{game['id']}").json()["board"] == game["board"]


def test_version_is_required_and_strict(client):
    game = create(client)
    path = f"/api/games/{game['id']}/moves"
    assert client.post(path, json=game["legal_moves"][0]).status_code == 422
    assert client.post(path, json={**game["legal_moves"][0], "version": True}).status_code == 422
    assert client.get("/api/games/nonexistent").status_code == 404


def test_undo_redo_seek_and_branching_history(client):
    game = create(client, ruleset="brandubh")
    initial = game["board"]
    game = move(client, game)
    first = game["board"]
    game = move(client, game)
    second = game["board"]
    path = f"/api/games/{game['id']}"
    game = client.post(path + "/undo", json={"version": game["version"]}).json()
    assert game["board"] == first and game["cursor"] == 1 and game["total_moves"] == 2
    game = client.post(path + "/redo", json={"version": game["version"]}).json()
    assert game["board"] == second
    game = client.post(path + "/seek", json={"version": game["version"], "cursor": 0}).json()
    assert game["board"] == initial
    assert (
        client.post(path + "/seek", json={"version": game["version"], "cursor": 999}).status_code
        == 400
    )
    game = move(client, game, game["legal_moves"][-1])
    assert game["total_moves"] == 1 and game["cursor"] == 1
    assert client.post(path + "/redo", json={"version": game["version"]}).status_code == 400
    validated = client.get(path + "/replay")
    assert validated.status_code == 200
    assert validated.json()["validated"] is True
    assert validated.json()["states"][-1]["board"] == game["board"]
    restarted = client.post(path + "/restart", json={"version": game["version"]}).json()
    assert restarted["board"] == initial and restarted["total_moves"] == 0
    assert restarted["id"] != game["id"] and restarted["version"] == 0
    assert client.get(path).json()["moves"] == game["moves"]


def test_games_and_profiles_survive_restart(tmp_path):
    app = create_app(tmp_path)
    with TestClient(app) as first:
        profile = first.post("/api/players", json={"name": "Freya"}).json()
        game = create(
            first,
            ruleset="tablut",
            players={"defenders": {"player_id": profile["id"], "name": "ignored"}},
        )
        game = move(first, game)
        assert game["players"]["defenders"]["name"] == "Freya"
    with TestClient(create_app(tmp_path)) as second:
        saved = second.get(f"/api/games/{game['id']}").json()
        assert saved == game
        assert any(p["id"] == profile["id"] for p in second.get("/api/players").json())
        replayed = second.get(f"/api/games/{game['id']}/replay").json()
        assert replayed["validated"] and replayed["states"][-1]["board"] == game["board"]


def test_sql_compare_and_swap_allows_only_one_writer(tmp_path):
    store = Store(tmp_path / "race.sqlite3")
    game = {"id": "race", "version": 0, "created_at": "today", "value": 0}
    store.create_game(game)

    def writer(value):
        candidate = {**game, "value": value}
        try:
            store.update_game(candidate, 0)
            return "updated"
        except ConflictError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(writer, [1, 2]))
    assert sorted(results) == ["conflict", "updated"]
    saved = store.get_game("race")
    assert saved["version"] == 1 and saved["value"] in (1, 2)


@pytest.mark.parametrize("algorithm", ["baseline", "alpha_beta", "mcts"])
def test_computer_moves_use_real_search_and_respect_turns(client, algorithm):
    game = create(
        client,
        ruleset="brandubh",
        mode="human-ai",
        human_side="defenders",
        players={
            "attackers": {
                "profile_id": algorithm,
                "config": {"time_limit": 0.03, "depth": 1, "iterations": 2, "seed": 3},
            }
        },
    )
    path = f"/api/games/{game['id']}"
    assert (
        client.post(path + "/moves", json={**game["legal_moves"][0], "version": 0}).status_code
        == 409
    )
    computer = client.post(path + "/ai-move", json={"version": 0})
    assert computer.status_code == 200, computer.text
    game = computer.json()
    assert game["ply"] == 1 and game["turn"] == "defenders"
    assert client.post(path + "/ai-move", json={"version": game["version"]}).status_code == 409
    move(client, game)


def test_ai_configuration_rejects_paths_and_incompatible_types(client):
    for config in [
        {"model_path": "/etc/passwd"},
        {"depth": -1},
        {"depth": True},
        {"depth": 1.5},
        {"algorithm": []},
        {"time_limit": 1000},
    ]:
        response = client.post(
            "/api/games", json={"mode": "ai-ai", "players": {"attackers": {"config": config}}}
        )
        assert response.status_code == 400
    assert client.post("/api/games", json={"ruleset": "unknown"}).status_code == 422
    assert client.post("/api/players", json={"name": "   "}).status_code == 400


def test_alpha_beta_search_config_reaches_the_stored_game(client):
    search = {
        "legacy": False,
        "transposition": True,
        "move_ordering": True,
        "symmetry_transposition": True,
        "symmetry_pruning": True,
        "table_capacity": 4096,
    }
    game = create(
        client,
        ruleset="brandubh",
        mode="ai-ai",
        players={
            "attackers": {
                "profile_id": "alpha_beta",
                "config": {"algorithm": "alpha_beta", "depth": 1, "time_limit": 0.05},
            },
            "defenders": {
                "config": {
                    "algorithm": "alpha_beta",
                    "depth": 1,
                    "time_limit": 0.05,
                    "search": search,
                }
            },
        },
    )
    attackers = game["players"]["attackers"]["config"]
    defenders = game["players"]["defenders"]["config"]
    assert "search" not in attackers
    assert defenders["search"] == search
    # Distinct search settings are distinct agents.
    assert game["players"]["attackers"]["variant_id"] != game["players"]["defenders"]["variant_id"]


@pytest.mark.parametrize(
    "search",
    [
        {"unknown": True},
        {"transposition": 1},
        {"move_ordering": "yes"},
        {"table_capacity": 0},
        {"table_capacity": True},
        {"symmetry_pruning": True, "table_capacity": 1.5},
    ],
)
def test_malformed_alpha_beta_search_config_is_rejected(client, search):
    response = client.post(
        "/api/games",
        json={
            "mode": "ai-ai",
            "players": {
                "attackers": {
                    "config": {
                        "algorithm": "alpha_beta",
                        "depth": 1,
                        "search": search,
                    }
                }
            },
        },
    )
    assert response.status_code == 400, response.text


def test_search_config_is_rejected_for_non_alpha_beta_algorithms(client):
    for algorithm in ("baseline", "shield_maiden", "mcts"):
        response = client.post(
            "/api/games",
            json={
                "mode": "ai-ai",
                "players": {
                    "attackers": {"config": {"algorithm": algorithm, "search": {"legacy": True}}}
                },
            },
        )
        assert response.status_code == 400, response.text


def test_statistics_are_separated_by_identity_kind_ruleset_and_mode(client):
    profile = client.post("/api/players", json={"name": "Astrid"}).json()
    # Seed terminal fixtures into storage to isolate counting from search strength;
    # game/replay legality itself is exercised through real moves above.
    game = create(
        client,
        ruleset="brandubh",
        mode="human-ai",
        human_side="defenders",
        players={"defenders": {"player_id": profile["id"]}},
    )
    store = client.app.state.store
    saved = store.get_game(game["id"])
    terminal = replace(State.from_dict(saved["state"]), winner="defenders", reason="escape", ply=12)
    saved.update(
        state=terminal.to_dict(),
        cursor=12,
        recorded_result={"winner": "defenders", "reason": "escape", "plies": 12},
    )
    store.update_game(saved, 0)
    create(client, ruleset="tablut")
    stats = client.get("/api/stats?ruleset=brandubh&mode=human-ai").json()
    assert stats["total_games"] == stats["completed_games"] == 1
    assert stats["defenders_wins"] == 1 and stats["average_game_length"] == 12
    assert {p["kind"] for p in stats["by_player"]} == {"human", "ai"}
    human = client.get("/api/stats?kind=human&player_id=" + profile["id"]).json()
    assert len(human["by_player"]) == 1
    assert human["by_player"][0]["wins"] == 1
    assert human["by_player"][0]["id"] == profile["id"]
    computer = client.get("/api/stats?kind=ai").json()
    assert all(p["kind"] == "ai" for p in computer["by_player"])
    assert computer["by_player"][0]["losses"] == 1
    assert client.get("/api/stats?kind=robot").status_code == 400


def test_model_import_rejects_unsafe_or_invalid_files(client):
    response = client.post(
        "/api/models/import",
        files={"file": ("bad.pt", b"not a safe tensor checkpoint", "application/octet-stream")},
    )
    assert response.status_code == 400
    assert client.get("/api/models").json() == []
    assert client.get("/api/models/missing/export").status_code == 404


def test_health_rules_and_device_metadata(client):
    assert client.get("/api/health").json()["status"] == "ok"
    rules = client.get("/api/rulesets").json()
    assert {r["id"] for r in rules} == {"tablut", "brandubh", "copenhagen"}
    assert all(r["size"] in (7, 9, 11) for r in rules)
    devices = client.get("/api/training/device").json()
    assert devices["default_device"] in ("cpu", "cuda:0", "cuda", "mps")
    assert isinstance(devices["cuda_available"], bool)


@pytest.mark.parametrize("frontend_built", [False, True])
def test_training_rejects_arbitrary_filesystem_config(tmp_path, monkeypatch, frontend_built):
    source = tmp_path / "source"
    if frontend_built:
        build = source / "frontend" / "dist"
        build.mkdir(parents=True)
        (build / "index.html").write_text("<!doctype html><title>Test frontend</title>")
    monkeypatch.setattr("hnefatafl.server.PROJECT_ROOT", source)
    with TestClient(create_app(tmp_path / "data")) as client:
        for configuration in [
            {"output_dir": "/etc"},
            {"registry_dir": "/tmp"},
            {"kind": "shell"},
            {"resume": "../../etc/passwd"},
        ]:
            response = client.post("/api/training/jobs", json=configuration)
            assert response.status_code == 400, response.text
        response = client.get("/api/training/jobs/../secret")
        # The optional frontend catch-all returns an API 404. Without that route,
        # default-deny rejects the unmatched path with 403 before routing.
        assert response.status_code == (404 if frontend_built else 403)
        assert response.json() == {"detail": "Access denied"}
        assert client.get("/api/training/jobs").json() == []


def wait_job(client, job, seconds=90):
    import time

    # Include cold PyTorch/Optuna process startup on shared runners. The actual
    # training/search budgets stay tiny, and health checks still run throughout.
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        job = client.get(f"/api/training/jobs/{job['id']}").json()
        if job["status"] != "running":
            assert job["status"] == "completed", job.get("logs", job)
            return job
        # The API must remain available during CPU work in another process.
        assert client.get("/api/health").json()["status"] == "ok"
        time.sleep(0.05)
    client.post(f"/api/training/jobs/{job['id']}/cancel")
    pytest.fail("Training subprocess did not finish within the smoke-test budget")


def test_training_worker_resume_registry_export_and_model_play(client):
    configuration = {
        "preset": "smoke",
        "ruleset": "brandubh",
        "device": "cpu",
        "cpu_threads": 1,
        "seed": 7,
    }
    launched = client.post("/api/training/jobs", json=configuration)
    assert launched.status_code == 201, launched.text
    job = wait_job(client, launched.json())
    assert job["progress"]["games_generated"] == 1
    assert job["checkpoint"].endswith(":last.pt")
    assert job["model_id"]
    exported = client.get(f"/api/models/{job['model_id']}/export")
    assert exported.status_code == 200 and len(exported.content) > 100
    imported = client.post("/api/models/import", files={"file": ("model.pt", exported.content)})
    assert imported.status_code == 201
    assert imported.json()["id"] == job["model_id"]
    inference_resume = client.post(
        "/api/training/jobs", json={**configuration, "resume": job["model_id"]}
    )
    assert inference_resume.status_code == 400 and "for play" in inference_resume.text
    best_resume = client.post(
        "/api/training/jobs",
        json={**configuration, "resume": job["checkpoint"].replace("last.pt", "best.pt")},
    )
    assert best_resume.status_code == 400 and "last.pt" in best_resume.text
    game = create(
        client,
        ruleset="brandubh",
        mode="ai-ai",
        players={
            "attackers": {
                "profile_id": "neural",
                "config": {"model_id": job["model_id"], "iterations": 2, "time_limit": 0.1},
            }
        },
    )
    played = client.post(f"/api/games/{game['id']}/ai-move", json={"version": 0})
    assert played.status_code == 200, played.text
    assert played.json()["ply"] == 1
    resumed = client.post(
        "/api/training/jobs", json={**configuration, "iterations": 2, "resume": job["checkpoint"]}
    )
    assert resumed.status_code == 201, resumed.text
    continuation = wait_job(client, resumed.json())
    assert continuation["progress"]["iteration"] == 2
    assert continuation["result"]["games_generated"] == 2


def test_optimization_worker_study_resumption_and_search_path_rejection(client):
    training = {
        "preset": "smoke",
        "ruleset": "brandubh",
        "device": "cpu",
        "cpu_threads": 1,
        "seed": 11,
    }
    unsafe = client.post(
        "/api/training/jobs",
        json={
            "kind": "tune",
            "search_space": {"registry_dir": {"type": "categorical", "choices": ["/tmp/evil"]}},
        },
    )
    assert unsafe.status_code == 400
    configuration = {
        "kind": "tune",
        "trials": 1,
        "study_name": "api-smoke",
        "training": training,
        "search_space": {"learning_rate": {"type": "categorical", "choices": [0.001]}},
    }
    first = client.post("/api/training/jobs", json=configuration)
    assert first.status_code == 201, first.text
    job = wait_job(client, first.json())
    assert job["result"]["completed_trials"] == 1
    resumed = client.post("/api/training/jobs", json={**configuration, "resume_study": job["id"]})
    assert resumed.status_code == 201, resumed.text
    continuation = wait_job(client, resumed.json())
    assert continuation["result"]["completed_trials"] == 2


def test_upload_limit_uses_the_checkpoint_validator_limit(client, monkeypatch):
    from hnefatafl import models

    # Exercise the boundary without allocating a production-sized model payload.
    monkeypatch.setattr(models, "MAX_CHECKPOINT_BYTES", 32)
    response = client.post("/api/models/import", files={"file": ("large.pt", b"x" * 33)})
    assert response.status_code == 413
    assert not list((client.app.state.data_dir / "models").glob("upload-*.pt"))


def test_replaying_finished_game_preserves_result_until_a_new_branch(client):
    game = create(client, ruleset="brandubh")
    # A complete, legal king capture from the standard initial arrangement.
    for source, target in [(10, 8), (25, 46), (26, 25), (23, 44), (22, 23)]:
        game = move(client, game, {"source": source, "target": target})
    assert game["winner"] == game["recorded_winner"] == "attackers"
    ended = game["ended_at"]
    before = client.get("/api/stats").json()
    assert before["completed_games"] == 1 and before["attackers_wins"] == 1
    game = client.post(
        f"/api/games/{game['id']}/seek", json={"version": game["version"], "cursor": 0}
    ).json()
    assert game["winner"] is None and game["recorded_winner"] == "attackers"
    assert game["ended_at"] == ended
    replay_stats = client.get("/api/stats").json()
    for key in [
        "completed_games",
        "attackers_wins",
        "defenders_wins",
        "draws",
        "average_game_length",
        "by_player",
        "by_ruleset",
    ]:
        assert replay_stats[key] == before[key]
    game = move(client, game, game["legal_moves"][-1])
    assert game["recorded_winner"] is None and game["ended_at"] is None
    assert game["total_moves"] == 1
    assert client.get("/api/stats").json()["completed_games"] == 0


def test_cancellation_waits_for_actual_worker_exit(client):
    import os
    import time

    launched = client.post(
        "/api/training/jobs", json={"preset": "smoke", "iterations": 1000, "device": "cpu"}
    )
    assert launched.status_code == 201
    job = launched.json()
    cancelled = client.post(f"/api/training/jobs/{job['id']}/cancel").json()
    assert cancelled["status"] in {"cancelling", "cancelled"}
    # Status changes to cancelled only after the actual OS process stops.
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        result = client.get(f"/api/training/jobs/{job['id']}").json()
        if result["status"] == "cancelled":
            break
        time.sleep(0.05)
    assert result["status"] == "cancelled"
    with pytest.raises(ProcessLookupError):
        os.kill(job["pid"], 0)


def test_server_shutdown_stops_and_reaps_its_worker(tmp_path):
    import os

    app = create_app(tmp_path)
    with TestClient(app) as session:
        response = session.post(
            "/api/training/jobs", json={"preset": "smoke", "iterations": 1000, "device": "cpu"}
        )
        assert response.status_code == 201
        job = response.json()
    with pytest.raises(ProcessLookupError):
        os.kill(job["pid"], 0)
    assert app.state.store.get_job(job["id"])["status"] == "cancelled"


@pytest.mark.parametrize(
    "distribution",
    [
        {"type": [], "low": 0.001, "high": 0.002},
        {"type": "int", "low": 1, "high": 2, "step": 0},
        {"type": "float", "low": 0, "high": 1, "log": True},
    ],
)
def test_malformed_search_distributions_fail_before_spawning(client, distribution):
    response = client.post(
        "/api/training/jobs",
        json={
            "kind": "tune",
            "trials": 1,
            "training": {"preset": "smoke"},
            "search_space": {"learning_rate": distribution},
        },
    )
    assert response.status_code == 400
    assert client.get("/api/training/jobs").json() == []


def test_ai_configuration_must_be_an_object(client):
    response = client.post(
        "/api/games", json={"mode": "ai-ai", "players": {"attackers": {"config": 42}}}
    )
    assert response.status_code == 400


def test_statistics_rates_include_draws_and_distinguish_model_identities(client, tmp_path):
    from hnefatafl.models import ModelRegistry, PolicyValueNet, save_checkpoint

    registry = ModelRegistry(client.app.state.data_dir / "models")
    # Identical user-facing model names remain distinguishable by stable IDs.
    models = []
    for index in range(2):
        checkpoint = save_checkpoint(
            tmp_path / f"stats-model-{index}.pt",
            PolicyValueNet("brandubh", channels=4, blocks=0),
            {"name": "Learner"},
        )
        models.append(registry.register(checkpoint))
    assert models[0]["id"] != models[1]["id"]
    profile = client.post("/api/players", json={"name": "Eira"}).json()
    store = client.app.state.store
    # Terminal records isolate arithmetic from engine correctness, which has
    # separate complete legal-game/replay regression coverage above.
    for index, winner in enumerate(["defenders", "attackers", "draw"]):
        model = models[0] if index < 2 else models[1]
        game = create(
            client,
            ruleset="brandubh",
            mode="human-ai",
            human_side="defenders",
            seed=0,
            players={
                "defenders": {"player_id": profile["id"]},
                "attackers": {
                    "name": "My oracle",
                    "profile_id": "neural",
                    "config": {"model_id": model["id"]},
                },
            },
        )
        saved = store.get_game(game["id"])
        saved["recorded_result"] = {"winner": winner, "reason": "test_result", "plies": 10 + index}
        store.update_game(saved, 0)
    # An unfinished game must not dilute any completed-result denominator.
    create(
        client,
        ruleset="brandubh",
        mode="human-ai",
        human_side="defenders",
        players={"defenders": {"player_id": profile["id"]}},
    )
    stats = client.get("/api/stats?ruleset=brandubh&mode=human-ai").json()
    assert stats["total_games"] == 4 and stats["completed_games"] == 3
    assert (
        stats["attacker_win_rate"]
        == stats["defender_win_rate"]
        == stats["draw_rate"]
        == pytest.approx(1 / 3)
    )
    rules = stats["by_ruleset"][0]
    assert (
        rules["attacker_win_rate"]
        == rules["defender_win_rate"]
        == rules["draw_rate"]
        == pytest.approx(1 / 3)
    )
    human = next(row for row in stats["by_player"] if row["id"] == profile["id"])
    assert human["games"] == 3
    assert human["win_rate"] == human["loss_rate"] == human["draw_rate"] == pytest.approx(1 / 3)
    assert human["model_id"] is None and human["profile_id"] is None
    ais = [row for row in stats["by_player"] if row["kind"] == "ai"]
    assert {row["model_id"] for row in ais} == {model["id"] for model in models}
    assert all(
        row["name"] == "My oracle"
        and row["profile_id"] == "neural"
        and row["profile_name"] == "Trained model"
        and row["model_name"] == "Learner"
        for row in ais
    )
    assert len({row["opponent_name"] for row in ais}) == 2
    twice = next(row for row in ais if row["model_id"] == models[0]["id"])
    assert twice["win_rate"] == twice["loss_rate"] == 0.5 and twice["draw_rate"] == 0
    once = next(row for row in ais if row["model_id"] == models[1]["id"])
    assert once["draw_rate"] == 1 and once["win_rate"] == once["loss_rate"] == 0
    empty = client.get("/api/stats?ruleset=copenhagen").json()
    assert empty["attacker_win_rate"] == empty["defender_win_rate"] == empty["draw_rate"] == 0
    assert empty["by_player"] == empty["by_ruleset"] == []


def test_schema_one_migrates_profiles_without_losing_rows(tmp_path):
    import sqlite3

    database = tmp_path / "old.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript("""
            CREATE TABLE players(id TEXT PRIMARY KEY,name TEXT NOT NULL,created_at TEXT NOT NULL);
            INSERT INTO players VALUES('legacy-player','Freya','2026-01-01T00:00:00Z');
            PRAGMA user_version=1;
        """)
    store = Store(database)
    assert store.players()[0]["id"] == "legacy-player"
    assert store.players()[0]["deleted_at"] is None
    assert store.delete_player("legacy-player", "2026-01-02T00:00:00Z")
    assert store.players() == []
    reopened = Store(database)
    assert reopened.players() == []
    assert reopened.players(include_deleted=True)[0]["name"] == "Freya"
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 8


def complete_human_game(client, game):
    from hnefatafl.ai import choose_move

    for _ in range(100):
        if game["winner"]:
            return game
        saved = client.app.state.store.get_game(game["id"])
        state = State.from_dict(saved["state"])
        selected = choose_move(state, {"algorithm": "baseline", "seed": 3 + state.ply})
        game = move(client, game, selected.to_dict())
    pytest.fail("Seeded fixture did not reach a rule-terminal outcome")


def test_delete_player_hides_selection_but_preserves_real_history_and_identity(client):
    player = client.post("/api/players", json={"name": "Sigrid"}).json()
    game = create(client, ruleset="brandubh", players={"defenders": {"player_id": player["id"]}})
    game = complete_human_game(client, game)
    stats_before = client.get("/api/stats").json()
    response = client.delete(f"/api/players/{player['id']}")
    assert response.status_code == 200 and response.json() == {"deleted": True, "id": player["id"]}
    assert not any(p["id"] == player["id"] for p in client.get("/api/players").json())
    assert client.delete(f"/api/players/{player['id']}").status_code == 404
    record = client.get(f"/api/games/{game['id']}").json()
    assert record["players"]["defenders"]["name"] == "Sigrid"
    assert record["players"]["defenders"]["player_id"] == player["id"]
    assert client.get(f"/api/games/{game['id']}/replay").json()["validated"]
    stats_after = client.get("/api/stats").json()
    historical = next(row for row in stats_after["by_player"] if row["id"] == player["id"])
    assert historical["deleted"] is True
    assert (
        historical["games"]
        == next(row for row in stats_before["by_player"] if row["id"] == player["id"])["games"]
    )
    assert (
        client.post(
            "/api/games", json={"players": {"defenders": {"player_id": player["id"]}}}
        ).status_code
        == 400
    )
    restart = client.post(f"/api/games/{game['id']}/restart", json={"version": game["version"]})
    assert restart.status_code == 409
    replacement = client.post("/api/players", json={"name": "Sigrid"}).json()
    assert replacement["id"] != player["id"]


def test_deleted_human_can_finish_existing_game_but_is_not_reused_by_name(client):
    player = client.post("/api/players", json={"name": "Eirik"}).json()
    game = create(client, ruleset="brandubh", players={"attackers": {"player_id": player["id"]}})
    assert client.delete(f"/api/players/{player['id']}").status_code == 200
    game = complete_human_game(client, game)
    assert game["players"]["attackers"]["player_id"] == player["id"]
    other = create(client, players={"attackers": {"name": "Eirik"}})
    assert other["players"]["attackers"]["player_id"] != player["id"]


def api_model(client, tmp_path, name="Hugin"):
    from hnefatafl.models import ModelRegistry, PolicyValueNet, save_checkpoint

    path = save_checkpoint(
        tmp_path / "original-training.pt", PolicyValueNet("brandubh", 4, 0), {"name": name}
    )
    model = ModelRegistry(client.app.state.data_dir / "models").register(path)
    return model, path


def test_model_delete_preserves_completed_legacy_names_records_and_checkpoints(client, tmp_path):
    model, original = api_model(client, tmp_path)
    # Construct a completed legal human game, then attach the legacy AI metadata
    # shape to isolate catalogue behavior without depending on neural strength.
    game = complete_human_game(client, create(client, ruleset="brandubh"))
    store = client.app.state.store
    saved = store.get_game(game["id"])
    saved["players"]["attackers"] = {
        "kind": "ai",
        "name": "My raven",
        "profile_id": "neural",
        "player_id": None,
        "config": {"algorithm": "neural", "model_id": model["id"], "iterations": 2},
    }
    store.update_game(saved, game["version"])
    before = client.get("/api/stats").json()
    deleted = client.delete(f"/api/models/{model['id']}")
    assert deleted.status_code == 200, deleted.text
    assert deleted.json() == {"deleted": True, "id": model["id"]}
    assert client.get("/api/models").json() == []
    assert not (client.app.state.data_dir / "models" / f"{model['id']}.pt").exists()
    assert original.exists()
    assert client.delete(f"/api/models/{model['id']}").status_code == 404
    assert client.delete("/api/models/not-a-model").status_code == 404
    assert client.get(f"/api/models/{model['id']}/export").status_code == 404
    replay = client.get(f"/api/games/{game['id']}/replay")
    assert replay.status_code == 200 and replay.json()["validated"]
    record = client.get(f"/api/games/{game['id']}").json()
    assert record["players"]["attackers"]["model_name"] == "Hugin"
    after = client.get("/api/stats").json()
    assert after["by_player"] == before["by_player"]
    assert after["by_player"][0]["model_name"] == "Hugin"
    restarted = client.post(f"/api/games/{game['id']}/restart", json={"version": record["version"]})
    assert restarted.status_code == 409
    # Replay is still possible, but branching cannot create an unusable AI game.
    viewed = client.post(
        f"/api/games/{game['id']}/seek", json={"cursor": 0, "version": record["version"]}
    ).json()
    branch = client.post(f"/api/games/{game['id']}/ai-move", json={"version": viewed["version"]})
    assert branch.status_code == 404
    viewed = client.post(
        f"/api/games/{game['id']}/seek", json={"cursor": 1, "version": viewed["version"]}
    ).json()
    human_branch = client.post(
        f"/api/games/{game['id']}/moves",
        json={**viewed["legal_moves"][0], "version": viewed["version"]},
    )
    assert human_branch.status_code == 409
    assert client.get(f"/api/games/{game['id']}").json()["total_moves"] == game["total_moves"]
    assert client.get(f"/api/games/{game['id']}/replay").json()["validated"]


def test_model_delete_blocks_live_references_and_snapshots_new_names(client, tmp_path):
    model, original = api_model(client, tmp_path)
    game = create(
        client,
        ruleset="brandubh",
        mode="human-ai",
        human_side="defenders",
        players={
            "attackers": {
                "profile_id": "neural",
                "config": {"model_id": model["id"], "iterations": 2},
            }
        },
    )
    assert game["players"]["attackers"]["model_name"] == "Hugin"
    assert game["players"]["attackers"]["profile_name"] == "Trained model"
    refused = client.delete(f"/api/models/{model['id']}")
    assert refused.status_code == 409 and "unfinished game" in refused.json()["detail"]
    assert len(client.get("/api/models").json()) == 1
    assert original.exists()


def test_ai_statistics_use_effective_parameter_variants_and_stable_defaults(client):
    configurations = [
        {"algorithm": "alpha_beta", "depth": 2},
        {"algorithm": "alpha_beta", "depth": 2, "time_limit": 2, "seed": 0},
        {"algorithm": "alpha_beta", "depth": 3},
        {"algorithm": "alpha_beta", "depth": 2, "seed": 1},
        {"algorithm": "alpha_beta", "depth": 2, "time_limit": 1},
    ]
    variants = []
    for config in configurations:
        game = create(
            client,
            ruleset="brandubh",
            mode="human-ai",
            seed=config.get("seed", 0),
            players={"attackers": {"config": config}},
        )
        player = game["players"]["attackers"]
        assert player["profile_id"] == "alpha_beta" and player["profile_name"] == "Jarl"
        assert player["config"]["evaluation"] == "heuristic"
        variants.append(player["variant_id"])
        saved = client.app.state.store.get_game(game["id"])
        saved["recorded_result"] = {"winner": "attackers", "reason": "fixture", "plies": 5}
        client.app.state.store.update_game(saved, 0)
    assert variants[0] == variants[1]
    assert variants[0] == variants[3]
    assert len(set(variants)) == 3
    stats = client.get("/api/stats?kind=ai").json()
    assert len(stats["by_player"]) == 3
    merged = next(row for row in stats["by_player"] if row["variant_id"] == variants[0])
    assert merged["games"] == 3
    assert merged["seed_count"] == 2
    assert merged["config"] == {
        "algorithm": "alpha_beta",
        "depth": 2,
        "evaluation": "heuristic",
        "time_limit": 2.0,
    }
    assert all(row["id"] == row["variant_id"] for row in stats["by_player"])


def test_historical_explicit_algorithm_keeps_engine_defaults_not_profile_defaults(client):
    game = create(client, ruleset="brandubh", mode="human-ai")
    saved = client.app.state.store.get_game(game["id"])
    saved["players"]["attackers"]["config"] = {"algorithm": "baseline"}
    saved["players"]["attackers"].pop("variant_id", None)
    saved["recorded_result"] = {"winner": "attackers", "reason": "fixture", "plies": 5}
    client.app.state.store.update_game(saved, 0)
    row = client.get("/api/stats?kind=ai").json()["by_player"][0]
    assert row["config"]["time_limit"] == 2.0
    new_profile = create(client, ruleset="brandubh", mode="human-ai")
    assert new_profile["players"]["attackers"]["config"]["time_limit"] == 1.0
    assert new_profile["players"]["attackers"]["variant_id"] != row["variant_id"]


def test_rulesets_and_statistics_are_ordered_by_board_size(client):
    rulesets = client.get("/api/rulesets").json()
    assert [rule["id"] for rule in rulesets] == ["brandubh", "tablut", "copenhagen"]
    for ruleset in ["copenhagen", "tablut", "brandubh"]:
        game = create(client, ruleset=ruleset)
        saved = client.app.state.store.get_game(game["id"])
        saved["recorded_result"] = {"winner": "draw", "reason": "fixture", "plies": 5}
        client.app.state.store.update_game(saved, 0)
    assert [row["ruleset"] for row in client.get("/api/stats").json()["by_ruleset"]] == [
        "brandubh",
        "tablut",
        "copenhagen",
    ]


def test_model_delete_serializes_with_new_game_reference_creation(client, tmp_path, monkeypatch):
    import threading
    from concurrent.futures import TimeoutError

    model, _ = api_model(client, tmp_path)
    entered = threading.Event()
    release = threading.Event()
    delete_started = threading.Event()
    original_create = Store.create_game

    def delayed_create(store, game):
        entered.set()
        assert release.wait(5)
        return original_create(store, game)

    def deletion():
        delete_started.set()
        return client.delete(f"/api/models/{model['id']}")

    monkeypatch.setattr(Store, "create_game", delayed_create)
    with ThreadPoolExecutor(max_workers=2) as executor:
        creating = executor.submit(
            client.post,
            "/api/games",
            json={
                "ruleset": "brandubh",
                "mode": "human-ai",
                "players": {
                    "attackers": {"profile_id": "neural", "config": {"model_id": model["id"]}}
                },
            },
        )
        assert entered.wait(2)
        deleting = executor.submit(deletion)
        assert delete_started.wait(2)
        try:
            with pytest.raises(TimeoutError):
                deleting.result(timeout=0.1)
        finally:
            release.set()
        assert creating.result(timeout=5).status_code == 201
        assert deleting.result(timeout=5).status_code == 409
    assert len(client.get("/api/models").json()) == 1


@pytest.mark.parametrize(
    "parameter,value",
    [
        ("iterations", 81),
        ("exploration", 2),
        ("rollout_depth", 0),
        ("temperature", 0.5),
    ],
)
def test_mcts_parameter_variants_keep_separate_records(client, parameter, value):
    for configuration in [{"algorithm": "mcts"}, {"algorithm": "mcts", parameter: value}]:
        game = create(
            client,
            ruleset="brandubh",
            mode="human-ai",
            seed=configuration.get("seed", 0),
            players={"attackers": {"config": configuration}},
        )
        saved = client.app.state.store.get_game(game["id"])
        saved["recorded_result"] = {"winner": "attackers", "reason": "fixture", "plies": 5}
        client.app.state.store.update_game(saved, 0)
    rows = client.get("/api/stats?kind=ai").json()["by_player"]
    assert len(rows) == 2
    assert len({row["variant_id"] for row in rows}) == 2
    assert rows[0]["config"][parameter] != rows[1]["config"][parameter]
    assert all(row["games"] == 1 and row["wins"] == 1 for row in rows)
