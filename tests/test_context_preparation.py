"""Deterministic ownership and capacity tests for context preparation."""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from hnefatafl.analysis_policy import AnalysisPolicyError
from hnefatafl.context_preparation import (
    ContextBuildRegistry,
    ContextRequestLimiter,
    MAX_BUILD_RECORDS,
)


def _busy(error: pytest.ExceptionInfo[AnalysisPolicyError]) -> bool:
    return error.value.status == 503 and error.value.code == "analysis_context_busy"


def _build(registry: ContextBuildRegistry, key):
    lease = registry.build(key)
    lease.__enter__()
    lease.reconstruct()
    registry.note_reconstruction()
    return lease


def test_more_than_the_old_registry_bound_keeps_one_owner_per_context():
    registry = ContextBuildRegistry(max_records=MAX_BUILD_RECORDS)
    for index in range(MAX_BUILD_RECORDS + 16):
        key = ("game", index, 0)
        with registry.build(key) as lease:
            lease.reconstruct()
            registry.note_reconstruction()
            assert registry.records[key].consumers == 1
        assert key not in registry.records
    assert registry.snapshot()["records"] == 0
    assert registry.snapshot()["reconstructions"] == MAX_BUILD_RECORDS + 16


def test_capacity_never_evicts_an_acquired_record():
    registry = ContextBuildRegistry(max_records=1, max_concurrent=1)
    first = _build(registry, ("game", 1, 0))
    with pytest.raises(AnalysisPolicyError) as busy:
        registry.build(("game", 2, 0))
    assert _busy(busy)
    assert ("game", 1, 0) in registry.records
    assert registry.records[("game", 1, 0)].consumers == 1
    first.close()
    second = _build(registry, ("game", 2, 0))
    assert second.key == ("game", 2, 0)
    second.close()


def test_multiple_acquired_records_are_each_independently_owned():
    registry = ContextBuildRegistry(max_records=4, max_concurrent=2)
    first = _build(registry, ("game", 1, 0))
    second = _build(registry, ("game", 2, 0))
    assert registry.snapshot()["building"] == 2
    third = registry.build(("game", 3, 0))
    third.__enter__()
    with pytest.raises(AnalysisPolicyError) as busy:
        third.reconstruct()
    assert _busy(busy)
    assert registry.snapshot()["building"] == 2
    first.close()
    third.reconstruct()
    assert registry.snapshot()["building"] == 2
    third.close()
    second.close()
    assert registry.snapshot()["building"] == 0
    assert registry.snapshot()["records"] == 0


def test_returned_but_not_yet_acquired_lease_cannot_be_retired():
    registry = ContextBuildRegistry(max_records=2, max_concurrent=2)
    lease = registry.build(("game", 1, 0))
    other = _build(registry, ("game", 2, 0))
    assert ("game", 1, 0) in registry.records
    assert registry.records[("game", 1, 0)].consumers == 1
    lease.__enter__()
    lease.reconstruct()
    assert registry.snapshot()["building"] == 2
    lease.close()
    other.close()
    assert registry.snapshot()["records"] == 0


def test_multiple_consumers_share_one_record_and_serialize():
    registry = ContextBuildRegistry(max_concurrent=1, wait_seconds=2.0)
    acquired = threading.Event()
    release = threading.Event()
    acquired_second = threading.Event()

    def builder():
        with registry.build("same") as lease:
            lease.reconstruct()
            acquired.set()
            release.wait(3)
            registry.note_reconstruction()

    def consumer():
        acquired.wait(2)
        with registry.build("same") as lease:
            lease.reconstruct()
            acquired_second.set()
            registry.note_reconstruction()

    first = threading.Thread(target=builder)
    first.start()
    assert acquired.wait(2)
    second = threading.Thread(target=consumer)
    second.start()
    time.sleep(0.05)
    assert not acquired_second.is_set()
    assert len(registry.records) == 1
    assert registry.records["same"].consumers == 2
    release.set()
    assert acquired_second.wait(2)
    first.join(3)
    second.join(3)
    assert registry.snapshot()["records"] == 0
    assert registry.snapshot()["reconstructions"] == 2
    assert registry.snapshot()["building"] == 0


def test_distinct_contexts_are_bounded_and_concurrent():
    registry = ContextBuildRegistry(max_concurrent=2, wait_seconds=2.0)
    barrier = threading.Barrier(3)
    release = threading.Event()

    def hold(key):
        with registry.build(key) as lease:
            lease.reconstruct()
            barrier.wait(2)
            release.wait(3)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(hold, f"game-{index}") for index in range(2)]
        barrier.wait(2)
        overflow = registry.build("game-overflow")
        overflow.__enter__()
        with pytest.raises(AnalysisPolicyError) as busy:
            overflow.reconstruct()
        assert _busy(busy)
        overflow.close()
        release.set()
        for future in futures:
            future.result(timeout=3)
    assert registry.snapshot()["records"] == 0
    assert registry.snapshot()["building"] == 0


