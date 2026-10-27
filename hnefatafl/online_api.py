"""Narrow authenticated online adapters; never accept an actor or client board."""

import sqlite3
from typing import Literal
from uuid import UUID

from fastapi import HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr

from .online_core import OnlineCore, OnlineError
from .online_settings import OnlineSettings
from .security_context import session_identity


class CreationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    ruleset: Literal["brandubh", "tablut", "copenhagen"] = "tablut"
    side_selection: Literal["attackers", "defenders", "random"]
    mode: Literal["single", "simultaneous", "sequential"] = "single"
    time_per_move: StrictInt = Field(default=259200, ge=60, le=1209600)
    rated: StrictBool = False


class ExpectedVersion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: StrictInt = Field(ge=0, le=2**63 - 2)


class OnlineMove(ExpectedVersion):
    source: StrictInt = Field(ge=0, le=120)
    target: StrictInt = Field(ge=0, le=120)


class VoidRequest(ExpectedVersion):
    note: StrictStr = Field(min_length=1, max_length=1000)


class GateRequest(VoidRequest):
    enabled: StrictBool


class RebuildRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ruleset: Literal["brandubh", "tablut", "copenhagen"] | None = None


def attach_online(app, auth, *, settings=None, clock=None):
    core = OnlineCore(auth, settings or OnlineSettings.environment(), clock)
    app.state.online = core

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
                503, "Online state could not be committed. Reload before retrying."
            ) from None

    @app.post("/api/online/games", status_code=201)
    def create(body: CreationRequest):
        return execute(
            lambda credential: core.create(
                credential,
                body.ruleset,
                body.side_selection,
                body.time_per_move,
                str(body.request_id),
                body.mode,
                body.rated,
            )
        )

    @app.get("/api/online/games")
    def listing(
        limit: int = Query(default=25, ge=1, le=100),
        offset: int = Query(default=0, ge=0, le=100000),
        section: Literal["all", "mine", "my_turn"] = "all",
    ):
        return execute(lambda credential: core.listing(credential, limit, offset, section=section))

    @app.get("/api/online/availability")
    def availability():
        return execute(core.availability)

    @app.get("/api/online/games/{game_id}")
    def read(game_id: str):
        return execute(lambda credential: core.read(credential, game_id))

    @app.post("/api/online/games/{game_id}/join")
    def join(game_id: str, body: ExpectedVersion):
        return execute(lambda credential: core.mutate(credential, game_id, body.version, "join"))

    @app.post("/api/online/games/{game_id}/cancel")
    def cancel(game_id: str, body: ExpectedVersion):
        return execute(lambda credential: core.mutate(credential, game_id, body.version, "cancel"))

    @app.post("/api/online/games/{game_id}/moves")
    def move(game_id: str, body: OnlineMove):
        return execute(
            lambda credential: core.mutate(
                credential, game_id, body.version, "move", source=body.source, target=body.target
            )
        )

    @app.post("/api/online/games/{game_id}/concede")
    def concede(game_id: str, body: ExpectedVersion):
        return execute(lambda credential: core.mutate(credential, game_id, body.version, "concede"))

    @app.post("/api/online/games/{game_id}/void")
    def void(game_id: str, body: VoidRequest):
        return execute(
            lambda credential: core.mutate(
                credential, game_id, body.version, "void", note=body.note
            )
        )

    @app.get("/api/online/games/{game_id}/replay")
    def replay(game_id: str):
        return execute(lambda credential: core.replay(credential, game_id))

    @app.get("/api/online/stats")
    def statistics():
        return execute(core.statistics)

    @app.get("/api/leaderboard")
    def leaderboard(
        ruleset: Literal["brandubh", "tablut", "copenhagen"] = "tablut",
        limit: int = Query(default=25, ge=1, le=100),
        offset: int = Query(default=0, ge=0, le=100000),
    ):
        return execute(lambda credential: core.leaderboard(credential, ruleset, limit, offset))

    @app.post("/api/online/ratings/rebuild")
    def rebuild_ratings(body: RebuildRequest):
        return execute(lambda credential: core.rebuild_ratings(credential, body.ruleset))

    @app.get("/api/online/timeout-gate")
    def gate():
        return execute(core.gate)

    @app.get("/api/online/monitoring")
    def monitoring():
        return execute(core.monitoring)

    @app.patch("/api/online/timeout-gate")
    def set_gate(body: GateRequest):
        return execute(
            lambda credential: core.set_gate(credential, body.version, body.enabled, body.note)
        )

    return core
