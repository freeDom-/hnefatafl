"""Safety, lifecycle and metrics guards for the optional disposable read benchmark."""

import json
import os
import signal
import socket
import statistics
import subprocess
import sys
import threading
import time
from collections import Counter, defaultdict
from pathlib import Path

import httpx
import pytest

from scripts import benchmark_backend_reads as bench
from scripts.benchmark_backend_reads import (
    BenchmarkResources,
    cleanup_fixture_directory,
    create_fixture_directory,
    deterministic_environment,
    environment_metadata,
    expected_request_count,
    fixture_environment,
    git_revision,
    handle_signal,
    mark_fixture_health,
    metrics,
    percentile,
    require,
    run_actor_requests,
    validate_fixture_directory,
    validate_parameters,
    validate_phase_counts,
    verified_source,
)


# ---------------------------------------------------------------------------
# Environment isolation
# ---------------------------------------------------------------------------


def test_fixture_environment_is_isolated_and_restored_on_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("HNEFATAFL_DATA_DIR", "/operator-storage-must-not-open")
    monkeypatch.setenv("HNEFATAFL_PUBLIC_URL", "https://operator.example")
    monkeypatch.setenv("HNEFATAFL_REGISTRATION_ENABLED", "true")
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.example:3128")
    monkeypatch.setenv("OMP_NUM_THREADS", "99")
    fixture = create_fixture_directory(tmp_path)
    origin = "http://127.0.0.1:8501"
    with pytest.raises(RuntimeError):
        with fixture_environment(fixture, origin=origin):
            assert os.environ["HNEFATAFL_DATA_DIR"] == str(fixture.path)
            assert os.environ["HNEFATAFL_PUBLIC_URL"] == origin
            assert os.environ["HNEFATAFL_ENV"] == "development"
            assert os.environ["HNEFATAFL_REGISTRATION_ENABLED"] == "false"
            assert "HTTP_PROXY" not in os.environ
            assert os.environ["OMP_NUM_THREADS"] == "2"
            raise RuntimeError("cancelled import")
    assert os.environ["HNEFATAFL_DATA_DIR"] == "/operator-storage-must-not-open"
    assert os.environ["HNEFATAFL_PUBLIC_URL"] == "https://operator.example"
    assert os.environ["HNEFATAFL_REGISTRATION_ENABLED"] == "true"
    assert os.environ["HTTP_PROXY"] == "http://proxy.example:3128"
    assert os.environ["OMP_NUM_THREADS"] == "99"


def test_deterministic_environment_covers_required_isolation(tmp_path):
    settings = deterministic_environment(tmp_path, "http://127.0.0.1:8501")
    assert settings["HNEFATAFL_DATA_DIR"] == str(tmp_path)
    assert settings["HNEFATAFL_ENV"] == "development"
    assert settings["HNEFATAFL_PUBLIC_URL"] == "http://127.0.0.1:8501"
    assert settings["HNEFATAFL_ALLOWED_HOSTS"] == "127.0.0.1,localhost"
    assert settings["HNEFATAFL_REGISTRATION_ENABLED"] == "false"
    assert settings["HNEFATAFL_ONLINE_ENABLED"] == "true"
    assert settings["HNEFATAFL_SESSION_SECONDS"] == "3600"
    assert settings["HNEFATAFL_ACCOUNT_GLOBAL_HASHES"] == "100000"
    assert settings["OMP_NUM_THREADS"] == "2"
    assert settings["MKL_NUM_THREADS"] == "2"
    assert settings["OPENBLAS_NUM_THREADS"] == "2"


def test_environment_metadata_distinguishes_unavailable_from_zero(monkeypatch):
    monkeypatch.setattr(bench.os, "cpu_count", lambda: None)
    metadata = environment_metadata(8501, caller_numeric_threads={"OMP_NUM_THREADS": "9"})
    assert metadata["logical_cpus"] is None
    assert metadata["port"] == 8501
    assert metadata["caller_numeric_threads"] == {"OMP_NUM_THREADS": "9"}
    assert metadata["benchmark_numeric_threads"]["OMP_NUM_THREADS"] in (None, "2")


# ---------------------------------------------------------------------------
# Fixture directory ownership
# ---------------------------------------------------------------------------


def test_fixture_directory_validation_rejects_unsafe_shapes(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="explicit"):
        validate_fixture_directory(None)
    with pytest.raises(ValueError, match="absolute"):
        validate_fixture_directory("relative/path")

    valid = create_fixture_directory(tmp_path)
    assert validate_fixture_directory(valid.path, require_config=False).identity == valid.identity

    link = tmp_path / (bench.FIXTURE_PREFIX + "link")
    link.symlink_to(valid.path)
    with pytest.raises(ValueError, match="symlink"):
        validate_fixture_directory(link)

    wrong = tmp_path / "not-a-benchmark-directory"
    wrong.mkdir()
    (wrong / bench.MARKER_NAME).write_text("a" * 32)
    with pytest.raises(ValueError, match="non-benchmark"):
        validate_fixture_directory(wrong)

    missing = tmp_path / (bench.FIXTURE_PREFIX + "missing")
    missing.mkdir()
    missing.chmod(0o700)
    with pytest.raises(ValueError, match="marker"):
        validate_fixture_directory(missing)

    invalid = tmp_path / (bench.FIXTURE_PREFIX + "invalid")
    invalid.mkdir()
    invalid.chmod(0o700)
    (invalid / bench.MARKER_NAME).write_text("not-a-hex-identity")
    with pytest.raises(ValueError, match="identity"):
        validate_fixture_directory(invalid)

    monkeypatch.setenv("HNEFATAFL_DATA_DIR", str(tmp_path / "somewhere-else"))
    with pytest.raises(ValueError, match="does not match"):
        validate_fixture_directory(valid.path)
    monkeypatch.delenv("HNEFATAFL_DATA_DIR")

    monkeypatch.setattr(bench.os, "getuid", lambda: -1)
    with pytest.raises(ValueError, match="owned"):
        validate_fixture_directory(valid.path, require_config=False)


