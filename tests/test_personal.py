"""Personal reads on real sessions/SQLite with deterministic authoritative time."""

from record_fixtures import historical_response
from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta, timezone
import json
import logging
import sqlite3
import shutil
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest

from hnefatafl.auth import COOKIE, digest
from hnefatafl.online_settings import OnlineSettings
from hnefatafl.server import create_app

ORIGIN = {"Origin": "http://testserver"}


@pytest.fixture(scope="module")
def seed(tmp_path_factory):
    root = tmp_path_factory.mktemp("personal-seed")
    app = create_app(root, online_background=False, account_background=False)
    sessions, accounts = {}, {}
    with TestClient(app) as client:
        for name in ("Archer", "Keeper", "Observer", "Odin"):
            client.cookies.clear()
            accounts[name] = app.state.auth.create_account(
                name,
                name.lower() + "@example.test",
                "personal-history-fixture-password",
                "ADMIN" if name == "Odin" else "USER",
            )
            response = client.post(
                "/api/auth/login",
                json={"identity": name, "password": "personal-history-fixture-password"},
                headers=ORIGIN,
            )
            assert response.status_code == 200
            sessions[name] = (response.cookies[COOKIE], response.json()["csrf_token"])
    with app.state.store.connection() as c:
        c.execute("UPDATE accounts SET email_verified_at=1")
    return root / "hnefatafl.sqlite3", accounts, sessions


@pytest.fixture
def world(tmp_path, seed):
    source, accounts, sessions = seed
    shutil.copy2(source, tmp_path / source.name)
    clock = [datetime(2035, 1, 1, tzinfo=timezone.utc)]
    app = create_app(
        tmp_path,
        online_settings=OnlineSettings(
            enabled=True, timeout_policy_approved=True, creation_limit=100, waiting_limit=100
        ),
        online_clock=lambda: clock[0],
        online_background=False,
        account_background=False,
    )
    with ExitStack() as stack:
        clients = {}
        for name, (token, csrf) in sessions.items():
            client = stack.enter_context(TestClient(app))
            client.cookies.set(COOKIE, token)
            client.headers.update({**ORIGIN, "X-CSRF-Token": csrf})
            clients[name] = client
        yield (
            app,
            clients,
            {name: digest(value[0]) for name, value in sessions.items()},
            accounts,
            clock,
        )


def online(world, *, mode="single"):
    app, _, credentials, _, _ = world
    created = app.state.online.create(
        credentials["Archer"], "brandubh", "attackers", 60, str(uuid4()), mode
    )["game"]
    return app.state.online.mutate(credentials["Keeper"], created["id"], 0, "join")["game"]


def draw_account(client):
    game = historical_response(client, json={"ruleset": "brandubh", "mode": "human-human"}).json()
    for source, target in [(3, 2), (17, 16), (2, 3), (16, 17)] * 2:
        response = client.post(
            f"/api/games/{game['id']}/moves",
            json={"version": game["version"], "source": source, "target": target},
        )
        assert response.status_code == 200, response.text
        game = response.json()
    return game


def test_ownership_details_and_administrator_personal_scope(world):
    _, clients, _, _, _ = world
    local = historical_response(
        clients["Archer"], json={"mode": "human-human", "ruleset": "brandubh"}
    ).json()
    active = online(world)
    for name in ("Observer", "Odin"):
        assert clients[name].get("/api/personal/single-player").json()["games"] == []
        assert clients[name].get("/api/personal/online").json()["games"] == []
        for path in (
            f"single-player/{local['id']}/replay",
            f"online/{active['id']}",
            f"online/{active['id']}/replay",
        ):
            assert clients[name].get("/api/personal/" + path).status_code == 404
        listing = clients[name].get("/api/online/games")
        assert listing.status_code == 200
        assert [g["id"] for g in listing.json()["games"]] == [active["id"]]
        assert clients[name].get(f"/api/online/games/{active['id']}").status_code == 200
    assert clients["Odin"].get(f"/api/games/{local['id']}").status_code == 200
    for name in ("Archer", "Keeper"):
        assert [g["id"] for g in clients[name].get("/api/personal/online").json()["games"]] == [
            active["id"]
        ]
        assert clients[name].get(f"/api/personal/online/{active['id']}/replay").json()["validated"]


@pytest.mark.parametrize(
    "path",
    [
        "single-player",
        "online",
        "single-player/missing/replay",
        "online/missing",
        "online/missing/replay",
    ],
)
def test_current_authentication(world, path):
    app, clients, _, accounts, _ = world
    with TestClient(app) as anonymous:
        assert anonymous.get("/api/personal/" + path).status_code == 401
    app.state.auth.update_account(accounts["Archer"]["id"], active=False)
    assert clients["Archer"].get("/api/personal/" + path).status_code == 401


@pytest.mark.parametrize(
    "suffix",
    [
        "?limit=0",
        "?limit=101",
        "?limit=true",
        "?ruleset=other",
        "?cursor=garbage",
        "?cursor=" + "a" * 513,
    ],
)
def test_pagination_and_filter_bounds(world, suffix):
    _, clients, _, _, _ = world
    for category in ("single-player", "online"):
        assert clients["Archer"].get("/api/personal/" + category + suffix).status_code == 422


