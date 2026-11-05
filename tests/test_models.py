"""Networks perform real forward/backward passes; imports accept tensor-only files."""

from pathlib import Path
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor

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


def test_registry_delete_preserves_original_checkpoints_and_can_reimport(tmp_path):
    source = save_checkpoint(
        tmp_path / "training" / "last.pt", PolicyValueNet("brandubh", 4, 0), {"name": "Astrid"}
    )
    original = source.read_bytes()
    registry = ModelRegistry(tmp_path / "models")
    model = registry.register(source)
    unrelated = registry.root / "readme.txt"
    unrelated.write_text("Retain this unrelated file")
    deleted = registry.delete(model["id"])
    assert deleted["id"] == model["id"]
    assert deleted["name"] == "Astrid"
    assert registry.list() == []
    assert json.loads(registry.index_path.read_text()) == []
    assert not Path(model["path"]).exists()
    assert source.read_bytes() == original
    assert unrelated.read_text() == "Retain this unrelated file"
    assert registry.import_model(source)["id"] == model["id"]
    assert Path(registry.get(model["id"])["path"]).is_file()


@pytest.mark.parametrize("identifier", ["../outside", "", "0" * 24, None])
def test_registry_delete_rejects_missing_or_invalid_ids_without_changing_models(
    tmp_path, identifier
):
    source = save_checkpoint(tmp_path / "last.pt", PolicyValueNet("brandubh", 4, 0), {})
    registry = ModelRegistry(tmp_path / "models")
    model = registry.register(source)
    before = registry.index_path.read_bytes()
    with pytest.raises(ValueError, match="Invalid model ID|not registered"):
        registry.delete(identifier)
    assert registry.index_path.read_bytes() == before
    assert Path(model["path"]).is_file()


def test_registry_delete_removes_stale_metadata_without_following_symlinks(tmp_path):
    source = save_checkpoint(tmp_path / "last.pt", PolicyValueNet("brandubh", 4, 0), {})
    registry = ModelRegistry(tmp_path / "models")
    model = registry.register(source)
    owned = Path(model["path"])
    owned.unlink()
    registry.delete(model["id"])
    assert registry.list() == []
    registry.import_model(source)
    owned.unlink()
    owned.symlink_to(source)
    before = source.read_bytes()
    registry.delete(model["id"])
    assert not owned.is_symlink()
    assert registry.list() == []
    assert source.read_bytes() == before


def test_registry_delete_keeps_weights_when_index_update_fails(tmp_path, monkeypatch):
    source = save_checkpoint(tmp_path / "last.pt", PolicyValueNet("brandubh", 4, 0), {})
    registry = ModelRegistry(tmp_path / "models")
    model = registry.register(source)
    before = registry.index_path.read_bytes()

    def unavailable_index(_entries):
        raise OSError("Metadata storage is unavailable")

    monkeypatch.setattr(registry, "_write_index", unavailable_index)
    with pytest.raises(OSError, match="Metadata storage"):
        registry.delete(model["id"])
    assert registry.index_path.read_bytes() == before
    assert Path(model["path"]).is_file()


def test_registry_delete_rolls_back_metadata_when_removing_copy_fails(tmp_path, monkeypatch):
    source = save_checkpoint(tmp_path / "last.pt", PolicyValueNet("brandubh", 4, 0), {})
    registry = ModelRegistry(tmp_path / "models")
    model = registry.register(source)
    original_unlink = Path.unlink

    def fail_owned_copy(path, *args, **kwargs):
        if path == Path(model["path"]):
            raise OSError("Model copy is unavailable")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_owned_copy)
    with pytest.raises(OSError, match="Model copy"):
        registry.delete(model["id"])
    assert registry.get(model["id"])["id"] == model["id"]


def test_registry_delete_and_import_serialize_without_lost_entries(tmp_path):
    source_a = save_checkpoint(tmp_path / "a.pt", PolicyValueNet("brandubh", 4, 0), {})
    source_b = save_checkpoint(tmp_path / "b.pt", PolicyValueNet("tablut", 4, 0), {})
    registry = ModelRegistry(tmp_path / "models")
    model_a = registry.register(source_a)
    with ThreadPoolExecutor(max_workers=2) as executor:
        deleted = executor.submit(registry.delete, model_a["id"])
        imported = executor.submit(registry.import_model, source_b)
        assert deleted.result()["id"] == model_a["id"]
        model_b = imported.result()
    assert [entry["id"] for entry in registry.list()] == [model_b["id"]]
    assert not Path(model_a["path"]).exists()
    assert Path(model_b["path"]).is_file()
    assert source_a.is_file() and source_b.is_file()


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


