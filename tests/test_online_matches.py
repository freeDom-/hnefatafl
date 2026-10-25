"""Owner-approved random sides and simultaneous/sequential two-game matches."""

from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import random
import sqlite3
import threading
from uuid import uuid4

import pytest

from hnefatafl.engine import apply_move, legal_moves, new_game
from hnefatafl.online_core import OnlineError, safe_position
from test_online_core import (
    actor,
    dump,
    race,
    resume,
    account_template as account_template,
    rig as rig,
)


def match(rig, mode, side="random", rule="brandubh"):
    return rig.core.create(rig.credentials["Archer"], rule, side, 60, uuid4().hex, mode)["game"]


@pytest.mark.parametrize("mode", ["single", "simultaneous", "sequential"])
@pytest.mark.parametrize("side", ["attackers", "defenders", "random"])
def test_side_choice_is_resolved_once_at_atomic_activation(rig, monkeypatch, mode, side):
    calls = []

    def choose(options):
        calls.append(options)
        return "defenders"

    monkeypatch.setattr("hnefatafl.online_core.secrets.choice", choose)
    waiting = match(rig, mode, side, "tablut")
    assert not calls and waiting["deadline"] is None
    if side == "random":
        assert waiting["creator_side"] is None
        assert waiting["participants"] == {"attackers": None, "defenders": None}
    chosen = "defenders" if side == "random" else side
    game = rig.core.mutate(rig.credentials["Keeper"], waiting["id"], 0, "join")["game"]
    assert len(calls) == (1 if side == "random" else 0)
    assert game["creator_side"] == game["match"]["first_creator_side"] == chosen
    assert game["participants"][chosen]["account_id"] == rig.accounts["Archer"]["id"]
    assert game["position"]["turn"] == "defenders"
    assert game["match"]["mode"] == mode
    assert len(game["match"]["games"]) == (2 if mode == "simultaneous" else 1)
    if mode == "simultaneous":
        second = rig.core.read(rig.credentials["Visitor"], game["match"]["games"][1]["id"])
        assert second["creator_side"] != chosen
        assert second["participants"][chosen]["account_id"] == rig.accounts["Keeper"]["id"]
        assert second["started_at"] == game["started_at"]
        assert second["deadline"] == game["deadline"]
        assert second["status"] == "active" and second["version"] == 1
    before = dump(rig)
    with pytest.raises(OnlineError):
        rig.core.mutate(rig.credentials["Visitor"], game["id"], 0, "join")
    assert dump(rig) == before
    with rig.store.connection() as c:
        with pytest.raises(sqlite3.IntegrityError):
            c.execute(
                "UPDATE online_matches SET side_selection='attackers' WHERE id=?",
                (game["match"]["id"],),
            ) if side != "attackers" else c.execute(
                "UPDATE online_matches SET mode='sequential' WHERE id=?", (game["match"]["id"],)
            ) if mode != "sequential" else c.execute(
                "UPDATE online_matches SET mode='single' WHERE id=?", (game["match"]["id"],)
            )


def test_sequential_return_game_starts_on_completion_with_swapped_fixed_seats(rig):
    waiting = match(rig, "sequential", "defenders")
    first = rig.core.mutate(rig.credentials["Keeper"], waiting["id"], 0, "join")["game"]
    assert len(first["match"]["games"]) == 1
    rig.clock.value += timedelta(seconds=10, microseconds=3)
    # Verification is an admission gate for the match, not a second admission
    # requirement for its already agreed return game.
    with rig.store.connection() as c:
        c.execute(
            "UPDATE accounts SET email_verified_at=NULL WHERE id IN (?,?)",
            (rig.accounts["Archer"]["id"], rig.accounts["Keeper"]["id"]),
        )
    finished = rig.core.mutate(actor(rig, first), first["id"], 1, "concede")["game"]
    assert finished["status"] == "completed" and len(finished["match"]["games"]) == 2
    second = rig.core.read(rig.credentials["Archer"], finished["match"]["games"][1]["id"])
    assert second["creator_side"] == "attackers"
    assert second["participants"]["attackers"]["account_id"] == rig.accounts["Archer"]["id"]
    assert second["started_at"] == rig.clock.value.isoformat().replace("+00:00", "Z")
    assert second["deadline"] == (rig.clock.value + timedelta(seconds=60)).isoformat().replace(
        "+00:00", "Z"
    )
    assert second["position"]["ply"] == 0 and second["result"] is None
    with pytest.raises(OnlineError):
        rig.core.mutate(actor(rig, first), first["id"], 1, "concede")
    rig.core.mutate(actor(rig, second), second["id"], 1, "concede")
    assert len(rig.core.listing(rig.credentials["Archer"])["games"]) == 2
    assert rig.core.statistics(rig.credentials["Archer"])["played"] == 2


def test_paused_timeout_does_not_start_return_game_or_shift_first_deadline(rig):
    first = match(rig, "sequential", "attackers")
    first = rig.core.mutate(rig.credentials["Keeper"], first["id"], 0, "join")["game"]
    rig.clock.value += timedelta(days=1)
    assert rig.core.sweep()["blocked"] == 1
    assert len(rig.core.listing(rig.credentials["Archer"])["games"]) == 1
    resume(rig)
    assert rig.core.sweep()["finalized"] == 1
    first = rig.core.read(rig.credentials["Archer"], first["id"])
    second = rig.core.read(rig.credentials["Keeper"], first["match"]["games"][1]["id"])
    assert first["result"]["completed_at"] != second["started_at"]
    assert second["started_at"] == rig.clock.value.isoformat().replace("+00:00", "Z")
    assert second["deadline"] == (rig.clock.value + timedelta(seconds=60)).isoformat().replace(
        "+00:00", "Z"
    )


