"""Project official league results in the existing authoritative writer lock."""

from dataclasses import dataclass
from datetime import datetime
import sqlite3

from . import league_policy as policy
from .online_core import OnlineError, micros
from .progression import record_league_tier


@dataclass(frozen=True)
class EffectiveLeagueResult:
    game_id: str
    season_id: str
    ruleset: str
    accounts: tuple[str, str]
    revision: int | None
    sequence: int | None
    state: str
    contributions: tuple[tuple[str, policy.Contribution], ...]
    projection: tuple[int | None, int | None, str]


def effective_league_result(c: sqlite3.Connection, game_id: str) -> EffectiveLeagueResult:
    """Resolve an official result independently of cached league projections."""
    row = c.execute(
        """SELECT l.*,g.result_revision AS official_revision,g.ruleset AS official_ruleset,
            g.attackers_id,g.defenders_id,m.rated,s.starts_at,s.ends_at,s.grace_ends_at,
            r.sequence AS rating_sequence,r.pool,r.state AS rating_state,r.effective_revision,
            r.attackers_id AS rating_attackers,r.defenders_id AS rating_defenders,
            r.winner,r.completed_at
        FROM league_games l JOIN online_games g ON g.id=l.game_id
        JOIN online_matches m ON m.id=g.match_id JOIN league_seasons s ON s.id=l.season_id
        LEFT JOIN rating_events r ON r.game_id=l.game_id WHERE l.game_id=?""",
        (game_id,),
    ).fetchone()
    if row is None:
        raise OnlineError(404, "League game not found.")
    accounts = (row["first_account"], row["second_account"])
    if (
        not row["rated"]
        or row["official_ruleset"] != row["ruleset"]
        or {row["attackers_id"], row["defenders_id"]} != set(accounts)
        or not row["starts_at"] <= row["paired_at"] < row["ends_at"]
    ):
        raise ValueError("League provenance does not match the official online game")
    if row["rating_sequence"] is not None and (
        row["pool"] != row["ruleset"]
        or row["rating_attackers"] != row["attackers_id"]
        or row["rating_defenders"] != row["defenders_id"]
    ):
        raise ValueError("League provenance does not match the authoritative rated seats")

    revision = row["official_revision"]
    state = "pending" if revision is None else "withdrawn"
    contributions: list[tuple[str, policy.Contribution]] = []
    if revision is not None and row["rating_state"] == "applied":
        if row["effective_revision"] != revision:
            raise ValueError("An applied league rating event must match the official result")
        state = "late"
        if row["starts_at"] <= row["completed_at"] < row["grace_ends_at"]:
            state = "eligible"
            for side in ("attackers", "defenders"):
                outcome: policy.Outcome = "draw"
                if row["winner"] != "draw":
                    outcome = "win" if row["winner"] == side else "loss"
                contributions.append(
                    (
                        row[side + "_id"],
                        policy.Contribution(game_id, row["rating_sequence"], outcome),
                    )
                )
    return EffectiveLeagueResult(
        game_id,
        row["season_id"],
        row["ruleset"],
        accounts,
        revision,
        row["rating_sequence"],
        state,
        tuple(contributions),
        (row["result_revision"], row["sequence"], row["state"]),
    )


def write_game_result(c: sqlite3.Connection, result: EffectiveLeagueResult) -> None:
    c.execute(
        "UPDATE league_games SET result_revision=?,sequence=?,state=? WHERE game_id=?",
        (result.revision, result.sequence, result.state, result.game_id),
    )


def record_contributions(c: sqlite3.Connection, result: EffectiveLeagueResult) -> None:
    c.executemany(
        "INSERT INTO league_contributions VALUES(?,?,?,?,?,?)",
        (
            (
                result.game_id,
                account,
                result.season_id,
                result.ruleset,
                contribution.sequence,
                contribution.outcome,
            )
            for account, contribution in result.contributions
        ),
    )


def standing_value(row: sqlite3.Row | None) -> policy.Standing:
    if row is None:
        return policy.Standing()
    return policy.Standing(row["points"], row["wins"], row["draws"], row["losses"])


def save_standing(
    c: sqlite3.Connection,
    season_id: str,
    ruleset: str,
    account_id: str,
    standing: policy.Standing,
    last_sequence: int | None,
    now: datetime,
    previous: sqlite3.Row | None = None,
) -> None:
    key = (season_id, ruleset, account_id)
    if not standing.played:
        c.execute(
            "DELETE FROM league_standings WHERE season_id=? AND ruleset=? AND account_id=?", key
        )
        return
    previous_tier, tier_changed_at = None, None
    if previous is not None:
        previous_tier, tier_changed_at = previous["previous_tier"], previous["tier_changed_at"]
        old_tier = standing_value(previous).tier
        if old_tier != standing.tier:
            previous_tier, tier_changed_at = old_tier, micros(now)
    c.execute(
        """INSERT INTO league_standings VALUES(?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(season_id,ruleset,account_id) DO UPDATE SET
        points=excluded.points,wins=excluded.wins,draws=excluded.draws,losses=excluded.losses,
        updated_at=excluded.updated_at,last_sequence=excluded.last_sequence,
        previous_tier=excluded.previous_tier,tier_changed_at=excluded.tier_changed_at""",
        (
            *key,
            standing.points,
            standing.wins,
            standing.draws,
            standing.losses,
            micros(now),
            last_sequence,
            previous_tier,
            tier_changed_at,
        ),
    )


