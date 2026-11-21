"""Semantic search-result validation and versioned workload contracts."""

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

import mcts_compare  # noqa: E402
import mcts_vectors  # noqa: E402
import mcts_workload  # noqa: E402
from hnefatafl.engine import Move, State, apply_move, new_game  # noqa: E402

SETTINGS = {"iterations": 8, "time_limit": 10.0, "seed": 0, "exploration": 1.4}


def _board(size: int, pieces: dict) -> tuple[str, ...]:
    board = ["."] * (size * size)
    for (row, column), piece in pieces.items():
        board[row * size + column] = piece
    return tuple(board)


def _attacker_win() -> State:
    # The king is gone: the engine allows a kingless board only for an attacker
    # win, which is the canonical terminal value -1.
    return State("brandubh", tuple("." for _ in range(49)), winner="attackers")


def _draw() -> State:
    return State("brandubh", new_game("brandubh").board, winner="draw")


def _defender_win() -> State:
    escape = State("brandubh", _board(7, {(0, 2): "K", (4, 4): "A"}), "defenders")
    terminal = apply_move(escape, Move(2, 0))
    assert terminal.winner == "defenders"
    return terminal


def _immediate_win() -> State:
    return State(
        "brandubh",
        _board(7, {(0, 1): "K", (0, 3): "A", (3, 3): "D", (5, 5): "A"}),
        "attackers",
    )


def _result(state: State, *, name: str = "case") -> tuple[dict, dict]:
    event = mcts_vectors.run_search(state.to_dict(), SETTINGS)
    assert event["kind"] == "complete"
    entry = {
        "name": name,
        "ruleset": state.ruleset,
        "kind": "terminal" if state.winner else "tactical",
        "state": state.to_dict(),
    }
    record = {
        "name": name,
        "ruleset": state.ruleset,
        "kind": entry["kind"],
        "path": event["search"]["position_identity"],
        "ok": True,
        "analysis": copy.deepcopy(event["analysis"]),
        "search": copy.deepcopy(event["search"]),
    }
    return entry, record


def _validate(state: State, entry: dict, record: dict) -> list[str]:
    return mcts_compare._validate_search(
        record,
        {"entry": entry, "state": state},
        SETTINGS,
        record["search"].get("model"),
        level="a",
        side="test",
    )


@pytest.mark.parametrize(
    "factory,expected",
    [(_defender_win, 1.0), (_attacker_win, -1.0), (_draw, 0.0)],
)
def test_terminal_values_are_exactly_defender_relative(factory, expected):
    state = factory()
    entry, record = _result(state)
    assert record["analysis"]["value"] == expected
    assert _validate(state, entry, record) == []
    # A wrong sign or magnitude must fail for the terminal-value reason.
    for wrong in (
        -expected if expected != 0 else 1.0,
        expected + 0.5 if expected != 0 else -1.0,
    ):
        broken = copy.deepcopy(record)
        broken["analysis"]["value"] = wrong
        broken["search"]["search_value"] = wrong
        failures = _validate(state, entry, broken)
        assert any("is not the canonical" in failure for failure in failures), failures


def test_terminal_inference_and_best_move_are_rejected():
    state = _defender_win()
    entry, record = _result(state)
    broken = copy.deepcopy(record)
    broken["search"]["neural_requests"] = 1
    assert any("requested inference" in f for f in _validate(state, entry, broken))
    broken = copy.deepcopy(record)
    broken["analysis"]["best_move"] = {"source": 0, "target": 1}
    broken["search"]["best_move"] = {"source": 0, "target": 1}
    assert any("reported a best move" in f for f in _validate(state, entry, broken))


def test_immediate_win_is_a_valid_control():
    state = _immediate_win()
    entry, record = _result(state)
    assert record["analysis"]["value"] == -1.0
    assert record["search"]["completion_reason"] == "immediate_win"
    assert record["analysis"]["best_move"] is not None
    assert _validate(state, entry, record) == []


