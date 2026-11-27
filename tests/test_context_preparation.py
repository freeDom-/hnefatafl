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


def test_more_than_the_old_registry_bound_keeps_one_owner_per_context():
    registry = ContextBuildRegistry(max_records=MAX_BUILD_RECORDS)
    for index in range(MAX_BUILD_RECORDS + 16):
        key = ("game", index, 0)
        with registry.build(key):
            registry.note_reconstruction()
            assert registry.records[key].consumers == 1
        assert key not in registry.records
    assert registry.snapshot()["records"] == 0
    assert registry.snapshot()["reconstructions"] == MAX_BUILD_RECORDS + 16


def test_capacity_never_evicts_an_acquired_record():
    registry = ContextBuildRegistry(max_records=1, max_concurrent=1)
    first = registry.build(("game", 1, 0))
    first.__enter__()
    with pytest.raises(AnalysisPolicyError) as busy:
        registry.build(("game", 2, 0))
    assert _busy(busy)
    assert ("game", 1, 0) in registry.records
    assert registry.records[("game", 1, 0)].consumers == 1
    first.__exit__(None, None, None)
    with registry.build(("game", 2, 0)) as lease:
        assert lease.key == ("game", 2, 0)


def test_multiple_acquired_records_are_each_independently_owned():
    registry = ContextBuildRegistry(max_records=4, max_concurrent=2)
    first = registry.build(("game", 1, 0))
    second = registry.build(("game", 2, 0))
    first.__enter__()
    second.__enter__()
    with pytest.raises(AnalysisPolicyError) as busy:
        registry.build(("game", 3, 0))
    assert _busy(busy)
    assert set(registry.records) == {("game", 1, 0), ("game", 2, 0)}
    first.__exit__(None, None, None)
    with registry.build(("game", 3, 0)):
        pass
    second.__exit__(None, None, None)


def test_returned_but_not_yet_acquired_lease_cannot_be_retired():
    registry = ContextBuildRegistry(max_records=1, max_concurrent=1)
    lease = registry.build(("game", 1, 0))
    with pytest.raises(AnalysisPolicyError) as busy:
        registry.build(("game", 2, 0))
    assert _busy(busy)
    assert ("game", 1, 0) in registry.records
    with lease:
        assert registry.records[("game", 1, 0)].consumers == 1


def test_multiple_consumers_share_one_record_and_serialize():
    registry = ContextBuildRegistry(max_concurrent=1, wait_seconds=2.0)
    acquired = threading.Event()
    release = threading.Event()
    acquired_second = threading.Event()

    def builder():
        with registry.build("same"):
            acquired.set()
            release.wait(3)
            registry.note_reconstruction()

    def consumer():
        acquired.wait(2)
        with registry.build("same"):
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


def test_distinct_contexts_are_bounded_and_concurrent():
    registry = ContextBuildRegistry(max_concurrent=2, wait_seconds=2.0)
    barrier = threading.Barrier(3)
    release = threading.Event()

    def hold(key):
        with registry.build(key):
            barrier.wait(2)
            release.wait(3)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(hold, f"game-{index}") for index in range(2)]
        barrier.wait(2)
        with pytest.raises(AnalysisPolicyError) as busy:
            registry.build("game-overflow")
        assert _busy(busy)
        release.set()
        for future in futures:
            future.result(timeout=3)
    assert registry.snapshot()["records"] == 0


def test_builder_exception_releases_ownership_and_allows_retry():
    registry = ContextBuildRegistry(max_concurrent=1)
    with pytest.raises(RuntimeError):
        with registry.build("game"):
            raise RuntimeError("reconstruction failed")
    assert registry.snapshot()["records"] == 0
    with registry.build("game"):
        registry.note_reconstruction()
    assert registry.snapshot()["reconstructions"] == 1


def test_waiting_consumer_timeout_does_not_retire_the_active_builder():
    registry = ContextBuildRegistry(max_concurrent=1, wait_seconds=0.05)
    lease = registry.build("game")
    lease.__enter__()
    with pytest.raises(AnalysisPolicyError) as busy:
        with registry.build("game"):
            pass
    assert _busy(busy)
    assert registry.records["game"].consumers == 1
    lease.__exit__(None, None, None)
    with registry.build("game"):
        pass
    assert registry.snapshot()["records"] == 0


def test_capacity_exhaustion_is_recoverable_after_genuine_retirement():
    registry = ContextBuildRegistry(max_records=1, max_concurrent=1)
    with registry.build("game"):
        registry.note_reconstruction()
        with pytest.raises(AnalysisPolicyError):
            registry.build("other")
    with registry.build("other"):
        registry.note_reconstruction()
    assert registry.snapshot()["reconstructions"] == 2


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