def _same_weight_checkpoint(tmp_path, name, created_at, version):
    """A checkpoint with fixed weights but caller-controlled metadata."""
    torch.manual_seed(1234)
    model = PolicyValueNet("brandubh", channels=8, blocks=0)
    return save_checkpoint(
        tmp_path / f"{name}.pt",
        model,
        {
            "name": name,
            "ruleset": "brandubh",
            "created_at": created_at,
            "version": version,
            "metrics": {"score": version},
        },
    )


def test_registry_reuses_identical_weights_across_different_metadata(tmp_path):
    first_path = _same_weight_checkpoint(tmp_path, "First", "2020-01-01T00:00:00Z", 1)
    second_path = _same_weight_checkpoint(tmp_path, "Second", "2021-06-01T00:00:00Z", 9)
    registry = ModelRegistry(tmp_path / "registry")
    first = registry.register(first_path, "First")
    second = registry.register(second_path, "Second")
    assert first["reused"] is False
    assert second["reused"] is True
    assert second["id"] == first["id"]
    assert len(registry.list()) == 1
    assert len(list((tmp_path / "registry").glob("*.pt"))) == 1
    # Reuse keeps the original catalog name rather than silently renaming it.
    assert registry.get(first["id"])["name"] == "First"
    stored = json.loads(registry.index_path.read_text(encoding="utf-8"))
    assert len(stored) == 1
    assert stored[0]["weights_sha256"]
    # The transient registration signal never leaks into persisted metadata.
    assert "reused" not in stored[0]


def test_registry_same_configuration_different_weights_stay_distinct(tmp_path):
    torch.manual_seed(0)
    model_a = PolicyValueNet("brandubh", channels=8, blocks=0)
    torch.manual_seed(1)
    model_b = PolicyValueNet("brandubh", channels=8, blocks=0)
    path_a = save_checkpoint(tmp_path / "a.pt", model_a, {"name": "A"})
    path_b = save_checkpoint(tmp_path / "b.pt", model_b, {"name": "B"})
    registry = ModelRegistry(tmp_path / "registry")
    first = registry.register(path_a)
    second = registry.register(path_b)
    assert first["id"] != second["id"]
    assert second["reused"] is False
    assert len(registry.list()) == 2
    assert len(list((tmp_path / "registry").glob("*.pt"))) == 2


def test_registry_concurrent_identical_weight_import_creates_one_entry(tmp_path):
    first_path = _same_weight_checkpoint(tmp_path, "First", "2020-01-01T00:00:00Z", 1)
    second_path = _same_weight_checkpoint(tmp_path, "Second", "2021-06-01T00:00:00Z", 2)
    registry = ModelRegistry(tmp_path / "registry")
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(registry.import_model, path) for path in (first_path, second_path)
        ]
        results = [future.result() for future in futures]
    assert {result["id"] for result in results} == {results[0]["id"]}
    assert sorted(result["reused"] for result in results) == [False, True]
    assert len(registry.list()) == 1
    assert len(list((tmp_path / "registry").glob("*.pt"))) == 1


def test_registry_backfills_legacy_fingerprint_and_dedupes(tmp_path):
    registry = ModelRegistry(tmp_path / "registry")
    first_path = _same_weight_checkpoint(tmp_path, "First", "2020-01-01T00:00:00Z", 1)
    first = registry.register(first_path, "First")
    # Simulate a catalog written before weight fingerprints existed.
    stored = json.loads(registry.index_path.read_text(encoding="utf-8"))
    stored[0].pop("weights_sha256")
    registry.index_path.write_text(json.dumps(stored), encoding="utf-8")
    second_path = _same_weight_checkpoint(tmp_path, "Second", "2021-06-01T00:00:00Z", 2)
    second = registry.import_model(second_path, "Second")
    assert second["id"] == first["id"]
    assert second["reused"] is True
    assert len(registry.list()) == 1
    refreshed = json.loads(registry.index_path.read_text(encoding="utf-8"))
    assert refreshed[0]["weights_sha256"]


def test_registry_reused_match_repairs_missing_owned_copy(tmp_path):
    registry = ModelRegistry(tmp_path / "registry")
    first_path = _same_weight_checkpoint(tmp_path, "First", "2020-01-01T00:00:00Z", 1)
    first = registry.register(first_path, "First")
    Path(first["path"]).unlink()
    second_path = _same_weight_checkpoint(tmp_path, "Second", "2021-06-01T00:00:00Z", 2)
    second = registry.import_model(second_path, "Second")
    assert second["id"] == first["id"]
    assert Path(second["path"]).is_file()
    assert len(registry.list()) == 1


