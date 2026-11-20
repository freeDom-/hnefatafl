"""Player tournaments: double elimination, authoritative results and authorization.

Tournament games are real authoritative online games, so these tests drive the
online core as a participant would and then settle the bracket from the recorded
results. No client-side bracket arithmetic is trusted.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import base64
import fcntl
import json
import shutil
import sqlite3
import threading
import time
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from hnefatafl.auth import COOKIE, Auth, SecuritySettings, digest
from hnefatafl.engine import State, apply_move, legal_moves
from hnefatafl.online_settings import OnlineSettings
from hnefatafl.player_tournaments import DISPLACED_GAME_NOTE, encode_cursor
from hnefatafl.server import create_app

POLICY = OnlineSettings(
    enabled=True,
    creation_policy_approved=True,
    retain_seat_without_verification=True,
    timeout_policy_approved=True,
)

PLAYERS = [f"P{index:02d}" for index in range(1, 16)]
ADMIN = "P16"
UNVERIFIED = "P00"


class Clock:
    def __init__(self):
        self.value = datetime(2027, 1, 2, 3, 4, 5, 123456, tzinfo=timezone.utc)

    def __call__(self):
        return self.value


@pytest.fixture(scope="module")
def account_template(tmp_path_factory):
    from hnefatafl.storage import Store

    store = Store(tmp_path_factory.mktemp("tournament-accounts") / "db.sqlite3")
    auth = Auth(store, SecuritySettings())
    accounts, tokens = {}, {}
    for name in [UNVERIFIED, *PLAYERS, ADMIN]:
        accounts[name] = auth.create_account(
            name,
            name.lower() + "@example.test",
            "tournament-fixture-password",
            "ADMIN" if name == ADMIN else "USER",
        )
        _, token, _ = auth.login(name, "tournament-fixture-password", "fixture-" + name)
        tokens[name] = token
    with store.connection() as c:
        c.execute("UPDATE accounts SET email_verified_at=1 WHERE username<>?", (UNVERIFIED,))
    return store.path, accounts, tokens


@pytest.fixture
def rig(tmp_path, account_template):
    template, accounts, tokens = account_template
    shutil.copy(template, tmp_path / "hnefatafl.sqlite3")
    clock = Clock()
    app = create_app(
        tmp_path,
        online_settings=POLICY,
        online_clock=clock,
        online_background=False,
        account_background=False,
    )
    credentials = {name: digest(token) for name, token in tokens.items()}
    return SimpleNamespace(
        app=app,
        store=app.state.store,
        auth=app.state.auth,
        online=app.state.online,
        core=app.state.player_tournaments,
        clock=clock,
        accounts=accounts,
        tokens=tokens,
        credentials=credentials,
        by_id={accounts[name]["id"]: credentials[name] for name in accounts},
        admin=credentials[ADMIN],
    )


@pytest.fixture
def client(rig):
    def build(name=ADMIN):
        result = TestClient(rig.app)
        result.cookies.set(COOKIE, rig.tokens[name])
        result.headers.update(
            {
                "Origin": "http://testserver",
                "X-CSRF-Token": digest("csrf:" + rig.tokens[name]),
            }
        )
        return result

    return build


def authenticated_client(rig, name=ADMIN):
    result = TestClient(rig.app)
    result.cookies.set(COOKIE, rig.tokens[name])
    result.headers.update(
        {
            "Origin": "http://testserver",
            "X-CSRF-Token": digest("csrf:" + rig.tokens[name]),
        }
    )
    return result


@pytest.fixture
def background_rig(tmp_path, account_template):
    template, accounts, tokens = account_template
    shutil.copy(template, tmp_path / "hnefatafl.sqlite3")
    clock = Clock()
    app = create_app(
        tmp_path,
        online_settings=POLICY,
        online_clock=clock,
        online_background=True,
        account_background=False,
    )
    credentials = {name: digest(token) for name, token in tokens.items()}
    return SimpleNamespace(
        app=app,
        store=app.state.store,
        auth=app.state.auth,
        online=app.state.online,
        core=app.state.player_tournaments,
        clock=clock,
        accounts=accounts,
        tokens=tokens,
        credentials=credentials,
        by_id={accounts[name]["id"]: credentials[name] for name in accounts},
        admin=credentials[ADMIN],
    )


def wait_until(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def create(rig, *, name="Cup", capacity=16, match_legs=1, ruleset="brandubh", deadline=None):
    """Provision a stored historical format; the public creation path cannot create it."""
    tournament = rig.core.create(
        rig.admin,
        name=name,
        ruleset=ruleset,
        capacity=capacity,
        match_mode="simultaneous",
        time_per_move=600,
        registration_deadline=deadline,
    )
    with rig.store.connection() as c:
        c.execute(
            "UPDATE player_tournaments SET match_mode='legacy',match_legs=? WHERE id=?",
            (match_legs, tournament["id"]),
        )
    return rig.core.detail(rig.admin, tournament["id"])


def tournament_with_entrants(rig, entrants, **kwargs):
    tournament = create(rig, **kwargs)
    for name in entrants:
        rig.core.register(rig.credentials[name], tournament["id"])
    return rig.core.detail(rig.admin, tournament["id"])


def safe_move(game):
    state = State.from_dict(game["position"])
    for move in legal_moves(state):
        if apply_move(state, move).winner is None:
            return move
    raise AssertionError("no non-terminal legal move")


def force_match(rig, match, winner_id):
    """Make one participant win every leg through authoritative online actions."""
    for leg in match["legs"]:
        game = rig.online.read(rig.admin, leg["game_id"])
        assert game["status"] == "active"
        turn = game["position"]["turn"]
        mover = game["participants"][turn]["account_id"]
        if mover == winner_id:
            move = safe_move(game)
            game = rig.online.mutate(
                rig.by_id[mover],
                game["id"],
                game["version"],
                "move",
                source=move.source,
                target=move.target,
            )["game"]
            turn = game["position"]["turn"]
            rig.online.mutate(
                rig.by_id[game["participants"][turn]["account_id"]],
                game["id"],
                game["version"],
                "concede",
            )
        else:
            rig.online.mutate(rig.by_id[mover], game["id"], game["version"], "concede")


def ready_matches(rig, tournament_id):
    bracket = rig.core.bracket(rig.admin, tournament_id)
    ready = [
        match
        for group in ("upper", "lower")
        for entry in bracket[group]
        for match in entry["matches"]
        if match["status"] == "ready"
    ]
    ready += [
        bracket[key]
        for key in ("final", "reset")
        if bracket[key] is not None and bracket[key]["status"] == "ready"
    ]
    return ready


def seeds_of(rig, tournament_id):
    return {
        entry["account_id"]: entry["seed"]
        for entry in rig.core.detail(rig.admin, tournament_id)["entrants"]
    }


def higher_seed(rig, tournament_id):
    seeds = seeds_of(rig, tournament_id)

    def choose(_match, a, b):
        return a if seeds[a] < seeds[b] else b

    return choose


def play_out(rig, tournament_id, chooser=None, limit=200):
    tournament = rig.core.detail(rig.admin, tournament_id)
    if tournament["status"] == "registration":
        tournament = rig.core.start(rig.admin, tournament_id, tournament["version"])
    chooser = chooser or higher_seed(rig, tournament_id)
    for _ in range(limit):
        if tournament["status"] != "running":
            return tournament
        pending = ready_matches(rig, tournament_id)
        if not pending:
            tournament = rig.core.settle(rig.admin, tournament_id)
            continue
        for match in pending:
            first = match["participants"]["a"]["account_id"]
            second = match["participants"]["b"]["account_id"]
            force_match(rig, match, chooser(match, first, second))
        tournament = rig.core.settle(rig.admin, tournament_id)
    raise AssertionError("tournament did not settle")


def advance_to_grand_final(rig, tournament_id):
    """Play every match except the grand final until the grand final is ready."""
    tournament = rig.core.detail(rig.admin, tournament_id)
    if tournament["status"] == "registration":
        rig.core.start(rig.admin, tournament_id, tournament["version"])
    seeds = seeds_of(rig, tournament_id)
    for _ in range(100):
        bracket = rig.core.bracket(rig.admin, tournament_id)
        final = bracket["final"]
        if final is not None and final["status"] == "ready":
            return final
        pending = [
            match
            for match in ready_matches(rig, tournament_id)
            if final is None or match["id"] != final["id"]
        ]
        if not pending:
            rig.core.settle(rig.admin, tournament_id)
            continue
        for match in pending:
            first = match["participants"]["a"]["account_id"]
            second = match["participants"]["b"]["account_id"]
            force_match(rig, match, first if seeds[first] < seeds[second] else second)
        rig.core.settle(rig.admin, tournament_id)
    raise AssertionError("the grand final never became ready")


def loss_counts(rig, tournament_id):
    bracket = rig.core.bracket(rig.admin, tournament_id)
    counts: dict[str, int] = {}
    matches = [
        match
        for group in ("upper", "lower")
        for entry in bracket[group]
        for match in entry["matches"]
    ]
    matches += [bracket[key] for key in ("final", "reset") if bracket[key] is not None]
    for match in matches:
        if match["status"] == "complete" and match["loser"]:
            counts[match["loser"]["account_id"]] = counts.get(match["loser"]["account_id"], 0) + 1
    return counts


def assert_double_elimination(rig, tournament_id):
    tournament = rig.core.detail(rig.admin, tournament_id)
    champion = tournament["official_winner"]["account_id"]
    losses = loss_counts(rig, tournament_id)
    assert losses.get(champion, 0) <= 1
    for entry in tournament["entrants"]:
        if entry["account_id"] != champion:
            assert losses.get(entry["account_id"], 0) == 2


def table_counts(rig):
    with rig.store.connection() as c:
        return {
            "matches": c.execute("SELECT COUNT(*) FROM player_tournament_matches").fetchone()[0],
            "legs": c.execute("SELECT COUNT(*) FROM player_tournament_legs").fetchone()[0],
            "games": c.execute("SELECT COUNT(*) FROM online_games").fetchone()[0],
        }


def assert_no_duplicate_matches_or_legs(rig):
    with rig.store.connection() as c:
        assert (
            c.execute(
                "SELECT COUNT(*) FROM (SELECT id FROM player_tournament_matches GROUP BY id HAVING COUNT(*)>1)"
            ).fetchone()[0]
            == 0
        )
        assert (
            c.execute(
                "SELECT COUNT(*) FROM (SELECT match_id,leg FROM player_tournament_legs GROUP BY match_id,leg HAVING COUNT(*)>1)"
            ).fetchone()[0]
            == 0
        )
        assert (
            c.execute(
                "SELECT COUNT(*) FROM (SELECT game_id FROM player_tournament_legs GROUP BY game_id HAVING COUNT(*)>1)"
            ).fetchone()[0]
            == 0
        )


def raw_cursor(payload) -> str:
    """Encode a cursor without the production encoder, to forge malformed payloads."""
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


# -- complete tournaments ------------------------------------------------


@pytest.mark.parametrize("entrants", [2, 4, 5, 8, 16])
def test_complete_tournaments_eliminate_after_two_losses(rig, entrants):
    names = [*PLAYERS, ADMIN][:entrants]
    tournament = tournament_with_entrants(rig, names, capacity=max(entrants, 2))
    tournament = play_out(rig, tournament["id"])
    assert tournament["status"] == "complete"
    assert tournament["official_winner"] is not None
    assert_double_elimination(rig, tournament["id"])


def test_non_power_of_two_bracket_uses_byes(rig):
    names = [*PLAYERS, ADMIN][:5]
    tournament = tournament_with_entrants(rig, names, capacity=5)
    started = rig.core.start(rig.admin, tournament["id"], tournament["version"])
    assert started["bracket_size"] == 8
    bracket = rig.core.bracket(rig.admin, tournament["id"])
    opening = next(entry for entry in bracket["upper"] if entry["round"] == 1)
    byes = [match for match in opening["matches"] if match["status"] == "bye"]
    assert len(byes) == 3
    # A bye never creates a game and never counts as a loss.
    assert all(not match["legs"] for match in byes)
    assert all(match["winner"] is not None for match in byes)
    finished = play_out(rig, tournament["id"])
    assert finished["status"] == "complete"
    assert_double_elimination(rig, tournament["id"])


def test_lower_bracket_path_can_win_the_tournament(rig):
    names = [*PLAYERS, ADMIN][:8]
    tournament = tournament_with_entrants(rig, names, capacity=8)
    started = rig.core.start(rig.admin, tournament["id"], tournament["version"])
    seeds = seeds_of(rig, tournament["id"])
    favourite = next(entry for entry in started["entrants"] if entry["seed"] == 1)
    state = {"lost": False}

    def chooser(_match, a, b):
        if not state["lost"] and favourite["account_id"] in (a, b):
            state["lost"] = True
            return b if favourite["account_id"] == a else a
        return a if seeds[a] < seeds[b] else b

    finished = play_out(rig, tournament["id"], chooser)
    assert finished["status"] == "complete"
    assert finished["official_winner"]["account_id"] == favourite["account_id"]
    bracket = rig.core.bracket(rig.admin, tournament["id"])
    assert bracket["final"]["decided_by"] == "play"
    assert bracket["final"]["winner"]["account_id"] == favourite["account_id"]
    assert bracket["reset"]["status"] == "complete"
    assert bracket["reset"]["winner"]["account_id"] == favourite["account_id"]
    assert loss_counts(rig, tournament["id"])[favourite["account_id"]] == 1
    assert_double_elimination(rig, tournament["id"])


def test_undefeated_winner_needs_no_reset(rig):
    names = [*PLAYERS, ADMIN][:4]
    tournament = tournament_with_entrants(rig, names, capacity=4)
    finished = play_out(rig, tournament["id"])
    bracket = rig.core.bracket(rig.admin, tournament["id"])
    assert bracket["reset"] is None
    assert (
        bracket["final"]["winner"]["account_id"]
        == bracket["final"]["participants"]["a"]["account_id"]
    )
    assert loss_counts(rig, tournament["id"]).get(finished["official_winner"]["account_id"], 0) == 0


# -- authoritative result handling ---------------------------------------


def test_voided_leg_disputes_bracket_and_rebuild_restores_progress(rig):
    names = [*PLAYERS, ADMIN][:4]
    tournament = tournament_with_entrants(rig, names, capacity=4)
    rig.core.start(rig.admin, tournament["id"], tournament["version"])
    match = ready_matches(rig, tournament["id"])[0]
    force_match(rig, match, match["participants"]["a"]["account_id"])
    rig.core.settle(rig.admin, tournament["id"])
    settled = [m for m in ready_matches(rig, tournament["id"])]
    assert match["id"] not in [m["id"] for m in settled]

    game_id = match["legs"][0]["game_id"]
    game = rig.online.read(rig.admin, game_id)
    rig.online.mutate(rig.admin, game_id, game["version"], "void", note="corrected result")

    disputed = rig.core.settle(rig.admin, tournament["id"])
    assert disputed["review_state"] == "disputed"
    bracket = rig.core.bracket(rig.admin, tournament["id"])
    broken = [m for entry in bracket["upper"] for m in entry["matches"] if m["id"] == match["id"]][
        0
    ]
    assert broken["status"] == "disputed"

    rebuilt = rig.core.rebuild(
        rig.admin, tournament["id"], disputed["version"], note="re-derive after correction"
    )
    assert rebuilt["status"] == "running" and rebuilt["review_state"] == "ok"
    bracket = rig.core.bracket(rig.admin, tournament["id"])
    restored = [
        m for entry in bracket["upper"] for m in entry["matches"] if m["id"] == match["id"]
    ][0]
    seeds = seeds_of(rig, tournament["id"])
    assert restored["status"] == "complete"
    assert restored["decided_by"] == "higher_seed"
    assert restored["legs"][0]["outcome"] == "void"
    assert seeds[restored["winner"]["account_id"]] == min(
        seeds[restored["participants"]["a"]["account_id"]],
        seeds[restored["participants"]["b"]["account_id"]],
    )
    audit = rig.core.audit(rig.admin, tournament["id"])
    assert {"disputed", "rebuild"} <= {entry["action"] for entry in audit["items"]}


def test_voided_single_leg_tie_resolves_to_higher_seed(rig):
    names = [*PLAYERS, ADMIN][:4]
    tournament = tournament_with_entrants(rig, names, capacity=4)
    rig.core.start(rig.admin, tournament["id"], tournament["version"])
    match = ready_matches(rig, tournament["id"])[0]
    game_id = match["legs"][0]["game_id"]
    game = rig.online.read(rig.admin, game_id)
    rig.online.mutate(rig.admin, game_id, game["version"], "void", note="no contest")
    rig.core.settle(rig.admin, tournament["id"])
    bracket = rig.core.bracket(rig.admin, tournament["id"])
    decided = [m for entry in bracket["upper"] for m in entry["matches"] if m["id"] == match["id"]][
        0
    ]
    seeds = seeds_of(rig, tournament["id"])
    assert decided["status"] == "complete" and decided["decided_by"] == "higher_seed"
    assert seeds[decided["winner"]["account_id"]] == min(
        seeds[decided["participants"]["a"]["account_id"]],
        seeds[decided["participants"]["b"]["account_id"]],
    )


def test_timeout_leg_decides_match_without_client_input(rig):
    names = [*PLAYERS, ADMIN][:3]
    tournament = tournament_with_entrants(rig, names, capacity=4)
    rig.core.start(rig.admin, tournament["id"], tournament["version"])
    match = ready_matches(rig, tournament["id"])[0]
    rig.clock.value += timedelta(seconds=601)
    assert rig.online.sweep()["finalized"] == 1
    rig.core.settle(rig.admin, tournament["id"])
    bracket = rig.core.bracket(rig.admin, tournament["id"])
    decided = [m for entry in bracket["upper"] for m in entry["matches"] if m["id"] == match["id"]][
        0
    ]
    assert decided["status"] == "complete"
    assert decided["legs"][0]["outcome"] in {"a", "b"}
    assert decided["winner"]["account_id"] in (
        decided["participants"]["a"]["account_id"],
        decided["participants"]["b"]["account_id"],
    )


def test_two_leg_matches_swap_sides_and_need_both_results(rig):
    names = [*PLAYERS, ADMIN][:4]
    tournament = tournament_with_entrants(rig, names, capacity=4, match_legs=2)
    rig.core.start(rig.admin, tournament["id"], tournament["version"])
    match = ready_matches(rig, tournament["id"])[0]
    assert len(match["legs"]) == 2
    first = rig.online.read(rig.admin, match["legs"][0]["game_id"])
    second = rig.online.read(rig.admin, match["legs"][1]["game_id"])
    assert (
        first["participants"]["attackers"]["account_id"] == match["participants"]["a"]["account_id"]
    )
    assert (
        second["participants"]["attackers"]["account_id"]
        == match["participants"]["b"]["account_id"]
    )
    # One decided leg is not enough: the match stays ready until both settle.
    force_match(rig, {"legs": match["legs"][:1]}, match["participants"]["a"]["account_id"])
    rig.core.settle(rig.admin, tournament["id"])
    bracket = rig.core.bracket(rig.admin, tournament["id"])
    partial = [m for entry in bracket["upper"] for m in entry["matches"] if m["id"] == match["id"]][
        0
    ]
    assert partial["status"] == "ready"
    assert [leg["outcome"] for leg in partial["legs"]] == ["a", None]
    force_match(rig, {"legs": match["legs"][1:]}, match["participants"]["a"]["account_id"])
    rig.core.settle(rig.admin, tournament["id"])
    bracket = rig.core.bracket(rig.admin, tournament["id"])
    decided = [m for entry in bracket["upper"] for m in entry["matches"] if m["id"] == match["id"]][
        0
    ]
    assert decided["status"] == "complete"
    assert decided["winner"]["account_id"] == match["participants"]["a"]["account_id"]


def test_settlement_is_idempotent_and_never_duplicates_games_or_advancement(rig):
    names = [*PLAYERS, ADMIN][:8]
    tournament = tournament_with_entrants(rig, names, capacity=8)
    rig.core.start(rig.admin, tournament["id"], tournament["version"])
    for match in ready_matches(rig, tournament["id"]):
        force_match(rig, match, match["participants"]["a"]["account_id"])
    first = rig.core.settle(rig.admin, tournament["id"])
    with rig.store.connection() as c:
        matches = c.execute("SELECT COUNT(*) FROM player_tournament_matches").fetchone()[0]
        legs = c.execute("SELECT COUNT(*) FROM player_tournament_legs").fetchone()[0]
        games = c.execute("SELECT COUNT(*) FROM online_games").fetchone()[0]
    for _ in range(3):
        again = rig.core.settle(rig.admin, tournament["id"])
        assert again["version"] == first["version"]
    with rig.store.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM player_tournament_matches").fetchone()[0] == matches
        assert c.execute("SELECT COUNT(*) FROM player_tournament_legs").fetchone()[0] == legs
        assert c.execute("SELECT COUNT(*) FROM online_games").fetchone()[0] == games
        assert (
            c.execute(
                "SELECT COUNT(*) FROM (SELECT id FROM player_tournament_matches GROUP BY id HAVING COUNT(*)>1)"
            ).fetchone()[0]
            == 0
        )


def test_concurrent_completions_advance_each_match_once(rig):
    names = [*PLAYERS, ADMIN][:8]
    tournament = tournament_with_entrants(rig, names, capacity=8)
    rig.core.start(rig.admin, tournament["id"], tournament["version"])
    matches = ready_matches(rig, tournament["id"])
    assert len(matches) >= 2
    with ThreadPoolExecutor(max_workers=len(matches)) as pool:
        list(
            pool.map(
                lambda match: force_match(rig, match, match["participants"]["a"]["account_id"]),
                matches,
            )
        )
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: rig.core.settle(rig.admin, tournament["id"]), range(4)))
    bracket = rig.core.bracket(rig.admin, tournament["id"])
    for match in matches:
        settled = [
            m for entry in bracket["upper"] for m in entry["matches"] if m["id"] == match["id"]
        ][0]
        assert settled["status"] == "complete"
        assert len(settled["legs"]) == 1


def test_voided_result_after_completion_reopens_the_tournament(rig):
    names = [*PLAYERS, ADMIN][:4]
    tournament = tournament_with_entrants(rig, names, capacity=4)
    finished = play_out(rig, tournament["id"])
    assert finished["status"] == "complete"
    bracket = rig.core.bracket(rig.admin, tournament["id"])
    game_id = bracket["final"]["legs"][0]["game_id"]
    game = rig.online.read(rig.admin, game_id)
    rig.online.mutate(rig.admin, game_id, game["version"], "void", note="late correction")
    reopened = rig.core.settle(rig.admin, tournament["id"])
    assert reopened["status"] == "running" and reopened["review_state"] == "disputed"
    assert reopened["official_winner"] is None and reopened["completed_at"] is None
    rebuilt = rig.core.rebuild(rig.admin, tournament["id"], reopened["version"], note="re-derive")
    assert rebuilt["review_state"] == "ok"
    assert rebuilt["status"] == "complete"
    assert rebuilt["official_winner"] is not None


def test_rebuild_recreates_the_reset_after_a_voided_reset_leg(rig):
    names = [*PLAYERS, ADMIN][:8]
    tournament = tournament_with_entrants(rig, names, capacity=8)
    started = rig.core.start(rig.admin, tournament["id"], tournament["version"])
    seeds = seeds_of(rig, tournament["id"])
    favourite = next(entry for entry in started["entrants"] if entry["seed"] == 1)
    state = {"lost": False}

    def chooser(_match, a, b):
        if not state["lost"] and favourite["account_id"] in (a, b):
            state["lost"] = True
            return b if favourite["account_id"] == a else a
        return a if seeds[a] < seeds[b] else b

    finished = play_out(rig, tournament["id"], chooser)
    assert finished["status"] == "complete"
    bracket = rig.core.bracket(rig.admin, tournament["id"])
    assert bracket["final"]["winner"]["account_id"] == favourite["account_id"]
    assert bracket["reset"]["winner"]["account_id"] == favourite["account_id"]

    game = rig.online.read(rig.admin, bracket["reset"]["legs"][0]["game_id"])
    rig.online.mutate(rig.admin, game["id"], game["version"], "void", note="late correction")
    disputed = rig.core.settle(rig.admin, tournament["id"])
    assert disputed["review_state"] == "disputed" and disputed["status"] == "running"

    rebuilt = rig.core.rebuild(
        rig.admin, tournament["id"], disputed["version"], note="re-derive after correction"
    )
    assert rebuilt["review_state"] == "ok"
    bracket = rig.core.bracket(rig.admin, tournament["id"])
    assert bracket["final"]["winner"]["account_id"] == favourite["account_id"]
    assert bracket["reset"] is not None and bracket["reset"]["status"] == "ready"
    assert len(bracket["reset"]["legs"]) == 1


def test_rebuild_reseats_a_downstream_match_after_a_voided_leg(rig, monkeypatch):
    names = [*PLAYERS, ADMIN][:4]
    tournament = tournament_with_entrants(rig, names, capacity=4)
    rig.core.start(rig.admin, tournament["id"], tournament["version"])
    seeds = seeds_of(rig, tournament["id"])
    bracket = rig.core.bracket(rig.admin, tournament["id"])
    opening = {
        match["id"]: match
        for entry in bracket["upper"]
        if entry["round"] == 1
        for match in entry["matches"]
    }
    first = opening[f"{tournament['id']}:upper:1:0"]
    target = opening[f"{tournament['id']}:upper:1:1"]
    force_match(rig, first, first["participants"]["a"]["account_id"])
    seat_a = target["participants"]["a"]["account_id"]
    seat_b = target["participants"]["b"]["account_id"]
    lower_seed = seat_a if seeds[seat_a] > seeds[seat_b] else seat_b
    higher_seed = seat_b if lower_seed == seat_a else seat_a
    force_match(rig, target, lower_seed)
    rig.core.settle(rig.admin, tournament["id"])

    bracket = rig.core.bracket(rig.admin, tournament["id"])
    upper_final = bracket["upper"][-1]["matches"][0]
    assert upper_final["id"] == f"{tournament['id']}:upper:2:0"
    assert upper_final["status"] == "ready"
    assert upper_final["participants"]["b"]["account_id"] == lower_seed
    assert len(upper_final["legs"]) == 1
    stale_game = upper_final["legs"][0]["game_id"]

    # An accepted move on the game that is about to be displaced is history a
    # rebuild must keep, not rewrite.
    stale = rig.online.read(rig.admin, stale_game)
    mover = stale["participants"][stale["position"]["turn"]]["account_id"]
    move = safe_move(stale)
    rig.online.mutate(
        rig.by_id[mover],
        stale_game,
        stale["version"],
        "move",
        source=move.source,
        target=move.target,
    )

    game = rig.online.read(rig.admin, target["legs"][0]["game_id"])
    rig.online.mutate(rig.admin, game["id"], game["version"], "void", note="corrected result")
    disputed = rig.core.settle(rig.admin, tournament["id"])
    assert disputed["review_state"] == "disputed"

    # A rebuild that fails while retiring the displaced game commits nothing:
    # the leg link, the live game and the disputed review state all survive.
    def explode(*_args, **_kwargs):
        raise RuntimeError("injected retirement failure")

    monkeypatch.setattr(rig.online, "void_game", explode)
    with pytest.raises(RuntimeError, match="injected"):
        rig.core.rebuild(
            rig.admin, tournament["id"], disputed["version"], note="fails mid-transaction"
        )
    monkeypatch.undo()
    assert rig.online.read(rig.admin, stale_game)["status"] == "active"
    rolled_back = rig.core.bracket(rig.admin, tournament["id"])
    upper_final = rolled_back["upper"][-1]["matches"][0]
    assert [leg["game_id"] for leg in upper_final["legs"]] == [stale_game]
    still_disputed = rig.core.detail(rig.admin, tournament["id"])
    assert still_disputed["review_state"] == "disputed"
    assert still_disputed["version"] == disputed["version"]

    rebuilt = rig.core.rebuild(
        rig.admin, tournament["id"], disputed["version"], note="re-derive after correction"
    )
    assert rebuilt["review_state"] == "ok"
    bracket = rig.core.bracket(rig.admin, tournament["id"])
    upper_final = bracket["upper"][-1]["matches"][0]
    assert upper_final["status"] == "ready"
    assert upper_final["participants"]["b"]["account_id"] == higher_seed
    assert len(upper_final["legs"]) == 1
    assert upper_final["legs"][0]["game_id"] != stale_game
    reseated = rig.online.read(rig.admin, upper_final["legs"][0]["game_id"])
    assert reseated["status"] == "active"
    assert {
        reseated["participants"]["attackers"]["account_id"],
        reseated["participants"]["defenders"]["account_id"],
    } == {upper_final["participants"]["a"]["account_id"], higher_seed}

    # The displaced game is retired through the authoritative online lifecycle:
    # terminal, unplayable, unable to expire, with its move and void audit kept.
    retired = rig.online.read(rig.admin, stale_game)
    assert retired["status"] == "voided" and retired["deadline"] is None
    assert retired["result"]["winner"] is None
    assert retired["result"]["termination_reason"] == "administrative_void"
    with pytest.raises(Exception, match="active game"):
        rig.online.mutate(rig.by_id[mover], stale_game, retired["version"], "concede")
    with rig.store.connection() as c:
        moves = c.execute(
            "SELECT COUNT(*) FROM online_moves WHERE game_id=?", (stale_game,)
        ).fetchone()[0]
        results = c.execute(
            "SELECT COUNT(*) FROM online_results WHERE game_id=?", (stale_game,)
        ).fetchone()[0]
        void_audit = c.execute(
            "SELECT actor_id,note FROM online_void_audit WHERE game_id=?", (stale_game,)
        ).fetchone()
    assert moves == 1
    assert results == 1
    assert void_audit is not None
    assert void_audit["actor_id"] == rig.accounts[ADMIN]["id"]
    assert void_audit["note"] == DISPLACED_GAME_NOTE

    # Only the replacement leg is linked, so only it can advance the bracket.
    linked = {
        leg["game_id"]
        for match in [
            *[m for entry in bracket["upper"] + bracket["lower"] for m in entry["matches"]],
            *[m for m in (bracket["final"], bracket["reset"]) if m is not None],
        ]
        for leg in match["legs"]
    }
    assert stale_game not in linked

    # Repeated settlement is idempotent and never duplicates games or links.
    counts = table_counts(rig)
    for _ in range(2):
        again = rig.core.settle(rig.admin, tournament["id"])
        assert again["version"] == rebuilt["version"]
    assert table_counts(rig) == counts
    assert_no_duplicate_matches_or_legs(rig)

    # A repeated rebuild re-derives the same bracket: no extra games, no second
    # void of the same leg and no rating event for a tournament game.
    rig.core.rebuild(
        rig.admin,
        tournament["id"],
        rig.core.detail(rig.admin, tournament["id"])["version"],
        note="rebuild again",
    )
    assert table_counts(rig) == counts
    assert_no_duplicate_matches_or_legs(rig)
    with rig.store.connection() as c:
        assert (
            c.execute(
                "SELECT COUNT(*) FROM online_void_audit WHERE game_id=?", (stale_game,)
            ).fetchone()[0]
            == 1
        )
        assert c.execute("SELECT COUNT(*) FROM rating_events").fetchone()[0] == 0

    # A retired leg can never expire into a fresh result.
    rig.clock.value += timedelta(seconds=601)
    rig.online.sweep()
    assert rig.online.read(rig.admin, stale_game)["status"] == "voided"
    with rig.store.connection() as c:
        assert (
            c.execute(
                "SELECT COUNT(*) FROM online_results WHERE game_id=?", (stale_game,)
            ).fetchone()[0]
            == results
        )


def test_rebuild_keeps_a_completed_game_that_the_reseat_detaches(rig):
    names = [*PLAYERS, ADMIN][:4]
    tournament = tournament_with_entrants(rig, names, capacity=4)
    rig.core.start(rig.admin, tournament["id"], tournament["version"])
    seeds = seeds_of(rig, tournament["id"])
    bracket = rig.core.bracket(rig.admin, tournament["id"])
    opening = {
        match["id"]: match
        for entry in bracket["upper"]
        if entry["round"] == 1
        for match in entry["matches"]
    }
    first = opening[f"{tournament['id']}:upper:1:0"]
    target = opening[f"{tournament['id']}:upper:1:1"]
    force_match(rig, first, first["participants"]["a"]["account_id"])
    seat_a = target["participants"]["a"]["account_id"]
    seat_b = target["participants"]["b"]["account_id"]
    lower_seed = seat_a if seeds[seat_a] > seeds[seat_b] else seat_b
    higher_seed = seat_b if lower_seed == seat_a else seat_a
    force_match(rig, target, lower_seed)
    rig.core.settle(rig.admin, tournament["id"])

    # Play the downstream match out so its leg is a completed game before the
    # upstream correction changes who belongs in it.
    bracket = rig.core.bracket(rig.admin, tournament["id"])
    upper_final = bracket["upper"][-1]["matches"][0]
    force_match(rig, upper_final, upper_final["participants"]["a"]["account_id"])
    rig.core.settle(rig.admin, tournament["id"])
    completed_game = upper_final["legs"][0]["game_id"]
    assert rig.online.read(rig.admin, completed_game)["status"] == "completed"

    game = rig.online.read(rig.admin, target["legs"][0]["game_id"])
    rig.online.mutate(rig.admin, game["id"], game["version"], "void", note="corrected result")
    disputed = rig.core.settle(rig.admin, tournament["id"])
    assert disputed["review_state"] == "disputed"
    rebuilt = rig.core.rebuild(
        rig.admin, tournament["id"], disputed["version"], note="re-derive after correction"
    )
    assert rebuilt["review_state"] == "ok"

    bracket = rig.core.bracket(rig.admin, tournament["id"])
    upper_final = bracket["upper"][-1]["matches"][0]
    assert upper_final["participants"]["b"]["account_id"] == higher_seed
    assert len(upper_final["legs"]) == 1
    assert upper_final["legs"][0]["game_id"] != completed_game
    assert rig.online.read(rig.admin, upper_final["legs"][0]["game_id"])["status"] == "active"

    # The completed game keeps its result and history: a rebuild detaches the
    # link but never voids or rewrites a game that already finished.
    preserved = rig.online.read(rig.admin, completed_game)
    assert preserved["status"] == "completed"
    assert preserved["result"]["winner"] is not None
    with rig.store.connection() as c:
        assert (
            c.execute(
                "SELECT COUNT(*) FROM online_void_audit WHERE game_id=?", (completed_game,)
            ).fetchone()[0]
            == 0
        )
        assert (
            c.execute(
                "SELECT COUNT(*) FROM online_results WHERE game_id=?", (completed_game,)
            ).fetchone()[0]
            == 1
        )
        assert c.execute("SELECT COUNT(*) FROM rating_events").fetchone()[0] == 0


def test_rebuild_retires_an_obsolete_grand_final_reset_game(rig):
    names = [*PLAYERS, ADMIN][:8]
    tournament = tournament_with_entrants(rig, names, capacity=8)
    final = advance_to_grand_final(rig, tournament["id"])
    challenger = final["participants"]["b"]["account_id"]
    rig.core.override(
        rig.admin,
        tournament["id"],
        final["id"],
        rig.core.detail(rig.admin, tournament["id"])["version"],
        challenger,
        "challenger awarded the grand final",
    )
    bracket = rig.core.bracket(rig.admin, tournament["id"])
    assert bracket["reset"]["status"] == "ready"
    assert len(bracket["reset"]["legs"]) == 1
    reset_game = bracket["reset"]["legs"][0]["game_id"]
    assert rig.online.read(rig.admin, reset_game)["status"] == "active"

    # An upstream correction disputes the bracket, so the rematch the override
    # created is re-derived from the authoritative results.
    upstream = bracket["upper"][0]["matches"][0]
    game = rig.online.read(rig.admin, upstream["legs"][0]["game_id"])
    rig.online.mutate(rig.admin, game["id"], game["version"], "void", note="corrected result")
    disputed = rig.core.settle(rig.admin, tournament["id"])
    assert disputed["review_state"] == "disputed"

    rebuilt = rig.core.rebuild(
        rig.admin, tournament["id"], disputed["version"], note="re-derive after correction"
    )
    assert rebuilt["review_state"] == "ok"
    bracket = rig.core.bracket(rig.admin, tournament["id"])
    # The rebuild clears the override, so the undefeated finalist wins and the
    # rematch is obsolete rather than merely re-created.
    assert bracket["reset"] is None

    retired = rig.online.read(rig.admin, reset_game)
    assert retired["status"] == "voided" and retired["deadline"] is None
    assert retired["result"]["winner"] is None
    mover = retired["participants"][retired["position"]["turn"]]["account_id"]
    with pytest.raises(Exception, match="active game"):
        rig.online.mutate(rig.by_id[mover], reset_game, retired["version"], "concede")
    with rig.store.connection() as c:
        void_audit = c.execute(
            "SELECT actor_id,note FROM online_void_audit WHERE game_id=?", (reset_game,)
        ).fetchone()
    assert void_audit is not None
    assert void_audit["actor_id"] == rig.accounts[ADMIN]["id"]
    assert void_audit["note"] == DISPLACED_GAME_NOTE

    # Exactly the obsolete rematch was retired: every other game is untouched.
    audit = rig.core.audit(rig.admin, tournament["id"])
    entry = next(item for item in audit["items"] if item["action"] == "rebuild")
    assert entry["note"] == "re-derive after correction"
    assert entry["details"] == {"retired_games": 1}
    with rig.store.connection() as c:
        voided = {row[0] for row in c.execute("SELECT game_id FROM online_void_audit")}
    assert voided == {
        final["legs"][0]["game_id"],  # voided by the override
        upstream["legs"][0]["game_id"],  # the corrected upstream leg
        reset_game,  # the obsolete rematch
    }


def test_dispute_freezes_the_whole_bracket_until_rebuild(rig):
    names = [*PLAYERS, ADMIN][:8]
    tournament = tournament_with_entrants(rig, names, capacity=8)
    identifier = tournament["id"]
    rig.core.start(rig.admin, identifier, tournament["version"])
    for match in ready_matches(rig, identifier):
        force_match(rig, match, match["participants"]["a"]["account_id"])
    rig.core.settle(rig.admin, identifier)
    for match in ready_matches(rig, identifier):
        force_match(rig, match, match["participants"]["a"]["account_id"])
    rig.core.settle(rig.admin, identifier)

    bracket = rig.core.bracket(rig.admin, identifier)
    disputed_target = next(
        match for match in bracket["upper"][1]["matches"] if match["id"].endswith(":2:1")
    )
    unaffected = next(
        match for match in bracket["lower"][1]["matches"] if match["id"].endswith(":2:1")
    )
    assert disputed_target["status"] == "complete"
    assert unaffected["status"] == "ready" and unaffected["legs"]
    # The unaffected lower match does not depend on the upper match we dispute.
    force_match(rig, unaffected, unaffected["participants"]["a"]["account_id"])
    before = table_counts(rig)

    game = rig.online.read(rig.admin, disputed_target["legs"][0]["game_id"])
    rig.online.mutate(rig.admin, game["id"], game["version"], "void", note="corrected result")
    disputed = rig.core.settle(rig.admin, identifier)
    assert disputed["review_state"] == "disputed"
    assert table_counts(rig) == before  # no new games, legs or matches

    bracket = rig.core.bracket(rig.admin, identifier)
    broken = next(
        match
        for entry in bracket["upper"]
        for match in entry["matches"]
        if match["id"] == disputed_target["id"]
    )
    assert broken["status"] == "disputed"
    # The unaffected branch is frozen too: it stays ready instead of completing.
    frozen = next(
        match
        for entry in bracket["lower"]
        for match in entry["matches"]
        if match["id"] == unaffected["id"]
    )
    assert frozen["status"] == "ready"
    for entry in bracket["upper"] + bracket["lower"]:
        for match in entry["matches"]:
            if match["status"] == "pending":
                assert match["legs"] == []

    # Repeated settlement stays frozen.
    for _ in range(3):
        again = rig.core.settle(rig.admin, identifier)
        assert again["review_state"] == "disputed"
    assert table_counts(rig) == before

    # Rebuild re-derives the bracket and the unaffected branch advances without
    # duplicate matches or legs.
    rebuilt = rig.core.rebuild(
        rig.admin, identifier, disputed["version"], note="re-derive after correction"
    )
    assert rebuilt["status"] == "running" and rebuilt["review_state"] == "ok"
    assert_no_duplicate_matches_or_legs(rig)
    finished = play_out(rig, identifier)
    assert finished["status"] == "complete"
    assert_no_duplicate_matches_or_legs(rig)


def test_disputed_completed_tournament_reopens_and_freezes_until_rebuild(rig):
    names = [*PLAYERS, ADMIN][:4]
    tournament = tournament_with_entrants(rig, names, capacity=4)
    finished = play_out(rig, tournament["id"])
    assert finished["status"] == "complete"
    bracket = rig.core.bracket(rig.admin, tournament["id"])
    game = rig.online.read(rig.admin, bracket["final"]["legs"][0]["game_id"])
    rig.online.mutate(rig.admin, game["id"], game["version"], "void", note="late correction")

    disputed = rig.core.settle(rig.admin, tournament["id"])
    assert disputed["status"] == "running" and disputed["review_state"] == "disputed"
    assert disputed["official_winner"] is None and disputed["completed_at"] is None
    # Frozen: no further settlement can re-crown a champion while disputed.
    frozen = rig.core.settle(rig.admin, tournament["id"])
    assert frozen["status"] == "running" and frozen["review_state"] == "disputed"
    assert frozen["official_winner"] is None

    rebuilt = rig.core.rebuild(
        rig.admin, tournament["id"], disputed["version"], note="re-derive after correction"
    )
    assert rebuilt["status"] == "complete" and rebuilt["review_state"] == "ok"
    assert rebuilt["official_winner"] is not None
    assert_no_duplicate_matches_or_legs(rig)


def test_admin_override_decides_a_ready_match(rig):
    names = [*PLAYERS, ADMIN][:4]
    tournament = tournament_with_entrants(rig, names, capacity=4)
    rig.core.start(rig.admin, tournament["id"], tournament["version"])
    match = ready_matches(rig, tournament["id"])[0]
    winner = match["participants"]["b"]["account_id"]
    updated = rig.core.override(
        rig.admin,
        tournament["id"],
        match["id"],
        rig.core.detail(rig.admin, tournament["id"])["version"],
        winner,
        "opponent failed to appear",
    )
    bracket = rig.core.bracket(rig.admin, tournament["id"])
    decided = [m for entry in bracket["upper"] for m in entry["matches"] if m["id"] == match["id"]][
        0
    ]
    assert decided["status"] == "complete" and decided["decided_by"] == "admin_override"
    assert decided["winner"]["account_id"] == winner
    # The outstanding games are retired so no live game outlives the decision.
    assert all(leg["game_status"] == "voided" for leg in decided["legs"])
    assert updated["version"] > 0
    audit = rig.core.audit(rig.admin, tournament["id"])
    override = next(entry for entry in audit["items"] if entry["action"] == "override")
    assert override["note"] == "opponent failed to appear"
    # Settling again never reopens the administrative decision.
    assert rig.core.settle(rig.admin, tournament["id"])["review_state"] == "ok"


def test_override_of_the_grand_final_for_the_challenger_forces_a_reset(rig):
    names = [*PLAYERS, ADMIN][:4]
    tournament = tournament_with_entrants(rig, names, capacity=4)
    final = advance_to_grand_final(rig, tournament["id"])
    challenger = final["participants"]["b"]["account_id"]
    overridden = rig.core.override(
        rig.admin,
        tournament["id"],
        final["id"],
        rig.core.detail(rig.admin, tournament["id"])["version"],
        challenger,
        "challenger awarded the grand final",
    )
    assert overridden["status"] == "running"
    bracket = rig.core.bracket(rig.admin, tournament["id"])
    assert bracket["reset"] is not None and bracket["reset"]["status"] == "ready"
    force_match(rig, bracket["reset"], challenger)
    finished = rig.core.settle(rig.admin, tournament["id"])
    assert finished["status"] == "complete"
    assert finished["official_winner"]["account_id"] == challenger


# -- registration, capacity and deadlines --------------------------------


def test_registration_capacity_deadline_and_withdrawal(rig):
    tournament = create(rig, capacity=2)
    assert rig.core.register(rig.credentials["P01"], tournament["id"])["entrant_count"] == 1
    assert rig.core.register(rig.credentials["P02"], tournament["id"])["entrant_count"] == 2
    with pytest.raises(Exception, match="full"):
        rig.core.register(rig.credentials["P03"], tournament["id"])
    withdrawn = rig.core.withdraw(rig.credentials["P02"], tournament["id"])
    assert withdrawn["entrant_count"] == 1
    assert rig.core.register(rig.credentials["P03"], tournament["id"])["entrant_count"] == 2
    with pytest.raises(Exception, match="not registered"):
        rig.core.withdraw(rig.credentials["P02"], tournament["id"])

    closed = create(
        rig,
        name="Closed",
        capacity=4,
        deadline=rig.clock.value + timedelta(seconds=1),
    )
    rig.clock.value += timedelta(seconds=2)
    with pytest.raises(Exception, match="deadline"):
        rig.core.register(rig.credentials["P01"], closed["id"])


def test_registration_requires_a_verified_account(rig):
    tournament = create(rig, capacity=4)
    with pytest.raises(Exception, match="verified"):
        rig.core.register(rig.credentials[UNVERIFIED], tournament["id"])


def test_start_requires_two_entrants_and_freezes_seeding(rig):
    tournament = tournament_with_entrants(rig, ["P01"], capacity=4)
    with pytest.raises(Exception, match="two registered"):
        rig.core.start(rig.admin, tournament["id"], tournament["version"])

    tournament = tournament_with_entrants(rig, ["P01", "P02", "P03"], capacity=4)
    order = [rig.accounts["P03"]["id"], rig.accounts["P01"]["id"], rig.accounts["P02"]["id"]]
    started = rig.core.start(rig.admin, tournament["id"], tournament["version"], order)
    assert started["bracket_size"] == 4
    seeds = seeds_of(rig, tournament["id"])
    assert seeds[rig.accounts["P03"]["id"]] == 1
    assert seeds[rig.accounts["P01"]["id"]] == 2

    bad = tournament_with_entrants(rig, ["P01", "P02"], capacity=4)
    with pytest.raises(Exception, match="exactly once"):
        rig.core.start(rig.admin, bad["id"], bad["version"], [rig.accounts["P01"]["id"]])


def test_start_ignores_null_seeds_left_by_withdrawn_entrants(rig):
    tournament = create(rig, capacity=4)
    for name in ("P01", "P02", "P03"):
        rig.core.register(rig.credentials[name], tournament["id"])
    tournament = rig.core.withdraw(rig.credentials["P02"], tournament["id"])
    started = rig.core.start(rig.admin, tournament["id"], tournament["version"])
    assert started["bracket_size"] == 2
    assert {entry["account_id"] for entry in started["entrants"]} == {
        rig.accounts["P01"]["id"],
        rig.accounts["P03"]["id"],
    }


def test_cancel_records_audit_and_stops_mutation(rig):
    tournament = tournament_with_entrants(rig, ["P01", "P02"], capacity=4)
    cancelled = rig.core.cancel(rig.admin, tournament["id"], tournament["version"])
    assert cancelled["status"] == "cancelled"
    with pytest.raises(Exception, match="no longer"):
        rig.core.cancel(rig.admin, tournament["id"], cancelled["version"])
    with pytest.raises(Exception, match="closed"):
        rig.core.register(rig.credentials["P03"], tournament["id"])
    audit = rig.core.audit(rig.admin, tournament["id"])
    assert any(entry["action"] == "cancelled" for entry in audit["items"])


def test_cancel_retires_running_legs_and_blocks_play_settlement_and_timeout(rig):
    names = [*PLAYERS, ADMIN][:4]
    tournament = tournament_with_entrants(rig, names, capacity=4)
    rig.core.start(rig.admin, tournament["id"], tournament["version"])
    matches = ready_matches(rig, tournament["id"])
    assert matches
    games = [leg["game_id"] for match in matches for leg in match["legs"]]
    assert games

    cancelled = rig.core.cancel(
        rig.admin, tournament["id"], rig.core.detail(rig.admin, tournament["id"])["version"]
    )
    assert cancelled["status"] == "cancelled"

    # Every outstanding leg is retired through the authoritative online core.
    for game_id in games:
        assert rig.online.read(rig.admin, game_id)["status"] == "voided"

    # A retired leg can no longer be played or conceded.
    game = rig.online.read(rig.admin, games[0])
    mover = game["participants"][game["position"]["turn"]]["account_id"]
    with pytest.raises(Exception, match="active game"):
        rig.online.mutate(rig.by_id[mover], games[0], game["version"], "concede")

    # A retired leg cannot be settled and its match keeps no result.
    settled = rig.core.settle(rig.admin, tournament["id"])
    assert settled["status"] == "cancelled"
    bracket = rig.core.bracket(rig.admin, tournament["id"])
    target = next(
        match
        for entry in bracket["upper"]
        for match in entry["matches"]
        if match["legs"] and match["legs"][0]["game_id"] == games[0]
    )
    assert target["status"] == "ready"
    assert all(leg["outcome"] is None for leg in target["legs"])

    # Timeout processing cannot create a result for a retired leg.
    rig.clock.value += timedelta(seconds=100000)
    assert rig.online.sweep()["finalized"] == 0
    with rig.store.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM rating_events").fetchone()[0] == 0
        voided_results = c.execute(
            "SELECT COUNT(*) FROM online_results r JOIN online_games g ON g.id=r.game_id WHERE g.status='voided'"
        ).fetchone()[0]
        assert voided_results == len(games)

    audit = rig.core.audit(rig.admin, tournament["id"])
    entry = next(e for e in audit["items"] if e["action"] == "cancelled")
    assert entry["details"] == {"voided_games": len(games)}


def test_cancel_preserves_completed_results_and_voids_only_outstanding_legs(rig):
    names = [*PLAYERS, ADMIN][:4]
    tournament = tournament_with_entrants(rig, names, capacity=4)
    rig.core.start(rig.admin, tournament["id"], tournament["version"])
    matches = ready_matches(rig, tournament["id"])
    assert len(matches) == 2
    finished, outstanding = matches[0], matches[1]
    force_match(rig, finished, finished["participants"]["a"]["account_id"])
    rig.core.settle(rig.admin, tournament["id"])
    completed_game = finished["legs"][0]["game_id"]
    outstanding_game = outstanding["legs"][0]["game_id"]
    assert rig.online.read(rig.admin, completed_game)["status"] == "completed"

    rig.core.cancel(
        rig.admin, tournament["id"], rig.core.detail(rig.admin, tournament["id"])["version"]
    )

    # Completed history survives: the game, its result and its recorded outcome.
    assert rig.online.read(rig.admin, completed_game)["status"] == "completed"
    with rig.store.connection() as c:
        assert (
            c.execute(
                "SELECT COUNT(*) FROM online_results WHERE game_id=?", (completed_game,)
            ).fetchone()[0]
            == 1
        )
        outcome = c.execute(
            "SELECT outcome FROM player_tournament_legs WHERE game_id=?", (completed_game,)
        ).fetchone()[0]
    assert outcome in {"a", "b"}
    # Only the outstanding leg is retired; a single void per leg is recorded.
    assert rig.online.read(rig.admin, outstanding_game)["status"] == "voided"
    with rig.store.connection() as c:
        assert (
            c.execute(
                "SELECT COUNT(*) FROM online_results WHERE game_id=?", (outstanding_game,)
            ).fetchone()[0]
            == 1
        )
        assert c.execute("SELECT COUNT(*) FROM rating_events").fetchone()[0] == 0


def test_create_validates_bounds(rig):
    with pytest.raises(Exception, match="Capacity"):
        rig.core.create(
            rig.admin,
            name="x",
            ruleset="brandubh",
            capacity=1,
            match_mode="sequential",
            time_per_move=600,
        )
    with pytest.raises(Exception, match="exactly two games"):
        rig.core.create(
            rig.admin, name="x", ruleset="brandubh", capacity=4, match_legs=9, time_per_move=600
        )
    with pytest.raises(Exception, match="ruleset"):
        rig.core.create(
            rig.admin,
            name="x",
            ruleset="chess",
            capacity=4,
            match_mode="sequential",
            time_per_move=600,
        )
    with pytest.raises(Exception, match="Time limit"):
        rig.core.create(
            rig.admin,
            name="x",
            ruleset="brandubh",
            capacity=4,
            match_mode="sequential",
            time_per_move=10,
        )
    with pytest.raises(Exception, match="name"):
        rig.core.create(
            rig.admin,
            name="   ",
            ruleset="brandubh",
            capacity=4,
            match_mode="sequential",
            time_per_move=600,
        )


# -- HTTP surface, projections and authorization -------------------------


def test_http_flow_and_public_read_boundaries(rig, client):
    admin, player = client(ADMIN), client("P01")
    response = admin.post(
        "/api/player-tournaments",
        json={"match_mode": "sequential", "name": "HTTP Cup", "ruleset": "brandubh", "capacity": 4},
    )
    assert response.status_code == 201, response.text
    tournament = response.json()
    assert tournament["status"] == "registration" and tournament["version"] == 0

    registered = player.post(f"/api/player-tournaments/{tournament['id']}/register")
    assert registered.status_code == 200 and registered.json()["entrant_count"] == 1
    assert (
        client("P02")
        .post(f"/api/player-tournaments/{tournament['id']}/register")
        .json()["entrant_count"]
        == 2
    )

    listing = player.get("/api/player-tournaments")
    assert listing.status_code == 200
    assert listing.json()["items"][0]["name"] == "HTTP Cup"
    assert listing.json()["next_cursor"] is None

    detail = player.get(f"/api/player-tournaments/{tournament['id']}").json()
    assert {entry["username"] for entry in detail["entrants"]} == {"P01", "P02"}
    assert "email" not in str(detail)

    assert player.get("/api/player-tournaments/missing").status_code == 404
    assert player.get(f"/api/player-tournaments/{tournament['id']}/bracket").status_code == 200
    assert player.get(f"/api/player-tournaments/{tournament['id']}/matches").status_code == 200
    assert player.get(f"/api/player-tournaments/{tournament['id']}/matches/nope").status_code == 404
    assert player.get(f"/api/player-tournaments/{tournament['id']}/audit").status_code == 403

    stale = admin.post(f"/api/player-tournaments/{tournament['id']}/start", json={"version": 0})
    assert stale.status_code == 409
    started = admin.post(
        f"/api/player-tournaments/{tournament['id']}/start", json={"version": detail["version"]}
    )
    assert started.status_code == 200 and started.json()["status"] == "running"
    matches = player.get(f"/api/player-tournaments/{tournament['id']}/matches").json()
    assert matches["items"]
    single = player.get(
        f"/api/player-tournaments/{tournament['id']}/matches/{matches['items'][0]['id']}"
    )
    assert single.status_code == 200 and "participants" in single.json()
    assert player.post(f"/api/player-tournaments/{tournament['id']}/settle").status_code == 200


def test_every_player_tournament_route_requires_authentication_and_permission(rig, client):
    tournament = tournament_with_entrants(rig, ["P01", "P02"], capacity=4)
    started = rig.core.start(rig.admin, tournament["id"], tournament["version"])
    bracket = rig.core.bracket(rig.admin, tournament["id"])
    match = ready_matches(rig, tournament["id"])[0]
    base = f"/api/player-tournaments/{tournament['id']}"
    anonymous = TestClient(rig.app)
    for method, route, body in (
        ("GET", "/api/player-tournaments", None),
        (
            "POST",
            "/api/player-tournaments",
            {"match_mode": "sequential", "name": "x", "ruleset": "brandubh", "capacity": 4},
        ),
        ("GET", base, None),
        ("GET", base + "/bracket", None),
        ("GET", base + "/matches", None),
        ("GET", base + "/matches/" + match["id"], None),
        ("GET", base + "/audit", None),
        ("POST", base + "/register", None),
        ("POST", base + "/withdraw", None),
        ("POST", base + "/settle", None),
        ("POST", base + "/start", {"version": started["version"]}),
        ("POST", base + "/cancel", {"version": started["version"]}),
        ("POST", base + "/rebuild", {"version": started["version"], "note": "n"}),
        (
            "POST",
            base + "/matches/" + match["id"] + "/override",
            {"version": 1, "winner": "x", "note": "n"},
        ),
    ):
        response = anonymous.request(method, route, json=body)
        assert response.status_code == 401, (method, route, response.status_code)

    player = client("P01")
    for method, route, body in (
        ("GET", base + "/audit", None),
        ("POST", base + "/start", {"version": started["version"]}),
        ("POST", base + "/cancel", {"version": started["version"]}),
        ("POST", base + "/rebuild", {"version": started["version"], "note": "n"}),
        (
            "POST",
            base + "/matches/" + match["id"] + "/override",
            {"version": 1, "winner": "x", "note": "n"},
        ),
    ):
        response = player.request(method, route, json=body)
        assert response.status_code == 403, (method, route, response.status_code)

    assert player.get("/api/player-tournaments").status_code == 200
    assert player.get(base + "/bracket").status_code == 200
    assert bracket["upper"]


def test_player_tournaments_are_separate_from_ai_test_tournaments(rig):
    tournament_with_entrants(rig, ["P01", "P02"], capacity=4)
    with rig.store.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM tournaments").fetchone()[0] == 0


def test_listing_filters_and_cursor_pagination(rig):
    ids = [create(rig, name=f"Cup {index}")["id"] for index in range(4)]
    first = rig.core.listing(rig.admin, limit=2)
    assert len(first["items"]) == 2 and first["next_cursor"]
    second = rig.core.listing(rig.admin, limit=2, cursor=first["next_cursor"])
    assert len(second["items"]) == 2 and second["next_cursor"] is None
    assert {item["id"] for item in first["items"]} & {
        item["id"] for item in second["items"]
    } == set()
    assert {item["id"] for item in first["items"]} | {
        item["id"] for item in second["items"]
    } == set(ids)
    cancelled = rig.core.cancel(rig.admin, ids[0], rig.core.detail(rig.admin, ids[0])["version"])
    assert cancelled["status"] == "cancelled"
    only = rig.core.listing(rig.admin, status="cancelled")
    assert [item["id"] for item in only["items"]] == [ids[0]]
    with pytest.raises(Exception, match="status"):
        rig.core.listing(rig.admin, status="bogus")
    with pytest.raises(Exception, match="cursor"):
        rig.core.listing(rig.admin, cursor="not-a-cursor")


def test_listing_cursor_is_bounded_and_bound_to_its_filter(rig):
    ids = [create(rig, name=f"Cup {index}")["id"] for index in range(3)]
    first = rig.core.listing(rig.admin, limit=2)
    assert len(first["items"]) == 2 and first["next_cursor"]

    # Oversized, empty, non-base64 and malformed payloads are all refused before
    # any query runs.
    for cursor in (
        "x" * 257,
        "",
        "not-a-cursor",
        "!!!",
        raw_cursor([1, 2, 3]),
        raw_cursor({"k": "listing"}),
        raw_cursor({"k": "listing", "s": None, "t": "not-an-integer", "i": ids[0]}),
        raw_cursor({"k": "listing", "s": None, "t": 1, "i": 5}),
        raw_cursor({"k": "listing", "s": None, "t": True, "i": ids[0]}),
        encode_cursor({"k": "listing", "s": "cancelled", "t": 1, "i": ids[0]}),
    ):
        with pytest.raises(Exception, match="cursor"):
            rig.core.listing(rig.admin, cursor=cursor)

    # A cursor is bound to the filter it was produced under, so it cannot be
    # replayed against a different listing.
    assert rig.core.listing(rig.admin, cursor=first["next_cursor"])["items"]
    with pytest.raises(Exception, match="cursor"):
        rig.core.listing(rig.admin, status="registration", cursor=first["next_cursor"])
    with pytest.raises(Exception, match="cursor"):
        rig.core.listing(rig.admin, status="cancelled", cursor=first["next_cursor"])

    # The final page is explicit and an empty filter keeps the list shape.
    second = rig.core.listing(rig.admin, limit=2, cursor=first["next_cursor"])
    assert len(second["items"]) == 1 and second["next_cursor"] is None
    empty = rig.core.listing(rig.admin, status="complete")
    assert empty["items"] == [] and empty["next_cursor"] is None
    assert rig.core.listing(rig.admin, status="cancelled")["items"] == []


def test_match_cursor_is_bounded_and_bound_to_its_tournament(rig):
    names = [*PLAYERS, ADMIN][:4]
    first = tournament_with_entrants(rig, names, capacity=4)
    second = tournament_with_entrants(rig, names, capacity=4)
    for tournament in (first, second):
        rig.core.start(rig.admin, tournament["id"], tournament["version"])

    page = rig.core.matches(rig.admin, first["id"], limit=1)
    assert len(page["items"]) == 1 and page["next_cursor"]
    assert rig.core.matches(rig.admin, first["id"], limit=1, cursor=page["next_cursor"])["items"]

    for cursor in (
        "x" * 257,
        "",
        raw_cursor({"k": "matches", "t": first["id"], "i": 5}),
        raw_cursor({"k": "matches", "t": 5, "i": page["items"][0]["id"]}),
        raw_cursor({"k": "listing", "s": None, "t": 1, "i": first["id"]}),
    ):
        with pytest.raises(Exception, match="cursor"):
            rig.core.matches(rig.admin, first["id"], cursor=cursor)

    # A match cursor names one tournament, so it cannot be replayed on another.
    with pytest.raises(Exception, match="cursor"):
        rig.core.matches(rig.admin, second["id"], cursor=page["next_cursor"])

    seen, cursor = [], None
    while True:
        page = rig.core.matches(rig.admin, first["id"], limit=2, cursor=cursor)
        seen.extend(item["id"] for item in page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert len(seen) == len(set(seen))
    assert len(seen) == sum(rig.core.detail(rig.admin, first["id"])["match_counts"].values())


def test_match_listing_batches_tournament_and_legs_for_large_pages(rig, monkeypatch):
    from hnefatafl import storage

    cup = create(rig, name="Large listing", capacity=64, match_legs=2)
    seat_a = rig.accounts[PLAYERS[0]]["id"]
    seat_b = rig.accounts[PLAYERS[1]]["id"]
    with rig.store.connection() as c:
        for index in range(200):
            match_id = f"large-match-{index:03d}"
            c.execute(
                """INSERT INTO player_tournament_matches(id,tournament_id,bracket,round,slot,status,
                participant_a,participant_b,source_a,source_b,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    match_id,
                    cup["id"],
                    "upper",
                    index + 1,
                    0,
                    "pending",
                    seat_a,
                    seat_b,
                    "seed:1",
                    "seed:2",
                    1,
                ),
            )
            c.execute(
                """INSERT INTO player_tournament_legs(match_id,leg,game_id,attackers_id,defenders_id,created_at)
                VALUES(?,?,?,?,?,?)""",
                (match_id, 1, f"large-game-{index:03d}", seat_a, seat_b, 1),
            )

    statements: list[str] = []
    original_connect = storage.sqlite3.connect

    def traced_connect(*args, **kwargs):
        connection = original_connect(*args, **kwargs)
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(storage.sqlite3, "connect", traced_connect)

    statements.clear()
    first = rig.core.matches(rig.admin, cup["id"], limit=1)
    single_count = len(statements)
    statements.clear()
    page = rig.core.matches(rig.admin, cup["id"], limit=200)
    batch_count = len(statements)

    # One shared tournament/leg read serves the whole page instead of two per match.
    assert len(first["items"]) == 1
    assert len(page["items"]) == 200 and page["next_cursor"] is None
    assert page["items"][0]["legs"][0]["game_id"] == "large-game-000"
    assert page["items"][-1]["legs"][0]["game_id"] == "large-game-199"
    assert batch_count == single_count
    assert batch_count <= 12


