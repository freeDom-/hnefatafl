"""Durable, resumable season projection repair."""

LEAGUE_RECOVERY_TABLES = ("league_rebuild_jobs",)


def migrate_league_recovery(c):
    c.execute("""CREATE TABLE IF NOT EXISTS league_rebuild_jobs (
        season_id TEXT PRIMARY KEY REFERENCES league_seasons(id),
        due_at INTEGER NOT NULL,
        stage TEXT NOT NULL DEFAULT 'clear_contributions' CHECK(stage IN (
            'clear_contributions','clear_standings','restore_games','fold_standings','catch_up'
        )),
        last_game_id TEXT NOT NULL DEFAULT '',
        last_ruleset TEXT NOT NULL DEFAULT '',
        last_account_id TEXT NOT NULL DEFAULT '',
        last_sequence INTEGER NOT NULL DEFAULT 0 CHECK(last_sequence>=0),
        attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts>=0),
        last_error TEXT
    )""")
    c.execute(
        "CREATE INDEX IF NOT EXISTS league_rebuild_due ON league_rebuild_jobs(due_at,season_id)"
    )
