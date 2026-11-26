"""Ruleset-specific permanent league tiers in the existing achievement authority."""

from concurrent.futures import ThreadPoolExecutor
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest


from hnefatafl import league_policy as policy
from hnefatafl.engine import RULESETS
from hnefatafl.league_results import settle_result
from hnefatafl.league_administration import LeagueAdministration
from hnefatafl.league_views import LeagueCursors, LeagueViews
from hnefatafl import leagues as leagues_module
from hnefatafl.leagues import Leagues
from hnefatafl.online_core import OnlineCore, micros
from hnefatafl.online_migrations import SCHEMA_VERSION
from hnefatafl.progression import (
    ACHIEVEMENTS,
    ACHIEVEMENT_CATALOG_VERSION,
    LEAGUE_ACHIEVEMENTS,
    LEAGUE_ACHIEVEMENT_BY_KEY,
    LEAGUE_TIER_POINTS,
    PORTRAIT_BY_ACHIEVEMENT,
    Progression,
    record_league_tier,
)
from hnefatafl.storage import Store
from test_leagues import finish_league_game
from test_online_core import POLICY, account_template as account_template, rig as rig


def standing(points, played=3):
    return policy.Standing(points, played, 0, 0)


def league_ids(ruleset, upto=None):
    return {
        achievement.id
        for achievement in LEAGUE_ACHIEVEMENTS
        if achievement.ruleset == ruleset and (upto is None or achievement.tier <= upto)
    }


def awarded(rig, account_id, ruleset=None):
    query = """SELECT a.achievement_id FROM achievement_awards a
        JOIN achievement_catalog c ON c.id=a.achievement_id
        WHERE a.account_id=? AND c.criterion='league_tier'"""
    args = [account_id]
    if ruleset is not None:
        query += " AND c.scope=?"
        args.append(ruleset)
    with rig.store.connection() as c:
        return {row[0] for row in c.execute(query, args)}


def peak(rig, account_id, ruleset):
    with rig.store.connection() as c:
        row = c.execute(
            "SELECT highest_tier FROM league_achievement_progress WHERE account_id=? AND ruleset=?",
            (account_id, ruleset),
        ).fetchone()
        return row[0] if row is not None else 0


def reset_league_progress(rig, account_id, ruleset=None):
    with rig.core._write() as c:
        # The disposable test database needs a pre-feature state. Normal code
        # never deletes awards; the next Store startup reinstates the guards.
        for action in ("update", "delete"):
            c.execute(f"DROP TRIGGER IF EXISTS achievement_awards_{action}_immutable")
        if ruleset is None:
            c.execute("DELETE FROM league_achievement_progress WHERE account_id=?", (account_id,))
            c.execute(
                """DELETE FROM achievement_awards WHERE account_id=? AND achievement_id IN (
                   SELECT id FROM achievement_catalog WHERE criterion='league_tier')""",
                (account_id,),
            )
        else:
            c.execute(
                "DELETE FROM league_achievement_progress WHERE account_id=? AND ruleset=?",
                (account_id, ruleset),
            )
            c.execute(
                """DELETE FROM achievement_awards WHERE account_id=? AND achievement_id IN (
                   SELECT id FROM achievement_catalog WHERE criterion='league_tier' AND scope=?)""",
                (account_id, ruleset),
            )


def test_definitions_are_generated_from_the_authoritative_league_policy():
    expected_rulesets = sorted(RULESETS, key=lambda key: RULESETS[key].size)
    assert [a.family for a in LEAGUE_ACHIEVEMENTS] == [
        f"{ruleset}-league" for ruleset in expected_rulesets for _ in policy.TIERS
    ]
    assert len(LEAGUE_ACHIEVEMENTS) == len(RULESETS) * len(policy.TIERS)
    assert len(ACHIEVEMENTS) == 92
    assert sum(a.points for a in ACHIEVEMENTS) == 6770
    for achievement in LEAGUE_ACHIEVEMENTS:
        rank = next(
            index
            for index, (tier_id, _, _) in enumerate(policy.TIERS, 1)
            if tier_id == achievement.league_tier
        )
        tier_id, tier_name, minimum = policy.TIERS[rank - 1]
        assert achievement.id == f"{achievement.ruleset}-league-{tier_id}"
        assert achievement.name == f"{achievement.ruleset.title()} League: {tier_name}"
        assert achievement.category == "league"
        assert achievement.criterion == "league_tier"
        assert achievement.scope == achievement.ruleset
        assert achievement.family_name == f"{achievement.ruleset.title()} League"
        assert achievement.threshold == rank
        assert achievement.tier == rank
        assert achievement.league_tier_name == tier_name
        assert achievement.minimum_points == minimum
        assert achievement.points == LEAGUE_TIER_POINTS[rank - 1]
        assert (achievement.ruleset, achievement.league_tier) in LEAGUE_ACHIEVEMENT_BY_KEY
        # League achievements reuse the existing portrait catalog rather than
        # inventing a second reward surface.
        assert achievement.id not in PORTRAIT_BY_ACHIEVEMENT


