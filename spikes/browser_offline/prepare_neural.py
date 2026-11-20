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

from hnefatafl.engine import RULESETS  # noqa: E402
from hnefatafl.models import ARCHITECTURE  # noqa: E402

from prepare import ASSETS as PYODIDE_ASSETS  # noqa: E402
from prepare import RUNTIME_SHA256 as PYODIDE_SHA256  # noqa: E402
from prepare import SOURCE_MODULES as CLASSICAL_MODULES  # noqa: E402
from prepare import VERSION as PYODIDE_VERSION  # noqa: E402

# The search worker runs the unchanged engine plus the shared encoder and the
# cooperative driver; it never loads PyTorch or model tensors.
SEARCH_SOURCE_MODULES = CLASSICAL_MODULES + ("encoding.py", "mcts_driver.py")
SEARCH_ROOT_MODULES = ("search_bridge.py", "mcts_vectors.py", "mcts_benchmark.py")
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
SIDECAR_SCHEMA = 2
# Schema 1 exports (Phase 1 revision <= 5) carry no source-weight fingerprint and
# are still accepted for Phase 1 reproduction; schema 2 is required for new
# checkpoint-bound validation.
SUPPORTED_SIDECAR_SCHEMAS = (1, 2)
SIDECAR_ARTIFACT = "hnefatafl-policy-value-onnx"
MANIFEST_SCHEMA = 3
MAX_MODEL_BYTES = 64 * 1024 * 1024
MAX_REFERENCE_BYTES = 16 * 1024 * 1024
MIN_OPSET = 13
MAX_OPSET = 21


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def gzip_size(path: Path) -> int:
    return len(gzip.compress(path.read_bytes(), mtime=0))


def validate_sidecar(sidecar: dict) -> tuple[str, int]:
    """Validate the declared architecture, ruleset, shapes and semantics."""
    if not isinstance(sidecar, dict):
        raise SystemExit("model.json must be a JSON object")
    if sidecar.get("schema") not in SUPPORTED_SIDECAR_SCHEMAS:
        raise SystemExit(f"Unsupported sidecar schema: {sidecar.get('schema')!r}")
    if sidecar.get("artifact") != SIDECAR_ARTIFACT:
        raise SystemExit(f"Unsupported artifact type: {sidecar.get('artifact')!r}")
    if sidecar.get("architecture") != ARCHITECTURE:
        raise SystemExit(f"Unsupported architecture {sidecar.get('architecture')!r}")
    ruleset = sidecar.get("ruleset")
    if ruleset not in RULESETS:
        raise SystemExit(f"Unsupported model ruleset: {ruleset!r}")
    size = RULESETS[ruleset].size
    configuration = sidecar.get("model_config")
    if not isinstance(configuration, dict) or set(configuration) != {
        "ruleset",
        "channels",
        "blocks",
    }:
        raise SystemExit("model_config must contain only ruleset, channels and blocks")
    if configuration.get("ruleset") != ruleset:
        raise SystemExit("model_config ruleset does not match the sidecar ruleset")
    channels = configuration.get("channels")
    if type(channels) is not int or not 4 <= channels <= 128:
        raise SystemExit("model_config channels must be an integer in 4..128")
    blocks = configuration.get("blocks")
    if type(blocks) is not int or not 0 <= blocks <= 8:
        raise SystemExit("model_config blocks must be an integer in 0..8")
    source_input = sidecar.get("input") or {}
    source_metadata = sidecar.get("source")
    if sidecar.get("schema") == 2:
        if not isinstance(source_metadata, dict):
            raise SystemExit("Schema-2 sidecar is missing source model metadata")
        fingerprint = source_metadata.get("weights_sha256")
        if not isinstance(fingerprint, str) or len(fingerprint) != 64:
            raise SystemExit("Schema-2 sidecar requires a source weight fingerprint")
        if source_metadata.get("encoder_version") != "six-plane-v1":
            raise SystemExit("Source metadata must record the six-plane-v1 encoder")
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
    if outputs.get("policy_logits", {}).get("shape") != ["batch", size**4]:
        raise SystemExit("policy_logits output shape is not ['batch', size**4]")
    if outputs.get("defender_value", {}).get("shape") != ["batch"]:
        raise SystemExit("defender_value output shape is not ['batch']")
    for name in ("policy_logits", "defender_value"):
        dtype = outputs.get(name, {}).get("dtype")
        if dtype is not None and dtype != "float32":
            raise SystemExit(f"{name} dtype must be float32")
    if sidecar.get("action_index") != "source*size**2+target":
        raise SystemExit("Unsupported policy action indexing")
    if sidecar.get("value_perspective") != "defenders":
        raise SystemExit("Value output must be defender-relative")
    if sidecar.get("legal_policy_masking") != "softmax over legal action indices only":
        raise SystemExit("Unsupported legal-policy masking description")
    return ruleset, size


