"""Narrow admission/retirement receipts for browser fallback analysis.

A browser can abort an HTTP request but cannot know whether the server received
it. The existing read-only analysis-status check could therefore observe no
active task for a request that was still in flight and conclude that local work
was safe to start. These receipts make that distinction explicit:

* ``prepared`` — the receipt exists but the analysis request has not been
  consumed. It is *not* safe evidence of retirement.
* ``active`` — the request was consumed and its task may still be running.
* ``retired`` — the request was consumed and no task remains active.
* ``expired`` — the request was never consumed and can no longer be admitted.
* missing/invalid data is ``unknown`` and never evidence of retirement.

Receipts are bounded, account-bound and never start computation. The status
endpoint remains read-only: it only inspects this registry and the existing
analysis service activity.
"""

from __future__ import annotations

import secrets
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Callable

from .analysis_policy import AnalysisPolicyError

MAX_RECEIPTS = 256
MAX_PER_ACCOUNT = 32
RECEIPT_TTL_SECONDS = 120.0
ACTIVE_GRACE_SECONDS = 10.0
EXPIRED_RETENTION_SECONDS = 300.0
RETIRED_RETENTION_SECONDS = 600.0


@dataclass
class _Receipt:
    identifier: str
    account_id: str
    game_id: str
    version: int
    cursor: int
    revision: int
    position_key: str
    created_at: float
    expires_at: float
    consumed_at: float | None = None


class AnalysisReceipts:
    """Bounded, account-bound fallback request receipts."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        max_receipts: int = MAX_RECEIPTS,
        max_per_account: int = MAX_PER_ACCOUNT,
        ttl_seconds: float = RECEIPT_TTL_SECONDS,
    ) -> None:
        self.clock = clock
        self.max_receipts = max_receipts
        self.max_per_account = max_per_account
        self.ttl_seconds = ttl_seconds
        self.epoch = uuid.uuid4().hex
        self.lock = threading.RLock()
        self.receipts: dict[str, _Receipt] = {}
        self.rejections = 0
        self.expired = 0

    def _error(self, message: str, code: str, status: int = 409) -> AnalysisPolicyError:
        return AnalysisPolicyError(message, code=code, status=status)

    def _cleanup(self, now: float) -> None:
        for identifier, receipt in list(self.receipts.items()):
            if receipt.consumed_at is None:
                if now - receipt.expires_at > EXPIRED_RETENTION_SECONDS:
                    self.receipts.pop(identifier, None)
                    self.expired += 1
            elif now - receipt.consumed_at > RETIRED_RETENTION_SECONDS:
                self.receipts.pop(identifier, None)

    def create(
        self,
        account_id: str,
        game_id: str,
        version: int,
        cursor: int,
        revision: int,
        position_key: str,
    ) -> dict:
        with self.lock:
            now = self.clock()
            self._cleanup(now)
            if len(self.receipts) >= self.max_receipts:
                self.rejections += 1
                raise self._error(
                    "Analysis receipt capacity is busy. Retry shortly.",
                    "analysis_receipt_capacity",
                    503,
                )
            owned = sum(1 for receipt in self.receipts.values() if receipt.account_id == account_id)
            if owned >= self.max_per_account:
                self.rejections += 1
                raise self._error(
                    "Too many pending analysis requests for this account.",
                    "analysis_receipt_account_busy",
                    429,
                )
            identifier = secrets.token_urlsafe(24)
            receipt = _Receipt(
                identifier=identifier,
                account_id=account_id,
                game_id=game_id,
                version=int(version),
                cursor=int(cursor),
                revision=int(revision),
                position_key=position_key,
                created_at=now,
                expires_at=now + self.ttl_seconds,
            )
            self.receipts[identifier] = receipt
            return {
                "schema": 1,
                "receipt": identifier,
                "epoch": self.epoch,
                "expires_in": self.ttl_seconds,
                "game_id": game_id,
                "version": receipt.version,
                "cursor": receipt.cursor,
                "advisor_revision": receipt.revision,
            }

    def consume(
        self,
        identifier: str,
        account_id: str,
        game_id: str,
        version: int,
        cursor: int,
        revision: int,
        position_key: str,
    ) -> None:
        with self.lock:
            now = self.clock()
            receipt = self.receipts.get(identifier)
            if receipt is None:
                raise self._error(
                    "The analysis receipt is missing or no longer valid.",
                    "analysis_receipt_invalid",
                )
            if receipt.account_id != account_id:
                raise self._error(
                    "The analysis receipt belongs to another account.", "analysis_receipt_invalid"
                )
            if (
                receipt.game_id != game_id
                or receipt.version != int(version)
                or receipt.cursor != int(cursor)
                or receipt.revision != int(revision)
                or receipt.position_key != position_key
            ):
                raise self._error(
                    "The analysis receipt does not match this position.",
                    "analysis_receipt_mismatch",
                )
            if receipt.consumed_at is None and now >= receipt.expires_at:
                self.receipts.pop(identifier, None)
                self.expired += 1
                raise self._error(
                    "The analysis receipt expired before the request arrived.",
                    "analysis_receipt_expired",
                )
            receipt.consumed_at = now

    def position_key(
        self,
        identifier: str,
        account_id: str,
        game_id: str,
        version: int,
        cursor: int,
        revision: int,
    ) -> str | None:
        with self.lock:
            receipt = self.receipts.get(identifier)
            if (
                receipt is None
                or receipt.account_id != account_id
                or receipt.game_id != game_id
                or receipt.version != int(version)
                or receipt.cursor != int(cursor)
                or receipt.revision != int(revision)
            ):
                return None
            return receipt.position_key

    def status(
        self,
        identifier: str,
        account_id: str,
        game_id: str,
        version: int,
        cursor: int,
        revision: int,
        active: bool,
    ) -> dict:
        with self.lock:
            now = self.clock()
            receipt = self.receipts.get(identifier)
            if receipt is None or receipt.account_id != account_id:
                # Missing, cleaned or foreign data is explicitly unknown. It is
                # never interpreted as proof that work completed.
                return {"schema": 1, "receipt": identifier, "epoch": self.epoch, "state": "unknown"}
            if (
                receipt.game_id != game_id
                or receipt.version != int(version)
                or receipt.cursor != int(cursor)
                or receipt.revision != int(revision)
            ):
                return {"schema": 1, "receipt": identifier, "epoch": self.epoch, "state": "unknown"}
            if receipt.consumed_at is None:
                state = "expired" if now >= receipt.expires_at else "prepared"
            elif active or now - receipt.consumed_at < ACTIVE_GRACE_SECONDS:
                state = "active"
            else:
                state = "retired"
            return {
                "schema": 1,
                "receipt": identifier,
                "epoch": self.epoch,
                "state": state,
                "game_id": receipt.game_id,
                "version": receipt.version,
                "cursor": receipt.cursor,
                "position_key": receipt.position_key,
                "advisor_revision": receipt.revision,
            }

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "receipts": len(self.receipts),
                "rejections": self.rejections,
                "expired": self.expired,
                "max_receipts": self.max_receipts,
                "max_per_account": self.max_per_account,
            }
