"""Additive schema-14 account preferences; no inferred values or public defaults."""

import sqlite3


def migrate_profiles(c: sqlite3.Connection) -> None:
    columns = {row["name"] for row in c.execute("PRAGMA table_info(accounts)")}
    for name, definition in (
        ("country_code", "TEXT"),
        ("city", "TEXT"),
        ("birth_year", "INTEGER"),
        ("show_display_name", "INTEGER NOT NULL DEFAULT 0 CHECK(show_display_name IN (0,1))"),
        ("show_country", "INTEGER NOT NULL DEFAULT 0 CHECK(show_country IN (0,1))"),
        ("show_city", "INTEGER NOT NULL DEFAULT 0 CHECK(show_city IN (0,1))"),
        ("show_age", "INTEGER NOT NULL DEFAULT 0 CHECK(show_age IN (0,1))"),
    ):
        if name not in columns:
            c.execute(f"ALTER TABLE accounts ADD COLUMN {name} {definition}")
