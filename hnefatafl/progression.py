"""Server-owned portrait and achievement catalogs with permanent award records.

This module is the single authority for stable portrait and achievement IDs, the
unlock relationship between them and the catalog versions published by the API.
``migrate_online`` mirrors both catalogs into the database on every startup so the
SQL reads can join them. A controlled schema migration reconciles historical records.

Awards are materialized idempotently from authoritative records only: rated
outcomes come from the applied rating ledger; local and vs-AI milestones come
from completed account games. Corrections change future eligibility, never earned rewards.
Nothing here accepts a client-supplied award, image URL or another account's
scope, and an earned milestone is never revoked.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass

from . import league_policy as policy
from .engine import RULESETS
from .online_core import OnlineError, instant, micros, stamp
from .personal import rated_streaks

# A catalog change (adding, renaming, retiring) bumps its version; awards record
# the achievement catalog version that granted them.
ACHIEVEMENT_CATALOG_VERSION = 4
PORTRAIT_CATALOG_VERSION = 2


@dataclass(frozen=True)
class Achievement:
    id: str
    name: str
    description: str
    category: str
    criterion: str
    scope: str | None
    threshold: int
    points: int = 10
    family: str = "local-first-game"
    tier: int = 1
    ruleset: str | None = None
    league_tier: str | None = None
    league_tier_name: str | None = None
    minimum_points: int | None = None
    family_name: str | None = None


@dataclass(frozen=True)
class Portrait:
    id: str
    name: str
    unlock_achievement_id: str | None
    retired: bool = False


# Counts are derived per read from authoritative records; nothing is cached in a
# per-account counter that a correction could not recompute.
_RATED_WINS = """SELECT a.pool,
    CASE WHEN e.attackers_id=a.account_id THEN 'attackers' ELSE 'defenders' END AS side,
    count(*) AS wins FROM rating_applications a
    JOIN rating_events e ON e.game_id=a.game_id AND e.pool=a.pool
    WHERE a.account_id=? AND e.state='applied' AND a.score=1.0
    GROUP BY a.pool,side"""
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
    "rated_defender_wins": (_RATED_WINS, None),
    "rated_attacker_wins": (_RATED_WINS, None),
    "analyzed_replays": (
        "SELECT count(*) FROM replay_analysis_completions WHERE account_id=?",
        None,
    ),
    "rated_win_streak_best": (None, None),
    "completed_local_games": (_LOCAL_COMPLETED, None),
    "defeated_ai_games": (_AI_DEFEATED, None),
    "league_tier": (None, None),
}
_CATEGORIES = ("rated", "ruleset", "streak", "local", "ai", "analysis", "league")

# Incremental points: 760 per ordinary family, 200 for streaks.
MILESTONES = (1, 3, 10, 25, 50, 100, 250, 500, 1000)
TIER_POINTS = (10, 20, 30, 40, 60, 80, 120, 160, 240)
STREAK_MILESTONES = (1, 3, 5, 10)
STREAK_POINTS = (10, 30, 60, 100)
# League achievements reuse the ordinary tiered scoring model for their five
# authoritative league tiers. The tier thresholds and display names come from
# the league policy, never from a second configuration.
LEAGUE_TIER_POINTS = TIER_POINTS[: len(policy.TIERS)]
# family, name, category, criterion, scope, legacy threshold IDs
FAMILIES: tuple[tuple[str, str, str, str, str | None, dict[int, str]], ...] = (
    ("rated-wins", "Rated online victories", "rated", "rated_wins", None, {1: "first-rated-win"}),
    (
        "brandubh-wins",
        "Rated Brandubh victories",
        "ruleset",
        "rated_wins",
        "brandubh",
        {1: "brandubh-initiate"},
    ),
    (
        "tablut-wins",
        "Rated Tablut victories",
        "ruleset",
        "rated_wins",
        "tablut",
        {1: "tablut-initiate"},
    ),
    (
        "copenhagen-wins",
        "Rated Copenhagen victories",
        "ruleset",
        "rated_wins",
        "copenhagen",
        {1: "copenhagen-initiate"},
    ),
    (
        "defender-wins",
        "Rated victories as Defender",
        "rated",
        "rated_defender_wins",
        None,
        {1: "defender-hold"},
    ),
    ("attacker-wins", "Rated victories as Attacker", "rated", "rated_attacker_wins", None, {}),
    (
        "ai-wins",
        "Single-player victories against AI",
        "ai",
        "defeated_ai_games",
        None,
        {1: "ai-first-victory"},
    ),
    ("analyzed-replays", "Replays analyzed", "analysis", "analyzed_replays", None, {}),
    ("rated-win-streak", "Best rated win streak", "streak", "rated_win_streak_best", None, {}),
)
ACHIEVEMENTS = (
    tuple(
        Achievement(
            legacy.get(threshold, f"{family}-{threshold}"),
            name,
            f"{name}: {threshold}.",
            category,
            criterion,
            scope,
            threshold,
            points,
            family,
            tier,
        )
        for family, name, category, criterion, scope, legacy in FAMILIES
        for tier, (threshold, points) in enumerate(
            zip(
                STREAK_MILESTONES if category == "streak" else MILESTONES,
                STREAK_POINTS if category == "streak" else TIER_POINTS,
                strict=True,
            ),
            1,
        )
    )
    + (
        Achievement(
            "local-first-game",
            "Fireside Game",
            "Finish a same-device local game.",
            "local",
            "completed_local_games",
            None,
            1,
        ),
    )
    + tuple(
        Achievement(
            f"{ruleset}-league-{tier_id}",
            f"{ruleset.title()} League: {tier_name}",
            f"Reach {tier_name} in the {ruleset.title()} League.",
            "league",
            "league_tier",
            ruleset,
            rank,
            LEAGUE_TIER_POINTS[rank - 1],
            f"{ruleset}-league",
            rank,
            ruleset,
            tier_id,
            tier_name,
            minimum,
            f"{ruleset.title()} League",
        )
        for ruleset in sorted(RULESETS, key=lambda key: RULESETS[key].size)
        for rank, (tier_id, tier_name, minimum) in enumerate(policy.TIERS, 1)
    )
)
LEAGUE_ACHIEVEMENTS = tuple(
    achievement for achievement in ACHIEVEMENTS if achievement.criterion == "league_tier"
)
LEAGUE_ACHIEVEMENT_BY_KEY = {
    (achievement.ruleset, achievement.league_tier): achievement
    for achievement in LEAGUE_ACHIEVEMENTS
}

# ``shieldbearer`` is the always-available default: every other portrait is
# unlocked by exactly one achievement. ``local-first-game`` deliberately grants no
# portrait, so an achievement is not required to carry one.
PORTRAITS: tuple[Portrait, ...] = (
    Portrait("shieldbearer", "Shieldbearer", None),
    Portrait("shieldmaiden", "Shieldmaiden", "rated-wins-10"),
    Portrait("iron-helm", "Iron Helm", "defender-wins-10"),
    Portrait("wanderer", "Wanderer", "brandubh-wins-10"),
    Portrait("skald", "Skald", "tablut-wins-10"),
    Portrait("fjord-scout", "Fjord Scout", "copenhagen-wins-10"),
    Portrait("raven-seer", "Raven Seer", "rated-wins-25"),
    Portrait("berserker", "Berserker", "rated-win-streak-5"),
    Portrait("one-eyed-jarl", "One-Eyed Jarl", "rated-win-streak-10"),
    Portrait("valkyrie", "Valkyrie", "attacker-wins-10"),
    Portrait("einherjar", "Einherjar", "rated-wins-50"),
    Portrait("forge-keeper", "Forge Keeper", "ai-wins-10"),
)

# The single source of truth for portrait gallery ordering. The API exposes the
# catalog in this order and every gallery renders the catalog as received, so a
# new surface must not re-sort it. A portrait absent from this list sorts after
# every listed portrait, by stable id, so introducing one stays deterministic
# without renumbering the entries above it.
PORTRAIT_DISPLAY_ORDER: tuple[str, ...] = (
    "shieldbearer",
    "forge-keeper",
    "shieldmaiden",
    "raven-seer",
    "einherjar",
    "iron-helm",
    "valkyrie",
    "wanderer",
    "skald",
    "fjord-scout",
    "berserker",
    "one-eyed-jarl",
)
_PORTRAIT_RANK = {portrait_id: index for index, portrait_id in enumerate(PORTRAIT_DISPLAY_ORDER)}


def _portrait_order(portrait: Portrait) -> tuple:
    return (_PORTRAIT_RANK.get(portrait.id, len(PORTRAIT_DISPLAY_ORDER)), portrait.id)


PORTRAITS = tuple(sorted(PORTRAITS, key=_portrait_order))

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
        if achievement.threshold < 1 or achievement.points < 1:
            raise ValueError(f"Achievement {achievement.id!r} needs a positive threshold")
        if achievement.criterion == "league_tier" and (
            achievement.ruleset not in RULESETS
            or achievement.scope != achievement.ruleset
            or (achievement.ruleset, achievement.league_tier) not in LEAGUE_ACHIEVEMENT_BY_KEY
        ):
            raise ValueError(f"Achievement {achievement.id!r} has invalid league metadata")
        scope_column = _CRITERIA[achievement.criterion][1]
        if (
            achievement.criterion != "league_tier"
            and achievement.scope is not None
            and (scope_column is None or achievement.scope not in RULESETS)
        ):
            raise ValueError(f"Achievement {achievement.id!r} has an unsupported scope")
    portrait_ids = [portrait.id for portrait in PORTRAITS]
    if len(portrait_ids) != len(set(portrait_ids)):
        raise ValueError("Portrait IDs must be unique")
    if len(PORTRAIT_DISPLAY_ORDER) != len(set(PORTRAIT_DISPLAY_ORDER)):
        raise ValueError("Portrait display order must not repeat an id")
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


def selected_portrait_ids(c: sqlite3.Connection, account_ids: Iterable[str]) -> dict[str, str]:
    """The effective published portrait for each account, in one batched read.

    The public listing resolves a whole page of participants without a query per
    participant. A stored selection the catalog still offers is published; a
    retired or absent selection resolves to the always-available default, exactly
    as ``selected_portrait_id`` does for a single account. A caller that passes an
    unknown account id gets the default, never an error.
    """
    identifiers = list(dict.fromkeys(account_ids))
    if not identifiers:
        return {}
    default = default_portrait_id(c)
    marks = ",".join("?" * len(identifiers))
    rows = c.execute(
        f"""SELECT s.account_id,s.portrait_id FROM account_portrait_selection s
            JOIN portrait_catalog p ON p.id=s.portrait_id AND p.retired=0
            WHERE s.account_id IN ({marks})""",
        identifiers,
    )
    selected = {row["account_id"]: row["portrait_id"] for row in rows}
    return {account_id: selected.get(account_id, default) for account_id in identifiers}


def _tail_win_streak(c: sqlite3.Connection, account_id: str, cap: int) -> int:
    """Consecutive wins at the very tail of the account's applied ledger, capped.

    Only meaningful immediately after appending that account's newest event: the
    tail is then the just-recorded result. The join mirrors ``rated_streaks`` so
    pool membership and seat attribution cannot drift. The reverse-sequence scan
    stops at the first non-win or after ``cap`` wins, so an ordinary completion
    never folds the whole history. A ``cap`` at or below the next unawarded
    threshold is enough to prove no streak milestone was crossed.
    """
    rows = c.execute(
        """SELECT e.winner,
              CASE WHEN e.attackers_id=? THEN 'attackers' ELSE 'defenders' END AS side
        FROM rating_events e
        WHERE e.state='applied' AND EXISTS (
            SELECT 1 FROM rating_applications a
            WHERE a.game_id=e.game_id AND a.account_id=? AND a.pool=e.pool)
        ORDER BY e.sequence DESC LIMIT ?""",
        (account_id, account_id, cap),
    )
    streak = 0
    for row in rows:
        if row["winner"] != row["side"]:
            break
        streak += 1
    return streak


def _league_tier_rank(identifier: str | None) -> int:
    return policy.tier_rank(identifier)


def _league_season_state(c: sqlite3.Connection, season, now: int) -> tuple[bool, str]:
    """The same availability semantics as the League progression read."""
    row = c.execute(
        """SELECT s.ends_at,s.grace_ends_at,
            EXISTS(SELECT 1 FROM league_rebuild_jobs j WHERE j.season_id=s.id) AS rebuilding,
            EXISTS(SELECT 1 FROM league_settlement_queue q WHERE q.season_id=s.id) AS pending
           FROM league_seasons s WHERE s.id=?""",
        (season.identifier,),
    ).fetchone()
    if row is None:
        return True, "current"
    if row["rebuilding"]:
        return False, "rebuilding"
    if now < row["ends_at"]:
        return True, "current"
    if row["pending"] or now < row["grace_ends_at"]:
        return True, "provisional"
    return True, "finalized"


def _league_family_view(
    ruleset,
    highest_rank,
    awards,
    standing_row,
    season,
    *,
    available: bool,
    status: str,
) -> dict:
    """Permanent league tiers beside the current season's provisional progress."""
    tiers = []
    for rank, (tier_id, tier_name, minimum) in enumerate(policy.TIERS, 1):
        achievement = LEAGUE_ACHIEVEMENT_BY_KEY[(ruleset, tier_id)]
        tiers.append(
            {
                "id": tier_id,
                "name": tier_name,
                "minimum_points": minimum,
                "achievement_id": achievement.id,
                "points": achievement.points,
                "unlocked": achievement.id in awards,
                "current": rank == highest_rank,
            }
        )
    standing = (
        policy.Standing(
            standing_row["points"],
            standing_row["wins"],
            standing_row["draws"],
            standing_row["losses"],
        )
        if available and standing_row is not None
        else None
    )
    tier = standing.tier if standing is not None and standing.played else None
    next_tier = None
    if standing is not None:
        current_minimum = next(
            value for identifier, _, value in policy.TIERS if identifier == standing.tier
        )
        next_tier = next(
            (
                {
                    "id": tier_id,
                    "name": tier_name,
                    "minimum_points": minimum,
                    "points_needed": minimum - standing.points,
                    "progress_points": standing.points - current_minimum,
                    "progress_total": minimum - current_minimum,
                }
                for tier_id, tier_name, minimum in policy.TIERS
                if minimum > standing.points
            ),
            None,
        )
    return {
        "ruleset": ruleset,
        "ruleset_name": ruleset.title(),
        "tiers": tiers,
        "highest_tier": policy.tier_for_rank(highest_rank),
        "highest_tier_name": (policy.TIERS[highest_rank - 1][1] if highest_rank else None),
        "permanent_progress": highest_rank,
        "permanent_total": len(policy.TIERS),
        "season": {
            "id": season.identifier,
            "available": available,
            "status": status,
            "points": standing.points if standing is not None else None,
            "tier": tier,
            "tier_name": (
                next(name for identifier, name, _ in policy.TIERS if identifier == tier)
                if tier is not None
                else None
            ),
            "qualified": standing.qualified if standing is not None else None,
            "played": standing.played if standing is not None else None,
            "qualifying_results": policy.QUALIFYING_RESULTS,
            "next_tier": next_tier,
        },
    }