def test_account_keyset_ties_combined_filters_and_insert_between_pages(world):
    app, clients, _, _, _ = world
    client = clients["Archer"]
    ids = [
        historical_response(client, json={"ruleset": "brandubh", "mode": "human-human"}).json()[
            "id"
        ]
        for _ in range(5)
    ]
    with app.state.store.connection() as c:
        c.execute(
            "UPDATE games SET created_at=?,data=json_set(data,'$.created_at',?)",
            ("2026-01-01T00:00:00Z",) * 2,
        )
    filters = {"limit": 2, "ruleset": "brandubh", "status": "in_progress"}
    first = client.get("/api/personal/single-player", params=filters).json()
    assert [g["id"] for g in first["games"]] == sorted(ids)[:2]
    historical_response(client, json={"ruleset": "tablut", "mode": "human-human"})
    cursor, seen = first["next_cursor"], [g["id"] for g in first["games"]]
    while cursor:
        response = client.get("/api/personal/single-player", params={**filters, "cursor": cursor})
        assert response.status_code == 200, response.text
        body = response.json()
        seen.extend(g["id"] for g in body["games"])
        cursor = body["next_cursor"]
    assert seen == sorted(ids)
    assert (
        client.get(
            "/api/personal/single-player", params={"cursor": first["next_cursor"]}
        ).status_code
        == 422
    )
    assert (
        clients["Keeper"]
        .get("/api/personal/single-player", params={**filters, "cursor": first["next_cursor"]})
        .status_code
        == 422
    )


def test_retained_results_and_review_are_cursor_independent_without_writes(world):
    app, clients, _, _, _ = world
    client = clients["Archer"]
    game = draw_account(client)
    assert (
        client.post(
            f"/api/games/{game['id']}/seek", json={"version": game["version"], "cursor": 0}
        ).status_code
        == 200
    )
    with app.state.store.connection() as c:
        before = tuple(
            c.execute("SELECT data,version FROM games WHERE id=?", (game["id"],)).fetchone()
        )
        rune_count = c.execute("SELECT count(*) FROM entity_runes").fetchone()[0]
    body = client.get(
        "/api/personal/single-player?status=completed&winner=draw&ruleset=brandubh"
    ).json()
    assert body["matching"] == 1 and body["games"][0]["winner"] == "draw"
    existing = client.get("/api/stats").json()
    assert all(body["totals"][k] == existing[k] for k in body["totals"])
    review = client.get(f"/api/personal/single-player/{game['id']}/replay").json()
    assert review["cursor"] == 0 and review["states"][-1]["winner"] == "draw"
    assert review["game"]["status"] == "completed" and len(review["moves"]) == 8
    assert client.get(f"/api/personal/single-player/{game['id']}/replay").json() == review
    with app.state.store.connection() as c:
        assert (
            tuple(c.execute("SELECT data,version FROM games WHERE id=?", (game["id"],)).fetchone())
            == before
        )
        assert c.execute("SELECT count(*) FROM entity_runes").fetchone()[0] == rune_count


def test_effective_online_results_abandonment_and_void_reversal_survive_restart(world):
    app, clients, credentials, _, clock = world
    core = app.state.online
    loss = online(world)
    core.mutate(credentials["Archer"], loss["id"], loss["version"], "concede")
    win = online(world)
    moved = core.mutate(
        credentials["Archer"], win["id"], win["version"], "move", source=3, target=1
    )["game"]
    core.mutate(credentials["Keeper"], win["id"], moved["version"], "concede")
    drawn = online(world)
    for i, (source, target) in enumerate([(3, 2), (17, 16), (2, 3), (16, 17)] * 2):
        core.mutate(
            credentials["Archer" if i % 2 == 0 else "Keeper"],
            drawn["id"],
            drawn["version"] + i,
            "move",
            source=source,
            target=target,
        )
    expired = online(world)
    core.set_gate(credentials["Odin"], 0, True, "fixture outage policy")
    clock[0] += timedelta(seconds=61)
    core.sweep()
    for name, expected in (("Archer", (1, 2, 1, 1)), ("Keeper", (2, 1, 1, 0))):
        totals = clients[name].get("/api/personal/online?section=history").json()["totals"]
        assert tuple(totals[k] for k in ("wins", "losses", "draws", "abandoned_games")) == expected
        assert totals == core.statistics(credentials[name])
        for outcome in ("win", "loss", "draw", "abandoned"):
            filtered = (
                clients[name]
                .get(
                    "/api/personal/online",
                    params={
                        "section": "history",
                        "status": "completed",
                        "ruleset": "brandubh",
                        "outcome": outcome,
                    },
                )
                .json()
            )
            assert (
                filtered["matching"]
                == totals[
                    {
                        "win": "wins",
                        "loss": "losses",
                        "draw": "draws",
                        "abandoned": "abandoned_games",
                    }[outcome]
                ]
            )
    current = core.read(credentials["Odin"], expired["id"])
    core.mutate(
        credentials["Odin"],
        expired["id"],
        current["version"],
        "void",
        note="private-correction-note-must-not-leak",
    )
    for _ in range(3):
        body = clients["Archer"].get("/api/personal/online?section=history").json()
        assert body["totals"]["played"] == 3 and body["totals"]["abandoned_games"] == 0
        voided = next(g for g in body["games"] if g["id"] == expired["id"])
        assert (
            voided["status"] == "voided"
            and voided["outcome"] is None
            and not voided["result"]["eligible"]
        )
        assert "private-correction" not in json.dumps(body)
        for suffix in ("", "/replay"):
            private = clients["Archer"].get(f"/api/personal/online/{expired['id']}{suffix}")
            assert private.status_code == 200
            assert "private-correction" not in private.text
            assert "corrected_by" not in private.text and "correction_note" not in private.text
    restarted = create_app(
        app.state.store.path.parent, online_background=False, account_background=False
    )
    with TestClient(restarted) as client:
        client.cookies.set(COOKIE, clients["Archer"].cookies.get(COOKIE))
        assert client.get("/api/personal/online?section=history").json()["totals"] == body["totals"]