def test_cleanup_fixture_directory_refuses_foreign_marker(tmp_path):
    fixture = create_fixture_directory(tmp_path)
    (fixture.path / bench.MARKER_NAME).write_text("f" * 32)
    assert cleanup_fixture_directory(fixture) is False
    assert fixture.path.exists()
    assert cleanup_fixture_directory(create_fixture_directory(tmp_path)) is True


# ---------------------------------------------------------------------------
# Server identity
# ---------------------------------------------------------------------------


def test_fixture_identity_uses_existing_health_route_only():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    mark_fixture_health(app, "fixture-only")
    with TestClient(app) as client:
        response = client.get("/api/health")
        assert response.status_code == 200
        assert response.headers["X-Hnefatafl-Benchmark-Fixture"] == "fixture-only"
        assert response.json() == {"status": "ok"}
        unknown = client.get("/__benchmark_identity")
        assert unknown.status_code == 404
        assert "X-Hnefatafl-Benchmark-Fixture" not in unknown.headers


def test_bind_exclusive_socket_sets_tcp_nodelay_for_measurement_fidelity():
    listener = bench.bind_exclusive_socket(bench.LOOPBACK, 0)
    port = listener.getsockname()[1]
    accepted = []

    def accept():
        connection, _ = listener.accept()
        accepted.append(connection)

    thread = threading.Thread(target=accept)
    thread.start()
    client = socket.create_connection((bench.LOOPBACK, port))
    thread.join(timeout=5)
    try:
        assert accepted
        assert accepted[0].getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY) == 1
    finally:
        client.close()
        for connection in accepted:
            connection.close()
        listener.close()


def test_bind_exclusive_socket_rejects_occupied_port_without_killing_occupant():
    occupant = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    occupant.bind((bench.LOOPBACK, 0))
    occupant.listen(1)
    port = occupant.getsockname()[1]
    try:
        with pytest.raises(OSError):
            bench.bind_exclusive_socket(bench.LOOPBACK, port)
        assert occupant.fileno() != -1
    finally:
        occupant.close()


# ---------------------------------------------------------------------------
# Parameters and counts
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "users,rounds,concurrency,port",
    [
        (1, 3, "1", 8501),
        (17, 3, "1", 8501),
        (8, 0, "1", 8501),
        (8, 11, "1", 8501),
        (8, 3, "0", 8501),
        (8, 3, "9", 8501),
        (8, 3, "1,1", 8501),
        (8, 3, "", 8501),
        (8, 3, "x", 8501),
        (8, 3, "1,2,3,4,5,6", 8501),
        (8, 3, "1", 80),
        (8, 3, "1", 65536),
    ],
)
def test_fixture_bounds(users, rounds, concurrency, port):
    with pytest.raises(ValueError):
        validate_parameters(users, rounds, concurrency, port)


def test_defaults_represent_distinct_account_concurrency():
    assert validate_parameters(8, 3, "1,4,8", 8501) == [1, 4, 8]
    assert expected_request_count(8, 3, 13) == 312


def test_validate_phase_counts_requires_every_pair():
    rows = [
        {"actor_index": 0, "endpoint": "a"},
        {"actor_index": 0, "endpoint": "b"},
        {"actor_index": 1, "endpoint": "a"},
        {"actor_index": 1, "endpoint": "b"},
    ]
    assert validate_phase_counts(rows, 2, 1, ("a", "b")) == []
    assert validate_phase_counts(rows[:-1], 2, 1, ("a", "b"))
    extra = rows + [{"actor_index": 0, "endpoint": "a"}]
    assert validate_phase_counts(extra, 2, 1, ("a", "b"))


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def test_percentile_uses_nearest_rank():
    assert percentile([5, 1, 3, 2, 4], 0.95) == 5
    assert percentile([1, 2, 3, 4, 5], 0.5) == 3
    assert percentile(list(range(1, 101)), 0.95) == 95
    assert percentile(list(range(1, 101)), 0.99) == 99