def test_successful_nonterminal_search_requires_a_consistent_best_move():
    state = new_game("brandubh")
    entry, record = _result(state)
    assert record["search"]["completed_simulations"] == SETTINGS["iterations"]
    assert _validate(state, entry, record) == []

    missing = copy.deepcopy(record)
    missing["analysis"]["best_move"] = None
    missing["search"]["best_move"] = None
    assert any("reported no best move" in f for f in _validate(state, entry, missing))

    illegal = copy.deepcopy(record)
    illegal_move = {"source": 0, "target": 1}
    illegal["analysis"]["best_move"] = illegal_move
    illegal["search"]["best_move"] = illegal_move
    assert any("not legal in the root position" in f for f in _validate(state, entry, illegal))

    inconsistent = copy.deepcopy(record)
    alternative = next(
        row
        for row in inconsistent["search"]["root_visits"]
        if {"source": row["source"], "target": row["target"]}
        != inconsistent["analysis"]["best_move"]
    )
    inconsistent["analysis"]["best_move"] = {
        "source": alternative["source"],
        "target": alternative["target"],
    }
    assert any(
        "disagrees with the search best_move" in f for f in _validate(state, entry, inconsistent)
    )

    absent = copy.deepcopy(record)
    absent["search"]["root_visits"] = [
        row
        for row in absent["search"]["root_visits"]
        if {"source": row["source"], "target": row["target"]} != absent["analysis"]["best_move"]
    ]
    failures = _validate(state, entry, absent)
    assert any("absent from the root visit distribution" in f for f in failures)


def test_altered_position_identity_or_history_is_rejected():
    state = new_game("brandubh")
    entry, record = _result(state)
    altered = copy.deepcopy(record)
    altered["search"]["position_identity"] = "0" * 64
    assert any("recomputed identity" in f for f in _validate(state, entry, altered))
    altered = copy.deepcopy(record)
    altered["path"] = "1" * 64
    assert any("record position association" in f for f in _validate(state, entry, altered))
    # A different repetition history changes the full-position identity.
    repeated = State("brandubh", state.board, state.turn, history=("a" * 8, "a" * 8))
    entry_repeated, record_repeated = _result(repeated, name="case")
    assert record_repeated["search"]["position_identity"] != record["search"]["position_identity"]
    cross = copy.deepcopy(record)
    cross["path"] = record_repeated["search"]["position_identity"]
    assert any("record position association" in f for f in _validate(state, entry, cross))


def test_mismatched_model_identity_is_rejected():
    state = new_game("brandubh")
    entry, record = _result(state)
    broken = copy.deepcopy(record)
    broken["search"]["model"]["sha256"] = "b" * 64
    failures = _validate(state, entry, broken)
    assert any("recomputed identity" in f for f in failures)


# ------------------------------------------------------------------ workloads


def _fixture_workload() -> dict:
    return mcts_workload.build_fixtures_workload(["brandubh"], ["opening"], 1)


def test_fixture_workloads_reconstruct_names_and_states():
    workload = _fixture_workload()
    validated = mcts_workload.validate_workload(workload)
    assert validated["expected_names"] == [entry["name"] for entry in workload["positions"]]
    substituted = copy.deepcopy(workload)
    substituted["positions"][0]["state"]["board"][0] = "A"
    substituted["positions_sha256"] = mcts_workload.positions_sha256(substituted["positions"])
    with pytest.raises(ValueError, match="canonical fixture states"):
        mcts_workload.validate_workload(substituted)


def test_custom_workloads_support_new_identifiers_and_history():
    state = new_game("brandubh")
    repeated = State("brandubh", state.board, state.turn, history=("a" * 8, "a" * 8))
    raw = [
        {"name": "owner-midgame-1", "kind": "midgame", "state": state.to_dict()},
        {"name": "owner-repetition-1", "kind": "repetition", "state": repeated.to_dict()},
    ]
    workload = mcts_workload.build_custom_workload(raw, "c" * 64)
    validated = mcts_workload.validate_workload(workload)
    assert validated["expected_names"] == ["owner-midgame-1", "owner-repetition-1"]
    assert validated["source"]["kind"] == "custom"
    # The independent file check catches a substituted state or history.
    with pytest.raises(ValueError, match="supplied position file"):
        mcts_workload.validate_workload(workload, file_positions=raw[:1])
    substituted = copy.deepcopy(raw)
    substituted[0]["state"]["board"][0] = "A"
    with pytest.raises(ValueError, match="supplied position file"):
        mcts_workload.validate_workload(workload, file_positions=substituted)


