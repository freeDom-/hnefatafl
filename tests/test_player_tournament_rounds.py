"""Synchronized rounds over real online games, including rebuilds and upgrades."""

import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest

from hnefatafl.auth import digest
from hnefatafl.online_core import OnlineError, stamp
from hnefatafl.online_migrations import retain_online_on_restore
from hnefatafl.player_tournaments import DISPLACED_GAME_NOTE
from hnefatafl.server import create_app
from hnefatafl.storage import Store
from test_player_tournaments import (
    ADMIN,
    PLAYERS,
    POLICY,
    account_template as account_template,
    assert_double_elimination,
    assert_no_duplicate_matches_or_legs,
    force_leg,
    ready_matches,
    rig as rig,
    safe_move,
    table_counts,
)

MODES = ("sequential", "simultaneous")


def create_cup(rig, mode, entrants, *, capacity=None):
    names = [*PLAYERS, ADMIN]
    for index in range(len(names) + 1, entrants + 1):
        name = f"P{index:02d}"
        password = "maximum-tournament-fixture-password"
        account = rig.auth.create_account(name, f"{name.lower()}@example.test", password, "USER")
        _, token, _ = rig.auth.login(name, password, f"maximum-tournament-{name}")
        with rig.store.connection() as c:
            c.execute("UPDATE accounts SET email_verified_at=1 WHERE id=?", (account["id"],))
        credential = digest(token)
        rig.by_id[account["id"]] = rig.credentials[name] = credential
        names.append(name)
    cup = rig.core.create(
        rig.admin,
        name="Global rounds",
        ruleset="brandubh",
        capacity=capacity or entrants,
        time_per_move=60,
        match_mode=mode,
    )
    for name in names[:entrants]:
        cup = rig.core.register(rig.credentials[name], cup["id"])
    return cup


def restart(rig):
    app = create_app(
        rig.store.path.parent,
        online_settings=POLICY,
        online_clock=rig.clock,
        online_background=False,
        account_background=False,
    )
    rig.app, rig.core, rig.online = app, app.state.player_tournaments, app.state.online
    worker = app.state.tournament_settlement
    assert worker.recover()
    return worker


def match(rig, tournament, match_id):
    return rig.core.match(rig.admin, tournament, match_id)


def player(match, seat):
    return match["participants"][seat]["account_id"]


def all_matches(bracket):
    return [
        *(match for entry in bracket["upper"] + bracket["lower"] for match in entry["matches"]),
        *(match for match in (bracket["final"], bracket["reset"]) if match is not None),
    ]


def decide(rig, mode, identifier, match, winner, *, drain=True):
    worker = rig.app.state.tournament_settlement
    force_leg(rig, match["legs"][0], winner)
    if mode == "sequential":
        assert worker.drain() == 1
        match = rig.core.match(rig.admin, identifier, match["id"])
    force_leg(rig, match["legs"][1], winner)
    if drain:
        assert worker.drain() == 1


