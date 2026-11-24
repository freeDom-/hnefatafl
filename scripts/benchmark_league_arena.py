"""Measure League operations on new temporary SQLite fixtures.

Run with the repository interpreter, without other local workloads. The main
comparison imports the same online/tournament services from an archived source
tree on PYTHONPATH and uses no League tables. It measures the common writers.
"""

from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import sqlite3
import subprocess
import tempfile
import threading
import time
from uuid import uuid4

from benchmark_backend_reads import (
    cpu_affinity_count,
    cpu_model,
    cgroup_cpu_quota_cores,
    percentile,
)

from hnefatafl import lifecycle
from hnefatafl.auth import Auth, SecuritySettings, digest
from hnefatafl.online_core import OnlineCore, OnlineError, micros
from hnefatafl.online_settings import OnlineSettings
from hnefatafl.player_tournament_settlement import TournamentSettlement
from hnefatafl.player_tournaments import PlayerTournaments
from hnefatafl.storage import Store

RULESET = "brandubh"
TIME = datetime(2026, 4, 12, 12, 0, tzinfo=timezone.utc)


@dataclass
class Probe:
    statements: int = 0
    writer_wait_ms: list[float] = field(default_factory=list)
    writer_hold_ms: list[float] = field(default_factory=list)
    capture_selects: bool = False
    selects: list[str] = field(default_factory=list)

    def trace(self, statement):
        self.statements += 1
        if self.capture_selects and statement.lstrip().startswith(("SELECT", "WITH")):
            self.selects.append(statement)


class ObservedConnection:
    def __init__(self, connection, probe):
        self.connection = connection
        self.probe = probe
        self.acquired = None

    def execute(self, sql, parameters=()):
        if sql != "BEGIN IMMEDIATE":
            return self.connection.execute(sql, parameters)
        started = time.perf_counter()
        result = self.connection.execute(sql, parameters)
        self.acquired = time.perf_counter()
        self.probe.writer_wait_ms.append((self.acquired - started) * 1000)
        return result

    def __getattr__(self, name):
        return getattr(self.connection, name)


class ObservedStore(Store):
    def __init__(self, path):
        self.recording = threading.local()
        self.background = None
        super().__init__(path)

    @contextmanager
    def connection(self):
        probe = getattr(self.recording, "probe", None) or self.background
        observed = None
        try:
            with super().connection() as connection:
                if probe is None:
                    yield connection
                else:
                    connection.set_trace_callback(probe.trace)
                    observed = ObservedConnection(connection, probe)
                    yield observed
        finally:
            if observed is not None and observed.acquired is not None:
                probe.writer_hold_ms.append((time.perf_counter() - observed.acquired) * 1000)


class Clock:
    now = TIME

    def __call__(self):
        return self.now


@dataclass(frozen=True)
class Actor:
    id: str
    token: str


