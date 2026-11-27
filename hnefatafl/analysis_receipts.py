"""Narrow admission/retirement receipts for browser fallback analysis.

A browser can abort an HTTP request but cannot know whether the server received
it. Receipts therefore distinguish:

* ``prepared`` — created, not submitted; still admissible until it expires;
* ``submitted`` — accepted by an endpoint, awaiting scheduling/admission;
* ``active`` — at least one task is queued/running or a duplicate is joining it;
* terminal states ``completed``/``failed``/``expired`` — no new computation is
  permitted with this receipt.

Receipt transitions are authoritative and atomic. A terminal receipt is moved to
a bounded terminal-evidence store so completed work does not consume active
admission capacity. ``reconciled_before`` is a server-wall-clock watermark: an
unknown receipt created at or before it was necessarily terminal before its
evidence was removed (active receipts are never evicted). This makes bounded
terminal cleanup safe for browser reconciliation.
"""

from __future__ import annotations

import secrets
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Callable, Literal

from .analysis_policy import AnalysisPolicyError

MAX_ACTIVE_RECEIPTS = 256
MAX_ACTIVE_PER_ACCOUNT = 32
MAX_TERMINAL_RECEIPTS = 2048
MAX_TERMINAL_PER_ACCOUNT = 512
RECEIPT_TTL_SECONDS = 120.0
TERMINAL_RETENTION_SECONDS = 1800.0
CLEANUP_INTERVAL_SECONDS = 5.0

TERMINAL_STATES = frozenset({"completed", "failed", "expired"})
AdvisorOutcome = Literal["complete", "partial", "unavailable", "failed", "superseded"]


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
    online: bool = False
    ply: int = 0
    side_to_move: str = "attackers"
    sequence: int = 0
    state: str = "prepared"
    submissions: int = 0
    tasks: int = 0
    admitted: bool = False
    advisor_outcome: AdvisorOutcome | None = None
    failed: bool = False
    terminal_at: float | None = None


class ReceiptLease:
    """One endpoint submission for a receipt, with exactly-once finalization."""

    def __init__(self, registry: "AnalysisReceipts", receipt: _Receipt, primary: bool) -> None:
        self.registry = registry
        self.receipt = receipt
        self.primary = primary
        self.finished = False

    @property
    def state(self) -> str:
        return self.receipt.state

    def track(self) -> None:
        self.registry.track_task(self.receipt)

    def complete(self, outcome: AdvisorOutcome | None) -> None:
        self.registry.complete_task(self.receipt, outcome)

    def result_available(self, outcome: AdvisorOutcome) -> None:
        self.registry.mark_result(self.receipt, outcome)

    def fail(self) -> None:
        self.registry.mark_failure(self.receipt)

    def finalize(self) -> None:
        """Finish this submission exactly once, independently of its result."""
        if self.finished:
            return
        self.finished = True
        self.registry.finish_submission(self.receipt)


