# Browser-side analysis roadmap

Canonical roadmap for high-strength browser-side Hnefatafl analysis. It tracks
every phase, its scope, dependencies, acceptance criteria, current status and
validation evidence. Update this document in every PR that touches browser-local
analysis and include the phase table in the pull request description.

Related: [Phase 1 validation](../validation/browser-analysis-phase1.md) ·
[Phase 0 feasibility](../validation/browser-analysis-prototype.md) ·
[browser neural feasibility](../validation/browser-neural-feasibility.md) ·
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
| 0 | Browser engine feasibility | Completed (historical, independently reproduced) |
| 1A | Full-strength Jarl and native/browser comparison | Partially validated |
| 1B | Trained-model ONNX export and browser inference | Partially validated (competitive weights and physical WebGPU blocked) |
| 2 | Browser-side neural MCTS | Not started (inference boundary prepared) |
| 3 | Search optimization, strength evaluation, physical devices | Not started |
| 4 | Production security, model distribution, runtime integration | Not started |
| 5 | Frontend analysis UI integration | Not started |
| 6 | Final acceptance, regression testing, controlled rollout | Not started |

## Phase 0 — Browser engine feasibility

- **Objective:** establish that the existing Python engine can run in a browser
  worker without a divergent JavaScript rules engine.
- **Status:** Completed and independently reproduced by the project owner on WSL
  (desktop and mobile-emulated profiles). Evidence:
  [browser-analysis-prototype.md](../validation/browser-analysis-prototype.md),
  [browser-offline-phase0.json](../validation/browser-offline-phase0.json). It is
  not recast as new validation.
- **Baseline (externally supplied):** Brandubh 1.0 / 34.1 / 40.3 ms; Tablut
  2.3 / 31.4 / 79.7 ms; Copenhagen 7.1 / 69.7 / 206.6 ms (baseline / depth-2
  alpha-beta / MCTS). Startup 1,019.2 ms (1,308.2 ms offline); cancellation ack
  0.8 ms; restart 1,039.6 ms; 20 MiB WASM heap capacity; 136 fixture comparisons;
  no external requests. Mobile emulation is not physical-mobile evidence.

## Phase 1A — Full-strength Jarl and native/browser comparison

- **Objective:** execute the production-equivalent Jarl agent in the isolated
  browser worker, compare it with native CPython on identical workloads, and
  report performance and correctness honestly.
- **Dependencies:** Phase 0 runtime staging; the `alpha_beta` catalog preset in
  `hnefatafl/ai.py`.
- **Status:** Partially validated.
- **Delivered:**
  - Production-equivalent Jarl preset: depth 5, 2 s cooperative deadline, Shield
    maiden evaluation, and the optimized search defaults (transposition, move
    ordering, PV/table/killer/history ordering).
  - Named workloads: quick (3 / 250 ms), interactive (5 / 1 s), standard (5 / 2 s),
    deep (8 / 5 s), extended (10 / 10 s).
  - One shared `jarl_benchmark.py` (positions, effective configs, workload
    identity, per-case records) used unchanged by native CPython and Pyodide.
  - One canonical comparison module `jarl_compare.py`; the native CLI, the
    browser verifier and the standalone comparator all use it.
  - `verify_jarl.mjs` analyses the exact `--positions` state objects (custom
    positions and same-name/different-board/position-history cases included),
    records the workload digest, runs one bounded request per case, preserves
    earlier records on failure, and exits non-zero on mandatory failures.
  - Per-request watchdog selection: 30 s initialization; 5 s ordinary job;
    analysis deadline + margin up to 20 s; 75 s per independently bounded
    benchmark case. No request can run indefinitely.
  - Explicit statuses: `completed`, `terminal`, `deadline_limited`,
    `unsupported`, `error`, `incomplete`.
- **Engine constraint:** the alpha-beta engine caps depth at 8 and the public
  validator at 5. `extended` (depth 10) is rejected by the engine and reported
  per case; it is never silently reduced.
