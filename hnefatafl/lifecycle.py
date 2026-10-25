"""Unintegrated online lifecycle contract; no IO, clock, scheduler or board rules.

Callers supply trusted account identities, server time and engine-validated states.
Effects are replacement result snapshots, not counter increments. Persisting them
exactly once requires the transaction described in docs/designs/online-phase0.md.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from enum import StrEnum

from .engine import RULESETS, State, new_game


class LifecycleError(ValueError):
    """An action does not satisfy the lifecycle contract."""


class Status(StrEnum):
    WAITING = "waiting"
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    VOIDED = "voided"


@dataclass(frozen=True)
class TimeControl:
    seconds: int = 3 * 24 * 60 * 60

    def __post_init__(self) -> None:
        # In particular, reject bool, numeric strings and fractional seconds.
        if type(self.seconds) is not int or not 60 <= self.seconds <= 14 * 24 * 60 * 60:
            raise LifecycleError("Time per move must be integer seconds from 60 to 1209600")


@dataclass(frozen=True)
class Actor:
    account_id: str
    is_admin: bool = False  # Trusted adapter input, never a client-supplied claim.

    def __post_init__(self) -> None:
        if not self.account_id.strip():
            raise LifecycleError("An account identity is required")


@dataclass(frozen=True)
class Result:
    game_id: str
    revision: int
    supersedes: int | None
    category: str
    ruleset: str
    ruleset_version: str
    attackers: str
    defenders: str
    started_at: datetime
    completed_at: datetime
    revised_at: datetime
    winner: str | None
    termination_reason: str
    move_count: int  # Accepted plies, including an engine-terminal move.
    competitive_eligible: bool
    abandoned_account_id: str | None
    correction_note: str | None = None
    corrected_by: str | None = None


@dataclass(frozen=True)
class Game:
    id: str
    creator: str
    creator_side: str
    control: TimeControl
    position: State
    updated_at: datetime
    status: Status = Status.WAITING
    opponent: str | None = None
    started_at: datetime | None = None
    deadline: datetime | None = None
    result: Result | None = None

    def account_for(self, side: str) -> str:
        if side not in ("attackers", "defenders"):
            raise LifecycleError("Unknown side")
        account = self.creator if side == self.creator_side else self.opponent
        if account is None:
            raise LifecycleError("Both players must be assigned")
        return account


@dataclass(frozen=True)
class Transition:
    game: Game
    effects: tuple[Result, ...] = ()


def _utc(now: datetime) -> datetime:
    if now.tzinfo is None or now.utcoffset() is None:
        raise LifecycleError("Time must be timezone aware")
    return now.astimezone(timezone.utc)


def _moment(game: Game, now: datetime) -> datetime:
    now = _utc(now)
    if now < game.updated_at:
        raise LifecycleError("Time cannot move backwards")
    return now


def waiting(
    game_id: str,
    creator: Actor,
    ruleset: str,
    creator_side: str,
    now: datetime,
    control: TimeControl = TimeControl(),
) -> Game:
    """Explicit side selection is a proposal; this module chooses no random side."""
    if not game_id.strip() or creator_side not in ("attackers", "defenders"):
        raise LifecycleError("A game identity and valid creator side are required")
    return Game(game_id, creator.account_id, creator_side, control, new_game(ruleset), _utc(now))


def join(game: Game, actor: Actor, now: datetime) -> Transition:
    now = _moment(game, now)
    if game.status != Status.WAITING or game.opponent is not None:
        raise LifecycleError("Only an unoccupied waiting game may start")
    if actor.account_id == game.creator:
        raise LifecycleError("The creator cannot join as their own opponent")
    return Transition(
        replace(
            game,
            opponent=actor.account_id,
            status=Status.ACTIVE,
            started_at=now,
            deadline=now + timedelta(seconds=game.control.seconds),
            updated_at=now,
        )
    )


def cancel(game: Game, actor: Actor, now: datetime) -> Transition:
    now = _moment(game, now)
    if game.status != Status.WAITING or actor.account_id != game.creator:
        raise LifecycleError("Only the creator may cancel a waiting game")
    return Transition(replace(game, status=Status.CANCELLED, updated_at=now))


def _finish(
    game: Game,
    now: datetime,
    winner: str,
    reason: str,
    abandoned: str | None = None,
) -> Transition:
    assert game.started_at is not None
    completed_at = game.deadline if reason == "timeout" else now
    assert completed_at is not None
    result = Result(
        game_id=game.id,
        revision=1,
        supersedes=None,
        category="online",
        ruleset=game.position.ruleset,
        ruleset_version=RULESETS[game.position.ruleset].version,
        attackers=game.account_for("attackers"),
        defenders=game.account_for("defenders"),
        started_at=game.started_at,
        completed_at=completed_at,
        revised_at=now,
        winner=winner,
        termination_reason=reason,
        move_count=game.position.ply,
        competitive_eligible=True,
        abandoned_account_id=abandoned,
    )
    return Transition(
        replace(game, status=Status.COMPLETED, deadline=None, result=result, updated_at=now),
        (result,),
    )


def expire(game: Game, now: datetime) -> Transition:
    """Waiting, not-yet-due and already terminal processing is a stable no-op."""
    now = _moment(game, now)
    if game.status != Status.ACTIVE:
        return Transition(game)
    assert game.deadline is not None
    if now < game.deadline:
        return Transition(game)
    loser = game.position.turn
    winner = "defenders" if loser == "attackers" else "attackers"
    return _finish(game, now, winner, "timeout", game.account_for(loser))


def _playing(game: Game, actor: Actor, now: datetime) -> datetime:
    now = _moment(game, now)
    if game.status != Status.ACTIVE:
        raise LifecycleError("Gameplay requires an active game")
    if actor.account_id != game.account_for(game.position.turn):
        raise LifecycleError("Only the player to move may act")
    return now


def accept_move(game: Game, actor: Actor, after: State, now: datetime) -> Transition:
    """Accept a trusted engine result; do not recompute move legality here.

    The future adapter must check expiry before engine work, then recheck under
    the transaction lock. A late eligible action returns the timeout transition.
    """
    now = _playing(game, actor, now)
    expired = expire(game, now)
    if expired.effects:
        return expired
    if (
        after.ruleset != game.position.ruleset
        or after.ply != game.position.ply + 1
        or after.turn == game.position.turn
        or (after.winner is not None and not after.reason)
    ):
        raise LifecycleError("Expected the next engine-validated state")
    moved = replace(game, position=after, updated_at=now)
    if after.winner is not None:
        assert after.reason is not None
        return _finish(moved, now, after.winner, after.reason)
    return Transition(replace(moved, deadline=now + timedelta(seconds=game.control.seconds)))


def concede(game: Game, actor: Actor, now: datetime) -> Transition:
    now = _playing(game, actor, now)
    expired = expire(game, now)
    if expired.effects:
        return expired
    winner = "defenders" if game.position.turn == "attackers" else "attackers"
    return _finish(game, now, winner, "concession")


def disconnect(game: Game, actor: Actor) -> Transition:
    """Connectivity has no lifecycle meaning, including while a deadline passes."""
    return Transition(game)


def void(game: Game, actor: Actor, now: datetime, note: str) -> Transition:
    """Replace a recorded result with an ineligible revision, retaining audit data.

    No winner correction/reopening API is implemented in this Phase 0 contract.
    """
    now = _moment(game, now)
    if not actor.is_admin or not note.strip():
        raise LifecycleError("Voiding requires an administrator and an audit note")
    if game.status == Status.VOIDED:
        return Transition(game)
    # Pre-start invalidation has no competitive result to reverse.
    if game.started_at is None:
        return Transition(replace(game, status=Status.VOIDED, deadline=None, updated_at=now))
    previous = game.result
    if previous is None:
        previous = _finish(game, now, "draw", "administrative_void").game.result
    assert previous is not None
    result = replace(
        previous,
        revision=previous.revision + 1 if game.result else 1,
        supersedes=previous.revision if game.result else None,
        revised_at=now,
        winner=None,
        termination_reason="administrative_void",
        competitive_eligible=False,
        abandoned_account_id=None,
        correction_note=note,
        corrected_by=actor.account_id,
    )
    return Transition(
        replace(game, status=Status.VOIDED, deadline=None, result=result, updated_at=now),
        (result,),
    )
