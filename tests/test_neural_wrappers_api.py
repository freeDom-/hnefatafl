"""Registered neural wrappers survive API normalization and real worker execution."""

from record_fixtures import historical_response
import time

import pytest
from auth_support import AuthenticatedClient as TestClient

from hnefatafl.engine import Move, apply_move, new_game
from hnefatafl.models import ModelRegistry, PolicyValueNet, save_checkpoint
from hnefatafl.server import create_app


@pytest.fixture
def client_and_model(tmp_path):
    checkpoint = save_checkpoint(tmp_path / "source.pt", PolicyValueNet("brandubh", 4, 0), {})
    model = ModelRegistry(tmp_path / "models").register(checkpoint)
    with TestClient(create_app(tmp_path)) as client:
        yield client, model


def opponent(model, wrapper):
    return {
        "profile_id": "neural",
        "config": {
            "model_id": model["id"],
            "iterations": 2,
            "depth": 1,
            "temperature": 0,
            "time_limit": 2,
            **({"search_wrapper": wrapper} if wrapper is not None else {}),
        },
    }


@pytest.mark.parametrize("wrapper", [None, "mcts", "alpha_beta", "none"])
def test_registered_wrappers_drive_seeded_gameplay_and_position_analysis(client_and_model, wrapper):
    client, model = client_and_model
    selected_moves = []
    for _ in range(2):
        response = historical_response(
            client,
            json={
                "ruleset": "brandubh",
                "mode": "human-ai",
                "human_side": "defenders",
                "seed": 91,
                "players": {"attackers": opponent(model, wrapper)},
            },
        )
        assert response.status_code == 200, response.text
        game = response.json()
        config = game["players"]["attackers"]["config"]
        assert config.get("search_wrapper", "mcts") == (wrapper or "mcts")
        analysis = client.post(
            f"/api/games/{game['id']}/analysis", json={"version": 0, "cursor": 0}
        )
        assert analysis.status_code == 200, analysis.text
        report = analysis.json()["evaluations"][0]
        assert report["source"] == ("neural_value" if wrapper == "none" else "neural_search")
        assert report["best_move"] in game["legal_moves"]
        if wrapper == "alpha_beta":
            assert report["completed_depth"] == 1
        elif wrapper != "none":
            assert report["simulations"] == 2
        played = client.post(f"/api/games/{game['id']}/ai-move", json={"version": 0})
        assert played.status_code == 200, played.text
        selected = {key: played.json()["moves"][0][key] for key in ("source", "target")}
        assert selected == report["best_move"]
        selected_moves.append(selected)
    assert selected_moves[0] == selected_moves[1]


def test_tournament_worker_executes_all_neural_wrappers(client_and_model):
    client, model = client_and_model
    response = client.post(
        "/api/tournaments",
        json={
            "name": "Neural wrappers",
            "rulesets": ["brandubh"],
            "players": [opponent(model, wrapper) for wrapper in ("mcts", "alpha_beta", "none")],
            "games": 2,
            "max_plies": 2,
            "seed": 17,
        },
    )
    assert response.status_code == 201, response.text
    tournament = response.json()
    deadline = time.monotonic() + 30
    while tournament["status"] in {"running", "pending"} and time.monotonic() < deadline:
        time.sleep(0.05)
        tournament = client.get(f"/api/tournaments/{tournament['id']}").json()
    assert tournament["status"] == "completed", tournament
    assert [
        player["config"].get("search_wrapper", "mcts") for player in tournament["config"]["players"]
    ] == ["mcts", "alpha_beta", "none"]
    assert tournament["progress"]["completed_games"] == 6
    for game in tournament["result"]["games"]:
        state = new_game("brandubh")
        assert len(game["moves"]) == 2
        for move in game["moves"]:
            state = apply_move(state, Move(**move))
        assert state.to_dict() == game["state"]


@pytest.mark.parametrize("wrapper", ["invalid", None, True, [], {}])
def test_wrapper_validation_rejects_invalid_values(client_and_model, wrapper):
    client, model = client_and_model
    game = client.post("/api/games", json={"ruleset": "brandubh"}).json()
    player = opponent(model, "mcts")
    player["config"]["search_wrapper"] = wrapper
    response = client.put(f"/api/games/{game['id']}/analytics", json={"advisor": player})
    assert response.status_code == 400, response.text
    assert "Search wrapper" in response.json()["detail"]


def test_wrappers_require_a_compatible_registered_neural_model(client_and_model):
    client, model = client_and_model
    game = client.post("/api/games", json={"ruleset": "tablut"}).json()
    for wrapper in ("mcts", "alpha_beta", "none"):
        incompatible = client.put(
            f"/api/games/{game['id']}/analytics", json={"advisor": opponent(model, wrapper)}
        )
        assert incompatible.status_code == 400, incompatible.text
    non_neural = client.put(
        f"/api/games/{game['id']}/analytics",
        json={"advisor": {"config": {"algorithm": "baseline", "search_wrapper": "none"}}},
    )
    assert non_neural.status_code == 400, non_neural.text
