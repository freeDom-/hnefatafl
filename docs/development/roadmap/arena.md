# Arena and platform roadmap

This is the current remaining-work roadmap. Delivered capability matrices are
historical snapshots and release evidence, not a second current manual.

## Current focus

| Work | Status | Source of truth |
| --- | --- | --- |
| Browser-local analysis Phase 6+ (offline resilience, then production acceptance) | Not started; external gates open | [Browser analysis roadmap](browser-analysis.md) |
| Combined release-candidate acceptance (capacity, recovery, security, 4K/200% zoom, full-theme/localization pass) | Not performed as one system | [Release readiness](../../releases/readiness.md) |
| Competitive browser checkpoints, physical devices, verified GPU, sustained memory and playing-strength evidence | Blocked on external resources | [Browser analysis roadmap](browser-analysis.md) |
| League Arena integrated release-candidate verification and administrator recovery drill | Merged; combined acceptance outstanding | [Leagues](../../game/leagues.md), [release readiness](../../releases/readiness.md) |
| Dependency advisory risk acceptance / removal | Time-limited; owner action | [Security advisory(../security-advisory.md) |
| Historical branch/docs cleanup and canonical schema inventory | Ongoing maintenance | This PR / release notes |

## Explicitly deferred or not implemented

- Offline play, a service worker and browser-offline runtime authorization.
- Single-tree browser batching and subtree reuse.
- Server-analysis decommissioning.
- Distributed self-play and PostgreSQL migration tooling.
- Durable UTC login/activity-day streaks.
- Commercial licensing or a managed-hosting offering.

## How to use this roadmap

Add only actionable remaining work, dependencies and acceptance criteria. Link a
GitHub issue or pull request instead of maintaining a duplicate task inventory.
When work completes, move durable design/evaluation knowledge to the canonical
guide and remove the completed item. See
[documentation guidelines](../documentation-guidelines.md).
