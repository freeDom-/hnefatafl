"""Real session/CSRF, private projections and additive account compatibility."""

from dataclasses import replace
from datetime import datetime, timezone
import json
import sqlite3

from fastapi.testclient import TestClient
import pytest

from hnefatafl.account_mail import AccountSettings
from hnefatafl.auth import COOKIE, digest, password_hash, verify
from hnefatafl.profile_api import COUNTRY_CODES
from hnefatafl.server import create_app
from hnefatafl.storage import Store
from test_registration import Mailbox

PASSWORD = "profile-fixture-password"
NEW_PASSWORD = "profile-replacement-password"
ORIGIN = {"Origin": "http://testserver"}
FIELDS = {
    "display_name": "Valkyrie",
    "country_code": "DE",
    "city": "Dresden",
    "birth_year": 1990,
}
FLAGS = ("show_display_name", "show_country", "show_city", "show_age")


@pytest.fixture(scope="module")
def template(tmp_path_factory):
    app = create_app(tmp_path_factory.mktemp("profile-template"), account_background=False)
    for name, role in (("Raven", "USER"), ("Odin", "ADMIN")):
        app.state.auth.create_account(name, name.lower() + "@example.test", PASSWORD, role)
    return app.state.store.path


@pytest.fixture
def app(tmp_path, template):
    with (
        sqlite3.connect(template) as source,
        sqlite3.connect(tmp_path / "hnefatafl.sqlite3") as target,
    ):
        source.backup(target)
    return create_app(
        tmp_path,
        account_settings=replace(
            AccountSettings(),
            public_origin="https://tafl.example.test",
            sender="mail@example.test",
            smtp_host="fake.invalid",
        ),
        mail_transport=Mailbox(),
        account_background=False,
    )


def login(client, name="Raven", password=PASSWORD):
    response = client.post(
        "/api/auth/login", json={"identity": name, "password": password}, headers=ORIGIN
    )
    assert response.status_code == 200, response.text
    client.headers.update({**ORIGIN, "X-CSRF-Token": response.json()["csrf_token"]})
    return response.json()["account"]


def test_owned_profile_defaults_empty_updates_and_roles(app):
    with TestClient(app) as client:
        assert client.get("/api/profile").status_code == 401
        for name in ("Raven", "Odin"):
            account = login(client, name)
            original = app.state.auth.account(account["id"])
            result = client.get("/api/profile")
            assert result.status_code == 200
            profile = result.json()
            assert all(profile[field] is None for field in FIELDS)
            assert all(profile[flag] is False for flag in FLAGS)
            assert profile["username"] == name and profile["email"] == account["email"]
            assert len(profile["country_codes"]) == len(set(COUNTRY_CODES)) == 249
            assert not profile["email_verified"]
            assert "password_hash" not in profile and "role" not in profile
            values = {
                "display_name": "  Þór 東京  ",
                "country_code": " de ",
                "city": "  Łódź  ",
                "birth_year": 1990,
                "show_city": True,
            }
            saved = client.patch("/api/profile", json=values)
            assert saved.status_code == 200
            assert saved.json()["display_name"] == "Þór 東京" and saved.json()["city"] == "Łódź"
            assert saved.json()["country_code"] == "DE"
            assert client.patch("/api/profile", json={"city": ""}).json()["city"] is None
            assert client.get("/api/profile").json()["show_city"] is True
            assert client.patch("/api/profile", json={}).status_code == 200
            cleared = client.patch("/api/profile", json={field: None for field in FIELDS}).json()
            assert all(cleared[field] is None for field in FIELDS)
            stored = app.state.auth.account(account["id"])
            for field in (
                "id",
                "username",
                "email",
                "password_hash",
                "role",
                "active",
                "email_verified_at",
            ):
                assert stored[field] == original[field]


@pytest.mark.parametrize(
    "changes",
    [
        {"display_name": "x" * 81},
        {"city": "x" * 101},
        {"city": "bad\ncity"},
        {"country_code": "UK"},
        {"country_code": "ZZ"},
        {"birth_year": 1899},
        {"birth_year": datetime.now(timezone.utc).year + 1},
        {"birth_year": "1990"},
        {"birth_year": True},
        {"show_country": "true"},
        {"show_city": None},
        {"role": "ADMIN"},
        {"active": False},
        {"email_verified_at": 1},
        {"password_hash": "raw-password-must-not-echo"},
        {"account_id": "another-user"},
        {"username": "Changed"},
        {"email": "new@example.test"},
    ],
)
def test_profile_rejects_invalid_fields_atomically(app, changes):
    with TestClient(app) as client:
        account = login(client)
        before = app.state.auth.account(account["id"])
        response = client.patch("/api/profile", json={"display_name": "Allowed", **changes})
        assert response.status_code == 422
        assert "raw-password-must-not-echo" not in response.text
        assert app.state.auth.account(account["id"]) == before


