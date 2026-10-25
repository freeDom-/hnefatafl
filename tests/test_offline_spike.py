"""Native fixture/harness checks; browser parity requires the separate spike run."""

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

from hnefatafl.engine import Move, State, apply_move, legal_moves

SPIKE = Path(__file__).resolve().parents[1] / "spikes/browser_offline"


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
