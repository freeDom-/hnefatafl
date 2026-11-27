"""End-to-end receipt lifecycle evidence for browser fallback analysis."""

from __future__ import annotations

import threading
import time

import pytest
from auth_support import AuthenticatedClient as TestClient
from fastapi.testclient import TestClient as RawClient
from record_fixtures import historical_game
from test_ratings import api_client, verified_login

from hnefatafl.analysis_policy import AnalysisPolicyError
from hnefatafl.engine import Move, State, apply_move, legal_moves, new_game
from hnefatafl.server import create_app

ADVISOR = {
    "name": "Jarl advisor",
    "profile_id": "alpha_beta",
    "config": {"algorithm": "alpha_beta", "depth": 1, "time_limit": 0.1},
}


def _app(tmp_path, monkeypatch):
    monkeypatch.setenv("HNEFATAFL_ANALYSIS_MODE", "browser_preferred")
    return create_app(tmp_path)


def _game(client: TestClient):
    return historical_game(client, ruleset="brandubh", seed=51, advisor=ADVISOR)


def _receipt(client: TestClient, game: dict, *, cursor=None, base="/api/games") -> str:
    settings = client.get(f"{base}/{game['id']}/analytics").json()
    response = client.post(
        f"{base}/{game['id']}/analysis-receipt",
        json={
            "version": game["version"],
            "cursor": game["cursor"] if cursor is None else cursor,
            "revision": settings["revision"],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["receipt"]


def _status(client: TestClient, receipt: str) -> dict:
    response = client.get(f"/api/analysis/receipts/{receipt}")
    assert response.status_code == 200, response.text
    return response.json()


def _result(client: TestClient, receipt: str) -> dict:
    response = client.get(f"/api/analysis/receipts/{receipt}/result")
    assert response.status_code == 200, response.text
    return response.json()


def _wait_terminal(client: TestClient, receipt: str) -> dict:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        status = _status(client, receipt)
        if status["terminal"]:
            return status
        time.sleep(0.02)
    raise AssertionError("receipt did not become terminal")


def test_consumed_but_unscheduled_receipt_stays_outstanding_then_retires(monkeypatch, tmp_path):
    app = _app(tmp_path, monkeypatch)
    client = TestClient(app)
    game = _game(client)
    receipt = _receipt(client, game)
    context = client.get(
        f"/api/games/{game['id']}/analysis-context",
        params={"cursor": game["cursor"], "version": game["version"], "revision": 0},
    ).json()
    position_key = context["position_key"]
    analyses = app.state.analyses
    original = analyses.schedule
    entered = threading.Event()
    release = threading.Event()
    evaluated = threading.Event()
    evaluation_count = 0
    original_evaluate = analyses.evaluate

    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    def evaluate(*args, **kwargs):
        nonlocal evaluation_count
        evaluation_count += 1
        evaluated.set()
        return original_evaluate(*args, **kwargs)

    monkeypatch.setattr(analyses, "schedule", blocked)
    monkeypatch.setattr(analyses, "evaluate", evaluate)
    response: dict = {}

    def submit():
        response["result"] = client.post(
            f"/api/games/{game['id']}/analysis",
            json={"version": game["version"], "cursor": game["cursor"], "background": True},
            headers={"X-Analysis-Fallback": "1", "X-Analysis-Receipt": receipt},
        )

    thread = threading.Thread(target=submit)
    thread.start()
    assert entered.wait(5)
    # Simulate a long pause after consumption but before scheduling. Time alone
    # is not retirement evidence.
    analyses_receipts = app.state.analysis_receipts
    original_clock = analyses_receipts.clock
    analyses_receipts.clock = lambda: original_clock() + 10_000
    status = _status(client, receipt)
    assert status["state"] == "submitted"
    assert status["terminal"] is False

    duplicate_client = TestClient(app)
    duplicate = duplicate_client.post(
        f"/api/games/{game['id']}/analysis",
        json={"version": game["version"], "cursor": game["cursor"], "background": True},
        headers={"X-Analysis-Fallback": "1", "X-Analysis-Receipt": receipt},
    )
    assert duplicate.status_code == 200, duplicate.text
    assert duplicate.json()["status"] == "pending"
    assert evaluated.is_set() is False
    assert evaluation_count == 0
    assert app.state.store.advisor_analysis(game["id"], position_key, 0) is None
    assert app.state.store.game_analysis(game["id"], position_key) is None

    release.set()
    thread.join(10)
    assert response["result"].status_code == 200, response["result"].text
    assert evaluated.wait(5)
    terminal = _wait_terminal(client, receipt)
    assert terminal["state"] == "completed"
    retrieved = _result(client, receipt)
    assert retrieved["status"] == "complete"
    assert retrieved["report"]["evaluations"]
    assert retrieved["report"]["position_key"] == position_key
    # Retrieval is read-only and does not start another evaluation.
    assert evaluation_count == 1
    duplicate_after_terminal = client.post(
        f"/api/games/{game['id']}/analysis",
        json={"version": game["version"], "cursor": game["cursor"], "background": True},
        headers={"X-Analysis-Fallback": "1", "X-Analysis-Receipt": receipt},
    )
    assert duplicate_after_terminal.status_code == 409
    assert duplicate_after_terminal.json()["code"] == "analysis_receipt_terminal"


def test_expired_unsubmitted_receipt_has_no_result_and_admits_nothing(monkeypatch, tmp_path):
    app = _app(tmp_path, monkeypatch)
    client = TestClient(app)
    game = _game(client)
    receipt = _receipt(client, game)
    receipts = app.state.analysis_receipts
    original = receipts.clock
    receipts.clock = lambda: original() + 10_000
    result = _result(client, receipt)
    assert result["status"] == "expired"
    assert result["terminal"] is True
    assert "report" not in result
    response = client.post(
        f"/api/games/{game['id']}/analysis",
        json={"version": game["version"], "cursor": game["cursor"], "background": True},
        headers={"X-Analysis-Fallback": "1", "X-Analysis-Receipt": receipt},
    )
    assert response.status_code == 409
    assert response.json()["code"] == "analysis_receipt_terminal"


def test_admission_rejection_is_terminal_and_blocks_duplicate_submission(monkeypatch, tmp_path):
    app = _app(tmp_path, monkeypatch)
    client = TestClient(app)
    game = _game(client)
    receipt = _receipt(client, game)
    evaluated = threading.Event()

    def reject(_account_id):
        raise AnalysisPolicyError(
            "Analysis capacity is busy. Retry shortly.",
            code="analysis_overloaded",
            status=503,
            retry_after=1,
        )

    original_evaluate = app.state.analyses.evaluate

    def evaluate(*args, **kwargs):
        evaluated.set()
        return original_evaluate(*args, **kwargs)

    monkeypatch.setattr(app.state.analyses, "evaluate", evaluate)
    monkeypatch.setattr(app.state.analysis_admission, "admit_task", reject)
    response = client.post(
        f"/api/games/{game['id']}/analysis",
        json={"version": game["version"], "cursor": game["cursor"], "background": True},
        headers={"X-Analysis-Fallback": "1", "X-Analysis-Receipt": receipt},
    )
    assert response.status_code == 503, response.text
    assert evaluated.is_set() is False
    status = _status(client, receipt)
    assert status["state"] == "failed" and status["terminal"] is True
    result = _result(client, receipt)
    assert result["status"] == "failed"
    duplicate = client.post(
        f"/api/games/{game['id']}/analysis",
        json={"version": game["version"], "cursor": game["cursor"], "background": True},
        headers={"X-Analysis-Fallback": "1", "X-Analysis-Receipt": receipt},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "analysis_receipt_terminal"
    assert evaluated.is_set() is False


def test_cache_hit_retires_without_starting_another_evaluation(monkeypatch, tmp_path):
    app = _app(tmp_path, monkeypatch)
    client = TestClient(app)
    game = _game(client)
    # Produce the cache entry without a receipt first.
    first = client.post(
        f"/api/games/{game['id']}/analysis",
        json={"version": game["version"], "cursor": game["cursor"]},
    )
    assert first.status_code == 200, first.text
    receipt = _receipt(client, game)
    calls = 0
    original_evaluate = app.state.analyses.evaluate

    def evaluate(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original_evaluate(*args, **kwargs)

    monkeypatch.setattr(app.state.analyses, "evaluate", evaluate)
    response = client.post(
        f"/api/games/{game['id']}/analysis",
        json={"version": game["version"], "cursor": game["cursor"], "background": True},
        headers={"X-Analysis-Fallback": "1", "X-Analysis-Receipt": receipt},
    )
    assert response.status_code == 200, response.text
    assert calls == 0
    status = _wait_terminal(client, receipt)
    assert status["state"] == "completed"
    assert _result(client, receipt)["status"] == "complete"


def test_synchronous_duplicate_observes_without_writing_or_evaluating(monkeypatch, tmp_path):
    app = _app(tmp_path, monkeypatch)
    client = TestClient(app)
    game = _game(client)
    receipt = _receipt(client, game)
    context = client.get(
        f"/api/games/{game['id']}/analysis-context",
        params={"cursor": game["cursor"], "version": game["version"], "revision": 0},
    ).json()
    position_key = context["position_key"]
    analyses = app.state.analyses
    original = analyses.wait
    entered = threading.Event()
    release = threading.Event()
    evaluation_count = 0
    original_evaluate = analyses.evaluate

    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    def evaluate(*args, **kwargs):
        nonlocal evaluation_count
        evaluation_count += 1
        return original_evaluate(*args, **kwargs)

    monkeypatch.setattr(analyses, "wait", blocked)
    monkeypatch.setattr(analyses, "evaluate", evaluate)
    primary: dict = {}

    def submit():
        primary["result"] = client.post(
            f"/api/games/{game['id']}/analysis",
            json={"version": game["version"], "cursor": game["cursor"], "background": False},
            headers={"X-Analysis-Fallback": "1", "X-Analysis-Receipt": receipt},
        )

    thread = threading.Thread(target=submit)
    thread.start()
    assert entered.wait(5)
    duplicate_client = TestClient(app)
    duplicate = duplicate_client.post(
        f"/api/games/{game['id']}/analysis",
        json={"version": game["version"], "cursor": game["cursor"], "background": False},
        headers={"X-Analysis-Fallback": "1", "X-Analysis-Receipt": receipt},
    )
    assert duplicate.status_code == 200, duplicate.text
    assert duplicate.json()["status"] == "pending"
    assert evaluation_count == 0
    assert app.state.store.advisor_analysis(game["id"], position_key, 0) is None
    assert app.state.store.game_analysis(game["id"], position_key) is None
    release.set()
    thread.join(10)
    assert primary["result"].status_code == 200, primary["result"].text
    assert evaluation_count == 1
    assert _wait_terminal(client, receipt)["state"] == "completed"


def test_synchronous_analysis_receipt_retires_after_wait(monkeypatch, tmp_path):
    app = _app(tmp_path, monkeypatch)
    client = TestClient(app)
    game = _game(client)
    receipt = _receipt(client, game)
    response = client.post(
        f"/api/games/{game['id']}/analysis",
        json={"version": game["version"], "cursor": game["cursor"], "background": False},
        headers={"X-Analysis-Fallback": "1", "X-Analysis-Receipt": receipt},
    )
    assert response.status_code == 200, response.text
    status = _status(client, receipt)
    assert status["state"] == "completed" and status["terminal"] is True


def test_failed_task_submission_retires_as_failure(monkeypatch, tmp_path):
    app = _app(tmp_path, monkeypatch)
    client = TestClient(app, raise_server_exceptions=False)
    game = _game(client)
    receipt = _receipt(client, game)

    def fail_submit(*_args, **_kwargs):
        raise RuntimeError("executor unavailable")

    monkeypatch.setattr(app.state.analyses.executor, "submit", fail_submit)
    response = client.post(
        f"/api/games/{game['id']}/analysis",
        json={"version": game["version"], "cursor": game["cursor"], "background": True},
        headers={"X-Analysis-Fallback": "1", "X-Analysis-Receipt": receipt},
    )
    assert response.status_code >= 500
    status = _status(client, receipt)
    assert status["state"] == "failed" and status["terminal"] is True


@pytest.mark.parametrize("use_receipt", [False, True], ids=["server-only", "fallback"])
def test_historical_analysis_matches_canonical_replay_after_seek(
    monkeypatch, tmp_path, use_receipt
):
    monkeypatch.setenv(
        "HNEFATAFL_ANALYSIS_MODE", "browser_preferred" if use_receipt else "server_only"
    )
    app = create_app(tmp_path)
    client = TestClient(app)
    game = historical_game(client, ruleset="brandubh", seed=51, advisor=ADVISOR)
    base = f"/api/games/{game['id']}"
    states = [new_game("brandubh")]
    for source, target in [(3, 2), (17, 16), (2, 3), (16, 17)] * 2:
        moved = client.post(
            base + "/moves", json={"version": game["version"], "source": source, "target": target}
        )
        assert moved.status_code == 200, moved.text
        game = moved.json()
        states.append(apply_move(states[-1], Move(source, target)))
    assert game["winner"] == "draw"
    analyzed = []
    real_evaluate = app.state.analyses.evaluate

    def capture(*args, state, **kwargs):
        analyzed.append(state)
        return real_evaluate(*args, state=state, **kwargs)

    monkeypatch.setattr(app.state.analyses, "evaluate", capture)
    # At the real frontier and after rewinding, compare each read against the
    # same replay contract that the browser uses (including the history prefix).
    for stored_cursor, requested in [(8, 7), (2, 8), (2, 2), (2, 4), (8, 8)]:
        if game["cursor"] != stored_cursor:
            game = client.post(
                base + "/seek", json={"version": game["version"], "cursor": stored_cursor}
            ).json()
        assert game["cursor"] == stored_cursor
        path = base + "/analysis"
        body = {
            "version": game["version"],
            "cursor": requested,
            "historical": True,
            "background": True,
        }
        if use_receipt:
            receipt = _receipt(client, game, cursor=requested)
            response = client.post(
                path, json=body, headers={"X-Analysis-Fallback": "1", "X-Analysis-Receipt": receipt}
            )
            assert response.status_code == 200, response.text
            assert _wait_terminal(client, receipt)["state"] == "completed"
            report = _result(client, receipt)["report"]
            context = client.get(base + "/analysis-context", params={"cursor": requested}).json()
            assert report["position_key"] == context["position_key"]
        else:
            deadline = time.monotonic() + 5
            while True:
                response = client.post(path, json=body)
                assert response.status_code == 200, response.text
                report = response.json()
                if report["status"] == "complete":
                    break
                assert time.monotonic() < deadline
                time.sleep(0.02)
        if (stored_cursor, requested) == (8, 8):
            # Returning to the genuine frontier reuses the correct cached result.
            assert len(analyzed) == 4
        else:
            assert analyzed[-1] == states[requested]
        assert report["cursor"] == report["ply"] == requested
        assert report["side_to_move"] == states[requested].turn
        advisor = next(entry for entry in report["evaluations"] if entry["agent_id"] == "advisor")
        assert advisor["search_seed"] == 51 + requested
        assert (advisor["source"] == "terminal") == bool(states[requested].winner)


def _completed_online(monkeypatch, tmp_path, *, board_result=False):
    from uuid import uuid4

    monkeypatch.setenv("HNEFATAFL_ANALYSIS_MODE", "browser_preferred")
    app, archer = api_client(tmp_path)
    verified_login(app, archer, "Archer")
    keeper = RawClient(app)
    verified_login(app, keeper, "Keeper")
    created = archer.post(
        "/api/online/games",
        json={"request_id": str(uuid4()), "ruleset": "brandubh", "side_selection": "attackers"},
    )
    assert created.status_code == 201, created.text
    path = f"/api/online/games/{created.json()['game']['id']}"
    game = keeper.post(path + "/join", json={"version": 0}).json()["game"]
    moves = (
        [Move(3, 2), Move(17, 16), Move(2, 3), Move(16, 17)] * 2
        if board_result
        else legal_moves(State.from_dict(game["position"]))[:1]
    )
    for move in moves:
        player = archer if game["position"]["turn"] == "attackers" else keeper
        moved = player.post(path + "/moves", json={"version": game["version"], **move.to_dict()})
        assert moved.status_code == 200, moved.text
        game = moved.json()["game"]
    if not board_result:
        ended = keeper.post(path + "/concede", json={"version": game["version"]})
        assert ended.status_code == 200, ended.text
        game = ended.json()["game"]
    assert game["status"] == "completed"
    assert game["position"]["winner"] == ("draw" if board_result else None)
    return app, archer, {**game, "cursor": len(game["moves"])}


@pytest.mark.parametrize(
    "background,online", [(True, False), (False, False), (True, True), (False, True)]
)
def test_advisor_change_after_receipt_validation_never_substitutes_settings(
    monkeypatch, tmp_path, background, online
):
    if online:
        app, client, game = _completed_online(monkeypatch, tmp_path)
    else:
        app = _app(tmp_path, monkeypatch)
        client = TestClient(app)
        game = _game(client)
    base = "/api/history/online" if online else "/api/games"
    path = f"{base}/{game['id']}"

    def save(advisor):
        response = client.put(
            path + "/analytics",
            json={"advisor": advisor, "time_limit": 1, "show_win_chances": True},
        )
        assert response.status_code == 200, response.text
        return response.json()["revision"]

    revision = save(ADVISOR)
    receipt = _receipt(client, game, base=base)
    registry = app.state.analysis_receipts
    real_begin = registry.begin_submission
    real_evaluate = app.state.analyses.evaluate
    evaluated = []

    def begin(*args, **kwargs):
        lease = real_begin(*args, **kwargs)
        entered.set()
        assert release.wait(5)
        return lease

    def capture(game, role, agent, settings, *args, **kwargs):
        evaluated.append((role, settings["revision"]))
        return real_evaluate(game, role, agent, settings, *args, **kwargs)

    entered, release = threading.Event(), threading.Event()
    monkeypatch.setattr(registry, "begin_submission", begin)
    monkeypatch.setattr(app.state.analyses, "evaluate", capture)
    responses = []
    thread = threading.Thread(
        target=lambda: responses.append(
            client.post(
                path + "/analysis",
                json={
                    "version": game["version"],
                    "cursor": game["cursor"],
                    "background": background,
                },
                headers={"X-Analysis-Fallback": "1", "X-Analysis-Receipt": receipt},
            )
        )
    )
    thread.start()
    try:
        assert entered.wait(5)
        new_revision = save(
            {**ADVISOR, "name": "Different advisor", "config": {**ADVISOR["config"], "depth": 2}}
        )
    finally:
        release.set()
        thread.join(10)
    assert _wait_terminal(client, receipt)["terminal"] is True
    assert evaluated == []
    assert new_revision != revision
    assert responses[0].status_code == 409
    assert responses[0].json()["code"] == "analysis_revision_stale"
    result = _result(client, receipt)
    assert result["status"] == "superseded" and "report" not in result


def test_receipt_content_access_tracks_current_owner_and_admin_permissions(monkeypatch, tmp_path):
    app = _app(tmp_path, monkeypatch)
    admin = TestClient(app)
    owner = RawClient(app)
    private = verified_login(app, owner, "Private")
    game = _game(owner)
    path = f"/api/games/{game['id']}"

    def complete(actor):
        receipt = _receipt(actor, game)
        response = actor.post(
            path + "/analysis",
            json={"version": game["version"], "cursor": 0},
            headers={"X-Analysis-Fallback": "1", "X-Analysis-Receipt": receipt},
        )
        assert response.status_code == 200, response.text
        assert _result(actor, receipt)["status"] == "complete"
        return receipt

    owners_receipt = complete(owner)
    admins_receipt = complete(admin)
    assert _result(admin, owners_receipt)["status"] == "unknown"
    with app.state.store.connection() as connection:
        connection.execute("UPDATE accounts SET role='USER' WHERE username='Odin'")
    assert _status(admin, admins_receipt)["terminal"] is True
    assert "game_id" not in _status(admin, admins_receipt)
    assert "report" not in _result(admin, admins_receipt)
    assert _result(owner, owners_receipt)["status"] == "complete"
    with app.state.store.connection() as connection:
        connection.execute(
            "UPDATE games SET owner_account_id=NULL WHERE id=? AND owner_account_id=?",
            (game["id"], private["id"]),
        )
    for payload in (_status(owner, owners_receipt), _result(owner, owners_receipt)):
        assert payload["terminal"] is True
        assert not (
            payload.keys() & {"game_id", "report", "hint", "evaluations", "position_key", "ply"}
        )


@pytest.mark.parametrize("partial", [False, True])
def test_retired_advisor_failure_preserves_playing_evidence_and_any_partial(
    monkeypatch, tmp_path, partial
):
    app = _app(tmp_path, monkeypatch)
    client = TestClient(app)
    game = historical_game(
        client,
        ruleset="brandubh",
        mode="human-ai",
        human_side="attackers",
        seed=51,
        advisor=ADVISOR,
    )
    path = f"/api/games/{game['id']}/analysis"
    receipt = _receipt(client, game)
    real_evaluate = app.state.analyses.evaluate
    calls = []

    def compute(game, role, agent, settings, progress, *, state):
        calls.append(role)
        if role == "advisor" and not partial:
            raise RuntimeError("deterministic advisor failure")
        result = real_evaluate(game, role, agent, settings, progress, state=state)
        if role == "advisor":
            progress(result)
            raise RuntimeError("failure after useful advisor progress")
        return result

    monkeypatch.setattr(app.state.analyses, "evaluate", compute)
    response = client.post(
        path,
        json={"version": game["version"], "cursor": 0, "background": True},
        headers={"X-Analysis-Fallback": "1", "X-Analysis-Receipt": receipt},
    )
    assert response.status_code == 200, response.text
    assert _wait_terminal(client, receipt)["terminal"] is True
    result = _result(client, receipt)
    assert result["status"] == ("partial" if partial else "failed")
    assert result["report"]["status"] == ("partial" if partial else "unavailable")
    evaluations = {entry["agent_id"]: entry for entry in result["report"]["evaluations"]}
    assert evaluations["defenders"]["source"] != "unavailable"
    assert (evaluations["advisor"]["source"] != "unavailable") == partial
    # Ordinary polling and game reloads preserve both the advisor outcome and
    # the independent playing-agent evidence.
    for _ in range(3):
        reply = client.post(
            path, json={"version": game["version"], "cursor": 0, "background": True}
        )
        assert reply.json()["status"] == ("partial" if partial else "unavailable")
        # The receipt view can reuse a stored rune, while the legacy view resolves it again.
        assert [
            {key: value for key, value in entry.items() if key != "rune"}
            for entry in reply.json()["evaluations"]
        ] == [
            {key: value for key, value in entry.items() if key != "rune"}
            for entry in result["report"]["evaluations"]
        ]
        assert reply.json()["hint"] == result["report"]["hint"]
        assert not app.state.analyses.tasks
    assert (
        client.get(f"/api/games/{game['id']}").json()["analysis"]["status"]
        == reply.json()["status"]
    )
    assert sorted(calls) == ["advisor", "defenders"]
    if partial:
        key = result["report"]["position_key"]
        stored_playing = app.state.store.game_analysis(game["id"], key)
        retained = client.post(path, json={"version": game["version"], "cursor": 0})
        assert retained.json()["status"] == "partial"
        monkeypatch.setattr(app.state.analyses, "evaluate", real_evaluate)
        retry = _receipt(client, game)
        submitted = client.post(
            path,
            json={"version": game["version"], "cursor": 0, "retry_revision": 0},
            headers={"X-Analysis-Receipt": retry},
        )
        assert submitted.status_code == 200
        assert _wait_terminal(client, retry)["terminal"]
        assert _result(client, retry)["status"] == "complete"
        assert app.state.store.game_analysis(game["id"], key) == stored_playing


@pytest.mark.parametrize("with_receipt", [False, True], ids=["server-only", "fallback"])
def test_explicit_retry_replaces_retired_partial_at_same_replay_revision(
    monkeypatch, tmp_path, with_receipt
):
    app = _app(tmp_path, monkeypatch) if with_receipt else create_app(tmp_path)
    with TestClient(app) as client:
        game = _game(client)
        base = f"/api/games/{game['id']}"
        for source, target in [(3, 2), (17, 16), (2, 3), (16, 17)] * 2:
            move = client.post(
                base + "/moves",
                json={"version": game["version"], "source": source, "target": target},
            )
            assert move.status_code == 200, move.text
            game = move.json()
        assert game["recorded_winner"] == "draw"
        body = {"version": game["version"], "cursor": 0, "historical": True, "background": True}
        store, service = app.state.store, app.state.analyses
        real_evaluate = service.evaluate
        entered, release = threading.Event(), threading.Event()
        first_progress, retire_first = threading.Event(), threading.Event()
        calls = []

        def compute(game, role, agent, settings, progress, *, state):
            calls.append((role, settings["revision"]))
            result = real_evaluate(game, role, agent, settings, lambda _: None, state=state)
            if len(calls) <= 2:
                progress({**result, "completed_depth": 2 if len(calls) == 1 else 1})
                if len(calls) == 1:
                    first_progress.set()
                    assert retire_first.wait(5)
                raise RuntimeError("deterministic interrupted search")
            entered.set()
            assert release.wait(5)
            return result

        monkeypatch.setattr(service, "evaluate", compute)

        def submit(*, retry=False, receipt=None):
            headers = {"X-Analysis-Fallback": "1", "X-Analysis-Receipt": receipt} if receipt else {}
            return client.post(
                base + "/analysis",
                json={**body, **({"retry_revision": 0} if retry else {})},
                headers=headers,
            )

        def wait_tasks():
            deadline = time.monotonic() + 5
            while service.tasks:
                assert time.monotonic() < deadline
                time.sleep(0.02)

        def credits():
            with store.connection() as c:
                return c.execute("SELECT count(*) FROM replay_analysis_completions").fetchone()[0]

        first_receipt = _receipt(client, game, cursor=0) if with_receipt else None
        first = submit(receipt=first_receipt)
        assert first.status_code == 200, first.text
        key = first.json()["position_key"]
        try:
            assert first_progress.wait(5)
            assert submit().json()["status"] == "pending"
            other = _receipt(client, game, cursor=0) if with_receipt else None
            assert submit(retry=True, receipt=other).json()["status"] == "pending"
            if other:
                assert _status(client, other)["state"] == "active"
            assert calls == [("advisor", 0)] and credits() == 0
        finally:
            retire_first.set()
        wait_tasks()
        if first_receipt:
            assert _wait_terminal(client, first_receipt)["terminal"]
            assert _result(client, first_receipt)["status"] == "partial"
            assert submit(retry=True, receipt=first_receipt).status_code == 409
        checkpoint = store.advisor_analysis(game["id"], key, 0)
        assert checkpoint["partial"] and checkpoint["retired_partial"]
        for _ in range(3):
            assert submit().status_code == 200
        assert calls == [("advisor", 0)] and credits() == 0

        second_receipt = _receipt(client, game, cursor=0) if with_receipt else None
        failed_retry = submit(retry=True, receipt=second_receipt)
        assert failed_retry.status_code == 200, failed_retry.text
        wait_tasks()
        assert len(calls) == 2
        assert store.advisor_analysis(game["id"], key, 0) == checkpoint
        if second_receipt:
            assert _wait_terminal(client, second_receipt)["terminal"]
            assert _result(client, second_receipt)["status"] == "partial"
        assert credits() == 0

        receipt = _receipt(client, game, cursor=0) if with_receipt else None
        duplicate = _receipt(client, game, cursor=0) if with_receipt else None
        try:
            assert submit(retry=True, receipt=receipt).status_code == 200
            assert entered.wait(5)
            observing = submit(retry=True, receipt=duplicate)
            assert observing.json()["status"] == "pending"
            assert submit().json()["status"] == "pending"
            if duplicate:
                assert _status(client, duplicate)["state"] == "active"
            assert calls == [("advisor", 0)] * 3
            assert credits() == 0
        finally:
            release.set()
        wait_tasks()
        completed = submit().json()
        assert completed["status"] == "complete"
        assert completed["position_key"] == key and completed["advisor_revision"] == 0
        for finished in (receipt, duplicate):
            if finished:
                assert _wait_terminal(client, finished)["terminal"]
                assert _result(client, finished)["report"]["status"] == "complete"
        report = store.advisor_analysis(game["id"], key, 0)
        assert not report["partial"] and credits() == 1
        assert submit(retry=True).json()["status"] == "complete"
        assert calls == [("advisor", 0)] * 3
        assert store.advisor_analysis(game["id"], key, 0) == report


def test_terminal_receipts_stay_inspectable_after_game_changes_or_deletion(monkeypatch, tmp_path):
    app = _app(tmp_path, monkeypatch)
    client = TestClient(app)
    game = _game(client)
    receipt = _receipt(client, game)
    path = f"/api/games/{game['id']}"
    submitted = client.post(
        path + "/analysis",
        json={"version": game["version"], "cursor": 0},
        headers={"X-Analysis-Fallback": "1", "X-Analysis-Receipt": receipt},
    )
    assert submitted.status_code == 200
    assert _result(client, receipt)["status"] == "complete"
    move = client.post(path + "/moves", json={"version": game["version"], **game["legal_moves"][0]})
    assert move.status_code == 200, move.text
    retired = _result(client, receipt)
    assert retired["terminal"] and retired["status"] == "superseded" and "report" not in retired
    deleted = client.delete(path, params={"version": move.json()["version"]})
    assert deleted.status_code == 200, deleted.text
    assert _status(client, receipt)["terminal"]
    retired = _result(client, receipt)
    assert retired["terminal"] and retired["status"] == "unavailable" and "report" not in retired


@pytest.mark.parametrize("board_result", [False, True], ids=["concession", "board-draw"])
def test_completed_online_receipt_uses_canonical_position_and_participant_scope(
    monkeypatch, tmp_path, board_result
):
    app, client, game = _completed_online(monkeypatch, tmp_path, board_result=board_result)
    base = "/api/history/online"
    path = f"{base}/{game['id']}"
    saved = client.put(path + "/analytics", json={"advisor": ADVISOR, "time_limit": 1})
    assert saved.status_code == 200, saved.text
    revision = saved.json()["revision"]
    cursor = game["cursor"]
    receipt = _receipt(client, game, base=base)
    context = client.get(path + "/analysis-context", params={"cursor": cursor}).json()
    analyzed = []
    real_evaluate = app.state.analyses.evaluate

    def capture(*args, state, **kwargs):
        analyzed.append(state)
        return real_evaluate(*args, state=state, **kwargs)

    monkeypatch.setattr(app.state.analyses, "evaluate", capture)
    submitted = client.post(
        path + "/analysis",
        json={"version": game["version"], "cursor": cursor, "background": True},
        headers={"X-Analysis-Fallback": "1", "X-Analysis-Receipt": receipt},
    )
    assert submitted.status_code == 200, submitted.text
    assert _wait_terminal(client, receipt)["state"] == "completed"
    expected = new_game("brandubh")
    for move in game["moves"]:
        expected = apply_move(expected, Move(move["source"], move["target"]))
    assert analyzed == [expected]
    assert expected.winner == ("draw" if board_result else None)

    # No game reconstruction, search or replay-credit write is involved in reading.
    def forbidden(*_args, **_kwargs):
        raise AssertionError("receipt lookup rebuilt a position or attempted replay credit")

    monkeypatch.setattr(app.state.store, "record_replay_analysis", forbidden)
    monkeypatch.setattr(app.state.store, "get_game", forbidden)
    monkeypatch.setattr("hnefatafl.server.apply_move", forbidden)
    result = _result(client, receipt)
    assert result["status"] == "complete"
    assert result["report"]["position_key"] == context["position_key"]
    assert result["report"]["ply"] == cursor
    assert result["report"]["advisor_revision"] == revision
    with app.state.store.connection() as c:
        assert c.execute("SELECT count(*) FROM replay_analysis_completions").fetchone()[0] == 1
    visitor = RawClient(app)
    verified_login(app, visitor, "Visitor")
    assert _result(visitor, receipt)["status"] == "unknown"
    unauthorized = visitor.post(
        path + "/analysis-receipt",
        json={
            "cursor": cursor,
            "version": game["version"],
            "revision": revision,
        },
    )
    assert unauthorized.status_code == 404


def test_retired_read_with_no_requested_advisor_result_is_unavailable(monkeypatch, tmp_path):
    app = _app(tmp_path, monkeypatch)
    client = TestClient(app)
    game = _game(client)
    receipt = _receipt(client, game)
    submitted = client.post(
        f"/api/games/{game['id']}/analysis",
        json={"version": game["version"], "cursor": 0, "historical": True},
        headers={"X-Analysis-Fallback": "1", "X-Analysis-Receipt": receipt},
    )
    assert submitted.status_code == 200, submitted.text
    assert _status(client, receipt)["terminal"]
    result = _result(client, receipt)
    assert result["status"] == "unavailable" and "report" not in result


@pytest.mark.parametrize("changed,retry", [("advisor", False), ("game", False), ("advisor", True)])
def test_running_advisor_can_retire_as_superseded_with_reason(
    monkeypatch, tmp_path, changed, retry
):
    app = _app(tmp_path, monkeypatch)
    client = TestClient(app)
    game = _game(client)
    base = f"/api/games/{game['id']}"
    entered, release = threading.Event(), threading.Event()
    real_evaluate = app.state.analyses.evaluate
    invocations = 0

    def compute(game, role, agent, settings, progress, *, state):
        nonlocal invocations
        invocations += 1
        if retry and invocations == 1:
            progress(real_evaluate(game, role, agent, settings, progress, state=state))
            raise RuntimeError("deterministic interruption before retry")
        entered.set()
        assert release.wait(5)
        return real_evaluate(game, role, agent, settings, progress, state=state)

    monkeypatch.setattr(app.state.analyses, "evaluate", compute)
    body = {"version": game["version"], "cursor": 0, "background": True}
    checkpoint = None
    if retry:
        initial = _receipt(client, game)
        first = client.post(base + "/analysis", json=body, headers={"X-Analysis-Receipt": initial})
        assert first.status_code == 200
        assert _wait_terminal(client, initial)["terminal"]
        checkpoint = app.state.store.advisor_analysis(game["id"], first.json()["position_key"], 0)
        assert checkpoint["retired_partial"]
        body["retry_revision"] = 0
    receipt = _receipt(client, game)
    try:
        submitted = client.post(
            base + "/analysis",
            json=body,
            headers={"X-Analysis-Fallback": "1", "X-Analysis-Receipt": receipt},
        )
        assert submitted.status_code == 200, submitted.text
        assert entered.wait(5)
        assert _status(client, receipt)["state"] == "active"
        if changed == "advisor":
            saved = client.put(base + "/analytics", json={"advisor": ADVISOR, "time_limit": 2})
        else:
            saved = client.post(
                base + "/moves", json={"version": game["version"], **game["legal_moves"][0]}
            )
        assert saved.status_code == 200, saved.text
    finally:
        release.set()
    assert _wait_terminal(client, receipt)["terminal"]
    result = _result(client, receipt)
    assert result["status"] == "superseded" and "report" not in result
    assert result["code"] == (
        "analysis_revision_stale" if changed == "advisor" else "analysis_context_stale"
    )
    if changed == "advisor":
        key = client.get(base + "/analysis-context").json()["position_key"]
        assert app.state.store.advisor_analysis(game["id"], key, 0) == checkpoint
        if retry:
            stale = client.post(base + "/analysis", json=body)
            assert stale.status_code == 409 and stale.json()["code"] == "analysis_revision_stale"
    assert invocations == (2 if retry else 1)
