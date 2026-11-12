"""Server-owned portrait and achievement catalogs with permanent award records.

This module is the single authority for stable portrait and achievement IDs, the
unlock relationship between them and the catalog versions published by the API.
``migrate_online`` mirrors both catalogs into the database on every startup so the
SQL reads can join them, and never invents a row for an existing account.

Awards are materialized idempotently from authoritative records only: rated
outcomes come from the applied rating ledger (so a withdrawn or corrected result
is never rewarded), local and vs-AI milestones from completed account games.
Nothing here accepts a client-supplied award, image URL or another account's
scope, and a milestone that was already confirmed is never revoked.
"""

from __future__ import annotations

import fcntl
import sqlite3
from dataclasses import dataclass

from .engine import RULESETS
from .online_core import OnlineError, instant, micros, stamp
from .personal import rated_streaks

# A catalog change (adding, renaming, retiring) bumps its version; awards record
# the achievement catalog version that granted them.
ACHIEVEMENT_CATALOG_VERSION = 1
PORTRAIT_CATALOG_VERSION = 1


@dataclass(frozen=True)
class Achievement:
    id: str
    name: str
    description: str
    category: str
    criterion: str
    scope: str | None
    threshold: int


@dataclass(frozen=True)
class Portrait:
    id: str
    name: str
    unlock_achievement_id: str | None
    retired: bool = False


# Counts are derived per read from authoritative records; nothing is cached in a
# per-account counter that a correction could not recompute.
_RATED_WINS = """SELECT count(*) FROM rating_applications a
    JOIN rating_events e ON e.game_id=a.game_id
    WHERE a.account_id=? AND e.state='applied' AND a.score=1.0"""
_LOCAL_COMPLETED = """SELECT count(*) FROM games
    WHERE category='local' AND owner_account_id=?
      AND json_extract(data,'$.recorded_result.winner') IS NOT NULL"""
_AI_DEFEATED = """SELECT count(*) FROM games
    WHERE category='account-single-player' AND owner_account_id=?
      AND json_extract(data,'$.mode')='human-ai'
      AND json_extract(data,'$.recorded_result.winner')=json_extract(data,'$.human_side')"""

# criterion -> (count SQL, scope column). A scoped criterion counts only the
# ruleset pool in its scope; the streak criterion is folded in Python because a
# best run is not a count of matching rows.
_CRITERIA: dict[str, tuple[str | None, str | None]] = {
    "rated_wins": (_RATED_WINS, "a.pool"),
    "rated_defender_wins": (_RATED_WINS + " AND e.defenders_id=a.account_id", None),
    "rated_win_streak_best": (None, None),
    "completed_local_games": (_LOCAL_COMPLETED, None),
    "defeated_ai_games": (_AI_DEFEATED, None),
}
_CATEGORIES = ("rated", "ruleset", "streak", "local", "ai")

ACHIEVEMENTS = (
    Achievement(
        "first-rated-win",
        "First Raid",
        "Win your first rated online game.",
        "rated",
        "rated_wins",
        None,
        1,
    ),
    Achievement(
        "rated-wins-10",
        "Veteran of Ten",
        "Win ten rated online games.",
        "rated",
        "rated_wins",
        None,
        10,
    ),
    Achievement(
        "rated-wins-25",
        "Saga-Worthy",
        "Win twenty-five rated online games.",
        "rated",
        "rated_wins",
        None,
        25,
    ),
    Achievement(
        "defender-hold",
        "Shield Wall",
        "Win a rated online game as the defender.",
        "rated",
        "rated_defender_wins",
        None,
        1,
    ),
    Achievement(
        "defender-wins-10",
        "Wall of Shields",
        "Win ten rated online games as the defender.",
        "rated",
        "rated_defender_wins",
        None,
        10,
    ),
    Achievement(
        "rated-win-streak-5",
        "Unbroken Line",
        "Reach a five-game rated win streak.",
        "streak",
        "rated_win_streak_best",
        None,
        5,
    ),
    Achievement(
        "rated-win-streak-10",
        "Champion of the Long Hall",
        "Reach a ten-game rated win streak.",
        "streak",
        "rated_win_streak_best",
        None,
        10,
    ),
    Achievement(
        "brandubh-initiate",
        "Brandubh Initiate",
        "Win a rated Brandubh game.",
        "ruleset",
        "rated_wins",
        "brandubh",
        1,
    ),
    Achievement(
        "tablut-initiate",
        "Tablut Initiate",
        "Win a rated Tablut game.",
        "ruleset",
        "rated_wins",
        "tablut",
        1,
    ),
    Achievement(
        "copenhagen-initiate",
        "Copenhagen Initiate",
        "Win a rated Copenhagen game.",
        "ruleset",
        "rated_wins",
        "copenhagen",
        1,
    ),
    Achievement(
        "local-first-game",
        "Fireside Game",
        "Finish a same-device local game.",
        "local",
        "completed_local_games",
        None,
        1,
    ),
    Achievement(
        "ai-first-victory",
        "Trial of Steel",
        "Beat a computer opponent in a finished single-player game.",
        "ai",
        "defeated_ai_games",
        None,
        1,
    ),
)

