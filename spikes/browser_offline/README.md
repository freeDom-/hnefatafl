# Browser-offline feasibility spike

[Documentation index](../../docs/README.md) · [Phase 0 design](../../docs/designs/online-phase0.md) ·
[Validation](../../docs/validation/online-phase0.md) ·
[Measured browser evidence](../../docs/validation/browser-offline-phase0.json)

> Phase 1 (full-strength Jarl and trained-model browser inference) builds on this
> prototype. See the
> [browser-side analysis roadmap](../../docs/designs/browser-analysis-roadmap.md) and
> [Phase 1 validation](../../docs/validation/browser-analysis-phase1.md).

Isolated static experiment: unchanged Python engine and baseline AI in a Pyodide
worker. No production routes, account bypass, application build changes, database,
new production dependency or downloaded models. Reusing Python proved feasible;
there is no evidence requiring a separately maintained JavaScript rules engine.
Such a rewrite would duplicate all three rulesets, repetition history and variant
outcomes, and require permanent differential tests on every engine change.

## Current-engine analysis extension

The current worker also exposes the existing classical analysis API, exact-position
cache, bounded scheduling and termination-based cancellation. See the
[current-engine feasibility report](../../docs/validation/browser-analysis-prototype.md)
for fresh measurements, native/browser differential tests and production gates.
The Phase 0 measurements and limitations below describe the original baseline-only
experiment; they are retained as historical evidence, not new validation.

### Phase 1: full-strength Jarl and neural inference

The worker exposes the production-equivalent Jarl preset (depth 5, 2 s, Shield
maiden, optimized search) and named benchmark workloads
(quick/interactive/standard/deep/extended) through a shared `jarl_benchmark.py`
runner used identically by native CPython and Pyodide. `jarl_compare.py` is the
single comparison implementation behind both the browser verifier and the
standalone comparator.

Corrected contract (revision 3):

- Every measured **repetition** is validated: record keys include the repetition
  index, duplicates are rejected, and expected coverage is derived independently
  from the workload cases × configurations × declared repetitions. Both reports
  omitting the same position, empty reports and mismatched repetition counts fail.
- Record-level position hash, ruleset, mode, seed and effective-settings digest
  must match the workload. The settings digest is numeric-normalised so it
  survives the Python/JavaScript JSON boundary.
- **Execution validity**, **parity at equal completed work** and **acceptance
  completion** are reported separately. A finite value, a legal workload move and
  the correct defender-relative terminal value are mandatory; parity is compared
  only when completed depth, interruption state and available node/evaluation
  counters agree. Asymmetric or limited cases are listed as unresolved acceptance
  targets, not regressions.
- Fixed-time differences remain informational; terminal, unsupported, failed and
  non-equivalent cases are excluded from equal-work latency ratios.
- Each benchmark case is an independently bounded request (75 s), so a later
  failure cannot discard earlier records. Ordinary analysis keeps a 5 s watchdog;
  a legitimate cooperative deadline up to 10 s derives a watchdog up to 20 s.

```sh
.venv/bin/python spikes/browser_offline/native_jarl_benchmark.py \
  --mode fixed_work --suite quick --warmup 1 --repetitions 2 --output /tmp/jarl-native.json
.venv/bin/python spikes/browser_offline/prepare.py --output /tmp/hnefatafl-analysis
node spikes/browser_offline/verify_jarl.mjs /tmp/hnefatafl-analysis \
  --mode fixed_work --suite quick --warmup 1 --reps 2 \
  --native /tmp/jarl-native.json --output /tmp/jarl-browser.json
.venv/bin/python spikes/browser_offline/compare_jarl_benchmarks.py \
  /tmp/jarl-native.json /tmp/jarl-browser.json --output /tmp/jarl-comparison.json
```

The neural side stages a *validated* export only: the ONNX graph, metadata,
reference vectors and parity report are bound by digests, and a forced replacement
invalidates the old marker before publishing. Browser parity validates the complete
inference response the runtime actually returned (model/ruleset and request/position
identity, logits, value, legal indices/order, probabilities, shapes, dtypes) and
independently verifies batch count, result count, per-result index and position
association. Missing response fields fail; deliberate corruption kinds must fail.

Inference requests are bound to the exact model they were prepared for: callers
capture `client.modelIdentity()` (digest, ruleset, size) when preparing a workload,
and both the client and worker reject a stale or mismatched identity before
execution. Loading requires a supported ruleset, and batch requests carry model
metadata once at batch level. Reference vectors are written as compact JSON so
Copenhagen remains within the unchanged 16 MiB experimental budget.

