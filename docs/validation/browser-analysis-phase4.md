# Phase 4 — Production browser-analysis security and runtime integration

[Roadmap](../designs/browser-analysis-roadmap.md) · [Architecture](../../ARCHITECTURE.md) ·
[Deployment](../DEPLOYMENT.md) · [Security](../SECURITY.md) ·
[Phase 3C-B](browser-analysis-phase3c-b.md) · [Phase 4 browser evidence](browser-analysis-phase4-browser.json) ·
[Phase 4 neural browser evidence](browser-analysis-phase4-browser-neural.json)

Status: **software foundation implemented and structurally validated**. Browser
analysis remains **disabled by default**. The outstanding Phase 3C-B external
gates (competitive checkpoints, playing strength, physical devices, verified GPU
execution, sustained-memory acceptance, Copenhagen depth-5) are **not** resolved
by this phase and remain mandatory for a production rollout.

This report distinguishes implemented, structurally validated, measured,
partially validated, externally blocked and deferred work. It makes no claim of
competitive playing strength, device compatibility or full offline operation.

## 1. Scope and architecture

Phase 4 integrates the validated Phase 0–3C-B browser runtime into the production
application without introducing a second search engine, a new authentication
system or a player-facing analysis UI.

| Boundary | Owner |
| --- | --- |
| Runtime distribution, pins, manifests, verification, activation | `hnefatafl/browser_runtime.py`, `hnefatafl/browser_pins.py` |
| Production Pyodide bridges (no benchmark/test actions) | `hnefatafl/browser_bridge.py`, `hnefatafl/browser_search_bridge.py`, `hnefatafl/browser_engine_identity.py` |
| Canonical ONNX export-artifact validation | `hnefatafl/onnx_artifacts.py` (shared with the spike staging tool) |
| Model publication, revocation, audit trail | `hnefatafl/browser_models.py` |
| Delivery endpoints, CSP, disabled-state policy | `hnefatafl/server.py`, `hnefatafl/auth.py` |
| Browser storage, downloads, capability detection, lifecycle | `frontend/src/browserAnalysis/` (lazy chunk) |
| Phase 5 integration contract | `frontend/src/browserAnalysis/entry.ts` |

The Python engine, position representation, repetition history, move
generation/validation, alpha-beta search, neural MCTS, encoder and
policy/value interpretation remain the authoritative implementations. The
browser runtime executes those same modules; no JavaScript/Rust/C++ search
engine was added. The browser worker/client JavaScript is distributed verbatim
from the validated spike (one implementation), while the spike's benchmark,
parity, fixture and test-worker capabilities are deliberately excluded from
production artifacts.

## 2. Runtime distribution and trust model

- Pinned runtime: Pyodide **0.27.7** and ONNX Runtime Web **1.22.0** with
  recorded SHA-256 digests for every required asset
  (`hnefatafl/browser_pins.py`). Staging fails closed on any digest mismatch,
  missing file or oversized asset.
- Layout: `<data>/browser_runtime/<runtime_id>/` plus an atomic `current`
  symlink. The directory is outside the release tree, so upgrades, rollbacks and
  restores do not move it.
- Deterministic manifest (`runtime-manifest.json`): schema, 16-hex content-derived
  `runtime_id`, pinned versions, engine/encoder/driver/worker-protocol identity,
  application version, per-file size/SHA-256, total size and limits. No
  timestamps; identical inputs produce identical IDs.
- Verification covers the **complete staged tree**: every manifest entry's size
  and digest, rejection of unexpected files and symlinks, protocol/encoder
  compatibility, and a staleness check against the running application version.
  A runtime staged for another release cannot activate silently.
- Serving is an exact allowlist: only paths in the verified manifest are served,
  under `/browser-analysis/runtime/<runtime_id>/…`, from the application origin,
  with `Cache-Control: public, max-age=31536000, immutable` and
  `Cross-Origin-Resource-Policy: same-origin`. No CDN is used at runtime.
- Activation: `build` stages into a temporary directory, verifies, renames
  atomically and only then swaps `current`; a failed build leaves no active
  runtime. `prune` retains the active runtime and keeps rollback possible.
- Licensing: the runtime distribution includes the MPL-2.0 Pyodide notice, the
  MIT ONNX Runtime notice and the PSF Python license under `licenses/`, covered by
  the same manifest digests.

Measured runtime artifact set (Brandubh/Tablut/Copenhagen-capable, structural):
**37 files, 48,102,829 bytes raw** (Pyodide, ORT Web WASM/WebGPU, Python engine
closure, workers/clients, manifests, license texts). The smoke ONNX model is
44,886 bytes; no competitive model is available.

