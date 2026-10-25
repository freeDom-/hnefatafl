# Browser-offline feasibility spike

[Documentation index](../../docs/README.md) · [Phase 0 design](../../docs/designs/online-phase0.md) ·
[Validation](../../docs/validation/online-phase0.md) ·
[Measured browser evidence](../../docs/validation/browser-offline-phase0.json)

Isolated static experiment: unchanged Python engine and baseline AI in a Pyodide
worker. No production routes, account bypass, application build changes, database,
new production dependency or downloaded models. Reusing Python proved feasible;
there is no evidence requiring a separately maintained JavaScript rules engine.
Such a rewrite would duplicate all three rulesets, repetition history and variant
outcomes, and require permanent differential tests on every engine change.

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
