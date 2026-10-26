"""Game randomness, removals and explicit cleanup of abandoned model references."""

from concurrent.futures import ThreadPoolExecutor
import threading

import pytest
from auth_support import AuthenticatedClient as TestClient

from hnefatafl.engine import legal_moves
from hnefatafl.server import create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as session:
        yield session


def create(client, **changes):
    response = client.post("/api/games", json={"ruleset": "brandubh", **changes})
    assert response.status_code == 201, response.text
    return response.json()


def register_model(client, tmp_path):
    from hnefatafl.models import ModelRegistry, PolicyValueNet, save_checkpoint

    original = save_checkpoint(
        tmp_path / "original.pt", PolicyValueNet("brandubh", 4, 0), {"name": "Hugin"}
    )
    model = ModelRegistry(client.app.state.data_dir / "models").register(original)
    return model, original


def model_game(client, model, **changes):
    return create(
        client,
        mode="ai-ai",
        players={
            "attackers": {
                "name": "Hugin",
                "profile_id": "neural",
                "config": {"model_id": model["id"], "iterations": 1, "time_limit": 0.01},
            },
            "defenders": {"name": "Jarl", "profile_id": "baseline"},
        },
        **changes,
    )


def complete(client, game, monkeypatch):
    # Use the real engine's seeded tactical policy for a short rule-terminal
    # record. Neural execution itself has separate model/tournament coverage.
    from hnefatafl.ai import choose_move

    def tactical(state, _config, *, diagnostics=None):
        return choose_move(
            state, {"algorithm": "baseline", "seed": 3 + state.ply}, diagnostics=diagnostics
        )

    monkeypatch.setattr("hnefatafl.ai.choose_move", tactical)
    for _ in range(100):
        if game["winner"]:
            return game
        response = client.post(
            f"/api/games/{game['id']}/ai-move", json={"version": game["version"]}
        )
        assert response.status_code == 200, response.text
        game = response.json()
    pytest.fail("The tactical fixture did not reach a rule outcome")


def test_new_games_get_random_saved_seeds_and_override_stale_profile_seeds(client, monkeypatch):
    seeds = iter([123, 987])
    monkeypatch.setattr("hnefatafl.server.secrets.randbits", lambda bits: next(seeds))
    first = create(
        client,
        mode="ai-ai",
        players={"attackers": {"config": {"algorithm": "baseline", "seed": 42}}},
    )
    second = create(client, mode="ai-ai")
    assert first["seed"] == 123 and second["seed"] == 987
    assert first["seed"] != second["seed"]
    assert all(player["config"]["seed"] == 123 for player in first["players"].values())
    assert all(player["config"]["seed"] == 987 for player in second["players"].values())
    assert client.get(f"/api/games/{first['id']}").json() == first


@pytest.mark.parametrize("seed", [True, -1, 2**32, 1.2, "17"])
def test_fixed_game_seed_is_strict_unsigned_integer(client, seed):
    response = client.post("/api/games", json={"seed": seed})
    assert response.status_code == 422
    assert client.get("/api/games").json() == []


def test_explicit_seed_is_reproducible_and_per_move_seed_wraps_without_mutating_base(
    client, monkeypatch
):
    observed = []

    def choose(state, config, *, diagnostics=None):
        observed.append(config["seed"])
        diagnostics.update(value=0)
        return legal_moves(state)[0]

    monkeypatch.setattr("hnefatafl.ai.choose_move", choose)
    games = [create(client, mode="ai-ai", seed=2**32 - 1) for _ in range(2)]
    records = []
    for game in games:
        for _ in range(2):
            response = client.post(
                f"/api/games/{game['id']}/ai-move", json={"version": game["version"]}
            )
            assert response.status_code == 200
            game = response.json()
        assert game["seed"] == 2**32 - 1
        assert all(player["config"]["seed"] == game["seed"] for player in game["players"].values())
        records.append(game["moves"])
    assert observed == [2**32 - 1, 0, 2**32 - 1, 0]
    assert records[0] == records[1]


def test_restart_uses_new_seed_even_if_random_source_repeats_and_preserves_old_game(
    client, monkeypatch
):
    first = create(client, mode="ai-ai", seed=7)
    monkeypatch.setattr("hnefatafl.server.secrets.randbits", lambda _bits: 7)
    response = client.post(f"/api/games/{first['id']}/restart", json={"version": 0})
    assert response.status_code == 200
    restarted = response.json()
    assert restarted["seed"] != first["seed"]
    assert restarted["id"] != first["id"]
    assert all(p["config"]["seed"] == restarted["seed"] for p in restarted["players"].values())
    assert client.get(f"/api/games/{first['id']}").json() == first


