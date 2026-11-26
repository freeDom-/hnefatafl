"""Additive fresh/upgrade/restore coverage without touching development data."""

import sqlite3
from datetime import datetime, timezone

import pytest

from hnefatafl import league_policy as policy
from hnefatafl.league_migrations import LEAGUE_TABLES
from hnefatafl.leagues import Leagues
from hnefatafl.online_migrations import SCHEMA_VERSION, retain_online_on_restore
from hnefatafl.storage import Store
from test_leagues import finish_league_game, join, set_rating
from test_online_core import account_template as account_template, rig as rig


# These are the overlapping tables from the reviewed League preview, before
# main and the completed League feature adopted distinct schema numbers.
PREVIEW_TABLES = {
    "league_requests": """CREATE TABLE league_requests (
        id TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES accounts(id),
        ruleset TEXT NOT NULL CHECK(ruleset IN ('brandubh','tablut','copenhagen')),
        created_at INTEGER NOT NULL, game_id TEXT REFERENCES league_games(game_id),
        status TEXT NOT NULL CHECK(status IN ('queued','matched','cancelled','expired'))
    )""",
    "league_queue": """CREATE TABLE league_queue (
        account_id TEXT PRIMARY KEY REFERENCES accounts(id),
        request_id TEXT NOT NULL UNIQUE REFERENCES league_requests(id),
        ruleset TEXT NOT NULL CHECK(ruleset IN ('brandubh','tablut','copenhagen')),
        joined_at INTEGER NOT NULL, expires_at INTEGER NOT NULL,
        CHECK(joined_at<expires_at)
    )""",
    "league_standings": """CREATE TABLE league_standings (
        season_id TEXT NOT NULL REFERENCES league_seasons(id),
        ruleset TEXT NOT NULL CHECK(ruleset IN ('brandubh','tablut','copenhagen')),
        account_id TEXT NOT NULL REFERENCES accounts(id), points INTEGER NOT NULL CHECK(points>=0),
        wins INTEGER NOT NULL CHECK(wins>=0), draws INTEGER NOT NULL CHECK(draws>=0),
        losses INTEGER NOT NULL CHECK(losses>=0), updated_at INTEGER NOT NULL,
        PRIMARY KEY(season_id,ruleset,account_id)
    )""",
    "league_settlement_queue": """CREATE TABLE league_settlement_queue (
        game_id TEXT PRIMARY KEY REFERENCES league_games(game_id), due_at INTEGER NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts>=0), last_error TEXT
    )""",
}


def test_reviewed_league_preview_upgrades_preserving_authority_and_exact_retries(rig):
    service = Leagues(rig.core)
    finish_league_game(service, rig)
    assert service.drain()["settled"] == 1
    pending = finish_league_game(service, rig)
    old = join(service, rig, "Visitor")["request_id"]
    service.cancel(rig.credentials["Visitor"], old)
    current = join(service, rig, "Visitor")
    alias = join(service, rig, "Visitor")

    with rig.store.connection() as c:
        set_rating(c, rig.accounts["Visitor"]["id"], 1777.0)
        c.execute(
            """INSERT INTO league_active_players(account_id,game_id)
            SELECT first_account,game_id FROM league_games WHERE game_id=?
            UNION ALL SELECT second_account,game_id FROM league_games WHERE game_id=?""",
            (pending["id"], pending["id"]),
        )
        for action in ("insert", "update", "delete"):
            c.execute(f"DROP TRIGGER league_queue_rating_{action}")
        for table, schema in PREVIEW_TABLES.items():
            c.execute(schema.replace(f"CREATE TABLE {table}", "CREATE TEMP TABLE old_shape"))
            columns = ",".join(row[1] for row in c.execute("PRAGMA table_info(old_shape)"))
            c.execute(f"INSERT INTO old_shape SELECT {columns} FROM {table}")
            c.execute(f"DROP TABLE {table}")
            c.execute(schema)
            c.execute(f"INSERT INTO {table} SELECT * FROM old_shape")
            c.execute("DROP TABLE old_shape")
        for table in (
            "league_side_pairs",
            "league_side_balance",
            "league_match_cursor",
            "league_rebuild_jobs",
        ):
            c.execute(f"DROP TABLE {table}")
        c.execute("PRAGMA user_version=22")

    upgraded = Store(rig.store.path)
    with upgraded.connection() as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert c.execute("SELECT COUNT(*) FROM league_games").fetchone()[0] == 2
        assert c.execute("SELECT rating FROM league_queue").fetchone()[0] == 1777.0
        assert [row[0] for row in c.execute("SELECT last_sequence FROM league_standings")] == [1, 1]
        assert [row[0] for row in c.execute("SELECT balance FROM league_side_balance")] == [0, 0]
        assert c.execute("SELECT COUNT(*) FROM league_side_pairs").fetchone()[0] == 1
        assert tuple(
            c.execute("SELECT season_id,pending_since FROM league_settlement_queue").fetchone()
        ) == tuple(
            c.execute(
                """SELECT l.season_id,r.processed_at FROM league_games l JOIN online_results r
                ON r.game_id=l.game_id WHERE l.game_id=?""",
                (pending["id"],),
            ).fetchone()
        )

    legacy = join(service, rig, "Visitor", request=old)
    assert legacy["status"] == "cancelled" and legacy["expires_at"] is None
    assert legacy["queue"]["request_id"] == current["request_id"]
    replayed_alias = join(service, rig, "Visitor", request=alias["request_id"])
    assert replayed_alias["expires_at"] == current["expires_at"]
    service.cancel(rig.credentials["Visitor"], alias["request_id"])
    assert service.queue_status(rig.credentials["Visitor"])["queue"] is None
    assert join(service, rig, "Archer", "tablut")["status"] == "queued"
    assert service.drain()["settled"] == 1
    assert Store(rig.store.path).path == upgraded.path

    # Canonical live-state restore accepts upgraded and fresh schema 25 layouts.
    restored = Store(rig.root / "fresh-restore.sqlite3")
    with upgraded.connection() as original, restored.connection() as candidate:
        columns = ",".join(row[1] for row in original.execute("PRAGMA table_info(accounts)"))
        rows = original.execute(f"SELECT {columns} FROM accounts").fetchall()
        candidate.executemany(
            f"INSERT INTO accounts({columns}) VALUES({','.join('?' for _ in rows[0])})", rows
        )
        retain_online_on_restore(original, candidate)
        assert candidate.execute("PRAGMA foreign_key_check").fetchall() == []


