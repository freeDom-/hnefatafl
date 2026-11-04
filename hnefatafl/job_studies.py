"""Portable Optuna support for a single inactive, job-owned SQLite study only."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from .job_archives import ArchiveError, portable, rewrite, training_config, validate_json

TABLES = {
    "studies",
    "version_info",
    "study_directions",
    "study_user_attributes",
    "study_system_attributes",
    "trials",
    "trial_user_attributes",
    "trial_system_attributes",
    "trial_params",
    "trial_values",
    "trial_intermediate_values",
    "trial_heartbeats",
    "alembic_version",
}
TUNABLE = {
    "channels",
    "blocks",
    "learning_rate",
    "weight_decay",
    "exploration",
    "temperature",
    "mcts_iterations",
    "batch_size",
    "epochs",
    "dirichlet_alpha",
    "dirichlet_fraction",
}


def search_space_config(value: Any) -> dict:
    """Validate and normalize the Optuna search-space contract.

    Three distribution kinds are supported and each extreme is checked against
    the training validator by the caller:

    * ``float`` — optional ``log`` for a positive logarithmic range;
    * ``int`` — optional positive integer ``step`` (``log`` requires step 1);
    * ``categorical`` — a non-empty list of numeric choices.

    The returned mapping is a normalized copy, so callers never mutate the
    stored configuration and unknown options are rejected by name.
    """
    import optuna

    if value is None:
        return {}
    if not isinstance(value, dict) or not value or set(value) - TUNABLE:
        raise ArchiveError("Unsupported optimization search space.")
    result: dict = {}
    for field, distribution in value.items():
        if not isinstance(distribution, dict):
            raise ArchiveError("Invalid optimization distribution.")
        kind = distribution.get("type", "float")
        if not isinstance(kind, str):
            raise ArchiveError("A search distribution type must be a string.")
        if kind == "categorical":
            if set(distribution) != {"type", "choices"}:
                raise ArchiveError("Invalid optimization choices.")
            choices = distribution["choices"]
            if not isinstance(choices, list) or not choices:
                raise ArchiveError("Invalid optimization choices.")
            if any(type(choice) is bool or type(choice) not in (int, float) for choice in choices):
                raise ArchiveError("Optimization choices must be numeric.")
            optuna.distributions.CategoricalDistribution(choices)
            result[field] = {"type": "categorical", "choices": list(choices)}
            continue
        if kind not in {"float", "int"}:
            raise ArchiveError("Unknown optimization distribution.")
        allowed = {"type", "low", "high", "log"} | ({"step"} if kind == "int" else set())
        if set(distribution) - allowed or not {"low", "high"} <= set(distribution):
            raise ArchiveError("Invalid optimization bounds.")
        low, high = distribution["low"], distribution["high"]
        if any(type(v) is bool or type(v) not in (int, float) for v in (low, high)):
            raise ArchiveError("Optimization bounds must be numeric.")
        if low > high:
            raise ArchiveError("Invalid optimization bounds.")
        logarithmic = distribution.get("log", False)
        if type(logarithmic) is not bool:
            raise ArchiveError("Invalid optimization logarithmic flag.")
        if logarithmic and low <= 0:
            raise ArchiveError("Logarithmic optimization requires positive bounds.")
        normalized: dict = {"type": kind, "low": low, "high": high, "log": logarithmic}
        if kind == "int":
            if type(low) is not int or type(high) is not int:
                raise ArchiveError("Integer optimization requires integer bounds.")
            step = distribution.get("step", 1)
            if type(step) is not int or step < 1 or (logarithmic and step != 1):
                raise ArchiveError(
                    "Integer optimization requires a positive step, and step 1 for logarithmic sampling."
                )
            normalized["step"] = step
            optuna.distributions.IntDistribution(low, high, step=step, log=logarithmic)
        else:
            if "step" in distribution:
                raise ArchiveError("Float optimization does not accept a step.")
            optuna.distributions.FloatDistribution(low, high, log=logarithmic)
        result[field] = normalized
    return result


def opponents_config(value: Any) -> list[dict]:
    """Validate the optional weighted multi-opponent objective."""
    if value is None:
        return []
    if not isinstance(value, list) or not value or len(value) > 8:
        raise ArchiveError("Invalid optimization opponents.")
    allowed = {"shield_bearer", "shield_maiden", "jarl"}
    result = []
    for entry in value:
        if not isinstance(entry, dict) or set(entry) - {"opponent", "weight"}:
            raise ArchiveError("Invalid optimization opponent.")
        opponent = entry.get("opponent", "shield_bearer")
        if opponent not in allowed:
            raise ArchiveError(
                "Optimization opponents must be fixed references: shield_bearer, shield_maiden or jarl."
            )
        weight = entry.get("weight", 1.0)
        if type(weight) is bool or type(weight) not in (int, float) or not 0 < float(weight) <= 1:
            raise ArchiveError("Optimization opponent weights must be between 0 and 1.")
        result.append({"opponent": opponent, "weight": float(weight)})
    return result


def verification_config(value: Any) -> dict:
    """Validate the optional held-out verification stage."""
    if value is None:
        return {}
    if not isinstance(value, dict) or set(value) - {"games", "seed", "opponent"}:
        raise ArchiveError("Invalid optimization verification settings.")
    result: dict = {}
    if "games" in value:
        games = value["games"]
        if type(games) is not int or not 2 <= games <= 10000 or games % 2:
            raise ArchiveError("Verification games must be an even integer between 2 and 10000.")
        result["games"] = games
    if "seed" in value:
        seed = value["seed"]
        if type(seed) is not int or not 0 <= seed < 2**32:
            raise ArchiveError("Verification seed must be an unsigned 32-bit integer.")
        result["seed"] = seed
    if "opponent" in value:
        opponent = value["opponent"]
        if opponent not in {"shield_bearer", "shield_maiden", "jarl"}:
            raise ArchiveError(
                "Verification opponent must be shield_bearer, shield_maiden or jarl."
            )
        result["opponent"] = opponent
    return result


def sampler_config(value: Any) -> dict:
    if value is None:
        return {}
    allowed = {"n_startup_trials", "multivariate", "group", "constant_liar"}
    if not isinstance(value, dict) or set(value) - allowed:
        raise ArchiveError("Invalid optimization sampler settings.")
    result: dict = {}
    if "n_startup_trials" in value:
        startup = value["n_startup_trials"]
        if type(startup) is not int or not 0 <= startup <= 1000:
            raise ArchiveError("Sampler startup trials must be between 0 and 1000.")
        result["n_startup_trials"] = startup
    for flag in ("multivariate", "group", "constant_liar"):
        if flag in value:
            if type(value[flag]) is not bool:
                raise ArchiveError("Sampler flags must be booleans.")
            result[flag] = value[flag]
    return result


def pruner_config(value: Any) -> dict:
    if value is None:
        return {}
    allowed = {"n_startup_trials", "n_warmup_steps", "interval_steps"}
    if not isinstance(value, dict) or set(value) - allowed:
        raise ArchiveError("Invalid optimization pruner settings.")
    result: dict = {}
    for key in allowed:
        if key in value:
            number = value[key]
            if type(number) is not int or not 0 <= number <= 100000:
                raise ArchiveError("Pruner settings must be non-negative integers.")
            result[key] = number
    return result


def tuning_config(value: dict) -> dict:
    if not isinstance(value, dict) or set(value) - {
        "trials",
        "study_name",
        "seed",
        "search_space",
        "training",
        "output_dir",
        "storage",
        "resume_study",
        "opponents",
        "verification",
        "sampler",
        "pruner",
        "max_failures",
    }:
        raise ArchiveError("Unsupported optimization configuration.")
    result = portable(value)
    for key, default, low, high in (("trials", 4, 1, 10000), ("seed", 0, 0, 2**32 - 1)):
        number = result.get(key, default)
        if type(number) is not int or not low <= number <= high:
            raise ArchiveError(f"Invalid optimization {key}.")
        result[key] = number
    name = result.get("study_name", "hnefatafl")
    if not isinstance(name, str) or not 1 <= len(name) <= 100:
        raise ArchiveError("Invalid optimization study name.")
    result["study_name"] = name
    base = training_config(result.get("training", {"preset": "smoke", "ruleset": "brandubh"}))
    result["training"] = base
    result["search_space"] = search_space_config(result.get("search_space"))
    result["opponents"] = opponents_config(result.get("opponents"))
    result["verification"] = verification_config(result.get("verification"))
    result["sampler"] = sampler_config(result.get("sampler"))
    result["pruner"] = pruner_config(result.get("pruner"))
    failures = result.get("max_failures", min(3, result["trials"]))
    if type(failures) is not int or not 1 <= failures <= 10000:
        raise ArchiveError("Invalid optimization failure budget.")
    result["max_failures"] = failures
    return result


def _migrate_study(
    path: Path,
    artifact_root: Path,
    *,
    source_root: Path | None = None,
    destination: Path | None = None,
    ids: dict | None = None,
    study_name: str | None = None,
) -> None:
    """Validate a copied database, scrub attributes and rebase owned checkpoint refs.

    No SQL supplied by the archive is executed. Unknown schemas, extra studies,
    live trials and external dependencies are rejected rather than repaired.
    """
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("PRAGMA trusted_schema=OFF")
        db.execute("PRAGMA secure_delete=ON")
        schema = db.execute(
            "SELECT type,name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
        ).fetchall()
        tables = {name for kind, name, _ in schema if kind == "table"}
        if tables != TABLES or any(
            kind not in {"table", "index"} or (sql and "VIRTUAL TABLE" in sql.upper())
            for kind, _, sql in schema
        ):
            raise ArchiveError("Unsupported Optuna SQLite schema or executable database objects.")
        if db.execute("PRAGMA quick_check(1)").fetchall() != [("ok",)]:
            raise ArchiveError("The optimization study database is corrupt.")
        if db.execute("SELECT schema_version FROM version_info").fetchall() != [(12,)]:
            raise ArchiveError("Unsupported Optuna study schema version (expected 12).")
        if study_name is not None and db.execute("SELECT study_name FROM studies").fetchall() != [
            (study_name,)
        ]:
            raise ArchiveError("Study name does not match the optimization job configuration.")
        if db.execute("SELECT COUNT(*) FROM studies").fetchone()[0] != 1:
            raise ArchiveError("Only a single job-owned Optuna study can be migrated.")
        if db.execute(
            "SELECT COUNT(*) FROM trials WHERE state NOT IN ('COMPLETE','PRUNED','FAIL')"
        ).fetchone()[0]:
            raise ArchiveError("Stop all Optuna trials before migrating this study.")
        if db.execute("SELECT COUNT(*) FROM trials").fetchone()[0] > 10000:
            raise ArchiveError("Optimization study exceeds the trial count limit.")
        for table in (
            "study_user_attributes",
            "study_system_attributes",
            "trial_user_attributes",
            "trial_system_attributes",
        ):
            count, total = db.execute(
                f"SELECT COUNT(*),COALESCE(SUM(length(value_json)),0) FROM {table}"
            ).fetchone()
            if count > 100000 or total > 16 * 1024 * 1024:
                raise ArchiveError("Optimization attributes exceed metadata limits.")
            rows = db.execute(f"SELECT {table[:-1]}_id,key,value_json FROM {table}")
            for identifier, key, encoded in rows.fetchall():
                if len(encoded) > 16 * 1024 * 1024 or len(key) > 200:
                    raise ArchiveError("Optimization attributes exceed metadata limits.")
                value = json.loads(encoded)
                validate_json(value)
                if key == "checkpoint":
                    if not isinstance(value, str):
                        raise ArchiveError("Invalid Optuna checkpoint reference.")
                    if source_root:
                        try:
                            relative = Path(value).resolve().relative_to(source_root)
                        except ValueError as exc:
                            raise ArchiveError(
                                "The study depends on checkpoints outside its job directory."
                            ) from exc
                        value = "job://" + relative.as_posix()
                    if not value.startswith("job://"):
                        raise ArchiveError(
                            "The study contains a nonportable checkpoint dependency."
                        )
                    relative_name = value[6:]
                    # rewrite applies the same path/type validation as job JSON.
                    rewrite(value, {}, artifact_root)
                    if not (artifact_root / relative_name).is_file():
                        raise ArchiveError(
                            "An Optuna trial checkpoint is missing from the archive."
                        )
                value = portable({key: value}, source_root)
                if destination is not None:
                    value = rewrite(value, ids or {}, destination)
                if key not in value:
                    db.execute(f"DELETE FROM {table} WHERE {table[:-1]}_id=?", (identifier,))
                else:
                    db.execute(
                        f"UPDATE {table} SET value_json=? WHERE {table[:-1]}_id=?",
                        (json.dumps(value[key], allow_nan=False), identifier),
                    )
        # Old worker heartbeats are execution ownership, never destination leases.
        db.execute("DELETE FROM trial_heartbeats")
        db.commit()
        # Remove old attribute values/paths from free pages as well as live rows.
        db.execute("VACUUM")
        db.execute("PRAGMA journal_mode=DELETE")
    import optuna

    storage = optuna.storages.RDBStorage(f"sqlite:///{path}")
    summaries = optuna.get_all_study_summaries(storage)
    study = optuna.load_study(study_name=summaries[0].study_name, storage=storage)
    # Materialize trials through the implementation's compatibility validator.
    study.get_trials(deepcopy=False)
    storage.engine.dispose()


def migrate_study(path: Path, artifact_root: Path, **kwargs) -> None:
    import optuna

    try:
        _migrate_study(path, artifact_root, **kwargs)
    except (sqlite3.Error, optuna.exceptions.StorageInternalError) as exc:
        raise ArchiveError("Invalid or incompatible Optuna SQLite study.") from exc


def copy_study(directory: Path, config: dict, target: Path) -> None:
    storage = config.get("storage", "sqlite:///" + str(directory / "study.sqlite3"))
    if not isinstance(storage, str) or not storage.startswith("sqlite:///"):
        raise ArchiveError("Only job-owned SQLite optimization studies can be exported.")
    source = Path(storage[10:]).resolve()
    if source != directory / "study.sqlite3" or source.is_symlink() or not source.is_file():
        raise ArchiveError(
            "This optimization study uses shared or external storage; migrate its original owning job or use a full-instance backup."
        )
    if source.stat().st_size > 128 * 1024 * 1024:
        raise ArchiveError("Optimization study exceeds the file size limit.")
    with (
        closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as src,
        closing(sqlite3.connect(target)) as dst,
    ):
        src.backup(dst)
    migrate_study(
        target, directory, source_root=directory, study_name=config.get("study_name", "hnefatafl")
    )
