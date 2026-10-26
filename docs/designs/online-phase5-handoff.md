# Phase 5 handoff

[Documentation index](../README.md) · [Guest/storage guide](../GUEST_PLAY.md) ·
[Phase 4 evidence](../validation/online-phase4.md) · [Online operations](../ONLINE.md)

Phase 4 adds a public `/guest` surface outside Auth. `guestTypes.ts`,
`guestStore.ts` and `guestEngine.ts` own only the bounded device slot and worker
transport. `hnefatafl.guest` imports only the existing engine/AI, reconstructs all
positions/results and enforces turn/replay boundaries. The worker's private
protocol cannot choose arbitrary AI profiles, models, boards or identities.
Account and online adapters keep their existing IDs and server authority.

Retain the production-build guest browser suite and its real SQLite row/network
assertions alongside the existing account, registration and online suites. Storage
tests use real IndexedDB, including transaction abort after a revision write,
corrupt/unsupported saves, terminal replay, unavailable storage and stale tabs.
The HTTP fixture controller is separate and loopback-only, guarded by an isolated
development-data prefix; it is never an application route.

## Preserve in the next phase

- One current/last guest slot, confirmed replacement/clear, no archive, automatic
  migration, account import, upload or competitive/training accounting.
- Exact bounded schema and real Python replay. Engine/runtime upgrades need
  compatibility, corresponding license/source and browser-CSP review.
- Existing account selection/setup key formats, server histories, auth redirects,
  epoch cancellation and account-keyed protected state.
- Dedicated online routes/DTOs, transactional quotas, seat permissions, versions,
  deadlines, results and all-role active-analysis denial.
- Production admission and timeout defaults remain disabled. No service worker,
  PWA, installation or fully disconnected startup is delivered.

## Decisions still outside approval

The next phase's product scope needs an owner instruction. Public/spectator
online access, waiting-game retention/expiry, rated eligibility, ladder/rewards,
aggregate match scoring, completed-online-game analysis/import/export and timeout
outage handling remain separate policy decisions. Admission enablement, production
migration, deployment and release are separate actions. Do not infer their
approval from the guest single-slot decision.

If offline startup is requested, first specify runtime cache activation/update,
old-cache retirement, rollback/security response, storage eviction recovery and
license retention. Current Chromium desktop/mobile emulation does not establish
Safari/Firefox support, physical-phone performance or durable offline storage.

## Subsequent Phase 5 owner instruction

The owner approved the private Phase 5 dashboard and later directed the navigation/
history consolidation as a refinement on its existing branch/PR #17. Current
behavior is in [Dashboard](../DASHBOARD.md) and its
[refinement evidence](../validation/dashboard-consolidation.md). The pending public,
rated, retention, outage and deployment policies above remain separate decisions.

The additional owner polish is on the same PR: automatic device-only guest AI,
shared auth spacing, account-bound preset creation (admin-only AI pairs), modal
online duration entry with unchanged aggregate limits, and automatically recovering
lobby cards. Local-player CRUD and unsupported legacy restart cloning are removed;
stored identities/configurations remain read/play compatibility shims. The shared
neural catalog and authorized advisor/training/tournaments remain independent.
The refinement evidence identifies exact full/focused checks and local timeout
limits; final validation remains in progress there.