def test_profile_requires_csrf_and_rechecks_revoked_session_under_lock(app, monkeypatch):
    with TestClient(app) as client:
        account = login(client)
        assert (
            client.patch("/api/profile", json=FIELDS, headers={"X-CSRF-Token": "wrong"}).status_code
            == 403
        )
        assert (
            client.patch(
                "/api/profile", json=FIELDS, headers={"Origin": "https://evil.test"}
            ).status_code
            == 403
        )
        assert client.patch("/api/profile", json={"city": "x" * 9000}).status_code == 413
        original = app.state.auth.current_account

        def revoke_before_transaction(c):
            c.execute("DELETE FROM sessions WHERE account_id=?", (account["id"],))
            return original(c)

        monkeypatch.setattr(app.state.auth, "current_account", revoke_before_transaction)
        assert client.patch("/api/profile", json=FIELDS).status_code == 401
        assert app.state.auth.account(account["id"])["city"] is None


@pytest.mark.parametrize(
    "flag,public_field", list(zip(FLAGS, ("name", "country_code", "city", "age_range")))
)
def test_leaderboard_filters_each_field_and_immediately_withdraws_sharing(app, flag, public_field):
    with TestClient(app) as client:
        account = login(client)
        with app.state.store.connection() as c:
            c.execute(
                "INSERT INTO player_ratings VALUES(?, 'tablut', 1200, 1, 1, 0, 0, 1, 0)",
                (account["id"],),
            )
        assert client.patch("/api/profile", json=FIELDS).status_code == 200

        def standing():
            response = client.get("/api/leaderboard?ruleset=tablut")
            assert response.status_code == 200
            assert response.headers["cache-control"] == "no-store"
            data = response.json()
            assert data["entries"][0] == data["me"]
            return data["entries"][0]

        hidden = standing()
        assert hidden["name"] == "Raven"
        assert (
            not {
                "country_code",
                "city",
                "age_range",
                "birth_year",
                "email",
                "password_hash",
                "email_verified_at",
            }
            & hidden.keys()
        )
        assert client.patch("/api/profile", json={flag: True}).status_code == 200
        public = standing()
        if flag == "show_display_name":
            assert public["name"] == FIELDS["display_name"]
        elif flag == "show_age":
            year = datetime.now(timezone.utc).year
            assert public["age_range"] == {"min": year - 1991, "max": year - 1990}
        else:
            assert public[public_field] == FIELDS[public_field]
        assert set(public) - set(hidden) <= {public_field}
        assert public["rank"] == hidden["rank"] and public["rating"] == hidden["rating"]
        assert client.patch("/api/profile", json={flag: False}).status_code == 200
        assert standing() == hidden


def test_password_change_keeps_current_session_revokes_others_and_reset_links(app):
    with TestClient(app) as client, TestClient(app) as other:
        account = login(client)
        login(other)
        assert (
            client.patch("/api/profile", json={**FIELDS, "show_country": True}).status_code == 200
        )
        before = app.state.auth.account(account["id"])
        service = app.state.registration
        service.request_mail("reset", account["email"])
        assert service.process_one()
        response = client.post(
            "/api/profile/password",
            json={
                "current_password": PASSWORD,
                "new_password": NEW_PASSWORD,
                "confirm_password": NEW_PASSWORD,
            },
        )
        assert response.status_code == 200
        assert PASSWORD not in response.text and NEW_PASSWORD not in response.text
        assert client.get("/api/profile").status_code == 200
        assert other.get("/api/profile").status_code == 401
        after = app.state.auth.account(account["id"])
        assert verify(NEW_PASSWORD, after["password_hash"])
        assert not verify(PASSWORD, after["password_hash"])
        assert {k: v for k, v in before.items() if k != "password_hash"} == {
            k: v for k, v in after.items() if k != "password_hash"
        }
        with app.state.store.connection() as c:
            assert not c.execute("SELECT 1 FROM account_tokens WHERE purpose='reset'").fetchall()
            assert not c.execute("SELECT 1 FROM account_mail WHERE purpose='reset'").fetchall()
        assert (
            other.post(
                "/api/auth/login", json={"identity": "Raven", "password": PASSWORD}, headers=ORIGIN
            ).status_code
            == 401
        )
        login(other, password=NEW_PASSWORD)
        login(other, "Odin")  # Cross-account authority was untouched.


