"""Authenticated adapter and assistance isolation; no browser/provider claims."""

from dataclasses import replace
from datetime import timedelta
import json
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from hnefatafl.auth import COOKIE, digest
from hnefatafl.online_settings import OnlineSettings
from hnefatafl.server import create_app
from test_online_core import (
    POLICY,
    active,
    actor,
    dump,
    move_for,
    resume,
    account_template as account_template,
    rig as rig,
)


@pytest.fixture
def api(rig):
    app = create_app(
        rig.root,
        online_settings=POLICY,
        online_clock=rig.clock,
        online_background=False,
        account_background=False,
    )

    def client(name="Archer"):
        result = TestClient(app)
        result.cookies.set(COOKIE, rig.tokens[name])
        result.headers.update(
            {"Origin": "http://testserver", "X-CSRF-Token": digest("csrf:" + rig.tokens[name])}
        )
        return result

    return app, client


def creation(**extra):
    return {
        "request_id": str(uuid4()),
        "ruleset": "brandubh",
        "side_selection": "attackers",
        **extra,
    }


def test_two_accounts_finish_durable_game_through_backend_api(rig, api):
    app, client = api
    archer, keeper, visitor = client(), client("Keeper"), client("Visitor")
    response = archer.post("/api/online/games", json=creation())
    assert response.status_code == 201, response.text
    game = response.json()["game"]
    assert game["time_per_move"] == 259200 and game["version"] == 0
    base = "/api/online/games/" + game["id"]
    joined = keeper.post(base + "/join", json={"version": 0})
    assert joined.status_code == 200
    game = joined.json()["game"]
    move = move_for(game)
    assert visitor.post(base + "/moves", json={"version": 1, **move.to_dict()}).status_code == 403
    assert keeper.post(base + "/concede", json={"version": 1}).status_code == 403
    moved = archer.post(base + "/moves", json={"version": 1, **move.to_dict()})
    assert moved.status_code == 200 and moved.json()["game"]["version"] == 2
    assert archer.post(base + "/moves", json={"version": 1, **move.to_dict()}).status_code == 409
    result = keeper.post(base + "/concede", json={"version": 2})
    assert result.status_code == 200
    finished = result.json()["game"]
    assert finished["status"] == "completed"
    assert finished["position"]["winner"] is None and finished["result"]["winner"] == "attackers"
    assert (
        finished["result"]["move_count"] == 1 and finished["result"]["abandoned_account_id"] is None
    )
    assert visitor.get(base).json() == finished
    assert visitor.get(base + "/replay").json()["validated"]
    assert archer.get("/api/online/stats").json()["wins"] == 1
    with app.state.store.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM online_results").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM online_moves").fetchone()[0] == 1
    assert keeper.post(base + "/concede", json={"version": 2}).status_code == 409


@pytest.mark.parametrize("duration", [True, False, "60", 60.0, 59, 1209601, None])
def test_api_duration_is_strict_and_errors_never_reflect_payload(rig, api, duration):
    _, client = api
    response = client().post(
        "/api/online/games",
        json=creation(time_per_move=duration),
    )
    assert response.status_code == 422
    assert "secret-account" not in response.text
    with rig.store.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM online_games").fetchone()[0] == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("actor_id", "other-account"),
        ("board", []),
        ("winner", "attackers"),
        ("deadline", "tomorrow"),
        ("rated_intent", True),
        ("ruleset_version", "1"),
        ("active", True),
    ],
)
def test_create_rejects_privileged_extra_fields(api, field, value):
    _, client = api
    assert client().post("/api/online/games", json=creation(**{field: value})).status_code == 422


@pytest.mark.parametrize("version", [True, "1", 1.0, None, -1])
def test_move_requires_explicit_integer_version(rig, api, version):
    game = active(rig)
    _, client = api
    move = move_for(game)
    response = client().post(
        f"/api/online/games/{game['id']}/moves", json={"version": version, **move.to_dict()}
    )
    assert response.status_code == 422
    assert rig.core.read(rig.credentials["Archer"], game["id"])["version"] == 1


