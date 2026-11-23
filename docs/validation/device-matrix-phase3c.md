# Physical browser and WebGPU validation matrix — Phase 3C

This runbook is for owner-run physical-device evidence. Mobile emulation is not
physical mobile evidence. Do not weaken provider or CSP requirements to obtain a
run.

## Required record fields

For each executed platform record:

- platform identifier (`windows-chrome`, `windows-edge`, `linux-chromium`,
  `linux-firefox`, `android-chrome`, `safari`, `other`);
- OS and version, browser and version, CPU, RAM;
- requested provider (`wasm`/`webgpu`);
- observed placement: `wasm`, `cpu-fallback`, `webgpu-session-unverified`,
  `webgpu-mixed`, `webgpu-session-verified`;
- explicit GPU verification evidence (per-operator placement) before using
  `webgpu-session-verified`;
- model architecture, model SHA-256, ONNX SHA-256;
- cold/warm initialization, first/warm inference, end-to-end MCTS simulations/s,
  cancellation/recovery result;
- optional sustained-memory profile reference.

Unaavailable platforms must be committed as `status: blocked` with a reason.
Validate with:

```sh
.venv/bin/python spikes/browser_offline/device_evidence.py evidence.json \
  --output evidence-validation.json
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
# WASM/CPU staging and checks
.venv/bin/python spikes/browser_offline/prepare_neural.py \
  --model-dir /tmp/export --output /tmp/device-spike-wasm --wasm-only
node spikes/browser_offline/neural_parity.mjs /tmp/device-spike-wasm \
  --provider wasm --output /tmp/wasm-parity.json
node spikes/browser_offline/test_mcts_ui.mjs /tmp/device-spike-wasm

# WebGPU staging (includes ort.webgpu.min.js and JSEP assets)
.venv/bin/python spikes/browser_offline/prepare_neural.py \
  --model-dir /tmp/export --output /tmp/device-spike-webgpu
test -f /tmp/device-spike-webgpu/ort/ort.webgpu.min.js
test -f /tmp/device-spike-webgpu/ort/ort-wasm-simd-threaded.jsep.mjs
test -f /tmp/device-spike-webgpu/ort/ort-wasm-simd-threaded.jsep.wasm
node spikes/browser_offline/neural_parity.mjs /tmp/device-spike-webgpu \
  --provider webgpu --require-webgpu --output /tmp/webgpu.json
node spikes/browser_offline/neural_parity.mjs /tmp/device-spike-webgpu \
  --provider webgpu --require-gpu-execution --output /tmp/gpu-execution.json
node spikes/browser_offline/neural_benchmark.mjs /tmp/device-spike-webgpu \
  --provider webgpu --output /tmp/webgpu-benchmark.json
node spikes/browser_offline/test_neural_ui.mjs /tmp/device-spike-webgpu
```

`--require-gpu-execution` intentionally fails when the pinned ORT Web build
cannot prove per-operator GPU placement. Retain `webgpu-session-unverified` unless
independently verified.
