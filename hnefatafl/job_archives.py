"""Versioned, bounded training-job migration. No foreign runtime state is restored."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import stat
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable
from uuid import uuid4

from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .storage import Store

FORMAT = "hnefatafl-training-job"
FORMAT_VERSION = 1
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_EXTRACTED_BYTES = 1024 * 1024 * 1024
MAX_FILE_BYTES = 128 * 1024 * 1024
MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_FILES = 2048
MAX_RATIO = 200
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
}
CHECKPOINTS = {"last.pt", "best.pt", "inference.pt", "resume.pt"}
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
}
SECRET_KEY = re.compile(r"token|secret|password|credential|authorization|lease|api[_-]?key", re.I)
ABSOLUTE_PATH = re.compile(r"(?<![\w:])(?:[A-Za-z]:[\\/]|/)[^\s\"'<>]*")
SECRET_TEXT = re.compile(
    r"(?i)(?:bearer\s+\S+|(?:[\w-]*(?:token|secret|password|credential|api_key)[\w-]*)[\"']?\s*[:=]\s*[\"']?[^\s,\"'}]+)"
)
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
}


class ArchiveError(ValueError):
    """A useful, client-safe archive validation error."""


class JobUploadLimit:
    """Bound multipart input before parsing/spooling, including chunked uploads."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope.get("method") != "POST"
            or scope.get("path") != "/api/training/jobs/import"
        ):
            await self.app(scope, receive, send)
            return
        maximum = MAX_ARCHIVE_BYTES + 1024 * 1024  # Multipart envelope allowance.
        headers = dict(scope.get("headers", []))
        try:
            length = int(headers.get(b"content-length", b"0"))
        except ValueError:
            length = maximum + 1
        if not 0 <= length <= maximum:
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

        await self.app(scope, bounded_receive, send)


def sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def clean_text(value: str) -> str:
    return ABSOLUTE_PATH.sub("[local path]", SECRET_TEXT.sub("[redacted]", value))


def portable(value: Any, directory: Path | None = None, depth: int = 0) -> Any:
    """Keep tensor training state, while stripping paths and execution ownership."""
    if depth > 30:
        raise ArchiveError("Job metadata nesting is too deep.")
    if isinstance(value, dict):
        return {
            key: portable(item, directory, depth + 1)
            for key, item in value.items()
            if not isinstance(key, str) or (key not in RUNTIME_KEYS and not SECRET_KEY.search(key))
        }
    if isinstance(value, (list, tuple)):
        items = [portable(item, directory, depth + 1) for item in value]
        return tuple(items) if isinstance(value, tuple) else items
    if isinstance(value, str):
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
        or any(ord(c) < 32 for c in name)
    ):
        raise ArchiveError("Unsafe archive member path.")


def artifact_name(name: str, *, tuning: bool = False) -> bool:
    path = PurePosixPath(name)
    if tuning:
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


def json_bytes(value: Any) -> bytes:
    data = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode()
    if len(data) > MAX_JSON_BYTES:
        raise ArchiveError("Job JSON exceeds the metadata size limit.")
    return data


def reject_constant(value: str):
    raise ArchiveError(f"Non-finite JSON value: {value}.")