class AnalysisReceipts:
    """Bounded, account-bound fallback request receipts."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        max_active: int = MAX_ACTIVE_RECEIPTS,
        max_active_per_account: int = MAX_ACTIVE_PER_ACCOUNT,
        max_terminal: int = MAX_TERMINAL_RECEIPTS,
        max_terminal_per_account: int = MAX_TERMINAL_PER_ACCOUNT,
        ttl_seconds: float = RECEIPT_TTL_SECONDS,
        terminal_retention_seconds: float = TERMINAL_RETENTION_SECONDS,
    ) -> None:
        self.clock = clock
        self.wall_clock = wall_clock
        self.max_active = max_active
        self.max_active_per_account = max_active_per_account
        self.max_terminal = max_terminal
        self.max_terminal_per_account = max_terminal_per_account
        self.ttl_seconds = ttl_seconds
        self.terminal_retention_seconds = terminal_retention_seconds
        self.epoch = uuid.uuid4().hex
        self.lock = threading.RLock()
        self.active: dict[str, _Receipt] = {}
        self.terminal: dict[str, _Receipt] = {}
        self.reconciled_before = 0.0
        self.reconciled_before_sequence = 0
        self.blocked_reconciled = 0
        self.sequence = 0
        self.last_cleanup = 0.0
        self.rejections = 0
        self.expired = 0
        self.status_reads = 0
        self.result_reads = 0
        self.cleanup_runs = 0

    def _error(self, message: str, code: str, status: int = 409) -> AnalysisPolicyError:
        return AnalysisPolicyError(message, code=code, status=status)

    def _advance_reconciled(self, candidate_sequence: int) -> None:
        if candidate_sequence <= 0:
            return
        self.blocked_reconciled = max(self.blocked_reconciled, int(candidate_sequence))
        oldest_active = min((receipt.sequence for receipt in self.active.values()), default=None)
        allowed = self.blocked_reconciled
        if oldest_active is not None:
            allowed = min(allowed, oldest_active - 1)
        if allowed > self.reconciled_before_sequence:
            self.reconciled_before_sequence = allowed
            self.reconciled_before = self.wall_clock()

    def _move_terminal(self, receipt: _Receipt, state: str, now: float) -> None:
        if receipt.identifier in self.active:
            self.active.pop(receipt.identifier, None)
        receipt.state = state
        receipt.terminal_at = now
        self.terminal[receipt.identifier] = receipt
        self._trim_terminal()
        self._advance_reconciled(self.blocked_reconciled)

    def _trim_terminal(self) -> None:
        owned: dict[str, int] = {}
        for identifier, receipt in list(self.terminal.items()):
            state_terminal_at = receipt.terminal_at or receipt.created_at
            if self.wall_clock() - state_terminal_at > self.terminal_retention_seconds:
                self.terminal.pop(identifier, None)
                self._advance_reconciled(receipt.sequence)
                continue
            owned[receipt.account_id] = owned.get(receipt.account_id, 0) + 1
        for account_id, count in owned.items():
            if count <= self.max_terminal_per_account:
                continue
            excess = count - self.max_terminal_per_account
            for identifier, receipt in list(self.terminal.items()):
                if excess <= 0:
                    break
                if receipt.account_id != account_id:
                    continue
                self.terminal.pop(identifier, None)
                self._advance_reconciled(receipt.sequence)
                excess -= 1
        while len(self.terminal) > self.max_terminal:
            identifier, receipt = next(iter(self.terminal.items()))
            self.terminal.pop(identifier, None)
            self._advance_reconciled(receipt.sequence)

    def _cleanup(self, now: float, *, force: bool = False) -> None:
        if not force and now - self.last_cleanup < CLEANUP_INTERVAL_SECONDS:
            return
        self.last_cleanup = now
        self.cleanup_runs += 1
        for identifier, receipt in list(self.active.items()):
            if receipt.state == "prepared" and now >= receipt.expires_at:
                self.expired += 1
                self._move_terminal(receipt, "expired", self.wall_clock())
        self._trim_terminal()

    def _expire_if_needed(self, receipt: _Receipt, now: float) -> None:
        if receipt.state == "prepared" and now >= receipt.expires_at:
            self.expired += 1
            self._move_terminal(receipt, "expired", self.wall_clock())

    def create(
        self,
        account_id: str,
        game_id: str,
        version: int,
        cursor: int,
        revision: int,
        position_key: str,
        *,
        online: bool = False,
        ply: int = 0,
        side_to_move: str = "attackers",
    ) -> dict:
        with self.lock:
            now = self.clock()
            self._cleanup(now, force=True)
            if len(self.active) >= self.max_active:
                self.rejections += 1
                raise self._error(
                    "Analysis receipt capacity is busy. Retry shortly.",
                    "analysis_receipt_capacity",
                    503,
                )
            owned = sum(1 for receipt in self.active.values() if receipt.account_id == account_id)
            if owned >= self.max_active_per_account:
                self.rejections += 1
                raise self._error(
                    "Too many pending analysis requests for this account.",
                    "analysis_receipt_account_busy",
                    429,
                )
            identifier = secrets.token_urlsafe(24)
            wall = self.wall_clock()
            self.sequence += 1
            receipt = _Receipt(
                identifier=identifier,
                account_id=account_id,
                game_id=game_id,
                version=int(version),
                cursor=int(cursor),
                revision=int(revision),
                position_key=position_key,
                created_at=wall,
                expires_at=now + self.ttl_seconds,
                online=online,
                ply=int(ply),
                side_to_move=side_to_move,
                sequence=self.sequence,
            )
            self.active[identifier] = receipt
            return {
                "schema": 1,
                "receipt": identifier,
                "epoch": self.epoch,
                "created_at": wall,
                "sequence": receipt.sequence,
                "expires_in": self.ttl_seconds,
                "reconciled_before": self.reconciled_before,
                "reconciled_before_sequence": self.reconciled_before_sequence,
                "game_id": game_id,
                "version": receipt.version,
                "cursor": receipt.cursor,
                "ply": receipt.ply,
                "side_to_move": receipt.side_to_move,
                "position_key": receipt.position_key,
                "advisor_revision": receipt.revision,
            }

    def _lookup(self, identifier: str, account_id: str) -> tuple[_Receipt | None, bool]:
        receipt = self.active.get(identifier)
        if receipt is not None:
            return (receipt, False) if receipt.account_id == account_id else (None, False)
        receipt = self.terminal.get(identifier)
        if receipt is not None:
            return (receipt, True) if receipt.account_id == account_id else (None, True)
        return None, False

    def begin_submission(
        self,
        identifier: str,
        account_id: str,
        game_id: str,
        version: int,
        cursor: int,
        revision: int,
        position_key: str,
    ) -> ReceiptLease:
        with self.lock:
            now = self.clock()
            self._cleanup(now)
            receipt, terminal = self._lookup(identifier, account_id)
            if receipt is not None:
                self._expire_if_needed(receipt, now)
            if receipt is None:
                raise self._error(
                    "The analysis receipt is missing or no longer valid.",
                    "analysis_receipt_invalid",
                )
            if receipt.state == "expired" or terminal:
                raise self._error(
                    "The analysis receipt is already terminal and cannot admit more work.",
                    "analysis_receipt_terminal",
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
            primary = receipt.state == "prepared"
            if primary:
                receipt.state = "submitted"
            receipt.submissions += 1
            return ReceiptLease(self, receipt, primary)

    def _maybe_terminal(self, receipt: _Receipt) -> None:
        if receipt.submissions != 0 or receipt.tasks != 0:
            return
        if receipt.identifier in self.terminal:
            return
        if receipt.advisor_outcome in {"failed", "superseded"} or (
            receipt.advisor_outcome is None and receipt.failed
        ):
            state = "failed"
        else:
            state = "completed"
        self._move_terminal(receipt, state, self.wall_clock())

    def track_task(self, receipt: _Receipt) -> None:
        with self.lock:
            if receipt.identifier in self.terminal:
                return
            receipt.tasks += 1
            receipt.admitted = True
            receipt.state = "active"

    def complete_task(self, receipt: _Receipt, outcome: AdvisorOutcome | None) -> None:
        with self.lock:
            if receipt.tasks > 0:
                receipt.tasks -= 1
            if outcome is not None:
                receipt.advisor_outcome = outcome
            self._maybe_terminal(receipt)

    def mark_result(self, receipt: _Receipt, outcome: AdvisorOutcome) -> None:
        with self.lock:
            if receipt.advisor_outcome is None:
                receipt.advisor_outcome = outcome

    def mark_failure(self, receipt: _Receipt) -> None:
        with self.lock:
            receipt.failed = True

    def finish_submission(self, receipt: _Receipt) -> None:
        with self.lock:
            if receipt.submissions > 0:
                receipt.submissions -= 1
            self._maybe_terminal(receipt)

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
            receipt, _terminal = self._lookup(identifier, account_id)
            if (
                receipt is None
                or receipt.game_id != game_id
                or receipt.version != int(version)
                or receipt.cursor != int(cursor)
                or receipt.revision != int(revision)
            ):
                return None
            return receipt.position_key

    def status_for_account(
        self, identifier: str, account_id: str, *, include_position: bool = True
    ) -> dict:
        with self.lock:
            now = self.clock()
            self.status_reads += 1
            self._cleanup(now)
            receipt, _terminal = self._lookup(identifier, account_id)
            if receipt is not None:
                self._expire_if_needed(receipt, now)
            payload = {
                "schema": 1,
                "receipt": identifier,
                "epoch": self.epoch,
                "reconciled_before": self.reconciled_before,
                "reconciled_before_sequence": self.reconciled_before_sequence,
            }
            if receipt is None:
                return {**payload, "state": "unknown", "terminal": False}
            lifecycle = {
                **payload,
                "state": receipt.state,
                "terminal": receipt.state in TERMINAL_STATES,
                "sequence": receipt.sequence,
            }
            if not include_position:
                return lifecycle
            return {
                **lifecycle,
                "created_at": receipt.created_at,
                "game_id": receipt.game_id,
                "version": receipt.version,
                "cursor": receipt.cursor,
                "ply": receipt.ply,
                "side_to_move": receipt.side_to_move,
                "position_key": receipt.position_key,
                "advisor_revision": receipt.revision,
                "advisor_outcome": receipt.advisor_outcome,
                "online": receipt.online,
                "retry_admission": receipt.state == "failed"
                and not receipt.admitted
                and receipt.advisor_outcome is None,
            }

    def status(
        self,
        identifier: str,
        account_id: str,
        game_id: str,
        version: int,
        cursor: int,
        revision: int,
    ) -> dict:
        with self.lock:
            now = self.clock()
            self.status_reads += 1
            self._cleanup(now)
            receipt, _terminal = self._lookup(identifier, account_id)
            if receipt is not None:
                self._expire_if_needed(receipt, now)
            if (
                receipt is None
                or receipt.game_id != game_id
                or receipt.version != int(version)
                or receipt.cursor != int(cursor)
                or receipt.revision != int(revision)
            ):
                return {
                    "schema": 1,
                    "receipt": identifier,
                    "epoch": self.epoch,
                    "state": "unknown",
                    "terminal": False,
                    "reconciled_before": self.reconciled_before,
                    "reconciled_before_sequence": self.reconciled_before_sequence,
                }
            return {
                "schema": 1,
                "receipt": identifier,
                "epoch": self.epoch,
                "state": receipt.state,
                "terminal": receipt.state in TERMINAL_STATES,
                "reconciled_before": self.reconciled_before,
                "reconciled_before_sequence": self.reconciled_before_sequence,
                "sequence": receipt.sequence,
                "game_id": receipt.game_id,
                "version": receipt.version,
                "cursor": receipt.cursor,
                "ply": receipt.ply,
                "side_to_move": receipt.side_to_move,
                "position_key": receipt.position_key,
                "advisor_revision": receipt.revision,
            }

    def note_result_read(self) -> None:
        with self.lock:
            self.result_reads += 1

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "active": len(self.active),
                "terminal": len(self.terminal),
                "reconciled_before": self.reconciled_before,
                "reconciled_before_sequence": self.reconciled_before_sequence,
                "blocked_reconciled": self.blocked_reconciled,
                "sequence": self.sequence,
                "rejections": self.rejections,
                "expired": self.expired,
                "status_reads": self.status_reads,
                "result_reads": self.result_reads,
                "cleanup_runs": self.cleanup_runs,
                "max_active": self.max_active,
                "max_active_per_account": self.max_active_per_account,
                "max_terminal": self.max_terminal,
                "max_terminal_per_account": self.max_terminal_per_account,
            }
