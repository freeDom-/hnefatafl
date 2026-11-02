"""Administrator-configured custom single-player opponents.

A custom opponent reuses the tournament participant configuration and
validation verbatim, so these tests cover authorization, validation parity,
actual execution, persistence and restart reproduction.
"""

import pytest
from fastapi.testclient import TestClient
from auth_support import AuthenticatedClient

from hnefatafl.server import create_app

PASSWORD = "custom-opponent-password-123"
ORIGIN = {"Origin": "http://testserver"}


@pytest.fixture
def application(tmp_path):
    return create_app(tmp_path)


@pytest.fixture
def admin(application):
    with AuthenticatedClient(application) as client:
        yield client


@pytest.fixture
def user(application):
    application.state.auth.create_account("Raven", "raven@example.test", PASSWORD, "USER")
    with TestClient(application) as client:
        response = client.post(
            "/api/auth/login",
            json={"identity": "Raven", "password": PASSWORD},
            headers=ORIGIN,
        )
        assert response.status_code == 200, response.text
        client.headers.update({**ORIGIN, "X-CSRF-Token": response.json()["csrf_token"]})
        yield client


def custom(client, *, human_side="defenders", players=None, **changes):
    """Create a single-player game with a custom opponent on the requested side."""
    side = "defenders" if human_side == "attackers" else "attackers"
    body = {
        "ruleset": "brandubh",
        "mode": "human-ai",
        "human_side": human_side,
        "players": players or {side: {"config": {"algorithm": "baseline", "time_limit": 0.2}}},
        **changes,
    }
    return client.post("/api/games", json=body)


def register_model(client, tmp_path, name="Hugin", ruleset="brandubh"):
    from hnefatafl.models import ModelRegistry, PolicyValueNet, save_checkpoint

    original = save_checkpoint(
        tmp_path / f"{name}.pt", PolicyValueNet(ruleset, 4, 0), {"name": name}
    )
    return ModelRegistry(client.app.state.data_dir / "models").register(original)


def test_admin_can_create_a_custom_opponent_and_the_configured_agent_moves(admin):
    response = custom(admin)
    assert response.status_code == 201, response.text
    game = response.json()
    opponent = game["players"]["attackers"]
    assert opponent["kind"] == "ai"
    assert opponent["config"]["algorithm"] == "baseline"
    assert opponent["config"]["time_limit"] == 0.2

    moved = admin.post(f"/api/games/{game['id']}/ai-move", json={"version": game["version"]})
    assert moved.status_code == 200, moved.text
    assert moved.json()["moves"], "the configured opponent must produce a move"


def test_custom_alpha_beta_opponent_honours_its_search_settings(admin):
    response = custom(
        admin,
        players={
            "attackers": {"config": {"algorithm": "alpha_beta", "depth": 3, "time_limit": 0.5}}
        },
    )
    assert response.status_code == 201, response.text
    config = response.json()["players"]["attackers"]["config"]
    assert config["algorithm"] == "alpha_beta"
    assert config["depth"] == 3


@pytest.mark.parametrize("human_side", ["defenders", "attackers"])
def test_custom_opponent_plays_either_side(admin, human_side):
    opponent_side = "defenders" if human_side == "attackers" else "attackers"
    response = custom(admin, human_side=human_side)
    assert response.status_code == 201, response.text
    game = response.json()
    assert game["players"][opponent_side]["kind"] == "ai"
    assert game["players"][human_side]["kind"] == "human"

    if human_side == "attackers":
        # The human opens so the configured defender can answer.
        move = game["legal_moves"][0]
        played = admin.post(
            f"/api/games/{game['id']}/moves",
            json={"version": game["version"], **move},
        )
        assert played.status_code == 200, played.text
        game = played.json()
    moved = admin.post(f"/api/games/{game['id']}/ai-move", json={"version": game["version"]})
    assert moved.status_code == 200, moved.text
    assert moved.json()["turn"] == human_side


def test_non_administrator_cannot_create_a_custom_opponent(user):
    response = custom(user)
    assert response.status_code == 403, response.text
    assert "administrator" in response.json()["detail"].lower()
    assert user.get("/api/games").json() == []


