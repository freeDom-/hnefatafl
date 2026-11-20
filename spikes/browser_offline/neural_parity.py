"""Canonical numerical and response-contract comparison for neural inference.

The reference file is produced by ``onnx_export.py`` from native PyTorch outputs.
The observed response comes from a provider (native ONNX Runtime, browser WASM or
browser WebGPU) and must include the complete inference response:

* model content identity and ruleset identity;
* a request/position identity for every result;
* policy and value tensor shapes and element types;
* actual policy logits and defender value;
* actual legal-action indices and their order;
* actual legal-only normalised probabilities;
* batch count, result count and per-result index where applicable.

Missing mandatory response fields fail validation. The harness does not
manufacture them from the reference: harness-generated terminal skips are marked
``harness_generated`` and are the only non-worker response accepted.

Deterministic batch order is verified independently of the reference: a batched
response must have ``batch_count == results_count == len(outputs)``, each output's
returned ``index`` must equal its array position, request identities must be
unique and must match the requested reference positions in order.

All numerical checks fail closed; near-tied rankings may reorder within a
justified tolerance and are reported as notes.
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
HEX64 = set("0123456789abcdef")
TOLERANCE_DEFAULTS = {
    "logit_atol": 1e-3,
    "value_atol": 1e-4,
    "probability_atol": 1e-5,
    "probability_relative_atol": 1e-3,
    "probability_relative_floor": 1e-4,
    "near_tie_atol": 1e-4,
    "postprocess_atol": POSTPROCESS_ATOL,
}
RESPONSE_REQUIRED_FIELDS = (
    "request_id",
    "position_digest",
    "policy_logits",
    "value",
    "legal",
    "legal_probabilities",
    "policy_shape",
    "value_shape",
    "policy_dtype",
    "value_dtype",
)


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _finite_list(values: Any, *, expected: int | None = None) -> bool:
    return (
        isinstance(values, list)
        and all(_finite(item) for item in values)
        and (expected is None or len(values) == expected)
    )


def _hex64(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= HEX64


def stable_softmax(values: list[float]) -> list[float]:
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
    if not _hex64(reference.get("model_sha256")):
        failures.append("reference: missing or malformed model identity")
    if reference.get("ruleset") not in (None, "brandubh", "tablut", "copenhagen"):
        failures.append("reference: unsupported ruleset identity")
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
        if not _hex64(case.get("position_digest")):
            failures.append(f"{position}: reference position digest is missing or malformed")
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
        for field in ("policy_shape", "value_shape", "policy_dtype", "value_dtype"):
            if case.get(field) is None:
                failures.append(f"{position}: reference missing {field}")
    return tolerances


def _result(
    failures: list[str],
    notes: list[str],
    rows: list[dict[str, Any]],
    *,
    provider: str | None,
    model_sha256: Any,
    model_ruleset: Any,
    tolerances: dict[str, float] | None,
    batch_count: Any = None,
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
        "model_ruleset": model_ruleset,
        "batch_count": batch_count,
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
    """Compare a complete observed inference response against the reference."""
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
            model_ruleset=None,
            tolerances=None,
        )

    tolerances = _validate_reference(reference, failures)
    model_sha = reference.get("model_sha256")
    ruleset = reference.get("ruleset")
    if not isinstance(observed.get("outputs"), list):
        failures.append("observed: missing outputs array")
        observed_outputs: list[dict[str, Any]] = []
    else:
        observed_outputs = observed["outputs"]

    if observed.get("model_sha256") != model_sha:
        failures.append(
            f"observed model identity {observed.get('model_sha256')!r} != {model_sha!r}"
        )
    if ruleset is not None and observed.get("model_ruleset") != ruleset:
        failures.append(f"observed model ruleset {observed.get('model_ruleset')!r} != {ruleset!r}")

    expected = _index_by_position(reference.get("cases", []), side="reference", failures=failures)
    actual = _index_by_position(observed_outputs, side="observed", failures=failures)
    expected_order = [
        case["position"] for case in reference.get("cases", []) if not case.get("terminal")
    ]
    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    if missing:
        failures.append(f"missing observed case(s): {missing[:5]}")
    if extra:
        failures.append(f"unexpected observed case(s): {extra[:5]}")

    batch_count = observed.get("batch_count")
    results_count = observed.get("results_count")
    batched = batch_count is not None or results_count is not None
    if batched:
        # Verify the batch contract independently of the reference values.
        if not isinstance(batch_count, int) or batch_count < 1:
            failures.append("observed: missing or invalid batch_count")
        if not isinstance(results_count, int) or results_count < 1:
            failures.append("observed: missing or invalid results_count")
        if isinstance(batch_count, int) and batch_count != len(expected_order):
            failures.append(
                f"observed batch_count {batch_count} != {len(expected_order)} requested positions"
            )
        if isinstance(results_count, int) and results_count != len(observed_outputs):
            failures.append(
                f"observed results_count {results_count} != {len(observed_outputs)} outputs"
            )
        if isinstance(batch_count, int) and batch_count != len(observed_outputs):
            failures.append(
                f"observed batch_count {batch_count} != {len(observed_outputs)} outputs"
            )
        seen_ids: set[str] = set()
        for position in range(len(observed_outputs)):
            entry = observed_outputs[position]
            if not isinstance(entry, dict):
                continue
            index = entry.get("index")
            if index is None:
                failures.append(f"observed output {position}: missing result index")
            elif index != position:
                failures.append(
                    f"observed output {position}: result index {index!r} != position {position}"
                )
            request_id = entry.get("request_id")
            if not isinstance(request_id, str) or not request_id:
                failures.append(f"observed output {position}: missing request_id")
            elif request_id in seen_ids:
                failures.append(f"observed output {position}: duplicate request_id {request_id!r}")
            else:
                seen_ids.add(request_id)
            if position < len(expected_order) and request_id != expected_order[position]:
                failures.append(
                    f"observed output {position}: request_id {request_id!r} is not associated "
                    f"with requested position {expected_order[position]!r}"
                )
            if position < len(expected_order):
                expected_digest = expected[expected_order[position]].get("position_digest")
                if entry.get("position_digest") != expected_digest:
                    failures.append(
                        f"observed output {position}: position digest does not match "
                        f"{expected_order[position]!r}"
                    )

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
            elif actual_case.get("harness_generated") is not True:
                failures.append(f"{position}: terminal skip must be marked harness_generated")
            rows.append({"position": position, "skipped": True})
            continue
        for field in RESPONSE_REQUIRED_FIELDS:
            if field not in actual_case or actual_case[field] is None:
                failures.append(f"{position}: response missing mandatory field {field!r}")

        per_case_model = actual_case.get("model_sha256")
        if per_case_model is not None and per_case_model != model_sha:
            failures.append(f"{position}: per-case model identity mismatch")
        elif per_case_model is None and not batched:
            failures.append(f"{position}: response missing per-result model identity")

        if actual_case.get("position_digest") != expected_case.get("position_digest"):
            failures.append(f"{position}: position identity mismatch")
        if not batched and actual_case.get("request_id") != position:
            failures.append(
                f"{position}: request_id {actual_case.get('request_id')!r} is not associated "
                f"with this position"
            )

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
        if len(observed_logits) != len(expected_logits):
            failures.append(
                f"{position}: policy logit count {len(observed_logits)} != {len(expected_logits)}"
            )
            continue
        if any(not (0 <= index < len(observed_logits)) for index in legal_actual):
            failures.append(f"{position}: observed legal index out of range")
            continue

        expected_policy_shape = expected_case.get("policy_shape")
        expected_value_shape = expected_case.get("value_shape")
        if isinstance(batch_count, int) and batch_count > 0:
            if isinstance(expected_policy_shape, list) and len(expected_policy_shape) == 2:
                expected_policy_shape = [batch_count, expected_policy_shape[1]]
            if expected_value_shape == [1]:
                expected_value_shape = [batch_count]
        if actual_case.get("policy_shape") != expected_policy_shape:
            failures.append(
                f"{position}: policy shape {actual_case.get('policy_shape')} != "
                f"{expected_policy_shape}"
            )
        if actual_case.get("value_shape") != expected_value_shape:
            failures.append(
                f"{position}: value shape {actual_case.get('value_shape')} != "
                f"{expected_value_shape}"
            )
        for field in ("policy_dtype", "value_dtype"):
            if actual_case.get(field) != EXPECTED_DTYPE:
                failures.append(
                    f"{position}: {field} {actual_case.get(field)!r} != {EXPECTED_DTYPE}"
                )

        if not _finite(actual_case.get("value")):
            failures.append(f"{position}: observed defender value is missing or non-finite")
            continue
        if abs(actual_case["value"]) > 1 + 1e-9:
            failures.append(f"{position}: observed value {actual_case['value']} outside [-1, 1]")

        expected_probs = expected_case.get("legal_probabilities")
        if not _finite_list(expected_probs):
            expected_probs = _legal_probabilities(expected_logits, legal_expected)

        if not isinstance(actual_case.get("legal_probabilities"), list):
            failures.append(f"{position}: observed legal probabilities are missing")
            continue
        if not _finite_list(actual_case["legal_probabilities"]) or len(
            actual_case["legal_probabilities"]
        ) != len(legal_actual):
            failures.append(f"{position}: observed legal probabilities are malformed")
            continue
        if any(value < -PROBABILITY_NEGATIVE_TOL for value in actual_case["legal_probabilities"]):
            failures.append(f"{position}: observed legal probability is negative")
        if abs(sum(actual_case["legal_probabilities"]) - 1) > PROBABILITY_SUM_ATOL:
            failures.append(f"{position}: observed legal probabilities do not sum to 1")

        try:
            recomputed = _legal_probabilities(observed_logits, legal_actual)
        except ValueError as error:
            failures.append(f"{position}: {error}")
            continue

        logit_error = max(abs(a - b) for a, b in zip(observed_logits, expected_logits))
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
        actual_rank = _ranked(legal_actual, actual_case["legal_probabilities"])
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

        non_terminal += 1
        rows.append(
            {
                "position": position,
                "kind": expected_case.get("kind"),
                "request_id": actual_case.get("request_id"),
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
        model_ruleset=ruleset,
        tolerances=tolerances,
        batch_count=batch_count,
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
                "model_ruleset": report.get("model_ruleset"),
                "batch_count": report.get("batch_count"),
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