@pytest.mark.parametrize("mode", ["simultaneous", "sequential"])
def test_paired_game_grouping_and_admitted_waiting_return(world, mode):
    app, clients, credentials, _, _ = world
    first = online(world, mode=mode)
    if mode == "sequential":
        app.state.online.mutate(credentials["Archer"], first["id"], first["version"], "concede")
    for name in ("Archer", "Keeper"):
        body = clients[name].get("/api/personal/online").json()
        if mode == "sequential":
            body["games"] += (
                clients[name].get("/api/personal/online?section=history").json()["games"]
            )
        assert len(body["games"]) == 2
        assert {g["match"]["id"] for g in body["games"]} == {first["match"]["id"]}
        assert all(len(g["match"]["games"]) == 2 for g in body["games"])
        assert "score" not in json.dumps(body) and body["totals"]["played"] == (
            1 if mode == "sequential" else 0
        )
        for game in body["games"]:
            assert clients[name].get(f"/api/personal/online/{game['id']}").status_code == 200
    if mode == "simultaneous":
        assert (
            clients["Archer"].get("/api/personal/online?section=your_turn").json()["matching"] == 1
        )


def test_overdue_is_not_your_turn_and_cancelled_is_not_a_loss(world):
    app, clients, credentials, _, clock = world
    active = online(world)
    waiting = app.state.online.create(
        credentials["Archer"], "brandubh", "random", 60, str(uuid4())
    )["game"]
    app.state.online.mutate(credentials["Archer"], waiting["id"], 0, "cancel")
    assert clients["Archer"].get("/api/personal/online?section=your_turn").json()["matching"] == 1
    clock[0] += timedelta(seconds=61)
    assert clients["Archer"].get("/api/personal/online?section=your_turn").json()["matching"] == 0
    other = clients["Archer"].get("/api/personal/online?section=other_active").json()
    assert other["games"][0]["id"] == active["id"] and other["games"][0]["overdue"]
    history = clients["Archer"].get("/api/personal/online?section=history&status=cancelled").json()
    assert history["matching"] == 1 and history["games"][0]["outcome"] is None
    assert history["totals"]["played"] == 0


def test_batched_safe_projections_readonly_replay_and_online_analysis_denial(world, monkeypatch):
    app, clients, credentials, _, _ = world
    for _ in range(4):
        online(world, mode="simultaneous")
    statements = []
    original = app.state.store.connection

    @contextmanager
    def traced():
        with original() as c:
            c.set_trace_callback(statements.append)
            yield c

    monkeypatch.setattr(app.state.store, "connection", traced)
    bodies, counts = [], []
    for limit in (1, 8):
        statements.clear()
        response = clients["Archer"].get("/api/personal/online", params={"limit": limit})
        assert response.status_code == 200, response.text
        bodies.append(response.json())
        counts.append(sum(s.lstrip().upper().startswith(("SELECT", "WITH")) for s in statements))
    assert counts[0] == counts[1] and counts[0] <= 8
    for forbidden in (
        "password",
        "email",
        "csrf",
        "correction_note",
        "corrected_by",
        "board",
        "position",
        "security",
        "evaluations",
        "hint",
    ):
        assert forbidden not in json.dumps(bodies[-1])
    game = bodies[-1]["games"][0]
    before = app.state.online.read(credentials["Archer"], game["id"])
    clients["Archer"].get(f"/api/personal/online/{game['id']}/replay")
    assert app.state.online.read(credentials["Archer"], game["id"]) == before
    for name in ("Archer", "Odin"):
        assert (
            clients[name].get(f"/api/personal/single-player/{game['id']}/replay").status_code == 404
        )
        assert clients[name].get(f"/api/games/{game['id']}/analytics").status_code == 409
        assert (
            clients[name]
            .post(
                f"/api/games/{game['id']}/analysis", json={"version": game["version"], "cursor": 0}
            )
            .status_code
            == 409
        )


