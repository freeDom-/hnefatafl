"""Reproducible openings, role denominators and real small-network comparisons."""

from copy import deepcopy
import json
import os
import subprocess
import sys
import time

import pytest
from auth_support import AuthenticatedClient as TestClient

from hnefatafl.ai import (
    _finalise_diagnostics,
    _merge_diagnostics,
    _search_diagnostics_summary,
    mcts_context,
    play_game,
    tournament,
)
from hnefatafl.engine import RULESETS, Move, State, apply_move, legal_moves, new_game
from hnefatafl.openings import generate_opening, opening_seed
from hnefatafl.server import create_app
from hnefatafl.symmetry import canonical_board


def assert_performance(stats, outcomes, side):
    wins = outcomes.count(side)
    draws = outcomes.count("draw")
    losses = sum(value is not None and value not in ("draw", side) for value in outcomes)
    completed = wins + losses + draws
    assert stats["games"] == len(outcomes)
    assert (stats["wins"], stats["draws"], stats["losses"], stats["truncated"]) == (
        wins,
        draws,
        losses,
        outcomes.count(None),
    )
    assert stats["completed"] == completed
    assert stats["win_rate"] == (wins / completed if completed else None)
    assert stats["score_rate"] == ((wins + draws / 2) / completed if completed else None)


def test_role_and_battlefield_rates_with_four_participants_and_partial_reports(monkeypatch):
    winners = iter(["attackers", "draw", "defenders", None] * 6)

    def play(ruleset, *_args, **_kwargs):
        winner = next(winners)
        return dict(ruleset=ruleset, winner=winner, plies=1, total_decision_time=0.1)

    monkeypatch.setattr("hnefatafl.ai.play_game", play)
    reports = []
    result = tournament(
        {"players": [{}] * 4, "rulesets": ["brandubh", "tablut"], "games": 2},
        report=lambda row: reports.append(deepcopy(row)),
    )
    assert len(reports) == 24
    for report in reports:
        for stats in report["standings"]:
            seat = stats["player"]
            own = []
            for side, key in (("attackers", "attacker_player"), ("defenders", "defender_player")):
                games = [game for game in report["games"] if game[key] == seat]
                assert_performance(stats["roles"][side], [g["winner"] for g in games], side)
                own.extend(
                    "draw"
                    if g["winner"] == "draw"
                    else None
                    if g["winner"] is None
                    else "win"
                    if g["winner"] == side
                    else "loss"
                    for g in games
                )
            assert_performance(stats, own, "win")
            assert stats["score"] == own.count("win") + own.count("draw") / 2
    for ruleset, field in result["by_ruleset"].items():
        for stats in field["standings"]:
            for side, key in (("attackers", "attacker_player"), ("defenders", "defender_player")):
                outcomes = [
                    g["winner"]
                    for g in result["games"]
                    if g["ruleset"] == ruleset and g[key] == stats["player"]
                ]
                assert_performance(stats["roles"][side], outcomes, side)


@pytest.mark.parametrize("ruleset", list(RULESETS))
def test_openings_are_legal_nonterminal_quiet_and_diverse(ruleset):
    seen = set()
    for seed in range(12):
        opening = generate_opening(ruleset, opening_seed(seed, ruleset, (0, 1), 0), seen)
        state = new_game(ruleset)
        for move in opening.moves:
            assert move in legal_moves(state)
            state = apply_move(state, move)
        assert state == opening.state
        assert not state.winner and legal_moves(state)
        assert state.turn == new_game(ruleset).turn
        assert state.board.count(".") == new_game(ruleset).board.count(".")
        assert len(state.history) == len(opening.moves) + 1
        assert len(set(state.history)) == len(state.history)
        assert opening.unique
        assert 2 <= len(opening.moves) <= 6 and len(opening.moves) % 2 == 0
        assert not any(apply_move(state, move).winner == state.turn for move in legal_moves(state))
    assert len(seen) == 12


def test_opening_reproducibility_across_processes_and_hash_seeds():
    code = (
        "import json; from hnefatafl.openings import generate_opening, opening_seed; "
        "o=generate_opening('brandubh',opening_seed(42,'brandubh',(0,1),0),set()); "
        "print(json.dumps([o.seed,[m.to_dict() for m in o.moves],o.state.to_dict()]))"
    )
    outputs = [
        subprocess.check_output(
            [sys.executable, "-c", code], env={**os.environ, "PYTHONHASHSEED": value}, text=True
        )
        for value in ("1", "999")
    ]
    assert outputs[0] == outputs[1]
    # A versioned golden opening also detects accidental algorithm changes.
    seed, moves, _ = json.loads(outputs[0])
    assert seed == 181010519976277
    assert moves == [
        {"source": 45, "target": 47},
        {"source": 25, "target": 4},
        {"source": 3, "target": 1},
        {"source": 31, "target": 29},
    ]