def test_listing_pagination_is_stable_while_tournaments_change(rig):
    known = {create(rig, name=f"Cup {index}")["id"] for index in range(4)}
    seen, cursor, pages = [], None, 0
    while True:
        page = rig.core.listing(rig.admin, limit=2, cursor=cursor)
        seen.extend(item["id"] for item in page["items"])
        pages += 1
        cursor = page["next_cursor"]
        if cursor is None:
            break
        assert pages < 20
        # A tournament created between two page reads never shifts the cursor or
        # repeats a row: the keyset walks strictly down (created_at, id).
        known.add(create(rig, name=f"Late {pages}")["id"])
    assert pages > 1
    assert len(seen) == len(set(seen))
    assert set(seen) <= known


def test_legs_link_authoritative_online_games_and_never_rate_them(rig):
    names = [*PLAYERS, ADMIN][:4]
    tournament = tournament_with_entrants(rig, names, capacity=4)
    rig.core.start(rig.admin, tournament["id"], tournament["version"])
    match = ready_matches(rig, tournament["id"])[0]
    game = rig.online.read(rig.admin, match["legs"][0]["game_id"])
    assert game["status"] == "active" and game["rated"] is False
    with rig.store.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM rating_events").fetchone()[0] == 0
        linked = c.execute(
            "SELECT COUNT(*) FROM player_tournament_legs l JOIN online_games g ON g.id=l.game_id"
        ).fetchone()[0]
        assert linked == len(ready_matches(rig, tournament["id"]))


