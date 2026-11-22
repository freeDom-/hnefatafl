# Browser-side neural MCTS — Phase 3B validation

Phase 3B optimization work on merged Phase 3A (`main` `64ec43e`). Phase 3A
correctness evidence remains authoritative and is not overwritten.

**Status:** Phase **3B.0** and the **3B.2 inference cache** are software
validated. **3B.1 direct worker communication** is implemented as an opt-in
experimental transport; the existing relay remains the default because measured
gain was modest/variable. **3B.3 independent-search batching** is implemented and
actual-browser validated, but retained opt-in because the smoke/CPU benchmark did
not show an end-to-end gain. **3B.4 single-tree batching** and **3B.5 subtree
reuse** are deferred after feasibility analysis. Phase 3B is **in progress**, not
complete. Phase 3C and all external hardware/competitive-model gates remain
outstanding.

## 1. Phase 3B.0 — remaining Phase 3A acceptance findings

### Authoritative best moves

`path_parity.py` now validates the actual nested
`full.analysis.best_move` / `compact.analysis.best_move` through the shared Python
engine. If the mirrored top-level fields are present they must equal the nested
result. Independently corrupted full, compact, mirrored or identically corrupted
moves fail. Terminal and immediate-win behavior is preserved.

### Coverage identifier safety

Model rulesets, expected rulesets, expected/actual position names, case rulesets,
declared model metadata and case definitions are type-checked before any set or
dictionary operation. Arrays, objects, nulls and invalid scalars produce
structured failed cases/reports and nonzero CLI exits rather than exceptions.

Regression tests in `tests/test_path_parity_validation.py` now cover 68 cases,
including the new authoritative-move and malformed-coverage-identifier mutations.

## 2. Phase 3B.1 — direct worker communication

Implemented an optional `MessageChannel` path:

1. The coordinator creates a channel and attaches one port to the Pyodide search
   worker and the other to the inference worker.
2. The search worker drives the existing Python `dispatch(start/resume)` loop,
   sends each encoded inference request directly to the inference worker, waits
   without blocking the event loop, validates request/model/position/legal
   association, then resumes Python.
3. The coordinator retains admission, ownership, watchdog, progress, cancellation,
   final-event validation and result publication. The main thread no longer
   relays every leaf payload.
4. The inference worker still performs the same compact validation, model
   identity and tensor cleanup. The direct port is bound per search/channel and
   dropped on cancellation/replacement; late replies cannot resume another
   context.

The existing coordinator relay remains the default. `directInference`/the
experimental UI checkbox selects the direct path. Direct cancellation/restart is
covered by actual browser tests and direct full/compact parity passes.

### Measured comparison (Brandubh smoke model, same stage)

| Workload | Relay | Direct | Change |
| --- | ---: | ---: | ---: |
| quick 128/256 | 667.7 sims/s | 692.9 sims/s | +3.8 % |
| strong 512/1,024 | 680.5 sims/s | 715.3 sims/s | +5.1 % |

The improvement is small and variable; direct mode is retained as an experimental
option, not made the default. The main-thread step instrumentation is not
applicable to the direct path (`step_ms` is zero and end-to-end elapsed is
reported).

## 3. Phase 3B.2 — bounded exact inference cache

Opt-in cache in the inference worker for legal-only MCTS predictions:

- Key: model SHA-256, ruleset, board size, requested/observed provider, encoded
  planes and ordered legal actions.
- Values: legal probabilities and defender-relative value.
- Required matching validated model/provider; cleared on model load/release.
- Deterministic LRU from `Map` order; bounded entries and estimated bytes.
  UI/benchmark use 8,192 entries / 64 MiB; worker hard caps are 16,384 entries /
  128 MiB.
- Hit/miss/eviction/entry/byte counters and hit rate; no tensor or PyProxy
  retention.
- Disabled by default; enabled explicitly in the experimental UI or benchmark.
  Full-logit and parity paths bypass the cache.

