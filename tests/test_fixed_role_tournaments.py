"""Fixed-role comparisons: one shared search, two fixed sides, distinct seats."""

import json
import time
from copy import deepcopy

import pytest
from auth_support import AuthenticatedClient as TestClient

from hnefatafl.ai import tournament, validate_fixed_role_players
from hnefatafl.engine import Move, apply_move, new_game
from hnefatafl.models import ModelRegistry, PolicyValueNet, save_checkpoint
from hnefatafl.server import create_app


def model_player(model_id, **changes):
    """One neural seat; both seats receive the identical shared search settings."""
    return {
        "profile_id": "neural",
        "config": {"model_id": model_id, "iterations": 1, "time_limit": 0.01, **changes},
    }


def fixed_role_configuration(models, **changes):
    return {
        "name": "Model side performance",
        "mode": "fixed_roles",
        "rulesets": ["brandubh"],
        "players": [model_player(models[0]["id"]), model_player(models[1]["id"])],
        "games": 3,
        "max_plies": 2,
        "seed": 42,
        **changes,
    }


@pytest.fixture
def models(tmp_path):
    registry = ModelRegistry(tmp_path / "models")
    registered = []
    for index, name in enumerate(("Eir", "Freyja")):
        checkpoint = save_checkpoint(
            tmp_path / f"training-{index}.pt",
            PolicyValueNet("brandubh", 4, 0),
            {"name": name},
        )
        registered.append(registry.register(checkpoint))
    return registered


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as session:
        yield session


def launch(client, config):
    response = client.post("/api/tournaments", json=config)
    assert response.status_code == 201, response.text
    return response.json()


def wait_for(client, identifier, predicate, *, timeout=60):
    deadline = time.monotonic() + timeout
    record = None
    while time.monotonic() < deadline:
        response = client.get(f"/api/tournaments/{identifier}")
        assert response.status_code == 200, response.text
        record = response.json()
        if predicate(record):
            return record
        assert record["status"] not in {"failed", "cancelled"}, record
        time.sleep(0.03)
    pytest.fail(f"Fixed-role tournament did not reach the requested state: {record}")


def test_fixed_role_tournament_keeps_sides_and_reports_odd_game_totals(client, models):
    created = launch(client, fixed_role_configuration(models, games=3))
    assert created["mode"] == "fixed_roles"
    assert created["config"]["mode"] == "fixed_roles"
    assert created["progress"] == {"completed_games": 0, "total_games": 3}
    assert [player["name"] for player in created["config"]["players"]] == ["Eir", "Freyja"]
    completed = wait_for(client, created["id"], lambda row: row["status"] == "completed")
    assert completed["progress"] == {"completed_games": 3, "total_games": 3}
    result = completed["result"]
    # Seat 0 attacks and seat 1 defends in every scheduled game.
    assert [game["attacker_player"] for game in result["games"]] == [0, 0, 0]
    assert [game["defender_player"] for game in result["games"]] == [1, 1, 1]
    assert [row["games"] for row in result["standings"]] == [3, 3]
    assert result["by_ruleset"]["brandubh"]["games"] == 3
    assert completed["ended_at"]
    for index, match in enumerate(result["games"]):
        state = new_game(match["ruleset"])
        for move in match["moves"]:
            state = apply_move(state, Move(**move))
        assert state.to_dict() == match["state"]
        replay = client.get(f"/api/tournaments/{completed['id']}/games/{index}/replay")
        assert replay.status_code == 200 and replay.json()["validated"]
        assert replay.json()["states"][-1] == match["state"]
    # A single game is a valid fixed-role count; the seat assignment never changes.
    single = launch(client, fixed_role_configuration(models, games=1))
    assert single["progress"]["total_games"] == 1
    single = wait_for(client, single["id"], lambda row: row["status"] == "completed")
    assert single["progress"] == {"completed_games": 1, "total_games": 1}
    assert single["result"]["games"][0]["attacker_player"] == 0


def test_same_model_in_both_seats_keeps_distinct_role_statistics(client, models):
    shared = model_player(models[0]["id"])
    created = launch(
        client,
        fixed_role_configuration(models, players=[shared, deepcopy(shared)], games=3),
    )
    completed = wait_for(client, created["id"], lambda row: row["status"] == "completed")
    players = completed["config"]["players"]
    assert players[0]["config"]["model_id"] == players[1]["config"]["model_id"]
    assert players[0]["variant_id"] == players[1]["variant_id"]
    result = completed["result"]
    # Identical model identities never merge the two seats.
    assert [row["player"] for row in result["standings"]] == [0, 1]
    assert [row["games"] for row in result["standings"]] == [3, 3]
    assert len(result["scores"]) == 2
    attacker_wins = sum(game["winner"] == "attackers" for game in result["games"])
    defender_wins = sum(game["winner"] == "defenders" for game in result["games"])
    draws = sum(game["winner"] == "draw" for game in result["games"])
    assert result["scores"][0] == attacker_wins + draws / 2
    assert result["scores"][1] == defender_wins + draws / 2


