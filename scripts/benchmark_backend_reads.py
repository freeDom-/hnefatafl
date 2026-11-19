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

STOP_TIMEOUT_SECONDS = 20.0
BARRIER_TIMEOUT_SECONDS = 30.0
STARTUP_TIMEOUT_SECONDS = 45.0
TERMINATE_GRACE_SECONDS = 5.0
TERMINATE_FORCE_SECONDS = 5.0
CLIENT_TIMEOUT = httpx.Timeout(10.0, connect=5.0, pool=5.0)

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
    ) -> None:
        process = self.process
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=grace)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=force)
        else:
            process.wait()

    def close(self) -> list[str]:
        """Release every owned resource; never lets one failure skip another."""
        problems: list[str] = []
        for client in self.clients:
            try:
                client.close()
            except Exception as error:  # noqa: BLE001 - cleanup must continue
                problems.append(f"client close: {type(error).__name__}")
        self.clients.clear()
        try:
            self.terminate_process()
        except Exception as error:  # noqa: BLE001 - report, do not raise
            problems.append(f"process cleanup: {type(error).__name__}")
        if self.log is not None:
            try:
                self.log.close()
            except Exception as error:  # noqa: BLE001
                problems.append(f"log close: {type(error).__name__}")
        if self.fixture is not None:
            try:
                if not cleanup_fixture_directory(self.fixture):
                    problems.append("fixture cleanup refused")
            except Exception as error:  # noqa: BLE001
                problems.append(f"fixture cleanup: {type(error).__name__}")
        return problems


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


def assert_import_origin(source: Path) -> None:
    """Fail unless the imported backend modules come from the verified checkout."""
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


def validate_fixture_directory(data: str | Path | None, *, require_config: bool = True) -> Fixture:
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
        if configured is not None and Path(configured).resolve() != resolved:
            raise ValueError("Fixture directory does not match HNEFATAFL_DATA_DIR")
    return Fixture(resolved, identity)


def create_fixture_directory(base: str | Path | None = None) -> Fixture:
    directory = Path(tempfile.mkdtemp(prefix=FIXTURE_PREFIX, dir=base))
    os.chmod(directory, 0o700)
    marker = directory / MARKER_NAME
    marker.write_text(uuid4().hex, encoding="utf-8")
    os.chmod(marker, 0o600)
    return validate_fixture_directory(directory, require_config=False)


def cleanup_fixture_directory(fixture: Fixture) -> bool:
    """Remove only the fixture this process created; never an operator path."""
    try:
        current = validate_fixture_directory(fixture.path, require_config=False)
    except ValueError:
        return False
    if current.identity != fixture.identity:
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


def cpu_quota_cores() -> float | None:
    try:
        parts = Path("/sys/fs/cgroup/cpu.max").read_text(encoding="utf-8").split()
        if parts[0] != "max":
            return int(parts[0]) / int(parts[1])
    except (OSError, ValueError, IndexError):
        pass
    try:
        quota = int(Path("/sys/fs/cgroup/cpu/cpu.cfs_quota_us").read_text(encoding="utf-8"))
        period = int(Path("/sys/fs/cgroup/cpu/cpu.cfs_period_us").read_text(encoding="utf-8"))
        if quota > 0 and period > 0:
            return quota / period
    except (OSError, ValueError):
        pass
    return None


def memory_limit_bytes() -> tuple[int | None, str | None]:
    try:
        value = Path("/sys/fs/cgroup/memory.max").read_text(encoding="utf-8").strip()
        if value != "max":
            return int(value), "cgroup-v2 memory.max"
    except (OSError, ValueError):
        pass
    try:
        value = int(Path("/sys/fs/cgroup/memory/memory.limit_in_bytes").read_text(encoding="utf-8"))
        if value < (1 << 60):
            return value, "cgroup-v1 memory.limit_in_bytes"
    except (OSError, ValueError):
        pass
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) * 1024, "proc meminfo MemTotal"
    except (OSError, ValueError, IndexError):
        pass
    return None, None


