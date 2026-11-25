"""Model-transition authorization, timeline and bounded-history evidence."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SPIKE = Path(__file__).resolve().parents[1] / "spikes/browser_offline"
if str(SPIKE) not in sys.path:
    sys.path.insert(0, str(SPIKE))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import memory_profile_collect as mpc  # noqa: E402
import memory_validation as mv  # noqa: E402
from test_memory_validation import _schema3_report  # noqa: E402

A = "a" * 64
B = "b" * 64
C = "c" * 64


def _stream(tmp_path, approved=(A, B), allow=True, cache=True):
    return mpc.TelemetryStream(
        tmp_path / "telemetry.jsonl",
        stale_after_ms=10000,
        expected={"session_id": "s", "model_sha256": A, "workload": "w"},
        allow_model_transitions=allow,
        approved_models=set(approved),
        require_cache_invalidation=cache,
    )


def _record(ts, model=A, transition=None, **extra):
    record = {
        "timestamp_ms": ts,
        "session_id": "s",
        "model_sha256": model,
        "workload": "w",
        "workers": 2,
        "completed_requests": int(ts),
        "successful_searches": int(ts),
        "neural_searches": int(ts),
        "completed_simulations": int(ts),
        "neural_inferences": int(ts),
        "provider_placements": ["wasm/wasm"],
        **extra,
    }
    if transition is not None:
        record["model_transition"] = transition
    return record


def _transition(sequence, source, destination, ts, **extra):
    value = {
        "sequence": sequence,
        "from": source,
        "to": destination,
        "timestamp_ms": ts,
        "recovered": True,
        "cache_invalidated": True,
    }
    value.update(extra)
    return value


def test_valid_a_b_a_transition_and_timeline(tmp_path):
    stream = _stream(tmp_path)
    stream._record(_record(100))
    stream._record(_record(200, B, _transition(1, A, B, 200)))
    stream._record(_record(300, A, _transition(2, B, A, 300)))
    assert stream.identity["model_sha256"] == A
    assert [entry["sequence"] for entry in stream.model_transitions] == [1, 2]
    assert stream.transition_history()["final_model"] == A


def test_unexpected_third_model_is_rejected_without_mutating_state(tmp_path):
    stream = _stream(tmp_path, approved=(A, B))
    stream._record(_record(100))
    stream._record(_record(200, B, _transition(1, A, B, 200)))
    stream._record(_record(300, C, _transition(2, B, C, 300)))
    assert stream.identity["model_sha256"] == B
    assert stream._transition_sequence == 1
    assert stream.identity_mismatch == 1
    # The rejected record must not make C trusted.
    stream._record(_record(400, C, _transition(2, B, C, 400)))
    assert stream.identity["model_sha256"] == B
    stream._record(_record(500))
    assert stream.identity["model_sha256"] == B


def test_malformed_transition_does_not_commit_identity(tmp_path):
    stream = _stream(tmp_path)
    stream._record(_record(100))
    malformed = _record(200, B, _transition(1, A, B, 200))
    malformed["events"] = {"worker_replacements": "bad"}
    stream._record(malformed)
    assert stream.identity["model_sha256"] == A
    assert stream._transition_sequence == 0
    # A later valid record for the original model is still accepted.
    stream._record(_record(300))
    assert stream.identity["model_sha256"] == A
    assert stream.malformed == 1


def test_incorrect_sequence_and_timestamp_are_rejected(tmp_path):
    stream = _stream(tmp_path)
    stream._record(_record(100))
    stream._record(_record(200, B, _transition(2, A, B, 200)))
    assert stream.identity["model_sha256"] == A
    stream._record(_record(300, B, _transition(1, A, B, 400)))
    assert stream.identity["model_sha256"] == A
    stream._record(_record(500, B, _transition(1, A, B, 600)))
    # Timestamp later than the record is rejected.
    assert stream.identity["model_sha256"] == A


def test_same_model_transition_and_failed_cache_invalidation_are_rejected(tmp_path):
    stream = _stream(tmp_path)
    stream._record(_record(100))
    stream._record(_record(200, A, _transition(1, A, A, 200)))
    assert stream.identity["model_sha256"] == A
    stream._record(_record(300, B, _transition(1, A, B, 300, cache_invalidated=False)))
    assert stream.identity["model_sha256"] == A
    assert stream.identity_mismatch == 2


def test_ordinary_profile_cannot_change_model(tmp_path):
    stream = _stream(tmp_path, allow=False)
    stream._record(_record(100))
    stream._record(_record(200, B, _transition(1, A, B, 200)))
    assert stream.identity["model_sha256"] == A
    assert stream.identity_mismatch == 1


def test_bounded_history_retains_continuity_beyond_1024(tmp_path):
    stream = _stream(tmp_path)
    stream._record(_record(1))
    for index in range(1, 1101):
        source, destination = (A, B) if index % 2 else (B, A)
        stream._record(
            _record(index + 1, destination, _transition(index, source, destination, index + 1))
        )
    history = stream.transition_history()
    assert history["total"] == 1100
    assert history["retained"] == 1024
    assert history["truncated"] is True
    assert history["first_retained_sequence"] == 77
    assert history["last_sequence"] == 1100
    assert history["final_model"] == stream.identity["model_sha256"]
    assert len(stream.model_transitions) == 1024


def _report_with_transitions():
    report = _schema3_report(
        profile="model-replacement",
        events={"model_replacements": 1},
        approved_model_sha256=[A, B],
    )
    transition = _transition(1, A, B, 60000)
    history = {
        "limit": 1024,
        "total": 1,
        "retained": 1,
        "truncated": False,
        "first_retained_sequence": 1,
        "first_retained_from": A,
        "last_sequence": 1,
        "final_model": B,
        "last_timestamp_ms": 60000,
        "chain_digest_sha256": "0" * 64,
        "retained_chain_digest_sha256": "",
    }
    import hashlib

    history["retained_chain_digest_sha256"] = hashlib.sha256(
        json.dumps(transition, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    workload = report["workload_telemetry"]
    workload["model_transitions"] = [transition]
    workload["model_transition_history"] = history
    workload["model_replacements"] = 1
    workload["model_sha256"] = B
    workload["acceptance_window"]["model_transitions"] = [transition]
    workload["acceptance_window"]["counters"]["model_replacements"] = 1
    final = workload["final_session"]
    final["profile"] = report["profile"]
    final["model_sha256"] = B
    final["recorded_at_ms"] = 180000
    final["counters"]["model_replacements"] = 1
    for sample in report["samples"]:
        if sample["timestamp_ms"] >= 60000:
            sample["telemetry"]["model_sha256"] = B
            sample["model_replacements"] = 1
    return report


def test_valid_model_transition_report_is_structural():
    result = mv.validate_report(_report_with_transitions())
    assert result["structural_ok"] is True, result["failures"]


@pytest.mark.parametrize(
    "mutate,message",
    [
        (
            lambda report: report["workload_telemetry"]["model_transitions"][0].update(
                {"sequence": 2}
            ),
            "transition chain",
        ),
        (
            lambda report: report["workload_telemetry"]["model_transitions"][0].update(
                {"timestamp_ms": 190000}
            ),
            "timeline",
        ),
        (
            lambda report: report["workload_telemetry"]["model_transitions"][0].update({"to": C}),
            "transition chain",
        ),
        (
            lambda report: report["samples"][10]["telemetry"].update({"model_sha256": A}),
            "timeline",
        ),
    ],
)
def test_malformed_transition_timeline_is_rejected(mutate, message):
    report = _report_with_transitions()
    mutate(report)
    result = mv.validate_report(report)
    assert result["structural_ok"] is False
    assert any(message in failure for failure in result["failures"])


def test_truncated_transition_history_is_validated_without_requiring_sequence_one():
    report = _report_with_transitions()
    transitions = []
    # Retain only sequences 77..1100; the retained chain starts at the anchor
    # recorded by the collector rather than at sequence 1.
    for sequence in range(77, 1101):
        source, destination = (B, A) if sequence % 2 else (A, B)
        transitions.append(_transition(sequence, source, destination, sequence))
    history = report["workload_telemetry"]["model_transition_history"]
    history.update(
        {
            "total": 1100,
            "retained": len(transitions),
            "truncated": True,
            "first_retained_sequence": 77,
            "first_retained_from": transitions[0]["from"],
            "last_sequence": 1100,
            "final_model": transitions[-1]["to"],
            "last_timestamp_ms": 1100,
        }
    )
    history["retained_chain_digest_sha256"] = (
        __import__("hashlib")
        .sha256(
            b"".join(
                json.dumps(item, sort_keys=True, separators=(",", ":")).encode()
                for item in transitions
            )
        )
        .hexdigest()
    )
    workload = report["workload_telemetry"]
    workload["model_transitions"] = transitions
    workload["model_replacements"] = 1100
    workload["model_sha256"] = transitions[-1]["to"]
    workload["acceptance_window"]["counters"]["model_replacements"] = 1
    workload["final_session"]["model_sha256"] = transitions[-1]["to"]
    workload["final_session"]["counters"]["model_replacements"] = 1100
    result = mv.validate_report(report)
    assert result["structural_ok"] is True, result["failures"]


def test_model_replacement_requires_explicit_approved_model_set(tmp_path):
    import subprocess

    command = [
        sys.executable,
        str(SPIKE / "memory_profile_collect.py"),
        "--pid",
        "1",
        "--profile",
        "model-replacement",
        "--warmup-duration",
        "0",
        "--duration",
        "1",
        "--telemetry-session-id",
        "s",
        "--telemetry-model-sha256",
        A,
        "--telemetry-workload",
        "w",
        "--approved-model-sha256",
        A,
        "--output",
        str(tmp_path / "profile.json"),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert result.returncode == 2
    assert "at least two" in result.stderr

    command = [
        sys.executable,
        str(SPIKE / "memory_profile_collect.py"),
        "--pid",
        "1",
        "--profile",
        "worker-replacement",
        "--warmup-duration",
        "0",
        "--duration",
        "1",
        "--telemetry-session-id",
        "s",
        "--telemetry-model-sha256",
        A,
        "--telemetry-workload",
        "w",
        "--approved-model-sha256",
        A,
        "--output",
        str(tmp_path / "profile.json"),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert result.returncode == 2
    assert "only valid" in result.stderr