def test_online_keyset_ties_combined_filters_and_concurrent_completion(world):
    app, clients, credentials, _, clock = world
    ids = [online(world)["id"] for _ in range(5)]
    extra = app.state.online.create(credentials["Archer"], "tablut", "defenders", 60, str(uuid4()))[
        "game"
    ]
    app.state.online.mutate(credentials["Keeper"], extra["id"], 0, "join")
    filters = {"section": "your_turn", "status": "active", "ruleset": "brandubh", "limit": 2}
    first = clients["Archer"].get("/api/personal/online", params=filters).json()
    assert first["matching"] == 5
    assert [g["id"] for g in first["games"]] == sorted(ids)[:2]
    # A game can leave this category after the first page. The cursor remains a
    # position, not a snapshot or permission to include a completed game.
    removed = sorted(ids)[2]
    app.state.online.mutate(credentials["Archer"], removed, 1, "concede")
    clock[0] += timedelta(seconds=1)
    newcomer = online(world)["id"]
    seen = [g["id"] for g in first["games"]]
    cursor = first["next_cursor"]
    while cursor:
        body = (
            clients["Archer"]
            .get("/api/personal/online", params={**filters, "cursor": cursor})
            .json()
        )
        seen.extend(g["id"] for g in body["games"])
        cursor = body["next_cursor"]
    assert seen == [identifier for identifier in sorted(ids) if identifier != removed]
    assert newcomer not in seen
    fresh = clients["Archer"].get("/api/personal/online", params=filters).json()
    assert fresh["games"][0]["id"] == newcomer
    for actor, params in (
        ("Keeper", filters),
        ("Archer", {**filters, "ruleset": "tablut"}),
        ("Archer", {**filters, "section": "history"}),
    ):
        assert (
            clients[actor]
            .get("/api/personal/online", params={**params, "cursor": first["next_cursor"]})
            .status_code
            == 422
        )


def test_explicit_unfinished_account_result_and_safe_replay_projection(world):
    app, clients, _, _, _ = world
    game = historical_response(
        clients["Archer"], json={"ruleset": "brandubh", "mode": "human-human"}
    ).json()
    assert app.state.store.get_game(game["id"])["recorded_result"] is None
    body = clients["Archer"].get("/api/personal/single-player").json()
    assert body["games"][0]["winner"] is None and body["totals"]["completed_games"] == 0
    assert body["totals"]["total_games"] == 1
    existing = clients["Archer"].get("/api/stats").json()
    assert all(body["totals"][key] == existing[key] for key in body["totals"])
    assert clients["Archer"].get("/api/personal/online").json()["totals"]["played"] == 0
    assert set(body["games"][0]) == {
        "id",
        "category",
        "ruleset",
        "mode",
        "created_at",
        "ended_at",
        "status",
        "winner",
        "plies",
        "players",
    }
    replay = clients["Archer"].get(f"/api/personal/single-player/{game['id']}/replay").json()
    for forbidden in ("config", "email", "password", "evaluations", "analysis", "rune"):
        assert forbidden not in json.dumps(replay)


@pytest.mark.parametrize("cursor", [0, 8])
def test_missing_account_result_is_unfinished_without_result_reconstruction(world, cursor):
    app, clients, _, _, _ = world
    client = clients["Archer"]
    game = draw_account(client)
    response = client.post(
        f"/api/games/{game['id']}/seek", json={"version": game["version"], "cursor": cursor}
    )
    assert response.status_code == 200
    # An absent result is unsupported data, not permission to reconstruct one.
    stored = app.state.store.get_game(game["id"])
    del stored["recorded_result"]
    with app.state.store.connection() as c:
        c.execute("UPDATE games SET data=? WHERE id=?", (json.dumps(stored), game["id"]))
    body = client.get("/api/personal/single-player").json()
    assert body["games"][0]["winner"] is None
    assert body["games"][0]["status"] == "in_progress"
    assert body["totals"]["completed_games"] == body["totals"]["draws"] == 0
    assert body["totals"]["in_progress"] == 1
    existing = client.get("/api/stats").json()
    assert all(body["totals"][key] == existing[key] for key in body["totals"])
    assert client.get("/api/personal/single-player?status=completed").json()["matching"] == 0
    replay = client.get(f"/api/personal/single-player/{game['id']}/replay").json()
    assert replay["validated"] and replay["states"][-1]["winner"] == "draw"
    assert replay["game"]["winner"] is None and replay["game"]["status"] == "in_progress"


def test_current_account_writes_store_result_metadata(world):
    app, clients, _, _, _ = world
    client = clients["Archer"]
    game = client.post("/api/games", json={"ruleset": "brandubh", "human_side": "attackers"}).json()

    def assert_stored(current, winner=None):
        stored = app.state.store.get_game(current["id"])
        result = stored["recorded_result"]
        assert (result["winner"] if result else None) == winner
        return current

    assert_stored(game)
    for operation, payload in (
        ("moves", {"source": 3, "target": 2}),
        ("undo", {}),
        ("redo", {}),
        ("seek", {"cursor": 0}),
        ("moves", {"source": 3, "target": 1}),
    ):
        response = client.post(
            f"/api/games/{game['id']}/{operation}", json={"version": game["version"], **payload}
        )
        assert response.status_code == 200, response.text
        game = assert_stored(response.json())
    restarted = client.post(f"/api/games/{game['id']}/restart", json={"version": game["version"]})
    assert restarted.status_code == 200
    assert_stored(restarted.json())
    completed = draw_account(client)
    assert_stored(completed, "draw")
    replayed = client.post(
        f"/api/games/{completed['id']}/seek", json={"version": completed["version"], "cursor": 0}
    )
    assert replayed.status_code == 200
    assert_stored(replayed.json(), "draw")
    branch = client.post(
        f"/api/games/{completed['id']}/moves",
        json={"version": replayed.json()["version"], "source": 3, "target": 1},
    )
    assert branch.status_code == 200
    assert_stored(branch.json())
    body = client.get("/api/personal/single-player").json()
    existing = client.get("/api/stats").json()
    assert all(body["totals"][key] == existing[key] for key in body["totals"])