class Progression:
    """Own achievement progress, portrait catalog, unlocks and selection."""

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
        if key not in cache and achievement.criterion in {
            "rated_wins",
            "rated_defender_wins",
            "rated_attacker_wins",
        }:
            rows = c.execute(_RATED_WINS, (account_id,)).fetchall()
            cache[("rated_wins", None)] = sum(row["wins"] for row in rows)
            for pool in RULESETS:
                cache[("rated_wins", pool)] = sum(
                    row["wins"] for row in rows if row["pool"] == pool
                )
            cache[("rated_defender_wins", None)] = sum(
                row["wins"] for row in rows if row["side"] == "defenders"
            )
            cache[("rated_attacker_wins", None)] = sum(
                row["wins"] for row in rows if row["side"] == "attackers"
            )
        if key not in cache:
            if achievement.criterion == "league_tier":
                row = c.execute(
                    """SELECT highest_tier FROM league_achievement_progress
                       WHERE account_id=? AND ruleset=?""",
                    (account_id, achievement.scope),
                ).fetchone()
                cache[key] = row[0] if row is not None else 0
            else:
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

    def _projection(self, c: sqlite3.Connection, account_id: str, now: int) -> dict:
        """Complete own progress from stored awards and authoritative records.

        Read-only: the caller owns the transaction. ``now`` supplies ``as_of``;
        authoritative event transactions alone grant awards.
        """
        awards = {
            row["achievement_id"]: row["awarded_at"]
            for row in c.execute(
                "SELECT achievement_id,awarded_at FROM achievement_awards WHERE account_id=?",
                (account_id,),
            )
        }
        current_season = policy.season_for(instant(now))
        season_available, season_status = _league_season_state(c, current_season, now)
        league_peaks = {
            row["ruleset"]: row["highest_tier"]
            for row in c.execute(
                """SELECT ruleset,highest_tier FROM league_achievement_progress
                   WHERE account_id=?""",
                (account_id,),
            )
        }
        league_standings = (
            {
                row["ruleset"]: row
                for row in c.execute(
                    """SELECT ruleset,points,wins,draws,losses FROM league_standings
                       WHERE season_id=? AND account_id=?""",
                    (current_season.identifier, account_id),
                )
            }
            if season_available
            else {}
        )
        cache: dict = {
            ("league_tier", ruleset): league_peaks.get(ruleset, 0) for ruleset in RULESETS
        }
        items: list[dict] = []
        for achievement in ACHIEVEMENTS:
            awarded_at = awards.get(achievement.id)
            items.append(
                {
                    "id": achievement.id,
                    "family_id": achievement.family,
                    "tier": achievement.tier,
                    "points": achievement.points,
                    "name": achievement.name,
                    "description": achievement.description,
                    "category": achievement.category,
                    "scope": achievement.scope,
                    "threshold": achievement.threshold,
                    "progress": self._measure(c, account_id, achievement, cache),
                    "unlocked": awarded_at is not None,
                    "awarded_at": stamp(instant(awarded_at)) if awarded_at is not None else None,
                    "portrait_id": PORTRAIT_BY_ACHIEVEMENT.get(achievement.id),
                    "ruleset": achievement.ruleset,
                    "league_tier": achievement.league_tier,
                    "league_tier_name": achievement.league_tier_name,
                    "minimum_points": achievement.minimum_points,
                    "family_name": achievement.family_name,
                }
            )
        families = []
        for family_id in dict.fromkeys(item["family_id"] for item in items):
            tiers = [item for item in items if item["family_id"] == family_id]
            awarded = [item for item in tiers if item["unlocked"]]
            next_tier = next(
                (
                    item
                    for item in tiers
                    if not item["unlocked"] and item["threshold"] > item["progress"]
                ),
                None,
            )
            league = (
                _league_family_view(
                    tiers[0]["ruleset"],
                    league_peaks.get(tiers[0]["ruleset"], 0),
                    awards,
                    league_standings.get(tiers[0]["ruleset"]),
                    current_season,
                    available=season_available,
                    status=season_status,
                )
                if tiers[0]["category"] == "league"
                else None
            )
            families.append(
                {
                    "id": family_id,
                    "name": tiers[0]["family_name"] or tiers[0]["name"],
                    "progress": tiers[0]["progress"],
                    "awarded_tier": max((item["tier"] for item in awarded), default=0),
                    "awarded_threshold": max((item["threshold"] for item in awarded), default=0),
                    "achieved_threshold": max(
                        (
                            item["threshold"]
                            for item in tiers
                            if item["progress"] >= item["threshold"]
                        ),
                        default=0,
                    ),
                    "total_tiers": len(tiers),
                    "awarded_count": len(awarded),
                    "next_milestone": next_tier,
                    "complete": len(awarded) == len(tiers),
                    "milestones": tiers,
                    "league": league,
                }
            )
        return {
            "catalog_version": ACHIEVEMENT_CATALOG_VERSION,
            "families": families,
            "unlocked_portrait_ids": [
                row[0]
                for row in c.execute(
                    "SELECT portrait_id FROM account_portraits WHERE account_id=? ORDER BY portrait_id",
                    (account_id,),
                )
            ],
            "portrait_catalog_version": PORTRAIT_CATALOG_VERSION,
            "items": items,
            "summary": {
                "unlocked": sum(1 for item in items if item["unlocked"]),
                "total": len(items),
                "earned_points": sum(item["points"] for item in items if item["unlocked"]),
                "total_points": sum(item["points"] for item in items),
            },
            "as_of": stamp(instant(now)),
        }

    def achievements(self, credential: str) -> dict:
        """Read-only own progress; a fresh account receives every item at zero.

        Authoritative event transactions award milestones. This read only
        projects durable awards and current progress; it never grants rewards.
        """
        with self.store.connection() as c:
            c.execute("BEGIN")
            account = self.online._identity(c, credential)
            return self._projection(c, account["id"], micros(self.online.clock()))

    @staticmethod
    def _unlock_requirement(c: sqlite3.Connection, portrait_id: str) -> str | None:
        row = c.execute(
            "SELECT unlock_achievement_id,retired FROM portrait_catalog WHERE id=?",
            (portrait_id,),
        ).fetchone()
        if row is None or row["retired"]:
            raise OnlineError(422, "Unknown or unavailable portrait.")
        return row["unlock_achievement_id"]

    @staticmethod
    def _has_portrait(c: sqlite3.Connection, account_id: str, portrait_id: str) -> bool:
        return (
            c.execute(
                "SELECT 1 FROM account_portraits WHERE account_id=? AND portrait_id=?",
                (account_id, portrait_id),
            ).fetchone()
            is not None
        )

    def unlock(self, credential: str, portrait_id: str) -> dict[str, str | bool]:
        """Claim an earned portrait once, using the session and permanent award."""
        with self.online._write() as c:
            account_id = self.online._identity(c, credential)["id"]
            requirement = self._unlock_requirement(c, portrait_id)
            if requirement is None or self._has_portrait(c, account_id, portrait_id):
                return {"portrait_id": portrait_id, "newly_unlocked": False}
            if not c.execute(
                "SELECT 1 FROM achievement_awards WHERE account_id=? AND achievement_id=?",
                (account_id, requirement),
            ).fetchone():
                raise OnlineError(403, "Complete the required achievement to unlock this portrait.")
            c.execute(
                """INSERT INTO account_portraits(account_id,portrait_id,granted_at,source_achievement_id)
                   VALUES(?,?,?,?)""",
                (account_id, portrait_id, micros(self.online.clock()), requirement),
            )
            return {"portrait_id": portrait_id, "newly_unlocked": True}

    def select(self, c: sqlite3.Connection, account_id: str, portrait_id: str) -> str:
        """Select an unlocked, currently offered portrait inside the caller's write."""
        requirement = self._unlock_requirement(c, portrait_id)
        if requirement is not None and not self._has_portrait(c, account_id, portrait_id):
            raise OnlineError(403, "This portrait is not unlocked yet.")
        now = micros(self.online.clock())
        c.execute(
            """INSERT INTO account_portrait_selection(account_id,portrait_id,updated_at)
               VALUES(?,?,?) ON CONFLICT(account_id) DO UPDATE SET
                 portrait_id=excluded.portrait_id,updated_at=excluded.updated_at""",
            (account_id, portrait_id, now),
        )
        return portrait_id


