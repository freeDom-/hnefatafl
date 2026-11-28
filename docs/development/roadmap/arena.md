# Arena and platform roadmap

This page tracks remaining program-level acceptance work. Completed feature
implementation and validation are recorded in release notes, the
[browser-analysis roadmap](browser-analysis.md) and the
[historical completeness snapshot](../../releases/arena-snapshot-2026-11-20.md).

Active feature roadmaps keep their complete phase history until the feature is
fully developed and accepted. This program page only lists work that is still
outstanding.

## Current focus

| Work | Status | Source of truth |
| --- | --- | --- |
| Browser-local analysis Phase 6+ (offline resilience, then production acceptance) | Not started; external gates open | [Browser analysis roadmap](browser-analysis.md) |
| Combined release-candidate acceptance (capacity, recovery, security, 4K/200% zoom, full-theme/localization pass) | Not performed as one system | [Release readiness](../../releases/readiness.md) |
| Competitive browser checkpoints, physical devices, verified GPU, sustained memory and playing-strength evidence | Blocked on external resources | [Browser analysis roadmap](browser-analysis.md) |
| League Arena integrated release-candidate verification and administrator recovery drill | Merged; combined acceptance outstanding | [Leagues](../../game/leagues.md), [release readiness](../../releases/readiness.md) |

## Explicitly deferred or not implemented

- Offline play, a service worker and browser-offline runtime authorization.
- Single-tree browser batching and subtree reuse.
- Server-analysis decommissioning.
- Distributed self-play and PostgreSQL migration tooling.
- Durable UTC login/activity-day streaks.
- Commercial licensing or a managed-hosting offering.

## How to use this roadmap

Add only outstanding work, dependencies and acceptance criteria here. Link a
GitHub issue or pull request instead of maintaining a duplicate task inventory.
A completed standalone task may be removed after its durable knowledge is in the
canonical documentation. Do **not** remove completed phases from an active feature
roadmap merely because they were merged; see
[documentation guidelines](../documentation-guidelines.md#implementation-roadmaps).
