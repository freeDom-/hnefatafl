"""Portable Optuna support for a single inactive, job-owned SQLite study only."""

from __future__ import annotations

import json
import re
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

DEFAULT_SEARCH_SPACE = {
    "learning_rate": {"type": "float", "low": 0.0001, "high": 0.01, "log": True},
    "exploration": {"type": "float", "low": 0.5, "high": 2.0},
    "channels": {"type": "categorical", "choices": [8, 16, 32]},
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
    """Validate the optional weighted multi-opponent objective.

    Fixed heuristics (Shield bearer, Shield maiden, Jarl) and a frozen neural
    model from the registry are accepted. The registry model ID is a content
    identity, so every trial evaluates the same pinned artifact.
    """
    if value is None or value == []:
        return []
    if not isinstance(value, list) or len(value) > 8:
        raise ArchiveError("Invalid optimization opponents.")
    allowed = {"shield_bearer", "shield_maiden", "jarl", "frozen_model"}
    model_id_pattern = re.compile(r"[0-9a-f]{24}")
    result = []
    for entry in value:
        if not isinstance(entry, dict):
            raise ArchiveError("Invalid optimization opponent.")
        opponent = entry.get("opponent", "shield_bearer")
        if opponent not in allowed:
            raise ArchiveError(
                "Optimization opponents must be fixed references: shield_bearer, "
                "shield_maiden, jarl or a frozen registered model."
            )
        weight = entry.get("weight", 1.0)
        if type(weight) is bool or type(weight) not in (int, float) or not 0 < float(weight) <= 1:
            raise ArchiveError("Optimization opponent weights must be between 0 and 1.")
        record: dict = {"opponent": opponent, "weight": float(weight)}
        if opponent == "frozen_model":
            if set(entry) != {"opponent", "weight", "model_id"} or not model_id_pattern.fullmatch(
                str(entry.get("model_id", ""))
            ):
                raise ArchiveError("A frozen neural opponent needs a registered model ID.")
            record["model_id"] = entry["model_id"]
        elif set(entry) - {"opponent", "weight"}:
            raise ArchiveError("Invalid optimization opponent.")
        result.append(record)
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
        "source_checkpoint",
        "opponents",
        "verification",
        "sampler",
        "pruner",
        "max_failures",
    }:
        raise ArchiveError("Unsupported optimization configuration.")
    result = portable(value)
    source_checkpoint = result.get("source_checkpoint")
    if source_checkpoint is not None:
        if (
            not isinstance(source_checkpoint, str)
            or not source_checkpoint
            or len(source_checkpoint) > 300
        ):
            raise ArchiveError("Invalid optimization source checkpoint reference.")
        result["source_checkpoint"] = source_checkpoint
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
    references: dict | None = None,
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
                if key in {"checkpoint", "trial_checkpoint", "last_checkpoint"}:
                    if not isinstance(value, str):
                        raise ArchiveError("Invalid Optuna checkpoint reference.")
                    if references and value in references:
                        value = "job://" + references[value]
                    elif source_root:
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
                    if table != "trial_user_attributes":
                        raise ArchiveError("Checkpoint references must belong to an Optuna trial.")
                    number = db.execute(
                        "SELECT trials.number FROM trials JOIN trial_user_attributes USING(trial_id) WHERE trial_user_attribute_id=?",
                        (identifier,),
                    ).fetchone()
                    if number is None or relative_name.split("/")[0] != f"trial-{number[0]}":
                        raise ArchiveError(
                            "Optuna checkpoint ownership does not match its trial number."
                        )
                    # rewrite applies the same path/type validation as job JSON.
                    rewrite(value, {}, artifact_root)
                    if not (artifact_root / relative_name).is_file():
                        raise ArchiveError(
                            "An Optuna trial checkpoint is missing from the archive."
                        )
                value = portable({key: value}, source_root, references=references)
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


def study_database(directory: Path, config: dict) -> Path:
    storage = config.get("storage", "sqlite:///" + str(directory / "study.sqlite3"))
    if not isinstance(storage, str) or not storage.startswith("sqlite:///"):
        raise ArchiveError("Only job-owned SQLite optimization studies can be exported.")
    source = Path(storage[10:])
    jobs = directory.parent
    if (
        source.name != "study.sqlite3"
        or source.parent.parent != jobs
        or not re.fullmatch(r"[0-9a-f]{32}", source.parent.name)
        or source.is_symlink()
        or source.parent.is_symlink()
        or source.resolve() != source
        or not source.is_file()
    ):
        raise ArchiveError(
            "This study uses shared or external storage outside the owning job store."
        )
    if source.stat().st_size > 128 * 1024 * 1024:
        raise ArchiveError("Optimization study exceeds the file size limit.")
    return source


