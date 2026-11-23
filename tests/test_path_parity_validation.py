"""Per-case acceptance for full-logit vs legal-only browser search parity.

Synthetic reports exercise the Python validator directly. The browser runner
only collects raw observations; this module owns case.ok/report.ok and shared
engine legality, so these negative tests do not need Chromium or ORT.
"""

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

import path_parity  # noqa: E402
from fixtures import jarl_positions  # noqa: E402
from hnefatafl.engine import State, legal_moves  # noqa: E402
from hnefatafl.mcts_driver import position_identity, validate_search_settings  # noqa: E402

MODEL = {"sha256": "a" * 64, "ruleset": "brandubh", "size": 7}


def _selection() -> list[dict]:
    wanted = {"brandubh-opening", "brandubh-tactical", "brandubh-repetition", "brandubh-terminal"}
    cases = [case for case in jarl_positions() if case["name"] in wanted]
    size = 7
    board = ["."] * (size * size)
    board[1] = "K"
    board[3] = "A"
    board[3 * size + 3] = "D"
    board[5 * size + 5] = "A"
    cases.append(
        {
            "name": "brandubh-immediate-win",
            "ruleset": "brandubh",
            "kind": "tactical",
            "state": {
                "ruleset": "brandubh",
                "board": board,
                "turn": "attackers",
                "ply": 0,
                "winner": None,
                "reason": None,
                "history": [],
            },
        }
    )
    return sorted(cases, key=lambda entry: entry["name"])


def _case(entry: dict, *, fixed_time: bool = False) -> dict:
    state = State.from_dict(entry["state"])
    terminal = state.winner is not None
    immediate = entry["name"] == "brandubh-immediate-win"
    moves = legal_moves(state)
    best = None if terminal else moves[0].to_dict()
    value = (
        1.0
        if state.winner == "defenders"
        else -1.0
        if state.winner == "attackers" or immediate
        else 0.25
    )
    completed = 0 if terminal or immediate else 64
    reason = (
        "terminal"
        if terminal
        else "immediate_win"
        if immediate
        else ("deadline" if fixed_time else "simulations")
    )
    root_visits = (
        []
        if terminal
        else [
            {"source": best["source"], "target": best["target"], "visits": completed, "share": 1.0}
        ]
    )
    settings = (
        {"iterations": 4096, "time_limit": 0.25, "seed": 7, "exploration": 1.4}
        if fixed_time
        else {"iterations": 64, "time_limit": 10, "seed": 7, "exploration": 1.4}
    )

    identity = position_identity(state, validate_search_settings(settings), MODEL)

    def result(mode: str) -> dict:
        search = {
            "algorithm": "neural-mcts-puct-v1",
            "encoder_version": "six-plane-v1",
            "driver_version": 1,
            "position_identity": identity,
            "ruleset": "brandubh",
            "size": 7,
            "model": dict(MODEL),
            "provider": {"requested": "wasm", "execution_placement": "wasm"},
            "seed": 7,
            "exploration": 1.4,
            "authoritative": False,
            "completed_simulations": completed,
            "requested_simulations": settings["iterations"],
            "neural_requests": 0 if terminal or immediate else completed + 1,
            "prediction_reuses": 0,
            "immediate_win_shortcuts": 1 if immediate else 0,
            "root_children": len(root_visits),
            "inference_cache_hits": 0,
            "completion_reason": reason,
            "deadline_cutoff": fixed_time and not terminal and not immediate,
            "root_visits": copy.deepcopy(root_visits),
            "best_move": copy.deepcopy(best),
            "coordinator": {
                "inference_payload_mode": None
                if terminal
                else ("legal-only" if mode == "compact" else "full-logits"),
                "response_policy_values": 0 if terminal or mode == "compact" else 100,
                "end_to_end_ms": 260.0 if fixed_time else 100.0,
            },
            "elapsed_ms": 90.0,
            "timing": {"whatever": 1},
            "hard_deadline": 20,
            "search_id": f"search-{mode}",
        }
        analysis = {
            "source": "terminal" if terminal else "neural_search",
            "value": value,
            "best_move": copy.deepcopy(best),
            "simulations": completed,
        }
        return {"analysis": analysis, "search": search}

    return {
        "ruleset": entry["ruleset"],
        "position": entry["name"],
        "kind": entry["kind"],
        "terminal": terminal,
        "mode": "fixed_time" if fixed_time else "fixed_simulations",
        "settings": settings,
        "state": entry["state"],
        "full": result("full"),
        "compact": result("compact"),
        "full_move": copy.deepcopy(best),
        "compact_move": copy.deepcopy(best),
        "ok": False,
    }