def assert_side_swap(rig, match):
    assert [leg["leg"] for leg in match["legs"]] == [1, 2]
    players = (player(match, "a"), player(match, "b"))
    for leg, seats in zip(match["legs"], (players, players[::-1]), strict=True):
        game = rig.online.read(rig.admin, leg["game_id"])
        assert (
            tuple(game["participants"][side]["account_id"] for side in ("attackers", "defenders"))
            == seats
        )
        assert game["rated"] is False


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("entrants", [2, 3, 4, 5, 8, 16, 64])
def test_authoritative_global_rounds_end_to_end(rig, mode, entrants, record_property):
    cup = create_cup(rig, mode, entrants)
    identifier = cup["id"]
    worker = rig.app.state.tournament_settlement
    started_at = time.perf_counter()
    cup = rig.core.start(rig.admin, identifier, cup["version"])
    start_seconds = time.perf_counter() - started_at
    assert cup["progression_policy"] == "synchronized"
    size = cup["bracket_size"]
    assert len(ready_matches(rig, identifier)) == entrants - size // 2
    previous_round = 0
    completed_rounds = set()
    while cup["status"] == "running":
        bracket = rig.core.bracket(rig.admin, identifier)
        progress = bracket["round_progress"]
        number = progress["number"]
        assert number > previous_round
        previous_round = number
        completed_rounds.add(number)
        upper_rounds = size.bit_length() - 1
        final_round = max(upper_rounds, len(bracket["lower"]) + 1) + 1
        by_round = {
            entry["id"]: {
                "upper": entry["round"],
                "lower": entry["round"] + 1,
                "grand_final": final_round,
                "reset": final_round + 1,
            }[entry["bracket"]]
            for entry in all_matches(bracket)
        }
        current_matches = []
        for entry in all_matches(bracket):
            assert entry["global_round"] == by_round[entry["id"]]
            if entry["global_round"] > number:
                assert entry["waiting_for_round"] and entry["legs"] == []
            elif entry["status"] == "ready":
                assert entry["global_round"] == number
                current_matches.append(entry)
                legs = entry["legs"]
                assert len(legs) == (1 if mode == "sequential" else 2)
                for leg in legs:
                    assert rig.online.read(rig.admin, leg["game_id"])["started_at"] == stamp(
                        rig.clock.value
                    )
                if mode == "simultaneous":
                    assert_side_swap(rig, entry)
        assert len(current_matches) == progress["outstanding_matches"] > 0
        assert progress["finished_matches"] == sum(
            entry["status"] in ("complete", "bye") and entry["global_round"] == number
            for entry in all_matches(bracket)
        )
        for index, entry in enumerate(current_matches):
            winner = player(entry, "b" if entry["bracket"] == "grand_final" else "a")
            rig.clock.value += timedelta(microseconds=1)
            force_leg(rig, entry["legs"][0], winner)
            if entrants == 5 and number == 1:
                worker = restart(rig)
            assert worker.drain() == 1
            midway = match(rig, identifier, entry["id"])
            assert midway["status"] == "ready" and midway["legs"][0]["outcome"] in ("a", "b")
            assert midway["legs"][1]["game_status"] == "active"
            if mode == "sequential":
                assert_side_swap(rig, midway)
                assert all(
                    len(match(rig, identifier, other["id"])["legs"]) == 1
                    for other in current_matches[index + 1 :]
                )
            force_leg(rig, midway["legs"][1], winner)
            if entrants == 5 and number == 1:
                worker = restart(rig)
            assert worker.drain() == 1
            if index < len(current_matches) - 1:
                assert rig.core.bracket(rig.admin, identifier)["round_progress"] == {
                    "number": number,
                    "finished_matches": progress["finished_matches"] + index + 1,
                    "outstanding_matches": len(current_matches) - index - 1,
                }
        cup = rig.core.detail(rig.admin, identifier)
    assert cup["status"] == "complete"
    bracket = rig.core.bracket(rig.admin, identifier)
    assert bracket["reset"]["status"] == "complete"
    assert bracket["round_progress"] is None
    assert len(completed_rounds) == previous_round
    assert_double_elimination(rig, identifier)
    assert_no_duplicate_matches_or_legs(rig)
    counts = table_counts(rig)
    assert counts["games"] == counts["legs"] == 2 * (2 * entrants - 1)
    assert worker.drain() == 0
    if entrants == 64:
        elapsed = time.perf_counter() - started_at
        record_property("start_seconds", round(start_seconds, 3))
        record_property("start_to_champion_seconds", round(elapsed, 3))
        assert start_seconds < 5 and elapsed < 45


@pytest.mark.parametrize("mode", MODES)
def test_bye_only_global_rounds_are_skipped_in_the_start_transaction(rig, monkeypatch, mode):
    cup = create_cup(rig, mode, 2, capacity=8)
    # Exercise a stored oversized bracket with two entrants in opposite halves.
    monkeypatch.setattr("hnefatafl.player_tournaments.power_of_two_at_least", lambda _value: 8)
    rig.core.start(rig.admin, cup["id"], cup["version"])
    bracket = rig.core.bracket(rig.admin, cup["id"])
    assert bracket["round_progress"] == {
        "number": 3,
        "finished_matches": 2,
        "outstanding_matches": 1,
    }
    ready = ready_matches(rig, cup["id"])
    assert [(entry["bracket"], entry["round"]) for entry in ready] == [("upper", 3)]
    decide(rig, mode, cup["id"], ready[0], player(ready[0], "a"))
    assert [(entry["bracket"], entry["round"]) for entry in ready_matches(rig, cup["id"])] == [
        ("grand_final", 1)
    ]


