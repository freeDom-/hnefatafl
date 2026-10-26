"""Account record construction independent of authenticated creation policy.

HTTP creation policy belongs to the authenticated adapter, not this data builder.
"""

from datetime import datetime, timezone
from uuid import uuid4

from .engine import new_game


def game_record(ruleset, mode, human_side, players, seed, *, advisor=None):
    return {
        "id": uuid4().hex,
        "ruleset": ruleset,
        "ruleset_version": 1,
        "mode": mode,
        "human_side": human_side,
        "players": players,
        "advisor": advisor,
        "analysis_enabled": True,
        "seed": seed,
        "state": new_game(ruleset).to_dict(),
        "moves": [],
        "cursor": 0,
        "version": 0,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "ended_at": None,
        "recorded_result": None,
    }
