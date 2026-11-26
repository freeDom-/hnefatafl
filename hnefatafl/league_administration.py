"""Version-checked League administration and durable authoritative recovery."""

import json
import logging
import sqlite3
from datetime import timedelta
from typing import Literal

from . import league_policy as policy
from .league_results import (
    effective_league_result,
    record_contributions,
    save_standing,
    standing_value,
    write_game_result,
)
from .league_views import LeagueError, LeagueViews, season_view
from .leagues import ELIGIBILITY_SCAN, queue_eligibility_page
from .online_core import DeploymentLocked, instant, micros, stamp
from .progression import record_league_candidate
from .security_context import online_ineligibility

LOGGER = logging.getLogger(__name__)
STALE_LIMIT = 20
STALE_SCAN = ELIGIBILITY_SCAN


def _audit(c, actor_id, action, now, reason, *, season_id=None, details=None):
    c.execute(
        "INSERT INTO league_audit(actor_id,action,season_id,at,reason,details) VALUES(?,?,?,?,?,?)",
        (actor_id, action, season_id, micros(now), reason, json.dumps(details)),
    )


def _reason(reason: str) -> str:
    cleaned = reason.strip()
    if not 1 <= len(cleaned) <= 1000:
        raise LeagueError(422, "league_invalid_request", "An administrative reason is required.")
    return cleaned


def _stale_reason(row, at: int) -> str | None:
    if row["expires_at"] <= at:
        return "expired"
    return online_ineligibility(dict(row))