@pytest.mark.parametrize(
    "path,target,method,action,error_type",
    [
        ("single-player", "personal", "single_player", "account_games", sqlite3.OperationalError),
        (
            "single-player/private-id/replay",
            "personal",
            "single_replay",
            "account_review",
            ValueError,
        ),
        ("online", "personal", "online_games", "online_games", KeyError),
        ("online/private-id", "online", "read", "online_detail", IndexError),
        ("online/private-id/replay", "online", "replay", "online_review", TypeError),
    ],
)
def test_internal_errors_log_only_safe_action_type_and_traceback(
    world, monkeypatch, caplog, path, target, method, action, error_type
):
    app, clients, credentials, _, _ = world
    private = "private-request-and-database-details"
    failure = error_type(private + credentials["Archer"])
    cause = ValueError(private)

    def broken(*args, **kwargs):
        raise failure from cause

    monkeypatch.setattr(getattr(app.state, target), method, broken)
    with caplog.at_level(logging.ERROR, logger="hnefatafl.personal_api"):
        response = clients["Archer"].get("/api/personal/" + path, params={"cursor": private})
    assert response.status_code == 503
    assert response.json() == {"detail": "Personal history unavailable. Retry shortly."}
    records = [record for record in caplog.records if record.name == "hnefatafl.personal_api"]
    assert len(records) == 1
    record = records[0]
    assert record.args == (action, error_type.__name__)
    assert record.exc_info and record.exc_info[2] is not None
    assert "Traceback (most recent call last)" in caplog.text
    for secret in (private, credentials["Archer"], "private-id"):
        assert secret not in caplog.text and secret not in response.text
    assert error_type.__name__ not in response.text


def test_hall_role_scope_and_independent_typed_review(world):
    _, clients, _, _, _ = world
    game = draw_account(clients["Archer"])
    live = online(world)
    for name in ("Archer", "Keeper"):
        rows = clients[name].get("/api/history/games").json()
        # A normal user sees their participating online games and their own
        # account single-player records, but never another account's.
        expected = {live["id"], game["id"]} if name == "Archer" else {live["id"]}
        assert {r["id"] for r in rows["games"]} == expected
        assert rows["matching"] == len(expected)
        account_rows = clients[name].get("/api/history/games?category=account-single-player")
        assert account_rows.status_code == 200
        assert account_rows.json()["matching"] == (1 if name == "Archer" else 0)
        assert clients[name].get("/api/history/games?player=Archer").status_code == 403
        owner_review = clients[name].get(f"/api/history/account-single-player/{game['id']}/replay")
        assert owner_review.status_code == (200 if name == "Archer" else 404)
        assert clients[name].get(f"/api/history/online/{game['id']}/replay").status_code == 404
        assert (
            clients[name].get(f"/api/history/account-single-player/{live['id']}/replay").status_code
            == 404
        )
    assert clients["Archer"].get(f"/api/games/{game['id']}").status_code == 200
    assert (
        clients["Archer"].get(f"/api/personal/single-player/{game['id']}/replay").status_code == 200
    )
    observer = clients["Observer"]
    assert (
        observer.get("/api/history/games?owner=Archer&admin=true&all_data=true").json()["games"]
        == []
    )
    assert observer.get(f"/api/history/online/{live['id']}/replay").status_code == 404
    # Existing global lobby/spectator access is intentionally unchanged.
    assert observer.get("/api/online/games").status_code == 200
    assert observer.get(f"/api/online/games/{live['id']}").status_code == 200
    odin = clients["Odin"]
    all_games = odin.get("/api/history/games").json()
    assert {r["id"] for r in all_games["games"]} == {game["id"], live["id"]}
    assert {r["category"] for r in all_games["totals"]} == {"online", "account-single-player"}
    for category, identifier in [("online", live["id"]), ("account-single-player", game["id"])]:
        # Independent lookup does not depend on list filters/page membership.
        filtered = odin.get(f"/api/history/games?category={category}&ruleset=tablut").json()
        assert filtered["games"] == [] and filtered["matching"] == 0 and filtered["totals"] == []
        response = odin.get(f"/api/history/{category}/{identifier}/replay")
        assert response.status_code == 200, response.text
        assert response.json()["validated"] is True
    assert (
        odin.get("/api/history/games?category=account-single-player&player=Archer").json()[
            "matching"
        ]
        == 1
    )
    assert odin.get("/api/history/games?player=%25").json()["matching"] == 0
    assert odin.get("/api/history/games?dashboard=true").json()["games"] == []
    assert odin.get(f"/api/personal/single-player/{game['id']}/replay").status_code == 404