@pytest.mark.parametrize("mode", MODES)
def test_concurrent_last_results_only_open_each_next_round_game_once(rig, mode):
    cup = create_cup(rig, mode, 4)
    identifier = cup["id"]
    rig.core.start(rig.admin, identifier, cup["version"])
    worker = rig.app.state.tournament_settlement
    opening = ready_matches(rig, identifier)
    for entry in opening:
        force_leg(rig, entry["legs"][0], player(entry, "a"))
    assert worker.drain() == 1
    barrier = Barrier(2, timeout=10)

    def complete(entry):
        second = match(rig, identifier, entry["id"])["legs"][1]
        barrier.wait()
        force_leg(rig, second, player(entry, "a"))
        worker.drain()

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(complete, opening))
    assert worker.drain() == 0
    assert [(entry["bracket"], entry["round"]) for entry in ready_matches(rig, identifier)] == [
        ("upper", 2),
        ("lower", 1),
    ]
    assert table_counts(rig)["games"] == (6 if mode == "sequential" else 8)
    assert_no_duplicate_matches_or_legs(rig)


@pytest.mark.parametrize("mode", MODES)
def test_draw_void_timeout_and_override_count_as_authoritative_round_decisions(rig, mode):
    cup = create_cup(rig, mode, 4)
    identifier = cup["id"]
    rig.core.start(rig.admin, identifier, cup["version"])
    first, second = ready_matches(rig, identifier)
    worker = rig.app.state.tournament_settlement
    game = rig.online.read(rig.admin, first["legs"][0]["game_id"])
    # Repeat a legal Brandubh position through the engine and online lifecycle.
    for source, target in [(3, 2), (17, 16), (2, 3), (16, 17)] * 2:
        mover = game["participants"][game["position"]["turn"]]["account_id"]
        game = rig.online.mutate(
            rig.by_id[mover], game["id"], game["version"], "move", source=source, target=target
        )["game"]
    assert game["result"]["winner"] == "draw"
    assert worker.drain() == 1
    first = match(rig, identifier, first["id"])
    assert first["status"] == "ready" and first["legs"][0]["outcome"] == "draw"
    leg = rig.online.read(rig.admin, first["legs"][1]["game_id"])
    rig.online.mutate(rig.admin, leg["id"], leg["version"], "void", note="Invalid return game")
    assert worker.drain() == 1
    first = match(rig, identifier, first["id"])
    assert first["decided_by"] == "higher_seed" and first["legs"][1]["outcome"] == "void"
    assert rig.core.bracket(rig.admin, identifier)["round_progress"]["number"] == 1

    if mode == "simultaneous":
        rig.clock.value += timedelta(seconds=1)
        return_game = rig.online.read(rig.admin, second["legs"][1]["game_id"])
        mover = return_game["participants"][return_game["position"]["turn"]]["account_id"]
        move = safe_move(return_game)
        rig.online.mutate(
            rig.by_id[mover],
            return_game["id"],
            return_game["version"],
            "move",
            source=move.source,
            target=move.target,
        )
        rig.clock.value -= timedelta(seconds=1)
    rig.clock.value += timedelta(seconds=60)
    rig.online.sweep()
    assert worker.drain() == 1
    second = match(rig, identifier, second["id"])
    assert second["status"] == "ready" and second["legs"][1]["game_status"] == "active"
    timed_out = rig.online.read(rig.admin, second["legs"][0]["game_id"])
    assert timed_out["result"]["termination_reason"] == "timeout"
    version = rig.core.detail(rig.admin, identifier)["version"]
    rig.core.override(rig.admin, identifier, second["id"], version, player(second, "a"), "Reviewed")
    assert rig.core.bracket(rig.admin, identifier)["round_progress"]["number"] == 2
    assert rig.online.read(rig.admin, timed_out["id"]) == timed_out
    assert match(rig, identifier, second["id"])["legs"][1]["game_status"] == "voided"


