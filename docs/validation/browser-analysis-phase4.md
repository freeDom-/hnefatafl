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
- Runtime assets are verified before first initialization and each served asset is
  stat/digest checked at the origin; a repeated initialization that is already ready
  is an idempotent no-op. There is no separate runtime byte store.
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

## 16. Review corrections (F1–F8, E1 follow-up, transition-history stabilization)

This section records the corrections made after the first Phase 4 review. The
original sections above remain as the initial implementation record; where they
describe superseded behavior, this section is authoritative.

### F1 — end-to-end asynchronous operation ownership (resolved)

`frontend/src/browserAnalysis/runtime.ts` now captures an explicit operation at
every public entry point before the first asynchronous boundary. An operation
carries an owner context generation, the runtime identity it targets, its own
`AbortController` (chained to the caller signal), and the provisional resources it
created. Ownership is rechecked after every relevant await and before resource
allocation, state publication, cache activation and result publication. An
operation that loses ownership aborts, disposes only the resources it created and
cannot create a worker, publish a model/result or clear newer state. Initialization
is idempotent and joins one in-flight promise; repeated calls do not accumulate
workers. Logout/account-switch/disposal bump the context generation, abort pending
operations and downloads, cancel active search/inference work, await a bounded
cleanup barrier and only then release/replace workers. Cancellation and
reinitialization semantics are explicit; a disposed runtime rejects all further
work. The runtime manager remains the integration point for the existing auth/logout
lifecycle; no second authentication system exists.

Deterministic tests use deferred promises at availability, module import, engine
probe, model load and search, and assert actual worker creation/disposal counts
(not just result absence): logout during availability/import/model loading/search,
cancellation during initialization, disposal during import, repeated/concurrent
initialization, overlapping account contexts, and obsolete completion after a newer
runtime is ready.

### F2 — immutable runtime artifact identity (resolved)

The server no longer resolves request assets through the mutable `current` link.
`runtime_snapshot()` loads and fully verifies the exact `<runtime_id>` directory
(manifest, complete inventory, pinned upstream digests, canonical identity).
Requests are served only from that snapshot; the active identity is discovered from
`current` on each availability request, so activation/deactivation after a cached
"unavailable" result is detected without a restart. Retained, still-present runtime
identities are served from their own immutable bytes (loaded on demand and bounded
in the process cache); pruned or unverifiable identities return a controlled 404.
Per-request checks use a bounded `(inode, size, mtime_ns)` stat-signature digest
cache, so same-size replacement is detected by re-hashing that file without hashing
the distribution on every request. Symlinked/escaping assets are rejected, and
response `X-Content-SHA256`/size always describe the exact resolved file. Browser
module caches are keyed by `${runtimeId}:${module}`, and a runtime identity change
disposes workers, clears modules/model/evidence/results and aborts old operations.
The trusted boundary is documented: a privileged administrator who can replace the
application code and its trusted manifests is outside the integrity claim.

Actual-browser validation: `test_runtime_identity_change_in_actual_browser` stages a
second verified runtime with a modified validated worker asset, activates it while
the server runs, then in Chromium observes the new runtime identity, initializes
it, and confirms the retained old URL still serves exactly the old digest/size.

### F3 — actual provider and search-result evidence (resolved)

The result contract now consumes the validated coordinator envelope
(`{analysis, search, settings}`). `evidence` carries `executionRuntime: 'pyodide'`,
`requestedProvider`, `observedPlacement`, `gpuExecutionVerified`, `inferenceObserved`,
`inferences`, `completedSimulations`, `completionReason`. Observed placement is read
only from `search.provider.execution_placement`; it is never synthesised from the
request. Searches with no inference report `observedPlacement: 'none'` and
`inferenceObserved: false`. Missing/malformed/contradictory evidence (missing provider
after inference, requested-provider mismatch, unknown placement, non-boolean GPU
verification, GPU placement for a WASM request, or provider evidence with zero
inferences) fails closed. Mixed/unverified placements are preserved verbatim; GPU
execution is reported exactly as the validated evidence reports it (the pinned
runtime reports `false`), with no alternative verification mechanism. Effective
settings, model identity, search id, completed simulations, inference counts and
completion reason are preserved. Classical Jarl is distinguished as Pyodide
execution with no neural inference.

