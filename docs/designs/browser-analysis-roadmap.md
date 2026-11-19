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

**Current integration revision:** `9daa668`, a normal merge of `origin/main`
`07d7438` (`perf/backend-read-baseline`) into `feat/browser-analysis-phase1`.
The upstream change touched only the backend-read benchmark, its documentation
and the documentation index; no engine, model, inference, dependency or frontend
runtime file changed. The staged engine closure and the production-equivalent
Jarl preset were revalidated against the merged sources.

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
  [browser-offline-phase0.json](../validation/browser-offline-phase0.json).
- **Baseline (externally supplied):** Brandubh 1.0 / 34.1 / 40.3 ms; Tablut
  2.3 / 31.4 / 79.7 ms; Copenhagen 7.1 / 69.7 / 206.6 ms (baseline / depth-2
  alpha-beta / MCTS). Startup 1,019.2 ms (1,308.2 ms offline); cancellation ack
  0.8 ms; restart 1,039.6 ms; 20 MiB WASM heap capacity; 136 fixture comparisons;
  no external requests. Mobile emulation is not physical-mobile evidence.
- **Revision-3 re-check:** the full `verify.mjs` browser gate still passes on the
  merged sources (both profiles, no external/API requests).

## Phase 1A — Full-strength Jarl and native/browser comparison

- **Objective:** execute the production-equivalent Jarl agent in the isolated
  browser worker, compare it with native CPython on identical workloads, and
  report performance, correctness and incompleteness honestly.
- **Dependencies:** Phase 0 runtime staging; the `alpha_beta` catalog preset in
  `hnefatafl/ai.py`.
- **Status:** Partially validated.
- **Delivered:**
  - Production-equivalent Jarl preset: depth 5, 2 s cooperative deadline, Shield
    maiden evaluation, optimized search defaults.
  - Named workloads: quick (3 / 250 ms), interactive (5 / 1 s), standard (5 / 2 s),
    deep (8 / 5 s), extended (10 / 10 s).
  - One shared `jarl_benchmark.py` (positions, per-position legal-move/terminal
    contracts, effective configs, workload digest) used unchanged by native
    CPython and Pyodide.
  - One canonical `jarl_compare.py` used by the browser verifier and the CLI.
  - `verify_jarl.mjs` analyses the exact `--positions` state objects, records the
    workload digest, runs each case as an independently bounded request, preserves
    earlier records on failure, and exits non-zero on mandatory failures.
  - Watchdogs: 30 s initialization; 5 s ordinary job; analysis deadline + margin
    up to 20 s; 75 s per benchmark case.
- **Revision-3 contract:** every repetition is validated (record keys include the
  repetition index; duplicates rejected; expected coverage derived independently
  from workload cases × configurations × declared repetitions). Record-level
  position hash, ruleset, mode, seed and normalised effective-settings digest are
  validated. Execution validity (finite value, legal workload move, correct
  defender-relative terminal value, coherent status/depth), parity at equal
  completed work, and acceptance completion are reported separately. Asymmetric or
  limited cases are unresolved acceptance targets, not regressions. Terminal,
  unsupported, failed and non-equivalent cases are excluded from equal-work
  latency ratios; node-rate metrics are `null` when unavailable.
- **Engine constraint:** alpha-beta depth is capped at 8; the public validator caps
  at 5. `extended` (depth 10) is rejected by the engine and reported explicitly;
  it is never silently reduced.