def test_dashboard_latest_three_filters_before_limit_and_stable_pages(world):
    app, clients, credentials, _, clock = world
    own_account = draw_account(clients["Odin"])
    others = draw_account(clients["Archer"])
    completed = []
    for _ in range(5):
        active = online(world)
        done = app.state.online.mutate(
            credentials["Archer"], active["id"], active["version"], "concede"
        )["game"]
        completed.append(done["id"])
    live = online(world)
    cancelled = app.state.online.create(
        credentials["Archer"], "brandubh", "random", 60, str(uuid4())
    )["game"]
    app.state.online.mutate(credentials["Archer"], cancelled["id"], 0, "cancel")
    # Withdrawal must disappear before the limit, not produce a short client-filtered page.
    app.state.online.mutate(credentials["Odin"], completed[-1], 2, "void", note="private test note")
    expected = sorted(completed[:-1])
    dashboard = clients["Archer"].get("/api/history/games?dashboard=true&limit=100").json()
    assert [r["id"] for r in dashboard["games"]] == expected[:3]
    # Archer's own account game joins the four eligible online completions.
    assert dashboard["limit"] == 3 and dashboard["matching"] == 5
    assert all(r["category"] == "online" and r["eligible"] for r in dashboard["games"])
    # Own-only remains true for administrators, with their own account category eligible.
    assert [
        r["id"] for r in clients["Odin"].get("/api/history/games?dashboard=true").json()["games"]
    ] == [own_account["id"]]
    first = clients["Archer"].get("/api/history/games?status=completed&limit=2").json()
    second = (
        clients["Archer"]
        .get(
            "/api/history/games",
            params={"status": "completed", "limit": 2, "cursor": first["next_cursor"]},
        )
        .json()
    )
    assert [r["id"] for r in first["games"] + second["games"]] == expected
    assert first["matching"] == second["matching"] == 5
    assert sum(total["completed"] for total in first["totals"]) == 5
    assert (
        clients["Keeper"]
        .get("/api/history/games", params={"status": "completed", "cursor": first["next_cursor"]})
        .status_code
        == 422
    )
    assert (
        clients["Archer"]
        .get("/api/history/games", params={"ruleset": "tablut", "cursor": first["next_cursor"]})
        .status_code
        == 422
    )
    for client in clients.values():
        assert client.post(f"/api/games/{live['id']}/analysis", json={}).status_code in (404, 422)
    assert "private test note" not in json.dumps(dashboard)
    # Revocation is reloaded by the history domain, not trusted from a supplied flag.
    with app.state.store.connection() as c:
        c.execute("UPDATE accounts SET role='USER' WHERE username='Odin'")
    # Revocation is reloaded: the former administrator keeps their own record
    # review but loses the all-account scope and name filter.
    assert clients["Odin"].get("/api/history/games?player=Archer").status_code == 403
    assert (
        clients["Odin"]
        .get(f"/api/history/account-single-player/{own_account['id']}/replay")
        .status_code
        == 200
    )
    assert (
        clients["Odin"].get(f"/api/history/account-single-player/{others['id']}/replay").status_code
        == 404
    )


def test_auth_shell_preserves_history_selection_query(world):
    from urllib.parse import parse_qs, urlsplit

    app, _, _, _, _ = world
    path = "/archive?ruleset=tablut&review=online&selected=online-test"
    with TestClient(app) as anonymous:
        response = anonymous.get(path, follow_redirects=False)
    assert response.status_code == 307
    redirect = urlsplit(response.headers["location"])
    assert redirect.path == "/login"
    assert parse_qs(redirect.query)["next"] == [path]


def test_admin_dashboard_own_scope_precedes_mixed_category_limit(world):
    app, clients, credentials, _, _ = world
    account = draw_account(clients["Odin"])
    others = draw_account(clients["Archer"])
    own = []
    for _ in range(4):
        game = app.state.online.create(
            credentials["Odin"], "brandubh", "attackers", 60, str(uuid4())
        )["game"]
        game = app.state.online.mutate(credentials["Observer"], game["id"], 0, "join")["game"]
        game = app.state.online.mutate(credentials["Odin"], game["id"], game["version"], "concede")[
            "game"
        ]
        own.append(game["id"])
    rows = clients["Odin"].get("/api/history/games?dashboard=true").json()
    assert [r["id"] for r in rows["games"]] == sorted(own)[:3]
    assert rows["matching"] == 5
    assert sum(r["games"] for r in rows["totals"]) == 5
    assert {r["id"] for r in rows["games"]}.isdisjoint({account["id"], others["id"]})


def test_latest_account_records_preserve_microsecond_order_before_limit(world):
    app, clients, _, _, _ = world
    games = sorted([draw_account(clients["Odin"]) for _ in range(4)], key=lambda g: g["id"])
    with app.state.store.connection() as c:
        for index, game in enumerate(games, 1):
            at = f"2035-01-01T00:00:00.{index:06d}+00:00"
            c.execute(
                "UPDATE games SET data=json_set(data,'$.ended_at',?) WHERE id=?", (at, game["id"])
            )
    latest = clients["Odin"].get("/api/history/games?dashboard=true").json()
    assert [r["id"] for r in latest["games"]] == [g["id"] for g in games[::-1][:3]]
    assert latest["matching"] == 4


def _finish_account(app, game_id, winner, plies=12):
    """Persist an authoritative result on an existing account record for statistics."""
    stored = app.state.store.get_game(game_id)
    stored["recorded_result"] = {
        "winner": winner,
        "reason": "fixture",
        "plies": plies,
        "ended_at": "2035-01-01T00:00:00+00:00",
    }
    with app.state.store.connection() as c:
        c.execute("UPDATE games SET data=? WHERE id=?", (json.dumps(stored), game_id))
    return game_id


def _account_game(client, ruleset, human_side, mode="human-ai"):
    return client.post(
        "/api/games", json={"ruleset": ruleset, "mode": mode, "human_side": human_side}
    ).json()


