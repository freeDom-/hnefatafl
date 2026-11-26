"""Server-owned analysis execution policy and fallback admission boundaries."""

import time

import pytest
from auth_support import AuthenticatedClient as TestClient
from fastapi.testclient import TestClient as RawTestClient
from record_fixtures import historical_game

from hnefatafl import ai
from hnefatafl.analysis_policy import (
    AnalysisAdmission,
    AnalysisPolicyError,
    resolve_policy,
)
from hnefatafl.auth import BROWSER_ANALYSIS_POLICY, CONTENT_SECURITY_POLICY, SecuritySettings
from hnefatafl.server import create_app

ADVISOR = {
    "name": "Jarl advisor",
    "profile_id": "alpha_beta",
    "config": {"algorithm": "alpha_beta", "depth": 1, "time_limit": 0.1},
}


def _app(tmp_path, monkeypatch, **environment):
    for key, value in environment.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    return create_app(tmp_path)


def _game(client: TestClient):
    return historical_game(
        client,
        ruleset="brandubh",
        seed=51,
        advisor=ADVISOR,
    )


def _analysis(client: TestClient, game: dict, **extra):
    return client.post(
        f"/api/games/{game['id']}/analysis",
        json={"version": game["version"], "cursor": game["cursor"], **extra},
    )


def test_policy_matrix_and_legacy_precedence():
    legacy_off = resolve_policy({"HNEFATAFL_BROWSER_ANALYSIS_ENABLED": "0"})
    assert legacy_off.mode == "server_only"
    assert legacy_off.browser_delivery is False
    assert legacy_off.server_computation is True

    legacy_on = resolve_policy({"HNEFATAFL_BROWSER_ANALYSIS_ENABLED": "1"})
    assert legacy_on.mode == "server_only"
    assert legacy_on.browser_delivery is True
    assert legacy_on.server_computation is True

    for mode in ("browser_only", "browser_preferred", "server_only", "disabled"):
        policy = resolve_policy(
            {
                "HNEFATAFL_ANALYSIS_MODE": mode,
                # The legacy flag must never enable delivery in an explicit
                # server_only/disabled policy.
                "HNEFATAFL_BROWSER_ANALYSIS_ENABLED": "1",
            }
        )
        assert policy.mode == mode
        assert policy.browser_delivery is (mode in {"browser_only", "browser_preferred"})
        assert policy.server_computation is (mode in {"server_only", "browser_preferred"})
        assert policy.fallback is (mode == "browser_preferred")

    explicit = resolve_policy(
        {
            "HNEFATAFL_ANALYSIS_MODE": "browser_preferred",
            "HNEFATAFL_BROWSER_ANALYSIS_ENABLED": "0",
            "HNEFATAFL_ANALYSIS_OFFLINE_SECONDS": "120",
            "HNEFATAFL_ANALYSIS_MAX_PER_USER": "1",
        }
    )
    assert explicit.browser_delivery is True
    assert explicit.offline_seconds == 120
    assert explicit.max_per_user == 1


@pytest.mark.parametrize(
    "environment",
    [
        {"HNEFATAFL_ANALYSIS_MODE": "auto"},
        {"HNEFATAFL_ANALYSIS_MODE": "Local"},
        {"HNEFATAFL_ANALYSIS_MODE": "server_only", "HNEFATAFL_ANALYSIS_WEBGPU": "1"},
        {"HNEFATAFL_ANALYSIS_MODE": "disabled", "HNEFATAFL_ANALYSIS_WEBGPU": "1"},
        {"HNEFATAFL_ANALYSIS_MODE": "browser_only", "HNEFATAFL_ANALYSIS_OFFLINE_SECONDS": "30"},
        {"HNEFATAFL_ANALYSIS_MODE": "browser_only", "HNEFATAFL_ANALYSIS_MAX_PER_USER": "99"},
        {"HNEFATAFL_ANALYSIS_MODE": "browser_only", "HNEFATAFL_BROWSER_ANALYSIS_ENABLED": "maybe"},
    ],
)
def test_invalid_or_contradictory_configuration_is_rejected(environment):
    with pytest.raises(ValueError):
        resolve_policy(environment)


def test_security_settings_use_the_resolved_policy(monkeypatch):
    monkeypatch.setenv("HNEFATAFL_ANALYSIS_MODE", "browser_only")
    monkeypatch.setenv("HNEFATAFL_BROWSER_ANALYSIS_ENABLED", "0")
    settings = SecuritySettings.environment()
    assert settings.analysis_mode == "browser_only"
    assert settings.browser_analysis is True


