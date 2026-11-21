# Browser-side neural MCTS — Phase 3 validation

Canonical evidence for the Phase 3 optimization and strength-evaluation work.
Phase 2 correctness evidence in
[browser-analysis-phase2.md](browser-analysis-phase2.md) remains the reference
and is not replaced by these measurements.

**Status:** Phase **3A** delivered and software-validated. Phase **3B**
(caching, independent-search batching, single-tree batching, subtree reuse) and
**3C** (competitive checkpoints, playing-strength tournaments, physical
hardware) have **not** been implemented in this revision. Phase 3 is therefore
in progress, not complete.

Related: [roadmap](../designs/browser-analysis-roadmap.md) ·
[Phase 2 validation](browser-analysis-phase2.md) ·
[Phase 1 validation](browser-analysis-phase1.md) ·
[isolated spike](../../spikes/browser_offline/README.md).

## 1. Remaining Phase 2 findings closed

### 1.1 Independent workload identity

The comparator no longer trusts the digest stored in either report. It
reconstructs the canonical identity with the existing
`mcts_vectors.workload_identity()` implementation from:

- comparison level;
- deterministic evaluator identity for Level A;
- validated effective search settings (numeric JSON types normalised);
- exact ordered workload positions with complete serialized state and repetition
  history;
- the validated ONNX model digest for Level B.

Both reports must match the independently recomputed digest and each other.
Identical corruption in both reports fails, as do modified settings, position
state, repetition history, model digest, level or evaluator. Level B remains
comparable across native PyTorch, native ONNX and browser ONNX when the same
validated export is used. The comparison schema is now 4 and records
`workload.recomputed_identity`, `browser_recomputed_identity` and
`identity_verified`.

### 1.2 Search diagnostic counters

All accumulated search counters must be non-negative integers:

- requested and completed simulations;
- neural requests and prediction reuses;
- immediate-win shortcuts and root children;
- inference-cache hits and trace length;
- `analysis.simulations`;
- root visit counts (already validated).

Zero remains valid for terminal and immediate-win cases. The comparator does not
assume one neural request per completed simulation; prediction reuse and
shortcuts make that relationship false. Negative counters are rejected in
Level A and Level B comparisons, including when both reports contain the same
corruption.

### 1.3 Test-only inference delay isolation

The delay capability is now absent from the normal worker rather than gated by a
client property or an activatable protocol action:

- `neural-worker.js` has no delay implementation, configuration action or
  generation flag. It rejects `test_delay_ms` in ordinary requests and rejects
  `configureTestHooks`, `inferTestonly`, `inferCompactTestonly` and the generic
  `test` action with `TEST_HOOKS_DISABLED`.
- `neural-worker-test.js` is a separate worker entry point. It imports the normal
  worker core and installs an in-worker test adapter that owns delay configuration
  and the bounded delay. `prepare_neural.py` does not stage this file.
- Browser tests serve that file explicitly from the source tree and select it via
  an injected test-worker URL; the normal page constructs the normal worker and
  cannot obtain the file from a staged deployment.
- A normal `NeuralClient` exposes no delay setter. A test client requires both the
  explicit adapter and the dedicated worker URL; requests otherwise fail with
  `TEST_HOOKS_DISABLED`.
- The test adapter is configured once per worker generation. Cancellation, drain,
  healthy-session reuse, unresponsive replacement and generation ownership are
  exercised through the dedicated worker.
- Tests directly submit every test action through a normal client and verify
  rejection, then verify ordinary inference still completes.

This is a build/test-time separation, not a runtime authorization token. Provider
and resource limits are unchanged.

## 2. Baseline instrumentation

The existing benchmark matrix was extended without a new format:

- position kinds now include repetition-sensitive positions alongside opening,
  midgame, endgame and tactical;
