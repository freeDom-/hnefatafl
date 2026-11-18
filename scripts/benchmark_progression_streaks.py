"""Measure the bounded tail probe against the full win-streak ledger fold.

Run from the repository root with the project interpreter:

    .venv/bin/python scripts/benchmark_progression_streaks.py --games 2000 --samples 25

The synthetic ledger stores one result per sequence for two accounts and keeps the
target's newest run short, which is the common ordinary-completion shape. The full
fold reads every applied event for the account; the tail probe reads at most the
next unawarded milestone. Both read the same joined ledger, so the comparison is
not a different query shape, only a different amount of history.

The script never touches a real database: it builds a throwaway store under the
system temporary directory and removes it afterwards.
"""

from __future__ import annotations

import argparse
import json
import statistics
import tempfile
import time
from pathlib import Path

from hnefatafl.auth import Auth, SecuritySettings
from hnefatafl.personal import rated_streaks
from hnefatafl.progression import _tail_win_streak
from hnefatafl.storage import Store

ROOT = Path(__file__).resolve().parents[1]


def seed(store: Store, games: int) -> tuple[str, str]:
    auth = Auth(store, SecuritySettings())
    target = auth.create_account("Target", "target@example.test", "benchmark-password", "USER")
    opponent = auth.create_account(
        "Opponent", "opponent@example.test", "benchmark-password", "USER"
    )
    with store.connection() as c:
        for sequence in range(1, games + 1):
            # The target wins every game except the one immediately before the
            # final result, so the newest run is exactly one and the next
            # unawarded milestone is three.
            target_wins = sequence != games - 1
            winner = "attackers" if target_wins else "defenders"
            c.execute(
                """INSERT INTO rating_events(
                     game_id,pool,sequence,state,effective_revision,winner,
                     attackers_id,defenders_id,completed_at,policy_version,created_at)
                   VALUES(?,?,?,'applied',1,?,?,?,?,?,?)""",
                (
                    f"synthetic-{sequence}",
                    "brandubh",
                    sequence,
                    winner,
                    target["id"],
                    opponent["id"],
                    sequence,
                    1,
                    sequence,
                ),
            )
            for account_id, score in (
                (target["id"], 1.0 if target_wins else 0.0),
                (opponent["id"], 0.0 if target_wins else 1.0),
            ):
                c.execute(
                    """INSERT INTO rating_applications(
                         game_id,account_id,pool,sequence,opponent_id,rating_before,rating_after,
                         delta,opponent_rating,expected,score,rated_games_after,policy_version)
                       VALUES(?,?,?,?,?,1500.0,1500.0,0.0,1500.0,0.5,?,?,1)""",
                    (
                        f"synthetic-{sequence}",
                        account_id,
                        "brandubh",
                        sequence,
                        opponent["id"] if account_id == target["id"] else target["id"],
                        score,
                        sequence,
                    ),
                )
    return target["id"], opponent["id"]


def measure(target: str, store: Store, samples: int) -> dict:
    full: list[float] = []
    tail: list[float] = []
    with store.connection() as c:
        # Warm the page cache before timing.
        rated_streaks(c, target)
        _tail_win_streak(c, target, 3)
        for _ in range(samples):
            started = time.perf_counter()
            rated_streaks(c, target)
            full.append(time.perf_counter() - started)
            started = time.perf_counter()
            _tail_win_streak(c, target, 3)
            tail.append(time.perf_counter() - started)
    return {
        "full_seconds": full,
        "tail_seconds": tail,
        "full_median_seconds": statistics.median(full),
        "tail_median_seconds": statistics.median(tail),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--games", type=int, default=2000)
    parser.add_argument("--samples", type=int, default=25)
    parser.add_argument(
        "--json", type=Path, default=ROOT / "docs/benchmarks/progression-streaks.json"
    )
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="hnefatafl-streak-bench-") as directory:
        store = Store(Path(directory) / "benchmark.sqlite3")
        target, _ = seed(store, args.games)
        result = measure(target, store, args.samples)
    result.update(
        {
            "games": args.games,
            "samples": args.samples,
            "tail_cap": 3,
            "full_rows": args.games,
            "tail_rows": 3,
            "speedup": result["full_median_seconds"] / result["tail_median_seconds"],
        }
    )
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
