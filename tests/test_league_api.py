"""Real League request, session, SQLite and lifespan integration."""

import fcntl
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from hnefatafl import league_administration, league_worker
from hnefatafl.online_core import micros
from hnefatafl.server import create_app
from test_leagues import seed_waiter
from test_online_api import api as api
from test_online_core import POLICY, account_template as account_template, move_for, rig as rig

LEAGUES = "/api/leagues"
ONLINE_GAMES = "/api/online/games"


def join(client, ruleset="brandubh", request_id=None):
    return client.post(
        LEAGUES + "/queue", json={"ruleset": ruleset, "request_id": request_id or str(uuid4())}
    )


def checked(response):
    assert response.is_success, response.text
    assert response.headers["cache-control"] == "no-store"
    return response.json()


def finish(client, rig, winner="Archer", loser="Keeper", ruleset="brandubh"):
    winner_client, loser_client = client(winner), client(loser)
    assert checked(join(winner_client, ruleset))["status"] == "queued"
    matched = checked(join(loser_client, ruleset))
    assert matched["status"] == "matched"
    game_id = matched["game_id"]
    href = ONLINE_GAMES + "/" + game_id
    assert checked(winner_client.get(LEAGUES + "/active-game"))["active_game"]["game_id"] == game_id
    game = checked(winner_client.get(href))
    if game["participants"][game["position"]["turn"]]["account_id"] != rig.accounts[loser]["id"]:
        move = move_for(game)
        game = checked(
            winner_client.post(href + "/moves", json={"version": game["version"], **move.to_dict()})
        )["game"]
    return checked(loser_client.post(href + "/concede", json={"version": game["version"]}))["game"]


def test_current_policy_and_personal_empty_states_do_not_invent_history(rig, api):
    _, client = api
    player = client()
    current = checked(player.get(LEAGUES + "/current?ruleset=brandubh"))
    assert current["queue"]["status"] == "idle"
    assert current["queue"]["server_time"] == current["server_time"]
    assert current["season"]["status"] == "current" and not current["season"]["stored"]
    assert current["progress"]["tier"] is None and current["progress"]["rank"] is None
    assert current["progress"]["played"] == 0
    assert [tier["minimum_points"] for tier in current["policy"]["tiers"]] == [0, 6, 15, 30, 50]
    assert current["policy"]["clock_seconds"] == 600 and current["policy"]["increment_seconds"] == 5
    assert current["policy"]["request_limits"] == {
        "rolling_window_seconds": 60,
        "per_rolling_window": 10,
        "per_utc_day": 100,
        "exact_retries_count": False,
    }
    assert checked(player.get(LEAGUES + "/standings?season=2027-01"))["items"] == []
    assert checked(player.get(LEAGUES + "/seasons")) == {"items": [], "next_cursor": None}
    assert (
        player.get(LEAGUES + "/progress?season=2026-12").json()["code"] == "league_season_missing"
    )
    with rig.store.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM league_seasons").fetchone()[0] == 0


def test_auth_default_deny_strict_queries_and_protected_mutations(rig, api):
    app, client = api
    player = client()
    for route in ("current", "history", "seasons", "queue", "active-game", "admin/health"):
        assert TestClient(app).get(f"{LEAGUES}/{route}").status_code == 401
    assert player.get(LEAGUES + "/unknown").status_code in {403, 404}
    assert player.delete(LEAGUES + "/queue").status_code in {403, 404}
    assert player.get(LEAGUES + "/admin/health").status_code == 403
    assert player.get(LEAGUES + "/admin/audit").status_code == 403
    assert player.post(LEAGUES + "/admin/matchmaking", json={}).status_code == 403

    for query in (
        "ruleset=unsupported",
        "season=2027-13",
        "limit=0",
        "limit=51",
        "account_id=private-fixture-sentinel",
        "cursor=" + "a" * 513,
    ):
        response = player.get(LEAGUES + "/standings?" + query)
        assert response.status_code == 422
        assert response.json()["code"] == "league_invalid_request"
        assert "private-fixture-sentinel" not in response.text
    response = player.get(LEAGUES + "/history?cursor=malformed")
    assert response.status_code == 422 and response.json()["code"] == "league_invalid_cursor"
    for route in ("queue", "active-game"):
        assert player.get(f"{LEAGUES}/{route}?account_id=other").status_code == 422
    assert client("Odin").get(LEAGUES + "/admin/health?account_id=other").status_code == 422

    payload = {"ruleset": "brandubh", "request_id": str(uuid4())}
    for override in ({"Origin": "https://other.example.test"}, {"X-CSRF-Token": "invalid"}):
        assert player.post(LEAGUES + "/queue", json=payload, headers=override).status_code == 403
    del player.headers["Origin"]
    assert player.post(LEAGUES + "/queue", json=payload).status_code == 403
    player.headers["Origin"] = "http://testserver"
    assert player.post(LEAGUES + "/queue", data=payload).status_code == 403
    assert player.post(LEAGUES + "/queue", json={**payload, "extra": "x" * 8500}).status_code == 413
    for invalid in (
        {**payload, "winner": "private-fixture-sentinel"},
        {**payload, "request_id": False},
    ):
        response = player.post(LEAGUES + "/queue", json=invalid)
        assert response.status_code == 422 and "private-fixture-sentinel" not in response.text
    with rig.store.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM league_requests").fetchone()[0] == 0


