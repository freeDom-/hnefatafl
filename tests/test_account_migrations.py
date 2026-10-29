"""Old-schema fixtures and read-only checks; never production data."""

import hashlib
import json
import sqlite3

import pytest

from hnefatafl.account_migrations import check_collisions
from hnefatafl.accounts import main as account_cli
from hnefatafl.auth import Auth, SecuritySettings
from hnefatafl.storage import Store


def legacy(path, *, collision=None):
    with sqlite3.connect(path) as c:
        c.execute(
            "CREATE TABLE accounts(id TEXT PRIMARY KEY,username TEXT NOT NULL UNIQUE COLLATE NOCASE,email TEXT NOT NULL UNIQUE,password_hash TEXT NOT NULL,role TEXT NOT NULL,active INTEGER NOT NULL DEFAULT 1,created_at REAL NOT NULL,last_login REAL,display_name TEXT)"
        )
        c.execute(
            "INSERT INTO accounts VALUES('one','Admin','ADMIN@EXAMPLE.test','retained-hash','ADMIN',1,123,124,'Old display')"
        )
        if collision:
            c.execute(
                "INSERT INTO accounts VALUES('two',?,?,'retained-other-hash','USER',0,125,NULL,'Other')",
                (
                    "Ａdmin" if collision == "username" else "Other",
                    "admin@example.test" if collision == "email" else "other@example.test",
                ),
            )
        c.execute(
            "CREATE TABLE games(id TEXT PRIMARY KEY,version INTEGER NOT NULL,created_at TEXT NOT NULL,data TEXT NOT NULL,owner_account_id TEXT)"
        )
        c.execute("INSERT INTO games VALUES('game',0,'before','{}','one')")
        c.execute("PRAGMA user_version=8")


def test_forward_migration_preserves_security_and_ownership(tmp_path):
    path = tmp_path / "hnefatafl.sqlite3"
    legacy(path)
    assert check_collisions(path) == {"ok": True, "collisions": []}
    store = Store(path)
    with store.connection() as c:
        account = dict(c.execute("SELECT * FROM accounts").fetchone())
        assert c.execute("PRAGMA user_version").fetchone()[0] == 13
        assert account["id"] == "one" and account["password_hash"] == "retained-hash"
        assert account["role"] == "ADMIN" and account["active"] == 1
        assert account["username"] == "Admin" and account["email"] == "admin@example.test"
        assert account["created_at"] == 123 and account["last_login"] == 124
        assert account["display_name"] == "Old display" and account["email_verified_at"] is None
        assert account["email_version"] == 1
        assert c.execute("SELECT owner_account_id FROM games").fetchone()[0] == "one"
        assert not c.execute("SELECT * FROM account_tokens").fetchall()
        assert not c.execute("SELECT * FROM account_mail").fetchall()
    assert Store(path).get_game("game") == {}


@pytest.mark.parametrize("field", ["username", "email"])
def test_normalized_collisions_stop_migration_without_reassigning_accounts(tmp_path, field):
    path = tmp_path / "hnefatafl.sqlite3"
    legacy(path, collision=field)
    report = check_collisions(path)
    assert report == {"ok": False, "collisions": [{"field": field, "account_ids": ["one", "two"]}]}
    with pytest.raises(ValueError, match="one, two"):
        Store(path)
    with sqlite3.connect(path) as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == 8
        assert "email_verified_at" not in {r[1] for r in c.execute("PRAGMA table_info(accounts)")}
        assert c.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 2
        assert c.execute("SELECT owner_account_id FROM games").fetchone()[0] == "one"


def test_operator_collision_command_is_read_only(tmp_path, capsys):
    path = tmp_path / "hnefatafl.sqlite3"
    legacy(path, collision="email")
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    assert account_cli(["check-collisions", "--data-dir", str(tmp_path)]) == 1
    result = json.loads(capsys.readouterr().out)
    assert not result["ok"] and "ADMIN@EXAMPLE" not in json.dumps(result)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    assert {p.name for p in tmp_path.iterdir()} == {"hnefatafl.sqlite3"}
    missing = tmp_path / "missing"
    assert account_cli(["check-collisions", "--data-dir", str(missing)]) == 1
    assert not missing.exists()


def test_newer_schema_is_refused(tmp_path):
    store = Store(tmp_path / "db.sqlite3")
    with store.connection() as c:
        c.execute("PRAGMA user_version=14")
    with pytest.raises(ValueError, match="newer unsupported schema"):
        Store(store.path)


def test_legacy_login_survives_schema_upgrade(tmp_path):
    path = tmp_path / "hnefatafl.sqlite3"
    store = Store(path)
    auth = Auth(store, SecuritySettings())
    saved = auth.create_account("Admin", "admin@example.test", "legacy-test-password", "ADMIN")
    _, session, _ = auth.login("Admin", "legacy-test-password", "test")
    # The forward upgrade retains legacy sessions and defaults verification to NULL.
    with store.connection() as c:
        for table in ("account_tokens", "account_mail", "account_limits"):
            c.execute(f"DROP TABLE {table}")
        c.execute("ALTER TABLE accounts DROP COLUMN email_verified_at")
        c.execute("ALTER TABLE accounts DROP COLUMN email_version")
        c.execute("PRAGMA user_version=8")
    upgraded = Auth(Store(path), SecuritySettings())
    assert upgraded.session(session)["id"] == saved["id"]
    assert upgraded.account(saved["id"])["email_verified_at"] is None
