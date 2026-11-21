"""Cooperative neural MCTS driver shared by native execution and the browser.

The search itself is unchanged: :func:`hnefatafl.ai.mcts_context` owns the tree,
PUCT selection, progressive widening, prediction reuse, backpropagation and best
move selection. This module only adds the bookkeeping an out-of-process
evaluator needs:

* validate an untrusted search request against explicit experimental bounds,
* own one generator and resume it exactly once per pending evaluation,
* describe each pending leaf (encoded planes and legal action indices) together
  with its exact request association,
* validate the returned legal priors and defender-relative value before they can
  influence the tree,
* turn the generator's diagnostics into the established analysis result plus a
  versioned search envelope.

It never imports PyTorch, ONNX Runtime, NumPy or any other heavyweight package,
so it can run inside the browser's Pyodide worker unchanged.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import time
from typing import Any, Callable

from .ai import analysis_from_search, mcts_context
from .encoding import ENCODER_VERSION, encode_planes, legal_action_indices
from .engine import Move, State, legal_moves

DRIVER_VERSION = 1
SEARCH_PROTOCOL = 1
SEARCH_ALGORITHM = "neural-mcts-puct-v1"

# Explicit experimental bounds. These are deliberately tighter than the native
# engine's internal limits: an untrusted browser request never selects them.
MIN_SIMULATIONS = 1
MAX_SIMULATIONS = 4096
MIN_TIME_LIMIT = 0.05
MAX_TIME_LIMIT = 10.0
MAX_EXPLORATION = 10.0
MAX_SEED = 2**32 - 1
# Cooperative hard stop for a single search, including inference waiting. The
# coordinator also enforces a watchdog; this is the in-worker recovery bound.
MIN_HARD_LIMIT = 2.0
MAX_HARD_LIMIT = 30.0
# Legal probabilities must sum to one within this tolerance. Only floating-point
# rounding from a validated softmax is accepted; larger deviations are rejected.
POLICY_SUM_TOLERANCE = 1e-3

SETTING_FIELDS = frozenset({"algorithm", "iterations", "time_limit", "exploration", "seed"})
# Values this driver always forces for analysis. They may only be supplied when
# they already equal the analysis defaults, so validation stays idempotent for
# an already-normalised configuration without allowing training-only behaviour.
ANALYSIS_FIXED = {"dirichlet_fraction": 0.0, "temperature": 0.0, "rollout_depth": 0}
INTEGER_SETTINGS = ("iterations", "seed")


class SearchRequestError(ValueError):
    """An invalid or unsupported search request; carries a stable code."""

    def __init__(self, code: str, message: str, completed_simulations: int = 0):
        super().__init__(message)
        self.code = code
        self.completed_simulations = completed_simulations


class SearchProtocolError(SearchRequestError):
    """The evaluator returned a response that does not match its request.

    Protocol errors are **not** recoverable: the search context is closed so a
    malformed response can never leave a tree waiting for a corrected prediction.
    The completed simulation count is attached for diagnostics.
    """


def _number(raw: dict, name: str) -> float:
    value = raw[name]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SearchRequestError("INVALID_SEARCH_CONFIG", f"{name} must be a number")
    if not math.isfinite(value):
        raise SearchRequestError("INVALID_SEARCH_CONFIG", f"{name} must be finite")
    return float(value)


def validate_search_settings(raw: Any) -> dict:
    """Validate and normalise an experimental neural MCTS configuration."""
    if not isinstance(raw, dict):
        raise SearchRequestError("INVALID_SEARCH_CONFIG", "Search settings must be an object")
    unknown = sorted(set(raw) - SETTING_FIELDS - set(ANALYSIS_FIXED))
    if unknown:
        raise SearchRequestError(
            "INVALID_SEARCH_CONFIG", f"Unsupported search setting(s): {', '.join(unknown)}"
        )
    for name, expected in ANALYSIS_FIXED.items():
        if name in raw:
            value = raw[name]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise SearchRequestError(
                    "INVALID_SEARCH_CONFIG", f"{name} must be the analysis default {expected!r}"
                )
            if float(value) != expected:
                raise SearchRequestError(
                    "INVALID_SEARCH_CONFIG",
                    f"{name} is fixed at {expected!r} for analysis and must not be overridden",
                )
    algorithm = raw.get("algorithm", "mcts")
    if algorithm != "mcts":
        raise SearchRequestError(
            "INVALID_SEARCH_CONFIG",
            "The browser neural search only runs the mcts algorithm with external inference",
        )
    for name in INTEGER_SETTINGS:
        if name in raw and type(raw[name]) is not int:
            raise SearchRequestError("INVALID_SEARCH_CONFIG", f"{name} must be an integer")
    iterations = raw.get("iterations", 128)
    if not MIN_SIMULATIONS <= iterations <= MAX_SIMULATIONS:
        raise SearchRequestError(
            "SIMULATION_BUDGET",
            f"iterations must be between {MIN_SIMULATIONS} and {MAX_SIMULATIONS}",
        )
    seconds = _number({"time_limit": raw.get("time_limit", 2.0)}, "time_limit")
    if not MIN_TIME_LIMIT <= seconds <= MAX_TIME_LIMIT:
        raise SearchRequestError(
            "TIME_BUDGET",
            f"time_limit must be between {MIN_TIME_LIMIT} and {MAX_TIME_LIMIT} seconds",
        )
    exploration = _number({"exploration": raw.get("exploration", 1.4)}, "exploration")
    if not 0 <= exploration <= MAX_EXPLORATION:
        raise SearchRequestError(
            "INVALID_SEARCH_CONFIG", f"exploration must be between 0 and {MAX_EXPLORATION}"
        )
    seed = raw.get("seed", 0)
    if not 0 <= seed <= MAX_SEED:
        raise SearchRequestError("INVALID_SEARCH_CONFIG", f"seed must be between 0 and {MAX_SEED}")
    return {
        "algorithm": "mcts",
        "iterations": int(iterations),
        "time_limit": float(seconds),
        "exploration": float(exploration),
        "seed": int(seed),
        # Analysis never enables training-only Dirichlet noise.
        "dirichlet_fraction": 0.0,
        "temperature": 0.0,
        "rollout_depth": 0,
    }


def hard_deadline_seconds(settings: dict) -> float:
    """Bound one search including inference waiting, independent of the UI."""
    return min(MAX_HARD_LIMIT, max(MIN_HARD_LIMIT, float(settings["time_limit"]) * 2.0))


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def validate_model_identity(raw: Any) -> dict:
    """Require the immutable model identity captured when preparing a search."""
    if not isinstance(raw, dict):
        raise SearchRequestError("MODEL_IDENTITY_REQUIRED", "A model identity is required")
    digest = raw.get("sha256")
    ruleset = raw.get("ruleset")
    size = raw.get("size")
    if not isinstance(digest, str) or len(digest) != 64:
        raise SearchRequestError(
            "MODEL_IDENTITY_INVALID", "Model sha256 must be a 64-character hex digest"
        )
    try:
        int(digest, 16)
    except ValueError:
        raise SearchRequestError(
            "MODEL_IDENTITY_INVALID", "Model sha256 must be a 64-character hex digest"
        ) from None
    if digest != digest.lower():
        raise SearchRequestError(
            "MODEL_IDENTITY_INVALID", "Model sha256 must be lowercase hexadecimal"
        )
    if not isinstance(ruleset, str) or type(size) is not int:
        raise SearchRequestError("MODEL_IDENTITY_INVALID", "Model ruleset and size are required")
    return {"sha256": digest, "ruleset": ruleset, "size": size}


def validate_state(raw: Any) -> State:
    if not isinstance(raw, dict):
        raise SearchRequestError("INVALID_STATE", "Position must be an object")
    try:
        state = State.from_dict(raw)
    except (ValueError, KeyError, TypeError) as error:
        raise SearchRequestError("INVALID_STATE", f"Invalid position: {error}") from None
    return state


def position_identity(state: State, settings: dict, model: dict) -> str:
    """Complete identity of a search result request.

    This covers the full serialized position (including repetition history and
    ply), the effective settings, the model and the search algorithm version. It
    is deliberately not derived from the six-plane encoding, which omits
    repetition history.
    """
    return _sha256(
        canonical_json(
            {
                "protocol": SEARCH_PROTOCOL,
                "algorithm": SEARCH_ALGORITHM,
                "driver": DRIVER_VERSION,
                "encoder": ENCODER_VERSION,
                "state": state.to_dict(),
                "settings": {key: settings[key] for key in sorted(SETTING_FIELDS)},
                "model": model,
            }
        )
    )


def neural_input_identity(state: State, planes: bytes, legal: list[int]) -> str:
    """Identity of the exact raw neural input (not of the search result)."""
    return _sha256(
        canonical_json(
            {
                "encoder": ENCODER_VERSION,
                "ruleset": state.ruleset,
                "size": state.size,
                "planes": base64.b64encode(planes).decode("ascii"),
                "legal": legal,
            }
        )
    )


def _sanitise_provider(raw: Any) -> dict | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise SearchRequestError("INVALID_REQUEST", "provider must be an object")
    allowed = {"requested", "execution_placement", "gpu_execution_verified"}
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise SearchRequestError("INVALID_REQUEST", "Unsupported provider field(s)")
    provider: dict[str, object] = {}
    for key in ("requested", "execution_placement"):
        value = raw.get(key)
        if value is not None:
            if not isinstance(value, str) or len(value) > 64:
                raise SearchRequestError("INVALID_REQUEST", f"Invalid provider {key}")
            provider[key] = value
    flag = raw.get("gpu_execution_verified")
    if flag is not None:
        if not isinstance(flag, bool):
            raise SearchRequestError("INVALID_REQUEST", "gpu_execution_verified must be boolean")
        provider["gpu_execution_verified"] = flag
    return provider


def _move_key(move: Move) -> dict:
    return {"source": move.source, "target": move.target}


class NeuralSearch:
    """One cooperative neural MCTS context with exact request association."""

    def __init__(
        self,
        state: State,
        settings: dict,
        model: dict,
        *,
        provider: dict | None = None,
        trace: bool = False,
        diagnostics: dict | None = None,
        progress: Callable[[dict], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
        clock: Callable[[], float] = time.monotonic,
        include_position: bool = True,
    ) -> None:
        self.state = state
        self.settings = settings
        # Native evaluators need the exact requested state; the browser
        # coordinator only needs the encoded planes and request association, so
        # the Pyodide bridge can omit a potentially large duplicated state.
        self.include_position = include_position
        self.model = validate_model_identity(model)
        if self.model["ruleset"] != state.ruleset or self.model["size"] != state.size:
            raise SearchRequestError(
                "MODEL_RULESET_MISMATCH", "The model identity does not match the position"
            )
        self.provider = provider
        self.trace = trace
        self.diagnostics = diagnostics if diagnostics is not None else {}
        self.position_identity = position_identity(state, settings, model)
        self.requested_simulations = settings["iterations"]
        self.started = clock()
        self.requests = 0
        self._trace: list[dict] = []
        self._pending: dict | None = None
        self._validating = 0.0
        self._latest_progress: dict | None = None
        self._progress_count = 0
        self._dropped_progress = 0
        self._closed = False
        self._context = None
        self._last_event_at = self.started
        self.final_summary = {
            "completed_simulations": 0,
            "neural_requests": 0,
            "prediction_reuses": 0,
            "deadline_hit": False,
        }
        # Mutually exclusive in-worker timings; the coordinator measures the
        # inference and transport wait separately and reports the remainder.
        self.timings = {
            "legal_generation_ms": 0.0,
            "encoding_ms": 0.0,
            "validation_ms": 0.0,
            "requests": 0,
        }
        self._hard_deadline = self.started + hard_deadline_seconds(settings)
        self.deadline_hit = False
        stop = should_stop

        def stop_check() -> bool:
            if clock() >= self._hard_deadline:
                self.deadline_hit = True
                return True
            return bool(stop is not None and stop())

        self._should_stop = stop_check
        self._progress = progress
        self._context = mcts_context(
            state,
            settings,
            diagnostics=self.diagnostics,
            progress=self._observe_progress,
            should_stop=self._should_stop,
            neural=True,
        )

    # -- progress -----------------------------------------------------------
    def _observe_progress(self, snapshot: dict) -> None:
        self._progress_count += 1
        # Keep only the newest completed snapshot: progress is bounded and never
        # counts pending work as simulations.
        if self._latest_progress is not None:
            self._dropped_progress += 1
        self._latest_progress = snapshot.copy()
        if self._progress is not None:
            self._progress(snapshot)

    def _take_progress(self) -> dict | None:
        snapshot = self._latest_progress
        self._latest_progress = None
        dropped = self._dropped_progress
        self._dropped_progress = 0
        if snapshot is None:
            return None
        snapshot["progress_dropped"] = dropped
        return snapshot

    # -- lifecycle ----------------------------------------------------------
    def start(self) -> dict:
        if self._closed:
            raise SearchProtocolError("SEARCH_CLOSED", "This search has been closed")
        if self.state.winner is not None:
            return self._complete(None, reason="terminal")
        if not legal_moves(self.state):
            raise SearchRequestError("NO_LEGAL_MOVES", "No legal moves in this position")
        return self._advance()

    def summary(self) -> dict:
        """Bounded diagnostics that survive context release."""
        diagnostics = self.diagnostics or {}
        return {
            "completed_simulations": int(diagnostics.get("simulations", 0) or 0),
            "neural_requests": self.requests,
            "prediction_reuses": int(diagnostics.get("mcts_node_prediction_reuses", 0) or 0),
            "deadline_hit": self.deadline_hit,
        }

    def close(self) -> None:
        """Close the abandoned generator and release its search-tree resources.

        Idempotent. The final completed/requested counters are retained in
        ``self.final_summary`` so callers can report failed or cancelled work
        without keeping the generator or its diagnostics alive.
        """
        if not self._closed:
            self.final_summary = self.summary()
        if self._context is not None:
            try:
                self._context.close()
            finally:
                self._context = None
        self._closed = True
        self._pending = None
        self._latest_progress = None
        self.diagnostics = {}
        self._trace = []

    # -- evaluation loop ----------------------------------------------------
    def _advance(self, response: tuple[dict[Move, float], float] | None = None) -> dict:
        assert self._context is not None
        try:
            if response is None:
                requested = next(self._context)
            else:
                requested = self._context.send(response)
        except StopIteration as finished:
            return self._complete(finished.value)
        except InterruptedError:
            summary = self.summary()
            self.close()
            reason = "deadline" if self.deadline_hit else "cancelled"
            search = self._search_summary(completed=False)
            search["completed_simulations"] = summary["completed_simulations"]
            search["completion_reason"] = reason
            search["deadline_cutoff"] = self.deadline_hit
            return {"kind": "cancelled", "reason": reason, "search": search}
        except SearchRequestError:
            self.close()
            raise
        except ValueError as error:
            # The generator rejects unreachable/terminal positions; surface it as
            # a stable protocol code instead of an opaque traceback.
            completed = self.summary()["completed_simulations"]
            self.close()
            raise SearchRequestError("SEARCH_FAILED", str(error), completed) from None
        except Exception as error:  # noqa: BLE001 - never leak a generator
            completed = self.summary()["completed_simulations"]
            self.close()
            raise SearchRequestError(
                "SEARCH_FAILED", f"{type(error).__name__}: {error}", completed
            ) from error
        return self._evaluation(requested)

    def _evaluation(self, state: State) -> dict:
        started = time.perf_counter()
        moves = legal_moves(state)
        if not moves:
            completed = self.summary()["completed_simulations"]
            self.close()
            raise SearchRequestError(
                "NO_LEGAL_MOVES", "Requested position has no legal moves", completed
            )
        self.timings["legal_generation_ms"] += (time.perf_counter() - started) * 1000.0
        started = time.perf_counter()
        planes = encode_planes(state)
        legal = legal_action_indices(state, moves)
        digest = neural_input_identity(state, planes, legal)
        self.timings["encoding_ms"] += (time.perf_counter() - started) * 1000.0
        self.timings["requests"] += 1
        index = self.requests
        self.requests += 1
        self._pending = {
            "index": index,
            "moves": moves,
            "legal": legal,
            "digest": digest,
            "position": state.to_dict(),
        }
        self._last_event_at = time.monotonic()
        request = {
            "request_index": index,
            "position_digest": digest,
            "ruleset": state.ruleset,
            "size": state.size,
            "planes": base64.b64encode(planes).decode("ascii"),
            "legal": legal,
        }
        if self.include_position:
            request["position"] = state.to_dict()
        return {
            "kind": "evaluate",
            "request": request,
            "progress": self._take_progress(),
        }

    def resume(self, payload: Any) -> dict:
        """Accept exactly one evaluation response and advance the generator.

        The response must match the pending request exactly and carry a proper
        legal distribution. A materially invalid response closes the search (it
        is never silently repaired); callers must start a new search.
        """
        if self._closed:
            raise SearchProtocolError("SEARCH_CLOSED", "This search has been closed")
        if self._pending is None:
            raise SearchProtocolError("NO_PENDING_REQUEST", "No neural evaluation is pending")
        try:
            return self._resume_validated(payload)
        except SearchProtocolError as error:
            error.completed_simulations = self.summary()["completed_simulations"]
            self.close()
            raise

    def _resume_validated(self, payload: Any) -> dict:
        pending = self._pending
        assert pending is not None
        if not isinstance(payload, dict):
            raise SearchProtocolError("INVALID_RESPONSE", "Evaluation response must be an object")
        validation_started = time.perf_counter()
        self._validating = validation_started
        index = payload.get("request_index")
        if type(index) is not int or index != pending["index"]:
            raise SearchProtocolError(
                "REQUEST_MISMATCH",
                "Evaluation response does not belong to the pending request",
            )
        legal = payload.get("legal")
        if not isinstance(legal, list) or legal != pending["legal"]:
            raise SearchProtocolError(
                "LEGAL_ACTIONS_MISMATCH",
                "Returned legal action indices do not match the requested position",
            )
        priors = payload.get("priors")
        if not isinstance(priors, list) or len(priors) != len(legal):
            raise SearchProtocolError(
                "INVALID_POLICY", "Legal priors must cover every requested legal action"
            )
        values: list[float] = []
        total = 0.0
        for prior in priors:
            if isinstance(prior, bool) or not isinstance(prior, (int, float)):
                raise SearchProtocolError("INVALID_POLICY", "Legal priors must be numbers")
            value = float(prior)
            if not math.isfinite(value) or value < 0:
                raise SearchProtocolError(
                    "INVALID_POLICY", "Legal priors must be finite and non-negative"
                )
            values.append(value)
            total += value
        if not math.isfinite(total) or total <= 0:
            raise SearchProtocolError("INVALID_POLICY", "Legal priors must have a positive sum")
        if abs(total - 1.0) > POLICY_SUM_TOLERANCE:
            # The validated inference backend promises legal probabilities that
            # sum to one. A materially different sum is a broken response, not
            # rounding, and is rejected instead of being silently renormalised.
            raise SearchProtocolError(
                "INVALID_POLICY",
                f"Legal priors must sum to 1 within {POLICY_SUM_TOLERANCE}; sum was {total!r}",
            )
        raw_value = payload.get("value")
        if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
            raise SearchProtocolError("INVALID_VALUE", "A defender-relative value is required")
        value = float(raw_value)
        if not math.isfinite(value) or not -1.0 <= value <= 1.0:
            raise SearchProtocolError(
                "INVALID_VALUE", "The defender-relative value must be finite and within [-1, 1]"
            )
        self.timings["validation_ms"] += (time.perf_counter() - self._validating) * 1000.0
        self._pending = None
        priors_by_move = dict(zip(pending["moves"], values))
        if self.trace:
            self._trace.append(
                {
                    "request_index": pending["index"],
                    "position_digest": pending["digest"],
                    "legal_count": len(pending["legal"]),
                    "value": value,
                }
            )
        del legal, priors
        return self._advance((priors_by_move, value))

    # -- completion ---------------------------------------------------------
    def _complete(
        self,
        probabilities: dict[Move, float] | None,
        *,
        reason: str | None = None,
    ) -> dict:
        self._pending = None
        diagnostics = self.diagnostics
        budget = self.requested_simulations
        simulations = int(diagnostics.get("simulations", 0) or 0)
        if reason is None:
            if diagnostics.get("immediate_win_shortcuts"):
                reason = "immediate_win"
            elif diagnostics.get("deadline_cutoffs"):
                reason = "deadline"
            elif simulations >= budget:
                reason = "simulations"
            elif simulations == 0:
                reason = "insufficient_budget"
            else:
                reason = "completed"
        distribution = probabilities or {}
        if distribution:
            best_move = max(distribution, key=lambda move: distribution[move])
        elif diagnostics.get("best_move"):
            best = diagnostics["best_move"]
            best_move = Move(best["source"], best["target"])
        else:
            best_move = None
        analysis_config = {
            key: self.settings[key] for key in SETTING_FIELDS if key in self.settings
        }
        if self.state.winner is not None:
            # Terminal positions are never searched and never request inference.
            analysis = analysis_from_search(self.state, analysis_config, None, {})
        else:
            details = dict(diagnostics)
            details.setdefault("simulations", simulations)
            if details.get("value") is None:
                raise SearchRequestError("SEARCH_FAILED", "Search produced no evaluation")
            analysis = analysis_from_search(
                self.state, analysis_config, best_move, details, model_backed=True
            )
        search = self._search_summary(completed=True)
        search["timing"] = {**self.timings}
        search.update(
            {
                "requested_simulations": budget,
                "completed_simulations": simulations,
                "root_value": diagnostics.get("root_value"),
                "search_value": diagnostics.get("value"),
                "best_move": _move_key(best_move) if best_move else None,
                "root_visits": self._root_visits(distribution),
                "neural_requests": int(diagnostics.get("mcts_neural_requests", 0) or 0),
                "prediction_reuses": int(diagnostics.get("mcts_node_prediction_reuses", 0) or 0),
                "inference_cache_hits": 0,
                "deadline_cutoff": bool(diagnostics.get("deadline_cutoffs")),
                "completion_reason": reason,
                "immediate_win_shortcuts": int(diagnostics.get("immediate_win_shortcuts", 0) or 0),
                "temperature": 0.0,
                "dirichlet_fraction": 0.0,
                # Bounded tree-size proxy: expanded root children only. The full
                # tree is not serialised out of the search process.
                "root_children": len(distribution),
            }
        )
        if self.trace:
            # The trace proves exact generator execution parity; a digest keeps
            # evidence compact while remaining reproducible.
            search["trace_length"] = len(self._trace)
            search["trace_sha256"] = _sha256(canonical_json(self._trace))
            search["trace"] = self._trace
        self.close()
        return {"kind": "complete", "analysis": analysis, "search": search}

    def _root_visits(self, distribution: dict[Move, float]) -> list[dict]:
        ordered = sorted(
            distribution.items(),
            key=lambda item: (-item[1], item[0].source, item[0].target),
        )
        visits = []
        total = math.fsum(distribution.values()) if distribution else 0.0
        for move, share in ordered:
            visits.append(
                {
                    "source": move.source,
                    "target": move.target,
                    "visits": int(round(share * self.diagnostics.get("simulations", 0)))
                    if total
                    else 0,
                    "share": share,
                }
            )
        return visits

    def _search_summary(self, *, completed: bool) -> dict:
        elapsed = max(0.0, time.monotonic() - self.started)
        return {
            "version": 1,
            "protocol": SEARCH_PROTOCOL,
            "driver_version": DRIVER_VERSION,
            "algorithm": SEARCH_ALGORITHM,
            "encoder_version": ENCODER_VERSION,
            "position_identity": self.position_identity,
            "ruleset": self.state.ruleset,
            "size": self.state.size,
            "model": dict(self.model),
            "provider": dict(self.provider) if self.provider else None,
            "seed": self.settings["seed"],
            "exploration": self.settings["exploration"],
            "time_limit": self.settings["time_limit"],
            "hard_deadline": hard_deadline_seconds(self.settings),
            "neural_requests": self.requests,
            "elapsed_ms": elapsed * 1000.0,
            "completed": completed,
            "authoritative": False,
        }
