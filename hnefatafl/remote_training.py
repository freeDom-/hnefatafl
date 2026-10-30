"""Persistent pull-based remote training with scoped, expiring worker leases.

The trusted local administrator creates worker credentials. Cloud machines only
make outbound authenticated requests; they cannot supply server filesystem paths.
"""

from __future__ import annotations

from datetime import datetime, timezone
import asyncio
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import tempfile
from typing import Any, NoReturn
from uuid import uuid4

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

from .storage import Store
from .versioning import known_revision, known_version

PROTOCOL_VERSION = 1
ACTIVE = {"queued", "pending", "running", "cancelling"}
TRAINING_FIELDS = {
    "ruleset",
    "preset",
    "channels",
    "blocks",
    "games",
    "iterations",
    "epochs",
    "batch_size",
    "learning_rate",
    "optimizer",
    "mcts_iterations",
    "mcts_time_limit",
    "self_play_mode",
    "self_play_concurrency",
    "inference_batch_size",
    "inference_cache_mode",
    "inference_cache_capacity",
    "inference_backend",
    "inference_device",
    "exploration",
    "temperature",
    "dirichlet_alpha",
    "dirichlet_fraction",
    "replay_buffer_size",
    "weight_decay",
    "momentum",
    "training_symmetry_mode",
    "checkpoint_frequency",
    "evaluation_frequency",
    "evaluation_opponent",
    "evaluation_mode",
    "evaluation_concurrency",
    "evaluation_batch_size",
    "evaluation_inference_backend",
    "evaluation_time_limit",
    "evaluation_jarl_depth",
    "evaluation_jarl_time_limit",
    "champion_confidence",
    "champion_min_pairs",
    "champion_min_improvement",
    "evaluation_games",
    "evaluation_iterations",
    "max_game_plies",
    "evaluation_max_plies",
    "seed",
    "device",
    "mixed_precision",
    "save_all_best",
    "cpu_threads",
    "resume",
    "model_name",
}


def validate_inference_capabilities(capabilities: dict) -> None:
    """Validate optional extension fields without rejecting legacy reports."""
    cache_modes = capabilities.get("inference_cache_modes", ["off"])
    if (
        not isinstance(cache_modes, list)
        or len(cache_modes) > 2
        or any(mode not in ("off", "exact") for mode in cache_modes)
    ):
        fail("Invalid worker inference_cache_modes.")
    symmetry_modes = capabilities.get("training_symmetry_modes", ["off"])
    if (
        not isinstance(symmetry_modes, list)
        or len(symmetry_modes) > 4
        or any(mode not in ("off", "random_d4") for mode in symmetry_modes)
    ):
        fail("Invalid worker training_symmetry_modes.")
    backends = capabilities.get("inference_backends", ["pytorch"])
    if (
        not isinstance(backends, list)
        or len(backends) > 4
        or any(item not in ("pytorch", "openvino") for item in backends)
    ):
        fail("Invalid worker inference_backends.")
    devices = capabilities.get("openvino_devices", [])
    if not isinstance(devices, list) or len(devices) > 32:
        fail("Invalid worker OpenVINO devices.")
    for entry in devices:
        if (
            not isinstance(entry, dict)
            or not isinstance(entry.get("id"), str)
            or not re.fullmatch(r"(?:GPU(?:\.\d+)?|CPU)", entry["id"])
            or not isinstance(entry.get("name"), str)
            or len(entry["name"]) > 200
            or type(entry.get("available")) is not bool
            or (
                "error" in entry
                and (not isinstance(entry["error"], str) or len(entry["error"]) > 500)
            )
        ):
            fail("Invalid worker OpenVINO device entry.")
    for key, limit in (("openvino_version", 80), ("openvino_error", 1200)):
        if key in capabilities and (
            not isinstance(capabilities[key], str) or len(capabilities[key]) > limit
        ):
            fail("Invalid worker OpenVINO runtime metadata.")


