"""Schema-17 portrait catalog, achievement awards and portrait selection.

Awards are derived from authoritative records only. These tests drive real rated
games, real voids/corrections, real account games and real sessions, and assert
that the progress read is read-only, that confirmation is an explicit
CSRF-protected operation, and that a reward is confirmed at most once and never
revoked or leaked.
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
    PORTRAITS,
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


def confirm(client):
    """The explicit CSRF-protected confirmation; returns its projection."""
    response = client.post("/api/personal/achievements/confirm")
    assert response.status_code == 200, response.text
    return response.json()


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
    with store.connection() as c:
        c.execute(
            "INSERT INTO games(id,version,created_at,data,owner_account_id,category) "
            "VALUES(?,?,?,?,?,?)",
            (game["id"], 0, game["created_at"], json.dumps(game), account["id"], category),
        )
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


def test_portrait_catalog_is_server_owned_with_an_always_available_default(web):
    _, client = web
    payload = client.get("/api/portraits/catalog").json()
    assert payload["catalog_version"] == 1
    assert payload["default_portrait_id"] == "shieldbearer"
    assert [item["id"] for item in payload["items"]] == [portrait.id for portrait in PORTRAITS]
    assert all(set(item) == {"id", "name", "unlock_achievement_id"} for item in payload["items"])
    unlocks = {item["id"]: item["unlock_achievement_id"] for item in payload["items"]}
    assert unlocks["shieldbearer"] is None
    assert unlocks["shieldmaiden"] == "first-rated-win"
    assert unlocks["forge-keeper"] == "ai-first-victory"
    # Only stable IDs and metadata: never an asset path or a client-supplied URL.
    assert "webp" not in client.get("/api/portraits/catalog").text


def test_progression_reads_require_a_current_authenticated_session(web):
    app, _ = web
    with TestClient(app) as anonymous:
        assert anonymous.get("/api/portraits/catalog").status_code == 401
        assert anonymous.get("/api/personal/achievements").status_code == 401
        assert anonymous.post("/api/personal/achievements/confirm").status_code == 401
        assert (
            anonymous.patch(
                "/api/profile/portrait", json={"portrait_id": "shieldbearer"}, headers=ORIGIN
            ).status_code
            == 401
        )


def test_achievement_read_stays_available_under_the_freeze_but_confirmation_does_not(web):
    app, client = web
    with app.state.auth.gate_path.open("rb") as gate:
        fcntl.flock(gate, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # The read is read-only, so a frozen deployment still serves it.
        assert client.get("/api/personal/achievements").status_code == 200
        # Confirmation is a non-SAFE mutation, so the freeze refuses it like a write.
        assert client.post("/api/personal/achievements/confirm").status_code == 503
        # A pure catalog read writes nothing and stays available under the freeze.
        assert client.get("/api/portraits/catalog").status_code == 200
    assert client.get("/api/personal/achievements").status_code == 200
    assert client.post("/api/personal/achievements/confirm").status_code == 200


def test_achievement_empty_state_lists_every_milestone_at_zero(web):
    app, client = web
    payload = client.get("/api/personal/achievements").json()
    assert payload["catalog_version"] == 2
    assert payload["portrait_catalog_version"] == 1
    assert [item["id"] for item in payload["items"]] == [
        achievement.id for achievement in ACHIEVEMENTS
    ]
    assert all(
        item["progress"] == 0 and item["unlocked"] is False and item["awarded_at"] is None
        for item in payload["items"]
    )
    assert payload["summary"] == {
        "unlocked": 0,
        "total": len(ACHIEVEMENTS),
        "earned_points": 0,
        "total_points": 6290,
        "claimable_points": 0,
    }
    assert payload["as_of"].endswith("Z")
    # A fresh account's read confirms nothing: no award, entitlement or selection.
    with app.state.store.connection() as c:
        for table in ("achievement_awards", "account_portraits", "account_portrait_selection"):
            assert c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
    assert by_progress(payload, "ai-first-victory")["portrait_id"] == "forge-keeper"
    assert by_progress(payload, "local-first-game")["portrait_id"] is None


def test_achievement_read_never_writes_and_confirmation_is_the_only_write(rig):
    # The authoritative ledger already proves the milestone, but the read only
    # projects it: no award, entitlement or selection row is created.
    finish(rig, active(rig), "attackers")
    before = progression_rows(rig.store)
    read = items_of(progression(rig).achievements(rig.credentials["Archer"]))
    assert read["first-rated-win"]["progress"] == 1
    assert read["first-rated-win"]["unlocked"] is False
    assert progression_rows(rig.store) == before
    # Only the explicit confirmation writes, and it returns the same projection.
    confirmed = items_of(progression(rig).confirm(rig.credentials["Archer"]))
    assert confirmed["first-rated-win"]["unlocked"] is True
    assert progression_rows(rig.store) != before


def test_achievement_confirmation_requires_csrf_and_a_same_origin(web):
    app, client = web
    csrf = client.headers.pop("X-CSRF-Token")
    # A non-SAFE confirmation without the session CSRF token is refused.
    assert client.post("/api/personal/achievements/confirm").status_code == 403
    client.headers["X-CSRF-Token"] = "forged"
    assert client.post("/api/personal/achievements/confirm").status_code == 403
    client.headers["X-CSRF-Token"] = csrf
    assert (
        client.post(
            "/api/personal/achievements/confirm", headers={"Origin": "https://evil.test"}
        ).status_code
        == 403
    )
    assert client.post("/api/personal/achievements/confirm").status_code == 200


def test_achievement_confirmation_is_self_scoped_and_idempotent(rig):
    app = create_app(rig.store.path.parent, online_background=False, account_background=False)
    with TestClient(app) as archer, TestClient(app) as keeper:
        login(archer, "Archer")
        login(keeper, "Keeper")
        finish(rig, active(rig), "attackers")
        # Archer confirms only their own award; Keeper's account stays untouched.
        first = items_of(confirm(archer))
        assert first["first-rated-win"]["unlocked"] is True
        keeper_read = items_of(keeper.get("/api/personal/achievements").json())
        assert keeper_read["first-rated-win"]["unlocked"] is False
        assert keeper_read["first-rated-win"]["progress"] == 0
        # Repeating the confirmation re-inserts the same key: one row, same time.
        second = items_of(confirm(archer))
        assert second["first-rated-win"]["awarded_at"] == first["first-rated-win"]["awarded_at"]
        with app.state.store.connection() as c:
            assert (
                c.execute(
                    "SELECT COUNT(*) FROM achievement_awards WHERE achievement_id='first-rated-win'"
                ).fetchone()[0]
                == 1
            )


def by_progress(payload, identifier):
    return items_of(payload)[identifier]


def test_rated_milestones_are_derived_from_the_applied_ledger(rig):
    service = progression(rig)
    finish(rig, active(rig, "brandubh"), "attackers")
    finish(rig, active(rig, "tablut"), "defenders")
    archer = items_of(service.confirm(rig.credentials["Archer"]))
    keeper = items_of(service.confirm(rig.credentials["Keeper"]))
    assert archer["first-rated-win"]["unlocked"] is True
    assert archer["first-rated-win"]["progress"] == 1
    assert archer["first-rated-win"]["awarded_at"].endswith("Z")
    assert archer["brandubh-initiate"]["unlocked"] is True
    assert archer["tablut-initiate"]["unlocked"] is False
    assert archer["defender-hold"]["unlocked"] is False
    assert archer["rated-wins-10"]["unlocked"] is False
    # The opponent's own projection only reflects their own side and ruleset.
    assert keeper["first-rated-win"]["unlocked"] is True
    assert keeper["defender-hold"]["unlocked"] is True
    assert keeper["tablut-initiate"]["unlocked"] is True
    assert keeper["brandubh-initiate"]["unlocked"] is False
    assert keeper["local-first-game"]["unlocked"] is False
    assert keeper["ai-first-victory"]["unlocked"] is False


def test_unrated_games_never_award_rated_milestones(rig):
    finish(rig, make_historical_casual(rig, active(rig)), "attackers")
    items = items_of(progression(rig).confirm(rig.credentials["Archer"]))
    assert all(item["unlocked"] is False for item in items.values())
    assert items["first-rated-win"]["progress"] == 0


def test_voided_results_never_leak_rewards_and_confirmed_awards_are_permanent(rig):
    service = progression(rig)
    voided = finish(rig, active(rig), "attackers")
    rig.core.mutate(
        rig.credentials["Archer"], voided["id"], voided["version"], "void", note="Incident"
    )
    # A result withdrawn before the first confirmation can never award anything.
    leaked = items_of(service.confirm(rig.credentials["Archer"]))
    assert leaked["first-rated-win"]["unlocked"] is False
    assert leaked["first-rated-win"]["progress"] == 0

    confirmed = finish(rig, active(rig), "attackers")
    first = items_of(service.confirm(rig.credentials["Archer"]))
    assert first["first-rated-win"]["unlocked"] is True
    rig.core.mutate(
        rig.credentials["Archer"], confirmed["id"], confirmed["version"], "void", note="Incident"
    )
    after = items_of(service.confirm(rig.credentials["Archer"]))
    # The confirmed unlock is permanent even though the current evidence is gone.
    assert after["first-rated-win"]["unlocked"] is True
    assert after["first-rated-win"]["awarded_at"] == first["first-rated-win"]["awarded_at"]
    assert after["first-rated-win"]["progress"] == 0


def test_streak_awards_follow_the_corrected_ledger(rig):
    service = progression(rig)
    games = [finish(rig, active(rig), "attackers") for _ in range(5)]
    assert (
        items_of(service.confirm(rig.credentials["Archer"]))["rated-win-streak-5"]["unlocked"]
        is True
    )
    fabricate_correction(rig, games[-1], "defenders")
    items = items_of(service.confirm(rig.credentials["Archer"]))
    assert items["rated-win-streak-5"]["unlocked"] is True
    assert items["rated-win-streak-5"]["progress"] == 4
    assert items["rated-wins-10"]["unlocked"] is False


def test_repeated_confirmations_and_pool_rebuilds_never_duplicate_awards(rig):
    service = progression(rig)
    for _ in range(5):
        finish(rig, active(rig), "attackers")
    first = items_of(service.confirm(rig.credentials["Archer"]))
    assert first["rated-win-streak-5"]["unlocked"] is True

    def stored():
        with rig.store.connection() as c:
            return (
                [
                    tuple(row)
                    for row in c.execute(
                        "SELECT account_id,achievement_id,awarded_at FROM achievement_awards "
                        "ORDER BY account_id,achievement_id"
                    )
                ],
                [
                    tuple(row)
                    for row in c.execute(
                        "SELECT account_id,portrait_id FROM account_portraits "
                        "ORDER BY account_id,portrait_id"
                    )
                ],
            )

    awards, entitlements = stored()
    assert awards and entitlements
    repeated = items_of(service.confirm(rig.credentials["Archer"]))
    assert {key: item["awarded_at"] for key, item in repeated.items()} == {
        key: item["awarded_at"] for key, item in first.items()
    }
    # A deterministic rebuild rewrites every application without re-awarding.
    with rig.core._write() as c:
        rig.core.ratings.rebuild_all(c, rig.clock.value)
    assert stored() == (awards, entitlements)
    assert (
        items_of(service.confirm(rig.credentials["Archer"]))["rated-win-streak-5"]["awarded_at"]
        == first["rated-win-streak-5"]["awarded_at"]
    )


def test_confirmed_awards_and_entitlements_are_append_only(rig):
    finish(rig, active(rig), "attackers")
    progression(rig).confirm(rig.credentials["Archer"])
    with rig.store.connection() as c:
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("UPDATE achievement_awards SET awarded_at=0")
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("DELETE FROM account_portraits")
        assert c.execute("SELECT COUNT(*) FROM achievement_awards").fetchone()[0] > 0


def test_local_and_vs_ai_milestones_require_completed_owned_games(web):
    app, client = web
    account = account_of(app, client)
    # An unfinished game, a loss and an AI-vs-AI game never confirm a milestone.
    persist_game(app.state.store, account, human_side="defenders", winner=None)
    persist_game(app.state.store, account, human_side="defenders", winner="attackers")
    persist_game(app.state.store, account, mode="ai-ai", human_side="defenders", winner="defenders")
    items = items_of(confirm(client))
    assert items["ai-first-victory"]["progress"] == 0
    assert items["ai-first-victory"]["unlocked"] is False
    assert items["local-first-game"]["unlocked"] is False

    persist_game(app.state.store, account, human_side="defenders", winner="defenders")
    items = items_of(confirm(client))
    assert items["ai-first-victory"]["unlocked"] is True
    assert items["ai-first-victory"]["progress"] == 1
    # Another account's completed games never count for this account.
    other = app.state.auth.account(rig_other_account(app))
    persist_game(app.state.store, other, human_side="defenders", winner="defenders")
    assert items_of(confirm(client))["ai-first-victory"]["progress"] == 1

    # A finished same-device local game confirms the local milestone end to end.
    local = play_local_draw(client)
    assert local["recorded_result"]["winner"] == "draw"
    assert items_of(confirm(client))["local-first-game"]["unlocked"] is True


def rig_other_account(app):
    with app.state.store.connection() as c:
        return c.execute("SELECT id FROM accounts WHERE username='Keeper'").fetchone()[0]


def test_portrait_selection_is_idempotent_and_rejects_unearned_unknown_and_retired(web):
    app, client = web
    assert client.get("/api/profile").json()["portrait_id"] == "shieldbearer"
    first = client.patch("/api/profile/portrait", json={"portrait_id": "shieldbearer"})
    assert first.status_code == 200 and first.json()["portrait_id"] == "shieldbearer"
    repeat = client.patch("/api/profile/portrait", json={"portrait_id": "shieldbearer"})
    assert repeat.status_code == 200 and repeat.json() == first.json()

    assert (
        client.patch("/api/profile/portrait", json={"portrait_id": "berserker"}).status_code == 403
    )
    assert (
        client.patch("/api/profile/portrait", json={"portrait_id": "no-such-portrait"}).status_code
        == 422
    )
    assert (
        client.patch(
            "/api/profile/portrait", json={"portrait_id": "https://evil.test/portrait.webp"}
        ).status_code
        == 422
    )
    assert (
        client.patch(
            "/api/profile/portrait",
            json={"portrait_id": "shieldbearer", "image_url": "https://evil.test/x.webp"},
        ).status_code
        == 422
    )

    # The confirmed milestone grants its portrait, which then becomes selectable.
    persist_game(app.state.store, account_of(app, client), winner="defenders")
    earned = client.patch("/api/profile/portrait", json={"portrait_id": "forge-keeper"})
    assert earned.status_code == 200, earned.text
    assert earned.json()["portrait_id"] == "forge-keeper"
    assert client.get("/api/profile").json()["portrait_id"] == "forge-keeper"
    # The profile publishes the selection only, never achievement progress.
    assert not {"achievements", "progress", "awards"} & set(earned.json())

    # A catalog change retires it: it is no longer selectable and the published
    # portrait falls back to the always-available default.
    with app.state.store.connection() as c:
        c.execute("UPDATE portrait_catalog SET retired=1 WHERE id='forge-keeper'")
    assert (
        client.patch("/api/profile/portrait", json={"portrait_id": "forge-keeper"}).status_code
        == 422
    )
    assert client.get("/api/profile").json()["portrait_id"] == "shieldbearer"
    catalog = client.get("/api/portraits/catalog").json()
    assert "forge-keeper" not in [item["id"] for item in catalog["items"]]
    assert catalog["default_portrait_id"] == "shieldbearer"


def test_selection_requires_csrf_and_sessions_stay_self_scoped(rig):
    app = create_app(rig.store.path.parent, online_background=False, account_background=False)
    with TestClient(app) as archer, TestClient(app) as keeper:
        login(archer, "Archer")
        login(keeper, "Keeper")
        finish(rig, active(rig), "attackers")
        assert items_of(confirm(archer))["first-rated-win"]["unlocked"] is True
        assert (
            items_of(keeper.get("/api/personal/achievements").json())["first-rated-win"]["unlocked"]
            is False
        )
        # The selection mutation keeps the existing CSRF requirement.
        csrf = archer.headers.pop("X-CSRF-Token")
        assert (
            archer.patch("/api/profile/portrait", json={"portrait_id": "shieldbearer"}).status_code
            == 403
        )
        archer.headers["X-CSRF-Token"] = csrf
        assert (
            archer.patch("/api/profile/portrait", json={"portrait_id": "shieldbearer"}).status_code
            == 200
        )
        # A disabled account loses both the read and the selection immediately.
        app.state.auth.update_account(rig.accounts["Keeper"]["id"], active=False)
        assert keeper.get("/api/personal/achievements").status_code == 401
        assert (
            keeper.patch("/api/profile/portrait", json={"portrait_id": "shieldbearer"}).status_code
            == 401
        )
        assert archer.get("/api/portraits/catalog").status_code == 200


def test_schema17_migration_seeds_catalogs_without_granting_awards(rig):
    finish(rig, active(rig), "attackers")
    path = rig.store.path
    with rig.store.connection() as c:
        for table in PROGRESSION_TABLES:
            c.execute(f"DROP TABLE {table}")
        c.execute("PRAGMA user_version=16")
    migrated = Store(path)
    with migrated.connection() as c:
        # Combined schema-18 migration: schema-16 upgrade reaches 18 while the
        # schema-17 progression tables are (re)created and seeded.
        assert c.execute("PRAGMA user_version").fetchone()[0] == 20
        assert c.execute("SELECT COUNT(*) FROM portrait_catalog").fetchone()[0] == len(PORTRAITS)
        assert c.execute("SELECT COUNT(*) FROM achievement_catalog").fetchone()[0] == len(
            ACHIEVEMENTS
        )
        # The migration itself grants no award, entitlement or selection.
        for table in ("achievement_awards", "account_portraits", "account_portrait_selection"):
            assert c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
        assert (
            c.execute(
                "SELECT id FROM portrait_catalog WHERE unlock_achievement_id IS NULL AND retired=0"
            ).fetchall()[0][0]
            == "shieldbearer"
        )
    # Reopening keeps the catalogs and adds nothing else.
    Store(path)
    with migrated.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM achievement_awards").fetchone()[0] == 0
    # The historical rated win stays authoritative evidence, so the first
    # confirmation confirms it instead of leaving progress and unlock disagreeing.
    items = items_of(progression(rig).confirm(rig.credentials["Archer"]))
    assert items["first-rated-win"]["unlocked"] is True
    assert items["first-rated-win"]["progress"] == 1


def test_selected_portraits_propagate_to_online_and_leaderboard_without_private_progress(rig):
    game = finish(rig, active(rig), "attackers")
    owner = rig.accounts["Archer"]["id"]
    service = progression(rig)
    service.confirm(rig.credentials["Archer"])
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


def test_tier_catalog_schedule_and_legacy_ids():
    from hnefatafl.progression import FAMILIES

    assert len(FAMILIES) == 9
    assert len(ACHIEVEMENTS) == 77
    assert sum(a.points for a in ACHIEVEMENTS if a.family != "local-first-game") == 6280
    assert sum(a.points for a in ACHIEVEMENTS) == 6290
    assert len({a.id for a in ACHIEVEMENTS}) == 77
    for family, *_ in FAMILIES:
        tiers = [a for a in ACHIEVEMENTS if a.family == family]
        expected = (
            (1, 3, 5, 10)
            if family == "rated-win-streak"
            else (1, 3, 10, 25, 50, 100, 250, 500, 1000)
        )
        assert tuple(a.threshold for a in tiers) == expected
        points = (
            (10, 30, 60, 100)
            if family == "rated-win-streak"
            else (10, 20, 30, 40, 60, 80, 120, 160, 240)
        )
        assert tuple(a.points for a in tiers) == points
        assert [a.tier for a in tiers] == list(range(1, len(tiers) + 1))
    assert all(
        p.unlock_achievement_id is None
        or any(a.id == p.unlock_achievement_id for a in ACHIEVEMENTS)
        for p in PORTRAITS
    )


def test_every_tier_boundary_and_permanent_points(rig, monkeypatch):
    """Exercise all 76 boundaries against the actual transactional award path.

    Counter correctness is covered separately by real ledger/game tests; this
    isolates thresholds, skips, score calculation and permanent confirmation.
    """
    service = progression(rig)
    current = {}
    monkeypatch.setattr(
        service,
        "_measure",
        lambda c, account, achievement, cache: current.get(achievement.family, 0),
    )
    credential = rig.credentials["Archer"]
    score = 0
    for achievement in ACHIEVEMENTS:
        current[achievement.family] = achievement.threshold - 1
        below = service.confirm(credential, achievement.family)
        assert not items_of(below)[achievement.id]["unlocked"]
        assert below["summary"]["earned_points"] == score
        current[achievement.family] += 1
        at = service.confirm(credential, achievement.family)
        score += achievement.points
        assert items_of(at)[achievement.id]["unlocked"]
        assert at["summary"]["earned_points"] == score
        assert service.confirm(credential, achievement.family)["summary"]["earned_points"] == score
    current.clear()
    final = service.confirm(credential)
    assert final["summary"]["earned_points"] == final["summary"]["total_points"] == 6290
    assert final["summary"]["claimable_points"] == 0
    assert all(
        family["complete"] and family["next_milestone"] is None for family in final["families"]
    )


def test_skipping_tiers_and_concurrent_selected_claims(rig):
    from concurrent.futures import ThreadPoolExecutor

    service = progression(rig)
    for _ in range(10):
        finish(rig, active(rig), "attackers")
    credential = rig.credentials["Archer"]
    before = service.achievements(credential)
    family = next(f for f in before["families"] if f["id"] == "rated-wins")
    assert family["collected_tier"] == 0
    assert family["claimable_points"] == 60
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: service.confirm(credential, "rated-wins"), range(12)))
    assert all(result["summary"]["earned_points"] == 60 for result in results)
    result = results[-1]
    assert {item["id"] for item in result["items"] if item["unlocked"]} == {
        "first-rated-win",
        "rated-wins-3",
        "rated-wins-10",
    }
    family = next(f for f in result["families"] if f["id"] == "rated-wins")
    assert family["collected_tier"] == 3
    assert family["collected_threshold"] == 10
    assert family["next_milestone"]["threshold"] == 25
    assert family["next_milestone"]["points"] == 40
    assert result["summary"]["claimable_points"] > 0  # unrelated families stay unclaimed
    assert service.confirm(credential)["summary"]["earned_points"] > 60


def test_selected_claim_api_rejects_arbitrary_progress_and_family(web):
    _, client = web
    for body in (
        {"family_id": "unknown"},
        {"progress": 1000},
        {"points": 1000},
        {"account_id": "other"},
    ):
        assert client.post("/api/personal/achievements/confirm", json=body).status_code == 422
    assert (
        client.post(
            "/api/personal/achievements/confirm", json={"family_id": "rated-wins"}
        ).status_code
        == 200
    )


def test_replay_analysis_receipts_require_success_completion_and_owner(rig):
    store = rig.store
    account = rig.accounts["Archer"]
    unfinished = persist_game(store, account)
    completed = persist_game(store, account, winner="defenders")
    report = {"evaluations": [{"agent_id": "advisor", "source": "alpha_beta"}]}
    # Views, partial or failed searches and playing-agent evaluations don't count.
    for invalid in (
        None,
        {**report, "partial": True},
        {"evaluations": [{"agent_id": "advisor", "source": "unavailable"}]},
        {"evaluations": [{"agent_id": "attackers", "source": "alpha_beta"}]},
    ):
        store.record_replay_analysis(completed, invalid, account["id"])
    store.record_replay_analysis(unfinished, report, account["id"])
    store.record_replay_analysis(completed, report, rig.accounts["Keeper"]["id"])
    service = progression(rig)
    assert (
        items_of(service.achievements(rig.credentials["Archer"]))["analyzed-replays-1"]["progress"]
        == 0
    )
    for _ in range(5):
        store.record_replay_analysis(completed, report, account["id"])
    assert (
        items_of(service.achievements(rig.credentials["Archer"]))["analyzed-replays-1"]["progress"]
        == 1
    )
    # The same string ID in the online category is a distinct replay identity.
    online = finish(rig, active(rig), "attackers")
    with store.connection() as c:
        c.execute(
            "INSERT INTO replay_analysis_completions VALUES(?,?,?)",
            (account["id"], "local", online["id"]),
        )
    store.record_replay_analysis(
        {"id": online["id"], "online": True, "recorded_result": {"winner": "attackers"}},
        report,
        account["id"],
    )
    assert (
        items_of(service.achievements(rig.credentials["Archer"]))["analyzed-replays-3"]["progress"]
        == 3
    )


def test_opening_hall_uses_bounded_batched_counter_queries(rig):
    queries = []
    with rig.store.connection() as c:
        c.set_trace_callback(queries.append)
        result = progression(rig)._projection(c, rig.accounts["Archer"]["id"], 1)
    assert len(result["items"]) == 77
    assert len(result["families"]) == 10
    selects = [q for q in queries if q.lstrip().upper().startswith("SELECT")]
    assert len(selects) < 20  # independent of the 77 milestone count
    assert not any(q.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for q in queries)


def test_replay_completion_rechecks_a_game_undone_while_analysis_runs(rig):
    game = persist_game(rig.store, rig.accounts["Archer"], winner="defenders")
    with rig.store.connection() as c:
        c.execute(
            "UPDATE games SET data=json_set(data,'$.recorded_result',NULL) WHERE id=?",
            (game["id"],),
        )
    rig.store.record_replay_analysis(
        game,
        {"evaluations": [{"agent_id": "advisor", "source": "heuristic"}]},
        rig.accounts["Archer"]["id"],
    )
    assert (
        items_of(progression(rig).achievements(rig.credentials["Archer"]))["analyzed-replays-1"][
            "progress"
        ]
        == 0
    )


@pytest.mark.parametrize("mode", ["simultaneous", "sequential"])
def test_two_leg_roles_and_thresholds_use_each_finalized_game(rig, mode):
    from uuid import uuid4

    first = rig.core.create(
        rig.credentials["Archer"], "brandubh", "attackers", 60, uuid4().hex, mode=mode
    )["game"]
    first = rig.core.mutate(rig.credentials["Keeper"], first["id"], 0, "join")["game"]
    first = finish(rig, first, "attackers")
    second = rig.core.read(rig.credentials["Archer"], first["match"]["games"][1]["id"])
    finish(rig, second, "defenders")  # Same account wins the side-swapped return.
    service = progression(rig)
    result = service.confirm(rig.credentials["Archer"])
    items = items_of(result)
    assert items["first-rated-win"]["progress"] == 2
    assert items["brandubh-initiate"]["progress"] == 2
    assert items["attacker-wins-1"]["progress"] == 1
    assert items["defender-hold"]["progress"] == 1
    assert items["rated-win-streak-1"]["progress"] == 2
    assert not items["rated-wins-3"]["unlocked"]
    assert result["summary"]["earned_points"] == 50
    assert service.confirm(rig.credentials["Archer"])["summary"] == result["summary"]


@pytest.mark.parametrize("ruleset", ["brandubh", "tablut", "copenhagen"])
def test_each_ruleset_and_actual_defender_seat_are_counted(rig, ruleset):
    finish(rig, active(rig, ruleset), "defenders")
    items = items_of(progression(rig).achievements(rig.credentials["Keeper"]))
    assert items["defender-hold"]["progress"] == 1
    assert items["attacker-wins-1"]["progress"] == 0
    for pool in ("brandubh", "tablut", "copenhagen"):
        assert items[f"{pool}-initiate"]["progress"] == int(pool == ruleset)
