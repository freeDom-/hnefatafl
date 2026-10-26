"""Route-derived authorization coverage, real cookies, real DB and no auth bypass."""

from record_fixtures import stored_player
import json
import re
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from hnefatafl.accounts import main as account_cli
from hnefatafl.auth import COOKIE, POLICY, SecuritySettings, password_hash, verify
from hnefatafl.security_context import PERMISSIONS
from hnefatafl.server import create_app
from hnefatafl.storage import Store

PASSWORD = "only-a-test-password-123"
ORIGIN = {"Origin": "http://testserver"}


@pytest.fixture
def application(tmp_path):
    return create_app(tmp_path)


def account(app, role="USER", name="Raven"):
    return app.state.auth.create_account(name, name + "@example.test", PASSWORD, role)


def login(client, name="Raven", password=PASSWORD):
    response = client.post(
        "/api/auth/login", json={"identity": name, "password": password}, headers=ORIGIN
    )
    assert response.status_code == 200, response.text
    client.headers.update({**ORIGIN, "X-CSRF-Token": response.json()["csrf_token"]})
    return response


def registered(app):
    return {
        (method, route.path)
        for route in app.routes
        if getattr(route, "path", "").startswith("/api/")
        for method in route.methods
    }


def test_every_registered_api_method_has_an_explicit_policy(application):
    assert registered(application) == set(POLICY)
    from pathlib import Path

    inventory = json.loads(
        (Path(__file__).resolve().parents[1] / "docs/API_PERMISSIONS.json").read_text()
    )
    assert {(row["method"], row["route"]) for row in inventory} == registered(application)
    assert all(
        permission in {"public", "worker", *PERMISSIONS["ADMIN"]} for permission in POLICY.values()
    )
    assert not any(
        getattr(route, "path", "") in {"/docs", "/redoc", "/openapi.json"}
        for route in application.routes
    )


@pytest.mark.parametrize("identity", ["anonymous", "USER", "ADMIN", "disabled", "unknown"])
def test_complete_registered_route_matrix(application, identity):
    auth = application.state.auth
    if identity != "anonymous":
        entry = account(application, "ADMIN" if identity == "ADMIN" else "USER")
    with TestClient(application) as client:
        if identity != "anonymous":
            login(client)
            if identity in {"disabled", "unknown"}:
                with auth.store.connection() as c:
                    c.execute(
                        "UPDATE accounts SET active=?,role=? WHERE id=?",
                        (
                            0 if identity == "disabled" else 1,
                            "unknown" if identity == "unknown" else "USER",
                            entry["id"],
                        ),
                    )
        before = [len(auth.store.jobs()), len(auth.store.tournaments())]
        for method, path in sorted(registered(application)):
            if path == "/api/auth/logout":
                continue  # Dedicated invalidation test; don't destroy matrix identity.
            concrete = re.sub(r"\{[^}]+\}", "missing", path)
            permission = POLICY[(method, path)]
            response = client.request(
                method,
                concrete,
                json=[] if method not in {"GET", "DELETE"} else None,
                headers=ORIGIN,
            )
            if permission == "public":
                assert response.status_code in {200, 422}, (method, path, response.text)
            elif permission == "worker":
                assert response.status_code == 401, (method, path, response.text)
            elif identity in {"anonymous", "disabled", "unknown"}:
                assert response.status_code == 401, (method, path, response.text)
            elif permission not in PERMISSIONS[identity]:
                assert response.status_code == 403, (method, path, response.text)
            else:
                assert response.status_code not in {401, 403, 500}, (method, path, response.text)
        assert before == [len(auth.store.jobs()), len(auth.store.tournaments())]