def test_registry_duplicates_reports_without_deleting(tmp_path):
    registry = ModelRegistry(tmp_path / "registry")
    first_path = _same_weight_checkpoint(tmp_path, "First", "2020-01-01T00:00:00Z", 1)
    first = registry.register(first_path, "First")
    # A legitimate legacy duplicate: identical weights, different bytes, and an ID
    # that still matches its own file hash prefix.
    variant = _same_weight_checkpoint(tmp_path, "Second", "2021-06-01T00:00:00Z", 2)
    duplicate_bytes = variant.read_bytes()
    duplicate_id = hashlib.sha256(duplicate_bytes).hexdigest()[:24]
    duplicate_path = tmp_path / "registry" / f"{duplicate_id}.pt"
    duplicate_path.write_bytes(duplicate_bytes)
    stored = json.loads(registry.index_path.read_text(encoding="utf-8"))
    for entry in stored:
        entry.pop("weights_sha256", None)
        entry.pop("checkpoint_sha256", None)
    stored.append({**stored[0], "id": duplicate_id, "name": "Duplicate"})
    registry.index_path.write_text(json.dumps(stored), encoding="utf-8")
    groups = registry.duplicates()
    assert len(groups) == 1
    assert {entry["id"] for entry in groups[0]} == {first["id"], duplicate_id}
    # Detection is read-only: entries and files remain untouched.
    assert len(registry.list()) == 2
    assert Path(first["path"]).is_file() and duplicate_path.is_file()


def test_registry_delete_keeps_other_models_and_source_checkpoints(tmp_path):
    registry = ModelRegistry(tmp_path / "registry")
    first_path = _same_weight_checkpoint(tmp_path, "First", "2020-01-01T00:00:00Z", 1)
    second_path = _same_weight_checkpoint(tmp_path, "Second", "2020-01-01T00:00:00Z", 2)
    torch.manual_seed(7)
    other = PolicyValueNet("brandubh", channels=8, blocks=0)
    other_path = save_checkpoint(tmp_path / "other.pt", other, {"name": "Other"})
    first = registry.register(first_path, "First")
    second = registry.register(other_path, "Other")
    registry.delete(first["id"])
    assert [entry["id"] for entry in registry.list()] == [second["id"]]
    assert Path(second["path"]).is_file()
    # Original training/job checkpoints are never touched by catalog deletion.
    assert first_path.is_file() and second_path.is_file() and other_path.is_file()


def _different_weight_checkpoint(tmp_path, name, seed):
    torch.manual_seed(seed)
    model = PolicyValueNet("brandubh", channels=8, blocks=0)
    return save_checkpoint(tmp_path / f"{name}.pt", model, {"name": name})


def test_registry_corrupted_copy_is_repaired_with_stable_identity(tmp_path):
    registry = ModelRegistry(tmp_path / "registry")
    source = _same_weight_checkpoint(tmp_path, "First", "2020-01-01T00:00:00Z", 1)
    first = registry.register(source, "First")
    owned = Path(first["path"])
    owned.write_bytes(b"corrupted")
    repaired = registry.import_model(source, "Ignored name")
    assert repaired["id"] == first["id"]
    assert repaired["reused"] is True
    # The original catalog name and identity survive the repair.
    assert repaired["name"] == "First"
    assert load_checkpoint(owned)[1]["metadata"]["name"] == "First"
    assert len(registry.list()) == 1


def test_registry_corrupted_copy_is_repaired_from_equivalent_weights(tmp_path):
    registry = ModelRegistry(tmp_path / "registry")
    source = _same_weight_checkpoint(tmp_path, "First", "2020-01-01T00:00:00Z", 1)
    first = registry.register(source, "First")
    owned = Path(first["path"])
    owned.write_bytes(b"corrupted")
    # Different file bytes (metadata) but identical inference weights.
    variant = _same_weight_checkpoint(tmp_path, "Second", "2021-06-01T00:00:00Z", 2)
    repaired = registry.import_model(variant, "Second")
    assert repaired["id"] == first["id"]
    assert repaired["name"] == "First"
    assert owned.is_file()
    assert len(registry.list()) == 1


def test_registry_truncated_copy_is_repaired(tmp_path):
    registry = ModelRegistry(tmp_path / "registry")
    source = _same_weight_checkpoint(tmp_path, "First", "2020-01-01T00:00:00Z", 1)
    first = registry.register(source, "First")
    owned = Path(first["path"])
    owned.write_bytes(owned.read_bytes()[:64])
    repaired = registry.import_model(source)
    assert repaired["id"] == first["id"]
    _, payload = load_checkpoint(owned)
    assert payload["metadata"]["name"] == "First"


