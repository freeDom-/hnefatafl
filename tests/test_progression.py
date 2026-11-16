"""Automatic permanent rewards from real game, rating and analysis events.

Reads and portrait selection never award. Corrections preserve rewards, while
current authoritative evidence controls future milestones.
"""

import fcntl
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from hnefatafl.auth import COOKIE
from hnefatafl.game_records import game_record
from hnefatafl.progression import (
    ACHIEVEMENTS,
    Progression,
)
from hnefatafl.server import create_app
from hnefatafl.storage import Store
from test_ratings import (
    account_template as account_template,
    active,
    fabricate_correction,
    finish,
    make_historical_casual,
    rig as rig,
)

ORIGIN = {"Origin": "http://testserver"}
PASSWORD = "rating-fixture-password"
PROGRESSION_TABLES = (
    "account_portrait_selection",
    "account_portraits",
    "achievement_awards",
    "portrait_catalog",
    "achievement_catalog",
)
# The deterministic four-move repetition that ends a Brandubh game in a draw.
DRAW_SEQUENCE = [(3, 2), (17, 16), (2, 3), (16, 17)] * 2


def items_of(payload):
    return {item["id"]: item for item in payload["items"]}


def progression(rig):
    return Progression(rig.core)


def progression_rows(store):
    """Every stored award, entitlement and selection row, order-stable."""
    with store.connection() as c:
        return {
            table: [tuple(row) for row in c.execute(f"SELECT * FROM {table} ORDER BY 1")]
            for table in (
                "achievement_awards",
                "account_portraits",
                "account_portrait_selection",
            )
        }


def login(client, name="Archer"):
    response = client.post(
        "/api/auth/login", json={"identity": name, "password": PASSWORD}, headers=ORIGIN
    )
    assert response.status_code == 200, response.text
    client.headers.update({**ORIGIN, "X-CSRF-Token": response.json()["csrf_token"]})


@pytest.fixture
def web(rig):
    """A real application on the rig database with an authenticated session."""
    app = create_app(rig.store.path.parent, online_background=False, account_background=False)
    with TestClient(app) as client:
        login(client)
        yield app, client


def account_of(app, client):
    return app.state.auth.account(app.state.auth.session(client.cookies[COOKIE])["id"])


def persist_game(
    store,
    account,
    *,
    category="account-single-player",
    mode="human-ai",
    human_side="defenders",
    winner=None,
    ruleset="brandubh",
):
    """Persist the authoritative record a local/vs-AI milestone is derived from.

    This writes the same shape the game engine writes, so the criteria are
    exercised against stored records rather than a fabricated award.
    """
    players = {}
    for side in ("attackers", "defenders"):
        if mode == "ai-ai" or (mode == "human-ai" and side != human_side):
            players[side] = {
                "name": "Computer",
                "kind": "ai",
                "profile_id": "baseline",
                "config": {},
            }
        else:
            players[side] = {
                "name": account["username"],
                "account_id": account["id"],
                "kind": "human",
                "config": {},
            }
    game = game_record(ruleset, mode, human_side, players, 7)
    if winner is not None:
        game["recorded_result"] = {
            "winner": winner,
            "reason": "king",
            "plies": 12,
            "ended_at": game["created_at"],
        }
    outcome = game["recorded_result"]
    game["recorded_result"] = None
    with store.connection() as c:
        c.execute(
            "INSERT INTO games(id,version,created_at,data,owner_account_id,category) "
            "VALUES(?,?,?,?,?,?)",
            (game["id"], 0, game["created_at"], json.dumps(game), account["id"], category),
        )
    if outcome:
        game["recorded_result"] = outcome
        store.update_game(game, 0)
    return game


def play_local_draw(client):
    game = client.post(
        "/api/games/local",
        json={"ruleset": "brandubh", "attackers_label": "Raven", "defenders_label": "Wolf"},
    ).json()
    for source, target in DRAW_SEQUENCE:
        response = client.post(
            f"/api/games/{game['id']}/moves",
            json={"version": game["version"], "source": source, "target": target},
        )
        assert response.status_code == 200, response.text
        game = response.json()
    return game


