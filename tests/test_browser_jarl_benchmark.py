"""Regression tests for the corrected Jarl benchmark comparison contract.

These exercise the canonical comparison module (``jarl_compare``) and the shared
workload identity (``jarl_benchmark``) without a browser. The real browser gate is
the opt-in ``verify_jarl.mjs`` run; this file proves the gate fails on regressions
instead of passing on first-repetition-only or plan-equality checks.
"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

SPIKE = Path(__file__).resolve().parents[1] / "spikes/browser_offline"
if str(SPIKE) not in sys.path:
    sys.path.insert(0, str(SPIKE))

import jarl_benchmark as jb  # noqa: E402
import jarl_compare as jc  # noqa: E402
from fixtures import jarl_positions  # noqa: E402
from hnefatafl.engine import State, new_game  # noqa: E402

POSITIONS = [
    case
    for case in jarl_positions()
    if case["name"] in ("brandubh-king-escape", "brandubh-terminal")
]
CONFIGS = ["quick"]


def make_report(
    *,
    positions=None,
    configs=None,
    mode="fixed_work",
    seed=7,
    repetitions=2,
    warmup=0,
    runtime="native",
):
    """Build a realistic report by running the shared benchmark module."""
    positions = positions or POSITIONS
    configs = configs or CONFIGS
    records: list[dict] = []
    for case in positions:
        state = State.from_dict(case["state"])
        digest = jb.position_digest(case)
        for config in configs:
            for _ in range(warmup):
                record = jb.run_case(
                    state,
                    config,
                    mode=mode,
                    seed=seed,
                    position=case.get("name"),
                    kind=case.get("kind"),
                    position_hash=digest,
                )
                record["phase"] = "warmup"
                records.append(record)
            for repetition in range(repetitions):
                record = jb.run_case(
                    state,
                    config,
                    mode=mode,
                    seed=seed,
                    position=case.get("name"),
                    kind=case.get("kind"),
                    position_hash=digest,
                )
                record["phase"] = "measured"
                record["repetition"] = repetition
                records.append(record)
    return jb.build_report(
        kind="test",
        runtime=runtime,
        environment={},
        positions=positions,
        configs=configs,
        mode=mode,
        seed=seed,
        repetitions=repetitions,
        warmup=warmup,
        records=records,
    )


@pytest.fixture(scope="module")
def golden():
    return make_report()


def _measured(report):
    return [row for row in report["records"] if row.get("phase") == "measured"]


def _measured_row(report, position, repetition):
    return next(
        row
        for row in report["records"]
        if row.get("phase") == "measured"
        and row.get("position") == position
        and row.get("repetition") == repetition
    )


def _legal_moves(report, position):
    case = next(
        case for case in report["identity"]["workload"]["cases"] if case["name"] == position
    )
    return [{"source": move["source"], "target": move["target"]} for move in case["legal_moves"]]


def _other_legal_move(report, position, current):
    for move in _legal_moves(report, position):
        if move != current:
            return move
    raise AssertionError("no alternative legal move")


def _illegal_move(report, position):
    legal = {json.dumps(move, sort_keys=True) for move in _legal_moves(report, position)}
    for source in range(64):
        for target in range(64):
            move = {"source": source, "target": target}
            if json.dumps(move, sort_keys=True) not in legal:
                return move
    raise AssertionError("no illegal move found")


def _mutated(report, fn):
    clone = copy.deepcopy(report)
    fn(clone)
    return clone


def test_identical_reports_establish_parity_and_acceptance(golden):
    result = jc.compare(copy.deepcopy(golden), copy.deepcopy(golden))
    assert result["ok"] is True, result["failures"]
    assert result["expected_cases"] == 4  # 2 positions x 1 config x 2 repetitions
    assert result["compared_cases"] == 4
    assert result["parity_cases"] == 2  # non-terminal x 2 repetitions
    assert result["parity_established"] == 2
    assert result["acceptance_complete"] is True
    assert result["unresolved"] == []


def test_incorrect_second_repetition_fails_parity(golden):
    def mutate(report):
        row = _measured_row(report, "brandubh-king-escape", 1)
        row["best_move"] = _other_legal_move(report, "brandubh-king-escape", row["best_move"])

    result = jc.compare(copy.deepcopy(golden), _mutated(golden, mutate))
    assert result["ok"] is False
    assert any(
        "parity at equal completed work differs" in message for message in result["failures"]
    )


def test_missing_later_repetition_fails_coverage(golden):
    def mutate(report):
        report["records"] = [
            row
            for row in report["records"]
            if not (
                row.get("phase") == "measured"
                and row.get("position") == "brandubh-king-escape"
                and row.get("repetition") == 1
            )
        ]

    result = jc.compare(copy.deepcopy(golden), _mutated(golden, mutate))
    assert result["ok"] is False
    assert any("actual report missing" in message for message in result["failures"])


def test_duplicate_measured_repetition_fails(golden):
    def mutate(report):
        report["records"].append(copy.deepcopy(_measured_row(report, "brandubh-king-escape", 0)))

    result = jc.compare(copy.deepcopy(golden), _mutated(golden, mutate))
    assert result["ok"] is False
    assert any("duplicate measured record" in message for message in result["failures"])


def test_both_reports_missing_the_same_expected_position_fails(golden):
    def mutate(report):
        report["records"] = [
            row for row in report["records"] if row.get("position") != "brandubh-terminal"
        ]

    expected = _mutated(golden, mutate)
    actual = _mutated(golden, mutate)
    result = jc.compare(expected, actual)
    assert result["ok"] is False
    assert any("expected report missing" in message for message in result["failures"])
    assert any("actual report missing" in message for message in result["failures"])


def test_both_reports_empty_fails(golden):
    def mutate(report):
        report["records"] = []

    result = jc.compare(_mutated(golden, mutate), _mutated(golden, mutate))
    assert result["ok"] is False
    assert any("no measured records" in message for message in result["failures"])


def test_missing_mandatory_identity_field_fails(golden):
    def mutate(report):
        del report["identity"]["seed"]

    result = jc.compare(copy.deepcopy(golden), _mutated(golden, mutate))
    assert result["ok"] is False
    assert any(
        "missing mandatory identity field 'seed'" in message for message in result["failures"]
    )


def test_changed_record_level_position_identity_fails(golden):
    def mutate(report):
        _measured_row(report, "brandubh-king-escape", 0)["position_hash"] = "0" * 64

    result = jc.compare(copy.deepcopy(golden), _mutated(golden, mutate))
    assert result["ok"] is False
    assert any("position hash does not match" in message for message in result["failures"])


def test_mismatched_repetition_counts_fail(golden):
    def mutate(report):
        report["identity"]["repetitions"] = 3

    result = jc.compare(copy.deepcopy(golden), _mutated(golden, mutate))
    assert result["ok"] is False
    assert any("identity.repetitions" in message for message in result["failures"])


def test_record_level_settings_seed_and_mode_are_validated(golden):
    for field, value in (
        ("seed", 8),
        ("mode", "fixed_time"),
        ("effective_config_digest", "0" * 64),
    ):

        def mutate(report, field=field, value=value):
            _measured_row(report, "brandubh-king-escape", 0)[field] = value

        result = jc.compare(copy.deepcopy(golden), _mutated(golden, mutate))
        assert result["ok"] is False, field
        assert any(
            "seed" in message or "mode" in message or "effective settings" in message
            for message in result["failures"]
        )


def test_terminal_value_disagreement_fails(golden):
    def mutate(report):
        row = _measured_row(report, "brandubh-terminal", 0)
        row["value"] = -row["value"] if row["value"] else -1.0

    result = jc.compare(copy.deepcopy(golden), _mutated(golden, mutate))
    assert result["ok"] is False
    assert any("terminal value" in message for message in result["failures"])


def test_illegal_best_move_fails(golden):
    def mutate(report):
        _measured_row(report, "brandubh-king-escape", 0)["best_move"] = _illegal_move(
            report, "brandubh-king-escape"
        )

    result = jc.compare(copy.deepcopy(golden), _mutated(golden, mutate))
    assert result["ok"] is False
    assert any("not legal" in message for message in result["failures"])


def test_both_side_execution_errors_fail(golden):
    def mutate(report):
        for repetition in (0, 1):
            row = _measured_row(report, "brandubh-king-escape", repetition)
            row["status"] = "error"
            row["error"] = "worker died"
            row["best_move"] = None
            row["value"] = None
            row["completed_depth"] = None

    expected = _mutated(golden, mutate)
    actual = _mutated(golden, mutate)
    result = jc.compare(expected, actual)
    assert result["ok"] is False
    assert any("execution error" in message for message in result["failures"])


def test_malformed_deadline_limited_result_fails(golden):
    def mutate(report):
        row = _measured_row(report, "brandubh-king-escape", 0)
        row["status"] = "deadline_limited"
        row["interrupted_depth"] = None
        row["completed_depth"] = 2

    result = jc.compare(copy.deepcopy(golden), _mutated(golden, mutate))
    assert result["ok"] is False
    assert any("no interrupted depth" in message for message in result["failures"])


def test_invalid_status_depth_combination_fails(golden):
    def mutate(report):
        row = _measured_row(report, "brandubh-king-escape", 0)
        row["status"] = "completed"
        row["interrupted_depth"] = 2

    result = jc.compare(copy.deepcopy(golden), _mutated(golden, mutate))
    assert result["ok"] is False
    assert any("also reports an interrupted depth" in message for message in result["failures"])


def test_unexpected_unsupported_configuration_fails(golden):
    def mutate(report):
        row = _measured_row(report, "brandubh-king-escape", 0)
        row["status"] = "unsupported"
        row["error"] = "ValueError: Search depth must be between 1 and 8"
        row["best_move"] = None
        row["completed_depth"] = None

    result = jc.compare(copy.deepcopy(golden), _mutated(golden, mutate))
    assert result["ok"] is False
    assert any("unexpected unsupported" in message for message in result["failures"])


def test_known_unsupported_depth_ten_is_reported_not_rejected():
    report = make_report(configs=["extended"], repetitions=1, positions=POSITIONS)
    result = jc.compare(copy.deepcopy(report), copy.deepcopy(report))
    assert result["ok"] is True, result["failures"]
    statuses = {row["expected_status"] for row in result["rows"]}
    assert "unsupported" in statuses
    assert result["acceptance_complete"] is False
    assert any(item["reason"] == "unsupported configuration" for item in result["unresolved"])


def test_asymmetric_completion_is_unresolved_not_a_regression(golden):
    def mutate(report):
        row = _measured_row(report, "brandubh-king-escape", 0)
        row["status"] = "deadline_limited"
        row["completed_depth"] = 2
        row["interrupted_depth"] = 3

    result = jc.compare(copy.deepcopy(golden), _mutated(golden, mutate))
    assert result["ok"] is True, result["failures"]
    assert result["acceptance_complete"] is False
    assert any(item["reason"] == "asymmetric completion" for item in result["unresolved"])


def test_different_work_counters_are_not_equal_work():
    expected = _report(
        [_record("brandubh-king-escape", "quick", "brandubh", nodes=120, evaluations=40)],
        mode="fixed_work",
    )
    actual = _report(
        [_record("brandubh-king-escape", "quick", "brandubh", nodes=118, evaluations=39)],
        mode="fixed_work",
    )
    result = jc.compare(expected, actual)
    assert result["ok"] is True, result["failures"]
    assert result["parity_cases"] == 0
    assert result["equal_work_latency_ratio_samples"] == 0
    assert result["acceptance_complete"] is False


def test_terminal_times_are_excluded_from_search_performance(golden):
    result = jc.compare(copy.deepcopy(golden), copy.deepcopy(golden))
    assert result["equal_work_latency_ratio_samples"] == 2  # non-terminal repetitions only


# ---------- synthetic report helper for focused validity tests ----------

EFFECTIVE = {"quick": {"algorithm": "alpha_beta", "depth": 3, "time_limit": 60.0}}


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
        "repetition": 0,
        "phase": "measured",
        "status": status,
        "terminal": status == "terminal",
        "unsupported": status == "unsupported",
        "best_move": None if status == "terminal" else (move or {"source": 1, "target": 2}),
        "value": 0.0 if status == "terminal" else value,
        "completed_depth": None if status in ("terminal", "error", "unsupported") else depth,
        "interrupted_depth": None,
        "expected_depth": 3,
        "effective_config_digest": jc._digest(EFFECTIVE["quick"]),
        "position_hash": "h",
        "elapsed_ms": 12.0,
        "nodes": 10,
        "evaluations": 5,
        "error": "boom" if status in ("error", "unsupported") else None,
    }
    record.update(extra)
    return record


def _report(records, *, mode="fixed_work"):
    case = {
        "name": "brandubh-king-escape",
        "kind": "opening",
        "ruleset": "brandubh",
        "position_digest": "h",
        "terminal": False,
        "winner": None,
        "terminal_value": None,
        "legal_moves": [{"source": 1, "target": 2}, {"source": 3, "target": 4}],
    }
    workload = {
        "protocol": 3,
        "digest": "d" * 64,
        "mode": mode,
        "seed": 7,
        "configs": ["quick"],
        "effective_configs": EFFECTIVE,
        "cases": [case],
    }
    identity = {
        "protocol": 3,
        "workload": workload,
        "workload_digest": workload["digest"],
        "mode": mode,
        "seed": 7,
        "repetitions": 1,
        "warmup": 0,
        "engine_identity": {"modules": ["engine"], "sha256": "b" * 64},
        "effective_configs": EFFECTIVE,
    }
    return {"schema": 3, "plan": jb.benchmark_plan(), "identity": identity, "records": records}


# ---------- workload identity (custom positions) ----------


def _custom_position(name, *, board_edit=None, history=None):
    base = new_game("brandubh").to_dict()
    if board_edit:
        base["board"] = board_edit(list(base["board"]))
    if history is not None:
        base["history"] = history
    return {"name": name, "kind": "custom", "state": base}


def test_same_name_with_different_board_changes_the_digest():
    first = _custom_position("collision")
    second = _custom_position("collision", board_edit=lambda board: board[:5] + ["A"] + board[6:])
    a = jb.workload_identity([first], ["quick"], mode="fixed_work", seed=7)
    b = jb.workload_identity([second], ["quick"], mode="fixed_work", seed=7)
    assert a["digest"] != b["digest"]


def test_same_board_with_different_repetition_history_changes_the_digest():
    plain = _custom_position("history")
    repeated = _custom_position(
        "history", history=list(plain["state"]["history"]) + list(plain["state"]["history"])
    )
    a = jb.workload_identity([plain], ["quick"], mode="fixed_work", seed=7)
    b = jb.workload_identity([repeated], ["quick"], mode="fixed_work", seed=7)
    assert a["digest"] != b["digest"]


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


# ---------- classification and per-case latency ----------


def test_classification_distinguishes_statuses():
    assert jb.classify({"terminal": True}) == jb.TERMINAL
    assert jb.classify({"unsupported": True}) == jb.UNSUPPORTED
    assert jb.classify({"error": "boom"}) == jb.ERROR
    assert jb.classify({"expected_depth": 5, "completed_depth": 5}) == jb.COMPLETED
    assert jb.classify({"expected_depth": 5, "completed_depth": 4}) == jb.INCOMPLETE
    assert (
        jb.classify({"expected_depth": 5, "completed_depth": 3, "interrupted_depth": 4})
        == jb.DEADLINE_LIMITED
    )
    assert jb.classify({"expected_depth": 5, "completed_depth": None}) == jb.INCOMPLETE


def test_native_report_has_per_case_latency_and_a_separate_batch_total(tmp_path):
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
    assert report["identity"]["workload_digest"]
    assert report["identity"]["engine_identity"]["sha256"]
    assert report["identity"]["repetitions"] == 1


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