class Fixture:
    def __init__(self, directory, label):
        self.store = ObservedStore(directory / label / "db.sqlite3")
        self.clock = Clock()
        self.auth = Auth(self.store, SecuritySettings())
        self.online = OnlineCore(self.auth, OnlineSettings(enabled=True), self.clock)

    def actors(self, prefix, count, *, admin=False):
        actors = [
            Actor(f"{prefix}{i:06d}", digest(f"league-benchmark-{prefix}-{i}"))
            for i in range(count)
        ]
        # Setup only: the service validates these ordinary account/session rows on
        # every measured operation. No need to hash thousands of login passwords.
        with self.store.connection() as c:
            c.executemany(
                """INSERT INTO accounts(id,username,email,password_hash,role,created_at,email_verified_at)
                   VALUES(?,?,?,'!no-benchmark-login',?,1,1)""",
                [
                    (a.id, a.id, f"{a.id}@example.test", "ADMIN" if admin else "USER")
                    for a in actors
                ],
            )
            c.executemany(
                "INSERT INTO sessions(token_hash,account_id,expires,csrf_hash) VALUES(?,?,?,?)",
                [(a.token, a.id, time.time() + 3600, digest(a.id)) for a in actors],
            )
        return actors

    def seat(self, attackers, defenders, *, league=None):
        with self.online._write() as c:
            now = self.clock()
            rating = {"rated": True} if league is not None else {}
            game = self.online.seat_game(
                c,
                creator_id=attackers.id,
                opponent_id=defenders.id,
                ruleset=RULESET,
                creator_side="attackers",
                control=lifecycle.TimeControl(600, "total", 5),
                now=now,
                request_id=str(uuid4()),
                **rating,
            )
            if league is not None:
                season = league._season(c, now)
                c.execute(
                    """INSERT INTO league_games(game_id,season_id,ruleset,first_account,second_account,paired_at)
                       VALUES(?,?,?,?,?,?)""",
                    (
                        game,
                        season.identifier,
                        RULESET,
                        *sorted((attackers.id, defenders.id)),
                        micros(now),
                    ),
                )
        return game

    def complete(self, game, attackers, defenders, *, attackers_win):
        if not attackers_win:
            return self.online.mutate(attackers.token, game, 1, "concede")["game"]
        moved = self.online.mutate(attackers.token, game, 1, "move", source=3, target=2)["game"]
        return self.online.mutate(defenders.token, game, moved["version"], "concede")["game"]


@dataclass
class Sample:
    kind: str
    milliseconds: float
    probe: Probe
    units: int = 0
    error: str | None = None


def run_sample(store, kind, operation):
    probe = Probe()
    store.recording.probe = probe
    started = time.perf_counter()
    units = 0
    error = None
    try:
        result = operation()
        units = 1 if result is None else int(result)
    except Exception as exc:
        # Error categories are sufficient evidence and avoid echoing internals.
        error = (
            f"{type(exc).__name__}:{exc.status}"
            if isinstance(exc, OnlineError)
            else type(exc).__name__
        )
    finally:
        store.recording.probe = None
    return Sample(kind, (time.perf_counter() - started) * 1000, probe, units, error)


def distribution(values):
    if not values:
        return None
    return {f"p{key}": round(percentile(values, key / 100), 4) for key in (50, 95, 99)}


def summarize(samples):
    writers = [wait for sample in samples for wait in sample.probe.writer_wait_ms]
    holds = [hold for sample in samples for hold in sample.probe.writer_hold_ms]
    statements = [sample.probe.statements for sample in samples]
    return {
        "operations": len(samples),
        "work_units": sum(sample.units for sample in samples),
        "errors": dict(Counter(sample.error for sample in samples if sample.error)),
        "latency_ms": distribution([sample.milliseconds for sample in samples]),
        "sql_statements": {"total": sum(statements), **(distribution(statements) or {})},
        "writer_transactions": len(writers),
        "writer_wait_ms": distribution(writers),
        "writer_hold_ms": distribution(holds),
    }


def timed(store, jobs, *, concurrency=1):
    def measure(job):
        kind, operation = job
        return run_sample(store, kind, operation)

    start = time.perf_counter()
    if concurrency > 1:
        with ThreadPoolExecutor(max_workers=concurrency) as writer_pool:
            samples = list(writer_pool.map(measure, jobs))
    else:
        samples = [measure(job) for job in jobs]
    elapsed = time.perf_counter() - start
    groups = {
        kind: [sample for sample in samples if sample.kind == kind]
        for kind in {s.kind for s in samples}
    }
    return {
        "seconds": round(elapsed, 5),
        "concurrency": concurrency,
        "operations_per_second": round(len(samples) / elapsed, 2),
        "work_units_per_second": round(sum(sample.units for sample in samples) / elapsed, 2),
        "by_operation": {kind: summarize(groups[kind]) for kind in sorted(groups)},
    }


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def explain(store, operation, match_sql):
    probe = Probe(capture_selects=True)
    store.recording.probe = probe
    try:
        operation()
    finally:
        store.recording.probe = None
    statements = [sql for sql in probe.selects if match_sql in sql]
    require(bool(statements), "Expected application query was not captured")
    with store.connection() as c:
        return [row[3] for sql in statements for row in c.execute("EXPLAIN QUERY PLAN " + sql)]