def test_durable_worker_settles_without_browser_and_consumers_are_idempotent(rig):
    tournament = tournament_with_entrants(rig, PLAYERS[:4], capacity=4, match_legs=2)
    rig.core.start(rig.admin, tournament["id"], tournament["version"])
    matches = ready_matches(rig, tournament["id"])
    for match in matches:
        force_match(rig, match, match["participants"]["a"]["account_id"])
    with rig.store.connection() as c:
        assert (
            c.execute("SELECT COUNT(*) FROM player_tournament_settlement_queue").fetchone()[0] == 1
        )
    from hnefatafl.player_tournament_settlement import TournamentSettlement

    # A recreated process sees the committed outbox; no participant API is used.
    workers = [TournamentSettlement(rig.core), TournamentSettlement(rig.core)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(lambda worker: worker.drain(), workers)) == 1
    assert workers[0].drain() == 0
    bracket = rig.core.bracket(rig.admin, tournament["id"])
    assert all(m["status"] == "complete" for r in bracket["upper"][:1] for m in r["matches"])
    with rig.store.connection() as c:
        assert (
            c.execute("SELECT COUNT(*) FROM player_tournament_settlement_queue").fetchone()[0] == 0
        )
        assert (
            c.execute(
                "SELECT COUNT(*) FROM player_tournament_audit WHERE action='automatic_settlement' AND actor_id IS NULL"
            ).fetchone()[0]
            == 1
        )