@pytest.mark.parametrize("count", [2, 3, 4])
def test_every_round_robin_return_game_uses_identical_full_opening(count, monkeypatch):
    beginnings = []

    def play(ruleset, *_args, opening=None, **_kwargs):
        beginnings.append(opening)
        return dict(ruleset=ruleset, winner=None, plies=0, total_decision_time=0)

    monkeypatch.setattr("hnefatafl.ai.play_game", play)
    config = dict(
        players=[{}] * count,
        rulesets=["brandubh", "tablut"],
        games=4,
        seed=42,
        randomize_start_positions=True,
    )
    result = tournament(config)
    unique = set()
    for index in range(0, len(beginnings), 2):
        first, second = beginnings[index : index + 2]
        assert first is second  # Immutable state, including ordered repetition history.
        games = result["games"][index : index + 2]
        assert games[0]["attacker_player"] == games[1]["defender_player"]
        assert games[0]["defender_player"] == games[1]["attacker_player"]
        key = first.state.ruleset, canonical_board(first.state.board)[0]
        assert key not in unique
        unique.add(key)
    repeated = []
    old = beginnings[:]
    beginnings.clear()
    tournament(config)
    repeated.extend(beginnings)
    assert old == repeated


def test_fixed_role_opening_sequence_survives_swapping_independent_agents():
    players = [
        dict(algorithm="baseline", time_limit=0.01),
        dict(algorithm="mcts", iterations=1, rollout_depth=0, time_limit=0.01),
    ]
    config = dict(
        mode="fixed_roles",
        players=players,
        rulesets=["brandubh"],
        games=5,
        max_plies=1,
        seed=42,
        randomize_start_positions=True,
    )
    first = tournament(config)
    swapped = tournament({**config, "players": players[::-1]})
    for original, repeated in zip(first["games"], swapped["games"], strict=True):
        assert original["attacker_player"] == repeated["attacker_player"] == 0
        assert original["defender_player"] == repeated["defender_player"] == 1
        assert original["opening_seed"] == repeated["opening_seed"]
        assert (
            original["moves"][: original["opening_plies"]]
            == repeated["moves"][: repeated["opening_plies"]]
        )
    for index, stats in enumerate(first["standings"]):
        played = "attackers" if index == 0 else "defenders"
        unplayed = "defenders" if index == 0 else "attackers"
        assert stats["roles"][played]["games"] == 5
        assert_performance(stats["roles"][unplayed], [], unplayed)


def test_openings_keep_full_history_and_do_not_count_as_agent_work(monkeypatch):
    opening = generate_opening("brandubh", 123, set())
    seen = []

    def choose(state, config, *, diagnostics, **_kwargs):
        seen.append((state, config))
        diagnostics["simulations"] = config["iterations"]
        return legal_moves(state)[0]

    monkeypatch.setattr("hnefatafl.ai.choose_move", choose)
    result = play_game(
        "brandubh",
        {"iterations": 256, "time_limit": 2},
        {"iterations": 64, "time_limit": 0.1},
        opening=opening,
        max_plies=2,
        seed=42,
    )
    assert seen[0][0] is opening.state
    assert seen[1][0].history[: len(opening.state.history)] == opening.state.history
    assert [(c["iterations"], c["time_limit"], c["seed"]) for _, c in seen] == [
        (256, 2, 42),
        (64, 0.1, 43),
    ]
    assert result["agent_plies"] == 2
    assert result["plies"] == result["opening_plies"] + 2
    assert result["diagnostics"]["attackers"]["decisions"] == 1
    assert result["diagnostics"]["defenders"]["decisions"] == 1
    assert result["diagnostics"]["attackers"]["mean_simulations"] == 256
    assert result["diagnostics"]["defenders"]["mean_simulations"] == 64
    state = new_game("brandubh")
    for move in result["moves"]:
        state = apply_move(state, Move(**move))
    assert state.to_dict() == result["state"]


