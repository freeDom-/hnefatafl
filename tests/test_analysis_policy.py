"""Server-owned analysis execution policy and fallback admission boundaries."""

import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from auth_support import AuthenticatedClient as TestClient
from fastapi.testclient import TestClient as RawTestClient
from record_fixtures import historical_game

from hnefatafl import ai
from hnefatafl.analysis_policy import (
    MAX_RATE_BUCKETS,
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
    assert payload["history_keys"] and len(payload["history_keys"]) == 1
    assert "history" not in payload["states"][0]
    assert payload["position_keys"] == [payload["position_key"]]
    assert payload["advisor"]["effective_config"]["time_limit"] == 5
    assert payload["advisor"]["revision"] == 0
    assert payload["advisor"]["requested_config"]["time_limit"] == 0.1
    assert payload["execution"]["mode"] == "browser_only"

    moved = client.post(
        f"/api/games/{game['id']}/moves",
        json={**game["legal_moves"][0], "version": game["version"]},
    ).json()
    context = client.get(
        f"/api/games/{moved['id']}/analysis-context",
        params={"cursor": 1, "version": moved["version"]},
    ).json()
    assert len(context["states"]) == 2
    assert len(context["history_keys"]) == 2
    assert context["cursor"] == 1 and context["version"] == moved["version"]
    stale = client.get(
        f"/api/games/{moved['id']}/analysis-context",
        params={"cursor": 1, "version": game["version"]},
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "analysis_context_stale"
    stale_revision = client.get(
        f"/api/games/{moved['id']}/analysis-context",
        params={"cursor": 1, "version": moved["version"], "revision": 99},
    )
    assert stale_revision.status_code == 409
    assert stale_revision.json()["code"] == "analysis_revision_stale"


def test_concurrent_context_misses_share_one_bounded_cache_entry(monkeypatch, tmp_path):
    app = _app(tmp_path, monkeypatch, HNEFATAFL_ANALYSIS_MODE="browser_only")
    client = TestClient(app)
    game = _game(client)
    url = f"/api/games/{game['id']}/analysis-context"
    params = {"cursor": 0, "version": game["version"], "revision": 0}
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda _: client.get(url, params=params), range(4)))
    assert all(response.status_code == 200 for response in responses)
    cache = app.state.analysis_context_cache
    assert len(cache) == 1
    assert app.state.analysis_context_builds.snapshot()["reconstructions"] == 1
    assert app.state.analysis_context_builds.snapshot()["records"] == 0
    assert all(
        response.json()["position_key"] == responses[0].json()["position_key"]
        for response in responses
    )


def test_analysis_status_is_read_only_and_reports_only_admitted_work(monkeypatch, tmp_path):
    app = _app(tmp_path, monkeypatch, HNEFATAFL_ANALYSIS_MODE="browser_preferred")
    client = TestClient(app)
    game = _game(client)
    status = client.get(
        f"/api/games/{game['id']}/analysis-status",
        params={"cursor": 0, "version": game["version"]},
    )
    assert status.status_code == 200, status.text
    assert status.json()["active"] is False
    assert status.json()["position_key"]

    # The endpoint reports an already-admitted task; it never schedules one.
    monkeypatch.setattr(app.state.analyses, "active", lambda _key: True)
    assert (
        client.get(
            f"/api/games/{game['id']}/analysis-status",
            params={"cursor": 0, "version": game["version"]},
        ).json()["active"]
        is True
    )


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
    # Duplicate release is detectably invalid and cannot drive counters negative
    # or undercount another active reservation.
    admission.release_task(token)
    assert admission.snapshot()["active"] == 0
    assert admission.snapshot()["admitted"] == 2
    assert admission.snapshot()["duplicate_release"] == 1


def test_rate_buckets_expire_without_unbounded_account_state():
    policy = resolve_policy(
        {
            "HNEFATAFL_ANALYSIS_MODE": "browser_preferred",
            "HNEFATAFL_ANALYSIS_REQUESTS_PER_MINUTE": "1",
        }
    )
    clock = {"now": 0.0}
    admission = AnalysisAdmission(policy, clock=lambda: clock["now"])
    for index in range(200):
        admission.limit_request(f"account-{index}")
    assert len(admission.requests_by_account) == 200
    clock["now"] = 61.0
    admission.limit_request("fresh")
    assert len(admission.requests_by_account) == 1
    clock["now"] = 122.0
    admission.limit_request("another")
    assert len(admission.requests_by_account) == 1