def test_eligibility_and_revoked_or_expired_sessions(rig, api):
    _, client = api
    archer, keeper, visitor = client(), client("Keeper"), client("Visitor")
    with rig.store.connection() as c:
        c.execute(
            "UPDATE accounts SET email_verified_at=NULL WHERE id=?", (rig.accounts["Archer"]["id"],)
        )
        c.execute(
            "UPDATE sessions SET expires=0 WHERE account_id=?", (rig.accounts["Keeper"]["id"],)
        )
    assert not checked(archer.get(LEAGUES + "/queue"))["eligible"]
    assert join(archer).status_code == 403
    assert keeper.get(LEAGUES + "/queue").status_code == 401
    checked(visitor.post("/api/auth/logout"))
    assert visitor.get(LEAGUES + "/history").status_code == 401
    with rig.store.connection() as c:
        c.execute("UPDATE accounts SET active=0 WHERE id=?", (rig.accounts["Archer"]["id"],))
    assert archer.get(LEAGUES + "/current").status_code == 401


def test_queue_retries_flooding_and_cancellation_are_account_scoped(api):
    _, client = api
    archer, keeper = client(), client("Keeper")
    entry = checked(join(archer))
    request_id = entry["request_id"]
    assert (
        keeper.post(LEAGUES + "/queue/cancel", json={"request_id": request_id}).status_code == 404
    )
    assert join(keeper, request_id=request_id).status_code == 409
    for _ in range(9):
        assert checked(join(archer))["queue"]["request_id"] == request_id
    limited = join(archer)
    assert limited.status_code == 429 and limited.json()["code"] == "league_rate_limited"
    assert checked(join(archer, request_id=request_id))["status"] == "queued"
    assert (
        checked(archer.post(LEAGUES + "/queue/cancel", json={"request_id": request_id}))["status"]
        == "cancelled"
    )
    assert checked(keeper.get(LEAGUES + "/queue"))["status"] == "idle"


def test_delayed_join_cannot_replace_a_newer_generation_or_cross_account(rig, api):
    _, client = api
    archer, keeper = client(), client("Keeper")
    path = LEAGUES + "/queue"
    old = {"ruleset": "brandubh", "request_id": str(uuid4()), "expected_generation": None}
    newest = {**old, "request_id": str(uuid4()), "ruleset": "tablut"}
    assert checked(archer.get(path))["join_generation"] is None
    assert checked(archer.post(path, json=newest))["join_generation"] == newest["request_id"]
    assert archer.post(path, json=old).status_code == 409
    assert archer.post(path + "/cancel", json={"request_id": old["request_id"]}).status_code == 404
    cancelled = checked(archer.post(path + "/cancel", json={"request_id": newest["request_id"]}))
    assert cancelled["status"] == "cancelled"
    assert archer.post(path, json=old).status_code == 409
    assert checked(archer.post(path, json=newest))["status"] == "cancelled"

    # Guard order follows accepted writes even if the server clock has stepped back.
    rig.clock.value -= timedelta(seconds=1)
    intentional = {**old, "request_id": str(uuid4()), "expected_generation": newest["request_id"]}
    state = checked(archer.post(path, json=intentional))
    assert state["queue"]["request_id"] == state["join_generation"] == intentional["request_id"]
    assert archer.post(path, json=old).status_code == 409
    assert checked(keeper.get(path))["join_generation"] is None
    headers = {"X-League-Account": rig.accounts["Archer"]["id"]}
    for endpoint in ("queue", "current", "progress", "history"):
        response = keeper.get(f"{LEAGUES}/{endpoint}", headers=headers)
        assert response.status_code == 409
        assert response.json()["code"] == "league_session_changed"
        assert intentional["request_id"] not in response.text