def test_legacy_enabled_keeps_delivery_and_csp_without_local_execution(monkeypatch, tmp_path):
    monkeypatch.delenv("HNEFATAFL_ANALYSIS_MODE", raising=False)
    app = _app(
        tmp_path,
        monkeypatch,
        HNEFATAFL_BROWSER_ANALYSIS_ENABLED="1",
    )
    client = TestClient(app)
    policy = client.get("/api/health").headers["content-security-policy"]
    assert policy == BROWSER_ANALYSIS_POLICY
    availability = client.get("/api/browser-analysis/availability").json()
    assert availability["analysis_mode"] == "server_only"
    assert availability["execution"]["browser_delivery"] is True
    assert availability["execution"]["browser_execution"] is False


def test_explicit_server_only_overrides_legacy_delivery(monkeypatch, tmp_path):
    app = _app(
        tmp_path,
        monkeypatch,
        HNEFATAFL_ANALYSIS_MODE="server_only",
        HNEFATAFL_BROWSER_ANALYSIS_ENABLED="1",
    )
    client = TestClient(app)
    assert client.get("/api/health").headers["content-security-policy"] == CONTENT_SECURITY_POLICY
    availability = client.get("/api/browser-analysis/availability").json()
    assert availability["analysis_mode"] == "server_only"
    assert availability["enabled"] is False
    assert availability["execution"]["browser_delivery"] is False


@pytest.mark.parametrize("mode", ["browser_only", "disabled"])
def test_no_server_computation_is_scheduled_or_executed(monkeypatch, tmp_path, mode):
    app = _app(tmp_path, monkeypatch, HNEFATAFL_ANALYSIS_MODE=mode)
    client = TestClient(app)
    game = _game(client)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("server analysis was invoked")

    monkeypatch.setattr(ai, "analyze_position", forbidden)
    monkeypatch.setattr(
        app.state.analyses,
        "schedule",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("a server analysis task was scheduled")
        ),
    )
    for extra in ({}, {"background": True}, {"historical": True}):
        response = _analysis(client, game, **extra)
        assert response.status_code == 409, response.text
        assert response.json()["code"] == "analysis_server_disabled"

    # Replay navigation, move validation and ordinary gameplay stay available.
    assert client.get(f"/api/games/{game['id']}").status_code == 200
    replay = client.get(f"/api/games/{game['id']}/replay")
    assert replay.status_code == 200 and replay.json()["validated"] is True
    moved = client.post(
        f"/api/games/{game['id']}/moves",
        json={**game["legal_moves"][0], "version": game["version"]},
    )
    assert moved.status_code == 200, moved.text


def test_disabled_policy_keeps_replay_and_settings_but_suppresses_analysis(monkeypatch, tmp_path):
    app = _app(tmp_path, monkeypatch, HNEFATAFL_ANALYSIS_MODE="disabled")
    client = TestClient(app)
    game = _game(client)
    assert client.get(f"/api/games/{game['id']}/analytics").status_code == 200
    assert _analysis(client, game).status_code == 409


def test_execution_policy_is_exposed_with_existing_settings(monkeypatch, tmp_path):
    app = _app(tmp_path, monkeypatch, HNEFATAFL_ANALYSIS_MODE="browser_preferred")
    client = TestClient(app)
    game = _game(client)
    settings = client.get(f"/api/games/{game['id']}/analytics").json()
    assert settings["execution"]["mode"] == "browser_preferred"
    assert settings["execution"]["fallback"] is True
    saved = client.put(
        f"/api/games/{game['id']}/analytics",
        json={"advisor": ADVISOR, "time_limit": 5, "show_win_chances": True},
    ).json()
    assert saved["execution"]["mode"] == "browser_preferred"


def test_browser_preferred_executes_bounded_fallback_with_same_limits(monkeypatch, tmp_path):
    calls = []

    def analyze(state, config, **kwargs):
        calls.append(config["algorithm"])
        from hnefatafl.engine import legal_moves

        chosen = legal_moves(state)[0]
        details = {"value": 0.25, "completed_depth": 1}
        return ai.analysis_from_search(state, config, chosen, details)

    monkeypatch.setattr(ai, "analyze_position", analyze)
    app = _app(
        tmp_path,
        monkeypatch,
        HNEFATAFL_ANALYSIS_MODE="browser_preferred",
        HNEFATAFL_ANALYSIS_REQUESTS_PER_MINUTE="60",
    )
    client = TestClient(app)
    game = _game(client)
    direct = _analysis(client, game, background=True)
    assert direct.status_code == 200, direct.text
    deadline = time.monotonic() + 5
    while not calls and time.monotonic() < deadline:
        time.sleep(0.02)
    assert calls == ["alpha_beta"]
    # A client-claimed local failure never grants extra capacity; the fallback
    # request is just another constrained server request.
    fallback = client.post(
        f"/api/games/{game['id']}/analysis",
        json={"version": game["version"], "cursor": game["cursor"], "background": True},
        headers={"X-Analysis-Fallback": "1"},
    )
    assert fallback.status_code == 200, fallback.text
    metrics = client.get("/api/analysis/metrics").json()
    assert metrics["policy"]["mode"] == "browser_preferred"
    assert metrics["counters"]["fallback_request"] == 1
    assert metrics["counters"]["direct_request"] >= 1


