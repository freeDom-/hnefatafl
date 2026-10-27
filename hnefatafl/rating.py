"""Pure, versioned Elo policy and deterministic replay; no IO or database.

Both participants' changes are computed from their pre-game ratings. Ratings keep
full float precision; rounding is a presentation concern only. A pool is one
ruleset, and there is no separate attacker/defender or time-control pool. The
policy is versioned so a future change can be identified and rebuilt explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass

INITIAL_RATING = 1500.0
K_FACTOR = 32.0
PROVISIONAL_GAMES = 10
RATING_POLICY_VERSION = 1
DRAW = "draw"
SIDES = ("attackers", "defenders")


@dataclass(frozen=True)
class RatedEvent:
    """One eligible rated game, ordered by its immutable persisted sequence."""

    sequence: int
    game_id: str
    attackers_id: str
    defenders_id: str
    winner: str


@dataclass(frozen=True)
class RatingApplication:
    game_id: str
    account_id: str
    opponent_id: str
    rating_before: float
    rating_after: float
    opponent_rating: float
    expected: float
    score: float
    rated_games_after: int

    @property
    def delta(self) -> float:
        return self.rating_after - self.rating_before

    @property
    def provisional(self) -> bool:
        return self.rated_games_after < PROVISIONAL_GAMES


def expected_score(rating: float, opponent: float) -> float:
    return 1.0 / (1.0 + 10.0 ** ((opponent - rating) / 400.0))


def score_for(winner: str, side: str) -> float:
    if winner == DRAW:
        return 0.5
    return 1.0 if winner == side else 0.0


def updated_rating(rating: float, opponent: float, score: float) -> float:
    return rating + K_FACTOR * (score - expected_score(rating, opponent))


def replay(events):
    """Deterministically replay ordered events for one pool.

    Returns ``(states, applications)``. ``states`` maps an account id to
    ``(rating, rated_games, wins, losses, draws)``; ``applications`` maps a game
    id to ``{account_id: RatingApplication}``. The caller is responsible for
    filtering events by pool and by applied state.
    """
    states: dict[str, tuple[float, int, int, int, int]] = {}
    applications: dict[str, dict[str, RatingApplication]] = {}
    for event in sorted(events, key=lambda item: item.sequence):
        before: dict[str, tuple[str, float, int, int, int, int]] = {}
        for side, account in (("attackers", event.attackers_id), ("defenders", event.defenders_id)):
            rating, games, wins, losses, draws = states.get(account, (INITIAL_RATING, 0, 0, 0, 0))
            before[side] = (account, rating, games, wins, losses, draws)
        game_apps: dict[str, RatingApplication] = {}
        for side, account in (("attackers", event.attackers_id), ("defenders", event.defenders_id)):
            other = "defenders" if side == "attackers" else "attackers"
            opponent, opponent_rating, *_ = before[other]
            _, rating, games, wins, losses, draws = before[side]
            score = score_for(event.winner, side)
            after = updated_rating(rating, opponent_rating, score)
            states[account] = (
                after,
                games + 1,
                wins + int(score == 1.0),
                losses + int(score == 0.0),
                draws + int(score == 0.5),
            )
            game_apps[account] = RatingApplication(
                game_id=event.game_id,
                account_id=account,
                opponent_id=opponent,
                rating_before=rating,
                rating_after=after,
                opponent_rating=opponent_rating,
                expected=expected_score(rating, opponent_rating),
                score=score,
                rated_games_after=games + 1,
            )
        applications[event.game_id] = game_apps
    return states, applications
