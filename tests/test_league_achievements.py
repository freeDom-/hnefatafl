"""Ruleset-specific permanent league tiers in the existing achievement authority."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest


from hnefatafl import league_policy as policy
from hnefatafl.engine import RULESETS
from hnefatafl.league_results import (
    RECONCILIATION_ROWS,
    authoritative_contribution_chunk,
    settle_result,
    validated_account_contribution,
)
from hnefatafl.league_administration import LeagueAdministration
from hnefatafl.league_views import LeagueCursors, LeagueViews
from hnefatafl import leagues as leagues_module
from hnefatafl import progression as progression_module
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


def test_already_completed_tier_avoids_evidence_checks_and_candidate_writes(rig, monkeypatch):
    acct = rig.accounts["Archer"]["id"]
    season_id = policy.season_for(rig.clock.value).identifier
    with rig.core._write() as c:
        c.execute(
            """INSERT INTO league_achievement_progress
               (account_id,ruleset,highest_tier,achieved_at) VALUES(?, 'brandubh',5,1)""",
            (acct,),
        )

    def unexpected(*args, **kwargs):
        raise AssertionError("completed tier performed an evidence check")

    monkeypatch.setattr(progression_module, "league_achievement_evidence_pending", unexpected)
    with rig.core._write() as c:
        assert not record_league_tier(
            c,
            acct,
            season_id,
            "brandubh",
            standing(50),
            2,
        )
    with rig.store.connection() as c:
        assert (
            c.execute(
                """SELECT COUNT(*) FROM league_achievement_reconciliation
               WHERE account_id=? AND season_id=? AND ruleset='brandubh'""",
                (acct, season_id),
            ).fetchone()[0]
            == 0
        )


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


def test_multiple_pending_settlements_back_off_and_wait_for_all_evidence(rig):
    service = Leagues(rig.core)
    acct = rig.accounts["Archer"]["id"]
    for _ in range(3):
        finish_league_game(service, rig, "Keeper")
        service.drain()
    rig.clock.value += timedelta(days=1)
    finish_league_game(service, rig, "Keeper")
    service.drain()
    assert peak(rig, acct, "brandubh") == 2

    rig.clock.value += timedelta(days=1)
    pending_loss = finish_league_game(service, rig, "Archer")
    with rig.core._write() as c:
        c.execute("DELETE FROM league_active_players WHERE game_id=?", (pending_loss["id"],))
        c.execute(
            "UPDATE league_settlement_queue SET due_at=? WHERE game_id=?",
            (micros(rig.clock.value) + 7_200_000_000, pending_loss["id"]),
        )
    fifth = finish_league_game(service, rig, "Keeper")
    with rig.core._write() as c:
        c.execute(
            "UPDATE league_settlement_queue SET due_at=? WHERE game_id=?",
            (micros(rig.clock.value), fifth["id"]),
        )

    # Only the later win settles. Its stale five-win standing would cross
    # Alfheim, but the earlier pending loss keeps it as a backed-off candidate.
    assert service.drain(limit=1)["settled"] == 1
    assert peak(rig, acct, "brandubh") == 2
    assert "brandubh-league-alfheim" not in awarded(rig, acct, "brandubh")
    with rig.store.connection() as c:
        row = c.execute(
            """SELECT attempts,due_at FROM league_achievement_reconciliation
               WHERE account_id=? AND ruleset='brandubh'""",
            (acct,),
        ).fetchone()
    assert row["attempts"] >= 1
    assert row["due_at"] > micros(rig.clock.value)

    # The deferred loss wakes the scope. The confirmed five wins plus two
    # losses are 13 points, so the permanent peak remains Nidavellir.
    rig.clock.value += timedelta(hours=3)
    assert service.drain(limit=1)["settled"] == 1
    assert peak(rig, acct, "brandubh") == 2
    assert awarded(rig, acct, "brandubh") == league_ids("brandubh", 2)
    with rig.store.connection() as c:
        assert (
            c.execute(
                """SELECT COUNT(*) FROM league_achievement_reconciliation
               WHERE account_id=? AND ruleset='brandubh'""",
                (acct,),
            ).fetchone()[0]
            == 0
        )


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


def test_out_of_order_replay_waits_for_the_pending_intermediate_loss(rig):
    service = Leagues(rig.core)
    acct = rig.accounts["Archer"]["id"]
    games = []

    # Day 1: initial loss plus two victories (the daily pair limit is three).
    games.append(finish_league_game(service, rig, "Archer"))
    service.drain()
    for _ in range(2):
        games.append(finish_league_game(service, rig, "Keeper"))
        service.drain()

    # Day 2: the remaining two of four victories.
    rig.clock.value += timedelta(days=1)
    for _ in range(2):
        games.append(finish_league_game(service, rig, "Keeper"))
        service.drain()
    assert peak(rig, acct, "brandubh") == 2

    # Day 3: complete an official loss but leave its league settlement pending.
    rig.clock.value += timedelta(days=1)
    pending_loss = finish_league_game(service, rig, "Archer")
    with rig.core._write() as c:
        c.execute("DELETE FROM league_active_players WHERE game_id=?", (pending_loss["id"],))
        now = micros(rig.clock.value)
        c.execute(
            "UPDATE league_settlement_queue SET due_at=? WHERE game_id=?",
            (now + 7_200_000_000, pending_loss["id"]),
        )

    # The fifth victory settles while the earlier loss remains unresolved. Its
    # stale standing would cross Alfheim, so it must only become a candidate.
    fifth = finish_league_game(service, rig, "Keeper")
    with rig.core._write() as c:
        c.execute("UPDATE league_settlement_queue SET due_at=? WHERE game_id=?", (now, fifth["id"]))
    assert service.drain()["settled"] == 1
    assert peak(rig, acct, "brandubh") == 2
    assert "brandubh-league-alfheim" not in awarded(rig, acct, "brandubh")

    # Void the initial loss and process that replay while the loss is still pending.
    rig.core.mutate(
        rig.credentials["Odin"],
        games[0]["id"],
        games[0]["version"],
        "void",
        note="Out-of-order replay fixture",
    )
    with rig.core._write() as c:
        c.execute(
            "UPDATE league_settlement_queue SET due_at=? WHERE game_id=?", (now, games[0]["id"])
        )
    assert service.drain()["settled"] == 1
    assert peak(rig, acct, "brandubh") == 2
    assert "brandubh-league-alfheim" not in awarded(rig, acct, "brandubh")

    # Processing the deferred loss yields five wins and one loss: 14 points and
    # Nidavellir peak, never Alfheim.
    rig.clock.value += timedelta(hours=3)
    for _ in range(4):
        result = service.drain()
        if result["achievements"] == 0 and result["settled"] == 0:
            break
    assert peak(rig, acct, "brandubh") == 2
    assert awarded(rig, acct, "brandubh") == league_ids("brandubh", 2)


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

    monkeypatch.setattr(leagues_module, "validated_account_contribution", fail)
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


def seed_synthetic_league_history(
    rig,
    count,
    outcomes,
    season,
    *,
    ruleset="brandubh",
    base_sequence=10_000,
    label="synthetic",
    include_league=True,
):
    """Seed authoritative provenance without paying engine cost per game."""
    acct = rig.accounts["Archer"]["id"]
    opponent = rig.accounts["Keeper"]["id"]
    first, second = sorted((acct, opponent))
    start = micros(season.start)
    end = micros(season.end)
    grace = micros(season.grace_end)
    base = base_sequence
    with rig.core._write() as c:
        c.execute(
            """INSERT OR IGNORE INTO league_seasons
               (id,starts_at,ends_at,grace_ends_at,policy_version)
               VALUES(?,?,?,?,1)""",
            (season.identifier, start, end, grace),
        )
        for offset, outcome in enumerate(outcomes):
            index = base + offset
            game_id = f"{label}-{index}"
            match_id = f"{label}-match-{index}"
            completed = start + offset + 1
            c.execute(
                """INSERT INTO online_matches(
                   id,creator_id,opponent_id,side_selection,mode,ruleset,ruleset_version,
                   time_per_move,created_at,activated_at,first_creator_side,rated,
                   time_control,increment_seconds)
                   VALUES(?,?,?,'random','single',?,'1',600,?,?,'attackers',1,'total',5)""",
                (match_id, acct, opponent, ruleset, start, start),
            )
            c.execute(
                """INSERT INTO online_games(
                   id,creation_request,match_id,leg,creator_id,creator_side,
                   attackers_id,defenders_id,ruleset,ruleset_version,time_per_move,
                   status,version,created_at,updated_at,started_at,completed_at,
                   position,result_revision,time_control,increment_seconds,
                   attackers_remaining_us,defenders_remaining_us)
                   VALUES(?,?,?,1,?,'attackers',?,?,?,'1',600,
                   'completed',1,?,?,?,?,'{}',1,'total',5,0,0)""",
                (
                    game_id,
                    f"{label}-request-{index}",
                    match_id,
                    acct,
                    acct,
                    opponent,
                    ruleset,
                    start,
                    start,
                    start,
                    completed,
                ),
            )
            c.execute(
                """INSERT INTO online_results
                   (game_id,revision,supersedes,competitive_eligible,abandoned_account_id,
                    completed_at,processed_at,payload) VALUES(?,1,NULL,1,NULL,?,?,'{}')""",
                (game_id, completed, completed),
            )
            winner = "attackers" if outcome == "win" else "defenders"
            c.execute(
                """INSERT INTO rating_events
                   (game_id,pool,sequence,state,effective_revision,winner,attackers_id,
                    defenders_id,completed_at,policy_version,created_at)
                   VALUES(?,?,?,'applied',1,?,?,?,?,1,?)""",
                (game_id, ruleset, index, winner, acct, opponent, completed, completed),
            )
            for account, other, score in (
                (acct, opponent, 1.0 if outcome == "win" else 0.0),
                (opponent, acct, 0.0 if outcome == "win" else 1.0),
            ):
                c.execute(
                    """INSERT INTO rating_applications
                       (game_id,account_id,pool,sequence,opponent_id,rating_before,
                        rating_after,delta,opponent_rating,expected,score,rated_games_after,
                        policy_version)
                       VALUES(?,?,?,?,?,1500,1500,0,1500,0.5,?,?,1)""",
                    (game_id, account, ruleset, index, other, score, offset + 1),
                )
            if include_league:
                c.execute(
                    """INSERT INTO league_games
                       (game_id,season_id,ruleset,first_account,second_account,paired_at,
                        sequence,result_revision,state)
                       VALUES(?,?,?,?,?,?,?,1,'eligible')""",
                    (game_id, season.identifier, ruleset, first, second, start + offset, index),
                )


def test_reconciliation_progress_survives_unrelated_same_season_activity(rig):
    season = policy.season_for(datetime(2030, 1, 10, tzinfo=timezone.utc))
    rig.clock.value = datetime(2030, 1, 10, 3, 4, 5, tzinfo=timezone.utc)
    seed_synthetic_league_history(
        rig,
        900,
        ["win"] * 900,
        season,
        base_sequence=10_000,
        label="continuous",
    )
    seed_synthetic_league_history(
        rig,
        1,
        ["win"],
        season,
        ruleset="tablut",
        base_sequence=30_000,
        label="other-ruleset",
    )
    acct = rig.accounts["Archer"]["id"]
    service = Leagues(rig.core)
    with rig.core._write() as c:
        c.execute(
            """INSERT INTO league_achievement_reconciliation
               (account_id,season_id,ruleset,candidate_tier,due_at)
               VALUES(?,?, 'brandubh',5,0)""",
            (acct, season.identifier),
        )

    with rig.store.connection() as c:
        c.execute("UPDATE league_seasons SET revision=revision+1 WHERE id=?", (season.identifier,))

    assert service.drain()["achievements"] == 0
    with rig.store.connection() as c:
        cursor = c.execute(
            """SELECT cursor_sequence FROM league_achievement_reconciliation
               WHERE account_id=? AND season_id=? AND ruleset='brandubh'""",
            (acct, season.identifier),
        ).fetchone()[0]
    assert cursor == 10_000 + RECONCILIATION_ROWS - 1

    # A different ruleset involving the same account changes the season revision
    # but not the Brandubh evidence generation.
    with rig.core._write() as c:
        assert settle_result(c, "other-ruleset-30000", rig.clock.value)
    service.drain()
    with rig.store.connection() as c:
        cursor = c.execute(
            """SELECT cursor_sequence FROM league_achievement_reconciliation
               WHERE account_id=? AND season_id=? AND ruleset='brandubh'""",
            (acct, season.identifier),
        ).fetchone()[0]
    assert cursor == 10_000 + 2 * RECONCILIATION_ROWS - 1

    # Unrelated accounts settling in the same season also leave the scope intact.
    finish_league_game(service, rig, "Odin", order=("Visitor", "Odin"))
    assert service.drain(limit=1)["settled"] == 1
    with rig.store.connection() as c:
        cursor = c.execute(
            """SELECT cursor_sequence FROM league_achievement_reconciliation
               WHERE account_id=? AND season_id=? AND ruleset='brandubh'""",
            (acct, season.identifier),
        ).fetchone()[0]
    assert cursor == 10_000 + 3 * RECONCILIATION_ROWS - 1

    rig.clock.value += timedelta(days=1)
    finish_league_game(service, rig, "Odin", order=("Visitor", "Odin"))
    for _ in range(4):
        service.drain()
        with rig.store.connection() as c:
            remaining = c.execute(
                """SELECT COUNT(*) FROM league_achievement_reconciliation
                   WHERE account_id=? AND season_id=? AND ruleset='brandubh'""",
                (acct, season.identifier),
            ).fetchone()[0]
        if not remaining:
            break
    assert remaining == 0
    assert peak(rig, acct, "brandubh") == 5


def test_large_history_reconciliation_is_chunked_restartable_and_evidence_aware(rig):
    season = policy.season_for(datetime(2030, 1, 15, tzinfo=timezone.utc))
    rig.clock.value = datetime(2030, 1, 15, 3, 4, 5, tzinfo=timezone.utc)
    outcomes = ["win", "win", "win"] + ["loss"] * 597
    seed_synthetic_league_history(rig, len(outcomes), outcomes, season)
    acct = rig.accounts["Archer"]["id"]
    with rig.core._write() as c:
        c.execute(
            """INSERT INTO league_achievement_reconciliation
               (account_id,season_id,ruleset,candidate_tier,due_at)
               VALUES(?,?, 'brandubh',2,0)""",
            (acct, season.identifier),
        )

    service = Leagues(rig.core)
    first = service.drain()
    assert first["achievements"] == 0
    with rig.store.connection() as c:
        row = c.execute(
            """SELECT cursor_sequence,evidence_generation,progress_tier
               FROM league_achievement_reconciliation
               WHERE account_id=? AND season_id=? AND ruleset='brandubh'""",
            (acct, season.identifier),
        ).fetchone()
    assert row["cursor_sequence"] == 10_000 + RECONCILIATION_ROWS - 1
    assert row["evidence_generation"] == 0
    assert row["progress_tier"] == 2
    assert peak(rig, acct, "brandubh") == 0

    # A relevant official result in this scope invalidates the accumulated
    # evidence and resets the durable cursor before a mixed-generation award.
    finish_league_game(service, rig, "Archer", order=("Archer", "Visitor"))
    restarted = Leagues(rig.core)
    restarted.drain()
    with rig.store.connection() as c:
        row = c.execute(
            """SELECT cursor_sequence,evidence_generation
               FROM league_achievement_reconciliation
               WHERE account_id=? AND season_id=? AND ruleset='brandubh'""",
            (acct, season.identifier),
        ).fetchone()
    assert row["cursor_sequence"] == 10_000 + RECONCILIATION_ROWS - 1
    assert row["evidence_generation"] == 1

    for _ in range(5):
        Leagues(rig.core).drain()
        with rig.store.connection() as c:
            remaining = c.execute(
                """SELECT COUNT(*) FROM league_achievement_reconciliation
                   WHERE account_id=? AND season_id=? AND ruleset='brandubh'""",
                (acct, season.identifier),
            ).fetchone()[0]
        if remaining == 0:
            break
    assert remaining == 0
    assert peak(rig, acct, "brandubh") == 2
    assert awarded(rig, acct, "brandubh") == league_ids("brandubh", 2)
    # Chunk completion is idempotent and cannot duplicate the awards.
    assert Leagues(rig.core).drain()["achievements"] == 0
    assert awarded(rig, acct, "brandubh") == league_ids("brandubh", 2)


def test_reconciliation_query_count_is_bounded_by_pages_not_games(rig, monkeypatch):
    season = policy.season_for(datetime(2031, 1, 15, tzinfo=timezone.utc))
    count = 600
    seed_synthetic_league_history(rig, count, ["win"] * count, season)
    acct = rig.accounts["Archer"]["id"]
    with rig.core._write() as c:
        c.execute(
            """INSERT INTO league_achievement_reconciliation
               (account_id,season_id,ruleset,candidate_tier,due_at)
               VALUES(?,?, 'brandubh',5,0)""",
            (acct, season.identifier),
        )

    statements: list[str] = []
    original = rig.store.connection

    @contextmanager
    def traced(*args, **kwargs):
        with original(*args, **kwargs) as c:
            c.set_trace_callback(statements.append)
            yield c

    monkeypatch.setattr(rig.store, "connection", traced)
    service = Leagues(rig.core)
    for _ in range(5):
        service.drain()
        with rig.store.connection() as c:
            remaining = c.execute(
                """SELECT COUNT(*) FROM league_achievement_reconciliation
                   WHERE account_id=? AND season_id=? AND ruleset='brandubh'""",
                (acct, season.identifier),
            ).fetchone()[0]
        if not remaining:
            break
    assert remaining == 0
    chunk_queries = sum("WITH first_seat AS" in sql for sql in statements)
    assert chunk_queries == (count + RECONCILIATION_ROWS - 1) // RECONCILIATION_ROWS
    assert chunk_queries == 3
    # A per-game implementation would need at least 600 effective-result queries.
    assert len(statements) < 100
    assert peak(rig, acct, "brandubh") == 5


def test_sparse_league_evidence_uses_league_sequence_keyset(rig):
    season = policy.season_for(datetime(2032, 1, 15, tzinfo=timezone.utc))
    base = 200_000
    for offset, ruleset in enumerate(("brandubh", "tablut", "copenhagen")):
        seed_synthetic_league_history(
            rig,
            5_000,
            ["win"] * 5_000,
            season,
            ruleset=ruleset,
            base_sequence=100_000 + offset * 10_000,
            label=f"unrelated-{ruleset}",
            include_league=False,
        )
    seed_synthetic_league_history(
        rig,
        600,
        ["win"] * 600,
        season,
        base_sequence=base,
        label="sparse",
    )
    acct = rig.accounts["Archer"]["id"]

    with rig.store.connection() as c:
        early = authoritative_contribution_chunk(
            c, acct, season.identifier, "brandubh", 0, "", RECONCILIATION_ROWS
        )
        assert len(early) == RECONCILIATION_ROWS
        assert {row["game_id"].split("-")[0] for row in early} == {"sparse"}
        middle = authoritative_contribution_chunk(
            c,
            acct,
            season.identifier,
            "brandubh",
            base + 299,
            f"sparse-{base + 299}",
            RECONCILIATION_ROWS,
        )
        assert len(middle) == RECONCILIATION_ROWS
        late = authoritative_contribution_chunk(
            c,
            acct,
            season.identifier,
            "brandubh",
            base + 549,
            f"sparse-{base + 549}",
            RECONCILIATION_ROWS,
        )
        assert len(late) == 50
        assert late[-1]["game_id"] == f"sparse-{base + 599}"
        assert all(row["game_id"].startswith("sparse-") for row in (*early, *middle, *late))
        assert all(
            validated_account_contribution(row, acct) is not None
            for row in (*early, *middle, *late)
        )


def test_scale_migration_and_reconciliation_across_rulesets_and_seasons(rig):
    rulesets = ("brandubh", "tablut", "copenhagen")
    seasons = [
        policy.season_for(datetime(2030, month, 15, tzinfo=timezone.utc)) for month in (1, 2, 3)
    ]
    for season_index, season in enumerate(seasons):
        for ruleset_index, ruleset in enumerate(rulesets):
            seed_synthetic_league_history(
                rig,
                300,
                ["win"] * 300,
                season,
                ruleset=ruleset,
                base_sequence=10_000 + (season_index * 10 + ruleset_index) * 1_000,
                label=f"scale-{season_index}-{ruleset_index}",
            )

    with rig.store.connection() as c:
        c.execute("DROP TABLE league_achievement_reconciliation")
        c.execute("DROP TABLE league_achievement_progress")
        c.execute("PRAGMA user_version=23")
    started = time.perf_counter()
    reopened = Store(rig.store.path)
    migrated = time.perf_counter() - started
    with reopened.connection() as c:
        assert (
            c.execute("SELECT COUNT(*) FROM league_achievement_reconciliation").fetchone()[0] == 18
        )

    service = Leagues(rig.core)
    started = time.perf_counter()
    for _ in range(10):
        service.drain(limit=20)
        with rig.store.connection() as c:
            remaining = c.execute(
                "SELECT COUNT(*) FROM league_achievement_reconciliation"
            ).fetchone()[0]
        if not remaining:
            break
    reconciled = time.perf_counter() - started
    assert remaining == 0
    for ruleset in rulesets:
        assert peak(rig, rig.accounts["Archer"]["id"], ruleset) == 5
        assert peak(rig, rig.accounts["Keeper"]["id"], ruleset) == 1
        assert len(awarded(rig, rig.accounts["Archer"]["id"], ruleset)) == 5
        assert len(awarded(rig, rig.accounts["Keeper"]["id"], ruleset)) == 1
    # Deliberately generous local regression bounds for 2,700 synthetic results
    # across three seasons and all rulesets.
    assert migrated < 30
    assert reconciled < 30


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
