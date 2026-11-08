"""Shared display names never replace canonical public account attribution."""

from contextlib import contextmanager
from datetime import timedelta
from uuid import uuid4

import pytest

from hnefatafl.auth import digest
from record_fixtures import historical_response

from test_personal import online, seed as seed, world as world


def waiting_history(world, player, count=1):
    app, _, credentials, _, clock = world
    identifiers = set()
    for _ in range(count):
        clock[0] += timedelta(microseconds=1)
        game = app.state.online.create(
            credentials[player], "brandubh", "attackers", 60, str(uuid4())
        )["game"]
        identifiers.add(game["id"])
    return identifiers


def share_name(client, name, enabled=True):
    response = client.patch("/api/profile", json={"display_name": name if enabled else None})
    assert response.status_code == 200


def test_history_search_accepts_visible_labels_and_preserves_literal_private_matching(world):
    _, clients, _, _, _ = world
    name = "Shared %_ ' Þór😀"
    for player in ("Archer", "Keeper"):
        share_name(clients[player], name)
    archer = waiting_history(world, "Archer")
    keeper = waiting_history(world, "Keeper")
    waiting_history(world, "Observer")

    def matches(query):
        response = clients["Odin"].get("/api/history/games", params={"player": query})
        assert response.status_code == 200
        result = response.json()
        identifiers = {game["id"] for game in result["games"]}
        assert result["matching"] == len(identifiers)
        return identifiers

    for query in (name, "Shared", "%", "_", "'"):
        assert matches(query) == archer | keeper
    for query in ("Archer", "archer", "@Archer", "(@Archer)", f"{name} (@Archer)"):
        assert matches(query) == archer
    assert matches(f"{name} (@Keeper)") == keeper
    assert matches("' OR 1=1 --") == set()
    share_name(clients["Archer"], "Private secret", False)
    assert matches("Private secret") == set()
    assert matches(f"{name} (@Archer)") == set()
    assert matches(name) == keeper
    assert matches("Archer") == archer
    share_name(clients["Archer"], name)
    assert matches(f"{name} (@Archer)") == archer


def test_attributed_history_search_keeps_counts_pages_cursors_and_authorization(world):
    app, clients, _, accounts, _ = world
    for player in ("Archer", "Keeper"):
        share_name(clients[player], "Same name")
    expected = waiting_history(world, "Archer", 5)
    waiting_history(world, "Keeper", 2)
    query = {
        "player": "Same name (@Archer)",
        "limit": 2,
        "category": "online",
        "ruleset": "brandubh",
        "status": "waiting",
    }
    first = clients["Odin"].get("/api/history/games", params=query).json()
    assert first["matching"] == 5 and len(first["games"]) == 2
    assert first["totals"][0]["games"] == 5
    assert clients["Odin"].get("/api/history/games", params=query).json() == first
    seen, result = set(), first
    while True:
        identifiers = {game["id"] for game in result["games"]}
        assert not seen & identifiers
        seen |= identifiers
        assert result["matching"] == 5 and result["totals"] == first["totals"]
        if not result["next_cursor"]:
            break
        result = (
            clients["Odin"]
            .get("/api/history/games", params={**query, "cursor": result["next_cursor"]})
            .json()
        )
    assert seen == expected
    changed = clients["Odin"].get(
        "/api/history/games",
        params={
            **query,
            "player": "Same name (@Keeper)",
            "cursor": first["next_cursor"],
        },
    )
    assert changed.status_code == 422
    for player in ("Archer", "Keeper", "Observer"):
        assert clients[player].get("/api/history/games", params=query).status_code == 403
        assert (
            clients[player]
            .get("/api/history/games", params={**query, "dashboard": True})
            .status_code
            == 403
        )
    own = clients["Archer"].get("/api/history/games").json()
    assert {game["id"] for game in own["games"]} == expected
    assert clients["Odin"].get("/api/history/games?dashboard=true").json()["games"] == []
    with app.state.store.connection() as c:
        c.execute("UPDATE accounts SET role='USER' WHERE id=?", (accounts["Odin"]["id"],))
    assert clients["Odin"].get("/api/history/games", params=query).status_code == 403
    clients["Observer"].cookies.clear()
    assert clients["Observer"].get("/api/history/games", params=query).status_code == 401


def test_search_accepts_maximum_unicode_attributed_label_and_bounds_longer_queries(world):
    app, clients, _, _, _ = world
    username, name = "W" * 64, "😀" * 80
    account = app.state.auth.create_account(username, "long@example.test", "long-history-password")
    with app.state.store.connection() as c:
        c.execute(
            "UPDATE accounts SET display_name=?,email_verified_at=1 WHERE id=?",
            (name, account["id"]),
        )
    _, token, _ = app.state.auth.login(username, "long-history-password", "fixture")
    game = app.state.online.create(digest(token), "brandubh", "attackers", 60, str(uuid4()))["game"]
    records = clients["Odin"].get("/api/history/games").json()["games"]
    label = next(record["attackers"] for record in records if record["id"] == game["id"])
    assert label == f"{name} (@{username})" and len(label) == 148
    response = clients["Odin"].get("/api/history/games", params={"player": label})
    assert response.status_code == 200
    assert response.json()["matching"] == 1 and response.json()["games"][0]["id"] == game["id"]
    assert (
        clients["Odin"].get("/api/history/games", params={"player": label + "x"}).status_code == 422
    )


def test_history_search_reuses_current_owner_attribution_for_account_records(world):
    _, clients, _, _, _ = world
    name = "Shared account owner"
    share_name(clients["Archer"], name)
    game = historical_response(
        clients["Archer"], json={"ruleset": "brandubh", "mode": "human-human"}
    ).json()
    result = (
        clients["Odin"]
        .get(
            "/api/history/games",
            params={
                "category": "account-single-player",
                "player": f"{name} (@Archer)",
            },
        )
        .json()
    )
    assert result["matching"] == 1 and result["games"][0]["id"] == game["id"]
    assert result["games"][0]["owner"] == f"{name} (@Archer)"


def test_history_search_query_count_does_not_grow_with_page_size(world, monkeypatch):
    app, clients, _, _, _ = world
    share_name(clients["Archer"], "Shared name")
    waiting_history(world, "Archer", 5)
    statements = []
    connection = app.state.store.connection

    @contextmanager
    def traced():
        with connection() as c:
            c.set_trace_callback(statements.append)
            yield c

    monkeypatch.setattr(app.state.store, "connection", traced)
    counts = []
    for limit in (1, 5):
        statements.clear()
        result = clients["Odin"].get(
            "/api/history/games",
            params={
                "player": "Shared name (@Archer)",
                "limit": limit,
            },
        )
        assert result.status_code == 200 and len(result.json()["games"]) == limit
        counts.append(
            sum(sql.lstrip().upper().startswith(("SELECT", "WITH")) for sql in statements)
        )
    assert counts[0] == counts[1] > 0


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
        assert response.json()["legacy_private_fields"] == []

    for shared in (False, True, False):
        for player in ("Archer", "Keeper"):
            assert (
                clients[player]
                .patch("/api/profile", json={"display_name": name if shared else None})
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
        clients["Archer"].patch("/api/profile", json={"display_name": "Archer"}).status_code == 200
    )
    history = clients["Keeper"].get("/api/history/games").json()["games"][0]
    assert history["id"] == game["id"] and history["attackers"] == "Archer"
    personal = clients["Archer"].get("/api/personal/online").json()["games"][0]
    assert personal["players"]["attackers"] == "Archer"
