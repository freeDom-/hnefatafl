"""Automatic milestone awards and durable manual portrait unlocks.

Reads and portrait selection never award. Corrections preserve rewards, while
current authoritative evidence controls future milestones.
"""

import fcntl
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

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
        "forge-keeper",
        "shieldmaiden",
        "raven-seer",
        "einherjar",
        "iron-helm",
        "valkyrie",
        "wanderer",
        "skald",
        "fjord-scout",
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


def test_portrait_display_order_is_explicit_and_deterministic():
    from hnefatafl.progression import (
        PORTRAIT_DISPLAY_ORDER,
        PORTRAITS,
        Portrait,
        _portrait_order,
    )

    assert [portrait.id for portrait in PORTRAITS] == list(PORTRAIT_DISPLAY_ORDER)
    # A portrait without an explicit position sorts after every listed portrait,
    # ordered by its stable id, so introducing one never renumbers the gallery.
    unlisted = sorted(
        [Portrait("zzz-new", "Zzz", None), Portrait("aaa-new", "Aaa", None)],
        key=_portrait_order,
    )
    assert [portrait.id for portrait in unlisted] == ["aaa-new", "zzz-new"]
    assert all(_portrait_order(portrait)[0] == len(PORTRAIT_DISPLAY_ORDER) for portrait in unlisted)


def test_existing_installation_reseeds_portrait_order_without_touching_progression(web):
    """A current-schema install with the previous ordinals upgrades on startup.

    Startup seeding is the only migration: reopening the store renumbers the
    catalog while every stored award, entitlement, selection, unlock association
    and catalog version is preserved, and repeated initialization is idempotent.
    """
    from hnefatafl.online_migrations import SCHEMA_VERSION
    from hnefatafl.progression import (
        ACHIEVEMENT_CATALOG_VERSION,
        PORTRAIT_CATALOG_VERSION,
        PORTRAIT_DISPLAY_ORDER,
        PORTRAITS,
        Portrait,
        _portrait_order,
    )

    app, client = web
    account = account_of(app, client)
    store = app.state.store
    # The exact persisted ordering this change replaces, written without a
    # schema or catalog-version change.
    previous_order = (
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
    )
    with store.connection() as c:
        for ordinal, portrait_id in enumerate(previous_order):
            c.execute("UPDATE portrait_catalog SET ordinal=? WHERE id=?", (ordinal, portrait_id))
        # Representative existing progression: a permanent award, its portrait
        # entitlement and a selected non-default portrait.
        c.execute(
            "INSERT INTO achievement_awards VALUES(?,?,?,?)",
            (account["id"], "rated-wins-10", 123, 1),
        )
        c.execute(
            "INSERT INTO account_portraits VALUES(?,?,?,?)",
            (account["id"], "shieldmaiden", 123, "rated-wins-10"),
        )
        c.execute(
            "INSERT INTO account_portrait_selection VALUES(?,?,?)",
            (account["id"], "shieldmaiden", 123),
        )

    def catalog_rows(target):
        with target.connection() as c:
            return {
                row["id"]: (row["name"], row["unlock_achievement_id"], row["retired"])
                for row in c.execute(
                    "SELECT id,name,unlock_achievement_id,retired FROM portrait_catalog"
                )
            }

    def ordered_catalog(target):
        """The complete stored portrait catalog in order, including ordinals."""
        with target.connection() as c:
            return [
                (
                    row["id"],
                    row["ordinal"],
                    row["name"],
                    row["unlock_achievement_id"],
                    row["retired"],
                )
                for row in c.execute(
                    "SELECT id,ordinal,name,unlock_achievement_id,retired "
                    "FROM portrait_catalog ORDER BY ordinal,id"
                )
            ]

    def catalog_versions(target):
        with target.connection() as c:
            return (
                c.execute("PRAGMA user_version").fetchone()[0],
                c.execute("SELECT max(catalog_version) FROM portrait_catalog").fetchone()[0],
                c.execute("SELECT max(catalog_version) FROM achievement_catalog").fetchone()[0],
            )

    before_rows = progression_rows(store)
    before_catalog = catalog_rows(store)
    before_versions = catalog_versions(store)
    assert before_versions == (
        SCHEMA_VERSION,
        PORTRAIT_CATALOG_VERSION,
        ACHIEVEMENT_CATALOG_VERSION,
    )
    # The previous persisted ordering, including its ordinal values, exists
    # before startup seeding runs.
    assert [row[0] for row in ordered_catalog(store)] == list(previous_order)

    # Reopen through the normal Store initialization path. Only the ordinals move.
    reopened = Store(store.path)
    reopened_catalog = ordered_catalog(reopened)
    assert [row[0] for row in reopened_catalog] == list(PORTRAIT_DISPLAY_ORDER)
    assert [row[1] for row in reopened_catalog] == list(range(len(PORTRAIT_DISPLAY_ORDER)))
    catalog = client.get("/api/portraits/catalog").json()
    assert [item["id"] for item in catalog["items"]] == list(PORTRAIT_DISPLAY_ORDER)
    assert catalog["default_portrait_id"] == "shieldbearer"
    assert catalog["catalog_version"] == PORTRAIT_CATALOG_VERSION
    assert progression_rows(reopened) == before_rows
    assert catalog_rows(reopened) == before_catalog
    assert catalog_versions(reopened) == before_versions
    # Names and unlock associations still match the code-owned catalog exactly.
    assert {
        portrait.id: (portrait.name, portrait.unlock_achievement_id) for portrait in PORTRAITS
    } == {
        portrait_id: (name, unlock)
        for portrait_id, (name, unlock, _retired) in before_catalog.items()
    }
    # The deterministic fallback for a future portrait without a position remains.
    assert _portrait_order(Portrait("future-portrait", "Future", None))[0] == len(
        PORTRAIT_DISPLAY_ORDER
    )

    # Repeated initialization stays idempotent: the complete ordered catalog
    # and every ordinal value are unchanged, and the API still exposes it.
    Store(reopened.path)
    assert ordered_catalog(reopened) == reopened_catalog
    assert [item["id"] for item in client.get("/api/portraits/catalog").json()["items"]] == list(
        PORTRAIT_DISPLAY_ORDER
    )
    assert progression_rows(reopened) == before_rows
    assert catalog_rows(reopened) == before_catalog
    assert catalog_versions(reopened) == before_versions


