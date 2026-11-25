# Browser analysis — Phase 3C-B competitive measurements and readiness

Phase 3C-B applies the merged Phase 3C-A validation platform to representative
browser-analysis runs. It records actual structural measurements, resolves the
remaining V1 provider-evidence finding, and states clearly which competitive,
sustained-memory, physical-device and GPU gates remain blocked.

**No competitive trained checkpoint, physical device or verified GPU runtime was
available in this environment.** The measurements below use fresh, disposable
32-channel / 2-block smoke checkpoints created by `smoke_checkpoint.py`. They are
synthetic structural-representative models, not competitively trained models, and
are never presented as playing-strength evidence.

## A. Acceptance matrix

| Gate | Coverage | Measurement status | Acceptance | Evidence | Blocker / next action |
| --- | --- | --- | --- | --- | --- |
| V1 provider-evidence consistency | Brandubh browser WASM | Validated | Met | `tests/test_tournament_execution.py::test_offline_wasm_evidence_cannot_claim_verified_gpu_execution` | Owner: none |
| Competitive checkpoint provenance | None (no owner checkpoints) | Blocked | Not met | [blocked report](phase3cb-competitive-blocked.json) | Owner supplies private trained checkpoints per ruleset |
| ONNX export/numerical parity | 32×2 synthetic, all rulesets | Measured | Structural/partial only (not competitive) | `phase3cb-*` parity evidence | Competitive weights required |
| Level A search parity | All three rulesets, 21/21 exact | Measured | Structural pass | [browser A](phase3cb-browser-a.json), [native A](phase3cb-native-a.json) | Competitive weights required for 3C-B acceptance |
| Level B model-backed parity | 7/7 best-move parity each ruleset | Measured | Structural pass with synthetic model | [Brandubh](phase3cb-browser-b-brandubh.json), [Tablut](phase3cb-browser-b-tablut.json), [Copenhagen](phase3cb-browser-b-copenhagen.json) | Competitive weights required |
| Fixed-work benchmark matrix | 128/256/512/1024, opening+tactical, 1 repetition | Measured | Partial: competitive policy requires all five kinds and ≥2 repetitions | [Brandubh](phase3cb-browser-bench-brandubh.json) and other ruleset reports | Owner full policy workload |
| Fixed-time benchmark matrix | 0.5/1/2/5 s | Measured | Partial as above | comparison reports | Owner full policy workload |
| 1,024 simulations < 2 s | Brandubh 2009 ms; Tablut 2712 ms; Copenhagen 3667 ms | Measured | Not met as a general claim (Brandubh approximately at target, others miss) | benchmark reports | Smaller/faster models or runtime optimization; not a merge gate |
| Browser vs native neural MCTS tournament | 2 pairs / 4 games, synthetic model | Measured, structural only | Not met (0 complete pairs; 3 truncated) | [report](phase3cb-tournament-browser-vs-native-neural.json) | ≥50 meaningful pairs with competitive model |
| Browser vs native Jarl tournament | 2 pairs / 4 games | Measured, structural only | Not met (1 complete pair, 2 truncated) | [report](phase3cb-tournament-browser-vs-native-jarl.json) | Competitive model + full sample |
| 10-minute sustained profile | Brandubh 32×2, real 600 s post-warmup | Measured | Partial/blocked (WASM capacity unobservable) | [profile](phase3cb-memory-10m-profile.json), [validation](phase3cb-memory-10m-validation.json) | Runtime metric exposure or documented partial acceptance |
| 30-minute sustained profile | Brandubh 32×2, real 1800 s post-warmup | Measured | Not met (JS heap growth threshold; compatibility-file consistency) | [profile](phase3cb-memory-30m-profile.json), [validation](phase3cb-memory-30m-validation.json) | Investigate JS heap step; rerun with telemetry-only progress |
| Lifecycle profiles | Cancel/restart, model reload, worker replacement | Measured | Partial/blocked (WASM unobservable; worker replacement RSS growth) | `phase3cb-memory-*-profile.json` | Owner lifecycle acceptance with observable metrics |
| Executable owner benchmark/tournament workflow (A1–A2) | Extended native/browser workload, 50-pair corpus generation, all adapter comparisons | Validated structurally/integration | Met | `test_extended_native_browser_benchmark_workloads_are_compatible`, `tests/test_tournament_openings.py` | Owner private models/hardware for full samples |
| Sustained workload driver integrity (A3–A8) | Failure accounting, nonterminal selection, telemetry-only progress, lifecycle modes | Validated with short actual-browser runs | Met for driver correctness; sustained acceptance remains blocked | `spikes/browser_offline/test_sustained_workload.mjs`, `tests/test_sustained_workload_driver.py` | Full owner 10/30-minute rerun |
| Physical device matrix | None | Blocked | Not met | [blocked matrix](phase3cb-device-evidence-blocked.json), [runbook](device-matrix-phase3c.md) | Physical Windows/Linux/macOS/Android/Safari runs |
| Verified GPU execution | No WebGPU adapter | Blocked | Not met | [capability report](phase3cb-webgpu-capability-blocked.json) | Runtime with supported placement diagnostics + physical GPU |
| Copenhagen depth-5 | Native, 2/5/10/30 s | Measured | Not met | [reassessment](phase3cb-copenhagen-depth5.json) | Owner decision on adaptive target |

## A.1 Correctness and reproducibility corrections

This revision resolves the review findings A1–A8:

- **A1 benchmark compatibility:** owner commands now use extended native and
  browser workloads with identical position kinds, budgets (128/256/512/1024 and
  0.5/1/2/5 s), repetitions (3), warmup (1), seed 7 and exploration 1.4. The
  native benchmark CLI gained `--repetitions`/`--warmup`; the opt-in integration
  test `test_extended_native_browser_benchmark_workloads_are_compatible` compares
  actual extended native/browser reports. The runbook includes full/compact and
  direct/relay parity commands and a concrete competitive manifest generator.
- **A2 opening corpus:** `tournament_openings.py` generates deterministic seeded
  legal-playout corpora with nonterminal states, correct ply/history, full-state
  symmetry-aware duplicate rejection, generator/seed/corpus digest metadata and
  validation. The tournament CLI records `openings_source`, rejects duplicate
  names/states, and rejects playing-strength runs with fewer than ten opening pairs
  before launching. The runbook includes 50-pair commands for all three rulesets
  and all required comparisons.
- **A3 workload failures:** the sustained driver emits cumulative
  `successful_searches`, `workload_failures`, `neural_searches`,
  `completed_simulations`, `neural_inferences`, no-inference shortcuts, completion
  reasons and provider placements. Failed searches never increment successful
  progress; exit codes are 0 (healthy), 2 (partial with failures) and 1 (no
  successful work/fatal). Memory validation treats workload failures as partial
  (non-accepted) even when RSS/heap/progress checks pass.
