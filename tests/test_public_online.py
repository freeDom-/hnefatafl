"""Public online discovery: signed-out reads, filters, paging, limits and no leak.

Every assertion uses the real ASGI boundary, isolated SQLite storage and the
shared online fixture. No test claims a stronger guarantee than the request it
actually performs.
"""

from dataclasses import replace
from datetime import timedelta
import hashlib
import json
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from hnefatafl.auth import COOKIE, digest
from hnefatafl.engine import Move, apply_move, new_game
from hnefatafl.server import create_app
from test_online_core import (
    POLICY,
    account_template as account_template,
    active,
    actor,
    dump,
    rig as rig,
)

PUBLIC = "/api/public/online/games"


@pytest.fixture
def api(rig):
    app = create_app(
        rig.root,
        online_settings=POLICY,
        online_clock=rig.clock,
        online_background=False,
        account_background=False,
    )
    return app, TestClient(app)


def signed_in(app, rig, name="Archer"):
    client = TestClient(app)
    client.cookies.set(COOKIE, rig.tokens[name])
    client.headers.update(
        {"Origin": "http://testserver", "X-CSRF-Token": digest("csrf:" + rig.tokens[name])}
    )
    return client


def waiting(rig, rule="brandubh", name="Archer"):
    return rig.core.create(rig.credentials[name], rule, "attackers", 60, uuid4().hex)["game"]


def completed(rig, rule="brandubh"):
    game = waiting(rig, rule)
    game = rig.core.mutate(rig.credentials["Keeper"], game["id"], 0, "join")["game"]
    # The player to move concedes, so the other side is the effective winner.
    return rig.core.mutate(actor(rig, game), game["id"], 1, "concede")["game"]


def scan_not_present(value, forbidden):
    if isinstance(value, dict):
        assert not (forbidden & set(value))
        for nested in value.values():
            scan_not_present(nested, forbidden)
    elif isinstance(value, list):
        for nested in value:
            scan_not_present(nested, forbidden)


FORBIDDEN = {
    "email",
    "account_id",
    "creator_id",
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
    "analysis",
    "evaluations",
    "hint",
    "advisor",
    "win_probability",
    "win_chance",
    "best_move",
    "suggestion",
    "legality",
}


def test_signed_out_and_signed_in_spectators_read_the_same_public_index(rig, api):
    app, anonymous = api
    live = active(rig)
    finished = completed(rig)
    listing = anonymous.get(PUBLIC)
    assert listing.status_code == 200
    assert listing.headers["cache-control"] == "no-store"
    items = {item["id"]: item for item in listing.json()["items"]}
    assert set(items) == {live["id"], finished["id"]}
    assert items[live["id"]]["status"] == "active"
    assert items[finished["id"]]["status"] == "completed"
    assert items[finished["id"]]["result"]["winner"] in {"attackers", "defenders", "draw"}
    scan_not_present(listing.json(), FORBIDDEN)

    spectator = signed_in(app, rig, "Visitor")
    assert spectator.get(PUBLIC).json() == listing.json()
    assert spectator.get(f"{PUBLIC}/{finished['id']}").status_code == 200


def test_live_detail_exposes_a_read_only_position_without_moves_or_assistance(rig, api):
    _, client = api
    game = active(rig)
    response = client.get(f"{PUBLIC}/{game['id']}")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["status"] == "active" and body["position"]["board"]
    assert body["position"]["ply"] == 0
    assert body["moves"] is None and body["result"] is None
    scan_not_present(body, FORBIDDEN)


def test_completed_detail_and_replay_are_cached_and_state_validated(rig, api):
    _, client = api
    game = completed(rig)
    detail = client.get(f"{PUBLIC}/{game['id']}")
    assert detail.status_code == 200
    assert detail.headers["cache-control"] == "public, max-age=60"
    body = detail.json()
    assert body["status"] == "completed" and body["moves"] is not None
    replay = client.get(f"{PUBLIC}/{game['id']}/replay")
    assert replay.status_code == 200
    assert replay.headers["cache-control"] == "public, max-age=60"
    payload = replay.json()
    assert payload["validated"] is True
    assert len(payload["states"]) == len(payload["moves"]) + 1
    assert payload["states"][-1] == body["position"]
    assert payload["analysis"] is None
    scan_not_present(payload, FORBIDDEN - {"analysis"})


def test_public_request_generates_no_analysis_and_reserves_no_runes(rig, api):
    _, client = api
    game = completed(rig)

    def forbidden(*args, **kwargs):
        raise AssertionError("A public request reached inference")

    import hnefatafl.ai as ai

    original_analyze, original_choose = ai.analyze_position, ai.choose_move
    ai.analyze_position, ai.choose_move = forbidden, forbidden
    try:
        with rig.store.connection() as c:
            before = (
                c.execute("SELECT COUNT(*) FROM game_analysis").fetchone()[0],
                c.execute("SELECT COUNT(*) FROM advisor_analysis").fetchone()[0],
                c.execute("SELECT COUNT(*) FROM entity_runes").fetchone()[0],
            )
        assert client.get(f"{PUBLIC}/{game['id']}").status_code == 200
        assert client.get(f"{PUBLIC}/{game['id']}/replay").status_code == 200
    finally:
        ai.analyze_position, ai.choose_move = original_analyze, original_choose
    with rig.store.connection() as c:
        assert (
            c.execute("SELECT COUNT(*) FROM game_analysis").fetchone()[0],
            c.execute("SELECT COUNT(*) FROM advisor_analysis").fetchone()[0],
            c.execute("SELECT COUNT(*) FROM entity_runes").fetchone()[0],
        ) == before


