"""Small reproducible real-HTTP read workload on a disposable SQLite fixture.

The benchmark only ever opens its own temporary fixture directory. Both the
driver and the internal API worker independently verify that directory, the
imported backend source and a deterministic benchmark environment before any
application module is imported. No AI search, training, model load or operator
storage is touched. Results are short same-host observations, not capacity.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import re
import secrets
import shutil
import signal
import socket
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import closing, contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator
from uuid import uuid4

import httpx

FIXTURE_PREFIX = "hnefatafl-load-"
MARKER_NAME = ".benchmark-only"
FIXTURE_IDENTITY = re.compile(r"^[0-9a-f]{32}$")
SCRIPT_PATH = Path(__file__).resolve()
SOURCE_ROOT = SCRIPT_PATH.parents[1]
LOCK_NAME = "uv.lock"
LOOPBACK = "127.0.0.1"

# Operator configuration that must never leak into a benchmark process. Any
# matching variable is removed for the duration of the run and restored after.
PROXY_KEYS = frozenset(
    {
        "HTTP_PROXY",
        "http_proxy",
        "HTTPS_PROXY",
        "https_proxy",
        "ALL_PROXY",
        "all_proxy",
        "NO_PROXY",
        "no_proxy",
    }
)
THREAD_KEYS = frozenset(
    {
        "OMP_NUM_THREADS",
        "MKL_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
    }
)

# The documented runner wraps the benchmark in `timeout --kill-after=15s`.
# Keep the whole internal shutdown budget below that so a cooperative SIGTERM
# is never escalated to SIGKILL before cleanup has a chance to finish.
EXTERNAL_KILL_AFTER_SECONDS = 15.0
STOP_TIMEOUT_SECONDS = 4.0
WORKER_DRAIN_SECONDS = 3.0
TERMINATE_GRACE_SECONDS = 3.0
TERMINATE_FORCE_SECONDS = 2.0
BARRIER_TIMEOUT_SECONDS = 30.0
STARTUP_TIMEOUT_SECONDS = 45.0
CANCEL_POLL_SECONDS = 0.2
CLIENT_TIMEOUT = httpx.Timeout(10.0, connect=5.0, pool=5.0)

# Top-level paths the application owns inside a fixture. Validation rejects
# symlinks and out-of-fixture resolution for these and for every other entry.
KNOWN_STORAGE = {
    MARKER_NAME: "file",
    "hnefatafl.sqlite3": "file",
    "hnefatafl.sqlite3-wal": "file",
    "hnefatafl.sqlite3-shm": "file",
    "hnefatafl.sqlite3-journal": "file",
    ".deployment-gate": "file",
    "jobs": "dir",
    "models": "dir",
    "tournaments": "dir",
    ".uploads": "dir",
    "jobs/.study-deletions": "dir",
}
SQLITE_STORAGE_NAMES = (
    "hnefatafl.sqlite3",
    "hnefatafl.sqlite3-wal",
    "hnefatafl.sqlite3-shm",
    "hnefatafl.sqlite3-journal",
)
OTHER_STORAGE_NAMES = (
    MARKER_NAME,
    ".deployment-gate",
    "jobs",
    "models",
    "tournaments",
    ".uploads",
)

LOCAL_GAMES_PER_PLAYER = 3
SINGLE_GAMES_PER_PLAYER = 2
ONLINE_GAMES_PER_PLAYER = 3
# A genuine accepted repetition draw: no forged result and no AI search.
REPETITION_MOVES = ((3, 2), (17, 16), (2, 3), (16, 17)) * 2
ENDPOINT_LABELS = (
    "dashboard_online",
    "dashboard_local",
    "dashboard_single",
    "profile",
    "personal_statistics",
    "online_statistics",
    "leaderboard",
    "lobby",
    "active_game",
    "completed_replay",
    "player_tournaments",
    "player_bracket",
    "recent_records",
)


class BenchmarkCancelled(Exception):
    """Raised cooperatively when the operator cancels the benchmark."""


@dataclass(frozen=True)
class Fixture:
    path: Path
    identity: str


@dataclass
class FixtureState:
    users: int
    account_ids: list[str]
    admin_id: str
    local_ids: list[list[str]]
    single_ids: list[list[str]]
    active_ids: list[str]
    replay_ids: list[str]
    waiting_ids: list[str]
    started_tournament_id: str
    upcoming_tournament_id: str


@dataclass
class CleanupResult:
    """Outcome of releasing benchmark resources, including what was retained."""

    problems: list[str] = field(default_factory=list)
    process_stopped: bool = True
    fixture_removed: bool | None = None
    retained_fixture: str | None = None

    @property
    def ok(self) -> bool:
        return not self.problems


@dataclass
class PhaseResult:
    """One measured concurrency phase plus worker/cleanup diagnostics."""

    rows: list[dict]
    maximum_inflight: int
    cancelled: bool
    worker_problems: list[str]
    workers_stopped: bool


@dataclass
class BenchmarkResources:
    """Owns every disposable resource and closes them in a bounded order."""

    cancel: threading.Event = field(default_factory=threading.Event)
    signal_name: str | None = None
    clients: list[httpx.Client] = field(default_factory=list)
    process: subprocess.Popen | None = None
    log: Any = None
    fixture: Fixture | None = None

    def register_client(self, client: httpx.Client) -> httpx.Client:
        # Register before the first request so a failed login still cleans up.
        self.clients.append(client)
        return client

    def terminate_process(
        self, *, grace: float = TERMINATE_GRACE_SECONDS, force: float = TERMINATE_FORCE_SECONDS
    ) -> bool:
        """Stop and reap the owned child; return False if it could not be reaped."""
        process = self.process
        if process is None:
            return True
        try:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=grace)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=force)
            else:
                process.wait()
        except Exception:  # noqa: BLE001 - reported by the caller as a cleanup failure
            return False
        return process.poll() is not None

    def close(self, *, close_clients: bool = True) -> CleanupResult:
        """Release every owned resource; one failure never skips the others."""
        result = CleanupResult()
        # Stop the child first so a terminated server unblocks any in-flight
        # request before the fixture is touched.
        result.process_stopped = self.terminate_process()
        if not result.process_stopped:
            result.problems.append("owned subprocess did not stop and was not reaped")
        if close_clients:
            for client in self.clients:
                try:
                    client.close()
                except Exception as error:  # noqa: BLE001 - cleanup must continue
                    result.problems.append(f"client close: {type(error).__name__}")
        self.clients.clear()
        if self.log is not None:
            try:
                self.log.close()
            except Exception as error:  # noqa: BLE001
                result.problems.append(f"log close: {type(error).__name__}")
        if self.fixture is not None:
            if not result.process_stopped:
                # Never delete a database an un-reaped server might still use.
                result.fixture_removed = False
                result.retained_fixture = str(self.fixture.path)
                result.problems.append("fixture retained because the subprocess may still use it")
            else:
                try:
                    result.fixture_removed = cleanup_fixture_directory(self.fixture)
                except Exception as error:  # noqa: BLE001
                    result.fixture_removed = False
                    result.problems.append(f"fixture cleanup: {type(error).__name__}")
                if result.fixture_removed is False:
                    result.retained_fixture = str(self.fixture.path)
                    result.problems.append("fixture cleanup refused; fixture retained")
        return result


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


def git_revision(source: Path) -> str:
    """Verified revision of a clean repository root; used by the provenance check."""
    source = source.resolve()
    try:
        revision = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=source, text=True, stderr=subprocess.DEVNULL
        ).strip()
        root = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=source,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        dirty = subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=source, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError) as error:
        raise ValueError("Benchmark requires a verifiable Git checkout") from error
    if Path(root).resolve() != source or dirty:
        raise ValueError("Benchmark requires the clean repository root; commit changes first")
    if not (source / "hnefatafl" / "server.py").is_file():
        raise ValueError("Source does not contain the Hnefatafl server")
    return revision


def verified_source(source: Path) -> str:
    """The runner and backend are one checkout, so both share one revision."""
    source = Path(source).resolve()
    if source != SOURCE_ROOT:
        raise ValueError("--source-root must be the benchmark runner's own checkout")
    if not (source / "scripts" / SCRIPT_PATH.name).is_file():
        raise ValueError("Benchmark runner is missing from the verified checkout")
    return git_revision(source)


def assert_import_origin(source: Path) -> dict[str, str]:
    """Fail unless the imported backend modules come from the verified checkout.

    Returns the verified imported locations so the report can record them rather
    than only checking them internally.
    """
    import hnefatafl.engine as engine_module
    import hnefatafl.server as server_module

    expected = {
        "server": (source / "hnefatafl" / "server.py").resolve(),
        "engine": (source / "hnefatafl" / "engine.py").resolve(),
    }
    actual = {
        "server": Path(server_module.__file__).resolve(),
        "engine": Path(engine_module.__file__).resolve(),
    }
    if actual != expected:
        raise ValueError("Imported backend does not match the verified source checkout")
    return {key: str(value) for key, value in actual.items()}


def file_fingerprint(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return "unavailable"


def dependency_versions() -> dict[str, str]:
    import fastapi
    import pydantic
    import uvicorn

    return {
        "python": platform.python_version(),
        "sqlite": sqlite3.sqlite_version,
        "fastapi": fastapi.__version__,
        "uvicorn": uvicorn.__version__,
        "httpx": httpx.__version__,
        "pydantic": pydantic.__version__,
    }


# ---------------------------------------------------------------------------
# Parameters
# ---------------------------------------------------------------------------


def validate_parameters(users: int, rounds: int, concurrency: str, port: int) -> list[int]:
    try:
        levels = [int(value) for value in concurrency.split(",")]
    except ValueError as error:
        raise ValueError("Concurrency must be a comma-separated integer list") from error
    if (
        not 2 <= users <= 16
        or not 1 <= rounds <= 10
        or not 1024 <= port <= 65535
        or not 1 <= len(levels) <= 5
        or len(set(levels)) != len(levels)
        or any(not 1 <= level <= users for level in levels)
    ):
        raise ValueError(
            "Bounds: users 2–16, rounds 1–10, 1–5 distinct concurrency levels <= users, port 1024–65535"
        )
    return levels


def expected_request_count(users: int, rounds: int, endpoint_count: int) -> int:
    return users * rounds * endpoint_count


def validate_phase_counts(
    rows: list[dict], users: int, rounds: int, endpoint_labels: tuple[str, ...]
) -> list[str]:
    """Every actor/endpoint/concurrency combination must be fully observed."""
    problems: list[str] = []
    expected_total = expected_request_count(users, rounds, len(endpoint_labels))
    if len(rows) != expected_total:
        problems.append(f"requests {len(rows)} != expected {expected_total}")
    per_actor = Counter(row["actor_index"] for row in rows)
    per_endpoint = Counter(row["endpoint"] for row in rows)
    per_pair = Counter((row["actor_index"], row["endpoint"]) for row in rows)
    for actor in range(users):
        if per_actor[actor] != rounds * len(endpoint_labels):
            problems.append(
                f"actor {actor} requests {per_actor[actor]} != {rounds * len(endpoint_labels)}"
            )
        for label in endpoint_labels:
            if per_pair[(actor, label)] != rounds:
                problems.append(
                    f"actor {actor} endpoint {label} requests {per_pair[(actor, label)]} != {rounds}"
                )
    for label in endpoint_labels:
        if per_endpoint[label] != users * rounds:
            problems.append(f"endpoint {label} requests {per_endpoint[label]} != {users * rounds}")
    return problems


# ---------------------------------------------------------------------------
# Fixture directory ownership
# ---------------------------------------------------------------------------


class MountInfoUnavailable(RuntimeError):
    """Mount information could not be read or resolved reliably."""


# The kernel escapes these four characters in mountinfo paths. Decoding them in
# a single left-to-right pass keeps a literal backslash (\134) from being
# decoded twice.
MOUNTINFO_ESCAPES = {
    "040": " ",
    "011": "\t",
    "012": "\n",
    "134": "\\",
}


def decode_mountinfo_path(value: str) -> str:
    """Decode mountinfo octal escapes (\\040, \\011, \\012, \\134) in one pass."""
    if "\\" not in value:
        return value
    result: list[str] = []
    index = 0
    length = len(value)
    while index < length:
        if value[index] == "\\" and index + 4 <= length:
            replacement = MOUNTINFO_ESCAPES.get(value[index + 1 : index + 4])
            if replacement is not None:
                result.append(replacement)
                index += 4
                continue
        result.append(value[index])
        index += 1
    return "".join(result)


def parse_mountinfo(text: str) -> list[Path]:
    """Decoded mount points from /proc/self/mountinfo contents."""
    points: list[Path] = []
    for line in text.splitlines():
        before, separator, _after = line.partition(" - ")
        if not separator:
            continue
        fields = before.split()
        if len(fields) >= 5:
            points.append(Path(decode_mountinfo_path(fields[4])))
    return points


def _mount_points() -> list[Path] | None:
    """Mount points from /proc/self/mountinfo, or None when unavailable."""
    try:
        text = Path("/proc/self/mountinfo").read_text(encoding="utf-8")
    except OSError:
        return None
    return parse_mountinfo(text)


def fixture_mount_boundaries(root: Path, *, points: list[Path] | None = None) -> list[Path]:
    """Mount points at or beneath the fixture root, including same-device binds.

    Mountinfo lists every mount, so a bind mount of operator storage onto a path
    inside the fixture is detected even though it shares the root's device. An
    empty list means the check ran and found no boundary; an unavailable or
    unresolvable mount table raises ``MountInfoUnavailable`` so an unsuccessful
    safety check is never mistaken for a clean one.
    """
    if points is None:
        points = _mount_points()
    if points is None:
        raise MountInfoUnavailable("mount information is unavailable")
    try:
        resolved_root = root.resolve()
    except OSError as error:
        raise MountInfoUnavailable("fixture root cannot be resolved") from error
    boundaries: list[Path] = []
    for point in points:
        try:
            resolved = point.resolve()
        except OSError as error:
            raise MountInfoUnavailable("mount point cannot be resolved") from error
        if resolved == resolved_root or resolved.is_relative_to(resolved_root):
            boundaries.append(resolved)
    return boundaries


def validate_fixture_storage(fixture: Fixture) -> None:
    """Reject indirect storage paths before the backend can open them.

    A directory name and marker do not prove that arbitrary preexisting content
    is disposable. Every entry must be a real path on the fixture's own
    filesystem, inside the fixture, and the application-owned files must not be
    hard-linked to external storage. Symlinks and nested mounts (including
    same-filesystem bind mounts) are rejected anywhere in the tree. This is a
    proportionate guard for an optional local benchmark, not a defence against
    an arbitrary hostile concurrent same-user process.
    """
    root = fixture.path
    boundaries = fixture_mount_boundaries(root)
    if boundaries:
        raise ValueError(
            "Fixture contains a mount boundary: " + ", ".join(str(path) for path in boundaries)
        )
    try:
        root_device = root.stat().st_dev
    except OSError as error:
        raise ValueError("Fixture directory is not readable") from error
    for current, directories, files in os.walk(root, followlinks=False):
        base = Path(current)
        for name in (*directories, *files):
            entry = base / name
            if entry.is_symlink():
                raise ValueError(f"Fixture contains a symlink: {entry.relative_to(root)}")
            try:
                entry_stat = entry.stat()
            except OSError as error:
                raise ValueError(
                    f"Fixture entry is not readable: {entry.relative_to(root)}"
                ) from error
            if entry_stat.st_dev != root_device:
                raise ValueError(
                    f"Fixture entry crosses a mount boundary: {entry.relative_to(root)}"
                )
            if not entry.resolve().is_relative_to(root):
                raise ValueError(f"Fixture path escapes the fixture: {entry.relative_to(root)}")
    for name, kind in KNOWN_STORAGE.items():
        path = root / name
        if path.is_symlink():
            raise ValueError(f"Fixture storage path {name} must not be a symlink")
        if not path.exists():
            continue
        path_stat = path.stat()
        if path_stat.st_dev != root_device:
            raise ValueError(f"Fixture storage path {name} crosses a mount boundary")
        if not path.resolve().is_relative_to(root):
            raise ValueError(f"Fixture storage path {name} escapes the fixture")
        if kind == "file":
            if not path.is_file():
                raise ValueError(f"Fixture storage path {name} must be a regular file")
            if path_stat.st_nlink > 1:
                raise ValueError(f"Fixture storage file {name} is hard-linked outside the fixture")
        if kind == "dir" and not path.is_dir():
            raise ValueError(f"Fixture storage path {name} must be a directory")


def validate_fixture_directory(
    data: str | Path | None, *, require_config: bool = True, require_storage: bool = True
) -> Fixture:
    if data is None:
        raise ValueError("Benchmark requires an explicit fixture directory")
    raw = Path(data)
    if not raw.is_absolute():
        raise ValueError("Fixture directory must be an absolute path")
    if raw.is_symlink():
        raise ValueError("Fixture directory must not be a symlink")
    try:
        resolved = raw.resolve(strict=True)
    except OSError as error:
        raise ValueError("Fixture directory does not exist") from error
    if not resolved.is_dir():
        raise ValueError("Fixture path must be a directory")
    if not resolved.name.startswith(FIXTURE_PREFIX):
        raise ValueError("Refusing non-benchmark data directory")
    status = resolved.stat()
    if status.st_uid != os.getuid():
        raise ValueError("Fixture directory must be owned by the benchmark user")
    if status.st_mode & 0o077:
        raise ValueError("Fixture directory must not be group/world accessible")
    marker = resolved / MARKER_NAME
    if marker.is_symlink() or not marker.is_file():
        raise ValueError("Fixture marker is missing or not a regular file")
    identity = marker.read_text(encoding="utf-8").strip()
    if not FIXTURE_IDENTITY.fullmatch(identity):
        raise ValueError("Fixture marker identity is invalid")
    if require_config:
        configured = os.environ.get("HNEFATAFL_DATA_DIR")
        if configured is None or Path(configured).resolve() != resolved:
            raise ValueError("Fixture directory does not match HNEFATAFL_DATA_DIR")
    fixture = Fixture(resolved, identity)
    if require_storage:
        validate_fixture_storage(fixture)
    return fixture


def create_fixture_directory(base: str | Path | None = None) -> Fixture:
    directory = Path(tempfile.mkdtemp(prefix=FIXTURE_PREFIX, dir=base))
    os.chmod(directory, 0o700)
    marker = directory / MARKER_NAME
    marker.write_text(uuid4().hex, encoding="utf-8")
    os.chmod(marker, 0o600)
    return validate_fixture_directory(directory, require_config=False)


def cleanup_fixture_directory(fixture: Fixture) -> bool:
    """Remove only the fixture this process created; never an operator path.

    ``shutil.rmtree`` removes symlinks without following them, so a foreign link
    inside our own fixture is safe to delete and cannot reach operator storage.
    A nested mount is refused rather than traversed, because recursive removal
    would delete the mounted contents. The fixture is retained in that case and
    the caller records a cleanup failure.
    """
    try:
        current = validate_fixture_directory(
            fixture.path, require_config=False, require_storage=False
        )
    except ValueError:
        return False
    if current.identity != fixture.identity:
        return False
    try:
        boundaries = fixture_mount_boundaries(current.path)
    except MountInfoUnavailable:
        # Without reliable mount information, recursive removal cannot be
        # proven safe; retain the fixture and report a cleanup failure.
        return False
    if boundaries:
        return False
    shutil.rmtree(current.path, ignore_errors=True)
    return not current.path.exists()


# ---------------------------------------------------------------------------
# Deterministic environment
# ---------------------------------------------------------------------------


def is_benchmark_config_key(key: str) -> bool:
    return key.startswith("HNEFATAFL_") or key in PROXY_KEYS or key in THREAD_KEYS


def deterministic_environment(data: Path, origin: str) -> dict[str, str]:
    """Explicit benchmark-local configuration; no operator value is inherited."""
    return {
        "HNEFATAFL_DATA_DIR": str(data),
        "HNEFATAFL_ENV": "development",
        "HNEFATAFL_PUBLIC_URL": origin,
        "HNEFATAFL_ALLOWED_HOSTS": "127.0.0.1,localhost",
        "HNEFATAFL_TRUSTED_PROXIES": "",
        "HNEFATAFL_SESSION_SECONDS": "3600",
        "HNEFATAFL_REGISTRATION_ENABLED": "false",
        "HNEFATAFL_ONLINE_ENABLED": "true",
        "HNEFATAFL_PUBLIC_ONLINE_ENABLED": "true",
        "HNEFATAFL_ONLINE_TIMEOUT_POLICY_APPROVED": "true",
        "HNEFATAFL_ONLINE_SWEEP_SECONDS": "1",
        "HNEFATAFL_ONLINE_SWEEP_BATCH": "50",
        "HNEFATAFL_ONLINE_WAITING_LIMIT": "1000",
        "HNEFATAFL_ONLINE_CREATION_LIMIT": "1000",
        "HNEFATAFL_ONLINE_CREATION_WINDOW_SECONDS": "900",
        "HNEFATAFL_PUBLIC_RATE_LIMIT": "100000",
        "HNEFATAFL_PUBLIC_RATE_WINDOW_SECONDS": "60",
        "HNEFATAFL_ACCOUNT_GLOBAL_REQUESTS": "100000",
        "HNEFATAFL_ACCOUNT_GLOBAL_HASHES": "100000",
        "HNEFATAFL_ACCOUNT_GLOBAL_MAIL": "100000",
        "HNEFATAFL_ACCOUNT_LIMIT_KEYS": "100000",
        "HNEFATAFL_ACCOUNT_REGISTER_IP": "100000",
        "HNEFATAFL_ACCOUNT_REGISTER_IDENTITY": "100000",
        "HNEFATAFL_ACCOUNT_RESEND_IP": "100000",
        "HNEFATAFL_ACCOUNT_RESEND_IDENTITY": "100000",
        "HNEFATAFL_ACCOUNT_RESET_IP": "100000",
        "HNEFATAFL_ACCOUNT_RESET_IDENTITY": "100000",
        "HNEFATAFL_ACCOUNT_CONFIRM_IP": "100000",
        "HNEFATAFL_ACCOUNT_CONFIRM_IDENTITY": "100000",
        "HNEFATAFL_ACCOUNT_QUEUE_LIMIT": "10000",
        "HNEFATAFL_ACCOUNT_MAX_ATTEMPTS": "10",
        "OMP_NUM_THREADS": "2",
        "MKL_NUM_THREADS": "2",
        "OPENBLAS_NUM_THREADS": "2",
        "NUMEXPR_NUM_THREADS": "2",
    }


@contextmanager
def fixture_environment(fixture: Fixture, *, origin: str) -> Iterator[None]:
    """Sanitize the process environment before any application import.

    Every ``HNEFATAFL_*``, proxy and numeric-thread variable is snapshotted,
    removed, replaced with benchmark-local values and restored afterwards, even
    if the body raises.
    """
    validate_fixture_directory(fixture.path, require_config=False)
    changes = deterministic_environment(fixture.path, origin)
    touched = sorted({key for key in os.environ if is_benchmark_config_key(key)} | set(changes))
    previous = {key: os.environ.get(key) for key in touched}
    for key in touched:
        os.environ.pop(key, None)
    os.environ.update(changes)
    try:
        yield
    finally:
        for key in changes:
            os.environ.pop(key, None)
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


# ---------------------------------------------------------------------------
# Server identity
# ---------------------------------------------------------------------------


def mark_fixture_health(app, fixture_identity: str) -> None:
    """Identify this disposable server through its existing public health path."""

    @app.middleware("http")
    async def fixture_header(request, call_next):
        response = await call_next(request)
        if request.method == "GET" and request.url.path == "/api/health":
            response.headers["X-Hnefatafl-Benchmark-Fixture"] = fixture_identity
        return response


def bind_exclusive_socket(host: str, port: int) -> socket.socket:
    """Bind without SO_REUSEPORT so an unrelated live listener is never reused.

    SO_REUSEADDR only reclaims TIME_WAIT addresses; on Linux a second bind over
    an active listener still fails with EADDRINUSE, which the caller reports.
    TCP_NODELAY is inherited by accepted sockets and keeps the measured latency
    free of the Nagle/delayed-ACK artifact a pre-bound socket would introduce.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    try:
        sock.bind((host, port))
        sock.listen(128)
    except OSError:
        sock.close()
        raise
    return sock