@pytest.mark.parametrize("action", ["cancel", "void"])
def test_random_waiting_cancel_and_prestart_void_never_assign_sides_or_create_return(rig, action):
    first = match(rig, "sequential")
    result = rig.core.mutate(
        rig.credentials["Archer" if action == "cancel" else "Odin"],
        first["id"],
        0,
        action,
        note="Private cancellation reason",
    )["game"]
    assert result["creator_side"] is None
    assert result["participants"] == {"attackers": None, "defenders": None}
    assert result["result"] is None and len(result["match"]["games"]) == 1


@pytest.mark.parametrize("mode,action", [("simultaneous", "join"), ("sequential", "concede")])
def test_return_creation_is_atomic_with_join_or_first_result(rig, mode, action):
    game = match(rig, mode, "attackers")
    if action == "concede":
        game = rig.core.mutate(rig.credentials["Keeper"], game["id"], 0, "join")["game"]
    before = dump(rig)
    with rig.store.connection() as c:
        c.execute(
            "CREATE TRIGGER fail_return BEFORE INSERT ON online_games WHEN NEW.leg=2 BEGIN SELECT RAISE(ABORT,'injected return failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        rig.core.mutate(
            rig.credentials["Keeper"] if action == "join" else actor(rig, game),
            game["id"],
            game["version"],
            action,
        )
    assert dump(rig) == before


def test_two_process_random_parallel_joins_create_only_one_pair(rig):
    first = match(rig, "simultaneous")
    outcomes = race(
        rig, first, ("join", rig.credentials["Keeper"]), ("join", rig.credentials["Visitor"])
    )
    assert sorted(outcomes, key=str) == [409, "join"]
    games = rig.core.listing(rig.credentials["Archer"])["games"]
    assert len(games) == 2 and all(game["status"] == "active" for game in games)
    assert games[0]["creator_side"] != games[1]["creator_side"]
    assert (
        games[0]["participants"]["attackers"]["account_id"]
        == games[1]["participants"]["defenders"]["account_id"]
    )


def test_two_process_sequential_finalization_creates_only_one_return(rig):
    game = match(rig, "sequential", "attackers")
    game = rig.core.mutate(rig.credentials["Keeper"], game["id"], 0, "join")["game"]
    resume(rig)
    rig.clock.value += timedelta(seconds=61)
    race(rig, game, ("sweep", ""), ("concede", actor(rig, game)))
    with rig.store.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM online_games").fetchone()[0] == 2
        assert c.execute("SELECT COUNT(*) FROM online_results").fetchone()[0] == 1
    games = rig.core.listing(rig.credentials["Keeper"])["games"]
    assert {game["status"] for game in games} == {"active", "completed"}


def test_unknown_join_commit_outcome_reloads_both_parallel_games(rig, monkeypatch):
    game = match(rig, "simultaneous")
    original = rig.core._write

    @contextmanager
    def response_lost():
        with original() as transaction:
            yield transaction
        raise ConnectionError("response lost after pair activation")

    with monkeypatch.context() as patch:
        patch.setattr(rig.core, "_write", response_lost)
        with pytest.raises(ConnectionError):
            rig.core.mutate(rig.credentials["Keeper"], game["id"], 0, "join")
    with pytest.raises(OnlineError):
        rig.core.mutate(rig.credentials["Keeper"], game["id"], 0, "join")
    assert len(rig.core.read(rig.credentials["Keeper"], game["id"])["match"]["games"]) == 2


@pytest.mark.parametrize("mode", ["single", "sequential"])
def test_complete_standard_initial_game_through_real_engine_and_atomic_results(rig, mode):
    game = match(rig, mode, "attackers")
    game = rig.core.mutate(rig.credentials["Keeper"], game["id"], 0, "join")["game"]
    state, rng = new_game("brandubh"), random.Random(0)
    for _ in range(200):
        move = rng.choice(legal_moves(state))
        state = apply_move(state, move)
        game = rig.core.mutate(
            actor(rig, game),
            game["id"],
            game["version"],
            "move",
            source=move.source,
            target=move.target,
        )["game"]
        assert game["position"] == safe_position(state)
        if state.winner:
            break
    assert state.winner == "defenders" and state.reason == "king_escaped"
    assert game["status"] == "completed" and game["deadline"] is None
    assert game["result"]["winner"] == state.winner and game["result"]["move_count"] == state.ply
    assert rig.core.replay(rig.credentials["Visitor"], game["id"])["validated"]
    with rig.store.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM online_moves").fetchone()[0] == state.ply
        assert c.execute("SELECT COUNT(*) FROM online_results").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM online_games").fetchone()[0] == (
            2 if mode == "sequential" else 1
        )


def test_parallel_games_accept_independent_moves_at_the_same_time(rig):
    game = match(rig, "simultaneous", "attackers")
    game = rig.core.mutate(rig.credentials["Keeper"], game["id"], 0, "join")["game"]
    games = [rig.core.read(rig.credentials["Visitor"], leg["id"]) for leg in game["match"]["games"]]
    barrier = threading.Barrier(2)

    def play(current):
        move = legal_moves(new_game(current["ruleset"]))[0]
        barrier.wait(timeout=20)
        return rig.core.mutate(
            actor(rig, current), current["id"], 1, "move", source=move.source, target=move.target
        )["game"]

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(play, games))
    assert all(result["version"] == 2 and result["position"]["ply"] == 1 for result in results)
