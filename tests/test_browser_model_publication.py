"""Explicit browser-model publication: authorization, validation and lifecycle.

ONNX graph validation is exercised by the existing export tests; these tests use
a synthetic validation payload so publication policy (authorization, atomicity,
immutability, supersession and revocation) is covered without ONNX or PyTorch
weights.
"""

import hashlib
import json
import sqlite3

import pytest

from hnefatafl import browser_models
from hnefatafl.browser_models import (
    PublicationError,
    PublishedModelStore,
    authorize_administrator,
)

MODEL_BYTES = b"synthetic-onnx-model-bytes" * 64
WEIGHTS = "a" * 64


def _export(tmp_path, *, model_bytes=MODEL_BYTES):
    export = tmp_path / "export"
    export.mkdir(parents=True, exist_ok=True)
    (export / "model.onnx").write_bytes(model_bytes)
    (export / "export-manifest.json").write_text('{"validated": true}\n')
    (export / "VALIDATED.json").write_text('{"validated": true}\n')
    return export


def _validation(model_bytes=MODEL_BYTES, *, validated=True, opset=17, encoding="six-plane-v1"):
    digest = hashlib.sha256(model_bytes).hexdigest()
    sidecar = {
        "schema": 2,
        "artifact": "hnefatafl-policy-value-onnx",
        "architecture": "tafl-policy-value-v1",
        "ruleset": "brandubh",
        "model_config": {"ruleset": "brandubh", "channels": 32, "blocks": 2},
        "source": {
            "weights_sha256": WEIGHTS,
            "sha256": "b" * 64,
            "encoder_version": "six-plane-v1",
        },
        "input": {
            "name": "planes",
            "encoding": encoding,
            "shape": ["batch", 6, 7, 7],
            "dtype": "float32",
            "dynamic_batch": True,
        },
        "outputs": {
            "policy_logits": {"shape": ["batch", 7**4], "dtype": "float32"},
            "defender_value": {"shape": ["batch"], "dtype": "float32"},
        },
    }
    return {
        "sidecar": sidecar,
        "reference": {"cases": [{}]},
        "ruleset": "brandubh",
        "size": 7,
        "digest": digest,
        "model_bytes": len(model_bytes),
        "reference_digest": "c" * 64,
        "sidecar_digest": "d" * 64,
        "manifest": {"validated": validated, "parity_passed": validated},
        "marker": {"validated": True},
        "graph": {"opset": opset},
        "validated": validated,
    }


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(
        browser_models, "validate_export_dir", lambda path, allow_unvalidated=False: _validation()
    )
    return PublishedModelStore(tmp_path)


def _db(tmp_path, rows):
    database = tmp_path / "hnefatafl.sqlite3"
    connection = sqlite3.connect(database)
    connection.execute(
        "CREATE TABLE accounts (id TEXT PRIMARY KEY, username TEXT UNIQUE COLLATE NOCASE, "
        "role TEXT NOT NULL, active INTEGER NOT NULL)"
    )
    for username, role, active in rows:
        connection.execute(
            "INSERT INTO accounts (id, username, role, active) VALUES (?, ?, ?, ?)",
            (username + "-id", username, role, active),
        )
    connection.commit()
    connection.close()


def test_publication_requires_a_validated_export(tmp_path, store, monkeypatch):
    export = _export(tmp_path)
    monkeypatch.setattr(
        browser_models,
        "validate_export_dir",
        lambda path, allow_unvalidated=False: _validation(validated=False),
    )
    with pytest.raises(PublicationError) as error:
        store.publish(export, actor="Odin")
    assert error.value.code == "UNVALIDATED"
    assert store.list_public() == []


def test_publish_is_immutable_idempotent_and_atomic(tmp_path, store):
    export = _export(tmp_path)
    record = store.publish(export, actor="Odin", display_name="Brandubh net")
    assert record["status"] == "published"
    assert record["onnx_sha256"] == hashlib.sha256(MODEL_BYTES).hexdigest()
    artifact = store.artifacts / f"{record['onnx_sha256']}.onnx"
    assert artifact.read_bytes() == MODEL_BYTES
    public = store.public_record(record)
    assert "path" not in json.dumps(public).lower().replace("dispatch", "")
    assert store.get_public(record["publication_id"]) == public

    again = store.publish(export, actor="Odin")
    assert again["reused"] is True
    assert len(store.history()) == 1
    assert len(store.list_public()) == 1
    assert (store.audit_path.read_text()).count('"action": "publish"') == 1

    # Deleting a server model never deletes the published artifact.
    artifact.unlink()
    with pytest.raises(PublicationError) as error:
        store.artifact_path(record["publication_id"])
    assert error.value.code == "ARTIFACT_MISSING"


