"""Real tournament workers, retained results, safe deletion and deterministic replay."""

from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import time

import pytest
from auth_support import AuthenticatedClient as TestClient

from hnefatafl.engine import Move, apply_move, new_game
from hnefatafl.server import create_app
from hnefatafl.storage import Store


def configuration(**changes):
    return {
        "name": "Ravens at the king's table",
        "rulesets": ["brandubh"],
        "players": [
            {"name": "Hugin", "config": {"algorithm": "baseline", "time_limit": 0.01}},
            {"name": "Munin", "config": {"algorithm": "baseline", "time_limit": 0.01}},
        ],
        "games": 2,
        "max_plies": 2,
        "seed": 3,
        **changes,
    }


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as session:
        yield session


def launch(client, config=None):
    response = client.post("/api/tournaments", json=config or configuration())
    assert response.status_code == 201, response.text
    record = response.json()
    assert record["status"] == "running"
    player_count = len(record["config"]["players"])
    assert record["progress"]["total_games"] == (
        record["config"]["games"]
        * len(record["config"]["rulesets"])
        * player_count
        * (player_count - 1)
        // 2
    )
    return record


def wait_for(client, identifier, predicate, *, timeout=20):
    deadline = time.monotonic() + timeout
    record = None
    while time.monotonic() < deadline:
        response = client.get(f"/api/tournaments/{identifier}")
        assert response.status_code == 200, response.text
        record = response.json()
        if predicate(record):
            return record
        assert record["status"] not in {"failed", "cancelled"}, record
        time.sleep(0.03)
    pytest.fail(f"Tournament did not reach the requested state: {record}")


def test_real_balanced_tournament_retains_truncations_and_legal_replay(client):
    record = launch(client, configuration(rulesets=["brandubh", "tablut", "copenhagen"]))
    record = wait_for(client, record["id"], lambda row: row["status"] == "completed")
    assert record["progress"] == {"completed_games": 6, "total_games": 6}
    assert record["created_at"] and record["ended_at"]
    result = record["result"]
    assert result["scores"] == [0.0, 0.0]
    assert result["truncated"] == 6
    assert [row["games"] for row in result["standings"]] == [6, 6]
    assert all(row["draws"] == 0 and row["truncated"] == 6 for row in result["standings"])
    assert result["average_game_length"] == 2
    assert result["average_decision_time"] > 0
    assert [row["attacker_player"] for row in result["games"]] == [0, 1, 0, 1, 0, 1]
    assert all(row["truncated"] == 2 for row in result["by_ruleset"].values())
    for index, match in enumerate(result["games"]):
        assert match["winner"] is None
        assert match["outcome"] == "truncated"
        assert match["reason"] == "experiment_ply_limit"
        state = new_game(match["ruleset"])
        for move in match["moves"]:
            state = apply_move(state, Move(**move))
        assert state.to_dict() == match["state"]
        replay = client.get(f"/api/tournaments/{record['id']}/games/{index}/replay")
        assert replay.status_code == 200, replay.text
        assert replay.json()["validated"]
        assert len(replay.json()["states"]) == 3
        assert replay.json()["states"][-1] == match["state"]
    assert client.get(f"/api/tournaments/{record['id']}/games/6/replay").status_code == 404
    assert client.get("/api/tournaments/missing").status_code == 404