def test_bootstrap_hash_uniqueness_and_idempotence(application, tmp_path, monkeypatch):
    credentials = {"username": "Odin", "email": "ODIN@example.test", "password": PASSWORD}
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(credentials)))
    assert account_cli(["init-admin", "--data-dir", str(tmp_path), "--credentials-stdin"]) == 0
    assert account_cli(["init-admin", "--data-dir", str(tmp_path)]) == 0
    with application.state.store.connection() as c:
        rows = c.execute("SELECT * FROM accounts").fetchall()
    assert len(rows) == 1 and rows[0]["username"] == "Odin"
    encoded = rows[0]["password_hash"]
    assert encoded.startswith("scrypt$") and PASSWORD not in encoded and verify(PASSWORD, encoded)
    assert password_hash(PASSWORD) != encoded
    with pytest.raises(sqlite3.IntegrityError):
        application.state.auth.create_account("ODIN", "another@example.test", PASSWORD)
    with pytest.raises(sqlite3.IntegrityError):
        application.state.auth.create_account("Other", "odin@example.test", PASSWORD)
    with pytest.raises(ValueError):
        application.state.auth.create_account(
            "Another", "another@example.test", PASSWORD, "ADMIN", bootstrap=True
        )
    import getpass
    import warnings
    from hnefatafl.accounts import read_password

    def unmasked(_prompt):
        warnings.warn("Password input would be echoed", getpass.GetPassWarning)
        raise AssertionError("Must refuse before reading an unmasked password")

    monkeypatch.setattr(getpass, "getpass", unmasked)
    with pytest.raises(ValueError, match="hide password"):
        read_password("Password: ")


def test_login_expiry_logout_restart_and_session_fixation(application, tmp_path):
    account(application)
    with TestClient(application) as first:
        response = login(first, "  RAVEN@EXAMPLE.TEST ")
        assert (
            "HttpOnly" in response.headers["set-cookie"]
            and "SameSite=lax" in response.headers["set-cookie"]
        )
        token = first.cookies[COOKIE]
        assert first.get("/api/auth/me").status_code == 200
        new_token = login(first).cookies[COOKIE]
        assert token != new_token
        assert application.state.auth.session(token) is None
    with TestClient(create_app(tmp_path)) as second:
        second.cookies.set(COOKIE, new_token)
        restored = second.get("/api/auth/me")
        assert restored.status_code == 200
        second.headers["X-CSRF-Token"] = restored.json()["csrf_token"]
        assert second.post("/api/auth/logout", json={}, headers=ORIGIN).status_code == 200
        assert second.get("/api/games").status_code == 401
        second.cookies.set(COOKIE, new_token)
        assert second.get("/api/games").status_code == 401
        login(second)
        with application.state.store.connection() as c:
            c.execute("UPDATE sessions SET expires=?", (time.time() - 1,))
        assert second.get("/api/games").status_code == 401


def test_invalid_credentials_rate_limits_and_disabled_accounts(application):
    entry = account(application)
    with TestClient(application) as client:
        texts = []
        for identity in ("Raven", "missing@example.test"):
            response = client.post(
                "/api/auth/login", json={"identity": identity, "password": "wrong"}, headers=ORIGIN
            )
            assert response.status_code == 401
            texts.append(response.json())
        assert texts[0] == texts[1]
        application.state.auth.update_account(entry["id"], active=False)
        assert (
            client.post(
                "/api/auth/login", json={"identity": "Raven", "password": PASSWORD}, headers=ORIGIN
            ).status_code
            == 401
        )
        for _ in range(8):
            client.post(
                "/api/auth/login", json={"identity": "Raven", "password": "wrong"}, headers=ORIGIN
            )
        assert (
            client.post(
                "/api/auth/login", json={"identity": "Raven", "password": PASSWORD}, headers=ORIGIN
            ).status_code
            == 429
        )


