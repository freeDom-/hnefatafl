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

**Current integration revision:** Phase 3A and Phase 3B are merged on `main`
(PR #112 and PR #115). Phase 3B direct transport, inference caching and
independent batching remain opt-in; single-tree batching and subtree reuse remain
deferred. Phase 3C-A validation infrastructure is in progress; competitive-model,
playing-strength, physical-device and sustained-memory gates remain blocked.
Phase 2 executes the existing Python neural MCTS generator in the browser through
the validated Phase 1B inference boundary; its revisions 1–4 are preserved in
[the Phase 2 report](../validation/browser-analysis-phase2.md). Phase 3A closes
the remaining Phase 2 findings (independent workload identity, non-negative
diagnostics, build-time-isolated test hooks), adds profiling fields and
repeated-workload controls, removes an unused serialized-state allocation, and
delivers the first measured optimization: legal-only inference transport with
base64 planes decoded directly in the inference worker, now backed by an
end-to-end full/compact search parity gate. Phase 3B delivered the opt-in direct transport, exact inference cache and
independent batching; single-tree batching/subtree reuse remain deferred. Phase
3C-A adds strict cleanup validation, competitive-checkpoint validation,
role-balanced tournament infrastructure, sustained-memory validation and physical
device runbooks. External competitive/hardware evidence remains outstanding;
Phase 3 is not complete.

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

Phase 3 external acceptance gates remain outstanding: competitive trained
checkpoints, physical WebGPU execution, physical desktop/mobile browser
compatibility, sustained memory measurements and playing-strength tournaments.
Phase 3 is not complete.

## Phase status

| Phase | Scope | Previous status | Current status | Validation evidence | Remaining work / blockers |
| --- | --- | --- | --- | --- | --- |
| 0 | Browser engine feasibility | Not started | Completed | [Phase 0 report](../validation/browser-analysis-prototype.md), [JSON](../validation/browser-offline-phase0.json) | Historical; independently reproduced |
| 1A | Full-strength Jarl | In progress | Partially validated | [Phase 1 validation](../validation/browser-analysis-phase1.md), `jarl-*-r3/r4.json` | Physical devices, Copenhagen depth-5 budget (Phase 3) |
| 1B | ONNX export and browser neural inference | In progress | Partially validated | [Phase 1 validation](../validation/browser-analysis-phase1.md), `neural-*-r3/r4/r5/r6`, `neural-*-r8/r9.json` | Competitive weights, physical WebGPU |
| 2 | Browser-side neural MCTS | Not started (inference boundary prepared) | **Revision 4: implementation and software validation complete; external gates open** | [Phase 2 validation](../validation/browser-analysis-phase2.md), `mcts-*-r1/r2/r3/r4.json`, `*-r8/r9.json` | Competitive trained-model validation, physical WebGPU/mobile, sustained memory, playing strength |
| 3 | Optimization and strength evaluation | Not started | **In progress** | [Phase 3 validation](../validation/browser-analysis-phase3.md), [Phase 3B](../validation/browser-analysis-phase3b.md), [Phase 3C-A](../validation/browser-analysis-phase3c.md) | 3C external gates outstanding |
| 3A | Validation, profiling and initial transport optimization | Not started | **Merged; implementation and software validation complete** | [Phase 3 validation](../validation/browser-analysis-phase3.md), `*-phase3a.json` | Competitive-model/device strength gates remain external |
| 3B | Caching and batching | Not started | **Merged; software validated within scope** | [Phase 3B validation](../validation/browser-analysis-phase3b.md), `*-phase3b.json` | Direct/cache/batching opt-in; single-tree batching/subtree reuse deferred; sustained memory open |
| 3C | Competitive strength and stability | Not started | **In progress: 3C-A validation platform; external gates blocked** | [Phase 3C-A validation](../validation/browser-analysis-phase3c.md), [device matrix](../validation/device-matrix-phase3c.md) | Competitive checkpoints, tournaments, verified WebGPU, physical desktop/mobile, sustained memory, playing strength |
| 3C-B | Competitive measurements, performance and stability | Not started | **Partial: software + structural measurements; external gates blocked** | [Phase 3C-B report](../validation/browser-analysis-phase3c-b.md) | Competitive models, physical devices, verified GPU, full tournaments, memory acceptance |
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
- **Validation evidence (revision 4, current):**
  - Level A: 21/21 exact with validated built-in workload states/history; custom
    3/3 exact with mandatory file digest; Level B Brandubh/Tablut/Copenhagen 7/7
    each against native ONNX and 7/7 vs native PyTorch.
  - Fixed-work completion is classified independently per runtime; acceptance
    requires both to complete (terminal/immediate-win excepted), with separate
    native/browser incomplete and deadline-limited reporting and unchanged
    unequal-work fixed-time semantics.
  - Fresh-page UI tests use only normal controls (Jarl, neural MCTS, other ruleset,
    non-default position, initial position-data failure and retry, selection and
    history preservation, no neural requests for Jarl); 30/30 UI checks.
  - Cascading supersession: 29/29 coordinator checks covering delayed
    acknowledgement/drain, acknowledgement/drain failure, obsolete responses and
    missing model identity; pending requests/acknowledgements are owner-bound and a
    cleanup barrier is inherited across admissions.
  - Real pending-inference browser coverage (bounded 0–5000 ms test delay): healthy
    drain retains the session/worker; unhealthy drain replaces them before the
    replacement runs.
  - Parity CLI failures are structured for malformed files/JSON/workloads and
    unexpected exceptions with nonzero exits and no second traceback; custom
    sources distinguish file-backed vs embedded and validate version/digest.
  - Benchmarks (smoke fixture): 1,024 fixed simulations in 1.74–1.94 s
    (527–590 simulations/s); equal-work ratios 0.134–0.243; fixed-time overshoot
    median 8–13 ms (max 15 ms); timing shares 36.1 % step boundary, 37.7 % neural
    wait, 25.2 % Python search, 0.5 % unattributed.
- **Validation evidence (revision 3, historical):**
  - Level A: 21/21 exact across all rulesets with validated built-in workload
    states/history; custom-position workload (3 owner-style positions including
    repetition history) 3/3 exact with a verified custom-source digest.
  - Level B (smoke fixtures): Brandubh 7/7 (256), Tablut and Copenhagen 7/7 (128)
    against native ONNX; Brandubh 7/7 against native PyTorch; max value delta
    ≤3.7e-9, visit total variation 0.
  - Semantic validation: exact terminal values (−1/+1/0), required and consistent
    best moves with maximum root visit share, recomputed full-position identity,
    and rejection of every tested corruption (sign, missing/illegal/inconsistent
    move, altered identity/history/model).
  - Workload contract: fixtures reconstructed with exact states/history; custom
    files validated by content and digest; both-omitted cases and malformed
    metadata produce structured failures; browser consumes the declared workload.
  - Lifecycle: abandoned inference outcomes classified (success, cleanup/session
    failure, generic error, obsolete generation, drain timeout); 38 neural-client
    checks, 24 coordinator checks, 23 analysis UI checks including immediate
    restart without sleeps after cancelling download/ORT init/cold init/blocked
    leaf inference, repeated cancel/restart, healthy reuse and unhealthy-drain
    replacement; 21 staged-integrity checks; 39 inference parity cases and 19
    inference UI checks re-verified; 4 identity boundaries exact.
  - Benchmarks (smoke fixture): 1,024 fixed simulations in 1.85–1.94 s
    (528–552 simulations/s); equal-work ratios 0.136–0.227; fixed-time overshoot
    median 7.5–16.5 ms, max 18 ms; timing shares 36.5 % step boundary, 37.9 %
    neural wait, 25.1 % Python search work, 0.6 % unattributed.
- **Validation evidence (revision 2, historical):**
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

## Phase 3 — Optimization and strength evaluation

- **Status:** Phase 3A/3B merged and software-validated within their declared
  scopes. Phase 3C-A validation infrastructure is implemented; external gates
  remain blocked.
- **Delivered in 3A (evidence: [Phase 3 report](../validation/browser-analysis-phase3.md)):**
  - Independent `workload_identity()` recomputation in the comparator, covering
    settings, ordered states/repetition history, evaluator and ONNX model digest;
    identical corruption in both reports is rejected.
  - Non-negative validation for all accumulated diagnostics with valid zeros for
    terminal/immediate-win cases and no one-request-per-simulation assumption.
  - Test-only inference delay isolated in a dedicated worker entry point that is
    never staged; the normal worker has no delay implementation/configuration and
    rejects every test action, even when called directly. Normal and test-worker
    lifecycle, cancellation and replacement behavior are covered.
  - Extended workload kinds/budgets, `--repetitions`, `--warmup` and
    `--payload-mode compact|full`; profiling fields for serialization, parsing,
    bytes, policy extraction and unattributed time.
  - Legal-only MCTS inference: base64 planes decoded directly to `Float32Array`,
    legal softmax read from the output tensor, no full policy-row transfer, and
    no duplicate serialized position in browser evaluation events or pending
    context state. An end-to-end full/compact search parity gate covers all three
    rulesets, repetition/terminal/immediate-win positions, a 32×2 architecture
    and fixed-time behavior. Full logits remain the parity/benchmark/inspection
    path.
  - Controlled Brandubh A/B: +32.6 % (128/256 budgets) and +32.7 % (512/1024);
    Tablut +22.3 %; Copenhagen +86.9 % on smoke/representative architectures;
    1,024 Brandubh simulations in 1.50–1.66 s, versus 1.76–2.32 s with full
    logits. These are not competitive-model or device claims.
- **Phase 3B merged (opt-in):**
  - Direct search↔inference `MessageChannel` implemented as opt-in; coordinator
    retains admission/ownership/watchdog/cancellation/final validation. Corrected
    runs vary (latest +7.4 % quick; strong -1.2 %; earlier runs ranged from
    -10.4 % to +14.1 %), so there is no reproducible gain yet; relay remains the
    default.
  - Bounded exact legal-only inference cache implemented opt-in (model/provider/
    planes/legal key, LRU, entry/byte/eviction/hit accounting, model invalidation).
    Corrected repeated-position smoke runs measured +21.9 % quick and +47.6 %
    strong at ~82 % hit rate; retained opt-in, not a general speedup claim.
  - Bounded independent-search batching scheduler implemented opt-in (batch/
    queue limits, request association, queue overflow/failure/cancellation
    tests). Actual two-context batching works; on CPU smoke it was slower
    (193.7 ms sequential vs 337.1 ms batched), so it is not enabled by default.
  - 3B.0 authoritative nested best-move validation and coverage-identifier
    hardening completed.
  - Direct transport is opt-in with no reproducible measured gain; the
    coordinator relay remains the default.
  - Exact cache is opt-in with workload-dependent repeated-position benefit.
  - Independent batching is implemented and actual-browser validated, but has no
    demonstrated CPU-smoke performance advantage.
  - Single-tree batching and subtree reuse are deferred with recorded rationale.
  - Sustained memory profiling is outstanding.
- **Outstanding/deferred 3B:** single-tree batched leaf selection (deferred:
  requires consequential search-tree refactor and still lacks benchmark
  justification), subtree reuse (deferred: internal generator tree makes safe
  reuse disproportionate), sustained 10/30-minute memory profiles.
- **Outstanding 3C:** competitive 32×2+ trained checkpoints, reproducible
  fixed-work and fixed-time role-balanced tournaments with uncertainty, WebGPU
  verified execution, physical desktop/mobile/Safari/Firefox validation.
- **Constraint:** sequential Python neural MCTS remains the correctness and
  playing-strength reference; no JavaScript/Rust/C++ search engine.
- **Aspirational target:** 1,024 simulations within two seconds on capable desktop
  hardware with a representative trained model. Only the tiny smoke model has
  reached it; the competitive-model gate is open.

## Phase 3C-A — Competitive validation, tournaments, memory and device runbooks

- **Status:** Implemented; external acceptance blocked.
- **Detach strictness:** direct detach acknowledgments require matching
  request/worker/generation and a well-formed `direct_port_detached` response;
  malformed/rejected/timeout cleanup fails closed; one detach implementation.
- **Competitive validation:** fail-closed owner-checkpoint provenance/parity/
  benchmark validator with an executable CLI; Level A/B gates must match their
  validation level, native and browser evidence must come from distinct runtimes,
  and model/weight/ruleset/architecture/encoder identities are bound to the
  export. `--validate` is structural report inspection only; provenance and
  acceptance policy are explicit, so a structural label cannot be edited into
  competitive acceptance.
- **Tournament harness:** role-balanced paired openings, canonical manifests
  binding game IDs/pair indexes to openings, authoritative replay (including
  failed-game prefixes), per-game seed/budget/deadline/move-limit validation,
  canonical bootstrap recomputation, and a persistent newline-JSON process agent
  with per-generation queues, request-id matching, bounded diagnostics and one
  hard deadline covering writes, reads and recovery.
- **Memory validation:** explicit budgets, timestamp-derived coverage, minimum
  usable RSS coverage, missing-metric reasons without substitution, sustained
  progress throughout the window, worker/cache/WASM limits and profile-specific
  lifecycle events; structural, partial and acceptance outcomes are separate and
  blocked/partial CLI runs exit nonzero. 10/30-minute profiles remain owner runs.
- **Device/WebGPU:** physical matrix/runbook and per-combination evidence
  validator; verified-GPU acceptance requires independent per-operator inventory
  coverage, and WebGPU fallback/unverified/mixed placement cannot satisfy it.
- **Copenhagen depth-5:** native measurements at 2/5/10/30 s still do not reach
  depth 5; requirement remains outstanding, browser evidence blocked.
- **Evidence:** [Phase 3C-A report](../validation/browser-analysis-phase3c.md),
  [device matrix](../validation/device-matrix-phase3c.md),
  `*-phase3c*.json`.
- **Explicit non-goals:** no single-tree batching, subtree reuse, production
  frontend, or playing-strength claim without actual tournament evidence.

## Phase 3C-B — Competitive measurements and readiness

- **Status:** Software and structural measurements complete; external acceptance
  gates blocked.
- **Delivered:** V1 shared provider-evidence validation; executable native neural
  MCTS tournament adapter; sustained browser workload driver; cache-growth
  validator correction; consolidated owner runbook; versioned 3C-B evidence.
- **Measured:** 32×2 synthetic structural-representative models for Brandubh,
  Tablut and Copenhagen; Level A 21/21 exact; Level B 7/7 best-move parity per
  ruleset; fixed-work 128–1,024 and fixed-time 0.5–5 s benchmarks; browser/native
  equal-work ratios; optimization comparisons; structural tournaments; real
  10/30-minute memory profiles and lifecycle runs; Copenhagen depth-5 reassessment.
- **Findings:** 1,024 simulations approximately met only for Brandubh (2.0 s);
  Tablut 2.7 s and Copenhagen 3.7 s miss the aspirational target. Relay remains
  default; direct transport was slower; cache benefit is workload-dependent;
  batching was slower on CPU/WASM. The 30-minute profile showed a bounded JS-heap
  step (9.5→19.6 MB) with stable process RSS but failed the declared growth
  threshold.
- **Blocked:** competitive trained checkpoints, meaningful playing-strength
  samples, physical desktop/mobile/Safari/Firefox, verified GPU execution,
  sustained-memory acceptance (WASM capacity unavailable/JS-heap threshold),
  Windows physical cleanup verification and Copenhagen depth-5 acceptance.
- **Recommendation:** keep depth 5 blocked for Copenhagen; if a practical target
  is required, evaluate adaptive iterative deepening in a separately approved
  change. Phase 4 production integration is not started.
- **Evidence:** [Phase 3C-B report](../validation/browser-analysis-phase3c-b.md),
  `phase3cb-*` evidence files.

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
| Phase 2 / independent search batching | Not implemented (Phase 3B candidate) |
| Phase 2 / single-tree batching | Deferred to Phase 3B |
| Phase 3A / independent workload identity | Validated (recomputed from level, settings, states/history, evaluator, model) |
| Phase 3A / diagnostic counters | Validated (non-negative; terminal zeros; Level A and B) |
| Phase 3A / test-hook isolation | Validated (dedicated test worker; normal worker has no capability; direct test actions rejected) |
| Phase 3A / profiling instrumentation | Implemented (boundary, bytes, serialization, parsing, policy extraction, unattributed) |
| Phase 3A / legal-only inference transport | Validated (exact legal-probability and end-to-end full/compact search parity; full path retained) |
| Phase 3A / full vs compact search parity | Validated (13 fixed-work cases across three rulesets, 32×2, 13 fixed-time cases) |
| Phase 3A / measured optimization | Validated on smoke/representative architectures (+22–87 %); allocation cleanup below noise |
| Phase 3A / representative 32×2 architecture | Performance measured only; synthetic, not competitive validation |
| Phase 3B / direct worker transport | Implemented opt-in; no reproducible gain after hardening; relay default |
| Phase 3B / exact inference cache | Implemented opt-in; repeated-workload benefit demonstrated |
| Phase 3B / independent batching | Implemented opt-in; actual two-context batching validated; no CPU smoke gain |
| Phase 3B / single-tree batching | Deferred with rationale |
| Phase 3B / subtree reuse | Deferred with rationale |
| Phase 3B / sustained memory | Monitoring/validator implemented; 10/30-minute profiles blocked |
| Phase 3C-A / detach acknowledgments | Validated (strict/fail-closed) |
| Phase 3C-A / competitive checkpoint validation | Implemented; blocked (no competitive checkpoints) |
| Phase 3C-A / tournament harness | Implemented/validated structurally; competitive/browser tournament blocked |
| Phase 3C-A / memory validation | Monitor/validator implemented; sustained profiles blocked |
| Phase 3C-A / device & WebGPU evidence | Runbook/validator implemented; physical hardware blocked |
| Phase 3C-A / Copenhagen depth-5 | Re-measured natively through 30 s; depth 5 still incomplete |
| Phase 3C / competitive models | Blocked (private checkpoints unavailable) |
| Phase 3C / playing-strength tournaments | Not started |
| Phase 3C / physical GPU/mobile | Blocked (hardware unavailable) |
| Phase 3C / sustained memory stability | Not started |
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
| Phase 2 / custom-position tactical parity | Validated (owner-style positions including repetition history) |
| Phase 2 / fixed-work benchmark acceptance | Validated (raw-record coverage, completion, duplicates/missing) |
| Phase 2 / fixed-time benchmark acceptance | Validated (informative unequal-work reporting, no false ratio) |
| Phase 2 / staged-source integrity | Validated (mandatory digests; 21 negative checks) |
| Phase 2 / lazy neural asset loading | Validated (Jarl with neural assets unavailable) |
| Phase 2 / bfcache and navigation restoration | Validated (full neural search after restore) |
| Phase 2 / native encoding performance | No regression; 8.9–15.5× faster with exact parity |
| Phase 2 / semantic search-result validation | Validated (exact terminal values, best-move consistency, recomputed identity, 10+ negative cases) |
| Phase 2 / fresh-page analysis initialization | Validated (auto position load, retry, selection preservation, 30 UI checks) |
| Phase 2 / independent fixed-work completion | Validated (native and browser classified separately; terminal/immediate-win exceptions) |
| Phase 2 / cascading supersession ownership | Validated (29 coordinator checks; owner-bound requests, inherited cleanup barrier) |
| Phase 2 / real pending-inference browser coverage | Validated (bounded delay seam; healthy reuse and unhealthy replacement) |
| Phase 2 / parity CLI failure reporting | Validated (structured input/workload/execution failures) |
| Phase 2 / custom-source verification | Validated (file vs embedded, mandatory digest, property-order independence) |
| Phase 2 / built-in and custom workload contract | Validated (versioned source, state/history reconstruction, file digest, structured failures) |
| Phase 2 / abandoned inference error handling | Validated (success, cleanup/session failure, generic error, obsolete generation, drain timeout) |
| Phase 2 / UI replacement admission after cancellation | Validated (23 browser checks; bounded cleanup awaited; immediate restart without sleeps) |
| Phase 2 / experimental UI | Validated in the isolated spike (23 checks; position/settings ownership) |
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

## Phase 3B/3C remaining priorities (from measured evidence)

1. Reduce the remaining ~40 % per-step coordinator↔Pyodide boundary cost: measure
   a direct search-worker↔inference-worker `MessageChannel` against the relay,
   keeping per-request timeout, cancellation, identity and ownership checks.
2. Evaluate bounded exact inference caching against the existing prediction reuse;
   retain only if repeated-workload measurements justify memory and complexity.
3. Add independent-search batching over several cooperative contexts; report
   actual batch distribution, foreground latency and cancellation isolation.
4. Only then evaluate single-tree batched leaf selection with virtual loss,
   reservations, exactly-once backpropagation and deterministic ordering.
5. Run owner commands with competitive checkpoints (32×2 and larger) for parity,
   throughput, memory growth and fixed-work/fixed-time role-balanced tournaments.
6. Physical-device and WebGPU runs with the documented placement reporting, plus
   sustained memory/stability profiles.

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
| 2026-11-24 | Phase 3C-B executable-workflow correction: compatible extended benchmark commands, reproducible 50-pair opening corpora, workload failure/accounting binding, nonterminal sustained selection, validated native neural artifacts, telemetry-only progress/ready handshake, lifecycle mode coverage and driver regressions. Historical memory outcomes preserved; 30-minute JS-heap step remains an unresolved investigation. |
| 2026-11-24 | Phase 3C-B: shared provider-evidence validation, native neural tournament adapter, sustained workload driver, 32×2 structural parity/benchmark measurements, real 10/30-minute memory profiles, optimization comparisons, device/GPU blockers and Copenhagen depth-5 recommendation. External gates remain blocked. |
| 2026-11-24 | Phase 3C-A ownership/evidence correction: token-verified process ownership resistant to group-id reuse, actual browser search provider/normalized-settings evidence, and eviction-independent lifecycle-event aggregates with reset handling. External gates remain blocked. |
| 2026-11-24 | Phase 3C-A final correction: captured process-group cleanup independent of the parent, absolute-ply execution evidence, mode-aware fixed-work/fixed-time agent execution and placement binding, observation-window boundary memory gaps, and acceptance-window telemetry identity/progress/event binding. External gates remain blocked. |
| 2026-11-24 | Phase 3C-A fourth review correction: live timestamp-aligned memory telemetry with separate warmup/acceptance windows and per-metric coverage/gap policy; process-tree shutdown before stream cleanup; training-provenance/architecture/competitive-workload policy; versioned agent handshake and per-move evidence; physical/emulated device provenance and runtime-registered GPU evidence. External gates remain blocked. |
| 2026-11-24 | Phase 3C-A third review correction: competitive CLI/provenance orchestration, runtime-independent parity gates, persistent process-agent lifecycle, canonical tournament replay/budget validation, sustained-memory coverage and progress gates, per-combination device acceptance, malformed-input hardening, and documented native/browser collection-to-validation workflows. External gates remain blocked. |
| 2026-11-23 | Phase 3C-A after merged PR #115: strict detach acknowledgment validation; competitive checkpoint validator; role-balanced paired tournament harness/validator; sustained-memory validator; physical device/WebGPU evidence schema/runbook; Copenhagen depth-5 native reassessment. External competitive/hardware/sustained-memory gates remain blocked; Phase 3C in progress. |
| 2026-11-22 | Phase 3B PR #115 attachment cleanup hardening: direct ownership states pending/attached/detaching/released; failed attachment holds ownership until bounded detach; unacknowledged detach invalidates the worker; one detach implementation; provider switches/loads reject during uncertain cleanup and recover after safe release. |
| 2026-11-22 | Phase 3B experimental PR #115, based on merged `main` `64ec43e`: authoritative nested best-move validation and coverage-identifier hardening; opt-in direct worker `MessageChannel`; bounded legal-only inference cache; bounded independent-search batching; actual-browser validation and smoke benchmarks. Single-tree batching/subtree reuse deferred; relay/cache/batching remain opt-in where benefit is conditional. Phase 3B in progress; 3C and external gates outstanding. |
| 2026-11-21 | Phase 3A acceptance hardening: browser collector `raw_ok`/`harness_failures` now gate acceptance separately from per-case failures; malformed nested reports validate safely; fixed-time simulation accounting and root-visit/deadline metadata are enforced per runtime; 57 synthetic regression tests and fresh acceptance evidence. Phase 3A remains implementation/software validated; 3B/3C outstanding. |
| 2026-11-21 | Phase 3A review corrections: delay capability moved to a dedicated non-staged test worker entry point; normal worker rejects direct test actions and ordinary inference stays operational; end-to-end full/compact search parity gate for Brandubh/Tablut/Copenhagen, repetition/terminal/immediate-win, 32×2 and fixed-time behavior; unused serialized-position allocation removed from pending context (impact below noise). Phase 3A implementation-validated; 3B/3C remain outstanding. |
| 2026-11-21 | Phase 3A on merged `main` `c23845f`: independent workload identity recomputation; non-negative diagnostic counters; isolated test-only delay hook; extended/repeatable benchmarks and new boundary/serialization/extraction metrics; legal-only MCTS inference transport with base64 plane decoding and no duplicate browser position. Controlled smoke-model gains +22–87 %; Phase 3B/3C remain outstanding. Fresh Phase 3 r1 evidence. |
| 2026-11-21 | Phase 2 revision 4, integrated `main` `8a11ee6`: fresh-page analysis-position initialization with retry and selection preservation; independent native/browser fixed-work completion classes; owner-bound pending requests and inherited cleanup barrier for cascading supersession; explicit model identity required; bounded pending-inference test seam with healthy/unhealthy browser coverage; exception-safe parity CLI with structured failure schema; file-backed vs embedded custom workload sources with mandatory digest and property-order-independent comparison. Fresh r4/r9 evidence; r1–r3 preserved. |
| 2026-11-21 | Phase 2 revision 3 (final PR #108 corrections), integrated `main` `3074790`: exact terminal values and non-terminal best-move/root-visit semantics; recomputed full-position identity; versioned built-in/custom workload sources with exact state/history and file-digest validation; interpreted abandoned-inference outcomes with session invalidation; UI replacement admission awaits bounded cancellation cleanup (immediate restart, healthy reuse, unhealthy-drain replacement). Fresh r3/r8 evidence; r1/r2 preserved. |
| 2026-11-20 | Phase 2 revision 2 (PR #108 review corrections): ownership reserved before initialization with validated response association; cancellation drains/terminates inference and releases the generator without killing newer work; UI cancellation covers model download/init; bfcache restoration installs usable clients; parity/benchmark comparators independently validate coverage, budgets, semantics and raw repetitions; checkpoint weight fingerprint binds native evaluators to the export; independent production-MCTS reference tests; strict legal-probability contract; exception-safe context cleanup; lazy neural assets; mandatory staged-source digests; shared encoder measured 8.9–15.5× faster. Fresh r2/r7 evidence; r1 preserved. |
