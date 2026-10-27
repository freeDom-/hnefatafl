"""Immutable agent opinions, advisory moves and replay/model lifecycle safety."""

from concurrent.futures import ThreadPoolExecutor
import sqlite3
import threading
import time

from auth_support import AuthenticatedClient as TestClient
from record_fixtures import historical_game
import pytest

from hnefatafl import ai
from hnefatafl.engine import State
from hnefatafl.models import ModelRegistry, PolicyValueNet, save_checkpoint
from hnefatafl.server import create_app
from hnefatafl.storage import ConflictError, Store


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as session:
        yield session


def create(client, **config):
    return historical_game(client, **{"ruleset": "brandubh", "seed": 51, **config})


def analyze(client, game):
    result = client.post(
        f"/api/games/{game['id']}/analysis",
        json={"version": game["version"], "cursor": game["cursor"]},
    )
    assert result.status_code == 200, result.text
    return result.json()


def move(client, game, selected=None):
    result = client.post(
        f"/api/games/{game['id']}/moves",
        json={**(selected or game["legal_moves"][0]), "version": game["version"]},
    )
    assert result.status_code == 200, result.text
    return result.json()


def model(client, tmp_path):
    original = save_checkpoint(
        tmp_path / "original-training.pt",
        PolicyValueNet("brandubh", 4, 0),
        {"name": "Hugin"},
    )
    return ModelRegistry(client.app.state.data_dir / "models").register(original), original


def test_both_agents_report_actual_sources_and_current_players_perspective(client, monkeypatch):
    game = create(
        client,
        mode="ai-ai",
        players={
            "attackers": {"name": "Hugin", "config": {"algorithm": "baseline", "time_limit": 0.1}},
            "defenders": {
                "name": "Munin",
                "config": {
                    "algorithm": "mcts",
                    "iterations": 3,
                    "rollout_depth": 1,
                    "time_limit": 0.1,
                },
            },
        },
    )
    before_stats = client.get("/api/stats").json()
    report = analyze(client, game)
    assert game["analysis_enabled"] is True and game["analysis"] is None
    assert report["side_to_move"] == "attackers" and report["cursor"] == 0
    assert [entry["agent_id"] for entry in report["evaluations"]] == ["attackers", "defenders"]
    assert [entry["source"] for entry in report["evaluations"]] == ["heuristic", "mcts_rollout"]
    for entry in report["evaluations"]:
        assert entry["win_probability"] == pytest.approx((1 - entry["value"]) / 2)
        assert entry["best_move"] in game["legal_moves"]
        assert entry["estimated"] and not entry["calibrated"]
        assert entry["search_seed"] == 51
    assert report["evaluations"][1]["simulations"] == 3
    assert report["hint"] is None
    saved = client.get(f"/api/games/{game['id']}").json()
    assert saved["version"] == game["version"] == 0 and saved["moves"] == []
    assert saved["analysis"] == report
    after_stats = client.get("/api/stats").json()
    assert {key: value for key, value in before_stats.items() if key != "recent_games"} == {
        key: value for key, value in after_stats.items() if key != "recent_games"
    }

    def must_not_recompute(*_args):
        raise AssertionError("A retained analysis was recomputed")

    monkeypatch.setattr(ai, "analyze_position", must_not_recompute)
    assert analyze(client, saved) == report


