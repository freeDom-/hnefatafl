"""Server-owned human league queue and seasonal result projections.

Every mutation shares OnlineCore's deployment gate and SQLite writer lock.
Official games, clocks, move validation and Elo remain in the online authority.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID, uuid4

from . import league_policy as policy
from . import lifecycle, rating
from .engine import RULESETS
from .online_core import OnlineCore, OnlineError, instant, micros, stamp
from .security_context import online_eligible


class Leagues:
    def __init__(self, online: OnlineCore):
        self.online = online
        self.store = online.store

    @staticmethod
    def _ruleset(ruleset: str) -> None:
        if ruleset not in RULESETS:
            raise OnlineError(422, "A supported league ruleset is required.")

    @staticmethod
    def _season(c, now: datetime):
        season = policy.season_for(now)
        c.execute(
            "INSERT OR IGNORE INTO league_seasons(id,starts_at,ends_at,grace_ends_at,policy_version) VALUES(?,?,?,?,?)",
            (
                season.identifier,
                micros(season.start),
                micros(season.end),
                micros(season.grace_end),
                policy.POLICY_VERSION,
            ),
        )
        return season

    @staticmethod
    def _cancel(c, account_id: str, status: str) -> None:
        c.execute(
            "UPDATE league_requests SET status=? WHERE account_id=? AND status='queued'",
            (status, account_id),
        )
        c.execute("DELETE FROM league_queue WHERE account_id=?", (account_id,))

    def _own_status(self, c, account_id: str, now: datetime):
        active = c.execute(
            "SELECT a.game_id,g.ruleset FROM league_active_players a JOIN league_games g ON g.game_id=a.game_id WHERE a.account_id=?",
            (account_id,),
        ).fetchone()
        if active:
            return {"status": "matched", "game_id": active["game_id"], "ruleset": active["ruleset"]}
        queued = c.execute(
            "SELECT * FROM league_queue WHERE account_id=?", (account_id,)
        ).fetchone()
        if queued:
            return {
                "status": "queued" if micros(now) < queued["expires_at"] else "expired",
                "ruleset": queued["ruleset"],
                "request_id": queued["request_id"],
                "joined_at": stamp(instant(queued["joined_at"])),
                "expires_at": stamp(instant(queued["expires_at"])),
                "rating_window": policy.rating_window((micros(now) - queued["joined_at"]) / 1e6),
            }
        return {"status": "idle"}

    def queue_status(self, credential: str):
        with self.store.connection() as c:
            actor = self.online._identity(c, credential)
            now = self.online._now()
            return {
                **self._own_status(c, actor["id"], now),
                "eligible": online_eligible(actor),
                "paused": bool(
                    c.execute("SELECT paused FROM league_settings WHERE id=1").fetchone()[0]
                ),
                "server_time": stamp(now),
            }

    def join(self, credential: str, ruleset: str, request_id: str):
        self._ruleset(ruleset)
        try:
            request_id = str(UUID(request_id))
        except (TypeError, ValueError, AttributeError):
            raise OnlineError(422, "A league request UUID is required.") from None
        self.online._admission()
        with self.online._write() as c:
            actor = self.online._identity(c, credential, admission=True)
            now = self.online._now()
            previous = c.execute(
                "SELECT * FROM league_requests WHERE id=?", (request_id,)
            ).fetchone()
            if previous:
                if previous["account_id"] != actor["id"] or previous["ruleset"] != ruleset:
                    raise OnlineError(409, "The request cannot be reused for this queue entry.")
                if previous["game_id"]:
                    return {"status": "matched", "game_id": previous["game_id"], "ruleset": ruleset}
                if previous["status"] != "queued":
                    return {"status": previous["status"], "ruleset": ruleset}
                return self._own_status(c, actor["id"], now)
            if c.execute(
                "SELECT 1 FROM league_active_players WHERE account_id=?", (actor["id"],)
            ).fetchone():
                raise OnlineError(409, "Finish your current league game before queuing again.")
            if c.execute("SELECT paused FROM league_settings WHERE id=1").fetchone()[0]:
                raise OnlineError(503, "League matchmaking is paused.")
            if not self.online._gate_enabled(c, now):
                raise OnlineError(503, "Online clocks are paused; league matching is unavailable.")
            queued = c.execute(
                "SELECT * FROM league_queue WHERE account_id=?", (actor["id"],)
            ).fetchone()
            if queued and queued["expires_at"] <= micros(now):
                self._cancel(c, actor["id"], "expired")
                queued = None
            if queued and queued["ruleset"] != ruleset:
                raise OnlineError(409, "Cancel your current league queue before changing ruleset.")
            day = now.replace(hour=0, minute=0, second=0, microsecond=0)
            requests = c.execute(
                """SELECT sum(created_at>=?),sum(created_at>=?)
                FROM league_requests WHERE account_id=? AND created_at>=?""",
                (
                    micros(day),
                    micros(now - timedelta(minutes=1)),
                    actor["id"],
                    micros(min(day, now - timedelta(minutes=1))),
                ),
            ).fetchone()
            if (requests[0] or 0) >= 100 or (requests[1] or 0) >= 10:
                raise OnlineError(429, "League queue request limit reached. Try again later.")
            season = self._season(c, now)
            c.execute(
                "INSERT INTO league_requests(id,account_id,ruleset,created_at,status) VALUES(?,?,?,?,'queued')",
                (request_id, actor["id"], ruleset, micros(now)),
            )
            if not queued:
                c.execute(
                    "INSERT INTO league_queue VALUES(?,?,?,?,?)",
                    (
                        actor["id"],
                        request_id,
                        ruleset,
                        micros(now),
                        micros(min(season.end, now + timedelta(seconds=policy.QUEUE_SECONDS))),
                    ),
                )
            self._match(c, now, ruleset)
            return self._own_status(c, actor["id"], now)

    def cancel(self, credential: str, request_id: str):
        try:
            request_id = str(UUID(request_id))
        except (TypeError, ValueError, AttributeError):
            raise OnlineError(422, "A league request UUID is required.") from None
        with self.online._write() as c:
            actor = self.online._identity(c, credential)
            now = self.online._now()
            requested = c.execute(
                "SELECT * FROM league_requests WHERE id=? AND account_id=?",
                (request_id, actor["id"]),
            ).fetchone()
            if requested is None:
                raise OnlineError(404, "League queue request not found.")
            if requested["status"] != "queued":
                result = {"status": requested["status"], "ruleset": requested["ruleset"]}
                if requested["game_id"]:
                    result["game_id"] = requested["game_id"]
                return result
            queued = c.execute(
                "SELECT expires_at FROM league_queue WHERE account_id=?", (actor["id"],)
            ).fetchone()
            status = "expired" if queued and queued["expires_at"] <= micros(now) else "cancelled"
            self._cancel(c, actor["id"], status)
            # A simultaneous match wins atomically; cancellation cannot stop it.
            # Replayed cancellations target their original request, never a new entry.
            return {"status": status, "ruleset": requested["ruleset"]}

    def _match(self, c, now: datetime, ruleset: str) -> bool:
        if c.execute("SELECT paused FROM league_settings WHERE id=1").fetchone()[0]:
            return False
        if (
            not self.online.settings.enabled
            or not self.online.settings.creation_policy_approved
            or not self.online._gate_enabled(c, now)
        ):
            return False
        rows = c.execute(
            """SELECT q.*,a.active,a.email_verified_at,a.role,
            COALESCE(r.rating,?) AS rating FROM league_queue q
            JOIN accounts a ON a.id=q.account_id
            LEFT JOIN player_ratings r ON r.account_id=q.account_id AND r.pool=q.ruleset
            WHERE q.ruleset=? ORDER BY q.joined_at,q.account_id LIMIT 32""",
            (rating.INITIAL_RATING, ruleset),
        ).fetchall()
        candidates = []
        for row in rows:
            if row["expires_at"] <= micros(now):
                self._cancel(c, row["account_id"], "expired")
            elif not online_eligible(dict(row)):
                self._cancel(c, row["account_id"], "cancelled")
            else:
                candidates.append(row)
        day = micros(now.replace(hour=0, minute=0, second=0, microsecond=0))
        # FIFO candidates and bounded pair comparisons; no queue-wide scan.
        for i, first in enumerate(candidates):
            for second in candidates[i + 1 :]:
                if not policy.compatible_ratings(
                    first["rating"],
                    second["rating"],
                    (micros(now) - first["joined_at"]) / 1e6,
                    (micros(now) - second["joined_at"]) / 1e6,
                ):
                    continue
                pair = sorted((first["account_id"], second["account_id"]))
                previous = c.execute(
                    "SELECT count(*) FROM league_games WHERE first_account=? AND second_account=? AND paired_at>=?",
                    (*pair, day),
                ).fetchone()[0]
                if previous >= policy.PAIR_DAILY_LIMIT:
                    continue
                season = self._season(c, now)
                game_id = self.online.seat_game(
                    c,
                    creator_id=first["account_id"],
                    opponent_id=second["account_id"],
                    ruleset=ruleset,
                    creator_side="attackers" if previous % 2 == 0 else "defenders",
                    control=lifecycle.TimeControl(
                        policy.CLOCK_SECONDS, "total", policy.INCREMENT_SECONDS
                    ),
                    now=now,
                    request_id=str(uuid4()),
                    rated=True,
                )
                c.execute(
                    "INSERT INTO league_games(game_id,season_id,ruleset,first_account,second_account,paired_at) VALUES(?,?,?,?,?,?)",
                    (game_id, season.identifier, ruleset, *pair, micros(now)),
                )
                for account_id in pair:
                    c.execute(
                        "INSERT INTO league_active_players VALUES(?,?)", (account_id, game_id)
                    )
                    c.execute(
                        "UPDATE league_requests SET status='matched',game_id=? WHERE account_id=? AND status='queued'",
                        (game_id, account_id),
                    )
                    c.execute("DELETE FROM league_queue WHERE account_id=?", (account_id,))
                return True
        return False

    @staticmethod
    def _rebuild_player(c, season_id: str, ruleset: str, account_id: str, now: datetime):
        rows = c.execute(
            "SELECT game_id,sequence,outcome FROM league_contributions WHERE season_id=? AND ruleset=? AND account_id=? ORDER BY sequence",
            (season_id, ruleset, account_id),
        ).fetchall()
        standing = policy.fold_contributions(
            [policy.Contribution(row["game_id"], row["sequence"], row["outcome"]) for row in rows]
        )
        c.execute(
            """INSERT INTO league_standings VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(season_id,ruleset,account_id) DO UPDATE SET
            points=excluded.points,wins=excluded.wins,draws=excluded.draws,
            losses=excluded.losses,updated_at=excluded.updated_at""",
            (
                season_id,
                ruleset,
                account_id,
                standing.points,
                standing.wins,
                standing.draws,
                standing.losses,
                micros(now),
            ),
        )
        return standing

    def settle(self, c, game_id: str, now: datetime) -> bool:
        """Replace one effective result contribution inside the online writer lock."""
        row = c.execute(
            """SELECT l.*,g.result_revision AS official_revision,g.status,
            s.starts_at,s.grace_ends_at FROM league_games l
            JOIN online_games g ON g.id=l.game_id JOIN league_seasons s ON s.id=l.season_id
            WHERE l.game_id=?""",
            (game_id,),
        ).fetchone()
        if row is None:
            raise OnlineError(404, "League game not found.")
        if row["official_revision"] is None:
            return False
        if row["result_revision"] == row["official_revision"]:
            return False
        event = c.execute("SELECT * FROM rating_events WHERE game_id=?", (game_id,)).fetchone()
        eligible = bool(
            event
            and event["state"] == "applied"
            and event["effective_revision"] == row["official_revision"]
        )
        state = "withdrawn"
        if eligible:
            if event["pool"] != row["ruleset"] or {
                event["attackers_id"],
                event["defenders_id"],
            } != {row["first_account"], row["second_account"]}:
                raise ValueError("League provenance does not match authoritative rated seats")
            state = (
                "eligible"
                if row["starts_at"] <= event["completed_at"] < row["grace_ends_at"]
                else "late"
            )
        c.execute("DELETE FROM league_contributions WHERE game_id=?", (game_id,))
        if state == "eligible":
            for side in ("attackers", "defenders"):
                outcome = (
                    "draw"
                    if event["winner"] == "draw"
                    else "win"
                    if event["winner"] == side
                    else "loss"
                )
                c.execute(
                    "INSERT INTO league_contributions VALUES(?,?,?,?,?,?)",
                    (
                        game_id,
                        event[side + "_id"],
                        row["season_id"],
                        row["ruleset"],
                        event["sequence"],
                        outcome,
                    ),
                )
        for account_id in (row["first_account"], row["second_account"]):
            self._rebuild_player(c, row["season_id"], row["ruleset"], account_id, now)
        c.execute(
            "UPDATE league_games SET result_revision=?,sequence=?,state=? WHERE game_id=?",
            (row["official_revision"], event["sequence"] if event else None, state, game_id),
        )
        c.execute("UPDATE league_seasons SET revision=revision+1 WHERE id=?", (row["season_id"],))
        c.execute("DELETE FROM league_active_players WHERE game_id=?", (game_id,))
        return True

    def drain(self, limit: int = 20):
        """Bounded durable result processing, expiry and fair queue matching."""
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("League settlement batch must be between 1 and 100")
        now = self.online._now()
        with self.store.connection() as c:
            due = [
                row[0]
                for row in c.execute(
                    "SELECT game_id FROM league_settlement_queue WHERE due_at<=? ORDER BY due_at,game_id LIMIT ?",
                    (micros(now), limit),
                )
            ]
        completed = 0
        for game_id in due:
            try:
                with self.online._write() as c:
                    now = self.online._now()
                    if not c.execute(
                        "SELECT 1 FROM league_settlement_queue WHERE game_id=? AND due_at<=?",
                        (game_id, micros(now)),
                    ).fetchone():
                        continue
                    self.settle(c, game_id, now)
                    c.execute("DELETE FROM league_settlement_queue WHERE game_id=?", (game_id,))
                    completed += 1
            except Exception:
                # Preserve durable work and neutral diagnostics; no private payloads.
                with self.online._write() as c:
                    now = self.online._now()
                    queued = c.execute(
                        "SELECT attempts FROM league_settlement_queue WHERE game_id=?", (game_id,)
                    ).fetchone()
                    if queued:
                        attempts = min(queued["attempts"] + 1, 20)
                        delay = min(300, 2 ** min(attempts, 8))
                        c.execute(
                            "UPDATE league_settlement_queue SET attempts=?,due_at=?,last_error='accounting_deferred' WHERE game_id=?",
                            (attempts, micros(now + timedelta(seconds=delay)), game_id),
                        )
        with self.online._write() as c:
            now = self.online._now()
            expired = c.execute(
                "SELECT account_id FROM league_queue WHERE expires_at<=? ORDER BY expires_at,account_id LIMIT ?",
                (micros(now), limit),
            ).fetchall()
            for row in expired:
                self._cancel(c, row["account_id"], "expired")
        matched = 0
        for ruleset in sorted(RULESETS):
            with self.online._write() as c:
                try:
                    matched += int(self._match(c, self.online._now(), ruleset))
                except OnlineError as error:
                    if error.status != 503:
                        raise
        return {"settled": completed, "expired": len(expired), "matched": matched}