def test_fresh_reconciliation_progress_tier_constraint(rig):
    season = policy.season_for(datetime(2033, 1, 15, tzinfo=timezone.utc))
    account = rig.accounts["Archer"]["id"]
    with rig.store.connection() as c:
        c.execute(
            """INSERT OR IGNORE INTO league_seasons
               (id,starts_at,ends_at,grace_ends_at,policy_version)
               VALUES(?,?,?,?,1)""",
            (
                season.identifier,
                int(season.start.timestamp() * 1_000_000),
                int(season.end.timestamp() * 1_000_000),
                int(season.grace_end.timestamp() * 1_000_000),
            ),
        )
        for invalid in (0, 6):
            with pytest.raises(sqlite3.IntegrityError):
                c.execute(
                    """INSERT INTO league_achievement_reconciliation
                       (account_id,season_id,ruleset,progress_tier,due_at)
                       VALUES(?,?,'brandubh',?,0)""",
                    (account, season.identifier, invalid),
                )
        c.execute(
            """INSERT INTO league_achievement_reconciliation
               (account_id,season_id,ruleset,progress_tier,due_at)
               VALUES(?,?,'brandubh',5,0)""",
            (account, season.identifier),
        )
        with pytest.raises(sqlite3.IntegrityError):
            c.execute(
                """UPDATE league_achievement_reconciliation SET progress_tier=0
                   WHERE account_id=? AND season_id=? AND ruleset='brandubh'""",
                (account, season.identifier),
            )


def test_schema_24_reconciliation_table_upgrades_in_place(rig):
    season = policy.season_for(rig.clock.value)
    account = rig.accounts["Archer"]["id"]
    with rig.store.connection() as c:
        c.execute(
            """INSERT OR IGNORE INTO league_seasons
               (id,starts_at,ends_at,grace_ends_at,policy_version)
               VALUES(?,?,?,?,1)""",
            (
                season.identifier,
                int(season.start.timestamp() * 1_000_000),
                int(season.end.timestamp() * 1_000_000),
                int(season.grace_end.timestamp() * 1_000_000),
            ),
        )
        c.execute("DROP INDEX league_achievement_reconciliation_due")
        c.execute("ALTER TABLE league_achievement_reconciliation RENAME TO old_reconciliation")
        c.execute(
            """CREATE TABLE league_achievement_reconciliation (
                account_id TEXT NOT NULL REFERENCES accounts(id),
                season_id TEXT NOT NULL REFERENCES league_seasons(id),
                ruleset TEXT NOT NULL,
                candidate_tier INTEGER,
                due_at INTEGER NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                PRIMARY KEY(account_id,season_id,ruleset)
            )"""
        )
        c.execute(
            """INSERT INTO league_achievement_reconciliation
               (account_id,season_id,ruleset,candidate_tier,due_at,attempts,last_error)
               VALUES(?,?,'brandubh',3,7,2,'retry')""",
            (account, season.identifier),
        )
        c.execute("DROP TABLE old_reconciliation")
        c.execute("PRAGMA user_version=24")

    reopened = Store(rig.store.path)
    with reopened.connection() as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        columns = {
            row[1] for row in c.execute("PRAGMA table_info(league_achievement_reconciliation)")
        }
        assert {
            "cursor_sequence",
            "cursor_game_id",
            "standing_points",
            "standing_wins",
            "standing_draws",
            "standing_losses",
            "progress_tier",
            "evidence_revision",
            "evidence_generation",
        } <= columns
        row = c.execute(
            "SELECT candidate_tier,due_at,attempts,last_error,cursor_sequence FROM league_achievement_reconciliation"
        ).fetchone()
        assert tuple(row) == (3, 7, 2, "retry", 0)
        indexes = {row[0] for row in c.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        assert "league_achievement_reconciliation_due" in indexes
        assert "league_games_first_sequence" in indexes
        assert "league_games_second_sequence" in indexes
        assert (
            c.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='league_achievement_evidence'"
            ).fetchone()[0]
            == 1
        )
        with pytest.raises(sqlite3.IntegrityError, match="progress tier"):
            c.execute(
                """UPDATE league_achievement_reconciliation SET progress_tier=99
                   WHERE account_id=? AND season_id=? AND ruleset='brandubh'""",
                (account, season.identifier),
            )