def test_rate_bucket_capacity_is_enforced_without_discarding_active_accounts():
    policy = resolve_policy(
        {
            "HNEFATAFL_ANALYSIS_MODE": "browser_preferred",
            "HNEFATAFL_ANALYSIS_REQUESTS_PER_MINUTE": "1",
        }
    )
    admission = AnalysisAdmission(policy, clock=lambda: 100.0)
    for index in range(MAX_RATE_BUCKETS):
        admission.limit_request(f"account-{index}")
    with pytest.raises(AnalysisPolicyError) as capacity:
        admission.limit_request("overflow")
    assert capacity.value.code == "analysis_rate_capacity"
    assert capacity.value.status == 503
    assert len(admission.requests_by_account) == MAX_RATE_BUCKETS
    assert "account-0" in admission.requests_by_account


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


def test_analysis_receipt_tracks_prepared_admission_and_retirement(monkeypatch, tmp_path):
    app = _app(tmp_path, monkeypatch, HNEFATAFL_ANALYSIS_MODE="browser_preferred")
    client = TestClient(app)
    game = _game(client)
    settings = client.get(f"/api/games/{game['id']}/analytics").json()
    created = client.post(
        f"/api/games/{game['id']}/analysis-receipt",
        json={
            "version": game["version"],
            "cursor": game["cursor"],
            "revision": settings["revision"],
        },
    )
    assert created.status_code == 200, created.text
    receipt = created.json()["receipt"]
    params = {
        "version": game["version"],
        "cursor": game["cursor"],
        "revision": settings["revision"],
        "receipt": receipt,
    }
    prepared = client.get(f"/api/games/{game['id']}/analysis-status", params=params).json()
    assert prepared["receipt_state"] == "prepared"
    assert prepared["receipt"] == receipt
    assert prepared["receipt_epoch"] == created.json()["epoch"]

    analysed = client.post(
        f"/api/games/{game['id']}/analysis",
        json={"version": game["version"], "cursor": game["cursor"], "background": True},
        headers={"X-Analysis-Fallback": "1", "X-Analysis-Receipt": receipt},
    )
    assert analysed.status_code == 200, analysed.text
    observed = client.get(f"/api/games/{game['id']}/analysis-status", params=params).json()
    assert observed["receipt_state"] in {"active", "completed"}
    assert observed["receipt_terminal"] == (observed["receipt_state"] == "completed")
    assert observed["game_id"] == game["id"]
    assert observed["advisor_revision"] == settings["revision"]

    unknown = client.get(
        f"/api/games/{game['id']}/analysis-status",
        params={**params, "receipt": "not-a-real-receipt"},
    ).json()
    assert unknown["receipt_state"] == "unknown"