def test_midgard_requires_three_eligible_results_and_inactive_players_gain_nothing(rig):
    account = rig.accounts["Archer"]["id"]
    with rig.core._write() as c:
        assert not record_league_tier(
            c,
            account,
            policy.season_for(rig.clock.value).identifier,
            "brandubh",
            standing(10, played=2),
            1,
        )
    assert peak(rig, account, "brandubh") == 0
    assert awarded(rig, account, "brandubh") == set()

    with rig.core._write() as c:
        assert record_league_tier(
            c,
            account,
            policy.season_for(rig.clock.value).identifier,
            "brandubh",
            standing(0, played=3),
            2,
        )
    assert peak(rig, account, "brandubh") == 1
    assert awarded(rig, account, "brandubh") == league_ids("brandubh", 1)

    # Marking the player inactive does not alter permanent history but cannot
    # manufacture new league results or awards.
    with rig.store.connection() as c:
        c.execute("UPDATE accounts SET active=0 WHERE id=?", (account,))
    before = awarded(rig, account)
    with rig.core._write() as c:
        assert not record_league_tier(
            c,
            account,
            policy.season_for(rig.clock.value).identifier,
            "brandubh",
            standing(10, played=2),
            3,
        )
    assert awarded(rig, account) == before


def test_every_tier_unlocks_at_its_exact_authoritative_boundary(rig):
    account = rig.accounts["Archer"]["id"]
    for ruleset in RULESETS:
        for rank, (_, _, minimum) in enumerate(policy.TIERS, 1):
            reset_league_progress(rig, account, ruleset)
            below = max(0, minimum - 1)
            with rig.core._write() as c:
                record_league_tier(
                    c,
                    account,
                    policy.season_for(rig.clock.value).identifier,
                    ruleset,
                    standing(below),
                    10 + rank,
                )
            expected_below = max(
                (
                    index
                    for index, (_, _, threshold) in enumerate(policy.TIERS, 1)
                    if threshold <= below
                ),
                default=0,
            )
            assert awarded(rig, account, ruleset) == league_ids(ruleset, expected_below)
            with rig.core._write() as c:
                record_league_tier(
                    c,
                    account,
                    policy.season_for(rig.clock.value).identifier,
                    ruleset,
                    standing(minimum),
                    20 + rank,
                )
            assert awarded(rig, account, ruleset) == league_ids(ruleset, rank)
            assert peak(rig, account, ruleset) == rank
        reset_league_progress(rig, account, ruleset)


def test_ruleset_progress_is_independent_and_survives_demotion_and_rollover(rig):
    account = rig.accounts["Archer"]["id"]
    with rig.core._write() as c:
        record_league_tier(
            c, account, policy.season_for(rig.clock.value).identifier, "brandubh", standing(30), 1
        )
    assert peak(rig, account, "brandubh") == 4
    assert peak(rig, account, "tablut") == 0 and peak(rig, account, "copenhagen") == 0
    assert awarded(rig, account, "tablut") == set()

    with rig.core._write() as c:
        record_league_tier(
            c, account, policy.season_for(rig.clock.value).identifier, "tablut", standing(6), 2
        )
    assert peak(rig, account, "brandubh") == 4
    assert peak(rig, account, "tablut") == 2

    # A demotion, season reset or inactivity never lowers the permanent peak.
    with rig.core._write() as c:
        assert not record_league_tier(
            c, account, policy.season_for(rig.clock.value).identifier, "brandubh", standing(0), 3
        )
        c.execute("DELETE FROM league_standings WHERE account_id=?", (account,))
    assert peak(rig, account, "brandubh") == 4
    assert awarded(rig, account, "brandubh") == league_ids("brandubh", 4)

    # Reaching the same tier again does not create a second award or score.
    before = awarded(rig, account, "brandubh")
    with rig.core._write() as c:
        assert not record_league_tier(
            c, account, policy.season_for(rig.clock.value).identifier, "brandubh", standing(30), 4
        )
    assert awarded(rig, account, "brandubh") == before
    with rig.store.connection() as c:
        c.execute("BEGIN")
        projection = Progression(rig.core)._projection(c, account, micros(rig.clock.value))
    family = next(item for item in projection["families"] if item["id"] == "brandubh-league")
    assert family["awarded_tier"] == 4
    assert family["awarded_count"] == 4
    assert (
        sum(
            item["points"]
            for item in projection["items"]
            if item["unlocked"] and item["family_id"] == "brandubh-league"
        )
        == 100
    )


