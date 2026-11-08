"""Current public identities are read projections, never edits to game history."""

from contextlib import contextmanager

import pytest

from hnefatafl.account_policy import attributed_name
from record_fixtures import historical_game
from test_personal import _finish_account, online, seed as seed, world as world
from test_profile_identity import share_name
from test_game_management import register_model


def create(client, side="attackers"):
    response = client.post("/api/games", json={"ruleset": "brandubh", "human_side": side})
    assert response.status_code == 201, response.text
    return response.json()


def stored_game(app, identifier):
    with app.state.store.connection() as c:
        return tuple(c.execute("SELECT * FROM games WHERE id=?", (identifier,)).fetchone())


def assert_identity(player, name, username="Archer"):
    assert player["name"] == name and player.get("username") == username
    assert not {"email", "birth_year", "role", "password_hash", "display_name"} & player.keys()


@pytest.mark.parametrize("side", ["attackers", "defenders"])
@pytest.mark.parametrize("completed", [False, True])
def test_new_and_legacy_linked_games_resolve_every_read_without_game_writes(world, side, completed):
    app, clients, _, accounts, _ = world
    client = clients["Archer"]
    share_name(client, "Thor")
    game = create(client, side)
    identifier = game["id"]
    assert_identity(game["players"][side], "Thor")
    saved = app.state.store.get_game(identifier)
    assert saved["players"][side] == {
        "name": "Archer",
        "account_id": accounts["Archer"]["id"],
        "kind": "human",
        "config": {},
    }
    # A PR #61 record already has a trustworthy ID, but contains the former name.
    with app.state.store.connection() as c:
        c.execute(
            "UPDATE games SET data=json_set(data,?,?) WHERE id=?",
            (f"$.players.{side}.name", "Thor", identifier),
        )
    if completed:
        _finish_account(app, identifier, side)
    before = stored_game(app, identifier)
    totals = client.get("/api/personal/single-player").json()["statistics"]
    for name, shared, expected in (
        ("Freya", True, "Freya"),
        ("Private secret", False, "Archer"),
        ("Þór 東京 😀", True, "Þór 東京 😀"),
        ("😀" * 80, True, "😀" * 80),
        (None, True, "Archer"),
        ("Odin", True, "Odin"),
    ):
        share_name(client, name, shared)
        detail = client.get(f"/api/games/{identifier}").json()
        listing = client.get("/api/games").json()[0]
        stats = client.get("/api/stats").json()
        history = client.get("/api/personal/single-player").json()
        personal_replay = client.get(f"/api/personal/single-player/{identifier}/replay").json()
        review = clients["Odin"].get(f"/api/history/account-single-player/{identifier}/replay")
        assert review.status_code == 200, review.text
        for player in (
            detail["players"][side],
            listing["players"][side],
            stats["recent_games"][0]["players"][side],
            history["games"][0]["players"][side],
            personal_replay["game"]["players"][side],
            review.json()["game"]["players"][side],
        ):
            assert_identity(player, expected)
        if completed:
            human = next(p for p in stats["by_player"] if p["kind"] == "human")
            assert_identity(human, expected)
            assert human["id"] == "account:" + accounts["Archer"]["id"]
            dashboard = client.get("/api/history/games?dashboard=true").json()["games"][0]
            assert dashboard[side] == attributed_name(expected, "Archer")
        hall = clients["Odin"].get("/api/history/games").json()["games"][0]
        assert hall[side] == attributed_name(expected, "Archer")
        assert personal_replay["validated"] and personal_replay["version"] == game["version"]
        assert history["statistics"] == totals
        assert stored_game(app, identifier) == before
        assert detail["version"] == game["version"]
    restarted = client.post(f"/api/games/{identifier}/restart", json={"version": 0})
    if completed:
        assert restarted.status_code == 409
        assert stored_game(app, identifier) == before
        return
    assert restarted.status_code == 200, restarted.text
    assert_identity(restarted.json()["players"][side], "Odin")
    fresh = app.state.store.get_game(restarted.json()["id"])
    assert fresh["players"][side]["name"] == "Archer"
    assert fresh["players"][side]["account_id"] == accounts["Archer"]["id"]
    assert stored_game(app, identifier) == before