def test_committed_hardened_evidence_matches_its_raw_samples():
    root = Path(__file__).resolve().parents[1]
    summary = json.loads(
        (root / "docs/benchmarks/backend-read-baseline-hardened.json").read_text(encoding="utf-8")
    )
    raw = json.loads(
        (root / "docs/benchmarks/backend-read-requests-hardened.json").read_text(encoding="utf-8")
    )
    by_level = defaultdict(list)
    for row in raw:
        by_level[row["concurrency"]].append(row)
    assert summary["complete"] is True
    assert sum(run["requests"] for run in summary["runs"]) == 936
    for run in summary["runs"]:
        rows = by_level[run["concurrency"]]
        assert run["requests"] == len(rows) == 312
        values = [row["milliseconds"] for row in rows]
        assert run["latency_ms"] == {
            "median": statistics.median(values),
            "p95": percentile(values, 0.95),
            "p99": percentile(values, 0.99),
            "max": max(values),
        }
        assert run["response_bytes"] == {
            "total": sum(row["bytes"] for row in rows),
            "max": max(row["bytes"] for row in rows),
        }
        assert run["status_counts"] == dict(Counter(str(row["status"]) for row in rows))
        assert run["successes"] == 312
        assert run["concurrency_achieved"] is True


def test_committed_corrected_evidence_matches_its_raw_samples():
    root = Path(__file__).resolve().parents[1]
    summary = json.loads(
        (root / "docs/benchmarks/backend-read-baseline-corrected.json").read_text(encoding="utf-8")
    )
    raw = json.loads(
        (root / "docs/benchmarks/backend-read-requests-corrected.json").read_text(encoding="utf-8")
    )
    by_level = defaultdict(list)
    for row in raw:
        by_level[row["concurrency"]].append(row)
    assert summary["complete"] is True
    assert summary["measurement_complete"] is True
    assert summary["cleanup"]["problems"] == []
    assert summary["source_revision"] == "c5e706683439ec7fa9e9ad7b75a4ffae322e96e1"
    assert sum(run["requests"] for run in summary["runs"]) == 936
    assert [run["concurrency"] for run in summary["runs"]] == [1, 4, 8]
    for run in summary["runs"]:
        rows = by_level[run["concurrency"]]
        assert run["requests"] == len(rows) == 312
        values = [row["milliseconds"] for row in rows]
        assert run["latency_ms"] == {
            "median": statistics.median(values),
            "p95": percentile(values, 0.95),
            "p99": percentile(values, 0.99),
            "max": max(values),
        }
        assert run["response_bytes"] == {
            "total": sum(row["bytes"] for row in rows),
            "max": max(row["bytes"] for row in rows),
        }
        assert run["status_counts"] == dict(Counter(str(row["status"]) for row in rows))
        assert run["successes"] == 312
        assert run["concurrency_achieved"] is True
        assert run["workers_stopped"] is True
        assert run["worker_problems"] == []
        for label, endpoint in run["endpoints"].items():
            subset = [row for row in rows if row["endpoint"] == label]
            assert endpoint["requests"] == 24
            assert endpoint["response_bytes"]["total"] == sum(row["bytes"] for row in subset)
            assert endpoint["latency_ms"]["median"] == statistics.median(
                [row["milliseconds"] for row in subset]
            )


def test_metrics_are_deterministic_and_handle_empty():
    rows = [
        {"status": 200, "bytes": 10, "milliseconds": 1.0},
        {"status": 200, "bytes": 20, "milliseconds": 2.0},
        {"status": 500, "bytes": 30, "milliseconds": 3.0},
    ]
    result = metrics(rows)
    assert result["requests"] == 3
    assert result["successes"] == 2
    assert result["failures"] == 1
    assert result["status_counts"] == {"200": 2, "500": 1}
    assert result["latency_ms"]["median"] == 2.0
    assert result["response_bytes"] == {"total": 60, "max": 30}
    empty = metrics([])
    assert empty["requests"] == 0
    assert empty["latency_ms"]["median"] is None
    assert empty["response_bytes"]["max"] is None


# ---------------------------------------------------------------------------
# Cancellation and cleanup
# ---------------------------------------------------------------------------


def test_run_actor_requests_stops_issuing_after_cancel():
    cancel = bench.threading.Event()
    calls = []

    def perform(actor, label, path):
        calls.append((actor, label))
        if len(calls) == 3:
            cancel.set()

    endpoints = [("a", "/a"), ("b", "/b")]
    issued = run_actor_requests(0, endpoints, 5, cancel, perform)
    assert issued == 3
    assert len(calls) == 3


class _FailingClient:
    def close(self):
        raise RuntimeError("client close failed")


class _FakeProcess:
    def __init__(self):
        self.terminated = False
        self.killed = False
        self.waited = False

    def poll(self):
        return 0 if (self.terminated or self.killed) else None

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    def wait(self, timeout=None):
        self.waited = True
        return 0


def test_resources_close_terminates_process_even_if_client_close_fails():
    resources = BenchmarkResources()
    resources.clients = [_FailingClient()]
    process = _FakeProcess()
    resources.process = process
    result = resources.close()
    assert process.terminated is True
    assert process.waited is True
    assert result.ok is False
    assert any("client close" in problem for problem in result.problems)


def test_resources_close_escalates_to_kill_when_graceful_times_out():
    class StubbornProcess(_FakeProcess):
        def wait(self, timeout=None):
            self.waited = True
            if not self.killed:
                raise subprocess.TimeoutExpired("child", timeout)
            return 0

    resources = BenchmarkResources()
    process = StubbornProcess()
    resources.process = process
    result = resources.close()
    assert process.terminated is True
    assert process.killed is True
    assert result.process_stopped is True


