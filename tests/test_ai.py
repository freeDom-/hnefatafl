import time

import pytest

from hnefatafl.ai import ai_profiles, choose_move, evaluate, mcts_policy, play_game, tournament
from hnefatafl.engine import Move, State, apply_move, legal_moves, new_game, replay


@pytest.mark.parametrize("ruleset", ["brandubh", "tablut", "copenhagen"])
@pytest.mark.parametrize("algorithm", ["baseline", "shield_maiden", "alpha_beta", "mcts"])
def test_every_algorithm_plays_legal_move_for_every_ruleset(ruleset, algorithm):
    state = new_game(ruleset)
    original = state.to_dict()
    move = choose_move(
        state,
        {
            "algorithm": algorithm,
            "depth": 2,
            "iterations": 12,
            "rollout_depth": 2,
            "time_limit": 0.2,
            "seed": 13,
        },
    )
    assert move in legal_moves(state)
    assert state.to_dict() == original
    assert apply_move(state, move).ply == 1


def escaping_state():
    board = ["."] * 49
    board[1] = "K"
    board[24] = "D"
    board[8] = "A"
    board[48] = "A"
    return State("brandubh", tuple(board), turn="defenders")


@pytest.mark.parametrize("algorithm", ["baseline", "shield_maiden", "alpha_beta", "mcts"])
def test_search_takes_immediate_king_escape(algorithm):
    state = escaping_state()
    move = choose_move(state, {"algorithm": algorithm, "depth": 2, "iterations": 2, "seed": 4})
    assert apply_move(state, move).winner == "defenders"


def test_terminal_evaluation_and_rejection():
    state = apply_move(escaping_state(), Move(1, 0))
    assert evaluate(state) == 1
    with pytest.raises(ValueError, match="terminal"):
        choose_move(state)


def test_seeded_mcts_is_reproducible_and_normalized():
    state = new_game("brandubh")
    config = {"iterations": 24, "seed": 24, "rollout_depth": 2, "time_limit": 10}
    first = mcts_policy(state, config)
    assert first == mcts_policy(state, config)
    assert sum(first.values()) == pytest.approx(1)
    assert all(
        move in legal_moves(state) and probability > 0 for move, probability in first.items()
    )


def test_alpha_beta_honors_short_deadline_with_legal_fallback():
    state = new_game("copenhagen")
    started = time.monotonic()
    move = choose_move(state, {"algorithm": "alpha_beta", "depth": 8, "time_limit": 0.01})
    assert move in legal_moves(state)
    assert time.monotonic() - started < 1.0


@pytest.mark.parametrize("ruleset", ["brandubh", "tablut", "copenhagen"])
def test_baselines_finish_real_games_with_replay(ruleset):
    result = play_game(ruleset, seed=3, max_plies=400)
    assert result["outcome"] == "completed"
    assert result["winner"] in ("attackers", "defenders", "draw")
    final = replay(ruleset, [Move(**move) for move in result["moves"]])
    assert final.winner == result["winner"]
    assert final.ply == result["plies"]


@pytest.mark.parametrize("ruleset", ["brandubh", "tablut", "copenhagen"])
@pytest.mark.parametrize("algorithm", ["alpha_beta", "mcts"])
def test_search_opponents_finish_actual_games_in_every_ruleset(ruleset, algorithm):
    result = play_game(
        ruleset,
        {
            "algorithm": algorithm,
            "depth": 2,
            "iterations": 12,
            "rollout_depth": 2,
            "time_limit": 0.05,
        },
        {"algorithm": "baseline"},
        seed=3,
        max_plies=400,
    )
    assert result["outcome"] == "completed"
    assert replay(ruleset, [Move(**move) for move in result["moves"]]).winner == result["winner"]


def test_ply_limit_is_explicitly_truncated_not_draw():
    result = play_game("brandubh", max_plies=1)
    assert result["winner"] is None
    assert result["outcome"] == "truncated"
    assert result["reason"] == "experiment_ply_limit"


def test_balanced_tournament_reports_actual_scores_and_reproducible_moves():
    config = {
        "players": [{"algorithm": "baseline"}, {"algorithm": "alpha_beta", "depth": 1}],
        "rulesets": ["brandubh"],
        "games": 2,
        "seed": 3,
        "max_plies": 100,
    }
    result = tournament(config)
    again = tournament(config)
    assert [game["attacker_player"] for game in result["games"]] == [0, 1]
    assert [game["moves"] for game in result["games"]] == [game["moves"] for game in again["games"]]
    assert sum(result["scores"]) == 2 - result["truncated"]
    assert result["average_game_length"] > 0
    assert result["average_decision_time"] > 0
    assert result["standings"][0]["wins"] == result["standings"][1]["losses"]
    assert result["by_ruleset"]["brandubh"]["games"] == 2
    assert result["average_decision_time"] == pytest.approx(
        sum(game["total_decision_time"] for game in result["games"])
        / sum(game["plies"] for game in result["games"])
    )


@pytest.mark.parametrize(
    "config",
    [
        {"algorithm": "missing"},
        {"algorithm": "neural"},
        {"algorithm": "mcts", "iterations": 0},
        {"algorithm": "alpha_beta", "depth": 0},
        {"time_limit": float("nan")},
    ],
)
def test_invalid_search_parameters_fail(config):
    with pytest.raises(ValueError):
        choose_move(new_game("brandubh"), config)


def test_profiles_cover_all_variants():
    profiles = ai_profiles()
    assert {p["algorithm"] for p in profiles} == {
        "baseline",
        "shield_maiden",
        "alpha_beta",
        "mcts",
        "neural",
    }
    assert all(
        set(p["supported_rulesets"]) == {"brandubh", "tablut", "copenhagen"} for p in profiles
    )
