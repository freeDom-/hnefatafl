"""Authenticated own-history reads; no caller-selected account or write route."""

import logging
import sqlite3
from typing import Literal

from fastapi import HTTPException, Query

from .online_core import OnlineError
from .history import RecordedHistory
from .personal import PersonalHistory
from .security_context import session_identity

Rule = Literal["brandubh", "tablut", "copenhagen"]
Status = Literal["waiting", "active", "completed", "cancelled", "voided"]
logger = logging.getLogger(__name__)


def attach_personal(app, online, *, replay):
    history = PersonalHistory(online, replay=replay)
    app.state.personal = history
    recorded = RecordedHistory(history)
    app.state.history = recorded

    def execute(action_name, action):
        try:
            credential = session_identity.get()
            if not credential:
                raise OnlineError(401, "Authentication required.")
            return action(credential)
        except OnlineError as error:
            raise HTTPException(error.status, str(error)) from None
        except (sqlite3.Error, ValueError, KeyError, IndexError, TypeError) as error:
            # Exception messages and causes can contain SQL/request values.
            # Retain the original type name and stack, with no values or chaining.
            logger.exception(
                "Personal history failed in %s (%s)",
                action_name,
                type(error).__name__,
                exc_info=(Exception, Exception(), error.__traceback__),
            )
            raise HTTPException(503, "Personal history unavailable. Retry shortly.") from None

    @app.get("/api/history/games")
    def recorded_games(
        limit: int = Query(default=25, ge=1, le=100),
        cursor: str | None = Query(default=None, min_length=1, max_length=512),
        category: Literal["online", "account-single-player"] | None = None,
        ruleset: Rule | None = None,
        player: str | None = Query(default=None, min_length=1, max_length=80),
        status: Literal["waiting", "active", "in_progress", "completed", "cancelled", "voided"]
        | None = None,
        winner: Literal["attackers", "defenders", "draw"] | None = None,
        dashboard: bool = False,
    ):
        return execute(
            "recorded_games",
            lambda credential: recorded.listing(
                credential,
                limit=limit,
                cursor=cursor,
                category=category,
                ruleset=ruleset,
                player=player,
                status=status,
                winner=winner,
                dashboard=dashboard,
            ),
        )

    @app.get("/api/history/{category}/{game_id}/replay")
    def recorded_review(category: Literal["online", "account-single-player"], game_id: str):
        return execute(
            "recorded_review", lambda credential: recorded.review(credential, category, game_id)
        )

    @app.get("/api/personal/single-player")
    def account_games(
        limit: int = Query(default=25, ge=1, le=100),
        cursor: str | None = Query(default=None, min_length=1, max_length=512),
        ruleset: Rule | None = None,
        status: Literal["in_progress", "completed"] | None = None,
        winner: Literal["attackers", "defenders", "draw"] | None = None,
    ):
        return execute(
            "account_games",
            lambda credential: history.single_player(
                credential,
                limit=limit,
                cursor=cursor,
                ruleset=ruleset,
                status=status,
                winner=winner,
            ),
        )

    @app.get("/api/personal/single-player/{game_id}/replay")
    def account_review(game_id: str):
        return execute(
            "account_review", lambda credential: history.single_replay(credential, game_id)
        )

    @app.get("/api/personal/online")
    def online_games(
        limit: int = Query(default=25, ge=1, le=100),
        cursor: str | None = Query(default=None, min_length=1, max_length=512),
        ruleset: Rule | None = None,
        section: Literal["current", "your_turn", "other_active", "waiting", "history"] = "current",
        status: Status | None = None,
        outcome: Literal["win", "loss", "draw", "abandoned"] | None = None,
    ):
        return execute(
            "online_games",
            lambda credential: history.online_games(
                credential,
                limit=limit,
                cursor=cursor,
                ruleset=ruleset,
                section=section,
                status=status,
                outcome=outcome,
            ),
        )

    @app.get("/api/personal/online/{game_id}")
    def online_detail(game_id: str):
        return execute(
            "online_detail",
            lambda credential: online.read(credential, game_id, participant_only=True),
        )

    @app.get("/api/personal/online/{game_id}/replay")
    def online_review(game_id: str):
        return execute(
            "online_review",
            lambda credential: online.replay(credential, game_id, participant_only=True),
        )