class LeagueAdministration:
    def __init__(self, views: LeagueViews):
        self.views = views
        self.core = views.core
        self.online = self.core.online

    def _stale_entries(self, c, at, actor_id, cursor):
        scope = ("stale_queue", actor_id, 2)
        cutoff, anchor, after = (
            self.views.cursors.decode(cursor, scope) if cursor else (at, None, None)
        )
        entries: list[tuple[sqlite3.Row, str]] = []
        position: tuple[int, str | None, int | None] | None = None
        if after is None:
            # Keep the expiry boundary fixed for the whole traversal. Use the
            # retained request record to seek past a removed anchor without
            # including an account ID in the browser's cursor.
            predicate, keys = "", ()
            if anchor is not None:
                request = c.execute(
                    "SELECT expires_at,account_id FROM league_requests WHERE id=?", (anchor,)
                ).fetchone()
                if request is None:
                    raise LeagueError(409, "league_page_changed", "Reload the stale queue page.")
                predicate, keys = "AND (expires_at,account_id)>(?,?)", tuple(request)
            expired = c.execute(
                f"""SELECT request_id,ruleset,joined_at,expires_at FROM league_queue
                WHERE expires_at<=? {predicate}
                ORDER BY expires_at,account_id LIMIT ?""",
                (cutoff, *keys, STALE_LIMIT + 1),
            ).fetchall()
            entries.extend((row, "expired") for row in expired[:STALE_LIMIT])
            if len(expired) > STALE_LIMIT:
                position = (cutoff, expired[STALE_LIMIT - 1]["request_id"], None)
            elif len(entries) == STALE_LIMIT:
                position = (cutoff, None, 0)
            after = 0
        if position is None:
            # Scan a bounded range of queue rows, applying current eligibility
            # only to entries outside the expiry phase's fixed boundary.
            page = queue_eligibility_page(c, after, STALE_SCAN + 1)
            visited = 0
            for row in page[:STALE_SCAN]:
                visited += 1
                if row["expires_at"] > cutoff:
                    reason = online_ineligibility(dict(row))
                    if reason is not None:
                        entries.append((row, reason))
                        if len(entries) == STALE_LIMIT:
                            break
            if visited < len(page):
                position = (cutoff, None, page[visited - 1]["position"])
        return (
            [
                {
                    "request_id": row["request_id"],
                    "ruleset": row["ruleset"],
                    "joined_at": stamp(instant(row["joined_at"])),
                    "expires_at": stamp(instant(row["expires_at"])),
                    "reason": reason,
                }
                for row, reason in entries
            ],
            self.views.cursors.encode(scope, position) if position is not None else None,
        )

    def health(self, credential, stale_cursor=None):
        with self.views.snapshot(credential, admin=True) as (c, actor, now):
            paused, version = c.execute(
                "SELECT paused,version FROM league_settings WHERE id=1"
            ).fetchone()
            at = micros(now)
            queue = c.execute(
                "SELECT ruleset,COUNT(*) AS total,SUM(expires_at<=?) AS expired FROM league_queue GROUP BY ruleset",
                (at,),
            ).fetchall()
            stale, next_cursor = self._stale_entries(c, at, actor["id"], stale_cursor)
            pending, failed, oldest, attempts, retry_at = c.execute(
                """SELECT COUNT(*),SUM(attempts>0),MIN(pending_since),MAX(attempts),
                    MIN(CASE WHEN attempts>0 THEN due_at END)
                FROM league_settlement_queue"""
            ).fetchone()
            rebuilds = [
                {**dict(row), "due_at": stamp(instant(row["due_at"]))}
                for row in c.execute(
                    """SELECT season_id,stage,attempts,due_at FROM league_rebuild_jobs
                    ORDER BY due_at,season_id LIMIT 20"""
                )
            ]
            rebuild_count, rebuild_retries = c.execute(
                "SELECT COUNT(*),SUM(attempts>0) FROM league_rebuild_jobs"
            ).fetchone()
            return {
                "settings": {"paused": bool(paused), "version": version},
                "queue": {
                    "total": sum(row["total"] for row in queue),
                    "expired": sum(row["expired"] for row in queue),
                    "by_ruleset": {row["ruleset"]: row["total"] for row in queue},
                    "stale": stale,
                    "stale_next_cursor": next_cursor,
                },
                "settlement": {
                    "pending": pending,
                    "failed": failed or 0,
                    "max_attempts": attempts or 0,
                    "next_retry_at": stamp(instant(retry_at)) if retry_at is not None else None,
                    "oldest_pending_seconds": max(0, (at - oldest) // 1000000)
                    if oldest is not None
                    else None,
                },
                "rebuilds": rebuilds,
                "rebuild_pending": rebuild_count,
                "rebuild_retries": rebuild_retries or 0,
            }

    def audit(self, credential, limit, cursor):
        with self.views.snapshot(credential, admin=True) as (c, actor, _):
            scope = ("audit", actor["id"], 1)
            args: tuple[int, ...]
            if cursor:
                (after,) = self.views.cursors.decode(cursor, scope)
                predicate, args = "WHERE sequence<?", (after,)
            else:
                predicate, args = "", ()
            rows = c.execute(
                f"""SELECT sequence,actor_id,action,season_id,at,reason FROM league_audit
                    {predicate} ORDER BY sequence DESC LIMIT ?""",
                (*args, limit + 1),
            ).fetchall()
            return {
                "items": [{**dict(row), "at": stamp(instant(row["at"]))} for row in rows[:limit]],
                "next_cursor": self.views.cursors.encode(scope, (rows[limit - 1]["sequence"],))
                if len(rows) > limit
                else None,
            }

    def configure(self, credential, version, paused, reason):
        reason = _reason(reason)
        with self.online._write() as c:
            actor = self.online._identity(c, credential, admin=True)
            row = c.execute("SELECT paused,version FROM league_settings WHERE id=1").fetchone()
            if row["version"] != version:
                raise LeagueError(
                    409,
                    "league_version_changed",
                    "League settings changed. Reload before retrying.",
                )
            if bool(row["paused"]) != paused:
                c.execute(
                    "UPDATE league_settings SET paused=?,version=version+1 WHERE id=1",
                    (int(paused),),
                )
                version += 1
                _audit(
                    c,
                    actor["id"],
                    "pause_matchmaking" if paused else "resume_matchmaking",
                    self.online._now(),
                    reason,
                    details={"version": version},
                )
            return {"paused": paused, "version": version}

    def cancel_stale(self, credential, request_id, reason):
        reason = _reason(reason)
        with self.online._write() as c:
            actor = self.online._identity(c, credential, admin=True)
            now = self.online._now()
            row = c.execute(
                """SELECT q.*,a.active,a.role,a.email_verified_at
                    FROM league_queue q JOIN accounts a ON a.id=q.account_id
                    WHERE q.request_id=?""",
                (request_id,),
            ).fetchone()
            if row is None:
                raise LeagueError(
                    409,
                    "league_version_changed",
                    "League queue entry changed. Reload before retrying.",
                )
            stale = _stale_reason(row, micros(now))
            if stale is None:
                raise LeagueError(
                    409, "league_queue_not_stale", "This league queue entry is still valid."
                )
            status = "expired" if stale == "expired" else "cancelled"
            self.core._cancel(c, row["account_id"], status, request_id=request_id)
            _audit(
                c,
                actor["id"],
                "cancel_stale_queue",
                now,
                reason,
                details={"request_id": request_id},
            )
            return {"request_id": request_id, "status": status}

    def rebuild(self, credential, identifier, version, reason):
        reason = _reason(reason)
        with self.online._write() as c:
            actor = self.online._identity(c, credential, admin=True)
            now = self.online._now()
            row = c.execute("SELECT * FROM league_seasons WHERE id=?", (identifier,)).fetchone()
            if row is None:
                raise LeagueError(404, "league_season_missing", "League season not found.")
            if row["revision"] != version:
                raise LeagueError(
                    409, "league_version_changed", "League season changed. Reload before retrying."
                )
            if c.execute(
                "SELECT 1 FROM league_rebuild_jobs WHERE season_id=?", (identifier,)
            ).fetchone():
                raise LeagueError(
                    409, "league_rebuilding", "League season rebuild is already in progress."
                )
            c.execute(
                "INSERT INTO league_rebuild_jobs(season_id,due_at) VALUES(?,?)",
                (identifier, micros(now)),
            )
            _audit(
                c,
                actor["id"],
                "request_rebuild",
                now,
                reason,
                season_id=identifier,
                details={"revision": version},
            )
            return {"season": season_view(row, now, rebuilding=True)}

    @staticmethod
    def _advance(c, season_id, stage):
        c.execute("UPDATE league_rebuild_jobs SET stage=? WHERE season_id=?", (stage, season_id))

    def _clear(self, c, season_id, stage, limit):
        table, order, next_stage = {
            "clear_contributions": (
                "league_contributions",
                "ruleset,account_id,sequence",
                "clear_standings",
            ),
            "clear_standings": ("league_standings", "ruleset,account_id", "restore_games"),
        }[stage]
        removed = c.execute(
            f"""DELETE FROM {table} WHERE rowid IN (
                SELECT rowid FROM {table} WHERE season_id=? ORDER BY {order} LIMIT ?
            )""",
            (season_id, limit),
        ).rowcount
        if removed < limit:
            self._advance(c, season_id, next_stage)

    def _restore_games(self, c, job, limit):
        identifier = job["season_id"]
        rows = c.execute(
            """SELECT l.game_id,g.status FROM league_games l JOIN online_games g ON g.id=l.game_id
               WHERE l.season_id=? AND l.game_id>? ORDER BY l.game_id LIMIT ?""",
            (identifier, job["last_game_id"], limit),
        ).fetchall()
        for row in rows:
            game_id = row["game_id"]
            effective = effective_league_result(c, game_id)
            record_contributions(c, effective)
            write_game_result(c, effective)
            if row["status"] == "active":
                self._restore_seats(c, effective)
            else:
                c.execute("DELETE FROM league_active_players WHERE game_id=?", (game_id,))
            c.execute("DELETE FROM league_settlement_queue WHERE game_id=?", (game_id,))
        if len(rows) < limit:
            self._advance(c, identifier, "fold_standings")
        else:
            c.execute(
                "UPDATE league_rebuild_jobs SET last_game_id=? WHERE season_id=?",
                (rows[-1]["game_id"], identifier),
            )

    def _restore_seats(self, c, effective):
        for account_id in effective.accounts:
            seat = c.execute(
                """SELECT p.game_id,g.status FROM league_active_players p
                   JOIN online_games g ON g.id=p.game_id WHERE p.account_id=?""",
                (account_id,),
            ).fetchone()
            if seat is None or seat["game_id"] != effective.game_id:
                if seat is not None and seat["status"] == "active":
                    raise ValueError("Conflicting active league seats")
                c.execute(
                    """INSERT INTO league_active_players(account_id,game_id) VALUES(?,?)
                       ON CONFLICT(account_id) DO UPDATE SET game_id=excluded.game_id""",
                    (account_id, effective.game_id),
                )
            queued = self.core._queue_entry(c, account_id)
            if queued is not None:
                self.core._cancel(c, account_id, "cancelled", request_id=queued["request_id"])

    def _fold_standings(self, c, job, limit):
        identifier = job["season_id"]
        rows = c.execute(
            """SELECT p.ruleset,p.account_id,p.sequence,p.outcome,r.created_at FROM league_contributions p
                JOIN rating_events r ON r.game_id=p.game_id
                WHERE p.season_id=? AND (p.ruleset,p.account_id,p.sequence)>(?,?,?)
                ORDER BY p.ruleset,p.account_id,p.sequence LIMIT ?""",
            (identifier, job["last_ruleset"], job["last_account_id"], job["last_sequence"], limit),
        ).fetchall()
        for ruleset, account_id, sequence, outcome, recorded_at in rows:
            previous = c.execute(
                "SELECT * FROM league_standings WHERE season_id=? AND ruleset=? AND account_id=?",
                (identifier, ruleset, account_id),
            ).fetchone()
            candidate = c.execute(
                """SELECT candidate_tier FROM league_achievement_reconciliation
                   WHERE account_id=? AND season_id=? AND ruleset=?""",
                (account_id, identifier, ruleset),
            ).fetchone()
            progress = policy.Progress(
                standing_value(previous),
                policy.tier_for_rank(candidate[0] if candidate is not None else None),
            ).apply(outcome)
            save_standing(
                c,
                identifier,
                ruleset,
                account_id,
                progress.standing,
                sequence,
                instant(recorded_at),
                previous,
            )
            previous_rank = candidate[0] if candidate is not None and candidate[0] else 0
            if policy.tier_rank(progress.highest_tier) > previous_rank:
                record_league_candidate(
                    c,
                    account_id,
                    identifier,
                    ruleset,
                    progress.highest_tier,
                    micros(instant(recorded_at)),
                )
        if len(rows) < limit:
            self._advance(c, identifier, "catch_up")
        else:
            cursor = tuple(rows[-1])[:3]
            c.execute(
                """UPDATE league_rebuild_jobs SET last_ruleset=?,last_account_id=?,last_sequence=?
                    WHERE season_id=?""",
                (*cursor, identifier),
            )

    def _catch_up(self, c, identifier, now):
        # One correction per writer lock during catch-up. Its exact player
        # replay can be longer than an ordinary append. A live completion may
        # enqueue here any time between rebuild transactions.
        queued = c.execute(
            """SELECT game_id FROM league_settlement_queue WHERE season_id=?
               ORDER BY due_at,game_id LIMIT 1""",
            (identifier,),
        ).fetchone()
        if queued:
            self.core.settle(c, queued["game_id"], now)
            c.execute("DELETE FROM league_settlement_queue WHERE game_id=?", (queued["game_id"],))
            return
        c.execute("UPDATE league_seasons SET revision=revision+1 WHERE id=?", (identifier,))
        c.execute("DELETE FROM league_rebuild_jobs WHERE season_id=?", (identifier,))
        _audit(
            c,
            None,
            "complete_rebuild",
            now,
            "Authoritative projection repair completed.",
            season_id=identifier,
        )

    def process_rebuild(self, limit=50) -> Literal["idle", "processed", "retry"]:
        """Advance one due phase in a short transaction and retain its durable cursor."""
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("League rebuild batch must be between 1 and 100")
        with self.core.store.connection() as c:
            due = c.execute(
                "SELECT season_id FROM league_rebuild_jobs WHERE due_at<=? ORDER BY due_at,season_id LIMIT 1",
                (micros(self.online._now()),),
            ).fetchone()
        if due is None:
            return "idle"
        identifier = due["season_id"]
        job = None
        try:
            with self.online._write() as c:
                now = self.online._now()
                job = c.execute(
                    "SELECT * FROM league_rebuild_jobs WHERE season_id=? AND due_at<=?",
                    (identifier, micros(now)),
                ).fetchone()
                if job is None:
                    return "idle"
                stage = job["stage"]
                if stage in {"clear_contributions", "clear_standings"}:
                    self._clear(c, identifier, stage, limit)
                elif stage == "restore_games":
                    self._restore_games(c, job, limit)
                elif stage == "fold_standings":
                    self._fold_standings(c, job, limit)
                else:
                    self._catch_up(c, identifier, now)
                c.execute(
                    "UPDATE league_rebuild_jobs SET due_at=?,attempts=0,last_error=NULL WHERE season_id=?",
                    (micros(now), identifier),
                )
        except DeploymentLocked:
            raise
        except Exception as error:
            if job is None:
                raise
            self._defer_rebuild(job)
            LOGGER.warning("League season recovery deferred (%s)", type(error).__name__)
            return "retry"
        return "processed"

    def _defer_rebuild(self, job):
        # The failed transaction has already rolled back. A deployment may
        # begin in the interval, leaving the job untouched for future retries.
        with self.online._write() as c:
            now = self.online._now()
            attempts = min(job["attempts"] + 1, 20)
            delay = min(300, 2 ** min(attempts, 9))
            c.execute(
                """UPDATE league_rebuild_jobs SET due_at=?,attempts=?,last_error='rebuild_deferred'
                   WHERE season_id=? AND due_at=? AND attempts=? AND stage=? AND last_game_id=?
                   AND last_ruleset=? AND last_account_id=? AND last_sequence=?""",
                (
                    micros(now + timedelta(seconds=delay)),
                    attempts,
                    job["season_id"],
                    job["due_at"],
                    job["attempts"],
                    job["stage"],
                    job["last_game_id"],
                    job["last_ruleset"],
                    job["last_account_id"],
                    job["last_sequence"],
                ),
            )
