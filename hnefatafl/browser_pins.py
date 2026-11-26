"""Pinned browser-analysis dependency and artifact policy.

One source of truth for the versions, digests, staged module lists and resource
budgets used by:

* :mod:`hnefatafl.browser_runtime` (distribution and verification),
* :mod:`hnefatafl.browser_models` (publication compatibility),
* the isolated browser spike staging tools.

Updating a pin requires updating its digest and the browser regression evidence;
the runtime and the published-model manifests bind these values so an
incompatible pair cannot activate.
"""

from __future__ import annotations

from pathlib import Path

# ---------------------------------------------------------------- runtime pins
PYODIDE_VERSION = "0.27.7"
PYODIDE_CDN = "https://cdn.jsdelivr.net/pyodide/v{version}/full/{name}"
PYODIDE_ASSETS: dict[str, str] = {
    "pyodide.js": "b4cb23a53aba19c221659b9fb40a2f18281d685691dc06647fb0afd0681baf98",
    "pyodide.asm.js": "6b4c90de5b7172873f04f21884d0e9d2274e305fe32116558fe3e4fbe3618d51",
    "pyodide.asm.wasm": "a50dd1843f805a0b7c45b61037ee0d7b26dfe85efe0e18ef95a34ad24e401f5f",
    "python_stdlib.zip": "16611534726e5d8ac2bd8f926410b2dcb8d6f49aa24913463533b457a2115c16",
    "pyodide-lock.json": "9c45b916001a750f4102fc287494f3eab215909c7535c626a20db80fd6333e2c",
}

ORT_VERSION = "1.22.0"
ORT_CDN = "https://cdn.jsdelivr.net/npm/onnxruntime-web@{version}/dist/{name}"
ORT_ASSETS: dict[str, str] = {
    "ort.min.js": "924e4fcdfdf69acccff3d024d6e1ad3f41ca858d27fc4dd59fc4927dd933e7db",
    "ort.webgpu.min.js": "4dec2f5e74de2ec0f496d39ef55ec23762e8c6ca58cb958e691c9f2350a7b1c8",
    "ort-wasm-simd-threaded.mjs": "30dd851d9c00622940500f71ddd2ff8820c5cb65270816080175b958705385a8",
    "ort-wasm-simd-threaded.wasm": "71aef04959c5c1b6de461b6538e2058e306610034a85aad2742d0c7fd4533fe4",
    "ort-wasm-simd-threaded.jsep.mjs": "1cbcba8f2c769c1eecbab66a1b1e55ef11704515bf4306373e3db3c37cf6dcd8",
    "ort-wasm-simd-threaded.jsep.wasm": "b45970d0632383a057c27ca5b660b216f8e00c17cf8db9f6207b5e4abc839368",
}
# The pinned ``ort.min.js`` bundle resolves the jsep kernels dynamically even for
# a WASM-only session, so omitting the jsep files breaks session creation.
ORT_WASM_ONLY = frozenset(
    {
        "ort.min.js",
        "ort-wasm-simd-threaded.mjs",
        "ort-wasm-simd-threaded.wasm",
        "ort-wasm-simd-threaded.jsep.mjs",
        "ort-wasm-simd-threaded.jsep.wasm",
    }
)

# Explicit stdlib-only closure of the engine, the shared encoder and the
# cooperative search driver. Torch, NumPy and ONNX never enter the runtime.
ENGINE_MODULES: tuple[str, ...] = (
    "__init__.py",
    "engine.py",
    "ai.py",
    "openings.py",
    "symmetry.py",
    "tournament_statistics.py",
    "shield_maiden.py",
    "encoding.py",
    "mcts_driver.py",
    "browser_engine_identity.py",
)

# Production bridge module -> staged root module name. ``worker.js`` and
# ``search-worker.js`` load these names directly.
ROOT_MODULES: dict[str, str] = {
    "bridge.py": "browser_bridge.py",
    "search_bridge.py": "browser_search_bridge.py",
}

# Validated browser worker/client implementation. The spike's test-only files
# (``neural-worker-test.js``, fixtures, benchmark harnesses) are never staged.
SPIKE_RUNTIME_JS = Path("spikes/browser_offline")
JS_ASSETS: tuple[str, ...] = (
    "worker.js",
    "analysis-client.js",
    "search-worker.js",
    "search-client.js",
    "search-assets.js",
    "neural-worker.js",
    "neural-client.js",
    "neural-identity.js",
    "neural-provider.js",
    "neural-tensors.js",
)

# Mandatory runtime inventory. Kept in sync with the TypeScript contract by a
# regression test; a self-consistent manifest that omits one of these entries is
# rejected even if its own digests agree.
REQUIRED_RUNTIME_FILES: tuple[str, ...] = (
    "runtime/pyodide.js",
    "runtime/pyodide.asm.js",
    "runtime/pyodide.asm.wasm",
    "runtime/python_stdlib.zip",
    "runtime/pyodide-lock.json",
    "ort/ort.min.js",
    "ort/ort-wasm-simd-threaded.mjs",
    "ort/ort-wasm-simd-threaded.wasm",
    "ort/ort-wasm-simd-threaded.jsep.mjs",
    "ort/ort-wasm-simd-threaded.jsep.wasm",
    "python/hnefatafl/engine.py",
    "python/hnefatafl/encoding.py",
    "python/hnefatafl/mcts_driver.py",
    "bridge.py",
    "search_bridge.py",
    "assets.json",
    "search-assets.json",
    "worker.js",
    "analysis-client.js",
    "search-worker.js",
    "search-client.js",
    "neural-client.js",
    "neural-worker.js",
)

# Required third-party license texts distributed with the runtime. These are
# source-controlled, not downloaded at build time, so reproduction is offline.
LICENSE_ASSETS: dict[str, str] = {
    "licenses/PYODIDE-MPL-2.0.txt": "hnefatafl/browser_licenses/PYODIDE-MPL-2.0.txt",
    "licenses/ONNXRUNTIME-MIT.txt": "hnefatafl/browser_licenses/ONNXRUNTIME-MIT.txt",
    "licenses/PYTHON-PSF-2.0.txt": "hnefatafl/browser_licenses/PYTHON-PSF-2.0.txt",
}

# ---------------------------------------------------------------- size budgets
MAX_RUNTIME_BYTES = 160 * 1024 * 1024
MAX_RUNTIME_FILES = 4096
MAX_RUNTIME_ASSET_BYTES = 64 * 1024 * 1024
MAX_MANIFEST_BYTES = 512 * 1024
MAX_MODEL_BYTES = 64 * 1024 * 1024
MAX_BRIDGE_BYTES = 512 * 1024
MAX_RUNTIME_MANIFEST_BYTES = MAX_MANIFEST_BYTES

LIMITS: dict[str, int] = {
    "max_runtime_bytes": MAX_RUNTIME_BYTES,
    "max_model_bytes": MAX_MODEL_BYTES,
    "max_bridge_payload_bytes": MAX_BRIDGE_BYTES,
    "max_runtime_manifest_bytes": MAX_RUNTIME_MANIFEST_BYTES,
    "max_active_searches": 2,
    "max_active_workers": 2,
    "max_inference_batch": 32,
}
