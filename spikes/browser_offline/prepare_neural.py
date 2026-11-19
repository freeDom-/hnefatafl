"""Stage the isolated neural inference spike under a disposable output directory.

Downloads only the pinned ORT Web assets (digest-verified), copies the spike
worker/page/client, and stages an explicitly supplied *validated* exported model
plus its native reference vectors. Nothing here enters the production
application, and no model is downloaded from the network.

Artifact integrity is checked before staging:

* the export must carry a passing ``VALIDATED.json`` marker (unless
  ``--allow-unvalidated`` is given for structural experiments);
* the model bytes must hash to the digest recorded in the sidecar, the manifest
  and the marker — a re-hashed copy is never accepted as proof of integrity;
* the sidecar schema, artifact type, ruleset, board size, input/output names,
  shapes, dtypes, dynamic batch and action-indexing semantics must agree with
  the graph and the engine.
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

from hnefatafl.engine import RULESETS  # noqa: E402

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
SIDECAR_SCHEMA = 1
SIDECAR_ARTIFACT = "hnefatafl-policy-value-onnx"
MAX_MODEL_BYTES = 64 * 1024 * 1024
MAX_REFERENCE_BYTES = 16 * 1024 * 1024


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def gzip_size(path: Path) -> int:
    return len(gzip.compress(path.read_bytes(), mtime=0))


def validate_sidecar(sidecar: dict) -> tuple[str, int]:
    """Validate the export sidecar and return (ruleset, board size)."""
    if not isinstance(sidecar, dict):
        raise SystemExit("model.json must be a JSON object")
    if sidecar.get("schema") != SIDECAR_SCHEMA:
        raise SystemExit(f"Unsupported sidecar schema: {sidecar.get('schema')!r}")
    if sidecar.get("artifact") != SIDECAR_ARTIFACT:
        raise SystemExit(f"Unsupported artifact type: {sidecar.get('artifact')!r}")
    ruleset = sidecar.get("ruleset")
    if ruleset not in RULESETS:
        raise SystemExit(f"Unsupported model ruleset: {ruleset!r}")
    size = RULESETS[ruleset].size
    configuration = sidecar.get("model_config")
    if not isinstance(configuration, dict) or configuration.get("ruleset") != ruleset:
        raise SystemExit("model_config ruleset does not match the sidecar ruleset")
    source_input = sidecar.get("input") or {}
    if source_input.get("name") != "planes" or source_input.get("encoding") != "six-plane-v1":
        raise SystemExit("Model input must be the six-plane 'planes' tensor")
    if source_input.get("shape") != ["batch", 6, size, size]:
        raise SystemExit(
            f"Model input shape {source_input.get('shape')} is not ['batch', 6, {size}, {size}]"
        )
    if source_input.get("dtype") != "float32":
        raise SystemExit("Model input dtype must be float32")
    if source_input.get("dynamic_batch") is not True:
        raise SystemExit("Model input must declare a dynamic batch dimension")
    outputs = sidecar.get("outputs") or {}
    expected_policy = RULESETS[ruleset].size ** 4
    if outputs.get("policy_logits", {}).get("shape") != ["batch", expected_policy]:
        raise SystemExit("policy_logits output shape is not ['batch', size**4]")
    if outputs.get("defender_value", {}).get("shape") != ["batch"]:
        raise SystemExit("defender_value output shape is not ['batch']")
    if sidecar.get("action_index") != "source*size**2+target":
        raise SystemExit("Unsupported policy action indexing")
    if sidecar.get("value_perspective") != "defenders":
        raise SystemExit("Value output must be defender-relative")
    return ruleset, size


def helpers_expected_policy(ruleset: str) -> int:
    return RULESETS[ruleset].size ** 4


def validate_graph(model_onnx: Path, sidecar: dict) -> None:
    """Check the graph's input/output names and dynamic batch without ORT."""
    try:
        import onnx
    except ImportError:
        raise SystemExit(
            "onnx is required to validate the model graph; install "
            "spikes/browser_offline/requirements-onnx.txt"
        ) from None
    try:
        graph = onnx.load(str(model_onnx))
        onnx.checker.check_model(graph)
    except Exception as error:  # noqa: BLE001 - surfaced as a staging failure
        raise SystemExit(f"Graph validation failed: {error}") from None
    names = {value.name for value in graph.graph.input}
    outputs = {value.name for value in graph.graph.output}
    if names != {"planes"}:
        raise SystemExit(f"Graph inputs {sorted(names)} do not match 'planes'")
    if outputs != {"policy_logits", "defender_value"}:
        raise SystemExit(f"Graph outputs {sorted(outputs)} are not the expected pair")
    dims = {
        value.name: [dim.dim_value or dim.dim_param for dim in value.type.tensor_type.shape.dim]
        for value in list(graph.graph.input) + list(graph.graph.output)
    }
    if dims.get("planes", [None])[0] not in ("batch", "B", "N", "batch_size", None):
        raise SystemExit("Graph input does not expose a dynamic batch dimension")


