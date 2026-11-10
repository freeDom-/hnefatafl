"""Synchronous clients join bounded advisor work without rewriting replay evidence."""

from concurrent.futures import Future, ThreadPoolExecutor
import threading

import pytest

from hnefatafl import ai, analysis_service
from hnefatafl.engine import legal_moves
from test_background_analysis import (
    analyze,
    client as client,
    complete,
    create,
    move,
    neural_advisor as neural_advisor,
    report,
    seek,
    settings,
    wait_for,
)


def synchronous(client, game):
    return client.post(
        f"/api/games/{game['id']}/analysis",
        json={"version": game["version"], "cursor": game["cursor"]},
    )


def observe_join(monkeypatch, service):
    joined = threading.Event()
    original = service._schedule

    def schedule(*args, **kwargs):
        result = original(*args, **kwargs)
        assert result[1] is not None and not result[1].done()
        joined.set()
        return result

    monkeypatch.setattr(service, "_schedule", schedule)
    return joined


@pytest.mark.parametrize("saved", [False, True])
def test_synchronous_neural_request_joins_background_search_and_keeps_completion(
    client, neural_advisor, monkeypatch, saved
):
    entered, release = threading.Event(), threading.Event()
    calls = []

    def search(state, config, *, diagnostics, progress, **kwargs):
        calls.append((config["iterations"], threading.current_thread().name))
        chosen = legal_moves(state)[0]
        diagnostics.update(value=0.25, simulations=1)
        progress(ai.analysis_from_search(state, config, chosen, diagnostics))
        entered.set()
        assert release.wait(5)
        diagnostics["simulations"] = config["iterations"]
        return {chosen: 1.0}

    monkeypatch.setattr(ai, "mcts_policy", search)
    neural_advisor["config"]["iterations"] = 4096
    game = create(client, **({} if saved else {"advisor": neural_advisor}))
    selected = settings(client, game, neural_advisor) if saved else {"revision": 0}
    service, store = client.app.state.analyses, client.app.state.store
    with ThreadPoolExecutor(max_workers=1) as requests:
        try:
            pending = analyze(client, game)
            assert entered.wait(5)
            partial = store.advisor_analysis(
                game["id"], pending["position_key"], selected["revision"]
            )
            assert partial["partial"]
            with monkeypatch.context() as patch:
                joined = observe_join(patch, service)
                response = requests.submit(synchronous, client, game)
                assert joined.wait(5) and not response.done()
                release.set()
                result = response.result(timeout=5)
            assert result.status_code == 200, result.text
        finally:
            release.set()
    body = result.json()
    assert body["evaluations"][0]["simulations"] == 4096
    assert body.get("advisor_revision") == (selected["revision"] if saved else None)
    assert len(calls) == 1 and calls[0][0] == 4096
    assert calls[0][1].startswith("board-analysis")
    assert synchronous(client, game).json() == body
    finished = store.advisor_analysis(game["id"], pending["position_key"], selected["revision"])
    for is_partial in (True, False):
        assert (
            store.save_analysis_entry(
                store.get_game(game["id"]),
                partial,
                partial["evaluations"][0],
                advisor_revision=selected["revision"],
                partial=is_partial,
            )
            == finished
        )
    assert complete(client, game)["evaluations"] == body["evaluations"]


def test_timed_out_synchronous_wait_does_not_cancel_shared_work(
    client, neural_advisor, monkeypatch
):
    entered, release = threading.Event(), threading.Event()
    calls = []

    def search(state, config, *, diagnostics, **kwargs):
        calls.append(config["iterations"])
        entered.set()
        assert release.wait(5)
        diagnostics.update(value=0.25, simulations=config["iterations"])
        return {legal_moves(state)[0]: 1.0}

    monkeypatch.setattr(ai, "mcts_policy", search)
    monkeypatch.setattr(analysis_service, "SYNCHRONOUS_WAIT_SECONDS", 0.01)
    game = create(client)
    settings(client, game, neural_advisor)
    try:
        analyze(client, game)
        assert entered.wait(5)
        response = synchronous(client, game)
        assert response.status_code == 503
        assert response.headers["Retry-After"] == "1"
        assert "still running" in response.json()["detail"]
        task = next(iter(client.app.state.analyses.tasks.values()))
        assert not task.cancelled() and not task.done()
    finally:
        release.set()
    assert complete(client, game)["evaluations"][0]["simulations"] == 1024
    assert synchronous(client, game).status_code == 200
    assert calls == [1024]


