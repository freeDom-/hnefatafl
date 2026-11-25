"""Production Pyodide cooperative neural-MCTS search bridge.

The worker keeps one small set of cooperative search contexts alive between
messages. Every message performs a bounded amount of work and returns
immediately with either the next neural evaluation request, the completed
analysis result, or a stable error envelope. Nothing here blocks waiting for
inference: the coordinator supplies the evaluation in a later message.

Only the production protocol is exposed. The browser spike's parity, workload and
benchmark actions never enter a production runtime artifact.

Protocol (JSON string in, JSON string out):

* ``{"action": "info"}`` — versions, bounds and engine/source identity.
* ``{"action": "start", "search_id", "state", "settings", "model",
   "provider", "trace"}`` — create a search and return its first event.
* ``{"action": "resume", "search_id", "request_index", "legal", "priors",
   "value"}`` — answer exactly one pending evaluation.
* ``{"action": "cancel", "search_id"}`` — cooperatively close and release a search.
* ``{"action": "close"}`` — release every live search context.

The response is ``{"ok": true, "event": ...}`` or
``{"ok": false, "code": "...", "message": "..."}``; a malformed request can never
produce a Python traceback across the worker boundary.
"""

from __future__ import annotations

import json
import re
import sys
import time

from hnefatafl.browser_engine_identity import engine_identity
from hnefatafl.encoding import ENCODER_VERSION
from hnefatafl.mcts_driver import (
    DRIVER_VERSION,
    MAX_EXPLORATION,
    MAX_SIMULATIONS,
    MAX_TIME_LIMIT,
    MIN_SIMULATIONS,
    MIN_TIME_LIMIT,
    SEARCH_ALGORITHM,
    SEARCH_PROTOCOL,
    NeuralSearch,
    SearchRequestError,
    validate_model_identity,
    validate_search_settings,
    validate_state,
)

MAX_PAYLOAD_BYTES = 512 * 1024
MAX_ACTIVE_SEARCHES = 2
MAX_SEARCH_ID = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")

_searches: dict[str, NeuralSearch] = {}


def _error(code: str, message: str) -> dict:
    return {"ok": False, "code": code, "message": message}


def _search_id(request: dict) -> str:
    search_id = request.get("search_id")
    if not isinstance(search_id, str) or not MAX_SEARCH_ID.match(search_id):
        raise SearchRequestError("INVALID_SEARCH_ID", "search_id must be 1-64 safe characters")
    return search_id


def _active(search_id: str) -> NeuralSearch:
    search = _searches.get(search_id)
    if search is None:
        raise SearchRequestError("NO_ACTIVE_SEARCH", "No search is active for this id")
    return search


def _start(request: dict, started: float) -> dict:
    search_id = _search_id(request)
    if search_id in _searches:
        raise SearchRequestError("SEARCH_BUSY", "A search with this id is already active")
    if len(_searches) >= MAX_ACTIVE_SEARCHES:
        raise SearchRequestError(
            "SEARCH_BUSY", f"At most {MAX_ACTIVE_SEARCHES} searches may be active"
        )
    trace = request.get("trace", False)
    if not isinstance(trace, bool):
        raise SearchRequestError("INVALID_REQUEST", "trace must be a boolean")
    provider = request.get("provider")
    if provider is not None and not isinstance(provider, dict):
        raise SearchRequestError("INVALID_REQUEST", "provider must be an object")
    state = validate_state(request.get("state"))
    settings = validate_search_settings(request.get("settings", {}))
    model = validate_model_identity(request.get("model"))
    if model["ruleset"] != state.ruleset:
        raise SearchRequestError(
            "MODEL_RULESET_MISMATCH", "The model ruleset does not match the position"
        )
    if model["size"] != state.size:
        raise SearchRequestError(
            "MODEL_SIZE_MISMATCH", "The model board size does not match the position"
        )
    search = NeuralSearch(
        state,
        settings,
        model,
        provider=provider,
        trace=trace,
        include_position=False,
    )
    _searches[search_id] = search
    try:
        event = search.start()
    except Exception:
        search.close()
        _searches.pop(search_id, None)
        raise
    if event["kind"] != "evaluate":
        _searches.pop(search_id, None)
    event["search_id"] = search_id
    event["python"] = {"action_ms": (time.perf_counter() - started) * 1000.0}
    return event