def test_saved_agents_keep_their_own_depth_for_both_actual_play_and_estimates(client, monkeypatch):
    game = create(
        client,
        mode="ai-ai",
        players={
            side: {
                "name": f"Jarl {depth}",
                "config": {"algorithm": "alpha_beta", "depth": depth, "time_limit": 15},
            }
            for side, depth in (("attackers", 1), ("defenders", 2))
        },
    )
    path = f"/api/games/{game['id']}"
    game = client.get(path).json()
    report = analyze(client, game)
    assert [entry["config"]["depth"] for entry in report["evaluations"]] == [1, 2]
    assert [entry["completed_depth"] for entry in report["evaluations"]] == [1, 2]
    selected_depths = []
    original = ai.choose_move

    def choose_at_saved_depth(state, config, **kwargs):
        selected_depths.append((state.turn, config["depth"]))
        return original(state, config, **kwargs)

    monkeypatch.setattr(ai, "choose_move", choose_at_saved_depth)
    attacker_move = client.post(path + "/ai-move", json={"version": game["version"]})
    assert attacker_move.status_code == 200, attacker_move.text
    game = attacker_move.json()
    assert game["moves"][0]["source"] == report["evaluations"][0]["best_move"]["source"]
    assert game["moves"][0]["target"] == report["evaluations"][0]["best_move"]["target"]
    defender_move = client.post(path + "/ai-move", json={"version": game["version"]})
    assert defender_move.status_code == 200, defender_move.text
    assert selected_depths == [("attackers", 1), ("defenders", 2)]
    assert [
        defender_move.json()["players"][side]["config"]["depth"]
        for side in ("attackers", "defenders")
    ] == [1, 2]


def test_advisor_moves_are_legal_and_reports_survive_replay_and_server_restart(client, tmp_path):
    game = create(
        client,
        advisor={
            "name": "Jarl advisor",
            "profile_id": "alpha_beta",
            "config": {"depth": 1, "time_limit": 0.1},
        },
    )
    report0 = analyze(client, game)
    hint = report0["hint"]
    assert hint["agent_id"] == "advisor" and hint["move"] in game["legal_moves"]
    assert report0["evaluations"][0]["agent_id"] == "advisor"
    game = move(client, game, hint["move"])
    report1 = analyze(client, game)
    assert report1["side_to_move"] == "defenders"
    assert report1["evaluations"][0]["search_seed"] == 52
    assert report1["evaluations"][0]["win_probability"] == pytest.approx(
        (1 + report1["evaluations"][0]["value"]) / 2
    )
    path = f"/api/games/{game['id']}"
    game = client.post(path + "/undo", json={"version": game["version"]}).json()
    assert game["analysis"] == report0
    assert analyze(client, game) == report0
    game = client.post(path + "/redo", json={"version": game["version"]}).json()
    assert game["analysis"] == report1
    with TestClient(create_app(tmp_path)) as reloaded:
        assert reloaded.get(path).json()["analysis"] == report1
    assert client.post(path + "/restart", json={"version": game["version"]}).status_code == 409
    assert client.get(path).json()["analysis"] == report1


def test_missing_history_and_legacy_games_do_not_invent_agent_opinions(client, monkeypatch):
    game = create(client, advisor={"profile_id": "baseline"})
    game = move(client, game)
    path = f"/api/games/{game['id']}"
    game = client.post(path + "/undo", json={"version": game["version"]}).json()
    missing = analyze(client, game)
    assert missing["unavailable_reason"] == "not_recorded"
    assert (
        missing["created_at"] is None and missing["evaluations"] == [] and missing["hint"] is None
    )
    game = client.post(path + "/redo", json={"version": game["version"]}).json()
    store = client.app.state.store
    old = store.get_game(game["id"])
    old.pop("analysis_enabled")
    store.update_game(old, game["version"])
    game = client.get(path).json()

    def forbidden(*_args):
        raise AssertionError("Legacy data must not be reevaluated")

    monkeypatch.setattr(ai, "analyze_position", forbidden)
    assert analyze(client, game)["unavailable_reason"] == "not_recorded"
    assert game["analysis"] is None


def test_position_cache_distinguishes_new_branches(client):
    game = create(client, advisor={"profile_id": "baseline", "config": {"time_limit": 0.1}})
    report0 = analyze(client, game)
    alternative = game["legal_moves"][-1]
    game = move(client, game, game["legal_moves"][0])
    report1 = analyze(client, game)
    game = client.post(f"/api/games/{game['id']}/undo", json={"version": game["version"]}).json()
    assert game["analysis"] == report0
    game = move(client, game, alternative)
    assert game["analysis"] is None
    branch = analyze(client, game)
    assert branch["cursor"] == report1["cursor"] == 1
    assert branch["position_key"] != report1["position_key"]