def test_registry_valid_different_weight_copy_is_never_overwritten(tmp_path):
    registry = ModelRegistry(tmp_path / "registry")
    source = _same_weight_checkpoint(tmp_path, "First", "2020-01-01T00:00:00Z", 1)
    first = registry.register(source, "First")
    owned = Path(first["path"])
    replacement = _different_weight_checkpoint(tmp_path, "Replacement", 99)
    owned.write_bytes(replacement.read_bytes())
    replacement_bytes = owned.read_bytes()
    # Different file bytes but the original inference weights: a new entry is
    # created and the valid different-weight copy is corrected, never overwritten.
    variant = _same_weight_checkpoint(tmp_path, "First copy", "2022-01-01T00:00:00Z", 3)
    result = registry.import_model(variant, "First copy")
    assert result["id"] != first["id"]
    assert result["reused"] is False
    assert owned.read_bytes() == replacement_bytes
    assert registry.get(first["id"])["id"] == first["id"]
    assert len(registry.list()) == 2


def test_registry_same_id_with_different_weights_fails_explicitly(tmp_path):
    registry = ModelRegistry(tmp_path / "registry")
    source = _same_weight_checkpoint(tmp_path, "First", "2020-01-01T00:00:00Z", 1)
    first = registry.register(source, "First")
    owned = Path(first["path"])
    replacement = _different_weight_checkpoint(tmp_path, "Replacement", 99)
    owned.write_bytes(replacement.read_bytes())
    replacement_bytes = owned.read_bytes()
    # The incoming bytes hash to the same ID but the registered copy holds other
    # weights: refuse rather than overwrite a valid checkpoint.
    with pytest.raises(ValueError, match="different weights"):
        registry.import_model(source, "First")
    assert owned.read_bytes() == replacement_bytes
    assert registry.get(first["id"])["id"] == first["id"]
    assert len(registry.list()) == 1


def test_registry_fully_legacy_entry_is_backfilled_and_deduped(tmp_path):
    registry = ModelRegistry(tmp_path / "registry")
    source = _same_weight_checkpoint(tmp_path, "First", "2020-01-01T00:00:00Z", 1)
    first = registry.register(source, "First")
    stored = json.loads(registry.index_path.read_text(encoding="utf-8"))
    stored[0].pop("weights_sha256")
    stored[0].pop("checkpoint_sha256")
    registry.index_path.write_text(json.dumps(stored), encoding="utf-8")
    variant = _same_weight_checkpoint(tmp_path, "Second", "2021-06-01T00:00:00Z", 2)
    second = registry.import_model(variant, "Second")
    assert second["id"] == first["id"]
    assert second["reused"] is True
    refreshed = json.loads(registry.index_path.read_text(encoding="utf-8"))
    assert refreshed[0]["weights_sha256"] and refreshed[0]["checkpoint_sha256"]
    assert len(registry.list()) == 1


def test_import_transaction_reuses_intact_entry_without_modification(tmp_path):
    registry = ModelRegistry(tmp_path / "registry")
    source = _same_weight_checkpoint(tmp_path, "First", "2020-01-01T00:00:00Z", 1)
    first = registry.register(source, "First")
    owned = Path(first["path"])
    before_bytes = owned.read_bytes()
    before_index = registry.index_path.read_bytes()
    with registry.import_transaction():
        reused = registry.import_model(source, "Second")
        assert reused["id"] == first["id"]
        assert reused["reused"] is True
    assert owned.read_bytes() == before_bytes
    assert registry.index_path.read_bytes() == before_index
    assert len(registry.list()) == 1


@pytest.mark.parametrize("damage", ["missing", "corrupt", "dangling"])
def test_import_transaction_refuses_repair_and_rolls_back(tmp_path, damage):
    registry = ModelRegistry(tmp_path / "registry")
    source = _same_weight_checkpoint(tmp_path, "First", "2020-01-01T00:00:00Z", 1)
    first = registry.register(source, "First")
    owned = Path(first["path"])
    if damage == "missing":
        owned.unlink()
    elif damage == "corrupt":
        owned.write_bytes(b"corrupted")
    else:
        owned.unlink()
        owned.symlink_to(tmp_path / "gone.pt")
    before_index = registry.index_path.read_bytes()
    with pytest.raises(ValueError, match="cannot be repaired"):
        with registry.import_transaction():
            registry.import_model(source, "Second")
    assert registry.index_path.read_bytes() == before_index
    assert len(registry.list()) == 1
    if damage == "missing":
        assert not owned.exists()
    elif damage == "corrupt":
        assert owned.read_bytes() == b"corrupted"
    else:
        assert owned.is_symlink() and not owned.exists()


