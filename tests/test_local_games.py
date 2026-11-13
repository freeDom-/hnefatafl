"""Account-owned same-device local 1v1: seats, rules, guards and category split."""

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from hnefatafl.security_context import principal
from hnefatafl.server import create_app
from hnefatafl.storage import Store

PASSWORD = "local-1v1-fixture-password"
ORIGIN = {"Origin": "http://testserver"}
# One attacker (3) walks to 1, then a defender at 23 closes on the now-open
# square and captures it. Deterministic on the standard brandubh start.
BRANDUBH_CAPTURE = [(3, 1), (23, 2)]
# Two full cycles of the same four reversible moves end the game by threefold
# repetition, giving a recorded draw without an AI opponent.
BRANDUBH_DRAW = [(3, 2), (17, 16), (2, 3), (16, 17)] * 2


@pytest.fixture
def application(tmp_path):
    return create_app(tmp_path, online_background=False, account_background=False)


def account(app, role="USER", name="Owner"):
    return app.state.auth.create_account(name, name.lower() + "@example.test", PASSWORD, role)


def login(client, name="Owner"):
    response = client.post(
        "/api/auth/login", json={"identity": name, "password": PASSWORD}, headers=ORIGIN
    )
    assert response.status_code == 200, response.text
    client.headers.update({**ORIGIN, "X-CSRF-Token": response.json()["csrf_token"]})
    return response


def make_local(client, **changes):
    response = client.post("/api/games/local", json={"ruleset": "brandubh", **changes})
    assert response.status_code == 201, response.text
    return response.json()


def make_single_player(client, **changes):
    response = client.post(
        "/api/games", json={"ruleset": "brandubh", "human_side": "attackers", **changes}
    )
    assert response.status_code == 201, response.text
    return response.json()


def move(client, game, source, target, *, version=None):
    response = client.post(
        f"/api/games/{game['id']}/moves",
        json={
            "version": game["version"] if version is None else version,
            "source": source,
            "target": target,
        },
    )
    return response


def test_create_labels_and_both_seats_play(application):
    account(application)
    with TestClient(application) as client:
        login(client)
        game = make_local(client, attackers_label="Raven", defenders_label="Wolf")
        assert game["mode"] == "local"
        assert game["category"] == "local"
        assert game["analysis_enabled"] is False
        assert game["turn"] == "attackers"
        assert game["version"] == 0
        for side, label in (("attackers", "Raven"), ("defenders", "Wolf")):
            seat = game["players"][side]
            assert seat["kind"] == "human"
            assert seat["name"] == label
            assert seat["label"] == label
            assert "account_id" not in seat

        # Seat one (attackers) then seat two (defenders) move from one session.
        first = move(client, game, 3, 1)
        assert first.status_code == 200, first.text
        first = first.json()
        assert first["version"] == 1 and first["turn"] == "defenders"
        second = move(client, first, 23, 2)
        assert second.status_code == 200, second.text
        second = second.json()
        assert second["version"] == 2 and second["turn"] == "attackers"
        assert second["captured"]["attackers"] == 1

        # An illegal move is rejected without changing the version.
        illegal = move(client, second, 0, 48)
        assert illegal.status_code == 400, illegal.text
        # A stale optimistic version is refused before any move is validated.
        assert move(client, second, 3, 1, version=1).status_code == 409


@pytest.mark.parametrize("ruleset", ["brandubh", "tablut", "copenhagen"])
def test_all_three_rulesets_accept_local_play(application, ruleset):
    account(application)
    with TestClient(application) as client:
        login(client)
        game = make_local(client, ruleset=ruleset)
        assert game["ruleset"] == ruleset
        legal = game["legal_moves"][0]
        played = move(client, game, legal["source"], legal["target"])
        assert played.status_code == 200, played.text
        assert played.json()["version"] == 1