def test_fixed_role_rejects_mismatched_effective_search_settings(client, models):
    for change in (
        {"iterations": 2},
        {"time_limit": 0.02},
        {"search_wrapper": "none"},
        {"temperature": 0.5},
    ):
        config = fixed_role_configuration(models)
        config["players"][1]["config"].update(change)
        response = client.post("/api/tournaments", json=config)
        assert response.status_code == 400, response.text
        assert "identical search settings" in response.json()["detail"]
        assert client.get("/api/tournaments").json() == []


def test_fixed_role_rejects_unsupported_participants_battlefields_and_counts(client, models):
    baseline = {"config": {"algorithm": "baseline", "time_limit": 0.01}}
    rejected = [
        fixed_role_configuration(models, players=[model_player(models[0]["id"]), baseline]),
        fixed_role_configuration(models, players=[model_player(models[0]["id"])]),
        fixed_role_configuration(
            models,
            players=[model_player(models[0]["id"]), model_player(models[1]["id"]), baseline],
        ),
        fixed_role_configuration(models, rulesets=["brandubh", "tablut"]),
        fixed_role_configuration(models, rulesets=[]),
        fixed_role_configuration(models, games=0),
        fixed_role_configuration(models, games=101),
        fixed_role_configuration(models, games=2.5),
        fixed_role_configuration(models, max_plies=0),
        fixed_role_configuration(models, seed=-1),
        {**fixed_role_configuration(models), "mode": "unknown"},
    ]
    for config in rejected:
        response = client.post("/api/tournaments", json=config)
        assert response.status_code in {400, 422}, response.text
        assert client.get("/api/tournaments").json() == []


def test_fixed_role_rejects_models_trained_for_another_battlefield(client, models, tmp_path):
    checkpoint = save_checkpoint(
        tmp_path / "tablut.pt", PolicyValueNet("tablut", 4, 0), {"name": "Tablut raven"}
    )
    other = ModelRegistry(tmp_path / "models").register(checkpoint)
    config = fixed_role_configuration(models)
    config["players"][1] = model_player(other["id"])
    response = client.post("/api/tournaments", json=config)
    assert response.status_code == 400, response.text
    assert client.get("/api/tournaments").json() == []


def test_fixed_role_cancellation_keeps_partial_results(client, models):
    config = fixed_role_configuration(models, games=100, max_plies=1000)
    created = launch(client, config)
    assert created["progress"]["total_games"] == 100
    partial = wait_for(client, created["id"], lambda row: row["progress"]["completed_games"] >= 1)
    assert partial["status"] == "running"
    stopped = client.post(f"/api/tournaments/{created['id']}/cancel")
    assert stopped.status_code == 200, stopped.text
    cancelled = stopped.json()
    assert cancelled["status"] == "cancelled"
    assert 1 <= cancelled["progress"]["completed_games"] < 100
    assert len(cancelled["result"]["games"]) == cancelled["progress"]["completed_games"]
    assert all(game["attacker_player"] == 0 for game in cancelled["result"]["games"])
    assert all(game["defender_player"] == 1 for game in cancelled["result"]["games"])
    retained = client.get(f"/api/tournaments/{created['id']}").json()
    assert retained == cancelled
    assert client.delete(f"/api/tournaments/{created['id']}").status_code == 200


def test_fixed_role_export_preserves_mode_seats_and_shared_configuration(client, models):
    created = launch(client, fixed_role_configuration(models, games=3))
    completed = wait_for(client, created["id"], lambda row: row["status"] == "completed")
    document = client.get(f"/api/tournaments/{completed['id']}/export").json()
    assert document["mode"] == "fixed_roles"
    assert document["rulesets"] == ["brandubh"]
    assert [player["config"]["model_id"] for player in document["players"]] == [
        models[0]["id"],
        models[1]["id"],
    ]
    attacker, defender = (player["config"] for player in document["players"])
    assert {key: value for key, value in attacker.items() if key != "model_id"} == {
        key: value for key, value in defender.items() if key != "model_id"
    }
    assert document["results"]["result"]["games"]
    # The exported document round-trips as a fresh, valid fixed-role request.
    body = {
        key: document[key]
        for key in ("name", "mode", "rulesets", "players", "games", "max_plies", "seed")
    }
    recreated = client.post("/api/tournaments", json=body)
    assert recreated.status_code == 201, recreated.text
    recreated_record = recreated.json()
    assert recreated_record["mode"] == "fixed_roles"
    assert [player["config"]["model_id"] for player in recreated_record["config"]["players"]] == [
        models[0]["id"],
        models[1]["id"],
    ]
    assert client.post(f"/api/tournaments/{recreated_record['id']}/cancel").status_code == 200


