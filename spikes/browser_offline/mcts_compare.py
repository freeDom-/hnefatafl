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

import mcts_workload  # noqa: E402
from hnefatafl.engine import RULESETS, Move, State, apply_move, legal_moves  # noqa: E402
from hnefatafl.encoding import ENCODER_VERSION  # noqa: E402
from hnefatafl.mcts_driver import (  # noqa: E402
    DRIVER_VERSION,
    SEARCH_ALGORITHM,
    position_identity,
    validate_search_settings,
)
from mcts_vectors import EVALUATOR_VERSION, workload_identity  # noqa: E402

SCHEMA = 1
COMPARISON_SCHEMA = 4
TERMINAL_VALUES = {"defenders": 1.0, "attackers": -1.0, "draw": 0.0}
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


COUNTER_FIELDS = (
    "requested_simulations",
    "completed_simulations",
    "neural_requests",
    "prediction_reuses",
    "immediate_win_shortcuts",
    "root_children",
    "inference_cache_hits",
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


def _workload_for(
    report: dict,
    file_positions: list[dict] | None,
    file_sha256: str | None = None,
) -> dict:
    return mcts_workload.validate_workload(
        report.get("workload"),
        file_positions=file_positions,
        file_sha256=file_sha256,
    )


def expected_position_names(report: dict) -> list[str]:
    """Validate the declared workload and return its expected case names."""
    return _workload_for(report, None)["expected_names"]


def _validate_report(
    report: dict,
    side: str,
    file_positions: list[dict] | None = None,
    file_sha256: str | None = None,
) -> dict:
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
    workload_info = None
    try:
        workload_info = mcts_workload.validate_workload(
            report.get("workload"),
            file_positions=file_positions,
            file_sha256=file_sha256,
        )
    except ValueError as error:
        fail(f"invalid workload definition: {error}")
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
    expected: list[str] = workload_info["expected_names"] if workload_info is not None else []
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
    if workload_info is not None:
        if raw_positions != workload_info["positions"]:
            fail("report positions do not match the validated workload")
        if expected != [entry.get("name") for entry in raw_positions]:
            fail("position coverage does not match the validated workload")
    model_meta = report.get("model_meta")
    expected_workload_identity = None
    if workload_info is not None and isinstance(settings, dict):
        evaluator = report.get("evaluator")
        model_digest = (
            model_meta.get("onnx_sha256")
            if report.get("level") == "b" and isinstance(model_meta, dict)
            else None
        )
        if isinstance(evaluator, str) and evaluator:
            expected_workload_identity = workload_identity(
                report.get("level"),
                evaluator,
                validate_search_settings(settings),
                workload_info["positions"],
                model_digest,
            )
            if report.get("workload_digest") != expected_workload_identity:
                fail("workload digest does not match the independently recomputed identity")
    if report.get("level") == "b":
        if not isinstance(model_meta, dict) or not _hex64(model_meta.get("onnx_sha256")):
            fail("model-backed report lacks a valid ONNX model digest")
        if model_meta is not None and model_meta.get("validated") is not True:
            fail("model-backed report does not use a validated export")
    elif report.get("level") == "a":
        if not isinstance(model_meta, dict) or model_meta.get("evaluator") is None:
            fail("deterministic report lacks evaluator metadata")
        elif model_meta.get("evaluator") != EVALUATOR_VERSION:
            fail("deterministic evaluator version is unsupported")
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
        "workload": workload_info,
        "workload_identity": expected_workload_identity,
        "workload_digest": report.get("workload_digest"),
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
    for field in COUNTER_FIELDS:
        counter = search.get(field)
        if counter is None and field in ("root_children", "inference_cache_hits"):
            fail(f"search.{field} must be an integer")
        elif not _is_int(counter):
            fail(f"search.{field} must be an integer")
        elif counter < 0:
            fail(f"search.{field} must be a non-negative integer")
    analysis_simulations = analysis.get("simulations")
    if analysis_simulations is not None and (
        not _is_int(analysis_simulations) or analysis_simulations < 0
    ):
        fail("analysis.simulations must be a non-negative integer")
    trace_length = search.get("trace_length")
    if trace_length is not None and (not _is_int(trace_length) or trace_length < 0):
        fail("search.trace_length must be a non-negative integer")
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
        if visits:
            fail("terminal position reported a root visit distribution")
        if completed != 0 or search.get("neural_requests") != 0:
            fail("terminal position requested inference or reported work")
        if reason not in ("terminal", "immediate_win"):
            fail("terminal position has an inconsistent completion reason")
        expected_value = TERMINAL_VALUES.get(state.winner)
        if _finite(value) and expected_value is not None:
            if float(value) != expected_value:
                fail(
                    f"terminal {state.winner} value {value!r} is not the canonical "
                    f"{expected_value!r}"
                )
    else:
        if analysis.get("source") != "neural_search":
            fail("non-terminal position was not classified as a neural search")
        if best_move is not None and Move(
            best_move["source"], best_move["target"]
        ) not in legal_moves(state):
            fail("best_move is not legal in the root position")
        if best_move is None:
            fail("a successful non-terminal search reported no best move")
        elif completed > 0 or reason == "immediate_win":
            shares = {
                (row.get("source"), row.get("target")): float(row.get("share", 0.0))
                for row in visits
                if isinstance(row, dict)
                and _is_int(row.get("source"))
                and _is_int(row.get("target"))
                and _finite(row.get("share"))
            }
            key = (best_move["source"], best_move["target"])
            if key not in shares:
                fail("best move is absent from the root visit distribution")
            elif shares and shares[key] != max(shares.values()):
                fail("best move does not have the maximum root visit share")
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
    # Recompute the exact search-position identity from the full state, effective
    # settings and model identity recorded by the report.
    search_model = search.get("model")
    try:
        effective = validate_search_settings(settings)
        if not isinstance(search_model, dict):
            raise ValueError("search model identity is missing")
        expected_identity = position_identity(state, effective, search_model)
    except (ValueError, KeyError, TypeError) as error:
        fail(f"cannot recompute the expected position identity: {error}")
    else:
        if search.get("position_identity") != expected_identity:
            fail("recorded position identity does not match the recomputed identity")
        if record.get("path") is not None and record.get("path") != expected_identity:
            fail("record position association does not match the recomputed identity")
        if (
            search.get("algorithm") != SEARCH_ALGORITHM
            or search.get("driver_version") != DRIVER_VERSION
            or search.get("encoder_version") != ENCODER_VERSION
        ):
            fail("search algorithm/driver/encoder identity is inconsistent")
    return failures


def _completion_class(record: dict, state: State, requested: int) -> str:
    """Classify fixed-work completion for one runtime independently."""
    search = record.get("search") or {}
    reason = search.get("completion_reason")
    completed = search.get("completed_simulations")
    if state.winner is not None:
        return "terminal"
    if reason == "immediate_win":
        return "immediate_win"
    if reason == "deadline" or search.get("deadline_cutoff") is True:
        return "deadline_limited"
    if _is_int(completed) and completed == requested and reason in ("simulations", "completed"):
        return "complete"
    if _is_int(completed) and completed < requested:
        return "incomplete"
    return "inconsistent"


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


def compare(
    native: dict,
    browser: dict,
    *,
    require_best_move: bool,
    custom_positions: list[dict] | None = None,
    custom_file_digest: str | None = None,
) -> dict:
    native_validation = _validate_report(native, "native", custom_positions, custom_file_digest)
    browser_validation = _validate_report(browser, "browser", custom_positions, custom_file_digest)
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
    native_identity = native_validation.get("workload_identity")
    browser_identity = browser_validation.get("workload_identity")
    if native_identity is None or browser_identity is None:
        failures.append("a workload identity could not be independently recomputed")
    elif native_identity != browser_identity:
        failures.append("independently recomputed workload identities differ")
    elif native_identity != native.get("workload_digest"):
        failures.append("native workload digest does not match its recomputed identity")
    elif browser_identity != browser.get("workload_digest"):
        failures.append("browser workload digest does not match its recomputed identity")
    native_settings = native_validation.get("settings")
    browser_settings = browser_validation.get("settings")
    if native_settings != browser_settings:
        failures.append("effective settings differ between the reports")
    native_workload = native_validation.get("workload")
    browser_workload = browser_validation.get("workload")
    if native_workload is not None and browser_workload is not None:
        if native_workload["source"] != browser_workload["source"]:
            failures.append("workload source differs between the reports")
        if native_workload["expected_names"] != browser_workload["expected_names"]:
            failures.append("workload expected names differ between the reports")
        if native_workload["positions_sha256"] != browser_workload["positions_sha256"]:
            failures.append("workload position digest differs between the reports")
        if native_workload["positions"] != browser_workload["positions"]:
            failures.append("workload positions or repetition history differ")
    elif native_workload is None or browser_workload is None:
        failures.append("a workload definition could not be validated")
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
    native_incomplete: list[str] = []
    browser_incomplete: list[str] = []
    native_deadline: list[str] = []
    browser_deadline: list[str] = []
    completion_classes: dict[str, str] = {}
    equal_work = 0
    max_value_delta = 0.0
    max_visit_tv = 0.0
    expected_names = native_workload["expected_names"] if native_workload is not None else []
    for name in expected_names:
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
        requested = browser_settings["iterations"]
        native_class = _completion_class(left, state, requested)
        browser_class = _completion_class(right, state, requested)
        completion_classes[name] = f"{native_class}/{browser_class}"
        entry.update(
            {
                "native_completion": native_class,
                "browser_completion": browser_class,
            }
        )
        if native_class == "deadline_limited":
            native_deadline.append(name)
        if browser_class == "deadline_limited":
            browser_deadline.append(name)
        if native_class in ("incomplete", "inconsistent"):
            native_incomplete.append(name)
        if browser_class in ("incomplete", "inconsistent"):
            browser_incomplete.append(name)
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
                native_class == "complete"
                and browser_class == "complete"
                and left_search.get("completed_simulations")
                == right_search.get("completed_simulations")
                == browser_settings["iterations"]
                and left_search.get("neural_requests") == right_search.get("neural_requests")
            )
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
    native_side_valid = not native_validation["failures"]
    browser_side_valid = not browser_validation["failures"]
    native_complete = native_side_valid and not native_incomplete and not native_deadline
    browser_complete = browser_side_valid and not browser_incomplete and not browser_deadline
    workload_completion = {
        "requested_simulations": (browser_settings or {}).get("iterations"),
        "native_incomplete_cases": native_incomplete,
        "browser_incomplete_cases": browser_incomplete,
        "native_deadline_limited_cases": native_deadline,
        "browser_deadline_limited_cases": browser_deadline,
        "incomplete_cases": sorted(set(native_incomplete) | set(browser_incomplete)),
        "deadline_limited_cases": sorted(set(native_deadline) | set(browser_deadline)),
        "native_complete": native_complete,
        "browser_complete": browser_complete,
        "complete": native_complete and browser_complete,
    }
    if native_incomplete:
        failures.append(
            "native fixed-work workload did not complete: " + ", ".join(native_incomplete[:8])
        )
    if browser_incomplete:
        failures.append(
            "browser fixed-work workload did not complete: " + ", ".join(browser_incomplete[:8])
        )
    if native_deadline:
        failures.append(
            "native fixed-work workload was deadline limited: " + ", ".join(native_deadline[:8])
        )
    if browser_deadline:
        failures.append(
            "browser fixed-work workload was deadline limited: " + ", ".join(browser_deadline[:8])
        )
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
        "error": None,
        "execution_validity": {
            "ok": execution_valid,
            "positions": positions,
            "case_failures": [failure for failure in failures if ": " in failure][:12],
        },
        "deterministic_parity": deterministic,
        "numerical_comparison": numerical,
        "workload": {
            "valid": native_workload is not None and browser_workload is not None,
            "source": native_workload["source"] if native_workload else None,
            "expected_cases": len(expected_names),
            "positions_sha256": (native_workload["positions_sha256"] if native_workload else None),
            "recomputed_identity": native_identity,
            "browser_recomputed_identity": browser_identity,
            "identity_verified": (
                native_identity is not None
                and native_identity == browser_identity
                and native_identity == native.get("workload_digest")
                and browser_identity == browser.get("workload_digest")
            ),
            "custom_file_verified": (
                bool(custom_positions and custom_file_digest) if native_workload else None
            ),
        },
        "workload_completion": workload_completion,
        "completion_classes": completion_classes,
        "performance_comparability": performance,
        "cases": records,
        "authoritative": False,
    }
    report["acceptance"] = not failures
    report["ok"] = report["acceptance"]
    return report


