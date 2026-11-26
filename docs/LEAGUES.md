# League policy and implementation contract

[Documentation index](README.md) · [Online authority](ONLINE.md) ·
[Arena completion plan](designs/arena-completion.md)

Policy version 1. PR #99 exposes League Arena at `/leagues`. Season standings
come from official League games played through the existing online interface.
Elo continues to use the normal rated online rules.

## Season and progression

Seasons are calendar months in UTC, with half-open boundaries `[start, next month)`.
A game belongs to the season in which the server pairs it. A season has separate
Brandubh, Tablut and Copenhagen standings; no cross-ruleset matchmaking. IDs and
boundaries are deterministic, but a read must not invent stored history or ranks.
A season is persisted only on a mutation that needs it.

League points: win +3, draw +1, loss -1, clamped at zero after each authoritative
result in stable result-ledger sequence. These measure seasonal progress, not
estimated playing strength. Thresholds:

| Tier | Minimum points |
| --- | ---: |
| Midgard | 0 |
| Nidavellir | 6 |
| Alfheim | 15 |
| Asgard | 30 |
| Valhalla | 50 |

A loss or result correction can demote a player; inactivity never subtracts points.
Season rollover resets league points, not Elo or permanent achievements. Show
recorded progress immediately, but qualify ranking only after three eligible
results. Do not fabricate an opponent, rank or percentile in an empty population.
Standings sort by points, wins and fewer losses. Equal sporting records share a
rank; account ID only stabilizes pagination. Selected portraits are published
through the existing safe portrait projection, not client-created asset paths.

## Permanent league achievements

Every supported league ruleset has its own five-tier achievement family: Brandubh,
Tablut and Copenhagen. The definitions are generated from the same authoritative
`league_policy.TIERS` used by the standings, so ordering, display names and point
thresholds cannot drift. The ordinary tiered achievement schedule supplies the
incremental points (10, 20, 30, 40 and 60). No second league-tier configuration or
separate reward system exists.

The server records a permanent highest tier per account and ruleset in
`league_achievement_progress` only after the player has three eligible league
results. Settlement awards the reached tier and every preceding tier in the same
transaction as the official standing update. Reaching the same tier again in a
later season cannot duplicate an award, and demotion, inactivity, season rollover
and later official corrections retain the permanent peak. A voided or corrected
result that has not yet settled cannot grant a tier. Progress in one ruleset never
affects another.

Schema 24 does not treat `league_contributions` as proof by itself. On upgrade it
queues one bounded reconciliation row per account, season and ruleset. The League
worker validates each row from `league_games` and effective official result
provenance: the durable game, applied rating revision and authoritative seats,
never cached standings or a partially cleared contribution table. Schema 25 adds
streaming cursor and accumulated-standing state; schema 26 adds scope-specific
evidence generations and league-sequence keyset indexes. A scope is verified in
fixed-size pages across short transactions, and unrelated settlements in the same
season do not invalidate accumulated work. It
folds eligible contributions in stable result sequence, applies the zero floor
after every result, requires three eligible results and records the highest
qualified tier. The process is idempotent and preserves existing awards. A
corrected historical peak that was never recorded before its superseding revision
cannot be reconstructed when the old evidence no longer exists.

Ordinary settlement evaluates a new result only when no previously settled result
for that account, season and ruleset awaits a correction, and no administrative
rebuild is in progress. Otherwise it stores the candidate peak in
`league_achievement_reconciliation` and leaves permanent awards unchanged. The
worker retries after the correction or rebuild settles, blocks rows with pending
corrections or a rebuilding season, and processes one account/scope per short
writer transaction. A correction that arrives before the reconciliation
transaction commits is seen by the same consistency check. A correction that
arrives after a valid award commits follows the permanent-achievement policy.

Administrative `fold_standings` writes candidate peaks only. They are validated
after authoritative catch-up, not from the incomplete recovery projection, so a
correction arriving between contribution restoration and folding cannot create a
permanent award. The existing durable rebuild cursor and bounded batches remain
unchanged. Unrelated accounts, rulesets and seasons retain their own rows and are
not blocked by one rebuilding season.

The Hall of Renown exposes an additive seasonal availability state. During an
administrative rebuild it keeps permanent tiers and earned achievements visible,
returns null seasonal points and qualification, hides seasonal progress bars, and
shows the existing localized rebuild message. After the rebuild completes, the
existing shared progression refresh shows the new standing without new polling.

Games qualify for seasonal scoring through the instant before the 24-hour grace
period ends. A game finishing exactly at the grace boundary is late. Existing
online clocks and results continue after that grace; late games retain their Elo
result but contribute no league points. Season summaries remain provisional until
grace ends. Later official corrections or voids can revise archived standings,
with a visible revision. There is no permanent freeze that hides an official error.

## Eligibility, queue and small populations

Only active, verified human accounts with normal play permission may queue.
Administrator accounts are human accounts too; administrative permission does not
bypass eligibility. AI participation is disabled. Existing tournament legs remain
casual and never enter league standings. League games use the normal rated online
engine and unchanged Elo pool, initial rating, K factor and provisional policy.

The fixed initial league clock is 600 seconds total per player with a 5-second
increment. Queue entry is explicit, ruleset-aware and globally unique per account.
An account may have at most one active league game, across all rulesets. Joining,
cancelling and matching are idempotent and serialized with authoritative online
writes. Queue entries expire after ten minutes or at the UTC season boundary, whichever
comes first; cancellation does not cancel an
already committed game. Cancellation targets its original queue request, so a
delayed retry cannot cancel a later entry. Return the official game link when
matching wins the race. New admission requests are limited to ten per minute
and one hundred per UTC day per account. Exact retries preserve the available
budget.

