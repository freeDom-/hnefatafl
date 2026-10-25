"""Guest import uses canonical engine replay; never account/online state."""

from copy import deepcopy
import json

import pytest
from hnefatafl.engine import RULESETS, apply_move, legal_moves, new_game
from hnefatafl.guest import GuestError, GuestSession, MAX_BYTES, MAX_MOVES, validate_record

VERSION = "a" * 64


@pytest.mark.parametrize("rule", list(RULESETS))
def test_guest_human_ai_replay_round_trip(rule):
    session = GuestSession(VERSION)
    session.start(rule, RULESETS[rule].first_player)
    initial = new_game(rule)
    move = legal_moves(initial)[0]
    moved = session.play(computer=False, source=move.source, target=move.target)
    assert moved["position"]["board"] == list(apply_move(initial, move).board)
    after = session.play(computer=True)
    record = json.loads(json.dumps(after["record"]))
    assert set(record) == {
        "schema",
        "engine_version",
        "ruleset",
        "ruleset_version",
        "human_side",
        "moves",
        "cursor",
    }
    restored = GuestSession(VERSION).restore(record)
    assert restored == after and restored["position"]["ply"] == 2
    saved_end = deepcopy(after["record"])
    historical = session.seek(0)
    assert historical["position"]["board"] == list(initial.board)
    assert session.restore(historical["record"]) == historical
    with pytest.raises(GuestError):
        session.play(computer=False, source=move.source, target=move.target)
    assert session.seek(2)["record"] == saved_end


@pytest.mark.parametrize(
    "change",
    [
        {"schema": 2},
        {"schema": True},
        {"engine_version": "b" * 64},
        {"ruleset": "unknown"},
        {"ruleset_version": "999"},
        {"human_side": "admin"},
        {"cursor": True},
        {"cursor": -1},
        {"cursor": 1},
        {"moves": [{"source": True, "target": 3}]},
        {"moves": [{"source": 999, "target": 0}]},
        {"moves": [{"source": 3, "target": 4, "actor": "Odin"}]},
        {"moves": [{"source": 0, "target": 1}]},
        {"winner": "attackers"},
        {"board": ["K"]},
        {"id": "online-" + "a" * 32},
        {"csrf_token": "never accepted"},
        {"moves": [{"source": 3, "target": 1}] * (MAX_MOVES + 1)},
    ],
)
def test_guest_rejects_invalid_data_without_overwriting_current_session(change):
    session = GuestSession(VERSION)
    before = session.start("brandubh", "attackers")
    with pytest.raises((GuestError, ValueError)):
        session.restore({**before["record"], **change})
    assert session.snapshot() == before


def test_guest_rejects_post_terminal_moves_and_recomputes_the_winner():
    session = GuestSession(VERSION)
    initial = session.start("brandubh", "attackers")
    cycle = [
        {"source": 3, "target": 2},
        {"source": 17, "target": 16},
        {"source": 2, "target": 3},
        {"source": 16, "target": 17},
    ] * 2
    record = {**initial["record"], "moves": cycle, "cursor": len(cycle)}
    terminal = session.restore(record)
    assert terminal["live_winner"] == "draw"
    assert terminal["live_reason"] == "threefold_repetition"
    assert GuestSession(VERSION).restore(terminal["record"]) == terminal
    bad = {
        **terminal["record"],
        "moves": terminal["record"]["moves"] + [{"source": 3, "target": 1}],
    }
    bad["cursor"] = len(bad["moves"])
    with pytest.raises(GuestError):
        validate_record(bad, VERSION)


def test_guest_cannot_move_for_the_other_side_or_trust_arbitrary_state():
    session = GuestSession(VERSION)
    session.start("brandubh", "defenders")
    with pytest.raises(GuestError):
        session.play(computer=False, source=3, target=1)
    with pytest.raises(GuestError):
        session.restore({"ruleset": "brandubh", "position": new_game("brandubh").to_dict()})


def test_guest_bounds_utf8_bytes_before_replay():
    session = GuestSession(VERSION)
    before = session.start("brandubh", "attackers")
    with pytest.raises(GuestError, match="storage limit"):
        session.restore({**before["record"], "ruleset_version": "ä" * MAX_BYTES})
    assert session.snapshot() == before
