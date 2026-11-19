"""Stage the isolated neural inference spike under a disposable output directory.

Downloads only the pinned ORT Web assets (digest-verified), copies the spike
worker/page/client and an explicitly supplied exported model, and writes the
capability/input metadata the benchmark page reads. Nothing here enters the
production application, and no model is downloaded from the network.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import urllib.request
from pathlib import Path

SPIKE = Path(__file__).resolve().parent
ROOT = SPIKE.parents[1]
for path in (str(SPIKE), str(ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

from fixtures import jarl_positions  # noqa: E402
from hnefatafl.engine import State, legal_moves  # noqa: E402
from hnefatafl.models import encode_state  # noqa: E402

ORT_VERSION = "1.22.0"
ORT_BASE = f"https://cdn.jsdelivr.net/npm/onnxruntime-web@{ORT_VERSION}/dist"
# Digest-verified pinned assets. The jsep bundle is only needed for WebGPU.
ORT_ASSETS = {
    "ort.min.js": "924e4fcdfdf69acccff3d024d6e1ad3f41ca858d27fc4dd59fc4927dd933e7db",
    "ort.webgpu.min.js": "4dec2f5e74de2ec0f496d39ef55ec23762e8c6ca58cb958e691c9f2350a7b1c8",
    "ort-wasm-simd-threaded.mjs": "30dd851d9c00622940500f71ddd2ff8820c5cb65270816080175b958705385a8",
    "ort-wasm-simd-threaded.wasm": "71aef04959c5c1b6de461b6538e2058e306610034a85aad2742d0c7fd4533fe4",
    "ort-wasm-simd-threaded.jsep.mjs": "1cbcba8f2c769c1eecbab66a1b1e55ef11704515bf4306373e3db3c37cf6dcd8",
    "ort-wasm-simd-threaded.jsep.wasm": "b45970d0632383a057c27ca5b660b216f8e00c17cf8db9f6207b5e4abc839368",
}
WASM_ONLY = {
    "ort.min.js",
    "ort-wasm-simd-threaded.mjs",
    "ort-wasm-simd-threaded.wasm",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ort-dir", type=Path, help="Previously downloaded ORT assets")
    parser.add_argument("--wasm-only", action="store_true", help="Skip the WebGPU jsep bundle")
    args = parser.parse_args()
    output = args.output.resolve()
    if output == ROOT or (ROOT in output.parents and not output.is_relative_to(ROOT / "work")):
        parser.error("Stage inside work/ or outside the repository")
    model_dir = args.model_dir.resolve()
    model_onnx = model_dir / "model.onnx"
    model_json = model_dir / "model.json"
    if not model_onnx.is_file() or not model_json.is_file():
        parser.error("--model-dir must contain model.onnx and model.json")
    sidecar = json.loads(model_json.read_text())
    ruleset = sidecar["ruleset"]
    size = sidecar["input"]["shape"][-1]

    output.mkdir(parents=True, exist_ok=True)
    for name in ("neural-worker.js", "neural.html", "neural-client.js"):
        shutil.copyfile(SPIKE / name, output / name)
    (output / "model").mkdir(exist_ok=True)
    shutil.copyfile(model_onnx, output / "model/model.onnx")
    shutil.copyfile(model_json, output / "model/model.json")

    ort_dir = output / "ort"
    ort_dir.mkdir(exist_ok=True)
    wanted = WASM_ONLY if args.wasm_only else set(ORT_ASSETS)
    for name in sorted(wanted):
        target = ort_dir / name
        if args.ort_dir:
            shutil.copyfile(args.ort_dir / name, target)
        else:
            url = f"{ORT_BASE}/{name}"
            print(f"Downloading {url}", flush=True)
            with urllib.request.urlopen(url, timeout=60) as response:
                target.write_bytes(response.read())
        if sha256(target) != ORT_ASSETS[name]:
            raise ValueError(f"Pinned ORT digest mismatch: {name}")

    # Representative encoded input for one non-terminal position of the model's
    # ruleset. It is serialized so the browser worker does not need PyTorch.
    case = next(
        case
        for case in jarl_positions()
        if case["ruleset"] == ruleset and case["kind"] != "terminal"
    )
    state = State.from_dict(case["state"])
    planes = encode_state(state).reshape(-1).tolist()
    legal = [move.source * size**2 + move.target for move in legal_moves(state)]
    (output / "neural-inputs.json").write_text(
        json.dumps(
            {
                "position": case["name"],
                "ruleset": ruleset,
                "size": size,
                "planes": planes,
                "legal": legal,
            },
            separators=(",", ":"),
        )
        + "\n"
    )
    assets = {
        "ort_version": ORT_VERSION,
        "providers_staged": ["wasm"] if args.wasm_only else ["wasm", "webgpu"],
        "model": {
            "file": "model/model.onnx",
            "ruleset": ruleset,
            "size": size,
            "sha256": sha256(model_onnx),
            "bytes": model_onnx.stat().st_size,
        },
        "sidecar": sidecar,
    }
    (output / "neural-assets.json").write_text(json.dumps(assets, indent=2) + "\n")
    print(json.dumps({"output": str(output), "model_bytes": model_onnx.stat().st_size}))


if __name__ == "__main__":
    main()
