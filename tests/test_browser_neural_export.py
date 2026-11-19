"""Structural tests for the experimental ONNX export tooling.

These use a tiny synthetic checkpoint to validate the export/metadata/parity
plumbing only. They are not trained-model strength or browser evidence, and they
skip when the optional exporter/runtime dependencies are not installed. The
browser and actual-trained-checkpoint gates stay explicit opt-in commands.
"""

import json
import sys
from pathlib import Path

import pytest

SPIKE = Path(__file__).resolve().parents[1] / "spikes/browser_offline"
if str(SPIKE) not in sys.path:
    sys.path.insert(0, str(SPIKE))

from hnefatafl.models import PolicyValueNet, save_checkpoint  # noqa: E402


def _checkpoint(tmp_path: Path, ruleset: str = "brandubh") -> Path:
    model = PolicyValueNet(ruleset=ruleset, channels=4, blocks=0)
    path = tmp_path / "source.pt"
    save_checkpoint(path, model, {"ruleset": ruleset, "name": "structural-test"})
    return path


def test_onnx_export_metadata_and_native_parity(tmp_path):
    pytest.importorskip("onnx")
    pytest.importorskip("onnxruntime")
    import onnx
    import onnxruntime as ort

    import onnx_export

    source = _checkpoint(tmp_path)
    output = tmp_path / "export"
    exported = onnx_export.export(source, output, opset=18, force=False)
    sidecar = exported["sidecar"]
    assert sidecar["ruleset"] == "brandubh"
    assert sidecar["model_config"] == {"ruleset": "brandubh", "channels": 4, "blocks": 0}
    assert sidecar["input"]["shape"] == ["batch", 6, 7, 7]
    assert sidecar["input"]["dynamic_batch"] is True
    assert sidecar["outputs"]["policy_logits"]["shape"] == ["batch", 2401]
    assert sidecar["onnx"]["checker"] == "passed"
    assert sidecar["onnx"]["shape_inference"] == "passed"
    assert sidecar["source"]["sha256"] == onnx_export.digest(source)
    assert (output / "model.onnx").is_file()
    assert not list(output.glob("*.onnx.data"))
    # The graph must accept a dynamic batch for MCTS leaf batching.
    graph = onnx.load(str(output / "model.onnx"))
    onnx.checker.check_model(graph)
    session = ort.InferenceSession(
        str(output / "model.onnx"),
        providers=["CPUExecutionProvider"],
        sess_options=onnx_export._single_thread_options(),
    )
    import numpy as np

    for batch in (1, 2, 8):
        planes = np.zeros((batch, 6, 7, 7), dtype=np.float32)
        policy, value = session.run(None, {session.get_inputs()[0].name: planes})
        assert policy.shape == (batch, 2401)
        assert value.shape == (batch,)

    report = onnx_export.parity(exported["model"], output / "model.onnx")
    assert report["cases"] == 6
    assert report["passes"] is True
    assert report["top5_all_agree"] is True
    assert report["worst_logit_max_abs_error"] <= report["tolerances"]["logit_atol"]
    assert report["worst_value_abs_error"] <= report["tolerances"]["value_atol"]


def test_onnx_export_refuses_to_overwrite_and_keeps_source(tmp_path):
    pytest.importorskip("onnx")
    import onnx_export

    source = _checkpoint(tmp_path)
    before = source.read_bytes()
    output = tmp_path / "export"
    onnx_export.export(source, output, opset=18, force=False)
    with pytest.raises(SystemExit, match="Refusing to replace"):
        onnx_export.export(source, output, opset=18, force=False)
    assert source.read_bytes() == before


def test_onnx_export_rejects_invalid_checkpoint(tmp_path):
    pytest.importorskip("onnx")
    import onnx_export

    bad = tmp_path / "bad.pt"
    bad.write_bytes(b"not a checkpoint")
    with pytest.raises(ValueError):
        onnx_export.export(bad, tmp_path / "export", opset=18, force=False)


def test_prepare_neural_refuses_missing_model(tmp_path):
    import subprocess

    result = subprocess.run(
        [
            sys.executable,
            str(SPIKE / "prepare_neural.py"),
            "--model-dir",
            str(tmp_path / "missing"),
            "--output",
            str(tmp_path / "out"),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "model.onnx" in result.stderr


def test_export_metadata_is_json_serialisable(tmp_path):
    pytest.importorskip("onnx")
    import onnx_export

    source = _checkpoint(tmp_path)
    output = tmp_path / "export"
    sidecar = onnx_export.export(source, output, opset=18, force=False)["sidecar"]
    # No filesystem path, optimizer state or training payload may leak into the
    # distributable metadata sidecar.
    serialised = json.dumps(sidecar)
    assert str(source) not in serialised
    assert "optimizer" not in serialised
    assert "state_dict" not in serialised
