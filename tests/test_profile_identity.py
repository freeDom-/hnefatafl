"""Shared display names never replace canonical public account attribution."""

import pytest

from test_personal import online, seed as seed, world as world


@pytest.mark.parametrize("name", ["Odin", "Shared Þór 東京", "😀" * 80])
def test_colliding_shared_names_keep_public_usernames_and_stable_authority(world, name):
    app, clients, _, accounts, _ = world
    game = online(world)
    with app.state.store.connection() as c:
        for player in ("Archer", "Keeper", "Odin"):
            c.execute(
                "INSERT INTO player_ratings VALUES(?, 'brandubh', 1200, 1, 1, 0, 0, 1, 0)",
                (accounts[player]["id"],),
            )
    baseline = clients["Archer"].get("/api/leaderboard?ruleset=brandubh").json()
    order = [entry["account_id"] for entry in baseline["entries"]]
    for player in ("Archer", "Keeper"):
        response = clients[player].patch("/api/profile", json={"display_name": name})
        assert response.status_code == 200
        assert not response.json()["show_display_name"]

    for shared in (False, True, False):
        for player in ("Archer", "Keeper"):
            assert (
                clients[player]
                .patch("/api/profile", json={"show_display_name": shared})
                .status_code
                == 200
            )
        response = clients["Archer"].get("/api/leaderboard?ruleset=brandubh")
        assert response.headers["cache-control"] == "no-store"
        board = response.json()
        assert [entry["account_id"] for entry in board["entries"]] == order
        for entry in board["entries"]:
            player = entry["username"]
            assert entry["account_id"] == accounts[player]["id"]
            assert entry["name"] == (name if shared and player != "Odin" else player)
            assert entry["rating"] == 1200
            assert set(entry) == {
                "account_id",
                "name",
                "username",
                "rating",
                "rated_games",
                "wins",
                "losses",
                "draws",
                "rank",
                "provisional",
            }
        assert board["me"] == next(
            entry for entry in board["entries"] if entry["username"] == "Archer"
        )
        current = clients["Observer"].get(f"/api/online/games/{game['id']}").json()
        assert current["version"] == game["version"]
        for side, player in (("attackers", "Archer"), ("defenders", "Keeper")):
            assert current["participants"][side] == {
                "account_id": accounts[player]["id"],
                "name": name if shared else player,
                "username": player,
            }
        expected = {
            side: f"{name} (@{player})" if shared else player
            for side, player in (("attackers", "Archer"), ("defenders", "Keeper"))
        }
        personal = clients["Archer"].get("/api/personal/online").json()["games"][0]
        assert personal["players"] == expected
        history = clients["Archer"].get("/api/history/games").json()["games"][0]
        assert {side: history[side] for side in expected} == expected
        assert not any(key.endswith("_username") for key in history)
        filtered = clients["Odin"].get("/api/history/games?player=Archer").json()
        assert filtered["matching"] == 1
        assert filtered["games"][0]["id"] == game["id"]
        for player in ("Archer", "Keeper", "Odin"):
            account = app.state.auth.account(accounts[player]["id"])
            assert account["username"] == player
            assert account["role"] == ("ADMIN" if player == "Odin" else "USER")


def test_equal_display_and_username_needs_no_duplicate_attribution(world):
    _, clients, _, _, _ = world
    game = online(world)
    assert (
        clients["Archer"]
        .patch("/api/profile", json={"display_name": "Archer", "show_display_name": True})
        .status_code
        == 200
    )
    history = clients["Keeper"].get("/api/history/games").json()["games"][0]
    assert history["id"] == game["id"] and history["attackers"] == "Archer"
    personal = clients["Archer"].get("/api/personal/online").json()["games"][0]
    assert personal["players"]["attackers"] == "Archer"