def study_artifacts(directory: Path, config: dict) -> tuple[dict, dict]:
    """Consolidate global trial numbers from every contributing owned job.

    No arbitrary paths are followed: references must resolve to known artifacts
    in a sibling UUID job directory, without symlinks at any level.
    """
    source = study_database(directory, config)
    refs: dict[str, str] = {}
    contributors = {source.parent.name, directory.name}
    trials = []

    def add(value: str, archive_name: str):
        path = Path(value)
        try:
            relative = path.relative_to(directory.parent)
        except ValueError as error:
            raise ArchiveError(
                "Study has a nonportable dependency outside the job store."
            ) from error
        if len(relative.parts) < 2 or not re.fullmatch(r"[0-9a-f]{32}", relative.parts[0]):
            raise ArchiveError("Study artifact has no valid owning job.")
        if path.resolve() != path or any(parent.is_symlink() for parent in (path, *path.parents)):
            raise ArchiveError("Study artifacts must not contain symlinks or traversal.")
        if not path.is_file():
            raise ArchiveError(f"Missing study dependency: {archive_name}.")
        if not artifact_name_for_study(archive_name):
            raise ArchiveError("Unsupported study artifact dependency.")
        contributors.add(relative.parts[0])
        refs[str(path)] = archive_name

    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("PRAGMA trusted_schema=OFF")
        rows = db.execute("SELECT trial_id,number FROM trials").fetchall()
        for trial_id, number in rows:
            attrs = {
                key: json.loads(value)
                for key, value in db.execute(
                    "SELECT key,value_json FROM trial_user_attributes WHERE trial_id=?", (trial_id,)
                )
            }
            roots = set()
            paths = {}
            for key in ("checkpoint", "trial_checkpoint", "last_checkpoint"):
                value = attrs.get(key)
                if not value:
                    continue
                path = Path(value)
                if path.parent.name != f"trial-{number}":
                    raise ArchiveError("Optuna trial artifact ownership does not match its number.")
                add(value, f"trial-{number}/{path.name}")
                roots.add(path.parent)
                paths[key] = "job://" + refs[value]
            if len(roots) > 1:
                raise ArchiveError("Optuna trial checkpoints have conflicting artifact owners.")
            # Include other allowlisted artifacts (history, metrics, selected best,
            # full last checkpoint), not just the winning inference copy.
            for trial_root in roots:
                for artifact in trial_root.rglob("*"):
                    if artifact.is_symlink():
                        raise ArchiveError("Study artifacts must not contain symlinks.")
                    name = f"trial-{number}/" + artifact.relative_to(trial_root).as_posix()
                    if artifact.is_file() and artifact_name_for_study(name):
                        add(str(artifact), name)
            trials.append({"number": number, "artifacts": paths})
    resume = config.get("training", {}).get("resume") or config.get("source_checkpoint")
    if resume:
        add(resume, "dependencies/source/last.pt")
    return refs, {
        "version": 1,
        "owning_job": source.parent.name,
        "contributing_jobs": sorted(contributors),
        "trials": trials,
        "source_checkpoint": "job://dependencies/source/last.pt" if resume else None,
    }


def artifact_name_for_study(name: str) -> bool:
    from .job_archives import artifact_name

    return artifact_name(name, tuning=True)


def copy_study(
    directory: Path, config: dict, target: Path, *, references: dict | None = None
) -> None:
    source = study_database(directory, config)
    with (
        closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as src,
        closing(sqlite3.connect(target)) as dst,
    ):
        src.backup(dst)
    migrate_study(
        target,
        target.parent,
        source_root=directory,
        study_name=config.get("study_name", "hnefatafl"),
        references=references,
    )


def trial_report(trial) -> dict:
    """One persisted trial, shared by live results and portable study snapshots."""
    return {
        "number": trial.number,
        "state": trial.state.name,
        "score": trial.value,
        "parameters": trial.params,
        **{
            key: trial.user_attrs.get(key)
            for key in (
                "best_score",
                "best_iteration",
                "best_source",
                "source_best_score",
                "final_score",
                "aggregate_score",
                "overall_selected_score",
                "opponents",
                "error",
            )
        },
        "duration": trial.duration.total_seconds() if trial.duration else None,
    }


def winner_report(best) -> dict:
    return {
        "best_trial": best.number,
        "best_score": best.value,
        "best_parameters": best.params,
        **{
            key: best.user_attrs.get(attribute)
            for key, attribute in {
                "best_iteration": "best_iteration",
                "best_source": "best_source",
                "source_best_score": "source_best_score",
                "best_checkpoint": "checkpoint",
                "best_trial_checkpoint": "trial_checkpoint",
                "best_configuration": "training_config",
                "best_opponents": "opponents",
            }.items()
        },
    }


def portable_study_report(path: Path) -> dict:
    """Project the copied study, which may be newer than its original job result."""
    import optuna

    storage = optuna.storages.RDBStorage(f"sqlite:///{path}")
    try:
        summary = optuna.get_all_study_summaries(storage)[0]
        study = optuna.load_study(study_name=summary.study_name, storage=storage)
        trials = study.trials
        completed_trials = [
            trial for trial in trials if trial.state == optuna.trial.TrialState.COMPLETE
        ]
        completed = len(completed_trials)
        from .training import parameter_importance

        return {
            "parameter_importance": parameter_importance(completed_trials),
            "trials": [trial_report(trial) for trial in trials],
            "completed_trials": completed,
            "failed_trials": sum(trial.state == optuna.trial.TrialState.FAIL for trial in trials),
            **(winner_report(study.best_trial) if completed else {}),
        }
    finally:
        storage.engine.dispose()
