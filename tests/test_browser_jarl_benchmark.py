"""Regression tests for the corrected native/browser Jarl benchmark contract.

These exercise the canonical comparison module (``jarl_compare``) and the shared
workload identity (``jarl_benchmark``) without a browser. The real browser gate is
the opt-in ``verify_jarl.mjs`` run; this file proves the gate would fail on a
regression instead of silently passing on plan equality alone.
"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

SPIKE = Path(__file__).resolve().parents[1] / "spikes/browser_offline"
ROOT = Path(__file__).resolve().parents[1]
if str(SPIKE) not in sys.path:
    sys.path.insert(0, str(SPIKE))

import jarl_benchmark as jb  # noqa: E402
import jarl_compare as jc  # noqa: E402
from hnefatafl.engine import State, new_game  # noqa: E402

EFFECTIVE = {
    "quick": {"algorithm": "alpha_beta", "depth": 3, "time_limit": 60.0},
    "standard": {"algorithm": "alpha_beta", "depth": 5, "time_limit": 60.0},
}


def _record(
    position, config, ruleset, *, status="completed", move=None, value=0.25, depth=3, **extra
):
    record = {
        "position": position,
        "kind": "opening",
        "config": config,
        "mode": "fixed_work",
        "ruleset": ruleset,
        "seed": 7,
        "status": status,
        "terminal": status == "terminal",
        "unsupported": status == "unsupported",
        "best_move": None if status == "terminal" else (move or {"source": 1, "target": 2}),
        "value": 0.0 if status == "terminal" else value,
        "completed_depth": None if status in ("terminal", "error", "unsupported") else depth,
        "elapsed_ms": 12.0,
        "error": "ValueError: Search depth must be between 1 and 8"
        if status == "unsupported"
        else None,
        "phase": "measured",
    }
    record.update(extra)
    return record


def _report(records, *, mode="fixed_work", digest="a" * 64, configs=None, knobs=None):
    identity = {
        "protocol": 2,
        "workload_digest": digest,
        "mode": mode,
        "seed": 7,
        "engine_identity": {"modules": ["engine"], "sha256": "b" * 64},
        "effective_configs": configs or EFFECTIVE,
    }
    if knobs:
        identity.update(knobs)
    return {"schema": 2, "identity": identity, "records": records}


BASE_RECORDS = [
    _record("brandubh-opening", "quick", "brandubh"),
    _record("brandubh-terminal", "quick", "brandubh", status="terminal"),
    _record("brandubh-opening", "extended", "brandubh", status="unsupported"),
]


def test_identical_reports_pass_the_gate():
    result = jc.compare(_report(copy.deepcopy(BASE_RECORDS)), _report(copy.deepcopy(BASE_RECORDS)))
    assert result["ok"] is True
    assert result["failures"] == []
    assert result["comparable_cases"] == 3


@pytest.mark.parametrize(
    "mutation, expected",
    [
        (
            lambda r: r["records"][0].__setitem__("best_move", {"source": 9, "target": 9}),
            "best move",
        ),
        (lambda r: r["records"][0].__setitem__("value", 0.99), "value"),
        (lambda r: r["records"][0].__setitem__("completed_depth", 2), "completed depth"),
        (lambda r: r["records"][0].__setitem__("status", "incomplete"), "did not complete"),
    ],
)
def test_fixed_work_divergences_fail_the_gate(mutation, expected):
    actual = _report(copy.deepcopy(BASE_RECORDS))
    mutation(actual)
    result = jc.compare(_report(copy.deepcopy(BASE_RECORDS)), actual)
    assert result["ok"] is False
    assert any(expected in message for message in result["failures"])


def test_terminal_null_move_is_not_a_failure_but_a_terminal_move_is():
    passes = jc.compare(_report(copy.deepcopy(BASE_RECORDS)), _report(copy.deepcopy(BASE_RECORDS)))
    assert passes["ok"] is True
    actual = _report(copy.deepcopy(BASE_RECORDS))
    actual["records"][1]["best_move"] = {"source": 1, "target": 3}
    result = jc.compare(_report(copy.deepcopy(BASE_RECORDS)), actual)
    assert result["ok"] is False
    assert any("terminal best_move must be null" in message for message in result["failures"])


@pytest.mark.parametrize(
    "knobs_key, knobs_value, expected",
    [
        ("seed", 8, "identity.seed"),
        ("workload_digest", "c" * 64, "identity.workload_digest"),
        (
            "engine_identity",
            {"modules": ["engine"], "sha256": "d" * 64},
            "identity.engine_identity",
        ),
        ("effective_configs", {"quick": {"depth": 2}}, "identity.effective_configs"),
    ],
)
def test_identity_mismatches_fail_the_gate(knobs_key, knobs_value, expected):
    actual = _report(copy.deepcopy(BASE_RECORDS), knobs={knobs_key: knobs_value})
    result = jc.compare(_report(copy.deepcopy(BASE_RECORDS)), actual)
    assert result["ok"] is False
    assert any(expected in message for message in result["failures"])


def test_missing_required_case_fails_the_gate():
    actual = _report(copy.deepcopy(BASE_RECORDS))
    actual["records"].pop(0)
    result = jc.compare(_report(copy.deepcopy(BASE_RECORDS)), actual)
    assert result["ok"] is False
    assert any("missing 1 required case" in message for message in result["failures"])


def test_unexpected_error_on_a_previously_completed_case_fails():
    actual = _report(copy.deepcopy(BASE_RECORDS))
    actual["records"][0]["status"] = "error"
    actual["records"][0]["error"] = "worker died"
    result = jc.compare(_report(copy.deepcopy(BASE_RECORDS)), actual)
    assert result["ok"] is False


def test_expected_deadline_limited_fixed_work_case_is_reported_not_failed():
    expected_records = [
        _record("brandubh-opening", "standard", "brandubh", status="deadline_limited", depth=4),
    ]
    actual = _report(copy.deepcopy(expected_records))
    actual["records"][0]["status"] = "deadline_limited"
    actual["records"][0]["completed_depth"] = 4
    result = jc.compare(_report(copy.deepcopy(expected_records)), actual)
    assert result["ok"] is True, result["failures"]
    assert result["limited_cases"] == 1
    # A limited case must never be counted as a completed correctness match.
    assert result["comparable_cases"] == 0
    # If the candidate claims it completed, that is a divergence.
    actual["records"][0]["status"] = "completed"
    assert jc.compare(_report(copy.deepcopy(expected_records)), actual)["ok"] is False


def test_completed_work_counter_differences_are_diagnostic_not_fatal():
    expected = _report(
        [_record("brandubh-opening", "quick", "brandubh", nodes=120, evaluations=40)]
    )
    actual = _report([_record("brandubh-opening", "quick", "brandubh", nodes=118, evaluations=39)])
    result = jc.compare(expected, actual)
    assert result["ok"] is True, result["failures"]
    assert any("completed-work counters differ" in message for message in result["warnings"])


def test_fixed_time_differences_are_informational_only():
    expected = _report([_record("brandubh-opening", "quick", "brandubh")], mode="fixed_time")
    actual = _report(
        [_record("brandubh-opening", "quick", "brandubh", value=0.9, depth=2)],
        mode="fixed_time",
    )
    result = jc.compare(expected, actual)
    assert result["ok"] is True, result["failures"]
    assert result["warnings"], "fixed-time divergence must be reported"


def test_fixed_time_missing_case_is_still_mandatory():
    expected = _report([_record("brandubh-opening", "quick", "brandubh")], mode="fixed_time")
    actual = _report([], mode="fixed_time")
    assert jc.compare(expected, actual)["ok"] is False


# ---------- workload identity (review item 2) ----------


def _custom_position(name, *, board_edit=None, history=None):
    base = new_game("brandubh").to_dict()
    if board_edit:
        board = list(base["board"])
        board = board_edit(board)
        base["board"] = board
    if history is not None:
        base["history"] = history
    return {"name": name, "kind": "custom", "state": base}


def test_same_name_with_different_board_changes_the_digest():
    first = _custom_position("collision")
    second = _custom_position("collision", board_edit=lambda board: board[:5] + ["A"] + board[6:])
    identity_a = jb.workload_identity([first], ["quick"], mode="fixed_work", seed=7)
    identity_b = jb.workload_identity([second], ["quick"], mode="fixed_work", seed=7)
    assert identity_a["digest"] != identity_b["digest"]


def test_same_board_with_different_repetition_history_changes_the_digest():
    plain = _custom_position("history")
    # The start position already records one occurrence; a second occurrence is
    # a genuinely different repetition history with the same board.
    repeated = _custom_position(
        "history", history=list(plain["state"]["history"]) + list(plain["state"]["history"])
    )
    identity_a = jb.workload_identity([plain], ["quick"], mode="fixed_work", seed=7)
    identity_b = jb.workload_identity([repeated], ["quick"], mode="fixed_work", seed=7)
    assert identity_a["digest"] != identity_b["digest"]


def test_position_order_changes_the_workload_digest():
    a = _custom_position("a")
    b = _custom_position("b", board_edit=lambda board: board[:5] + ["A"] + board[6:])
    forward = jb.workload_identity([a, b], ["quick"], mode="fixed_work", seed=7)
    reverse = jb.workload_identity([b, a], ["quick"], mode="fixed_work", seed=7)
    assert forward["digest"] != reverse["digest"]


def test_digest_covers_mode_seed_and_effective_settings():
    one = _custom_position("a")
    base = jb.workload_identity([one], ["quick"], mode="fixed_work", seed=7)
    assert (
        jb.workload_identity([one], ["quick"], mode="fixed_work", seed=8)["digest"]
        != base["digest"]
    )
    assert (
        jb.workload_identity([one], ["quick"], mode="fixed_time", seed=7)["digest"]
        != base["digest"]
    )
    assert (
        jb.workload_identity([one], ["standard"], mode="fixed_work", seed=7)["digest"]
        != base["digest"]
    )


def test_entirely_new_custom_position_is_a_valid_workload():
    custom = _custom_position("never-seen-before")
    identity = jb.workload_identity([custom], ["quick"], mode="fixed_work", seed=7)
    assert identity["cases"][0]["name"] == "never-seen-before"
    assert identity["cases"][0]["ruleset"] == "brandubh"


# ---------- classification and per-case latency (review items 1 and 10) ----------


def test_classification_distinguishes_statuses():
    assert jb.classify({"terminal": True}) == jb.TERMINAL
    assert jb.classify({"unsupported": True}) == jb.UNSUPPORTED
    assert jb.classify({"error": "boom"}) == jb.ERROR
    assert jb.classify({"expected_depth": 5, "completed_depth": 5}) == jb.COMPLETED
    assert jb.classify({"expected_depth": 5, "completed_depth": 4}) == jb.INCOMPLETE
    assert jb.classify({"expected_depth": 5, "completed_depth": 3, "interrupted_depth": 4}) == (
        jb.DEADLINE_LIMITED
    )
    assert jb.classify({"expected_depth": 5, "completed_depth": None}) == jb.INCOMPLETE


def test_run_case_marks_terminal_and_unsupported_without_conflating_them():
    terminal = State.from_dict(next(position["state"] for position in _terminal_positions()))
    record = jb.run_case(terminal, "quick", mode="fixed_work", seed=7)
    assert record["status"] == jb.TERMINAL and record["best_move"] is None
    live = new_game("brandubh")
    unsupported = jb.run_case(live, "extended", mode="fixed_work", seed=7)
    assert unsupported["status"] == jb.UNSUPPORTED
    assert unsupported["error"] and "between 1 and 8" in unsupported["error"]


def _terminal_positions():
    from fixtures import jarl_positions

    return [case for case in jarl_positions() if case["kind"] == "terminal"]


def test_native_report_has_per_case_latency_and_a_separate_batch_total(tmp_path):
    from fixtures import jarl_positions

    positions = [case for case in jarl_positions() if case["name"] == "brandubh-king-escape"]
    positions_path = tmp_path / "positions.json"
    positions_path.write_text(json.dumps(positions))
    output = tmp_path / "native.json"
    subprocess.run(
        [
            sys.executable,
            str(SPIKE / "native_jarl_benchmark.py"),
            "--mode",
            "fixed_work",
            "--configs",
            "quick",
            "--warmup",
            "0",
            "--repetitions",
            "1",
            "--positions",
            str(positions_path),
            "--output",
            str(output),
        ],
        check=True,
        capture_output=True,
    )
    report = json.loads(output.read_text())
    measured = [row for row in report["records"] if row["phase"] == "measured"]
    assert measured
    for row in measured:
        assert "elapsed_ms" in row
        assert "wall_ms" not in row and "wall_clock_ms" not in row
    assert report["batch_wall_clock_seconds"] > 0
    # Identity must be recorded for a later comparison.
    assert report["identity"]["workload_digest"]
    assert report["identity"]["engine_identity"]["sha256"]


def test_duplicate_position_names_are_rejected(tmp_path):
    base = new_game("brandubh").to_dict()
    positions = [
        {"name": "dup", "kind": "custom", "state": base},
        {"name": "dup", "kind": "custom", "state": base},
    ]
    path = tmp_path / "positions.json"
    path.write_text(json.dumps(positions))
    result = subprocess.run(
        [
            sys.executable,
            str(SPIKE / "native_jarl_benchmark.py"),
            "--positions",
            str(path),
            "--output",
            str(tmp_path / "out.json"),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "unique" in result.stderr
