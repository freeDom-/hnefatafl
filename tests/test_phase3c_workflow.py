"""Collect-to-validate structural integration for the Phase 3C-A platform.

These exercise the public CLIs an owner runs, not only imported helpers.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SPIKE = Path(__file__).resolve().parents[1] / "spikes/browser_offline"


def _config(tmp_path: Path, label: str) -> Path:
    path = tmp_path / f"{label}.json"
    path.write_text(
        json.dumps(
            {
                "label": label,
                "kind": "scripted",
                "runtime": "in-process",
                "provider": "in-process",
                "model": None,
            }
        )
    )
    return path


def test_tournament_run_cli_collects_and_validates_a_structural_report(tmp_path):
    agent = SPIKE / "native_tournament_agent.py"
    output = tmp_path / "report.json"
    result = subprocess.run(
        [
            sys.executable,
            str(SPIKE / "tournament.py"),
            "--run",
            "--ruleset",
            "brandubh",
            "--limit",
            "1",
            "--simulations",
            "4",
            "--time-limit",
            "0.2",
            "--seed",
            "3",
            "--max-plies",
            "6",
            "--agent-timeout",
            "5",
            "--agent-a-command",
            f"{sys.executable} {agent} --engine scripted",
            "--agent-b-command",
            f"{sys.executable} {agent} --engine scripted",
            "--agent-a-config",
            str(_config(tmp_path, "scripted-a")),
            "--agent-b-config",
            str(_config(tmp_path, "scripted-b")),
            "--output",
            str(output),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    summary = json.loads(result.stdout)
    assert summary["ok"] is True
    assert summary["structural_only"] is True
    assert summary["playing_strength_acceptance"] is False
    report = json.loads(output.read_text())
    assert report["schema"] == tournament_schema()
    assert report["playing_strength"]["status"] == "blocked"
    assert report["summary"]["games"] == 2

    validated = subprocess.run(
        [
            sys.executable,
            str(SPIKE / "tournament.py"),
            str(output),
            "--output",
            str(tmp_path / "validation.json"),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert validated.returncode == 0, validated.stdout + validated.stderr
    assert json.loads(validated.stdout)["playing_strength_acceptance"] is False


def test_tournament_run_cli_reports_malformed_openings_files(tmp_path):
    openings = tmp_path / "openings.json"
    openings.write_text("{not json")
    result = subprocess.run(
        [
            sys.executable,
            str(SPIKE / "tournament.py"),
            "--run",
            "--openings-file",
            str(openings),
            "--agent-a-command",
            f"{sys.executable} {SPIKE / 'native_tournament_agent.py'}",
            "--agent-b-command",
            f"{sys.executable} {SPIKE / 'native_tournament_agent.py'}",
            "--agent-a-config",
            str(_config(tmp_path, "scripted-a")),
            "--agent-b-config",
            str(_config(tmp_path, "scripted-b")),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert json.loads(result.stdout)["ok"] is False


def tournament_schema():
    import sys

    if str(SPIKE) not in sys.path:
        sys.path.insert(0, str(SPIKE))
    import tournament

    return tournament.SCHEMA


def test_memory_profile_collection_to_validation_workflow(tmp_path):
    import subprocess as sp

    progress = tmp_path / "progress.txt"
    progress.write_text("0")
    workload = sp.Popen(
        [
            sys.executable,
            "-c",
            (
                "import pathlib,time\n"
                f"p=pathlib.Path({str(progress)!r})\n"
                "for i in range(1,200):\n"
                "    p.write_text(str(i))\n"
                "    time.sleep(0.02)\n"
            ),
        ]
    )
    output = tmp_path / "profile.json"
    try:
        result = sp.run(
            [
                sys.executable,
                str(SPIKE / "memory_profile_collect.py"),
                "--pid",
                str(workload.pid),
                "--profile",
                "worker-replacement",
                "--duration",
                "1.2",
                "--interval",
                "0.1",
                "--warmup-samples",
                "1",
                "--progress-file",
                str(progress),
                "--events-json",
                '{"worker_replacements": 1}',
                "--output",
                str(output),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
    finally:
        workload.terminate()
        workload.wait(timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(output.read_text())
    assert len(report["samples"]) >= 10
    assert all(sample["process_rss_bytes"] for sample in report["samples"])
    assert report["samples"][-1]["completed_requests"] > report["samples"][0]["completed_requests"]
    assert all(sample["js_heap_used_bytes"] is None for sample in report["samples"])
    assert "js_heap_used_bytes" in report["unavailable_metrics"]

    import sys as _sys

    if str(SPIKE) not in _sys.path:
        _sys.path.insert(0, str(SPIKE))
    import memory_validation as mv

    validation = mv.validate_report(report)
    assert validation["structural_ok"] is True, validation["failures"]
    assert validation["acceptance_ok"] is False
    assert validation["status"] == "partial"
