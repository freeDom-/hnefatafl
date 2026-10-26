"""Personal reads on real sessions/SQLite with deterministic authoritative time."""

from contextlib import ExitStack, contextmanager
from datetime import datetime, timedelta, timezone
import json
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
    game = client.post("/api/games", json={"ruleset": "brandubh", "mode": "human-human"}).json()
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
    local = (
        clients["Archer"]
        .post("/api/games", json={"mode": "human-human", "ruleset": "brandubh"})
        .json()
    )
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
        client.post("/api/games", json={"ruleset": "brandubh", "mode": "human-human"}).json()["id"]
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
    client.post("/api/games", json={"ruleset": "tablut", "mode": "human-human"})
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


def test_retained_legacy_results_and_review_are_cursor_independent_without_writes(world):
    app, clients, _, _, _ = world
    client = clients["Archer"]
    game = draw_account(client)
    assert (
        client.post(
            f"/api/games/{game['id']}/seek", json={"version": game["version"], "cursor": 0}
        ).status_code
        == 200
    )
    for legacy in (False, True):
        if legacy:
            with app.state.store.connection() as c:
                c.execute(
                    "UPDATE games SET data=json_remove(data,'$.recorded_result') WHERE id=?",
                    (game["id"],),
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
                tuple(
                    c.execute("SELECT data,version FROM games WHERE id=?", (game["id"],)).fetchone()
                )
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
    game = draw_account(clients["Archer"])
    with app.state.store.connection() as c:
        c.execute(
            "UPDATE games SET data=json_set(data,'$.recorded_result',NULL) WHERE id=?",
            (game["id"],),
        )
    body = clients["Archer"].get("/api/personal/single-player").json()
    assert body["games"][0]["winner"] is None and body["totals"]["completed_games"] == 0
    assert body["totals"]["total_games"] == 1
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
