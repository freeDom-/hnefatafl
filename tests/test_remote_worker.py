"""Worker credentials, local ownership and retry/cancellation behavior."""

from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import stat
import threading
import time
from types import SimpleNamespace
import urllib.request

import pytest

from hnefatafl import remote_worker as remote


TOKEN = "worker-" + "a" * 40
LEASE_TOKEN = "lease-" + "b" * 40
JOB_ID = "c" * 32


def job(identifier=JOB_ID):
    return {
        "id": identifier,
        "kind": "train",
        "config": {"ruleset": "brandubh", "model_name": "Remote Ulf"},
        "lease_token": LEASE_TOKEN,
        "resume_available": False,
    }


class FastStop(threading.Event):
    def wait(self, timeout=None):
        return super().wait(min(timeout or 0.001, 0.001))


class ReplyAPI:
    token = TOKEN
    timeout = 0.1

    def redact(self, message, lease_token=""):
        return message.replace(self.token, "[redacted]").replace(
            lease_token or "!unused!", "[redacted]"
        )

    def request(self, route, payload=None, **kwargs):
        return {"lease_seconds": 120, "cancel_requested": False}


def supervisor(monkeypatch, api, runner):
    from hnefatafl import models

    monkeypatch.setattr(models, "device_info", lambda: {})
    monkeypatch.setattr(remote, "WorkerAPI", lambda *args, **kwargs: api)
    monkeypatch.setattr(remote, "JobRunner", runner)


@contextmanager
def redirect_server():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append((self.path, self.headers.get("Authorization")))
            self.send_response(302)
            self.send_header("Location", "/credential-destination")
            self.end_headers()

        def do_GET(self):
            requests.append((self.path, self.headers.get("Authorization")))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_credentials_are_not_forwarded_to_redirect_destinations():
    with redirect_server() as (url, requests):
        api = remote.WorkerAPI(url, TOKEN)
        api.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), remote._NoRedirects()
        )
        with pytest.raises(remote.WorkerAPIError, match="redirects are disabled") as error:
            api.request("/api/worker/heartbeat")
        assert error.value.status == 302
        assert requests == [("/api/worker/heartbeat", f"Bearer {TOKEN}")]
        assert TOKEN not in str(error.value)


@pytest.mark.parametrize("body", [b"not json", b"[]", b"null"])
def test_api_requires_a_json_object(body):
    api = remote.WorkerAPI("https://example.invalid", TOKEN)
    api.opener = SimpleNamespace(open=lambda *args, **kwargs: io.BytesIO(body))
    with pytest.raises(remote.WorkerAPIError, match="JSON|object"):
        api.request("/api/worker/claim")


def test_json_response_size_is_bounded(monkeypatch):
    monkeypatch.setattr(remote, "MAX_RESPONSE_BYTES", 16)
    api = remote.WorkerAPI("https://example.invalid", TOKEN)
    api.opener = SimpleNamespace(open=lambda *args, **kwargs: io.BytesIO(b"x" * 17))
    with pytest.raises(remote.WorkerAPIError, match="size limit"):
        api.request("/api/worker/heartbeat")


def test_resume_uses_a_header_and_does_not_leave_partial_oversized_downloads(tmp_path, monkeypatch):
    api = remote.WorkerAPI("https://example.invalid", TOKEN)
    requests = []

    def open_request(request):
        requests.append(request)
        return io.BytesIO(b"checkpoint")

    monkeypatch.setattr(api, "_request", open_request)
    target = tmp_path / "resume.pt"
    api.download_resume(JOB_ID, LEASE_TOKEN, target)
    assert target.read_bytes() == b"checkpoint"
    assert LEASE_TOKEN not in requests[0].full_url
    assert requests[0].get_header("X-lease-token") == LEASE_TOKEN
    assert requests[0].get_header("Authorization") == f"Bearer {TOKEN}"
    target.unlink()
    monkeypatch.setattr(remote, "MAX_ARTIFACT_BYTES", 3)
    with pytest.raises(ValueError, match="size limit"):
        api.download_resume(JOB_ID, LEASE_TOKEN, target)
    assert not target.exists()
    assert not target.with_suffix(".download").exists()


def test_progress_strips_nested_paths_and_credentials_without_losing_history():
    original = {
        "iteration": 2,
        "checkpoint": "/cloud/last.pt",
        "history": [{"iteration": 1, "policy_loss": 0.75, "path": "/cloud/data"}],
        "model": {"id": "model-id", "lease_token": LEASE_TOKEN, "token": TOKEN},
    }
    safe = remote.safe_progress(original)
    assert safe == {
        "iteration": 2,
        "history": [{"iteration": 1, "policy_loss": 0.75}],
        "model": {"id": "model-id"},
    }
    assert original["checkpoint"] == "/cloud/last.pt"