def test_advisor_model_is_protected_and_discard_removes_its_analysis(client, tmp_path):
    registered, original = model(client, tmp_path)
    game = create(
        client,
        advisor={
            "name": "Hugin advisor",
            "profile_id": "neural",
            "config": {"model_id": registered["id"], "iterations": 2, "time_limit": 0.1},
        },
    )
    report = analyze(client, game)
    assert report["evaluations"][0]["source"] == "neural_search"
    assert report["evaluations"][0]["model_name"] == "Hugin"
    assert report["hint"]["move"] in game["legal_moves"]
    path = f"/api/models/{registered['id']}"
    usage = client.get(path + "/usage").json()
    assert [saved["id"] for saved in usage["unfinished_games"]] == [game["id"]]
    assert client.delete(path).status_code == 409
    removed = client.delete(
        path, params={"discard_unfinished": "true", "usage_token": usage["usage_token"]}
    )
    assert removed.status_code == 200
    assert client.get(f"/api/games/{game['id']}").status_code == 404
    assert client.app.state.store.game_analysis(game["id"], report["position_key"]) is None
    assert original.is_file()


def test_completed_model_replay_retains_analysis_after_model_deletion(
    client, tmp_path, monkeypatch
):
    registered, _original = model(client, tmp_path)
    model_rune = client.get("/api/models").json()[0]["rune"]
    game = create(
        client,
        advisor={
            "name": "Hugin advisor",
            "profile_id": "neural",
            "config": {"model_id": registered["id"], "iterations": 1, "time_limit": 0.01},
        },
    )
    report = analyze(client, game)
    assert game["advisor"]["rune"] == report["evaluations"][0]["rune"] == model_rune
    # Complete the retained real game with legal deterministic baseline moves;
    # the advisor's weights affect its opinion, not who controls the players.
    for ply in range(100):
        if game["winner"]:
            break
        state = State.from_dict(client.app.state.store.get_game(game["id"])["state"])
        selected = ai.choose_move(
            state, {"algorithm": "baseline", "seed": 3 + ply, "time_limit": 0.1}
        )
        game = move(client, game, selected.to_dict())
    assert game["winner"]
    assert client.delete(f"/api/models/{registered['id']}").status_code == 200
    terminal = analyze(client, game)
    assert terminal["evaluations"][0]["source"] == "terminal"
    assert terminal["evaluations"][0]["calibrated"] is True
    assert terminal["evaluations"][0]["estimated"] is False
    assert terminal["hint"] is None
    game = client.post(
        f"/api/games/{game['id']}/seek", json={"version": game["version"], "cursor": 0}
    ).json()
    assert game["analysis"] == report
    assert analyze(client, game) == report
    assert client.get(f"/api/games/{game['id']}/replay").json()["validated"]
    branch = client.post(
        f"/api/games/{game['id']}/moves",
        json={**game["legal_moves"][0], "version": game["version"]},
    )
    assert branch.status_code == 409
    assert (
        client.post(
            f"/api/games/{game['id']}/restart", json={"version": game["version"]}
        ).status_code
        == 409
    )


@pytest.mark.parametrize("action", ["move", "delete"])
def test_late_analysis_cannot_overwrite_moves_or_resurrect_deleted_games(
    client, monkeypatch, action
):
    game = create(client, advisor={"profile_id": "baseline", "config": {"time_limit": 0.1}})
    started, release = threading.Event(), threading.Event()
    original = ai.analyze_position

    def waiting(*args):
        started.set()
        assert release.wait(5)
        return original(*args)

    monkeypatch.setattr(ai, "analyze_position", waiting)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(
            client.post, f"/api/games/{game['id']}/analysis", json={"version": 0, "cursor": 0}
        )
        assert started.wait(5)
        try:
            if action == "move":
                changed = move(client, game)
                assert changed["version"] == 1
            else:
                assert client.delete(f"/api/games/{game['id']}?version=0").status_code == 200
        finally:
            release.set()
        assert future.result(timeout=5).status_code == 409
    with client.app.state.store.connection() as connection:
        assert connection.execute("SELECT count(*) FROM game_analysis").fetchone()[0] == 0
    if action == "move":
        assert client.get(f"/api/games/{game['id']}").json() == changed