def test_worker_recovers_missing_outbox_and_timeout_results(rig):
    tournament = tournament_with_entrants(rig, PLAYERS[:3], capacity=4)
    rig.core.start(rig.admin, tournament["id"], tournament["version"])
    rig.clock.value += timedelta(seconds=601)
    assert rig.online.sweep()["finalized"] == 1
    with rig.store.connection() as c:
        c.execute("DELETE FROM player_tournament_settlement_queue")
    worker = rig.app.state.tournament_settlement
    worker.recover()
    worker.recover()
    assert worker.drain() == 1
    assert worker.drain() == 0
    bracket = rig.core.bracket(rig.admin, tournament["id"])
    assert any(m["status"] == "complete" for r in bracket["upper"] for m in r["matches"])


def test_worker_revision_reopens_completed_tournament_and_freezes(rig):
    tournament = tournament_with_entrants(rig, PLAYERS[:4], capacity=4)
    finished = play_out(rig, tournament["id"])
    assert finished["status"] == "complete"
    worker = rig.app.state.tournament_settlement
    worker.drain()
    game_id = rig.core.bracket(rig.admin, tournament["id"])["final"]["legs"][0]["game_id"]
    game = rig.online.read(rig.admin, game_id)
    rig.online.mutate(rig.admin, game_id, game["version"], "void", note="late correction")
    assert worker.drain() == 1
    reopened = rig.core.detail(rig.admin, tournament["id"])
    assert reopened["review_state"] == "disputed"
    assert reopened["status"] == "running"
    assert reopened["official_winner"] is None
    assert worker.drain() == 0


