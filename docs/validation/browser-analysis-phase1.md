# Browser analysis Phase 1 validation (revision 5)

Final focused validation for Phase 1A (full-strength Jarl) and Phase 1B
(trained-model ONNX export and browser neural inference). Revision 5 adds the
request-side model-identity contract, complete UI operation ownership through
asset loading, hardware-independent provider tests, and larger-board
reference-vector compatibility.

## Integration and source identity

- Integrated current `main` (`1b4c4ae`, backend-read mountinfo hardening) with a
  normal merge as `b84a748`. The upstream change touched only the backend-read
  benchmark, its tests and documentation; no engine, model, inference or frontend
  runtime source used by the spike changed.
- Revision-1 through revision-4 evidence remains unchanged:
  `*-2026-11-19.json`, `*-r2.json`, `*-r3.json`, `*-r4.json`. Revision-5 evidence
  is the `*-r5.json` set below. The Jarl engine/benchmark comparison is unchanged
  in this round, so revision-4 Jarl measurements remain the current Jarl evidence.

Machine-readable revision-5 evidence:

- [ONNX export manifest](neural-export-manifest-r5.json) ·
  [native ONNX parity](neural-native-onnx-parity-r5.json)
- [browser WASM parity](neural-browser-parity-wasm-r5.json) ·
  [identity corruption kinds rejected](neural-browser-corruption-r5.json)
- [browser WASM benchmark](neural-browser-wasm-benchmark-r5.json) ·
  [actual-browser UI checks](neural-browser-ui-r5.json)
- [Copenhagen fixture structural export](neural-copenhagen-fixture-export-r5.json)
- [WebGPU parity blocked](neural-browser-parity-webgpu-blocked-r5.json) ·
  [WebGPU benchmark blocked](neural-browser-benchmark-webgpu-blocked-r5.json) ·
  [GPU-execution evidence blocked](neural-browser-gpu-execution-blocked-r5.json)

## Environment

Linux x86_64 LXC, 4 CPUs; CPython 3.11.18; torch 2.14.1+cpu; onnx 1.23.2 /
onnxruntime 1.32.0; Node 22.23.4; Chromium 156.0.8078.4 headless; Pyodide 0.27.7;
ORT Web 1.22.0. No physical GPU adapter, physical phone or competitive checkpoint
was available.

## Phase 1B — request-side model identity

Inference requests are now bound to the exact model they were prepared for:

- The shared `neural-identity.js` contract requires `{sha256, ruleset, size}` and
  rejects missing, malformed, mismatched-digest, wrong-ruleset and wrong-size
  identities with distinct error codes.
- The worker validates the expected identity against the **active session**
  before building inputs, for both single and batched inference. Batch-level model
  metadata is used once per batch rather than duplicated per position.
- The client exposes `modelIdentity()` so consumers capture identity when the
  workload is prepared, and `_assertModel()` rejects a stale identity before it
  reaches the worker.
- Single-position inference validates the supplied size and ruleset against the
  model; loading now requires a supported ruleset identity.
- `neural_parity.mjs`, `neural_benchmark.mjs` and the UI capture identity after
  load and pass it to every inference, batch, benchmark and sustained call.
- Deliberate corruption tests cover wrong digest, correct-architecture/different
  weights, missing identity, wrong ruleset, wrong size, incompatible batch, and a
  request prepared for model A submitted after model B replaced it. Both the
  client assertion and the worker-side check are exercised.

## Phase 1B — UI operation ownership

`neural.html` now propagates the operation token through manifest loading,
reference-vector loading, model initialization, inference, benchmark execution,
status updates and result publication:

- Shared asset fetches are cached, but every operation re-checks ownership after
  each asynchronous boundary; a cancelled operation cannot resume when a shared
  fetch completes.
- Cancellation clears ownership before terminating the client, and a replacement
  operation can proceed. Provider selection is disabled during active work and
  changes are ignored while busy.
