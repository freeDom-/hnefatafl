"""Compare native and browser neural MCTS parity reports.

The comparator independently validates both reports before comparing them. It
never trusts a report's own definition of its workload, coverage, budget or
success: expected positions are re-derived from the shared fixture selection
recorded in the report, and every result must be execution-valid before parity,
numerical differences, workload completion and acceptance are reported.

Level A (shared deterministic evaluator) requires exact equality of the completed
analysis and search payload, including the evaluation trace: it isolates
traversal, RNG consumption, legal ordering, value perspective, expansion,
backpropagation, visit counts and best-move selection from inference numerics.

Level B (actual model inference) requires valid results and identical workload and
model identity, then reports best-move, value, completed-simulation and
visit-distribution differences. Floating-point providers may legitimately change
an MCTS trajectory; divergence is measured, never treated as equality. Pass
``--require-best-move-parity`` for a strict owner gate.

Report sections are separated so a caller can distinguish:

* execution validity (legal, finite, coherent results),
* deterministic shared-driver parity (Level A only),
* actual model-backed numerical differences (Level B only),
* workload completion (fixed-work budgets actually finished),
* performance comparability (equal completed work),
* overall acceptance.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

SPIKE = Path(__file__).resolve().parent
ROOT = SPIKE.parents[1]
for path in (str(SPIKE), str(ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

from fixtures import jarl_positions  # noqa: E402
from hnefatafl.engine import RULESETS, Move, State, apply_move, legal_moves  # noqa: E402

SCHEMA = 1
COMPARISON_SCHEMA = 2
LEVELS = ("a", "b")
SETTING_FIELDS = ("iterations", "time_limit", "seed", "exploration")
COMPLETION_REASONS = {
    "simulations",
    "deadline",
    "immediate_win",
    "terminal",
    "insufficient_budget",
    "completed",
}
VALUE_TOLERANCE = 5e-4
EPSILON = 1e-9


def _is_int(value) -> bool:
    return type(value) is int


def _is_number(value) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float))


def _finite(value) -> bool:
    return _is_number(value) and math.isfinite(float(value))


def _hex64(value) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _settings(raw) -> dict:
    if not isinstance(raw, dict) or set(raw) != set(SETTING_FIELDS):
        raise ValueError("settings must contain exactly iterations, time_limit, seed, exploration")
    settings = raw
    if not _is_int(settings["iterations"]) or not 1 <= settings["iterations"] <= 4096:
        raise ValueError("settings.iterations is not a supported integer budget")
    if not _finite(settings["time_limit"]) or not 0.05 <= settings["time_limit"] <= 10:
        raise ValueError("settings.time_limit is outside the supported range")
    if not _is_int(settings["seed"]) or not 0 <= settings["seed"] <= 2**32 - 1:
        raise ValueError("settings.seed is not a supported integer")
    if not _finite(settings["exploration"]) or not 0 <= settings["exploration"] <= 10:
        raise ValueError("settings.exploration is outside the supported range")
    return dict(settings)


def expected_position_names(report: dict) -> list[str]:
    """Re-derive the expected position coverage from the recorded selection."""
    workload = report.get("workload")
    if not isinstance(workload, dict):
        raise ValueError("report has no workload definition")
    selection = workload.get("selection")
    if not isinstance(selection, dict):
        raise ValueError("report workload has no selection definition")
    if selection.get("source") != "fixtures.jarl_positions":
        raise ValueError("report workload selection source is unsupported")
    rulesets = selection.get("rulesets")
    if (
        not isinstance(rulesets, list)
        or not rulesets
        or any(name not in RULESETS for name in rulesets)
    ):
        raise ValueError("report workload selection has invalid rulesets")
    kinds = selection.get("kinds")
    if kinds is not None and (
        not isinstance(kinds, list) or not kinds or any(not isinstance(k, str) for k in kinds)
    ):
        raise ValueError("report workload selection has invalid kinds")
    limit = selection.get("limit")
    if limit is not None and (not _is_int(limit) or limit < 1):
        raise ValueError("report workload selection has an invalid limit")
    selected = [
        case
        for case in jarl_positions()
        if case["ruleset"] in rulesets and (kinds is None or case["kind"] in kinds)
    ]
    if limit is not None:
        counted: dict[str, int] = {}
        limited = []
        for case in selected:
            if counted.get(case["ruleset"], 0) >= limit:
                continue
            counted[case["ruleset"]] = counted.get(case["ruleset"], 0) + 1
            limited.append(case)
        selected = limited
    return [case["name"] for case in selected]


def _validate_report(report: dict, side: str) -> dict:
    failures: list[str] = []

    def fail(message: str) -> None:
        failures.append(f"{side}: {message}")

    if not isinstance(report, dict):
        return {"failures": [f"{side}: report is not an object"], "positions": {}, "results": {}}
    if report.get("kind") != "mcts-parity":
        fail("kind is not mcts-parity")
    if report.get("schema") != SCHEMA:
        fail(f"unsupported schema {report.get('schema')!r}")
    if report.get("level") not in LEVELS:
        fail("level is not a or b")
    identity = report.get("engine_identity")
    if not isinstance(identity, dict) or not _hex64(identity.get("sha256")):
        fail("engine source identity is missing or malformed")
    if not _hex64(report.get("workload_digest")):
        fail("workload digest is missing or malformed")
    if not isinstance(report.get("evaluator"), str) or not report["evaluator"]:
        fail("evaluator name is missing")
    workflow_failures = report.get("failures")
    if not isinstance(workflow_failures, list) or any(
        not isinstance(item, str) for item in workflow_failures
    ):
        fail("failures must be a list of strings")
    elif workflow_failures:
        fail("the report itself records failures")
    settings = None
    try:
        settings = _settings(report.get("settings"))
    except ValueError as error:
        fail(str(error))
    expected: list[str] = []
    try:
        expected = expected_position_names(report)
    except ValueError as error:
        fail(str(error))
    positions: dict[str, dict] = {}
    raw_positions = report.get("positions")
    if not isinstance(raw_positions, list) or not raw_positions:
        fail("positions must be a non-empty list")
        raw_positions = []
    for entry in raw_positions:
        if not isinstance(entry, dict):
            fail("position entry is not an object")
            continue
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            fail("position entry has no name")
            continue
        if name in positions:
            fail(f"duplicate position identifier {name!r}")
            continue
        if entry.get("ruleset") not in RULESETS:
            fail(f"{name}: unsupported ruleset")
            continue
        try:
            state = State.from_dict(entry.get("state"))
        except (ValueError, KeyError, TypeError) as error:
            fail(f"{name}: invalid position state ({error})")
            continue
        if state.ruleset != entry["ruleset"]:
            fail(f"{name}: ruleset does not match the serialized position")
            continue
        positions[name] = {"entry": entry, "state": state}
    if expected and [entry.get("name") for entry in raw_positions] != expected:
        fail("position coverage does not match the recorded workload selection")
    model_meta = report.get("model_meta")
    if report.get("level") == "b":
        if not isinstance(model_meta, dict) or not _hex64(model_meta.get("onnx_sha256")):
            fail("model-backed report lacks a valid ONNX model digest")
        if model_meta is not None and model_meta.get("validated") is not True:
            fail("model-backed report does not use a validated export")
    elif report.get("level") == "a":
        if not isinstance(model_meta, dict) or model_meta.get("evaluator") is None:
            fail("deterministic report lacks evaluator metadata")
        if isinstance(settings, dict) and settings["iterations"] > 0:
            pass
    results: dict[str, dict] = {}
    raw_results = report.get("results")
    if not isinstance(raw_results, list):
        fail("results must be a list")
        raw_results = []
    for record in raw_results:
        if not isinstance(record, dict):
            fail("result entry is not an object")
            continue
        name = record.get("name")
        if not isinstance(name, str) or not name:
            fail("result entry has no name")
            continue
        if name in results:
            fail(f"duplicate result identifier {name!r}")
            continue
        results[name] = record
    names = set(results)
    if names != set(positions):
        missing = sorted(set(positions) - names)
        extra = sorted(names - set(positions))
        if missing:
            fail(f"missing results for {missing[:5]}")
        if extra:
            fail(f"unexpected results for {extra[:5]}")
    return {
        "failures": failures,
        "positions": positions,
        "results": results,
        "settings": settings,
        "model_meta": model_meta,
        "level": report.get("level"),
    }


def _validate_search(
    record: dict,
    position: dict,
    settings: dict,
    model_meta: dict | None,
    *,
    level: str,
    side: str,
) -> list[str]:
    failures: list[str] = []
    name = record["name"]
    state: State = position["state"]

    def fail(message: str) -> None:
        failures.append(f"{side}: {name}: {message}")

    if record.get("ruleset") != position["entry"].get("ruleset"):
        fail("result ruleset does not match the workload position")
    analysis = record.get("analysis")
    search = record.get("search")
    if not isinstance(analysis, dict) or not isinstance(search, dict):
        fail("result lacks analysis or search metadata")
        return failures
    if analysis.get("source") not in ("neural_search", "terminal"):
        fail(f"unexpected analysis source {analysis.get('source')!r}")
    value = analysis.get("value")
    if not _finite(value) or not -1.0 <= float(value) <= 1.0:
        fail("analysis value is not finite within [-1, 1]")
    best_move = analysis.get("best_move")
    if best_move is not None and not (
        isinstance(best_move, dict)
        and _is_int(best_move.get("source"))
        and _is_int(best_move.get("target"))
    ):
        fail("analysis best_move is malformed")
        best_move = None
    for field in (
        "requested_simulations",
        "completed_simulations",
        "neural_requests",
        "prediction_reuses",
        "immediate_win_shortcuts",
    ):
        if not _is_int(search.get(field)):
            fail(f"search.{field} must be an integer")
    for field in ("root_value", "search_value"):
        field_value = search.get(field)
        if field_value is not None and (
            not _finite(field_value) or not -1.0 <= float(field_value) <= 1.0
        ):
            fail(f"search.{field} is not finite within [-1, 1]")
    if search.get("algorithm") != "neural-mcts-puct-v1":
        fail("search algorithm identity is missing or unexpected")
    if search.get("encoder_version") != "six-plane-v1":
        fail("search encoder version is missing or unexpected")
    if search.get("authoritative") is not False:
        fail("search result must be explicitly non-authoritative")
    if not _hex64(search.get("position_identity")):
        fail("search position identity is missing or malformed")
    if search.get("ruleset") != position["entry"].get("ruleset"):
        fail("search ruleset does not match the workload position")
    if search.get("size") != state.size:
        fail("search board size does not match the workload position")
    model = search.get("model")
    if not isinstance(model, dict) or not _hex64(model.get("sha256")):
        fail("search model identity is missing or malformed")
    elif (
        model.get("ruleset") != position["entry"].get("ruleset") or model.get("size") != state.size
    ):
        fail("search model identity does not match the workload position")
    elif level == "b" and isinstance(model_meta, dict):
        if model.get("sha256") != model_meta.get("onnx_sha256"):
            fail("search model digest does not match the validated report model")
    requested = search.get("requested_simulations")
    if _is_int(requested) and requested != settings["iterations"]:
        fail("search requested_simulations was redefined by the report")
    completed = search.get("completed_simulations")
    if _is_int(completed) and completed > settings["iterations"]:
        fail("completed simulations exceed the requested budget")
    reason = search.get("completion_reason")
    if reason not in COMPLETION_REASONS:
        fail(f"unexpected completion reason {reason!r}")
    cutoff = search.get("deadline_cutoff")
    if not isinstance(cutoff, bool):
        fail("search.deadline_cutoff must be a boolean")
    elif cutoff and reason != "deadline":
        fail("deadline cutoff does not match the completion reason")
    if _is_int(completed) and completed > 0:
        if analysis.get("simulations") != completed:
            fail("analysis simulations disagree with the search diagnostics")
    elif analysis.get("simulations") not in (None, 0):
        fail("analysis simulations disagree with the search diagnostics")
    if (
        search.get("search_value") is not None
        and _finite(value)
        and _finite(search.get("search_value"))
    ):
        if abs(float(value) - float(search["search_value"])) > 1e-12:
            fail("analysis value disagrees with the search value")
    if best_move is not None and search.get("best_move") != best_move:
        fail("analysis best_move disagrees with the search best_move")
    # Visits
    visits = search.get("root_visits")
    if not isinstance(visits, list):
        fail("root_visits must be a list")
        visits = []
    seen: set[tuple[int, int]] = set()
    visit_total = 0
    share_total = 0.0
    for row in visits:
        if not isinstance(row, dict):
            fail("root visit entry is not an object")
            continue
        source, target = row.get("source"), row.get("target")
        if not _is_int(source) or not _is_int(target):
            fail("root visit action is malformed")
            continue
        key = (source, target)
        if key in seen:
            fail(f"duplicate root visit for action {key}")
            continue
        seen.add(key)
        move = Move(source, target)
        if move not in legal_moves(state):
            fail(f"root visit action {key} is not legal in the root position")
        row_visits = row.get("visits")
        share = row.get("share")
        if not _is_int(row_visits) or row_visits < 0:
            fail("root visit count must be a non-negative integer")
            continue
        if not _finite(share) or not -EPSILON <= float(share) <= 1 + EPSILON:
            fail("root visit share must be finite within [0, 1]")
            continue
        visit_total += row_visits
        share_total += float(share)
    if _is_int(completed) and completed > 0:
        if not visits:
            fail("a completed search reported no root visit distribution")
        if abs(visit_total - completed) > max(1, len(visits) // 2 + 1):
            fail("root visit counts are inconsistent with completed simulations")
        if abs(share_total - 1.0) > 1e-6:
            fail("root visit shares do not sum to one")
    # Terminal, immediate-win and workload completion semantics.
    if state.winner is not None:
        if analysis.get("source") != "terminal":
            fail("terminal position was not classified terminal")
        if best_move is not None:
            fail("terminal position reported a best move")
        if completed != 0 or search.get("neural_requests") != 0:
            fail("terminal position requested inference or reported work")
        if reason not in ("terminal", "immediate_win"):
            fail("terminal position has an inconsistent completion reason")
    else:
        if analysis.get("source") != "neural_search":
            fail("non-terminal position was not classified as a neural search")
        if best_move is not None and Move(
            best_move["source"], best_move["target"]
        ) not in legal_moves(state):
            fail("best_move is not legal in the root position")
        if reason == "immediate_win":
            if completed != 0:
                fail("immediate win must not claim completed simulations")
            if search.get("immediate_win_shortcuts") != 1:
                fail("immediate win did not record the shortcut")
            expected = 1.0 if state.turn == "defenders" else -1.0
            if _finite(value) and abs(float(value) - expected) > 1e-12:
                fail("immediate-win value is not defender-relative")
            if best_move is not None:
                child = apply_move(state, Move(best_move["source"], best_move["target"]))
                if child.winner != state.turn:
                    fail("immediate-win best move does not win")
        elif completed == settings["iterations"]:
            if reason not in ("simulations",):
                fail("a complete fixed-work search has an inconsistent completion reason")
        elif reason == "deadline":
            if not cutoff:
                fail("deadline-limited search did not record the cutoff")
        elif reason in ("simulations",):
            fail("reported full budget but completed fewer simulations")
    return failures


def _best_move(record: dict) -> Move | None:
    move = (record.get("analysis") or {}).get("best_move")
    if not isinstance(move, dict):
        return None
    return Move(int(move["source"]), int(move["target"]))


def _visits(record: dict) -> dict[tuple[int, int], float]:
    rows = (record.get("search") or {}).get("root_visits") or []
    return {(int(row["source"]), int(row["target"])): float(row["share"]) for row in rows}


def _total_variation(left: dict, right: dict) -> float:
    keys = set(left) | set(right)
    return 0.5 * sum(abs(left.get(key, 0.0) - right.get(key, 0.0)) for key in keys)


def _payload(record: dict) -> dict:
    search = dict(record.get("search") or {})
    search.pop("trace", None)
    return {"analysis": record.get("analysis"), "search": search}


def compare(native: dict, browser: dict, *, require_best_move: bool) -> dict:
    native_validation = _validate_report(native, "native")
    browser_validation = _validate_report(browser, "browser")
    failures: list[str] = list(native_validation["failures"]) + list(browser_validation["failures"])
    notes: list[str] = []
    level = native.get("level")
    if browser.get("level") != level:
        failures.append("comparison level differs between the reports")
    if native.get("schema") != browser.get("schema"):
        failures.append("report schema differs between the reports")
    native_engine = (native.get("engine_identity") or {}).get("sha256")
    browser_engine = (browser.get("engine_identity") or {}).get("sha256")
    if native_engine != browser_engine:
        failures.append("engine source identity differs between the reports")
    if native.get("workload_digest") != browser.get("workload_digest"):
        failures.append("workload digest differs between the reports")
    native_settings = native_validation.get("settings")
    browser_settings = browser_validation.get("settings")
    if native_settings != browser_settings:
        failures.append("effective settings differ between the reports")
    native_positions = native_validation["positions"]
    browser_positions = browser_validation["positions"]
    if set(native_positions) != set(browser_positions):
        failures.append("position coverage differs between the reports")
    for name in sorted(set(native_positions) & set(browser_positions)):
        left_state = native_positions[name]["state"].to_dict()
        right_state = browser_positions[name]["state"].to_dict()
        if left_state != right_state:
            failures.append(f"{name}: serialized position or repetition history differs")
    level_b = level == "b"
    native_model = native_validation.get("model_meta")
    browser_model = browser_validation.get("model_meta")
    if level_b and isinstance(native_model, dict) and isinstance(browser_model, dict):
        if native_model.get("onnx_sha256") != browser_model.get("onnx_sha256"):
            failures.append("model digest differs between the reports")
    records = []
    execution_valid = True
    exact_cases = 0
    best_move_matches = 0
    divergent: list[str] = []
    incomplete: list[str] = []
    deadline_limited: list[str] = []
    equal_work = 0
    max_value_delta = 0.0
    max_visit_tv = 0.0
    for name in expected_position_names(native) if native.get("workload") else []:
        left = native_validation["results"].get(name)
        right = browser_validation["results"].get(name)
        entry = {"name": name}
        if left is None or right is None:
            failures.append(f"{name}: missing result on one runtime")
            execution_valid = False
            entry["ok"] = False
            records.append(entry)
            continue
        if left.get("ok") is not True or right.get("ok") is not True:
            failures.append(
                f"{name}: execution failed (native: {left.get('error')}, "
                f"browser: {right.get('error')})"
            )
            execution_valid = False
            entry["ok"] = False
            records.append(entry)
            continue
        if native_settings is None or browser_settings is None:
            entry["ok"] = False
            records.append(entry)
            continue
        case_failures = _validate_search(
            right,
            browser_positions[name],
            browser_settings,
            browser_model,
            level=level,
            side="browser",
        ) + _validate_search(
            left,
            native_positions[name],
            native_settings,
            native_model,
            level=level,
            side="native",
        )
        failures.extend(case_failures)
        if case_failures:
            execution_valid = False
            entry["ok"] = False
            records.append(entry)
            continue
        state = browser_positions[name]["state"]
        right_search = right.get("search") or {}
        left_search = left.get("search") or {}
        entry.update(
            {
                "ok": True,
                "terminal": state.winner is not None,
                "completion_reason": right_search.get("completion_reason"),
                "completed_simulations": right_search.get("completed_simulations"),
            }
        )
        if state.winner is None and right_search.get("completion_reason") != "immediate_win":
            if right_search.get("completed_simulations") != browser_settings["iterations"]:
                incomplete.append(name)
            if right_search.get("completion_reason") == "deadline":
                deadline_limited.append(name)
        if level == "a":
            left_trace = (left.get("search") or {}).get("trace_sha256")
            right_trace = (right.get("search") or {}).get("trace_sha256")
            if not left_trace or left_trace != right_trace:
                failures.append(f"{name}: evaluation-trace digest differs or is missing")
            if (left.get("search") or {}).get("trace_length") != (right.get("search") or {}).get(
                "trace_length"
            ):
                failures.append(f"{name}: evaluation-trace length differs")
            if _payload(left) != _payload(right):
                failures.append(f"{name}: deterministic search payload differs")
                entry["exact"] = False
            else:
                exact_cases += 1
                entry["exact"] = True
        else:
            native_move = _best_move(left)
            browser_move = _best_move(right)
            terminal = state.winner is not None
            same_move = (
                native_move == browser_move
                if terminal
                else native_move is not None and browser_move == native_move
            )
            best_move_matches += int(same_move)
            if not same_move:
                divergent.append(name)
            left_value = (left.get("analysis") or {}).get("value")
            right_value = (right.get("analysis") or {}).get("value")
            delta = abs(float(left_value) - float(right_value))
            visits = _total_variation(_visits(left), _visits(right))
            max_value_delta = max(max_value_delta, delta)
            max_visit_tv = max(max_visit_tv, visits)
            same_work = (
                left_search.get("completed_simulations")
                == right_search.get("completed_simulations")
                == browser_settings["iterations"]
            ) and left_search.get("neural_requests") == right_search.get("neural_requests")
            equal_work += int(same_work)
            entry.update(
                {
                    "best_move_match": same_move,
                    "value_delta": delta,
                    "visit_total_variation": visits,
                    "completed_simulations_delta": int(right_search.get("completed_simulations", 0))
                    - int(left_search.get("completed_simulations", 0)),
                    "neural_requests_delta": right_search.get("neural_requests", 0)
                    - left_search.get("neural_requests", 0),
                    "equal_work": same_work,
                }
            )
            if require_best_move and not same_move:
                failures.append(f"{name}: browser best move differs from the native reference")
        records.append(entry)
    positions = len(records)
    deterministic = {
        "required": level == "a",
        "exact_cases": exact_cases,
        "positions": positions,
        "ok": (exact_cases == positions) if level == "a" else None,
    }
    numerical = {
        "required": level_b,
        "positions": positions,
        "best_move_matches": best_move_matches if level_b else None,
        "divergent_cases": divergent if level_b else [],
        "max_value_delta": max_value_delta if level_b else None,
        "max_visit_total_variation": max_visit_tv if level_b else None,
        "best_move_parity": (best_move_matches == positions) if level_b else None,
        "strict_gate": require_best_move,
    }
    if level_b and divergent:
        notes.append(
            f"{len(divergent)} case(s) diverged under different inference numerics: "
            + ", ".join(divergent[:8])
        )
    if deterministic["required"] and not deterministic["ok"]:
        failures.append("deterministic parity is incomplete")
    workload_completion = {
        "requested_simulations": (browser_settings or {}).get("iterations"),
        "incomplete_cases": incomplete,
        "deadline_limited_cases": deadline_limited,
        "complete": not incomplete,
    }
    if incomplete:
        failures.append("fixed-work workload did not complete: " + ", ".join(incomplete[:8]))
    performance = {
        "equal_work_cases": equal_work if level_b else None,
        "positions": positions,
        "comparable": (equal_work == positions) if level_b else None,
        "note": (
            "Equal completed simulations and neural requests are required before a "
            "latency or throughput comparison is meaningful."
        ),
    }
    report = {
        "kind": "mcts-parity-comparison",
        "schema": COMPARISON_SCHEMA,
        "level": level,
        "native_runtime": native.get("runtime"),
        "browser_runtime": browser.get("runtime"),
        "failures": failures,
        "notes": notes,
        "execution_validity": {
            "ok": execution_valid,
            "positions": positions,
            "case_failures": [failure for failure in failures if ": " in failure][:12],
        },
        "deterministic_parity": deterministic,
        "numerical_comparison": numerical,
        "workload_completion": workload_completion,
        "performance_comparability": performance,
        "cases": records,
        "authoritative": False,
    }
    report["acceptance"] = not failures
    report["ok"] = report["acceptance"]
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("native", type=Path)
    parser.add_argument("browser", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--require-best-move-parity", action="store_true")
    args = parser.parse_args()
    report = compare(
        json.loads(args.native.read_text()),
        json.loads(args.browser.read_text()),
        require_best_move=args.require_best_move_parity,
    )
    text = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text)
    print(
        json.dumps(
            {
                "ok": report["ok"],
                "level": report["level"],
                "execution_valid": report["execution_validity"]["ok"],
                "deterministic_parity": report["deterministic_parity"]["ok"],
                "best_move_parity": report["numerical_comparison"]["best_move_parity"],
                "workload_complete": report["workload_completion"]["complete"],
                "failures": report["failures"],
                "notes": report["notes"],
            },
            indent=2,
        )
    )
    sys.exit(0 if report["ok"] else 1)


if __name__ == "__main__":
    main()
