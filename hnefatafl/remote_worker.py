"""Outbound, leased training workers with isolated subprocesses and safe transfers.

The worker credential is read by the CLI from HNEFATAFL_WORKER_TOKEN. It is never
put in commands, URLs, training subprocess environments or progress messages.
"""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Iterator
import urllib.error
import urllib.parse
import urllib.request
from uuid import uuid4


PROTOCOL_VERSION = 1
MAX_ARTIFACT_BYTES = 128 * 1024 * 1024
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
_IDENTIFIER = re.compile(r"[0-9a-f]{32}\Z")
_TOKEN = re.compile(r"[A-Za-z0-9._~-]{16,512}\Z")
_BEST = re.compile(r"iteration-([0-9]{1,6})\.pt\Z")
_PRIVATE_KEYS = {
    "token",
    "lease_token",
    "output_dir",
    "registry_dir",
    "resume",
    "path",
    "checkpoint",
    "best_checkpoint",
    "inference_checkpoint",
}


class WorkerAPIError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status

    @property
    def retryable(self) -> bool:
        return self.status is None or self.status in {408, 429} or self.status >= 500


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # urllib otherwise forwards bearer credentials to a redirected origin.
        return None


def safe_progress(value: Any) -> Any:
    """Remove worker paths/credentials without changing graph and metric values."""
    if isinstance(value, dict):
        return {
            key: safe_progress(item)
            for key, item in value.items()
            if isinstance(key, str) and key not in _PRIVATE_KEYS
        }
    if isinstance(value, list):
        return [safe_progress(item) for item in value]
    return value


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, UnicodeError, ValueError):
        return {}


def _write_private(path: Path, value: dict) -> None:
    temporary = path.with_name(f".{path.name}-{uuid4().hex}")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(value, output, allow_nan=False)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def owned_job_directory(directory: Path, identifier: str) -> Path:
    """A server-issued identifier can only select a distinct local job directory."""
    if not _IDENTIFIER.fullmatch(identifier):
        raise ValueError("The server returned an invalid training job identifier")
    jobs = directory / "jobs"
    owned = jobs / identifier
    if jobs.is_symlink() or owned.is_symlink() or not owned.resolve().is_relative_to(directory):
        raise ValueError("The training job directory is not a safe local directory")
    owned.mkdir(parents=True, exist_ok=True, mode=0o700)
    return owned


@contextmanager
def worker_directory(path: Path) -> Iterator[Path]:
    """One supervisor owns a work directory, including restart recovery."""
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = path.resolve()
    with (path / ".worker.lock").open("a+b") as handle:
        if sys.platform != "win32":
            import fcntl

            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError("Another remote worker already owns this work directory") from None
            try:
                yield path
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        else:
            import msvcrt

            handle.write(b"0")
            handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                raise ValueError("Another remote worker already owns this work directory") from None
            try:
                yield path
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


