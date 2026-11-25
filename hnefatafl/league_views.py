"""Bounded, authenticated projections over the League and online authorities."""

import base64
import binascii
import hashlib
import hmac
import json
import secrets
import sqlite3
from contextlib import contextmanager
from urllib.parse import quote

from . import league_policy as policy
from .account_policy import resolve_accounts
from .engine import RULESETS
from .league_results import standing_value
from .leagues import Leagues
from .online_core import OnlineError, instant, micros, stamp
from .progression import selected_portrait_ids


class LeagueError(OnlineError):
    def __init__(self, status: int, code: str, detail: str):
        super().__init__(status, detail)
        self.code = code


class LeagueCursors:
    """Authenticate cursor positions and accumulated ranks for this API process."""

    def __init__(self):
        self._key = secrets.token_bytes(32)

    def encode(self, scope: tuple, position: tuple) -> str:
        payload = json.dumps((scope, position), separators=(",", ":")).encode()
        signature = hmac.digest(self._key, payload, hashlib.sha256)
        return base64.urlsafe_b64encode(signature + payload).decode().rstrip("=")

    def decode(self, cursor: str, scope: tuple) -> tuple:
        try:
            raw = base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True)
            signature, payload = raw[:32], raw[32:]
            if not hmac.compare_digest(signature, hmac.digest(self._key, payload, hashlib.sha256)):
                raise ValueError
            encoded_scope, position = json.loads(payload)
            if tuple(encoded_scope[:-1]) != scope[:-1]:
                raise ValueError
        except (ValueError, TypeError, binascii.Error):
            raise LeagueError(422, "league_invalid_cursor", "Invalid league page cursor.") from None
        if encoded_scope[-1] != scope[-1]:
            raise LeagueError(
                409, "league_page_changed", "League page changed. Reload from the start."
            )
        return tuple(position)


def season_view(row, now, *, rebuilding=False, pending=False, stored=True):
    if rebuilding:
        status = "rebuilding"
    elif micros(now) < row["ends_at"]:
        status = "current"
    elif pending or micros(now) < row["grace_ends_at"]:
        status = "provisional"
    else:
        status = "finalized"
    return {
        "id": row["id"],
        "starts_at": stamp(instant(row["starts_at"])),
        "ends_at": stamp(instant(row["ends_at"])),
        "grace_ends_at": stamp(instant(row["grace_ends_at"])),
        "status": status,
        "revision": row["revision"],
        "stored": stored,
    }


def _players(c, identifiers):
    accounts = resolve_accounts(c, identifiers)
    portraits = selected_portrait_ids(c, accounts)
    return {
        account_id: {
            "id": account_id,
            "username": identity.get("username", ""),
            "display_name": identity["name"],
            "portrait_id": portraits[account_id],
        }
        for account_id, identity in accounts.items()
    }


def _totals(standing):
    return {
        "tier": standing.tier if standing.played else None,
        "points": standing.points,
        "wins": standing.wins,
        "draws": standing.draws,
        "losses": standing.losses,
        "played": standing.played,
    }


_QUALIFIED = f"wins+draws+losses>={policy.QUALIFYING_RESULTS}"
_SPORTING_COLUMNS = ("-points", "-wins", "losses")
_STANDING_COLUMNS = (*_SPORTING_COLUMNS, "account_id")
_QUALIFIED_SCOPE = f"FROM league_standings WHERE season_id=? AND ruleset=? AND {_QUALIFIED}"
_SEASON_FIELDS = """s.*,j.season_id IS NOT NULL AS rebuilding,
    EXISTS(SELECT 1 FROM league_settlement_queue q WHERE q.season_id=s.id) AS pending"""


def _stored_season_view(row, now):
    return season_view(row, now, rebuilding=bool(row["rebuilding"]), pending=bool(row["pending"]))


def _seek_predicate(columns: tuple[str, ...], inequality: str) -> str:
    # SQLite cannot seek a row-value range whose index members are expressions.
    # One inequality after an equality prefix uses every part of the index key.
    return " AND ".join(
        [*(f"{column}=?" for column in columns[:-1]), f"{columns[-1]}{inequality}?"]
    )