def test_builder_exception_releases_ownership_and_allows_retry():
    registry = ContextBuildRegistry(max_concurrent=1)
    with pytest.raises(RuntimeError):
        with registry.build("game") as lease:
            lease.reconstruct()
            raise RuntimeError("reconstruction failed")
    assert registry.snapshot()["records"] == 0
    assert registry.snapshot()["building"] == 0
    with registry.build("game") as lease:
        lease.reconstruct()
        registry.note_reconstruction()
    assert registry.snapshot()["reconstructions"] == 1
    assert registry.snapshot()["building"] == 0


def test_waiting_consumer_timeout_does_not_retire_the_active_builder():
    registry = ContextBuildRegistry(max_concurrent=1, wait_seconds=0.05)
    lease = _build(registry, "game")
    with pytest.raises(AnalysisPolicyError) as busy:
        with registry.build("game"):
            pass
    assert _busy(busy)
    assert registry.records["game"].consumers == 1
    lease.close()
    with registry.build("game") as next_lease:
        next_lease.reconstruct()
    assert registry.snapshot()["records"] == 0
    assert registry.snapshot()["building"] == 0


def test_capacity_exhaustion_is_recoverable_after_genuine_retirement():
    registry = ContextBuildRegistry(max_records=2, max_concurrent=1)
    first = _build(registry, "game")
    other = registry.build("other")
    other.__enter__()
    with pytest.raises(AnalysisPolicyError):
        other.reconstruct()
    first.close()
    other.reconstruct()
    registry.note_reconstruction()
    other.close()
    assert registry.snapshot()["reconstructions"] == 2
    assert registry.snapshot()["building"] == 0
    assert registry.snapshot()["records"] == 0


def test_waiter_cannot_exceed_aggregate_capacity_when_another_context_wins():
    registry = ContextBuildRegistry(max_concurrent=1, wait_seconds=2.0)
    first = _build(registry, "A")
    waiter_entered = threading.Event()
    allow_waiter = threading.Event()
    results: list[str] = []

    def waiting_consumer():
        with registry.build("A") as lease:
            waiter_entered.set()
            allow_waiter.wait(3)
            try:
                lease.reconstruct()
                results.append("admitted")
            except AnalysisPolicyError:
                results.append("busy")
            finally:
                # Keep the record alive only for the bounded handover test.
                registry.note_reconstruction()

    thread = threading.Thread(target=waiting_consumer)
    thread.start()
    time.sleep(0.05)
    # The first A builder finishes; the waiter now enters the per-key lock but
    # has not yet acquired aggregate reconstruction capacity.
    first.close()
    assert waiter_entered.wait(2)
    assert registry.snapshot()["building"] == 0
    competitor = _build(registry, "B")
    assert registry.snapshot()["building"] == 1
    allow_waiter.set()
    thread.join(3)
    assert results == ["busy"]
    assert registry.snapshot()["building"] == 1
    competitor.close()
    assert registry.snapshot()["building"] == 0
    assert registry.snapshot()["records"] == 0


def test_waiter_obtains_capacity_after_builder_failure():
    registry = ContextBuildRegistry(max_concurrent=1, wait_seconds=2.0)
    entered = threading.Event()
    release = threading.Event()
    result: list[str] = []

    def failing_builder():
        try:
            with registry.build("A") as lease:
                lease.reconstruct()
                entered.set()
                release.wait(3)
                raise RuntimeError("build failed")
        except RuntimeError:
            pass

    def waiter():
        entered.wait(2)
        with registry.build("A") as lease:
            time.sleep(0.05)
            lease.reconstruct()
            result.append("admitted")

    builder = threading.Thread(target=failing_builder)
    builder.start()
    assert entered.wait(2)
    consumer = threading.Thread(target=waiter)
    consumer.start()
    release.set()
    builder.join(3)
    consumer.join(3)
    assert result == ["admitted"]
    assert registry.snapshot()["building"] == 0
    assert registry.snapshot()["records"] == 0


def test_acquisition_timeout_then_double_close_releases_exactly_once():
    registry = ContextBuildRegistry(max_concurrent=1, wait_seconds=0.05)
    holder = _build(registry, "A")
    waiter = registry.build("B")
    waiter.__enter__()
    with pytest.raises(AnalysisPolicyError) as busy:
        waiter.reconstruct(timeout=0.02)
    assert _busy(busy)
    waiter.close()
    waiter.close()
    assert registry.snapshot()["records"] == 1
    assert registry.snapshot()["building"] == 1
    holder.close()
    assert registry.snapshot()["records"] == 0
    assert registry.snapshot()["building"] == 0


def test_context_request_rate_limit_is_separate_and_bounded():
    clock = {"now": 0.0}
    limiter = ContextRequestLimiter(2, max_buckets=2, clock=lambda: clock["now"])
    limiter.limit("a")
    limiter.limit("a")
    with pytest.raises(AnalysisPolicyError) as rate:
        limiter.limit("a")
    assert rate.value.status == 429
    assert rate.value.code == "analysis_context_rate_limited"
    limiter.limit("b")
    with pytest.raises(AnalysisPolicyError) as capacity:
        limiter.limit("c")
    assert capacity.value.status == 503
    clock["now"] = 61.0
    limiter.limit("c")
    assert limiter.snapshot()["buckets"] == 1