def serve(source: Path, data: Path | None, port: int) -> None:
    """Internal worker: independently prove the fixture and source before import."""
    fixture = validate_fixture_directory(data)
    revision = verified_source(source)
    origin = f"http://{LOOPBACK}:{port}"
    with fixture_environment(fixture, origin=origin):
        sys.path.insert(0, str(source))
        from hnefatafl.online_settings import OnlineSettings
        from hnefatafl.server import create_app

        assert_import_origin(source)
        app = create_app(
            fixture.path,
            online_settings=OnlineSettings(enabled=True),
            account_background=False,
            online_background=False,
        )
        mark_fixture_health(app, fixture.identity)
        import uvicorn

        sock = bind_exclusive_socket(LOOPBACK, port)
        config = uvicorn.Config(
            app,
            host=LOOPBACK,
            port=port,
            access_log=False,
            log_level="warning",
            workers=1,
        )
        server = uvicorn.Server(config)
        # Expose the verified revision through the log stream for provenance.
        print(f"benchmark worker revision={revision}", flush=True)
        server.run(sockets=[sock])


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def percentile(values, fraction):
    values = sorted(values)
    return values[max(0, math.ceil(len(values) * fraction) - 1)]


def metrics(rows):
    if not rows:
        return {
            "requests": 0,
            "successes": 0,
            "failures": 0,
            "status_counts": {},
            "latency_ms": {"median": None, "p95": None, "p99": None, "max": None},
            "response_bytes": {"total": 0, "max": None},
        }
    values = [row["milliseconds"] for row in rows]
    successes = sum(row["status"] == 200 for row in rows)
    return {
        "requests": len(rows),
        "successes": successes,
        "failures": len(rows) - successes,
        "status_counts": dict(Counter(str(row["status"]) for row in rows)),
        "latency_ms": {
            "median": statistics.median(values),
            "p95": percentile(values, 0.95),
            "p99": percentile(values, 0.99),
            "max": max(values),
        },
        "response_bytes": {
            "total": sum(row["bytes"] for row in rows),
            "max": max(row["bytes"] for row in rows),
        },
    }