@pytest.mark.parametrize(
    "changes",
    [
        {"current_password": "wrong-password"},
        {"new_password": "short", "confirm_password": "short"},
        {"new_password": "a" * 12, "confirm_password": "a" * 12},
        {"confirm_password": "mismatch"},
        {"current_password": "sensitive" * 300},
        {"account_id": "another-user"},
    ],
)
def test_password_failures_preserve_session_and_security_state(app, changes):
    with TestClient(app) as client:
        account = login(client)
        before = app.state.auth.account(account["id"])
        response = client.post(
            "/api/profile/password",
            json={
                "current_password": PASSWORD,
                "new_password": NEW_PASSWORD,
                "confirm_password": NEW_PASSWORD,
                **changes,
            },
        )
        assert response.status_code == 422
        assert (
            PASSWORD not in response.text
            and NEW_PASSWORD not in response.text
            and "sensitive" not in response.text
        )
        assert client.get("/api/profile").status_code == 200
        assert app.state.auth.account(account["id"]) == before


def test_password_change_durable_limits_and_csrf(app):
    service = app.state.registration
    service.settings = replace(service.settings, confirm_identity=1)
    with TestClient(app) as client:
        account = login(client)
        body = {
            "current_password": "wrong",
            "new_password": NEW_PASSWORD,
            "confirm_password": NEW_PASSWORD,
        }
        assert (
            client.post(
                "/api/profile/password", json=body, headers={"X-CSRF-Token": "wrong"}
            ).status_code
            == 403
        )
        assert client.post("/api/profile/password", json=body).status_code == 422
        assert client.post("/api/profile/password", json=body).status_code == 429
        with app.state.store.connection() as c:
            assert (
                c.execute(
                    "SELECT attempts FROM account_limits WHERE key=?",
                    ("confirm:identity:" + digest(account["id"]),),
                ).fetchone()[0]
                == 1
            )


def test_password_change_cannot_overwrite_an_intervening_same_session_change(app, monkeypatch):
    from hnefatafl import profile_api

    with TestClient(app) as client:
        account = login(client)
        intervening = password_hash("intervening-password-change")

        def race(value):
            encoded = password_hash(value)
            with app.state.store.connection() as c:
                c.execute(
                    "UPDATE accounts SET password_hash=? WHERE id=?", (intervening, account["id"])
                )
            return encoded

        monkeypatch.setattr(profile_api, "password_hash", race)
        response = client.post(
            "/api/profile/password",
            json={
                "current_password": PASSWORD,
                "new_password": NEW_PASSWORD,
                "confirm_password": NEW_PASSWORD,
            },
        )
        assert response.status_code == 409
        assert app.state.auth.account(account["id"])["password_hash"] == intervening
        assert client.get("/api/profile").status_code == 200


def test_verification_reuses_single_use_mail_flow_and_updates_own_profile(app):
    service = app.state.registration
    with TestClient(app) as client:
        account = login(client)
        assert not client.get("/api/profile").json()["email_verified"]
        response = client.post("/api/auth/verification/resend", json={"email": account["email"]})
        assert response.status_code == 202 and "token" not in response.text
        assert service.process_one()
        token = service.transport.token()
        with app.state.store.connection() as c:
            assert token not in json.dumps(
                [tuple(row) for row in c.execute("SELECT * FROM account_tokens")]
            )
        assert client.post("/api/auth/verify-email", json={"token": token}).status_code == 200
        assert client.get("/api/profile").json()["email_verified"] is True
        assert client.get("/api/auth/me").json()["account"]["email_verified"] is True
        assert client.post("/api/auth/verify-email", json={"token": token}).status_code == 400
        assert (
            client.post(
                "/api/auth/verification/resend", json={"email": account["email"]}
            ).status_code
            == 202
        )
        assert service.process_one() is False


def test_schema13_upgrade_preserves_accounts_sessions_and_optional_legacy_names(app):
    auth = app.state.auth
    account, token, _ = auth.login("Odin", PASSWORD, "fixture")
    with auth.store.connection() as c:
        c.execute(
            "UPDATE accounts SET display_name='Legacy name',email_verified_at=123 WHERE id=?",
            (account["id"],),
        )
        for field in ("country_code", "city", "birth_year", *FLAGS):
            c.execute(f"ALTER TABLE accounts DROP COLUMN {field}")
        c.execute("PRAGMA user_version=13")
        c.execute("UPDATE online_timeout_gate SET enabled=0,version=0")
        before = dict(c.execute("SELECT * FROM accounts WHERE id=?", (account["id"],)).fetchone())
    store = Store(auth.store.path)
    Store(store.path)  # Idempotent restart.
    with store.connection() as c:
        after = dict(c.execute("SELECT * FROM accounts WHERE id=?", (account["id"],)).fetchone())
        assert c.execute("PRAGMA user_version").fetchone()[0] == 14
        assert all(after[field] is None for field in ("country_code", "city", "birth_year"))
        assert all(after[flag] == 0 for flag in FLAGS)
        assert all(after[field] == value for field, value in before.items())
        assert c.execute("SELECT enabled FROM online_timeout_gate").fetchone()[0] == 0
    assert auth.session(token)["id"] == account["id"]
    assert COOKIE not in after
