# Browser-side analysis roadmap

Canonical roadmap for high-strength browser-side Hnefatafl analysis. It tracks
every phase, its scope, dependencies, acceptance criteria, current status and
validation evidence. Update this document in every PR that touches browser-local
analysis and include the phase table in the pull request description.

Related documents: [browser prototype feasibility](../validation/browser-analysis-prototype.md) ·
[browser neural feasibility](../validation/browser-neural-feasibility.md) ·
[Phase 1 validation](../validation/browser-analysis-phase1.md) ·
[isolated spike](../../spikes/browser_offline/README.md) ·
[documentation index](../README.md).

## Status definitions

| Status | Meaning |
| --- | --- |
| Not started | No implementation work has begun. |
| In progress | Work is actively underway, incomplete. |
| Implemented but not validated | Code exists but has no acceptance evidence. |
| Partially validated | Some acceptance criteria are met; others are open. |
| Completed | All acceptance criteria for the phase are met and evidenced. |
| Blocked | An external dependency (hardware, weights, authorization) prevents progress. |
| Regression detected | Previously passing evidence no longer holds. |

Implementation completion is not acceptance. A passing unit test does not
establish browser compatibility, runtime performance, trained-model strength or
production readiness.

## Phase status

| Phase | Scope | Status |
| --- | --- | --- |
| 0 | Browser engine feasibility | Completed (historical, externally reproduced) |
| 1A | Full-strength Jarl and native/browser performance comparison | Partially validated |
| 1B | Trained-model ONNX export and browser inference | Partially validated (trained representative weights and physical WebGPU blocked) |
| 2 | Browser-side neural MCTS | Not started (design only) |
| 3 | Search optimization, strength evaluation, physical devices | Not started |
| 4 | Production security, model distribution, runtime integration | Not started |
| 5 | Frontend analysis UI integration | Not started |
| 6 | Final acceptance, regression testing, controlled rollout | Not started |

## Phase 0 — Browser engine feasibility

- **Objective:** establish that the existing Python engine can run in a browser
  worker without a divergent JavaScript rules engine.
- **Status:** Completed and independently reproduced by the project owner on WSL
  (desktop and mobile-emulated profiles). Historical evidence lives in
  [browser-analysis-prototype.md](../validation/browser-analysis-prototype.md) and
  [browser-offline-phase0.json](../validation/browser-offline-phase0.json); it is not
  recast as new validation.
- **Baseline measurements (externally supplied, project owner):** Brandubh
  baseline 1.0 ms / alpha-beta depth 2 34.1 ms / MCTS 40.3 ms; Tablut 2.3 / 31.4 /
  79.7 ms; Copenhagen 7.1 / 69.7 / 206.6 ms. Runtime startup 1,019.2 ms
  (1,308.2 ms offline); cancellation ack 0.8 ms; worker restart 1,039.6 ms;
  20 MiB WASM heap; 136 fixture comparisons; no external network requests.
  Mobile-emulated results are **not** physical mobile evidence.

## Phase 1A — Full-strength Jarl and native/browser comparison

- **Objective:** execute the production-equivalent Jarl agent in the isolated
  browser worker, compare it with native CPython on identical workloads, and
  report performance and correctness.
- **Dependencies:** Phase 0 runtime staging; the production `alpha_beta`
  catalog preset in `hnefatafl/ai.py`.
- **Status:** Partially validated.
- **Delivered:**
  - Production-equivalent Jarl preset exposed in the spike: depth 5, 2 s
    cooperative deadline, Shield maiden evaluation and the optimized search
    defaults (transposition, move ordering, PV/tt/killer/history ordering).
  - Named benchmark workloads: quick (depth 3/250 ms), interactive (5/1 s),
    standard (5/2 s), deep (8/5 s), extended (10/10 s).
  - Shared `jarl_benchmark.py` runner used identically by native CPython and the
    Pyodide worker, plus `native_jarl_benchmark.py` and `verify_jarl.mjs`.
  - Extended experimental bounds: depth 1–8, 4096 simulations, 50 rollout plies,
    10 s cooperative deadline. The hard client watchdog stays separate from the
    cooperative deadline (5 s ordinary, up to 180 s opt-in for benchmark jobs).