def require(response, expected=200):
    if response.status_code != expected:
        # Never print bodies, cookies, CSRF tokens or credentials.
        raise RuntimeError(
            f"Fixture request failed: {response.request.method} "
            f"{response.request.url.path} status={response.status_code}"
        )
    return response.json()


# ---------------------------------------------------------------------------
# Environment metadata (unavailable is distinct from a measured zero)
# ---------------------------------------------------------------------------


def cpu_model() -> str | None:
    try:
        for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
            if line.lower().startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        return None
    return None


def cpu_affinity_count() -> int | None:
    try:
        return len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        return None


@dataclass(frozen=True)
class CgroupMount:
    """A cgroup2 mount: the subtree root inside the filesystem and its mount point."""

    root: str
    point: Path


def parse_cgroup_mount(text: str) -> CgroupMount | None:
    """Resolve the cgroup2 mount root and point from mountinfo contents."""
    for line in text.splitlines():
        before, separator, after = line.partition(" - ")
        if not separator:
            continue
        fields = before.split()
        if len(fields) >= 5 and after.split() and after.split()[0] == "cgroup2":
            return CgroupMount(
                root=decode_mountinfo_path(fields[3]),
                point=Path(decode_mountinfo_path(fields[4])),
            )
    return None


def cgroup_v2_mount() -> CgroupMount | None:
    """Resolve the cgroup2 mount root and point from /proc/self/mountinfo."""
    try:
        text = Path("/proc/self/mountinfo").read_text(encoding="utf-8")
    except OSError:
        return None
    return parse_cgroup_mount(text)


def _process_cgroup_path() -> str | None:
    """This process's cgroup v2 path relative to the hierarchy root."""
    try:
        for line in Path("/proc/self/cgroup").read_text(encoding="utf-8").splitlines():
            if line.startswith("0::"):
                return line.split("::", 1)[1].strip()
    except OSError:
        return None
    return None


def cgroup_v2_leaf(mount: CgroupMount) -> Path | None:
    """Resolve this process's cgroup directory under a possibly delegated mount.

    ``/proc/self/cgroup`` reports the path relative to the whole cgroup
    hierarchy. A mountinfo root other than ``/`` means the mounted filesystem is
    a delegated subtree, so the root prefix must be stripped before joining with
    the mount point. Returns ``None`` when the process lies outside that subtree.
    """
    cgroup_path = _process_cgroup_path()
    if cgroup_path is None:
        return None
    root = mount.root.rstrip("/")
    if root:
        if cgroup_path == root:
            relative = ""
        elif cgroup_path.startswith(root + "/"):
            relative = cgroup_path[len(root) + 1 :]
        else:
            return None
    else:
        relative = cgroup_path.strip("/")
    return mount.point / relative if relative else mount.point


def _cgroup_v2_hierarchy(name: str) -> list[str]:
    """Values for one cgroup v2 file from the process slice up to the mount root."""
    mount = cgroup_v2_mount()
    if mount is None:
        return []
    leaf = cgroup_v2_leaf(mount)
    if leaf is None:
        return []
    values: list[str] = []
    current = leaf
    while True:
        candidate = current / name
        try:
            if candidate.is_file():
                values.append(candidate.read_text(encoding="utf-8").strip())
        except OSError:
            pass
        if current == mount.point or mount.point not in current.parents:
            break
        current = current.parent
    return values


def cgroup_cpu_quota_cores() -> float | None:
    ratios: list[float] = []
    for value in _cgroup_v2_hierarchy("cpu.max"):
        parts = value.split()
        try:
            if parts[0] != "max":
                ratios.append(int(parts[0]) / int(parts[1]))
        except (ValueError, IndexError):
            continue
    if ratios:
        return min(ratios)
    # cgroup v1 fallback: this reads the fixed controller root and does not
    # resolve nested slices or delegated mounts, so it may over-report the
    # available quota. It is retained only for older hosts and returns null
    # when the files are absent.
    try:
        quota = int(Path("/sys/fs/cgroup/cpu/cpu.cfs_quota_us").read_text(encoding="utf-8"))
        period = int(Path("/sys/fs/cgroup/cpu/cpu.cfs_period_us").read_text(encoding="utf-8"))
        if quota > 0 and period > 0:
            return quota / period
    except (OSError, ValueError):
        pass
    return None


def cgroup_memory_limit_bytes() -> int | None:
    limits: list[int] = []
    for value in _cgroup_v2_hierarchy("memory.max"):
        if value != "max":
            try:
                limits.append(int(value))
            except ValueError:
                continue
    if limits:
        return min(limits)
    # cgroup v1 fallback with the same fixed-root limitation as the CPU quota.
    try:
        value = int(Path("/sys/fs/cgroup/memory/memory.limit_in_bytes").read_text(encoding="utf-8"))
        if value < (1 << 60):
            return value
    except (OSError, ValueError):
        pass
    return None


def system_memory_bytes() -> int | None:
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


