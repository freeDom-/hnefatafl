"""Live search never blocks play or attaches evidence to a rejected move/line."""

from concurrent.futures import ThreadPoolExecutor
import threading
import time

from auth_support import AuthenticatedClient as TestClient
from record_fixtures import historical_game
import pytest

from hnefatafl import ai
from hnefatafl.engine import legal_moves
from hnefatafl.models import ModelRegistry, PolicyValueNet, save_checkpoint
from hnefatafl.server import create_app


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path)) as session:
        yield session


def create(client, **options):
    return historical_game(client, **{"ruleset": "brandubh", "seed": 51, **options})


def analyze(client, game):
    response = client.post(
        f"/api/games/{game['id']}/analysis",
        json={"version": game["version"], "cursor": game["cursor"], "background": True},
    )
    assert response.status_code == 200, response.text
    return response.json()


def settings(client, game, advisor=None, **options):
    response = client.put(
        f"/api/games/{game['id']}/analytics", json={"advisor": advisor, **options}
    )
    assert response.status_code == 200, response.text
    return response.json()


def wait_for(predicate):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        threading.Event().wait(0.005)
    raise AssertionError("Background search did not finish")


def complete(client, game):
    return wait_for(
        lambda: report if (report := analyze(client, game))["status"] == "complete" else None
    )


def report(state, value=0.25, *, last=False, depth=1):
    return {
        "source": "heuristic",
        "value": value,
        "best_move": legal_moves(state)[-1 if last else 0].to_dict(),
        "completed_depth": depth,
    }


def move(client, game, selected=None):
    response = client.post(
        f"/api/games/{game['id']}/moves",
        json={**(selected or game["legal_moves"][0]), "version": game["version"]},
    )
    assert response.status_code == 200, response.text
    return response.json()


