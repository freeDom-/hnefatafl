"""Native fixture/harness checks; browser parity requires the separate spike run."""

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

from hnefatafl.engine import Move, State, apply_move, legal_moves

SPIKE = Path(__file__).resolve().parents[1] / "spikes/browser_offline"
if str(SPIKE) not in sys.path:
    sys.path.insert(0, str(SPIKE))


def test_reference_fixture_coverage_and_serialization():
    spec = importlib.util.spec_from_file_location("offline_fixtures", SPIKE / "fixtures.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    cases = json.loads(json.dumps(module.vectors()))
    assert len(cases) == 34
    assert {case["before"]["state"]["ruleset"] for case in cases} == {
        "brandubh",
        "tablut",
        "copenhagen",
    }
    for case in cases:
        state = State.from_dict(case["before"]["state"])
        assert [m.to_dict() for m in legal_moves(state)] == case["before"]["legal_moves"]
        after = apply_move(state, Move(**case["move"]))
        assert after.to_dict() == case["after"]["state"]
    assert any(case["after"]["state"]["winner"] == "draw" for case in cases)
    assert any(case["after"]["state"]["reason"] == "threefold_repetition" for case in cases)


def test_staged_shell_has_no_runtime_or_production_dependencies(tmp_path):
    subprocess.run(
        [sys.executable, str(SPIKE / "prepare.py"), "--shell-only", "--output", str(tmp_path)],
        check=True,
    )
    manifest = json.loads((tmp_path / "assets.json").read_text())
    assert not manifest["runtime_included"]
    assert not (tmp_path / "runtime").exists()
    assert "__BUNDLE_ID__" not in (tmp_path / "sw.js").read_text()
    assert len(json.loads((tmp_path / "fixtures.json").read_text())) == 34
    assert (tmp_path / "python/hnefatafl/engine.py").read_bytes() == (
        SPIKE.parents[1] / "hnefatafl/engine.py"
    ).read_bytes()
    for path in SPIKE.glob("*.py"):
        compile(path.read_text(), str(path), "exec")


def test_normal_staging_excludes_the_dedicated_test_worker():
    preparing = (SPIKE / "prepare_neural.py").read_text()
    normal = (SPIKE / "neural-worker.js").read_text()
    test_worker = (SPIKE / "neural-worker-test.js").read_text()
    assert "neural-worker-test.js" not in preparing
    assert "testHooksEnabled" not in normal
    assert "async function testDelay" not in normal
    assert "handleTestAction" in normal
    assert 'importScripts("./neural-worker.js")' in test_worker
    assert "__neuralTestAdapter" in test_worker


def test_native_bridge_exercises_same_worker_interface():
    namespace = {}
    exec(compile((SPIKE / "bridge.py").read_text(), "bridge.py", "exec"), namespace)
    dispatch = namespace["dispatch"]
    for rule in ("brandubh", "tablut", "copenhagen"):
        initial = json.loads(dispatch(json.dumps({"action": "new", "ruleset": rule})))
        moved = json.loads(
            dispatch(
                json.dumps(
                    {"action": "move", "state": initial["state"], "move": initial["legal_moves"][0]}
                )
            )
        )
        ai = json.loads(dispatch(json.dumps({"action": "ai", "state": moved["state"]})))
        assert ai["state"]["ply"] == 2 and ai["move"] in moved["legal_moves"]
        assert ai["ai_ms"] >= 0


def _analysis_bridge():
    namespace = {}
    exec(compile((SPIKE / "bridge.py").read_text(), "bridge.py", "exec"), namespace)
    return namespace["dispatch"]


def test_staged_classical_dependency_closure_is_unchanged(tmp_path):
    subprocess.run(
        [sys.executable, str(SPIKE / "prepare.py"), "--shell-only", "--output", str(tmp_path)],
        check=True,
    )
    manifest = json.loads((tmp_path / "assets.json").read_text())
    expected = {
        "__init__.py",
        "engine.py",
        "ai.py",
        "openings.py",
        "symmetry.py",
        "tournament_statistics.py",
        "shield_maiden.py",
    }
    assert set(manifest["python_modules"]) == expected
    for name in expected:
        assert (tmp_path / "python/hnefatafl" / name).read_bytes() == (
            SPIKE.parents[1] / "hnefatafl" / name
        ).read_bytes()
    assert "models.py" not in manifest["python_modules"]
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.path.insert(0, sys.argv[1]); "
            "from hnefatafl.ai import analyze_position; "
            "assert 'torch' not in sys.modules",
            str(tmp_path / "python"),
        ],
        check=True,
        capture_output=True,
    )
    assert result.returncode == 0
    references = json.loads((tmp_path / "search-fixtures.json").read_text())
    assert len(references) == 3
    assert all(row["analysis"]["completed_depth"] == 1 for row in references)