def test_replacement_requires_explicit_flag_and_keeps_history(tmp_path, store, monkeypatch):
    first_export = _export(tmp_path)
    first = store.publish(first_export, actor="Odin")
    second_bytes = b"second-validated-model" * 128
    second_export = tmp_path / "export-2"
    second_export.mkdir()
    (second_export / "model.onnx").write_bytes(second_bytes)
    (second_export / "export-manifest.json").write_text('{"validated": true}\n')
    (second_export / "VALIDATED.json").write_text('{"validated": true}\n')
    monkeypatch.setattr(
        browser_models,
        "validate_export_dir",
        lambda path, allow_unvalidated=False: _validation(second_bytes),
    )
    with pytest.raises(PublicationError) as error:
        store.publish(second_export, actor="Odin")
    assert error.value.code == "ALREADY_PUBLISHED"

    second = store.publish(second_export, actor="Odin", replace=True)
    assert second["publication_id"] == first["publication_id"]
    assert second["onnx_sha256"] != first["onnx_sha256"]
    history = store.history(first["publication_id"])
    statuses = [entry["status"] for entry in history]
    assert statuses == ["superseded", "published"]
    assert history[0]["superseded_by"] == second["onnx_sha256"]
    assert len(store.list_public()) == 1
    assert store.list_public()[0]["onnx_sha256"] == second["onnx_sha256"]


def test_revocation_is_explicit_and_retains_metadata(tmp_path, store):
    record = store.publish(_export(tmp_path), actor="Odin")
    revoked = store.revoke(record["publication_id"], actor="Odin", reason="superseded by owner")
    assert revoked["status"] == "revoked"
    assert store.list_public() == []
    with pytest.raises(PublicationError):
        store.artifact_path(record["publication_id"])
    with pytest.raises(PublicationError):
        store.revoke(record["publication_id"], actor="Odin")
    history = store.history(record["publication_id"])
    assert history[-1]["revocation_reason"] == "superseded by owner"
    assert "revoke" in store.audit_path.read_text()


@pytest.mark.parametrize(
    "identifier", ["../models/abc", "0" * 23, "z" * 24, "../../etc/passwd", ""]
)
def test_invalid_publication_identifiers_are_rejected(store, identifier):
    with pytest.raises((PublicationError, ValueError)):
        store.revoke(identifier, actor="Odin")
    with pytest.raises(PublicationError):
        store.artifact_path(identifier)


def test_artifact_conflict_is_rejected(tmp_path, store):
    export = _export(tmp_path)
    validation = _validation()
    conflict = store.artifacts / f"{validation['digest']}.onnx"
    conflict.write_bytes(b"different contents")
    with pytest.raises(PublicationError) as error:
        store.publish(export, actor="Odin")
    assert error.value.code == "ARTIFACT_CONFLICT"
    assert store.list_public() == []


def test_oversized_or_incompatible_models_are_rejected(tmp_path, store, monkeypatch):
    export = _export(tmp_path)
    oversized = _validation()
    oversized["model_bytes"] = browser_models.MAX_MODEL_BYTES + 1
    monkeypatch.setattr(
        browser_models, "validate_export_dir", lambda path, allow_unvalidated=False: oversized
    )
    with pytest.raises(PublicationError):
        store.publish(export, actor="Odin")

    monkeypatch.setattr(
        browser_models,
        "validate_export_dir",
        lambda path, allow_unvalidated=False: _validation(opset=99),
    )
    with pytest.raises(PublicationError):
        store.publish(export, actor="Odin")

    monkeypatch.setattr(
        browser_models,
        "validate_export_dir",
        lambda path, allow_unvalidated=False: _validation(encoding="two-plane-v0"),
    )
    with pytest.raises(PublicationError):
        store.publish(export, actor="Odin")
    assert store.list_public() == []


def test_failed_index_write_does_not_publish(tmp_path, store, monkeypatch):
    export = _export(tmp_path)
    original = store._write_index
    monkeypatch.setattr(
        store, "_write_index", lambda payload: (_ for _ in ()).throw(OSError("disk full"))
    )
    with pytest.raises(PublicationError) as error:
        store.publish(export, actor="Odin")
    assert error.value.code == "PUBLICATION_IO"
    assert store.list_public() == []
    # No publication record survived the failed atomic index write. The orphaned
    # immutable artifact is harmless and a retry publishes exactly once.
    monkeypatch.setattr(store, "_write_index", original)
    retried = store.publish(export, actor="Odin")
    assert retried["status"] == "published"
    assert len(store.list_public()) == 1
    assert len(store.history()) == 1