RATED_CRITERIA = frozenset(
    {"rated_wins", "rated_defender_wins", "rated_attacker_wins", "rated_win_streak_best"}
)
LOCAL_CRITERIA = frozenset({"completed_local_games", "defeated_ai_games"})


def award_achievements(
    c: sqlite3.Connection,
    account_ids: Iterable[str],
    now: int,
    criteria: frozenset[str] | None = None,
    *,
    streak_tails: frozenset[str] | None = None,
) -> None:
    """Award proven milestones in the caller's event transaction.

    Counter reads are cached by criterion/scope; events measure only affected
    counters. Award keys and append-only guards preserve permanence.

    ``streak_tails`` is the safe fast path for a freshly appended rated result:
    it names the accounts whose newest ledger event is the just-recorded win (an
    empty set for a draw). Every other account skips the streak criterion, since
    a loss or draw cannot raise a best win streak. A named account still falls
    back to the exact full-ledger fold as soon as its bounded tail reaches the
    next unawarded milestone. ``None`` always measures exactly, as corrections,
    withdrawals, rebuilds and migration require.
    """
    for account_id in dict.fromkeys(account_ids):
        cache: dict = {}
        awarded = {
            row[0]
            for row in c.execute(
                "SELECT achievement_id FROM achievement_awards WHERE account_id=?", (account_id,)
            )
        }
        skip_streak = False
        if streak_tails is not None:
            if account_id not in streak_tails:
                skip_streak = True
            else:
                next_threshold = min(
                    (
                        achievement.threshold
                        for achievement in ACHIEVEMENTS
                        if achievement.criterion == "rated_win_streak_best"
                        and achievement.id not in awarded
                    ),
                    default=None,
                )
                if (
                    next_threshold is None
                    or _tail_win_streak(c, account_id, next_threshold) < next_threshold
                ):
                    skip_streak = True
        for achievement in ACHIEVEMENTS:
            if achievement.criterion == "league_tier":
                # League tiers are recorded with their authoritative standings
                # in the settlement transaction, never inferred from a counter.
                continue
            if achievement.id in awarded:
                continue
            if criteria is not None and achievement.criterion not in criteria:
                continue
            if skip_streak and achievement.criterion == "rated_win_streak_best":
                continue
            if Progression._measure(c, account_id, achievement, cache) >= achievement.threshold:
                c.execute(
                    """INSERT OR IGNORE INTO achievement_awards(
                       account_id,achievement_id,awarded_at,catalog_version) VALUES(?,?,?,?)""",
                    (account_id, achievement.id, now, ACHIEVEMENT_CATALOG_VERSION),
                )


