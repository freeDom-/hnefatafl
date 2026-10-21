"""Server-authoritative local game API, SQLite history and isolated training jobs."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import signal
import subprocess
import sys
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Literal, NoReturn
from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from .engine import Move, RULESETS, State, apply_move, legal_moves, new_game
from .storage import ConflictError, Store


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SIDES = ("attackers", "defenders")


def effective_ai_config(configuration: dict, profile_id: str | None = None) -> dict:
    """Resolve actual search defaults, including historical profile semantics.

    Older requests merged a profile only when no algorithm was supplied. A
    stored explicit algorithm therefore uses the engine defaults for missing
    fields; retroactively applying profile defaults would mislabel old games.
    Only effective parameters enter the identity, not leftover UI controls for
    algorithms which are no longer selected.
    """
    from .ai import ai_profiles

    raw = dict(configuration)
    if profile_id and "algorithm" not in raw:
        profile = next((p for p in ai_profiles() if p["id"] == profile_id), None)
        if profile:
            raw = {**profile["parameters"], **raw, "algorithm": profile["algorithm"]}
    algorithm = raw.get("algorithm", "baseline")
    if algorithm == "alphabeta":
        algorithm = "alpha_beta"
    result = {
        "algorithm": algorithm,
        "seed": int(raw.get("seed", 0)),
        "time_limit": float(raw.get("time_limit", 2.0)),
        "evaluation": "heuristic",
    }
    if algorithm == "alpha_beta":
        result["depth"] = int(raw.get("depth", 2))
    elif algorithm in {"mcts", "neural"}:
        result.update(
            iterations=int(raw.get("iterations", 80)),
            rollout_depth=int(raw.get("rollout_depth", 8)),
            exploration=float(raw.get("exploration", 1.4)),
            temperature=float(raw.get("temperature", 0.0)),
        )
    if algorithm in {"alpha_beta", "mcts", "neural"} and raw.get("model_id"):
        result.update(model_id=raw["model_id"], evaluation="neural")
    return result


def ai_variant_id(config: dict) -> str:
    encoded = json.dumps(config, sort_keys=True, separators=(",", ":"), allow_nan=False)
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


class GameRequest(BaseModel):
    ruleset: Literal["tablut", "brandubh", "copenhagen"] = "tablut"
    mode: Literal["human-human", "human-ai", "ai-ai"] = "human-human"
    human_side: Literal["attackers", "defenders"] = "defenders"
    players: dict[str, dict[str, Any]] = Field(default_factory=dict)


class PlayerRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class TournamentPlayerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    profile_id: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)


class TournamentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    rulesets: list[Literal["brandubh", "tablut", "copenhagen"]] = Field(
        default=["brandubh"], min_length=1, max_length=3
    )
    players: list[TournamentPlayerRequest] = Field(min_length=2, max_length=2)
    games: StrictInt = Field(default=2, ge=2, le=100)
    max_plies: StrictInt = Field(default=300, ge=1, le=1000)
    seed: StrictInt = Field(default=0, ge=0, le=2**32 - 1)


def create_app(data_dir: str | Path | None = None) -> FastAPI:
    try:
        package_version = version("hnefatafl")
    except PackageNotFoundError:
        package_version = "0+local"
    app = FastAPI(title="Hnefatafl", version=package_version)
    root = Path(data_dir or os.environ.get("HNEFATAFL_DATA_DIR", "data")).resolve()
    root.mkdir(parents=True, exist_ok=True)
    (root / "jobs").mkdir(exist_ok=True)
    (root / "models").mkdir(exist_ok=True)
    store = Store(root / "hnefatafl.sqlite3")
    from .tournaments import TournamentManager

    tournaments = TournamentManager(store, root)
    app.state.store = store
    app.state.data_dir = root
    processes: dict[str, subprocess.Popen] = {}
    job_lock = threading.Lock()
    # Catalog changes and new references must be atomic with respect to each
    # other in the documented single API-worker deployment.
    catalog_lock = threading.RLock()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        await asyncio.to_thread(tournaments.shutdown)
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

    def recorded_outcome(game: dict) -> dict:
        """The end of the retained record is independent of a replay cursor."""
        summary = game.get("recorded_result")
        if isinstance(summary, dict):
            return summary
        if "recorded_result" in game:
            return {"winner": None, "reason": None, "plies": len(game["moves"])}
        # Backward-compatible derivation for databases from early pre-releases.
        terminal = (
            state_of(game)
            if game["cursor"] == len(game["moves"])
            else state_at(game, len(game["moves"]))
        )
        return {
            "winner": terminal.winner,
            "reason": terminal.reason,
            "plies": terminal.ply,
            "ended_at": game.get("ended_at"),
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
            result["players"][side] = player
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
        )
        return result

    def persist(game: dict, version: int) -> dict:
        try:
            store.update_game(game, version)
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
            "alpha_beta",
            "mcts",
            "neural",
        }:
            fail("Unknown AI algorithm.")
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
        resolved = effective_ai_config(config)
        if resolved.get("model_id"):
            if not isinstance(resolved["model_id"], str):
                fail("Model ID must be a string.")
            model_info(resolved["model_id"])
        if resolved["algorithm"] == "neural" and not resolved.get("model_id"):
            fail("The neural opponent requires a registered model.")
        return resolved

    def normalise_players(request: GameRequest) -> dict:
        result: dict[str, dict[str, Any]] = {}
        for side in SIDES:
            is_ai = request.mode == "ai-ai" or (
                request.mode == "human-ai" and side != request.human_side
            )
            provided = request.players.get(side, {})
            kind = "ai" if is_ai else "human"
            player_id = provided.get("player_id")
            name = str(provided.get("name") or ("Computer" if is_ai else side.title())).strip()
            if not name or len(name) > 80:
                fail("Player names must contain 1–80 characters.")
            if player_id and kind == "human":
                profile = next((p for p in store.players() if p["id"] == player_id), None)
                if not profile:
                    fail("Unknown player profile.")
                name = profile["name"]
            elif kind == "human":
                existing = next((p for p in store.players() if p["name"] == name), None)
                if existing:
                    player_id = existing["id"]
                else:
                    player_id = uuid4().hex
                    store.create_player({"id": player_id, "name": name, "created_at": now()})
            result[side] = {
                "name": name,
                "kind": kind,
                "player_id": player_id if kind == "human" else None,
                "profile_id": provided.get("profile_id", "baseline") if kind == "ai" else None,
                "config": clean_ai_config(
                    provided.get("config"), provided.get("profile_id", "baseline")
                )
                if kind == "ai"
                else {},
            }
            if is_ai:
                from .ai import ai_profiles

                # Manual algorithm selection must not inherit an unrelated
                # profile label left behind by the setup form.
                algorithm = result[side]["config"]["algorithm"]
                profile = next(p for p in ai_profiles() if p["algorithm"] == algorithm)
                result[side]["profile_id"] = profile["id"]
                result[side]["profile_name"] = profile["name"]
                result[side]["variant_id"] = ai_variant_id(result[side]["config"])
                model_id = result[side]["config"].get("model_id")
                if model_id:
                    model = model_info(model_id)
                    if model["ruleset"] != request.ruleset:
                        fail("This model is for a different ruleset.")
                    result[side]["model_name"] = model["name"]
        return result

    def append_move(game: dict, move: Move, version: int) -> dict:
        with catalog_lock:
            if game["cursor"] < len(game["moves"]):
                available = (
                    {model["id"] for model in models_list()}
                    if any(
                        player.get("config", {}).get("model_id")
                        for player in game["players"].values()
                    )
                    else set()
                )
                if any(
                    player.get("config", {}).get("model_id") not in available
                    for player in game["players"].values()
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
            return persist(game, version)

    @app.get("/api/health")
    def health():
        return {"status": "ok", "database": "sqlite", "schema_version": 3}

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

        return ai_profiles()

    @app.post("/api/games", status_code=201)
    def create_game(request: GameRequest):
        with catalog_lock:
            state = new_game(request.ruleset)
            game = {
                "id": uuid4().hex,
                "ruleset": request.ruleset,
                "ruleset_version": 1,
                "mode": request.mode,
                "human_side": request.human_side,
                "players": normalise_players(request),
                "state": state.to_dict(),
                "moves": [],
                "cursor": 0,
                "version": 0,
                "created_at": now(),
                "ended_at": None,
                "recorded_result": None,
            }
            store.create_game(game)
            return response(game)

    @app.get("/api/games")
    def games(ruleset: str | None = None, mode: str | None = None):
        return [
            response(g)
            for g in store.list_games()
            if (not ruleset or g["ruleset"] == ruleset) and (not mode or g["mode"] == mode)
        ]

    @app.get("/api/games/{game_id}")
    def get_game(game_id: str):
        return response(game_or_404(game_id))

    @app.get("/api/games/{game_id}/replay")
    def game_replay(game_id: str):
        game = game_or_404(game_id)
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
        return {"game_id": game_id, "validated": True, "cursor": game["cursor"], "states": states}

    @app.post("/api/games/{game_id}/moves")
    def make_move(game_id: str, request: MoveRequest):
        game = game_or_404(game_id, request.version)
        if game["players"][state_of(game).turn]["kind"] != "human":
            fail("It is the computer's turn.", 409)
        return append_move(game, Move(request.source, request.target), request.version)

    @app.post("/api/games/{game_id}/ai-move")
    def ai_move(game_id: str, request: VersionRequest):
        from .ai import choose_move

        game = game_or_404(game_id, request.version)
        state = state_of(game)
        player = game["players"][state.turn]
        if player["kind"] != "ai":
            fail("It is a human player's turn.", 409)
        if state.winner:
            fail("The game is already finished.")
        config = clean_ai_config(player["config"], player["profile_id"])
        if config.get("model_id"):
            model = model_info(config.pop("model_id"))
            if model.get("ruleset") != game["ruleset"]:
                fail("This model is for a different ruleset.")
            config["model_path"] = str(model_file(model))
        try:
            move = choose_move(state, config)
        except (ValueError, FileNotFoundError) as exc:
            fail(str(exc))
        return append_move(game, move, request.version)

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
            game = game_or_404(game_id, request.version)
            active = {player["id"] for player in store.players()}
            for player in game["players"].values():
                if player["kind"] == "human" and player.get("player_id") not in active:
                    fail(
                        "Choose active players in a new game; a previous profile was deleted.", 409
                    )
                model_id = player.get("config", {}).get("model_id")
                if model_id and not any(model["id"] == model_id for model in models_list()):
                    fail(
                        "Choose an available model in a new game; the previous model was deleted.",
                        409,
                    )
            game.update(
                id=uuid4().hex,
                state=new_game(game["ruleset"]).to_dict(),
                moves=[],
                cursor=0,
                version=0,
                created_at=now(),
                ended_at=None,
                recorded_result=None,
            )
            store.create_game(game)
            return response(game)

    @app.get("/api/players")
    def players():
        return store.players()

    @app.post("/api/players", status_code=201)
    def create_player(request: PlayerRequest):
        name = request.name.strip()
        if not name:
            fail("A player needs a display name.")
        with catalog_lock:
            return store.create_player({"id": uuid4().hex, "name": name, "created_at": now()})

    @app.delete("/api/players/{player_id}")
    def delete_player(player_id: str):
        with catalog_lock:
            if not store.delete_player(player_id, now()):
                fail("Player profile not found.", 404)
        return {"deleted": True, "id": player_id}

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
                not player_id or any(p.get("player_id") == player_id for p in g["players"].values())
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
                identifier = (
                    player.get("player_id")
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
                    },
                )
                item["games"] += 1
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
                name = provided.name.strip()
                if not name:
                    fail("Opponent names must contain 1–80 characters.")
                config = clean_ai_config(provided.config, provided.profile_id)
                # The existing balanced runner uses a shared tournament seed,
                # increasing it by game and ply. Snapshot that base value
                # rather than retaining a per-opponent seed that is not used.
                config["seed"] = request.seed
                profile = profiles[config["algorithm"]]
                snapshot = {
                    "name": name,
                    "profile_id": profile["id"],
                    "profile_name": profile["name"],
                    "config": config,
                    "variant_id": ai_variant_id(config),
                }
                if config.get("model_id"):
                    model = model_info(config["model_id"])
                    if any(ruleset != model["ruleset"] for ruleset in request.rulesets):
                        fail("A selected model must match every tournament battlefield.")
                    model_file(model)
                    snapshot["model_name"] = model["name"]
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

    # Model helpers remain constrained to the registry root; clients never pass paths.
    def models_list() -> list[dict]:
        return registry().list()

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
        return models_list()

    @app.delete("/api/models/{model_id}")
    def delete_model(model_id: str):
        with catalog_lock:
            model = model_info(model_id)
            if any(
                tournament["status"] == "running"
                and tournaments.alive(tournament)
                and any(
                    player.get("config", {}).get("model_id") == model_id
                    for player in tournament["config"]["players"]
                )
                for tournament in tournaments.list()
            ):
                fail(
                    "This model is used by a running tournament. Stop it before deleting the model.",
                    409,
                )
            if any(
                not recorded_outcome(game)["winner"]
                and any(
                    player.get("config", {}).get("model_id") == model_id
                    for player in game["players"].values()
                )
                for game in store.list_games()
            ):
                fail(
                    "This model is used by an unfinished game. Finish that game before deleting it.",
                    409,
                )
            store.archive_model(
                {key: model[key] for key in ("id", "name", "ruleset", "version") if key in model}
            )
            try:
                registry().delete(model_id)
            except ValueError as exc:
                fail(str(exc), 404)
            return {"deleted": True, "id": model_id}

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
            return result
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
        job = store.get_job(identifier)
        if job is None:
            fail("Training job not found.", 404)
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
        if checkpoint.is_file():
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

    @app.get("/api/training/jobs")
    def training_jobs():
        return [read_job(j["id"]) for j in store.jobs()]

    @app.get("/api/training/jobs/{job_id}")
    def training_job(job_id: str):
        return read_job(job_id)

    @app.post("/api/training/jobs", status_code=201)
    def launch_training(config: dict[str, Any]):
        with job_lock:
            if any(
                read_job(j["id"])["status"] in {"running", "cancelling"} for j in store.jobs()
            ) or any(process.poll() is None for process in list(processes.values())):
                fail("A training job is already running. Stop it before starting another.", 409)
            config = dict(config)
            kind = config.pop("kind", "train")
            if kind not in ("train", "tune"):
                fail("Training kind must be train or tune.")
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
                "exploration",
                "temperature",
                "dirichlet_alpha",
                "dirichlet_fraction",
                "replay_buffer_size",
                "weight_decay",
                "checkpoint_frequency",
                "evaluation_frequency",
                "evaluation_games",
                "evaluation_iterations",
                "max_game_plies",
                "evaluation_max_plies",
                "seed",
                "device",
                "mixed_precision",
                "cpu_threads",
                "resume",
                "model_name",
            }
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
                    resolve_device(result["device"])
                    if result.get("resume"):
                        model, checkpoint_data = load_checkpoint(result["resume"])
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

    @app.post("/api/training/jobs/{job_id}/cancel")
    def cancel_training(job_id: str):
        job = read_job(job_id)
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
