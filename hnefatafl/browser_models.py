"""Explicit publication of browser-compatible inference artifacts.

A registered server model is *not* a published browser model. Publication is a
separate, administrator-authorized, auditable operation with its own immutable
content-addressed artifact store:

``<data>/browser_models/``
    ``publications.json``  atomically replaced publication index (history kept)
    ``artifacts/<sha256>.onnx``  immutable, digest-named ONNX artifacts
    ``audit.jsonl``  append-only administrative audit trail
    ``.publications.lock``  cross-process publication lock

Only a fully validated export produced by the existing ``onnx_export.py``
workflow can be published: the canonical artifact contract is
:mod:`hnefatafl.onnx_artifacts`, so publication cannot accept a weaker check
than the spike validation. Server model deletion never removes a published copy;
unpublication is explicit and recorded.

Distribution makes the ONNX weights accessible to any client that downloads
them. Access control can restrict distribution but cannot keep downloaded
weights secret.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .browser_pins import LIMITS, MAX_MODEL_BYTES, ORT_VERSION
from .encoding import ENCODER_VERSION
from .engine import RULESETS
from .mcts_driver import SEARCH_PROTOCOL
from .models import ARCHITECTURE
from .onnx_artifacts import (
    MAX_OPSET,
    MIN_OPSET,
    OnnxArtifactError,
    validate_export_dir,
)
from .browser_bridge import BRIDGE_PROTOCOL

PUBLICATION_SCHEMA = 1
PUBLICATION_DIRECTORY = "browser_models"
PUBLICATION_ID = re.compile(r"[0-9a-f]{24}\Z")
_HEX64 = re.compile(r"[0-9a-f]{64}\Z")
RUNTIME_MANIFEST_SCHEMA = 1
MODEL_MANIFEST_SCHEMA = 1
MAX_INDEX_BYTES = 8 * 1024 * 1024
STATUS_PUBLISHED = "published"
STATUS_SUPERSEDED = "superseded"
STATUS_REVOKED = "revoked"
PUBLIC_STATUSES = {STATUS_PUBLISHED}


class PublicationError(ValueError):
    """A publication request is unauthorized, invalid or not permitted."""

    def __init__(self, message: str, *, code: str = "PUBLICATION_ERROR") -> None:
        super().__init__(message)
        self.code = code


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _digest_file(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def publication_root(data_dir: str | Path) -> Path:
    return Path(data_dir).resolve() / PUBLICATION_DIRECTORY


def publication_id_for(ruleset: str, source_weights_sha256: str) -> str:
    """Stable publication slot identity, independent of export/ONNX bytes."""
    material = f"browser-model-v1\0{ruleset}\0{source_weights_sha256}".encode()
    return hashlib.sha256(material).hexdigest()[:24]


@contextmanager
def _lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if sys.platform == "win32":  # pragma: no cover - documented Linux target
            import msvcrt

            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def authorize_administrator(data_dir: str | Path, username: str) -> str:
    """Verify an existing active ADMIN account using the application database.

    Publication uses the same authorization model as the local account CLI: the
    operation runs as the OS account owning the data directory and must name an
    existing, active administrator. A browser session can never publish.
    """
    if not isinstance(username, str) or not username.strip():
        raise PublicationError("An administrator account is required", code="UNAUTHORIZED")
    database = Path(data_dir).resolve() / "hnefatafl.sqlite3"
    if not database.is_file():
        raise PublicationError(
            "The application database is missing; initialize the administrator first",
            code="UNAUTHORIZED",
        )
    try:
        connection = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    except sqlite3.Error as error:
        raise PublicationError(f"Cannot read the account database: {error}") from error
    try:
        row = connection.execute(
            "SELECT username, role, active FROM accounts WHERE username = ? COLLATE NOCASE",
            (username.strip(),),
        ).fetchone()
    except sqlite3.Error as error:
        raise PublicationError(f"Cannot read the account database: {error}") from error
    finally:
        connection.close()
    if row is None or row[1] != "ADMIN" or not row[2]:
        raise PublicationError(
            "The named account is not an active administrator", code="UNAUTHORIZED"
        )
    return str(row[0])


class PublishedModelStore:
    """Immutable, content-addressed browser model publication store."""

    def __init__(self, data_dir: str | Path):
        self.root = publication_root(data_dir)
        self.index_path = self.root / "publications.json"
        self.artifacts = self.root / "artifacts"
        self.audit_path = self.root / "audit.jsonl"
        self.lock_path = self.root / ".publications.lock"
        self.root.mkdir(parents=True, exist_ok=True)
        self.artifacts.mkdir(parents=True, exist_ok=True)
        self._artifact_cache: dict[str, tuple[tuple[int, int, int], str]] = {}
        self._artifact_lock = threading.Lock()

    # ------------------------------------------------------------ index access
    def _read_index(self) -> dict[str, Any]:
        if not self.index_path.exists():
            return {"schema": PUBLICATION_SCHEMA, "publications": []}
        try:
            size = self.index_path.stat().st_size
            if not 0 < size <= MAX_INDEX_BYTES:
                raise PublicationError("Publication index size is invalid")
            payload = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise PublicationError(f"Publication index is unreadable: {error}") from error
        if not isinstance(payload, dict) or payload.get("schema") != PUBLICATION_SCHEMA:
            raise PublicationError("Unsupported publication index schema")
        records = payload.get("publications")
        if not isinstance(records, list):
            raise PublicationError("Publication index is invalid")
        return payload

    def _write_index(self, payload: dict[str, Any]) -> None:
        descriptor, temporary = tempfile.mkstemp(prefix=".publications-", dir=self.root)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True, allow_nan=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.index_path)
            _fsync_directory(self.root)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def _audit(self, action: str, actor: str, **fields: Any) -> None:
        entry = {"at": _now(), "action": action, "actor": actor, **fields}
        with self.audit_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True, allow_nan=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    # ------------------------------------------------------------- projections
    @staticmethod
    def public_record(record: dict[str, Any]) -> dict[str, Any]:
        """Projection safe for browser clients: no filesystem paths or metadata."""
        return {
            "schema": MODEL_MANIFEST_SCHEMA,
            "publication_id": record["publication_id"],
            "artifact_version": record["onnx_sha256"],
            "display_name": record["display_name"],
            "ruleset": record["ruleset"],
            "board_size": record["board_size"],
            "architecture": record["architecture"],
            "encoder_version": record["encoder_version"],
            "source_weights_sha256": record["source_weights_sha256"],
            "onnx_sha256": record["onnx_sha256"],
            "artifact_bytes": record["artifact_bytes"],
            "runtime": record["runtime"],
            "io": record["io"],
            "status": record["status"],
            "published_at": record["published_at"],
        }

    def list_public(self) -> list[dict[str, Any]]:
        with _lock(self.lock_path):
            payload = self._read_index()
        return [
            self.public_record(record)
            for record in payload["publications"]
            if record.get("status") in PUBLIC_STATUSES
        ]

    def get_public(self, publication_id: str) -> dict[str, Any] | None:
        return next(
            (record for record in self.list_public() if record["publication_id"] == publication_id),
            None,
        )

    def _verified_artifact(self, record: dict[str, Any]) -> Path:
        """Resolve and verify the immutable artifact belonging to one record."""
        expected = record["onnx_sha256"]
        if not isinstance(expected, str) or not _HEX64.fullmatch(expected):
            raise PublicationError(
                "Published artifact identity is invalid", code="ARTIFACT_MISSING"
            )
        path = self.artifacts / f"{expected}.onnx"
        try:
            resolved = path.resolve(strict=True)
        except OSError:
            raise PublicationError(
                "Published artifact is missing", code="ARTIFACT_MISSING"
            ) from None
        if path.is_symlink() or not resolved.is_relative_to(self.artifacts.resolve()):
            raise PublicationError("Published artifact path is invalid", code="ARTIFACT_MISSING")
        if not resolved.is_file():
            raise PublicationError("Published artifact is missing", code="ARTIFACT_MISSING")
        try:
            stat = resolved.stat()
        except OSError:
            raise PublicationError(
                "Published artifact is missing", code="ARTIFACT_MISSING"
            ) from None
        if stat.st_size != record["artifact_bytes"]:
            raise PublicationError("Published artifact size is invalid", code="ARTIFACT_INTEGRITY")
        signature = (stat.st_ino, stat.st_size, stat.st_mtime_ns)
        with self._artifact_lock:
            cached = self._artifact_cache.get(expected)
        if cached is not None and cached[0] == signature:
            return resolved
        actual = _digest_file(resolved)
        if actual != expected:
            with self._artifact_lock:
                self._artifact_cache.pop(expected, None)
            raise PublicationError(
                "Published artifact integrity check failed", code="ARTIFACT_INTEGRITY"
            )
        with self._artifact_lock:
            if len(self._artifact_cache) >= 64 and expected not in self._artifact_cache:
                self._artifact_cache.pop(next(iter(self._artifact_cache)))
            self._artifact_cache[expected] = (signature, actual)
        return resolved

    def resolve_artifact(
        self, publication_id: str, artifact_version: str | None = None
    ) -> tuple[dict[str, Any], Path]:
        """Resolve record, immutable version and file identity under one lock.

        A concurrent replacement or revocation cannot pair a new record with an
        old file (or the reverse). The immutable file bytes are verified by a
        bounded stat-signature cache so ordinary downloads do not re-hash the
        whole model on every request.
        """
        if not isinstance(publication_id, str) or not PUBLICATION_ID.fullmatch(publication_id):
            raise PublicationError("Published model not found", code="NOT_FOUND")
        if artifact_version is not None and (
            not isinstance(artifact_version, str) or not _HEX64.fullmatch(artifact_version)
        ):
            raise PublicationError("Published model not found", code="NOT_FOUND")
        with _lock(self.lock_path):
            payload = self._read_index()
            record = next(
                (
                    entry
                    for entry in payload["publications"]
                    if entry.get("publication_id") == publication_id
                    and entry.get("status") in PUBLIC_STATUSES
                ),
                None,
            )
            if record is None:
                raise PublicationError("Published model not found", code="NOT_FOUND")
            if artifact_version is not None and record["onnx_sha256"] != artifact_version:
                raise PublicationError(
                    "The requested artifact version is not published", code="NOT_FOUND"
                )
            path = self._verified_artifact(record)
            return dict(record), path

    def artifact_path(self, publication_id: str, digest: str | None = None) -> Path:
        """Compatibility helper returning the verified immutable artifact path."""
        _record, path = self.resolve_artifact(publication_id, digest)
        return path

    def _verify_registered_source(self, model_id: str, weights: str) -> None:
        """Bind an optional provenance claim to the actual registered weights."""
        from .models import ModelRegistry

        registry = ModelRegistry(self.root.parent / "models")
        try:
            entry = registry.get(model_id)
        except ValueError as error:
            raise PublicationError(f"The named registered model is unavailable: {error}") from error
        if entry.get("weights_sha256") != weights:
            raise PublicationError(
                "The export was not produced from the named registered model",
                code="SOURCE_MISMATCH",
            )

    # --------------------------------------------------------------- publishing
    def prepare(self, export_dir: str | Path) -> dict[str, Any]:
        """Validate an export directory for publication (never mutates state)."""
        export_dir = Path(export_dir).resolve()
        try:
            validation = validate_export_dir(export_dir, allow_unvalidated=False)
        except OnnxArtifactError as error:
            raise PublicationError(str(error), code=error.code) from error
        sidecar = validation["sidecar"]
        manifest = validation["manifest"] or {}
        marker = validation["marker"] or {}
        if validation["validated"] is not True:
            raise PublicationError("Only a validated export may be published", code="UNVALIDATED")
        if marker.get("validated") is not True or manifest.get("validated") is not True:
            raise PublicationError("Export validation status is inconsistent", code="UNVALIDATED")
        if not manifest.get("parity_passed"):
            raise PublicationError("Export has no passing parity evidence", code="UNVALIDATED")
        source = sidecar.get("source") or {}
        weights = source.get("weights_sha256")
        if not isinstance(weights, str) or len(weights) != 64:
            raise PublicationError("Export has no source-weight fingerprint", code="UNVALIDATED")
        size = validation["model_bytes"]
        if size > MAX_MODEL_BYTES or size > LIMITS["max_model_bytes"]:
            raise PublicationError("Model exceeds the browser distribution size budget")
        graph = validation.get("graph") or {}
        opset = graph.get("opset")
        if type(opset) is not int or not MIN_OPSET <= opset <= MAX_OPSET:
            raise PublicationError("Model opset is not supported by the pinned runtime")
        ruleset = validation["ruleset"]
        model_config = sidecar.get("model_config") or {}
        runtime = {
            "runtime_manifest_schema": RUNTIME_MANIFEST_SCHEMA,
            "ort_version": ORT_VERSION,
            "min_opset": MIN_OPSET,
            "max_opset": MAX_OPSET,
            "encoder_version": ENCODER_VERSION,
            "search_protocol": SEARCH_PROTOCOL,
            "bridge_protocol": BRIDGE_PROTOCOL,
        }
        if runtime["encoder_version"] != sidecar.get("input", {}).get("encoding"):
            raise PublicationError("Encoder compatibility check failed")
        return {
            "export_dir": export_dir,
            "ruleset": ruleset,
            "board_size": validation["size"],
            "architecture": {
                "name": ARCHITECTURE,
                "ruleset": model_config.get("ruleset"),
                "channels": model_config.get("channels"),
                "blocks": model_config.get("blocks"),
            },
            "encoder_version": ENCODER_VERSION,
            "source_weights_sha256": weights,
            "source_checkpoint_sha256": source.get("sha256"),
            "onnx_sha256": validation["digest"],
            "artifact_bytes": size,
            "runtime": runtime,
            "io": {
                "input": sidecar.get("input"),
                "outputs": sidecar.get("outputs"),
            },
            "validation": {
                "export_manifest_sha256": _digest_file(export_dir / "export-manifest.json"),
                "validated_marker_sha256": _digest_file(export_dir / "VALIDATED.json"),
                "parity_sha256": manifest.get("parity_sha256"),
                "reference_sha256": validation["reference_digest"],
                "sidecar_sha256": validation["sidecar_digest"],
                "opset": opset,
            },
            "model_bytes": validation["model_bytes"],
            "publication_id": publication_id_for(ruleset, weights),
        }

    def publish(
        self,
        export_dir: str | Path,
        *,
        actor: str,
        display_name: str | None = None,
        model_id: str | None = None,
        replace: bool = False,
        expected_ruleset: str | None = None,
    ) -> dict[str, Any]:
        """Publish a validated export atomically, optionally replacing a slot."""
        prepared = self.prepare(export_dir)
        if model_id is not None:
            self._verify_registered_source(model_id, prepared["source_weights_sha256"])
        if expected_ruleset and prepared["ruleset"] != expected_ruleset:
            raise PublicationError("Export ruleset does not match the requested ruleset")
        if display_name is not None and (
            not isinstance(display_name, str) or not 1 <= len(display_name.strip()) <= 100
        ):
            raise PublicationError("Display name must contain 1 to 100 characters")
        ruleset = prepared["ruleset"]
        if ruleset not in RULESETS:  # defensive; validation already checks this
            raise PublicationError("Unsupported ruleset")
        digest = prepared["onnx_sha256"]
        source = Path(prepared["export_dir"]) / "model.onnx"

        with _lock(self.lock_path):
            payload = self._read_index()
            records: list[dict[str, Any]] = payload["publications"]
            already = next(
                (
                    record
                    for record in records
                    if record.get("onnx_sha256") == digest
                    and record.get("status") == STATUS_PUBLISHED
                ),
                None,
            )
            if already is not None:
                return {**already, "reused": True}
            current = next(
                (
                    record
                    for record in records
                    if record.get("publication_id") == prepared["publication_id"]
                    and record.get("status") == STATUS_PUBLISHED
                ),
                None,
            )
            if current is not None and not replace:
                raise PublicationError(
                    "A different artifact is already published for this source model; "
                    "pass --replace to publish a new validated version",
                    code="ALREADY_PUBLISHED",
                )

            target = self.artifacts / f"{digest}.onnx"
            if target.exists():
                if _digest_file(target) != digest:
                    raise PublicationError(
                        "An existing published artifact has a different digest",
                        code="ARTIFACT_CONFLICT",
                    )
            else:
                descriptor, temporary = tempfile.mkstemp(
                    prefix=".artifact-", suffix=".onnx", dir=self.artifacts
                )
                os.close(descriptor)
                temporary_path = Path(temporary)
                try:
                    with source.open("rb") as incoming, temporary_path.open("wb") as outgoing:
                        while True:
                            chunk = incoming.read(1024 * 1024)
                            if not chunk:
                                break
                            outgoing.write(chunk)
                        outgoing.flush()
                        os.fsync(outgoing.fileno())
                    if _digest_file(temporary_path) != digest:
                        raise PublicationError("Artifact digest changed while publishing")
                    os.replace(temporary_path, target)
                    _fsync_directory(self.artifacts)
                finally:
                    temporary_path.unlink(missing_ok=True)

            now = _now()
            identifier = prepared["publication_id"]
            if current is not None:
                current["status"] = STATUS_SUPERSEDED
                current["superseded_at"] = now
                current["superseded_by"] = digest
            record = {
                "schema": PUBLICATION_SCHEMA,
                "publication_id": identifier,
                "model_id": model_id,
                "display_name": (
                    display_name.strip()
                    if display_name
                    else f"{ruleset} browser model ({prepared['architecture']['channels']}x"
                    f"{prepared['architecture']['blocks']})"
                ),
                "ruleset": ruleset,
                "board_size": prepared["board_size"],
                "architecture": prepared["architecture"],
                "encoder_version": prepared["encoder_version"],
                "source_weights_sha256": prepared["source_weights_sha256"],
                "source_checkpoint_sha256": prepared["source_checkpoint_sha256"],
                "onnx_sha256": digest,
                "artifact_bytes": prepared["artifact_bytes"],
                "runtime": prepared["runtime"],
                "io": prepared["io"],
                "validation": prepared["validation"],
                "status": STATUS_PUBLISHED,
                "published_at": now,
                "published_by": actor,
                "revoked_at": None,
                "revoked_by": None,
                "revocation_reason": None,
                "superseded_at": None,
                "superseded_by": None,
            }
            previous_index = json.loads(json.dumps(payload))
            records.append(record)
            try:
                self._write_index(payload)
                self._audit(
                    "publish",
                    actor,
                    publication_id=identifier,
                    onnx_sha256=digest,
                    source_weights_sha256=prepared["source_weights_sha256"],
                    ruleset=ruleset,
                    replaced=bool(current),
                )
            except OSError as error:
                # The artifact is immutable and harmless if left orphaned; the
                # index must not keep a publication the audit trail rejected.
                try:
                    self._write_index(previous_index)
                except OSError:
                    pass
                raise PublicationError(
                    f"Publication audit/index write failed: {error}", code="PUBLICATION_IO"
                ) from error
            return {**record, "reused": False}

    def revoke(
        self, publication_id: str, *, actor: str, reason: str | None = None
    ) -> dict[str, Any]:
        """Record an explicit revocation; the artifact copy is retained."""
        if not isinstance(publication_id, str) or not PUBLICATION_ID.fullmatch(publication_id):
            raise PublicationError("Invalid publication identifier")
        with _lock(self.lock_path):
            payload = self._read_index()
            record = next(
                (
                    entry
                    for entry in payload["publications"]
                    if entry.get("publication_id") == publication_id
                    and entry.get("status") == STATUS_PUBLISHED
                ),
                None,
            )
            if record is None:
                raise PublicationError("No published model for this identifier", code="NOT_FOUND")
            previous_index = json.loads(json.dumps(payload))
            record["status"] = STATUS_REVOKED
            record["revoked_at"] = _now()
            record["revoked_by"] = actor
            record["revocation_reason"] = (reason or "revoked by administrator")[:200]
            try:
                self._write_index(payload)
                self._audit(
                    "revoke",
                    actor,
                    publication_id=publication_id,
                    onnx_sha256=record["onnx_sha256"],
                    reason=record["revocation_reason"],
                )
            except OSError as error:
                try:
                    self._write_index(previous_index)
                except OSError:
                    pass
                raise PublicationError(
                    f"Revocation audit/index write failed: {error}", code="PUBLICATION_IO"
                ) from error
            return dict(record)

    def history(self, publication_id: str | None = None) -> list[dict[str, Any]]:
        with _lock(self.lock_path):
            payload = self._read_index()
        return [
            dict(record)
            for record in payload["publications"]
            if publication_id is None or record.get("publication_id") == publication_id
        ]


def _store(data_dir: Path) -> PublishedModelStore:
    return PublishedModelStore(data_dir)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Publish validated browser-analysis models (administrator only)"
    )
    parser.add_argument("--data-dir", type=Path, required=True)
    sub = parser.add_subparsers(dest="command", required=True)

    publish = sub.add_parser("publish", help="Validate and publish an ONNX export")
    publish.add_argument("export_dir", type=Path)
    publish.add_argument("--account", required=True, help="Active ADMIN account name")
    publish.add_argument("--name", help="Display name")
    publish.add_argument("--model-id", help="Registered server model ID for provenance")
    publish.add_argument("--ruleset", help="Require an exact ruleset")
    publish.add_argument(
        "--replace", action="store_true", help="Replace the published version for this source"
    )

    revoke = sub.add_parser("revoke", help="Revoke a published model")
    revoke.add_argument("publication_id")
    revoke.add_argument("--account", required=True)
    revoke.add_argument("--reason")

    listing = sub.add_parser("list", help="List published browser models")
    listing.add_argument("--history", action="store_true", help="Include superseded/revoked")

    verify = sub.add_parser("verify", help="Validate an export without publishing")
    verify.add_argument("export_dir", type=Path)

    show = sub.add_parser("show", help="Show one published model manifest")
    show.add_argument("publication_id")

    args = parser.parse_args(argv)
    store = _store(args.data_dir)
    try:
        if args.command == "publish":
            actor = authorize_administrator(args.data_dir, args.account)
            record = store.publish(
                args.export_dir,
                actor=actor,
                display_name=args.name,
                model_id=args.model_id,
                replace=args.replace,
                expected_ruleset=args.ruleset,
            )
            print(json.dumps(store.public_record(record), indent=2, sort_keys=True))
            return 0
        if args.command == "revoke":
            actor = authorize_administrator(args.data_dir, args.account)
            record = store.revoke(args.publication_id, actor=actor, reason=args.reason)
            print(json.dumps({"publication_id": record["publication_id"], "status": "revoked"}))
            return 0
        if args.command == "list":
            entries = store.history() if args.history else store.list_public()
            print(json.dumps(entries, indent=2, sort_keys=True))
            return 0
        if args.command == "verify":
            prepared = store.prepare(args.export_dir)
            print(
                json.dumps(
                    {
                        key: prepared[key]
                        for key in (
                            "publication_id",
                            "ruleset",
                            "board_size",
                            "architecture",
                            "onnx_sha256",
                            "artifact_bytes",
                            "runtime",
                        )
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            return 0
        if args.command == "show":
            shown = store.get_public(args.publication_id)
            if shown is None:
                raise PublicationError("Published model not found", code="NOT_FOUND")
            print(json.dumps(shown, indent=2, sort_keys=True))
            return 0
    except PublicationError as error:
        print(json.dumps({"error": str(error), "code": error.code}))
        return 1
    return 2


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    sys.exit(main())
