"""Fail-closed numerical-parity tests for the neural reference-vector contract.

These exercise the canonical comparator (``neural_parity``) and prove that NaN,
infinite, mis-shaped, missing, corrupted or non-normalised outputs fail the gate,
while justified near-tied reorderings are informational only.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

SPIKE = Path(__file__).resolve().parents[1] / "spikes/browser_offline"
if str(SPIKE) not in sys.path:
    sys.path.insert(0, str(SPIKE))

import neural_parity as np_compare  # noqa: E402

DIGEST = "a" * 64
TOLERANCES = {
    "logit_atol": 1e-3,
    "value_atol": 1e-4,
    "probability_atol": 1e-5,
    "probability_relative_atol": 1e-3,
    "probability_relative_floor": 1e-4,
    "near_tie_atol": 1e-4,
}


def _probabilities(logits, legal):
    return np_compare.stable_softmax([logits[index] for index in legal])


def _reference(*, logits=(0.1, -5.0, 0.3, -9.0), legal=(0, 2), value=0.2):
    return {
        "schema": 2,
        "model_sha256": DIGEST,
        "tolerances": dict(TOLERANCES),
        "cases": [
            {
                "position": "brandubh-opening",
                "kind": "opening",
                "terminal": False,
                "legal": list(legal),
                "policy_logits": list(logits),
                "value": value,
                "legal_probabilities": _probabilities(logits, legal),
                "policy_shape": [1, len(logits)],
                "value_shape": [1],
            },
            {
                "position": "brandubh-terminal",
                "kind": "terminal",
                "terminal": True,
                "legal": [],
                "policy_logits": None,
                "value": 0.0,
                "legal_probabilities": [],
                "policy_shape": [1, 2401],
                "value_shape": [1],
                "inference_bypassed": True,
            },
        ],
    }


def _observed(reference, *, logits=None, value=None, shapes=None):
    case = reference["cases"][0]
    return {
        "provider": "wasm:wasm",
        "model_sha256": DIGEST,
        "outputs": [
            {
                "position": case["position"],
                "policy_logits": list(logits if logits is not None else case["policy_logits"]),
                "value": value if value is not None else case["value"],
                "policy_shape": (shapes or {}).get("policy", case["policy_shape"]),
                "value_shape": (shapes or {}).get("value", case["value_shape"]),
            },
            {"position": reference["cases"][1]["position"], "skipped": True},
        ],
    }


def test_identical_native_and_browser_outputs_pass():
    reference = _reference()
    result = np_compare.compare(reference, _observed(reference))
    assert result["ok"] is True, result["failures"]
    assert result["cases"] == 2 and result["non_terminal_cases"] == 1


def test_nan_logit_fails_closed():
    reference = _reference()
    logits = list(reference["cases"][0]["policy_logits"])
    logits[1] = float("nan")
    result = np_compare.compare(reference, _observed(reference, logits=logits))
    assert result["ok"] is False
    assert any("non-finite" in message for message in result["failures"])


def test_infinite_value_fails_closed():
    reference = _reference()
    result = np_compare.compare(reference, _observed(reference, value=float("inf")))
    assert result["ok"] is False


def test_nan_never_satisfies_a_tolerance():
    reference = _reference()
    logits = list(reference["cases"][0]["policy_logits"])
    logits[0] = float("nan")
    result = np_compare.compare(reference, _observed(reference, logits=logits))
    # A naive `abs(nan - x) <= atol` comparison would be False, but a buggy
    # implementation using `not (error > atol)` could accept NaN. This gate must
    # reject it explicitly.
    assert result["ok"] is False


def test_logit_error_above_tolerance_fails():
    reference = _reference()
    logits = list(reference["cases"][0]["policy_logits"])
    logits[2] += 0.01
    result = np_compare.compare(reference, _observed(reference, logits=logits))
    assert result["ok"] is False
    assert any("logit abs error" in message for message in result["failures"])


def test_value_error_above_tolerance_fails():
    reference = _reference()
    result = np_compare.compare(reference, _observed(reference, value=0.2005))
    assert result["ok"] is False
    assert any("value abs error" in message for message in result["failures"])


def test_missing_case_fails():
    reference = _reference()
    observed = _observed(reference)
    observed["outputs"].pop(0)
    result = np_compare.compare(reference, observed)
    assert result["ok"] is False
    assert any("missing observed case" in message for message in result["failures"])


def test_empty_parity_report_fails():
    reference = _reference()
    reference["cases"] = [reference["cases"][1]]  # terminal only
    result = np_compare.compare(reference, _observed(_reference()))
    assert result["ok"] is False
    assert any("empty parity report" in message for message in result["failures"])


def test_wrong_policy_shape_fails():
    reference = _reference()
    result = np_compare.compare(reference, _observed(reference, shapes={"policy": [1, 2]}))
    assert result["ok"] is False
    assert any("policy shape" in message for message in result["failures"])


def test_model_digest_mismatch_fails():
    reference = _reference()
    observed = _observed(reference)
    observed["model_sha256"] = "b" * 64
    result = np_compare.compare(reference, observed)
    assert result["ok"] is False
    assert any("model digest mismatch" in message for message in result["failures"])


def test_material_ranking_change_fails():
    reference = _reference(logits=(0.1, -5.0, 0.3, -9.0), legal=(0, 2))
    logits = [0.4, -5.0, 0.0, -9.0]  # reverses the legal ranking materially
    result = np_compare.compare(reference, _observed(reference, logits=logits))
    assert result["ok"] is False
    assert any("material top-5 ranking change" in message for message in result["failures"])


def test_near_tied_reordering_is_informational_only():
    logits = [0.1, -5.0, 0.1000001, -9.0]
    reference = _reference(logits=logits, legal=(0, 2))
    swapped = [0.1000001, -5.0, 0.1, -9.0]
    result = np_compare.compare(reference, _observed(reference, logits=swapped))
    assert result["ok"] is True, result["failures"]
    assert any("near-tied" in message for message in result["notes"])


def test_terminal_case_that_runs_inference_fails():
    reference = _reference()
    observed = _observed(reference)
    terminal = observed["outputs"][1]
    terminal.pop("skipped")
    terminal["policy_logits"] = [0.0] * 2401
    result = np_compare.compare(reference, observed)
    assert result["ok"] is False
    assert any("terminal case must bypass inference" in message for message in result["failures"])


def test_extreme_finite_logits_do_not_overflow():
    logits = [1000.0, -1000.0, 0.0, -1000.0]
    reference = _reference(logits=logits, legal=(0, 2))
    probabilities = reference["cases"][0]["legal_probabilities"]
    assert all(math.isfinite(value) for value in probabilities)
    assert abs(sum(probabilities) - 1) < 1e-9
    result = np_compare.compare(reference, _observed(reference))
    assert result["ok"] is True, result["failures"]


def test_non_normalised_reference_probabilities_fail():
    reference = _reference()
    reference["cases"][0]["legal_probabilities"] = [0.9, 0.9]
    result = np_compare.compare(reference, _observed(reference))
    assert result["ok"] is False
    assert any(
        "do not sum to 1" in message or "probability" in message for message in result["failures"]
    )


@pytest.mark.parametrize("tolerance", ["logit_atol", "value_atol", "probability_atol"])
def test_nonpositive_tolerances_are_rejected(tolerance):
    reference = _reference()
    reference["tolerances"][tolerance] = 0
    result = np_compare.compare(reference, _observed(reference))
    assert result["ok"] is False
    assert any("tolerances" in message for message in result["failures"])
