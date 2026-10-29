"""Clock modes, increments and default timeout ratings against real SQLite."""

from datetime import timedelta
from uuid import uuid4

import pytest

from hnefatafl.auth import Auth, SecuritySettings
from hnefatafl.online_core import OnlineCore, OnlineError
from hnefatafl.online_settings import OnlineSettings
from hnefatafl.storage import Store
from test_online_core import (
    actor,
    move_for,
    account_template as account_template,
    rig as rig,
)


def start(rig, control="total", increment=5, *, rule="brandubh", mode="single"):
    game = rig.core.create(
        rig.credentials["Archer"],
        rule,
        "attackers",
        60,
        uuid4().hex,
        mode,
        time_control=control,
        increment_seconds=increment,
    )["game"]
    assert game["deadline"] is None and game["remaining_seconds"] is None
    return rig.core.mutate(rig.credentials["Keeper"], game["id"], 0, "join")["game"]


def move(rig, game):
    chosen = move_for(game)
    return rig.core.mutate(
        actor(rig, game),
        game["id"],
        game["version"],
        "move",
        source=chosen.source,
        target=chosen.target,
    )["game"]


@pytest.mark.parametrize("rule", ["brandubh", "tablut", "copenhagen"])
@pytest.mark.parametrize("control", ["per_move", "total"])
def test_default_timeout_loses_for_current_player_and_rates_exactly_once(
    rig, monkeypatch, rule, control
):
    monkeypatch.delenv("HNEFATAFL_ONLINE_TIMEOUT_POLICY_APPROVED", raising=False)
    rig.core.settings = OnlineSettings.environment()
    assert rig.core.settings.timeout_policy_approved
    rig.core.settings = OnlineSettings(enabled=True)
    game = start(rig, control, 0, rule=rule)
    loser = game["participants"][game["position"]["turn"]]["account_id"]
    winner = "attackers" if game["position"]["turn"] == "defenders" else "defenders"
    rig.clock.value += timedelta(seconds=60)
    assert rig.core.sweep()["finalized"] == 1  # No administrator resume required.
    result = rig.core.read(rig.credentials["Archer"], game["id"])
    assert result["status"] == "completed" and result["deadline"] is None
    assert result["result"]["winner"] == winner
    assert result["result"]["termination_reason"] == "timeout"
    assert result["result"]["abandoned_account_id"] == loser
    assert result["result"]["completed_at"] == game["deadline"]
    assert result["result"]["rated_eligible"]
    assert sorted(row["delta"] for row in result["result"]["rating"]) == [-16, 16]
    assert rig.core.sweep()["finalized"] == 0
    with rig.store.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM online_results").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM rating_events").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM rating_applications").fetchone()[0] == 2


@pytest.mark.parametrize("rule", ["brandubh", "tablut"])
def test_total_clocks_only_charge_moving_player_and_keep_increments_on_restart(rig, rule):
    game = start(rig, rule=rule)
    first = game["position"]["turn"]
    second = "defenders" if first == "attackers" else "attackers"
    assert game["remaining_seconds"] == {first: 60, second: 60}
    rig.clock.value += timedelta(seconds=10, microseconds=500001)
    previous = game
    game = move(rig, game)
    assert game["remaining_seconds"] == {first: 54.499999, second: 60}
    assert game["deadline"] == (rig.clock.value + timedelta(seconds=60)).isoformat().replace(
        "+00:00", "Z"
    )
    # A duplicate/stale action must not grant another increment.
    with pytest.raises(OnlineError, match="changed"):
        move(rig, previous)
    rig.clock.value += timedelta(seconds=20, microseconds=250000)
    game = move(rig, game)
    assert game["remaining_seconds"] == {first: 54.499999, second: 44.75}
    assert game["deadline"] == (
        rig.clock.value + timedelta(seconds=54, microseconds=499999)
    ).isoformat().replace("+00:00", "Z")
    reopened = OnlineCore(
        Auth(Store(rig.store.path), SecuritySettings()), OnlineSettings(), rig.clock
    )
    assert reopened.read(rig.credentials["Archer"], game["id"]) == game
    rig.clock.value += timedelta(seconds=54, microseconds=499999)
    assert reopened.sweep()["finalized"] == 1
    finished = reopened.read(rig.credentials["Archer"], game["id"])
    assert finished["remaining_seconds"] == {first: 0, second: 44.75}
    assert finished["result"]["winner"] == second
    assert finished["position"]["ply"] == 2


def test_per_move_increment_resets_without_carrying_unused_time(rig):
    game = start(rig, "per_move", 3)
    assert game["deadline"] == (rig.clock.value + timedelta(seconds=60)).isoformat().replace(
        "+00:00", "Z"
    )
    for elapsed in (10, 20, 30):
        rig.clock.value += timedelta(seconds=elapsed)
        game = move(rig, game)
        assert game["remaining_seconds"] is None
        assert game["deadline"] == (rig.clock.value + timedelta(seconds=63)).isoformat().replace(
            "+00:00", "Z"
        )


@pytest.mark.parametrize("mode", ["simultaneous", "sequential"])
def test_return_leg_inherits_configuration_and_starts_with_fresh_total_clocks(rig, mode):
    first = start(rig, increment=7, mode=mode)
    rig.clock.value += timedelta(seconds=10)
    first = move(rig, first)
    if mode == "sequential":
        first = rig.core.mutate(actor(rig, first), first["id"], first["version"], "concede")["game"]
    second = rig.core.read(rig.credentials["Keeper"], first["match"]["games"][1]["id"])
    assert second["time_control"] == "total" and second["increment_seconds"] == 7
    assert second["remaining_seconds"] == {"attackers": 60, "defenders": 60}
    assert second["position"]["ply"] == 0
    assert first["remaining_seconds"]["attackers"] == 57
    assert first["participants"]["attackers"] == second["participants"]["defenders"]


@pytest.mark.parametrize("increment", [True, False, 1.0, "1", -1, 1209601, None])
def test_service_increment_is_strict(rig, increment):
    with pytest.raises(OnlineError):
        start(rig, increment=increment)