def test_claim_ledgers_are_private_and_a_directory_has_one_supervisor(tmp_path):
    ledger = tmp_path / "pending-claim.json"
    remote._write_private(ledger, {"request_id": JOB_ID, "secret": TOKEN})
    assert stat.S_IMODE(ledger.stat().st_mode) == 0o600
    remote._write_private(ledger, {"request_id": "d" * 32})
    assert stat.S_IMODE(ledger.stat().st_mode) == 0o600
    assert TOKEN not in ledger.read_text()
    with remote.worker_directory(tmp_path / "worker") as owned:
        assert owned == (tmp_path / "worker").resolve()
        with pytest.raises(ValueError, match="already owns"):
            with remote.worker_directory(owned):
                pytest.fail("Two supervisors acquired the same worker directory")
    with remote.worker_directory(tmp_path / "worker"):
        pass


def test_owned_job_directories_cannot_escape_through_identifiers_or_symlinks(tmp_path):
    directory = tmp_path / "worker"
    directory.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "important.txt"
    marker.write_text("keep")
    with pytest.raises(ValueError, match="identifier"):
        remote.owned_job_directory(directory, "../outside")
    (directory / "jobs").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="safe local"):
        remote.owned_job_directory(directory, JOB_ID)
    (directory / "jobs").unlink()
    (directory / "jobs").mkdir()
    (directory / "jobs" / JOB_ID).symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="safe local"):
        remote.owned_job_directory(directory, JOB_ID)
    assert marker.read_text() == "keep"


def test_lost_claim_reply_retries_the_saved_request_without_duplicate_execution(
    tmp_path, monkeypatch
):
    claims = []
    starts = []

    class API(ReplyAPI):
        def request(self, route, payload=None, **kwargs):
            if route.endswith("/heartbeat"):
                return {"worker": {"capacity": 1}, "heartbeat_interval": 10}
            claims.append(payload["request_id"])
            ledger = tmp_path / "pending-claim.json"
            assert json.loads(ledger.read_text())["request_id"] == payload["request_id"]
            assert stat.S_IMODE(ledger.stat().st_mode) == 0o600
            if len(claims) == 1:
                raise remote.WorkerAPIError("Reply was lost after assignment")
            return {"job": job(), "heartbeat_interval": 0.1, "lease_seconds": 120}

    class Runner:
        def __init__(self, api, lease, directory, stop, **kwargs):
            self.job_id = lease.job["id"]
            self.cancel = threading.Event()

        def _save(self, **changes):
            pass

        def _signal_child(self):
            pass

        def run(self):
            starts.append(self.job_id)
            return {"id": self.job_id, "status": "completed"}

    supervisor(monkeypatch, API(), Runner)
    result = remote.run_worker(
        "https://example.invalid",
        TOKEN,
        tmp_path,
        max_jobs=1,
        should_stop=FastStop(),
        emit=lambda _: None,
    )
    assert len(claims) == 2 and claims[0] == claims[1]
    assert starts == [JOB_ID]
    assert result["jobs"] == [{"id": JOB_ID, "status": "completed"}]
    assert not (tmp_path / "pending-claim.json").exists()


def test_heartbeat_capacity_remains_in_force_between_heartbeats(tmp_path, monkeypatch):
    first_started = threading.Event()
    first_released = threading.Event()
    claimed_while_full = []
    claims = []

    class API(ReplyAPI):
        def request(self, route, payload=None, **kwargs):
            if route.endswith("/heartbeat"):
                return {"worker": {"capacity": 1}, "heartbeat_interval": 10}
            if claims:
                assert first_started.wait(2)
                if not first_released.is_set():
                    claimed_while_full.append(True)
            identifier = ("c" if not claims else "d") * 32
            claims.append(identifier)
            return {"job": job(identifier), "heartbeat_interval": 0.1, "lease_seconds": 120}

    class Runner:
        def __init__(self, api, lease, directory, stop, **kwargs):
            self.job_id = lease.job["id"]
            self.cancel = threading.Event()

        def _save(self, **changes):
            pass

        def _signal_child(self):
            pass

        def run(self):
            if self.job_id == JOB_ID:
                first_started.set()
                assert first_released.wait(2)
            return {"id": self.job_id, "status": "completed"}

    supervisor(monkeypatch, API(), Runner)

    class Stop(FastStop):
        waits = 0

        def wait(self, timeout=None):
            self.waits += 1
            if self.waits == 3:
                first_released.set()
            return super().wait(timeout)

    try:
        remote.run_worker(
            "https://example.invalid",
            TOKEN,
            tmp_path,
            concurrency=2,
            max_jobs=2,
            should_stop=Stop(),
            emit=lambda _: None,
        )
    finally:
        first_released.set()
    assert len(claims) == 2
    assert not claimed_while_full