def league_achievement_evidence_pending(
    c: sqlite3.Connection,
    account_id: str,
    season_id: str,
    ruleset: str,
    *,
    sequence: int | None = None,
    exclude_game: str | None = None,
) -> bool:
    """Whether newer evidence could invalidate an upward league evaluation.

    A previously settled game whose official revision differs from the league
    projection always blocks. When settling a result with ``sequence``, an
    unsettled earlier official result in the same account/scope also blocks:
    appending it to today's standing would ignore a lower-sequence gap that the
    eventual stable fold must insert first.
    """
    conditions = "(l.result_revision IS NOT NULL AND g.result_revision IS NOT l.result_revision)"
    args: list[str | int] = [season_id, ruleset, account_id, account_id]
    if sequence is not None:
        conditions += " OR (r.state='applied' AND r.sequence<?)"
        args.append(sequence)
    predicate = ""
    if exclude_game is not None:
        predicate = "AND q.game_id<>?"
        args.append(exclude_game)
    return (
        c.execute(
            f"""SELECT 1 FROM league_settlement_queue q
            JOIN league_games l ON l.game_id=q.game_id
            JOIN online_games g ON g.id=q.game_id
            LEFT JOIN rating_events r ON r.game_id=q.game_id
            WHERE q.season_id=? AND l.ruleset=?
              AND (l.first_account=? OR l.second_account=?)
              AND ({conditions})
              {predicate}
            LIMIT 1""",
            args,
        ).fetchone()
        is not None
    )


