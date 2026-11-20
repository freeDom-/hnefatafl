# Browser-side analysis roadmap

Canonical roadmap for high-strength browser-side Hnefatafl analysis. It tracks
every phase, its scope, dependencies, acceptance criteria, current status and
validation evidence. Update this document in every PR that touches browser-local
analysis and include the phase table in the pull request description.

Related: [Phase 2 validation](../validation/browser-analysis-phase2.md) ·
[Phase 1 validation](../validation/browser-analysis-phase1.md) ·
[Phase 0 feasibility](../validation/browser-analysis-prototype.md) ·
[browser neural feasibility](../validation/browser-neural-feasibility.md) ·
[isolated spike](../../spikes/browser_offline/README.md) ·
[documentation index](../README.md).

**Current integration revision:** Phase 2 revision 2 on a branch merged with
`origin/main` `0c7b4f0` (documentation and release-readiness changes only). Phase 2
executes the existing Python neural MCTS generator in the browser through the
validated Phase 1B inference boundary. Revision 2 is the PR #108 review-correction
round: search ownership and cancellation across both workers, independent
production-MCTS reference validation, hardened comparators, checkpoint-bound model
identity, exception-safe cleanup, a strict legal-probability contract, lazy neural
assets, bfcache recovery and mandatory staged-source integrity. Revision-1
evidence is preserved unchanged.

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

| Phase | Scope | Previous status | Current status | Validation evidence | Remaining work / blockers |
| --- | --- | --- | --- | --- | --- |
| 0 | Browser engine feasibility | Not started | Completed | [Phase 0 report](../validation/browser-analysis-prototype.md), [JSON](../validation/browser-offline-phase0.json) | Historical; independently reproduced |
| 1A | Full-strength Jarl | In progress | Partially validated | [Phase 1 validation](../validation/browser-analysis-phase1.md), `jarl-*-r3/r4.json` | Physical devices, Copenhagen depth-5 budget (Phase 3) |
| 1B | ONNX export and browser neural inference | In progress | Partially validated | [Phase 1 validation](../validation/browser-analysis-phase1.md), `neural-*-r3/r4/r5.json`, `neural-*-r6.json` | Competitive weights, physical WebGPU |
| 2 | Browser-side neural MCTS | Not started (inference boundary prepared) | Implemented, software-validated; external gates open | [Phase 2 validation](../validation/browser-analysis-phase2.md), `mcts-*-r1.json` | Competitive-model throughput, physical devices, playing strength |
| 3 | Optimization and strength evaluation | Not started | Not started (inputs measured) | Phase 2 benchmark timing breakdown | Per-step boundary and transport optimization, strength tournaments, batched leaves |
| 4 | Production security and runtime integration | Not started | Not started | — | CSP, model distribution, offline storage, memory budgets |
| 5 | Production frontend integration | Not started | Not started | — | Deferred feature boundary, bundle budgets, design tokens |
| 6 | Final acceptance and controlled rollout | Not started | Not started | — | All external gates |

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
- **Revision-4 contract:** deterministic parity is compared whenever both valid
  results completed the same depth, even when node/evaluation counters differ;
  computational-work equivalence (depth, interruption state and measured counters)
  only governs equal-work latency/throughput. Any mandatory failure prevents
  `acceptance_complete`, and invalid parity never contributes acceptance counts.
  Required depth and cooperative deadline come from the validated effective
  configuration; a record cannot redefine `expected_depth`, report an inconsistent
  budget, or supply malformed work counters.
- **Engine constraint:** alpha-beta depth is capped at 8; the public validator caps
  at 5. `extended` (depth 10) is rejected by the engine and reported explicitly;
  it is never silently reduced.