@pytest.mark.parametrize("revoked", [False, True])
def test_reporter_signals_the_child_without_waiting_for_an_upload_to_finish(
    tmp_path, monkeypatch, revoked
):
    signalled = threading.Event()

    class API(ReplyAPI):
        def request(self, route, payload=None, **kwargs):
            if revoked:
                raise remote.WorkerAPIError("Worker was revoked", 403)
            runner.report_stop.set()
            return {"cancel_requested": True, "lease_seconds": 120}

    runner = remote.JobRunner(API(), remote.Lease(job(), 0.05, 120), tmp_path, threading.Event())
    monkeypatch.setattr(runner, "_signal_child", signalled.set)
    runner._report()
    assert runner.cancel.is_set()
    assert signalled.is_set()
    assert runner.lost.is_set() is revoked


@pytest.mark.parametrize("canonical_status", ["completed", "cancelled"])
def test_training_child_receives_only_owned_paths_and_no_worker_credential(
    tmp_path, monkeypatch, canonical_status
):
    captured = {}
    progress_reports = []
    final_history = [{"iteration": 1, "policy_loss": 1.2, "value_loss": 0.4}]
    assigned = job()
    assigned["config"].update(
        output_dir="/server/private", registry_dir="/server/models", resume="/server/last.pt"
    )
    (tmp_path / "result.json").write_text(json.dumps({"status": "completed"}))
    monkeypatch.setenv("HNEFATAFL_WORKER_TOKEN", TOKEN)

    class Process:
        pid = 1234567

        def poll(self):
            return 0

        def wait(self, **kwargs):
            return 0

    def launch(arguments, **options):
        captured.update(arguments=arguments, options=options)
        (tmp_path / "progress.json").write_text(
            json.dumps({"iteration": 1, "history": final_history})
        )
        return Process()

    class API(ReplyAPI):
        def request(self, route, payload=None, **kwargs):
            if route.endswith("/progress"):
                progress_reports.append(json.loads(json.dumps(payload)))
            if route.endswith("/finish"):
                assert progress_reports[-1]["progress"]["history"] == final_history
                return {"status": canonical_status}
            return super().request(route, payload, **kwargs)

    monkeypatch.setattr(remote.subprocess, "Popen", launch)
    runner = remote.JobRunner(API(), remote.Lease(assigned, 30, 120), tmp_path, threading.Event())
    monkeypatch.setattr(runner, "_artifacts", lambda **kwargs: None)
    result = runner.run()
    assert result["status"] == canonical_status
    assert "HNEFATAFL_WORKER_TOKEN" not in captured["options"]["env"]
    assert TOKEN not in json.dumps(captured["arguments"])
    written = json.loads((tmp_path / "config.json").read_text())
    assert written["output_dir"] == str(tmp_path)
    assert written["registry_dir"] == str(tmp_path / "models")
    assert "resume" not in written
    ledger = json.loads((tmp_path / "lease.json").read_text())
    assert ledger["credential"] == hashlib.sha256(TOKEN.encode()).hexdigest()
    assert ledger["phase"] == "finished" and ledger["status"] == canonical_status
    assert TOKEN not in (tmp_path / "lease.json").read_text()
    assert progress_reports[-1]["progress"]["history"] == final_history


def test_recovering_an_ambiguous_finish_uses_the_server_status_without_sending_more_progress(
    tmp_path,
):
    calls = []

    class API(ReplyAPI):
        def request(self, route, payload=None, **kwargs):
            calls.append(route)
            assert route.endswith("/finish")
            assert payload["status"] == "completed"
            return {"status": "cancelled"}

    runner = remote.JobRunner(
        API(), remote.Lease(job(), 30, 120), tmp_path, threading.Event(), recover=True
    )
    runner._save(phase="finishing", status="completed")
    assert runner.run()["status"] == "cancelled"
    assert len(calls) == 1
    assert json.loads((tmp_path / "lease.json").read_text())["status"] == "cancelled"


def test_worker_cli_reads_credential_from_environment_without_printing_it(monkeypatch, capsys):
    from hnefatafl import cli

    called = {}

    def run(server, token, directory, **options):
        called.update(server=server, token=token, directory=directory, options=options)
        return {"status": "stopped", "jobs": []}

    monkeypatch.setenv("HNEFATAFL_WORKER_TOKEN", TOKEN)
    monkeypatch.setattr(remote, "run_worker", run)
    monkeypatch.setattr(cli.signal, "signal", lambda *args: None)
    result = cli.main(
        [
            "worker",
            "--server",
            "https://example.invalid",
            "--work-dir",
            "cloud",
            "--concurrency",
            "2",
            "--once",
        ]
    )
    assert result == 0
    assert called["token"] == TOKEN
    assert called["options"]["concurrency"] == 2 and called["options"]["once"]
    output = capsys.readouterr()
    assert TOKEN not in output.out + output.err