## 3. Model publication and authorization

Registration on the server is **not** publication for browsers. Publication is a
separate administrator-authorized operation over an immutable, content-addressed
artifact store:

```
<data>/browser_models/publications.json     atomic index (history retained)
<data>/browser_models/artifacts/<sha256>.onnx   immutable published artifacts
<data>/browser_models/audit.jsonl           append-only audit trail
<data>/browser_models/.publications.lock    cross-process lock
```

Operations (CLI: `python -m hnefatafl.browser_models …`):

| Operation | Behavior |
| --- | --- |
| `verify <export>` | Runs the canonical export validation without changing state. |
| `publish <export> --account <ADMIN>` | Requires an existing active `ADMIN` account in the application database; validates marker/manifest/parity/sidecar/reference digests, the actual ONNX graph, architecture, ruleset, board size, IO contract, opset, encoder and runtime compatibility, size budget and optional registered-source fingerprint; writes the artifact and index atomically; appends an audit entry. |
| `publish … --replace` | Explicit replacement of the published slot for the same source weights; the previous record becomes `superseded` and its bytes remain immutable. |
| `revoke <publication_id> --account <ADMIN>` | Marks the publication `revoked`; retains artifact and history; removes it from listing and download. |
| `list [--history]` | Public projections or full history including superseded/revoked records. |

Only a `VALIDATED.json` export with a passing parity marker can be published; an
unvalidated export is refused. Publication is idempotent by ONNX digest, and a
failed index write cannot publish a record (an orphaned content-addressed
artifact is harmless and a retry publishes exactly once). Registered-model
deletion does not delete published copies; supersession and revocation are
explicit and audited. Distribution necessarily exposes the ONNX weights to any
authorized client that downloads them; access control restricts distribution but
cannot keep downloaded weights secret.

Real smoke publication was exercised end-to-end (structural, not competitive):
`publication_id ab67b0c808fdd6a16a571cc8`,
`onnx_sha256 9d573ef38321bf76bfc425e358deac46b8462c3b5d247eea1c3ff07754af4a91`,
Brandubh 7×7, 4 channels / 0 blocks, 44,886 bytes.

## 4. Artifact delivery contracts

Additive, versioned, authenticated `play` read endpoints:

| Endpoint | Response |
| --- | --- |
| `GET /api/browser-analysis/availability` | `{schema, enabled, runtime_available, runtime, reason, published_models}`; runtime projection contains only public identity, pins, files/digests and limits. |
| `GET /api/browser-analysis/models` | Published-model manifests (no paths, provenance-only fields are not exposed). |
| `GET /api/browser-analysis/models/{publication_id}` | One published manifest; unknown/revoked → 404. |
| `GET /api/browser-analysis/models/{publication_id}/artifact` | Streamed immutable ONNX bytes; `Content-Type: application/octet-stream`, `Cache-Control: no-store`, `ETag`/`X-Content-SHA256`, attachment filename, exact declared size. |
| `GET /browser-analysis/runtime/{runtime_id}/{path}` | Verified runtime asset (allowlist, immutable cache). |

Disabled feature: catalog/artifact/runtime requests return 404 (shared with the
unavailable state to avoid artifact discovery), and availability reports
`enabled:false`. Runtime absence leaves the catalog reachable but reports
`runtime-missing`; ordinary pages and server analysis are unaffected.

Existing Origin/CSRF/mutation protections are unchanged: these endpoints are
read-only, authenticated, session-scoped and served with the existing boundary
headers. `FileResponse` streams artifacts from disk; the server never buffers a
model in memory. Publication (expensive) never runs inside the API request loop.

## 5. Content Security Policy

The production boundary owns one policy. Disabled deployments keep the existing
policy bytes exactly:

```
default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'
```

Enabled deployments add only the minimum WASM allowance and an explicit narrow
worker source:

```
default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; worker-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'
```

No broad `unsafe-eval`, no `unsafe-inline` script source, no `blob:` workers, no
wildcard or remote origin, no COOP/COEP change. The policy was validated against
**actual FastAPI-served production responses** with real worker/WASM
initialization (section 10), not the spike server.

## 6. Browser storage, quotas and downloads