@pytest.mark.parametrize("mode", MODES)
def test_rebuild_retires_unproven_later_games_and_keeps_proven_games(rig, mode):
    cup = create_cup(rig, mode, 16)
    identifier = cup["id"]
    rig.core.start(rig.admin, identifier, cup["version"])
    opening = ready_matches(rig, identifier)
    corrected = opening[0]
    for index, entry in enumerate(opening):
        decide(rig, mode, identifier, entry, player(entry, "a" if index else "b"))
    overridden = f"{identifier}:upper:2:0"
    for entry in ready_matches(rig, identifier):
        if entry["id"] == overridden:
            version = rig.core.detail(rig.admin, identifier)["version"]
            rig.core.override(
                rig.admin, identifier, overridden, version, player(entry, "a"), "Earlier override"
            )
        else:
            decide(rig, mode, identifier, entry, player(entry, "a"))
    affected = match(rig, identifier, f"{identifier}:upper:3:0")
    unaffected = match(rig, identifier, f"{identifier}:upper:3:1")
    assert affected["status"] == unaffected["status"] == "ready"
    worker = rig.app.state.tournament_settlement
    force_leg(rig, affected["legs"][0], player(affected, "a"))
    assert worker.drain() == 1
    affected = match(rig, identifier, affected["id"])
    history, stale_game = [rig.online.read(rig.admin, leg["game_id"]) for leg in affected["legs"]]
    assert history["status"] == "completed" and stale_game["status"] == "active"
    safe_ids = [leg["game_id"] for leg in unaffected["legs"]]
    for leg in match(rig, identifier, corrected["id"])["legs"]:
        result = rig.online.read(rig.admin, leg["game_id"])
        rig.online.mutate(
            rig.admin, result["id"], result["version"], "void", note="Correct earlier match"
        )
    assert worker.drain() == 1
    disputed = rig.core.detail(rig.admin, identifier)
    assert disputed["review_state"] == "disputed"
    with pytest.raises(OnlineError, match="Rebuild the bracket"):
        rig.core.override(
            rig.admin,
            identifier,
            unaffected["id"],
            disputed["version"],
            player(unaffected, "a"),
            "Frozen",
        )

    rebuilt = rig.core.rebuild(rig.admin, identifier, disputed["version"], "Re-derive every round")
    assert rebuilt["review_state"] == "ok"
    bracket = rig.core.bracket(rig.admin, identifier)
    assert bracket["round_progress"]["number"] == 2
    former_override = match(rig, identifier, overridden)
    assert former_override["status"] == "ready" and former_override["decided_by"] is None
    later = match(rig, identifier, affected["id"])
    assert later["status"] == "pending" and later["legs"] == []
    retained = match(rig, identifier, unaffected["id"])
    assert retained["status"] == "pending"
    assert [leg["game_id"] for leg in retained["legs"]] == safe_ids
    assert all(rig.online.read(rig.admin, game_id)["status"] == "active" for game_id in safe_ids)
    assert rig.online.read(rig.admin, history["id"]) == history
    retired = rig.online.read(rig.admin, stale_game["id"])
    assert retired["status"] == "voided" and retired["deadline"] is None
    assert retired["result"]["termination_reason"] == "administrative_void"
    with rig.store.connection() as c:
        audit = c.execute(
            "SELECT actor_id,note FROM online_void_audit WHERE game_id=?", (retired["id"],)
        ).fetchone()
        assert tuple(audit) == (rig.accounts[ADMIN]["id"], DISPLACED_GAME_NOTE)
        assert not c.execute(
            """SELECT id FROM online_games WHERE status='active'
            AND id NOT IN (SELECT game_id FROM player_tournament_legs)"""
        ).fetchall()
    counts = table_counts(rig)
    rig.core.rebuild(rig.admin, identifier, rebuilt["version"], "Verify identical derivation")
    assert table_counts(rig) == counts
    for entry in ready_matches(rig, identifier):
        decide(rig, mode, identifier, entry, player(entry, "a"))
    assert rig.core.bracket(rig.admin, identifier)["round_progress"]["number"] == 3
    replacement = match(rig, identifier, affected["id"])
    assert replacement["status"] == "ready"
    assert all(leg["game_id"] not in (retired["id"], history["id"]) for leg in replacement["legs"])
    assert [leg["game_id"] for leg in match(rig, identifier, unaffected["id"])["legs"]] == safe_ids
    assert_no_duplicate_matches_or_legs(rig)


@pytest.mark.parametrize("mode", MODES)
def test_rebuild_validates_role_order_even_when_both_accounts_match(rig, mode, monkeypatch):
    cup = create_cup(rig, mode, 2)
    seat_game = rig.online.seat_game

    def reverse_first_game(*args, **kwargs):
        if ":upper:1:0:1:" in kwargs["request_id"]:
            kwargs["creator_side"] = "defenders"
        return seat_game(*args, **kwargs)

    monkeypatch.setattr(rig.online, "seat_game", reverse_first_game)
    started = rig.core.start(rig.admin, cup["id"], cup["version"])
    original = ready_matches(rig, cup["id"])[0]
    wrong = original["legs"][0]["game_id"]
    monkeypatch.setattr(rig.online, "seat_game", seat_game)
    rig.core.rebuild(rig.admin, cup["id"], started["version"], "Checked role assignments")
    assert rig.online.read(rig.admin, wrong)["status"] == "voided"
    corrected = match(rig, cup["id"], original["id"])
    assert corrected["legs"][0]["game_id"] != wrong
    if mode == "simultaneous":
        assert corrected["legs"][1]["game_id"] == original["legs"][1]["game_id"]
        assert_side_swap(rig, corrected)
    else:
        game = rig.online.read(rig.admin, corrected["legs"][0]["game_id"])
        assert game["participants"]["attackers"]["account_id"] == player(corrected, "a")