def test_completed_cache_is_reused_while_a_late_worker_is_still_running(
    client, neural_advisor, monkeypatch
):
    entered, release = threading.Event(), threading.Event()
    observed = []
    store = client.app.state.store

    def search(state, config, *, diagnostics, progress, **kwargs):
        chosen = legal_moves(state)[0]
        diagnostics.update(value=0.25, simulations=1)
        progress(ai.analysis_from_search(state, config, chosen, diagnostics))
        entered.set()
        assert release.wait(5)
        diagnostics["simulations"] = 2
        progress(ai.analysis_from_search(state, config, chosen, diagnostics))
        observed.append(
            store.advisor_analysis(game["id"], pending["position_key"], selected["revision"])
        )
        return {chosen: 1.0}

    monkeypatch.setattr(ai, "mcts_policy", search)
    monkeypatch.setattr(analysis_service, "SYNCHRONOUS_WAIT_SECONDS", 0.01)
    game = create(client)
    selected = settings(client, game, neural_advisor)
    try:
        pending = analyze(client, game)
        assert entered.wait(5)
        partial = store.advisor_analysis(game["id"], pending["position_key"], selected["revision"])
        finished = store.save_analysis_entry(
            store.get_game(game["id"]),
            partial,
            {**partial["evaluations"][0], "simulations": 1024},
            advisor_revision=selected["revision"],
        )
        result = synchronous(client, game)
        assert result.status_code == 200, result.text
        assert result.json()["evaluations"][0]["simulations"] == 1024
        assert client.app.state.analyses.tasks
    finally:
        release.set()
    wait_for(lambda: not client.app.state.analyses.tasks)
    assert observed == [finished]
    assert (
        store.advisor_analysis(game["id"], pending["position_key"], selected["revision"])
        == finished
    )


def test_revision_change_rejects_waiting_synchronous_response_and_old_progress(
    client, neural_advisor, monkeypatch
):
    entered, release = threading.Event(), threading.Event()
    completed = []

    def search(state, config, *, diagnostics, progress, **kwargs):
        chosen = legal_moves(state)[0]
        diagnostics.update(value=0.25, simulations=1)
        progress(ai.analysis_from_search(state, config, chosen, diagnostics))
        if config["iterations"] == 2048:
            entered.set()
            assert release.wait(5)
            diagnostics["simulations"] = 2
            progress(ai.analysis_from_search(state, config, chosen, diagnostics))
        completed.append(config["iterations"])
        diagnostics["simulations"] = config["iterations"]
        return {chosen: 1.0}

    monkeypatch.setattr(ai, "mcts_policy", search)
    game = create(client)
    neural_advisor["config"]["iterations"] = 2048
    old = settings(client, game, neural_advisor)
    with ThreadPoolExecutor(max_workers=1) as requests:
        try:
            analyze(client, game)
            assert entered.wait(5)
            with monkeypatch.context() as patch:
                joined = observe_join(patch, client.app.state.analyses)
                waiting = requests.submit(synchronous, client, game)
                assert joined.wait(5)
            neural_advisor["config"]["iterations"] = 4096
            new = settings(client, game, neural_advisor)
            final = complete(client, game)
            assert final["advisor_revision"] == new["revision"] > old["revision"]
            release.set()
            result = waiting.result(timeout=5)
            assert result.status_code == 409, result.text
        finally:
            release.set()
    assert completed == [4096]
    assert complete(client, game) == final


