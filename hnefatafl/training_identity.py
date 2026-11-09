"""Content identities and conservative compatibility for persistent experiments."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path

# Everything else is conservatively experiment-defining, including hardware,
# concurrency, deadlines and numerical backends. These can affect search results.
OPERATIONAL_TRAINING_FIELDS = {
    "output_dir",
    "registry_dir",
    "resume",
    "preset",
    "model_name",
    "checkpoint_frequency",
    "save_all_best",
}


def content_digest(value) -> str:
    """Hash tensor-only checkpoint data without serialization/path dependence."""
    import torch

    digest = hashlib.sha256()

    def add(item):
        if isinstance(item, torch.Tensor):
            tensor = item.detach().cpu().contiguous()
            digest.update(str((tensor.dtype, tuple(tensor.shape))).encode())
            digest.update(tensor.reshape(-1).view(torch.uint8).numpy().tobytes())
        elif isinstance(item, dict):
            digest.update(b"{")
            for key in sorted(item, key=str):
                # Snapshot metadata carries machine-local paths and cosmetic names;
                # the model/optimizer/replay/RNG state is hashed independently.
                from .job_archives import RUNTIME_KEYS

                if key in RUNTIME_KEYS | {"metadata", "checkpoint", "model_id"}:
                    continue
                add(key)
                add(item[key])
            digest.update(b"}")
        elif isinstance(item, (list, tuple)):
            digest.update(b"[")
            for child in item:
                add(child)
            digest.update(b"]")
        else:
            digest.update(json.dumps(item, sort_keys=True, allow_nan=False).encode())
        digest.update(b"\0")

    add(value)
    return digest.hexdigest()


def source_configuration(payload: dict) -> dict:
    """Effective saved optimizer settings, including older stale metadata."""
    config = dict(payload.get("metadata", {}).get("training_config", {}))
    groups = (payload.get("optimizer_state") or {}).get("param_groups", [])
    if groups:
        for name, parameter in (
            ("learning_rate", "lr"),
            ("weight_decay", "weight_decay"),
            ("momentum", "momentum"),
            ("schedulefree_warmup_steps", "warmup_steps"),
        ):
            if parameter in groups[0]:
                config[name] = groups[0][parameter]
    return config


def source_identity(payload: dict) -> dict:
    return {
        "checkpoint_content": content_digest(
            {
                key: payload.get(key)
                for key in ("model_config", "state_dict", "optimizer_state", "extra")
            }
        ),
        "weights_content": content_digest(payload.get("state_dict")),
        "iteration": int(payload.get("extra", {}).get("iteration", 0)),
    }


def file_generation(path: Path) -> list[int]:
    stat = path.stat()
    # Reading may legitimately update atime; only content/replacement signals
    # belong in the generation guard.
    return [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]


def checkpoint_identity(path: str | Path, payload: dict) -> dict:
    """Persist once per file generation; never called by dashboard polling.

    ctime/inode as well as mtime/size invalidate the cache after replacement or
    tampering. The cache is a performance hint in the trusted job store only.
    """
    path = Path(path)
    if path.is_symlink():
        raise ValueError("Source checkpoint must not be a symlink")
    generation = file_generation(path)
    cache = path.with_name("." + path.name + ".identity.json")
    if cache.is_file() and not cache.is_symlink():
        try:
            saved = json.loads(cache.read_text())
            if saved["generation"] == generation and saved["version"] == 1:
                return saved["identity"]
        except (ValueError, KeyError, OSError):
            pass
    identity = source_identity(payload)
    # Optional cache: read-only CLI sources remain usable.
    try:
        from .training import _write_json

        _write_json(cache, {"version": 1, "generation": generation, "identity": identity})
    except OSError:
        pass
    return identity


def differences(stored: object, requested: object, prefix: str = "") -> list[str]:
    if isinstance(stored, dict) and isinstance(requested, dict):
        names: list[str] = []
        for key in sorted(set(stored) | set(requested)):
            path = f"{prefix}.{key}" if prefix else str(key)
            names.extend(differences(stored.get(key), requested.get(key), path))
        return names
    return [] if stored == requested else [prefix or "identity"]


def established_identity(stored, trial_identities: list) -> dict | None:
    """Reconstruct only from complete, independently recorded trial metadata.

    v1 fingerprints omitted source content/workload and used a different
    objective. Historical data stays readable/exportable, never relabelled.
    """
    if isinstance(stored, dict) and stored.get("version") == 2:
        return stored
    if trial_identities and all(
        isinstance(value, dict) and value.get("version") == 2 and value == trial_identities[0]
        for value in trial_identities
    ):
        return trial_identities[0]
    return None


LEGACY_MESSAGE = (
    "Historical study compatibility cannot be established from its saved metadata. "
    "It remains available for read-only inspection and export; start a new study for further trials."
)


def check_study_identity(path: Path, requested: dict) -> None:
    """Read-only preflight, before the API launches any worker subprocess."""
    if path.is_symlink() or not path.is_file():
        raise ValueError("The saved study is missing or unsafe")
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("PRAGMA trusted_schema=OFF")
        if db.execute("SELECT COUNT(*) FROM studies").fetchone()[0] != 1:
            raise ValueError("Expected one owning study")
        rows = db.execute(
            "SELECT value_json FROM study_user_attributes WHERE key='hnefatafl_identity'"
        ).fetchall()
        stored = json.loads(rows[0][0]) if rows else None
        trials = db.execute("SELECT trial_id FROM trials").fetchall()
        evidence = dict(
            db.execute(
                "SELECT trial_id,value_json FROM trial_user_attributes WHERE key='experiment_identity'"
            )
        )
        identity = established_identity(
            stored, [json.loads(evidence.get(t[0], "null")) for t in trials]
        )
    if identity is None:
        raise ValueError(LEGACY_MESSAGE)
    conflicts = differences(identity, requested)
    if conflicts:
        raise ValueError(
            "The saved study has an incompatible experiment identity: " + ", ".join(conflicts)
        )


def load_source_checkpoint(path, loader):
    """Tie a loaded source to one file generation, including on cache hits."""
    path = Path(path)
    before = file_generation(path)
    model, payload = loader(path)
    after = file_generation(path)
    if before != after:
        raise ValueError("The source checkpoint changed while it was being loaded")
    identity = checkpoint_identity(path, payload)
    if after != file_generation(path):
        raise ValueError("The source checkpoint changed while its identity was checked")
    return model, payload, identity


@contextmanager
def study_lease(configuration: dict):
    """One local SQLite writer invocation; held through result finalization.

    Optuna allocates trial numbers transactionally, but experiment-identity
    adoption also needs serialization when two independent CLIs start together.
    This is separate from the server's job/catalog locks.
    """
    import os
    import sys

    output = Path(configuration.get("output_dir", "data/tuning")).resolve()
    storage = configuration.get("storage", f"sqlite:///{output / 'study.sqlite3'}")
    if not isinstance(storage, str) or not storage.startswith("sqlite:///"):
        yield  # External RDB storage retains Optuna's own concurrency model.
        return
    database = Path(storage[10:]).absolute()
    database.parent.mkdir(parents=True, exist_ok=True)
    if any(path.is_symlink() for path in (database, *database.parents)):
        raise ValueError("Study storage must not contain symlinks")
    lock_path = database.with_name("." + database.name + ".lock")
    if lock_path.is_symlink():
        raise ValueError("Study lock must not be a symlink")
    descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        try:
            if sys.platform == "win32":
                import msvcrt

                if os.fstat(descriptor).st_size == 0:
                    os.write(descriptor, b"0")
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise ValueError(
                "This optimization study is already running; stop it before resuming"
            ) from error
        yield
    finally:
        os.close(descriptor)
