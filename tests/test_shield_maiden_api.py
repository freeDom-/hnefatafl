"""Shared Shield maiden identities through game, analysis and tournament APIs."""

import time

import pytest
from fastapi.testclient import TestClient

from hnefatafl.ai import choose_move
from hnefatafl.engine import State
from hnefatafl.server import create_app


@pytest.mark.parametrize("ruleset", ["brandubh", "tablut", "copenhagen"])
def test_profile_custom_analysis_and_stats_use_distinct_effective_identity(tmp_path, ruleset):
    with TestClient(create_app(tmp_path)) as client:
        profiles = client.get("/api/ai/profiles").json()
        maiden = next(p for p in profiles if p["id"] == "shield_maiden")
        assert maiden["name"] == "Shield maiden" and maiden["difficulty"] == "Intermediate"
        created = client.post(
            "/api/games",
            json={
                "ruleset": ruleset,
                "mode": "ai-ai",
                "seed": 41,
                "players": {
                    "attackers": {"profile_id": "shield_maiden"},
                    "defenders": {"profile_id": "baseline"},
                },
            },
        )
        assert created.status_code == 201, created.text
        game = created.json()
        assert game["players"]["attackers"]["config"] == {
            "algorithm": "shield_maiden",
            "heuristic_version": 1,
            "evaluation": "heuristic",
            "time_limit": 1.0,
            "seed": 41,
        }
        assert (
            game["players"]["attackers"]["variant_id"] != game["players"]["defenders"]["variant_id"]
        )
        state = State.from_dict(game)
        if state.turn == "attackers":
            expected = choose_move(state, game["players"]["attackers"]["config"])
            response = client.post(
                f"/api/games/{game['id']}/ai-move", json={"version": game["version"]}
            )
            assert response.status_code == 200, response.text
            move = response.json()["moves"][-1]
            assert (move["source"], move["target"]) == (expected.source, expected.target)
        custom = client.post(
            "/api/games",
            json={
                "ruleset": ruleset,
                "mode": "ai-ai",
                "players": {
                    "attackers": {"config": {"algorithm": "shield_maiden", "time_limit": 0.03}}
                },
            },
        )
        assert custom.status_code == 201, custom.text
        assert custom.json()["players"]["attackers"]["config"]["time_limit"] == 0.03
        # Statistics include completed records only. Finish the real API game.
        game = client.get(f"/api/games/{game['id']}").json()
        for _ in range(300):
            if game["winner"]:
                break
            response = client.post(
                f"/api/games/{game['id']}/ai-move", json={"version": game["version"]}
            )
            assert response.status_code == 200, response.text
            game = response.json()
        assert game["winner"]
        stats = client.get("/api/stats").json()
        assert any(
            row.get("config", {}).get("algorithm") == "shield_maiden" for row in stats["by_player"]
        )
        assert any(
            row.get("config", {}).get("algorithm") == "baseline" for row in stats["by_player"]
        )
        invalid = client.post(
            "/api/games",
            json={
                "mode": "ai-ai",
                "players": {
                    "attackers": {"config": {"algorithm": "shield_maiden", "heuristic_version": 2}}
                },
            },
        )
        assert invalid.status_code == 400


def test_tournament_snapshots_and_runs_shield_maiden(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        response = client.post(
            "/api/tournaments",
            json={
                "name": "Maiden witness",
                "rulesets": ["brandubh"],
                "games": 2,
                "max_plies": 4,
                "players": [{"profile_id": "shield_maiden"}, {"profile_id": "baseline"}],
            },
        )
        assert response.status_code == 201, response.text
        tournament = response.json()
        assert tournament["config"]["players"][0]["name"] == "Shield maiden"
        assert tournament["config"]["players"][0]["config"]["heuristic_version"] == 1
        deadline = time.monotonic() + 20
        while tournament["status"] in ("pending", "running") and time.monotonic() < deadline:
            time.sleep(0.02)
            tournament = client.get(f"/api/tournaments/{tournament['id']}").json()
        assert tournament["status"] == "completed", tournament
        assert len(tournament["result"]["games"]) == 2