def _rank(c: sqlite3.Connection, scope: tuple[str, str], standing: policy.Standing) -> int:
    key = (-standing.points, -standing.wins, standing.losses)
    counts = []
    args: list[str | int] = []
    for length in range(1, len(_SPORTING_COLUMNS) + 1):
        predicate = _seek_predicate(_SPORTING_COLUMNS[:length], "<")
        counts.append(f"(SELECT COUNT(*) {_QUALIFIED_SCOPE} AND {predicate})")
        args.extend((*scope, *key[:length]))
    return c.execute(f"SELECT 1+{'+'.join(counts)}", args).fetchone()[0]


def _standing_page(
    c: sqlite3.Connection, scope: tuple[str, str], after: tuple | None, limit: int
) -> list[sqlite3.Row]:
    if after is None:
        order = ",".join(_STANDING_COLUMNS)
        return c.execute(
            f"SELECT * {_QUALIFIED_SCOPE} ORDER BY {order} LIMIT ?", (*scope, limit)
        ).fetchall()

    rows: list[sqlite3.Row] = []
    # Read the remaining tie group first, followed by each worse sporting range.
    for length in range(len(_STANDING_COLUMNS), 0, -1):
        predicate = _seek_predicate(_STANDING_COLUMNS[:length], ">")
        order = ",".join(_STANDING_COLUMNS[length - 1 :])
        rows.extend(
            c.execute(
                f"SELECT * {_QUALIFIED_SCOPE} AND {predicate} ORDER BY {order} LIMIT ?",
                (*scope, *after[:length], limit - len(rows)),
            ).fetchall()
        )
        if len(rows) == limit:
            break
    return rows