- **A4 position selection:** sustained runs select only nonterminal analysis
  positions (`opening`, `midgame`, `tactical`, `endgame`, `repetition`), exclude
  terminal and immediate-win fixtures, deduplicate states, record the selected
  positions and require observed neural-analysis searches (not just request
  counts) for acceptance. Terminal/no-inference shortcuts are counted separately.
- **A5 native neural integrity:** the adapter now calls
  `prepare_neural.validate_export_dir()` (validated marker/manifest/ONNX/reference
  digests), verifies checkpoint weights, architecture, encoder, ruleset and size,
  retains the exact verified model, injects it through `_model` (no path-based
  reload/cache), reports CPU/device identity, and rejects modified/stale/mismatched
  artifacts before handshake. Tests cover corrupted ONNX, stale marker, mismatched
  checkpoint, retained-model requests and valid execution.
- **A6 telemetry-only progress:** the compatibility progress file is optional; the
  recommended owner workflow uses telemetry-only progress with fresh
  `--overwrite` files, unique session ids and a ready-file handshake before the
  collector starts. Legacy progress-file diagnostics remain explicit.
- **A7 lifecycle coverage:** the driver adds `model-replacement` (different
  validated model identity with cache-invalidation check), `cache-churn`,
  `batching` (two independent coordinators with actual batch utilization/foreground
  elapsed time) and `client-disposal` alongside repeated/cancel/reload/worker
  replacement. Model identity transitions are recorded explicitly; replacement
  profiles that change model identity remain blocked by the strict collector
  identity contract rather than silently accepted.
- **A8 driver regression suite:** `test_sustained_workload.mjs` covers selection,
  work accounting, failures/exit statuses, telemetry identity, no-inference
  shortcuts, cancellation handling and optional actual-browser runs for repeated,
  cancel/restart, reload, worker replacement, cache churn, batching and model
  replacement. `tests/test_sustained_workload_driver.py` runs it when Node is
  available; actual-browser modes are opt-in via `SPIKE_NEURAL_DIR` and
  `SPIKE_REPLACEMENT_MODEL_*`.

## A.2 Integration corrections (B1–B6)

- **B1 model replacement:** model-replacement profiles authorize explicit
  A↔B↔A transitions in the collector; ordinary profiles still reject model
  changes. The driver alternates between distinct validated staged model
  digests, skips loading the already-active model, counts a replacement only after
  successful loading, cache invalidation and a successful recovery search, and
  emits transition source/destination/sequence/timestamp. Transition history is
  bounded, cache-invalidation failures make the workload non-accepted, and the
  validator checks the chain from the original expected model to the final model.
  Evidence: [profile](phase3cb-memory-model-replacement-r2-profile.json),
  [validation](phase3cb-memory-model-replacement-r2-validation.json),
  [workload](phase3cb-memory-model-replacement-r2-workload.json).
- **B2 lifecycle profiles:** collector/validator now support
  `model-replacement`, `client-disposal`, `cache-churn` and
  `independent-batching`; driver mode→profile mapping is documented. Required
  events are observed inside the acceptance window only. Client disposal is
  distinct from worker replacement, cache-churn produces genuine bounded eviction
  (small cache budgets), batching requires successful multi-context rounds, and
  model reload/replacement remain distinct. Actual short lifecycle runs were
  collected and validated (structural true; acceptance partial because WASM heap
  capacity is unobservable). Evidence:
  [model replacement](phase3cb-memory-model-replacement-r2-profile.json),
  [client disposal](phase3cb-memory-client-disposal-r2-profile.json),
  [cache churn](phase3cb-memory-cache-churn-r2-profile.json),
  [independent batching](phase3cb-memory-batching-r2-profile.json).
- **B3 meaningful work:** `recordSuccess` rejects empty/missing outcomes, missing
  best moves, malformed/negative/non-integer counters, invalid completion reasons,
  absent effective settings, provider fallback/contradictions and invented
  observations. No-inference immediate-win/terminal shortcuts are valid but do not
  count as neural searches. Sustained acceptance requires distributed neural-search
  progress inside the measured window, monotonic counters, zero workload failures,
  a completed final summary and consistent successful/neural/shortcut/inference
  counts. Cache-invalidation failures and incomplete workloads are non-accepted.
- **B4 batching:** cumulative `batchStatistics()` is converted to deltas with reset
  handling, actual successful multi-context rounds and max batch size are recorded,
  per-search foreground latency is separate from round elapsed time, and peak
  search/inference/total workers come from the temporary coordinators themselves.
  All temporary coordinators are disposed in `finally` on success, cancellation or
  failure. Independent batching remains opt-in.
- **B5 readiness:** `wait_for_ready.py` bounds startup (default 120 s), checks
  process liveness, validates ready JSON session/model/mode/positions and
  terminates the owned workload on early exit, timeout or mismatch. The owner
  runbook uses it instead of unbounded polling.
- **B6 attestation:** `competitive_manifest.py` reuses
  `model_provenance`/`_load_manifest`, requires an explicit owner attestation
  statement for competitive classification and records the actual UTC timestamp.
  Structural tests only generate structural manifests; no competitive attestation
  is fabricated in tests or evidence.

## A.3 Evidence-validation corrections (C1–C6)

- **C1 model transitions:** Model replacement requires an explicit
  `--approved-model-sha256` set established before collection; the original model
  must be included and at least two fingerprints are required. The collector
  validates source/destination/sequence/timestamp/recovery/cache invalidation,
  rejects same-model and third-model transitions, and commits identity/transition
  state only after the entire telemetry record validates. The retained 1,024-entry
  history carries an anchor (`first_retained_sequence`, `first_retained_from`),
  final model, total count and retained-chain digest, so history eviction remains
  verifiable. Sample model identities are checked against the transition timeline,
  not a union of observed models. Ordinary profiles remain immutable.
  Evidence: [profile](phase3cb-c1-model-replacement-profile.json),
  [validation](phase3cb-c1-model-replacement-validation.json),
  [workload](phase3cb-c1-model-replacement-workload.json).
- **C2 final workload evidence:** Collector reports separate
  `acceptance_window` counters/transitions and a `final_session` summary bound to
  the same session/profile/workload/model set. Final counters may exceed the
  measured window (continued work) but cannot fall below it; failures after the
  last sample, missing/reset counters, incomplete summaries, missing exit status,
  failed cleanup, premature termination and final model transitions are detected.
  The collector cannot reap the workload process because it is not its parent, so
  it records `actual_process_exit_status: null` with the reason and reports the
  workload's predicted exit status separately; the owner runbook preserves the
  actual `wait` status. Final telemetry is emitted only after browser/context
  cleanup. Historical schema-3 profiles without this contract remain historical
  evidence and are not presented as newly accepted.