def league_season_rebuilding(c: sqlite3.Connection, season_id: str) -> bool:
    return (
        c.execute("SELECT 1 FROM league_rebuild_jobs WHERE season_id=?", (season_id,)).fetchone()
        is not None
    )


def record_league_candidate(
    c: sqlite3.Connection,
    account_id: str,
    season_id: str,
    ruleset: str,
    peak_tier: str | None,
    now: int,
) -> None:
    """Persist a candidate peak without granting a permanent achievement.

    Candidates keep peaks discovered by incomplete recovery and queued
    corrections. A bounded reconciliation pass validates them against effective
    official results before any award is written.
    """
    rank = _league_tier_rank(peak_tier)
    if not rank:
        return
    c.execute(
        """INSERT INTO league_achievement_reconciliation(
             account_id,season_id,ruleset,candidate_tier,due_at)
           VALUES(?,?,?,?,?)
           ON CONFLICT(account_id,season_id,ruleset) DO UPDATE SET
             candidate_tier=CASE
               WHEN excluded.candidate_tier>COALESCE(candidate_tier,0)
               THEN excluded.candidate_tier ELSE candidate_tier END,
             due_at=MIN(due_at,excluded.due_at),attempts=0,last_error=NULL""",
        (account_id, season_id, ruleset, rank, now),
    )