- **Engine constraint:** the production alpha-beta engine caps depth at 8
  (`hnefatafl/ai.py`); the public configuration validator caps it at 5. The
  `extended` (depth 10) workload is therefore **not** silently reduced: it is
  reported per case as `ValueError: Search depth must be between 1 and 8`.
- **Validation evidence (reproduced by the implementation agent, 2026-11-19):**
  - Deterministic **fixed-work depth-3** searches across all three rulesets and
    seven position kinds (opening, midgame, endgame, tactical, king-escape,
    repetition, terminal): **21/21 best-move agreement, 21/21 value agreement**
    between native CPython and browser Pyodide.
  - Deterministic **fixed-work depth-5** (production depth) Brandubh:
    **7/7 best-move agreement, 7/7 value agreement**; browser ≈2.1–2.4× native
    wall-clock at equal completed work.
  - Browser startup ≈2.07 s; cancellation ack ≈0.2–0.3 ms; worker restart
    ≈1.7–1.8 s; UI click during an active search ≈0.2–0.3 ms; 20 MiB WASM heap.
  - [Native quick report](../validation/browser-analysis-jarl-native-2026-11-19.json) ·
    [browser quick report](../validation/browser-analysis-jarl-browser-2026-11-19.json) ·
    [native depth-5 report](../validation/browser-analysis-jarl-native-depth5-2026-11-19.json) ·
    [browser depth-5 report](../validation/browser-analysis-jarl-browser-depth5-2026-11-19.json).
- **Open criteria:** Tablut/Copenhagen fixed-work at depth 5+; fixed-time
  workload comparisons with completed-depth accounting; physical mobile and
  low-end device latency; deadline-overshoot percentiles over repeated runs.
- **Blockers:** none. Physical device validation belongs to Phase 3.

## Phase 1B — Trained-model ONNX export and browser inference

- **Objective:** export an actual trained policy/value checkpoint to
  inference-only ONNX, establish native parity, and execute it in a browser via
  ORT Web WASM and WebGPU with explicit capability reporting.
- **Dependencies:** Phase 1A worker boundary; an exported model; optional pinned
  exporter/runtime environment (`spikes/browser_offline/requirements-onnx.txt`).
- **Status:** Partially validated. Trained representative (32×2) weights were not
  available, and WebGPU has no adapter in the development environment.
- **Delivered:**
  - `onnx_export.py`: reproducible export from the validated tensor-only
    checkpoint loader to inference-only ONNX (opset 18, dynamic batch), with an
    architecture metadata sidecar (ruleset, shapes, action indexing, value
    perspective, legal masking, SHA-256, exporter/runtime versions).
  - `parity()` native PyTorch ↔ ONNX Runtime CPU comparison of logits, value,
    legal-only normalized policy, top-5 ranking and illegal-action mass.
  - `prepare_neural.py`: digest-verified ORT Web 1.22.0 staging (WASM only or
    WASM+WebGPU), model copy and serialized input staging.
  - `neural-worker.js` / `neural-client.js` / `neural.html`: ORT Web inference
    with single-threaded WASM fallback (no COOP/COEP) and a WebGPU path that
    fails explicitly when no adapter is present.
  - `neural_benchmark.mjs`: browser benchmark across batch sizes 1–32 that
    reports the provider actually requested and never labels a WASM session GPU.
