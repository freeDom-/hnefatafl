"""Persistent, non-recycled presentation identities across catalog and history."""

from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
import time

from auth_support import AuthenticatedClient as TestClient
from record_fixtures import stored_player, retired_player, historical_game, historical_response

from hnefatafl.identities import RUNES, effective_ai_config, rune_for_ordinal
from hnefatafl.models import ModelRegistry, PolicyValueNet, save_checkpoint
from hnefatafl.server import create_app
from hnefatafl.storage import Store


def test_neural_wrapper_effective_settings_preserve_legacy_mcts_defaults():
    original = {"algorithm": "neural", "model_id": "registered"}
    legacy = effective_ai_config(original)
    assert legacy == effective_ai_config({**original, "search_wrapper": "mcts"})
    assert "search_wrapper" not in legacy
    assert legacy["iterations"] == 80
    alpha = effective_ai_config(
        {**original, "search_wrapper": "alpha_beta", "depth": 3, "iterations": 12}
    )
    assert alpha["algorithm"] == "neural" and alpha["search_wrapper"] == "alpha_beta"
    assert alpha["depth"] == 3 and "iterations" not in alpha
    policy = effective_ai_config(
        {**original, "search_wrapper": "none", "temperature": 0.8, "depth": 3}
    )
    assert policy["temperature"] == 0.8 and policy["evaluation"] == "neural"
    assert "depth" not in policy and "iterations" not in policy


def test_allocator_is_unique_past_single_alphabet_and_safe_across_connections(tmp_path):
    path = tmp_path / "identities.sqlite3"
    store = Store(path)
    reserved = store.runes(f"player:{index}" for index in range(80))
    assert list(reserved.values())[:24] == list(RUNES)
    assert len(set(reserved.values())) == 80
    assert rune_for_ordinal(24) == RUNES[0] * 2
    stores = [Store(path) for _ in range(4)]
    with ThreadPoolExecutor(max_workers=4) as executor:
        values = list(
            executor.map(lambda index: stores[index % 4].rune(f"new-model:{index % 12}"), range(48))
        )
    assert len(set(values)) == 12
    assert not set(values).intersection(reserved.values())
    assert Store(path).runes(reserved) == reserved


def test_schema_four_migration_keeps_game_json_and_deleted_human_runes(tmp_path):
    path = tmp_path / "history.sqlite3"
    store = Store(path)
    stored_player(store, {"id": "retired", "name": "Astrid", "created_at": "2026-01-01"})
    retired_player(store, "retired", "2026-01-02")
    game = {"id": "old", "created_at": "2026-01-01", "version": 7, "moves": []}
    store.create_game(game)
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE entity_runes")
        connection.execute("PRAGMA user_version=4")
        old_json = connection.execute("SELECT data FROM games WHERE id='old'").fetchone()[0]
    migrated = Store(path)
    retired = migrated.players(include_deleted=True)[0]
    fresh = stored_player(migrated, {"id": "new", "name": "Astrid", "created_at": "2026-01-03"})
    assert fresh["rune"] != retired["rune"]
    assert Store(path).players(include_deleted=True)[0]["rune"] == fresh["rune"]
    assert migrated.rune("player:retired") == retired["rune"]
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 10
        assert connection.execute("SELECT data FROM games WHERE id='old'").fetchone()[0] == old_json


def test_legacy_game_response_backfills_runes_without_rewriting_payload(tmp_path):
    app = create_app(tmp_path)
    with TestClient(app) as client:
        game = historical_game(client, ruleset="brandubh")
        database = tmp_path / "hnefatafl.sqlite3"
        with sqlite3.connect(database) as connection:
            saved = json.loads(
                connection.execute("SELECT data FROM games WHERE id=?", (game["id"],)).fetchone()[0]
            )
            for player in saved["players"].values():
                player.pop("rune", None)
            old_json = json.dumps(saved)
            connection.execute("UPDATE games SET data=? WHERE id=?", (old_json, game["id"]))
        loaded = client.get(f"/api/games/{game['id']}").json()
        profiles = {player["id"]: player for player in client.app.state.store.players()}
        for player in loaded["players"].values():
            assert player["rune"] == profiles[player["player_id"]]["rune"]
        assert loaded["players"]["attackers"]["rune"] != loaded["players"]["defenders"]["rune"]
        with sqlite3.connect(database) as connection:
            assert (
                connection.execute("SELECT data FROM games WHERE id=?", (game["id"],)).fetchone()[0]
                == old_json
            )


