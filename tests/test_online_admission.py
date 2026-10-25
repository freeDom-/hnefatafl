"""Bounded authenticated creation, transactional quotas and private monitoring."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
import json
import logging
import multiprocessing
from pathlib import Path
import sqlite3
import threading
from uuid import uuid4

import pytest

from hnefatafl.auth import Auth, SecuritySettings
from hnefatafl.online_core import OnlineCore, OnlineError
from hnefatafl.online_migrations import retain_online_on_restore
from hnefatafl.online_settings import OnlineSettings
from hnefatafl.storage import Store
from test_online_api import api as api, creation
from test_online_core import (
    POLICY,
    account_template as account_template,
    rig as rig,
)


def create(rig, who="Archer", mode="single", request_id=None):
    return rig.core.create(
        rig.credentials[who], "brandubh", "attackers", 60, request_id or uuid4().hex, mode
    )["game"]


def metric(rig, reason):
    with rig.store.connection() as c:
        return dict(
            c.execute("SELECT * FROM online_creation_metrics WHERE reason=?", (reason,)).fetchone()
        )


@pytest.mark.parametrize("release", ["cancel", "join", "void"])
def test_waiting_cap_rejects_atomically_and_lifecycle_releases_capacity(rig, release):
    rig.core.settings = replace(POLICY, waiting_limit=1)
    first = create(rig)
    with pytest.raises(OnlineError) as denied:
        create(rig)
    assert denied.value.status == 429
    with rig.store.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM online_matches").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM online_games").fetchone()[0] == 1
    assert metric(rig, "waiting_limit")["denials"] == 1
    who = {"cancel": "Archer", "join": "Keeper", "void": "Odin"}[release]
    rig.core.mutate(rig.credentials[who], first["id"], 0, release, note="Private test reason")
    assert create(rig)["status"] == "waiting"


def test_successful_creation_window_survives_cancellation_and_expires_exactly(rig):
    rig.core.settings = replace(POLICY, creation_limit=2, creation_window_seconds=60)
    for _ in range(2):
        game = create(rig)
        rig.core.mutate(rig.credentials["Archer"], game["id"], 0, "cancel")
    with pytest.raises(OnlineError) as denied:
        create(rig)
    assert denied.value.status == 429 and "rate limit" in str(denied.value)
    rig.clock.value += timedelta(seconds=60, microseconds=-1)
    with pytest.raises(OnlineError):
        create(rig)
    rig.clock.value += timedelta(microseconds=1)
    assert create(rig)["status"] == "waiting"
    assert metric(rig, "creation_rate")["denials"] == 2
    assert create(rig, "Keeper")["status"] == "waiting"


def test_duplicate_creation_retry_is_still_a_conflict_and_spends_no_budget(rig):
    rig.core.settings = replace(POLICY, waiting_limit=1, creation_limit=1)
    request_id = uuid4().hex
    first = create(rig, request_id=request_id)
    with pytest.raises(OnlineError) as duplicate:
        create(rig, request_id=request_id)
    assert duplicate.value.status == 409
    assert metric(rig, "waiting_limit")["denials"] == 0
    assert metric(rig, "creation_rate")["denials"] == 0
    assert rig.core.read(rig.credentials["Archer"], first["id"])["version"] == 0


@pytest.mark.parametrize("mode", ["simultaneous", "sequential"])
def test_pair_is_one_creation_and_automatic_return_does_not_spend_admission(rig, mode):
    rig.core.settings = replace(POLICY, waiting_limit=1, creation_limit=1)
    game = create(rig, mode=mode)
    game = rig.core.mutate(rig.credentials["Keeper"], game["id"], 0, "join")["game"]
    if mode == "sequential":
        game = rig.core.mutate(rig.credentials["Archer"], game["id"], 1, "concede")["game"]
    assert len(game["match"]["games"]) == 2
    with rig.store.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM online_matches").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM online_games").fetchone()[0] == 2
    assert metric(rig, "creation_rate")["denials"] == 0
    with pytest.raises(OnlineError, match="rate limit"):
        create(rig)


def test_failed_transaction_does_not_spend_creation_budget_or_leave_a_match(rig):
    rig.core.settings = replace(POLICY, creation_limit=1)
    with rig.store.connection() as c:
        c.execute(
            "CREATE TRIGGER fail_create BEFORE INSERT ON online_games BEGIN SELECT RAISE(ABORT,'injected'); END"
        )
    request_id = uuid4().hex
    with pytest.raises(sqlite3.IntegrityError):
        create(rig, request_id=request_id)
    with rig.store.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM online_matches").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM online_games").fetchone()[0] == 0
        c.execute("DROP TRIGGER fail_create")
    assert create(rig, request_id=request_id)["status"] == "waiting"


@pytest.mark.parametrize("limit", ["waiting_limit", "creation_limit"])
def test_two_connections_cannot_both_take_last_creation_slot(rig, limit):
    rig.core.settings = replace(POLICY, **{limit: 1})
    barrier = threading.Barrier(2)

    def compete():
        independent = OnlineCore(rig.auth, rig.core.settings, rig.clock)
        barrier.wait(timeout=20)
        try:
            independent.create(rig.credentials["Archer"], "brandubh", "attackers", 60, uuid4().hex)
            return 201
        except OnlineError as error:
            return error.status

    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(lambda _: compete(), range(2))) == [201, 429]
    with rig.store.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM online_matches").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM online_games").fetchone()[0] == 1


def _process_create(path, settings, moment, credential, barrier, out):
    auth = Auth(Store(Path(path)), SecuritySettings())
    core = OnlineCore(auth, settings, lambda: moment)
    barrier.wait(timeout=30)
    try:
        core.create(credential, "brandubh", "attackers", 60, uuid4().hex)
        out.put(201)
    except OnlineError as error:
        out.put(error.status)


@pytest.mark.parametrize("limit", ["waiting_limit", "creation_limit"])
def test_two_processes_share_last_slot_and_persist_one_denial(rig, limit):
    settings = replace(POLICY, **{limit: 1})
    context = multiprocessing.get_context("spawn")
    barrier, out = context.Barrier(2), context.Queue()
    workers = [
        context.Process(
            target=_process_create,
            args=(
                str(rig.store.path),
                settings,
                rig.clock.value,
                rig.credentials["Archer"],
                barrier,
                out,
            ),
        )
        for _ in range(2)
    ]
    try:
        for process in workers:
            process.start()
        assert sorted([out.get(timeout=40), out.get(timeout=40)]) == [201, 429]
        for process in workers:
            process.join(timeout=30)
            assert process.exitcode == 0
    finally:
        for process in workers:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
        out.close()
    assert (
        metric(rig, "waiting_limit" if limit == "waiting_limit" else "creation_rate")["denials"]
        == 1
    )
    with rig.store.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM online_matches").fetchone()[0] == 1


def test_restart_preserves_rate_budget_and_denial_metrics(rig):
    rig.core.settings = replace(POLICY, creation_limit=1)
    game = create(rig)
    rig.core.mutate(rig.credentials["Archer"], game["id"], 0, "cancel")
    restarted = OnlineCore(
        Auth(Store(rig.store.path), SecuritySettings()), rig.core.settings, rig.clock
    )
    with pytest.raises(OnlineError, match="rate limit"):
        restarted.create(rig.credentials["Archer"], "brandubh", "attackers", 60, uuid4().hex)
    assert metric(rig, "creation_rate")["denials"] == 1


def test_denial_logs_are_sanitized_and_bounded_but_counts_include_every_attempt(rig, caplog):
    rig.core.settings = replace(POLICY, waiting_limit=1)
    create(rig)
    with caplog.at_level(logging.WARNING, logger="hnefatafl.online_core"):
        for _ in range(5):
            with pytest.raises(OnlineError):
                create(rig)
        assert len(caplog.records) == 1
        rig.clock.value += timedelta(seconds=60)
        with pytest.raises(OnlineError):
            create(rig)
    assert len(caplog.records) == 2 and "cumulative denials=6" in caplog.text
    assert metric(rig, "waiting_limit")["denials"] == 6
    assert all(
        value not in caplog.text
        for value in (
            *rig.tokens.values(),
            *rig.credentials.values(),
            *(a["id"] for a in rig.accounts.values()),
        )
    )
    assert "@" not in caplog.text and "Archer" not in caplog.text
    with rig.store.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM online_creation_metrics").fetchone()[0] == 2


def test_http_429_and_monitoring_are_private_read_only_and_show_growth(rig, api):
    app, client = api
    app.state.online.settings = replace(POLICY, waiting_limit=1)
    archer = client()
    assert archer.post("/api/online/games", json=creation()).status_code == 201
    assert archer.post("/api/online/games", json=creation()).status_code == 429
    assert archer.get("/api/online/monitoring").status_code == 403
    anonymous = type(archer)(app)
    assert anonymous.get("/api/online/monitoring").status_code == 401
    monitoring = client("Odin").get("/api/online/monitoring")
    assert monitoring.status_code == 200
    data = monitoring.json()
    assert data["online"]["matches"] == data["online"]["games"] == 1
    assert data["online"]["by_status"]["waiting"] == 1
    assert data["denied_creations"]["waiting_limit"]["count"] == 1
    assert data["database"]["allocated_bytes"] >= data["database"]["used_bytes"] > 0
    assert data["database"]["main_file_bytes"] > 0 and data["database"]["wal_bytes"] >= 0
    assert "@" not in json.dumps(data) and "password" not in json.dumps(data)
    assert client("Odin").get("/api/online/monitoring").json() == data


@pytest.mark.parametrize("name", ["Archer", "Odin"])
def test_account_quotas_are_independent_and_administrators_have_no_exemption(rig, name):
    rig.core.settings = replace(POLICY, waiting_limit=1)
    create(rig, name)
    with pytest.raises(OnlineError) as denied:
        create(rig, name)
    assert denied.value.status == 429
    other = "Keeper" if name == "Archer" else "Archer"
    assert create(rig, other)["status"] == "waiting"


@pytest.mark.parametrize(
    "field,value",
    [
        ("waiting_limit", 0),
        ("waiting_limit", True),
        ("waiting_limit", 1.0),
        ("waiting_limit", 1001),
        ("creation_limit", 0),
        ("creation_limit", "10"),
        ("creation_limit", False),
        ("creation_limit", 1001),
        ("creation_window_seconds", 59),
        ("creation_window_seconds", 86401),
        ("creation_window_seconds", 900.0),
    ],
)
def test_settings_never_disable_limits_implicitly(field, value):
    with pytest.raises(ValueError):
        replace(POLICY, **{field: value})


def test_existing_schema10_adds_monitoring_without_rewriting_authority(rig):
    game = create(rig)
    with rig.store.connection() as c:
        before = tuple(c.execute("SELECT * FROM online_games").fetchone())
        c.execute("DROP TABLE online_creation_metrics")
        c.execute("DROP INDEX online_creation_window")
        c.execute("DROP INDEX online_waiting_creator")
    reopened = Store(rig.store.path)
    with reopened.connection() as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == 10
        assert tuple(c.execute("SELECT * FROM online_games").fetchone()) == before
        assert c.execute("SELECT COUNT(*) FROM online_creation_metrics").fetchone()[0] == 2
    assert rig.core.read(rig.credentials["Archer"], game["id"])["status"] == "waiting"


def test_older_schema10_snapshot_preserves_current_budget_and_denial_counters(rig, tmp_path):
    snapshot = tmp_path / "before-hardening.sqlite3"
    with sqlite3.connect(rig.store.path) as source, sqlite3.connect(snapshot) as target:
        source.backup(target)
        target.execute("DROP TABLE online_creation_metrics")
        target.execute("DROP INDEX online_creation_window")
        target.execute("DROP INDEX online_waiting_creator")
    rig.core.settings = replace(POLICY, creation_limit=1)
    game = create(rig)
    rig.core.mutate(rig.credentials["Archer"], game["id"], 0, "cancel")
    with pytest.raises(OnlineError):
        create(rig)
    with rig.store.connection() as live, sqlite3.connect(snapshot) as restored:
        retain_online_on_restore(live, restored)
    restored_core = OnlineCore(
        Auth(Store(snapshot), SecuritySettings()), rig.core.settings, rig.clock
    )
    with pytest.raises(OnlineError, match="rate limit"):
        restored_core.create(rig.credentials["Archer"], "brandubh", "attackers", 60, uuid4().hex)
    with restored_core.store.connection() as c:
        assert (
            c.execute(
                "SELECT denials FROM online_creation_metrics WHERE reason='creation_rate'"
            ).fetchone()[0]
            == 2
        )
        assert c.execute("SELECT COUNT(*) FROM online_matches").fetchone()[0] == 1


def test_environment_configures_all_three_creation_limits(monkeypatch):
    monkeypatch.setenv("HNEFATAFL_ONLINE_WAITING_LIMIT", "3")
    monkeypatch.setenv("HNEFATAFL_ONLINE_CREATION_LIMIT", "6")
    monkeypatch.setenv("HNEFATAFL_ONLINE_CREATION_WINDOW_SECONDS", "1200")
    settings = OnlineSettings.environment()
    assert (settings.waiting_limit, settings.creation_limit, settings.creation_window_seconds) == (
        3,
        6,
        1200,
    )