- fixed-simulation budgets cover 128–4096 and fixed-time budgets 0.25–10 s;
- `--repetitions` and `--warmup` expose controlled repeated measurement;
- `--payload-mode compact|full` supports a same-workload A/B comparison.

New coordinator metrics (nested and non-overlapping components are labelled):

| Field | Meaning |
| --- | --- |
| `bridge_overhead_ms` | Step time outside in-Python action time: JSON serialization, worker dispatch and Pyodide invocation |
| `serialize_ms` / `parse_ms` | Coordinator JSON encoding/decoding (inside bridge overhead) |
| `request_bytes` / `response_bytes` | Search-worker message sizes |
| `neural_wait_ms` | Whole inference round trip including dispatch and cancellation race |
| `tensor_prepare_ms` / `inference_execution_ms` | ORT input preparation and session run |
| `policy_extract_ms` | Output validation, policy extraction and legal normalisation |
| `python_action_ms` | In-Python search/action time |
| `unattributed_ms` | End-to-end time not covered by step or neural wait |

Reports also retain model SHA-256, engine identity, encoder/driver version,
ruleset, exact workload identity, settings, provider request/placement, completed
work, deadline outcome, browser version and available memory figures.

## 3. Delivered optimization: legal-only inference payload

MCTS consumes only the legal-action probabilities and defender-relative value.
It never needs the full policy row. The Phase 2 path nevertheless transferred
every policy logit from the neural worker through structured clone to the main
thread, then discarded it.

Delivered path:

1. The search worker's encoded planes stay base64 until the inference worker
   decodes them directly into a `Float32Array` (no intermediate JS number array).
2. The neural worker computes the legal softmax directly from the output tensor
   and returns only legal indices/probabilities, value and request association.
3. Full logits, illegal mass and raw inspection remain available through the
   original `infer`, parity and benchmark paths.
4. The coordinator records the payload mode and the number of transferred policy
   values. `compactInference = false` preserves the full-logit path as a
   controlled A/B reference.
5. The Pyodide bridge no longer sends a duplicate serialized board/history with
   each evaluation request; the native evaluator path still receives it.

Numerical behaviour is unchanged: the compact legal softmax uses the same tensor
values, action order, max-subtraction and normalisation. Raw-versus-compact
tensor extraction is tested for exact legal-probability equality. MCTS search
semantics, seeded traversal, values, legal ordering and model identity are
unchanged.

### 3.1 End-to-end search path parity

`test_search_path_parity.mjs` collects raw observations only; the shared Python
validator (`path_parity.py`, using the real engine for move legality) owns
acceptance. Every case has an independent failure list and `case.ok`; `report.ok`
requires coverage, every case, the collector's `raw_ok`, and well-formed
`harness_failures` to pass. Collector failures are preserved separately from
per-case validation failures, successful cases are never discarded when an
independent browser error occurs, and the CLI exits nonzero on any failure.
Malformed nested full/compact/search/analysis/coordinator/model/provider/settings
fields produce structured failed cases instead of exceptions.

The runner executes the same real coordinator and neural worker twice per
position with only `coordinator.compactInference` changed. It requires
the same best move, defender-relative value (within 1e-12), completed simulations,
root visit distribution, neural request count, prediction reuse count, completion
reason, terminal/immediate-win outcome, model identity and position identity; the
entire result payload must be identical after removing timing/transport fields.
Fixed-time runs verify valid completed work, deadline consistency, legal moves,
identity and payload mode without requiring equal trees.

Coverage is derived from the declared workload: a complete three-model run must
cover all supported rulesets; a single-model run requires that model's
opening/tactical/repetition/terminal categories and Brandubh immediate win.
Unexpectedly empty or reduced workloads fail with synthetic failed cases. Negative
regression tests cover illegal/missing moves, altered model/position identity,
altered visits, wrong completed work, invalid fixed-time accounting, wrong
deadline reporting, malformed nested reports, collector failures, missing
expected positions/categories and a single failing case among passing cases.