class LeagueViews:
    def __init__(self, core: Leagues, cursors: LeagueCursors):
        self.core = core
        self.cursors = cursors

    @contextmanager
    def snapshot(self, credential: str, *, admin=False):
        with self.core.store.connection() as c:
            c.execute("BEGIN")
            actor = self.core.online._identity(c, credential, admin=admin)
            yield c, actor, self.core.online._now()

    @staticmethod
    def _season(c, identifier, now):
        current = policy.season_for(now)
        key = identifier or current.identifier
        row = c.execute(
            f"""SELECT {_SEASON_FIELDS} FROM league_seasons s
                LEFT JOIN league_rebuild_jobs j ON j.season_id=s.id WHERE s.id=?""",
            (key,),
        ).fetchone()
        if row:
            return _stored_season_view(row, now)
        if key != current.identifier:
            raise LeagueError(404, "league_season_missing", "League season not found.")
        return season_view(
            {
                "id": current.identifier,
                "starts_at": micros(current.start),
                "ends_at": micros(current.end),
                "grace_ends_at": micros(current.grace_end),
                "revision": 0,
            },
            now,
            stored=False,
        )

    @staticmethod
    def _progress(c, account_id, ruleset, season):
        if season["status"] == "rebuilding":
            return None
        scope = (season["id"], ruleset)
        row = c.execute(
            "SELECT * FROM league_standings WHERE season_id=? AND ruleset=? AND account_id=?",
            (*scope, account_id),
        ).fetchone()
        standing = standing_value(row)
        rank = _rank(c, scope, standing) if standing.qualified else None
        current_minimum = next(
            minimum for name, _, minimum in policy.TIERS if name == standing.tier
        )
        next_tier = next(
            (
                {
                    "id": name,
                    "minimum_points": minimum,
                    "points_needed": minimum - standing.points,
                    "progress_points": standing.points - current_minimum,
                    "progress_total": minimum - current_minimum,
                }
                for name, _, minimum in policy.TIERS
                if minimum > standing.points
            ),
            None,
        )
        return {
            **_totals(standing),
            "qualified": standing.qualified,
            "qualifying_results": policy.QUALIFYING_RESULTS,
            "rank": rank,
            "next_tier": next_tier,
            "previous_tier": row["previous_tier"] if row else None,
            "tier_changed_at": stamp(instant(row["tier_changed_at"]))
            if row and row["tier_changed_at"] is not None
            else None,
        }

    def current(self, credential, ruleset):
        with self.snapshot(credential) as (c, actor, now):
            season = self._season(c, None, now)
            return {
                "server_time": stamp(now),
                "season": season,
                "policy": {
                    "version": policy.POLICY_VERSION,
                    "rulesets": list(RULESETS),
                    "tiers": [
                        {"id": key, "minimum_points": minimum} for key, _, minimum in policy.TIERS
                    ],
                    "win_points": policy.WIN_POINTS,
                    "draw_points": policy.DRAW_POINTS,
                    "loss_points": policy.LOSS_POINTS,
                    "points_floor": 0,
                    "qualifying_results": policy.QUALIFYING_RESULTS,
                    "grace_seconds": policy.FINISHING_GRACE_SECONDS,
                    "queue_seconds": policy.QUEUE_SECONDS,
                    "requires_verified_human": True,
                    "global_queue_or_active_limit": 1,
                    "clock_seconds": policy.CLOCK_SECONDS,
                    "increment_seconds": policy.INCREMENT_SECONDS,
                    "rating_window": {
                        "initial": policy.RATING_WINDOW_INITIAL,
                        "maximum": policy.RATING_WINDOW_MAX,
                        "step": policy.RATING_WINDOW_STEP,
                        "interval_seconds": policy.RATING_WINDOW_INTERVAL_SECONDS,
                    },
                    "pair_daily_limit": policy.PAIR_DAILY_LIMIT,
                    "request_limits": {
                        "rolling_window_seconds": policy.REQUEST_WINDOW_SECONDS,
                        "per_rolling_window": policy.REQUESTS_PER_MINUTE,
                        "per_utc_day": policy.REQUESTS_PER_UTC_DAY,
                        "exact_retries_count": False,
                    },
                },
                "progress": self._progress(c, actor["id"], ruleset, season),
                "queue": self.core.queue_snapshot(c, actor, now),
            }

    def active_game(self, credential):
        with self.snapshot(credential) as (c, actor, _):
            active = self.core._active_game(c, actor["id"])
            return {"active_game": dict(active) if active is not None else None}

    def progress(self, credential, ruleset, identifier):
        with self.snapshot(credential) as (c, actor, now):
            season = self._season(c, identifier, now)
            return {"season": season, "progress": self._progress(c, actor["id"], ruleset, season)}

    def seasons(self, credential, limit, cursor):
        scope = ("seasons", 1)
        after = self.cursors.decode(cursor, scope) if cursor else ("9999-99",)
        with self.snapshot(credential) as (c, _, now):
            rows = c.execute(
                f"""SELECT {_SEASON_FIELDS} FROM league_seasons s
                 LEFT JOIN league_rebuild_jobs j ON j.season_id=s.id
                 WHERE s.id<? ORDER BY s.id DESC LIMIT ?""",
                (*after, limit + 1),
            ).fetchall()
            return {
                "items": [_stored_season_view(row, now) for row in rows[:limit]],
                "next_cursor": self.cursors.encode(scope, (rows[limit - 1]["id"],))
                if len(rows) > limit
                else None,
            }

    def standings(self, credential, ruleset, identifier, limit, cursor):
        with self.snapshot(credential) as (c, _, now):
            season = self._season(c, identifier, now)
            if season["status"] == "rebuilding":
                return {"season": season, "items": [], "next_cursor": None}
            scope = ("standings", season["id"], ruleset, season["revision"])
            seen = rank = 0
            previous = after = None
            if cursor:
                *sort_key, seen, rank = self.cursors.decode(cursor, scope)
                after = tuple(sort_key)
                previous = after[:3]
            rows = _standing_page(c, (season["id"], ruleset), after, limit + 1)
            players = _players(c, [row["account_id"] for row in rows[:limit]])
            items = []
            for row in rows[:limit]:
                seen += 1
                sporting = (-row["points"], -row["wins"], row["losses"])
                if sporting != previous:
                    rank = seen
                    previous = sporting
                items.append(
                    {
                        "rank": rank,
                        "player": players[row["account_id"]],
                        **_totals(standing_value(row)),
                    }
                )
            next_cursor = None
            if len(rows) > limit:
                assert previous is not None
                next_cursor = self.cursors.encode(
                    scope, (*previous, rows[limit - 1]["account_id"], seen, rank)
                )
            return {"season": season, "items": items, "next_cursor": next_cursor}

    @staticmethod
    def _history_rows(c, account_id, ruleset, season_id, after, limit):
        # Each side of the UNION reads one bounded page from its account-leading index.
        predicates = "AND (paired_at,game_id)<(?,?)" if after else ""
        args = (account_id, season_id, ruleset, *after, limit)
        return c.execute(
            f"""WITH first_seat AS (
                  SELECT game_id,paired_at FROM league_games
                  WHERE first_account=? AND season_id=? AND ruleset=? {predicates}
                  ORDER BY paired_at DESC,game_id DESC LIMIT ?
                ), second_seat AS (
                  SELECT game_id,paired_at FROM league_games
                  WHERE second_account=? AND season_id=? AND ruleset=? {predicates}
                  ORDER BY paired_at DESC,game_id DESC LIMIT ?
                ), games AS (
                  SELECT * FROM first_seat UNION ALL SELECT * FROM second_seat
                )
                SELECT l.game_id,l.paired_at,l.state,l.result_revision AS settled_revision,
                       g.attackers_id,g.defenders_id,g.status,g.result_revision,
                       json_extract(r.payload,'$.winner') AS winner
                FROM games p JOIN league_games l ON l.game_id=p.game_id
                JOIN online_games g ON g.id=l.game_id
                LEFT JOIN online_results r ON r.game_id=g.id AND r.revision=g.result_revision
                WHERE ? IN (g.attackers_id,g.defenders_id)
                ORDER BY p.paired_at DESC,p.game_id DESC LIMIT ?""",
            (*args, *args, account_id, limit),
        ).fetchall()

    def history(self, credential, ruleset, identifier, limit, cursor):
        with self.snapshot(credential) as (c, actor, now):
            season = self._season(c, identifier, now)
            actor_id = actor["id"]
            scope = ("history", actor_id, season["id"], ruleset, 1)
            after = self.cursors.decode(cursor, scope) if cursor else ()
            rows = self._history_rows(c, actor_id, ruleset, season["id"], after, limit + 1)
            opponents = [
                row["defenders_id"] if row["attackers_id"] == actor_id else row["attackers_id"]
                for row in rows[:limit]
            ]
            players = _players(c, opponents)
            items = []
            for row, opponent in zip(rows, opponents):
                side = "attackers" if row["attackers_id"] == actor_id else "defenders"
                winner = row["winner"]
                result = None
                if winner:
                    result = "draw" if winner == "draw" else "win" if winner == side else "loss"
                href = f"/online/games/{quote(row['game_id'], safe='')}"
                state = (
                    row["state"] if row["settled_revision"] == row["result_revision"] else "pending"
                )
                items.append(
                    {
                        "game_id": row["game_id"],
                        "paired_at": stamp(instant(row["paired_at"])),
                        "opponent": players[opponent],
                        "side": side,
                        "game_status": row["status"],
                        "result": result,
                        "state": state,
                        "corrected": (row["result_revision"] or 0) > 1,
                        "result_revision": row["result_revision"],
                        "game_href": href,
                        "replay_href": f"{href}/review" if row["status"] == "completed" else None,
                    }
                )
            return {
                "season": season,
                "items": items,
                "next_cursor": self.cursors.encode(
                    scope, (rows[limit - 1]["paired_at"], rows[limit - 1]["game_id"])
                )
                if len(rows) > limit
                else None,
            }
