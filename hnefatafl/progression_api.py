"""Authenticated portrait catalog, own-achievement projection and portrait unlocks.

Selection lives with the CSRF-protected profile routes. Authoritative game and
analysis transactions award milestones; these reads never grant rewards.
"""

import logging
import sqlite3

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator

from .online_core import OnlineError
from .progression import Progression
from .security_context import session_identity

logger = logging.getLogger(__name__)


class PortraitSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    portrait_id: StrictStr = Field(min_length=1, max_length=64)

    @field_validator("portrait_id")
    @classmethod
    def identifier(cls, value):
        value = value.strip()
        if not 1 <= len(value) <= 64 or any(char.isspace() for char in value):
            raise ValueError("Invalid portrait ID")
        return value


class PortraitUnlockResponse(BaseModel):
    portrait_id: str
    newly_unlocked: bool


def attach_progression(app, online):
    progression = Progression(online)
    app.state.progression = progression

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
                "Progression failed in %s (%s)",
                action_name,
                type(error).__name__,
                exc_info=(Exception, Exception(), error.__traceback__),
            )
            raise HTTPException(503, "Progression unavailable. Retry shortly.") from None

    @app.get("/api/portraits/catalog")
    def portrait_catalog():
        return execute("portrait_catalog", progression.catalog)

    @app.post("/api/portraits/unlock", response_model=PortraitUnlockResponse)
    def unlock_portrait(body: PortraitSelection):
        return execute(
            "unlock_portrait", lambda credential: progression.unlock(credential, body.portrait_id)
        )

    @app.get("/api/personal/achievements")
    def achievements():
        return execute("achievements", progression.achievements)
