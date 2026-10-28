"""Schema 12: durable online records, rating state and start-rating snapshots."""

import sqlite3
import time

SCHEMA_VERSION = 12
ONLINE_TABLES = (
    "online_matches",
    "online_games",
    "online_moves",
    "online_results",
    "online_void_audit",
    "online_timeout_gate",
    "online_gate_audit",
    # Immutable per-game start ratings are captured at activation and are
    # deliberately independent of the mutable rating ledger below.
    "online_game_start_ratings",
    # Rating state is mutable (deterministic rebuilds), so it is retained on
    # restore but never append-only. Children follow their parents.
    "rating_events",
    "rating_applications",
    "player_ratings",
)
APPEND_ONLY = ("online_moves", "online_results", "online_void_audit", "online_gate_audit")


def migrate_online(c: sqlite3.Connection) -> None:
    # BLOB affinity deliberately prevents SQLite from coercing 60.0/'60' to an
    # integer before CHECK. Python and API boundaries also require actual ints.
    statements = (
        """CREATE TABLE IF NOT EXISTS online_matches (
            id TEXT PRIMARY KEY, creator_id TEXT NOT NULL REFERENCES accounts(id),
            opponent_id TEXT REFERENCES accounts(id),
            side_selection TEXT NOT NULL CHECK(side_selection IN ('attackers','defenders','random')),
            mode TEXT NOT NULL CHECK(mode IN ('single','simultaneous','sequential')),
            ruleset TEXT NOT NULL, ruleset_version TEXT NOT NULL,
            time_per_move BLOB NOT NULL CHECK(typeof(time_per_move)='integer' AND time_per_move BETWEEN 60 AND 1209600),
            created_at BLOB NOT NULL CHECK(typeof(created_at)='integer'),
            activated_at BLOB CHECK(activated_at IS NULL OR (typeof(activated_at)='integer' AND activated_at>=created_at)),
            first_creator_side TEXT CHECK(first_creator_side IN ('attackers','defenders')),
            CHECK(opponent_id IS NULL OR opponent_id<>creator_id),
            CHECK((activated_at IS NULL AND opponent_id IS NULL AND first_creator_side IS NULL)
               OR (activated_at IS NOT NULL AND opponent_id IS NOT NULL AND first_creator_side IS NOT NULL))
        )""",
        """CREATE TABLE IF NOT EXISTS online_games (
            id TEXT PRIMARY KEY, category TEXT NOT NULL DEFAULT 'online' CHECK(category='online'),
            creation_request TEXT NOT NULL,
            match_id TEXT NOT NULL REFERENCES online_matches(id), leg INTEGER NOT NULL CHECK(leg IN (1,2)),
            creator_id TEXT NOT NULL REFERENCES accounts(id),
            creator_side TEXT CHECK(creator_side IN ('attackers','defenders')),
            attackers_id TEXT REFERENCES accounts(id), defenders_id TEXT REFERENCES accounts(id),
            ruleset TEXT NOT NULL, ruleset_version TEXT NOT NULL CHECK(length(ruleset_version)>0),
            time_per_move BLOB NOT NULL CHECK(typeof(time_per_move)='integer' AND time_per_move BETWEEN 60 AND 1209600),
            rated_intent INTEGER NOT NULL DEFAULT 0 CHECK(rated_intent=0),
            status TEXT NOT NULL CHECK(status IN ('waiting','active','completed','cancelled','voided')),
            version INTEGER NOT NULL CHECK(version>=0),
            created_at BLOB NOT NULL CHECK(typeof(created_at)='integer'),
            updated_at BLOB NOT NULL CHECK(typeof(updated_at)='integer' AND updated_at>=created_at),
            started_at BLOB CHECK(started_at IS NULL OR (typeof(started_at)='integer' AND started_at>=created_at)),
            deadline BLOB CHECK(deadline IS NULL OR typeof(deadline)='integer'),
            completed_at BLOB CHECK(completed_at IS NULL OR (typeof(completed_at)='integer' AND completed_at>=started_at)),
            position TEXT NOT NULL CHECK(json_valid(position)), result_revision INTEGER,
            CHECK(attackers_id IS NULL OR defenders_id IS NULL OR attackers_id<>defenders_id),
            UNIQUE(creator_id,creation_request),
            UNIQUE(match_id,leg),
            CHECK((creator_side='attackers' AND attackers_id IS NOT NULL AND creator_id=attackers_id)
               OR (creator_side='defenders' AND defenders_id IS NOT NULL AND creator_id=defenders_id)
               OR (creator_side IS NULL AND started_at IS NULL AND attackers_id IS NULL AND defenders_id IS NULL)),
            CHECK((status='active' AND started_at IS NOT NULL AND deadline IS NOT NULL AND deadline>updated_at)
               OR (status<>'active' AND deadline IS NULL)),
            CHECK((started_at IS NOT NULL AND attackers_id IS NOT NULL AND defenders_id IS NOT NULL)
               OR (started_at IS NULL AND status IN ('waiting','cancelled','voided') AND
                   CASE creator_side WHEN 'attackers' THEN defenders_id ELSE attackers_id END IS NULL)),
            CHECK(started_at IS NULL OR creator_side IS NOT NULL),
            CHECK((status='completed' AND completed_at IS NOT NULL AND result_revision IS NOT NULL)
               OR (status='voided') OR (completed_at IS NULL AND result_revision IS NULL)),
            CHECK(status<>'voided' OR (started_at IS NULL AND completed_at IS NULL AND result_revision IS NULL)
               OR (started_at IS NOT NULL AND completed_at IS NOT NULL AND result_revision IS NOT NULL)),
            FOREIGN KEY(id,result_revision) REFERENCES online_results(game_id,revision) DEFERRABLE INITIALLY DEFERRED
        )""",
        "CREATE INDEX IF NOT EXISTS online_due ON online_games(deadline,id) WHERE status='active'",
        "CREATE INDEX IF NOT EXISTS online_created ON online_games(created_at DESC,id)",
        "CREATE INDEX IF NOT EXISTS online_attackers ON online_games(attackers_id)",
        "CREATE INDEX IF NOT EXISTS online_defenders ON online_games(defenders_id)",
        "CREATE INDEX IF NOT EXISTS online_waiting_creator ON online_games(creator_id) WHERE status='waiting'",
        "CREATE INDEX IF NOT EXISTS online_creation_window ON online_matches(creator_id,created_at)",
        """CREATE TABLE IF NOT EXISTS online_game_start_ratings (
            game_id TEXT NOT NULL REFERENCES online_games(id),
            account_id TEXT NOT NULL REFERENCES accounts(id),
            pool TEXT NOT NULL,
            rating REAL NOT NULL,
            captured_at BLOB NOT NULL CHECK(typeof(captured_at)='integer'),
            PRIMARY KEY(game_id,account_id)
        )""",
        "CREATE INDEX IF NOT EXISTS online_start_ratings_account ON online_game_start_ratings(account_id,pool)",
        """CREATE TABLE IF NOT EXISTS online_creation_metrics (
            reason TEXT PRIMARY KEY NOT NULL CHECK(reason IN ('waiting_limit','creation_rate')),
            denials INTEGER NOT NULL CHECK(denials>=0),
            last_denied_at BLOB CHECK(last_denied_at IS NULL OR typeof(last_denied_at)='integer'),
            last_logged_at BLOB CHECK(last_logged_at IS NULL OR typeof(last_logged_at)='integer')
        )""",
        "INSERT OR IGNORE INTO online_creation_metrics VALUES('waiting_limit',0,NULL,NULL)",
        "INSERT OR IGNORE INTO online_creation_metrics VALUES('creation_rate',0,NULL,NULL)",
        """CREATE TABLE IF NOT EXISTS online_moves (
            game_id TEXT NOT NULL REFERENCES online_games(id), ply INTEGER NOT NULL CHECK(ply>=1),
            source BLOB NOT NULL CHECK(typeof(source)='integer' AND source BETWEEN 0 AND 120),
            target BLOB NOT NULL CHECK(typeof(target)='integer' AND target BETWEEN 0 AND 120 AND target<>source),
            accepted_at BLOB NOT NULL CHECK(typeof(accepted_at)='integer'),
            game_version INTEGER NOT NULL CHECK(game_version>=1),
            PRIMARY KEY(game_id,ply), UNIQUE(game_id,game_version)
        )""",
        """CREATE TABLE IF NOT EXISTS online_results (
            game_id TEXT NOT NULL REFERENCES online_games(id), revision INTEGER NOT NULL CHECK(revision>=1),
            supersedes INTEGER, competitive_eligible INTEGER NOT NULL CHECK(competitive_eligible IN (0,1)),
            abandoned_account_id TEXT REFERENCES accounts(id),
            completed_at BLOB NOT NULL CHECK(typeof(completed_at)='integer'),
            processed_at BLOB NOT NULL CHECK(typeof(processed_at)='integer'),
            payload TEXT NOT NULL CHECK(json_valid(payload)), PRIMARY KEY(game_id,revision),
            CHECK((revision=1 AND supersedes IS NULL) OR (revision>1 AND supersedes IS NOT NULL AND supersedes=revision-1)),
            FOREIGN KEY(game_id,supersedes) REFERENCES online_results(game_id,revision),
            CHECK(competitive_eligible=1 OR abandoned_account_id IS NULL)
        )""",
        """CREATE TABLE IF NOT EXISTS online_void_audit (
            game_id TEXT PRIMARY KEY REFERENCES online_games(id), actor_id TEXT NOT NULL REFERENCES accounts(id),
            at BLOB NOT NULL CHECK(typeof(at)='integer'), note TEXT NOT NULL CHECK(length(trim(note)) BETWEEN 1 AND 1000),
            previous_result_revision INTEGER, resulting_version INTEGER NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS online_timeout_gate (
            id INTEGER PRIMARY KEY CHECK(id=1), enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
            version INTEGER NOT NULL CHECK(version>=0), updated_at BLOB NOT NULL CHECK(typeof(updated_at)='integer')
        )""",
        """CREATE TABLE IF NOT EXISTS online_gate_audit (
            version INTEGER PRIMARY KEY, enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
            at BLOB NOT NULL CHECK(typeof(at)='integer'), actor_id TEXT REFERENCES accounts(id),
            source TEXT NOT NULL CHECK(source IN ('migration','admin','restore')),
            note TEXT NOT NULL CHECK(length(trim(note)) BETWEEN 1 AND 1000),
            CHECK(source<>'admin' OR actor_id IS NOT NULL)
        )""",
        "INSERT OR IGNORE INTO online_timeout_gate VALUES(1,0,0,0)",
        "INSERT OR IGNORE INTO online_gate_audit VALUES(0,0,0,NULL,'migration','Timeout finalization paused pending outage policy approval.')",
        """CREATE TRIGGER IF NOT EXISTS online_configuration_immutable BEFORE UPDATE ON online_games
            WHEN NEW.id<>OLD.id OR NEW.creator_id<>OLD.creator_id OR NEW.match_id<>OLD.match_id OR NEW.leg<>OLD.leg
              OR (OLD.creator_side IS NOT NULL AND NEW.creator_side IS NOT OLD.creator_side)
              OR NEW.ruleset<>OLD.ruleset OR NEW.ruleset_version<>OLD.ruleset_version
              OR NEW.creation_request<>OLD.creation_request
              OR NEW.time_per_move<>OLD.time_per_move OR NEW.category<>OLD.category
              OR NEW.rated_intent<>OLD.rated_intent OR NEW.created_at<>OLD.created_at
              OR (OLD.started_at IS NOT NULL AND (NEW.started_at IS NOT OLD.started_at
                  OR NEW.creator_side IS NOT OLD.creator_side OR NEW.attackers_id IS NOT OLD.attackers_id OR NEW.defenders_id IS NOT OLD.defenders_id))
            BEGIN SELECT RAISE(ABORT,'Online configuration and activated seats are immutable'); END""",
        """CREATE TRIGGER IF NOT EXISTS online_transition_guard BEFORE UPDATE ON online_games
            WHEN NEW.version<>OLD.version+1 OR NEW.updated_at<OLD.updated_at OR NOT (
                (OLD.status='waiting' AND NEW.status IN ('active','cancelled','voided')) OR
                (OLD.status='active' AND NEW.status IN ('active','completed','voided')) OR
                (OLD.status IN ('completed','cancelled') AND NEW.status='voided'))
            BEGIN SELECT RAISE(ABORT,'Invalid online transition'); END""",
        """CREATE TABLE IF NOT EXISTS rating_events (
            game_id TEXT PRIMARY KEY REFERENCES online_games(id),
            pool TEXT NOT NULL, sequence INTEGER NOT NULL UNIQUE,
            state TEXT NOT NULL CHECK(state IN ('applied','withdrawn')),
            effective_revision INTEGER NOT NULL,
            winner TEXT NOT NULL CHECK(winner IN ('attackers','defenders','draw')),
            attackers_id TEXT NOT NULL REFERENCES accounts(id),
            defenders_id TEXT NOT NULL REFERENCES accounts(id),
            completed_at BLOB NOT NULL CHECK(typeof(completed_at)='integer'),
            policy_version INTEGER NOT NULL CHECK(policy_version>=1),
            created_at BLOB NOT NULL CHECK(typeof(created_at)='integer'),
            CHECK(attackers_id<>defenders_id)
        )""",
        "CREATE INDEX IF NOT EXISTS rating_events_pool ON rating_events(pool,sequence)",
        """CREATE TABLE IF NOT EXISTS rating_applications (
            game_id TEXT NOT NULL REFERENCES rating_events(game_id),
            account_id TEXT NOT NULL REFERENCES accounts(id), pool TEXT NOT NULL,
            sequence INTEGER NOT NULL, opponent_id TEXT NOT NULL REFERENCES accounts(id),
            rating_before REAL NOT NULL, rating_after REAL NOT NULL, delta REAL NOT NULL,
            opponent_rating REAL NOT NULL, expected REAL NOT NULL, score REAL NOT NULL,
            rated_games_after INTEGER NOT NULL CHECK(rated_games_after>=1),
            policy_version INTEGER NOT NULL CHECK(policy_version>=1),
            PRIMARY KEY(game_id,account_id)
        )""",
        "CREATE INDEX IF NOT EXISTS rating_applications_account ON rating_applications(account_id,pool)",
        """CREATE TABLE IF NOT EXISTS player_ratings (
            account_id TEXT NOT NULL REFERENCES accounts(id), pool TEXT NOT NULL,
            rating REAL NOT NULL, rated_games INTEGER NOT NULL CHECK(rated_games>=0),
            wins INTEGER NOT NULL CHECK(wins>=0), losses INTEGER NOT NULL CHECK(losses>=0),
            draws INTEGER NOT NULL CHECK(draws>=0),
            policy_version INTEGER NOT NULL CHECK(policy_version>=1),
            updated_at BLOB NOT NULL CHECK(typeof(updated_at)='integer'),
            PRIMARY KEY(account_id,pool)
        )""",
        "CREATE INDEX IF NOT EXISTS player_ratings_pool ON player_ratings(pool,rating DESC)",
    )
    for statement in statements:
        c.execute(statement)
    # The authoritative rated/casual flag is a match-level creation property. It
    # is added to existing schema-10 databases in place; online_games.rated_intent
    # remains a legacy always-zero placeholder so no table rebuild is required.
    match_columns = {row[1] for row in c.execute("PRAGMA table_info(online_matches)")}
    if "rated" not in match_columns:
        c.execute(
            "ALTER TABLE online_matches ADD COLUMN rated INTEGER NOT NULL DEFAULT 0 CHECK(rated IN (0,1))"
        )
    c.execute("DROP TRIGGER IF EXISTS online_match_immutable")
    c.execute("""CREATE TRIGGER online_match_immutable BEFORE UPDATE ON online_matches
        WHEN NEW.id<>OLD.id OR NEW.creator_id<>OLD.creator_id OR NEW.side_selection<>OLD.side_selection
          OR NEW.mode<>OLD.mode OR NEW.ruleset<>OLD.ruleset OR NEW.ruleset_version<>OLD.ruleset_version
          OR NEW.time_per_move<>OLD.time_per_move OR NEW.created_at<>OLD.created_at
          OR NEW.rated<>OLD.rated
          OR (OLD.activated_at IS NOT NULL AND (NEW.activated_at IS NOT OLD.activated_at
              OR NEW.opponent_id IS NOT OLD.opponent_id OR NEW.first_creator_side IS NOT OLD.first_creator_side))
        BEGIN SELECT RAISE(ABORT,'Match configuration and admitted participants are immutable'); END""")
    for table in APPEND_ONLY:
        for action in ("UPDATE", "DELETE"):
            c.execute(f"""CREATE TRIGGER IF NOT EXISTS {table}_{action.lower()}_immutable
                BEFORE {action} ON {table}
                BEGIN SELECT RAISE(ABORT,'Online audit and accepted moves are append-only'); END""")


