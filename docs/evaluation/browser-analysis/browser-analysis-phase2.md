# Browser analysis Phase 2 validation (revisions 1 and 2)

Focused validation for Phase 2 of the
[browser-side analysis roadmap](../../development/roadmap/browser-analysis.md): the
existing Python neural MCTS search now runs locally in the browser, using the
validated ONNX Runtime Web inference boundary from Phase 1B.

Revision 1 established implementation readiness and software validation.
Revision 2 is the PR #108 review-correction round: reliable search ownership and
cancellation across both workers, independent production-MCTS reference
validation, hardened native/browser comparators, checkpoint-bound model
identity, exception-safe context cleanup, a strict legal-probability contract,
lazy neural assets, bfcache recovery and mandatory staged-source integrity.
Historical revision-1 evidence is preserved unchanged (`*-r1.json`); revision-2
measurements are the `*-r2.json` / `*-r7.json` set. Competitive trained models,
physical devices and playing strength remain **external acceptance gates** and
are not claimed.

## Environment

| Item | Value |
| --- | --- |
| Host | Linux x86_64 LXC, 4 CPUs, glibc 2.41 |
| Native runtime | CPython 3.11.18, torch 2.14.1+cpu, onnxruntime 1.32.0, onnx 1.23.2 |
| Browser harness | Node.js 22.23.4, Playwright 1.64.1, Chromium 156.0.8078.4 |
| Python-in-browser | Pyodide 0.27.7 (CPython 3.12.7) |
| Inference runtime | ONNX Runtime Web 1.22.0, `wasm` (single-threaded, SIMD) |
| Models | Structural smoke fixtures only: 4 channels, 0 residual blocks, minimal synthetic training |
| GPU | No physical WebGPU adapter available in this environment |

`engine_identity.sha256 = caa20157…e257a` for the native and Pyodide runs in the
Level A report; equality is required by the comparator and proves both runtimes
executed the same engine, encoder and driver sources.

## What was delivered

| Subphase | Delivered |
| --- | --- |
| 2.0 | Phase 1 follow-up corrections: retryable asset loading, page lifecycle ownership, independent corruption boundaries, mandatory model identity for every workload |
| 2.1 | PyTorch-independent six-plane encoder (`hnefatafl/encoding.py`), shared by native inference and the browser search worker |
| 2.2 | Cooperative browser neural MCTS driver (`hnefatafl/mcts_driver.py`, `search_bridge.py`) around the unchanged `mcts_context()` generator |
| 2.3 | Reproducible Level A/B/C parity suite with one comparison implementation (`mcts_compare.py`) |
| 2.4 | Search worker + coordinator (`search-worker.js`, `search-client.js`), worker generations, deadlines, cooperative cancellation and bounded recovery |
| 2.5 | Native and browser neural MCTS benchmarks with timing, throughput and resource reporting |
| 2.6 | Experimental analysis UI on the existing neural page (Jarl and neural MCTS), plus generated benchmark evidence |

## Architectural decision

The search is **not** reimplemented in JavaScript, TypeScript, Rust or C++. The
browser runs the existing Python engine and `mcts_context()` generator inside
Pyodide:

- `mcts_context(neural=True)` yields one dependent leaf evaluation at a time.
- The search worker advances exactly one generator slice per message, so it never
  blocks its event loop waiting for inference.
- Each yielded leaf is encoded with the shared PyTorch-free encoder and sent to
  the persistent neural inference worker (ORT Web) by the coordinator.
- The validated response (legal priors and defender-relative value) resumes the
  same generator; traversal, PUCT selection, progressive widening, lazy states,
  prediction reuse, backpropagation and deterministic best-move selection are
  unchanged.

Rejected alternatives:

- **A second rules/search engine in JavaScript** would duplicate three rulesets,
  repetition history and variant outcomes and require permanent differential
  tests on every engine change. Nothing in the measurements justifies that cost.
- **Bypassing the validated inference worker** (e.g. calling ORT from the search
  worker) would duplicate model-identity, tensor-validation and provider-reporting
  logic that Phase 1B already established.
- **Single-tree batched leaf selection** changes the order in which predictions
  affect traversal and therefore playing strength. Sequential mode remains the
  correctness reference; batching is deferred to Phase 3.
- **`SharedArrayBuffer`/`Atomics.wait()`** would require cross-origin isolation
  and blocking synchronisation; the cooperative boundary works without either.

## Search semantics preserved

`mcts_context()` and its supporting code were not modified. The driver adds only
bookkeeping and validation:

- Analysis never enables Dirichlet noise; `temperature` is forced to `0`, so
  suggestions use deterministic best-move selection (maximum visits), like the
  existing analysis path.
- Values remain defender-relative throughout; no sign conversion happens in
  JavaScript.
- `analysis_from_search()` gained one optional keyword, `model_backed`, so an
  externally evaluated neural search is classified as `neural_search` without a
  fake model path or placeholder model object. Native callers are unchanged.
- The established analysis keys (`value`, `source`, `best_move`, `simulations`,
  …) are preserved; new diagnostics live in a versioned `search` envelope.

## Phase 1 follow-up corrections

### Retryable initial asset loading

`neural.html` now has one shared loader for the eager warm-up and every on-demand
load. It checks `response.ok` before parsing, reports malformed JSON explicitly,
clears failed cached promises so a retry is possible without a page reload, keeps
successful immutable assets cached, re-checks operation ownership after every
await and prevents a cancelled or shut-down operation from creating a worker.
Verified by new browser checks: initial manifest failure → retry, initial
reference-vector failure → retry, malformed JSON → retry, cancellation during a
retry (no worker/model created), plus the existing ownership checks.

### Page lifecycle ownership

`pagehide` now reuses the cancellation path: operation ownership is invalidated
before the neural client and coordinator are disposed, so pending asset
preparation, model loading or an active search cannot publish results or spawn a
replacement worker afterwards. `pageshow` (`persisted`) restores the page to a
usable idle state. Verified in the browser: `pagehide` during asset preparation
prevents worker creation (`spawns: 0`), `pagehide` during model loading publishes
no model, `pageshow` allows a new operation, and real navigation away/back
(`about:blank` → back) recovers without a reload.

### Independent batch worker corruption testing

Request-identity corruption now names exactly one execution boundary
(`single-client`, `single-worker`, `batch-client`, `batch-worker`). No
batch-specific corruption is applied during single-position validation. Each run
asserts the exact expected error code at the intended boundary, proves a control
request with the correct identity succeeds at that same boundary, and exits `1`
only when the intended rejection was observed (`3` when it was not). Evidence:
[neural-browser-identity-boundaries-r6.json](./evidence/neural-browser-identity-boundaries-r6.json)
— all four boundaries report `MODEL_IDENTITY_MISMATCH` with
`control_accepted: true`, and `corruption_detected: true`.

### Mandatory model identity