- **C3 provider/search identity:** Successful-search validation receives the
  expected workload provider and original request settings and verifies effective
  iterations, time limit, exploration, seed, completion and provider observations.
  WASM cannot report WebGPU; WebGPU cannot silently fall back to WASM/CPU; GPU
  verification flags are strict booleans; unverified/mixed placements remain
  classified rather than promoted. Provider placements are accumulated only from
  validated searches; model initialization placements are separate. Legitimate
  no-inference shortcuts do not invent a placement.
- **C4 workers/batching/lifecycle:** Telemetry keeps separate search and inference
  worker counts and total simultaneous usage, including the primary coordinator
  and temporary batching coordinators. Validation enforces the declared worker
  budget against observed peak usage, validates peak consistency/types, keeps
  cumulative batch deltas/resets, distinguishes multi-request batches, and
  requires successful multi-context work inside the acceptance window. Temporary
  coordinators are disposed in `finally` and cleanup failures are reported and
  make the workload non-successful. Worker replacement verifies a search-worker
  generation change; client disposal verifies the old coordinator was disposed and
  a new runtime installed; reload/replacement/restart events count only after a
  valid recovery search. Evidence:
  [batching profile](phase3cb-c4-batching-profile.json),
  [validation](phase3cb-c4-batching-validation.json),
  [workload](phase3cb-c4-batching-workload.json).
- **C5 readiness ownership:** `wait_for_ready.py` rejects nonpositive PIDs and
  nonfinite/nonpositive timeouts, captures the original process identity
  (start-time based), revalidates it before every signal, uses pidfds where
  supported, never signals a process group, snapshots owned descendants before
  cleanup and reports incomplete cleanup. PID reuse/disappearance leaves the
  replacement process untouched. The owner runbook uses a cleanup trap, stops on
  readiness/collection failure, preserves `wait` status and validates only after
  final evidence.
- **C6 malformed inputs:** All new counters, flags, final-summary fields,
  cache-invalidation/batch/peak fields and transition-history fields are
  type-checked before arithmetic, set membership or insertion. Booleans are
  rejected for strict integers; negative/nonfinite values and incompatible
  strings/lists/objects/null produce structured invalid or partial reports without
  uncaught exceptions.

## A.4 Final ownership/completion/timeline corrections (D1–D4)

- **D1 ownership persistence:** The readiness handshake writes a bounded
  ownership document (`kind`, schema, pid, start-time/cmdline identity, session,
  original model, mode, capture time) before waiting. `--terminate-only` requires
  that persisted document and never establishes ownership from the current PID
  occupant. It revalidates the original start-time identity before every signal,
  uses pidfds where available, revalidates descendant parent relationships and
  identities, reports ambiguous/incomplete cleanup, and treats an already-gone
  original instance as clean without adopting a replacement. Repeated cleanup is
  idempotent. The owner runbook reuses the same file for readiness failure,
  collection failure, interruption and normal completion.
- **D2 evicted timeline:** The validator no longer assumes that the retained
  history anchor's source model was active before the anchor. When an earlier
  interval is evicted it returns “unknown” for that interval; the collector emits
  compact sample-aligned anchors (`timestamp`, model, transition sequence) so
  earlier samples are checked against the observed anchor and approved set. The
  affected historical interval is classified partial rather than assigned an
  invented identity; the retained suffix remains strictly checked. A live
  collector→validator regression exercises 1,100 A↔B transitions with samples
  before and after the 1,024-entry boundary; digests are never treated as
  reconstruction of unavailable transitions. The compressed actual report is
  [phase3cb-d2-evicted-history-profile.json.gz](phase3cb-d2-evicted-history-profile.json.gz)
  with a compact [summary](phase3cb-d2-evicted-history-summary.json); its earlier
  interval is honestly partial, not assigned an invented model.
- **D3 completion evidence:** `finalize_workload_evidence.py` is the minimal
  executable parent-side step after `wait`. It requires the persisted ownership
  document, actual wait status, final workload summary and collected profile;
  reconciles session/workload/profile/mode/model/counters/cleanup/completion;
  rejects contradictory actual/reported statuses and missing/malformed summary
  output; writes completion evidence atomically. Validation requires explicit
  successful parent-observed status for complete acceptance, while preserving
  diagnostic partial profiles when parent-side evidence is unavailable. The
  collector still never fabricates an actual exit status.
- **D4 malformed live transitions:** Source/destination fingerprints, initial and
  subsequent model identities, strict integer sequences, timestamps, recovery and
  cache flags are validated before set membership/comparison. List/object/bool/
  null/malformed-string values are rejected without committing identity or
  transition state; later valid telemetry continues to process.

**Python 3.11 baseline:** the six `tests/test_mcts_optimization.py` frozen-reference
cases (`case1`, `case2`, `case5`, `case6`, `case9`, `case10`) fail identically on
base `f2590b993cab24d7d3ab89249174ac052af5d2e6` and current head under the same
Python 3.11.18/venv. They are pre-existing compatibility failures, not introduced
by this PR; production MCTS and frozen digests were not modified.

## B. Software delivered in Phase 3C-B

1. **V1 provider-evidence consistency.** Live process-agent validation and saved
   tournament-report validation now share `_provider_evidence_failures()`.
   WASM/CPU/mixed/unverified observations cannot claim verified GPU execution;
   GPU flags are strict booleans; contradictory observation fields are rejected;
   no-inference shortcuts must report no placement; native CPU providers remain
   valid. A mutation of genuine committed WASM evidence claiming
   `gpu_execution_verified: true` is rejected offline and by the shared live check.
2. **Executable native neural tournament adapter.** `native_tournament_agent.py`
   gained `--engine neural --checkpoint ... --model-export ...`, using the
   production neural MCTS and checkpoint loader. It reports the ONNX/weight/model
   identity, `torch-cpu` provider, MCTS simulations/inference counts, effective
   settings, seeds and completion reasons through the version-2 handshake and
   per-move evidence contracts. Browser vs native neural MCTS is now executable.
3. **Sustained workload driver.** `sustained_workload.mjs` runs repeated real
   browser neural-MCTS searches and optional cancellation, model reload or worker
   replacement, emitting live session/model/workload telemetry and progress. It is
   measurement tooling only.
4. **Narrow memory-validator correction.** `cache_bytes` growth is bounded by the
   declared cache budget rather than a general 25 %/1 MiB growth threshold; cache
   filling is expected, while exceeding `max_cache_bytes` still fails.
5. **Owner runbook** below and refreshed evidence links; no production UI, model
   delivery, rules, search algorithm or security-boundary changes.

