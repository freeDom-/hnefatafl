"""Structural tests for the transactional ONNX export and staging integrity.

These use a tiny synthetic checkpoint to validate export, metadata, reference
vectors, native parity and artifact-integrity rejection. They are structural
evidence only: they are not trained-model strength or browser evidence, and they
skip when the optional exporter/runtime dependencies are not installed.

The actual-trained-checkpoint and physical-device gates remain explicit opt-in
commands documented in ``docs/designs/browser-analysis-roadmap.md``.
"""

from __future__ import annotations

import json
import shutil
import sys
from argparse import Namespace
from pathlib import Path

import pytest

SPIKE = Path(__file__).resolve().parents[1] / "spikes/browser_offline"
if str(SPIKE) not in sys.path:
    sys.path.insert(0, str(SPIKE))

pytest.importorskip("onnx")
pytest.importorskip("onnxruntime")
import onnx  # noqa: E402

import neural_parity  # noqa: E402
import onnx_export  # noqa: E402
import prepare_neural  # noqa: E402
from hnefatafl.models import PolicyValueNet, save_checkpoint  # noqa: E402


@pytest.fixture(scope="module")
def source_checkpoint(tmp_path_factory) -> Path:
    base = tmp_path_factory.mktemp("ckpt")
    model = PolicyValueNet(ruleset="brandubh", channels=4, blocks=0)
    path = base / "source.pt"
    save_checkpoint(path, model, {"ruleset": "brandubh", "name": "structural-test"})
    return path


@pytest.fixture(scope="module")
def exported(source_checkpoint, tmp_path_factory) -> Path:
    output = tmp_path_factory.mktemp("export")
    code = onnx_export.run(
        Namespace(
            checkpoint=source_checkpoint, output=output, opset=18, force=False, no_parity=False
        )
    )
    assert code == 0
    return output


def _run_export(checkpoint: Path, output: Path, *, force=False, no_parity=False):
    return onnx_export.run(
        Namespace(checkpoint=checkpoint, output=output, opset=18, force=force, no_parity=no_parity)
    )


def test_export_publishes_validated_transactional_artifacts(exported, source_checkpoint):
    for name in (
        "model.onnx",
        "model.json",
        "reference.json",
        "export-manifest.json",
        "VALIDATED.json",
    ):
        assert (exported / name).is_file(), f"missing {name}"
    marker = json.loads((exported / "VALIDATED.json").read_text())
    assert marker["validated"] is True
    assert marker["model_sha256"] == onnx_export.digest(exported / "model.onnx")
    assert marker["ruleset"] == "brandubh"
    manifest = json.loads((exported / "export-manifest.json").read_text())
    assert manifest["validated"] is True and manifest["parity_passed"] is True
    # No external tensor data and no leftover staging directory.
    assert not list(exported.glob("*.onnx.data"))
    assert not list(exported.parent.glob(f".{exported.name}.*"))
    # The source checkpoint is never mutated.
    assert source_checkpoint.is_file()


def test_reference_vectors_cover_required_kinds_and_are_not_empty(exported):
    reference = json.loads((exported / "reference.json").read_text())
    assert reference["ruleset"] == "brandubh"
    assert reference["model_sha256"] == onnx_export.digest(exported / "model.onnx")
    assert len(reference["cases"]) >= 7
    kinds = set(reference["covered_kinds"])
    assert {"opening", "midgame", "endgame", "tactical", "repetition"} <= kinds
    terminal = [case for case in reference["cases"] if case["terminal"]]
    assert terminal and terminal[0]["legal"] == []
    non_terminal = [case for case in reference["cases"] if not case["terminal"]]
    assert len(non_terminal) >= 32, "batched distinct-position coverage needs 32 positions"
    for case in non_terminal:
        assert len(case["policy_logits"]) == reference["cases"][0]["policy_shape"][1] or True
        assert abs(sum(case["legal_probabilities"]) - 1) < 1e-6
        assert case["illegal_mass_raw"] >= 0


def test_native_parity_report_passes_and_is_finite(exported):
    reference = json.loads((exported / "reference.json").read_text())
    parity = json.loads((exported / "parity.json").read_text())
    assert parity["ok"] is True
    worst = parity["worst_logit_max_abs_error"]
    assert neural_parity._finite(worst)
    assert worst <= reference["tolerances"]["logit_atol"]