def test_handle_signal_sets_cooperative_cancellation():
    resources = BenchmarkResources()
    handle_signal(resources, signal.SIGTERM, None)
    assert resources.cancel.is_set()
    assert resources.signal_name == "SIGTERM"


def test_seed_accounts_stops_during_fixture_creation():
    cancel = bench.threading.Event()

    class FakeAuth:
        def __init__(self):
            self.created = 0

        def create_account(self, *args, **kwargs):
            self.created += 1
            cancel.set()
            return {"id": f"account-{self.created}"}

    class FakeSetup:
        def __init__(self):
            self.state = type("State", (), {"auth": FakeAuth()})()

    with pytest.raises(bench.BenchmarkCancelled):
        bench.seed_accounts(FakeSetup(), 4, cancel)


def test_measure_level_records_network_errors():
    class FailingActor:
        def get(self, path):
            raise httpx.ConnectError("refused")

    resources = BenchmarkResources()
    phase = bench.measure_level(
        1, [0], [[("endpoint", "/api/endpoint")]], 1, resources, [FailingActor()]
    )
    assert phase.cancelled is False
    assert phase.maximum_inflight == 1
    assert phase.workers_stopped is True
    assert phase.rows[0]["status"] == 0
    assert phase.rows[0]["error_type"] == "ConnectError"
    assert metrics(phase.rows)["failures"] == 1


def test_warmup_rejects_ownership_mismatch():
    class FakeClient:
        def get(self, path):
            return httpx.Response(200, json={"games": [{"id": "unexpected"}]})

    state = bench.FixtureState(
        users=1,
        account_ids=["account"],
        admin_id="admin",
        local_ids=[["expected"]],
        single_ids=[[]],
        active_ids=["active"],
        replay_ids=["replay"],
        waiting_ids=["waiting"],
        started_tournament_id="started",
        upcoming_tournament_id="upcoming",
    )
    with pytest.raises(RuntimeError, match="ownership mismatch"):
        bench.warmup_fixture([FakeClient()], [[]], state, bench.threading.Event())


def test_login_actors_rejects_identity_mismatch(monkeypatch):
    class FakeClient:
        def __init__(self, origin):
            self.headers = {}

        def post(self, path, json=None):
            return httpx.Response(
                200,
                request=httpx.Request("POST", "http://127.0.0.1:8501/api/auth/login"),
                json={
                    "account": {"id": "wrong", "role": "USER", "email_verified": True},
                    "csrf_token": "token",
                },
            )

    monkeypatch.setattr(bench, "new_client", FakeClient)
    probe = type("Probe", (), {"base_url": "http://127.0.0.1:8501"})()
    resources = BenchmarkResources()
    with pytest.raises(RuntimeError, match="does not match"):
        bench.login_actors(
            probe,
            [("LoadPlayer00", "secret", "expected-id", "USER")],
            resources,
            bench.threading.Event(),
        )


def test_start_server_reports_failed_subprocess_startup(monkeypatch, tmp_path):
    class ExitedProcess:
        pid = 43210

        def poll(self):
            return 1

        def terminate(self):
            pass

        def kill(self):
            pass

        def wait(self, timeout=None):
            return 1

    class FailingProbe:
        def get(self, path):
            raise httpx.ConnectError("refused")

        def close(self):
            pass

    monkeypatch.setattr(bench.subprocess, "Popen", lambda *a, **k: ExitedProcess())
    monkeypatch.setattr(bench, "new_client", lambda origin: FailingProbe())
    fixture = create_fixture_directory(tmp_path)
    resources = BenchmarkResources()
    try:
        with pytest.raises(RuntimeError, match="failed to start"):
            bench.start_server(bench.SOURCE_ROOT, fixture, 8501, tmp_path, resources)
    finally:
        resources.close()


def test_serve_rejects_a_non_fixture_directory(tmp_path):
    plain = tmp_path / "plain-directory"
    plain.mkdir()
    with pytest.raises(ValueError, match="non-benchmark"):
        bench.serve(bench.SOURCE_ROOT, plain, 8501)


# ---------------------------------------------------------------------------
# Error hygiene
# ---------------------------------------------------------------------------


def test_safe_error_redacts_secret_like_diagnostics():
    redacted = bench.safe_error(RuntimeError("login failed for password=hunter2"))
    assert "hunter2" not in redacted
    assert redacted.endswith("<redacted diagnostic>")
    assert bench.safe_error(RuntimeError("Fixture request failed")) == (
        "RuntimeError: Fixture request failed"
    )


def test_require_never_leaks_response_body_or_credentials():
    response = httpx.Response(
        500,
        request=httpx.Request("GET", "http://127.0.0.1:8501/api/profile"),
        json={"password": "super-secret", "csrf_token": "secret-token"},
    )
    with pytest.raises(RuntimeError) as error:
        require(response)
    message = str(error.value)
    assert "super-secret" not in message
    assert "secret-token" not in message
    assert "/api/profile" in message


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


def git(directory: Path, *arguments: str) -> str:
    return subprocess.check_output(
        ["git", *arguments], cwd=directory, text=True, stderr=subprocess.DEVNULL
    ).strip()