def test_real_scoring_shared_ranks_archived_revisions_and_safe_history(rig, api):
    app, client = api
    archer, keeper, admin = client(), client("Keeper"), client("Odin")
    with rig.store.connection() as c:
        c.execute(
            "UPDATE accounts SET display_name=?,city=?,profile_private_fields=? WHERE id=?",
            (
                "Private Display Mark",
                "Private City Mark",
                '["display_name","city"]',
                rig.accounts["Keeper"]["id"],
            ),
        )
    games = []
    for winner, loser in [("Archer", "Keeper")] * 3 + [("Visitor", "Odin")] * 3:
        game = finish(client, rig, winner, loser)
        games.append(game)
        if len(games) == 1:
            before = checked(archer.get(LEAGUES + "/history?ruleset=brandubh"))["items"][0]
            assert (before["result"], before["state"], before["game_status"]) == (
                "win",
                "pending",
                "completed",
            )
            assert before["replay_href"] == before["game_href"] + "/review"
        assert app.state.leagues.drain()["settled"] == 1
        rig.clock.value += timedelta(seconds=1)
    rules = "ruleset=brandubh"
    progress = checked(archer.get(LEAGUES + "/current?" + rules))["progress"]
    assert (progress["rank"], progress["points"], progress["tier"], progress["previous_tier"]) == (
        1,
        9,
        "nidavellir",
        "midgard",
    )
    assert checked(keeper.get(LEAGUES + "/progress?" + rules))["progress"]["rank"] == 3
    assert checked(archer.get(LEAGUES + "/progress?ruleset=copenhagen"))["progress"]["played"] == 0
    assert checked(archer.get("/api/online/stats"))["wins"] == 3
    assert checked(archer.get(LEAGUES + "/active-game")) == {"active_game": None}

    standings_url = LEAGUES + "/standings"
    filters = {"ruleset": "brandubh", "season": "2027-01", "limit": 1}
    first = checked(archer.get(standings_url, params=filters))
    second = checked(archer.get(standings_url, params={**filters, "cursor": first["next_cursor"]}))
    third = checked(archer.get(standings_url, params={**filters, "cursor": second["next_cursor"]}))
    assert [page["items"][0]["rank"] for page in (first, second, third)] == [1, 1, 3]
    cursor = first["next_cursor"]
    tampered = ("A" if cursor[0] != "A" else "B") + cursor[1:]
    assert (
        archer.get(standings_url, params={**filters, "cursor": tampered}).json()["code"]
        == "league_invalid_cursor"
    )
    assert {page["items"][0]["player"]["id"] for page in (first, second)} == {
        rig.accounts["Archer"]["id"],
        rig.accounts["Visitor"]["id"],
    }
    assert first["season"]["stored"]
    history_path = LEAGUES + "/history"
    own = archer.get(history_path, params=filters)
    item = checked(own)["items"][0]
    assert item["opponent"]["id"] == rig.accounts["Keeper"]["id"]
    assert item["opponent"]["display_name"] == rig.accounts["Keeper"]["username"]
    assert item["opponent"]["portrait_id"]
    for secret in (
        "Private Display Mark",
        "Private City Mark",
        "@example.test",
        "scrypt",
        rig.tokens["Keeper"],
    ):
        assert secret not in own.text
    own_cursor = own.json()["next_cursor"]
    assert (
        checked(archer.get(history_path, params={**filters, "cursor": own_cursor}))["items"][0][
            "game_id"
        ]
        != item["game_id"]
    )
    assert (
        keeper.get(history_path, params={**filters, "cursor": own_cursor}).json()["code"]
        == "league_invalid_cursor"
    )

    rig.clock.value = rig.clock.value.replace(month=2, day=1, hour=0)
    archived = checked(archer.get(LEAGUES + "/seasons"))["items"][0]
    assert (archived["id"], archived["status"]) == ("2027-01", "provisional")
    assert checked(archer.get(LEAGUES + "/current?" + rules))["progress"]["played"] == 0
    rig.clock.value += timedelta(days=1)
    assert checked(archer.get(standings_url, params=filters))["season"]["status"] == "finalized"
    game = games[0]
    private_note = "Private correction note fixture"
    checked(
        admin.post(
            f"{ONLINE_GAMES}/{game['id']}/void",
            json={"version": game["version"], "note": private_note},
        )
    )
    app.state.leagues.drain()
    stale = archer.get(standings_url, params={**filters, "cursor": first["next_cursor"]})
    assert stale.status_code == 409 and stale.json()["code"] == "league_page_changed"
    updated = checked(archer.get(standings_url, params=filters))
    assert updated["season"]["revision"] > archived["revision"]
    response = archer.get(LEAGUES + "/history?" + rules + "&season=2027-01")
    withdrawn = next(row for row in checked(response)["items"] if row["game_id"] == game["id"])
    assert withdrawn["state"] == "withdrawn" and withdrawn["corrected"]
    assert withdrawn["result"] is withdrawn["replay_href"] is None
    assert private_note not in response.text