def seek(client, game, cursor):
    response = client.post(
        f"/api/games/{game['id']}/seek", json={"version": game["version"], "cursor": cursor}
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_human_can_move_while_ai_opinion_runs_and_late_opinion_is_saved(client, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def slow_analysis(state, _configuration, *, progress=None):
        entered.set()
        assert release.wait(5)
        return report(state, -0.4)

    monkeypatch.setattr(ai, "analyze_position", slow_analysis)
    game = create(client, mode="human-ai", human_side="attackers")
    path = f"/api/games/{game['id']}"
    try:
        pending = analyze(client, game)
        assert pending["status"] == "pending"
        assert entered.wait(5)
        # This request completes while the analysis thread is still held.
        advanced = move(client, game)
        assert advanced["cursor"] == 1 and not release.is_set()
        assert advanced["analysis"] is None
    finally:
        release.set()
    stored = wait_for(
        lambda: client.app.state.store.game_analysis(game["id"], pending["position_key"])
    )
    assert stored["evaluations"][0]["agent_id"] == "defenders"
    assert stored["evaluations"][0]["win_probability"] == pytest.approx(0.7)
    replay = seek(client, client.get(path).json(), 0)
    assert replay["analysis"] == stored
    assert replay["analysis"]["evaluations"][0]["search_seed"] == 51


def test_completion_racing_poll_does_not_return_complete_with_a_stale_empty_report(
    client, monkeypatch
):
    entered, release = threading.Event(), threading.Event()

    def held_analysis(state, _configuration, *, progress=None):
        entered.set()
        assert release.wait(5)
        return report(state)

    monkeypatch.setattr(ai, "analyze_position", held_analysis)
    game = create(client, mode="human-ai", human_side="attackers")
    store = client.app.state.store
    try:
        assert analyze(client, game)["status"] == "pending"
        assert entered.wait(5)
        original = store.game_analysis
        reads = 0

        def finish_after_final_read(*args):
            nonlocal reads
            retained = original(*args)
            reads += 1
            if reads == 2:
                # Hold the stale read while the worker commits and becomes idle.
                release.set()
                wait_for(lambda: not client.app.state.analyses.tasks)
            return retained

        monkeypatch.setattr(store, "game_analysis", finish_after_final_read)
        result = analyze(client, game)
        assert result["status"] == "pending" or result["evaluations"]
        assert complete(client, game)["evaluations"]
    finally:
        release.set()


def test_actual_ai_search_is_reused_and_not_precomputed_on_its_turn(client, monkeypatch):
    game = create(client, mode="human-ai", human_side="defenders")
    calls = []
    original = ai.choose_move

    def actual_search(state, config, *, diagnostics=None, progress=None):
        calls.append((state.ply, config["algorithm"]))
        return original(state, config, diagnostics=diagnostics, progress=progress)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("The acting AI must not search once for odds and again to play")

    monkeypatch.setattr(ai, "choose_move", actual_search)
    monkeypatch.setattr(ai, "analyze_position", forbidden)
    assert analyze(client, game)["status"] == "complete"
    assert calls == []
    response = client.post(f"/api/games/{game['id']}/ai-move", json={"version": game["version"]})
    assert response.status_code == 200, response.text
    advanced = response.json()
    assert calls == [(0, "baseline")]
    replay = seek(client, advanced, 0)
    entry = replay["analysis"]["evaluations"][0]
    assert entry["agent_id"] == "attackers"
    assert entry["best_move"] == {key: advanced["moves"][0][key] for key in ("source", "target")}
    assert entry["win_probability"] == pytest.approx((1 - entry["value"]) / 2)
    assert analyze(client, replay)["evaluations"] == replay["analysis"]["evaluations"]
    assert calls == [(0, "baseline")]


def test_both_ai_opinions_use_one_actual_search_and_one_background_search(client, monkeypatch):
    game = create(
        client,
        mode="ai-ai",
        players={
            "attackers": {"config": {"algorithm": "baseline"}},
            "defenders": {"config": {"algorithm": "alpha_beta", "depth": 1}},
        },
    )
    opinions = []

    def other_agent(state, config, *, progress=None):
        opinions.append(config["algorithm"])
        return report(state, -0.6)

    monkeypatch.setattr(ai, "analyze_position", other_agent)
    initial = complete(client, game)
    assert opinions == ["alpha_beta"]
    assert [entry["agent_id"] for entry in initial["evaluations"]] == ["defenders"]
    response = client.post(f"/api/games/{game['id']}/ai-move", json={"version": game["version"]})
    assert response.status_code == 200, response.text
    replay = seek(client, response.json(), 0)
    assert {entry["agent_id"] for entry in replay["analysis"]["evaluations"]} == {
        "attackers",
        "defenders",
    }
    assert opinions == ["alpha_beta"]


def test_ai_can_play_while_other_agents_background_search_is_still_running(client, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def slow_other_agent(state, _config, *, progress=None):
        entered.set()
        assert release.wait(5)
        return report(state)

    monkeypatch.setattr(ai, "analyze_position", slow_other_agent)
    game = create(client, mode="ai-ai")
    try:
        initial = analyze(client, game)
        assert entered.wait(5)
        response = client.post(
            f"/api/games/{game['id']}/ai-move", json={"version": game["version"]}
        )
        assert response.status_code == 200, response.text
        assert response.json()["cursor"] == 1 and not release.is_set()
    finally:
        release.set()
    wait_for(lambda: not client.app.state.analyses.tasks)
    retained = client.app.state.store.game_analysis(game["id"], initial["position_key"])
    assert {entry["agent_id"] for entry in retained["evaluations"]} == {"attackers", "defenders"}


def test_advisor_partial_results_update_move_and_probability_without_changing_game(
    client, monkeypatch
):
    entered, release = threading.Event(), threading.Event()
    observed = []

    def progressive(state, config, *, progress=None):
        observed.append(config["time_limit"])
        progress(report(state, -0.4))
        entered.set()
        assert release.wait(5)
        return report(state, 0.3, last=True, depth=2)

    monkeypatch.setattr(ai, "analyze_position", progressive)
    game = create(client)
    preferences = settings(client, game, {"name": "Jarl advisor", "profile_id": "alpha_beta"})
    try:
        analyze(client, game)
        assert entered.wait(5)
        partial = analyze(client, game)
        assert partial["status"] == "pending"
        assert partial["evaluations"][0]["completed_depth"] == 1
        assert partial["evaluations"][0]["win_probability"] == pytest.approx(0.7)
        first_hint = partial["hint"]["move"]
        assert client.get(f"/api/games/{game['id']}").json()["version"] == 0
    finally:
        release.set()
    final = complete(client, game)
    assert final["advisor_revision"] == preferences["revision"]
    assert final["hint"]["move"] != first_hint
    assert final["evaluations"][0]["completed_depth"] == 2
    assert final["evaluations"][0]["win_probability"] == pytest.approx(0.35)
    assert observed == [5]


def test_changed_advisor_rejects_late_previous_search(client, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def advisory(state, config, *, progress=None):
        if config["algorithm"] == "alpha_beta":
            progress(report(state, -0.8))
            entered.set()
            assert release.wait(5)
            return report(state, -0.9)
        return report(state, 0.8, last=True)

    monkeypatch.setattr(ai, "analyze_position", advisory)
    game = create(client)
    old = settings(client, game, {"name": "Previous advisor", "profile_id": "alpha_beta"})
    try:
        analyze(client, game)
        assert entered.wait(5)
        new = settings(client, game, {"name": "Current advisor", "profile_id": "baseline"})
        assert new["revision"] > old["revision"]
        # The replacement starts while the previous worker is still held.
        final = complete(client, game)
        assert final["evaluations"][0]["name"] == "Current advisor"
        assert final["evaluations"][0]["win_probability"] == pytest.approx(0.1)
    finally:
        release.set()
    wait_for(lambda: not client.app.state.analyses.tasks)
    assert complete(client, game)["evaluations"] == final["evaluations"]


def test_hidden_winning_chances_keep_advisor_move_but_publish_no_probability(client, monkeypatch):
    calls = []

    def advisory(state, config, *, progress=None):
        calls.append(config["time_limit"])
        return report(state, -0.6)

    monkeypatch.setattr(ai, "analyze_position", advisory)
    game = create(client, mode="human-ai", human_side="attackers")
    preferences = settings(
        client, game, {"profile_id": "baseline"}, show_win_chances=False, time_limit=3
    )
    final = complete(client, game)
    assert final["hint"]["move"] in game["legal_moves"]
    assert [entry["agent_id"] for entry in final["evaluations"]] == ["advisor"]
    assert final["evaluations"][0]["win_probability"] is None
    assert "value" not in final["evaluations"][0]
    assert calls == [3]
    assert preferences["show_win_chances"] is False
    assert client.get(f"/api/games/{game['id']}").json()["version"] == 0


def test_deleted_game_cannot_be_recreated_by_a_late_background_result(client, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def slow(state, _config, *, progress=None):
        entered.set()
        assert release.wait(5)
        return report(state)

    monkeypatch.setattr(ai, "analyze_position", slow)
    game = create(client)
    settings(client, game, {"profile_id": "baseline"})
    try:
        pending = analyze(client, game)
        assert entered.wait(5)
        response = client.delete(f"/api/games/{game['id']}", params={"version": 0})
        assert response.status_code == 200, response.text
    finally:
        release.set()
    wait_for(lambda: not client.app.state.analyses.tasks)
    store = client.app.state.store
    assert store.get_game(game["id"]) is None
    assert store.analytics(game["id"]) is None
    assert store.game_analysis(game["id"], pending["position_key"]) is None
    assert store.advisor_analysis(game["id"], pending["position_key"], 1) is None


def test_read_only_history_preserves_the_live_line_and_its_pending_opinion(client, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def slow(state, _config, *, progress=None):
        entered.set()
        assert release.wait(5)
        return report(state)

    monkeypatch.setattr(ai, "analyze_position", slow)
    initial = create(client)
    game = move(client, initial)
    preferences = settings(client, game, {"profile_id": "baseline"})
    try:
        pending = analyze(client, game)
        assert entered.wait(5)
        rewound = seek(client, game, 0)
        denied = client.post(
            f"/api/games/{game['id']}/moves",
            json={**initial["legal_moves"][-1], "version": rewound["version"]},
        )
        assert denied.status_code == 409
        assert client.get(f"/api/games/{game['id']}").json()["moves"] == game["moves"]
    finally:
        release.set()
    wait_for(lambda: not client.app.state.analyses.tasks)
    assert (
        client.app.state.store.advisor_analysis(
            game["id"], pending["position_key"], preferences["revision"]
        )
        is not None
    )


def test_ai_move_losing_version_race_does_not_store_an_unplayed_opinion(client, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def slow_move(state, _config, *, diagnostics=None, progress=None):
        diagnostics.update(value=0.7, completed_depth=1)
        entered.set()
        assert release.wait(5)
        return legal_moves(state)[0]

    monkeypatch.setattr(ai, "choose_move", slow_move)
    game = create(client, mode="human-ai", human_side="defenders")
    path = f"/api/games/{game['id']}"
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(client.post, path + "/ai-move", json={"version": 0})
        try:
            assert entered.wait(5)
            assert seek(client, game, 0)["version"] == 1
        finally:
            release.set()
        assert future.result(timeout=5).status_code == 409
    retained = client.get(path).json()
    assert retained["moves"] == [] and retained["analysis"] is None


def test_concurrent_ai_requests_retain_only_winning_moves_search_evidence(client, monkeypatch):
    rendezvous = threading.Barrier(2)
    counter_lock = threading.Lock()
    calls = 0

    def concurrent_search(state, _config, *, diagnostics=None, progress=None):
        nonlocal calls
        with counter_lock:
            calls += 1
            index = calls
        diagnostics.update(value=0.7 if index == 1 else -0.7, completed_depth=1)
        rendezvous.wait(timeout=5)
        return legal_moves(state)[0 if index == 1 else -1]

    monkeypatch.setattr(ai, "choose_move", concurrent_search)
    game = create(client, mode="human-ai", human_side="defenders")
    path = f"/api/games/{game['id']}"
    with ThreadPoolExecutor(max_workers=2) as executor:
        requests = [
            executor.submit(client.post, path + "/ai-move", json={"version": 0}) for _ in range(2)
        ]
        results = [future.result(timeout=5) for future in requests]
    assert sorted(response.status_code for response in results) == [200, 409]
    advanced = next(response.json() for response in results if response.status_code == 200)
    entry = seek(client, advanced, 0)["analysis"]["evaluations"][0]
    actual = {key: advanced["moves"][0][key] for key in ("source", "target")}
    assert entry["best_move"] == actual
    assert entry["value"] == (0.7 if actual == game["legal_moves"][0] else -0.7)


def test_live_analytics_model_reference_changes_are_reflected_in_deletion_usage(client, tmp_path):
    checkpoint = save_checkpoint(
        tmp_path / "trained.pt", PolicyValueNet("brandubh", 4, 0), {"name": "Hugin"}
    )
    model = ModelRegistry(client.app.state.data_dir / "models").register(checkpoint)
    game = create(client)
    preferences = settings(
        client,
        game,
        {"profile_id": "neural", "config": {"model_id": model["id"], "iterations": 1}},
    )
    path = f"/api/models/{model['id']}"
    usage = client.get(path + "/usage").json()
    assert [entry["id"] for entry in usage["unfinished_games"]] == [game["id"]]
    assert client.delete(path).status_code == 409
    removed = settings(client, game)
    assert removed["revision"] > preferences["revision"]
    assert client.get(path + "/usage").json()["unfinished_games"] == []
    assert client.delete(path).status_code == 200
    assert client.get(f"/api/games/{game['id']}").status_code == 200


def test_restart_does_not_reactivate_a_removed_legacy_advisor_model(client, tmp_path):
    checkpoint = save_checkpoint(
        tmp_path / "legacy.pt", PolicyValueNet("brandubh", 4, 0), {"name": "Munin"}
    )
    model = ModelRegistry(client.app.state.data_dir / "models").register(checkpoint)
    game = create(
        client,
        advisor={"profile_id": "neural", "config": {"model_id": model["id"], "iterations": 1}},
    )
    settings(client, game, None)
    assert client.delete(f"/api/models/{model['id']}").status_code == 200
    restarted = client.post(f"/api/games/{game['id']}/restart", json={"version": 0})
    assert restarted.status_code == 409  # Historical creation is no longer supported.


def test_restart_retains_current_analytics_preferences_with_new_search_seed(client):
    game = client.post("/api/games", json={"ruleset": "brandubh"}).json()
    previous = settings(
        client,
        game,
        {"name": "Current advisor", "profile_id": "baseline"},
        time_limit=3,
        show_win_chances=False,
    )
    response = client.post(f"/api/games/{game['id']}/restart", json={"version": 0})
    assert response.status_code == 200, response.text
    restarted = response.json()
    current = restarted["analytics"]
    assert current["time_limit"] == 3 and current["show_win_chances"] is False
    assert current["advisor"]["name"] == previous["advisor"]["name"]
    assert current["advisor"]["config"]["seed"] == restarted["seed"] != game["seed"]
