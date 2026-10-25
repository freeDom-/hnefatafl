"""Deterministic contract tests; no database or concurrency guarantees claimed."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from hnefatafl.engine import Move, State, apply_move, legal_moves
from hnefatafl.lifecycle import (
    Actor,
    LifecycleError,
    Status,
    TimeControl,
    accept_move,
    cancel,
    concede,
    disconnect,
    expire,
    join,
    void,
    waiting,
)

NOW = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)
CREATOR, OPPONENT, STRANGER = Actor("creator"), Actor("opponent"), Actor("stranger")
ADMIN = Actor("admin", is_admin=True)


def lobby(rule="brandubh", side="attackers", control=TimeControl(60)):
    return waiting("game", CREATOR, rule, side, NOW, control)


def active(rule="brandubh", side="attackers"):
    return join(lobby(rule, side), OPPONENT, NOW + timedelta(days=1)).game


def moving_actor(game):
    return Actor(game.account_for(game.position.turn))


def after_move(game):
    return apply_move(game.position, legal_moves(game.position)[0])


def test_time_control_defaults_and_bounds():
    assert TimeControl().seconds == 259200
    assert TimeControl(60).seconds == 60
    assert TimeControl(1209600).seconds == 1209600


@pytest.mark.parametrize("seconds", [0, -1, 59, 1209601, True, 60.0, 60.5, "60", None])
def test_invalid_duration_is_unambiguous(seconds):
    with pytest.raises(LifecycleError):
        TimeControl(seconds)


@pytest.mark.parametrize("rule", ["brandubh", "tablut", "copenhagen"])
@pytest.mark.parametrize("side", ["attackers", "defenders"])
def test_activation_assigns_fixed_sides_and_starts_first_deadline(rule, side):
    game = lobby(rule, side)
    assert game.started_at is game.deadline is game.result is None
    start = NOW + timedelta(days=1)
    outcome = join(game, OPPONENT, start)
    started = outcome.game
    assert started.status == Status.ACTIVE and outcome.effects == ()
    assert started.started_at == start and started.deadline == start + timedelta(seconds=60)
    assert started.account_for(side) == CREATOR.account_id
    assert started.position.turn == ("defenders" if rule == "tablut" else "attackers")
    assert game.opponent is None  # Immutable input.
    with pytest.raises(LifecycleError):
        join(started, STRANGER, start)


def test_activation_preconditions_and_explicit_time():
    with pytest.raises(LifecycleError, match="own opponent"):
        join(lobby(), CREATOR, NOW)
    with pytest.raises(LifecycleError):
        waiting("game", CREATOR, "brandubh", "random", NOW)
    with pytest.raises(LifecycleError):
        waiting("", CREATOR, "brandubh", "attackers", NOW)
    with pytest.raises(LifecycleError):
        Actor("")
    with pytest.raises(ValueError):
        lobby("unknown")
    with pytest.raises(LifecycleError, match="timezone"):
        join(lobby(), OPPONENT, NOW.replace(tzinfo=None))
    with pytest.raises(LifecycleError, match="backwards"):
        join(lobby(), OPPONENT, NOW - timedelta(seconds=1))
    offset = NOW.astimezone(timezone(timedelta(hours=2)))
    assert join(lobby(), OPPONENT, offset).game.started_at.tzinfo == timezone.utc
    occupied = replace(lobby(), opponent="already assigned")
    with pytest.raises(LifecycleError):
        join(occupied, OPPONENT, NOW)


@pytest.mark.parametrize("actor", [OPPONENT, STRANGER, ADMIN])
def test_only_player_to_move_can_move_or_concede(actor):
    game = active()
    for action in (
        lambda: accept_move(game, actor, after_move(game), game.started_at),
        lambda: concede(game, actor, game.deadline),
    ):
        with pytest.raises(LifecycleError, match="player to move"):
            action()


@pytest.mark.parametrize("offset", [-1, 0, 1])
def test_move_deadline_boundary_and_renewal(offset):
    game = active()
    moment = game.deadline + timedelta(microseconds=offset)
    outcome = accept_move(game, CREATOR, after_move(game), moment)
    if offset < 0:
        assert outcome.game.status == Status.ACTIVE and outcome.effects == ()
        assert outcome.game.deadline == moment + timedelta(seconds=60)
        assert outcome.game.position.ply == 1 and outcome.game.account_for("attackers") == "creator"
        next_moment = moment + timedelta(seconds=7)
        second = accept_move(outcome.game, OPPONENT, after_move(outcome.game), next_moment)
        assert second.game.deadline == next_moment + timedelta(seconds=60)
    else:
        assert_timeout(outcome, game)
        assert outcome.game.position == game.position  # Late move was not applied.


def assert_timeout(outcome, original):
    result = outcome.game.result
    assert outcome.game.status == Status.COMPLETED and outcome.game.deadline is None
    assert outcome.effects == (result,)
    assert result.winner == "defenders" and result.termination_reason == "timeout"
    assert result.abandoned_account_id == "creator" and result.competitive_eligible
    assert result.completed_at == original.deadline and result.move_count == 0


@pytest.mark.parametrize("offset", [-1, 0, 1])
def test_timely_and_late_concession(offset):
    game = active()
    moment = game.deadline + timedelta(microseconds=offset)
    outcome = concede(game, CREATOR, moment)
    if offset < 0:
        assert outcome.game.result.termination_reason == "concession"
        assert outcome.game.result.abandoned_account_id is None
        assert outcome.game.result.winner == "defenders"
        assert outcome.game.result.completed_at == moment
    else:
        assert_timeout(outcome, game)
        assert outcome == expire(game, moment)


def test_expiry_noop_before_boundary_and_duplicate_finalization():
    game = active()
    assert expire(game, game.deadline - timedelta(microseconds=1)).game is game
    finished = expire(game, game.deadline)
    assert_timeout(finished, game)
    repeated = expire(finished.game, game.deadline + timedelta(days=2))
    assert repeated.game is finished.game and repeated.effects == ()
    with pytest.raises(LifecycleError, match="active"):
        concede(finished.game, CREATOR, game.deadline)
    assert finished.game.result.revision == 1
    assert expire(lobby(), NOW + timedelta(days=50)).game.status == Status.WAITING


def test_timeout_uses_side_to_move_not_creator_identity():
    game = active("tablut", "attackers")
    result = expire(game, game.deadline).game.result
    assert result.winner == "attackers" and result.abandoned_account_id == "opponent"


def test_engine_terminal_state_is_consumed_without_duplicating_rules():
    game = active("tablut", "defenders")
    board = ["."] * 81
    board[20], board[70] = "K", "A"
    position = State("tablut", tuple(board), "defenders")
    game = replace(game, position=position)
    escaped = apply_move(position, Move(20, 2))
    outcome = accept_move(game, CREATOR, escaped, game.started_at)
    result = outcome.game.result
    assert result.winner == "defenders" and result.termination_reason == "king_escaped"
    assert result.move_count == 1 and result.abandoned_account_id is None
    assert result.ruleset == "tablut" and result.ruleset_version == "1"
    assert result.category == "online" and result.revision == 1 and result.supersedes is None
    with pytest.raises(LifecycleError):
        accept_move(active(), CREATOR, active().position, active().started_at)


def test_draw_from_engine_is_a_completed_competitive_result():
    # Lifecycle consumes an engine result, independently of its rules computation.
    game = active()
    drawn = replace(after_move(game), winner="draw", reason="threefold_repetition")
    outcome = accept_move(game, CREATOR, drawn, game.started_at)
    assert outcome.game.result.winner == "draw"
    assert outcome.game.result.competitive_eligible
    assert outcome.game.result.abandoned_account_id is None


def test_disconnect_has_no_effect_and_does_not_postpone_expiry():
    game = active()
    assert disconnect(game, CREATOR).game is game
    assert disconnect(game, OPPONENT).effects == ()
    assert_timeout(expire(game, game.deadline), game)


def test_waiting_cancellation_has_no_accounting_effects():
    for actor in (OPPONENT, ADMIN):
        with pytest.raises(LifecycleError):
            cancel(lobby(), actor, NOW)
    cancelled = cancel(lobby(), CREATOR, NOW)
    assert cancelled.game.status == Status.CANCELLED and cancelled.effects == ()
    assert cancelled.game.result is cancelled.game.deadline is None
    with pytest.raises(LifecycleError):
        cancel(active(), CREATOR, active().started_at)


@pytest.mark.parametrize(
    "status", [Status.WAITING, Status.CANCELLED, Status.COMPLETED, Status.VOIDED]
)
def test_nonactive_states_reject_gameplay_and_terminal_states_reject_join(status):
    game = replace(lobby(), status=status)
    for action in (
        lambda: accept_move(game, CREATOR, after_move(game), NOW),
        lambda: concede(game, CREATOR, NOW),
    ):
        with pytest.raises(LifecycleError, match="active"):
            action()
    if status != Status.WAITING:
        with pytest.raises(LifecycleError):
            join(game, OPPONENT, NOW)


def test_administrative_void_replaces_result_and_repeats_without_effects():
    game = active()
    completed = expire(game, game.deadline).game
    for actor, note in ((CREATOR, "correction"), (ADMIN, "")):
        with pytest.raises(LifecycleError):
            void(completed, actor, game.deadline, note)
    voided = void(completed, ADMIN, game.deadline, "confirmed server outage")
    result = voided.game.result
    assert voided.game.status == Status.VOIDED and voided.effects == (result,)
    assert result.revision == 2 and result.supersedes == 1
    assert not result.competitive_eligible and result.abandoned_account_id is None
    assert result.winner is None and result.correction_note == "confirmed server outage"
    assert result.corrected_by == ADMIN.account_id
    assert completed.result.abandoned_account_id == "creator"
    assert void(voided.game, ADMIN, game.deadline, "retry").effects == ()
    assert void(lobby(), ADMIN, NOW, "invalid lobby").effects == ()
    active_void = void(game, ADMIN, game.started_at, "invalid game")
    assert active_void.game.result.revision == 1
    assert not active_void.game.result.competitive_eligible
