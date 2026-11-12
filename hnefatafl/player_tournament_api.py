"""Narrow authenticated adapters for player tournaments; never trust a client actor."""

import sqlite3
from datetime import datetime
from typing import Literal

from fastapi import HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr

from .online_core import OnlineError
from .player_tournaments import PlayerTournaments
from .security_context import session_identity


class TournamentCreation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: StrictStr = Field(min_length=1, max_length=120)
    ruleset: Literal["brandubh", "tablut", "copenhagen"] = "tablut"
    capacity: StrictInt = Field(ge=2, le=64)
    match_legs: StrictInt = Field(default=1, ge=1, le=5)
    time_per_move: StrictInt = Field(default=259200, ge=60, le=1209600)
    registration_deadline: datetime | None = None


class ExpectedVersion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: StrictInt = Field(ge=0, le=2**63 - 2)


class StartRequest(ExpectedVersion):
    seeds: list[StrictStr] | None = None


class RebuildRequest(ExpectedVersion):
    note: StrictStr = Field(min_length=1, max_length=1000)


class OverrideRequest(RebuildRequest):
    winner: StrictStr


def attach_player_tournaments(app, auth, online, *, clock=None):
    core = PlayerTournaments(auth, online, clock)
    app.state.player_tournaments = core

    def execute(action):
        try:
            credential = session_identity.get()
            if not credential:
                raise OnlineError(401, "Authentication required.")
            return action(credential)
        except OnlineError as error:
            raise HTTPException(error.status, str(error)) from None
        except (sqlite3.IntegrityError, sqlite3.OperationalError):
            raise HTTPException(
                503, "Tournament state could not be committed. Reload before retrying."
            ) from None

    @app.get("/api/player-tournaments")
    def listing(
        status: Literal["registration", "running", "complete", "cancelled"] | None = None,
        limit: int = Query(default=20, ge=1, le=50),
        cursor: str | None = None,
    ):
        return execute(
            lambda credential: core.listing(credential, status=status, limit=limit, cursor=cursor)
        )

    @app.post("/api/player-tournaments", status_code=201)
    def create(body: TournamentCreation):
        return execute(
            lambda credential: core.create(
                credential,
                name=body.name,
                ruleset=body.ruleset,
                capacity=body.capacity,
                match_legs=body.match_legs,
                time_per_move=body.time_per_move,
                registration_deadline=body.registration_deadline,
            )
        )

    @app.get("/api/player-tournaments/{tournament_id}")
    def detail(tournament_id: str):
        return execute(lambda credential: core.detail(credential, tournament_id))

    @app.get("/api/player-tournaments/{tournament_id}/bracket")
    def bracket(tournament_id: str):
        return execute(lambda credential: core.bracket(credential, tournament_id))

    @app.get("/api/player-tournaments/{tournament_id}/matches")
    def matches(
        tournament_id: str,
        limit: int = Query(default=50, ge=1, le=200),
        cursor: str | None = None,
    ):
        return execute(
            lambda credential: core.matches(credential, tournament_id, limit=limit, cursor=cursor)
        )

    @app.get("/api/player-tournaments/{tournament_id}/matches/{match_id}")
    def match(tournament_id: str, match_id: str):
        return execute(lambda credential: core.match(credential, tournament_id, match_id))

    @app.get("/api/player-tournaments/{tournament_id}/audit")
    def audit(tournament_id: str, limit: int = Query(default=50, ge=1, le=100)):
        return execute(lambda credential: core.audit(credential, tournament_id, limit=limit))

    @app.post("/api/player-tournaments/{tournament_id}/register")
    def register(tournament_id: str):
        return execute(lambda credential: core.register(credential, tournament_id))

    @app.post("/api/player-tournaments/{tournament_id}/withdraw")
    def withdraw(tournament_id: str):
        return execute(lambda credential: core.withdraw(credential, tournament_id))

    @app.post("/api/player-tournaments/{tournament_id}/settle")
    def settle(tournament_id: str):
        return execute(lambda credential: core.settle(credential, tournament_id))

    @app.post("/api/player-tournaments/{tournament_id}/start")
    def start(tournament_id: str, body: StartRequest):
        return execute(
            lambda credential: core.start(credential, tournament_id, body.version, body.seeds)
        )

    @app.post("/api/player-tournaments/{tournament_id}/cancel")
    def cancel(tournament_id: str, body: ExpectedVersion):
        return execute(lambda credential: core.cancel(credential, tournament_id, body.version))

    @app.post("/api/player-tournaments/{tournament_id}/rebuild")
    def rebuild(tournament_id: str, body: RebuildRequest):
        return execute(
            lambda credential: core.rebuild(credential, tournament_id, body.version, body.note)
        )

    @app.post("/api/player-tournaments/{tournament_id}/matches/{match_id}/override")
    def override(tournament_id: str, match_id: str, body: OverrideRequest):
        return execute(
            lambda credential: core.override(
                credential, tournament_id, match_id, body.version, body.winner, body.note
            )
        )

    return core