Fixed-time accounting is checked per runtime: integer completed work within
`0..requested`, `analysis.simulations` agreement, root-visit consistency,
completion-reason/deadline metadata, terminal/immediate exceptions, and timing
presence. Full and compact may complete different valid amounts of work.

### 3.2 Serialized-state allocation

`NeuralSearch._evaluation()` no longer stores `state.to_dict()` in its pending
request context. The native evaluator path still materializes the serialized
position in the event when `include_position=True`; the browser path
(`include_position=False`) materializes nothing. Exact position/neural-input
digests, repetition history, legal ordering and request/resume association are
unchanged.

A same-commit quick benchmark with and without the retained allocation measured
622–663 sims/s. The difference is below run-to-run noise; no speedup is claimed
for this cleanup.

## 4. Measured result

Environment: this repository's Proxmox LTS container, Chromium 156.0.8078.4,
Pyodide 0.27.7, ONNX Runtime Web 1.22.0 WASM, CPU execution, tiny structural
smoke checkpoint. All A/B runs use the same commit, staged assets, workload,
model and process; only `--payload-mode` differs. Medians over three measured
repetitions (two for 512/1024 fixed work). These are smoke-model numbers, not
competitive-model or physical-device claims.

| Workload | Full logits (sims/s) | Legal-only (sims/s) | Change |
| --- | ---: | ---: | ---: |
| Brandubh 128/256 + 0.5/1 s | 533.5 | 707.2 | +32.6 % |
| Brandubh 512/1024 + 2/5 s | 501.5 | 665.5 | +32.7 % |
| Tablut 128 + 0.5 s | 365.4 | 446.9 | +22.3 % |
| Copenhagen 128 + 0.5 s | 220.9 | 412.9 | +86.9 % |
| Brandubh 32×2 synthetic, 128/256 + 1 s | not run | 459.9 | architecture scaling only |

Fixed-work details (Brandubh, smoke model):

- 1,024 simulations complete in **1.50–1.66 s** with legal-only inference, versus
  1.76–2.32 s with full logits.
- Per 1,024-simulation case the old path transferred about **2.44 million policy
  values**; the optimized path transfers zero.
- Median full-logit policy extraction cost was ~125 ms per strong case; the
  legal-only extraction cost was ~10 ms.
- Median timing shares at 512/1024 legal-only: 39.9 % step boundary,
  28.5 % neural wait, 31.3 % Python search, 0.4 % unattributed. Absolute
  boundary time per simulation is roughly 0.48 ms, down from the Phase 2
  measurement; it remains the largest single component and motivates a direct
  worker channel in a later subphase.
- The 32×2 synthetic network is not a trained competitive model; it only shows
  that the pipeline scales to a representative architecture.

The Phase 2 full-logit baseline (r4) was 556.6 sims/s for Brandubh 512/1024; the
controlled full-logit run at this revision is 501.5, while legal-only is 665.5.
Only the controlled within-revision comparison is used as optimization evidence.

## 5. Correctness and regression evidence