def _report(*, fixed_time: bool = False) -> dict:
    entries = _selection()
    cases = [_case(entry, fixed_time=fixed_time) for entry in entries]
    return {
        "kind": "mcts-search-path-parity-observations",
        "schema": 1,
        "roots": ["brandubh"],
        "expected_rulesets": ["brandubh"],
        "models": [{**MODEL, "provider_requested": "wasm", "execution_placement": "wasm"}],
        "expected_cases": [
            {"name": entry["name"], "ruleset": entry["ruleset"]} for entry in entries
        ],
        "cases": cases,
        "harness_failures": [],
        "raw_ok": True,
    }


def _coding(report) -> None:
    report["mode"] = report["cases"][0]["mode"]


def test_valid_synthetic_report_passes_with_independent_case_statuses():
    report = _report()
    report["mode"] = "fixed_simulations"
    validated = path_parity.validate_report(report)
    assert validated["ok"] is True, validated["failures"]
    assert validated["cases"] and all(case["ok"] is True for case in validated["cases"])
    assert validated["execution_failures"] == []
    assert validated["coverage_failures"] == []


@pytest.mark.parametrize(
    "mutation,fixed_time",
    [
        (
            lambda case: (
                case["full"]["analysis"].update(best_move={"source": 99, "target": 99}),
                case.update(full_move={"source": 99, "target": 99}),
            ),
            False,
        ),
        (
            lambda case: (
                case["full"]["analysis"].update(best_move=None),
                case["compact"]["analysis"].update(best_move=None),
                case.update(full_move=None, compact_move=None),
            ),
            False,
        ),
        (lambda case: case["compact"]["search"]["model"].update(sha256="c" * 64), False),
        (lambda case: case["compact"]["search"].update(position_identity="d" * 64), False),
        (lambda case: case["compact"]["search"]["root_visits"][0].update(visits=1), False),
        (lambda case: case["compact"]["search"].update(completed_simulations=63), False),
        (
            lambda case: case["compact"]["search"].update(
                deadline_cutoff=False, completion_reason="simulations"
            ),
            True,
        ),
    ],
)
def test_negative_case_mutations_are_individually_rejected(mutation, fixed_time):
    report = _report(fixed_time=fixed_time)
    report["mode"] = "fixed_time" if fixed_time else "fixed_simulations"
    # Apply the mutation to a completing non-terminal, non-immediate case.
    case = next(
        item
        for item in report["cases"]
        if not item["terminal"] and item["position"] != "brandubh-immediate-win"
    )
    mutation(case)
    validated = path_parity.validate_report(report)
    assert validated["ok"] is False
    assert case["ok"] is False
    assert case["failures"] or case["execution_error"]
    assert any(not other["ok"] for other in validated["cases"]) is False or case["ok"] is False


def test_missing_expected_position_and_category_are_reported_as_failed_cases():
    report = _report()
    report["mode"] = "fixed_simulations"
    # Remove the expected position and its case: the validator must add a failed case.
    removed = report["cases"].pop(0)
    validated = path_parity.validate_report(report)
    assert validated["ok"] is False
    missing = [
        case for case in validated["cases"] if case["execution_error"] == "missing expected case"
    ]
    assert missing and missing[0]["ok"] is False
    assert any(removed["position"] in failure for failure in validated["failures"])

    report = _report()
    report["mode"] = "fixed_simulations"
    # Remove a category and its expectation; coverage must synthesize a failed case.
    report["cases"] = [case for case in report["cases"] if case["kind"] != "repetition"]
    report["expected_cases"] = [
        entry for entry in report["expected_cases"] if "repetition" not in entry["name"]
    ]
    validated = path_parity.validate_report(report)
    assert validated["ok"] is False
    missing = [
        case
        for case in validated["cases"]
        if "missing required position category" in " ".join(case["failures"])
    ]
    assert missing and missing[0]["ok"] is False


def test_individual_failure_does_not_mark_other_cases_failed():
    report = _report()
    report["mode"] = "fixed_simulations"
    target = report["cases"][0]
    target["full"]["analysis"]["value"] = 0.5
    validated = path_parity.validate_report(report)
    assert validated["ok"] is False
    failed = [case for case in validated["cases"] if not case["ok"]]
    assert failed == [target]
    assert all(case["ok"] for case in validated["cases"] if case is not target)