def test_preset_creation_remains_available_to_every_player(user):
    response = user.post(
        "/api/games",
        json={
            "ruleset": "brandubh",
            "mode": "human-ai",
            "players": {"attackers": {"profile_id": "mcts"}},
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["players"]["attackers"]["config"]["algorithm"] == "mcts"


@pytest.mark.parametrize(
    "config",
    [
        {"algorithm": "baseline", "unknown_field": 1},
        {"algorithm": "alpha_beta", "depth": 9},
        {"algorithm": "mcts", "iterations": 0},
        {"algorithm": "mcts", "time_limit": 99},
        {"algorithm": "mystery"},
        {"algorithm": "mcts", "search_wrapper": "mcts"},
        {"algorithm": "neural", "search_wrapper": "mcts"},
        {"algorithm": "neural", "model_id": "not-a-registered-model"},
    ],
)
def test_invalid_custom_configurations_are_rejected_with_a_message(admin, config):
    response = custom(admin, players={"attackers": {"config": config}})
    assert response.status_code in (400, 404), response.text
    detail = response.json()["detail"]
    assert isinstance(detail, str) and detail


def test_unknown_profile_id_is_rejected_cleanly(admin):
    response = custom(admin, players={"attackers": {"profile_id": "not_a_profile"}})
    assert response.status_code == 400, response.text
    assert "profile" in response.json()["detail"].lower()


def test_custom_neural_opponent_uses_its_registered_model(admin, tmp_path):
    model = register_model(admin, tmp_path)
    response = custom(
        admin,
        players={
            "attackers": {
                "config": {
                    "algorithm": "neural",
                    "model_id": model["id"],
                    "search_wrapper": "none",
                    "time_limit": 1,
                }
            }
        },
    )
    assert response.status_code == 201, response.text
    game = response.json()
    assert game["players"]["attackers"]["config"]["model_id"] == model["id"]
    assert game["players"]["attackers"]["model_name"] == model["name"]
    moved = admin.post(f"/api/games/{game['id']}/ai-move", json={"version": game["version"]})
    assert moved.status_code == 200, moved.text


def test_model_for_another_ruleset_is_rejected(admin, tmp_path):
    model = register_model(admin, tmp_path, name="WrongBoard", ruleset="tablut")
    response = custom(
        admin,
        players={
            "attackers": {
                "config": {
                    "algorithm": "neural",
                    "model_id": model["id"],
                    "search_wrapper": "none",
                    "time_limit": 1,
                }
            }
        },
    )
    assert response.status_code == 400, response.text
    assert "battlefield" in response.json()["detail"].lower()


def test_custom_opponent_configuration_survives_reload_and_restart(admin):
    response = custom(
        admin,
        players={
            "attackers": {
                "config": {
                    "algorithm": "alpha_beta",
                    "depth": 4,
                    "time_limit": 0.5,
                    "heuristic": "shield_bearer",
                }
            }
        },
    )
    assert response.status_code == 201, response.text
    game = response.json()
    reloaded = admin.get(f"/api/games/{game['id']}").json()
    assert reloaded["players"]["attackers"]["config"] == game["players"]["attackers"]["config"]

    restarted = admin.post(f"/api/games/{game['id']}/restart", json={"version": game["version"]})
    assert restarted.status_code == 200, restarted.text
    replacement = restarted.json()
    assert replacement["id"] != game["id"]
    config = replacement["players"]["attackers"]["config"]
    assert config["algorithm"] == "alpha_beta"
    assert config["depth"] == 4
    assert config["heuristic"] == "shield_bearer"


def test_unavailable_model_is_reported_without_breaking_the_game(admin, tmp_path):
    from hnefatafl.models import ModelRegistry

    model = register_model(admin, tmp_path)
    response = custom(
        admin,
        players={
            "attackers": {
                "config": {
                    "algorithm": "neural",
                    "model_id": model["id"],
                    "search_wrapper": "none",
                    "time_limit": 1,
                }
            }
        },
    )
    assert response.status_code == 201, response.text
    game = response.json()
    # The delete API refuses to strand the unfinished game on a missing model.
    refused = admin.delete(f"/api/models/{model['id']}")
    assert refused.status_code == 409, refused.text
    # So remove the registry entry directly to model a restored backup or
    # manual cleanup, then confirm the game fails gracefully.
    ModelRegistry(admin.app.state.data_dir / "models").delete(model["id"])

    moved = admin.post(f"/api/games/{game['id']}/ai-move", json={"version": game["version"]})
    assert moved.status_code in (400, 404, 409), moved.text
    assert "model" in moved.json()["detail"].lower()
    # The record stays readable and unchanged rather than being left mid-move.
    reloaded = admin.get(f"/api/games/{game['id']}").json()
    assert reloaded["version"] == game["version"]
    assert reloaded["moves"] == []


def test_custom_configuration_is_hidden_from_the_game_response(admin):
    response = custom(admin)
    assert response.status_code == 201, response.text
    assert "creation" not in response.json()


def test_a_custom_opponent_game_completes_and_is_counted(admin):
    response = custom(admin)
    assert response.status_code == 201, response.text
    game = response.json()
    for _ in range(400):
        if game["winner"]:
            break
        if game["turn"] == "attackers":
            played = admin.post(
                f"/api/games/{game['id']}/ai-move", json={"version": game["version"]}
            )
        else:
            played = admin.post(
                f"/api/games/{game['id']}/moves",
                json={"version": game["version"], **game["legal_moves"][0]},
            )
        assert played.status_code == 200, played.text
        game = played.json()
    assert game["winner"], "the configured opponent should reach a rule outcome"
    assert game["recorded_winner"] == game["winner"]
    assert game["ended_at"]
    stats = admin.get("/api/stats?ruleset=brandubh&mode=human-ai").json()
    assert stats["total_games"] >= 1
    assert stats["completed_games"] >= 1


def test_custom_neural_alpha_beta_opponent_uses_its_search_settings(admin, tmp_path):
    model = register_model(admin, tmp_path)
    search = {
        "legacy": False,
        "transposition": True,
        "move_ordering": False,
        "symmetry_transposition": False,
        "symmetry_pruning": False,
        "table_capacity": 4096,
    }
    response = custom(
        admin,
        players={
            "attackers": {
                "config": {
                    "algorithm": "neural",
                    "model_id": model["id"],
                    "search_wrapper": "alpha_beta",
                    "depth": 2,
                    "time_limit": 2,
                    "search": search,
                }
            }
        },
    )
    assert response.status_code == 201, response.text
    game = response.json()
    config = game["players"]["attackers"]["config"]
    assert config["search_wrapper"] == "alpha_beta"
    assert config["depth"] == 2
    assert config["search"] == search

    # The alpha-beta wrapper really runs with the model rather than a policy-only
    # forward pass, and it reports its own completed search depth.
    analysis = admin.post(f"/api/games/{game['id']}/analysis", json={"version": 0, "cursor": 0})
    assert analysis.status_code == 200, analysis.text
    report = analysis.json()["evaluations"][0]
    assert report["source"] == "neural_search"
    assert report.get("completed_depth", 0) >= 1

    moved = admin.post(f"/api/games/{game['id']}/ai-move", json={"version": game["version"]})
    assert moved.status_code == 200, moved.text


@pytest.mark.parametrize(
    "search",
    [
        {"symmetry_transposition": True},
        {"symmetry_pruning": True},
    ],
)
def test_model_backed_symmetry_search_is_rejected(admin, tmp_path, search):
    model = register_model(admin, tmp_path)
    response = custom(
        admin,
        players={
            "attackers": {
                "config": {
                    "algorithm": "neural",
                    "model_id": model["id"],
                    "search_wrapper": "alpha_beta",
                    "time_limit": 1,
                    "search": search,
                }
            }
        },
    )
    assert response.status_code == 400, response.text
    assert "symmetry" in response.json()["detail"].lower()


def test_search_settings_are_rejected_for_a_wrapper_that_ignores_them(admin):
    response = custom(
        admin,
        players={
            "attackers": {
                "config": {
                    "algorithm": "mcts",
                    "time_limit": 1,
                    "search": {"transposition": True},
                }
            }
        },
    )
    assert response.status_code == 400, response.text
    assert "alpha-beta" in response.json()["detail"].lower()


def test_custom_alpha_beta_opponent_keeps_its_own_symmetry_settings(admin):
    search = {
        "legacy": False,
        "transposition": True,
        "move_ordering": True,
        "symmetry_transposition": True,
        "symmetry_pruning": True,
        "table_capacity": 2048,
    }
    response = custom(
        admin,
        players={
            "attackers": {
                "config": {
                    "algorithm": "alpha_beta",
                    "depth": 2,
                    "time_limit": 1,
                    "search": search,
                }
            }
        },
    )
    assert response.status_code == 201, response.text
    config = response.json()["players"]["attackers"]["config"]
    assert config["search"] == search
    assert config["evaluation"] == "heuristic"