- Storage trade-off: published ONNX models live in IndexedDB (content-addressed,
  atomic single-transaction writes, digest re-verified on reuse, LRU eviction,
  byte and entry quotas). Runtime JavaScript/WASM is verified on every
  initialization but deliberately **not duplicated** into IndexedDB: executing
  stored bytes would require blob workers (broader CSP) and Pyodide resolves its
  internal assets by URL. Runtime assets reuse the origin's immutable HTTP cache
  instead. No service worker is introduced.
- Application limits (conservative, configurable in
  `frontend/src/browserAnalysis/contracts.ts`): runtime ≤160 MiB, model ≤64 MiB,
  aggregate transfers ≤192 MiB, 2 concurrent downloads, ≤96 MiB/4 stored models,
  120 s download timeout, one bound retry, one active search, ≤4,096 simulations,
  ≤10 s cooperative time limit, 45 s init, 5 s max job ceiling.
- Download manager: explicit initiation, streaming with live progress,
  per-artifact and aggregate size bounds, declared-size comparison, final size and
  SHA-256 verification, deduplication of concurrent identical requests,
  cancellation, timeout, bounded retries (transport only), cleanup on failure and
  rejection of stale completions. An HTTP 2xx is never treated as integrity.
- Failure handling distinguishes unavailable storage, quota exhaustion,
  corrupted cached artifacts, cache misses, aborted downloads and integrity
  failures. A denied or exhausted store degrades to in-memory/unpersisted use; the
  application stays usable.
- Cross-tab safety relies on IndexedDB transactional semantics and immutable keys.
  Robust global cross-tab memory/search coordination is not guaranteed by the
  browser; per-tab limits are enforced and the limitation is documented.

## 7. Runtime lifecycle and resource boundaries

`frontend/src/browserAnalysis/runtime.ts` owns availability, capability checks,
verified asset loading, lazy module import, Jarl and neural search, cancellation,
provider reporting, storage usage and disposal. It reuses the validated clients
and workers (request ownership, generations, watchdogs, bounded shutdown, worker
replacement, model identity, stale-result rejection, legal-move and
settings validation).

- Jarl runs through the classical worker without downloading a model or touching
  ONNX; neural MCTS runs only with an explicitly published, compatible model and
  request-side digest identity.
- Coordinator relay remains the default; direct transport, exact inference cache
  and independent batching remain opt-in and unchanged.
- A failed local execution produces a structured error state; it never invokes
  server analysis or another agent as a substitute, and never silently changes
  search strength, algorithm or provider.
- **Enforcement boundary:** browser processes do not expose enforceable per-tab
  RAM limits. Application limits bound downloads, storage, workers, searches and
  simulations; actual RAM usage is estimated/observed only. No hard browser-process
  RAM enforcement is claimed.

## 8. Account, replay and authorization isolation

No new replay-access API was added. Browser-analysis delivery is authenticated
`play` delivery of published model manifests and immutable inference artifacts;
it exposes no game, replay, position, account or permission data. Private games,
singleplayer replay permissions, completed-online replay permissions and the
ongoing-online-game assistance restriction are unchanged and remain
server-authoritative. Browser-computed analysis is advisory and cannot update
games, clocks, results, ratings, league progression, achievements or
tournaments. A client that receives a playable position and executable analysis
code can independently analyze that position; server-side protections are
preserved without claiming client restrictions can eliminate external tools.

Logout/session expiry/account switching: the Phase 4 runtime exposes
`invalidateAccountContext()`/`dispose()` which increment a generation, abort
downloads, cancel searches, drop results/position context and release workers.
Delayed completions cannot restore a previous account's state. Published model
bytes are public shared artifacts and may be retained; no private replay or
account data is stored by this module. Server-side revocation stops future
protected downloads; it cannot delete copies already downloaded or copied.

## 9. Offline behavior

- Published models stored in IndexedDB remain integrity-verifiable without the
  network. No authorization is inferred from stale local state: protected
  downloads require a current session.
- Runtime execution offline is **not** promised: the validated workers load
  URLs that require the origin (or its HTTP cache). A missing runtime reports
  `runtime-missing` and ordinary application behavior continues. No offline
  login, offline online-game submission or offline application redesign is
  implemented.
- Stale runtime/model combinations are rejected by encoder/protocol/version pins;
  an incompatible cached artifact is discarded on verification.

## 10. Validation performed

### Actual production browser (Chromium 156.0.8078.4, real FastAPI + production CSP)

`tests/test_browser_analysis_browser.py` (opt-in) and
`scripts/verify_browser_analysis.mjs` against a disposable data directory, the
production Vite build and a staged, verified runtime:

