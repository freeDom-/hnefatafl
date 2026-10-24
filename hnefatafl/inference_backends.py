"""Optional neural forward engines. PyTorch alone owns trainable weights.

OpenVINO imports stay lazy. Device probing runs once in a crash-isolated process;
training already runs in supervised processes, never in the HTTP server.
"""

from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, version
import json
import re
import subprocess
import sys
import time
from typing import Any

import torch

from .engine import new_game
from .models import PolicyValueNet, encode_state

_DEVICE = re.compile(r"(?:GPU(?:\.\d+)?|CPU)\Z")


def normalize_inference(config: dict) -> None:
    backend = config["inference_backend"]
    device = config["inference_device"]
    if backend not in ("pytorch", "openvino"):
        raise ValueError("inference_backend must be pytorch or openvino")
    if not isinstance(device, str) or len(device) > 32:
        raise ValueError("inference_device must be a device identifier")
    if backend == "openvino":
        if device != "auto" and not _DEVICE.fullmatch(device):
            raise ValueError("OpenVINO inference_device must be auto, GPU, GPU.N or CPU")
        if config["device"] not in ("cpu", "auto"):
            raise ValueError("OpenVINO inference requires PyTorch CPU optimization")
    elif device not in ("auto", config["device"]):
        raise ValueError("PyTorch inference_device must be auto or match the training device")


def selected_openvino_device(device: str) -> str:
    return "GPU" if device == "auto" else device


def supports_openvino(capabilities: dict, device: str) -> bool:
    requested = selected_openvino_device(device)
    entries = capabilities.get("openvino_devices", [])
    backends = capabilities.get("inference_backends", [])
    if not isinstance(backends, list):
        return False
    if not isinstance(entries, list):
        return False
    return (
        any(
            isinstance(entry, dict)
            and entry.get("available") is True
            and isinstance(entry.get("id"), str)
            and (
                entry["id"] == requested
                or (requested == "GPU" and re.fullmatch(r"GPU\.\d+", entry["id"]))
            )
            for entry in entries
        )
        and "openvino" in backends
    )


