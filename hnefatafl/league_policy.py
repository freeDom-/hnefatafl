"""Version-one seasonal progress policy; official winners remain online-owned.

Pure calendar/scoring functions shared by league projections and ledger replay.
No HTTP, SQLite, engine mutation, Elo changes or client-derived rewards.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

POLICY_VERSION = 1
QUEUE_SECONDS = 600
CLOCK_SECONDS = 600
INCREMENT_SECONDS = 5
PAIR_DAILY_LIMIT = 3
QUALIFYING_RESULTS = 3
TIERS = (
    ("midgard", "Midgard", 0),
    ("nidavellir", "Nidavellir", 6),
    ("alfheim", "Alfheim", 15),
    ("asgard", "Asgard", 30),
    ("valhalla", "Valhalla", 50),
)
Outcome = Literal["win", "draw", "loss"]


def utc(moment: datetime) -> datetime:
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("League instants must have an explicit timezone")
    return moment.astimezone(timezone.utc)


@dataclass(frozen=True)
class Season:
    identifier: str
    start: datetime
    end: datetime

    @property
    def grace_end(self) -> datetime:
        return self.end + timedelta(days=1)

    def contains_pairing(self, moment: datetime) -> bool:
        return self.start <= utc(moment) < self.end

    def accepts_completion(self, moment: datetime) -> bool:
        return self.start <= utc(moment) < self.grace_end


def season_for(moment: datetime) -> Season:
    now = utc(moment)
    start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
    end = datetime(now.year + (now.month == 12), now.month % 12 + 1, 1, tzinfo=timezone.utc)
    return Season(start.strftime("%Y-%m"), start, end)


def rating_window(wait_seconds: float) -> int:
    """Each player's wait bounds the mutually accepted pairing window."""
    return min(600, 200 + 100 * (max(0, int(wait_seconds)) // 60))


def compatible_ratings(first: float, second: float, first_wait: float, second_wait: float) -> bool:
    return abs(first - second) <= min(rating_window(first_wait), rating_window(second_wait))


@dataclass(frozen=True)
class Contribution:
    game_id: str
    sequence: int
    outcome: Outcome


@dataclass(frozen=True)
class Standing:
    points: int = 0
    wins: int = 0
    draws: int = 0
    losses: int = 0

    @property
    def played(self) -> int:
        return self.wins + self.draws + self.losses

    @property
    def qualified(self) -> bool:
        return self.played >= QUALIFYING_RESULTS

    @property
    def tier(self) -> str:
        return next(
            identifier for identifier, _, minimum in reversed(TIERS) if self.points >= minimum
        )


def fold_contributions(contributions: list[Contribution]) -> Standing:
    """Rebuild after revisions/voids; subtraction cannot undo a zero floor.

    Callers supply the effective, eligible contribution at most once per game.
    Stable authoritative result sequence determines order, never browser order.
    """
    game_ids = set()
    sequences = set()
    points = wins = draws = losses = 0
    for item in sorted(contributions, key=lambda row: row.sequence):
        if item.game_id in game_ids or item.sequence in sequences:
            raise ValueError("Duplicate game or authoritative result sequence")
        if not item.game_id or type(item.sequence) is not int or item.sequence < 1:
            raise ValueError("A contribution requires a game and positive ledger sequence")
        game_ids.add(item.game_id)
        sequences.add(item.sequence)
        if item.outcome == "win":
            wins += 1
            points += 3
        elif item.outcome == "draw":
            draws += 1
            points += 1
        elif item.outcome == "loss":
            losses += 1
            points = max(0, points - 1)
        else:
            raise ValueError("Unknown league outcome")
    return Standing(points, wins, draws, losses)