def test_real_season_rollover_keeps_the_permanent_peak_without_duplicate_rewards(rig):
    service = Leagues(rig.core)
    account = rig.accounts["Archer"]["id"]
    for _ in range(3):
        finish_league_game(service, rig, "Keeper")
        service.drain()
    january = awarded(rig, account, "brandubh")
    assert january == league_ids("brandubh", 2)

    rig.clock.value = datetime(2027, 2, 3, 3, 4, 5, 123456, tzinfo=timezone.utc)
    for _ in range(3):
        finish_league_game(service, rig, "Keeper")
        service.drain()
    assert awarded(rig, account, "brandubh") == january

    rig.clock.value += timedelta(days=1)
    for _ in range(3):
        finish_league_game(service, rig, "Keeper")
        service.drain()
    assert peak(rig, account, "brandubh") == 3
    assert awarded(rig, account, "brandubh") == league_ids("brandubh", 3)
    with rig.store.connection() as c:
        seasons = {
            row[0]
            for row in c.execute(
                "SELECT season_id FROM league_standings WHERE account_id=? AND ruleset='brandubh'",
                (account,),
            )
        }
        assert seasons == {"2027-01", "2027-02"}
        assert (
            c.execute(
                """SELECT COUNT(*) FROM achievement_awards a JOIN achievement_catalog c
               ON c.id=a.achievement_id
               WHERE a.account_id=? AND c.criterion='league_tier' AND c.scope='brandubh'""",
                (account,),
            ).fetchone()[0]
            == 3
        )


def test_official_league_results_qualify_midgard_and_isolate_rulesets(rig):
    service = Leagues(rig.core)
    account = rig.accounts["Archer"]["id"]
    for result in range(3):
        finish_league_game(service, rig, "Keeper")
        service.drain()
        if result < 2:
            assert peak(rig, account, "brandubh") == 0
            assert awarded(rig, account, "brandubh") == set()
    assert peak(rig, account, "brandubh") == 2
    assert awarded(rig, account, "brandubh") == league_ids("brandubh", 2)
    assert peak(rig, account, "tablut") == 0
    assert peak(rig, account, "copenhagen") == 0

    # Qualified opponents with zero points still receive Midgard; the same
    # official results cannot leak into another ruleset.
    assert peak(rig, rig.accounts["Keeper"]["id"], "brandubh") == 1
    assert awarded(rig, rig.accounts["Keeper"]["id"], "tablut") == set()


def test_pending_void_blocks_the_invalid_midgard_qualification(rig):
    service = Leagues(rig.core)
    acct = rig.accounts["Archer"]["id"]
    games = []
    for _ in range(2):
        games.append(finish_league_game(service, rig, "Keeper"))
        service.drain()
    assert peak(rig, acct, "brandubh") == 0
    third = finish_league_game(service, rig, "Keeper")
    rig.core.mutate(
        rig.credentials["Odin"],
        games[0]["id"],
        games[0]["version"],
        "void",
        note="Official correction awaiting settlement",
    )
    with rig.core._write() as c:
        now = micros(rig.clock.value)
        c.execute(
            "UPDATE league_settlement_queue SET due_at=? WHERE game_id=?",
            (now + 3_600_000_000, games[0]["id"]),
        )
        c.execute("UPDATE league_settlement_queue SET due_at=? WHERE game_id=?", (now, third["id"]))

    # The new result settles while the invalidation is still pending. It must
    # not grant the stale third result's qualification.
    assert service.drain()["settled"] == 1
    assert peak(rig, acct, "brandubh") == 0
    assert awarded(rig, acct, "brandubh") == set()
    with rig.store.connection() as c:
        candidate = c.execute(
            """SELECT candidate_tier FROM league_achievement_reconciliation
               WHERE account_id=? AND ruleset='brandubh'""",
            (acct,),
        ).fetchone()
    assert candidate[0] == 2

    rig.clock.value += timedelta(hours=2)
    assert service.drain()["settled"] == 1
    assert peak(rig, acct, "brandubh") == 0
    assert awarded(rig, acct, "brandubh") == set()
    with rig.store.connection() as c:
        assert (
            c.execute(
                """SELECT COUNT(*) FROM league_achievement_reconciliation
               WHERE account_id=? AND ruleset='brandubh'""",
                (acct,),
            ).fetchone()[0]
            == 0
        )