def test_analysis_rejects_stale_versions_and_wrong_cursor_without_computation(client, monkeypatch):
    game = create(client, advisor={"profile_id": "baseline"})
    game = move(client, game)
    path = f"/api/games/{game['id']}/analysis"
    assert client.post(path, json={"version": 0, "cursor": 1}).status_code == 409
    assert client.post(path, json={"version": 1, "cursor": 0}).status_code == 409
    assert client.post(path, json={"version": True, "cursor": 1}).status_code == 422
    assert client.post(path, json={"version": 1}).status_code == 422
    assert (
        client.post("/api/games/nonexistent/analysis", json={"version": 0, "cursor": 0}).status_code
        == 404
    )


def test_failed_agent_analysis_is_explicitly_unavailable(client, monkeypatch):
    game = create(client, advisor={"profile_id": "baseline"})

    def broken(*_args):
        raise ValueError("Cannot evaluate")

    monkeypatch.setattr(ai, "analyze_position", broken)
    result = analyze(client, game)
    assert result["evaluations"][0]["win_probability"] is None
    assert result["evaluations"][0]["source"] == "unavailable"
    assert result["evaluations"][0]["unavailable_reason"] == "evaluation_failed"
    assert result["hint"] is None


def test_advisor_validation_matches_ai_opponents(client, tmp_path):
    registered, _ = model(client, tmp_path)
    cases = [
        {"profile_id": "neural"},
        {"name": "   "},
        {"config": {"algorithm": "unknown"}},
        {"config": {"iterations": True}},
        {"config": {"model_id": "missing", "algorithm": "neural"}},
    ]
    game = client.post("/api/games", json={"ruleset": "brandubh"}).json()
    for advisor in cases:
        assert client.put(
            f"/api/games/{game['id']}/analytics", json={"advisor": advisor}
        ).status_code in {400, 404}
    other = client.post("/api/games", json={"ruleset": "tablut"}).json()
    wrong_ruleset = client.put(
        f"/api/games/{other['id']}/analytics",
        json={"advisor": {"profile_id": "neural", "config": {"model_id": registered["id"]}}},
    )
    assert wrong_ruleset.status_code == 400
    assert (
        client.put(
            f"/api/games/{game['id']}/analytics",
            json={"advisor": {"arbitrary_path": "/tmp/model.pt"}},
        ).status_code
        == 422
    )


def test_schema_three_migration_preserves_games_and_analysis_deletion_rolls_back(tmp_path):
    path = tmp_path / "history.sqlite3"
    store = Store(path)
    game = {"id": "old", "version": 0, "created_at": "2026-10-21", "moves": []}
    store.create_game(game)
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE game_analysis")
        connection.execute("PRAGMA user_version=3")
    upgraded = Store(path)
    assert upgraded.get_game("old") == game
    snapshot = {"position_key": "abc", "value": 1}
    assert upgraded.save_game_analysis("old", 0, snapshot) == snapshot
    assert upgraded.save_game_analysis("old", 0, {**snapshot, "value": -1}) == snapshot
    with pytest.raises(RuntimeError):
        with upgraded.discard_games({"old": 0}):
            raise RuntimeError("Catalog operation failed")
    assert upgraded.get_game("old") == game
    assert upgraded.game_analysis("old", "abc") == snapshot
    with pytest.raises(ConflictError):
        upgraded.save_game_analysis("old", 99, snapshot)
    upgraded.delete_game("old", 0)
    assert upgraded.game_analysis("old", "abc") is None
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 10


def test_seed_only_model_variants_merge_without_losing_actual_game_seeds(client, tmp_path):
    registered, _ = model(client, tmp_path)
    identifiers = []
    for seed in (17, 23, 17):
        game = create(
            client,
            seed=seed,
            mode="human-ai",
            players={
                "attackers": {
                    "name": "Hugin",
                    "profile_id": "neural",
                    "config": {"model_id": registered["id"], "iterations": 2, "time_limit": 0.1},
                }
            },
        )
        identifiers.append(game["players"]["attackers"]["variant_id"])
        assert game["players"]["attackers"]["config"]["seed"] == seed
        stored = client.app.state.store.get_game(game["id"])
        stored["recorded_result"] = {"winner": "attackers", "reason": "fixture", "plies": 5}
        client.app.state.store.update_game(stored, 0)
    assert len(set(identifiers)) == 1
    rows = client.get("/api/stats?kind=ai").json()["by_player"]
    assert len(rows) == 1
    assert rows[0]["games"] == rows[0]["wins"] == 3
    assert rows[0]["seed_count"] == 2
    assert rows[0]["rune"] == client.get("/api/models").json()[0]["rune"]
    assert "seed" not in rows[0]["config"]


