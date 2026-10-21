"""Durable, process-isolated tournament execution and worker lifecycle.

Only completed games enter a report. A stopped search discards its unfinished
current game rather than manufacturing a rule draw or a budget truncation.
"""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .storage import Store


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


class TournamentManager:
    """Own workers for the supported single API-process deployment."""

    def __init__(self, store: Store, root: Path):
        self.store = store
        self.root = root
        self.directory = root / "tournaments"
        self.directory.mkdir(exist_ok=True)
        self.processes: dict[str, subprocess.Popen] = {}
        self.lock = threading.RLock()

    def alive(self, tournament: dict) -> bool:
        process = self.processes.get(tournament["id"])
        if process is not None:
            return process.poll() is None
        pid = tournament.get("pid")
        if not pid:
            return False
        try:
            arguments = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
            return (
                b"hnefatafl.tournaments" in arguments
                and str(self.store.path).encode() in arguments
                and tournament["id"].encode() in arguments
            )
        except OSError:
            return False

    def read(self, identifier: str) -> dict | None:
        with self.lock:
            tournament = self.store.get_tournament(identifier)
            if tournament is None:
                return None
            if tournament["status"] == "running" and not self.alive(tournament):
                self.store.update_tournament(
                    identifier,
                    {
                        "status": "failed",
                        "ended_at": timestamp(),
                        "error": "The tournament worker stopped. Completed games have been kept.",
                    },
                    running=True,
                )
                tournament = self.store.get_tournament(identifier)
                assert tournament is not None
            process = self.processes.get(identifier)
            if process is not None and tournament["status"] != "running":
                # A worker publishes its final state immediately before Python
                # teardown. Reap it before presenting a deletable terminal row.
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
            if process is not None and process.poll() is not None:
                self.processes.pop(identifier, None)
            return tournament

    @staticmethod
    def public(tournament: dict, *, summary: bool = False) -> dict:
        return {
            key: value
            for key, value in tournament.items()
            if key != "pid" and not (summary and key == "result")
        }

    def list(self) -> list[dict]:
        with self.lock:
            return [
                record
                for item in self.store.tournaments()
                if (record := self.read(item["id"])) is not None
            ]

    def launch(self, name: str, config: dict) -> dict:
        """Caller holds the catalog lock, keeping model references valid."""
        with self.lock:
            if any(item["status"] == "running" and self.alive(item) for item in self.list()):
                raise ValueError(
                    "A tournament is already running. Stop it before starting another."
                )
            identifier = uuid4().hex
            directory = self.directory / identifier
            directory.mkdir()
            player_count = len(config["players"])
            pair_count = player_count * (player_count - 1) // 2
            tournament = {
                "id": identifier,
                "name": name,
                "status": "running",
                "created_at": timestamp(),
                "ended_at": None,
                "config": config,
                "progress": {
                    "completed_games": 0,
                    "total_games": len(config["rulesets"]) * config["games"] * pair_count,
                },
                "result": {
                    "players": [player["config"] for player in config["players"]],
                    "scores": [0.0] * player_count,
                    "standings": [
                        dict(
                            player=index, wins=0, losses=0, draws=0, truncated=0, games=0, score=0.0
                        )
                        for index in range(player_count)
                    ],
                    "by_ruleset": {
                        ruleset: {
                            "wins": [0] * player_count,
                            "draws": 0,
                            "truncated": 0,
                            "games": 0,
                        }
                        for ruleset in config["rulesets"]
                    },
                    "games": [],
                    "truncated": 0,
                    "average_game_length": 0.0,
                    "average_decision_time": 0.0,
                    "seed": config["seed"],
                },
                "error": None,
            }
            self.store.save_tournament(tournament)
            try:
                with (directory / "worker.log").open("wb") as output:
                    process = subprocess.Popen(
                        [
                            sys.executable,
                            "-m",
                            "hnefatafl.tournaments",
                            "--database",
                            str(self.store.path),
                            "--id",
                            identifier,
                        ],
                        stdout=output,
                        stderr=subprocess.STDOUT,
                        cwd=Path(__file__).resolve().parents[1],
                        start_new_session=True,
                    )
                self.processes[identifier] = process
                self.store.update_tournament(identifier, {"pid": process.pid})
            except OSError:
                self.store.update_tournament(
                    identifier,
                    {
                        "status": "failed",
                        "ended_at": timestamp(),
                        "error": "The tournament worker could not start.",
                    },
                )
            record = self.read(identifier)
            assert record is not None
            return self.public(record)

    def cancel(self, identifier: str) -> dict | None:
        with self.lock:
            tournament = self.read(identifier)
            if tournament is None or not self.alive(tournament):
                return tournament
            process = self.processes.get(identifier)
            pid = process.pid if process is not None else tournament["pid"]
            try:
                os.killpg(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            deadline = time.monotonic() + 2
            while self.alive(tournament) and time.monotonic() < deadline:
                time.sleep(0.02)
            if self.alive(tournament):
                try:
                    os.killpg(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                deadline = time.monotonic() + 5
                while self.alive(tournament) and time.monotonic() < deadline:
                    time.sleep(0.02)
                if self.alive(tournament):
                    raise ValueError(
                        "The tournament worker has not stopped yet. Try stopping it again."
                    )
            if process is not None:
                process.wait(timeout=5)
                self.processes.pop(identifier, None)
            # Merge after the worker has stopped; never overwrite its last
            # successfully persisted result with an older API snapshot.
            self.store.update_tournament(
                identifier, {"status": "cancelled", "ended_at": timestamp()}, running=True
            )
            return self.store.get_tournament(identifier)

    def delete(self, identifier: str) -> bool:
        with self.lock:
            tournament = self.read(identifier)
            if tournament is None:
                return False
            if self.alive(tournament):
                raise ValueError("Stop this tournament before deleting it.")
            # IDs used for filesystem access are generated server-side and
            # must match the saved identity; URL input never supplies a path.
            if len(identifier) != 32 or any(c not in "0123456789abcdef" for c in identifier):
                return self.store.delete_tournament(identifier)
            directory = self.directory / identifier
            if directory.is_symlink():
                directory.unlink()
            elif directory.exists():
                shutil.rmtree(directory)
            return self.store.delete_tournament(identifier)

    def shutdown(self) -> None:
        with self.lock:
            for tournament in self.list():
                if self.alive(tournament):
                    self.cancel(tournament["id"])


def run_worker(database: Path, identifier: str) -> int:
    from .ai import tournament as run_tournament

    store = Store(database)
    record = store.get_tournament(identifier)
    if record is None or record["status"] != "running":
        return 1
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    config = dict(record["config"])
    snapshots = config["players"]
    configs = [dict(player["config"]) for player in snapshots]

    def report(result: dict) -> None:
        # Filesystem paths are worker implementation details, including when
        # evaluating a registered model. Persist only the public configurations.
        result = {**result, "players": [player["config"] for player in snapshots]}
        store.update_tournament(
            identifier,
            {
                "result": result,
                "progress": {
                    "completed_games": len(result["games"]),
                    "total_games": record["progress"]["total_games"],
                },
            },
            running=True,
        )

    try:
        for player in configs:
            if player.get("model_id"):
                from .models import ModelRegistry

                model = ModelRegistry(database.parent / "models").get(player["model_id"])
                player["model_path"] = model["path"]
        config["players"] = configs
        result = run_tournament(config, report=report, should_stop=stop.is_set)
        report(result)
        store.update_tournament(
            identifier,
            {"status": "cancelled" if stop.is_set() else "completed", "ended_at": timestamp()},
            running=True,
        )
        return 0
    except Exception:
        traceback.print_exc()
        store.update_tournament(
            identifier,
            {
                "status": "failed",
                "ended_at": timestamp(),
                "error": "The tournament worker failed. Completed games have been kept. See its worker log.",
            },
            running=True,
        )
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Internal persisted tournament worker")
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--id", required=True)
    args = parser.parse_args()
    return run_worker(args.database, args.id)


if __name__ == "__main__":
    raise SystemExit(main())