def test_registry_repairs_a_dangling_symlink_outside_transaction(tmp_path):
    registry = ModelRegistry(tmp_path / "registry")
    source = _same_weight_checkpoint(tmp_path, "First", "2020-01-01T00:00:00Z", 1)
    first = registry.register(source, "First")
    owned = Path(first["path"])
    owned.unlink()
    owned.symlink_to(tmp_path / "gone.pt")
    repaired = registry.import_model(source, "Second")
    assert repaired["id"] == first["id"]
    assert repaired["name"] == "First"
    assert not owned.is_symlink()
    assert load_checkpoint(owned)[0] is not None


def test_import_transaction_rolls_back_new_file_when_index_write_fails(tmp_path, monkeypatch):
    registry = ModelRegistry(tmp_path / "registry")
    source = _same_weight_checkpoint(tmp_path, "First", "2020-01-01T00:00:00Z", 1)
    registry.register(source, "First")
    before_files = sorted(path.name for path in registry.root.glob("*.pt"))
    before_index = registry.index_path.read_bytes()
    new = _different_weight_checkpoint(tmp_path, "Second", 55)

    def failing_write(_entries):
        raise OSError("index storage unavailable")

    monkeypatch.setattr(registry, "_write_index", failing_write)
    with pytest.raises(OSError, match="index storage unavailable"):
        with registry.import_transaction():
            registry.import_model(new, "Second")
    assert sorted(path.name for path in registry.root.glob("*.pt")) == before_files
    assert registry.index_path.read_bytes() == before_index
    assert len(registry.list()) == 1


def test_registry_duplicates_is_read_only_and_groups_identical_weights(tmp_path):
    registry = ModelRegistry(tmp_path / "registry")
    source = _same_weight_checkpoint(tmp_path, "First", "2020-01-01T00:00:00Z", 1)
    first = registry.register(source, "First")
    duplicate_id = "c" * 24
    (tmp_path / "registry" / f"{duplicate_id}.pt").write_bytes(Path(first["path"]).read_bytes())
    stored = json.loads(registry.index_path.read_text(encoding="utf-8"))
    stored.append({**stored[0], "id": duplicate_id, "name": "Duplicate"})
    registry.index_path.write_text(json.dumps(stored), encoding="utf-8")
    before_index = registry.index_path.read_bytes()
    before_files = sorted(path.name for path in registry.root.glob("*.pt"))
    groups = registry.duplicates()
    assert len(groups) == 1 and len(groups[0]) == 2
    # The scan holds the registry lock and never rewrites the index or files.
    assert registry.index_path.read_bytes() == before_index
    assert sorted(path.name for path in registry.root.glob("*.pt")) == before_files


def _tablut_checkpoint(tmp_path, name="Tablut"):
    torch.manual_seed(777)
    return save_checkpoint(
        tmp_path / f"{name}.pt",
        PolicyValueNet("tablut", channels=8, blocks=0),
        {"name": name},
    )


def _append_duplicate_entry(registry, duplicate_id, name="Duplicate"):
    """Append a second catalog entry that owns a byte-identical copy."""
    stored = json.loads(registry.index_path.read_text(encoding="utf-8"))
    original = stored[0]
    source = registry.root / f"{original['id']}.pt"
    target = registry.root / f"{duplicate_id}.pt"
    target.write_bytes(source.read_bytes())
    stored.append({**original, "id": duplicate_id, "name": name})
    registry.index_path.write_text(json.dumps(stored), encoding="utf-8")
    return target


def _entry_without_path(registry, model_id):
    entry = next(item for item in registry.list() if item["id"] == model_id)
    entry.pop("path")
    return entry


def _make_legacy(registry, model_id=None):
    """Strip the weight fingerprint fields to simulate a pre-migration entry."""
    stored = json.loads(registry.index_path.read_text(encoding="utf-8"))
    for entry in stored:
        if model_id is None or entry["id"] == model_id:
            entry.pop("weights_sha256", None)
            entry.pop("checkpoint_sha256", None)
    registry.index_path.write_text(json.dumps(stored), encoding="utf-8")


