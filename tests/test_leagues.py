"""Real SQLite queue races and shared rated-online authority, no browser or sleep."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

import pytest

from hnefatafl.leagues import Leagues
from hnefatafl.online_core import OnlineError
from test_online_core import account_template as account_template, rig as rig


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


def finish_league_game(service, rig, loser="Archer"):
    from hnefatafl.engine import legal_moves
    from test_online_core import actor

    join(service, rig, "Archer")
    matched = join(service, rig, "Keeper")
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
        c.execute("INSERT INTO league_settlement_queue VALUES(?,0,0,NULL)", (terminal["id"],))
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
    from datetime import datetime, timezone

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
    from datetime import datetime, timezone

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