def test_named_registered_source_must_match(tmp_path, store):
    export = _export(tmp_path)
    with pytest.raises(PublicationError) as error:
        store.publish(export, actor="Odin", model_id="0" * 24)
    assert "registered model is unavailable" in str(error.value)


@pytest.mark.parametrize(
    "rows, username, allowed",
    [
        ([("Odin", "ADMIN", 1)], "Odin", True),
        ([("Odin", "ADMIN", 1)], "odin", True),
        ([("Raven", "USER", 1)], "Raven", False),
        ([("Odin", "ADMIN", 0)], "Odin", False),
        ([("Odin", "ADMIN", 1)], "Loki", False),
    ],
)
def test_publication_requires_an_active_administrator(tmp_path, rows, username, allowed):
    _db(tmp_path, rows)
    if allowed:
        assert authorize_administrator(tmp_path, username).lower() == username.lower()
    else:
        with pytest.raises(PublicationError) as error:
            authorize_administrator(tmp_path, username)
        assert error.value.code == "UNAUTHORIZED"


def test_publication_requires_the_application_database(tmp_path):
    with pytest.raises(PublicationError) as error:
        authorize_administrator(tmp_path, "Odin")
    assert error.value.code == "UNAUTHORIZED"


def test_cli_requires_an_administrator_account(tmp_path, capsys):
    _db(tmp_path, [("Raven", "USER", 1)])
    code = browser_models.main(
        [
            "--data-dir",
            str(tmp_path),
            "publish",
            str(_export(tmp_path)),
            "--account",
            "Raven",
        ]
    )
    assert code == 1
    assert "UNAUTHORIZED" in capsys.readouterr().out


def _replace_export(tmp_path, monkeypatch, store, bytes_):
    export = tmp_path / "replacement"
    export.mkdir(exist_ok=True)
    (export / "model.onnx").write_bytes(bytes_)
    (export / "export-manifest.json").write_text('{"validated": true}\n')
    (export / "VALIDATED.json").write_text('{"validated": true}\n')
    monkeypatch.setattr(
        browser_models,
        "validate_export_dir",
        lambda path, allow_unvalidated=False: _validation(bytes_),
    )
    return store.publish(export, actor="Odin", replace=True)


def test_resolve_artifact_binds_record_version_and_file(tmp_path, store, monkeypatch):
    record = store.publish(_export(tmp_path), actor="Odin")
    resolved, path = store.resolve_artifact(record["publication_id"])
    assert resolved["onnx_sha256"] == record["onnx_sha256"]
    assert path.read_bytes() == MODEL_BYTES
    resolved, path = store.resolve_artifact(record["publication_id"], record["onnx_sha256"])
    assert path.read_bytes() == MODEL_BYTES
    for version in ("f" * 64, "not-a-digest"):
        with pytest.raises(PublicationError) as error:
            store.resolve_artifact(record["publication_id"], version)
        assert error.value.code == "NOT_FOUND"


def test_replacement_is_atomic_for_record_version_and_file(tmp_path, store, monkeypatch):
    first = store.publish(_export(tmp_path), actor="Odin")
    first_path = store.artifacts / f"{first['onnx_sha256']}.onnx"
    second_bytes = b"second validated model" * 128
    second = _replace_export(tmp_path, monkeypatch, store, second_bytes)
    assert second["publication_id"] == first["publication_id"]
    assert second["onnx_sha256"] != first["onnx_sha256"]
    resolved, path = store.resolve_artifact(first["publication_id"])
    assert resolved["onnx_sha256"] == second["onnx_sha256"]
    assert path.read_bytes() == second_bytes
    with pytest.raises(PublicationError):
        store.resolve_artifact(first["publication_id"], first["onnx_sha256"])
    # An already-resolved immutable version remains readable after replacement.
    assert first_path.read_bytes() == MODEL_BYTES


def test_revocation_between_resolution_and_serving(tmp_path, store):
    record = store.publish(_export(tmp_path), actor="Odin")
    _resolved, path = store.resolve_artifact(record["publication_id"])
    store.revoke(record["publication_id"], actor="Odin", reason="test")
    # An already-started authorized download keeps the immutable bytes...
    assert path.read_bytes() == MODEL_BYTES
    # ...while every new request fails closed.
    with pytest.raises(PublicationError) as error:
        store.resolve_artifact(record["publication_id"])
    assert error.value.code == "NOT_FOUND"


def test_same_size_corrupted_artifact_is_rejected(tmp_path, store):
    record = store.publish(_export(tmp_path), actor="Odin")
    path = store.artifacts / f"{record['onnx_sha256']}.onnx"
    corrupted = b"x" * len(MODEL_BYTES)
    path.write_bytes(corrupted)
    with pytest.raises(PublicationError) as error:
        store.resolve_artifact(record["publication_id"])
    assert error.value.code == "ARTIFACT_INTEGRITY"


