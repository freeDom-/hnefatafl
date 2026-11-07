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
from uuid import uuid4


# One second matches the dashboard's expected timer resolution. Publication is a
# small atomic JSON replace, so it is cheap enough to run at this cadence.
HEARTBEAT_SECONDS = 1.0

# The full progress snapshot retains the complete iteration history and is
# rewritten only on real progress events. Heartbeats go to a separate, bounded
# file so a one-second cadence never rewrites megabytes of history.
HEARTBEAT_FILENAME = "heartbeat.json"

_HEARTBEAT_KEYS = (
    "run",
    "status",
    "stage",
    "iteration",
    "active_iteration",
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
    "adaptation",
)


def iso(timestamp: float) -> str:
    """Format one epoch second as a stable UTC timestamp."""
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat(timespec="milliseconds")


# Live monitoring fields belong to the run that published them: the stage, the
# timers and the counters of a training trial. A tuning study keeps the last
# trial's data while it works between trials, but never its live stage or timers.
_LIVE_KEYS = (
    "stage",
    "phase_started_at",
    "phase_elapsed",
    "iteration_started_at",
    "iteration_elapsed",
    "active_iteration",
    "phase_progress",
    "duration",
    "updated_at",
    "last_progress_at",
    "last_progress_seconds",
)


def heartbeat_projection(state: dict) -> dict:
    """Bounded liveness snapshot; retained history and metrics stay in progress.json."""
    return {key: state[key] for key in _HEARTBEAT_KEYS if key in state}


def without_live_timing(state: dict) -> dict:
    """Retained snapshot without the live stage, timers and counters of a run."""
    return {key: value for key, value in state.items() if key not in _LIVE_KEYS}


def same_run(retained: dict, incoming: dict) -> bool:
    """Whether an incoming snapshot describes the run that wrote the retained one.

    Revisions restart with every training process, so a new Optuna trial, a
    resumed job or a restarted worker can reach the revision of an older run.
    Each monitor publishes a ``run`` identity beside its revision; a heartbeat
    may only be applied to the exact run (and trial) that produced it. Files
    written before identities existed fall back to the revision check alone.
    """
    current, arriving = retained.get("run"), incoming.get("run")
    if current is None and arriving is None:
        return True
    if current != arriving:
        return False
    trial, other = retained.get("trial"), incoming.get("trial")
    return trial is None or other is None or trial == other


def overlay_heartbeat(progress: dict, heartbeat: dict | None) -> dict:
    """Apply a heartbeat snapshot that belongs to the same publication generation.

    Revisions only increase within one run and every real progress event bumps
    one, so a heartbeat can refresh a snapshot but never replace a newer terminal
    state, and a heartbeat from another run or trial is never applied at all.
    """
    if not isinstance(heartbeat, dict):
        return progress
    revision = heartbeat.get("revision")
    if type(revision) is not int or revision != progress.get("revision"):
        return progress
    if not same_run(progress, heartbeat):
        return progress
    merged = {**progress, **heartbeat}
    nested, retained = heartbeat.get("trial_progress"), progress.get("trial_progress")
    if isinstance(retained, dict):
        # Tuning jobs nest the running trial's snapshot one level deep, so the
        # retained trial is never replaced by another trial's projection.
        merged["trial_progress"] = (
            {**retained, **nested}
            if isinstance(nested, dict) and same_run(retained, nested)
            else retained
        )
    return merged


def read_heartbeat(directory: Path) -> dict | None:
    """Load a job's heartbeat snapshot, tolerating an absent or partial file."""
    try:
        return json.loads((directory / HEARTBEAT_FILENAME).read_text())
    except (OSError, ValueError):
        return None


def publication_age(directory: Path, *, now: float | None = None) -> float | None:
    """Seconds since a local worker last published a monitoring snapshot.

    The retained ``progress.json`` and the bounded heartbeat file are written by
    the worker on the same host as this server, so their modification times share
    the server clock and never depend on an unsynchronised remote worker clock.
    The newest of the two is authoritative; ``None`` means neither exists yet.
    """
    newest: float | None = None
    for name in ("progress.json", HEARTBEAT_FILENAME):
        try:
            modified = (directory / name).stat().st_mtime
        except OSError:
            continue
        newest = modified if newest is None else max(newest, modified)
    if newest is None:
        return None
    return max(0.0, (time.time() if now is None else now) - newest)


class ProgressTicks(Protocol):
    """The per-position subset of ``ProgressSink`` a game context needs.

    A spawned actor cannot share the parent's monitor, so it forwards its own
    batched accumulator instead. Declaring the narrow surface keeps both
    callables valid without pretending the actor owns phase or absolute state.
    """

    def tick(self, **deltas: float) -> None: ...