@pytest.mark.parametrize("previous_schema", [21, 22])
def test_upgrade_preserves_games_jobs_and_checkpoint_bytes(tmp_path, previous_schema):
    store = Store(tmp_path / "dev-fixture.sqlite3")
    game = {"id": "valuable-game", "version": 7, "created_at": "2026-01-01", "retained": True}
    job = {"id": "valuable-job", "created_at": "2026-01-01", "status": "completed", "updates": 2}
    store.create_game(game)
    store.save_job(job)
    checkpoint = tmp_path / "retained-checkpoint.pt"
    checkpoint.write_bytes(b"fixture bytes; no checkpoint load claimed")
    before = checkpoint.read_bytes()
    with store.connection() as c:
        for table in reversed(LEAGUE_TABLES):
            c.execute(f"DROP TABLE {table}")
        c.execute(f"PRAGMA user_version={previous_schema}")
    upgraded = Store(store.path)
    assert upgraded.get_game(game["id"]) == game
    assert upgraded.get_job(job["id"]) == job
    assert checkpoint.read_bytes() == before
    with upgraded.connection() as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        names = {row[0] for row in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert set(LEAGUE_TABLES) <= names
        assert tuple(c.execute("SELECT paused,version FROM league_settings").fetchone()) == (0, 0)
    # Reopening must not create a fictional season or reset existing configuration.
    with upgraded.connection() as c:
        c.execute("UPDATE league_settings SET paused=1,version=3")
    reopened = Store(store.path)
    with reopened.connection() as c:
        assert c.execute("SELECT count(*) FROM league_seasons").fetchone()[0] == 0
        assert tuple(c.execute("SELECT paused,version FROM league_settings").fetchone()) == (1, 3)


def test_league_audit_is_append_only_and_live_authority_survives_restore(tmp_path):
    live = Store(tmp_path / "live.sqlite3")
    restored = Store(tmp_path / "candidate.sqlite3")
    with live.connection() as c:
        c.execute("INSERT INTO league_seasons VALUES('2026-11',1,2,3,1,7)")
        c.execute("UPDATE league_settings SET paused=1,version=4")
        c.execute(
            "INSERT INTO league_audit(actor_id,action,season_id,at,reason,details) VALUES(NULL,'pause','2026-11',1,'Fixture reason','{}')"
        )
    for operation in ("UPDATE league_audit SET reason='changed'", "DELETE FROM league_audit"):
        with live.connection() as c:
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                c.execute(operation)
    with live.connection() as original, restored.connection() as candidate:
        retain_online_on_restore(original, candidate)
    with restored.connection() as c:
        assert c.execute("SELECT revision FROM league_seasons").fetchone()[0] == 7
        assert tuple(c.execute("SELECT paused,version FROM league_settings").fetchone()) == (1, 4)
        assert c.execute("SELECT reason FROM league_audit").fetchone()[0] == "Fixture reason"
        assert c.execute("SELECT enabled FROM online_timeout_gate").fetchone()[0] == 0
        assert c.execute("PRAGMA foreign_key_check").fetchall() == []


def test_queue_and_standing_discovery_have_matching_indexes(tmp_path):
    store = Store(tmp_path / "fresh.sqlite3")
    queries = {
        "league_queue_pool_age": "SELECT account_id FROM league_queue WHERE ruleset='brandubh' ORDER BY joined_at,account_id LIMIT 32",
        "league_queue_expiry": "SELECT account_id FROM league_queue WHERE expires_at<=1 ORDER BY expires_at,account_id LIMIT 32",
        "league_queue_rating_age": "SELECT account_id FROM league_queue INDEXED BY league_queue_rating_age WHERE ruleset='brandubh' AND CAST(rating / 200.0 AS INTEGER)=7 AND (joined_at,account_id)>(1,'') ORDER BY joined_at,account_id LIMIT 9",
        "league_requests_actor_time": "SELECT id FROM league_requests WHERE account_id='alice' ORDER BY created_at DESC,rowid DESC LIMIT 1",
        "league_settlement_due": "SELECT game_id FROM league_settlement_queue WHERE due_at<=1 ORDER BY due_at,game_id LIMIT 20",
        "league_games_first_history": """WITH pending AS (
              SELECT q.game_id,l.result_revision,
                g.result_revision AS official_revision,r.state AS rating_state,
                r.sequence AS rating_sequence
              FROM league_games l
              JOIN league_settlement_queue q ON q.game_id=l.game_id
              JOIN online_games g ON g.id=l.game_id
              LEFT JOIN rating_events r ON r.game_id=l.game_id
              WHERE l.first_account='alice' AND l.season_id='2026-11' AND l.ruleset='brandubh'
              UNION ALL
              SELECT q.game_id,l.result_revision,
                g.result_revision AS official_revision,r.state AS rating_state,
                r.sequence AS rating_sequence
              FROM league_games l
              JOIN league_settlement_queue q ON q.game_id=l.game_id
              JOIN online_games g ON g.id=l.game_id
              LEFT JOIN rating_events r ON r.game_id=l.game_id
              WHERE l.second_account='alice' AND l.season_id='2026-11' AND l.ruleset='brandubh'
            )
            SELECT 1 FROM pending WHERE
              (result_revision IS NOT NULL AND official_revision IS NOT result_revision)
              OR (rating_state='applied' AND rating_sequence<9) LIMIT 1""",
        "league_achievement_reconciliation_due": """SELECT account_id FROM league_achievement_reconciliation r
            LEFT JOIN league_rebuild_jobs j ON j.season_id=r.season_id
            WHERE r.due_at<=1 AND (r.due_at,r.account_id,r.season_id,r.ruleset)>(0,'','','')
            ORDER BY r.due_at,r.account_id,r.season_id,r.ruleset LIMIT 20""",
        "league_standings_qualified": "SELECT account_id FROM league_standings WHERE season_id='2026-11' AND ruleset='brandubh' AND wins+draws+losses>=3 ORDER BY -points,-wins,losses,account_id LIMIT 20",
        "league_standings_account": "SELECT ruleset,points FROM league_standings WHERE account_id='alice' AND season_id='2026-11'",
        "league_games_second_history": "SELECT game_id FROM league_games WHERE second_account='alice' AND season_id='2026-11' AND ruleset='brandubh' ORDER BY paired_at DESC,game_id DESC LIMIT 20",
        "league_games_rebuild": "SELECT game_id FROM league_games WHERE season_id='2026-11' AND game_id>'a' ORDER BY game_id LIMIT 20",
    }
    with store.connection() as c:
        for index, query in queries.items():
            plan = " ".join(row[3] for row in c.execute("EXPLAIN QUERY PLAN " + query))
            assert index in plan
            assert "TEMP B-TREE" not in plan
        tail = " ".join(
            row[3]
            for row in c.execute(
                """EXPLAIN QUERY PLAN SELECT sequence FROM league_contributions
                   WHERE season_id='2026-11' AND ruleset='brandubh' AND account_id='alice'
                   ORDER BY sequence DESC LIMIT 1"""
            )
        )
        assert "SCAN league_contributions" not in tail
        assert "TEMP B-TREE" not in tail
        chunk_plan = " ".join(
            row[3]
            for row in c.execute(
                """EXPLAIN QUERY PLAN WITH first_seat AS (
                    SELECT l.game_id,l.sequence FROM league_games l
                    WHERE l.first_account='alice' AND l.season_id='2026-11'
                      AND l.ruleset='brandubh' AND l.sequence IS NOT NULL
                      AND (l.sequence,l.game_id)>(1,'')
                    ORDER BY l.sequence,l.game_id LIMIT 256
                  ), second_seat AS (
                    SELECT l.game_id,l.sequence FROM league_games l
                    WHERE l.second_account='alice' AND l.season_id='2026-11'
                      AND l.ruleset='brandubh' AND l.sequence IS NOT NULL
                      AND (l.sequence,l.game_id)>(1,'')
                    ORDER BY l.sequence,l.game_id LIMIT 256
                  )
                  SELECT * FROM first_seat UNION ALL SELECT * FROM second_seat
                  ORDER BY sequence,game_id LIMIT 256"""
            )
        )
        assert "league_games_first_sequence" in chunk_plan
        assert "league_games_second_sequence" in chunk_plan