def environment_metadata(port: int, *, caller_numeric_threads: dict | None = None) -> dict:
    """Describe the actual benchmark environment; unavailable values stay null."""
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "cpu_model": cpu_model(),
        "logical_cpus": os.cpu_count(),
        "affinity_cpus": cpu_affinity_count(),
        "cpu_quota_cores": cgroup_cpu_quota_cores(),
        "system_memory_bytes": system_memory_bytes(),
        "cgroup_memory_limit_bytes": cgroup_memory_limit_bytes(),
        "caller_numeric_threads": caller_numeric_threads
        if caller_numeric_threads is not None
        else {key: os.environ.get(key) for key in sorted(THREAD_KEYS)},
        "benchmark_numeric_threads": {key: os.environ.get(key) for key in sorted(THREAD_KEYS)},
        "sqlite": sqlite3.sqlite_version,
        "client": "httpx keep-alive clients; one sequential worker per distinct account",
        "server": "uvicorn one worker, background account/online sweep loops disabled",
        "port": port,
    }


# ---------------------------------------------------------------------------
# Cancellation-aware request execution
# ---------------------------------------------------------------------------


def waves(items: list[int], size: int) -> Iterator[list[int]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def run_actor_requests(
    actor_index: int,
    endpoints: list[tuple[str, str]],
    rounds: int,
    cancel: threading.Event,
    perform: Callable[[int, str, str], None],
    first_barrier: threading.Barrier | None = None,
) -> int:
    """Issue one actor's requests, stopping before the next request on cancel."""
    issued = 0
    for _ in range(rounds):
        for label, path in endpoints:
            if cancel.is_set():
                return issued
            if first_barrier is not None:
                try:
                    first_barrier.wait(timeout=BARRIER_TIMEOUT_SECONDS)
                except threading.BrokenBarrierError:
                    return issued
                first_barrier = None
                # A cancellation can arrive while the wave waits at the barrier.
                if cancel.is_set():
                    return issued
            perform(actor_index, label, path)
            issued += 1
    return issued


def measure_level(
    level: int,
    actor_ids: list[int],
    templates: list[list[tuple[str, str]]],
    rounds: int,
    resources: BenchmarkResources,
    actors: list[httpx.Client],
    *,
    unblock: Callable[[], bool] | None = None,
) -> PhaseResult:
    """Measure one concurrency level with synchronized waves and bounded stop.

    The wait tracks only still-pending futures, so a completed actor never makes
    the loop spin until the slowest one finishes. On cancellation the barrier is
    aborted, queued futures are cancelled, and the server can be stopped to
    unblock an in-flight request within the shutdown budget.
    """
    rows: list[dict] = []
    lock = threading.Lock()
    active: set[int] = set()
    maximum = [0]
    worker_problems: list[str] = []

    def perform(actor_index: int, label: str, path: str) -> None:
        with lock:
            active.add(actor_index)
            maximum[0] = max(maximum[0], len(active))
        started = time.perf_counter()
        status = 0
        size = 0
        error = None
        try:
            response = actors[actor_index].get(path)
            status = response.status_code
            size = len(response.content)
        except Exception as caught:  # noqa: BLE001 - record transport failure
            error = type(caught).__name__
        finally:
            with lock:
                active.discard(actor_index)
        with lock:
            rows.append(
                {
                    "actor_index": actor_index,
                    "endpoint": label,
                    "status": status,
                    "bytes": size,
                    "milliseconds": (time.perf_counter() - started) * 1000,
                    "error_type": error,
                }
            )

    executor = ThreadPoolExecutor(max_workers=level, thread_name_prefix="bench")
    futures: list = []
    cancelled = False
    try:
        for wave in waves(actor_ids, level):
            if resources.cancel.is_set():
                cancelled = True
                break
            barrier = threading.Barrier(len(wave))
            wave_futures = [
                executor.submit(
                    run_actor_requests,
                    actor,
                    templates[actor],
                    rounds,
                    resources.cancel,
                    perform,
                    barrier,
                )
                for actor in wave
            ]
            futures.extend(wave_futures)
            pending = set(wave_futures)
            while pending:
                if resources.cancel.is_set():
                    cancelled = True
                    barrier.abort()
                    break
                _, pending = wait(pending, timeout=CANCEL_POLL_SECONDS, return_when=FIRST_COMPLETED)
            if cancelled:
                break
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
        pending = wait(set(futures), timeout=STOP_TIMEOUT_SECONDS)[1]
        if pending and unblock is not None and not unblock():
            worker_problems.append("could not stop the API subprocess to unblock workers")
        if pending:
            pending = wait(pending, timeout=WORKER_DRAIN_SECONDS)[1]
        if pending:
            worker_problems.append(f"{len(pending)} measurement workers did not stop")
        # Inspect exceptions only on futures that already finished. A pending
        # future is never awaited here, so the shutdown stays bounded. Python's
        # ThreadPoolExecutor may still join a stuck non-daemon worker at
        # interpreter shutdown; the external hard timeout is the final bound.
        for future in futures:
            if future.cancelled() or not future.done():
                continue
            try:
                future.result(timeout=0)
            except BenchmarkCancelled:
                pass
            except Exception as error:  # noqa: BLE001 - record worker failure explicitly
                worker_problems.append(f"worker: {type(error).__name__}")
        workers_stopped = not pending
        executor.shutdown(wait=False)
    with lock:
        snapshot = list(rows)
    return PhaseResult(snapshot, maximum[0], cancelled, worker_problems, workers_stopped)


def measurement_should_stop(phase: PhaseResult, cancel: threading.Event) -> bool:
    """A cancelled phase or one whose workers did not stop ends the plan."""
    return phase.cancelled or cancel.is_set() or not phase.workers_stopped


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def new_client(origin: str) -> httpx.Client:
    return httpx.Client(
        base_url=origin,
        headers={"Origin": origin},
        timeout=CLIENT_TIMEOUT,
        trust_env=False,
        limits=httpx.Limits(max_connections=16, max_keepalive_connections=16),
    )


def actor_endpoints(active_id: str, replay_id: str, tournament_id: str) -> list[tuple[str, str]]:
    return [
        ("dashboard_online", "/api/personal/online?section=current&limit=25"),
        ("dashboard_local", "/api/personal/local?status=in_progress&limit=5"),
        ("dashboard_single", "/api/personal/single-player?status=in_progress&limit=25"),
        ("profile", "/api/profile"),
        ("personal_statistics", "/api/personal/single-player?limit=1"),
        ("online_statistics", "/api/online/stats"),
        ("leaderboard", "/api/leaderboard?ruleset=brandubh&limit=10"),
        ("lobby", "/api/online/games?section=all&limit=25"),
        ("active_game", f"/api/online/games/{active_id}"),
        ("completed_replay", f"/api/online/games/{replay_id}/replay"),
        ("player_tournaments", "/api/player-tournaments?limit=20"),
        ("player_bracket", f"/api/player-tournaments/{tournament_id}/bracket"),
        ("recent_records", "/api/history/games?category=online&limit=3"),
    ]


# ---------------------------------------------------------------------------
# Fixture seeding
# ---------------------------------------------------------------------------


def seed_accounts(setup, users: int, cancel: threading.Event) -> list[tuple[str, str, str, str]]:
    """Create distinct verified accounts plus one administrator out of band."""
    credentials: list[tuple[str, str, str, str]] = []
    for index in range(users + 1):
        if cancel.is_set():
            raise BenchmarkCancelled("cancelled during fixture creation")
        admin = index == users
        username = "LoadAdmin" if admin else f"LoadPlayer{index:02}"
        password = secrets.token_urlsafe(24)
        account = setup.state.auth.create_account(
            username,
            f"{username.lower()}@example.test",
            password,
            "ADMIN" if admin else "USER",
            bootstrap=admin,
        )
        credentials.append((username, password, account["id"], "ADMIN" if admin else "USER"))
    with setup.state.store.connection() as connection:
        connection.execute("UPDATE accounts SET email_verified_at=1")
    return credentials


def play_repetition(
    client: httpx.Client,
    path: str,
    first,
    second,
    start_version: int,
    *,
    wrapped: bool,
    cancel: threading.Event,
) -> dict:
    version = start_version
    game: dict = {}
    for turn, (source, target) in enumerate(REPETITION_MOVES):
        if cancel.is_set():
            raise BenchmarkCancelled("cancelled during repetition fixture creation")
        mover = first if turn % 2 == 0 else second
        payload = require(
            mover.post(path, json={"version": version, "source": source, "target": target})
        )
        game = payload["game"] if wrapped else payload
        version = game["version"]
    return game


def seed_fixture(
    actors: list[httpx.Client],
    admin: httpx.Client,
    credentials: list[tuple[str, str, str, str]],
    users: int,
    cancel: threading.Event,
) -> FixtureState:
    """Seed authoritative fixtures through real HTTP adapters; no AI search."""
    from hnefatafl.engine import RULESETS

    def mutate(client: httpx.Client, method: str, path: str, payload=None, expected=200):
        if cancel.is_set():
            raise BenchmarkCancelled("cancelled during fixture creation")
        response = client.post(path, json=payload) if method == "post" else client.get(path)
        return require(response, expected)

    account_ids = [row[2] for row in credentials[:-1]]
    local_ids: list[list[str]] = []
    single_ids: list[list[str]] = []
    active_ids: list[str] = []
    replay_ids: list[str] = []
    waiting_ids: list[str] = []
    for index, client in enumerate(actors):
        if cancel.is_set():
            raise BenchmarkCancelled("cancelled during fixture creation")
        local_ids.append(
            [
                mutate(
                    client,
                    "post",
                    "/api/games/local",
                    {
                        "ruleset": "brandubh",
                        "attackers_label": "Local attacker",
                        "defenders_label": "Local defender",
                    },
                    201,
                )["id"]
                for _ in range(LOCAL_GAMES_PER_PLAYER)
            ]
        )
        # Complete one local pass-and-play game with a legal repetition draw.
        play_repetition(
            client,
            f"/api/games/{local_ids[-1][0]}/moves",
            client,
            client,
            0,
            wrapped=False,
            cancel=cancel,
        )
        single_ids.append([])
        for rule in ("brandubh", "tablut"):
            single_ids[-1].append(
                mutate(
                    client,
                    "post",
                    "/api/games",
                    {
                        "ruleset": rule,
                        "human_side": RULESETS[rule].first_player,
                        "players": {
                            "defenders"
                            if RULESETS[rule].first_player == "attackers"
                            else "attackers": {"profile_id": "baseline"}
                        },
                    },
                    201,
                )["id"]
            )
        for kind in ("active", "completed", "waiting"):
            created = mutate(
                client,
                "post",
                "/api/online/games",
                {
                    "request_id": str(uuid4()),
                    "ruleset": "brandubh",
                    "side_selection": "attackers",
                    "time_per_move": 259200,
                },
                201,
            )["game"]
            path = "/api/online/games/" + created["id"]
            opponent = actors[(index + 1) % len(actors)]
            if kind == "waiting":
                waiting_ids.append(created["id"])
                continue
            joined = mutate(opponent, "post", path + "/join", {"version": created["version"]})[
                "game"
            ]
            if kind == "active":
                active_ids.append(joined["id"])
                continue
            completed = play_repetition(
                client,
                path + "/moves",
                client,
                opponent,
                joined["version"],
                wrapped=True,
                cancel=cancel,
            )
            if completed["status"] != "completed" or completed["result"]["winner"] != "draw":
                raise RuntimeError("Expected an authoritative completed repetition fixture")
            replay_ids.append(completed["id"])
    tournament = mutate(
        admin,
        "post",
        "/api/player-tournaments",
        {
            "name": "Read benchmark bracket",
            "ruleset": "brandubh",
            "match_mode": "simultaneous",
            "capacity": users,
            "time_per_move": 259200,
        },
        201,
    )
    started_tournament_id = tournament["id"]
    for client in actors:
        mutate(client, "post", f"/api/player-tournaments/{started_tournament_id}/register")
    current = mutate(admin, "get", f"/api/player-tournaments/{started_tournament_id}")
    mutate(
        admin,
        "post",
        f"/api/player-tournaments/{started_tournament_id}/start",
        {"version": current["version"]},
    )
    upcoming = mutate(
        admin,
        "post",
        "/api/player-tournaments",
        {
            "name": "Upcoming benchmark bracket",
            "ruleset": "tablut",
            "match_mode": "simultaneous",
            "capacity": users,
            "time_per_move": 259200,
        },
        201,
    )
    return FixtureState(
        users=users,
        account_ids=account_ids,
        admin_id=credentials[-1][2],
        local_ids=local_ids,
        single_ids=single_ids,
        active_ids=active_ids,
        replay_ids=replay_ids,
        waiting_ids=waiting_ids,
        started_tournament_id=started_tournament_id,
        upcoming_tournament_id=upcoming["id"],
    )


# ---------------------------------------------------------------------------
# Fixture semantic verification (untimed)
# ---------------------------------------------------------------------------


def bracket_match_count(bracket: dict) -> int:
    total = sum(len(round_["matches"]) for group in ("upper", "lower") for round_ in bracket[group])
    total += int(bool(bracket.get("final")))
    total += int(bool(bracket.get("reset")))
    return total


def bracket_leg_count(bracket: dict) -> int:
    total = 0
    for group in ("upper", "lower"):
        for round_ in bracket[group]:
            for match in round_["matches"]:
                total += len(match["legs"])
    for key in ("final", "reset"):
        match = bracket.get(key)
        if match:
            total += len(match["legs"])
    return total


def power_of_two_at_least(value: int) -> int:
    return 1 << (value - 1).bit_length()


def expected_bracket_match_count(bracket_size: int) -> int:
    """Double-elimination match count derived from the bracket size, not the API.

    Mirrors the production bracket plan: a full upper bracket, the lower-bracket
    drop/consolidation rounds, and one grand final (a reset match is only added
    later if the lower-bracket entrant wins).
    """
    upper = bracket_size.bit_length() - 1
    if upper < 1:
        return 0
    lower = 0
    if upper >= 2:
        lower = bracket_size // 4
        survivors = bracket_size // 4
        for drop in range(2, upper + 1):
            lower += survivors
            if drop < upper:
                lower += survivors // 2
                survivors //= 2
    return (bracket_size - 1) + lower + 1


def expected_bracket_leg_count(users: int, bracket_size: int) -> int:
    """At start only first-round matches without a bye are ready, two legs each."""
    return 2 * users - bracket_size


def require_exact_ids(actual: set[str], expected: set[str], label: str) -> None:
    """Reject missing, extra or empty listings where the fixture is fully known."""
    if not expected:
        raise RuntimeError(f"{label}: expected a non-empty set of seeded identifiers")
    missing = expected - actual
    extra = actual - expected
    if missing or extra:
        raise RuntimeError(f"{label}: missing {sorted(missing)}, unexpected {sorted(extra)}")


def require_no_ids(actual: set[str], label: str) -> None:
    """Reject a listing that should be empty for the bounded fixture."""
    if actual:
        raise RuntimeError(f"{label}: expected no records, found {sorted(actual)}")


def require_page_size(identifiers: list, expected_count: int, label: str) -> None:
    """Reject empty, short, over-long or duplicated pages."""
    if len(identifiers) != expected_count:
        raise RuntimeError(f"{label}: {len(identifiers)} records != expected {expected_count}")
    if len(set(identifiers)) != len(identifiers):
        raise RuntimeError(f"{label}: duplicate identifiers in one page")


def validate_leaderboard_page(page: dict, account_ids: list[str], limit: int) -> None:
    """Check the timed leaderboard page against the rated fixture accounts."""
    entries = page.get("entries", [])
    identifiers = [entry.get("account_id") for entry in entries]
    expected_count = min(len(account_ids), limit)
    require_page_size(identifiers, expected_count, "leaderboard")
    if any(identifier not in account_ids for identifier in identifiers):
        raise RuntimeError("Leaderboard returned a foreign account")
    if page.get("total") != len(account_ids):
        raise RuntimeError("Leaderboard total does not match the rated players")
    if page.get("limit") != limit:
        raise RuntimeError("Leaderboard did not report the requested page size")
    if expected_count == len(account_ids) and set(identifiers) != set(account_ids):
        raise RuntimeError("Leaderboard does not contain every rated account")
    policy = page.get("policy")
    if not isinstance(policy, dict) or not {
        "version",
        "initial_rating",
        "k_factor",
        "provisional_games",
    } <= set(policy):
        raise RuntimeError("Leaderboard policy metadata is missing")
    ranks: list[int] = []
    for entry in entries:
        if not {
            "account_id",
            "rating",
            "rated_games",
            "wins",
            "losses",
            "draws",
            "rank",
        } <= set(entry):
            raise RuntimeError("Leaderboard entry is missing rating metadata")
        rating = entry["rating"]
        if isinstance(rating, bool) or not isinstance(rating, (int, float)):
            raise RuntimeError("Leaderboard entry has an invalid rating")
        counts = (entry["rated_games"], entry["wins"], entry["losses"], entry["draws"])
        if any(isinstance(value, bool) or not isinstance(value, int) for value in counts):
            raise RuntimeError("Leaderboard entry has invalid rating counts")
        rated, wins, losses, draws = counts
        if rated != wins + losses + draws:
            raise RuntimeError("Leaderboard rating counts are inconsistent")
        # Every seeded player has exactly two completed rated draws.
        if rated != 2 or wins != 0 or losses != 0 or draws != 2:
            raise RuntimeError("Leaderboard entry does not match the two rated draws")
        rank = entry["rank"]
        if isinstance(rank, bool) or not isinstance(rank, int) or rank < 1:
            raise RuntimeError("Leaderboard entry has an invalid rank")
        ranks.append(rank)
    if len(set(ranks)) != len(ranks):
        raise RuntimeError("Leaderboard page has duplicate ranks")


def validate_lobby_page(games: list[dict], expected_ordered: list[str], limit: int) -> None:
    """Check the timed lobby page against the production ordering and visibility."""
    identifiers = [game.get("id") for game in games]
    expected_count = min(len(expected_ordered), limit)
    require_page_size(identifiers, expected_count, "lobby")
    if any(identifier not in expected_ordered for identifier in identifiers):
        raise RuntimeError("Lobby returned an unexpected online game")
    for game in games:
        if game.get("status") not in {"waiting", "active"}:
            raise RuntimeError(f"Lobby exposed a game with status {game.get('status')!r}")
    if identifiers != expected_ordered[:expected_count]:
        raise RuntimeError("Lobby page does not match the production ordering")


def validate_single_player_statistics(page: dict, expected_games: int) -> None:
    """Check the timed statistics projection of the unfinished single-player fixture."""
    games = page.get("games", [])
    if len(games) != 1:
        raise RuntimeError("Statistics endpoint did not return exactly one game")
    totals = page.get("totals", {})
    if (
        totals.get("total_games") != expected_games
        or totals.get("completed_games") != 0
        or totals.get("in_progress") != expected_games
    ):
        raise RuntimeError("Single-player totals do not match the unfinished-game fixture")
    statistics = page.get("statistics", {})
    groups = {group.get("ruleset"): group for group in statistics.get("groups", [])}
    if set(groups) != {"brandubh", "tablut"}:
        raise RuntimeError("Single-player statistics groups do not match the fixture")
    for ruleset, group in groups.items():
        if (
            group.get("played") != 1
            or group.get("completed") != 0
            or group.get("wins") != 0
            or group.get("losses") != 0
            or group.get("draws") != 0
        ):
            raise RuntimeError(f"Single-player statistics for {ruleset} are not unfinished")
    if statistics.get("coverage", {}).get("unattributed_games") != 0:
        raise RuntimeError("Single-player statistics report unattributed games")


def tournament_games(bracket: dict) -> dict[str, tuple[str | None, str | None]]:
    """Map every bracket leg's game id to its two participant account ids."""
    games: dict[str, tuple[str | None, str | None]] = {}
    for group in ("upper", "lower"):
        for round_ in bracket[group]:
            for match in round_["matches"]:
                games.update(_match_games(match))
    for key in ("final", "reset"):
        match = bracket.get(key)
        if match:
            games.update(_match_games(match))
    return games


def _match_games(match: dict) -> dict[str, tuple[str | None, str | None]]:
    first = (match.get("participants", {}).get("a") or {}).get("account_id")
    second = (match.get("participants", {}).get("b") or {}).get("account_id")
    return {leg["game_id"]: (first, second) for leg in match.get("legs", [])}


def verify_fixture(
    *,
    actors: list[httpx.Client],
    admin: httpx.Client,
    state: FixtureState,
    db_path: Path,
    cancel: threading.Event,
) -> dict:
    """Prove the fixture is owned, complete and semantically as expected."""
    users = state.users
    account_ids = state.account_ids
    expected_repetition = [(source, target) for source, target in REPETITION_MOVES]
    facts: dict[str, Any] = {"checked_accounts": users}

    def check() -> None:
        if cancel.is_set():
            raise BenchmarkCancelled("cancelled during semantic verification")

    check()

    def get(client: httpx.Client, path: str):
        check()
        return require(client.get(path))

    # Distinct, non-administrator, verified player identities.
    if len(set(account_ids)) != users:
        raise RuntimeError("Fixture player accounts are not distinct")
    if state.admin_id in account_ids:
        raise RuntimeError("Administrator identity collides with a player account")
    facts["distinct_player_accounts"] = len(set(account_ids))
    facts["administrator_accounts"] = 1

    # Establish bracket expectations independently of the returned counts.
    bracket_size = power_of_two_at_least(users)
    expected_matches = expected_bracket_match_count(bracket_size)
    expected_legs = expected_bracket_leg_count(users, bracket_size)
    reference = get(actors[0], f"/api/player-tournaments/{state.started_tournament_id}/bracket")
    if bracket_match_count(reference) != expected_matches:
        raise RuntimeError(
            f"Bracket matches {bracket_match_count(reference)} != expected {expected_matches}"
        )
    if bracket_leg_count(reference) != expected_legs:
        raise RuntimeError(
            f"Bracket legs {bracket_leg_count(reference)} != expected {expected_legs}"
        )
    game_participants = tournament_games(reference)
    if not game_participants:
        raise RuntimeError("Tournament bracket has no first-round games")
    for game_id, (first, second) in game_participants.items():
        if first not in account_ids or second not in account_ids or first == second:
            raise RuntimeError(f"Tournament game {game_id} has unexpected participants")
    # Tournament responses must agree across accounts.
    for client in actors[1:]:
        other = get(client, f"/api/player-tournaments/{state.started_tournament_id}/bracket")
        if tournament_games(other) != game_participants:
            raise RuntimeError("Tournament bracket differs across authenticated accounts")
    facts["bracket_size"] = bracket_size
    facts["tournament_matches"] = expected_matches
    facts["tournament_legs"] = expected_legs

    # The lobby page must follow the production ordering and visibility, so
    # derive it from the same authoritative active/waiting rows the API reads.
    with closing(sqlite3.connect(db_path)) as connection:
        expected_lobby_order = [
            row[0]
            for row in connection.execute(
                "SELECT id FROM online_games WHERE status IN ('waiting','active') "
                "ORDER BY created_at DESC,id"
            )
        ]
    if set(expected_lobby_order) != (
        set(state.active_ids) | set(state.waiting_ids) | set(game_participants)
    ):
        raise RuntimeError("Fixture active/waiting games do not match the lobby query")

    for index, client in enumerate(actors):
        account_id = account_ids[index]
        opponent_id = account_ids[(index + 1) % users]
        me = get(client, "/api/auth/me")["account"]
        if me["id"] != account_id or me["role"] != "USER":
            raise RuntimeError("Authenticated account does not match the seeded player")
        if me["email_verified"] is not True:
            raise RuntimeError("Seeded player account is not verified")

        expected_local = set(state.local_ids[index])
        expected_local_open = {state.local_ids[index][1], state.local_ids[index][2]}
        expected_local_done = {state.local_ids[index][0]}
        local_all = get(client, "/api/personal/local?limit=100")
        require_exact_ids(
            {row["id"] for row in local_all["games"]}, expected_local, f"local actor {index}"
        )
        local_open = get(client, "/api/personal/local?status=in_progress&limit=5")
        require_exact_ids(
            {row["id"] for row in local_open["games"]},
            expected_local_open,
            f"in-progress local actor {index}",
        )
        local_done = get(client, "/api/personal/local?status=completed&limit=5")
        require_exact_ids(
            {row["id"] for row in local_done["games"]},
            expected_local_done,
            f"completed local actor {index}",
        )
        completed_local = next(
            row for row in local_done["games"] if row["id"] == state.local_ids[index][0]
        )
        if completed_local["winner"] != "draw" or completed_local["plies"] != len(REPETITION_MOVES):
            raise RuntimeError("Completed local fixture is not the expected repetition draw")
        local_replay = get(client, f"/api/personal/local/{state.local_ids[index][0]}/replay")
        if local_replay.get("validated") is not True or len(local_replay["states"]) != 9:
            raise RuntimeError("Completed local replay is not a validated nine-state game")
        if [(row["source"], row["target"]) for row in local_replay["moves"]] != (
            expected_repetition
        ):
            raise RuntimeError("Completed local replay moves do not match the seeded line")

        expected_single = set(state.single_ids[index])
        single_all = get(client, "/api/personal/single-player?limit=100")
        require_exact_ids(
            {row["id"] for row in single_all["games"]},
            expected_single,
            f"single-player actor {index}",
        )
        single_open = get(client, "/api/personal/single-player?status=in_progress&limit=25")
        require_exact_ids(
            {row["id"] for row in single_open["games"]},
            expected_single,
            f"in-progress single-player actor {index}",
        )
        single_done = get(client, "/api/personal/single-player?status=completed&limit=25")
        require_no_ids(
            {row["id"] for row in single_done["games"]},
            f"completed single-player actor {index}",
        )

        expected_completed = {state.replay_ids[index], state.replay_ids[(index - 1) % users]}
        expected_active = {state.active_ids[index], state.active_ids[(index - 1) % users]}
        expected_waiting = {state.waiting_ids[index]}
        expected_tournament = {
            game_id
            for game_id, (first, second) in game_participants.items()
            if account_id in (first, second)
        }
        expected_current = expected_active | expected_waiting | expected_tournament
        current = get(client, "/api/personal/online?section=current&limit=100")
        current_ids = {row["id"] for row in current["games"]}
        require_exact_ids(current_ids, expected_current, f"current online actor {index}")
        if any(row["status"] == "completed" for row in current["games"]):
            raise RuntimeError("Completed online fixture leaked into current games")
        waiting = get(client, "/api/personal/online?section=waiting&limit=100")
        require_exact_ids(
            {row["id"] for row in waiting["games"]},
            expected_waiting,
            f"waiting online actor {index}",
        )
        history = get(client, "/api/personal/online?section=history&limit=100")
        require_exact_ids(
            {row["id"] for row in history["games"]},
            expected_completed,
            f"online history actor {index}",
        )
        if any(row["outcome"] != "draw" for row in history["games"]):
            raise RuntimeError("Online history does not record the expected draws")

        active = get(client, f"/api/online/games/{state.active_ids[index]}")
        if active["status"] != "active":
            raise RuntimeError("Active online fixture has the wrong status")
        if (
            active["participants"]["attackers"]["account_id"] != account_id
            or active["participants"]["defenders"]["account_id"] != opponent_id
        ):
            raise RuntimeError("Active online fixture participants are wrong")
        replay = get(client, f"/api/online/games/{state.replay_ids[index]}/replay")
        if replay.get("validated") is not True or len(replay["states"]) != 9:
            raise RuntimeError("Completed replay fixture is not a validated nine-state game")
        if [(row["source"], row["target"]) for row in replay["game"]["moves"]] != (
            expected_repetition
        ):
            raise RuntimeError("Completed replay fixture moves do not match the seeded line")
        if (
            replay["game"]["status"] != "completed"
            or replay["game"]["result"]["winner"] != "draw"
            or replay["game"]["result"]["termination_reason"] != "threefold_repetition"
        ):
            raise RuntimeError("Completed replay fixture result is not authoritative")
        replay_participants = replay["game"]["participants"]
        if {
            replay_participants["attackers"]["account_id"],
            replay_participants["defenders"]["account_id"],
        } != {account_id, opponent_id}:
            raise RuntimeError("Completed replay participants do not match the seeded identities")

        statistics = get(client, "/api/online/stats")
        if (
            statistics["played"] != 2
            or statistics["draws"] != 2
            or statistics["wins"] != 0
            or statistics["losses"] != 0
        ):
            raise RuntimeError("Online statistics do not reflect the completed draws")
        board = get(client, "/api/leaderboard?ruleset=brandubh&limit=100")
        if board["total"] != users:
            raise RuntimeError("Leaderboard total does not match the rated players")
        require_exact_ids(
            {row["account_id"] for row in board["entries"]},
            set(account_ids),
            f"leaderboard actor {index}",
        )
        rating = get(client, "/api/personal/ratings/brandubh/history?limit=100")
        if rating["matching"] != 2 or len(rating["items"]) != 2:
            raise RuntimeError("Rating history does not match the completed games")
        if {row["game_id"] for row in rating["items"]} != expected_completed:
            raise RuntimeError("Rating history game identifiers do not match the completed games")
        recorded = get(client, "/api/history/games?category=online&limit=100")
        require_exact_ids(
            {row["id"] for row in recorded["games"]},
            expected_current | expected_completed,
            f"recorded online actor {index}",
        )

        tournaments = get(client, "/api/player-tournaments?limit=50")
        require_exact_ids(
            {row["id"] for row in tournaments["items"]},
            {state.started_tournament_id, state.upcoming_tournament_id},
            f"tournament discovery actor {index}",
        )
        started = get(client, f"/api/player-tournaments/{state.started_tournament_id}")
        if (
            started["status"] != "running"
            or started["entrant_count"] != users
            or started["bracket_size"] != bracket_size
        ):
            raise RuntimeError("Started tournament status or entrant count is wrong")
        if {row["account_id"] for row in started["entrants"]} != set(account_ids):
            raise RuntimeError("Started tournament entrants do not match the seeded players")
        upcoming = get(client, f"/api/player-tournaments/{state.upcoming_tournament_id}")
        if upcoming["status"] != "registration" or upcoming["entrant_count"] != 0:
            raise RuntimeError("Upcoming tournament fixture is not an empty registration")

        # Timed endpoint variants must be semantically valid in an untimed phase.
        dashboard_online = get(client, "/api/personal/online?section=current&limit=25")
        require_exact_ids(
            {row["id"] for row in dashboard_online["games"]},
            expected_current,
            f"dashboard online actor {index}",
        )
        dashboard_local = get(client, "/api/personal/local?status=in_progress&limit=5")
        require_exact_ids(
            {row["id"] for row in dashboard_local["games"]},
            expected_local_open,
            f"dashboard local actor {index}",
        )
        dashboard_single = get(client, "/api/personal/single-player?status=in_progress&limit=25")
        require_exact_ids(
            {row["id"] for row in dashboard_single["games"]},
            expected_single,
            f"dashboard single-player actor {index}",
        )
        profile = get(client, "/api/profile")
        if profile.get("username") != me["username"] or profile.get("email") != me["email"]:
            raise RuntimeError("Profile does not match the authenticated account")
        statistics_page = get(client, "/api/personal/single-player?limit=1")
        if statistics_page["games"][0]["id"] not in expected_single:
            raise RuntimeError("Statistics endpoint returned a foreign game")
        validate_single_player_statistics(statistics_page, SINGLE_GAMES_PER_PLAYER)
        leaderboard_page = get(client, "/api/leaderboard?ruleset=brandubh&limit=10")
        validate_leaderboard_page(leaderboard_page, account_ids, 10)
        lobby = get(client, "/api/online/games?section=all&limit=25")
        validate_lobby_page(lobby["games"], expected_lobby_order, 25)
        recent = get(client, "/api/history/games?category=online&limit=3")
        if len(recent["games"]) != 3:
            raise RuntimeError("Recent records did not return the requested page size")
        if not {row["id"] for row in recent["games"]} <= (expected_current | expected_completed):
            raise RuntimeError("Recent records returned a foreign game")

    admin_me = get(admin, "/api/auth/me")["account"]
    if admin_me["id"] != state.admin_id or admin_me["role"] != "ADMIN":
        raise RuntimeError("Administrator identity verification failed")
    expected_counts = {
        "accounts": users + 1,
        "games": users * (LOCAL_GAMES_PER_PLAYER + SINGLE_GAMES_PER_PLAYER),
        "online_games": users * ONLINE_GAMES_PER_PLAYER + expected_legs,
        "online_moves": users * len(REPETITION_MOVES),
        "online_results": users,
        "rating_events": users,
        "player_ratings": users,
        "player_tournaments": 2,
        "player_tournament_entrants": users,
        "player_tournament_matches": expected_matches,
        "player_tournament_legs": expected_legs,
    }
    tables = list(expected_counts)
    with closing(sqlite3.connect(db_path)) as connection:
        observed = {
            table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in tables
        }
    facts["expected_seed_counts"] = expected_counts
    facts["observed_seed_counts"] = observed
    if observed != expected_counts:
        raise RuntimeError(
            "Authoritative fixture counts do not match expectations: "
            f"expected {expected_counts}, observed {observed}"
        )
    facts["completed_online_games"] = users
    facts["completed_local_games"] = users
    facts["single_player_outcomes"] = "unfinished-game workload (no AI search in this benchmark)"
    return facts


# ---------------------------------------------------------------------------
# Benchmark driver
# ---------------------------------------------------------------------------


def warmup_fixture(
    actors: list[httpx.Client],
    templates: list[list[tuple[str, str]]],
    state: FixtureState,
    cancel: threading.Event,
) -> int:
    """Untimed warm-up: every endpoint must answer and ownership must hold."""
    requests = 0
    for index, client in enumerate(actors):
        if cancel.is_set():
            raise BenchmarkCancelled("cancelled during warm-up")
        local = require(client.get("/api/personal/local?status=in_progress&limit=5"))
        if not {row["id"] for row in local["games"]} <= set(state.local_ids[index]):
            raise RuntimeError("Warm-up personal ownership mismatch")
        replay = require(client.get(f"/api/online/games/{state.replay_ids[index]}/replay"))
        if len(replay["states"]) != 9:
            raise RuntimeError("Warm-up replay fixture must contain nine validated states")
        requests += 2
        for label, path in templates[index]:
            if cancel.is_set():
                raise BenchmarkCancelled("cancelled during warm-up")
            require(client.get(path))
            requests += 1
    return requests


def server_cpu_seconds(process: subprocess.Popen) -> float:
    fields = Path(f"/proc/{process.pid}/stat").read_text(encoding="utf-8").rsplit(")", 1)[1].split()
    return (int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK")


def server_memory(process: subprocess.Popen) -> dict:
    status = Path(f"/proc/{process.pid}/status").read_text(encoding="utf-8").splitlines()
    return {
        line.split(":", 1)[0]: int(line.split()[1])
        for line in status
        if line.startswith(("VmRSS:", "VmHWM:"))
    }


def start_server(
    source: Path,
    fixture: Fixture,
    port: int,
    out: Path,
    resources: BenchmarkResources,
) -> tuple[subprocess.Popen, httpx.Client]:
    """Spawn and probe the worker, registering resources before they can leak."""
    if resources.cancel.is_set():
        raise BenchmarkCancelled("cancelled during startup")
    log = open(out / "server.log", "w")
    resources.log = log
    process = subprocess.Popen(
        [
            sys.executable,
            str(SCRIPT_PATH),
            "--serve",
            "--source-root",
            str(source),
            "--data-dir",
            str(fixture.path),
            "--port",
            str(port),
        ],
        env=dict(os.environ),
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    resources.process = process
    origin = f"http://{LOOPBACK}:{port}"
    probe = resources.register_client(new_client(origin))
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    while True:
        if resources.cancel.is_set():
            raise BenchmarkCancelled("cancelled during startup")
        try:
            health = probe.get("/api/health")
            if (
                health.status_code == 200
                and health.headers.get("X-Hnefatafl-Benchmark-Fixture") == fixture.identity
            ):
                return process, probe
        except httpx.HTTPError:
            pass
        if process.poll() is not None:
            raise RuntimeError("Disposable benchmark server failed to start")
        if time.monotonic() > deadline:
            raise RuntimeError("Disposable benchmark server did not become ready")
        time.sleep(0.1)


def login_actors(
    probe: httpx.Client,
    credentials: list[tuple[str, str, str, str]],
    resources: BenchmarkResources,
    cancel: threading.Event,
) -> list[httpx.Client]:
    origin = str(probe.base_url).rstrip("/")
    clients: list[httpx.Client] = []
    for username, password, account_id, role in credentials:
        if cancel.is_set():
            raise BenchmarkCancelled("cancelled during authentication")
        client = resources.register_client(new_client(origin))
        session = require(
            client.post("/api/auth/login", json={"identity": username, "password": password})
        )
        account = session["account"]
        if account["id"] != account_id or account["role"] != role:
            raise RuntimeError("Login response does not match the seeded account")
        if role == "USER" and account["email_verified"] is not True:
            raise RuntimeError("Measured account is not verified")
        client.headers["X-CSRF-Token"] = session["csrf_token"]
        clients.append(client)
    return clients


def write_raw_evidence(out: Path, raw: list[dict]) -> list[str]:
    """Persist collected samples first so telemetry or summary failures lose nothing."""
    try:
        # One compact sample per line keeps large raw evidence reviewable.
        rows = ",\n".join(json.dumps(row, separators=(",", ":")) for row in raw)
        (out / "requests.json").write_text("[\n" + rows + "\n]\n", encoding="utf-8")
    except Exception as error:  # noqa: BLE001 - report, do not lose the summary
        return [f"requests evidence: {type(error).__name__}"]
    return []


def write_summary_evidence(out: Path, report: dict) -> list[str]:
    try:
        (out / "summary.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    except Exception as error:  # noqa: BLE001
        return [f"summary evidence: {type(error).__name__}"]
    return []


SECRET_PATTERN = re.compile(
    r"(?i)(password|passwd|token|csrf|cookie|authorization|secret|bearer|api[_-]?key)"
)


def safe_error(error: BaseException) -> str:
    """Type plus a short message, redacted when it may carry a secret."""
    text = f"{type(error).__name__}: {error}"
    if SECRET_PATTERN.search(text):
        return f"{type(error).__name__}: <redacted diagnostic>"
    return text[:500]


def main(args, resources: BenchmarkResources | None = None) -> dict:
    resources = resources or BenchmarkResources()
    source = Path(args.source_root).resolve()
    concurrency = validate_parameters(args.users, args.rounds, args.concurrency, args.port)
    revision = verified_source(source)
    if args.data_dir:
        raise ValueError("--data-dir is only valid with --serve")
    out = Path(args.output).resolve()
    out.mkdir(parents=True, exist_ok=False)
    origin = f"http://{LOOPBACK}:{args.port}"
    caller_threads = {key: os.environ.get(key) for key in sorted(THREAD_KEYS)}

    report: dict[str, Any] = {
        "schema": 3,
        "scope": (
            "warm bounded GETs against fresh disposable SQLite, one API process, "
            "no AI searches or training"
        ),
        "source_root": str(source),
        "source_revision": revision,
        "source_clean": True,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "runner_script": {"path": str(SCRIPT_PATH), "sha256": file_fingerprint(SCRIPT_PATH)},
        "dependency_lock": {
            "path": LOCK_NAME,
            "sha256": file_fingerprint(source / LOCK_NAME),
            "installed_matches_lock": "unverified; a recorded lock fingerprint is not a lock check",
        },
        "dependency_versions": dependency_versions(),
        "environment": None,
        "imported_backend": None,
        "parameters": {"users": args.users, "rounds": args.rounds, "concurrency": concurrency},
        "fixture": {
            "endpoints": list(ENDPOINT_LABELS),
            "local_games_per_player": LOCAL_GAMES_PER_PLAYER,
            "single_games_per_player": SINGLE_GAMES_PER_PLAYER,
            "online_games_per_player": ONLINE_GAMES_PER_PLAYER,
            "repetition_plies": len(REPETITION_MOVES),
        },
    }
    raw: list[dict] = []
    runs: list[dict] = []
    failure: BaseException | None = None
    cancelled = False
    clients_safe = True
    fixture: Fixture | None = None
    try:
        fixture = create_fixture_directory()
        resources.fixture = fixture
        with fixture_environment(fixture, origin=origin):
            sys.path.insert(0, str(source))
            from hnefatafl.online_settings import OnlineSettings
            from hnefatafl.server import create_app

            report["imported_backend"] = assert_import_origin(source)
            report["environment"] = environment_metadata(
                args.port, caller_numeric_threads=caller_threads
            )
            report["fixture"]["identity_sha256"] = hashlib.sha256(
                fixture.identity.encode()
            ).hexdigest()
            setup = create_app(
                fixture.path,
                online_settings=OnlineSettings(enabled=True),
                account_background=False,
                online_background=False,
            )
            credentials = seed_accounts(setup, args.users, resources.cancel)
            process, probe = start_server(source, fixture, args.port, out, resources)
            clients = login_actors(probe, credentials, resources, resources.cancel)
            actors = clients[:-1]
            admin = clients[-1]
            state = seed_fixture(actors, admin, credentials, args.users, resources.cancel)
            db = fixture.path / "hnefatafl.sqlite3"
            report["seed_counts"] = read_seed_counts(db)
            report["fixture_verification"] = verify_fixture(
                actors=actors, admin=admin, state=state, db_path=db, cancel=resources.cancel
            )
            report["sqlite"] = read_sqlite_metadata(db)
            report["query_plans"] = read_query_plans(db, state)
            report["indexes"] = read_indexes(db)
            report["warmup_requests"] = warmup_fixture(
                actors,
                [
                    actor_endpoints(
                        state.active_ids[i], state.replay_ids[i], state.started_tournament_id
                    )
                    for i in range(args.users)
                ],
                state,
                resources.cancel,
            )
            report["percentile_method"] = (
                f"nearest rank; {args.users * args.rounds} samples per endpoint per level"
            )
            report["fixture"]["authenticated_accounts"] = len(actors)
            report["fixture"]["actor_identity_verified"] = True
            templates = [
                actor_endpoints(
                    state.active_ids[i], state.replay_ids[i], state.started_tournament_id
                )
                for i in range(args.users)
            ]
            workers_aborted = False
            for level in concurrency:
                if resources.cancel.is_set():
                    raise BenchmarkCancelled("cancelled before measurement")
                cpu_before = safe_server_cpu_seconds(process)
                started = time.perf_counter()
                phase = measure_level(
                    level,
                    list(range(args.users)),
                    templates,
                    args.rounds,
                    resources,
                    actors,
                    unblock=resources.terminate_process,
                )
                elapsed = time.perf_counter() - started
                # Preserve collected samples immediately, before metrics or any
                # optional process telemetry, so a telemetry failure cannot
                # discard them. Concurrency attribution stays on every sample.
                raw.extend({"concurrency": level, **row} for row in phase.rows)
                problems = validate_phase_counts(
                    phase.rows, args.users, args.rounds, ENDPOINT_LABELS
                )
                run_metrics = metrics(phase.rows)
                by_endpoint = defaultdict(list)
                for row in phase.rows:
                    by_endpoint[row["endpoint"]].append(row)
                concurrency_achieved = phase.maximum_inflight >= level
                run = {
                    "concurrency": level,
                    "configured_concurrency": level,
                    "maximum_observed_inflight_distinct_accounts": phase.maximum_inflight,
                    "distinct_authenticated_accounts": len(
                        {row["actor_index"] for row in phase.rows}
                    ),
                    "authenticated_accounts": len(actors),
                    "concurrency_achieved": concurrency_achieved,
                    "expected_requests": expected_request_count(
                        args.users, args.rounds, len(ENDPOINT_LABELS)
                    ),
                    "count_problems": problems,
                    "worker_problems": phase.worker_problems,
                    "workers_stopped": phase.workers_stopped,
                    "cancelled": phase.cancelled,
                    "complete": (
                        not phase.cancelled
                        and not problems
                        and not phase.worker_problems
                        and phase.workers_stopped
                        and run_metrics["failures"] == 0
                        and concurrency_achieved
                    ),
                    "elapsed_seconds": elapsed,
                    "observed_requests_per_second": len(phase.rows) / elapsed if elapsed else None,
                    **run_metrics,
                    "endpoints": {key: metrics(value) for key, value in by_endpoint.items()},
                }
                runs.append(run)
                cpu_after = safe_server_cpu_seconds(process)
                cpu_seconds = (
                    None if cpu_before is None or cpu_after is None else cpu_after - cpu_before
                )
                run["api_process_cpu_seconds"] = cpu_seconds
                run["api_process_core_seconds_per_wall_second"] = (
                    cpu_seconds / elapsed if cpu_seconds is not None and elapsed else None
                )
                run["api_process_memory_kib_after_phase"] = safe_server_memory(process)
                run["api_process_telemetry_available"] = (
                    cpu_seconds is not None
                    and run["api_process_memory_kib_after_phase"] is not None
                )
                print(json.dumps({k: v for k, v in run.items() if k != "endpoints"}), flush=True)
                if measurement_should_stop(phase, resources.cancel):
                    cancelled = cancelled or phase.cancelled or resources.cancel.is_set()
                    workers_aborted = workers_aborted or not phase.workers_stopped
                    break
            if workers_aborted:
                raise RuntimeError(
                    "Measurement workers did not stop; skipping remaining phases and verification"
                )
            if resources.cancel.is_set():
                raise BenchmarkCancelled("cancelled during measurement")
            # Repeat essential semantics after measurement, before success.
            report["post_measurement_verification"] = verify_fixture(
                actors=actors, admin=admin, state=state, db_path=db, cancel=resources.cancel
            )
            if verified_source(source) != revision:
                raise RuntimeError("Source changed during measurement; evidence invalid")
    except BenchmarkCancelled:
        cancelled = True
        report["cancelled"] = True
        report["cancel_signal"] = resources.signal_name
    except Exception as error:  # noqa: BLE001 - preserve evidence then re-raise
        report["error"] = safe_error(error)
        failure = error
    finally:
        clients_safe = all(run.get("workers_stopped", True) for run in runs)
        cleanup = resources.close(close_clients=clients_safe)
        report["cleanup"] = {
            "problems": list(cleanup.problems),
            "process_stopped": cleanup.process_stopped,
            "fixture_removed": cleanup.fixture_removed,
            "retained_fixture": cleanup.retained_fixture,
            "clients_closed": clients_safe,
        }
        report["cleanup_problems"] = list(cleanup.problems)
        report["runs"] = runs
        measured_levels = [run["concurrency"] for run in runs]
        measurement_complete = (
            failure is None
            and not cancelled
            and not resources.cancel.is_set()
            and bool(runs)
            and measured_levels == concurrency
            and all(run["complete"] for run in runs)
        )
        report["measurement_complete"] = measurement_complete
        report["complete"] = measurement_complete and cleanup.ok
        report["limitations"] = [
            "small bounded fixture, warm local keep-alive GETs only; not production capacity",
            "same-host threaded HTTP driver and one API process, no WAN/TLS/proxy/device load",
            "client-side overlap is not proof of simultaneous backend handler execution",
            "background account/online sweep disabled, no mixed writes or AI/training contention",
            "single-player statistics cover an unfinished-game workload; no AI search is run",
            "short completed repetition draws, no large-history or rare-name scans",
            "no league queue exists in baseline so it is not benchmarked",
            "cleanup after SIGKILL or abnormal host termination cannot be guaranteed",
        ]
        # Raw samples are written before the summary so a summary failure keeps them.
        evidence_problems = write_raw_evidence(out, raw)
        report["cleanup_problems"].extend(evidence_problems)
        report["cleanup"]["problems"].extend(evidence_problems)
        report["complete"] = measurement_complete and cleanup.ok and not evidence_problems
        summary_problems = write_summary_evidence(out, report)
        if summary_problems:
            report["cleanup_problems"].extend(summary_problems)
            report["complete"] = False
            write_summary_evidence(out, report)
    if failure is not None:
        raise failure
    if not report["complete"]:
        raise RuntimeError("Benchmark did not complete a valid baseline; see summary.json")
    return report


def read_seed_counts(db: Path) -> dict[str, int]:
    tables = [
        "accounts",
        "games",
        "online_games",
        "online_moves",
        "online_results",
        "rating_events",
        "player_ratings",
        "player_tournaments",
        "player_tournament_entrants",
        "player_tournament_matches",
        "player_tournament_legs",
    ]
    with closing(sqlite3.connect(db)) as connection:
        return {
            table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in tables
        }


def read_sqlite_metadata(db: Path) -> dict:
    with closing(sqlite3.connect(db)) as connection:
        wal = Path(str(db) + "-wal")
        return {
            "version": sqlite3.sqlite_version,
            "journal_mode": connection.execute("PRAGMA journal_mode").fetchone()[0],
            "database_bytes": db.stat().st_size,
            "wal_bytes": wal.stat().st_size if wal.exists() else 0,
        }


def read_query_plans(db: Path, state: FixtureState) -> dict:
    plans = {
        "owned_single_category": (
            "SELECT id FROM games WHERE category=? AND owner_account_id=? ORDER BY created_at DESC LIMIT 25",
            ("account-single-player", state.account_ids[0]),
        ),
        "owned_local_category": (
            "SELECT id FROM games WHERE category=? AND owner_account_id=? ORDER BY created_at DESC LIMIT 5",
            ("local", state.account_ids[0]),
        ),
        "online_status_updated": (
            "SELECT id FROM online_games WHERE status=? ORDER BY updated_at DESC,id LIMIT 25",
            ("active",),
        ),
        "online_moves_replay": (
            "SELECT ply,source,target,accepted_at FROM online_moves WHERE game_id=? ORDER BY ply",
            (state.replay_ids[0],),
        ),
        "tournament_matches": (
            "SELECT id FROM player_tournament_matches WHERE tournament_id=? ORDER BY bracket,round,slot LIMIT 50",
            (state.started_tournament_id,),
        ),
    }
    result: dict[str, Any] = {
        "note": "Separate representative read fragments on the small fixture, not every HTTP query."
    }
    with closing(sqlite3.connect(db)) as connection:
        for label, (query, values) in plans.items():
            try:
                result[label] = {
                    "query": query,
                    "plan": [
                        row[3] for row in connection.execute("EXPLAIN QUERY PLAN " + query, values)
                    ],
                }
            except sqlite3.Error as error:
                result[label] = {"query": query, "error": str(error)}
    return result


def read_indexes(db: Path) -> dict[str, list[str]]:
    tables = [
        "games",
        "online_games",
        "online_moves",
        "player_tournaments",
        "player_tournament_matches",
        "player_ratings",
    ]
    with closing(sqlite3.connect(db)) as connection:
        return {
            table: [row[1] for row in connection.execute("PRAGMA index_list(" + table + ")")]
            for table in tables
        }


def safe_server_memory(process: subprocess.Popen) -> dict | None:
    # Telemetry is optional: any read failure must not discard collected samples.
    try:
        return server_memory(process)
    except Exception:  # noqa: BLE001
        return None


def safe_server_cpu_seconds(process: subprocess.Popen) -> float | None:
    try:
        return server_cpu_seconds(process)
    except Exception:  # noqa: BLE001
        return None


def handle_signal(resources: BenchmarkResources, signum: int, _frame) -> None:
    try:
        resources.signal_name = signal.Signals(signum).name
    except ValueError:
        resources.signal_name = str(signum)
    resources.cancel.set()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", default=str(SOURCE_ROOT))
    parser.add_argument("--port", type=int, default=8501)
    parser.add_argument("--users", type=int, default=8)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--concurrency", default="1,4,8")
    parser.add_argument(
        "--output",
        default=str(Path(tempfile.gettempdir()) / ("arena-backend-reads-" + uuid4().hex)),
    )
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--data-dir")
    parsed = parser.parse_args()
    if parsed.serve:
        serve(
            Path(parsed.source_root),
            Path(parsed.data_dir) if parsed.data_dir else None,
            parsed.port,
        )
    else:
        active = BenchmarkResources()
        signal.signal(signal.SIGINT, lambda signum, frame: handle_signal(active, signum, frame))
        signal.signal(signal.SIGTERM, lambda signum, frame: handle_signal(active, signum, frame))
        try:
            completed = main(parsed, active)
        except Exception as error:  # noqa: BLE001 - CLI boundary
            print(f"Benchmark failed: {safe_error(error)}", file=sys.stderr)
            sys.exit(1)
        if not completed.get("complete"):
            print("Benchmark did not complete a valid baseline.", file=sys.stderr)
            sys.exit(1)
