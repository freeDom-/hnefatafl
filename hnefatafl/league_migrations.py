"""Additive league authority tables, owned by the online schema transaction."""

# Parents precede children for retained-authority restore; never reset game data.
LEAGUE_TABLES = (
    "league_seasons",
    "league_settings",
    "league_games",
    "league_requests",
    "league_queue",
    "league_active_players",
    "league_contributions",
    "league_standings",
    "league_settlement_queue",
    "league_audit",
)


def migrate_leagues(c):
    statements = (
        """CREATE TABLE IF NOT EXISTS league_seasons (
            id TEXT PRIMARY KEY, starts_at INTEGER NOT NULL, ends_at INTEGER NOT NULL,
            grace_ends_at INTEGER NOT NULL, policy_version INTEGER NOT NULL CHECK(policy_version=1),
            revision INTEGER NOT NULL DEFAULT 0 CHECK(revision>=0),
            CHECK(starts_at<ends_at AND ends_at<grace_ends_at)
        )""",
        """CREATE TABLE IF NOT EXISTS league_settings (
            id INTEGER PRIMARY KEY CHECK(id=1), paused INTEGER NOT NULL DEFAULT 0 CHECK(paused IN (0,1)),
            version INTEGER NOT NULL DEFAULT 0 CHECK(version>=0)
        )""",
        "INSERT OR IGNORE INTO league_settings(id) VALUES(1)",
        """CREATE TABLE IF NOT EXISTS league_games (
            game_id TEXT PRIMARY KEY REFERENCES online_games(id),
            season_id TEXT NOT NULL REFERENCES league_seasons(id),
            ruleset TEXT NOT NULL CHECK(ruleset IN ('brandubh','tablut','copenhagen')),
            first_account TEXT NOT NULL REFERENCES accounts(id),
            second_account TEXT NOT NULL REFERENCES accounts(id),
            paired_at INTEGER NOT NULL, sequence INTEGER,
            result_revision INTEGER, state TEXT NOT NULL DEFAULT 'pending'
                CHECK(state IN ('pending','eligible','late','withdrawn')),
            CHECK(first_account<second_account)
        )""",
        "CREATE INDEX IF NOT EXISTS league_games_pair_day ON league_games(first_account,second_account,paired_at)",
        "CREATE INDEX IF NOT EXISTS league_games_season ON league_games(season_id,ruleset,sequence,game_id)",
        """CREATE TABLE IF NOT EXISTS league_requests (
            id TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES accounts(id),
            ruleset TEXT NOT NULL CHECK(ruleset IN ('brandubh','tablut','copenhagen')),
            created_at INTEGER NOT NULL, game_id TEXT REFERENCES league_games(game_id),
            status TEXT NOT NULL CHECK(status IN ('queued','matched','cancelled','expired'))
        )""",
        "CREATE INDEX IF NOT EXISTS league_requests_actor_time ON league_requests(account_id,created_at)",
        """CREATE TABLE IF NOT EXISTS league_queue (
            account_id TEXT PRIMARY KEY REFERENCES accounts(id),
            request_id TEXT NOT NULL UNIQUE REFERENCES league_requests(id),
            ruleset TEXT NOT NULL CHECK(ruleset IN ('brandubh','tablut','copenhagen')),
            joined_at INTEGER NOT NULL, expires_at INTEGER NOT NULL,
            CHECK(joined_at<expires_at)
        )""",
        "CREATE INDEX IF NOT EXISTS league_queue_pool_age ON league_queue(ruleset,joined_at,account_id)",
        "CREATE INDEX IF NOT EXISTS league_queue_expiry ON league_queue(expires_at,account_id)",
        """CREATE TABLE IF NOT EXISTS league_active_players (
            account_id TEXT PRIMARY KEY REFERENCES accounts(id),
            game_id TEXT NOT NULL REFERENCES league_games(game_id)
        )""",
        "CREATE INDEX IF NOT EXISTS league_active_game ON league_active_players(game_id)",
        """CREATE TABLE IF NOT EXISTS league_contributions (
            game_id TEXT NOT NULL REFERENCES league_games(game_id),
            account_id TEXT NOT NULL REFERENCES accounts(id),
            season_id TEXT NOT NULL REFERENCES league_seasons(id),
            ruleset TEXT NOT NULL CHECK(ruleset IN ('brandubh','tablut','copenhagen')),
            sequence INTEGER NOT NULL CHECK(sequence>0),
            outcome TEXT NOT NULL CHECK(outcome IN ('win','draw','loss')),
            PRIMARY KEY(game_id,account_id),
            UNIQUE(season_id,ruleset,account_id,sequence)
        )""",
        "CREATE INDEX IF NOT EXISTS league_contributions_actor ON league_contributions(season_id,ruleset,account_id,sequence)",
        """CREATE TABLE IF NOT EXISTS league_standings (
            season_id TEXT NOT NULL REFERENCES league_seasons(id),
            ruleset TEXT NOT NULL CHECK(ruleset IN ('brandubh','tablut','copenhagen')),
            account_id TEXT NOT NULL REFERENCES accounts(id), points INTEGER NOT NULL CHECK(points>=0),
            wins INTEGER NOT NULL CHECK(wins>=0), draws INTEGER NOT NULL CHECK(draws>=0),
            losses INTEGER NOT NULL CHECK(losses>=0), updated_at INTEGER NOT NULL,
            PRIMARY KEY(season_id,ruleset,account_id)
        )""",
        "CREATE INDEX IF NOT EXISTS league_standings_order ON league_standings(season_id,ruleset,points DESC,wins DESC,losses,account_id)",
        """CREATE TABLE IF NOT EXISTS league_settlement_queue (
            game_id TEXT PRIMARY KEY REFERENCES league_games(game_id), due_at INTEGER NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts>=0), last_error TEXT
        )""",
        "CREATE INDEX IF NOT EXISTS league_settlement_due ON league_settlement_queue(due_at,game_id)",
        """CREATE TABLE IF NOT EXISTS league_audit (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            actor_id TEXT REFERENCES accounts(id), action TEXT NOT NULL,
            season_id TEXT REFERENCES league_seasons(id), at INTEGER NOT NULL,
            reason TEXT NOT NULL, details TEXT NOT NULL
        )""",
    )
    for statement in statements:
        c.execute(statement)
