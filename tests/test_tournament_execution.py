"""Q4: manifest-to-execution binding for persistent tournament agents."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SPIKE = Path(__file__).resolve().parents[1] / "spikes/browser_offline"
ROOT = SPIKE.parents[1]
for path in (str(SPIKE), str(ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

import tournament  # noqa: E402

AGENT = SPIKE / "native_tournament_agent.py"


def _config(tmp_path: Path, stem: str, **overrides) -> Path:
    config = {
        "label": stem,
        "kind": "native-jarl",
        "runtime": "native-cpython",
        "provider": "cpu",
        "algorithm": "alpha_beta",
        "depth": 2,
        "model": None,
    }
    config.update(overrides)
    path = tmp_path / f"{stem}.json"
    path.write_text(json.dumps(config))
    return path


def _run(tmp_path: Path, *, config_a: dict, config_b: dict, command_a: str, command_b: str):
    a = _config(tmp_path, "agent-a", **config_a)
    b = _config(tmp_path, "agent-b", **config_b)
    output = tmp_path / "report.json"
    result = subprocess.run(
        [
            sys.executable,
            str(SPIKE / "tournament.py"),
            "--run",
            "--ruleset",
            "brandubh",
            "--limit",
            "1",
            "--simulations",
            "4",
            "--time-limit",
            "2",
            "--max-plies",
            "2",
            "--agent-timeout",
            "10",
            "--agent-a-command",
            command_a,
            "--agent-b-command",
            command_b,
            "--agent-a-config",
            str(a),
            "--agent-b-config",
            str(b),
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    report = json.loads(output.read_text()) if output.exists() else None
    return result, report


def _jarl_command() -> str:
    return f"{sys.executable} {AGENT} --engine jarl --depth 2"


def _scripted_command() -> str:
    return f"{sys.executable} {AGENT} --engine scripted"


def test_real_agent_handshake_and_per_move_evidence_validate(tmp_path):
    result, report = _run(
        tmp_path,
        config_a={"label": "agent-a"},
        config_b={"label": "agent-b", "kind": "scripted", "algorithm": "scripted", "depth": None},
        command_a=_jarl_command(),
        command_b=_scripted_command(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert report["summary"]["execution_errors"] == 0
    assert report["agent_handshakes"]["a"]["algorithm"] == "alpha_beta"
    assert report["agent_handshakes"]["a"]["kind"] == "native-jarl"
    assert report["agent_handshakes"]["b"]["kind"] == "scripted"
    evidence = [
        e
        for game in report["games"]
        for e in game["execution_evidence"]
        if e.get("agent") == "agent-a"
    ]
    assert evidence
    assert all(e["algorithm"] == "alpha_beta" for e in evidence)
    assert all(e["depth"] == 2 for e in evidence)
    assert all(e["provider"] == "cpu" for e in evidence)
    validated = tournament.validate_report(report)
    assert validated["ok"] is True, validated["failures"]


def test_command_configuration_mismatch_is_rejected(tmp_path):
    result, _ = _run(
        tmp_path,
        config_a={"label": "agent-a", "kind": "native-jarl"},
        config_b={
            "label": "agent-b",
            "kind": "scripted",
            "algorithm": "scripted",
            "depth": None,
        },
        command_a=_scripted_command(),
        command_b=_scripted_command(),
    )
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert any("handshake" in failure for failure in payload["failures"])


def test_incorrect_adapter_identity_is_rejected(tmp_path):
    script = tmp_path / "protocol_agent.py"
    script.write_text(
        "import json,sys\n"
        "print(json.dumps({'kind':'handshake','protocol':2,'agent':{"
        "'protocol':2,'kind':'native-jarl','runtime':'native-cpython',"
        "'provider':'cpu','algorithm':'alpha_beta','depth':2,'model_sha256':None,"
        "'weights_sha256':None,'ruleset':'brandubh','size':7}}), flush=True)\n"
        "for line in sys.stdin:\n"
        "    request=json.loads(line)\n"
        "    print(json.dumps({'id':request['id'],'move':{'source':0,'target':1},"
        "'evidence':{'settings':request['settings'],'seed':request['seed'],"
        "'algorithm':'alpha_beta','provider':'cpu','completed_simulations':None,"
        "'depth':2,'completion_reason':'completed','elapsed_ms':1}}), flush=True)\n"
    )
    agent = tournament.JsonProcessAgent(
        [sys.executable, str(script)],
        timeout=2,
        expected_identity={
            "kind": "native-jarl",
            "runtime": "native-cpython",
            "provider": "cpu",
            "algorithm": "alpha_beta",
            "depth": 2,
            "model_sha256": "a" * 64,
        },
    )
    try:
        with pytest.raises(tournament.AgentProtocolError, match="model_sha256"):
            agent(
                __import__("hnefatafl.engine", fromlist=["new_game"]).new_game("brandubh"),
                {"iterations": 4, "time_limit": 1, "exploration": 1.4},
                7,
            )
    finally:
        agent.close()


@pytest.mark.parametrize(
    "mutation,needle",
    [
        (lambda game: game["execution_evidence"][0].update(seed=999), "seed"),
        (lambda game: game["execution_evidence"][0].update(provider="wasm"), "provider"),
        (
            lambda game: game["execution_evidence"][0].update(
                completed_simulations=1, algorithm="mcts"
            ),
            "algorithm",
        ),
        (lambda game: game["execution_evidence"][0].update(depth=1), "depth"),
        (
            lambda game: game["execution_evidence"].pop(0),
            "missing execution evidence",
        ),
    ],
)
def test_invalid_per_move_evidence_blocks_validation(tmp_path, mutation, needle):
    result, report = _run(
        tmp_path,
        config_a={"label": "agent-a"},
        config_b={"label": "agent-b", "kind": "scripted", "algorithm": "scripted", "depth": None},
        command_a=_jarl_command(),
        command_b=_scripted_command(),
    )
    assert result.returncode == 0
    game = next(game for game in report["games"] if game["agent_a_side"] == "attackers")
    mutation(game)
    validated = tournament.validate_report(report)
    assert validated["ok"] is False
    assert any(needle in failure for failure in validated["failures"]), validated["failures"]
    assert validated["playing_strength_acceptance"] is False
