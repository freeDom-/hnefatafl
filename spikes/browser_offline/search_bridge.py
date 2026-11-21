"""Pyodide search-worker bridge for experimental browser neural MCTS.

The worker keeps one small set of cooperative search contexts alive between
messages. Every message performs a bounded amount of work and returns immediately
with either the next neural evaluation request, the completed analysis result, or
a stable error envelope. Nothing here blocks waiting for inference: the
coordinator supplies the evaluation in a later message.

Protocol (JSON string in, JSON string out):

* ``{"action": "info"}`` — versions, bounds and engine/source identity.
* ``{"action": "start", "search_id", "state", "settings", "model",
   "provider", "trace"}`` — create a search and return its first event.
* ``{"action": "resume", "search_id", "request_index", "legal", "priors",
   "value"}`` — answer exactly one pending evaluation.
* ``{"action": "cancel", "search_id"}`` — cooperatively close and release a search.
* ``{"action": "close"}`` — release every live search context.
* ``{"action": "parity", "state", "settings", "model"}`` — run the deterministic
   Level A parity search entirely inside this runtime (no inference).

The response is ``{"ok": true, "event": ...}`` or
``{"ok": false, "code": "...", "message": "..."}``; a malformed request can never
produce a Python traceback across the worker boundary.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import re
import sys
import time

from hnefatafl.mcts_driver import (
    DRIVER_VERSION,
    SEARCH_ALGORITHM,
    SEARCH_PROTOCOL,
    MAX_EXPLORATION,
    MAX_SIMULATIONS,
    MAX_TIME_LIMIT,
    MIN_SIMULATIONS,
    MIN_TIME_LIMIT,
    NeuralSearch,
    SearchRequestError,
    validate_model_identity,
    validate_search_settings,
    validate_state,
)
from hnefatafl.encoding import ENCODER_VERSION

MAX_PAYLOAD_BYTES = 512 * 1024
MAX_ACTIVE_SEARCHES = 2
MAX_SEARCH_ID = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")

_searches: dict[str, NeuralSearch] = {}


def _error(code: str, message: str) -> dict:
    return {"ok": False, "code": code, "message": message}


def engine_identity() -> dict:
    """Hash the staged sources so native and browser runs compare exactly."""
    from hnefatafl import ai, encoding, engine, mcts_driver, shield_maiden, symmetry

    modules = (engine, ai, encoding, mcts_driver, shield_maiden, symmetry)
    digest = hashlib.sha256()
    names = []
    per_module = {}
    try:
        for module in modules:
            names.append(module.__name__)
            source = inspect.getsource(module)
            per_module[module.__name__] = hashlib.sha256(source.encode()).hexdigest()
            digest.update(module.__name__.encode())
            digest.update(b"\0")
            digest.update(source.encode())
            digest.update(b"\0")
    except (OSError, TypeError, IndexError):
        return {"modules": names, "sha256": None, "reason": "source unavailable"}
    return {
        "modules": names,
        "module_sha256": per_module,
        "sha256": digest.hexdigest(),
        "driver_version": DRIVER_VERSION,
        "encoder_version": ENCODER_VERSION,
        "search_algorithm": SEARCH_ALGORITHM,
    }


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
        # An acknowledgement describes exactly what was released.
        "released": {
            "search_context": cancelled,
            "python_generator": cancelled,
            "completed_simulations": summary["completed_simulations"],
        },
        "completed_simulations": summary["completed_simulations"],
        "python": {"action_ms": (time.perf_counter() - started) * 1000.0},
    }


def _parity(request: dict, started: float) -> dict:
    from mcts_vectors import parity_payload, run_search

    event = run_search(
        request.get("state"),
        request.get("settings", {}),
        model=request.get("model"),
        trace=bool(request.get("trace", True)),
    )
    payload = parity_payload(event)
    payload["python"] = {"action_ms": (time.perf_counter() - started) * 1000.0}
    return payload


def _workload(request: dict, started: float) -> dict:
    from mcts_vectors import workload_identity

    level = request.get("level")
    if level not in ("a", "b"):
        raise SearchRequestError("INVALID_REQUEST", "level must be a or b")
    evaluator = request.get("evaluator")
    if not isinstance(evaluator, str) or len(evaluator) > 64:
        raise SearchRequestError("INVALID_REQUEST", "evaluator must be a short string")
    positions = request.get("positions")
    if not isinstance(positions, list) or not 1 <= len(positions) <= 64:
        raise SearchRequestError("INVALID_REQUEST", "positions must be a list of 1-64 entries")
    settings = validate_search_settings(request.get("settings", {}))
    model_digest = request.get("model_onnx_sha256")
    if model_digest is not None and (not isinstance(model_digest, str) or len(model_digest) != 64):
        raise SearchRequestError("INVALID_REQUEST", "model_onnx_sha256 must be a 64-char digest")
    return {
        "kind": "workload",
        "digest": workload_identity(level, evaluator, settings, positions, model_digest),
        "settings": {
            key: settings[key] for key in ("iterations", "time_limit", "seed", "exploration")
        },
        "positions": len(positions),
        "python": {"action_ms": (time.perf_counter() - started) * 1000.0},
    }


def _benchmark_workload(request: dict, started: float) -> dict:
    from mcts_benchmark import digest_workload

    positions = request.get("positions")
    if not isinstance(positions, list) or not 1 <= len(positions) <= 64:
        raise SearchRequestError("INVALID_REQUEST", "positions must be a list of 1-64 entries")
    cases = request.get("cases")
    if not isinstance(cases, list) or not 1 <= len(cases) <= 32:
        raise SearchRequestError("INVALID_REQUEST", "cases must be a list of 1-32 entries")
    seed = request.get("seed", 7)
    if type(seed) is not int or not 0 <= seed <= 2**32 - 1:
        raise SearchRequestError("INVALID_REQUEST", "invalid benchmark seed")
    exploration = request.get("exploration", 1.4)
    if isinstance(exploration, bool) or not isinstance(exploration, (int, float)):
        raise SearchRequestError("INVALID_REQUEST", "invalid exploration")
    model_digest = request.get("model_onnx_sha256")
    if model_digest is not None and (not isinstance(model_digest, str) or len(model_digest) != 64):
        raise SearchRequestError("INVALID_REQUEST", "model_onnx_sha256 must be a 64-char digest")
    return {
        "kind": "benchmark-workload",
        "digest": digest_workload(
            positions,
            cases,
            seed=seed,
            exploration=float(exploration),
            model_sha256=model_digest,
        ),
        "cases": len(cases),
        "positions": len(positions),
        "python": {"action_ms": (time.perf_counter() - started) * 1000.0},
    }


def dispatch(payload: str) -> str:
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
        if action == "info":
            return json.dumps(
                {
                    "ok": True,
                    "info": {
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
                    },
                }
            )
        if action == "json":
            # Protocol self-check for the browser harness: proves a request was
            # evaluated in this runtime, not in the coordinator.
            return json.dumps({"ok": True, "python": sys.version})
        if action == "start":
            event = _start(request, started)
            return json.dumps({"ok": True, "event": event})
        if action == "resume":
            event = _resume(request, started)
            return json.dumps({"ok": True, "event": event})
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
        if action == "parity":
            return json.dumps({"ok": True, "event": _parity(request, started)})
        if action == "workload":
            return json.dumps({"ok": True, "event": _workload(request, started)})
        if action == "benchmark_workload":
            return json.dumps({"ok": True, "event": _benchmark_workload(request, started)})
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
