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
    assert payload["catalog_version"] == 1
    assert payload["portrait_catalog_version"] == 1
    assert [item["id"] for item in payload["items"]] == [
        achievement.id for achievement in ACHIEVEMENTS
    ]
    assert all(
        item["progress"] == 0 and item["unlocked"] is False and item["awarded_at"] is None
        for item in payload["items"]
    )
    assert payload["summary"] == {"unlocked": 0, "total": len(ACHIEVEMENTS)}
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
        assert c.execute("PRAGMA user_version").fetchone()[0] == 18
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
