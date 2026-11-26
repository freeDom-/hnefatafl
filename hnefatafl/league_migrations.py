"""Additive league authority tables, owned by the online schema transaction."""

from .league_policy import QUALIFYING_RESULTS, RATING_WINDOW_INITIAL, TIERS
from .league_recovery_migrations import LEAGUE_RECOVERY_TABLES, migrate_league_recovery
from .rating import INITIAL_RATING

RATING_BUCKET = f"CAST(rating / {float(RATING_WINDOW_INITIAL)} AS INTEGER)"

# Parents precede children for retained-authority restore; never reset game data.
LEAGUE_TABLES = (
    "league_seasons",
    "league_settings",
    "league_games",
    "league_requests",
    "league_queue",
    "league_match_cursor",
    "league_active_players",
    "league_side_balance",
    "league_side_pairs",
    "league_contributions",
    "league_standings",
    "league_achievement_progress",
    "league_achievement_evidence",
    "league_achievement_reconciliation",
    "league_settlement_queue",
    "league_audit",
    *LEAGUE_RECOVERY_TABLES,
)


def _add_preview_columns(c) -> bool:
    # Main's schema 22 never shipped League tables. The reviewed draft also
    # used that version, so identify that layout by the actual request table.
    columns = {row[1] for row in c.execute("PRAGMA table_info(league_requests)")}
    if not columns or "entry_id" in columns:
        return False
    for table, additions in (
        (
            "league_requests",
            ("entry_id TEXT NOT NULL DEFAULT ''", "joined_at INTEGER", "expires_at INTEGER"),
        ),
        (
            "league_queue",
            (
                f"rating REAL NOT NULL DEFAULT {INITIAL_RATING}",
                "opponent_joined_at INTEGER",
                "opponent_account TEXT",
            ),
        ),
        (
            "league_standings",
            (
                "last_sequence INTEGER CHECK(last_sequence>0)",
                "previous_tier TEXT",
                "tier_changed_at INTEGER",
            ),
        ),
        (
            "league_settlement_queue",
            ("season_id TEXT REFERENCES league_seasons(id)", "pending_since INTEGER"),
        ),
    ):
        for definition in additions:
            c.execute(f"ALTER TABLE {table} ADD COLUMN {definition}")
    return True


def _backfill_preview(c):
    c.execute("""UPDATE league_requests AS r SET (entry_id,joined_at,expires_at)=(
        SELECT q.request_id,q.joined_at,q.expires_at FROM league_queue q
        WHERE q.account_id=r.account_id AND q.ruleset=r.ruleset
    ) WHERE r.status='queued' AND EXISTS(
        SELECT 1 FROM league_queue q WHERE q.account_id=r.account_id AND q.ruleset=r.ruleset
    )""")
    # Terminal preview requests retained their outcome but did not persist the
    # original entry's timestamps. Keep those explicitly unknown.
    c.execute("""UPDATE league_requests SET entry_id=id,
        status=CASE WHEN status='queued' THEN 'cancelled' ELSE status END WHERE entry_id=''""")
    c.execute(
        """UPDATE league_queue AS q SET rating=COALESCE((
            SELECT rating FROM player_ratings r WHERE r.account_id=q.account_id AND r.pool=q.ruleset
        ),?)""",
        (INITIAL_RATING,),
    )
    c.execute("""UPDATE league_standings AS t SET last_sequence=(
        SELECT sequence FROM league_contributions c
        WHERE c.season_id=t.season_id AND c.ruleset=t.ruleset AND c.account_id=t.account_id
        ORDER BY sequence DESC LIMIT 1
    )""")
    c.execute("""UPDATE league_settlement_queue AS q SET
        season_id=(SELECT season_id FROM league_games WHERE game_id=q.game_id),
        pending_since=COALESCE((
            SELECT MIN(r.processed_at) FROM online_results r JOIN league_games l ON l.game_id=r.game_id
            WHERE r.game_id=q.game_id AND r.revision>COALESCE(l.result_revision,0)
        ),q.due_at)""")
    c.execute("""INSERT INTO league_side_balance(account_id,ruleset,balance)
        SELECT account_id,ruleset,SUM(balance) FROM (
            SELECT g.attackers_id AS account_id,l.ruleset,1 AS balance
                FROM league_games l JOIN online_games g ON g.id=l.game_id
            UNION ALL
            SELECT g.defenders_id,l.ruleset,-1 FROM league_games l JOIN online_games g ON g.id=l.game_id
        ) GROUP BY account_id,ruleset""")
    c.execute("""INSERT INTO league_side_pairs(first_account,second_account,ruleset,first_attacked)
        SELECT first_account,second_account,ruleset,first_attacked FROM (
            SELECT l.first_account,l.second_account,l.ruleset,g.attackers_id=l.first_account AS first_attacked,
                ROW_NUMBER() OVER(PARTITION BY l.first_account,l.second_account,l.ruleset
                    ORDER BY l.paired_at DESC,l.rowid DESC) AS newest
            FROM league_games l JOIN online_games g ON g.id=l.game_id
        ) WHERE newest=1""")