def test_single_player_personal_statistics_use_account_seat_and_exclude_ai_ai(world):
    app, clients, _, _, _ = world
    client = clients["Archer"]
    # Attributed human-ai games across rulesets and sides.
    _finish_account(app, _account_game(client, "brandubh", "attackers")["id"], "attackers", 20)
    _finish_account(app, _account_game(client, "brandubh", "attackers")["id"], "defenders", 30)
    _finish_account(app, _account_game(client, "brandubh", "defenders")["id"], "defenders", 40)
    _finish_account(app, _account_game(client, "tablut", "attackers")["id"], "draw", 10)
    _finish_account(app, _account_game(client, "tablut", "defenders")["id"], "attackers", 15)
    _account_game(client, "tablut", "attackers")
    # Legacy human-human record with no account-bound seat must not fabricate a result.
    draw_account(client)
    stats = client.get("/api/personal/single-player").json()["statistics"]
    groups = {g["ruleset"]: g for g in stats["groups"]}
    brandubh = groups["brandubh"]
    assert brandubh["mode"] is None
    assert brandubh["played"] == 3
    assert (brandubh["wins"], brandubh["losses"], brandubh["draws"]) == (2, 1, 0)
    assert brandubh["win_rate"] == 2 / 3
    assert brandubh["win_rate_attackers"] == 0.5
    assert brandubh["win_rate_defenders"] == 1.0
    assert brandubh["average_game_length"] == 30.0
    assert brandubh["average_game_length_unit"] == "moves"
    tablut = groups["tablut"]
    assert tablut["played"] == 3  # two completed plus one unfinished
    assert (tablut["wins"], tablut["losses"], tablut["draws"]) == (0, 1, 1)
    assert tablut["win_rate"] == 0.0
    assert tablut["win_rate_attackers"] == 0.0
    assert tablut["win_rate_defenders"] == 0.0
    assert tablut["average_game_length"] == 12.5
    assert stats["coverage"]["unattributed_games"] == 1
    # Empty dataset (another account) yields no fabricated groups.
    assert clients["Observer"].get("/api/personal/single-player").json()["statistics"] == {
        "groups": [],
        "coverage": {"unattributed_games": 0},
    }
    # Administrator personal statistics stay own-account even with all_data.
    odin = clients["Odin"]
    ai_game = _account_game(odin, "brandubh", "attackers", mode="ai-ai")
    _finish_account(app, ai_game["id"], "attackers", 25)
    # AI-vs-AI games are excluded from personal history and statistics; they
    # remain stored for administrator AI-strength analysis by direct ID.
    odin_history = odin.get("/api/personal/single-player").json()
    assert odin_history["statistics"] == {
        "groups": [],
        "coverage": {"unattributed_games": 0},
    }
    assert ai_game["id"] not in {game["id"] for game in odin_history["games"]}
    assert odin_history["totals"]["total_games"] == 0
    assert "archer" not in json.dumps(odin_history)


def test_online_personal_statistics_sides_timeouts_and_effective_results(world):
    app, clients, credentials, _, clock = world
    core = app.state.online
    # Archer as attackers wins by opponent concession.
    win = online(world)
    moved = core.mutate(
        credentials["Archer"], win["id"], win["version"], "move", source=3, target=1
    )["game"]
    core.mutate(credentials["Keeper"], win["id"], moved["version"], "concede")
    # Archer as attackers loses by conceding on their own turn.
    loss = online(world)
    core.mutate(credentials["Archer"], loss["id"], loss["version"], "concede")
    # Rule draw.
    drawn = online(world)
    for i, (source, target) in enumerate([(3, 2), (17, 16), (2, 3), (16, 17)] * 2):
        core.mutate(
            credentials["Archer" if i % 2 == 0 else "Keeper"],
            drawn["id"],
            drawn["version"] + i,
            "move",
            source=source,
            target=target,
        )
    # Archer's own timeout is a loss, not an opponent timeout.
    expired = online(world)
    core.set_gate(credentials["Odin"], 0, True, "fixture outage policy")
    clock[0] += timedelta(seconds=61)
    core.sweep()
    stats = clients["Archer"].get("/api/personal/online?section=history").json()["statistics"]
    groups = {g["ruleset"]: g for g in stats["groups"]}
    single = groups["brandubh"]
    assert single["played"] == 4
    assert (single["wins"], single["losses"], single["draws"]) == (1, 2, 1)
    assert single["timed_out"] == 1
    assert single["win_rate"] == 0.25
    assert single["win_rate_attackers"] == 0.25
    assert single["win_rate_defenders"] is None
    assert single["average_game_length_unit"] == "moves"
    # Keeper sees the same games from the opposite seat with no timeouts.
    keeper = {
        g["ruleset"]: g
        for g in clients["Keeper"]
        .get("/api/personal/online?section=history")
        .json()["statistics"]["groups"]
    }
    assert keeper["brandubh"]["timed_out"] == 0
    # Void withdrawal removes the contribution from both totals and statistics.
    current = core.read(credentials["Odin"], expired["id"])
    core.mutate(credentials["Odin"], expired["id"], current["version"], "void", note="fixture void")
    after = clients["Archer"].get("/api/personal/online?section=history").json()["statistics"]
    group = {g["ruleset"]: g for g in after["groups"]}["brandubh"]
    assert group["played"] == 3 and group["losses"] == 1 and group["timed_out"] == 0
    # Empty online dataset.
    assert clients["Observer"].get("/api/personal/online").json()["statistics"] == {
        "groups": [],
        "coverage": {"unattributed_games": 0},
    }


