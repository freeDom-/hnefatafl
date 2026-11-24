"""Persistent process-agent lifecycle: deadlines, recovery and protocol integrity."""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

SPIKE = Path(__file__).resolve().parents[1] / "spikes/browser_offline"
if str(SPIKE) not in sys.path:
    sys.path.insert(0, str(SPIKE))

import tournament  # noqa: E402
from hnefatafl.engine import new_game  # noqa: E402

AGENT_SCRIPT = r"""
import json
import sys
import time

for line in sys.stdin:
    request = json.loads(line)
    mode = (request.get("settings") or {}).get("mode", "ok")
    if mode in ("hang", "blocking"):
        time.sleep(5)
        continue
    if mode == "eof":
        sys.exit(3)
    if mode == "malformed":
        print("this is not json", flush=True)
        continue
    if mode == "stale":
        print(json.dumps({"id": "0:0", "move": {"source": 0, "target": 1}}), flush=True)
        continue
    if mode == "diagnostic":
        for _ in range(200):
            sys.stderr.write("x" * 1000 + "\n")
        sys.stderr.flush()
    print(
        json.dumps({"id": request["id"], "move": {"source": 0, "target": 1}}),
        flush=True,
    )
"""


@pytest.fixture()
def agent_script(tmp_path):
    path = tmp_path / "agent.py"
    path.write_text(AGENT_SCRIPT)
    return path


def _agent(script: Path, *, timeout: float = 1.0, **kwargs) -> tournament.JsonProcessAgent:
    return tournament.JsonProcessAgent([sys.executable, str(script)], timeout=timeout, **kwargs)


def _state():
    return new_game("brandubh")


def _request(agent, mode="ok", pad=None):
    settings = {"mode": mode}
    if pad is not None:
        settings["pad"] = pad
    return agent(_state(), settings, 7)


def test_same_adapter_recovers_after_timeout_stale_eof_and_malformed(agent_script):
    agent = _agent(agent_script)
    try:
        assert _request(agent) == tournament.Move(0, 1)
        first_generation = agent.diagnostics()["generation"]

        with pytest.raises(tournament.AgentTimeoutError):
            _request(agent, "hang")
        assert agent.process is None
        assert _request(agent) == tournament.Move(0, 1)
        assert agent.diagnostics()["generation"] > first_generation

        with pytest.raises(tournament.AgentProtocolError, match="does not match"):
            _request(agent, "stale")
        assert _request(agent) == tournament.Move(0, 1)

        with pytest.raises(tournament.AgentProtocolError, match="without a response"):
            _request(agent, "eof")
        assert _request(agent) == tournament.Move(0, 1)

        with pytest.raises(tournament.AgentProtocolError, match="malformed JSON"):
            _request(agent, "malformed")
        assert _request(agent) == tournament.Move(0, 1)
    finally:
        agent.close()
    assert agent.process is None
    assert agent.diagnostics()["running"] is False


def test_blocking_child_cannot_block_past_the_deadline_and_is_reaped(agent_script):
    agent = _agent(agent_script, timeout=0.3)
    try:
        started = time.monotonic()
        with pytest.raises(tournament.AgentTimeoutError):
            _request(agent, "blocking", pad="x" * 262144)
        elapsed = time.monotonic() - started
        assert elapsed < 5.0
        assert agent.process is None
    finally:
        agent.close()


def test_excessive_diagnostics_are_bounded_and_do_not_break_the_protocol(agent_script):
    agent = _agent(agent_script, timeout=2.0, max_stderr_bytes=4096)
    try:
        assert _request(agent, "diagnostic") == tournament.Move(0, 1)
        diagnostics = agent.diagnostics()
        assert diagnostics["running"] is True
        assert diagnostics["stderr_bytes"] <= 4096
        assert _request(agent) == tournament.Move(0, 1)
    finally:
        agent.close()