def test_git_revision_rejects_dirty_nested_and_missing_checkout(tmp_path):
    with pytest.raises(ValueError, match="Git checkout"):
        git_revision(tmp_path)
    git(tmp_path, "init")
    server = tmp_path / "hnefatafl" / "server.py"
    server.parent.mkdir()
    server.write_text("# fixture\n")
    git(tmp_path, "add", ".")
    git(
        tmp_path,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.test",
        "commit",
        "-m",
        "fixture",
    )
    revision = git_revision(tmp_path)
    assert revision == git(tmp_path, "rev-parse", "HEAD")
    with pytest.raises(ValueError, match="repository root"):
        git_revision(server.parent)
    server.write_text("# changed\n")
    with pytest.raises(ValueError, match="clean repository"):
        git_revision(tmp_path)
    git(tmp_path, "checkout", "--", "hnefatafl/server.py")
    server.unlink()
    git(tmp_path, "add", "-u")
    git(
        tmp_path,
        "-c",
        "user.name=Fixture",
        "-c",
        "user.email=fixture@example.test",
        "commit",
        "-m",
        "remove fixture",
    )
    with pytest.raises(ValueError, match="does not contain"):
        git_revision(tmp_path)


def test_verified_source_requires_the_runners_own_checkout(tmp_path):
    with pytest.raises(ValueError, match="own checkout"):
        verified_source(tmp_path)


# ---------------------------------------------------------------------------
# Cleanup completion
# ---------------------------------------------------------------------------


def test_cleanup_failure_retains_fixture_and_is_not_ok(tmp_path):
    fixture = create_fixture_directory(tmp_path)

    class Stubborn:
        def poll(self):
            return None

        def terminate(self):
            pass

        def kill(self):
            pass

        def wait(self, timeout=None):
            if timeout is not None:
                raise subprocess.TimeoutExpired("child", timeout)
            raise RuntimeError("still running")

    resources = BenchmarkResources()
    resources.fixture = fixture
    resources.process = Stubborn()
    result = resources.close()
    assert result.ok is False
    assert result.process_stopped is False
    assert result.fixture_removed is False
    assert result.retained_fixture == str(fixture.path)
    assert fixture.path.exists()
    assert any("may still use it" in problem for problem in result.problems)


def test_internal_shutdown_budget_is_below_external_grace():
    # A design invariant, not a proof that a stalled worker always stops in
    # time; the external hard timeout remains the final bound.
    total = (
        bench.STOP_TIMEOUT_SECONDS
        + bench.WORKER_DRAIN_SECONDS
        + bench.TERMINATE_GRACE_SECONDS
        + bench.TERMINATE_FORCE_SECONDS
    )
    assert total < bench.EXTERNAL_KILL_AFTER_SECONDS


def test_measurement_should_stop_only_after_a_stopped_phase():
    stopped = bench.PhaseResult([], 1, False, [], True)
    assert bench.measurement_should_stop(stopped, threading.Event()) is False
    pending = bench.PhaseResult([], 1, False, ["did not stop"], False)
    assert bench.measurement_should_stop(pending, threading.Event()) is True
    cancelled = bench.PhaseResult([], 1, True, [], True)
    assert bench.measurement_should_stop(cancelled, threading.Event()) is True


# ---------------------------------------------------------------------------
# Cancellation propagation
# ---------------------------------------------------------------------------


def test_play_repetition_stops_on_cancel():
    cancel = threading.Event()
    cancel.set()

    class Client:
        pass

    with pytest.raises(bench.BenchmarkCancelled):
        bench.play_repetition(Client(), "/x", Client(), Client(), 0, wrapped=True, cancel=cancel)


def test_seed_fixture_stops_on_cancel():
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(bench.BenchmarkCancelled):
        bench.seed_fixture([object()], object(), [("u", "p", "id", "USER")], 1, cancel)


def _state_for_cancel_tests():
    return bench.FixtureState(
        users=1,
        account_ids=["account"],
        admin_id="admin",
        local_ids=[["local"]],
        single_ids=[["single"]],
        active_ids=["active"],
        replay_ids=["replay"],
        waiting_ids=["waiting"],
        started_tournament_id="started",
        upcoming_tournament_id="upcoming",
    )


def test_verify_fixture_stops_on_cancel(tmp_path):
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(bench.BenchmarkCancelled):
        bench.verify_fixture(
            actors=[object()],
            admin=object(),
            state=_state_for_cancel_tests(),
            db_path=tmp_path / "missing.sqlite3",
            cancel=cancel,
        )


def test_warmup_stops_on_cancel():
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(bench.BenchmarkCancelled):
        bench.warmup_fixture([object()], [[]], _state_for_cancel_tests(), cancel)


def test_login_stops_on_cancel():
    cancel = threading.Event()
    cancel.set()
    probe = type("Probe", (), {"base_url": "http://127.0.0.1:8501"})()
    with pytest.raises(bench.BenchmarkCancelled):
        bench.login_actors(probe, [("u", "p", "id", "USER")], BenchmarkResources(), cancel)