def test_administration_version_checks_stale_only_and_private_immutable_audit(rig, api):
    _, client = api
    admin, player = client("Odin"), client()
    path = LEAGUES + "/admin"
    request_id = checked(join(player))["request_id"]
    settings = checked(admin.get(path + "/health"))["settings"]
    reason = "Private operational wording"
    pause = {"version": settings["version"], "paused": True, "reason": reason}
    bad_reason = admin.post(path + "/matchmaking", json={**pause, "reason": "   "})
    assert bad_reason.status_code == 422
    assert (
        admin.post(
            path + "/matchmaking", json={**pause, "version": str(settings["version"])}
        ).status_code
        == 422
    )
    next_version = checked(admin.post(path + "/matchmaking", json=pause))["version"]
    stale = admin.post(path + "/matchmaking", json=pause)
    assert stale.status_code == 409 and stale.json()["code"] == "league_version_changed"
    assert checked(player.get(LEAGUES + "/queue"))["paused"]
    body = {"request_id": request_id, "reason": reason}
    active = admin.post(path + "/queue/cancel", json=body)
    assert active.status_code == 409 and active.json()["code"] == "league_queue_not_stale"

    rig.clock.value += timedelta(minutes=10)
    health = checked(admin.get(path + "/health"))
    assert (
        health["queue"]["expired"] == 1 and health["queue"]["stale"][0]["request_id"] == request_id
    )
    assert checked(admin.post(path + "/queue/cancel", json=body))["status"] == "expired"
    assert admin.post(path + "/queue/cancel", json=body).status_code == 409
    assert (
        checked(
            admin.post(
                path + "/matchmaking", json={**pause, "paused": False, "version": next_version}
            )
        )["paused"]
        is False
    )
    first = checked(admin.get(path + "/audit?limit=1"))
    second = checked(
        admin.get(path + "/audit", params={"limit": 1, "cursor": first["next_cursor"]})
    )
    assert [result["items"][0]["action"] for result in (first, second)] == [
        "resume_matchmaking",
        "cancel_stale_queue",
    ]
    assert first["items"][0]["reason"] == reason
    assert reason not in player.get(LEAGUES + "/current").text
    with pytest.raises(sqlite3.IntegrityError), rig.store.connection() as c:
        c.execute("UPDATE league_audit SET reason='replacement'")
    with pytest.raises(sqlite3.IntegrityError), rig.store.connection() as c:
        c.execute("DELETE FROM league_audit")