- **Validation evidence (revision 3):**
  - quick (depth 3), all rulesets, reps=2: 42/42 expected, 42/42 compared and
    execution-valid, 36/36 parity at equal work, acceptance complete, browser
    2.14× native at equal work.
  - standard (depth 5), Brandubh, reps=2: 14/14, 12/12 parity, acceptance
    complete, 2.25×.
  - standard (depth 5), Tablut+Copenhagen, reps=1: 14/14 execution-valid, 12/12
    parity; **3 Copenhagen cases unresolved** (opening, midgame, repetition reach
    depth 4 on both runtimes inside the 60 s safety limit). Tablut completes
    depth 5.
  - fixed-time standard, all rulesets, reps=2: 42/42 valid, 16 cases with
    identical completed work and matching parity, 24 not equal-work comparable.
    Informational only; not a speedup claim.
  - Evidence: [jarl-native-fixed-work-quick-r3.json](../validation/jarl-native-fixed-work-quick-r3.json),
    [jarl-browser-fixed-work-quick-r3.json](../validation/jarl-browser-fixed-work-quick-r3.json),
    [jarl-native-depth5-brandubh-r3.json](../validation/jarl-native-depth5-brandubh-r3.json),
    [jarl-browser-depth5-brandubh-r3.json](../validation/jarl-browser-depth5-brandubh-r3.json),
    [jarl-native-depth5-tablut-copenhagen-r3.json](../validation/jarl-native-depth5-tablut-copenhagen-r3.json),
    [jarl-browser-depth5-tablut-copenhagen-r3.json](../validation/jarl-browser-depth5-tablut-copenhagen-r3.json),
    [jarl-native-fixed-time-standard-r3.json](../validation/jarl-native-fixed-time-standard-r3.json),
    [jarl-browser-fixed-time-standard-r3.json](../validation/jarl-browser-fixed-time-standard-r3.json).
- **Historical evidence:** revision 1 and revision 2 files are preserved; they do
  not validate the revision-3 contract.
- **Open criteria:** physical mobile and low-end device latency; deadline
  overshoot percentiles over repeated runs; a longer Copenhagen budget decision
  (Phase 3, not this PR).
- **Blockers:** none for depth 3/5 fixed-work; physical devices belong to Phase 3.

## Phase 1B — Trained-model ONNX export and browser inference

- **Objective:** export an actual trained policy/value checkpoint to
  inference-only ONNX, establish native and browser numerical policy parity, and
  execute it in a browser via ORT Web WASM/WebGPU with explicit capability
  reporting.
- **Dependencies:** Phase 1A worker boundary; a validated export; the optional
  pinned exporter environment (`requirements-onnx.txt`).
- **Status:** Partially validated. Smoke-model export, native parity, browser WASM
  policy parity, distinct-position batching and worker lifecycle are validated;
  competitive trained weights and physical WebGPU are blocked.