def test_legacy_synchronous_replay_is_read_only_but_explicit_background_advice_works(
    client, neural_advisor, monkeypatch
):
    game = create(client, mode="human-ai", human_side="attackers")
    original = synchronous(client, game).json()
    store = client.app.state.store
    evidence = store.game_analysis(game["id"], original["position_key"])
    game = move(client, game)
    selected = settings(client, game, neural_advisor)
    game = seek(client, game, 0)
    before = client.get(f"/api/games/{game['id']}").json()
    calls = []

    def search(state, config, *, diagnostics, **kwargs):
        calls.append((state.ply, config["iterations"]))
        diagnostics.update(value=0.25, simulations=config["iterations"])
        return {legal_moves(state)[0]: 1.0}

    monkeypatch.setattr(ai, "mcts_policy", search)
    result = synchronous(client, game)
    assert result.status_code == 200, result.text
    assert result.json()["advisor_revision"] == selected["revision"]
    assert result.json()["position_key"] == original["position_key"]
    assert result.json()["evaluations"] == original["evaluations"]
    assert calls == []
    path = f"/api/games/{game['id']}/analysis"
    final = wait_for(
        lambda: (
            response
            if (
                response := client.post(
                    path,
                    json={
                        "version": game["version"],
                        "cursor": 0,
                        "historical": True,
                        "background": True,
                    },
                ).json()
            )["status"]
            == "complete"
            else None
        )
    )
    assert (final["cursor"], final["position_key"]) == (0, original["position_key"])
    assert calls == [(0, 1024)]
    assert store.game_analysis(game["id"], original["position_key"]) == evidence
    # Analysis data is separate from the authoritative replay line/version.
    after = client.get(f"/api/games/{game['id']}").json()
    assert {k: v for k, v in before.items() if k != "analysis"} == {
        k: v for k, v in after.items() if k != "analysis"
    }


@pytest.mark.parametrize("saved", [False, True])
def test_revision_change_after_completion_cannot_publish_an_old_sync_response(
    client, neural_advisor, monkeypatch, saved
):
    def search(state, config, *, diagnostics, **kwargs):
        diagnostics.update(value=0.25, simulations=config["iterations"])
        return {legal_moves(state)[0]: 1.0}

    monkeypatch.setattr(ai, "mcts_policy", search)
    game = create(client, **({} if saved else {"advisor": neural_advisor}))
    if saved:
        preferences = settings(client, game, neural_advisor)
    else:
        preferences = client.get(f"/api/games/{game['id']}/analytics").json()
    service, store = client.app.state.analyses, client.app.state.store
    original = service.wait

    def complete_then_edit(*args, **kwargs):
        original(*args, **kwargs)
        store.save_analytics(
            game["id"],
            {
                "advisor": preferences["advisor"],
                "time_limit": 6,
                "show_win_chances": True,
            },
        )

    monkeypatch.setattr(service, "wait", complete_then_edit)
    result = synchronous(client, game)
    assert result.status_code == 409, result.text
    assert "advisor changed" in result.json()["detail"]
    assert store.analytics(game["id"])["revision"] == preferences["revision"] + 1