- **Validation evidence (revision 2):**
  - Deterministic **fixed-work depth 3**, all three rulesets, seven position
    kinds: **21/21 comparable cases** agree on best move, value and depth.
  - Deterministic **fixed-work depth 5**, Brandubh: **7/7** agree.
  - Deterministic **fixed-work depth 5**, Tablut + Copenhagen: **11/11**
    comparable cases agree; **3** Copenhagen cases (opening, midgame, repetition)
    reach only depth 4 inside the 60 s safety limit **on native CPython too**, and
    are reported as `deadline_limited` rather than passed or failed.
  - Equal-work browser ÷ native median: 2.21× (quick), 2.12× (Brandubh depth 5),
    2.14× (Tablut+Copenhagen depth 5).
  - Startup 2.1–2.7 s; cancellation ack 0.2 ms; worker restart 2.1–2.3 s;
    click-to-handle 65–78 ms with 19–25 ms max event-loop lag during active
    search.
  - Evidence: [jarl-native-fixed-work-quick-r2.json](../validation/jarl-native-fixed-work-quick-r2.json),
    [jarl-browser-fixed-work-quick-r2.json](../validation/jarl-browser-fixed-work-quick-r2.json),
    [jarl-native-depth5-brandubh-r2.json](../validation/jarl-native-depth5-brandubh-r2.json),
    [jarl-browser-depth5-brandubh-r2.json](../validation/jarl-browser-depth5-brandubh-r2.json),
    [jarl-native-depth5-tablut-copenhagen-r2.json](../validation/jarl-native-depth5-tablut-copenhagen-r2.json),
    [jarl-browser-depth5-tablut-copenhagen-r2.json](../validation/jarl-browser-depth5-tablut-copenhagen-r2.json).
- **Superseded revision-1 evidence:** `*-2026-11-19.json` compared only the
  benchmark plan, selected positions by name and did not fail on fixed-work
  divergence. Those files are preserved as history and are not evidence for the
  corrected contract.
- **Open criteria:** Tablut fixed-time workload comparison; physical mobile and
  low-end device latency; deadline-overshoot percentiles over repeated runs.
- **Blockers:** none for depth 3/5 fixed-work; physical devices belong to Phase 3.

## Phase 1B — Trained-model ONNX export and browser inference

- **Objective:** export an actual trained policy/value checkpoint to
  inference-only ONNX, establish native and browser numerical parity, and execute
  it in a browser via ORT Web WASM/WebGPU with explicit capability reporting.
- **Dependencies:** Phase 1A worker boundary; a validated export; the optional
  pinned exporter environment (`requirements-onnx.txt`).
- **Status:** Partially validated. Smoke-model export, native parity and browser
  WASM numerical parity are validated; competitive trained weights and physical
  WebGPU are blocked.
- **Delivered:**
  - Transactional, fail-closed `onnx_export.py`: stages artifacts, validates the
    graph, computes native reference vectors, runs native parity, and publishes a
    `VALIDATED.json` marker only on success. `--no-parity` yields an unvalidated
    artifact that staging refuses by default. Unsafe output paths are refused and
    unrelated files are never deleted.
  - Metadata sidecar (ruleset, shapes, action indexing, value perspective, legal
    masking, SHA-256, exporter/runtime versions) and `reference.json` with full
    position identity, six-plane inputs, policy logits, defender values, legal
    indices, legal-only probabilities, shapes/dtypes and tolerances.
  - Canonical fail-closed comparator `neural_parity.py` (finite outputs, stable
    legal softmax, named outputs, per-case shapes, illegal masking, top-k
    agreement, material vs near-tied ranking changes, empty-reference rejection).
  - `prepare_neural.py` validates schema, artifact type, digest, size, ruleset,
    board size, input/output contract, dynamic batch and graph outputs before
    staging; it refuses unvalidated artifacts unless explicitly allowed.
  - Neural worker/client/page: digest-verified model bytes, explicit session and
    tensor ownership with release, genuine distinct-position batched inference
    (1–32), bounded/cancellable download, worker-generation lifecycle guards, and
    provider reporting that never labels WASM as GPU.
