"""Deterministic lifecycle tests for bounded fallback admission receipts."""

from __future__ import annotations

import pytest

from hnefatafl.analysis_policy import AnalysisPolicyError
from hnefatafl.analysis_receipts import AnalysisReceipts


def _create(registry: AnalysisReceipts, **overrides) -> str:
    values = {
        "account_id": "a",
        "game_id": "g",
        "version": 1,
        "cursor": 0,
        "revision": 2,
        "position_key": "key",
    }
    values.update(overrides)
    return registry.create(**values)["receipt"]


def _begin(registry: AnalysisReceipts, identifier: str, **overrides):
    values = {
        "identifier": identifier,
        "account_id": "a",
        "game_id": "g",
        "version": 1,
        "cursor": 0,
        "revision": 2,
        "position_key": "key",
    }
    values.update(overrides)
    return registry.begin_submission(**values)


def test_prepared_expiry_is_terminal_and_not_admissible():
    clock = {"now": 0.0}
    wall = {"now": 1000.0}
    registry = AnalysisReceipts(
        clock=lambda: clock["now"], wall_clock=lambda: wall["now"], ttl_seconds=5.0
    )
    identifier = _create(registry)
    clock["now"] = 10.0
    status = registry.status_for_account(identifier, "a")
    assert status["state"] == "expired" and status["terminal"] is True
    with pytest.raises(AnalysisPolicyError) as terminal:
        _begin(registry, identifier)
    assert terminal.value.code == "analysis_receipt_terminal"


def test_submitted_outstanding_is_not_retired_by_time():
    clock = {"now": 0.0}
    wall = {"now": 1000.0}
    registry = AnalysisReceipts(
        clock=lambda: clock["now"], wall_clock=lambda: wall["now"], ttl_seconds=5.0
    )
    identifier = _create(registry)
    lease = _begin(registry, identifier)
    assert lease.primary is True
    clock["now"] = 600.0
    wall["now"] = 1600.0
    status = registry.status_for_account(identifier, "a")
    assert status["state"] == "submitted" and status["terminal"] is False
    duplicate = _begin(registry, identifier)
    assert duplicate.primary is False
    duplicate.finalize()
    assert registry.status_for_account(identifier, "a")["state"] == "submitted"
    lease.finalize()
    assert registry.status_for_account(identifier, "a")["state"] == "completed"


def test_terminal_receipt_cannot_admit_more_work():
    registry = AnalysisReceipts()
    identifier = _create(registry)
    lease = _begin(registry, identifier)
    lease.finalize()
    assert registry.status_for_account(identifier, "a")["terminal"] is True
    with pytest.raises(AnalysisPolicyError) as terminal:
        _begin(registry, identifier)
    assert terminal.value.code == "analysis_receipt_terminal"


def test_task_lifetime_keeps_receipt_active_until_completion():
    registry = AnalysisReceipts()
    identifier = _create(registry)
    lease = _begin(registry, identifier)
    lease.track()
    lease.finalize()
    assert registry.status_for_account(identifier, "a")["state"] == "active"
    lease.complete(False)
    status = registry.status_for_account(identifier, "a")
    assert status["state"] == "failed" and status["terminal"] is True


def test_cache_result_without_task_is_terminal_completion():
    registry = AnalysisReceipts()
    identifier = _create(registry)
    lease = _begin(registry, identifier)
    lease.result_available()
    lease.finalize()
    assert registry.status_for_account(identifier, "a")["state"] == "completed"


def test_admission_failure_is_terminal_failure():
    registry = AnalysisReceipts()
    identifier = _create(registry)
    lease = _begin(registry, identifier)
    lease.fail()
    lease.finalize()
    status = registry.status_for_account(identifier, "a")
    assert status["state"] == "failed" and status["terminal"] is True
    with pytest.raises(AnalysisPolicyError):
        _begin(registry, identifier)


def test_completed_receipts_do_not_consume_active_capacity():
    registry = AnalysisReceipts(max_active=1, max_active_per_account=1, max_terminal=64)
    identifier = _create(registry)
    lease = _begin(registry, identifier)
    lease.finalize()
    assert registry.snapshot()["active"] == 0
    assert registry.snapshot()["terminal"] == 1
    replacement = _create(registry, game_id="g2")
    assert replacement


def test_more_than_old_terminal_per_account_limits_remain_queryable():
    registry = AnalysisReceipts(max_terminal=256, max_terminal_per_account=128)
    identifiers = []
    for index in range(40):
        identifier = _create(registry, game_id=f"g{index}")
        lease = _begin(registry, identifier, game_id=f"g{index}")
        lease.finalize()
        identifiers.append(identifier)
    assert all(
        registry.status_for_account(identifier, "a")["terminal"] for identifier in identifiers
    )
    assert registry.snapshot()["active"] == 0


def test_terminal_cleanup_advances_reconciled_before_without_evicting_active():
    clock = {"now": 0.0}
    wall = {"now": 1000.0}
    registry = AnalysisReceipts(
        clock=lambda: clock["now"],
        wall_clock=lambda: wall["now"],
        terminal_retention_seconds=10.0,
        max_terminal=2,
    )
    active = _create(registry, game_id="active")
    active_lease = _begin(registry, active, game_id="active")
    first = _create(registry, game_id="first")
    first_created = registry.status_for_account(first, "a")["created_at"]
    first_lease = _begin(registry, first, game_id="first")
    first_lease.finalize()
    wall["now"] += 1
    second = _create(registry, game_id="second")
    second_lease = _begin(registry, second, game_id="second")
    second_lease.finalize()
    active_lease.finalize()
    wall["now"] += 20
    third = _create(registry, game_id="third")
    third_lease = _begin(registry, third, game_id="third")
    third_lease.finalize()
    unknown = registry.status_for_account(first, "a")
    assert unknown["state"] == "unknown"
    assert unknown["reconciled_before"] >= first_created


def test_terminal_evidence_capacity_is_independent_of_active_admission():
    registry = AnalysisReceipts(max_active=4, max_terminal=512, max_terminal_per_account=64)
    identifiers = []
    for index in range(300):
        account = f"account-{index % 12}"
        identifier = _create(registry, account_id=account, game_id=f"g{index}")
        lease = _begin(registry, identifier, account_id=account, game_id=f"g{index}")
        lease.finalize()
        identifiers.append((identifier, account))
    assert registry.snapshot()["active"] == 0
    assert all(
        registry.status_for_account(identifier, account)["terminal"]
        for identifier, account in identifiers
    )
    # Active admission was not consumed by terminal evidence.
    assert _create(registry, account_id="fresh", game_id="fresh")


def test_receipts_are_account_bound_and_unknown_is_not_terminal_evidence():
    registry = AnalysisReceipts()
    identifier = _create(registry)
    other = registry.status_for_account(identifier, "b")
    assert other["state"] == "unknown" and other["terminal"] is False
    missing = registry.status_for_account("missing", "a")
    assert missing["state"] == "unknown" and missing["terminal"] is False


def test_active_capacity_is_enforced_without_evicting_pending_work():
    registry = AnalysisReceipts(max_active=1, max_active_per_account=2)
    identifier = _create(registry)
    lease = _begin(registry, identifier)
    with pytest.raises(AnalysisPolicyError) as capacity:
        _create(registry, game_id="g2")
    assert capacity.value.status == 503
    assert registry.status_for_account(identifier, "a")["state"] == "submitted"
    lease.finalize()
    assert _create(registry, game_id="g2")
