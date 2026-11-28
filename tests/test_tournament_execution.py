"""Q4: manifest-to-execution binding for persistent tournament agents."""

from __future__ import annotations

import copy
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


def _run(
    tmp_path: Path,
    *,
    config_a: dict,
    config_b: dict,
    command_a: str,
    command_b: str,
    mode: str = "fixed-work",
    simulations: int = 4,
    time_limit: float = 2,
    max_plies: int = 2,
    opening_file: Path | None = None,
    agent_timeout: float = 10,
):
    tmp_path.mkdir(parents=True, exist_ok=True)
    a = _config(tmp_path, "agent-a", **config_a)
    b = _config(tmp_path, "agent-b", **config_b)
    output = tmp_path / "report.json"
    command = [
        sys.executable,
        str(SPIKE / "tournament.py"),
        "--run",
        "--ruleset",
        "brandubh",
        "--limit",
        "1",
        "--simulations",
        str(simulations),
        "--time-limit",
        str(time_limit),
        "--max-plies",
        str(max_plies),
        "--mode",
        mode,
        "--agent-timeout",
        str(agent_timeout),
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
    ]
    if opening_file is not None:
        command[command.index("--limit") : command.index("--limit") + 2] = [
            "--openings-file",
            str(opening_file),
        ]
    result = subprocess.run(
        command,
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


def _run_noninitial(tmp_path: Path):
    from fixtures import jarl_positions

    midgame = next(
        entry
        for entry in jarl_positions()
        if entry["ruleset"] == "brandubh" and entry["kind"] == "midgame"
    )
    openings = tmp_path / "openings.json"
    openings.write_text(
        json.dumps({"openings": [{"name": midgame["name"], "state": midgame["state"]}]})
    )
    a = _config(tmp_path, "agent-a")
    b = _config(
        tmp_path,
        "agent-b",
        kind="scripted",
        algorithm="scripted",
        depth=None,
    )
    output = tmp_path / "noninitial.json"
    result = subprocess.run(
        [
            sys.executable,
            str(SPIKE / "tournament.py"),
            "--run",
            "--ruleset",
            "brandubh",
            "--openings-file",
            str(openings),
            "--simulations",
            "4",
            "--time-limit",
            "2",
            "--max-plies",
            str(midgame["state"]["ply"] + 2),
            "--agent-timeout",
            "10",
            "--agent-a-command",
            _jarl_command(),
            "--agent-b-command",
            _scripted_command(),
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
    return (
        result,
        json.loads(output.read_text()) if output.exists() else None,
        midgame["state"]["ply"],
    )


def test_noninitial_opening_execution_evidence_uses_absolute_ply(tmp_path):
    result, report, starting_ply = _run_noninitial(tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert starting_ply > 0
    validated = tournament.validate_report(report)
    assert validated["ok"] is True, validated["failures"]
    evidence = [
        entry
        for game in report["games"]
        for entry in game["execution_evidence"]
        if entry.get("agent") == "agent-a"
    ]
    assert evidence
    assert all(entry["ply"] >= starting_ply for entry in evidence)
    assert all(
        entry["seed"] == game["seed"] + entry["ply"]
        for game in report["games"]
        for entry in game["execution_evidence"]
        if entry.get("agent") == "agent-a"
    )

    mutated = copy.deepcopy(report)
    mutated["games"][0]["execution_evidence"][0]["ply"] = 0
    validated = tournament.validate_report(mutated)
    assert validated["ok"] is False
    assert any("out of range" in failure for failure in validated["failures"])

    mutated = copy.deepcopy(report)
    target = next(
        entry
        for game in mutated["games"]
        for entry in game["execution_evidence"]
        if entry.get("agent") == "agent-a"
    )
    target["seed"] = target["seed"] + 1
    validated = tournament.validate_report(mutated)
    assert validated["ok"] is False
    assert any("seed" in failure for failure in validated["failures"])


def _seer_config(tmp_path: Path) -> dict:
    return {
        "kind": "native-seer",
        "algorithm": "mcts",
        "depth": None,
        "model": None,
    }


def _scripted_b() -> dict:
    return {
        "label": "agent-b",
        "kind": "scripted",
        "algorithm": "scripted",
        "depth": None,
    }


def _seer_command() -> str:
    return f"{sys.executable} {AGENT} --engine seer"


def test_alpha_beta_fixed_time_allows_deadline_limited_depth(tmp_path):
    result, report = _run(
        tmp_path,
        config_a={"label": "agent-a", "depth": 5},
        config_b={"label": "agent-b", "depth": 1},
        command_a=f"{sys.executable} {AGENT} --engine jarl --depth 5",
        command_b=f"{sys.executable} {AGENT} --engine jarl --depth 1",
        mode="fixed-time",
        time_limit=0.4,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = [
        entry
        for game in report["games"]
        for entry in game["execution_evidence"]
        if entry.get("agent") == "agent-a"
    ]
    assert evidence
    assert all(entry["settings"]["mode"] == "fixed-time" for entry in evidence)
    assert all(0 < entry["depth"] <= 5 for entry in evidence)
    assert all(entry["completion_reason"] in ("deadline", "completed") for entry in evidence)
    assert tournament.validate_report(report)["ok"] is True


def test_mcts_fixed_work_and_fixed_time_semantics(tmp_path):
    result, report = _run(
        tmp_path,
        config_a={"label": "agent-a", **_seer_config(tmp_path)},
        config_b=_scripted_b(),
        command_a=_seer_command(),
        command_b=_scripted_command(),
        mode="fixed-work",
        simulations=8,
        time_limit=5,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = [
        entry
        for game in report["games"]
        for entry in game["execution_evidence"]
        if entry.get("agent") == "agent-a"
    ]
    assert all(entry["completion_reason"] == "simulations" for entry in evidence)
    assert all(entry["completed_simulations"] == 8 for entry in evidence)
    assert tournament.validate_report(report)["ok"] is True

    result, report = _run(
        tmp_path / "time",
        config_a={"label": "agent-a", **_seer_config(tmp_path)},
        config_b={"label": "agent-b", "depth": 1},
        command_a=_seer_command(),
        command_b=f"{sys.executable} {AGENT} --engine jarl --depth 1",
        mode="fixed-time",
        simulations=5000,
        time_limit=0.2,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = [
        entry
        for game in report["games"]
        for entry in game["execution_evidence"]
        if entry.get("agent") == "agent-a"
    ]
    assert all(entry["settings"]["mode"] == "fixed-time" for entry in evidence)
    assert all(entry["completion_reason"] in ("deadline", "simulations") for entry in evidence)
    assert tournament.validate_report(report)["ok"] is True


def test_incomplete_fixed_work_and_bad_reasons_are_rejected(tmp_path):
    result, report = _run(
        tmp_path,
        config_a={"label": "agent-a", "depth": 3},
        config_b=_scripted_b(),
        command_a=f"{sys.executable} {AGENT} --engine jarl --depth 3",
        command_b=_scripted_command(),
        mode="fixed-work",
    )
    assert result.returncode == 0
    game = next(game for game in report["games"] if game["agent_a_side"] == "attackers")
    game["execution_evidence"][0]["depth"] = 1
    game["execution_evidence"][0]["completion_reason"] = "deadline"
    validated = tournament.validate_report(report)
    assert validated["ok"] is False
    assert any(
        "unequal depth" in failure or "did not reach" in failure
        for failure in validated["failures"]
    )

    result, report = _run(
        tmp_path / "mcts",
        config_a={"label": "agent-a", **_seer_config(tmp_path)},
        config_b=_scripted_b(),
        command_a=_seer_command(),
        command_b=_scripted_command(),
        mode="fixed-work",
        simulations=8,
        time_limit=5,
    )
    assert result.returncode == 0
    game = next(game for game in report["games"] if game["agent_a_side"] == "attackers")
    game["execution_evidence"][0]["completion_reason"] = "deadline"
    validated = tournament.validate_report(report)
    assert validated["ok"] is False

    game["execution_evidence"][0]["provider"] = "wasm"
    validated = tournament.validate_report(report)
    assert validated["ok"] is False
    assert any(
        "placement" in failure or "CPU fallback" in failure or "provider" in failure
        for failure in validated["failures"]
    )


def test_unsupported_agent_mode_is_rejected_before_launch(tmp_path):
    result, _ = _run(
        tmp_path,
        config_a={"label": "agent-a", "kind": "scripted", "algorithm": "scripted", "depth": None},
        config_b=_scripted_b(),
        command_a=_scripted_command(),
        command_b=_scripted_command(),
        mode="fixed-time",
    )
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert any("fixed-time" in failure for failure in payload["failures"])


def test_immediate_win_mcts_evidence_is_accepted(tmp_path):
    from fixtures import jarl_positions

    position = next(entry for entry in jarl_positions() if entry["name"] == "brandubh-king-escape")
    openings = tmp_path / "immediate.json"
    openings.write_text(
        json.dumps({"openings": [{"name": position["name"], "state": position["state"]}]})
    )
    result, report = _run(
        tmp_path,
        config_a={
            "label": "agent-a",
            "kind": "native-seer",
            "algorithm": "mcts",
            "depth": None,
            "model": None,
        },
        config_b=_scripted_b(),
        command_a=_seer_command(),
        command_b=_scripted_command(),
        mode="fixed-work",
        simulations=8,
        time_limit=5,
        max_plies=2,
        opening_file=openings,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert tournament.validate_report(report)["ok"] is True


def _committed_browser_report():
    import json

    path = (
        Path(__file__).resolve().parents[1]
        / "docs/evaluation/browser-analysis/evidence/tournament-browser-structural-phase3c.json"
    )
    return json.loads(path.read_text())


def test_browser_search_evidence_uses_observed_provider_and_effective_settings():
    report = _committed_browser_report()
    assert tournament.validate_report(report)["ok"] is True
    browser_entries = [
        entry
        for game in report["games"]
        for entry in game["execution_evidence"]
        if entry.get("agent") == "browser-neural"
    ]
    assert browser_entries
    assert all(entry["provider"] == "wasm" for entry in browser_entries)
    assert all(entry["neural_inferences"] > 0 for entry in browser_entries)
    assert all(entry["settings"]["iterations"] == 4 for entry in browser_entries)
    assert all(entry["gpu_execution_verified"] is False for entry in browser_entries)

    mutated = copy.deepcopy(report)
    target = next(
        entry
        for game in mutated["games"]
        for entry in game["execution_evidence"]
        if entry.get("agent") == "browser-neural"
    )
    target["provider"] = "webgpu-session-unverified"
    assert tournament.validate_report(mutated)["ok"] is False

    mutated = copy.deepcopy(report)
    target = next(
        entry
        for game in mutated["games"]
        for entry in game["execution_evidence"]
        if entry.get("agent") == "browser-neural"
    )
    target["provider"] = "cpu-fallback"
    validated = tournament.validate_report(mutated)
    assert validated["ok"] is False
    assert any(
        "placement" in failure or "CPU fallback" in failure or "provider" in failure
        for failure in validated["failures"]
    )

    mutated = copy.deepcopy(report)
    target = next(
        entry
        for game in mutated["games"]
        for entry in game["execution_evidence"]
        if entry.get("agent") == "browser-neural"
    )
    target["settings"]["iterations"] = 999
    assert tournament.validate_report(mutated)["ok"] is False


def test_no_inference_browser_shortcut_does_not_invent_placement():
    report = _committed_browser_report()
    captured = copy.deepcopy(report)
    target = next(
        entry
        for game in captured["games"]
        for entry in game["execution_evidence"]
        if entry.get("agent") == "browser-neural"
    )
    target.update(
        provider=None,
        provider_requested=None,
        provider_observed=False,
        neural_inferences=0,
        gpu_execution_verified=False,
        completed_simulations=0,
        completion_reason="immediate_win",
    )
    validated = tournament.validate_report(captured)
    assert validated["ok"] is True, validated["failures"]

    target["provider"] = "wasm"
    validated = tournament.validate_report(captured)
    assert validated["ok"] is False
    assert any(
        "inference" in failure or "contradicts" in failure for failure in validated["failures"]
    )


def test_offline_wasm_evidence_cannot_claim_verified_gpu_execution():
    report = _committed_browser_report()
    target = next(
        entry
        for game in report["games"]
        for entry in game["execution_evidence"]
        if entry.get("agent") == "browser-neural"
    )
    assert target["provider"] == "wasm"
    target["gpu_execution_verified"] = True
    validated = tournament.validate_report(report)
    assert validated["ok"] is False
    assert any("WASM observation cannot claim" in failure for failure in validated["failures"])

    live_failures = tournament._provider_evidence_failures(
        requested="wasm",
        observed="wasm",
        reported_requested="wasm",
        inferences=5,
        gpu_verified=True,
        provider_observed=True,
    )
    assert any("WASM observation cannot claim" in failure for failure in live_failures)


def test_native_neural_adapter_handshake_and_execution(tmp_path):
    pytest.importorskip("onnx")
    pytest.importorskip("onnxruntime")
    checkpoint = tmp_path / "smoke.pt"
    export = tmp_path / "export"
    subprocess.run(
        [
            sys.executable,
            str(SPIKE / "smoke_checkpoint.py"),
            "--ruleset",
            "brandubh",
            "--channels",
            "4",
            "--blocks",
            "0",
            "--output",
            str(checkpoint),
        ],
        check=True,
        capture_output=True,
        timeout=180,
    )
    subprocess.run(
        [sys.executable, str(SPIKE / "onnx_export.py"), str(checkpoint), str(export)],
        check=True,
        capture_output=True,
        timeout=300,
    )
    sidecar = json.loads((export / "model.json").read_text())
    model = {
        "onnx_sha256": sidecar["onnx"]["sha256"],
        "weights_sha256": sidecar["source"]["weights_sha256"],
        "ruleset": sidecar["ruleset"],
        "size": 7,
    }
    config_a = {
        "label": "native-neural",
        "kind": "native-neural",
        "provider": "torch-cpu",
        "algorithm": "mcts",
        "depth": None,
        "model": model,
    }
    result, report = _run(
        tmp_path,
        config_a=config_a,
        config_b=_scripted_b(),
        command_a=(
            f"{sys.executable} {AGENT} --engine neural "
            f"--checkpoint {checkpoint} --model-export {export}"
        ),
        command_b=_scripted_command(),
        mode="fixed-work",
        simulations=4,
        time_limit=2,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert report["agent_handshakes"]["a"]["kind"] == "native-neural"
    assert report["agent_handshakes"]["a"]["model_sha256"] == model["onnx_sha256"]
    evidence = [
        entry
        for game in report["games"]
        for entry in game["execution_evidence"]
        if entry.get("agent") == "native-neural"
    ]
    assert evidence
    assert all(entry["algorithm"] == "mcts" for entry in evidence)
    assert all(entry["provider_requested"] == "torch-cpu" for entry in evidence)
    assert all(entry["gpu_execution_verified"] is False for entry in evidence)
    assert tournament.validate_report(report)["ok"] is True


def _native_neural_agent_artifacts(tmp_path):
    pytest.importorskip("onnx")
    pytest.importorskip("onnxruntime")
    from test_competitive_validation import _exported_checkpoint

    return _exported_checkpoint(tmp_path, channels=4, blocks=0, name="adapter-model")


def test_native_neural_adapter_rejects_invalid_or_mismatched_artifacts(tmp_path):
    checkpoint, export = _native_neural_agent_artifacts(tmp_path)
    base_command = [
        sys.executable,
        str(AGENT),
        "--engine",
        "neural",
        "--checkpoint",
        str(checkpoint),
        "--model-export",
        str(export),
    ]
    valid = subprocess.run(base_command, input="", capture_output=True, text=True, timeout=120)
    assert valid.returncode == 0
    assert "handshake" in valid.stdout

    modified = tmp_path / "modified-export"
    subprocess.run(
        [
            sys.executable,
            "-c",
            f"import shutil; shutil.copytree({str(export)!r}, {str(modified)!r})",
        ],
        check=True,
    )
    with (modified / "model.onnx").open("ab") as handle:
        handle.write(b"corrupt")
    corrupted = subprocess.run(
        [*base_command[:5], str(modified)],
        input="",
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert corrupted.returncode == 2
    assert str(checkpoint) not in corrupted.stderr

    stale = tmp_path / "stale-export"
    subprocess.run(
        [sys.executable, "-c", f"import shutil; shutil.copytree({str(export)!r}, {str(stale)!r})"],
        check=True,
    )
    marker = json.loads((stale / "VALIDATED.json").read_text())
    marker["validated"] = False
    (stale / "VALIDATED.json").write_text(json.dumps(marker))
    result = subprocess.run(
        [*base_command[:5], str(stale)],
        input="",
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 2

    other_checkpoint = tmp_path / "other.pt"
    subprocess.run(
        [
            sys.executable,
            str(SPIKE / "smoke_checkpoint.py"),
            "--ruleset",
            "brandubh",
            "--channels",
            "4",
            "--blocks",
            "0",
            "--seed",
            "99",
            "--output",
            str(other_checkpoint),
        ],
        check=True,
        capture_output=True,
        timeout=120,
    )
    mismatched = subprocess.run(
        [
            sys.executable,
            str(AGENT),
            "--engine",
            "neural",
            "--checkpoint",
            str(other_checkpoint),
            "--model-export",
            str(export),
        ],
        input="",
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert mismatched.returncode == 2


def test_native_neural_adapter_retains_verified_checkpoint_after_file_replacement(tmp_path):
    checkpoint, export = _native_neural_agent_artifacts(tmp_path)
    replacement, _ = _native_neural_agent_artifacts(tmp_path / "replacement")
    agent = subprocess.Popen(
        [
            sys.executable,
            str(AGENT),
            "--engine",
            "neural",
            "--checkpoint",
            str(checkpoint),
            "--model-export",
            str(export),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    from fixtures import jarl_positions

    position = next(entry for entry in jarl_positions() if entry["name"] == "brandubh-opening")
    request = {
        "id": "1:1",
        "state": position["state"],
        "settings": {
            "algorithm": "mcts",
            "iterations": 4,
            "time_limit": 2,
            "exploration": 1.4,
            "seed": 7,
            "mode": "fixed-work",
        },
        "seed": 7,
    }
    try:
        handshake = json.loads(agent.stdout.readline())
        assert handshake["kind"] == "handshake"
        model_digest = handshake["agent"]["model_sha256"]
        agent.stdin.write(json.dumps(request) + "\n")
        agent.stdin.flush()
        first = json.loads(agent.stdout.readline())
        assert first["evidence"]["model_sha256"] == model_digest

        checkpoint.write_bytes(replacement.read_bytes())
        request["id"] = "1:2"
        agent.stdin.write(json.dumps(request) + "\n")
        agent.stdin.flush()
        second = json.loads(agent.stdout.readline())
        assert second["evidence"]["model_sha256"] == model_digest
        assert second["move"] != first["move"] or second["evidence"]["seed"] == request["seed"]
    finally:
        if agent.stdin:
            agent.stdin.close()
        try:
            agent.wait(timeout=10)
        except subprocess.TimeoutExpired:
            agent.kill()
            agent.wait(timeout=10)
