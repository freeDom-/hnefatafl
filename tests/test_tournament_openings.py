"""Reproducible tournament opening corpora."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SPIKE = Path(__file__).resolve().parents[1] / "spikes/browser_offline"
if str(SPIKE) not in sys.path:
    sys.path.insert(0, str(SPIKE))

import tournament  # noqa: E402
import tournament_openings as openings  # noqa: E402
from hnefatafl.engine import State, legal_moves  # noqa: E402


def test_generation_is_deterministic_and_unique_for_all_rulesets():
    for ruleset in ("brandubh", "tablut", "copenhagen"):
        first = openings.generate_corpus(ruleset, 6, seed=1234, max_plies=40)
        second = openings.generate_corpus(ruleset, 6, seed=1234, max_plies=40)
        assert first == second
        assert first["corpus_sha256"] == second["corpus_sha256"]
        assert len(first["openings"]) == 6
        names = [entry["name"] for entry in first["openings"]]
        assert len(set(names)) == len(names)
        keys = set()
        for entry in first["openings"]:
            state = State.from_dict(entry["state"])
            assert state.ruleset == ruleset
            assert state.winner is None
            assert legal_moves(state)
            assert entry["starting_ply"] == state.ply
            key = repr(
                __import__(
                    "hnefatafl.symmetry", fromlist=["canonical_state_key"]
                ).canonical_state_key(state)[0]
            )
            assert key not in keys
            keys.add(key)
        validated = openings.validate_corpus(first, ruleset, minimum_pairs=6)
        assert validated["count"] == 6
        assert validated["corpus_sha256"] == first["corpus_sha256"]


def test_corpus_validation_rejects_duplicates_and_bad_counts():
    corpus = openings.generate_corpus("brandubh", 4, seed=7, max_plies=30)
    duplicated = json.loads(json.dumps(corpus))
    duplicated["openings"][1]["state"] = duplicated["openings"][0]["state"]
    duplicated["openings"][1]["starting_ply"] = duplicated["openings"][0]["starting_ply"]
    try:
        openings.validate_corpus(duplicated, "brandubh", minimum_pairs=4)
    except ValueError as error:
        assert "duplicate" in str(error) or "digest" in str(error)
    else:  # pragma: no cover - defensive
        raise AssertionError("duplicate corpus accepted")

    try:
        openings.validate_corpus(corpus, "brandubh", minimum_pairs=5)
    except ValueError as error:
        assert "at least 5" in str(error)
    else:  # pragma: no cover - defensive
        raise AssertionError("insufficient corpus accepted")


def test_insufficient_openings_are_rejected_before_playing_strength_launch(tmp_path):
    corpus = openings.generate_corpus("brandubh", 3, seed=9, max_plies=24)
    path = tmp_path / "corpus.json"
    path.write_text(json.dumps(corpus))
    agent = SPIKE / "native_tournament_agent.py"
    config = tmp_path / "scripted.json"
    config.write_text(
        json.dumps(
            {
                "label": "scripted",
                "kind": "scripted",
                "runtime": "native-cpython",
                "provider": "cpu",
                "algorithm": "scripted",
                "model": None,
            }
        )
    )
    result = subprocess.run(
        [
            sys.executable,
            str(SPIKE / "tournament.py"),
            "--run",
            "--ruleset",
            "brandubh",
            "--openings-file",
            str(path),
            "--simulations",
            "1",
            "--time-limit",
            "0.2",
            "--max-plies",
            "4",
            "--agent-timeout",
            "5",
            "--evidence-class",
            "playing-strength",
            "--agent-a-command",
            f"{sys.executable} {agent} --engine scripted",
            "--agent-b-command",
            f"{sys.executable} {agent} --engine scripted",
            "--agent-a-config",
            str(config),
            "--agent-b-config",
            str(config),
            "--output",
            str(tmp_path / "report.json"),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert any("at least 10" in failure for failure in payload["failures"])
    assert not (tmp_path / "report.json").exists()


def test_corpus_source_is_recorded_in_manifest_and_report(tmp_path):
    corpus = openings.generate_corpus("brandubh", 2, seed=11, max_plies=20)
    path = tmp_path / "corpus.json"
    path.write_text(json.dumps(corpus))
    entries, source = tournament._load_openings(path, "brandubh", None)
    assert len(entries) == 2
    assert source["kind"] == "corpus"
    assert source["corpus_sha256"] == corpus["corpus_sha256"]
    assert source["generator"]["seed"] == 11
