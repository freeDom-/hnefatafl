"""Measure bounded analysis-context delivery for representative long games.

Creates deterministic legal random games, inserts them through the real store,
then measures the authenticated context endpoint's reconstruction/response sizes
for short and long histories and repeated version changes.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from hnefatafl.auth import COOKIE  # noqa: E402
from hnefatafl.engine import apply_move, legal_moves, new_game  # noqa: E402
from hnefatafl.game_records import game_record  # noqa: E402
from hnefatafl.security_context import principal  # noqa: E402
from hnefatafl.server import create_app  # noqa: E402

PASSWORD = "fixture-only-password-123"


def generated_moves(ruleset: str, limit: int, seed: int):
    rng = random.Random(seed)
    state = new_game(ruleset)
    moves = []
    for _ in range(limit):
        legal = legal_moves(state)
        if not legal:
            break
        move = rng.choice(legal)
        before = state.turn
        state = apply_move(state, move)
        moves.append({"source": move.source, "target": move.target, "side": before})
        if state.winner:
            break
    return state, moves


def measure_capacity(app, client: TestClient) -> dict:
    """Aggregate preparation, polling and gameplay-under-load measurements."""

    def create_game(seed: int) -> dict:
        state, moves = generated_moves("copenhagen", 160, seed)
        players = {
            side: {"name": side.title(), "kind": "human", "config": {}}
            for side in ("attackers", "defenders")
        }
        game = game_record("copenhagen", "human-human", "defenders", players, seed)
        game["moves"] = moves
        game["cursor"] = len(moves)
        game["state"] = state.to_dict()
        game["version"] = len(moves)
        app.state.store.create_game(game)
        return game

    same = create_game(901)
    same_url = f"/api/games/{same['id']}/analysis-context"
    same_params = {"cursor": same["cursor"], "version": same["version"]}
    before_builds = app.state.analysis_context_builds.snapshot()
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=8) as pool:
        same_responses = list(
            pool.map(lambda _: client.get(same_url, params=same_params), range(8))
        )
    same_ms = (time.perf_counter() - started) * 1000
    assert all(response.status_code == 200 for response in same_responses)
    builds = app.state.analysis_context_builds.snapshot()
    same_reconstructions = builds["reconstructions"] - before_builds["reconstructions"]

    distinct_games = [create_game(1000 + index) for index in range(12)]
    cache_before = len(app.state.analysis_context_cache)
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=6) as pool:
        distinct_responses = list(
            pool.map(
                lambda game: client.get(
                    f"/api/games/{game['id']}/analysis-context",
                    params={"cursor": game["cursor"], "version": game["version"]},
                ),
                distinct_games,
            )
        )
    distinct_ms = (time.perf_counter() - started) * 1000
    distinct_ok = sum(response.status_code == 200 for response in distinct_responses)
    distinct_busy = sum(response.status_code == 503 for response in distinct_responses)
    assert distinct_ok + distinct_busy == len(distinct_responses)

    status_game = create_game(1200)
    started = time.perf_counter()
    for _ in range(50):
        status = client.get(
            f"/api/games/{status_game['id']}/analysis-status",
            params={"cursor": status_game["cursor"], "version": status_game["version"]},
        )
        assert status.status_code == 200, status.text
    status_ms = (time.perf_counter() - started) * 1000 / 50

    blocked = create_game(1300)
    registry = app.state.analysis_context_builds
    leases = [registry.build(("capacity", index, 0)) for index in range(registry.max_concurrent)]
    for lease in leases:
        lease.__enter__()
    try:
        overloaded = client.get(
            f"/api/games/{blocked['id']}/analysis-context",
            params={"cursor": blocked["cursor"], "version": blocked["version"]},
        )
        gameplay = []
        for _ in range(10):
            begin = time.perf_counter()
            response = client.get(f"/api/games/{blocked['id']}")
            gameplay.append((time.perf_counter() - begin) * 1000)
            assert response.status_code == 200
    finally:
        for lease in leases:
            lease.__exit__(None, None, None)
    recovered = client.get(
        f"/api/games/{blocked['id']}/analysis-context",
        params={"cursor": blocked["cursor"], "version": blocked["version"]},
    )
    assert recovered.status_code == 200, recovered.text
    return {
        "same_context_concurrent": {
            "requests": len(same_responses),
            "latency_ms": same_ms,
            "reconstructions": same_reconstructions,
            "leftover_records": registry.snapshot()["records"],
        },
        "distinct_contexts": {
            "requests": len(distinct_responses),
            "ok": distinct_ok,
            "busy": distinct_busy,
            "latency_ms": distinct_ms,
            "cache_entries_before": cache_before,
            "cache_entries_after": len(app.state.analysis_context_cache),
        },
        "status_poll_avg_ms": status_ms,
        "preparation_capacity": {
            "rejected_status": overloaded.status_code,
            "rejected_code": overloaded.json().get("code"),
            "max_concurrent": registry.max_concurrent,
            "gameplay_latency_ms": gameplay,
        },
        "cache_entries_after_load": len(app.state.analysis_context_cache),
        "build_snapshot": registry.snapshot(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    records = []
    with tempfile.TemporaryDirectory(prefix="phase5-context-") as root:
        app = create_app(root)
        with TestClient(app) as client:
            auth = app.state.auth
            auth.create_account("Odin", "odin@example.test", PASSWORD, "ADMIN", bootstrap=True)
            login = client.post(
                "/api/auth/login",
                json={"identity": "Odin", "password": PASSWORD},
                headers={"Origin": "http://testserver"},
            )
            assert login.status_code == 200, login.text
            client.headers.update(
                {
                    "Origin": "http://testserver",
                    "X-CSRF-Token": login.json()["csrf_token"],
                }
            )
            account = auth.session(client.cookies.get(COOKIE))
            token = principal.set(account)
            try:
                for ruleset in ("brandubh", "tablut", "copenhagen"):
                    for target in (0, 20, 80, 160):
                        state, moves = generated_moves(ruleset, target, 17 + target)
                        players = {
                            side: {
                                "name": side.title(),
                                "kind": "human",
                                "config": {},
                            }
                            for side in ("attackers", "defenders")
                        }
                        game = game_record(ruleset, "human-human", "defenders", players, 7)
                        game["moves"] = moves
                        game["cursor"] = len(moves)
                        game["state"] = state.to_dict()
                        game["version"] = len(moves)
                        app.state.store.create_game(game)
                        started = time.perf_counter()
                        response = client.get(
                            f"/api/games/{game['id']}/analysis-context",
                            params={"cursor": len(moves), "version": len(moves)},
                        )
                        elapsed = (time.perf_counter() - started) * 1000
                        assert response.status_code == 200, response.text
                        body = response.json()
                        repeated = []
                        for _ in range(5):
                            begin = time.perf_counter()
                            again = client.get(
                                f"/api/games/{game['id']}/analysis-context",
                                params={"cursor": len(moves), "version": len(moves)},
                            )
                            repeated.append((time.perf_counter() - begin) * 1000)
                            assert again.status_code == 200
                        records.append(
                            {
                                "ruleset": ruleset,
                                "moves": len(moves),
                                "response_bytes": len(response.content),
                                "cold_ms": elapsed,
                                "cached_ms": repeated,
                                "states": len(body["states"]),
                                "history_keys": len(body["history_keys"]),
                                "position_keys": len(body["position_keys"]),
                                "duplicated_history_bytes": sum(
                                    len(state.get("history", [])) for state in body["states"]
                                ),
                            }
                        )
                capacity = measure_capacity(app, client)
            finally:
                principal.reset(token)
    report = {
        "kind": "browser-analysis-context-benchmark",
        "schema": 1,
        "environment": {"python": sys.version.split()[0]},
        "records": records,
        "capacity": capacity,
        "limits": {"max_states": 4096, "cache_entries": 8, "cache_bytes": 24 * 1024 * 1024},
    }
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2)[:2000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