def test_worker_failed_transaction_is_retained_with_bounded_retry(rig, monkeypatch):
    tournament = tournament_with_entrants(rig, PLAYERS[:4], capacity=4)
    rig.core.start(rig.admin, tournament["id"], tournament["version"])
    match = ready_matches(rig, tournament["id"])[0]
    force_match(rig, match, match["participants"]["a"]["account_id"])
    advance = rig.core._advance

    def fail(*args):
        raise RuntimeError("private payload must not be persisted")

    monkeypatch.setattr(rig.core, "_advance", fail)
    worker = rig.app.state.tournament_settlement
    assert worker.drain() == 0
    with rig.store.connection() as c:
        row = c.execute("SELECT * FROM player_tournament_settlement_queue").fetchone()
        assert row["attempts"] == 1 and row["last_error"] == "settlement_failed"
    assert worker.drain() == 0
    monkeypatch.setattr(rig.core, "_advance", advance)
    rig.clock.value += timedelta(seconds=3)
    assert worker.drain() == 1


def test_planned_start_filters_and_cursor_scope(rig):
    def planned(name, ruleset):
        return rig.core.create(
            rig.admin,
            name=name,
            ruleset=ruleset,
            capacity=4,
            match_mode="sequential",
            time_per_move=600,
            scheduled_start_at=rig.clock.value + timedelta(days=1),
        )

    planned("First", "brandubh")
    planned("Second", "brandubh")
    planned("Other", "tablut")
    create(rig, name="Unscheduled")
    page = rig.core.listing(rig.admin, ruleset="brandubh", phase="upcoming", limit=1)
    assert len(page["items"]) == 1
    assert page["items"][0]["scheduled_start_at"] is not None
    assert page["next_cursor"]
    second = rig.core.listing(
        rig.admin, ruleset="brandubh", phase="upcoming", limit=1, cursor=page["next_cursor"]
    )
    assert len(second["items"]) == 1
    assert second["items"][0]["id"] != page["items"][0]["id"]
    with pytest.raises(Exception, match="cursor"):
        rig.core.listing(rig.admin, ruleset="tablut", phase="upcoming", cursor=page["next_cursor"])
    with pytest.raises(Exception, match="timezone"):
        rig.core.create(
            rig.admin,
            name="Bad",
            ruleset="brandubh",
            capacity=4,
            match_mode="sequential",
            time_per_move=600,
            scheduled_start_at=datetime(2027, 1, 2),
        )
    with pytest.raises(Exception, match="Registration"):
        rig.core.create(
            rig.admin,
            name="Bad",
            ruleset="brandubh",
            capacity=4,
            match_mode="sequential",
            time_per_move=600,
            registration_deadline=rig.clock.value + timedelta(days=2),
            scheduled_start_at=rig.clock.value + timedelta(days=1),
        )
    # These cups are not full: due scheduling leaves them in registration.
    rig.clock.value += timedelta(days=2)
    assert rig.core.listing(rig.admin, phase="upcoming")["items"] == []
    assert all(item["status"] == "registration" for item in rig.core.listing(rig.admin)["items"])