def _tensor_info(value) -> dict:
    tensor = value.type.tensor_type
    dims = []
    dynamic = False
    for dim in tensor.shape.dim:
        if dim.dim_value:
            dims.append(dim.dim_value)
        else:
            dims.append(dim.dim_param or None)
            dynamic = True
    return {"elem_type": tensor.elem_type, "rank": len(dims), "dims": dims, "dynamic": dynamic}


def validate_graph(model_onnx: Path, sidecar: dict) -> dict:
    """Validate the actual ONNX graph rather than trusting metadata alone."""
    try:
        import onnx

        float_type = onnx.TensorProto.FLOAT
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

    opsets = {(opset.domain or "ai.onnx"): opset.version for opset in graph.opset_import}
    standard = opsets.get("ai.onnx")
    if standard is None:
        raise SystemExit("Graph declares no standard ONNX opset")
    if not MIN_OPSET <= standard <= MAX_OPSET:
        raise SystemExit(f"Unsupported ONNX opset {standard} (allowed {MIN_OPSET}..{MAX_OPSET})")
    ruleset, size = sidecar["ruleset"], RULESETS[sidecar["ruleset"]].size

    inputs = {value.name: _tensor_info(value) for value in graph.graph.input}
    outputs = {value.name: _tensor_info(value) for value in graph.graph.output}
    if set(inputs) != {"planes"} or set(outputs) != {"policy_logits", "defender_value"}:
        raise SystemExit(
            f"Graph inputs/outputs {sorted(inputs)}/{sorted(outputs)} are not the expected contract"
        )
    planes = inputs["planes"]
    if planes["elem_type"] != float_type:
        raise SystemExit("Graph input 'planes' must be float32")
    if planes["rank"] != 4 or planes["dims"][1:] != [6, size, size]:
        raise SystemExit(f"Graph input dims {planes['dims']} are not [batch, 6, {size}, {size}]")
    if not planes["dynamic"]:
        raise SystemExit("Graph input does not expose a dynamic batch dimension")
    policy = outputs["policy_logits"]
    if policy["elem_type"] != float_type:
        raise SystemExit("Graph output 'policy_logits' must be float32")
    if policy["rank"] != 2 or policy["dims"][1] != size**4:
        raise SystemExit(f"Graph policy dims {policy['dims']} are not [batch, {size**4}]")
    if not policy["dynamic"]:
        raise SystemExit("Graph policy output does not expose a dynamic batch dimension")
    value = outputs["defender_value"]
    if value["elem_type"] != float_type:
        raise SystemExit("Graph output 'defender_value' must be float32")
    if value["rank"] != 1:
        raise SystemExit(f"Graph value dims {value['dims']} are not [batch]")
    if not value["dynamic"]:
        raise SystemExit("Graph value output does not expose a dynamic batch dimension")
    return {
        "opset": standard,
        "ruleset": ruleset,
        "size": size,
        "inputs": inputs,
        "outputs": outputs,
    }


def _require_fields(payload: dict, *, name: str, fields: tuple[str, ...]) -> None:
    if not isinstance(payload, dict):
        raise SystemExit(f"{name} must be a JSON object")
    missing = [field for field in fields if field not in payload]
    if missing:
        raise SystemExit(f"{name} is missing required field(s): {missing}")


