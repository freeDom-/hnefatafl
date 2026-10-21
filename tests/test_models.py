"""Networks perform real forward/backward passes; imports accept tensor-only files."""

from pathlib import Path

import pytest
import torch

from hnefatafl.engine import new_game
from hnefatafl.models import (
    ModelRegistry,
    PolicyValueNet,
    device_info,
    encode_state,
    export_checkpoint,
    load_checkpoint,
    resolve_device,
    save_checkpoint,
)


@pytest.mark.parametrize(
    "ruleset,size,attackers,defenders",
    [
        ("brandubh", 7, 8, 4),
        ("tablut", 9, 16, 8),
        ("copenhagen", 11, 24, 12),
    ],
)
def test_encode_and_forward_backward(ruleset, size, attackers, defenders):
    state = new_game(ruleset)
    encoded = encode_state(state)
    assert encoded.shape == (6, size, size)
    assert encoded.dtype == torch.float32
    assert encoded[0].sum().item() == attackers
    assert encoded[1].sum().item() == defenders
    assert encoded[2].sum().item() == 1
    assert encoded[3].sum().item() == (size * size if state.turn == "defenders" else 0)
    assert encoded[4].sum().item() == 1
    assert encoded[5].sum().item() == (0 if ruleset == "tablut" else 4)
    model = PolicyValueNet(ruleset, channels=8, blocks=1)
    optimizer = torch.optim.Adam(model.parameters(), lr=0.001)
    first = next(model.parameters()).detach().clone()
    policy, value = model(encoded.unsqueeze(0))
    assert policy.shape == (1, size**4)
    assert value.shape == (1,)
    assert torch.isfinite(policy).all()
    assert -1 <= value.item() <= 1
    loss = (
        torch.nn.functional.cross_entropy(policy, torch.tensor([0])) + (value - 1).square().mean()
    )
    loss.backward()
    optimizer.step()
    assert not torch.equal(first, next(model.parameters()).detach())
    with pytest.raises(ValueError, match="shape"):
        model(torch.zeros(1, 6, size + 1, size + 1))


def test_checkpoint_preserves_weights_optimizer_and_safe_extra(tmp_path):
    model = PolicyValueNet("brandubh", channels=8, blocks=0)
    optimizer = torch.optim.AdamW(model.parameters(), lr=0.003)
    encoded = encode_state(new_game("brandubh")).unsqueeze(0)
    policy, value = model(encoded)
    (policy.square().mean() + value.square().mean()).backward()
    optimizer.step()
    model.eval()
    before = model(encoded)
    path = save_checkpoint(
        tmp_path / "model.pt",
        model,
        {"name": "First", "ruleset": "brandubh"},
        optimizer,
        {"iteration": 1, "rng": torch.get_rng_state(), "items": [1, "a", None]},
    )
    loaded, payload = load_checkpoint(path)
    after = loaded(encoded)
    assert all(torch.equal(left, right) for left, right in zip(before, after))
    assert payload["optimizer_state"]["state"]
    assert payload["extra"]["iteration"] == 1
    assert torch.equal(payload["extra"]["rng"], torch.get_rng_state())
    assert payload["metadata"]["name"] == "First"
    assert payload["model_config"] == {"ruleset": "brandubh", "channels": 8, "blocks": 0}
    with pytest.raises(ValueError, match="Unsupported checkpoint value type"):
        save_checkpoint(tmp_path / "bad.pt", model, {"unsafe": object()})
    assert not (tmp_path / "bad.pt").exists()


