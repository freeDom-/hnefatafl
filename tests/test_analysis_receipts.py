"""Deterministic tests for bounded browser-fallback admission receipts."""

from __future__ import annotations

import pytest

from hnefatafl.analysis_policy import AnalysisPolicyError
from hnefatafl.analysis_receipts import ACTIVE_GRACE_SECONDS, AnalysisReceipts


def receipt(
    registry: AnalysisReceipts,
    *,
    account: str = "a",
    game: str = "g",
    version: int = 1,
    cursor: int = 0,
    revision: int = 2,
    position: str = "key",
) -> str:
    return registry.create(account, game, version, cursor, revision, position)["receipt"]


def test_prepared_active_retired_and_expired_are_distinct():
    clock = {"now": 0.0}
    registry = AnalysisReceipts(clock=lambda: clock["now"], ttl_seconds=10.0)
    pending = receipt(registry)
    status = registry.status(pending, "a", "g", 1, 0, 2, False)
    assert status["state"] == "prepared"

    registry.consume(pending, "a", "g", 1, 0, 2, "key")
    assert registry.status(pending, "a", "g", 1, 0, 2, True)["state"] == "active"
    clock["now"] = ACTIVE_GRACE_SECONDS + 0.1
    assert registry.status(pending, "a", "g", 1, 0, 2, True)["state"] == "active"
    assert registry.status(pending, "a", "g", 1, 0, 2, False)["state"] == "retired"

    expired = receipt(registry, position="other")
    clock["now"] = 30.0
    assert registry.status(expired, "a", "g", 1, 0, 2, False)["state"] == "expired"
    with pytest.raises(AnalysisPolicyError) as late:
        registry.consume(expired, "a", "g", 1, 0, 2, "other")
    assert late.value.code == "analysis_receipt_expired"


def test_missing_or_mismatched_status_is_unknown_never_inactive():
    clock = {"now": 0.0}
    registry = AnalysisReceipts(clock=lambda: clock["now"])
    missing = registry.status("not-a-receipt", "a", "g", 1, 0, 2, False)
    assert missing["state"] == "unknown"
    identifier = receipt(registry)
    assert registry.status(identifier, "b", "g", 1, 0, 2, False)["state"] == "unknown"
    assert registry.status(identifier, "a", "other", 1, 0, 2, False)["state"] == "unknown"
    assert registry.status(identifier, "a", "g", 9, 0, 2, False)["state"] == "unknown"
    assert registry.status(identifier, "a", "g", 1, 9, 2, False)["state"] == "unknown"
    assert registry.status(identifier, "a", "g", 1, 0, 9, False)["state"] == "unknown"


def test_receipts_cannot_be_consumed_by_another_account_or_position():
    registry = AnalysisReceipts()
    identifier = receipt(registry)
    with pytest.raises(AnalysisPolicyError) as account:
        registry.consume(identifier, "b", "g", 1, 0, 2, "key")
    assert account.value.code == "analysis_receipt_invalid"
    with pytest.raises(AnalysisPolicyError) as position:
        registry.consume(identifier, "a", "g", 1, 0, 2, "different")
    assert position.value.code == "analysis_receipt_mismatch"


def test_capacity_never_evicts_an_active_receipt():
    registry = AnalysisReceipts(max_receipts=2, max_per_account=4)
    first = receipt(registry)
    registry.consume(first, "a", "g", 1, 0, 2, "key")
    receipt(registry, game="g2")
    with pytest.raises(AnalysisPolicyError) as capacity:
        receipt(registry, game="g3")
    assert capacity.value.status == 503
    assert capacity.value.code == "analysis_receipt_capacity"
    assert registry.status(first, "a", "g", 1, 0, 2, True)["state"] == "active"


def test_expired_unconsumed_receipts_release_capacity_but_consumed_retirement_evidence_remains():
    clock = {"now": 0.0}
    registry = AnalysisReceipts(
        clock=lambda: clock["now"], ttl_seconds=5.0, max_receipts=1, max_per_account=2
    )
    expired = receipt(registry)
    clock["now"] = 10.0
    assert registry.status(expired, "a", "g", 1, 0, 2, False)["state"] == "expired"
    clock["now"] = 400.0
    replacement = receipt(registry, game="g2")
    assert replacement
    assert registry.status(expired, "a", "g", 1, 0, 2, False)["state"] == "unknown"


def test_per_account_capacity_is_enforced_without_evicting_newer_records():
    registry = AnalysisReceipts(max_per_account=1)
    receipt(registry)
    with pytest.raises(AnalysisPolicyError) as busy:
        receipt(registry, game="g2")
    assert busy.value.code == "analysis_receipt_account_busy"
    assert registry.snapshot()["receipts"] == 1