### Measured results (Brandubh smoke model)

| Workload | Cache disabled | Cache enabled | Change | Hit rate |
| --- | ---: | ---: | ---: | ---: |
| quick 128/256 | 598.8 sims/s | 850.7 sims/s | +42.1 % | 81.6 % |
| strong 512/1,024 | 722.1 sims/s | 900.9 sims/s | +24.8 % | 81.9 % |

Cache held 1,854 entries / 2.97 MiB (quick) and 7,946 entries / 12.84 MiB
(strong) with no evictions in these runs. The benefit is workload-dependent:
repeated positions/analysis reuse gains substantially; a single one-shot search
does not. It remains opt-in and is not a claim of general speedup.

## 4. Phase 3B.3 — independent-search batching

Implemented an opt-in bounded `NeuralClient` batch scheduler:

- FIFO batches of 1–`maxBatch` (default 4; configurable 1–32).
- Bounded queue (`maxQueue`, default 16), request IDs, model/ruleset/size
  compatibility, ordered result association, exactly-once resolution.
- Queue overflow, batch failure, queued cancellation and in-flight ignore paths
  are unit tested.
- Each independent search still owns a separate cooperative Pyodide search
  worker; they share one neural client/session and batch legal-only requests.

Actual browser validation with two independent Brandubh contexts at 32
simulations: 33 batches, observed batch size 2, both searches completed with
correct model/position association. Sequential vs batched wall time on the shared
CPU environment was **193.7 ms vs 337.1 ms** — batching did **not** improve
end-to-end throughput for this smoke/CPU setup because the extra Pyodide worker
contends for CPU. The scheduler remains opt-in and is not enabled by default.
Larger GPUs/larger models may benefit; that requires Phase 3C hardware evidence.

## 5. Phase 3B.4 — single-tree batching decision

**Deferred, not implemented.** The sequential generator's next leaf depends on
the previous prediction; virtual loss/reservations, duplicate-leaf suppression,
deferred expansion, exactly-once backpropagation and cancellation would require a
substantial search-tree refactor. No benchmark evidence yet justifies changing
the sequential reference. Sequential mode remains the only enabled MCTS.

## 6. Phase 3B.5 — subtree reuse decision

**Deferred, not implemented.** The native generator owns its tree internally.
Safe reuse requires exact root/history/model/settings/driver identity, completed
evaluation state, ownership/generation binding and careful reuse accounting.
Refactoring the native search for this was disproportionate for Phase 3B; the
decision is recorded as deferred rather than claiming an implementation.

## 7. Phase 3B.6 — stability status

Quick actual-browser lifecycle tests cover repeated searches, cancellation,
replacement, pending-inference drain, cache repeat use and two-context batching.
Node lifecycle tests cover generation ownership, queue overflow/failure and
cancellation. No 10/30-minute owner-run memory profile has been performed;
sustained process-memory acceptance remains outstanding. Available JS heap/WASM
capacity figures are reported, never substituted for actual process memory.

## 8. Validation summary

| Gate | Result |
| --- | --- |
| Path parity per-case validator (3B.0) | 68 passed |
| Direct full/compact fixed-work parity | 5/5 |
| Direct full/compact fixed-time validation | 5/5 |
| Relay full/compact fixed-work/time | 5/5 / 5/5 |
| Analysis UI (cache, direct, pending inference, navigation) | 32/32 |
| Neural inference UI (cache eviction/reload, hook isolation) | 22/22 |
| Neural client lifecycle (incl. batching/cache) | 46/46 |
| Search coordinator lifecycle | 30/30 |
| Staged asset integrity | 21/21 |
| Two-context actual-browser batching | pass; 33 batches of 2 |
| Cache hit/miss/eviction bounds | actual browser + stats |
| Level A/B model-backed parity | retained from Phase 3A, no search semantics changed |

