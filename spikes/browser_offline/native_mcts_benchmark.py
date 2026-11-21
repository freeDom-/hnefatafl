"""Native neural-MCTS benchmark (reference for the browser benchmark).

Runs the same workload matrix the browser harness runs, using the unchanged
cooperative engine and an actual exported model through ONNX Runtime CPU (or
PyTorch CPU). Reports actual completed simulations, throughput, the measured
in-worker timing breakdown and an explicit unattributed remainder.

    .venv/bin/python spikes/browser_offline/native_mcts_benchmark.py \\
        --model-export /tmp/model-export --evaluator onnx --quick \\
        --output /tmp/mcts-native-quick.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

SPIKE = Path(__file__).resolve().parent
ROOT = SPIKE.parents[1]
for path in (str(SPIKE), str(ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

import mcts_benchmark  # noqa: E402
import mcts_workload  # noqa: E402
import mcts_vectors  # noqa: E402
import native_mcts_parity  # noqa: E402
from hnefatafl.engine import State  # noqa: E402
from hnefatafl.mcts_driver import validate_search_settings  # noqa: E402


def timed_evaluator(evaluator):
    state = {"ms": 0.0, "calls": 0}

    def wrapped(position):
        started = time.perf_counter()
        priors, value = evaluator(position)
        state["ms"] += (time.perf_counter() - started) * 1000.0
        state["calls"] += 1
        return priors, value

    return wrapped, state


def run_case(
    position: dict,
    *,
    mode: str,
    budget: float,
    settings: dict,
    evaluator,
    model: dict,
    provider: str,
    repetition: int,
    warm: bool,
) -> dict:
    state = State.from_dict(position["state"])
    timer = {"ms": 0.0, "calls": 0}

    def measured(requested):
        started = time.perf_counter()
        priors, value = evaluator(requested)
        timer["ms"] += (time.perf_counter() - started) * 1000.0
        timer["calls"] += 1
        return priors, value

    record = {
        "runtime": "native-cpython",
        "provider": provider,
        "ruleset": position["ruleset"],
        "position": position["name"],
        "kind": position["kind"],
        "mode": mode,
        "budget": budget,
        "repetition": repetition,
        "warm": warm,
        "requested_simulations": settings["iterations"],
    }
    started = time.perf_counter()
    try:
        event = mcts_vectors.drive(state, settings, model, measured, trace=False)
    except Exception as error:  # noqa: BLE001 - reported per case
        record.update(
            {
                "ok": False,
                "error": f"{type(error).__name__}: {error}",
                "elapsed_ms": (time.perf_counter() - started) * 1000.0,
            }
        )
        return record
    elapsed = (time.perf_counter() - started) * 1000.0
    search = event["search"] if event["kind"] == "complete" else {}
    timing = search.get("timing") or {}
    worker_ms = (
        timing.get("legal_generation_ms", 0.0)
        + timing.get("encoding_ms", 0.0)
        + timing.get("validation_ms", 0.0)
    )
    traversal = max(0.0, elapsed - timer["ms"] - worker_ms)
    overshoot = max(0.0, elapsed - float(budget) * 1000.0) if mode == "fixed_time" else 0.0
    record.update(
        {
            "ok": event["kind"] == "complete",
            "completed_simulations": search.get("completed_simulations", 0),
            "completion_reason": search.get("completion_reason"),
            "deadline_cutoff": search.get("deadline_cutoff", False),
            "neural_requests": search.get("neural_requests", 0),
            "prediction_reuses": search.get("prediction_reuses", 0),
            "root_children": search.get("root_children", 0),
            "elapsed_ms": elapsed,
            "inference_ms": timer["ms"],
            "inference_calls": timer["calls"],
            "encode_ms": timing.get("encoding_ms", 0.0),
            "legal_generation_ms": timing.get("legal_generation_ms", 0.0),
            "state_validation_ms": timing.get("validation_ms", 0.0),
            "traversal_ms": traversal,
            "deadline_overshoot_ms": overshoot,
            "simulations_per_second": (
                search.get("completed_simulations", 0) / (elapsed / 1000.0) if elapsed else None
            ),
            "analysis_source": (event.get("analysis") or {}).get("source"),
            "search_value": (event.get("analysis") or {}).get("value"),
        }
    )
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-export", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--evaluator", choices=["onnx", "torch"], default="onnx")
    parser.add_argument("--rulesets", default=None)
    parser.add_argument("--mode", choices=["both", *mcts_benchmark.MODES], default="both")
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--extended", action="store_true")
    parser.add_argument("--simulations", default=None, help="Comma-separated budgets")
    parser.add_argument("--time-budgets", default=None, help="Comma-separated seconds")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--exploration", type=float, default=1.4)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    export_dir = args.model_export.resolve()
    sidecar = json.loads((export_dir / "model.json").read_text())
    rulesets = (
        [name.strip() for name in args.rulesets.split(",") if name.strip()]
        if args.rulesets
        else [sidecar["ruleset"]]
    )
    preset = mcts_benchmark.EXTENDED if args.extended else mcts_benchmark.QUICK
    simulations = (
        [int(value) for value in args.simulations.split(",")]
        if args.simulations
        else preset["simulations"]
    )
    time_budgets = (
        [float(value) for value in args.time_budgets.split(",")]
        if args.time_budgets
        else preset["time_budgets"]
    )
    modes = list(mcts_benchmark.MODES) if args.mode == "both" else [args.mode]
    cases = mcts_benchmark.cases_for(modes, simulations, time_budgets)
    workload = mcts_workload.build_fixtures_workload(rulesets, list(preset["kinds"]), None)
    positions = workload["positions"]
    if not positions:
        parser.error("the selected rulesets have no benchmark positions")

    started = time.perf_counter()
    if args.evaluator == "torch" and args.checkpoint is None:
        parser.error("--checkpoint is required for the torch evaluator")
    artifacts = native_mcts_parity.load_model_artifacts(
        export_dir, args.checkpoint.resolve() if args.checkpoint is not None else None
    )
    if artifacts["ruleset"] not in rulesets:
        parser.error(f"The exported model ruleset {artifacts['ruleset']} is not in --rulesets")
    if args.evaluator == "onnx":
        evaluator, model_meta = native_mcts_parity.onnx_evaluator(export_dir)
    else:
        evaluator, model_meta = native_mcts_parity.torch_evaluator(
            args.checkpoint.resolve(),
            export_dir,
            model=artifacts["checkpoint_model"],
            weights_sha256=artifacts["weights_sha256"],
        )
    model_meta["weights_sha256"] = artifacts["weights_sha256"]
    model_meta["validated"] = artifacts["validated"]
    model_load_ms = (time.perf_counter() - started) * 1000.0
    model_sha = model_meta["onnx_sha256"]
    workload_digest = mcts_benchmark.digest_workload(
        positions,
        cases,
        seed=args.seed,
        exploration=args.exploration,
        model_sha256=model_sha,
    )

    records = []
    for position in positions:
        state = State.from_dict(position["state"])
        model = {"sha256": model_sha, "ruleset": state.ruleset, "size": state.size}
        for case in cases:
            settings = validate_search_settings(
                mcts_benchmark.settings_for(
                    case["mode"],
                    case["budget"],
                    seed=args.seed,
                    exploration=args.exploration,
                )
            )
            for warmup in range(preset["warmup"]):
                run_case(
                    position,
                    mode=case["mode"],
                    budget=case["budget"],
                    settings=settings,
                    evaluator=evaluator,
                    model=model,
                    provider=model_meta["evaluator"],
                    repetition=-(warmup + 1),
                    warm=True,
                )
            for repetition in range(preset["repetitions"]):
                records.append(
                    run_case(
                        position,
                        mode=case["mode"],
                        budget=case["budget"],
                        settings=settings,
                        evaluator=evaluator,
                        model=model,
                        provider=model_meta["evaluator"],
                        repetition=repetition,
                        warm=True,
                    )
                )
    failures = [
        f"{record['position']}/{record['mode']}/{record['budget']}: {record.get('error', 'search failed')}"
        for record in records
        if not record["ok"]
    ]
    summary = mcts_benchmark.summarize(records)
    report = {
        "kind": "mcts-benchmark",
        "schema": 1,
        "runtime": "native-cpython",
        "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "environment": {
            "platform": sys.platform,
            "python": sys.version.split()[0],
            "cpu_count": __import__("os").cpu_count(),
        },
        "engine_identity": native_mcts_parity.engine_identity(),
        "workload_digest": workload_digest,
        "workload": {
            **workload,
            "cases": cases,
            "seed": args.seed,
            "exploration": args.exploration,
            "repetitions": preset["repetitions"],
            "warmup": preset["warmup"],
        },
        "model": {"onnx_sha256": model_sha, "meta": model_meta},
        "cold_model_load_ms": model_load_ms,
        "records": records,
        "summary": summary,
        "failures": failures,
        "authoritative": False,
    }
    report["ok"] = not failures and summary["fixed_simulations_complete"]
    text = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text)
    print(
        json.dumps(
            {
                "ok": report["ok"],
                "records": len(records),
                "positions": len(positions),
                "cases": len(cases),
                "cold_model_load_ms": round(model_load_ms, 1),
                "fixed_simulations_complete": summary["fixed_simulations_complete"],
                "failures": failures[:5],
            },
            indent=2,
        )
    )
    sys.exit(0 if report["ok"] else 1)


if __name__ == "__main__":
    main()