def test_stale_queue_health_seeks_past_eligible_accounts_and_reports_policy_reasons(rig, api):
    _, client = api
    admin = client("Odin")
    at = micros(rig.clock.value)
    with rig.core._write() as c:
        expired = seed_waiter(c, "Expired", 1500, at - 601_000_000, at - 1)
        for index in range(league_administration.STALE_SCAN + 4):
            seed_waiter(c, f"Eligible-{index}", 1500, at, at + 600_000_000)
        entries = {
            "disabled": seed_waiter(c, "Disabled", 1500, at, at + 600_000_000),
            "unverified": seed_waiter(c, "Unverified", 1500, at, at + 600_000_000),
            "ineligible": seed_waiter(c, "Ineligible", 1500, at, at + 600_000_000),
        }
        c.execute("UPDATE accounts SET active=0 WHERE id='Disabled'")
        c.execute("UPDATE accounts SET email_verified_at=NULL WHERE id='Unverified'")
        c.execute("UPDATE accounts SET role='no-play' WHERE id='Ineligible'")
    path = LEAGUES + "/admin/health"
    first = checked(admin.get(path))["queue"]
    assert [(row["reason"], row["request_id"]) for row in first["stale"]] == [("expired", expired)]
    assert first["stale_next_cursor"]
    page = checked(admin.get(path, params={"stale_cursor": first["stale_next_cursor"]}))["queue"]
    assert {row["reason"]: row["request_id"] for row in page["stale"]} == entries
    assert page["stale_next_cursor"] is None
    assert all(
        set(row) == {"request_id", "ruleset", "joined_at", "expires_at", "reason"}
        for row in page["stale"]
    )
    for request in entries.values():
        response = admin.post(
            LEAGUES + "/admin/queue/cancel", json={"request_id": request, "reason": "Test removal"}
        )
        assert checked(response)["status"] == "cancelled"


def test_administrative_cancel_and_concurrent_replacement_target_different_generations(rig, api):
    _, client = api
    archer, admin = client(), client("Odin")
    old = checked(join(archer))["request_id"]
    rig.clock.value += timedelta(minutes=10)
    replacement = str(uuid4())
    with ThreadPoolExecutor(max_workers=2) as workers:
        cancel = workers.submit(
            admin.post,
            LEAGUES + "/admin/queue/cancel",
            json={"request_id": old, "reason": "Test expiry race"},
        )
        rejoin = workers.submit(join, archer, "tablut", replacement)
        assert checked(rejoin.result())["queue"]["request_id"] == replacement
        outcome = cancel.result()
        if outcome.is_success:
            assert checked(outcome)["status"] == "expired"
        else:
            assert outcome.status_code == 409
            assert outcome.json()["code"] == "league_version_changed"
    assert checked(archer.get(LEAGUES + "/queue"))["queue"]["request_id"] == replacement


def test_rebuild_repairs_corruption_resumes_after_restart_and_catches_late_void(rig, api):
    app, client = api
    admin, player = client("Odin"), client()
    first = finish(client, rig)
    assert app.state.leagues.drain()["settled"] == 1
    second = finish(client, rig, winner="Keeper", loser="Archer")
    assert app.state.leagues.drain()["settled"] == 1
    rules = "ruleset=brandubh"
    current_path = LEAGUES + "/current?" + rules
    season = checked(player.get(current_path))["season"]
    with rig.store.connection() as c:
        c.execute("DELETE FROM league_contributions WHERE game_id=?", (first["id"],))
        c.execute("UPDATE league_standings SET points=99")
    endpoint = f"{LEAGUES}/admin/seasons/{season['id']}/rebuild"
    payload = {"version": season["revision"], "reason": "Repair disposable test projections"}
    assert (
        admin.post(endpoint, json={**payload, "version": payload["version"] - 1}).status_code == 409
    )
    response = admin.post(endpoint, json=payload)
    assert response.status_code == 202 and checked(response)["season"]["status"] == "rebuilding"
    assert admin.post(endpoint, json=payload).json()["code"] == "league_rebuilding"
    assert checked(player.get(current_path))["progress"] is None
    assert checked(player.get(LEAGUES + "/standings?" + rules))["items"] == []

    administration = app.state.league_administration
    for _ in range(20):
        assert administration.process_rebuild(limit=1) == "processed"
        if (
            administration.health(rig.credentials["Odin"])["rebuilds"][0]["stage"]
            == "fold_standings"
        ):
            break
    else:
        pytest.fail("The rebuild did not reach the fold phase")
    checked(
        admin.post(
            f"{ONLINE_GAMES}/{second['id']}/void",
            json={"version": second["version"], "note": "Official late test void"},
        )
    )
    rig.clock.value += timedelta(seconds=10)
    assert app.state.leagues.drain()["settled"] == 0
    pending = checked(admin.get(LEAGUES + "/admin/health"))["settlement"]
    assert pending == {
        "pending": 1,
        "failed": 0,
        "oldest_pending_seconds": 10,
        "max_attempts": 0,
        "next_retry_at": None,
    }
    restarted = create_app(
        rig.root,
        online_settings=POLICY,
        online_clock=rig.clock,
        online_background=False,
        account_background=False,
    )
    recovery = restarted.state.league_administration
    for _ in range(20):
        if recovery.process_rebuild(limit=1) == "idle":
            break
    else:
        pytest.fail("The durable rebuild did not complete")
    after = restarted.state.league_views.current(rig.credentials["Archer"], "brandubh")
    assert (
        after["season"]["status"] == "current" and after["season"]["revision"] > season["revision"]
    )
    assert after["progress"]["points"] == 3 and after["progress"]["played"] == 1
    assert recovery.health(rig.credentials["Odin"])["settlement"]["pending"] == 0
    with rig.store.connection() as c:
        assert [
            row[0] for row in c.execute("SELECT action FROM league_audit ORDER BY sequence")
        ] == ["request_rebuild", "complete_rebuild"]
        assert (
            c.execute(
                "SELECT COUNT(*) FROM league_contributions WHERE game_id=?", (first["id"],)
            ).fetchone()[0]
            == 2
        )


