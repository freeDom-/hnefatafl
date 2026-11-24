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
import queue
import random
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
    "native-neural",
    "browser-neural",
    "browser-jarl",
)
NEURAL_KINDS = ("native-neural", "browser-neural")
BROWSER_KINDS = ("browser-neural", "browser-jarl")


class AgentTimeoutError(TimeoutError):
    """Raised when an agent exceeds its hard per-move execution deadline."""


class AgentProtocolError(RuntimeError):
    """Raised when a process agent violates the line protocol or lifecycle."""


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
        self._failure: str | None = None

    # -- lifecycle ---------------------------------------------------------
    def __enter__(self) -> "JsonProcessAgent":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.close()
        return False

    def close(self) -> None:
        self._closed = True
        self._stop_generation()

    def _stop_generation(self) -> None:
        process = self.process
        self.process = None
        threads = self._threads
        self._threads = []
        if process is not None:
            try:
                if process.stdin is not None:
                    process.stdin.close()
            except (OSError, ValueError):
                pass
            if process.poll() is None:
                try:
                    process.terminate()
                except OSError:
                    pass
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    try:
                        process.kill()
                    except OSError:
                        pass
                    try:
                        process.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        pass
            for stream in (process.stdout, process.stderr):
                try:
                    if stream is not None:
                        stream.close()
                except (OSError, ValueError):
                    pass
        for thread in threads:
            thread.join(timeout=1)

    def _record_stderr(self, line: str) -> None:
        encoded = len(line.encode("utf-8", "replace"))
        self.stderr_lines.append(line.rstrip("\n"))
        self.stderr_bytes += encoded
        while self.stderr_bytes > self.max_stderr_bytes and self.stderr_lines:
            removed = self.stderr_lines.popleft()
            self.stderr_bytes -= len(removed.encode("utf-8", "replace")) + 1

    def _start(self) -> None:
        self._stop_generation()
        self._generation += 1
        generation = self._generation
        self.responses = queue.Queue(maxsize=64)
        responses = self.responses
        self._failure = None
        try:
            process = subprocess.Popen(
                self.command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                cwd=self.cwd,
            )
        except OSError as error:
            raise AgentProtocolError(f"cannot start agent process: {error}") from None
        self.process = process

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
        writer.start()
        remaining = deadline - time.monotonic()
        if remaining > 0:
            writer.join(remaining)
        if writer.is_alive():
            self._stop_generation()
            raise AgentTimeoutError(f"agent write exceeded {self.timeout:g}s deadline")
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
            self._failure = None
            return Move(source, target)

    # -- diagnostics -------------------------------------------------------
    def diagnostics(self) -> dict:
        return {
            "generation": self._generation,
            "stderr_bytes": self.stderr_bytes,
            "stderr_lines": len(self.stderr_lines),
            "running": self.process is not None and self.process.poll() is None,
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
        "settings": dict(settings),
        "agent_timeout_s": float(agent_timeout),
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
            state = apply_move(state, move)
            game["moves"].append(move.to_dict())
        game["plies"] = state.ply
        game["winner"] = state.winner
        game["reason"] = state.reason or ("move_limit" if state.winner is None else None)
        game["completed"] = state.winner is not None
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
) -> dict:
    if evidence_class not in ("structural", "playing-strength"):
        raise ValueError("evidence_class must be structural or playing-strength")
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
    settings = {
        "simulations": simulations,
        "iterations": simulations,
        "time_limit": time_limit,
        "exploration": 1.4,
        "seed": seed,
        "max_plies": max_plies,
        "agent_timeout_s": float(agent_timeout),
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
    manifest = {
        "ruleset": ruleset,
        "agents": {"a": labels[0], "b": labels[1]},
        "agent_config": config,
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


def _validate_agent_config(config: Any, agents: dict, ruleset: str, failures: list[str]) -> None:
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
            if provider not in (None, "in-process"):
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
    seed = settings.get("seed")
    if type(seed) is not int or seed < 0:
        fail("settings.seed must be a non-negative integer")
        seed = 0
    max_plies = settings.get("max_plies")
    if not _positive_int(max_plies):
        max_plies = 0
    ruleset = report.get("ruleset")
    _validate_agent_config(
        report.get("agent_config"), agents, ruleset if isinstance(ruleset, str) else "", failures
    )
    manifest_openings = manifest.get("openings") if isinstance(manifest.get("openings"), list) else []
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
            for field in ("iterations", "time_limit", "exploration"):
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
                raw_opening_state.get("history")
                if isinstance(raw_opening_state, dict)
                else None
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
            playing_strength.get("status") == "measured"
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


def _agent_from_command(agent_command: list[str], timeout: float, cwd) -> JsonProcessAgent:
    return JsonProcessAgent(agent_command, timeout=timeout, cwd=cwd)


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
        deadline = float(args.agent_timeout)
        with _agent_from_command(
            shlex.split(args.agent_a_command), deadline, args.cwd
        ) as agent_a, _agent_from_command(
            shlex.split(args.agent_b_command), deadline, args.cwd
        ) as agent_b:
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
            )
    except (ValueError, OSError) as error:
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
