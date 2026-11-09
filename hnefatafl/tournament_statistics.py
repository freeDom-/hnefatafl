"""Incremental tournament performance and additive historical projections."""

from __future__ import annotations


def performance() -> dict:
    return dict(
        games=0,
        wins=0,
        losses=0,
        draws=0,
        truncated=0,
        completed=0,
        win_rate=None,
        score_rate=None,
    )


def standing(player: int) -> dict:
    return {
        "player": player,
        **performance(),
        "score": 0.0,
        "roles": {side: performance() for side in ("attackers", "defenders")},
    }


def rates(stats: dict) -> dict:
    completed = stats["wins"] + stats["losses"] + stats["draws"]
    return {
        "completed": completed,
        "win_rate": stats["wins"] / completed if completed else None,
        "score_rate": (stats["wins"] + 0.5 * stats["draws"]) / completed if completed else None,
    }


def record_performance(stats: dict, winner: str | None, side: str) -> None:
    stats["games"] += 1
    outcome = (
        "truncated"
        if winner is None
        else "draws"
        if winner == "draw"
        else "wins"
        if winner == side
        else "losses"
    )
    stats[outcome] += 1
    stats.update(rates(stats))


def record_standing(stats: dict, winner: str | None, side: str) -> None:
    record_performance(stats, winner, side)
    record_performance(stats["roles"][side], winner, side)
    stats["score"] = stats["wins"] + 0.5 * stats["draws"]


def historical_statistics(result: dict, player_count: int) -> dict:
    """Backfill once per full-detail read, never in collection/progress hot paths.

    New reports already carry the counters and return immediately. Missing or
    incomplete historical match histories leave side performance unavailable;
    their recorded overall counters and scoring remain authoritative.
    """
    rows = result.get("standings", [])
    if len(rows) == player_count and all("roles" in row for row in rows):
        return result
    rebuilt = [standing(index) for index in range(player_count)]
    battlefields: dict[str, list[dict]] = {}
    for game in result.get("games", []):
        attacker = game.get("attacker_player")
        defender = game.get("defender_player", 1 - attacker if attacker in (0, 1) else None)
        if (
            not isinstance(attacker, int)
            or not isinstance(defender, int)
            or not 0 <= attacker < player_count
            or not 0 <= defender < player_count
            or attacker == defender
            or (player_count > 2 and "defender_player" not in game)
        ):
            continue
        field = battlefields.setdefault(game["ruleset"], [standing(i) for i in range(player_count)])
        for player, side in ((attacker, "attackers"), (defender, "defenders")):
            record_standing(rebuilt[player], game["winner"], side)
            record_standing(field[player], game["winner"], side)
    complete_history = all(rebuilt[row["player"]]["games"] == row["games"] for row in rows)
    enriched = [
        {
            **row,
            **rates(row),
            **({"roles": rebuilt[row["player"]]["roles"]} if complete_history else {}),
        }
        for row in rows
    ]
    return {
        **result,
        "standings": enriched,
        "by_ruleset": {
            key: {
                **value,
                **(
                    {"standings": battlefields.get(key, [standing(i) for i in range(player_count)])}
                    if complete_history
                    else {}
                ),
            }
            for key, value in result.get("by_ruleset", {}).items()
        },
    }
