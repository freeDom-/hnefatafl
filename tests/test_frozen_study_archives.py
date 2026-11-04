"""Frozen dependencies must remain verifiable through portable study migration."""

import hashlib
import io
import json
import sqlite3
import zipfile
from pathlib import Path

import pytest
import torch
from auth_support import AuthenticatedClient as TestClient

from hnefatafl.models import ModelRegistry, PolicyValueNet, load_checkpoint, save_checkpoint
from hnefatafl.server import create_app
from hnefatafl.training import tune
from test_job_archives import export, job_record, upload


@pytest.fixture
def source(tmp_path):
    with TestClient(create_app(tmp_path / "source")) as client:
        root = client.app.state.data_dir
        path = root / "opponent.pt"
        save_checkpoint(path, PolicyValueNet("brandubh", 8, 1), {"name": "Pinned opponent"})
        registry = ModelRegistry(root / "models")
        model = registry.register(path)
        job, directory = job_record(client, kind="tune")
        config = {
            "study_name": "Frozen dependency",
            "trials": 1,
            "output_dir": str(directory),
            "search_space": {"learning_rate": {"type": "categorical", "choices": [0.001]}},
            "training": {
                "preset": "smoke",
                "ruleset": "brandubh",
                "device": "cpu",
                "cpu_threads": 1,
                "max_game_plies": 2,
                "evaluation_max_plies": 2,
                "registry_dir": str(root / "models"),
            },
            "opponents": [{"opponent": "frozen_model", "model_id": model["id"], "weight": 1}],
        }
        result = tune(config)
        job.update(config=config, progress=result, result=result)
        client.app.state.store.save_job(job)
        yield client, job, registry, model


@pytest.fixture
def destination(tmp_path):
    with TestClient(create_app(tmp_path / "destination")) as client:
        yield client


def change_archive(content, change):
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    manifest = json.loads(files.pop("manifest.json"))
    change(files, manifest)
    manifest["files"] = {
        name: {"size": len(value), "sha256": hashlib.sha256(value).hexdigest()}
        for name, value in files.items()
    }
    files["manifest.json"] = json.dumps(manifest).encode()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, value in files.items():
            archive.writestr(name, value)
    return buffer.getvalue()


@pytest.mark.parametrize("problem", ["deleted", "missing_file", "tampered", "ruleset", "format"])
def test_export_rejects_unavailable_or_changed_frozen_models(source, problem):
    client, job, registry, model = source
    path = Path(model["path"])
    if problem == "deleted":
        registry.delete(model["id"])
    elif problem == "missing_file":
        path.unlink()
    elif problem == "tampered":
        _, payload = load_checkpoint(path)
        next(iter(payload["state_dict"].values())).add_(1)
        torch.save(payload, path)
    elif problem == "ruleset":
        save_checkpoint(path, PolicyValueNet("tablut", 8, 1), {"name": "Wrong ruleset"})
    else:
        path.write_bytes(b"not a model")
    response = client.get(f"/api/training/jobs/{job['id']}/export")
    assert response.status_code == 400, response.text
    assert model["id"] in response.text
    assert client.app.state.store.get_job(job["id"]) is not None


@pytest.mark.parametrize("legacy", [False, True])
def test_valid_pinned_frozen_model_import_and_deduplication(source, destination, legacy):
    client, job, registry, model = source
    content = export(client, job)
    if legacy:
        content = change_archive(content, lambda files, manifest: manifest.update(format_version=1))
    destination_registry = ModelRegistry(destination.app.state.data_dir / "models")
    existing = destination_registry.import_model(model["path"], "Locally named opponent")
    unrelated_path = destination.app.state.data_dir / "unrelated.pt"
    save_checkpoint(unrelated_path, PolicyValueNet("tablut", 4, 0), {"name": "Unrelated"})
    destination_registry.register(unrelated_path)
    before = destination_registry.list()
    response = upload(destination, content)
    assert response.status_code == 201, response.text
    imported = response.json()
    assert destination_registry.list() == before
    assert imported["config"]["opponents"][0]["model_id"] == existing["id"]
    # Re-export verifies the rebased study's pins and contains the required model.
    migrated = export(destination, imported)
    with zipfile.ZipFile(io.BytesIO(migrated)) as archive:
        assert f"models/{existing['id']}.pt" in archive.namelist()