def worker_compatible(config: dict, capabilities: dict) -> bool:
    if config.get("training_symmetry_mode", "off") not in capabilities.get(
        "training_symmetry_modes", ["off"]
    ):
        return False
    if config.get("inference_cache_mode", "off") not in capabilities.get(
        "inference_cache_modes", ["off"]
    ):
        return False
    device = config.get("device", "auto")
    if device not in ("auto", "cpu") and capabilities.get(f"{device}_available") is not True:
        return False
    if config.get("inference_backend", "pytorch") == "openvino":
        if device == "auto" and (
            capabilities.get("default_device", "cpu") != "cpu"
            or capabilities.get("cuda_available") is True
            or capabilities.get("mps_available") is True
        ):
            return False
        from .inference_backends import supports_openvino

        return supports_openvino(capabilities, config.get("inference_device", "auto"))
    return True


def timestamp() -> float:
    return datetime.now(timezone.utc).timestamp()


def iso(value: float | None = None) -> str:
    return datetime.fromtimestamp(timestamp() if value is None else value, timezone.utc).isoformat()


def digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def fail(message: str, status: int = 400) -> NoReturn:
    raise HTTPException(status, message)


def clean(value: Any, depth: int = 0) -> Any:
    """Keep bounded, path-free JSON progress; never accept a worker's file references."""
    if depth > 12:
        return None
    if isinstance(value, dict):
        return {
            str(key): clean(item, depth + 1)
            for key, item in value.items()
            if str(key)
            not in {
                "path",
                "checkpoint",
                "best_checkpoint",
                "inference_checkpoint",
                "output_dir",
                "registry_dir",
                "resume",
                "lease_token",
                "token",
                "model",
                "model_id",
                "best_models",
            }
        }
    if isinstance(value, list):
        return [clean(item, depth + 1) for item in value[-2000:]]
    if isinstance(value, str):
        return value[:2000]
    return value


class WorkerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    capacity: StrictInt = Field(default=1, ge=1, le=16)


class HeartbeatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    capabilities: dict[str, Any] = Field(default_factory=dict)


class VersionOverrideRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool


class ClaimRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(
        default_factory=lambda: uuid4().hex,
        min_length=1,
        max_length=100,
        pattern=r"^[A-Za-z0-9_-]+$",
    )


class ProgressRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lease_token: str = Field(min_length=1, max_length=200)
    history_offset: StrictInt | None = Field(default=None, ge=0)
    sequence: StrictInt = Field(ge=0)
    progress: dict[str, Any] = Field(default_factory=dict)
    logs: str = Field(default="", max_length=16000)


class FinishRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lease_token: str = Field(min_length=1, max_length=200)
    status: str
    error: str | None = Field(default=None, max_length=2000)


