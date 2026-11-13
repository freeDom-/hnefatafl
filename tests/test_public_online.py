"""Public online discovery: signed-out reads, filters, paging, limits and no leak.

Every assertion uses the real ASGI boundary, isolated SQLite storage and the
shared online fixture. No test claims a stronger guarantee than the request it
actually performs.
"""

from dataclasses import replace
from datetime import timedelta
import hashlib
import json
import sqlite3
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


def replay_state(game):
    state = new_game(game["ruleset"])
    for move in game["moves"]:
        state = apply_move(state, Move(move["source"], move["target"]))
    return state


def stored_analysis_key(state):
    return hashlib.sha256(
        json.dumps(state.to_dict(), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def store_analysis(rig, game, key, report):
    with rig.store.connection() as c:
        c.execute(
            "INSERT INTO game_analysis(game_id,position_key,data) VALUES(?,?,?)",
            (game["id"], key, json.dumps(report)),
        )


def analysis_snapshot(rig, game, key):
    with rig.store.connection() as c:
        return (
            c.execute("SELECT COUNT(*) FROM game_analysis").fetchone()[0],
            c.execute("SELECT COUNT(*) FROM advisor_analysis").fetchone()[0],
            c.execute("SELECT COUNT(*) FROM entity_runes").fetchone()[0],
            c.execute(
                "SELECT data FROM game_analysis WHERE game_id=? AND position_key=?",
                (game["id"], key),
            ).fetchone()[0],
        )


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


def test_public_replay_projects_stored_analysis_onto_the_public_allowlist(rig, api):
    _, client = api
    game = completed(rig)
    state = new_game(game["ruleset"])
    for move in game["moves"]:
        state = apply_move(state, Move(move["source"], move["target"]))
    key = hashlib.sha256(
        json.dumps(state.to_dict(), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    # A realistic stored report: public values, a nested agent configuration
    # (filesystem path, seed, search block), internal metadata and unexpected
    # extra properties at every level.
    report = {
        "cursor": len(game["moves"]),
        "ply": state.ply,
        "side_to_move": state.turn,
        "position_key": key,
        "created_at": "2027-01-02T03:04:05.123456Z",
        "partial": True,
        "status": "pending",
        "advisor_revision": 7,
        "private_notes": "operator only",
        "unexpected_internal_field": "must not survive",
        "evaluations": [
            {
                "agent_id": "defenders",
                "role": "defenders",
                "name": "Keeper",
                "rune": "ᚠᛇ",
                "profile_id": "seer",
                "profile_name": "Seer",
                "model_name": "trained-model",
                "config": {
                    "algorithm": "mcts",
                    "seed": 12345,
                    "iterations": 1024,
                    "model_id": "registered-model-id",
                    "model_path": "/srv/hnefatafl/models/secret.pt",
                    "search": {"depth": 5, "internal": True},
                },
                "source": "mcts_rollout",
                "value": 0.5,
                "win_probability": 0.75,
                "estimated": True,
                "calibrated": False,
                "best_move": {"source": 3, "target": 11, "note": "internal"},
                "search_seed": 4242,
                "nodes": 9999,
                "transposition_hits": 12,
                "private_note": "operator only",
                "unexpected": {"token": "secret"},
            }
        ],
        "hint": {
            "agent_id": "advisor",
            "move": {"source": 3, "target": 11, "ply": 1},
            "note": "internal",
        },
    }
    with rig.store.connection() as c:
        c.execute(
            "INSERT INTO game_analysis(game_id,position_key,data) VALUES(?,?,?)",
            (game["id"], key, json.dumps(report)),
        )

    def snapshot():
        with rig.store.connection() as c:
            return (
                c.execute("SELECT COUNT(*) FROM game_analysis").fetchone()[0],
                c.execute("SELECT COUNT(*) FROM advisor_analysis").fetchone()[0],
                c.execute("SELECT COUNT(*) FROM entity_runes").fetchone()[0],
                c.execute(
                    "SELECT data FROM game_analysis WHERE game_id=? AND position_key=?",
                    (game["id"], key),
                ).fetchone()[0],
            )

    before = snapshot()
    response = client.get(f"{PUBLIC}/{game['id']}/replay")
    assert response.status_code == 200
    analysis = response.json()["analysis"]
    assert analysis == {
        "cursor": len(game["moves"]),
        "ply": state.ply,
        "side_to_move": state.turn,
        "created_at": "2027-01-02T03:04:05.123456Z",
        "evaluations": [
            {
                "agent_id": "defenders",
                "role": "defenders",
                "name": "Keeper",
                "profile_name": "Seer",
                "model_name": "trained-model",
                "source": "mcts_rollout",
                "value": 0.5,
                "win_probability": 0.75,
                "best_move": {"source": 3, "target": 11},
                "estimated": True,
                "calibrated": False,
            }
        ],
        "hint": {"agent_id": "advisor", "move": {"source": 3, "target": 11}},
    }
    # The projection is recursive, so no nested key escapes the allowlist.
    scan_not_present(
        analysis,
        {
            "config",
            "rune",
            "profile_id",
            "search_seed",
            "nodes",
            "transposition_hits",
            "private_note",
            "private_notes",
            "unexpected",
            "unexpected_internal_field",
            "model_path",
            "position_key",
            "partial",
            "status",
            "advisor_revision",
            "token",
        },
    )
    # A public GET neither rewrites the stored report nor touches the counters.
    assert snapshot() == before


def test_public_replay_omits_malformed_stored_analysis_fields(rig, api):
    _, client = api
    game = completed(rig)
    state = replay_state(game)
    key = stored_analysis_key(state)
    # Valid public values alongside deliberately malformed ones: an approved key
    # holding a nested object, array or wrong scalar must be dropped rather than
    # copied into the signed-out response.
    report = {
        "cursor": len(game["moves"]),
        "ply": {"value": state.ply},
        "side_to_move": state.turn,
        "created_at": "2027-01-02T03:04:05.123456Z",
        "evaluations": [
            {
                "agent_id": "defenders",
                "role": "defenders",
                "name": "Keeper",
                "profile_name": "Seer",
                "model_name": "trained-model",
                "source": "mcts_rollout",
                "value": 0.5,
                "win_probability": 0.75,
                "best_move": {"source": 3, "target": 11},
                "estimated": True,
                "calibrated": False,
            },
            {
                "agent_id": {"id": "defenders"},
                "role": ["defenders"],
                "name": 7,
                "profile_name": {"profile": "seer"},
                "model_name": {"model_path": "/srv/hnefatafl/models/secret.pt"},
                "source": {"algorithm": "mcts"},
                "value": {"value": 0.5},
                "win_probability": {"model_path": "/srv/hnefatafl/models/secret.pt"},
                "best_move": {"source": {"square": 3}, "target": [11]},
                "estimated": "yes",
                "calibrated": 1,
                "unavailable_reason": {"reason": "evaluation_failed"},
            },
            "not-an-entry",
            None,
        ],
        "hint": {
            "agent_id": "advisor",
            "move": {"source": [3], "target": {"square": 11}},
        },
    }
    store_analysis(rig, game, key, report)
    before = analysis_snapshot(rig, game, key)

    response = client.get(f"{PUBLIC}/{game['id']}/replay")
    assert response.status_code == 200
    assert response.json()["analysis"] == {
        "cursor": len(game["moves"]),
        "side_to_move": state.turn,
        "created_at": "2027-01-02T03:04:05.123456Z",
        "evaluations": [
            {
                "agent_id": "defenders",
                "role": "defenders",
                "name": "Keeper",
                "profile_name": "Seer",
                "model_name": "trained-model",
                "source": "mcts_rollout",
                "value": 0.5,
                "win_probability": 0.75,
                "best_move": {"source": 3, "target": 11},
                "estimated": True,
                "calibrated": False,
            },
            {},
        ],
        "hint": {"agent_id": "advisor"},
    }
    # No nested key, filesystem path or exception string from a malformed value
    # survives the projection.
    scan_not_present(
        response.json()["analysis"],
        {"id", "profile", "model_path", "algorithm", "square", "reason"},
    )
    assert "secret.pt" not in response.text and "evaluation_failed" not in response.text
    # A public GET neither rewrites the stored report nor touches the counters.
    assert analysis_snapshot(rig, game, key) == before


def test_public_replay_rejects_bool_non_finite_and_out_of_range_analysis_values(rig, api):
    _, client = api
    game = completed(rig)
    state = replay_state(game)
    key = stored_analysis_key(state)
    # ``bool`` is an ``int`` subclass, a win chance is bounded to a probability,
    # move coordinates must fit the reported board and non-finite numbers cannot
    # be serialised as JSON at all.
    report = {
        "cursor": True,
        "ply": state.ply,
        "side_to_move": state.turn,
        "created_at": "not-a-timestamp",
        "evaluations": [
            {
                "agent_id": "attackers",
                "value": True,
                "win_probability": 1.5,
                "estimated": None,
                "calibrated": True,
                "best_move": {"source": 9999, "target": 3},
            },
            {
                "agent_id": None,
                "value": float("nan"),
                "win_probability": float("inf"),
                "best_move": None,
            },
        ],
        "hint": {"agent_id": "advisor", "move": {"source": 0, "target": state.size**2}},
    }
    store_analysis(rig, game, key, report)
    before = analysis_snapshot(rig, game, key)

    response = client.get(f"{PUBLIC}/{game['id']}/replay")
    assert response.status_code == 200
    assert response.json()["analysis"] == {
        "ply": state.ply,
        "side_to_move": state.turn,
        "evaluations": [
            {"agent_id": "attackers", "estimated": None, "calibrated": True},
            {"agent_id": None, "best_move": None},
        ],
        "hint": {"agent_id": "advisor"},
    }
    # A non-finite stored value never reaches the response as invalid JSON.
    assert "NaN" not in response.text and "Infinity" not in response.text
    assert analysis_snapshot(rig, game, key) == before


def test_public_analysis_cannot_leak_an_internal_config_under_an_approved_key(rig, api):
    _, client = api
    game = completed(rig)
    state = replay_state(game)
    key = stored_analysis_key(state)
    # The whole effective search configuration (model filesystem path, seed and
    # nested search block) is smuggled into every approved key at every level.
    configuration = {
        "algorithm": "mcts",
        "seed": 12345,
        "model_id": "registered-model-id",
        "model_path": "/srv/hnefatafl/models/secret.pt",
        "search": {"depth": 5, "internal": True},
    }
    report = {
        "cursor": configuration,
        "ply": configuration,
        "side_to_move": configuration,
        "created_at": configuration,
        "evaluations": [
            {
                field: configuration
                for field in (
                    "agent_id",
                    "role",
                    "name",
                    "profile_name",
                    "model_name",
                    "source",
                    "value",
                    "win_probability",
                    "best_move",
                    "estimated",
                    "calibrated",
                    "unavailable_reason",
                )
            }
        ],
        "hint": {"agent_id": configuration, "move": configuration},
    }
    store_analysis(rig, game, key, report)
    before = analysis_snapshot(rig, game, key)

    response = client.get(f"{PUBLIC}/{game['id']}/replay")
    assert response.status_code == 200
    # Every approved key held the wrong type, so nothing is published but the
    # fixed public shape.
    assert response.json()["analysis"] == {"evaluations": [{}], "hint": {}}
    scan_not_present(
        response.json()["analysis"], set(configuration) | {"model_path", "seed", "search"}
    )
    assert "secret.pt" not in response.text and "registered-model-id" not in response.text
    assert analysis_snapshot(rig, game, key) == before


def test_active_games_expose_no_cached_analysis_through_any_public_endpoint(rig, api):
    _, client = api
    game = active(rig)
    state = new_game(game["ruleset"])
    key = hashlib.sha256(
        json.dumps(state.to_dict(), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    stored = {
        "cursor": 0,
        "ply": 0,
        "side_to_move": state.turn,
        "position_key": key,
        "created_at": "2027-01-02T03:04:05.123456Z",
        "evaluations": [
            {
                "agent_id": "attackers",
                "name": "Keeper",
                "config": {"model_path": "/srv/hnefatafl/models/secret.pt"},
            }
        ],
        "hint": None,
    }
    with rig.store.connection() as c:
        c.execute(
            "INSERT INTO game_analysis(game_id,position_key,data) VALUES(?,?,?)",
            (game["id"], key, json.dumps(stored)),
        )
    detail = client.get(f"{PUBLIC}/{game['id']}")
    assert detail.status_code == 200
    scan_not_present(detail.json(), {"analysis", "evaluations", "hint"})
    assert client.get(f"{PUBLIC}/{game['id']}/replay").status_code == 404
    # There is no public analysis route; an unknown path is denied, not served.
    assert client.get(f"{PUBLIC}/{game['id']}/analysis").status_code in {401, 403, 404, 405}
    assert all("analysis" not in item for item in client.get(PUBLIC).json()["items"])
    with rig.store.connection() as c:
        assert c.execute(
            "SELECT data FROM game_analysis WHERE game_id=? AND position_key=?",
            (game["id"], key),
        ).fetchone()[0] == json.dumps(stored)


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
    assert client.get(f"{PUBLIC}?player=_").json()["items"] == []
    assert client.get(f"{PUBLIC}?player=%27%20OR%201%3D1--").json()["items"] == []
    assert client.get(f"{PUBLIC}?player={'a' * 149}").status_code == 422
    assert client.get(f"{PUBLIC}?player=").status_code == 422


def test_player_search_folds_non_ascii_case_in_published_names(rig, api):
    _, client = api
    game = active(rig)
    with rig.store.connection() as c:
        c.execute(
            "UPDATE accounts SET display_name=? WHERE id=?",
            ("JÖRG MÜLLER", rig.accounts["Keeper"]["id"]),
        )
    # A lowercase query matches an uppercase accented published label.
    assert {item["id"] for item in client.get(f"{PUBLIC}?player=müller").json()["items"]} == {
        game["id"]
    }
    assert {item["id"] for item in client.get(f"{PUBLIC}?player=jörg").json()["items"]} == {
        game["id"]
    }
    assert {item["id"] for item in client.get(f"{PUBLIC}?player=MÜLLER").json()["items"]} == {
        game["id"]
    }
    # Full case folding also folds the German sharp s (Straße -> strasse).
    with rig.store.connection() as c:
        c.execute(
            "UPDATE accounts SET display_name=? WHERE id=?",
            ("Straße", rig.accounts["Keeper"]["id"]),
        )
    assert {item["id"] for item in client.get(f"{PUBLIC}?player=STRASSE").json()["items"]} == {
        game["id"]
    }
    assert client.get(f"{PUBLIC}?player=nobody").json()["items"] == []


def test_player_search_only_covers_published_labels(rig, api):
    _, client = api
    active(rig)
    with rig.store.connection() as c:
        c.execute(
            "UPDATE accounts SET display_name=?, profile_private_fields='[\"display_name\"]' "
            "WHERE id=?",
            ("Verborgener Öl", rig.accounts["Keeper"]["id"]),
        )
    # A withheld display name is not searchable, but the public handle stays so.
    assert client.get(f"{PUBLIC}?player=Verborgener").json()["items"] == []
    assert client.get(f"{PUBLIC}?player=Öl").json()["items"] == []
    assert client.get(f"{PUBLIC}?player=keeper").json()["items"]


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


def test_rated_match_without_an_applied_rating_event_is_published_unrated(rig, api):
    _, client = api
    game = active(rig)
    # An unverified participant makes the rated match ineligible for the ledger,
    # so no rating event is applied even though the match itself stays rated.
    with rig.store.connection() as c:
        c.execute(
            "UPDATE accounts SET email_verified_at=NULL WHERE id=?",
            (rig.accounts["Keeper"]["id"],),
        )
    finished = rig.core.mutate(actor(rig, game), game["id"], game["version"], "concede")["game"]
    assert finished["result"]["rated_eligible"] is False
    with rig.store.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM rating_events").fetchone()[0] == 0
    detail = client.get(f"{PUBLIC}/{game['id']}").json()
    assert detail["rated"] is True  # The match-level intent is unchanged.
    assert detail["result"]["rated"] is False
    assert detail["result"]["winner_delta"] is None
    item = next(row for row in client.get(PUBLIC).json()["items"] if row["id"] == game["id"])
    assert item["result"]["rated"] is False and item["result"]["winner_delta"] is None


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


# The security boundary owns Cache-Control: it strips every handler directive and
# emits exactly one. Only an anonymous completed read may be shared; every other
# /api/ response, including errors, is forced to no-store.


def cache_directives(response):
    return response.headers.get_list("cache-control")


def test_anonymous_completed_detail_is_publicly_cacheable(rig, api):
    _, client = api
    game = completed(rig)
    response = client.get(f"{PUBLIC}/{game['id']}")
    assert response.status_code == 200
    assert cache_directives(response) == ["public, max-age=60"]


def test_anonymous_completed_replay_is_publicly_cacheable(rig, api):
    _, client = api
    game = completed(rig)
    response = client.get(f"{PUBLIC}/{game['id']}/replay")
    assert response.status_code == 200
    assert cache_directives(response) == ["public, max-age=60"]


def test_anonymous_live_detail_is_not_cacheable(rig, api):
    _, client = api
    game = active(rig)
    response = client.get(f"{PUBLIC}/{game['id']}")
    assert response.status_code == 200
    assert cache_directives(response) == ["no-store"]


def test_anonymous_listing_is_not_cacheable(rig, api):
    _, client = api
    active(rig)
    response = client.get(PUBLIC)
    assert response.status_code == 200
    assert cache_directives(response) == ["no-store"]


def test_public_error_responses_are_never_publicly_cacheable(rig, api, monkeypatch):
    app, client = api
    game = completed(rig)
    missing = client.get(f"{PUBLIC}/missing")
    assert missing.status_code == 404 and cache_directives(missing) == ["no-store"]
    malformed = client.get(f"{PUBLIC}/{game['id']}!")
    assert malformed.status_code == 422 and cache_directives(malformed) == ["no-store"]

    limited = create_app(
        rig.root,
        online_settings=replace(POLICY, public_rate_limit=1, public_rate_window_seconds=60),
        online_clock=rig.clock,
        online_background=False,
        account_background=False,
    )
    throttled_client = TestClient(limited)
    assert throttled_client.get(PUBLIC).status_code == 200
    throttled = throttled_client.get(PUBLIC)
    assert throttled.status_code == 429 and cache_directives(throttled) == ["no-store"]

    def unavailable(*args, **kwargs):
        raise sqlite3.Error("unavailable")

    monkeypatch.setattr(app.state.public_online, "detail", unavailable)
    broken = client.get(f"{PUBLIC}/{game['id']}")
    assert broken.status_code == 503 and cache_directives(broken) == ["no-store"]

    # The public namespace has no 403 path; a boundary denial is still no-store.
    denied = signed_in(app, rig, "Visitor").get("/api/training/jobs")
    assert denied.status_code == 403 and cache_directives(denied) == ["no-store"]


def test_no_response_carries_duplicate_cache_control_directives(rig, api):
    _, client = api
    game = completed(rig)
    live = active(rig)
    responses = [
        client.get(PUBLIC),
        client.get(f"{PUBLIC}/{game['id']}"),
        client.get(f"{PUBLIC}/{game['id']}/replay"),
        client.get(f"{PUBLIC}/{live['id']}"),
        client.get(f"{PUBLIC}/missing"),
        client.get(f"{PUBLIC}/{game['id']}!"),
        client.get("/api/profile"),
        client.get("/play", follow_redirects=False),
    ]
    for response in responses:
        assert len(cache_directives(response)) == 1, (response.url, response.headers)
