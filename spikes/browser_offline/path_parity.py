"""Validate full-logit versus legal-only browser neural MCTS path parity.

The browser runner only collects raw search results. This module applies the
shared Python engine's legality checks and every per-case acceptance rule, so an
individual failure can never be reported as successful and the standalone CLI
exits nonzero when any required case or collector step fails.
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
COMPLETION_REASONS = {
    "simulations",
    "deadline",
    "immediate_win",
    "terminal",
    "insufficient_budget",
    "completed",
}
VALUE_TOLERANCE = 1e-12
TRANSPORT_FIELDS = ("elapsed_ms", "timing", "hard_deadline", "coordinator", "search_id")


def _canonical(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        return f"<invalid:{type(value).__name__}>"


def _is_int(value: Any) -> bool:
    return type(value) is int


def _finite(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
    )


def _hex64(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _object(value: Any, label: str, failures: list[str]) -> dict:
    if isinstance(value, dict):
        return value
    failures.append(f"{label} must be an object")
    return {}


def _projection(result: dict) -> dict:
    search = dict(result.get("search") or {}) if isinstance(result.get("search"), dict) else {}
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


def _validate_root_visits(search: dict, completed: int, label: str, failures: list[str]) -> None:
    visits = search.get("root_visits")
    if not isinstance(visits, list):
        failures.append(f"{label}: root_visits must be a list")
        return
    if completed <= 0:
        return
    if not visits:
        failures.append(f"{label}: completed search reported no root visits")
        return
    total = 0
    share_total = 0.0
    seen: set[tuple[int, int]] = set()
    for row in visits:
        if not isinstance(row, dict):
            failures.append(f"{label}: root visit entry must be an object")
            continue
        source, target = row.get("source"), row.get("target")
        if not _is_int(source) or not _is_int(target):
            failures.append(f"{label}: root visit action is malformed")
            continue
        key = (source, target)
        if key in seen:
            failures.append(f"{label}: duplicate root visit action {key}")
            continue
        seen.add(key)
        count = row.get("visits")
        share = row.get("share")
        if not _is_int(count) or count < 0:
            failures.append(f"{label}: root visit count must be a non-negative integer")
            continue
        if not _finite(share) or not 0 <= float(share) <= 1:
            failures.append(f"{label}: root visit share must be within [0, 1]")
            continue
        total += count
        share_total += float(share)
    tolerance = max(1, len(visits) // 2 + 1)
    if abs(total - completed) > tolerance:
        failures.append(
            f"{label}: root visit counts ({total}) are inconsistent with completed work ({completed})"
        )
    if abs(share_total - 1.0) > 1e-6:
        failures.append(f"{label}: root visit shares do not sum to one")


def _validate_terminal_or_immediate(
    search: dict,
    analysis: dict,
    label: str,
    terminal: bool,
    failures: list[str],
) -> bool:
    reason = search.get("completion_reason")
    cutoff = search.get("deadline_cutoff")
    if terminal:
        if search.get("completed_simulations") != 0:
            failures.append(f"{label}: terminal search reported completed simulations")
        if analysis.get("simulations") not in (None, 0):
            failures.append(f"{label}: terminal analysis reported simulations")
        if reason not in ("terminal", "immediate_win"):
            failures.append(f"{label}: terminal search has an inconsistent completion reason")
        if cutoff is not False:
            failures.append(f"{label}: terminal search must not report a deadline cutoff")
        return True
    if reason == "immediate_win":
        if search.get("completed_simulations") != 0:
            failures.append(f"{label}: immediate win reported completed simulations")
        if analysis.get("simulations") not in (None, 0):
            failures.append(f"{label}: immediate win reported analysis simulations")
        if search.get("immediate_win_shortcuts") != 1:
            failures.append(f"{label}: immediate win did not record its shortcut")
        if cutoff is not False:
            failures.append(f"{label}: immediate win must not report a deadline cutoff")
        return True
    return False


def _validate_common(case: dict, failures: list[str]) -> None:
    full, compact = case.get("full"), case.get("compact")
    if not isinstance(full, dict) or not isinstance(compact, dict):
        failures.append("full and compact results must both be objects")
        return
    full_search = _object(full.get("search"), "full.search", failures)
    compact_search = _object(compact.get("search"), "compact.search", failures)
    _object(full.get("analysis"), "full.analysis", failures)
    _object(compact.get("analysis"), "compact.analysis", failures)
    full_coordinator = _object(full_search.get("coordinator"), "full.search.coordinator", failures)
    compact_coordinator = _object(
        compact_search.get("coordinator"), "compact.search.coordinator", failures
    )
    for label, search in (("full", full_search), ("compact", compact_search)):
        _object(search.get("model"), f"{label}.search.model", failures)
        provider = search.get("provider")
        if provider is not None and not isinstance(provider, dict):
            failures.append(f"{label}.search.provider must be an object or null")
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
    full_mode = full_coordinator.get("inference_payload_mode")
    compact_mode = compact_coordinator.get("inference_payload_mode")
    if terminal:
        if full_mode is not None or compact_mode is not None:
            failures.append("terminal search reported an inference payload")
    else:
        if full_mode != "full-logits":
            failures.append("full path did not report full-logits")
        if compact_mode != "legal-only":
            failures.append("compact path did not report legal-only")
    full_values = full_coordinator.get("response_policy_values")
    if not _is_int(full_values):
        failures.append("full path has no integer policy-value count")
    elif full_values < 0:
        failures.append("full path policy-value count is negative")
    compact_values = compact_coordinator.get("response_policy_values")
    if compact_values != 0:
        failures.append("compact path transferred policy values")


def _validate_fixed_work(case: dict, failures: list[str]) -> None:
    settings = case.get("settings") or {}
    requested = settings.get("iterations")
    full, compact = case.get("full") or {}, case.get("compact") or {}
    full_search = full.get("search") if isinstance(full.get("search"), dict) else {}
    compact_search = compact.get("search") if isinstance(compact.get("search"), dict) else {}
    full_analysis = full.get("analysis") if isinstance(full.get("analysis"), dict) else {}
    compact_analysis = compact.get("analysis") if isinstance(compact.get("analysis"), dict) else {}
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
    for label, search, analysis in (
        ("full", full_search, full_analysis),
        ("compact", compact_search, compact_analysis),
    ):
        _validate_terminal_or_immediate(search, analysis, label, terminal, failures)
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
        search = result.get("search") if isinstance(result.get("search"), dict) else {}
        analysis = result.get("analysis") if isinstance(result.get("analysis"), dict) else {}
        coordinator = (
            search.get("coordinator") if isinstance(search.get("coordinator"), dict) else {}
        )
        completed = search.get("completed_simulations")
        if not _is_int(completed):
            failures.append(f"{label}: completed simulations must be an integer")
            continue
        if completed < 0:
            failures.append(f"{label}: completed simulations are negative")
        if not _is_int(requested) or not 0 <= completed <= requested:
            failures.append(
                f"{label}: completed simulations {completed!r} exceed the requested {requested!r}"
            )
        requested_field = search.get("requested_simulations")
        if not _is_int(requested_field) or requested_field != requested:
            failures.append(f"{label}: requested_simulations is inconsistent with settings")
        cutoff = search.get("deadline_cutoff")
        if not isinstance(cutoff, bool):
            failures.append(f"{label}: deadline_cutoff must be a boolean")
        reason = search.get("completion_reason")
        if reason not in COMPLETION_REASONS:
            failures.append(f"{label}: unsupported completion reason {reason!r}")
        special = _validate_terminal_or_immediate(search, analysis, label, terminal, failures)
        if not special:
            analysis_simulations = analysis.get("simulations")
            if completed > 0 and analysis_simulations != completed:
                failures.append(f"{label}: analysis simulations disagree with completed work")
            elif completed == 0 and analysis_simulations not in (None, 0):
                failures.append(f"{label}: analysis simulations disagree with completed work")
            if cutoff is True and reason != "deadline":
                failures.append(f"{label}: deadline cutoff does not match the completion reason")
            if reason == "deadline" and cutoff is not True:
                failures.append(f"{label}: deadline reason lacks a cutoff")
            if completed < requested and reason not in ("deadline", "insufficient_budget"):
                failures.append(f"{label}: incomplete fixed-time work has no deadline reason")
        _validate_root_visits(search, completed, label, failures)
        elapsed = coordinator.get("end_to_end_ms")
        if not _finite(elapsed) or float(elapsed) < 0:
            failures.append(f"{label}: search timing is missing or invalid")
        elif not terminal and reason != "immediate_win" and float(elapsed) < budget_ms * 0.5:
            failures.append(f"{label}: search timing is implausibly short")


def _case_failures(case: dict, expected_model: dict | None) -> tuple[list[str], str | None]:
    failures: list[str] = []
    execution_error = case.get("execution_error")
    if execution_error is not None:
        return failures, str(execution_error)
    full, compact = case.get("full"), case.get("compact")
    if not isinstance(full, dict) or not isinstance(compact, dict):
        failures.append("full and compact results must both be objects")
        return failures, None
    state_raw = case.get("state")
    try:
        state = State.from_dict(state_raw)
    except (ValueError, KeyError, TypeError) as error:
        return failures, f"invalid case state: {error}"
    settings_raw = case.get("settings")
    if not isinstance(settings_raw, dict):
        failures.append("settings must be an object")
        return failures, None
    try:
        settings = validate_search_settings(settings_raw)
    except (ValueError, KeyError, TypeError) as error:
        failures.append(f"invalid settings: {error}")
        return failures, None
    _validate_common(case, failures)
    search = full.get("search") if isinstance(full.get("search"), dict) else {}
    if expected_model is not None:
        model = search.get("model") if isinstance(search.get("model"), dict) else {}
        for field in ("sha256", "ruleset", "size"):
            if model.get(field) != expected_model.get(field):
                failures.append(f"model {field} does not match the declared model")
    try:
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
    models_raw = report.get("models")
    if not isinstance(models_raw, list) or not models_raw:
        return ["model coverage must be a non-empty list"]
    models = []
    for index, entry in enumerate(models_raw):
        if not isinstance(entry, dict):
            failures.append(f"model coverage entry {index} must be an object")
            continue
        if entry.get("ruleset") not in RULESETS:
            failures.append(f"model coverage entry {index} has an unsupported ruleset")
        if not _is_int(entry.get("size")) or not 5 <= int(entry["size"]) <= 15:
            failures.append(f"model coverage entry {index} has an invalid size")
        if not _hex64(entry.get("sha256")):
            failures.append(f"model coverage entry {index} has an invalid digest")
        models.append(entry)
    rulesets = [entry.get("ruleset") for entry in models]
    if len(set(rulesets)) != len(rulesets):
        failures.append("duplicate model ruleset coverage")
    expected_rulesets = report.get("expected_rulesets")
    if not isinstance(expected_rulesets, list) or not expected_rulesets:
        failures.append("expected_rulesets must be a non-empty list")
        expected_rulesets = []
    elif any(name not in RULESETS for name in expected_rulesets):
        failures.append("expected_rulesets contains an unsupported ruleset")
        expected_rulesets = [name for name in expected_rulesets if name in RULESETS]
    elif len(set(expected_rulesets)) != len(expected_rulesets):
        failures.append("expected_rulesets contains duplicates")
    if set(expected_rulesets) != set(rulesets):
        failures.append("expected ruleset coverage does not match the run")
    roots = report.get("roots")
    if not isinstance(roots, list) or any(not isinstance(root, str) for root in roots):
        failures.append("roots must be a list of strings")
        roots = []
    if len(roots) >= 3 and set(rulesets) != set(RULESETS):
        failures.append("a complete run must cover all supported rulesets")
    expected_cases = report.get("expected_cases")
    if not isinstance(expected_cases, list):
        failures.append("expected_cases must be a list")
        expected_cases = []
    valid_expected = []
    for index, entry in enumerate(expected_cases):
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("name"), str)
            or not entry.get("name")
            or entry.get("ruleset") not in RULESETS
        ):
            failures.append(f"expected case entry {index} is malformed")
            continue
        valid_expected.append(entry)
    cases = report.get("cases") if isinstance(report.get("cases"), list) else []
    actual_names = {
        (case.get("ruleset"), case.get("position")) for case in cases if isinstance(case, dict)
    }
    for entry in valid_expected:
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
    observed_names = [
        (case.get("ruleset"), case.get("position")) for case in cases if isinstance(case, dict)
    ]
    if len(set(observed_names)) != len(observed_names):
        failures.append("measured cases contain duplicate position identifiers")
    for ruleset in expected_rulesets:
        present = {
            case.get("kind")
            for case in cases
            if isinstance(case, dict) and case.get("ruleset") == ruleset
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
                for case in cases
                if isinstance(case, dict)
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
    if len(valid_expected) and len(cases) != len(valid_expected):
        failures.append("measured case count does not match expected coverage")
    return failures


def _collector_failures(report: dict, failures: list[str]) -> list[str]:
    collector: list[str] = []
    raw_ok = report.get("raw_ok")
    if not isinstance(raw_ok, bool):
        failures.append("raw_ok must be a boolean")
    harness = report.get("harness_failures")
    if not isinstance(harness, list) or any(
        not isinstance(message, str) or not message for message in harness
    ):
        failures.append("harness_failures must be a list of non-empty strings")
        harness = []
    if raw_ok is False:
        collector.append("browser collector reported raw_ok=false")
    collector.extend(harness)
    if raw_ok is True and harness:
        collector.append("browser collector reported failures but set raw_ok=true")
    if raw_ok is False and not harness:
        collector.append("browser collector reported raw_ok=false without a failure message")
    return collector


def validate_report(report: dict) -> dict:
    failures: list[str] = []
    execution_failures: list[str] = []
    synthetic: list[dict] = []
    if not isinstance(report, dict):
        return {
            "ok": False,
            "cases": [],
            "failures": ["report must be an object"],
            "execution_failures": [],
            "collector_failures": [],
            "coverage_failures": [],
            "case_failures": [],
            "authoritative": False,
        }
    if report.get("schema") != SCHEMA:
        failures.append(f"unsupported report schema {report.get('schema')!r}")
    collector = _collector_failures(report, failures)
    failures.extend(f"collector: {message}" for message in collector)
    cases = report.get("cases")
    if not isinstance(cases, list) or not cases:
        failures.append("report has no measured cases")
        cases = []
    models_raw = report.get("models")
    models = {}
    if isinstance(models_raw, list):
        for entry in models_raw:
            if isinstance(entry, dict) and isinstance(entry.get("ruleset"), str):
                models[entry["ruleset"]] = entry
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
    case_failure_messages = [
        f"{case.get('position')}: {message}"
        for case in cases
        if isinstance(case, dict)
        for message in case.get("failures") or []
    ]
    failures.extend(f"execution: {message}" for message in execution_failures)
    report["cases"] = cases
    report["failures"] = failures
    report["execution_failures"] = execution_failures
    report["collector_failures"] = collector
    report["coverage_failures"] = coverage_failures
    report["case_failures"] = case_failure_messages
    report["collector_ok"] = not collector and isinstance(report.get("raw_ok"), bool)
    report["cases_ok"] = bool(cases) and all(
        isinstance(case, dict) and case.get("ok") is True for case in cases
    )
    report["coverage_ok"] = not coverage_failures
    report["ok"] = not failures and report["collector_ok"] and report["cases_ok"]
    report["authoritative"] = False
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = json.loads(args.report.read_text())
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
                "cases": len(validated.get("cases") or []),
                "collector_ok": validated.get("collector_ok"),
                "failures": validated.get("failures") or [],
            },
            indent=2,
        )
    )
    sys.exit(0 if validated["ok"] else 1)


if __name__ == "__main__":
    main()