def environment_metadata(port: int) -> dict:
    memory_bytes, memory_source = memory_limit_bytes()
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "cpu_model": cpu_model(),
        "logical_cpus": os.cpu_count(),
        "affinity_cpus": cpu_affinity_count(),
        "cpu_quota_cores": cpu_quota_cores(),
        "memory_limit_bytes": memory_bytes,
        "memory_limit_source": memory_source,
        "sqlite": sqlite3.sqlite_version,
        "numeric_threads": {key: os.environ.get(key) for key in sorted(THREAD_KEYS)},
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
) -> tuple[list[dict], int, bool]:
    """Measure one concurrency level with synchronized waves and bounded stop."""
    rows: list[dict] = []
    lock = threading.Lock()
    active: set[int] = set()
    maximum = [0]

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
    futures = []
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
            while True:
                if resources.cancel.is_set():
                    cancelled = True
                    barrier.abort()
                    break
                _, pending = wait(wave_futures, timeout=0.2, return_when=FIRST_COMPLETED)
                if not pending:
                    break
            if cancelled:
                break
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
        wait(futures, timeout=STOP_TIMEOUT_SECONDS)
        executor.shutdown(wait=False)
    return rows, maximum[0], cancelled


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
    client: httpx.Client, path: str, first, second, start_version: int, *, wrapped: bool
) -> dict:
    version = start_version
    game: dict = {}
    for turn, (source, target) in enumerate(REPETITION_MOVES):
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
                require(
                    client.post(
                        "/api/games/local",
                        json={
                            "ruleset": "brandubh",
                            "attackers_label": "Local attacker",
                            "defenders_label": "Local defender",
                        },
                    ),
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
        )
        single_ids.append([])
        for rule in ("brandubh", "tablut"):
            single_ids[-1].append(
                require(
                    client.post(
                        "/api/games",
                        json={
                            "ruleset": rule,
                            "human_side": RULESETS[rule].first_player,
                            "players": {
                                "defenders"
                                if RULESETS[rule].first_player == "attackers"
                                else "attackers": {"profile_id": "baseline"}
                            },
                        },
                    ),
                    201,
                )["id"]
            )
        for kind in ("active", "completed", "waiting"):
            created = require(
                client.post(
                    "/api/online/games",
                    json={
                        "request_id": str(uuid4()),
                        "ruleset": "brandubh",
                        "side_selection": "attackers",
                        "time_per_move": 259200,
                    },
                ),
                201,
            )["game"]
            path = "/api/online/games/" + created["id"]
            opponent = actors[(index + 1) % len(actors)]
            if kind == "waiting":
                waiting_ids.append(created["id"])
                continue
            joined = require(opponent.post(path + "/join", json={"version": created["version"]}))[
                "game"
            ]
            if kind == "active":
                active_ids.append(joined["id"])
                continue
            completed = play_repetition(
                client, path + "/moves", client, opponent, joined["version"], wrapped=True
            )
            if completed["status"] != "completed" or completed["result"]["winner"] != "draw":
                raise RuntimeError("Expected an authoritative completed repetition fixture")
            replay_ids.append(completed["id"])
    tournament = require(
        admin.post(
            "/api/player-tournaments",
            json={
                "name": "Read benchmark bracket",
                "ruleset": "brandubh",
                "match_mode": "simultaneous",
                "capacity": users,
                "time_per_move": 259200,
            },
        ),
        201,
    )
    started_tournament_id = tournament["id"]
    for client in actors:
        if cancel.is_set():
            raise BenchmarkCancelled("cancelled during fixture creation")
        require(client.post(f"/api/player-tournaments/{started_tournament_id}/register"))
    current = require(admin.get(f"/api/player-tournaments/{started_tournament_id}"))
    require(
        admin.post(
            f"/api/player-tournaments/{started_tournament_id}/start",
            json={"version": current["version"]},
        )
    )
    upcoming = require(
        admin.post(
            "/api/player-tournaments",
            json={
                "name": "Upcoming benchmark bracket",
                "ruleset": "tablut",
                "match_mode": "simultaneous",
                "capacity": users,
                "time_per_move": 259200,
            },
        ),
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


def verify_fixture(
    *,
    actors: list[httpx.Client],
    admin: httpx.Client,
    state: FixtureState,
    db_path: Path,
) -> dict:
    """Prove the fixture is owned, complete and semantically as expected."""
    users = state.users
    expected_repetition = [(source, target) for source, target in REPETITION_MOVES]
    facts: dict[str, Any] = {"checked_accounts": users}
    # Distinct, non-administrator, verified player identities.
    if len(set(state.account_ids)) != users:
        raise RuntimeError("Fixture player accounts are not distinct")
    if state.admin_id in state.account_ids:
        raise RuntimeError("Administrator identity collides with a player account")
    facts["distinct_player_accounts"] = len(set(state.account_ids))
    facts["administrator_accounts"] = 1
    for index, client in enumerate(actors):
        me = require(client.get("/api/auth/me"))["account"]
        if me["id"] != state.account_ids[index] or me["role"] != "USER":
            raise RuntimeError("Authenticated account does not match the seeded player")
        if me["email_verified"] is not True:
            raise RuntimeError("Seeded player account is not verified")
        local = require(client.get("/api/personal/local?limit=100"))
        owned_local = {row["id"] for row in local["games"]}
        if not set(state.local_ids[index]) <= owned_local:
            raise RuntimeError("Personal local records do not match the seeded account")
        completed_local = [row for row in local["games"] if row["id"] == state.local_ids[index][0]]
        if not completed_local or completed_local[0]["status"] != "completed":
            raise RuntimeError("Completed local fixture is missing or unfinished")
        single = require(client.get("/api/personal/single-player?limit=100"))
        owned_single = {row["id"] for row in single["games"]}
        if not set(state.single_ids[index]) <= owned_single:
            raise RuntimeError("Personal single-player records do not match the seeded account")
        if any(
            row["status"] != "in_progress"
            for row in single["games"]
            if row["id"] in state.single_ids[index]
        ):
            raise RuntimeError("Single-player fixture unexpectedly reports a completed result")
        current = require(client.get("/api/personal/online?section=current&limit=100"))
        current_ids = {row["id"] for row in current["games"]}
        if (
            state.active_ids[index] not in current_ids
            or state.waiting_ids[index] not in current_ids
        ):
            raise RuntimeError("Current online fixture is missing from personal history")
        if state.replay_ids[index] in current_ids:
            raise RuntimeError("Completed online fixture leaked into current games")
        active = require(client.get(f"/api/online/games/{state.active_ids[index]}"))
        if active["status"] != "active":
            raise RuntimeError("Active online fixture has the wrong status")
        opponent_id = state.account_ids[(index + 1) % users]
        if (
            active["participants"]["attackers"]["account_id"] != state.account_ids[index]
            or active["participants"]["defenders"]["account_id"] != opponent_id
        ):
            raise RuntimeError("Active online fixture participants are wrong")
        replay = require(client.get(f"/api/online/games/{state.replay_ids[index]}/replay"))
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
        statistics = require(client.get("/api/online/stats"))
        if statistics["played"] != 2 or statistics["draws"] != 2:
            raise RuntimeError("Online statistics do not reflect the completed draws")
        board = require(client.get("/api/leaderboard?ruleset=brandubh&limit=100"))
        if board["total"] != users or not {row["account_id"] for row in board["entries"]} >= set(
            state.account_ids
        ):
            raise RuntimeError("Leaderboard is missing seeded rated accounts")
        rating = require(client.get("/api/personal/ratings/brandubh/history?limit=100"))
        if rating["matching"] != 2 or len(rating["items"]) != 2:
            raise RuntimeError("Rating history does not match the completed games")
        history = require(client.get("/api/history/games?category=online&limit=100"))
        history_ids = {row["id"] for row in history["games"]}
        if state.replay_ids[index] not in history_ids:
            raise RuntimeError("Recorded history is missing a completed online fixture")
        tournaments = require(client.get("/api/player-tournaments?limit=50"))
        listed = {row["id"] for row in tournaments["items"]}
        if {state.started_tournament_id, state.upcoming_tournament_id} - listed:
            raise RuntimeError("Tournament discovery is missing a seeded tournament")
        started = require(client.get(f"/api/player-tournaments/{state.started_tournament_id}"))
        if started["status"] != "running" or started["entrant_count"] != users:
            raise RuntimeError("Started tournament status or entrant count is wrong")
        if {row["account_id"] for row in started["entrants"]} != set(state.account_ids):
            raise RuntimeError("Started tournament entrants do not match the seeded players")
        upcoming = require(client.get(f"/api/player-tournaments/{state.upcoming_tournament_id}"))
        if upcoming["status"] != "registration" or upcoming["entrant_count"] != 0:
            raise RuntimeError("Upcoming tournament fixture is not an empty registration")
        bracket = require(
            client.get(f"/api/player-tournaments/{state.started_tournament_id}/bracket")
        )
        matches = bracket_match_count(bracket)
        legs = bracket_leg_count(bracket)
        if matches <= 0 or legs <= 0:
            raise RuntimeError("Tournament bracket is missing entrants, matches or legs")
        facts.setdefault("tournament_matches", matches)
        facts.setdefault("tournament_legs", legs)
    admin_me = require(admin.get("/api/auth/me"))["account"]
    if admin_me["id"] != state.admin_id or admin_me["role"] != "ADMIN":
        raise RuntimeError("Administrator identity verification failed")
    expected_counts = {
        "accounts": users + 1,
        "games": users * (LOCAL_GAMES_PER_PLAYER + SINGLE_GAMES_PER_PLAYER),
        "online_games": users * ONLINE_GAMES_PER_PLAYER + facts["tournament_legs"],
        "online_moves": users * len(REPETITION_MOVES),
        "online_results": users,
        "rating_events": users,
        "player_ratings": users,
        "player_tournaments": 2,
        "player_tournament_entrants": users,
        "player_tournament_matches": facts["tournament_matches"],
        "player_tournament_legs": facts["tournament_legs"],
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


def write_evidence(out: Path, report: dict, raw: list[dict]) -> None:
    (out / "summary.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    # One compact sample per line keeps large raw evidence reviewable.
    rows = ",\n".join(json.dumps(row, separators=(",", ":")) for row in raw)
    (out / "requests.json").write_text("[\n" + rows + "\n]\n", encoding="utf-8")


def safe_error(error: BaseException) -> str:
    text = f"{type(error).__name__}: {error}"
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

    report: dict[str, Any] = {
        "schema": 2,
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
        "environment": environment_metadata(args.port),
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
    fixture: Fixture | None = None
    setup = None
    try:
        fixture = create_fixture_directory()
        resources.fixture = fixture
        with fixture_environment(fixture, origin=origin):
            sys.path.insert(0, str(source))
            from hnefatafl.online_settings import OnlineSettings
            from hnefatafl.server import create_app

            assert_import_origin(source)
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
                actors=actors, admin=admin, state=state, db_path=db
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
            for level in concurrency:
                if resources.cancel.is_set():
                    raise BenchmarkCancelled("cancelled before measurement")
                cpu_before = server_cpu_seconds(process)
                started = time.perf_counter()
                rows, max_inflight, cancelled = measure_level(
                    level, list(range(args.users)), templates, args.rounds, resources, actors
                )
                elapsed = time.perf_counter() - started
                cpu_seconds = server_cpu_seconds(process) - cpu_before
                problems = validate_phase_counts(rows, args.users, args.rounds, ENDPOINT_LABELS)
                run_metrics = metrics(rows)
                by_endpoint = defaultdict(list)
                for row in rows:
                    by_endpoint[row["endpoint"]].append(row)
                concurrency_achieved = max_inflight >= level
                run = {
                    "concurrency": level,
                    "configured_concurrency": level,
                    "maximum_observed_inflight_distinct_accounts": max_inflight,
                    "distinct_authenticated_accounts": len({row["actor_index"] for row in rows}),
                    "authenticated_accounts": len(actors),
                    "concurrency_achieved": concurrency_achieved,
                    "expected_requests": expected_request_count(
                        args.users, args.rounds, len(ENDPOINT_LABELS)
                    ),
                    "count_problems": problems,
                    "cancelled": cancelled,
                    "complete": (
                        not cancelled
                        and not problems
                        and run_metrics["failures"] == 0
                        and concurrency_achieved
                    ),
                    "elapsed_seconds": elapsed,
                    "observed_requests_per_second": len(rows) / elapsed if elapsed else None,
                    "api_process_cpu_seconds": cpu_seconds,
                    "api_process_core_seconds_per_wall_second": cpu_seconds / elapsed
                    if elapsed
                    else None,
                    "api_process_memory_kib_after_phase": safe_server_memory(process),
                    **run_metrics,
                    "endpoints": {key: metrics(value) for key, value in by_endpoint.items()},
                }
                runs.append(run)
                raw.extend({"concurrency": level, **row} for row in rows)
                print(json.dumps({k: v for k, v in run.items() if k != "endpoints"}), flush=True)
                if cancelled or resources.cancel.is_set():
                    break
            if verified_source(source) != revision:
                raise RuntimeError("Source changed during measurement; evidence invalid")
    except BenchmarkCancelled:
        report["cancelled"] = True
        report["cancel_signal"] = resources.signal_name
    except Exception as error:  # noqa: BLE001 - preserve evidence then re-raise
        report["error"] = safe_error(error)
        failure = error
    finally:
        report["cleanup_problems"] = resources.close()
        report["runs"] = runs
        report["complete"] = (
            failure is None
            and not resources.cancel.is_set()
            and bool(runs)
            and all(run["complete"] for run in runs)
        )
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
        write_evidence(out, report, raw)
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
    try:
        return server_memory(process)
    except (OSError, ValueError):
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
