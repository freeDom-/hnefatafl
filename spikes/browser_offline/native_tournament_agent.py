"""Line-protocol native tournament agent for ``tournament.py --run``.

Speaks the persistent process protocol (version 2)::

    handshake: {"kind": "handshake", "protocol": 2, "agent": {...}}
    request:   {"id": "...", "state": {...}, "settings": {...}, "seed": 7}
    response:  {"id": "...", "move": {...}, "evidence": {...}}

The shared Python engine and the production ``hnefatafl.ai`` presets perform the
search. The handshake reports the actual engine identity, kind, runtime, provider,
algorithm and supported knobs; every response reports the effective settings,
seed, completed work/depth, completion reason and elapsed time so the tournament
validator can bind declared configurations to observed execution.

``--engine scripted`` / ``scripted-last`` select the first/last legal move and are
for structural workflow checks only (never playing-strength evidence).
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

import native_mcts_parity  # noqa: E402
import prepare_neural  # noqa: E402
from hnefatafl.ai import choose_move  # noqa: E402
from hnefatafl.engine import State, legal_moves  # noqa: E402

PROTOCOL_VERSION = 2
DEFAULT_KIND = {
    "scripted": "scripted",
    "scripted-last": "scripted",
    "jarl": "native-jarl",
    "seer": "native-seer",
    "neural": "native-neural",
}
ALGORITHM = {
    "scripted": "scripted",
    "scripted-last": "scripted",
    "jarl": "alpha_beta",
    "seer": "mcts",
    "neural": "mcts",
}


def _search(
    state: State,
    settings: dict,
    seed: int,
    engine: str,
    depth: int,
    verified_model=None,
):
    started = time.perf_counter()
    mode = settings.get("mode", "fixed-work")
    if engine in ("scripted", "scripted-last"):
        moves = legal_moves(state)
        move = moves[0] if engine == "scripted" else moves[-1]
        return move, {
            "completed_simulations": None,
            "depth": None,
            "completion_reason": "completed",
            "settings": {
                "mode": mode,
                "iterations": None,
                "time_limit": settings.get("time_limit"),
                "exploration": settings.get("exploration"),
                "seed": seed,
            },
        }
    time_limit = float(settings.get("time_limit", 2.0))
    diagnostics: dict = {}
    if engine == "jarl":
        config = {
            "algorithm": "alpha_beta",
            "depth": depth,
            "time_limit": time_limit,
            "seed": seed,
        }
    elif engine == "seer":
        config = {
            "algorithm": "mcts",
            "iterations": int(settings.get("iterations", 128)),
            "time_limit": time_limit,
            "exploration": float(settings.get("exploration", 1.4)),
            "seed": seed,
        }
    elif engine == "neural":
        if verified_model is None:
            raise ValueError("the neural engine requires a verified model")
        config = {
            "algorithm": "neural",
            "search_wrapper": "mcts",
            "_model": verified_model,
            "iterations": int(settings.get("iterations", 128)),
            "time_limit": time_limit,
            "exploration": float(settings.get("exploration", 1.4)),
            "seed": seed,
        }
    else:  # pragma: no cover - argparse restricts the value
        raise ValueError(f"unsupported engine {engine!r}")
    move = choose_move(state, config, diagnostics=diagnostics)
    if engine == "jarl":
        completed_depth = int(diagnostics.get("completed_depth", 0))
        interrupted = diagnostics.get("interrupted_depth")
        if mode == "fixed-time" and (
            completed_depth < depth or interrupted is not None
        ):
            reason = "deadline"
        else:
            reason = "completed"
        return move, {
            "completed_simulations": None,
            "depth": completed_depth,
            "completion_reason": reason,
            "settings": {
                "mode": mode,
                "iterations": None,
                "time_limit": time_limit,
                "exploration": settings.get("exploration"),
                "seed": seed,
                "max_depth": depth,
            },
        }
    completed = int(diagnostics.get("simulations", 0))
    requested = int(settings.get("iterations", 128))
    if diagnostics.get("immediate_win_shortcuts"):
        reason = "immediate_win"
    elif completed < requested:
        reason = "deadline"
    else:
        reason = "simulations"
    return move, {
        "completed_simulations": completed,
        "depth": None,
        "completion_reason": reason,
        "neural_inferences": int(diagnostics.get("mcts_neural_requests", 0)),
        "settings": {
            "mode": mode,
            "iterations": requested,
            "time_limit": time_limit,
            "exploration": settings.get("exploration"),
            "seed": seed,
        },
    }


def _verify_neural_artifacts(checkpoint: Path, export_dir: Path):
    """Validate the full export contract and return the retained checkpoint model."""
    from hnefatafl.models import load_checkpoint, weights_fingerprint

    try:
        export = prepare_neural.validate_export_dir(export_dir, allow_unvalidated=False)
    except (SystemExit, ValueError, OSError, TypeError) as error:
        message = str(error)
        message = message.replace(str(export_dir), "<model-export>")
        raise ValueError(f"ONNX export validation failed: {message}") from None
    sidecar = export["sidecar"]
    try:
        model, _ = load_checkpoint(checkpoint.resolve(), device="cpu")
    except (SystemExit, ValueError, OSError, TypeError) as error:
        message = str(error).replace(str(checkpoint), "<checkpoint>")
        raise ValueError(f"checkpoint validation failed: {message}") from None
    source = sidecar.get("source") or {}
    if weights_fingerprint(model) != source.get("weights_sha256"):
        raise ValueError("checkpoint weights do not match the validated ONNX export")
    metadata = sidecar.get("model_config") or {}
    architecture = {
        "channels": getattr(model, "channels", None),
        "blocks": getattr(model, "blocks", None),
    }
    if architecture["channels"] != metadata.get("channels") or architecture["blocks"] != metadata.get(
        "blocks"
    ):
        raise ValueError("checkpoint architecture does not match the validated ONNX export")
    if sidecar.get("ruleset") != getattr(model, "ruleset", sidecar.get("ruleset")):
        raise ValueError("checkpoint ruleset does not match the validated ONNX export")
    encoder = source.get("encoder_version") or (sidecar.get("input") or {}).get("encoding")
    if encoder != "six-plane-v1":
        raise ValueError("validated export uses an unsupported encoder version")
    shape = (sidecar.get("input") or {}).get("shape")
    size = shape[-1] if isinstance(shape, list) and len(shape) >= 2 else None
    if size != getattr(model, "size", size):
        raise ValueError("checkpoint board size does not match the validated ONNX export")
    identity = {
        "model_sha256": (sidecar.get("onnx") or {}).get("sha256"),
        "weights_sha256": source.get("weights_sha256"),
        "ruleset": sidecar.get("ruleset"),
        "size": size,
        "encoder_version": encoder,
        "architecture": architecture,
        "device": "cpu",
    }
    return model, identity


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--engine",
        choices=("scripted", "scripted-last", "jarl", "seer", "neural"),
        default="scripted",
    )
    parser.add_argument(
        "--kind",
        choices=("scripted", "native-jarl", "native-seer", "native-neural"),
    )
    parser.add_argument("--depth", type=int, default=5)
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--model-export", type=Path)
    args = parser.parse_args()
    kind = args.kind or DEFAULT_KIND[args.engine]
    algorithm = ALGORITHM[args.engine]
    model_identity = {}
    verified_model = None
    if args.engine == "neural":
        if args.checkpoint is None or args.model_export is None:
            parser.error("--engine neural requires --checkpoint and --model-export")
        try:
            verified_model, model_identity = _verify_neural_artifacts(
                args.checkpoint, args.model_export
            )
        except ValueError as error:
            print(f"native_tournament_agent: {error}", file=sys.stderr)
            return 2
    handshake = {
        "kind": "handshake",
        "protocol": PROTOCOL_VERSION,
        "agent": {
            "protocol": PROTOCOL_VERSION,
            "kind": kind,
            "runtime": "native-cpython",
            "provider": "torch-cpu" if args.engine == "neural" else "cpu",
            "algorithm": algorithm,
            "engine_sha256": native_mcts_parity.engine_identity()["sha256"],
            **model_identity,
            "settings_support": {
                "iterations": args.engine in ("seer", "neural"),
                "time_limit": True,
                "depth": args.engine == "jarl",
            },
            "modes": (
                ["fixed-work"]
                if args.engine in ("scripted", "scripted-last")
                else ["fixed-work", "fixed-time"]
            ),
        },
    }
    if args.engine == "jarl":
        handshake["agent"]["depth"] = args.depth
    print(json.dumps(handshake, separators=(",", ":")), flush=True)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            request_id = request["id"]
            state = State.from_dict(request["state"])
            settings = request.get("settings") or {}
            seed = int(request.get("seed", settings.get("seed", 0)))
            if state.winner is not None:
                raise ValueError("cannot search a terminal position")
            started = time.perf_counter()
            move, result = _search(
                state, settings, seed, args.engine, args.depth, verified_model
            )
            elapsed_ms = (time.perf_counter() - started) * 1000.0
        except Exception as error:  # noqa: BLE001 - report the failure on stderr and exit 2
            print(f"native_tournament_agent: {type(error).__name__}: {error}", file=sys.stderr)
            return 2
        result_settings = result.pop("settings", None)
        evidence = {
            "settings": result_settings
            or {
                "mode": settings.get("mode", "fixed-work"),
                "iterations": settings.get("iterations"),
                "time_limit": settings.get("time_limit"),
                "exploration": settings.get("exploration"),
                "seed": seed,
            },
            "seed": seed,
            "algorithm": algorithm,
            "provider": "torch-cpu" if args.engine == "neural" else "cpu",
            "provider_requested": "torch-cpu" if args.engine == "neural" else "cpu",
            "provider_observed": args.engine == "neural",
            "neural_inferences": result.get("neural_inferences", 0),
            "gpu_execution_verified": False,
            "model_sha256": model_identity.get("model_sha256"),
            "elapsed_ms": elapsed_ms,
            **result,
        }
        print(
            json.dumps(
                {"id": request_id, "move": move.to_dict(), "evidence": evidence},
                separators=(",", ":"),
            ),
            flush=True,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