```sh
uv pip install --python .venv/bin/python -r spikes/browser_offline/requirements-onnx.txt
.venv/bin/python spikes/browser_offline/onnx_export.py /path/to/last.pt /tmp/model-export
.venv/bin/python spikes/browser_offline/prepare_neural.py \
  --model-dir /tmp/model-export --output /tmp/neural-spike
node spikes/browser_offline/neural_parity.mjs /tmp/neural-spike \
  --provider wasm --output /tmp/neural-parity-wasm.json
node spikes/browser_offline/neural_parity.mjs /tmp/neural-spike \
  --provider wasm --corrupt reorder --output /tmp/neural-corrupt.json   # must fail
node spikes/browser_offline/neural_parity.mjs /tmp/neural-spike \
  --provider wasm --corrupt request-model-worker --output /tmp/neural-identity.json
node spikes/browser_offline/neural_benchmark.mjs /tmp/neural-spike \
  --provider wasm --sustained 200 --output /tmp/neural-wasm.json
node spikes/browser_offline/test_neural_ui.mjs /tmp/neural-spike \
  --output /tmp/neural-ui.json
node spikes/browser_offline/test_neural_identity.mjs
node spikes/browser_offline/test_neural_tensors.mjs
node spikes/browser_offline/neural_parity.mjs /tmp/neural-spike \
  --provider webgpu --require-webgpu
node spikes/browser_offline/neural_parity.mjs /tmp/neural-spike \
  --provider webgpu --require-gpu-execution   # fails without a placement API
node spikes/browser_offline/test_browser_lifecycle.mjs
```

`--require-webgpu` requires an obtained adapter, a created session, a successful
inference and an observed placement of `webgpu-session-verified` (WebGPU providers
only). A session created for WebGPU without observable per-node placement is
reported as `webgpu-session-unverified`; a mixed list is `webgpu-mixed`; WASM-only
is `cpu-fallback`. No WASM fallback is ever labelled GPU.

See the [roadmap](../../docs/designs/browser-analysis-roadmap.md) and
[Phase 1 validation](../../docs/validation/browser-analysis-phase1.md).

### Lifecycle and scheduling corrections

Displayed analysis is bound to the exact position and settings that produced it.
Starting a new game, restoring a position, making a human move or completing an AI
move clears the analysis pane and invalidates any in-flight analysis render, so a
superseded result can never publish. Cache reuse still applies when the identical
position, repetition history and settings are analyzed again.

Foreground gameplay preempts background analysis: a human move, AI move, new game
or restore cancels the active analysis through the existing generation guard and
terminates/recreates the worker. Ordinary analysis cache hits never restart the
worker, and a synchronous `postMessage` failure clears the pending timer, request
state and busy flag. Expected `AbortError` cancellation is reported as a cancelled
state rather than a generic spike error, and position-dependent controls stay
disabled until the first position is rendered.

The 512 KiB request limit is enforced on the exact serialized payload in UTF-8
bytes on both sides of the worker boundary; the Python `dispatch` check remains an
independent safety boundary. Multibyte payloads whose UTF-16 length is within the
limit but whose UTF-8 encoding exceeds it are rejected.

### Second review corrections

Offline service-worker registration is independent of the cancellable initial
game. Cancelling or replacing that game cannot prevent registration, a failed or
cancelled attempt can be retried, duplicate registrations are avoided by a cached
startup promise, installation failure or a stalled activation is surfaced
explicitly, and the scope stays the isolated spike directory. The startup promise
always settles.

A cache hit is treated as the newest request: it cancels any slower in-flight
search instead of leaving it running, while an idle worker is never restarted for
an ordinary cache hit. Manual Cancel clears the analyzing indicator immediately and
is idempotent. Cancellation (`AbortError`), hard timeout (`TimeoutError`), worker
failure and supersession now produce distinct feedback, and obsolete handlers
cannot overwrite newer status. A synchronous `postMessage` failure resets the
transport so the next request creates a fresh worker. Terminal positions disable
Move and AI selection (analysis remains available), invalid or empty moves are
rejected, and playable positions restore the controls.

### Third review corrections

Foreground gameplay has explicit priority over background analysis. The client
tracks whether the active worker job is foreground; a background request or cached
result never terminates a foreground New Game, Restore, Move or AI operation.
Foreground work still preempts background analysis, multiple background analyses
retain newest-request-wins behavior, and a background request that cannot preempt
the foreground is discarded with `ForegroundPriorityError` instead of disturbing
the current position or status.