def seed_ratings(c, ratings):
    c.executemany(
        """INSERT INTO player_ratings(account_id,pool,rating,rated_games,wins,losses,draws,policy_version,updated_at)
           VALUES(?,?,?,0,0,0,0,1,?)""",
        [(a.id, RULESET, value, micros(TIME)) for a, value in ratings],
    )


def seed_queue(fixture, service, actors, *, deep_tail):
    now = micros(fixture.clock())
    with fixture.online._write() as c:
        service._season(c, fixture.clock())
        for offset, actor in enumerate(actors):
            request = str(uuid4())
            at = now - len(actors) + offset
            expires = at + 600_000_000
            c.execute(
                """INSERT INTO league_requests(id,account_id,ruleset,created_at,entry_id,joined_at,expires_at,status)
                   VALUES(?,?,?,?,?,?,?,'queued')""",
                (request, actor.id, RULESET, at, request, at, expires),
            )
            c.execute(
                "INSERT INTO league_queue(account_id,request_id,ruleset,joined_at,expires_at) VALUES(?,?,?,?,?)",
                (actor.id, request, RULESET, at, expires),
            )
        if deep_tail:
            # Large, distinct synthetic gaps deliberately keep every earlier
            # entry outside the official maximum window throughout this trial.
            ratings = [(actor, 2300 + (610 * i)) for i, actor in enumerate(actors[:-2])]
            seed_ratings(c, ratings)


def queue_workloads(root, args):
    from hnefatafl.leagues import Leagues

    report = {}
    for size, label, deep_tail in (
        (8, "small", False),
        (args.players, "large", False),
        (130, "deep_tail", True),
    ):
        fixture = Fixture(root, f"queue-{label}")
        service = Leagues(fixture.online)
        actors = fixture.actors("Queue", size)
        seed_queue(fixture, service, actors, deep_tail=deep_tail)
        starts = time.perf_counter()
        steps = []
        max_cycles = 130 if deep_tail else (args.samples if size > 8 else 4)

        def cycle():
            result = service.drain(limit=20)
            return result["matched"]

        for _ in range(max_cycles):
            steps.append(run_sample(fixture.store, "worker_cycle", cycle))
            if deep_tail and service.queue_status(actors[-1].token)["status"] == "matched":
                break
        elapsed = time.perf_counter() - starts
        with fixture.store.connection() as c:
            pairs = c.execute("SELECT count(*) FROM league_games").fetchone()[0]
        require(pairs > 0, f"The {label} queue did not produce a pairing")
        if deep_tail:
            require(
                pairs == 1 and service.queue_status(actors[-1].token)["status"] == "matched",
                "The deep tail did not pair",
            )
        report[label] = {
            "starting_queue": size,
            "matched_pairs": pairs,
            "cycles": len(steps),
            "pairs_per_second": round(pairs / elapsed, 2),
            "seconds": round(elapsed, 5),
            **summarize(steps),
        }
    return report


