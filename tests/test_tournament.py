"""Role-balanced paired-opening tournament harness and validator."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

SPIKE = Path(__file__).resolve().parents[1] / "spikes/browser_offline"
for path in (str(SPIKE), str(SPIKE.parents[1])):
    if path not in sys.path:
        sys.path.insert(0, path)

import tournament  # noqa: E402
from fixtures import jarl_positions  # noqa: E402
from hnefatafl.engine import legal_moves  # noqa: E402


def first_legal(state, settings, seed):
    return legal_moves(state)[0]


def last_legal(state, settings, seed):
    return legal_moves(state)[-1]


def _openings():
    return [
        entry
        for entry in jarl_positions()
        if entry["ruleset"] == "brandubh" and entry["kind"] in ("opening", "tactical")
    ]


def _report():
    return tournament.run_paired_openings(
        first_legal,
        last_legal,
        _openings(),
        labels=("first", "last"),
        ruleset="brandubh",
        simulations=8,
        time_limit=1,
        seed=11,
        max_plies=24,
    )


def test_paired_openings_validate_and_cover_roles():
    report = _report()
    assert report["schema"] == tournament.SCHEMA
    validated = tournament.validate_report(report)
    assert validated["ok"] is True, validated["failures"]
    assert len(report["games"]) == 2 * len(_openings())
    assert report["summary"]["games"] == len(report["games"])
    assert report["summary"]["pairs"] == len(_openings())
    assert all(
        {game["agent_a_side"], game["agent_b_side"]} == {"attackers", "defenders"}
        for game in report["games"]
    )
    assert report["authoritative"] is False


@pytest.mark.parametrize(
    "mutation",
    [
        lambda report: report["games"].pop(),
        lambda report: report["games"].append(copy.deepcopy(report["games"][0])),
        lambda report: report["games"][0]["moves"].append({"source": 0, "target": 0}),
        lambda report: report["games"][0].update(agent_a="other"),
        lambda report: report["games"][0].update(
            agent_a_side="attackers", agent_b_side="attackers"
        ),
        lambda report: report["games"][0].update(winner="invalid"),
        lambda report: report["summary"].update(games=0),
        lambda report: report["summary"].update(completed=0),
    ],
)
def test_validator_rejects_negative_tournament_reports(mutation):
    report = _report()
    mutation(report)
    validated = tournament.validate_report(report)
    assert validated["ok"] is False
    assert validated["failures"]


def test_validator_cli_returns_nonzero(tmp_path):
    report = _report()
    report["games"].pop()
    source = tmp_path / "report.json"
    source.write_text(json.dumps(report))
    result = subprocess.run(
        [sys.executable, str(SPIKE / "tournament.py"), str(source)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode != 0
    assert "game identifiers" in result.stdout


def test_unresponsive_agent_hits_hard_deadline_and_other_games_continue():
    import time

    def blocking(state, settings, seed):
        time.sleep(0.2)
        return legal_moves(state)[0]

    report = tournament.run_paired_openings(
        blocking,
        last_legal,
        _openings()[:1],
        labels=("blocking", "last"),
        ruleset="brandubh",
        simulations=8,
        time_limit=1,
        seed=3,
        max_plies=8,
        agent_timeout=0.02,
    )
    assert report["settings"]["agent_timeout_s"] == 0.02
    assert len(report["games"]) == 2
    assert all("AgentTimeoutError" in (game["error"] or "") for game in report["games"])
    assert report["summary"]["execution_errors"] == 2
    assert tournament.validate_report(report)["ok"] is True


def test_validator_detects_pair_opening_history_and_terminal_mismatch():
    report = _report()
    a = report["games"][0]
    b = next(game for game in report["games"] if game["pair"] == a["pair"] and game is not a)
    b["opening_state"] = copy.deepcopy(b["opening_state"])
    b["opening_state"]["history"] = ["different"]
    assert tournament.validate_report(report)["ok"] is False

    report = _report()
    game = next(game for game in report["games"] if game["completed"])
    game["plies"] += 1
    assert tournament.validate_report(report)["ok"] is False

    report = _report()
    game = report["games"][0]
    game["error"] = "forced execution failure"
    game["completed"] = True
    game["winner"] = "draw"
    assert tournament.validate_report(report)["ok"] is False


def test_validator_rejects_changed_role_and_fabricated_summary():
    report = _report()
    report["games"][0]["agent_a_side"] = "defenders"
    assert tournament.validate_report(report)["ok"] is False

    report = _report()
    report["summary"]["by_agent"]["first"]["wins"] += 1
    assert tournament.validate_report(report)["ok"] is False

    report = _report()
    report["summary"]["paired_score_ci95"] = [0, 1]
    assert tournament.validate_report(report)["ok"] is False


def test_portable_process_agent_timeout_and_recovery(tmp_path):
    script = tmp_path / "agent.py"
    script.write_text(
        "import json,sys\n"
        "for line in sys.stdin:\n"
        "    request=json.loads(line)\n"
        "    from hnefatafl.engine import State, legal_moves\n"
        "    if request['settings'].get('hang'):\n"
        "        continue\n"
        "    moves=legal_moves(State.from_dict(request['state']))\n"
        "    print(json.dumps({'id': request['id'], 'move': moves[0].to_dict()}), flush=True)\n"
    )
    env = {"PYTHONPATH": str(SPIKE.parents[1])}
    import os

    env.update(os.environ)
    agent = tournament.JsonProcessAgent([sys.executable, str(script)], timeout=1.0, cwd=tmp_path)
    state = __import__("hnefatafl.engine", fromlist=["new_game"]).new_game("brandubh")
    assert agent(state, {"iterations": 1}, 1) in legal_moves(state)
    generation = agent.diagnostics()["generation"]
    with pytest.raises(tournament.AgentTimeoutError):
        agent(state, {"iterations": 1, "hang": True}, 1)
    # The same adapter instance must recover with a replacement process.
    assert agent(state, {"iterations": 1}, 1) in legal_moves(state)
    assert agent.diagnostics()["generation"] > generation
    agent.close()


def test_manifest_and_initial_ply_invariants():
    report = _report()
    report["manifest"]["openings"][0]["state"] = copy.deepcopy(
        report["manifest"]["openings"][0]["state"]
    )
    report["manifest"]["openings"][0]["state"]["board"][0] = "A"
    assert tournament.validate_report(report)["ok"] is False

    report = _report()
    report["agents"]["b"] = report["agents"]["a"]
    assert tournament.validate_report(report)["ok"] is False

    midgame = next(
        entry
        for entry in jarl_positions()
        if entry["ruleset"] == "brandubh" and entry["kind"] == "midgame"
    )
    assert midgame["state"]["ply"] > 0
    report = tournament.run_paired_openings(
        first_legal,
        last_legal,
        [midgame],
        labels=("first", "last"),
        ruleset="brandubh",
        simulations=8,
        time_limit=1,
        seed=5,
        max_plies=24,
    )
    assert tournament.validate_report(report)["ok"] is True


def test_failed_game_with_illegal_prefix_history_is_rejected():
    report = _report()
    game = report["games"][0]
    game["error"] = "forced"
    game["completed"] = False
    game["winner"] = None
    game["moves"] = [{"source": 0, "target": 0}]
    game["plies"] = 1
    assert tournament.validate_report(report)["ok"] is False


# ---------------------------------------------------------------------------
# N4: canonical manifest, replay, budgets and statistics acceptance
# ---------------------------------------------------------------------------


def _recompute(report):
    report["summary"] = tournament.summarize(
        report["games"],
        (report["agents"]["a"], report["agents"]["b"]),
        seed=report["settings"]["seed"],
    )
    return report


def _game(report, leg="pair-0-ab"):
    return next(game for game in report["games"] if game["game_id"] == leg)


def _assert_failure(report, needle):
    validated = tournament.validate_report(report)
    assert validated["ok"] is False, needle
    assert any(needle in failure for failure in validated["failures"]), validated["failures"]


def test_game_identifier_is_bound_to_the_expected_manifest_opening():
    report = _report()
    game = _game(report)
    other = report["manifest"]["openings"][1]
    game["opening"] = other["name"]
    game["opening_state"] = copy.deepcopy(other["state"])
    _recompute(report)
    _assert_failure(report, "pair index contradicts the manifest opening")


def test_both_legs_must_share_exact_opening_state_and_history():
    report = _report()
    opening = report["manifest"]["openings"][0]
    opening["state"] = copy.deepcopy(opening["state"])
    opening["state"]["history"] = ["brandubh-opening"]
    _recompute(report)
    _assert_failure(report, "opening state/history differs from manifest")


def test_actual_deserialized_rulesets_are_validated():
    report = _report()
    report["games"][0]["ruleset"] = "tablut"
    _recompute(report)
    _assert_failure(report, "ruleset mismatch")

    report = _report()
    report["manifest"]["openings"][0]["state"]["ruleset"] = "not-a-ruleset"
    _assert_failure(report, "manifest opening state is invalid")


def test_nonterminal_openings_and_role_assignments_are_enforced():
    report = _report()
    opening = report["manifest"]["openings"][0]["state"]
    opening["winner"] = "defenders"
    opening["reason"] = "king_escaped"
    for game in report["games"]:
        if game["pair"] == 0:
            game["opening_state"] = copy.deepcopy(opening)
    _recompute(report)
    _assert_failure(report, "opening state is terminal")

    report = _report()
    game = _game(report)
    game["attacker_agent"], game["defender_agent"] = (
        game["defender_agent"],
        game["attacker_agent"],
    )
    _assert_failure(report, "attacker assignment contradicts agent roles")


def test_complete_agent_configuration_and_model_identity_are_required():
    report = _report()
    report["manifest"]["agent_config"]["b"]["kind"] = "browser-neural"
    report["agent_config"]["b"]["kind"] = "browser-neural"
    _assert_failure(report, "model identity is missing")

    report = _report()
    report["manifest"]["agent_config"]["b"]["runtime"] = ""
    report["agent_config"]["b"]["runtime"] = ""
    _assert_failure(report, "runtime is missing")

    report = _report()
    report["agent_config"]["a"]["model"] = {
        "onnx_sha256": "a" * 64,
        "weights_sha256": "b" * 64,
        "ruleset": "tablut",
        "size": 9,
    }
    report["manifest"]["agent_config"] = copy.deepcopy(report["agent_config"])
    report["agent_config"]["a"]["kind"] = "native-neural"
    report["manifest"]["agent_config"]["a"]["kind"] = "native-neural"
    report["agent_config"]["a"]["provider"] = "onnxruntime-cpu"
    report["manifest"]["agent_config"]["a"]["provider"] = "onnxruntime-cpu"
    _assert_failure(report, "model ruleset mismatch")


def test_per_game_algorithm_seed_budget_and_deadline_are_validated():
    for field, value, needle in (
        ("algorithm", "alpha-beta", "algorithm identity"),
        ("iterations", 4, "iterations differs"),
        ("time_limit", 5, "time_limit differs"),
    ):
        report = _report()
        _game(report)["settings"][field] = value
        _assert_failure(report, needle)

    report = _report()
    game = _game(report)
    game["settings"]["seed"] = game["seed"] + 1
    _assert_failure(report, "game seed does not match the scheduled seed")

    report = _report()
    _game(report)["agent_timeout_s"] = 99
    _assert_failure(report, "adapter deadline does not match")


def test_absolute_ply_move_limits_and_initial_ply():
    report = _report()
    game = _game(report)
    game["plies"] += 1
    _assert_failure(report, "absolute ply count does not match")

    report = _report()
    game = _game(report)
    game["plies"] = report["settings"]["max_plies"] + 1
    _assert_failure(report, "exceeds its recorded move limit")

    report = _report()
    report["manifest"]["openings"][0]["starting_ply"] = 7
    _assert_failure(report, "starting ply is inconsistent")

    midgame = next(
        entry
        for entry in jarl_positions()
        if entry["ruleset"] == "brandubh" and entry["kind"] == "midgame"
    )
    limit = midgame["state"]["ply"] + 2
    report = tournament.run_paired_openings(
        first_legal,
        last_legal,
        [midgame],
        labels=("first", "last"),
        ruleset="brandubh",
        simulations=8,
        time_limit=1,
        seed=5,
        max_plies=limit,
    )
    assert tournament.validate_report(report)["ok"] is True
    assert all(game["plies"] <= limit for game in report["games"])


def test_truncated_move_limit_outcome_is_validated():
    report = tournament.run_paired_openings(
        first_legal,
        last_legal,
        _openings()[:1],
        labels=("first", "last"),
        ruleset="brandubh",
        simulations=8,
        time_limit=1,
        max_plies=3,
    )
    assert all(not game["completed"] for game in report["games"])
    assert tournament.validate_report(report)["ok"] is True
    game = report["games"][0]
    game["plies"] -= 1
    _recompute(report)
    _assert_failure(report, "did not reach the recorded move limit")


def test_terminal_reason_and_completion_combinations_are_validated():
    report = _report()
    game = next(game for game in report["games"] if game["completed"])
    game["reason"] = "king_escaped" if game["reason"] != "king_escaped" else "king_captured"
    _assert_failure(report, "terminal reason does not match replay")

    report = _report()
    game = next(game for game in report["games"] if game["completed"])
    game["reason"] = "move_limit"
    _assert_failure(report, "completion reason is not a terminal reason")

    report = tournament.run_paired_openings(
        first_legal,
        last_legal,
        _openings()[:1],
        labels=("first", "last"),
        ruleset="brandubh",
        simulations=8,
        time_limit=1,
        max_plies=3,
    )
    report["games"][0]["reason"] = "terminal"
    _recompute(report)
    _assert_failure(report, "uncompleted game reason is not a move limit")


def test_failed_game_prefix_is_replayed_before_accepting_the_failure():
    report = _report()
    game = next(game for game in report["games"] if game["completed"] and game["moves"])
    legal_prefix = game["moves"][:1]
    game.update(
        moves=legal_prefix,
        plies=game["starting_ply"] + len(legal_prefix),
        completed=False,
        winner=None,
        reason="execution_error",
        error="RuntimeError: simulated agent failure",
    )
    _recompute(report)
    validated = tournament.validate_report(report)
    assert validated["ok"] is True, validated["failures"]

    game["moves"] = legal_prefix + [{"source": 0, "target": 0}]
    game["plies"] = game["starting_ply"] + len(game["moves"])
    _recompute(report)
    _assert_failure(report, "illegal move")


def test_pair_ordering_is_canonical_for_bootstrap_statistics():
    report = _report()
    reference = copy.deepcopy(report["summary"])
    report["games"] = list(reversed(report["games"]))
    _recompute(report)
    assert report["summary"] == reference
    assert tournament.validate_report(report)["ok"] is True


def test_structural_and_playing_strength_evidence_are_distinguished():
    report = _report()
    report["structural_only"] = True
    report["playing_strength"]["status"] = "measured"
    _assert_failure(report, "must mark playing strength blocked")

    report = _report()
    report["structural_only"] = False
    report["evidence_class"] = "playing-strength"
    report["playing_strength"] = {"status": "measured"}
    _assert_failure(report, "requires real agent configurations")

    report = _report()
    report["structural_only"] = False
    report["evidence_class"] = "playing-strength"
    report["playing_strength"] = {"status": "measured"}
    for key in ("a", "b"):
        report["agent_config"][key] = {
            "label": report["agents"][key],
            "kind": "native-neural",
            "runtime": "native-cpython",
            "provider": "onnxruntime-cpu",
            "model": {
                "onnx_sha256": "a" * 64,
                "weights_sha256": "b" * 64,
                "ruleset": "brandubh",
                "size": 7,
            },
        }
    report["manifest"]["agent_config"] = copy.deepcopy(report["agent_config"])
    _assert_failure(report, "at least 10 complete pairs")
