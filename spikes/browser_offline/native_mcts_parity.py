"""Native reference runs for browser neural MCTS parity and benchmarks.

This is the native half of the reproducible comparison suite:

* ``--level a`` drives the unchanged cooperative MCTS generator with the shared
  deterministic evaluator; the browser runs the same module inside Pyodide.
* ``--level b`` drives it with an actual exported model, using either PyTorch or
  ONNX Runtime on CPU, to compare against browser WASM/WebGPU inference.

The solver never changes search semantics: the same ``NeuralSearch`` driver is
used by the browser, so Level A can require exact equality and Level B can report
numerical differences where floating-point inference legitimately diverges.

Commands::

    .venv/bin/python spikes/browser_offline/native_mcts_parity.py \\
        --level a --iterations 128 --output /tmp/mcts-parity-native-a.json
    .venv/bin/python spikes/browser_offline/native_mcts_parity.py \\
        --level b --model-export /tmp/model-export --evaluator onnx \\
        --iterations 128 --output /tmp/mcts-parity-native-b.json
"""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import math
import sys
import time
from pathlib import Path
from typing import Any, Callable

SPIKE = Path(__file__).resolve().parent
ROOT = SPIKE.parents[1]
for path in (str(SPIKE), str(ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

from fixtures import jarl_positions  # noqa: E402
from hnefatafl.engine import State, legal_moves  # noqa: E402
from hnefatafl.mcts_driver import (  # noqa: E402
    DRIVER_VERSION,
    SEARCH_ALGORITHM,
    position_identity,
    validate_search_settings,
)
from mcts_vectors import (  # noqa: E402
    EVALUATOR_VERSION,
    EVALUATOR_MODEL_SHA256,
    drive,
    evaluate as deterministic_evaluate,
    parity_payload,
    workload_identity,
)

SETTINGS = {"iterations": 128, "time_limit": 10.0, "seed": 7, "exploration": 1.4}
TERMINAL_KINDS = {"terminal"}


def digest_json(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def engine_identity() -> dict:
    from hnefatafl import ai, encoding, engine, mcts_driver, shield_maiden, symmetry

    modules = (engine, ai, encoding, mcts_driver, shield_maiden, symmetry)
    digest = hashlib.sha256()
    names = []
    try:
        for module in modules:
            names.append(module.__name__)
            digest.update(module.__name__.encode())
            digest.update(b"\0")
            digest.update(inspect.getsource(module).encode())
            digest.update(b"\0")
    except (OSError, TypeError, IndexError):
        return {"modules": names, "sha256": None, "reason": "source unavailable"}
    return {
        "modules": names,
        "sha256": digest.hexdigest(),
        "driver_version": DRIVER_VERSION,
        "encoder_version": encoding.ENCODER_VERSION,
        "search_algorithm": SEARCH_ALGORITHM,
    }


def legal_softmax(logits: list[float], legal: list[int]) -> list[float]:
    values = [float(logits[index]) for index in legal]
    maximum = max(values)
    exponentials = [math.exp(value - maximum) for value in values]
    total = sum(exponentials)
    return [value / total for value in exponentials]


def onnx_evaluator(export_dir: Path) -> tuple[Callable[[State], tuple[list[float], float]], dict]:
    import numpy
    import onnxruntime as ort

    from hnefatafl.encoding import encode_planes, legal_action_indices
    from hnefatafl.engine import RULESETS

    sidecar = json.loads((export_dir / "model.json").read_text())
    ruleset = sidecar["ruleset"]
    size = RULESETS[ruleset].size
    digest = sha256_file(export_dir / "model.onnx")
    options = ort.SessionOptions()
    options.intra_op_num_threads = 1
    options.inter_op_num_threads = 1
    session = ort.InferenceSession(
        str(export_dir / "model.onnx"), sess_options=options, providers=["CPUExecutionProvider"]
    )

    def evaluate(state: State) -> tuple[list[float], float]:
        planes = encode_planes(state)
        batch = numpy.array([float(value) for value in planes], dtype="float32").reshape(
            1, 6, size, size
        )
        moves = legal_moves(state)
        legal = legal_action_indices(state, moves)
        logits, value = session.run(None, {"planes": batch})
        return legal_softmax(logits[0].tolist(), legal), float(value[0])

    return evaluate, {
        "onnx_sha256": digest,
        "ruleset": ruleset,
        "size": size,
        "evaluator": "onnxruntime-cpu",
        "providers": session.get_providers(),
    }


def torch_evaluator(
    checkpoint: Path, export_dir: Path
) -> tuple[Callable[[State], tuple[list[float], float]], dict]:
    import torch

    from hnefatafl.engine import RULESETS
    from hnefatafl.models import encode_state, load_checkpoint, weights_fingerprint

    model, metadata = load_checkpoint(checkpoint, device="cpu")
    model.eval()
    sidecar = json.loads((export_dir / "model.json").read_text())
    ruleset = sidecar["ruleset"]
    digest = sha256_file(export_dir / "model.onnx")
    fingerprint = weights_fingerprint(model)

    def evaluate(state: State) -> tuple[list[float], float]:
        with torch.inference_mode():
            logits, value = model(encode_state(state).unsqueeze(0))
        moves = legal_moves(state)
        area = state.size**2
        ids = [move.source * area + move.target for move in moves]
        probabilities = torch.softmax(logits[0, ids], dim=0).tolist()
        return probabilities, float(value.reshape(-1)[0])

    return evaluate, {
        "onnx_sha256": digest,
        "weights_sha256": fingerprint,
        "ruleset": metadata.get("ruleset", ruleset),
        "size": RULESETS[ruleset].size,
        "evaluator": "torch-cpu",
    }


def select_positions(rulesets: list[str], limit: int | None) -> list[dict]:
    selected = [case for case in jarl_positions() if case["ruleset"] in rulesets]
    if limit is None:
        return selected
    grouped: dict[str, int] = {}
    result = []
    for case in selected:
        count = grouped.get(case["ruleset"], 0)
        if count >= limit:
            continue
        grouped[case["ruleset"]] = count + 1
        result.append(case)
    return result


def run_case(
    case: dict, settings: dict, evaluator, model: dict, *, include_trace: bool = False
) -> dict:
    state = State.from_dict(case["state"])
    record = {
        "name": case["name"],
        "ruleset": case["ruleset"],
        "kind": case["kind"],
        "terminal": state.winner is not None,
    }
    started = time.perf_counter()
    try:
        event = drive(state, settings, model, evaluator, trace=True)
    except Exception as error:  # noqa: BLE001 - reported per case
        record.update({"ok": False, "error": f"{type(error).__name__}: {error}"})
        record["elapsed_ms"] = (time.perf_counter() - started) * 1000.0
        return record
    record["elapsed_ms"] = (time.perf_counter() - started) * 1000.0
    payload = parity_payload(event)
    if not include_trace and payload.get("search"):
        payload["search"].pop("trace", None)
    record.update(
        {
            "ok": payload["kind"] == "complete",
            "kind_event": payload["kind"],
            "analysis": payload.get("analysis"),
            "search": payload.get("search"),
            "path": position_identity(state, settings, model),
        }
    )
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--level", choices=["a", "b"], default="a")
    parser.add_argument("--evaluator", choices=["deterministic", "torch", "onnx"], default=None)
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--model-export", type=Path)
    parser.add_argument("--positions", type=Path, help="Custom serialized position list")
    parser.add_argument("--rulesets", default="brandubh,tablut,copenhagen")
    parser.add_argument("--limit", type=int, help="Maximum positions per ruleset")
    parser.add_argument("--iterations", type=int, default=SETTINGS["iterations"])
    parser.add_argument("--time-limit", type=float, default=SETTINGS["time_limit"])
    parser.add_argument("--seed", type=int, default=SETTINGS["seed"])
    parser.add_argument("--exploration", type=float, default=SETTINGS["exploration"])
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--include-trace",
        action="store_true",
        help="Store the complete evaluation trace as well as its digest",
    )
    args = parser.parse_args()

    level = args.level
    evaluator_name = args.evaluator or ("deterministic" if level == "a" else "onnx")
    settings = validate_search_settings(
        {
            "iterations": args.iterations,
            "time_limit": args.time_limit,
            "seed": args.seed,
            "exploration": args.exploration,
        }
    )
    rulesets = [name.strip() for name in args.rulesets.split(",") if name.strip()]
    unknown = [name for name in rulesets if name not in {"brandubh", "tablut", "copenhagen"}]
    if unknown:
        parser.error(f"Unsupported ruleset(s): {unknown}")
    if args.positions:
        cases = json.loads(args.positions.read_text())
        cases = [case for case in cases if case["ruleset"] in rulesets]
    else:
        cases = select_positions(rulesets, args.limit)

    model = None
    model_meta: dict | None = None
    evaluator = None
    if level == "a" or evaluator_name == "deterministic":
        if evaluator_name != "deterministic":
            parser.error("Level A only supports the deterministic evaluator")
        evaluator = deterministic_evaluate
        model_meta = {"evaluator": EVALUATOR_VERSION, "structural_only": True}
    else:
        if args.model_export is None:
            parser.error("--model-export is required for a model-backed run")
        export_dir = args.model_export.resolve()
        if evaluator_name == "onnx":
            evaluator, model_meta = onnx_evaluator(export_dir)
        else:
            if args.checkpoint is None:
                parser.error("--checkpoint is required for the torch evaluator")
            evaluator, model_meta = torch_evaluator(args.checkpoint.resolve(), export_dir)
    environment = {
        "runtime": f"cpython-{sys.version.split()[0]}",
        "platform": sys.platform,
    }
    results = []
    failures = []
    for case in cases:
        state = State.from_dict(case["state"])
        if level == "a":
            case_model = {
                "sha256": EVALUATOR_MODEL_SHA256,
                "ruleset": state.ruleset,
                "size": state.size,
            }
        else:
            case_model = {
                "sha256": model_meta["onnx_sha256"],
                "ruleset": state.ruleset,
                "size": state.size,
            }
        if level == "a" and state.ruleset not in {"brandubh", "tablut", "copenhagen"}:
            failures.append(f"{case['name']}: unsupported ruleset")
            continue
        record = run_case(
            case,
            settings,
            evaluator=evaluator,
            model=case_model,
            include_trace=args.include_trace,
        )
        if not record["ok"]:
            if record["terminal"]:
                record["note"] = "terminal positions complete without inference"
            else:
                failures.append(f"{case['name']}: {record.get('error', 'search failed')}")
        results.append(record)

    workload_digest = workload_identity(
        level,
        evaluator_name,
        settings,
        [case["state"] for case in cases],
        (model_meta or {}).get("onnx_sha256"),
    )
    report = {
        "kind": "mcts-parity",
        "schema": 1,
        "level": level,
        "runtime": "native-cpython",
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "environment": environment,
        "engine_identity": engine_identity(),
        "evaluator": evaluator_name,
        "model_meta": model_meta,
        "model": model,
        "settings": {
            key: settings[key] for key in ("iterations", "time_limit", "seed", "exploration")
        },
        "workload_digest": workload_digest,
        "positions": [
            {
                "name": case["name"],
                "ruleset": case["ruleset"],
                "kind": case["kind"],
                "state": case["state"],
            }
            for case in cases
        ],
        "results": results,
        "failures": failures,
        "authoritative": False,
    }
    report["ok"] = not failures
    text = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text)
    print(
        json.dumps(
            {
                "ok": report["ok"],
                "level": level,
                "evaluator": evaluator_name,
                "cases": len(results),
                "failures": failures[:5],
                "workload_digest": report["workload_digest"],
                "engine_sha256": report["engine_identity"]["sha256"],
            },
            indent=2,
        )
    )
    sys.exit(0 if report["ok"] else 1)


if __name__ == "__main__":
    main()