- **Validation evidence (implementation agent, 2026-11-19):**
  - Actual trained **Brandubh** smoke checkpoint (channels 4, blocks 0) exported
    to a 46,924-byte ONNX graph with `onnx.checker` and shape inference passing.
  - Native PyTorch ↔ ONNX Runtime CPU parity over six non-terminal positions:
    worst logit max-abs error 1.43e-6, value 5.96e-8, legal-policy relative error
    2.76e-7, top-5 ranking identical.
  - Browser ORT Web **WASM** inference of the actual exported model succeeded
    (legal-only probabilities normalise to 1); batch 1–32 medians measured.
  - Browser **WebGPU** request failed explicitly with "no adapter is available"
    and reported `provider_is_gpu: false`; no silent CPU fallback.
  - [ONNX export/parity](../validation/browser-neural-onnx-export-2026-11-19.json) ·
    [browser WASM](../validation/browser-neural-wasm-2026-11-19.json) ·
    [browser WebGPU capability](../validation/browser-neural-webgpu-2026-11-19.json).
- **Open criteria / blockers:**
  - Representative competitive (32×2) trained weights for all three rulesets are
    **not available** in this environment. Export/parity/browser tooling is
    validated only with a tiny trained smoke checkpoint. Actual trained-model
    acceptance remains **blocked** pending the owner's checkpoints.
  - WebGPU execution is **unvalidated** because the development host has no
    adapter. Independent physical-device validation (Windows Chrome/Edge, Intel,
    NVIDIA RTX 3060, Linux, Firefox WASM, Android Chrome, Safari) is required
    before any GPU claim.
  - Model/runtime download budgets, transfer sizes and long-run memory growth are
    not yet measured.

## Phase 2 — Browser-side neural MCTS (subsequent PR)

- **Status:** Not started. Phase 1B deliberately exposes a single-shot batched
  inference interface (batch sizes 1–32) so a future MCTS can request batched
  leaf inference. No independent MCTS engine is implemented.
- **Design constraints:** reuse existing tree traversal/expansion/selection/
  backpropagation; evaluate an asynchronous Python-search/browser-inference
  boundary; avoid per-node serialization and blocking synchronization.
- **Acceptance:** completed simulations (128–4096) measured against 0.5–10 s
  budgets; 1,024 simulations in ≈2 s is an aspirational target, not a claim.

## Phase 3 — Optimization and strength evaluation (subsequent PRs)

- **Status:** Not started.
- **Scope:** profiling-justified optimizations, persistent worker/ORT session
  reuse, exact neural-inference and bounded search-result caches with separate
  identity, batched leaf inference, cooperative cancellation, MCTS subtree reuse.
- **Strength tournaments:** browser Jarl vs native Jarl; browser neural MCTS vs
  native neural MCTS; browser neural MCTS vs native Jarl; 256/512 vs 1024; WebGPU
  vs WASM; batched vs sequential. Balanced roles, paired openings, seeds and
  statistical uncertainty. Fixed-time and fixed-iteration reported separately.

## Phase 4 — Production security and runtime integration (subsequent PRs)

- **Status:** Not started.
- **Scope:** same-origin pinned/integrity-verified runtime and model delivery,
  dependency auditing and licensing/notices, explicit model publication
  authorization, size/memory budgets, storage quotas/eviction, interrupted
  downloads, cache invalidation, worker exhaustion, account/replay isolation,
  logout cleanup, offline behavior, deployment/rollback.
- **CSP note:** the current policy blocks WASM compilation; any adjustment must be
  narrowly scoped, reviewed and tested against real application headers. No broad
  `unsafe-eval`, no COOP/COEP without evaluating application impact. Server
  authority over games, clocks, results, ratings and tournaments is preserved.

## Phase 5 — Frontend analysis UI integration (subsequent PRs)

- **Status:** Not started.
- **Scope:** normalized `Auto`/`Local`/`Server`/`Disabled` execution modes,
  lazy-loaded local execution, no eager bundle growth, no automatic runtime/model
  download on public spectator pages, no silent agent/strength substitution, and
  reuse of existing design tokens and components.

## Phase 6 — Final production acceptance

- **Status:** Not started.
- **Scope:** strength preservation, parity, resource limits, device/browser
  compatibility, theme/language integration, permission isolation, model
  distribution security, download/init experience, server fallback, deployment and
  regression resistance. The feature stays opt-in until acceptance is met.

## Sub-status matrix

