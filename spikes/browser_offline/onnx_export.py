"""Export a validated checkpoint to inference-only ONNX with metadata and parity.

This is experimental tooling outside the production application. It reads the
existing tensor-only checkpoint loader, writes an inference-only ONNX graph plus
an architecture metadata sidecar, and reports native PyTorch/ONNX Runtime CPU
parity. It never mutates the source checkpoint, never includes optimizer/replay/
training state, and never ships a runtime dependency into the application.

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
import sys
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

# FP32 parity tolerances. These are set from the observed native PyTorch/ORT
# error and kept loose enough to accommodate different fused kernels while still
# catching a materially wrong graph. They are reported, not hidden.
LOGIT_ATOL = 1e-3
VALUE_ATOL = 1e-4
RELATIVE_ATOL = 1e-4


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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


def export(source: Path, output: Path, opset: int, force: bool) -> dict:
    import torch

    if not source.is_file():
        raise SystemExit(f"Checkpoint not found: {source}")
    target = output / "model.onnx"
    if target.exists() and not force:
        raise SystemExit(f"Refusing to replace existing export: {target}")
    output.mkdir(parents=True, exist_ok=True)
    model, payload = load_checkpoint(source, device="cpu")
    model.eval()
    example_state = next(
        State.from_dict(case["state"])
        for case in jarl_positions()
        if case["ruleset"] == model.ruleset and case["kind"] != "terminal"
    )
    example = encode_state(example_state).unsqueeze(0)
    started = time.monotonic()
    torch.onnx.export(
        model,
        (example,),
        target,
        input_names=["planes"],
        output_names=["policy_logits", "defender_value"],
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
    # Re-save as one self-contained file. The dynamo exporter can emit external
    # tensor data, which ORT Web cannot load from a single ArrayBuffer.
    onnx.save_model(graph, str(target), save_as_external_data=False)
    for stale in output.glob("*.onnx.data"):
        stale.unlink()
    graph = onnx.load(str(target))
    onnx.checker.check_model(graph)
    inferred = onnx.shape_inference.infer_shapes(graph)
    dims = {
        value.name: [dim.dim_value or dim.dim_param for dim in value.type.tensor_type.shape.dim]
        for value in list(inferred.graph.input) + list(inferred.graph.output)
    }
    n = model.size
    sidecar = {
        "schema": 1,
        "artifact": "hnefatafl-policy-value-onnx",
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
            "policy_logits": {"name": "policy_logits", "shape": ["batch", n**4]},
            "defender_value": {"name": "defender_value", "shape": ["batch"]},
        },
        "action_index": "source*size**2+target",
        "value_perspective": "defenders",
        "legal_policy_masking": "softmax over legal action indices only",
        "graph_input_output_dims": dims,
        "source": {
            "name": source.name,
            "sha256": digest(source),
            "bytes": source.stat().st_size,
        },
        "onnx": {
            "sha256": digest(target),
            "bytes": target.stat().st_size,
            "opset": opset,
            "checker": "passed",
            "shape_inference": "passed",
        },
        "exporter": {
            "torch": package_version("torch"),
            "onnx": package_version("onnx"),
            "onnxscript": package_version("onnxscript"),
            "onnxruntime": package_version("onnxruntime"),
            "export_seconds": export_seconds,
        },
        "generated": datetime.now(timezone.utc).isoformat(),
    }
    (output / "model.json").write_text(json.dumps(sidecar, indent=2) + "\n")
    return {"sidecar": sidecar, "model": model, "target": target}


def parity(model, target: Path) -> dict:
    import numpy as np
    import onnxruntime as ort
    import torch

    session = ort.InferenceSession(
        str(target),
        providers=["CPUExecutionProvider"],
        sess_options=_single_thread_options(),
    )
    input_name = session.get_inputs()[0].name
    rows = []
    worst_logit = worst_value = 0.0
    worst_relative = 0.0
    for case in jarl_positions():
        if case["ruleset"] != model.ruleset:
            continue
        state = State.from_dict(case["state"])
        moves = legal_moves(state)
        if not moves:
            continue
        encoded = encode_state(state).unsqueeze(0)
        with torch.inference_mode():
            torch_logits, torch_value = model(encoded)
        outputs = session.run(None, {input_name: encoded.numpy()})
        ort_logits, ort_value = outputs[0], outputs[1]
        logit_error = float(np.max(np.abs(torch_logits.numpy() - ort_logits)))
        value_error = float(abs(torch_value.item() - float(np.asarray(ort_value).reshape(-1)[0])))
        worst_logit = max(worst_logit, logit_error)
        worst_value = max(worst_value, value_error)
        ids = [m.source * state.size**2 + m.target for m in moves]
        torch_legal = torch.softmax(torch_logits[0, ids], dim=0).numpy()
        ort_legal = np.asarray(np.exp(ort_logits[0, ids]) / np.exp(ort_logits[0, ids]).sum())
        relative = float(
            np.max(np.abs(torch_legal - ort_legal) / np.maximum(np.abs(torch_legal), 1e-12))
        )
        worst_relative = max(worst_relative, relative)
        torch_rank = [moves[i].to_dict() for i in np.argsort(-torch_legal)[:5]]
        ort_rank = [moves[i].to_dict() for i in np.argsort(-ort_legal)[:5]]
        legal_set = set(ids)
        illegal = float(
            sum(
                math.exp(float(ort_logits[0, index]))
                for index in range(ort_logits.shape[1])
                if index not in legal_set
            )
            / (
                sum(math.exp(float(ort_logits[0, index])) for index in range(ort_logits.shape[1]))
                + 1e-30
            )
        )
        rows.append(
            {
                "position": case["name"],
                "kind": case["kind"],
                "logit_max_abs_error": logit_error,
                "value_abs_error": value_error,
                "legal_policy_relative_error": relative,
                "top5_move_agreement": torch_rank == ort_rank,
                "illegal_probability_raw_softmax": illegal,
                "legal_count": len(moves),
            }
        )
    return {
        "tolerances": {
            "logit_atol": LOGIT_ATOL,
            "value_atol": VALUE_ATOL,
            "relative_atol": RELATIVE_ATOL,
        },
        "cases": len(rows),
        "worst_logit_max_abs_error": worst_logit,
        "worst_value_abs_error": worst_value,
        "worst_legal_policy_relative_error": worst_relative,
        "top5_all_agree": all(row["top5_move_agreement"] for row in rows),
        "passes": worst_logit <= LOGIT_ATOL
        and worst_value <= VALUE_ATOL
        and worst_relative <= RELATIVE_ATOL,
        "rows": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkpoint", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--opset", type=int, default=18)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--no-parity", action="store_true")
    args = parser.parse_args()
    exported = export(args.checkpoint, args.output, args.opset, args.force)
    report = {"sidecar": exported["sidecar"]}
    if not args.no_parity:
        report["parity"] = parity(exported["model"], exported["target"])
        (args.output / "parity.json").write_text(json.dumps(report["parity"], indent=2) + "\n")
    (args.output / "export.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