- 16/16 checks: login, production document, CSP `wasm-unsafe-eval` with no broad
  `unsafe-eval`, `worker-src 'self'`, ordinary page loads **no** runtime or model
  assets, runtime initializes, engine identity reported, Jarl search completes
  (advisory, no model, no neural initialization), assets same-origin, no external
  runtime origins.
- Measured: cold init **2,486–2,488 ms**; second init **1,972–2,181 ms**; Jarl
  depth-3 analysis **240–245 ms**; runtime asset requests 109 (Jarl) / 142
  (Jarl+neural); model artifact requests 0 during Jarl and 2 for an explicit
  neural initialization (download manager + validated client); no external
  requests.
  Evidence: [browser-analysis-phase4-browser.json](browser-analysis-phase4-browser.json).
- Neural MCTS with the published smoke model: 32 simulations, best move
  `26→12`, value `-0.41834926698356867`, model digest bound to the published
  `9d573ef…`, provider `wasm` with `gpuExecutionVerified:false`.
  Evidence: [browser-analysis-phase4-browser-neural.json](browser-analysis-phase4-browser-neural.json).
- Disabled deployment: production build starts and serves the existing CSP
  without `wasm-unsafe-eval`, health works and runtime paths remain unavailable.

### Automated tests (bounded, no sustained measurements in CI)

- `tests/test_browser_runtime_distribution.py`: 10 passed (deterministic
  manifest, tamper/missing/unexpected/symlink/stale/incompatible rejection, atomic
  build/activation, allowlist resolution, CLI exit statuses, license inventory).
- `tests/test_browser_model_publication.py`: 20 passed (validated-only
  publication, immutable artifacts, idempotency, replacement/supersession,
  revocation, invalid identifiers, artifact conflict, size/opset/encoder
  rejection, atomic index failure, registered-source binding, active-admin
  authorization, CLI denial).
- `tests/test_browser_analysis_api.py`: 8 passed (disabled default, narrow CSP,
  authentication, runtime manifest/asset delivery and allowlist, model listing and
  streaming headers, revocation, truncated artifact fail-closed, runtime absence,
  logout invalidation).
- `tests/test_browser_analysis_lifecycle_sweep.py`: 8 passed (E1: validator exit
  0/1/2 handling, partial continuation without acceptance, readiness/collection
  failure cleanup, interruption status, ownership-file cleanup, per-case report
  path).
- `tests/test_security.py` + API inventory: 24 passed; `docs/API_PERMISSIONS.json`
  regenerated with the four additive routes.
- Existing browser-runtime regressions: 39 passed, 8 skipped (optional staging),
  including the refactored canonical export validator; with optional
  `onnx`/`onnxruntime` installed, `tests/test_browser_neural_export.py` is
  **31 passed**.
- Frontend Vitest: **1,618 passed (112 files)**; the new browser-analysis suites
  contribute **42 passed** across manifest, storage/quota/LRU/corruption, download
  lifecycle and runtime manager coverage.
- Full backend suite (Python 3.13 host): **4,430 passed, 41 skipped, 46 deselected**
  with `-k "not openvino"`. The deselected OpenVINO-named tests crash the shared
  host interpreter because the optional OpenVINO package is not installed in the
  reused environment; CI installs all extras and does not deselect them. No
  Phase 4 test fails.
- `make deployment-check`: **63 + 9 passed** (real lifespan, exclusive deployment
  flock and recovery paths).
- Ruff, `ruff format --check`, mypy (79 files), frontend Prettier check and
  `make docs` pass.

### Bundle impact

Initial authenticated graph unchanged: **573,936 bytes raw / 183,181 bytes gzip**
before and after (same four files). The browser-analysis contract is a separate
lazy entry chunk (`assets/entry-*.js`, 26,131 bytes raw) that no ordinary route
imports; initial budget checks pass unchanged.

## 11. Dependency and license audit

| Component | Version | License | Distribution |
| --- | --- | --- | --- |
| Pyodide | 0.27.7 | MPL-2.0 | Yes, pinned/digest-verified runtime |
| ONNX Runtime Web | 1.22.0 | MIT | Yes, pinned/digest-verified runtime |
| CPython stdlib (Pyodide) | 3.12.x | PSF-2.0 | Yes, inside Pyodide bundle |

No new npm runtime dependency was added; `npm run audit:ci` policy is unchanged
and no findings were suppressed. `onnx`/`onnxruntime` remain optional
administrative tooling for export validation and are **not** shipped to browsers.
License texts are committed under `hnefatafl/browser_licenses/` and staged into
every runtime; `THIRD_PARTY_NOTICES.md` records the versions and obligations.
MPL-2.0 applies to Pyodide itself and does not relicense project code; the project
does not relicense any third-party component.