def test_registry_is_content_addressed_and_reimport_repairs(tmp_path):
    model = PolicyValueNet("tablut", channels=4, blocks=0)
    path = save_checkpoint(
        tmp_path / "source.pt",
        model,
        {
            "ruleset": "tablut",
            "version": 2,
            "metrics": {"wins": 1},
            "training_config": {"seed": 3},
            "evaluation": {"games": 2},
        },
    )
    registry = ModelRegistry(tmp_path / "registry")
    first = registry.register(path, "Trained Tablut")
    assert first["name"] == "Trained Tablut"
    assert first["ruleset"] == "tablut"
    assert first["version"] == 2
    assert first["metrics"] == {"wins": 1}
    assert Path(first["path"]).is_file()
    assert registry.get(first["id"])["id"] == first["id"]
    Path(first["path"]).unlink()
    assert registry.import_model(path)["id"] == first["id"]
    assert Path(first["path"]).is_file()
    assert len(registry.list()) == 1
    with pytest.raises(ValueError, match="Invalid model ID"):
        registry.get("../../source")
    with pytest.raises(ValueError, match="not registered"):
        registry.get("0" * 24)
    with pytest.raises(ValueError, match="name"):
        registry.import_model(path, "")


@pytest.mark.parametrize(
    "change,match",
    [
        ("version", "version"),
        ("architecture", "architecture"),
        ("ruleset", "Unknown model ruleset"),
        ("shape", "shapes"),
        ("dtype", "dtypes"),
        ("nan", "non-finite"),
        ("metadata_ruleset", "ruleset"),
    ],
)
def test_incompatible_checkpoint_rejected(tmp_path, change, match):
    original = save_checkpoint(tmp_path / "original.pt", PolicyValueNet("brandubh", 4, 0), {})
    payload = torch.load(original, weights_only=True)
    if change == "version":
        payload["format_version"] = 99
    elif change == "architecture":
        payload["architecture"] = "unknown"
    elif change == "ruleset":
        payload["model_config"]["ruleset"] = "unknown"
    elif change == "metadata_ruleset":
        payload["metadata"]["ruleset"] = "copenhagen"
    else:
        key = next(iter(payload["state_dict"]))
        if change == "shape":
            payload["state_dict"][key] = torch.zeros(1)
        elif change == "dtype":
            payload["state_dict"][key] = payload["state_dict"][key].to(torch.float64)
        elif change == "nan":
            payload["state_dict"][key].flatten()[0] = float("nan")
    path = tmp_path / "invalid.pt"
    torch.save(payload, path)
    with pytest.raises(ValueError, match=match):
        load_checkpoint(path)
    with pytest.raises(ValueError):
        ModelRegistry(tmp_path / "models").import_model(path)
    assert ModelRegistry(tmp_path / "models").list() == []


def test_noncheckpoint_and_unsafe_pickle_are_rejected(tmp_path):
    path = tmp_path / "not.pt"
    path.write_text("this is not a neural network", encoding="utf-8")
    with pytest.raises(ValueError, match="format"):
        load_checkpoint(path)
    # pathlib objects require an unsafe global in pickle, so weights_only rejects them.
    torch.save({"path": Path("not-authorized")}, path)
    with pytest.raises(ValueError, match="safe tensor-only"):
        load_checkpoint(path)
    with pytest.raises(ValueError, match="missing"):
        load_checkpoint(tmp_path / "missing.pt")


def test_device_selection_reports_actual_capabilities():
    info = device_info()
    assert resolve_device("cpu").type == "cpu"
    assert info["default_device"] in ("cpu", "cuda", "mps")
    assert isinstance(info["cuda_available"], bool)
    with pytest.raises(ValueError, match="device must"):
        resolve_device("invisible-device")
    if not info["cuda_available"]:
        with pytest.raises(ValueError, match="CUDA"):
            resolve_device("cuda")
    if not info["mps_available"]:
        with pytest.raises(ValueError, match="MPS"):
            resolve_device("mps")


def test_oversized_sparse_checkpoint_is_rejected_before_loading(tmp_path):
    from hnefatafl.models import MAX_CHECKPOINT_BYTES

    path = tmp_path / "too-large.pt"
    with path.open("wb") as stream:
        stream.seek(MAX_CHECKPOINT_BYTES)
        stream.write(b"x")
    with pytest.raises(ValueError, match="size limit"):
        load_checkpoint(path)
    with pytest.raises(ValueError, match="too large"):
        ModelRegistry(tmp_path / "registry").import_model(path)