def test_tournament_records_distinct_alpha_beta_search_configs(client):
    created = launch(
        client,
        configuration(
            players=[
                {
                    "name": "Legacy",
                    "config": {
                        "algorithm": "alpha_beta",
                        "depth": 1,
                        "time_limit": 0.05,
                        "search": {"legacy": True},
                    },
                },
                {
                    "name": "Full symmetry",
                    "config": {
                        "algorithm": "alpha_beta",
                        "depth": 1,
                        "time_limit": 0.05,
                        "search": {
                            "legacy": False,
                            "transposition": True,
                            "move_ordering": True,
                            "symmetry_transposition": True,
                            "symmetry_pruning": True,
                        },
                    },
                },
            ],
            games=2,
            max_plies=4,
        ),
    )
    record = wait_for(client, created["id"], lambda row: row["status"] == "completed")
    players = record["config"]["players"]
    assert players[0]["config"]["search"] == {
        "legacy": True,
        "transposition": True,
        "move_ordering": True,
        "symmetry_transposition": False,
        "symmetry_pruning": False,
        "table_capacity": 100_000,
    }
    assert players[1]["config"]["search"]["symmetry_pruning"] is True
    assert players[0]["variant_id"] != players[1]["variant_id"]
    assert players[0]["agent_rune"] != players[1]["agent_rune"]


def test_saved_results_survive_restart_and_delete_only_the_tournament(tmp_path):
    with TestClient(create_app(tmp_path)) as first:
        player = first.post("/api/players", json={"name": "Astrid"}).json()
        game = first.post("/api/games", json={"ruleset": "brandubh"}).json()
        created = launch(first)
        saved = wait_for(first, created["id"], lambda row: row["status"] == "completed")
        other = launch(first, configuration(name="Keep this separate tournament"))
        other = wait_for(first, other["id"], lambda row: row["status"] == "completed")
    with TestClient(create_app(tmp_path)) as second:
        unrelated = tmp_path / "tournaments" / "keep.txt"
        unrelated.write_text("Retain unrelated files")
        detail = second.get(f"/api/tournaments/{created['id']}")
        assert detail.status_code == 200
        assert detail.json() == saved
        summaries = second.get("/api/tournaments").json()
        assert {row["id"] for row in summaries} == {created["id"], other["id"]}
        assert next(row for row in summaries if row["id"] == created["id"])["progress"] == {
            "completed_games": 2,
            "total_games": 2,
        }
        deleted = second.delete(f"/api/tournaments/{created['id']}")
        assert deleted.status_code == 200, deleted.text
        assert deleted.json() == {"deleted": True, "id": created["id"]}
        assert not (tmp_path / "tournaments" / created["id"]).exists()
        assert (tmp_path / "tournaments" / other["id"]).is_dir()
        assert unrelated.read_text() == "Retain unrelated files"
        assert second.get(f"/api/tournaments/{created['id']}").status_code == 404
        assert second.delete(f"/api/tournaments/{created['id']}").status_code == 404
        assert second.get(f"/api/tournaments/{created['id']}/games/0/replay").status_code == 404
        assert second.get(f"/api/tournaments/{other['id']}").json() == other
        assert second.get(f"/api/games/{game['id']}").json() == game
        assert any(row["id"] == player["id"] for row in second.get("/api/players").json())


def test_completed_rule_outcomes_update_named_opponent_standings(client):
    config = configuration(max_plies=100)
    for player in config["players"]:
        player["config"]["time_limit"] = 0.1
    created = launch(client, config)
    completed = wait_for(client, created["id"], lambda row: row["status"] == "completed")
    assert [row["name"] for row in completed["config"]["players"]] == ["Shield bearer"] * 2
    result = completed["result"]
    assert result["truncated"] == 0
    assert all(match["outcome"] == "completed" for match in result["games"])
    expected_scores = [0.0, 0.0]
    for match in result["games"]:
        if match["winner"] == "draw":
            expected_scores = [value + 0.5 for value in expected_scores]
        else:
            winner = (
                match["attacker_player"]
                if match["winner"] == "attackers"
                else 1 - match["attacker_player"]
            )
            expected_scores[winner] += 1
    assert result["scores"] == expected_scores
    assert [row["score"] for row in result["standings"]] == expected_scores
    assert all(row["wins"] + row["losses"] + row["draws"] == 2 for row in result["standings"])