def test_catalog_schedule_requirements_and_order(web):
    from hnefatafl.progression import FAMILIES

    app, client = web
    assert len(ACHIEVEMENTS) == 77
    assert sum(a.points for a in ACHIEVEMENTS) == 6290
    assert len({a.id for a in ACHIEVEMENTS}) == 77
    for family, *_ in FAMILIES:
        tiers = [a for a in ACHIEVEMENTS if a.family == family]
        thresholds = (
            (1, 3, 5, 10)
            if family == "rated-win-streak"
            else (1, 3, 10, 25, 50, 100, 250, 500, 1000)
        )
        points = (
            (10, 30, 60, 100)
            if family == "rated-win-streak"
            else (10, 20, 30, 40, 60, 80, 120, 160, 240)
        )
        assert tuple(a.threshold for a in tiers) == thresholds
        assert tuple(a.points for a in tiers) == points
    catalog = client.get("/api/portraits/catalog").json()
    assert catalog["catalog_version"] == 2
    assert [p["id"] for p in catalog["items"]] == [
        "shieldbearer",
        "fjord-scout",
        "forge-keeper",
        "iron-helm",
        "shieldmaiden",
        "skald",
        "valkyrie",
        "wanderer",
        "raven-seer",
        "einherjar",
        "berserker",
        "one-eyed-jarl",
    ]
    assert {p["id"]: p["unlock_achievement_id"] for p in catalog["items"]} == {
        "shieldbearer": None,
        "shieldmaiden": "rated-wins-10",
        "iron-helm": "defender-wins-10",
        "wanderer": "brandubh-wins-10",
        "skald": "tablut-wins-10",
        "fjord-scout": "copenhagen-wins-10",
        "forge-keeper": "ai-wins-10",
        "raven-seer": "rated-wins-25",
        "einherjar": "rated-wins-50",
        "valkyrie": "attacker-wins-10",
        "berserker": "rated-win-streak-5",
        "one-eyed-jarl": "rated-win-streak-10",
    }
    assert "webp" not in json.dumps(catalog)