Pair the oldest mutually compatible eligible entries. Both players must accept
the rating window implied by their wait: initially 200 Elo, widening by 100 each
full minute to a maximum 600. Re-read eligibility, ratings, queue age, active-game
state and season under the write lock before seating. No self-pair, duplicate
seat or incompatible ruleset may commit. Waiting time must not silently relax the
maximum rating bound or substitute an AI. An empty queue explains the lack of
players, expiry and cancellation rather than promising an estimated wait.

Matchmaking processes bounded age-ordered pages in each ruleset. Each pass
examines up to eight starters and revalidates up to eight opponents per starter
from indexed rating buckets. It saves both positions and wraps at the end.
Within a background sweep, new arrivals can be opponents immediately, while the
starter horizon stays fixed so continuous arrivals cannot postpone wraparound.
A changed rating resets that player's opponent position. A wider window can
admit an earlier candidate when the scanner reaches it again after wrapping.
The scanner prefers the oldest entries it visits. Large queues and churn can
change the order relative to an exhaustive search.

On the one-second cadence the worker can start up to 16 pairing transactions
over a 60 ms burst, with a 3 ms handoff between attempts. Each transaction can
commit one match and waits up to 20 ms for the SQLite writer. An in-flight
transaction finishes even if the burst reaches its budget. Expiry and result
accounting run before the burst. Rulesets rotate between and within bursts.
Unfinished starter and opponent searches remain eligible for the next burst.
Completed searches with no match back off from one to eight seconds. When a
rating window can expose new opponents, the worker revisits at its next known
opening or after eight seconds, whichever comes first. SQLite contention
leaves discovery ready for the next tick. The worker clears its in-memory
schedule when it observes matching is unavailable or the pause version changes.
Waiting windows, seasons and expiry remain authoritative at commit.

Cap league pairings at three per unordered opponent pair per UTC day. Count
committed pairings even if a result is later voided, so corrections cannot reopen
an unlimited farming loophole. This conservatively implements the earlier
three-scoring-game proposal. It limits repeated pairing, not collusion across
accounts. Queue discovery may show a cooldown reason but never private account
metadata. A global administrator matching pause blocks new pairings, without
changing existing authoritative online clocks or results.

Side assignments use each account's committed attacker-minus-defender balance
for that ruleset. The player with the lower balance attacks. Among equal
balances the server alternates the pair's last assignment, with a stable
ruleset-and-account choice for a first meeting. The balances survive day and
season boundaries and count committed games. Queue order and the daily pairing
counter play no part in choosing sides.

The server retains each accepted request ID and its original queue generation
as an idempotency record. An additional ID for the same ruleset while queued
counts toward the request budget and refers to the same entry and expiry.
An exact retry returns that ID's original entry outcome and the current queue
and active-game state in one snapshot. A matched ID can outlive its game.
Cancellation acts on the entry associated with the submitted ID. A delayed
cancellation can therefore return an earlier outcome while a newer entry stays
queued. Request records have no time-based expiry. Rate checks read the current
rolling minute and UTC day using the account/time index.

Canonical queue snapshots also expose the account's last accepted request ID as
`join_generation`. An explicit browser join includes that value as
`expected_generation`, including null for the initial generation. An unseen
request with an older generation receives a conflict. A known request keeps
its idempotent outcome. The browser stores only the pending request UUID,
ruleset and expected generation under the current account. A fresh read can
retire a committed or superseded intent. Navigation, reload and reconnect
reconcile by reading the queue. Recovery offers an explicit retry with the
original request data or a discard. With multiple unresolved requests, tabs
show the first UUID in sorted order. A discard of an unseen current request
records that UUID as cancelled and advances the generation, fencing the
original and any guarded sibling requests with the same expected generation.
New withdrawal records have their own per-account allowance of ten per rolling
minute and one hundred per UTC day. Exact retries and superseded discards need
no new record. Existing request records keep their original accounting.
The browser keeps the pending intent when withdrawal is unconfirmed. A
committed join can still be cancelled by its original request
unless it has already produced a game. Joining remains an explicit action.

## Results, revisions and recovery

Use one server-owned provenance record per official online game, carrying season,
ruleset, players, pairing time and effective result revision. The normal online
result transaction durably enqueues affected league accounting. No browser is
needed to settle results. An eligible applied rated result can contribute once;
corrections replace that contribution and voids withdraw it. Never add a second
rating application or compute the winner in the client.

Because the points floor is nonlinear, a correction must replay the affected
player's season contributions in authoritative sequence, not subtract a guessed
delta. Persist stable ledger order and idempotency keys. Account/season lookup and
queue/result discovery must use indexes. Worker batches, retries and outstanding
work must be bounded; avoid recurring whole-history scans. Old-season matching is
forbidden at rollover even while old games finish during grace.

An eligible new contribution can extend the current standing when its sequence
follows the indexed ledger tail and the standing records that tail. Replacements,
gaps and out-of-order results replay the affected player ledger. Progress without
eligible results has no earned tier or rank. Archived standings stay provisional
during grace or while results are pending. Settlement advances the revision when
it changes the season's records.

Administrators may inspect queue/settlement health, pause matching, cancel stale
entries and request an audited version-checked season rebuild with a private
reason. Official game corrections and voids use the existing online authority;
league controls cannot invent winners. Recovery must survive restart and browser
absence, preserve corrections, and be testable without live accounts. Restore
inventory must include every new league table in dependency order. The next schema
number must be chosen from current main, not an earlier branch's proposed number.
No developer database, games, training job, checkpoint or model is deleted to
introduce leagues.

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
See [League worker recovery](DEPLOYMENT.md#league-worker-recovery).

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