Fixture loading is wrapped in the same foreground ownership: a delayed fetch that
is superseded by a newer game returns without starting worker work or overwriting
the status, and manual Cancel invalidates it. An active analysis failure now clears
the `Analyzing…` indicator before reporting the error, and cancellation with no
active work is a no-op that preserves the healthy idle worker.

### Final review corrections

Worker error handling uses the same active-worker identity check as worker
messages. A delayed `error` event from a cancelled, replaced or terminated worker
is ignored, so it cannot abort the replacement worker or reject an unrelated
request; an error from the currently active worker still rejects the pending
promise, clears its timer and resets the transport for the next request. The
branch is also merged with current `main` without touching the staged Python
engine closure or the production application.

## Reproduce

From the repository root, with existing Python/Node development dependencies:

```sh
.venv/bin/python spikes/browser_offline/prepare.py
node spikes/browser_offline/verify.mjs
```

The first command downloads only five pinned Pyodide 0.27.7 core assets from
`https://cdn.jsdelivr.net/pyodide/v0.27.7/full/`, stages static sources and generates
CPython reference fixtures under ignored `work/phase0/offline/`. The verifier starts
its own ephemeral loopback static server and uses existing frontend Playwright.
Install its Chromium with `frontend/node_modules/.bin/playwright install chromium`
if needed, or select an installed browser as actually tested here:

```sh
SPIKE_CHROMIUM=/usr/bin/chromium node spikes/browser_offline/verify.mjs
```

To reuse previously downloaded assets without another network setup step:

```sh
.venv/bin/python spikes/browser_offline/prepare.py \
  --runtime-dir work/phase0/offline/runtime --output work/phase0/measured
SPIKE_CHROMIUM=/usr/bin/chromium node spikes/browser_offline/verify.mjs \
  work/phase0/measured
```

To interact manually, keep a static server running and open
`http://127.0.0.1:8765/`. Only this temporary static server is needed for initial
browser installation/caching; no FastAPI server is used:

```sh
.venv/bin/python -m http.server 8765 --bind 127.0.0.1 \
  --directory work/phase0/offline
```

Use a stable origin for manual cache/reload tests. Wait for successful runtime
loading and `window.spike.ready` (includes service-worker activation), then disable
network in browser developer tools and reload. HTTPS or loopback secure context
is required for service workers. In the automated run, `context.setOffline(true)`
disables browser network after installation; the page reload creates a fresh
worker/runtime and all cases run again from the service-worker cache.

Error-path-only check (does **not** establish engine execution):

```sh
.venv/bin/python spikes/browser_offline/prepare.py --shell-only \
  --output work/phase0/shell
SPIKE_CHROMIUM=/usr/bin/chromium node spikes/browser_offline/verify.mjs \
  work/phase0/shell --shell-only
```

The shell-only directory should be fresh, without previously staged runtime files.
It displays a runtime-loading error while the UI response control still works.

## What is executable

- [prepare.py](prepare.py) stages the runtime and byte-for-byte copies of
  `__init__.py`, `engine.py` and `ai.py`. Asset inventory records raw bytes,
  gzip estimates and SHA-256; only downloaded core runtime is needed.
- [fixtures.py](fixtures.py) generates 34 reference cases from native CPython:
  eight opening plies per ruleset, multi-capture, king escape, repetition and a
  Copenhagen shield wall. Each includes full serialized state/repetition history,
  legal moves, selected move and expected next state/legal moves. These are
  representative cross-runtime comparisons, not exhaustive rules verification;
  existing engine tests remain authoritative for complete rule coverage.
- [worker.js](worker.js) loads local runtime assets and Python source, never
  `micropip`, a remote inference service or a model. [bridge.py](bridge.py) exposes
  new game, legal move, local baseline AI, fixture comparison and diagnostic calls.
- [app.js](app.js)/[index.html](index.html) present one in-memory game, legal move
  selection, AI, fixtures and a UI responsiveness control. Starting another game
  replaces it. There is no browsable history or backend connection.
- [sw.js](sw.js) caches the explicit complete asset manifest and serves it offline,
  including navigation. Cache identity depends on bundle contents and old spike
  caches are removed on activation. Its scope is the staged directory on the
  isolated origin, never the production app.