# ``shieldbearer`` is the always-available default: every other portrait is
# unlocked by exactly one achievement. ``local-first-game`` deliberately grants no
# portrait, so an achievement is not required to carry one.
PORTRAITS = (
    Portrait("shieldbearer", "Shieldbearer", None),
    Portrait("shieldmaiden", "Shieldmaiden", "first-rated-win"),
    Portrait("iron-helm", "Iron Helm", "defender-hold"),
    Portrait("wanderer", "Wanderer", "brandubh-initiate"),
    Portrait("skald", "Skald", "tablut-initiate"),
    Portrait("fjord-scout", "Fjord Scout", "copenhagen-initiate"),
    Portrait("raven-seer", "Raven Seer", "rated-wins-10"),
    Portrait("berserker", "Berserker", "rated-win-streak-5"),
    Portrait("one-eyed-jarl", "One-Eyed Jarl", "rated-win-streak-10"),
    Portrait("valkyrie", "Valkyrie", "defender-wins-10"),
    Portrait("einherjar", "Einherjar", "rated-wins-25"),
    Portrait("forge-keeper", "Forge Keeper", "ai-first-victory"),
)

PORTRAIT_BY_ACHIEVEMENT = {
    portrait.unlock_achievement_id: portrait.id
    for portrait in PORTRAITS
    if portrait.unlock_achievement_id is not None
}


def _validate_catalogs() -> None:
    """Fail at import for a malformed code catalog instead of seeding a bad row."""
    identifiers = [achievement.id for achievement in ACHIEVEMENTS]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("Achievement IDs must be unique")
    for achievement in ACHIEVEMENTS:
        if achievement.criterion not in _CRITERIA:
            raise ValueError(f"Unknown achievement criterion {achievement.criterion!r}")
        if achievement.category not in _CATEGORIES:
            raise ValueError(f"Unknown achievement category {achievement.category!r}")
        if achievement.threshold < 1:
            raise ValueError(f"Achievement {achievement.id!r} needs a positive threshold")
        scope_column = _CRITERIA[achievement.criterion][1]
        if achievement.scope is not None and (
            scope_column is None or achievement.scope not in RULESETS
        ):
            raise ValueError(f"Achievement {achievement.id!r} has an unsupported scope")
    portrait_ids = [portrait.id for portrait in PORTRAITS]
    if len(portrait_ids) != len(set(portrait_ids)):
        raise ValueError("Portrait IDs must be unique")
    defaults = [
        portrait
        for portrait in PORTRAITS
        if portrait.unlock_achievement_id is None and not portrait.retired
    ]
    if len(defaults) != 1:
        raise ValueError("Exactly one always-available default portrait is required")
    for portrait in PORTRAITS:
        if portrait.unlock_achievement_id is not None and portrait.unlock_achievement_id not in set(
            identifiers
        ):
            raise ValueError(f"Portrait {portrait.id!r} unlocks an unknown achievement")


_validate_catalogs()