def test_csrf_origin_cookies_and_forged_claims(application):
    account(application)
    with TestClient(application) as client:
        assert (
            client.post(
                "/api/auth/login",
                json={"identity": "Raven", "password": PASSWORD},
                headers={"Origin": "https://evil.test"},
            ).status_code
            == 403
        )
        login(client)
        client.headers.pop("X-CSRF-Token")
        assert client.post("/api/games", json={}).status_code == 403
        client.headers["X-CSRF-Token"] = "forged"
        assert client.post("/api/games", json={}).status_code == 403
        login(client)
        assert (
            client.post("/api/games", json={}, headers={"Origin": "https://evil.test"}).status_code
            == 403
        )
        assert (
            client.post(
                "/api/training/jobs?role=ADMIN",
                json={"role": "ADMIN"},
                headers={"Authorization": "Bearer forged", "X-User-ID": "Odin", "X-Role": "ADMIN"},
            ).status_code
            == 403
        )
        assert not application.state.store.jobs()
        assert not application.state.store.list_games()
        client.cookies.clear()
        client.cookies.set(COOKIE, "forged")
        assert client.get("/api/games?admin=true").status_code == 401
        assert (
            client.get("/api/auth/registration").json()["message"]
            == "Registration is not available yet."
        )
        # Public registration still rejects client-supplied privileges.
        assert client.post("/api/auth/register", json={"role": "ADMIN"}).status_code == 422


def test_object_ownership_all_game_routes_statistics_and_players(application):
    account(application, name="First")
    account(application, name="Second")
    account(application, "ADMIN", name="Odin")
    with (
        TestClient(application) as first,
        TestClient(application) as second,
        TestClient(application) as admin,
    ):
        login(first, "First")
        login(second, "Second")
        login(admin, "Odin")
        game = first.post(
            "/api/games", json={"ruleset": "brandubh", "human_side": "attackers"}
        ).json()
        assert game["id"]
        for method, path in registered(application):
            if "{game_id}" not in path:
                continue
            response = second.request(
                method,
                path.replace("{game_id}", game["id"]),
                json={"version": 0, "source": 1, "target": 2, "cursor": 0},
            )
            expected = {403} if POLICY[(method, path)] == "accounts" else {404, 422}
            assert response.status_code in expected, (method, path, response.text)
        assert second.get("/api/games").json() == []
        assert second.get("/api/players").status_code in {403, 404}
        assert (
            second.post(
                "/api/games", json={"players": {"defenders": {"account_id": "forged"}}}
            ).status_code
            == 422
        )
        assert second.get("/api/stats").json()["total_games"] == 0
        assert admin.get("/api/games/" + game["id"]).status_code == 200
        assert (
            first.post(
                f"/api/games/{game['id']}/moves", json={**game["legal_moves"][0], "version": 0}
            ).status_code
            == 200
        )


def test_stale_permissions_worker_revocation_and_last_admin(application):
    entry = account(application, "ADMIN")
    account(application, "ADMIN", "Other")
    with TestClient(application) as client:
        login(client)
        worker = client.post(
            "/api/training/workers", json={"name": "Machine", "capacity": 1}
        ).json()
        bearer = {"Authorization": "Bearer " + worker["token"]}
        assert client.post("/api/worker/heartbeat", json={}, headers=bearer).status_code == 200
        application.state.auth.update_account(entry["id"], role="USER")
        assert client.get("/api/training/jobs").status_code == 401
        assert client.post("/api/worker/heartbeat", json={}, headers=bearer).status_code == 403
        login(client)
        assert client.get("/api/training/jobs").status_code == 403
        with pytest.raises(ValueError):
            with application.state.store.connection() as c:
                other = c.execute("SELECT id FROM accounts WHERE username='Other'").fetchone()[0]
            application.state.auth.update_account(other, active=False)


def test_unknown_routes_methods_trailing_slashes_and_websockets(application):
    @application.get("/api/accidentally-unprotected")
    def unsafe():
        raise AssertionError("Default-deny must prevent execution")

    application.router.routes.insert(0, application.router.routes.pop())
    account(application)
    with TestClient(application) as client:
        for path in (
            "/api/training/jobs/",
            "/api//training/jobs",
            "/api/legacy/training",
            "/openapi.json",
            "/docs",
        ):
            assert client.get(path, follow_redirects=False).status_code in {401, 307}
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/api/training/stream"):
                pass
        login(client)
        assert client.get("/api/accidentally-unprotected").status_code == 403
        assert client.request("TRACE", "/api/games").status_code == 403


