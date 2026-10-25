"""Schema 9 account upgrade and an operator-safe read-only collision preflight."""

from collections import defaultdict
from pathlib import Path
import sqlite3
import unicodedata

from .account_policy import normalize

SCHEMA_VERSION = 9


def collision_report(connection: sqlite3.Connection) -> dict:
    if not connection.execute("SELECT 1 FROM sqlite_master WHERE name='accounts'").fetchone():
        return {"ok": True, "collisions": []}
    rows = connection.execute("SELECT id,username,email FROM accounts ORDER BY id").fetchall()
    collisions = []
    for index, field in ((1, "username"), (2, "email")):
        groups = defaultdict(list)
        for row in rows:
            groups[normalize(row[index])].append(row[0])
        for accounts in groups.values():
            if len(accounts) > 1:
                collisions.append({"field": field, "account_ids": accounts})
    return {"ok": not collisions, "collisions": collisions}


def check_collisions(path: Path) -> dict:
    # URI mode=ro never creates a database or invokes Store's migrations.
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as connection:
        return collision_report(connection)


def migrate_accounts(connection: sqlite3.Connection) -> None:
    report = collision_report(connection)
    if not report["ok"]:
        identifiers = sorted({i for c in report["collisions"] for i in c["account_ids"]})
        raise ValueError(
            "Normalized account collisions; run accounts check-collisions and resolve account IDs: "
            + ", ".join(identifiers)
        )
    columns = {r[1] for r in connection.execute("PRAGMA table_info(accounts)")}
    if "email_verified_at" not in columns:
        connection.execute("ALTER TABLE accounts ADD COLUMN email_verified_at REAL")
        connection.execute(
            "ALTER TABLE accounts ADD COLUMN email_version INTEGER NOT NULL DEFAULT 1"
        )
        for row in connection.execute("SELECT id,username,email FROM accounts").fetchall():
            connection.execute(
                "UPDATE accounts SET username=?,email=? WHERE id=?",
                (unicodedata.normalize("NFKC", row[1]).strip(), normalize(row[2]), row[0]),
            )
    connection.execute("""CREATE TABLE IF NOT EXISTS account_tokens (
        account_id TEXT NOT NULL, purpose TEXT NOT NULL CHECK(purpose IN ('verify','reset')),
        token_hash TEXT NOT NULL UNIQUE, email_version INTEGER NOT NULL, address_hash TEXT NOT NULL,
        created_at REAL NOT NULL, expires REAL NOT NULL, consumed_at REAL,
        PRIMARY KEY(account_id,purpose))""")
    connection.execute("CREATE INDEX IF NOT EXISTS account_token_expiry ON account_tokens(expires)")
    connection.execute("""CREATE TABLE IF NOT EXISTS account_mail (
        account_id TEXT NOT NULL, purpose TEXT NOT NULL CHECK(purpose IN ('verify','reset')),
        generation TEXT NOT NULL, email_version INTEGER NOT NULL, address_hash TEXT NOT NULL,
        status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
        created_at REAL NOT NULL, next_attempt REAL NOT NULL,
        lease_id TEXT, lease_until REAL, PRIMARY KEY(account_id,purpose))""")
    connection.execute(
        "CREATE INDEX IF NOT EXISTS account_mail_due ON account_mail(status,next_attempt)"
    )
    connection.execute("""CREATE TABLE IF NOT EXISTS account_limits (
        key TEXT PRIMARY KEY, started REAL NOT NULL, attempts INTEGER NOT NULL)""")
    connection.execute("CREATE INDEX IF NOT EXISTS account_limit_expiry ON account_limits(started)")