def test_pending_correction_crossing_a_tier_boundary_grants_only_confirmed_peaks(rig):
    service = Leagues(rig.core)
    acct = rig.accounts["Archer"]["id"]
    games = []
    for _ in range(3):
        games.append(finish_league_game(service, rig, "Keeper"))
        service.drain()
    rig.clock.value += timedelta(days=1)
    games.append(finish_league_game(service, rig, "Keeper"))
    service.drain()
    assert peak(rig, acct, "brandubh") == 2

    fifth = finish_league_game(service, rig, "Keeper")
    rig.core.mutate(
        rig.credentials["Odin"],
        games[0]["id"],
        games[0]["version"],
        "void",
        note="Cross-boundary pending correction",
    )
    with rig.core._write() as c:
        now = micros(rig.clock.value)
        c.execute(
            "UPDATE league_settlement_queue SET due_at=? WHERE game_id=?",
            (now + 3_600_000_000, games[0]["id"]),
        )
        c.execute("UPDATE league_settlement_queue SET due_at=? WHERE game_id=?", (now, fifth["id"]))

    service.drain()
    # The stale ledger would cross Alfheim. Confirm only the already legitimate
    # Nidavellir peak remains until the correction is settled.
    assert peak(rig, acct, "brandubh") == 2
    assert awarded(rig, acct, "brandubh") == league_ids("brandubh", 2)

    rig.clock.value += timedelta(hours=2)
    service.drain()
    assert peak(rig, acct, "brandubh") == 2
    assert awarded(rig, acct, "brandubh") == league_ids("brandubh", 2)


def test_deferred_correction_reconciliation_survives_worker_restart(rig):
    service = Leagues(rig.core)
    acct = rig.accounts["Archer"]["id"]
    games = []
    for _ in range(2):
        games.append(finish_league_game(service, rig, "Keeper"))
        service.drain()
    third = finish_league_game(service, rig, "Keeper")
    rig.core.mutate(
        rig.credentials["Odin"],
        games[0]["id"],
        games[0]["version"],
        "void",
        note="Restart reconciliation fixture",
    )
    with rig.core._write() as c:
        now = micros(rig.clock.value)
        c.execute(
            "UPDATE league_settlement_queue SET due_at=? WHERE game_id=?",
            (now + 3_600_000_000, games[0]["id"]),
        )
        c.execute("UPDATE league_settlement_queue SET due_at=? WHERE game_id=?", (now, third["id"]))
    service.drain()
    assert peak(rig, acct, "brandubh") == 0
    assert awarded(rig, acct, "brandubh") == set()

    # A fresh worker processes the durable correction and reconciliation state.
    restarted = Leagues(rig.core)
    rig.clock.value += timedelta(hours=2)
    assert restarted.drain()["settled"] == 1
    assert peak(rig, acct, "brandubh") == 0
    assert awarded(rig, acct, "brandubh") == set()


def test_void_before_settlement_grants_nothing_and_later_corrections_keep_rewards(rig):
    service = Leagues(rig.core)
    account = rig.accounts["Archer"]["id"]
    game = finish_league_game(service, rig, "Keeper")
    rig.core.mutate(
        rig.credentials["Odin"], game["id"], game["version"], "void", note="Voided fixture"
    )
    service.drain()
    assert peak(rig, account, "brandubh") == 0
    assert awarded(rig, account, "brandubh") == set()

    rig.clock.value += timedelta(days=1)
    settled = []
    for _ in range(3):
        settled.append(finish_league_game(service, rig, "Keeper"))
        service.drain()
    assert awarded(rig, account, "brandubh") == league_ids("brandubh", 2)
    before = awarded(rig, account, "brandubh")
    rig.core.mutate(
        rig.credentials["Odin"],
        settled[0]["id"],
        settled[0]["version"],
        "void",
        note="Correction after award",
    )
    service.drain()
    assert awarded(rig, account, "brandubh") == before
    assert peak(rig, account, "brandubh") == 2