def test_capture_and_recorded_draw_end_state(application):
    account(application)
    with TestClient(application) as client:
        login(client)
        capture = make_local(client)
        for source, target in BRANDUBH_CAPTURE:
            response = move(client, capture, source, target)
            assert response.status_code == 200, response.text
            capture = response.json()
        assert capture["captured"]["attackers"] == 1

        drawn = make_local(client)
        for source, target in BRANDUBH_DRAW:
            response = move(client, drawn, source, target)
            assert response.status_code == 200, response.text
            drawn = response.json()
        assert drawn["winner"] == "draw"
        assert drawn["recorded_winner"] == "draw"
        assert drawn["recorded_reason"] == "threefold_repetition"
        finished = move(client, drawn, 3, 1)
        assert finished.status_code == 400  # finished games reject further moves


def test_owner_only_history_replay_and_moves(application):
    account(application, name="Owner")
    account(application, name="Other")
    account(application, "ADMIN", name="Odin")
    with (
        TestClient(application) as owner_client,
        TestClient(application) as other_client,
        TestClient(application) as admin_client,
    ):
        login(owner_client, "Owner")
        login(other_client, "Other")
        login(admin_client, "Odin")
        game = make_local(owner_client, attackers_label="Raven", defenders_label="Wolf")

        assert [g["id"] for g in owner_client.get("/api/personal/local").json()["games"]] == [
            game["id"]
        ]
        assert other_client.get("/api/personal/local").json()["games"] == []
        assert owner_client.get(f"/api/personal/local/{game['id']}/replay").status_code == 200

        for path in (
            f"/api/games/{game['id']}",
            f"/api/personal/local/{game['id']}/replay",
            f"/api/history/local/{game['id']}/replay",
        ):
            assert other_client.get(path).status_code == 404, path
        assert move(other_client, game, 3, 1).status_code == 404
        assert other_client.delete(f"/api/games/{game['id']}?version=0").status_code == 404

        # Trusted all-data administrators get read-only access, never a move.
        assert admin_client.get(f"/api/games/{game['id']}").status_code == 200
        hall = admin_client.get("/api/history/games?category=local").json()
        assert [g["id"] for g in hall["games"]] == [game["id"]]
        assert hall["games"][0]["category"] == "local"
        assert admin_client.get(f"/api/history/local/{game['id']}/replay").status_code == 200
        assert move(admin_client, game, 3, 1).status_code == 403
        assert admin_client.delete(f"/api/games/{game['id']}?version=0").status_code == 403

        # A category mismatch still hides the record from the wrong surface.
        assert (
            owner_client.get(f"/api/history/account-single-player/{game['id']}/replay").status_code
            == 404
        )


def test_replay_categories_never_cross_and_owners_stay_isolated(application):
    account(application, name="Owner")
    account(application, name="Other")
    account(application, "ADMIN", name="Odin")
    with (
        TestClient(application) as owner,
        TestClient(application) as other,
        TestClient(application) as admin,
    ):
        login(owner, "Owner")
        login(other, "Other")
        login(admin, "Odin")
        solo = make_single_player(owner)
        local = make_local(owner, attackers_label="Raven", defenders_label="Wolf")

        # Each personal replay route accepts only its own persisted category.
        own_solo = owner.get(f"/api/personal/single-player/{solo['id']}/replay")
        assert own_solo.status_code == 200, own_solo.text
        assert own_solo.json()["game"]["category"] == "account-single-player"
        assert owner.get(f"/api/personal/single-player/{local['id']}/replay").status_code == 404
        own_local = owner.get(f"/api/personal/local/{local['id']}/replay")
        assert own_local.status_code == 200, own_local.text
        assert own_local.json()["game"]["category"] == "local"
        assert owner.get(f"/api/personal/local/{solo['id']}/replay").status_code == 404

        # Hall replay validates the requested category against the persisted row.
        assert (
            owner.get(f"/api/history/account-single-player/{solo['id']}/replay").status_code == 200
        )
        assert (
            owner.get(f"/api/history/account-single-player/{local['id']}/replay").status_code == 404
        )
        assert owner.get(f"/api/history/local/{local['id']}/replay").status_code == 200
        assert owner.get(f"/api/history/local/{solo['id']}/replay").status_code == 404

        # Unrelated accounts never reach another owner's record on any route.
        for path in (
            f"/api/personal/single-player/{solo['id']}/replay",
            f"/api/personal/local/{local['id']}/replay",
            f"/api/history/account-single-player/{solo['id']}/replay",
            f"/api/history/local/{local['id']}/replay",
        ):
            assert other.get(path).status_code == 404, path

        # Trusted all-data administrators read either category, but a local
        # game still never accepts a category or mutation it does not own.
        assert (
            admin.get(f"/api/history/account-single-player/{solo['id']}/replay").status_code == 200
        )
        assert admin.get(f"/api/history/local/{local['id']}/replay").status_code == 200
        assert admin.get(f"/api/history/local/{solo['id']}/replay").status_code == 404
        assert admin.get(f"/api/personal/local/{local['id']}/replay").status_code == 404
        assert move(admin, local, 3, 1).status_code == 403
        assert admin.delete(f"/api/games/{local['id']}?version=0").status_code == 403


