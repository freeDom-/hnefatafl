"""Bounded tests for the E1 lifecycle-sweep correction.

The sweep script is executed with stub commands, so validator exit statuses,
partial-result continuation, collection failure, interruption, cleanup and
report-path selection are covered without any sustained browser measurement.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "browser_analysis_lifecycle_sweep.sh"

STUB = """#!/usr/bin/env bash
name=$(basename "$1"); shift
log=${STUB_LOG:?}
case "$name" in
  sustained_workload.mjs)
    echo "workload $*" >> "$log"
    if [ "${STUB_WORKLOAD_HANG:-0}" = "1" ]; then sleep 30 >/dev/null 2>&1; fi
    exit "${STUB_WORKLOAD_EXIT:-0}" ;;
  wait_for_ready.py)
    echo "wait $*" >> "$log"
    case " $* " in
      *" --terminate-only "*) exit "${STUB_TERMINATE_EXIT:-0}" ;;
    esac
    exit "${STUB_READY_EXIT:-0}" ;;
  memory_profile_collect.py)
    echo "collect $*" >> "$log"
    exit "${STUB_COLLECT_EXIT:-0}" ;;
  finalize_workload_evidence.py)
    echo "finalize $*" >> "$log"
    exit "${STUB_FINALIZE_EXIT:-0}" ;;
  memory_validation.py)
    echo "validate $*" >> "$log"
    counter=${STUB_COUNTER:?}
    index=0
    if [ -f "$counter" ]; then index=$(cat "$counter"); fi
    echo $((index + 1)) > "$counter"
    statuses=${STUB_VALIDATOR_STATUSES:-0}
    IFS=',' read -r -a values <<< "$statuses"
    value=${values[$index]:-${values[-1]}}
    exit "$value" ;;
  *) exit 0 ;;