def test_start_server_stops_on_cancel(tmp_path):
    cancel = threading.Event()
    cancel.set()
    resources = BenchmarkResources(cancel=cancel)
    fixture = create_fixture_directory(tmp_path)
    with pytest.raises(bench.BenchmarkCancelled):
        bench.start_server(bench.SOURCE_ROOT, fixture, 8501, tmp_path, resources)
    assert resources.process is None


def test_measure_level_stops_on_cancel():
    cancel = threading.Event()
    cancel.set()
    resources = BenchmarkResources(cancel=cancel)
    phase = bench.measure_level(1, [0], [[("e", "/e")]], 1, resources, [object()])
    assert phase.cancelled is True
    assert phase.rows == []


def test_real_subprocess_termination_escalates_and_reaps():
    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)",
        ]
    )
    resources = BenchmarkResources()
    resources.process = child
    try:
        assert resources.terminate_process(grace=0.5, force=5.0) is True
        assert child.poll() is not None
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()


# ---------------------------------------------------------------------------
# Measurement scheduling
# ---------------------------------------------------------------------------


def test_measure_level_does_not_busy_wait_on_completed_futures(monkeypatch):
    calls = {"count": 0}
    real_wait = bench.wait

    def counting_wait(futures, timeout=None, return_when=None):
        calls["count"] += 1
        return real_wait(futures, timeout=timeout, return_when=return_when)

    monkeypatch.setattr(bench, "wait", counting_wait)

    class SlowActor:
        def __init__(self, delay):
            self.delay = delay

        def get(self, path):
            time.sleep(self.delay)
            return httpx.Response(
                200, request=httpx.Request("GET", "http://127.0.0.1:1" + path), content=b""
            )

    resources = BenchmarkResources()
    phase = bench.measure_level(
        2,
        [0, 1],
        [[("e", "/e")], [("e", "/e")]],
        1,
        resources,
        [SlowActor(0.01), SlowActor(0.6)],
    )
    assert len(phase.rows) == 2
    assert phase.maximum_inflight == 2
    assert calls["count"] <= 8


# ---------------------------------------------------------------------------
# Fixture storage safety
# ---------------------------------------------------------------------------


def test_fixture_storage_rejects_symlink_to_external_sentinel(tmp_path):
    sentinel = tmp_path / "operator.sqlite3"
    sentinel.write_text("operator-data", encoding="utf-8")
    fixture = create_fixture_directory(tmp_path)
    (fixture.path / "hnefatafl.sqlite3").symlink_to(sentinel)
    with pytest.raises(ValueError, match="symlink"):
        validate_fixture_directory(fixture.path, require_config=False)
    assert sentinel.read_text(encoding="utf-8") == "operator-data"


def test_fixture_storage_rejects_nested_symlink_escape(tmp_path):
    external = tmp_path / "outside"
    external.mkdir()
    fixture = create_fixture_directory(tmp_path)
    jobs = fixture.path / "jobs"
    jobs.mkdir()
    (jobs / "escape").symlink_to(external)
    with pytest.raises(ValueError, match="symlink"):
        validate_fixture_directory(fixture.path, require_config=False)


def test_serve_rejects_indirect_storage_before_backend_init(tmp_path, monkeypatch):
    sentinel = tmp_path / "sentinel.sqlite3"
    sentinel.write_text("keep", encoding="utf-8")
    fixture = create_fixture_directory(tmp_path)
    (fixture.path / "hnefatafl.sqlite3").symlink_to(sentinel)
    monkeypatch.setenv("HNEFATAFL_DATA_DIR", str(fixture.path))
    with pytest.raises(ValueError, match="symlink"):
        bench.serve(bench.SOURCE_ROOT, fixture.path, 8501)
    assert sentinel.read_text(encoding="utf-8") == "keep"


# ---------------------------------------------------------------------------
# Exact fixture semantics
# ---------------------------------------------------------------------------


def test_require_exact_ids_rejects_missing_extra_and_empty():
    bench.require_exact_ids({"a", "b"}, {"a", "b"}, "x")
    with pytest.raises(RuntimeError, match="missing"):
        bench.require_exact_ids({"a"}, {"a", "b"}, "x")
    with pytest.raises(RuntimeError, match="unexpected"):
        bench.require_exact_ids({"a", "b", "c"}, {"a", "b"}, "x")
    with pytest.raises(RuntimeError, match="non-empty"):
        bench.require_exact_ids(set(), set(), "x")


def test_require_no_ids_rejects_foreign_records():
    bench.require_no_ids(set(), "x")
    with pytest.raises(RuntimeError, match="expected no records"):
        bench.require_no_ids({"a"}, "x")


def test_expected_bracket_counts_match_double_elimination_plan():
    assert bench.expected_bracket_match_count(2) == 2
    assert bench.expected_bracket_match_count(4) == 6
    assert bench.expected_bracket_match_count(8) == 14
    assert bench.expected_bracket_leg_count(2, 2) == 2
    assert bench.expected_bracket_leg_count(4, 4) == 4
    assert bench.expected_bracket_leg_count(8, 8) == 8
    assert bench.expected_bracket_leg_count(3, 4) == 2
    assert bench.expected_bracket_leg_count(5, 8) == 2
    assert bench.expected_bracket_leg_count(6, 8) == 4