def test_inference_export_preserves_predictions_and_original_resume_data(tmp_path):
    model = PolicyValueNet("brandubh", channels=8, blocks=0)
    optimizer = torch.optim.Adam(model.parameters(), lr=0.001)
    encoded = encode_state(new_game("brandubh")).unsqueeze(0)
    logits, value = model(encoded)
    (logits.square().mean() + (value - 1).square().mean()).backward()
    optimizer.step()
    checkpoint = save_checkpoint(
        tmp_path / "resume.pt",
        model,
        {"name": "Ready to play", "ruleset": "brandubh", "metrics": {"games": 2}},
        optimizer,
        {"iteration": 3, "replay": torch.rand(16, 6, 7, 7), "rng": torch.get_rng_state()},
    )
    exported = export_checkpoint(checkpoint, tmp_path / "inference.pt")
    original_model, original = load_checkpoint(checkpoint)
    inference_model, inference = load_checkpoint(exported)
    with torch.inference_mode():
        before = original_model(encoded)
        after = inference_model(encoded)
    assert all(torch.equal(left, right) for left, right in zip(before, after))
    assert inference["metadata"] == original["metadata"]
    assert inference["optimizer_state"] is None
    assert inference["extra"] == {}
    assert original["optimizer_state"]["state"]
    assert original["extra"]["iteration"] == 3
    assert original["extra"]["replay"].shape == (16, 6, 7, 7)
    assert exported.stat().st_size < checkpoint.stat().st_size
    registered = ModelRegistry(tmp_path / "registry").register(exported)
    assert registered["name"] == "Ready to play"
    assert registered["metrics"] == {"games": 2}
    with pytest.raises(ValueError, match="different path"):
        export_checkpoint(checkpoint, checkpoint)
    assert load_checkpoint(checkpoint)[1]["optimizer_state"]["state"]


@pytest.mark.parametrize(
    "metadata,field",
    [
        ({"version": {"unexpected": "object"}}, "version"),
        ({"version": []}, "version"),
        ({"version": True}, "version"),
        ({"version": -1}, "version"),
        ({"version": " "}, "version"),
        ({"version": "v" * 65}, "version"),
        ({"created_at": {"invalid": "date"}}, "created_at"),
        ({"created_at": "not-a-date"}, "created_at"),
        ({"created_at": None}, "created_at"),
        ({"name": ["not", "a", "name"]}, "name"),
        ({"name": ""}, "name"),
        ({"name": "x" * 101}, "name"),
        ({"metrics": [1]}, "metrics"),
        ({"training_config": []}, "training_config"),
        ({"evaluation": "bad"}, "evaluation"),
    ],
)
def test_invalid_public_model_metadata_rejected_on_save_and_import(tmp_path, metadata, field):
    model = PolicyValueNet("brandubh", channels=4, blocks=0)
    with pytest.raises(ValueError, match=field):
        save_checkpoint(tmp_path / "rejected.pt", model, metadata)
    assert not (tmp_path / "rejected.pt").exists()
    valid = save_checkpoint(tmp_path / "valid.pt", model, {})
    payload = torch.load(valid, weights_only=True)
    payload["metadata"].update(metadata)
    invalid = tmp_path / "invalid.pt"
    torch.save(payload, invalid)
    with pytest.raises(ValueError, match=field):
        load_checkpoint(invalid)
    registry = ModelRegistry(tmp_path / "registry")
    with pytest.raises(ValueError, match=field):
        registry.import_model(invalid)
    assert registry.list() == []


@pytest.mark.parametrize("version", [0, 2, "v1.2.0-beta.1"])
def test_valid_public_metadata_round_trips(tmp_path, version):
    metadata = {
        "version": version,
        "name": "A trained model",
        "created_at": "2026-10-21T02:00:00Z",
        "ruleset": "brandubh",
        "metrics": {"score": 0.5},
        "training_config": {"seed": 7},
        "evaluation": {"games": 2},
    }
    path = save_checkpoint(tmp_path / "valid.pt", PolicyValueNet("brandubh", 4, 0), metadata)
    assert load_checkpoint(path)[1]["metadata"] == metadata
    entry = ModelRegistry(tmp_path / "registry").register(path)
    assert entry["version"] == version
    assert entry["created_at"] == metadata["created_at"]
    assert entry["name"] == metadata["name"]