def test_stale_generation_responses_are_rejected(agent_script):
    agent = _agent(agent_script)
    try:
        assert _request(agent) == tournament.Move(0, 1)
        assert agent.responses is not None
        agent.responses.put(
            {"generation": agent.diagnostics()["generation"] - 1, "line": '{"id": "x"}'}
        )
        with pytest.raises(tournament.AgentProtocolError, match="replaced generation"):
            _request(agent)
        assert _request(agent) == tournament.Move(0, 1)
    finally:
        agent.close()


def test_concurrent_requests_are_rejected_without_corrupting_the_session(agent_script):
    agent = _agent(agent_script, timeout=0.5)
    try:
        assert _request(agent) == tournament.Move(0, 1)
        errors = []

        def block():
            try:
                _request(agent, "hang")
            except tournament.AgentTimeoutError:
                pass
            except Exception as error:  # pragma: no cover - diagnostic
                errors.append(error)

        thread = threading.Thread(target=block)
        thread.start()
        time.sleep(0.05)
        with pytest.raises(tournament.AgentProtocolError, match="concurrent"):
            _request(agent)
        thread.join(timeout=3)
        assert not errors
        assert _request(agent) == tournament.Move(0, 1)
    finally:
        agent.close()


def test_context_manager_closes_streams_and_reader_threads(agent_script):
    with _agent(agent_script) as agent:
        assert _request(agent) == tournament.Move(0, 1)
        threads = list(agent._threads)
        assert threads
    assert agent.process is None
    for thread in threads:
        assert not thread.is_alive()
    assert not any(
        thread.name.startswith("agent-") and thread.is_alive()
        for thread in threading.enumerate()
        if thread is not threading.current_thread()
    )
    with pytest.raises(tournament.AgentProtocolError, match="closed"):
        _request(agent)


def test_adapter_deadline_must_match_the_recorded_tournament_setting(agent_script):
    from fixtures import jarl_positions

    opening = next(
        entry
        for entry in jarl_positions()
        if entry["ruleset"] == "brandubh" and entry["kind"] == "opening"
    )
    agent_a = _agent(agent_script, timeout=1.0)
    agent_b = _agent(agent_script, timeout=1.0)
    try:
        with pytest.raises(ValueError, match="records 2s"):
            tournament.run_paired_openings(
                agent_a,
                agent_b,
                [opening],
                labels=("a", "b"),
                ruleset="brandubh",
                simulations=4,
                time_limit=1.0,
                max_plies=8,
                agent_timeout=2.0,
            )
        report = tournament.run_paired_openings(
            agent_a,
            agent_b,
            [opening],
            labels=("a", "b"),
            ruleset="brandubh",
            simulations=4,
            time_limit=1.0,
            max_plies=8,
            agent_timeout=1.0,
        )
        assert report["settings"]["agent_timeout_s"] == 1.0
        assert all(game["agent_timeout_s"] == 1.0 for game in report["games"])
        assert tournament.validate_report(report)["ok"] is True, report["summary"]
    finally:
        agent_a.close()
        agent_b.close()


def test_blocked_write_cleanup_is_bounded_and_reaps_descendants(tmp_path):
    """External watchdog: a child that never reads stdin cannot hang CI."""
    import json
    import subprocess

    probe = Path(__file__).resolve().parent / "bounded_adapter_probe.py"
    result = subprocess.run(
        [sys.executable, str(probe)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True, payload
    assert payload["descendant_alive"] is False
    assert payload["leaked_threads"] == []
    assert payload["process_after_close"] is False
    assert payload["recovered_move"] == {"source": 0, "target": 1}


@pytest.mark.parametrize("mode", ["exit-immediately", "sigterm-ignore", "hold-pipes"])
def test_parent_exit_and_sigterm_ignoring_descendants_are_cleaned_up(mode):
    import json
    import subprocess

    probe = Path(__file__).resolve().parent / "bounded_tree_probe.py"
    result = subprocess.run(
        [sys.executable, str(probe), mode],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True, payload
    assert payload["descendant_alive"] is False
    assert payload["owned_group_members"] == []
    assert payload["leaked_threads"] == []
    assert payload["process_after_close"] is False