def record_league_tier(
    c: sqlite3.Connection,
    account_id: str,
    season_id: str,
    ruleset: str,
    standing,
    now: int,
    *,
    game_id: str | None = None,
    peak_tier: str | None = None,
    sequence: int | None = None,
) -> bool:
    """Award a qualified league peak only from consistent authoritative evidence.

    If a previously settled result in the same account/scope awaits correction,
    or an administrative rebuild is incomplete, the candidate is durable and a
    later bounded reconciliation validates it against effective results.
    """
    if ruleset not in RULESETS:
        return False
    candidate = peak_tier
    final_tier = standing.tier if standing.qualified else None
    if _league_tier_rank(final_tier) > _league_tier_rank(candidate):
        candidate = final_tier
    if not _league_tier_rank(candidate):
        return False
    if league_season_rebuilding(c, season_id) or league_achievement_evidence_pending(
        c, account_id, season_id, ruleset, sequence=sequence, exclude_game=game_id
    ):
        record_league_candidate(c, account_id, season_id, ruleset, candidate, now)
        return False
    return record_league_peak(c, account_id, ruleset, candidate, now)


def record_league_peak(
    c: sqlite3.Connection,
    account_id: str,
    ruleset: str,
    peak_tier: str | None,
    now: int,
) -> bool:
    """Record a validated peak and materialize every preceding achievement tier."""
    rank = _league_tier_rank(peak_tier)
    if ruleset not in RULESETS or not rank:
        return False
    previous = c.execute(
        "SELECT highest_tier FROM league_achievement_progress WHERE account_id=? AND ruleset=?",
        (account_id, ruleset),
    ).fetchone()
    if previous is not None and previous[0] >= rank:
        return False
    c.execute(
        """INSERT INTO league_achievement_progress(account_id,ruleset,highest_tier,achieved_at)
           VALUES(?,?,?,?)
           ON CONFLICT(account_id,ruleset) DO UPDATE SET
             highest_tier=excluded.highest_tier,achieved_at=excluded.achieved_at
           WHERE excluded.highest_tier>league_achievement_progress.highest_tier""",
        (account_id, ruleset, rank, now),
    )
    award_league_achievements(c, account_id, ruleset, now, rank)
    return True