def validate_export_dir(model_dir: Path, allow_unvalidated: bool) -> dict:
    model_onnx = model_dir / "model.onnx"
    model_json = model_dir / "model.json"
    reference_json = model_dir / "reference.json"
    marker_path = model_dir / "VALIDATED.json"
    manifest_path = model_dir / "export-manifest.json"
    for required in (model_onnx, model_json, reference_json):
        if not required.is_file():
            raise SystemExit(f"--model-dir is missing {required.name}")
    if model_onnx.stat().st_size > MAX_MODEL_BYTES:
        raise SystemExit("Model exceeds the experimental size limit")
    if reference_json.stat().st_size > MAX_REFERENCE_BYTES:
        raise SystemExit("Reference vectors exceed the experimental size limit")

    sidecar = json.loads(model_json.read_text())
    ruleset, size = validate_sidecar(sidecar)
    actual_digest = sha256(model_onnx)
    actual_bytes = model_onnx.stat().st_size
    if sidecar.get("onnx", {}).get("sha256") != actual_digest:
        raise SystemExit("Model digest does not match the sidecar")

    manifest = None
    marker = None
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text())
        _require_fields(
            manifest,
            name="export-manifest.json",
            fields=(
                "schema",
                "validated",
                "artifact",
                "model_sha256",
                "model_bytes",
                "ruleset",
                "reference_sha256",
                "sidecar_sha256",
            ),
        )
        if manifest.get("schema") != MANIFEST_SCHEMA:
            raise SystemExit(f"Unsupported export manifest schema {manifest.get('schema')!r}")
        if manifest.get("artifact") != SIDECAR_ARTIFACT:
            raise SystemExit("Export manifest artifact type mismatch")
    if marker_path.is_file():
        marker = json.loads(marker_path.read_text())
        _require_fields(
            marker,
            name="VALIDATED.json",
            fields=(
                "validated",
                "model_sha256",
                "reference_sha256",
                "sidecar_sha256",
                "parity_sha256",
                "ruleset",
            ),
        )
        if manifest is None:
            raise SystemExit("VALIDATED.json exists without a coherent export manifest")
        if marker.get("validated") is not True or manifest.get("validated") is not True:
            raise SystemExit("Stale marker/manifest: validation status does not agree")
        if manifest.get("parity_passed") is not True:
            raise SystemExit("Export manifest is not marked as parity-passed")
        if marker.get("model_sha256") != actual_digest:
            raise SystemExit("Stale marker: model digest does not match")
    elif not allow_unvalidated:
        raise SystemExit(
            "Model artifact has no VALIDATED.json marker. Re-export with parity "
            "checks, or pass --allow-unvalidated for structural experiments."
        )
    else:
        print("WARNING: staging an UNVALIDATED model for structural experiments")

    if manifest is not None:
        if manifest.get("validated") is True and marker is None:
            raise SystemExit("Manifest claims validation but VALIDATED.json is missing")
        if manifest.get("model_sha256") != actual_digest:
            raise SystemExit("Export manifest model digest does not match")
        if manifest.get("model_bytes") != actual_bytes:
            raise SystemExit("Export manifest model size does not match")
        if manifest.get("ruleset") != ruleset:
            raise SystemExit("Export manifest ruleset does not match the sidecar")
        source_metadata = sidecar.get("source") or {}
        if manifest.get("source_weights_sha256") != source_metadata.get("weights_sha256"):
            raise SystemExit("Export manifest source-weight fingerprint does not match")
        if manifest.get("checkpoint_sha256") != source_metadata.get("sha256"):
            raise SystemExit("Export manifest checkpoint digest does not match")
    if marker is not None and marker.get("ruleset") != ruleset:
        raise SystemExit("VALIDATED.json ruleset does not match the sidecar")

    # Bind the accepted artifact set: the marker and manifest must cover the
    # exact metadata sidecar and parity report, not just the model bytes.
    actual_sidecar = sha256(model_json)
    if manifest is not None and manifest.get("sidecar_sha256") != actual_sidecar:
        raise SystemExit("Metadata sidecar digest does not match the export manifest")
    if marker is not None and marker.get("sidecar_sha256") != actual_sidecar:
        raise SystemExit("Metadata sidecar digest does not match VALIDATED.json")
    validated = marker is not None
    parity_path = model_dir / "parity.json"
    if validated:
        if not parity_path.is_file():
            raise SystemExit("Validated export is missing its parity report")
        actual_parity = sha256(parity_path)
        if manifest.get("parity_sha256") != actual_parity:
            raise SystemExit("Parity report digest does not match the export manifest")
        if marker.get("parity_sha256") != actual_parity:
            raise SystemExit("Parity report digest does not match VALIDATED.json")
    elif parity_path.is_file():
        raise SystemExit("Unvalidated export must not contain a stale parity report")

    validate_graph(model_onnx, sidecar)

    reference = json.loads(reference_json.read_text())
    reference_digest = sha256(reference_json)
    sidecar_reference = sidecar.get("reference") or {}
    if sidecar_reference.get("sha256") != reference_digest:
        raise SystemExit("Reference-vector digest does not match the sidecar")
    if manifest is not None and manifest.get("reference_sha256") != reference_digest:
        raise SystemExit("Export manifest reference-vector digest does not match")
    if marker is not None and marker.get("reference_sha256") != reference_digest:
        raise SystemExit("VALIDATED.json reference-vector digest does not match")
    if reference.get("ruleset") != ruleset:
        raise SystemExit("Reference vectors were generated for a different ruleset")
    if reference.get("model_sha256") != actual_digest:
        raise SystemExit("Reference vectors were generated for different model bytes")
    if not reference.get("cases"):
        raise SystemExit("Reference vectors contain no cases")
    if reference.get("schema") != 2:
        raise SystemExit(f"Unsupported reference schema {reference.get('schema')!r}")

    return {
        "sidecar": sidecar,
        "reference": reference,
        "ruleset": ruleset,
        "size": size,
        "digest": actual_digest,
        "reference_digest": reference_digest,
        "validated": marker is not None,
    }


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