def test_request_rate_limit_applies_before_any_computation(monkeypatch, tmp_path):
    app = _app(
        tmp_path,
        monkeypatch,
        HNEFATAFL_ANALYSIS_MODE="browser_preferred",
        HNEFATAFL_ANALYSIS_REQUESTS_PER_MINUTE="1",
    )
    client = TestClient(app)
    game = _game(client)
    first = _analysis(client, game, background=True)
    assert first.status_code == 200, first.text
    second = _analysis(client, game, background=True)
    assert second.status_code == 429, second.text
    assert second.json()["code"] == "analysis_rate_limited"
    assert second.headers["retry-after"]


def test_analysis_context_is_authorized_read_only_and_includes_rule_history(monkeypatch, tmp_path):
    app = _app(tmp_path, monkeypatch, HNEFATAFL_ANALYSIS_MODE="browser_only")
    client = TestClient(app)
    game = _game(client)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("context retrieval must never compute")

    monkeypatch.setattr(ai, "analyze_position", forbidden)
    response = client.get(f"/api/games/{game['id']}/analysis-context")
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["schema"] == 1 and payload["game_id"] == game["id"]
    assert len(payload["states"]) == 1
    assert payload["states"][0]["history"]
    assert payload["execution"]["mode"] == "browser_only"

    moved = client.post(
        f"/api/games/{game['id']}/moves",
        json={**game["legal_moves"][0], "version": game["version"]},
    ).json()
    context = client.get(f"/api/games/{moved['id']}/analysis-context", params={"cursor": 1}).json()
    assert len(context["states"]) == 2
    assert len(context["states"][1]["history"]) == 2
    assert context["cursor"] == 1


def test_analysis_context_rejects_a_game_with_analysis_disabled(monkeypatch, tmp_path):
    app = _app(tmp_path, monkeypatch, HNEFATAFL_ANALYSIS_MODE="browser_only")
    client = TestClient(app)
    # Local 1v1 games explicitly have no advisor and no server analysis.
    local = client.post(
        "/api/games/local",
        json={"ruleset": "brandubh", "attackers_label": "A", "defenders_label": "B"},
    )
    assert local.status_code == 201, local.text
    response = client.get(f"/api/games/{local.json()['id']}/analysis-context")
    assert response.status_code == 409
    assert response.json()["code"] == "analysis_not_enabled"


def test_admission_limits_are_global_and_per_account():
    policy = resolve_policy(
        {
            "HNEFATAFL_ANALYSIS_MODE": "browser_preferred",
            "HNEFATAFL_ANALYSIS_MAX_CONCURRENT": "2",
            "HNEFATAFL_ANALYSIS_MAX_PER_USER": "1",
            "HNEFATAFL_ANALYSIS_REQUESTS_PER_MINUTE": "2",
        }
    )
    admission = AnalysisAdmission(policy, clock=lambda: 100.0)
    admission.limit_request("a")
    admission.limit_request("a")
    with pytest.raises(AnalysisPolicyError) as rate:
        admission.limit_request("a")
    assert rate.value.status == 429

    token = admission.admit_task("a")
    with pytest.raises(AnalysisPolicyError) as user:
        admission.admit_task("a")
    assert user.value.code == "analysis_account_busy"
    second = admission.admit_task("b")
    with pytest.raises(AnalysisPolicyError) as global_limit:
        admission.admit_task("c")
    assert global_limit.value.code == "analysis_overloaded"
    admission.release_task(token)
    admission.release_task(second)
    assert admission.snapshot()["active"] == 0
    assert admission.snapshot()["admitted"] == 2


def test_rate_window_resets_and_old_requests_are_pruned():
    policy = resolve_policy(
        {
            "HNEFATAFL_ANALYSIS_MODE": "browser_preferred",
            "HNEFATAFL_ANALYSIS_REQUESTS_PER_MINUTE": "1",
        }
    )
    clock = {"now": 0.0}
    admission = AnalysisAdmission(policy, clock=lambda: clock["now"])
    admission.limit_request("a")
    clock["now"] = 61.0
    admission.limit_request("a")
    assert admission.snapshot().get("rate_rejected", 0) == 0


def test_analysis_metrics_requires_administrator(monkeypatch, tmp_path):
    app = _app(tmp_path, monkeypatch, HNEFATAFL_ANALYSIS_MODE="browser_preferred")
    client = TestClient(app)
    # The authenticated fixture account is ADMIN; an anonymous request is denied
    # by the session boundary before the handler's permission check.
    anonymous = RawTestClient(app)
    assert anonymous.get("/api/analysis/metrics").status_code == 401
    assert client.get("/api/analysis/metrics").status_code == 200
