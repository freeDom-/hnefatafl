"""Exercise authenticated current and archived progression against growing populations."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json
import os
from pathlib import Path
import tempfile
import time

from fastapi.testclient import TestClient

from benchmark_league_arena import (
    Fixture,
    Probe,
    RULESET,
    TIME,
    distribution,
    require,
    source_evidence,
)
from hnefatafl.auth import COOKIE
from hnefatafl.leagues import Leagues
from hnefatafl.online_core import micros


def population(root, size):
    from hnefatafl.server import create_app

    fixture = Fixture(root, str(size), filename="hnefatafl.sqlite3")
    core = Leagues(fixture.online)
    actors = fixture.actors("Rank", 4)
    ranks = {"high": 0, "middle": size // 2, "low": size - 1, "tie": size // 4}
    targets = dict(zip(ranks.values(), actors, strict=True))
    tie_start, tie_end = size // 8, 3 * size // 8
    at = micros(TIME)
    with fixture.online._write() as c:
        current = core._season(c, TIME).identifier
        historical = core._season(c, TIME - timedelta(days=30)).identifier
        accounts = [targets[i].id if i in targets else f"Ranking{i:06d}" for i in range(size)]
        c.executemany(
            """INSERT OR IGNORE INTO accounts
            (id,username,email,password_hash,role,created_at,email_verified_at)
            VALUES(?,?,?,'!disposable-rank-fixture','USER',1,1)""",
            ((name, name, f"{name}@example.test") for name in accounts),
        )
        for season in (current, historical):
            rows = []
            for i, name in enumerate(accounts):
                sequence = tie_start if tie_start <= i < tie_end else i
                wins = size - sequence + 3
                rows.append((season, RULESET, name, wins * 3, wins, at))
            c.executemany(
                """INSERT INTO league_standings
                    (season_id,ruleset,account_id,points,wins,draws,losses,updated_at)
                    VALUES(?,?,?,?,?,0,0,?)""",
                rows,
            )
    app = create_app(
        fixture.store.path.parent,
        online_settings=fixture.online.settings,
        online_clock=fixture.clock,
        online_background=False,
        account_background=False,
    )
    app.state.leagues.store = fixture.store
    endpoints = {
        "current": (f"/api/leagues/current?ruleset={RULESET}", current),
        "current_progress": (f"/api/leagues/progress?ruleset={RULESET}", current),
        "archived_progress": (
            f"/api/leagues/progress?ruleset={RULESET}&season={historical}",
            historical,
        ),
    }
    return (
        fixture,
        app,
        endpoints,
        {
            label: (index, tie_start + 1 if label == "tie" else position + 1)
            for index, (label, position) in enumerate(ranks.items())
        },
    )


def sample(client, path, season, index, expected_rank):
    started = time.perf_counter()
    headers = {"Cookie": f"{COOKIE}=league-benchmark-Rank-{index}"}
    response = client.get(path, headers=headers)
    require(response.is_success, "Authenticated rank endpoint rejected a fixture account")
    payload = response.json()
    require(payload["season"]["id"] == season, "Read returned a different season")
    require(payload["progress"]["rank"] == expected_rank, "Sporting rank diverged")
    return (time.perf_counter() - started) * 1000


def run_population(root, size, samples, readers):
    fixture, app, endpoints, ranks = population(root, size)
    results = {}
    with TestClient(app, base_url="http://localhost") as client:
        for label, (path, season) in endpoints.items():
            results[label] = {}
            for rank, (index, expected) in ranks.items():
                probe = Probe(capture_selects=True, vm_quantum=100)
                fixture.store.background = probe
                try:
                    sample(client, path, season, index, expected)
                finally:
                    fixture.store.background = None
                rank_sql = [
                    sql for sql in probe.selects if sql.startswith("SELECT 1+(SELECT COUNT")
                ]
                require(len(rank_sql) == 1, "Rank count was absent from the authenticated read")
                with fixture.store.connection() as c:
                    plan = [
                        row[3]
                        for row in c.execute("EXPLAIN QUERY PLAN " + rank_sql[0])
                        if "league_standings" in row[3]
                    ]
                latency = {}
                for workers in (1, readers):
                    started = time.perf_counter()
                    if workers == 1:
                        durations = [
                            sample(client, path, season, index, expected) for _ in range(samples)
                        ]
                    else:
                        with ThreadPoolExecutor(max_workers=workers) as pool:
                            durations = list(
                                pool.map(
                                    lambda _: sample(client, path, season, index, expected),
                                    range(samples),
                                )
                            )
                    latency[str(workers)] = {
                        "samples": samples,
                        "latency_ms": distribution(durations),
                        "reads_per_second": round(samples / (time.perf_counter() - started), 2),
                    }
                results[label][rank] = {
                    "rank": expected,
                    "league_snapshot_sql_statements": probe.statements,
                    "league_snapshot_vm_steps_floor": probe.vm_steps,
                    "rank_range_plan": plan,
                    "reads": latency,
                }
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--populations", type=int, nargs="+", default=[1024, 16384, 131072])
    parser.add_argument("--samples", type=int, default=60)
    parser.add_argument("--readers", type=int, default=6)
    parser.add_argument("--source-revision")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    args.baseline = False
    if (
        not 1 <= len(set(args.populations)) == len(args.populations) <= 5
        or any(not 128 <= size <= 200_000 for size in args.populations)
        or not 10 <= args.samples <= 200
        or not 2 <= args.readers <= 16
    ):
        parser.error("Use 1–5 distinct populations of 128–200,000, 10–200 samples and 2–16 readers")
    with tempfile.TemporaryDirectory(prefix="hnefatafl-league-rank-") as directory:
        # Importing the server also constructs the module-level app.
        os.environ["HNEFATAFL_DATA_DIR"] = str(Path(directory) / "bootstrap")
        records = {
            **source_evidence(args),
            "populations": {
                size: run_population(Path(directory), size, args.samples, args.readers)
                for size in args.populations
            },
        }
    content = json.dumps(records, indent=2) + "\n"
    if args.output is None:
        print(content, end="")
    else:
        args.output.write_text(content)


if __name__ == "__main__":
    main()
