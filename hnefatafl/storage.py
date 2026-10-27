"""Small, transactional SQLite repository. JSON payloads retain ruleset versions.

The compare-and-swap update is authoritative: concurrent moves cannot silently
replace each other, including moves computed by an AI outside the transaction.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Iterator

from .identities import rune_for_ordinal
from .security_context import owner_scope, principal
from .account_migrations import migrate_accounts
from .online_migrations import SCHEMA_VERSION, migrate_online


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
            if schema > SCHEMA_VERSION:
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
            connection.execute("""
                CREATE TABLE IF NOT EXISTS game_analysis (
                    game_id TEXT NOT NULL,
                    position_key TEXT NOT NULL,
                    data TEXT NOT NULL,
                    PRIMARY KEY (game_id, position_key)
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS entity_runes (
                    entity_key TEXT PRIMARY KEY,
                    ordinal INTEGER NOT NULL UNIQUE,
                    rune TEXT NOT NULL UNIQUE
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS training_workers (
                    id TEXT PRIMARY KEY,
                    token_hash TEXT NOT NULL UNIQUE,
                    data TEXT NOT NULL
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS game_analytics (
                    game_id TEXT PRIMARY KEY,
                    data TEXT NOT NULL
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS advisor_analysis (
                    game_id TEXT NOT NULL,
                    position_key TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    data TEXT NOT NULL,
                    PRIMARY KEY (game_id, position_key, revision)
                )
            """)
            # Schema 8 separates login accounts from local/historical players.
            # NULL ownership intentionally leaves historical records admin-only.
            for statement in (
                "CREATE TABLE IF NOT EXISTS accounts (id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE, email TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL, role TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, created_at REAL NOT NULL, last_login REAL, display_name TEXT)",
                "CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES accounts(id), expires REAL NOT NULL, csrf_hash TEXT NOT NULL)",
                "CREATE INDEX IF NOT EXISTS session_account ON sessions(account_id)",
                "CREATE TABLE IF NOT EXISTS auth_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
                "CREATE TABLE IF NOT EXISTS login_attempts (key TEXT PRIMARY KEY, started REAL NOT NULL, attempts INTEGER NOT NULL)",
                "CREATE TABLE IF NOT EXISTS game_participants (game_id TEXT NOT NULL REFERENCES games(id), side TEXT NOT NULL, account_id TEXT NOT NULL REFERENCES accounts(id), PRIMARY KEY(game_id,side))",
            ):
                connection.execute(statement)
            for table in ("games", "players"):
                columns = {r["name"] for r in connection.execute(f"PRAGMA table_info({table})")}
                if "owner_account_id" not in columns:
                    connection.execute(
                        f"ALTER TABLE {table} ADD COLUMN owner_account_id TEXT REFERENCES accounts(id)"
                    )
                connection.execute(
                    f"CREATE INDEX IF NOT EXISTS {table}_owner ON {table}(owner_account_id)"
                )
            migrate_accounts(connection)
            migrate_online(connection)
            connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        # Reserve historical humans, including tombstones, in a stable order.
        # Saved game JSON and model/checkpoint bytes are never rewritten.
        with self.connection() as connection:
            player_keys = [
                f"player:{row['id']}"
                for row in connection.execute("SELECT id FROM players ORDER BY id")
            ]
        self.runes(player_keys)

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
        account = principal.get()
        owner = account["id"] if account else None
        with self.connection() as connection:
            connection.execute(
                "INSERT INTO games(id,version,created_at,data,owner_account_id) VALUES(?,?,?,?,?)",
                (game["id"], game["version"], game["created_at"], json.dumps(game), owner),
            )

    def runes(self, entity_keys: Iterable[str]) -> dict[str, str]:
        """Reserve globally unique, non-recycled presentation runes atomically."""
        keys = list(dict.fromkeys(entity_keys))
        if not keys:
            return {}
        result = {}
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            next_ordinal = connection.execute(
                "SELECT COALESCE(MAX(ordinal), -1) + 1 FROM entity_runes"
            ).fetchone()[0]
            for key in keys:
                row = connection.execute(
                    "SELECT rune FROM entity_runes WHERE entity_key=?", (key,)
                ).fetchone()
                if row is not None:
                    result[key] = row["rune"]
                    continue
                rune = rune_for_ordinal(next_ordinal)
                connection.execute(
                    "INSERT INTO entity_runes(entity_key,ordinal,rune) VALUES(?,?,?)",
                    (key, next_ordinal, rune),
                )
                result[key] = rune
                next_ordinal += 1
        return result

    def rune(self, entity_key: str) -> str:
        return self.runes([entity_key])[entity_key]

    def get_game(self, game_id: str) -> dict | None:
        owner = owner_scope()
        with self.connection() as connection:
            row = connection.execute(
                "SELECT data FROM games WHERE id=?" + (" AND owner_account_id=?" if owner else ""),
                (game_id, owner) if owner else (game_id,),
            ).fetchone()
        return json.loads(row["data"]) if row else None

    def online_game_exists(self, game_id: str) -> bool:
        with self.connection() as connection:
            return (
                connection.execute("SELECT 1 FROM online_games WHERE id=?", (game_id,)).fetchone()
                is not None
            )

    def list_games(self) -> list[dict]:
        owner = owner_scope()
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT data FROM games "
                + ("WHERE owner_account_id=? " if owner else "")
                + "ORDER BY created_at DESC,id",
                (owner,) if owner else (),
            ).fetchall()
        return [json.loads(row["data"]) for row in rows]

    def update_game(self, game: dict, expected_version: int, analysis: dict | None = None) -> None:
        game["version"] = expected_version + 1
        with self.connection() as connection:
            cursor = connection.execute(
                "UPDATE games SET data=?,version=? WHERE id=? AND version=?",
                (json.dumps(game), game["version"], game["id"], expected_version),
            )
            if cursor.rowcount != 1:
                raise ConflictError("This game changed. Reload it before moving again.")
            if analysis is not None:
                row = connection.execute(
                    "SELECT data FROM game_analysis WHERE game_id=? AND position_key=?",
                    (game["id"], analysis["position_key"]),
                ).fetchone()
                retained = json.loads(row["data"]) if row else {**analysis, "evaluations": []}
                actual_roles = {entry["agent_id"] for entry in analysis["evaluations"]}
                retained["evaluations"] = [
                    entry
                    for entry in retained["evaluations"]
                    if entry["agent_id"] not in actual_roles
                ] + analysis["evaluations"]
                connection.execute(
                    "INSERT INTO game_analysis(game_id,position_key,data) VALUES(?,?,?) "
                    "ON CONFLICT(game_id,position_key) DO UPDATE SET data=excluded.data",
                    (game["id"], analysis["position_key"], json.dumps(retained, allow_nan=False)),
                )

    def game_analysis(self, game_id: str, position_key: str) -> dict | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT data FROM game_analysis WHERE game_id=? AND position_key=?",
                (game_id, position_key),
            ).fetchone()
        return json.loads(row["data"]) if row else None

    def save_game_analysis(self, game_id: str, expected_version: int, snapshot: dict) -> dict:
        """Attach the first analysis without changing the gameplay version.

        The writer lock and version check make deletion, replay seeks and moves
        invalidate late computations. A duplicate computation returns the first
        retained snapshot, never overwriting the historical estimate.
        """
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT version FROM games WHERE id=?", (game_id,)).fetchone()
            if row is None or row["version"] != expected_version:
                raise ConflictError("This game changed. Reload it before analyzing again.")
            connection.execute(
                "INSERT OR IGNORE INTO game_analysis(game_id,position_key,data) VALUES(?,?,?)",
                (game_id, snapshot["position_key"], json.dumps(snapshot, allow_nan=False)),
            )
            retained = connection.execute(
                "SELECT data FROM game_analysis WHERE game_id=? AND position_key=?",
                (game_id, snapshot["position_key"]),
            ).fetchone()
            return json.loads(retained["data"])

    def analytics(self, game_id: str) -> dict | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT data FROM game_analytics WHERE game_id=?", (game_id,)
            ).fetchone()
        return json.loads(row["data"]) if row else None

    def save_analytics(self, game_id: str, settings: dict, *, allow_online=False) -> dict:
        """Preferences use their own revision, independently of gameplay moves."""
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            exists = connection.execute("SELECT 1 FROM games WHERE id=?", (game_id,)).fetchone()
            if exists is None and allow_online:
                exists = connection.execute(
                    "SELECT 1 FROM online_games WHERE id=?", (game_id,)
                ).fetchone()
            if exists is None:
                raise ConflictError("Game not found.")
            row = connection.execute(
                "SELECT data FROM game_analytics WHERE game_id=?", (game_id,)
            ).fetchone()
            previous = json.loads(row["data"]) if row else None
            previous_values = (
                {key: value for key, value in previous.items() if key != "revision"}
                if previous
                else None
            )
            revision = previous.get("revision", 0) if previous else 0
            result = {**settings, "revision": revision + (previous_values != settings)}
            connection.execute(
                "INSERT INTO game_analytics(game_id,data) VALUES(?,?) "
                "ON CONFLICT(game_id) DO UPDATE SET data=excluded.data",
                (game_id, json.dumps(result, allow_nan=False)),
            )
            return result

    def advisor_analysis(self, game_id: str, key: str, revision: int) -> dict | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT data FROM advisor_analysis WHERE game_id=? AND position_key=? AND revision=?",
                (game_id, key, revision),
            ).fetchone()
        return json.loads(row["data"]) if row else None

    def save_analysis_entry(
        self,
        original: dict,
        snapshot: dict,
        entry: dict,
        *,
        advisor_revision: int | None = None,
        partial: bool = False,
        online: bool = False,
    ) -> dict:
        """Save late work only while the exact searched line remains retained.

        Moving or seeking does not destroy an opinion about a retained position.
        Deletion, a shortened branch and changed analytics preferences reject late
        writes. Player opinions are immutable per role once completed; advisor
        progress occupies a separate, revisioned collection.
        """
        game_id, key, cursor = original["id"], snapshot["position_key"], snapshot["cursor"]
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if online:
                # Online games are terminal and immutable here, so the retained
                # line cannot change; only confirm the record still exists.
                if (
                    connection.execute(
                        "SELECT 1 FROM online_games WHERE id=?", (game_id,)
                    ).fetchone()
                    is None
                ):
                    raise ConflictError("Game no longer exists.")
            else:
                row = connection.execute("SELECT data FROM games WHERE id=?", (game_id,)).fetchone()
                if row is None:
                    raise ConflictError("Game no longer exists.")
                current = json.loads(row["data"])
                if (
                    current.get("seed") != original.get("seed")
                    or current.get("ruleset") != original.get("ruleset")
                    or len(current["moves"]) < cursor
                    or current["moves"][:cursor] != original["moves"][:cursor]
                ):
                    raise ConflictError("The analyzed line is no longer retained.")
            if advisor_revision is not None:
                preferences = connection.execute(
                    "SELECT data FROM game_analytics WHERE game_id=?", (game_id,)
                ).fetchone()
                revision = json.loads(preferences["data"])["revision"] if preferences else 0
                if revision != advisor_revision:
                    raise ConflictError("The move advisor changed.")
                report = {**snapshot, "evaluations": [entry], "partial": partial}
                report["hint"] = (
                    {"agent_id": "advisor", "move": entry["best_move"]}
                    if entry.get("best_move")
                    else None
                )
                connection.execute(
                    "INSERT INTO advisor_analysis(game_id,position_key,revision,data) VALUES(?,?,?,?) "
                    "ON CONFLICT(game_id,position_key,revision) DO UPDATE SET data=excluded.data",
                    (game_id, key, advisor_revision, json.dumps(report, allow_nan=False)),
                )
                return report
            row = connection.execute(
                "SELECT data FROM game_analysis WHERE game_id=? AND position_key=?", (game_id, key)
            ).fetchone()
            report = (
                json.loads(row["data"]) if row else {**snapshot, "evaluations": [], "hint": None}
            )
            if not any(item["agent_id"] == entry["agent_id"] for item in report["evaluations"]):
                report["evaluations"].append(entry)
                connection.execute(
                    "INSERT INTO game_analysis(game_id,position_key,data) VALUES(?,?,?) "
                    "ON CONFLICT(game_id,position_key) DO UPDATE SET data=excluded.data",
                    (game_id, key, json.dumps(report, allow_nan=False)),
                )
            return report

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
                connection.execute("DELETE FROM game_analysis WHERE game_id=?", (game_id,))
                connection.execute("DELETE FROM game_analytics WHERE game_id=?", (game_id,))
                connection.execute("DELETE FROM advisor_analysis WHERE game_id=?", (game_id,))
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
        owner = owner_scope()
        with self.connection() as connection:
            result = [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM players "
                    + "WHERE 1=1 "
                    + ("" if include_deleted else "AND deleted_at IS NULL ")
                    + ("AND owner_account_id=? " if owner else "")
                    + "ORDER BY name,id",
                    (owner,) if owner else (),
                )
            ]
        runes = self.runes(f"player:{player['id']}" for player in result)
        return [{**player, "rune": runes[f"player:{player['id']}"]} for player in result]

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

    @contextmanager
    def discard_job(self, job_id: str) -> Iterator[None]:
        """Remove a job only if its related filesystem cleanup also succeeds."""
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute("DELETE FROM jobs WHERE id=?", (job_id,))
            if cursor.rowcount != 1:
                raise ConflictError("Training job not found.")
            yield

    def jobs(self) -> list[dict]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT data FROM jobs ORDER BY created_at DESC,id"
            ).fetchall()
        return [json.loads(row["data"]) for row in rows]

    def create_worker(self, worker: dict, token_hash: str) -> None:
        with self.connection() as connection:
            connection.execute(
                "INSERT INTO training_workers(id,token_hash,data) VALUES(?,?,?)",
                (worker["id"], token_hash, json.dumps(worker)),
            )

    def save_worker(self, worker: dict) -> None:
        with self.connection() as connection:
            connection.execute(
                "UPDATE training_workers SET data=? WHERE id=?",
                (json.dumps(worker), worker["id"]),
            )

    def get_worker(self, worker_id: str) -> dict | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT data FROM training_workers WHERE id=?", (worker_id,)
            ).fetchone()
        return json.loads(row["data"]) if row else None

    def worker_for_token(self, token_hash: str) -> dict | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT data FROM training_workers WHERE token_hash=?", (token_hash,)
            ).fetchone()
        return json.loads(row["data"]) if row else None

    def workers(self) -> list[dict]:
        with self.connection() as connection:
            rows = connection.execute("SELECT data FROM training_workers ORDER BY id").fetchall()
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