def test_every_online_route_is_authenticated_and_gate_is_admin_only(rig, api):
    app, client = api
    game = active(rig)
    base = "/api/online/games/" + game["id"]
    anonymous = TestClient(app)
    for method, route, body in (
        ("GET", "/api/online/games", None),
        ("GET", base, None),
        ("GET", base + "/replay", None),
        ("GET", "/api/online/stats", None),
        ("GET", "/api/online/timeout-gate", None),
        ("POST", "/api/online/games", creation()),
        ("POST", base + "/join", {"version": 1}),
        ("POST", base + "/cancel", {"version": 1}),
        ("POST", base + "/moves", {"version": 1, "source": 0, "target": 1}),
        ("POST", base + "/concede", {"version": 1}),
        ("POST", base + "/void", {"version": 1, "note": "private"}),
        ("PATCH", "/api/online/timeout-gate", {"version": 0, "enabled": True, "note": "private"}),
    ):
        assert anonymous.request(method, route, json=body).status_code == 401
    for method in ("GET", "PATCH"):
        assert (
            client()
            .request(
                method,
                "/api/online/timeout-gate",
                json={"version": 0, "enabled": True, "note": "private"}
                if method == "PATCH"
                else None,
            )
            .status_code
            == 403
        )
    assert client("Odin").get("/api/online/timeout-gate").status_code == 200