class WorkerAPI:
    def __init__(self, server: str, token: str, timeout: float = 20):
        parsed = urllib.parse.urlsplit(server)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Use an HTTP(S) server URL without credentials, query or fragment")
        if not _TOKEN.fullmatch(token):
            raise ValueError("HNEFATAFL_WORKER_TOKEN must contain a valid worker credential")
        self.server = server.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.opener = urllib.request.build_opener(_NoRedirects())

    def redact(self, message: str, lease_token: str = "") -> str:
        for secret in (self.token, lease_token):
            if secret:
                message = message.replace(secret, "[redacted]")
        return message[:1000]

    def _request(self, request: urllib.request.Request):
        try:
            return self.opener.open(request, timeout=self.timeout)
        except urllib.error.HTTPError as error:
            message = f"Worker API request failed ({error.code})"
            if 300 <= error.code < 400:
                message = "Worker API redirects are disabled; use the final server URL"
            else:
                try:
                    detail = json.loads(error.read(16000)).get("detail")
                    if isinstance(detail, str):
                        message = detail
                except (OSError, ValueError, AttributeError):
                    pass
            raise WorkerAPIError(self.redact(message), error.code) from None
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise WorkerAPIError(self.redact(f"Worker API connection failed: {error}")) from None

    @staticmethod
    def _response(response) -> dict:
        content = response.read(MAX_RESPONSE_BYTES + 1)
        if len(content) > MAX_RESPONSE_BYTES:
            raise WorkerAPIError("Worker API response exceeds the size limit")
        try:
            value = json.loads(content)
        except ValueError:
            raise WorkerAPIError("Worker API returned invalid JSON") from None
        if not isinstance(value, dict):
            raise WorkerAPIError("Worker API response must be an object")
        return value

    def request(self, route: str, payload: dict | None = None, *, method: str = "POST") -> dict:
        data = json.dumps(payload or {}, allow_nan=False).encode() if method != "GET" else None
        request = urllib.request.Request(
            self.server + route,
            data=data,
            method=method,
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
        )
        with self._request(request) as response:
            return self._response(response)

    def artifact(self, job_id: str, lease: str, kind: str, path: Path) -> dict:
        size = path.stat().st_size
        if not 0 < size <= MAX_ARTIFACT_BYTES:
            raise ValueError("Training artifacts must contain at most 128 MiB")
        boundary = "tafl-" + uuid4().hex
        prefix = (
            f'--{boundary}\r\nContent-Disposition: form-data; name="lease_token"\r\n\r\n'
            f"{lease}\r\n--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; filename="artifact.pt"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode()
        suffix = f"\r\n--{boundary}--\r\n".encode()

        def chunks():
            yield prefix
            with path.open("rb") as source:
                while chunk := source.read(1024 * 1024):
                    yield chunk
            yield suffix

        request = urllib.request.Request(
            f"{self.server}/api/worker/jobs/{job_id}/artifacts/{kind}",
            data=chunks(),
            method="POST",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Content-Length": str(len(prefix) + size + len(suffix)),
            },
        )
        with self._request(request) as response:
            return self._response(response)

    def download_resume(self, job_id: str, lease: str, path: Path) -> None:
        request = urllib.request.Request(
            f"{self.server}/api/worker/jobs/{job_id}/resume",
            headers={"Authorization": f"Bearer {self.token}", "X-Lease-Token": lease},
        )
        temporary = path.with_suffix(".download")
        try:
            with self._request(request) as response, temporary.open("wb") as output:
                total = 0
                while chunk := response.read(1024 * 1024):
                    total += len(chunk)
                    if total > MAX_ARTIFACT_BYTES:
                        raise ValueError("Resume checkpoint exceeds the 128 MiB size limit")
                    output.write(chunk)
                if total == 0:
                    raise ValueError("Resume checkpoint is empty")
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


@dataclass
class Lease:
    job: dict
    interval: float
    seconds: float