`infer()`, `inferMany()`, `benchmark()`, `sustained()` and the new neural MCTS
search now require the immutable model identity (`sha256`, `ruleset`, `size`)
captured when the workload is prepared. The client rejects a missing or stale
identity before transport; the worker validates it against the active session
before touching inputs. A search captures identity once at start and re-validates
every inference response, so a model switch mid-search fails closed instead of
mixing predictions. Verified by Node lifecycle checks, browser worker-path checks
and the corruption evidence above.

## Search correctness and parity

### Level A — shared search-driver parity (exact)

Native CPython and browser Pyodide drove the same generator with the shared
deterministic evaluator (pure integer hash priors, integer-over-power-of-two
value; no transcendental functions).

- 21 positions: all three rulesets; opening, midgame, endgame, tactical, king-escape,
  terminal (king escape) and repetition-sensitive states.
- 128 requested simulations; workload digest and engine source identity equal.
- **21/21 exact** matches: analysis, root value, search value, completed
  simulations, visit distribution, root children, neural requests, prediction
  reuses and the complete evaluation trace (position digest, legal count, value).

Evidence: [native](./evidence/mcts-parity-native-a-r1.json) ·
[browser](./evidence/mcts-parity-browser-a-r1.json).

### Level B — actual model-backed search

The same staged model, positions, repetition history, settings, seed,
exploration and analysis temperature were used on both runtimes. Reference
models: native ONNX Runtime CPU and native PyTorch CPU; browser ORT Web WASM.

| Ruleset | Simulations | Positions | Best moves matched | Max value delta | Max visit TV | Requests delta |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Brandubh | 256 | 7 | 7/7 | 2.2e-9 | 0 | 0 |
| Tablut | 128 | 7 | 7/7 | 3.0e-9 | 0 | 0 |
| Copenhagen | 128 | 7 | 7/7 | 3.7e-9 | 0 | 0 |

Browser vs native **PyTorch** (Brandubh, 256 simulations): 7/7 best moves, same
workload and identical completed simulations. For this tiny smoke model the WASM
and CPU trees were numerically equivalent apart from last-bit value differences;
this is reported as an observation, not as a general guarantee. The comparator
would report divergent trajectories rather than failing them silently.

Evidence: [brandubh native ONNX](./evidence/mcts-parity-native-b-brandubh-r1.json) ·
[brandubh browser](./evidence/mcts-parity-browser-b-brandubh-r1.json) ·
[brandubh native PyTorch](./evidence/mcts-parity-native-b-brandubh-torch-r1.json) ·
[PyTorch↔browser comparison](./evidence/mcts-parity-compare-torch-browser-brandubh-r1.json) ·
[tablut](./evidence/mcts-parity-browser-b-tablut-r1.json) ·
[copenhagen](./evidence/mcts-parity-browser-b-copenhagen-r1.json).

### Legal, terminal and perspective checks

Every non-terminal browser best move was validated against the exact root
position with the unchanged engine. Terminal positions complete without neural
inference and are classified `terminal`. The comparator fails a non-finite or
out-of-range value. Negative controls: manager rejection of reordered legal
indices, non-finite/negative priors, zero-sum policies and out-of-range values;
immediate-win shortcut completes without claiming the simulation budget.

## Benchmarks (structural smoke models — not competitive throughput)

Workload: opening and tactical positions, 1 warm-up + 1 repetition, WASM,
`brandubh` unless stated. Native reference: ONNX Runtime CPU, single-threaded.

### Fixed-simulation throughput

| Workload | Native sims/s | Browser sims/s | Browser/native |
| --- | ---: | ---: | ---: |
| Brandubh 128 + 256 | 3,298.5 | 522.7 | 0.163 |
| Brandubh 512 + 1,024 | 3,145.0 | 491.0 | 0.160 |
| Tablut 128 | 1,653.0 | 337.6 | 0.212 |
| Copenhagen 128 | 1,093.3 | 232.1 | 0.214 |

Representative end-to-end browser fixed-work results (4ch/0-residual smoke model):

| Case | Completed | End-to-end | Simulations/s |
| --- | ---: | ---: | ---: |
| Brandubh opening, 512 | 512 | 1,027 ms | 499 |
| Brandubh opening, 1,024 | 1,024 | 1,816 ms | 564 |
| Brandubh tactical, 512 | 512 | 1,059 ms | 484 |
| Brandubh tactical, 1,024 | 1,024 | 2,311 ms | 443 |

The aspirational target (1,024 simulations in ≈2 s on capable desktop hardware) is
therefore approximately met **for a 4-channel, zero-residual structural fixture**
(1,024 fixed simulations completed in 1.82–2.31 s, 443–564 simulations/s). This is not evidence for competitive networks, and raw inference
throughput is not extrapolated into MCTS throughput.

### Fixed-time behaviour

| Workload | Browser median completed | Median deadline overshoot | Max overshoot |
| --- | ---: | ---: | ---: |
| Brandubh 0.5 s / 1 s | 353 | 9.0 ms | 11 ms |
| Brandubh 2 s / 5 s | 2,016 | 14.0 ms | 18 ms |
| Tablut 0.5 s | 182 | 8.5 ms | 9 ms |
| Copenhagen 0.5 s | 124 | 9.0 ms | 11 ms |

Completed simulations differ from native by design; the report never compares
unequal work as a speedup.

### Timing breakdown (browser, 512–1,024 simulations)

Mutually exclusive top-level categories (median share of end-to-end time):

| Category | Share | Notes |
| --- | ---: | --- |
| Coordinator ↔ Pyodide step boundary | 36.0 % | JSON serialization + `runPython` call boundary, ~0.68 ms/step |
| Neural wait (search → inference result) | 37.3 % | includes transport to/from the neural worker, tensor prep and ORT execution |
| Python search work | 25.9 % | traversal 61 %, encoding 16 %, legal generation 16 %, response validation 4.6 % |
| Unattributed | 0.6 % | measurement gap |

Nested (do **not** add to the table above): within neural wait, ORT execution is
36.0 % and tensor preparation 3.2 %; the remainder is inference dispatch and
worker transport. Median 512–1,024-simulation case: 1,438 ms end-to-end, 517 ms
step boundary, 537 ms neural wait (193 ms ORT execution), 373 ms Python (228 ms
traversal, 61 ms encoding, 61 ms legal generation, 17 ms validation), 8.3 ms
unattributed, 764.5 inferences.

### Cold and warm startup

| Measurement | Browser | Native |
| --- | ---: | ---: |
| Page ready (assets + reference vectors) | 85–226 ms | n/a |
| Search worker + ORT + model ready (cold) | 2.72–3.20 s | 0.09–0.10 s |
| Warm search reuse | no worker/session recreation | same session |

Repeated searches reuse the Pyodide search worker and the ORT model session
(verified: coordinator generation and model digest unchanged).

### Identified bottlenecks (in measured order)

1. **Per-step coordinator/Pyodide boundary (36 %).** Every dependent evaluation
   costs one JSON round trip across the worker and Pyodide boundary. This is now
   the largest single cost and the clearest Phase 3 target.
