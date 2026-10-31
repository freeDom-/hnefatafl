"""Phase-independent training progress, heartbeats and compact job payloads.

The training process publishes ``progress.json``. Historically that file was
written only when a game, an optimization epoch or a whole evaluation finished,
so a long-running unit of work froze every counter *and* the elapsed time the
dashboard renders. This module separates three concepts that must not depend on
each other:

* worker heartbeat -- the process is alive and its publisher still runs;
* computational progress -- actual training work advanced;
* publication -- a monitoring snapshot reached the file (and therefore the
  server or the remote worker's reporter).

``TrainingMonitor`` owns the first two and drives the third from a daemon
thread, so monitoring keeps advancing while a single game, optimization epoch or
evaluation match is still running.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol


# One second matches the dashboard's expected timer resolution. Publication is a
# small atomic JSON replace, so it is cheap enough to run at this cadence.
HEARTBEAT_SECONDS = 1.0

# The full progress snapshot retains the complete iteration history and is
# rewritten only on real progress events. Heartbeats go to a separate, bounded
# file so a one-second cadence never rewrites megabytes of history.
HEARTBEAT_FILENAME = "heartbeat.json"

_HEARTBEAT_KEYS = (
    "status",
    "stage",
    "iteration",
    "total_iterations",
    "games_generated",
    "self_play_games",
    "duration",
    "checkpoint",
    "error",
    "phase_started_at",
    "phase_elapsed",
    "iteration_started_at",
    "iteration_elapsed",
    "started_at",
    "updated_at",
    "last_progress_at",
    "last_progress_seconds",
    "revision",
    "phase_progress",
)


def iso(timestamp: float) -> str:
    """Format one epoch second as a stable UTC timestamp."""
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat(timespec="milliseconds")


def heartbeat_projection(state: dict) -> dict:
    """Bounded liveness snapshot; retained history and metrics stay in progress.json."""
    return {key: state[key] for key in _HEARTBEAT_KEYS if key in state}


def overlay_heartbeat(progress: dict, heartbeat: dict | None) -> dict:
    """Apply a heartbeat snapshot that belongs to the same publication generation.

    Revisions only increase and every real progress event bumps one, so a
    heartbeat can refresh a snapshot but never replace a newer terminal state.
    """
    if not isinstance(heartbeat, dict):
        return progress
    revision = heartbeat.get("revision")
    if type(revision) is not int or revision != progress.get("revision"):
        return progress
    merged = {**progress, **heartbeat}
    nested, retained = heartbeat.get("trial_progress"), progress.get("trial_progress")
    if isinstance(nested, dict) and isinstance(retained, dict):
        # Tuning jobs nest the running trial's snapshot one level deep.
        merged["trial_progress"] = {**retained, **nested}
    return merged


def read_heartbeat(directory: Path) -> dict | None:
    """Load a job's heartbeat snapshot, tolerating an absent or partial file."""
    try:
        return json.loads((directory / HEARTBEAT_FILENAME).read_text())
    except (OSError, ValueError):
        return None


class ProgressSink(Protocol):
    """Hot-path surface used by self-play, optimization and evaluation code.

    Implementations must stay cheap: these methods run once per searched position
    or optimizer batch and must not write files, allocate large objects or block
    on shared resources.
    """

    def phase(self, name: str, *, iteration: int | None = None) -> None: ...

    def tick(self, **deltas: float) -> None: ...

    def counts(self, **values: float | str) -> None: ...