def test_bounded_generation_deduplication_fallback_and_cancellation(monkeypatch):
    opening = generate_opening("brandubh", 123, set())
    key = canonical_board(opening.state.board)[0]
    monkeypatch.setattr("hnefatafl.openings.MAX_ATTEMPTS", 2)
    monkeypatch.setattr("hnefatafl.openings.canonical_board", lambda _: (key, None))
    duplicate = generate_opening("brandubh", 123, {key})
    assert not duplicate.unique and not duplicate.state.winner
    with pytest.raises(InterruptedError):
        generate_opening("brandubh", 123, set(), lambda: True)
    monkeypatch.setattr("hnefatafl.openings.legal_moves", lambda _: [])
    with pytest.raises(ValueError, match="bounded attempts"):
        generate_opening("brandubh", 123, set())


def wait_completed(client, identifier):
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        response = client.get(f"/api/tournaments/{identifier}")
        assert response.status_code == 200
        record = response.json()
        if record["status"] == "completed":
            return record
        assert record["status"] == "running", record
        time.sleep(0.05)
    pytest.fail("Bounded tournament failed to complete")


def test_real_64_vs_256_checkpoint_benchmark_persistence_and_replay(tmp_path):
    from hnefatafl.models import ModelRegistry, PolicyValueNet, save_checkpoint

    model = ModelRegistry(tmp_path / "models").register(
        save_checkpoint(
            tmp_path / "small.pt", PolicyValueNet("brandubh", 4, 0), {"name": "Benchmark weights"}
        )
    )
    players = [
        dict(
            config=dict(
                algorithm="neural",
                model_id=model["id"],
                iterations=iterations,
                time_limit=15,
                temperature=0,
            )
        )
        for iterations in (64, 256)
    ]
    config = dict(
        name="64 vs 256",
        players=players,
        rulesets=["brandubh"],
        games=20,
        max_plies=2,
        seed=42,
        randomize_start_positions=True,
    )
    with TestClient(create_app(tmp_path)) as client:
        response = client.post("/api/tournaments", json=config)
        assert response.status_code == 201, response.text
        saved = wait_completed(client, response.json()["id"])
        assert [p["config"]["iterations"] for p in saved["config"]["players"]] == [64, 256]
        assert len(saved["result"]["games"]) == 20
        for index, row in enumerate(saved["result"]["standings"]):
            assert row["roles"]["attackers"]["games"] == 10
            assert row["roles"]["defenders"]["games"] == 10
            assert row["diagnostics"]["decisions"] == 20
            assert 0 < row["diagnostics"]["mean_simulations"] <= (64, 256)[index]
            # The effective budget, budget completion and deadline cutoffs are
            # retained as compact aggregates, never as a raw decision stream.
            assert row["diagnostics"]["configured_simulations"] == (64, 256)[index]
            assert row["diagnostics"]["simulation_decisions"] == 20
            assert 0 <= row["diagnostics"]["budget_reached_fraction"] <= 1
            assert row["diagnostics"]["deadline_cutoffs"] >= 0
            assert row["diagnostics"]["immediate_win_shortcuts"] >= 0
        for index in range(0, 20, 2):
            first, second = saved["result"]["games"][index : index + 2]
            assert first["opening_seed"] == second["opening_seed"]
            assert (
                first["moves"][: first["opening_plies"]]
                == second["moves"][: second["opening_plies"]]
            )
        fixed = client.post(
            "/api/tournaments",
            json={**config, "mode": "fixed_roles", "players": players[::-1], "games": 3},
        )
        assert fixed.status_code == 201, fixed.text
        fixed_saved = wait_completed(client, fixed.json()["id"])
        assert [p["config"]["iterations"] for p in fixed_saved["config"]["players"]] == [256, 64]
        assert all(g["attacker_player"] == 0 for g in fixed_saved["result"]["games"])
        exported = client.get(f"/api/tournaments/{fixed_saved['id']}/export").json()
        assert exported["randomize_start_positions"] is True
        assert [p["config"]["iterations"] for p in exported["players"]] == [256, 64]
        # Export and persistence preserve the new diagnostic scalars verbatim.
        exported_diagnostics = exported["results"]["result"]["standings"][0]["diagnostics"]
        assert exported_diagnostics["configured_simulations"] == 256
        assert exported_diagnostics["simulation_decisions"] == 3
    with TestClient(create_app(tmp_path)) as client:
        for record in (saved, fixed_saved):
            assert client.get(f"/api/tournaments/{record['id']}").json() == record
            for index, game in enumerate(record["result"]["games"]):
                replay = client.get(f"/api/tournaments/{record['id']}/games/{index}/replay")
                assert replay.status_code == 200, replay.text
                assert replay.json()["states"][-1] == game["state"]
                assert replay.json()["opening_plies"] == game["opening_plies"]