def test_failed_rebuild_batch_rolls_back_and_retries_durably(rig, api, monkeypatch, caplog):
    app, client = api
    game = finish(client, rig)
    admin = client("Odin")
    endpoint = LEAGUES + "/admin/seasons/2027-01/rebuild"
    assert (
        admin.post(endpoint, json={"version": 0, "reason": "Test rebuild rollback"}).status_code
        == 202
    )
    administration = app.state.league_administration
    credential = rig.credentials["Odin"]
    assert administration.process_rebuild(limit=1) == "processed"
    assert administration.process_rebuild(limit=1) == "processed"
    assert administration.health(credential)["rebuilds"][0]["stage"] == "restore_games"
    original = league_administration.write_game_result

    def fail_after_write(c, result):
        original(c, result)
        raise RuntimeError("private failure fixture")

    with monkeypatch.context() as patch:
        patch.setattr(league_administration, "write_game_result", fail_after_write)
        assert administration.process_rebuild(limit=1) == "retry"
    with rig.store.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM league_contributions").fetchone()[0] == 0
        row = c.execute(
            "SELECT state,result_revision FROM league_games WHERE game_id=?", (game["id"],)
        ).fetchone()
        assert tuple(row) == ("pending", None)
        assert c.execute("SELECT COUNT(*) FROM league_settlement_queue").fetchone()[0] == 1
    assert "private failure fixture" not in caplog.text
    assert administration.health(credential)["rebuilds"][0]["attempts"] == 1
    assert administration.process_rebuild(limit=1) == "idle"
    rig.clock.value += timedelta(seconds=2)
    for _ in range(12):
        if administration.process_rebuild(limit=1) == "idle":
            break
    assert administration.health(credential)["rebuilds"] == []
    assert administration.health(credential)["settlement"]["pending"] == 0


def wait_for(predicate, seconds=5):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.025)
    pytest.fail("The lifespan worker did not finish the expected cycle")