@pytest.mark.parametrize(
    "changes",
    [
        {"name": "   "},
        {"name": "x" * 81},
        {"rulesets": []},
        {"rulesets": ["brandubh", "brandubh"]},
        {"rulesets": ["../../outside"]},
        {"games": 3},
        {"games": 0},
        {"games": 102},
        {"games": True},
        {"max_plies": 0},
        {"max_plies": 1001},
        {"max_plies": 1.5},
        {"seed": -1},
        {"seed": True},
        {"players": []},
        {"players": [{"name": "Alone", "config": {"algorithm": "baseline"}}]},
        {"players": [{"config": {}}] * 5},
        {"output": "/tmp/unsafe-output"},
    ],
)
def test_invalid_tournament_setup_is_rejected_before_creating_data(client, changes):
    response = client.post("/api/tournaments", json=configuration(**changes))
    assert response.status_code in {400, 422}, response.text
    assert client.get("/api/tournaments").json() == []


@pytest.mark.parametrize(
    "player",
    [
        {"name": "Raven", "config": {"algorithm": "unknown"}},
        {"name": "Raven", "config": {"algorithm": "baseline", "time_limit": 0}},
        {"name": "Raven", "config": {"algorithm": "baseline", "model_path": "/etc/passwd"}},
        {"name": "Raven", "config": {"algorithm": "mcts", "iterations": True}},
        {"name": "Raven", "config": {"algorithm": "neural"}},
        {"name": "Raven", "profile_id": "nonexistent", "config": {}},
    ],
)
def test_invalid_opponent_config_is_rejected(client, player):
    config = configuration()
    config["players"][0] = player
    response = client.post("/api/tournaments", json=config)
    assert response.status_code in {400, 422}, response.text
    assert client.get("/api/tournaments").json() == []


def test_active_tournament_stops_keeps_partial_results_and_can_then_be_deleted(client):
    players = [
        {
            "name": name,
            "config": {"algorithm": "alpha_beta", "depth": 5, "time_limit": 0.05},
        }
        for name in ("Hugin", "Munin")
    ]
    created = launch(client, configuration(players=players, games=100, max_plies=2))
    assert client.get("/api/health").status_code == 200
    assert client.post("/api/tournaments", json=configuration()).status_code == 409
    refused = client.delete(f"/api/tournaments/{created['id']}")
    assert refused.status_code == 409, refused.text
    partial = wait_for(client, created["id"], lambda row: row["progress"]["completed_games"] >= 1)
    assert partial["status"] == "running"
    stopped = client.post(f"/api/tournaments/{created['id']}/cancel")
    assert stopped.status_code == 200, stopped.text
    cancelled = stopped.json()
    assert cancelled["status"] == "cancelled"
    assert 1 <= cancelled["progress"]["completed_games"] < 100
    assert len(cancelled["result"]["games"]) == cancelled["progress"]["completed_games"]
    assert cancelled["ended_at"]
    assert client.get(f"/api/tournaments/{created['id']}/games/0/replay").json()["validated"]
    retained = client.get(f"/api/tournaments/{created['id']}").json()
    assert retained == cancelled
    deleted = client.delete(f"/api/tournaments/{created['id']}")
    assert deleted.status_code == 200
    next_run = launch(client)
    wait_for(client, next_run["id"], lambda row: row["status"] == "completed")


