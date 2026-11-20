"""Fail-closed tests for the complete neural inference-response contract.

These verify that the comparator requires the actual response fields (model and
ruleset identity, request/position identity, tensor data/shape/dtype, legal
indices/order, probabilities, batch count/index/order) and that numerical
corruptions fail.
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
RULESET = "brandubh"
DIGEST = "b" * 64
DIGESTS = ["b" * 64, "c" * 64, "d" * 64]
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


def _probabilities(logits, legal):
    return parity.stable_softmax([logits[index] for index in legal])


def _case(position, digest, logits=None, legal=None, value=0.2, terminal=False):
    logits = list(logits if logits is not None else LOGITS)
    legal = list(legal if legal is not None else LEGAL)
    if terminal:
        return {
            "position": position,
            "kind": "terminal",
            "position_digest": digest,
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
        }
    return {
        "position": position,
        "kind": "opening",
        "position_digest": digest,
        "terminal": False,
        "legal": legal,
        "policy_logits": logits,
        "value": value,
        "legal_probabilities": _probabilities(logits, legal),
        "policy_shape": [1, len(logits)],
        "value_shape": [1],
        "policy_dtype": "float32",
        "value_dtype": "float32",
    }


def _reference(*, logits=None, legal=None, cases=None):
    return {
        "schema": 2,
        "model_sha256": MODEL,
        "ruleset": RULESET,
        "tolerances": dict(TOLERANCES),
        "cases": cases
        or [
            _case("brandubh-opening", DIGEST, logits=logits, legal=legal),
            _case("brandubh-terminal", "e" * 64, terminal=True),
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
    request_id=None,
    index=None,
):
    case = next(entry for entry in reference["cases"] if not entry["terminal"])
    logits = list(logits if logits is not None else case["policy_logits"])
    legal = list(legal if legal is not None else case["legal"])
    probabilities = (
        list(probabilities) if probabilities is not None else _probabilities(logits, legal)
    )
    return {
        "position": case["position"],
        "index": index,
        "request_id": request_id if request_id is not None else case["position"],
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


def _observed(reference, *, output=None, **kwargs):
    return {
        "schema": 4,
        "provider": "wasm:wasm",
        "model_sha256": MODEL,
        "model_ruleset": RULESET,
        "outputs": [
            output if output is not None else _output(reference, **kwargs),
            {"position": "brandubh-terminal", "skipped": True, "harness_generated": True},
        ],
    }


def test_complete_response_passes():
    reference = _reference()
    result = parity.compare(reference, _observed(reference))
    assert result["ok"] is True, result["failures"]
    assert result["non_terminal_cases"] == 1
    assert result["worst_postprocess_abs_error"] <= TOLERANCES["postprocess_atol"]


@pytest.mark.parametrize(
    "kwargs, marker",
    [
        (
            {"logits": [LOGITS[0] + float("nan"), *LOGITS[1:]], "probabilities": [0.5, 0.5]},
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
        ({"position_digest": "f" * 64}, "position identity"),
        ({"legal": [0, 1]}, "legal indices/order"),
        ({"legal": [2, 0]}, "legal indices/order"),
        ({"probabilities": [0.9, 0.9]}, "do not sum to 1"),
        ({"probabilities": [0.4, 0.6]}, "inconsistent with the returned"),
    ],
)
def test_numerical_and_metadata_corruption_fails(kwargs, marker):
    reference = _reference()
    result = parity.compare(reference, _observed(reference, **kwargs))
    assert result["ok"] is False
    assert any(marker in message for message in result["failures"]), result["failures"]


def test_missing_per_case_model_identity_fails():
    reference = _reference()
    output = _output(reference)
    output.pop("model_sha256")
    result = parity.compare(reference, _observed(reference, output=output))
    assert result["ok"] is False
    assert any("model identity" in message for message in result["failures"])


def test_missing_policy_shape_fails():
    reference = _reference()
    output = _output(reference)
    output.pop("policy_shape")
    result = parity.compare(reference, _observed(reference, output=output))
    assert result["ok"] is False
    assert any(
        "missing mandatory field 'policy_shape'" in message for message in result["failures"]
    )


def test_missing_value_dtype_fails():
    reference = _reference()
    output = _output(reference)
    output.pop("value_dtype")
    result = parity.compare(reference, _observed(reference, output=output))
    assert result["ok"] is False
    assert any("missing mandatory field 'value_dtype'" in message for message in result["failures"])


def test_wrong_request_association_fails():
    reference = _reference()
    result = parity.compare(reference, _observed(reference, request_id="other-position"))
    assert result["ok"] is False
    assert any("not associated with this position" in message for message in result["failures"])


def test_unexpected_response_structure_fails():
    reference = _reference()
    observed = _observed(reference)
    observed["outputs"] = {"not": "a list"}
    result = parity.compare(reference, observed)
    assert result["ok"] is False
    assert any("missing outputs array" in message for message in result["failures"])


def test_missing_top_level_model_identity_fails():
    reference = _reference()
    observed = _observed(reference)
    observed.pop("model_sha256")
    result = parity.compare(reference, observed)
    assert result["ok"] is False
    assert any("observed model identity" in message for message in result["failures"])


def test_missing_top_level_ruleset_identity_fails():
    reference = _reference()
    observed = _observed(reference)
    observed.pop("model_ruleset")
    result = parity.compare(reference, observed)
    assert result["ok"] is False
    assert any("model ruleset" in message for message in result["failures"])


def test_terminal_skip_must_be_marked_harness_generated():
    reference = _reference()
    observed = _observed(reference)
    observed["outputs"][1] = {"position": "brandubh-terminal", "skipped": True}
    result = parity.compare(reference, observed)
    assert result["ok"] is False
    assert any("harness_generated" in message for message in result["failures"])


# ---------- independent batch contract ----------

BATCH_CASES = [
    _case("p0", DIGESTS[0]),
    _case("p1", DIGESTS[1]),
    _case("p2", DIGESTS[2]),
]


def _batch_reference():
    return _reference(cases=BATCH_CASES)


def _batch_observed(reference):
    entries = reference["cases"]
    outputs = [
        {
            "position": entry["position"],
            "index": index,
            "request_id": entry["position"],
            "position_digest": entry["position_digest"],
            "model_sha256": MODEL,
            "policy_logits": entry["policy_logits"],
            "value": entry["value"],
            "legal": entry["legal"],
            "legal_probabilities": entry["legal_probabilities"],
            "policy_shape": [len(entries), len(entry["policy_logits"])],
            "value_shape": [len(entries)],
            "policy_dtype": "float32",
            "value_dtype": "float32",
        }
        for index, entry in enumerate(entries)
    ]
    return {
        "schema": 4,
        "provider": "wasm:wasm",
        "model_sha256": MODEL,
        "model_ruleset": RULESET,
        "batch_count": len(entries),
        "results_count": len(outputs),
        "outputs": outputs,
    }


def test_correct_batch_passes():
    reference = _batch_reference()
    result = parity.compare(reference, _batch_observed(reference))
    assert result["ok"] is True, result["failures"]
    assert result["batch_count"] == 3


def _corrupt_batch(kind):
    reference = _batch_reference()
    observed = _batch_observed(reference)
    outputs = observed["outputs"]
    if kind == "batch-count":
        observed["batch_count"] += 1
    elif kind == "missing-result":
        outputs.pop()
        observed["results_count"] = len(outputs)
    elif kind == "extra-result":
        outputs.append({**outputs[0], "index": len(outputs), "request_id": "extra"})
        observed["results_count"] = len(outputs)
    elif kind == "duplicate-index":
        outputs[1]["index"] = outputs[0]["index"]
    elif kind == "missing-index":
        outputs[0].pop("index")
    elif kind == "wrong-index":
        outputs[0]["index"] = 1
    elif kind == "reorder":
        outputs.reverse()
    elif kind == "position":
        outputs[0]["request_id"], outputs[1]["request_id"] = (
            outputs[1]["request_id"],
            outputs[0]["request_id"],
        )
        outputs[0]["position_digest"], outputs[1]["position_digest"] = (
            outputs[1]["position_digest"],
            outputs[0]["position_digest"],
        )
    return reference, observed


@pytest.mark.parametrize(
    "kind",
    [
        "batch-count",
        "missing-result",
        "extra-result",
        "duplicate-index",
        "missing-index",
        "wrong-index",
        "reorder",
        "position",
    ],
)
def test_batch_corruption_independently_fails(kind):
    reference, observed = _corrupt_batch(kind)
    result = parity.compare(reference, observed)
    assert result["ok"] is False, f"{kind} unexpectedly passed"
    assert result["failures"], kind


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
    reference["cases"] = [reference["cases"][1]]
    result = parity.compare(reference, _observed(_reference()))
    assert result["ok"] is False
    assert any("no non-terminal reference case" in message for message in result["failures"])


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
