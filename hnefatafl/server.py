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
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, NoReturn
from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from .engine import Move, RULESETS, State, apply_move, legal_moves, new_game
from .identities import agent_identity, effective_ai_config
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


Preset = Literal["baseline", "shield_maiden", "alpha_beta", "mcts"]


class OpponentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile_id: Preset = "baseline"


class GameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ruleset: Literal["tablut", "brandubh", "copenhagen"] = "tablut"
    mode: Literal["human-ai", "ai-ai"] = "human-ai"
    human_side: Literal["attackers", "defenders"] = "defenders"
    players: dict[str, OpponentRequest] = Field(default_factory=dict)


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
    rulesets: list[Literal["brandubh", "tablut", "copenhagen"]] = Field(
        default=["brandubh"], min_length=1, max_length=3
    )
    players: list[TournamentPlayerRequest] = Field(min_length=2, max_length=4)
    games: StrictInt = Field(default=2, ge=2, le=100)
    max_plies: StrictInt = Field(default=300, ge=1, le=1000)
    seed: StrictInt = Field(default=0, ge=0, le=2**32 - 1)


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

    app.add_middleware(JobUploadLimit)
    root = Path(data_dir or os.environ.get("HNEFATAFL_DATA_DIR", "data")).resolve()
    root.mkdir(parents=True, exist_ok=True)
    (root / "jobs").mkdir(exist_ok=True)
    (root / "models").mkdir(exist_ok=True)
    store = Store(root / "hnefatafl.sqlite3")
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

    def missing_analysis(game: dict) -> dict:
        state = state_of(game)
        return {
            "cursor": game["cursor"],
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

    def response(game: dict) -> dict:
        state = state_of(game)
        start = new_game(game["ruleset"])
        result = {key: value for key, value in game.items() if key != "state"}
        from .ai import ai_profiles

        profiles = {profile["id"]: profile["name"] for profile in ai_profiles()}
        retired_names = {model["id"]: model["name"] for model in store.historical_models()}
        result["players"] = {}
        for side, recorded_player in game["players"].items():
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
        if config is not None and not isinstance(config, dict):
            fail("AI configuration must be an object.")
        config = dict(config or {})
        supported = {
            "algorithm",
            "depth",
            "iterations",
            "time_limit",
            "seed",
            "exploration",
            "temperature",
            "evaluation",
            "model_id",
            "rollout_depth",
            "search_wrapper",
            "heuristic_version",
        }
        if set(config) - supported:
            fail("Unsupported AI configuration field.")
        if profile_id and "algorithm" not in config:
            from .ai import ai_profiles

            profile = next((p for p in ai_profiles() if p["id"] == profile_id), None)
            if profile is None:
                fail("Unknown AI profile.")
            config = {**profile.get("parameters", {}), **config, "algorithm": profile["algorithm"]}
        algorithm = config.setdefault("algorithm", "baseline")
        if algorithm == "alphabeta":
            algorithm = config["algorithm"] = "alpha_beta"
        if not isinstance(algorithm, str) or algorithm not in {
            "baseline",
            "shield_maiden",
            "alpha_beta",
            "mcts",
            "neural",
        }:
            fail("Unknown AI algorithm.")
        if "heuristic_version" in config and (
            algorithm != "shield_maiden"
            or type(config["heuristic_version"]) is not int
            or config["heuristic_version"] != 1
        ):
            fail("Unsupported Shield maiden heuristic version.")
        numeric_ranges = {
            "depth": (1, 5),
            "rollout_depth": (0, 50),
            "iterations": (1, 5000),
            "time_limit": (0.01, 15),
            "exploration": (0, 10),
            "temperature": (0, 5),
            "seed": (0, 2**32 - 1),
        }
        for name, (minimum, maximum) in numeric_ranges.items():
            if name in config:
                value = config[name]
                if (
                    name in {"depth", "iterations", "seed", "rollout_depth"}
                    and type(value) is not int
                ):
                    fail(f"AI {name} must be an integer.")
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not minimum <= value <= maximum
                ):
                    fail(f"AI {name} must be between {minimum} and {maximum}.")
        if "evaluation" in config and config["evaluation"] not in ("heuristic", "neural"):
            fail("Evaluation must be heuristic or neural.")
        if "search_wrapper" in config:
            if algorithm != "neural":
                fail("Search wrappers apply only to trained neural models.")
            if config["search_wrapper"] not in ("mcts", "alpha_beta", "none"):
                fail("Search wrapper must be mcts, alpha_beta or none.")
        resolved = effective_ai_config(config)
        if resolved.get("model_id"):
            if not isinstance(resolved["model_id"], str):
                fail("Model ID must be a string.")
            model_info(resolved["model_id"])
        if resolved["algorithm"] == "neural" and not resolved.get("model_id"):
            fail("The neural opponent requires a registered model.")
        return resolved

    def normalise_advisor(provided: AdvisorRequest | None, ruleset: str, seed: int) -> dict | None:
        if provided is None:
            return None
        from .ai import ai_profiles

        name = provided.name.strip()
        if not name:
            fail("The move advisor needs a name.")
        config = clean_ai_config(provided.config, provided.profile_id)
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
                available = (
                    {model["id"] for model in models_list()}
                    if any(
                        player.get("config", {}).get("model_id")
                        for player in referenced_agents(game)
                    )
                    else set()
                )
                if any(
                    player.get("config", {}).get("model_id") not in available
                    for player in referenced_agents(game)
                    if player.get("config", {}).get("model_id")
                ):
                    fail(
                        "This recorded game uses a deleted model. Start a new game to play another line.",
                        409,
                    )
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

    def insert_account_game(c, account, request: GameRequest, *, seed=None):
        from .ai import ai_profiles
        from .game_records import game_record
        from .security_context import allowed

        if request.mode == "ai-ai" and not allowed(account, "all_data"):
            fail("Administrator permission is required for AI vs AI.", 403)
        ai_sides = set(SIDES) if request.mode == "ai-ai" else set(SIDES) - {request.human_side}
        if set(request.players) - ai_sides:
            fail("Human identity comes from the authenticated account.", 422)
        seed = secrets.randbits(32) if seed is None else seed
        catalog = {p["id"]: p for p in ai_profiles()}
        players = {}
        for side in SIDES:
            if side not in ai_sides:
                players[side] = {
                    "name": str(account.get("display_name") or account["username"])[:80],
                    "account_id": account["id"],
                    "kind": "human",
                    "config": {},
                }
                continue
            profile = catalog[request.players.get(side, OpponentRequest()).profile_id]
            config = effective_ai_config(
                {**profile["parameters"], "algorithm": profile["algorithm"], "seed": seed}
            )
            players[side] = {
                "name": profile["name"],
                "kind": "ai",
                "profile_id": profile["id"],
                "profile_name": profile["name"],
                "config": config,
                "variant_id": ai_variant_id(config),
            }
        game = game_record(request.ruleset, request.mode, request.human_side, players, seed)
        game["creation_policy"] = 1
        c.execute(
            "INSERT INTO games(id,version,created_at,data,owner_account_id) VALUES(?,?,?,?,?)",
            (game["id"], 0, game["created_at"], json.dumps(game), account["id"]),
        )
        return game

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

    @app.get("/api/games")
    def games(ruleset: str | None = None, mode: str | None = None, kind: str | None = None):
        if kind not in (None, "human", "ai"):
            fail("Kind must be human or ai.")
        return [
            response(g)
            for g in store.list_games()
            if (not ruleset or g["ruleset"] == ruleset)
            and (not mode or g["mode"] == mode)
            and (not kind or any(player["kind"] == kind for player in g["players"].values()))
        ]

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
        configuration = effective_ai_config(agent["config"], agent.get("profile_id"))
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
        configuration = effective_ai_config(agent["config"], agent.get("profile_id"))
        if role == "advisor":
            configuration["time_limit"] = settings["time_limit"]
        if "seed" in game:
            configuration["seed"] = (configuration["seed"] + state.ply) % 2**32
        entry = evaluation_entry(game, role, agent, settings, state=state)
        try:
            if configuration.get("model_id") and not state.winner:
                with catalog_lock:
                    available = model_info(configuration["model_id"])
                    configuration["model_path"] = str(model_file(available))
            report = analyze_position(
                state,
                configuration,
                progress=lambda partial: progress(
                    evaluation_entry(game, role, agent, settings, partial)
                ),
            )
            return evaluation_entry(game, role, agent, settings, report)
        except (OSError, HTTPException):
            entry["unavailable_reason"] = "model_unavailable"
        except (ValueError, RuntimeError):
            entry["unavailable_reason"] = "evaluation_failed"
        return entry

    from .analysis_service import AnalysisService

    analyses = AnalysisService(store, evaluate_background)
    app.state.analyses = analyses

    @app.get("/api/games/{game_id}/analytics")
    def get_analytics(game_id: str):
        return analytics_settings(game_or_404(game_id))

    @app.put("/api/games/{game_id}/analytics")
    def set_analytics(game_id: str, request: AnalyticsRequest):
        with catalog_lock:
            game = game_or_404(game_id)
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
        result = current_analysis(game, settings, state=state) or missing_analysis(game)
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
            return analysis_response(
                current_analysis(game, settings, state=state) or missing_analysis(game)
            )
        if request.cursor != game["cursor"]:
            fail("This game changed. Reload it before analyzing again.", 409)
        if request.background:
            return background_analysis(game)
        state = state_of(game)
        key = position_key(state)
        cached = store.game_analysis(game_id, key)
        if cached is not None:
            return analysis_response(cached)
        # A recorded opinion belongs to the original point in play. Never
        # fabricate historical evidence by evaluating with newer/deleted weights.
        if not game.get("analysis_enabled") or game["cursor"] < len(game["moves"]):
            return missing_analysis(game)
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
        if game.get("advisor"):
            agents.append(("advisor", game["advisor"]))
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
                if role == "advisor" and report.get("best_move") is not None:
                    snapshot["hint"] = {"agent_id": "advisor", "move": report["best_move"]}
            except (OSError, HTTPException):
                entry["unavailable_reason"] = "model_unavailable"
            except (ValueError, RuntimeError):
                entry["unavailable_reason"] = "evaluation_failed"
            snapshot["evaluations"].append(entry)
        try:
            return store.save_game_analysis(game_id, request.version, snapshot)
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

        try:
            with store.connection() as c:
                c.execute("BEGIN")
                account = online._identity(c, session_identity.get())
                if not allowed(account, "all_data"):
                    online._participant(c, account["id"], identifier)
                row, game = online._load(c, identifier)
                if game.status.value != "completed" or game.result is None:
                    fail("Analysis is available only for completed online games.", 403)
                players = {}
                for side in SIDES:
                    record = c.execute(
                        "SELECT username,display_name FROM accounts WHERE id=?",
                        (row[side + "_id"],),
                    ).fetchone()
                    players[side] = {
                        "name": str(record["display_name"] or record["username"])[:80],
                        "kind": "human",
                        "config": {},
                    }
                moves = [
                    {"source": item["source"], "target": item["target"]}
                    for item in c.execute(
                        "SELECT source,target FROM online_moves WHERE game_id=? ORDER BY ply",
                        (identifier,),
                    )
                ]
        except OnlineError as error:
            fail(str(error), error.status)
        return {
            "id": identifier,
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
            current_analysis(game, settings, state=state) or missing_analysis(game)
        )

    @app.delete("/api/games/{game_id}")
    def delete_game(
        game_id: str, version: str = Query(pattern=r"^(0|[1-9][0-9]*)$", max_length=16)
    ):
        with catalog_lock:
            expected_version = int(version)
            game_or_404(game_id, expected_version)
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
        game = game_or_404(game_id, request.version)
        if game["players"][state_of(game).turn]["kind"] != "human":
            fail("It is the computer's turn.", 409)
        return append_move(game, Move(request.source, request.target), request.version)

    @app.post("/api/games/{game_id}/ai-move")
    def ai_move(game_id: str, request: VersionRequest):
        from .ai import analysis_from_search, choose_move

        game = game_or_404(game_id, request.version)
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
            model = model_info(config.pop("model_id"))
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
        if request.cursor > len(game["moves"]):
            fail("This move is outside the recorded game.")
        state = state_at(game, request.cursor)
        game["cursor"] = request.cursor
        game["state"] = state.to_dict()
        return persist(game, request.version)

    @app.post("/api/games/{game_id}/undo")
    def undo(game_id: str, request: VersionRequest):
        game = game_or_404(game_id, request.version)
        if not game["cursor"]:
            fail("There is no move to undo.")
        return seek(game_id, SeekRequest(version=request.version, cursor=game["cursor"] - 1))

    @app.post("/api/games/{game_id}/redo")
    def redo(game_id: str, request: VersionRequest):
        game = game_or_404(game_id, request.version)
        if game["cursor"] >= len(game["moves"]):
            fail("There is no move to redo.")
        return seek(game_id, SeekRequest(version=request.version, cursor=game["cursor"] + 1))

    @app.post("/api/games/{game_id}/restart")
    def restart(game_id: str, request: VersionRequest):
        with catalog_lock:
            prior = game_or_404(game_id, request.version)
            if recorded_outcome(prior)["winner"] or state_of(prior).winner:
                fail("Completed games cannot be restarted. Start a new game instead.", 409)
            if prior.get("creation_policy") != 1:
                fail(
                    "Start a new account game with the supported opponents; this saved setup is read/play compatible only.",
                    409,
                )
            profiles = {
                side: OpponentRequest(profile_id=p["profile_id"])
                for side, p in prior["players"].items()
                if p["kind"] == "ai"
            }
            setup = GameRequest(
                ruleset=prior["ruleset"],
                mode=prior["mode"],
                human_side=prior["human_side"],
                players=profiles,
            )
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
                game = insert_account_game(c, account, setup, seed=seed)
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
            for g in store.list_games()
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
            for side, player in game["players"].items():
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
            "recent_games": [response(g) for g in selected[:10]],
        }

    def tournament_or_404(identifier: str) -> dict:
        tournament = tournaments.read(identifier)
        if tournament is None:
            fail("Tournament not found.", 404)
        return tournament

    @app.get("/api/tournaments")
    def list_tournaments():
        return [tournaments.public(item, summary=True) for item in tournaments.list()]

    @app.post("/api/tournaments", status_code=201)
    def create_tournament(request: TournamentRequest):
        from .ai import ai_profiles

        if not request.name.strip():
            fail("Tournament names must contain 1–80 characters.")
        if len(set(request.rulesets)) != len(request.rulesets):
            fail("Select each tournament battlefield only once.")
        if request.games % 2:
            fail("Use an even number of games so both opponents play each side equally.")
        with catalog_lock:
            profiles = {item["algorithm"]: item for item in ai_profiles()}
            players = []
            for provided in request.players:
                provided_config = provided.config
                if provided.profile_id in {"baseline", "shield_maiden", "alpha_beta", "mcts"}:
                    preset = profiles[provided.profile_id]
                    provided_config = {**preset["parameters"], "algorithm": preset["algorithm"]}
                config = clean_ai_config(provided_config, provided.profile_id)
                # The existing balanced runner uses a shared tournament seed,
                # increasing it by game and ply. Snapshot that base value
                # rather than retaining a per-opponent seed that is not used.
                config["seed"] = request.seed
                profile = profiles[config["algorithm"]]
                snapshot = {
                    "name": profile["name"],
                    "profile_id": profile["id"],
                    "profile_name": profile["name"],
                    "config": config,
                    "variant_id": ai_variant_id(config),
                    "agent_rune": agent_rune({"kind": "ai", "config": config}),
                }
                if config.get("model_id"):
                    model = model_info(config["model_id"])
                    if any(ruleset != model["ruleset"] for ruleset in request.rulesets):
                        fail("A selected model must match every tournament battlefield.")
                    model_file(model)
                    snapshot["name"] = snapshot["model_name"] = model["name"]
                    # Retain provenance after catalog renames/deletion, without server paths.
                    snapshot["model"] = {
                        key: value
                        for key, value in model.items()
                        if key
                        in {
                            "id",
                            "name",
                            "ruleset",
                            "rune",
                            "version",
                            "iteration",
                            "created_at",
                            "architecture",
                            "self_play_games",
                            "metrics",
                            "evaluation",
                        }
                    }
                    if isinstance(model.get("training_config"), dict):
                        snapshot["model"]["training_config"] = {
                            key: value
                            for key, value in model["training_config"].items()
                            if key in training_fields - {"resume"}
                        }
                players.append(snapshot)
            config = {
                "players": players,
                "rulesets": sorted(request.rulesets, key=lambda item: RULESETS[item].size),
                "games": request.games,
                "max_plies": request.max_plies,
                "seed": request.seed,
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
        return {"validated": True, "ruleset": game["ruleset"], "states": states}

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
            for game in store.list_games()
            if not recorded_outcome(game)["winner"]
            and any(
                player.get("config", {}).get("model_id") == model_id
                for player in referenced_agents(game)
            )
        ]

    def running_model_tournaments(model_id: str) -> list[dict]:
        return [
            {key: tournament[key] for key in ("id", "name", "status")}
            for tournament in tournaments.list()
            if tournament["status"] == "running"
            and tournaments.alive(tournament)
            and any(
                player.get("config", {}).get("model_id") == model_id
                for player in tournament["config"]["players"]
            )
        ]

    def model_usage_token(model_id: str, games: list[dict]) -> str:
        versions = sorted((game["id"], game["version"]) for game in games)
        return hashlib.sha256(json.dumps([model_id, versions]).encode()).hexdigest()

    @app.get("/api/models/{model_id}/usage")
    def model_usage(model_id: str):
        with catalog_lock:
            model_info(model_id)
            games = unfinished_model_games(model_id)
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
                            side: {"name": player["name"], "rune": agent_rune(player)}
                            for side, player in game["players"].items()
                        },
                    }
                    for game in games
                ],
                "running_tournaments": running_model_tournaments(model_id),
            }

    @app.delete("/api/models/{model_id}")
    def delete_model(
        model_id: str,
        discard_unfinished: bool = False,
        usage_token: str | None = Query(default=None, pattern=r"^[0-9a-f]{64}$"),
    ):
        with catalog_lock:
            model = model_info(model_id)
            if running_model_tournaments(model_id):
                fail(
                    "This model is used by a running tournament. Stop it before deleting the model.",
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
        # Identifiers are server-generated hex; this also protects reads from URL traversal.
        if len(identifier) != 32 or any(c not in "0123456789abcdef" for c in identifier):
            fail("Training job not found.", 404)
        return root / "jobs" / identifier

    def read_job(identifier: str) -> dict:
        # Serialize reads that reconcile worker state with launch/deletion so a
        # late response cannot reinsert a deleted record.
        with job_lock:
            return read_job_locked(identifier)

    def read_job_locked(identifier: str, *, reconcile: bool = True) -> dict:
        if reconcile:
            remote_training.reconcile()
        job = store.get_job(identifier)
        if job is None:
            fail("Training job not found.", 404)
        if job.get("execution") == "remote":
            return remote_training.public_job(job)
        directory = job_path(identifier)
        for filename, key in (("progress.json", "progress"), ("result.json", "result")):
            path = directory / filename
            if path.exists():
                try:
                    job[key] = json.loads(path.read_text())
                except (json.JSONDecodeError, OSError):
                    pass
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
        if logfile.exists():
            with logfile.open("rb") as handle:
                handle.seek(max(0, logfile.stat().st_size - 16000))
                job["logs"] = handle.read().decode(errors="replace")
        return job

    training_fields = {
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

    @app.get("/api/training/jobs")
    def training_jobs():
        with job_lock:
            remote_training.reconcile()
            return [read_job_locked(j["id"], reconcile=False) for j in store.jobs()]

    @app.get("/api/training/jobs/{job_id}/export")
    def export_training_job(job_id: str):
        from .job_archives import export_job, TERMINAL

        descriptor, filename = tempfile.mkstemp(suffix=".zip", prefix="hnefatafl-job-")
        os.close(descriptor)
        archive = Path(filename)
        try:
            with job_lock, catalog_lock:
                # Export reads retained state without reconciling or modifying the row.
                directory = job_path(job_id)
                job = store.get_job(job_id)
                if job is None:
                    fail("Training job not found.", 404)
                if job.get("status") not in TERMINAL or job_worker_alive(job):
                    fail("Stop this training job before exporting its data.", 409)
                export_job(job, directory, registry(), archive, package_version)
            return FileResponse(
                archive,
                filename=f"hnefatafl-job-{job_id}.zip",
                media_type="application/zip",
                background=BackgroundTask(archive.unlink, missing_ok=True),
            )
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
    def import_training_job(file: UploadFile = File(...)):
        import zipfile
        import sqlite3
        import zlib
        from .job_archives import import_job, MAX_ARCHIVE_BYTES

        try:
            with tempfile.TemporaryDirectory(prefix="hnefatafl-upload-") as temporary:
                archive = Path(temporary) / "job.zip"
                count = 0
                with archive.open("wb") as output:
                    while chunk := file.file.read(1024 * 1024):
                        count += len(chunk)
                        if count > MAX_ARCHIVE_BYTES:
                            fail("Job archives are limited to 512 MiB.", 413)
                        output.write(chunk)
                with job_lock, catalog_lock:
                    result = import_job(
                        archive,
                        root,
                        store,
                        resume_info=lambda job, directory: resume_checkpoint(
                            job["id"], job=job, directory=directory
                        )[0],
                    )
                    return result
        except (
            ValueError,
            TypeError,
            KeyError,
            RuntimeError,
            EOFError,
            zipfile.BadZipFile,
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

    def job_worker_alive(job: dict) -> bool:
        if job.get("execution") == "remote":
            return job["status"] in {"running", "cancelling"}
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
        with job_lock:
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
            allowed = (
                training_fields
                if kind == "train"
                else {"trials", "study_name", "seed", "search_space", "training", "resume_study"}
            )
            if set(config) - allowed:
                fail("Unsupported training configuration field.")
            identifier = uuid4().hex
            directory = job_path(identifier)

            def validate_training(values: dict) -> dict:
                if not isinstance(values, dict) or set(values) - training_fields:
                    fail("Unsupported training configuration field.")
                values = dict(values)
                resume = values.pop("resume", None)
                if resume:
                    # Resolve only opaque server-issued tokens, never arbitrary paths.
                    if not isinstance(resume, str):
                        fail("Resume must name a saved training job checkpoint.")
                    found = next((m for m in models_list() if m["id"] == resume), None)
                    if found:
                        fail(
                            "Registered models are for play. Resume using a training job's last checkpoint."
                        )
                    elif ":" in resume:
                        prior_id, filename = resume.split(":", 1)
                        if filename != "last.pt":
                            fail(
                                "Only last.pt contains optimizer and replay state for training resumption."
                            )
                        checkpoint = job_path(prior_id) / filename
                        if not checkpoint.is_file():
                            fail("The saved checkpoint was not found.")
                        values["resume"] = str(checkpoint)
                    else:
                        if resume != "last.pt":
                            fail(
                                "Resume requires a job checkpoint token or an unambiguous last.pt."
                            )
                        matches = [p for p in (root / "jobs").glob("*/last.pt")]
                        if len(matches) != 1:
                            fail("The saved checkpoint was not found or its name is ambiguous.")
                        values["resume"] = str(matches[0].resolve())
                values.update(output_dir=str(directory), registry_dir=str(root / "models"))
                try:
                    from .models import load_checkpoint, resolve_device
                    from .training import normalize_config

                    result = normalize_config(values)
                    if result["device"] not in {"auto", "cpu", "cuda", "mps"}:
                        fail("Training device must be auto, cpu, cuda or mps.")
                    if execution == "local":
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
                        for name in ("optimizer", "seed"):
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
                training = validate_training(
                    config.get("training", {"preset": "smoke", "ruleset": "brandubh"})
                )
                if training["evaluation_opponent"] != "shield_bearer":
                    fail("Optuna requires the comparable fixed Shield bearer objective")
                trials = config.get("trials", 4)
                study_name = config.get("study_name", "hnefatafl")
                if not isinstance(study_name, str) or not 1 <= len(study_name) <= 100:
                    fail("study_name must contain 1–100 characters.")
                seed = config.get("seed", 0)
                if type(seed) is not int or not 0 <= seed < 2**32:
                    fail("seed must be an unsigned 32-bit integer.")
                if type(trials) is not int or not 1 <= trials <= 10000:
                    fail("trials must be an integer between 1 and 10000.")
                tunable = {
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
                search = config.get("search_space")
                if search is not None:
                    if not isinstance(search, dict) or not search or set(search) - tunable:
                        fail("Unsupported hyperparameter search field.")
                    # Check each extreme against the actual training validator.
                    for name, distribution in search.items():
                        if not isinstance(distribution, dict):
                            fail("A search distribution must be an object.")
                        distribution_kind = distribution.get("type", "float")
                        if not isinstance(distribution_kind, str):
                            fail("A search distribution type must be a string.")
                        if set(distribution) - {"type", "choices", "low", "high", "step", "log"}:
                            fail("Unsupported search distribution option.")
                        if distribution_kind == "categorical":
                            choices = distribution.get("choices")
                            if (
                                not isinstance(choices, list)
                                or not choices
                                or any(
                                    isinstance(v, bool) or not isinstance(v, (int, float))
                                    for v in choices
                                )
                            ):
                                fail("A categorical search needs numeric choices.")
                            if set(distribution) - {"type", "choices"}:
                                fail("Categorical search accepts only type and choices.")
                        elif distribution_kind in {"int", "float"}:
                            choices = [distribution.get("low"), distribution.get("high")]
                            if (
                                any(
                                    isinstance(v, bool) or not isinstance(v, (int, float))
                                    for v in choices
                                )
                                or choices[0] > choices[1]
                            ):
                                fail("Invalid search-space bounds.")
                            logarithmic = distribution.get("log", False)
                            if type(logarithmic) is not bool or (logarithmic and choices[0] <= 0):
                                fail(
                                    "Logarithmic search requires positive bounds and a boolean log flag."
                                )
                            if distribution_kind == "int":
                                step = distribution.get("step", 1)
                                if (
                                    any(type(v) is not int for v in choices)
                                    or type(step) is not int
                                    or step < 1
                                    or (logarithmic and step != 1)
                                ):
                                    fail(
                                        "Integer searches require integer bounds, a positive integer step, and step 1 for logarithmic sampling."
                                    )
                            elif "step" in distribution:
                                fail("Float searches do not accept a step option.")
                        else:
                            fail("Unknown search-space distribution.")
                        for value in choices:
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
                resume_study = config.pop("resume_study", None)
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
                    config["storage"] = "sqlite:///" + str(database)
                config.update(training=training, output_dir=str(directory))
            if execution == "remote":
                return remote_training.enqueue(identifier, config, worker_id)
            directory.mkdir()
            config_file = directory / "config.json"
            config_file.write_text(json.dumps(config, indent=2))
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

        immutable = {"ruleset", "channels", "blocks", "seed", "optimizer"}
        editable = training_fields - immutable - {"resume"}
        if "iterations" not in request or set(request) - (editable | {"execution", "worker_id"}):
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