def test_legacy_migration_preserves_data_and_has_no_invented_ownership(tmp_path):
    old = Store(tmp_path / "hnefatafl.sqlite3")
    stored_player(old, {"id": "historical", "name": "Old player", "created_at": "yesterday"})
    # Simulate legacy schema 7 columns by recreating only original tables.
    with old.connection() as c:
        c.execute("PRAGMA user_version=7")
    migrated = Store(old.path)
    assert migrated.players()[0]["id"] == "historical"
    assert migrated.players()[0]["owner_account_id"] is None
    with migrated.connection() as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == 10
        assert c.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 0


def test_production_configuration_is_fail_closed(monkeypatch):
    monkeypatch.setenv("HNEFATAFL_ENV", "production")
    with pytest.raises(ValueError):
        SecuritySettings.environment()
    monkeypatch.setenv("HNEFATAFL_PUBLIC_URL", "https://tafl.example.test")
    monkeypatch.setenv("HNEFATAFL_ALLOWED_HOSTS", "tafl.example.test")
    monkeypatch.setenv("HNEFATAFL_TRUSTED_PROXIES", "10.0.0.2")
    assert SecuritySettings.environment().production


def test_production_cookies_hosts_safe_errors_and_bounded_login(application, tmp_path, monkeypatch):
    from dataclasses import replace

    build = tmp_path / "project/frontend/dist"
    build.mkdir(parents=True)
    (build / "index.html").write_text("test login shell")
    monkeypatch.setattr("hnefatafl.server.PROJECT_ROOT", tmp_path / "project")
    account(application, "ADMIN")
    application.state.auth.settings = replace(application.state.auth.settings, production=True)
    with TestClient(application, base_url="https://testserver") as client:
        response = client.post(
            "/api/auth/login",
            json={"identity": "Raven", "password": PASSWORD},
            headers={"Origin": "https://testserver"},
        )
        assert response.status_code == 200
        cookie = response.headers["set-cookie"]
        assert "Secure" in cookie and "HttpOnly" in cookie and "SameSite=lax" in cookie
        assert client.get("/api/auth/me").status_code == 200
        assert client.get("/api/health", headers={"Host": "evil.test"}).status_code == 400
        secret = "do-not-reflect-this-password"
        invalid = client.post(
            "/api/auth/login",
            json={"identity": "Raven", "password": secret, "role": "ADMIN"},
            headers={"Origin": "https://testserver"},
        )
        assert invalid.status_code == 422 and secret not in invalid.text
        assert invalid.headers["cache-control"] == "no-store"
        oversized = client.post(
            "/api/auth/login",
            content=b"x" * 8193,
            headers={"Origin": "https://testserver", "Content-Type": "application/json"},
        )
        assert oversized.status_code == 413


def test_deployment_gate_denies_mutations_and_preserves_readiness(application):
    import fcntl

    account(application)
    with TestClient(application) as client:
        login(client)
        with application.state.auth.gate_path.open("rb") as gate:
            fcntl.flock(gate, fcntl.LOCK_EX | fcntl.LOCK_NB)
            assert client.post("/api/games", json={}).status_code == 503
            assert client.get("/api/auth/me").status_code == 200
            assert client.get("/api/health").status_code == 200
            assert application.state.store.list_games() == []
        assert client.post("/api/games", json={}).status_code == 201


def test_ambiguous_account_status_cannot_reuse_session(application):
    entry = account(application)
    with TestClient(application) as client:
        login(client)
        for status in (2, "false"):
            with application.state.store.connection() as c:
                c.execute("UPDATE accounts SET active=? WHERE id=?", (status, entry["id"]))
            assert client.get("/api/auth/me").status_code == 401
