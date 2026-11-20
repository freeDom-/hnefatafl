"""Export a validated checkpoint to inference-only ONNX with metadata and parity.

Experimental tooling outside the production application. It reads the existing
tensor-only checkpoint loader, writes an inference-only ONNX graph, an
architecture metadata sidecar and a native reference-vector file, and reports
PyTorch/ONNX Runtime CPU parity. It never mutates the source checkpoint, never
includes optimizer/replay/training state, and never ships a runtime dependency
into the application.

The export is *transactional and fail-closed*: artifacts are produced in a
temporary staging directory and only moved into the requested output directory
after the graph validates and (unless ``--no-parity``) numerical parity passes.
A ``VALIDATED.json`` marker records the accepted digest; ``prepare_neural.py``
refuses to stage a model without a passing marker unless explicitly allowed.

Exact command for the project owner's own trained models:

    .venv/bin/python spikes/browser_offline/onnx_export.py \
        /path/to/last.pt /tmp/my-model-export

Requires the optional pinned exporter environment documented in
``spikes/browser_offline/requirements-onnx.txt``.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata as metadata
import json
import math
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

SPIKE = Path(__file__).resolve().parent
ROOT = SPIKE.parents[1]
for path in (str(SPIKE), str(ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

from fixtures import jarl_positions  # noqa: E402
from hnefatafl.engine import State, legal_moves  # noqa: E402
from hnefatafl.models import encode_state, load_checkpoint  # noqa: E402

# FP32 parity tolerances. Chosen from the observed native PyTorch/ORT error and
# kept loose enough to accommodate different fused kernels while still catching
# a materially wrong graph. They are recorded in the artifact and never relaxed
# automatically for a browser provider.
# Logit parity is judged absolutely: a relative error on a logit near zero is
# meaningless, while a 1e-3 absolute shift is far below any move-ranking effect.
LOGIT_ATOL = 1e-3
VALUE_ATOL = 1e-4
# Relative error is judged on the normalised legal probabilities, where it is
# meaningful, and only for actions whose expected probability is above a floor.
PROBABILITY_ATOL = 1e-5
PROBABILITY_RELATIVE_ATOL = 1e-3
PROBABILITY_RELATIVE_FLOOR = 1e-4
# A ranking difference is only material when the expected probabilities differ
# by more than this; near-tied moves may reorder under floating point noise.
NEAR_TIE_ATOL = 1e-4
REQUIRED_KINDS = ("opening", "midgame", "endgame", "tactical", "repetition", "terminal")
OUTPUT_NAMES = ("policy_logits", "defender_value")
SIDECAR_ARTIFACT = "hnefatafl-policy-value-onnx"
SIDECAR_SCHEMA = 1
# Guardrails for the experimental environment.
MAX_MODEL_BYTES = 64 * 1024 * 1024
MAX_REFERENCE_BYTES = 16 * 1024 * 1024


class ExportError(RuntimeError):
    """A failed or unsafe export; always reported with a non-zero exit code."""


def digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def digest(path: Path) -> str:
    return digest_bytes(path.read_bytes())


def package_version(name: str) -> str | None:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def _single_thread_options():
    """Keep native parity deterministic and avoid container CPU-affinity noise."""
    import onnxruntime as ort

    options = ort.SessionOptions()
    options.intra_op_num_threads = 1
    options.inter_op_num_threads = 1
    return options


def _safe_output_path(output: Path) -> Path:
    """Refuse output inside the repository outside ``work/`` and refuse root."""
    resolved = output.resolve()
    if resolved == ROOT or resolved == Path(resolved.anchor):
        raise SystemExit("Refusing to export into the repository root or filesystem root")
    if ROOT in resolved.parents and not resolved.is_relative_to(ROOT / "work"):
        raise SystemExit("Stage exports inside work/ or outside the repository")
    return resolved


def _stable_softmax(values: list[float]) -> list[float]:
    """Numerically stable softmax; non-finite logits are a hard error."""
    if not values:
        return []
    if any(not math.isfinite(value) for value in values):
        raise ValueError("Legal logits contain a non-finite value")
    maximum = max(values)
    exponentials = [math.exp(value - maximum) for value in values]
    total = sum(exponentials)
    if not math.isfinite(total) or total <= 0:
        raise ValueError("Legal softmax normaliser is not finite and positive")
    return [item / total for item in exponentials]


def legal_logits(logits: list[float], ids: list[int]) -> list[float]:
    """Extract the legal logits and validate their indices in range."""
    size_total = len(logits)
    selected = []
    for index in ids:
        if not isinstance(index, int) or not 0 <= index < size_total:
            raise ValueError(f"Legal action index {index} out of range [0, {size_total})")
        value = float(logits[index])
        if not math.isfinite(value):
            raise ValueError("Policy logits contain a non-finite value")
        selected.append(value)
    return selected


def illegal_mass(logits: list[float], legal: list[int]) -> float:
    """Raw illegal-action mass *before* masking (diagnostic only).

    A legal-only softmax assigns exactly zero to every illegal index by
    construction, so reporting a masked value of zero everywhere would be
    vacuous. This raw diagnostic measures how much probability a naive global
    softmax would have placed on illegal actions.
    """
    if not logits:
        return 0.0
    maximum = max(logits)
    exponentials = [math.exp(value - maximum) for value in logits]
    total = sum(exponentials)
    if not math.isfinite(total) or total <= 0:
        raise ValueError("Global softmax normaliser is not finite and positive")
    legal_set = set(legal)
    return sum(item for index, item in enumerate(exponentials) if index not in legal_set) / total


def _rank_top(ids: list[int], probabilities: list[float], k: int = 5):
    order = sorted(range(len(ids)), key=lambda position: -probabilities[position])
    return [(ids[position], probabilities[position]) for position in order[:k]]


def _finite_vector(values) -> bool:
    return all(isinstance(value, (int, float)) and math.isfinite(value) for value in values)


def export_graph(source: Path, staging: Path, opset: int) -> dict:
    """Export the ONNX graph into ``staging`` and return the graph metadata."""
    import torch

    model, payload = load_checkpoint(source, device="cpu")
    model.eval()
    example_state = next(
        State.from_dict(case["state"])
        for case in jarl_positions()
        if case["ruleset"] == model.ruleset and case["kind"] != "terminal"
    )
    example = encode_state(example_state).unsqueeze(0)
    target = staging / "model.onnx"
    started = time.monotonic()
    torch.onnx.export(
        model,
        (example,),
        target,
        input_names=["planes"],
        output_names=list(OUTPUT_NAMES),
        opset_version=opset,
        dynamo=True,
        # The inference runtime must accept batch sizes 1..32 for MCTS leaf
        # batching. Exporting a static batch dimension would silently reject
        # every batched request.
        dynamic_shapes=({0: torch.export.Dim("batch")},),
    )
    export_seconds = time.monotonic() - started

    import onnx

    graph = onnx.load(str(target))
    # Re-save as one self-contained file: ORT Web cannot load external tensor
    # data from a single ArrayBuffer. Only our own generated sidecar is removed.
    onnx.save_model(graph, str(target), save_as_external_data=False)
    external = staging / "model.onnx.data"
    if external.exists():
        external.unlink()
    graph = onnx.load(str(target))
    onnx.checker.check_model(graph)
    inferred = onnx.shape_inference.infer_shapes(graph)
    dims = {
        value.name: [dim.dim_value or dim.dim_param for dim in value.type.tensor_type.shape.dim]
        for value in list(inferred.graph.input) + list(inferred.graph.output)
    }
    n = model.size
    if target.stat().st_size > MAX_MODEL_BYTES:
        raise ExportError("Exported ONNX graph exceeds the experimental size limit")
    sidecar = {
        "schema": SIDECAR_SCHEMA,
        "artifact": SIDECAR_ARTIFACT,
        "ruleset": model.ruleset,
        "architecture": payload["architecture"],
        "model_config": dict(model.config),
        "input": {
            "name": "planes",
            "shape": ["batch", 6, n, n],
            "dtype": "float32",
            "encoding": "six-plane-v1",
            "dynamic_batch": True,
        },
        "outputs": {
            "policy_logits": {
                "name": "policy_logits",
                "shape": ["batch", n**4],
                "dtype": "float32",
            },
            "defender_value": {"name": "defender_value", "shape": ["batch"], "dtype": "float32"},
        },
        "action_index": "source*size**2+target",
        "value_perspective": "defenders",
        "legal_policy_masking": "softmax over legal action indices only",
        "graph_input_output_dims": dims,
        "source": {"name": source.name, "sha256": digest(source), "bytes": source.stat().st_size},
        "onnx": {"sha256": digest(target), "bytes": target.stat().st_size, "opset": opset},
        "exporter": {
            "torch": package_version("torch"),
            "onnx": package_version("onnx"),
            "onnxscript": package_version("onnxscript"),
            "onnxruntime": package_version("onnxruntime"),
            "export_seconds": export_seconds,
        },
        "generated": datetime.now(timezone.utc).isoformat(),
    }
    return {"sidecar": sidecar, "model": model, "target": target}


def _generated_positions(ruleset: str, *, minimum: int = 32) -> list[dict]:
    """Seeded random-play positions for distinct-position batch coverage."""
    import random

    from hnefatafl.engine import apply_move, new_game

    rng = random.Random(20261119)
    state = new_game(ruleset)
    generated: list[dict] = []
    attempts = 0
    while len(generated) < minimum and attempts < minimum * 40:
        attempts += 1
        moves = legal_moves(state)
        if not moves or state.winner:
            state = new_game(ruleset)
            continue
        state = apply_move(state, rng.choice(moves))
        if state.winner or not legal_moves(state):
            continue
        generated.append(
            {
                "name": f"{ruleset}-generated-{len(generated)}",
                "ruleset": ruleset,
                "kind": "generated",
                "state": state.to_dict(),
            }
        )
    return generated


def reference_vectors(model, target: Path) -> dict:
    """Compute native PyTorch reference vectors for every covered position.

    These are *actual computed outputs*, not browser-derived values. They are the
    authoritative reference a browser provider must reproduce.
    """
    import torch

    cases = []
    seen_kinds = set()
    selected = [case for case in jarl_positions() if case["ruleset"] == model.ruleset]
    # Add seeded distinct positions so batched inference (up to 32) exercises
    # genuinely different boards instead of wrapping one small fixed set.
    selected.extend(_generated_positions(model.ruleset, minimum=32))
    for case in selected:
        state = State.from_dict(case["state"])
        moves = legal_moves(state)
        encoded = encode_state(state).unsqueeze(0)
        ids = [move.source * state.size**2 + move.target for move in moves]
        terminal = bool(state.winner) or not moves
        entry = {
            "position": case["name"],
            "kind": case["kind"],
            "ruleset": model.ruleset,
            "state": case["state"],
            "position_digest": hashlib.sha256(
                json.dumps(case["state"], sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
            "planes": [float(value) for value in encoded.reshape(-1).tolist()],
            "legal": ids,
            "terminal": terminal,
            "input_shape": list(encoded.shape),
        }
        if not terminal:
            with torch.inference_mode():
                logits, value = model(encoded)
            logits_list = [float(item) for item in logits[0].tolist()]
            if not _finite_vector(logits_list):
                raise ExportError(f"Native logits for {case['name']} are not finite")
            legal_values = legal_logits(logits_list, ids)
            probabilities = _stable_softmax(legal_values)
            contained = float(value.reshape(-1)[0])
            if not math.isfinite(contained):
                raise ExportError(f"Native value for {case['name']} is not finite")
            entry.update(
                {
                    "policy_logits": logits_list,
                    "value": contained,
                    "legal_probabilities": probabilities,
                    "top5": [
                        {"action": int(action), "probability": float(prob)}
                        for action, prob in _rank_top(ids, probabilities)
                    ],
                    "illegal_mass_raw": illegal_mass(logits_list, ids),
                    "policy_shape": [1, len(logits_list)],
                    "value_shape": [1],
                    "policy_dtype": "float32",
                    "value_dtype": "float32",
                }
            )
            seen_kinds.add(case["kind"])
        else:
            entry.update(
                {
                    "policy_logits": None,
                    "value": 0.0,
                    "legal_probabilities": [],
                    "top5": [],
                    "policy_shape": [1, state.size**4],
                    "value_shape": [1],
                    "policy_dtype": "float32",
                    "value_dtype": "float32",
                    "inference_bypassed": True,
                }
            )
        cases.append(entry)

    missing = [kind for kind in REQUIRED_KINDS if kind not in seen_kinds and kind != "terminal"]
    if not cases:
        raise ExportError(f"No reference positions exist for ruleset {model.ruleset!r}")
    if len(seen_kinds) < 3:
        raise ExportError(
            f"Reference coverage too small: only {sorted(seen_kinds)} for {model.ruleset}"
        )
    return {
        "schema": 2,
        "artifact": SIDECAR_ARTIFACT,
        "ruleset": model.ruleset,
        "model_sha256": digest(target),
        "model_config": dict(model.config),
        "output_names": list(OUTPUT_NAMES),
        "action_index": "source*size**2+target",
        "value_perspective": "defenders",
        "tolerances": {
            "logit_atol": LOGIT_ATOL,
            "value_atol": VALUE_ATOL,
            "probability_atol": PROBABILITY_ATOL,
            "probability_relative_atol": PROBABILITY_RELATIVE_ATOL,
            "probability_relative_floor": PROBABILITY_RELATIVE_FLOOR,
            "near_tie_atol": NEAR_TIE_ATOL,
        },
        "covered_kinds": sorted(seen_kinds),
        "missing_kinds": missing,
        "cases": cases,
        "generated": datetime.now(timezone.utc).isoformat(),
    }


def native_onnx_outputs(model, target: Path, reference: dict) -> dict:
    """Run the exported graph through native ONNX Runtime CPU.

    Outputs are requested by their expected names, never by positional order.
    """
    import numpy as np
    import onnxruntime as ort

    session = ort.InferenceSession(
        str(target), providers=["CPUExecutionProvider"], sess_options=_single_thread_options()
    )
    input_name = session.get_inputs()[0].name
    if input_name != "planes":
        raise ExportError(f"Unexpected input name {input_name!r}, expected 'planes'")
    outputs = []
    for case in reference["cases"]:
        if case["terminal"]:
            outputs.append(
                {
                    "position": case["position"],
                    "skipped": True,
                    "harness_generated": True,
                }
            )
            continue
        planes = np.asarray(case["planes"], dtype=np.float32).reshape(case["input_shape"])
        result = session.run(list(OUTPUT_NAMES), {input_name: planes})
        policy, value = result
        if not np.isfinite(policy).all() or not np.isfinite(value).all():
            raise ExportError(f"ONNX Runtime produced a non-finite output for {case['position']}")
        logits = [float(item) for item in np.asarray(policy).reshape(-1)]
        legal = list(case.get("legal") or [])
        values = [logits[index] for index in legal]
        maximum = max(values)
        exponentials = [math.exp(item - maximum) for item in values]
        total = sum(exponentials)
        outputs.append(
            {
                "position": case["position"],
                "request_id": case["position"],
                "position_digest": case.get("position_digest"),
                "model_sha256": reference["model_sha256"],
                "policy_logits": logits,
                "value": float(np.asarray(value).reshape(-1)[0]),
                "policy_shape": list(policy.shape),
                "value_shape": list(value.shape),
                "policy_dtype": str(policy.dtype),
                "value_dtype": str(value.dtype),
                "legal": legal,
                "legal_probabilities": [item / total for item in exponentials],
            }
        )
    return {
        "schema": 4,
        "provider": "onnxruntime-cpu",
        "model_sha256": reference["model_sha256"],
        "model_ruleset": reference.get("ruleset"),
        "outputs": outputs,
    }


def parity(model, target: Path, reference: dict) -> dict:
    """Native PyTorch vs native ONNX Runtime parity (via the canonical checker)."""
    import neural_parity

    native = native_onnx_outputs(model, target, reference)
    return neural_parity.compare(reference, native, provider="onnxruntime-cpu")


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2) + "\n")


def _write_compact_json(path: Path, payload: dict) -> None:
    """Compact JSON for large reference vectors.

    Indented serialization of a Copenhagen reference set exceeds the 16 MiB
    experimental budget (about 17 MB), while compact tokens preserve the exact
    float values and fit comfortably. Digests cover the written bytes.
    """
    path.write_text(json.dumps(payload, separators=(",", ":"), allow_nan=False) + "\n")


def run(args) -> int:
    source = args.checkpoint.resolve()
    if not source.is_file():
        raise SystemExit(f"Checkpoint not found: {source}")
    output = _safe_output_path(args.output)
    if output.exists() and args.force:
        # Invalidate acceptance before attempting a replacement so a failure at
        # any later step cannot leave an old marker accepting new/partial bytes.
        for stale in ("VALIDATED.json", "parity.json", "export.json"):
            (output / stale).unlink(missing_ok=True)
    if output.exists():
        existing = [item for item in output.iterdir() if item.name not in {".keep"}]
        if existing and not args.force:
            raise SystemExit(
                f"Refusing to replace a non-empty output directory: {output} "
                "(pass --force to replace only this tool's artifacts)"
            )
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.", dir=output.parent))
    try:
        exported = export_graph(source, staging, args.opset)
        sidecar = exported["sidecar"]
        reference = reference_vectors(exported["model"], exported["target"])
        if len(reference["cases"]) == 0:
            raise ExportError("Parity reference set is empty")
        reference["model_sha256"] = sidecar["onnx"]["sha256"]
        # The recorded reference digest must cover the exact bytes written to
        # disk, not a differently-formatted serialization.
        _write_compact_json(staging / "reference.json", reference)
        if (staging / "reference.json").stat().st_size > MAX_REFERENCE_BYTES:
            raise ExportError("Reference vector file exceeds the experimental size limit")
        reference_digest = digest(staging / "reference.json")
        sidecar["reference"] = {
            "sha256": reference_digest,
            "cases": len(reference["cases"]),
            "covered_kinds": reference["covered_kinds"],
            "missing_kinds": reference["missing_kinds"],
        }
        _write_json(staging / "model.json", sidecar)
        sidecar_digest = digest(staging / "model.json")

        manifest = {
            "schema": 3,
            "validated": False,
            "artifact": SIDECAR_ARTIFACT,
            "model_sha256": sidecar["onnx"]["sha256"],
            "model_bytes": sidecar["onnx"]["bytes"],
            "ruleset": sidecar["ruleset"],
            "reference_sha256": sidecar["reference"]["sha256"],
            "sidecar_sha256": sidecar_digest,
            "generated": datetime.now(timezone.utc).isoformat(),
        }
        if args.no_parity:
            manifest["reason"] = "exported with --no-parity (unvalidated)"
            _write_json(staging / "export-manifest.json", manifest)
        else:
            report = parity(exported["model"], exported["target"], reference)
            _write_json(staging / "parity.json", report)
            manifest["parity_sha256"] = digest(staging / "parity.json")
            manifest["parity_passed"] = bool(report["ok"])
            manifest["parity_providers"] = report["providers"]
            manifest["validated"] = bool(report["ok"])
            if not report["ok"]:
                manifest["reason"] = "native parity failed"
            _write_json(staging / "export-manifest.json", manifest)
            if not report["ok"]:
                _write_json(
                    staging / "export.json",
                    {"sidecar": sidecar, "parity": report, "ok": False},
                )
                raise ExportError(
                    "Native parity failed; artifacts were NOT published. "
                    f"Failures: {report['failures'][:3]}"
                )

        # Coherent commit protocol: remove any previous acceptance marker FIRST,
        # then publish the new artifact set and write the marker LAST. A crash
        # mid-publication therefore leaves an unvalidated directory, never an old
        # marker blessing new bytes.
        output.mkdir(parents=True, exist_ok=True)
        for stale in ("VALIDATED.json", "parity.json", "export.json"):
            (output / stale).unlink(missing_ok=True)
        for name in ("model.onnx", "model.json", "reference.json", "export-manifest.json"):
            os.replace(staging / name, output / name)
        if (staging / "parity.json").exists():
            os.replace(staging / "parity.json", output / "parity.json")
        if manifest["validated"]:
            _write_json(
                output / "VALIDATED.json",
                {
                    "validated": True,
                    "model_sha256": manifest["model_sha256"],
                    "reference_sha256": manifest["reference_sha256"],
                    "sidecar_sha256": manifest["sidecar_sha256"],
                    "parity_sha256": manifest.get("parity_sha256"),
                    "ruleset": manifest["ruleset"],
                    "generated": manifest["generated"],
                },
            )
        print(
            json.dumps(
                {
                    "output": str(output),
                    "validated": manifest["validated"],
                    "model_sha256": manifest["model_sha256"],
                    "model_bytes": manifest["model_bytes"],
                    "reference_cases": len(reference["cases"]),
                    "covered_kinds": reference["covered_kinds"],
                },
                indent=2,
            )
        )
        return 0 if manifest["validated"] else 1
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkpoint", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--opset", type=int, default=18)
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--no-parity",
        action="store_true",
        help="Export without native parity; the artifact is labeled unvalidated",
    )
    args = parser.parse_args()
    try:
        raise SystemExit(run(args))
    except ExportError as error:
        print(f"export failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
