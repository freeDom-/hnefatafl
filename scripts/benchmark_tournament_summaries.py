#!/usr/bin/env python3
"""Measure tournament collection and polling cost before and after lazy loading.

Builds synthetic saved collections in temporary databases: many completed
tournaments plus one running tournament, each with hundreds of games and long
move histories. Nothing is written to the repository or to user data. The
"before" numbers reproduce the previous paths: decode every full record and strip
the results in Python, read the complete record again for a running tournament
while polling, and re-download the selected match history on every poll.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path


TOURNAMENTS = 24
GAMES = 200
PLIES = 60
POLLS = 600
REPEATS = 5


def synthetic(identifier: str, index: int) -> dict:
    games = [
        {
            "ruleset": "brandubh",
            "winner": "attackers" if game % 2 else "defenders",
            "outcome": "completed",
            "reason": "king_captured",
            "plies": PLIES,
            "moves": [{"source": ply % 37, "target": (ply + 1) % 37} for ply in range(PLIES)],
            "attacker_player": 0,
            "defender_player": 1,
            "average_decision_time": 0.0123,
            "total_decision_time": 0.0123 * PLIES,
            "state": {"board": ["." * 7] * 7, "turn": "attackers", "ply": PLIES},
        }
        for game in range(GAMES)
    ]
    return {
        "id": identifier,
        "name": f"Synthetic series {index}",
        "status": "completed",
        "created_at": f"2026-10-{(index % 28) + 1:02d}T15:00:00Z",
        "ended_at": "2026-10-31T15:05:00Z",
        "config": {
            "mode": "fixed_roles" if index % 3 == 0 else "round_robin",
            "players": [
                {
                    "name": f"Raven {index}",
                    "config": {
                        "algorithm": "neural",
                        "model_id": f"model-{index}",
                        "search_wrapper": "mcts",
                        "iterations": 200,
                        "time_limit": 1,
                    },
                },
                {
                    "name": f"Wolf {index}",
                    "config": {
                        "algorithm": "neural",
                        "model_id": f"model-{index + 100}",
                        "search_wrapper": "mcts",
                        "iterations": 200,
                        "time_limit": 1,
                    },
                },
            ],
            "rulesets": ["brandubh"],
            "games": GAMES,
            "max_plies": PLIES,
            "seed": index,
        },
        "progress": {"completed_games": GAMES, "total_games": GAMES},
        "result": {
            "players": [],
            "scores": [100.0, 100.0],
            "standings": [],
            "by_ruleset": {
                "brandubh": {"wins": [100, 100], "draws": 0, "truncated": 0, "games": GAMES}
            },
            "games": games,
            "truncated": 0,
            "average_game_length": PLIES,
            "average_decision_time": 0.0123,
            "seed": index,
        },
        "error": None,
    }


class Meter:
    """Count storage reads and Python-side JSON decoding while measuring."""

    def __init__(self, store):
        self.store = store
        self.reads = 0
        self.decoded_bytes = 0
        self.original_tournaments = store.tournaments
        self.original_get = store.get_tournament

    def __enter__(self):
        import hnefatafl.storage as storage

        store = self.store
        self.storage = storage
        self.original_loads = storage.json.loads

        def tournaments(*args, **kwargs):
            self.reads += 1
            return self.original_tournaments(*args, **kwargs)

        def get(identifier):
            self.reads += 1
            return self.original_get(identifier)

        def loads(payload, *args, **kwargs):
            if isinstance(payload, str):
                self.decoded_bytes += len(payload)
            return self.original_loads(payload, *args, **kwargs)

        store.tournaments = tournaments
        store.get_tournament = get
        storage.json.loads = loads
        return self

    def __exit__(self, *_):
        self.store.tournaments = self.original_tournaments
        self.store.get_tournament = self.original_get
        self.storage.json.loads = self.original_loads


class LiveWorker:
    """A worker handle owned by this process; the benchmark never starts one."""

    def poll(self):
        return None

    def wait(self, timeout=None):
        return 0


def timed(action, repeats=REPEATS):
    samples = []
    value = None
    for _ in range(repeats):
        started = time.perf_counter()
        value = action()
        samples.append(time.perf_counter() - started)
    return statistics.median(samples), len(json.dumps(value, allow_nan=False)), value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Write the measurements as JSON")
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

    from hnefatafl.storage import Store
    from hnefatafl.tournaments import TournamentManager

    report: dict = {"tournaments": TOURNAMENTS, "games_per_tournament": GAMES, "plies": PLIES}
    with tempfile.TemporaryDirectory(prefix="tafl-tournament-bench-") as temporary:
        root = Path(temporary)
        store = Store(root / "hnefatafl.sqlite3")
        for index in range(TOURNAMENTS):
            store.save_tournament(synthetic(f"{index:032x}", index))
        report["database_bytes"] = (root / "hnefatafl.sqlite3").stat().st_size
        manager = TournamentManager(store, root)

        with Meter(store) as meter:
            old_time, old_bytes, old_records = timed(
                lambda: [
                    manager.public(manager.read(record["id"]), summary=True)
                    for record in store.tournaments()
                ]
            )
            report["before"] = {
                "list_seconds": old_time,
                "list_bytes": old_bytes,
                "storage_reads": meter.reads,
                "decoded_bytes": meter.decoded_bytes,
            }
        with Meter(store) as meter:
            new_time, new_bytes, new_records = timed(
                lambda: [
                    manager.public(record, summary=True) for record in manager.list(summary=True)
                ]
            )
            report["after"] = {
                "list_seconds": new_time,
                "list_bytes": new_bytes,
                "storage_reads": meter.reads,
                "decoded_bytes": meter.decoded_bytes,
            }
        assert old_bytes == new_bytes, "the collection response body must not change"
        assert old_records == new_records

        # Polling: one selected tournament is displayed for an hour of 1.8s polls.
        identifier = f"{0:032x}"
        detail_bytes = len(json.dumps(store.get_tournament(identifier), allow_nan=False))
        report["selected_detail_bytes"] = detail_bytes
        report["polling"] = {
            "polls": POLLS,
            "before_bytes": POLLS * (old_bytes + detail_bytes),
            "after_bytes": POLLS * new_bytes + detail_bytes,
        }

        # A running tournament with a large retained history: polling summaries
        # must be as cheap as polling a completed collection. The worker handle
        # is a deterministic stand-in, so the record stays running.
        running_store = Store(root / "running.sqlite3")
        running_record = synthetic("f" * 32, 99)
        running_record["status"] = "running"
        running_record["ended_at"] = None
        running_store.save_tournament(running_record)
        running_manager = TournamentManager(running_store, root)
        running_manager.processes["f" * 32] = LiveWorker()
        with Meter(running_store) as meter:
            running_before_time, running_before_bytes, running_before = timed(
                lambda: [
                    running_manager.public(running_manager.read(record["id"]), summary=True)
                    for record in running_store.tournaments()
                ]
            )
            running_before_reads = meter.reads
            running_before_decoded = meter.decoded_bytes
        with Meter(running_store) as meter:
            running_after_time, running_after_bytes, running_after = timed(
                lambda: [
                    running_manager.public(record, summary=True)
                    for record in running_manager.list(summary=True)
                ]
            )
            running_after_reads = meter.reads
            running_after_decoded = meter.decoded_bytes
        assert running_before_bytes == running_after_bytes
        assert running_after[0]["status"] == "running"
        report["running_polling"] = {
            "full_record_bytes": len(json.dumps(running_record, allow_nan=False)),
            "summary_bytes": running_after_bytes,
            "before": {
                "seconds": running_before_time,
                "storage_reads": running_before_reads,
                "decoded_bytes": running_before_decoded,
            },
            "after": {
                "seconds": running_after_time,
                "storage_reads": running_after_reads,
                "decoded_bytes": running_after_decoded,
            },
            "polling": {
                "polls": POLLS,
                "before_bytes": POLLS * running_before_decoded // REPEATS,
                "after_bytes": POLLS * running_after_decoded // REPEATS,
            },
        }

        # End-to-end: the HTTP collection response the browser actually receives.
        sys.path.insert(1, str(Path(__file__).resolve().parents[1] / "tests"))
        from auth_support import AuthenticatedClient
        from hnefatafl.server import create_app

        with AuthenticatedClient(create_app(temporary)) as client:
            http_time, http_bytes, http_body = timed(
                lambda: client.get("/api/tournaments?summary=true").json(), repeats=3
            )
            report["http"] = {"seconds": http_time, "bytes": http_bytes}
            assert len(http_body) == TOURNAMENTS
            assert all("result" not in record for record in http_body)

    report["improvement"] = {
        "list_seconds": round(
            report["before"]["list_seconds"] / report["after"]["list_seconds"], 1
        ),
        "storage_reads": report["before"]["storage_reads"] / report["after"]["storage_reads"],
        "polling_bytes": round(
            report["polling"]["before_bytes"] / report["polling"]["after_bytes"], 1
        ),
        "running_decoded_bytes": round(
            report["running_polling"]["before"]["decoded_bytes"]
            / report["running_polling"]["after"]["decoded_bytes"],
            1,
        ),
        "running_polling_bytes": round(
            report["running_polling"]["polling"]["before_bytes"]
            / report["running_polling"]["polling"]["after_bytes"],
            1,
        ),
    }
    print(json.dumps(report, indent=2))
    if args.output:
        args.output.write_text(json.dumps(report, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