def test_legacy_single_player_record_survives_category_migration(tmp_path):
    application = create_app(tmp_path, online_background=False, account_background=False)
    account(application, name="Owner")
    with TestClient(application) as client:
        login(client, "Owner")
        solo = make_single_player(client)

    # Rewind to schema 15: no additive category column, the single-player row
    # keeping its historical, pre-local meaning.
    with application.state.store.connection() as c:
        c.execute("DROP INDEX IF EXISTS games_category_owner")
        c.execute("ALTER TABLE games DROP COLUMN category")
        c.execute("PRAGMA user_version=15")

    upgraded = create_app(tmp_path, online_background=False, account_background=False)
    with TestClient(upgraded) as client:
        login(client, "Owner")
        assert upgraded.state.store.get_game(solo["id"]) is not None
        response = client.get(f"/api/personal/single-player/{solo['id']}/replay")
        assert response.status_code == 200, response.text
        assert response.json()["game"]["category"] == "account-single-player"
        assert (
            client.get(f"/api/history/account-single-player/{solo['id']}/replay").status_code == 200
        )
        # The migrated record is never reclassified as a local game.
        assert client.get(f"/api/personal/local/{solo['id']}/replay").status_code == 404
        assert client.get(f"/api/history/local/{solo['id']}/replay").status_code == 404


def test_hall_replay_trusts_the_persisted_category_over_payload_mode(application):
    account(application)
    with TestClient(application) as client:
        login(client)
        local = make_local(client)
        # Force a payload/column disagreement: only the persisted category column
        # may classify the record, so the local board is never served as an
        # account-single-player replay.
        with application.state.store.connection() as c:
            row = c.execute("SELECT data FROM games WHERE id=?", (local["id"],)).fetchone()
            payload = json.loads(row[0])
            payload["mode"] = "human-ai"
            c.execute("UPDATE games SET data=? WHERE id=?", (json.dumps(payload), local["id"]))
            assert (
                c.execute("SELECT category FROM games WHERE id=?", (local["id"],)).fetchone()[0]
                == "local"
            )
        assert (
            client.get(f"/api/history/account-single-player/{local['id']}/replay").status_code
            == 404
        )
        assert client.get(f"/api/history/local/{local['id']}/replay").status_code == 200


def test_local_play_is_out_of_stats_and_never_touches_elo(application):
    account(application)
    with TestClient(application) as client:
        login(client)
        game = make_local(client)
        for source, target in BRANDUBH_DRAW:
            game = move(client, game, source, target).json()
        assert game["winner"] == "draw"

        assert client.get("/api/games").json() == []
        assert client.get("/api/stats").json()["total_games"] == 0
        assert client.get("/api/personal/single-player").json()["games"] == []
        assert client.get("/api/history/games").json()["games"] == []
        assert client.get("/api/personal/online").json()["games"] == []
        with application.state.store.connection() as c:
            for table in ("rating_events", "rating_applications", "player_ratings"):
                assert c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_local_restart_keeps_seats_and_rejects_ai_analysis(application):
    account(application)
    with TestClient(application) as client:
        login(client)
        game = make_local(client, attackers_label="Raven", defenders_label="Wolf")
        assert (
            client.post(
                f"/api/games/{game['id']}/analysis", json={"version": 0, "cursor": 0}
            ).json()["evaluations"]
            == []
        )
        assert (
            client.put(f"/api/games/{game['id']}/analytics", json={"advisor": None}).status_code
            == 400
        )
        assert (
            client.post(f"/api/games/{game['id']}/ai-move", json={"version": 0}).status_code == 409
        )

        restarted = client.post(f"/api/games/{game['id']}/restart", json={"version": 0})
        assert restarted.status_code == 200, restarted.text
        restarted = restarted.json()
        assert restarted["id"] != game["id"] and restarted["mode"] == "local"
        assert restarted["version"] == 0 and restarted["moves"] == []
        assert restarted["players"]["attackers"]["label"] == "Raven"
        assert restarted["players"]["defenders"]["label"] == "Wolf"