def test_restore_retains_live_tournament_outbox_and_authoritative_results(rig, tmp_path):
    import sqlite3

    from hnefatafl.online_migrations import retain_online_on_restore

    snapshot = tmp_path / "before-results.sqlite3"
    with rig.store.connection() as source, sqlite3.connect(snapshot) as target:
        source.backup(target)
    tournament = tournament_with_entrants(rig, PLAYERS[:4], capacity=4)
    rig.core.start(rig.admin, tournament["id"], tournament["version"])
    match = ready_matches(rig, tournament["id"])[0]
    force_match(rig, match, match["participants"]["a"]["account_id"])
    with rig.store.connection() as source, sqlite3.connect(snapshot) as target:
        target.row_factory = sqlite3.Row
        retain_online_on_restore(source, target)
        assert (
            target.execute(
                "SELECT tournament_id FROM player_tournament_settlement_queue"
            ).fetchone()[0]
            == tournament["id"]
        )
        assert target.execute("SELECT COUNT(*) FROM online_results").fetchone()[0] > 0
        assert not target.execute("PRAGMA foreign_key_check").fetchall()


def test_background_worker_advances_with_no_client_polling(rig, monkeypatch):
    import threading

    tournament = tournament_with_entrants(rig, PLAYERS[:4], capacity=4)
    rig.core.start(rig.admin, tournament["id"], tournament["version"])
    match = ready_matches(rig, tournament["id"])[0]
    worker = rig.app.state.tournament_settlement
    committed = threading.Event()
    drain = worker.drain

    def observe():
        count = drain()
        if count:
            committed.set()
        return count

    monkeypatch.setattr(worker, "drain", observe)
    worker.start()
    try:
        force_match(rig, match, match["participants"]["a"]["account_id"])
        assert committed.wait(5), "server worker did not settle committed result"
    finally:
        worker.shutdown()
    assert worker.thread is not None and not worker.thread.is_alive()
    bracket = rig.core.bracket(rig.admin, tournament["id"])
    decided = next(m for r in bracket["upper"] for m in r["matches"] if m["id"] == match["id"])
    assert decided["status"] == "complete"


def test_automatic_progress_invalidates_reviewed_admin_version(rig):
    tournament = tournament_with_entrants(rig, PLAYERS[:4], capacity=4)
    reviewed = rig.core.start(rig.admin, tournament["id"], tournament["version"])
    match = ready_matches(rig, tournament["id"])[0]
    force_match(rig, match, match["participants"]["a"]["account_id"])
    assert rig.app.state.tournament_settlement.drain() == 1
    updated = rig.core.detail(rig.admin, tournament["id"])
    assert updated["version"] > reviewed["version"]
    with pytest.raises(Exception, match="changed"):
        rig.core.cancel(rig.admin, tournament["id"], reviewed["version"])
    stable = rig.core.settle(rig.admin, tournament["id"])
    assert stable["version"] == updated["version"]


def scheduled_cup(rig, *, capacity=2, creator="P01", **kwargs):
    return rig.core.create(
        rig.credentials[creator],
        name="Scheduled cup",
        ruleset="brandubh",
        capacity=capacity,
        match_mode="sequential",
        time_per_move=600,
        scheduled_start_at=rig.clock.value + timedelta(minutes=1),
        **kwargs,
    )


@pytest.mark.parametrize("full_when", ["before", "at", "after"])
def test_scheduled_starts_without_a_browser_and_only_when_full(rig, full_when):
    cup = scheduled_cup(rig)
    identifier = cup["id"]
    rig.core.register(rig.credentials["P01"], identifier)
    worker = rig.app.state.tournament_settlement
    if full_when == "before":
        rig.core.register(rig.credentials["P02"], identifier)
    assert worker.start_due() == 0
    assert rig.core.detail(rig.admin, identifier)["status"] == "registration"
    rig.clock.value += timedelta(minutes=1)
    if full_when == "after":
        assert worker.start_due() == 0
        with rig.store.connection() as c:
            assert (
                c.execute(
                    "SELECT start_due_at FROM player_tournaments WHERE id=?", (identifier,)
                ).fetchone()[0]
                is None
            )
    if full_when != "before":
        rig.core.register(rig.credentials["P02"], identifier)
    assert worker.start_due() == 1
    started = rig.core.detail(rig.admin, identifier)
    assert started["status"] == "running" and started["entrant_count"] == 2
    assert {e["seed"] for e in started["entrants"]} == {1, 2}
    counts = table_counts(rig)
    assert worker.start_due() == 0 and counts == table_counts(rig)
    audit = rig.core.audit(rig.admin, identifier)["items"]
    event = next(e for e in audit if e["action"] == "started")
    assert event["actor_id"] is None and event["details"]["source"] == "automatic"


@pytest.mark.parametrize("creator", ["P01", ADMIN])
def test_unfilled_due_tournament_can_be_started_by_creator_or_admin(rig, creator):
    cup = scheduled_cup(rig, capacity=4)
    for name in PLAYERS[:2]:
        cup = rig.core.register(rig.credentials[name], cup["id"])
    rig.clock.value += timedelta(minutes=2)
    assert rig.app.state.tournament_settlement.start_due() == 0
    assert table_counts(rig)["games"] == 0
    started = rig.core.start(rig.credentials[creator], cup["id"], cup["version"])
    assert started["status"] == "running"
    event = next(
        e for e in rig.core.audit(rig.admin, cup["id"])["items"] if e["action"] == "started"
    )
    assert event["details"]["source"] == ("administrator" if creator == ADMIN else "creator")


@pytest.mark.parametrize("entrants", [0, 1])
def test_neither_manual_nor_scheduled_start_accepts_fewer_than_two(rig, entrants):
    cup = scheduled_cup(rig)
    for name in PLAYERS[:entrants]:
        cup = rig.core.register(rig.credentials[name], cup["id"])
    rig.clock.value += timedelta(minutes=2)
    assert rig.app.state.tournament_settlement.start_due() == 0
    for credential in [rig.admin, rig.credentials["P01"]]:
        with pytest.raises(Exception, match="two registered"):
            rig.core.start(credential, cup["id"], cup["version"])
    assert table_counts(rig) == {"matches": 0, "legs": 0, "games": 0}


@pytest.mark.parametrize("restart_after_due", [True, False])
def test_start_queue_survives_restart_and_downtime(rig, restart_after_due):
    cup = scheduled_cup(rig)
    for name in PLAYERS[:2]:
        rig.core.register(rig.credentials[name], cup["id"])
    if restart_after_due:
        rig.clock.value += timedelta(days=2)
    app = create_app(
        rig.store.path.parent,
        online_settings=POLICY,
        online_clock=rig.clock,
        online_background=False,
        account_background=False,
    )
    worker = app.state.tournament_settlement
    worker.recover()
    if not restart_after_due:
        assert worker.start_due() == 0
        rig.clock.value += timedelta(minutes=1)
    assert worker.start_due() == 1
    assert rig.core.detail(rig.admin, cup["id"])["status"] == "running"


def test_scheduled_start_failure_rolls_back_and_retries_without_duplicate_games(
    rig, monkeypatch, caplog
):
    cup = scheduled_cup(rig)
    for name in PLAYERS[:2]:
        rig.core.register(rig.credentials[name], cup["id"])
    rig.clock.value += timedelta(minutes=2)
    original = rig.online.seat_game

    def failed(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("private error payload")

    monkeypatch.setattr(rig.online, "seat_game", failed)
    worker = rig.app.state.tournament_settlement
    assert worker.start_due() == 0
    assert table_counts(rig) == {"matches": 0, "legs": 0, "games": 0}
    with rig.store.connection() as c:
        row = c.execute("SELECT * FROM player_tournaments WHERE id=?", (cup["id"],)).fetchone()
        assert row["start_attempts"] == 1 and row["start_error"] == "start_failed"
    assert "private error payload" not in caplog.text
    monkeypatch.setattr(rig.online, "seat_game", original)
    assert worker.start_due() == 0
    rig.clock.value += timedelta(seconds=3)
    assert worker.start_due() == 1
    assert table_counts(rig) == {"matches": 2, "legs": 1, "games": 1}


def test_concurrent_creator_admin_and_scheduled_starts_commit_exactly_once(rig):
    from hnefatafl.online_core import OnlineError

    cup = scheduled_cup(rig)
    for name in PLAYERS[:2]:
        cup = rig.core.register(rig.credentials[name], cup["id"])
    rig.clock.value += timedelta(minutes=2)

    def start(credential):
        try:
            return rig.core.start(credential, cup["id"], cup["version"])["status"]
        except OnlineError as error:
            assert error.status == 409
            return "stale"

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [
            pool.submit(start, credential) for credential in [rig.admin, rig.credentials["P01"]]
        ]
        futures += [pool.submit(rig.app.state.tournament_settlement.start_due) for _ in range(2)]
        for future in futures:
            future.result()
    assert table_counts(rig) == {"matches": 2, "legs": 1, "games": 1}
    assert (
        len([e for e in rig.core.audit(rig.admin, cup["id"])["items"] if e["action"] == "started"])
        == 1
    )


def test_withdrawal_at_due_time_defers_start_until_capacity_is_restored(rig):
    cup = scheduled_cup(rig)
    for name in PLAYERS[:2]:
        cup = rig.core.register(rig.credentials[name], cup["id"])
    rig.clock.value += timedelta(minutes=1)
    rig.core.withdraw(rig.credentials["P02"], cup["id"])
    assert rig.app.state.tournament_settlement.start_due() == 0
    rig.core.register(rig.credentials["P03"], cup["id"])
    assert rig.app.state.tournament_settlement.start_due() == 1
    assert {e["account_id"] for e in rig.core.detail(rig.admin, cup["id"])["entrants"]} == {
        rig.accounts[n]["id"] for n in ["P01", "P03"]
    }


def test_creator_http_capabilities_ownership_eligibility_and_admin_boundaries(rig, client):
    creator, other = client("P01"), client("P02")
    cup = creator.post(
        "/api/player-tournaments", json={"match_mode": "sequential", "name": "Owned", "capacity": 4}
    ).json()
    assert cup["created_by"] == rig.accounts["P01"]["id"]
    assert cup["capabilities"] == {"can_create": True, "can_start": True, "can_moderate": False}
    base = f"/api/player-tournaments/{cup['id']}"
    assert other.get(base).json()["capabilities"]["can_start"] is False
    for name in PLAYERS[:2]:
        cup = client(name).post(base + "/register").json()
    assert other.post(base + "/start", json={"version": cup["version"]}).status_code == 403
    for endpoint in ["cancel", "rebuild"]:
        assert (
            creator.post(
                base + "/" + endpoint,
                json={
                    "version": cup["version"],
                    **({"note": "n"} if endpoint == "rebuild" else {}),
                },
            ).status_code
            == 403
        )
    assert creator.get(base + "/audit").status_code == 403
    assert creator.post(base + "/start", json={"version": cup["version"]}).status_code == 200
    assert (
        client(UNVERIFIED)
        .post(
            "/api/player-tournaments",
            json={"match_mode": "sequential", "name": "Denied", "capacity": 4},
        )
        .status_code
        == 403
    )
    with rig.store.connection() as c:
        assert (
            c.execute("SELECT role FROM accounts WHERE id=?", (cup["created_by"],)).fetchone()[0]
            == "USER"
        )


def test_persisted_creation_limits_cannot_be_bypassed_by_cancellation(rig, client):
    creator = client("P01")
    body = {"match_mode": "sequential", "name": "Quota", "capacity": 4}
    cups = [creator.post("/api/player-tournaments", json=body).json() for _ in range(3)]
    assert creator.post("/api/player-tournaments", json=body).status_code == 429
    for cup in cups:
        rig.core.cancel(rig.admin, cup["id"], cup["version"])
    for _ in range(2):
        cup = creator.post("/api/player-tournaments", json=body).json()
        rig.core.cancel(rig.admin, cup["id"], cup["version"])
    assert creator.post("/api/player-tournaments", json=body).status_code == 429
    assert creator.get("/api/player-tournaments").json()["capabilities"]["can_create"] is False
    rig.clock.value += timedelta(days=1, seconds=1)
    assert creator.post("/api/player-tournaments", json=body).status_code == 201


@pytest.mark.parametrize("name", ["\u202eevil", "bad\x00name", "bad\nname", "   "])
def test_tournament_name_rejects_controls_and_empty_names(rig, client, name):
    assert (
        client("P01")
        .post(
            "/api/player-tournaments",
            json={"match_mode": "sequential", "name": name, "capacity": 4},
        )
        .status_code
        == 422
    )


def test_registration_versions_are_atomic_and_reject_stale_custom_seeds(rig, client):
    cup = scheduled_cup(rig, capacity=4)
    first = rig.core.register(rig.credentials["P01"], cup["id"])
    assert first["version"] == 1
    assert rig.core.register(rig.credentials["P01"], cup["id"])["version"] == 1
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda n: rig.core.register(rig.credentials[n], cup["id"]), ["P02", "P03"]))
    reviewed = rig.core.detail(rig.admin, cup["id"])
    assert reviewed["version"] == 3
    rig.core.withdraw(rig.credentials["P03"], cup["id"])
    response = client("P01").post(
        f"/api/player-tournaments/{cup['id']}/start",
        json={
            "version": reviewed["version"],
            "seeds": [e["account_id"] for e in reviewed["entrants"]],
        },
    )
    assert response.status_code == 409
    assert rig.core.detail(rig.admin, cup["id"])["version"] == 4


