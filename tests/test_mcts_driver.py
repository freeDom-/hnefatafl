"""Cooperative neural MCTS driver, bridge protocol and result semantics.

These tests exercise the real ``mcts_context`` generator through the shared
driver, so they cover the exact code both native execution and the browser
search worker run. The deterministic evaluator stands in for inference; it is
not a strength claim.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

SPIKE = Path(__file__).resolve().parents[1] / "spikes/browser_offline"
if str(SPIKE) not in sys.path:
    sys.path.insert(0, str(SPIKE))

import mcts_vectors  # noqa: E402
import search_bridge  # noqa: E402
from hnefatafl.ai import mcts_context  # noqa: E402
from hnefatafl.engine import Move, State, apply_move, legal_moves, new_game  # noqa: E402
from hnefatafl.mcts_driver import (  # noqa: E402
    NeuralSearch,
    SearchProtocolError,
    SearchRequestError,
    neural_input_identity,
    position_identity,
    validate_search_settings,
)

SETTINGS = {"iterations": 48, "time_limit": 10.0, "seed": 7, "exploration": 1.4}


def model_for(state: State) -> dict:
    return {"sha256": "a" * 64, "ruleset": state.ruleset, "size": state.size}


def run_reference(state: State, settings: dict) -> dict:
    diagnostics: dict = {}
    context = mcts_context(
        state,
        settings,
        diagnostics=diagnostics,
        neural=True,
    )
    pending = next(context)
    while True:
        requested = State.from_dict(pending.to_dict())
        priors, value = mcts_vectors.evaluate(requested)
        try:
            pending = context.send((dict(zip(legal_moves(requested), priors)), value))
        except StopIteration as finished:
            return {"diagnostics": diagnostics, "probabilities": finished.value}
    return {"diagnostics": diagnostics, "probabilities": {}}  # pragma: no cover


# ---------------------------------------------------------------- settings


@pytest.mark.parametrize(
    "settings,code",
    [
        ({"iterations": 0}, "SIMULATION_BUDGET"),
        ({"iterations": 4097}, "SIMULATION_BUDGET"),
        ({"iterations": True}, "INVALID_SEARCH_CONFIG"),
        ({"iterations": 12.5}, "INVALID_SEARCH_CONFIG"),
        ({"time_limit": 0.01}, "TIME_BUDGET"),
        ({"time_limit": 11}, "TIME_BUDGET"),
        ({"time_limit": float("nan")}, "INVALID_SEARCH_CONFIG"),
        ({"exploration": -1}, "INVALID_SEARCH_CONFIG"),
        ({"exploration": 11}, "INVALID_SEARCH_CONFIG"),
        ({"seed": -1}, "INVALID_SEARCH_CONFIG"),
        ({"seed": 2**32}, "INVALID_SEARCH_CONFIG"),
        ({"algorithm": "neural"}, "INVALID_SEARCH_CONFIG"),
        ({"dirichlet_fraction": 0.25}, "INVALID_SEARCH_CONFIG"),
        ({"temperature": 1}, "INVALID_SEARCH_CONFIG"),
        ({"model_path": "/tmp/model.pt"}, "INVALID_SEARCH_CONFIG"),
        ({"depth": 3}, "INVALID_SEARCH_CONFIG"),
        ("not-an-object", "INVALID_SEARCH_CONFIG"),
    ],
)
def test_invalid_search_settings_are_rejected_with_stable_codes(settings, code):
    with pytest.raises(SearchRequestError) as error:
        validate_search_settings(settings)
    assert error.value.code == code


def test_valid_settings_are_normalised_without_training_noise():
    settings = validate_search_settings(dict(SETTINGS))
    assert settings["algorithm"] == "mcts"
    assert settings["iterations"] == 48
    assert settings["dirichlet_fraction"] == 0.0
    assert settings["temperature"] == 0.0


# ---------------------------------------------------------------- parity


def test_driver_matches_the_generator_driven_directly():
    state = new_game("brandubh")
    settings = validate_search_settings(SETTINGS)
    event = mcts_vectors.run_search(state.to_dict(), SETTINGS, trace=False)
    reference = run_reference(state, dict(settings))
    search = event["search"]
    assert event["kind"] == "complete"
    assert search["completed_simulations"] == reference["diagnostics"]["simulations"]
    assert search["search_value"] == reference["diagnostics"]["value"]
    assert search["neural_requests"] == reference["diagnostics"]["mcts_neural_requests"]
    assert search["prediction_reuses"] == reference["diagnostics"]["mcts_node_prediction_reuses"]
    ranked = sorted(reference["probabilities"].items(), key=lambda item: (-item[1], item[0]))
    observed = [(row["source"], row["target"], row["share"]) for row in search["root_visits"]]
    assert observed == [(move.source, move.target, share) for move, share in ranked]
    best = ranked[0][0]
    assert event["analysis"]["best_move"] == best.to_dict()


@pytest.mark.parametrize("ruleset", ["brandubh", "tablut", "copenhagen"])
def test_deterministic_search_is_reproducible(ruleset):
    state = new_game(ruleset)
    first = mcts_vectors.parity_payload(
        mcts_vectors.run_search(state.to_dict(), {"iterations": 24, "time_limit": 10, "seed": 3})
    )
    second = mcts_vectors.parity_payload(
        mcts_vectors.run_search(state.to_dict(), {"iterations": 24, "time_limit": 10, "seed": 3})
    )
    assert first == second
    assert first["search"]["completed_simulations"] == 24


# ---------------------------------------------------------------- response contract


@pytest.mark.parametrize(
    "mutation,code",
    [
        ({"request_index": 99}, "REQUEST_MISMATCH"),
        ({"legal": "reversed"}, "LEGAL_ACTIONS_MISMATCH"),
        ({"priors": "short"}, "INVALID_POLICY"),
        ({"prior": -0.1}, "INVALID_POLICY"),
        ({"prior": float("nan")}, "INVALID_POLICY"),
        ({"prior": float("inf")}, "INVALID_POLICY"),
        ({"prior": "0.5"}, "INVALID_POLICY"),
        ({"prior": None}, "INVALID_POLICY"),
        ({"value": 1.5}, "INVALID_VALUE"),
        ({"value": -2}, "INVALID_VALUE"),
        ({"value": float("nan")}, "INVALID_VALUE"),
        ({"value": True}, "INVALID_VALUE"),
        ({"value": "0.2"}, "INVALID_VALUE"),
        ({"priors": "sum_high"}, "INVALID_POLICY"),
        ({"priors": "sum_low"}, "INVALID_POLICY"),
    ],
)
def test_malformed_responses_close_the_search_without_repair(mutation, code):
    state = new_game("brandubh")
    search = NeuralSearch(state, validate_search_settings(SETTINGS), model_for(state))
    request = search.start()["request"]
    priors = [1.0 / len(request["legal"])] * len(request["legal"])
    payload = {
        "request_index": request["request_index"],
        "legal": request["legal"],
        "priors": priors,
        "value": 0.0,
    }
    if "prior" in mutation:
        payload["priors"] = [mutation["prior"]] + priors[1:]
    elif mutation.get("priors") == "short":
        payload["priors"] = priors[:-1]
    elif mutation.get("priors") == "sum_high":
        payload["priors"] = [value * 1.05 for value in priors]
    elif mutation.get("priors") == "sum_low":
        payload["priors"] = [value * 0.95 for value in priors]
    elif mutation.get("legal"):
        payload["legal"] = request["legal"][::-1]
    if "value" in mutation:
        payload["value"] = mutation["value"]
    with pytest.raises(SearchProtocolError) as error:
        search.resume({**payload, **({"request_index": 99} if "request_index" in mutation else {})})
    assert error.value.code == code
    # Malformed responses are not recoverable: the context is closed and the
    # caller must start a new search.
    assert search._closed is True
    with pytest.raises(SearchProtocolError) as closed:
        search.resume(payload)
    assert closed.value.code == "SEARCH_CLOSED"
    search.close()


def test_floating_point_rounding_in_a_valid_policy_is_accepted_unchanged():
    state = new_game("brandubh")
    search = NeuralSearch(state, validate_search_settings(SETTINGS), model_for(state))
    request = search.start()["request"]
    count = len(request["legal"])
    # A float32 legal softmax can sum to 1 within a few ulps.
    priors = [1.0 / count] * (count - 1)
    priors.append(1.0 - sum(priors) + 1e-7)
    total = sum(priors)
    assert total != 1.0 and abs(total - 1.0) <= 1e-3
    event = search.resume(
        {
            "request_index": request["request_index"],
            "legal": request["legal"],
            "priors": priors,
            "value": 0.0,
        }
    )
    assert event["kind"] in ("evaluate", "complete")
    search.close()


def test_failed_search_releases_the_bridge_registry_capacity():
    state = new_game("brandubh")
    model = mcts_vectors.evaluator_model_identity(state)
    for index in range(3):
        search_id = f"fail-{index}"
        start = bridge(
            {
                "action": "start",
                "search_id": search_id,
                "state": state.to_dict(),
                "settings": {"iterations": 8, "time_limit": 10},
                "model": model,
            }
        )
        assert start["ok"] is True
        request = start["event"]["request"]
        bad = bridge(
            {
                "action": "resume",
                "search_id": search_id,
                "request_index": request["request_index"],
                "legal": request["legal"],
                "priors": [1.0] * len(request["legal"]),
                "value": 0.0,
            }
        )
        assert bad["ok"] is False and bad["code"] == "INVALID_POLICY"
        # A failed context never occupies registry capacity or remains active.
        assert bridge({"action": "cancel", "search_id": search_id})["event"]["cancelled"] is False
    started = bridge(
        {
            "action": "start",
            "search_id": "after-failures",
            "state": state.to_dict(),
            "settings": {"iterations": 8, "time_limit": 10},
            "model": model,
        }
    )
    assert started["ok"] is True
    assert bridge({"action": "cancel", "search_id": "after-failures"})["event"]["cancelled"] is True


def test_a_zero_sum_policy_is_rejected_not_renormalised():
    state = new_game("brandubh")
    search = NeuralSearch(state, validate_search_settings(SETTINGS), model_for(state))
    request = search.start()["request"]
    with pytest.raises(SearchProtocolError) as error:
        search.resume(
            {
                "request_index": request["request_index"],
                "legal": request["legal"],
                "priors": [0.0] * len(request["legal"]),
                "value": 0.0,
            }
        )
    assert error.value.code == "INVALID_POLICY"
    search.close()


# ---------------------------------------------------------------- semantics


def test_value_perspective_and_legal_best_move_across_rulesets():
    for ruleset in ("brandubh", "tablut", "copenhagen"):
        state = new_game(ruleset)
        event = mcts_vectors.run_search(state.to_dict(), SETTINGS, trace=False)
        analysis = event["analysis"]
        move = Move(**analysis["best_move"])
        assert move in legal_moves(state)
        assert -1.0 <= analysis["value"] <= 1.0
        assert analysis["source"] == "neural_search"
        assert event["search"]["completed_simulations"] == SETTINGS["iterations"]
        assert math.isclose(sum(row["share"] for row in event["search"]["root_visits"]), 1.0)


def test_immediate_win_shortcut_is_reported_without_claiming_simulations():
    # Defenders to move with an immediate king escape.
    size = 7
    board = ["."] * (size * size)
    board[0 * size + 2] = "K"
    board[4 * size + 4] = "A"
    state = State("brandubh", tuple(board), "defenders")
    event = mcts_vectors.run_search(state.to_dict(), {"iterations": 64, "time_limit": 10})
    assert event["kind"] == "complete"
    assert event["search"]["completion_reason"] == "immediate_win"
    assert event["analysis"]["value"] == 1.0
    # Exactly one neural evaluation is needed to discover the win; the budget is
    # never reported as completed simulations.
    assert event["search"]["completed_simulations"] == 0
    assert event["search"]["neural_requests"] == 1


def test_terminal_positions_never_request_inference():
    escape = State(
        "brandubh",
        tuple("K" if index == 2 else "A" if index == 24 else "." for index in range(49)),
        "defenders",
    )
    terminal = apply_move(escape, Move(2, 0))
    assert terminal.winner == "defenders"
    search = NeuralSearch(terminal, validate_search_settings(SETTINGS), model_for(terminal))
    event = search.start()
    assert event["kind"] == "complete"
    assert event["analysis"] == {"value": 1.0, "source": "terminal", "best_move": None}
    assert event["search"]["neural_requests"] == 0


def test_deadline_reports_actual_completed_work():
    state = new_game("copenhagen")
    event = mcts_vectors.run_search(
        state.to_dict(), {"iterations": 4096, "time_limit": 0.05, "seed": 1}, trace=False
    )
    search = event["search"]
    assert event["kind"] == "complete"
    assert search["completion_reason"] == "deadline"
    assert search["deadline_cutoff"] is True
    assert 0 < search["completed_simulations"] < 4096
    assert event["analysis"]["simulations"] == search["completed_simulations"]


def test_cancellation_never_publishes_analysis():
    state = new_game("tablut")
    search = NeuralSearch(
        state,
        validate_search_settings(SETTINGS),
        model_for(state),
        should_stop=lambda: True,
    )
    event = search.start()
    assert event["kind"] == "cancelled"
    assert "analysis" not in event
    search.close()
    search.close()  # idempotent


def test_search_identity_distinguishes_history_but_not_neural_input():
    fresh = new_game("brandubh")
    repeated = State("brandubh", fresh.board, fresh.turn, history=("a" * 8, "a" * 8))
    settings = validate_search_settings(SETTINGS)
    model = model_for(fresh)
    assert position_identity(fresh, settings, model) != position_identity(repeated, settings, model)
    moves = legal_moves(fresh)
    from hnefatafl.encoding import encode_planes, legal_action_indices

    planes = encode_planes(fresh)
    indices = legal_action_indices(fresh, moves)
    assert neural_input_identity(fresh, planes, indices) != position_identity(
        fresh, settings, model
    )


def test_identity_validation_rejects_unsupported_models():
    with pytest.raises(SearchRequestError) as error:
        NeuralSearch(new_game("brandubh"), validate_search_settings(SETTINGS), None)
    assert error.value.code == "MODEL_IDENTITY_REQUIRED"


# ---------------------------------------------------------------- bridge


def bridge(payload: dict) -> dict:
    return json.loads(search_bridge.dispatch(json.dumps(payload)))


def test_serialized_position_is_materialized_only_when_requested():
    state = new_game("brandubh")
    hidden = NeuralSearch(
        state,
        validate_search_settings(SETTINGS),
        model_for(state),
        include_position=False,
    )
    try:
        event = hidden.start()
        assert "position" not in event["request"]
        assert "position" not in hidden._pending
        assert event["request"]["position_digest"]
    finally:
        hidden.close()
    exposed = NeuralSearch(
        state,
        validate_search_settings(SETTINGS),
        model_for(state),
        include_position=True,
    )
    try:
        event = exposed.start()
        assert event["request"]["position"] == state.to_dict()
        # The serialized copy is an event payload, never retained in pending state.
        assert "position" not in exposed._pending
    finally:
        exposed.close()


def test_bridge_round_trip_and_state_lifecycle():
    state = new_game("brandubh")
    start = bridge(
        {
            "action": "start",
            "search_id": "run-1",
            "state": state.to_dict(),
            "settings": SETTINGS,
            "model": mcts_vectors.evaluator_model_identity(state),
            "trace": True,
        }
    )
    assert start["ok"] is True
    event = start["event"]
    while event["kind"] == "evaluate":
        request = event["request"]
        # The browser coordinator consumes encoded planes and legal association,
        # not a duplicate serialized board/history payload.
        assert "position" not in request
        assert len(request["planes"]) > 0
        priors = [1.0 / len(request["legal"])] * len(request["legal"])
        event = bridge(
            {
                "action": "resume",
                "search_id": "run-1",
                "request_index": request["request_index"],
                "legal": request["legal"],
                "priors": priors,
                "value": 0.0,
            }
        )["event"]
    assert event["kind"] == "complete"
    assert len(event["search"]["trace"]) == event["search"]["neural_requests"]
    # A completed search is released, so a late resume cannot touch it.
    late = bridge(
        {
            "action": "resume",
            "search_id": "run-1",
            "request_index": 0,
            "legal": [],
            "priors": [],
            "value": 0,
        }
    )
    assert late["ok"] is False and late["code"] == "NO_ACTIVE_SEARCH"


def test_bridge_rejects_malformed_and_oversized_requests():
    assert json.loads(search_bridge.dispatch("{"))["code"] == "INVALID_REQUEST"
    assert json.loads(search_bridge.dispatch(123))["code"] == "INVALID_REQUEST"
    assert bridge({"action": "unknown"})["code"] == "UNKNOWN_ACTION"
    huge = json.dumps({"action": "parity", "state": "x" * (600 * 1024)})
    assert json.loads(search_bridge.dispatch(huge))["code"] == "PAYLOAD_TOO_LARGE"
    assert bridge({"action": "cancel", "search_id": "bad id"})["code"] == "INVALID_SEARCH_ID"
    assert bridge({"action": "resume", "search_id": "missing"})["code"] == "NO_ACTIVE_SEARCH"


def test_bridge_cancel_releases_and_is_idempotent():
    state = new_game("brandubh")
    start = bridge(
        {
            "action": "start",
            "search_id": "cancel-1",
            "state": state.to_dict(),
            "settings": SETTINGS,
            "model": mcts_vectors.evaluator_model_identity(state),
        }
    )
    assert start["event"]["kind"] == "evaluate"
    first = bridge({"action": "cancel", "search_id": "cancel-1"})["event"]
    assert first["cancelled"] is True
    second = bridge({"action": "cancel", "search_id": "cancel-1"})["event"]
    assert second["cancelled"] is False
    assert bridge({"action": "close"})["event"]["kind"] == "closed"


def test_bridge_enforces_active_search_limit_and_duplicate_ids():
    state = new_game("brandubh")
    payload = {
        "action": "start",
        "state": state.to_dict(),
        "settings": SETTINGS,
        "model": mcts_vectors.evaluator_model_identity(state),
    }
    assert bridge({**payload, "search_id": "limit-a"})["ok"] is True
    assert bridge({**payload, "search_id": "limit-b"})["ok"] is True
    assert bridge({**payload, "search_id": "limit-c"})["code"] == "SEARCH_BUSY"
    assert bridge({**payload, "search_id": "limit-a"})["code"] == "SEARCH_BUSY"
    bridge({"action": "close"})


def test_bridge_rejects_a_model_that_does_not_match_the_position():
    state = new_game("brandubh")
    start = bridge(
        {
            "action": "start",
            "search_id": "mismatch",
            "state": state.to_dict(),
            "settings": SETTINGS,
            "model": {"sha256": "b" * 64, "ruleset": "tablut", "size": 9},
        }
    )
    assert start["ok"] is False
    assert start["code"] == "MODEL_RULESET_MISMATCH"


def test_bridge_parity_action_returns_a_stripped_deterministic_result():
    state = new_game("tablut")
    result = bridge(
        {
            "action": "parity",
            "state": state.to_dict(),
            "settings": {"iterations": 16, "time_limit": 10, "seed": 2},
            "trace": True,
        }
    )
    assert result["ok"] is True
    event = result["event"]
    assert event["kind"] == "complete"
    assert "elapsed_ms" not in event["search"]
    assert "timing" not in event["search"]
    assert event["search"]["completed_simulations"] == 16


def _brandubh_board(pieces: dict, turn: str) -> State:
    size = 7
    board = ["."] * (size * size)
    for (row, column), piece in pieces.items():
        board[row * size + column] = piece
    return State("brandubh", tuple(board), turn)


def test_immediate_wins_keep_defender_perspective_for_both_sides():
    # Attackers to move: (row 0, col 3) -> (0, 2) sandwiches the king against the
    # restricted corner. An accidental sign inversion would report +1.
    attacker = _brandubh_board({(0, 1): "K", (0, 3): "A", (3, 3): "D", (5, 5): "A"}, "attackers")
    event = mcts_vectors.run_search(attacker.to_dict(), {"iterations": 32, "time_limit": 10})
    assert event["search"]["completion_reason"] == "immediate_win"
    assert event["analysis"]["value"] == -1.0
    assert event["analysis"]["best_move"] == Move(3, 2).to_dict()
    # Defenders to move: the king escapes to the corner for +1.
    defender = _brandubh_board({(0, 2): "K", (4, 4): "A"}, "defenders")
    event = mcts_vectors.run_search(defender.to_dict(), {"iterations": 32, "time_limit": 10})
    assert event["search"]["completion_reason"] == "immediate_win"
    assert event["analysis"]["value"] == 1.0
    assert event["analysis"]["best_move"] == Move(2, 0).to_dict()


@pytest.mark.parametrize("turn", ["attackers", "defenders"])
def test_constant_defender_value_is_backed_up_without_sign_inversion(turn):
    state = State("brandubh", new_game("brandubh").board, turn)
    value = 0.375

    def constant(requested):
        moves = legal_moves(requested)
        return [1.0 / len(moves)] * len(moves), value

    event = mcts_vectors.drive(
        state,
        {"iterations": 24, "time_limit": 10, "seed": 5},
        model_for(state),
        constant,
        trace=False,
    )
    assert event["kind"] == "complete"
    assert event["analysis"]["value"] == pytest.approx(value)
    assert event["search"]["search_value"] == pytest.approx(value)
    assert event["search"]["root_value"] == pytest.approx(value)


def test_drive_releases_the_generator_when_the_evaluator_fails(monkeypatch):
    state = new_game("brandubh")
    captured = []
    factory = mcts_vectors.NeuralSearch

    class Captured(factory):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            captured.append(self)

    monkeypatch.setattr(mcts_vectors, "NeuralSearch", Captured)

    def failing(requested):
        raise RuntimeError("evaluator exploded")

    with pytest.raises(RuntimeError, match="evaluator exploded"):
        mcts_vectors.drive(
            state,
            {"iterations": 8, "time_limit": 10},
            model_for(state),
            failing,
        )
    assert captured and captured[0]._closed is True
    assert captured[0].final_summary["completed_simulations"] == 0
    # The next search is unaffected.
    event = mcts_vectors.run_search(state.to_dict(), {"iterations": 8, "time_limit": 10})
    assert event["kind"] == "complete"


def test_hard_deadline_is_reported_separately_from_cancellation():
    state = new_game("brandubh")
    ticks = {"now": 1000.0, "calls": 0}

    def clock_after_first_request():
        ticks["calls"] += 1
        if ticks["calls"] > 2:
            ticks["now"] += 10.0
        return ticks["now"]

    search = NeuralSearch(
        state,
        validate_search_settings({"iterations": 64, "time_limit": 0.5}),
        model_for(state),
        clock=clock_after_first_request,
    )
    event = search.start()
    assert event["kind"] == "evaluate"
    request = event["request"]
    priors = [1.0 / len(request["legal"])] * len(request["legal"])
    event = search.resume(
        {
            "request_index": request["request_index"],
            "legal": request["legal"],
            "priors": priors,
            "value": 0.0,
        }
    )
    assert event["kind"] == "cancelled"
    assert event["reason"] == "deadline"
    assert event["search"]["completion_reason"] == "deadline"
    assert event["search"]["deadline_cutoff"] is True
    assert event["search"]["completed_simulations"] >= 0
    search.close()