def read_json(path: Path) -> dict:
    if path.stat().st_size > MAX_JSON_BYTES:
        raise ArchiveError("Job JSON exceeds the metadata size limit.")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ArchiveError("Duplicate JSON metadata field.")
            result[key] = value
        return result

    try:
        value = json.loads(
            path.read_bytes(), object_pairs_hook=pairs, parse_constant=reject_constant
        )
        if not isinstance(value, dict):
            raise ArchiveError("Job JSON must be an object.")
        validate_json(value)
        return value
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise ArchiveError(f"Invalid job JSON: {exc}") from exc


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
    for key in ("progress", "result", "metrics", "provenance"):
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
    source: Path, target: Path, directory: Path | None = None, ids: dict | None = None
) -> dict:
    import torch
    from .models import load_checkpoint

    model, payload = load_checkpoint(source)
    payload = portable(payload, directory)
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
    """Build on disk using bounded reads. Caller holds lifecycle/catalog locks."""
    validate_job(job)
    if directory.is_symlink() or directory.resolve() != directory or directory.parent.is_symlink():
        raise ArchiveError("The saved training directory is not a safe local job directory.")
    with tempfile.TemporaryDirectory(prefix="job-export-") as temporary:
        staging = Path(temporary)
        record = portable(
            {key: value for key, value in job.items() if key in JOB_FIELDS}, directory
        )
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

        if directory.exists():
            for source in sorted(directory.rglob("*")):
                name = source.relative_to(directory).as_posix()
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
                    payload = write_checkpoint(source, target, directory)
                    references.update(model_ids(payload))
                elif source.suffix == ".json":
                    data = portable(read_json(source), directory)
                    validate_model_references(data)
                    references.update(model_ids(data))
                    target.write_bytes(json_bytes(data))
                else:
                    with source.open(errors="replace") as src, target.open("w") as dst:
                        while chunk := src.readline(65536):
                            dst.write(clean_text(chunk))
                include(target, name)
        if job["kind"] == "tune":
            from .job_studies import copy_study

            copy_study(directory, job["config"], staging / "study.sqlite3")
            include(staging / "study.sqlite3", "study.sqlite3")
        # DB configuration and remote reports may be the only copies available.
        for name, value in (
            ("config.json", record["config"]),
            ("progress.json", record.get("progress")),
            ("result.json", record.get("result")),
        ):
            if name not in files and value is not None:
                (staging / name).write_bytes(json_bytes(value))
                include(staging / name, name)
        models = []
        available = {entry["id"]: entry for entry in registry.list()}
        for identifier in sorted(references):
            entry = available.get(identifier)
            if not entry:
                continue  # Deleted inference copies remain historical references only.
            name = f"models/{identifier}.pt"
            source = Path(entry["path"])
            if source.is_symlink() or not source.is_file():
                raise ArchiveError("An associated registered model is missing or unsafe.")
            write_checkpoint(source, staging / name)
            include(staging / name, name)
            models.append({"original_id": identifier, "file": name, "name": entry["name"]})
        (staging / "job.json").write_bytes(json_bytes(record))
        include(staging / "job.json", "job.json")
        manifest = {
            "format": FORMAT,
            "format_version": FORMAT_VERSION,
            "app_version": app_version,
            "original_job_id": job["id"],
            "job_type": job["kind"],
            "files": files,
            "models": models,
        }
        # Stored entries bound extraction ratios even for highly repetitive tensor buffers.
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
            archive.writestr("manifest.json", json_bytes(manifest))
            for name in files:
                archive.write(staging / name, name)
        if output.stat().st_size > MAX_ARCHIVE_BYTES:
            raise ArchiveError("Job archive exceeds the 512 MiB archive limit.")