- **Validation evidence (revision 4):**
  - Fresh quick (depth 3) fixed-work on the merged sources: 42/42 expected,
    compared and execution-valid; 36/36 parity; acceptance complete; 2.13×
    equal-work ratio.
  - Revision-3 depth-5 and fixed-time measurements **re-evaluated** with the
    revision-4 comparator (engine source identity unchanged, no long reruns):
    Brandubh 14/14 with 12/12 parity and acceptance complete; Tablut+Copenhagen
    12/12 parity with 3 Copenhagen cases unresolved at depth 4 and acceptance
    incomplete; fixed-time 16/16 parity at equal work with 24 not equal-work
    comparable.
  - Evidence: [jarl-native-fixed-work-quick-r4.json](../validation/jarl-native-fixed-work-quick-r4.json),
    [jarl-browser-fixed-work-quick-r4.json](../validation/jarl-browser-fixed-work-quick-r4.json),
    [jarl-acceptance-depth5-brandubh-r4.json](../validation/jarl-acceptance-depth5-brandubh-r4.json),
    [jarl-acceptance-depth5-tablut-copenhagen-r4.json](../validation/jarl-acceptance-depth5-tablut-copenhagen-r4.json),
    [jarl-acceptance-fixed-time-standard-r4.json](../validation/jarl-acceptance-fixed-time-standard-r4.json).
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
  - Revision-4 response contract: the comparator requires model/ruleset identity,
    request/position identity, policy/value data, shapes, dtypes, legal indices
    and order, probabilities and batch count/result count/index. Missing fields
    fail; the harness never substitutes expected values. Harness-generated
    terminal skips are marked; native ONNX outputs are normalized explicitly.
  - Revision-4 batch contract: `batch_count == results_count == outputs.length`,
    each returned index equals its array position, request identities are unique
    and match the requested order. Sixteen corruption kinds fail independently.
  - Revision-4 resource ownership: output tensors are disposed on every failure
    path; disposal failure reports `RESOURCE_CLEANUP_FAILED` and resets the worker.
  - Revision-4 artifact trust chain: manifest and marker cover model,
    reference-vector, metadata-sidecar and parity-report digests; unvalidated
    replacement cannot inherit a marker and cannot keep a stale parity report.
  - Revision-4 UI: an internal loader runs inside the owned operation, so Infer,
    distinct batch and Benchmark work from a clean initial state; provider changes
    reload; cancellation invalidates stale UI.
  - Revision-5 request identity: inference requests carry the immutable expected
    model digest, ruleset and size. The worker validates them against the active
    session before building inputs (single and batch-level metadata), the client
    exposes `modelIdentity()` for capture at workload preparation and rejects
    stale identities, and loading requires a supported ruleset. A request prepared
    for model A cannot execute against model B.
  - Revision-5 UI ownership: the operation token is propagated through manifest
    and reference-vector loading, model initialization, inference, benchmark and
    publication; shared cached fetches never let a cancelled operation resume.
  - Revision-5 provider tests: deterministic simulated WebGPU failure recovery is
    separated from a hardware test that accepts legitimate WebGPU success or an
    explicit unsupported outcome.
  - Revision-5 reference compatibility: reference vectors are written as compact
    JSON (exact values, digest over bytes), so Copenhagen (11.55 MB) fits the
    unchanged 16 MiB budget; indented output had exceeded it.
- **Provider reporting (revision 4):** requested provider, adapter availability,
  session creation, inference success, numerical parity and observed placement are
  separate. Placement is `wasm`, `webgpu-session-unverified`, `webgpu-mixed`,
  `cpu-fallback` or `placement-unknown`. A session-level provider list is never
  treated as proof of per-operator GPU execution, and `gpu_execution_verified` is
  false because the pinned ORT Web version exposes no supported per-operator
  diagnostic. `--require-webgpu` requires an adapter, a WebGPU-requested session
  and successful numerical inference; `--require-gpu-execution` requires
  independently verified GPU execution and currently fails explicitly.