- Six additional actual-browser checks cover cancellation during manifest and
  reference loading, replacement after cancellation, obsolete asset completion,
  absence of a stale worker/model, and absence of stale status/result
  publication.

## Phase 1B — hardware-independent provider tests

The UI suite separates deterministic failure recovery from hardware capability:

- A simulated WebGPU initialization failure verifies that switching back to WASM
  recovers deterministically.
- A hardware test accepts either legitimate outcome: successful WebGPU-requested
  inference (validated against the numerical response, without claiming GPU-only
  placement) or an explicit adapter/unsupported failure. Success is never treated
  as a regression.
- `--require-webgpu` still requires a WebGPU-requested session and numerical
  success; `--require-gpu-execution` still requires independently verified GPU
  execution and fails explicitly because the pinned ORT Web runtime exposes no
  per-operator placement diagnostic.

## Phase 1B — larger-board reference vectors

Measurements with tiny fixture networks (structural only; **not** competitive
trained-model validation):

| Ruleset | Cases | Logits/case | Indented JSON | Compact JSON | Limit |
| --- | ---: | ---: | ---: | ---: | ---: |
| Brandubh | 39 | 2,401 | 2.90 MB | 1.90 MB | 16 MiB |
| Tablut | 39 | 6,561 | 7.76 MB | 5.24 MB | 16 MiB |
| Copenhagen | 39 | 14,641 | **16.96 MB** | **11.55 MB** | 16 MiB |

Indented Copenhagen reference vectors exceeded the experimental budget, so the
exporter now writes reference vectors as compact JSON (exact float values
preserved, digest computed over the written bytes). The 16 MiB limit remains.
A full Copenhagen fixture export validates and stages at 11.57 MB with 39 parity
cases; no fitness-for-strength claim is made.

## Measured results (revision 5)

| Check | Result |
| --- | --- |
| Browser WASM parity (39 cases) | pass; worst logit 1.67e-6, postprocess 0 |
| Distinct-position batches 1–32 | pass, ordered and associated |
| Request-identity corruptions | 6/6 fail (client and worker paths) |
| WASM init / first inference / warm run | 551.1 ms / 21.8 ms / 0.6 ms |
| Sustained in-worker throughput (200 × batch 8) | 4,476.8 positions/s |
| Actual-browser UI checks | 11/11 (clean state, ownership, cancel, provider) |
| WebGPU hardware outcome on this host | explicit no-adapter failure |
| Copenhagen fixture export | validated, 11.57 MB reference, 39 cases |

Heap fields remain `null` (no `performance.memory` in headless Chromium; ORT Web
does not expose its WASM heap), so no memory-stability claim is made.

## Repository checks

- Focused native tests: Jarl 35, neural parity 47, ONNX export 31 (optional deps),
  client lifecycle 2 (27 Node checks), tensor cleanup 1 (7 Node checks),
  identity 1 (7 Node checks), offline spike 17 — **134 passed**.
- Actual-browser UI gate with `SPIKE_NEURAL_DIR`: **1 passed** (11 interactions).
- `verify.mjs` Phase 0 gate remains green (unchanged classical path).
- `ruff check`/`format --check`, `mypy hnefatafl`, `scripts/check_docs.py` pass.
- Full CI runs on the final head.

## Remaining blockers and Phase 2 readiness

- Competitive trained weights (32×2+) for all three rulesets: blocked.
- Physical WebGPU execution and verified GPU execution: blocked; per-operator
  placement is not exposed by the pinned runtime.
- Physical desktop/mobile performance and process memory: blocked.
- Three Copenhagen depth-5 Jarl cases remain unresolved within the bounded 60 s
  budget (documented in the revision-4 Jarl evidence).
- Playing strength: Phase 3, not started.

The WASM inference infrastructure is now ready for Phase 2 development: request
and response identity, tensor ownership, batch order, cancellation, lifecycle and
error classification are validated. Phase 2 is not implemented in this PR.