@pytest.mark.parametrize("value", [1, "true", None, [], {}])
def test_randomization_requires_a_real_boolean(value, tmp_path):
    with pytest.raises(ValueError, match="boolean"):
        tournament({"randomize_start_positions": value})
    with TestClient(create_app(tmp_path)) as client:
        response = client.post(
            "/api/tournaments",
            json=dict(
                name="Invalid toggle",
                players=[{"config": {"algorithm": "baseline"}}] * 2,
                randomize_start_positions=value,
            ),
        )
        assert response.status_code == 422


def test_historical_statistics_are_reconstructed_without_rewriting_saved_results(tmp_path):
    config = dict(
        name="Legacy",
        players=[{"config": {"algorithm": "baseline"}}] * 2,
        rulesets=["brandubh"],
        games=2,
        max_plies=2,
        seed=42,
    )
    with TestClient(create_app(tmp_path)) as client:
        created = client.post("/api/tournaments", json=config)
        assert created.status_code == 201
        saved = wait_completed(client, created.json()["id"])
        original = deepcopy(saved["result"])
        legacy = deepcopy(original)
        for row in legacy["standings"]:
            for key in ("roles", "completed", "win_rate", "score_rate"):
                row.pop(key)
        for row in legacy["by_ruleset"].values():
            row.pop("standings")
        for game in legacy["games"]:
            game.pop("defender_player")
        client.app.state.store.update_tournament(saved["id"], {"result": legacy})
        enriched = client.get(f"/api/tournaments/{saved['id']}").json()["result"]
        assert enriched["standings"] == original["standings"]
        assert enriched["by_ruleset"] == original["by_ruleset"]
        assert client.app.state.store.get_tournament(saved["id"])["result"] == legacy
        exported = client.get(f"/api/tournaments/{saved['id']}/export").json()
        assert exported["results"]["result"]["standings"] == original["standings"]
        legacy["games"] = []
        client.app.state.store.update_tournament(saved["id"], {"result": legacy})
        unavailable = client.get(f"/api/tournaments/{saved['id']}").json()["result"]
        assert "roles" not in unavailable["standings"][0]
        assert unavailable["standings"][0]["win_rate"] is None


def test_disabled_openings_keep_historical_moves_and_cancellation_keeps_only_saved_games():
    config = dict(
        players=[{"algorithm": "baseline"}] * 2,
        rulesets=["brandubh"],
        games=4,
        max_plies=2,
        seed=42,
    )
    old = tournament(config)
    explicit = tournament({**config, "randomize_start_positions": False})
    assert [g["moves"] for g in old["games"]] == [g["moves"] for g in explicit["games"]]
    assert all("opening_seed" not in game for game in old["games"])
    reports = []
    partial = tournament(
        {**config, "randomize_start_positions": True},
        report=lambda r: reports.append(deepcopy(r)),
        should_stop=lambda: bool(reports),
    )
    assert len(partial["games"]) == len(reports) == 1
    assert partial == reports[0]
    assert partial["standings"][0]["roles"]["attackers"]["games"] == 1
    assert partial["standings"][0]["roles"]["defenders"]["games"] == 0


def drive_mcts(state, config, diagnostics):
    """Run the cooperative context to completion with legal uniform priors."""
    context = mcts_context(state, config, diagnostics=diagnostics)
    pending = next(context)
    assert pending is state
    try:
        while True:
            priors = {move: 1.0 for move in legal_moves(pending)}
            pending = context.send((priors, 0.0))
    except StopIteration:
        return


def escaping_state():
    board = ["."] * 49
    board[1] = "K"
    board[24] = "D"
    board[8] = "A"
    board[48] = "A"
    return State("brandubh", tuple(board), turn="defenders")


