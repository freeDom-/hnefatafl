"""API contracts, durable replay and optimistic concurrency using real game rules."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

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


def test_training_rejects_arbitrary_filesystem_config(client):
    for configuration in [
        {"output_dir": "/etc"},
        {"registry_dir": "/tmp"},
        {"kind": "shell"},
        {"resume": "../../etc/passwd"},
    ]:
        response = client.post("/api/training/jobs", json=configuration)
        assert response.status_code == 400, response.text
    assert client.get("/api/training/jobs/../secret").status_code == 404


def wait_job(client, job, seconds=30):
    import time

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