| Area | Status |
| --- | --- |
| Brandubh | Validated (depth 3 and 5 native/browser Jarl agreement; ONNX + browser WASM smoke model) |
| Tablut | Partially validated (depth 3 Jarl agreement; no trained model) |
| Copenhagen | Partially validated (depth 3 Jarl agreement; no trained model) |
| Native CPython | Validated for benchmark and ONNX parity |
| Browser Pyodide | Validated for Jarl depth 3/5 deterministic agreement |
| Native ONNX inference | Validated (CPU parity on a trained smoke checkpoint) |
| Browser WASM inference | Validated (actual exported smoke model, batch 1–32) |
| Browser WebGPU inference | Blocked (no adapter on the development host) |
| Physical desktop hardware | Blocked (development host is a headless Linux container) |
| Physical mobile hardware | Blocked |
| Playing strength | Not started |
| Production security | Not started |
| Frontend integration | Not started |

## Open findings and technical debt

- Browser Pyodide is consistently ≈2.1–2.4× slower than native CPython at equal
  completed work; this is expected overhead, not a regression.
- Fixed-time searches complete different depths on the two runtimes, so
  latency-only comparisons must not be reported as speedups.
- `verify_jarl.mjs` default suite is bounded to the quick workload; standard and
  extended suites are opt-in because they run for minutes.
- The neural `provider_is_gpu` flag reflects a session created with the WebGPU
  execution provider after an adapter was confirmed. It does not prove that GPU
  kernels executed; that requires physical-device validation.
- The browser neural worker loads a single ORT bundle; switching from WASM to
  WebGPU in one worker is rejected and requires recreating the worker.
- No trained representative weights are committed; the spike never downloads a
  model from the network.

## Recommended next tasks

1. Provide representative trained checkpoints (32×2 and larger) for all three
   rulesets and run the documented export/parity/browser commands.
2. Run the WebGPU benchmark on physical Windows/Linux/Android/Safari hardware and
   record explicit capability and provider reports.
3. Extend the Jarl benchmark to Tablut/Copenhagen fixed-work depth 5+ and to
   fixed-time completed-depth comparison with percentiles.
4. Design the asynchronous Python-search/browser-inference boundary for Phase 2
   neural MCTS.
5. Investigate a narrow, reviewed CSP allowance for WASM as part of Phase 4.

## Reproducibility

```sh
# Environment
uv sync --frozen --extra train --extra dev
uv pip install --python .venv/bin/python -r spikes/browser_offline/requirements-onnx.txt

# Native Jarl benchmark
.venv/bin/python spikes/browser_offline/native_jarl_benchmark.py \
  --suite quick --mode fixed_work --output /tmp/jarl-native.json

# Stage the Pyodide spike and run the browser Jarl benchmark
.venv/bin/python spikes/browser_offline/prepare.py --output /tmp/hnefatafl-analysis
node spikes/browser_offline/verify_jarl.mjs /tmp/hnefatafl-analysis \
  --suite quick --mode fixed_work --native /tmp/jarl-native.json \
  --output /tmp/jarl-browser.json
.venv/bin/python spikes/browser_offline/compare_jarl_benchmarks.py \
  /tmp/jarl-native.json /tmp/jarl-browser.json

# Export an owner-supplied checkpoint and check native parity
.venv/bin/python spikes/browser_offline/onnx_export.py /path/to/last.pt /tmp/model-export

# Stage and run browser neural inference (WASM, then WebGPU on capable hardware)
.venv/bin/python spikes/browser_offline/prepare_neural.py \
  --model-dir /tmp/model-export --output /tmp/neural-spike
node spikes/browser_offline/neural_benchmark.mjs /tmp/neural-spike \
  --provider wasm --output /tmp/neural-wasm.json
node spikes/browser_offline/neural_benchmark.mjs /tmp/neural-spike \
  --provider webgpu --require-webgpu --output /tmp/neural-webgpu.json
```

## Status history

| Date | Change |
| --- | --- |
| 2026-11-19 | Roadmap created; Phase 1A and Phase 1B implemented and partially validated. |