def seed_catalog(c: sqlite3.Connection) -> None:
    """Mirror both code-owned catalogs into the database, idempotently.

    The code catalog is authoritative, so a changed name, unlock or retirement
    updates the stored row on the next startup without renumbering any ID.
    """
    c.executemany(
        """INSERT INTO achievement_catalog(
             id,catalog_version,ordinal,name,description,category,criterion,scope,threshold)
           VALUES(?,?,?,?,?,?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET
             catalog_version=excluded.catalog_version,ordinal=excluded.ordinal,name=excluded.name,
             description=excluded.description,category=excluded.category,
             criterion=excluded.criterion,scope=excluded.scope,threshold=excluded.threshold""",
        [
            (
                achievement.id,
                ACHIEVEMENT_CATALOG_VERSION,
                index,
                achievement.name,
                achievement.description,
                achievement.category,
                achievement.criterion,
                achievement.scope,
                achievement.threshold,
            )
            for index, achievement in enumerate(ACHIEVEMENTS)
        ],
    )
    # The always-available default is written last: retiring the previous default
    # and introducing its replacement then never leaves two non-retired defaults
    # at the same instant, which the partial unique index rejects.
    ordered = sorted(
        enumerate(PORTRAITS),
        key=lambda entry: entry[1].unlock_achievement_id is None and not entry[1].retired,
    )
    c.executemany(
        """INSERT INTO portrait_catalog(
             id,catalog_version,ordinal,name,unlock_achievement_id,retired)
           VALUES(?,?,?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET
             catalog_version=excluded.catalog_version,ordinal=excluded.ordinal,name=excluded.name,
             unlock_achievement_id=excluded.unlock_achievement_id,retired=excluded.retired""",
        [
            (
                portrait.id,
                PORTRAIT_CATALOG_VERSION,
                index,
                portrait.name,
                portrait.unlock_achievement_id,
                int(portrait.retired),
            )
            for index, portrait in ordered
        ],
    )


def default_portrait_id(c: sqlite3.Connection) -> str:
    """The always-available default; a retired default is never offered."""
    row = c.execute(
        "SELECT id FROM portrait_catalog WHERE unlock_achievement_id IS NULL AND retired=0 "
        "ORDER BY ordinal,id LIMIT 1"
    ).fetchone()
    if row is None:
        raise OnlineError(503, "The portrait catalog is unavailable.")
    return row["id"]


def selected_portrait_id(c: sqlite3.Connection, account_id: str) -> str:
    """The effective published portrait: the stored selection or the default.

    A stored selection that the current catalog retired is never published, so
    the default is returned instead of a portrait the gallery no longer offers.
    """
    row = c.execute(
        """SELECT s.portrait_id FROM account_portrait_selection s
           JOIN portrait_catalog p ON p.id=s.portrait_id AND p.retired=0
           WHERE s.account_id=?""",
        (account_id,),
    ).fetchone()
    return row["portrait_id"] if row is not None else default_portrait_id(c)