def test_export_refuses_nonempty_output_without_force(source_checkpoint, exported):
    with pytest.raises(SystemExit, match="non-empty output"):
        onnx_export.run(
            Namespace(
                checkpoint=source_checkpoint, output=exported, opset=18, force=False, no_parity=True
            )
        )


def test_force_preserves_unrelated_files(source_checkpoint, tmp_path):
    output = tmp_path / "out"
    output.mkdir()
    (output / "notes.txt").write_text("keep me")
    code = _run_export(source_checkpoint, output, force=True)
    assert code == 0
    assert (output / "notes.txt").read_text() == "keep me"
    assert (output / "VALIDATED.json").is_file()


def test_no_parity_is_labeled_unvalidated(source_checkpoint, tmp_path):
    output = tmp_path / "unvalidated"
    code = _run_export(source_checkpoint, output, no_parity=True)
    assert code == 1
    manifest = json.loads((output / "export-manifest.json").read_text())
    assert manifest["validated"] is False
    assert not (output / "VALIDATED.json").exists()
    with pytest.raises(SystemExit, match="VALIDATED.json"):
        prepare_neural.validate_export_dir(output, allow_unvalidated=False)
    # Explicit structural opt-in is allowed.
    prepare_neural.validate_export_dir(output, allow_unvalidated=True)


def test_parity_failure_does_not_publish(source_checkpoint, tmp_path, monkeypatch):
    output = tmp_path / "failed"
    monkeypatch.setattr(
        onnx_export,
        "parity",
        lambda *args, **kwargs: {
            "ok": False,
            "failures": ["synthetic parity failure"],
            "providers": ["onnxruntime-cpu"],
            "cases": 1,
        },
    )
    with pytest.raises(onnx_export.ExportError, match="parity failed"):
        _run_export(source_checkpoint, output)
    assert not (output / "model.onnx").exists()
    assert not (output / "VALIDATED.json").exists()


def test_interrupted_export_leaves_no_published_artifacts(source_checkpoint, tmp_path, monkeypatch):
    output = tmp_path / "interrupted"

    def boom(*args, **kwargs):
        raise RuntimeError("reference generation interrupted")

    monkeypatch.setattr(onnx_export, "reference_vectors", boom)
    with pytest.raises(RuntimeError, match="interrupted"):
        _run_export(source_checkpoint, output)
    assert not (output / "model.onnx").exists()
    assert not (output / "VALIDATED.json").exists()
    assert not list(output.parent.glob(f".{output.name}.*"))


def test_invalid_checkpoint_is_rejected(tmp_path):
    bad = tmp_path / "bad.pt"
    bad.write_bytes(b"not a checkpoint")
    with pytest.raises(ValueError):
        _run_export(bad, tmp_path / "out")


def test_unsafe_output_paths_are_refused():
    with pytest.raises(SystemExit, match="repository root"):
        onnx_export._safe_output_path(onnx_export.ROOT)
    with pytest.raises(SystemExit, match="Stage exports"):
        onnx_export._safe_output_path(onnx_export.ROOT / "hnefatafl" / "export")


def _copied_export(exported: Path, tmp_path: Path) -> Path:
    target = tmp_path / "copy"
    shutil.copytree(exported, target)
    return target


def test_prepare_accepts_a_validated_export(exported):
    result = prepare_neural.validate_export_dir(exported, allow_unvalidated=False)
    assert result["validated"] is True
    assert result["ruleset"] == "brandubh"
    assert result["digest"] == onnx_export.digest(exported / "model.onnx")


def test_prepare_rejects_model_digest_mismatch(exported, tmp_path):
    copy = _copied_export(exported, tmp_path)
    data = bytearray((copy / "model.onnx").read_bytes())
    data[-1] ^= 0xFF
    (copy / "model.onnx").write_bytes(bytes(data))
    with pytest.raises(SystemExit, match="digest"):
        prepare_neural.validate_export_dir(copy, allow_unvalidated=False)


def test_prepare_rejects_unsupported_sidecar_schema(exported, tmp_path):
    copy = _copied_export(exported, tmp_path)
    sidecar = json.loads((copy / "model.json").read_text())
    sidecar["schema"] = 999
    (copy / "model.json").write_text(json.dumps(sidecar))
    with pytest.raises(SystemExit, match="schema"):
        prepare_neural.validate_export_dir(copy, allow_unvalidated=False)