def test_registered_model_is_protected_while_running_and_snapshot_survives_deletion(
    client, tmp_path
):
    from hnefatafl.models import ModelRegistry, PolicyValueNet, save_checkpoint

    checkpoint = save_checkpoint(
        tmp_path / "training-original.pt",
        PolicyValueNet("brandubh", 4, 0),
        {
            "name": "Eir",
            "iteration": 3,
            "training_config": {
                "iterations": 5,
                "games": 4,
                "mcts_iterations": 16,
                "seed": 42,
                "output_dir": str(tmp_path),
                "resume": str(tmp_path / "secret.pt"),
            },
        },
    )
    model = ModelRegistry(client.app.state.data_dir / "models").register(checkpoint)
    config = configuration()
    config["players"][0] = {
        "name": "Eir's champion",
        "profile_id": "neural",
        "config": {"model_id": model["id"], "iterations": 1, "time_limit": 0.01},
    }
    wrong_rules = deepcopy(config)
    wrong_rules["rulesets"] = ["brandubh", "tablut"]
    assert client.post("/api/tournaments", json=wrong_rules).status_code == 400
    running = launch(client, {**config, "games": 100, "max_plies": 1000})
    refused = client.delete(f"/api/models/{model['id']}")
    assert refused.status_code == 409
    assert "tournament" in refused.json()["detail"].lower()
    assert client.post(f"/api/tournaments/{running['id']}/cancel").status_code == 200
    completed = launch(client, config)
    completed = wait_for(client, completed["id"], lambda row: row["status"] == "completed")
    opponent = completed["config"]["players"][0]
    assert opponent["name"] == opponent["model_name"] == "Eir"
    assert opponent["rune"] == opponent["agent_rune"] == client.get("/api/models").json()[0]["rune"]
    assert opponent["model"]["architecture"]["channels"] == 4
    assert opponent["model"]["iteration"] == 3
    assert opponent["model"]["training_config"] == {
        "iterations": 5,
        "games": 4,
        "mcts_iterations": 16,
        "seed": 42,
    }
    renamed = client.patch(f"/api/models/{model['id']}", json={"name": "Eir renamed"})
    assert renamed.status_code == 200
    assert client.get(f"/api/tournaments/{completed['id']}").json() == completed
    assert opponent["config"]["model_id"] == model["id"]
    assert opponent["config"]["algorithm"] == "neural"
    assert opponent["variant_id"].startswith("ai:")
    # API documents never reveal the worker's resolved checkpoint path.
    assert "model_path" not in json.dumps(completed)
    assert str(tmp_path) not in json.dumps(completed)
    assert client.delete(f"/api/models/{model['id']}").status_code == 200
    assert checkpoint.exists()
    assert client.get(f"/api/tournaments/{completed['id']}").json() == completed
    replay = client.get(f"/api/tournaments/{completed['id']}/games/0/replay")
    assert replay.status_code == 200 and replay.json()["validated"]


def test_immediate_cancel_retains_structured_zero_game_results(client):
    config = configuration(games=100, max_plies=1000)
    config["players"][0]["config"] = {
        "algorithm": "alpha_beta",
        "depth": 5,
        "time_limit": 1,
    }
    created = launch(client, config)
    response = client.post(f"/api/tournaments/{created['id']}/cancel")
    assert response.status_code == 200, response.text
    stopped = response.json()
    assert stopped["status"] == "cancelled"
    assert stopped["progress"] == {"completed_games": 0, "total_games": 100}
    assert stopped["result"]["games"] == []
    assert stopped["result"]["scores"] == [0, 0]
    assert all(row["games"] == 0 for row in stopped["result"]["standings"])
    assert client.get(f"/api/tournaments/{created['id']}/games/0/replay").status_code == 404


def test_worker_failure_keeps_results_and_hides_checkpoint_details(client, tmp_path):
    from hnefatafl.models import ModelRegistry, PolicyValueNet, save_checkpoint

    checkpoint = save_checkpoint(
        tmp_path / "original.pt", PolicyValueNet("brandubh", 4, 0), {"name": "Broken raven"}
    )
    model = ModelRegistry(client.app.state.data_dir / "models").register(checkpoint)
    Path(model["path"]).write_bytes(b"Not a tensor checkpoint")
    config = configuration()
    config["players"][0] = {
        "name": "Unavailable champion",
        "profile_id": "neural",
        "config": {"model_id": model["id"], "iterations": 1, "time_limit": 0.01},
    }
    created = launch(client, config)
    failed = wait_for(client, created["id"], lambda row: row["status"] == "failed")
    assert failed["error"]
    assert failed["ended_at"]
    assert failed["progress"]["completed_games"] == 0
    assert failed["result"]["games"] == []
    assert "model_path" not in json.dumps(failed)
    assert str(tmp_path) not in json.dumps(failed)
    assert "Traceback" not in json.dumps(failed)
    assert client.delete(f"/api/tournaments/{created['id']}").status_code == 200


