"""Phase 6 rating policy, durable application, rebuild and leaderboard contracts."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import sqlite3
from types import SimpleNamespace
from uuid import uuid4

import pytest

from hnefatafl import lifecycle, rating
from hnefatafl.auth import Auth, SecuritySettings, digest
from hnefatafl.engine import RULESETS, State, legal_moves
from hnefatafl.online_core import (
    OnlineCore,
    OnlineError,
    micros,
    result_payload,
    stored_result,
)
from hnefatafl.online_settings import OnlineSettings
from hnefatafl.storage import Store

POLICY = OnlineSettings(
    enabled=True,
    creation_policy_approved=True,
    retain_seat_without_verification=True,
    timeout_policy_approved=True,
)


class Clock:
    def __init__(self):
        self.value = datetime(2028, 3, 4, 5, 6, 7, 654321, tzinfo=timezone.utc)

    def __call__(self):
        return self.value


@pytest.fixture(scope="module")
def account_template(tmp_path_factory):
    store = Store(tmp_path_factory.mktemp("rating-accounts") / "db.sqlite3")
    auth = Auth(store, SecuritySettings())
    accounts, credentials = {}, {}
    for name, role in (("Archer", "ADMIN"), ("Keeper", "USER"), ("Visitor", "USER")):
        accounts[name] = auth.create_account(
            name, name.lower() + "@example.test", "rating-fixture-password", role
        )
        _, token, _ = auth.login(name, "rating-fixture-password", "fixture-" + name)
        credentials[name] = digest(token)
    with store.connection() as c:
        c.execute("UPDATE accounts SET email_verified_at=1")
    return store.path, accounts, credentials


@pytest.fixture
def rig(tmp_path, account_template):
    template, accounts, credentials = account_template
    path = tmp_path / "hnefatafl.sqlite3"
    with sqlite3.connect(template) as source, sqlite3.connect(path) as target:
        source.backup(target)
    auth = Auth(Store(path), SecuritySettings())
    clock = Clock()
    return SimpleNamespace(
        core=OnlineCore(auth, POLICY, clock),
        auth=auth,
        store=auth.store,
        clock=clock,
        accounts=accounts,
        credentials=credentials,
    )


def credential_for(rig, game, side):
    account_id = game["participants"][side]["account_id"]
    return next(c for name, c in rig.credentials.items() if rig.accounts[name]["id"] == account_id)


def waiting(rig, ruleset="brandubh", rated=False):
    return rig.core.create(
        rig.credentials["Archer"], ruleset, "attackers", 60, uuid4().hex, rated=rated
    )["game"]


def active(rig, ruleset="brandubh", rated=False):
    game = waiting(rig, ruleset, rated)
    return rig.core.mutate(rig.credentials["Keeper"], game["id"], 0, "join")["game"]


def finish(rig, game, winner):
    """Reach a terminal result with the requested winner via legal play."""
    loser = "defenders" if winner == "attackers" else "attackers"
    while True:
        turn = game["position"]["turn"]
        credential = credential_for(rig, game, turn)
        if turn == loser:
            return rig.core.mutate(credential, game["id"], game["version"], "concede")["game"]
        move = legal_moves(State.from_dict(game["position"]))[0]
        game = rig.core.mutate(
            credential, game["id"], game["version"], "move", source=move.source, target=move.target
        )["game"]


def fabricate_draw(rig, game):
    """Apply an authoritative repetition-draw result through the durable layer."""
    with rig.core._write() as c:
        row = c.execute("SELECT * FROM online_games WHERE id=?", (game["id"],)).fetchone()
        now = rig.clock.value
        result = lifecycle.Result(
            game_id=game["id"],
            revision=1,
            supersedes=None,
            category="online",
            ruleset=row["ruleset"],
            ruleset_version=row["ruleset_version"],
            attackers=row["attackers_id"],
            defenders=row["defenders_id"],
            started_at=now,
            completed_at=now,
            revised_at=now,
            winner="draw",
            termination_reason="repetition",
            move_count=10,
            competitive_eligible=True,
            abandoned_account_id=None,
        )
        c.execute(
            "INSERT INTO online_results VALUES(?,?,?,?,?,?,?,?)",
            (
                game["id"],
                1,
                None,
                1,
                None,
                micros(now),
                micros(now),
                json.dumps(result_payload(result)),
            ),
        )
        c.execute(
            "UPDATE online_games SET status='completed',deadline=NULL,result_revision=1,completed_at=?,version=version+1 WHERE id=?",
            (micros(now), game["id"]),
        )
        rig.core.ratings.apply(c, row["match_id"], game["id"], result, now)


def pool_state(rig, account_id, pool="brandubh"):
    with rig.store.connection() as c:
        return c.execute(
            "SELECT rating,rated_games,wins,losses,draws FROM player_ratings WHERE account_id=? AND pool=?",
            (account_id, pool),
        ).fetchone()


# --- Pure policy ---------------------------------------------------------------


def test_known_elo_values_and_precision():
    assert rating.expected_score(1500.0, 1500.0) == 0.5
    assert rating.expected_score(1600.0, 1500.0) == pytest.approx(0.6400649998028851)
    assert rating.updated_rating(1500.0, 1500.0, 1.0) == 1516.0
    assert rating.updated_rating(1500.0, 1500.0, 0.0) == 1484.0
    assert rating.updated_rating(1500.0, 1500.0, 0.5) == 1500.0
    # Internal precision is not rounded to an integer for presentation.
    assert rating.updated_rating(1500.0, 1600.0, 1.0) == pytest.approx(
        1500.0 + 32.0 * (1.0 - 0.3599350001971149)
    )


@pytest.mark.parametrize(
    "winner,side,expected",
    [
        ("attackers", "attackers", 1.0),
        ("attackers", "defenders", 0.0),
        ("defenders", "defenders", 1.0),
        ("draw", "attackers", 0.5),
        ("draw", "defenders", 0.5),
    ],
)
def test_score_mapping(winner, side, expected):
    assert rating.score_for(winner, side) == expected


def test_replay_uses_pre_game_ratings_for_both_players_and_is_order_independent():
    events = [
        rating.RatedEvent(2, "b", "p1", "p2", "defenders"),
        rating.RatedEvent(1, "a", "p1", "p2", "attackers"),
    ]
    states, applications = rating.replay(events)
    # Game 1: p1 beats p2 from equal ratings.
    first = applications["a"]
    assert first["p1"].rating_before == 1500.0 and first["p1"].rating_after == 1516.0
    assert first["p2"].rating_before == 1500.0 and first["p2"].rating_after == 1484.0
    # Game 2 uses the post-game-1 ratings, both from the same pre-game snapshot.
    second = applications["b"]
    assert second["p1"].rating_before == 1516.0 and second["p2"].rating_before == 1484.0
    assert second["p2"].score == 1.0 and second["p2"].rating_after > second["p2"].rating_before
    assert states["p1"][1] == 2 and states["p2"][1] == 2
    # Shuffling the input list cannot change the persisted-sequence result.
    assert rating.replay(list(reversed(events)))[0] == states


def test_replay_provisional_threshold():
    events = [rating.RatedEvent(i, f"g{i}", "p1", "p2", "draw") for i in range(1, 11)]
    states, _ = rating.replay(events)
    assert states["p1"][1] == 10
    assert states["p1"][4] == 10  # draws
    # The tenth game leaves a player non-provisional.
    assert (
        rating.RatingApplication("g", "p", "o", 1500, 1500, 1500, 0.5, 0.5, 10).provisional is False
    )
    assert (
        rating.RatingApplication("g", "p", "o", 1500, 1500, 1500, 0.5, 0.5, 9).provisional is True
    )


# --- Durable application -------------------------------------------------------


def test_rated_game_applies_both_sides_casual_does_not(rig):
    rated = active(rig, rated=True)
    assert rated["rated"] is True
    rated = finish(rig, rated, "attackers")
    assert rated["result"]["rated_eligible"] is True and rated["result"]["rated_reason"] is None
    assert {row["account_id"] for row in rated["result"]["rating"]} == {
        rig.accounts["Archer"]["id"],
        rig.accounts["Keeper"]["id"],
    }
    archer = pool_state(rig, rig.accounts["Archer"]["id"])
    keeper = pool_state(rig, rig.accounts["Keeper"]["id"])
    assert archer["rating"] == 1516.0 and archer["wins"] == 1
    assert keeper["rating"] == 1484.0 and keeper["losses"] == 1
    assert archer["rated_games"] == keeper["rated_games"] == 1

    casual = active(rig, rated=False)
    assert casual["rated"] is False
    casual = finish(rig, casual, "attackers")
    assert casual["result"]["rated_eligible"] is False
    assert casual["result"]["rated_reason"] == "casual"
    assert casual["result"]["rating"] is None
    assert pool_state(rig, rig.accounts["Archer"]["id"])["rated_games"] == 1


def test_pools_are_independent_per_ruleset(rig):
    for ruleset in ("brandubh", "tablut", "copenhagen"):
        game = finish(rig, active(rig, ruleset, rated=True), "attackers")
        assert game["ruleset"] == ruleset
    for ruleset in ("brandubh", "tablut", "copenhagen"):
        assert pool_state(rig, rig.accounts["Archer"]["id"], ruleset)["rated_games"] == 1
    with rig.store.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM rating_events").fetchone()[0] == 3


def test_draws_and_finalized_timeouts_count(rig):
    draw = active(rig, rated=True)
    fabricate_draw(rig, draw)
    archer = pool_state(rig, rig.accounts["Archer"]["id"])
    keeper = pool_state(rig, rig.accounts["Keeper"]["id"])
    assert (archer["rating"], archer["draws"]) == (1500.0, 1)
    assert (keeper["rating"], keeper["draws"]) == (1500.0, 1)

    timeout = active(rig, rated=True)
    gate = rig.core.gate(rig.credentials["Archer"])
    rig.core.set_gate(rig.credentials["Archer"], gate["version"], True, "Deterministic test policy")
    rig.clock.value += timedelta(seconds=61)
    assert rig.core.sweep()["finalized"] == 1
    timed_out = rig.core.read(rig.credentials["Archer"], timeout["id"])
    assert timed_out["result"]["termination_reason"] == "timeout"
    assert timed_out["result"]["rated_eligible"] is True
    loser = timed_out["result"]["abandoned_account_id"]
    assert pool_state(rig, loser)["losses"] == 1


def test_unverified_rated_game_is_recorded_but_not_rated(rig):
    game = active(rig, rated=True)
    with rig.store.connection() as c:
        c.execute(
            "UPDATE accounts SET email_verified_at=NULL WHERE id=?",
            (rig.accounts["Keeper"]["id"],),
        )
    game = finish(rig, game, "attackers")
    assert game["result"]["rated_eligible"] is False
    assert game["result"]["rated_reason"] == "ineligible"
    assert pool_state(rig, rig.accounts["Archer"]["id"]) is None
    with rig.store.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM rating_events").fetchone()[0] == 0


def test_void_withdraws_and_rebuilds_later_games_deterministically(rig):
    first = finish(rig, active(rig, rated=True), "attackers")
    second = finish(rig, active(rig, rated=True), "defenders")
    assert pool_state(rig, rig.accounts["Archer"]["id"])["rated_games"] == 2

    voided = rig.core.mutate(
        rig.credentials["Archer"], first["id"], first["version"], "void", note="Incident"
    )["game"]
    assert voided["status"] == "voided"
    assert voided["result"]["rated_eligible"] is False
    assert voided["result"]["rated_reason"] == "voided"
    archer = pool_state(rig, rig.accounts["Archer"]["id"])
    # Only the second game remains, so it was recomputed from 1500.
    assert archer["rated_games"] == 1
    assert archer["rating"] == pytest.approx(1484.0)
    with rig.store.connection() as c:
        rows = c.execute(
            "SELECT game_id,sequence,state FROM rating_events ORDER BY sequence"
        ).fetchall()
        assert [(r["game_id"], r["sequence"], r["state"]) for r in rows] == [
            (first["id"], 1, "withdrawn"),
            (second["id"], 2, "applied"),
        ]
        assert c.execute("SELECT COUNT(*) FROM rating_applications").fetchone()[0] == 2


def test_two_game_match_legs_rate_independently_and_repeat_opponents_count(rig):
    match = rig.core.create(
        rig.credentials["Archer"],
        "brandubh",
        "attackers",
        60,
        uuid4().hex,
        mode="simultaneous",
        rated=True,
    )["game"]
    match = rig.core.mutate(rig.credentials["Keeper"], match["id"], 0, "join")["game"]
    legs = [match, rig.core.read(rig.credentials["Archer"], match["match"]["games"][1]["id"])]
    for leg in legs:
        finish(rig, leg, "attackers")
    archer = pool_state(rig, rig.accounts["Archer"]["id"])
    keeper = pool_state(rig, rig.accounts["Keeper"]["id"])
    # The return leg swaps sides, so each player wins exactly one leg.
    assert archer["rated_games"] == 2 and archer["wins"] == 1 and archer["losses"] == 1
    assert keeper["rated_games"] == 2 and keeper["wins"] == 1 and keeper["losses"] == 1
    with rig.store.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM rating_events").fetchone()[0] == 2


def test_duplicate_application_is_idempotent(rig):
    game = finish(rig, active(rig, rated=True), "attackers")
    with rig.store.connection() as c:
        row = c.execute("SELECT * FROM online_games WHERE id=?", (game["id"],)).fetchone()
        saved = stored_result(
            c.execute(
                "SELECT payload FROM online_results WHERE game_id=? AND revision=?",
                (game["id"], row["result_revision"]),
            ).fetchone()[0]
        )
    before = pool_state(rig, rig.accounts["Archer"]["id"])
    with rig.core._write() as c:
        rig.core.ratings.apply(c, row["match_id"], game["id"], saved, rig.clock.value)
    assert pool_state(rig, rig.accounts["Archer"]["id"]) == before
    with rig.store.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM rating_events").fetchone()[0] == 1
        assert c.execute("SELECT COUNT(*) FROM rating_applications").fetchone()[0] == 2


def test_rating_write_failure_rolls_back_the_result(rig):
    game = active(rig, rated=True)
    with rig.store.connection() as c:
        c.execute(
            "CREATE TRIGGER injected BEFORE INSERT ON rating_applications BEGIN SELECT RAISE(ABORT,'injected'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        finish(rig, game, "attackers")
    current = rig.core.read(rig.credentials["Archer"], game["id"])
    assert current["status"] == "active" and current["result"] is None
    with rig.store.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM online_results").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM rating_events").fetchone()[0] == 0


def test_concurrent_completion_serializes_and_applies_each_once(rig):
    games = [active(rig, rated=True), active(rig, rated=True)]
    barrier = ThreadPoolExecutor(2)

    def complete(game):
        independent = OnlineCore(rig.auth, POLICY, rig.clock)
        return finish(
            SimpleNamespace(
                core=independent,
                auth=rig.auth,
                store=rig.store,
                clock=rig.clock,
                accounts=rig.accounts,
                credentials=rig.credentials,
            ),
            game,
            "attackers",
        )

    with barrier as pool:
        results = list(pool.map(complete, games))
    assert all(result["result"]["rated_eligible"] for result in results)
    archer = pool_state(rig, rig.accounts["Archer"]["id"])
    assert archer["rated_games"] == 2
    with rig.store.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM rating_events").fetchone()[0] == 2
        assert c.execute("SELECT COUNT(*) FROM rating_applications").fetchone()[0] == 4


def test_rated_flag_is_immutable_and_client_cannot_forge(rig):
    game = active(rig, rated=False)
    with rig.store.connection() as c:
        with pytest.raises(sqlite3.IntegrityError):
            c.execute("UPDATE online_matches SET rated=1 WHERE id=?", (game["match"]["id"],))
    assert game["rated"] is False
    with pytest.raises(OnlineError):
        rig.core.create(
            rig.credentials["Archer"], "brandubh", "attackers", 60, uuid4().hex, rated=1
        )


def test_admin_rebuild_is_deterministic_and_permission_checked(rig):
    game = finish(rig, active(rig, rated=True), "attackers")
    before = pool_state(rig, rig.accounts["Archer"]["id"])
    assert rig.core.rebuild_ratings(rig.credentials["Archer"], "brandubh")["policy_version"] == 1
    assert pool_state(rig, rig.accounts["Archer"]["id"]) == before
    with pytest.raises(OnlineError) as error:
        rig.core.rebuild_ratings(rig.credentials["Keeper"])
    assert error.value.status == 403
    assert game["id"]


# --- Leaderboard ---------------------------------------------------------------


def test_leaderboard_unifies_provisional_and_qualified_and_reports_standing(rig):
    for _ in range(10):
        finish(rig, active(rig, rated=True), "attackers")
    board = rig.core.leaderboard(rig.credentials["Archer"], "brandubh", limit=25, offset=0)
    assert board["policy"]["provisional_games"] == 10
    assert [entry["name"] for entry in board["entries"]] == ["Archer", "Keeper"]
    assert board["total"] == 2
    assert board["entries"][0]["rank"] == 1
    assert all(entry["provisional"] is False for entry in board["entries"])
    assert board["me"]["provisional"] is False and board["me"]["rank"] == 1
    assert board["me"]["rated_games"] == 10


def test_leaderboard_includes_provisional_players_without_separate_section(rig):
    finish(rig, active(rig, rated=True), "attackers")
    board = rig.core.leaderboard(rig.credentials["Archer"], "brandubh")
    assert {entry["name"] for entry in board["entries"]} == {"Archer", "Keeper"}
    assert all(entry["provisional"] is True for entry in board["entries"])
    assert board["me"]["provisional"] is True
    # A different ruleset pool has no standing yet.
    empty = rig.core.leaderboard(rig.credentials["Archer"], "tablut")
    assert empty["me"] is None
    assert empty["entries"] == [] and empty["total"] == 0


def test_leaderboard_pagination_and_deterministic_ties(rig):
    # Ten draws leave every participant tied at the initial rating.
    for _ in range(10):
        fabricate_draw(rig, active(rig, rated=True))
    board = rig.core.leaderboard(rig.credentials["Archer"], "brandubh", limit=1, offset=0)
    assert board["total"] == 2
    assert len(board["entries"]) == 1
    second = rig.core.leaderboard(rig.credentials["Archer"], "brandubh", limit=1, offset=1)
    assert second["entries"][0]["rank"] == 2
    ordered = [board["entries"][0], second["entries"][0]]
    assert [entry["account_id"] for entry in ordered] == sorted(
        entry["account_id"] for entry in ordered
    )


def _seed_rating(rig, name, rating, games):
    with rig.store.connection() as c:
        c.execute(
            "INSERT OR REPLACE INTO player_ratings VALUES(?,?,?,?,?,?,?,?,?)",
            (rig.accounts[name]["id"], "brandubh", rating, games, 1, 1, 1, 1, 1),
        )


def test_leaderboard_equal_elo_orders_by_games_then_account_id(rig):
    _seed_rating(rig, "Keeper", 1500.0, 5)
    _seed_rating(rig, "Archer", 1500.0, 9)
    _seed_rating(rig, "Visitor", 1500.0, 9)
    board = rig.core.leaderboard(rig.credentials["Archer"], "brandubh", limit=25, offset=0)
    assert [entry["rated_games"] for entry in board["entries"]] == [9, 9, 5]
    # Equal rating and games fall back to the immutable account id.
    assert [entry["account_id"] for entry in board["entries"][:2]] == sorted(
        entry["account_id"] for entry in board["entries"][:2]
    )


def test_leaderboard_ranks_by_rounded_elo(rig):
    _seed_rating(rig, "Archer", 1500.4, 9)
    _seed_rating(rig, "Keeper", 1500.6, 5)
    board = rig.core.leaderboard(rig.credentials["Archer"], "brandubh")
    assert [entry["name"] for entry in board["entries"]] == ["Keeper", "Archer"]
    assert [round(entry["rating"]) for entry in board["entries"]] == [1501, 1500]


def test_leaderboard_rejects_invalid_pagination_and_ruleset(rig):
    for pool in ("unknown", ""):
        with pytest.raises(OnlineError):
            rig.core.leaderboard(rig.credentials["Archer"], pool)
    for limit, offset in ((0, 0), (101, 0), (25, -1)):
        with pytest.raises(OnlineError):
            rig.core.leaderboard(rig.credentials["Archer"], "brandubh", limit, offset)


def test_rating_rebuild_requires_versioned_policy(rig):
    assert rating.RATING_POLICY_VERSION >= 1
    assert rating.INITIAL_RATING == 1500.0 and rating.K_FACTOR == 32.0
    assert set(RULESETS) >= {"brandubh", "tablut", "copenhagen"}


# --- API surface ---------------------------------------------------------------


def api_client(tmp_path):
    from fastapi.testclient import TestClient

    from hnefatafl.server import create_app

    app = create_app(
        tmp_path,
        online_settings=OnlineSettings(
            enabled=True,
            creation_policy_approved=True,
            retain_seat_without_verification=True,
            timeout_policy_approved=True,
        ),
        online_background=False,
    )
    return app, TestClient(app)


def verified_login(app, client, name="Raven", role="USER"):
    account = app.state.auth.create_account(
        name, name.lower() + "@example.test", "api-rating-password", role
    )
    with app.state.auth.store.connection() as c:
        c.execute("UPDATE accounts SET email_verified_at=1 WHERE id=?", (account["id"],))
    response = client.post(
        "/api/auth/login",
        json={"identity": name, "password": "api-rating-password"},
        headers={"Origin": "http://testserver"},
    )
    assert response.status_code == 200, response.text
    client.headers.update(
        {"Origin": "http://testserver", "X-CSRF-Token": response.json()["csrf_token"]}
    )
    return account


def test_leaderboard_api_requires_authentication_and_returns_policy(tmp_path):
    app, client = api_client(tmp_path)
    with client:
        assert client.get("/api/leaderboard").status_code == 401
        verified_login(app, client)
        board = client.get("/api/leaderboard?ruleset=tablut").json()
        assert board["ruleset"] == "tablut"
        assert board["policy"]["provisional_games"] == 10
        assert board["entries"] == [] and board["total"] == 0
        assert board["me"] is None
        assert client.get("/api/leaderboard?ruleset=unknown").status_code == 422
        assert client.get("/api/leaderboard?limit=0").status_code == 422


def test_rated_creation_is_server_validated_and_immutable(tmp_path):
    app, client = api_client(tmp_path)
    with client:
        verified_login(app, client)
        request = {
            "request_id": str(uuid4()),
            "ruleset": "brandubh",
            "side_selection": "attackers",
            "time_per_move": 60,
            "rated": True,
        }
        created = client.post("/api/online/games", json=request)
        assert created.status_code == 201, created.text
        game = created.json()["game"]
        assert game["rated"] is True
        # A string is not a boolean and must be rejected before persistence.
        forged = client.post(
            "/api/online/games", json={**request, "request_id": str(uuid4()), "rated": "true"}
        )
        assert forged.status_code == 422
        # An extra client-supplied rating field is forbidden.
        extra = client.post(
            "/api/online/games",
            json={**request, "request_id": str(uuid4()), "rating": 9999},
        )
        assert extra.status_code == 422


def test_rating_rebuild_endpoint_requires_administrator(tmp_path):
    app, client = api_client(tmp_path)
    with client:
        verified_login(app, client, "Raven", "USER")
        assert client.post("/api/online/ratings/rebuild", json={}).status_code == 403
        verified_login(app, client, "Odin", "ADMIN")
        response = client.post("/api/online/ratings/rebuild", json={"ruleset": "brandubh"})
        assert response.status_code == 200, response.text
        assert response.json()["rebuilt"] == ["brandubh"]


# --- Phase 6 UI refinement backend contracts -----------------------------------


def test_lobby_sections_exclude_terminal_and_filter_participation(rig):
    archer_waiting = waiting(rig)
    archer_active = active(rig)  # Brandubh attackers (Archer) move first.
    keeper_waiting = rig.core.create(
        rig.credentials["Keeper"], "brandubh", "attackers", 60, uuid4().hex
    )["game"]
    finished = finish(rig, active(rig), "attackers")

    all_ids = {g["id"] for g in rig.core.listing(rig.credentials["Archer"], section="all")["games"]}
    assert all_ids == {archer_waiting["id"], archer_active["id"], keeper_waiting["id"]}
    assert finished["id"] not in all_ids

    mine = {g["id"] for g in rig.core.listing(rig.credentials["Archer"], section="mine")["games"]}
    assert mine == {archer_waiting["id"], archer_active["id"]}

    my_turn = [
        g["id"] for g in rig.core.listing(rig.credentials["Archer"], section="my_turn")["games"]
    ]
    assert my_turn == [archer_active["id"]]
    assert rig.core.listing(rig.credentials["Keeper"], section="my_turn")["games"] == []
    with pytest.raises(OnlineError):
        rig.core.listing(rig.credentials["Archer"], section="unknown")


def test_terminal_king_square_tracks_recorded_king_moves():
    assert OnlineCore._terminal_king_square("brandubh", []) == 24
    moves = [{"source": 24, "target": 23}]
    assert OnlineCore._terminal_king_square("brandubh", moves) == 23
    # A later non-king move from the king's old square must not move the marker.
    moves.append({"source": 24, "target": 17})
    assert OnlineCore._terminal_king_square("brandubh", moves) == 23


def test_projection_exposes_terminal_king_square_only_for_king_capture(rig):
    conceded = finish(rig, active(rig), "attackers")
    assert rig.core.read(rig.credentials["Archer"], conceded["id"])["terminal_king_square"] is None

    captured = active(rig)
    with rig.core._write() as c:
        row = c.execute("SELECT * FROM online_games WHERE id=?", (captured["id"],)).fetchone()
        now = rig.clock.value
        position = json.loads(row["position"])
        position["board"] = ["." if piece == "K" else piece for piece in position["board"]]
        position["winner"] = "attackers"
        position["reason"] = "king_captured"
        result = lifecycle.Result(
            game_id=captured["id"],
            revision=1,
            supersedes=None,
            category="online",
            ruleset=row["ruleset"],
            ruleset_version=row["ruleset_version"],
            attackers=row["attackers_id"],
            defenders=row["defenders_id"],
            started_at=now,
            completed_at=now,
            revised_at=now,
            winner="attackers",
            termination_reason="king_captured",
            move_count=0,
            competitive_eligible=True,
            abandoned_account_id=None,
        )
        c.execute(
            "INSERT INTO online_results VALUES(?,?,?,?,?,?,?,?)",
            (
                captured["id"],
                1,
                None,
                1,
                None,
                micros(now),
                micros(now),
                json.dumps(result_payload(result)),
            ),
        )
        c.execute(
            "UPDATE online_games SET status='completed',deadline=NULL,result_revision=1,completed_at=?,position=?,version=version+1 WHERE id=?",
            (micros(now), json.dumps(position), captured["id"]),
        )
    read = rig.core.read(rig.credentials["Archer"], captured["id"])
    assert read["terminal_king_square"] == 24
    assert "K" not in read["position"]["board"]


def test_online_statistics_group_by_ruleset_and_include_rating(rig):
    from hnefatafl.personal import PersonalHistory

    finish(rig, active(rig, "brandubh", rated=True), "attackers")
    finish(rig, active(rig, "brandubh", rated=True), "defenders")
    finish(rig, active(rig, "tablut", rated=False), "attackers")
    with rig.store.connection() as c:
        stats = PersonalHistory._online_statistics(c, rig.accounts["Archer"]["id"])
    groups = {group["ruleset"]: group for group in stats["groups"]}
    assert set(groups) == {"brandubh", "tablut"}
    assert all(group["mode"] is None for group in groups.values())
    assert groups["brandubh"]["played"] == 2
    assert groups["brandubh"]["rating"] is not None
    assert groups["tablut"]["played"] == 1
    assert groups["tablut"]["rating"] is None
