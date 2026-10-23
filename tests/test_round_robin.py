"""Round-robin participation, score allocation, durable workers and legacy replay."""

from collections import Counter
from copy import deepcopy
import time

import pytest
from auth_support import AuthenticatedClient as TestClient

from hnefatafl.ai import tournament
from hnefatafl.engine import Move, apply_move, new_game
from hnefatafl.server import create_app


def configuration(player_count=4, **changes):
    return {
        "name": "Four minds at the king's table",
        "players": [
            {
                "name": f"Raven {index + 1}",
                "config": {"algorithm": "baseline", "time_limit": 0.01},
            }
            for index in range(player_count)
        ],
        "rulesets": ["brandubh"],
        "games": 2,
        "max_plies": 2,
        "seed": 13,
        **changes,
    }


def wait_for_completion(client, identifier):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        response = client.get(f"/api/tournaments/{identifier}")
        assert response.status_code == 200, response.text
        record = response.json()
        if record["status"] == "completed":
            return record
        assert record["status"] == "running", record
        time.sleep(0.03)
    pytest.fail("Round-robin worker did not finish its bounded games")


@pytest.mark.parametrize("player_count", [3, 4])
@pytest.mark.parametrize("rulesets", [["brandubh"], ["brandubh", "tablut"]])
def test_real_worker_balances_every_pair_and_retains_replay_after_restart(
    tmp_path, player_count, rulesets
):
    config = configuration(player_count, rulesets=rulesets)
    total = player_count * (player_count - 1) * len(rulesets)
    per_player = 2 * (player_count - 1) * len(rulesets)
    with TestClient(create_app(tmp_path)) as client:
        response = client.post("/api/tournaments", json=config)
        assert response.status_code == 201, response.text
        started = response.json()
        assert started["progress"]["total_games"] == total
        assert started["result"]["scores"] == [0.0] * player_count
        assert len(started["result"]["standings"]) == player_count
        saved = wait_for_completion(client, started["id"])
        assert saved["progress"] == {"completed_games": total, "total_games": total}
        result = saved["result"]
        assert len(result["games"]) == total
        assert result["scores"] == [0.0] * player_count
        assert result["truncated"] == total
        assert all(
            row["games"] == row["truncated"] == per_player
            and row["wins"] == row["losses"] == row["draws"] == 0
            for row in result["standings"]
        )
        ordered_pairs = Counter(
            (game["ruleset"], game["attacker_player"], game["defender_player"])
            for game in result["games"]
        )
        assert len(ordered_pairs) == total
        assert set(ordered_pairs.values()) == {1}
        for ruleset in rulesets:
            assert result["by_ruleset"][ruleset] == {
                "wins": [0] * player_count,
                "draws": 0,
                "truncated": total // len(rulesets),
                "games": total // len(rulesets),
            }
            for first in range(player_count):
                for second in range(first + 1, player_count):
                    assert ordered_pairs[ruleset, first, second] == 1
                    assert ordered_pairs[ruleset, second, first] == 1
        for game in result["games"]:
            state = new_game(game["ruleset"])
            for move in game["moves"]:
                state = apply_move(state, Move(**move))
            assert state.to_dict() == game["state"]
    with TestClient(create_app(tmp_path)) as restarted:
        response = restarted.get(f"/api/tournaments/{saved['id']}")
        assert response.status_code == 200 and response.json() == saved
        replay = restarted.get(f"/api/tournaments/{saved['id']}/games/{total - 1}/replay")
        assert replay.status_code == 200 and replay.json()["validated"]
        assert replay.json()["states"][-1] == saved["result"]["games"][-1]["state"]


