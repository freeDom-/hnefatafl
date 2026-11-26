"""Stage the isolated neural inference spike under a disposable output directory.

Downloads only the pinned ORT Web assets (digest-verified), copies the spike
worker/page/client, and stages an explicitly supplied *validated* exported model
plus its native reference vectors. Nothing here enters the production
application, and no model is downloaded from the network.

Artifact lifecycle validation is coherent and fail-closed:

* the export must carry a passing ``VALIDATED.json`` marker and a manifest that
  agree on validation status, ruleset, model digest and reference-vector digest
  (unless ``--allow-unvalidated`` is given for a separate structural mode);
* the model *and* reference-vector bytes are verified against their recorded
  digests — a re-hashed copy is never accepted as proof of integrity;
* the sidecar schema, artifact type, architecture configuration, ruleset, board
  size, input/output contract, dynamic batch, tensor ranks, element types, policy
  width and graph outputs are validated against the actual ONNX graph;
* stale, incomplete or mismatched marker/manifest combinations are rejected, so
  an unvalidated replacement can never inherit an earlier marker.
"""

from __future__ import annotations

import argparse
import gzip
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


from prepare import ASSETS as PYODIDE_ASSETS  # noqa: E402
from prepare import RUNTIME_SHA256 as PYODIDE_SHA256  # noqa: E402
from prepare import SOURCE_MODULES as CLASSICAL_MODULES  # noqa: E402
from prepare import VERSION as PYODIDE_VERSION  # noqa: E402

# The search worker runs the unchanged engine plus the shared encoder and the
# cooperative driver; it never loads PyTorch or model tensors.
SEARCH_SOURCE_MODULES = CLASSICAL_MODULES + ("encoding.py", "mcts_driver.py")
SEARCH_ROOT_MODULES = (
    "search_bridge.py",
    "mcts_vectors.py",
    "mcts_benchmark.py",
    "mcts_workload.py",
)
SEARCH_ASSETS_SCHEMA = 1

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
# The pinned `ort.min.js` build is the all-in-one bundle and resolves the jsep
# kernels dynamically even for a WASM-only session, so `--wasm-only` skips only
# the WebGPU entry bundle. Omitting the jsep kernels makes session creation fail
# with "no available backend found".
WASM_ONLY = {
    "ort.min.js",
    "ort-wasm-simd-threaded.mjs",
    "ort-wasm-simd-threaded.wasm",
    "ort-wasm-simd-threaded.jsep.mjs",
    "ort-wasm-simd-threaded.jsep.wasm",
}
# Canonical export-artifact contract (shared with production publication).
from hnefatafl.onnx_artifacts import OnnxArtifactError  # noqa: E402
from hnefatafl.onnx_artifacts import sha256  # noqa: E402
from hnefatafl.onnx_artifacts import validate_export_dir as _validate_export_dir  # noqa: E402


def gzip_size(path: Path) -> int:
    return len(gzip.compress(path.read_bytes(), mtime=0))


def validate_export_dir(model_dir: Path, allow_unvalidated: bool) -> dict:
    """Spike CLI wrapper around the canonical production artifact contract."""
    try:
        return _validate_export_dir(model_dir, allow_unvalidated=allow_unvalidated)
    except OnnxArtifactError as error:
        raise SystemExit(str(error)) from None