class TrainingMonitor:
    """Phase/iteration timing plus an independent, phase-agnostic heartbeat.

    The compute path only mutates plain counters. A daemon thread re-invokes
    ``writer`` every ``interval`` seconds, which republishes the current snapshot
    with continuously advancing durations even when no counter moved.

    ``writer`` is called with ``heartbeat=True`` from that thread and must be
    safe to call concurrently with the compute path's own publications; the
    training loop serializes both behind one lock.
    """

    def __init__(
        self,
        writer: Callable[..., None],
        *,
        interval: float = HEARTBEAT_SECONDS,
        phase: str = "starting",
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
    ) -> None:
        self._writer = writer
        self._interval = max(float(interval), 0.05)
        self._clock = clock
        self._wall = wall
        self._lock = threading.Lock()
        self._phase = phase
        self._phase_started = clock()
        self._iteration: int | None = None
        self._iteration_started = clock()
        self._started = clock()
        self._started_at = wall()
        self._last_progress = clock()
        self._counters: dict[str, float | str] = {}
        self._revision = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- hot path ---------------------------------------------------------
    def phase(self, name: str, *, iteration: int | None = None) -> None:
        """Enter a new phase, resetting its counters and progress age."""
        now = self._clock()
        with self._lock:
            self._phase = name
            self._phase_started = now
            if iteration is not None and iteration != self._iteration:
                self._iteration = iteration
                self._iteration_started = now
            self._counters = {}
            self._last_progress = now

    def tick(self, **deltas: float) -> None:
        """Accumulate a delta and mark meaningful computational progress."""
        now = self._clock()
        with self._lock:
            counters = self._counters
            for key, value in deltas.items():
                previous = counters.get(key)
                # Absolute counters may hold strings (an opponent name); only
                # numeric values accumulate.
                counters[key] = (previous if isinstance(previous, (int, float)) else 0) + value
            self._last_progress = now

    def counts(self, **values: float | str) -> None:
        """Replace absolute counters without claiming computational progress."""
        with self._lock:
            self._counters.update(values)

    # -- publication ------------------------------------------------------
    def published(self) -> int:
        """Record one substantive publication and return its revision.

        Heartbeat snapshots keep the current revision, so consumers can tell a
        real progress event from a liveness refresh without diffing payloads.
        """
        with self._lock:
            self._revision += 1
            return self._revision

    def snapshot(self) -> dict:
        now = self._clock()
        with self._lock:
            phase, phase_started = self._phase, self._phase_started
            iteration_started = self._iteration_started
            last_progress, counters = self._last_progress, dict(self._counters)
            revision = self._revision
        return {
            "stage": phase,
            "phase_started_at": iso(self._started_at + (phase_started - self._started)),
            "phase_elapsed": max(now - phase_started, 0.0),
            "iteration_started_at": iso(self._started_at + (iteration_started - self._started)),
            "iteration_elapsed": max(now - iteration_started, 0.0),
            "started_at": iso(self._started_at),
            "updated_at": iso(self._wall()),
            "last_progress_at": iso(self._started_at + (last_progress - self._started)),
            "last_progress_seconds": max(now - last_progress, 0.0),
            "revision": revision,
            "phase_progress": counters,
        }

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="training-monitor", daemon=True)
        self._thread.start()

    def stop(self, *, timeout: float = 5.0) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=timeout)

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                self._writer(heartbeat=True)
            except Exception:  # pragma: no cover - monitoring must never kill training
                # A failed heartbeat write is not a training failure; the next
                # tick retries. Errors still surface through the terminal publish.
                pass


def compact_progress(progress: dict) -> dict:
    result = {
        key: value
        for key, value in progress.items()
        if key not in {"history", "metrics", "evaluation_selection", "trial_progress"}
    }
    metrics = progress.get("metrics")
    if isinstance(metrics, dict):
        result["metrics"] = {
            key: value for key, value in metrics.items() if key not in {"self_play", "evaluation"}
        }
    trial = progress.get("trial_progress")
    if isinstance(trial, dict):
        result["trial_progress"] = compact_progress(trial)
    return result


def job_summary(job: dict) -> dict:
    result = {key: value for key, value in job.items() if key not in {"progress", "result", "logs"}}
    if isinstance(job.get("progress"), dict):
        result["progress"] = compact_progress(job["progress"])
    saved = job.get("result")
    if isinstance(saved, dict):
        # Older completed jobs can retain their counters only in result.json.
        # Preserve the result-then-progress precedence used by the navigator.
        progress = result.setdefault("progress", {})
        for key in ("self_play_games", "games_generated"):
            if key not in progress and key in saved:
                progress[key] = saved[key]
    registered = saved.get("model") if isinstance(saved, dict) else None
    if isinstance(registered, dict) and registered.get("id"):
        result["model_id"] = registered["id"]
    result["summary"] = True
    return result