Tests cover WASM, WebGPU requested but unavailable, mixed placement, no-inference
shortcuts, missing/malformed evidence, provider mismatch, effective settings and a
real production-browser neural run (33 inferences, 32 simulations, `wasm`).

### F4 — generation-safe download accounting and cancellation (resolved)

`DownloadManager` now uses a generation-scoped reservation pool. Cancellation
retires the current pool and creates a fresh one; old operations can release only
into their retired pool, so they cannot free capacity owned by newer work. Aggregate
byte counters are pool-scoped. Queued operations are rejected on cancellation and
never counted as active transfers. Slots are acquired/released exactly once. Each
shared transfer tracks its live subscribers; an individual cancel removes its
listener immediately, and the underlying transfer is aborted only when the last
subscriber leaves. Concurrent callers with the same key must agree on
url/size/digest. Already-aborted signals never start a request; queue waiting,
retries, transfer, hashing and cache admission are bounded by one operation
deadline. Progress is delivered only to live subscribers. Cache admission is
ownership-checked before and after and removed again if the operation was cancelled.
Deterministic tests cover repeated `cancelAll`/restart, old completions, cancelled
queued admissions, already-aborted signals, shared subscribers, last-subscriber
cancellation, cancellation during hashing, aggregate/concurrency limits and
reservation/listener leaks.

### F5 — verified model bytes feed actual inference (resolved)

The validated neural client exposes `loadModelBytes(buffer, options)` and shares one
session-initialization path with URL loading (`_initializeSession`). The production
runtime re-checks current publication authorization (metadata request, never a
cached authorization claim), obtains verified bytes from the IndexedDB cache or one
bounded manager download, re-verifies size/digest through the client, and passes the
buffer by structured clone (never a transfer), so persistent/cached buffers are not
detached. Model initialization no longer performs a second network transfer.
Revocation is rechecked before every activation; previously downloaded bytes remain
usable only while the publication is still authorized. The artifact cache serializes
concurrent admissions, preserves byte/entry quotas, replaces the same key without
evicting unrelated entries, verifies cached size+digest, and reports
`{retained, persistent, reason}` separately. Actual-browser evidence: one model
artifact request for an explicit download plus two neural searches; `modelMetadata`
requests are authorization checks, not transfers.

### F6 — atomic publication and delivery identity (resolved)

`PublishedModelStore.resolve_artifact(publication_id, artifact_version)` reads the
publication index once, selects the published record, binds the exact immutable
version and verifies the file identity (not a symlink, inside the artifact root,
declared size, cached stat-signature SHA-256) under one lock. The endpoint supports
`?version=<sha256>` and consistently returns 404 for stale/unknown versions; response
headers come from the resolved record/file. Revocation during an already-started
download lets that authorized immutable stream finish, while all new requests fail;
revocation cannot recall transferred bytes. Publication/revocation now roll back the
index if the audit write fails and report `PUBLICATION_IO`, leaving recoverable state
(orphaned immutable artifacts are harmless and retried publications reuse them).
Tests cover replacement, revocation between resolve and serve, same-size corruption,
symlink/missing artifacts, stale versions, authorization failures and audit rollback.

### F7 — complete runtime compatibility validation (resolved)

Python and TypeScript now validate the same contract: schema, 16-hex identity,
Pyodide/ORT versions, encoder, driver, search algorithm, search/bridge protocols,
architecture, application version, required inventory, per-file size, file count,
declared total versus inventory, aggregate budget and canonical identity. Python
independently re-binds every pinned upstream binary through both the file inventory
and the declared upstream lists, so a self-consistent manifest cannot substitute a
different Pyodide/ORT binary. `0+local` is explicitly development-only: verification
requires `development=True` (CLI `--development`, used automatically by a source
checkout server) and never counts as a production release match. The TypeScript side
recomputes the canonical SHA-256 identity and rejects a mismatched manifest; a
Python↔TypeScript cross-check test pins both implementations. Malformed, recomputed,
missing-required, excessive-size and incompatible-version cases are covered.

