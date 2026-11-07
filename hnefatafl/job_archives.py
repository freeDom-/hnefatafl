"""Versioned, bounded training-job migration. No foreign runtime state is restored."""

from __future__ import annotations

import hashlib
import gzip
import io
import json
import os
import re
import shutil
import stat
import tarfile
import tempfile
import threading
from contextlib import nullcontext, contextmanager, ExitStack
from dataclasses import dataclass, field as dataclass_field
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable
from uuid import uuid4

from starlette.exceptions import HTTPException
from starlette.responses import FileResponse, JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .storage import Store

FORMAT = "hnefatafl-training-job"
FORMAT_VERSION = 3
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_EXTRACTED_BYTES = 1024 * 1024 * 1024
MAX_FILE_BYTES = 128 * 1024 * 1024
MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_FILES = 2048
MAX_RATIO = 200
JSON_CHUNK_BYTES = 1024 * 1024
GZIP_LEVEL = 1
JOB_DOCUMENT_FIELDS = {"config", "progress", "result", "metrics", "logs"}
WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *[f"{prefix}{i}" for prefix in ("COM", "LPT") for i in (*range(1, 10), "¹", "²", "³")],
}
TERMINAL = {"completed", "cancelled", "failed"}
HEX_JOB = re.compile(r"[0-9a-f]{32}")
HEX_MODEL = re.compile(r"[0-9a-f]{24}")
# Only known job-owned data types are transferred, never arbitrary adjacent files.
JSON_FILES = {
    "config.json",
    "progress.json",
    "result.json",
    "history.json",
    "metrics.json",
    "evaluation.json",
    "study-artifacts.json",
}
CHECKPOINTS = {"last.pt", "best.pt", "inference.pt", "resume.pt", "objective.pt"}
RUNTIME_KEYS = {
    "pid",
    "worker_pid",
    "worker_id",
    "target_worker_id",
    "worker_name",
    "execution",
    "output_dir",
    "registry_dir",
    "path",
    "resume",
    "resume_source",
    "resume_study",
    "storage",
    "lease_expires",
    "lease_expires_at",
    "lease_sequence",
    "claim_request_id",
    "finish_received",
    "finish_requested_status",
    "uploaded_models",
    "checkpoint_iteration",
    "artifact_owner",
}
SECRET_KEY = re.compile(r"token|secret|password|credential|authorization|lease|api[_-]?key", re.I)
ABSOLUTE_PATH = re.compile(r"(?<![\w:])(?:[A-Za-z]:[\\/]|/)[^\s\"'<>]*")
SECRET_TEXT = re.compile(r"(?i)(?:bearer\s+\S+|(?<![\w-])(?P<key>[\w-]+)[\"']?\s*[:=]\s*[\"']?)")
SECRET_ASSIGNMENT_KEY = re.compile(r"token|secret|password|credential|api_key", re.I)
SECRET_VALUE = re.compile(r"[^\s,\"'}]+")
JOB_FIELDS = {
    "id",
    "kind",
    "status",
    "config",
    "progress",
    "result",
    "metrics",
    "logs",
    "error",
    "created_at",
    "ended_at",
    "started_at",
    "checkpoint",
    "model_id",
    "provenance",
    "software",
}


class ArchiveError(ValueError):
    """A useful, client-safe archive validation error."""


