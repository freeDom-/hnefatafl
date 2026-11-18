"""Shared successful analysis credits every authorized requester atomically."""

from concurrent.futures import ThreadPoolExecutor
import json
import threading

import pytest

from hnefatafl.analysis_service import AnalysisService
from hnefatafl.progression import Progression
from test_progression import persist_game, rig as rig, account_template as account_template
from test_ratings import active, finish

REPORT = {"evaluations": [{"agent_id": "advisor", "source": "heuristic"}]}
SNAPSHOT = {"position_key": "shared-position", "cursor": 0, "evaluations": [], "hint": None}


def receipts(store):
    with store.connection() as c:
        return [
            tuple(row)
            for row in c.execute(
                "SELECT account_id,category,game_id FROM replay_analysis_completions ORDER BY 1,2,3"
            )
        ]


def terminal(rig, account="Archer"):
    game = finish(rig, active(rig), "attackers")
    return {
        "id": game["id"],
        "online": True,
        "recorded_result": {"winner": "attackers"},
        "_analysis_account_id": rig.accounts[account]["id"],
    }


def test_shared_work_credits_both_participants_without_poll_and_bounds_requesters(rig):
    entered, release = threading.Event(), threading.Event()
    calls = []

    def evaluate(*args, **kwargs):
        calls.append(1)
        entered.set()
        assert release.wait(5)
        return REPORT["evaluations"][0]

    service = AnalysisService(rig.store, evaluate)
    game = terminal(rig)
    try:
        key = service.schedule(game, SNAPSHOT, "advisor", {}, {"revision": 0})
        assert entered.wait(5)
        second = {**game, "_analysis_account_id": rig.accounts["Keeper"]["id"]}
        assert service.schedule(second, SNAPSHOT, "advisor", {}, {"revision": 0}) == key
        for _ in range(20):
            visitor = {**game, "_analysis_account_id": rig.accounts["Visitor"]["id"]}
            service.schedule(visitor, SNAPSHOT, "advisor", {}, {"revision": 0})
            service.schedule(second, SNAPSHOT, "advisor", {}, {"revision": 0})
        assert service.requesters == {key: {rig.accounts[n]["id"] for n in ("Archer", "Keeper")}}
        future = service.tasks[key]
        release.set()
        future.result(timeout=5)
        service.schedule(second, SNAPSHOT, "advisor", {}, {"revision": 0})
    finally:
        release.set()
        service.shutdown()
    assert calls == [1]
    assert len(receipts(rig.store)) == 2
    assert service.requesters == {}
    for name in ("Archer", "Keeper"):
        payload = Progression(rig.core).achievements(rig.credentials[name])
        assert (
            next(f for f in payload["families"] if f["id"] == "analyzed-replays")["awarded_tier"]
            == 1
        )
    assert all(r[0] != rig.accounts["Visitor"]["id"] for r in receipts(rig.store))