def test_schema15_upgrade_adds_local_category_without_rewriting_history(tmp_path):
    store = Store(tmp_path / "db.sqlite3")
    with store.connection() as c:
        c.execute("DROP INDEX IF EXISTS games_category_owner")
        c.execute("ALTER TABLE games DROP COLUMN category")
        c.execute(
            "INSERT INTO games(id,version,created_at,data,owner_account_id) VALUES('old',0,'then',?,NULL)",
            ('{"id":"old","ruleset":"tablut","mode":"human-ai"}',),
        )
        c.execute("PRAGMA user_version=15")
    upgraded = Store(store.path)
    with upgraded.connection() as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == 17
        columns = {row[1] for row in c.execute("PRAGMA table_info(games)")}
        assert "category" in columns
        assert c.execute("SELECT category FROM games WHERE id='old'").fetchone()[0] == (
            "account-single-player"
        )
        assert c.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name='games_category_owner'"
        ).fetchone()
    assert Store(store.path).get_game("old")["id"] == "old"
    with sqlite3.connect(store.path) as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == 17


def test_category_migration_is_idempotent_on_schema16(tmp_path):
    store = Store(tmp_path / "db.sqlite3")
    with store.connection() as c:
        c.execute(
            "INSERT INTO games(id,version,created_at,data,owner_account_id,category) "
            "VALUES('local',0,'now',?,NULL,'local')",
            ('{"id":"local","mode":"local","ruleset":"tablut","moves":[]}',),
        )
    # Re-opening an already-migrated schema-16 database re-runs the additive
    # migration without duplicating the column or rewriting the stored category.
    for _ in range(2):
        reopened = Store(store.path)
        with reopened.connection() as c:
            assert c.execute("PRAGMA user_version").fetchone()[0] == 17
            assert [row[1] for row in c.execute("PRAGMA table_info(games)")].count("category") == 1
            assert c.execute("SELECT category FROM games WHERE id='local'").fetchone()[0] == "local"
    assert Store(store.path).get_game("local")["mode"] == "local"


def test_list_games_category_filter_preserves_owner_scope(tmp_path):
    store = Store(tmp_path / "db.sqlite3")
    owner = {"id": "owner-1", "role": "USER", "active": 1}
    records = [
        ("solo-1", "owner-1", "account-single-player"),
        ("local-1", "owner-1", "local"),
        ("solo-2", "owner-2", "account-single-player"),
        ("local-2", "owner-2", "local"),
    ]
    with store.connection() as c:
        for index, (game_id, account_id, category) in enumerate(records):
            c.execute(
                "INSERT INTO games(id,version,created_at,data,owner_account_id,category) "
                "VALUES(?,0,?,?,?,?)",
                (
                    game_id,
                    f"{index:04d}",
                    json.dumps(
                        {
                            "id": game_id,
                            "mode": "local" if category == "local" else "human-ai",
                        }
                    ),
                    account_id,
                    category,
                ),
            )
    token = principal.set(owner)
    try:
        # Owner scoping survives every filter combination.
        assert {g["id"] for g in store.list_games()} == {"solo-1", "local-1"}
        assert {g["id"] for g in store.list_games(exclude_category="local")} == {"solo-1"}
        assert {g["id"] for g in store.list_games(category="local")} == {"local-1"}
    finally:
        principal.reset(token)


