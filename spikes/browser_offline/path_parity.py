"""Validate full-logit versus legal-only browser neural MCTS path parity.

The browser runner only collects raw search results. This module applies the
shared Python engine's legality checks and all per-case acceptance rules, so an
individual failure can never be reported as successful and the standalone CLI
exits nonzero when any required case fails.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

SPIKE = Path(__file__).resolve().parent
ROOT = SPIKE.parents[1]
for path in (str(SPIKE), str(ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

from hnefatafl.engine import RULESETS, Move, State, legal_moves  # noqa: E402
from hnefatafl.mcts_driver import position_identity, validate_search_settings  # noqa: E402

SCHEMA = 1
REQUIRED_CATEGORIES = ("opening", "tactical", "repetition", "terminal")
VALUE_TOLERANCE = 1e-12
TRANSPORT_FIELDS = ("elapsed_ms", "timing", "hard_deadline", "coordinator", "search_id")


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _is_int(value: Any) -> bool:
    return type(value) is int


def _finite(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
    )


def _projection(result: dict) -> dict:
    search = dict(result.get("search") or {})
    for field in TRANSPORT_FIELDS:
        search.pop(field, None)
    return {"analysis": result.get("analysis"), "search": search}


def _move(move: Any) -> Move | None:
    if not isinstance(move, dict):
        return None
    source, target = move.get("source"), move.get("target")
    if not _is_int(source) or not _is_int(target):
        return None
    return Move(source, target)


def _validate_legality(case: dict, failures: list[str], state: State) -> None:
    terminal = state.winner is not None
    for label in ("full", "compact"):
        move_raw = case.get(f"{label}_move")
        move = _move(move_raw)
        if terminal:
            if move_raw is not None:
                failures.append(f"{label}: terminal position reported a best move")
            continue
        if move is None:
            failures.append(f"{label}: completed non-terminal search has no best move")
        elif move not in legal_moves(state):
            failures.append(f"{label}: best move {move.source}->{move.target} is not legal")


def _validate_common(case: dict, failures: list[str]) -> None:
    full, compact = case.get("full"), case.get("compact")
    if not isinstance(full, dict) or not isinstance(compact, dict):
        failures.append("missing full or compact search result")
        return
    full_search = full.get("search") or {}
    compact_search = compact.get("search") or {}
    field_pairs = (
        ("model identity", full_search.get("model"), compact_search.get("model")),
        (
            "position identity",
            full_search.get("position_identity"),
            compact_search.get("position_identity"),
        ),
        ("provider", full_search.get("provider"), compact_search.get("provider")),
        ("ruleset", full_search.get("ruleset"), compact_search.get("ruleset")),
        ("board size", full_search.get("size"), compact_search.get("size")),
    )
    for label, left, right in field_pairs:
        if _canonical(left) != _canonical(right):
            failures.append(f"{label} differs between full and compact paths")
    terminal = case.get("terminal") is True
    full_mode = (full_search.get("coordinator") or {}).get("inference_payload_mode")
    compact_mode = (compact_search.get("coordinator") or {}).get("inference_payload_mode")
    if terminal:
        if full_mode is not None or compact_mode is not None:
            failures.append("terminal search reported an inference payload")
    else:
        if full_mode != "full-logits":
            failures.append("full path did not report full-logits")
        if compact_mode != "legal-only":
            failures.append("compact path did not report legal-only")
    full_values = (full_search.get("coordinator") or {}).get("response_policy_values")
    if full_values is None or not _is_int(full_values):
        failures.append("full path has no policy-value count")
    elif full_values < 0:
        failures.append("full path policy-value count is negative")
    compact_values = (compact_search.get("coordinator") or {}).get("response_policy_values")
    if compact_values != 0:
        failures.append("compact path transferred policy values")


def _validate_fixed_work(case: dict, failures: list[str]) -> None:
    settings = case.get("settings") or {}
    requested = settings.get("iterations")
    full, compact = case.get("full") or {}, case.get("compact") or {}
    full_search, compact_search = full.get("search") or {}, compact.get("search") or {}
    full_analysis, compact_analysis = full.get("analysis") or {}, compact.get("analysis") or {}
    terminal = case.get("terminal") is True
    immediate = full_search.get("completion_reason") == "immediate_win"
    if not terminal and not immediate:
        for label, search in (("full", full_search), ("compact", compact_search)):
            completed = search.get("completed_simulations")
            if not _is_int(completed) or completed != requested:
                failures.append(f"{label}: completed {completed!r} of {requested!r} simulations")
    for field in (
        "completed_simulations",
        "neural_requests",
        "prediction_reuses",
        "completion_reason",
        "deadline_cutoff",
        "position_identity",
    ):
        if _canonical(full_search.get(field)) != _canonical(compact_search.get(field)):
            failures.append(f"{field} differs between full and compact paths")
    if _canonical(full_search.get("root_visits")) != _canonical(compact_search.get("root_visits")):
        failures.append("root visit distribution differs between full and compact paths")
    if _canonical(full_analysis.get("best_move")) != _canonical(compact_analysis.get("best_move")):
        failures.append("best move differs between full and compact paths")
    full_value, compact_value = full_analysis.get("value"), compact_analysis.get("value")
    if not _finite(full_value) or not _finite(compact_value):
        failures.append("defender-relative value is not finite")
    elif abs(float(full_value) - float(compact_value)) > VALUE_TOLERANCE:
        failures.append("defender-relative value differs between full and compact paths")
    if _canonical(_projection(full)) != _canonical(_projection(compact)):
        failures.append("search result payload differs between full and compact paths")
    if terminal:
        if (
            full_search.get("completed_simulations") != 0
            or compact_search.get("completed_simulations") != 0
        ):
            failures.append("terminal search reported completed simulations")
        if full_search.get("completion_reason") not in ("terminal", "immediate_win"):
            failures.append("terminal search has an inconsistent completion reason")
    if immediate:
        expected = 1.0 if (case.get("state") or {}).get("turn") == "defenders" else -1.0
        if not _finite(full_value) or abs(float(full_value) - expected) > VALUE_TOLERANCE:
            failures.append("immediate-win value is not defender-relative")


def _validate_fixed_time(case: dict, failures: list[str]) -> None:
    settings = case.get("settings") or {}
    requested = settings.get("iterations")
    budget_ms = float(settings.get("time_limit", 0)) * 1000.0
    terminal = case.get("terminal") is True
    for label, result in (("full", case.get("full") or {}), ("compact", case.get("compact") or {})):
        search = result.get("search") or {}
        completed = search.get("completed_simulations")
        if not _is_int(completed) or completed < 0:
            failures.append(f"{label}: invalid completed work")
            continue
        immediate = search.get("completion_reason") == "immediate_win"
        if not terminal and not immediate:
            if not search.get("deadline_cutoff") and completed != requested:
                failures.append(f"{label}: fixed-time run has no deadline cutoff")
            if search.get("completion_reason") != "deadline" and completed != requested:
                failures.append(f"{label}: fixed-time completion reason is inconsistent")
        elapsed = (search.get("coordinator") or {}).get("end_to_end_ms")
        if not _finite(elapsed) or float(elapsed) < 0:
            failures.append(f"{label}: search timing is missing or invalid")
        elif not terminal and not immediate and float(elapsed) < budget_ms * 0.5:
            failures.append(f"{label}: search timing is implausibly short")


def _case_failures(case: dict, expected_model: dict | None) -> tuple[list[str], str | None]:
    failures: list[str] = []
    execution_error = case.get("execution_error")
    if execution_error is not None:
        return failures, str(execution_error)
    if not isinstance(case.get("full"), dict) or not isinstance(case.get("compact"), dict):
        return failures, "missing full or compact search result"
    state_raw = case.get("state")
    try:
        state = State.from_dict(state_raw)
    except (ValueError, KeyError, TypeError) as error:
        return failures, f"invalid case state: {error}"
    _validate_common(case, failures)
    search = case["full"].get("search") or {}
    if expected_model is not None:
        model = search.get("model") or {}
        for field in ("sha256", "ruleset", "size"):
            if model.get(field) != expected_model.get(field):
                failures.append(f"model {field} does not match the declared model")
    try:
        settings = validate_search_settings(case.get("settings") or {})
        expected_position = position_identity(state, settings, search.get("model"))
    except (ValueError, KeyError, TypeError) as error:
        failures.append(f"cannot recompute position identity: {error}")
    else:
        if search.get("position_identity") != expected_position:
            failures.append("position identity does not match the recomputed identity")
    _validate_legality(case, failures, state)
    if case.get("mode") == "fixed_simulations":
        _validate_fixed_work(case, failures)
    elif case.get("mode") == "fixed_time":
        _validate_fixed_time(case, failures)
    else:
        failures.append("unknown search mode")
    return failures, None


def _coverage(report: dict, synthetic: list[dict]) -> list[str]:
    failures: list[str] = []
    models = report.get("models") or []
    rulesets = [entry.get("ruleset") for entry in models if isinstance(entry, dict)]
    if not rulesets:
        return ["no model coverage was declared"]
    if len(set(rulesets)) != len(rulesets):
        failures.append("duplicate model ruleset coverage")
    expected_rulesets = report.get("expected_rulesets") or []
    if not expected_rulesets:
        failures.append("no expected ruleset coverage was declared")
    elif set(expected_rulesets) != set(rulesets):
        failures.append("expected ruleset coverage does not match the run")
    if len(report.get("roots") or []) >= 3 and set(rulesets) != set(RULESETS):
        failures.append("a complete run must cover all supported rulesets")
    expected_cases = report.get("expected_cases") or []
    actual_names = {
        (case.get("ruleset"), case.get("position")) for case in report.get("cases") or []
    }
    for entry in expected_cases:
        key = (entry.get("ruleset"), entry.get("name"))
        if key not in actual_names:
            failures.append(f"missing expected position {key[1]!r} for {key[0]}")
            synthetic.append(
                {
                    "ruleset": key[0],
                    "position": key[1],
                    "kind": "missing",
                    "mode": report.get("mode"),
                    "ok": False,
                    "failures": [f"missing expected position {key[1]!r}"],
                    "execution_error": "missing expected case",
                }
            )
    for ruleset in expected_rulesets:
        present = {
            case.get("kind") for case in report.get("cases") or [] if case.get("ruleset") == ruleset
        }
        for category in REQUIRED_CATEGORIES:
            if category not in present:
                failures.append(f"{ruleset}: missing required position category {category!r}")
                synthetic.append(
                    {
                        "ruleset": ruleset,
                        "position": f"{ruleset}-missing-{category}",
                        "kind": category,
                        "mode": report.get("mode"),
                        "ok": False,
                        "failures": [f"missing required position category {category!r}"],
                        "execution_error": "missing required category",
                    }
                )
        if ruleset == "brandubh":
            immediate = any(
                case.get("ruleset") == "brandubh"
                and case.get("position") == "brandubh-immediate-win"
                for case in report.get("cases") or []
            )
            if not immediate:
                failures.append("brandubh: missing immediate-win coverage")
                synthetic.append(
                    {
                        "ruleset": "brandubh",
                        "position": "brandubh-missing-immediate-win",
                        "kind": "tactical",
                        "mode": report.get("mode"),
                        "ok": False,
                        "failures": ["missing immediate-win coverage"],
                        "execution_error": "missing required case",
                    }
                )
    expected_count = len(expected_cases)
    if expected_count and len(report.get("cases") or []) != expected_count:
        failures.append("measured case count does not match expected coverage")
    return failures


def validate_report(report: dict) -> dict:
    failures: list[str] = []
    execution_failures: list[str] = []
    synthetic: list[dict] = []
    cases = report.get("cases")
    if not isinstance(cases, list) or not cases:
        failures.append("report has no measured cases")
        cases = []
    models = {
        entry.get("ruleset"): entry
        for entry in report.get("models") or []
        if isinstance(entry, dict)
    }
    for case in cases:
        if not isinstance(case, dict):
            failures.append("case entry is not an object")
            continue
        case_failures, execution_error = _case_failures(case, models.get(case.get("ruleset")))
        case["failures"] = case_failures
        case["execution_error"] = execution_error
        case["ok"] = execution_error is None and not case_failures
        if execution_error is not None:
            execution_failures.append(f"{case.get('position')}: {execution_error}")
        failures.extend(f"{case.get('position')}: {message}" for message in case_failures)
    coverage_failures = _coverage(report, synthetic)
    failures.extend(coverage_failures)
    cases.extend(synthetic)
    report["cases"] = cases
    report["failures"] = failures
    report["execution_failures"] = execution_failures
    report["coverage_failures"] = coverage_failures
    report["ok"] = not failures and not execution_failures and all(case.get("ok") for case in cases)
    report["authoritative"] = False
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = json.loads(args.report.read_text())
        if not isinstance(report, dict):
            raise ValueError("report must be an object")
    except (OSError, ValueError, TypeError) as error:
        print(json.dumps({"ok": False, "error": str(error)}), file=sys.stderr)
        sys.exit(2)
    validated = validate_report(report)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(validated, indent=2) + "\n")
    print(
        json.dumps(
            {
                "ok": validated["ok"],
                "cases": len(validated["cases"]),
                "failures": validated["failures"],
            },
            indent=2,
        )
    )
    sys.exit(0 if validated["ok"] else 1)


if __name__ == "__main__":
    main()