def award_league_achievements(
    c: sqlite3.Connection,
    account_id: str,
    ruleset: str,
    now: int,
    highest_tier: int | None = None,
) -> None:
    """Materialize all qualified league tiers up to the stored permanent peak."""
    if ruleset not in RULESETS:
        return
    if highest_tier is None:
        row = c.execute(
            "SELECT highest_tier FROM league_achievement_progress WHERE account_id=? AND ruleset=?",
            (account_id, ruleset),
        ).fetchone()
        if row is None:
            return
        highest_tier = row[0]
    for rank, (tier_id, _, _) in enumerate(policy.TIERS, 1):
        if rank > highest_tier:
            break
        achievement = LEAGUE_ACHIEVEMENT_BY_KEY[(ruleset, tier_id)]
        c.execute(
            """INSERT OR IGNORE INTO achievement_awards(
               account_id,achievement_id,awarded_at,catalog_version) VALUES(?,?,?,?)""",
            (account_id, achievement.id, now, ACHIEVEMENT_CATALOG_VERSION),
        )


def reconstruct_league_achievements(c: sqlite3.Connection, now: int) -> None:
    """Queue bounded verification of historical league scopes after migration.

    The migration does not trust `league_contributions`: it may be stale during a
    pending correction or partially cleared by an interrupted rebuild. One
    reconciliation row per account/season/ruleset lets the worker recompute peaks
    from effective official results after the scope becomes consistent.
    """
    c.execute(
        """INSERT OR IGNORE INTO league_achievement_reconciliation(
             account_id,season_id,ruleset,candidate_tier,due_at)
           SELECT first_account,season_id,ruleset,NULL,? FROM league_games
           UNION
           SELECT second_account,season_id,ruleset,NULL,? FROM league_games""",
        (now, now),
    )


def reconcile_legacy_achievements(c: sqlite3.Connection, now: int) -> None:
    """Keep the schema-21 backfill for installations upgrading from older schemas."""
    accounts = [row[0] for row in c.execute("SELECT id FROM accounts")]
    award_achievements(c, accounts, now)
    c.execute(
        """INSERT INTO account_portraits(account_id,portrait_id,granted_at,source_achievement_id)
           SELECT a.account_id,p.id,?,p.unlock_achievement_id FROM portrait_catalog p
           JOIN achievement_awards a ON a.achievement_id=p.unlock_achievement_id
           WHERE p.retired=0 ON CONFLICT(account_id,portrait_id) DO NOTHING""",
        (now,),
    )