## C. Competitive model results

No competitive trained checkpoints were available. For every ruleset a fresh
32×2 smoke checkpoint was created and exported through the validated ONNX pipeline.
These models have no competitive strength and are labelled `structural
representative` (synthetic training, 12 steps) throughout.

| Ruleset | Encoder | Architecture | Native/browser Level B | Placement |
| --- | --- | --- | --- | --- |
| Brandubh 7×7 | six-plane-v1 | 32 ch / 2 blocks | 7/7 best-move parity | WASM |
| Tablut 9×9 | six-plane-v1 | 32 ch / 2 blocks | 7/7 best-move parity | WASM |
| Copenhagen 11×11 | six-plane-v1 | 32 ch / 2 blocks | 7/7 best-move parity | WASM |

Level A shared-driver parity passed 21/21 exactly across all rulesets. Level B
comparisons accepted execution validity, workload completion and best-move parity;
the comparison reports are committed with the browser reports. These establish
software correctness for a representative architecture, **not** competitive-model
parity or strength.

## D. Native/browser performance

Measurements are native ONNX CPU versus browser Pyodide/ORT Web WASM, same 32×2
model, same positions (opening/tactical quick workload), one measured repetition
(warmup 1), relay transport, compact legal-only inference, cache disabled. Medians
over the two quick positions:

| Ruleset | Cold model load | Equal-work browser/native ratio | 128 sims | 256 sims | 512 sims | 1024 sims |
| --- | --- | --- | --- | --- | --- | --- |
| Brandubh | 3,756 ms | 0.291 | 393 ms | 541 ms | 1,138 ms | 2,009 ms |
| Tablut | 3,525 ms | 0.503 | 500 ms | 661 ms | 1,259 ms | 2,712 ms |
| Copenhagen | 4,998 ms | 0.449 | 672 ms | 1,342 ms | 1,862 ms | 3,667 ms |

Fixed-time completed work (median simulations):

| Ruleset | 0.5 s | 1 s | 2 s | 5 s |
| --- | --- | --- | --- | --- |
| Brandubh | 252 (native 678) | 404 (native 1,910) | 1,009 (native 3,719) | 2,558 (native 4,096 ceiling) |
| Tablut | 218 (native 558) | 434 (native 1,187) | 750 (native 2,166) | 1,568 (native 4,096 ceiling) |
| Copenhagen | 152 (native 287) | 310 (native 646) | 492 (native 867) | 1,256 (native 3,014) |

Equal-work ratios are reported only where both runtimes completed the same work.
The 1,024-simulation aspirational target is approximately met for the smallest
board (2.0 s on Brandubh) but missed for Tablut (2.7 s) and Copenhagen (3.7 s)
with this synthetic representative model. Browser throughput is dominated by
inference/transport rather than Python search; the measured ratio range is
0.29–0.50. These are partial-policy measurements: competitive acceptance requires
all five position kinds and at least two repetitions, which were not run here.

### Optimization comparisons (Brandubh, 32×2, 256/1024 simulations)

| Variant | 256 opening | 1024 opening | 256 tactical | 1024 tactical |
| --- | --- | --- | --- | --- |
| Relay compact (baseline) | 604 ms | 2,027 ms | 458 ms | 1,868 ms |
| Direct transport | +4.1 % | +28.9 % | +7.4 % | +8.1 % |
| Exact cache enabled | -19.4 % | -41.5 % | +43.4 % | +30.0 % |
| Full-logit response | +155.5 % | +130.8 % | +74.2 % | +45.6 % |

- **Relay remains the default.** Direct transport was slower in this run and had
  no reproducible gain across Phase 3B/3C-B.
- **Cache is workload-dependent.** Repeated opening workloads benefited strongly
  at 1024 simulations (cache hit rate 80.3 %, 4.1 MB of 64 MB); the tactical case
  was slower, so caching stays opt-in.
- **Full-logit responses are consistently slower**; compact legal-only inference
  remains the search path.
- **Independent batching** completed both contexts, but the 64-simulation test was
  slower when batched (765 ms) than sequential (477 ms) on CPU/WASM. Keep it
  opt-in; batch utilization is reported in `phase3cb-batching-*.json`.

## E. Playing-strength tournaments

Both required primary comparisons are executable with the new native neural
adapter. The runs below are structural smoke runs with the synthetic 32×2 model
and must not be interpreted as strength results:

| Comparison | Mode | Games | Completed | Truncated | Errors | Complete pairs |
| --- | --- | --- | --- | --- | --- | --- |
| Browser neural vs native neural (same model) | fixed-work 8 simulations, ≤12 plies | 4 | 1 | 3 | 0 | 0 |
| Browser neural vs native Jarl | fixed-time 2 s, ≤12 plies | 4 | 2 | 2 | 0 | 1 |

Truncated games are not rule draws. With 0–1 complete pairs and a non-competitive
model, no score, Elo or superiority claim is made. The validator's paired bootstrap
requires at least ten complete pairs for playing-strength eligibility; the target
for owner runs is 50 pairs (100 games) per primary comparison.

## F. Sustained memory and runtime stability

Real browser runs, 32×2 Brandubh, continuous search (64 simulations per request),
cache enabled, telemetry emitted by the live browser workload. The acceptance
window excludes the 60 s warmup.

| Profile | Acceptance window | Requests | RSS median/peak | JS heap median/peak | Cache peak | Workers | Validation |
| --- | --- | --- | --- | --- | --- | --- | --- |
| sustained-10m (historical) | 600 s | 9,086 | 930.9 / 971.2 MB | 9.5 / 9.5 MB | 12.6 MB | 2 | partial: WASM capacity unavailable |
| sustained-10m (telemetry-only rerun) | 600 s | 7,719 | see validation/profile | 9.5 MB stable | 12.6 MB | 2 | partial: WASM capacity unavailable; zero workload failures |
| sustained-10m (corrected rerun) | 600 s | see workload | see profile | 9.5 MB stable | bounded | 2 | partial: WASM capacity unavailable; zero failures, complete summary |
| sustained-30m (historical) | 1,800 s | 25,445 | 947.4 / 979.1 MB | 9.5 / 19.6 MB | 12.6 MB | 2 | invalid: JS-heap growth threshold; compatibility progress lag; WASM unavailable |
| cancel-restart (historical) | 90 s | 2,143 | 907.3 / 945.1 MB | 9.5 / 9.5 MB | 12.5 MB | 2 | partial: WASM unavailable |
| model-reload (historical) | 90 s | 1,760 | 877.7 / 957.4 MB | 9.5 / 9.5 MB | 0.3 MB | 2 | partial: WASM unavailable |
| worker-replacement (90 s historical) | 90 s | 391 | 863.7 / 981.8 MB | 9.5 / 9.5 MB | 0.5 MB | 1–2 | invalid: RSS growth threshold; WASM unavailable |
| worker-replacement (240 s rerun) | 240 s | 311 | 858→898 MB medians; peak 1,015 MB | 9.5 MB stable | 0.5 MB | 1–2 | partial: WASM unavailable; growth threshold not exceeded |
| sustained-30m (corrected rerun) | 1,800 s | 18,136 | 936.9 MB median / 978.8 MB peak | 9.5→31.6 MB (plateau) | bounded | 2 | invalid: JS-heap growth threshold; partial: WASM unavailable |