### F8 — explicit algorithm and effective-settings contract (resolved)

`SearchRequest.algorithm` is required (`'jarl'` or `'neural_mcts'`). Jarl resolves to
the existing production `alpha_beta` profile (`depth 5`, `time_limit 2`,
`shield_maiden`, production search defaults) and never falls back to the baseline
agent; unknown/out-of-contract settings, depth >5, a model/provier on a Jarl request,
or a missing algorithm are rejected with structured errors. The production bridge
requires an explicit algorithm (`alpha_beta` or classical `mcts`) and no longer
defaults to `baseline`. Classical budgets are bounded at 3 s inside the existing
5 s job watchdog; neural MCTS keeps its separate cooperative budget (≤10 s) and
watchdog. Initialization, execution and shutdown deadlines remain separate.
Effective settings are returned in `result.settings` and `status.lastSettings`.
Production algorithms/defaults are unchanged. Actual-browser parity checks use
explicit settings and assert the effective Jarl and neural configurations.

### E1 follow-up and transition-history stabilization

The lifecycle sweep script now returns **6** for workload execution failure, keeping
it distinct from validator exit code 2 (structurally valid partial evidence), and
preserves ownership-file cleanup diagnostics; stub tests assert the distinction.
The flaky `test_collector_and_validator_span_evicted_and_retained_history` no longer
relies on wall-clock completion: the telemetry writer emits an explicit
`--transition-complete-file` handshake when the required 1,100 transitions have been
produced, and the collector samples concurrently. The expected 1,100 total/1,024
retained history and the evicted/retained sample-span assertion are unchanged and
now deterministic.

### Offline behavior — explicit proposed deferral