def test_server_shutdown_stops_its_worker_and_retains_the_tournament(tmp_path):
    config = configuration(games=100, max_plies=1000)
    config["players"][0]["config"] = {
        "algorithm": "alpha_beta",
        "depth": 5,
        "time_limit": 1,
    }
    with TestClient(create_app(tmp_path)) as first:
        created = launch(first, config)
    with TestClient(create_app(tmp_path)) as second:
        record = second.get(f"/api/tournaments/{created['id']}").json()
        assert record["status"] == "cancelled"
        assert record["progress"]["completed_games"] == 0
        assert record["result"]["games"] == []
        assert record["ended_at"]


def test_schema_two_upgrade_keeps_existing_history_and_archived_metadata(tmp_path):
    database = tmp_path / "history.sqlite3"
    store = Store(database)
    game = {"id": "history", "version": 0, "created_at": "2026-01-01", "moves": []}
    store.create_game(game)
    store.create_player({"id": "retired", "name": "Freya", "created_at": "2026-01-01"})
    store.delete_player("retired", "2026-01-02")
    store.archive_model({"id": "old-model", "name": "Eir"})
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TABLE tournaments")
        connection.execute("PRAGMA user_version=2")
    upgraded = Store(database)
    assert upgraded.get_game("history") == game
    assert upgraded.players() == []
    assert upgraded.players(include_deleted=True)[0]["deleted_at"] == "2026-01-02"
    assert upgraded.historical_models() == [{"id": "old-model", "name": "Eir"}]
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 9


def test_interrupted_worker_is_reported_without_losing_retained_data(tmp_path):
    store = Store(tmp_path / "hnefatafl.sqlite3")
    interrupted = {
        "id": "a" * 32,
        "name": "Interrupted saga",
        "status": "running",
        "created_at": "2026-01-01T00:00:00Z",
        "config": configuration(),
        "progress": {"completed_games": 0, "total_games": 2},
        "pid": 2147483647,
    }
    store.save_tournament(interrupted)
    with TestClient(create_app(tmp_path)) as session:
        record = session.get(f"/api/tournaments/{interrupted['id']}").json()
        assert record["status"] == "failed"
        assert record["ended_at"]
        assert record["error"]
        cleaned = deepcopy(record["config"])
        for player in cleaned["players"]:
            assert player.pop("rune")
            assert player.pop("agent_rune")
        assert cleaned == interrupted["config"]
        assert store.get_tournament(interrupted["id"])["config"] == interrupted["config"]
        assert record["progress"] == interrupted["progress"]
        assert session.delete(f"/api/tournaments/{interrupted['id']}").status_code == 200


def test_tournament_names_come_from_profiles_without_client_aliases(client):
    config = configuration()
    config["players"] = [
        {"profile_id": "baseline", "config": {"time_limit": 0.01}},
        {
            "name": "This alias is ignored",
            "profile_id": "alpha_beta",
            "config": {"time_limit": 0.01, "depth": 1},
        },
    ]
    created = launch(client, config)
    players = created["config"]["players"]
    assert [player["name"] for player in players] == ["Shield bearer", "Jarl"]
    assert all(player["name"] == player["profile_name"] for player in players)
    wait_for(client, created["id"], lambda row: row["status"] == "completed")