def test_duplicate_and_concurrent_settlement_never_duplicates_league_awards(rig):
    service = Leagues(rig.core)
    account = rig.accounts["Archer"]["id"]
    for _ in range(3):
        finish_league_game(service, rig, "Keeper")
    with ThreadPoolExecutor(max_workers=2) as workers:
        list(workers.map(lambda _: Leagues(rig.core).drain(), range(2)))
    expected = league_ids("brandubh", 2)
    assert awarded(rig, account, "brandubh") == expected
    assert peak(rig, account, "brandubh") == 2
    with rig.store.connection() as c:
        row = c.execute(
            "SELECT game_id FROM league_games ORDER BY paired_at DESC LIMIT 1"
        ).fetchone()
        before = c.execute(
            "SELECT COUNT(*) FROM achievement_awards WHERE account_id=? AND achievement_id IN "
            "(SELECT id FROM achievement_catalog WHERE criterion='league_tier')",
            (account,),
        ).fetchone()[0]
    with rig.core._write() as c:
        assert settle_result(c, row[0], rig.clock.value) is False
    with rig.store.connection() as c:
        after = c.execute(
            "SELECT COUNT(*) FROM achievement_awards WHERE account_id=? AND achievement_id IN "
            "(SELECT id FROM achievement_catalog WHERE criterion='league_tier')",
            (account,),
        ).fetchone()[0]
    assert after == before == 2


def test_historical_backfill_recovers_a_lost_peak_from_the_authoritative_ledger(rig):
    service = Leagues(rig.core)
    acct = rig.accounts["Archer"]["id"]
    # Three wins reach Nidavellir; four later losses lower the final standing to
    # Midgard while the historical peak remains Nidavellir.
    for _ in range(3):
        finish_league_game(service, rig, "Keeper")
        service.drain()
    rig.clock.value += timedelta(days=1)
    for _ in range(3):
        finish_league_game(service, rig, "Archer")
        service.drain()
    rig.clock.value += timedelta(days=1)
    finish_league_game(service, rig, "Archer")
    service.drain()
    with rig.store.connection() as c:
        final = c.execute(
            "SELECT points FROM league_standings WHERE account_id=? AND ruleset='brandubh'",
            (acct,),
        ).fetchone()[0]
    assert final == 5
    reset_league_progress(rig, acct)
    with rig.store.connection() as c:
        c.execute(
            "INSERT INTO achievement_awards VALUES(?,?,?,?)",
            (acct, "rated-wins-10", 5, ACHIEVEMENT_CATALOG_VERSION),
        )
        c.execute("PRAGMA user_version=23")

    reopened = Store(rig.store.path)
    with reopened.connection() as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert (
            c.execute(
                "SELECT COUNT(*) FROM league_achievement_progress WHERE account_id=?", (acct,)
            ).fetchone()[0]
            == 0
        )
    # Migration queues bounded verification instead of trusting the partially
    # cleared ledger or final standings. The worker validates effective results.
    assert Leagues(rig.core).drain()["achievements"] >= 1
    with reopened.connection() as c:
        assert (
            c.execute(
                "SELECT highest_tier FROM league_achievement_progress WHERE account_id=? AND ruleset='brandubh'",
                (acct,),
            ).fetchone()[0]
            == 2
        )
        ids = {
            row[0]
            for row in c.execute(
                "SELECT achievement_id FROM achievement_awards WHERE account_id=?", (acct,)
            )
        }
    assert ids & league_ids("brandubh") == league_ids("brandubh", 2)
    assert "rated-wins-10" in ids
    # A second startup is idempotent and cannot change the reconstructed peak.
    with reopened.connection() as c:
        before = [
            tuple(row)
            for row in c.execute(
                "SELECT * FROM league_achievement_progress ORDER BY account_id,ruleset"
            )
        ]
        before_awards = [
            tuple(row) for row in c.execute("SELECT * FROM achievement_awards ORDER BY 1,2")
        ]
    Store(reopened.path)
    with reopened.connection() as c:
        assert [
            tuple(row)
            for row in c.execute(
                "SELECT * FROM league_achievement_progress ORDER BY account_id,ruleset"
            )
        ] == before
        assert [
            tuple(row) for row in c.execute("SELECT * FROM achievement_awards ORDER BY 1,2")
        ] == before_awards


def _administration(rig):
    return LeagueAdministration(LeagueViews(Leagues(rig.core), LeagueCursors()))


