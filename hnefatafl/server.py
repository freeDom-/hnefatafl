"""Server-authoritative local game API, SQLite history and isolated training jobs."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
from contextlib import asynccontextmanager, contextmanager, ExitStack
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, NoReturn
from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, Query, Response, UploadFile, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator

from .account_policy import (
    BIDI_CONTROLS,
    game_accounts,
    game_players,
    public_identity,
    resolve_accounts,
)
from .engine import Move, RULESETS, State, apply_move, legal_moves, new_game
from .identities import agent_identity, effective_ai_config
from .training_identity import OPERATIONAL_TRAINING_FIELDS
from .storage import ConflictError, Store
from .versioning import application_metadata


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SIDES = ("attackers", "defenders")


def ai_variant_id(config: dict) -> str:
    # Randomness changes an experiment, not the identity of its search agent.
    identity = {key: value for key, value in config.items() if key != "seed"}
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return "ai:" + hashlib.sha256(encoded.encode()).hexdigest()[:24]


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


# The accepted inputs of a new training job. `resume` is accepted but is resolved
# to an owned checkpoint path before launching. Kept at module scope so the
# contract test can compare it with the frontend and remote-worker field sets.
TRAINING_FIELDS = frozenset(
    {
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
        "schedulefree_warmup_steps",
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
        "search_workers",
        "resume",
        "model_name",
        "adaptation",
        "adaptation_lr_candidates",
        "adaptation_cadence",
        "adaptation_warmup",
        "adaptation_min_dwell",
        "adaptation_min_dwell_iterations",
        "adaptation_lag",
        "adaptation_min_trials",
    }
)

# Optuna jobs accept the study controls plus one nested training configuration.
TUNE_FIELDS = frozenset(
    {
        "trials",
        "study_name",
        "seed",
        "search_space",
        "training",
        "resume_study",
        "source_checkpoint",
        "opponents",
        "verification",
        "sampler",
        "pruner",
        "max_failures",
    }
)


class TrainingFieldsRejected(Exception):
    """Rejected training configuration fields, named so a client can act on them.

    ``fields`` uses dotted paths for nested configurations (``training.channels``)
    so the caller can map every entry back to an input. The HTTP response keeps
    ``detail`` as a human-readable string for existing consumers and adds the
    machine-readable ``code`` and ``fields`` beside it.
    """

    code = "unsupported_training_field"

    def __init__(self, fields: list[str], message: str) -> None:
        super().__init__(message)
        self.fields = fields
        self.message = message


# The CLI accepts these persistent-output settings, but the HTTP API owns them:
# letting a client choose a server path would let it write outside the job store.
SERVER_OWNED_TRAINING_FIELDS = {
    "output_dir": "assigned by the server for each job",
    "registry_dir": "chosen by the server for registered models",
}


def unsupported_training_message(fields: list[str]) -> str:
    noun = "field" if len(fields) == 1 else "fields"
    message = f"Unsupported training configuration {noun}: {', '.join(fields)}"
    owned = sorted(
        field for field in fields if field.rsplit(".", 1)[-1] in SERVER_OWNED_TRAINING_FIELDS
    )
    if owned:
        reasons = "; ".join(
            f"{field} is {SERVER_OWNED_TRAINING_FIELDS[field.rsplit('.', 1)[-1]]}"
            for field in owned
        )
        message += f". {reasons}; remove it or run the training CLI with this config"
    return message


def reject_training_fields(
    fields: set[str] | frozenset[str], *, prefix: str = "", message: str | None = None
) -> None:
    """Reject every unsupported field, deterministically ordered and fully named."""
    names = [f"{prefix}{name}" for name in sorted(fields)]
    if names:
        raise TrainingFieldsRejected(names, message or unsupported_training_message(names))


def check_training_fields(
    values: object, allowed: set[str] | frozenset[str], *, prefix: str = ""
) -> None:
    if isinstance(values, dict):
        reject_training_fields(set(values) - set(allowed), prefix=prefix)


# Fields that define what a tuning study optimizes. A resumed study may extend
# its trial budget, but changing one of these would make its stored trials
# incomparable, so it is rejected before the study is opened.

STUDY_IDENTITY_TRAINING_FIELDS = tuple(
    sorted(set(TRAINING_FIELDS) - OPERATIONAL_TRAINING_FIELDS)
) + ("resume",)


def resume_study_conflicts(
    requested: dict,
    requested_training: dict,
    training: dict,
    previous_config: dict,
) -> list[str]:
    """Names of the experiment-identity fields a study resumption changes.

    Only fields the client explicitly sent are compared; a field it omitted is
    taken from the previous job, so it can never count as a change.
    """
    from .job_studies import (
        opponents_config,
        pruner_config,
        sampler_config,
        search_space_config,
        verification_config,
    )

    def normalize(key: str, value: object) -> object:
        try:
            if key == "search_space":
                return search_space_config(value)
            if key == "opponents":
                return opponents_config(value)
            if key == "verification":
                return verification_config(value)
            if key == "sampler":
                return sampler_config(value)
            if key == "pruner":
                return pruner_config(value)
        except Exception:
            return value
        return value

    conflicts = [
        key
        for key in ("study_name", "seed", "search_space", "opponents", "sampler", "pruner")
        if key in requested
        and key in previous_config
        and normalize(key, requested[key]) != normalize(key, previous_config[key])
    ]
    previous_training = previous_config.get("training") or {}
    conflicts.extend(
        f"training.{key}"
        for key in STUDY_IDENTITY_TRAINING_FIELDS
        if key in requested_training
        and training.get(key) != previous_training.get(key)
        and not (
            key == "schedulefree_warmup_steps" and training.get("optimizer") != "adamw_schedulefree"
        )
    )
    return sorted(set(conflicts))


class VersionRequest(BaseModel):
    version: StrictInt = Field(ge=0)


class MoveRequest(VersionRequest):
    source: StrictInt = Field(ge=0)
    target: StrictInt = Field(ge=0)


class SeekRequest(VersionRequest):
    cursor: StrictInt = Field(ge=0)


class AnalysisRequest(SeekRequest):
    background: bool = False
    # Read-only history review analyzes any recorded cursor without seeking the
    # authoritative game; live gameplay keeps the exact-cursor contract.
    historical: bool = False


class AdvisorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="Move advisor", min_length=1, max_length=80)
    profile_id: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)


class AnalyticsRequest(BaseModel):
    advisor: AdvisorRequest | None = None
    time_limit: float = Field(default=5, ge=0.01, le=15, allow_inf_nan=False)
    show_win_chances: bool = True


class OpponentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # A named catalog preset, or None for an administrator-configured custom
    # opponent whose free-form ``config`` is validated exactly like a
    # tournament participant.
    profile_id: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)


class GameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ruleset: Literal["tablut", "brandubh", "copenhagen"] = "tablut"
    mode: Literal["human-ai", "ai-ai"] = "human-ai"
    human_side: Literal["attackers", "defenders"] = "defenders"
    players: dict[str, OpponentRequest] = Field(default_factory=dict)


class LocalGameRequest(BaseModel):
    """Same-device pass-and-play: two labelled human seats, no AI opponent."""

    model_config = ConfigDict(extra="forbid")
    ruleset: Literal["tablut", "brandubh", "copenhagen"] = "tablut"
    attackers_label: str = Field(default="Player 1", min_length=1, max_length=40)
    defenders_label: str = Field(default="Player 2", min_length=1, max_length=40)

    @field_validator("attackers_label", "defenders_label")
    @classmethod
    def seat_label(cls, value: str) -> str:
        # Labels are rendered verbatim next to the board, so control and bidi
        # formatting characters could spoof or garble the other seat's name.
        # Ordinary international letters and punctuation stay allowed.
        if any(unicodedata.category(char) == "Cc" or char in BIDI_CONTROLS for char in value):
            raise ValueError("Seat labels cannot contain control or bidirectional characters.")
        value = value.strip()
        if not value:
            raise ValueError("Both local seats need a label.")
        return value


class ModelNameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)


class TournamentPlayerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Legacy clients may still send aliases; canonical names always come from the catalog.
    name: str | None = Field(default=None, max_length=100)
    profile_id: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)


class TournamentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    # Missing mode keeps the historical balanced round-robin behaviour.
    mode: Literal["round_robin", "fixed_roles"] = "round_robin"
    rulesets: list[Literal["brandubh", "tablut", "copenhagen"]] = Field(
        default=["brandubh"], min_length=1, max_length=3
    )
    players: list[TournamentPlayerRequest] = Field(min_length=2, max_length=4)
    # A fixed-role comparison accepts 1–100 games; round-robin still requires an even count.
    games: StrictInt = Field(default=2, ge=1, le=100)
    max_plies: StrictInt = Field(default=300, ge=1, le=1000)
    seed: StrictInt = Field(default=0, ge=0, le=2**32 - 1)
    randomize_start_positions: StrictBool = False


def create_app(
    data_dir: str | Path | None = None,
    *,
    account_settings=None,
    mail_transport=None,
    account_clock=None,
    account_background=True,
    online_settings=None,
    online_clock=None,
    online_background=True,
) -> FastAPI:
    server_build = application_metadata()
    package_version = server_build["package_version"]
    app = FastAPI(
        title="Hnefatafl", version=package_version, docs_url=None, redoc_url=None, openapi_url=None
    )
    from .job_archives import JobUploadLimit

    archive_lock = threading.Lock()
    app.add_middleware(JobUploadLimit, archive_lock=archive_lock)
    root = Path(data_dir or os.environ.get("HNEFATAFL_DATA_DIR", "data")).resolve()
    root.mkdir(parents=True, exist_ok=True)
    (root / "jobs").mkdir(exist_ok=True)
    (root / "models").mkdir(exist_ok=True)
    store = Store(root / "hnefatafl.sqlite3")
    from .job_deletion import StudyDeletion

    study_deletion = StudyDeletion(root / "jobs", store)
    study_deletion.recover()
    app.state.study_deletion = study_deletion
    from .tournaments import TournamentManager

    tournaments = TournamentManager(store, root)
    app.state.store = store
    app.state.data_dir = root
    from .auth import attach_auth

    auth = attach_auth(app, store)
    from .account_api import attach_accounts

    account_options = {"settings": account_settings, "transport": mail_transport}
    if account_clock is not None:
        account_options["clock"] = account_clock
    registration = attach_accounts(app, auth, **account_options)
    from .profile_api import attach_profiles

    attach_profiles(app, auth, registration)
    from .online_api import attach_online

    online = attach_online(app, auth, settings=online_settings, clock=online_clock)
    processes: dict[str, subprocess.Popen] = {}
    job_lock = threading.RLock()
    # Catalog changes and new references must be atomic with respect to each
    # other in the documented single API-worker deployment.
    catalog_lock = threading.RLock()
    from .remote_training import RemoteTraining

    remote_training = RemoteTraining(store, root, job_lock, catalog_lock, server_build=server_build)
    auth.worker_auth = remote_training.authenticate
    remote_training.attach_routes(app)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if auth.settings.production:
            if not (PROJECT_ROOT / "frontend/dist/index.html").is_file():
                raise RuntimeError("Build the production frontend before startup")
            with store.connection() as connection:
                if not connection.execute(
                    "SELECT 1 FROM accounts WHERE role='ADMIN' AND active=1"
                ).fetchone():
                    raise RuntimeError(
                        "Provision the initial administrator using the local CLI before startup"
                    )
        if account_background:
            registration.start()
        if online_background:
            await asyncio.to_thread(online.sweep)
            online.start()
        try:
            yield
        finally:
            await asyncio.to_thread(online.shutdown)
            await asyncio.to_thread(registration.shutdown)
        await asyncio.to_thread(tournaments.shutdown)
        await asyncio.to_thread(analyses.shutdown)
        # A server shutdown stops workers it started, preserving completed
        # checkpoints. Restored tasks must explicitly resume retained files.
        for identifier, process in list(processes.items()):
            if process.poll() is not None:
                continue
            job = store.get_job(identifier)
            if job and job["status"] in {"running", "cancelling"}:
                job["status"] = "cancelling"
                store.save_job(job)
            try:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    await asyncio.to_thread(process.wait, timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    await asyncio.to_thread(process.wait, timeout=5)
            except ProcessLookupError:
                pass
            read_job(identifier)

    app.router.lifespan_context = lifespan

    def fail(message: str, status: int = 400) -> NoReturn:
        raise HTTPException(status, message)

    @app.exception_handler(TrainingFieldsRejected)
    async def _training_fields_rejected(_request, error: TrainingFieldsRejected):
        # Keep `detail` a string for existing consumers; add the rejected fields
        # so a client can point at the exact input instead of guessing.
        return JSONResponse(
            status_code=400,
            content={
                "detail": error.message,
                "code": error.code,
                "fields": error.fields,
            },
        )

    def game_or_404(game_id: str, version: int | None = None) -> dict:
        # All local analysis/AI/seek/export adapters use this guard. Separate
        # online tables also keep collections, tournaments and training isolated.
        if store.online_game_exists(game_id):
            fail("Online games require the dedicated read-only game/replay adapters.", 409)
        game = store.get_game(game_id)
        if game is None:
            fail("Game not found.", 404)
        if version is not None and game["version"] != version:
            fail("This game changed. Reload it before moving again.", 409)
        return game

    def require_local_owner(game: dict) -> None:
        """Local pass-and-play mutations belong to the creating account only.

        Administrators retain read-only access for support; they never move a
        local game on someone else's behalf. Non-owners already fail owner scope.
        """
        if game.get("mode") != "local":
            return
        from .security_context import principal

        if store.game_owner(game["id"]) != (principal.get() or {}).get("id"):
            fail("Only the creating account may change this local game.", 403)

    def state_of(game: dict) -> State:
        return State.from_dict(game["state"])

    def state_at(game: dict, cursor: int) -> State:
        state = new_game(game["ruleset"])
        for recorded in game["moves"][:cursor]:
            state = apply_move(state, Move(recorded["source"], recorded["target"]))
        return state

    def position_key(state: State) -> str:
        # Repetition history is part of the position's rule semantics. Including
        # it also prevents an undo/new branch from reusing another line's report.
        encoded = json.dumps(state.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode()).hexdigest()

    def analytics_settings(game: dict) -> dict:
        return store.analytics(game["id"]) or {
            "advisor": game.get("advisor"),
            "time_limit": 5,
            "show_win_chances": True,
            "revision": 0,
        }

    def referenced_agents(game: dict) -> list[dict]:
        advisor = analytics_settings(game)["advisor"]
        return [*game["players"].values(), *([advisor] if advisor else [])]

    def missing_analysis(
        game: dict, *, state: State | None = None, cursor: int | None = None
    ) -> dict:
        state = state or state_of(game)
        return {
            "cursor": game["cursor"] if cursor is None else cursor,
            "ply": state.ply,
            "side_to_move": state.turn,
            "position_key": position_key(state),
            "created_at": None,
            "evaluations": [],
            "hint": None,
            "unavailable_reason": "not_recorded",
        }

    def recorded_outcome(game: dict) -> dict:
        """Only the stored result completes a record, independent of replay cursor."""
        summary = game.get("recorded_result")
        if isinstance(summary, dict):
            return summary
        return {"winner": None, "reason": None, "plies": len(game["moves"])}

    def agent_rune(agent: dict) -> str:
        return store.rune(agent_identity(agent))

    def with_agent_rune(agent: dict) -> dict:
        return {**agent, "rune": agent_rune(agent)}

    def analysis_response(analysis: dict | None) -> dict | None:
        if analysis is None:
            return None
        return {
            **analysis,
            "evaluations": [with_agent_rune(entry) for entry in analysis["evaluations"]],
        }

    def resolved_players(games: list[dict]) -> dict[str, dict]:
        identifiers = [
            game["id"]
            for game in games
            if any(
                player.get("kind") == "human" and player.get("account_id")
                for player in game["players"].values()
            )
        ]
        owners = {}
        if identifiers:
            with store.connection() as c:
                owners = game_accounts(c, identifiers)
        return {game["id"]: game_players(game, owners) for game in games}

    def response(game: dict, players: dict | None = None) -> dict:
        state = state_of(game)
        start = new_game(game["ruleset"])
        result = {key: value for key, value in game.items() if key not in ("state", "creation")}
        from .ai import ai_profiles

        profiles = {profile["id"]: profile["name"] for profile in ai_profiles()}
        retired_names = {model["id"]: model["name"] for model in store.historical_models()}
        result["players"] = {}
        players = players if players is not None else resolved_players([game])[game["id"]]
        for side, recorded_player in players.items():
            player = dict(recorded_player)
            if player["kind"] == "ai":
                player["config"] = effective_ai_config(
                    player.get("config", {}), player.get("profile_id")
                )
                actual_profile = player["config"]["algorithm"]
                player["profile_name"] = (
                    player.get("profile_name")
                    if player.get("profile_id") == actual_profile
                    else None
                ) or profiles.get(actual_profile, actual_profile)
                player["profile_id"] = actual_profile
                player["variant_id"] = ai_variant_id(player["config"])
                model_id = player["config"].get("model_id")
                if model_id and not player.get("model_name") and model_id in retired_names:
                    player["model_name"] = retired_names[model_id]
            result["players"][side] = with_agent_rune(player)
        if game.get("advisor"):
            result["advisor"] = with_agent_rune(game["advisor"])
        result.update(
            size=state.size,
            board=list(state.board),
            turn=state.turn,
            ply=state.ply,
            winner=state.winner,
            reason=state.reason,
            total_moves=len(game["moves"]),
            recorded_winner=recorded_outcome(game)["winner"],
            recorded_reason=recorded_outcome(game)["reason"],
            recorded_plies=recorded_outcome(game)["plies"],
            legal_moves=[move.to_dict() for move in legal_moves(state)],
            captured={
                "attackers": start.board.count("A") - state.board.count("A"),
                "defenders": sum(start.board.count(p) - state.board.count(p) for p in ("D", "K")),
            },
            analysis=analysis_response(current_analysis(game)),
            analytics=analytics_settings(game),
        )
        return result

    def persist(game: dict, version: int, analysis: dict | None = None) -> dict:
        try:
            store.update_game(game, version, analysis)
        except ConflictError as exc:
            fail(str(exc), 409)
        return response(game)

    def registry():
        from .models import ModelRegistry

        return ModelRegistry(root / "models")

    def clean_ai_config(config: dict | None, profile_id: str | None = None) -> dict:
        from .ai import resolve_ai_config

        try:
            resolved = resolve_ai_config(config, profile_id)
        except ValueError as error:
            fail(str(error))
        if resolved.get("model_id"):
            registered_model(resolved["model_id"])
        return resolved

    def normalise_advisor(provided: AdvisorRequest | None, ruleset: str, seed: int) -> dict | None:
        if provided is None:
            return None
        from .ai import ai_profiles
        from .analysis_service import advisor_config

        name = provided.name.strip()
        if not name:
            fail("The move advisor needs a name.")
        config = clean_ai_config(
            advisor_config(provided.config, provided.profile_id), provided.profile_id
        )
        config["seed"] = seed
        profile = next(p for p in ai_profiles() if p["algorithm"] == config["algorithm"])
        result = {
            "name": name,
            "kind": "ai",
            "profile_id": profile["id"] if provided.profile_id else None,
            "profile_name": profile["name"],
            "config": config,
        }
        if config.get("model_id"):
            model = model_info(config["model_id"])
            if model["ruleset"] != ruleset:
                fail("This model is for a different ruleset.")
            model_file(model)
            result["model_name"] = model["name"]
        return with_agent_rune(result)

    def append_move(game: dict, move: Move, version: int, analysis: dict | None = None) -> dict:
        with catalog_lock:
            if game["cursor"] < len(game["moves"]):
                fail("Past positions are read-only. Return to the live board to play.", 409)
            before = state_of(game)
            try:
                after = apply_move(before, move)
            except (ValueError, IndexError) as exc:
                fail(str(exc))
            enemy = {"D", "K"} if before.turn == "attackers" else {"A"}
            captured = [
                {"square": i, "piece": piece}
                for i, piece in enumerate(before.board)
                if piece in enemy and after.board[i] != piece
            ]

            def coordinate(square: int) -> str:
                row, col = divmod(square, before.size)
                return f"{chr(97 + col)}{before.size - row}"

            record = {
                "source": move.source,
                "target": move.target,
                "side": before.turn,
                "captured": captured,
                "notation": f"{coordinate(move.source)} → {coordinate(move.target)}",
            }
            game["moves"] = game["moves"][: game["cursor"]] + [record]
            game["cursor"] += 1
            game["state"] = after.to_dict()
            game["ended_at"] = now() if after.winner else None
            game["recorded_result"] = (
                {
                    "winner": after.winner,
                    "reason": after.reason,
                    "plies": after.ply,
                    "ended_at": game["ended_at"],
                }
                if after.winner
                else None
            )
            return persist(game, version, analysis)

    @app.get("/api/health")
    def health():
        with store.connection() as connection:
            connection.execute("SELECT 1 FROM accounts LIMIT 1")
        return {"status": "ok"}

    @app.get("/api/rulesets")
    def rulesets():
        result = []
        for identifier, rule in sorted(RULESETS.items(), key=lambda item: item[1].size):
            document = PROJECT_ROOT / "docs" / "rules" / f"{identifier}.md"
            text = document.read_text() if document.exists() else ""
            result.append(
                {
                    "id": identifier,
                    "name": rule.name,
                    "size": rule.size,
                    "description": {
                        "tablut": "The 9×9 king's escape to the edge.",
                        "brandubh": "A compact 7×7 battle for the four corners.",
                        "copenhagen": "An 11×11 siege with shield walls and edge forts.",
                    }[identifier],
                    "rules_markdown": text,
                }
            )
        return result

    @app.get("/api/ai/profiles")
    def ai_profiles_route():
        from .ai import ai_profiles

        return [
            with_agent_rune(
                {**profile, "config": {**profile["parameters"], "algorithm": profile["algorithm"]}}
            )
            for profile in ai_profiles()
        ]

    # Registered-model metadata retained beside a participant for provenance.
    # Server paths and checkpoints never leave the process; the presentation
    # rune is reserved separately by the caller that stores it.
    MODEL_PROVENANCE_FIELDS = frozenset(
        {
            "id",
            "name",
            "ruleset",
            "version",
            "iteration",
            "created_at",
            "architecture",
            "self_play_games",
            "metrics",
            "evaluation",
        }
    )

    def registered_model(model_id: str) -> dict:
        """Resolve a registry entry without reserving a presentation rune.

        ``model_info`` also allocates a rune, which needs its own write
        transaction; this lookup stays safe inside a game-creation transaction.
        """
        try:
            return registry().get(model_id)
        except (ValueError, OSError):
            fail("Model not found.", 404)

    def resolve_ai_participant(
        provided: OpponentRequest | TournamentPlayerRequest, *, rulesets, seed: int
    ) -> dict:
        """Resolve one tournament-style participant into a stored AI player snapshot.

        Shared by tournament creation and single-player custom opponents so both
        accept the identical configuration surface and validation. A named
        catalog preset is authoritative: its recorded parameters replace any
        configuration supplied beside it. This performs no database work, so it
        is safe inside a creation transaction; a caller that persists the
        presentation rune reserves it itself.
        """
        from .ai import FIXED_PROFILE_IDS, ai_profiles

        profiles = {item["algorithm"]: item for item in ai_profiles()}
        config = dict(provided.config or {})
        if provided.profile_id in FIXED_PROFILE_IDS:
            preset = profiles[provided.profile_id]
            config = {**preset["parameters"], "algorithm": preset["algorithm"]}
        elif provided.profile_id and provided.profile_id not in profiles:
            fail("Unknown AI profile.")
        config = clean_ai_config(config, provided.profile_id)
        config["seed"] = seed
        profile = profiles[config["algorithm"]]
        snapshot = {
            "name": profile["name"],
            "profile_id": profile["id"],
            "profile_name": profile["name"],
            "config": config,
            "variant_id": ai_variant_id(config),
        }
        if config.get("model_id"):
            model = registered_model(config["model_id"])
            if any(ruleset != model["ruleset"] for ruleset in rulesets):
                fail(
                    "A selected model must match the battlefield."
                    if len(rulesets) == 1
                    else "A selected model must match every tournament battlefield."
                )
            model_file(model)
            snapshot["name"] = snapshot["model_name"] = model["name"]
            snapshot["model"] = {
                key: value for key, value in model.items() if key in MODEL_PROVENANCE_FIELDS
            }
            if isinstance(model.get("training_config"), dict):
                snapshot["model"]["training_config"] = {
                    key: value
                    for key, value in model["training_config"].items()
                    if key in TRAINING_FIELDS - {"resume"}
                }
        return snapshot

    def insert_account_game(c, account, request: GameRequest, *, seed=None, allow_custom=False):
        from .game_records import game_record
        from .security_context import allowed

        if request.mode == "ai-ai" and not allowed(account, "all_data"):
            fail("Administrator permission is required for AI vs AI.", 403)
        ai_sides = set(SIDES) if request.mode == "ai-ai" else set(SIDES) - {request.human_side}
        if set(request.players) - ai_sides:
            fail("Human identity comes from the authenticated account.", 422)
        # A custom configuration is an administrator-only resource. Restarting an
        # owned game revalidates the stored request without re-asking for the role.
        if any(player.config for player in request.players.values()) and not (
            allow_custom or allowed(account, "all_data")
        ):
            fail("Administrator permission is required for a custom opponent.", 403)
        seed = secrets.randbits(32) if seed is None else seed
        players = {}
        for side in SIDES:
            if side not in ai_sides:
                players[side] = {
                    "name": account["username"],
                    "account_id": account["id"],
                    "kind": "human",
                    "config": {},
                }
                continue
            provided = request.players.get(side) or OpponentRequest(profile_id="baseline")
            if not provided.profile_id and not provided.config:
                provided = OpponentRequest(profile_id="baseline")
            snapshot = resolve_ai_participant(provided, rulesets=[request.ruleset], seed=seed)
            players[side] = {
                "name": snapshot["name"],
                "kind": "ai",
                "profile_id": snapshot["profile_id"],
                "profile_name": snapshot["profile_name"],
                "config": snapshot["config"],
                "variant_id": snapshot["variant_id"],
            }
            if snapshot.get("model_name"):
                players[side]["model_name"] = snapshot["model_name"]
        game = game_record(request.ruleset, request.mode, request.human_side, players, seed)
        game["creation_policy"] = 1
        # Retain the exact creation request so a restart reproduces a custom
        # opponent instead of silently reverting to a named preset.
        game["creation"] = request.model_dump()
        c.execute(
            "INSERT INTO games(id,version,created_at,data,owner_account_id) VALUES(?,?,?,?,?)",
            (game["id"], 0, game["created_at"], json.dumps(game), account["id"]),
        )
        return game

    def insert_local_game(c, account, request: LocalGameRequest):
        """Create an account-owned same-device game with two labelled human seats.

        No seat carries an account_id: both sides belong to the one signed-in
        account, so the existing owner scope plus the explicit local owner guard
        authorise every move. The category keeps it out of single-player stats.
        """
        from .game_records import game_record

        labels = {
            "attackers": request.attackers_label.strip(),
            "defenders": request.defenders_label.strip(),
        }
        if not labels["attackers"] or not labels["defenders"]:
            fail("Both local seats need a label.", 422)
        players = {
            side: {"name": labels[side], "label": labels[side], "kind": "human", "config": {}}
            for side in SIDES
        }
        seed = secrets.randbits(32)
        game = game_record(request.ruleset, "local", "attackers", players, seed)
        # Local play has no AI opponent, so it never runs or records analysis.
        game["analysis_enabled"] = False
        game["category"] = "local"
        game["creation_policy"] = 2
        game["creation"] = request.model_dump()
        c.execute(
            "INSERT INTO games(id,version,created_at,data,owner_account_id,category) "
            "VALUES(?,?,?,?,?,?)",
            (game["id"], 0, game["created_at"], json.dumps(game), account["id"], "local"),
        )
        return game

    def restart_setup(prior: dict) -> GameRequest:
        """Rebuild the original creation request, falling back for legacy records."""
        stored = prior.get("creation")
        if isinstance(stored, dict):
            try:
                return GameRequest.model_validate(stored)
            except ValueError:
                pass
        return GameRequest(
            ruleset=prior["ruleset"],
            mode=prior["mode"],
            human_side=prior["human_side"],
            players={
                side: OpponentRequest(profile_id=player["profile_id"])
                for side, player in prior["players"].items()
                if player["kind"] == "ai"
            },
        )

    def account_creator(c):
        from .online_core import OnlineError
        from .security_context import session_identity

        try:
            return online._identity(c, session_identity.get())
        except OnlineError as error:
            fail(str(error), error.status)

    @app.post("/api/games", status_code=201)
    def create_game(request: GameRequest):
        with catalog_lock, store.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            account = account_creator(c)
            game = insert_account_game(c, account, request)
        return response(game)

    @app.post("/api/games/local", status_code=201)
    def create_local_game(request: LocalGameRequest):
        with catalog_lock, store.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            account = account_creator(c)
            game = insert_local_game(c, account, request)
        return response(game)

    @app.get("/api/games")
    def games(ruleset: str | None = None, mode: str | None = None, kind: str | None = None):
        if kind not in (None, "human", "ai"):
            fail("Kind must be human or ai.")
        selected = [
            g
            for g in store.list_games(category="account-single-player")
            # Ordinary account games are exactly the persisted single-player
            # category; local pass-and-play and any future category never appear
            # in the single-player/vs-AI launcher or statistics.
            if (not ruleset or g["ruleset"] == ruleset)
            and (not mode or g["mode"] == mode)
            and (not kind or any(player["kind"] == kind for player in g["players"].values()))
        ]
        players = resolved_players(selected)
        return [response(g, players[g["id"]]) for g in selected]

    @app.get("/api/games/{game_id}")
    def get_game(game_id: str):
        return response(game_or_404(game_id))

    def position_snapshot(
        game: dict, *, state: State | None = None, cursor: int | None = None
    ) -> dict:
        state = state or state_of(game)
        return {
            "cursor": game["cursor"] if cursor is None else cursor,
            "ply": state.ply,
            "side_to_move": state.turn,
            "position_key": position_key(state),
            "created_at": now(),
            "evaluations": [],
            "hint": None,
        }

    def current_analysis(
        game: dict, settings: dict | None = None, *, state: State | None = None
    ) -> dict | None:
        settings = settings or analytics_settings(game)
        key = position_key(state or state_of(game))
        recorded = store.game_analysis(game["id"], key)
        advisor = store.advisor_analysis(game["id"], key, settings["revision"])
        # Before the first preference change, legacy advisor reports keep their
        # original representation and remain usable without model weights.
        if store.analytics(game["id"]) is None and advisor is None:
            return recorded
        if recorded is None and advisor is None:
            return None
        result = dict(recorded or advisor or {})
        result["evaluations"] = [
            entry
            for entry in (recorded or {}).get("evaluations", [])
            if entry["agent_id"] != "advisor"
        ]
        result["hint"] = None
        if settings["advisor"] and advisor:
            result["evaluations"].extend(advisor["evaluations"])
            result["hint"] = advisor["hint"]
        result.pop("partial", None)
        return result

    def evaluation_entry(
        game: dict,
        role: str,
        agent: dict,
        settings: dict,
        report: dict | None = None,
        *,
        state: State | None = None,
    ) -> dict:
        state = state or state_of(game)
        from .analysis_service import advisor_config

        raw = (
            advisor_config(agent["config"], agent.get("profile_id"))
            if role == "advisor"
            else agent["config"]
        )
        configuration = effective_ai_config(raw, agent.get("profile_id"))
        if role == "advisor":
            configuration["time_limit"] = settings["time_limit"]
        entry = {
            "agent_id": role,
            "role": role,
            "name": agent["name"],
            "rune": agent_rune(agent),
            "profile_id": agent.get("profile_id"),
            "profile_name": agent.get("profile_name"),
            "model_name": agent.get("model_name"),
            "config": configuration,
            "source": "unavailable",
            "win_probability": None,
            "estimated": not bool(state.winner),
            "calibrated": bool(state.winner),
        }
        if report is not None:
            entry.update(report)
            value = report["value"] if state.turn == "defenders" else -report["value"]
            if role != "advisor" or settings["show_win_chances"]:
                entry["win_probability"] = max(0.0, min(1.0, (value + 1.0) / 2.0))
            else:
                # Move selection needs search scores, but probability conversion
                # and publication are optional and never require a second search.
                entry.pop("value", None)
            entry["search_seed"] = (
                (configuration["seed"] + state.ply) % 2**32
                if "seed" in game
                else configuration["seed"]
            )
        return entry

    def evaluate_background(
        game: dict, role: str, agent: dict, settings: dict, progress, *, state: State | None = None
    ):
        from .ai import analyze_position

        state = state or state_of(game)
        entry = evaluation_entry(game, role, agent, settings, state=state)
        configuration = dict(entry["config"])
        if "seed" in game:
            configuration["seed"] = (configuration["seed"] + state.ply) % 2**32
        try:
            if configuration.get("model_id") and not state.winner:
                with catalog_lock:
                    available = model_info(configuration["model_id"])
                    configuration["model_path"] = str(model_file(available))
            report = analyze_position(
                state,
                configuration,
                progress=lambda partial: progress(
                    evaluation_entry(game, role, agent, settings, partial, state=state)
                ),
            )
            return evaluation_entry(game, role, agent, settings, report, state=state)
        except (OSError, HTTPException):
            entry["unavailable_reason"] = "model_unavailable"
        except (ValueError, RuntimeError):
            entry["unavailable_reason"] = "evaluation_failed"
        return entry

    from .analysis_service import AnalysisService, AnalysisUnavailable, SupersededAnalysis

    analyses = AnalysisService(store, evaluate_background)
    app.state.analyses = analyses

    @app.exception_handler(AnalysisUnavailable)
    async def _analysis_unavailable(_request, error: AnalysisUnavailable):
        return JSONResponse({"detail": str(error)}, status_code=503, headers={"Retry-After": "1"})

    @app.get("/api/games/{game_id}/analytics")
    def get_analytics(game_id: str):
        return analytics_settings(game_or_404(game_id))

    @app.put("/api/games/{game_id}/analytics")
    def set_analytics(game_id: str, request: AnalyticsRequest):
        with catalog_lock:
            game = game_or_404(game_id)
            if game.get("mode") == "local":
                fail("Local games have no move advisor.", 400)
            existing = analytics_settings(game)["advisor"]
            same_advisor = bool(
                existing
                and request.advisor
                and request.advisor.model_dump()
                == {
                    "name": existing["name"],
                    "profile_id": existing.get("profile_id"),
                    "config": existing["config"],
                }
            )
            # Completed histories may refer to removed weights. Visibility/time
            # preferences still remain editable without restoring that model.
            advisor = (
                existing
                if same_advisor
                else normalise_advisor(
                    request.advisor,
                    game["ruleset"],
                    game.get("seed", 0),
                )
            )
            try:
                return store.save_analytics(
                    game_id,
                    {
                        "advisor": advisor,
                        "time_limit": request.time_limit,
                        "show_win_chances": request.show_win_chances,
                    },
                )
            except ConflictError as exc:
                fail(str(exc), 409)

    def background_analysis(
        game: dict, *, state: State | None = None, cursor: int | None = None
    ) -> dict:
        state = state or state_of(game)
        settings = analytics_settings(game)
        snapshot = position_snapshot(game, state=state, cursor=cursor)
        recorded = current_analysis(game, settings, state=state)
        existing = {entry["agent_id"] for entry in (recorded or {}).get("evaluations", [])}
        tasks = []
        # Actual AI turns produce their own evidence in /ai-move. Historical
        # player evidence is never invented later using changed model weights.
        position = game["cursor"] if cursor is None else cursor
        frontier = position == len(game["moves"])
        human_turn = game["players"][state.turn]["kind"] == "human"
        if settings["show_win_chances"] and game.get("analysis_enabled") and frontier:
            for role, agent in game["players"].items():
                if (
                    agent["kind"] == "ai"
                    and role not in existing
                    and (human_turn or state.winner or role != state.turn)
                ):
                    tasks.append(
                        analyses.schedule(game, snapshot, role, agent, settings, state=state)
                    )
        if settings["advisor"]:
            advisor_report = store.advisor_analysis(
                game["id"], snapshot["position_key"], settings["revision"]
            )
            if (
                advisor_report is None
                and "advisor" in existing
                and store.analytics(game["id"]) is None
            ):
                pass  # An immutable legacy advisor report is already retained.
            elif advisor_report is None or advisor_report.get("partial"):
                tasks.append(
                    analyses.schedule(
                        game, snapshot, "advisor", settings["advisor"], settings, state=state
                    )
                )
        # Read activity before data: a worker can commit between these reads.
        # A stale "pending" only causes another poll; stale "complete" could
        # otherwise hide the newly committed result indefinitely.
        pending = any(analyses.active(key) for key in tasks)
        result = current_analysis(game, settings, state=state) or missing_analysis(
            game, state=state, cursor=cursor
        )
        rendered = analysis_response(
            {
                **result,
                "status": "pending" if pending else "complete",
                "advisor_revision": settings["revision"],
            }
        )
        assert rendered is not None
        return rendered

    @app.post("/api/games/{game_id}/analysis")
    def analyze_game(game_id: str, request: AnalysisRequest):
        from .ai import analyze_position

        game = game_or_404(game_id, request.version)
        if request.historical:
            if not 0 <= request.cursor <= len(game["moves"]):
                fail("The requested position is outside this recorded game.", 422)
            state = state_at(game, request.cursor)
            if request.background:
                return background_analysis(game, state=state, cursor=request.cursor)
            settings = analytics_settings(game)
            recorded = current_analysis(game, settings, state=state) or missing_analysis(
                game, state=state, cursor=request.cursor
            )
            return analysis_response(
                {
                    **recorded,
                    "cursor": request.cursor,
                    "status": "complete",
                    "advisor_revision": settings["revision"],
                }
            )
        if request.cursor != game["cursor"]:
            fail("This game changed. Reload it before analyzing again.", 409)
        if request.background:
            return background_analysis(game)
        state = state_of(game)
        key = position_key(state)
        settings = analytics_settings(game)
        saved_settings = store.analytics(game_id) is not None
        cached = store.game_analysis(game_id, key)

        def retained_response() -> dict | None:
            result = current_analysis(game, settings) or missing_analysis(game)
            current = analytics_settings(game_or_404(game_id, request.version))
            if current["revision"] != settings["revision"]:
                fail("The move advisor changed. Retry analysis.", 409)
            if saved_settings:
                result = {**result, "advisor_revision": settings["revision"]}
            return analysis_response(result)

        # A recorded opinion belongs to the original point in play. Never
        # fabricate historical evidence by evaluating with newer/deleted weights.
        if game["cursor"] < len(game["moves"]) or (
            not game.get("analysis_enabled") and not saved_settings
        ):
            return retained_response()
        legacy_advisor = bool(
            not saved_settings
            and cached
            and any(entry["agent_id"] == "advisor" for entry in cached["evaluations"])
        )
        if settings["advisor"] and not legacy_advisor:
            try:
                analyses.wait(
                    game,
                    position_snapshot(game),
                    "advisor",
                    settings["advisor"],
                    settings,
                    state=state,
                )
            except SupersededAnalysis as exc:
                fail(str(exc), 409)
            # Preserve the synchronous API's optimistic version contract after
            # waiting. The worker still owns its independent retained-line guard.
            game_or_404(game_id, request.version)
        if cached is not None or not game.get("analysis_enabled"):
            return retained_response()
        snapshot = {
            "cursor": game["cursor"],
            "ply": state.ply,
            "side_to_move": state.turn,
            "position_key": key,
            "created_at": now(),
            "evaluations": [],
            "hint": None,
        }
        agents = [
            (side, player) for side, player in game["players"].items() if player["kind"] == "ai"
        ]
        for role, agent in agents:
            configuration = effective_ai_config(agent["config"], agent.get("profile_id"))
            entry = {
                "agent_id": role,
                "role": role,
                "name": agent["name"],
                "rune": agent_rune(agent),
                "profile_id": agent.get("profile_id"),
                "profile_name": agent.get("profile_name"),
                "model_name": agent.get("model_name"),
                "config": configuration,
                "source": "unavailable",
                "win_probability": None,
                "estimated": not bool(state.winner),
                "calibrated": bool(state.winner),
            }
            runtime_config = dict(configuration)
            if "seed" in game:
                runtime_config["seed"] = (configuration["seed"] + state.ply) % 2**32
            try:
                # An exact terminal value does not need model weights and stays
                # inspectable even if the completed game's model was removed.
                if runtime_config.get("model_id") and not state.winner:
                    with catalog_lock:
                        available = next(
                            (m for m in models_list() if m["id"] == runtime_config["model_id"]),
                            None,
                        )
                        if available is None:
                            raise FileNotFoundError("The recorded model is unavailable")
                        runtime_config["model_path"] = str(model_file(available))
                report = analyze_position(state, runtime_config)
                defender_value = report["value"]
                current_value = defender_value if state.turn == "defenders" else -defender_value
                entry.update(
                    report,
                    value=defender_value,
                    win_probability=max(0.0, min(1.0, (current_value + 1.0) / 2.0)),
                    search_seed=runtime_config["seed"],
                )
            except (OSError, HTTPException):
                entry["unavailable_reason"] = "model_unavailable"
            except (ValueError, RuntimeError):
                entry["unavailable_reason"] = "evaluation_failed"
            snapshot["evaluations"].append(entry)
        if not saved_settings:
            # Keep the original synchronous response/storage shape for clients
            # with a legacy game advisor, while sharing its bounded computation.
            advisor = store.advisor_analysis(game_id, key, settings["revision"])
            if advisor:
                snapshot["evaluations"].extend(advisor["evaluations"])
                snapshot["hint"] = advisor["hint"]
        try:
            store.save_game_analysis(game_id, request.version, snapshot)
            return retained_response()
        except ConflictError as exc:
            fail(str(exc), 409)

    def online_analysis_game(identifier: str) -> dict:
        """Read-only analysis view of a completed online game.

        Only terminal games are eligible. Ongoing online games never expose
        analysis, suggestions, evaluations or win probabilities, and a client
        countdown reaching zero is never treated as completion.
        """
        from .online_core import OnlineError
        from .security_context import allowed, session_identity

        # The requested online game id must stay distinct from the participant
        # account ids read below, or the move query and returned game object end
        # up keyed by an account id instead of the game.
        game_id = identifier
        try:
            with store.connection() as c:
                c.execute("BEGIN")
                account = online._identity(c, session_identity.get())
                if not allowed(account, "all_data"):
                    online._participant(c, account["id"], game_id)
                row, game = online._load(c, game_id)
                if game.status.value != "completed" or game.result is None:
                    fail("Analysis is available only for completed online games.", 403)
                players = {}
                accounts = resolve_accounts(c, (row[side + "_id"] for side in SIDES))
                for side in SIDES:
                    account_id = row[side + "_id"]
                    players[side] = {
                        **accounts.get(account_id, public_identity(None)),
                        "account_id": account_id,
                        "kind": "human",
                        "config": {},
                    }
                moves = [
                    {"source": item["source"], "target": item["target"]}
                    for item in c.execute(
                        "SELECT source,target FROM online_moves WHERE game_id=? ORDER BY ply",
                        (game_id,),
                    )
                ]
        except OnlineError as error:
            fail(str(error), error.status)
        return {
            "id": game_id,
            "online": True,
            "ruleset": row["ruleset"],
            "mode": "human-human",
            "players": players,
            "moves": moves,
            "cursor": len(moves),
            "state": game.position.to_dict(),
            "analysis_enabled": True,
            "advisor": None,
            "recorded_result": {
                "winner": game.result.winner,
                "reason": game.result.termination_reason,
                "plies": len(moves),
            },
        }

    @app.get("/api/history/online/{game_id}/analytics")
    def online_analytics(game_id: str):
        return analytics_settings(online_analysis_game(game_id))

    @app.put("/api/history/online/{game_id}/analytics")
    def set_online_analytics(game_id: str, request: AnalyticsRequest):
        with catalog_lock:
            game = online_analysis_game(game_id)
            existing = analytics_settings(game)["advisor"]
            same_advisor = bool(
                existing
                and request.advisor
                and request.advisor.model_dump()
                == {
                    "name": existing["name"],
                    "profile_id": existing.get("profile_id"),
                    "config": existing["config"],
                }
            )
            advisor = (
                existing if same_advisor else normalise_advisor(request.advisor, game["ruleset"], 0)
            )
            try:
                return store.save_analytics(
                    game_id,
                    {
                        "advisor": advisor,
                        "time_limit": request.time_limit,
                        "show_win_chances": request.show_win_chances,
                    },
                    allow_online=True,
                )
            except ConflictError as exc:
                fail(str(exc), 409)

    @app.post("/api/history/online/{game_id}/analysis")
    def analyze_online_game(game_id: str, request: AnalysisRequest):
        game = online_analysis_game(game_id)
        if not 0 <= request.cursor <= len(game["moves"]):
            fail("The requested position is outside this recorded game.", 422)
        state = state_at(game, request.cursor)
        if request.background:
            return background_analysis(game, state=state, cursor=request.cursor)
        settings = analytics_settings(game)
        return analysis_response(
            current_analysis(game, settings, state=state)
            or missing_analysis(game, state=state, cursor=request.cursor)
        )

    @app.delete("/api/games/{game_id}")
    def delete_game(
        game_id: str, version: str = Query(pattern=r"^(0|[1-9][0-9]*)$", max_length=16)
    ):
        with catalog_lock:
            expected_version = int(version)
            require_local_owner(game_or_404(game_id, expected_version))
            try:
                store.delete_game(game_id, expected_version)
            except ConflictError as exc:
                fail(str(exc), 409)
            return {"deleted": True, "id": game_id}

    @app.get("/api/games/{game_id}/replay")
    def game_replay(game_id: str):
        return validated_account_replay(game_or_404(game_id))

    def validated_account_replay(game: dict):
        state = new_game(game["ruleset"])
        states = [state.to_dict()]
        try:
            for recorded in game["moves"]:
                state = apply_move(state, Move(recorded["source"], recorded["target"]))
                states.append(state.to_dict())
        except ValueError:
            fail("Stored game history failed deterministic validation.", 409)
        if states[game["cursor"]] != game["state"]:
            fail("Stored position does not match the recorded moves.", 409)
        return {
            "game_id": game["id"],
            "validated": True,
            "cursor": game["cursor"],
            "states": states,
        }

    from .personal_api import attach_personal

    attach_personal(app, online, replay=validated_account_replay)

    @app.post("/api/games/{game_id}/moves")
    def make_move(game_id: str, request: MoveRequest):
        from .security_context import principal

        game = game_or_404(game_id, request.version)
        require_local_owner(game)
        player = game["players"][state_of(game).turn]
        if player["kind"] != "human":
            fail("It is the computer's turn.", 409)
        if player.get("account_id") and player["account_id"] != (principal.get() or {}).get("id"):
            fail("Only the player whose turn it is may move.", 403)
        return append_move(game, Move(request.source, request.target), request.version)

    @app.post("/api/games/{game_id}/ai-move")
    def ai_move(game_id: str, request: VersionRequest):
        from .ai import analysis_from_search, choose_move

        game = game_or_404(game_id, request.version)
        if game["cursor"] < len(game["moves"]):
            fail("Past positions are read-only. Return to the live board to play.", 409)
        state = state_of(game)
        player = game["players"][state.turn]
        if player["kind"] != "ai":
            fail("It is a human player's turn.", 409)
        if state.winner:
            fail("The game is already finished.")
        config = clean_ai_config(player["config"], player["profile_id"])
        if "seed" in game:
            config["seed"] = (config["seed"] + state.ply) % 2**32
        if config.get("model_id"):
            model = registered_model(config.pop("model_id"))
            if model.get("ruleset") != game["ruleset"]:
                fail("This model is for a different ruleset.")
            config["model_path"] = str(model_file(model))
        try:
            details: dict = {}
            move = choose_move(state, config, diagnostics=details)
            searched = analysis_from_search(state, config, move, details)
            entry = evaluation_entry(game, state.turn, player, analytics_settings(game), searched)
            snapshot = {**position_snapshot(game), "evaluations": [entry]}
        except (ValueError, FileNotFoundError) as exc:
            fail(str(exc))
        return append_move(game, move, request.version, snapshot)

    @app.post("/api/games/{game_id}/seek")
    def seek(game_id: str, request: SeekRequest):
        game = game_or_404(game_id, request.version)
        require_local_owner(game)
        if request.cursor > len(game["moves"]):
            fail("This move is outside the recorded game.")
        state = state_at(game, request.cursor)
        game["cursor"] = request.cursor
        game["state"] = state.to_dict()
        return persist(game, request.version)

    @app.post("/api/games/{game_id}/undo")
    def undo(game_id: str, request: VersionRequest):
        game = game_or_404(game_id, request.version)
        require_local_owner(game)
        if not game["cursor"]:
            fail("There is no move to undo.")
        return seek(game_id, SeekRequest(version=request.version, cursor=game["cursor"] - 1))

    @app.post("/api/games/{game_id}/redo")
    def redo(game_id: str, request: VersionRequest):
        game = game_or_404(game_id, request.version)
        require_local_owner(game)
        if game["cursor"] >= len(game["moves"]):
            fail("There is no move to redo.")
        return seek(game_id, SeekRequest(version=request.version, cursor=game["cursor"] + 1))

    @app.post("/api/games/{game_id}/restart")
    def restart(game_id: str, request: VersionRequest):
        with catalog_lock:
            prior = game_or_404(game_id, request.version)
            require_local_owner(prior)
            if recorded_outcome(prior)["winner"] or state_of(prior).winner:
                fail("Completed games cannot be restarted. Start a new game instead.", 409)
            if prior.get("mode") == "local":
                # Restart keeps the two local seat labels and starts a fresh
                # record; local games never copy advisor/AI preferences.
                local_setup = LocalGameRequest.model_validate(prior["creation"])
                with store.connection() as c:
                    c.execute("BEGIN IMMEDIATE")
                    account = account_creator(c)
                    if not c.execute(
                        "SELECT 1 FROM games WHERE id=? AND version=? AND owner_account_id=?",
                        (game_id, request.version, account["id"]),
                    ).fetchone():
                        fail("The game changed. Reload before restarting.", 409)
                    game = insert_local_game(c, account, local_setup)
                return response(game)
            if prior.get("creation_policy") != 1:
                fail(
                    "Start a new account game with the supported opponents; this saved setup is read/play compatible only.",
                    409,
                )
            setup = restart_setup(prior)
            settings = analytics_settings(prior)
            with store.connection() as c:
                c.execute("BEGIN IMMEDIATE")
                account = account_creator(c)
                from .security_context import allowed

                if not c.execute(
                    "SELECT 1 FROM games WHERE id=? AND version=? AND (? OR owner_account_id=?)",
                    (game_id, request.version, allowed(account, "all_data"), account["id"]),
                ).fetchone():
                    fail("The game changed. Reload before restarting.", 409)
                seed = secrets.randbits(32)
                if seed == prior.get("seed"):
                    seed = (seed + 1) % 2**32
                game = insert_account_game(c, account, setup, seed=seed, allow_custom=True)
            preferences = {
                key: settings[key] for key in ("advisor", "time_limit", "show_win_chances")
            }
            if preferences["advisor"]:
                preferences["advisor"] = {
                    **preferences["advisor"],
                    "config": {**preferences["advisor"]["config"], "seed": seed},
                }
            store.save_analytics(game["id"], preferences)
            return response(game)

    @app.get("/api/stats")
    def stats(
        ruleset: str | None = None,
        mode: str | None = None,
        kind: str | None = None,
        player_id: str | None = None,
    ):
        if kind not in (None, "human", "ai"):
            fail("Kind must be human or ai.")
        selected = [
            g
            for g in store.list_games(category="account-single-player")
            # Local pass-and-play and future categories must never mix into vs-AI
            # or account single-player statistics; the positive category filter
            # keeps the lookup on the indexed column before deserialization.
            if (not ruleset or g["ruleset"] == ruleset)
            and (not mode or g["mode"] == mode)
            and (not kind or any(p["kind"] == kind for p in g["players"].values()))
            and (
                not player_id
                or any(
                    p.get("player_id") == player_id
                    or "account:" + str(p.get("account_id")) == player_id
                    for p in g["players"].values()
                )
            )
        ]
        completed = [g for g in selected if recorded_outcome(g)["winner"]]
        players = resolved_players(selected)
        by_ruleset: dict[str, dict] = {}
        by_player: dict[str, dict] = {}
        from .ai import ai_profiles

        profile_names = {profile["id"]: profile["name"] for profile in ai_profiles()}
        model_names = {model["id"]: model["name"] for model in store.historical_models()}
        deleted_players = {
            player["id"] for player in store.players(include_deleted=True) if player["deleted_at"]
        }
        if any(
            player.get("config", {}).get("model_id")
            for game in completed
            for player in game["players"].values()
        ):
            try:
                model_names.update(
                    {
                        model["id"]: model["name"]
                        for model in models_list()
                        if isinstance(model.get("name"), str)
                    }
                )
            except (ValueError, OSError):
                # Historical results still work when a registry was removed or
                # damaged; its stable model ID remains an unambiguous label.
                pass
        for game in completed:
            outcome = recorded_outcome(game)
            summary = by_ruleset.setdefault(
                game["ruleset"],
                {
                    "ruleset": game["ruleset"],
                    "games": 0,
                    "wins": 0,
                    "draws": 0,
                    "attackers_wins": 0,
                    "defenders_wins": 0,
                    "average_game_length": 0,
                    "_plies": 0,
                },
            )
            summary["games"] += 1
            summary["_plies"] += outcome["plies"]
            if outcome["winner"] == "draw":
                summary["draws"] += 1
            else:
                summary["wins"] += 1
                summary[f"{outcome['winner']}_wins"] += 1
            for side, player in players[game["id"]].items():
                if kind and player["kind"] != kind:
                    continue
                config = (
                    effective_ai_config(player.get("config", {}), player.get("profile_id"))
                    if player["kind"] == "ai"
                    else None
                )
                experiment_seed = config.pop("seed", None) if config is not None else None
                identifier = (
                    (
                        "account:" + player["account_id"]
                        if player.get("account_id")
                        else player.get("player_id")
                    )
                    if player["kind"] == "human"
                    else ai_variant_id(config or {})
                )
                identifier = str(identifier)
                if player_id and identifier != player_id:
                    continue
                profile_id = config["algorithm"] if config is not None else None
                model_id = config.get("model_id") if config is not None else None
                profile_name = (
                    (player.get("profile_name") if player.get("profile_id") == profile_id else None)
                    or profile_names.get(profile_id, profile_id)
                    if profile_id
                    else None
                )
                model_name = (
                    player.get("model_name") or model_names.get(model_id, f"Model {model_id}")
                    if model_id
                    else None
                )
                opponent_name = (
                    " · ".join(
                        label
                        for label in (
                            profile_name,
                            f"{model_name} [{model_id[:8]}]" if model_id else None,
                        )
                        if label
                    )
                    if player["kind"] == "ai"
                    else None
                )
                item = by_player.setdefault(
                    identifier,
                    {
                        "id": identifier,
                        "name": player["name"],
                        **({"username": player["username"]} if player.get("username") else {}),
                        "rune": agent_rune({**player, "config": config or {}}),
                        "kind": player["kind"],
                        "profile_id": profile_id,
                        "model_id": model_id,
                        "profile_name": profile_name,
                        "model_name": model_name,
                        "opponent_name": opponent_name,
                        "config": config,
                        "variant_id": identifier if config is not None else None,
                        "deleted": identifier in deleted_players,
                        "games": 0,
                        "wins": 0,
                        "losses": 0,
                        "draws": 0,
                        "attackers_games": 0,
                        "defenders_games": 0,
                        "average_game_length": 0,
                        "_plies": 0,
                        "_seeds": set(),
                    },
                )
                item["games"] += 1
                if experiment_seed is not None:
                    item["_seeds"].add(experiment_seed)
                item[f"{side}_games"] += 1
                item["_plies"] += outcome["plies"]
                item[
                    "draws"
                    if outcome["winner"] == "draw"
                    else "wins"
                    if outcome["winner"] == side
                    else "losses"
                ] += 1
        for summary in [*by_ruleset.values(), *by_player.values()]:
            summary["average_game_length"] = summary.pop("_plies") / summary["games"]
        for summary in by_player.values():
            summary.update(
                win_rate=summary["wins"] / summary["games"],
                loss_rate=summary["losses"] / summary["games"],
                draw_rate=summary["draws"] / summary["games"],
                seed_count=len(summary.pop("_seeds")),
            )
        for summary in by_ruleset.values():
            summary.update(
                attacker_win_rate=summary["attackers_wins"] / summary["games"],
                defender_win_rate=summary["defenders_wins"] / summary["games"],
                draw_rate=summary["draws"] / summary["games"],
            )
        attackers_wins = sum(recorded_outcome(g)["winner"] == "attackers" for g in completed)
        defenders_wins = sum(recorded_outcome(g)["winner"] == "defenders" for g in completed)
        draws = sum(recorded_outcome(g)["winner"] == "draw" for g in completed)
        return {
            "total_games": len(selected),
            "completed_games": len(completed),
            "in_progress": len(selected) - len(completed),
            "attackers_wins": attackers_wins,
            "defenders_wins": defenders_wins,
            "draws": draws,
            "attacker_win_rate": attackers_wins / len(completed) if completed else 0,
            "defender_win_rate": defenders_wins / len(completed) if completed else 0,
            "draw_rate": draws / len(completed) if completed else 0,
            "average_game_length": sum(recorded_outcome(g)["plies"] for g in completed)
            / len(completed)
            if completed
            else 0,
            "by_ruleset": sorted(
                by_ruleset.values(), key=lambda row: RULESETS[row["ruleset"]].size
            ),
            "by_player": list(by_player.values()),
            "recent_games": [response(g, players[g["id"]]) for g in selected[:10]],
        }

    def tournament_or_404(identifier: str) -> dict:
        tournament = tournaments.read(identifier)
        if tournament is None:
            fail("Tournament not found.", 404)
        return tournament

    @app.get("/api/tournaments")
    def list_tournaments(summary: bool = True):
        # The collection defaults to lightweight summaries, so saved match
        # histories are never decoded or transferred just to render the
        # navigator, and every participant rune is resolved in one batch.
        return tournaments.public_collection(tournaments.list(summary=summary), summary=summary)

    @app.post("/api/tournaments", status_code=201)
    def create_tournament(request: TournamentRequest):
        from .ai import validate_fixed_role_players

        if not request.name.strip():
            fail("Tournament names must contain 1–80 characters.")
        if len(set(request.rulesets)) != len(request.rulesets):
            fail("Select each tournament battlefield only once.")
        if request.mode == "fixed_roles":
            if len(request.players) != 2:
                fail("A fixed-role comparison needs exactly two participants.")
            if len(request.rulesets) != 1:
                fail("A fixed-role comparison uses exactly one battlefield.")
        elif request.games % 2:
            fail("Use an even number of games so both opponents play each side equally.")
        with catalog_lock:
            players = []
            for provided in request.players:
                snapshot = resolve_ai_participant(
                    provided, rulesets=request.rulesets, seed=request.seed
                )
                snapshot["agent_rune"] = agent_rune({"kind": "ai", "config": snapshot["config"]})
                if snapshot.get("model"):
                    snapshot["model"]["rune"] = store.rune(f"model:{snapshot['model']['id']}")
                players.append(snapshot)
            if request.mode == "fixed_roles":
                # Apply regular agent validation independently to both fixed seats.
                try:
                    validate_fixed_role_players([player["config"] for player in players])
                except ValueError as exc:
                    fail(str(exc))
            config = {
                "mode": request.mode,
                "players": players,
                "rulesets": sorted(request.rulesets, key=lambda item: RULESETS[item].size),
                "games": request.games,
                "max_plies": request.max_plies,
                "seed": request.seed,
                "randomize_start_positions": request.randomize_start_positions,
            }
            try:
                return tournaments.launch(request.name.strip(), config)
            except ValueError as exc:
                fail(str(exc), 409)

    @app.get("/api/tournaments/{tournament_id}")
    def get_tournament(tournament_id: str):
        return tournaments.public(tournament_or_404(tournament_id))

    @app.post("/api/tournaments/{tournament_id}/cancel")
    def cancel_tournament(tournament_id: str):
        with catalog_lock:
            tournament_or_404(tournament_id)
            try:
                cancelled = tournaments.cancel(tournament_id)
            except ValueError as exc:
                fail(str(exc), 409)
            if cancelled is None:
                fail("Tournament not found.", 404)
            return tournaments.public(cancelled)

    @app.delete("/api/tournaments/{tournament_id}")
    def delete_tournament(tournament_id: str):
        with catalog_lock:
            tournament_or_404(tournament_id)
            try:
                tournaments.delete(tournament_id)
            except ValueError as exc:
                fail(str(exc), 409)
            return {"deleted": True, "id": tournament_id}

    @app.get("/api/tournaments/{tournament_id}/export")
    def export_tournament(tournament_id: str):
        tournament_or_404(tournament_id)
        payload = tournaments.export(tournament_id)
        if payload is None:
            fail("Tournament not found.", 404)
        try:
            body = json.dumps(payload, indent=2, allow_nan=False)
        except (TypeError, ValueError):
            fail("The saved tournament results could not be exported.", 500)
        return Response(
            content=body,
            media_type="application/json",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="hnefatafl-tournament-{tournament_id}.json"'
                )
            },
        )

    @app.get("/api/tournaments/{tournament_id}/games/{game_index}/replay")
    def replay_tournament_game(tournament_id: str, game_index: int):
        tournament = tournament_or_404(tournament_id)
        games = (tournament.get("result") or {}).get("games", [])
        if not 0 <= game_index < len(games):
            fail("Tournament game not found.", 404)
        game = games[game_index]
        state = new_game(game["ruleset"])
        states = [state.to_dict()]
        try:
            for move in game["moves"]:
                state = apply_move(state, Move(move["source"], move["target"]))
                states.append(state.to_dict())
        except (ValueError, IndexError, KeyError, TypeError):
            fail("The saved tournament game could not be validated.", 409)
        if state.to_dict() != game["state"]:
            fail("The saved tournament game does not match its replay.", 409)
        return {
            "validated": True,
            "ruleset": game["ruleset"],
            "states": states,
            "opening_plies": game.get("opening_plies", 0),
            "opening_seed": game.get("opening_seed"),
        }

    @app.get("/api/training/device")
    def devices():
        from .models import device_info

        return device_info()

    @app.get("/api/training/presets")
    def training_presets_route():
        from .training_presets import training_presets

        return training_presets()

    # Model helpers remain constrained to the registry root; clients never pass paths.
    def models_list() -> list[dict]:
        models = registry().list()
        runes = store.runes(f"model:{model['id']}" for model in models)
        return [{**model, "rune": runes[f"model:{model['id']}"]} for model in models]

    def model_info(model_id: str) -> dict:
        result = next((m for m in models_list() if m["id"] == model_id), None)
        if result is None:
            fail("Model not found.", 404)
        return result

    def model_file(model: dict) -> Path:
        candidate = Path(
            model.get("path") or model.get("checkpoint") or root / "models" / f"{model['id']}.pt"
        )
        if not candidate.is_absolute():
            candidate = root / "models" / candidate
        candidate = candidate.resolve()
        if not candidate.is_relative_to((root / "models").resolve()) or not candidate.is_file():
            fail("Model file is unavailable.", 404)
        return candidate

    @app.get("/api/models")
    def models():
        from .security_context import allowed, principal

        entries = models_list()
        if allowed(principal.get(), "models"):
            return entries
        # Gameplay needs an inference catalog, never training metadata or paths.
        return [
            {key: entry[key] for key in ("id", "name", "ruleset", "rune") if key in entry}
            for entry in entries
        ]

    @app.patch("/api/models/{model_id}")
    def rename_model(model_id: str, request: ModelNameRequest):
        with catalog_lock:
            model_info(model_id)
            try:
                model = registry().rename(model_id, request.name)
            except ValueError as exc:
                fail(str(exc))
            except OSError:
                fail("The model name could not be saved. Try again.", 500)
            return {**model, "rune": store.rune(f"model:{model_id}")}

    def unfinished_model_games(model_id: str) -> list[dict]:
        return [
            game
            for game in store.list_games(category="account-single-player")
            # Local games have no AI opponent, so they can never reference a
            # model; filtering to the single-player category at the storage level
            # only avoids needless JSON work, it never changes the result.
            if not recorded_outcome(game)["winner"]
            and any(
                player.get("config", {}).get("model_id") == model_id
                for player in referenced_agents(game)
            )
        ]

    def running_model_tournaments(model_id: str) -> list[dict]:
        return [
            {key: tournament[key] for key in ("id", "name", "status")}
            for tournament in tournaments.list(summary=True)
            if tournament["status"] == "running"
            and tournaments.alive(tournament)
            and any(
                player.get("config", {}).get("model_id") == model_id
                for player in tournament["config"]["players"]
            )
        ]

    def running_model_tunings(model_id: str) -> list[dict]:
        """Running optimization studies that pinned this model as an opponent."""
        found = []
        for job in store.jobs():
            if job.get("kind") != "tune":
                continue
            if read_job(job["id"])["status"] not in {
                "running",
                "cancelling",
                "pending",
                "queued",
            }:
                continue
            opponents = job.get("config", {}).get("opponents") or []
            if any(
                isinstance(entry, dict) and entry.get("model_id") == model_id for entry in opponents
            ):
                found.append(
                    {
                        "id": job["id"],
                        "name": job.get("name", job["id"]),
                        "status": job.get("status", "running"),
                    }
                )
        return found

    def model_usage_token(model_id: str, games: list[dict]) -> str:
        versions = sorted((game["id"], game["version"]) for game in games)
        return hashlib.sha256(json.dumps([model_id, versions]).encode()).hexdigest()

    @app.get("/api/models/{model_id}/usage")
    def model_usage(model_id: str):
        with job_lock, catalog_lock:
            model_info(model_id)
            games = unfinished_model_games(model_id)
            players = resolved_players(games)
            return {
                "model_id": model_id,
                "usage_token": model_usage_token(model_id, games),
                "unfinished_games": [
                    {
                        "id": game["id"],
                        "version": game["version"],
                        "ruleset": game["ruleset"],
                        "created_at": game["created_at"],
                        "total_moves": len(game["moves"]),
                        "players": {
                            side: {
                                "name": player["name"],
                                "rune": agent_rune(player),
                                **(
                                    {"username": player["username"]}
                                    if player.get("username")
                                    else {}
                                ),
                            }
                            for side, player in players[game["id"]].items()
                        },
                    }
                    for game in games
                ],
                "running_tournaments": running_model_tournaments(model_id),
                "running_optimizations": running_model_tunings(model_id),
            }

    @app.delete("/api/models/{model_id}")
    def delete_model(
        model_id: str,
        discard_unfinished: bool = False,
        usage_token: str | None = Query(default=None, pattern=r"^[0-9a-f]{64}$"),
    ):
        with job_lock, catalog_lock:
            model = model_info(model_id)
            if running_model_tournaments(model_id):
                fail(
                    "This model is used by a running tournament. Stop it before deleting the model.",
                    409,
                )
            if running_model_tunings(model_id):
                fail(
                    "This model is a frozen opponent of a running optimization study. "
                    "Stop it before deleting the model.",
                    409,
                )
            games = unfinished_model_games(model_id)
            if games and not discard_unfinished:
                fail(
                    "This model is used by an unfinished game saved on this server. "
                    "Resume it from the Hall of records, remove it from history, "
                    "or explicitly discard its unfinished games when deleting this model.",
                    409,
                )
            if discard_unfinished and usage_token is not None:
                expected = model_usage_token(model_id, games)
                if not secrets.compare_digest(usage_token, expected):
                    fail(
                        "The unfinished games changed. Review this model's usage again before deleting it.",
                        409,
                    )
            archive = {
                key: model[key]
                for key in ("id", "name", "ruleset", "version", "rune")
                if key in model
            }
            discarded = (
                {game["id"]: game["version"] for game in games} if discard_unfinished else {}
            )
            try:
                with store.discard_games(discarded, archived_model=archive):
                    registry().delete(model_id)
            except ConflictError as exc:
                fail(str(exc), 409)
            except ValueError as exc:
                fail(str(exc), 404)
            except OSError:
                fail(
                    "The model files could not be removed. No unfinished games were discarded.", 500
                )
            result: dict[str, Any] = {"deleted": True, "id": model_id}
            if discard_unfinished:
                result["discarded_game_ids"] = list(discarded)
            return result

    @app.post("/api/models/import", status_code=201)
    async def import_model(file: UploadFile = File(...)):
        from .models import MAX_CHECKPOINT_BYTES

        maximum = MAX_CHECKPOINT_BYTES
        destination = root / "models" / f"upload-{uuid4().hex}.pt"
        try:
            count = 0
            with destination.open("wb") as output:
                while chunk := await file.read(1024 * 1024):
                    count += len(chunk)
                    if count > maximum:
                        fail(f"Model uploads are limited to {maximum // (1024 * 1024)} MiB.", 413)
                    output.write(chunk)
            try:
                result = registry().import_model(destination)
            except (ValueError, RuntimeError, KeyError, TypeError, EOFError) as exc:
                fail(f"Invalid or incompatible model: {exc}")
            return {**result, "rune": store.rune(f"model:{result['id']}")}
        finally:
            destination.unlink(missing_ok=True)
            await file.close()

    @app.get("/api/models/{model_id}/export")
    def export_model(model_id: str):
        model = model_info(model_id)
        return FileResponse(
            model_file(model),
            filename=f"hnefatafl-{model_id}.pt",
            media_type="application/octet-stream",
        )

    def job_path(identifier: str) -> Path:
        if study_deletion.recovery_required:
            fail(
                "Interrupted study deletion needs recovery. Retry its deletion preview or restart after repairing filesystem access.",
                503,
            )
        # Identifiers are server-generated hex; this also protects reads from URL traversal.
        if len(identifier) != 32 or any(c not in "0123456789abcdef" for c in identifier):
            fail("Training job not found.", 404)
        return root / "jobs" / identifier

    def read_job(identifier: str) -> dict:
        # Serialize reads that reconcile worker state with launch/deletion so a
        # late response cannot reinsert a deleted record.
        with job_lock:
            return read_job_locked(identifier)

    def read_job_locked(
        identifier: str,
        *,
        reconcile: bool = True,
        snapshot: dict | None = None,
        summary: bool = False,
    ) -> dict:
        if reconcile:
            remote_training.reconcile()
        job = snapshot if snapshot is not None else store.get_job(identifier)
        if job is None:
            fail("Training job not found.", 404)
        if job.get("execution") == "remote":
            visible = remote_training.public_job(job)
            if summary:
                from .training_monitoring import job_summary

                return job_summary(visible)
            return visible
        if summary and job["status"] in {"running", "cancelling"}:
            # Reconciliation can persist a terminal transition. Never save a
            # projected navigator snapshot over the full retained history.
            job = store.get_job(identifier) or job
        if (
            summary
            and job["status"] in {"completed", "cancelled", "failed"}
            and isinstance(job.get("_import_reports"), dict)
        ):
            job = {**job, **job["_import_reports"]}
        directory = job_path(identifier)
        from .training_monitoring import overlay_heartbeat, read_heartbeat

        recovered_result = False
        for filename, key in (("progress.json", "progress"), ("result.json", "result")):
            if summary and job["status"] not in {"running", "cancelling", "pending"}:
                if key != "result" or isinstance(job.get("result"), dict):
                    continue
            path = directory / filename
            if path.exists():
                try:
                    retained = json.loads(path.read_text())
                    if summary and job["status"] not in {"running", "cancelling", "pending"}:
                        # A legacy row or a completion race can predate the final
                        # result file. Recover it once without losing history.
                        job = store.get_job(identifier) or job
                        recovered_result = True
                    if key == "progress":
                        # The worker republishes timings and counters to a small
                        # heartbeat file between real progress events.
                        retained = overlay_heartbeat(retained, read_heartbeat(directory))
                    job[key] = retained
                except (json.JSONDecodeError, OSError):
                    pass
        if recovered_result:
            store.save_job(job)
        process = processes.get(identifier)
        worker_alive = process is not None and process.poll() is None
        if process is None and job.get("pid"):
            try:
                command = Path(f"/proc/{job['pid']}/cmdline").read_bytes()
                worker_alive = (
                    b"hnefatafl.cli" in command
                    and str(directory / "config.json").encode() in command
                )
            except OSError:
                pass
        if job["status"] in {"running", "cancelling"}:
            previous_status = job["status"]
            result = job.get("result")
            pending_cancel = job["status"] == "cancelling"
            if result and not worker_alive:
                job["status"] = result.get("status", "completed")
                job["ended_at"] = now()
            elif process is not None and process.poll() is not None:
                job["status"] = (
                    "cancelled"
                    if pending_cancel
                    else "completed"
                    if process.returncode == 0
                    else "failed"
                )
                job["ended_at"] = now()
                if process.returncode and not pending_cancel:
                    job["error"] = f"Worker exited with code {process.returncode}. See the job log."
            elif process is None and not worker_alive:
                job.update(
                    status="cancelled" if pending_cancel else "failed",
                    ended_at=now(),
                    error=None
                    if pending_cancel
                    else "The training worker stopped before a result was saved; resume its checkpoint.",
                )
            if job["status"] != previous_status:
                store.save_job(job)
        if process is not None and process.poll() is not None:
            processes.pop(identifier, None)
        checkpoint = directory / "last.pt"
        if checkpoint.is_file() and job.get("resumable", True):
            job["checkpoint"] = f"{identifier}:last.pt"
        registered = job.get("result", {}).get("model")
        if registered:
            job["model_id"] = registered.get("id")
        logfile = directory / "worker.log"
        if not summary and logfile.exists():
            with logfile.open("rb") as handle:
                handle.seek(max(0, logfile.stat().st_size - 16000))
                job["logs"] = handle.read().decode(errors="replace")
        from .training_monitoring import publication_age

        # A local worker's files share this server's clock, so their newest
        # modification time is the authoritative publication freshness. A stale
        # snapshot must not look fresh just because the dashboard just mounted.
        # Only a live job can be unresponsive, so terminal payloads stay stable.
        if job["status"] in {"running", "cancelling"}:
            age = publication_age(directory)
            if age is not None:
                job["publication_age_seconds"] = age
        if summary:
            from .training_monitoring import job_summary

            return job_summary(job)
        job.pop("_import_reports", None)
        return job

    training_fields = TRAINING_FIELDS

    @app.get("/api/training/jobs")
    def training_jobs(summary: bool = False):
        with job_lock:
            remote_training.reconcile()
            return [
                read_job_locked(j["id"], reconcile=False, snapshot=j, summary=summary)
                for j in store.jobs(summary=summary)
            ]

    @app.get("/api/training/jobs/{job_id}/export")
    def export_training_job(job_id: str, request: Request):
        admission = request.state.archive_admission
        with admission.worker():
            return build_job_export(job_id, admission)

    def build_job_export(job_id, admission):
        from .job_archives import export_snapshot, snapshot_job, JobArchiveResponse, TERMINAL
        from .models import ModelSnapshotChanged

        descriptor, filename = tempfile.mkstemp(suffix=".tar.gz", prefix="hnefatafl-job-")
        os.close(descriptor)
        archive = Path(filename)
        admission.output = archive
        try:
            with (
                tempfile.TemporaryDirectory(prefix=".job-snapshot-", dir=root) as temporary,
                ExitStack() as pins,
            ):
                with job_lock, catalog_lock:
                    # Export reads retained state without reconciling or modifying the row.
                    directory = job_path(job_id)
                    job = store.get_job(job_id)
                    if job is None:
                        fail("Training job not found.", 404)
                    if job.get("status") not in TERMINAL or job_worker_alive(job):
                        fail("Stop this training job before exporting its data.", 409)
                    if job.get("kind") == "tune":
                        from .job_studies import study_database

                        database = study_database(directory, job["config"])
                        for contributor in store.jobs():
                            if contributor.get("kind") != "tune":
                                continue
                            saved_storage = contributor.get("config", {}).get(
                                "storage",
                                "sqlite:///" + str(job_path(contributor["id"]) / "study.sqlite3"),
                            )
                            if (
                                saved_storage == "sqlite:///" + str(database)
                                and read_job(contributor["id"])["status"] not in TERMINAL
                            ):
                                fail(
                                    "Stop every job contributing to this study before exporting it.",
                                    409,
                                )
                    snapshot = pins.enter_context(
                        snapshot_job(job, directory, registry(), Path(temporary))
                    )
                # Sync FastAPI routes already run in its thread pool. Compression
                # holds no lifecycle/catalog lock and only one export runs at a time.
                export_snapshot(snapshot, archive, package_version)
            return JobArchiveResponse(
                archive,
                filename=f"hnefatafl-job-{job_id}.tar.gz",
                media_type="application/gzip",
            )
        except ModelSnapshotChanged as exc:
            archive.unlink(missing_ok=True)
            fail(str(exc), 409)
        except (ValueError, TypeError, KeyError, RuntimeError) as exc:
            archive.unlink(missing_ok=True)
            fail(f"Cannot export training job: {exc}")
        except OSError:
            archive.unlink(missing_ok=True)
            fail("The saved job artifacts could not be read. Try again.", 500)
        except BaseException:
            archive.unlink(missing_ok=True)
            raise

    @app.post("/api/training/jobs/import", status_code=201)
    def import_training_job(request: Request, file: UploadFile = File(...), summary: bool = False):
        with request.state.archive_admission.worker():
            return restore_training_job(file, request.state.archive_admission, summary=summary)

    def restore_training_job(file, admission, *, summary):
        import zipfile
        import sqlite3
        import zlib
        import tarfile
        from .job_archives import import_job, MAX_ARCHIVE_BYTES

        try:
            with tempfile.TemporaryDirectory(prefix="hnefatafl-upload-") as temporary:
                archive = Path(temporary) / "job.archive"
                count = 0
                with archive.open("wb") as output:
                    while chunk := file.file.read(1024 * 1024):
                        count += len(chunk)
                        if count > MAX_ARCHIVE_BYTES:
                            fail("Job archives are limited to 512 MiB.", 413)
                        output.write(chunk)

                @contextmanager
                def publication_context():
                    with job_lock, catalog_lock:
                        admission.ensure_open()
                        yield

                imported = import_job(
                    archive,
                    root,
                    store,
                    resume_info=lambda job, directory: resume_checkpoint(
                        job["id"], job=job, directory=directory
                    )[0],
                    publication_context=publication_context,
                )
                if summary:
                    from .training_monitoring import imported_job_summary

                    return imported_job_summary(imported)
                with job_lock:
                    return read_job_locked(imported["id"], reconcile=False)

        except (
            ValueError,
            TypeError,
            KeyError,
            RuntimeError,
            EOFError,
            zipfile.BadZipFile,
            tarfile.TarError,
            zlib.error,
            NotImplementedError,
        ) as exc:
            fail(f"Invalid training-job archive: {exc}")
        except (OSError, sqlite3.Error):
            fail("The training job could not be restored. No new job was retained.", 500)
        finally:
            file.file.close()

    @app.get("/api/training/jobs/{job_id}")
    def training_job(job_id: str):
        return read_job(job_id)

    @app.patch("/api/training/jobs/{job_id}")
    def rename_training_job(job_id: str, request: ModelNameRequest):
        # Renaming changes display metadata only: IDs, paths, checkpoints,
        # parameters, worker assignments and execution state are untouched.
        with job_lock:
            job = store.get_job(job_id)
            if job is None:
                fail("Training job not found.", 404)
            name = request.name.strip()
            if not name or len(name) > 100:
                fail("Training job names must contain 1–100 characters.")
            job["name"] = name
            store.save_job(job)
            return read_job(job_id)

    def job_worker_alive(job: dict) -> bool:
        if job.get("execution") == "remote":
            return job["status"] in {"running", "cancelling"} or bool(
                job.get("lease_hash")
                and not job.get("finish_received")
                and job.get("lease_expires", 0) > time.time()
            )
        process = processes.get(job["id"])
        if process is not None:
            return process.poll() is None
        if not job.get("pid"):
            return False
        try:
            command = Path(f"/proc/{job['pid']}/cmdline").read_bytes()
            return (
                b"hnefatafl.cli" in command
                and str(job_path(job["id"]) / "config.json").encode() in command
            )
        except OSError:
            return False

    def study_preview_locked(job_id: str):
        from .job_studies import study_deletion_group

        study_deletion.recover()
        job_path(job_id)
        jobs = store.jobs()
        if not any(job["id"] == job_id for job in jobs):
            fail("Training job not found.", 404)
        return study_deletion_group(jobs, root / "jobs", job_id, job_worker_alive)

    @app.get("/api/training/jobs/{job_id}/study-deletion-preview")
    def preview_study_deletion(job_id: str):
        import sqlite3
        from .job_archives import ArchiveError

        with job_lock, catalog_lock:
            try:
                preview, _ = study_preview_locked(job_id)
                return preview
            except ArchiveError as error:
                fail(str(error), 409)
            except (KeyError, TypeError):
                fail(
                    "Study job metadata is incomplete. Repair the saved records before deleting this study.",
                    409,
                )
            except (OSError, sqlite3.Error):
                fail(
                    "Study deletion recovery or artifact inspection failed. Repair filesystem access and retry.",
                    503,
                )

    @app.delete("/api/training/jobs/{job_id}/study")
    def delete_study(job_id: str, confirmation_token: str = Query(..., pattern=r"^[0-9a-f]{64}$")):
        import sqlite3
        from .job_archives import ArchiveError
        from .training_identity import study_lease

        with job_lock, catalog_lock:
            try:
                preview, index = study_preview_locked(job_id)
                if not preview["can_delete"]:
                    fail(" ".join(preview["blocked_reasons"]), 409)
                if not secrets.compare_digest(confirmation_token, preview["confirmation_token"]):
                    fail(
                        "The study deletion scope changed. Refresh the preview and confirm the affected jobs again.",
                        409,
                    )
                # Same CLI lease as training/export. The calculation reads each
                # database once; generation guards reject a concurrent writer
                # rather than re-reading and silently enlarging confirmed scope.
                database = next(
                    path for path in index.cached if path.parent.name == preview["owner_job_id"]
                )
                with study_lease(
                    {"storage": "sqlite:///" + str(database), "output_dir": str(database.parent)}
                ):
                    if index.cached[database]["generation"] != index.generation(database):
                        fail(
                            "Study metadata changed. Refresh its deletion preview before confirming again.",
                            409,
                        )
                    identifiers = [job["id"] for job in preview["jobs"]]
                    cleanup_pending = study_deletion.delete(identifiers)
                for identifier in identifiers:
                    processes.pop(identifier, None)
                return {"deleted": True, "job_ids": identifiers, "cleanup_pending": cleanup_pending}
            except ArchiveError as error:
                fail(str(error), 409 if not study_deletion.recovery_required else 503)
            except (KeyError, TypeError):
                fail(
                    "Study job metadata is incomplete. Repair the saved records before deleting this study.",
                    409,
                )
            except ValueError as error:
                fail(str(error), 409)
            except (OSError, sqlite3.Error):
                fail(
                    "Study deletion could not finish. Preserved or quarantined data will be recovered when the preview is retried or the server restarts.",
                    503,
                )

    @app.delete("/api/training/jobs/{job_id}")
    def delete_training_job(job_id: str):
        with job_lock:
            job = read_job(job_id)
            if job["status"] in {"running", "cancelling", "pending", "queued"} or job_worker_alive(
                job
            ):
                fail("Stop this training job before deleting its data.", 409)
            directory = job_path(job_id)
            jobs_root = root / "jobs"
            # Never follow a replacement directory outside this server's store.
            # rmtree itself unlinks internal symlinks rather than their targets.
            if jobs_root.is_symlink() or directory.is_symlink() or directory.resolve() != directory:
                fail("The saved training directory is not a safe local job directory.", 409)
            if directory.exists() and not directory.is_dir():
                fail("The saved training directory is not a safe local job directory.", 409)
            from .job_studies import study_job_dependencies
            from .job_archives import ArchiveError

            try:
                dependencies = study_job_dependencies(store.jobs(), jobs_root, job_id)
            except ArchiveError as error:
                fail(str(error), 409)
            if dependencies:
                fail(
                    "Retained optimization "
                    + ", ".join(item["name"] for item in dependencies)
                    + " depends on this job's training data. Remove its dependent study/job references before deleting these checkpoints.",
                    409,
                )
            for candidate in store.jobs():
                if candidate["id"] == job_id:
                    continue
                saved = candidate.get("config", {})
                storage = saved.get("storage", "")
                if isinstance(storage, str) and storage.startswith("sqlite:///"):
                    database = Path(storage[len("sqlite:///") :]).resolve()
                    if database.is_relative_to(directory):
                        fail(
                            "Another saved optimization job uses this job's study. "
                            "Delete the dependent optimization job first.",
                            409,
                        )
                resumed = saved.get("resume")
                nested = saved.get("training", {})
                if not resumed and isinstance(nested, dict):
                    resumed = nested.get("resume")
                if (
                    isinstance(resumed, str)
                    and Path(resumed).resolve().is_relative_to(directory)
                    and job_worker_alive(candidate)
                ):
                    fail(
                        "A running training job uses this checkpoint. Stop it before deleting the data.",
                        409,
                    )
            try:
                # Keep the job row if cleanup fails, permitting inspection and
                # retry. Models are owned registry copies outside this tree.
                with store.discard_job(job_id):
                    if directory.exists():
                        shutil.rmtree(directory)
            except ConflictError:
                fail("Training job not found.", 404)
            except OSError:
                fail("The training data could not be completely removed. Try again.", 500)
            processes.pop(job_id, None)
            return {"deleted": True, "job_id": job_id}

    @app.post("/api/training/jobs", status_code=201)
    def launch_training(config: dict[str, Any]):
        with job_lock, catalog_lock:
            config = dict(config)
            execution = config.pop("execution", "local")
            worker_id = config.pop("worker_id", None)
            if execution not in ("local", "remote"):
                fail("Training execution must be local or remote.")
            if worker_id is not None and (execution != "remote" or not isinstance(worker_id, str)):
                fail("Select a remote worker only for remote training.")
            if execution == "local" and (
                any(
                    j.get("execution", "local") == "local"
                    and read_job(j["id"])["status"] in {"running", "cancelling"}
                    for j in store.jobs()
                )
                or any(process.poll() is None for process in list(processes.values()))
            ):
                fail("A training job is already running. Stop it before starting another.", 409)
            kind = config.pop("kind", "train")
            if kind not in ("train", "tune"):
                fail("Training kind must be train or tune.")
            if kind == "tune" and execution == "remote":
                fail("Remote optimization is not supported. Run tuning on this server.")
            allowed = training_fields if kind == "train" else TUNE_FIELDS
            check_training_fields(config, allowed)
            identifier = uuid4().hex
            directory = job_path(identifier)

            def resolve_resume_token(resume: object) -> str:
                """Resolve an opaque server-issued checkpoint reference to a path.

                Accepted forms are an unambiguous `last.pt`, a
                `<job_id>:<relative>/last.pt` token, or an absolute path already
                inside this server's job store (a study's persisted source).
                """
                if not isinstance(resume, str):
                    fail("Resume must name a saved training job checkpoint.")
                if next((m for m in models_list() if m["id"] == resume), None):
                    fail(
                        "Registered models are for play. Resume using a training job's last checkpoint."
                    )
                if Path(resume).is_absolute():
                    jobs_root = (root / "jobs").resolve()
                    raw_checkpoint = Path(resume)
                    if any(path.is_symlink() for path in (raw_checkpoint, *raw_checkpoint.parents)):
                        fail("Checkpoint references must not contain symlinks.")
                    checkpoint = raw_checkpoint.resolve()
                    if (
                        not checkpoint.is_relative_to(jobs_root)
                        or checkpoint.name not in {"last.pt", "objective.pt"}
                        or not checkpoint.is_file()
                    ):
                        fail("The saved checkpoint was not found.")
                    return str(checkpoint)
                if ":" in resume:
                    prior_id, filename = resume.split(":", 1)
                    if Path(filename).name not in {"last.pt", "objective.pt"}:
                        fail("Select a full training checkpoint (last.pt or objective.pt).")
                    base = job_path(prior_id)
                    raw_checkpoint = base / filename
                    if any(path.is_symlink() for path in (raw_checkpoint, *raw_checkpoint.parents)):
                        fail("Checkpoint references must not contain symlinks.")
                    checkpoint = raw_checkpoint.resolve()
                    if not checkpoint.is_relative_to(base.resolve()) or not checkpoint.is_file():
                        fail("The saved checkpoint was not found.")
                    return str(checkpoint)
                if resume != "last.pt":
                    fail("Resume requires a job checkpoint token or an unambiguous last.pt.")
                matches = [p for p in (root / "jobs").glob("*/last.pt")]
                if len(matches) != 1:
                    fail("The saved checkpoint was not found or its name is ambiguous.")
                return str(matches[0].resolve())

            schedule_free_checked = False

            def validate_training(values: dict, *, prefix: str = "") -> dict:
                nonlocal schedule_free_checked
                if not isinstance(values, dict):
                    fail("A training configuration must be an object.")
                check_training_fields(values, training_fields, prefix=prefix)
                values = dict(values)
                resume = values.pop("resume", None)
                if resume:
                    values["resume"] = resolve_resume_token(resume)
                values.update(output_dir=str(directory), registry_dir=str(root / "models"))
                try:
                    from .models import load_checkpoint, resolve_device
                    from .training import normalize_config

                    if "schedulefree_warmup_steps" in values:
                        warmup = values["schedulefree_warmup_steps"]
                        if type(warmup) is not int or not 0 <= warmup <= 1000000:
                            raise TrainingFieldsRejected(
                                [f"{prefix}schedulefree_warmup_steps"],
                                "schedulefree_warmup_steps must be an integer between 0 and 1000000",
                            )
                    if (
                        values.get("optimizer") == "adamw_schedulefree"
                        and values.get("adaptation", "off") != "off"
                    ):
                        from .training_optimizers import SCHEDULE_FREE_ADAPTATION_ERROR

                        raise TrainingFieldsRejected(
                            [f"{prefix}optimizer", f"{prefix}adaptation"],
                            SCHEDULE_FREE_ADAPTATION_ERROR,
                        )

                    result = normalize_config(values)
                    if result["device"] not in {"auto", "cpu", "cuda", "mps"}:
                        fail("Training device must be auto, cpu, cuda or mps.")
                    if execution == "local":
                        if (
                            result["optimizer"] == "adamw_schedulefree"
                            and not schedule_free_checked
                        ):
                            from .training_optimizers import (
                                SCHEDULE_FREE_DEPENDENCY_ERROR,
                                schedule_free_available,
                            )

                            if not schedule_free_available():
                                raise TrainingFieldsRejected(
                                    [f"{prefix}optimizer"], SCHEDULE_FREE_DEPENDENCY_ERROR
                                )
                            # Tuning validates search-space extremes through this
                            # same function; one execution probe per request suffices.
                            schedule_free_checked = True
                        resolved = resolve_device(result["device"])
                        if result["inference_backend"] == "openvino":
                            from .inference_backends import openvino_capabilities, supports_openvino

                            capabilities = openvino_capabilities()
                            if resolved.type != "cpu":
                                fail(
                                    "OpenVINO inference requires PyTorch CPU optimization; select CPU."
                                )
                            if not supports_openvino(capabilities, result["inference_device"]):
                                fail(
                                    "Requested OpenVINO device is unavailable. Install the openvino extra and Intel GPU runtime. "
                                    + capabilities.get("openvino_error", "")
                                )
                    if result.get("resume"):
                        model, checkpoint_data = load_checkpoint(result["resume"])
                        saved_config = checkpoint_data.get("metadata", {}).get(
                            "training_config", {}
                        )
                        for name in ("optimizer", "seed", "schedulefree_warmup_steps"):
                            if name in saved_config and saved_config[name] != result[name]:
                                fail(f"Resume {name} must match the saved training job.")
                        if checkpoint_data.get("optimizer_state") is None:
                            fail(
                                "This model is for play; resume requires a full last.pt checkpoint."
                            )
                        if (
                            model.ruleset != result["ruleset"]
                            or model.config["channels"] != result["channels"]
                            or model.config["blocks"] != result["blocks"]
                        ):
                            fail(
                                "Resume ruleset and architecture must match the saved training job."
                            )
                    if type(result["mixed_precision"]) is not bool:
                        fail("mixed_precision must be a boolean.")
                    return result
                except (ValueError, TypeError, KeyError) as exc:
                    fail(str(exc))

            if kind == "train":
                config = validate_training(config)
            else:
                from .job_archives import ArchiveError
                from .job_studies import (
                    opponents_config,
                    pruner_config,
                    sampler_config,
                    search_space_config,
                    verification_config,
                )

                requested = dict(config)
                requested_training = dict(requested.get("training") or {})
                resume_study = config.pop("resume_study", None)
                previous: dict | None = None
                previous_config: dict = {}
                if resume_study:
                    previous = read_job(str(resume_study))
                    database = job_path(previous["id"]) / "study.sqlite3"
                    previous_storage = previous["config"].get("storage", "")
                    if previous_storage.startswith("sqlite:///"):
                        database = Path(previous_storage[len("sqlite:///") :]).resolve()
                    if not database.is_relative_to((root / "jobs").resolve()):
                        fail("The saved study is outside the job store.")
                    if previous["kind"] != "tune" or not database.is_file():
                        fail("This job has no saved optimization study.")
                    if previous["status"] in {"running", "pending", "queued", "cancelling"} or any(
                        entry.get("kind") == "tune"
                        and entry.get("status") in {"running", "pending", "queued", "cancelling"}
                        and entry.get("config", {}).get("storage") == "sqlite:///" + str(database)
                        for entry in store.jobs()
                    ):
                        fail("Stop the active owning study before resuming it.")
                    config["storage"] = "sqlite:///" + str(database)
                    previous_config = previous.get("config", {})
                    # Server-authoritative identity: keep every previous setting the
                    # client did not explicitly override, so a resumed study keeps
                    # its checkpoint source, opponent, verification and strategy.
                    for key in (
                        "training",
                        "search_space",
                        "opponents",
                        "verification",
                        "sampler",
                        "pruner",
                        "seed",
                        "study_name",
                        "source_checkpoint",
                    ):
                        if key not in requested and key in previous_config:
                            config[key] = previous_config[key]

                if previous is not None:
                    previous_training = {
                        key: value
                        for key, value in previous_config.get("training", {}).items()
                        if key in training_fields
                    }
                    # Only stored server paths are removed. Explicit client-owned
                    # path attempts still reach normal validation and are rejected.
                    config["training"] = {**previous_training, **requested_training}

                # A portable source-checkpoint token (for example from an
                # imported study) is resolved to a server-owned path and injected
                # into the nested training configuration for the CLI.
                source_checkpoint = config.get("source_checkpoint")
                if source_checkpoint is not None:
                    resolved_source = resolve_resume_token(source_checkpoint)
                    training_input = dict(config.get("training") or {})
                    if "source_checkpoint" in requested:
                        if (
                            "resume" in requested_training
                            and resolve_resume_token(requested_training["resume"])
                            != resolved_source
                        ):
                            fail(
                                "source_checkpoint and training.resume identify different source checkpoints."
                            )
                        training_input["resume"] = resolved_source
                    else:
                        training_input.setdefault("resume", resolved_source)
                    config["training"] = training_input

                training = validate_training(
                    config.get("training", {"preset": "smoke", "ruleset": "brandubh"}),
                    prefix="training.",
                )
                if training["evaluation_opponent"] == "champion":
                    fail(
                        "Optimization cannot use the evolving Champion as its objective; "
                        "select a fixed opponent or a frozen registered model."
                    )
                if training.get("adaptation", "off") != "off":
                    fail(
                        "Disable automatic hyperparameter updates while tuning with Optuna; "
                        "the study varies the learning rate across trials."
                    )
                trials = config.get("trials", 4)
                study_name = config.get("study_name", "hnefatafl")
                if not isinstance(study_name, str) or not 1 <= len(study_name) <= 100:
                    fail("study_name must contain 1–100 characters.")
                seed = config.get("seed", 0)
                if type(seed) is not int or not 0 <= seed < 2**32:
                    fail("seed must be an unsigned 32-bit integer.")
                if type(trials) is not int or not 1 <= trials <= 10000:
                    fail("trials must be an integer between 1 and 10000.")

                search = config.get("search_space")
                if search is not None:
                    try:
                        search = search_space_config(search)
                    except ArchiveError as exc:
                        fail(str(exc))
                    config["search_space"] = search
                    # Check each extreme against the actual training validator.
                    for name, distribution in search.items():
                        values = (
                            distribution["choices"]
                            if distribution["type"] == "categorical"
                            else [distribution["low"], distribution["high"]]
                        )
                        for value in values:
                            validate_training(
                                {
                                    **{
                                        k: v
                                        for k, v in training.items()
                                        if k in training_fields and k != "resume"
                                    },
                                    name: value,
                                }
                            )
                else:
                    search = {}
                # Optional aggregate objective, held-out verification and study
                # strategy use the same portable contract as archive import.
                try:
                    if "opponents" in config:
                        config["opponents"] = opponents_config(config["opponents"])
                    if "verification" in config:
                        config["verification"] = verification_config(config["verification"])
                    if "sampler" in config:
                        config["sampler"] = sampler_config(config["sampler"])
                    if "pruner" in config:
                        config["pruner"] = pruner_config(config["pruner"])
                except ArchiveError as exc:
                    fail(str(exc))
                max_failures = config.get("max_failures", min(3, trials))
                if type(max_failures) is not int or not 1 <= max_failures <= trials:
                    fail("max_failures must be an integer between 1 and the trial budget.")

                # Checkpoint-based tuning is validated before any trial runs: the
                # iteration target must exceed the checkpoint, and architecture
                # fixed by the checkpoint cannot be sampled.
                if training.get("resume"):
                    from .models import load_checkpoint

                    _, checkpoint_data = load_checkpoint(training["resume"])
                    completed = int(checkpoint_data.get("extra", {}).get("iteration", 0))
                    target = training.get("iterations")
                    if type(target) is not int or target <= completed:
                        fail(
                            "Checkpoint-based tuning needs iterations greater than the "
                            f"checkpoint's completed iteration ({completed})."
                        )
                    for name in ("channels", "blocks"):
                        if name in search:
                            fail(f"{name} is fixed by the source checkpoint and cannot be tuned.")

                if previous is not None:
                    conflicts = resume_study_conflicts(
                        requested, requested_training, training, previous_config
                    )
                    if conflicts:
                        raise TrainingFieldsRejected(
                            conflicts,
                            "Resume study cannot change the experiment identity: "
                            + ", ".join(conflicts),
                        )
                config.update(training=training, output_dir=str(directory))
                from .training import requested_study_identity
                from .training_identity import check_study_identity

                try:
                    identity = requested_study_identity(config)
                    if previous is not None:
                        check_study_identity(database, identity)
                except (ValueError, OSError) as exc:
                    fail(str(exc))
            if execution == "remote":
                return remote_training.enqueue(identifier, config, worker_id)
            directory.mkdir()
            config_file = directory / "config.json"
            # normalize_config already bounds every field, so a finite-only dump
            # can never fail here; it stays explicit so an unsafe value can never
            # be written as non-standard JSON for a later reader.
            config_file.write_text(json.dumps(config, indent=2, allow_nan=False))
            command = [sys.executable, "-m", "hnefatafl.cli", kind, "--config", str(config_file)]
            logfile = directory / "worker.log"
            with logfile.open("wb") as output:
                process = subprocess.Popen(
                    command,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    cwd=PROJECT_ROOT,
                    start_new_session=True,
                )
            processes[identifier] = process
            job = {
                "id": identifier,
                "kind": kind,
                "execution": "local",
                "status": "running",
                "config": {
                    k: v for k, v in config.items() if k not in {"output_dir", "registry_dir"}
                },
                "progress": {},
                "created_at": now(),
                "ended_at": None,
                "error": None,
                "pid": process.pid,
            }
            store.save_job(job)
            return read_job(identifier)

    def resume_checkpoint(
        job_id: str, *, job: dict | None = None, directory: Path | None = None
    ) -> tuple[dict, dict | None]:
        """Read one explicitly selected full checkpoint, never a client-supplied path."""
        job = job if job is not None else read_job(job_id)
        values = job.get("config", {})
        info = {
            "job_id": job_id,
            "completed_iteration": 0,
            "ruleset": values.get("ruleset", ""),
            "channels": values.get("channels", 0),
            "blocks": values.get("blocks", 0),
            "optimizer": values.get("optimizer", ""),
            "model_name": values.get("model_name", ""),
            "can_resume": False,
        }
        if job.get("kind") == "tune":
            info["reason"] = "Optimization jobs use Resume study instead."
            return info, None
        if job["status"] in {"running", "cancelling", "pending", "queued"}:
            info["reason"] = "Stop this training job before resuming its checkpoint."
            return info, None
        checkpoint = (directory or job_path(job_id)) / "last.pt"
        if not checkpoint.is_file():
            info["reason"] = "The saved checkpoint was not found."
            return info, None
        try:
            from .models import load_checkpoint

            model, payload = load_checkpoint(checkpoint)
            saved = payload.get("metadata", {}).get("training_config", {})
            extra = payload.get("extra", {})
            completed = extra.get("iteration")
            if (
                payload.get("optimizer_state") is None
                or not isinstance(saved, dict)
                or not saved.get("optimizer")
                or type(completed) is not int
                or completed < 0
                or not {"replay", "python_rng", "torch_rng"}.issubset(extra)
            ):
                info["reason"] = "The saved file is not a full resumable training checkpoint."
                return info, None
            info.update(
                completed_iteration=completed,
                ruleset=model.ruleset,
                channels=model.channels,
                blocks=model.blocks,
                optimizer=saved["optimizer"],
                seed=saved.get("seed", values.get("seed", 0)),
                model_name=saved.get("model_name", values.get("model_name", "")),
                checkpoint=f"{job_id}:last.pt",
            )
            # Optimizer groups are authoritative: older checkpoint metadata may
            # differ after a prior resume restored its parameter values.
            from .training import DEFAULTS, normalize_config

            # Checkpoint inputs are authoritative. Older checkpoints may omit
            # fields introduced later; prefer their job's saved inputs before
            # filling only genuinely absent fields through normal validation.
            if "training_symmetry_mode" not in values and "training_symmetry_mode" not in saved:
                # Pre-0.28 jobs/checkpoints predate D4 augmentation. Keep their
                # historical OFF behavior; explicit saved values still win.
                saved = {**saved, "training_symmetry_mode": "off"}
            saved = normalize_config(
                {
                    **{
                        key: value
                        for key, value in values.items()
                        if key in training_fields - {"resume"}
                    },
                    **{
                        key: value
                        for key, value in saved.items()
                        if key in training_fields - {"resume"}
                    },
                    **model.config,
                }
            )
            optimizer_state = payload["optimizer_state"]
            if not isinstance(optimizer_state, dict):
                raise ValueError("Invalid optimizer state")
            groups = optimizer_state.get("param_groups", [])
            if not isinstance(groups, list) or not groups or not isinstance(groups[0], dict):
                raise ValueError("Missing optimizer parameter groups")
            for name, parameter in (
                ("learning_rate", "lr"),
                ("weight_decay", "weight_decay"),
                ("momentum", "momentum"),
            ):
                value = float(groups[0].get(parameter, saved.get(name, DEFAULTS[name])))
                if not (1e-7 if name == "learning_rate" else 0) <= value <= 1:
                    raise ValueError("Invalid saved optimizer parameter")
                info[name] = saved[name] = value
            if saved["optimizer"] == "adamw_schedulefree":
                warmup = groups[0].get("warmup_steps")
                if type(warmup) is not int or not 0 <= warmup <= 1000000:
                    raise ValueError("Invalid saved Schedule-Free warm-up")
                saved["schedulefree_warmup_steps"] = warmup
                info["schedulefree_warmup_steps"] = warmup
            info["config"] = {
                key: value for key, value in saved.items() if key in training_fields - {"resume"}
            }
            info["can_resume"] = True
            return info, saved
        except (OSError, ValueError, TypeError, KeyError, RuntimeError):
            info["reason"] = "The saved training checkpoint could not be read safely."
            return info, None

    @app.get("/api/training/jobs/{job_id}/resume")
    def training_resume_info(job_id: str):
        info, _ = resume_checkpoint(job_id)
        return info

    @app.post("/api/training/jobs/{job_id}/resume", status_code=201)
    def resume_training(job_id: str, request: dict[str, Any]):
        with job_lock:
            return resume_training_locked(job_id, request)

    def resume_training_locked(job_id: str, request: dict[str, Any]):
        from .training import DEFAULTS

        immutable = {
            "ruleset",
            "channels",
            "blocks",
            "seed",
            "optimizer",
            "schedulefree_warmup_steps",
        }
        editable = training_fields - immutable - {"resume"}
        unexpected = set(request) - (editable | {"execution", "worker_id"})
        if unexpected:
            names = ", ".join(sorted(unexpected))
            raise TrainingFieldsRejected(
                sorted(unexpected),
                f"Resume cannot change {names}; checkpoint identity is immutable.",
            )
        if "iterations" not in request:
            fail(
                "Resume accepts editable training settings and execution target; checkpoint identity is immutable."
            )
        for name in editable & request.keys():
            if isinstance(DEFAULTS[name], float) and type(request[name]) not in (int, float):
                fail(f"{name} must be a number.")
        target = request["iterations"]
        if type(target) is not int or not 1 <= target <= 100000:
            fail("Target total iterations must be an integer between 1 and 100000.")
        info, saved = resume_checkpoint(job_id)
        if saved is None:
            fail(info["reason"], 409)
        if target <= info["completed_iteration"]:
            fail("Target total iterations must exceed the saved completed iteration.")
        # Keep every saved input except reviewed overrides. Launch assigns a
        # fresh owned directory and applies the normal new-job validation.
        config = {key: value for key, value in saved.items() if key in training_fields}
        config.update({name: request[name] for name in editable & request.keys()})
        config.update(kind="train", iterations=target, resume=info["checkpoint"])
        source = read_job(job_id)
        config["execution"] = request.get("execution", source.get("execution", "local"))
        worker_id = (
            request.get("worker_id")
            if "execution" in request
            else request.get("worker_id", source.get("target_worker_id"))
        )
        if config["execution"] == "remote" and worker_id is not None:
            config["worker_id"] = worker_id
        return launch_training(config)

    @app.post("/api/training/jobs/{job_id}/cancel")
    def cancel_training(job_id: str):
        with job_lock:
            return cancel_training_locked(job_id)

    def cancel_training_locked(job_id: str):
        job = read_job(job_id)
        if job.get("execution") == "remote":
            return remote_training.cancel(job)
        if job["status"] not in {"running", "cancelling"}:
            return job
        process = processes.get(job_id)
        pid = process.pid if process is not None else job.get("pid")
        if pid:
            try:
                command = Path(f"/proc/{pid}/cmdline").read_bytes()
                if (
                    b"hnefatafl.cli" in command
                    and str(job_path(job_id) / "config.json").encode() in command
                ):
                    os.killpg(pid, signal.SIGTERM)
            except (OSError, ProcessLookupError):
                pass
        job.update(status="cancelling")
        store.save_job(job)
        return read_job(job_id)

    # Serve a production Vite build, retaining JSON 404s for all unknown API paths.
    build = PROJECT_ROOT / "frontend" / "dist"
    if build.exists():
        assets = build / "assets"
        if assets.exists():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def frontend(path: str):
            if path == "api" or path.startswith("api/"):
                fail("API endpoint not found.", 404)
            candidate = (build / path).resolve()
            if candidate.is_relative_to(build.resolve()) and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(build / "index.html")

    return app


app = create_app()