- **Validation evidence (revision 4, trained **smoke** model):**
  - Native PyTorch → ORT CPU and browser WASM parity over 39 cases: worst logit
    7.15e-7 / 1.67e-6, value 2.98e-8, postprocess 0; batched 1–32 pass.
  - All 16 deliberate corruption kinds fail (numerical, metadata, identity, batch
    count/missing/extra/duplicate/missing/wrong index, reorder, position).
  - WASM: init 508.6 ms, first inference 18.6 ms, warm 0.6 ms, sustained
    4,469.3 positions/s; six actual-browser UI checks pass.
  - Evidence: [neural-export-manifest-r4.json](../validation/neural-export-manifest-r4.json),
    [neural-native-onnx-parity-r4.json](../validation/neural-native-onnx-parity-r4.json),
    [neural-browser-parity-wasm-r4.json](../validation/neural-browser-parity-wasm-r4.json),
    [neural-browser-corruption-r4.json](../validation/neural-browser-corruption-r4.json),
    [neural-browser-wasm-benchmark-r4.json](../validation/neural-browser-wasm-benchmark-r4.json),
    [neural-browser-ui-r4.json](../validation/neural-browser-ui-r4.json),
    [neural-browser-parity-webgpu-blocked-r4.json](../validation/neural-browser-parity-webgpu-blocked-r4.json),
    [neural-browser-benchmark-webgpu-blocked-r4.json](../validation/neural-browser-benchmark-webgpu-blocked-r4.json),
    [neural-browser-gpu-execution-blocked-r4.json](../validation/neural-browser-gpu-execution-blocked-r4.json).
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

## Phase 2 — Browser-side neural MCTS

- **Objective:** execute the *existing* Python neural MCTS search semantics
  locally in the browser using actual ONNX-backed predictions, without a second
  rules/search implementation.
- **Status:** Implemented and software-validated on this revision. Mandatory
  implementation gates are met; competitive-model, physical-device and
  playing-strength gates remain open (see
  [Phase 2 validation](../validation/browser-analysis-phase2.md)).
- **Design decision:** `mcts_context(neural=True)` owns traversal, PUCT, widening,
  lazy states, prediction reuse and backpropagation unchanged. A shared
  PyTorch-free encoder produces the six-plane input; a Pyodide search worker
  advances exactly one generator slice per message; the coordinator relays the
  dependent evaluation to the persistent ORT Web inference worker and resumes the
  same generator. No JavaScript/Rust/C++ MCTS, no PyTorch in the browser, and no
  blocking synchronisation or `SharedArrayBuffer` requirement.
- **Subphases:**
  - **2.0 Phase 1 follow-ups:** retryable shared asset loading with status checks
    and cleared failed promises; page-lifecycle cancellation ownership; independent
    single/batch corruption boundaries with exact error-code assertions; mandatory
    model identity for `infer`, `inferMany`, `benchmark`, `sustained` and search
    workloads.
  - **2.1 Shared encoding:** `hnefatafl/encoding.py` (`six-plane-v1`, byte planes,
    action indexing), used by native `encode_state()` and the search worker; exact
    parity across all rulesets, both sides, openings, tactics, endgames, terminals
    and repetition-sensitive states.
  - **2.2 Cooperative driver:** `hnefatafl/mcts_driver.py` + search worker bridge:
    bounded request validation, exact request/response association, finite
    non-negative priors, defender-relative value validation, deterministic
    best-move finalisation, versioned `search` envelope, model and full position
    identity, cooperative cancellation and generator disposal.
  - **2.3 Parity suite:** Level A shared-driver parity (exact), Level B actual
    model-backed parity (moves/values/visits/reporting), Level C fixed-time
    behaviour; one comparison implementation (`mcts_compare.py`).
  - **2.4 Worker lifecycle:** separate search and neural workers, coordinator
    generations, per-step and watchdog deadlines, supersession, cooperative cancel
    with bounded termination recovery, progress throttling, retained sessions
    across compatible searches.
  - **2.5 Benchmarks:** native and browser neural MCTS benchmarks over the shared
    workload matrix with completed-work, throughput, timing-breakdown and resource
    reporting (cold vs warm, fixed work vs fixed time).
  - **2.6 Experimental UI:** Jarl and neural MCTS analysis on the existing neural
    page, with lazy neural loading, budgets, progress, cancellation, provider/model
    reporting and result export. Not production frontend integration.