## 9. Reproduction

```sh
# stage a smoke model
.venv/bin/python spikes/browser_offline/smoke_checkpoint.py \
  --ruleset brandubh --channels 4 --blocks 0 --output work/p3b/smoke.pt
.venv/bin/python spikes/browser_offline/onnx_export.py work/p3b/smoke.pt work/p3b/export
.venv/bin/python spikes/browser_offline/prepare_neural.py \
  --model-dir work/p3b/export --output work/p3b/spike --wasm-only

# cache and transport comparisons
node spikes/browser_offline/mcts_benchmark.mjs work/p3b/spike --provider wasm \
  --quick --repetitions 2 --payload-mode compact --output work/p3b/no-cache.json
node spikes/browser_offline/mcts_benchmark.mjs work/p3b/spike --provider wasm \
  --quick --repetitions 2 --payload-mode compact --cache --output work/p3b/cache.json
node spikes/browser_offline/mcts_benchmark.mjs work/p3b/spike --provider wasm \
  --quick --repetitions 2 --transport direct --output work/p3b/direct.json

# independent batching
node spikes/browser_offline/test_independent_batching.mjs work/p3b/spike \
  --simulations 32 --mode sequential --output work/p3b/seq.json
node spikes/browser_offline/test_independent_batching.mjs work/p3b/spike \
  --simulations 32 --mode batched --output work/p3b/batched.json

# parity and acceptance
node spikes/browser_offline/test_search_path_parity.mjs work/p3b/spike \
  --simulations 64 --transport direct --output work/p3b/direct-fixed.json
node spikes/browser_offline/test_search_path_parity.mjs work/p3b/spike \
  --fixed-time --transport direct --output work/p3b/direct-time.json
.venv/bin/python -m pytest tests/test_path_parity_validation.py \
  tests/test_browser_search_path_parity.py -q
node spikes/browser_offline/test_search_lifecycle.mjs
node spikes/browser_offline/test_browser_lifecycle.mjs
node spikes/browser_offline/test_mcts_ui.mjs work/p3b/spike
```

## 10. Evidence files

- Cache: [enabled](mcts-benchmark-cache-enabled-phase3b.json),
  [disabled](mcts-benchmark-cache-disabled-phase3b.json),
  [strong enabled](mcts-benchmark-cache-strong-phase3b.json),
  [strong disabled](mcts-benchmark-cache-strong-disabled-phase3b.json)
- Transport: [relay quick](mcts-benchmark-transport-relay-quick-phase3b.json) /
  [direct quick](mcts-benchmark-transport-direct-quick-phase3b.json); relay/direct
  [strong](mcts-benchmark-transport-relay-strong-phase3b.json),
  [strong direct](mcts-benchmark-transport-direct-strong-phase3b.json)
- Batching: [sequential](mcts-batching-sequential-phase3b.json),
  [batched](mcts-batching-batched-phase3b.json),
  [two contexts](mcts-batching-two-contexts-phase3b.json)
- Direct parity: [fixed](mcts-parity-direct-fixed-phase3b.json),
  [fixed time](mcts-parity-direct-time-phase3b.json)
- Relay parity: [fixed](mcts-parity-relay-fixed-phase3b.json),
  [fixed time](mcts-parity-relay-time-phase3b.json)
- [Analysis UI](mcts-browser-ui-phase3b.json),
  [neural UI](neural-browser-ui-phase3b.json),
  [search lifecycle](mcts-search-lifecycle-phase3b.txt),
  [neural lifecycle](neural-client-lifecycle-phase3b.txt),
  [staged integrity](search-assets-integrity-phase3b.txt)

## 11. External gates remain

Competitive trained checkpoints, statistically meaningful fixed-work/fixed-time
tournaments, verified physical WebGPU execution, physical desktop/mobile
compatibility and sustained process-memory measurements are **not** claimed by
Phase 3B. Phase 3C owns those gates.