def test_achievement_reconciliation_retries_durably(rig, monkeypatch):
    service = Leagues(rig.core)
    acct = rig.accounts["Archer"]["id"]
    for _ in range(3):
        finish_league_game(service, rig, "Keeper")
        service.drain()
    reset_league_progress(rig, acct)
    with rig.store.connection() as c:
        season_id = c.execute("SELECT season_id FROM league_games LIMIT 1").fetchone()[0]
        c.execute(
            """INSERT INTO league_achievement_reconciliation
               (account_id,season_id,ruleset,candidate_tier,due_at)
               VALUES(?,?,'brandubh',2,0)""",
            (acct, season_id),
        )

    def fail(*args):
        raise RuntimeError("private reconciliation failure")

    monkeypatch.setattr(leagues_module, "confirmed_account_progress", fail)
    failed = service.drain()
    assert failed["achievement_retries"] == 1
    assert failed["achievements"] == 0
    with rig.store.connection() as c:
        row = c.execute(
            """SELECT attempts,due_at FROM league_achievement_reconciliation
               WHERE account_id=? AND season_id=? AND ruleset='brandubh'""",
            (acct, season_id),
        ).fetchone()
    assert row["attempts"] == 1 and row["due_at"] > 0
    assert peak(rig, acct, "brandubh") == 0

    rig.clock.value += timedelta(seconds=3)
    monkeypatch.undo()
    assert service.drain()["achievements"] == 1
    assert peak(rig, acct, "brandubh") == 2
    assert awarded(rig, acct, "brandubh") == league_ids("brandubh", 2)
    assert service.drain()["achievements"] == 0


def test_genuine_schema_23_upgrade_defers_pending_corrections_before_awarding(rig):
    service = Leagues(rig.core)
    acct = rig.accounts["Archer"]["id"]
    games = []
    for _ in range(2):
        games.append(finish_league_game(service, rig, "Keeper"))
        service.drain()
    third = finish_league_game(service, rig, "Keeper")
    rig.core.mutate(
        rig.credentials["Odin"],
        games[0]["id"],
        games[0]["version"],
        "void",
        note="Schema upgrade pending correction",
    )
    with rig.core._write() as c:
        now = micros(rig.clock.value)
        c.execute(
            "UPDATE league_settlement_queue SET due_at=? WHERE game_id=?",
            (now + 3_600_000_000, games[0]["id"]),
        )
        c.execute("UPDATE league_settlement_queue SET due_at=? WHERE game_id=?", (now, third["id"]))
    # A genuine schema 23 database has neither achievement table.
    reset_league_progress(rig, acct)
    with rig.store.connection() as c:
        c.execute("DROP TABLE league_achievement_reconciliation")
        c.execute("DROP TABLE league_achievement_progress")
        c.execute("PRAGMA user_version=23")
    reopened = Store(rig.store.path)
    with reopened.connection() as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert c.execute("SELECT COUNT(*) FROM league_achievement_reconciliation").fetchone()[0] > 0
        assert (
            c.execute(
                "SELECT COUNT(*) FROM achievement_awards WHERE account_id=? AND achievement_id LIKE '%league%'",
                (acct,),
            ).fetchone()[0]
            == 0
        )

    assert Leagues(rig.core).drain()["settled"] == 1
    assert peak(rig, acct, "brandubh") == 0
    assert awarded(rig, acct, "brandubh") == set()
    rig.clock.value += timedelta(hours=2)
    assert Leagues(rig.core).drain()["settled"] == 1
    assert peak(rig, acct, "brandubh") == 0
    assert awarded(rig, acct, "brandubh") == set()