def failure_report(kind: str, message: str, *, level: str | None = None) -> dict:
    """Complete, internally consistent failure report for any input error."""
    return {
        "kind": "mcts-parity-comparison",
        "schema": COMPARISON_SCHEMA,
        "level": level,
        "native_runtime": None,
        "browser_runtime": None,
        "failures": [f"{kind}: {message}"],
        "notes": [],
        "error": {"kind": kind, "message": message},
        "execution_validity": {
            "ok": False,
            "positions": 0,
            "case_failures": [],
        },
        "deterministic_parity": {
            "required": level == "a",
            "exact_cases": 0,
            "positions": 0,
            "ok": False if level == "a" else None,
        },
        "numerical_comparison": {
            "required": level == "b",
            "positions": 0,
            "best_move_matches": None,
            "divergent_cases": [],
            "max_value_delta": None,
            "max_visit_total_variation": None,
            "best_move_parity": None,
            "strict_gate": False,
        },
        "workload": {
            "valid": False,
            "source": None,
            "expected_cases": 0,
            "positions_sha256": None,
            "recomputed_identity": None,
            "browser_recomputed_identity": None,
            "identity_verified": False,
            "custom_file_verified": None,
        },
        "completion_classes": {},
        "workload_completion": {
            "requested_simulations": None,
            "native_incomplete_cases": [],
            "browser_incomplete_cases": [],
            "native_deadline_limited_cases": [],
            "browser_deadline_limited_cases": [],
            "incomplete_cases": [],
            "deadline_limited_cases": [],
            "native_complete": False,
            "browser_complete": False,
            "complete": False,
        },
        "performance_comparability": {
            "equal_work_cases": None,
            "positions": 0,
            "comparable": None,
            "cases": [],
            "note": "No comparison was performed.",
        },
        "cases": [],
        "acceptance": False,
        "ok": False,
        "authoritative": False,
    }