def test_different_positions_are_isolated_when_latest_advice_supersedes_replay(
    client, neural_advisor, monkeypatch
):
    entered, release = threading.Event(), threading.Event()
    completed = []

    def search(state, config, *, diagnostics, progress, **kwargs):
        chosen = legal_moves(state)[0]
        diagnostics.update(value=0.25, simulations=1)
        if state.ply == 0:
            progress(ai.analysis_from_search(state, config, chosen, diagnostics))
            entered.set()
            assert release.wait(5)
            diagnostics["simulations"] = 2
            progress(ai.analysis_from_search(state, config, chosen, diagnostics))
        completed.append(state.ply)
        diagnostics["simulations"] = config["iterations"]
        return {chosen: 1.0}

    monkeypatch.setattr(ai, "mcts_policy", search)
    game = move(client, create(client))
    selected = settings(client, game, neural_advisor)
    try:
        old = client.post(
            f"/api/games/{game['id']}/analysis",
            json={
                "version": game["version"],
                "cursor": 0,
                "historical": True,
                "background": True,
            },
        ).json()
        assert entered.wait(5)
        new = synchronous(client, game)
        assert new.status_code == 200, new.text
        current = new.json()
        assert current["cursor"] == current["ply"] == 1
        assert current["position_key"] != old["position_key"]
        assert current["advisor_revision"] == selected["revision"]
        assert current["evaluations"][0]["search_seed"] == 52
    finally:
        release.set()
    wait_for(lambda: not client.app.state.analyses.tasks)
    assert completed == [1]
    retained = client.app.state.store.advisor_analysis(
        game["id"], old["position_key"], selected["revision"]
    )
    assert retained["partial"] and retained["cursor"] == retained["ply"] == 0
    assert synchronous(client, game).json() == current


def test_pending_historical_snapshot_uses_requested_position_before_first_progress(
    client, neural_advisor, monkeypatch
):
    entered, release = threading.Event(), threading.Event()

    def evaluate(state, config, **kwargs):
        entered.set()
        assert release.wait(5)
        return report(state)

    monkeypatch.setattr(ai, "analyze_position", evaluate)
    game = move(client, create(client))
    selected = settings(client, game, neural_advisor)
    path = f"/api/games/{game['id']}/analysis"
    request = {"version": game["version"], "cursor": 0, "historical": True, "background": True}
    try:
        pending = client.post(path, json=request).json()
        assert entered.wait(5)
        assert pending["status"] == "pending" and pending["evaluations"] == []
        assert pending["cursor"] == pending["ply"] == 0
        assert pending["advisor_revision"] == selected["revision"]
    finally:
        release.set()
    final = wait_for(
        lambda: (
            result
            if (result := client.post(path, json=request).json())["status"] == "complete"
            else None
        )
    )
    assert pending["position_key"] == final["position_key"]


@pytest.mark.parametrize("neural", [False, True])
def test_saved_sync_revision_metadata_covers_cached_missing_and_unavailable_reports(
    client, neural_advisor, monkeypatch, neural
):
    game = create(client)
    selected = settings(client, game, neural_advisor if neural else {"profile_id": "baseline"})
    calls = []

    def evaluate(state, config, **kwargs):
        calls.append(config["algorithm"])
        if neural:
            raise FileNotFoundError("Checkpoint unavailable")
        return report(state)

    monkeypatch.setattr(ai, "analyze_position", evaluate)
    first = synchronous(client, game)
    assert first.status_code == 200, first.text
    assert first.json()["advisor_revision"] == selected["revision"]
    if neural:
        assert first.json()["evaluations"][0]["unavailable_reason"] == "model_unavailable"
    assert synchronous(client, game).json() == first.json()
    assert len(calls) == 1
    game = seek(client, move(client, game), 0)
    updated = settings(client, game, None)
    missing = synchronous(client, game).json()
    assert missing["advisor_revision"] == updated["revision"]
    assert missing["evaluations"] == [] and len(calls) == 1


def test_omitted_saved_settings_keep_legacy_response_shape(client):
    game = create(client)
    first = synchronous(client, game)
    assert first.status_code == 200
    assert "advisor_revision" not in first.json()
    assert set(first.json()) == {
        "cursor",
        "ply",
        "side_to_move",
        "position_key",
        "created_at",
        "evaluations",
        "hint",
    }
    assert synchronous(client, game).json() == first.json()