def test_worker_cli_missing_credential_has_an_actionable_error(monkeypatch, capsys):
    from hnefatafl import cli

    monkeypatch.delenv("HNEFATAFL_WORKER_TOKEN", raising=False)
    monkeypatch.setattr(cli.signal, "signal", lambda *args: None)
    assert cli.main(["worker", "--server", "https://example.invalid", "--once"]) == 1
    assert "HNEFATAFL_WORKER_TOKEN" in capsys.readouterr().err


def test_large_progress_flushes_incremental_history_without_losing_rows(tmp_path):
    history = [
        {
            "iteration": index,
            "policy_loss": 1.2,
            "evaluation": {"score": 0.5, "games": [{"reason": "x" * 200}]},
        }
        for index in range(3000)
    ]
    (tmp_path / "progress.json").write_text(json.dumps({"iteration": 3000, "history": history}))
    received = []
    sizes = []

    class API(ReplyAPI):
        def request(self, route, payload=None, **kwargs):
            assert route.endswith("/progress")
            assert payload["history_offset"] == len(received)
            sizes.append(len(json.dumps(payload)))
            received.extend(payload["progress"]["history"])
            return {"lease_seconds": 120}

    runner = remote.JobRunner(API(), remote.Lease(job(), 30, 120), tmp_path, threading.Event())
    runner._final_report()
    assert received == history
    assert len(sizes) > 1 and max(sizes) < 200_000
    assert runner.history_sent == 3000
    assert json.loads((tmp_path / "progress.json").read_text())["history"] == history


def test_progress_report_overlays_a_matching_heartbeat_only(tmp_path):
    (tmp_path / "progress.json").write_text(
        json.dumps({"revision": 4, "duration": 10.0, "history": [{"iteration": 1}]})
    )
    (tmp_path / "heartbeat.json").write_text(
        json.dumps({"revision": 4, "duration": 12.5, "stage": "self_play", "phase_elapsed": 4.0})
    )
    runner = remote.JobRunner(ReplyAPI(), remote.Lease(job(), 10, 120), tmp_path, threading.Event())
    report, _, _ = runner._progress_report()
    assert report["progress"]["duration"] == 12.5
    assert report["progress"]["stage"] == "self_play"
    # The retained history still travels with the report.
    assert report["progress"]["history"] == [{"iteration": 1}]

    (tmp_path / "heartbeat.json").write_text(json.dumps({"revision": 3, "duration": 1.0}))
    stale, _, _ = runner._progress_report()
    assert stale["progress"]["duration"] == 10.0
    assert "stage" not in stale["progress"]


def test_progress_publication_is_decoupled_from_the_lease_interval(tmp_path):
    assert remote.PROGRESS_REPORT_SECONDS == 2.0
    runner = remote.JobRunner(ReplyAPI(), remote.Lease(job(), 10, 120), tmp_path, threading.Event())
    assert runner.interval == 10 and runner.progress_interval == 2.0
    quick = remote.JobRunner(ReplyAPI(), remote.Lease(job(), 1, 120), tmp_path, threading.Event())
    assert quick.progress_interval == 1.0


def test_worker_republishes_progress_while_the_training_process_runs(tmp_path, monkeypatch):
    reports = []
    (tmp_path / "progress.json").write_text(
        json.dumps({"revision": 1, "duration": 5.0, "history": [{"iteration": 1}]})
    )
    (tmp_path / "heartbeat.json").write_text(json.dumps({"revision": 1, "duration": 6.0}))

    class API(ReplyAPI):
        def request(self, route, payload=None, **kwargs):
            assert route.endswith("/progress")
            reports.append(payload["progress"])
            return {"lease_seconds": 120, "cancel_requested": False}

    monkeypatch.setattr(remote, "PROGRESS_REPORT_SECONDS", 0.05)
    runner = remote.JobRunner(API(), remote.Lease(job(), 10, 120), tmp_path, threading.Event())
    assert runner.progress_interval == 0.05
    reporter = threading.Thread(target=runner._report, daemon=True)
    reporter.start()
    try:
        deadline = time.monotonic() + 10
        while len(reports) < 3 and time.monotonic() < deadline:
            time.sleep(0.02)
    finally:
        runner.report_stop.set()
        reporter.join(timeout=5)
    assert len(reports) >= 3
    assert all(report["duration"] == 6.0 for report in reports)
    assert runner.sequence >= 3