Observations:

Corrected telemetry-only rerun evidence:
[10-minute profile](phase3cb-memory-10m-r2-profile.json),
[validation](phase3cb-memory-10m-r2-validation.json),
[workload summary](phase3cb-memory-10m-r2-workload.json),
[worker-replacement profile](phase3cb-memory-worker-replacement-r2-profile.json),
[validation](phase3cb-memory-worker-replacement-r2-validation.json),
[workload summary](phase3cb-memory-worker-replacement-r2-workload.json).

- Process RSS stayed roughly 930–979 MB for the 10/30-minute runs; no monotonic
  unbounded process growth was observed. The 30-minute validation still fails its
  declared growth threshold because the JS heap stepped from 9.5 MB to 19.6 MB at
  about 17 minutes and then plateaued. That step is a real finding; acceptance is
  not claimed.
- Browser worker count stayed at 2 for sustained/reload/cancel profiles and
  fluctuated 1–2 during worker replacement; OS process count stayed at 7, and is
  recorded separately.
- Cache stayed within the 64 MB budget (12.6 MB peak); cache hits and bytes are
  reported by the actual inference cache.
- Cancellation/restart completed 1,017 observed cancellations with recovery;
  model reload completed 113 observed reloads; worker replacement completed 16
  observed replacements with recovery.
- The historical 30-minute run recorded eight transient progress-file/telemetry
  mismatches. The corrected recommended workflow is telemetry-only and does not
  require independent files to be atomic; the historical run remains non-accepted.
- **Memory-growth investigation:** the original 30-minute JS p50 stepped from
  9.5 MB to 19.6 MB at ~17 minutes; the corrected B1–B4 30-minute rerun showed a
  step to 31.6 MB at ~20 minutes and then a flat plateau through minute 30. Worker
  count remained 2, OS process count 7, process RSS stayed around 912–954 MB
  median, and cache remained bounded. No retained search/coordinator reference
  accumulation was found: the coordinator holds only the active search, completed
  contexts are released, and worker count did not grow. The step is consistent with
  V8 old-space/GC growth rather than a demonstrated unbounded leak, but it is not
  proven; the historical invalid reports and the corrected invalid report are
  preserved. The 90-second worker-replacement RSS failure was a startup transient;
  a corrected 240-second rerun (52 replacements) showed median 858→898 MB and peak
  1,015 MB, below the declared threshold with stable workers/processes. No code
  defect was reproduced; these remain investigation observations, not acceptance.
  Corrected evidence: [10-minute r3](phase3cb-memory-10m-r3-profile.json),
  [30-minute r3](phase3cb-memory-30m-r3-profile.json),
  [30-minute validation](phase3cb-memory-30m-r3-validation.json).

  **Diagnostic probe (implemented, not yet run for 30 minutes).** Add
  `--heap-gc-probe` to the driver; every telemetry sample then forces a CDP
  `HeapProfiler.collectGarbage` before reading `performance.memory` and records
  both `js_heap_used_bytes` and `js_heap_after_gc_bytes`. If only the pre-GC value
  steps and the after-GC value remains flat, the observation is allocated/old-space
  growth rather than retained objects; if both step and remain elevated, capture
  CDP heap snapshots at the start, before the step and after the plateau:

  ```sh
  node spikes/browser_offline/sustained_workload.mjs /private/spike-brandubh \
    --duration 1890 --mode repeated --session heap-probe --provider wasm \
    --telemetry /tmp/heap-probe.jsonl --ready /tmp/heap-probe.ready --overwrite \
    --iterations 128 --time-limit 2 --cache --heap-gc-probe --output /tmp/heap-probe.json
  # In Chromium DevTools (Performance/Memory) or a Playwright CDP session:
  #   HeapProfiler.enable; HeapProfiler.collectGarbage;
  #   HeapProfiler.takeHeapSnapshot(reportProgress=false)
  # Compare retained constructors/objects between the snapshots. Keep snapshots
  # outside the repository; they are large and may contain runtime state.
  ```

  This probe changes GC timing and is diagnostic only; it is never enabled in
  normal acceptance runs. A short 12 s probe completed with `js_heap_used_bytes`
  and `js_heap_after_gc_bytes` both flat at 9.5 MB; it cannot reproduce the
  ~20-minute step, so the 30-minute diagnostic (and retained-object snapshot
  comparison) remains pending owner hardware. The unobservable WASM heap metric
  remains a runtime limitation.
- WASM heap capacity is not exposed by the pinned ORT Web runtime, so these
  profiles remain partial/blocked rather than accepted. No substitute metric was
  used.

## G. Physical devices, WebGPU and Windows

- **Physical devices: blocked.** No Windows, Linux Firefox, Android or Safari
  hardware was available. The mandatory matrix remains blocked with reasons in
  [phase3cb-device-evidence-blocked.json](phase3cb-device-evidence-blocked.json);
  emulation is not physical evidence.
- **WebGPU: blocked.** The environment reports `null adapter`; no WebGPU session
  was created. See [webgpu-capability-blocked.json](phase3cb-webgpu-capability-blocked.json).
  No verified-GPU claim is made, and the fail-closed evidence registry is
  unchanged.
- **Windows subprocess cleanup:** not physically verified. Windows descendant
  cleanup after the immediate parent exits remains best-effort; stale `taskkill`
  is never used. Linux process-group/token ownership remains the verified path.
  A Windows Job Object subsystem is deferred to a separate focused PR.

## H. Copenhagen depth-5 reassessment

| Position | 2 s | 5 s | 10 s | 30 s |
| --- | --- | --- | --- | --- |
| copenhagen-opening | depth 3 | depth 3 | depth 3 | depth 4 |
| copenhagen-midgame | depth 3 | depth 3 | depth 3 | depth 4 |
| copenhagen-repetition | depth 3 | depth 3 | depth 4 | depth 4 |

At 2/5/10/30 s all three positions remain deadline-limited and do not complete
depth 5; 30 s reaches depth 4 at best. Elapsed times track the requested budgets
(deadline overshoot is small relative to the budget).