@pytest.mark.parametrize("outcome", ["failure", "superseded", "shutdown"])
def test_failed_superseded_and_shutdown_work_does_not_credit_and_releases_requesters(rig, outcome):
    entered, release = threading.Event(), threading.Event()

    def evaluate(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        if outcome == "failure":
            raise RuntimeError("Controlled analysis failure")
        return REPORT["evaluations"][0]

    service = AnalysisService(rig.store, evaluate)
    game = terminal(rig)
    key = service.schedule(game, SNAPSHOT, "advisor", {}, {"revision": 0})
    assert entered.wait(5)
    future = service.tasks[key]
    if outcome == "superseded":
        with rig.store.connection() as c:
            c.execute(
                "INSERT INTO game_analytics VALUES(?,?)", (game["id"], json.dumps({"revision": 1}))
            )
    if outcome == "shutdown":
        # Taking this same lock makes the shutdown boundary deterministic.
        with service.lock:
            service.closed = True
    release.set()
    future.result(timeout=5)
    service.shutdown()
    assert receipts(rig.store) == []
    assert service.requesters == {}


def test_cached_receipts_do_not_wait_for_a_sqlite_writer(rig):
    game = persist_game(rig.store, rig.accounts["Archer"], winner="defenders")
    account = rig.accounts["Archer"]["id"]
    rig.store.record_replay_analysis(game, REPORT, account)
    before = receipts(rig.store)
    with rig.store.connection() as writer, ThreadPoolExecutor(max_workers=1) as pool:
        writer.execute("BEGIN IMMEDIATE")
        # A redundant BEGIN IMMEDIATE would block here for the 15s DB timeout.
        pool.submit(rig.store.record_replay_analysis, game, REPORT, account).result(timeout=2)
    assert receipts(rig.store) == before


def test_concurrent_first_receipts_and_categories_accounts_are_unique(rig):
    game = terminal(rig)
    account = rig.accounts["Archer"]["id"]
    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(lambda _: rig.store.record_replay_analysis(game, REPORT, account), range(12)))
    assert receipts(rig.store) == [(account, "online", game["id"])]
    keeper = rig.accounts["Keeper"]["id"]
    rig.store.record_replay_analysis(game, REPORT, keeper)
    rig.store.record_replay_analysis(game, REPORT, rig.accounts["Visitor"]["id"])
    local = persist_game(rig.store, rig.accounts["Archer"], winner="defenders")
    with rig.store.connection() as c:
        c.execute(
            "UPDATE games SET id=?,data=json_set(data,'$.id',?) WHERE id=?",
            (game["id"], game["id"], local["id"]),
        )
    local["id"] = game["id"]
    rig.store.record_replay_analysis(local, REPORT, account)
    assert len(receipts(rig.store)) == 3
    data = Progression(rig.core).achievements(rig.credentials["Archer"])
    assert next(f for f in data["families"] if f["id"] == "analyzed-replays")["progress"] == 2


def test_partial_failed_live_and_undone_reports_do_not_qualify(rig):
    game = persist_game(rig.store, rig.accounts["Archer"], winner="defenders")
    account = rig.accounts["Archer"]["id"]
    for report in (
        None,
        {**REPORT, "partial": True},
        {"evaluations": [{"agent_id": "advisor", "source": "unavailable"}]},
        {"evaluations": [{"agent_id": "attackers", "source": "heuristic"}]},
    ):
        rig.store.record_replay_analysis(game, report, account)
    rig.store.record_replay_analysis({**game, "recorded_result": None}, REPORT, account)
    with rig.store.connection() as c:
        c.execute(
            "UPDATE games SET data=json_set(data,'$.recorded_result',NULL) WHERE id=?",
            (game["id"],),
        )
    rig.store.record_replay_analysis(game, REPORT, account)
    assert receipts(rig.store) == []


def test_late_second_requester_is_credited_when_worker_commits_during_cache_read(rig, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def evaluate(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return REPORT["evaluations"][0]

    service = AnalysisService(rig.store, evaluate)
    game = terminal(rig)
    try:
        key = service.schedule(game, SNAPSHOT, "advisor", {}, {"revision": 0})
        assert entered.wait(5)
        future = service.tasks[key]
        original = rig.store.advisor_analysis
        reads = 0

        def complete_during_read(*args):
            nonlocal reads
            retained = original(*args)
            reads += 1
            if reads == 1:
                assert retained is None
                release.set()
                # Future completion does not need the scheduling lock held by
                # this caller; its cleanup callback runs after enrollment ends.
                future.result(timeout=5)
            return retained

        monkeypatch.setattr(rig.store, "advisor_analysis", complete_during_read)
        second = {**game, "_analysis_account_id": rig.accounts["Keeper"]["id"]}
        assert service.schedule(second, SNAPSHOT, "advisor", {}, {"revision": 0}) == key
        assert {r[0] for r in receipts(rig.store)} == {
            rig.accounts[name]["id"] for name in ("Archer", "Keeper")
        }
    finally:
        release.set()
        service.shutdown()
    assert not service.requesters