- **Delivered:**
  - Transactional fail-closed `onnx_export.py`: staging, graph validation, native
    reference vectors, native parity, manifest + `VALIDATED.json` that agree on
    status/ruleset/model digest/model size/**reference digest**. A forced
    replacement invalidates the old marker before attempting publication, so a
    failed or unvalidated replacement cannot inherit it. `--no-parity` produces an
    explicitly unvalidated artifact that staging refuses by default.
  - Reference vectors carry position identity, six-plane inputs, logits, defender
    values, legal indices, legal-only probabilities, shapes and dtypes.
  - Canonical fail-closed `neural_parity.py`: validates the **actual returned**
    legal indices and order, reported probabilities, postprocessing consistency,
    shapes/dtypes, model/position identity, finiteness, batch count/order,
    duplicate identities, tolerances and material vs near-tied ranking changes.
  - `prepare_neural.py` validates the actual ONNX graph (opset, tensor ranks,
    element types, spatial dims, dynamic batch, policy width) plus the full
    sidecar/manifest/marker contract and both model and reference digests.
  - Neural worker: awaiting session release, releasing failed sessions, disposing
    input/output tensors in finally blocks, validating returned tensors (batch
    count, shapes, dtypes, finiteness, value range, legal indices, FP32 overflow)
    before publishing, and genuine distinct-position batched inference (1–32).
  - Neural client: generation guards extended across download admission, bounded
    body read, digest verification, worker initialization, session creation and
    model publication; failed WASM loads do not block a later WebGPU worker;
    deterministic load admission and cancellation; failed replacements never
    publish a stale model.
- **Provider reporting:** requested, adapter, session-created, inference-succeeded
  and observed placement are separate. Placement is `wasm`,
  `webgpu-session-verified`, `webgpu-session-unverified`, `webgpu-mixed`,
  `cpu-fallback` or `placement-unknown`. Only `webgpu-session-verified` sets
  `gpu_execution_verified`. `--require-webgpu` requires that verified state.
- **Validation evidence (revision 3, trained **smoke** model):**
  - Native PyTorch → ORT CPU over 39 reference cases: worst logit 7.15e-7, value
    2.98e-8, postprocess 0, zero material ranking changes.
  - Native PyTorch → browser ORT WASM over the same 39 cases: worst logit 1.67e-6,
    value 2.98e-8, postprocess 0.
  - Distinct-position batched inference 1–32 all matched their own single
    reference in order.
  - All nine deliberate corruption kinds (logit, value, legal indices, ordering,
    probabilities, policy shape, value shape, model identity, batch count/order)
    fail the mandatory gate.
  - WASM: init 528.9 ms, first inference 24.7 ms, warm run 0.8 ms, sustained
    in-worker 4,523.6 positions/s (batch 8).
  - WebGPU: no adapter; both gates fail explicitly and exit non-zero.
  - Evidence: [neural-export-manifest-r3.json](../validation/neural-export-manifest-r3.json),
    [neural-native-onnx-parity-r3.json](../validation/neural-native-onnx-parity-r3.json),
    [neural-browser-parity-wasm-r3.json](../validation/neural-browser-parity-wasm-r3.json),
    [neural-browser-corruption-r3.json](../validation/neural-browser-corruption-r3.json),
    [neural-browser-wasm-benchmark-r3.json](../validation/neural-browser-wasm-benchmark-r3.json),
    [neural-browser-parity-webgpu-blocked-r3.json](../validation/neural-browser-parity-webgpu-blocked-r3.json),
    [neural-browser-benchmark-webgpu-blocked-r3.json](../validation/neural-browser-benchmark-webgpu-blocked-r3.json).
- **Open criteria / blockers:**
  - **Competitive trained weights (32×2+) for all three rulesets are unavailable.**
    Only a tiny trained smoke checkpoint was validated. Competitive-model parity,
    performance and strength remain **blocked**.
  - **WebGPU execution is blocked:** no adapter on this host. Physical-device
    validation (Windows Chrome/Edge, Intel, NVIDIA RTX 3060, Linux, Firefox WASM,
    Android Chrome, Safari) is required before any GPU claim.
  - Representative-network memory growth and download budgets are unmeasured.
  - ORT Web exposes no stable per-node placement API; strict GPU-only execution may
    remain unverified even on hardware and is reported as such.

## Phase 2 — Browser-side neural MCTS (subsequent PR)

- **Status:** Not started. Phase 1B provides a distinct-position batched inference
  interface (1–32), validated tensor/publication contract and bounded asynchronous
  client intended as the future search boundary. No MCTS engine is implemented or
  claimed; batched inference alone does not start this phase.
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
  reuse of existing design tokens and components. Production UI and design system
  are unchanged by this PR.

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
| All-repetition fixed-work correctness gate | Implemented and validated (r3) |
| Fixed-work acceptance completion | Brandubh/Tablut complete; Copenhagen 3 cases unresolved |
| Fixed-time search performance | Partially validated (informational only) |
| Brandubh validation | Validated (depth 3 and 5 fixed-work) |
| Tablut validation | Validated (depth 3 and 5 fixed-work) |
| Copenhagen validation | Partially validated (depth 3 full; depth 5 limited on 3 cases) |
| Native ONNX parity | Validated (smoke model) |
| Actual browser WASM execution | Validated |
| Browser WASM numerical policy parity | Validated (smoke model, actual returned policy) |
| WebGPU capability | Reported; adapter unavailable |
| Verified GPU execution | Blocked (no physical adapter) |
| Distinct-position batched inference | Completed (1–32, equals single inference) |
| Competitive trained-model validation | Blocked (weights unavailable) |
| Physical-device validation | Blocked |
| Worker lifecycle validation | Validated (25 deterministic Node checks + browser gates) |
| Artifact integrity/lifecycle validation | Validated (structural negative tests) |
| Runtime tensor validation | Validated (worker rejects malformed outputs) |
| Playing-strength validation | Not started |
| Production integration | Not started |

## Open findings and technical debt

- Browser Pyodide is consistently ≈2.1–2.3× slower than native CPython at equal
  completed work; expected overhead, not a regression.
- Three Copenhagen depth-5 fixed-work cases cannot finish inside the 60 s safety
  limit on native CPython either. They are reported as unresolved acceptance
  targets; lengthening the budget or choosing a shallower workload is a Phase 3
  decision, not a silent assertion change.
- The neural runtime exposes no per-node placement API; strict GPU-only execution
  may remain unverifiable and is reported as `webgpu-session-unverified`.
- The smoke model's absolute latencies are not representative of competitive
  networks or of MCTS simulation throughput.
- Headless Chromium does not expose `performance.memory`, and ORT Web does not
  expose its WASM heap size, so heap fields are `null`; no memory-stability claim
  is made.
- `verify_jarl.mjs`/`neural_*.mjs` remain opt-in browser gates; their comparison
  and validation logic is covered by ordinary native pytest.

## Recommended next tasks

1. Provide representative trained checkpoints (32×2 and larger) for all three
   rulesets and run the documented export/parity/browser commands.
2. Run `neural_parity.mjs`/`neural_benchmark.mjs --provider webgpu --require-webgpu`
   on physical Windows/Linux/Android/Safari hardware and record capability and
   placement reports.
3. Decide a bounded Copenhagen depth-5 budget (or workload) in Phase 3 and record
   the resulting completion.
4. Design the asynchronous Python-search/browser-inference boundary for Phase 2
   neural MCTS.
5. Investigate a narrow, reviewed CSP allowance for WASM as part of Phase 4.

## Reproducibility (revision 3)

```sh
# Environment
uv sync --frozen --extra train --extra dev
uv pip install --python .venv/bin/python -r spikes/browser_offline/requirements-onnx.txt

# Native vs browser Jarl (mandatory fixed-work gate, every repetition)
.venv/bin/python spikes/browser_offline/native_jarl_benchmark.py \
  --mode fixed_work --suite quick --warmup 1 --repetitions 2 --output /tmp/jarl-native.json
.venv/bin/python spikes/browser_offline/prepare.py --output /tmp/hnefatafl-analysis
node spikes/browser_offline/verify_jarl.mjs /tmp/hnefatafl-analysis \
  --mode fixed_work --suite quick --warmup 1 --reps 2 \
  --native /tmp/jarl-native.json --output /tmp/jarl-browser.json
# standalone comparison (non-zero on mandatory failure, --require-complete for acceptance)
.venv/bin/python spikes/browser_offline/compare_jarl_benchmarks.py \
  /tmp/jarl-native.json /tmp/jarl-browser.json --output /tmp/jarl-comparison.json

# Custom positions (exact supplied states, not name-selected fixtures)
node spikes/browser_offline/verify_jarl.mjs /tmp/hnefatafl-analysis \
  --positions /path/to/custom-positions.json --mode fixed_work \
  --native /tmp/jarl-native-custom.json --output /tmp/jarl-browser-custom.json

# Owner-supplied trained checkpoint: export + native parity
.venv/bin/python spikes/browser_offline/onnx_export.py /path/to/last.pt /tmp/model-export

# Stage and run browser policy parity, corruption checks and benchmark
.venv/bin/python spikes/browser_offline/prepare_neural.py \
  --model-dir /tmp/model-export --output /tmp/neural-spike
node spikes/browser_offline/neural_parity.mjs /tmp/neural-spike \
  --provider wasm --output /tmp/neural-parity-wasm.json
node spikes/browser_offline/neural_parity.mjs /tmp/neural-spike \
  --provider wasm --corrupt legal --output /tmp/neural-parity-corrupt.json   # must fail
node spikes/browser_offline/neural_benchmark.mjs /tmp/neural-spike \
  --provider wasm --sustained 200 --output /tmp/neural-wasm.json
node spikes/browser_offline/neural_parity.mjs /tmp/neural-spike \
  --provider webgpu --require-webgpu --output /tmp/neural-parity-webgpu.json

# Deterministic client lifecycle checks (no browser)
node spikes/browser_offline/test_browser_lifecycle.mjs
```

Physical WebGPU (Windows Chrome/Edge over a WSL-served staged directory) is
documented in `spikes/browser_offline/README.md`.

## Status history

| Date | Change |
| --- | --- |
| 2026-11-19 | Roadmap created; Phase 1A/1B implemented and partially validated (revision 1). |
| 2026-11-19 | Revision 2: corrected fixed-work gate, custom positions, bounded per-case watchdogs, browser WASM parity, provider reporting, lifecycle, artifact integrity and distinct-position batching. |
| 2026-11-19 | Revision 3: integrated `main` `07d7438`; validate every repetition and derive coverage independently; separate execution/parity/acceptance; validate the actual returned browser policy and all corruption kinds; coherent artifact lifecycle; graph/runtime tensor validation; awaited resource release; closed load/cancellation races; refined provider evidence and harness failure handling. |