def test_list_games_exclusion_keeps_migrated_legacy_records(tmp_path):
    store = Store(tmp_path / "db.sqlite3")
    with store.connection() as c:
        c.execute("DROP INDEX IF EXISTS games_category_owner")
        c.execute("ALTER TABLE games DROP COLUMN category")
        c.execute(
            "INSERT INTO games(id,version,created_at,data,owner_account_id) "
            "VALUES('legacy',0,'then',?,NULL)",
            ('{"id":"legacy","mode":"human-ai"}',),
        )
        c.execute("PRAGMA user_version=15")
    # The legacy row keeps its historical single-player meaning and is never
    # dropped by the local-exclusion filter after the additive migration.
    upgraded = Store(store.path)
    assert [g["id"] for g in upgraded.list_games(exclude_category="local")] == ["legacy"]
    assert [g["id"] for g in upgraded.list_games(category="account-single-player")] == ["legacy"]
    assert upgraded.list_games(category="local") == []


def _insert_game(c, game_id, account_id, category, created_at):
    c.execute(
        "INSERT INTO games(id,version,created_at,data,owner_account_id,category) "
        "VALUES(?,0,?,?,?,?)",
        (game_id, created_at, json.dumps({"id": game_id}), account_id, category),
    )


def test_single_player_listing_uses_the_category_owner_index(tmp_path):
    store = Store(tmp_path / "db.sqlite3")
    with store.connection() as c:
        # The positive single-player filter searches the category index...
        positive = [
            row[3]
            for row in c.execute(
                "EXPLAIN QUERY PLAN SELECT data FROM games "
                "WHERE owner_account_id=? AND category=? ORDER BY created_at DESC,id",
                ("owner-1", "account-single-player"),
            ).fetchall()
        ]
        assert any("games_category_owner" in detail for detail in positive), positive
        # ...while the previous COALESCE inequality could only use the owner index
        # and had to read and filter every owned row.
        previous = [
            row[3]
            for row in c.execute(
                "EXPLAIN QUERY PLAN SELECT data FROM games "
                "WHERE owner_account_id=? AND "
                "COALESCE(category,'account-single-player')<>? ORDER BY created_at DESC,id",
                ("owner-1", "local"),
            ).fetchall()
        ]
        assert all("games_category_owner" not in detail for detail in previous), previous


def test_single_player_listing_excludes_future_categories(tmp_path):
    store = Store(tmp_path / "db.sqlite3")
    owner = {"id": "owner-1", "role": "USER", "active": 1}
    with store.connection() as c:
        _insert_game(c, "solo-1", "owner-1", "account-single-player", "0001")
        _insert_game(c, "local-1", "owner-1", "local", "0002")
        _insert_game(c, "tournament-1", "owner-1", "player-tournament", "0003")
    token = principal.set(owner)
    try:
        # A future category is never mistaken for a single-player record.
        assert {g["id"] for g in store.list_games(category="account-single-player")} == {"solo-1"}
        # The generic unfiltered caller still sees every category.
        assert {g["id"] for g in store.list_games()} == {
            "solo-1",
            "local-1",
            "tournament-1",
        }
    finally:
        principal.reset(token)


def test_single_player_listing_stays_indexed_with_many_local_games(tmp_path):
    store = Store(tmp_path / "db.sqlite3")
    owner = {"id": "owner-1", "role": "USER", "active": 1}
    with store.connection() as c:
        for index in range(500):
            _insert_game(c, f"local-{index}", "owner-1", "local", f"1{index:04d}")
        _insert_game(c, "solo-1", "owner-1", "account-single-player", "0000")
    token = principal.set(owner)
    try:
        assert [g["id"] for g in store.list_games(category="account-single-player")] == ["solo-1"]
    finally:
        principal.reset(token)
    with store.connection() as c:
        details = [
            row[3]
            for row in c.execute(
                "EXPLAIN QUERY PLAN SELECT data FROM games "
                "WHERE owner_account_id=? AND category=? ORDER BY created_at DESC,id",
                ("owner-1", "account-single-player"),
            ).fetchall()
        ]
    assert any("games_category_owner" in detail for detail in details), details