- **Validation evidence (revision 2, current):**
  - Level A: 21/21 positions exact across all three rulesets with identical engine
    source identity, workload digest and fixture-derived coverage; workload
    complete. The comparator independently validates both reports (schema,
    identity, coverage, budgets, terminal/immediate-win semantics, visit
    integrity) and separates execution validity, deterministic parity, numerical
    comparison, workload completion and performance comparability.
  - Level B (smoke fixtures): Brandubh 7/7 (256 simulations), Tablut and Copenhagen
    7/7 (128 simulations) against native ONNX; Brandubh 7/7 against native PyTorch;
    max value delta ≤3.7e-9, visit total variation 0. Checkpoint weight
    fingerprints bind the native PyTorch evaluator to the exported ONNX artifact;
    identical-architecture different-weight checkpoints are rejected.
  - Independent production-MCTS reference: new tests drive the production
    `mcts_context`/`mcts_policy` directly and require exact tree, policy, value,
    visit, request/reuse and evaluation-trace agreement for deterministic
    predictions (including a real tiny network), plus terminal/immediate-win and
    cancellation-vs-deadline semantics.
  - Benchmarks (4-channel zero-residual smoke fixture): 1,024 fixed simulations
    complete in 1.93–1.94 s (528–532 simulations/s) on Brandubh; 512–1,024
    workloads complete with acceptance true. Browser/native equal-work throughput
    ratio 0.139–0.210 (Brandubh/Tablut/Copenhagen). Fixed-time overshoot median
    8.5–20 ms, max 22 ms. Benchmarks validate raw repetition coverage, missing and
    duplicate records, failed repetitions and fixed-work completion before
    comparing equal work.
  - Lifecycle gates: 24 search-coordinator checks, 33 neural-client checks, 18
    staged-source integrity checks and 18 analysis UI checks (lazy assets,
    cancellation during download/session/worker initialization, bfcache and
    navigation restoration with a full neural search, non-default positions,
    in-flight selector change). Phase 1 inference parity (39 cases), benchmark and
    19 UI checks re-verified at this revision; four identity corruption boundaries
    assert exact error codes.
  - Native encoding: the shared encoder is 8.9–15.5× faster than the previous
    tensor-assignment path with exact parity (19.3–26.9 µs per representative
    position).
  - Measured bottleneck (unchanged): 36 % coordinator/Pyodide step boundary, 37 %
    neural wait (only ~36 % of it ORT execution), 25 % Python search work, 0.6 %
    unattributed.
- **Validation evidence (revision 1, historical):** preserved in
  [Phase 2 validation](../validation/browser-analysis-phase2.md) and the
  `mcts-*-r1.json` files; superseded by revision 2 for current acceptance.
  - UI: 9/9 neural MCTS analysis checks; Phase 1 inference UI 19/19 and inference
    parity 39/39 re-verified at this revision; identity boundaries 4/4 with exact
    codes.
- **Explicit non-claims:** no competitive-model throughput, no GPU execution, no
  physical-device performance, no playing-strength result, no production
  integration.
- **Next:** see Phase 3 priorities below; competitive checkpoints and physical
  hardware run the documented commands unchanged.

## Phase 3 — Optimization and strength evaluation (subsequent PRs)