def test_local_analysis_preserves_states_and_uses_real_completed_diagnostics():
    from hnefatafl.engine import new_game

    dispatch = _analysis_bridge()
    for rule in ("brandubh", "tablut", "copenhagen"):
        state = new_game(rule).to_dict()
        before = json.dumps(state, sort_keys=True)
        for algorithm in ("baseline", "alpha_beta", "mcts"):
            result = json.loads(
                dispatch(
                    json.dumps(
                        {
                            "action": "analysis",
                            "state": state,
                            "config": {
                                "algorithm": algorithm,
                                "depth": 1,
                                "time_limit": 0.02,
                                "iterations": 4,
                                "rollout_depth": 0,
                            },
                        }
                    )
                )
            )
            assert result["schema"] == 1 and result["authoritative"] is False
            assert result["backend"] == "local-python"
            assert result["ruleset"] == rule
            assert -1 <= result["analysis"]["value"] <= 1
            assert result["analysis"]["best_move"] in [
                m.to_dict() for m in legal_moves(State.from_dict(state))
            ]
            assert json.dumps(state, sort_keys=True) == before


def test_local_terminal_repetition_and_history_identity():
    from hnefatafl.engine import new_game

    dispatch = _analysis_bridge()
    state = new_game("brandubh").to_dict()
    first = json.loads(dispatch(json.dumps({"action": "analysis", "state": state})))
    state["history"] = ["attackers:" + "".join(state["board"])] * 2
    second = json.loads(dispatch(json.dumps({"action": "analysis", "state": state})))
    assert first["position_key"] != second["position_key"]
    spec = importlib.util.spec_from_file_location("analysis_fixtures", SPIKE / "fixtures.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for row in module.vectors():
        if row["after"]["state"]["winner"] is None:
            continue
        result = json.loads(
            dispatch(json.dumps({"action": "analysis", "state": row["after"]["state"]}))
        )
        assert result["analysis"]["best_move"] is None
        assert result["analysis"]["source"] == "terminal"
        assert result["analysis"]["value"] == (
            0
            if row["after"]["state"]["winner"] == "draw"
            else 1
            if row["after"]["state"]["winner"] == "defenders"
            else -1
        )


def test_local_analysis_refuses_unbounded_and_neural_inputs():
    import pytest
    from hnefatafl.engine import new_game

    dispatch = _analysis_bridge()
    # Bounds are the documented experimental limits: depth 1-8 (the engine's
    # alpha-beta cap), 4096 simulations, 50 rollout plies and a 10 s cooperative
    # deadline. A value one step beyond each bound must still be rejected.
    for config in (
        {"algorithm": "neural"},
        {"model_path": "arbitrary.pt"},
        {"iterations": 4097},
        {"depth": 9},
        {"depth": 10},
        {"rollout_depth": 51},
        {"time_limit": 10.01},
        {"time_limit": float("nan")},
        {"time_limit": True},
        {"seed": -1},
        {"heuristic": "unknown"},
        {"search": {"unknown": True}},
    ):
        with pytest.raises(ValueError):
            dispatch(
                json.dumps(
                    {
                        "action": "analysis",
                        "state": new_game("brandubh").to_dict(),
                        "config": config,
                    }
                )
            )
    with pytest.raises(ValueError, match="512 KiB"):
        dispatch(" " * (512 * 1024 + 1))


def test_extended_analysis_bounds_are_accepted():
    dispatch = _analysis_bridge()

    # Depth 8 is the engine cap and a 1 s deadline is within the new bound. The
    # tactical king-escape position keeps the completed search small.
    spec = importlib.util.spec_from_file_location("analysis_fixtures", SPIKE / "fixtures.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    state = next(
        case["before"]["state"]
        for case in module.vectors()
        if case["name"] == "brandubh-king-escape"
    )
    result = json.loads(
        dispatch(
            json.dumps(
                {
                    "action": "analysis",
                    "state": state,
                    "config": {
                        "algorithm": "alpha_beta",
                        "depth": 8,
                        "time_limit": 10.0,
                        "search": {"table_capacity": 1000},
                    },
                }
            )
        )
    )
    assert result["settings"]["depth"] == 8
    assert result["settings"]["search"]["table_capacity"] == 1000
    assert result["analysis"]["completed_depth"] == 8


def test_jarl_plan_reports_the_engine_depth_constraint():
    dispatch = _analysis_bridge()
    plan = json.loads(dispatch(json.dumps({"action": "jarl_plan"})))
    assert plan["configs"]["standard"] == {"depth": 5, "time_limit": 2.0}
    assert plan["production_jarl"] == {"depth": 5, "time_limit": 2.0}
    assert plan["engine_max_depth"] == 8
    assert plan["unsupported"] == {"extended": "engine alpha-beta depth cap is 8"}


def test_jarl_benchmark_runs_shared_positions_in_the_worker_contract():
    dispatch = _analysis_bridge()
    spec = importlib.util.spec_from_file_location("analysis_fixtures", SPIKE / "fixtures.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    positions = [case for case in module.jarl_positions() if case["name"] == "brandubh-king-escape"]
    result = json.loads(
        dispatch(
            json.dumps(
                {
                    "action": "jarl_benchmark",
                    "positions": positions,
                    "configs": ["quick", "extended"],
                    "mode": "fixed_time",
                    "seed": 7,
                }
            )
        )
    )
    assert result["mode"] == "fixed_time"
    records = {(r["config"], r["position"]): r for r in result["records"]}
    quick = records[("quick", "brandubh-king-escape")]
    assert quick["completed_depth"] == 3 and quick["error"] is None
    assert quick["time_limit_ms"] == 250.0
    # The engine rejects depth 10, so the unsupported row is reported per case
    # instead of being silently reduced to a runnable depth.
    extended = records[("extended", "brandubh-king-escape")]
    assert extended["error"] and "between 1 and 8" in extended["error"]


def test_jarl_identity_is_stable_and_covers_effective_configuration():
    dispatch = _analysis_bridge()
    spec = importlib.util.spec_from_file_location("analysis_fixtures", SPIKE / "fixtures.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    positions = [case for case in module.jarl_positions() if case["name"] == "brandubh-king-escape"]
    first = json.loads(
        dispatch(
            json.dumps(
                {
                    "action": "jarl_identity",
                    "positions": positions,
                    "configs": ["quick"],
                    "mode": "fixed_work",
                    "seed": 7,
                }
            )
        )
    )
    assert first["protocol"] == 3
    assert len(first["workload_digest"]) == 64
    assert first["effective_configs"]["quick"]["depth"] == 3
    assert first["engine_identity"]["sha256"]
    second = json.loads(
        dispatch(
            json.dumps(
                {
                    "action": "jarl_identity",
                    "positions": positions,
                    "configs": ["quick"],
                    "mode": "fixed_time",
                    "seed": 7,
                }
            )
        )
    )
    # Mode and effective settings are part of the identity.
    assert first["workload_digest"] != second["workload_digest"]


def test_jarl_case_runs_one_pair_and_rejects_multiples():
    import pytest

    dispatch = _analysis_bridge()
    spec = importlib.util.spec_from_file_location("analysis_fixtures", SPIKE / "fixtures.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    positions = [case for case in module.jarl_positions() if case["name"] == "brandubh-king-escape"]
    record = json.loads(
        dispatch(
            json.dumps(
                {
                    "action": "jarl_case",
                    "positions": positions,
                    "configs": ["quick"],
                    "mode": "fixed_work",
                    "seed": 7,
                }
            )
        )
    )
    assert record["status"] == "completed"
    assert record["completed_depth"] == record["expected_depth"] == 3
    with pytest.raises(ValueError):
        dispatch(
            json.dumps(
                {
                    "action": "jarl_case",
                    "positions": positions + positions,
                    "configs": ["quick"],
                }
            )
        )
    with pytest.raises(ValueError):
        dispatch(
            json.dumps(
                {"action": "jarl_case", "positions": positions, "configs": ["quick", "standard"]}
            )
        )


def test_jarl_benchmark_rejects_duplicate_position_names():
    import pytest

    ns = {}
    exec(compile((SPIKE / "bridge.py").read_text(), "bridge.py", "exec"), ns)
    dispatch = ns["dispatch"]
    from fixtures import jarl_positions

    position = next(p for p in jarl_positions() if p["name"] == "brandubh-king-escape")
    with pytest.raises(ValueError, match="unique"):
        dispatch(
            json.dumps(
                {
                    "action": "jarl_identity",
                    "positions": [position, position],
                    "configs": ["quick"],
                }
            )
        )


def test_jarl_benchmark_rejects_unbounded_requests():
    import pytest

    dispatch = _analysis_bridge()
    for request in (
        {"action": "jarl_benchmark", "positions": [], "configs": ["quick"]},
        {"action": "jarl_benchmark", "positions": [{}] * 65, "configs": ["quick"]},
        {"action": "jarl_benchmark", "positions": [{}], "configs": ["quick"] * 9},
        {"action": "jarl_benchmark", "positions": [{}], "configs": []},
        {"action": "jarl_benchmark", "positions": [{}], "configs": ["quick"], "mode": "x"},
        {"action": "jarl_benchmark", "positions": [{}], "configs": ["quick"], "seed": -1},
    ):
        with pytest.raises(ValueError):
            dispatch(json.dumps(request))


def test_production_jarl_config_matches_direct_search():
    from hnefatafl.ai import analyze_position
    from hnefatafl.engine import new_game

    spec = importlib.util.spec_from_file_location("jarl_benchmark", SPIKE / "jarl_benchmark.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    state = new_game("brandubh")
    config = {
        "algorithm": "alpha_beta",
        "depth": 5,
        "time_limit": 2.0,
        "heuristic": "shield_maiden",
        "search": dict(module.PRODUCTION_SEARCH),
        "seed": 7,
    }
    direct = analyze_position(state, config)
    via_bridge = json.loads(
        _analysis_bridge()(
            json.dumps(
                {
                    "action": "analysis",
                    "state": state.to_dict(),
                    "config": {
                        "algorithm": "alpha_beta",
                        "depth": 5,
                        "time_limit": 2.0,
                        "heuristic": "shield_maiden",
                        "search": dict(module.PRODUCTION_SEARCH),
                        "seed": 7,
                    },
                }
            )
        )
    )["analysis"]
    assert via_bridge["best_move"] == direct["best_move"]
    assert via_bridge["value"] == direct["value"]


def test_input_size_uses_utf8_bytes_and_serialized_boundary():
    import pytest

    dispatch = _analysis_bridge()
    base = json.dumps({"action": "info", "pad": ""})
    overhead = len(base.encode("utf-8"))
    exact = json.dumps({"action": "info", "pad": "x" * (512 * 1024 - overhead)})
    assert len(exact.encode("utf-8")) == 512 * 1024
    assert json.loads(dispatch(exact))["strategy"] == "baseline"
    over = json.dumps({"action": "info", "pad": "x" * (512 * 1024 - overhead + 1)})
    with pytest.raises(ValueError, match="512 KiB"):
        dispatch(over)
    # Multibyte characters: the string length is within 512 Ki chars but the
    # serialized UTF-8 payload is larger, so the byte boundary must reject it.
    unicode_payload = json.dumps(
        {"action": "info", "pad": "é" * (512 * 1024 - overhead)},
        ensure_ascii=False,
    )
    assert len(unicode_payload) <= 512 * 1024
    assert len(unicode_payload.encode("utf-8")) > 512 * 1024
    with pytest.raises(ValueError, match="512 KiB"):
        dispatch(unicode_payload)
    with pytest.raises(ValueError, match="serialized JSON string"):
        dispatch({"action": "info"})


def test_staging_refuses_runtime_digest_mismatch(tmp_path):
    runtime = tmp_path / "runtime-input"
    runtime.mkdir()
    (runtime / "pyodide.js").write_text("wrong runtime")
    result = subprocess.run(
        [
            sys.executable,
            str(SPIKE / "prepare.py"),
            "--runtime-dir",
            str(runtime),
            "--output",
            str(tmp_path / "output"),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "Pinned runtime digest mismatch: pyodide.js" in result.stderr
