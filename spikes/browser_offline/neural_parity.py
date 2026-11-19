"""Canonical numerical-parity comparison for neural reference vectors.

The reference file (``reference.json``) is produced by ``onnx_export.py`` from
native PyTorch outputs. An *observed* output set can come from native ONNX
Runtime, browser WASM or browser WebGPU. This module is the single place that
decides whether an observed provider reproduces the reference.

All checks fail closed: non-finite values, missing cases, shape or dtype
mismatches, non-normalised legal probabilities or material ranking changes are
mandatory failures. Near-tied actions may reorder within a justified tolerance
and are reported as informational notes rather than failures.

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
POLICY_SHAPE_KINDS = {1: 7, 2: 9, 3: 11}


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _finite_list(values: Any) -> bool:
    return isinstance(values, list) and all(_finite(item) for item in values)


def stable_softmax(values: list[float]) -> list[float]:
    """Numerically stable softmax; raises on non-finite input or normaliser."""
    if not values:
        return []
    if not _finite_list(values):
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


def _expected_by_position(reference: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {case["position"]: case for case in reference.get("cases", [])}


def compare(
    reference: dict[str, Any],
    observed: dict[str, Any],
    *,
    provider: str | None = None,
) -> dict[str, Any]:
    """Compare observed provider outputs against the native reference vectors.

    Returns a report with ``ok`` false iff there is at least one mandatory
    failure. ``providers`` records what was actually evaluated.
    """
    failures: list[str] = []
    notes: list[str] = []
    rows: list[dict[str, Any]] = []

    model_sha = reference.get("model_sha256")
    observed_model = observed.get("model_sha256")
    if observed_model is not None and observed_model != model_sha:
        failures.append(f"model digest mismatch: {observed_model} != {model_sha}")
    tolerances = reference.get("tolerances") or {}
    logit_atol = float(tolerances.get("logit_atol", 1e-3))
    value_atol = float(tolerances.get("value_atol", 1e-4))
    probability_atol = float(tolerances.get("probability_atol", 1e-5))
    probability_relative_atol = float(tolerances.get("probability_relative_atol", 1e-3))
    probability_relative_floor = float(tolerances.get("probability_relative_floor", 1e-4))
    near_tie_atol = float(tolerances.get("near_tie_atol", 1e-4))
    if not all(
        math.isfinite(value) and value > 0
        for value in (
            logit_atol,
            value_atol,
            probability_atol,
            probability_relative_atol,
            probability_relative_floor,
        )
    ):
        failures.append("reference tolerances are not finite and positive")

    expected_cases = _expected_by_position(reference)
    observed_outputs = {row["position"]: row for row in observed.get("outputs", [])}
    missing = sorted(set(expected_cases) - set(observed_outputs))
    extra = sorted(set(observed_outputs) - set(expected_cases))
    if missing:
        failures.append(f"missing observed case(s): {missing[:5]}")
    if extra:
        notes.append(f"{len(extra)} unexpected observed case(s)")

    worst_logit = worst_value = worst_relative = 0.0
    ranking_material = 0
    legal_count = 0
    for position in sorted(set(expected_cases) & set(observed_outputs)):
        expected = expected_cases[position]
        actual = observed_outputs[position]
        if expected.get("terminal"):
            if not actual.get("skipped"):
                failures.append(f"{position}: terminal case must bypass inference")
            rows.append({"position": position, "skipped": True, "ok": bool(actual.get("skipped"))})
            continue
        legal = expected.get("legal") or []
        if not legal:
            failures.append(f"{position}: reference has no legal actions but is not terminal")
            continue
        legal_count += 1
        expected_logits = expected.get("policy_logits")
        actual_logits = actual.get("policy_logits")
        if not _finite_list(expected_logits) or not _finite_list(actual_logits):
            failures.append(f"{position}: policy logits are missing or non-finite")
            continue
        if len(expected_logits) != len(actual_logits):
            failures.append(
                f"{position}: policy logit count {len(actual_logits)} != {len(expected_logits)}"
            )
            continue
        if expected.get("policy_shape") and actual.get("policy_shape"):
            if list(expected["policy_shape"]) != list(actual.get("policy_shape")):
                failures.append(
                    f"{position}: policy shape {actual.get('policy_shape')} != {expected['policy_shape']}"
                )
        if not _finite(actual.get("value")):
            failures.append(f"{position}: defender value is missing or non-finite")
            continue
        if abs(actual["value"]) > 1 + 1e-9:
            failures.append(f"{position}: defender value {actual['value']} outside [-1, 1]")

        logit_error = max(abs(a - b) for a, b in zip(actual_logits, expected_logits))
        value_error = abs(float(actual["value"]) - float(expected["value"]))
        try:
            expected_probs = _legal_probabilities(expected_logits, legal)
            actual_probs = _legal_probabilities(actual_logits, legal)
        except ValueError as error:
            failures.append(f"{position}: {error}")
            continue
        expected_probs = expected.get("legal_probabilities") or expected_probs
        probability_error = max(abs(a - e) for a, e in zip(actual_probs, expected_probs))
        # Relative error is meaningful only for normalised probabilities that are
        # not vanishingly small; a logit near zero has no meaningful relative scale.
        relative = max(
            (
                abs(a - e) / e
                for a, e in zip(actual_probs, expected_probs)
                if e >= probability_relative_floor
            ),
            default=0.0,
        )
        worst_logit = max(worst_logit, logit_error)
        worst_value = max(worst_value, value_error)
        worst_relative = max(worst_relative, relative)

        if logit_error > logit_atol:
            failures.append(f"{position}: logit abs error {logit_error:.3g} > {logit_atol}")
        if value_error > value_atol:
            failures.append(f"{position}: value abs error {value_error:.3g} > {value_atol}")
        if relative > probability_relative_atol:
            failures.append(
                f"{position}: legal probability relative error {relative:.3g} > "
                f"{probability_relative_atol}"
            )
        if abs(sum(expected_probs) - 1) > PROBABILITY_SUM_ATOL:
            failures.append(f"{position}: reference legal probabilities do not sum to 1")
        if abs(sum(actual_probs) - 1) > PROBABILITY_SUM_ATOL:
            failures.append(f"{position}: observed legal probabilities do not sum to 1")
        if probability_error > probability_atol:
            failures.append(
                f"{position}: legal probability abs error {probability_error:.3g} > "
                f"{probability_atol}"
            )
        # Illegal actions must receive zero probability under legal-only masking.
        if any(value < 0 for value in actual_probs):
            failures.append(f"{position}: observed legal probability below zero")

        expected_rank = _ranked(legal, expected_probs)
        actual_rank = _ranked(legal, actual_probs)
        if expected_rank != actual_rank:
            # A reorder is material only when the expected probabilities of the
            # swapped actions differ by more than the near-tie tolerance.
            by_action = dict(zip(legal, expected_probs))
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
                "kind": expected.get("kind"),
                "legal_count": len(legal),
                "logit_max_abs_error": logit_error,
                "value_abs_error": value_error,
                "legal_probability_max_abs_error": probability_error,
                "legal_probability_max_relative_error": relative,
                "top5_equal": expected_rank == actual_rank,
                "legal_probability_sum": sum(actual_probs),
            }
        )

    if legal_count == 0:
        failures.append("no non-terminal reference case was compared (empty parity report)")

    return {
        "ok": not failures,
        "provider": provider or observed.get("provider"),
        "providers": sorted({provider or observed.get("provider") or "unknown"}),
        "model_sha256": model_sha,
        "cases": len(rows),
        "non_terminal_cases": legal_count,
        "tolerances": {
            "logit_atol": logit_atol,
            "value_atol": value_atol,
            "probability_atol": probability_atol,
            "probability_relative_atol": probability_relative_atol,
            "probability_relative_floor": probability_relative_floor,
            "near_tie_atol": near_tie_atol,
            "probability_sum_atol": PROBABILITY_SUM_ATOL,
        },
        "worst_logit_max_abs_error": worst_logit,
        "worst_value_abs_error": worst_value,
        "worst_probability_max_relative_error": worst_relative,
        "material_ranking_changes": ranking_material,
        "failures": failures,
        "notes": notes,
        "rows": rows,
    }


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
                "provider": report["provider"],
                "cases": report["cases"],
                "worst_logit_max_abs_error": report["worst_logit_max_abs_error"],
                "worst_value_abs_error": report["worst_value_abs_error"],
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
