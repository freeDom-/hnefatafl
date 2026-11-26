"""Canonical validation of browser-compatible ONNX export artifacts.

This module is the single implementation of the export/manifest/sidecar/graph
contract. It is used by the isolated browser spike staging tool and by the
production model-publication workflow, so a published artifact cannot pass a
weaker check than the one that validated it in the spike.

Failures raise :class:`OnnxArtifactError`; callers convert them to their own CLI
or API error style. The ONNX package is an optional dependency and is imported
lazily inside :func:`validate_graph`.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .encoding import ENCODER_VERSION
from .engine import RULESETS
from .models import ARCHITECTURE

SIDECAR_ARTIFACT = "hnefatafl-policy-value-onnx"
SIDECAR_SCHEMA = 2
SUPPORTED_SIDECAR_SCHEMAS = (1, 2)
MANIFEST_SCHEMA = 3
MAX_MODEL_BYTES = 64 * 1024 * 1024
MAX_REFERENCE_BYTES = 16 * 1024 * 1024
MIN_OPSET = 13
MAX_OPSET = 21
ONNX_WEB_OPSET_SUPPORT = (MIN_OPSET, MAX_OPSET)


class OnnxArtifactError(ValueError):
    """An export artifact is missing, inconsistent or not browser-compatible."""

    def __init__(self, message: str, *, code: str = "INVALID_EXPORT") -> None:
        super().__init__(message)
        self.code = code


def sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def validate_sidecar(sidecar: Any) -> tuple[str, int]:
    """Validate the declared architecture, ruleset, shapes and semantics."""
    if not isinstance(sidecar, dict):
        raise OnnxArtifactError("model.json must be a JSON object")
    if sidecar.get("schema") not in SUPPORTED_SIDECAR_SCHEMAS:
        raise OnnxArtifactError(f"Unsupported sidecar schema: {sidecar.get('schema')!r}")
    if sidecar.get("artifact") != SIDECAR_ARTIFACT:
        raise OnnxArtifactError(f"Unsupported artifact type: {sidecar.get('artifact')!r}")
    if sidecar.get("architecture") != ARCHITECTURE:
        raise OnnxArtifactError(f"Unsupported architecture {sidecar.get('architecture')!r}")
    ruleset = sidecar.get("ruleset")
    if ruleset not in RULESETS:
        raise OnnxArtifactError(f"Unsupported model ruleset: {ruleset!r}")
    size = RULESETS[ruleset].size
    configuration = sidecar.get("model_config")
    if not isinstance(configuration, dict) or set(configuration) != {
        "ruleset",
        "channels",
        "blocks",
    }:
        raise OnnxArtifactError("model_config must contain only ruleset, channels and blocks")
    if configuration.get("ruleset") != ruleset:
        raise OnnxArtifactError("model_config ruleset does not match the sidecar ruleset")
    channels = configuration.get("channels")
    if type(channels) is not int or not 4 <= channels <= 128:
        raise OnnxArtifactError("model_config channels must be an integer in 4..128")
    blocks = configuration.get("blocks")
    if type(blocks) is not int or not 0 <= blocks <= 8:
        raise OnnxArtifactError("model_config blocks must be an integer in 0..8")
    source_input = sidecar.get("input") or {}
    source_metadata = sidecar.get("source")
    if sidecar.get("schema") == 2:
        if not isinstance(source_metadata, dict):
            raise OnnxArtifactError("Schema-2 sidecar is missing source model metadata")
        fingerprint = source_metadata.get("weights_sha256")
        if not isinstance(fingerprint, str) or len(fingerprint) != 64:
            raise OnnxArtifactError("Schema-2 sidecar requires a source weight fingerprint")
        if source_metadata.get("encoder_version") != ENCODER_VERSION:
            raise OnnxArtifactError(f"Source metadata must record the {ENCODER_VERSION} encoder")
    if source_input.get("name") != "planes" or source_input.get("encoding") != ENCODER_VERSION:
        raise OnnxArtifactError("Model input must be the six-plane 'planes' tensor")
    if source_input.get("shape") != ["batch", 6, size, size]:
        raise OnnxArtifactError(
            f"Model input shape {source_input.get('shape')} is not ['batch', 6, {size}, {size}]"
        )
    if source_input.get("dtype") != "float32":
        raise OnnxArtifactError("Model input dtype must be float32")
    if source_input.get("dynamic_batch") is not True:
        raise OnnxArtifactError("Model input must declare a dynamic batch dimension")
    outputs = sidecar.get("outputs") or {}
    if outputs.get("policy_logits", {}).get("shape") != ["batch", size**4]:
        raise OnnxArtifactError("policy_logits output shape is not ['batch', size**4]")
    if outputs.get("defender_value", {}).get("shape") != ["batch"]:
        raise OnnxArtifactError("defender_value output shape is not ['batch']")
    for name in ("policy_logits", "defender_value"):
        dtype = outputs.get(name, {}).get("dtype")
        if dtype is not None and dtype != "float32":
            raise OnnxArtifactError(f"{name} dtype must be float32")
    if sidecar.get("action_index") != "source*size**2+target":
        raise OnnxArtifactError("Unsupported policy action indexing")
    if sidecar.get("value_perspective") != "defenders":
        raise OnnxArtifactError("Value output must be defender-relative")
    if sidecar.get("legal_policy_masking") != "softmax over legal action indices only":
        raise OnnxArtifactError("Unsupported legal-policy masking description")
    return ruleset, size


def _tensor_info(value: Any) -> dict:
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
        raise OnnxArtifactError(
            "onnx is required to validate the model graph; install "
            "spikes/browser_offline/requirements-onnx.txt",
            code="ONNX_UNAVAILABLE",
        ) from None
    try:
        graph = onnx.load(str(model_onnx))
        onnx.checker.check_model(graph)
    except Exception as error:  # noqa: BLE001 - surfaced as a validation failure
        raise OnnxArtifactError(f"Graph validation failed: {error}") from None

    opsets = {(opset.domain or "ai.onnx"): opset.version for opset in graph.opset_import}
    standard = opsets.get("ai.onnx")
    if standard is None:
        raise OnnxArtifactError("Graph declares no standard ONNX opset")
    if not MIN_OPSET <= standard <= MAX_OPSET:
        raise OnnxArtifactError(
            f"Unsupported ONNX opset {standard} (allowed {MIN_OPSET}..{MAX_OPSET})"
        )
    ruleset, size = sidecar["ruleset"], RULESETS[sidecar["ruleset"]].size

    inputs = {value.name: _tensor_info(value) for value in graph.graph.input}
    outputs = {value.name: _tensor_info(value) for value in graph.graph.output}
    if set(inputs) != {"planes"} or set(outputs) != {"policy_logits", "defender_value"}:
        raise OnnxArtifactError(
            f"Graph inputs/outputs {sorted(inputs)}/{sorted(outputs)} are not the expected contract"
        )
    planes = inputs["planes"]
    if planes["elem_type"] != float_type:
        raise OnnxArtifactError("Graph input 'planes' must be float32")
    if planes["rank"] != 4 or planes["dims"][1:] != [6, size, size]:
        raise OnnxArtifactError(
            f"Graph input dims {planes['dims']} are not [batch, 6, {size}, {size}]"
        )
    if not planes["dynamic"]:
        raise OnnxArtifactError("Graph input does not expose a dynamic batch dimension")
    policy = outputs["policy_logits"]
    if policy["elem_type"] != float_type:
        raise OnnxArtifactError("Graph output 'policy_logits' must be float32")
    if policy["rank"] != 2 or policy["dims"][1] != size**4:
        raise OnnxArtifactError(f"Graph policy dims {policy['dims']} are not [batch, {size**4}]")
    if not policy["dynamic"]:
        raise OnnxArtifactError("Graph policy output does not expose a dynamic batch dimension")
    value = outputs["defender_value"]
    if value["elem_type"] != float_type:
        raise OnnxArtifactError("Graph output 'defender_value' must be float32")
    if value["rank"] != 1:
        raise OnnxArtifactError(f"Graph value dims {value['dims']} are not [batch]")
    if not value["dynamic"]:
        raise OnnxArtifactError("Graph value output does not expose a dynamic batch dimension")
    return {
        "opset": standard,
        "ruleset": ruleset,
        "size": size,
        "inputs": inputs,
        "outputs": outputs,
    }


def _require_fields(payload: Any, *, name: str, fields: tuple[str, ...]) -> None:
    if not isinstance(payload, dict):
        raise OnnxArtifactError(f"{name} must be a JSON object")
    missing = [field for field in fields if field not in payload]
    if missing:
        raise OnnxArtifactError(f"{name} is missing required field(s): {missing}")


def validate_export_dir(model_dir: Path, allow_unvalidated: bool = False) -> dict:
    """Validate a complete export directory produced by ``onnx_export.py``.

    Every mandatory digest is recomputed. An unvalidated structural export is
    refused unless ``allow_unvalidated`` is explicitly requested (publication
    never does).
    """
    model_dir = Path(model_dir)
    model_onnx = model_dir / "model.onnx"
    model_json = model_dir / "model.json"
    reference_json = model_dir / "reference.json"
    marker_path = model_dir / "VALIDATED.json"
    manifest_path = model_dir / "export-manifest.json"
    for required in (model_onnx, model_json, reference_json):
        if not required.is_file():
            raise OnnxArtifactError(f"--model-dir is missing {required.name}")
    if model_onnx.stat().st_size > MAX_MODEL_BYTES:
        raise OnnxArtifactError("Model exceeds the experimental size limit")
    if reference_json.stat().st_size > MAX_REFERENCE_BYTES:
        raise OnnxArtifactError("Reference vectors exceed the experimental size limit")

    try:
        sidecar = json.loads(model_json.read_text())
    except (OSError, ValueError) as error:
        raise OnnxArtifactError(f"model.json is unreadable: {error}") from error
    ruleset, size = validate_sidecar(sidecar)
    actual_digest = sha256(model_onnx)
    actual_bytes = model_onnx.stat().st_size
    if sidecar.get("onnx", {}).get("sha256") != actual_digest:
        raise OnnxArtifactError("Model digest does not match the sidecar")

    manifest = None
    marker = None
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text())
        except (OSError, ValueError) as error:
            raise OnnxArtifactError(f"export-manifest.json is unreadable: {error}") from error
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
            raise OnnxArtifactError(
                f"Unsupported export manifest schema {manifest.get('schema')!r}"
            )
        if manifest.get("artifact") != SIDECAR_ARTIFACT:
            raise OnnxArtifactError("Export manifest artifact type mismatch")
    if marker_path.is_file():
        try:
            marker = json.loads(marker_path.read_text())
        except (OSError, ValueError) as error:
            raise OnnxArtifactError(f"VALIDATED.json is unreadable: {error}") from error
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
            raise OnnxArtifactError("VALIDATED.json exists without a coherent export manifest")
        if marker.get("validated") is not True or manifest.get("validated") is not True:
            raise OnnxArtifactError("Stale marker/manifest: validation status does not agree")
        if manifest.get("parity_passed") is not True:
            raise OnnxArtifactError("Export manifest is not marked as parity-passed")
        if marker.get("model_sha256") != actual_digest:
            raise OnnxArtifactError("Stale marker: model digest does not match")
    elif not allow_unvalidated:
        raise OnnxArtifactError(
            "Model artifact has no VALIDATED.json marker. Re-export with parity checks."
        )

    if manifest is not None:
        if manifest.get("validated") is True and marker is None:
            raise OnnxArtifactError("Manifest claims validation but VALIDATED.json is missing")
        if manifest.get("model_sha256") != actual_digest:
            raise OnnxArtifactError("Export manifest model digest does not match")
        if manifest.get("model_bytes") != actual_bytes:
            raise OnnxArtifactError("Export manifest model size does not match")
        if manifest.get("ruleset") != ruleset:
            raise OnnxArtifactError("Export manifest ruleset does not match the sidecar")
        source_metadata = sidecar.get("source") or {}
        if manifest.get("source_weights_sha256") != source_metadata.get("weights_sha256"):
            raise OnnxArtifactError("Export manifest source-weight fingerprint does not match")
        if manifest.get("checkpoint_sha256") != source_metadata.get("sha256"):
            raise OnnxArtifactError("Export manifest checkpoint digest does not match")
    if marker is not None and marker.get("ruleset") != ruleset:
        raise OnnxArtifactError("VALIDATED.json ruleset does not match the sidecar")

    actual_sidecar = sha256(model_json)
    if manifest is not None and manifest.get("sidecar_sha256") != actual_sidecar:
        raise OnnxArtifactError("Metadata sidecar digest does not match the export manifest")
    if marker is not None and marker.get("sidecar_sha256") != actual_sidecar:
        raise OnnxArtifactError("Metadata sidecar digest does not match VALIDATED.json")
    validated = marker is not None
    parity_path = model_dir / "parity.json"
    if validated:
        if manifest is None or marker is None:
            raise OnnxArtifactError("Validated export is missing its manifest or marker")
        if not parity_path.is_file():
            raise OnnxArtifactError("Validated export is missing its parity report")
        actual_parity = sha256(parity_path)
        if manifest.get("parity_sha256") != actual_parity:
            raise OnnxArtifactError("Parity report digest does not match the export manifest")
        if marker.get("parity_sha256") != actual_parity:
            raise OnnxArtifactError("Parity report digest does not match VALIDATED.json")
    elif parity_path.is_file():
        raise OnnxArtifactError("Unvalidated export must not contain a stale parity report")

    graph = validate_graph(model_onnx, sidecar)

    try:
        reference = json.loads(reference_json.read_text())
    except (OSError, ValueError) as error:
        raise OnnxArtifactError(f"reference.json is unreadable: {error}") from error
    reference_digest = sha256(reference_json)
    sidecar_reference = sidecar.get("reference") or {}
    if sidecar_reference.get("sha256") != reference_digest:
        raise OnnxArtifactError("Reference-vector digest does not match the sidecar")
    if manifest is not None and manifest.get("reference_sha256") != reference_digest:
        raise OnnxArtifactError("Export manifest reference-vector digest does not match")
    if marker is not None and marker.get("reference_sha256") != reference_digest:
        raise OnnxArtifactError("VALIDATED.json reference-vector digest does not match")
    if reference.get("ruleset") != ruleset:
        raise OnnxArtifactError("Reference vectors were generated for a different ruleset")
    if reference.get("model_sha256") != actual_digest:
        raise OnnxArtifactError("Reference vectors were generated for different model bytes")
    if not reference.get("cases"):
        raise OnnxArtifactError("Reference vectors contain no cases")
    if reference.get("schema") != 2:
        raise OnnxArtifactError(f"Unsupported reference schema {reference.get('schema')!r}")

    return {
        "sidecar": sidecar,
        "reference": reference,
        "ruleset": ruleset,
        "size": size,
        "digest": actual_digest,
        "model_bytes": actual_bytes,
        "reference_digest": reference_digest,
        "sidecar_digest": actual_sidecar,
        "manifest": manifest,
        "marker": marker,
        "graph": graph,
        "validated": validated,
    }