@pytest.mark.parametrize(
    "problem", ["inventory_missing", "file_missing", "corrupt", "wrong_contents"]
)
def test_bad_frozen_import_rolls_back_without_orphaned_data(source, destination, problem, tmp_path):
    client, job, _, model = source
    content = export(client, job)

    def change(files, manifest):
        name = f"models/{model['id']}.pt"
        if problem == "inventory_missing":
            manifest["models"] = []
            files.pop(name)
        elif problem == "file_missing":
            files.pop(name)
        elif problem == "corrupt":
            files[name] = b"not a checkpoint"
        else:
            changed = tmp_path / "replacement.pt"
            _, payload = load_checkpoint(model["path"])
            next(iter(payload["state_dict"].values())).add_(1)
            torch.save(payload, changed)
            files[name] = changed.read_bytes()

    damaged = change_archive(content, change)
    root = destination.app.state.data_dir
    registry = ModelRegistry(root / "models")
    before = registry.list()
    response = upload(destination, damaged)
    assert response.status_code == 400, response.text
    assert registry.list() == before and destination.app.state.store.jobs() == []
    assert list((root / "jobs").iterdir()) == []
    assert not list(root.glob(".job-import-*"))


def test_frozen_registration_failure_is_transactional(source, destination, monkeypatch):
    client, job, _, _ = source
    content = export(client, job)
    original = ModelRegistry.import_model

    def fail_after_publish(self, *args, **kwargs):
        original(self, *args, **kwargs)
        raise ValueError("Injected frozen model publication failure")

    monkeypatch.setattr(ModelRegistry, "import_model", fail_after_publish)
    response = upload(destination, content)
    assert response.status_code == 400, response.text
    root = destination.app.state.data_dir
    assert ModelRegistry(root / "models").list() == []
    assert not list((root / "models").glob("*.pt"))
    assert destination.app.state.store.jobs() == [] and list((root / "jobs").iterdir()) == []


def test_imported_trial_ownership_is_protected_after_rebasing(source, destination):
    client, original, _, _ = source
    imported = upload(destination, export(client, original)).json()
    root = destination.app.state.data_dir
    contributor, directory = job_record(destination, identifier="b" * 32, kind="tune")
    config = {**imported["config"], "output_dir": str(directory)}
    config["training"] = {**config["training"], "registry_dir": str(root / "models")}
    result = tune(config)
    contributor.update(config=config, progress=result, result=result)
    destination.app.state.store.save_job(contributor)
    full = Path(result["best_trial_checkpoint"])
    before = full.read_bytes()
    response = destination.delete(f"/api/training/jobs/{contributor['id']}")
    assert response.status_code == 409, response.text
    assert full.read_bytes() == before
    assert destination.app.state.store.get_job(contributor["id"]) is not None


def test_import_rejects_damaged_existing_id_without_overwriting_it(source, destination):
    client, job, _, model = source
    content = export(client, job)
    registry = ModelRegistry(destination.app.state.data_dir / "models")
    portable = destination.app.state.data_dir / "opponent.pt"
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        portable.write_bytes(archive.read(f"models/{model['id']}.pt"))
    existing = registry.import_model(portable)
    path = Path(existing["path"])
    _, payload = load_checkpoint(path)
    next(iter(payload["state_dict"].values())).add_(1)
    torch.save(payload, path)
    before = path.read_bytes()
    entries = registry.list()
    response = upload(destination, content)
    assert response.status_code == 400, response.text
    assert path.read_bytes() == before and registry.list() == entries
    assert destination.app.state.store.jobs() == []


@pytest.mark.parametrize("conflicting", [False, True])
def test_export_requires_unambiguous_saved_frozen_pins(source, conflicting):
    client, job, _, model = source
    database = client.app.state.data_dir / "jobs" / job["id"] / "study.sqlite3"
    with sqlite3.connect(database) as db:
        for table, key in [
            ("study_user_attributes", "hnefatafl_identity"),
            ("trial_user_attributes", "experiment_identity"),
        ]:
            identity = json.loads(
                db.execute(f"SELECT value_json FROM {table} WHERE key=?", (key,)).fetchone()[0]
            )
            if conflicting:
                if key == "hnefatafl_identity":
                    identity["frozen_models"][model["id"]] = "0" * 64
            else:
                identity.pop("frozen_models")
            db.execute(f"UPDATE {table} SET value_json=? WHERE key=?", (json.dumps(identity), key))
    response = client.get(f"/api/training/jobs/{job['id']}/export")
    assert response.status_code == 400, response.text
    assert model["id"] in response.text
