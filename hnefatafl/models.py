"""Policy/value networks and a validated, content-addressed model registry.

Checkpoints contain tensors and primitive values, never pickled model instances.
Values use the defender perspective: +1 is a defender win, -1 an attacker win.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
from typing import Any, Iterator
import zipfile

import torch
from torch import nn

from .engine import State

FORMAT_VERSION = 1
ARCHITECTURE = "tafl-policy-value-v1"
BOARD_SIZES = {"brandubh": 7, "tablut": 9, "copenhagen": 11}
MAX_CHECKPOINT_BYTES = 128 * 1024 * 1024
_MODEL_ID = re.compile(r"[0-9a-f]{24}\Z")


def resolve_device(requested: str = "auto") -> torch.device:
    """Resolve an explicit device, failing clearly rather than silently changing it."""
    requested = str(requested).lower()
    if requested == "auto":
        if torch.cuda.is_available():
            return torch.device("cuda")
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            return torch.device("mps")
        return torch.device("cpu")
    if requested == "cpu":
        return torch.device("cpu")
    if requested == "cuda" or re.fullmatch(r"cuda:\d+", requested):
        if not torch.cuda.is_available():
            raise ValueError("CUDA was requested, but compatible CUDA hardware is unavailable")
        device = torch.device(requested)
        if device.index is not None and device.index >= torch.cuda.device_count():
            raise ValueError("Requested CUDA device does not exist")
        return device
    if requested == "mps":
        if not hasattr(torch.backends, "mps") or not torch.backends.mps.is_available():
            raise ValueError("MPS was requested, but an available Metal device was not detected")
        return torch.device("mps")
    raise ValueError("device must be auto, cpu, cuda, cuda:N, or mps")


def device_info() -> dict[str, Any]:
    devices: list[dict[str, Any]] = []
    if torch.cuda.is_available():
        for index in range(torch.cuda.device_count()):
            properties = torch.cuda.get_device_properties(index)
            devices.append(
                {
                    "id": f"cuda:{index}",
                    "name": properties.name,
                    "total_memory": properties.total_memory,
                    "allocated_memory": torch.cuda.memory_allocated(index),
                }
            )
    from .inference_backends import openvino_capabilities

    return {
        **openvino_capabilities(),
        "evaluation_batching": True,
        "inference_cache_modes": ["off", "exact"],
        "training_symmetry_modes": ["off", "random_d4"],
        "cpu_available": True,
        "parallel_search": True,
        "torch_version": str(torch.__version__),
        "cuda_available": torch.cuda.is_available(),
        "cuda_version": torch.version.cuda,
        "mps_available": bool(hasattr(torch.backends, "mps") and torch.backends.mps.is_available()),
        "default_device": str(resolve_device()),
        "devices": devices,
    }


def encode_state(state: State) -> torch.Tensor:
    """Encode A, D, K, defender turn, throne and corners as six float planes."""
    size = state.size
    if state.ruleset not in BOARD_SIZES or size != BOARD_SIZES[state.ruleset]:
        raise ValueError("Unsupported ruleset or incompatible board dimensions")
    if len(state.board) != size * size or any(piece not in ".ADK" for piece in state.board):
        raise ValueError("Invalid board encoding")
    encoded = torch.zeros((6, size, size), dtype=torch.float32)
    for index, piece in enumerate(state.board):
        if piece in "ADK":
            encoded["ADK".index(piece), index // size, index % size] = 1.0
    if state.turn == "defenders":
        encoded[3].fill_(1.0)
    elif state.turn != "attackers":
        raise ValueError("Invalid side to move")
    encoded[4, size // 2, size // 2] = 1.0
    # Tablut escapes on any edge and does not use restricted corner squares.
    if state.ruleset != "tablut":
        for row, column in ((0, 0), (0, size - 1), (size - 1, 0), (size - 1, size - 1)):
            encoded[5, row, column] = 1.0
    return encoded


class _ResidualBlock(nn.Module):
    def __init__(self, channels: int):
        super().__init__()
        self.layers = nn.Sequential(
            nn.Conv2d(channels, channels, 3, padding=1, bias=False),
            nn.GroupNorm(1, channels),
            nn.SiLU(),
            nn.Conv2d(channels, channels, 3, padding=1, bias=False),
            nn.GroupNorm(1, channels),
        )
        self.activation = nn.SiLU()

    def forward(self, features: torch.Tensor) -> torch.Tensor:
        return self.activation(features + self.layers(features))


class PolicyValueNet(nn.Module):
    """Small residual network with a source/destination attention policy head.

    The output policy is [batch, size**4], indexed by source*size**2+target.
    A bilinear head models interactions between each source and destination while
    keeping model size independent of board area. Values have shape [batch].
    Separate model instances/checkpoints are used per ruleset.
    """

    def __init__(self, ruleset: str, channels: int = 32, blocks: int = 2):
        super().__init__()
        if ruleset not in BOARD_SIZES:
            raise ValueError("Unknown model ruleset")
        if type(channels) is not int or not 4 <= channels <= 128:
            raise ValueError("channels must be an integer between 4 and 128")
        if type(blocks) is not int or not 0 <= blocks <= 8:
            raise ValueError("blocks must be an integer between 0 and 8")
        self.ruleset = ruleset
        self.size = BOARD_SIZES[ruleset]
        self.channels = channels
        self.blocks = blocks
        self.config = {"ruleset": ruleset, "channels": channels, "blocks": blocks}
        self.trunk = nn.Sequential(
            nn.Conv2d(6, channels, 3, padding=1, bias=False),
            nn.GroupNorm(1, channels),
            nn.SiLU(),
            *[_ResidualBlock(channels) for _ in range(blocks)],
        )
        self.policy_dimension = min(channels, 16)
        self.policy_query = nn.Conv2d(channels, self.policy_dimension, 1)
        self.policy_key = nn.Conv2d(channels, self.policy_dimension, 1)
        self.policy_bias = nn.Conv2d(channels, 2, 1)
        self.value_head = nn.Sequential(
            nn.Conv2d(channels, 8, 1),
            nn.SiLU(),
            nn.AdaptiveAvgPool2d(1),
            nn.Flatten(),
            nn.Linear(8, channels),
            nn.SiLU(),
            nn.Linear(channels, 1),
            nn.Tanh(),
        )

    def forward(self, batch: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        if not torch.jit.is_tracing() and (
            batch.ndim != 4 or tuple(batch.shape[1:]) != (6, self.size, self.size)
        ):
            raise ValueError(f"Expected inputs with shape [batch,6,{self.size},{self.size}]")
        features = self.trunk(batch)
        query = self.policy_query(features).flatten(2).transpose(1, 2)
        key = self.policy_key(features).flatten(2)
        logits = torch.bmm(query, key) / math.sqrt(self.policy_dimension)
        bias = self.policy_bias(features).flatten(2)
        logits = logits + bias[:, 0, :, None] + bias[:, 1, None, :]
        return logits.flatten(1), self.value_head(features).squeeze(-1)


def _safe_data(value: Any, *, depth: int = 0) -> None:
    """Reject unsupported payload types before saving or accepting a checkpoint."""
    if depth > 30:
        raise ValueError("Checkpoint data nesting is too deep")
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("Checkpoint metadata contains a non-finite number")
        return
    if isinstance(value, torch.Tensor):
        if (
            value.layout != torch.strided
            or value.numel() * value.element_size() > MAX_CHECKPOINT_BYTES
        ):
            raise ValueError("Checkpoint contains an unsupported tensor")
        if not torch.isfinite(value).all().item():
            raise ValueError("Checkpoint contains non-finite tensors")
        return
    if isinstance(value, (list, tuple)):
        if len(value) > 1_000_000:
            raise ValueError("Checkpoint container is too large")
        for item in value:
            _safe_data(item, depth=depth + 1)
        return
    if isinstance(value, dict):
        if len(value) > 100_000:
            raise ValueError("Checkpoint dictionary is too large")
        for key, item in value.items():
            if type(key) not in (str, int):
                raise ValueError("Checkpoint keys must be strings or integers")
            _safe_data(item, depth=depth + 1)
        return
    raise ValueError(f"Unsupported checkpoint value type: {type(value).__name__}")


def _cpu_tensors(value: Any) -> Any:
    if isinstance(value, torch.Tensor):
        return value.detach().cpu()
    if isinstance(value, dict):
        return {key: _cpu_tensors(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return tuple(_cpu_tensors(item) for item in value)
    if isinstance(value, list):
        return [_cpu_tensors(item) for item in value]
    return value


def _validate_metadata(metadata: dict[str, Any], ruleset: str) -> None:
    """Validate public JSON fields before they can reach a registry or browser."""
    if not isinstance(metadata, dict):
        raise ValueError("Checkpoint metadata must be an object")
    if "self_play_games" in metadata:
        from .game_statistics import validate_statistics

        validate_statistics(metadata["self_play_games"])
    if "version" in metadata:
        version = metadata["version"]
        valid_number = type(version) is int and 0 <= version <= 2**53 - 1
        valid_string = (
            isinstance(version, str)
            and 1 <= len(version) <= 64
            and bool(version.strip())
            and version.isprintable()
        )
        if not (valid_number or valid_string):
            raise ValueError(
                "Checkpoint metadata version must be a nonnegative integer or 1–64 character string"
            )
    created_at = metadata.get("created_at")
    if not isinstance(created_at, str) or not 1 <= len(created_at) <= 64:
        raise ValueError("Checkpoint metadata created_at must be an ISO date/time string")
    try:
        datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(
            "Checkpoint metadata created_at must be an ISO date/time string"
        ) from error
    if "name" in metadata:
        name = metadata["name"]
        if (
            not isinstance(name, str)
            or not 1 <= len(name) <= 100
            or not name.strip()
            or not name.isprintable()
        ):
            raise ValueError("Checkpoint metadata name must contain 1 to 100 printable characters")
    for key in ("metrics", "training_config", "evaluation"):
        if key in metadata and not isinstance(metadata[key], dict):
            raise ValueError(f"Checkpoint metadata {key} must be an object")
    if metadata.get("ruleset", ruleset) != ruleset:
        raise ValueError("Checkpoint metadata ruleset does not match its architecture")
    try:
        json.dumps(metadata, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ValueError("Checkpoint metadata must contain JSON-compatible values") from error


def save_checkpoint(
    path: str | Path,
    model: PolicyValueNet,
    metadata: dict[str, Any],
    optimizer: torch.optim.Optimizer | None = None,
    extra: dict[str, Any] | None = None,
) -> Path:
    """Atomically save portable CPU tensors and safe primitive metadata."""
    _safe_data(metadata)
    if not isinstance(metadata, dict):
        raise ValueError("Checkpoint metadata must be an object")
    public_metadata = {"created_at": datetime.now(timezone.utc).isoformat(), **metadata}
    _validate_metadata(public_metadata, model.ruleset)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "format_version": FORMAT_VERSION,
        "architecture": ARCHITECTURE,
        "model_config": dict(model.config),
        "state_dict": _cpu_tensors(model.state_dict()),
        "metadata": public_metadata,
        "optimizer_state": _cpu_tensors(optimizer.state_dict()) if optimizer is not None else None,
        "extra": _cpu_tensors(extra or {}),
    }
    _safe_data(payload)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    os.close(descriptor)
    try:
        torch.save(payload, temporary)
        if Path(temporary).stat().st_size > MAX_CHECKPOINT_BYTES:
            raise ValueError("Checkpoint exceeds the 128 MiB size limit")
        # Complete serialization before replacing a valid, resumable checkpoint.
        with open(temporary, "rb") as saved:
            os.fsync(saved.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return path


def load_checkpoint(path: str | Path, device: str = "cpu") -> tuple[PolicyValueNet, dict[str, Any]]:
    """Load only restricted tensor checkpoints; arbitrary Python pickle is rejected."""
    path = Path(path)
    if not path.is_file() or not 0 < path.stat().st_size <= MAX_CHECKPOINT_BYTES:
        raise ValueError("Checkpoint is missing, empty, or exceeds the 128 MiB size limit")
    if not zipfile.is_zipfile(path):
        raise ValueError("Only the current tensor-only PyTorch checkpoint format is supported")
    with zipfile.ZipFile(path) as archive:
        if sum(item.file_size for item in archive.infolist()) > MAX_CHECKPOINT_BYTES:
            raise ValueError("Checkpoint expanded storage exceeds the size limit")
    try:
        payload = torch.load(path, map_location="cpu", weights_only=True)
    except Exception as error:
        raise ValueError("Invalid checkpoint; only safe tensor-only files are accepted") from error
    _safe_data(payload)
    if (
        not isinstance(payload, dict)
        or type(payload.get("format_version")) is not int
        or payload.get("format_version") != FORMAT_VERSION
    ):
        raise ValueError("Unsupported checkpoint format version")
    if payload.get("architecture") != ARCHITECTURE:
        raise ValueError("Unsupported neural-network architecture")
    configuration = payload.get("model_config")
    if not isinstance(configuration, dict) or set(configuration) != {
        "ruleset",
        "channels",
        "blocks",
    }:
        raise ValueError("Invalid model configuration")
    if not isinstance(payload.get("metadata"), dict) or not isinstance(payload.get("extra"), dict):
        raise ValueError("Invalid checkpoint metadata")
    # Model construction initializes weights before replacing them; loading must
    # not perturb a running experiment's CPU RNG stream.
    with torch.random.fork_rng(devices=[]):
        model = PolicyValueNet(**configuration)
    weights = payload.get("state_dict")
    expected = model.state_dict()
    if not isinstance(weights, dict) or set(weights) != set(expected):
        raise ValueError("Checkpoint weights do not match the declared architecture")
    if any(
        not isinstance(weights[key], torch.Tensor)
        or weights[key].shape != tensor.shape
        or weights[key].dtype != tensor.dtype
        for key, tensor in expected.items()
    ):
        raise ValueError("Checkpoint weights have incompatible shapes or dtypes")
    _validate_metadata(payload["metadata"], model.ruleset)
    try:
        model.load_state_dict(weights, strict=True)
    except (KeyError, TypeError, RuntimeError) as error:
        raise ValueError("Checkpoint weights do not match the declared architecture") from error
    model.to(resolve_device(device)).eval()
    return model, payload


def export_checkpoint(source: str | Path, target: str | Path) -> Path:
    """Export validated inference weights without optimizer or replay/RNG state.

    Keep the original training checkpoint to resume a job. An exported model is
    portable and retains the architecture, training configuration and evaluation
    metadata, but intentionally cannot reproduce the training process by itself.
    """
    source = Path(source)
    target = Path(target)
    if source.resolve() == target.resolve():
        raise ValueError("Inference export must use a different path from its training checkpoint")
    model, payload = load_checkpoint(source, device="cpu")
    from .game_statistics import checkpoint_statistics

    statistics = checkpoint_statistics(payload)
    if statistics is not None:
        payload["metadata"]["self_play_games"] = statistics
    return save_checkpoint(target, model, payload["metadata"])


def weights_fingerprint(model: PolicyValueNet) -> str:
    """Deterministic identity of the inference weights, independent of metadata.

    The digest covers the architecture and every tensor's key, shape, dtype and
    bytes. Optimizer state, training metadata, timestamps and file layout are
    deliberately excluded, so two checkpoints with identical inference behavior
    share a fingerprint even when their JSON metadata differs. This is a CPU-only
    operation and never moves a model to the GPU.
    """
    digest = hashlib.sha256()
    digest.update(ARCHITECTURE.encode("utf-8"))
    digest.update(json.dumps(model.config, sort_keys=True).encode("utf-8"))
    state = model.state_dict()
    for key in sorted(state):
        tensor = state[key].detach().to("cpu").contiguous()
        digest.update(key.encode("utf-8"))
        digest.update(str(tuple(tensor.shape)).encode("utf-8"))
        digest.update(str(tensor.dtype).encode("utf-8"))
        digest.update(tensor.numpy().tobytes())
    return digest.hexdigest()


@contextmanager
def _registry_lock(path: Path) -> Iterator[None]:
    # The documented deployment target is Linux. Windows uses its native file lock.
    with path.open("a+b") as lock:
        if sys.platform == "win32":
            import msvcrt

            if lock.tell() == 0:
                lock.write(b"\0")
                lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


class ModelRegistry:
    """Content-addressed local models with atomic metadata and safe imports."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.index_path = self.root / "index.json"
        self._in_transaction = False

    @contextmanager
    def _locked(self) -> Iterator[None]:
        if self._in_transaction:
            yield
        else:
            with _registry_lock(self.root / ".registry.lock"):
                yield

    @contextmanager
    def import_transaction(self) -> Iterator[None]:
        """Hold the catalog lock and roll back new imports if a job restore fails.

        Use a dedicated registry instance. Existing entries/copies are never
        repaired or replaced inside this transaction.
        """
        with _registry_lock(self.root / ".registry.lock"):
            entries = self._read_index()
            files = set(self.root.glob("*.pt"))
            self._in_transaction = True
            try:
                yield
            except BaseException:
                if self._read_index() != entries:
                    self._write_index(entries)
                for path in set(self.root.glob("*.pt")) - files:
                    path.unlink(missing_ok=True)
                raise
            finally:
                self._in_transaction = False

    def _read_index(self) -> list[dict[str, Any]]:
        if not self.index_path.exists():
            return []
        try:
            entries = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError("Model registry index is unreadable") from error
        if not isinstance(entries, list) or any(
            not isinstance(entry, dict) or not _MODEL_ID.fullmatch(str(entry.get("id", "")))
            for entry in entries
        ):
            raise ValueError("Invalid model registry index")
        return entries

    def _write_index(self, entries: list[dict[str, Any]]) -> None:
        """Atomically replace metadata while the caller holds the registry lock."""
        descriptor, temporary = tempfile.mkstemp(prefix=".index-", dir=self.root)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as index:
                json.dump(entries, index, indent=2, allow_nan=False)
                index.write("\n")
                index.flush()
                os.fsync(index.fileno())
            os.replace(temporary, self.index_path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def _same_architecture(self, entry: dict[str, Any], model: PolicyValueNet) -> bool:
        """Whether a catalog entry describes the same ruleset and network shape."""
        architecture = entry.get("architecture")
        return (
            entry.get("ruleset") == model.ruleset
            and isinstance(architecture, dict)
            and architecture.get("ruleset") == model.config["ruleset"]
            and architecture.get("channels") == model.config["channels"]
            and architecture.get("blocks") == model.config["blocks"]
        )

    def _inspect_entry(self, entry: dict[str, Any]) -> tuple[str, str | None, bool]:
        """Verify one owned copy against its recorded integrity hash.

        Returns ``(state, fingerprint, changed)``. ``state`` is ``"ok"`` when the
        file is present and its weights are known, ``"missing"`` when the file is
        absent, and ``"unreadable"`` when the file cannot be validated. A readable
        file is authoritative, so a stale recorded fingerprint or integrity hash is
        corrected in place and ``changed`` is true. Intact files are checked with a
        streaming hash and are never deserialized; only a changed or legacy file is
        loaded, and always on the CPU.
        """
        path = self.root / f"{entry['id']}.pt"
        if not path.is_file():
            return "missing", None, False
        try:
            with path.open("rb") as handle:
                file_hash = hashlib.file_digest(handle, "sha256").hexdigest()
        except OSError:
            return "unreadable", None, False
        recorded_hash = entry.get("checkpoint_sha256")
        recorded_fingerprint = entry.get("weights_sha256")
        if recorded_hash == file_hash and isinstance(recorded_fingerprint, str):
            return "ok", recorded_fingerprint, False
        try:
            loaded, _ = load_checkpoint(path)
        except (ValueError, RuntimeError, OSError, EOFError, zipfile.BadZipFile):
            return "unreadable", None, False
        fingerprint = weights_fingerprint(loaded)
        changed = recorded_hash != file_hash or recorded_fingerprint != fingerprint
        entry["weights_sha256"] = fingerprint
        entry["checkpoint_sha256"] = file_hash
        return "ok", fingerprint, changed

    def _matching_entry(
        self, entries: list[dict[str, Any]], fingerprint: str, model: PolicyValueNet
    ) -> tuple[dict[str, Any] | None, bool, str | None]:
        """Find an existing entry whose verified weights match ``fingerprint``.

        Entries imported before weight fingerprints existed are lazily backfilled
        once and persisted. A readable file is authoritative: a stale recorded
        fingerprint is corrected and never used to claim a match. A file that is
        missing or unreadable is only reused when its recorded fingerprint matches,
        so it can be restored from the validated incoming copy. A valid file with
        different weights is never overwritten.

        Returns ``(entry, changed, state)`` where ``state`` is ``None`` when no
        match was found, ``"ok"`` for a usable copy, or ``"missing"``/``"unreadable"``
        when the matched copy needs repair.
        """
        changed = False
        for entry in entries:
            stored = entry.get("weights_sha256")
            if stored is None:
                if not self._same_architecture(entry, model):
                    continue
            elif stored != fingerprint:
                continue
            state, actual, entry_changed = self._inspect_entry(entry)
            changed = changed or entry_changed
            if actual is not None:
                if actual == fingerprint:
                    return entry, changed, state
                # A valid copy with different weights: correct it and keep looking.
                continue
            if stored == fingerprint:
                # Missing or unreadable, but recorded as the same weights: repairable.
                return entry, changed, state
        return None, changed, None

    def duplicates(self) -> list[list[dict[str, Any]]]:
        """Read-only groups of registered entries that share inference weights.

        Detection never deletes or rewrites anything. It holds the registry lock
        for the whole scan, so concurrent imports cannot make the groups internally
        inconsistent, and backfills missing fingerprints on in-memory copies only.
        """
        groups: dict[str, list[dict[str, Any]]] = {}
        with self._locked():
            for source in self._read_index():
                entry = dict(source)
                state, fingerprint, _ = self._inspect_entry(entry)
                if state != "ok" or fingerprint is None:
                    continue
                groups.setdefault(fingerprint, []).append(
                    {**entry, "path": str(self.root / f"{entry['id']}.pt")}
                )
        return [group for group in groups.values() if len(group) > 1]

    def list(self) -> list[dict[str, Any]]:
        with self._locked():
            entries = self._read_index()
        return [{**entry, "path": str(self.root / f"{entry['id']}.pt")} for entry in entries]

    def get(self, model_id: str) -> dict[str, Any]:
        if not isinstance(model_id, str) or not _MODEL_ID.fullmatch(model_id):
            raise ValueError("Invalid model ID")
        for entry in self.list():
            if entry["id"] == model_id:
                if not Path(entry["path"]).is_file():
                    raise ValueError("Registered model checkpoint is missing")
                return entry
        raise ValueError("Model is not registered")

    def register(self, path: str | Path, name: str | None = None) -> dict[str, Any]:
        return self.import_model(path, name)

    def rename(self, model_id: str, name: str) -> dict[str, Any]:
        """Change the catalog label without altering checkpoint bytes or identity."""
        if not isinstance(model_id, str) or not _MODEL_ID.fullmatch(model_id):
            raise ValueError("Invalid model ID")
        if not isinstance(name, str) or not name.strip() or len(name) > 100:
            raise ValueError("Model name must contain 1 to 100 characters")
        with self._locked():
            entries = self._read_index()
            entry = next((value for value in entries if value["id"] == model_id), None)
            if entry is None:
                raise ValueError("Model is not registered")
            entry["name"] = name.strip()
            self._write_index(entries)
            return {**entry, "path": str(self.root / f"{model_id}.pt")}

    def delete(self, model_id: str) -> dict[str, Any]:
        """Remove one registry entry and its owned copy, retaining original checkpoints.

        All registry readers and writers use the same lock. Commit metadata first
        so failed index writes leave weights intact; restore the prior metadata
        if removing the copy fails. Unlinking a symlink removes only that link.
        """
        if not isinstance(model_id, str) or not _MODEL_ID.fullmatch(model_id):
            raise ValueError("Invalid model ID")
        with self._locked():
            entries = self._read_index()
            removed = next((entry for entry in entries if entry["id"] == model_id), None)
            if removed is None:
                raise ValueError("Model is not registered")
            self._write_index([entry for entry in entries if entry["id"] != model_id])
            try:
                (self.root / f"{model_id}.pt").unlink(missing_ok=True)
            except OSError:
                self._write_index(entries)
                raise
            return dict(removed)

    def _install_repair(self, temporary: Path, target: Path) -> None:
        """Atomically restore an owned copy, refusing to touch it in a transaction.

        An archive import must roll back completely on failure, so an existing
        registered file is never replaced inside ``import_transaction()``. The
        operator can re-import the model normally to repair it, then retry.
        """
        if self._in_transaction:
            raise ValueError(
                "A registered model copy is missing or damaged and cannot be "
                "repaired during an archive import; re-import the model first"
            )
        os.replace(temporary, target)

    def import_model(self, path: str | Path, name: str | None = None) -> dict[str, Any]:
        """Copy first, validate that exact copy, then publish metadata atomically.

        Registration is idempotent by verified inference weights: an equivalent
        model already in the catalog is reused instead of copied. Existing copies
        are integrity-checked against their recorded file hash before reuse, so a
        missing, truncated, corrupted or weight-modified checkpoint is never
        silently reused. A missing or unreadable copy is restored atomically from
        the validated incoming file when its recorded weights match; a valid copy
        with different weights is never overwritten. The returned mapping carries a
        transient ``reused`` flag that is never persisted in the index.
        """
        if name is not None and (not isinstance(name, str) or not name.strip() or len(name) > 100):
            raise ValueError("Model name must contain 1 to 100 characters")
        source = Path(path)
        if not source.is_file() or source.stat().st_size > MAX_CHECKPOINT_BYTES:
            raise ValueError("Model file is missing or too large")
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".import-", suffix=".pt", dir=self.root
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            shutil.copyfile(source, temporary)
            model, payload = load_checkpoint(temporary)
            fingerprint = weights_fingerprint(model)
            with temporary.open("rb") as handle:
                file_sha256 = hashlib.file_digest(handle, "sha256").hexdigest()
            identifier = file_sha256[:24]
            metadata = payload["metadata"]
            from .game_statistics import checkpoint_statistics

            statistics = checkpoint_statistics(payload)
            if statistics is not None:
                metadata = {**metadata, "self_play_games": statistics}
            # Keep registry metadata JSON-serializable even when checkpoint extras
            # contain optimizer tensors or training RNG state.
            json_metadata = {}
            for key in (
                "metrics",
                "training_config",
                "evaluation",
                "created_at",
                "iteration",
                "version",
                "self_play_games",
            ):
                value = metadata.get(key)
                if value is not None:
                    try:
                        json.dumps(value, allow_nan=False)
                    except (TypeError, ValueError):
                        raise ValueError(
                            f"Registry metadata {key} must be JSON-serializable"
                        ) from None
                    json_metadata[key] = value
            entry = {
                "id": identifier,
                "name": name.strip()
                if name
                else str(metadata.get("name", f"{model.ruleset} model"))[:100],
                "ruleset": model.ruleset,
                "architecture": {"name": ARCHITECTURE, **model.config},
                "version": metadata.get("version", 1),
                "created_at": metadata.get("created_at", datetime.now(timezone.utc).isoformat()),
                "imported_at": datetime.now(timezone.utc).isoformat(),
                "metrics": {},
                "weights_sha256": fingerprint,
                "checkpoint_sha256": file_sha256,
                **json_metadata,
            }
            destination = self.root / f"{identifier}.pt"
            with self._locked():
                entries = self._read_index()
                existing = next((value for value in entries if value["id"] == identifier), None)
                if existing is not None:
                    state, actual, entry_changed = self._inspect_entry(existing)
                    changed = entry_changed
                    if actual is not None and actual != fingerprint:
                        # The same ID resolves to different, valid weights: never overwrite.
                        raise ValueError(
                            "A registered model file with this ID contains different weights"
                        )
                    if state != "ok":
                        # Re-import repairs a missing or damaged owned copy atomically.
                        self._install_repair(temporary, destination)
                        existing["weights_sha256"] = fingerprint
                        existing["checkpoint_sha256"] = file_sha256
                        changed = True
                    if changed:
                        self._write_index(entries)
                    return {**existing, "path": str(destination), "reused": True}
                match, changed, match_state = self._matching_entry(entries, fingerprint, model)
                if match is not None:
                    match_path = self.root / f"{match['id']}.pt"
                    if match_state != "ok":
                        # Restore the expected weights from the validated incoming copy.
                        self._install_repair(temporary, match_path)
                        match["checkpoint_sha256"] = file_sha256
                        changed = True
                    if changed:
                        self._write_index(entries)
                    return {**match, "path": str(match_path), "reused": True}
                if changed:
                    self._write_index(entries)
                if self._in_transaction and destination.exists():
                    raise ValueError("An unregistered model file already occupies this model ID")
                os.replace(temporary, destination)
                entries.append(entry)
                self._write_index(entries)
            return {**entry, "path": str(destination), "reused": False}
        finally:
            temporary.unlink(missing_ok=True)