def test_own_timeout_loss_differs_from_opponent_timeout_win(world):
    app, clients, credentials, _, clock = world
    core = app.state.online
    core.set_gate(credentials["Odin"], 0, True, "fixture outage policy")
    # Opponent (Keeper, defenders) times out after Archer's first move.
    opponent_timeout = online(world)
    core.mutate(
        credentials["Archer"],
        opponent_timeout["id"],
        opponent_timeout["version"],
        "move",
        source=3,
        target=1,
    )
    clock[0] += timedelta(seconds=61)
    core.sweep()
    # Own (Archer, attackers) timeout on the next game's opening turn.
    own_timeout = online(world)
    clock[0] += timedelta(seconds=61)
    core.sweep()
    assert own_timeout["id"] != opponent_timeout["id"]
    group = {
        g["ruleset"]: g
        for g in clients["Archer"]
        .get("/api/personal/online?section=history")
        .json()["statistics"]["groups"]
    }["brandubh"]
    assert group["played"] == 2
    assert (group["wins"], group["losses"]) == (1, 1)
    assert group["timed_out"] == 1


def test_statistics_ai_opponents_and_sides_are_complete_despite_history_paging(world):
    app, clients, _, _, _ = world
    client = clients["Archer"]
    _finish_account(app, _account_game(client, "brandubh", "attackers")["id"], "attackers", 20)
    _finish_account(app, _account_game(client, "brandubh", "defenders")["id"], "attackers", 40)
    _account_game(client, "brandubh", "defenders")
    seer = client.post(
        "/api/games",
        json={
            "ruleset": "brandubh",
            "mode": "human-ai",
            "human_side": "attackers",
            "players": {"defenders": {"profile_id": "mcts"}},
        },
    ).json()
    _finish_account(app, seer["id"], "draw", 10)
    response = client.get("/api/personal/single-player?limit=1&status=in_progress").json()
    assert len(response["games"]) == 1
    group = response["statistics"]["groups"][0]
    assert (
        group["played"],
        group["completed"],
        group["wins"],
        group["losses"],
        group["draws"],
    ) == (4, 3, 1, 1, 1)
    assert group["win_rate"] == 1 / 3
    assert group["average_game_length"] == 70 / 3
    attackers, defenders = group["by_side"]["attackers"], group["by_side"]["defenders"]
    assert (
        attackers["played"],
        attackers["wins"],
        attackers["draws"],
        attackers["average_game_length"],
    ) == (2, 1, 1, 15)
    assert (
        defenders["played"],
        defenders["completed"],
        defenders["losses"],
        defenders["average_game_length"],
    ) == (2, 1, 1, 40)
    agents = {agent["profile_id"]: agent for agent in group["agents"]}
    assert set(agents) == {"baseline", "mcts"}
    baseline = agents["baseline"]
    assert baseline["played"] == 3 and baseline["completed"] == 2
    assert baseline["average_game_length"] == 30
    assert baseline["by_side"]["attackers"]["win_rate"] == 1
    assert baseline["by_side"]["defenders"]["win_rate"] == 0
    assert agents["mcts"]["by_side"]["defenders"]["played"] == 0
    assert agents["mcts"]["by_side"]["defenders"]["win_rate"] is None
    assert (
        clients["Observer"].get("/api/personal/single-player").json()["statistics"]["groups"] == []
    )


def test_online_statistics_side_lengths_rating_and_void_withdrawal(world):
    app, clients, credentials, _, _ = world
    core = app.state.online
    attackers = online(world)
    core.mutate(credentials["Archer"], attackers["id"], attackers["version"], "concede")
    other = core.create(credentials["Keeper"], "brandubh", "attackers", 60, str(uuid4()))["game"]
    joined = core.mutate(credentials["Archer"], other["id"], 0, "join")["game"]
    moved = core.mutate(
        credentials["Keeper"], other["id"], joined["version"], "move", source=3, target=1
    )["game"]
    ended = core.mutate(credentials["Archer"], other["id"], moved["version"], "concede")["game"]
    group = clients["Archer"].get("/api/personal/online?limit=1").json()["statistics"]["groups"][0]
    assert group["played"] == group["completed"] == group["losses"] == 2
    assert group["average_game_length"] == 0.5
    assert group["by_side"]["attackers"]["average_game_length"] == 0
    assert group["by_side"]["defenders"]["average_game_length"] == 1
    with app.state.store.connection() as c:
        rating = c.execute(
            "SELECT rating FROM player_ratings WHERE account_id=? AND pool='brandubh'",
            (clients["Archer"].get("/api/auth/me").json()["account"]["id"],),
        ).fetchone()[0]
    assert group["rating"] == rating
    core.mutate(
        credentials["Odin"],
        ended["id"],
        ended["version"],
        "void",
        note="statistics fixture withdrawal",
    )
    after = clients["Archer"].get("/api/personal/online").json()["statistics"]["groups"][0]
    assert after["played"] == 1 and after["average_game_length"] == 0
    assert after["by_side"]["defenders"]["played"] == 0
    assert after["by_side"]["defenders"]["average_game_length"] is None
