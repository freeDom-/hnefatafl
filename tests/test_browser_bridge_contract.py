"""Production browser bridge: explicit algorithm and bounded settings contract."""

import json

import pytest

from hnefatafl import browser_bridge
from hnefatafl.engine import new_game


def _dispatch(payload):
    return json.loads(browser_bridge.dispatch(json.dumps(payload)))


def test_explicit_algorithm_is_required_and_baseline_is_rejected():
    with pytest.raises(ValueError, match="explicit analysis algorithm"):
        browser_bridge.analysis_config({})
    with pytest.raises(ValueError, match="alpha_beta or classical mcts"):
        browser_bridge.analysis_config({"algorithm": "baseline"})
    with pytest.raises(ValueError, match="alpha_beta or classical mcts"):
        browser_bridge.analysis_config({"algorithm": "neural"})
    assert browser_bridge.analysis_config({"algorithm": "alpha_beta"})["algorithm"] == "alpha_beta"


def test_classical_budgets_are_bounded_consistently():
    for config in (
        {"algorithm": "alpha_beta", "depth": 6},
        {"algorithm": "alpha_beta", "time_limit": 3.5},
        {"algorithm": "alpha_beta", "time_limit": 0},
    ):
        with pytest.raises(ValueError):
            browser_bridge.analysis_config(config)
    accepted = browser_bridge.analysis_config(
        {"algorithm": "alpha_beta", "depth": 5, "time_limit": 3.0}
    )
    assert accepted["depth"] == 5 and accepted["time_limit"] == 3.0


def test_nested_search_configuration_is_strictly_validated():
    for search in ({"unknown": True}, {"transposition": "yes"}, {"table_capacity": 0}):
        with pytest.raises(ValueError):
            browser_bridge.analysis_config({"algorithm": "alpha_beta", "search": search})
    accepted = browser_bridge.analysis_config(
        {"algorithm": "alpha_beta", "search": {"table_capacity": 1024}}
    )
    assert accepted["search"]["table_capacity"] == 1024


def test_nonzero_temperature_and_unknown_fields_are_rejected():
    assert (
        browser_bridge.analysis_config({"algorithm": "alpha_beta", "temperature": 0})["temperature"]
        == 0
    )
    with pytest.raises(ValueError, match="temperature 0"):
        browser_bridge.analysis_config({"algorithm": "alpha_beta", "temperature": 0.5})
    with pytest.raises(ValueError, match="Unsupported local analysis settings"):
        browser_bridge.analysis_config({"algorithm": "alpha_beta", "unknown": 1})


def test_dispatch_analysis_and_illegal_move_contracts():
    state = new_game("brandubh").to_dict()
    result = _dispatch(
        {
            "action": "analysis",
            "state": state,
            "config": {"algorithm": "alpha_beta", "depth": 1, "time_limit": 0.05},
        }
    )
    assert result["ok"] is True
    assert result["event"]["settings"]["algorithm"] == "alpha_beta"
    assert result["event"]["analysis"]["completed_depth"] >= 0
    illegal = _dispatch(
        {
            "action": "move",
            "state": state,
            "move": {"source": 0, "target": 0},
        }
    )
    assert illegal["ok"] is False
    missing = _dispatch({"action": "analysis", "state": state, "config": {}})
    assert missing["ok"] is False
