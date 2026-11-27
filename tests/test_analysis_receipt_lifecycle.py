"""End-to-end receipt lifecycle evidence for browser fallback analysis."""

from __future__ import annotations

import threading
import time

from auth_support import AuthenticatedClient as TestClient
from record_fixtures import historical_game

from hnefatafl.analysis_policy import AnalysisPolicyError
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


def _receipt(client: TestClient, game: dict) -> str:
    settings = client.get(f"/api/games/{game['id']}/analytics").json()
    response = client.post(
        f"/api/games/{game['id']}/analysis-receipt",
        json={
            "version": game["version"],
            "cursor": game["cursor"],
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
        json={"version": game["version"], "cursor": game["cursor"], "background": True},
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
