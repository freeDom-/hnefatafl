"""Creation policy is enforced independently of the dialog and retained read DTOs."""

import pytest
from auth_support import AuthenticatedClient
from record_fixtures import historical_game

from hnefatafl.ai import ai_profiles
from hnefatafl.auth import COOKIE
from hnefatafl.identities import effective_ai_config
from hnefatafl.server import create_app


@pytest.fixture
def client(tmp_path):
    with AuthenticatedClient(create_app(tmp_path)) as session:
        yield session


def demote(client):
    account = client.app.state.auth.session(client.cookies[COOKIE])
    with client.app.state.store.connection() as c:
        c.execute("UPDATE accounts SET role='USER' WHERE id=?", (account["id"],))
    return account


def test_normal_creation_binds_account_identity_and_resolves_all_four_presets(client):
    account = demote(client)
    definitions = {p["id"]: p for p in ai_profiles()}
    for side in ("attackers", "defenders"):
        enemy = "defenders" if side == "attackers" else "attackers"
        for profile in ("baseline", "shield_maiden", "alpha_beta", "mcts"):
            response = client.post(
                "/api/games",
                json={
                    "ruleset": "brandubh",
                    "human_side": side,
                    "players": {enemy: {"profile_id": profile}},
                },
            )
            assert response.status_code == 201, response.text
            game = response.json()
            assert game["mode"] == "human-ai" and game["creation_policy"] == 1
            assert game["players"][side]["account_id"] == account["id"]
            assert game["players"][side]["name"] == account["username"]
            assert "player_id" not in game["players"][side]
            computer = game["players"][enemy]
            assert computer["name"] == definitions[profile]["name"]
            assert computer["config"] == effective_ai_config(
                {**definitions[profile]["parameters"], "algorithm": profile, "seed": game["seed"]}
            )
            stored = client.app.state.store.get_game(game["id"])
            assert stored["recorded_result"] is None
    with client.app.state.store.connection() as c:
        assert c.execute("SELECT count(*) FROM players").fetchone()[0] == 0
    assert (
        len({g["players"][g["human_side"]]["rune"] for g in client.get("/api/games").json()}) == 1
    )


def test_forged_creation_cannot_set_identity_configuration_or_admin_mode(client):
    demote(client)
    forbidden = [
        {"mode": "human-human"},
        {"seed": 7},
        {"advisor": {"profile_id": "baseline"}},
        {"players": {"defenders": {}}},
        {"players": {"attackers": {"profile_id": "neural"}}},
        {"players": {"attackers": {"profile_id": "custom"}}},
        {"players": {"attackers": {"profile_id": "baseline", "config": {"time_limit": 15}}}},
        {"players": {"attackers": {"name": "Another account"}}},
        {"players": {"attackers": {"account_id": "someone-else"}}},
        {"players": {"attackers": {"player_id": "someone-else"}}},
        {"players": {"intruder": {"profile_id": "mcts"}}},
        {"account_id": "someone-else"},
    ]
    for payload in forbidden:
        response = client.post("/api/games", json=payload)
        assert response.status_code == 422, (payload, response.text)
    assert client.post("/api/games", json={"mode": "ai-ai"}).status_code == 403
    assert client.get("/api/games").json() == []
    assert client.get("/api/players").status_code == 404
    for method in (client.post, client.delete):
        assert method("/api/players").status_code == 403


def test_administrator_ai_pairs_use_presets_but_neural_catalog_remains_for_workshops(client):
    response = client.post(
        "/api/games",
        json={
            "mode": "ai-ai",
            "players": {
                "attackers": {"profile_id": "shield_maiden"},
                "defenders": {"profile_id": "mcts"},
            },
        },
    )
    assert response.status_code == 201, response.text
    assert all(p["kind"] == "ai" for p in response.json()["players"].values())
    assert "neural" in {p["id"] for p in client.get("/api/ai/profiles").json()}
    assert (
        client.post(
            "/api/games", json={"mode": "ai-ai", "players": {"attackers": {"profile_id": "neural"}}}
        ).status_code
        == 422
    )
    demote(client)
    game = response.json()
    assert client.post(f"/api/games/{game['id']}/restart", json={"version": 0}).status_code == 403
    assert len(client.get("/api/games").json()) == 1


def test_role_is_rechecked_inside_creation_transaction(client, monkeypatch):
    core = client.app.state.online
    original = core._identity

    def freshly_demoted(connection, identity):
        connection.execute("UPDATE accounts SET role='USER' WHERE username='Odin'")
        return original(connection, identity)

    monkeypatch.setattr(core, "_identity", freshly_demoted)
    assert client.post("/api/games", json={"mode": "ai-ai"}).status_code == 403
    with client.app.state.store.connection() as c:
        assert c.execute("SELECT count(*) FROM games").fetchone()[0] == 0


def test_historical_modes_and_custom_agents_remain_readable_playable_but_cannot_restart(client):
    for mode in ("human-human", "ai-ai"):
        game = historical_game(
            client,
            ruleset="brandubh",
            mode=mode,
            players={"attackers": {"config": {"algorithm": "baseline", "time_limit": 0.01}}},
        )
        before = len(client.get("/api/games").json())
        assert (
            client.post(f"/api/games/{game['id']}/restart", json={"version": 0}).status_code == 409
        )
        assert len(client.get("/api/games").json()) == before
        path = f"/api/games/{game['id']}"
        if mode == "human-human":
            moved = client.post(path + "/moves", json={**game["legal_moves"][0], "version": 0})
        else:
            moved = client.post(path + "/ai-move", json={"version": 0})
        assert moved.status_code == 200, moved.text
        assert moved.json()["total_moves"] == 1
        assert client.get(path + "/replay").json()["validated"]