@pytest.mark.parametrize(
    "when", ["2027-03-28T03:30:00+02:00", "2027-10-31T02:30:00+02:00", "2027-10-31T02:30:00+01:00"]
)
def test_schedule_uses_utc_across_dst_boundaries(rig, client, when):
    response = client("P01").post(
        "/api/player-tournaments",
        json={"match_mode": "sequential", "name": "DST", "capacity": 2, "scheduled_start_at": when},
    )
    assert response.status_code == 201
    cup = response.json()
    instant = datetime.fromisoformat(when).astimezone(timezone.utc)
    assert datetime.fromisoformat(cup["scheduled_start_at"]) == instant
    for name in PLAYERS[:2]:
        rig.core.register(rig.credentials[name], cup["id"])
    rig.clock.value = instant - timedelta(microseconds=1)
    assert rig.app.state.tournament_settlement.start_due() == 0
    rig.clock.value = instant
    assert rig.app.state.tournament_settlement.start_due() == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("scheduled_start_at", "2026-01-01T00:00:00Z"),
        ("registration_deadline", "2026-01-01T00:00:00Z"),
        ("scheduled_start_at", "2028-01-01T00:00:00"),
    ],
)
def test_schedule_rejects_past_and_naive_dates(rig, client, field, value):
    assert (
        client("P01")
        .post(
            "/api/player-tournaments",
            json={"match_mode": "sequential", "name": "Bad", "capacity": 2, field: value},
        )
        .status_code
        == 422
    )


@pytest.mark.parametrize("mode", ["single", "sequential", "simultaneous"])
@pytest.mark.parametrize("clock_mode,increment", [("per_move", 0), ("total", 0), ("total", 10)])
def test_supported_and_historical_modes_share_online_clocks_and_sequential_recovery(
    rig, mode, clock_mode, increment
):
    cup = rig.core.create(
        rig.credentials["P01"],
        name="Modes",
        ruleset="brandubh",
        capacity=2,
        match_mode="sequential" if mode == "single" else mode,
        time_per_move=600,
        time_control=clock_mode,
        increment_seconds=increment,
    )
    if mode == "single":
        # A pre-policy stored single tournament, not a creation API escape hatch.
        with rig.store.connection() as c:
            c.execute(
                "UPDATE player_tournaments SET match_mode='single',match_legs=1 WHERE id=?",
                (cup["id"],),
            )
    for name in PLAYERS[:2]:
        cup = rig.core.register(rig.credentials[name], cup["id"])
    rig.core.start(rig.credentials["P01"], cup["id"], cup["version"])
    match = ready_matches(rig, cup["id"])[0]
    assert len(match["legs"]) == (2 if mode == "simultaneous" else 1)
    first = rig.online.read(rig.admin, match["legs"][0]["game_id"])
    assert first["time_control"] == clock_mode and first["increment_seconds"] == increment
    assert first["rated"] is False
    rig.clock.value += timedelta(seconds=5)
    move = safe_move(first)
    rig.online.mutate(
        rig.by_id[first["participants"]["attackers"]["account_id"]],
        first["id"],
        first["version"],
        "move",
        source=move.source,
        target=move.target,
    )
    first = rig.online.read(rig.admin, first["id"])
    if clock_mode == "total":
        with rig.store.connection() as c:
            assert (
                c.execute(
                    "SELECT attackers_remaining_us FROM online_games WHERE id=?", (first["id"],)
                ).fetchone()[0]
                == (595 + increment) * 1000000
            )
    force_match(rig, {**match, "legs": match["legs"][:1]}, match["participants"]["a"]["account_id"])
    from hnefatafl.player_tournament_settlement import TournamentSettlement

    worker = TournamentSettlement(rig.core)  # Restart after game 1, before advancement.
    worker.recover()
    assert worker.drain() == 1
    current = rig.core.match(rig.admin, cup["id"], match["id"])
    if mode != "single":
        assert current["status"] == "ready" and len(current["legs"]) == 2
        second = rig.online.read(rig.admin, current["legs"][1]["game_id"])
        assert (
            second["status"] == "active"
            and second["participants"]["attackers"]["account_id"]
            == first["participants"]["defenders"]["account_id"]
        )
        if mode == "sequential":
            assert datetime.fromisoformat(second["started_at"]) == rig.clock.value
        force_match(
            rig,
            {**current, "legs": current["legs"][1:]},
            current["participants"]["a"]["account_id"],
        )
        assert worker.drain() == 1
    assert rig.core.match(rig.admin, cup["id"], match["id"])["status"] == "complete"
    assert_no_duplicate_matches_or_legs(rig)
    with rig.store.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM rating_events").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM achievement_awards").fetchone()[0] == 0


def test_ordinary_settle_requests_do_not_acquire_write_lock_or_traverse(rig, monkeypatch):
    cup = tournament_with_entrants(rig, PLAYERS[:2], capacity=2)
    rig.core.start(rig.admin, cup["id"], cup["version"])

    def unexpected(*args, **kwargs):
        raise AssertionError("A public retry must remain read-only")

    monkeypatch.setattr(rig.core, "_write", unexpected)
    monkeypatch.setattr(rig.core, "_advance", unexpected)
    for _ in range(10):
        assert rig.core.settle(rig.credentials["P01"], cup["id"])["status"] == "running"


@pytest.mark.parametrize("mode", ["single", "sequential", "simultaneous"])
def test_supported_and_historical_modes_preserve_byes_two_losses_and_grand_final_reset(rig, mode):
    cup = rig.core.create(
        rig.admin,
        name="New bracket",
        ruleset="brandubh",
        capacity=5,
        match_mode="sequential" if mode == "single" else mode,
        time_per_move=600,
    )
    if mode == "single":
        with rig.store.connection() as c:
            c.execute(
                "UPDATE player_tournaments SET match_mode='single',match_legs=1 WHERE id=?",
                (cup["id"],),
            )
    for name in PLAYERS[:5]:
        cup = rig.core.register(rig.credentials[name], cup["id"])
    rig.core.start(rig.admin, cup["id"], cup["version"])
    saw_reset = False
    for _ in range(60):
        cup = rig.core.detail(rig.admin, cup["id"])
        if cup["status"] == "complete":
            break
        for match in ready_matches(rig, cup["id"]):
            saw_reset |= match["bracket"] == "reset"
            winner = match["participants"]["b" if match["bracket"] == "grand_final" else "a"][
                "account_id"
            ]
            active = [leg for leg in match["legs"] if leg["game_status"] == "active"]
            assert active
            force_match(rig, {**match, "legs": active}, winner)
        rig.app.state.tournament_settlement.drain()
    assert cup["status"] == "complete" and saw_reset
    assert_double_elimination(rig, cup["id"])
    assert_no_duplicate_matches_or_legs(rig)


@pytest.mark.parametrize("action", ["cancel", "override", "rebuild"])
@pytest.mark.parametrize("between_games", [False, True])
def test_sequential_moderation_does_not_leave_orphaned_games(rig, action, between_games):
    cup = rig.core.create(
        rig.admin,
        name="Moderated",
        ruleset="brandubh",
        capacity=2,
        match_mode="sequential",
        time_per_move=600,
    )
    for name in PLAYERS[:2]:
        cup = rig.core.register(rig.credentials[name], cup["id"])
    rig.core.start(rig.admin, cup["id"], cup["version"])
    match = ready_matches(rig, cup["id"])[0]
    if between_games:
        force_match(rig, match, match["participants"]["a"]["account_id"])
        rig.app.state.tournament_settlement.drain()
    cup = rig.core.detail(rig.admin, cup["id"])
    if action == "cancel":
        rig.core.cancel(rig.admin, cup["id"], cup["version"])
    elif action == "override":
        rig.core.override(
            rig.admin,
            cup["id"],
            match["id"],
            cup["version"],
            match["participants"]["b"]["account_id"],
            "Reviewed",
        )
    else:
        rig.core.rebuild(rig.admin, cup["id"], cup["version"], "Re-derive sequential series")
    rig.app.state.tournament_settlement.drain()
    assert_no_duplicate_matches_or_legs(rig)
    with rig.store.connection() as c:
        assert (
            c.execute("""SELECT COUNT(*) FROM online_games g
        WHERE g.status='active' AND NOT EXISTS (SELECT 1 FROM player_tournament_legs l
        JOIN player_tournament_matches m ON m.id=l.match_id
        JOIN player_tournaments t ON t.id=m.tournament_id
        WHERE l.game_id=g.id AND m.status='ready' AND t.status='running')""").fetchone()[0]
            == 0
        )


def test_disabled_entrant_defers_scheduled_start_until_reactivation(rig):
    cup = scheduled_cup(rig)
    for name in PLAYERS[:2]:
        rig.core.register(rig.credentials[name], cup["id"])
    with rig.store.connection() as c:
        c.execute("UPDATE accounts SET active=0 WHERE id=?", (rig.accounts["P02"]["id"],))
    rig.clock.value += timedelta(minutes=1)
    worker = rig.app.state.tournament_settlement
    assert worker.start_due() == 0
    assert table_counts(rig)["games"] == 0
    with rig.store.connection() as c:
        c.execute("UPDATE accounts SET active=1 WHERE id=?", (rig.accounts["P02"]["id"],))
    rig.clock.value += timedelta(seconds=3)
    assert worker.start_due() == 1


@pytest.mark.parametrize("mode", ["per_move", "total"])
def test_tournament_timeout_uses_online_worker_and_settles_sequential_leg(rig, mode):
    cup = rig.core.create(
        rig.admin,
        name="Timeout",
        ruleset="brandubh",
        capacity=2,
        match_mode="sequential",
        time_per_move=60,
        time_control=mode,
        increment_seconds=10 if mode == "total" else 0,
    )
    for name in PLAYERS[:2]:
        cup = rig.core.register(rig.credentials[name], cup["id"])
    rig.core.start(rig.admin, cup["id"], cup["version"])
    first = ready_matches(rig, cup["id"])[0]
    rig.clock.value += timedelta(seconds=61)
    rig.online.sweep()
    assert rig.app.state.tournament_settlement.drain() == 1
    current = rig.core.match(rig.admin, cup["id"], first["id"])
    assert current["status"] == "ready" and len(current["legs"]) == 2
    assert current["legs"][0]["outcome"] == "b"
    assert current["legs"][1]["game_status"] == "active"


@pytest.mark.parametrize("legs", [1, 2, 3, 4, 5])
def test_schema19_legacy_tournaments_keep_every_game_and_leg_after_upgrade(rig, legs):
    from hnefatafl.storage import Store

    cup = tournament_with_entrants(rig, PLAYERS[:2], capacity=2, match_legs=legs)
    rig.core.start(rig.admin, cup["id"], cup["version"])
    before = rig.core.matches(rig.admin, cup["id"])["items"]
    with rig.store.connection() as c:
        c.execute("DROP INDEX player_tournaments_start_due")
        for column in [
            "match_mode",
            "time_control",
            "increment_seconds",
            "start_due_at",
            "start_attempts",
            "start_error",
        ]:
            c.execute(f"ALTER TABLE player_tournaments DROP COLUMN {column}")
        c.execute("PRAGMA user_version=19")
    Store(rig.store.path)
    upgraded = rig.core.detail(rig.admin, cup["id"])
    assert upgraded["match_mode"] == "legacy" and upgraded["match_legs"] == legs
    assert upgraded["time_control"] == "per_move" and upgraded["increment_seconds"] == 0
    assert rig.core.matches(rig.admin, cup["id"])["items"] == before
    assert table_counts(rig)["games"] == legs
    with rig.store.connection() as c:
        assert not c.execute("PRAGMA foreign_key_check").fetchall()
        assert c.execute("PRAGMA user_version").fetchone()[0] == 21


def test_worker_starts_return_clock_after_acquiring_the_write_lock(rig, monkeypatch):
    from contextlib import contextmanager

    cup = rig.core.create(
        rig.admin,
        name="Fresh clock",
        ruleset="brandubh",
        capacity=2,
        match_mode="sequential",
        time_per_move=60,
        time_control="total",
    )
    for name in PLAYERS[:2]:
        cup = rig.core.register(rig.credentials[name], cup["id"])
    rig.core.start(rig.admin, cup["id"], cup["version"])
    match = ready_matches(rig, cup["id"])[0]
    force_match(rig, match, match["participants"]["a"]["account_id"])
    original_write = rig.core._write

    @contextmanager
    def delayed_write():
        with original_write() as c:
            rig.clock.value += timedelta(seconds=7)
            yield c

    monkeypatch.setattr(rig.core, "_write", delayed_write)
    assert rig.app.state.tournament_settlement.drain() == 1
    returned = rig.core.match(rig.admin, cup["id"], match["id"])["legs"][1]
    game = rig.online.read(rig.admin, returned["game_id"])
    assert datetime.fromisoformat(game["started_at"]) == rig.clock.value
    assert datetime.fromisoformat(game["deadline"]) == rig.clock.value + timedelta(seconds=60)