- **Validation evidence (revision 2, trained **smoke** model — not competitive):**
  - Native PyTorch → ORT CPU parity over 39 reference cases: worst logit abs
    1.43e-6, value 5.96e-8, zero material ranking changes.
  - Native PyTorch → browser ORT WASM parity over the same 39 cases: worst logit
    abs 2.15e-6, value 5.96e-8, legal-probability relative 9.9e-7, zero material
    ranking changes.
  - Deliberate corruption (one logit +0.05) fails the gate.
  - Distinct-position batched inference 1–32 matches independent single inference
    exactly.
  - Browser WASM: ORT+session init 474.0 ms, first inference 19.0 ms, warm run
    median 0.6 ms, model 46,924 B raw / 6,915 B gzip estimate.
  - Evidence: [neural-export-manifest-wasm-parity.json](../validation/neural-export-manifest-wasm-parity.json),
    [neural-native-onnx-parity.json](../validation/neural-native-onnx-parity.json),
    [neural-browser-parity-wasm-r2.json](../validation/neural-browser-parity-wasm-r2.json),
    [neural-browser-parity-corruption-r2.json](../validation/neural-browser-parity-corruption-r2.json),
    [neural-browser-wasm-benchmark-r2.json](../validation/neural-browser-wasm-benchmark-r2.json).
- **Provider reporting:** requested provider, adapter availability, session
  creation, inference success, observed placement (`wasm`,
  `webgpu-session-verified`, `webgpu-session-unverified`, `cpu-fallback`,
  `webgpu-mixed`) and `gpu_execution_verified` are reported separately.
  `--require-webgpu` requires verified GPU placement, not merely a requested
  provider string.
- **Open criteria / blockers:**
  - **Competitive trained weights (32×2+) for all three rulesets are unavailable.**
    Only a tiny trained smoke checkpoint was validated. Competitive-model parity,
    performance and strength remain **blocked**.
  - **WebGPU execution is blocked:** no adapter on this host. A WebGPU request
    fails explicitly. Independent physical-device validation (Windows Chrome/Edge,
    Intel, NVIDIA RTX 3060, Linux, Firefox WASM, Android Chrome, Safari) is
    required before any GPU claim.
  - Representative-network memory growth and download budgets are unmeasured.
  - ORT Web exposes no stable per-node placement API; even on hardware, strict
    GPU-only execution may remain unverified and would be reported as such.

## Phase 2 — Browser-side neural MCTS (subsequent PR)

- **Status:** Not started. Phase 1B deliberately provides a distinct-position
  batched inference interface (1–32) and a bounded asynchronous client for the
  future search boundary. No MCTS engine is implemented or claimed.
- **Design constraints:** reuse existing tree traversal/expansion/selection/
  backpropagation; avoid per-node serialization and blocking synchronization.
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
  statistical uncertainty; fixed-time and fixed-iteration reported separately.

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
| Production-equivalent Jarl execution | Validated (browser worker) |
| Fixed-work correctness gate | Implemented and validated (r2) |
| Fixed-time search performance | Partially validated (informational only) |
| Brandubh validation | Validated (depth 3 and 5 fixed-work) |
| Tablut validation | Validated (depth 3 and 5 fixed-work) |
| Copenhagen validation | Partially validated (depth 3 full; depth 5 limited on 3 cases) |
| Native ONNX parity | Validated (smoke model) |
| Actual browser WASM execution | Validated |
| Browser WASM numerical parity | Validated (smoke model) |
| WebGPU capability | Reported; adapter unavailable |
| Verified GPU execution | Blocked (no physical adapter) |
| Distinct-position batched inference | Completed (1–32, equals single inference) |
| Competitive trained-model validation | Blocked (weights unavailable) |
| Physical-device validation | Blocked |
| Worker lifecycle validation | Validated (deterministic fake-worker tests + browser cancel/restart) |
| Artifact integrity validation | Validated (structural negative tests) |
| Playing-strength validation | Not started |
| Production integration | Not started |

## Open findings and technical debt

- Browser Pyodide is consistently ≈2.1–2.2× slower than native CPython at equal
  completed work; expected overhead, not a regression.