def test_missing_saved_replay_report_keeps_its_position_and_revision(
    client, neural_advisor, monkeypatch
):
    game = seek(client, move(client, create(client)), 0)
    selected = settings(client, game, neural_advisor)

    def forbidden(*args, **kwargs):
        pytest.fail("Synchronous replay cannot start a neural search")

    monkeypatch.setattr(ai, "analyze_position", forbidden)
    result = synchronous(client, game)
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["cursor"] == body["ply"] == 0
    assert body["position_key"] and body["evaluations"] == []
    assert body["unavailable_reason"] == "not_recorded"
    assert body["advisor_revision"] == selected["revision"]


def test_analysis_admission_is_bounded_even_when_queued_revisions_are_superseded(
    client, monkeypatch
):
    release = threading.Event()
    calls, submissions = [], []
    service = client.app.state.analyses
    original = service.executor.submit

    def submit(*args, **kwargs):
        submissions.append(args)
        return original(*args, **kwargs)

    def evaluate(state, config, *, progress=None):
        calls.append((config["seed"], config["time_limit"]))
        assert release.wait(5)
        return report(state)

    monkeypatch.setattr(service.executor, "submit", submit)
    monkeypatch.setattr(ai, "analyze_position", evaluate)
    monkeypatch.setattr(analysis_service, "MAX_ANALYSIS_TASKS", 3)
    games = [create(client, seed=seed) for seed in (51, 52, 53)]
    for game in games:
        settings(client, game, {"profile_id": "baseline"})
    try:
        for game in games:
            assert analyze(client, game)["status"] == "pending"
        wait_for(lambda: len(calls) == 2)
        for index in range(20):
            settings(client, games[2], {"profile_id": "baseline"}, time_limit=6 + index / 10)
            response = client.post(
                f"/api/games/{games[2]['id']}/analysis",
                json={
                    "version": 0,
                    "cursor": 0,
                    "background": True,
                },
            )
            assert response.status_code == 503
            assert len(service.tasks) <= 3
        assert len(submissions) == 3 and len(calls) == 2
    finally:
        release.set()
    wait_for(lambda: not service.tasks)
    final = complete(client, games[2])
    assert final["evaluations"][0]["config"]["time_limit"] == 7.9
    assert sorted(seed for seed, _ in calls) == [51, 52, 53]
    assert calls[-1][0] == 53


def test_shutdown_cancels_queued_sync_wait_and_does_not_deadlock_workers(client, monkeypatch):
    release = threading.Event()
    calls = []

    def evaluate(state, config, *, progress=None):
        calls.append(config["seed"])
        assert release.wait(5)
        progress(report(state))
        return report(state)

    monkeypatch.setattr(ai, "analyze_position", evaluate)
    service = client.app.state.analyses
    games = [create(client, seed=seed) for seed in (51, 52, 53)]
    for game in games:
        settings(client, game, {"profile_id": "baseline"})
        analyze(client, game)
    wait_for(lambda: len(calls) == 2)
    with ThreadPoolExecutor(max_workers=2) as requests:
        try:
            with monkeypatch.context() as patch:
                joined = observe_join(patch, service)
                waiting = requests.submit(synchronous, client, games[2])
                assert joined.wait(5)
            stopping = requests.submit(service.shutdown)
            assert waiting.result(timeout=5).status_code == 503
        finally:
            release.set()
        stopping.result(timeout=5)
    assert not service.tasks and sorted(calls) == [51, 52]


def test_late_task_cleanup_cannot_cancel_a_replacement_with_the_same_key(client):
    service = client.app.state.analyses
    key = ("game", "position", "advisor", 1)
    old, replacement = Future(), Future()
    # Simulate a retry admitted after an old future finished but before its
    # completion callback acquired the scheduling lock.
    service.tasks[key] = replacement
    service.latest_advisor[key[0]] = key
    service._forget(key, old)
    assert service.active(key)
    assert service.latest_advisor[key[0]] == key
    service._forget(key, replacement)
    assert not service.tasks and not service.latest_advisor
