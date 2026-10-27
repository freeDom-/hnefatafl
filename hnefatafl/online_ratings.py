"""Durable rating events, applications, rebuilds and leaderboard queries.

Every method runs inside the caller's ``BEGIN IMMEDIATE`` transaction, so a
result revision, its rating event and its application records commit or roll
back together. The event sequence is assigned under that writer lock and never
changes when a result revision is corrected. A rebuild replays applied events in
sequence order inside one transaction, so readers never observe a partial pool.
"""

from __future__ import annotations

from datetime import datetime, timezone

from . import rating
from .engine import RULESETS

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_POOLS = tuple(sorted(RULESETS))


def _micros(moment: datetime) -> int:
    delta = moment.astimezone(timezone.utc) - _EPOCH
    return (delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds


class Ratings:
    """Rating persistence bound to the online writer lock."""

    @staticmethod
    def _eligible(c, account_id: str) -> bool:
        row = c.execute(
            "SELECT active,email_verified_at FROM accounts WHERE id=?", (account_id,)
        ).fetchone()
        return bool(row and row["active"] == 1 and row["email_verified_at"] is not None)

    @staticmethod
    def _state(c, account_id: str, pool: str):
        row = c.execute(
            "SELECT rating,rated_games,wins,losses,draws FROM player_ratings WHERE account_id=? AND pool=?",
            (account_id, pool),
        ).fetchone()
        if row is None:
            return (rating.INITIAL_RATING, 0, 0, 0, 0)
        return (row["rating"], row["rated_games"], row["wins"], row["losses"], row["draws"])

    def _apply_one(self, c, pool: str, event, now: datetime) -> None:
        at = _micros(now)
        before = {
            "attackers": self._state(c, event["attackers_id"], pool),
            "defenders": self._state(c, event["defenders_id"], pool),
        }
        for side in rating.SIDES:
            other = "defenders" if side == "attackers" else "attackers"
            account, opponent = event[side + "_id"], event[other + "_id"]
            current, games, wins, losses, draws = before[side]
            opponent_rating = before[other][0]
            score = rating.score_for(event["winner"], side)
            expected = rating.expected_score(current, opponent_rating)
            after = current + rating.K_FACTOR * (score - expected)
            c.execute(
                """INSERT OR REPLACE INTO rating_applications(
                     game_id,account_id,pool,sequence,opponent_id,rating_before,rating_after,
                     delta,opponent_rating,expected,score,rated_games_after,policy_version)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    event["game_id"],
                    account,
                    pool,
                    event["sequence"],
                    opponent,
                    current,
                    after,
                    after - current,
                    opponent_rating,
                    expected,
                    score,
                    games + 1,
                    rating.RATING_POLICY_VERSION,
                ),
            )
            c.execute(
                """INSERT INTO player_ratings(
                     account_id,pool,rating,rated_games,wins,losses,draws,policy_version,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(account_id,pool) DO UPDATE SET
                     rating=excluded.rating, rated_games=excluded.rated_games,
                     wins=excluded.wins, losses=excluded.losses, draws=excluded.draws,
                     policy_version=excluded.policy_version, updated_at=excluded.updated_at""",
                (
                    account,
                    pool,
                    after,
                    games + 1,
                    wins + int(score == 1.0),
                    losses + int(score == 0.0),
                    draws + int(score == 0.5),
                    rating.RATING_POLICY_VERSION,
                    at,
                ),
            )

    def rebuild_pool(self, c, pool: str, now: datetime) -> None:
        c.execute("DELETE FROM rating_applications WHERE pool=?", (pool,))
        c.execute("DELETE FROM player_ratings WHERE pool=?", (pool,))
        events = c.execute(
            "SELECT * FROM rating_events WHERE pool=? AND state='applied' ORDER BY sequence",
            (pool,),
        ).fetchall()
        for event in events:
            self._apply_one(c, pool, event, now)

    def rebuild_all(self, c, now: datetime) -> None:
        for pool in _POOLS:
            self.rebuild_pool(c, pool, now)

    def apply(self, c, match_id: str, game_id: str, result, now: datetime) -> None:
        """Apply or withdraw the rating event for one terminal result revision.

        Idempotent: a repeated notification for the same effective revision is a
        no-op, while a changed revision preserves the event's sequence position
        and deterministically rebuilds the affected pool.
        """
        match = c.execute("SELECT rated FROM online_matches WHERE id=?", (match_id,)).fetchone()
        if match is None or not match["rated"]:
            return
        pool = result.ruleset
        existing = c.execute("SELECT * FROM rating_events WHERE game_id=?", (game_id,)).fetchone()
        eligible = (
            result.competitive_eligible
            and result.winner is not None
            and self._eligible(c, result.attackers)
            and self._eligible(c, result.defenders)
        )
        if not eligible:
            if existing is not None and existing["state"] == "applied":
                c.execute(
                    "UPDATE rating_events SET state='withdrawn',effective_revision=?,completed_at=? WHERE game_id=?",
                    (result.revision, _micros(result.completed_at), game_id),
                )
                self.rebuild_pool(c, pool, now)
            return
        if existing is not None:
            if existing["state"] == "applied" and existing["effective_revision"] == result.revision:
                return
            c.execute(
                """UPDATE rating_events SET state='applied',effective_revision=?,winner=?,
                     attackers_id=?,defenders_id=?,completed_at=?,policy_version=? WHERE game_id=?""",
                (
                    result.revision,
                    result.winner,
                    result.attackers,
                    result.defenders,
                    _micros(result.completed_at),
                    rating.RATING_POLICY_VERSION,
                    game_id,
                ),
            )
            self.rebuild_pool(c, pool, now)
            return
        sequence = c.execute("SELECT COALESCE(MAX(sequence),0)+1 FROM rating_events").fetchone()[0]
        c.execute(
            """INSERT INTO rating_events(game_id,pool,sequence,state,effective_revision,winner,
                 attackers_id,defenders_id,completed_at,policy_version,created_at)
               VALUES(?,?,?,'applied',?,?,?,?,?,?,?)""",
            (
                game_id,
                pool,
                sequence,
                result.revision,
                result.winner,
                result.attackers,
                result.defenders,
                _micros(result.completed_at),
                rating.RATING_POLICY_VERSION,
                _micros(now),
            ),
        )
        event = c.execute("SELECT * FROM rating_events WHERE game_id=?", (game_id,)).fetchone()
        self._apply_one(c, pool, event, now)

    @staticmethod
    def _entry(row, rank: int) -> dict:
        return {
            "account_id": row["account_id"],
            "name": str(row["name"])[:80],
            "rating": row["rating"],
            "rated_games": row["rated_games"],
            "wins": row["wins"],
            "losses": row["losses"],
            "draws": row["draws"],
            "rank": rank,
            "provisional": row["rated_games"] < rating.PROVISIONAL_GAMES,
        }

    # Ranking and presentation both round Elo to the nearest integer, so the
    # displayed value and the order can never disagree. Equal displayed Elo is
    # broken by more completed rated games, then by the immutable account id.
    _ORDER = "ORDER BY CAST(ROUND(pr.rating) AS INTEGER) DESC,pr.rated_games DESC,pr.account_id ASC"
    _RANK = (
        "CAST(ROUND(pr.rating) AS INTEGER) > CAST(ROUND(?) AS INTEGER)"
        " OR (CAST(ROUND(pr.rating) AS INTEGER) = CAST(ROUND(?) AS INTEGER)"
        " AND (pr.rated_games > ? OR (pr.rated_games = ? AND pr.account_id < ?)))"
    )

    @staticmethod
    def _standing(c, pool: str, account_id: str):
        row = c.execute(
            """SELECT pr.account_id,pr.rating,pr.rated_games,pr.wins,pr.losses,pr.draws,
                      coalesce(a.display_name,a.username) AS name
               FROM player_ratings pr JOIN accounts a ON a.id=pr.account_id
               WHERE pr.account_id=? AND pr.pool=?""",
            (account_id, pool),
        ).fetchone()
        if row is None:
            return None
        rank = c.execute(
            "SELECT COUNT(*)+1 FROM player_ratings pr JOIN accounts a ON a.id=pr.account_id "
            f"WHERE pr.pool=? AND a.active=1 AND ({Ratings._RANK})",
            (
                pool,
                row["rating"],
                row["rating"],
                row["rated_games"],
                row["rated_games"],
                account_id,
            ),
        ).fetchone()[0]
        return Ratings._entry(row, rank)

    @classmethod
    def leaderboard(cls, c, account_id: str, pool: str, limit: int, offset: int) -> dict:
        columns = """pr.account_id,pr.rating,pr.rated_games,pr.wins,pr.losses,pr.draws,
                     coalesce(a.display_name,a.username) AS name"""
        total = c.execute(
            "SELECT COUNT(*) FROM player_ratings pr JOIN accounts a ON a.id=pr.account_id "
            "WHERE pr.pool=? AND a.active=1",
            (pool,),
        ).fetchone()[0]
        rows = c.execute(
            f"SELECT {columns} FROM player_ratings pr JOIN accounts a ON a.id=pr.account_id "
            f"WHERE pr.pool=? AND a.active=1 {cls._ORDER} LIMIT ? OFFSET ?",
            (pool, limit, offset),
        ).fetchall()
        return {
            "ruleset": pool,
            "policy": {
                "version": rating.RATING_POLICY_VERSION,
                "initial_rating": rating.INITIAL_RATING,
                "k_factor": rating.K_FACTOR,
                "provisional_games": rating.PROVISIONAL_GAMES,
            },
            "entries": [cls._entry(row, offset + index + 1) for index, row in enumerate(rows)],
            "total": total,
            "limit": limit,
            "offset": offset,
            "me": cls._standing(c, pool, account_id),
        }