**Recommendation:** keep Copenhagen depth 5 explicitly blocked; do not silently
lower production Jarl strength. If a practical target is required, the next
approved step should be adaptive iterative deepening with a bounded practical
deadline (or an owner-approved revised depth/time requirement), evaluated as a
separate change.

## I. Owner-side Phase 3C-B runbook

Private checkpoints, ONNX files, telemetry and device reports stay outside the
repository. The commands below were checked against the implemented CLIs; replace
`/private/...` with owner paths and keep unique session ids/files per run.

```sh
# 0. Environment
uv sync --frozen --extra train --extra dev
uv pip install --python .venv/bin/python -r spikes/browser_offline/requirements-onnx.txt
npm --prefix frontend ci && npx --prefix frontend playwright install chromium

# 1. Validated export and staging (repeat per ruleset)
.venv/bin/python spikes/browser_offline/onnx_export.py /private/brandubh.pt /private/export-brandubh
.venv/bin/python spikes/browser_offline/prepare_neural.py \
  --model-dir /private/export-brandubh --output /private/spike-brandubh

# 2. Numerical/search parity: Level A, Level B, direct/relay and full/compact
.venv/bin/python spikes/browser_offline/native_mcts_parity.py --level a \
  --iterations 128 --output /tmp/native-a.json
node spikes/browser_offline/mcts_parity.mjs /private/spike-brandubh \
  --native /tmp/native-a.json --level a --output /tmp/browser-a.json
.venv/bin/python spikes/browser_offline/native_mcts_parity.py --level b \
  --evaluator onnx --model-export /private/export-brandubh \
  --checkpoint /private/brandubh.pt --rulesets brandubh --iterations 256 \
  --output /tmp/native-b.json
node spikes/browser_offline/mcts_parity.mjs /private/spike-brandubh \
  --native /tmp/native-b.json --level b --provider wasm --output /tmp/browser-b.json
node spikes/browser_offline/test_search_path_parity.mjs /private/spike-brandubh \
  --gate direct-relay-compact --output /tmp/direct-relay-fixed.json
node spikes/browser_offline/test_search_path_parity.mjs /private/spike-brandubh \
  --gate direct-relay-compact --fixed-time --output /tmp/direct-relay-fixed-time.json
node spikes/browser_offline/test_search_path_parity.mjs /private/spike-brandubh \
  --gate relay-full-compact --output /tmp/relay-full-compact.json

# 3. Compatible native/browser benchmark workloads (same extended preset,
#    budgets, repetitions, warmup, seed and exploration)
.venv/bin/python spikes/browser_offline/native_mcts_benchmark.py \
  --model-export /private/export-brandubh --checkpoint /private/brandubh.pt \
  --evaluator onnx --extended --simulations 128,256,512,1024 \
  --time-budgets 0.5,1,2,5 --repetitions 3 --warmup 1 --seed 7 --exploration 1.4 \
  --output /tmp/native-bench.json
node spikes/browser_offline/mcts_benchmark.mjs /private/spike-brandubh \
  --provider wasm --extended --simulations 128,256,512,1024 \
  --time-budgets 0.5,1,2,5 --repetitions 3 --warmup 1 --seed 7 --exploration 1.4 \
  --native /tmp/native-bench.json --output /tmp/browser-bench.json
.venv/bin/python spikes/browser_offline/mcts_benchmark.py /tmp/native-bench.json \
  /tmp/browser-bench.json --output /tmp/benchmark-comparison.json

# 3b. Optimization comparisons (opt-in; keep relay as default)
for variant in relay direct cache full; do
  args="--transport relay --payload-mode compact"
  [ "$variant" = direct ] && args="--transport direct --payload-mode compact"
  [ "$variant" = cache ] && args="--transport relay --payload-mode compact --cache"
  [ "$variant" = full ] && args="--transport relay --payload-mode full"
  node spikes/browser_offline/mcts_benchmark.mjs /private/spike-brandubh \
    --provider wasm --quick --simulations 256,1024 --time-budgets 1,2 \
    --repetitions 1 --warmup 1 $args --output /tmp/bench-$variant.json
done
node spikes/browser_offline/test_independent_batching.mjs /private/spike-brandubh \
  --mode sequential --simulations 64 --output /tmp/batching-sequential.json
node spikes/browser_offline/test_independent_batching.mjs /private/spike-brandubh \
  --mode batched --simulations 64 --output /tmp/batching-batched.json

# 4. Competitive evidence manifest: requires explicit owner classification and
#    attestation. The timestamp is recorded at execution time; structural tests do
#    not create competitive attestations. Reuse the validated provenance helper.
export RULESET=brandubh CHECKPOINT=/private/brandubh.pt EXPORT=/private/export-brandubh
export EVIDENCE=/tmp/phase3cb-evidence
mkdir -p "$EVIDENCE"
cp /tmp/native-a.json "$EVIDENCE/native-a.json"
cp /tmp/browser-a.json "$EVIDENCE/browser-a.json"
cp /tmp/native-b.json "$EVIDENCE/native-b.json"
cp /tmp/browser-b.json "$EVIDENCE/browser-b.json"
cp /tmp/direct-relay-fixed.json "$EVIDENCE/direct-relay-fixed.json"
cp /tmp/direct-relay-fixed-time.json "$EVIDENCE/direct-relay-fixed-time.json"
cp /tmp/native-bench.json "$EVIDENCE/native-bench.json"
cp /tmp/browser-bench.json "$EVIDENCE/browser-bench.json"
export OWNER_ATTESTATION_STATEMENT="Owner attests this checkpoint was competitively trained."
.venv/bin/python spikes/browser_offline/competitive_manifest.py \
  --checkpoint "$CHECKPOINT" --export-dir "$EXPORT" --evidence-dir "$EVIDENCE" \
  --validation-level competitive-trained --policy competitive \
  --attestation-statement "$OWNER_ATTESTATION_STATEMENT" \
  --output "$EVIDENCE/manifest.json"
.venv/bin/python spikes/browser_offline/competitive_validation.py \
  --checkpoint "$CHECKPOINT" --export-dir "$EXPORT" \
  --report "$EVIDENCE/manifest.json" --output /tmp/competitive-validation.json

# 5. Reproducible 50-pair opening corpora (repeat for tablut/copenhagen)
.venv/bin/python spikes/browser_offline/tournament_openings.py \
  --ruleset brandubh --pairs 50 --seed 20261124 --max-plies 120 \
  --output /private/openings-brandubh.json
.venv/bin/python spikes/browser_offline/tournament_openings.py \
  --ruleset tablut --pairs 50 --seed 20261124 --max-plies 140 \
  --output /private/openings-tablut.json
.venv/bin/python spikes/browser_offline/tournament_openings.py \
  --ruleset copenhagen --pairs 50 --seed 20261124 --max-plies 160 \
  --output /private/openings-copenhagen.json
.venv/bin/python spikes/browser_offline/tournament_openings.py \
  --ruleset brandubh --pairs 50 --validate /private/openings-brandubh.json

# 6. Agent configs (same model identity for native/browser neural)
cat > /tmp/native-neural.json <<'JSON'
{"label":"native-neural","kind":"native-neural","runtime":"native-cpython","provider":"torch-cpu","algorithm":"mcts","model":{"onnx_sha256":"<64 hex>","weights_sha256":"<64 hex>","ruleset":"brandubh","size":7}}
JSON
cat > /tmp/browser-neural.json <<'JSON'
{"label":"browser-neural","kind":"browser-neural","runtime":"browser-pyodide","provider":"wasm","algorithm":"mcts","model":{"onnx_sha256":"<64 hex>","weights_sha256":"<64 hex>","ruleset":"brandubh","size":7}}
JSON
cat > /tmp/native-jarl.json <<'JSON'
{"label":"native-jarl","kind":"native-jarl","runtime":"native-cpython","provider":"cpu","algorithm":"alpha_beta","depth":5,"model":null}
JSON
# Fixed-work primary comparison
.venv/bin/python spikes/browser_offline/tournament.py --run --ruleset brandubh \
  --openings-file /private/openings-brandubh.json --simulations 256 --time-limit 2 \
  --seed 7 --max-plies 200 --mode fixed-work --agent-timeout 120 --evidence-class playing-strength \
  --agent-a-command ".venv/bin/python spikes/browser_offline/native_tournament_agent.py --engine neural --checkpoint /private/brandubh.pt --model-export /private/export-brandubh" \
  --agent-b-command "node spikes/browser_offline/browser_tournament_agent.mjs /private/spike-brandubh --provider wasm" \
  --agent-a-config /tmp/native-neural.json --agent-b-config /tmp/browser-neural.json \
  --output /tmp/tournament-neural-fixed-work.json
.venv/bin/python spikes/browser_offline/tournament.py /tmp/tournament-neural-fixed-work.json
# Same comparison under fixed time
.venv/bin/python spikes/browser_offline/tournament.py --run --ruleset brandubh \
  --openings-file /private/openings-brandubh.json --simulations 4096 --time-limit 2 \
  --seed 8 --max-plies 200 --mode fixed-time --agent-timeout 120 --evidence-class playing-strength \
  --agent-a-command ".venv/bin/python spikes/browser_offline/native_tournament_agent.py --engine neural --checkpoint /private/brandubh.pt --model-export /private/export-brandubh" \
  --agent-b-command "node spikes/browser_offline/browser_tournament_agent.mjs /private/spike-brandubh --provider wasm" \
  --agent-a-config /tmp/native-neural.json --agent-b-config /tmp/browser-neural.json \
  --output /tmp/tournament-neural-fixed-time.json
# Browser neural vs native Jarl (equal time; work units are not comparable)
.venv/bin/python spikes/browser_offline/tournament.py --run --ruleset brandubh \
  --openings-file /private/openings-brandubh.json --simulations 4096 --time-limit 2 \
  --seed 9 --max-plies 200 --mode fixed-time --agent-timeout 120 --evidence-class playing-strength \
  --agent-a-command ".venv/bin/python spikes/browser_offline/native_tournament_agent.py --engine jarl --depth 5" \
  --agent-b-command "node spikes/browser_offline/browser_tournament_agent.mjs /private/spike-brandubh --provider wasm" \
  --agent-a-config /tmp/native-jarl.json --agent-b-config /tmp/browser-neural.json \
  --output /tmp/tournament-jarl-fixed-time.json
# Browser WASM vs browser WebGPU (only when a physical adapter is available)
.venv/bin/python spikes/browser_offline/prepare_neural.py --model-dir /private/export-brandubh --output /private/spike-brandubh-webgpu
cat > /tmp/browser-webgpu.json <<'JSON'
{"label":"browser-webgpu","kind":"browser-neural","runtime":"browser-pyodide","provider":"webgpu","algorithm":"mcts","model":{"onnx_sha256":"<64 hex>","weights_sha256":"<64 hex>","ruleset":"brandubh","size":7}}
JSON
.venv/bin/python spikes/browser_offline/tournament.py --run --ruleset brandubh \
  --openings-file /private/openings-brandubh.json --simulations 128 --time-limit 2 \
  --seed 10 --max-plies 200 --mode fixed-work --agent-timeout 120 \
  --agent-a-command "node spikes/browser_offline/browser_tournament_agent.mjs /private/spike-brandubh --provider wasm" \
  --agent-b-command "node spikes/browser_offline/browser_tournament_agent.mjs /private/spike-brandubh-webgpu --provider webgpu" \
  --agent-a-config /tmp/browser-neural.json --agent-b-config /tmp/browser-webgpu.json \
  --output /tmp/tournament-wasm-webgpu.json

# 7. Sustained memory: telemetry-only progress, fresh files, ready handshake.
# No `set -e`: validator statuses are handled explicitly (0 accepted, 1 invalid,
# 2 structurally valid but partial). Cleanup is ownership-file based; the active
# workload identity is cleared only after the workload has been reaped.
set -uo pipefail
SESSION="owner-$(date +%s)"; TELEMETRY="/tmp/$SESSION.jsonl"; READY="/tmp/$SESSION.ready"
SUMMARY="/tmp/$SESSION-workload.json"; OWNERSHIP="$READY.owner.json"
COLLECTED="/tmp/$SESSION-memory-collected.json"; FINAL="/tmp/$SESSION-memory.json"
WORKLOAD_PID=""
cleanup_owner() {
  status=$?
  trap - EXIT INT TERM
  if [ -n "$WORKLOAD_PID" ]; then
    .venv/bin/python spikes/browser_offline/wait_for_ready.py --terminate-only \
      --ownership-file "$OWNERSHIP" || true
  fi
  exit "$status"
}
trap cleanup_owner EXIT INT TERM
node spikes/browser_offline/sustained_workload.mjs /private/spike-brandubh \
  --duration 690 --mode repeated --session "$SESSION" --provider wasm \
  --telemetry "$TELEMETRY" --ready "$READY" --overwrite \
  --iterations 128 --time-limit 2 --cache --output "$SUMMARY" &
WORKLOAD_PID=$!
# Readiness failure exits 3 and terminates the owned workload; the trap above
# preserves the failure status and useful cleanup diagnostics.
.venv/bin/python spikes/browser_offline/wait_for_ready.py --ready "$READY" \
  --pid "$WORKLOAD_PID" --ownership-file "$OWNERSHIP" --timeout 180 \
  --session "$SESSION" --model-sha256 "<64 hex>" --mode repeated
.venv/bin/python spikes/browser_offline/memory_profile_collect.py --pid "$WORKLOAD_PID" \
  --profile sustained-10m --warmup-duration 60 --duration 600 --interval 1 \
  --telemetry-file "$TELEMETRY" --telemetry-session-id "$SESSION" \
  --telemetry-model-sha256 "<64 hex>" --telemetry-workload sustained-analysis \
  --inference-cache --final-summary-timeout 120 --output "$COLLECTED"
WORKLOAD_STATUS=0
if wait "$WORKLOAD_PID"; then WORKLOAD_STATUS=0; else WORKLOAD_STATUS=$?; fi
WORKLOAD_PID=""
# The collector is not the parent. Persist the actual wait status and reconcile the
# final summary before validation; the original ownership file is reused for cleanup.
.venv/bin/python spikes/browser_offline/finalize_workload_evidence.py \
  --profile "$COLLECTED" --workload-summary "$SUMMARY" \
  --ownership-file "$OWNERSHIP" --exit-status "$WORKLOAD_STATUS" --output "$FINAL"
test "$WORKLOAD_STATUS" -eq 0
# Validate the session-specific finalized report, never an earlier collected or
# historical file. Exit 2 is structurally valid partial evidence, not acceptance.
.venv/bin/python spikes/browser_offline/memory_validation.py "$FINAL"
VALIDATOR_STATUS=$?
case "$VALIDATOR_STATUS" in
  0) echo "accepted: $FINAL" ;;
  2) echo "partial (not accepted): $FINAL" ;;
  *) echo "invalid evidence: $FINAL"; exit 1 ;;
esac
# 30 minutes: --duration 1890 on the driver, --profile sustained-30m --duration 1800 collector.
# Lifecycle profile mapping (driver mode -> collector --profile):
#   repeated            -> sustained-10m / sustained-30m
#   cancel-restart      -> cancel-restart
#   model-reload        -> model-reload
#   model-replacement   -> model-replacement (authorized A<->B transitions)
#   worker-replacement  -> worker-replacement
#   client-disposal     -> client-disposal
#   cache-churn         -> cache-churn (use --cache-entries 4 --cache-bytes 65536)
#   batching            -> independent-batching
# Model replacement additionally needs two validated staged model files and an
# explicit approved model set (original + every alternate):
#   --mode model-replacement --replacement-model-file models/alt.onnx \
#     --replacement-model-sha256 "<alt digest>" [--replacement-model-file-2 ...]
#   collector: --approved-model-sha256 "<original digest>" \
#              --approved-model-sha256 "<alt digest>" ...
#   readiness:  --ownership-file "$READY.owner.json" (reuse for terminate-only)
#   completion: finalize_workload_evidence.py with the summary and actual wait status
# Copy alternate validated ONNX artifacts into the staged root under models/ and
# use --cache for transitions/cache-churn.
#
# Bounded lifecycle sweep. Use the script for every sweep; it owns the loop,
# validator exit-status policy, ownership-file cleanup, process-tracking reset and
# per-case finalized report path. It never labels partial evidence as accepted:
#
#   exit 0  every case accepted
#   exit 1  structurally invalid evidence or unexpected execution failure
#   exit 2  at least one structurally valid partial result (not acceptance)
#   exit 3  readiness/ownership verification failure (cleanup already attempted)
#   exit 4  collection failure (cleanup already attempted)
#   exit 5  evidence finalization failure
STAGE_DIR=/private/spike-brandubh MODEL_SHA256="<64 hex>" \
  bash scripts/browser_analysis_lifecycle_sweep.sh
#
# Bounded tests for this workflow (stub commands, no browser, no sustained run):
#   .venv/bin/python -m pytest tests/test_browser_analysis_lifecycle_sweep.py -q

# 8. Physical device matrix
.venv/bin/python spikes/browser_offline/device_evidence.py /private/device-evidence.json \
  --output /private/device-validation.json

# 9. Evidence validation summary
.venv/bin/python spikes/browser_offline/competitive_validation.py --validate /tmp/competitive-validation.json
# Validate the finalized session-specific reports produced above (never a
# historical /tmp/memory-10m.json path).
.venv/bin/python spikes/browser_offline/memory_validation.py "$FINAL" --output "${FINAL%.json}-validation.json"
```