class JobArchiveResponse(FileResponse):
    """Remove the temporary archive even when a download disconnects/fails."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            Path(self.path).unlink(missing_ok=True)


ARCHIVE_BUSY = "Another job archive transfer is in progress. Retry when it finishes."


class ArchiveAdmission:
    """One request owns capacity through body parsing, processing and response.

    If ASGI cancellation abandons a running sync worker, that worker retains the
    slot until its finally block runs. A late worker cannot start on a closed lease.
    """

    def __init__(self, lock):
        self.lock = lock
        self.guard = threading.Lock()
        self.running = False
        self.closed = False
        self.released = False
        self.output: Path | None = None

    @contextmanager
    def worker(self):
        with self.guard:
            if self.closed:
                raise HTTPException(409, "Archive request ended. Retry the transfer.")
            self.running = True
        try:
            yield self
        finally:
            with self.guard:
                self.running = False
                self._release()

    def ensure_open(self):
        with self.guard:
            if self.closed:
                raise HTTPException(
                    409, "Archive request ended before publication. Retry the import."
                )

    def request_finished(self):
        with self.guard:
            self.closed = True
            self._release()

    def _release(self):
        if self.closed and not self.running and not self.released:
            self.released = True
            try:
                if self.output is not None:
                    self.output.unlink(missing_ok=True)
            finally:
                self.lock.release()


class JobUploadLimit:
    """Admit archives before parsing/spooling; bound even chunked multipart input."""

    def __init__(self, app: ASGIApp, archive_lock):
        self.app = app
        self.archive_lock = archive_lock

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        importing = (
            scope.get("method") == "POST" and scope.get("path") == "/api/training/jobs/import"
        )
        exporting = scope.get("method") == "GET" and re.fullmatch(
            r"/api/training/jobs/[^/]+/export", scope.get("path", "")
        )
        if scope["type"] != "http" or not (importing or exporting):
            await self.app(scope, receive, send)
            return
        maximum = MAX_ARCHIVE_BYTES + 1024 * 1024  # Multipart envelope allowance.
        headers = dict(scope.get("headers", []))
        try:
            length = int(headers.get(b"content-length", b"0"))
        except ValueError:
            length = maximum + 1
        if importing and not 0 <= length <= maximum:
            await JSONResponse(
                {"detail": "Job archive upload exceeds the size limit."}, status_code=413
            )(scope, receive, send)
            return
        count = 0

        async def bounded_receive():
            nonlocal count
            message = await receive()
            count += len(message.get("body", b""))
            if count > maximum:
                raise HTTPException(413, "Job archive upload exceeds the size limit.")
            return message

        if not self.archive_lock.acquire(blocking=False):
            await JSONResponse(
                {"detail": ARCHIVE_BUSY}, status_code=409, headers={"Retry-After": "1"}
            )(scope, receive, send)
            return
        admission = ArchiveAdmission(self.archive_lock)
        scope.setdefault("state", {})["archive_admission"] = admission
        try:
            await self.app(scope, bounded_receive if importing else receive, send)
        finally:
            admission.request_finished()


def sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def clean_text(value: str) -> str:
    # Scan complete assignments first. Repeated partial key matches otherwise
    # make long diagnostic words quadratic, even when no credential exists.
    parts: list[str] = []
    position = retained = 0
    while match := SECRET_TEXT.search(value, position):
        position = match.end()
        key = match.group("key")
        if key is not None:
            if not SECRET_ASSIGNMENT_KEY.search(key):
                continue  # Still inspect its value for nested credential assignments.
            content = SECRET_VALUE.match(value, position)
            if content is None:
                continue
            position = content.end()
        parts.extend((value[retained : match.start()], "[redacted]"))
        retained = position
    parts.append(value[retained:])
    return ABSOLUTE_PATH.sub("[local path]", "".join(parts))


def portable(
    value: Any, directory: Path | None = None, depth: int = 0, *, references: dict | None = None
) -> Any:
    """Keep tensor training state, while stripping paths and execution ownership."""
    if depth > 30:
        raise ArchiveError("Job metadata nesting is too deep.")
    if isinstance(value, dict):
        return {
            key: portable(item, directory, depth + 1, references=references)
            for key, item in value.items()
            if not isinstance(key, str) or (key not in RUNTIME_KEYS and not SECRET_KEY.search(key))
        }
    if isinstance(value, (list, tuple)):
        items = [portable(item, directory, depth + 1, references=references) for item in value]
        return tuple(items) if isinstance(value, tuple) else items
    if isinstance(value, str):
        if references and value in references:
            return "job://" + references[value]
        if value.startswith("job://"):
            safe_name(value[6:])
            return value
        if directory and value.startswith(str(directory) + "/"):
            return "job://" + value[len(str(directory)) + 1 :]
        return clean_text(value)
    return value


def safe_name(name: str) -> None:
    path = PurePosixPath(name)
    if (
        not name
        or len(name) > 240
        or "\\" in name
        or ":" in name
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in name.split("/"))
        or any(
            part.endswith((".", " ")) or part.split(".")[0].upper() in WINDOWS_RESERVED
            for part in path.parts
        )
        or any(ord(c) < 32 for c in name)
    ):
        raise ArchiveError("Unsafe archive member path.")


def artifact_name(name: str, *, tuning: bool = False) -> bool:
    path = PurePosixPath(name)
    if tuning:
        if name == "dependencies/source/last.pt":
            return True
        if name in {"study.sqlite3", "best-config.json"}:
            return True
        if re.fullmatch(r"trial-\d{1,10}", path.parts[0]) and len(path.parts) > 1:
            return artifact_name("/".join(path.parts[1:]))
    if len(path.parts) == 1:
        return name in JSON_FILES | CHECKPOINTS | {"worker.log"}
    return (
        len(path.parts) == 2
        and path.parts[0] == "best"
        and bool(re.fullmatch(r"iteration-\d{1,10}\.pt", path.name))
    )


def json_bytes(value: Any, *, maximum: int = MAX_JSON_BYTES) -> bytes:
    data = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
    if len(data) > maximum:
        raise ArchiveError("Job JSON exceeds the metadata size limit.")
    return data


def write_json(path: Path, value: Any, *, maximum: int = MAX_FILE_BYTES) -> None:
    """Avoid a second document-sized string/bytes allocation during restoration."""
    size = 0
    with path.open("wb") as handle:
        for part in json.JSONEncoder(
            ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).iterencode(value):
            data = part.encode("utf-8")
            size += len(data)
            if size > maximum:
                raise ArchiveError("Job JSON data exceeds the artifact size limit.")
            handle.write(data)


def reject_constant(value: str):
    raise ArchiveError(f"Non-finite JSON value: {value}.")


def read_json(path: Path, *, maximum: int = MAX_JSON_BYTES, object_only: bool = True) -> Any:
    if path.stat().st_size > maximum:
        raise ArchiveError("Job JSON exceeds the metadata size limit.")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ArchiveError("Duplicate JSON metadata field.")
            result[key] = value
        return result

    try:
        with path.open(encoding="utf-8") as handle:
            value = json.load(handle, object_pairs_hook=pairs, parse_constant=reject_constant)
        if object_only and not isinstance(value, dict):
            raise ArchiveError("Job JSON must be an object.")
        validate_json(value)
        return value
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise ArchiveError(f"Invalid job JSON: {exc}") from exc


class Documents:
    """Stream canonical JSON into bounded, shared, content-addressed members.

    Chunk boundaries are bytes, not iteration boundaries. No float conversion or
    history sampling occurs, and identical progress/result histories share storage.
    """

    def __init__(self, staging: Path, include: Callable[[Path, str], None]):
        self.staging = staging
        self.include = include
        self.inventory: dict[str, dict] = {}

    def write(self, name: str, value: Any) -> None:
        if name not in self.inventory and len(self.inventory) >= MAX_FILES:
            raise ArchiveError("Job archive exceeds the JSON document count limit.")
        validate_json(value)
        encoder = json.JSONEncoder(
            ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        buffer = bytearray()
        chunks = []
        size = 0

        def flush():
            member = f"data/{hashlib.sha256(buffer).hexdigest()}.json-part"
            target = self.staging / member
            if not target.exists():
                target.parent.mkdir(exist_ok=True)
                target.write_bytes(buffer)
                self.include(target, member)
            chunks.append(member)
            buffer.clear()

        for part in encoder.iterencode(value):
            data = part.encode("utf-8")
            size += len(data)
            if size > MAX_FILE_BYTES:
                raise ArchiveError(f"Job JSON data exceeds the artifact size limit: {name}.")
            offset = 0
            while offset < len(data):
                count = min(JSON_CHUNK_BYTES - len(buffer), len(data) - offset)
                buffer.extend(data[offset : offset + count])
                offset += count
                if len(buffer) == JSON_CHUNK_BYTES:
                    flush()
        if buffer:
            flush()
        self.inventory[name] = {"size": size, "chunks": chunks}


def same_json(left: Any, right: Any) -> bool:
    """Match canonical JSON, including numeric types and the sign of zero."""
    if left is right:
        return True
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(
            same_json(value, right[key]) for key, value in left.items()
        )
    if isinstance(left, (list, tuple)):
        return len(left) == len(right) and all(same_json(a, b) for a, b in zip(left, right))
    if isinstance(left, float):
        return left == right and (left != 0.0 or left.hex() == right.hex())
    return left == right


@dataclass
class ExportSnapshot:
    job: dict
    directory: Path
    source: Path
    references: dict[str, str]
    study_inventory: dict | None
    registry: Any
    catalog: Path | None = None
    pending: list = dataclass_field(default_factory=list)
    resources: ExitStack = dataclass_field(default_factory=ExitStack)
    pin_directories: dict[Path, Path] = dataclass_field(default_factory=dict)
    pin_count: int = 0

    def pin(self, source: Path, destination: Path, *, anchor: Path | None = None) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(source, destination)
        except OSError as exc:
            import errno

            if exc.errno != errno.EXDEV:
                raise
            # A private hard link on the source filesystem retains the inode
            # without one descriptor per artifact. Keep it outside UUID job trees
            # so deleting the original job cannot remove these pins.
            anchor = self.directory.parent if anchor is None else anchor
            try:
                if anchor not in self.pin_directories:
                    self.pin_directories[anchor] = Path(
                        self.resources.enter_context(
                            tempfile.TemporaryDirectory(prefix=".archive-pins-", dir=anchor)
                        )
                    )
                pinned = self.pin_directories[anchor] / str(self.pin_count)
                self.pin_count += 1
                os.link(source, pinned)
            except OSError as error:
                raise ArchiveError(
                    "Cannot establish a safe archive snapshot on the source filesystem. "
                    "Check data-directory permissions and hard-link support, then retry."
                ) from error
            self.pending.append((pinned, destination))

    def materialize(self) -> None:
        pending, self.pending = self.pending, []
        for pinned, destination in pending:
            with pinned.open("rb") as source, destination.open("xb") as output:
                shutil.copyfileobj(source, output, 1024 * 1024)

    def pin_model(self, source: Path) -> Path:
        destination = self.source / "models" / source.name
        if source.is_file() and not source.is_symlink():
            self.pin(source, destination, anchor=self.registry.root)
        return destination

    def pin_catalog(self, source: Path) -> Path:
        destination = self.source / ".model-index.json"
        self.pin(source, destination, anchor=self.registry.root)
        return destination

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.resources.close()


def snapshot_job(job: dict, directory: Path, registry, target: Path) -> ExportSnapshot:
    """Pin inactive immutable files under lifecycle locks; do expensive work later.

    Job/registry publication uses atomic replacement. Hard links keep the old
    inode alive across deletion/replacement without copying tensor buffers. A
    cross-filesystem fallback stages source-filesystem links and defers copying. Model
    references are discovered from these artifacts outside the global locks.
    """
    validate_job(job)
    if directory.is_symlink() or directory.resolve() != directory or directory.parent.is_symlink():
        raise ArchiveError("The saved training directory is not a safe local job directory.")
    refs: dict[str, str] = {}
    inventory = None

    snapshot = ExportSnapshot(job, directory, target, refs, inventory, registry)

    def collect():
        nonlocal refs, inventory
        if job["kind"] == "tune":
            from .job_studies import study_artifacts

            refs, inventory = study_artifacts(directory, job["config"])
        sources = {
            str(path): path.relative_to(directory).as_posix() for path in directory.rglob("*")
        }
        sources.update(refs)
        for source_name, name in sorted(sources.items()):
            source = Path(source_name)
            if source.is_symlink():
                raise ArchiveError("Job artifacts must not contain symlinks.")
            if (
                name == "study.sqlite3"
                or not source.is_file()
                or not artifact_name(name, tuning=job["kind"] == "tune")
            ):
                continue
            if source.stat().st_size > MAX_FILE_BYTES:
                raise ArchiveError("Job artifact exceeds the file size limit.")
            snapshot.pin(source, target / name)
        if job["kind"] == "tune":
            from .job_studies import backup_study

            backup_study(directory, job["config"], target / "study.sqlite3")

    target.mkdir(parents=True, exist_ok=True)
    try:
        if job["kind"] == "tune":
            from .training_identity import study_lease

            with study_lease({**job["config"], "output_dir": str(directory)}):
                collect()
        else:
            collect()
        snapshot.catalog = registry.pin_index(snapshot.pin_catalog)
    except BaseException:
        snapshot.resources.close()
        raise
    snapshot.references, snapshot.study_inventory = refs, inventory
    return snapshot


def validate_json(value: Any, depth: int = 0) -> None:
    if depth > 25:
        raise ArchiveError("Job JSON nesting is too deep.")
    if isinstance(value, dict):
        if len(value) > 10000 or any(len(key) > 200 for key in value):
            raise ArchiveError("Job JSON object exceeds field limits.")
        for item in value.values():
            validate_json(item, depth + 1)
    elif isinstance(value, list):
        if len(value) > 100000:
            raise ArchiveError("Job JSON list exceeds field limits.")
        for item in value:
            validate_json(item, depth + 1)
    elif isinstance(value, str) and len(value) > 1000000:
        raise ArchiveError("Job JSON string exceeds field limits.")


def training_config(value: Any) -> dict:
    from .training import normalize_config

    if not isinstance(value, dict):
        raise ArchiveError("Training configuration must be an object.")
    config = normalize_config(portable(value))
    if (
        config["device"] not in {"auto", "cpu", "cuda", "mps"}
        or type(config["mixed_precision"]) is not bool
    ):
        raise ArchiveError("Invalid training execution settings.")
    return portable(config)


def validate_job(job: dict) -> None:
    if not isinstance(job.get("id"), str) or not HEX_JOB.fullmatch(job["id"]):
        raise ArchiveError("Invalid original job ID.")
    if job.get("kind") not in {"train", "tune"} or job.get("status") not in TERMINAL:
        raise ArchiveError("Only completed, cancelled or failed training jobs can be transferred.")
    for key in ("created_at", "started_at", "ended_at"):
        stamp = job.get(key)
        if stamp is None and key != "created_at":
            continue
        if not isinstance(stamp, str) or len(stamp) > 64:
            raise ArchiveError(f"Invalid job {key} timestamp.")
        datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    for key in ("progress", "result", "metrics", "provenance", "software"):
        if key in job and not isinstance(job[key], dict):
            raise ArchiveError(f"Job {key} must be an object.")
    for key in ("error", "checkpoint"):
        if job.get(key) is not None and not isinstance(job[key], str):
            raise ArchiveError(f"Job {key} must be text.")
    logs = job.get("logs", "")
    if not isinstance(logs, str) and not (
        isinstance(logs, list) and all(isinstance(line, str) for line in logs)
    ):
        raise ArchiveError("Job logs must contain text.")
    validate_model_references(job)
    job_config(job.get("config"), job["kind"])


def validate_model_references(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "model" and item is not None:
                if (
                    not isinstance(item, dict)
                    or not isinstance(item.get("id"), str)
                    or not HEX_MODEL.fullmatch(item["id"])
                ):
                    raise ArchiveError("Invalid job model association.")
            if (
                key == "model_id"
                and item is not None
                and (not isinstance(item, str) or not HEX_MODEL.fullmatch(item))
            ):
                raise ArchiveError("Invalid job model ID.")
            validate_model_references(item)
    elif isinstance(value, list):
        for item in value:
            validate_model_references(item)


def job_config(value: Any, kind: str) -> dict:
    if kind == "tune":
        from .job_studies import tuning_config

        return tuning_config(value)
    return training_config(value)


def write_checkpoint(
    source: Path,
    target: Path,
    directory: Path | None = None,
    ids: dict | None = None,
    *,
    references: dict | None = None,
) -> dict:
    import torch
    from .models import load_checkpoint

    model, payload = load_checkpoint(source)
    payload = portable(payload, directory, references=references)
    saved = payload["metadata"].get("training_config")
    if saved is not None:
        normalized = training_config(saved)
        for key, actual in model.config.items():
            if key in saved and normalized[key] != actual:
                raise ArchiveError(
                    "Checkpoint architecture does not match its training configuration."
                )
    if ids:
        payload = rewrite(payload, ids, directory=directory)
    # A file object gives deterministic ZIP member names, independent of staging paths.
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("wb") as handle:
        torch.save(payload, handle)
    return payload


def model_ids(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "model_id" and isinstance(item, str) and HEX_MODEL.fullmatch(item):
                found.add(item)
            elif key == "model" and isinstance(item, dict) and isinstance(item.get("id"), str):
                found.add(item["id"])
            found.update(model_ids(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            found.update(model_ids(item))
    return found


def export_job(job: dict, directory: Path, registry, output: Path, app_version: str) -> None:
    """Standalone export; API callers pin a snapshot under their lifecycle locks."""
    with tempfile.TemporaryDirectory(prefix="job-snapshot-", dir=directory.parent) as temporary:
        snapshot = snapshot_job(job, directory, registry, Path(temporary))
        export_snapshot(snapshot, output, app_version)


def export_snapshot(snapshot: ExportSnapshot, output: Path, app_version: str) -> None:
    """Sanitize/serialize/compress a pinned snapshot without lifecycle/catalog locks."""
    try:
        snapshot.materialize()
        _export_snapshot(snapshot, output, app_version)
    except BaseException:
        output.unlink(missing_ok=True)
        raise
    finally:
        snapshot.resources.close()


def _export_snapshot(snapshot: ExportSnapshot, output: Path, app_version: str) -> None:
    job, directory = snapshot.job, snapshot.directory
    with tempfile.TemporaryDirectory(prefix="job-export-") as temporary:
        staging = Path(temporary)
        artifact_refs = snapshot.references
        study_inventory = snapshot.study_inventory
        record = portable(
            {key: value for key, value in job.items() if key in JOB_FIELDS},
            directory,
            references=artifact_refs,
        )
        source = job.get("config", {}).get("training", {}).get("resume")
        if source and source in artifact_refs:
            record["config"]["source_checkpoint"] = "job://" + artifact_refs[source]
        # Remote inference copies live in the registry. A failed worker may
        # never send the final report that would expose those associations.
        uploaded = job.get("uploaded_models", {})
        if isinstance(uploaded, dict) and uploaded:
            inference = uploaded.get("inference")
            if isinstance(inference, dict) and HEX_MODEL.fullmatch(str(inference.get("id", ""))):
                record.setdefault("model_id", inference["id"])
                record.setdefault("progress", {}).setdefault("model", portable(inference))
            retained = record.setdefault("progress", {}).setdefault("best_models", [])
            for kind, model in uploaded.items():
                if (
                    re.fullmatch(r"best-\d{1,10}", kind)
                    and isinstance(model, dict)
                    and HEX_MODEL.fullmatch(str(model.get("id", "")))
                ):
                    retained_entry = {"iteration": int(kind[5:]), "model_id": model["id"]}
                    if retained_entry not in retained:
                        retained.append(retained_entry)
        provenance = record.setdefault("provenance", {})
        provenance.setdefault("original_job_id", job["id"])
        provenance.setdefault("original_execution", job.get("execution", "local"))
        provenance.setdefault("original_model_ids", sorted(model_ids(record)))
        references = model_ids(record)
        files: dict[str, dict] = {}

        def include(path: Path, name: str):
            if path.stat().st_size > MAX_FILE_BYTES or len(files) >= MAX_FILES - 1:
                raise ArchiveError("Job archive exceeds file limits.")
            files[name] = {"size": path.stat().st_size, "sha256": sha256(path)}
            if sum(item["size"] for item in files.values()) > MAX_EXTRACTED_BYTES:
                raise ArchiveError("Job archive exceeds the extracted size limit.")

        documents = Documents(staging, include)
        section_documents: dict[str, dict] = {}
        if snapshot.source.exists():
            sources = {
                str(path): path.relative_to(snapshot.source).as_posix()
                for path in snapshot.source.rglob("*")
            }
            for source_name, name in sorted(sources.items()):
                source = Path(source_name)
                if source.is_symlink():
                    raise ArchiveError("Job artifacts must not contain symlinks.")
                if not source.is_file() or not artifact_name(name, tuning=job["kind"] == "tune"):
                    continue
                if source.stat().st_size > MAX_FILE_BYTES:
                    raise ArchiveError("Job artifact exceeds the file size limit.")
                target = staging / name
                target.parent.mkdir(parents=True, exist_ok=True)
                if name == "study.sqlite3":
                    continue  # Copied below through SQLite's consistent backup API.
                if source.suffix == ".pt":
                    payload = write_checkpoint(source, target, directory, references=artifact_refs)
                    references.update(model_ids(payload))
                elif source.suffix == ".json":
                    data = portable(
                        read_json(source, maximum=MAX_FILE_BYTES),
                        directory,
                        references=artifact_refs,
                    )
                    validate_model_references(data)
                    references.update(model_ids(data))
                    documents.write(name, data)
                    for section in JOB_DOCUMENT_FIELDS & record.keys():
                        if same_json(data, record[section]):
                            section_documents[section] = documents.inventory[name]
                    continue
                else:
                    with source.open(errors="replace") as src, target.open("w") as dst:
                        while chunk := src.readline(65536):
                            dst.write(clean_text(chunk))
                include(target, name)
        if job["kind"] == "tune":
            shutil.copyfile(snapshot.source / "study.sqlite3", staging / "study.sqlite3")
            from .job_studies import migrate_study

            migrate_study(
                staging / "study.sqlite3",
                snapshot.source,
                source_root=directory,
                study_name=job["config"].get("study_name", "hnefatafl"),
                references=artifact_refs,
            )
            documents.write("study-artifacts.json", study_inventory)
            # config.json must carry the portable source dependency too.
            documents.write("config.json", record["config"])
            section_documents["config"] = documents.inventory["config.json"]
            include(staging / "study.sqlite3", "study.sqlite3")
            from .job_studies import portable_study_report

            projection = portable_study_report(staging / "study.sqlite3")
            for section in ("progress", "result"):
                previous = record.get(section) or {}
                # Keep old verification as historical evidence when its model is
                # no longer the current study winner. Never attach it to a new one.
                if previous.get("best_trial") != projection.get("best_trial"):
                    if previous.get("verification_result"):
                        previous["historical_verification"] = previous.pop("verification_result")
                    previous["verification_status"] = "not_requested"
                    previous.pop("verification_error", None)
                    previous.pop("parameter_importance", None)
                record[section] = {**previous, **projection}
                documents.write(f"{section}.json", record[section])
                section_documents[section] = documents.inventory[f"{section}.json"]
            if projection.get("best_configuration"):
                documents.write("best-config.json", projection["best_configuration"])

        # DB configuration and remote reports may be the only copies available.
        for name, value in (
            ("config.json", record["config"]),
            ("progress.json", record.get("progress")),
            ("result.json", record.get("result")),
        ):
            if name not in documents.inventory and value is not None:
                documents.write(name, value)
                section_documents[name[:-5]] = documents.inventory[name]
        if job["kind"] == "tune":
            references = {
                entry["model_id"]
                for entry in job["config"].get("opponents", [])
                if entry.get("opponent") == "frozen_model"
            }
        frozen_pins = {}
        if job["kind"] == "tune":
            from .job_studies import frozen_study_dependencies

            frozen_pins = frozen_study_dependencies(job["config"], staging / "study.sqlite3")
        models = []
        # Discovery above includes reports/checkpoints, uploaded best models and
        # frozen dependencies. Only this complete set touches registry files.
        entries = snapshot.registry.pin_entries(
            references, snapshot.pin_model, snapshot=snapshot.catalog
        )
        snapshot.materialize()
        available = {entry["id"]: entry for entry in entries}
        for identifier in sorted(references):
            entry = available.get(identifier)
            if not entry:
                if identifier in frozen_pins:
                    raise ArchiveError(
                        f"Required frozen model {identifier} is missing from the registry; restore it before exporting this study."
                    )
                continue  # Deleted inference copies remain historical references only.
            name = f"models/{identifier}.pt"
            source = Path(entry["path"])
            if source.is_symlink() or not source.is_file():
                if identifier in frozen_pins:
                    raise ArchiveError(f"Required frozen model {identifier} is missing or unsafe.")
                raise ArchiveError("An associated registered model is missing or unsafe.")
            if identifier in frozen_pins:
                from .job_studies import validate_frozen_artifact

                validate_frozen_artifact(
                    source,
                    identifier,
                    frozen_pins[identifier],
                    job["config"]["training"]["ruleset"],
                )
            write_checkpoint(source, staging / name)
            include(staging / name, name)
            models.append({"original_id": identifier, "file": name, "name": entry["name"]})
        job_fields = {}
        for key in sorted(JOB_DOCUMENT_FIELDS & record.keys()):
            name = f"metadata/{key}.json"
            value = record.pop(key)
            if key in section_documents:
                documents.inventory[name] = section_documents[key]
            else:
                documents.write(name, value)
            job_fields[key] = name
        if len(documents.inventory) > MAX_FILES:
            raise ArchiveError("Job archive exceeds the JSON document count limit.")
        (staging / "job.json").write_bytes(json_bytes(record))
        include(staging / "job.json", "job.json")
        # Study projection can replace an older report. Only retain chunks still
        # referenced by the final documents, including shared record sections.
        used_chunks = {chunk for item in documents.inventory.values() for chunk in item["chunks"]}
        for name in list(files):
            if name.startswith("data/") and name not in used_chunks:
                del files[name]
                (staging / name).unlink()
        manifest = {
            "format": FORMAT,
            "format_version": FORMAT_VERSION,
            "app_version": app_version,
            "original_job_id": job["id"],
            "job_type": job["kind"],
            "files": files,
            "models": models,
            "documents": documents.inventory,
            "job_fields": job_fields,
        }
        # Bound reconstructed data too: deduplication must not permit a logical bomb.
        logical_size = sum(item["size"] for item in documents.inventory.values()) + sum(
            item["size"] for name, item in files.items() if not name.startswith("data/")
        )
        if logical_size > MAX_EXTRACTED_BYTES:
            raise ArchiveError("Job archive exceeds the reconstructed size limit.")
        manifest_data = json_bytes(manifest)
        if len(manifest_data) + sum(item["size"] for item in files.values()) > MAX_EXTRACTED_BYTES:
            raise ArchiveError("Job archive exceeds the extracted size limit.")
        write_tar(output, manifest_data, staging, files)
        if output.stat().st_size > MAX_ARCHIVE_BYTES:
            raise ArchiveError("Job archive exceeds the 512 MiB archive limit.")


def write_tar(output: Path, manifest: bytes, staging: Path, files: dict) -> None:
    """One gzip pass; do not separately recompress the stored tensor ZIP payloads."""
    with (
        output.open("wb") as raw,
        gzip.GzipFile(
            filename="", fileobj=raw, mode="wb", compresslevel=GZIP_LEVEL, mtime=0
        ) as compressed,
        tarfile.open(fileobj=compressed, mode="w|", format=tarfile.USTAR_FORMAT) as archive,
    ):
        for name in ["manifest.json", *sorted(files)]:
            member = tarfile.TarInfo(name)
            member.mode = 0o600
            member.size = len(manifest) if name == "manifest.json" else files[name]["size"]
            with (
                io.BytesIO(manifest)
                if name == "manifest.json"
                else (staging / name).open("rb") as src
            ):
                archive.addfile(member, src)
            if raw.tell() > MAX_ARCHIVE_BYTES:
                raise ArchiveError("Job archive exceeds the 512 MiB archive limit.")


def extract(archive_path: Path, staging: Path) -> dict:
    if archive_path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ArchiveError("Job archive exceeds the 512 MiB archive limit.")
    try:
        with archive_path.open("rb") as handle:
            signature = handle.read(4)
        if signature.startswith(b"\x1f\x8b"):
            names = extract_tar(archive_path, staging)
        elif zipfile.is_zipfile(archive_path):
            names = extract_zip(archive_path, staging)
        else:
            raise ArchiveError("Expected a TAR.GZ job archive or a legacy ZIP archive.")
    except (tarfile.TarError, zipfile.BadZipFile, EOFError, gzip.BadGzipFile) as exc:
        raise ArchiveError("Damaged job archive; upload a complete TAR.GZ or ZIP export.") from exc
    manifest = read_json(staging / "manifest.json")
    if (
        manifest.get("format") != FORMAT
        or type(manifest.get("format_version")) is not int
        or manifest["format_version"] not in {1, 2, FORMAT_VERSION}
    ):
        raise ArchiveError("Unsupported training-job archive format version.")
    if (
        not isinstance(manifest.get("app_version"), str)
        or not 1 <= len(manifest["app_version"]) <= 64
    ):
        raise ArchiveError("Invalid archive application version.")
    files = manifest.get("files")
    required = {"job.json"} if manifest["format_version"] == 3 else {"job.json", "config.json"}
    if (
        not isinstance(files, dict)
        or set(files) != names - {"manifest.json"}
        or not required <= files.keys()
    ):
        raise ArchiveError("Job archive inventory is incomplete or inconsistent.")
    for name, details in files.items():
        if (
            not isinstance(details, dict)
            or type(details.get("size")) is not int
            or not isinstance(details.get("sha256"), str)
        ):
            raise ArchiveError("Invalid archive checksum metadata.")
        path = staging / name
        if details["size"] != path.stat().st_size or details["sha256"] != sha256(path):
            raise ArchiveError(f"Job archive checksum mismatch: {name}.")
    return manifest


def check_member(name: str, size: int, names: set[str], total: int) -> int:
    safe_name(name)
    folded = name.casefold()
    if folded in names:
        raise ArchiveError("Duplicate or conflicting archive member.")
    if len(names) >= MAX_FILES:
        raise ArchiveError("Job archive exceeds the file count limit.")
    if size < 0 or size > MAX_FILE_BYTES or total + size > MAX_EXTRACTED_BYTES:
        raise ArchiveError("Job archive exceeds the extracted size limit.")
    if name in {"manifest.json", "job.json"} and size > MAX_JSON_BYTES:
        raise ArchiveError("Job JSON exceeds the metadata size limit.")
    names.add(folded)
    return total + size


def check_names(names: set[str]) -> None:
    for name in names:
        if any(
            str(parent) in names for parent in PurePosixPath(name).parents if str(parent) != "."
        ):
            raise ArchiveError("Conflicting archive file and directory members.")
    if "manifest.json" not in names:
        raise ArchiveError("Job archive is missing manifest.json.")


def copy_member(src, target: Path, size: int) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with target.open("xb") as dst:
        while chunk := src.read(min(1024 * 1024, size - count + 1)):
            count += len(chunk)
            if count > size:
                raise ArchiveError("Archive member exceeds its declared size.")
            dst.write(chunk)
    if count != size:
        raise ArchiveError("Archive member is incomplete.")


class RegularTarInfo(tarfile.TarInfo):
    @classmethod
    def fromtarfile(cls, archive):
        # Parse only the public fixed-size header API. Some Python patch levels
        # bypass frombuf overrides when processing extension headers internally.
        member = cls.frombuf(
            archive.fileobj.read(tarfile.BLOCKSIZE), archive.encoding, archive.errors
        )
        member.offset_data = archive.fileobj.tell()
        member.offset = member.offset_data - tarfile.BLOCKSIZE
        archive.offset = (
            member.offset_data
            + ((member.size + tarfile.BLOCKSIZE - 1) // tarfile.BLOCKSIZE) * tarfile.BLOCKSIZE
        )
        return member

    @classmethod
    def frombuf(cls, buf, encoding, errors):
        # Reject extended/sparse headers BEFORE tarfile reads their attacker-sized
        # payloads. Our USTAR writer needs only ordinary files, including on 3.11.
        member = super().frombuf(buf, encoding, errors)
        if member.type not in {tarfile.REGTYPE, tarfile.AREGTYPE}:
            raise ArchiveError("Unsupported TAR entry: only regular files are allowed.")
        return member


class BoundedTarReader:
    def __init__(self, source):
        self.source = source
        self.remaining = MAX_EXTRACTED_BYTES + MAX_FILES * 1024 + 10240

    def read(self, size: int = -1) -> bytes:
        data = self.source.read(min(size if size >= 0 else 1024 * 1024, self.remaining + 1))
        self.remaining -= len(data)
        if self.remaining < 0:
            raise ArchiveError("Job archive exceeds the decompressed TAR size limit.")
        return data


def extract_tar(archive_path: Path, staging: Path) -> set[str]:
    names: set[str] = set()
    original_names: set[str] = set()
    total = 0
    with gzip.open(archive_path, "rb") as compressed:
        bounded: Any = BoundedTarReader(compressed)
        with tarfile.open(fileobj=bounded, mode="r|", tarinfo=RegularTarInfo) as archive:
            for item in archive:
                total = check_member(item.name, item.size, names, total)
                # Check prefixes before creating directories, even in streaming mode.
                if any(
                    name.startswith(item.name.casefold() + "/")
                    for name in names - {item.name.casefold()}
                ):
                    raise ArchiveError("Conflicting archive file and directory members.")
                if any(
                    str(parent).casefold() in names
                    for parent in PurePosixPath(item.name).parents
                    if str(parent) != "."
                ):
                    raise ArchiveError("Conflicting archive file and directory members.")
                original_names.add(item.name)
                src = archive.extractfile(item)
                if src is None:
                    raise ArchiveError("Unreadable TAR member.")
                with src:
                    copy_member(src, staging / item.name, item.size)
        # tarfile stops at its end marker. Drain bounded gzip to validate CRC/EOF
        # and prevent an unbounded hidden trailing decompression stream.
        while bounded.read(1024 * 1024):
            pass
    check_names(names)
    if "manifest.json" not in original_names:
        raise ArchiveError("Job archive is missing manifest.json.")
    return original_names


def extract_zip(archive_path: Path, staging: Path) -> set[str]:
    with zipfile.ZipFile(archive_path) as archive:
        infos = archive.infolist()
        if not infos or len(infos) > MAX_FILES:
            raise ArchiveError("Job archive exceeds the file count limit.")
        names: set[str] = set()
        total = 0
        for item in infos:
            mode = item.external_attr >> 16
            if (
                item.is_dir()
                or stat.S_IFMT(mode) not in {0, stat.S_IFREG}
                or item.flag_bits & 1
                or item.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
            ):
                raise ArchiveError("Unsupported archive entry type or encryption.")
            total = check_member(item.filename, item.file_size, names, total)
            if item.file_size > max(1, item.compress_size) * MAX_RATIO:
                raise ArchiveError("Job archive exceeds the decompression ratio limit.")
        check_names(names)
        if "manifest.json" not in {item.filename for item in infos}:
            raise ArchiveError("Job archive is missing manifest.json.")
        # Extract manually, with actual byte bounds; ZipFile.extract is never used.
        for item in infos:
            with archive.open(item) as src:
                copy_member(src, staging / item.filename, item.file_size)
        return {item.filename for item in infos}


def restore_documents(manifest: dict, staging: Path, record: dict) -> dict:
    """Validate the logical inventory before reconstructing any large document."""
    documents = manifest.get("documents")
    fields = manifest.get("job_fields")
    files = manifest["files"]
    if (
        not isinstance(documents, dict)
        or not 1 <= len(documents) <= MAX_FILES
        or not isinstance(fields, dict)
        or not {"config"} <= fields.keys() <= JOB_DOCUMENT_FIELDS
        or any(key in record for key in fields)
        or any(value != f"metadata/{key}.json" for key, value in fields.items())
        or not set(fields.values()) <= documents.keys()
        or "config.json" not in documents
    ):
        raise ArchiveError("Invalid archive JSON document inventory or job field references.")
    used = set()
    total = sum(item["size"] for name, item in files.items() if not name.startswith("data/"))
    folded = {name.casefold() for name in files}
    for name, details in documents.items():
        safe_name(name)
        if (
            name.casefold() in folded
            or not (
                artifact_name(name, tuning=manifest.get("job_type") == "tune")
                and name.endswith(".json")
                or name in fields.values()
            )
            or not isinstance(details, dict)
            or type(details.get("size")) is not int
            or not 1 <= details["size"] <= MAX_FILE_BYTES
            or not isinstance(details.get("chunks"), list)
            or not 1
            <= len(details["chunks"])
            <= (MAX_FILE_BYTES + JSON_CHUNK_BYTES - 1) // JSON_CHUNK_BYTES
        ):
            raise ArchiveError("Invalid archive JSON document.")
        folded.add(name.casefold())
        total += details["size"]
        if total > MAX_EXTRACTED_BYTES:
            raise ArchiveError("Job archive exceeds the reconstructed size limit.")
        size = 0
        for index, chunk in enumerate(details["chunks"]):
            if (
                not isinstance(chunk, str)
                or not re.fullmatch(r"data/[0-9a-f]{64}\.json-part", chunk)
                or chunk not in files
                or files[chunk]["sha256"] != chunk[5:-10]
                or not 1 <= files[chunk]["size"] <= JSON_CHUNK_BYTES
                or index < len(details["chunks"]) - 1
                and files[chunk]["size"] != JSON_CHUNK_BYTES
            ):
                raise ArchiveError("Invalid or missing archive JSON chunk.")
            size += files[chunk]["size"]
            used.add(chunk)
        if size != details["size"]:
            raise ArchiveError("Archive JSON document size does not match its chunks.")
    check_names(folded | {"manifest.json"})
    if used != {name for name in files if name.startswith("data/")}:
        raise ArchiveError("Archive contains unreferenced JSON chunks.")
    for name, details in documents.items():
        target = staging / name
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as output:
            for chunk in details["chunks"]:
                with (staging / chunk).open("rb") as source:
                    shutil.copyfileobj(source, output, 1024 * 1024)
    for field, name in fields.items():
        record[field] = read_json(
            staging / name, maximum=MAX_FILE_BYTES, object_only=field != "logs"
        )
    # The ordinary importer receives the same filenames as legacy archives.
    # Private record documents/chunks never enter a published job directory.
    for name in fields.values():
        (staging / name).unlink()
    shutil.rmtree(staging / "metadata", ignore_errors=True)
    shutil.rmtree(staging / "data", ignore_errors=True)
    manifest["files"] = {
        **{name: details for name, details in files.items() if not name.startswith("data/")},
        **{name: details for name, details in documents.items() if name not in fields.values()},
    }
    return record


def rewrite(value: Any, ids: dict, directory: Path | None = None) -> Any:
    if isinstance(value, dict):
        result = {
            ids.get(key, key) or key: item if key == "provenance" else rewrite(item, ids, directory)
            for key, item in value.items()
        }
        if isinstance(result.get("model"), dict) and result["model"].get("id") is None:
            result["model"] = None
        return result
    if isinstance(value, (list, tuple)):
        items = [rewrite(item, ids, directory) for item in value]
        return tuple(items) if isinstance(value, tuple) else items
    if isinstance(value, str):
        if value in ids:
            return ids[value]
        if value.startswith("job://"):
            name = value[6:]
            safe_name(name)
            if not artifact_name(name, tuning=True):
                raise ArchiveError("Invalid internal artifact reference.")
            return str(directory / name) if directory else value
    return value


def import_job(
    archive: Path,
    root: Path,
    store: Store,
    *,
    resume_info: Callable[[dict, Path], dict],
    publication_context: Callable = nullcontext,
) -> dict:
    """Validate in isolation, then publish under caller's lifecycle/catalog locks."""
    from .models import ModelRegistry

    jobs_root = root / "jobs"
    if jobs_root.is_symlink() or jobs_root.resolve() != jobs_root:
        raise ArchiveError("The destination job store is not a safe local directory.")
    models_root = root / "models"
    if models_root.is_symlink() or models_root.resolve() != models_root:
        raise ArchiveError("The destination model store is not a safe local directory.")
    with tempfile.TemporaryDirectory(prefix=".job-import-", dir=root) as temporary:
        staging = Path(temporary)
        manifest = extract(archive, staging)
        record = read_json(staging / "job.json")
        modern = manifest["format_version"] == 3
        if modern:
            record = restore_documents(manifest, staging, record)
        json_limit = MAX_FILE_BYTES if modern else MAX_JSON_BYTES
        validate_job(record)
        if (
            manifest.get("original_job_id") != record["id"]
            or manifest.get("job_type") != record["kind"]
        ):
            raise ArchiveError("Archive manifest does not match the job metadata.")
        entries = manifest.get("models")
        if not isinstance(entries, list) or len(entries) > MAX_FILES:
            raise ArchiveError("Invalid associated model inventory.")
        models: dict[str, dict] = {}
        for entry in entries:
            if (
                not isinstance(entry, dict)
                or not isinstance(entry.get("original_id"), str)
                or not HEX_MODEL.fullmatch(entry["original_id"])
            ):
                raise ArchiveError("Invalid associated model ID.")
            old = entry["original_id"]
            if (
                old in models
                or entry.get("file") != f"models/{old}.pt"
                or entry["file"] not in manifest["files"]
            ):
                raise ArchiveError("Invalid associated model artifact.")
            if not isinstance(entry.get("name"), str) or not 1 <= len(entry["name"].strip()) <= 100:
                raise ArchiveError("Invalid associated model name.")
            models[old] = entry
        allowed = {"job.json", *[entry["file"] for entry in models.values()]}
        for name in manifest["files"]:
            if name not in allowed and not artifact_name(name, tuning=record["kind"] == "tune"):
                raise ArchiveError(f"Unsupported job artifact: {name}.")
        record = portable({key: value for key, value in record.items() if key in JOB_FIELDS})
        config_file = job_config(
            read_json(staging / "config.json", maximum=json_limit), record["kind"]
        )
        record["config"] = job_config(record["config"], record["kind"])
        if config_file != record["config"]:
            raise ArchiveError("Job configuration does not match config.json.")
        checkpoint_refs = set()
        for name in manifest["files"]:
            path = staging / name
            if path.suffix == ".pt":
                payload = write_checkpoint(path, path)
                checkpoint_refs.update(model_ids(payload))
                if record["kind"] == "train":
                    model_config = payload["model_config"]
                    if any(model_config[key] != record["config"][key] for key in model_config):
                        raise ArchiveError(
                            "Checkpoint architecture does not match the imported job."
                        )
            elif path.suffix == ".json" and name != "job.json":
                data = portable(read_json(path, maximum=json_limit))
                validate_model_references(data)
                write_json(path, data, maximum=json_limit)
            elif name == "worker.log":
                with (
                    path.open(errors="replace") as src,
                    path.with_suffix(".clean").open("w") as dst,
                ):
                    while chunk := src.readline(65536):
                        dst.write(clean_text(chunk))
                path.with_suffix(".clean").replace(path)
        if record["kind"] == "tune":
            from .job_studies import migrate_study

            if "study.sqlite3" not in manifest["files"]:
                raise ArchiveError("Optimization archive is missing its owned study database.")
            migrate_study(
                staging / "study.sqlite3", staging, study_name=record["config"]["study_name"]
            )
        if record["kind"] == "tune":
            from .job_studies import frozen_study_dependencies, validate_frozen_artifact

            pins = frozen_study_dependencies(record["config"], staging / "study.sqlite3")
            for model_id, expected in pins.items():
                if model_id not in models:
                    raise ArchiveError(
                        f"Required frozen model {model_id} is missing from the archive."
                    )
                validate_frozen_artifact(
                    staging / models[model_id]["file"],
                    model_id,
                    expected,
                    record["config"]["training"]["ruleset"],
                )
        with publication_context():
            identifier = uuid4().hex
            destination = jobs_root / identifier
            if destination.exists() or store.get_job(identifier):
                raise ArchiveError("Could not allocate an unused job ID; retry the import.")
            old_id = record["id"]
            ids: dict[str, Any] = {old_id: identifier}
            record.update(id=identifier, execution="local")
            record["provenance"] = {
                **record.get("provenance", {}),
                "original_job_id": record.get("provenance", {}).get("original_job_id", old_id),
                "source_job_id": old_id,
                "archive_app_version": manifest["app_version"],
                "imported_at": datetime.now(timezone.utc).isoformat(),
            }
            registry = ModelRegistry(root / "models")
            moved = False
            try:
                with registry.import_transaction():
                    frozen_dependencies = {
                        entry["model_id"]
                        for entry in record["config"].get("opponents", [])
                        if entry.get("opponent") == "frozen_model"
                    }
                    for old, entry in models.items():
                        if record["kind"] == "tune" and old not in frozen_dependencies:
                            continue  # Legacy trial registrations stay local artifacts.
                        path = staging / entry["file"]
                        # The registry is the sole authority for registering or reusing
                        # archive models. It verifies each owned copy's immutable weight
                        # identity and integrity hash before deduplication, so a replaced
                        # but valid registered checkpoint can never be adopted here.
                        registered = registry.import_model(path, entry["name"])
                        if record["kind"] == "tune":
                            # A catalog ID may already exist with damaged bytes. Do
                            # not silently reuse it, or overwrite it during import.
                            validate_frozen_artifact(
                                Path(registered["path"]),
                                old,
                                pins[old],
                                record["config"]["training"]["ruleset"],
                            )
                        ids[old] = registered["id"]
                    # Always register an inference artifact, including jobs whose source
                    # catalog copy had been removed before export.
                    for inference in (
                        sorted(staging.rglob("inference.pt")) if record["kind"] == "train" else []
                    ):
                        registered = registry.import_model(
                            inference,
                            record["config"].get(
                                "model_name", record["config"].get("training", {}).get("model_name")
                            ),
                        )
                        ids.setdefault(registered["id"], registered["id"])
                        for report_name in ("progress.json", "result.json"):
                            report_path = inference.parent / report_name
                            if report_path.is_file():
                                previous_model = read_json(report_path, maximum=json_limit).get(
                                    "model"
                                )
                                if isinstance(previous_model, dict) and previous_model.get("id"):
                                    ids[previous_model["id"]] = registered["id"]
                        if record["kind"] == "train":
                            record["model_id"] = registered["id"]
                            for section in ("progress", "result"):
                                record.setdefault(section, {})["model"] = registered
                    for missing in (
                        (model_ids(record) | checkpoint_refs) - ids.keys() - set(ids.values())
                    ):
                        ids[missing] = None
                    record = rewrite(record, ids, destination)
                    report_summaries = {}
                    for name in manifest["files"]:
                        if name == "job.json" or name.startswith("models/"):
                            continue
                        path = staging / name
                        if path.suffix == ".pt":
                            # Rebase only job checkpoints; registered inference copies stay
                            # byte-identical for content-addressed deduplication.
                            write_checkpoint(path, path, destination, ids)
                        elif path.suffix == ".json":
                            data = rewrite(read_json(path, maximum=json_limit), ids, destination)
                            if name in {"progress.json", "result.json"}:
                                data["status"] = record["status"]
                                if record.get("model_id"):
                                    data["model"] = registry.get(record["model_id"])
                                if record["kind"] == "tune":
                                    data["storage"] = "sqlite:///" + str(
                                        destination / "study.sqlite3"
                                    )
                                from .training_monitoring import imported_report_summary

                                report_summaries[name[:-5]] = imported_report_summary(data)
                                if not modern:
                                    record[name[:-5]] = data
                            write_json(path, data, maximum=json_limit)
                    if record["kind"] == "tune":
                        from .job_studies import migrate_study

                        migrate_study(
                            staging / "study.sqlite3", staging, destination=destination, ids=ids
                        )
                        storage = "sqlite:///" + str(destination / "study.sqlite3")
                        record["config"]["storage"] = storage
                        for section in ("progress", "result"):
                            if section in record:
                                record[section]["storage"] = storage
                    from .training_monitoring import imported_report_summary

                    for section in ("progress", "result"):
                        if section not in report_summaries:
                            report_summaries[section] = imported_report_summary(
                                record.get(section, {})
                            )
                    for section in ("metrics", "provenance", "software"):
                        report_summaries[section] = imported_report_summary(
                            {"metrics": record.get(section, {})}
                        ).get("metrics", {})
                    record["_import_reports"] = report_summaries
                    record.pop("checkpoint", None)
                    info = resume_info(record, staging)
                    record["resumable"] = info["can_resume"]
                    if info["can_resume"]:
                        record["checkpoint"] = f"{identifier}:last.pt"
                    (staging / "config.json").write_bytes(
                        json_bytes(
                            {
                                **record["config"],
                                "output_dir": str(destination),
                                "registry_dir": str(root / "models"),
                            }
                        )
                    )
                    for path in (staging / "job.json", staging / "manifest.json"):
                        path.unlink()
                    shutil.rmtree(staging / "models", ignore_errors=True)
                    with store.connection() as connection:
                        connection.execute("BEGIN IMMEDIATE")
                        connection.execute(
                            "INSERT INTO jobs(id,created_at,data) VALUES(?,?,?)",
                            (identifier, record["created_at"], json.dumps(record, allow_nan=False)),
                        )
                        # mkdir reserves the UUID without replacing an existing directory.
                        destination.mkdir()
                        moved = True
                        for source in staging.iterdir():
                            source.rename(destination / source.name)
                return record
            except BaseException:
                if moved:
                    shutil.rmtree(destination)
                raise
