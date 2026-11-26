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
            finally:
                principal.reset(token)
    report = {
        "kind": "browser-analysis-context-benchmark",
        "schema": 1,
        "environment": {"python": sys.version.split()[0]},
        "records": records,
        "limits": {"max_states": 4096, "cache_entries": 8, "cache_bytes": 24 * 1024 * 1024},
    }
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2)[:2000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