def test_single_player_listing_respects_admin_scope(tmp_path):
    store = Store(tmp_path / "db.sqlite3")
    with store.connection() as c:
        _insert_game(c, "solo-1", "owner-1", "account-single-player", "0001")
        _insert_game(c, "solo-2", "owner-2", "account-single-player", "0002")
        _insert_game(c, "local-1", "owner-1", "local", "0003")
    admin = {"id": "admin-1", "role": "ADMIN", "active": 1}
    token = principal.set(admin)
    try:
        # An all_data administrator is not owner-scoped, but the category filter
        # still applies and local games stay out of the single-player listing.
        assert {g["id"] for g in store.list_games(category="account-single-player")} == {
            "solo-1",
            "solo-2",
        }
    finally:
        principal.reset(token)


def test_unrelated_listings_never_deserialize_local_games(application, monkeypatch):
    account(application)
    with TestClient(application) as client:
        login(client)
        solo = make_single_player(client)
        for _ in range(25):
            make_local(client)

        import hnefatafl.storage as storage

        real_loads = storage.json.loads
        parsed_games: list[dict] = []

        def recording_loads(value, *args, **kwargs):
            parsed = real_loads(value, *args, **kwargs)
            if isinstance(parsed, dict) and "mode" in parsed and "players" in parsed:
                parsed_games.append(parsed)
            return parsed

        monkeypatch.setattr(storage.json, "loads", recording_loads)

        assert [g["id"] for g in client.get("/api/games").json()] == [solo["id"]]
        assert client.get("/api/stats").json()["total_games"] == 1

        # The spy saw the surviving single-player payload but no local one:
        # local rows are excluded by SQL before any JSON deserialization.
        assert {game["id"] for game in parsed_games} == {solo["id"]}
        assert all(game["mode"] != "local" for game in parsed_games)


REJECTED_SEAT_LABELS = [
    "Raven\nWolf",
    "Raven\r",
    "Raven\t",
    "Raven\x00",
    "Raven\x1b[31m",
    "Raven\x7f",
    "Raven\x9b",
    "\u202eRaven",
    "\u202aRaven",
    "\u2066Raven",
    "\u2069Raven",
    "\u200eRaven",
    "\u200fRaven",
    "\u061cRaven",
    "   ",
]


@pytest.mark.parametrize("label", REJECTED_SEAT_LABELS)
def test_local_seat_labels_reject_control_and_bidi_input(application, label):
    account(application)
    with TestClient(application) as client:
        login(client)
        response = client.post(
            "/api/games/local",
            json={"ruleset": "brandubh", "attackers_label": label, "defenders_label": "Wolf"},
        )
        assert response.status_code == 422, response.text
        assert client.get("/api/games").json() == []


@pytest.mark.parametrize(
    "label",
    [
        "Éowyn",
        "Ólafr",
        "Ольга",
        "李雷",
        "こんにちは",
        "Níðhǫggr",
        "Bjørn",
        "Renée O'Brien",
        "Иван Петров",
        "Ṣalāḥ al-Dīn",
    ],
)
def test_local_seat_labels_accept_international_names(application, label):
    account(application)
    with TestClient(application) as client:
        login(client)
        game = make_local(client, attackers_label=label, defenders_label="Wolf")
        assert game["players"]["attackers"]["label"] == label
        assert game["players"]["defenders"]["label"] == "Wolf"


def test_local_seat_labels_trim_surrounding_space_and_keep_length_limits(application):
    account(application)
    with TestClient(application) as client:
        login(client)
        trimmed = make_local(client, attackers_label="  Renée O'Brien  ", defenders_label="Wolf")
        assert trimmed["players"]["attackers"]["label"] == "Renée O'Brien"

        longest = make_local(client, attackers_label="A" * 40)
        assert longest["players"]["attackers"]["label"] == "A" * 40
        too_long = client.post(
            "/api/games/local",
            json={"ruleset": "brandubh", "attackers_label": "A" * 41},
        )
        assert too_long.status_code == 422, too_long.text