class ProgressSink(ProgressTicks, Protocol):
    """Hot-path surface used by self-play, optimization and evaluation code.

    Implementations must stay cheap: these methods run once per searched position
    or optimizer batch and must not write files, allocate large objects or block
    on shared resources.
    """

    def phase(self, name: str, *, iteration: int | None = None) -> None: ...

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
        run: str | None = None,
    ) -> None:
        self._writer = writer
        self._interval = max(float(interval), 0.05)
        self._clock = clock
        self._wall = wall
        self._lock = threading.Lock()
        # Identity of this training process. Revisions restart with every run, so
        # consumers need it to reject a heartbeat from an earlier trial or worker.
        # Never named `_run`: that is the heartbeat thread's target.
        self._run_id = run or uuid4().hex[:16]
        self._phase = phase
        self._phase_started = clock()
        # No iteration is timed until one is entered: loading a checkpoint,
        # building a model or restoring state is initialization, not iteration
        # work, and must not publish an iteration duration.
        self._iteration: int | None = None
        self._iteration_started: float | None = None
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
            iteration, iteration_started = self._iteration, self._iteration_started
            last_progress, counters = self._last_progress, dict(self._counters)
            revision = self._revision
        snapshot = {
            "run": self._run_id,
            "stage": phase,
            "phase_started_at": iso(self._started_at + (phase_started - self._started)),
            "phase_elapsed": max(now - phase_started, 0.0),
            "started_at": iso(self._started_at),
            "updated_at": iso(self._wall()),
            "last_progress_at": iso(self._started_at + (last_progress - self._started)),
            "last_progress_seconds": max(now - last_progress, 0.0),
            "revision": revision,
            "phase_progress": counters,
        }
        if iteration is not None:
            # The active iteration is published immediately on entering it, not
            # only when the first game or epoch of that iteration finishes. Its
            # timing exists only while an iteration is actually running.
            snapshot["active_iteration"] = iteration
            if iteration_started is not None:
                snapshot["iteration_started_at"] = iso(
                    self._started_at + (iteration_started - self._started)
                )
                snapshot["iteration_elapsed"] = max(now - iteration_started, 0.0)
        return snapshot

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
        if key
        not in {
            "history",
            "metrics",
            "evaluation_selection",
            "trial_progress",
            "trials",
            "experiment_identity",
            "source_configuration",
            "parameter_importance",
            "best_configuration",
            "best_opponents",
        }
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
    reports = job.get("_import_reports")
    if job.get("status") in {"completed", "cancelled", "failed"} and isinstance(reports, dict):
        job = {**job, **reports}
    result = {
        key: value
        for key, value in job.items()
        if key not in {"progress", "result", "logs", "_import_reports"}
    }
    if isinstance(job.get("progress"), dict):
        result["progress"] = compact_progress(job["progress"])
    saved = job.get("result")
    if isinstance(saved, dict):
        # Older completed jobs can retain their counters only in result.json.
        # Preserve the result-then-progress precedence used by the navigator.
        progress = result.setdefault("progress", {})
        for key in (
            "iteration",
            "active_iteration",
            "total_iterations",
            "self_play_games",
            "games_generated",
            "best_models",
        ):
            if key not in progress and key in saved:
                value = saved[key]
                if key == "best_models":
                    value = imported_report_summary({key: value}).get(key, [])
                progress[key] = value
    registered = saved.get("model") if isinstance(saved, dict) else None
    if isinstance(registered, dict) and registered.get("id"):
        result["model_id"] = registered["id"]
    result["summary"] = True
    return result


def imported_job_summary(job: dict) -> dict:
    """Return imported navigator metadata without reopening restored documents."""
    result = job_summary(job)
    if isinstance(result.get("error"), str):
        result["error"] = result["error"][:2000]
    return result


def imported_report_summary(report: dict) -> dict:
    """Cache navigator fields once at import; full reports remain separate artifacts.

    Scalar counters/maps have a 32 KiB budget. Retained model associations carry
    only unique 24-character IDs, bounded by the archive's 2,048-member ceiling;
    no unfamiliar list/diagnostic can copy another full history into SQLite.
    """
    keys = (
        *_HEARTBEAT_KEYS,
        "trial",
        "completed_trials",
        "total_trials",
        "best_trial",
        "best_score",
        "metrics",
        "model",
        "performance",
        "trial_progress",
    )

    budget = 32 * 1024

    def small(value, depth=0):
        nonlocal budget
        if isinstance(value, dict):
            if depth >= 3 or len(value) > 128:
                return {}
            result = {}
            for key, item in value.items():
                if (
                    key in {"history", "self_play", "games"}
                    or len(key) > 200
                    or isinstance(item, (list, tuple))
                ):
                    continue
                projected = small(item, depth + 1)
                if projected is not None:
                    result[key] = projected
            return result
        if (
            type(value) not in (int, float, bool, type(None), str)
            or isinstance(value, str)
            and len(value) > 2000
        ):
            return None
        size = len(json.dumps(value, ensure_ascii=False).encode()) + 205
        if size > budget:
            return None
        budget -= size
        return value

    result = {}
    # Identity/counters take precedence over optional metrics within the budget.
    for key in dict.fromkeys(("iteration", "self_play_games", "games_generated", "model", *keys)):
        if key in report and not isinstance(report[key], (list, tuple)):
            value = (
                {"id": report[key].get("id")}
                if key == "model" and isinstance(report[key], dict)
                else report[key]
            )
            projected = small(value)
            if projected is not None:
                result[key] = projected
    while len(json.dumps(result, ensure_ascii=False).encode()) > 32 * 1024:
        result.pop(next(reversed(result)))
    retained = report.get("best_models")
    if isinstance(retained, list):
        references = []
        seen = set()
        for entry in retained:
            identifier = entry.get("model_id") if isinstance(entry, dict) else None
            if (
                not isinstance(identifier, str)
                or len(identifier) != 24
                or any(char not in "0123456789abcdef" for char in identifier)
                or identifier in seen
            ):
                continue
            seen.add(identifier)
            references.append({"model_id": identifier})
            if len(references) == 2048:
                break
        result["best_models"] = references
    return result