@pytest.mark.parametrize("mode", ["sequential", "simultaneous"])
def test_new_tournaments_explicitly_require_two_side_swapped_games(rig, client, mode):
    response = client("P01").post(
        "/api/player-tournaments", json={"name": "Paired cup", "capacity": 2, "match_mode": mode}
    )
    assert response.status_code == 201
    cup = response.json()
    assert cup["match_mode"] == mode and cup["match_legs"] == 2
    for name in PLAYERS[:2]:
        cup = rig.core.register(rig.credentials[name], cup["id"])
    rig.core.start(rig.credentials["P01"], cup["id"], cup["version"])
    match = ready_matches(rig, cup["id"])[0]
    first = rig.online.read(rig.admin, match["legs"][0]["game_id"])
    force_match(rig, {**match, "legs": match["legs"][:1]}, match["participants"]["a"]["account_id"])
    rig.app.state.tournament_settlement.drain()
    current = rig.core.match(rig.admin, cup["id"], match["id"])
    assert current["status"] == "ready" and len(current["legs"]) == 2
    second = rig.online.read(rig.admin, current["legs"][1]["game_id"])
    assert second["status"] == "active"
    assert (
        second["participants"]["attackers"]["account_id"]
        == first["participants"]["defenders"]["account_id"]
    )
    assert (
        second["participants"]["defenders"]["account_id"]
        == first["participants"]["attackers"]["account_id"]
    )


@pytest.mark.parametrize(
    "extra",
    [
        {},
        {"match_mode": None},
        {"match_mode": "single"},
        {"match_mode": "legacy"},
        {"match_legs": 1},
        {"match_legs": 2},
        {"match_legs": 5},
        {"match_mode": "sequential", "match_legs": 1},
        {"match_mode": "simultaneous", "match_legs": 2},
        {"match_mode": "sequential", "match_legs": None},
    ],
)
def test_new_creation_rejects_missing_single_and_legacy_bypasses(rig, client, extra):
    response = client("P01").post(
        "/api/player-tournaments", json={"name": "Invalid mode", "capacity": 2, **extra}
    )
    assert response.status_code == 422
    with rig.store.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM player_tournaments").fetchone()[0] == 0
    core_fields = {
        key: value for key, value in extra.items() if key in {"match_mode", "match_legs"}
    }
    # None is the core's ordinary absent legacy-argument value, never a bypass.
    if (
        "match_legs" in core_fields
        and core_fields["match_legs"] is None
        and core_fields.get("match_mode") in ("sequential", "simultaneous")
    ):
        cup = rig.core.create(
            rig.admin, name="Explicit paired mode", ruleset="brandubh", capacity=2, **core_fields
        )
        assert cup["match_legs"] == 2 and cup["match_mode"] == "sequential"
        return
    with pytest.raises(Exception, match="exactly two games"):
        rig.core.create(rig.admin, name="Invalid", ruleset="brandubh", capacity=2, **core_fields)


@pytest.mark.parametrize("capacity", [2, 4])
def test_creator_early_start_policy_and_server_capabilities(rig, client, capacity):
    cup = scheduled_cup(rig, capacity=capacity)
    for name in PLAYERS[:2]:
        cup = rig.core.register(rig.credentials[name], cup["id"])
    creator_view = rig.core.detail(rig.credentials["P01"], cup["id"])
    assert creator_view["capabilities"]["can_start"] is (capacity > 2)
    assert rig.core.detail(rig.admin, cup["id"])["capabilities"]["can_start"] is True
    base = f"/api/player-tournaments/{cup['id']}"
    response = client("P01").post(base + "/start", json={"version": cup["version"]})
    if capacity == 2:
        assert response.status_code == 409 and "planned start" in response.json()["detail"]
        assert table_counts(rig) == {"matches": 0, "legs": 0, "games": 0}
        response = client(ADMIN).post(base + "/start", json={"version": cup["version"]})
        assert response.status_code == 200
        event = next(
            e for e in rig.core.audit(rig.admin, cup["id"])["items"] if e["action"] == "started"
        )
        assert event["details"]["source"] == "administrator"
    else:
        assert response.status_code == 200
    assert response.json()["status"] == "running"


def test_concurrent_early_creator_and_admin_override_start_once(rig):
    from hnefatafl.online_core import OnlineError

    cup = scheduled_cup(rig)
    for name in PLAYERS[:2]:
        cup = rig.core.register(rig.credentials[name], cup["id"])

    def start(credential):
        try:
            return rig.core.start(credential, cup["id"], cup["version"])["status"]
        except OnlineError as error:
            assert error.status == 409
            return "conflict"

    with ThreadPoolExecutor(max_workers=3) as pool:
        creator = pool.submit(start, rig.credentials["P01"])
        admin = pool.submit(start, rig.admin)
        automatic = pool.submit(rig.app.state.tournament_settlement.start_due)
        assert creator.result() == "conflict"
        assert admin.result() == "running"
        assert automatic.result() == 0
    assert table_counts(rig) == {"matches": 2, "legs": 1, "games": 1}
    assert (
        len([e for e in rig.core.audit(rig.admin, cup["id"])["items"] if e["action"] == "started"])
        == 1
    )


def test_actual_bootstrap_admin_can_create_without_verified_email(tmp_path, monkeypatch):
    import io
    from hnefatafl.accounts import main as account_cli

    password = "bootstrap-fixture-only-password"
    monkeypatch.setattr(
        "sys.stdin",
        io.StringIO(
            json.dumps(
                {"username": "Bootstrap", "email": "bootstrap@example.test", "password": password}
            )
        ),
    )
    assert account_cli(["init-admin", "--data-dir", str(tmp_path), "--credentials-stdin"]) == 0
    app = create_app(
        tmp_path, online_settings=POLICY, online_background=False, account_background=False
    )
    with TestClient(app) as browser:
        login = browser.post(
            "/api/auth/login",
            json={"identity": "Bootstrap", "password": password},
            headers={"Origin": "http://testserver"},
        )
        assert login.status_code == 200
        session = login.json()
        assert session["account"]["email_verified"] is False
        browser.headers.update(
            {"Origin": "http://testserver", "X-CSRF-Token": session["csrf_token"]}
        )
        response = browser.post(
            "/api/player-tournaments",
            json={"name": "Bootstrap cup", "capacity": 2, "match_mode": "sequential"},
        )
        assert response.status_code == 201
        cup = response.json()
        assert cup["capabilities"]["can_create"] and cup["capabilities"]["can_moderate"]
        assert browser.get(f"/api/player-tournaments/{cup['id']}/audit").status_code == 200
        assert (
            browser.post(
                f"/api/player-tournaments/{cup['id']}/cancel", json={"version": cup["version"]}
            ).status_code
            == 200
        )
        with app.state.store.connection() as c:
            c.execute("UPDATE accounts SET role='USER' WHERE id=?", (session["account"]["id"],))
        assert (
            browser.post(
                "/api/player-tournaments",
                json={"name": "Unverified user", "capacity": 2, "match_mode": "sequential"},
            ).status_code
            == 403
        )
        with app.state.store.connection() as c:
            c.execute(
                "UPDATE accounts SET role='ADMIN',active=0 WHERE id=?", (session["account"]["id"],)
            )
        assert (
            browser.post(
                "/api/player-tournaments",
                json={"name": "Disabled", "capacity": 2, "match_mode": "sequential"},
            ).status_code
            == 401
        )


def tournament_counts(rig):
    with rig.store.connection() as c:
        return {
            table: c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "online_games",
                "online_results",
                "player_tournament_matches",
                "player_tournament_legs",
                "player_tournament_audit",
                "player_tournament_settlement_queue",
                "rating_events",
            )
        }


@pytest.mark.parametrize("populated", [False, True])
def test_startup_survives_exclusive_deployment_gate_and_recovers(background_rig, populated):
    rig = background_rig
    tournament = None
    if populated:
        tournament = tournament_with_entrants(rig, PLAYERS[:4], capacity=4, match_legs=2)
        rig.core.start(rig.admin, tournament["id"], tournament["version"])
        match = ready_matches(rig, tournament["id"])[0]
        force_match(rig, match, match["participants"]["a"]["account_id"])
        # Simulate a schema-20 snapshot or a pre-worker terminal result whose
        # durable reconciliation outbox was never written.
        with rig.store.connection() as c:
            c.execute("DELETE FROM player_tournament_settlement_queue")
    worker = rig.app.state.tournament_settlement
    before = tournament_counts(rig)
    gate = rig.auth.gate_path.open("rb")
    fcntl.flock(gate, fcntl.LOCK_EX | fcntl.LOCK_NB)
    client = authenticated_client(rig)
    try:
        with client:
            assert client.get("/api/health").json() == {"status": "ok"}
            assert client.get("/api/auth/me").status_code == 200
            session = client.cookies.pop(COOKIE)
            try:
                assert client.get("/api/rulesets").status_code == 401
            finally:
                client.cookies.set(COOKIE, session)
            assert client.post("/api/games", json={}).status_code == 503
            assert (
                client.post(
                    "/api/player-tournaments",
                    json={"name": "Deployment cup", "capacity": 2, "match_mode": "sequential"},
                ).status_code
                == 503
            )
            # The startup attempt must not write while the updater holds the
            # gate, and background workers must not bypass it either.
            assert not worker.recovered
            assert worker.start_due() == 0
            assert worker.drain() == 0
            assert tournament_counts(rig) == before
            if tournament is not None:
                with rig.store.connection() as c:
                    assert (
                        c.execute(
                            "SELECT COUNT(*) FROM player_tournament_audit WHERE action='automatic_settlement'"
                        ).fetchone()[0]
                        == 0
                    )
            fcntl.flock(gate, fcntl.LOCK_UN)
            assert wait_until(lambda: worker.recovered), "deferred recovery did not run"
            if tournament is not None:
                assert wait_until(lambda: _automatic_settlements(rig) == 1), (
                    "deferred settlement did not complete"
                )
                settled = tournament_counts(rig)
                # A repeated reconciliation/consumption is idempotent: no
                # duplicate games, results or automatic audit events.
                assert worker.recover() is True
                assert worker.drain() == 0
                assert tournament_counts(rig) == settled
                bracket = rig.core.bracket(rig.admin, tournament["id"])
                assert any(
                    m["status"] == "complete" for r in bracket["upper"] for m in r["matches"]
                )
            assert client.post("/api/games", json={}).status_code == 201
    finally:
        gate.close()


def _automatic_settlements(rig):
    with rig.store.connection() as c:
        return c.execute(
            "SELECT COUNT(*) FROM player_tournament_audit WHERE action='automatic_settlement'"
        ).fetchone()[0]


def test_recovery_defers_only_for_deployment_lock_and_restart_is_idempotent(rig):
    from hnefatafl.player_tournament_settlement import TournamentSettlement

    worker = TournamentSettlement(rig.core)
    gate = rig.auth.gate_path.open("rb")
    fcntl.flock(gate, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        assert worker.recover() is False
        assert worker.recover() is False
        assert not worker.recovered
        # A process restart before the lock is released still defers.
        restarted = TournamentSettlement(rig.core)
        assert restarted.recover() is False
        assert not restarted.recovered
    finally:
        fcntl.flock(gate, fcntl.LOCK_UN)
        gate.close()
    assert worker.recover() is True and worker.recovered
    assert restarted.recover() is True and restarted.recovered


def test_recovery_propagates_unexpected_errors_and_does_not_mark_complete(rig, monkeypatch):
    from hnefatafl.player_tournament_settlement import TournamentSettlement

    worker = TournamentSettlement(rig.core)

    def broken():
        raise sqlite3.OperationalError("private database payload")

    monkeypatch.setattr(worker.core, "_write", broken)
    with pytest.raises(sqlite3.OperationalError, match="private database payload"):
        worker.recover()
    assert not worker.recovered
    # The worker retry path records no completion and backs off instead of
    # spinning on the same failure.
    assert worker._recover_if_needed() is False
    assert not worker.recovered


def test_shutdown_while_recovery_is_deferred_stops_the_worker(rig, monkeypatch):
    from hnefatafl.player_tournament_settlement import TournamentSettlement

    worker = TournamentSettlement(rig.core)
    attempted = threading.Event()
    original = worker.recover

    def observe():
        attempted.set()
        return original()

    monkeypatch.setattr(worker, "recover", observe)
    gate = rig.auth.gate_path.open("rb")
    fcntl.flock(gate, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        worker.start()
        assert attempted.wait(5), "worker did not attempt deferred recovery"
        worker.shutdown()
        assert worker.thread is not None and not worker.thread.is_alive()
        assert not worker.recovered
    finally:
        fcntl.flock(gate, fcntl.LOCK_UN)
        gate.close()


def test_due_scheduled_start_stays_blocked_during_deployment(rig):
    from hnefatafl.player_tournament_settlement import TournamentSettlement

    tournament = rig.core.create(
        rig.admin,
        name="Scheduled cup",
        ruleset="brandubh",
        capacity=4,
        match_mode="sequential",
        time_per_move=600,
        scheduled_start_at=rig.clock.value + timedelta(minutes=1),
    )
    for name in PLAYERS[:4]:
        rig.core.register(rig.credentials[name], tournament["id"])
    rig.clock.value += timedelta(minutes=2)
    worker = TournamentSettlement(rig.core)
    gate = rig.auth.gate_path.open("rb")
    fcntl.flock(gate, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        assert worker.start_due() == 0
        assert rig.core.detail(rig.admin, tournament["id"])["status"] == "registration"
    finally:
        fcntl.flock(gate, fcntl.LOCK_UN)
        gate.close()
    assert worker.start_due() == 1
    assert rig.core.detail(rig.admin, tournament["id"])["status"] == "running"
