"""Compare shared writers and measure the League worker at its real cadence.

Use a disposable fixture prepared on main for the common arms. The League
arms seed pending requests before the worker starts so HTTP admission cannot
be mistaken for background pairings.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
import json
from pathlib import Path
from random import Random
import shutil
import sqlite3
import tempfile
import threading
import time
from uuid import uuid4

from benchmark_league_arena import (
    Actor,
    Fixture,
    Probe,
    RULESET,
    TIME,
    distribution,
    mixed_writers,
    require,
    run_sample,
    seed_tournaments,
    source_evidence,
    summarize,
    timed,
)
from hnefatafl.auth import digest
from hnefatafl.online_core import micros
from hnefatafl.player_tournament_settlement import RECOVERY_RETRY_SECONDS, TournamentSettlement
from hnefatafl.player_tournaments import PlayerTournaments

ONLINE_FIRST = Actor("Online000000", digest("league-benchmark-Online-0"))
RATED_FIRST = Actor("Rated000000", digest("league-benchmark-Rated-0"))
MATCHMAKING_SCENARIOS = (
    "compatible",
    "resume",
    "expanding_windows",
    "capped_and_incompatible",
    "mixed_online",
    "expiry",
)
DISCOVERY_SCENARIOS = ("large_compatible", "sparse_tail", "sparse_mixed", "realistic_mixed")
RULESETS = ("brandubh", "copenhagen", "tablut")


def options():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "shared", "matchmaking"))
    parser.add_argument("--baseline", action="store_true")
    parser.add_argument("--source-revision")
    parser.add_argument("--template", type=Path)
    parser.add_argument("--samples", type=int, default=100)
    parser.add_argument("--writers", type=int, default=6)
    parser.add_argument("--worker-seconds", type=float, default=10)
    parser.add_argument(
        "--scenario", action="append", choices=MATCHMAKING_SCENARIOS + DISCOVERY_SCENARIOS
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 20 <= args.samples <= 400 or not 2 <= args.writers <= 16:
        parser.error("Use 20–400 samples and 2–16 foreground writers")
    if not 8 <= args.worker_seconds <= 60:
        parser.error("Use an 8–60 second sampling window")
    if args.mode in {"prepare", "shared"} and not args.template:
        parser.error("Supply the shared template directory")
    if args.mode == "matchmaking" and args.baseline:
        parser.error("The matchmaking arms require a League source tree")
    return args


def prepare(root, args):
    args.template.mkdir()
    fixture = Fixture(root, "common-template")
    first, second = fixture.actors("Online", 2)
    games = [fixture.seat(first, second) for _ in range(args.samples)]
    seed_tournaments(fixture, args.samples)
    first, second = fixture.actors("Rated", 2)
    fixture.online.settings = replace(fixture.online.settings, creation_limit=args.samples + 1)
    rated = []
    for _ in range(args.samples):
        waiting = fixture.online.create(first.token, RULESET, "attackers", 600, str(uuid4()))[
            "game"
        ]
        rated.append(fixture.online.mutate(second.token, waiting["id"], 0, "join")["game"]["id"])
    with (
        fixture.store.connection() as source,
        sqlite3.connect(args.template / "common.sqlite") as c,
    ):
        source.backup(c)
    (args.template / "games.json").write_text(json.dumps({"moves": games, "terminal": rated}))


def common_fixture(root, args, label):
    directory = root / label
    directory.mkdir()
    shutil.copyfile(args.template / "common.sqlite", directory / "db.sqlite3")
    fixture = Fixture(root, label)
    games = json.loads((args.template / "games.json").read_text())
    require(len(games["moves"]) == args.samples, "Sample count differs from the shared template")
    return fixture, games


def move(fixture, game_id):
    return fixture.online.mutate(ONLINE_FIRST.token, game_id, 1, "move", source=3, target=2)[
        "action_accepted"
    ]


def concede(fixture, game_id):
    return fixture.online.mutate(RATED_FIRST.token, game_id, 1, "concede")["action_accepted"]


def league_services(fixture):
    from hnefatafl.league_administration import LeagueAdministration
    from hnefatafl.league_views import LeagueCursors, LeagueViews
    from hnefatafl.league_worker import LeagueWorker
    from hnefatafl.leagues import Leagues

    core = Leagues(fixture.online)
    worker = LeagueWorker(LeagueAdministration(LeagueViews(core, LeagueCursors())))
    return core, worker


def worker_metrics(probe):
    return {
        "sql_statements": probe.statements,
        "writer_transactions": len(probe.writer_hold_ms),
        "writer_wait_ms": distribution(probe.writer_wait_ms),
        "writer_hold_ms": distribution(probe.writer_hold_ms),
        "writer_latency_ms": distribution(probe.writer_latency_ms),
        "sql_in_writer_total_ms": round(probe.sql_in_writer_ms, 3),
        "busy_writers": len(probe.writer_wait_ms) - len(probe.writer_hold_ms),
    }


def scheduled_moves(fixture, games, writers, started, interval, bunch):
    samples = []
    with ThreadPoolExecutor(max_workers=writers) as pool:
        for offset in range(0, len(games), bunch):
            due = started + interval * (offset // bunch)
            time.sleep(max(0, due - time.perf_counter()))
            batch = games[offset : offset + bunch]
            samples.extend(
                pool.map(
                    lambda game: run_sample(
                        fixture.store, "online_move", lambda: move(fixture, game)
                    ),
                    batch,
                )
            )
    return samples


def live_common(root, args, *, league_load=False):
    interval = RECOVERY_RETRY_SECONDS
    fixture, games = common_fixture(root, args, "live-mixed" if league_load else "live-common")
    tournament = TournamentSettlement(PlayerTournaments(fixture.auth, fixture.online))
    require(tournament.recover(), "Tournament worker could not start")
    core, worker = (None, None) if args.baseline else league_services(fixture)
    if league_load:
        seed_waiting(fixture, fixture.actors("Mixed", 240), [1500] * 240, 0, ready=True)
        results = fixture.actors("PendingLeague", 80)
        for first, second in zip(results[::2], results[1::2], strict=True):
            game = fixture.seat(first, second, league=core)
            fixture.complete(game, first, second, attackers_win=False)
    tournament_probe, league_probe = Probe(), Probe()
    fixture.store.by_thread.update(
        {"player-tournament-settlement": tournament_probe, "league-settlement": league_probe}
    )
    started = time.perf_counter()
    cpu = time.process_time()
    tournament.start()
    fixture.online.start()
    if worker is not None:
        worker.start()
    try:
        moves = scheduled_moves(
            fixture, games["moves"], args.writers, started + interval - 0.01, interval, 20
        )
        deadline = started + (args.samples // 20 + 4) * interval
        while time.perf_counter() < deadline:
            with fixture.store.connection() as c:
                pending = c.execute(
                    "SELECT 1 FROM player_tournament_settlement_queue LIMIT 1"
                ).fetchone()
            if not pending:
                break
            time.sleep(0.025)
        else:
            raise RuntimeError("The live tournament worker did not drain its queue")
        elapsed = time.perf_counter() - started
    finally:
        if worker is not None:
            worker.shutdown()
        fixture.online.shutdown()
        tournament.shutdown()
    require(not summarize(moves)["errors"], "A live shared move failed")
    league_matches = league_settled = 0
    if league_load:
        with fixture.store.connection() as c:
            league_matches = c.execute("SELECT COUNT(*) FROM league_games").fetchone()[0] - 40
            league_settled = (
                40 - c.execute("SELECT COUNT(*) FROM league_settlement_queue").fetchone()[0]
            )
        require(league_matches > 0, "The mixed workload must have live League pairings")
    return {
        "wall_seconds": round(elapsed, 4),
        "process_cpu_seconds": round(time.process_time() - cpu, 4),
        "online": summarize(moves),
        "tournament": worker_metrics(tournament_probe),
        "league": worker_metrics(league_probe),
        "league_pairs": league_matches,
        "league_settled": league_settled,
    }


def common(root, args):
    fixture, games = common_fixture(root, args, "isolated")
    tournament = TournamentSettlement(PlayerTournaments(fixture.auth, fixture.online))
    single = {
        "moves": timed(
            fixture.store,
            [("online_move", lambda game=g: move(fixture, game)) for g in games["moves"]],
        ),
        "rated_terminal": timed(
            fixture.store,
            [("rated_terminal", lambda game=g: concede(fixture, game)) for g in games["terminal"]],
        ),
        "tournament": timed(
            fixture.store,
            [("tournament_drain", lambda: tournament.drain(limit=1))] * args.samples,
        ),
    }
    fixture, games = common_fixture(root, args, "service-common")
    tournament = TournamentSettlement(PlayerTournaments(fixture.auth, fixture.online))
    gate = threading.Lock()

    def drain():
        with gate:
            return tournament.drain(limit=1)

    tasks = []
    for game in games["moves"]:
        tasks.extend(
            (("online_move", lambda game=game: move(fixture, game)), ("tournament_drain", drain))
        )
    shared = timed(fixture.store, tasks, concurrency=args.writers)
    for operation in shared["by_operation"].values():
        require(
            not operation["errors"] and operation["work_units"] == args.samples,
            "The shared fixture did not fully commit",
        )
    plans = {}
    if not args.baseline:
        with fixture.store.connection() as c:
            for label, sql in {
                "provenance": "SELECT season_id FROM league_games WHERE game_id=?",
                "reviewed_outbox": """INSERT INTO league_settlement_queue
                    (game_id,season_id,due_at,pending_since)
                    SELECT game_id,season_id,?,? FROM league_games WHERE game_id=?
                    ON CONFLICT(game_id) DO UPDATE SET due_at=MIN(due_at,excluded.due_at),
                    attempts=0,last_error=NULL""",
                "active_seats": "DELETE FROM league_active_players WHERE game_id=?",
            }.items():
                params = (0, 0, "fixture") if label == "reviewed_outbox" else ("fixture",)
                plans[label] = [row[3] for row in c.execute("EXPLAIN QUERY PLAN " + sql, params)]
    return {
        "isolated": single,
        "shared_service": shared,
        "shared_live": live_common(root, args),
        "mixed_live": None if args.baseline else live_common(root, args, league_load=True),
        "league_service": None if args.baseline else mixed_writers(root, args, include_league=True),
        "terminal_query_plans": plans,
    }


class LiveClock:
    """Stable UTC fixture with a real monotonic elapsed time during sampling."""

    started: float | None = None

    def __call__(self):
        elapsed = 0 if self.started is None else time.perf_counter() - self.started
        return TIME + timedelta(seconds=elapsed)


def seed_waiting(fixture, actors, ratings, age_seconds, ready, *, ruleset=RULESET):
    now = fixture.clock()
    joined = micros(now - timedelta(seconds=age_seconds))
    expires = joined + 600_000_000
    rows = [(actor.id, str(uuid4()), rating) for actor, rating in zip(actors, ratings, strict=True)]
    with fixture.online._write() as c:
        c.executemany(
            """INSERT INTO player_ratings
            (account_id,pool,rating,rated_games,wins,losses,draws,policy_version,updated_at)
            VALUES(?,?,?,0,0,0,0,1,?)
            ON CONFLICT(account_id,pool) DO UPDATE SET rating=excluded.rating""",
            [(account, ruleset, rating, joined) for account, _, rating in rows],
        )
        c.executemany(
            """INSERT INTO league_requests
            (id,account_id,ruleset,created_at,status,entry_id,joined_at,expires_at)
            VALUES(?,?,?,?,'queued',?,?,?)""",
            [
                (identifier, account, ruleset, joined, identifier, joined, expires)
                for account, identifier, _ in rows
            ],
        )
        c.executemany(
            """INSERT INTO league_queue(account_id,request_id,ruleset,joined_at,expires_at,rating)
            VALUES(?,?,?,?,?,?)""",
            [
                (account, identifier, ruleset, joined, expires, rating)
                for account, identifier, rating in rows
            ],
        )
    return joined, {account for account, _, _ in rows} if ready else set()


def seed_discovery(fixture, scenario):
    joined_at, targets = {}, set()
    rng = Random(99)
    pools = RULESETS if scenario.endswith("mixed") else (RULESET,)
    for ruleset in pools:
        if scenario in {"sparse_tail", "sparse_mixed"}:
            count = 2304 if scenario == "sparse_tail" else 1024
            queued = fixture.actors(f"Wait-{ruleset}", count)
            ratings = [5_000 + 1_000 * index for index in range(count - 4)] + [
                1500,
                1500,
                2400,
                2425,
            ]
            age = 568 if scenario == "sparse_tail" else 540
            joined, _ = seed_waiting(fixture, queued, ratings, age, ready=False, ruleset=ruleset)
            targets.update(actor.id for actor in queued[-4:])
            joined_at.update((actor.id, joined) for actor in queued)
        elif scenario == "realistic_mixed":
            for age in (90, 45, 0):
                queued = fixture.actors(f"Wait-{ruleset}-{age}", 120)
                ratings = [
                    1500 if index % 5 == 0 else round(rng.gauss(1500, 245)) for index in range(120)
                ]
                ratings[-4:] = [760, 840, 2390, 2570]
                joined, eligible = seed_waiting(
                    fixture, queued, ratings, age, ready=True, ruleset=ruleset
                )
                targets.update(eligible)
                joined_at.update((actor.id, joined) for actor in queued)
        else:
            queued = fixture.actors("Wait-compatible", 960)
            joined, targets = seed_waiting(fixture, queued, [1500] * len(queued), 0, ready=True)
            joined_at.update((actor.id, joined) for actor in queued)
    return joined_at, targets


def churn_discovery(fixture, core, actors, started):
    cancelled = 0
    for index, actor in enumerate(actors):
        time.sleep(max(0, started + index * 0.5 - time.perf_counter()))
        ruleset = RULESETS[index % len(RULESETS)]
        seed_waiting(fixture, [actor], [6_000_000 + 1000 * index], 0, ready=False, ruleset=ruleset)
        time.sleep(max(0, started + index * 0.5 + 0.4 - time.perf_counter()))
        state = core.queue_status(actor.token)
        if core.cancel(actor.token, state["queue"]["request_id"])["status"] == "cancelled":
            cancelled += 1
    return cancelled


def matchmaking(root, args, scenario):
    fixture = Fixture(root, scenario)
    fixture.online.clock = fixture.clock = LiveClock()
    core, worker = league_services(fixture)
    count = 240
    if scenario == "capped_and_incompatible":
        count = 64
    queued = fixture.actors("Queue", count) if scenario in MATCHMAKING_SCENARIOS else []
    age = 0
    ratings = [1500] * len(queued)
    if scenario == "expanding_windows":
        age = 58
        ratings = [1500 + (index // 2) * 1500 + (index % 2) * 250 for index in range(count)]
    if scenario == "expiry":
        age = 597
    if scenario == "capped_and_incompatible":
        ratings = [1500 + (index // 2) * 1500 for index in range(16)] + [
            14000 + index * 1500 for index in range(count - 16)
        ]
        # Three real committed results make each of the first eight pairs
        # incompatible for this UTC day. Every other account is isolated by Elo.
        for first, second in zip(queued[:16:2], queued[1:16:2], strict=True):
            for _ in range(3):
                game = fixture.seat(first, second, league=core)
                fixture.complete(game, first, second, attackers_win=False)
        core.drain(limit=100)
    if scenario in DISCOVERY_SCENARIOS:
        joined_at, eligible = seed_discovery(fixture, scenario)
    else:
        joined, eligible = seed_waiting(
            fixture, queued, ratings, age, ready=scenario != "capped_and_incompatible"
        )
        joined_at = {actor.id: joined for actor in queued}
    (admin,) = fixture.actors("QueueAdmin", 1, admin=True)
    administration = worker.administration
    if scenario == "resume":
        administration.configure(admin.token, 0, True, "Disposable benchmark pause")
    games = []
    if scenario == "mixed_online" or scenario.endswith("mixed"):
        attackers, defenders = fixture.actors("Online", 2)
        games = [fixture.seat(attackers, defenders) for _ in range(args.samples)]
    arrivals = (
        fixture.actors("Churn", int(args.worker_seconds * 2) - 1)
        if scenario.endswith("mixed")
        else []
    )
    with fixture.store.connection() as c:
        sequence = c.execute("SELECT COALESCE(MAX(rowid),0) FROM league_games").fetchone()[0]
    probe = Probe()
    fixture.store.by_thread["league-settlement"] = probe
    started = time.perf_counter()
    fixture.clock.started = started
    cpu = time.process_time()
    worker.start()
    samples = []
    pairs = []
    actionable = started + (2.0 if scenario == "expanding_windows" else 0.0)
    monitor = ThreadPoolExecutor(max_workers=2)
    churned = 0
    try:
        if scenario == "resume":
            time.sleep(2.1)
            with fixture.store.connection() as c:
                require(
                    c.execute("SELECT MAX(rowid) FROM league_games").fetchone()[0] is None,
                    "The worker started matching during the pause",
                )
            administration.configure(admin.token, 1, False, "Disposable benchmark resume")
            actionable = time.perf_counter()
        if games:
            foreground = monitor.submit(
                scheduled_moves,
                fixture,
                games,
                args.writers,
                started + 0.05,
                args.worker_seconds / max(1, (len(games) + 5) // 6),
                6,
            )
        if arrivals:
            churn = monitor.submit(churn_discovery, fixture, core, arrivals, started + 0.25)
        deadline = actionable + args.worker_seconds
        while time.perf_counter() < deadline:
            with fixture.store.connection() as c:
                rows = c.execute(
                    """SELECT rowid,paired_at,ruleset,first_account,second_account FROM league_games
                    WHERE rowid>? ORDER BY rowid""",
                    (sequence,),
                ).fetchall()
            if rows:
                observed = time.perf_counter()
                sequence = rows[-1][0]
                pairs.extend((row[1], observed, row[2], row[3], row[4]) for row in rows)
            time.sleep(0.050)
        if games:
            samples = foreground.result()
        if arrivals:
            churned = churn.result()
    finally:
        worker.shutdown()
        monitor.shutdown()
    at = micros(fixture.clock())
    with fixture.store.connection() as c:
        expired = sum(
            account in eligible and joined_at[account] == created
            for account, created in c.execute(
                """SELECT account_id,created_at FROM league_requests
                WHERE status='expired' OR (status='queued' AND expires_at<=?)""",
                (at,),
            )
        )
        unmatched = c.execute(
            "SELECT count(*) FROM league_queue WHERE expires_at>?", (at,)
        ).fetchone()[0]
    if scenario in MATCHMAKING_SCENARIOS:
        require(bool(pairs) == bool(eligible), "Unexpected matchmaking outcome in " + scenario)
    require(not samples or not summarize(samples)["errors"], "Foreground moves failed")
    require(churned == len(arrivals), "A queued arrival did not cancel")
    wall = time.perf_counter() - started
    return {
        "queued_players": len(joined_at),
        "target_players": len(eligible),
        "observed_pairs": len(pairs),
        "pairs_by_ruleset": {
            ruleset: sum(row[2] == ruleset for row in pairs) for ruleset in RULESETS
        },
        "arrivals_cancelled": churned,
        "still_queued": unmatched,
        "expired_eligible": expired,
        "pairings_per_sampling_second": round(len(pairs) / args.worker_seconds, 3),
        "after_actionable_ms": distribution([(row[1] - actionable) * 1000 for row in pairs]),
        "queue_wait_ms": distribution(
            [
                (at - joined_at[account]) / 1000
                for at, _, _, first, second in pairs
                for account in (first, second)
            ]
        ),
        "time_to_first_pair_seconds": round(pairs[0][1] - actionable, 4) if pairs else None,
        "time_to_last_pair_seconds": round(pairs[-1][1] - actionable, 4) if pairs else None,
        "process_cpu_seconds": round(time.process_time() - cpu, 4),
        "wall_seconds": round(wall, 4),
        "worker": worker_metrics(probe),
        "online": summarize(samples) if samples else None,
    }


def main():
    args = options()
    with tempfile.TemporaryDirectory(prefix="hnefatafl-league-live-") as directory:
        root = Path(directory)
        if args.mode == "prepare":
            prepare(root, args)
            return
        settings = {
            "samples": args.samples,
            "writers": args.writers,
            "worker_seconds": args.worker_seconds,
        }
        results = (
            common(root, args)
            if args.mode == "shared"
            else {
                scenario: matchmaking(root, args, scenario)
                for scenario in (args.scenario or MATCHMAKING_SCENARIOS)
            }
        )
        report = {**source_evidence(args), "settings": settings, "workloads": results}
    payload = json.dumps(report, indent=2) + "\n"
    if args.output is None:
        print(payload, end="")
    else:
        args.output.write_text(payload)


if __name__ == "__main__":
    main()