class RemoteTraining:
    def __init__(self, store: Store, root: Path, lock, catalog_lock, *, server_build: dict):
        self.store = store
        self.root = root
        self.lock = lock
        self.catalog_lock = catalog_lock
        self.server_build = dict(server_build)
        self.lease_seconds = 120
        self.heartbeat_interval = 10

    def compatibility(self, worker: dict) -> dict:
        capabilities = worker.get("capabilities", {})
        protocol = capabilities.get("protocol_version", PROTOCOL_VERSION)
        protocol_ok = type(protocol) is int and protocol == PROTOCOL_VERSION
        worker_version = known_version(capabilities.get("package_version"))
        server_version = known_version(self.server_build.get("package_version"))
        worker_revision = known_revision(capabilities.get("git_revision"))
        server_revision = known_revision(self.server_build.get("git_revision"))
        mismatches = []
        if worker_version is None or server_version is None:
            mismatches.append("unknown_application_version")
        elif worker_version != server_version:
            mismatches.append("application_version_mismatch")
        if worker_revision and server_revision and worker_revision != server_revision:
            mismatches.append("git_revision_mismatch")
        override_enabled = worker.get("version_override_enabled", False) is True
        override_active = (
            override_enabled
            and bool(mismatches)
            and "unknown_application_version" not in mismatches
            and protocol_ok
        )
        code = (
            "incompatible_protocol"
            if not protocol_ok
            else "override_active"
            if override_active
            else mismatches[0]
            if mismatches
            else "compatible"
        )
        messages = {
            "compatible": "Compatible",
            "application_version_mismatch": "Application version mismatch",
            "unknown_application_version": "Missing or unknown application version",
            "git_revision_mismatch": "Git commit mismatch",
            "incompatible_protocol": "Incompatible worker protocol",
            "override_active": "Administrator override active",
        }
        return {
            "eligible": protocol_ok and (not mismatches or override_active),
            "code": code,
            "reason": messages[code],
            "mismatches": mismatches,
            "protocol_compatible": protocol_ok,
            "override_enabled": override_enabled,
            "override_active": override_active,
            "worker_version": worker_version,
            "server_version": self.server_build.get("package_version"),
            "worker_git_revision": worker_revision,
            "server_git_revision": server_revision,
        }

    def queue_status(self, job: dict) -> dict:
        workers = [
            worker
            for worker in self.store.workers()
            if not worker.get("revoked_at")
            and job.get("target_worker_id") in (None, worker["id"])
            and self.public_worker(worker)["status"] == "online"
        ]
        hardware = [
            worker
            for worker in workers
            if worker_compatible(job["config"], worker.get("capabilities", {}))
        ]
        eligible = [worker for worker in hardware if self.compatibility(worker)["eligible"]]
        if not workers or eligible:
            code, reason = "capacity", "Waiting for worker capacity."
        elif not hardware:
            code, reason = "hardware", "Waiting for compatible hardware or inference capabilities."
        elif any(self.compatibility(worker)["protocol_compatible"] for worker in hardware):
            code, reason = "application_version", "Waiting for a compatible application version."
        else:
            code, reason = "protocol", "Waiting for a compatible worker protocol."
        return {"queue_reason_code": code, "queue_reason": reason}

    def directory(self, job_id: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{32}", job_id):
            fail("Training job not found.", 404)
        directory = self.root / "jobs" / job_id
        if (self.root / "jobs").is_symlink() or directory.is_symlink():
            fail("The saved training directory is not a safe local job directory.", 409)
        return directory

    def public_job(self, job: dict) -> dict:
        visible = {
            key: value
            for key, value in job.items()
            if key
            not in {
                "lease_hash",
                "lease_sequence",
                "resume_source",
                "uploaded_models",
                "worker_pid",
                "claim_request_id",
                "finish_received",
                "finish_requested_status",
                "checkpoint_iteration",
            }
        }
        if isinstance(visible.get("progress"), dict):
            # The last worker snapshot precedes the authoritative finish/expiry.
            visible["progress"] = {**visible["progress"], "status": job["status"]}
        if job["status"] == "queued":
            visible["progress"] = {
                **visible.get("progress", {}),
                "status": job["status"],
                **self.queue_status(job),
            }
        return visible

    def reconcile(self) -> None:
        for job in self.store.jobs():
            if job.get("execution") != "remote" or job["status"] not in {"running", "cancelling"}:
                continue
            if job.get("lease_expires", 0) <= timestamp():
                job.update(
                    status="cancelled" if job["status"] == "cancelling" else "failed",
                    ended_at=iso(),
                    error="The remote worker stopped reporting before its lease expired. Resume a retained checkpoint to continue.",
                )
                self.store.save_job(job)

    def public_worker(self, worker: dict) -> dict:
        active = sum(
            job.get("worker_id") == worker["id"] and job["status"] in {"running", "cancelling"}
            for job in self.store.jobs()
        )
        last = worker.get("last_seen_epoch")
        return {
            key: value
            for key, value in {
                **worker,
                "status": "revoked"
                if worker.get("revoked_at")
                else "online"
                if last is not None and timestamp() - last < self.lease_seconds
                else "offline",
                "active_jobs": active,
                "version_override_enabled": worker.get("version_override_enabled", False),
                "compatibility": self.compatibility(worker),
            }.items()
            if key != "last_seen_epoch"
        }

    def authenticate(self, authorization: str | None) -> dict:
        if not authorization or not authorization.startswith("Bearer "):
            fail("A valid worker token is required.", 401)
        worker = self.store.worker_for_token(digest(authorization[7:]))
        if worker is None or worker.get("revoked_at"):
            fail("The worker token is invalid or revoked.", 403)
        from .security_context import allowed

        with self.store.connection() as connection:
            owner = connection.execute(
                "SELECT * FROM accounts WHERE id=?", (worker.get("owner_account_id"),)
            ).fetchone()
        if owner is None or not allowed(dict(owner), "training"):
            fail("The enrolling administrator is no longer authorized. Re-enroll this worker.", 403)
        return worker

    def lease(self, worker: dict, job_id: str, token: str, *, terminal: bool = False) -> dict:
        self.reconcile()
        job = self.store.get_job(job_id)
        if job is None:
            fail("Training job not found.", 404)
        if job.get("execution") != "remote" or job.get("worker_id") != worker["id"]:
            fail("This worker does not own the training job.", 403)
        if not secrets.compare_digest(job.get("lease_hash", ""), digest(token)):
            fail("The training lease is invalid.", 403)
        if job["status"] not in {"running", "cancelling"} and not terminal:
            fail("The remote training lease is no longer active.", 409)
        return job

    def touch(self, worker: dict) -> None:
        worker.update(last_seen=iso(), last_seen_epoch=timestamp())
        self.store.save_worker(worker)

    def renew(self, job: dict) -> None:
        job.update(last_reported_at=iso(), lease_expires=timestamp() + self.lease_seconds)
        job["lease_expires_at"] = iso(job["lease_expires"])

    def enqueue(self, identifier: str, config: dict, worker_id: str | None) -> dict:
        with self.lock:
            if worker_id is not None:
                worker = self.store.get_worker(worker_id)
                if worker is None or worker.get("revoked_at"):
                    fail("Choose an available remote worker.")
                compatibility = self.compatibility(worker)
                if not compatibility["eligible"]:
                    fail(
                        f"Selected worker is incompatible: {compatibility['reason']} "
                        f"(worker {compatibility['worker_version'] or 'unknown'}, "
                        f"server {compatibility['server_version']}).",
                        409,
                    )
                if not worker_compatible(config, worker.get("capabilities", {})):
                    fail(
                        "Selected worker is incompatible with the training device or neural inference backend. A compatible worker is required."
                    )
            directory = self.directory(identifier)
            directory.mkdir()
            resume = config.get("resume")
            if resume:
                import shutil

                shutil.copyfile(resume, directory / "resume.pt")
            saved = {
                key: value for key, value in config.items() if key in TRAINING_FIELDS - {"resume"}
            }
            job: dict = {
                "id": identifier,
                "kind": "train",
                "execution": "remote",
                "status": "queued",
                "target_worker_id": worker_id,
                "config": saved,
                "progress": {},
                "created_at": iso(),
                "ended_at": None,
                "error": None,
            }
            if resume:
                job["resume_source"] = True
            (directory / "config.json").write_text(json.dumps(saved, indent=2))
            self.store.save_job(job)
            return self.public_job(job)

    def cancel(self, job: dict) -> dict:
        with self.lock:
            self.reconcile()
            job = self.store.get_job(job["id"]) or job
            if job["status"] == "queued":
                job.update(status="cancelled", ended_at=iso())
            elif job["status"] == "running":
                job["status"] = "cancelling"
            self.store.save_job(job)
            return self.public_job(job)

    def accept_artifact(
        self,
        job_id: str,
        kind: str,
        path: Path,
        lease_token: str,
        authorization: str | None,
        network: Any,
        payload: dict,
    ) -> dict:
        from .models import ModelRegistry

        result: dict
        with self.lock:
            worker = self.authenticate(authorization)
            job = self.lease(worker, job_id, lease_token)
            config = job["config"]
            if (
                network.ruleset != config["ruleset"]
                or network.channels != config["channels"]
                or network.blocks != config["blocks"]
            ):
                fail("The uploaded artifact does not match this training job.")
            metadata = payload.get("metadata", {})
            if kind == "checkpoint":
                extra = payload.get("extra", {})
                if payload.get("optimizer_state") is None or not {
                    "replay",
                    "python_rng",
                    "torch_rng",
                    "iteration",
                }.issubset(extra):
                    fail("A full resumable training checkpoint is required.")
                iteration = extra["iteration"]
                if type(iteration) is not int or not 0 <= iteration <= config["iterations"]:
                    fail("The checkpoint iteration is outside this training job.")
                if iteration < job.get("checkpoint_iteration", -1):
                    fail("A newer training checkpoint has already been saved.", 409)
                recorded = metadata.get("training_config", {})
                if (
                    not isinstance(recorded, dict)
                    or recorded.get("optimizer") != config["optimizer"]
                ):
                    fail("The checkpoint optimizer does not match this training job.")
                os.replace(path, self.directory(job_id) / "last.pt")
                job["checkpoint"] = f"{job_id}:last.pt"
                job["checkpoint_iteration"] = iteration
                result = {"checkpoint": True}
            else:
                if payload.get("optimizer_state") is not None:
                    fail("Upload an inference-only model for this artifact kind.")
                recorded = metadata.get("training_config", {})
                if not isinstance(recorded, dict) or recorded.get("ruleset") != config["ruleset"]:
                    fail("The model has no compatible recorded training configuration.")
                if kind.startswith("best-") and not config.get("save_all_best"):
                    fail("This training job does not retain every best model.")
                with self.catalog_lock:
                    model = ModelRegistry(self.root / "models").register(path, config["model_name"])
                    model["rune"] = self.store.rune(f"model:{model['id']}")
                job.setdefault("uploaded_models", {})[kind] = model
                result = {"model": model}
            self.renew(job)
            self.touch(worker)
            self.store.save_job(job)
            return result

    def attach_routes(self, app: FastAPI) -> None:
        @app.get("/api/training/workers")
        def workers():
            with self.lock:
                self.reconcile()
                return [
                    self.public_worker(worker)
                    for worker in self.store.workers()
                    if not worker.get("revoked_at")
                ]

        @app.post("/api/training/workers", status_code=201)
        def create_worker(request: WorkerRequest):
            from .security_context import principal

            account = principal.get()
            assert account is not None
            if not request.name.strip():
                fail("Worker names must contain 1–80 characters.")
            with self.lock:
                token = secrets.token_urlsafe(40)
                worker = {
                    "owner_account_id": account["id"],
                    "id": uuid4().hex,
                    "name": request.name.strip(),
                    "capacity": request.capacity,
                    "created_at": iso(),
                    "last_seen": None,
                    "capabilities": {},
                    "revoked_at": None,
                    "version_override_enabled": False,
                }
                self.store.create_worker(worker, digest(token))
                return {"worker": self.public_worker(worker), "token": token}

        @app.patch("/api/training/workers/{worker_id}/version-override")
        def version_override(worker_id: str, request: VersionOverrideRequest):
            from .security_context import allowed, principal

            account = principal.get()
            if not allowed(account, "accounts"):
                fail("Only authenticated administrators may change version overrides.", 403)
            assert account is not None
            with self.lock:
                worker = self.store.get_worker(worker_id)
                if worker is None or worker.get("revoked_at"):
                    fail("Remote worker not found.", 404)
                if worker.get("version_override_enabled", False) != request.enabled:
                    event = {"enabled": request.enabled, "at": iso(), "account_id": account["id"]}
                    worker["version_override_enabled"] = request.enabled
                    worker["version_override_changed_at"] = event["at"]
                    worker["version_override_changed_by"] = account["id"]
                    worker.setdefault("version_override_history", []).append(event)
                    self.store.save_worker(worker)
                return self.public_worker(worker)

        @app.delete("/api/training/workers/{worker_id}")
        def revoke_worker(worker_id: str):
            with self.lock:
                worker = self.store.get_worker(worker_id)
                if worker is None:
                    fail("Remote worker not found.", 404)
                worker["revoked_at"] = iso()
                self.store.save_worker(worker)
                for job in self.store.jobs():
                    if job.get("execution") != "remote" or job["status"] not in ACTIVE:
                        continue
                    if (
                        job.get("worker_id") == worker_id
                        or job.get("target_worker_id") == worker_id
                    ):
                        job.update(
                            status="failed" if job["status"] != "queued" else "cancelled",
                            ended_at=iso(),
                            error="The remote worker was revoked.",
                        )
                        self.store.save_job(job)
                return {"revoked": True, "worker_id": worker_id}

        @app.post("/api/worker/heartbeat")
        def heartbeat(request: HeartbeatRequest, authorization: str | None = Header(default=None)):
            with self.lock:
                worker = self.authenticate(authorization)
                try:
                    size = len(json.dumps(request.capabilities, allow_nan=False))
                except (ValueError, TypeError):
                    fail("Worker capabilities must contain finite JSON values.")
                if size > 16000:
                    fail("Worker capabilities are too large.", 413)
                validate_inference_capabilities(request.capabilities)
                # A heartbeat is a full snapshot. Missing version fields must not
                # inherit an earlier report's proof of compatibility.
                worker["capabilities"] = clean(request.capabilities)
                self.touch(worker)
                self.reconcile()
                if not self.compatibility(worker)["protocol_compatible"]:
                    # Retain connectivity and diagnostics even for old protocols.
                    fail("The worker protocol version is incompatible.", 409)
                return {
                    "worker": self.public_worker(worker),
                    "heartbeat_interval": self.heartbeat_interval,
                }

        @app.post("/api/worker/claim")
        def claim(request: ClaimRequest, authorization: str | None = Header(default=None)):
            with self.lock:
                worker = self.authenticate(authorization)
                self.reconcile()
                self.touch(worker)
                compatibility = self.compatibility(worker)
                if not compatibility["protocol_compatible"]:
                    fail("The worker protocol version is incompatible.", 409)
                available = next(
                    (
                        job
                        for job in self.store.jobs()
                        if job.get("worker_id") == worker["id"]
                        and job.get("claim_request_id") == request.request_id
                    ),
                    None,
                )
                if available is not None and available["status"] not in {"running", "cancelling"}:
                    fail("This claim has already ended. Use a new claim request.", 409)
                active = self.public_worker(worker)["active_jobs"]
                if available is None and compatibility["eligible"] and active < worker["capacity"]:
                    available = next(
                        (
                            job
                            for job in reversed(self.store.jobs())
                            if job.get("execution") == "remote"
                            and job["status"] == "queued"
                            and job.get("target_worker_id") in (None, worker["id"])
                            and worker_compatible(job["config"], worker.get("capabilities", {}))
                        ),
                        None,
                    )
                reply: dict = {
                    "job": None,
                    "heartbeat_interval": self.heartbeat_interval,
                    "lease_seconds": self.lease_seconds,
                    "compatibility": compatibility,
                }
                if available is not None:
                    assert authorization is not None
                    token = hmac.new(
                        authorization[7:].encode(),
                        f"{available['id']}:{request.request_id}".encode(),
                        hashlib.sha256,
                    ).hexdigest()
                    if available["status"] == "queued":
                        available.update(
                            status="running",
                            worker_id=worker["id"],
                            worker_name=worker["name"],
                            lease_hash=digest(token),
                            lease_sequence=-1,
                            started_at=iso(),
                            claim_request_id=request.request_id,
                            software={
                                "worker": {
                                    "package_version": compatibility["worker_version"],
                                    "git_revision": compatibility["worker_git_revision"],
                                },
                                "server": dict(self.server_build),
                                "version_override_active": compatibility["override_active"],
                            },
                        )
                    self.renew(available)
                    self.store.save_job(available)
                    configuration = dict(available["config"])
                    if (
                        "inference_backends" not in worker.get("capabilities", {})
                        and configuration.get("inference_backend", "pytorch") == "pytorch"
                    ):
                        # Protocol-1 workers predating this optional extension
                        # reject unknown configuration keys, even default values.
                        configuration.pop("inference_backend", None)
                        configuration.pop("inference_device", None)
                    if "training_symmetry_modes" not in worker.get("capabilities", {}):
                        # Workers predating augmentation reject unknown keys; OFF is
                        # their behavior and worker_compatible() excludes random_d4.
                        configuration.pop("training_symmetry_mode", None)
                    if "inference_cache_modes" not in worker.get("capabilities", {}):
                        configuration.pop("inference_cache_mode", None)
                        configuration.pop("inference_cache_capacity", None)
                    reply["job"] = {
                        "id": available["id"],
                        "kind": "train",
                        "config": configuration,
                        "lease_token": token,
                        "resume_available": bool(available.get("resume_source")),
                    }
                return reply

        @app.post("/api/worker/jobs/{job_id}/progress")
        def progress(
            job_id: str, request: ProgressRequest, authorization: str | None = Header(default=None)
        ):
            try:
                size = len(json.dumps(request.progress, allow_nan=False))
            except (TypeError, ValueError):
                fail("Training progress must contain finite JSON values.")
            if size > 512000:
                fail("Training progress is too large.", 413)
            with self.lock:
                worker = self.authenticate(authorization)
                job = self.lease(worker, job_id, request.lease_token)
                previous = job.get("lease_sequence", -1)
                if request.sequence < previous:
                    fail("A newer training report has already been saved.", 409)
                if request.sequence > previous:
                    report = clean(request.progress)
                    if request.history_offset is not None:
                        retained = job.get("progress", {}).get("history", [])
                        chunk = report.get("history", [])
                        offset = request.history_offset
                        if not isinstance(chunk, list) or offset > len(retained):
                            fail("Training history offset does not match retained progress.", 409)
                        # Idempotent sequence handling above also covers ambiguous retries.
                        # Keep any already retained suffix during supervisor recovery.
                        report["history"] = (
                            retained[:offset] + chunk + retained[offset + len(chunk) :]
                        )
                    report["status"] = job["status"]
                    models = job.get("uploaded_models", {})
                    report["best_models"] = [
                        {"iteration": int(kind[5:]), "model_id": value["id"]}
                        for kind, value in models.items()
                        if kind.startswith("best-")
                    ]
                    if (self.directory(job_id) / "last.pt").is_file():
                        report["checkpoint"] = f"{job_id}:last.pt"
                    job.update(progress=report, lease_sequence=request.sequence)
                    if request.logs:
                        # Worker reports the latest bounded log tail, not a delta.
                        job["logs"] = request.logs[-16000:]
                self.renew(job)
                self.touch(worker)
                self.store.save_job(job)
                return {
                    "cancel_requested": job["status"] == "cancelling",
                    "lease_seconds": self.lease_seconds,
                }

        @app.get("/api/worker/jobs/{job_id}/resume")
        def resume(
            job_id: str,
            authorization: str | None = Header(default=None),
            x_lease_token: str = Header(default=""),
        ):
            with self.lock:
                worker = self.authenticate(authorization)
                job = self.lease(worker, job_id, x_lease_token)
                checkpoint = self.directory(job_id) / "resume.pt"
                if not job.get("resume_source") or not checkpoint.is_file():
                    fail("The saved checkpoint was not found.", 404)
                omitted = set()
                if "training_symmetry_modes" not in worker.get("capabilities", {}):
                    omitted.add("training_symmetry_mode")
                if "inference_cache_modes" not in worker.get("capabilities", {}):
                    omitted.update({"inference_cache_mode", "inference_cache_capacity"})
                if "inference_backends" not in worker.get("capabilities", {}):
                    omitted.update({"inference_backend", "inference_device"})
                if omitted:
                    from .models import load_checkpoint
                    import torch

                    # Keep the owned/source checkpoint byte-for-byte intact.
                    # Older training binaries merge saved config before validation,
                    # so their transport copy must omit the new preferences too.
                    _, payload = load_checkpoint(checkpoint)
                    saved = payload["metadata"].get("training_config", {})
                    if omitted.intersection(saved):
                        suffix = "-".join(sorted(omitted))
                        legacy = checkpoint.with_name(f"legacy-{suffix}.pt")
                        if not legacy.exists():
                            payload["metadata"]["training_config"] = {
                                key: value for key, value in saved.items() if key not in omitted
                            }
                            temporary = legacy.with_suffix(".tmp")
                            torch.save(payload, temporary)
                            os.replace(temporary, legacy)
                        checkpoint = legacy
                return FileResponse(
                    checkpoint, filename="resume.pt", media_type="application/octet-stream"
                )

        @app.post("/api/worker/jobs/{job_id}/artifacts/{kind}")
        async def artifact(
            job_id: str,
            kind: str,
            file: UploadFile = File(...),
            lease_token: str = Form(...),
            authorization: str | None = Header(default=None),
        ):
            from .models import MAX_CHECKPOINT_BYTES, load_checkpoint

            if kind not in {"checkpoint", "inference"} and not re.fullmatch(
                r"best-[0-9]{1,6}", kind
            ):
                fail("Unknown training artifact kind.")
            with self.lock:
                worker = self.authenticate(authorization)
                self.lease(worker, job_id, lease_token)
            uploads = self.root / ".uploads"
            if uploads.is_symlink():
                fail("The artifact upload directory is not a safe local directory.", 409)
            uploads.mkdir(exist_ok=True)
            descriptor, name = tempfile.mkstemp(prefix="worker-", suffix=".pt", dir=uploads)
            path = Path(name)
            try:
                count = 0
                with os.fdopen(descriptor, "wb") as output:
                    while chunk := await file.read(1024 * 1024):
                        count += len(chunk)
                        if count > MAX_CHECKPOINT_BYTES:
                            fail("Training artifacts are limited to 128 MiB.", 413)
                        output.write(chunk)
                try:
                    network, payload = await asyncio.to_thread(load_checkpoint, path)
                except (ValueError, RuntimeError, KeyError, TypeError, EOFError, OSError) as error:
                    fail(f"Invalid or incompatible training artifact: {error}")
                return await asyncio.to_thread(
                    self.accept_artifact,
                    job_id,
                    kind,
                    path,
                    lease_token,
                    authorization,
                    network,
                    payload,
                )
            finally:
                path.unlink(missing_ok=True)
                await file.close()

        @app.post("/api/worker/jobs/{job_id}/finish")
        def finish(
            job_id: str, request: FinishRequest, authorization: str | None = Header(default=None)
        ):
            if request.status not in {"completed", "cancelled", "failed"}:
                fail("Remote training status must be completed, cancelled or failed.")
            with self.lock:
                worker = self.authenticate(authorization)
                job = self.lease(worker, job_id, request.lease_token, terminal=True)
                if job["status"] not in {"running", "cancelling"}:
                    if job.get(
                        "finish_requested_status", job["status"]
                    ) == request.status and job.get("finish_received"):
                        return self.public_job(job)
                    fail("The remote training lease is no longer active.", 409)
                status = "cancelled" if job["status"] == "cancelling" else request.status
                models = job.get("uploaded_models", {})
                if status == "completed" and (
                    "inference" not in models
                    or not (self.directory(job_id) / "last.pt").is_file()
                    or job.get("checkpoint_iteration") != job["config"]["iterations"]
                ):
                    fail(
                        "Upload the full checkpoint and trained inference model before completing this job.",
                        409,
                    )
                result = {**job.get("progress", {}), "status": status}
                if status == "completed":
                    result["model"] = models["inference"]
                    job["model_id"] = models["inference"]["id"]
                job.update(
                    status=status,
                    ended_at=iso(),
                    error=request.error,
                    result=result,
                    finish_received=True,
                    finish_requested_status=request.status,
                )
                self.touch(worker)
                self.store.save_job(job)
                return self.public_job(job)
