# Phase 6 handoff

[Documentation index](../README.md) · [Phase 5 contract](personal-dashboard-phase5.md) ·
[Dashboard guide](../DASHBOARD.md) · [Phase 5 evidence](../validation/online-phase5.md)

Phase 5 delivers the default authenticated Dashboard with reused account setup and
online lobby, separate personal statistics, compact own recent records and read-only
Hall review. `personal.py` retains own-account summaries/totals; `personal_api.py`
retains five authenticated personal GET adapters and adds two read-only history
adapters. `history.py` enforces participant online scope for normal users and trusted
`all_data` Hall browsing for administrators, while Dashboard stays own-only.
Shared rows link to independently URL-selected canonical review. Existing private
account/online replay deep links remain, and viewer controls change local state only.
There is no schema change or stored accounting system.
Account totals read only persisted `recorded_result` metadata. No legacy account
records or engine result reconstruction are supported; missing/null results remain
unfinished. Canonical single-game validation is retained. Unexpected personal API
failures log scrubbed type/traceback plus fixed action IDs; HTTP details remain
redacted. Global authenticated online listing/detail intentionally allow spectators.

Preserve the Phase 4 guest suite and one-slot policy, session-epoch/cancellation
checks, strict personal API scope even for administrators, role-scoped Hall reads,
existing authenticated spectator behavior, quotas, immutable online configuration,
seats, expected versions, deadlines, result revisions and all-role analysis denial.
Keep terminal lifecycle/effective result distinct from the engine board outcome.
The dashboard consumes only current eligible per-game results; void revisions
withdraw previous contributions without materialized counter repair. Page cursors
are positions tied to account/filters, not snapshots or access capabilities.

Production admission and timeout finalization remain disabled by default. There
is no rating, populated leaderboard, public history, match score, completed-online analysis,
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

## Consolidated Dashboard integration point

The owner classified the pre–Phase 6 consolidation as a refinement of Phase 5 on
PR #17. Dashboard is now default/post-login with reused single-player setup and
online lobby, distinct personal totals, shared latest-three Recorded games and a
small **Leaderboard — Upcoming in Phase 6** placeholder. Integrate future approved
leaderboard presentation there; do not create another landing/setup surface.

Hall now uses shared compact rows and independent URL-selected canonical review.
Normal users have participating online history only; administrators use trusted
`all_data` history reads across server categories/accounts. Administrator Dashboard
history stays own-only. Existing global authenticated lobby/spectator visibility
is separate and unchanged. History visibility adds no assistance/mutation/exports.
The Local players UI is removed; setup profiles/APIs and historical data remain.
All rating-policy decisions above remain unapproved. See
[refinement evidence](../validation/dashboard-consolidation.md).

## Account setup and guest/lobby refinement

Current account creation binds the human seat to the signed-in account and resolves
Shield bearer, Shield maiden, Jarl or Seer on the server. Normal accounts create
Human vs AI; current administrators can also create/restart AI vs AI. Creation and
restart recheck current identity/role transactionally. Removed local-player CRUD,
custom/neural account-creation controls and old preference fields must not become
alternate creation paths. Historical modes, player IDs and stored model/configuration
snapshots remain read/play compatibility data; unsupported saved setups cannot be
cloned through restart. Authorized advisor/training/tournament controls retain the
full catalog and their model/search configuration independently.

Guest AI now advances automatically only at the live, nonterminal AI turn, including
validated restore. Keep explicit retry, operation-generation/cancellation guards,
accepted-move saves and the one-device-slot policy. Shared modal cleanup restores
focus to a connected opener. Online creation accepts day/hour/minute components in
the client while the authoritative aggregate remains 60–1209600 seconds; retained
sub-minute requests keep exact seconds. Existing deadlines stay unchanged. Lobby
polling resynchronizes on reconnect/focus/visible tabs and exposes stale/retry state
without manual Refresh or a persistent connected banner.

See the [refinement evidence](../validation/dashboard-consolidation.md) for exact
source revisions, full/focused results and local timeout limitations. These UI and
creation decisions approve no rating, deployment or production gate change.