def stage_search_runtime(output: Path, runtime_dir: Path | None) -> dict:
    """Stage the Pyodide engine and the cooperative search workers."""
    from fixtures import jarl_positions

    runtime = output / "runtime"
    runtime.mkdir(exist_ok=True)
    runtime_inventory = []
    for name in PYODIDE_ASSETS:
        target = runtime / name
        if runtime_dir:
            shutil.copyfile(runtime_dir / name, target)
        else:
            url = f"https://cdn.jsdelivr.net/pyodide/v{PYODIDE_VERSION}/full/{name}"
            print(f"Downloading {url}", flush=True)
            with urllib.request.urlopen(url, timeout=60) as response:
                target.write_bytes(response.read())
        digest = sha256(target)
        if digest != PYODIDE_SHA256[name]:
            raise SystemExit(f"Pinned Pyodide digest mismatch: {name}")
        runtime_inventory.append(
            {
                "path": f"runtime/{name}",
                "bytes": target.stat().st_size,
                "gzip_bytes": gzip_size(target),
                "sha256": digest,
            }
        )
    package = output / "python/hnefatafl"
    package.mkdir(parents=True, exist_ok=True)
    inventory = []
    for name in SEARCH_SOURCE_MODULES:
        shutil.copyfile(ROOT / "hnefatafl" / name, package / name)
    staged = [f"python/hnefatafl/{name}" for name in SEARCH_SOURCE_MODULES]
    staged += list(SEARCH_ROOT_MODULES)
    for path in staged:
        target = output / path
        data = target.read_bytes()
        inventory.append(
            {
                "path": path,
                "bytes": len(data),
                "gzip_bytes": gzip_size(target),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    raw = sum(entry["bytes"] for entry in inventory) + sum(
        entry["bytes"] for entry in runtime_inventory
    )
    bundle = hashlib.sha256(
        json.dumps([entry["sha256"] for entry in inventory], sort_keys=True).encode()
    ).hexdigest()[:16]
    manifest = {
        "schema": SEARCH_ASSETS_SCHEMA,
        "pyodide": PYODIDE_VERSION,
        "python_modules": list(SEARCH_SOURCE_MODULES),
        "root_modules": list(SEARCH_ROOT_MODULES),
        "runtime_included": True,
        "bundle_id": bundle,
        "runtime_inventory": runtime_inventory,
        "inventory": inventory,
    }
    (output / "search-assets.json").write_text(json.dumps(manifest, indent=2) + "\n")
    # The classical analysis worker (Jarl) loads its own small manifest and the
    # shared benchmark module; it never touches the neural runtime.
    (output / "assets.json").write_text(
        json.dumps(
            {
                "python_modules": list(SEARCH_SOURCE_MODULES),
                "root_modules": ["jarl_benchmark.py"],
            },
            indent=2,
        )
        + "\n"
    )
    (output / "mcts-positions.json").write_text(json.dumps(jarl_positions(), separators=(",", ":")))
    (output / "fixtures.json").write_text(json.dumps(_fixtures_vectors(), separators=(",", ":")))
    return {"bundle_id": bundle, "raw_bytes": raw, "inventory": inventory}


def _fixtures_vectors() -> list[dict]:
    from fixtures import vectors

    return vectors()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ort-dir", type=Path, help="Previously downloaded ORT assets")
    parser.add_argument(
        "--runtime-dir", type=Path, help="Previously downloaded, trusted Pyodide runtime"
    )
    parser.add_argument(
        "--wasm-only",
        action="store_true",
        help="Skip the WebGPU entry bundle (jsep kernels are still staged for the all-in-one WASM bundle)",
    )
    parser.add_argument(
        "--allow-unvalidated",
        action="store_true",
        help="Stage a model without a passing VALIDATED.json marker (structural only)",
    )
    args = parser.parse_args()
    output = args.output.resolve()
    if output == ROOT or (ROOT in output.parents and not output.is_relative_to(ROOT / "work")):
        parser.error("Stage inside work/ or outside the repository")
    model_dir = args.model_dir.resolve()
    export = validate_export_dir(model_dir, args.allow_unvalidated)
    sidecar = export["sidecar"]
    ruleset = export["ruleset"]
    size = export["size"]

    output.mkdir(parents=True, exist_ok=True)
    for name in (
        "neural-worker.js",
        "neural-provider.js",
        "neural-tensors.js",
        "neural-identity.js",
        "neural.html",
        "neural-client.js",
        "analysis-client.js",
        "worker.js",
        "search-worker.js",
        "search-assets.js",
        "search-client.js",
        "bridge.py",
        "jarl_benchmark.py",
        "search_bridge.py",
        "mcts_vectors.py",
        "mcts_benchmark.py",
        "mcts_workload.py",
    ):
        shutil.copyfile(SPIKE / name, output / name)
    (output / "model").mkdir(exist_ok=True)
    shutil.copyfile(model_dir / "model.onnx", output / "model/model.onnx")
    shutil.copyfile(model_dir / "model.json", output / "model/model.json")
    shutil.copyfile(model_dir / "reference.json", output / "model/reference.json")
    if sha256(output / "model/model.onnx") != export["digest"]:
        raise SystemExit("Staged model digest changed during copy")
    if sha256(output / "model/reference.json") != export["reference_digest"]:
        raise SystemExit("Staged reference-vector digest changed during copy")

    ort_dir = output / "ort"
    ort_dir.mkdir(exist_ok=True)
    wanted = WASM_ONLY if args.wasm_only else set(ORT_ASSETS)
    ort_inventory = []
    for name in sorted(wanted):
        target = ort_dir / name
        if args.ort_dir:
            shutil.copyfile(args.ort_dir / name, target)
        else:
            url = f"{ORT_BASE}/{name}"
            print(f"Downloading {url}", flush=True)
            with urllib.request.urlopen(url, timeout=60) as response:
                target.write_bytes(response.read())
        digest = sha256(target)
        if digest != ORT_ASSETS[name]:
            raise ValueError(f"Pinned ORT digest mismatch: {name}")
        ort_inventory.append(
            {
                "path": f"ort/{name}",
                "bytes": target.stat().st_size,
                "gzip_bytes": gzip_size(target),
                "sha256": digest,
            }
        )

    assets = {
        "schema": 2,
        "ort_version": ORT_VERSION,
        "providers_staged": ["wasm"] if args.wasm_only else ["wasm", "webgpu"],
        "model": {
            "file": "model/model.onnx",
            "ruleset": ruleset,
            "size": size,
            "sha256": export["digest"],
            "bytes": (output / "model/model.onnx").stat().st_size,
            "gzip_bytes": gzip_size(output / "model/model.onnx"),
            "validated": export["validated"],
            "reference": "model/reference.json",
            "reference_sha256": export["reference_digest"],
        },
        "sidecar": sidecar,
        "ort_inventory": ort_inventory,
    }

    # Stage the Pyodide search runtime so the same page can run Jarl and the
    # browser neural MCTS driver against the unchanged Python engine.
    search = stage_search_runtime(output, args.runtime_dir)
    assets["search"] = {
        "manifest": "search-assets.json",
        "bundle_id": search["bundle_id"],
        "pyodide": PYODIDE_VERSION,
        "python_modules": list(SEARCH_SOURCE_MODULES),
        "root_modules": list(SEARCH_ROOT_MODULES),
    }
    (output / "neural-assets.json").write_text(json.dumps(assets, indent=2) + "\n")
    print(
        json.dumps(
            {
                "output": str(output),
                "model_bytes": assets["model"]["bytes"],
                "model_gzip_bytes": assets["model"]["gzip_bytes"],
                "validated": export["validated"],
                "providers": assets["providers_staged"],
                "search_runtime_bytes": search["raw_bytes"],
            }
        )
    )


if __name__ == "__main__":
    main()
