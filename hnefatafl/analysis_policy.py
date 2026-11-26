"""Server-authoritative game-analysis execution policy.

The deployment, not the player, decides where optional analysis runs. The policy
is resolved once from the environment at startup, is never persisted per account
and cannot be overridden by a browser request or browser storage.

Modes
-----
``browser_only``
    Eligible analysis executes in the browser. Server-side analysis endpoints
    reject every request and never schedule computation.
``browser_preferred``
    Browser execution is preferred. The same endpoints remain available as a
    strictly bounded compatibility fallback and are protected by admission
    controls.
``server_only``
    The historical behaviour: analysis executes on the server. Browser runtime
    delivery is disabled unless the legacy flag is the only configuration.
``disabled``
    Optional analysis is disabled. Replay navigation, gameplay, move validation,
    AI opponents and training are unaffected.

Legacy compatibility
--------------------
``HNEFATAFL_BROWSER_ANALYSIS_ENABLED=1`` without ``HNEFATAFL_ANALYSIS_MODE``
keeps its original meaning: server analysis stays available and the browser
runtime may be delivered for diagnostics, but browser-first execution is not
activated. An explicit ``HNEFATAFL_ANALYSIS_MODE`` always wins; the legacy flag
then cannot enable browser delivery in ``server_only``/``disabled`` mode.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from typing import Mapping

MODES = ("browser_only", "browser_preferred", "server_only", "disabled")
LEGACY_ENV = "HNEFATAFL_BROWSER_ANALYSIS_ENABLED"
MODE_ENV = "HNEFATAFL_ANALYSIS_MODE"
WEBGPU_ENV = "HNEFATAFL_ANALYSIS_WEBGPU"
OFFLINE_ENV = "HNEFATAFL_ANALYSIS_OFFLINE_SECONDS"
MAX_CONCURRENT_ENV = "HNEFATAFL_ANALYSIS_MAX_CONCURRENT"
MAX_PER_USER_ENV = "HNEFATAFL_ANALYSIS_MAX_PER_USER"
RATE_ENV = "HNEFATAFL_ANALYSIS_REQUESTS_PER_MINUTE"

DEFAULT_OFFLINE_SECONDS = 900
MIN_OFFLINE_SECONDS = 60
MAX_OFFLINE_SECONDS = 3600
DEFAULT_MAX_CONCURRENT = 16
DEFAULT_MAX_PER_USER = 4
DEFAULT_REQUESTS_PER_MINUTE = 240
MAX_TASKS_BOUND = 64
MAX_PER_USER_BOUND = 8
MAX_RATE_BOUND = 600


class AnalysisPolicyError(Exception):
    """A request is not permitted by the deployment execution policy."""

    def __init__(self, message: str, *, code: str, status: int = 409, retry_after: int = 0):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status = status
        self.retry_after = retry_after


def _switch(raw: str, name: str) -> bool:
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off", ""}:
        return False
    raise ValueError(f"{name} must be 0 or 1")


def _bounded_int(raw: str | None, name: str, default: int, low: int, high: int) -> int:
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError as error:
        raise ValueError(f"{name} must be an integer") from error
    if not low <= value <= high:
        raise ValueError(f"{name} must be between {low} and {high}")
    return value


@dataclass(frozen=True)
class AnalysisPolicy:
    mode: str
    browser_delivery: bool
    server_computation: bool
    fallback: bool
    webgpu: bool
    offline_seconds: int
    max_concurrent: int
    max_per_user: int
    requests_per_minute: int

    def public(self) -> dict:
        """Capability projection safe for every authenticated client."""
        return {
            "mode": self.mode,
            "browser_delivery": self.browser_delivery,
            "browser_execution": self.mode in {"browser_only", "browser_preferred"},
            "server_computation": self.server_computation,
            "fallback": self.fallback,
            "webgpu": self.webgpu,
            "offline_authorization_seconds": self.offline_seconds,
            "limits": {
                "max_concurrent": self.max_concurrent,
                "max_per_user": self.max_per_user,
                "requests_per_minute": self.requests_per_minute,
            },
        }


def resolve_policy(environment: Mapping[str, str] | None = None) -> AnalysisPolicy:
    """Resolve and validate the deployment policy.

    Invalid or contradictory values fail at startup. An explicit mode wins over
    the legacy delivery flag.
    """
    environment = os.environ if environment is None else environment
    raw_mode = environment.get(MODE_ENV, "").strip().lower()
    legacy_raw = environment.get(LEGACY_ENV, "0")
    legacy = _switch(legacy_raw, LEGACY_ENV)
    if raw_mode:
        if raw_mode not in MODES:
            raise ValueError(
                f"{MODE_ENV} must be one of {', '.join(MODES)} (received {raw_mode!r})"
            )
        mode = raw_mode
        # Explicit policy owns delivery. The legacy flag is ignored so it can
        # never enable a browser path that the explicit policy disabled.
        delivery = mode in {"browser_only", "browser_preferred"}
    else:
        mode = "server_only"
        delivery = legacy
    server = mode in {"server_only", "browser_preferred"}
    fallback = mode == "browser_preferred"
    webgpu = _switch(environment.get(WEBGPU_ENV, "0"), WEBGPU_ENV)
    if webgpu and not delivery:
        raise ValueError(f"{WEBGPU_ENV}=1 requires a browser execution mode")
    offline = _bounded_int(
        environment.get(OFFLINE_ENV),
        OFFLINE_ENV,
        DEFAULT_OFFLINE_SECONDS,
        MIN_OFFLINE_SECONDS,
        MAX_OFFLINE_SECONDS,
    )
    return AnalysisPolicy(
        mode=mode,
        browser_delivery=delivery,
        server_computation=server,
        fallback=fallback,
        webgpu=webgpu,
        offline_seconds=offline,
        max_concurrent=_bounded_int(
            environment.get(MAX_CONCURRENT_ENV),
            MAX_CONCURRENT_ENV,
            DEFAULT_MAX_CONCURRENT,
            1,
            MAX_TASKS_BOUND,
        ),
        max_per_user=_bounded_int(
            environment.get(MAX_PER_USER_ENV),
            MAX_PER_USER_ENV,
            DEFAULT_MAX_PER_USER,
            1,
            MAX_PER_USER_BOUND,
        ),
        requests_per_minute=_bounded_int(
            environment.get(RATE_ENV),
            RATE_ENV,
            DEFAULT_REQUESTS_PER_MINUTE,
            1,
            MAX_RATE_BOUND,
        ),
    )


class AnalysisAdmission:
    """Bounded, per-process admission for server analysis requests.

    The supported deployment runs one application process (the existing
    deployment lock enforces that invariant). These limits therefore bound the
    whole server, not one worker among several. Direct legacy requests and
    browser-fallback requests pass through exactly the same controls.

    This class intentionally does not trust any client-reported local failure:
    admission depends only on the authenticated account, the global task count
    and the request rate.
    """

    def __init__(self, policy: AnalysisPolicy, clock=time.monotonic, *, enforce: bool = True):
        self.policy = policy
        self.enforce = enforce
        self.clock = clock
        self.lock = threading.RLock()
        self.active_global = 0
        self.active_by_account: dict[str, int] = {}
        self.requests_by_account: dict[str, list[float]] = {}
        self.counters: dict[str, int] = {}

    def _count(self, name: str) -> None:
        self.counters[name] = self.counters.get(name, 0) + 1

    def count(self, name: str) -> None:
        """Record a coarse request category without any client-supplied detail."""
        with self.lock:
            self._count(name)

    def _prune(self, account: str, now: float) -> list[float]:
        window = now - 60.0
        recent = [value for value in self.requests_by_account.get(account, []) if value > window]
        if recent:
            self.requests_by_account[account] = recent
        else:
            self.requests_by_account.pop(account, None)
        return recent

    def limit_request(self, account_id: str) -> None:
        """Rate-limit one analysis request for an authenticated account."""
        with self.lock:
            now = self.clock()
            recent = self._prune(account_id, now)
            if len(recent) >= self.policy.requests_per_minute:
                self._count("rate_rejected")
                retry = max(1, int(60 - (now - recent[0])))
                raise AnalysisPolicyError(
                    "Analysis request rate limit reached. Retry shortly.",
                    code="analysis_rate_limited",
                    status=429,
                    retry_after=retry,
                )
            recent.append(now)
            self.requests_by_account[account_id] = recent

    def admit_task(self, account_id: str) -> tuple[str, float]:
        """Reserve one concurrent analysis task; release exactly once."""
        with self.lock:
            if self.enforce and self.active_global >= self.policy.max_concurrent:
                self._count("overload_rejected")
                raise AnalysisPolicyError(
                    "Analysis capacity is busy. Retry shortly.",
                    code="analysis_overloaded",
                    status=503,
                    retry_after=1,
                )
            if (
                self.enforce
                and self.active_by_account.get(account_id, 0) >= self.policy.max_per_user
            ):
                self._count("per_user_rejected")
                raise AnalysisPolicyError(
                    "Too many analyses are already running for this account.",
                    code="analysis_account_busy",
                    status=429,
                    retry_after=2,
                )
            self.active_global += 1
            self.active_by_account[account_id] = self.active_by_account.get(account_id, 0) + 1
            self._count("admitted")
            return account_id, self.clock()

    def release_task(self, token: tuple[str, float]) -> None:
        account_id, _ = token
        with self.lock:
            self.active_global = max(0, self.active_global - 1)
            remaining = self.active_by_account.get(account_id, 0) - 1
            if remaining > 0:
                self.active_by_account[account_id] = remaining
            else:
                self.active_by_account.pop(account_id, None)
            self._count("released")

    def snapshot(self) -> dict:
        """Coarse, privacy-preserving counters (no account identifiers)."""
        with self.lock:
            return {
                **self.counters,
                "active": self.active_global,
                "active_accounts": len(self.active_by_account),
            }