def test_tournament_games_maps_legs_to_participants():
    bracket = {
        "upper": [
            {
                "round": 1,
                "matches": [
                    {
                        "participants": {
                            "a": {"account_id": "x"},
                            "b": {"account_id": "y"},
                        },
                        "legs": [{"game_id": "g1"}, {"game_id": "g2"}],
                    }
                ],
            }
        ],
        "lower": [],
        "final": None,
        "reset": None,
    }
    assert bench.tournament_games(bracket) == {"g1": ("x", "y"), "g2": ("x", "y")}


# ---------------------------------------------------------------------------
# Partial evidence and telemetry
# ---------------------------------------------------------------------------


def test_raw_samples_survive_summary_failure(tmp_path):
    out = tmp_path / "evidence"
    out.mkdir()
    raw = [
        {
            "concurrency": 1,
            "actor_index": 0,
            "endpoint": "e",
            "status": 200,
            "bytes": 1,
            "milliseconds": 1.0,
            "error_type": None,
        }
    ]
    assert bench.write_raw_evidence(out, raw) == []
    assert (out / "requests.json").exists()
    problems = bench.write_summary_evidence(out, {"unserializable": object()})
    assert problems and problems[0].startswith("summary evidence")
    assert (out / "requests.json").read_text(encoding="utf-8")


def test_telemetry_unavailable_is_none():
    class Dead:
        pid = 999_999_999

    assert bench.safe_server_cpu_seconds(Dead()) is None
    assert bench.safe_server_memory(Dead()) is None


# ---------------------------------------------------------------------------
# Environment metadata
# ---------------------------------------------------------------------------


def test_assert_import_origin_records_imported_paths():
    recorded = bench.assert_import_origin(bench.SOURCE_ROOT)
    assert recorded["server"] == str((bench.SOURCE_ROOT / "hnefatafl" / "server.py").resolve())
    assert recorded["engine"].endswith("hnefatafl/engine.py")


def test_cgroup_hierarchy_walks_nested_slices(tmp_path, monkeypatch):
    root = tmp_path / "cg"
    (root / "a" / "b").mkdir(parents=True)
    (root / "a" / "cpu.max").write_text("50000 100000", encoding="utf-8")
    (root / "a" / "b" / "cpu.max").write_text("max 100000", encoding="utf-8")
    mount = bench.CgroupMount(root="/", point=root)
    monkeypatch.setattr(bench, "cgroup_v2_mount", lambda: mount)
    monkeypatch.setattr(bench, "cgroup_v2_leaf", lambda item: root / "a" / "b")
    assert bench._cgroup_v2_hierarchy("cpu.max") == ["max 100000", "50000 100000"]
    assert bench.cgroup_cpu_quota_cores() == 0.5


def test_cgroup_leaf_resolves_normal_root_and_nested_slice(tmp_path, monkeypatch):
    mount = bench.CgroupMount(root="/", point=tmp_path / "cg")
    monkeypatch.setattr(bench, "_process_cgroup_path", lambda: "/user.slice/foo.scope")
    assert bench.cgroup_v2_leaf(mount) == mount.point / "user.slice" / "foo.scope"


def test_cgroup_leaf_resolves_delegated_subtree(tmp_path, monkeypatch):
    mount = bench.CgroupMount(root="/delegated", point=tmp_path / "cg")
    monkeypatch.setattr(bench, "_process_cgroup_path", lambda: "/delegated/session-1")
    assert bench.cgroup_v2_leaf(mount) == mount.point / "session-1"


def test_cgroup_leaf_outside_delegated_subtree_is_unavailable(tmp_path, monkeypatch):
    mount = bench.CgroupMount(root="/delegated", point=tmp_path / "cg")
    monkeypatch.setattr(bench, "_process_cgroup_path", lambda: "/other/slice")
    assert bench.cgroup_v2_leaf(mount) is None


def test_cgroup_leaf_unavailable_without_process_path(tmp_path, monkeypatch):
    mount = bench.CgroupMount(root="/", point=tmp_path / "cg")
    monkeypatch.setattr(bench, "_process_cgroup_path", lambda: None)
    assert bench.cgroup_v2_leaf(mount) is None


# ---------------------------------------------------------------------------
# Bounded worker shutdown
# ---------------------------------------------------------------------------