def test_csrf_origin_json_and_body_limits_apply_to_online_actions(rig, api):
    _, client = api
    current = client()
    for headers in (
        {"X-CSRF-Token": "wrong"},
        {"Origin": "https://attacker.test"},
        {"Content-Type": "text/plain"},
    ):
        assert (
            current.post("/api/online/games", json=creation(), headers=headers).status_code == 403
        )
    current.headers.pop("Origin")
    assert current.post("/api/online/games", json=creation()).status_code == 403
    current.headers["Origin"] = "http://testserver"
    response = current.post(
        "/api/online/games",
        content=b"private-note" * 1000,
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413 and "private-note" not in response.text
    response = current.post(
        "/api/online/games",
        content=iter([b"x" * 5000, b"x" * 5000]),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413
    assert current.get("/api/online/games?limit=101").status_code == 422
    assert current.get("/api/online/games?offset=100001").status_code == 422
    assert dump(rig)["online_games"] == []


def test_safe_projections_and_admin_notes_are_never_bundled_into_games(rig, api):
    game = active(rig)
    rig.core.mutate(actor(rig, game), game["id"], 1, "concede")
    _, client = api
    note = "private recipient@example.test <script>secret diagnostic</script>"
    response = client("Odin").post(
        f"/api/online/games/{game['id']}/void", json={"version": 2, "note": note}
    )
    assert response.status_code == 200
    visitor = client("Visitor")
    forbidden = {
        "email",
        "password_hash",
        "csrf_token",
        "csrf_hash",
        "token_hash",
        "role",
        "active",
        "last_login",
        "email_verified_at",
        "correction_note",
        "corrected_by",
        "evaluations",
        "win_probability",
        "hint",
        "advisor",
        "diagnostics",
    }

    def assert_safe(value):
        if isinstance(value, dict):
            assert not forbidden.intersection(value)
            for nested in value.values():
                assert_safe(nested)
        elif isinstance(value, list):
            for nested in value:
                assert_safe(nested)

    for route in (
        f"/api/online/games/{game['id']}",
        f"/api/online/games/{game['id']}/replay",
        "/api/online/games",
        "/api/online/stats",
    ):
        data = visitor.get(route).json()
        assert_safe(data)
        assert note not in json.dumps(data) and "@example.test" not in json.dumps(data)
    with rig.store.connection() as c:
        assert c.execute("SELECT note FROM online_void_audit").fetchone()[0] == note


@pytest.mark.parametrize("who", ["Archer", "Visitor", "Odin"])
def test_all_legacy_analysis_ai_replay_and_branch_adapters_reject_online_ids(
    rig, api, monkeypatch, who
):
    from hnefatafl.analysis_service import AnalysisService

    game = active(rig)
    _, client = api

    def forbidden(*args, **kwargs):
        raise AssertionError("An online ID reached local analysis or AI")

    monkeypatch.setattr("hnefatafl.ai.analyze_position", forbidden)
    monkeypatch.setattr("hnefatafl.ai.choose_move", forbidden)
    monkeypatch.setattr(AnalysisService, "schedule", forbidden)
    base = "/api/games/" + game["id"]
    before = dump(rig)
    for method, route, body in (
        ("GET", base, None),
        ("GET", base + "/analytics", None),
        ("PUT", base + "/analytics", {}),
        ("POST", base + "/analysis", {"version": 1, "cursor": 0}),
        ("POST", base + "/analysis", {"version": 1, "cursor": 0, "background": True}),
        ("POST", base + "/ai-move", {"version": 1}),
        ("POST", base + "/seek", {"version": 1, "cursor": 0}),
        ("POST", base + "/undo", {"version": 1}),
        ("POST", base + "/redo", {"version": 1}),
        ("POST", base + "/restart", {"version": 1}),
        ("POST", base + "/moves", {"version": 1, "source": 0, "target": 1}),
        ("GET", base + "/replay", None),
        ("DELETE", base + "?version=1", None),
    ):
        response = client(who).request(method, route, json=body)
        assert response.status_code == 409, (route, response.text)
    assert dump(rig) == before
    assert client(who).get("/api/games").json() == []
    for suffix in ("analysis", "hints", "chances", "ai-move", "seek", "undo"):
        assert client(who).post(
            f"/api/online/games/{game['id']}/{suffix}", json={"version": 1}
        ).status_code in (403, 404)


def test_private_singleplayer_ownership_stays_separate(rig, api):
    _, client = api
    archer, visitor = client(), client("Visitor")
    local = archer.post("/api/games", json={"ruleset": "brandubh"})
    assert local.status_code == 201
    identifier = local.json()["id"]
    assert visitor.get("/api/games/" + identifier).status_code == 404
    assert visitor.get("/api/games/" + identifier + "/replay").status_code == 404
    assert visitor.get("/api/online/games/" + identifier).status_code == 404
    assert archer.get("/api/games/" + identifier).status_code == 200
    assert archer.get("/api/online/games").json()["games"] == []


def test_session_revoke_after_middleware_identity_read_prevents_mutation(rig, api, monkeypatch):
    app, client = api
    game = active(rig)
    original = app.state.online.mutate

    def revoked(*args, **kwargs):
        app.state.auth.revoke(rig.tokens["Archer"])
        return original(*args, **kwargs)

    monkeypatch.setattr(app.state.online, "mutate", revoked)
    response = client().post(f"/api/online/games/{game['id']}/concede", json={"version": 1})
    assert response.status_code == 401
    assert rig.core.read(rig.credentials["Keeper"], game["id"])["status"] == "active"


def test_unapproved_policies_stay_disabled_and_existing_games_remain_readable(rig, api):
    app, client = api
    game = active(rig)
    app.state.online.settings = OnlineSettings(enabled=True, creation_policy_approved=False)
    assert client().post("/api/online/games", json=creation()).status_code == 503
    assert client("Visitor").get("/api/online/games/" + game["id"]).status_code == 200
    assert (
        client("Odin")
        .patch(
            "/api/online/timeout-gate", json={"version": 0, "enabled": True, "note": "Not approval"}
        )
        .status_code
        == 503
    )
    assert (
        client("Odin")
        .patch(
            "/api/online/timeout-gate",
            json={"version": 0, "enabled": False, "note": "Incident pause"},
        )
        .status_code
        == 200
    )


@pytest.mark.parametrize("paused", [True, False])
def test_lifespan_startup_reconciles_bounded_expiry_and_shuts_down_worker(rig, paused):
    game = active(rig)
    if not paused:
        resume(rig)
    rig.clock.value += timedelta(seconds=61)
    app = create_app(
        rig.root,
        online_settings=replace(POLICY, sweep_seconds=300),
        online_clock=rig.clock,
        account_background=False,
    )
    with TestClient(app):
        assert app.state.online.thread and app.state.online.thread.is_alive()
        current = rig.core.read(rig.credentials["Keeper"], game["id"])
        assert current["status"] == ("active" if paused else "completed")
        if paused:
            assert current["deadline"] == game["deadline"]
    assert app.state.online.stop.is_set() and not app.state.online.thread.is_alive()


def test_deployment_freeze_blocks_http_and_background_expiry(rig, api):
    import fcntl

    game = active(rig)
    resume(rig)
    rig.clock.value += timedelta(seconds=61)
    _, client = api
    with rig.auth.gate_path.open("rb") as gate:
        fcntl.flock(gate, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert (
            client()
            .post(f"/api/online/games/{game['id']}/concede", json={"version": 1})
            .status_code
            == 503
        )
        assert rig.core.sweep()["blocked"] == 1
        assert rig.core.read(rig.credentials["Keeper"], game["id"])["status"] == "active"
    assert rig.core.sweep()["finalized"] == 1