2. **Inference wait (37 %), of which only ~36 % is actual ORT execution.** The
   rest is inference dispatch/worker transport. A direct search↔inference
   `MessageChannel` should be measured against the current coordinator relay.
3. **Python search work (26 %),** dominated by traversal (61 % of it); encoding
   and legal generation together are ~8 % of end-to-end time.
4. **Cold startup ~2.7–3.2 s**, dominated by Pyodide and ORT initialization, not
   by the model (46.9 KB artefact).

No optimization has been applied on the basis of these numbers yet; they are the
Phase 3 starting point. The browser path deliberately runs **uncached** in
Phase 2: `inference_cache_hits` is reported as `0`, and any future exact
inference cache must key on model digest, encoder version, exact encoded input,
precision/provider compatibility and the legal-action set, never on the six-plane
digest alone.

### Resource metrics

| Metric | Value | Availability |
| --- | ---: | --- |
| Pyodide search-worker WASM heap capacity | 20,971,520 bytes | measured |
| Page JS heap used / limit | 10.0 MB / 3.76 GB | Chromium hint |
| Neural worker WASM heap | null | not exposed by pinned ORT Web |
| Peak pending inference requests | 1 | sequential driver |
| Search-tree proxy (root children) | reported per case | bounded |
| Worker recreation during repeated searches | 0 | measured |
| Cancellation latency | ≤ 2.5 s bounded; cooperative ack normally next message | bounded design |

Actual process RSS is not available from the browser harness and is reported as
unavailable, not estimated.

## Cancellation, progress and lifecycle

- Every search is bound to a search id, worker generation, model identity, full
  position identity, effective settings, cancellation state and watchdog.
- Cancellation is cooperative (`cancel` closes the Python generator) with a
  2.5 s acknowledgement deadline; termination is the bounded final recovery path.
- Cancelled or superseded searches publish no analysis, never resume an invalid
  generator and never terminate a newer worker. An in-flight inference is raced
  against the cancellation signal so a cancelled search cannot stay pending.
- Progress reports completed simulations only (no pending/reserved work), is
  throttled to 100 ms and never overwrites a newer status.
- Abandoned Python generators are closed and their diagnostics/trace released;
  the bridge drops the search context on completion, cancellation and close.
- Verified by 14 deterministic Node coordinator checks and browser checks for
  cancel during analysis, `pagehide` teardown and repeated-search reuse.

## Experimental UI

`neural.html` now contains a position-analysis panel:

- ruleset and position selection (21 serialized positions), Jarl or neural MCTS,
  provider, simulations (128–4096), time budget (0.25–10 s), exploration, seed;
- start/cancel/export, live progress, best move, defender-relative value,
  completed simulations, simulations/second, duration, model, provider and
  completion reason, with an accessible status region and disabled states;
- neural runtime and model load lazily; choosing Jarl never creates the ORT
  worker, the search worker or a model session (verified).

Browser UI evidence: [mcts-browser-ui-r1.json](./evidence/mcts-browser-ui-r1.json) (9/9
checks) and the retained Phase 1 inference page checks
[neural-browser-ui-r6.json](./evidence/neural-browser-ui-r6.json) (19/19).

## Security and isolation

- No production route, API, authorization, database, CSP or frontend dependency
  changed. All new code is in `hnefatafl/` shared pure modules and
  `spikes/browser_offline/`.
- Search requests are bounded: ≤512 KiB payload, 1–2 active contexts, 1–4096
  simulations, 0.05–10 s cooperative deadline, exploration 0–10, finite/non-negative
  priors, defender value in [-1, 1], exact legal-index association, model identity.
- The search worker loads only same-origin staged assets and verifies SHA-256
  digests for Python sources; no external URL, model path or executable code can
  be supplied through the browser interface.
- Automated browser gates block non-origin requests and assert zero external
  requests; the staged page works from the loopback server only.
- No `SharedArrayBuffer`, `Atomics.wait`, COOP/COEP or CSP relaxation was added.

### Offline behaviour

- An already initialized model session and warm search worker continue to work
  without network connectivity (no network requests are made after staging).
- A browser reload with locally cached Pyodide/ORT/model assets is supported when
  the serving origin's HTTP cache or service worker serves them; the Phase 2 page
  does not register a service worker.
- A **fully offline cold startup is not supported** by this prototype: the page
  requires same-origin asset fetches. Persistent offline storage for the neural
  runtime/model is explicitly deferred (Phase 4).

## Regression testing

| Gate | Result |
| --- | --- |
| New encoding-parity tests | 58 passed |
| New driver/bridge/result-semantics tests | 40 passed |
| Node coordinator lifecycle checks | 14/14 |
| Node neural/Jarl client lifecycle checks | 28/28 |
| Existing Jarl, MCTS, analysis, models, inference, parallel-search tests | passed (focused run) |
| `tests/test_mcts_optimization.py::test_frozen_reference_…` | 6 of 12 cases fail **identically on pristine `main`** in this environment (unrelated pre-existing failure; untouched by this change) |
| `ruff check` / `ruff format --check` / `mypy hnefatafl` | clean |
| Phase 1 WASM inference parity at this revision | 39/39 cases pass; batches 1–32 pass |
| Phase 1 inference UI at this revision | 19/19 checks |

## Outstanding external acceptance gates

These are **not** satisfied by this revision and must not be reported as done:

1. Competitive trained checkpoints (32×2 and larger) for all three rulesets:
   correctness, throughput and strength.
2. Physical desktop performance (WSL-to-Windows Chrome/Edge, NVIDIA RTX 3060 over
   Thunderbolt, Intel integrated GPUs).
3. Physical WebGPU execution (`--require-webgpu` on real hardware, mixed/unknown
   placement reporting, `--require-gpu-execution` still unverifiable).
4. Physical mobile and Safari/Firefox behaviour, long-term memory stability.
5. Playing-strength validation (browser vs native neural MCTS, browser vs Jarl,
   simulation-budget comparisons, WASM vs WebGPU, sequential vs batched).
6. Production integration (security review, model distribution, offline storage,
   CSP, frontend feature boundary).

## Reproduce

