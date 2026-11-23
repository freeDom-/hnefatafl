"""Role-balanced paired-opening tournament harness and validator."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

SPIKE = Path(__file__).resolve().parents[1] / "spikes/browser_offline"
for path in (str(SPIKE), str(SPIKE.parents[1])):
    if path not in sys.path:
        sys.path.insert(0, path)

import tournament  # noqa: E402
from fixtures import jarl_positions  # noqa: E402
from hnefatafl.engine import legal_moves  # noqa: E402


def first_legal(state, settings, seed):
    return legal_moves(state)[0]


def last_legal(state, settings, seed):
    return legal_moves(state)[-1]


def _openings():
    return [
        entry
        for entry in jarl_positions()
        if entry["ruleset"] == "brandubh" and entry["kind"] in ("opening", "tactical")
    ]


def _report():
    return tournament.run_paired_openings(
        first_legal,
        last_legal,
        _openings(),
        labels=("first", "last"),
        ruleset="brandubh",
        simulations=8,
        time_limit=1,
        seed=11,
        max_plies=24,
    )


def test_paired_openings_validate_and_cover_roles():
    report = _report()
    assert report["schema"] == tournament.SCHEMA
    validated = tournament.validate_report(report)
    assert validated["ok"] is True, validated["failures"]
    assert len(report["games"]) == 2 * len(_openings())
    assert report["summary"]["games"] == len(report["games"])
    assert report["summary"]["pairs"] == len(_openings())
    assert all(
        {game["agent_a_side"], game["agent_b_side"]} == {"attackers", "defenders"}
        for game in report["games"]
    )
    assert report["authoritative"] is False


@pytest.mark.parametrize(
    "mutation",
    [
        lambda report: report["games"].pop(),
        lambda report: report["games"].append(copy.deepcopy(report["games"][0])),
        lambda report: report["games"][0]["moves"].append({"source": 0, "target": 0}),
        lambda report: report["games"][0].update(agent_a="other"),
        lambda report: report["games"][0].update(
            agent_a_side="attackers", agent_b_side="attackers"
        ),
        lambda report: report["games"][0].update(winner="invalid"),
        lambda report: report["summary"].update(games=0),
        lambda report: report["summary"].update(completed=0),
    ],
)
def test_validator_rejects_negative_tournament_reports(mutation):
    report = _report()
    mutation(report)
    validated = tournament.validate_report(report)
    assert validated["ok"] is False
    assert validated["failures"]


def test_validator_cli_returns_nonzero(tmp_path):
    report = _report()
    report["games"].pop()
    source = tmp_path / "report.json"
    source.write_text(json.dumps(report))
    result = subprocess.run(
        [sys.executable, str(SPIKE / "tournament.py"), str(source)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode != 0
    assert "game identifiers" in result.stdout