def _summary(report: dict) -> dict:
    execution = report.get("execution_validity") or {}
    deterministic = report.get("deterministic_parity") or {}
    numerical = report.get("numerical_comparison") or {}
    completion = report.get("workload_completion") or {}
    performance = report.get("performance_comparability") or {}
    return {
        "ok": report.get("ok") is True,
        "level": report.get("level"),
        "error": report.get("error"),
        "execution_valid": execution.get("ok"),
        "deterministic_parity": deterministic.get("ok"),
        "best_move_parity": numerical.get("best_move_parity"),
        "workload_complete": completion.get("complete"),
        "equal_work_cases": performance.get("equal_work_cases"),
        "failures": report.get("failures") or [],
        "notes": report.get("notes") or [],
    }


def _write_report(path: Path, report: dict) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2) + "\n")
        return True
    except OSError as error:
        print(json.dumps({"ok": False, "write_error": str(error)}), file=sys.stderr)
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("native", type=Path)
    parser.add_argument("browser", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--require-best-move-parity", action="store_true")
    parser.add_argument(
        "--positions-file",
        type=Path,
        help="Original custom position file; verifies the declared custom workload",
    )
    args = parser.parse_args()
    level = None
    report = None
    exit_code = 1
    try:
        native = json.loads(args.native.read_text())
        browser = json.loads(args.browser.read_text())
        if not isinstance(native, dict) or not isinstance(browser, dict):
            raise ValueError("both reports must be JSON objects")
        level = native.get("level")
        custom_positions = None
        custom_digest = None
        if args.positions_file is not None:
            custom_positions, custom_digest = mcts_workload.load_custom_positions(
                args.positions_file
            )
        report = compare(
            native,
            browser,
            require_best_move=args.require_best_move_parity,
            custom_positions=custom_positions,
            custom_file_digest=custom_digest,
        )
        exit_code = 0 if report.get("ok") else 1
    except (OSError, UnicodeDecodeError) as error:
        report = failure_report("input", f"cannot read or decode an input file: {error}")
        exit_code = 2
    except (json.JSONDecodeError, ValueError, TypeError) as error:
        report = failure_report("workload", f"malformed comparison input: {error}", level=level)
        exit_code = 2
    except Exception as error:  # noqa: BLE001 - never emit a second traceback
        report = failure_report("execution", f"{type(error).__name__}: {error}", level=level)
        exit_code = 2
    if args.output is not None:
        _write_report(args.output, report)
    print(json.dumps(_summary(report), indent=2))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
