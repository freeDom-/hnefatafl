"""Schema-9/legacy upgrade and safe restore-candidate compatibility."""

import json
import sqlite3

import pytest

from hnefatafl.online_migrations import ONLINE_TABLES, retain_online_on_restore
from hnefatafl.storage import Store
from test_account_migrations import legacy
from test_online_core import active, account_template as account_template, rig as rig


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
        assert c.execute("PRAGMA user_version").fetchone()[0] == 13
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
            1,
            0,
        )
    assert upgraded.get_game("local")["retained"]
    assert Store(store.path).get_game("local") == upgraded.get_game("local")


def test_representative_schema8_and_normalization_collision_upgrade(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    legacy(path)
    Store(path)
    with sqlite3.connect(path) as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == 13
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
        c.execute("PRAGMA user_version=14")
    with pytest.raises(ValueError, match="newer unsupported"):
        Store(live.path)


@pytest.mark.parametrize("explicit_pause", [False, True])
def test_schema12_upgrade_preserves_games_and_only_resumes_untouched_default(rig, explicit_pause):
    game = active(rig)
    path = rig.store.path
    store = rig.store
    with store.connection() as c:
        # Reproduce the prior schema by removing only schema-13 additions.
        for trigger in (
            "online_clock_configuration_immutable",
            "online_clock_insert_guard",
            "online_clock_update_guard",
            "online_match_immutable",
            "online_gate_audit_update_immutable",
        ):
            c.execute(f"DROP TRIGGER {trigger}")
        for table, columns in (
            ("online_matches", ("time_control", "increment_seconds")),
            (
                "online_games",
                (
                    "time_control",
                    "increment_seconds",
                    "attackers_remaining_us",
                    "defenders_remaining_us",
                ),
            ),
        ):
            for column in columns:
                c.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
        c.execute(
            "UPDATE online_timeout_gate SET enabled=0,version=?", (1 if explicit_pause else 0,)
        )
        c.execute(
            "UPDATE online_gate_audit SET enabled=0,note='Legacy default pause' WHERE version=0"
        )
        if explicit_pause:
            c.execute("INSERT INTO online_gate_audit VALUES(1,0,0,NULL,'restore','Restore pause')")
        c.execute("PRAGMA user_version=12")
    upgraded = Store(path)
    with upgraded.connection() as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == 13
        assert tuple(c.execute("SELECT enabled,version FROM online_timeout_gate").fetchone()) == (
            0 if explicit_pause else 1,
            1,
        )
        assert c.execute("SELECT enabled FROM online_gate_audit WHERE version=0").fetchone()[0] == 0
        columns = {row[1] for row in c.execute("PRAGMA table_info(online_games)")}
        assert {
            "time_control",
            "increment_seconds",
            "attackers_remaining_us",
            "defenders_remaining_us",
        } <= columns
    assert rig.core.read(rig.credentials["Archer"], game["id"]) == game
    Store(path)  # Repeated startup neither resumes an explicit pause nor appends an audit entry.
    with upgraded.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM online_gate_audit").fetchone()[0] == 2