esac
"""


@pytest.fixture
def sweep(tmp_path):
    stub = tmp_path / "stub"
    stub.write_text(STUB)
    stub.chmod(0o755)
    log = tmp_path / "calls.log"
    log.write_text("")
    counter = tmp_path / "validator.count"
    stage = tmp_path / "stage"
    stage.mkdir()

    def run(cases="cancel-restart cancel-restart|model-reload model-reload", **env):
        environment = {
            **os.environ,
            "PYTHON": str(stub),
            "NODE": str(stub),
            "SPIKE_DIR": str(tmp_path / "spike"),
            "STAGE_DIR": str(stage),
            "WORK_DIR": str(tmp_path),
            "MODEL_SHA256": "a" * 64,
            "SESSION_PREFIX": "unit",
            "CASES": cases,
            "STUB_LOG": str(log),
            "STUB_COUNTER": str(counter),
            **{key: str(value) for key, value in env.items()},
        }
        return subprocess.run(
            ["bash", str(SCRIPT)],
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
        )

    return run, log, counter


def _calls(log: Path) -> list[str]:
    return log.read_text().splitlines()


def test_accepted_evidence_runs_every_case_and_validates_its_own_report(sweep):
    run, log, _ = sweep
    result = run()
    assert result.returncode == 0, result.stderr
    calls = _calls(log)
    validates = [line for line in calls if line.startswith("validate ")]
    assert len(validates) == 2
    for line in validates:
        report = Path(line.split(" ", 1)[1]).name
        assert report.startswith("unit-")
        assert report.endswith("-memory.json")
        assert "memory-10m.json" not in report
        assert "memory-collected" not in report
    # Both workloads were reaped before the next case, and cleanup uses the
    # ownership file rather than a PID-only termination.
    assert sum(line.startswith("workload ") for line in calls) == 2
    assert not any("/tmp/memory-10m" in line for line in calls)


def test_partial_evidence_continues_the_sweep_but_is_not_accepted(sweep):
    run, _log, _counter = sweep
    result = run(STUB_VALIDATOR_STATUSES="2,0")
    assert result.returncode == 2
    assert "PARTIAL" in result.stderr
    assert "INVALID" not in result.stderr
    assert "completed with partial evidence" in result.stderr


def test_invalid_evidence_stops_the_sweep(sweep):
    run, log, _counter = sweep
    result = run(STUB_VALIDATOR_STATUSES="1")
    assert result.returncode == 1
    assert "INVALID" in result.stderr
    calls = _calls(log)
    assert sum(line.startswith("validate ") for line in calls) == 1
    assert sum(line.startswith("workload ") for line in calls) == 1


def test_unexpected_validator_status_is_invalid(sweep):
    run, _log, _counter = sweep
    result = run(STUB_VALIDATOR_STATUSES="7")
    assert result.returncode == 1


def test_readiness_failure_cleans_up_with_the_ownership_file(sweep):
    run, log, _counter = sweep
    result = run(STUB_READY_EXIT=3)
    assert result.returncode == 3
    calls = _calls(log)
    terminate = [line for line in calls if line.startswith("wait ") and "--terminate-only" in line]
    assert terminate
    assert "--ownership-file" in terminate[0]
    assert "--pid" not in terminate[0]
    assert sum(line.startswith("workload ") for line in calls) == 1


def test_cleanup_failure_is_reported_without_changing_the_original_status(sweep):
    run, _log, _counter = sweep
    result = run(STUB_READY_EXIT=3, STUB_TERMINATE_EXIT=7)
    assert result.returncode == 3
    assert "cleanup warning" in result.stderr
    assert "ownership-safe termination failed" in result.stderr


def test_collection_failure_preserves_status_and_cleans_up(sweep):
    run, log, _counter = sweep
    result = run(STUB_COLLECT_EXIT=5)
    assert result.returncode == 4
    assert "collection failed" in result.stderr
    calls = _calls(log)
    assert any("--terminate-only" in line for line in calls)


def test_workload_failure_is_distinct_from_partial_validator_evidence(sweep):
    run, log, _counter = sweep
    result = run(STUB_WORKLOAD_EXIT=9)
    # A workload failure must never be reported as validator exit code 2.
    assert result.returncode == 6
    assert "PARTIAL" not in result.stderr
    calls = _calls(log)
    assert sum(line.startswith("validate ") for line in calls) == 0
    assert any(line.startswith("finalize ") for line in calls)


def test_interruption_cleans_up_and_preserves_the_signal_status(sweep):
    run, log, _counter = sweep
    stub = log.parent / "stub"
    stage = log.parent / "stage"
    stdout_path = log.parent / "stdout.txt"
    stderr_path = log.parent / "stderr.txt"
    stdout_file = stdout_path.open("w")
    stderr_file = stderr_path.open("w")
    process = subprocess.Popen(
        ["bash", str(SCRIPT)],
        env={
            "PATH": os.environ["PATH"],
            "PYTHON": str(stub),
            "NODE": str(stub),
            "SPIKE_DIR": str(log.parent / "spike"),
            "STAGE_DIR": str(stage),
            "WORK_DIR": str(log.parent),
            "MODEL_SHA256": "a" * 64,
            "SESSION_PREFIX": "unit",
            "CASES": "cancel-restart cancel-restart",
            "STUB_LOG": str(log),
            "STUB_COUNTER": str(log.parent / "validator.count"),
            "STUB_WORKLOAD_HANG": "1",
        },
        stdout=stdout_file,
        stderr=stderr_file,
        text=True,
    )
    try:
        deadline = time.time() + 10
        while time.time() < deadline:
            if any(line.startswith("wait ") for line in _calls(log)):
                break
            time.sleep(0.05)
        process.send_signal(signal.SIGTERM)
        process.wait(timeout=10)
    finally:
        if process.poll() is None:
            process.kill()
        stdout_file.close()
        stderr_file.close()
    stdout = stdout_path.read_text()
    stderr = stderr_path.read_text()
    assert process.returncode != 0
    # SIGTERM is preserved by the cleanup trap rather than replaced by the
    # cleanup command's own status.
    assert process.returncode in (143, -signal.SIGTERM)
    assert "wait " in log.read_text()
    assert "--terminate-only" in log.read_text()
    assert "--ownership-file" in log.read_text()
    assert "Traceback" not in stderr and "Traceback" not in stdout