@pytest.mark.parametrize(
    ("profile_id", "expected"),
    [
        ("baseline", {"algorithm": "baseline", "time_limit": 1.0}),
        ("alpha_beta", {"algorithm": "alpha_beta", "depth": 5, "time_limit": 2.0}),
        (
            "mcts",
            {
                "algorithm": "mcts",
                "iterations": 5000,
                "temperature": 0.0,
                "exploration": 1.4,
                "time_limit": 2.0,
            },
        ),
    ],
)
def test_tournament_named_presets_have_fixed_budgets(client, monkeypatch, profile_id, expected):
    from hnefatafl.tournaments import TournamentManager

    monkeypatch.setattr(
        TournamentManager, "launch", lambda _manager, _name, config: {"config": config}
    )
    response = client.post(
        "/api/tournaments",
        json=configuration(
            players=[
                {
                    "profile_id": profile_id,
                    "config": {
                        "algorithm": "baseline",
                        "depth": 1,
                        "iterations": 2,
                        "time_limit": 0.01,
                        "temperature": 1,
                        "exploration": 0.2,
                    },
                },
                {"config": {"algorithm": "alpha_beta", "depth": 3, "time_limit": 0.2}},
                {"config": {"algorithm": "baseline", "time_limit": 0.03}},
            ]
        ),
    )
    assert response.status_code == 201, response.text
    players = response.json()["config"]["players"]
    for key, value in expected.items():
        assert players[0]["config"][key] == value
    assert players[1]["config"]["algorithm"] == "alpha_beta"
    assert players[1]["config"]["depth"] == 3
    assert players[1]["config"]["time_limit"] == 0.2
    assert players[2]["config"]["algorithm"] == "baseline"
    assert players[2]["config"]["time_limit"] == 0.03


def test_tournament_export_contains_configuration_and_results_without_server_metadata(
    client, monkeypatch
):
    from hnefatafl.tournaments import TournamentManager

    record = launch(client, configuration())
    record = wait_for(client, record["id"], lambda row: row["status"] == "completed")
    response = client.get(f"/api/tournaments/{record['id']}/export")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/json")
    assert "attachment" in response.headers["content-disposition"]
    document = response.json()
    assert document["format"] == "hnefatafl-tournament-configuration"
    assert document["format_version"] == 1
    assert document["name"] == record["name"]
    assert document["rulesets"] == record["config"]["rulesets"]
    assert document["games"] == record["config"]["games"]
    assert document["max_plies"] == record["config"]["max_plies"]
    assert document["seed"] == record["config"]["seed"]
    for player in document["players"]:
        assert set(player) <= {"profile_id", "config"}
        assert player["config"]["algorithm"]
    assert document["results"]["status"] == "completed"
    assert document["results"]["result"]["games"]
    # Server-managed identifiers, worker PID and runes never leak into the export.
    serialized = json.dumps(document)
    assert record["id"] not in serialized
    assert '"pid"' not in serialized and "agent_rune" not in serialized
    # The exported configuration round-trips as a fresh, valid request.
    body = {
        key: document[key] for key in ("name", "rulesets", "players", "games", "max_plies", "seed")
    }
    monkeypatch.setattr(
        TournamentManager, "launch", lambda _manager, name, config: {"name": name, "config": config}
    )
    recreated = client.post("/api/tournaments", json=body)
    assert recreated.status_code == 201, recreated.text


def test_custom_tournament_mcts_keeps_explicit_budget(client, monkeypatch):
    from hnefatafl.tournaments import TournamentManager

    monkeypatch.setattr(
        TournamentManager, "launch", lambda _manager, _name, config: {"config": config}
    )
    response = client.post(
        "/api/tournaments",
        json=configuration(
            players=[
                {"config": {"algorithm": "mcts", "iterations": 33, "time_limit": 7.5}},
                {"config": {"algorithm": "baseline", "time_limit": 0.03}},
            ]
        ),
    )
    assert response.status_code == 201, response.text
    players = response.json()["config"]["players"]
    assert players[0]["config"]["algorithm"] == "mcts"
    assert players[0]["config"]["iterations"] == 33
    assert players[0]["config"]["time_limit"] == 7.5