@pytest.mark.parametrize(
    ("profile_id", "expected"),
    [
        ("baseline", {"algorithm": "baseline", "time_limit": 1.0}),
        ("alpha_beta", {"algorithm": "alpha_beta", "depth": 5, "time_limit": 2.0}),
        (
            "mcts",
            {
                "algorithm": "mcts",
                "iterations": 5000,
                "temperature": 0.0,
                "exploration": 1.4,
                "time_limit": 2.0,
            },
        ),
    ],
)
def test_named_opponents_always_use_fixed_search_presets(client, profile_id, expected):
    game = create(
        client,
        mode="ai-ai",
        players={
            side: {
                "profile_id": profile_id,
                "config": {
                    "algorithm": "baseline",
                    "depth": 1,
                    "iterations": 2,
                    "temperature": 1,
                    "exploration": 0.2,
                    "time_limit": 0.01,
                },
            }
            for side in ("attackers", "defenders")
        },
    )
    for player in game["players"].values():
        for key, value in expected.items():
            assert player["config"][key] == value


def test_custom_opponent_keeps_explicit_search_budgets(client):
    game = create(
        client,
        mode="ai-ai",
        players={
            "attackers": {"config": {"algorithm": "alpha_beta", "depth": 3, "time_limit": 0.2}},
            "defenders": {
                "config": {
                    "algorithm": "mcts",
                    "iterations": 7,
                    "exploration": 0.5,
                    "temperature": 0.3,
                    "time_limit": 0.4,
                }
            },
        },
    )
    assert game["players"]["attackers"]["config"]["depth"] == 3
    assert game["players"]["attackers"]["config"]["time_limit"] == 0.2
    assert game["players"]["defenders"]["config"]["iterations"] == 7
    assert game["players"]["defenders"]["config"]["exploration"] == 0.5
    assert game["players"]["defenders"]["config"]["temperature"] == 0.3
    assert game["players"]["defenders"]["config"]["time_limit"] == 0.4


def test_custom_shield_bearer_algorithm_keeps_its_budget(client):
    game = create(
        client,
        mode="human-ai",
        human_side="defenders",
        players={
            "attackers": {"config": {"algorithm": "baseline", "time_limit": 0.03}},
        },
    )
    assert game["players"]["attackers"]["config"]["algorithm"] == "baseline"
    assert game["players"]["attackers"]["config"]["time_limit"] == 0.03


def test_historical_analysis_reads_requested_cursor_without_seeking(client):
    game = create(client, advisor={"profile_id": "baseline", "config": {"time_limit": 0.1}})
    first = move(client, game)
    second = move(client, first)
    before = client.get(f"/api/games/{game['id']}").json()
    path = f"/api/games/{game['id']}/analysis"
    report = None
    for _ in range(40):
        result = client.post(
            path,
            json={
                "version": second["version"],
                "cursor": 0,
                "background": True,
                "historical": True,
            },
        )
        assert result.status_code == 200, result.text
        report = result.json()
        if report["status"] == "complete":
            break
        time.sleep(0.05)
    assert report is not None and report["status"] == "complete"
    assert report["cursor"] == 0 and report["ply"] == 0
    assert any(entry["agent_id"] == "advisor" for entry in report["evaluations"])
    # Read-only: the authoritative cursor, version and stored result are unchanged.
    assert client.get(f"/api/games/{game['id']}").json() == before


def test_historical_analysis_rejects_out_of_range_cursor(client):
    game = create(client, advisor={"profile_id": "baseline"})
    result = client.post(
        f"/api/games/{game['id']}/analysis",
        json={"version": game["version"], "cursor": 1, "background": True, "historical": True},
    )
    assert result.status_code == 422