def test_lifespan_recovers_durable_work_and_matches_without_browser(rig, api, monkeypatch):
    _, client = api
    completed = finish(client, rig)
    # Keep a separate ruleset waiting while the first game awaits accounting.
    with rig.store.connection() as c:
        c.execute(
            """INSERT INTO player_ratings(
            account_id,pool,rating,rated_games,wins,losses,draws,policy_version,updated_at
        ) VALUES(?,'tablut',1750,0,0,0,0,1,?)""",
            (rig.accounts["Odin"]["id"], micros(rig.clock.value)),
        )
    assert checked(join(client("Visitor"), "tablut"))["status"] == "queued"
    assert checked(join(client("Odin"), "tablut"))["status"] == "queued"
    rig.clock.value += timedelta(minutes=2)
    restored = create_app(
        rig.root, online_settings=POLICY, online_clock=rig.clock, account_background=False
    )
    worker = restored.state.league_worker
    monkeypatch.setattr(league_worker, "POLL_SECONDS", 0.05)

    with restored.state.auth.gate_path.open("rb") as gate:
        fcntl.flock(gate, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with TestClient(restored):
            worker.start()
            current_thread = worker.thread
            wait_for(lambda: worker.health()["last_error"] == "deployment_locked")
            assert worker.thread is current_thread
            with rig.store.connection() as c:
                assert (
                    c.execute(
                        "SELECT attempts FROM league_settlement_queue WHERE game_id=?",
                        (completed["id"],),
                    ).fetchone()[0]
                    == 0
                )
            fcntl.flock(gate, fcntl.LOCK_UN)
            wait_for(
                lambda: (
                    restored.state.leagues.queue_status(rig.credentials["Visitor"])["active_game"]
                    is not None
                )
            )
            wait_for(lambda: worker.health()["last_success_at"] is not None)
            with rig.store.connection() as c:
                assert c.execute("SELECT COUNT(*) FROM league_settlement_queue").fetchone()[0] == 0
                assert (
                    c.execute(
                        "SELECT COUNT(*) FROM rating_events WHERE game_id=?", (completed["id"],)
                    ).fetchone()[0]
                    == 1
                )
    assert worker.thread is not None and not worker.thread.is_alive()


def test_worker_health_separates_idle_success_from_durable_retry(rig, api, monkeypatch):
    app, client = api
    admin = client("Odin")
    worker = app.state.league_worker
    monkeypatch.setattr(league_worker, "POLL_SECONDS", 0.15)
    path = LEAGUES + "/admin/health"
    original = worker.core.settle

    def fail(*_args):
        raise RuntimeError("private injected query or exception")

    worker.start()
    try:
        wait_for(lambda: worker.health()["state"] == "idle")
        assert worker.health()["last_success_at"] is None
        monkeypatch.setattr(worker.core, "settle", fail)
        finish(client, rig)
        wait_for(lambda: worker.health()["state"] == "settlement_retry")
        health = checked(admin.get(path))
        assert health["worker"]["last_success_at"] is None
        assert health["settlement"]["pending"] == health["settlement"]["failed"] == 1
        assert health["settlement"]["max_attempts"] == 1
        assert health["settlement"]["next_retry_at"]
        assert "private injected" not in str(health)
    finally:
        worker.shutdown()
    monkeypatch.setattr(worker.core, "settle", original)
    worker.start()
    try:
        wait_for(lambda: worker.health()["state"] == "idle")
        assert checked(admin.get(path))["settlement"]["failed"] == 1
        rig.clock.value += timedelta(seconds=2)
        wait_for(lambda: worker.health()["last_success_at"] is not None)
        assert checked(admin.get(path))["settlement"]["pending"] == 0
    finally:
        worker.shutdown()


def test_worker_reports_rebuild_stage_and_repeated_retries_across_restart(rig, api, monkeypatch):
    app, client = api
    finish(client, rig)
    admin = client("Odin")
    path = LEAGUES + "/admin/health"
    endpoint = LEAGUES + "/admin/seasons/2027-01/rebuild"
    assert (
        admin.post(endpoint, json={"version": 0, "reason": "Private test reason"}).status_code
        == 202
    )
    worker = app.state.league_worker
    monkeypatch.setattr(league_worker, "POLL_SECONDS", 0.15)

    def fail(*_args):
        raise RuntimeError("private rebuild exception")

    monkeypatch.setattr(worker.administration, "_clear", fail)
    for attempts in (1, 2):
        worker.start()
        try:
            wait_for(lambda: worker.health()["state"] == "rebuild_retry")
            health = checked(admin.get(path))
            assert health["worker"]["last_success_at"] is None
            assert health["rebuild_pending"] == health["rebuild_retries"] == 1
            assert health["rebuilds"][0]["attempts"] == attempts
            assert health["rebuilds"][0]["stage"] == "clear_contributions"
            assert health["rebuilds"][0]["due_at"]
            assert "Private test reason" not in str(health)
            assert "private rebuild exception" not in str(health)
        finally:
            worker.shutdown()
        rig.clock.value += timedelta(seconds=2**attempts)
