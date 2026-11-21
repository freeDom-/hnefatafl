"""Native neural-MCTS parity harness and benchmark comparison contracts.

These run the native half of the comparison suite and the single comparison
implementation. They do not need Chromium, ORT Web or a staged model: browser
gates are the opt-in tests in ``test_browser_mcts_validation.py``.
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

import mcts_benchmark  # noqa: E402
import mcts_workload  # noqa: E402
from argparse import Namespace  # noqa: E402
import mcts_compare  # noqa: E402


def _native(
    tmp_path: Path, name: str, *extra: str, limit: str = "1", kinds: str | None = None
) -> dict:
    output = tmp_path / name
    result = subprocess.run(
        [
            sys.executable,
            str(SPIKE / "native_mcts_parity.py"),
            "--level",
            "a",
            "--iterations",
            "16",
            "--limit",
            limit,
            "--output",
            str(output),
            *extra,
            *(["--kinds", kinds] if kinds else []),
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(output.read_text())


@pytest.fixture(scope="module")
def parity_base(tmp_path_factory) -> dict:
    """A full-coverage native Level A report (all kinds, all rulesets)."""
    return _native(tmp_path_factory.mktemp("parity"), "base.json", limit="7")


def test_deterministic_parity_runs_are_exactly_reproducible(tmp_path):
    first = _native(tmp_path, "a.json")
    second = _native(tmp_path, "b.json")
    report = mcts_compare.compare(first, second, require_best_move=True)
    assert report["ok"] is True, report["failures"]
    assert report["deterministic_parity"]["ok"] is True
    assert report["deterministic_parity"]["exact_cases"] == 3
    assert report["execution_validity"]["ok"] is True
    assert report["workload_completion"]["complete"] is True
    assert report["acceptance"] is True


def test_deterministic_parity_detects_a_divergent_evaluation(tmp_path):
    first = _native(tmp_path, "a.json")
    second = _native(tmp_path, "b.json")
    # Simulate a browser that reported a different (still internally coherent)
    # search value for the same deterministic evaluations.
    record = second["results"][0]
    record["analysis"]["value"] += 1e-6
    record["search"]["search_value"] += 1e-6
    report = mcts_compare.compare(first, second, require_best_move=False)
    assert report["ok"] is False
    assert any("deterministic search payload differs" in failure for failure in report["failures"])
    assert report["deterministic_parity"]["ok"] is False


def test_parity_comparator_requires_matching_workload_and_coverage(tmp_path):
    first = _native(tmp_path, "a.json")
    second = _native(tmp_path, "b.json")
    second["workload_digest"] = "0" * 64
    second["positions"] = second["positions"][:-1]
    second["results"] = second["results"][:-1]
    report = mcts_compare.compare(first, second, require_best_move=False)
    failures = " ".join(report["failures"])
    assert "workload digest differs" in failures
    assert "position coverage differs" in failures
    assert "position coverage does not match the validated workload" in failures
    assert "missing result on one runtime" in failures


def test_parity_comparator_rejects_illegal_best_moves(tmp_path):
    first = _native(tmp_path, "a.json")
    second = _native(tmp_path, "b.json")
    for record in second["results"]:
        if record["analysis"]["source"] == "neural_search":
            record["analysis"]["best_move"] = {"source": 99, "target": 99}
            record["search"]["best_move"] = {"source": 99, "target": 99}
            break
    report = mcts_compare.compare(first, second, require_best_move=False)
    assert any("not legal" in failure for failure in report["failures"])
    assert report["execution_validity"]["ok"] is False


def test_workload_identity_normalises_python_javascript_numeric_types():
    from mcts_vectors import workload_identity

    positions = [
        {
            "name": "p",
            "ruleset": "brandubh",
            "kind": "opening",
            "state": mcts_vectors_state(),
        }
    ]
    first = workload_identity(
        "a",
        "deterministic",
        {"iterations": 128, "time_limit": 10, "seed": 7, "exploration": 1.4},
        positions,
        None,
    )
    second = workload_identity(
        "a",
        "deterministic",
        {"iterations": 128, "time_limit": 10.0, "seed": 7, "exploration": 1.4},
        positions,
        None,
    )
    assert first == second


def mcts_vectors_state() -> dict:
    from hnefatafl.engine import new_game

    return new_game("brandubh").to_dict()


def test_benchmark_digest_normalises_json_numeric_types():
    positions = [{"state": {"ruleset": "brandubh", "board": [], "turn": "attackers"}}]
    cases = [
        {"mode": "fixed_time", "budget": 1},
        {"mode": "fixed_simulations", "budget": 128.0},
    ]
    first = mcts_benchmark.digest_workload(
        positions, cases, seed=7, exploration=1.4, model_sha256="a" * 64
    )
    second = mcts_benchmark.digest_workload(
        positions, cases, seed=7.0, exploration=1.4, model_sha256="a" * 64
    )
    assert first == second
    other = mcts_benchmark.digest_workload(
        positions,
        [{"mode": "fixed_time", "budget": 2}, cases[1]],
        seed=7,
        exploration=1.4,
        model_sha256="a" * 64,
    )
    assert other != first


def _benchmark_payload() -> dict:
    workload = mcts_workload.build_fixtures_workload(["brandubh"], ["opening"], None)
    workload["cases"] = [{"mode": "fixed_simulations", "budget": 128}]
    workload["seed"] = 7
    workload["exploration"] = 1.4
    workload["repetitions"] = 1
    workload["warmup"] = 1
    return workload


def _benchmark_report(
    runtime: str,
    *,
    completed: int,
    sps: float,
    digest: str,
    records: list[dict] | None = None,
    workload: dict | None = None,
) -> dict:
    workload = copy.deepcopy(workload or _benchmark_payload())
    if records is None:
        records = [
            {
                "runtime": runtime,
                "provider": "onnxruntime-cpu",
                "ruleset": "brandubh",
                "position": "brandubh-opening",
                "kind": "opening",
                "mode": "fixed_simulations",
                "budget": 128,
                "repetition": 0,
                "warm": False,
                "ok": True,
                "requested_simulations": 128,
                "completed_simulations": completed,
                "elapsed_ms": completed / sps * 1000,
                "completion_reason": "simulations" if completed == 128 else "deadline",
                "deadline_cutoff": completed != 128,
                "neural_requests": completed + 1,
                "prediction_reuses": 3,
                "root_children": 8,
                "simulations_per_second": sps,
            }
        ]
    return {
        "kind": "mcts-benchmark",
        "schema": 1,
        "runtime": runtime,
        "engine_identity": {"sha256": "e" * 64},
        "workload_digest": digest,
        "workload": workload,
        "model": {"onnx_sha256": "a" * 64},
        "records": records,
        "summary": mcts_benchmark.summarize(records),
        "failures": [],
    }


def test_benchmark_comparison_only_reports_equal_work_ratios():
    native = _benchmark_report("native-cpython", completed=128, sps=1000.0, digest="d" * 64)
    browser = _benchmark_report("browser-pyodide", completed=128, sps=250.0, digest="d" * 64)
    report = mcts_benchmark.compare_reports(native, browser)
    assert report["ok"] is True
    assert report["performance"]["equal_work_cases"] == 1
    assert report["performance"]["median_browser_to_native_ratio"] == pytest.approx(0.25)
    assert report["workload_completion"]["browser_fixed_work_complete"] is True

    unequal = _benchmark_report("browser-pyodide", completed=64, sps=250.0, digest="d" * 64)
    report = mcts_benchmark.compare_reports(native, unequal)
    # An incomplete fixed-work repetition is a workload-completion failure, not a
    # legitimate unequal-work performance comparison.
    assert report["ok"] is False
    assert report["performance"]["equal_work_cases"] == 0
    assert report["performance"]["median_browser_to_native_ratio"] is None
    assert any("completed 64 of 128" in failure for failure in report["failures"])


def test_benchmark_comparator_rejects_missing_duplicate_and_failed_records():
    native = _benchmark_report("native-cpython", completed=128, sps=1000.0, digest="d" * 64)

    missing = _benchmark_report(
        "browser-pyodide", completed=128, sps=250.0, digest="d" * 64, records=[]
    )
    report = mcts_benchmark.compare_reports(native, missing)
    assert report["ok"] is False
    assert any("expected measured repetition(s) are missing" in f for f in report["failures"])

    duplicate = _benchmark_report("browser-pyodide", completed=128, sps=250.0, digest="d" * 64)
    duplicate["records"].append(copy.deepcopy(duplicate["records"][0]))
    report = mcts_benchmark.compare_reports(native, duplicate)
    assert any("duplicate measured repetition" in f for f in report["failures"])

    failed = _benchmark_report("browser-pyodide", completed=128, sps=250.0, digest="d" * 64)
    failed["records"][0]["ok"] = False
    report = mcts_benchmark.compare_reports(native, failed)
    assert report["execution_validity"]["failed_records"] == 1
    assert any("repetition failed" in f for f in report["failures"])


def test_benchmark_comparator_detects_a_case_omitted_from_both_reports():
    native = _benchmark_report("native-cpython", completed=128, sps=1000.0, digest="d" * 64)
    browser = _benchmark_report("browser-pyodide", completed=128, sps=250.0, digest="d" * 64)
    for report in (native, browser):
        # Both runtimes claim the same empty workload; the built-in source still
        # defines the complete canonical fixture set.
        report["workload"]["positions"] = []
        report["workload"]["expected_names"] = []
        report["workload"]["positions_sha256"] = mcts_workload.positions_sha256([])
        report["records"] = []
    comparison = mcts_benchmark.compare_reports(native, browser)
    assert comparison["ok"] is False
    assert any("non-empty list" in f for f in comparison["failures"])


def test_benchmark_comparator_keeps_fixed_time_comparisons_informative():
    workload = _benchmark_payload()
    workload["cases"] = [{"mode": "fixed_time", "budget": 1.0}]

    def fixed_time(runtime: str, completed: int) -> dict:
        record = {
            "runtime": runtime,
            "provider": "onnxruntime-cpu",
            "ruleset": "brandubh",
            "position": "brandubh-opening",
            "kind": "opening",
            "mode": "fixed_time",
            "budget": 1.0,
            "repetition": 0,
            "warm": False,
            "ok": True,
            "requested_simulations": 4096,
            "completed_simulations": completed,
            "elapsed_ms": 1005.0,
            "completion_reason": "deadline",
            "deadline_cutoff": True,
            "neural_requests": completed + 1,
            "prediction_reuses": 2,
            "root_children": 6,
            "simulations_per_second": completed / 1.005,
            "deadline_overshoot_ms": 5.0,
        }
        return _benchmark_report(
            runtime,
            completed=completed,
            sps=completed / 1.005,
            digest="d" * 64,
            records=[record],
            workload=workload,
        )

    report = mcts_benchmark.compare_reports(
        fixed_time("native-cpython", 3000), fixed_time("browser-pyodide", 700)
    )
    assert report["ok"] is True
    assert report["performance"]["equal_work_cases"] == 0
    assert report["performance"]["median_browser_to_native_ratio"] is None
    assert any("different work" in note for note in report["notes"])


def _export(checkpoint: Path, output: Path) -> None:
    import onnx_export

    code = onnx_export.run(
        Namespace(checkpoint=checkpoint, output=output, opset=18, force=False, no_parity=False)
    )
    assert code == 0


@pytest.fixture(scope="module")
def bound_models(tmp_path_factory):
    pytest.importorskip("onnx")
    pytest.importorskip("onnxruntime")
    from hnefatafl.models import PolicyValueNet, save_checkpoint

    import torch

    base = tmp_path_factory.mktemp("bound")
    paths = {}
    for name, seed in (("model-a", 1), ("model-b", 2)):
        torch.manual_seed(seed)
        model = PolicyValueNet(ruleset="brandubh", channels=4, blocks=0)
        path = base / f"{name}.pt"
        save_checkpoint(path, model, {"ruleset": "brandubh", "name": name})
        paths[name] = path
    _export(paths["model-a"], base / "export-a")
    _export(paths["model-b"], base / "export-b")
    return paths, base / "export-a"


def test_native_checkpoint_is_bound_to_the_exported_weights(bound_models):
    import native_mcts_parity

    paths, export_a = bound_models
    artifacts = native_mcts_parity.load_model_artifacts(export_a, paths["model-a"])
    assert artifacts["validated"] is True
    assert len(artifacts["weights_sha256"]) == 64
    # Same architecture and ruleset, different weights: rejected.
    with pytest.raises(SystemExit, match="weights do not match"):
        native_mcts_parity.load_model_artifacts(export_a, paths["model-b"])


def test_native_runtime_rejects_positions_outside_the_model_ruleset(bound_models, tmp_path):
    paths, export_a = bound_models
    output = tmp_path / "native.json"
    result = subprocess.run(
        [
            sys.executable,
            str(SPIKE / "native_mcts_parity.py"),
            "--level",
            "b",
            "--evaluator",
            "onnx",
            "--model-export",
            str(export_a),
            "--rulesets",
            "tablut",
            "--iterations",
            "8",
            "--limit",
            "1",
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode != 0
    assert "not covered by the exported model" in result.stderr


def test_export_without_source_fingerprint_is_rejected(bound_models, tmp_path):
    import json
    import shutil

    paths, export_a = bound_models
    legacy = tmp_path / "legacy-export"
    shutil.copytree(export_a, legacy)
    for name in ("VALIDATED.json", "parity.json", "export-manifest.json"):
        (legacy / name).unlink(missing_ok=True)
    sidecar = json.loads((legacy / "model.json").read_text())
    sidecar["schema"] = 1
    sidecar["source"].pop("weights_sha256")
    sidecar["source"].pop("encoder_version")
    (legacy / "model.json").write_text(json.dumps(sidecar))
    import native_mcts_parity

    with pytest.raises(SystemExit):
        native_mcts_parity.load_model_artifacts(legacy, paths["model-a"])


def _parity_report(base: dict) -> dict:
    return copy.deepcopy(base)


def _comparison(first: dict, second: dict, *, require_best_move: bool = False) -> dict:
    return mcts_compare.compare(first, second, require_best_move=require_best_move)


@pytest.mark.parametrize(
    "mutation,fragment",
    [
        (
            lambda r: r.update(schema=99),
            "unsupported schema",
        ),
        (
            lambda r: r["engine_identity"].pop("sha256"),
            "engine source identity is missing or malformed",
        ),
        (
            lambda r: r.pop("workload"),
            "invalid workload definition",
        ),
        (
            lambda r: r["results"][0]["search"].update(requested_simulations=8),
            "requested_simulations was redefined",
        ),
        (
            lambda r: r["results"][0]["search"]["root_visits"].append(
                dict(r["results"][0]["search"]["root_visits"][0])
            ),
            "duplicate root visit",
        ),
        (
            lambda r: r["results"][0]["search"]["root_visits"][0].update(share=float("nan")),
            "root visit share must be finite",
        ),
        (
            lambda r: r["results"][0]["search"]["root_visits"][0].update(visits=-1),
            "root visit count must be a non-negative integer",
        ),
        (
            lambda r: r["results"][0]["search"]["root_visits"][0].update(source=0, target=1),
            "is not legal in the root position",
        ),
        (
            lambda r: r["results"][0]["search"].update(completed_simulations=999),
            "completed simulations exceed the requested budget",
        ),
        (
            lambda r: r["results"][0].update(ok=False, error="forced failure"),
            "execution failed",
        ),
    ],
)
def test_parity_comparator_rejects_malformed_results(parity_base, mutation, fragment):
    first = _parity_report(parity_base)
    second = _parity_report(parity_base)
    mutation(second)
    report = _comparison(first, second)
    assert report["acceptance"] is False
    assert any(fragment in failure for failure in report["failures"]), report["failures"]


def test_parity_comparator_rejects_terminal_inference_and_skipped_budget(parity_base):
    first = _parity_report(parity_base)
    second = _parity_report(parity_base)
    terminal = next(
        record
        for record in second["results"]
        if (record.get("search") or {}).get("completion_reason") == "terminal"
    )
    terminal["search"]["neural_requests"] = 1
    report = _comparison(first, second)
    assert any("terminal position requested inference" in f for f in report["failures"])

    second = _parity_report(parity_base)
    incomplete = next(
        record
        for record in second["results"]
        if (record.get("search") or {}).get("completion_reason") == "simulations"
    )
    budget = second["settings"]["iterations"]
    incomplete["search"].update(
        completed_simulations=budget - 1,
        completion_reason="insufficient_budget",
    )
    incomplete["analysis"]["simulations"] = budget - 1
    report = _comparison(first, second)
    assert report["workload_completion"]["complete"] is False
    assert any("did not complete" in failure for failure in report["failures"])


def test_parity_comparator_detects_a_case_omitted_from_both_reports(parity_base):
    first = _parity_report(parity_base)
    second = _parity_report(parity_base)
    for report in (first, second):
        report["positions"] = report["positions"][:-1]
        report["results"] = report["results"][:-1]
    comparison = _comparison(first, second)
    assert comparison["acceptance"] is False
    assert any(
        "report positions do not match the validated workload" in failure
        for failure in comparison["failures"]
    )


def test_level_b_divergence_is_reported_but_not_a_correctness_failure(parity_base):
    native = _parity_report(parity_base)
    browser = _parity_report(parity_base)
    from hnefatafl.engine import State
    from hnefatafl.mcts_driver import position_identity, validate_search_settings
    from mcts_vectors import workload_identity

    for report in (native, browser):
        report["level"] = "b"
        report["model_meta"] = {"onnx_sha256": "a" * 64, "validated": True}
        report["evaluator"] = "onnx"
        settings = validate_search_settings(report["settings"])
        report["workload_digest"] = workload_identity(
            "b", "onnx", settings, report["workload"]["positions"], "a" * 64
        )
        states = {entry["name"]: State.from_dict(entry["state"]) for entry in report["positions"]}
        for record in report["results"]:
            record["search"]["model"]["sha256"] = "a" * 64
            identity = position_identity(
                states[record["name"]], settings, record["search"]["model"]
            )
            record["search"]["position_identity"] = identity
            record["path"] = identity
    # A numerical provider may choose a different (legal) best move; measured.
    record = next(
        r
        for r in browser["results"]
        if r["analysis"]["source"] == "neural_search" and len(r["search"]["root_visits"]) > 1
    )
    current = record["analysis"]["best_move"]
    alternative = next(
        row
        for row in record["search"]["root_visits"]
        if {"source": row["source"], "target": row["target"]} != current
    )
    move = {"source": alternative["source"], "target": alternative["target"]}
    record["analysis"]["best_move"] = move
    record["search"]["best_move"] = move
    comparison = _comparison(native, browser)
    assert comparison["execution_validity"]["ok"] is True
    assert comparison["numerical_comparison"]["best_move_parity"] is False
    assert comparison["numerical_comparison"]["divergent_cases"]
    # Divergence is not an execution or workload failure.
    assert comparison["acceptance"] is True, comparison["failures"]
    strict = _comparison(native, browser, require_best_move=True)
    assert strict["acceptance"] is False


def _mutable_completion(report: dict, name: str, **changes) -> None:
    record = next(item for item in report["results"] if item["name"] == name)
    record["search"].update(changes)
    if "completed_simulations" in changes:
        record["analysis"]["simulations"] = changes["completed_simulations"]


def _incomplete_case(report: dict) -> str:
    return next(
        record["name"]
        for record in report["results"]
        if (record.get("search") or {}).get("completion_reason") == "simulations"
    )


def test_fixed_work_acceptance_requires_both_runtimes_to_complete(parity_base):
    native = _parity_report(parity_base)
    browser = _parity_report(parity_base)
    requested = native["settings"]["iterations"]
    report = _comparison(native, browser)
    assert report["acceptance"] is True, report["failures"]
    assert report["workload_completion"]["native_complete"] is True
    assert report["workload_completion"]["browser_complete"] is True
    # Level A reports deterministic parity; equal-work performance comparability
    # is a level-B/benchmark concern.
    assert report["performance_comparability"]["equal_work_cases"] is None

    case = _incomplete_case(native)
    native_incomplete = _parity_report(parity_base)
    _mutable_completion(
        native_incomplete,
        case,
        completed_simulations=requested - 1,
        completion_reason="insufficient_budget",
        deadline_cutoff=False,
    )
    report = _comparison(native_incomplete, _parity_report(parity_base))
    assert report["acceptance"] is False
    assert report["workload_completion"]["native_complete"] is False
    assert report["workload_completion"]["browser_complete"] is True
    assert any("native fixed-work workload did not complete" in f for f in report["failures"])

    browser_incomplete = _parity_report(parity_base)
    _mutable_completion(
        browser_incomplete,
        case,
        completed_simulations=requested - 1,
        completion_reason="insufficient_budget",
        deadline_cutoff=False,
    )
    report = _comparison(_parity_report(parity_base), browser_incomplete)
    assert report["acceptance"] is False
    assert report["workload_completion"]["native_complete"] is True
    assert report["workload_completion"]["browser_complete"] is False
    assert any("browser fixed-work workload did not complete" in f for f in report["failures"])

    both = _parity_report(parity_base)
    _mutable_completion(
        both,
        case,
        completed_simulations=requested - 1,
        completion_reason="insufficient_budget",
        deadline_cutoff=False,
    )
    report = _comparison(native_incomplete, both)
    assert report["acceptance"] is False
    assert report["workload_completion"]["complete"] is False
    assert len(report["workload_completion"]["incomplete_cases"]) >= 1


def test_native_deadline_limited_fixed_work_is_incomplete(parity_base):
    browser = _parity_report(parity_base)
    native = _parity_report(parity_base)
    requested = native["settings"]["iterations"]
    case = _incomplete_case(native)
    _mutable_completion(
        native,
        case,
        completed_simulations=requested - 1,
        completion_reason="deadline",
        deadline_cutoff=True,
    )
    report = _comparison(native, browser)
    assert report["acceptance"] is False
    assert case in report["workload_completion"]["native_deadline_limited_cases"]
    assert report["workload_completion"]["native_complete"] is False
    assert any("native fixed-work workload was deadline limited" in f for f in report["failures"])


def _custom_report_with_immediate_win(tmp_path: Path) -> dict:
    from hnefatafl.engine import Move, State, apply_move, legal_moves, new_game

    opening = new_game("brandubh")
    move = legal_moves(opening)[0]
    key = apply_move(opening, move).history[-1]
    repetition = {
        "name": "terminal-repetition",
        "kind": "repetition",
        "state": {
            **opening.to_dict(),
            "history": [key, key],
        },
    }
    size = 7
    board = ["."] * (size * size)
    board[1] = "K"
    board[3] = "A"
    board[3 * size + 3] = "D"
    board[5 * size + 5] = "A"
    immediate = {
        "name": "immediate-attacker-win",
        "kind": "tactical",
        "state": State("brandubh", tuple(board), "attackers").to_dict(),
    }
    escape_board = ["."] * (size * size)
    escape_board[2] = "K"
    escape_board[4 * size + 4] = "A"
    terminal = apply_move(State("brandubh", tuple(escape_board), "defenders"), Move(2, 0))
    terminal_case = {
        "name": "terminal-defender-win",
        "kind": "terminal",
        "state": terminal.to_dict(),
    }
    positions = tmp_path / "semantic-positions.json"
    positions.write_text(json.dumps([repetition, immediate, terminal_case], separators=(",", ":")))
    return _native(
        tmp_path,
        "native.json",
        "--positions",
        str(positions),
        "--rulesets",
        "brandubh",
    )


def test_terminal_and_immediate_win_are_legitimate_fixed_work_exceptions(tmp_path):
    native = _custom_report_with_immediate_win(tmp_path)
    browser = copy.deepcopy(native)
    report = _comparison(native, browser)
    assert report["acceptance"] is True, report["failures"]
    classes = report["completion_classes"]
    assert classes["immediate-attacker-win"] == "immediate_win/immediate_win"
    assert classes["terminal-defender-win"] == "terminal/terminal"
    assert classes["terminal-repetition"] in (
        "complete/complete",
        "terminal/terminal",
    )
    # Immediate-win work is not counted as an equal-work performance measurement.
    equal_names = {
        f"{row['position']}"
        for row in report["performance_comparability"].get("cases", [])
        if row.get("equal_work")
    }
    assert "immediate-attacker-win" not in equal_names


@pytest.mark.parametrize(
    "mutation",
    [
        lambda native, browser: native.update(workload_digest="0" * 64),
        lambda native, browser: browser.update(workload_digest="0" * 64),
        lambda native, browser: (
            native.update(workload_digest="0" * 64),
            browser.update(workload_digest="0" * 64),
        ),
        lambda native, browser: (
            native["settings"].update(exploration=0.5),
            browser["settings"].update(exploration=0.5),
        ),
        lambda native, browser: (
            native["workload"]["positions"][0]["state"]["board"].__setitem__(0, "A"),
            browser["workload"]["positions"][0]["state"]["board"].__setitem__(0, "A"),
        ),
        lambda native, browser: (
            native["workload"]["positions"][0]["state"]["history"].append(
                "defenders:" + "".join(native["workload"]["positions"][0]["state"]["board"])
            ),
            browser["workload"]["positions"][0]["state"]["history"].append(
                "defenders:" + "".join(browser["workload"]["positions"][0]["state"]["board"])
            ),
        ),
        lambda native, browser: (
            native.update(level="b", evaluator="onnx", model_meta={"onnx_sha256": "a" * 64}),
            browser.update(level="b", evaluator="onnx", model_meta={"onnx_sha256": "a" * 64}),
        ),
        lambda native, browser: (
            native.update(evaluator="deterministic-other"),
            browser.update(evaluator="deterministic-other"),
        ),
    ],
)
def test_parity_comparator_independently_recomputes_workload_identity(parity_base, mutation):
    native = _parity_report(parity_base)
    browser = _parity_report(parity_base)
    mutation(native, browser)
    report = _comparison(native, browser)
    assert report["acceptance"] is False
    assert report["workload"]["identity_verified"] is False
    assert any(
        "workload digest does not match" in failure
        or "independently recomputed workload identities differ" in failure
        or "could not be independently recomputed" in failure
        for failure in report["failures"]
    ), report["failures"]


def _level_b_reports(parity_base: dict) -> tuple[dict, dict]:
    from hnefatafl.engine import State
    from hnefatafl.mcts_driver import position_identity, validate_search_settings
    from mcts_vectors import workload_identity

    native = _parity_report(parity_base)
    browser = _parity_report(parity_base)
    for report in (native, browser):
        report["level"] = "b"
        report["model_meta"] = {"onnx_sha256": "a" * 64, "validated": True}
        report["evaluator"] = "onnx"
        settings = validate_search_settings(report["settings"])
        report["workload_digest"] = workload_identity(
            "b", "onnx", settings, report["workload"]["positions"], "a" * 64
        )
        states = {entry["name"]: State.from_dict(entry["state"]) for entry in report["positions"]}
        for record in report["results"]:
            record["search"]["model"]["sha256"] = "a" * 64
            identity = position_identity(
                states[record["name"]], settings, record["search"]["model"]
            )
            record["search"]["position_identity"] = identity
            record["path"] = identity
    return native, browser


def test_level_b_workload_identity_uses_the_exported_model_digest(parity_base):
    native, browser = _level_b_reports(parity_base)
    report = _comparison(native, browser)
    assert report["workload"]["identity_verified"] is True
    assert report["acceptance"] is True, report["failures"]
    browser["model_meta"]["onnx_sha256"] = "b" * 64
    report = _comparison(native, browser)
    assert report["acceptance"] is False
    assert any("model digest differs" in failure for failure in report["failures"])
    assert any("workload digest does not match" in failure for failure in report["failures"])


@pytest.mark.parametrize(
    "field",
    [
        "requested_simulations",
        "completed_simulations",
        "neural_requests",
        "prediction_reuses",
        "immediate_win_shortcuts",
        "root_children",
        "inference_cache_hits",
        "trace_length",
    ],
)
def test_parity_comparator_rejects_negative_diagnostic_counters(parity_base, field):
    native = _parity_report(parity_base)
    browser = _parity_report(parity_base)
    record = next(
        item
        for item in browser["results"]
        if (item.get("search") or {}).get("completion_reason") == "simulations"
    )
    record["search"][field] = -1
    report = _comparison(native, browser)
    assert report["acceptance"] is False
    assert any(
        f"search.{field} must be a non-negative integer" in failure
        for failure in report["failures"]
    ), report["failures"]


def test_parity_comparator_rejects_negative_counters_in_level_b(parity_base):
    native, browser = _level_b_reports(parity_base)
    record = next(
        item
        for item in browser["results"]
        if (item.get("search") or {}).get("completion_reason") == "simulations"
    )
    record["search"]["prediction_reuses"] = -1
    report = _comparison(native, browser)
    assert report["acceptance"] is False
    assert any(
        "search.prediction_reuses must be a non-negative integer" in failure
        for failure in report["failures"]
    ), report["failures"]


def test_parity_comparator_accepts_zero_counters_on_terminal_positions(parity_base):
    native = _parity_report(parity_base)
    browser = _parity_report(parity_base)
    terminal = next(
        item
        for item in browser["results"]
        if (item.get("search") or {}).get("completion_reason") == "terminal"
    )
    for field in (
        "neural_requests",
        "prediction_reuses",
        "immediate_win_shortcuts",
        "root_children",
        "inference_cache_hits",
    ):
        terminal["search"][field] = 0
    report = _comparison(native, browser)
    assert report["acceptance"] is True, report["failures"]