- [verify.mjs](verify.mjs) checks fresh runtime loading, fixtures, human and AI
  moves for each ruleset, a UI click while a synthetic 500 ms Python loop is still
  busy in the worker, and the full fixture/gameplay sequence after offline reload.
  It blocks nonlocal requests and asserts no application API requests occur.

### Physical-device validation (owner hardware)

Privately supplied trained weights must stay local. Do **not** bind a file server
to `0.0.0.0` or open a LAN HTTP address: that can expose the model artifacts and
does not provide the secure context required by WebGPU and Web Crypto.

#### WSL to a native Windows browser (safe loopback)

```sh
# 1. Export and stage in WSL (private checkpoint stays local).
.venv/bin/python spikes/browser_offline/onnx_export.py /path/to/last.pt /tmp/model-export
.venv/bin/python spikes/browser_offline/prepare_neural.py \
  --model-dir /tmp/model-export --output /tmp/neural-spike
# 2. Loopback only. WSL2 normally forwards http://localhost:8765 to Windows.
.venv/bin/python -m http.server 8765 --bind 127.0.0.1 --directory /tmp/neural-spike
```

Open `http://localhost:8765/neural.html` in Windows Chrome/Edge and verify in the
DevTools console:

```js
window.isSecureContext          // must be true
!!crypto.subtle                 // must be true
navigator.gpu                   // may be undefined on unsupported browsers
await navigator.gpu?.requestAdapter()   // null/undefined means no GPU adapter
```

If Windows localhost forwarding is unavailable, use an SSH tunnel or trusted
HTTPS. Do not disable browser security.

```powershell
# From Windows, tunnel the WSL loopback port and open http://localhost:8765.
ssh -N -L 8765:127.0.0.1:8765 <wsl-user>@<wsl-host>
```

For a non-loopback origin, serve through a reverse proxy with a certificate the
Windows browser already trusts (for example a local development CA). HTTP over a
LAN address is not a valid WebGPU/Web Crypto test.

#### Native Windows automated parity and benchmark

Copy the staged directory to a Windows-accessible path (for example
`C:\work\neural-spike`). Use a Windows checkout of this repository, Node.js 22+,
and either the Playwright Chromium or an installed Chrome/Edge.

```powershell
cd C:\path\to\hnefatafl
npm --prefix frontend ci
npx --prefix frontend playwright install chromium   # or use installed Chrome below

# Resolve the Windows Python interpreter explicitly (no Unix .venv path).
$env:SPIKE_PYTHON = (Get-Command py -ErrorAction SilentlyContinue).Source
if (-not $env:SPIKE_PYTHON) { $env:SPIKE_PYTHON = (Get-Command python).Source }
$env:SPIKE_CHROMIUM = "C:\Program Files\Google\Chrome\Application\chrome.exe"
# Edge alternative:
# $env:SPIKE_CHROMIUM = "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

node spikes\browser_offline\neural_parity.mjs C:\work\neural-spike `
  --provider wasm --output C:\work\parity-wasm.json
node spikes\browser_offline\neural_parity.mjs C:\work\neural-spike `
  --provider webgpu --require-webgpu --output C:\work\parity-webgpu.json
node spikes\browser_offline\neural_benchmark.mjs C:\work\neural-spike `
  --provider webgpu --require-webgpu --output C:\work\bench-webgpu.json
node spikes\browser_offline\test_neural_ui.mjs C:\work\neural-spike `
  --output C:\work\ui.json
```

`--require-webgpu` requires a WebGPU-requested session and successful numerical
inference. It does **not** claim per-operator GPU execution.
`--require-gpu-execution` requires independently verified actual GPU execution;
the pinned ORT Web build exposes no per-operator placement API, so it currently
fails explicitly even on GPU hardware. Report `execution_placement` verbatim.

#### Firefox, Safari and physical mobile

The automated harness launches Chromium only. `--provider wasm` selects the
execution provider; it does **not** run the harness on Firefox or Safari.

- Firefox/Safari: open the loopback (or trusted HTTPS) page manually, choose
  `wasm`, and use Capabilities → Load → Infer / Infer distinct batch /
  Benchmark. Copy the `#output` JSON as evidence, or run the harness with
  `SPIKE_CHROMIUM` pointed at a Chromium-based browser only.
- Physical mobile: use trusted HTTPS or a secure tunnel to the loopback origin;
  do not expose the model directory over plain LAN HTTP.

## Actual environment and measurements

