"""Deterministic external evaluator for shared search-driver parity (Level A).

The evaluator is a pure integer/hash function of a position, so native CPython
and Pyodide produce bit-identical legal priors and defender-relative values
without any floating-point transcendental function. Driving the *real*
``mcts_context`` generator with it isolates traversal, RNG consumption, legal
ordering, value perspective, expansion, backpropagation and best-move selection
from inference numerics.

This module is staged into the browser search worker and imported natively by the
parity harness. It never imports PyTorch or a network backend.
"""

from __future__ import annotations

import hashlib
from typing import Any

from hnefatafl.engine import State, legal_moves
from hnefatafl.mcts_driver import (
    NeuralSearch,
    canonical_json,
    validate_model_identity,
    validate_search_settings,
    validate_state,
)

EVALUATOR_VERSION = "deterministic-hash-v1"
# Structural identity: this is not a trained model and no strength is claimed.
EVALUATOR_MODEL_SHA256 = hashlib.sha256(EVALUATOR_VERSION.encode()).hexdigest()


def evaluator_model_identity(state: State) -> dict:
    return {
        "sha256": EVALUATOR_MODEL_SHA256,
        "ruleset": state.ruleset,
        "size": state.size,
    }


def _position_key(state: State) -> str:
    return canonical_json(
        {
            "ruleset": state.ruleset,
            "board": state.board,
            "turn": state.turn,
            "winner": state.winner,
        }
    )


def evaluate(state: State) -> tuple[list[float], float]:
    """Return ``(legal priors, defender value)`` for the requested position.

    Priors use exact weighted integer division; the value uses an integer
    numerator over a power-of-two denominator. Both are correctly rounded IEEE
    754 operations, so the two runtimes agree exactly.
    """
    moves = legal_moves(state)
    key = _position_key(state).encode("utf-8")
    weights = []
    for index in range(len(moves)):
        digest = hashlib.sha256(key + b"|" + str(index).encode("ascii")).digest()
        weights.append(1 + int.from_bytes(digest[:4], "big") % 997)
    total = sum(weights)
    priors = [weight / total for weight in weights]
    defenders = state.board.count("D")
    attackers = state.board.count("A")
    edge_bonus = 0
    if "K" in state.board:
        size = state.size
        king = state.board.index("K")
        row, column = divmod(king, size)
        distance = (
            min(row, column, size - 1 - row, size - 1 - column)
            if state.ruleset == "tablut"
            else min(
                row + column,
                row + size - 1 - column,
                size - 1 - row + column,
                2 * (size - 1) - row - column,
            )
        )
        edge_bonus = max(0, size - 1 - distance) * 2
    numerator = 3 * defenders - 2 * attackers + edge_bonus
    numerator = max(-32, min(32, numerator))
    return priors, numerator / 32.0


def drive(
    state: Any,
    settings: Any,
    model: Any,
    evaluator,
    *,
    trace: bool = True,
) -> dict:
    """Drive one complete search with an arbitrary external evaluator.

    ``evaluator(state)`` must return ``(legal priors, defender value)``. Native
    harnesses use it with a deterministic, PyTorch or ONNX evaluator; the browser
    coordinator supplies responses from the neural worker instead.
    """
    position = state if isinstance(state, State) else validate_state(state)
    configuration = validate_search_settings(settings)
    identity = validate_model_identity(model)
    search = NeuralSearch(position, configuration, identity, trace=trace)
    try:
        event = search.start()
        while event["kind"] == "evaluate":
            requested = validate_state(event["request"]["position"])
            priors, value = evaluator(requested)
            event = search.resume(
                {
                    "request_index": event["request"]["request_index"],
                    "legal": event["request"]["legal"],
                    "priors": priors,
                    "value": value,
                }
            )
        return event
    finally:
        # Release the generator on success, cancellation, an evaluator failure or
        # any unexpected error; close() is idempotent and keeps a final summary.
        search.close()


def run_search(
    state: Any,
    settings: Any,
    *,
    model: Any = None,
    trace: bool = True,
) -> dict:
    """Drive one complete deterministic search and return its final event."""
    position = state if isinstance(state, State) else validate_state(state)
    identity = (
        validate_model_identity(model) if model is not None else evaluator_model_identity(position)
    )
    return drive(position, settings, identity, lambda requested: evaluate(requested), trace=trace)


def workload_identity(
    level: str,
    evaluator: str,
    settings: dict,
    positions: list[dict],
    model_onnx_sha256: str | None,
) -> str:
    """Canonical workload digest shared by the native and browser harnesses."""
    workload = {
        "level": level,
        # Level B identifies the numerical model by its ONNX digest; the native
        # PyTorch/ONNX and browser providers must share one workload identity.
        "evaluator": evaluator if level == "a" else None,
        "settings": {
            key: settings[key] for key in ("iterations", "time_limit", "seed", "exploration")
        },
        "positions": [entry["state"] if "state" in entry else entry for entry in positions],
        "model_onnx_sha256": model_onnx_sha256,
    }
    return hashlib.sha256(canonical_json(workload).encode()).hexdigest()


def parity_payload(event: dict) -> dict:
    """Strip wall-clock fields so two runtimes can be compared exactly."""
    if event["kind"] != "complete":
        return event
    search = dict(event["search"])
    for field in ("elapsed_ms", "hard_deadline", "time_limit", "timing"):
        search.pop(field, None)
    return {"kind": "complete", "analysis": event["analysis"], "search": search}