Safe offline execution of the Pyodide/ORT runtime is **not implemented**. Verified
published models remain readable from IndexedDB and are re-authorized before use,
but worker execution loads origin URLs and the runtime has no service worker.
Implementing offline runtime execution would require either a global service worker
(interception of Pyodide's internal asset fetches) or blob-based worker/runtime
execution with a broader `worker-src`/`connect-src` policy. Both are disproportionate
for Phase 4 and affect existing application caching/auth/spectator behavior. The
owner has **explicitly approved deferring offline browser-analysis runtime execution
to a separate implementation phase**; it is no longer a Phase 4 completion
requirement. This PR does not implement it. Interrupted downloads, missing assets, storage denial and
reconnection are handled by falling back to a clear unavailable/offline state; no
offline acceptance is claimed.

### Dependency and license audit (Phase 4 review)

No dependency was added or upgraded. Distributed runtime components remain Pyodide
0.27.7 (MPL-2.0), ONNX Runtime Web 1.22.0 (MIT) and the CPython stdlib inside Pyodide
(PSF-2.0), pinned by recorded digests. Required license texts are committed and
staged into every runtime. The audit confirms these permissive/weak-copyleft terms
are compatible with distribution under the project license with notices preserved;
`onnx`/`onnxruntime` remain optional administrator-only export tooling. Unresolved
licensing action: the pinned Pyodide distribution embeds additional third-party
components whose notices are governed by Pyodide's own distribution; the project
ships the MPL/PSF core texts and points to the pinned upstream distribution for its
complete notice set. No new security advisory was introduced; the existing
`npm run audit:ci` policy is unchanged and no findings were suppressed.

### Measurements after correction (actual production browser)

| Measurement | Jarl-only | Jarl + published neural model |
| --- | --- | --- |
| Cold runtime initialization | 2,469–4,439 ms | 2,470–2,975 ms |
| Second initialization (idempotent ready) | 5.5–9.6 ms | 6.4–10.2 ms |
| Jarl depth-3 search | 242–367 ms | 243–262 ms |
| Runtime asset requests | 75 | 104 |
| Model artifact transfers | 0 | **1** (explicit download reused by repeated searches) |
| Model metadata/authorization requests | 0 | 3 |
| Jarl cancellation + recovery search | yes | yes |
| Initial bundle after merging current main | 585,882 raw / 186,757 gzip | same (budgets 600,000/200,000 pass) |

Evidence: [Jarl browser report](browser-analysis-phase4-browser.json) ·
[neural browser report](browser-analysis-phase4-browser-neural.json). The earlier
report's two-transfer model count and 1,972–2,181 ms second initialization are
superseded: one cold model transfer now suffices, and a repeated initialization that
is already ready is an idempotent no-op (5.9–6.6 ms). The initial bundle grew from
main's merged arena work, not from the lazy browser-analysis chunk; the chunk is
absent from the initial manifest graph and both budgets still pass.

### Remaining external gates (unchanged)

Competitive checkpoints/playing strength, physical device matrix, verified GPU
execution, unobservable WASM heap capacity, unresolved 30-minute JS heap growth,
Copenhagen depth-5, physical Windows cleanup and the pre-existing Python 3.11
frozen-reference failures remain outstanding exactly as documented above. No search
algorithm, strength or default was changed by these corrections.

## 17. G1–G6 review corrections

### G1 — complete cancellation and model-session recovery

- Classical cancellation now propagates the operation signal to the actual
  `LocalAnalysisClient.cancel()`, and watchdog expiry also cancels the underlying
  request. Listeners are removed on every completion path; worker ownership stays
  with the validated client and obsolete cancellations cannot terminate a newer
  search. The cancellation cleanup barrier is awaited before replacement searches;
  repeated Jarl searches after cancellation reuse the same client and recover.
- The manager no longer trusts remembered `loadedModelDigest`/`loadedProvider`
  fields. Every neural search reconciles against the client's actual live
  `sessionIdentity()` (digest, ruleset, size, provider). A failed replacement,
  cancelled load or worker failure that destroys the session forces reinitialization
  from verified bytes on the next request; healthy sessions are reused.
- Account invalidation is an atomic admission boundary: the context generation is
  bumped, stale results are cleared immediately, old worker references are removed
  before the first await, new operations wait on the cleanup barrier, and delayed
  cleanup disposes only the exact captured resources. If bounded cleanup cannot be
  established the captured workers are force-disposed and an explicit retryable
  error state is published instead of silently reusing uncertain state.
- Recovery tests assert actual worker creation/disposal counts: Jarl abort +
  immediate search, model A success → B load failure → A recovery, failed initial
  initialization + retry, cancellation during model load + same-model retry, worker
  failure + recovery, and account invalidation overlapping initialization/search
  admission. The actual-browser gate now cancels a live depth-5 Jarl search and
  performs a successful recovery search.

### G2 — resource limits across download generations

- Concurrency slots and byte reservations are now manager-global and are released
  only when the underlying transfer genuinely settles. A retiring generation keeps
  its real transfer slots and reservations until completion; cancellation is not
  treated as proof that work stopped. Reads/servers may still observe peak occupancy
  equal to the configured concurrency, never above it.
- Ownership is captured before the asynchronous cache lookup; a `cancelAll()` during
  the lookup cannot start an obsolete transfer afterwards. Queued waiters are
  removed promptly on abort/deadline, and last-subscriber cancellation aborts the
  shared transfer while preserving deduplication for remaining subscribers.
- Abandoned shared transfers are replaced rather than joined. Cache admissions carry
  an ownership token; obsolete cleanup uses `removeOwned(key, admissionId)` and can
  never delete a newer same-key admission.
- **Instance boundary:** the public Phase 5 `BrowserAnalysisRuntime.create()` path is
  a per-tab singleton (a second instance is rejected with `quota-exceeded` until the
  first is disposed). Direct constructors remain internal/test-only. Limits are
  therefore per runtime instance/tab; cross-tab aggregate enforcement remains
  best-effort and documented. No limits were increased for these corrections.

### G3 — provider and search-evidence validation

- Provider evidence now rejects: requested-provider mismatch; GPU placement for a
  WASM request; any GPU-verification claim (the pinned runtime has no independent
  per-operator mechanism); unauthorized WebGPU→WASM/CPU fallback; provider evidence
  with zero inference; and missing/malformed placement or verification fields.
  Mixed/unverified classifications are preserved when actually reported.
- Effective settings are required, validated against the normalized requested
  configuration, and no longer substituted from the request. Completion reason must
  be one of the driver's canonical reasons; completed simulations must not exceed the
  requested budget, and a `simulations` completion must complete the requested count.
  Model/search identities and the advisory, non-authoritative contract are
  preserved.
- Mutation regressions use actual coordinator envelopes for contradictory evidence,
  GPU-true-for-WASM, WebGPU fallback, missing/mismatched settings, malformed reason
  and excessive/incomplete simulation counts.

### G4 — verified runtime and model file identities

- File signatures now include device, inode, size, mtime and ctime. Verification
  lstat-walks every unresolved path component, rejects symlinks/non-regular files,
  checks the signature before and after hashing, and requires the file not to change
  during verification.
- Runtime and published-model assets are verified and opened once; the response
  streams the verified descriptor with headers bound to that exact file. Replacement
  between verification and response opening therefore cannot substitute bytes.
  Same-size in-place modification with restored mtime is detected via ctime/device
  identity; external symlinks/escapes and mid-verification replacement fail closed.
  Bounded caches remain per runtime directory/artifact and whole-model buffering is
  avoided. Concurrent activation, revocation/replacement and pruning keep their
  previous safe behavior.
- Trusted boundary (documented): the application/data directory is controlled by the
  service owner. This does not claim protection against a privileged administrator
  who can replace the application and its trusted manifests.

### G5 — healthy-session reuse

- Neural searches revalidate publication authorization, then check the actual live
  session digest/ruleset/size/provider before retrieving model bytes. A matching
  healthy session performs no IndexedDB read, no re-hash and no transfer. Bytes are
  fetched/verified only for initialization or replacement (including provider switch
  or session loss). Verifying tests cover repeated searches, storage eviction while
  a session is live, revoked publication, provider switch and session-loss reload.
  Measured actual-browser behavior remains **one** cold model artifact transfer for
  an explicit download plus repeated neural searches.

### G6 — effective-settings validation

- Temperature is a fixed deterministic analysis parameter: zero is accepted and not
  forwarded; any nonzero value is rejected with a structured error. The production
  bridge enforces the same rule and advertises a `[0, 0]` temperature bound.
- Jarl nested search configuration is strictly validated (known boolean fields and
  bounded positive `table_capacity`; symmetry implies the ordinary table) before the
  worker, while the canonical Python normalizer remains the final authority. Unknown
  fields are never silently ignored. Effective Jarl/neural settings from the response
  are required to match the requested normalized configuration and are returned to
  Phase 5. Production Jarl defaults and classical/neural budgets are unchanged.

### E1 diagnostics and offline/licensing status

- The lifecycle cleanup trap now reports ownership-safe termination failures with a
  warning while preserving the original operation exit status; a bounded stub test
  covers it.
- Offline runtime execution is **deferred by explicit owner approval** to a separate
  implementation phase and is not implemented here. Verified cached models remain
  usable with current authorization checks; no service worker or CSP broadening was
  added.
- Licensing: see §18; required core texts are shipped and the inspected distributions
  are recorded. Embedded notices not provided by those distributions remain an
  unresolved distribution blocker, without a completion claim.

### Current head validation

Focused Phase 4/security/bridge/lifecycle/deployment tests: **105 passed**; full
frontend Vitest: **1,702 passed (113 files)**, including 102 browser-analysis tests. Actual production-browser Jarl/neural runs, live Jarl
cancellation followed by a successful recovery search, and the runtime-identity-change
gate pass (refreshed evidence files). Full CI and mergeability are reported on the PR.


## 18. H1–H4 corrections, owner-approved offline deferral and distribution licensing

### H1 — coordinated initialization/search/cleanup admission

- `initialize()` is idempotent for a compatible ready or searching runtime: an
  initialization during an active Jarl or neural search returns the current state
  without creating/disposing workers or interrupting the search. Initialization and
  search admissions share the same ownership/cleanup barriers; runtime-identity
  replacement aborts search operations bound to the old identity and requires
  reinitialization before new searches.
- Availability responses carry a monotonic sequence; a late response can never
  replace a newer applied availability state.
- Cancellation captures the exact clients from its context. On cleanup timeout only
  those captured clients are quarantined/terminated (and only if still current), the
  stale `activeSearch` is cleared, and the cleanup barrier is released so future
  operations are not permanently blocked. Status/error publication is guarded by
  context and owner identity, so an obsolete cancellation/invalidation cannot
  overwrite a newer state. Successful recovery after a timed-out cancellation is
  tested with actual worker counts; obsolete cleanup completing later cannot dispose
  a newer session.
- H4: the caller-signal abort forwarding listener is stored and removed on success,
  failure, cancellation and abandonment; already-aborted signals attach no listener.
  A shared long-lived signal regression verifies balanced add/remove across repeated
  operations. This is not claimed to explain historical sustained-memory results.

### H2 — resource limits across complete runtime retirement

- Download managers expose `whenIdle()`/`drain(timeout)` and global occupancy
  (active transfers, reserved bytes, queued transfers). Runtime `dispose()` returns a
  bounded `RuntimeDisposalOutcome {drained, activeTransfers, reservedBytes,
  transfers, queued}`. Workers are force-disposed after the bounded cancellation
  barrier; downloads are cancelled and drained.
- The managed per-tab singleton reservation is released exactly once only when
  retiring work has genuinely settled. If the caller-facing disposal deadline
  expires during non-interruptible hashing/admission, `drained:false`/occupancy is
  returned, admission stays reserved, and the reservation is released when
  `whenIdle()` resolves. Immediate replacement creation during disposal is rejected;
  creation after complete disposal succeeds; repeated `dispose()` returns the same
  outcome.
- The raw `BrowserAnalysisRuntime` constructor is no longer exported from the public
  Phase 5 entry point. `createBrowserAnalysis()` is the production path and enforces
  the singleton; direct construction remains internal/test-only. Per-instance/tab
  limits and cross-tab best-effort behavior are unchanged; limits were not increased.

### H3 — bounded download outcomes and response cleanup

- One caller deadline covers cache lookup, queue wait, transfer, retry, hashing and
  storage admission. Callers receive a structured `download-timeout` (or
  cancellation) within that deadline; actual slot/byte reservations remain until the
  underlying non-interruptible work settles. Late hashing/admission never activates
  a stale artifact (`removeOwned` token check), and callers can retry successfully
  afterwards.
- Rejected/abandoned response bodies and unread readers are explicitly cancelled
  (reader lock release is not treated as cancellation); cancellation failures do not
  mask the original integrity/size error. Subscriber isolation, digest/size/retry
  limits, healthy-session reuse and one-transfer cold model initialization remain.

### Validation at the H-correction head

Runtime lifecycle tests (including initialize during Jarl/neural search, identity
replacement admission, out-of-order availability, overlapping cancel/invalidate,
cleanup-timeout recovery, obsolete cleanup, managed disposal with active
transfer/hashing/admission, repeated dispose and signal-listener balance) and bounded
download deadline/cancellation/response-cancel tests pass:
**122 browser-analysis Vitest tests** (64 runtime, 34 download, 12 storage, 12
manifest) and **1,722 full frontend tests (113 files)**.

Actual production browser (real FastAPI + CSP, final build):

| Measurement | Jarl-only | Jarl + published neural |
| --- | --- | --- |
| Cold runtime initialization | 2,631.5 ms | 2,773.6 ms |
| Repeated initialization (idempotent ready/searching) | 0 ms | 0 ms |
| Jarl search | 274.2 ms | 285.7 ms |
| Runtime asset requests | 75 | 104 |
| Model artifact transfers | 0 | **1** (reused by repeated searches) |
| Model metadata/authorization requests | 0 | 3 |
| Live cancellation + recovery search | pass | pass |

Downloads/hashing/admission retire under retained accounting; a replacement managed
runtime is rejected until retirement finishes and then created successfully. No
sustained-memory rerun was required.

## 19. Distribution licensing review (exact sources inspected)

Inspected distributions:

- Pyodide **0.27.7** GitHub tag `0.27.7` `LICENSE`
  (`https://raw.githubusercontent.com/pyodide/pyodide/0.27.7/LICENSE`, 16,725 bytes,
  MPL-2.0), matching the shipped `PYODIDE-MPL-2.0.txt`.
- Pyodide **0.27.7** release `pyodide-core-0.27.7.tar.bz2`
  (`https://github.com/pyodide/pyodide/releases/download/0.27.7/pyodide-core-0.27.7.tar.bz2`,
  5,608,503 bytes): contains the runtime binaries but **no** LICENSE/NOTICE/
  third-party notice files.
- CPython 3.12.11 `LICENSE` (PSF-2.0), matching the shipped `PYTHON-PSF-2.0.txt`.
- `onnxruntime-web` **1.22.0** npm tarball
  (`https://registry.npmjs.org/onnxruntime-web/-/onnxruntime-web-1.22.0.tgz`):
  `package.json` declares `"license": "MIT"`; the tarball contains **no**
  LICENSE/ThirdPartyNotices file, and its README points to the upstream repository
  license.

Current status: the core texts required for the three distributed components are
shipped and pinned. **Remaining blocker:** the complete embedded third-party notice
inventory for the Pyodide core binaries (bundled Emscripten/CPython-adjacent
components) and ONNX Runtime Web WASM/WebGPU binaries is not shipped by those
distributions and could not be independently retrieved in this environment. The
licensing audit is therefore **not claimed complete**; the project must obtain and
ship the applicable upstream notices, or obtain explicit owner/legal acceptance of
the distribution basis, before a wider redistribution/release. No component is
relicensed under the project license and no unrelated dependency was upgraded.

## 20. I1–I2 cache-retirement and cleanup-barrier corrections

### I1 — tracked cache verification and retirement accounting

- `DownloadManager` now owns one deduplicated, tracked cache-verification path per
  artifact key. Cache reads and digest verification are included in `occupancy`
  (`cacheVerifications`), `whenIdle()` and `drain()`; they therefore keep managed
  runtime admission and disposal outcomes occupied until the non-interruptible
  browser operation genuinely settles.
- Caller deadlines/cancellation only stop the caller's wait. A late cache result
  cannot begin a transfer, activate a model or mutate newer runtime state; repeated
  timed-out lookups join the same outstanding verification rather than accumulating
  work. Authorization, ownership-token invalidation and generation checks remain.
- `BrowserAnalysisRuntime.ensureModelBytes()` no longer performs a separate direct
  `cache.getVerified()` lookup; it uses the same bounded/tracked download-manager
  path. An ordinary cache miss performs exactly one lookup, an authorized cached
  artifact initializes with zero model transfers, and an already healthy neural
  session still performs no cache lookup or model hashing.

Tests cover pending cache read/digest during managed disposal, caller timeout with
outstanding verification, repeated timed-out lookups and occupancy, replacement
rejection until retirement, creation afterwards, cancellation during cached-model
initialization, cached initialization through the manager, one-lookup misses and
healthy-session reuse.

### I2 — cleanup-barrier recovery across invalidation and disposal

- Captured clients from cancellation, account invalidation and disposal are tracked
  in one retirement registry. Later disposal composes with the prior cleanup barrier
  and includes earlier captured retirements; on timeout only captured resources from
  that/older contexts are force-terminated (idempotently guarded), and the obsolete
  barrier is replaced only while it is still current. A newer context or barrier is
  never overwritten.
- Account-invalidation timeout can no longer permanently block initialization:
  affected workers are terminated, the stale barrier is removed, structured
  retryable state is published, and a later initialize/search succeeds with exact
  worker counts. Cancellation timeout and invalidation overlap use the same
  mechanism. Disposal no longer releases managed admission while earlier
  invalidation retirement is outstanding; the bounded disposal outcome still reports
  non-interruptible download/cache work until it settles.

### Validation at the I-correction head

Browser-analysis Vitest: **134 passed** (72 runtime lifecycle/cache-retirement,
38 download accounting/deadline/cache, 12 storage, 12 manifest); full frontend
**1,734 passed (113 files)**. Actual production browser (final build): Jarl and
neural runs plus live cancellation/recovery pass, one model artifact transfer,
metadata revalidations 3; cold init 2.79 s (Jarl) / 4.37 s (neural), Jarl search
272–325 ms, idempotent repeated init 0 ms. Initial bundle remains 585,756 raw /
186,716 gzip with the lazy chunk absent. No sustained-memory rerun was required.
