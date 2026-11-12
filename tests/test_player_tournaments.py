"""Player tournaments: double elimination, authoritative results and authorization.

Tournament games are real authoritative online games, so these tests drive the
online core as a participant would and then settle the bracket from the recorded
results. No client-side bracket arithmetic is trusted.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import shutil
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from hnefatafl.auth import COOKIE, Auth, SecuritySettings, digest
from hnefatafl.engine import State, apply_move, legal_moves
from hnefatafl.online_settings import OnlineSettings
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


def create(rig, *, name="Cup", capacity=16, match_legs=1, ruleset="brandubh", deadline=None):
    body = {
        "name": name,
        "ruleset": ruleset,
        "capacity": capacity,
        "match_legs": match_legs,
        "time_per_move": 600,
    }
    if deadline is not None:
        body["registration_deadline"] = deadline
    return rig.core.create(rig.admin, **body)


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


# -- complete tournaments ------------------------------------------------


@pytest.mark.parametrize("entrants", [4, 5, 8, 16])
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


def test_rebuild_reseats_a_downstream_match_after_a_voided_leg(rig):
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
        deadline=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
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
    rig.core.withdraw(rig.credentials["P02"], tournament["id"])
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


def test_create_validates_bounds(rig):
    with pytest.raises(Exception, match="Capacity"):
        rig.core.create(
            rig.admin, name="x", ruleset="brandubh", capacity=1, match_legs=1, time_per_move=600
        )
    with pytest.raises(Exception, match="Match legs"):
        rig.core.create(
            rig.admin, name="x", ruleset="brandubh", capacity=4, match_legs=9, time_per_move=600
        )
    with pytest.raises(Exception, match="ruleset"):
        rig.core.create(
            rig.admin, name="x", ruleset="chess", capacity=4, match_legs=1, time_per_move=600
        )
    with pytest.raises(Exception, match="time limit"):
        rig.core.create(
            rig.admin, name="x", ruleset="brandubh", capacity=4, match_legs=1, time_per_move=10
        )
    with pytest.raises(Exception, match="name"):
        rig.core.create(
            rig.admin, name="   ", ruleset="brandubh", capacity=4, match_legs=1, time_per_move=600
        )


# -- HTTP surface, projections and authorization -------------------------


def test_http_flow_and_public_read_boundaries(rig, client):
    admin, player = client(ADMIN), client("P01")
    response = admin.post(
        "/api/player-tournaments",
        json={"name": "HTTP Cup", "ruleset": "brandubh", "capacity": 4},
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

    started = admin.post(f"/api/player-tournaments/{tournament['id']}/start", json={"version": 0})
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
        ("POST", "/api/player-tournaments", {"name": "x", "ruleset": "brandubh", "capacity": 4}),
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
        ("POST", "/api/player-tournaments", {"name": "x", "ruleset": "brandubh", "capacity": 4}),
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