def _add_reconciliation_columns(c):
    columns = {row[1] for row in c.execute("PRAGMA table_info(league_achievement_reconciliation)")}
    for name, definition in (
        ("cursor_sequence", "INTEGER NOT NULL DEFAULT 0 CHECK(cursor_sequence>=0)"),
        ("cursor_game_id", "TEXT NOT NULL DEFAULT ''"),
        ("standing_points", "INTEGER NOT NULL DEFAULT 0 CHECK(standing_points>=0)"),
        ("standing_wins", "INTEGER NOT NULL DEFAULT 0 CHECK(standing_wins>=0)"),
        ("standing_draws", "INTEGER NOT NULL DEFAULT 0 CHECK(standing_draws>=0)"),
        ("standing_losses", "INTEGER NOT NULL DEFAULT 0 CHECK(standing_losses>=0)"),
        ("progress_tier", "INTEGER"),
        ("evidence_revision", "INTEGER"),
        ("evidence_generation", "INTEGER"),
    ):
        if name not in columns:
            c.execute(
                f"ALTER TABLE league_achievement_reconciliation ADD COLUMN {name} {definition}"
            )


def _ensure_reconciliation_guard(c):
    c.execute(
        f"""CREATE TRIGGER IF NOT EXISTS league_achievement_progress_tier_insert_guard
            BEFORE INSERT ON league_achievement_reconciliation
            WHEN NEW.progress_tier IS NOT NULL
              AND (NEW.progress_tier<1 OR NEW.progress_tier>{len(TIERS)})
            BEGIN SELECT RAISE(ABORT,'Invalid league achievement progress tier'); END"""
    )
    c.execute(
        f"""CREATE TRIGGER IF NOT EXISTS league_achievement_progress_tier_update_guard
            BEFORE UPDATE OF progress_tier ON league_achievement_reconciliation
            WHEN NEW.progress_tier IS NOT NULL
              AND (NEW.progress_tier<1 OR NEW.progress_tier>{len(TIERS)})
            BEGIN SELECT RAISE(ABORT,'Invalid league achievement progress tier'); END"""
    )


