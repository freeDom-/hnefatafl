"""Durable online state: real SQLite transactions, controlled UTC, no sleeps/mail."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import multiprocessing
from pathlib import Path
import sqlite3
import threading
from types import SimpleNamespace
from uuid import uuid4

import pytest

from hnefatafl.auth import Auth, SecuritySettings, digest
from hnefatafl.engine import RULESETS, State, legal_moves
from hnefatafl.online_core import OnlineCore, OnlineError, instant, micros
from hnefatafl.online_migrations import ONLINE_TABLES
from hnefatafl.online_settings import OnlineSettings
from hnefatafl.storage import Store

POLICY = OnlineSettings(
    enabled=True,
    creation_policy_approved=True,
    retain_seat_without_verification=True,
    timeout_policy_approved=True,
)


class Clock:
    def __init__(self):
        self.value = datetime(2027, 1, 2, 3, 4, 5, 123456, tzinfo=timezone.utc)

    def __call__(self):
        return self.value


class OnlineFixture(SimpleNamespace):
    def __repr__(self):
        return "OnlineFixture(isolated)"


@pytest.fixture(scope="module")
def account_template(tmp_path_factory):
    store = Store(tmp_path_factory.mktemp("online-accounts") / "db.sqlite3")
    auth = Auth(store, SecuritySettings())
    accounts, credentials, tokens = {}, {}, {}
    for name in ("Archer", "Keeper", "Visitor", "Odin"):
        accounts[name] = auth.create_account(
            name,
            name.lower() + "@example.test",
            "online-fixture-password",
            "ADMIN" if name == "Odin" else "USER",
        )
        _, token, _ = auth.login(name, "online-fixture-password", "fixture-" + name)
        credentials[name], tokens[name] = digest(token), token
    with store.connection() as c:
        c.execute("UPDATE accounts SET email_verified_at=1")
    return store.path, accounts, credentials, tokens


@pytest.fixture
def rig(tmp_path, account_template):
    template, accounts, credentials, tokens = account_template
    path = tmp_path / "hnefatafl.sqlite3"
    with sqlite3.connect(template) as source, sqlite3.connect(path) as target:
        source.backup(target)
    auth = Auth(Store(path), SecuritySettings())
    clock = Clock()
    core = OnlineCore(auth, POLICY, clock)
    return OnlineFixture(
        core=core,
        auth=auth,
        store=auth.store,
        clock=clock,
        accounts=accounts,
        credentials=credentials,
        tokens=tokens,
        root=tmp_path,
    )


def waiting(rig, rule="brandubh", side="attackers", seconds=60):
    return rig.core.create(rig.credentials["Archer"], rule, side, seconds, uuid4().hex)["game"]


def active(rig, rule="brandubh", side="attackers"):
    game = waiting(rig, rule, side)
    return rig.core.mutate(rig.credentials["Keeper"], game["id"], 0, "join")["game"]


def actor(rig, game):
    identifier = game["participants"][game["position"]["turn"]]["account_id"]
    return next(
        credential
        for name, credential in rig.credentials.items()
        if rig.accounts[name]["id"] == identifier
    )


def move_for(game):
    return legal_moves(State.from_dict(game["position"]))[0]


def dump(rig):
    with rig.store.connection() as c:
        return {
            table: [tuple(row) for row in c.execute(f"SELECT * FROM {table}")]
            for table in ONLINE_TABLES
        }


def resume(rig):
    gate = rig.core.gate(rig.credentials["Odin"])
    return rig.core.set_gate(
        rig.credentials["Odin"], gate["version"], True, "Approved deterministic test policy"
    )


def test_durable_game_uses_engine_first_player_and_fresh_precise_deadlines(rig):
    game = waiting(rig, "tablut")
    assert game["status"] == "waiting" and game["deadline"] is None
    assert game["position"]["turn"] == "defenders"
    rig.clock.value += timedelta(microseconds=7)
    game = rig.core.mutate(rig.credentials["Keeper"], game["id"], 0, "join")["game"]
    assert game["version"] == 1
    assert game["started_at"] == "2027-01-02T03:04:05.123463Z"
    assert game["deadline"] == "2027-01-02T03:05:05.123463Z"
    rig.clock.value += timedelta(seconds=5, microseconds=11)
    move = move_for(game)
    moved = rig.core.mutate(
        actor(rig, game), game["id"], 1, "move", source=move.source, target=move.target
    )["game"]
    assert moved["version"] == 2 and moved["position"]["ply"] == 1
    assert moved["deadline"] == "2027-01-02T03:05:10.123474Z"
    reopened = OnlineCore(Auth(Store(rig.store.path), SecuritySettings()), POLICY, rig.clock)
    assert reopened.read(rig.credentials["Visitor"], game["id"]) == moved
    assert reopened.replay(rig.credentials["Visitor"], game["id"])["validated"]
    with rig.store.connection() as c:
        row = c.execute("SELECT * FROM online_games").fetchone()
        assert (
            type(row["deadline"]) is int and row["deadline"] == micros(rig.clock.value) + 60000000
        )
        assert c.execute("SELECT COUNT(*) FROM online_moves").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM games").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM game_participants").fetchone()[0] == 0


@pytest.mark.parametrize("seconds", [True, False, "60", 60.0, 59, 1209601, None])
def test_duration_is_strict_at_service_and_database_boundary(rig, seconds):
    with pytest.raises(OnlineError):
        waiting(rig, seconds=seconds)
    game = waiting(rig)
    with rig.store.connection() as c:
        row = dict(c.execute("SELECT * FROM online_games WHERE id=?", (game["id"],)).fetchone())
        match_id = "match-" + uuid4().hex
        c.execute(
            "INSERT INTO online_matches SELECT ?,creator_id,NULL,side_selection,mode,ruleset,ruleset_version,time_per_move,created_at,NULL,NULL FROM online_matches WHERE id=?",
            (match_id, row["match_id"]),
        )
        row.update(id="invalid-" + uuid4().hex, creation_request=uuid4().hex, time_per_move=seconds)
        row["match_id"] = match_id
        with pytest.raises(sqlite3.IntegrityError):
            c.execute(
                f"INSERT INTO online_games({','.join(row)}) VALUES({','.join('?' for _ in row)})",
                tuple(row.values()),
            )
        # The identical identity/match/seat fixture accepts a valid duration.
        # This rules out an unrelated uniqueness failure masking a type bug.
        row["time_per_move"] = 60
        c.execute(
            f"INSERT INTO online_games({','.join(row)}) VALUES({','.join('?' for _ in row)})",
            tuple(row.values()),
        )


@pytest.mark.parametrize("seconds", [60, 259200, 1209600])
def test_duration_inclusive_bounds(rig, seconds):
    assert waiting(rig, seconds=seconds)["time_per_move"] == seconds


def test_account_eligibility_creator_recheck_and_fixed_seats(rig):
    game = waiting(rig)
    with pytest.raises(OnlineError, match="current game state"):
        rig.core.mutate(rig.credentials["Archer"], game["id"], 0, "join")
    with rig.store.connection() as c:
        c.execute(
            "UPDATE accounts SET email_verified_at=NULL WHERE id=?", (rig.accounts["Keeper"]["id"],)
        )
    with pytest.raises(OnlineError, match="verified email"):
        rig.core.mutate(rig.credentials["Keeper"], game["id"], 0, "join")
    with rig.store.connection() as c:
        c.execute("UPDATE accounts SET email_verified_at=1")
        c.execute("UPDATE accounts SET active=0 WHERE id=?", (rig.accounts["Archer"]["id"],))
    with pytest.raises(OnlineError, match="Both participants"):
        rig.core.mutate(rig.credentials["Keeper"], game["id"], 0, "join")
    with rig.store.connection() as c:
        c.execute("UPDATE accounts SET active=1")
    game = rig.core.mutate(rig.credentials["Keeper"], game["id"], 0, "join")["game"]
    with rig.store.connection() as c:
        for field, value in (
            ("creator_side", "defenders"),
            ("attackers_id", rig.accounts["Visitor"]["id"]),
            ("ruleset_version", "2"),
            ("time_per_move", 120),
        ):
            with pytest.raises(sqlite3.IntegrityError):
                c.execute(f"UPDATE online_games SET {field}=? WHERE id=?", (value, game["id"]))


@pytest.mark.parametrize("action", ["move", "concede"])
@pytest.mark.parametrize("who", ["Keeper", "Visitor", "Odin"])
def test_wrong_turn_spectator_and_administrator_cannot_play_or_finalize(rig, action, who):
    game = active(rig)
    resume(rig)
    rig.clock.value += timedelta(seconds=61)
    before = dump(rig)
    with pytest.raises(OnlineError) as error:
        rig.core.mutate(
            rig.credentials[who], game["id"], game["version"], action, source=0, target=1
        )
    assert error.value.status == 403 and dump(rig) == before


@pytest.mark.parametrize("action", ["move", "concede"])
@pytest.mark.parametrize("offset", [-1, 0, 1])
def test_authoritative_deadline_boundary_and_late_concession(rig, action, offset):
    game = active(rig)
    resume(rig)
    deadline = rig.clock.value + timedelta(seconds=60)
    rig.clock.value = deadline + timedelta(microseconds=offset)
    move = move_for(game)
    result = rig.core.mutate(
        actor(rig, game), game["id"], 1, action, source=move.source, target=move.target
    )
    updated = result["game"]
    if offset < 0 and action == "move":
        assert result["action_accepted"] and updated["position"]["ply"] == 1
        assert updated["deadline"] == (rig.clock.value + timedelta(seconds=60)).isoformat().replace(
            "+00:00", "Z"
        )
        assert updated["result"] is None
    elif offset < 0:
        assert updated["result"]["termination_reason"] == "concession"
        assert updated["position"]["winner"] is None
        assert updated["result"]["abandoned_account_id"] is None
    else:
        assert result["transition"] == "timeout" and not result["action_accepted"]
        assert updated["position"]["ply"] == 0 and updated["position"]["winner"] is None
        assert updated["result"]["completed_at"] == deadline.isoformat().replace("+00:00", "Z")
        assert updated["result"]["revised_at"] == rig.clock.value.isoformat().replace("+00:00", "Z")
        assert updated["result"]["abandoned_account_id"] == rig.accounts["Archer"]["id"]
        assert rig.core.statistics(rig.credentials["Archer"])["abandoned_games"] == 1
        assert rig.core.statistics(rig.credentials["Keeper"])["abandoned_games"] == 0


def test_pause_gate_covers_sweep_requests_and_restart_without_extensions(rig):
    game = active(rig)
    rig.clock.value += timedelta(seconds=3600)
    for action in ("move", "concede"):
        with pytest.raises(OnlineError, match="paused"):
            rig.core.mutate(actor(rig, game), game["id"], 1, action, source=0, target=1)
    assert rig.core.sweep() == {"examined": 1, "finalized": 0, "blocked": 1}
    restarted = OnlineCore(Auth(Store(rig.store.path), SecuritySettings()), POLICY, rig.clock)
    assert restarted.read(rig.credentials["Archer"], game["id"])["deadline"] == game["deadline"]
    assert restarted.sweep()["finalized"] == 0
    resume(rig)
    assert restarted.sweep()["finalized"] == 1
    assert restarted.sweep()["finalized"] == 0
    terminal = restarted.read(rig.credentials["Archer"], game["id"])
    assert terminal["result"]["completed_at"] == game["deadline"]
    assert terminal["deadline"] is None
    assert rig.core.statistics(rig.credentials["Archer"])["abandoned_games"] == 1


def test_runtime_policy_gate_cannot_be_bypassed_by_persisted_resume(rig):
    game = active(rig)
    resume(rig)
    rig.clock.value += timedelta(seconds=61)
    blocked = OnlineCore(rig.auth, replace(POLICY, timeout_policy_approved=False), rig.clock)
    assert blocked.gate(rig.credentials["Odin"])["effective_enabled"] is False
    assert blocked.sweep()["blocked"] == 1
    with pytest.raises(OnlineError, match="paused"):
        blocked.mutate(actor(rig, game), game["id"], 1, "concede")
    with pytest.raises(OnlineError, match="policy approval"):
        blocked.set_gate(rig.credentials["Odin"], 1, True, "Not an approval bypass")
    assert blocked.set_gate(rig.credentials["Odin"], 1, False, "Incident pause")["enabled"] == 0


def test_concession_void_audit_and_derived_accounting(rig):
    game = active(rig)
    terminal = rig.core.mutate(actor(rig, game), game["id"], 1, "concede")["game"]
    assert terminal["result"]["revision"] == 1
    assert rig.core.statistics(rig.credentials["Archer"])["losses"] == 1
    assert rig.core.statistics(rig.credentials["Archer"])["abandoned_games"] == 0
    with pytest.raises(OnlineError):
        rig.core.mutate(rig.credentials["Visitor"], game["id"], 2, "void", note="private")
    voided = rig.core.mutate(
        rig.credentials["Odin"], game["id"], 2, "void", note="private incident details"
    )["game"]
    assert voided["status"] == "voided" and voided["result"]["revision"] == 2
    assert voided["result"]["supersedes"] == 1 and not voided["result"]["competitive_eligible"]
    assert "private incident" not in json.dumps(voided)
    assert rig.core.statistics(rig.credentials["Archer"])["played"] == 0
    before = dump(rig)
    repeated = rig.core.mutate(
        rig.credentials["Odin"], game["id"], 3, "void", note="different private note"
    )
    assert not repeated["action_accepted"] and repeated["transition"] == "none"
    assert dump(rig) == before
    with rig.store.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM online_results").fetchone()[0] == 2
        assert (
            c.execute("SELECT note FROM online_void_audit").fetchone()[0]
            == "private incident details"
        )
        for table in ("online_results", "online_void_audit", "online_gate_audit"):
            with pytest.raises(sqlite3.IntegrityError):
                c.execute(f"DELETE FROM {table}")


def test_timeout_then_void_removes_abandonment_without_rewriting_old_result(rig):
    game = active(rig)
    resume(rig)
    rig.clock.value += timedelta(seconds=61)
    rig.core.sweep()
    assert rig.core.statistics(rig.credentials["Archer"])["abandoned_games"] == 1
    rig.core.mutate(
        rig.credentials["Odin"], game["id"], 2, "void", note="Approved incident resolution"
    )
    assert rig.core.statistics(rig.credentials["Archer"])["abandoned_games"] == 0
    with rig.store.connection() as c:
        rows = c.execute(
            "SELECT competitive_eligible,abandoned_account_id FROM online_results ORDER BY revision"
        ).fetchall()
    assert tuple(rows[0]) == (1, rig.accounts["Archer"]["id"])
    assert tuple(rows[1]) == (0, None)


@pytest.mark.parametrize("terminal", ["cancel", "void"])
def test_waiting_terminal_states_have_no_result_or_played_line(rig, terminal):
    game = waiting(rig)
    who = "Archer" if terminal == "cancel" else "Odin"
    result = rig.core.mutate(rig.credentials[who], game["id"], 0, terminal, note="No played game")[
        "game"
    ]
    assert result["result"] is None and result["deadline"] is None
    assert rig.core.statistics(rig.credentials["Archer"])["played"] == 0
    with pytest.raises(OnlineError):
        rig.core.mutate(rig.credentials["Keeper"], game["id"], 1, "join")


@pytest.mark.parametrize(
    "table,action",
    [
        ("online_games", "move"),
        ("online_moves", "move"),
        ("online_results", "concede"),
        ("online_void_audit", "void"),
    ],
)
def test_failure_between_state_move_result_and_audit_writes_rolls_back(rig, table, action):
    game = active(rig)
    if action == "void":
        game = rig.core.mutate(actor(rig, game), game["id"], 1, "concede")["game"]
    before = dump(rig)
    operation = "UPDATE" if table == "online_games" else "INSERT"
    with rig.store.connection() as c:
        c.execute(
            f"CREATE TRIGGER injected BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT,'injected failure'); END"
        )
    move = move_for(game)
    who = rig.credentials["Odin"] if action == "void" else actor(rig, game)
    with pytest.raises(sqlite3.IntegrityError):
        rig.core.mutate(
            who,
            game["id"],
            game["version"],
            action,
            source=move.source,
            target=move.target,
            note="Private correction",
        )
    assert dump(rig) == before


def test_lost_response_then_retry_never_commits_another_move_or_result(rig, monkeypatch):
    game = active(rig)
    original = rig.core._write

    @contextmanager
    def lost_response():
        with original() as transaction:
            yield transaction
        raise ConnectionError("simulated response loss after commit")

    move = move_for(game)
    with monkeypatch.context() as patch:
        patch.setattr(rig.core, "_write", lost_response)
        with pytest.raises(ConnectionError):
            rig.core.mutate(
                actor(rig, game), game["id"], 1, "move", source=move.source, target=move.target
            )
    with pytest.raises(OnlineError) as error:
        rig.core.mutate(
            actor(rig, game), game["id"], 1, "move", source=move.source, target=move.target
        )
    assert error.value.status == 409
    current = rig.core.read(rig.credentials["Archer"], game["id"])
    assert current["version"] == 2 and len(current["moves"]) == 1
    with monkeypatch.context() as patch:
        patch.setattr(rig.core, "_write", lost_response)
        with pytest.raises(ConnectionError):
            rig.core.mutate(actor(rig, current), game["id"], 2, "concede")
    with pytest.raises(OnlineError):
        rig.core.mutate(actor(rig, current), game["id"], 2, "concede")
    with rig.store.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM online_results").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM online_moves").fetchone()[0] == 1


def test_create_retry_conflicts_and_listing_recovers_committed_identity(rig):
    request = uuid4().hex
    original = rig.core.create(rig.credentials["Archer"], "brandubh", "attackers", 60, request)
    with pytest.raises(OnlineError, match="already committed"):
        rig.core.create(rig.credentials["Archer"], "brandubh", "attackers", 60, request)
    recovered = rig.core.listing(rig.credentials["Archer"], mine=True)
    assert [g["id"] for g in recovered["games"]] == [original["game"]["id"]]


def test_unsupported_rules_version_fails_before_replay_action_and_sweep(rig, monkeypatch):
    game = active(rig)
    resume(rig)
    rig.clock.value += timedelta(seconds=61)
    before = dump(rig)
    monkeypatch.setitem(RULESETS, "brandubh", replace(RULESETS["brandubh"], version="future"))
    for action in (
        lambda: rig.core.read(rig.credentials["Archer"], game["id"]),
        lambda: rig.core.replay(rig.credentials["Archer"], game["id"]),
        lambda: rig.core.mutate(rig.credentials["Archer"], game["id"], 1, "concede"),
    ):
        with pytest.raises(OnlineError) as error:
            action()
        assert error.value.status == 503
    assert rig.core.sweep()["blocked"] == 1 and dump(rig) == before


def test_backwards_clock_is_fail_closed_for_actions_sweeps_and_gate(rig):
    game = active(rig)
    resume(rig)
    rig.clock.value -= timedelta(microseconds=1)
    with pytest.raises(OnlineError, match="backwards"):
        rig.core.mutate(actor(rig, game), game["id"], 1, "concede")
    with pytest.raises(OnlineError, match="backwards"):
        rig.core.set_gate(rig.credentials["Odin"], 1, False, "Clock incident")
    with pytest.raises(OnlineError):
        micros(datetime(2027, 1, 1))
    assert (
        instant(
            micros(
                game_time := datetime(
                    2027, 1, 1, microsecond=999999, tzinfo=timezone(timedelta(hours=2))
                )
            )
        )
        == game_time
    )


def test_concurrent_same_version_moves_use_independent_connections(rig):
    game = active(rig)
    move = move_for(game)
    barrier = threading.Barrier(2)

    def compete():
        independent = OnlineCore(rig.auth, POLICY, rig.clock)
        barrier.wait(timeout=20)
        try:
            return independent.mutate(
                actor(rig, game), game["id"], 1, "move", source=move.source, target=move.target
            )["transition"]
        except OnlineError as error:
            return error.status

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: compete(), range(2)))
    assert sorted(results, key=str) == [409, "move"]
    current = rig.core.read(rig.credentials["Archer"], game["id"])
    assert current["version"] == 2 and len(current["moves"]) == 1


def _process_race(path, settings, moment, barrier, out, action, credential, game_id, version):
    core = OnlineCore(Auth(Store(Path(path)), SecuritySettings()), settings, lambda: moment)
    coordinates = move_for(core.read(credential, game_id)).to_dict() if action == "move" else {}
    barrier.wait(timeout=30)
    try:
        if action == "sweep":
            out.put(core.sweep()["finalized"])
        else:
            out.put(
                core.mutate(
                    credential,
                    game_id,
                    version,
                    action,
                    note="Private race resolution",
                    **coordinates,
                )["transition"]
            )
    except OnlineError as error:
        out.put(error.status)


def race(rig, game, left, right):
    context = multiprocessing.get_context("spawn")
    barrier, out = context.Barrier(2), context.Queue()
    processes = [
        context.Process(
            target=_process_race,
            args=(
                str(rig.store.path),
                POLICY,
                rig.clock.value,
                barrier,
                out,
                action,
                credential,
                game["id"],
                game["version"],
            ),
        )
        for action, credential in (left, right)
    ]
    for process in processes:
        process.start()
    results = [out.get(timeout=45), out.get(timeout=45)]
    for process in processes:
        process.join(timeout=30)
        assert process.exitcode == 0
    return results


def test_two_process_joins_activate_exactly_two_distinct_seats(rig):
    game = waiting(rig)
    results = race(
        rig, game, ("join", rig.credentials["Keeper"]), ("join", rig.credentials["Visitor"])
    )
    assert sorted(results, key=str) == [409, "join"]
    current = rig.core.read(rig.credentials["Archer"], game["id"])
    assert current["version"] == 1 and current["status"] == "active"
    assert current["participants"]["attackers"] != current["participants"]["defenders"]
    assert current["deadline"] == (rig.clock.value + timedelta(seconds=60)).isoformat().replace(
        "+00:00", "Z"
    )


def test_cancel_versus_join_has_one_committed_transition(rig):
    game = waiting(rig)
    results = race(
        rig, game, ("cancel", rig.credentials["Archer"]), ("join", rig.credentials["Keeper"])
    )
    assert 409 in results and len([r for r in results if isinstance(r, str)]) == 1
    assert rig.core.read(rig.credentials["Archer"], game["id"])["version"] == 1


@pytest.mark.parametrize("contender", ["sweep", "concede", "void"])
def test_independent_process_expiry_races_do_not_duplicate_results(rig, contender):
    game = active(rig)
    resume(rig)
    rig.clock.value += timedelta(seconds=61)
    race(
        rig,
        game,
        ("sweep", ""),
        (contender, rig.credentials["Odin"] if contender == "void" else actor(rig, game)),
    )
    current = rig.core.read(rig.credentials["Archer"], game["id"])
    if current["status"] == "voided":
        assert rig.core.statistics(rig.credentials["Archer"])["abandoned_games"] == 0
    else:
        assert current["result"]["termination_reason"] == "timeout"
        assert rig.core.statistics(rig.credentials["Archer"])["abandoned_games"] == 1
    with rig.store.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM online_results").fetchone()[0] <= 2
        assert c.execute("SELECT COUNT(*) FROM online_moves").fetchone()[0] == 0


@pytest.mark.parametrize("revocation", ["session", "active", "role", "clock"])
def test_permissions_and_time_are_rechecked_after_obtaining_write_lock(
    rig, monkeypatch, revocation
):
    game = active(rig)
    resume(rig)
    pending = threading.Event()
    original = rig.store.connection

    @contextmanager
    def observed_connection():
        with original() as c:
            c.set_trace_callback(
                lambda statement: pending.set() if statement == "BEGIN IMMEDIATE" else None
            )
            yield c

    credential = rig.credentials["Odin"] if revocation == "role" else actor(rig, game)
    with original() as blocker:
        blocker.execute("BEGIN IMMEDIATE")
        monkeypatch.setattr(rig.store, "connection", observed_connection)
        with ThreadPoolExecutor(1) as pool:
            future = pool.submit(
                rig.core.mutate,
                credential,
                game["id"],
                1,
                "void" if revocation == "role" else "concede",
                note="Private admin action",
            )
            assert pending.wait(timeout=20)
            if revocation == "session":
                blocker.execute("DELETE FROM sessions WHERE token_hash=?", (credential,))
            elif revocation == "active":
                blocker.execute(
                    "UPDATE accounts SET active=0 WHERE id=?", (rig.accounts["Archer"]["id"],)
                )
            elif revocation == "role":
                blocker.execute(
                    "UPDATE accounts SET role='USER' WHERE id=?", (rig.accounts["Odin"]["id"],)
                )
            else:
                rig.clock.value += timedelta(seconds=60)
            blocker.commit()
            if revocation == "clock":
                assert future.result(timeout=20)["transition"] == "timeout"
            else:
                with pytest.raises(OnlineError):
                    future.result(timeout=20)
    current = rig.core.read(rig.credentials["Keeper"], game["id"])
    assert current["version"] == (2 if revocation == "clock" else 1)


def test_existing_seat_verification_is_explicitly_policy_controlled(rig):
    game = active(rig)
    with rig.store.connection() as c:
        c.execute(
            "UPDATE accounts SET email_verified_at=NULL WHERE id=?", (rig.accounts["Archer"]["id"],)
        )
    undecided = OnlineCore(
        rig.auth, replace(POLICY, retain_seat_without_verification=None), rig.clock
    )
    with pytest.raises(OnlineError, match="policy requires"):
        undecided.mutate(actor(rig, game), game["id"], 1, "concede")
    assert (
        rig.core.mutate(actor(rig, game), game["id"], 1, "concede")["game"]["status"] == "completed"
    )


@pytest.mark.parametrize("action", ["move", "concede"])
@pytest.mark.parametrize("offset", [-1, 0, 1])
def test_process_move_or_concession_versus_expiry_at_all_boundaries(rig, action, offset):
    game = active(rig)
    resume(rig)
    rig.clock.value += timedelta(seconds=60, microseconds=offset)
    race(rig, game, ("sweep", ""), (action, actor(rig, game)))
    current = rig.core.read(rig.credentials["Keeper"], game["id"])
    assert current["version"] == 2
    if offset < 0 and action == "move":
        assert current["status"] == "active" and len(current["moves"]) == 1
    elif offset < 0:
        assert current["result"]["termination_reason"] == "concession"
        assert current["result"]["abandoned_account_id"] is None
    else:
        assert current["result"]["termination_reason"] == "timeout"
        assert len(current["moves"]) == 0
        assert rig.core.statistics(rig.credentials["Archer"])["abandoned_games"] == 1


def test_bounded_sweeps_and_gate_clock_anomaly(rig):
    first, second = active(rig), active(rig)
    resume(rig)
    rig.clock.value += timedelta(seconds=61)
    rig.core.settings = replace(POLICY, sweep_batch=1)
    assert rig.core.sweep() == {"examined": 1, "finalized": 1, "blocked": 0}
    assert rig.core.sweep() == {"examined": 1, "finalized": 1, "blocked": 0}
    assert rig.core.sweep()["examined"] == 0
    third = active(rig)
    rig.clock.value += timedelta(seconds=120)
    rig.core.set_gate(rig.credentials["Odin"], 1, False, "Clock incident inspection")
    rig.clock.value -= timedelta(seconds=10)
    assert rig.core.sweep()["blocked"] == 1
    with pytest.raises(OnlineError) as error:
        rig.core.mutate(actor(rig, third), third["id"], 1, "concede")
    assert error.value.status == 503
    assert rig.core.read(rig.credentials["Keeper"], first["id"])["result"]
    assert rig.core.read(rig.credentials["Keeper"], second["id"])["result"]


@pytest.mark.parametrize("name", ["Archer", "Odin"])
def test_unverified_users_and_administrators_cannot_create(rig, name):
    with rig.store.connection() as c:
        c.execute(
            "UPDATE accounts SET email_verified_at=NULL WHERE id=?", (rig.accounts[name]["id"],)
        )
    with pytest.raises(OnlineError) as error:
        rig.core.create(rig.credentials[name], "brandubh", "attackers", 60, uuid4().hex)
    assert error.value.status == 403


def test_deadline_time_is_sampled_after_locked_state_reload(rig, monkeypatch):
    game = active(rig)
    resume(rig)
    original = rig.core._load
    boundary = rig.clock.value + timedelta(seconds=60)

    def state_loaded(c, identifier):
        current = original(c, identifier)
        rig.clock.value = boundary
        return current

    monkeypatch.setattr(rig.core, "_load", state_loaded)
    result = rig.core.mutate(actor(rig, game), game["id"], 1, "concede")
    assert result["transition"] == "timeout" and not result["action_accepted"]
    assert result["game"]["result"]["abandoned_account_id"] == rig.accounts["Archer"]["id"]