def test_move_responses_remain_current_and_profile_edits_do_not_break_version_guards(world):
    _, clients, _, _, _ = world
    client = clients["Archer"]
    game = create(client)
    share_name(client, "Freya")
    moved = client.post(
        f"/api/games/{game['id']}/moves", json={**game["legal_moves"][0], "version": 0}
    )
    assert moved.status_code == 200, moved.text
    assert_identity(moved.json()["players"]["attackers"], "Freya")
    share_name(client, "Thor")
    assert (
        client.post(
            f"/api/games/{game['id']}/moves", json={**game["legal_moves"][0], "version": 0}
        ).status_code
        == 409
    )
    replay = client.get(f"/api/personal/single-player/{game['id']}/replay").json()
    assert replay["validated"] and replay["version"] == 1 and len(replay["moves"]) == 1
    assert_identity(replay["game"]["players"]["attackers"], "Thor")


def test_unlinked_forged_ai_and_missing_account_records_never_rebind_by_name(world):
    app, clients, _, accounts, _ = world
    client = clients["Archer"]
    share_name(client, "Current public name")
    share_name(clients["Keeper"], "Another account")
    legacy = historical_game(client, ruleset="brandubh", players={"attackers": {"name": "Archer"}})
    forged = create(client)
    missing = create(clients["Keeper"])
    with app.state.store.connection() as c:
        c.execute(
            "UPDATE games SET data=json_set(data,'$.players.attackers.account_id',?,"
            "'$.players.attackers.name','Historical stranger','$.players.attackers.username','Keeper',"
            "'$.players.defenders.account_id',?) WHERE id=?",
            (accounts["Keeper"]["id"], accounts["Archer"]["id"], forged["id"]),
        )
        c.execute(
            "UPDATE games SET data=json_set(data,'$.players.attackers.name','Private deleted name') WHERE id=?",
            (missing["id"],),
        )
        c.execute("DELETE FROM accounts WHERE id=?", (accounts["Keeper"]["id"],))
    unlinked = client.get(f"/api/games/{legacy['id']}").json()["players"]["attackers"]
    assert unlinked["name"] == "Archer" and "username" not in unlinked
    current = client.get(f"/api/games/{forged['id']}").json()["players"]
    assert current["attackers"]["name"] == "Historical stranger"
    assert "username" not in current["attackers"]
    assert current["defenders"]["name"] == forged["players"]["defenders"]["name"]
    assert current["defenders"]["kind"] == "ai" and "username" not in current["defenders"]
    unknown = clients["Odin"].get(f"/api/games/{missing['id']}").json()["players"]["attackers"]
    assert unknown["name"] == "" and "username" not in unknown
    replay = (
        clients["Odin"].get(f"/api/history/account-single-player/{missing['id']}/replay").json()
    )
    assert replay["game"]["players"]["attackers"]["name"] == ""
    for term, ids in (
        ("Archer", {legacy["id"], forged["id"]}),
        ("Historical stranger", {forged["id"]}),
        ("Private deleted name", set()),
        ("Another account", set()),
    ):
        result = clients["Odin"].get("/api/history/games", params={"player": term}).json()
        assert {g["id"] for g in result["games"]} == ids
    for viewer in ("Observer", "Archer"):
        if viewer == "Observer":
            assert clients[viewer].get(f"/api/games/{legacy['id']}").status_code == 404
        assert clients[viewer].get(f"/api/games/{missing['id']}").status_code == 404
    assert (
        client.post(
            f"/api/games/{forged['id']}/moves", json={**forged["legal_moves"][0], "version": 0}
        ).status_code
        == 403
    )