def test_symlinked_and_escaping_artifacts_are_rejected(tmp_path, store):
    record = store.publish(_export(tmp_path), actor="Odin")
    path = store.artifacts / f"{record['onnx_sha256']}.onnx"
    outside = tmp_path / "outside.onnx"
    outside.write_bytes(MODEL_BYTES)
    path.unlink()
    path.symlink_to(outside)
    with pytest.raises(PublicationError):
        store.resolve_artifact(record["publication_id"])
    path.unlink()
    missing = store.resolve_artifact
    with pytest.raises(PublicationError) as error:
        missing(record["publication_id"])
    assert error.value.code == "ARTIFACT_MISSING"


def test_audit_failure_rolls_back_publication_and_revocation(tmp_path, store, monkeypatch):
    def broken(*args, **kwargs):
        raise OSError("audit unavailable")

    monkeypatch.setattr(store, "_audit", broken)
    with pytest.raises(PublicationError) as error:
        store.publish(_export(tmp_path), actor="Odin")
    assert error.value.code == "PUBLICATION_IO"
    assert store.list_public() == []
    assert store.history() == []

    monkeypatch.undo()
    monkeypatch.setattr(
        browser_models, "validate_export_dir", lambda path, allow_unvalidated=False: _validation()
    )
    record = store.publish(_export(tmp_path), actor="Odin")
    monkeypatch.setattr(store, "_audit", broken)
    with pytest.raises(PublicationError) as error:
        store.revoke(record["publication_id"], actor="Odin")
    assert error.value.code == "PUBLICATION_IO"
    assert store.get_public(record["publication_id"]) is not None


def test_same_size_model_modification_with_restored_mtime_is_rejected(tmp_path, store):
    import os

    record = store.publish(_export(tmp_path), actor="Odin")
    _resolved, path = store.resolve_artifact(record["publication_id"])
    stat = path.stat()
    changed = bytearray(path.read_bytes())
    changed[0] ^= 0xFF
    path.write_bytes(bytes(changed))
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    with pytest.raises(PublicationError) as error:
        store.resolve_artifact(record["publication_id"])
    assert error.value.code == "ARTIFACT_INTEGRITY"


def test_model_replacement_during_verification_is_rejected(tmp_path, store, monkeypatch):
    import os

    from hnefatafl import browser_runtime

    record = store.publish(_export(tmp_path), actor="Odin")
    path = store.artifacts / f"{record['onnx_sha256']}.onnx"
    stat = path.stat()
    original_hash = browser_runtime._hash_fd

    def racing_hash(fd):
        digest = original_hash(fd)
        changed = bytearray(path.read_bytes())
        changed[0] ^= 0xFF
        path.write_bytes(bytes(changed))
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        return digest

    monkeypatch.setattr(browser_runtime, "_hash_fd", racing_hash)
    with pytest.raises(PublicationError) as error:
        store.open_artifact(record["publication_id"])
    assert error.value.code == "ARTIFACT_INTEGRITY"


def test_verified_model_descriptor_streams_old_bytes_after_replacement(tmp_path, store):
    record = store.publish(_export(tmp_path), actor="Odin")
    _record, handle, size = store.open_artifact(record["publication_id"])
    try:
        before = handle.read()
        assert len(before) == size
        assert hashlib.sha256(before).hexdigest() == record["onnx_sha256"]
        (store.artifacts / f"{record['onnx_sha256']}.onnx").write_bytes(b"x" * size)
    finally:
        handle.close()
    assert before == MODEL_BYTES


def test_open_model_stream_survives_concurrent_revocation(tmp_path, store):
    record = store.publish(_export(tmp_path), actor="Odin")
    _record, handle, _size = store.open_artifact(record["publication_id"])
    try:
        store.revoke(record["publication_id"], actor="Odin", reason="test")
        assert handle.read() == MODEL_BYTES
        with pytest.raises(PublicationError):
            store.open_artifact(record["publication_id"])
    finally:
        handle.close()


def test_external_symlink_model_artifact_is_rejected(tmp_path, store):
    record = store.publish(_export(tmp_path), actor="Odin")
    path = store.artifacts / f"{record['onnx_sha256']}.onnx"
    outside = tmp_path / "outside.onnx"
    outside.write_bytes(MODEL_BYTES)
    path.unlink()
    path.symlink_to(outside)
    with pytest.raises(PublicationError) as error:
        store.open_artifact(record["publication_id"])
    assert error.value.code in {"ARTIFACT_MISSING", "ARTIFACT_INTEGRITY"}