def test_analysis_receipt_is_bound_to_account_position_and_revision(monkeypatch, tmp_path):
    app = _app(tmp_path, monkeypatch, HNEFATAFL_ANALYSIS_MODE="browser_preferred")
    client = TestClient(app)
    game = _game(client)
    settings = client.get(f"/api/games/{game['id']}/analytics").json()
    created = client.post(
        f"/api/games/{game['id']}/analysis-receipt",
        json={
            "version": game["version"],
            "cursor": game["cursor"],
            "revision": settings["revision"],
        },
    )
    assert created.status_code == 200, created.text
    receipt = created.json()["receipt"]
    stale = client.get(
        f"/api/games/{game['id']}/analysis-status",
        params={
            "version": game["version"],
            "cursor": game["cursor"],
            "revision": settings["revision"] + 1,
            "receipt": receipt,
        },
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "analysis_revision_stale"
    other = _game(client)
    mismatch = client.post(
        f"/api/games/{other['id']}/analysis",
        json={"version": other["version"], "cursor": other["cursor"], "background": True},
        headers={"X-Analysis-Receipt": receipt, "X-Analysis-Fallback": "1"},
    )
    assert mismatch.status_code == 409
    assert mismatch.json()["code"] in {
        "analysis_receipt_invalid",
        "analysis_receipt_mismatch",
    }
    # The original receipt was never consumed by the rejected request.
    status = client.get(
        f"/api/games/{game['id']}/analysis-status",
        params={
            "version": game["version"],
            "cursor": game["cursor"],
            "revision": settings["revision"],
            "receipt": receipt,
        },
    ).json()
    assert status["receipt_state"] == "prepared"


def test_analysis_context_build_capacity_rejects_instead_of_corrupting_ownership(
    monkeypatch, tmp_path
):
    app = _app(tmp_path, monkeypatch, HNEFATAFL_ANALYSIS_MODE="browser_only")
    client = TestClient(app)
    game = _game(client)
    registry = app.state.analysis_context_builds
    leases = [registry.build(("blocked", index, 0)) for index in range(registry.max_concurrent)]
    for lease in leases:
        lease.__enter__()
        lease.reconstruct()
    try:
        response = client.get(
            f"/api/games/{game['id']}/analysis-context",
            params={"cursor": 0, "version": game["version"], "revision": 0},
        )
        assert response.status_code == 503
        assert response.json()["code"] == "analysis_context_busy"
        assert all(key[0] == "blocked" for key in registry.records)
    finally:
        for lease in leases:
            lease.__exit__(None, None, None)
    recovered = client.get(
        f"/api/games/{game['id']}/analysis-context",
        params={"cursor": 0, "version": game["version"], "revision": 0},
    )
    assert recovered.status_code == 200, recovered.text
    assert registry.snapshot()["reconstructions"] == 1


def test_cached_context_reads_do_not_require_reconstruction_capacity(monkeypatch, tmp_path):
    app = _app(tmp_path, monkeypatch, HNEFATAFL_ANALYSIS_MODE="browser_only")
    client = TestClient(app)
    game = _game(client)
    params = {"cursor": 0, "version": game["version"], "revision": 0}
    assert client.get(f"/api/games/{game['id']}/analysis-context", params=params).status_code == 200
    registry = app.state.analysis_context_builds
    leases = [registry.build(("blocked", index, 0)) for index in range(registry.max_concurrent)]
    for lease in leases:
        lease.__enter__()
        lease.reconstruct()
    try:
        cached = client.get(f"/api/games/{game['id']}/analysis-context", params=params)
        assert cached.status_code == 200, cached.text
        assert registry.snapshot()["reconstructions"] == 1
    finally:
        for lease in leases:
            lease.__exit__(None, None, None)


def test_analysis_metrics_requires_administrator(monkeypatch, tmp_path):
    app = _app(tmp_path, monkeypatch, HNEFATAFL_ANALYSIS_MODE="browser_preferred")
    client = TestClient(app)
    # The authenticated fixture account is ADMIN; an anonymous request is denied
    # by the session boundary before the handler's permission check.
    anonymous = RawTestClient(app)
    assert anonymous.get("/api/analysis/metrics").status_code == 401
    metrics = client.get("/api/analysis/metrics")
    assert metrics.status_code == 200
    assert metrics.json()["context"]["builds"]["max_records"] == 64
    assert metrics.json()["receipts"]["max_active_per_account"] == 32


def test_synchronous_playing_agent_evaluations_use_task_admission(monkeypatch, tmp_path):
    app = _app(
        tmp_path,
        monkeypatch,
        HNEFATAFL_ANALYSIS_MODE="browser_preferred",
        HNEFATAFL_ANALYSIS_MAX_PER_USER="4",
    )
    client = TestClient(app)
    game = historical_game(
        client,
        ruleset="brandubh",
        seed=53,
        mode="ai-ai",
        players={
            "attackers": {"profile_id": "baseline"},
            "defenders": {"profile_id": "alpha_beta", "config": {"depth": 1, "time_limit": 0.1}},
        },
    )
    admission = app.state.analysis_admission
    original = admission.admit_task
    admitted = []

    def record(account_id):
        token = original(account_id)
        admitted.append(token)
        return token

    monkeypatch.setattr(admission, "admit_task", record)
    response = _analysis(client, game)
    assert response.status_code == 200, response.text
    # Both playing-agent evaluations pass through the same task admission as
    # advisor/background work; no direct synchronous path bypasses it.
    assert len(admitted) >= 2
    assert admission.snapshot()["active"] == 0
    assert admission.snapshot()["admitted"] >= 2


def test_synchronous_playing_agent_admission_rejection_is_structured(monkeypatch, tmp_path):
    app = _app(tmp_path, monkeypatch, HNEFATAFL_ANALYSIS_MODE="browser_preferred")
    client = TestClient(app)
    game = historical_game(
        client,
        ruleset="brandubh",
        seed=54,
        mode="ai-ai",
        players={"attackers": {"profile_id": "baseline"}, "defenders": {"profile_id": "baseline"}},
    )

    def reject(_account_id):
        raise AnalysisPolicyError(
            "Analysis capacity is busy. Retry shortly.",
            code="analysis_overloaded",
            status=503,
            retry_after=1,
        )

    monkeypatch.setattr(app.state.analysis_admission, "admit_task", reject)
    response = _analysis(client, game)
    assert response.status_code == 503, response.text
    assert response.json()["code"] == "analysis_overloaded"
    assert response.headers["retry-after"] == "1"
