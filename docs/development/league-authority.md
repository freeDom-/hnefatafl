# League authority and recovery

[Development index](README.md) · [League guide](../game/leagues.md) · [Online operations](../administration/online-operations.md)

This page contains internal League API, worker/recovery and acceptance details.
Player-facing policy is in [Leagues](../game/leagues.md).

## API and recovery

The authenticated API uses the session account for personal progress, history
and queue operations. Requests use a supported ruleset, an optional season ID
in `YYYY-MM` format where applicable, and a page size from 1 to 50 for lists.
List responses include an opaque `next_cursor`. Reload from the first page when
the server reports a changed revision or the process has restarted. Standings
keep one shared sporting rank for tied players across page boundaries.
The Arena sends its expected account in `X-League-Account`. If a different
session has replaced the cookie, the server returns a conflict before reading
or writing League data for that session.

The season selector applies to personal progression, standings and history.
Matchmaking stays in the current UTC season. The shared **Refresh leagues**
control updates the visible projections. Focus, visibility and reconnect also
wake the reads. While idle, only the queue has a recurring one-minute read so
it can discover remote session activity, eligibility, pause or clock changes.

| Method and path | Result |
| --- | --- |
| `GET /api/leagues/current` | Current UTC month, policy, per-ruleset progress and current queue |
| `GET /api/leagues/seasons` | Persisted seasons with status and revision |
| `GET /api/leagues/progress` | Own seasonal results, tier, qualification and rank |
| `GET /api/leagues/standings` | Qualified standings with published identity and portrait |
| `GET /api/leagues/history` | Own official games, result eligibility and authorized game/replay links |
| `GET /api/leagues/queue` | Account eligibility, queue, server time and active game |
| `POST /api/leagues/queue` | Join with a ruleset and request UUID |
| `POST /api/leagues/queue/cancel` | Cancel or retrieve the outcome of the specified request UUID |
| `POST /api/leagues/queue/discard` | Resolve a pending join with its ruleset, UUID and expected generation |
| `GET /api/leagues/active-game` | The current official League game, if any |
| `GET /api/leagues/admin/health` | Queue, worker, settlement and rebuild status |
| `GET /api/leagues/admin/audit` | Private append-only administration history |
| `POST /api/leagues/admin/matchmaking` | Set paused state with its current version and a reason |
| `POST /api/leagues/admin/queue/cancel` | Cancel a stale entry by its original UUID and a reason |
| `POST /api/leagues/admin/seasons/{season_id}/rebuild` | Schedule a rebuild with the current season revision and a reason |

The League worker shares the online lifespan configuration and periodically
checks indexed due work, queue expiry and matchmaking. Administrative pause
blocks queue admission and pairing. The worker continues expiry and bounded
eligibility reconciliation during administrative pause, disabled admission and
clock pause. Existing gameplay and results continue according to the online
authority and its clock gate. Deployment contention leaves pending writes
available for a later cycle.

The administration panel shows worker liveness, the last check and successful
processing timestamps, deployment deferral, pending and retrying settlements,
and the age of the oldest pending result. Rebuild jobs show their stage and
durable attempt count and next retry. The stale queue list pages through the
expiry index up to the first page's server time, then scans bounded ranges for
disabled, unverified and otherwise ineligible accounts beyond that expiry
boundary. Each page uses live queue rows and current account eligibility.
Removed entries disappear and restored eligibility takes effect on unread
pages. **Refresh league health** and **Refresh leagues** start a new stale
queue traversal to show newly expired entries and changes behind the cursor.
Automatic health polling retains the current stale page. The audit keeps its
own pagination. Cancellation checks current staleness and targets the exact
queue request UUID.

For a projection
repair, submit the season revision displayed by the server and a reason. The
worker clears and reconstructs contributions and standings in durable batches
from League game provenance and effective rated results. A running rebuild
reports the season as rebuilding. The worker catches up intervening result
changes before it publishes the repaired standings and their new revision.
Administrative audit and League authority participate in compatible restores.
See [League worker recovery](../installation/updating.md#league-worker-recovery).

## Acceptance gates

Database tests must prove simultaneous queue joins/cancels, duplicate request IDs,
self-match exclusion, cross-ruleset isolation, distinct player seats, inactive or
unverified accounts, window growth/caps, expiry, pair caps, season/grace boundaries,
result retries/corrections/voids, nonlinear-floor rebuild, restart and restoration.
Read endpoints remain bounded and account-safe; unknown routes remain denied.

The frontend must show season/tier progress, qualified standings, official match
history, explicit queue/cancellation/expiry, match links, promotion/demotion and
empty-population states. Administrator monitoring must be permission-gated.
Reuse Arena themes, tier artwork, shared controls, focus and reduced-motion
behavior; support English/German and small screens. No live online advisor is
introduced. Domain and browser checks must pass before this contract becomes a
claim that league gameplay is delivered.