```sh
# 1. Environment
uv sync --frozen --extra train --extra dev
uv pip install --python .venv/bin/python -r spikes/browser_offline/requirements-onnx.txt
npm --prefix frontend ci
npx --prefix frontend playwright install chromium

# 2. Structural smoke checkpoint (not a competitive model) and validated export
.venv/bin/python spikes/browser_offline/smoke_checkpoint.py \
  --ruleset brandubh --output /tmp/smoke-brandubh.pt
.venv/bin/python spikes/browser_offline/onnx_export.py \
  /tmp/smoke-brandubh.pt /tmp/model-export

# 3. Stage the complete experimental runtime (ORT + model + Pyodide engine)
.venv/bin/python spikes/browser_offline/prepare_neural.py \
  --model-dir /tmp/model-export --output /tmp/neural-spike

# 4. Level A shared-driver parity: native then browser, then compare in-process
.venv/bin/python spikes/browser_offline/native_mcts_parity.py \
  --level a --iterations 128 --output /tmp/mcts-native-a.json
node spikes/browser_offline/mcts_parity.mjs /tmp/neural-spike \
  --native /tmp/mcts-native-a.json --level a --output /tmp/mcts-browser-a.json

# 5. Level B actual model-backed parity (native ONNX and browser WASM)
.venv/bin/python spikes/browser_offline/native_mcts_parity.py \
  --level b --evaluator onnx --model-export /tmp/model-export \
  --rulesets brandubh --iterations 256 --output /tmp/mcts-native-b.json
node spikes/browser_offline/mcts_parity.mjs /tmp/neural-spike \
  --native /tmp/mcts-native-b.json --level b --provider wasm \
  --output /tmp/mcts-browser-b.json
# optional strict gate: --require-best-move-parity
# native PyTorch reference instead of ONNX:
.venv/bin/python spikes/browser_offline/native_mcts_parity.py \
  --level b --evaluator torch --checkpoint /tmp/smoke-brandubh.pt \
  --model-export /tmp/model-export --rulesets brandubh --iterations 256 \
  --output /tmp/mcts-native-b-torch.json
.venv/bin/python spikes/browser_offline/mcts_compare.py \
  /tmp/mcts-native-b-torch.json /tmp/mcts-browser-b.json

# 6. Benchmarks (native then browser; the browser harness compares in-process)
.venv/bin/python spikes/browser_offline/native_mcts_benchmark.py \
  --model-export /tmp/model-export --evaluator onnx --quick \
  --output /tmp/mcts-native-bench.json
node spikes/browser_offline/mcts_benchmark.mjs /tmp/neural-spike \
  --provider wasm --quick --native /tmp/mcts-native-bench.json \
  --output /tmp/mcts-browser-bench.json
# extended owner-hardware matrix (select explicitly)
node spikes/browser_offline/mcts_benchmark.mjs /tmp/neural-spike \
  --provider webgpu --extended --native /tmp/mcts-native-extended.json \
  --output /tmp/mcts-browser-extended.json

# 7. UI, cancellation, lifecycle and corruption gates
node spikes/browser_offline/test_mcts_ui.mjs /tmp/neural-spike --output /tmp/mcts-ui.json
node spikes/browser_offline/test_search_lifecycle.mjs
node spikes/browser_offline/test_neural_ui.mjs /tmp/neural-spike --output /tmp/ui.json
node spikes/browser_offline/test_browser_lifecycle.mjs
node spikes/browser_offline/neural_parity.mjs /tmp/neural-spike \
  --provider wasm --corrupt request-model-batch-worker \
  --output /tmp/identity-batch-worker.json   # exit 1 = rejection detected as expected

# Owner-supplied competitive checkpoint: same commands with --model-export from
# onnx_export.py on the private checkpoint (never committed).
```

Machine-readable evidence in this directory:

- Level A: [native](./evidence/mcts-parity-native-a-r1.json) ·
  [browser](./evidence/mcts-parity-browser-a-r1.json)
- Level B: brandubh [native ONNX](./evidence/mcts-parity-native-b-brandubh-r1.json) ·
  [browser](./evidence/mcts-parity-browser-b-brandubh-r1.json) ·
  [native PyTorch](./evidence/mcts-parity-native-b-brandubh-torch-r1.json) ·
  [PyTorch↔browser comparison](./evidence/mcts-parity-compare-torch-browser-brandubh-r1.json);
  tablut [native](./evidence/mcts-parity-native-b-tablut-r1.json) ·
  [browser](./evidence/mcts-parity-browser-b-tablut-r1.json);
  copenhagen [native](./evidence/mcts-parity-native-b-copenhagen-r1.json) ·
  [browser](./evidence/mcts-parity-browser-b-copenhagen-r1.json)
- Benchmarks: [native quick](./evidence/mcts-benchmark-native-quick-r1.json) ·
  [browser quick](./evidence/mcts-benchmark-browser-quick-r1.json) ·
  [native strong](./evidence/mcts-benchmark-native-strong-r1.json) ·
  [browser strong](./evidence/mcts-benchmark-browser-strong-r1.json) ·
  tablut [native](./evidence/mcts-benchmark-native-tablut-r1.json) /
  [browser](./evidence/mcts-benchmark-browser-tablut-r1.json) ·
  copenhagen [native](./evidence/mcts-benchmark-native-copenhagen-r1.json) /
  [browser](./evidence/mcts-benchmark-browser-copenhagen-r1.json)
- UI and Phase 1 follow-ups: [neural MCTS UI](./evidence/mcts-browser-ui-r1.json) ·
  [inference UI](./evidence/neural-browser-ui-r6.json) ·
  [identity boundaries](./evidence/neural-browser-identity-boundaries-r6.json) ·
  [inference parity](./evidence/neural-browser-parity-wasm-r6.json) ·
  [inference benchmark](./evidence/neural-browser-wasm-benchmark-r6.json)


---

# Revision 2 — PR #108 review corrections

## Corrections delivered

