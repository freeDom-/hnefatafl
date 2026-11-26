"""A disposable restore retains official League authority and unfinished work."""

import sqlite3
from uuid import uuid4

from hnefatafl.auth import Auth, SecuritySettings
from hnefatafl.leagues import Leagues
from hnefatafl.online_core import OnlineCore
from hnefatafl.online_migrations import retain_online_on_restore
from hnefatafl.storage import Store
from test_leagues import finish_league_game, join
from test_online_core import POLICY, account_template as account_template, rig as rig


def test_restored_leagues_apply_pending_result_and_void_without_reopening_pairing(rig, tmp_path):
    candidate_path = tmp_path / "restore-candidate" / "db.sqlite3"
    candidate_path.parent.mkdir()
    with sqlite3.connect(rig.store.path) as live, sqlite3.connect(candidate_path) as candidate:
        live.backup(candidate)

    service = Leagues(rig.core)
    first = finish_league_game(service, rig)
    assert service.drain()["settled"] == 1
    second = finish_league_game(service, rig)
    visitor = join(service, rig, "Visitor", "tablut")
    archer = rig.credentials["Archer"]
    generation = service.queue_status(archer)["join_generation"]
    withdrawn = str(uuid4())
    assert service.discard(archer, "brandubh", withdrawn, generation)["status"] == "cancelled"
    rig.core.mutate(
        rig.credentials["Odin"],
        first["id"],
        first["version"],
        "void",
        note="Disposable retained-authority recovery fixture",
    )
    with rig.core._write() as c:
        season_id = c.execute(
            "SELECT season_id FROM league_games WHERE game_id=?", (second["id"],)
        ).fetchone()[0]
        c.execute(
            """INSERT OR REPLACE INTO league_achievement_reconciliation
               (account_id,season_id,ruleset,candidate_tier,due_at,attempts,last_error)
               VALUES(?,?,'brandubh',2,0,0,NULL)""",
            (rig.accounts["Archer"]["id"], season_id),
        )

    candidate_store = Store(candidate_path)
    with rig.store.connection() as live, candidate_store.connection() as candidate:
        candidate.execute("PRAGMA foreign_keys=ON")
        candidate.execute("BEGIN IMMEDIATE")
        candidate.execute("PRAGMA defer_foreign_keys=ON")
        retain_online_on_restore(live, candidate)

    restored = OnlineCore(Auth(candidate_store, SecuritySettings()), POLICY, rig.clock)
    leagues = Leagues(restored)
    outcome = leagues.join(
        archer, "brandubh", withdrawn, expected_generation=generation, guarded=True
    )
    assert outcome["status"] == "cancelled" and outcome["join_generation"] == withdrawn
    with candidate_store.connection() as c:
        assert c.execute("SELECT enabled FROM online_timeout_gate").fetchone()[0] == 0
        assert (
            c.execute(
                "SELECT candidate_tier FROM league_achievement_reconciliation WHERE account_id=?",
                (rig.accounts["Archer"]["id"],),
            ).fetchone()[0]
            == 2
        )
        assert (
            c.execute("SELECT request_id FROM league_queue").fetchone()[0] == visitor["request_id"]
        )
        assert {row[0] for row in c.execute("SELECT game_id FROM league_settlement_queue")} == {
            first["id"],
            second["id"],
        }

    assert leagues.drain()["settled"] == 2
    assert leagues.drain()["settled"] == 0
    assert restored.read(rig.credentials["Archer"], first["id"])["status"] == "voided"
    assert restored.read(rig.credentials["Keeper"], second["id"])["status"] == "completed"
    with candidate_store.connection() as c:
        assert dict(c.execute("SELECT game_id,state FROM league_games")) == {
            first["id"]: "withdrawn",
            second["id"]: "eligible",
        }
        assert dict(c.execute("SELECT account_id,points FROM league_standings")) == {
            rig.accounts["Archer"]["id"]: 0,
            rig.accounts["Keeper"]["id"]: 3,
        }
        assert c.execute("SELECT count(*) FROM league_active_players").fetchone()[0] == 0
        assert c.execute("PRAGMA foreign_key_check").fetchall() == []
