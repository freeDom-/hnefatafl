"""Small reproducible real-HTTP read workload on a disposable SQLite fixture.

Never accepts operator data paths. No AI search/training/model loads. Driver and
single API worker share one local host; results are observations, not capacity.
"""

from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor
from collections import Counter, defaultdict
import json
import hashlib
from datetime import datetime, timezone
import math
import os
from pathlib import Path
import platform
import secrets
import signal
import sqlite3
import statistics
import threading
import subprocess
import sys
import tempfile
import time
from uuid import uuid4


def verified_source(source: Path) -> str:
    """Reject mislabeled or dirty sources before importing or seeding anything."""
    source = source.resolve()
    try:
        revision = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=source, text=True, stderr=subprocess.DEVNULL
        ).strip()
        root = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=source,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        dirty = subprocess.check_output(
            ["git", "status", "--porcelain"],
            cwd=source,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError) as error:
        raise ValueError("Benchmark requires a verifiable Git checkout") from error
    if Path(root).resolve() != source or dirty:
        raise ValueError("Benchmark requires the clean repository root; commit changes first")
    if not (source / "hnefatafl" / "server.py").is_file():
        raise ValueError("Source does not contain the Hnefatafl server")
    return revision


def validate_parameters(users: int, rounds: int, concurrency: str, port: int) -> list[int]:
    try:
        levels = [int(value) for value in concurrency.split(",")]
    except ValueError as error:
        raise ValueError("Concurrency must be a comma-separated integer list") from error
    if (
        not 2 <= users <= 16
        or not 1 <= rounds <= 10
        or not 1024 <= port <= 65535
        or not 1 <= len(levels) <= 5
        or len(set(levels)) != len(levels)
        or any(not 1 <= level <= users for level in levels)
    ):
        raise ValueError(
            "Bounds: users 2–16, rounds 1–10, 1–5 distinct concurrency levels <= users, port 1024–65535"
        )
    return levels


def serve(source: Path, data: Path, port: int) -> None:
    if not data.name.startswith("hnefatafl-load-") or not (data / ".benchmark-only").exists():
        raise SystemExit("Refusing non-benchmark data directory")
    sys.path.insert(0, str(source))
    from hnefatafl.server import create_app
    from hnefatafl.online_settings import OnlineSettings
    import uvicorn

    app = create_app(
        data,
        online_settings=OnlineSettings(enabled=True),
        account_background=False,
        online_background=False,
    )

    async def benchmark_identity():
        return {"fixture": (data / ".benchmark-only").read_text().strip()}

    app.add_api_route("/__benchmark_identity", benchmark_identity, methods=["GET"])
    app.router.routes.insert(0, app.router.routes.pop())
    uvicorn.run(app, host="127.0.0.1", port=port, access_log=False, log_level="warning", workers=1)


def percentile(values, fraction):
    values = sorted(values)
    return values[max(0, math.ceil(len(values) * fraction) - 1)]


def metrics(rows):
    values = [row["milliseconds"] for row in rows]
    return {
        "requests": len(rows),
        "successes": sum(row["status"] == 200 for row in rows),
        "status_counts": dict(Counter(str(row["status"]) for row in rows)),
        "latency_ms": {
            "median": statistics.median(values),
            "p95": percentile(values, 0.95),
            "p99": percentile(values, 0.99),
            "max": max(values),
        },
        "response_bytes": {
            "total": sum(row["bytes"] for row in rows),
            "max": max(row["bytes"] for row in rows),
        },
    }


def require(response, expected=200):
    if response.status_code != expected:
        # Do not print responses, credentials, cookies, CSRF or private data.
        raise RuntimeError(
            f"Fixture request failed: {response.request.method} {response.request.url.path} status={response.status_code}"
        )
    return response.json()


