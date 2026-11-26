"""Real SQLite queue races and shared rated-online authority, no browser or sleep."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import fcntl
import random
from threading import Event
from uuid import uuid4

import pytest

from hnefatafl import league_policy as policy, league_results, leagues
from hnefatafl.leagues import MATCH_OPPONENTS, MATCH_STARTERS, Leagues
from hnefatafl.online_core import OnlineError, micros
from test_online_core import (
    account_template as account_template,
    active,
    actor,
    move_for,
    rig as rig,
)


def join(service, rig, name, ruleset="brandubh", request=None):
    return service.join(rig.credentials[name], ruleset, request or str(uuid4()))


def test_queue_is_explicit_ruleset_aware_and_never_creates_fake_opponents(rig):
    service = Leagues(rig.core)
    assert service.queue_status(rig.credentials["Archer"])["status"] == "idle"
    queued = join(service, rig, "Archer")
    assert queued["status"] == "queued"
    assert join(service, rig, "Keeper", "tablut")["status"] == "queued"
    with rig.store.connection() as c:
        assert c.execute("SELECT count(*) FROM online_games").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM league_queue").fetchone()[0] == 2
    with pytest.raises(OnlineError) as error:
        join(service, rig, "Archer", "tablut")
    assert error.value.status == 409
    assert service.cancel(rig.credentials["Archer"], queued["request_id"])["status"] == "cancelled"
    assert service.cancel(rig.credentials["Archer"], queued["request_id"])["status"] == "cancelled"


def test_matching_uses_the_existing_rated_online_game_clock_and_start_ratings(rig):
    service = Leagues(rig.core)
    request = str(uuid4())
    join(service, rig, "Archer", request=request)
    matched = join(service, rig, "Keeper")
    assert matched["status"] == "matched"
    game = rig.core.read(rig.credentials["Archer"], matched["game_id"])
    assert game["status"] == "active"
    assert game["rated"] is True
    assert game["time_control"] == "total"
    assert game["time_per_move"] == 600
    assert game["increment_seconds"] == 5
    assert join(service, rig, "Archer", request=request)["game_id"] == game["id"]
    assert service.cancel(rig.credentials["Archer"], request)["game_id"] == game["id"]
    with rig.store.connection() as c:
        assert c.execute("SELECT count(*) FROM online_games").fetchone()[0] == 1
        assert c.execute("SELECT count(*) FROM online_game_start_ratings").fetchone()[0] == 2
        assert c.execute("SELECT count(*) FROM league_active_players").fetchone()[0] == 2
        assert c.execute("SELECT count(*) FROM league_queue").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM rating_events").fetchone()[0] == 0


def test_same_request_concurrently_queues_once_and_cannot_cross_accounts(rig):
    service = Leagues(rig.core)
    request = str(uuid4())
    with ThreadPoolExecutor(max_workers=2) as workers:
        responses = list(
            workers.map(lambda _: join(service, rig, "Archer", request=request), range(2))
        )
    assert all(row["status"] == "queued" for row in responses)
    with rig.store.connection() as c:
        assert c.execute("SELECT count(*) FROM league_queue").fetchone()[0] == 1
        assert c.execute("SELECT count(*) FROM league_requests").fetchone()[0] == 1
    with pytest.raises(OnlineError) as error:
        join(service, rig, "Keeper", request=request)
    assert error.value.status == 409
    with pytest.raises(OnlineError) as error:
        join(service, rig, "Archer", "tablut", request=request)
    assert error.value.status == 409


def test_simultaneous_opponents_cannot_double_seat_a_waiting_player(rig):
    service = Leagues(rig.core)
    join(service, rig, "Archer")
    with ThreadPoolExecutor(max_workers=2) as workers:
        list(workers.map(lambda name: join(service, rig, name), ["Keeper", "Visitor"]))
    with rig.store.connection() as c:
        assert c.execute("SELECT count(*) FROM online_games").fetchone()[0] == 1
        assert c.execute("SELECT count(*) FROM league_active_players").fetchone()[0] == 2
        assert c.execute("SELECT count(*) FROM league_queue").fetchone()[0] == 1
        assert c.execute("PRAGMA foreign_key_check").fetchall() == []


def test_expiry_does_not_extend_on_retry_and_new_request_can_reenter(rig):
    service = Leagues(rig.core)
    request = str(uuid4())
    first = join(service, rig, "Archer", request=request)
    rig.clock.value += timedelta(seconds=600)
    assert service.queue_status(rig.credentials["Archer"])["status"] == "expired"
    assert service.drain()["expired"] == 1
    recovered = Leagues(rig.core).queue_status(rig.credentials["Archer"])
    assert recovered["status"] == "expired" and recovered["queue"] is None
    assert recovered["request_id"] == request
    repeated = join(service, rig, "Archer", request=request)
    assert repeated["status"] == "expired"
    assert repeated["expires_at"] == first["expires_at"]
    newer = join(service, rig, "Archer")
    assert newer["status"] == "queued"
    assert newer["expires_at"] != first["expires_at"]
    assert join(service, rig, "Archer", request=request)["status"] == "expired"


def test_unverified_or_inactive_accounts_cannot_queue_or_keep_a_waiting_seat(rig):
    service = Leagues(rig.core)
    join(service, rig, "Archer")
    with rig.store.connection() as c:
        c.execute("UPDATE accounts SET active=0 WHERE id=?", (rig.accounts["Archer"]["id"],))
        c.execute(
            "UPDATE accounts SET email_verified_at=NULL WHERE id=?",
            (rig.accounts["Visitor"]["id"],),
        )
    with pytest.raises(OnlineError) as error:
        join(service, rig, "Visitor")
    assert error.value.status == 403
    assert join(service, rig, "Keeper")["status"] == "queued"
    with rig.store.connection() as c:
        assert c.execute("SELECT count(*) FROM online_games").fetchone()[0] == 0
        assert (
            c.execute("SELECT account_id FROM league_queue").fetchone()[0]
            == rig.accounts["Keeper"]["id"]
        )


def test_join_request_rate_limits_do_not_break_retries(rig):
    service = Leagues(rig.core)
    first = str(uuid4())
    join(service, rig, "Archer", request=first)
    for _ in range(9):
        join(service, rig, "Archer")
    with pytest.raises(OnlineError) as error:
        join(service, rig, "Archer")
    assert error.value.status == 429
    assert join(service, rig, "Archer", request=first)["status"] == "queued"


def test_cancel_and_match_commit_one_coherent_outcome(rig):
    service = Leagues(rig.core)
    queued = join(service, rig, "Archer")
    with ThreadPoolExecutor(max_workers=2) as workers:
        cancel = workers.submit(service.cancel, rig.credentials["Archer"], queued["request_id"])
        match = workers.submit(join, service, rig, "Keeper")
        cancelled, joined = cancel.result(), match.result()
    with rig.store.connection() as c:
        active = c.execute("SELECT count(*) FROM league_active_players").fetchone()[0]
        games = c.execute("SELECT count(*) FROM online_games").fetchone()[0]
        if active:
            assert active == 2 and games == 1
            assert cancelled["game_id"] == joined["game_id"]
        else:
            assert games == 0
            assert cancelled["status"] == "cancelled"
            assert joined["status"] == "queued"


def finish_league_game(service, rig, loser="Archer", order=("Archer", "Keeper")):
    from hnefatafl.engine import legal_moves

    join(service, rig, order[0])
    matched = join(service, rig, order[1])
    game = rig.core.read(rig.credentials["Archer"], matched["game_id"])
    if actor(rig, game) != rig.credentials[loser]:
        with rig.store.connection() as c:
            _, domain = rig.core._load(c, game["id"])
        move = legal_moves(domain.position)[0]
        game = rig.core.mutate(
            actor(rig, game),
            game["id"],
            game["version"],
            "move",
            source=move.source,
            target=move.target,
        )["game"]
    return rig.core.mutate(rig.credentials[loser], game["id"], game["version"], "concede")["game"]


def test_official_results_enqueue_once_and_settle_without_browser(rig):
    service = Leagues(rig.core)
    terminal = finish_league_game(service, rig)
    with rig.store.connection() as c:
        assert c.execute("SELECT count(*) FROM league_settlement_queue").fetchone()[0] == 1
        assert c.execute("SELECT count(*) FROM league_contributions").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM rating_events").fetchone()[0] == 1
    # A fresh service simulates restart: durable work belongs to SQLite, not UI.
    restarted = Leagues(rig.core)
    assert restarted.drain()["settled"] == 1
    assert restarted.drain()["settled"] == 0
    with rig.store.connection() as c:
        assert c.execute("SELECT count(*) FROM league_contributions").fetchone()[0] == 2
        points = dict(c.execute("SELECT account_id,points FROM league_standings"))
        assert points[rig.accounts["Archer"]["id"]] == 0
        assert points[rig.accounts["Keeper"]["id"]] == 3
        assert c.execute("SELECT count(*) FROM league_active_players").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM rating_events").fetchone()[0] == 1
        before = c.execute("SELECT revision FROM league_seasons").fetchone()[0]
        c.execute(
            """INSERT INTO league_settlement_queue(game_id,season_id,due_at,pending_since)
            SELECT game_id,season_id,0,0 FROM league_games WHERE game_id=?""",
            (terminal["id"],),
        )
    assert restarted.drain()["settled"] == 1
    with rig.store.connection() as c:
        assert c.execute("SELECT revision FROM league_seasons").fetchone()[0] == before
        assert c.execute("SELECT count(*) FROM league_contributions").fetchone()[0] == 2


def test_void_replays_season_floor_and_does_not_reopen_pair_farming(rig):
    service = Leagues(rig.core)
    first = finish_league_game(service, rig, "Archer")
    service.drain()
    second = finish_league_game(service, rig, "Keeper")
    service.drain()
    finish_league_game(service, rig, "Keeper")
    service.drain()
    with rig.store.connection() as c:
        before = dict(c.execute("SELECT account_id,points FROM league_standings"))
    assert before[rig.accounts["Archer"]["id"]] == 6
    assert before[rig.accounts["Keeper"]["id"]] == 1
    rig.core.mutate(
        rig.credentials["Odin"], first["id"], first["version"], "void", note="Fixture correction"
    )
    service.drain()
    with rig.store.connection() as c:
        corrected = dict(c.execute("SELECT account_id,points FROM league_standings"))
    assert corrected[rig.accounts["Archer"]["id"]] == 6
    assert corrected[rig.accounts["Keeper"]["id"]] == 0
    rig.core.mutate(
        rig.credentials["Odin"],
        second["id"],
        second["version"],
        "void",
        note="Second fixture correction",
    )
    service.drain()
    with rig.store.connection() as c:
        assert (
            c.execute(
                "SELECT points FROM league_standings WHERE account_id=?",
                (rig.accounts["Archer"]["id"],),
            ).fetchone()[0]
            == 3
        )
        assert c.execute("SELECT count(*) FROM league_contributions").fetchone()[0] == 2
    assert join(service, rig, "Archer")["status"] == "queued"
    assert join(service, rig, "Keeper")["status"] == "queued"
    with rig.store.connection() as c:
        assert c.execute("SELECT count(*) FROM league_games").fetchone()[0] == 3
    unrelated = join(service, rig, "Visitor")
    assert unrelated["status"] == "matched"
    assert rig.accounts["Visitor"]["id"] in {
        seat["account_id"]
        for seat in rig.core.read(rig.credentials["Visitor"], unrelated["game_id"])[
            "participants"
        ].values()
    }


def test_league_maintenance_pauses_new_matches_without_changing_games(rig):
    service = Leagues(rig.core)
    with rig.store.connection() as c:
        c.execute("UPDATE league_settings SET paused=1")
    with pytest.raises(OnlineError) as error:
        join(service, rig, "Archer")
    assert error.value.status == 503
    assert service.drain()["matched"] == 0
    with rig.store.connection() as c:
        assert c.execute("SELECT count(*) FROM online_games").fetchone()[0] == 0


def test_replayed_cancellation_cannot_cancel_a_new_queue_or_other_account(rig):
    service = Leagues(rig.core)
    first = join(service, rig, "Archer")
    with pytest.raises(OnlineError) as error:
        service.cancel(rig.credentials["Keeper"], first["request_id"])
    assert error.value.status == 404
    assert service.cancel(rig.credentials["Archer"], first["request_id"])["status"] == "cancelled"
    second = join(service, rig, "Archer")
    assert second["request_id"] != first["request_id"]
    assert service.cancel(rig.credentials["Archer"], first["request_id"])["status"] == "cancelled"
    assert service.queue_status(rig.credentials["Archer"])["request_id"] == second["request_id"]


def test_queue_expires_at_utc_season_boundary(rig):
    service = Leagues(rig.core)
    rig.clock.value = datetime(2027, 1, 31, 23, 59, 30, tzinfo=timezone.utc)
    queued = join(service, rig, "Archer")
    rig.clock.value += timedelta(seconds=30)
    assert service.queue_status(rig.credentials["Archer"])["status"] == "expired"
    assert join(service, rig, "Keeper")["status"] == "queued"
    assert join(service, rig, "Archer", request=queued["request_id"])["status"] == "expired"
    assert join(service, rig, "Archer")["status"] == "matched"
    with rig.store.connection() as c:
        assert c.execute("SELECT season_id FROM league_games").fetchone()[0] == "2027-02"


def test_rolling_minute_request_limit_survives_midnight(rig):
    service = Leagues(rig.core)
    rig.clock.value = datetime(2027, 1, 2, 23, 59, 45, tzinfo=timezone.utc)
    for _ in range(10):
        join(service, rig, "Archer")
    rig.clock.value += timedelta(seconds=15)
    with pytest.raises(OnlineError) as error:
        join(service, rig, "Archer")
    assert error.value.status == 429
    rig.clock.value += timedelta(seconds=46)
    assert join(service, rig, "Archer")["status"] == "queued"


def set_rating(c, account_id, value, ruleset="brandubh"):
    c.execute(
        """INSERT INTO player_ratings VALUES(?,?,?,0,0,0,0,1,0)
        ON CONFLICT(account_id,pool) DO UPDATE SET rating=excluded.rating""",
        (account_id, ruleset, value),
    )


def seed_waiter(c, name, rating, joined, expires, ruleset="brandubh"):
    request = str(uuid4())
    c.execute(
        """INSERT INTO accounts(id,username,email,password_hash,role,created_at,email_verified_at)
        VALUES(?,?,?,'fixture','USER',0,1)""",
        (name, name, f"{name}@example.test"),
    )
    set_rating(c, name, rating, ruleset)
    c.execute(
        """INSERT INTO league_requests(id,account_id,ruleset,created_at,status,entry_id,joined_at,expires_at)
        VALUES(?,?,?,?,'queued',?,?,?)""",
        (request, name, ruleset, joined, request, joined, expires),
    )
    c.execute(
        """INSERT INTO league_queue(account_id,request_id,ruleset,joined_at,expires_at,rating)
        VALUES(?,?,?,?,?,?)""",
        (name, request, ruleset, joined, expires, rating),
    )
    return request


def test_opponent_cursor_reaches_deep_pages_and_rechecks_expanding_windows(rig):
    service = Leagues(rig.core)
    now = rig.clock.value
    at = micros(now)
    cutoff = micros(policy.season_for(now).start)
    expires = at + 400_000_000
    with rig.core._write() as c:
        seed_waiter(c, "Oldest", 1500.0, at - 121_000_000, expires)
        for index in range(MATCH_OPPONENTS * 2 + 1):
            seed_waiter(c, f"Window-{index:02d}", 1750.0, at - 1000 + index, expires)
        seed_waiter(c, "Deep", 1500.0, at, expires)
    for previous in (f"Window-{MATCH_OPPONENTS - 1:02d}", f"Window-{MATCH_OPPONENTS * 2 - 1:02d}"):
        with rig.core._write() as c:
            first = service._valid_entry(c, "Oldest", "brandubh", at, cutoff)
            discovery = service._match_from(c, first, now, at, cutoff, at)
            assert not discovery.matched and discovery.more
            assert service._queue_entry(c, "Oldest")["opponent_account"] == previous
        service = Leagues(rig.core)  # The per-entry cursor survives a restart.
    with rig.core._write() as c:
        first = service._valid_entry(c, "Oldest", "brandubh", at, cutoff)
        assert service._match_from(c, first, now, at, cutoff, at).matched
        assert {row[0] for row in c.execute("SELECT account_id FROM league_active_players")} == {
            "Oldest",
            "Deep",
        }

    # Skipped opponents can become eligible when their own window expands.
    with rig.core._write() as c:
        seed_waiter(c, "NextOldest", 1500.0, at - 121_000_000, expires)
        first = service._valid_entry(c, "NextOldest", "brandubh", at, cutoff)
        assert not service._match_from(c, first, now, at, cutoff, at).matched
    now += timedelta(seconds=61)
    at = micros(now)
    with rig.core._write() as c:
        for _ in range(4):
            first = service._valid_entry(c, "NextOldest", "brandubh", at, cutoff)
            if service._match_from(c, first, now, at, cutoff, at).matched:
                break
        else:
            pytest.fail("A newly compatible opponent was never revisited")


def test_background_budget_commits_separately_and_rotates_rulesets(rig, monkeypatch):
    service = Leagues(rig.core)
    game = active(rig)
    move = move_for(game)
    at = micros(rig.clock.value)
    monkeypatch.setattr(leagues, "MATCH_TRANSACTIONS", 4)
    monkeypatch.setattr(leagues, "MATCH_CYCLE_SECONDS", 5)
    with rig.core._write() as c:
        for ruleset in ("brandubh", "tablut", "copenhagen"):
            for index in range(80):
                seed_waiter(
                    c, f"{ruleset}-{index:02d}", 1500, at + index, at + 600_000_000, ruleset
                )
    rig.clock.value += timedelta(seconds=1)
    stop = Event()
    waited = stop.wait
    moved = None

    def handoff(seconds):
        nonlocal moved
        if moved is None:
            with rig.store.connection() as c:
                assert c.execute("SELECT count(*) FROM league_games").fetchone()[0] == 1
            moved = foreground.submit(
                rig.core.mutate,
                actor(rig, game),
                game["id"],
                game["version"],
                "move",
                source=move.source,
                target=move.target,
            ).result(timeout=5)["game"]
        return waited(seconds)

    monkeypatch.setattr(stop, "wait", handoff)
    with ThreadPoolExecutor(max_workers=1) as foreground:
        assert service.drain(stop=stop)["matched"] == 4
    assert moved["version"] == game["version"] + 1
    with rig.store.connection() as c:
        assert dict(c.execute("SELECT ruleset,count(*) FROM league_games GROUP BY ruleset")) == {
            "brandubh": 2,
            "copenhagen": 1,
            "tablut": 1,
        }
        assert set(
            row[0]
            for row in c.execute("SELECT first_account FROM league_games WHERE ruleset='brandubh'")
        ) == {"brandubh-00", "brandubh-02"}
    stop.set()
    assert service.drain(stop=stop)["matched"] == 0


def test_only_completed_unsuccessful_sweeps_back_off_and_expiry_continues(rig, monkeypatch):
    service = Leagues(rig.core)
    at = micros(rig.clock.value)
    ticks = 100.0
    monkeypatch.setattr(leagues, "monotonic", lambda: ticks)
    monkeypatch.setattr(leagues, "MATCH_TRANSACTIONS", 4)
    with rig.core._write() as c:
        for index in range(35):
            seed_waiter(
                c, f"Disjoint-{index}", 1500 + 1000 * index, at - 300_000_000, at + 300_000_000
            )
    with monkeypatch.context() as patch:
        calls = []
        original = service._match

        def observe(*args):
            calls.append(1)
            return original(*args)

        patch.setattr(service, "_match", observe)
        stop = Event()
        assert service.drain(stop=stop)["matched"] == 0
        assert len(calls) == 4
        service.drain(stop=stop)
        assert len(calls) == 5  # The unfinished sweep continues without a delay.
        service.drain(stop=stop)
        assert len(calls) == 5
        ticks += 1
        service.drain(stop=stop)
        service.drain(stop=stop)
        assert len(calls) == 10
        scanned = len(calls)
        ticks += 1
        rig.clock.value += timedelta(minutes=10)
        assert service.drain(stop=stop)["expired"] == 20
        assert len(calls) == scanned


def test_sparse_tails_get_a_turn_across_rulesets_while_arrivals_and_cancellations_continue(
    rig, monkeypatch
):
    service = Leagues(rig.core)
    at = micros(rig.clock.value)
    monkeypatch.setattr(leagues, "MATCH_TRANSACTIONS", 3)
    monkeypatch.setattr(leagues, "monotonic", lambda: 100.0)
    pools = ("brandubh", "copenhagen", "tablut")
    with rig.core._write() as c:
        for ruleset in pools:
            for index in range(97):
                seed_waiter(
                    c,
                    f"{ruleset}-older-{index:03d}",
                    10_000 + 1000 * index,
                    at,
                    at + 600_000_000,
                    ruleset,
                )
            for index in range(2):
                seed_waiter(c, f"{ruleset}-tail-{index}", 1500, at + 1, at + 600_000_001, ruleset)
        expired = seed_waiter(c, "Expired before discovery", 1500, at - 600_000_000, at, "brandubh")

    stop = Event()
    matched = 0
    previous = None
    for index in range(13):
        rig.clock.value += timedelta(seconds=1)
        now = micros(rig.clock.value)
        with rig.core._write() as c:
            if previous is not None:
                service._cancel(c, f"Arrival-{index - 1}", "cancelled", previous)
            previous = seed_waiter(
                c, f"Arrival-{index}", 2750, now, now + 600_000_000, pools[index % 3]
            )
        matched += service.drain(stop=stop)["matched"]
    assert matched == 3
    rig.clock.value += timedelta(minutes=10)
    service.drain(stop=stop)
    with rig.store.connection() as c:
        games = {
            tuple(row)
            for row in c.execute("SELECT first_account,second_account,ruleset FROM league_games")
        }
        assert games == {(f"{ruleset}-tail-0", f"{ruleset}-tail-1", ruleset) for ruleset in pools}
        assert (
            c.execute("SELECT status FROM league_requests WHERE id=?", (expired,)).fetchone()[0]
            == "expired"
        )
        assert (
            c.execute("SELECT count(*) FROM league_requests WHERE status='matched'").fetchone()[0]
            == 6
        )


def test_window_opening_and_sqlite_busy_do_not_trigger_unsuccessful_backoff(rig, monkeypatch):
    service = Leagues(rig.core)
    at = micros(rig.clock.value)
    ticks = 100.0
    monkeypatch.setattr(leagues, "monotonic", lambda: ticks)
    with rig.core._write() as c:
        seed_waiter(c, "Older", 1500, at - 59_000_000, at + 541_000_000)
        seed_waiter(c, "Younger", 1750, at - 58_000_000, at + 542_000_000)
    stop = Event()
    assert service.drain(stop=stop)["matched"] == 0
    for _ in range(2):
        ticks += 1
        rig.clock.value += timedelta(seconds=1)
        with rig.store.connection() as blocker:
            blocker.execute("BEGIN IMMEDIATE")
            assert service.drain(stop=stop)["matched"] == 0
    assert service.drain(stop=stop)["matched"] == 1


def test_round_robin_finds_compatible_players_beyond_old_candidate_batch(rig):
    service = Leagues(rig.core)
    at = micros(rig.clock.value)
    with rig.core._write() as c:
        for index in range(40):
            name = f"Early-{index:02d}"
            request = str(uuid4())
            rating = 3000.0 + 1000 * index
            joined = at - 1000000 + index
            expires = at + 599000000
            c.execute(
                """INSERT INTO accounts(id,username,email,password_hash,role,created_at,email_verified_at)
                VALUES(?,?,?,'fixture','USER',0,1)""",
                (name, name, f"{name}@example.test"),
            )
            set_rating(c, name, rating)
            c.execute(
                """INSERT INTO league_requests(id,account_id,ruleset,created_at,status,entry_id,joined_at,expires_at)
                VALUES(?,?,'brandubh',?,'queued',?,?,?)""",
                (request, name, joined, request, joined, expires),
            )
            c.execute(
                """INSERT INTO league_queue(account_id,request_id,ruleset,joined_at,expires_at,rating)
                VALUES(?,?,'brandubh',?,?,?)""",
                (name, request, joined, expires, rating),
            )
    join(service, rig, "Archer")
    join(service, rig, "Keeper")
    # Continue the on-disk cursor using a newly started service.
    restarted = Leagues(rig.core)
    for _ in range((42 // MATCH_STARTERS) + 1):
        restarted.drain()
        active = restarted.queue_status(rig.credentials["Keeper"])["active_game"]
        if active:
            break
    assert active == restarted.queue_status(rig.credentials["Archer"])["active_game"]
    assert active is not None
    with rig.store.connection() as c:
        assert c.execute("SELECT count(*) FROM league_queue").fetchone()[0] == 40
        assert c.execute("SELECT count(*) FROM league_games").fetchone()[0] == 1


def test_rating_change_and_both_windows_gate_automatic_matching(rig):
    service = Leagues(rig.core)
    keeper = rig.accounts["Keeper"]["id"]
    with rig.core._write() as c:
        set_rating(c, keeper, 2101.0)
    join(service, rig, "Archer")
    rig.clock.value += timedelta(minutes=4)
    assert join(service, rig, "Keeper")["status"] == "queued"
    rig.clock.value += timedelta(minutes=4)
    assert service.drain()["matched"] == 0  # Both max windows reject a 601 Elo gap.
    with rig.core._write() as c:
        set_rating(c, keeper, 2100.0)
        assert (
            c.execute("SELECT rating FROM league_queue WHERE account_id=?", (keeper,)).fetchone()[0]
            == 2100.0
        )
    assert service.drain()["matched"] == 1


def test_younger_entry_must_wait_for_its_own_window(rig):
    service = Leagues(rig.core)
    with rig.core._write() as c:
        set_rating(c, rig.accounts["Keeper"]["id"], 1800.0)
    join(service, rig, "Archer")
    rig.clock.value += timedelta(minutes=4)
    assert join(service, rig, "Keeper")["rating_window"] == 200
    rig.clock.value += timedelta(seconds=60, microseconds=-1)
    assert service.drain()["matched"] == 0
    rig.clock.value += timedelta(microseconds=1)
    assert service.drain()["matched"] == 1


def test_side_balance_persists_for_repeated_opponents_across_day_and_queue_order(rig):
    service = Leagues(rig.core)
    rig.clock.value = datetime(2027, 1, 2, 23, 59, 58, tzinfo=timezone.utc)
    first = finish_league_game(service, rig)
    rig.clock.value += timedelta(seconds=3)
    second = finish_league_game(service, rig, order=("Keeper", "Archer"))
    first_seats = {side: player["account_id"] for side, player in first["participants"].items()}
    second_seats = {side: player["account_id"] for side, player in second["participants"].items()}
    assert first_seats["attackers"] == second_seats["defenders"]
    assert first_seats["defenders"] == second_seats["attackers"]
    with rig.store.connection() as c:
        assert [row[0] for row in c.execute("SELECT balance FROM league_side_balance")] == [0, 0]
        # The canonical online creator is the same in both matches, regardless
        # of which participant entered the queue first.
        assert c.execute("SELECT COUNT(DISTINCT creator_id) FROM online_games").fetchone()[0] == 1


def test_exact_requests_and_cancel_aliases_remain_bound_to_the_original_entry(rig):
    service = Leagues(rig.core)
    first = join(service, rig, "Archer")
    alias = join(service, rig, "Archer")
    assert alias["request_id"] != first["request_id"]
    assert alias["queue"]["request_id"] == first["request_id"]
    assert alias["expires_at"] == first["expires_at"]
    assert service.cancel(rig.credentials["Archer"], alias["request_id"])["status"] == "cancelled"
    newer = join(service, rig, "Archer")
    for previous in (first, alias):
        replay = join(service, rig, "Archer", request=previous["request_id"])
        assert replay["status"] == "cancelled"
        assert replay["queue"]["request_id"] == newer["request_id"]
        assert (
            service.cancel(rig.credentials["Archer"], previous["request_id"])["status"]
            == "cancelled"
        )


def test_old_match_retries_are_distinct_from_actual_active_game_and_live_queue(rig):
    service = Leagues(rig.core)
    first = join(service, rig, "Archer")
    terminal = finish_league_game(service, rig)
    assert service.queue_status(rig.credentials["Archer"])["status"] == "idle"
    new_queue = join(service, rig, "Archer", "tablut")
    assert new_queue["status"] == "queued"
    old_result = service.cancel(rig.credentials["Archer"], first["request_id"])
    assert old_result["status"] == "matched"
    assert old_result["game_id"] == terminal["id"]
    assert old_result["active_game"] is None
    assert old_result["queue"]["request_id"] == new_queue["request_id"]
    newer = join(service, rig, "Visitor", "tablut")
    old_retry = join(service, rig, "Archer", request=first["request_id"])
    assert old_retry["game_id"] == terminal["id"]
    assert old_retry["active_game"]["game_id"] == newer["game_id"]
    assert join(service, rig, "Archer", request=first["request_id"])["queue"] is None


def test_pause_and_policy_change_allow_exact_retry_but_reconcile_invalid_entry(rig):
    service = Leagues(rig.core)
    queued = join(service, rig, "Archer")
    rig.core.settings = replace(rig.core.settings, creation_policy_approved=False)
    with rig.store.connection() as c:
        c.execute("UPDATE league_settings SET paused=1")
        c.execute(
            "UPDATE accounts SET email_verified_at=NULL WHERE id=?", (rig.accounts["Archer"]["id"],)
        )
    replay = join(service, rig, "Archer", request=queued["request_id"])
    assert replay["status"] == "cancelled"
    assert replay["paused"] and not replay["eligible"] and not replay["enabled"]
    assert service.drain()["matched"] == 0
    retry = join(service, rig, "Archer", request=queued["request_id"])
    assert retry["status"] == "cancelled" and retry["queue"] is None


def test_rate_limit_resets_at_midnight_without_forgetting_old_request(rig):
    service = Leagues(rig.core)
    rig.clock.value = datetime(2027, 1, 3, 23, 20, tzinfo=timezone.utc)
    for minute in range(10):
        for number in range(10):
            current = join(service, rig, "Archer")
            if minute == number == 0:
                first_request = current["request_id"]
        rig.clock.value += timedelta(minutes=1)
    with pytest.raises(OnlineError) as error:
        join(service, rig, "Archer")
    assert error.value.status == 429
    rig.clock.value = datetime(2027, 1, 4, tzinfo=timezone.utc)
    new = join(service, rig, "Archer")
    replay = join(service, rig, "Archer", request=first_request)
    assert replay["status"] == "expired" and replay["queue"]["request_id"] == new["request_id"]


def test_deployment_lock_defers_work_without_attempts(rig):
    service = Leagues(rig.core)
    terminal = finish_league_game(service, rig)
    with rig.auth.gate_path.open("rb") as gate:
        fcntl.flock(gate, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert service.drain() == {
            "settled": 0,
            "expired": 0,
            "reconciled": 0,
            "matched": 0,
            "deferred": True,
            "settlement_retries": 0,
            "achievements": 0,
            "achievement_retries": 0,
        }
    with rig.store.connection() as c:
        assert tuple(
            c.execute(
                "SELECT attempts,last_error FROM league_settlement_queue WHERE game_id=?",
                (terminal["id"],),
            ).fetchone()
        ) == (0, None)
    assert service.drain()["settled"] == 1


def test_failed_matching_rolls_back_seats_game_provenance_and_balances(rig, monkeypatch):
    service = Leagues(rig.core)
    with rig.core._write() as c:
        set_rating(c, rig.accounts["Keeper"]["id"], 2300.0)
    join(service, rig, "Archer")
    join(service, rig, "Keeper")
    with rig.core._write() as c:
        set_rating(c, rig.accounts["Keeper"]["id"], 1500.0)
    original = service._seat

    def fail_after_seating(c, first, second, now):
        original(c, first, second, now)
        raise OnlineError(503, "fixture")

    monkeypatch.setattr(service, "_seat", fail_after_seating)
    with pytest.raises(OnlineError, match="fixture"):
        service.drain()
    with rig.store.connection() as c:
        assert c.execute("SELECT count(*) FROM league_queue").fetchone()[0] == 2
        for table in (
            "online_games",
            "league_games",
            "league_active_players",
            "league_side_balance",
        ):
            assert c.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
    monkeypatch.setattr(service, "_seat", original)
    assert service.drain()["matched"] == 1


def test_failed_settlement_rolls_back_partial_writes_and_retries(rig, monkeypatch):
    service = Leagues(rig.core)
    terminal = finish_league_game(service, rig)
    original = service.settle

    def fail_after_projecting(c, game_id, now):
        original(c, game_id, now)
        raise RuntimeError("private fixture text")

    monkeypatch.setattr(service, "settle", fail_after_projecting)
    failed = service.drain()
    assert failed["settled"] == 0 and failed["settlement_retries"] == 1
    with rig.store.connection() as c:
        assert c.execute("SELECT count(*) FROM league_contributions").fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM league_standings").fetchone()[0] == 0
        assert c.execute("SELECT revision FROM league_seasons").fetchone()[0] == 0
        assert tuple(
            c.execute(
                "SELECT attempts,last_error FROM league_settlement_queue WHERE game_id=?",
                (terminal["id"],),
            ).fetchone()
        ) == (1, "accounting_deferred")
    rig.clock.value += timedelta(seconds=2)
    assert Leagues(rig.core).drain()["settled"] == 1


@pytest.mark.parametrize("delta,state", [(-1, "eligible"), (0, "late"), (1, "late")])
def test_authoritative_completion_time_controls_grace_boundary(rig, delta, state):
    service = Leagues(rig.core)
    rig.clock.value = datetime(2026, 12, 31, 23, 59, tzinfo=timezone.utc)
    season = policy.season_for(rig.clock.value)
    join(service, rig, "Archer")
    game_id = join(service, rig, "Keeper")["game_id"]
    # Supply a synthetic late-game deadline. OnlineCore, not this fixture,
    # records the timeout revision and Elo outcome at that deadline.
    deadline = season.grace_end + timedelta(microseconds=delta)
    with rig.core._write() as c:
        c.execute(
            "UPDATE online_games SET deadline=?,version=version+1 WHERE id=?",
            (micros(deadline), game_id),
        )
    rig.clock.value = season.grace_end + timedelta(seconds=5)
    rig.core.sweep()
    assert service.drain()["settled"] == 1
    with rig.store.connection() as c:
        assert tuple(c.execute("SELECT season_id,state FROM league_games").fetchone()) == (
            "2026-12",
            state,
        )
        assert c.execute("SELECT count(*) FROM rating_events").fetchone()[0] == 1
        assert c.execute("SELECT count(*) FROM league_standings").fetchone()[0] == (
            2 if state == "eligible" else 0
        )
        assert c.execute("SELECT count(*) FROM league_achievement_progress").fetchone()[0] == 0
        assert (
            c.execute(
                """SELECT COUNT(*) FROM achievement_awards a JOIN achievement_catalog c
               ON c.id=a.achievement_id WHERE c.criterion='league_tier'"""
            ).fetchone()[0]
            == 0
        )


def test_projection_matches_full_replay_after_random_appends_reordering_void_and_repair(
    rig, monkeypatch
):
    service = Leagues(rig.core)
    random_source = random.Random(81823)
    outcomes = ["Archer", "Keeper"] * 12
    random_source.shuffle(outcomes)
    replay = league_results.rebuild_player
    fallbacks = []

    def observe(*args):
        fallbacks.append(args[3])
        return replay(*args)

    monkeypatch.setattr(league_results, "rebuild_player", observe)
    terminals = []
    for index, loser in enumerate(outcomes):
        rig.clock.value += timedelta(hours=12)
        terminal = finish_league_game(service, rig, loser)
        terminals.append(terminal)
        if index < 12:
            assert service.drain()["settled"] == 1
    assert fallbacks == []

    def assert_equivalence(check_permanent=False, exact=False):
        with rig.store.connection() as c:
            for row in c.execute("SELECT * FROM league_standings"):
                contributions = [
                    policy.Contribution(*item)
                    for item in c.execute(
                        """SELECT game_id,sequence,outcome FROM league_contributions
                        WHERE season_id=? AND ruleset=? AND account_id=?""",
                        (row["season_id"], row["ruleset"], row["account_id"]),
                    )
                ]
                progress = policy.fold_progress(contributions)
                assert league_results.standing_value(row) == progress.standing
                assert row["last_sequence"] == max(item.sequence for item in contributions)
                if check_permanent:
                    peak = c.execute(
                        """SELECT highest_tier FROM league_achievement_progress
                           WHERE account_id=? AND ruleset=?""",
                        (row["account_id"], row["ruleset"]),
                    ).fetchone()
                    actual = peak[0] if peak is not None else 0
                    expected = policy.tier_rank(progress.highest_tier)
                    assert actual == expected if exact else actual <= expected

    assert_equivalence(check_permanent=True, exact=True)
    shuffled = terminals[12:]
    random_source.shuffle(shuffled)
    for terminal in shuffled:
        with rig.core._write() as c:
            assert service.settle(c, terminal["id"], rig.clock.value)
        # Out-of-order appends with earlier official results still queued may
        # defer a peak. They must never award more than the current fold.
        assert_equivalence(check_permanent=True)
    assert len(fallbacks) >= 2
    service.drain()
    assert_equivalence(check_permanent=True, exact=True)
    old = terminals[3]
    rig.core.mutate(
        rig.credentials["Odin"], old["id"], old["version"], "void", note="Fixture reason"
    )
    assert service.drain()["settled"] > 0
    assert_equivalence()
    missing = terminals[6]["id"]
    with rig.core._write() as c:
        c.execute("DELETE FROM league_contributions WHERE game_id=?", (missing,))
        after_void = len(fallbacks)
        assert service.settle(c, missing, rig.clock.value)
        assert len(fallbacks) == after_void + 2
    assert_equivalence()
