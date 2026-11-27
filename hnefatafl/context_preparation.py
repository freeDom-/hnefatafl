"""Bounded coordination for browser-analysis context preparation.

The analysis-context endpoints are read-only, but a cache miss reconstructs the
whole recorded move history. That work must be deduplicated per immutable
``(game, version, advisor revision)`` identity and bounded independently of the
shared analysis executor and the HTTP request pool.

The previous implementation kept raw locks in an ``OrderedDict`` and, at the
capacity bound, moved a still-locked entry to another key. That changed the
lock's owning context and could also retire a lock a consumer had obtained but
not yet acquired. This module replaces it with explicit build records whose
lifetime is exactly the number of reserved consumers:

* one record per context identity;
* one serialized reconstruction per record;
* records are removed only when no consumer (builder or waiter) remains;
* capacity exhaustion rejects additional distinct work with a recoverable
  overload response instead of evicting active records.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from typing import Callable, Hashable

from .analysis_policy import AnalysisPolicyError

MAX_BUILD_RECORDS = 64
MAX_CONCURRENT_BUILDS = 4
MAX_WAITING_CONSUMERS = 16
BUILD_WAIT_SECONDS = 5.0
MAX_CONTEXT_RATE_BUCKETS = 4096
CONTEXT_RATE_CLEANUP_SECONDS = 30.0


def _busy(message: str, retry_after: int = 1) -> AnalysisPolicyError:
    return AnalysisPolicyError(
        message,
        code="analysis_context_busy",
        status=503,
        retry_after=retry_after,
    )


@dataclass
class _BuildRecord:
    lock: threading.Lock = field(default_factory=threading.Lock)
    consumers: int = 0


class BuildLease(AbstractContextManager["BuildLease"]):
    """One reserved consumer of a context build record.

    Reserving a lease increments the record's consumer count *before* the lock
    is returned to the caller. A returned-but-not-yet-acquired lease can
    therefore never be retired by capacity enforcement. Cluster reconstruction
    capacity is acquired separately, only after the caller has rechecked the
    cache and established that it genuinely needs to rebuild.
    """

    def __init__(
        self,
        registry: "ContextBuildRegistry",
        key: Hashable,
        record: _BuildRecord,
        *,
        wait_seconds: float,
    ) -> None:
        self.registry = registry
        self.key = key
        self.record = record
        self.wait_seconds = wait_seconds
        self.acquired = False
        self.capacity_held = False
        self.waiting = True
        self.closed = False

    def __enter__(self) -> "BuildLease":
        if self.closed:
            raise _busy("The analysis context build was already released.")
        if not self.record.lock.acquire(timeout=max(0.0, self.wait_seconds)):
            self.registry.abandon_lease(self)
            self.closed = True
            raise _busy("Timed out waiting for the analysis context builder.")
        self.acquired = True
        self.registry.enter_lease(self)
        return self

    def reconstruct(self, timeout: float = 0.0) -> None:
        """Reserve aggregate reconstruction capacity for an actual rebuild."""
        if self.closed or not self.acquired:
            raise _busy("The analysis context build is not active.")
        if self.capacity_held:
            return
        if not self.registry.capacity.acquire(timeout=max(0.0, timeout)):
            raise _busy("Analysis context reconstruction capacity is busy. Retry shortly.")
        self.capacity_held = True
        self.registry.capacity_acquired()

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        if self.capacity_held:
            self.capacity_held = False
            self.registry.capacity_released()
            try:
                self.registry.capacity.release()
            except ValueError:
                # A bounded semaphore duplicate release is a bookkeeping fault,
                # never a reason to keep the record or its lock alive.
                pass
        if self.acquired:
            self.acquired = False
            try:
                self.record.lock.release()
            except RuntimeError:
                pass
        self.registry.abandon_lease(self)


class ContextBuildRegistry:
    """Strictly bounded ownership of in-flight context reconstructions."""

    def __init__(
        self,
        *,
        max_records: int = MAX_BUILD_RECORDS,
        max_concurrent: int = MAX_CONCURRENT_BUILDS,
        max_waiting: int = MAX_WAITING_CONSUMERS,
        wait_seconds: float = BUILD_WAIT_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_records = max_records
        self.max_concurrent = max_concurrent
        self.max_waiting = max_waiting
        self.wait_seconds = wait_seconds
        self.clock = clock
        self.guard = threading.Lock()
        self.records: OrderedDict[Hashable, _BuildRecord] = OrderedDict()
        self.capacity = threading.BoundedSemaphore(max_concurrent)
        self.building = 0
        self.waiting = 0
        self.reconstructions = 0
        self.rejections = 0

    def build(self, key: Hashable) -> BuildLease:
        """Reserve exactly one compatible build/wait slot for ``key``."""
        with self.guard:
            record = self.records.get(key)
            if record is None:
                if len(self.records) >= self.max_records:
                    self.rejections += 1
                    raise _busy("Analysis context preparation capacity is busy. Retry shortly.")
                record = _BuildRecord()
                self.records[key] = record
            else:
                self.records.move_to_end(key)
            if self.waiting >= self.max_waiting:
                self.rejections += 1
                raise _busy("Analysis context preparation is busy. Retry shortly.")
            record.consumers += 1
            self.waiting += 1
            return BuildLease(self, key, record, wait_seconds=self.wait_seconds)

    def enter_lease(self, lease: BuildLease) -> None:
        with self.guard:
            if lease.waiting:
                lease.waiting = False
                self.waiting = max(0, self.waiting - 1)

    def capacity_acquired(self) -> None:
        with self.guard:
            self.building += 1

    def capacity_released(self) -> None:
        with self.guard:
            self.building = max(0, self.building - 1)

    def abandon_lease(self, lease: BuildLease) -> None:
        with self.guard:
            if lease.waiting:
                lease.waiting = False
                self.waiting = max(0, self.waiting - 1)
            lease.record.consumers = max(0, lease.record.consumers - 1)
            if lease.record.consumers == 0 and self.records.get(lease.key) is lease.record:
                self.records.pop(lease.key, None)

    def note_reconstruction(self) -> None:
        with self.guard:
            self.reconstructions += 1

    def snapshot(self) -> dict:
        with self.guard:
            return {
                "records": len(self.records),
                "building": self.building,
                "pending": 0,
                "waiting": self.waiting,
                "reconstructions": self.reconstructions,
                "rejections": self.rejections,
                "max_records": self.max_records,
                "max_concurrent": self.max_concurrent,
                "max_waiting": self.max_waiting,
            }


class ContextRequestLimiter:
    """Per-account request-rate protection for expensive context preparation."""

    def __init__(
        self,
        requests_per_minute: int,
        *,
        max_buckets: int = MAX_CONTEXT_RATE_BUCKETS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.requests_per_minute = requests_per_minute
        self.max_buckets = max_buckets
        self.clock = clock
        self.lock = threading.Lock()
        self.buckets: dict[str, list[float]] = {}
        self.last_cleanup = 0.0
        self.rejections = 0

    def _expire_all(self, now: float) -> None:
        window = now - 60.0
        for account, values in list(self.buckets.items()):
            recent = [value for value in values if value > window]
            if recent:
                self.buckets[account] = recent
            else:
                self.buckets.pop(account, None)

    def limit(self, account_id: str) -> None:
        with self.lock:
            now = self.clock()
            if now - self.last_cleanup >= CONTEXT_RATE_CLEANUP_SECONDS:
                self.last_cleanup = now
                self._expire_all(now)
            if account_id not in self.buckets and len(self.buckets) >= self.max_buckets:
                self._expire_all(now)
                if len(self.buckets) >= self.max_buckets:
                    self.rejections += 1
                    raise _busy("Analysis context request capacity is busy. Retry shortly.")
            window = now - 60.0
            recent = [value for value in self.buckets.get(account_id, []) if value > window]
            if len(recent) >= self.requests_per_minute:
                self.rejections += 1
                raise AnalysisPolicyError(
                    "Analysis context request rate limit reached. Retry shortly.",
                    code="analysis_context_rate_limited",
                    status=429,
                    retry_after=max(1, int(60 - (now - recent[0]))),
                )
            recent.append(now)
            self.buckets[account_id] = recent

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "buckets": len(self.buckets),
                "requests_per_minute": self.requests_per_minute,
                "rejections": self.rejections,
            }