def migrate_leagues(c):
    upgrading_preview = _add_preview_columns(c)
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
        "CREATE INDEX IF NOT EXISTS league_games_rebuild ON league_games(season_id,game_id)",
        "CREATE INDEX IF NOT EXISTS league_games_first_history ON league_games(first_account,season_id,ruleset,paired_at DESC,game_id DESC)",
        "CREATE INDEX IF NOT EXISTS league_games_second_history ON league_games(second_account,season_id,ruleset,paired_at DESC,game_id DESC)",
        "CREATE INDEX IF NOT EXISTS league_games_first_sequence ON league_games(first_account,season_id,ruleset,sequence,game_id) WHERE sequence IS NOT NULL",
        "CREATE INDEX IF NOT EXISTS league_games_second_sequence ON league_games(second_account,season_id,ruleset,sequence,game_id) WHERE sequence IS NOT NULL",
        """CREATE TABLE IF NOT EXISTS league_requests (
            id TEXT PRIMARY KEY, account_id TEXT NOT NULL REFERENCES accounts(id),
            ruleset TEXT NOT NULL CHECK(ruleset IN ('brandubh','tablut','copenhagen')),
            created_at INTEGER NOT NULL, game_id TEXT REFERENCES league_games(game_id),
            status TEXT NOT NULL CHECK(status IN ('queued','matched','cancelled','expired')),
            entry_id TEXT NOT NULL, joined_at INTEGER, expires_at INTEGER,
            CHECK((joined_at IS NULL)=(expires_at IS NULL)),
            CHECK(status<>'queued' OR joined_at IS NOT NULL),
            CHECK(joined_at<expires_at), CHECK((status='matched')=(game_id IS NOT NULL))
        )""",
        "CREATE INDEX IF NOT EXISTS league_requests_actor_time ON league_requests(account_id,created_at)",
        # The implicit rowid suffix lets a client guard against earlier join
        # intents even when the authoritative clock has moved backwards.
        "CREATE INDEX IF NOT EXISTS league_requests_actor ON league_requests(account_id)",
        "CREATE INDEX IF NOT EXISTS league_requests_live ON league_requests(account_id,entry_id) WHERE status='queued'",
        f"""CREATE TABLE IF NOT EXISTS league_queue (
            account_id TEXT PRIMARY KEY REFERENCES accounts(id),
            request_id TEXT NOT NULL UNIQUE REFERENCES league_requests(id),
            ruleset TEXT NOT NULL CHECK(ruleset IN ('brandubh','tablut','copenhagen')),
            joined_at INTEGER NOT NULL, expires_at INTEGER NOT NULL,
            rating REAL NOT NULL DEFAULT {INITIAL_RATING},
            opponent_joined_at INTEGER, opponent_account TEXT,
            CHECK(joined_at<expires_at),
            CHECK((opponent_joined_at IS NULL)=(opponent_account IS NULL))
        )""",
        "CREATE INDEX IF NOT EXISTS league_queue_pool_age ON league_queue(ruleset,joined_at,account_id)",
        "CREATE INDEX IF NOT EXISTS league_queue_expiry ON league_queue(expires_at,account_id)",
        f"CREATE INDEX IF NOT EXISTS league_queue_rating_age ON league_queue(ruleset,{RATING_BUCKET},joined_at,account_id)",
        """CREATE TABLE IF NOT EXISTS league_match_cursor (
            ruleset TEXT PRIMARY KEY CHECK(ruleset IN ('brandubh','tablut','copenhagen')),
            joined_at INTEGER NOT NULL, account_id TEXT NOT NULL
        )""",
        # Elo remains authoritative. The copy is only an indexed discovery hint,
        # kept up to date in the same transaction including Elo rebuilds.
        """CREATE TRIGGER IF NOT EXISTS league_queue_rating_insert AFTER INSERT ON player_ratings
            BEGIN UPDATE league_queue SET rating=NEW.rating,opponent_joined_at=NULL,opponent_account=NULL
            WHERE account_id=NEW.account_id AND ruleset=NEW.pool; END""",
        """CREATE TRIGGER IF NOT EXISTS league_queue_rating_update AFTER UPDATE OF rating ON player_ratings
            WHEN NEW.rating<>OLD.rating
            BEGIN UPDATE league_queue SET rating=NEW.rating,opponent_joined_at=NULL,opponent_account=NULL
            WHERE account_id=NEW.account_id AND ruleset=NEW.pool; END""",
        f"""CREATE TRIGGER IF NOT EXISTS league_queue_rating_delete AFTER DELETE ON player_ratings
            BEGIN UPDATE league_queue SET rating={INITIAL_RATING},opponent_joined_at=NULL,opponent_account=NULL
            WHERE account_id=OLD.account_id AND ruleset=OLD.pool; END""",
        """CREATE TABLE IF NOT EXISTS league_active_players (
            account_id TEXT PRIMARY KEY REFERENCES accounts(id),
            game_id TEXT NOT NULL REFERENCES league_games(game_id)
        )""",
        "CREATE INDEX IF NOT EXISTS league_active_game ON league_active_players(game_id)",
        """CREATE TABLE IF NOT EXISTS league_side_balance (
            account_id TEXT NOT NULL REFERENCES accounts(id), ruleset TEXT NOT NULL,
            balance INTEGER NOT NULL, PRIMARY KEY(account_id,ruleset)
        )""",
        """CREATE TABLE IF NOT EXISTS league_side_pairs (
            first_account TEXT NOT NULL REFERENCES accounts(id),
            second_account TEXT NOT NULL REFERENCES accounts(id), ruleset TEXT NOT NULL,
            first_attacked INTEGER NOT NULL CHECK(first_attacked IN (0,1)),
            PRIMARY KEY(first_account,second_account,ruleset), CHECK(first_account<second_account)
        )""",
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
        """CREATE TABLE IF NOT EXISTS league_standings (
            season_id TEXT NOT NULL REFERENCES league_seasons(id),
            ruleset TEXT NOT NULL CHECK(ruleset IN ('brandubh','tablut','copenhagen')),
            account_id TEXT NOT NULL REFERENCES accounts(id), points INTEGER NOT NULL CHECK(points>=0),
            wins INTEGER NOT NULL CHECK(wins>=0), draws INTEGER NOT NULL CHECK(draws>=0),
            losses INTEGER NOT NULL CHECK(losses>=0), updated_at INTEGER NOT NULL,
            last_sequence INTEGER CHECK(last_sequence>0),
            previous_tier TEXT CHECK(previous_tier IN ('midgard','nidavellir','alfheim','asgard','valhalla')),
            tier_changed_at INTEGER,
            CHECK((previous_tier IS NULL)=(tier_changed_at IS NULL)),
            PRIMARY KEY(season_id,ruleset,account_id)
        )""",
        f"""CREATE INDEX IF NOT EXISTS league_standings_qualified
            ON league_standings(season_id,ruleset,-points,-wins,losses,account_id)
            WHERE wins+draws+losses>={QUALIFYING_RESULTS}""",
        "CREATE INDEX IF NOT EXISTS league_standings_account ON league_standings(account_id,season_id,ruleset)",
        f"""CREATE TABLE IF NOT EXISTS league_achievement_progress (
            account_id TEXT NOT NULL REFERENCES accounts(id),
            ruleset TEXT NOT NULL CHECK(ruleset IN ('brandubh','tablut','copenhagen')),
            highest_tier INTEGER NOT NULL CHECK(highest_tier BETWEEN 1 AND {len(TIERS)}),
            achieved_at INTEGER NOT NULL,
            PRIMARY KEY(account_id,ruleset)
        )""",
        """CREATE TABLE IF NOT EXISTS league_achievement_evidence (
            account_id TEXT NOT NULL REFERENCES accounts(id),
            season_id TEXT NOT NULL REFERENCES league_seasons(id),
            ruleset TEXT NOT NULL CHECK(ruleset IN ('brandubh','tablut','copenhagen')),
            generation INTEGER NOT NULL DEFAULT 0 CHECK(generation>=0),
            PRIMARY KEY(account_id,season_id,ruleset)
        )""",
        f"""CREATE TABLE IF NOT EXISTS league_achievement_reconciliation (
            account_id TEXT NOT NULL REFERENCES accounts(id),
            season_id TEXT NOT NULL REFERENCES league_seasons(id),
            ruleset TEXT NOT NULL CHECK(ruleset IN ('brandubh','tablut','copenhagen')),
            candidate_tier INTEGER CHECK(candidate_tier BETWEEN 1 AND {len(TIERS)}),
            due_at INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts>=0),
            last_error TEXT,
            cursor_sequence INTEGER NOT NULL DEFAULT 0 CHECK(cursor_sequence>=0),
            cursor_game_id TEXT NOT NULL DEFAULT '',
            standing_points INTEGER NOT NULL DEFAULT 0 CHECK(standing_points>=0),
            standing_wins INTEGER NOT NULL DEFAULT 0 CHECK(standing_wins>=0),
            standing_draws INTEGER NOT NULL DEFAULT 0 CHECK(standing_draws>=0),
            standing_losses INTEGER NOT NULL DEFAULT 0 CHECK(standing_losses>=0),
            progress_tier INTEGER CHECK(progress_tier BETWEEN 1 AND {len(TIERS)}),
            evidence_revision INTEGER,
            evidence_generation INTEGER,
            PRIMARY KEY(account_id,season_id,ruleset)
        )""",
        """CREATE INDEX IF NOT EXISTS league_achievement_reconciliation_due
            ON league_achievement_reconciliation(due_at,account_id,season_id,ruleset)""",
        """CREATE INDEX IF NOT EXISTS league_achievement_reconciliation_season
            ON league_achievement_reconciliation(season_id,account_id,ruleset)""",
        """CREATE TABLE IF NOT EXISTS league_settlement_queue (
            game_id TEXT PRIMARY KEY REFERENCES league_games(game_id), due_at INTEGER NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts>=0), last_error TEXT,
            season_id TEXT NOT NULL REFERENCES league_seasons(id), pending_since INTEGER NOT NULL
        )""",
        "CREATE INDEX IF NOT EXISTS league_settlement_due ON league_settlement_queue(due_at,game_id)",
        "CREATE INDEX IF NOT EXISTS league_settlement_season_due ON league_settlement_queue(season_id,due_at,game_id)",
        "CREATE INDEX IF NOT EXISTS league_settlement_pending ON league_settlement_queue(pending_since)",
        """CREATE TABLE IF NOT EXISTS league_audit (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            actor_id TEXT REFERENCES accounts(id), action TEXT NOT NULL,
            season_id TEXT REFERENCES league_seasons(id), at INTEGER NOT NULL,
            reason TEXT NOT NULL, details TEXT NOT NULL
        )""",
    )
    for statement in statements:
        c.execute(statement)
    _add_reconciliation_columns(c)
    _ensure_reconciliation_guard(c)
    if upgrading_preview:
        _backfill_preview(c)
    migrate_league_recovery(c)
