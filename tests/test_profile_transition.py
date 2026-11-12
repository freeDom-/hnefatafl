"""Schema-14 privacy survives schema-15 automatic publication and field-only PATCHes."""

import json

import pytest
from fastapi.testclient import TestClient

from hnefatafl.account_policy import public_name
from hnefatafl.storage import Store
from test_profile import app as app, template as template, login


def legacy(app, game_id=None):
    with app.state.store.connection() as c:
        c.execute("ALTER TABLE accounts DROP COLUMN profile_private_fields")
        c.execute("ALTER TABLE accounts DROP COLUMN date_of_birth")
        c.execute("PRAGMA user_version=14")
        c.execute(
            "UPDATE accounts SET display_name='Saved secret',country_code='DE',city='Private city',birth_year=1990,show_display_name=0,show_country=1,show_city=0,show_age=1 WHERE username='Raven'"
        )
        c.execute(
            "UPDATE accounts SET display_name='Public name',country_code='SE',city='Public city',birth_year=1988,show_display_name=1,show_country=0,show_city=1,show_age=0 WHERE username='Odin'"
        )
        c.execute("UPDATE online_timeout_gate SET enabled=0,version=0")
        if game_id:
            c.execute(
                "UPDATE games SET data=json_set(data,'$.players.attackers.name','Saved secret') WHERE id=?",
                (game_id,),
            )
        for row in c.execute("SELECT id FROM accounts"):
            c.execute(
                "INSERT INTO player_ratings VALUES(?, 'tablut', 1200, 1, 1, 0, 0, 1, 0)",
                (row["id"],),
            )
        return [dict(r) for r in c.execute("SELECT * FROM accounts ORDER BY id")]


def test_schema14_migration_preserves_private_values_public_values_and_account_security(app):
    before = legacy(app)
    Store(app.state.store.path)
    Store(app.state.store.path)
    with app.state.store.connection() as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == 16
        assert c.execute("SELECT enabled FROM online_timeout_gate").fetchone()[0] == 0
        rows = [dict(r) for r in c.execute("SELECT * FROM accounts ORDER BY id")]
        for old, row in zip(before, rows, strict=True):
            assert all(row[key] == value for key, value in old.items())
            assert row["date_of_birth"] is None
        pending = {row["username"]: json.loads(row["profile_private_fields"]) for row in rows}
        assert set(pending["Raven"]) == {"display_name", "city"}
        assert pending["Odin"] == ["country_code"]
    with TestClient(app) as client:
        login(client)
        private = client.get("/api/profile").json()
        assert private["display_name"] == "Saved secret" and private["legacy_birth_year"] == 1990
        assert set(private["legacy_private_fields"]) == {"display_name", "city"}
        assert not any(key.startswith("show_") for key in private)
        board = client.get("/api/leaderboard?ruleset=tablut").json()
        entries = {entry["username"]: entry for entry in board["entries"]}
        assert entries["Raven"]["name"] == "Raven" and entries["Raven"]["country_code"] == "DE"
        assert "city" not in entries["Raven"]
        assert entries["Odin"]["name"] == "Public name" and entries["Odin"]["city"] == "Public city"
        assert "country_code" not in entries["Odin"]
        for entry in entries.values():
            assert (
                not {
                    "age",
                    "age_range",
                    "date_of_birth",
                    "birth_year",
                    "profile_private_fields",
                    "legacy_private_fields",
                    "email",
                    "role",
                    "password_hash",
                }
                & entry.keys()
            )
        assert board["me"] == entries["Raven"]


def test_field_only_updates_and_explicit_reconfirmation_never_publish_other_legacy_values(app):
    with TestClient(app) as client:
        account = login(client)
        game = client.post(
            "/api/games", json={"ruleset": "brandubh", "human_side": "attackers"}
        ).json()
        legacy(app, game["id"])
        Store(app.state.store.path)
        with app.state.store.connection() as c:
            original = tuple(c.execute("SELECT * FROM games WHERE id=?", (game["id"],)).fetchone())

        def current():
            return client.get("/api/leaderboard?ruleset=tablut").json()["me"]

        def player():
            return client.get(f"/api/games/{game['id']}").json()["players"]["attackers"]

        assert player()["name"] == current()["name"] == "Raven"
        baseline = {key: current()[key] for key in ("rating", "rank", "rated_games", "account_id")}
        assert client.patch("/api/profile", json={}).status_code == 200
        assert player()["name"] == "Raven" and "city" not in current()
        edited = client.patch("/api/profile", json={"city": "Kyōto"}).json()
        assert edited["legacy_private_fields"] == ["display_name"]
        assert edited["legacy_birth_year"] == 1990
        assert current()["city"] == "Kyōto" and player()["name"] == "Raven"
        # Sending the same private value is explicit one-time confirmation.
        response = client.patch("/api/profile", json={"display_name": "Saved secret"})
        assert response.status_code == 200 and response.json()["legacy_private_fields"] == []
        assert current()["name"] == player()["name"] == "Saved secret"
        assert player()["account_id"] == account["id"] and player()["username"] == "Raven"
        with app.state.store.connection() as c:
            assert (
                c.execute(
                    "SELECT show_display_name FROM accounts WHERE id=?", (account["id"],)
                ).fetchone()[0]
                == 0
            )
        Store(app.state.store.path)  # Old false flags must not withhold a confirmed field again.
        assert current()["name"] == "Saved secret"
        assert (
            client.patch("/api/profile", json={"date_of_birth": "2000-02-29"}).json()[
                "legacy_birth_year"
            ]
            is None
        )
        assert type(current()["age"]) is int
        for field in ("display_name", "city", "date_of_birth"):
            assert client.patch("/api/profile", json={field: ""}).status_code == 200
        assert current()["name"] == player()["name"] == "Raven"
        assert "city" not in current() and "age" not in current()
        assert current()["country_code"] == "DE"
        assert {key: current()[key] for key in baseline} == baseline
        with app.state.store.connection() as c:
            assert (
                tuple(c.execute("SELECT * FROM games WHERE id=?", (game["id"],)).fetchone())
                == original
            )


@pytest.mark.parametrize("pending", ["not-json", "{}", '["unknown"]', "[{}]"])
def test_invalid_internal_legacy_metadata_fails_closed(pending):
    assert (
        public_name(
            {"username": "Raven", "display_name": "Private", "profile_private_fields": pending}
        )
        == "Raven"
    )
