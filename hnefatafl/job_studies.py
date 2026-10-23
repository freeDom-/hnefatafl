"""Portable Optuna support for a single inactive, job-owned SQLite study only."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

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


def tuning_config(value: dict) -> dict:
    import optuna

    if not isinstance(value, dict) or set(value) - {
        "trials",
        "study_name",
        "seed",
        "search_space",
        "training",
        "output_dir",
        "storage",
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
    search = result.get("search_space")
    if search is not None:
        if not isinstance(search, dict) or not search or set(search) - TUNABLE:
            raise ArchiveError("Unsupported optimization search space.")
        for field, distribution in search.items():
            if not isinstance(distribution, dict):
                raise ArchiveError("Invalid optimization distribution.")
            kind = distribution.get("type", "float")
            options = {k: v for k, v in distribution.items() if k != "type"}
            if kind == "categorical":
                if (
                    set(options) != {"choices"}
                    or not isinstance(options["choices"], list)
                    or not options["choices"]
                ):
                    raise ArchiveError("Invalid optimization choices.")
                values = options["choices"]
                optuna.distributions.CategoricalDistribution(values)
            elif kind in {"float", "int"}:
                if (
                    set(options) - {"low", "high", "log", *({"step"} if kind == "int" else set())}
                    or not {"low", "high"} <= options.keys()
                ):
                    raise ArchiveError("Invalid optimization bounds.")
                if "log" in options and type(options["log"]) is not bool:
                    raise ArchiveError("Invalid optimization logarithmic flag.")
                values = [options["low"], options["high"]]
                if kind == "int":
                    if (
                        any(type(v) is not int for v in values)
                        or type(options.get("step", 1)) is not int
                    ):
                        raise ArchiveError("Invalid integer optimization bounds.")
                    optuna.distributions.IntDistribution(**options)
                else:
                    optuna.distributions.FloatDistribution(**options)
            else:
                raise ArchiveError("Unknown optimization distribution.")
            for number in values:
                if type(number) not in {int, float}:
                    raise ArchiveError("Optimization choices must be numeric.")
                training_config({**base, field: number})
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
