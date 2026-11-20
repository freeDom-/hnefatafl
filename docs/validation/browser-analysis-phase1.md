# Browser analysis Phase 1 validation (revision 4)

Validation record for Phase 1A (full-strength Jarl) and Phase 1B (trained-model
ONNX export and browser neural inference) after integrating current `main`
(`17d3819`, merged as `3fa29c2`) and applying the final focused corrections.

## Integration and source identity

- PR head before this round: `274e983`. Current main at review: `17d38192` (39
  commits ahead). Integrated with a normal merge as `3fa29c2`.
- Upstream changes were frontend feature splitting, analysis components, bundle
  budgets and documentation. No engine, model, inference, dependency or frontend
  runtime source used by the spike changed, so the revision-3 engine/runtime
  identities still apply.
- The staged engine closure and production-equivalent Jarl preset were
  revalidated against the merged sources. Revision-1/2/3 measurement artifacts are
  preserved unchanged; the depth-5 and fixed-time figures below are revision-3
  measurements **re-evaluated** with the revision-4 comparator, not rerun, and are
  identified as such.

Historical evidence: `*-2026-11-19.json` (r1), `*-r2.json` (r2), `*-r3.json` (r3).
They validate older contracts and must not be read as revision-4 acceptance.

Machine-readable revision-4 evidence:

- [native fixed-work quick](jarl-native-fixed-work-quick-r4.json) ·
  [browser fixed-work quick](jarl-browser-fixed-work-quick-r4.json)
- [r3 depth-5 Brandubh re-evaluated](jarl-acceptance-depth5-brandubh-r4.json) ·
  [r3 depth-5 Tablut+Copenhagen re-evaluated](jarl-acceptance-depth5-tablut-copenhagen-r4.json) ·
  [r3 fixed-time standard re-evaluated](jarl-acceptance-fixed-time-standard-r4.json)
- [ONNX export manifest](neural-export-manifest-r4.json) ·
  [native ONNX parity](neural-native-onnx-parity-r4.json)
- [browser WASM parity](neural-browser-parity-wasm-r4.json) ·
  [16 corruption kinds rejected](neural-browser-corruption-r4.json)
- [browser WASM benchmark](neural-browser-wasm-benchmark-r4.json) ·
  [actual-browser UI checks](neural-browser-ui-r4.json)
- [WebGPU parity blocked](neural-browser-parity-webgpu-blocked-r4.json) ·
  [WebGPU benchmark blocked](neural-browser-benchmark-webgpu-blocked-r4.json) ·
  [GPU-execution evidence blocked](neural-browser-gpu-execution-blocked-r4.json)

## Environment

Linux x86_64 LXC, 4 CPUs; CPython 3.11.18; torch 2.14.1+cpu; onnx 1.23.2 /
onnxruntime 1.32.0; Node 22.23.4; Chromium 156.0.8078.4 headless; Pyodide 0.27.7;
ORT Web 1.22.0. No physical GPU adapter, physical phone or competitive checkpoint
was available.

## Phase 1A — corrected acceptance semantics

Revision-4 corrections in `jarl_compare.py`:

- **Deterministic parity is independent of work counters.** Two valid
  non-terminal results with the same completed depth are always compared for best
  move and defender value, even when node/evaluation counters differ.
- **Computational-work equivalence only governs performance ratios.** Equal-work
  latency requires equal depth, the same interruption state and matching
  measured node/evaluation counters; unavailable counters are not treated as
  equivalent.
- **Acceptance requires no mandatory failure.** A parity mismatch, missing
  duplicate/invalid record, unexpected execution error or invalid case prevents
  `acceptance_complete`, and invalid parity cases never contribute acceptance
  counts.
- **Expected depth and budget come from the validated effective configuration.**
  A record that redefines `expected_depth`, reports a different completed depth,
  or reports an inconsistent `time_limit_ms` fails validation. Malformed work
  counters are rejected.
- Deadline-limited, unsupported and terminal cases stay distinct; unresolved
  completion remains listed; fixed-time differences from different completed
  depths stay informational; terminal/unsupported/failed cases are excluded from
  search-overhead statistics.

### Fresh revision-4 run (merged sources)

| Comparison | Expected | Compared | Valid | Parity | Acceptance | Equal-work browser ÷ native |
| --- | ---: | ---: | ---: | ---: | --- | ---: |
| quick (depth 3), all rulesets, reps=2 | 42 | 42 | 42 | 36/36 | complete | 2.13× |

Startup 2,825 ms; each case independently bounded; no failures or warnings.

### Revision-3 measurements re-evaluated under the revision-4 comparator

No long searches were repeated because the engine source identity is unchanged.

| Workload (r3 measurements) | Expected | Compared | Parity | Acceptance | Unresolved |
| --- | ---: | ---: | ---: | --- | ---: |
| depth 5, Brandubh, reps=2 | 14 | 14 | 12/12 | complete | 0 |
| depth 5, Tablut+Copenhagen, reps=1 | 14 | 14 | 12/12 | **incomplete** | 3 |
| fixed-time standard, all rulesets, reps=2 | 42 | 42 | 16/16 | evidence complete | 24 (different work) |

The three unresolved cases remain Copenhagen `opening`, `midgame` and
`repetition`: native CPython and browser Pyodide both reach depth 4 inside the
60 s fixed-work safety limit; parity matches at that equal depth and completion is
honestly reported as unresolved. The 60 s safety limit was not increased. The
fixed-time run reports 20 informational warnings and never claims a speedup.

## Phase 1B — response contract, tensor ownership and provider evidence