| Gate | Result |
| --- | --- |
| Level A deterministic parity (21 positions, all rulesets) | 21/21 exact; workload identity independently verified |
| Custom workloads (3 positions incl. repetition history) | 3/3 exact; file digest verified |
| Level B Brandubh 256, 7 positions | 7/7 best moves; validated ONNX digest |
| Native PyTorch vs browser WASM | 7/7 best moves |
| Workload identity mutations (native/browser/both digest, settings, state, history, level, evaluator, model) | rejected |
| Negative counter tests (Level A and B) | rejected; terminal zeros accepted |
| Comparator CLI failure handling | unchanged structured nonzero reports |
| Search coordinator lifecycle | 30/30 |
| Neural client lifecycle (incl. hook isolation and direct test-action rejection) | 42/42 |
| Staged-source integrity | 21/21 |
| Analysis UI (fresh page, real pending inference, compact payload display) | 30/30 |
| Neural inference UI incl. normal-page hook rejection and malformed compact input | 21/21 |
| Inference parity (single + batches) | 39 cases + batches, no failures |
| Identity-boundary corruptions | 4/4 detected |
| Tensor compact/full extraction | exact legal-probability parity |
| Full-logit vs legal-only search paths (fixed work) | 13/13 exact across Brandubh/Tablut/Copenhagen + 5/5 32×2 |
| Full-logit vs legal-only search paths (fixed time) | 13/13 valid deadline/legal/identity cases |
| Search-path per-case negative validation | 57 synthetic tests: illegal/missing move, identity, visits, work, deadline, malformed nesting, collector failures, missing case/category, single failure |
| Actual-browser collector error after cases | nonzero exit; all cases pass but `collector_ok=false` and report rejected |
| Normal-page direct test actions | 5/5 rejected; ordinary inference still completes |
| Dedicated test worker | delayed inference, healthy drain, unresponsive replacement and generation ownership pass |

Frozen native MCTS reference tests still fail identically on pristine `main` in
this environment; they are not weakened or skipped. The full Python suite runs
in CI on a compatible environment.

## 6. Phase 3 subphase status

| Subphase | Status | Evidence / remaining work |
| --- | --- | --- |
| 3.0 remaining Phase 2 findings | Completed | identity, counters, test-hook isolation |
| 3.1 controlled baselines/profiling | Completed for smoke/representative architectures | extended matrix, new timing fields |
| 3.2 coordinator/Pyodide boundary | Partially addressed | duplicate position/pending serialization removed; direct worker channel not implemented; boundary remains ~40 % |
| 3.3 inference payloads | Completed for MCTS | legal-only transport, base64 planes, full-logit reference retained; end-to-end path parity gate |
| 3.4 inference caching | Not started | must measure benefit against existing prediction reuse |
| 3.5 independent-search batching | Not started | ORT batch path exists; scheduler not implemented |
| 3.6 single-tree batched MCTS | Not started | sequential reference retained |
| 3.7 subtree reuse | Not started | no engine refactor attempted |
| 3.8 competitive-model validation | Blocked externally | owner checkpoints unavailable; 32×2 synthetic only |
| 3.9 playing-strength tournaments | Not started | framework/evidence outstanding |
| 3.10 physical GPU/browser | Blocked externally | no WebGPU adapter/physical mobile |
| 3.11 sustained resource stability | Not started | quick lifecycle only |
| 3.12 experimental UI | Partially updated | payload mode/statistics only |

## 7. Reproduction

```sh
# Stage (owner supplies any private checkpoint path for real models)
.venv/bin/python spikes/browser_offline/smoke_checkpoint.py \
  --ruleset brandubh --channels 4 --blocks 0 --output work/phase3/smoke.pt
.venv/bin/python spikes/browser_offline/onnx_export.py \
  work/phase3/smoke.pt work/phase3/export
.venv/bin/python spikes/browser_offline/prepare_neural.py \
  --model-dir work/phase3/export --output work/phase3/spike --wasm-only

# Controlled payload A/B
node spikes/browser_offline/mcts_benchmark.mjs work/phase3/spike \
  --provider wasm --simulations 512,1024 --time-budgets 2,5 \
  --repetitions 2 --payload-mode full --output work/phase3/full.json
node spikes/browser_offline/mcts_benchmark.mjs work/phase3/spike \
  --provider wasm --simulations 512,1024 --time-budgets 2,5 \
  --repetitions 2 --payload-mode compact --output work/phase3/compact.json

# Parity and identity
.venv/bin/python spikes/browser_offline/native_mcts_parity.py \
  --level a --iterations 128 --output work/phase3/native-a.json
node spikes/browser_offline/mcts_parity.mjs work/phase3/spike \
  --provider wasm --native work/phase3/native-a.json --output work/phase3/browser-a.json
.venv/bin/python spikes/browser_offline/mcts_compare.py \
  work/phase3/native-a.json work/phase3/browser-a.json

# End-to-end full/compact search parity (one or more staged models)
SPIKE_NEURAL_DIR=work/phase3/spike \
SPIKE_NEURAL_DIR_TABLUT=work/phase3/spike-tablut \
SPIKE_NEURAL_DIR_COPENHAGEN=work/phase3/spike-copenhagen \
  .venv/bin/python -m pytest tests/test_browser_search_path_parity.py -q

# Browser/UI/lifecycle
node spikes/browser_offline/test_mcts_ui.mjs work/phase3/spike
node spikes/browser_offline/test_neural_ui.mjs work/phase3/spike
node spikes/browser_offline/test_search_lifecycle.mjs
node spikes/browser_offline/test_browser_lifecycle.mjs
node spikes/browser_offline/test_neural_tensors.mjs
.venv/bin/python -m pytest tests/test_mcts_benchmark.py tests/test_mcts_workload.py -q
```