class Progression:
    """Portrait catalog, own achievement progress and portrait selection."""

    def __init__(self, online):
        self.online, self.store = online, online.store

    @staticmethod
    def _catalog(c: sqlite3.Connection) -> dict:
        rows = c.execute(
            "SELECT id,name,unlock_achievement_id FROM portrait_catalog "
            "WHERE retired=0 ORDER BY ordinal,id"
        ).fetchall()
        return {
            "catalog_version": PORTRAIT_CATALOG_VERSION,
            "default_portrait_id": default_portrait_id(c),
            "items": [
                {
                    "id": row["id"],
                    "name": row["name"],
                    "unlock_achievement_id": row["unlock_achievement_id"],
                }
                for row in rows
            ],
        }

    def catalog(self, credential: str) -> dict:
        """Authenticated catalog metadata; never account-specific entitlement."""
        with self.store.connection() as c:
            c.execute("BEGIN")
            self.online._identity(c, credential)
            return self._catalog(c)

    @staticmethod
    def _measure(c: sqlite3.Connection, account_id: str, achievement, cache: dict) -> int:
        key = (achievement.criterion, achievement.scope)
        if key not in cache:
            statement, scope_column = _CRITERIA[achievement.criterion]
            if statement is None:
                # A best run is folded from the ordered ledger, not counted.
                cache[key] = rated_streaks(c, account_id)[2]
            else:
                values = [account_id]
                if achievement.scope is not None:
                    if scope_column is None:
                        raise ValueError(f"{achievement.id!r} cannot be scoped")
                    statement += f" AND {scope_column}=?"
                    values.append(achievement.scope)
                cache[key] = c.execute(statement, values).fetchone()[0]
        return cache[key]

    def _materialize(self, c: sqlite3.Connection, account_id: str, now: int) -> None:
        """Confirm every milestone the authoritative records already prove.

        Idempotent: a repeated read, a duplicated result update or a rebuilt pool
        can only re-insert the same primary keys, so an award is written once and
        an award that was already granted is never removed by a later correction.
        """
        cache: dict = {}
        for achievement in ACHIEVEMENTS:
            if self._measure(c, account_id, achievement, cache) >= achievement.threshold:
                c.execute(
                    """INSERT OR IGNORE INTO achievement_awards(
                         account_id,achievement_id,awarded_at,catalog_version)
                       VALUES(?,?,?,?)""",
                    (account_id, achievement.id, now, ACHIEVEMENT_CATALOG_VERSION),
                )
        # Entitlements mirror the award ledger and self-heal: every currently
        # offered portrait of an already-awarded achievement is granted, so a
        # portrait added to the catalog later still becomes selectable.
        c.execute(
            """INSERT OR IGNORE INTO account_portraits(
                 account_id,portrait_id,granted_at,source_achievement_id)
               SELECT ?,p.id,?,p.unlock_achievement_id FROM portrait_catalog p
               JOIN achievement_awards a ON a.achievement_id=p.unlock_achievement_id
               WHERE a.account_id=? AND p.retired=0 AND p.unlock_achievement_id IS NOT NULL""",
            (account_id, now, account_id),
        )

    def achievements(self, credential: str) -> dict:
        """Complete own progress; a fresh account receives every item at zero."""
        # Reading progress confirms awards, so it is a write and obeys the same
        # deployment freeze as OnlineCore._write and the HTTP middleware.
        with self.online.auth.gate_path.open("rb") as gate:
            try:
                fcntl.flock(gate, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                raise OnlineError(503, "Deployment in progress. Please retry shortly.") from None
            with self.store.connection() as c:
                # It also takes the writer lock and commits the confirmations
                # together with the projection it returns.
                c.execute("BEGIN IMMEDIATE")
                account = self.online._identity(c, credential)
                now = micros(self.online.clock())
                self._materialize(c, account["id"], now)
                awards = {
                    row["achievement_id"]: row["awarded_at"]
                    for row in c.execute(
                        "SELECT achievement_id,awarded_at FROM achievement_awards WHERE account_id=?",
                        (account["id"],),
                    )
                }
                cache: dict = {}
                items = []
                for achievement in ACHIEVEMENTS:
                    awarded_at = awards.get(achievement.id)
                    items.append(
                        {
                            "id": achievement.id,
                            "name": achievement.name,
                            "description": achievement.description,
                            "category": achievement.category,
                            "scope": achievement.scope,
                            "threshold": achievement.threshold,
                            "progress": self._measure(c, account["id"], achievement, cache),
                            "unlocked": awarded_at is not None,
                            "awarded_at": stamp(instant(awarded_at))
                            if awarded_at is not None
                            else None,
                            "portrait_id": PORTRAIT_BY_ACHIEVEMENT.get(achievement.id),
                        }
                    )
                return {
                    "catalog_version": ACHIEVEMENT_CATALOG_VERSION,
                    "portrait_catalog_version": PORTRAIT_CATALOG_VERSION,
                    "items": items,
                    "summary": {
                        "unlocked": sum(1 for item in items if item["unlocked"]),
                        "total": len(items),
                    },
                    "as_of": stamp(instant(now)),
                }

    def select(self, c: sqlite3.Connection, account_id: str, portrait_id: str) -> str:
        """Select an earned, currently offered portrait inside the caller's write.

        Unknown, retired and not-yet-earned IDs are rejected; there is no field
        for a client-supplied image, URL or entitlement.
        """
        now = micros(self.online.clock())
        self._materialize(c, account_id, now)
        row = c.execute(
            "SELECT unlock_achievement_id,retired FROM portrait_catalog WHERE id=?",
            (portrait_id,),
        ).fetchone()
        if row is None or row["retired"]:
            raise OnlineError(422, "Unknown or unavailable portrait.")
        if (
            row["unlock_achievement_id"] is not None
            and not c.execute(
                "SELECT 1 FROM account_portraits WHERE account_id=? AND portrait_id=?",
                (account_id, portrait_id),
            ).fetchone()
        ):
            raise OnlineError(403, "This portrait is not unlocked yet.")
        c.execute(
            """INSERT INTO account_portrait_selection(account_id,portrait_id,updated_at)
               VALUES(?,?,?) ON CONFLICT(account_id) DO UPDATE SET
                 portrait_id=excluded.portrait_id,updated_at=excluded.updated_at""",
            (account_id, portrait_id, now),
        )
        return portrait_id
