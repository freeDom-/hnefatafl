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

Final command results and CI evidence are recorded here after completion.

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