def test_legacy_entry_with_intact_file_backfills_and_reuses(tmp_path):
    registry = ModelRegistry(tmp_path / "registry")
    source = _same_weight_checkpoint(tmp_path, "First", "2020-01-01T00:00:00Z", 1)
    first = registry.register(source, "First")
    _make_legacy(registry, first["id"])
    # Identical weights, different metadata and a different content-addressed ID.
    variant = _same_weight_checkpoint(tmp_path, "Variant", "2021-06-01T00:00:00Z", 2)
    reused = registry.import_model(variant, "Variant")
    assert reused["id"] == first["id"]
    assert reused["reused"] is True
    stored = json.loads(registry.index_path.read_text(encoding="utf-8"))
    assert stored[0]["weights_sha256"]
    assert (
        stored[0]["checkpoint_sha256"]
        == hashlib.sha256(Path(first["path"]).read_bytes()).hexdigest()
    )
    # No duplicate entry or checkpoint copy is created.
    assert len(registry.list()) == 1
    assert len(list(registry.root.glob("*.pt"))) == 1


def test_legacy_entry_with_replaced_weights_is_inconsistent(tmp_path):
    registry = ModelRegistry(tmp_path / "registry")
    source = _same_weight_checkpoint(tmp_path, "First", "2020-01-01T00:00:00Z", 1)
    first = registry.register(source, "First")
    _make_legacy(registry, first["id"])
    before_entry = _entry_without_path(registry, first["id"])
    owned = Path(first["path"])
    replacement = _different_weight_checkpoint(tmp_path, "Replacement", 42)
    owned.write_bytes(replacement.read_bytes())
    replacement_bytes = owned.read_bytes()
    # The replacement is registered independently; A keeps its identity and file.
    imported = registry.import_model(replacement, "Replacement")
    assert imported["id"] != first["id"]
    assert imported["reused"] is False
    assert _entry_without_path(registry, first["id"]) == before_entry
    assert owned.read_bytes() == replacement_bytes
    assert len(registry.list()) == 2
    # Importing the original bytes (same ID) raises instead of adopting weights.
    with pytest.raises(ValueError, match="different weights"):
        registry.import_model(source, "First")


def test_legacy_entry_with_replaced_ruleset_is_inconsistent(tmp_path):
    registry = ModelRegistry(tmp_path / "registry")
    source = _same_weight_checkpoint(tmp_path, "First", "2020-01-01T00:00:00Z", 1)
    first = registry.register(source, "First")
    _make_legacy(registry, first["id"])
    owned = Path(first["path"])
    tablut = _tablut_checkpoint(tmp_path)
    owned.write_bytes(tablut.read_bytes())
    tablut_bytes = owned.read_bytes()
    imported = registry.import_model(tablut, "Tablut")
    assert imported["id"] != first["id"]
    assert imported["ruleset"] == "tablut"
    assert registry.get(first["id"])["ruleset"] == "brandubh"
    assert owned.read_bytes() == tablut_bytes
    assert len(registry.list()) == 2


@pytest.mark.parametrize("damage", ["corrupt", "truncated", "missing"])
def test_legacy_damaged_checkpoint_is_not_backfilled(tmp_path, damage):
    registry = ModelRegistry(tmp_path / "registry")
    source = _same_weight_checkpoint(tmp_path, "First", "2020-01-01T00:00:00Z", 1)
    first = registry.register(source, "First")
    _make_legacy(registry, first["id"])
    before_entry = _entry_without_path(registry, first["id"])
    owned = Path(first["path"])
    if damage == "missing":
        owned.unlink()
    elif damage == "corrupt":
        owned.write_bytes(b"corrupted")
    else:
        owned.write_bytes(owned.read_bytes()[:64])
    before_index = registry.index_path.read_bytes()
    state, fingerprint, changed = registry._inspect_entry(dict(before_entry))
    assert state in {"missing", "unreadable"}
    assert fingerprint is None and changed is False
    # Inspection and duplicate detection never mutate the index or files.
    assert registry.duplicates() == []
    assert registry.index_path.read_bytes() == before_index
    assert _entry_without_path(registry, first["id"]) == before_entry


def test_legacy_inconsistent_entry_is_read_only_for_duplicates(tmp_path):
    registry = ModelRegistry(tmp_path / "registry")
    source = _same_weight_checkpoint(tmp_path, "First", "2020-01-01T00:00:00Z", 1)
    first = registry.register(source, "First")
    _make_legacy(registry, first["id"])
    replacement = _different_weight_checkpoint(tmp_path, "Replacement", 42)
    owned = Path(first["path"])
    owned.write_bytes(replacement.read_bytes())
    before_index = registry.index_path.read_bytes()
    before_files = {path.name: path.read_bytes() for path in registry.root.glob("*.pt")}
    assert registry.duplicates() == []
    assert registry.index_path.read_bytes() == before_index
    assert {path.name: path.read_bytes() for path in registry.root.glob("*.pt")} == before_files