@pytest.mark.parametrize(
    "stage",
    ["clear_contributions", "clear_standings", "restore_games", "fold_standings", "catch_up"],
)
def test_schema_23_upgrade_is_safe_in_every_rebuild_phase(rig, stage):
    service = Leagues(rig.core)
    acct = rig.accounts["Archer"]["id"]
    terminals = []
    for _ in range(3):
        terminals.append(finish_league_game(service, rig, "Keeper"))
        service.drain()
    assert peak(rig, acct, "brandubh") == 2
    reset_league_progress(rig, acct)
    with rig.store.connection() as c:
        season_id = c.execute("SELECT season_id FROM league_games LIMIT 1").fetchone()[0]
        if stage in {"clear_standings", "restore_games"}:
            c.execute("DELETE FROM league_contributions WHERE season_id=?", (season_id,))
        if stage in {"restore_games", "fold_standings"}:
            c.execute("DELETE FROM league_standings WHERE season_id=?", (season_id,))
        c.execute(
            "INSERT INTO league_rebuild_jobs(season_id,due_at,stage) VALUES(?,0,?)",
            (season_id, stage),
        )
        c.execute("DROP TABLE league_achievement_reconciliation")
        c.execute("DROP TABLE league_achievement_progress")
        c.execute("PRAGMA user_version=23")

    reopened = Store(rig.store.path)
    # Reconciliation rows exist, but the rebuild scope remains unavailable.
    assert Leagues(rig.core).drain()["achievements"] == 0
    assert peak(rig, acct, "brandubh") == 0
    assert awarded(rig, acct, "brandubh") == set()

    administration = _administration(rig)
    assert administration.process_rebuild(limit=1) == "processed"
    # A restarted worker resumes solely from durable rebuild and reconciliation
    # state.
    administration = _administration(rig)
    for _ in range(40):
        if administration.process_rebuild(limit=2) == "idle":
            break
    else:
        pytest.fail("Rebuild did not complete")
    with rig.store.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM league_rebuild_jobs").fetchone()[0] == 0
    # The completed rebuild is validated by a bounded worker pass before awards.
    assert Leagues(rig.core).drain()["achievements"] >= 1
    assert peak(rig, acct, "brandubh") == 2
    assert awarded(rig, acct, "brandubh") == league_ids("brandubh", 2)
    with reopened.connection() as c:
        assert (
            c.execute(
                """SELECT COUNT(*) FROM achievement_awards a JOIN achievement_catalog c
               ON c.id=a.achievement_id
               WHERE a.account_id=? AND c.criterion='league_tier'""",
                (acct,),
            ).fetchone()[0]
            == 2
        )
    # Repeated work is idempotent.
    assert Leagues(rig.core).drain()["achievements"] == 0
    assert awarded(rig, acct, "brandubh") == league_ids("brandubh", 2)


def test_correction_between_restore_and_fold_cannot_grant_candidate_peak(rig):
    service = Leagues(rig.core)
    acct = rig.accounts["Archer"]["id"]
    terminals = []
    for _ in range(3):
        terminals.append(finish_league_game(service, rig, "Keeper"))
        service.drain()
    reset_league_progress(rig, acct)
    with rig.store.connection() as c:
        season_id = c.execute("SELECT season_id FROM league_games LIMIT 1").fetchone()[0]
        c.execute(
            "INSERT INTO league_rebuild_jobs(season_id,due_at,stage) VALUES(?,0,'restore_games')",
            (season_id,),
        )
        c.execute("DELETE FROM league_contributions WHERE season_id=?", (season_id,))
        c.execute("DELETE FROM league_standings WHERE season_id=?", (season_id,))

    administration = _administration(rig)
    # Restore contributions, then let a correction arrive before folding.
    for _ in range(20):
        assert administration.process_rebuild(limit=1) == "processed"
        with rig.store.connection() as c:
            stage = c.execute(
                "SELECT stage FROM league_rebuild_jobs WHERE season_id=?", (season_id,)
            ).fetchone()[0]
        if stage == "fold_standings":
            break
    else:
        pytest.fail("Restore did not reach the fold phase")
    rig.core.mutate(
        rig.credentials["Odin"],
        terminals[0]["id"],
        terminals[0]["version"],
        "void",
        note="Correction between restore and fold",
    )
    for _ in range(20):
        if administration.process_rebuild(limit=1) == "idle":
            break
    else:
        pytest.fail("Rebuild catch-up did not complete")
    assert Leagues(rig.core).drain()["achievements"] >= 1
    # Two eligible results remain after the correction.
    assert peak(rig, acct, "brandubh") == 0
    assert awarded(rig, acct, "brandubh") == set()


def test_backfill_does_not_fabricate_progress_from_standings_alone(rig):
    acct = rig.accounts["Keeper"]["id"]
    with rig.store.connection() as c:
        c.execute(
            """INSERT OR IGNORE INTO league_seasons(id,starts_at,ends_at,grace_ends_at,policy_version)
               VALUES('2026-11',1,2,3,1)"""
        )
        c.execute(
            """INSERT INTO league_standings
               (season_id,ruleset,account_id,points,wins,draws,losses,updated_at)
               VALUES('2026-11','tablut',?,50,3,0,0,1)""",
            (acct,),
        )
        c.execute("PRAGMA user_version=23")
    reopened = Store(rig.store.path)
    with reopened.connection() as c:
        assert (
            c.execute(
                "SELECT COUNT(*) FROM league_achievement_progress WHERE account_id=?", (acct,)
            ).fetchone()[0]
            == 0
        )
        assert (
            c.execute(
                """SELECT COUNT(*) FROM achievement_awards a JOIN achievement_catalog c ON c.id=a.achievement_id
               WHERE a.account_id=? AND c.criterion='league_tier'""",
                (acct,),
            ).fetchone()[0]
            == 0
        )


