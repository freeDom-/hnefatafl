"""Compare native and browser neural MCTS parity reports.

Level A (shared deterministic evaluator) requires exact equality of the completed
analysis and search payload, including the evaluation trace: it isolates
traversal, RNG consumption, legal ordering, value perspective, expansion,
backpropagation, visit counts and best-move selection from inference numerics.

Level B (actual model inference) requires identical workload/model identity and
valid legal results, then reports best-move, value, completed-simulation and
visit-distribution differences. Floating-point inference can legitimately change
an MCTS trajectory, so divergent trajectories are *measured*, not silently
declared equal. Pass ``--require-best-move-parity`` for a strict owner gate.

This comparator is the single comparison implementation behind the browser
harness and the standalone CLI.
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

from hnefatafl.engine import Move, State, legal_moves  # noqa: E402

VALUE_TOLERANCE = 5e-4
VISIT_TOLERANCE = 1e-6


def _load(path: Path) -> dict:
    return json.loads(path.read_text())


def _case_map(report: dict) -> dict:
    return {record["name"]: record for record in report.get("results", [])}


def _payload(record: dict) -> dict:
    search = dict(record.get("search") or {})
    search.pop("trace", None)
    return {"analysis": record.get("analysis"), "search": search}


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


def compare(native: dict, browser: dict, *, require_best_move: bool) -> dict:
    failures: list[str] = []
    notes: list[str] = []
    report = {
        "kind": "mcts-parity-comparison",
        "schema": 1,
        "level": native.get("level"),
        "native_runtime": native.get("runtime"),
        "browser_runtime": browser.get("runtime"),
        "failures": failures,
        "notes": notes,
        "cases": [],
        "authoritative": False,
    }
    for field in ("kind", "schema", "level"):
        if native.get(field) != browser.get(field):
            failures.append(f"report {field} differs: {native.get(field)} != {browser.get(field)}")
    native_engine = (native.get("engine_identity") or {}).get("sha256")
    browser_engine = (browser.get("engine_identity") or {}).get("sha256")
    if not native_engine or not browser_engine:
        failures.append("engine source identity is unavailable on at least one runtime")
    elif native_engine != browser_engine:
        failures.append(f"engine source identity differs: {native_engine} != {browser_engine}")
    if native.get("workload_digest") != browser.get("workload_digest"):
        failures.append("workload digest differs; the two runs are not the same workload")
    native_positions = [entry["name"] for entry in native.get("positions", [])]
    browser_positions = [entry["name"] for entry in browser.get("positions", [])]
    if native_positions != browser_positions:
        failures.append("position coverage differs between native and browser runs")
    position_states = {
        entry["name"]: State.from_dict(entry["state"])
        for entry in native.get("positions", [])
        if isinstance(entry.get("state"), dict)
    }
    native_cases = _case_map(native)
    browser_cases = _case_map(browser)
    level = native.get("level")
    exact_cases = 0
    best_move_matches = 0
    divergent: list[str] = []
    for name in native_positions:
        left = native_cases.get(name)
        right = browser_cases.get(name)
        entry = {"name": name}
        if left is None or right is None:
            failures.append(f"{name}: missing result on one runtime")
            entry["ok"] = False
            report["cases"].append(entry)
            continue
        if not left.get("ok") or not right.get("ok"):
            failures.append(
                f"{name}: search failed (native: {left.get('error')}, browser: {right.get('error')})"
            )
            entry["ok"] = False
            report["cases"].append(entry)
            continue
        state = position_states.get(name)
        move = _best_move(right)
        analysis = right.get("analysis") or {}
        entry.update(
            {
                "ok": True,
                "source": analysis.get("source"),
                "value": analysis.get("value"),
                "best_move": analysis.get("best_move"),
                "completed_simulations": (right.get("search") or {}).get("completed_simulations"),
            }
        )
        if analysis.get("source") not in ("neural_search", "terminal"):
            failures.append(f"{name}: unexpected analysis source {analysis.get('source')!r}")
        if state is not None and state.winner is None:
            if move is None or move not in legal_moves(state):
                failures.append(f"{name}: browser best move is not legal in the root position")
            value = analysis.get("value")
            if (
                not isinstance(value, (int, float))
                or not math.isfinite(value)
                or not -1 <= value <= 1
            ):
                failures.append(f"{name}: browser value is not finite within [-1, 1]")
        if level == "a":
            trace_left = (left.get("search") or {}).get("trace_sha256")
            trace_right = (right.get("search") or {}).get("trace_sha256")
            if not trace_left or trace_left != trace_right:
                failures.append(f"{name}: evaluation-trace digest differs or is missing")
            if _payload(left) != _payload(right):
                failures.append(f"{name}: deterministic search payload differs")
            else:
                exact_cases += 1
            entry["exact"] = _payload(left) == _payload(right)
        else:
            native_move = _best_move(left)
            terminal = (state is not None and state.winner is not None) or analysis.get(
                "source"
            ) == "terminal"
            # Terminal positions have no move on either runtime; that is agreement,
            # not a divergence.
            same_move = (
                native_move == move if terminal else native_move is not None and move == native_move
            )
            best_move_matches += int(same_move)
            if not same_move:
                divergent.append(name)
            left_value = (left.get("analysis") or {}).get("value")
            right_value = analysis.get("value")
            delta = (
                abs(float(left_value) - float(right_value))
                if isinstance(left_value, (int, float)) and isinstance(right_value, (int, float))
                else None
            )
            visits = _total_variation(_visits(left), _visits(right))
            simulations_delta = int(
                (right.get("search") or {}).get("completed_simulations", 0)
            ) - int((left.get("search") or {}).get("completed_simulations", 0))
            requests_delta = int((right.get("search") or {}).get("neural_requests", 0)) - int(
                (left.get("search") or {}).get("neural_requests", 0)
            )
            entry.update(
                {
                    "best_move_match": same_move,
                    "native_best_move": (left.get("analysis") or {}).get("best_move"),
                    "value_delta": delta,
                    "visit_total_variation": visits,
                    "completed_simulations_delta": simulations_delta,
                    "neural_requests_delta": requests_delta,
                }
            )
            if delta is not None and delta > VALUE_TOLERANCE and same_move:
                notes.append(f"{name}: same best move but value delta {delta:.3g}")
            if require_best_move and not same_move:
                failures.append(f"{name}: browser best move differs from the native reference")
        report["cases"].append(entry)
    if level == "a":
        report["exact_cases"] = exact_cases
        report["positions"] = len(native_positions)
        report["parity"] = exact_cases == len(native_positions)
        if not report["parity"]:
            failures.append("deterministic parity is incomplete")
    else:
        report["best_move_matches"] = best_move_matches
        report["positions"] = len(native_positions)
        report["divergent_cases"] = divergent
        report["best_move_parity"] = best_move_matches == len(native_positions)
        if divergent:
            notes.append(
                f"{len(divergent)} case(s) diverged under different inference numerics: "
                + ", ".join(divergent[:8])
            )
    report["ok"] = not failures
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("native", type=Path)
    parser.add_argument("browser", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--require-best-move-parity", action="store_true")
    args = parser.parse_args()
    report = compare(
        _load(args.native),
        _load(args.browser),
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
                "positions": report["positions"],
                "parity": report.get("parity", report.get("best_move_parity")),
                "exact_cases": report.get("exact_cases"),
                "best_move_matches": report.get("best_move_matches"),
                "failures": report["failures"],
                "notes": report["notes"],
            },
            indent=2,
        )
    )
    sys.exit(0 if report["ok"] else 1)


if __name__ == "__main__":
    main()
