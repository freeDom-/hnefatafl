"""Schema-9/legacy upgrade and safe restore-candidate compatibility."""

import json
import sqlite3

import pytest

from hnefatafl.online_migrations import ONLINE_TABLES, retain_online_on_restore
from hnefatafl.storage import Store
from test_account_migrations import legacy


def schema9(path):
    store = Store(path)
    with store.connection() as c:
        for table in reversed(ONLINE_TABLES):
            c.execute(f"DROP TABLE {table}")
        c.execute("DROP TABLE online_creation_metrics")
        c.execute("PRAGMA user_version=9")
    return store


def test_schema9_upgrade_preserves_account_security_sessions_and_local_data(tmp_path):
    store = schema9(tmp_path / "db.sqlite3")
    with store.connection() as c:
        c.execute(
            "INSERT INTO accounts VALUES('account','Raven','raven@example.test','retained','USER',1,1,2,'Raven',3,4)"
        )
        c.execute(
            "INSERT INTO sessions VALUES('session-digest','account',9999999999,'csrf-digest')"
        )
        c.execute(
            "INSERT INTO account_tokens VALUES('account','reset','token-digest',4,'address-digest',1,9999999999,NULL)"
        )
        c.execute("INSERT INTO account_limits VALUES('hashes',1,2)")
        c.execute(
            "INSERT INTO games VALUES('local',7,'then',?,'account')",
            (json.dumps({"id": "local", "retained": True}),),
        )
    upgraded = Store(store.path)
    with upgraded.connection() as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == 11
        assert tuple(
            c.execute(
                "SELECT password_hash,email_verified_at,email_version FROM accounts"
            ).fetchone()
        ) == ("retained", 3, 4)
        assert c.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM account_tokens").fetchone()[0] == 1
        assert c.execute("SELECT attempts FROM account_limits").fetchone()[0] == 2
        assert c.execute("SELECT COUNT(*) FROM online_games").fetchone()[0] == 0
        assert tuple(c.execute("SELECT enabled,version FROM online_timeout_gate").fetchone()) == (
            0,
            0,
        )
    assert upgraded.get_game("local")["retained"]
    assert Store(store.path).get_game("local") == upgraded.get_game("local")


def test_representative_schema8_and_normalization_collision_upgrade(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    legacy(path)
    Store(path)
    with sqlite3.connect(path) as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == 11
        assert c.execute("SELECT owner_account_id FROM games").fetchone()[0] == "one"
        assert c.execute("SELECT email_verified_at FROM accounts").fetchone()[0] is None
    colliding = tmp_path / "collision.sqlite3"
    legacy(colliding, collision="email")
    with pytest.raises(ValueError, match="collision"):
        Store(colliding)
    with sqlite3.connect(colliding) as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == 8
        assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='online_games'").fetchone()


def test_online_downgrade_and_unknown_future_restore_are_refused(tmp_path):
    live = Store(tmp_path / "live.sqlite3")
    old = schema9(tmp_path / "old.sqlite3")
    with live.connection() as source, old.connection() as target:
        with pytest.raises(ValueError, match="Online schema changed"):
            retain_online_on_restore(source, target)
    with live.connection() as c:
        c.execute("PRAGMA user_version=12")
    with pytest.raises(ValueError, match="newer unsupported"):
        Store(live.path)