def validate_export_dir(model_dir: Path, allow_unvalidated: bool) -> dict:
    model_onnx = model_dir / "model.onnx"
    model_json = model_dir / "model.json"
    reference_json = model_dir / "reference.json"
    marker = model_dir / "VALIDATED.json"
    manifest_path = model_dir / "export-manifest.json"
    for required in (model_onnx, model_json, reference_json):
        if not required.is_file():
            raise SystemExit(f"--model-dir is missing {required.name}")
    if model_onnx.stat().st_size > MAX_MODEL_BYTES:
        raise SystemExit("Model exceeds the experimental size limit")
    if reference_json.stat().st_size > MAX_REFERENCE_BYTES:
        raise SystemExit("Reference vectors exceed the experimental size limit")
    if not marker.is_file():
        if not allow_unvalidated:
            raise SystemExit(
                "Model artifact has no VALIDATED.json marker. Re-export with parity "
                "checks, or pass --allow-unvalidated for structural experiments."
            )
        print("WARNING: staging an UNVALIDATED model for structural experiments")

    sidecar = json.loads(model_json.read_text())
    ruleset, size = validate_sidecar(sidecar)

    # Integrity first: never parse a graph whose bytes do not match the
    # recorded digest, and never re-hash a copy and call it verified.
    actual_digest = sha256(model_onnx)
    recorded = [d for d in (sidecar.get("onnx", {}).get("sha256"),) if d]
    manifest = json.loads(manifest_path.read_text()) if manifest_path.is_file() else {}
    if manifest.get("model_sha256"):
        recorded.append(manifest["model_sha256"])
    if marker.is_file():
        recorded.append(json.loads(marker.read_text()).get("model_sha256"))
    if not recorded:
        raise SystemExit("No recorded model digest to verify against")
    if any(digest != actual_digest for digest in recorded):
        raise SystemExit("Model digest does not match the recorded integrity metadata")

    validate_graph(model_onnx, sidecar)

    reference = json.loads(reference_json.read_text())
    if reference.get("ruleset") != ruleset:
        raise SystemExit("Reference vectors were generated for a different ruleset")
    if reference.get("model_sha256") != actual_digest:
        raise SystemExit("Reference vectors were generated for different model bytes")
    if not reference.get("cases"):
        raise SystemExit("Reference vectors contain no cases")
    return {
        "sidecar": sidecar,
        "reference": reference,
        "ruleset": ruleset,
        "size": size,
        "digest": actual_digest,
        "validated": marker.is_file(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ort-dir", type=Path, help="Previously downloaded ORT assets")
    parser.add_argument("--wasm-only", action="store_true", help="Skip the WebGPU jsep bundle")
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
    for name in ("neural-worker.js", "neural-provider.js", "neural.html", "neural-client.js"):
        shutil.copyfile(SPIKE / name, output / name)
    (output / "model").mkdir(exist_ok=True)
    shutil.copyfile(model_dir / "model.onnx", output / "model/model.onnx")
    shutil.copyfile(model_dir / "model.json", output / "model/model.json")
    shutil.copyfile(model_dir / "reference.json", output / "model/reference.json")
    staged_digest = sha256(output / "model/model.onnx")
    if staged_digest != export["digest"]:
        raise SystemExit("Staged model digest changed during copy")

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
        },
        "sidecar": sidecar,
        "ort_inventory": ort_inventory,
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
            }
        )
    )


if __name__ == "__main__":
    main()