2026-10-25: Linux x86_64 cloud container, kernel 6.18.55/glibc 2.41, Intel Xeon
Platinum 8573C, three exposed logical CPUs. Host CPython 3.12.14, Node 24.19.0,
Playwright 1.64.1, system Chromium 154.0.8037.57. Browser runtime is Pyodide 0.27.7
with CPython 3.12.7/Clang 19; no third-party Python package is loaded.

Single measured run, fresh browser context against an uncompressed loopback server:

| Measurement | Result |
| --- | --- |
| Runtime + source + first new-game response | 4883.7 ms |
| Fresh worker after cached offline reload | 4714.2 ms |
| 34 CPython fixtures, before and after network disable | Passed both times |
| UI interaction while worker busy | Passed; synthetic worker loop 500 ms |
| Brandubh baseline AI, setup / offline reload | 36.1 / 9.8 ms |
| Tablut baseline AI, setup / offline reload | 28.1 / 33.0 ms |
| Copenhagen baseline AI, setup / offline reload | 67.4 / 79.5 ms |
| External runtime or application API requests | None |

Startup excludes service-worker installation time; the harness waits for caching
to finish before disabling network. AI timings bracket `choose_move` in the worker,
exclude serialization/IPC/rendering, and use the position after one deterministic
human move. These are individual observations, not percentiles or strength results.

| Core asset | Raw bytes | gzip estimate bytes |
| --- | ---: | ---: |
| pyodide.js | 14913 | 5981 |
| pyodide.asm.js | 1255688 | 235580 |
| pyodide.asm.wasm | 10105545 | 3137600 |
| python_stdlib.zip | 2360737 | 2322662 |
| pyodide-lock.json | 112205 | 25823 |

The measured 14-file staged bundle totals 14060475 raw bytes (13.41 MiB), or
5750583 bytes (5.48 MiB) summing deterministic gzip estimates. Totals exclude the
self-describing `assets.json` manifest and HTTP headers. Gzip estimates use Python
`gzip.compress` default level 9, `mtime=0`, **not measured CDN transfer sizes**;
the test server sends uncompressed assets. Browser cache/Wasm heap usage is larger
and was not profiled. Source/build changes change totals; use the generated inventory
for a new run. Exact asset digests and individual measurements are retained in
the linked JSON evidence.

## Network, packaging, licensing and limitations

Initial preparation needs the CDN through the configured network proxy. No network
dependencies were observed after browser installation/caching; the core runtime's
package loader has a CDN default, but this spike never invokes package installation.
Developer tooling and optional JS source maps are not runtime requirements.
No full Pyodide distribution, training dependencies or model weights are downloaded.
See the pinned upstream [deployment instructions](https://pyodide.org/en/0.27.7/usage/downloading-and-deploying.html)
for the core assets and MIME requirements, and
[worker guidance](https://pyodide.org/en/0.27.7/usage/webworker.html) for the execution boundary.

Pyodide's [license](https://github.com/pyodide/pyodide/blob/0.27.7/LICENSE) and bundled
CPython/third-party notices must be reviewed and shipped when distributing runtime
assets. This spike fetches development assets into ignored `work/`, not a licensed
production redistribution package. There is no top-level repository LICENSE file
at the assessment baseline; clarify the project's distribution license too. Asset
hashes record what was measured; production packaging must pin/verify trusted
upstream artifacts, include notices and use a deliberate runtime/security update
policy rather than blindly copying the development downloader.

The supported strategy is the existing heuristic `baseline`, seed 7, time limit
0.1 seconds. Its cooperative budget is checked between candidate moves, so one
candidate's work can overrun the nominal budget. No neural parity, strong AI,
full-game strength, alpha-beta/MCTS/Shield maiden performance, physical mobile,
Safari/Firefox, storage quotas, memory pressure, eviction or flaky-install recovery
has been validated. A production implementation should bound/cancel jobs by
terminating/recreating a worker, and design UI states around the measured startup.

Reload restores cached code, **not the last game** in this minimal in-memory spike.
Production single-slot persistence/replacement UX, replay, accessibility,
installation, cache upgrade/rollback/failure handling and CSP integration remain
work. The existing production CSP/auth shell cannot simply serve these assets
without a separate reviewed integration. Approve the design's retention policy first.

Recommendation: continue with Python/Pyodide reuse plus bounded heuristic AI for
the next offline prototype. Budget for a roughly 14 MB raw installation and
multi-second startup, then measure physical phones and low-memory devices before
choosing production defaults. Reconsider a second engine only if those measured
constraints justify its ongoing correctness and maintenance cost.