def test_same_id_missing_file_with_different_recorded_identity_is_refused(tmp_path):
    registry = ModelRegistry(tmp_path / "registry")
    source = _same_weight_checkpoint(tmp_path, "First", "2020-01-01T00:00:00Z", 1)
    first = registry.register(source, "First")
    stored = json.loads(registry.index_path.read_text(encoding="utf-8"))
    stored[0]["weights_sha256"] = "f" * 64
    registry.index_path.write_text(json.dumps(stored), encoding="utf-8")
    Path(first["path"]).unlink()
    # A damaged file cannot prove a different incoming identity.
    with pytest.raises(ValueError, match="different recorded identity"):
        registry.import_model(source, "First")


def test_registry_replaced_with_incompatible_ruleset_preserves_identity(tmp_path):
    registry = ModelRegistry(tmp_path / "registry")
    source = _same_weight_checkpoint(tmp_path, "First", "2020-01-01T00:00:00Z", 1)
    first = registry.register(source, "First")
    fingerprint = registry.get(first["id"])["weights_sha256"]
    owned = Path(first["path"])
    tablut = _tablut_checkpoint(tmp_path)
    owned.write_bytes(tablut.read_bytes())
    tablut_bytes = owned.read_bytes()
    # Importing the original bytes must fail: the owned copy no longer matches.
    with pytest.raises(ValueError, match="different weights"):
        registry.import_model(source, "First")
    # The replacement is a genuinely different ruleset and registers separately.
    replacement = registry.import_model(tablut, "Tablut")
    assert replacement["id"] != first["id"]
    assert replacement["ruleset"] == "tablut"
    assert replacement["reused"] is False
    # The original entry keeps its recorded identity and its (replaced) file.
    assert registry.get(first["id"])["weights_sha256"] == fingerprint
    assert owned.read_bytes() == tablut_bytes
    assert len(registry.list()) == 2


def test_registry_replaced_with_different_weights_preserves_identity(tmp_path):
    registry = ModelRegistry(tmp_path / "registry")
    source = _same_weight_checkpoint(tmp_path, "First", "2020-01-01T00:00:00Z", 1)
    first = registry.register(source, "First")
    fingerprint = registry.get(first["id"])["weights_sha256"]
    owned = Path(first["path"])
    replacement = _different_weight_checkpoint(tmp_path, "Replacement", 42)
    owned.write_bytes(replacement.read_bytes())
    replacement_bytes = owned.read_bytes()
    with pytest.raises(ValueError, match="different weights"):
        registry.import_model(source, "First")
    imported = registry.import_model(replacement, "Replacement")
    assert imported["id"] != first["id"]
    assert imported["reused"] is False
    assert registry.get(first["id"])["weights_sha256"] == fingerprint
    assert owned.read_bytes() == replacement_bytes
    assert len(registry.list()) == 2


def test_registry_metadata_only_change_refreshes_hash_without_new_entry(tmp_path):
    registry = ModelRegistry(tmp_path / "registry")
    source = _same_weight_checkpoint(tmp_path, "First", "2020-01-01T00:00:00Z", 1)
    first = registry.register(source, "First")
    fingerprint = registry.get(first["id"])["weights_sha256"]
    owned = Path(first["path"])
    variant = _same_weight_checkpoint(tmp_path, "First copy", "2022-01-01T00:00:00Z", 3)
    # Same weights, different bytes (metadata-only): a legitimate byte change.
    owned.write_bytes(variant.read_bytes())
    reused = registry.import_model(source, "First")
    assert reused["id"] == first["id"]
    assert reused["reused"] is True
    stored = json.loads(registry.index_path.read_text(encoding="utf-8"))
    assert stored[0]["weights_sha256"] == fingerprint
    assert stored[0]["checkpoint_sha256"] == hashlib.sha256(owned.read_bytes()).hexdigest()
    assert len(registry.list()) == 1


@pytest.mark.parametrize("damaged_first", [True, False])
def test_registry_prefers_healthy_duplicate_regardless_of_order(tmp_path, damaged_first):
    registry = ModelRegistry(tmp_path / "registry")
    source = _same_weight_checkpoint(tmp_path, "First", "2020-01-01T00:00:00Z", 1)
    first = registry.register(source, "First")
    duplicate_id = "d" * 24
    duplicate_path = _append_duplicate_entry(registry, duplicate_id, "Duplicate")
    healthy_id = first["id"] if damaged_first else duplicate_id
    damaged_path = duplicate_path if damaged_first else Path(first["path"])
    healthy_path = duplicate_path if damaged_first else Path(first["path"])
    damaged_path.write_bytes(b"corrupted")
    before_healthy = healthy_path.read_bytes()
    variant = _same_weight_checkpoint(tmp_path, "Variant", "2021-06-01T00:00:00Z", 2)
    result = registry.import_model(variant, "Variant")
    # The healthy duplicate is reused; the damaged one is left untouched.
    assert result["id"] == healthy_id
    assert result["reused"] is True
    assert damaged_path.read_bytes() == b"corrupted"
    assert healthy_path.read_bytes() == before_healthy
    assert len(registry.list()) == 2