def retain_online_on_restore(live: sqlite3.Connection, restored: sqlite3.Connection) -> None:
    """Stopped writers only: retain live authority, then pause timeout processing.

    This operates on the disposable restore candidate, never rewrites live audit
    history, and refuses an older/incompatible online schema before root cutover.
    """
    live_version = live.execute("PRAGMA user_version").fetchone()[0]
    copy_version = restored.execute("PRAGMA user_version").fetchone()[0]
    if max(live_version, copy_version) < SCHEMA_VERSION:
        return
    if live_version != copy_version or live_version != SCHEMA_VERSION:
        raise ValueError("Online schema changed; manual recovery is required")
    columns = {}
    for table in ONLINE_TABLES:
        before = [r[1] for r in live.execute(f"PRAGMA table_info({table})")]
        after = [r[1] for r in restored.execute(f"PRAGMA table_info({table})")]
        if not before or before != after:
            raise ValueError("Online schema changed; manual recovery is required")
        columns[table] = before
    # Audit immutability applies to normal operation. Only this stopped-writer
    # restore-candidate copy replaces rows, reinstating the guards before commit.
    for table in APPEND_ONLY:
        for action in ("update", "delete"):
            restored.execute(f"DROP TRIGGER IF EXISTS {table}_{action}_immutable")
    for table in reversed(ONLINE_TABLES):
        restored.execute(f"DELETE FROM {table}")
    for table in ONLINE_TABLES:
        restored.executemany(
            f"INSERT INTO {table} VALUES({','.join('?' for _ in columns[table])})",
            live.execute(f"SELECT * FROM {table}").fetchall(),
        )
    pause_online_after_restore(
        restored, "Live online state retained; timeout finalization paused after restore."
    )
    migrate_online(restored)
    # Optional observability is not authoritative game state. Older schema-10
    # snapshots lack this table; initialize it above and retain live counters when
    # available rather than rejecting an otherwise compatible recovery.
    if live.execute("SELECT 1 FROM sqlite_master WHERE name='online_creation_metrics'").fetchone():
        for row in live.execute(
            "SELECT denials,last_denied_at,last_logged_at,reason FROM online_creation_metrics"
        ):
            restored.execute(
                "UPDATE online_creation_metrics SET denials=?,last_denied_at=?,last_logged_at=? WHERE reason=?",
                tuple(row),
            )


def pause_online_after_restore(restored: sqlite3.Connection, note: str) -> None:
    version = restored.execute("PRAGMA user_version").fetchone()[0]
    if version < SCHEMA_VERSION:
        return
    if version != SCHEMA_VERSION:
        raise ValueError("Online schema changed; manual recovery is required")
    gate = restored.execute(
        "SELECT version,updated_at FROM online_timeout_gate WHERE id=1"
    ).fetchone()
    if gate is None:
        raise ValueError("Online timeout state is missing; manual recovery is required")
    latest = restored.execute("SELECT COALESCE(MAX(updated_at),0) FROM online_games").fetchone()[0]
    at = max(time.time_ns() // 1000, gate[1], latest)
    restored.execute(
        "UPDATE online_timeout_gate SET enabled=0,version=version+1,updated_at=? WHERE id=1", (at,)
    )
    restored.execute(
        "INSERT INTO online_gate_audit VALUES(?,0,?,NULL,'restore',?)",
        (gate[0] + 1, at, note),
    )
