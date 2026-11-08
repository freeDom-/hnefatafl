"""Additive profiles and schema-15 publication exceptions for legacy private data."""

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
        ("date_of_birth", "TEXT"),
        (
            "profile_private_fields",
            "TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(profile_private_fields) AND json_type(profile_private_fields)='array')",
        ),
    ):
        if name not in columns:
            c.execute(f"ALTER TABLE accounts ADD COLUMN {name} {definition}")
    if "profile_private_fields" not in columns:
        # Run once when adding the marker. Later profile submissions remove only
        # explicitly edited fields, and restarting must never withhold them again.
        # Keep legacy flags/year intact; no birthday or consent is inferred.
        c.execute("""UPDATE accounts SET profile_private_fields=(
            SELECT json_group_array(key) FROM json_each(json_object(
                'display_name',display_name IS NOT NULL AND display_name<>'' AND show_display_name=0,
                'country_code',country_code IS NOT NULL AND country_code<>'' AND show_country=0,
                'city',city IS NOT NULL AND city<>'' AND show_city=0
            )) WHERE value=1
        )""")