## 8. Evidence files

- [quick compact](mcts-benchmark-quick-compact-phase3a.json),
  [quick full](mcts-benchmark-quick-full-phase3a.json),
  [comparison](mcts-benchmark-quick-compare-phase3a.json)
- [strong compact](mcts-benchmark-strong-compact-phase3a.json),
  [strong full](mcts-benchmark-strong-full-phase3a.json),
  [comparison](mcts-benchmark-strong-compare-phase3a.json)
- Tablut [compact](mcts-benchmark-tablut-compact-phase3a.json) /
  [full](mcts-benchmark-tablut-full-phase3a.json); Copenhagen
  [compact](mcts-benchmark-copenhagen-compact-phase3a.json) /
  [full](mcts-benchmark-copenhagen-full-phase3a.json)
- [32×2 synthetic](mcts-benchmark-brandubh-32x2-phase3a.json)
- Search-path parity (acceptance hardening): [fixed work, three rulesets](mcts-search-path-parity-fixed-acceptance-phase3a.json),
  [32×2](mcts-search-path-parity-32x2-acceptance-phase3a.json), fixed-time
  [Brandubh](mcts-search-path-parity-time-brandubh-acceptance-phase3a.json),
  [Tablut](mcts-search-path-parity-time-tablut-acceptance-phase3a.json),
  [Copenhagen](mcts-search-path-parity-time-copenhagen-acceptance-phase3a.json);
  [rejected collector error](mcts-search-path-parity-collector-error-acceptance-phase3a.json)
- Serialized-state cleanup: [before](mcts-benchmark-state-allocation-before-phase3a.json) /
  [after](mcts-benchmark-state-allocation-after-phase3a.json) (difference below noise)
- Parity: [native A](mcts-parity-native-a-phase3a.json) /
  [browser A](mcts-parity-browser-a-phase3a.json); [native B](mcts-parity-native-b-phase3a.json) /
  [browser B](mcts-parity-browser-b-phase3a.json); [native PyTorch](mcts-parity-native-b-torch-phase3a.json) /
  [comparison](mcts-parity-compare-torch-browser-phase3a.json); custom
  [native](mcts-parity-native-custom-phase3a.json) /
  [browser](mcts-parity-browser-custom-phase3a.json) /
  [positions](mcts-custom-positions-phase3a.json)
- UI/lifecycle: [analysis UI](mcts-browser-ui-phase3a.json) ·
  [neural UI](neural-browser-ui-phase3a.json) ·
  [inference parity](neural-browser-parity-wasm-phase3a.json) ·
  [search lifecycle](mcts-search-lifecycle-phase3a.txt) ·
  [neural lifecycle](neural-client-lifecycle-phase3a.txt) ·
  [staged integrity](search-assets-integrity-phase3a.txt)

Phase 0/1/2 evidence is unchanged.