def test_legacy_saved_game_preserves_original_constant_seed_semantics(client, monkeypatch):
    game = create(client, mode="ai-ai", seed=19)
    store = client.app.state.store
    legacy = store.get_game(game["id"])
    legacy.pop("seed")
    store.update_game(legacy, 0)
    observed = []

    def choose(state, config, *, diagnostics=None):
        observed.append(config["seed"])
        diagnostics.update(value=0)
        return legal_moves(state)[0]

    monkeypatch.setattr("hnefatafl.ai.choose_move", choose)
    for version in [1, 2]:
        response = client.post(f"/api/games/{game['id']}/ai-move", json={"version": version})
        assert response.status_code == 200
    assert observed == [19, 19]
    assert "seed" not in response.json()
    monkeypatch.setattr("hnefatafl.server.secrets.randbits", lambda _bits: 19)
    restarted = client.post(f"/api/games/{game['id']}/restart", json={"version": 3})
    assert restarted.status_code == 201 or restarted.status_code == 200
    assert restarted.json()["seed"] != 19


def test_completed_game_cannot_restart_at_terminal_or_earlier_replay_positions(client, monkeypatch):
    game = complete(client, create(client, mode="ai-ai", seed=3), monkeypatch)
    path = f"/api/games/{game['id']}"
    statistics = client.get("/api/stats").json()

    def denied(current):
        before = client.get("/api/stats").json()
        response = client.post(path + "/restart", json={"version": current["version"]})
        assert response.status_code == 409
        assert "Completed games cannot be restarted" in response.json()["detail"]
        assert client.get(path).json() == current
        assert len(client.get("/api/games").json()) == 1
        assert client.get("/api/stats").json() == before
        assert {key: value for key, value in before.items() if key != "recent_games"} == {
            key: value for key, value in statistics.items() if key != "recent_games"
        }

    denied(game)
    replay = client.post(path + "/seek", json={"version": game["version"], "cursor": 0}).json()
    assert replay["winner"] is None and replay["recorded_winner"]
    denied(replay)


@pytest.mark.parametrize("query", ["", "?version=-1", "?version=true", "?version=1.0", "?version="])
def test_game_removal_requires_an_unambiguous_version(client, query):
    game = create(client)
    response = client.delete(f"/api/games/{game['id']}{query}")
    assert response.status_code == 422
    assert client.get(f"/api/games/{game['id']}").status_code == 200


def test_remove_completed_replayed_game_updates_all_statistics_without_recording_loss(
    client, monkeypatch
):
    completed = complete(client, create(client, mode="ai-ai", seed=3), monkeypatch)
    # A replay cursor must not change whether the retained result is counted.
    response = client.post(
        f"/api/games/{completed['id']}/seek", json={"version": completed["version"], "cursor": 0}
    )
    replayed = response.json()
    assert replayed["winner"] is None and replayed["recorded_winner"]
    before = client.get("/api/stats").json()
    assert before["completed_games"] == 1
    assert before["by_ruleset"] and before["by_player"]
    assert client.delete(f"/api/games/{completed['id']}?version=0").status_code == 409
    removed = client.delete(f"/api/games/{completed['id']}?version={replayed['version']}")
    assert removed.status_code == 200
    assert removed.json() == {"deleted": True, "id": completed["id"]}
    assert client.get(f"/api/games/{completed['id']}").status_code == 404
    assert client.get(f"/api/games/{completed['id']}/replay").status_code == 404
    assert client.delete(f"/api/games/{completed['id']}?version=0").status_code == 404
    stats = client.get("/api/stats").json()
    assert stats["total_games"] == stats["completed_games"] == stats["in_progress"] == 0
    assert stats["attackers_wins"] == stats["defenders_wins"] == stats["draws"] == 0
    assert stats["by_ruleset"] == stats["by_player"] == stats["recent_games"] == []
    assert stats["attacker_win_rate"] == stats["defender_win_rate"] == stats["draw_rate"] == 0


def test_game_removal_during_ai_search_never_resurrects_the_record(client, monkeypatch):
    game = create(client, mode="ai-ai")
    entered, release = threading.Event(), threading.Event()

    def paused_choose(state, _config, *, diagnostics=None):
        entered.set()
        assert release.wait(5)
        diagnostics.update(value=0)
        return legal_moves(state)[0]

    monkeypatch.setattr("hnefatafl.ai.choose_move", paused_choose)
    with ThreadPoolExecutor(max_workers=1) as executor:
        moving = executor.submit(
            client.post, f"/api/games/{game['id']}/ai-move", json={"version": 0}
        )
        assert entered.wait(2)
        try:
            assert client.delete(f"/api/games/{game['id']}?version=0").status_code == 200
        finally:
            release.set()
        assert moving.result(timeout=5).status_code == 409
    assert client.get("/api/games").json() == []
    assert client.get("/api/stats").json()["total_games"] == 0


def test_human_ai_filters_select_same_games_as_statistics(client):
    human = create(client)
    both = create(client, mode="human-ai")
    ai = create(client, mode="ai-ai")
    for kind, expected in [("human", {human["id"], both["id"]}), ("ai", {ai["id"], both["id"]})]:
        games = client.get(f"/api/games?kind={kind}").json()
        assert {game["id"] for game in games} == expected
        assert client.get(f"/api/stats?kind={kind}").json()["total_games"] == len(expected)
    assert client.get("/api/games?kind=unknown").status_code == 400