def _resume(request: dict, started: float) -> dict:
    search_id = _search_id(request)
    search = _active(search_id)
    try:
        event = search.resume(request)
    except Exception:
        # A failed search never stays registered: its generator and diagnostics
        # are released here, not only on a later client cancellation request.
        search.close()
        _searches.pop(search_id, None)
        raise
    if event["kind"] != "evaluate":
        _searches.pop(search_id, None)
    event["search_id"] = search_id
    event["python"] = {"action_ms": (time.perf_counter() - started) * 1000.0}
    return event


def _cancel(request: dict, started: float) -> dict:
    search_id = _search_id(request)
    search = _searches.pop(search_id, None)
    cancelled = search is not None
    summary = (
        search.summary()
        if search is not None
        else {"completed_simulations": 0, "neural_requests": 0, "prediction_reuses": 0}
    )
    if search is not None:
        search.close()
    return {
        "kind": "cancelled",
        "reason": "cancelled",
        "search_id": search_id,
        "cancelled": cancelled,
        "released": {
            "search_context": cancelled,
            "python_generator": cancelled,
            "completed_simulations": summary["completed_simulations"],
        },
        "completed_simulations": summary["completed_simulations"],
        "python": {"action_ms": (time.perf_counter() - started) * 1000.0},
    }


def info() -> dict:
    return {
        "protocol": SEARCH_PROTOCOL,
        "driver_version": DRIVER_VERSION,
        "encoder_version": ENCODER_VERSION,
        "algorithm": SEARCH_ALGORITHM,
        "active_searches": len(_searches),
        "bounds": {
            "min_simulations": MIN_SIMULATIONS,
            "max_simulations": MAX_SIMULATIONS,
            "min_time_limit": MIN_TIME_LIMIT,
            "max_time_limit": MAX_TIME_LIMIT,
            "max_exploration": MAX_EXPLORATION,
            "max_payload_bytes": MAX_PAYLOAD_BYTES,
            "max_active_searches": MAX_ACTIVE_SEARCHES,
        },
        "python": sys.version,
        "engine_identity": engine_identity(),
        "authoritative": False,
    }


def _dispatch(payload: str, extra=None) -> str:
    started = time.perf_counter()
    try:
        if not isinstance(payload, str):
            return json.dumps(_error("INVALID_REQUEST", "Request must be a serialized string"))
        if len(payload.encode("utf-8")) > MAX_PAYLOAD_BYTES:
            return json.dumps(_error("PAYLOAD_TOO_LARGE", "Search request exceeds 512 KiB"))
        try:
            request = json.loads(payload)
        except (TypeError, ValueError) as error:
            return json.dumps(_error("INVALID_REQUEST", f"Malformed request JSON: {error}"))
        if not isinstance(request, dict):
            return json.dumps(_error("INVALID_REQUEST", "Request must be an object"))
        action = request.get("action")
        if extra is not None:
            extra_event = extra(action, request, started)
            if extra_event is not None:
                return json.dumps({"ok": True, "event": extra_event})
        if action == "info":
            return json.dumps({"ok": True, "info": info()})
        if action == "start":
            return json.dumps({"ok": True, "event": _start(request, started)})
        if action == "resume":
            return json.dumps({"ok": True, "event": _resume(request, started)})
        if action == "cancel":
            return json.dumps({"ok": True, "event": _cancel(request, started)})
        if action == "close":
            released = len(_searches)
            for search in _searches.values():
                search.close()
            _searches.clear()
            return json.dumps(
                {"ok": True, "event": {"kind": "closed", "released_searches": released}}
            )
        return json.dumps(_error("UNKNOWN_ACTION", f"Unknown search action: {action!r}"))
    except SearchRequestError as error:
        envelope = _error(error.code, str(error))
        envelope["completed_simulations"] = error.completed_simulations
        return json.dumps(envelope)
    except InterruptedError:
        return json.dumps(_error("CANCELLED", "Search cancelled"))
    except MemoryError:
        for search in _searches.values():
            search.close()
        _searches.clear()
        return json.dumps(_error("RESOURCE_EXHAUSTED", "Search exceeded available memory"))
    except Exception as error:  # noqa: BLE001 - never cross the worker boundary raw
        return json.dumps(_error("SEARCH_FAILED", f"{type(error).__name__}: {error}"))


def dispatch(payload: str) -> str:
    """Production dispatch: the cooperative search protocol only."""
    return _dispatch(payload)