Structural smoke mode remains available through the existing quick benchmark/parity
commands and small fixture-based tournaments; it is not full acceptance. The
tournament CLI rejects playing-strength runs with fewer than ten opening pairs
before launching, and generated corpora record generator/seed/corpus identity.

## I.1 Local verification

- Focused Python regression across the Phase 3C-B suites, competitive/device
  validators, malformed-input suites, readiness/manifest helpers and existing MCTS
  validation/benchmarks: **304 passed, 3 browser opt-in gates skipped** in the focused C1–D4 regression.
- Node lifecycle regressions: 67/67, 7/7, 30/30, 21/21, 8/8.
- Optional actual-browser sustained-driver runs passed repeated, cancel/restart,
  reload, worker replacement, client disposal, cache churn, batching and A↔B↔A
  model replacement with alternate validated models; telemetry-only collection,
  bounded readiness and clean shutdown were exercised.
- `make lint`, `make typecheck` and `make docs` pass.
- Frontend Vitest: **1,576 passed** (108 files); benchmark budgets and production
  build pass.
- Local `make check` backend run: **4,409 passed, 68 skipped, 6 failed** only in
  `tests/test_mcts_optimization.py` frozen-reference digests under this host's
  Python 3.11.18. No MCTS file is modified by this change; CI uses Python 3.12.
  The focused C1–D4 regression is **304 passed, 3 browser gates skipped**, and the
  optional actual-browser sustained suite passes.

## J. Readiness recommendation

| Area | Status |
| --- | --- |
| Phase 3C-B software (V1, native neural adapter, sustained driver, runbook) | Implemented and structurally validated |
| Competitive model acceptance | Blocked: no competitive checkpoints |
| Playing-strength acceptance | Blocked: synthetic model, insufficient pairs |
| Sustained-memory acceptance | Blocked/partial: WASM metric unavailable; 30-minute JS-heap threshold not met |
| Physical-device acceptance | Blocked: no physical hardware |
| Verified GPU execution | Blocked: no adapter and no supported placement diagnostic |
| Copenhagen depth-5 | Blocked for Copenhagen; recommendation documented |
| Phase 4 production integration | Not started; no model delivery or production UI work in this phase |

Phase 3 remains **not fully validated** while these external gates are open. This
PR is the evidence-driven conclusion of the feasible Phase 3C-B work in this
environment, not a claim of competitive or hardware acceptance.
