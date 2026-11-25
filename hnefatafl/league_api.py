"""Explicit authenticated HTTP interfaces for seasonal League play."""

import sqlite3
from typing import Annotated, Literal
from uuid import UUID

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    StringConstraints,
)

from .league_administration import LeagueAdministration
from .league_views import LeagueCursors, LeagueError, LeagueViews
from .league_worker import LeagueWorker
from .leagues import Leagues
from .online_core import DeploymentLocked, OnlineCore, OnlineError
from .security_context import session_identity

Ruleset = Literal["brandubh", "tablut", "copenhagen"]
SeasonId = Annotated[str, StringConstraints(pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$")]


class LeagueRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RulesetQuery(LeagueRequest):
    ruleset: Ruleset = "tablut"


class SeasonQuery(RulesetQuery):
    season: SeasonId | None = None


class PageQuery(LeagueRequest):
    limit: int = Field(default=20, ge=1, le=50)
    cursor: str | None = Field(default=None, min_length=1, max_length=512)


class HealthQuery(LeagueRequest):
    stale_cursor: str | None = Field(default=None, min_length=1, max_length=512)


class SeasonPageQuery(SeasonQuery, PageQuery):
    pass


class QueueRequest(LeagueRequest):
    request_id: UUID


class JoinRequest(QueueRequest):
    ruleset: Ruleset
    expected_generation: UUID | None = None


class AdministrativeReason(LeagueRequest):
    reason: StrictStr = Field(min_length=1, max_length=1000)


class VersionedReason(AdministrativeReason):
    version: StrictInt = Field(ge=0, le=2**63 - 2)


class MatchmakingChange(VersionedReason):
    paused: StrictBool


class StaleCancellation(AdministrativeReason, QueueRequest):
    pass


DOMAIN_CODES = {
    401: "league_authentication_required",
    403: "league_ineligible",
    404: "league_request_missing",
    409: "league_conflict",
    422: "league_invalid_request",
    429: "league_rate_limited",
    503: "league_unavailable",
}


def attach_leagues(app: FastAPI, online: OnlineCore) -> LeagueWorker:
    core = Leagues(online)
    views = LeagueViews(core, LeagueCursors())
    administration = LeagueAdministration(views)
    worker = LeagueWorker(administration)
    app.state.leagues = core
    app.state.league_views = views
    app.state.league_administration = administration
    app.state.league_worker = worker

    def execute(action):
        try:
            credential = session_identity.get()
            if credential is None:
                raise OnlineError(401, "Authentication required.")
            return action(credential)
        except LeagueError as error:
            return JSONResponse({"detail": str(error), "code": error.code}, error.status)
        except DeploymentLocked as error:
            return JSONResponse(
                {"detail": str(error), "code": "league_deployment_locked"}, error.status
            )
        except OnlineError as error:
            code = DOMAIN_CODES.get(error.status, "league_unavailable")
            return JSONResponse({"detail": str(error), "code": code}, error.status)
        except (sqlite3.IntegrityError, sqlite3.OperationalError):
            return JSONResponse(
                {
                    "detail": "League state is unavailable. Reload before retrying.",
                    "code": "league_unavailable",
                },
                503,
            )

    @app.get("/api/leagues/current")
    def current(query: Annotated[RulesetQuery, Query()]):
        return execute(lambda credential: views.current(credential, query.ruleset))

    @app.get("/api/leagues/seasons")
    def seasons(query: Annotated[PageQuery, Query()]):
        return execute(lambda credential: views.seasons(credential, query.limit, query.cursor))

    @app.get("/api/leagues/progress")
    def progress(query: Annotated[SeasonQuery, Query()]):
        return execute(lambda credential: views.progress(credential, query.ruleset, query.season))

    @app.get("/api/leagues/standings")
    def standings(query: Annotated[SeasonPageQuery, Query()]):
        return execute(
            lambda credential: views.standings(
                credential, query.ruleset, query.season, query.limit, query.cursor
            )
        )

    @app.get("/api/leagues/history")
    def history(query: Annotated[SeasonPageQuery, Query()]):
        return execute(
            lambda credential: views.history(
                credential, query.ruleset, query.season, query.limit, query.cursor
            )
        )

    @app.get("/api/leagues/queue")
    def queue(query: Annotated[LeagueRequest, Query()]):
        return execute(core.queue_status)

    @app.post("/api/leagues/queue")
    def join(body: JoinRequest):
        generation = body.expected_generation
        return execute(
            lambda credential: core.join(
                credential,
                body.ruleset,
                str(body.request_id),
                expected_generation=str(generation) if generation is not None else None,
                guarded="expected_generation" in body.model_fields_set,
            )
        )

    @app.post("/api/leagues/queue/cancel")
    def cancel(body: QueueRequest):
        return execute(lambda credential: core.cancel(credential, str(body.request_id)))

    @app.get("/api/leagues/active-game")
    def active_game(query: Annotated[LeagueRequest, Query()]):
        return execute(views.active_game)

    @app.get("/api/leagues/admin/health")
    def health(query: Annotated[HealthQuery, Query()]):
        return execute(
            lambda credential: {
                **administration.health(credential, query.stale_cursor),
                "worker": worker.health(),
            }
        )

    @app.get("/api/leagues/admin/audit")
    def audit(query: Annotated[PageQuery, Query()]):
        return execute(
            lambda credential: administration.audit(credential, query.limit, query.cursor)
        )

    @app.post("/api/leagues/admin/matchmaking")
    def matchmaking(body: MatchmakingChange):
        return execute(
            lambda credential: administration.configure(
                credential, body.version, body.paused, body.reason
            )
        )

    @app.post("/api/leagues/admin/queue/cancel")
    def cancel_stale(body: StaleCancellation):
        return execute(
            lambda credential: administration.cancel_stale(
                credential, str(body.request_id), body.reason
            )
        )

    @app.post("/api/leagues/admin/seasons/{season_id}/rebuild", status_code=202)
    def rebuild(season_id: SeasonId, body: VersionedReason):
        return execute(
            lambda credential: administration.rebuild(
                credential, season_id, body.version, body.reason
            )
        )

    return worker