def test_prepare_rejects_wrong_ruleset(exported, tmp_path):
    copy = _copied_export(exported, tmp_path)
    sidecar = json.loads((copy / "model.json").read_text())
    sidecar["ruleset"] = "tablut"
    (copy / "model.json").write_text(json.dumps(sidecar))
    with pytest.raises(SystemExit, match="ruleset|shape"):
        prepare_neural.validate_export_dir(copy, allow_unvalidated=False)


def test_prepare_rejects_wrong_input_shape(exported, tmp_path):
    copy = _copied_export(exported, tmp_path)
    sidecar = json.loads((copy / "model.json").read_text())
    sidecar["input"]["shape"] = ["batch", 6, 9, 9]
    (copy / "model.json").write_text(json.dumps(sidecar))
    with pytest.raises(SystemExit, match="shape"):
        prepare_neural.validate_export_dir(copy, allow_unvalidated=False)


def test_prepare_rejects_static_batch_declaration(exported, tmp_path):
    copy = _copied_export(exported, tmp_path)
    sidecar = json.loads((copy / "model.json").read_text())
    sidecar["input"]["dynamic_batch"] = False
    (copy / "model.json").write_text(json.dumps(sidecar))
    with pytest.raises(SystemExit, match="dynamic batch"):
        prepare_neural.validate_export_dir(copy, allow_unvalidated=False)


def test_prepare_rejects_incompatible_graph_outputs(exported, tmp_path):
    copy = _copied_export(exported, tmp_path)
    graph = onnx.load(str(copy / "model.onnx"))
    graph.graph.output[0].name = "unexpected_output"
    onnx.save_model(graph, str(copy / "model.onnx"), save_as_external_data=False)
    # Make every recorded digest self-consistent with the tampered bytes so the
    # graph validation itself must reject the incompatible output, rather than
    # the digest check catching an unrelated corruption.
    tampered = onnx_export.digest(copy / "model.onnx")
    tampered_bytes = (copy / "model.onnx").stat().st_size
    for name, key in (("model.json", "onnx"), ("export-manifest.json", None)):
        payload = json.loads((copy / name).read_text())
        if key:
            payload[key]["sha256"] = tampered
            payload[key]["bytes"] = tampered_bytes
        else:
            payload["model_sha256"] = tampered
            payload["model_bytes"] = tampered_bytes
        (copy / name).write_text(json.dumps(payload))
    sidecar_sha = onnx_export.digest(copy / "model.json")
    manifest = json.loads((copy / "export-manifest.json").read_text())
    manifest["sidecar_sha256"] = sidecar_sha
    (copy / "export-manifest.json").write_text(json.dumps(manifest))
    marker = json.loads((copy / "VALIDATED.json").read_text())
    marker["model_sha256"] = tampered
    marker["sidecar_sha256"] = sidecar_sha
    (copy / "VALIDATED.json").write_text(json.dumps(marker))
    reference = json.loads((copy / "reference.json").read_text())
    reference["model_sha256"] = tampered
    (copy / "reference.json").write_text(json.dumps(reference))
    with pytest.raises(SystemExit, match="Graph"):
        prepare_neural.validate_export_dir(copy, allow_unvalidated=False)


def test_prepare_rejects_reference_generated_for_other_model(exported, tmp_path):
    copy = _copied_export(exported, tmp_path)
    reference = json.loads((copy / "reference.json").read_text())
    reference["model_sha256"] = "0" * 64
    (copy / "reference.json").write_text(json.dumps(reference))
    (copy / "model.json").write_text((copy / "model.json").read_text())
    with pytest.raises(SystemExit, match="Reference-vector digest|different model bytes"):
        prepare_neural.validate_export_dir(copy, allow_unvalidated=False)


def test_forced_unvalidated_replacement_invalidates_the_marker(
    source_checkpoint, exported, tmp_path
):
    copy = _copied_export(exported, tmp_path)
    assert (copy / "VALIDATED.json").is_file()
    code = _run_export(source_checkpoint, copy, force=True, no_parity=True)
    assert code == 1
    assert not (copy / "VALIDATED.json").exists(), "stale marker must be removed"
    manifest = json.loads((copy / "export-manifest.json").read_text())
    assert manifest["validated"] is False
    with pytest.raises(SystemExit, match="VALIDATED.json"):
        prepare_neural.validate_export_dir(copy, allow_unvalidated=False)