def extract(archive_path: Path, staging: Path) -> dict:
    if archive_path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ArchiveError("Job archive exceeds the 512 MiB archive limit.")
    with zipfile.ZipFile(archive_path) as archive:
        infos = archive.infolist()
        if not infos or len(infos) > MAX_FILES:
            raise ArchiveError("Job archive exceeds the file count limit.")
        names = set()
        total = 0
        for item in infos:
            safe_name(item.filename)
            mode = item.external_attr >> 16
            if (
                item.is_dir()
                or stat.S_IFMT(mode) not in {0, stat.S_IFREG}
                or item.flag_bits & 1
                or item.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}
            ):
                raise ArchiveError("Unsupported archive entry type or encryption.")
            folded = item.filename.casefold()
            if folded in names:
                raise ArchiveError("Duplicate or conflicting archive member.")
            names.add(folded)
            total += item.file_size
            if item.file_size > MAX_FILE_BYTES or total > MAX_EXTRACTED_BYTES:
                raise ArchiveError("Job archive exceeds the extracted size limit.")
            if item.file_size > max(1, item.compress_size) * MAX_RATIO:
                raise ArchiveError("Job archive exceeds the decompression ratio limit.")
        for name in names:
            if any(
                str(parent) in names for parent in PurePosixPath(name).parents if str(parent) != "."
            ):
                raise ArchiveError("Conflicting archive file and directory members.")
        if "manifest.json" not in {item.filename for item in infos}:
            raise ArchiveError("Job archive is missing manifest.json.")
        # Extract manually, with actual byte bounds; ZipFile.extract is never used.
        for item in infos:
            target = staging / item.filename
            target.parent.mkdir(parents=True, exist_ok=True)
            count = 0
            with archive.open(item) as src, target.open("xb") as dst:
                while chunk := src.read(1024 * 1024):
                    count += len(chunk)
                    if count > min(item.file_size, MAX_FILE_BYTES):
                        raise ArchiveError("Archive member exceeds its declared size.")
                    dst.write(chunk)
        manifest = read_json(staging / "manifest.json")
        if (
            manifest.get("format") != FORMAT
            or type(manifest.get("format_version")) is not int
            or manifest["format_version"] != FORMAT_VERSION
        ):
            raise ArchiveError("Unsupported training-job archive format version.")
        if (
            not isinstance(manifest.get("app_version"), str)
            or not 1 <= len(manifest["app_version"]) <= 64
        ):
            raise ArchiveError("Invalid archive application version.")
        files = manifest.get("files")
        if (
            not isinstance(files, dict)
            or set(files) != {item.filename for item in infos} - {"manifest.json"}
            or not {"job.json", "config.json"} <= files.keys()
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


def rewrite(value: Any, ids: dict, directory: Path | None = None) -> Any:
    if isinstance(value, dict):
        result = {
            key: item if key == "provenance" else rewrite(item, ids, directory)
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
    archive: Path, root: Path, store: Store, *, resume_info: Callable[[dict, Path], dict]
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
        config_file = job_config(read_json(staging / "config.json"), record["kind"])
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
                data = portable(read_json(path))
                validate_model_references(data)
                path.write_bytes(json_bytes(data))
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
                # Compare portable copies too: stripping local paths must not duplicate
                # a model whose original bytes are already registered here.
                portable_existing = {}
                probe = staging / ".model-probe.pt"
                for entry in registry.list():
                    if Path(entry["path"]).is_file():
                        try:
                            write_checkpoint(Path(entry["path"]), probe)
                        except (ValueError, RuntimeError, OSError):
                            continue  # An unrelated damaged copy must not block migration.
                        portable_existing[sha256(probe)] = entry
                probe.unlink(missing_ok=True)
                for old, entry in models.items():
                    path = staging / entry["file"]
                    registered = portable_existing.get(sha256(path))
                    if registered is None:
                        registered = registry.import_model(path, entry["name"])
                        portable_existing[sha256(path)] = registered
                    ids[old] = registered["id"]
                # Always register an inference artifact, including jobs whose source
                # catalog copy had been removed before export.
                for inference in sorted(staging.rglob("inference.pt")):
                    registered = portable_existing.get(sha256(inference))
                    if registered is None:
                        registered = registry.import_model(
                            inference,
                            record["config"].get(
                                "model_name", record["config"].get("training", {}).get("model_name")
                            ),
                        )
                    ids.setdefault(registered["id"], registered["id"])
                    portable_existing[sha256(inference)] = registered
                    for report_name in ("progress.json", "result.json"):
                        report_path = inference.parent / report_name
                        if report_path.is_file():
                            previous_model = read_json(report_path).get("model")
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
                for name in manifest["files"]:
                    if name == "job.json" or name.startswith("models/"):
                        continue
                    path = staging / name
                    if path.suffix == ".pt":
                        # Rebase only job checkpoints; registered inference copies stay
                        # byte-identical for content-addressed deduplication.
                        write_checkpoint(path, path, destination, ids)
                    elif path.suffix == ".json":
                        data = rewrite(read_json(path), ids, destination)
                        if name in {"progress.json", "result.json"}:
                            data["status"] = record["status"]
                            if record.get("model_id"):
                                data["model"] = registry.get(record["model_id"])
                            record[name[:-5]] = data
                        path.write_bytes(json_bytes(data))
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
                            (staging / f"{section}.json").write_bytes(json_bytes(record[section]))
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