- **Status:** Not started; Phase 2 supplied the measurements that select its work.
- **Priorities from measured evidence (in order):** reduce the
  coordinator/Pyodide per-step boundary (largest single cost); measure a direct
  search↔inference `MessageChannel` against the coordinator relay (inference
  dispatch is ~65 % of neural wait for a tiny model); consider independent-search
  batching that amortises one dispatch across several contexts; only then evaluate
  single-tree batched leaf selection (virtual loss, reservations, exactly-once
  backpropagation) with sequential mode retained as the correctness reference.
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
- **Latest frontend architecture to integrate with:** the merged feature-splitting
  work (`perf/frontend-feature-splitting`, `docs/validation/frontend-feature-splitting.md`)
  with deferred feature modules (`frontend/src/deferredFeature.tsx`), shared
  stylesheet coordination, `useGameAnalysis`/`AgentAnalysis` analysis components and
  the bundle-budget check (`frontend/scripts/check-bundle-budget.mjs`). Local
  browser analysis must join the deferred/lazy-loaded boundary, must not increase
  the initial bundle, and must satisfy the existing bundle budgets. Production UI
  and design system are unchanged by this PR.

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
| Complete inference-response contract | Validated (mandatory fields; no substitution) |
| Independent batch order/association | Validated (16 corruption kinds) |
| Output-tensor release on failure paths | Validated (7 instrumented checks) |
| Request-side model identity binding | Validated (client + worker; 6 corruption kinds) |
| UI operation ownership through asset loading | Validated (delayed asset/cancel checks) |
| Hardware-independent provider tests | Validated (deterministic + outcome-agnostic) |
| Larger-board reference compatibility | Structurally validated (Copenhagen fixture 11.57 MB) |
| Actual-browser UI clean-state flows | Validated (11 interaction checks) |
| GPU execution evidence | Not available (no per-operator diagnostic); GPU execution blocked |
| Competitive trained-model validation | Blocked (weights unavailable) |
| Physical-device validation | Blocked |
| Worker lifecycle validation | Validated (27 deterministic Node checks + browser gates) |
| Artifact integrity/lifecycle validation | Validated (structural negative tests) |
| Runtime tensor validation | Validated (worker rejects malformed outputs) |
| Playing-strength validation | Not started |
| Production integration | Not started |
| Phase 2 / Phase 1 follow-up corrections | Completed (retryable assets, lifecycle ownership, independent boundaries, mandatory identity) |
| Phase 2 / shared PyTorch-free position encoding | Completed and exact-parity validated |
| Phase 2 / cooperative browser search driver | Completed (software-validated) |
| Phase 2 / deterministic search parity (Level A) | Completed: 21/21 exact, all rulesets |
| Phase 2 / actual model-backed parity (Level B) | Validated with structural smoke models only |
| Phase 2 / Brandubh | Search, parity and benchmark validated (smoke model) |
| Phase 2 / Tablut | Search, parity and benchmark validated (smoke model) |
| Phase 2 / Copenhagen | Search, parity and benchmark validated (smoke model) |
| Phase 2 / sequential neural MCTS | Completed (correctness reference) |
| Phase 2 / independent search batching | Not implemented (Phase 3 candidate) |
| Phase 2 / single-tree batching | Deferred to Phase 3 |
| Phase 2 / WASM inference in search | Validated |
| Phase 2 / WebGPU-requested inference in search | Not validated (no adapter) |
| Phase 2 / verified physical GPU execution | Blocked (no per-operator diagnostic, no adapter) |
| Phase 2 / worker communication overhead | Measured: 36 % step boundary, ~64 % of neural wait is dispatch/transport |
| Phase 2 / search cancellation | Validated (cooperative + bounded recovery) |
| Phase 2 / search memory lifecycle | Generator/trace/diagnostics released; long-term growth unmeasured |
| Phase 2 / competitive trained models | Blocked (weights unavailable) |
| Phase 2 / end-to-end simulations per second | Measured for smoke fixtures only (232–564/s) |
| Phase 2 / tactical validation | Validated structurally (legal moves, terminals, immediate wins) |
| Phase 2 / playing strength | Not started |
| Phase 2 / search ownership before initialization | Validated (24 coordinator checks) |
| Phase 2 / cancellation across search and inference workers | Validated (drain/terminate, replacement refusal, repeated recovery) |
| Phase 2 / model identity and checkpoint binding | Validated (source-weight fingerprint, same-architecture different-weight rejection) |
| Phase 2 / search-context cleanup | Validated (all exit paths, registry capacity, final summary) |
| Phase 2 / independent production MCTS parity | Validated (exact deterministic reference, real-network policy reference) |
| Phase 2 / fixed-work benchmark acceptance | Validated (raw-record coverage, completion, duplicates/missing) |
| Phase 2 / fixed-time benchmark acceptance | Validated (informative unequal-work reporting, no false ratio) |
| Phase 2 / staged-source integrity | Validated (mandatory digests; 21 negative checks) |
| Phase 2 / lazy neural asset loading | Validated (Jarl with neural assets unavailable) |
| Phase 2 / bfcache and navigation restoration | Validated (full neural search after restore) |
| Phase 2 / native encoding performance | No regression; 8.9–15.5× faster with exact parity |
| Phase 2 / experimental UI | Validated in the isolated spike (18 checks; position/settings ownership) |
| Phase 2 / production integration | Not started |

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
- `verify_jarl.mjs`/`neural_*.mjs`/`mcts_*.mjs` remain opt-in browser gates; their
  comparison and validation logic is covered by ordinary native pytest.
