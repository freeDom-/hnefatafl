# Phase 6 handoff

[Documentation index](../README.md) · [Phase 5 contract](personal-dashboard-phase5.md) ·
[Dashboard guide](../DASHBOARD.md) · [Phase 5 evidence](../validation/online-phase5.md)

Phase 5 delivers a private authenticated dashboard, separately scoped account and
online pages, and read-only review. `personal.py` performs bounded set-based summary
queries and query-derived totals. `personal_api.py` declares five authenticated
GET adapters; there is no schema change or stored accounting system. Account
replay reuses existing validation; the online review reuses the dedicated viewer
with participant-only reads. Generic viewer controls contain no game adapter.

Preserve the Phase 4 guest suite and one-slot policy, session-epoch/cancellation
checks, strict personal scope even for administrators, legacy Hall/API permissions,
existing authenticated spectator behavior, quotas, immutable online configuration,
seats, expected versions, deadlines, result revisions and all-role analysis denial.
Keep terminal lifecycle/effective result distinct from the engine board outcome.
The dashboard consumes only current eligible per-game results; void revisions
withdraw previous contributions without materialized counter repair. Page cursors
are positions tied to account/filters, not snapshots or access capabilities.

Production admission and timeout finalization remain disabled by default. There
is no rating, leaderboard, public history, match score, completed-online analysis,
retention/expiry policy, release or deployment approval in Phase 5.

## Owner decisions required before ratings

No values below are approved or implemented. A future ratings instruction must
settle the complete policy and its versioning before changing stored results:

| Decision | Questions to resolve |
| --- | --- |
| Initial rating | Starting value, rating pools by ruleset/side/time control, treatment of existing accounts and historical games. |
| K and provisional policy | Update formula, K schedule, provisional duration/uncertainty, activity/decay policy and whether new accounts affect established accounts differently. |
| Draws | Scoring for each approved draw reason and handling of administrative outcomes. |
| Repeated opponents | Whether/how repeated pairs, side-swapped legs, short games and deliberate farming affect eligibility/weight. No aggregate match score currently exists. |
| Eligibility | Explicit rated opt-in/configuration, verified/account state at which times, allowed rules/time controls, concession/timeout, minimum play and moderation/anti-abuse treatment. Current `rated_eligible` remains false. |
| Ordering | Stable event order for simultaneous finishes, delayed timeout adjudication, ties and backfilled/corrected results; freeze/serialization boundaries across workers. |
| Correction/rebuild | How a void/correction retracts prior effects; deterministic rebuild from effective revisions, policy versions, idempotency, checkpoints/audit, reconciliation and restore/rollback behavior. |

Do not infer competitive approval from the existence of eligible W/L/D counts.
`competitive_eligible` supports current personal statistics; `rated_eligible`
remains separately denied by policy. Existing statistics and record ownership must
not be silently reinterpreted as account-relative single-player skill.

Public/spectator history, waiting-game expiry, rewards, completed-online assistance,
offline startup/cache policy, production migration/admission, timeout outage policy,
deployment and releases remain independent owner decisions. Next-phase tests must
retain real API/session authorization and production-build guest/dashboard browser
checks, including delayed account switches, no guest reads/uploads, void withdrawal
and replay without authoritative writes.