@pytest.mark.parametrize(
    "mutation,fragment",
    [
        (lambda w: w.update(schema=99), "unsupported workload schema"),
        (lambda w: w.update(source={"kind": "unknown"}), "source kind"),
        (lambda w: w.update(positions=[]), "non-empty"),
        (lambda w: w.update(expected_names=[]), "expected_names"),
        (lambda w: w.update(expected_names=["other"]), "expected_names"),
        (lambda w: w.update(positions_sha256="0" * 64), "position digest"),
        (lambda w: w.update(selection=None), "fixture selection"),
        (
            lambda w: w["selection"].update(rulesets=["not-a-ruleset"]),
            "invalid rulesets",
        ),
        (lambda w: w["selection"].update(limit=0), "invalid limit"),
    ],
)
def test_malformed_workload_metadata_is_rejected(mutation, fragment):
    workload = _fixture_workload()
    mutation(workload)
    with pytest.raises(ValueError, match=fragment):
        mcts_workload.validate_workload(workload)


def test_duplicate_custom_names_and_empty_files_are_rejected(tmp_path):
    state = new_game("brandubh").to_dict()
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text(
        json.dumps([{"name": "same", "state": state}, {"name": "same", "state": state}])
    )
    with pytest.raises(ValueError, match="duplicate position"):
        mcts_workload.load_custom_positions(duplicate)
    empty = tmp_path / "empty.json"
    empty.write_text("[]")
    with pytest.raises(ValueError, match="non-empty"):
        mcts_workload.load_custom_positions(empty)


