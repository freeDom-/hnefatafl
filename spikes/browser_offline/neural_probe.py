"""Read-only source weights, bounded native checks, outputs only in /tmp."""

from pathlib import Path
import importlib.metadata as metadata
import importlib.util
import hashlib
import json
import time
import warnings
import sys
import torch
from hnefatafl.engine import legal_moves, new_game
from hnefatafl.models import load_checkpoint, encode_state

torch.set_num_threads(1)
if len(sys.argv) != 3:
    raise SystemExit("Usage: neural_probe.py CHECKPOINT DISPOSABLE_OUTPUT_DIRECTORY")
source = Path(sys.argv[1]).resolve()
output = Path(sys.argv[2]).resolve()
output.mkdir(parents=True, exist_ok=True)
if source.is_relative_to(output):
    raise SystemExit("Keep source weights outside the disposable output directory")
model, payload = load_checkpoint(source, device="cpu")
state = new_game(model.ruleset)
encoded = encode_state(state).unsqueeze(0)
with torch.inference_mode():
    logits, value = model(encoded)
    again_logits, again_value = model(encoded)
    moves = legal_moves(state)
    ids = torch.tensor([m.source * state.size**2 + m.target for m in moves])
    probabilities = torch.softmax(logits[0, ids], dim=0)
    full = torch.zeros_like(logits[0])
    full[ids] = probabilities
    illegal = torch.ones_like(full, dtype=torch.bool)
    illegal[ids] = False
steps = []
for item in (payload.get("optimizer_state") or {}).get("state", {}).values():
    v = item.get("step")
    if isinstance(v, torch.Tensor) and v.numel() == 1:
        steps.append(float(v))
history = payload["extra"].get("history", [])
training_fields = [
    "iteration",
    "policy_loss",
    "value_loss",
    "updates",
    "optimizer_updates",
    "games",
    "games_generated",
]
info = {
    "source_kind": "ephemeral browser training smoke output; not a strength-qualified production model",
    "source": str(source),
    "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
    "source_bytes": source.stat().st_size,
    "architecture": payload["architecture"],
    "model_config": model.config,
    "parameter_count": sum(p.numel() for p in model.parameters()),
    "parameter_bytes": sum(p.numel() * p.element_size() for p in model.parameters()),
    "training": {
        "iteration": payload["extra"].get("iteration"),
        "games_generated": payload["extra"].get("games_generated"),
        "optimizer_step_min": min(steps) if steps else None,
        "optimizer_step_max": max(steps) if steps else None,
        "last_history": {
            k: history[-1][k] for k in training_fields if history and k in history[-1]
        },
    },
    "environment": {"torch": str(torch.__version__), "cpu_threads": torch.get_num_threads()},
    "native": {
        "input_shape": list(encoded.shape),
        "policy_shape": list(logits.shape),
        "value_shape": list(value.shape),
        "finite_logits": bool(torch.isfinite(logits).all()),
        "value": value.item(),
        "exact_repeat_logits": bool(torch.equal(logits, again_logits)),
        "exact_repeat_value": bool(torch.equal(value, again_value)),
        "legal_count": len(moves),
        "legal_policy_sum": float(probabilities.sum()),
        "illegal_probability_sum": float(full[illegal].sum()),
    },
    "dependencies": {
        n: bool(importlib.util.find_spec(n))
        for n in ["onnx", "onnxscript", "onnxruntime", "openvino"]
    },
}
for name in ["openvino", "torch"]:
    try:
        info["environment"][name] = metadata.version(name)
    except metadata.PackageNotFoundError:
        pass
# The installed modern exporter needs onnxscript. Do not install/download it or
# weaken checkpoint validation simply to make this isolated audit appear complete.
target = output / "trained.onnx"
if target.exists():
    raise SystemExit("Refusing to replace an existing export")
started = time.monotonic()
try:
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter("always")
        torch.onnx.export(
            model,
            (encoded,),
            target,
            input_names=["planes"],
            output_names=["policy_logits", "defender_value"],
            opset_version=18,
            dynamo=True,
        )
    info["onnx_export"] = {
        "ok": True,
        "bytes": target.stat().st_size,
        "warnings": [str(w.message)[:180] for w in seen],
    }
except Exception as e:
    info["onnx_export"] = {"ok": False, "exception_type": type(e).__name__, "message": str(e)[:200]}
info["onnx_export"]["elapsed_seconds"] = time.monotonic() - started
info["onnx_export"]["artifact_exists"] = target.exists()
(output / "native-evidence.json").write_text(json.dumps(info, indent=2) + "\n")
print(json.dumps(info, indent=2))