def test_hall_search_uses_current_linked_identity_and_keeps_pages_and_legacy_names(world):
    app, clients, _, _, _ = world
    client, admin = clients["Archer"], clients["Odin"]
    share_name(client, "Thor")
    games = [create(client) for _ in range(5)]
    share_name(clients["Keeper"], "Freya 😀")
    other = create(clients["Keeper"])
    legacy = historical_game(
        clients["Observer"], ruleset="brandubh", players={"attackers": {"name": "Thor"}}
    )
    with app.state.store.connection() as c:
        for game in games:
            c.execute(
                "UPDATE games SET data=json_set(data,'$.players.attackers.name','Thor') WHERE id=?",
                (game["id"],),
            )
    share_name(client, "Freya 😀")
    expected = {g["id"] for g in games}
    for query in ("Freya 😀 (@Archer)", "Archer", "@Archer"):
        params = {"player": query, "limit": 2, "category": "account-single-player"}
        seen = set()
        response = admin.get("/api/history/games", params=params).json()
        while True:
            assert response["matching"] == 5 and response["totals"][0]["games"] == 5
            identifiers = {g["id"] for g in response["games"]}
            assert not seen & identifiers
            seen |= identifiers
            if not response["next_cursor"]:
                break
            response = admin.get(
                "/api/history/games", params={**params, "cursor": response["next_cursor"]}
            ).json()
        assert seen == expected
    assert admin.get("/api/history/games", params={"player": "Freya 😀"}).json()["matching"] == 6
    assert (
        admin.get("/api/history/games", params={"player": "Freya 😀 (@Keeper)"}).json()["games"][0][
            "id"
        ]
        == other["id"]
    )
    assert {g["id"] for g in admin.get("/api/history/games?player=Thor").json()["games"]} == {
        legacy["id"]
    }
    share_name(client, "Freya 😀", False)
    assert (
        admin.get("/api/history/games", params={"player": "Freya 😀 (@Archer)"}).json()["matching"]
        == 0
    )
    assert admin.get("/api/history/games?player=@Archer").json()["matching"] == 0
    assert admin.get("/api/history/games?player=Archer").json()["matching"] == 5
    assert client.get("/api/history/games?player=Archer").status_code == 403
    assert {g["id"] for g in clients["Observer"].get("/api/history/games").json()["games"]} == {
        legacy["id"]
    }


@pytest.mark.parametrize("completed", [False, True])
def test_online_surfaces_keep_current_identity_without_changing_authoritative_records(
    world, completed
):
    app, clients, credentials, accounts, _ = world
    game = online(world)
    if completed:
        game = app.state.online.mutate(
            credentials["Archer"], game["id"], game["version"], "concede"
        )["game"]
    with app.state.store.connection() as c:
        c.execute(
            "INSERT OR REPLACE INTO player_ratings VALUES(?, 'brandubh', 1200, 1, 1, 0, 0, 1, 0)",
            (accounts["Archer"]["id"],),
        )
        before = tuple(c.execute("SELECT * FROM online_games WHERE id=?", (game["id"],)).fetchone())
    for name, shared in (
        ("Freya", True),
        ("Odin", True),
        ("😀" * 80, True),
        ("secret", False),
        (None, True),
    ):
        share_name(clients["Archer"], name, shared)
        expected = name if name and shared else "Archer"
        detail = clients["Observer"].get(f"/api/online/games/{game['id']}").json()
        listing = app.state.online.listing(credentials["Observer"])["games"][0]
        lobby = clients["Observer"].get("/api/online/games").json()["games"]
        assert bool(lobby) is not completed
        replay = clients["Archer"].get(f"/api/personal/online/{game['id']}/replay").json()
        for projected in (detail, listing, replay["game"]):
            assert_identity(projected["participants"]["attackers"], expected)
            assert projected["version"] == game["version"]
        section = "history" if completed else "current"
        personal = (
            clients["Archer"]
            .get("/api/personal/online", params={"section": section})
            .json()["games"][0]
        )
        hall = clients["Archer"].get("/api/history/games").json()["games"][0]
        assert (
            personal["players"]["attackers"]
            == hall["attackers"]
            == attributed_name(expected, "Archer")
        )
        board = clients["Archer"].get("/api/leaderboard?ruleset=brandubh").json()
        assert_identity(board["me"], expected)
        assert board["me"] == next(
            entry for entry in board["entries"] if entry["username"] == "Archer"
        )
        assert board["me"]["rating"] == 1200
        with app.state.store.connection() as c:
            assert (
                tuple(c.execute("SELECT * FROM online_games WHERE id=?", (game["id"],)).fetchone())
                == before
            )