def test_model_rune_is_shared_by_playing_sides_advice_and_analysis(tmp_path):
    original = tmp_path / "original.pt"
    save_checkpoint(original, PolicyValueNet("brandubh", 4, 0), {"name": "Eir"})
    registered = ModelRegistry(tmp_path / "models").register(original, "Eir")
    with TestClient(create_app(tmp_path)) as client:
        model = client.get("/api/models").json()[0]
        assert model["id"] == registered["id"]
        agent = {
            "name": "Eir",
            "profile_id": "neural",
            "config": {"model_id": model["id"], "iterations": 1, "time_limit": 0.05},
        }
        created = historical_response(
            client,
            json={
                "ruleset": "brandubh",
                "mode": "ai-ai",
                "players": {"attackers": agent, "defenders": agent},
                "advisor": agent,
            },
        )
        assert created.status_code == 200, created.text
        game = created.json()
        assert {player["rune"] for player in game["players"].values()} == {model["rune"]}
        assert game["advisor"]["rune"] == model["rune"]
        report = client.post(
            f"/api/games/{game['id']}/analysis", json={"version": 0, "cursor": 0}
        ).json()
        assert {agent["rune"] for agent in report["evaluations"]} == {model["rune"]}
        assert client.app.state.store.players() == []
    with TestClient(create_app(tmp_path)) as reloaded:
        assert reloaded.get("/api/models").json()[0]["rune"] == model["rune"]
        assert reloaded.get(f"/api/games/{game['id']}").json()["analysis"] == report


def test_tournament_uses_shared_agent_rune_and_survives_deletion(tmp_path):
    app = create_app(tmp_path)
    with TestClient(app) as client:
        game = historical_response(
            client, json={"ruleset": "brandubh", "mode": "ai-ai", "seed": 3}
        ).json()
        agent_rune = game["players"]["attackers"]["rune"]
        assert game["players"]["defenders"]["rune"] == agent_rune
        config = {
            "name": "Rune gathering",
            "rulesets": ["brandubh"],
            "games": 2,
            "max_plies": 1,
            "players": [
                {"name": name, "config": game["players"]["attackers"]["config"]}
                for name in ("Hugin", "Munin")
            ],
        }
        created = client.post("/api/tournaments", json=config)
        assert created.status_code == 201, created.text
        tournament = created.json()
        players = tournament["config"]["players"]
        assert {player["rune"] for player in players} == {agent_rune}
        assert {player["agent_rune"] for player in players} == {agent_rune}
        for _ in range(200):
            saved = client.get(f"/api/tournaments/{tournament['id']}").json()
            assert saved["config"]["players"] == players
            if saved["status"] == "completed":
                break
            time.sleep(0.025)
        assert saved["status"] == "completed", saved
        assert client.delete(f"/api/tournaments/{tournament['id']}").status_code == 200
        assert (
            client.get(f"/api/games/{game['id']}").json()["players"]["attackers"]["rune"]
            == agent_rune
        )


def test_legacy_tournament_uses_effective_agent_identity_without_rewriting_config(tmp_path):
    app = create_app(tmp_path)
    with TestClient(app) as client:
        config = {"algorithm": "baseline", "time_limit": 0.01}
        game = historical_response(
            client,
            json={
                "mode": "ai-ai",
                "ruleset": "brandubh",
                "players": {
                    side: {"name": side, "config": config} for side in ("attackers", "defenders")
                },
            },
        ).json()
        old = {
            "id": "old-report",
            "name": "Old gathering",
            "status": "completed",
            "created_at": "2026-01-01",
            "config": {"players": [{"name": "Old Hugin", "config": config}]},
            "result": {"games": []},
        }
        app.state.store.save_tournament(old)
        saved = client.get("/api/tournaments/old-report").json()
        player = saved["config"]["players"][0]
        assert player["agent_rune"] == game["players"]["attackers"]["rune"]
        assert player["rune"] == player["agent_rune"]
        assert app.state.store.get_tournament("old-report") == old