def test_only_match_participants_receive_wins_draws_losses_or_unfinished_counts(monkeypatch):
    outcomes = iter(["attackers", "draw", "defenders", None, "attackers", "defenders"])

    def play(_ruleset, _attackers, _defenders, **_options):
        winner = next(outcomes)
        return {
            "winner": winner,
            "outcome": "completed" if winner else "truncated",
            "plies": 1,
            "moves": [],
            "total_decision_time": 0.01,
        }

    monkeypatch.setattr("hnefatafl.ai.play_game", play)
    result = tournament({"players": [{}, {}, {}], "rulesets": ["brandubh"], "games": 2})
    assert result["scores"] == [1.5, 2.5, 1.0]
    assert result["standings"] == [
        dict(player=0, wins=1, losses=1, draws=1, truncated=1, games=4, score=1.5),
        dict(player=1, wins=2, losses=1, draws=1, truncated=0, games=4, score=2.5),
        dict(player=2, wins=1, losses=2, draws=0, truncated=1, games=4, score=1.0),
    ]
    assert result["by_ruleset"]["brandubh"] == {
        "wins": [1, 2, 1],
        "draws": 1,
        "truncated": 1,
        "games": 6,
    }


def test_round_robin_partial_report_leaves_nonparticipants_untouched():
    reports = []

    def report(result):
        reports.append(deepcopy(result))

    result = tournament(
        {
            "players": [{"algorithm": "baseline", "time_limit": 0.01}] * 4,
            "rulesets": ["brandubh"],
            "games": 2,
            "max_plies": 1,
        },
        report=report,
        should_stop=lambda: bool(reports),
    )
    assert len(reports) == len(result["games"]) == 1
    assert result["scores"] == [0.0] * 4
    assert [row["games"] for row in result["standings"]] == [1, 1, 0, 0]
    assert [row["truncated"] for row in result["standings"]] == [1, 1, 0, 0]


@pytest.mark.parametrize("count", [0, 1, 5])
def test_round_robin_rejects_unsupported_player_counts(count):
    with pytest.raises(ValueError, match="two and four"):
        tournament({"players": [{}] * count})


def test_api_rejects_five_valid_opponents_without_retaining_a_tournament(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        response = client.post("/api/tournaments", json=configuration(5))
        assert response.status_code == 422
        assert client.get("/api/tournaments").json() == []


def test_legacy_two_player_match_without_defender_field_still_replays(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        response = client.post("/api/tournaments", json=configuration(2))
        assert response.status_code == 201
        saved = wait_for_completion(client, response.json()["id"])
        record = client.app.state.store.get_tournament(saved["id"])
        for match in record["result"]["games"]:
            match.pop("defender_player")
        client.app.state.store.update_tournament(saved["id"], {"result": record["result"]})
    with TestClient(create_app(tmp_path)) as restarted:
        replay = restarted.get(f"/api/tournaments/{saved['id']}/games/1/replay")
        assert replay.status_code == 200 and replay.json()["validated"]
        assert replay.json()["states"][-1] == record["result"]["games"][1]["state"]


def test_fourth_opponent_model_is_protected_until_tournament_stops(tmp_path):
    from hnefatafl.models import ModelRegistry, PolicyValueNet, save_checkpoint

    with TestClient(create_app(tmp_path)) as client:
        checkpoint = save_checkpoint(
            tmp_path / "original.pt", PolicyValueNet("brandubh", 4, 0), {"name": "Fourth raven"}
        )
        model = ModelRegistry(tmp_path / "models").register(checkpoint)
        config = configuration(games=100, max_plies=1000)
        config["players"][3] = {
            "name": "Fourth raven",
            "profile_id": "neural",
            "config": {"model_id": model["id"], "iterations": 1, "time_limit": 0.01},
        }
        response = client.post("/api/tournaments", json=config)
        assert response.status_code == 201, response.text
        started = response.json()
        assert started["progress"]["total_games"] == 600
        assert client.delete(f"/api/models/{model['id']}").status_code == 409
        assert (
            client.delete(f"/api/models/{model['id']}?discard_unfinished=true").status_code == 409
        )
        stopped = client.post(f"/api/tournaments/{started['id']}/cancel")
        assert stopped.status_code == 200 and stopped.json()["status"] == "cancelled"
        assert len(stopped.json()["result"]["scores"]) == 4
        assert client.delete(f"/api/models/{model['id']}").status_code == 200
        assert checkpoint.exists()