def test_fixed_role_summary_identifies_both_models_without_match_history(client, models):
    created = launch(client, fixed_role_configuration(models, games=3))
    summaries = client.get("/api/tournaments?summary=true").json()
    summary = next(row for row in summaries if row["id"] == created["id"])
    assert summary["summary"] is True
    assert summary["mode"] == "fixed_roles"
    assert "result" not in summary
    assert summary["progress"] == {"completed_games": 0, "total_games": 3}
    assert [player["name"] for player in summary["config"]["players"]] == ["Eir", "Freyja"]
    assert [player["config"]["model_id"] for player in summary["config"]["players"]] == [
        models[0]["id"],
        models[1]["id"],
    ]
    assert all(player["agent_rune"] for player in summary["config"]["players"])
    wait_for(client, created["id"], lambda row: row["status"] == "completed")
    # The full detail still exposes the complete saved record.
    detail = client.get(f"/api/tournaments/{created['id']}").json()
    assert len(detail["result"]["games"]) == 3


def test_round_robin_mode_defaults_and_even_game_requirement_still_hold(client):
    # Missing mode keeps the historical balanced round robin.
    response = client.post(
        "/api/tournaments",
        json={
            "name": "Balanced",
            "rulesets": ["brandubh"],
            "players": [
                {"config": {"algorithm": "baseline", "time_limit": 0.01}},
                {"config": {"algorithm": "baseline", "time_limit": 0.01}},
            ],
            "games": 2,
            "max_plies": 2,
            "seed": 3,
        },
    )
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["mode"] == "round_robin"
    assert created["config"]["mode"] == "round_robin"
    assert created["progress"]["total_games"] == 2
    assert client.post(f"/api/tournaments/{created['id']}/cancel").status_code == 200
    odd = client.post(
        "/api/tournaments",
        json={
            "name": "Odd",
            "rulesets": ["brandubh"],
            "players": [
                {"config": {"algorithm": "baseline", "time_limit": 0.01}},
                {"config": {"algorithm": "baseline", "time_limit": 0.01}},
            ],
            "games": 3,
            "max_plies": 2,
            "seed": 3,
        },
    )
    assert odd.status_code == 400, odd.text
    assert "even number of games" in odd.json()["detail"]


def test_fixed_role_runner_validates_before_playing():
    with pytest.raises(ValueError, match="two participants"):
        validate_fixed_role_players([{"algorithm": "neural", "model_id": "a"}])
    with pytest.raises(ValueError, match="trained neural models"):
        validate_fixed_role_players(
            [{"algorithm": "baseline"}, {"algorithm": "neural", "model_id": "a"}]
        )
    with pytest.raises(ValueError, match="identical search settings"):
        validate_fixed_role_players(
            [
                {"algorithm": "neural", "model_id": "a", "iterations": 5},
                {"algorithm": "neural", "model_id": "b", "iterations": 6},
            ]
        )
    validate_fixed_role_players(
        [
            {"algorithm": "neural", "model_id": "a", "iterations": 5},
            {"algorithm": "neural", "model_id": "b", "iterations": 5},
        ]
    )
    with pytest.raises(ValueError, match="even number of games"):
        tournament(
            {"players": [{"algorithm": "baseline"}] * 2, "games": 3, "rulesets": ["brandubh"]}
        )
    with pytest.raises(ValueError, match="1 and 10000"):
        tournament(
            {
                "mode": "fixed_roles",
                "players": [
                    {"algorithm": "neural", "model_id": "a"},
                    {"algorithm": "neural", "model_id": "b"},
                ],
                "games": 0,
                "rulesets": ["brandubh"],
            }
        )
    with pytest.raises(ValueError, match="Unknown tournament mode"):
        tournament({"mode": "mystery", "players": [], "games": 2})