def test_public_replay_surfaces_only_already_stored_analysis(rig, api):
    _, client = api
    game = completed(rig)
    state = new_game(game["ruleset"])
    for move in game["moves"]:
        state = apply_move(state, Move(move["source"], move["target"]))
    key = hashlib.sha256(
        json.dumps(state.to_dict(), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    snapshot = {
        "cursor": len(game["moves"]),
        "ply": state.ply,
        "side_to_move": state.turn,
        "position_key": key,
        "created_at": "2027-01-02T03:04:05.123456Z",
        "evaluations": [],
        "hint": None,
    }
    with rig.store.connection() as c:
        c.execute(
            "INSERT INTO game_analysis(game_id,position_key,data) VALUES(?,?,?)",
            (game["id"], key, json.dumps(snapshot)),
        )
    assert client.get(f"{PUBLIC}/{game['id']}/replay").json()["analysis"] == snapshot


def test_single_player_and_local_records_never_reach_the_public_namespace(rig, api):
    app, client = api
    signed = signed_in(app, rig)
    single = signed.post("/api/games", json={"ruleset": "brandubh"})
    local = signed.post("/api/games/local", json={"ruleset": "brandubh"})
    assert single.status_code == 201 and local.status_code == 201
    private = {single.json()["id"], local.json()["id"]}
    listing = client.get(PUBLIC).json()
    assert all(item["id"] not in private for item in listing["items"])
    for identifier in private:
        assert client.get(f"{PUBLIC}/{identifier}").status_code == 404
        assert client.get(f"{PUBLIC}/{identifier}/replay").status_code == 404


def test_public_filters_are_server_side_and_bounded(rig, api):
    _, client = api
    brandubh = active(rig, "brandubh")
    tablut = active(rig, "tablut")
    finished = completed(rig, "copenhagen")
    assert {item["id"] for item in client.get(f"{PUBLIC}?ruleset=tablut").json()["items"]} == {
        tablut["id"]
    }
    assert {item["id"] for item in client.get(f"{PUBLIC}?status=completed").json()["items"]} == {
        finished["id"]
    }
    winner = finished["result"]["winner"]
    assert {item["id"] for item in client.get(f"{PUBLIC}?outcome={winner}").json()["items"]} == {
        finished["id"]
    }
    assert (
        client.get(f"{PUBLIC}?ruleset=brandubh&status=active").json()["items"][0]["id"]
        == (brandubh["id"])
    )
    assert client.get(f"{PUBLIC}?ruleset=unknown").status_code == 422
    assert client.get(f"{PUBLIC}?status=unknown").status_code == 422
    assert client.get(f"{PUBLIC}?outcome=unknown").status_code == 422


def test_player_search_matches_public_names_and_handles_only(rig, api):
    _, client = api
    game = active(rig)
    assert {item["id"] for item in client.get(f"{PUBLIC}?player=kee").json()["items"]} == {
        game["id"]
    }
    assert {item["id"] for item in client.get(f"{PUBLIC}?player=ARCHER").json()["items"]} == {
        game["id"]
    }
    assert client.get(f"{PUBLIC}?player=nobody").json()["items"] == []
    # Wildcards and quotes are literal input, not a capability or an error.
    assert client.get(f"{PUBLIC}?player=%25").json()["items"] == []
    assert client.get(f"{PUBLIC}?player=%27%20OR%201%3D1--").json()["items"] == []
    assert client.get(f"{PUBLIC}?player={'a' * 149}").status_code == 422
    assert client.get(f"{PUBLIC}?player=").status_code == 422


def test_cursor_paging_is_opaque_and_bound_to_the_filter_set(rig, api):
    _, client = api
    for _ in range(3):
        waiting(rig)
        rig.clock.value += timedelta(seconds=1)
    first = client.get(f"{PUBLIC}?limit=2")
    assert first.status_code == 200
    page = first.json()
    assert len(page["items"]) == 2 and page["next_cursor"]
    second = client.get(f"{PUBLIC}?limit=2&cursor={page['next_cursor']}").json()
    assert len(second["items"]) == 1 and second["next_cursor"] is None
    assert {item["id"] for item in [*page["items"], *second["items"]]} == {
        item["id"] for item in client.get(PUBLIC).json()["items"]
    }
    for bad in ("not-a-cursor", "@@@@", "a" * 513):
        assert client.get(f"{PUBLIC}?cursor={bad}").status_code == 422
    assert client.get(f"{PUBLIC}?limit=0").status_code == 422
    assert client.get(f"{PUBLIC}?limit=101").status_code == 422
    # A cursor minted under one filter set is rejected under another.
    other = client.get(f"{PUBLIC}?limit=1&status=waiting").json()
    assert (
        client.get(f"{PUBLIC}?limit=1&status=active&cursor={other['next_cursor']}").status_code
        == 422
    )


def test_per_ip_rate_limit_is_stricter_than_authenticated_lobby_polling(rig):
    app = create_app(
        rig.root,
        online_settings=replace(POLICY, public_rate_limit=3, public_rate_window_seconds=60),
        online_clock=rig.clock,
        online_background=False,
        account_background=False,
    )
    client = TestClient(app)
    for _ in range(3):
        assert client.get(PUBLIC).status_code == 200
    limited = client.get(PUBLIC)
    assert limited.status_code == 429 and "too many" in limited.json()["detail"].lower()
    # An unrelated authenticated route is unaffected by the public limiter.
    assert signed_in(app, rig, "Visitor").get("/api/online/games").status_code == 200


def test_unknown_and_live_replay_ids_return_a_consistent_404(rig, api):
    _, client = api
    game = active(rig)
    for identifier in ("online-" + "0" * 32, "online-" + uuid4().hex, "missing"):
        assert client.get(f"{PUBLIC}/{identifier}").status_code == 404
    assert client.get(f"{PUBLIC}/{game['id']}/replay").status_code == 404
    assert client.get(f"{PUBLIC}/{game['id'] + '!'}").status_code == 422


def test_voided_results_are_published_as_ineligible_not_as_a_win(rig, api):
    _, client = api
    game = completed(rig)
    voided = rig.core.mutate(
        rig.credentials["Odin"], game["id"], game["version"], "void", note="Test incident"
    )
    assert voided["game"]["status"] == "voided"
    detail = client.get(f"{PUBLIC}/{game['id']}").json()
    assert detail["status"] == "voided"
    assert detail["result"]["winner"] is None
    assert detail["result"]["rated"] is False and detail["result"]["winner_delta"] is None
    assert detail["result"]["termination_reason"] == "administrative_void"
    assert client.get(f"{PUBLIC}/{game['id']}/replay").status_code == 404


def test_public_namespace_is_get_only_and_never_mutates(rig, api):
    app, client = api
    game = active(rig)
    paths = {route.path for route in app.router.routes}
    assert f"{PUBLIC}/{{game_id}}/analysis" not in paths
    before = dump(rig)
    for method, route in (
        ("POST", PUBLIC),
        ("POST", f"{PUBLIC}/{game['id']}/join"),
        ("POST", f"{PUBLIC}/{game['id']}/moves"),
        ("POST", f"{PUBLIC}/{game['id']}/concede"),
        ("POST", f"{PUBLIC}/{game['id']}/void"),
        ("POST", f"{PUBLIC}/{game['id']}/analysis"),
        ("PATCH", f"{PUBLIC}/{game['id']}"),
        ("DELETE", f"{PUBLIC}/{game['id']}"),
    ):
        assert client.request(method, route, json={"version": 1}).status_code in {
            401,
            403,
            404,
            405,
        }
    assert dump(rig) == before


def test_public_participants_never_expose_an_unpublished_display_name(rig, api):
    _, client = api
    game = active(rig)
    with rig.store.connection() as c:
        c.execute(
            "UPDATE accounts SET display_name='Withheld Secret', "
            "profile_private_fields='[\"display_name\"]' WHERE id=?",
            (rig.accounts["Keeper"]["id"],),
        )
    body = client.get(f"{PUBLIC}/{game['id']}").json()
    assert "Withheld Secret" not in json.dumps(body)
    defender = body["participants"]["defenders"]
    assert defender["display_name"] == "Keeper" and defender["username"] == "Keeper"


def test_waiting_games_are_listed_without_a_move_list(rig, api):
    _, client = api
    game = waiting(rig)
    item = client.get(PUBLIC).json()["items"][0]
    assert item["id"] == game["id"] and item["status"] == "waiting"
    assert item["participants"]["attackers"]["display_name"] == "Archer"
    assert item["participants"]["defenders"] is None
    assert item["result"] is None
    detail = client.get(f"{PUBLIC}/{game['id']}").json()
    assert detail["moves"] is None and detail["deadline"] is None


def test_feature_flag_withdrawal_hides_the_whole_namespace(rig):
    game = active(rig)
    app = create_app(
        rig.root,
        online_settings=replace(POLICY, public_discovery_enabled=False),
        online_clock=rig.clock,
        online_background=False,
        account_background=False,
    )
    client = TestClient(app)
    assert client.get(PUBLIC).status_code == 404
    assert client.get(f"{PUBLIC}/{game['id']}").status_code == 404
    assert client.get(f"{PUBLIC}/{game['id']}/replay").status_code == 404
    # The authenticated routes keep working exactly as before.
    assert signed_in(app, rig, "Visitor").get("/api/online/games").status_code == 200