## 12. Deployment, update and rollback

- Runtime and publication data live under the persistent data root
  (`/var/lib/hnefatafl/browser_runtime`, `/var/lib/hnefatafl/browser_models`), which
  existing deployment, backup and rollback procedures preserve. They are never
  inside a release directory.
- `make browser-runtime` stages/verifies/activates; `make browser-runtime-status`
  reports the active runtime (exit 3 when unavailable). Activation is atomic and
  verified; `prune` keeps rollback material.
- Because manifests pin the application version, encoder, driver and worker
  protocols, a partially completed deployment cannot silently pair a new release
  with an incompatible runtime: the server reports the runtime unavailable until a
  matching runtime is staged. A rollback to an older release likewise refuses the
  newer runtime instead of executing it.
- An installation without runtime assets starts normally; the feature stays
  disabled/unavailable and ordinary pages are unaffected. No Docker requirement
  and no change to single-process deployment/locking assumptions.
- Backup/restore: the existing full data-root snapshot covers publication metadata
  and artifacts; runtime assets are reproducible from pinned inputs. Restore an
  older `publications.json` only together with its content-addressed artifacts
  (the index never points outside `<data>/browser_models/artifacts`).
- Interrupted installation: re-run `build`/`activate`; incomplete staging
  directories are ignored and removable, and an unverified runtime is never
  activated.

## 13. Phase 5 integration contract

`frontend/src/browserAnalysis/entry.ts` exports `createBrowserAnalysis()` with a
stable, versioned surface (types in `contracts.ts`):

`availability`, `initialize`, `listModels`, `downloadModel`, `runSearch`,
`cancel`, `invalidateAccountContext`, `dispose`, `status`, `lastResult`,
`storageUsage`, plus structured `BrowserAnalysisError` codes and explicit states
`disabled | unavailable | idle | downloading | initializing | ready | searching |
cancelled | error`.

Phase 5 must: keep `BROWSER_ANALYSIS_DEFAULT_ENABLED = false`/server flag opt-in,
load this module only from the deferred boundary, never auto-download on public
spectator pages, present execution-mode selection and fallback explicitly, and not
claim GPU verification without independent evidence.

## 14. Outstanding gates and limitations

Preserved (not resolved by Phase 4):

- Competitive 32×2+ private checkpoints for Brandubh/Tablut/Copenhagen and real
  playing-strength tournaments (structural smoke model only here).
- Physical Windows/Linux/Android/Safari device matrix, mobile backgrounding and
  resource pressure; emulation is not physical evidence.
- Verified WebGPU execution (no per-operator diagnostic; GPU is never claimed).
- Sustained-memory acceptance (30-minute JS-heap step unresolved; WASM heap
  capacity unobservable; thresholds unchanged).
- Copenhagen depth-5 completion inside tested budgets; production Jarl strength
  unchanged.
- Physical Windows descendant cleanup remains unverified.
- Python 3.11 frozen-reference baseline unchanged; no digests weakened.

Phase 4 limitations:

- No player-facing UI, no execution-mode selector, no automatic downloads.
- Runtime execution offline is not provided (no service worker; documented above);
  model artifacts are offline-verifiable but protected downloads still require a
  valid session.
- A cold neural initialization fetches the model twice (application download
  manager for progress/quota/verification, then the validated client for the
  session); it is a documented efficiency limitation with no security impact.
- Cross-tab aggregate RAM/search coordination is best-effort per tab.
- Runtime requests are re-verified per initialization (Pyodide boot dominates
  second-init timing); there is no runtime byte store.
- Publication is CLI-only (no management UI); competitive model publication is
  externally blocked.
- The staged runtime pins the locally installed package version; the source
  checkout uses the installed metadata rather than Git state (documented).

## 15. Required owner actions

1. Stage and activate a runtime from pinned assets in the deployment data root
   (`make browser-runtime`) and verify `make browser-runtime-status`.
2. Publish validated competitive exports with an active administrator account;
   never expose private checkpoints, optimizer state or training metadata.
3. Keep `HNEFATAFL_BROWSER_ANALYSIS_ENABLED=0` until Phase 5 and the remaining
   Phase 3 acceptance gates are approved.
4. Re-run the actual-browser gate on physical hardware when available and record
   evidence; do not treat this report as device or competitive acceptance.
5. Decide on a service worker/offline execution design only through a separately
   approved change.