def test_unavailable_online_account_keeps_its_seat_without_disclosing_saved_names(world):
    app, clients, credentials, accounts, _ = world
    share_name(clients["Keeper"], "Private deleted name")
    game = online(world)
    game = app.state.online.mutate(credentials["Archer"], game["id"], game["version"], "concede")[
        "game"
    ]
    with app.state.store.connection() as c:
        c.execute("DELETE FROM accounts WHERE id=?", (accounts["Keeper"]["id"],))
        before = tuple(c.execute("SELECT * FROM online_games WHERE id=?", (game["id"],)).fetchone())
    detail = clients["Observer"].get(f"/api/online/games/{game['id']}").json()
    replay = clients["Archer"].get(f"/api/personal/online/{game['id']}/replay").json()
    for current in (detail, replay["game"]):
        assert current["participants"]["defenders"] == {
            "name": "",
            "account_id": accounts["Keeper"]["id"],
        }
    personal = clients["Archer"].get("/api/personal/online?section=history").json()["games"][0]
    assert personal["players"]["defenders"] is None
    hall = clients["Archer"].get("/api/history/games").json()["games"][0]
    assert hall["defenders"] is None
    assert clients["Archer"].get(f"/api/history/online/{game['id']}/analytics").status_code == 200
    with app.state.store.connection() as c:
        assert (
            tuple(c.execute("SELECT * FROM online_games WHERE id=?", (game["id"],)).fetchone())
            == before
        )


def test_model_usage_resolves_account_names_and_keeps_ai_provenance(world, tmp_path):
    app, clients, _, _, _ = world
    client = clients["Odin"]
    model, _ = register_model(client, tmp_path)
    response = client.post(
        "/api/games",
        json={
            "ruleset": "brandubh",
            "human_side": "attackers",
            "players": {
                "defenders": {
                    "profile_id": "neural",
                    "config": {"model_id": model["id"], "time_limit": 0.01},
                }
            },
        },
    )
    assert response.status_code == 201, response.text
    game = response.json()
    before = stored_game(app, game["id"])
    share_name(client, "Current Odin")
    usage = client.get(f"/api/models/{model['id']}/usage").json()["unfinished_games"][0]
    assert_identity(usage["players"]["attackers"], "Current Odin", "Odin")
    assert usage["players"]["defenders"]["name"] == game["players"]["defenders"]["name"]
    assert stored_game(app, game["id"]) == before


def test_maximum_unicode_single_player_label_remains_searchable_without_storing_display_name(world):
    app, clients, _, _, _ = world
    username, name = "W" * 64, "😀" * 80
    app.state.auth.create_account(
        username, "long-linked@example.test", "long-linked-fixture-password"
    )
    client = clients["Observer"]
    login = client.post(
        "/api/auth/login", json={"identity": username, "password": "long-linked-fixture-password"}
    )
    assert login.status_code == 200
    client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
    share_name(client, name)
    game = create(client)
    assert_identity(game["players"]["attackers"], name, username)
    assert app.state.store.get_game(game["id"])["players"]["attackers"]["name"] == username
    label = attributed_name(name, username)
    assert len(label) == 148
    result = clients["Odin"].get("/api/history/games", params={"player": label}).json()
    assert result["matching"] == 1 and result["games"][0]["attackers"] == label
    share_name(client, name, False)
    assert (
        clients["Odin"].get("/api/history/games", params={"player": label}).json()["matching"] == 0
    )


def test_identity_queries_are_batched_for_game_lists_stats_online_and_history(world, monkeypatch):
    app, clients, _, _, _ = world
    client = clients["Archer"]
    create(client)
    online(world)
    statements = []
    original = app.state.store.connection

    @contextmanager
    def traced():
        with original() as c:
            c.set_trace_callback(statements.append)
            yield c

    monkeypatch.setattr(app.state.store, "connection", traced)
    paths = (
        "/api/games",
        "/api/stats",
        "/api/online/games",
        "/api/personal/single-player",
        "/api/history/games",
    )
    counts = []
    write_counts = []
    for size in (1, 6):
        if size > 1:
            for _ in range(size - 1):
                create(client)
                online(world)
        statements.clear()
        share_name(client, f"Current name {size}")
        writes = [
            sql
            for sql in statements
            if sql.lstrip().upper().startswith(("UPDATE", "INSERT", "DELETE"))
        ]
        assert not any(" games " in sql.lower() or "online_games" in sql.lower() for sql in writes)
        write_counts.append(len(writes))
        batch = []
        for path in paths:
            statements.clear()
            result = client.get(path)
            assert result.status_code == 200, result.text
            reads = [
                sql for sql in statements if sql.lstrip().upper().startswith(("SELECT", "WITH"))
            ]
            # Existing full-game projections also read runes, analysis and AI
            # provenance per game; only new account identity work is counted.
            batch.append(sum("accounts" in sql.lower() for sql in reads))
        counts.append(batch)
    assert counts[0] == counts[1] and all(count > 0 for count in counts[0])
    assert write_counts[0] == write_counts[1] > 0