def _run_cli(tmp_path: Path, report: dict) -> tuple[int, dict]:
    source = tmp_path / "observations.json"
    output = tmp_path / "validated.json"
    source.write_text(json.dumps(report))
    result = subprocess.run(
        [sys.executable, str(SPIKE / "path_parity.py"), str(source), "--output", str(output)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    return result.returncode, json.loads(output.read_text())


@pytest.mark.parametrize(
    "mutation,fixed_time",
    [
        (
            lambda case: (
                case["full"]["analysis"].update(best_move={"source": 99, "target": 99}),
                case.update(full_move={"source": 99, "target": 99}),
            ),
            False,
        ),
        (
            lambda case: (
                case["full"]["analysis"].update(best_move=None),
                case.update(full_move=None),
            ),
            False,
        ),
        (lambda case: case["compact"]["search"]["model"].update(sha256="c" * 64), False),
        (lambda case: case["compact"]["search"].update(position_identity="d" * 64), False),
        (lambda case: case["compact"]["search"]["root_visits"][0].update(visits=1), False),
        (lambda case: case["compact"]["search"].update(completed_simulations=63), False),
        (
            lambda case: case["compact"]["search"].update(
                deadline_cutoff=False, completion_reason="simulations"
            ),
            True,
        ),
    ],
)
def test_cli_exits_nonzero_and_records_case_failure(tmp_path, mutation, fixed_time):
    report = _report(fixed_time=fixed_time)
    report["mode"] = "fixed_time" if fixed_time else "fixed_simulations"
    case = next(
        item
        for item in report["cases"]
        if not item["terminal"] and item["position"] != "brandubh-immediate-win"
    )
    mutation(case)
    code, validated = _run_cli(tmp_path, report)
    assert code != 0
    assert validated["ok"] is False
    assert any(not item["ok"] for item in validated["cases"])


def test_cli_missing_position_and_category_exit_nonzero(tmp_path):
    report = _report()
    report["mode"] = "fixed_simulations"
    removed = report["cases"].pop(0)
    code, validated = _run_cli(tmp_path, report)
    assert code != 0
    assert any(removed["position"] in failure for failure in validated["failures"])
    assert any(case["execution_error"] == "missing expected case" for case in validated["cases"])

    report = _report()
    report["mode"] = "fixed_simulations"
    report["cases"] = [case for case in report["cases"] if case["kind"] != "repetition"]
    report["expected_cases"] = [
        entry for entry in report["expected_cases"] if "repetition" not in entry["name"]
    ]
    code, validated = _run_cli(tmp_path, report)
    assert code != 0
    assert any(
        case["execution_error"] == "missing required category" for case in validated["cases"]
    )


def _fixed_time_case(report: dict) -> dict:
    return next(
        case
        for case in report["cases"]
        if not case["terminal"] and case["position"] != "brandubh-immediate-win"
    )


def _set_completed(case: dict, completed: int) -> None:
    for label in ("full", "compact"):
        result = case[label]
        result["search"]["completed_simulations"] = completed
        result["analysis"]["simulations"] = completed
        if result["search"]["root_visits"]:
            result["search"]["root_visits"][0]["visits"] = max(0, completed)
            result["search"]["root_visits"][0]["share"] = 1.0


def test_valid_fixed_time_deadline_budget_and_different_work():
    report = _report(fixed_time=True)
    report["mode"] = "fixed_time"
    validated = path_parity.validate_report(report)
    assert validated["ok"] is True, validated["failures"]

    # Completing the requested simulation budget before the deadline is valid.
    report = _report(fixed_time=True)
    report["mode"] = "fixed_time"
    case = _fixed_time_case(report)
    _set_completed(case, case["settings"]["iterations"])
    case["full"]["search"].update(deadline_cutoff=False, completion_reason="simulations")
    case["compact"]["search"].update(deadline_cutoff=False, completion_reason="simulations")
    validated = path_parity.validate_report(report)
    assert validated["ok"] is True, validated["failures"]

    # Fixed time may complete different valid amounts of work on each path.
    report = _report(fixed_time=True)
    report["mode"] = "fixed_time"
    case = _fixed_time_case(report)
    _set_completed(case, 32)
    validated = path_parity.validate_report(report)
    assert validated["ok"] is True, validated["failures"]


@pytest.mark.parametrize(
    "mutation",
    [
        lambda case: _set_completed(case, case["settings"]["iterations"] + 1),
        lambda case: _set_completed(case, -1),
        lambda case: case["compact"]["analysis"].update(simulations=63),
        lambda case: case["compact"]["search"]["root_visits"][0].update(visits=1),
        lambda case: case["compact"]["search"].update(
            deadline_cutoff=False, completion_reason="simulations"
        ),
        lambda case: case["compact"]["search"].update(deadline_cutoff=None),
        lambda case: case["compact"]["search"].pop("deadline_cutoff"),
        lambda case: _set_completed(case, 4097),
    ],
)
def test_fixed_time_accounting_rejects_impossible_reports(mutation):
    report = _report(fixed_time=True)
    report["mode"] = "fixed_time"
    case = _fixed_time_case(report)
    mutation(case)
    validated = path_parity.validate_report(report)
    assert validated["ok"] is False
    assert case["ok"] is False
    assert case["failures"] or case["execution_error"]


def test_fixed_time_terminal_and_immediate_shortcuts_remain_valid():
    report = _report(fixed_time=True)
    report["mode"] = "fixed_time"
    validated = path_parity.validate_report(report)
    terminal = next(case for case in validated["cases"] if case["terminal"])
    immediate = next(
        case for case in validated["cases"] if case["position"] == "brandubh-immediate-win"
    )
    assert terminal["ok"] is True
    assert immediate["ok"] is True
    assert terminal["full"]["search"]["completed_simulations"] == 0
    assert immediate["full"]["search"]["immediate_win_shortcuts"] == 1


def _set_path(report: dict, path: tuple, value) -> None:
    target = report
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value


@pytest.mark.parametrize(
    "path,value",
    [
        (("schema",), 2),
        (("harness_failures",), "not-a-list"),
        (("raw_ok",), "yes"),
        (("models",), [None]),
        (("models",), "not-a-list"),
        (("expected_rulesets",), "brandubh"),
        (("expected_cases",), "not-a-list"),
        (("roots",), [1]),
        (("cases",), ["not-a-case"]),
        (("cases", 0, "full"), None),
        (("cases", 0, "compact"), []),
        (("cases", 0, "full", "search"), None),
        (("cases", 0, "full", "analysis"), []),
        (("cases", 0, "full", "search", "coordinator"), "invalid"),
        (("cases", 0, "full", "search", "model"), []),
        (("cases", 0, "full", "search", "provider"), []),
        (("cases", 0, "settings"), "invalid"),
        (("cases", 0, "state"), []),
    ],
)
def test_malformed_nested_reports_fail_safely(tmp_path, path, value):
    report = _report()
    report["mode"] = "fixed_simulations"
    _set_path(report, path, value)
    validated = path_parity.validate_report(report)
    assert validated["ok"] is False
    assert validated["failures"]
    code, cli_report = _run_cli(tmp_path, report)
    assert code != 0
    assert cli_report["ok"] is False
    assert cli_report["failures"]


def _collector_report(failures: list, raw_ok) -> dict:
    report = _report()
    report["mode"] = "fixed_simulations"
    report["harness_failures"] = failures
    report["raw_ok"] = raw_ok
    return report


def test_collector_failures_prevent_acceptance_without_discarding_cases():
    report = _collector_report(["pageerror: uncaught after all searches"], False)
    validated = path_parity.validate_report(report)
    assert validated["ok"] is False
    assert validated["collector_ok"] is False
    assert validated["collector_failures"] == [
        "browser collector reported raw_ok=false",
        "pageerror: uncaught after all searches",
    ]
    assert all(case["ok"] is True for case in validated["cases"])
    assert any(
        "pageerror: uncaught after all searches" in failure for failure in validated["failures"]
    )


def test_raw_ok_false_without_failure_message_is_rejected():
    report = _collector_report([], False)
    validated = path_parity.validate_report(report)
    assert validated["ok"] is False
    assert "browser collector reported raw_ok=false without a failure message" in " ".join(
        validated["collector_failures"]
    )


def test_harness_failures_with_raw_ok_true_is_rejected():
    report = _collector_report(["collector: late failure"], True)
    validated = path_parity.validate_report(report)
    assert validated["ok"] is False
    assert any("raw_ok=true" in failure for failure in validated["collector_failures"])


@pytest.mark.parametrize(
    "failures,raw_ok",
    [
        (["late collector failure"], False),
        ([], False),
        (["late collector failure"], True),
        ("not-a-list", False),
        ([1], False),
        ([], "true"),
        ([], None),
    ],
)
def test_collector_failures_exit_nonzero(tmp_path, failures, raw_ok):
    report = _collector_report(failures, raw_ok)
    code, validated = _run_cli(tmp_path, report)
    assert code != 0
    assert validated["ok"] is False
    assert validated["failures"]
    assert all(case["ok"] is True for case in validated["cases"])


def test_one_failed_case_does_not_hide_other_successes_or_collector_errors():
    report = _report()
    report["mode"] = "fixed_simulations"
    target = report["cases"][0]
    target["full"]["analysis"]["value"] = 0.5
    report["harness_failures"] = ["collector failure after case"]
    report["raw_ok"] = False
    validated = path_parity.validate_report(report)
    assert validated["ok"] is False
    failed = [case for case in validated["cases"] if not case["ok"]]
    assert failed == [target]
    assert any("collector failure after case" in failure for failure in validated["failures"])
