"""Fail-closed tests for the browser neural policy parity contract.

These exercise the canonical comparator (``neural_parity``) and prove that the
gate validates the **actual returned browser policy** — legal indices, their
order, normalised probabilities, shapes, dtypes and model identity — rather than
reconstructing that policy from logits.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

SPIKE = Path(__file__).resolve().parents[1] / "spikes/browser_offline"
if str(SPIKE) not in sys.path:
    sys.path.insert(0, str(SPIKE))

import neural_parity as parity  # noqa: E402

MODEL = "a" * 64
DIGEST = "b" * 64
TOLERANCES = {
    "logit_atol": 1e-3,
    "value_atol": 1e-4,
    "probability_atol": 1e-5,
    "probability_relative_atol": 1e-3,
    "probability_relative_floor": 1e-4,
    "near_tie_atol": 1e-4,
    "postprocess_atol": 1e-6,
}
LOGITS = [0.1, -5.0, 0.3, -9.0]
LEGAL = [0, 2]


def _softmax(values):
    return parity.stable_softmax(values)


def _probabilities(logits, legal):
    return _softmax([logits[index] for index in legal])


def _reference(*, logits=None, legal=None):
    logits = list(logits if logits is not None else LOGITS)
    legal = list(legal if legal is not None else LEGAL)
    return {
        "schema": 2,
        "model_sha256": MODEL,
        "tolerances": dict(TOLERANCES),
        "cases": [
            {
                "position": "brandubh-opening",
                "kind": "opening",
                "position_digest": DIGEST,
                "terminal": False,
                "legal": legal,
                "policy_logits": logits,
                "value": 0.2,
                "legal_probabilities": _probabilities(logits, legal),
                "policy_shape": [1, len(logits)],
                "value_shape": [1],
                "policy_dtype": "float32",
                "value_dtype": "float32",
            },
            {
                "position": "brandubh-terminal",
                "kind": "terminal",
                "position_digest": "c" * 64,
                "terminal": True,
                "legal": [],
                "policy_logits": None,
                "value": 0.0,
                "legal_probabilities": [],
                "policy_shape": [1, 2401],
                "value_shape": [1],
                "policy_dtype": "float32",
                "value_dtype": "float32",
                "inference_bypassed": True,
            },
        ],
    }


def _output(
    reference,
    *,
    logits=None,
    value=None,
    legal=None,
    probabilities=None,
    policy_shape=None,
    value_shape=None,
    policy_dtype="float32",
    value_dtype="float32",
    model=MODEL,
    position_digest=DIGEST,
    index=None,
):
    case = reference["cases"][0]
    logits = list(logits if logits is not None else case["policy_logits"])
    legal = list(legal if legal is not None else case["legal"])
    probabilities = (
        list(probabilities) if probabilities is not None else _probabilities(logits, legal)
    )
    return {
        "position": case["position"],
        "index": index,
        "position_digest": position_digest,
        "model_sha256": model,
        "policy_logits": logits,
        "value": value if value is not None else case["value"],
        "legal": legal,
        "legal_probabilities": probabilities,
        "policy_shape": policy_shape if policy_shape is not None else case["policy_shape"],
        "value_shape": value_shape if value_shape is not None else case["value_shape"],
        "policy_dtype": policy_dtype,
        "value_dtype": value_dtype,
    }


def _observed(reference, *, output=None, terminal_skipped=True, **kwargs):
    return {
        "schema": 3,
        "provider": "wasm:wasm",
        "model_sha256": MODEL,
        "outputs": [
            output if output is not None else _output(reference, **kwargs),
            {"position": "brandubh-terminal", "skipped": terminal_skipped},
        ],
    }


def test_identical_reported_policy_passes():
    reference = _reference()
    result = parity.compare(reference, _observed(reference))
    assert result["ok"] is True, result["failures"]
    assert result["non_terminal_cases"] == 1
    assert result["worst_postprocess_abs_error"] <= TOLERANCES["postprocess_atol"]


@pytest.mark.parametrize(
    "kwargs, marker",
    [
        (
            {
                "logits": [LOGITS[0] + float("nan"), *LOGITS[1:]],
                "legal": [0, 2],
                "probabilities": [0.5, 0.5],
            },
            "non-finite",
        ),
        ({"value": float("inf")}, "non-finite"),
        ({"value": 1.5}, "outside [-1, 1]"),
        ({"logits": [LOGITS[0] + 0.01, *LOGITS[1:]]}, "logit abs error"),
        ({"value": 0.25}, "value abs error"),
        ({"policy_shape": [1, 1]}, "policy shape"),
        ({"value_shape": [2]}, "value shape"),
        ({"policy_dtype": "float16"}, "policy_dtype"),
        ({"value_dtype": "int32"}, "value_dtype"),
        ({"model": "d" * 64}, "model identity"),
        ({"position_digest": "e" * 64}, "position identity"),
        ({"legal": [0, 1]}, "legal indices/order"),
        ({"legal": [2, 0]}, "legal indices/order"),
        ({"probabilities": [0.9, 0.9]}, "do not sum to 1"),
        ({"probabilities": [0.4, 0.6]}, "inconsistent with the returned"),
    ],
)
def test_mandatory_policy_corruption_fails(kwargs, marker):
    reference = _reference()
    result = parity.compare(reference, _observed(reference, **kwargs))
    assert result["ok"] is False
    assert any(marker in message for message in result["failures"]), result["failures"]


def test_duplicate_legal_indices_fail():
    reference = _reference()
    result = parity.compare(reference, _observed(reference, legal=[0, 0]))
    assert result["ok"] is False
    assert any("duplicate observed legal indices" in message for message in result["failures"])


def test_material_ranking_change_fails():
    reference = _reference(logits=[0.1, -5.0, 0.3, -9.0])
    logits = [0.4, -5.0, 0.0, -9.0]
    result = parity.compare(reference, _observed(reference, logits=logits))
    assert result["ok"] is False
    assert any("material top-5 ranking change" in message for message in result["failures"])


def test_near_tied_reordering_is_informational_only():
    logits = [0.1, -5.0, 0.1000001, -9.0]
    reference = _reference(logits=logits)
    swapped = [0.1000001, -5.0, 0.1, -9.0]
    result = parity.compare(reference, _observed(reference, logits=swapped))
    assert result["ok"] is True, result["failures"]
    assert any("near-tied" in message for message in result["notes"])


def test_missing_case_fails():
    reference = _reference()
    observed = _observed(reference)
    observed["outputs"].pop(0)
    result = parity.compare(reference, observed)
    assert result["ok"] is False
    assert any("missing observed case" in message for message in result["failures"])


def test_duplicate_observed_position_fails():
    reference = _reference()
    observed = _observed(reference)
    observed["outputs"].append(dict(observed["outputs"][0]))
    result = parity.compare(reference, observed)
    assert result["ok"] is False
    assert any("duplicate position identity" in message for message in result["failures"])


def test_duplicate_reference_position_fails():
    reference = _reference()
    reference["cases"].append(dict(reference["cases"][0]))
    result = parity.compare(reference, _observed(_reference()))
    assert result["ok"] is False
    assert any("duplicate position identity" in message for message in result["failures"])


def test_empty_reference_fails():
    reference = _reference()
    reference["cases"] = [reference["cases"][1]]  # terminal only
    result = parity.compare(reference, _observed(_reference()))
    assert result["ok"] is False
    assert any("no non-terminal reference case" in message for message in result["failures"])


def test_terminal_case_that_runs_inference_fails():
    reference = _reference()
    result = parity.compare(reference, _observed(reference, terminal_skipped=False))
    assert result["ok"] is False
    assert any("terminal case must bypass inference" in message for message in result["failures"])


def test_top_level_model_identity_mismatch_fails():
    reference = _reference()
    observed = _observed(reference)
    observed["model_sha256"] = "f" * 64
    result = parity.compare(reference, observed)
    assert result["ok"] is False
    assert any("model identity" in message for message in result["failures"])


def test_batched_count_and_order_are_validated():
    reference = _reference()
    base = _observed(reference)
    wrong_count = dict(base, batch_count=2)
    assert parity.compare(reference, wrong_count)["ok"] is False

    output = _output(reference, index=3)
    wrong_order = dict(base, batch_count=1, outputs=[output, base["outputs"][1]])
    result = parity.compare(reference, wrong_order)
    assert result["ok"] is False
    assert any("ordering" in message for message in result["failures"])


def test_extreme_finite_logits_do_not_overflow():
    logits = [1000.0, -1000.0, 0.0, -1000.0]
    reference = _reference(logits=logits)
    result = parity.compare(reference, _observed(reference))
    assert result["ok"] is True, result["failures"]
    assert all(math.isfinite(value) for value in reference["cases"][0]["legal_probabilities"])


@pytest.mark.parametrize("tolerance", list(TOLERANCES))
def test_nonpositive_tolerances_are_rejected(tolerance):
    reference = _reference()
    reference["tolerances"][tolerance] = 0
    result = parity.compare(reference, _observed(reference))
    assert result["ok"] is False
    assert any("tolerance" in message for message in result["failures"])


def test_nonfinite_reference_values_are_rejected():
    reference = _reference()
    reference["cases"][0]["value"] = float("nan")
    result = parity.compare(reference, _observed(reference))
    assert result["ok"] is False
    assert any("non-finite reference value" in message for message in result["failures"])


def test_reconstructed_probabilities_cannot_substitute_for_reported_ones():
    # The comparator must compare the *reported* probabilities, so reporting a
    # normalised but wrong distribution must fail even though the logits are
    # correct and a reconstruction would look perfect.
    reference = _reference()
    reported = list(reference["cases"][0]["legal_probabilities"])
    reported = [reported[1], reported[0]]
    result = parity.compare(reference, _observed(reference, probabilities=reported))
    assert result["ok"] is False
    assert any(
        "inconsistent with the returned" in message or "abs error" in message
        for message in result["failures"]
    )
