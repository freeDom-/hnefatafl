# Phase 4 guest separation validation

[Documentation index](../README.md) · [Guest/storage guide](../GUEST_PLAY.md) ·
[Phase 5 handoff](../designs/online-phase5-handoff.md)

The owner explicitly approved “Yes, I approve this” after the explanation of the
single-slot retention/replacement and engine-replay policy, on 2026-10-25 UTC.
The dedicated branch is `feat/phase4-guest-local`, based on integration commit
`f49f7b659709c53ca694c00a381e8a5e78c744f0` after Phase 3 PR #13 merged. The PR
targets `feat/online-multiplayer` and is left unmerged. Version remains 0.29.0.
No production data/configuration, migration, admission/timeout activation,
deployment, release or tag is part of this work.

## Delivered boundary

Public guest play runs the unchanged Python engine and baseline AI in a bounded
dedicated Pyodide worker. Auth/account App and Online are separate lifecycles;
existing server records and browser selection/setup keys retain their formats.
The sole guest slot stores a bounded versioned move envelope and replay cursor,
never boards/results/identities. Restoration derives legality and terminal results
by canonical engine replay. Explicit replacement/clear, transaction rollback,
stale-tab revision guards and visible memory-only recovery implement the approved
policy. No guest archive, upload, account results or competitive/training records
are added.

Backend changes only expose the exact guest shell paths and grant Wasm compilation
to the content-addressed static worker. No API endpoint or permission grant was
added; legacy active-online assistance guards remain unchanged. The existing
inline appearance bootstrap was moved verbatim to a same-origin static asset so
the production document CSP can execute it.

## Validation results

The final local `make check` passed on 2026-10-26 UTC: **1,440 backend tests
passed / 36 skipped**, **305 frontend tests passed / 37 files**, plus Ruff,
mypy (44 source files), TypeScript, formatting, docs and production build.
Local skips comprise missing optional OpenVINO numerical coverage and unavailable
CUDA/Intel GPU hardware; the backend emitted one existing TestClient deprecation
warning. The build retains the existing large-chunk advisory.

| Command (repository root) | Result |
| --- | --- |
| `.venv/bin/python -m pytest -q tests/test_guest.py tests/test_guest_boundary.py tests/test_guest_package.py` | **27 passed**, one existing deprecation warning |
| `npm --prefix frontend test -- src/Guest.test.tsx src/guestEngine.test.ts src/guestTypes.test.ts src/Auth.test.tsx src/api.test.ts src/App.test.tsx` (before final extra tests) | **46 passed**, 6 files |
| `npm --prefix frontend test -- src/api.test.ts src/Online.test.tsx src/useOnlinePoll.test.tsx src/Auth.test.tsx` (signal correction) | **39 passed**, 4 files |
| `npm --prefix frontend test -- src/api.test.ts` (late body regression) | **4 passed** |
| `make check` | **Passed**, counts above; includes the final restored-side regression |
| `PLAYWRIGHT_CHROMIUM_EXECUTABLE=/usr/bin/chromium npm --prefix frontend run test:guest` | **13 passed / 1 asset-load stall** locally; all **14 passed** in CI below |
| `PLAYWRIGHT_CHROMIUM_EXECUTABLE=/usr/bin/chromium npm --prefix frontend run test:guest -- --grep 'anonymous guest\|guest survives'` (final production bundle) | **4 passed**, no skips; includes the previously stalled mobile case |
| `make docs`; `npm --prefix frontend run format:check` (final evidence follow-up) | **Passed**: 80 Markdown files / 587 local links/images/anchors; formatting passed |
| `.venv/bin/python scripts/audit_api.py` | **77 API grants accounted for**, no new endpoint/grant |
| `.venv/bin/ruff check scripts/prepare_guest.py frontend/guest/bridge.py frontend/tests/start_guest_backend.py`; same paths with `ruff format --check` | **Passed**, 3 files |

[Verify run 83](https://github.com/freeDom-/hnefatafl/actions/runs/40245502325)
passed both jobs on functional commit
`29de01a2a7dd801e93b12c6298f1e09eb3ae6ca5`. Its all-extras backend run passed
**1,470 tests / 6 skipped** (CUDA and Intel OpenVINO GPU hardware unavailable).
The browser job passed **303 unit tests / 37 files**, formatting/types/build,
**66 legacy browser checks / 4 intentional mobile duplicate skips**,
**6 registration**, **10 online**, and **14 guest** browser checks, with no
guest/registration/online skips. An initial CI failure exposed loss of caller
cancellation after response headers; native signal composition corrected it.
The final follow-up adds a late-response-body regression, reflects the validated
restored side in setup controls with a regression test, and completes runtime
library notices and this evidence. See [PR #15 checks](https://github.com/freeDom-/hnefatafl/pull/15/checks)
for the latest head's checks; this record does not relabel an older run.

The guest harness builds the production UI and serves it through real FastAPI
security middleware/SQLite, without a Vite CSP bypass. Test accounts and admission
enablement exist only in disposable development storage. Each browser context has
isolated storage. The loopback fixture controller counts real server rows; it is
not an application API. Desktop and mobile Chromium projects run sequentially.

Coverage includes accepted human and computer moves, reload without duplicate
moves, replay, zero guest API calls and unchanged server row counts; strict
document and Wasm-only worker CSP; no service-worker registration; accessible
confirmation/cancellation; atomic failed and successful replacement; explicit
clear retaining preferences/account selection; malformed/version/engine/illegal/
forged saves; computed terminal results and post-terminal rejection; unavailable
IndexedDB; stale-tab clear conflicts; login/logout/account switching, browser back,
preserved account history, no auto-upload, and online deep links avoiding the
guest adapter. Native tests additionally cover all three rulesets, strict bounds,
turn restrictions, exact private protocol and pinned packaging/update identity.

## Evidence limits and update policy

The final generated Pyodide package measures **14,058,059 raw bytes / 20 files**,
including Python/application source and notices. The executable/runtime payload
is unchanged by the final notice-only additions. In fresh browser contexts, one
page-navigation-to-engine-ready sample per project measured **11.420 s desktop /
6.649 s mobile emulation locally**, and **2.555 s / 2.271 s in CI**. These are
loopback observations without network/CPU throttling, not physical-phone or
general performance benchmarks. Local testing also encountered one stalled asset
transfer on a second mobile engine load; the trace showed pending runtime assets
before restoration. The final local targeted rerun passed that case on both projects; CI also passed
it along with the complete guest suite. Desktop (1280 px) and mobile (390 px)
screenshots were inspected; the final startup checks found no horizontal overflow
or browser console errors.

Only local/CI Chromium desktop and mobile emulation are claimed. No physical
phone, Safari, Firefox, production deployment, GPU, AI-strength, durable-storage,
PWA/installability or fully offline-startup validation is claimed. Fault injection
aborts real IndexedDB transactions but does not simulate power loss or guarantee
OS/browser durability. Optional/hardware skips retain their actual meanings.

Pyodide 0.27.7 is SHA-512 pinned and self-hosted with matching MPL, CPython and
Emscripten notices/source links. No extra Python packages or model weights are
fetched. Runtime binaries are generated/ignored build output. No production
service worker or persistent runtime cache is installed. Content-addressed assets
plus a no-store manifest and server responses avoid retaining an app-managed old
runtime cache. Replay compatibility changes fail closed and require explicit
recovery; the [guest guide](../GUEST_PLAY.md) defines the exact limits and upgrade
behavior. The repository's own distribution license is not newly assigned here.