def main(args):
    import httpx

    source = Path(args.source_root).resolve()
    concurrency = validate_parameters(args.users, args.rounds, args.concurrency, args.port)
    revision = verified_source(source)
    sys.path.insert(0, str(source))
    from hnefatafl.server import create_app
    import hnefatafl.server as server_module

    if Path(server_module.__file__).resolve() != source / "hnefatafl" / "server.py":
        raise ValueError("Imported server does not match verified source")
    from hnefatafl.engine import RULESETS
    from hnefatafl.online_settings import OnlineSettings

    out = Path(args.output).resolve()
    # Never overwrite prior evidence or write into an operator data directory.
    out.mkdir(parents=True, exist_ok=False)
    origin = f"http://127.0.0.1:{args.port}"
    clients = []
    process = None
    report = {
        "scope": "warm bounded GETs against fresh disposable SQLite, one API process, no AI searches or training",
        "source_root": str(source),
        "source_revision": revision,
        "source_clean": True,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "logical_cpus": os.cpu_count(),
            "client": "httpx keep-alive clients; one sequential worker per distinct account",
            "server": "uvicorn one worker, background account/online sweep loops disabled",
            "port": args.port,
        },
        "parameters": {"users": args.users, "rounds": args.rounds, "concurrency": concurrency},
    }
    with tempfile.TemporaryDirectory(prefix="hnefatafl-load-", dir="/tmp") as directory:
        data = Path(directory)
        fixture_identity = uuid4().hex
        (data / ".benchmark-only").write_text(fixture_identity)
        # Bootstrap distinct accounts out of band, then use real HTTP login and
        # game/tournament mutation adapters for every seeded record.
        setup = create_app(
            data,
            online_settings=OnlineSettings(enabled=True),
            account_background=False,
            online_background=False,
        )
        credentials = []
        for i in range(args.users + 1):
            username = f"LoadPlayer{i:02}" if i < args.users else "LoadAdmin"
            password = secrets.token_urlsafe(24)
            account = setup.state.auth.create_account(
                username,
                f"{username.lower()}@example.test",
                password,
                "USER" if i < args.users else "ADMIN",
                bootstrap=i == args.users,
            )
            credentials.append((username, password, account["id"]))
        with setup.state.store.connection() as c:
            c.execute("UPDATE accounts SET email_verified_at=1")
        db = data / "hnefatafl.sqlite3"
        env = {
            **os.environ,
            "HNEFATAFL_DATA_DIR": str(data),
            "HNEFATAFL_ENV": "development",
            "HNEFATAFL_ALLOWED_HOSTS": "127.0.0.1,localhost",
            "OMP_NUM_THREADS": "2",
            "MKL_NUM_THREADS": "2",
            "OPENBLAS_NUM_THREADS": "2",
        }
        log = open(out / "server.log", "w")
        process = subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--serve",
                "--source-root",
                str(source),
                "--data-dir",
                str(data),
                "--port",
                str(args.port),
            ],
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            ready = time.monotonic() + 30
            while True:
                try:
                    health = httpx.get(origin + "/api/health", timeout=1, trust_env=False)
                    identity = httpx.get(
                        origin + "/__benchmark_identity", timeout=1, trust_env=False
                    )
                    if (
                        health.status_code == 200
                        and identity.status_code == 200
                        and identity.json().get("fixture") == fixture_identity
                    ):
                        break
                except httpx.HTTPError:
                    pass
                if process.poll() is not None or time.monotonic() > ready:
                    raise RuntimeError("Disposable benchmark server failed to start")
                time.sleep(0.1)
            for name, password, _ in credentials:
                client = httpx.Client(
                    base_url=origin,
                    headers={"Origin": origin},
                    timeout=10,
                    trust_env=False,
                    limits=httpx.Limits(max_connections=16, max_keepalive_connections=16),
                )
                session = require(
                    client.post("/api/auth/login", json={"identity": name, "password": password})
                )
                client.headers["X-CSRF-Token"] = session["csrf_token"]
                clients.append(client)
            actors = clients[:-1]
            admin = clients[-1]
            account_ids = [row[2] for row in credentials[:-1]]
            local_ids = []
            single_ids = []
            active_ids = []
            replay_ids = []
            for i, client in enumerate(actors):
                local_ids.append(
                    [
                        require(
                            client.post(
                                "/api/games/local",
                                json={
                                    "ruleset": "brandubh",
                                    "attackers_label": "Local attacker",
                                    "defenders_label": "Local defender",
                                },
                            ),
                            201,
                        )["id"]
                        for _ in range(3)
                    ]
                )
                single_ids.append([])
                for rule in ["brandubh", "tablut"]:
                    single_ids[-1].append(
                        require(
                            client.post(
                                "/api/games",
                                json={
                                    "ruleset": rule,
                                    "human_side": RULESETS[rule].first_player,
                                    "players": {
                                        "defenders"
                                        if RULESETS[rule].first_player == "attackers"
                                        else "attackers": {"profile_id": "baseline"}
                                    },
                                },
                            ),
                            201,
                        )["id"]
                    )
                for kind in ["active", "completed", "waiting"]:
                    result = require(
                        client.post(
                            "/api/online/games",
                            json={
                                "request_id": str(uuid4()),
                                "ruleset": "brandubh",
                                "side_selection": "attackers",
                                "time_per_move": 259200,
                            },
                        ),
                        201,
                    )
                    game = result["game"]
                    path = "/api/online/games/" + game["id"]
                    opponent = actors[(i + 1) % len(actors)]
                    if kind == "waiting":
                        continue
                    game = require(
                        opponent.post(path + "/join", json={"version": game["version"]})
                    )["game"]
                    if kind == "active":
                        active_ids.append(game["id"])
                        continue
                    # Genuine accepted eight-ply repetition draw, not forged
                    # results or imported histories; no AI work is performed.
                    for turn, (source_square, target) in enumerate(
                        [(3, 2), (17, 16), (2, 3), (16, 17)] * 2
                    ):
                        actor = client if turn % 2 == 0 else opponent
                        game = require(
                            actor.post(
                                path + "/moves",
                                json={
                                    "version": game["version"],
                                    "source": source_square,
                                    "target": target,
                                },
                            )
                        )["game"]
                    if game["status"] != "completed":
                        raise RuntimeError("Expected authoritative completed repetition fixture")
                    replay_ids.append(game["id"])
            tournament = require(
                admin.post(
                    "/api/player-tournaments",
                    json={
                        "name": "Read benchmark bracket",
                        "ruleset": "brandubh",
                        "match_mode": "simultaneous",
                        "capacity": args.users,
                        "time_per_move": 259200,
                    },
                ),
                201,
            )
            tournament_id = tournament["id"]
            for client in actors:
                require(client.post(f"/api/player-tournaments/{tournament_id}/register"))
            current = require(admin.get(f"/api/player-tournaments/{tournament_id}"))
            require(
                admin.post(
                    f"/api/player-tournaments/{tournament_id}/start",
                    json={"version": current["version"]},
                )
            )
            require(
                admin.post(
                    "/api/player-tournaments",
                    json={
                        "name": "Upcoming benchmark bracket",
                        "ruleset": "tablut",
                        "match_mode": "simultaneous",
                        "capacity": args.users,
                        "time_per_move": 259200,
                    },
                ),
                201,
            )
            endpoint_builders = {
                "dashboard_online": lambda i: "/api/personal/online?section=current&limit=25",
                "dashboard_local": lambda i: "/api/personal/local?status=in_progress&limit=5",
                "dashboard_single": lambda i: (
                    "/api/personal/single-player?status=in_progress&limit=25"
                ),
                "profile": lambda i: "/api/profile",
                "personal_statistics": lambda i: "/api/personal/single-player?limit=1",
                "online_statistics": lambda i: "/api/online/stats",
                "leaderboard": lambda i: "/api/leaderboard?ruleset=brandubh&limit=10",
                "lobby": lambda i: "/api/online/games?section=all&limit=25",
                "active_game": lambda i: "/api/online/games/" + active_ids[i],
                "completed_replay": lambda i: "/api/online/games/" + replay_ids[i] + "/replay",
                "player_tournaments": lambda i: "/api/player-tournaments?limit=20",
                "player_bracket": lambda i: "/api/player-tournaments/" + tournament_id + "/bracket",
                "recent_records": lambda i: "/api/history/games?category=online&limit=3",
            }
            # Warm all endpoints and check the fixture is owned/populated.
            for i, client in enumerate(actors):
                local = require(client.get(endpoint_builders["dashboard_local"](i)))
                if set(row["id"] for row in local["games"]) != set(local_ids[i]):
                    raise RuntimeError("Personal ownership fixture mismatch")
                replay = require(client.get(endpoint_builders["completed_replay"](i)))
                if len(replay["states"]) != 9:
                    raise RuntimeError("Replay fixture must contain nine validated states")
                for build in endpoint_builders.values():
                    require(client.get(build(i)))
            with sqlite3.connect(db) as c:
                tables = [
                    "accounts",
                    "games",
                    "online_games",
                    "online_moves",
                    "online_results",
                    "rating_events",
                    "player_ratings",
                    "player_tournaments",
                    "player_tournament_entrants",
                    "player_tournament_matches",
                    "player_tournament_legs",
                ]
                report["seed_counts"] = {
                    table: c.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                    for table in tables
                }
                report["sqlite"] = {
                    "version": sqlite3.sqlite_version,
                    "journal_mode": c.execute("PRAGMA journal_mode").fetchone()[0],
                    "database_bytes": db.stat().st_size,
                    "wal_bytes": Path(str(db) + "-wal").stat().st_size
                    if Path(str(db) + "-wal").exists()
                    else 0,
                }
                plans = {
                    "owned_single_category": (
                        "SELECT id FROM games WHERE category=? AND owner_account_id=? ORDER BY created_at DESC LIMIT 25",
                        ("account-single-player", account_ids[0]),
                    ),
                    "owned_local_category": (
                        "SELECT id FROM games WHERE category=? AND owner_account_id=? ORDER BY created_at DESC LIMIT 5",
                        ("local", account_ids[0]),
                    ),
                    "online_status_updated": (
                        "SELECT id FROM online_games WHERE status=? ORDER BY updated_at DESC,id LIMIT 25",
                        ("active",),
                    ),
                    "online_moves_replay": (
                        "SELECT ply,source,target,accepted_at FROM online_moves WHERE game_id=? ORDER BY ply",
                        (replay_ids[0],),
                    ),
                    "tournament_matches": (
                        "SELECT id FROM player_tournament_matches WHERE tournament_id=? ORDER BY bracket,round,slot LIMIT 50",
                        (tournament_id,),
                    ),
                }
                plan_report = {}
                for label, (query, values) in plans.items():
                    try:
                        plan_report[label] = {
                            "query": query,
                            "plan": [
                                row[3] for row in c.execute("EXPLAIN QUERY PLAN " + query, values)
                            ],
                        }
                    except sqlite3.Error as e:
                        plan_report[label] = {"query": query, "error": str(e)}
                report["query_plan_note"] = (
                    "Separate representative read fragments on the small fixture, not an assertion that every HTTP service query has this plan."
                )
                report["query_plans"] = plan_report
                report["indexes"] = {
                    table: [row[1] for row in c.execute("PRAGMA index_list(" + table + ")")]
                    for table in [
                        "games",
                        "online_games",
                        "online_moves",
                        "player_tournaments",
                        "player_tournament_matches",
                        "player_ratings",
                    ]
                }
            runs = []
            raw = []
            state_lock = threading.Lock()
            active_actors = set()
            maximum_identities = [0]

            def request(item):
                i, label, path = item
                with state_lock:
                    if i in active_actors:
                        raise RuntimeError("Driver must not overlap requests for the same account")
                    active_actors.add(i)
                    maximum_identities[0] = max(maximum_identities[0], len(active_actors))
                started = time.perf_counter()
                status = 0
                size = 0
                error = None
                try:
                    response = actors[i].get(path)
                    status = response.status_code
                    size = len(response.content)
                except httpx.HTTPError as e:
                    error = type(e).__name__
                finally:
                    with state_lock:
                        active_actors.remove(i)
                return {
                    "actor_index": i,
                    "endpoint": label,
                    "status": status,
                    "bytes": size,
                    "milliseconds": (time.perf_counter() - started) * 1000,
                    "error_type": error,
                }

            def actor_workload(i):
                # One worker/session per actor: configured concurrency is genuine
                # distinct-account request concurrency, not repeated contexts.
                return [
                    request((i, label, build(i)))
                    for _ in range(args.rounds)
                    for label, build in endpoint_builders.items()
                ]

            def server_cpu_seconds():
                fields = Path(f"/proc/{process.pid}/stat").read_text().rsplit(")", 1)[1].split()
                return (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK")

            def server_memory():
                status = Path(f"/proc/{process.pid}/status").read_text().splitlines()
                return {
                    line.split(":", 1)[0]: int(line.split()[1])
                    for line in status
                    if line.startswith(("VmRSS:", "VmHWM:"))
                }

            for level in concurrency:
                maximum_identities[0] = 0
                cpu_before = server_cpu_seconds()
                started = time.perf_counter()
                with ThreadPoolExecutor(max_workers=level) as executor:
                    rows = [
                        row
                        for result in executor.map(actor_workload, range(args.users))
                        for row in result
                    ]
                elapsed = time.perf_counter() - started
                cpu_seconds = server_cpu_seconds() - cpu_before
                by_endpoint = defaultdict(list)
                for row in rows:
                    by_endpoint[row["endpoint"]].append(row)
                run = {
                    "concurrency": level,
                    "maximum_observed_inflight_distinct_accounts": maximum_identities[0],
                    "distinct_authenticated_accounts": len(set(row["actor_index"] for row in rows)),
                    "elapsed_seconds": elapsed,
                    "observed_requests_per_second": len(rows) / elapsed,
                    "api_process_cpu_seconds": cpu_seconds,
                    "api_process_core_seconds_per_wall_second": cpu_seconds / elapsed,
                    "api_process_memory_kib_after_phase": server_memory(),
                    **metrics(rows),
                    "endpoints": {k: metrics(v) for k, v in by_endpoint.items()},
                }
                runs.append(run)
                raw.extend({"concurrency": level, **row} for row in rows)
                print(json.dumps({k: v for k, v in run.items() if k != "endpoints"}), flush=True)
            report["warmup_requests"] = args.users * (len(endpoint_builders) + 2)
            report["percentile_method"] = (
                f"nearest rank; {args.users * args.rounds} samples per endpoint per level"
            )
            if verified_source(source) != revision:
                raise RuntimeError("Source changed during measurement; evidence invalid")
            report["runs"] = runs
            report["limitations"] = [
                "small eight-player/default fixture, warm local keep-alive GETs only; not production capacity",
                "same-host threaded HTTP driver and one API process, no WAN/TLS/proxy/device load",
                "background account/online sweep disabled, no mixed writes or AI/training contention",
                "short eight-ply completed replays, no large-history or rare-name scans",
                "no league queue exists in baseline so it is not benchmarked",
                "no inference/analysis load generated; use separate bounded-admission/security tests",
                "statistics/leaderboard sample represents this fixture only; no large-database or hardware extrapolation",
            ]
            (out / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
            (out / "requests.json").write_text(json.dumps(raw, indent=2) + "\n")
            if any(run["successes"] != run["requests"] for run in runs):
                raise RuntimeError("Benchmark recorded non200 responses; inspect report")
        finally:
            for client in clients:
                client.close()
            if process:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            log.close()
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--port", type=int, default=8501)
    parser.add_argument("--users", type=int, default=8)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--concurrency", default="1,4,8")
    parser.add_argument(
        "--output",
        default=str(Path(tempfile.gettempdir()) / ("arena-backend-reads-" + uuid4().hex)),
    )
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--data-dir")
    args = parser.parse_args()
    if args.serve:
        serve(Path(args.source_root), Path(args.data_dir), args.port)
    else:
        # Run under the shared flock and a timeout. Graceful cancellation still
        # closes HTTP clients, terminates the API child and removes owned data.
        def cancelled(_signum, _frame):
            raise SystemExit("Benchmark cancelled")

        signal.signal(signal.SIGTERM, cancelled)
        main(args)
