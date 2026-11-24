"""Additive fresh/upgrade/restore coverage without touching development data."""

import sqlite3

import pytest

from hnefatafl.league_migrations import LEAGUE_TABLES
from hnefatafl.online_migrations import SCHEMA_VERSION, retain_online_on_restore
from hnefatafl.storage import Store


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
        "league_settlement_due": "SELECT game_id FROM league_settlement_queue WHERE due_at<=1 ORDER BY due_at,game_id LIMIT 20",
        "league_standings_order": "SELECT account_id FROM league_standings WHERE season_id='2026-11' AND ruleset='brandubh' ORDER BY points DESC,wins DESC,losses,account_id LIMIT 20",
    }
    with store.connection() as c:
        for index, query in queries.items():
            plan = " ".join(row[3] for row in c.execute("EXPLAIN QUERY PLAN " + query))
            assert index in plan
            assert "TEMP B-TREE" not in plan