def rebuild_player(
    c: sqlite3.Connection,
    season_id: str,
    ruleset: str,
    account_id: str,
    now: datetime,
    previous: sqlite3.Row | None = None,
) -> policy.Progress:
    progress = policy.Progress()
    last_sequence = None
    for sequence, outcome in c.execute(
        """SELECT sequence,outcome FROM league_contributions
        WHERE season_id=? AND ruleset=? AND account_id=? ORDER BY sequence""",
        (season_id, ruleset, account_id),
    ):
        progress = progress.apply(outcome)
        last_sequence = sequence
    save_standing(
        c,
        season_id,
        ruleset,
        account_id,
        progress.standing,
        last_sequence,
        now,
        previous,
    )
    return progress


def settle_result(c: sqlite3.Connection, game_id: str, now: datetime) -> bool:
    result = effective_league_result(c, game_id)
    if result.revision is None:
        return False
    old_contributions = {
        row["account_id"]: policy.Contribution(game_id, row["sequence"], row["outcome"])
        for row in c.execute(
            "SELECT account_id,sequence,outcome FROM league_contributions WHERE game_id=?",
            (game_id,),
        )
    }
    new_contributions = dict(result.contributions)
    if (
        result.projection == (result.revision, result.sequence, result.state)
        and old_contributions == new_contributions
    ):
        return False

    # An append is safe only for a new result after this player's ledger tail,
    # with a projection known to include that tail. The indexed tail probe does
    # not read earlier contributions. Every replacement or gap takes replay.
    previous = {}
    append = set()
    for account in result.accounts:
        key = (result.season_id, result.ruleset, account)
        current = c.execute(
            "SELECT * FROM league_standings WHERE season_id=? AND ruleset=? AND account_id=?", key
        ).fetchone()
        previous[account] = current
        contribution = new_contributions.get(account)
        if result.projection[0] is None and not old_contributions and contribution:
            tail = c.execute(
                """SELECT sequence FROM league_contributions
                WHERE season_id=? AND ruleset=? AND account_id=? ORDER BY sequence DESC LIMIT 1""",
                key,
            ).fetchone()
            last_sequence = tail[0] if tail else None
            if last_sequence == (current["last_sequence"] if current else None) and (
                last_sequence is None or last_sequence < contribution.sequence
            ):
                append.add(account)

    if old_contributions:
        c.execute("DELETE FROM league_contributions WHERE game_id=?", (game_id,))
    record_contributions(c, result)
    for account in result.accounts:
        current = previous[account]
        peak_tier = None
        if account in append:
            contribution = new_contributions[account]
            standing = standing_value(current).apply(contribution.outcome)
            save_standing(
                c,
                result.season_id,
                result.ruleset,
                account,
                standing,
                contribution.sequence,
                now,
                current,
            )
            peak_tier = standing.tier if standing.qualified else None
        else:
            progress = rebuild_player(c, result.season_id, result.ruleset, account, now, current)
            standing = progress.standing
            peak_tier = progress.highest_tier
        contribution = new_contributions.get(account)
        record_league_tier(
            c,
            account,
            result.season_id,
            result.ruleset,
            standing,
            micros(now),
            game_id=game_id,
            peak_tier=peak_tier,
            sequence=contribution.sequence if contribution is not None else None,
        )
    write_game_result(c, result)
    c.execute("UPDATE league_seasons SET revision=revision+1 WHERE id=?", (result.season_id,))
    c.execute("DELETE FROM league_active_players WHERE game_id=?", (game_id,))
    return True


def effective_account_contributions(
    c: sqlite3.Connection, season_id: str, ruleset: str, account_id: str
) -> list[policy.Contribution]:
    """Resolve this account's eligible contributions from effective official results.

    `league_games` is the durable provenance index and `effective_league_result`
    validates each game against the applied rating ledger. The result never trusts
    cached `league_contributions`, so interrupted rebuilds or pending corrections
    cannot fabricate evidence. Used only by bounded reconciliation, never by an
    ordinary append.
    """
    rows = c.execute(
        """SELECT game_id FROM league_games
           WHERE season_id=? AND ruleset=? AND first_account=?
           UNION
           SELECT game_id FROM league_games
           WHERE season_id=? AND ruleset=? AND second_account=?""",
        (season_id, ruleset, account_id, season_id, ruleset, account_id),
    ).fetchall()
    contributions = []
    for row in rows:
        effective = effective_league_result(c, row["game_id"])
        contribution = dict(effective.contributions).get(account_id)
        if contribution is not None:
            contributions.append(contribution)
    return contributions


def confirmed_account_progress(
    c: sqlite3.Connection, season_id: str, ruleset: str, account_id: str
) -> policy.Progress:
    return policy.fold_progress(effective_account_contributions(c, season_id, ruleset, account_id))