@pytest.mark.parametrize("mode", MODES)
def test_schema21_upgrade_retains_existing_progression_and_schema22_restore(rig, mode, tmp_path):
    unstarted = create_cup(rig, mode, 8)
    existing = create_cup(rig, mode, 8)
    identifier = existing["id"]
    rig.core.start(rig.admin, identifier, existing["version"])
    with rig.store.connection() as c:
        # Schema 21 modern matches used the independent advancement path.
        c.execute(
            "UPDATE player_tournaments SET progression_policy='independent' WHERE id=?",
            (identifier,),
        )
    opening = ready_matches(rig, identifier)
    for entry in opening[:1]:
        decide(rig, mode, identifier, entry, player(entry, "a"))
    retained = ready_matches(rig, identifier)
    history = create_cup(rig, "simultaneous", 2)
    rig.core.start(rig.admin, history["id"], history["version"])
    while history["status"] != "complete":
        entry = ready_matches(rig, history["id"])[0]
        decide(rig, "simultaneous", history["id"], entry, player(entry, "a"))
        history = rig.core.detail(rig.admin, history["id"])
    history_bracket = rig.core.bracket(rig.admin, history["id"])
    before_upgrade = tmp_path / "schema21.sqlite3"
    with rig.store.connection() as c:
        for column in ("progression_policy", "schedule_version"):
            c.execute(f"ALTER TABLE player_tournaments DROP COLUMN {column}")
        c.execute("PRAGMA user_version=21")
    with rig.store.connection() as source, sqlite3.connect(before_upgrade) as target:
        source.backup(target)
    restart(rig)
    assert rig.core.detail(rig.admin, identifier)["progression_policy"] == "independent"
    assert ready_matches(rig, identifier) == retained
    assert not any(
        rig.online.read(rig.admin, leg["game_id"])["status"] != "active"
        for entry in retained
        for leg in entry["legs"]
    )
    old_history = rig.core.bracket(rig.admin, history["id"])
    assert [entry["legs"] for entry in all_matches(old_history)] == [
        entry["legs"] for entry in all_matches(history_bracket)
    ]
    assert old_history["round_progress"] is None
    decide(rig, mode, identifier, retained[0], player(retained[0], "a"))
    assert any(
        entry["round"] == 2 and entry["bracket"] == "upper"
        for entry in ready_matches(rig, identifier)
    )
    assert any(
        entry["round"] == 1 and entry["bracket"] == "upper"
        for entry in ready_matches(rig, identifier)
    )
    snapshot = tmp_path / "schema22.sqlite3"
    with rig.store.connection() as source, sqlite3.connect(snapshot) as target:
        source.backup(target)
    unstarted = rig.core.reschedule(
        rig.admin, unstarted["id"], unstarted["version"], rig.clock.value + timedelta(minutes=1)
    )
    started = rig.core.start(rig.admin, unstarted["id"], unstarted["version"])
    assert started["progression_policy"] == "synchronized"
    with rig.store.connection() as source, sqlite3.connect(before_upgrade) as target:
        with pytest.raises(ValueError, match="Online schema changed"):
            retain_online_on_restore(source, target)
    with rig.store.connection() as source, sqlite3.connect(snapshot) as target:
        retain_online_on_restore(source, target)
        policies = dict(target.execute("SELECT id,progression_policy FROM player_tournaments"))
        assert policies[identifier] == "independent"
        assert policies[unstarted["id"]] == "synchronized"
        assert (
            target.execute(
                "SELECT schedule_version FROM player_tournaments WHERE id=?", (unstarted["id"],)
            ).fetchone()[0]
            == 1
        )
        assert not target.execute("PRAGMA foreign_key_check").fetchall()
    restored = Store(snapshot)
    with restored.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM online_void_audit").fetchone()[0] == 0
