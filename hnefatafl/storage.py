"""Small, transactional SQLite repository. JSON payloads retain ruleset versions.

The compare-and-swap update is authoritative: concurrent moves cannot silently
replace each other, including moves computed by an AI outside the transaction.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class ConflictError(ValueError):
    """A client attempted to update an old game version."""


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as connection:
            connection.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS games (
                    id TEXT PRIMARY KEY,
                    version INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS players (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    deleted_at TEXT
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    data TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS model_history (
                    id TEXT PRIMARY KEY,
                    data TEXT NOT NULL
                );
            """)
            # An additive, transactional migration preserves identities and
            # embedded game-history snapshots from the original schema.
            connection.execute("BEGIN IMMEDIATE")
            schema = connection.execute("PRAGMA user_version").fetchone()[0]
            if schema > 3:
                raise ValueError("This database uses a newer unsupported schema")
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(players)")}
            if "deleted_at" not in columns:
                connection.execute("ALTER TABLE players ADD COLUMN deleted_at TEXT")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS tournaments (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    data TEXT NOT NULL
                )
            """)
            connection.execute("PRAGMA user_version=3")

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=15000")
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def create_game(self, game: dict) -> None:
        with self.connection() as connection:
            connection.execute(
                "INSERT INTO games(id,version,created_at,data) VALUES(?,?,?,?)",
                (game["id"], game["version"], game["created_at"], json.dumps(game)),
            )

    def get_game(self, game_id: str) -> dict | None:
        with self.connection() as connection:
            row = connection.execute("SELECT data FROM games WHERE id=?", (game_id,)).fetchone()
        return json.loads(row["data"]) if row else None

    def list_games(self) -> list[dict]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT data FROM games ORDER BY created_at DESC,id"
            ).fetchall()
        return [json.loads(row["data"]) for row in rows]

    def update_game(self, game: dict, expected_version: int) -> None:
        game["version"] = expected_version + 1
        with self.connection() as connection:
            cursor = connection.execute(
                "UPDATE games SET data=?,version=? WHERE id=? AND version=?",
                (json.dumps(game), game["version"], game["id"], expected_version),
            )
            if cursor.rowcount != 1:
                raise ConflictError("This game changed. Reload it before moving again.")

    @contextmanager
    def discard_games(
        self, versions: dict[str, int], *, archived_model: dict | None = None
    ) -> Iterator[None]:
        """Commit removals together, rolling back if a related catalog change fails.

        The caller may remove a registry file inside this transaction. Holding
        the writer lock prevents a replay seek or concurrent move from changing
        the selected records between consent validation and their removal.
        """
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for game_id, expected_version in versions.items():
                cursor = connection.execute(
                    "DELETE FROM games WHERE id=? AND version=?", (game_id, expected_version)
                )
                if cursor.rowcount != 1:
                    raise ConflictError("This game changed. Reload it before deleting it.")
            if archived_model is not None:
                connection.execute(
                    "INSERT OR IGNORE INTO model_history(id,data) VALUES(?,?)",
                    (archived_model["id"], json.dumps(archived_model)),
                )
            yield

    def delete_game(self, game_id: str, expected_version: int) -> None:
        with self.discard_games({game_id: expected_version}):
            pass

    def players(self, *, include_deleted: bool = False) -> list[dict]:
        with self.connection() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM players "
                    + ("" if include_deleted else "WHERE deleted_at IS NULL ")
                    + "ORDER BY name,id"
                )
            ]

    def delete_player(self, player_id: str, deleted_at: str) -> bool:
        """Hide a profile from selection while retaining its historical identity."""
        with self.connection() as connection:
            cursor = connection.execute(
                "UPDATE players SET deleted_at=? WHERE id=? AND deleted_at IS NULL",
                (deleted_at, player_id),
            )
        return cursor.rowcount == 1

    def create_player(self, player: dict) -> dict:
        with self.connection() as connection:
            connection.execute(
                "INSERT INTO players(id,name,created_at) VALUES(?,?,?)",
                (player["id"], player["name"], player["created_at"]),
            )
        return player

    def archive_model(self, metadata: dict) -> None:
        """Retain display provenance for pre-snapshot games, never model weights."""
        with self.connection() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO model_history(id,data) VALUES(?,?)",
                (metadata["id"], json.dumps(metadata)),
            )

    def historical_models(self) -> list[dict]:
        with self.connection() as connection:
            rows = connection.execute("SELECT data FROM model_history").fetchall()
        return [json.loads(row["data"]) for row in rows]

    def save_job(self, job: dict) -> None:
        with self.connection() as connection:
            connection.execute(
                "INSERT INTO jobs(id,created_at,data) VALUES(?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                (job["id"], job["created_at"], json.dumps(job)),
            )

    def get_job(self, job_id: str) -> dict | None:
        with self.connection() as connection:
            row = connection.execute("SELECT data FROM jobs WHERE id=?", (job_id,)).fetchone()
        return json.loads(row["data"]) if row else None

    def jobs(self) -> list[dict]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT data FROM jobs ORDER BY created_at DESC,id"
            ).fetchall()
        return [json.loads(row["data"]) for row in rows]

    def save_tournament(self, tournament: dict) -> None:
        with self.connection() as connection:
            connection.execute(
                "INSERT INTO tournaments(id,created_at,data) VALUES(?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                (tournament["id"], tournament["created_at"], json.dumps(tournament)),
            )

    def get_tournament(self, tournament_id: str) -> dict | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT data FROM tournaments WHERE id=?", (tournament_id,)
            ).fetchone()
        return json.loads(row["data"]) if row else None

    def tournaments(self) -> list[dict]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT data FROM tournaments ORDER BY created_at DESC,id"
            ).fetchall()
        return [json.loads(row["data"]) for row in rows]

    def update_tournament(
        self, tournament_id: str, changes: dict, *, running: bool = False
    ) -> None:
        """Merge fields transactionally; never resurrect a deleted or stopped run."""
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT data FROM tournaments WHERE id=?", (tournament_id,)
            ).fetchone()
            if row is None:
                return
            tournament = json.loads(row["data"])
            if running and tournament["status"] != "running":
                return
            tournament.update(changes)
            connection.execute(
                "UPDATE tournaments SET data=? WHERE id=?", (json.dumps(tournament), tournament_id)
            )

    def delete_tournament(self, tournament_id: str) -> bool:
        with self.connection() as connection:
            cursor = connection.execute("DELETE FROM tournaments WHERE id=?", (tournament_id,))
        return cursor.rowcount == 1