def _native_report(tmp_path: Path, *args: str) -> dict:
    output = tmp_path / "native.json"
    result = subprocess.run(
        [
            sys.executable,
            str(SPIKE / "native_mcts_parity.py"),
            "--level",
            "a",
            "--iterations",
            "8",
            "--output",
            str(output),
            *args,
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(output.read_text())


def test_custom_position_parity_requires_the_same_workload(tmp_path):
    working = tmp_path / "custom.json"
    positions = [
        {"name": "owner-a", "kind": "tactical", "state": new_game("brandubh").to_dict()},
        {"name": "owner-b", "kind": "endgame", "state": new_game("tablut").to_dict()},
    ]
    working.write_text(json.dumps(positions))
    native = _native_report(tmp_path, "--positions", str(working), "--rulesets", "brandubh,tablut")
    loaded, digest = mcts_workload.load_custom_positions(working)
    browser = copy.deepcopy(native)
    assert native["workload"]["source"] == {
        "kind": "custom",
        "version": 1,
        "file_sha256": digest,
    }
    comparison = mcts_compare.compare(
        native,
        browser,
        require_best_move=False,
        custom_positions=loaded,
        custom_file_digest=digest,
    )
    assert comparison["acceptance"] is True, comparison["failures"]
    assert comparison["workload"]["custom_file_verified"] is True

    # A familiar identifier with a different board must not be accepted.
    altered = copy.deepcopy(browser)
    altered["workload"]["positions"][0]["state"]["board"][0] = "A"
    comparison = mcts_compare.compare(
        native, altered, require_best_move=False, custom_positions=loaded
    )
    assert comparison["acceptance"] is False
    assert any("workload position digest" in f for f in comparison["failures"])

    # A changed repetition history is a different position.
    altered = copy.deepcopy(browser)
    altered["workload"]["positions"][1]["state"]["history"] = ["a" * 8]
    comparison = mcts_compare.compare(
        native, altered, require_best_move=False, custom_positions=loaded
    )
    assert comparison["acceptance"] is False


def test_custom_workload_missing_case_is_detected_with_the_source_file(tmp_path):
    source = tmp_path / "custom.json"
    source.write_text(
        json.dumps(
            [
                {"name": "one", "state": new_game("brandubh").to_dict()},
                {"name": "two", "state": new_game("brandubh").to_dict()},
            ]
        )
    )
    native = _native_report(tmp_path, "--positions", str(source), "--rulesets", "brandubh")
    loaded, file_digest = mcts_workload.load_custom_positions(source)
    truncated = copy.deepcopy(native)
    for report in (native, truncated):
        report["workload"]["positions"] = report["workload"]["positions"][:1]
        report["workload"]["expected_names"] = report["workload"]["expected_names"][:1]
        report["workload"]["positions_sha256"] = mcts_workload.positions_sha256(
            report["workload"]["positions"]
        )
        report["positions"] = report["positions"][:1]
        report["results"] = report["results"][:1]
    comparison = mcts_compare.compare(
        native,
        truncated,
        require_best_move=False,
        custom_positions=loaded,
        custom_file_digest=file_digest,
    )
    assert comparison["acceptance"] is False
    assert any("supplied position file" in f for f in comparison["failures"])


def test_comparator_cli_returns_structured_failure_for_malformed_reports(tmp_path):
    native = _native_report(tmp_path, "--limit", "1")
    broken = copy.deepcopy(native)
    broken["workload"]["selection"]["rulesets"] = ["bogus"]
    broken_path = tmp_path / "broken.json"
    broken_path.write_text(json.dumps(broken))
    output = tmp_path / "comparison.json"
    result = subprocess.run(
        [
            sys.executable,
            str(SPIKE / "mcts_compare.py"),
            str(tmp_path / "native.json"),
            str(broken_path),
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode != 0
    assert "Traceback" not in result.stderr
    report = json.loads(output.read_text())
    assert report["acceptance"] is False
    assert any("invalid workload definition" in f for f in report["failures"])


def test_embedded_and_file_backed_custom_sources_are_distinct():
    state = new_game("brandubh").to_dict()
    embedded = mcts_workload.build_custom_workload([{"name": "e", "state": state}])
    assert embedded["source"] == {"kind": "embedded", "version": 1}
    assert mcts_workload.validate_workload(embedded)["source"]["kind"] == "embedded"
    with pytest.raises(ValueError, match="cannot be verified"):
        mcts_workload.validate_workload(embedded, file_positions=[{"name": "e", "state": state}])

    backup = mcts_workload.build_custom_workload([{"name": "e", "state": state}], "d" * 64)
    assert backup["source"]["kind"] == "custom"
    assert backup["source"]["file_sha256"] == "d" * 64
    with pytest.raises(ValueError, match="does not match the supplied file"):
        mcts_workload.validate_workload(backup, file_sha256="c" * 64)
    with pytest.raises(ValueError, match="file digest is malformed"):
        mcts_workload.build_custom_workload([{"name": "e", "state": state}], "not-a-digest")


def test_custom_source_version_and_digest_are_validated():
    state = new_game("brandubh").to_dict()
    workload = mcts_workload.build_custom_workload([{"name": "e", "state": state}], "d" * 64)
    broken = copy.deepcopy(workload)
    broken["source"]["version"] = 99
    with pytest.raises(ValueError, match="source version"):
        mcts_workload.validate_workload(broken)

    missing = copy.deepcopy(workload)
    missing["source"].pop("file_sha256")
    with pytest.raises(ValueError, match="file digest is malformed"):
        mcts_workload.validate_workload(missing)

    malformed = copy.deepcopy(workload)
    malformed["source"]["file_sha256"] = "Z" * 64
    with pytest.raises(ValueError, match="file digest is malformed"):
        mcts_workload.validate_workload(malformed)


def test_custom_workload_property_order_does_not_change_the_contract(tmp_path):
    state = new_game("brandubh")
    ordered = {
        "ruleset": state.ruleset,
        "board": list(state.board),
        "turn": state.turn,
        "ply": state.ply,
        "winner": state.winner,
        "reason": state.reason,
        "history": list(state.history),
    }
    reordered = {key: ordered[key] for key in reversed(list(ordered))}
    first = tmp_path / "ordered.json"
    second = tmp_path / "reordered.json"
    first.write_text(json.dumps([{"name": "same", "kind": "opening", "state": ordered}]))
    second.write_text(json.dumps([{"name": "same", "kind": "opening", "state": reordered}]))
    a, digest_a = mcts_workload.load_custom_positions(first)
    b, digest_b = mcts_workload.load_custom_positions(second)
    assert a == b
    assert digest_a != digest_b  # exact source-file digests differ by bytes
    assert mcts_workload.positions_sha256(a) == mcts_workload.positions_sha256(b)
    workload = mcts_workload.build_custom_workload(a, digest_a)
    validated = mcts_workload.validate_workload(workload, file_positions=b, file_sha256=digest_a)
    assert validated["positions"] == a


def _run_cli(tmp_path: Path, native: Path, browser: Path, *extra: str):
    output = tmp_path / "comparison.json"
    result = subprocess.run(
        [
            sys.executable,
            str(SPIKE / "mcts_compare.py"),
            str(native),
            str(browser),
            "--output",
            str(output),
            *extra,
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    report = json.loads(output.read_text()) if output.exists() else None
    return result, report


def test_parity_cli_reports_malformed_inputs_without_tracebacks(tmp_path):
    native = _native_report(tmp_path, "--limit", "1")
    valid = tmp_path / "native.json"
    broken_json = tmp_path / "broken.json"
    broken_json.write_text("{not json")
    result, report = _run_cli(tmp_path, valid, broken_json)
    assert result.returncode == 2
    assert "Traceback" not in result.stderr
    assert report["error"]["kind"] == "workload"
    assert report["acceptance"] is False
    assert report["workload_completion"]["complete"] is False

    missing = tmp_path / "missing.json"
    result, report = _run_cli(tmp_path, valid, missing)
    assert result.returncode == 2
    assert report["error"]["kind"] == "input"

    array = tmp_path / "array.json"
    array.write_text("[]")
    result, report = _run_cli(tmp_path, valid, array)
    assert result.returncode == 2
    assert report["error"]["kind"] == "workload"

    malformed = copy.deepcopy(native)
    malformed["workload"]["positions"][0]["state"]["board"] = "not-a-board"
    bad_positions = tmp_path / "bad-positions.json"
    bad_positions.write_text(json.dumps(malformed))
    result, report = _run_cli(tmp_path, valid, bad_positions)
    assert result.returncode == 1
    assert any("invalid workload definition" in f for f in report["failures"])
    assert report["acceptance"] is False
    assert report["error"] is None

    summary = json.loads(result.stdout)
    assert summary["ok"] is False
    assert summary["workload_complete"] is False


def test_parity_cli_reports_unexpected_comparison_exceptions(tmp_path, monkeypatch, capsys):
    _native_report(tmp_path, "--limit", "1")
    native_path = tmp_path / "native.json"

    def explode(*args, **kwargs):
        raise RuntimeError("simulated comparison crash")

    monkeypatch.setattr(mcts_compare, "compare", explode)
    output = tmp_path / "comparison.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "mcts_compare.py",
            str(native_path),
            str(native_path),
            "--output",
            str(output),
        ],
    )
    with pytest.raises(SystemExit) as exit_info:
        mcts_compare.main()
    assert exit_info.value.code == 2
    report = json.loads(output.read_text())
    assert report["error"]["kind"] == "execution"
    assert report["acceptance"] is False
    summary = json.loads(capsys.readouterr().out)
    assert summary["ok"] is False
    assert summary["error"]["kind"] == "execution"
    assert summary["workload_complete"] is False


def test_parity_cli_verifies_the_custom_file_digest(tmp_path):
    source = tmp_path / "custom.json"
    source.write_text(json.dumps([{"name": "owner", "state": new_game("brandubh").to_dict()}]))
    native_path = tmp_path / "native.json"
    result = subprocess.run(
        [
            sys.executable,
            str(SPIKE / "native_mcts_parity.py"),
            "--level",
            "a",
            "--iterations",
            "8",
            "--positions",
            str(source),
            "--rulesets",
            "brandubh",
            "--output",
            str(native_path),
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    good, report = _run_cli(tmp_path, native_path, native_path, "--positions-file", str(source))
    assert good.returncode == 0, good.stdout + good.stderr
    assert report["acceptance"] is True
    assert report["workload"]["custom_file_verified"] is True

    tampered = tmp_path / "tampered.json"
    tampered.write_text(json.dumps([{"name": "owner", "state": new_game("tablut").to_dict()}]))
    bad, report = _run_cli(tmp_path, native_path, native_path, "--positions-file", str(tampered))
    assert bad.returncode == 1
    assert report["acceptance"] is False
    assert any("file digest" in f for f in report["failures"])