class JobRunner:
    def __init__(
        self,
        api: WorkerAPI,
        lease: Lease,
        directory: Path,
        stop: threading.Event,
        *,
        recover: bool = False,
    ):
        self.api = api
        self.job = lease.job
        self.directory = directory
        self.stop = stop
        self.interval = max(0.05, min(float(lease.interval), 30))
        self.lease_seconds = max(1.0, float(lease.seconds))
        self.token = self.job["lease_token"]
        self.job_id = self.job["id"]
        self.recover = recover
        self.cancel = threading.Event()
        self.lost = threading.Event()
        self.report_stop = threading.Event()
        self.process: subprocess.Popen | None = None
        self.last_ack = time.monotonic()
        self.sequence = int(self.job.get("sequence", 0))
        self.history_sent = 0
        self.uploaded: dict[str, tuple[int, int]] = {}
        self.manifest_lock = threading.Lock()
        self.manifest: dict[str, Any] = {
            "job": self.job,
            "interval": self.interval,
            "lease_seconds": self.lease_seconds,
            "credential": hashlib.sha256(api.token.encode()).hexdigest(),
            "phase": "claimed",
        }
        self.error = ""

    def _save(self, **changes) -> None:
        with self.manifest_lock:
            self.manifest.update(changes)
            _write_private(self.directory / "lease.json", self.manifest)

    def _log(self) -> str:
        try:
            with (self.directory / "worker.log").open("rb") as log:
                log.seek(max(0, log.seek(0, 2) - 16000))
                return self.api.redact(log.read().decode(errors="replace"), self.token)
        except OSError:
            return ""

    def _report(self) -> None:
        pending = None
        while not self.report_stop.is_set():
            if self.stop.is_set():
                self.cancel.set()
                self._signal_child()
            if pending is None:
                pending, history_end, _ = self._progress_report()
            try:
                response = self.api.request(f"/api/worker/jobs/{self.job_id}/progress", pending)
                self.last_ack = time.monotonic()
                self.lease_seconds = float(response.get("lease_seconds", self.lease_seconds))
                self.sequence += 1
                self.history_sent = history_end
                self._save(sequence=self.sequence)
                pending = None
                if response.get("cancel_requested"):
                    self.cancel.set()
                    self._signal_child()
            except WorkerAPIError as error:
                if not error.retryable or time.monotonic() - self.last_ack >= self.lease_seconds:
                    self.error = self.api.redact(str(error), self.token)
                    self.lost.set()
                    self.cancel.set()
                    self._signal_child()
                    return
            except (ValueError, OSError) as error:
                self.error = self.api.redact(str(error), self.token)
                self.lost.set()
                self.cancel.set()
                self._signal_child()
                return
            self.report_stop.wait(self.interval if pending is None else min(2, self.interval))

    def _progress_report(self) -> tuple[dict, int, bool]:
        """Keep the original small-report contract; stream large history in chunks."""
        progress = safe_progress(
            _read_json(self.directory / "progress.json") or {"stage": "starting"}
        )
        history = progress.get("history", [])
        offset = None
        end = len(history)
        if (len(json.dumps(progress)) > 400_000 or len(history) > 2000) and history:
            offset = min(self.history_sent, len(history))
            chunk: list[dict] = []
            size = 0
            for point in history[offset : offset + 1000]:
                point_size = len(json.dumps(point))
                if chunk and size + point_size > 128_000:
                    break
                chunk.append(point)
                size += point_size
            progress["history"] = chunk
            end = offset + len(chunk)
        payload = {
            "lease_token": self.token,
            "sequence": self.sequence,
            "progress": progress,
            "logs": self._log(),
        }
        if offset is not None:
            payload["history_offset"] = offset
        return payload, end, end < len(history)

    def _final_report(self) -> None:
        """Flush a short run's final metrics after the periodic reporter has stopped."""
        while True:
            payload, history_end, more = self._progress_report()
            response = self._retry(
                lambda: self.api.request(f"/api/worker/jobs/{self.job_id}/progress", payload)
            )
            self.last_ack = time.monotonic()
            self.sequence += 1
            self.history_sent = history_end
            self._save(sequence=self.sequence)
            if response.get("cancel_requested"):
                self.cancel.set()
            if not more:
                break

    def _retry(self, operation: Callable[[], Any]) -> Any:
        delay = 0.5
        while not self.lost.is_set():
            try:
                return operation()
            except WorkerAPIError as error:
                if not error.retryable:
                    raise
                if time.monotonic() - self.last_ack >= self.lease_seconds:
                    raise WorkerAPIError("The training lease could not be renewed") from None
                if self.report_stop.wait(delay):
                    # Completion retries remain valid after heartbeat reporting stops.
                    time.sleep(delay)
                delay = min(delay * 2, 5)
        raise WorkerAPIError(self.error or "The training lease was lost", 409)

    def _upload(self, kind: str, source: Path) -> None:
        if source.is_symlink() or not source.resolve().is_relative_to(self.directory):
            raise ValueError("Training artifact is outside this job's directory")
        current = source.stat()
        if not 0 < current.st_size <= MAX_ARTIFACT_BYTES:
            raise ValueError("Training artifacts must contain at most 128 MiB")
        signature = (current.st_mtime_ns, current.st_size)
        if self.uploaded.get(kind) == signature:
            return
        temporary = self.directory / f".upload-{uuid4().hex}.pt"
        try:
            # Training atomically replaces checkpoints; retain the same inode/bytes
            # across HTTP retries even if the next iteration finishes meanwhile.
            with source.open("rb") as incoming, temporary.open("wb") as outgoing:
                shutil.copyfileobj(incoming, outgoing, length=1024 * 1024)
            self._retry(lambda: self.api.artifact(self.job_id, self.token, kind, temporary))
            self.uploaded[kind] = signature
        finally:
            temporary.unlink(missing_ok=True)

    def _artifacts(self, *, final: bool = False, completed: bool = False) -> None:
        checkpoint = self.directory / "last.pt"
        if checkpoint.is_file():
            self._upload("checkpoint", checkpoint)
        for path in sorted((self.directory / "best").glob("iteration-*.pt")):
            match = _BEST.fullmatch(path.name)
            if match:
                self._upload(f"best-{int(match.group(1))}", path)
        if final and completed:
            inference = self.directory / "inference.pt"
            if not inference.is_file():
                raise ValueError("Completed training did not produce an inference model")
            self._upload("inference", inference)

    def _signal_child(self) -> None:
        if self.process is None or self.process.poll() is not None:
            return
        try:
            if os.name == "posix":
                os.killpg(self.process.pid, signal.SIGTERM)
            else:
                self.process.terminate()
        except ProcessLookupError:
            pass

    def _reap(self) -> None:
        if self.process is None:
            return
        try:
            self.process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            if os.name == "posix":
                os.killpg(self.process.pid, signal.SIGKILL)
            else:
                self.process.kill()
            self.process.wait(timeout=5)

    def _recover_process(self) -> None:
        previous = _read_json(self.directory / "lease.json")
        self.manifest.update(previous)
        self.sequence = int(previous.get("sequence", 0))
        pid = previous.get("pid")
        candidates = {pid} if isinstance(pid, int) and pid > 1 else set()
        if os.name == "posix" and Path("/proc").is_dir():
            # A hard kill can occur between Popen and persisting the child's PID.
            candidates.update(
                int(path.name) for path in Path("/proc").iterdir() if path.name.isdecimal()
            )
        for candidate in candidates:
            try:
                command = Path(f"/proc/{candidate}/cmdline").read_bytes().split(b"\0")
                config_index = command.index(b"--config")
                matches = (
                    b"hnefatafl.cli" in command
                    and command[config_index + 1] == str(self.directory / "config.json").encode()
                )
            except (OSError, ValueError, IndexError):
                continue
            if not matches:
                continue
            # Only a matching owned configuration identifies a child; PID reuse
            # must never terminate another process.
            try:
                os.killpg(candidate, signal.SIGTERM)
            except ProcessLookupError:
                continue
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                try:
                    status = Path(f"/proc/{candidate}/status").read_text()
                    if "State:\tZ" in status:
                        break
                    if not Path(f"/proc/{candidate}/cmdline").read_bytes():
                        break
                except OSError:
                    break
                time.sleep(0.1)
            else:
                try:
                    os.killpg(candidate, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def run(self) -> dict:
        reporter = threading.Thread(target=self._report, daemon=True)
        status = "failed"
        try:
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            if self.recover:
                self._recover_process()
                if self.manifest.get("phase") == "finishing":
                    # The earlier finish may have committed before its response was lost.
                    status = self.manifest.get("status", "failed")
                    payload = {"lease_token": self.token, "status": status}
                    if self.manifest.get("error"):
                        payload["error"] = self.manifest["error"]
                    reply = self._retry(
                        lambda: self.api.request(f"/api/worker/jobs/{self.job_id}/finish", payload)
                    )
                    status = self._finished_status(reply, status)
                    self._save(phase="finished", status=status)
                    return {"id": self.job_id, "status": status}
            else:
                self._save()
            reporter.start()
            if not self.recover:
                config = {
                    key: value
                    for key, value in self.job["config"].items()
                    if key not in {"output_dir", "registry_dir", "resume"}
                }
                config.update(
                    output_dir=str(self.directory), registry_dir=str(self.directory / "models")
                )
                if self.job.get("resume_available"):
                    source = self.directory / "resume.pt"
                    self._retry(lambda: self.api.download_resume(self.job_id, self.token, source))
                    config["resume"] = str(source)
                configuration = self.directory / "config.json"
                _write_private(configuration, config)
                environment = dict(os.environ)
                environment.pop("HNEFATAFL_WORKER_TOKEN", None)
                with (self.directory / "worker.log").open("ab") as log:
                    self.process = subprocess.Popen(
                        [
                            sys.executable,
                            "-m",
                            "hnefatafl.cli",
                            "train",
                            "--config",
                            str(configuration),
                        ],
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        env=environment,
                        start_new_session=os.name == "posix",
                    )
                self._save(pid=self.process.pid, phase="running")
                signalled = False
                stopped_at = None
                while self.process.poll() is None:
                    if self.stop.is_set() or self.cancel.is_set() or self.lost.is_set():
                        if not signalled:
                            self._signal_child()
                            signalled = True
                            stopped_at = time.monotonic()
                        elif stopped_at is not None and time.monotonic() - stopped_at > 30:
                            self._reap()
                    elif not self.lost.is_set():
                        self._artifacts()
                    self.cancel.wait(0.25)
                self._reap()
            result = _read_json(self.directory / "result.json")
            if self.stop.is_set() or self.cancel.is_set():
                status = "cancelled"
            elif result.get("status") == "completed":
                status = "completed"
            elif result.get("status") == "cancelled":
                status = "cancelled"
            else:
                self.error = (
                    "The remote supervisor restarted before this run finished. Resume its checkpoint."
                    if self.recover
                    else "The training process failed. Inspect its retained worker log."
                )
            if not self.lost.is_set():
                self._artifacts(final=True, completed=status == "completed")
        except Exception as error:
            status = "failed"
            self.error = self.api.redact(str(error), self.token)
            self._signal_child()
            self._reap()
        finally:
            self.report_stop.set()
            if reporter.is_alive():
                reporter.join(timeout=self.api.timeout + 2)
                if reporter.is_alive():
                    self.error = "The training progress request did not stop before completion"
                    self.lost.set()
        if self.lost.is_set():
            self._save(phase="abandoned", error=self.error)
            return {"id": self.job_id, "status": "failed", "error": self.error}
        try:
            self._final_report()
            if self.cancel.is_set():
                status = "cancelled"
        except Exception as error:
            self.error = self.api.redact(str(error), self.token)
            self._save(phase="abandoned", error=self.error)
            return {"id": self.job_id, "status": "failed", "error": self.error}
        self._save(phase="finishing", status=status, error=self.error)
        try:
            payload = {"lease_token": self.token, "status": status}
            if self.error:
                payload["error"] = self.error
            reply = self._retry(
                lambda: self.api.request(f"/api/worker/jobs/{self.job_id}/finish", payload)
            )
            status = self._finished_status(reply, status)
            self._save(phase="finished", status=status)
        except Exception as error:
            self.error = self.api.redact(str(error), self.token)
            self._save(phase="finishing", error=self.error)
            return {"id": self.job_id, "status": "failed", "error": self.error}
        return {"id": self.job_id, "status": status}

    @staticmethod
    def _finished_status(reply: dict, requested: str) -> str:
        status = reply.get("status", requested)
        if not isinstance(status, str) or status not in {"completed", "cancelled", "failed"}:
            raise ValueError("The server returned an invalid training completion status")
        return status


def run_worker(
    server: str,
    token: str,
    work_dir: str | Path,
    *,
    concurrency: int = 1,
    once: bool = False,
    max_jobs: int | None = None,
    should_stop: threading.Event | None = None,
    emit: Callable[[dict], None] | None = None,
) -> dict:
    if type(concurrency) is not int or not 1 <= concurrency <= 16:
        raise ValueError("Worker concurrency must be an integer between 1 and 16")
    if max_jobs is not None and (type(max_jobs) is not int or max_jobs < 1):
        raise ValueError("--max-jobs must be a positive integer")
    api = WorkerAPI(server, token)
    stop = should_stop or threading.Event()
    emit = emit or (lambda event: print(json.dumps(event, allow_nan=False), flush=True))
    from .models import device_info

    from .versioning import application_metadata

    capabilities = {
        "protocol_version": PROTOCOL_VERSION,
        **application_metadata(),
        **device_info(),
    }
    results: list[dict] = []
    with (
        worker_directory(Path(work_dir)) as directory,
        ThreadPoolExecutor(max_workers=concurrency) as pool,
    ):
        futures: dict[Future, JobRunner] = {}
        count = 0
        next_heartbeat = 0.0
        interval = 10.0
        limit = concurrency
        fingerprint = hashlib.sha256(token.encode()).hexdigest()
        for path in sorted((directory / "jobs").glob("*/lease.json")):
            saved = _read_json(path)
            job = saved.get("job", {})
            if not _IDENTIFIER.fullmatch(str(job.get("id", ""))) or not _TOKEN.fullmatch(
                str(job.get("lease_token", ""))
            ):
                continue
            owned = owned_job_directory(directory, job["id"])
            if path.parent != owned or saved.get("phase") == "finished":
                continue
            runner = JobRunner(
                api,
                Lease(job, saved.get("interval", 10), saved.get("lease_seconds", 120)),
                path.parent,
                stop,
                recover=True,
            )
            if saved.get("credential") != fingerprint or saved.get("phase") == "abandoned":
                # Credential rotation cannot leave the old supervisor's children
                # running. No requests are made using the previous credential.
                runner._recover_process()
                runner._save(
                    phase="abandoned", error="The worker credential changed or its lease was lost"
                )
                continue
            futures[pool.submit(runner.run)] = runner
            count += 1
        pending_path = directory / "pending-claim.json"
        saved_pending = _read_json(pending_path)
        claim_id = (
            saved_pending.get("request_id")
            if saved_pending.get("credential") == fingerprint
            else None
        )
        try:
            while True:
                for future, runner in list(futures.items()):
                    if future.done():
                        result = future.result()
                        results.append(result)
                        emit({"event": "job_finished", **result})
                        del futures[future]
                if stop.is_set() and not futures:
                    break
                if max_jobs is not None and count >= max_jobs and not futures:
                    break
                now = time.monotonic()
                if now >= next_heartbeat:
                    try:
                        response = api.request(
                            "/api/worker/heartbeat", {"capabilities": capabilities}
                        )
                        interval = max(0.25, min(float(response.get("heartbeat_interval", 10)), 30))
                        capacity = response.get("worker", {}).get("capacity", concurrency)
                        limit = min(concurrency, capacity) if type(capacity) is int else concurrency
                        next_heartbeat = now + interval
                    except WorkerAPIError as error:
                        if not error.retryable:
                            raise
                        next_heartbeat = now + min(interval, 2)
                        stop.wait(0.5)
                        continue
                if (
                    not stop.is_set()
                    and len(futures) < limit
                    and (max_jobs is None or count < max_jobs)
                ):
                    if not claim_id:
                        claim_id = uuid4().hex
                        _write_private(
                            pending_path, {"request_id": claim_id, "credential": fingerprint}
                        )
                    try:
                        response = api.request("/api/worker/claim", {"request_id": claim_id})
                        job = response.get("job")
                        if job is None:
                            pending_path.unlink(missing_ok=True)
                            claim_id = None
                            if once and not futures:
                                break
                        else:
                            if (
                                not isinstance(job, dict)
                                or not _IDENTIFIER.fullmatch(str(job.get("id", "")))
                                or not _TOKEN.fullmatch(str(job.get("lease_token", "")))
                                or job.get("kind") != "train"
                                or not isinstance(job.get("config"), dict)
                            ):
                                raise ValueError("The server returned an invalid training lease")
                            owned = owned_job_directory(directory, job["id"])
                            duplicate = next(
                                (
                                    runner
                                    for runner in futures.values()
                                    if runner.job_id == job["id"]
                                ),
                                None,
                            )
                            if duplicate:
                                if duplicate.token != job["lease_token"]:
                                    raise ValueError(
                                        "The server replaced an already active training lease"
                                    )
                                pending_path.unlink(missing_ok=True)
                                claim_id = None
                                continue
                            runner = JobRunner(
                                api,
                                Lease(
                                    job,
                                    response.get("heartbeat_interval", interval),
                                    response.get("lease_seconds", 120),
                                ),
                                owned,
                                stop,
                            )
                            runner._save()
                            futures[pool.submit(runner.run)] = runner
                            count += 1
                            pending_path.unlink(missing_ok=True)
                            claim_id = None
                            emit({"event": "job_claimed", "id": job["id"]})
                            continue
                    except WorkerAPIError as error:
                        if error.status == 409:
                            # An interrupted claim can outlive its lease. Its
                            # assignment stays terminal; use a new claim nonce.
                            pending_path.unlink(missing_ok=True)
                            claim_id = None
                            continue
                        if not error.retryable:
                            raise
                stop.wait(min(interval, 1))
        finally:
            stop.set()
            for future, runner in list(futures.items()):
                runner.cancel.set()
                runner._signal_child()
            for future in list(futures):
                result = future.result()
                results.append(result)
                emit({"event": "job_finished", **result})
    return {"status": "stopped", "jobs": results}