- Phase 2 measured overhead: the coordinator/Pyodide per-step boundary (≈0.68 ms
  per dependent evaluation, 36 % of end-to-end time) and inference dispatch
  transport (≈64 % of neural wait) dominate before Python traversal. These are
  measured, not optimized, in Phase 2.
- Phase 2 throughput numbers use 4-channel, zero-residual structural fixtures.
  They say nothing about competitive-network throughput; raw inference throughput
  is not extrapolated to MCTS throughput.
- Search-result identity is the full position (including repetition history) plus
  ruleset, model digest, effective settings, seed and algorithm version. The
  six-plane encoding is an inference-input identity only and is never used to
  reuse a completed search result.

## Phase 3 priorities (from measured evidence)

1. Reduce the per-step coordinator↔Pyodide boundary cost (JSON serialization and
   `runPython` dispatch): compact event payloads, fewer crossings per simulation.
2. Measure a direct search-worker↔inference-worker `MessageChannel` against the
   coordinator relay; keep per-request timeout, cancellation and identity checks.
3. Add independent-search batching over several cooperative contexts so one
   dispatch amortises across positions (benchmarks/replay validation).
4. Only after that, evaluate single-tree batched leaf selection with virtual loss,
   reservations, exactly-once backpropagation and deterministic ordering.
5. Run the owner commands with competitive checkpoints (32×2 and larger) and
   record model-backed throughput, memory growth and strength.
6. Physical-device and WebGPU runs with the documented placement reporting.

## Recommended next tasks

1. Provide representative trained checkpoints (32×2 and larger) for all three
   rulesets and run the documented export/parity/browser commands, including the
   Phase 2 neural MCTS parity and benchmark commands.
2. Run the neural MCTS benchmark (`--provider webgpu --require-webgpu`) and the
   Phase 1 inference gates on physical Windows/Linux/Android/Safari hardware;
   record capability, placement and end-to-end simulations/second.
3. Optimize the measured Phase 2 bottlenecks (step boundary, inference dispatch)
   and add independent-search batching; then evaluate single-tree batching.
4. Decide a bounded Copenhagen depth-5 budget (or workload) and record the
   resulting completion.
5. Run playing-strength tournaments (browser vs native neural MCTS at equal
   budgets, browser vs Jarl, WASM vs WebGPU) with paired openings and uncertainty.
6. Investigate a narrow, reviewed CSP allowance for WASM as part of Phase 4.

## Reproducibility (Phase 2 revision 1)

```sh
# Environment
uv sync --frozen --extra train --extra dev
uv pip install --python .venv/bin/python -r spikes/browser_offline/requirements-onnx.txt
npm --prefix frontend ci && npx --prefix frontend playwright install chromium

# Structural smoke checkpoint (not competitive) and validated export
.venv/bin/python spikes/browser_offline/smoke_checkpoint.py --ruleset brandubh --output /tmp/smoke.pt
.venv/bin/python spikes/browser_offline/onnx_export.py /tmp/smoke.pt /tmp/model-export
.venv/bin/python spikes/browser_offline/prepare_neural.py \
  --model-dir /tmp/model-export --output /tmp/neural-spike

# Level A shared-driver parity (exact), Level B model-backed parity
.venv/bin/python spikes/browser_offline/native_mcts_parity.py --level a \
  --iterations 128 --output /tmp/mcts-native-a.json
node spikes/browser_offline/mcts_parity.mjs /tmp/neural-spike \
  --native /tmp/mcts-native-a.json --level a --output /tmp/mcts-browser-a.json
.venv/bin/python spikes/browser_offline/native_mcts_parity.py --level b \
  --evaluator onnx --model-export /tmp/model-export --rulesets brandubh \
  --iterations 256 --output /tmp/mcts-native-b.json
node spikes/browser_offline/mcts_parity.mjs /tmp/neural-spike \
  --native /tmp/mcts-native-b.json --level b --provider wasm \
  --output /tmp/mcts-browser-b.json

# Benchmarks (fixed work and fixed time; equal-work comparison)
.venv/bin/python spikes/browser_offline/native_mcts_benchmark.py \
  --model-export /tmp/model-export --evaluator onnx --quick --output /tmp/mcts-native-bench.json
node spikes/browser_offline/mcts_benchmark.mjs /tmp/neural-spike \
  --provider wasm --quick --native /tmp/mcts-native-bench.json \
  --output /tmp/mcts-browser-bench.json

# Experimental analysis UI, cancellation/lifecycle and Phase 1 follow-up gates
node spikes/browser_offline/test_mcts_ui.mjs /tmp/neural-spike --output /tmp/mcts-ui.json
node spikes/browser_offline/test_search_lifecycle.mjs
node spikes/browser_offline/test_neural_ui.mjs /tmp/neural-spike --output /tmp/ui.json
node spikes/browser_offline/test_browser_lifecycle.mjs
```