- Three Copenhagen depth-5 fixed-work cases cannot finish inside the 60 s safety
  limit on native CPython either; they are reported as limited, and a longer
  budget or a shallower workload is a Phase 3 decision.
- The neural runtime exposes no per-node placement API; strict GPU-only execution
  may remain unverifiable and must be reported as `webgpu-session-unverified`.
- The smoke model's absolute latencies are not representative of competitive
  networks or of MCTS simulation throughput.
- Headless Chromium does not expose `performance.memory`, so JS heap hints are
  `null`; the ORT Web WASM heap size is not exposed at all.
- `verify_jarl.mjs`/`neural_*.mjs` remain opt-in browser gates (they need a
  Pyodide/ORT download and Chromium), while their correctness comparison logic is
  covered by ordinary native pytest.

## Recommended next tasks

1. Provide representative trained checkpoints (32×2 and larger) for all three
   rulesets and run the documented export/parity/browser commands.
2. Run `neural_parity.mjs`/`neural_benchmark.mjs --provider webgpu --require-webgpu`
   on physical Windows/Linux/Android/Safari hardware and record capability and
   placement reports.
3. Extend fixed-time Jarl comparisons with completed-depth accounting and
   deadline-overshoot percentiles.
4. Design the asynchronous Python-search/browser-inference boundary for Phase 2
   neural MCTS.
5. Investigate a narrow, reviewed CSP allowance for WASM as part of Phase 4.

## Reproducibility (corrected commands)

```sh
# Environment
uv sync --frozen --extra train --extra dev
uv pip install --python .venv/bin/python -r spikes/browser_offline/requirements-onnx.txt

# Native vs browser Jarl (mandatory fixed-work correctness gate)
.venv/bin/python spikes/browser_offline/native_jarl_benchmark.py \
  --mode fixed_work --suite quick --output /tmp/jarl-native.json
.venv/bin/python spikes/browser_offline/prepare.py --output /tmp/hnefatafl-analysis
node spikes/browser_offline/verify_jarl.mjs /tmp/hnefatafl-analysis \
  --mode fixed_work --suite quick --native /tmp/jarl-native.json \
  --output /tmp/jarl-browser.json
# standalone comparison (exits non-zero on mandatory failure)
.venv/bin/python spikes/browser_offline/compare_jarl_benchmarks.py \
  /tmp/jarl-native.json /tmp/jarl-browser.json --output /tmp/jarl-comparison.json

# Custom positions (exact supplied states, not name-selected fixtures)
node spikes/browser_offline/verify_jarl.mjs /tmp/hnefatafl-analysis \
  --positions /path/to/custom-positions.json --mode fixed_work \
  --native /tmp/jarl-native-custom.json --output /tmp/jarl-browser-custom.json

# Owner-supplied trained checkpoint: export + native parity
.venv/bin/python spikes/browser_offline/onnx_export.py /path/to/last.pt /tmp/model-export

# Stage and run browser numerical parity + benchmark
.venv/bin/python spikes/browser_offline/prepare_neural.py \
  --model-dir /tmp/model-export --output /tmp/neural-spike
node spikes/browser_offline/neural_parity.mjs /tmp/neural-spike \
  --provider wasm --output /tmp/neural-parity-wasm.json
node spikes/browser_offline/neural_benchmark.mjs /tmp/neural-spike \
  --provider wasm --output /tmp/neural-wasm.json
node spikes/browser_offline/neural_parity.mjs /tmp/neural-spike \
  --provider webgpu --require-webgpu --output /tmp/neural-parity-webgpu.json

# Deterministic client lifecycle checks (no browser)
node spikes/browser_offline/test_browser_lifecycle.mjs
```

## Status history

| Date | Change |
| --- | --- |
| 2026-11-19 | Roadmap created; Phase 1A/1B implemented and partially validated (revision 1). |
| 2026-11-19 | Revision 2: corrected correctness gate, custom positions, bounded per-case watchdogs, browser numerical parity, provider reporting, lifecycle, artifact integrity and distinct-position batching. Revision-1 evidence preserved as history. |