def test_model_usage_lists_abandoned_server_games_and_explicit_discard_preserves_completed(
    client, tmp_path, monkeypatch
):
    model, original = register_model(client, tmp_path)
    completed = complete(client, model_game(client, model, seed=3), monkeypatch)
    replayed = client.post(
        f"/api/games/{completed['id']}/seek", json={"version": completed["version"], "cursor": 0}
    ).json()
    abandoned = model_game(client, model)
    other = create(client)
    usage = client.get(f"/api/models/{model['id']}/usage").json()
    assert usage["model_id"] == model["id"] and usage["usage_token"]
    assert usage["running_tournaments"] == []
    assert [game["id"] for game in usage["unfinished_games"]] == [abandoned["id"]]
    assert usage["unfinished_games"][0]["players"]["attackers"]["name"] == "Hugin"
    assert usage["unfinished_games"][0]["total_moves"] == 0
    refusal = client.delete(f"/api/models/{model['id']}")
    assert refusal.status_code == 409 and "saved on this server" in refusal.json()["detail"]
    removed = client.delete(
        f"/api/models/{model['id']}?discard_unfinished=true&usage_token={usage['usage_token']}"
    )
    assert removed.status_code == 200, removed.text
    assert removed.json()["discarded_game_ids"] == [abandoned["id"]]
    assert client.get(f"/api/games/{abandoned['id']}").status_code == 404
    assert client.get(f"/api/games/{completed['id']}").json() == replayed
    assert client.get(f"/api/games/{completed['id']}/replay").json()["validated"]
    assert client.get(f"/api/games/{other['id']}").status_code == 200
    stats = client.get("/api/stats").json()
    assert stats["total_games"] == 2 and stats["completed_games"] == 1
    assert stats["in_progress"] == 1
    assert original.exists()
    assert client.get(f"/api/models/{model['id']}/usage").status_code == 404


def test_model_discard_rechecks_the_preview_when_new_references_appear(client, tmp_path):
    model, _ = register_model(client, tmp_path)
    first = model_game(client, model)
    usage = client.get(f"/api/models/{model['id']}/usage").json()
    second = model_game(client, model)
    rejected = client.delete(
        f"/api/models/{model['id']}?discard_unfinished=true&usage_token={usage['usage_token']}"
    )
    assert rejected.status_code == 409
    assert {game["id"] for game in client.get("/api/games").json()} == {first["id"], second["id"]}
    usage = client.get(f"/api/models/{model['id']}/usage").json()
    removed = client.delete(
        f"/api/models/{model['id']}?discard_unfinished=true&usage_token={usage['usage_token']}"
    )
    assert removed.status_code == 200
    assert set(removed.json()["discarded_game_ids"]) == {first["id"], second["id"]}
    assert client.get("/api/stats").json()["total_games"] == 0


def test_model_file_failure_rolls_back_discard_and_historical_metadata(
    client, tmp_path, monkeypatch
):
    from hnefatafl.models import ModelRegistry

    model, _ = register_model(client, tmp_path)
    abandoned = model_game(client, model)
    history_before = client.app.state.store.historical_models()

    def fail_delete(_registry, _identifier):
        raise OSError("Cannot write registry")

    monkeypatch.setattr(ModelRegistry, "delete", fail_delete)
    removed = client.delete(f"/api/models/{model['id']}?discard_unfinished=true")
    assert removed.status_code == 500
    assert client.get(f"/api/games/{abandoned['id']}").json() == abandoned
    assert any(entry["id"] == model["id"] for entry in client.get("/api/models").json())
    assert client.app.state.store.historical_models() == history_before
    assert client.get("/api/stats").json()["total_games"] == 1


def test_running_tournament_still_blocks_explicit_model_discard(client, tmp_path):
    model, _ = register_model(client, tmp_path)
    response = client.post(
        "/api/tournaments",
        json={
            "name": "Active model evaluation",
            "rulesets": ["brandubh"],
            "players": [
                {
                    "name": "Hugin",
                    "profile_id": "neural",
                    "config": {"model_id": model["id"], "iterations": 1, "time_limit": 0.01},
                },
                {
                    "name": "Jarl",
                    "config": {"algorithm": "alpha_beta", "depth": 5, "time_limit": 1},
                },
            ],
            "games": 100,
            "max_plies": 1000,
            "seed": 3,
        },
    )
    assert response.status_code == 201, response.text
    tournament = response.json()
    try:
        usage = client.get(f"/api/models/{model['id']}/usage").json()
        assert usage["running_tournaments"] == [
            {"id": tournament["id"], "name": tournament["name"], "status": "running"}
        ]
        removed = client.delete(f"/api/models/{model['id']}?discard_unfinished=true")
        assert removed.status_code == 409 and "tournament" in removed.json()["detail"]
        assert client.get("/api/models").json()
    finally:
        assert client.post(f"/api/tournaments/{tournament['id']}/cancel").status_code == 200
