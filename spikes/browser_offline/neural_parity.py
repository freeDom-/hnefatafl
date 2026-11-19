"""Canonical numerical-parity comparison for neural reference vectors.

The reference file (``reference.json``) is produced by ``onnx_export.py`` from
native PyTorch outputs. The *observed* output set comes from native ONNX Runtime,
browser WASM or browser WebGPU and must include the **actual policy the runtime
returned**: logits, defender value, legal-action indices and their order, the
legal-only normalised probabilities, tensor shapes/dtypes and model identity.

Browser parity therefore exercises the browser's own postprocessing: the returned
legal indices and probabilities are validated as reported, then additionally
checked for mathematical consistency with the returned logits. Reconstructing the
browser policy from its logits and using that reconstruction as the comparison
target would hide exactly the postprocessing bugs this gate exists to catch.

All checks fail closed: non-finite values, missing cases, duplicate identities,
shape/dtype mismatches, reordered or invalid legal indices, non-normalised
probabilities, inconsistent postprocessing or material ranking changes are
mandatory failures. Near-tied actions may reorder within a justified tolerance and
are reported as informational notes.

Usage as a gate:

    python neural_parity.py reference.json observed.json --output parity.json
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

PROBABILITY_SUM_ATOL = 1e-4
PROBABILITY_NEGATIVE_TOL = 1e-9
POSTPROCESS_ATOL = 1e-6
EXPECTED_DTYPE = "float32"
TOLERANCE_DEFAULTS = {
    "logit_atol": 1e-3,
    "value_atol": 1e-4,
    "probability_atol": 1e-5,
    "probability_relative_atol": 1e-3,
    "probability_relative_floor": 1e-4,
    "near_tie_atol": 1e-4,
    "postprocess_atol": POSTPROCESS_ATOL,
}


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _finite_list(values: Any, *, expected: int | None = None) -> bool:
    return (
        isinstance(values, list)
        and all(_finite(item) for item in values)
        and (expected is None or len(values) == expected)
    )


def stable_softmax(values: list[float]) -> list[float]:
    """Numerically stable softmax; raises on non-finite input or normaliser."""
    if not values:
        return []
    if not all(_finite(value) for value in values):
        raise ValueError("Softmax input contains a non-finite value")
    maximum = max(values)
    exponentials = [math.exp(value - maximum) for value in values]
    total = sum(exponentials)
    if not math.isfinite(total) or total <= 0:
        raise ValueError("Softmax normaliser is not finite and positive")
    return [item / total for item in exponentials]


def _legal_probabilities(logits: list[float], legal: list[int]) -> list[float]:
    return stable_softmax([logits[index] for index in legal])


def _ranked(ids: list[int], probabilities: list[float], k: int = 5) -> list[int]:
    order = sorted(range(len(ids)), key=lambda position: -probabilities[position])
    return [ids[position] for position in order[:k]]


def _tolerances(raw: Any) -> dict[str, float]:
    values = dict(TOLERANCE_DEFAULTS)
    if isinstance(raw, dict):
        for name in TOLERANCE_DEFAULTS:
            if name in raw:
                values[name] = raw[name]
    return values


def _index_by_position(
    cases: list[dict[str, Any]], *, side: str, failures: list[str]
) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for case in cases:
        if not isinstance(case, dict) or not isinstance(case.get("position"), str):
            failures.append(f"{side}: malformed case entry")
            continue
        position = case["position"]
        if position in index:
            failures.append(f"{side}: duplicate position identity {position!r}")
            continue
        index[position] = case
    return index


def _validate_reference(reference: dict[str, Any], failures: list[str]) -> dict[str, float]:
    tolerances = _tolerances(reference.get("tolerances"))
    for name, value in tolerances.items():
        if not _finite(value) or value <= 0:
            failures.append(f"reference tolerance {name} is not finite and positive")
    if not reference.get("model_sha256") or not isinstance(reference["model_sha256"], str):
        failures.append("reference: missing model identity")
    cases = reference.get("cases")
    if not isinstance(cases, list) or not cases:
        failures.append("reference: no reference cases (empty parity report)")
        return tolerances
    for case in cases:
        if not isinstance(case, dict):
            continue
        position = case.get("position")
        if case.get("terminal"):
            continue
        if not _finite_list(case.get("policy_logits")):
            failures.append(f"{position}: non-finite reference policy logits")
        if not _finite(case.get("value")):
            failures.append(f"{position}: non-finite reference value")
        legal = case.get("legal")
        if not isinstance(legal, list) or not legal:
            failures.append(f"{position}: reference has no legal actions")
        elif len(set(legal)) != len(legal) or any(
            not isinstance(index, int) or isinstance(index, bool) or index < 0 for index in legal
        ):
            failures.append(f"{position}: invalid reference legal indices")
        if not _finite_list(case.get("legal_probabilities")):
            failures.append(f"{position}: non-finite reference probabilities")
    return tolerances


def _result(
    failures: list[str],
    notes: list[str],
    rows: list[dict[str, Any]],
    *,
    provider: str | None,
    model_sha256: Any,
    tolerances: dict[str, float] | None,
    worst: dict[str, float] | None = None,
    non_terminal: int = 0,
    ranking_material: int = 0,
) -> dict[str, Any]:
    worst = worst or {
        "logit_max_abs_error": 0.0,
        "value_abs_error": 0.0,
        "legal_probability_max_abs_error": 0.0,
        "legal_probability_max_relative_error": 0.0,
        "postprocess_max_abs_error": 0.0,
    }
    return {
        "ok": not failures,
        "provider": provider,
        "providers": sorted({provider}) if provider else [],
        "model_sha256": model_sha256,
        "cases": len(rows),
        "non_terminal_cases": non_terminal,
        "tolerances": tolerances,
        "worst_logit_max_abs_error": worst["logit_max_abs_error"],
        "worst_value_abs_error": worst["value_abs_error"],
        "worst_legal_probability_max_abs_error": worst["legal_probability_max_abs_error"],
        "worst_legal_probability_max_relative_error": worst["legal_probability_max_relative_error"],
        "worst_postprocess_abs_error": worst["postprocess_max_abs_error"],
        "material_ranking_changes": ranking_material,
        "failures": failures,
        "notes": notes,
        "rows": rows,
    }


def compare(
    reference: dict[str, Any],
    observed: dict[str, Any],
    *,
    provider: str | None = None,
) -> dict[str, Any]:
    """Compare actual provider outputs against native reference vectors."""
    failures: list[str] = []
    notes: list[str] = []
    rows: list[dict[str, Any]] = []
    if not isinstance(reference, dict) or not isinstance(observed, dict):
        return _result(
            ["comparison inputs must be objects"],
            notes,
            rows,
            provider=provider,
            model_sha256=None,
            tolerances=None,
        )

    tolerances = _validate_reference(reference, failures)
    model_sha = reference.get("model_sha256")
    if observed.get("model_sha256") != model_sha:
        failures.append(
            f"observed model identity {observed.get('model_sha256')!r} != {model_sha!r}"
        )

    expected = _index_by_position(reference.get("cases", []), side="reference", failures=failures)
    actual = _index_by_position(observed.get("outputs", []), side="observed", failures=failures)
    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    if missing:
        failures.append(f"missing observed case(s): {missing[:5]}")
    if extra:
        notes.append(f"{len(extra)} unexpected observed case(s)")

    batch_count = observed.get("batch_count")
    if batch_count is not None:
        compared_non_terminal = sum(1 for case in expected.values() if not case.get("terminal"))
        if not isinstance(batch_count, int) or batch_count != compared_non_terminal:
            failures.append(
                f"batched result count {batch_count!r} != {compared_non_terminal} expected"
            )
        indexes = [case.get("index") for case in actual.values()]
        if any(index is not None for index in indexes):
            present = sorted(index for index in indexes if index is not None)
            if present != list(range(len(indexes))):
                failures.append("batched result ordering/indexes are not a complete ordered set")

    logit_atol = tolerances["logit_atol"]
    value_atol = tolerances["value_atol"]
    probability_atol = tolerances["probability_atol"]
    probability_relative_atol = tolerances["probability_relative_atol"]
    probability_relative_floor = tolerances["probability_relative_floor"]
    near_tie_atol = tolerances["near_tie_atol"]
    postprocess_atol = tolerances["postprocess_atol"]

    worst = {
        "logit_max_abs_error": 0.0,
        "value_abs_error": 0.0,
        "legal_probability_max_abs_error": 0.0,
        "legal_probability_max_relative_error": 0.0,
        "postprocess_max_abs_error": 0.0,
    }
    ranking_material = 0
    non_terminal = 0

    for position in sorted(set(expected) & set(actual)):
        expected_case = expected[position]
        actual_case = actual[position]
        if expected_case.get("terminal"):
            if actual_case.get("skipped") is not True:
                failures.append(f"{position}: terminal case must bypass inference")
            rows.append({"position": position, "skipped": True})
            continue
        if actual_case.get("skipped"):
            failures.append(f"{position}: non-terminal case was skipped")
            continue
        non_terminal += 1

        if actual_case.get("model_sha256") not in (None, model_sha):
            failures.append(f"{position}: per-case model identity mismatch")
        expected_digest = expected_case.get("position_digest")
        actual_digest = actual_case.get("position_digest")
        if expected_digest is not None and actual_digest not in (None, expected_digest):
            failures.append(f"{position}: position identity mismatch")

        legal_expected = expected_case.get("legal") or []
        legal_actual = actual_case.get("legal")
        if not isinstance(legal_actual, list) or any(
            not isinstance(index, int) or isinstance(index, bool) for index in legal_actual
        ):
            failures.append(f"{position}: observed legal indices are malformed")
            continue
        if len(set(legal_actual)) != len(legal_actual):
            failures.append(f"{position}: duplicate observed legal indices")
        if legal_actual != legal_expected:
            failures.append(
                f"{position}: observed legal indices/order differ from the reference "
                f"({legal_actual[:4]} != {legal_expected[:4]})"
            )

        expected_logits = expected_case.get("policy_logits")
        observed_logits = actual_case.get("policy_logits")
        if not _finite_list(expected_logits) or not _finite_list(observed_logits):
            failures.append(f"{position}: policy logits are missing or non-finite")
            continue
        if len(observed_logits) != len(expected_case["policy_logits"]):
            failures.append(
                f"{position}: policy logit count {len(observed_logits)} != "
                f"{len(expected_case['policy_logits'])}"
            )
            continue
        if any(not (0 <= index < len(observed_logits)) for index in legal_actual):
            failures.append(f"{position}: observed legal index out of range")
            continue

        expected_policy_shape = expected_case.get("policy_shape")
        expected_value_shape = expected_case.get("value_shape")
        if isinstance(batch_count, int) and batch_count > 0:
            # A batched response legitimately reports [batch, size**4]/[batch].
            if isinstance(expected_policy_shape, list) and len(expected_policy_shape) == 2:
                expected_policy_shape = [batch_count, expected_policy_shape[1]]
            if expected_value_shape == [1]:
                expected_value_shape = [batch_count]
        if actual_case.get("policy_shape") not in (None, expected_policy_shape):
            failures.append(
                f"{position}: policy shape {actual_case.get('policy_shape')} != "
                f"{expected_policy_shape}"
            )
        if actual_case.get("value_shape") not in (None, expected_value_shape):
            failures.append(
                f"{position}: value shape {actual_case.get('value_shape')} != "
                f"{expected_value_shape}"
            )
        for field in ("policy_dtype", "value_dtype"):
            value = actual_case.get(field)
            if value is not None and value != EXPECTED_DTYPE:
                failures.append(f"{position}: {field} {value!r} != {EXPECTED_DTYPE}")

        if not _finite(actual_case.get("value")):
            failures.append(f"{position}: observed defender value is missing or non-finite")
            continue
        if abs(actual_case["value"]) > 1 + 1e-9:
            failures.append(f"{position}: observed value {actual_case['value']} outside [-1, 1]")

        expected_probs = expected_case.get("legal_probabilities")
        if not _finite_list(expected_probs):
            expected_probs = _legal_probabilities(
                expected_case["policy_logits"], expected_case.get("legal") or []
            )

        if not isinstance(actual_case.get("legal_probabilities"), list):
            failures.append(f"{position}: observed legal probabilities are missing")
            continue
        if not _finite_list(actual_case["legal_probabilities"]) or len(
            actual_case["legal_probabilities"]
        ) != len(actual_case.get("legal") or []):
            failures.append(f"{position}: observed legal probabilities are malformed")
            continue
        if any(value < -PROBABILITY_NEGATIVE_TOL for value in actual_case["legal_probabilities"]):
            failures.append(f"{position}: observed legal probability is negative")
        if abs(sum(actual_case["legal_probabilities"]) - 1) > PROBABILITY_SUM_ATOL:
            failures.append(f"{position}: observed legal probabilities do not sum to 1")

        try:
            recomputed = _legal_probabilities(observed_logits, actual_case.get("legal") or [])
        except ValueError as error:
            failures.append(f"{position}: {error}")
            continue

        logit_error = max(
            abs(a - b) for a, b in zip(actual_case["policy_logits"], expected_case["policy_logits"])
        )
        value_error = abs(float(actual_case["value"]) - float(expected_case["value"]))
        probability_error = max(
            abs(a - e) for a, e in zip(actual_case["legal_probabilities"], expected_probs)
        )
        postprocess_error = max(
            abs(a - b) for a, b in zip(actual_case["legal_probabilities"], recomputed)
        )
        relative = max(
            (
                abs(a - e) / e
                for a, e in zip(actual_case["legal_probabilities"], expected_probs)
                if e >= probability_relative_floor
            ),
            default=0.0,
        )
        worst["logit_max_abs_error"] = max(worst["logit_max_abs_error"], logit_error)
        worst["value_abs_error"] = max(worst["value_abs_error"], value_error)
        worst["legal_probability_max_abs_error"] = max(
            worst["legal_probability_max_abs_error"], probability_error
        )
        worst["legal_probability_max_relative_error"] = max(
            worst["legal_probability_max_relative_error"], relative
        )
        worst["postprocess_max_abs_error"] = max(
            worst["postprocess_max_abs_error"], postprocess_error
        )

        if logit_error > logit_atol:
            failures.append(f"{position}: logit abs error {logit_error:.3g} > {logit_atol}")
        if value_error > value_atol:
            failures.append(f"{position}: value abs error {value_error:.3g} > {value_atol}")
        if probability_error > probability_atol:
            failures.append(
                f"{position}: legal probability abs error {probability_error:.3g} > "
                f"{probability_atol}"
            )
        if relative > probability_relative_atol:
            failures.append(
                f"{position}: legal probability relative error {relative:.3g} > "
                f"{probability_relative_atol}"
            )
        if postprocess_error > postprocess_atol:
            failures.append(
                f"{position}: returned probabilities are inconsistent with the returned "
                f"logits and legal indices ({postprocess_error:.3g} > {postprocess_atol})"
            )

        expected_rank = _ranked(legal_expected, expected_probs)
        actual_rank = _ranked(actual_case.get("legal") or [], actual_case["legal_probabilities"])
        if expected_rank != actual_rank:
            by_action = dict(zip(legal_expected, expected_probs))
            material = any(
                abs(by_action.get(left, 0.0) - by_action.get(right, 0.0)) > near_tie_atol
                for left, right in zip(expected_rank, actual_rank)
                if left != right
            )
            if material:
                ranking_material += 1
                failures.append(f"{position}: material top-5 ranking change")
            else:
                notes.append(f"{position}: near-tied top-5 reordering within tolerance")

        rows.append(
            {
                "position": position,
                "kind": expected_case.get("kind"),
                "legal_count": len(legal_actual),
                "logit_max_abs_error": logit_error,
                "value_abs_error": value_error,
                "legal_probability_max_abs_error": probability_error,
                "legal_probability_max_relative_error": relative,
                "postprocess_max_abs_error": postprocess_error,
                "top5_equal": expected_rank == actual_rank,
                "legal_probability_sum": sum(actual_case["legal_probabilities"]),
            }
        )

    if non_terminal == 0:
        failures.append("no non-terminal reference case was compared (empty parity report)")

    return _result(
        failures,
        notes,
        rows,
        provider=provider or observed.get("provider"),
        model_sha256=model_sha,
        tolerances=tolerances,
        worst=worst,
        non_terminal=non_terminal,
        ranking_material=ranking_material,
    )


def _read(spec: Path) -> str:
    return sys.stdin.read() if str(spec) == "-" else spec.read_text()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reference", type=Path)
    parser.add_argument("observed", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    reference = json.loads(_read(args.reference))
    observed = json.loads(_read(args.observed))
    report = compare(reference, observed)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                "ok": report["ok"],
                "provider": report.get("provider"),
                "cases": report["cases"],
                "worst_logit_max_abs_error": report["worst_logit_max_abs_error"],
                "worst_value_abs_error": report["worst_value_abs_error"],
                "worst_postprocess_abs_error": report["worst_postprocess_abs_error"],
                "material_ranking_changes": report["material_ranking_changes"],
                "failures": report["failures"][:10],
            },
            indent=2,
        )
    )
    for message in report["failures"]:
        print(f"PARITY FAILURE: {message}", file=sys.stderr)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