@lru_cache(maxsize=1)
def openvino_capabilities() -> dict[str, Any]:
    result: dict[str, Any] = {"inference_backends": ["pytorch"], "openvino_devices": []}
    try:
        result["openvino_version"] = version("openvino")
    except PackageNotFoundError:
        return result
    try:
        probe = subprocess.run(
            [sys.executable, "-m", "hnefatafl.inference_backends", "--probe"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if probe.returncode:
            raise RuntimeError(f"OpenVINO probe exited {probe.returncode}: {probe.stderr[-1000:]}")
        detected = json.loads(probe.stdout)
        result["openvino_devices"] = detected["devices"][:32]
        if any(entry["available"] for entry in result["openvino_devices"]):
            result["inference_backends"].append("openvino")
        if detected.get("error"):
            result["openvino_error"] = detected["error"][:1200]
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired, RuntimeError) as error:
        result["openvino_error"] = str(error)[:1200]
    return result


class TorchInference:
    def __init__(self, model: PolicyValueNet, config: dict):
        self.generation = 0
        self.model = model
        self.device = next(model.parameters()).device
        self.info = {
            "inference_backend": "pytorch",
            "inference_device": str(self.device),
            "optimization_device": str(self.device),
        }

    def forward(self, batch: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        # Shared policy inference owns the existing autocast boundary.
        return self.model(batch.to(self.device))

    def refresh(self) -> dict:
        self.generation += 1
        return {}

    def close(self) -> None:
        self.generation += 1


class OpenVINOInference:
    """One bounded dynamic-batch FP32 graph per weight snapshot; no batch cache.

    Convert an independent CPU copy through TorchScript, avoiding torch.export.
    Replacement is atomic: a failed refresh leaves the previous graph intact.
    """

    def __init__(self, model: PolicyValueNet, config: dict):
        if next(model.parameters()).device.type != "cpu":
            raise ValueError("OpenVINO inference requires PyTorch CPU optimization; set device=cpu")
        try:
            import openvino as ov
        except ImportError as error:
            raise ValueError(
                "OpenVINO is unavailable; install with uv sync --extra train --extra openvino "
                f"({error})"
            ) from error
        self.ov = ov
        self.generation = 0
        self.model = model
        self.device = torch.device("cpu")  # Shared legal-policy postprocessing.
        self.target = selected_openvino_device(config.get("inference_device", "auto"))
        self.cpu_threads = config.get("cpu_threads", 2)
        self.maximum = min(
            config["inference_batch_size"], config["self_play_concurrency"], config["games"]
        )
        self.core = ov.Core()
        available = list(self.core.available_devices)
        if self.target not in available and not (
            self.target == "GPU" and any(re.fullmatch(r"GPU\.\d+", item) for item in available)
        ):
            raise ValueError(f"OpenVINO device {self.target} unavailable; detected {available}")
        self.info = {
            "inference_backend": "openvino",
            "inference_device": self.target,
            "inference_device_name": str(self.core.get_property(self.target, "FULL_DEVICE_NAME")),
            "inference_precision": "float32",
            "optimization_device": "cpu",
            "evaluation_inference_backend": "pytorch",
            "evaluation_inference_device": "cpu",
        }
        self.compiled = None
        self.request = None
        self.refresh_count = 0

    def refresh(self) -> dict:
        started = time.perf_counter()
        # Clone storage as well as modules: optimization must never mutate the
        # constants retained by a converted inference graph.
        snapshot = deepcopy(self.model).cpu().float().eval()
        sample = torch.zeros((2, 6, snapshot.size, snapshot.size))
        with torch.inference_mode():
            traced = torch.jit.trace(snapshot, sample, check_trace=True)
            graph = self.ov.convert_model(traced, example_input=sample)
        graph.reshape(
            {
                graph.input(0): self.ov.PartialShape(
                    [self.ov.Dimension(1, self.maximum), 6, snapshot.size, snapshot.size]
                )
            }
        )
        converted = time.perf_counter()
        properties = {"INFERENCE_PRECISION_HINT": "f32", "PERFORMANCE_HINT": "LATENCY"}
        if self.target == "CPU":
            properties["INFERENCE_NUM_THREADS"] = self.cpu_threads
        compiled = self.core.compile_model(graph, self.target, properties)
        request = compiled.create_infer_request()
        # Validate compilation and materialize shape kernels outside search deadlines.
        validation = encode_state(new_game(snapshot.ruleset)).unsqueeze(0)
        output = request.infer({0: validation.numpy()})
        with torch.inference_mode():
            expected = snapshot(validation)
        for index, reference in enumerate(expected):
            actual = torch.from_numpy(output[compiled.output(index)].copy())
            if actual.shape != reference.shape or not torch.allclose(
                actual, reference, atol=3e-5, rtol=1e-4
            ):
                raise ValueError("OpenVINO FP32 conversion failed policy/value parity validation")
        finished = time.perf_counter()
        self.compiled, self.request = compiled, request
        self.refresh_count += 1
        self.generation += 1
        return {
            "inference_conversion_seconds": converted - started,
            "inference_compilation_seconds": finished - converted,
            "inference_refresh_seconds": finished - started,
            "inference_refresh_count": self.refresh_count,
        }

    def forward(self, batch: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        if self.request is None or self.compiled is None:
            raise RuntimeError("OpenVINO model must be refreshed before inference")
        if batch.ndim != 4 or not 1 <= batch.shape[0] <= self.maximum:
            raise ValueError("Inference batch exceeds configured OpenVINO bounds")
        output = self.request.infer({0: batch.cpu().contiguous().numpy()})
        # Copy the reusable request buffers before any subsequent inference.
        logits = torch.from_numpy(output[self.compiled.output(0)].copy())
        values = torch.from_numpy(output[self.compiled.output(1)].copy())
        if logits.shape != (batch.shape[0], self.model.size**4) or values.shape != (
            batch.shape[0],
        ):
            raise ValueError("OpenVINO returned incompatible policy/value shapes")
        if not torch.isfinite(logits).all() or not torch.isfinite(values).all():
            raise ValueError("OpenVINO returned non-finite policy/value outputs")
        return logits, values

    def close(self) -> None:
        self.generation += 1
        self.request = None
        self.compiled = None


InferenceBackend = TorchInference | OpenVINOInference


def create_inference(model: PolicyValueNet, config: dict) -> InferenceBackend:
    if config.get("inference_backend", "pytorch") == "openvino":
        return OpenVINOInference(model, config)
    return TorchInference(model, config)


def _probe() -> dict:
    import openvino as ov

    torch.set_num_threads(1)
    core = ov.Core()
    devices = []
    for identifier in list(core.available_devices)[:32]:
        if not _DEVICE.fullmatch(identifier):
            continue
        entry = {
            "id": identifier,
            "name": str(core.get_property(identifier, "FULL_DEVICE_NAME"))[:200],
            "available": False,
        }
        try:
            backend = OpenVINOInference(
                PolicyValueNet("brandubh", 4, 0),
                {
                    "inference_device": identifier,
                    "games": 2,
                    "inference_batch_size": 2,
                    "self_play_concurrency": 2,
                },
            )
            backend.refresh()
            backend.forward(torch.zeros((2, 6, 7, 7)))
            backend.close()
            entry["available"] = True
        except Exception as error:
            entry["error"] = str(error)[:500]
        devices.append(entry)
    return {"devices": devices}


if __name__ == "__main__":
    try:
        print(json.dumps(_probe(), allow_nan=False))
    except Exception as error:
        print(json.dumps({"devices": [], "error": str(error)[:1200]}))