Private competitive checkpoints use the same commands with the model directory
produced by `onnx_export.py`; weights are never committed.

## Reproducibility (revision 3, Jarl and Phase 1)

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
  --provider wasm --corrupt reorder --output /tmp/neural-parity-corrupt.json  # must fail
node spikes/browser_offline/neural_parity.mjs /tmp/neural-spike \
  --provider wasm --corrupt request-model-worker --output /tmp/neural-identity-corrupt.json
node spikes/browser_offline/test_neural_identity.mjs
node spikes/browser_offline/neural_benchmark.mjs /tmp/neural-spike \
  --provider wasm --sustained 200 --output /tmp/neural-wasm.json
node spikes/browser_offline/test_neural_ui.mjs /tmp/neural-spike \
  --output /tmp/neural-ui.json
node spikes/browser_offline/test_neural_tensors.mjs
node spikes/browser_offline/neural_parity.mjs /tmp/neural-spike \
  --provider webgpu --require-webgpu --output /tmp/neural-parity-webgpu.json
node spikes/browser_offline/neural_parity.mjs /tmp/neural-spike \
  --provider webgpu --require-gpu-execution   # fails without a placement API

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
| 2026-11-20 | Revision 4: integrated `main` `17d3819` (frontend feature splitting and bundle budgets); deterministic parity independent of work counters; acceptance cannot pass with mandatory failures; record depth/budget validated against the effective configuration; complete inference-response contract and independent batch order/association; output tensors released on every failure path; artifact trust chain covers sidecar and parity digests; actual-browser clean-state UI flows; provider evidence no longer claims GPU execution from session lists. |
| 2026-11-20 | Revision 5: integrated `main` `1b4c4ae`; request-side model identity (digest/ruleset/size) validated against the active session for single and batched inference; operation ownership propagated through asset loading; hardware-independent provider tests; compact reference vectors keep Copenhagen within the 16 MiB budget; WASM inference infrastructure marked ready for Phase 2. |
| 2026-11-20 | Phase 2 revision 1: existing Python neural MCTS now runs in the browser through a shared PyTorch-free encoder and a cooperative search worker; Level A exact parity (21/21), Level B smoke-model parity for all three rulesets (7/7 each), native/browser benchmarks with a measured timing breakdown, cancellation/lifecycle and experimental analysis UI. Phase 1 follow-ups (retryable assets, page lifecycle, independent corruption boundaries, mandatory model identity) completed. Competitive models, physical devices and playing strength remain open. |
| 2026-11-20 | Phase 2 revision 2 (PR #108 review corrections): ownership reserved before initialization with validated response association; cancellation drains/terminates inference and releases the generator without killing newer work; UI cancellation covers model download/init; bfcache restoration installs usable clients; parity/benchmark comparators independently validate coverage, budgets, semantics and raw repetitions; checkpoint weight fingerprint binds native evaluators to the export; independent production-MCTS reference tests; strict legal-probability contract; exception-safe context cleanup; lazy neural assets; mandatory staged-source digests; shared encoder measured 8.9–15.5× faster. Fresh r2/r7 evidence; r1 preserved. |
