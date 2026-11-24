"""Lifespan-owned League processing over the durable online outbox."""

from datetime import datetime, timezone
import logging
import threading

from .league_administration import LeagueAdministration
from .online_core import DeploymentLocked, stamp

LOGGER = logging.getLogger(__name__)
POLL_SECONDS = 1.0
MAX_RETRY_SECONDS = 60.0


class LeagueWorker:
    def __init__(self, administration: LeagueAdministration):
        self.administration = administration
        self.core = administration.core
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._last_checked_at: str | None = None
        self._last_success_at: str | None = None
        self._last_error: str | None = None

    def health(self):
        with self._lock:
            return {
                "running": bool(self.thread and self.thread.is_alive() and not self.stop.is_set()),
                "last_checked_at": self._last_checked_at,
                "last_success_at": self._last_success_at,
                "last_error": self._last_error,
            }

    def _report(self, checked_at: str | None, error: str | None):
        with self._lock:
            self._last_checked_at = checked_at
            self._last_error = error
            if error is None:
                self._last_success_at = checked_at

    def _run(self):
        delay = POLL_SECONDS
        while not self.stop.wait(delay):
            checked_at = stamp(datetime.now(timezone.utc))
            try:
                self.administration.process_rebuild()
                if self.stop.is_set():
                    break
                result = self.core.drain(stop=self.stop)
                if result.get("deferred"):
                    raise DeploymentLocked()
            except DeploymentLocked:
                self._report(checked_at, "deployment_locked")
                delay = POLL_SECONDS
            except Exception:
                # Do not include identifiers, SQL, request details or tracebacks.
                LOGGER.warning("League worker deferred a processing cycle")
                self._report(checked_at, "processing_deferred")
                delay = min(delay * 2, MAX_RETRY_SECONDS)
            else:
                self._report(checked_at, None)
                delay = POLL_SECONDS

    def start(self):
        with self._lock:
            if self.thread and self.thread.is_alive():
                return
            self._last_checked_at = self._last_success_at = self._last_error = None
            self.stop.clear()
            # No startup writer or historical reconciliation. The outbox is
            # written in the same transaction as each official result revision.
            self.thread = threading.Thread(target=self._run, name="league-settlement", daemon=True)
            self.thread.start()

    def shutdown(self):
        with self._lock:
            self.stop.set()
            thread = self.thread
        if thread is not None:
            thread.join(timeout=32)