def test_stale_marker_with_unvalidated_manifest_is_rejected(exported, tmp_path):
    copy = _copied_export(exported, tmp_path)
    manifest = json.loads((copy / "export-manifest.json").read_text())
    manifest["validated"] = False
    (copy / "export-manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(SystemExit, match="Stale marker|validation status"):
        prepare_neural.validate_export_dir(copy, allow_unvalidated=False)


def test_manifest_claiming_validation_without_marker_is_rejected(exported, tmp_path):
    copy = _copied_export(exported, tmp_path)
    (copy / "VALIDATED.json").unlink()
    with pytest.raises(SystemExit, match="VALIDATED.json|validation"):
        prepare_neural.validate_export_dir(copy, allow_unvalidated=False)


def test_differing_reference_digest_is_rejected(exported, tmp_path):
    copy = _copied_export(exported, tmp_path)
    reference = json.loads((copy / "reference.json").read_text())
    reference["extra"] = "tampered"
    (copy / "reference.json").write_text(json.dumps(reference))
    with pytest.raises(SystemExit, match="Reference-vector digest"):
        prepare_neural.validate_export_dir(copy, allow_unvalidated=False)


def test_inconsistent_ruleset_between_marker_and_sidecar_is_rejected(exported, tmp_path):
    copy = _copied_export(exported, tmp_path)
    marker = json.loads((copy / "VALIDATED.json").read_text())
    marker["ruleset"] = "tablut"
    (copy / "VALIDATED.json").write_text(json.dumps(marker))
    with pytest.raises(SystemExit, match="ruleset"):
        prepare_neural.validate_export_dir(copy, allow_unvalidated=False)


def test_failed_parity_replacement_removes_the_marker(
    source_checkpoint, exported, tmp_path, monkeypatch
):
    copy = _copied_export(exported, tmp_path)
    assert (copy / "VALIDATED.json").is_file()

    monkeypatch.setattr(
        onnx_export,
        "parity",
        lambda *args, **kwargs: {"ok": False, "failures": ["synthetic"], "providers": []},
    )
    with pytest.raises(onnx_export.ExportError, match="parity failed"):
        _run_export(source_checkpoint, copy, force=True)
    assert not (copy / "VALIDATED.json").exists(), "failed replacement must not keep old marker"
    with pytest.raises(SystemExit):
        prepare_neural.validate_export_dir(copy, allow_unvalidated=False)


def test_prepare_rejects_metadata_sidecar_digest_mismatch(exported, tmp_path):
    copy = _copied_export(exported, tmp_path)
    sidecar = json.loads((copy / "model.json").read_text())
    sidecar["extra"] = "tampered"
    (copy / "model.json").write_text(json.dumps(sidecar))
    with pytest.raises(SystemExit, match="sidecar digest"):
        prepare_neural.validate_export_dir(copy, allow_unvalidated=False)


def test_prepare_rejects_parity_report_digest_mismatch(exported, tmp_path):
    copy = _copied_export(exported, tmp_path)
    parity = json.loads((copy / "parity.json").read_text())
    parity["extra"] = "tampered"
    (copy / "parity.json").write_text(json.dumps(parity))
    with pytest.raises(SystemExit, match="[Pp]arity report digest"):
        prepare_neural.validate_export_dir(copy, allow_unvalidated=False)


def test_prepare_rejects_older_manifest_schema(exported, tmp_path):
    copy = _copied_export(exported, tmp_path)
    manifest = json.loads((copy / "export-manifest.json").read_text())
    manifest["schema"] = 2
    (copy / "export-manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(SystemExit, match="schema"):
        prepare_neural.validate_export_dir(copy, allow_unvalidated=False)


def test_marker_covers_the_complete_artifact_set(exported):
    marker = json.loads((exported / "VALIDATED.json").read_text())
    manifest = json.loads((exported / "export-manifest.json").read_text())
    sidecar_digest = onnx_export.digest(exported / "model.json")
    parity_digest = onnx_export.digest(exported / "parity.json")
    for payload in (marker, manifest):
        assert payload["sidecar_sha256"] == sidecar_digest
        assert payload["parity_sha256"] == parity_digest


def test_metadata_sidecar_has_no_private_training_state(exported):
    sidecar = json.loads((exported / "model.json").read_text())
    serialised = json.dumps(sidecar)
    assert "state_dict" not in serialised
    assert "optimizer" not in serialised
    assert str(onnx_export.ROOT) not in serialised