def test_fixed_role_runner_never_swaps_sides(monkeypatch):
    played = []

    def play(_ruleset, attackers, defenders, **options):
        played.append((attackers["model_id"], defenders["model_id"], options["seed"]))
        return {
            "winner": "attackers",
            "outcome": "completed",
            "plies": 1,
            "moves": [],
            "total_decision_time": 0.01,
        }

    monkeypatch.setattr("hnefatafl.ai.play_game", play)
    result = tournament(
        {
            "mode": "fixed_roles",
            "players": [
                {"algorithm": "neural", "model_id": "a", "iterations": 5},
                {"algorithm": "neural", "model_id": "b", "iterations": 5},
            ],
            "games": 3,
            "rulesets": ["brandubh"],
            "seed": 7,
        }
    )
    assert played == [("a", "b", 7), ("a", "b", 8), ("a", "b", 9)]
    assert [game["attacker_player"] for game in result["games"]] == [0, 0, 0]
    assert result["scores"] == [3.0, 0.0]
    assert result["by_ruleset"]["brandubh"]["wins"] == [3, 0]


def test_worker_rejects_a_tampered_stored_pairing(client, models):
    created = launch(client, fixed_role_configuration(models, games=1))
    wait_for(client, created["id"], lambda row: row["status"] == "completed")
    stored = client.app.state.store.get_tournament(created["id"])
    assert stored["config"]["mode"] == "fixed_roles"
    # A stored record that would bypass the API equality check fails the worker.
    stored["status"] = "running"
    stored["ended_at"] = None
    stored["config"]["players"][1]["config"]["iterations"] = 99
    client.app.state.store.save_tournament(stored)
    from hnefatafl.tournaments import run_worker

    assert run_worker(client.app.state.store.path, created["id"]) == 1
    failed = client.get(f"/api/tournaments/{created['id']}").json()
    assert failed["status"] == "failed"
    assert "worker failed" in failed["error"]
    assert json.dumps(failed["config"])  # configuration stays readable for review


@pytest.mark.parametrize(
    "rulesets",
    [["brandubh", "tablut"], [], "brandubh", ["unknown"], [7], ["brandubh", "brandubh"], None],
)
def test_fixed_role_runner_requires_exactly_one_supported_battlefield(rulesets, monkeypatch):
    played = []

    def play(*args, **options):
        played.append((args, options))
        return {
            "winner": "attackers",
            "outcome": "completed",
            "plies": 1,
            "moves": [],
            "total_decision_time": 0.01,
        }

    monkeypatch.setattr("hnefatafl.ai.play_game", play)
    config = {
        "mode": "fixed_roles",
        "rulesets": rulesets,
        "players": [
            {"algorithm": "neural", "model_id": "model-a"},
            {"algorithm": "neural", "model_id": "model-b"},
        ],
        "games": 5,
    }
    with pytest.raises(ValueError, match="battlefield|rulesets"):
        tournament(config)
    assert played == []


def test_fixed_role_runner_rejects_a_missing_battlefield(monkeypatch):
    monkeypatch.setattr(
        "hnefatafl.ai.play_game", lambda *args, **options: pytest.fail("no game may start")
    )
    with pytest.raises(ValueError, match="exactly one battlefield"):
        tournament(
            {
                "mode": "fixed_roles",
                "players": [
                    {"algorithm": "neural", "model_id": "model-a"},
                    {"algorithm": "neural", "model_id": "model-b"},
                ],
                "games": 5,
            }
        )


def test_one_battlefield_still_schedules_odd_fixed_role_counts(monkeypatch):
    played = []

    def play(_ruleset, attackers, defenders, **options):
        played.append(_ruleset)
        return {
            "winner": "attackers",
            "outcome": "completed",
            "plies": 1,
            "moves": [],
            "total_decision_time": 0.01,
        }

    monkeypatch.setattr("hnefatafl.ai.play_game", play)
    result = tournament(
        {
            "mode": "fixed_roles",
            "rulesets": ["brandubh"],
            "players": [
                {"algorithm": "neural", "model_id": "model-a", "iterations": 5},
                {"algorithm": "neural", "model_id": "model-b", "iterations": 5},
            ],
            "games": 5,
        }
    )
    assert played == ["brandubh"] * 5
    assert len(result["games"]) == 5
    assert result["by_ruleset"] == {
        "brandubh": {"wins": [5, 0], "draws": 0, "truncated": 0, "games": 5}
    }


def test_round_robin_keeps_accepting_multiple_battlefields(monkeypatch):
    played = []

    def play(ruleset, _attackers, _defenders, **options):
        played.append(ruleset)
        return {
            "winner": "attackers",
            "outcome": "completed",
            "plies": 1,
            "moves": [],
            "total_decision_time": 0.01,
        }

    monkeypatch.setattr("hnefatafl.ai.play_game", play)
    result = tournament(
        {
            "players": [{"algorithm": "baseline"}] * 2,
            "rulesets": ["brandubh", "tablut"],
            "games": 2,
            "max_plies": 1,
        }
    )
    assert played == ["brandubh", "brandubh", "tablut", "tablut"]
    assert sorted(result["by_ruleset"]) == ["brandubh", "tablut"]