def test_fresh_read_is_read_only_and_manual_endpoint_removed(web):
    app, client = web
    before = progression_rows(app.state.store)
    data = client.get("/api/personal/achievements").json()
    assert data["catalog_version"] == 3
    assert data["summary"] == {"unlocked": 0, "total": 77, "earned_points": 0, "total_points": 6290}
    assert len(data["families"]) == 10
    assert data["unlocked_portrait_ids"] == []
    assert progression_rows(app.state.store) == before
    assert not any(
        getattr(route, "path", "") == "/api/personal/achievements/confirm" for route in app.routes
    )
    assert client.post("/api/personal/achievements/confirm").status_code == 403
    with app.state.auth.gate_path.open("rb") as gate:
        fcntl.flock(gate, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert client.get("/api/personal/achievements").status_code == 200
        assert (
            client.patch("/api/profile/portrait", json={"portrait_id": "shieldbearer"}).status_code
            == 503
        )


def test_rated_result_automatically_awards_without_a_hall_visit(rig):
    finish(rig, active(rig), "attackers")
    with rig.store.connection() as c:
        awarded = {
            r[0]
            for r in c.execute(
                "SELECT achievement_id FROM achievement_awards WHERE account_id=?",
                (rig.accounts["Archer"]["id"],),
            )
        }
    assert awarded == {
        "first-rated-win",
        "brandubh-initiate",
        "attacker-wins-1",
        "rated-win-streak-1",
    }
    first = progression(rig).achievements(rig.credentials["Archer"])
    assert first["summary"]["earned_points"] == 40
    before = progression_rows(rig.store)
    for _ in range(3):
        assert (
            progression(rig).achievements(rig.credentials["Archer"])["summary"]["earned_points"]
            == 40
        )
    assert progression_rows(rig.store) == before
    assert progression(rig).achievements(rig.credentials["Keeper"])["summary"]["earned_points"] == 0
    assert first["unlocked_portrait_ids"] == []  # first win no longer grants a portrait


@pytest.mark.parametrize("ruleset", ["brandubh", "tablut", "copenhagen"])
@pytest.mark.parametrize("side,account", [("attackers", "Archer"), ("defenders", "Keeper")])
def test_actual_role_and_ruleset_awards(rig, ruleset, side, account):
    finish(rig, active(rig, ruleset), side)
    data = items_of(progression(rig).achievements(rig.credentials[account]))
    assert data["defender-hold"]["progress"] == int(side == "defenders")
    assert data["attacker-wins-1"]["progress"] == int(side == "attackers")
    for pool in ("brandubh", "tablut", "copenhagen"):
        assert data[f"{pool}-initiate"]["unlocked"] == (pool == ruleset)


def test_casual_results_do_not_award(rig):
    finish(rig, make_historical_casual(rig, active(rig)), "attackers")
    assert progression(rig).achievements(rig.credentials["Archer"])["summary"]["earned_points"] == 0


def test_corrections_keep_rewards_and_use_current_progress_for_future_targets(rig):
    games = [finish(rig, active(rig), "attackers") for _ in range(10)]
    service = progression(rig)
    before = service.achievements(rig.credentials["Archer"])
    assert {"shieldmaiden", "wanderer", "valkyrie"} <= set(before["unlocked_portrait_ids"])
    with rig.store.connection() as c:
        assert c.execute("SELECT count(*) FROM account_portrait_selection").fetchone()[0] == 0
    assert (
        next(f for f in before["families"] if f["id"] == "rated-wins")["next_milestone"][
            "threshold"
        ]
        == 25
    )
    fabricate_correction(rig, games[-1], "defenders")
    after = service.achievements(rig.credentials["Archer"])
    family = next(f for f in after["families"] if f["id"] == "rated-wins")
    assert (
        family["progress"] == 9
        and family["awarded_threshold"] == 10
        and family["achieved_threshold"] == 3
    )
    assert family["next_milestone"]["threshold"] == 25
    assert after["summary"]["earned_points"] == before["summary"]["earned_points"]
    rig.core.mutate(
        rig.credentials["Archer"], games[0]["id"], games[0]["version"], "void", note="Correction"
    )
    assert (
        service.achievements(rig.credentials["Archer"])["summary"]["earned_points"]
        == before["summary"]["earned_points"]
    )


def test_duplicate_results_rebuilds_and_concurrent_reads_never_duplicate_rewards(rig):
    from concurrent.futures import ThreadPoolExecutor

    for _ in range(10):
        finish(rig, active(rig), "attackers")
    service = progression(rig)
    before = progression_rows(rig.store)
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(
            pool.map(lambda _: service.achievements(rig.credentials["Archer"]), range(12))
        )
    assert all(r["summary"] == results[0]["summary"] for r in results)
    rig.core.rebuild_ratings(rig.credentials["Archer"])
    assert progression_rows(rig.store) == before


def test_all_76_boundaries_and_permanent_points(rig, monkeypatch):
    from hnefatafl.progression import award_achievements

    current = {}
    monkeypatch.setattr(
        Progression, "_measure", staticmethod(lambda c, actor, a, cache: current.get(a.family, 0))
    )
    service, credential = progression(rig), rig.credentials["Archer"]

    def event():
        with rig.store.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            award_achievements(c, (rig.accounts["Archer"]["id"],), 1)
        return service.achievements(credential)

    score = 0
    for a in ACHIEVEMENTS:
        current[a.family] = a.threshold - 1
        assert not items_of(event())[a.id]["unlocked"]
        current[a.family] += 1
        score += a.points
        at = event()
        assert items_of(at)[a.id]["unlocked"] and at["summary"]["earned_points"] == score
        assert event()["summary"]["earned_points"] == score
    current.clear()
    final = event()
    assert final["summary"]["earned_points"] == 6290
    assert all(f["complete"] and f["next_milestone"] is None for f in final["families"])


def test_historical_progress_is_reconciled_once_and_migration_is_idempotent(rig, monkeypatch):
    import hnefatafl.progression as module

    original = module.award_achievements
    monkeypatch.setattr(module, "award_achievements", lambda *args: None)
    for _ in range(10):
        finish(rig, active(rig), "attackers")
    monkeypatch.setattr(module, "award_achievements", original)
    with rig.store.connection() as c:
        assert c.execute("SELECT count(*) FROM achievement_awards").fetchone()[0] == 0
        c.execute("PRAGMA user_version=20")
    migrated = Store(rig.store.path)
    data = progression(rig).achievements(rig.credentials["Archer"])
    assert items_of(data)["rated-wins-10"]["unlocked"]
    assert {"shieldmaiden", "wanderer", "valkyrie"} <= set(data["unlocked_portrait_ids"])
    before = progression_rows(migrated)
    Store(migrated.path)
    assert progression_rows(migrated) == before


def test_grandfathered_portrait_entitlements_survive_harder_requirements(web):
    app, client = web
    account = account_of(app, client)
    with app.state.store.connection() as c:
        c.execute(
            "INSERT INTO achievement_awards VALUES(?,?,?,?)",
            (account["id"], "first-rated-win", 123, 1),
        )
        c.execute(
            "INSERT INTO account_portraits VALUES(?,?,?,?)",
            (account["id"], "shieldmaiden", 123, "first-rated-win"),
        )
        c.execute(
            "INSERT INTO account_portrait_selection VALUES(?,?,?)",
            (account["id"], "shieldmaiden", 123),
        )
        c.execute("PRAGMA user_version=20")
    Store(app.state.store.path)
    assert client.get("/api/profile").json()["portrait_id"] == "shieldmaiden"
    payload = client.get("/api/personal/achievements").json()
    assert "shieldmaiden" in payload["unlocked_portrait_ids"]
    assert not items_of(payload)["rated-wins-10"]["unlocked"]
    response = client.patch("/api/profile/portrait", json={"portrait_id": "shieldmaiden"})
    assert response.status_code == 200 and response.json()["portrait_id"] == "shieldmaiden"
    assert not items_of(client.get("/api/personal/achievements").json())["rated-wins-10"][
        "unlocked"
    ]


def test_selection_is_explicit_secure_idempotent_and_never_awards(web):
    app, client = web
    before = progression_rows(app.state.store)
    for id, expected in (
        ("shieldmaiden", 403),
        ("unknown", 422),
        ("https://evil.test/x.webp", 422),
    ):
        assert (
            client.patch("/api/profile/portrait", json={"portrait_id": id}).status_code == expected
        )
    assert progression_rows(app.state.store) == before
    csrf = client.headers.pop("X-CSRF-Token")
    assert (
        client.patch("/api/profile/portrait", json={"portrait_id": "shieldbearer"}).status_code
        == 403
    )
    client.headers["X-CSRF-Token"] = csrf
    first = client.patch("/api/profile/portrait", json={"portrait_id": "shieldbearer"})
    assert first.status_code == 200
    assert (
        client.patch("/api/profile/portrait", json={"portrait_id": "shieldbearer"}).json()
        == first.json()
    )
    with TestClient(app) as anonymous:
        assert anonymous.get("/api/personal/achievements").status_code == 401
        assert (
            anonymous.patch(
                "/api/profile/portrait", json={"portrait_id": "shieldbearer"}, headers=ORIGIN
            ).status_code
            == 401
        )


def test_local_game_awarded_through_real_move_api_without_visiting_hall(web):
    app, client = web
    game = play_local_draw(client)
    assert game["recorded_winner"] == "draw"
    with app.state.store.connection() as c:
        assert (
            c.execute(
                "SELECT count(*) FROM achievement_awards WHERE achievement_id='local-first-game'"
            ).fetchone()[0]
            == 1
        )
    assert client.get("/api/personal/achievements").json()["summary"]["earned_points"] == 10


def test_ai_victory_is_awarded_through_actual_human_and_computer_move_routes(web, monkeypatch):
    from record_fixtures import historical_game
    from hnefatafl.engine import Move

    app, client = web
    game = historical_game(client, ruleset="brandubh", mode="human-ai", human_side="attackers")
    scripted = iter([Move(25, 46), Move(23, 44)])

    def scripted_move(state, config, *, diagnostics):
        diagnostics["value"] = 0.25
        return next(scripted)

    monkeypatch.setattr("hnefatafl.ai.choose_move", scripted_move)
    for source, target in [(10, 8), (26, 25), (22, 23)]:
        response = client.post(
            f"/api/games/{game['id']}/moves",
            json={"version": game["version"], "source": source, "target": target},
        )
        assert response.status_code == 200, response.text
        game = response.json()
        if not game["winner"]:
            response = client.post(
                f"/api/games/{game['id']}/ai-move", json={"version": game["version"]}
            )
            assert response.status_code == 200, response.text
            game = response.json()
    assert game["recorded_winner"] == "attackers"
    with app.state.store.connection() as c:
        assert (
            c.execute(
                "SELECT count(*) FROM achievement_awards WHERE achievement_id='ai-first-victory'"
            ).fetchone()[0]
            == 1
        )
    assert (
        "forge-keeper"
        not in client.get("/api/personal/achievements").json()["unlocked_portrait_ids"]
    )


def test_ai_criteria_exclude_losses_unfinished_selfplay_and_other_owners(web):
    app, client = web
    account = account_of(app, client)
    for mode, winner in (("human-ai", None), ("human-ai", "attackers"), ("ai-ai", "defenders")):
        persist_game(app.state.store, account, mode=mode, winner=winner)
    assert client.get("/api/personal/achievements").json()["summary"]["earned_points"] == 0
    for _ in range(10):
        persist_game(app.state.store, account, winner="defenders")
    data = client.get("/api/personal/achievements").json()
    assert items_of(data)["ai-wins-10"]["unlocked"]
    assert "forge-keeper" in data["unlocked_portrait_ids"]
    assert client.get("/api/profile").json()["portrait_id"] == "shieldbearer"
    with app.state.store.connection() as c:
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("DELETE FROM achievement_awards")
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("DELETE FROM account_portraits")


@pytest.mark.parametrize("mode", ["simultaneous", "sequential"])
def test_two_leg_roles_and_thresholds_use_each_finalized_game(rig, mode):
    from uuid import uuid4

    first = rig.core.create(
        rig.credentials["Archer"], "brandubh", "attackers", 60, uuid4().hex, mode=mode
    )["game"]
    first = rig.core.mutate(rig.credentials["Keeper"], first["id"], 0, "join")["game"]
    first = finish(rig, first, "attackers")
    second = rig.core.read(rig.credentials["Archer"], first["match"]["games"][1]["id"])
    finish(rig, second, "defenders")
    data = progression(rig).achievements(rig.credentials["Archer"])
    assert items_of(data)["first-rated-win"]["progress"] == 2
    assert items_of(data)["defender-hold"]["progress"] == 1
    assert items_of(data)["attacker-wins-1"]["progress"] == 1
    assert data["summary"]["earned_points"] == 50


def test_hall_counter_reads_are_batched_and_read_only(rig):
    queries = []
    with rig.store.connection() as c:
        c.set_trace_callback(queries.append)
        data = progression(rig)._projection(c, rig.accounts["Archer"]["id"], 1)
    assert len(data["items"]) == 77
    assert sum(q.lstrip().upper().startswith("SELECT") for q in queries) < 15
    assert not any(q.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for q in queries)


def test_selected_portraits_propagate_to_online_and_leaderboard_without_private_progress(rig):
    for _ in range(10):
        game = finish(rig, active(rig), "attackers")
    owner = rig.accounts["Archer"]["id"]
    service = progression(rig)
    with rig.core._write() as c:
        service.select(c, owner, "shieldmaiden")

    board = rig.core.read(rig.credentials["Keeper"], game["id"])
    standings = rig.core.leaderboard(rig.credentials["Keeper"], "brandubh")
    participant = board["participants"]["attackers"]
    assert participant["portrait_id"] == "shieldmaiden"
    assert set(participant) <= {"account_id", "name", "username", "portrait_id"}
    own = next(row for row in standings["entries"] if row["account_id"] == owner)
    assert own["portrait_id"] == "shieldmaiden"
    assert standings["me"]["portrait_id"] == "shieldbearer"
    queries = []
    with rig.store.connection() as c:
        c.set_trace_callback(queries.append)
        rig.core.ratings.leaderboard(c, rig.accounts["Keeper"]["id"], "brandubh", 100, 0)
    assert len([query for query in queries if "FROM account_portrait_selection" in query]) == 1
    assert not {"achievements", "awards", "email", "date_of_birth"} & set(own)
    # A live lobby and direct read use the same safe selection, including default
    # fallback. Completed games are not included in the live lobby.
    from datetime import timedelta

    rig.clock.value += timedelta(hours=1)
    live = active(rig)
    lobby = rig.core.listing(rig.credentials["Keeper"], section="all")
    listed = next(item for item in lobby["games"] if item["id"] == live["id"])
    assert listed["participants"]["attackers"]["portrait_id"] == "shieldmaiden"
    with rig.store.connection() as c:
        c.execute("UPDATE portrait_catalog SET retired=1 WHERE id='shieldmaiden'")
    assert (
        rig.core.read(rig.credentials["Keeper"], live["id"])["participants"]["attackers"][
            "portrait_id"
        ]
        == "shieldbearer"
    )
    standings = rig.core.leaderboard(rig.credentials["Archer"], "brandubh")
    assert standings["me"]["portrait_id"] == "shieldbearer"


def test_automatic_awards_and_entitlements_are_append_only(rig):
    for _ in range(10):
        finish(rig, active(rig), "attackers")
    before = progression_rows(rig.store)
    for statement in (
        "UPDATE achievement_awards SET awarded_at=0",
        "DELETE FROM achievement_awards",
        "UPDATE account_portraits SET granted_at=0",
        "DELETE FROM account_portraits",
    ):
        with rig.store.connection() as c, pytest.raises(sqlite3.IntegrityError):
            c.execute(statement)
    assert progression_rows(rig.store) == before


def test_retired_portraits_and_extra_selection_fields_are_rejected(web):
    app, client = web
    assert (
        client.patch(
            "/api/profile/portrait",
            json={"portrait_id": "shieldbearer", "image_url": "https://evil.test/x.webp"},
        ).status_code
        == 422
    )
    with app.state.store.connection() as c:
        c.execute("UPDATE portrait_catalog SET retired=1 WHERE id='forge-keeper'")
    assert (
        client.patch("/api/profile/portrait", json={"portrait_id": "forge-keeper"}).status_code
        == 422
    )
    assert "forge-keeper" not in {
        item["id"] for item in client.get("/api/portraits/catalog").json()["items"]
    }


def test_concurrent_game_completions_and_retries_award_each_milestone_once(rig):
    from concurrent.futures import ThreadPoolExecutor
    from hnefatafl.online_core import OnlineError

    games = [active(rig), active(rig)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        completed = list(pool.map(lambda game: finish(rig, game, "defenders"), games))
    payload = progression(rig).achievements(rig.credentials["Keeper"])
    assert items_of(payload)["first-rated-win"]["progress"] == 2
    before = progression_rows(rig.store)
    for game in completed:
        with pytest.raises(OnlineError):
            rig.core.mutate(rig.credentials["Archer"], game["id"], game["version"] - 1, "concede")
    assert progression_rows(rig.store) == before
    with rig.store.connection() as c:
        assert (
            c.execute(
                "SELECT count(*) FROM achievement_awards WHERE account_id=? AND achievement_id='first-rated-win'",
                (rig.accounts["Keeper"]["id"],),
            ).fetchone()[0]
            == 1
        )


def test_completion_and_awards_roll_back_together(rig, monkeypatch):
    import hnefatafl.progression as module

    game = active(rig)
    before = progression_rows(rig.store)

    def fail(*args):
        raise RuntimeError("Controlled award failure")

    monkeypatch.setattr(module, "award_achievements", fail)
    with pytest.raises(RuntimeError, match="Controlled award failure"):
        finish(rig, game, "defenders")
    current = rig.core.read(rig.credentials["Archer"], game["id"])
    assert current["status"] == "active"
    assert progression_rows(rig.store) == before
    with rig.store.connection() as c:
        assert c.execute("SELECT count(*) FROM rating_applications").fetchone()[0] == 0
