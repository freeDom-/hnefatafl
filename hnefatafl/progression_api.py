"""Authenticated portrait catalog and own-achievement progress adapters.

Both reads are self-scoped: they take the session credential from the trusted
request context and never accept an account, entitlement or award from the
client. Selecting a portrait is a profile mutation and lives with the other
profile routes.
"""

import logging
import sqlite3

from fastapi import HTTPException

from .online_core import OnlineError
from .progression import Progression
from .security_context import session_identity

logger = logging.getLogger(__name__)


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

    @app.get("/api/personal/achievements")
    def achievements():
        return execute("achievements", progression.achievements)