def test_representative_multi_season_migration_and_reconciliation_stays_bounded(rig):
    service = Leagues(rig.core)
    acct = rig.accounts["Archer"]["id"]
    for month in (1, 2, 3):
        rig.clock.value = datetime(2027, month, 2, 3, 4, 5, 123456, tzinfo=timezone.utc)
        for _ in range(3):
            finish_league_game(service, rig, "Keeper")
            service.drain()
    reset_league_progress(rig, acct)
    with rig.store.connection() as c:
        c.execute("DROP TABLE league_achievement_reconciliation")
        c.execute("DROP TABLE league_achievement_progress")
        c.execute("PRAGMA user_version=23")

    started = time.perf_counter()
    reopened = Store(rig.store.path)
    migrated = time.perf_counter() - started
    with reopened.connection() as c:
        queued = c.execute("SELECT COUNT(*) FROM league_achievement_reconciliation").fetchone()[0]
        assert queued >= 6
        assert c.execute("SELECT COUNT(*) FROM league_achievement_progress").fetchone()[0] == 0

    restored = Leagues(rig.core)
    started = time.perf_counter()
    for _ in range(10):
        result = restored.drain(limit=5)
        if result["achievements"] == 0:
            break
    reconciled = time.perf_counter() - started
    assert peak(rig, acct, "brandubh") == 2
    assert awarded(rig, acct, "brandubh") == league_ids("brandubh", 2)
    # These are deliberately generous regression bounds on representative
    # multi-season evidence, not production throughput claims.
    assert migrated < 10
    assert reconciled < 10


def test_hall_hides_incomplete_seasonal_progress_but_keeps_permanent_tiers(rig):
    service = Leagues(rig.core)
    acct = rig.accounts["Archer"]["id"]
    for _ in range(3):
        finish_league_game(service, rig, "Keeper")
        service.drain()
    projection = Progression(rig.core).achievements(rig.credentials["Archer"])
    family = next(item for item in projection["families"] if item["id"] == "brandubh-league")
    assert family["league"]["season"]["available"] is True
    assert family["league"]["season"]["points"] == 9
    assert family["league"]["highest_tier"] == "nidavellir"

    with rig.store.connection() as c:
        season_id = c.execute("SELECT season_id FROM league_games LIMIT 1").fetchone()[0]
        c.execute("INSERT INTO league_rebuild_jobs(season_id,due_at) VALUES(?,0)", (season_id,))
    rebuilding = Progression(rig.core).achievements(rig.credentials["Archer"])
    family = next(item for item in rebuilding["families"] if item["id"] == "brandubh-league")
    assert family["awarded_count"] == 2
    assert family["league"]["highest_tier"] == "nidavellir"
    assert family["league"]["season"] == {
        "id": season_id,
        "available": False,
        "status": "rebuilding",
        "points": None,
        "tier": None,
        "tier_name": None,
        "qualified": None,
        "played": None,
        "qualifying_results": 3,
        "next_tier": None,
    }
    with rig.store.connection() as c:
        c.execute("DELETE FROM league_rebuild_jobs WHERE season_id=?", (season_id,))
    recovered = Progression(rig.core).achievements(rig.credentials["Archer"])
    family = next(item for item in recovered["families"] if item["id"] == "brandubh-league")
    assert family["league"]["season"]["available"] is True
    assert family["league"]["season"]["points"] == 9
    assert family["league"]["season"]["next_tier"]["id"] == "alfheim"
    assert awarded(rig, acct, "brandubh") == league_ids("brandubh", 2)


def test_disabled_league_features_never_hide_or_delete_earned_achievements(rig):
    service = Leagues(rig.core)
    acct = rig.accounts["Archer"]["id"]
    for _ in range(3):
        finish_league_game(service, rig, "Keeper")
        service.drain()
    before = awarded(rig, acct, "brandubh")
    assert before == league_ids("brandubh", 2)
    disabled = replace(POLICY, enabled=False)
    projection = Progression(OnlineCore(rig.auth, disabled, rig.clock)).achievements(
        rig.credentials["Archer"]
    )
    family = next(item for item in projection["families"] if item["id"] == "brandubh-league")
    assert family["awarded_count"] == 2
    assert family["league"]["highest_tier"] == "nidavellir"
    assert family["league"]["season"]["id"] == policy.season_for(rig.clock.value).identifier
    assert awarded(rig, acct, "brandubh") == before