def test_measure_level_reports_worker_pending_after_bounded_waits(monkeypatch):
    monkeypatch.setattr(bench, "STOP_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(bench, "WORKER_DRAIN_SECONDS", 0.2)
    monkeypatch.setattr(bench, "CANCEL_POLL_SECONDS", 0.05)
    resources = BenchmarkResources()

    class StuckActor:
        def get(self, path):
            time.sleep(1.2)
            return httpx.Response(
                200, request=httpx.Request("GET", "http://127.0.0.1:1"), content=b""
            )

    def cancel_later():
        time.sleep(0.1)
        resources.cancel.set()

    threading.Thread(target=cancel_later, daemon=True).start()
    started = time.monotonic()
    phase = bench.measure_level(1, [0], [[("e", "/e")]], 1, resources, [StuckActor()])
    elapsed = time.monotonic() - started
    assert elapsed < 1.0
    assert phase.cancelled is True
    assert phase.workers_stopped is False
    assert any("did not stop" in problem for problem in phase.worker_problems)
    assert bench.measurement_should_stop(phase, resources.cancel) is True


def test_safe_telemetry_swallows_unexpected_errors(monkeypatch):
    def boom(process):
        raise RuntimeError("unexpected telemetry failure")

    monkeypatch.setattr(bench, "server_cpu_seconds", boom)
    monkeypatch.setattr(bench, "server_memory", boom)

    class Dead:
        pid = 1

    assert bench.safe_server_cpu_seconds(Dead()) is None
    assert bench.safe_server_memory(Dead()) is None


# ---------------------------------------------------------------------------
# Hard-linked storage
# ---------------------------------------------------------------------------


def test_fixture_storage_rejects_hard_link_to_external_sentinel(tmp_path):
    sentinel = tmp_path / "operator.sqlite3"
    sentinel.write_text("operator-data", encoding="utf-8")
    fixture = create_fixture_directory(tmp_path)
    os.link(sentinel, fixture.path / "hnefatafl.sqlite3")
    with pytest.raises(ValueError, match="hard-linked"):
        validate_fixture_directory(fixture.path, require_config=False)
    assert sentinel.read_text(encoding="utf-8") == "operator-data"


# ---------------------------------------------------------------------------
# Timed endpoint semantics
# ---------------------------------------------------------------------------


def _leaderboard_entry(account_id, rank):
    return {
        "account_id": account_id,
        "rating": 1500.0,
        "rated_games": 1,
        "wins": 0,
        "losses": 0,
        "draws": 1,
        "rank": rank,
    }


def _leaderboard_page(account_ids):
    return {
        "entries": [
            _leaderboard_entry(account_id, index + 1)
            for index, account_id in enumerate(account_ids)
        ],
        "total": len(account_ids),
        "limit": 10,
        "policy": {
            "version": 1,
            "initial_rating": 1500,
            "k_factor": 32,
            "provisional_games": 10,
        },
    }


def test_validate_leaderboard_page_rejects_empty_incomplete_and_foreign():
    accounts = ["a", "b", "c"]
    bench.validate_leaderboard_page(_leaderboard_page(accounts), accounts, 10)
    with pytest.raises(RuntimeError, match="expected 3"):
        bench.validate_leaderboard_page(
            {**_leaderboard_page(accounts), "entries": []}, accounts, 10
        )
    with pytest.raises(RuntimeError, match="foreign"):
        foreign = _leaderboard_page(accounts)
        foreign["entries"][0]["account_id"] = "z"
        bench.validate_leaderboard_page(foreign, accounts, 10)
    with pytest.raises(RuntimeError, match="total"):
        bench.validate_leaderboard_page({**_leaderboard_page(accounts), "total": 2}, accounts, 10)


def test_validate_lobby_page_rejects_empty_short_and_foreign():
    expected = ["g1", "g2", "g3"]
    games = [{"id": game_id, "status": "active"} for game_id in expected]
    bench.validate_lobby_page(games, expected, 25)
    with pytest.raises(RuntimeError, match="expected 3"):
        bench.validate_lobby_page([], expected, 25)
    with pytest.raises(RuntimeError, match="expected 3"):
        bench.validate_lobby_page(games[:2], expected, 25)
    with pytest.raises(RuntimeError, match="production ordering"):
        bench.validate_lobby_page([games[1], games[0], games[2]], expected, 25)
    with pytest.raises(RuntimeError, match="completed"):
        bench.validate_lobby_page(
            [{"id": game_id, "status": "completed"} for game_id in expected], expected, 25
        )


def test_validate_lobby_page_honours_a_limited_page():
    expected = [f"g{index}" for index in range(30)]
    page = [{"id": game_id, "status": "active"} for game_id in expected[:25]]
    bench.validate_lobby_page(page, expected, 25)
    with pytest.raises(RuntimeError, match="expected 25"):
        bench.validate_lobby_page(page[:24], expected, 25)


def _single_player_page():
    return {
        "games": [{"id": "g"}],
        "totals": {"total_games": 2, "completed_games": 0, "in_progress": 2},
        "statistics": {
            "groups": [
                {
                    "ruleset": "brandubh",
                    "played": 1,
                    "completed": 0,
                    "wins": 0,
                    "losses": 0,
                    "draws": 0,
                },
                {
                    "ruleset": "tablut",
                    "played": 1,
                    "completed": 0,
                    "wins": 0,
                    "losses": 0,
                    "draws": 0,
                },
            ],
            "coverage": {"unattributed_games": 0},
        },
    }


def test_validate_single_player_statistics_rejects_malformed():
    bench.validate_single_player_statistics(_single_player_page(), 2)
    with pytest.raises(RuntimeError, match="exactly one"):
        bench.validate_single_player_statistics({**_single_player_page(), "games": []}, 2)
    with pytest.raises(RuntimeError, match="totals"):
        bench.validate_single_player_statistics(
            {**_single_player_page(), "totals": {"total_games": 0}}, 2
        )
    with pytest.raises(RuntimeError, match="groups"):
        bench.validate_single_player_statistics(
            {**_single_player_page(), "statistics": {"groups": []}}, 2
        )