def standings_workload(root, args):
    from hnefatafl.leagues import Leagues
    from hnefatafl.league_views import LeagueCursors, LeagueViews

    fixture = Fixture(root, "standings")
    service = Leagues(fixture.online)
    views = LeagueViews(service, LeagueCursors())
    actors = fixture.actors("Ranked", args.players)
    viewer = actors[-1]
    with fixture.online._write() as c:
        season = service._season(c, TIME).identifier
        c.executemany(
            """INSERT INTO league_standings(season_id,ruleset,account_id,points,wins,draws,losses,updated_at,last_sequence)
               VALUES(?,?,?,?,?,0,2,?,1)""",
            [
                (season, RULESET, actor.id, (i % 32 + 1) * 3, i % 32 + 1, micros(TIME))
                for i, actor in enumerate(actors)
            ],
        )

    def page(cursor):
        return views.standings(viewer.token, RULESET, season, 25, cursor)

    cursor = None
    for _ in range(args.players * 4 // (5 * 25)):
        cursor = page(cursor)["next_cursor"]
        require(cursor is not None, "Standings fixture ended before deep cursor")
    require(bool(page(cursor)["items"]), "Deep standings page was empty")

    def read_page(after):
        return len(page(after)["items"])

    return {
        "ranked_players": args.players,
        "page_size": 25,
        "deep_offset": (args.players * 4 // (5 * 25)) * 25,
        "deep_page_query_plan": explain(
            fixture.store, lambda: page(cursor), "SELECT * FROM league_standings"
        ),
        **timed(
            fixture.store,
            [("first_page", lambda: read_page(None)), ("deep_page", lambda: read_page(cursor))]
            * args.samples,
        ),
    }


def seed_tournaments(fixture, samples):
    service = PlayerTournaments(fixture.auth, fixture.online)
    settler = TournamentSettlement(service)
    users = fixture.actors("CupUser", 2)
    (admin,) = fixture.actors("CupAdmin", 1, admin=True)
    by_id = {a.id: a for a in users}
    for number in range(samples):
        cup = service.create(
            admin.token,
            name=f"Benchmark cup {number}",
            ruleset=RULESET,
            capacity=2,
            match_mode="sequential",
        )
        for member in users:
            cup = service.register(member.token, cup["id"])
        cup = service.start(admin.token, cup["id"], cup["version"])
        match = next(
            m for m in service.matches(admin.token, cup["id"])["items"] if m["status"] == "ready"
        )
        first = fixture.online.read(admin.token, match["legs"][0]["game_id"])
        actor = by_id[first["participants"]["attackers"]["account_id"]]
        fixture.online.mutate(actor.token, first["id"], first["version"], "concede")
    return settler


def mixed_writers(root, args, *, include_league):
    fixture = Fixture(root, "mixed-league" if include_league else "mixed-common")
    (attackers, defenders) = fixture.actors("Online", 2)
    games = [fixture.seat(attackers, defenders) for _ in range(args.samples)]
    tournament = seed_tournaments(fixture, args.samples)
    tournament_writer = threading.Lock()

    def drain_tournament():
        # One tournament worker, as in the service. Online and League writers
        # still contend with this writer in SQLite.
        with tournament_writer:
            require(tournament.drain(limit=1) == 1, "Tournament settlement did not commit")
        return 1

    tasks = []
    for game in games:
        tasks.append(
            (
                "online_move",
                lambda game=game: bool(
                    fixture.online.mutate(attackers.token, game, 1, "move", source=3, target=2)
                ),
            )
        )
        tasks.append(("tournament_drain", drain_tournament))

    if include_league:
        from hnefatafl.leagues import Leagues

        service = Leagues(fixture.online)
        league_writer = threading.Lock()
        result_actors = fixture.actors("LeagueResult", args.samples * 2)
        for first, second in zip(result_actors[::2], result_actors[1::2], strict=True):
            game = fixture.seat(first, second, league=service)
            fixture.complete(game, first, second, attackers_win=False)
            fixture.clock.now += timedelta(milliseconds=1)
        # Distinct identities have no compatible opponent under the fixed clock.
        queue_actors = fixture.actors("QueueActor", args.samples)
        with fixture.online._write() as c:
            seed_ratings(c, [(actor, 900 + 610 * i) for i, actor in enumerate(queue_actors)])

        def join_and_cancel(actor):
            request = str(uuid4())
            status = service.join(actor.token, RULESET, request)
            require(status["status"] == "queued", "Unrelated measured player matched")
            status = service.cancel(actor.token, request)
            require(status["status"] == "cancelled", "Measured cancellation failed")
            return 2

        def drain_league():
            with league_writer:
                require(service.drain(limit=1)["settled"] == 1, "League settlement did not commit")
            return 1

        for idx, actor in enumerate(queue_actors):
            tasks.insert(
                4 * idx + 2, ("league_join_cancel", lambda actor=actor: join_and_cancel(actor))
            )
            tasks.insert(4 * idx + 3, ("league_drain", drain_league))

    result = timed(fixture.store, tasks, concurrency=args.writers)
    with fixture.store.connection() as c:
        require(
            c.execute("SELECT count(*) FROM online_moves").fetchone()[0] == args.samples,
            "Online moves did not all commit",
        )
        require(
            c.execute("SELECT count(*) FROM player_tournament_settlement_queue").fetchone()[0] == 0,
            "Tournament settlements remained pending",
        )
        if include_league:
            require(
                c.execute("SELECT count(*) FROM league_settlement_queue").fetchone()[0] == 0,
                "League settlements remained pending",
            )
    return result


def result_workloads(root, args):
    from hnefatafl.leagues import Leagues
    from hnefatafl.league_views import LeagueCursors, LeagueViews

    report = {}
    fixture = Fixture(root, "results")
    service = Leagues(fixture.online)
    views = LeagueViews(service, LeagueCursors())
    (target,) = fixture.actors("Target", 1)
    (admin,) = fixture.actors("ResultAdmin", 1, admin=True)
    corpus_size = args.games + args.samples * 2
    opponents = [
        *fixture.actors("Opponent", corpus_size // 2),
        *fixture.actors("Visitor", corpus_size - corpus_size // 2),
    ]
    game_ids = []
    for i, opponent in enumerate(opponents):
        game = fixture.seat(target, opponent, league=service)
        fixture.complete(game, target, opponent, attackers_win=i % 3 != 0)
        game_ids.append(game)
        fixture.clock.now += timedelta(milliseconds=1)

    for start in range(0, args.games, 50):
        settled = service.drain(limit=min(50, args.games - start))
        require(
            settled["settled"] == min(50, args.games - start),
            "Initial result corpus did not settle",
        )

    replay = Fixture(root, "replay")
    with sqlite3.connect(fixture.store.path) as source, sqlite3.connect(replay.store.path) as copy:
        source.backup(copy)
    replay.clock.now = fixture.clock.now
    replay_service = Leagues(replay.online)

    season = "2026-04"
    page = views.history(target.token, RULESET, season, 25, None)
    deep = page["next_cursor"]
    for _ in range(args.games * 4 // (5 * 25) - 1):
        deep = views.history(target.token, RULESET, season, 25, deep)["next_cursor"]
        require(deep is not None, "History fixture ended before deep cursor")

    report["personal_history"] = timed(
        fixture.store,
        [
            (
                "first_page",
                lambda: len(views.history(target.token, RULESET, season, 25, None)["items"]),
            ),
            (
                "deep_page",
                lambda: len(views.history(target.token, RULESET, season, 25, deep)["items"]),
            ),
        ]
        * args.samples,
    )
    report["personal_history"]["deep_offset"] = (args.games * 4 // (5 * 25)) * 25
    report["personal_history"]["deep_page_query_plan"] = explain(
        fixture.store,
        lambda: views.history(target.token, RULESET, season, 25, deep),
        "WITH first_seat",
    )

    def settle_one(worker):
        result = worker.drain(limit=1)
        require(result["settled"] == 1, "No pending result was settled")
        return 1

    report["result_processing"] = timed(
        fixture.store, [("append", lambda: settle_one(service))] * args.samples
    )
    replay_started = time.perf_counter()
    replay_samples = []
    for _ in range(args.samples):
        # Make only the high-history player's projection metadata disagree with
        # its ledger tail. This triggers the normal exact fallback on an otherwise
        # identical result. Exclude the deliberate mutation from sample latency.
        with replay.online._write() as c:
            c.execute(
                "UPDATE league_standings SET last_sequence=last_sequence+1 WHERE account_id=?",
                (target.id,),
            )
        replay_samples.append(
            run_sample(replay.store, "replay", lambda: settle_one(replay_service))
        )
    elapsed = time.perf_counter() - replay_started
    report["result_processing_full_replay"] = {
        "using_same_results": True,
        "operations_per_second_excluding_fixture_writes": round(
            len(replay_samples) * 1000 / sum(sample.milliseconds for sample in replay_samples), 2
        ),
        "elapsed_seconds_including_fixture_writes": round(elapsed, 5),
        **summarize(replay_samples),
    }
    statement = (
        "SELECT points,wins,draws,losses,last_sequence FROM league_standings WHERE account_id=?"
    )
    with fixture.store.connection() as live, replay.store.connection() as copied:
        require(
            tuple(live.execute(statement, (target.id,)).fetchone())
            == tuple(copied.execute(statement, (target.id,)).fetchone()),
            "Incremental and exact settlement disagreed",
        )
    report["settlement_throughput"] = timed(
        fixture.store, [("batch", lambda: service.drain(limit=args.samples)["settled"])]
    )

    def correct(game):
        latest = fixture.online.read(admin.token, game)
        fixture.online.mutate(
            admin.token, game, latest["version"], "void", note="Synthetic benchmark withdrawal"
        )

    # Keep correction sample size bounded; the existing Elo authority rebuilds
    # that whole rating pool before the League worker sees each withdrawal.
    correction_jobs = [
        ("online_void", lambda game=game: correct(game))
        for game in game_ids[: min(args.samples, 10)]
    ]
    report["official_correction"] = timed(fixture.store, correction_jobs)
    report["withdrawal_projection"] = timed(
        fixture.store, [("withdrawal", lambda: settle_one(service))] * len(correction_jobs)
    )
    report["rebuild"] = rebuild_workload(fixture, views, target, admin, season, corpus_size)
    return report


def rebuild_workload(fixture, views, target, admin, season, corpus_size):
    from hnefatafl.league_administration import LeagueAdministration

    administration = LeagueAdministration(views)
    current = views.current(target.token, RULESET)
    version = current["season"]["revision"]
    old = current["progress"]
    with fixture.online._write() as c:
        c.execute("DELETE FROM league_standings WHERE account_id=?", (target.id,))
    start = time.perf_counter()
    request = run_sample(
        fixture.store,
        "rebuild_request",
        lambda: bool(
            administration.rebuild(admin.token, season, version, "Disposable benchmark repair")
        ),
    )
    require(not request.error, "Rebuild request failed")
    require(views.current(target.token, RULESET)["progress"] is None, "Partial standings escaped")
    steps = []
    batch = 50
    for _ in range(corpus_size * 10 // batch + 20):
        step = run_sample(
            fixture.store, "rebuild_batch", lambda: administration.process_rebuild(batch)
        )
        steps.append(step)
        require(not step.error, "Rebuild batch raised an error")
        if not step.units:
            break
    elapsed = time.perf_counter() - start
    restored = views.current(target.token, RULESET)
    require(restored["season"]["revision"] == version + 1, "Rebuild never finalized")
    for key in ("points", "wins", "draws", "losses", "rank", "tier"):
        require(restored["progress"][key] == old[key], "Rebuild changed the official standing")
    return {
        "official_games": corpus_size,
        "batch_limit": batch,
        "active_transactions": sum(step.units for step in steps),
        "seconds_without_worker_wait": round(elapsed, 5),
        "games_per_second_without_worker_wait": round(corpus_size / elapsed, 2),
        "request": summarize([request]),
        "steps": summarize(steps),
    }


def resident_bytes():
    try:
        return int(Path("/proc/self/statm").read_text().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, IndexError):
        return None


def idle_worker(root, args):
    from hnefatafl.league_administration import LeagueAdministration
    from hnefatafl.leagues import Leagues
    from hnefatafl.league_views import LeagueCursors, LeagueViews
    from hnefatafl.league_worker import LeagueWorker

    fixture = Fixture(root, "idle")
    core = Leagues(fixture.online)
    worker = LeagueWorker(LeagueAdministration(LeagueViews(core, LeagueCursors())))
    probe = Probe()
    fixture.store.background = probe
    before_rss = resident_bytes()
    before_cpu = time.process_time()
    started = time.perf_counter()
    worker.start()
    try:
        threading.Event().wait(args.idle_seconds)
    finally:
        worker.shutdown()
    elapsed = time.perf_counter() - started
    health = worker.health()
    require(
        bool(health["last_success_at"]) and not health["last_error"],
        "Idle worker did not complete a clean cycle",
    )
    require(not health["running"], "Idle worker did not shut down")
    return {
        "seconds": round(elapsed, 5),
        "process_cpu_ms": round((time.process_time() - before_cpu) * 1000, 3),
        "process_rss_before_bytes": before_rss,
        "process_rss_after_bytes": resident_bytes(),
        "sql_statements": probe.statements,
        "writer_transactions": len(probe.writer_wait_ms),
        "errors": 0,
    }


def source_evidence(args):
    import hnefatafl

    package = Path(hnefatafl.__file__).resolve().parent
    git_revision = subprocess.run(
        ["git", "-C", str(package), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    dirty = bool(
        subprocess.run(
            ["git", "-C", str(package), "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
    )
    modules = ["online_core.py", "player_tournaments.py", "player_tournament_settlement.py"]
    if not args.baseline:
        modules += sorted(path.name for path in package.glob("league*.py"))
    return {
        "git_revision": args.source_revision or git_revision,
        "source_tree_dirty": dirty,
        "source_sha256": {
            name: hashlib.sha256((package / name).read_bytes()).hexdigest() for name in modules
        },
    }


def options():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--players", type=int, default=4096)
    parser.add_argument("--games", type=int, default=1200)
    parser.add_argument("--samples", type=int, default=30)
    parser.add_argument("--writers", type=int, default=6)
    parser.add_argument("--idle-seconds", type=int, default=8)
    parser.add_argument("--baseline", action="store_true")
    parser.add_argument("--source-revision")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not (100 <= args.players <= 50_000 and 100 <= args.games <= 20_000):
        parser.error("Use 100–50,000 players and 100–20,000 games")
    if not (1 <= args.samples <= 100 and 1 <= args.writers <= 24):
        parser.error("Use 1–100 samples and 1–24 writers")
    if not 2 <= args.idle_seconds <= 120:
        parser.error("Use 2–120 seconds for the idle measurement")
    return args


def main():
    args = options()
    with tempfile.TemporaryDirectory(prefix="hnefatafl-league-benchmark-") as directory:
        root = Path(directory)
        report = {
            **source_evidence(args),
            "environment": {
                "platform": platform.platform(),
                "cpu_model": cpu_model(),
                "logical_cpus": os.cpu_count(),
                "affinity_cpus": cpu_affinity_count(),
                "cpu_quota_cores": cgroup_cpu_quota_cores(),
                "python": platform.python_version(),
                "sqlite": sqlite3.sqlite_version,
            },
            "workload": {
                "players": args.players,
                "historical_games": args.games,
                "samples": args.samples,
                "writers": args.writers,
            },
            "mixed_common": mixed_writers(root, args, include_league=False),
        }
        if not args.baseline:
            report["mixed_with_league"] = mixed_writers(root, args, include_league=True)
            report["queues"] = queue_workloads(root, args)
            report["standings"] = standings_workload(root, args)
            report.update(result_workloads(root, args))
            report["idle_worker"] = idle_worker(root, args)
    payload = json.dumps(report, indent=2) + "\n"
    if args.output is None:
        print(payload, end="")
    else:
        args.output.write_text(payload, encoding="utf-8")


if __name__ == "__main__":
    main()