1. **Search ownership before asynchronous initialization.** The coordinator
   reserves a unique search record (identifier, worker generation, expected model
   identity, settings, deadline, cancellation state) synchronously, before any
   await. Ownership is re-checked after cancelling a previous search, worker
   initialization, the start response, every inference, every resume, progress
   publication and final publication. The worker-returned `search_id` is
   validated, never overwritten; a mismatch fails the search and replaces the
   worker. Cold-initialization cancellation is immediate (the boot promise is raced
   against the search's cancellation signal), competing waiters cannot both own a
   search, and only the newest admission may start.
2. **Cancellation across both workers.** `NeuralClient.cancelRequest()` rejects the
   caller immediately, then either drains the in-flight inference (retaining the
   healthy ORT session and worker) or terminates an unresponsive worker within a
   bounded drain deadline. Replacement inferences are refused while draining.
   The coordinator releases the inference it owns during search cancellation,
   failure and worker loss, reports exactly what was released, and never cancels
   a newer search's context or a replacement worker.
3. **Complete UI analysis cancellation.** Analysis ownership now covers position
   loading, manifest loading, model download/digest verification, session
   initialization, search-worker initialization, traversal, inference and
   publication. Cancel clears ownership first, cancels the Jarl client, the search
   coordinator and the neural client (`cancelWork` aborts an owned model operation
   or drains an inference). Obsolete loads cannot publish a session. Raw inference
   and neural MCTS are mutually exclusive owners of the shared neural client.
4. **Back-forward cache restoration.** A disposed coordinator/neural client is
   never reused. `pagehide` still invalidates ownership and disposes; `pageshow`
   (persisted) now installs fresh runtime instances, keeps only immutable
   manifest/position caches, clears operation state and restores the controls.
   Tests complete a new Jarl analysis **and a new 128-simulation neural MCTS
   search** after restoration, and after real navigation away/back.
5. **Hardened parity validation.** `mcts_compare.py` now validates both reports
   independently: schema, level, engine identity, workload digest, reproducible
   fixture selection (re-derived from `fixtures.jarl_positions`, so a case omitted
   from both reports is detected), unique positions/results, complete coverage,
   settings, model identity, per-case execution validity, requested/completed
   simulations (a report cannot redefine its budget), completion reason/deadline
   consistency, terminal and immediate-win semantics (no terminal inference),
   defender-relative values, legal moves and visit distribution integrity
   (duplicates, illegal actions, non-finite shares, negative visits, incoherent
   totals). Acceptance, deterministic parity, numerical comparison, workload
   completion and performance comparability are separate sections.
6. **Checkpoint-bound model identity.** The exporter sidecar (schema 2) records the
   source weight fingerprint and encoder version; the manifest records the
   fingerprint and checkpoint digest inside the existing digest chain. Native
   parity and benchmark paths reuse the staging validator and require the
   checkpoint's `weights_fingerprint` and architecture to match the export, reject
   a ruleset/workload mismatch before inference, and never derive an apparent model
   identity from the workload position. Tests export two checkpoints with
   identical architecture/ruleset and reject the wrong one.
7. **Independent production MCTS reference.** New tests drive the production
   `mcts_context` and `mcts_policy` (with a real tiny network) directly and require
   the driver to reproduce the tree digest, seeded traversal, best move, values,
   completed simulations, request/reuse counters and evaluation trace exactly, plus
   terminal and immediate-win semantics; cancellation is shown to be distinct from
   a deadline.
8. **Benchmark validation from raw records.** Expected measured repetitions are
   derived from the workload (positions, mode/budget cases, repetitions) and the
   fixture selection; missing/duplicate/failed records and unexpected records fail.
   Fixed-work completion is mandatory; fixed-time runs with different completed
   work are reported without a throughput ratio; equal-work ratios require equal
   completed simulations **and** neural evaluations.
9. **Exception-safe cleanup.** `NeuralSearch` closes its generator on every exit
   path (completion, cancellation, invalid response, inference failure, timeout,
   memory error, disposal) and retains a final completed-work summary. Malformed
   evaluator responses are explicitly non-recoverable and release the bridge
   registry slot; hard deadline and cancellation are reported separately.
10. **Strict legal-probability contract.** Materially invalid distributions
    (|sum-1| > 1e-3) are rejected instead of renormalised; only floating-point
    rounding is accepted, unchanged. Non-finite/negative priors, malformed legal
    indices, mismatched association and out-of-range values remain hard errors.
11. **Lazy neural assets.** Nothing neural is fetched on page load. Jarl issues zero
    neural requests and works when the manifest, model, reference vectors and ORT
    runtime are unavailable. Neural MCTS loads only the model manifest; the large
    numerical reference vectors load only for raw inference/benchmarks. Failure and
    retry behaviour is preserved.
12. **Position and state consistency.** Refreshing loaded positions preserves the
    selected position; the exact position name, ruleset, agent, provider and
    settings are captured at admission, so selector changes cannot redirect a
    running analysis. Tests cover midgame, endgame and repetition-sensitive
    positions and an in-flight selector change.
13. **Mandatory staged-source integrity.** The worker validates the manifest
    (schema, module lists, uniqueness, required `search_bridge.py`, inventory
    completeness) and requires a valid expected SHA-256 for every mandatory Python
    source before fetching; no source is executed unverified. Negative tests cover
    missing/altered/duplicate/malformed entries.
14. **Native encoding performance.** The shared byte-plane encoder with a
    zero-copy uint8 view is **8.9× (Brandubh), 11.5× (Tablut), 15.5× (Copenhagen)**
    faster than the previous tensor-assignment path for representative single
    positions, with exact tensor parity, unchanged dtype/shape/device and batched
    stacking; a generous non-regression test guards future changes.

## Revision-2 validation evidence

| Gate | Result |
| --- | --- |
| Level A shared-driver parity (all rulesets, 21 positions, 128 simulations) | **21/21 exact**, engine identity and workload digest equal, workload complete |
| Level B Brandubh 256 / Tablut 128 / Copenhagen 128 | 7/7 best moves each; max value Δ 2.2e-9 / 3.0e-9 / 3.7e-9; visit TV 0; acceptance true |
| Browser WASM vs native PyTorch (Brandubh 256) | 7/7 best moves; acceptance true |
| Independent production reference | exact tree/policy/value/visit/trace agreement for deterministic predictions; `mcts_policy` with a real network matches |
| Search coordinator checks | 24/24 (ownership, cold-init cancel, competing searches, stale start id, cancel before publication, delayed responses, both worker failures, repeated recovery) |
| Neural client lifecycle checks | 33/33 (drain vs terminate, replacement refusal while draining, load cancellation, idle retention) |
| Staged-source integrity checks | 21/21 |
| Analysis UI checks | 18/18 including lazy assets, cancellation during download/session/worker init, bfcache and navigation restoration with a full neural search, non-default positions, in-flight selector change |
| Phase 1 inference UI / parity at this revision | 19/19 / 39 cases + batches 1–32 |
| Identity-boundary corruption | 4/4 exact `MODEL_IDENTITY_MISMATCH` with control requests accepted |
| Native encoding benchmark | 19.3–26.9 µs per position vs 170.8–416.8 µs previously (exact parity) |

## Revision-2 browser measurements (structural smoke fixture)

| Workload | Browser sims/s | Native sims/s | Equal-work ratio |
| --- | ---: | ---: | ---: |
| Brandubh 128 + 256 | 451.6 | 3,255.1 | 0.139 |
| Brandubh 512 + 1,024 | 525.8 | 3,200.3 | 0.161 |
| Tablut 128 | 329.8 | 2,000.0 | 0.164 |
| Copenhagen 128 | 210.0 | 990.4 | 0.210 |

1,024 fixed simulations complete in 1.93–1.94 s (532–528 simulations/s) for the
smoke fixture. Fixed-time medians: Brandubh 306 (0.5 s/1 s) and 1,824 (2 s/5 s),
Tablut 186, Copenhagen 119 completed simulations; deadline overshoot median
8.5–20 ms, max 22 ms. Timing shares at 512–1,024 simulations: 35.8 % coordinator
step boundary, 37.3 % neural wait (203 ms of it ORT execution, 18 ms tensor
preparation), 24.6 % Python search work, 0.6 % unattributed. The measured
bottlenecks are unchanged, so Phase 3 priorities remain.

Cold start at revision 2: page scripts ready in 71 ms; search worker + ORT +
model ready in 2.93 s. Pyodide search-worker WASM heap 20 MiB; neural worker heap
still unavailable; JS heap 10 MB (Chromium hint).

## Revision-2 evidence files

- Parity: [Level A native](./evidence/mcts-parity-native-a-r2.json) ·
  [Level A browser](./evidence/mcts-parity-browser-a-r2.json) ·
  brandubh [native ONNX](./evidence/mcts-parity-native-b-brandubh-r2.json) /
  [browser](./evidence/mcts-parity-browser-b-brandubh-r2.json) /
  [native PyTorch](./evidence/mcts-parity-native-b-brandubh-torch-r2.json) /
  [PyTorch↔browser](./evidence/mcts-parity-compare-torch-browser-brandubh-r2.json) ·
  tablut [native](./evidence/mcts-parity-native-b-tablut-r2.json) /
  [browser](./evidence/mcts-parity-browser-b-tablut-r2.json) ·
  copenhagen [native](./evidence/mcts-parity-native-b-copenhagen-r2.json) /
  [browser](./evidence/mcts-parity-browser-b-copenhagen-r2.json)
- Benchmarks: [native quick](./evidence/mcts-benchmark-native-quick-r2.json) /
  [browser quick](./evidence/mcts-benchmark-browser-quick-r2.json) ·
  [native strong](./evidence/mcts-benchmark-native-strong-r2.json) /
  [browser strong](./evidence/mcts-benchmark-browser-strong-r2.json) ·
  tablut [native](./evidence/mcts-benchmark-native-tablut-r2.json) /
  [browser](./evidence/mcts-benchmark-browser-tablut-r2.json) ·
  copenhagen [native](./evidence/mcts-benchmark-native-copenhagen-r2.json) /
  [browser](./evidence/mcts-benchmark-browser-copenhagen-r2.json)
- Lifecycle and UI: [analysis UI](./evidence/mcts-browser-ui-r2.json) ·
  [search coordinator](./evidence/mcts-search-lifecycle-r2.txt) ·
  [neural client](./evidence/neural-client-lifecycle-r2.txt) ·
  [staged-source integrity](./evidence/search-assets-integrity-r2.txt)
- Encoding: [native encoder benchmark](./evidence/encoding-native-benchmark-r2.json)
- Phase 1 re-verification: [inference parity](./evidence/neural-browser-parity-wasm-r7.json) ·
  [inference benchmark](./evidence/neural-browser-wasm-benchmark-r7.json) ·
  [inference UI](./evidence/neural-browser-ui-r7.json) ·
  [identity boundaries](./evidence/neural-browser-identity-boundaries-r7.json)

## Revision-2 reproduction additions

```sh
# independent production-MCTS reference and strict comparators
.venv/bin/python -m pytest tests/test_mcts_reference.py tests/test_mcts_benchmark.py
node spikes/browser_offline/test_search_lifecycle.mjs
node spikes/browser_offline/test_search_assets.mjs
node spikes/browser_offline/test_browser_lifecycle.mjs

# native encoding performance comparison (exact parity asserted in the tool)
.venv/bin/python spikes/browser_offline/encoding_benchmark.py --repetitions 5

# cancellation/restoration/lazy-asset UI gates (needs a staged spike)
SPIKE_NEURAL_DIR=/tmp/neural-spike .venv/bin/python -m pytest \
  tests/test_browser_mcts_validation.py -q
```

## Remaining external gates (unchanged)

Competitive trained-model correctness/throughput/memory, physical WebGPU
(`--require-webgpu`; per-operator GPU execution still unverifiable), physical
desktop/mobile and Safari/Firefox behaviour, and playing-strength validation
remain external acceptance gates. They are not satisfied by revision 2.

---

# Revision 3 — final review corrections

Revision 3 integrates `origin/main` `3074790` and closes the remaining review
findings on `2d627a3`. The architecture is unchanged: the Python `mcts_context()`
search runs in the Pyodide worker with the persistent ORT Web inference worker.

## Corrections delivered

1. **Semantic validation of analysis results.** The comparator now requires exact
   defender-relative terminal values (−1 attacker win, +1 defender win, 0 draw),
   a best move for every successful non-terminal search, membership of that move
   in the root visit distribution with the maximum share, consistency between
   `analysis.best_move`, `search.best_move` and the output, and recomputes the
   full search-position identity from the serialized state (including repetition
   history), effective settings, model identity, algorithm/driver version and
   encoder version. The independent per-record `path` association is checked when
   present. Terminal positions must report no move, no visits and no inference;
   immediate wins must keep their shortcut semantics.
2. **Versioned workload source contract.** Reports declare
   `workload.schema = 1`, an explicit source (`fixtures` v1 with a validated
   selection, or `custom` v1 with the position-file digest), the exact ordered
   positions, `expected_names` and a canonical `positions_sha256`. Built-in
   workloads are reconstructed from `fixtures.jarl_positions` including exact
   boards and repetition history; custom files are validated by content and digest,
   and the browser harness consumes precisely the declared workload. The
   comparator cross-checks both reports and, for custom workloads, verifies the
   original `--positions-file`. Malformed workload metadata produces a structured
   failed comparison (nonzero exit, no traceback).
3. **Abandoned neural-request errors.** A late response to a cancelled inference
   is now interpreted: success completes the drain and keeps a healthy session;
   `RESOURCE_CLEANUP_FAILED`/`SESSION_RELEASE_FAILED` invalidate the session and
   replace the worker; an ordinary inference error drains with an explicit failure
   outcome; worker/transport failures and drain timeouts terminate; a late message
   from an obsolete generation is ignored. `sessionRetained` is only true when
   cleanup is established.
4. **UI replacement admission waits for cleanup.** Cancel gives immediate feedback
   and immediately removes result-publication authority, while the bounded cleanup
   (search coordinator + neural client drain/termination) is tracked and awaited by
   the next admission. Raw inference and neural MCTS share one ownership policy
   (incompatible controls disabled, direct calls fail explicitly); both Cancel
   controls have clear enabled states. A healthy drain reuses the model session and
   search worker; an unhealthy drain recreates them. No busy-worker error is
   exposed and no page reload is needed.

## Revision-3 validation evidence

| Gate | Result |
| --- | --- |
| Level A (all rulesets, 21 positions, 128 simulations) | 21/21 exact; workload complete; acceptance true |
| Level B Brandubh 256 / Tablut 128 / Copenhagen 128 | 7/7 best moves each; acceptance true |
| Browser WASM vs native PyTorch (Brandubh 256) | 7/7 best moves; acceptance true |
| Custom-position Level A (3 owner-style positions, incl. repetition history) | 3/3 exact; source `custom` v1 with verified file digest; acceptance true |
| Semantic negative tests (terminal signs, missing/illegal/inconsistent best move, identity/history/model tampering) | every corruption fails for its intended reason |
| Workload contract tests (fixtures/custom, duplicates, malformed metadata, missing cases, changed digests) | pass; CLI failure is structured |
| Neural client checks | 38/38 including abandoned success/cleanup/session-release/generic error/obsolete response |
| Search coordinator checks | 24/24 |
| Staged-source integrity | 21/21 |
| Analysis UI checks | 23/23 including immediate restart after cancelling download, ORT init, cold Pyodide init and a blocked leaf inference, repeated cancel/restart, unhealthy-drain recreation, raw/analysis mutual exclusion and cancel control states |
| Phase 1 inference UI / parity at this revision | 19/19 / 39 cases + batches 1–32 |
| Identity-boundary corruption | 4/4 exact codes with accepted controls |

## Revision-3 browser measurements (structural smoke fixture)

| Workload | Browser sims/s | Native sims/s | Equal-work ratio |
| --- | ---: | ---: | ---: |
| Brandubh 128 + 256 | 415.6 | 3,032.1 | 0.140 |
| Brandubh 512 + 1,024 | 518.5 | 3,196.1 | 0.166 |
| Tablut 128 | 278.6 | 2,071.6 | 0.136 |
| Copenhagen 128 | 241.8 | 1,064.4 | 0.227 |

1,024 fixed simulations complete in 1.85–1.94 s (528–552 simulations/s) for the
smoke fixture. Fixed-time medians: Brandubh 405 (0.5/1 s) and 1,825 (2/5 s),
Tablut 160, Copenhagen 122 completed simulations; deadline overshoot median
7.5–16.5 ms, max 18 ms. Timing shares at 512–1,024 simulations: 36.5 %
coordinator step boundary, 37.9 % neural wait (203 ms ORT execution), 25.1 %
Python search work, 0.6 % unattributed. Cold start: 71 ms page scripts, 3.18 s
worker + ORT + model. These are tiny-model baselines, not competitive-model or
physical-device claims.

## Revision-3 evidence files

- Parity: [Level A native](./evidence/mcts-parity-native-a-r3.json) /
  [browser](./evidence/mcts-parity-browser-a-r3.json); custom
  [native](./evidence/mcts-parity-native-a-custom-r3.json) /
  [browser](./evidence/mcts-parity-browser-a-custom-r3.json); brandubh
  [native ONNX](./evidence/mcts-parity-native-b-brandubh-r3.json) /
  [browser](./evidence/mcts-parity-browser-b-brandubh-r3.json) /
  [native PyTorch](./evidence/mcts-parity-native-b-brandubh-torch-r3.json) /
  [comparison](./evidence/mcts-parity-compare-torch-browser-brandubh-r3.json); tablut
  [native](./evidence/mcts-parity-native-b-tablut-r3.json) /
  [browser](./evidence/mcts-parity-browser-b-tablut-r3.json); copenhagen
  [native](./evidence/mcts-parity-native-b-copenhagen-r3.json) /
  [browser](./evidence/mcts-parity-browser-b-copenhagen-r3.json)
- Benchmarks: [native quick](./evidence/mcts-benchmark-native-quick-r3.json) /
  [browser quick](./evidence/mcts-benchmark-browser-quick-r3.json) ·
  [native strong](./evidence/mcts-benchmark-native-strong-r3.json) /
  [browser strong](./evidence/mcts-benchmark-browser-strong-r3.json) · tablut
  [native](./evidence/mcts-benchmark-native-tablut-r3.json) /
  [browser](./evidence/mcts-benchmark-browser-tablut-r3.json) · copenhagen
  [native](./evidence/mcts-benchmark-native-copenhagen-r3.json) /
  [browser](./evidence/mcts-benchmark-browser-copenhagen-r3.json)
- Lifecycle and UI: [analysis UI](./evidence/mcts-browser-ui-r3.json) ·
  [search coordinator](./evidence/mcts-search-lifecycle-r3.txt) ·
  [neural client](./evidence/neural-client-lifecycle-r3.txt) ·
  [staged-source integrity](./evidence/search-assets-integrity-r3.txt)
- Phase 1 re-verification: [inference parity](./evidence/neural-browser-parity-wasm-r8.json) ·
  [inference benchmark](./evidence/neural-browser-wasm-benchmark-r8.json) ·
  [inference UI](./evidence/neural-browser-ui-r8.json) ·
  [identity boundaries](./evidence/neural-browser-identity-boundaries-r8.json)
- Revision-1/2 evidence remains unchanged; the encoding benchmark from revision 2
  (`encoding-native-benchmark-r2.json`) is unchanged because the encoder source did
  not change.

## Revision-3 reproduction additions

```sh
# Semantic verification and workload contracts
.venv/bin/python -m pytest tests/test_mcts_workload.py tests/test_mcts_benchmark.py

# Custom-position parity (native first, then browser, with file verification)
.venv/bin/python spikes/browser_offline/native_mcts_parity.py --level a \
  --positions /path/to/positions.json --rulesets brandubh,tablut --output /tmp/native-custom.json
node spikes/browser_offline/mcts_parity.mjs /tmp/neural-spike \
  --native /tmp/native-custom.json --level a --positions-file /path/to/positions.json \
  --output /tmp/browser-custom.json

# Abandoned-inference and immediate-restart lifecycle
node spikes/browser_offline/test_browser_lifecycle.mjs
node spikes/browser_offline/test_mcts_ui.mjs /tmp/neural-spike --output /tmp/mcts-ui.json
```

## Remaining external gates (unchanged)

Competitive trained models, physical WebGPU execution, physical desktop/mobile
and Safari/Firefox, sustained process-memory stability and playing strength remain
external acceptance gates. They are not satisfied by revision 3. Frozen native
MCTS reference failures still reproduce identically on pristine `main` (verified by
extraction) and remain documented, not skipped.

---

# Revision 4 — focused final corrections

Revision 4 integrates `origin/main` `8a11ee6` (release 0.41.1, deployment fixture
timing) and closes the last review findings on `56b77a8`. No architecture,
batching, production integration or search-semantics change was made.

## Corrections delivered

1. **Fresh-page analysis initialization.** The experimental interface initializes
   the small shared position dataset itself and populates the initial ruleset.
   Start Analysis stays disabled until a valid position exists; loading and failure
   states are explicit; a `Reload positions` control retries a failed download.
   Refreshes preserve the current selection and stale responses cannot overwrite a
   newer dataset (request generations). Admission captures the exact position,
   ruleset, agent, provider and settings after initialization, with selectors
   disabled by ownership. Neural model weights, ORT, reference vectors and workers
   remain lazy, and Jarl still runs with every neural-only asset unavailable.
2. **Independent fixed-work completion for both runtimes.** Each result is
   classified per runtime as complete, terminal, immediate-win, deadline-limited,
   incomplete or inconsistent. Fixed-work acceptance requires the native **and**
   browser workloads to complete (terminal/immediate-win excepted), reports native
   and browser incomplete/deadline cases separately, and keeps overall acceptance
   and workload-completion status consistent. Unequal fixed-time work remains an
   informative comparison, never an equal-work measurement.
3. **Cascading supersession ownership.** Pending worker requests and cancellation
   acknowledgements are bound to their search owner; only the owning search's
   pending request may be rejected. A cleanup barrier accumulates all outstanding
   cancellation/drain dependencies, so a newest admission waits for every older
   cleanup rather than only the previous search. An unhealthy worker is replaced by
   its newer owner (never terminated by older cleanup), with bounded recovery when
   acknowledgements fail. The implicit `model` fallback in `search()` was removed:
   every neural search must supply the identity captured at workload preparation.
4. **Real pending-inference browser coverage.** A bounded experimental test seam
   (`test_delay_ms`, 0–5000 ms) delays a genuine worker inference. Browser tests
   cancel with a real request outstanding and immediately restart: a healthy drain
   retains the model session/worker, while an unresponsive inference is replaced
   before the replacement runs; obsolete results/errors cannot affect it.
5. **Exception-safe parity CLI.** Input loading, JSON parsing, custom-file loading
   and comparison are all protected. Every failure produces a complete structured
   report (input/workload/execution classification, `acceptance: false`,
   `workload_completion.complete: false`) and a useful nonzero exit without a second
   traceback; malformed input exits 2, comparison failures exit 1.
6. **Complete custom-workload source verification.** Custom sources now distinguish
   file-backed (`custom`, mandatory exact file SHA-256) from intentionally embedded
   (`embedded`, no file provenance). Source schema version and digest format are
   validated; the comparator receives and verifies the original file digest; the
   browser harness requires and verifies the declared digest and compares
   semantically equivalent JSON without depending on property order. Ordered
   position identifiers, normalized position-content digests and full state/history
   remain distinct concepts.

## Revision-4 validation evidence

| Gate | Result |
| --- | --- |
| Level A (21 built-in positions, all rulesets) | 21/21 exact; workload complete; acceptance true |
| Custom Level A (3 owner-style positions incl. repetition history) | 3/3 exact; `custom` v1 with verified file digest; acceptance true |
| Level B Brandubh 256 / Tablut 128 / Copenhagen 128 | 7/7 best moves each; workload complete; acceptance true |
| Browser WASM vs native PyTorch (Brandubh 256) | 7/7; acceptance true |
| Fixed-work completion tests (both complete, either incomplete, both, deadline-limited, terminal/immediate-win, unequal fixed-time) | pass |
| Semantic/corruption tests (terminal values, best-move consistency, identity/history/model) | pass |
| Workload tests (file/embedded sources, wrong/missing/malformed digest, version, property order, both-omitted, empty) | pass |
| CLI failure tests (malformed JSON, missing file, non-object, malformed metadata, unexpected exception) | structured nonzero, no traceback |
| Search coordinator lifecycle (cascading admissions, delayed ack/drain, ack failure, drain failure, missing model identity) | 29/29 |
| Neural client lifecycle (incl. abandoned outcomes) | 38/38 |
| Staged-source integrity | 21/21 |
| Analysis UI (fresh-page Jarl/neural via normal controls, ruleset/non-default position, initial data failure recovery, selection preservation, real pending-inference cancellation/restart, bfcache/navigation) | 30/30 |
| Phase 1 inference parity / UI / identity boundaries at this revision | 39 cases + batches / 19/19 / 4/4 exact |

## Revision-4 browser measurements (tiny smoke fixture only)

| Workload | Browser sims/s | Native sims/s | Equal-work ratio |
| --- | ---: | ---: | ---: |
| Brandubh 128 + 256 | 423.0 | 3,285.7 | 0.134 |
| Brandubh 512 + 1,024 | 556.6 | 3,102.3 | 0.181 |
| Tablut 128 | 324.7 | 2,042.0 | 0.158 |
| Copenhagen 128 | 222.4 | 922.3 | 0.243 |

1,024 fixed simulations complete in 1.74–1.94 s (527–590 simulations/s) for the
smoke fixture. Fixed-time medians: 390 (Brandubh 0.5/1 s), 1,390 (2/5 s), 187
(Tablut), 126 (Copenhagen); overshoot median 8–13 ms, max 15 ms. Timing shares at
512–1,024 simulations: 36.1 % step boundary, 37.7 % neural wait, 25.2 % Python
search, 0.5 % unattributed. These small single-repetition differences versus
revision 3 are uncontrolled noise and are not claimed as a speedup or regression.

## Revision-4 evidence files

- Parity: [Level A native](./evidence/mcts-parity-native-a-r4.json) /
  [browser](./evidence/mcts-parity-browser-a-r4.json); custom
  [native](./evidence/mcts-parity-native-a-custom-r4.json) /
  [browser](./evidence/mcts-parity-browser-a-custom-r4.json); brandubh
  [native ONNX](./evidence/mcts-parity-native-b-brandubh-r4.json) /
  [browser](./evidence/mcts-parity-browser-b-brandubh-r4.json) /
  [native PyTorch](./evidence/mcts-parity-native-b-brandubh-torch-r4.json) /
  [comparison](./evidence/mcts-parity-compare-torch-browser-brandubh-r4.json); tablut
  [native](./evidence/mcts-parity-native-b-tablut-r4.json) /
  [browser](./evidence/mcts-parity-browser-b-tablut-r4.json); copenhagen
  [native](./evidence/mcts-parity-native-b-copenhagen-r4.json) /
  [browser](./evidence/mcts-parity-browser-b-copenhagen-r4.json)
- Benchmarks: [native quick](./evidence/mcts-benchmark-native-quick-r4.json) /
  [browser quick](./evidence/mcts-benchmark-browser-quick-r4.json) ·
  [native strong](./evidence/mcts-benchmark-native-strong-r4.json) /
  [browser strong](./evidence/mcts-benchmark-browser-strong-r4.json) · tablut
  [native](./evidence/mcts-benchmark-native-tablut-r4.json) /
  [browser](./evidence/mcts-benchmark-browser-tablut-r4.json) · copenhagen
  [native](./evidence/mcts-benchmark-native-copenhagen-r4.json) /
  [browser](./evidence/mcts-benchmark-browser-copenhagen-r4.json)
- Lifecycle/UI: [analysis UI](./evidence/mcts-browser-ui-r4.json) ·
  [search coordinator](./evidence/mcts-search-lifecycle-r4.txt) ·
  [neural client](./evidence/neural-client-lifecycle-r4.txt) ·
  [staged-source integrity](./evidence/search-assets-integrity-r4.txt)
- Phase 1 re-verification: [inference parity](./evidence/neural-browser-parity-wasm-r9.json) ·
  [inference benchmark](./evidence/neural-browser-wasm-benchmark-r9.json) ·
  [inference UI](./evidence/neural-browser-ui-r9.json) ·
  [identity boundaries](./evidence/neural-browser-identity-boundaries-r9.json)
- Revisions 1–3 evidence is unchanged; the revision-2 encoding benchmark remains
  current because the encoder source did not change.

## Revision-4 reproduction additions

```sh
# Fresh-page UI, cascading cleanup, pending inference and CLI failure gates
SPIKE_NEURAL_DIR=/tmp/neural-spike .venv/bin/python -m pytest \
  tests/test_browser_mcts_validation.py tests/test_browser_neural_ui.py -q
node spikes/browser_offline/test_search_lifecycle.mjs
node spikes/browser_offline/test_browser_lifecycle.mjs
.venv/bin/python -m pytest tests/test_mcts_workload.py tests/test_mcts_benchmark.py
```

## Remaining external gates (unchanged)

Competitive trained models, physical WebGPU execution, physical desktop/mobile and
Safari/Firefox, sustained process-memory stability and playing strength remain
external acceptance gates. Revision 4 does not claim them. Frozen native MCTS
reference failures still reproduce identically on pristine `main` and remain
documented, not skipped.
