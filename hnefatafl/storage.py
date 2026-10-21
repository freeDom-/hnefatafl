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
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    data TEXT NOT NULL
                );
                PRAGMA user_version=1;
            """)

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

    def players(self) -> list[dict]:
        with self.connection() as connection:
            return [
                dict(row) for row in connection.execute("SELECT * FROM players ORDER BY name,id")
            ]

    def create_player(self, player: dict) -> dict:
        with self.connection() as connection:
            connection.execute(
                "INSERT INTO players(id,name,created_at) VALUES(?,?,?)",
                (player["id"], player["name"], player["created_at"]),
            )
        return player

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