def test_search_diagnostics_distinguish_budget_deadlines_and_shortcuts():
    records = [
        ({"simulations": 64, "simulation_budget": 64}, 0.01, None),
        ({"simulations": 20, "simulation_budget": 64, "deadline_cutoffs": 1}, 0.02, None),
        ({"simulations": 0, "simulation_budget": 64, "immediate_win_shortcuts": 1}, 0.001, None),
        ({"nodes": 10, "evaluations": 5}, 0.03, 3),
    ]
    summary = _search_diagnostics_summary(records)
    assert summary["decisions"] == 4
    assert summary["configured_simulations"] == 64
    assert summary["simulation_decisions"] == 3
    assert summary["budget_reached_fraction"] == pytest.approx(1 / 3)
    assert summary["deadline_cutoffs"] == 1
    assert summary["immediate_win_shortcuts"] == 1
    # The non-MCTS decision contributes no simulation mean of its own.
    assert summary["mean_simulations"] == pytest.approx((64 + 20 + 0) / 3)

    accumulator: dict = {}
    _merge_diagnostics(accumulator, summary)
    _merge_diagnostics(
        accumulator,
        _search_diagnostics_summary([({"simulations": 256, "simulation_budget": 256}, 0.01, None)]),
    )
    final = _finalise_diagnostics(accumulator)
    assert final["simulation_decisions"] == 4
    assert final["configured_simulations"] == pytest.approx((64 * 3 + 256) / 4)
    assert final["budget_reached_fraction"] == pytest.approx(2 / 4)
    assert final["deadline_cutoffs"] == 1
    assert final["deadline_cutoff_fraction"] == pytest.approx(1 / 4)
    assert final["immediate_win_shortcuts"] == 1


def test_search_diagnostics_omit_mcts_fields_for_non_mcts_decisions():
    summary = _search_diagnostics_summary(
        [
            ({"completed_depth": 3, "nodes": 10}, 0.01, 3),
            ({"completed_depth": 2, "nodes": 5}, 0.02, 2),
        ]
    )
    for key in (
        "simulation_decisions",
        "configured_simulations",
        "budget_reached_fraction",
        "deadline_cutoffs",
        "deadline_cutoff_fraction",
        "immediate_win_shortcuts",
    ):
        assert key not in summary
    accumulator: dict = {}
    _merge_diagnostics(accumulator, summary)
    final = _finalise_diagnostics(accumulator)
    for key in (
        "simulation_decisions",
        "configured_simulations",
        "budget_reached_fraction",
        "deadline_cutoffs",
        "deadline_cutoff_fraction",
        "immediate_win_shortcuts",
    ):
        assert key not in final


def test_mcts_context_records_budget_when_search_completes():
    details: dict = {}
    drive_mcts(
        new_game("brandubh"),
        {"iterations": 5, "time_limit": 300, "seed": 1, "rollout_depth": 1},
        details,
    )
    assert details["simulation_budget"] == 5
    assert details["simulations"] == 5
    assert "deadline_cutoffs" not in details
    assert "immediate_win_shortcuts" not in details


def test_mcts_context_records_a_real_deadline_cutoff(monkeypatch):
    clock = {"calls": 0}

    def monotonic():
        clock["calls"] += 1
        # The first call sets the deadline; every later check is past it.
        return 0.0 if clock["calls"] == 1 else 1e9

    monkeypatch.setattr("hnefatafl.ai.time.monotonic", monotonic)
    details: dict = {}
    drive_mcts(
        new_game("brandubh"),
        {"iterations": 64, "time_limit": 1.0, "seed": 1, "rollout_depth": 1},
        details,
    )
    assert details["simulation_budget"] == 64
    assert details["deadline_cutoffs"] == 1
    assert 0 < details["simulations"] < 64
    assert "immediate_win_shortcuts" not in details


def test_mcts_context_immediate_win_is_not_a_deadline_cutoff():
    state = escaping_state()
    details: dict = {}
    context = mcts_context(state, {"iterations": 64, "time_limit": 300}, diagnostics=details)
    assert next(context) is state
    priors = {move: 1.0 for move in legal_moves(state)}
    with pytest.raises(StopIteration):
        context.send((priors, 0.0))
    assert details["simulation_budget"] == 64
    assert details["simulations"] == 0
    assert details["immediate_win_shortcuts"] == 1
    assert "deadline_cutoffs" not in details


def test_tournament_omits_diagnostics_without_retained_search_metrics(monkeypatch):
    def play(ruleset, *_args, **_kwargs):
        return dict(ruleset=ruleset, winner=None, plies=0, total_decision_time=0)

    monkeypatch.setattr("hnefatafl.ai.play_game", play)
    result = tournament({"players": [{}] * 2, "rulesets": ["brandubh"], "games": 2})
    assert all("diagnostics" not in row for row in result["standings"])