### Complete inference-response contract

The comparator now requires the actual response fields: model identity, model
ruleset, request/position identity, policy/value data, shapes, dtypes, legal
indices and order, legal-only probabilities, and batch count/result count/index.
Missing mandatory fields fail; the harness does not substitute expected values.
Harness-generated terminal skips are marked `harness_generated`. Native ONNX
outputs are normalized to the same schema explicitly.

Batch order is verified independently: `batch_count == results_count ==
outputs.length`, each returned `index` equals its array position, request
identities are unique, and each result's request/position identity matches the
requested position order. Sixteen deliberate corruption kinds fail
independently, including batch count, missing/extra result, duplicate/missing/
wrong index, reorder and wrong position association.

### Artifact trust chain

The export manifest and `VALIDATED.json` now cover the complete accepted artifact
set: model digest/size, reference-vector digest, metadata-sidecar digest and
parity-report digest. Staging verifies the actual graph, sidecar, reference and
parity digests; an unvalidated replacement cannot inherit a marker, and an
unvalidated artifact must not contain a stale parity report.

| Metric | Value |
| --- | --- |
| Checkpoint | trained Brandubh **smoke** model (channels 4, blocks 0) |
| Model SHA-256 | `959578236ead3fa9…` |
| ONNX graph / gzip estimate | 46,924 B / 6,926 B |
| Reference cases | 39 (38 non-terminal incl. 32 distinct generated, 1 terminal) |
| Native PyTorch → ORT CPU | worst logit 7.15e-7, value 2.98e-8, postprocess 0 |
| Native PyTorch → browser WASM | worst logit 1.67e-6, value 2.98e-8, postprocess 0 |

Distinct-position batched inference at 1, 2, 4, 8, 16 and 32 passed.

### Output-tensor ownership

Every output tensor is disposed in a `finally` path, including invalid shapes,
non-finite values, extraction and normalisation failures. A disposal failure is
reported as `RESOURCE_CLEANUP_FAILED` and resets the worker rather than silently
reusing a potentially unhealthy one. Seven deterministic Node checks use
instrumented tensor objects.

### Browser inference performance (smoke model, WASM)

| Metric | Value |
| --- | ---: |
| ORT + session initialization | 508.6 ms |
| First inference (cold) | 18.6 ms |
| Warm run median | 0.6 ms |
| Sustained in-worker throughput (200 × batch 8) | 4,469.3 positions/s |

| Batch | Median | p90 | Positions/s |
| ---: | ---: | ---: | ---: |
| 1 | 0.8 ms | 1.12 ms | 1,250 |
| 2 | 1.3 ms | 2.20 ms | 1,538 |
| 4 | 1.8 ms | 3.32 ms | 2,222 |
| 8 | 2.7 ms | 3.14 ms | 2,963 |
| 16 | 4.0 ms | 4.60 ms | 4,000 |
| 32 | 6.7 ms | 8.60 ms | 4,776 |

Heap fields are `null`: headless Chromium does not expose `performance.memory`
and ORT Web does not expose its WASM heap. No memory-stability claim is made.

### Actual-browser UI checks

Six interactions pass from a clean initial state: Infer, distinct batch,
Benchmark, controls disabled while active, cancellation invalidating stale UI, and
provider change failure followed by WASM recovery. The nested-load admission bug
is fixed with an internal loader that runs inside the owned operation.

### Provider evidence semantics

Session-level provider lists are not treated as proof of per-operator GPU
execution. Placement is `wasm`, `webgpu-session-unverified`, `webgpu-mixed`,
`cpu-fallback` or `placement-unknown`; `gpu_execution_verified` is false because
the pinned ORT Web version exposes no supported per-operator placement diagnostic.

- `--require-webgpu` requires an obtained adapter, a WebGPU-requested session and
  successful numerical inference. It does not claim GPU-only execution.
- `--require-gpu-execution` requires independently verified actual GPU execution
  and currently fails explicitly.

On this host both WebGPU gates fail with
`WebGPU requested but no adapter is available` and exit non-zero.

## Repository checks

- `tests/test_browser_jarl_benchmark.py`: 35 passed.
- `tests/test_browser_neural_parity.py`: 47 passed.
- `tests/test_browser_neural_export.py`: 29 passed (optional ONNX deps).
- `tests/test_browser_client_lifecycle.py`: 2 passed (25 Node checks).
- `tests/test_browser_tensor_cleanup.py`: 1 passed (7 Node checks).
- `tests/test_browser_neural_ui.py`: opt-in actual-browser UI gate (6 checks
  passed when `SPIKE_NEURAL_DIR` was set for this run).
- `tests/test_offline_spike.py`: 17 passed.
- `verify.mjs` Phase 0 gate, `verify_jarl.mjs`, `neural_parity.mjs`,
  `neural_benchmark.mjs` and `test_neural_ui.mjs` all run manually.
- `ruff check`/`format --check`, `mypy hnefatafl` and `scripts/check_docs.py` pass.

## Remaining blockers and readiness

- Competitive trained weights (32×2+) for all three rulesets: blocked.
- Physical WebGPU execution: blocked (no adapter); per-operator placement is not
  exposed by the pinned runtime even on hardware.
- Physical desktop/mobile performance and process memory: blocked.
- Three Copenhagen depth-5 cases remain unresolved within the 60 s budget.
- Playing strength: Phase 3, not started.

Software inference contracts are now reliable enough that Phase 2 (browser neural
MCTS) can proceed using validated WASM inference, in parallel with competitive
model and physical-GPU validation. This PR does not implement Phase 2.
