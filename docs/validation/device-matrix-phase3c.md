# Physical browser and WebGPU validation matrix — Phase 3C

This runbook is for owner-run physical-device evidence. Mobile emulation is not
physical mobile evidence. Do not weaken provider or CSP requirements to obtain a
run.

## Record schema (`browser-device-evidence`, schema 2)

Each matrix entry is unique by `(platform, provider_requested)`. Unavailable
entries are `status: blocked` with a reason; a missing entry fails structural
validation.

```json
{
  "platform": "windows-chrome",
  "status": "measured",
  "browser_version": "Chromium 131",
  "os": "Windows 11",
  "cpu": "Intel i7-12700",
  "ram_bytes": 34359738368,
  "runtime": "ort-web-1.22",
  "architecture": "brandubh-32x2",
  "model_sha256": "<64 hex>",
  "onnx_sha256": "<64 hex>",
  "workload": "brandubh-opening",
  "mcts_budget": 256,
  "cancellation_recovery": true,
  "recovery_evidence": {
    "cancel_acknowledged": true,
    "restart_completed": true,
    "post_cancel_inference": true
  },
  "provider_requested": "webgpu",
  "execution_placement": "webgpu-session-verified",
  "gpu_execution_verified": true,
  "cold_init_ms": 420.5,
  "warm_init_ms": 12.4,
  "first_inference_ms": 41.2,
  "warm_inference_ms": 4.8,
  "mcts_simulations_per_second": 212.3,
  "gpu_evidence": {
    "evidence_source": "ort-web-per-operator-v1",
    "all_operators_covered": true,
    "gpu_execution_verified": true,
    "operator_inventory": ["Conv", "MatMul", "Softmax"],
    "session": {
      "model_sha256": "<64 hex>",
      "onnx_sha256": "<64 hex>",
      "runtime": "ort-web-1.22",
      "workload": "brandubh-opening",
      "architecture": "brandubh-32x2",
      "provider_requested": "webgpu"
    },
    "per_operator_placement": [
      {"operator": "Conv", "provider": "webgpu"},
      {"operator": "MatMul", "provider": "webgpu"},
      {"operator": "Softmax", "provider": "webgpu"}
    ]
  }
}
```

Observed placement vocabulary: `wasm`, `cpu-fallback`,
`webgpu-session-unverified`, `webgpu-mixed`, `webgpu-session-verified`.

Acceptance semantics:

| Result | Meaning |
| --- | --- |
| `structural_ok` | Every measured record is complete, unique and self-consistent. |
| `measurement_acceptance` | Every mandatory combination is measured and honours its requested provider; an explicit CPU fallback is a measurement but fails a WebGPU entry. |
| `webgpu_session_acceptance` | Every mandatory WebGPU entry actually created a WebGPU session (unverified or verified). |
| `verified_gpu_acceptance` | Every mandatory WebGPU entry has independent per-operator placement covering the declared inventory plus matching model/runtime/workload/session identity. |
| `acceptance` | Requires all of the above. |

`all_operators_covered: true` is never trusted alone; the inventory must be
covered exactly. `webgpu-mixed` and `webgpu-session-unverified` cannot satisfy the
verified-GPU gate, and `cpu-fallback` cannot satisfy a WebGPU gate. The pinned ORT
Web runtime exposes no supported per-operator diagnostic, so verified-GPU
acceptance remains **blocked** even when all physical sessions are measured.

Validate with:

```sh
.venv/bin/python spikes/browser_offline/device_evidence.py evidence.json \
  --output evidence-validation.json
# exit 0 = verified-GPU acceptance, 2 = structurally valid but acceptance blocked,
# 1 = invalid evidence
```

## Matrix

| Platform | Provider | Cold init | Warm inference | MCTS sims/s | GPU verified | Status |
| --- | --- | --- | --- | --- | --- | --- |
| Windows Chrome/Edge | WASM | pending | pending | pending | n/a | Blocked: no hardware |
| Windows Chrome/Edge | WebGPU | pending | pending | pending | requires evidence | Blocked: no hardware |
| Linux Chromium | WASM | pending | pending | pending | n/a | Blocked: owner run |
| Linux Firefox | WASM | pending | pending | pending | n/a | Blocked: owner run |
| Android Chrome (physical) | WASM/WebGPU | pending | pending | pending | requires evidence | Blocked: no device |
| Safari (supported Apple) | WASM/WebGPU | pending | pending | pending | requires evidence | Blocked: no device |

## Commands

WASM-only staging intentionally omits the WebGPU entry bundle. Stage a separate
directory without `--wasm-only` for WebGPU tests; the pinned all-in-one WASM
bundle still needs the staged JSEP kernels.

```sh
# Shared preparation: validated export + staged runtime
.venv/bin/python spikes/browser_offline/onnx_export.py /private/last.pt /tmp/export
.venv/bin/python spikes/browser_offline/prepare_neural.py \
  --model-dir /tmp/export --output /tmp/device-spike-wasm --wasm-only
.venv/bin/python spikes/browser_offline/prepare_neural.py \
  --model-dir /tmp/export --output /tmp/device-spike-webgpu
test -f /tmp/device-spike-webgpu/ort/ort.webgpu.min.js

# WASM/CPU checks
node spikes/browser_offline/neural_parity.mjs /tmp/device-spike-wasm \
  --provider wasm --output /tmp/wasm-parity.json
node spikes/browser_offline/mcts_benchmark.mjs /tmp/device-spike-wasm \
  --provider wasm --quick --native /tmp/native-bench.json \
  --output /tmp/wasm-bench.json
node spikes/browser_offline/test_mcts_ui.mjs /tmp/device-spike-wasm

# WebGPU capability/parity (fails explicitly without an adapter)
node spikes/browser_offline/neural_parity.mjs /tmp/device-spike-webgpu \
  --provider webgpu --require-webgpu --output /tmp/webgpu-parity.json
node spikes/browser_offline/neural_parity.mjs /tmp/device-spike-webgpu \
  --provider webgpu --require-gpu-execution   # remains blocked without evidence

# Browser tournament adapter on the same staged session
node spikes/browser_offline/browser_tournament_agent.mjs /tmp/device-spike-wasm \
  --provider wasm   # speaks the persistent JSON line protocol on stdin/stdout

# Sustained-memory collection while the browser workload runs
.venv/bin/python spikes/browser_offline/memory_profile_collect.py \
  --pid "$BROWSER_PID" --profile sustained-10m --duration 600 --interval 1 \
  --progress-file /tmp/completed-requests.txt \
  --js-heap-file /tmp/js-heap.jsonl --cache-bytes-file /tmp/cache-bytes.jsonl \
  --output /tmp/memory-profile.json
.venv/bin/python spikes/browser_offline/memory_validation.py \
  /tmp/memory-profile.json --output /tmp/memory-validation.json

# Finally assemble the matrix JSON above and validate it
.venv/bin/python spikes/browser_offline/device_evidence.py /tmp/device-evidence.json \
  --output /tmp/device-validation.json
```

`--pid` must name the browser process-tree root. `--progress-file` must be written
(integer, or `{"completed_requests": N}`) by the workload; a stale counter produces
an idle-workload rejection rather than a fabricated pass.

Unavailable platforms must be committed as `status: blocked` with a reason. The
matrix must contain every mandatory `(platform, provider)` entry exactly once.