def test_registry_reuses_first_healthy_duplicate_in_registry_order(tmp_path):
    registry = ModelRegistry(tmp_path / "registry")
    source = _same_weight_checkpoint(tmp_path, "First", "2020-01-01T00:00:00Z", 1)
    first = registry.register(source, "First")
    _append_duplicate_entry(registry, "e" * 24, "Second")
    _append_duplicate_entry(registry, "f" * 24, "Third")
    variant = _same_weight_checkpoint(tmp_path, "Variant", "2021-06-01T00:00:00Z", 2)
    result = registry.import_model(variant, "Variant")
    assert result["id"] == first["id"]
    assert result["reused"] is True
    assert len(registry.list()) == 3


def test_import_transaction_reuses_healthy_duplicate_without_repair(tmp_path):
    registry = ModelRegistry(tmp_path / "registry")
    source = _same_weight_checkpoint(tmp_path, "First", "2020-01-01T00:00:00Z", 1)
    first = registry.register(source, "First")
    duplicate_id = "d" * 24
    duplicate_path = _append_duplicate_entry(registry, duplicate_id, "Duplicate")
    Path(first["path"]).write_bytes(b"corrupted")
    before_duplicate = duplicate_path.read_bytes()
    variant = _same_weight_checkpoint(tmp_path, "Variant", "2021-06-01T00:00:00Z", 2)
    # Archive-style transaction: a healthy duplicate avoids a prohibited repair.
    with registry.import_transaction():
        result = registry.import_model(variant, "Variant")
    assert result["id"] == duplicate_id
    assert Path(first["path"]).read_bytes() == b"corrupted"
    assert duplicate_path.read_bytes() == before_duplicate
    assert len(registry.list()) == 2


def test_inspect_entry_rejects_oversized_file_before_hashing(tmp_path, monkeypatch):
    registry = ModelRegistry(tmp_path / "registry")
    source = _same_weight_checkpoint(tmp_path, "First", "2020-01-01T00:00:00Z", 1)
    first = registry.register(source, "First")
    Path(first["path"]).write_bytes(b"x" * 32)
    entry = _entry_without_path(registry, first["id"])
    calls: list[str] = []
    original = hashlib.file_digest

    def tracking(handle, name):
        calls.append(name)
        return original(handle, name)

    monkeypatch.setattr("hnefatafl.models.hashlib.file_digest", tracking)
    monkeypatch.setattr("hnefatafl.models.MAX_CHECKPOINT_BYTES", 8)
    state, fingerprint, changed = registry._inspect_entry(entry)
    assert state == "unreadable"
    assert fingerprint is None and changed is False
    assert calls == []


def test_inspect_entry_filesystem_edge_states(tmp_path, monkeypatch):
    registry = ModelRegistry(tmp_path / "registry")
    source = _same_weight_checkpoint(tmp_path, "First", "2020-01-01T00:00:00Z", 1)
    first = registry.register(source, "First")
    entry = _entry_without_path(registry, first["id"])
    owned = Path(first["path"])
    original = owned.read_bytes()
    # A normal valid checkpoint is healthy and unchanged.
    state, fingerprint, changed = registry._inspect_entry(entry)
    assert state == "ok"
    assert fingerprint == entry["weights_sha256"]
    assert changed is False
    # Truncated.
    owned.write_bytes(original[:32])
    assert registry._inspect_entry(entry)[0] == "unreadable"
    # Missing.
    owned.unlink()
    assert registry._inspect_entry(entry)[0] == "missing"
    # Dangling symlink.
    owned.symlink_to(tmp_path / "gone.pt")
    assert registry._inspect_entry(entry)[0] == "missing"
    owned.unlink()
    owned.write_bytes(original)
    # A file that becomes unavailable after the existence check.
    monkeypatch.setattr(Path, "is_file", lambda self: True)

    def unavailable(_self):
        raise OSError("unavailable")

    monkeypatch.setattr(Path, "stat", unavailable)
    assert registry._inspect_entry(entry)[0] == "unreadable"