def test_fresh_read_is_read_only_and_achievements_have_no_claim_endpoint(web):
    app, client = web
    before = progression_rows(app.state.store)
    data = client.get("/api/personal/achievements").json()
    assert data["catalog_version"] == 3
    assert data["summary"] == {"unlocked": 0, "total": 77, "earned_points": 0, "total_points": 6290}
    assert len(data["families"]) == 10
    assert data["unlocked_portrait_ids"] == []
    assert client.get("/api/profile").json()["portrait_id"] == "shieldbearer"
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
        assert (
            client.post("/api/portraits/unlock", json={"portrait_id": "shieldbearer"}).status_code
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
    assert before["unlocked_portrait_ids"] == []
    credential = rig.credentials["Archer"]
    assert service.unlock(credential, "shieldmaiden")["newly_unlocked"] is True
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
    assert after["unlocked_portrait_ids"] == ["shieldmaiden"]
    assert service.unlock(credential, "shieldmaiden")["newly_unlocked"] is False
    # A previously earned milestone can still be used to claim its portrait.
    assert service.unlock(credential, "wanderer")["newly_unlocked"] is True
    rig.core.mutate(
        rig.credentials["Archer"], games[0]["id"], games[0]["version"], "void", note="Correction"
    )
    assert (
        service.achievements(rig.credentials["Archer"])["summary"]["earned_points"]
        == before["summary"]["earned_points"]
    )


def test_duplicate_results_rebuilds_and_concurrent_reads_never_duplicate_rewards(rig):
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
    monkeypatch.setattr(module, "award_achievements", lambda *args, **kwargs: None)
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
    assert client.post("/api/portraits/unlock", json={"portrait_id": "shieldmaiden"}).json() == {
        "portrait_id": "shieldmaiden",
        "newly_unlocked": False,
    }
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


@pytest.mark.parametrize("wins", [9, 10, 11])
def test_unlock_requires_the_automatically_awarded_milestone(web, wins):
    app, client = web
    account = account_of(app, client)
    choice = {"portrait_id": "forge-keeper"}
    for _ in range(wins):
        persist_game(app.state.store, account, winner="defenders")
    before = client.get("/api/personal/achievements").json()
    milestone = items_of(before)["ai-wins-10"]
    assert milestone["progress"] == wins
    assert milestone["unlocked"] == (wins >= 10)
    assert before["unlocked_portrait_ids"] == []
    assert client.patch("/api/profile/portrait", json=choice).status_code == 403

    response = client.post("/api/portraits/unlock", json=choice)
    if wins < 10:
        assert response.status_code == 403
        assert client.get("/api/personal/achievements").json()["unlocked_portrait_ids"] == []
        return

    assert response.status_code == 200
    assert response.json() == {**choice, "newly_unlocked": True}
    assert client.get("/api/profile").json()["portrait_id"] == "shieldbearer"
    after = client.get("/api/personal/achievements").json()
    assert after["summary"] == before["summary"]
    assert after["unlocked_portrait_ids"] == ["forge-keeper"]
    selection = client.patch("/api/profile/portrait", json=choice)
    assert selection.status_code == 200 and selection.json()["portrait_id"] == "forge-keeper"


def test_unlock_requires_own_authenticated_csrf_protected_request(web, rig):
    app, client = web
    choice = {"portrait_id": "forge-keeper"}
    path = "/api/portraits/unlock"
    for _ in range(10):
        persist_game(app.state.store, rig.accounts["Archer"], winner="defenders")
    with TestClient(app) as other:
        assert other.post(path, json=choice).status_code == 401
        login(other, "Keeper")
        assert other.post(path, json=choice).status_code == 403
        assert (
            other.post(
                path, json={**choice, "account_id": rig.accounts["Archer"]["id"]}
            ).status_code
            == 422
        )

    assert client.post(path, json=choice, headers={"X-CSRF-Token": "wrong"}).status_code == 403
    assert (
        client.post(path, json=choice, headers={"Origin": "https://other.example"}).status_code
        == 403
    )
    for invalid in ("unknown", "", "bad id", 42, "x" * 65):
        assert client.post(path, json={"portrait_id": invalid}).status_code == 422
    assert client.post(path, json={**choice, "progress": 1000}).status_code == 422
    assert client.post(path, json={**choice, "ignored": "x" * 8192}).status_code == 413
    with app.state.store.connection() as c:
        c.execute("UPDATE portrait_catalog SET retired=1 WHERE id='forge-keeper'")
    assert client.post(path, json=choice).status_code == 422
    assert client.post(path, json={"portrait_id": "shieldbearer"}).json() == {
        "portrait_id": "shieldbearer",
        "newly_unlocked": False,
    }
    assert progression_rows(app.state.store)["account_portraits"] == []


def test_concurrent_unlocks_and_restart_preserve_claims_without_auto_claiming_others(web, rig):
    app, first_tab = web
    for _ in range(10):
        finish(rig, active(rig), "attackers")
    choice = {"portrait_id": "shieldmaiden"}
    path = "/api/portraits/unlock"
    barrier = Barrier(2)

    def request(tab):
        barrier.wait(timeout=5)
        response = tab.post(path, json=choice)
        assert response.status_code == 200, response.text
        return response.json()["newly_unlocked"]

    with TestClient(app) as second_tab, ThreadPoolExecutor(max_workers=2) as workers:
        login(second_tab)
        assert sorted(workers.map(request, [first_tab, second_tab])) == [False, True]
    result = first_tab.patch("/api/profile/portrait", json=choice)
    assert result.status_code == 200 and result.json()["portrait_id"] == "shieldmaiden"
    before = progression_rows(app.state.store)
    assert len(before["account_portraits"]) == 1

    restarted = create_app(rig.store.path.parent, online_background=False, account_background=False)
    with TestClient(restarted, cookies=first_tab.cookies, headers=first_tab.headers) as tab:
        progress = tab.get("/api/personal/achievements").json()
        assert progress["unlocked_portrait_ids"] == ["shieldmaiden"]
        assert items_of(progress)["brandubh-wins-10"]["unlocked"] is True
        assert (
            tab.patch("/api/profile/portrait", json={"portrait_id": "wanderer"}).status_code == 403
        )
        assert tab.get("/api/profile").json()["portrait_id"] == "shieldmaiden"
        assert tab.post(path, json=choice).json() == {**choice, "newly_unlocked": False}
    assert progression_rows(restarted.state.store) == before


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
    assert "forge-keeper" not in data["unlocked_portrait_ids"]
    assert (
        client.post("/api/portraits/unlock", json={"portrait_id": "forge-keeper"}).status_code
        == 200
    )
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
    service.unlock(rig.credentials["Archer"], "shieldmaiden")
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


def test_automatic_awards_and_claimed_entitlements_are_append_only(rig):
    for _ in range(10):
        finish(rig, active(rig), "attackers")
    progression(rig).unlock(rig.credentials["Archer"], "shieldmaiden")
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

    def fail(*args, **kwargs):
        raise RuntimeError("Controlled award failure")

    monkeypatch.setattr(module, "award_achievements", fail)
    with pytest.raises(RuntimeError, match="Controlled award failure"):
        finish(rig, game, "defenders")
    current = rig.core.read(rig.credentials["Archer"], game["id"])
    assert current["status"] == "active"
    assert progression_rows(rig.store) == before
    with rig.store.connection() as c:
        assert c.execute("SELECT count(*) FROM rating_applications").fetchone()[0] == 0


@pytest.mark.parametrize("pool", ["brandubh", None])
def test_rating_rebuild_scopes_award_queries_to_ledger_actors(rig, pool):
    from uuid import uuid4

    other = rig.core.create(
        rig.credentials["Visitor"], "tablut", "attackers", 60, uuid4().hex, rated=True
    )["game"]
    other = rig.core.mutate(rig.credentials["Archer"], other["id"], 0, "join")["game"]
    finish(rig, other, "attackers")
    game = finish(rig, active(rig), "attackers")
    fabricate_correction(rig, game, "defenders")
    queries = []
    original = Progression._measure

    def measured(c, actor, achievement, cache):
        queries.append((actor, achievement.criterion))
        return original(c, actor, achievement, cache)

    from unittest.mock import patch

    rig.core.rebuild_ratings(rig.credentials["Archer"], pool)
    before = progression_rows(rig.store)
    with patch.object(Progression, "_measure", staticmethod(measured)):
        rig.core.rebuild_ratings(rig.credentials["Archer"], pool)
        rig.core.rebuild_ratings(rig.credentials["Archer"], pool)
    assert queries
    assert {actor for actor, _ in queries} <= {
        rig.accounts[n]["id"]
        for n in (("Archer", "Keeper", "Visitor") if pool is None else ("Archer", "Keeper"))
    }
    assert not {criterion for _, criterion in queries} & {
        "completed_local_games",
        "defeated_ai_games",
        "analyzed_replays",
    }
    assert progression_rows(rig.store) == before


def test_rebuild_query_trace_avoids_unrelated_game_history(rig, capsys):
    from hnefatafl.progression import reconcile_legacy_achievements
    from unittest.mock import patch

    for i in range(10):
        finish(rig, active(rig, ("brandubh", "tablut", "copenhagen")[i % 3]), "attackers")
    traces = {"historical": [], "rated": []}
    with rig.core._write() as c:
        c.set_trace_callback(traces["historical"].append)
        reconcile_legacy_achievements(c, 1)
    original = rig.store.connection
    from contextlib import contextmanager

    @contextmanager
    def traced():
        with original() as c:
            c.set_trace_callback(traces["rated"].append)
            yield c

    with patch.object(rig.store, "connection", traced):
        rig.core.rebuild_ratings(rig.credentials["Archer"], "brandubh")
    counts = {
        name: sum("SELECT count(*) FROM games" in q for q in queries)
        for name, queries in traces.items()
    }
    assert counts == {"historical": 6, "rated": 0}
    print(counts)


def test_ordinary_completion_skips_the_full_streak_fold_when_no_milestone_can_change(
    rig, monkeypatch
):
    """A loss, a draw and a below-threshold tail never fold the whole ledger."""
    import hnefatafl.progression as module
    from hnefatafl.progression import RATED_CRITERIA, award_achievements

    archer = rig.accounts["Archer"]["id"]
    keeper = rig.accounts["Keeper"]["id"]
    folds = []
    original = module.rated_streaks

    def tracked(c, actor):
        folds.append(actor)
        return original(c, actor)

    monkeypatch.setattr(module, "rated_streaks", tracked)

    # The first win awards streak 1: the winner folds exactly once, the loser
    # never does, because a fresh loss cannot raise a best winning streak.
    finish(rig, active(rig), "attackers")
    assert folds == [archer]
    folds.clear()

    # A loss for Archer and a win for Keeper considers only the winner.
    finish(rig, active(rig), "defenders")
    assert folds == [keeper]
    folds.clear()

    # Archer's run is back to zero. A single new win leaves the bounded tail at
    # one, below the next unawarded milestone of three, so nothing folds.
    finish(rig, active(rig), "attackers")
    assert folds == []
    finish(rig, active(rig), "attackers")  # tail two, still below three
    assert folds == []

    # The third consecutive win reaches the milestone, so the exact fold runs
    # and the award is granted.
    finish(rig, active(rig), "attackers")
    assert folds == [archer]
    assert items_of(progression(rig).achievements(rig.credentials["Archer"]))["rated-win-streak-3"][
        "unlocked"
    ]

    # A draw names an empty tail: neither participant folds, even with history.
    folds.clear()
    with rig.store.connection() as c:
        c.execute("BEGIN IMMEDIATE")
        award_achievements(c, (archer, keeper), 1, RATED_CRITERIA, streak_tails=frozenset())
    assert folds == []


def test_rebuild_measures_exactly_and_finds_a_longer_streak_after_a_correction(rig):
    """Historical corrections never take the tail shortcut."""
    for _ in range(2):
        finish(rig, active(rig), "attackers")
    loss = finish(rig, active(rig), "defenders")
    for _ in range(2):
        finish(rig, active(rig), "attackers")
    payload = progression(rig).achievements(rig.credentials["Archer"])
    assert not items_of(payload)["rated-win-streak-3"]["unlocked"]

    # Turning the single loss into a win merges the runs into five consecutive
    # wins. Only the exact rebuild measurement can see that.
    fabricate_correction(rig, loss, "attackers")
    rig.core.rebuild_ratings(rig.credentials["Archer"])
    payload = progression(rig).achievements(rig.credentials["Archer"])
    assert items_of(payload)["rated-win-streak-3"]["unlocked"]
    assert items_of(payload)["rated-win-streak-5"]["unlocked"]


def test_tail_streak_is_bounded_and_stops_at_the_first_non_win(rig):
    """The tail probe never folds more than the requested cap."""
    from hnefatafl.progression import _tail_win_streak

    archer = rig.accounts["Archer"]["id"]
    for _ in range(3):
        finish(rig, active(rig), "attackers")
    finish(rig, active(rig), "defenders")
    for _ in range(5):
        finish(rig, active(rig), "attackers")
    with rig.store.connection() as c:
        assert _tail_win_streak(c, archer, 3) == 3
        assert _tail_win_streak(c, archer, 5) == 5
        assert _tail_win_streak(c, archer, 100) == 5
