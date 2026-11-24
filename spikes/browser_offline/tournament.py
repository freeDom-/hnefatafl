"""Role-balanced paired-opening tournament harness and strict report validator.

The runner is agent-agnostic: native Jarl/neural agents, browser process agents,
or deterministic test agents implement ``choose(state, settings, seed) -> Move``.
Rules, repetition history, legal-move validation and terminal outcomes always use
the shared Python engine. Experimental results are non-authoritative.

Process agents use a persistent newline-delimited JSON protocol::

    request:  {"id": "<unique>", "state": {...}, "settings": {...}, "seed": 7}
    response: {"id": "<same>", "move": {"source": 0, "target": 1}}

The adapter enforces one hard deadline covering request writes, response reads and
process recovery. Stale, duplicated, unexpected or malformed responses fail closed
and replace the child process.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import queue
import random
import secrets
import shlex
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable

SPIKE = Path(__file__).resolve().parent
ROOT = SPIKE.parents[1]
for path in (str(SPIKE), str(ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

from hnefatafl.engine import Move, State, apply_move, legal_moves  # noqa: E402

SCHEMA = 2
AGENT = Callable[[State, dict, int], Move]
SIDES = ("attackers", "defenders")
DEFAULT_AGENT_TIMEOUT = 30.0
PROTOCOL_VERSION = 2
EVIDENCE_REASONS = {
    "simulations",
    "deadline",
    "immediate_win",
    "terminal",
    "insufficient_budget",
    "completed",
}
# Bounded allowance for termination, descendant cleanup, stream closure and
# reader/writer thread joins after a request deadline has expired.
CLEANUP_ALLOWANCE = 2.0
PROCESS_KILL_GRACE = 1.0
OWNER_ENV = "HNEFATAFL_TOURNAMENT_OWNER"
MAX_RESPONSE_BYTES = 1 << 20
MAX_STDERR_BYTES = 64 * 1024
MIN_STRENGTH_PAIRS = 10
TERMINAL_REASONS = {
    "king_captured",
    "king_escaped",
    "encirclement",
    "edge_fort",
    "threefold_repetition",
    "no_legal_moves",
    "terminal",
}
AGENT_KINDS = (
    "scripted",
    "native-jarl",
    "native-seer",
    "native-neural",
    "browser-neural",
    "browser-jarl",
)
NEURAL_KINDS = ("native-neural", "browser-neural")
BROWSER_KINDS = ("browser-neural", "browser-jarl")
AGENT_MODES = {
    "scripted": ("fixed-work",),
    "native-jarl": ("fixed-work", "fixed-time"),
    "native-seer": ("fixed-work", "fixed-time"),
    "native-neural": ("fixed-work", "fixed-time"),
    "browser-neural": ("fixed-work", "fixed-time"),
    "browser-jarl": ("fixed-work", "fixed-time"),
}


def _provider_evidence_failures(
    *,
    requested,
    observed,
    reported_requested,
    inferences,
    gpu_verified,
    provider_observed=None,
) -> list[str]:
    """Shared consistency checks for live and saved provider observations."""
    failures: list[str] = []
    if type(inferences) is not int or inferences < 0:
        return ["inference accounting is missing or invalid"]
    if type(gpu_verified) is not bool:
        failures.append("GPU verification flag must be a strict boolean")
        gpu_verified = False
    if provider_observed is not None and type(provider_observed) is not bool:
        failures.append("provider_observed must be a strict boolean")
    if requested in ("wasm", "webgpu"):
        if provider_observed is False and observed is not None:
            failures.append("provider observation contradicts provider_observed=false")
        elif provider_observed is True and observed is None:
            failures.append("provider observation contradicts provider_observed=true")
        if inferences == 0:
            if observed is not None:
                failures.append("placement was reported without neural inference")
            if reported_requested not in (None, requested):
                failures.append("requested provider contradicts the declared configuration")
            if gpu_verified:
                failures.append("GPU execution was claimed without neural inference")
        else:
            if reported_requested != requested:
                failures.append("requested provider contradicts the declared configuration")
            if requested == "wasm":
                if observed != "wasm":
                    failures.append("observed placement is not WASM")
                    if observed == "cpu-fallback":
                        failures.append("CPU fallback cannot satisfy a WASM observation")
                if gpu_verified:
                    failures.append("WASM observation cannot claim verified GPU execution")
            else:
                if observed in ("wasm", "cpu-fallback"):
                    failures.append("WebGPU request silently fell back to WASM or CPU execution")
                elif observed not in (
                    "webgpu-session-unverified",
                    "webgpu-mixed",
                    "webgpu-session-verified",
                ):
                    failures.append("observed placement is not a WebGPU session placement")
                if gpu_verified and observed != "webgpu-session-verified":
                    failures.append(
                        "unverified or mixed WebGPU cannot claim verified GPU execution"
                    )
    else:
        if observed != requested:
            failures.append("observed provider contradicts the declared provider")
        if gpu_verified:
            failures.append("non-WebGPU provider cannot claim verified GPU execution")
    return failures


class AgentTimeoutError(TimeoutError):
    """Raised when an agent exceeds its hard per-move execution deadline."""


class AgentProtocolError(RuntimeError):
    """Raised when a process agent violates the line protocol or lifecycle."""


class AgentConfigurationError(AgentProtocolError):
    """Raised when the observed adapter identity contradicts the declaration."""


def _close_stream(stream) -> str | None:
    try:
        if stream is not None:
            stream.close()
    except (OSError, ValueError) as error:
        return f"stream close failed: {error}"
    return None


def _process_start_time(pid: int) -> int | None:
    """Linux boot-relative start time used to avoid signalling a reused PID."""
    if not Path("/proc").is_dir():
        return None
    try:
        stat_text = Path(f"/proc/{pid}/stat").read_text()
        tail = stat_text[stat_text.rfind(")") + 2 :].split()
        return int(tail[19])
    except (OSError, IndexError, ValueError):
        return None


def _proc_pids() -> list[int]:
    if not Path("/proc").is_dir():
        return []
    try:
        return [int(name) for name in os.listdir("/proc") if name.isdigit()]
    except OSError:
        return []


def _proc_group_start(pid: int) -> tuple[int, int] | None:
    try:
        stat_text = Path(f"/proc/{pid}/stat").read_text()
        tail = stat_text[stat_text.rfind(")") + 2 :].split()
        return int(tail[2]), int(tail[19])
    except (OSError, IndexError, ValueError):
        return None


def _proc_environ(pid: int) -> bytes | None:
    try:
        return Path(f"/proc/{pid}/environ").read_bytes()
    except OSError:
        return None


def _member_owned(
    pid: int,
    pgid: int,
    owner_token: str,
    minimum_start_time: int | None,
) -> int | None:
    """Return the process start time when the instance verifiably belongs to us."""
    group_start = _proc_group_start(pid)
    if group_start is None:
        return None
    process_group, start_time = group_start
    if process_group != pgid:
        return None
    if minimum_start_time is not None and start_time < minimum_start_time:
        return None
    environment = _proc_environ(pid)
    if environment is None:
        return None
    marker = f"{OWNER_ENV}={owner_token}".encode()
    if marker not in environment.split(b"\0"):
        return None
    return start_time


def _group_member_pids(pgid: int) -> list[int]:
    members = []
    for pid in _proc_pids():
        group_start = _proc_group_start(pid)
        if group_start is not None and group_start[0] == pgid:
            members.append(pid)
    return members


def _owned_group_members(
    pgid: int,
    owner_token: str,
    minimum_start_time: int | None,
) -> list[tuple[int, int]]:
    """Return verified owned (pid, start_time) instances in the captured group."""
    members = []
    for pid in _group_member_pids(pgid):
        start_time = _member_owned(pid, pgid, owner_token, minimum_start_time)
        if start_time is not None:
            members.append((pid, start_time))
    return members


def _signal_member(
    pid: int,
    start_time: int,
    signum: int,
    pgid: int,
    owner_token: str,
) -> None:
    """Signal one verified owned instance, closing the identity/pidfd race."""
    if _member_owned(pid, pgid, owner_token, start_time) != start_time:
        return
    if hasattr(os, "pidfd_open") and hasattr(signal, "pidfd_send_signal"):
        try:
            descriptor = os.pidfd_open(pid)
        except (OSError, ProcessLookupError):
            return
        try:
            # Re-verify after opening the pidfd: the pid may have been reused
            # between the initial check and pidfd_open().
            if _member_owned(pid, pgid, owner_token, start_time) != start_time:
                return
            signal.pidfd_send_signal(descriptor, signum)
        except (ProcessLookupError, OSError):
            pass
        finally:
            try:
                os.close(descriptor)
            except OSError:
                pass
        return
    if _member_owned(pid, pgid, owner_token, start_time) != start_time:
        return
    try:
        os.kill(pid, signum)
    except (ProcessLookupError, OSError):
        pass


def _terminate_process_tree(
    process: subprocess.Popen,
    process_group: int | None,
    root_start_time: int | None,
    owner_token: str | None,
    allowance: float,
) -> list[str]:
    """Terminate the owned process group/tree before any stream cleanup.

    The captured group id and root start time are used instead of rediscovering
    the group through the immediate child, so descendants are still terminated
    after the parent exits. Members are signalled individually through pidfds
    where available, never blindly via a recycled group id.
    """
    failures: list[str] = []
    pid = getattr(process, "pid", None)
    if type(pid) is not int or pid <= 0:
        return ["process has no valid pid"]
    deadline = time.monotonic() + allowance
    if os.name == "posix" and process_group is not None and process_group != os.getpgrp():
        owner_token = owner_token or ""
        all_members = set(_group_member_pids(process_group))
        owned = _owned_group_members(process_group, owner_token, root_start_time)
        ambiguous = all_members - {member_pid for member_pid, _ in owned}
        if ambiguous:
            failures.append(
                f"ownership could not be verified for {len(ambiguous)} process-group "
                "member(s); left untouched"
            )
        for member_pid, member_start in owned:
            _signal_member(member_pid, member_start, signal.SIGTERM, process_group, owner_token)
        # Wait for graceful exit even when the immediate parent has already gone.
        grace_deadline = min(deadline, time.monotonic() + PROCESS_KILL_GRACE)
        while time.monotonic() < grace_deadline:
            if not _owned_group_members(process_group, owner_token, root_start_time):
                break
            time.sleep(0.02)
        remaining = _owned_group_members(process_group, owner_token, root_start_time)
        for member_pid, member_start in remaining:
            _signal_member(member_pid, member_start, signal.SIGKILL, process_group, owner_token)
        # Escalation must not depend on the immediate parent still being alive.
        while time.monotonic() < deadline:
            if not _owned_group_members(process_group, owner_token, root_start_time):
                break
            time.sleep(0.02)
        if _owned_group_members(process_group, owner_token, root_start_time):
            failures.append("owned process-group members survived the cleanup allowance")
    elif os.name == "nt":
        # Never invoke taskkill with a stale root PID after the original root has
        # exited: Windows offers no safe group enumeration without Job Objects.
        if process.poll() is None:
            try:
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=max(0.1, allowance / 2),
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as error:
                failures.append(f"taskkill failed: {error}")
        # If the root has exited, descendant ownership cannot be established and
        # the limitation is recorded by the caller as an incomplete-cleanup risk.
    # Reap the immediate child if it is still present.
    try:
        process.wait(timeout=max(0.05, deadline - time.monotonic()))
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except (ProcessLookupError, OSError):
            pass
        try:
            process.wait(timeout=max(0.05, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            failures.append("process could not be reaped within the cleanup allowance")
    return failures


class JsonProcessAgent:
    """Persistent line-protocol agent with a bounded hard deadline.

    A new response queue is created for every subprocess generation, so a reader
    thread from a replaced generation can never publish into the live one. Every
    request carries a unique identifier and only the matching response is
    accepted. Diagnostics are bounded; timed-out or malformed children are
    terminated and reaped. Use as a context manager (or call ``close()``) to
    guarantee cleanup.
    """

    enforces_own_deadline = True

    def __init__(
        self,
        command: list[str],
        *,
        timeout: float = DEFAULT_AGENT_TIMEOUT,
        cwd: str | Path | None = None,
        max_stderr_bytes: int = MAX_STDERR_BYTES,
        expected_identity: dict | None = None,
    ):
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
            raise ValueError("agent timeout must be positive")
        if not isinstance(max_stderr_bytes, int) or isinstance(max_stderr_bytes, bool):
            raise ValueError("max_stderr_bytes must be an integer")
        if max_stderr_bytes <= 0:
            raise ValueError("max_stderr_bytes must be positive")
        if not command or any(not isinstance(part, str) or not part for part in command):
            raise ValueError("agent command must be a non-empty string list")
        self.command = list(command)
        self.timeout = float(timeout)
        self.cwd = cwd
        self.max_stderr_bytes = max_stderr_bytes
        self.process: subprocess.Popen | None = None
        self.responses: queue.Queue | None = None
        self.stderr_lines: deque[str] = deque()
        self.stderr_bytes = 0
        self._generation = 0
        self._counter = 0
        self._lock = threading.Lock()
        self._closed = False
        self._threads: list[threading.Thread] = []
        self._writers: list[threading.Thread] = []
        self._cleanup_failures: deque[str] = deque(maxlen=8)
        self._process_group: int | None = None
        self._root_start_time: int | None = None
        self._owner_token: str | None = None
        self.last_process_group: int | None = None
        self._failure: str | None = None
        self.expected_identity = dict(expected_identity) if expected_identity else None
        self.handshake: dict | None = None
        self.last_evidence: dict | None = None

    # -- lifecycle ---------------------------------------------------------
    def __enter__(self) -> "JsonProcessAgent":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.close()
        return False

    def close(self) -> None:
        # Bounded close/request serialization: a request can hold the lock for at
        # most its own deadline. If that cannot be acquired, force termination of
        # the owned tree but retain the reported cleanup failure.
        if not self._lock.acquire(timeout=self.timeout + CLEANUP_ALLOWANCE):
            self._closed = True
            process = self.process
            self._cleanup_failures.append("close could not acquire adapter ownership")
            if process is not None:
                for failure in _terminate_process_tree(
                    process,
                    self._process_group,
                    self._root_start_time,
                    self._owner_token,
                    CLEANUP_ALLOWANCE,
                ):
                    self._cleanup_failures.append(failure)
            return
        try:
            self._closed = True
            self._stop_generation()
        finally:
            self._lock.release()

    def _stop_generation(self) -> None:
        process = self.process
        group = self._process_group
        root_start = self._root_start_time
        owner_token = self._owner_token
        self.last_process_group = group
        self.process = None
        self._process_group = None
        self._root_start_time = None
        self._owner_token = None
        threads = self._threads
        self._threads = []
        writers = self._writers
        self._writers = []
        cleanup_deadline = time.monotonic() + CLEANUP_ALLOWANCE
        # Terminate the captured group/tree BEFORE touching buffered streams: a
        # writer may hold the stdin lock and readers may be blocked on inherited
        # pipe handles held by surviving descendants.
        if process is not None:
            for failure in _terminate_process_tree(
                process, group, root_start, owner_token, CLEANUP_ALLOWANCE
            ):
                self._cleanup_failures.append(failure)
        for writer in writers:
            writer.join(
                timeout=max(0.0, min(PROCESS_KILL_GRACE, cleanup_deadline - time.monotonic()))
            )
        stuck_writers = [writer for writer in writers if writer.is_alive()]
        if stuck_writers:
            self._cleanup_failures.append(
                f"{len(stuck_writers)} writer thread(s) did not terminate within the allowance"
            )
        for thread in threads:
            thread.join(
                timeout=max(0.0, min(PROCESS_KILL_GRACE, cleanup_deadline - time.monotonic()))
            )
        stuck_threads = [thread for thread in threads if thread.is_alive()]
        if stuck_threads:
            self._cleanup_failures.append(
                f"{len(stuck_threads)} reader thread(s) did not terminate within the allowance"
            )
        if process is not None:
            # Never synchronously close a buffered stream whose reader/writer is
            # still alive: it may hold the stream lock while blocked. Leaving that
            # handle to process teardown is safer than blocking cleanup.
            if not stuck_writers:
                failure = _close_stream(process.stdin)
                if failure:
                    self._cleanup_failures.append(failure)
            if not stuck_threads:
                for stream in (process.stdout, process.stderr):
                    failure = _close_stream(stream)
                    if failure:
                        self._cleanup_failures.append(failure)

    def _record_stderr(self, line: str) -> None:
        encoded = len(line.encode("utf-8", "replace"))
        self.stderr_lines.append(line.rstrip("\n"))
        self.stderr_bytes += encoded
        while self.stderr_bytes > self.max_stderr_bytes and self.stderr_lines:
            removed = self.stderr_lines.popleft()
            self.stderr_bytes -= len(removed.encode("utf-8", "replace")) + 1

    def _start(self) -> None:
        self._stop_generation()
        self._owner_token = secrets.token_hex(16)
        self._generation += 1
        generation = self._generation
        self.responses = queue.Queue(maxsize=64)
        responses = self.responses
        self._failure = None
        try:
            creationflags = 0
            if os.name == "nt":
                creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            process = subprocess.Popen(
                self.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                cwd=self.cwd,
                env={**os.environ, OWNER_ENV: self._owner_token},
                start_new_session=os.name == "posix",
                creationflags=creationflags,
            )
        except OSError as error:
            raise AgentProtocolError(f"cannot start agent process: {error}") from None
        self.process = process
        if os.name == "posix":
            self._process_group = process.pid
        else:
            self._process_group = process.pid
        self._root_start_time = _process_start_time(process.pid)

        def pump_stdout(generation: int, stream, target: queue.Queue) -> None:
            try:
                while True:
                    line = stream.readline(MAX_RESPONSE_BYTES + 1)
                    if not line:
                        break
                    if len(line) > MAX_RESPONSE_BYTES:
                        target.put_nowait(
                            {"generation": generation, "error": "response exceeds limit"}
                        )
                        return
                    try:
                        target.put_nowait({"generation": generation, "line": line})
                    except queue.Full:
                        return
            except (OSError, ValueError):
                pass
            finally:
                try:
                    target.put_nowait({"generation": generation, "eof": True})
                except queue.Full:
                    pass

        def pump_stderr(stream) -> None:
            try:
                while True:
                    line = stream.readline(self.max_stderr_bytes + 1)
                    if not line:
                        break
                    self._record_stderr(line)
            except (OSError, ValueError):
                pass

        stdout_thread = threading.Thread(
            target=pump_stdout,
            args=(generation, process.stdout, responses),
            daemon=True,
            name=f"agent-stdout-{generation}",
        )
        stderr_thread = threading.Thread(
            target=pump_stderr,
            args=(process.stderr,),
            daemon=True,
            name=f"agent-stderr-{generation}",
        )
        self._threads = [stdout_thread, stderr_thread]
        stdout_thread.start()
        stderr_thread.start()
        if self.expected_identity is not None:
            self._await_handshake(generation, responses)

    def _await_handshake(self, generation: int, responses: queue.Queue) -> None:
        deadline = time.monotonic() + self.timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._stop_generation()
                raise AgentConfigurationError("agent did not send an initialization handshake")
            try:
                entry = responses.get(timeout=remaining)
            except queue.Empty:
                self._stop_generation()
                raise AgentProtocolError("agent did not send an initialization handshake") from None
            if not isinstance(entry, dict) or entry.get("generation") != generation:
                self._stop_generation()
                raise AgentConfigurationError("agent sent a stale handshake generation")
            if entry.get("eof") or entry.get("error"):
                self._stop_generation()
                detail = entry.get("error") or "process exited"
                raise AgentConfigurationError(f"agent failed before its handshake: {detail}")
            line = entry.get("line")
            try:
                payload = json.loads(line) if isinstance(line, str) else None
            except ValueError:
                payload = None
            if not isinstance(payload, dict) or payload.get("kind") != "handshake":
                self._stop_generation()
                raise AgentConfigurationError("agent did not begin with a protocol handshake")
            if payload.get("protocol") != PROTOCOL_VERSION:
                self._stop_generation()
                raise AgentConfigurationError(f"agent handshake protocol is not {PROTOCOL_VERSION}")
            agent = payload.get("agent")
            if not isinstance(agent, dict):
                self._stop_generation()
                raise AgentConfigurationError("agent handshake lacks an agent description")
            for key, expected in self.expected_identity.items():
                if agent.get(key) != expected:
                    self._stop_generation()
                    raise AgentConfigurationError(
                        f"agent handshake {key}={agent.get(key)!r} does not match "
                        f"the declared configuration {expected!r}"
                    )
            self.handshake = agent
            return

    # -- protocol ----------------------------------------------------------
    def _fail(self, message: str) -> AgentProtocolError:
        self._stop_generation()
        return AgentProtocolError(message)

    def _write_request(self, process: subprocess.Popen, line: str, outcome: dict) -> None:
        try:
            assert process.stdin is not None
            process.stdin.write(line)
            process.stdin.flush()
            outcome["ok"] = True
        except (BrokenPipeError, OSError, ValueError) as error:
            outcome["error"] = str(error)

    def __call__(self, state: State, settings: dict, seed: int) -> Move:
        if self._closed:
            raise AgentProtocolError("agent adapter is closed")
        if not self._lock.acquire(blocking=False):
            raise AgentProtocolError("concurrent requests are not supported by one agent adapter")
        try:
            return self._request(state, settings, seed)
        finally:
            self._lock.release()

    def _request(self, state: State, settings: dict, seed: int) -> Move:
        deadline = time.monotonic() + self.timeout
        if self.process is None or self.process.poll() is not None:
            try:
                self._start()
            except AgentProtocolError:
                raise
            except OSError as error:
                raise AgentProtocolError(f"cannot start agent process: {error}") from None
        process = self.process
        responses = self.responses
        if process is None or responses is None:
            raise AgentProtocolError("agent process is unavailable")
        generation = self._generation
        self._counter += 1
        request_id = f"{generation}:{self._counter}"
        request = json.dumps(
            {
                "id": request_id,
                "state": state.to_dict(),
                "settings": settings,
                "seed": seed,
            },
            separators=(",", ":"),
        )
        outcome: dict[str, Any] = {}
        writer = threading.Thread(
            target=self._write_request,
            args=(process, request + "\n", outcome),
            daemon=True,
            name=f"agent-write-{request_id}",
        )
        self._writers.append(writer)
        writer.start()
        remaining = deadline - time.monotonic()
        if remaining > 0:
            writer.join(remaining)
        if not writer.is_alive() and writer in self._writers:
            self._writers.remove(writer)
        if writer.is_alive():
            self._stop_generation()
            detail = "; ".join(self._cleanup_failures)
            raise AgentTimeoutError(
                f"agent write exceeded {self.timeout:g}s deadline"
                + (f" (cleanup: {detail})" if detail else "")
            )
        if "error" in outcome:
            raise self._fail(f"agent process write failed: {outcome['error']}")
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._stop_generation()
                raise AgentTimeoutError(f"agent exceeded {self.timeout:g}s deadline")
            try:
                entry = responses.get(timeout=remaining)
            except queue.Empty:
                self._stop_generation()
                raise AgentTimeoutError(f"agent exceeded {self.timeout:g}s deadline") from None
            if not isinstance(entry, dict) or entry.get("generation") != generation:
                raise self._fail("agent published a response from a replaced generation")
            if entry.get("eof"):
                detail = ""
                if self._failure:
                    detail = f": {self._failure}"
                elif self.stderr_lines:
                    detail = f": {list(self.stderr_lines)[-1]}"
                raise self._fail(f"agent process exited without a response{detail}")
            if entry.get("error"):
                raise self._fail(f"agent protocol error: {entry['error']}")
            line = entry.get("line")
            if not isinstance(line, str):
                raise self._fail("agent published a malformed response entry")
            try:
                payload = json.loads(line)
            except ValueError:
                raise self._fail("agent returned malformed JSON") from None
            if not isinstance(payload, dict):
                raise self._fail("agent response is not a JSON object")
            if payload.get("id") != request_id:
                raise self._fail("agent response id does not match the active request")
            move = payload.get("move")
            if not isinstance(move, dict):
                raise self._fail("agent response lacks a move object")
            source, target = move.get("source"), move.get("target")
            if type(source) is not int or type(target) is not int:
                raise self._fail("agent response move is malformed")
            evidence = None
            if self.expected_identity is not None:
                evidence = self._validate_evidence(payload.get("evidence"), settings, seed)
            self.last_evidence = evidence
            self._failure = None
            return Move(source, target)

    def _validate_evidence(self, evidence, settings: dict, seed: int) -> dict:
        if not isinstance(evidence, dict):
            raise self._fail("agent response lacks per-move execution evidence")
        effective = evidence.get("settings")
        if not isinstance(effective, dict):
            raise self._fail("agent evidence lacks effective settings")
        if effective.get("mode") != settings.get("mode", "fixed-work"):
            raise self._fail("agent evidence mode does not match the tournament mode")
        if effective.get("seed") != seed or evidence.get("seed") != seed:
            raise self._fail("agent evidence seed does not match the request")
        algorithm = evidence.get("algorithm")
        if not isinstance(algorithm, str) or algorithm != self.expected_identity.get("algorithm"):
            raise self._fail("agent evidence algorithm does not match the handshake")
        for field in ("time_limit", "exploration"):
            if field in settings and effective.get(field) != settings.get(field):
                raise self._fail(f"agent evidence {field} does not match the request")
        if algorithm in ("mcts", "neural-mcts-puct-v1"):
            if effective.get("iterations") != settings.get("iterations"):
                raise self._fail("agent evidence iterations do not match the request")
        completed = evidence.get("completed_simulations")
        if completed is not None:
            if type(completed) is not int or completed < 0:
                raise self._fail("agent evidence completed_simulations is invalid")
            iterations = settings.get("iterations")
            if type(iterations) is int and completed > iterations:
                raise self._fail("agent evidence completed work exceeds the requested budget")
        depth = evidence.get("depth")
        if depth is not None and (type(depth) is not int or depth < 0):
            raise self._fail("agent evidence depth is invalid")
        if algorithm in ("mcts", "neural-mcts-puct-v1") and completed is None:
            raise self._fail("MCTS evidence lacks completed simulations")
        if algorithm in ("alpha_beta", "alpha-beta") and depth is None:
            raise self._fail("alpha-beta evidence lacks the completed depth")
        reason = evidence.get("completion_reason")
        if reason not in EVIDENCE_REASONS:
            raise self._fail("agent evidence completion reason is invalid")
        elapsed = evidence.get("elapsed_ms")
        if (
            isinstance(elapsed, bool)
            or not isinstance(elapsed, (int, float))
            or not math.isfinite(float(elapsed))
            or elapsed < 0
        ):
            raise self._fail("agent evidence elapsed_ms is invalid")
        provider = evidence.get("provider")
        requested = evidence.get("provider_requested")
        expected_provider = self.expected_identity.get("provider")
        inferences = evidence.get("neural_inferences")
        gpu_verified = evidence.get("gpu_execution_verified")
        provider_failures = _provider_evidence_failures(
            requested=expected_provider,
            observed=provider,
            reported_requested=requested,
            inferences=inferences,
            gpu_verified=gpu_verified,
            provider_observed=evidence.get("provider_observed"),
        )
        if provider_failures:
            raise self._fail("agent evidence: " + provider_failures[0])
        model_digest = evidence.get("model_sha256")
        expected_digest = self.expected_identity.get("model_sha256")
        if expected_digest is not None and model_digest != expected_digest:
            raise self._fail("agent evidence model identity does not match the handshake")
        return {
            "settings": {
                "mode": effective.get("mode"),
                "iterations": effective.get("iterations"),
                "time_limit": effective.get("time_limit"),
                "exploration": effective.get("exploration"),
                "seed": effective.get("seed"),
                "max_depth": effective.get("max_depth"),
            },
            "seed": seed,
            "algorithm": algorithm,
            "completed_simulations": completed,
            "depth": depth,
            "completion_reason": reason,
            "elapsed_ms": float(elapsed),
            "provider": provider,
            "provider_requested": requested,
            "provider_observed": evidence.get("provider_observed"),
            "neural_inferences": inferences,
            "gpu_execution_verified": gpu_verified is True,
            "model_sha256": model_digest,
        }

    # -- diagnostics -------------------------------------------------------
    def diagnostics(self) -> dict:
        return {
            "generation": self._generation,
            "stderr_bytes": self.stderr_bytes,
            "stderr_lines": len(self.stderr_lines),
            "running": self.process is not None and self.process.poll() is None,
            "cleanup_failures": list(self._cleanup_failures),
            "handshake": self.handshake,
            "process_group": self._process_group,
            "last_process_group": self.last_process_group,
            "request_deadline_s": self.timeout,
            "cleanup_allowance_s": CLEANUP_ALLOWANCE,
        }


class _deadline:
    """Hard per-call deadline using SIGALRM in the main process.

    In-process callables must additionally be bounded by a supported platform.
    This guard restores the previously installed SIGALRM handler and timer, so it
    never overwrites an unrelated process timer.
    """

    def __init__(self, seconds: float):
        if not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or seconds <= 0:
            raise ValueError("agent timeout must be positive")
        self.seconds = float(seconds)
        self.previous = None
        self.previous_timer = None
        self.enabled = False

    def __enter__(self):
        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError("hard agent deadlines require the main thread")
        if not hasattr(signal, "setitimer") or not hasattr(signal, "SIGALRM"):
            raise RuntimeError("hard agent deadlines require SIGALRM support")
        self.previous = signal.getsignal(signal.SIGALRM)
        self.previous_timer = signal.getitimer(signal.ITIMER_REAL)
        signal.signal(signal.SIGALRM, self._alarm)
        signal.setitimer(signal.ITIMER_REAL, self.seconds)
        self.enabled = True
        return self

    def _alarm(self, _signum, _frame):
        raise AgentTimeoutError(f"agent exceeded {self.seconds:g}s deadline")

    def __exit__(self, exc_type, exc, tb):
        if self.enabled:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, self.previous)
            if self.previous_timer and self.previous_timer[0] > 0:
                signal.setitimer(signal.ITIMER_REAL, *self.previous_timer)
        return False


def _move(value: Any) -> Move:
    if isinstance(value, Move):
        return value
    if isinstance(value, dict):
        return Move(value["source"], value["target"])
    raise ValueError("agent returned a malformed move")


def _positive_int(value: Any) -> bool:
    return type(value) is int and value > 0


def _hex64(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _validate_positive_int(value: Any, label: str) -> int:
    if not _positive_int(value):
        raise ValueError(f"{label} must be a positive integer")
    return value


def _validate_opening(opening: Any, ruleset: str, *, max_plies: int) -> dict:
    if not isinstance(opening, dict):
        raise ValueError("opening must be an object")
    name = opening.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError("opening name must be a non-empty string")
    raw_state = opening.get("state")
    if not isinstance(raw_state, dict):
        raise ValueError(f"opening {name!r} has no state object")
    state = State.from_dict(raw_state)
    if state.ruleset != ruleset:
        raise ValueError(f"opening {name!r} ruleset {state.ruleset!r} does not match {ruleset!r}")
    if state.winner is not None:
        raise ValueError(f"opening {name!r} must be non-terminal")
    if state.ply >= max_plies:
        raise ValueError(f"opening {name!r} starts at or beyond the move limit")
    return {
        "name": name,
        "state": raw_state,
        "ruleset": state.ruleset,
        "starting_ply": state.ply,
    }


def _agent_config(labels: tuple[str, str], agent_config: dict | None) -> dict:
    if agent_config is None:
        return {
            "a": {
                "label": labels[0],
                "kind": "scripted",
                "runtime": "in-process",
                "provider": "in-process",
                "model": None,
            },
            "b": {
                "label": labels[1],
                "kind": "scripted",
                "runtime": "in-process",
                "provider": "in-process",
                "model": None,
            },
        }
    if not isinstance(agent_config, dict) or set(agent_config) != {"a", "b"}:
        raise ValueError("agent_config must describe exactly agents a and b")
    return agent_config


def play_game(
    attacker: AGENT,
    defender: AGENT,
    opening: dict,
    *,
    ruleset: str,
    simulations: int,
    time_limit: float,
    seed: int,
    max_plies: int = 200,
    agent_timeout: float = DEFAULT_AGENT_TIMEOUT,
    mode: str = "fixed-work",
    attacker_label: str | None = None,
    defender_label: str | None = None,
) -> dict:
    state = State.from_dict(opening["state"])
    if state.ruleset != ruleset:
        raise ValueError(f"opening ruleset {state.ruleset!r} does not match {ruleset!r}")
    if state.winner is not None:
        raise ValueError("tournament openings must be non-terminal")
    settings = {
        "algorithm": "mcts",
        "iterations": simulations,
        "time_limit": time_limit,
        "exploration": 1.4,
        "seed": seed,
        "mode": mode,
    }
    game = {
        "opening": opening["name"],
        "opening_state": opening["state"],
        "ruleset": ruleset,
        "attacker_agent": None,
        "defender_agent": None,
        "winner": None,
        "reason": None,
        "starting_ply": state.ply,
        "plies": state.ply,
        "move_limit": max_plies,
        "moves": [],
        "completed": False,
        "error": None,
        "seed": seed,
        "settings": {**settings, "mode": mode},
        "mode": mode,
        "agent_timeout_s": float(agent_timeout),
        "execution_evidence": [],
    }
    try:
        while state.winner is None and state.ply < max_plies:
            agent = attacker if state.turn == "attackers" else defender
            if getattr(agent, "enforces_own_deadline", False):
                move = _move(agent(state, settings, seed + state.ply))
            else:
                with _deadline(agent_timeout):
                    move = _move(agent(state, settings, seed + state.ply))
            if move not in legal_moves(state):
                raise ValueError(f"agent selected an illegal move {move.to_dict()}")
            evidence = getattr(agent, "last_evidence", None)
            if isinstance(evidence, dict):
                label = attacker_label if state.turn == "attackers" else defender_label
                game["execution_evidence"].append(
                    {
                        "agent": label,
                        "ply": state.ply,
                        "turn": state.turn,
                        **evidence,
                    }
                )
            state = apply_move(state, move)
            game["moves"].append(move.to_dict())
        game["plies"] = state.ply
        game["winner"] = state.winner
        game["reason"] = state.reason or ("move_limit" if state.winner is None else None)
        game["completed"] = state.winner is not None
    except AgentConfigurationError:
        raise
    except Exception as error:  # noqa: BLE001 - one failed game must not abort the report
        game["error"] = f"{type(error).__name__}: {error}"
        game["plies"] = state.ply
        game["winner"] = None
        game["completed"] = False
        game["reason"] = "execution_error"
    return game


def supports_own_deadline(agent) -> bool:
    return bool(getattr(agent, "enforces_own_deadline", False))


def validate_execution_environment(agent_a, agent_b) -> None:
    if supports_own_deadline(agent_a) and supports_own_deadline(agent_b):
        return
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("tournament callables require the main thread or process agents")
    if not hasattr(signal, "setitimer") or not hasattr(signal, "SIGALRM"):
        raise RuntimeError("tournament callables require SIGALRM or process-isolated agents")


def _declared_deadline(agent) -> float | None:
    if not supports_own_deadline(agent):
        return None
    value = getattr(agent, "timeout", None)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def run_paired_openings(
    agent_a: AGENT,
    agent_b: AGENT,
    openings: list[dict],
    *,
    labels: tuple[str, str],
    ruleset: str,
    simulations: int,
    time_limit: float,
    seed: int = 7,
    max_plies: int = 200,
    agent_timeout: float = DEFAULT_AGENT_TIMEOUT,
    agent_config: dict | None = None,
    evidence_class: str = "structural",
    mode: str = "fixed-work",
) -> dict:
    if evidence_class not in ("structural", "playing-strength"):
        raise ValueError("evidence_class must be structural or playing-strength")
    if mode not in ("fixed-work", "fixed-time"):
        raise ValueError("mode must be fixed-work or fixed-time")
    if not isinstance(labels, tuple) or len(labels) != 2:
        raise ValueError("labels must contain two agent identity strings")
    if any(not isinstance(label, str) or not label for label in labels):
        raise ValueError("agent identity labels must be non-empty strings")
    if labels[0] == labels[1]:
        raise ValueError("agent identity labels must be distinct")
    _validate_positive_int(simulations, "simulations")
    _validate_positive_int(max_plies, "max_plies")
    if isinstance(seed, bool) or type(seed) is not int or seed < 0:
        raise ValueError("seed must be a non-negative integer")
    if not isinstance(time_limit, (int, float)) or isinstance(time_limit, bool) or time_limit <= 0:
        raise ValueError("time_limit must be positive")
    validate_execution_environment(agent_a, agent_b)
    for agent, label in ((agent_a, labels[0]), (agent_b, labels[1])):
        declared = _declared_deadline(agent)
        if declared is not None and abs(declared - float(agent_timeout)) > 1e-9:
            raise ValueError(
                f"agent {label!r} enforces a {declared:g}s deadline but the tournament "
                f"records {float(agent_timeout):g}s"
            )
    if not isinstance(openings, list) or not openings:
        raise ValueError("openings must be a non-empty list")
    validated_openings = [
        _validate_opening(opening, ruleset, max_plies=max_plies) for opening in openings
    ]
    names = [opening["name"] for opening in validated_openings]
    if len(set(names)) != len(names):
        raise ValueError("opening identities must be unique")
    config = _agent_config(labels, agent_config)
    for key in ("a", "b"):
        entry = config.get(key) if isinstance(config.get(key), dict) else {}
        kind = entry.get("kind")
        if not isinstance(kind, str) or mode not in AGENT_MODES.get(kind, ()):
            raise ValueError(f"agent {key} kind {kind!r} does not support {mode} mode")
    settings = {
        "simulations": simulations,
        "iterations": simulations,
        "time_limit": time_limit,
        "exploration": 1.4,
        "seed": seed,
        "max_plies": max_plies,
        "agent_timeout_s": float(agent_timeout),
        "mode": mode,
    }
    games = []
    for pair, opening in enumerate(validated_openings):
        for swap in (False, True):
            a_side = "defenders" if swap else "attackers"
            attacker, defender = (agent_b, agent_a) if swap else (agent_a, agent_b)
            game_seed = seed + pair * 100 + int(swap)
            game = play_game(
                attacker,
                defender,
                opening,
                ruleset=ruleset,
                simulations=simulations,
                time_limit=time_limit,
                seed=game_seed,
                max_plies=max_plies,
                agent_timeout=agent_timeout,
                mode=mode,
                attacker_label=labels[1] if swap else labels[0],
                defender_label=labels[0] if swap else labels[1],
            )
            game.update(
                {
                    "game_id": f"pair-{pair}-{'ba' if swap else 'ab'}",
                    "pair": pair,
                    "agent_a": labels[0],
                    "agent_b": labels[1],
                    "agent_a_side": a_side,
                    "agent_b_side": "attackers" if a_side == "defenders" else "defenders",
                    "attacker_agent": labels[1] if swap else labels[0],
                    "defender_agent": labels[0] if swap else labels[1],
                }
            )
            games.append(game)
    handshakes = {
        "a": getattr(agent_a, "handshake", None),
        "b": getattr(agent_b, "handshake", None),
    }
    manifest = {
        "ruleset": ruleset,
        "agents": {"a": labels[0], "b": labels[1]},
        "agent_config": config,
        "agent_handshakes": handshakes,
        "settings": settings,
        "openings": [
            {
                "pair": index,
                "name": opening["name"],
                "state": opening["state"],
                "starting_ply": opening["starting_ply"],
            }
            for index, opening in enumerate(validated_openings)
        ],
    }
    summary = summarize(games, labels, seed=seed)
    playing_strength = {
        "status": "blocked" if evidence_class == "structural" else "measured",
        "reason": (
            "structural tournament evidence is not playing-strength evidence"
            if evidence_class == "structural"
            else "eligible for statistical review; strength is not inferred by the validator"
        ),
    }
    report = {
        "kind": "browser-analysis-tournament",
        "manifest": manifest,
        "schema": SCHEMA,
        "ruleset": ruleset,
        "agents": {"a": labels[0], "b": labels[1]},
        "agent_config": config,
        "agent_handshakes": handshakes,
        "settings": settings,
        "openings": [entry["name"] for entry in validated_openings],
        "games": games,
        "summary": summary,
        "structural_only": evidence_class == "structural",
        "evidence_class": evidence_class,
        "playing_strength": playing_strength,
        "authoritative": False,
    }
    report["ok"] = not validate_report(report)["failures"]
    return report


def _agent_score(game: dict, agent: str, side: str) -> float | None:
    if not game.get("completed") or game.get("winner") is None:
        return None
    if game["winner"] == "draw":
        return 0.5
    return 1.0 if game["winner"] == side else 0.0


def summarize(games: list[dict], labels: tuple[str, str], *, seed: int = 7) -> dict:
    a, b = labels
    result = {
        "games": len(games),
        "completed": 0,
        "draws": 0,
        "truncated": 0,
        "move_limit_games": 0,
        "execution_errors": 0,
        "attacker_wins": 0,
        "defender_wins": 0,
        "by_agent": {
            a: {"wins": 0, "losses": 0, "draws": 0, "score": 0.0},
            b: {"wins": 0, "losses": 0, "draws": 0, "score": 0.0},
        },
        "by_role": {side: {"wins": 0, "losses": 0, "draws": 0} for side in SIDES},
        "average_plies": None,
        "paired_score": None,
        "paired_score_ci95": None,
        "pairs": 0,
    }
    pair_scores: dict[int, dict[str, list[float]]] = {}
    plies = []
    ordered = sorted(
        (game for game in games if isinstance(game, dict)),
        key=lambda game: (
            game.get("pair") if type(game.get("pair")) is int else 0,
            str(game.get("game_id", "")),
        ),
    )
    for game in ordered:
        if game.get("error"):
            result["execution_errors"] += 1
        if game.get("completed") is not True:
            if game.get("error"):
                continue
            result["truncated"] += 1
            if game.get("reason") == "move_limit":
                result["move_limit_games"] += 1
            continue
        winner = game.get("winner")
        if winner not in ("attackers", "defenders", "draw"):
            continue
        side = game.get("agent_a_side")
        agent_b_side = game.get("agent_b_side")
        if side not in SIDES or agent_b_side not in SIDES:
            continue
        pair = game.get("pair")
        if type(pair) is not int:
            continue
        plies_value = game.get("plies")
        if type(plies_value) is not int:
            continue
        result["completed"] += 1
        plies.append(plies_value)
        if game["winner"] == "draw":
            result["draws"] += 1
        elif winner == "attackers":
            result["attacker_wins"] += 1
        elif winner == "defenders":
            result["defender_wins"] += 1
        score_a = _agent_score(game, a, side)
        if score_a is None:
            continue
        score_b = 1.0 - score_a
        for agent, score, agent_side in (
            (a, score_a, side),
            (b, score_b, agent_b_side),
        ):
            if score == 0.5:
                result["by_agent"][agent]["draws"] += 1
                result["by_role"][agent_side]["draws"] += 1
            elif score == 1.0:
                result["by_agent"][agent]["wins"] += 1
                result["by_role"][agent_side]["wins"] += 1
            else:
                result["by_agent"][agent]["losses"] += 1
                result["by_role"][agent_side]["losses"] += 1
            result["by_agent"][agent]["score"] += score
        pair_scores.setdefault(pair, {"a": [], "b": []})
        pair_scores[pair]["a"].append(score_a)
        pair_scores[pair]["b"].append(score_b)
    if plies:
        result["average_plies"] = sum(plies) / len(plies)
    paired = [
        sum(pair_scores[pair]["a"]) / len(pair_scores[pair]["a"])
        for pair in sorted(pair_scores)
        if len(pair_scores[pair]["a"]) >= 2
    ]
    result["pairs"] = len(paired)
    if paired:
        result["paired_score"] = sum(paired) / len(paired)
        rng = random.Random(seed)
        bootstrap = []
        for _ in range(2000):
            sample = [paired[rng.randrange(len(paired))] for _ in paired]
            bootstrap.append(sum(sample) / len(sample))
        bootstrap.sort()
        result["paired_score_ci95"] = [
            bootstrap[int(0.025 * (len(bootstrap) - 1))],
            bootstrap[int(0.975 * (len(bootstrap) - 1))],
        ]
    return result


def _canonical(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        return f"<invalid:{type(value).__name__}>"


def _replay(opening: State, moves: list[dict]) -> State:
    state = opening
    for raw in moves:
        move = _move(raw)
        if move not in legal_moves(state):
            raise ValueError(f"illegal move {move.to_dict()} in replay")
        state = apply_move(state, move)
    return state


def _validate_agent_config(
    config: Any, agents: dict, ruleset: str, failures: list[str], mode: str = "fixed-work"
) -> None:
    if not isinstance(config, dict) or set(config) != {"a", "b"}:
        failures.append("agent_config must describe both agents")
        return
    for key in ("a", "b"):
        entry = config.get(key)
        if not isinstance(entry, dict):
            failures.append(f"agent_config.{key} must be an object")
            continue
        if entry.get("label") != agents.get(key):
            failures.append(f"agent_config.{key}.label does not match the report")
        kind = entry.get("kind")
        if kind not in AGENT_KINDS:
            failures.append(f"agent_config.{key}.kind is unsupported")
        runtime = entry.get("runtime")
        if not isinstance(runtime, str) or not runtime:
            failures.append(f"agent_config.{key}.runtime is missing")
        provider = entry.get("provider")
        model = entry.get("model")
        if kind == "scripted":
            if model is not None:
                failures.append(f"agent_config.{key} scripted agent cannot declare a model")
            if provider not in (None, "in-process", "cpu"):
                failures.append(f"agent_config.{key} scripted provider is inconsistent")
            continue
        if kind in NEURAL_KINDS:
            if not isinstance(model, dict):
                failures.append(f"agent_config.{key} model identity is missing")
            else:
                for field in ("onnx_sha256", "weights_sha256"):
                    if not _hex64(model.get(field)):
                        failures.append(f"agent_config.{key}.model {field} is malformed")
                if model.get("ruleset") != ruleset:
                    failures.append(f"agent_config.{key}.model ruleset mismatch")
                if not _positive_int(model.get("size")):
                    failures.append(f"agent_config.{key}.model size is invalid")
        elif model is not None:
            failures.append(f"agent_config.{key} {kind} agent cannot declare a model")
        if kind in BROWSER_KINDS and provider not in ("wasm", "webgpu"):
            failures.append(f"agent_config.{key} browser provider is invalid")
        if kind not in BROWSER_KINDS and not isinstance(provider, str):
            failures.append(f"agent_config.{key} provider is invalid")
        algorithm = entry.get("algorithm")
        if kind == "scripted":
            if algorithm not in (None, "scripted"):
                failures.append(f"agent_config.{key} scripted algorithm is inconsistent")
        elif algorithm not in ("mcts", "alpha_beta"):
            failures.append(f"agent_config.{key} algorithm is missing or unsupported")
        if algorithm == "alpha_beta" and not _positive_int(entry.get("depth")):
            failures.append(f"agent_config.{key} alpha-beta depth is missing")
        if not isinstance(kind, str) or mode not in AGENT_MODES.get(kind, ()):
            failures.append(f"agent_config.{key} does not support the {mode} execution mode")


def _validate_agent_handshakes(config, handshakes, failures: list[str]) -> None:
    if not isinstance(config, dict):
        return
    both_scripted = all(
        isinstance(config.get(key), dict) and config[key].get("kind") == "scripted"
        for key in ("a", "b")
    )
    if handshakes is None and both_scripted:
        return
    if not isinstance(handshakes, dict) or set(handshakes) != {"a", "b"}:
        failures.append("agent_handshakes must describe both agents")
        return
    for key in ("a", "b"):
        entry = config.get(key) if isinstance(config.get(key), dict) else {}
        kind = entry.get("kind")
        handshake = handshakes.get(key)
        if kind == "scripted":
            if handshake is not None and not isinstance(handshake, dict):
                failures.append(f"agent_handshakes.{key} is malformed")
            elif handshake is not None:
                if handshake.get("kind") != "scripted":
                    failures.append(f"agent_handshakes.{key} contradicts the scripted kind")
                if handshake.get("protocol") != PROTOCOL_VERSION:
                    failures.append(f"agent_handshakes.{key} protocol version is unsupported")
            continue
        if not isinstance(handshake, dict):
            failures.append(f"agent_handshakes.{key} is missing")
            continue
        if handshake.get("protocol") != PROTOCOL_VERSION:
            failures.append(f"agent_handshakes.{key} protocol version is unsupported")
        for field in ("kind", "runtime", "provider", "algorithm"):
            if handshake.get(field) != entry.get(field):
                failures.append(f"agent_handshakes.{key} {field} contradicts the configuration")
        model = entry.get("model")
        if isinstance(model, dict):
            if handshake.get("model_sha256") != model.get("onnx_sha256"):
                failures.append(
                    f"agent_handshakes.{key} model onnx_sha256 contradicts the configuration"
                )
            if handshake.get("weights_sha256") != model.get("weights_sha256"):
                failures.append(
                    f"agent_handshakes.{key} model weights_sha256 contradicts the configuration"
                )
            for field in ("ruleset", "size"):
                if handshake.get(field) != model.get(field):
                    failures.append(
                        f"agent_handshakes.{key} model {field} contradicts the configuration"
                    )
        engine = handshake.get("engine_sha256")
        if engine is not None and not _hex64(engine):
            failures.append(f"agent_handshakes.{key} engine identity is malformed")


def _validate_execution_evidence(
    game: dict,
    config: dict,
    settings: dict,
    mode: str,
    failures: list[str],
) -> None:
    label = game.get("game_id")
    entry_a = config.get("a") if isinstance(config.get("a"), dict) else {}
    entry_b = config.get("b") if isinstance(config.get("b"), dict) else {}
    kind_a = entry_a.get("kind")
    kind_b = entry_b.get("kind")
    if kind_a == "scripted" and kind_b == "scripted":
        return
    evidence = game.get("execution_evidence")
    if not isinstance(evidence, list):
        failures.append(f"{label}: per-move execution evidence is missing")
        return
    moves = game.get("moves")
    if not isinstance(moves, list):
        return
    opening_state = game.get("opening_state")
    if not isinstance(opening_state, dict):
        return
    try:
        state = State.from_dict(opening_state)
    except (ValueError, KeyError, TypeError):
        return
    starting_ply = state.ply
    turns = []
    for move in moves:
        turns.append(state.turn)
        try:
            state = apply_move(state, _move(move))
        except (ValueError, KeyError, TypeError):
            return
    by_ply = {}
    for entry in evidence:
        if not isinstance(entry, dict):
            failures.append(f"{label}: execution evidence entry is not an object")
            continue
        ply = entry.get("ply")
        if type(ply) is not int:
            failures.append(f"{label}: execution evidence ply is invalid")
            continue
        if ply in by_ply:
            failures.append(f"{label}: execution evidence ply is duplicated")
            continue
        if ply < starting_ply or ply >= starting_ply + len(turns):
            failures.append(f"{label}: execution evidence ply is out of range")
            continue
        by_ply[ply] = entry
    game_seed = game.get("seed")
    for relative_ply, turn in enumerate(turns):
        ply = starting_ply + relative_ply
        side = "a" if turn == "attackers" else "b"
        # Determine which declared agent played this turn from the role mapping.
        if game.get("agent_a_side") == turn:
            side = "a"
        elif game.get("agent_b_side") == turn:
            side = "b"
        entry = config.get(side) if isinstance(config.get(side), dict) else {}
        if entry.get("kind") == "scripted":
            continue
        observation = by_ply.get(ply)
        if observation is None:
            failures.append(f"{label}: missing execution evidence for ply {ply}")
            continue
        if observation.get("agent") != (game.get(f"agent_{side}")):
            failures.append(f"{label}: execution evidence agent identity mismatch")
        if type(game_seed) is not int:
            failures.append(f"{label}: game seed is invalid for execution evidence")
            break
        if observation.get("seed") != game_seed + ply:
            failures.append(f"{label}: execution evidence seed does not match the move seed")
        if observation.get("algorithm") != entry.get("algorithm"):
            failures.append(f"{label}: execution evidence algorithm mismatch")
        provider_failures = _provider_evidence_failures(
            requested=entry.get("provider"),
            observed=observation.get("provider"),
            reported_requested=observation.get("provider_requested"),
            inferences=observation.get("neural_inferences"),
            gpu_verified=observation.get("gpu_execution_verified"),
            provider_observed=observation.get("provider_observed"),
        )
        failures.extend(f"{label}: execution evidence {message}" for message in provider_failures)
        model = entry.get("model")
        if isinstance(model, dict):
            if observation.get("model_sha256") != model.get("onnx_sha256"):
                failures.append(f"{label}: execution evidence model identity mismatch")
        effective = observation.get("settings")
        if not isinstance(effective, dict):
            failures.append(f"{label}: execution evidence settings are missing")
            continue
        if effective.get("mode") != mode:
            failures.append(f"{label}: execution evidence mode mismatch")
        if effective.get("seed") != game_seed + ply:
            failures.append(f"{label}: execution evidence effective seed mismatch")
        if effective.get("time_limit") != settings.get("time_limit"):
            failures.append(f"{label}: execution evidence time_limit mismatch")
        if effective.get("exploration") != settings.get("exploration"):
            failures.append(f"{label}: execution evidence exploration mismatch")
        completed = observation.get("completed_simulations")
        reason = observation.get("completion_reason")
        algorithm = entry.get("algorithm")
        if algorithm == "alpha_beta":
            if reason not in ("completed", "deadline", "immediate_win", "terminal"):
                failures.append(f"{label}: alpha-beta completion reason is invalid")
            max_depth = effective.get("max_depth")
            recorded_depth = observation.get("depth")
            if max_depth != entry.get("depth"):
                failures.append(f"{label}: execution evidence max depth mismatch")
            if type(recorded_depth) is not int or recorded_depth < 0:
                failures.append(f"{label}: execution evidence completed depth is invalid")
            elif _positive_int(entry.get("depth")) and recorded_depth > entry["depth"]:
                failures.append(f"{label}: execution evidence depth exceeds the configured cap")
            elif mode == "fixed-work":
                if reason not in ("immediate_win", "terminal"):
                    if reason != "completed":
                        failures.append(
                            f"{label}: fixed-work alpha-beta did not reach its depth limit"
                        )
                    elif recorded_depth != entry.get("depth"):
                        failures.append(f"{label}: fixed-work alpha-beta completed unequal depth")
            elif mode == "fixed-time":
                if type(recorded_depth) is not int or recorded_depth < 1:
                    failures.append(f"{label}: fixed-time alpha-beta completed no usable depth")
                if reason == "deadline" and recorded_depth == entry.get("depth"):
                    failures.append(
                        f"{label}: deadline reason contradicts a completed maximum depth"
                    )
                if reason == "completed" and recorded_depth != entry.get("depth"):
                    failures.append(f"{label}: completed reason contradicts the recorded depth")
        else:
            if reason not in ("simulations", "deadline", "immediate_win", "terminal"):
                failures.append(f"{label}: MCTS completion reason is invalid")
            if effective.get("iterations") != settings.get("iterations"):
                failures.append(f"{label}: execution evidence iterations mismatch")
            if type(completed) is not int or completed < 0:
                failures.append(f"{label}: execution evidence completed work is unknown")
            elif completed > settings["iterations"]:
                failures.append(f"{label}: execution evidence exceeds the simulation budget")
            elif mode == "fixed-work":
                if reason not in ("immediate_win", "terminal"):
                    if reason != "simulations":
                        failures.append(
                            f"{label}: fixed-work MCTS did not complete its simulation budget"
                        )
                    elif completed != settings["iterations"]:
                        failures.append(f"{label}: fixed-work MCTS completed unequal work")
            elif mode == "fixed-time":
                if reason == "simulations" and completed != settings["iterations"]:
                    failures.append(f"{label}: simulation-era reason contradicts completed work")
                if reason == "deadline" and completed > settings["iterations"]:
                    failures.append(f"{label}: deadline completion exceeds the ceiling")
        elapsed = observation.get("elapsed_ms")
        if (
            isinstance(elapsed, bool)
            or not isinstance(elapsed, (int, float))
            or not math.isfinite(float(elapsed))
            or elapsed < 0
        ):
            failures.append(f"{label}: execution evidence elapsed time is invalid")
        elif mode == "fixed-time":
            if reason not in ("deadline", "simulations", "completed", "immediate_win", "terminal"):
                failures.append(f"{label}: fixed-time agent used an unsupported completion reason")
            if float(elapsed) > settings["time_limit"] * 1000.0 + 2000.0:
                failures.append(f"{label}: fixed-time agent exceeded its time budget")


def validate_report(report: dict) -> dict:
    failures: list[str] = []

    def fail(message: str) -> None:
        failures.append(message)

    if not isinstance(report, dict):
        return {"ok": False, "failures": ["report must be an object"]}
    if report.get("kind") != "browser-analysis-tournament" or report.get("schema") not in (
        SCHEMA,
        SCHEMA - 1,
    ):
        fail("unsupported tournament report schema")
    openings = report.get("openings")
    games = report.get("games")
    if not isinstance(openings, list) or not openings:
        fail("openings must be a non-empty list")
        openings = []
    if not isinstance(games, list):
        fail("games must be a list")
        games = []
    agents = report.get("agents")
    if not isinstance(agents, dict):
        fail("agents must be an object")
        agents = {}
    for key in ("a", "b"):
        if not isinstance(agents.get(key), str) or not agents.get(key):
            fail(f"agent {key.upper()} identity is missing")
    if agents.get("a") == agents.get("b"):
        fail("agent labels must be distinct")
    manifest = report.get("manifest")
    if not isinstance(manifest, dict):
        fail("manifest must be an object")
        manifest = {}
    else:
        if manifest.get("ruleset") != report.get("ruleset"):
            fail("manifest ruleset does not match report")
        if manifest.get("agents") != agents:
            fail("manifest agent identities do not match report")
        if _canonical(manifest.get("agent_config")) != _canonical(report.get("agent_config")):
            fail("manifest agent configuration does not match report")
        if _canonical(manifest.get("agent_handshakes")) != _canonical(
            report.get("agent_handshakes")
        ):
            fail("manifest agent handshakes do not match report")
        if _canonical(manifest.get("settings")) != _canonical(report.get("settings")):
            fail("manifest settings do not match report")
        manifest_openings = manifest.get("openings")
        if not isinstance(manifest_openings, list):
            fail("manifest openings must be a list")
            manifest_openings = []
        manifest_names = []
        for entry in manifest_openings:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            if not isinstance(name, str) or not name:
                fail("manifest opening name is malformed")
                continue
            manifest_names.append(name)
        if len(set(manifest_names)) != len(manifest_names):
            fail("manifest opening identities are duplicated")
        if manifest_names != openings:
            fail("report openings do not match manifest order")
        for index, entry in enumerate(manifest_openings):
            if not isinstance(entry, dict) or entry.get("pair") != index:
                fail("manifest opening pair index is inconsistent")
                continue
            if not isinstance(entry.get("name"), str) or not entry["name"]:
                fail("manifest opening name is malformed")
            raw_state = entry.get("state")
            if not isinstance(raw_state, dict):
                fail("manifest opening state is missing")
                continue
            try:
                state = State.from_dict(raw_state)
            except (ValueError, KeyError, TypeError) as error:
                fail(f"manifest opening state is invalid: {error}")
                continue
            if state.ruleset != report.get("ruleset"):
                fail("manifest opening ruleset does not match the report")
            if state.winner is not None:
                fail("manifest openings must be non-terminal")
            if entry.get("starting_ply") != state.ply:
                fail("manifest opening starting ply is inconsistent")
    settings = report.get("settings")
    if not isinstance(settings, dict):
        fail("settings must be an object")
        settings = {}
    for field in ("simulations", "iterations", "max_plies"):
        if not _positive_int(settings.get(field)):
            fail(f"settings.{field} must be a positive integer")
    for field in ("time_limit", "exploration", "agent_timeout_s"):
        value = settings.get(field)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
            fail(f"settings.{field} must be positive")
    if settings.get("simulations") != settings.get("iterations"):
        fail("settings.simulations and settings.iterations must agree")
    mode = settings.get("mode", "fixed-work")
    if mode not in ("fixed-work", "fixed-time"):
        fail("settings.mode must be fixed-work or fixed-time")
    seed = settings.get("seed")
    if type(seed) is not int or seed < 0:
        fail("settings.seed must be a non-negative integer")
        seed = 0
    max_plies = settings.get("max_plies")
    if not _positive_int(max_plies):
        max_plies = 0
    ruleset = report.get("ruleset")
    _validate_agent_config(
        report.get("agent_config"),
        agents,
        ruleset if isinstance(ruleset, str) else "",
        failures,
        mode,
    )
    _validate_agent_handshakes(report.get("agent_config"), report.get("agent_handshakes"), failures)
    manifest_openings = (
        manifest.get("openings") if isinstance(manifest.get("openings"), list) else []
    )
    opening_by_name = {
        entry.get("name"): entry
        for entry in manifest_openings
        if isinstance(entry, dict) and isinstance(entry.get("name"), str)
    }
    opening_states: dict[str, dict] = {}
    for game in games:
        if not isinstance(game, dict):
            continue
        name = game.get("opening")
        raw = game.get("opening_state")
        if not isinstance(name, str) or not name:
            fail(f"{game.get('game_id')}: opening identity is malformed")
            continue
        if not isinstance(raw, dict):
            fail(f"{game.get('game_id')}: opening_state must be an object")
            continue
        try:
            current = State.from_dict(raw)
        except (ValueError, KeyError, TypeError) as error:
            fail(f"{game.get('game_id')}: invalid opening_state: {error}")
            continue
        if current.winner is not None:
            fail(f"{game.get('game_id')}: opening state is terminal")
        if name in opening_states and opening_states[name] != current.to_dict():
            fail(f"{game.get('game_id')}: paired opening state/history differs")
        opening_states[name] = current.to_dict()
    ids: list[Any] = []
    for game in games:
        if not isinstance(game, dict):
            fail("game entry must be an object")
            continue
        game_id_value = game.get("game_id")
        game_id = game_id_value if isinstance(game_id_value, str) else None
        ids.append(game_id)
        label = game_id or "<missing game id>"
        if game_id is None:
            fail("game identifier must be a string")
        opening_name = game.get("opening")
        if not isinstance(opening_name, str) or opening_name not in openings:
            fail(f"{label}: unknown opening {opening_name!r}")
            continue
        if game.get("ruleset") != ruleset:
            fail(f"{label}: ruleset mismatch")
        manifest_entry = opening_by_name.get(opening_name)
        if manifest_entry is None:
            fail(f"{label}: opening is absent from manifest")
        elif _canonical(manifest_entry.get("state")) != _canonical(game.get("opening_state")):
            fail(f"{label}: opening state/history differs from manifest")
        pair = game.get("pair")
        if type(pair) is not int or pair < 0 or pair >= len(openings):
            fail(f"{label}: pair index is invalid")
            pair = None
        elif manifest_entry is not None and manifest_entry.get("pair") != pair:
            fail(f"{label}: pair index contradicts the manifest opening")
        game_settings = game.get("settings")
        if not isinstance(game_settings, dict):
            fail(f"{label}: game settings are missing")
            game_settings = {}
        else:
            if game_settings.get("algorithm") != "mcts":
                fail(f"{label}: algorithm identity is missing or unexpected")
            for field in ("iterations", "time_limit", "exploration", "mode"):
                if game_settings.get(field) != settings.get(field):
                    fail(f"{label}: {field} differs from manifest")
            game_seed = game_settings.get("seed")
            if type(game_seed) is not int:
                fail(f"{label}: game seed is invalid")
            elif pair is not None:
                leg = (game_id or "").split("-")[-1]
                expected_seed = seed + pair * 100 + (1 if leg == "ba" else 0)
                if game_seed != expected_seed:
                    fail(f"{label}: game seed does not match the scheduled seed")
                if game.get("seed") != expected_seed:
                    fail(f"{label}: recorded seed does not match the scheduled seed")
        if game.get("agent_timeout_s") != settings.get("agent_timeout_s"):
            fail(f"{label}: adapter deadline does not match the recorded tournament setting")
        if game.get("agent_a") != agents.get("a"):
            fail(f"{label}: agent A identity mismatch")
        if game.get("agent_b") != agents.get("b"):
            fail(f"{label}: agent B identity mismatch")
        a_side_value = game.get("agent_a_side")
        b_side_value = game.get("agent_b_side")
        if (
            not isinstance(a_side_value, str)
            or not isinstance(b_side_value, str)
            or a_side_value not in SIDES
            or b_side_value not in SIDES
            or a_side_value == b_side_value
        ):
            fail(f"{label}: role assignment is invalid")
            a_side = None
        else:
            a_side = a_side_value
        expected_attacker = agents.get("b") if a_side == "defenders" else agents.get("a")
        expected_defender = agents.get("a") if a_side == "defenders" else agents.get("b")
        if a_side is not None:
            if game.get("attacker_agent") != expected_attacker:
                fail(f"{label}: attacker assignment contradicts agent roles")
            if game.get("defender_agent") != expected_defender:
                fail(f"{label}: defender assignment contradicts agent roles")
        moves = game.get("moves")
        if not isinstance(moves, list):
            fail(f"{label}: moves must be a list")
            continue
        if game_id is not None:
            parts = game_id.split("-")
            if (
                len(parts) != 3
                or parts[0] != "pair"
                or not parts[1].isdigit()
                or parts[2] not in ("ab", "ba")
            ):
                fail(f"{label}: malformed game identifier")
            elif pair is not None and pair != int(parts[1]):
                fail(f"{label}: pair index contradicts identifier")
            elif a_side is not None:
                if parts[2] == "ba" and a_side != "defenders":
                    fail(f"{label}: swapped leg role assignment is inconsistent")
                if parts[2] == "ab" and a_side != "attackers":
                    fail(f"{label}: original leg role assignment is inconsistent")
        starting_ply = game.get("starting_ply")
        if type(starting_ply) is not int or starting_ply < 0:
            fail(f"{label}: starting_ply is invalid")
            continue
        if opening_name in opening_states:
            expected_state = opening_states[opening_name]
            if expected_state.get("ply") != starting_ply:
                fail(f"{label}: starting ply does not match the opening state")
            raw_opening_state = game.get("opening_state")
            actual_history = (
                raw_opening_state.get("history") if isinstance(raw_opening_state, dict) else None
            )
            if expected_state.get("history") != actual_history:
                fail(f"{label}: opening repetition history differs")
        if game.get("move_limit") != max_plies:
            fail(f"{label}: move limit differs from the manifest")
        plies = game.get("plies")
        if type(plies) is not int or plies < starting_ply:
            fail(f"{label}: absolute ply count is invalid")
            continue
        if plies != starting_ply + len(moves):
            fail(f"{label}: absolute ply count does not match initial ply and moves")
        if max_plies and plies > max_plies:
            fail(f"{label}: game exceeds its recorded move limit")
        try:
            state = _replay(State.from_dict(game["opening_state"]), moves)
        except (ValueError, KeyError, TypeError) as error:
            fail(f"{label}: {error}")
            continue
        error_value = game.get("error")
        reason_value = game.get("reason")
        if reason_value is not None and not isinstance(reason_value, str):
            fail(f"{label}: completion reason must be a string")
            reason_value = None
        if error_value is not None:
            if not isinstance(error_value, str) or not error_value:
                fail(f"{label}: execution error must be a non-empty string")
            if game.get("completed") or game.get("winner") is not None:
                fail(f"{label}: error game claims completion")
            if reason_value != "execution_error":
                fail(f"{label}: execution error game has an inconsistent reason")
            if state.winner is not None:
                fail(f"{label}: execution error prefix reached a terminal state")
            continue
        if reason_value == "execution_error":
            fail(f"{label}: execution error reason without an error")
        if game.get("completed"):
            if game.get("winner") not in ("attackers", "defenders", "draw"):
                fail(f"{label}: completed game has an invalid winner")
            if state.winner != game.get("winner"):
                fail(f"{label}: winner does not match replay")
            if state.winner is None:
                fail(f"{label}: completed game replay is not terminal")
            elif reason_value != (state.reason or "terminal"):
                fail(f"{label}: terminal reason does not match replay")
            if reason_value not in TERMINAL_REASONS:
                fail(f"{label}: completion reason is not a terminal reason")
        else:
            if state.winner is not None:
                fail(f"{label}: uncompleted game replay is terminal")
            if game.get("winner") is not None:
                fail(f"{label}: uncompleted game claims a winner")
            if reason_value != "move_limit":
                fail(f"{label}: uncompleted game reason is not a move limit")
            if max_plies and plies != max_plies:
                fail(f"{label}: move-limited game did not reach the recorded move limit")
    if len(set(ids)) != len(ids):
        fail("duplicate game identifiers")
    expected_pairs = {
        f"pair-{index}-{leg}" for index in range(len(openings)) for leg in ("ab", "ba")
    }
    if set(ids) != expected_pairs:
        fail("game identifiers do not cover every opening pair and role swap exactly once")
    for game in games:
        if isinstance(game, dict):
            _validate_execution_evidence(
                game,
                report.get("agent_config") if isinstance(report.get("agent_config"), dict) else {},
                settings,
                mode,
                failures,
            )
    summary = report.get("summary")
    if not isinstance(summary, dict):
        fail("summary must be an object")
        summary = {}
    if summary.get("games") != len(games):
        fail("summary game count does not match games")
    completed = sum(1 for game in games if isinstance(game, dict) and game.get("completed"))
    if summary.get("completed") != completed:
        fail("summary completed count does not match games")
    labels = (agents.get("a"), agents.get("b"))
    if all(isinstance(label, str) for label in labels):
        recomputed = summarize(games, labels, seed=seed)
        if _canonical(recomputed) != _canonical(summary):
            fail("summary statistics do not match independently recomputed results")
    structural_only = report.get("structural_only")
    playing_strength = report.get("playing_strength")
    if not isinstance(playing_strength, dict):
        fail("playing_strength must be an object")
        playing_strength = {}
    strength_acceptance = False
    if structural_only is True:
        if playing_strength.get("status") != "blocked":
            fail("structural tournament evidence must mark playing strength blocked")
    elif structural_only is False:
        if report.get("evidence_class") != "playing-strength":
            fail("non-structural evidence must declare the playing-strength class")
        config = report.get("agent_config")
        if isinstance(config, dict):
            for key in ("a", "b"):
                entry = config.get(key)
                if isinstance(entry, dict) and entry.get("kind") == "scripted":
                    fail("playing-strength evidence requires real agent configurations")
        if playing_strength.get("status") not in ("measured", "blocked"):
            fail("playing strength status is invalid")
        if summary.get("execution_errors", 0) or summary.get("truncated", 0):
            fail("playing-strength evidence cannot include failed or truncated games")
        if summary.get("pairs", 0) < MIN_STRENGTH_PAIRS:
            fail(f"playing-strength evidence needs at least {MIN_STRENGTH_PAIRS} complete pairs")
        strength_acceptance = (
            not failures
            and playing_strength.get("status") == "measured"
            and not summary.get("execution_errors", 0)
            and not summary.get("truncated", 0)
            and summary.get("pairs", 0) >= MIN_STRENGTH_PAIRS
        )
    else:
        fail("structural_only must be an explicit boolean")
    return {
        "kind": "browser-analysis-tournament-validation",
        "ok": not failures,
        "failures": failures,
        "games": len(games),
        "pairs": len(openings),
        "structural_ok": not failures,
        "playing_strength_acceptance": strength_acceptance,
    }


def _read_json(path: Path) -> dict:
    try:
        raw = path.read_text()
    except OSError as error:
        raise ValueError(f"cannot read report {path.name}: {error}") from None
    try:
        value = json.loads(raw)
    except (ValueError, TypeError) as error:
        raise ValueError(f"report {path.name} is not valid JSON: {error}") from None
    if not isinstance(value, dict):
        raise ValueError(f"report {path.name} must be an object")
    return value


def _load_openings(path: Path | None, ruleset: str, limit: int | None) -> list[dict]:
    if path is None:
        from fixtures import jarl_positions

        entries = [
            entry
            for entry in jarl_positions()
            if entry["ruleset"] == ruleset
            and entry["kind"] in ("opening", "midgame", "tactical", "endgame")
        ]
        if limit is not None:
            entries = entries[:limit]
        return [{"name": entry["name"], "state": entry["state"]} for entry in entries]
    document = _read_json(path)
    raw = document.get("openings") if isinstance(document.get("openings"), list) else document
    if not isinstance(raw, list) or not raw:
        raise ValueError("openings file must contain a non-empty list")
    entries = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise ValueError("opening entry must be an object")
        entries.append({"name": entry.get("name"), "state": entry.get("state")})
    if limit is not None:
        entries = entries[:limit]
    return entries


def _expected_identity(entry: dict) -> dict:
    if not isinstance(entry, dict):
        raise ValueError("agent configuration must be an object")
    kind = entry.get("kind")
    algorithm = entry.get("algorithm")
    if kind == "scripted":
        algorithm = algorithm or "scripted"
    if algorithm not in ("scripted", "mcts", "alpha_beta"):
        raise ValueError("non-scripted agent configuration must declare a supported algorithm")
    identity = {
        "kind": kind,
        "runtime": entry.get("runtime"),
        "provider": entry.get("provider"),
        "algorithm": algorithm,
        "modes": list(AGENT_MODES.get(kind, ())),
    }
    if algorithm == "alpha_beta":
        if not _positive_int(entry.get("depth")):
            raise ValueError("alpha-beta agent configuration must declare its depth")
        identity["depth"] = entry["depth"]
    model = entry.get("model")
    if isinstance(model, dict):
        identity.update(
            {
                "model_sha256": model.get("onnx_sha256"),
                "weights_sha256": model.get("weights_sha256"),
                "ruleset": model.get("ruleset"),
                "size": model.get("size"),
            }
        )
    return identity


def _agent_from_command(
    agent_command: list[str],
    timeout: float,
    cwd,
    *,
    expected_identity: dict | None = None,
) -> JsonProcessAgent:
    return JsonProcessAgent(
        agent_command,
        timeout=timeout,
        cwd=cwd,
        expected_identity=expected_identity,
    )


def _run_cli(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    if not args.agent_a_command or not args.agent_b_command:
        parser.error("--run requires --agent-a-command and --agent-b-command")
    if not args.agent_a_config or not args.agent_b_config:
        parser.error("--run requires --agent-a-config and --agent-b-config")
    try:
        config = {
            "a": _read_json(args.agent_a_config),
            "b": _read_json(args.agent_b_config),
        }
        openings = _load_openings(args.openings_file, args.ruleset, args.limit)
        labels = (config["a"].get("label"), config["b"].get("label"))
        for key in ("a", "b"):
            entry = config[key] if isinstance(config[key], dict) else {}
            kind = entry.get("kind")
            if not isinstance(kind, str) or args.mode not in AGENT_MODES.get(kind, ()):
                raise ValueError(f"agent {key} kind {kind!r} does not support {args.mode} mode")
        deadline = float(args.agent_timeout)
        with (
            _agent_from_command(
                shlex.split(args.agent_a_command),
                deadline,
                args.cwd,
                expected_identity=_expected_identity(config["a"]),
            ) as agent_a,
            _agent_from_command(
                shlex.split(args.agent_b_command),
                deadline,
                args.cwd,
                expected_identity=_expected_identity(config["b"]),
            ) as agent_b,
        ):
            report = run_paired_openings(
                agent_a,
                agent_b,
                openings,
                labels=labels,
                ruleset=args.ruleset,
                simulations=args.simulations,
                time_limit=args.time_limit,
                seed=args.seed,
                max_plies=args.max_plies,
                agent_timeout=deadline,
                agent_config=config,
                evidence_class=args.evidence_class,
                mode=args.mode,
            )
    except (ValueError, OSError, AgentProtocolError, AgentTimeoutError) as error:
        print(json.dumps({"ok": False, "failures": [str(error)]}, indent=2))
        return 1
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n")
    validated = validate_report(report)
    summary = {
        "ok": validated["ok"],
        "games": report["summary"]["games"],
        "completed": report["summary"]["completed"],
        "execution_errors": report["summary"]["execution_errors"],
        "structural_only": report["structural_only"],
        "playing_strength_acceptance": validated["playing_strength_acceptance"],
        "failures": validated["failures"],
    }
    print(json.dumps(summary, indent=2))
    return 0 if validated["ok"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path, nargs="?")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--run", action="store_true", help="collect a new tournament report")
    parser.add_argument("--ruleset", default="brandubh")
    parser.add_argument("--openings-file", type=Path)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--simulations", type=int, default=8)
    parser.add_argument("--time-limit", type=float, default=1.0)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--max-plies", type=int, default=60)
    parser.add_argument("--agent-timeout", type=float, default=DEFAULT_AGENT_TIMEOUT)
    parser.add_argument("--agent-a-command")
    parser.add_argument("--agent-b-command")
    parser.add_argument("--agent-a-config", type=Path)
    parser.add_argument("--agent-b-config", type=Path)
    parser.add_argument("--cwd", type=Path)
    parser.add_argument("--mode", choices=("fixed-work", "fixed-time"), default="fixed-work")
    parser.add_argument(
        "--evidence-class", choices=("structural", "playing-strength"), default="structural"
    )
    args = parser.parse_args(argv)
    if args.run:
        return _run_cli(parser, args)
    if args.report is None:
        parser.error("a report path is required unless --run is used")
    try:
        report = _read_json(args.report)
    except ValueError as error:
        result = {"ok": False, "failures": [str(error)]}
        if args.output:
            args.output.write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result, indent=2))
        return 1
    validated = validate_report(report)
    if args.output:
        args.output.write_text(json.dumps(validated, indent=2) + "\n")
    print(json.dumps(validated, indent=2))
    return 0 if validated["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
