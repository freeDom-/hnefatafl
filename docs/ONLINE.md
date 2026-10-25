# Online gameplay and durable core

[Documentation index](README.md) · [Phase 0 contract](designs/online-phase0.md) ·
[Security](SECURITY.md) · [Phase 2 evidence](validation/online-phase2.md) ·
[Playing online](ONLINE_PLAY.md) · [Phase 3 evidence](validation/online-phase3.md) ·
[Phase 4 handoff](designs/online-phase4-handoff.md)

Phase 2 provides authenticated backend gameplay. It reuses the existing engine,
pure lifecycle, accounts and SQLite transactions. Phase 3 adds the authenticated
lobby and playable UI described in [Playing online](ONLINE_PLAY.md). Anonymous
access, online advice, Elo, aggregate match scoring and deployment remain absent.
Account single-player, training, workers and model records keep their existing
tables and behavior. The old local `game_participants` scaffold references local
`games`; online seats therefore live in the separate authoritative online tables.

## Confirmed behavior and owner decisions

The Phase 0 contract governs distinct participants, turn order, concession,
cancellation, terminal engine moves, timeout boundaries and audited voids.
The owner explicitly refined/approved these Phase 2 policies in conversation:

- Creators choose attackers, defenders or **random**. Random assignment happens
  once, inside the successful join transaction; it is not assigned at creation or
  rerolled by reconnects/stale requests. The ruleset determines the first mover.
- A **two-game match** replaces the ambiguous name “full-set”. Its modes are
  `simultaneous` (both games activate when the opponent joins) and `sequential`
  (the return game activates when the first completes). `single` remains available.
  The return game swaps the admitted accounts' sides, using the same rules/version
  and time control. Each game has its own identity, version, deadline and result.
- Configuration freezes at creation: rules/version, side-selection policy, mode
  and time per move. Cancel/recreate instead of editing the waiting configuration.
- Verification gates admission. An activated player who later loses verification
  retains existing-seat moves/concession while active with a valid session. This
  includes the already-agreed return game; it is not another open join/admission.

Anonymous visibility, waiting-lobby retention/lifetime, rated eligibility and the
production outage-resolution procedure remain **unapproved**. Anonymous access and
rated input are rejected. No automatic waiting-game expiry or deadline extension
is implemented. Keeping a durable waiting record is not approval of a future
public-lobby retention policy. Production automatic forfeits start disabled.

Each game in a two-game match contributes independently to derived online counts;
there is no match-level winner, Elo or score. Administrative void applies to the
specified game. It does not silently void a sibling or reopen a completed game.
A first game voided/cancelled before completion does not start a sequential return;
voiding it after completion does not rewind the already-started return. Operators
must inspect both linked games when deciding an incident's resolution.

## Configuration and time

| Environment setting | Default | Meaning |
| --- | --- | --- |
| `HNEFATAFL_ONLINE_ENABLED` | `false` | Admission for create/join; does not strand existing seats or pause clocks |
| `HNEFATAFL_ONLINE_TIMEOUT_POLICY_APPROVED` | `false` | Operational prerequisite for timeout finalization; set only after separate owner approval of an outage procedure |
| `HNEFATAFL_ONLINE_SWEEP_SECONDS` | `10` | Background interval, integer 1–300 |
| `HNEFATAFL_ONLINE_SWEEP_BATCH` | `50` | Maximum candidate games per pass, integer 1–256 |
| `HNEFATAFL_ONLINE_WAITING_LIMIT` | `5` | Maximum outstanding waiting lobbies per creator, integer 1–1000 |
| `HNEFATAFL_ONLINE_CREATION_LIMIT` | `10` | Successful new matches per account/window, integer 1–1000 |
| `HNEFATAFL_ONLINE_CREATION_WINDOW_SECONDS` | `900` | Rolling creation window, integer 60–86400 seconds |

No deployment configuration is enabled by this phase. The timeout prerequisite
does not itself resume the persistent gate: both controls must allow finalization.
It is checked by the request and sweeper paths, including after restart and across
connections. Mixed processes with the prerequisite disabled always fail closed;
deploy one API process with consistent configuration, as before.

Time per move defaults to **259200 seconds**, inclusive range **60–1209600**.
Only actual integer JSON/service/SQLite values are accepted; booleans, strings,
floats (including `60.0`) and fractional durations are rejected. SQLite uses
non-coercing affinity plus type/range checks. UTC instants are integer Unix
microseconds; JSON uses six fractional digits and `Z`.

The first deadline is activation plus the full duration. A timely nonterminal move
gives the next player a fresh duration. At `now >= deadline`, move/concession is
too late. Completion clears the active deadline. Logout, disconnect and restart
do not reset time. Waiting games have no deadline. Late timeout processing records
completion at the original deadline, with processing/revision time separate.
The sequential return activates at the first game's actual processing/acceptance
time, with its own full duration; this does not extend the first game's deadline.

The write lock precedes the authoritative clock sample. Candidate-sweep selection
is only a hint; each candidate reloads and resamples inside its own write transaction.
Backwards time relative to the game's last transition or timeout-control audit
fails closed. This does not detect every possible host-clock adjustment. Monitor
clock synchronization and investigate anomalies rather than rewriting timestamps.

## Creation quotas and monitoring

Verified accounts, including administrators, may have at most five waiting lobbies
and create ten matches per rolling fifteen minutes by default. Both checks use
indexed persisted records under the same `BEGIN IMMEDIATE` as creation. Admission
scans stop at the configured cap even when upgrading an account already above it.
Competing connections/processes cannot both take the last slot. HTTP 429 indicates
the caller's waiting or creation-rate limit; it creates no match/game rows.

Activation, cancellation or pre-start void frees a waiting slot. Cancellation and
void retain the creation record and do not refill the rolling rate budget. A record
expires from that budget at exactly creation time plus the window. Successful
creations spend budget; transaction failures do not. Duplicate creation UUIDs still
return 409 before quota checks and do not spend budget or count as quota denials.
Limits are per account across all rulesets/modes. A two-game match is one creation;
its automatic return game uses the existing admission and spends no additional slot.
Set consistent limits across API processes. Zero/invalid values are rejected;
use the existing admission switch to disable new games.

`GET /api/online/monitoring` requires a current administrator session. It reports
active configuration, cumulative quota-denial counts/last-denial times, match/game/
move/result totals, game-state counts and SQLite allocated/used/free bytes. Main
database and WAL file bytes are reported separately; logical SQLite usage and file
sizes are operational trends, not one atomic filesystem snapshot. This read does
not change limits, counters or games. It exposes no account IDs, emails, credentials,
file paths or administrative notes. Normal users and anonymous callers are denied.

Two fixed rows in `online_creation_metrics` retain denial counts across connections
and restart; no attacker-controlled limiter keys are allocated. A rejected creation
commits only this bounded telemetry before returning 429. Sanitized warning messages
contain only a fixed reason and cumulative count, at most once per reason/minute
across processes and restart. Every quota denial still increments its counter when
the database commit succeeds. Collect monitoring snapshots periodically (for example
once a minute), alert on counter increases and database/WAL growth or low disk space,
and set alert thresholds for the deployment's capacity. No external monitoring
service, automatic deletion, lobby-expiry policy or data-retention approval is added.

Schema 10 gains optional monitoring state and two indexes without rewriting
authoritative rows or changing their layout. Existing schema-10 databases initialize
them on startup. Compatible recovery also accepts older schema-10 snapshots that
lack monitoring state: it retains live creation records/budgets and available live
denial counts. Snapshot-only recovery can retain only the counters present in that
snapshot. This observability addition does not make older authoritative schemas
compatible or weaken the account/online rollback boundary.

## Persistence, authorization and concurrency

[online_migrations.py](../hnefatafl/online_migrations.py) introduces schema **10**:
`online_matches`, `online_games`, ordered `online_moves`, append-only
`online_results`, private `online_void_audit`, singleton `online_timeout_gate` and
append-only `online_gate_audit`. Constraints protect distinct seats, one game per
match/leg, creation-request uniqueness, duration types, lifecycle shape, activated
identity/configuration immutability and result revision continuity. The effective
result pointer has a deferred foreign key, permitting state/result atomic commits.

[online_core.py](../hnefatafl/online_core.py) acquires the existing deployment gate
and `BEGIN IMMEDIATE`, reloads the presented session and current account, loads
the lifecycle/participants/version, then samples/uses server time. All moves,
seat activation, deadlines, version changes, result revisions, private void audit
and any return-game creation commit together. Python locks are not the authority.
SQLite's existing 15-second contention timeout remains; transient failures return
a sanitized unavailable response and clients reload before retrying.

Create/join require `online_eligible`, including administrators. Activation also
rechecks the waiting creator. Moves/concession require the participant whose side
is to move, an active account and a current non-revoked session. Administration
does not impersonate a seat. Spectators cannot mutate. The original middleware
account snapshot cannot authorize a write after a concurrent session/status/role
change: the trusted session digest is rechecked inside the transaction.

Every mutation of an existing game requires its integer `version`. Stale versions
conflict without changing state. A response lost after commit may leave the client
uncertain; reload the game/listing, then use its current version. Retrying the old
version cannot append another move/result/return game or fabricate duplicate
success. Creation requires a client UUID `request_id`; repeated use by the same
creator conflicts, and `GET /api/online/games?mine=true` recovers the identity.
HTTP delivery is not exactly once; committed logical transitions are at most once.

Statistics derive from each game's **effective eligible** result, rather than
incrementing counters. Timeout attributes one abandoned game to the timed-out
account only; concession contributes none. Cancelled/pre-start voided games have
no result. A started void retains the prior revision, replaces effective eligibility
and removes all of that game's derived contributions. No materialized projections,
reward consumers or generic event log are introduced.

Ruleset/version is pinned before any engine state loads or replay executes.
Unsupported stored versions fail with 503 and require compatible code/operator
review. They are never silently replayed under current rules. Rule-semantic changes
must bump the engine's rules version and provide an explicit compatibility path.
The implementation currently supports only each installed ruleset's current version.

## API and information boundary

All routes below require the existing session and `play` permission; void and gate
controls require `accounts` permission. Mutations require same-origin JSON, CSRF,
strict allowlisted bodies and an 8192-byte limit including chunked requests.
Validation/database failures suppress raw inputs and private notes. Exact grants
are in [the API inventory](API_PERMISSIONS.json).

| Method and path | Body / purpose |
| --- | --- |
| `POST /api/online/games` | Create waiting game/match: request UUID, ruleset, side selection, mode, optional duration |
| `GET /api/online/games` | Authenticated listing: limit 1–100 (default 25), offset 0–100000, optional `mine=true` |
| `GET /api/online/games/{id}` | Dedicated safe authoritative projection |
| `POST /api/online/games/{id}/join` | `version`; admit opponent and activate one/two games |
| `POST /api/online/games/{id}/cancel` | `version`; creator while waiting |
| `POST /api/online/games/{id}/moves` | `version`, integer `source`/`target`; engine validates |
| `POST /api/online/games/{id}/concede` | `version`; only the player to move |
| `POST /api/online/games/{id}/void` | `version`, private `note` of 1–1000 nonblank characters; administrator |
| `GET /api/online/games/{id}/replay` | Deterministically validated read-only states; no shared cursor |
| `GET /api/online/stats` | Own effective online counts, separate from single-player |
| `GET /api/online/monitoring` | Administrator-only quota denials, aggregate counts and database/WAL growth |
| `GET /api/online/timeout-gate` | Administrator-only current/effective gate and latest 50 private audit entries |
| `PATCH /api/online/timeout-gate` | Administrator: current gate `version`, boolean `enabled`, private `note` |

Mutations return `{game, transition, action_accepted}`. A late authorized action
can return `transition: "timeout", action_accepted: false` after finalizing timeout;
the requested move/concession was not accepted. With finalization paused it returns
409 and leaves the overdue Active record/deadline unchanged. Wrong actors are
rejected before any expiry mutation. An authorized repeated void at the current
version is an explicit no-op; an older version still conflicts.

Read/list/replay GETs do not mutate or consume timeouts. An overdue game may remain
Active pending the shared request/sweeper path or incident review. Do not infer a
winner from a client countdown or only from `position.winner`: concessions/timeouts
do not invent an engine winner. Use lifecycle and effective `result`.

Game/result/participant projections contain explicit safe fields only: stable IDs,
display name, side, rules/version, board, accepted moves, times, lifecycle, per-game
version and effective result. Private emails, roles/status, security/login data,
credentials, administrative notes, advisor configuration, hints, probabilities and
search diagnostics are excluded, including replay states. Gate audit is private
administrator operations data and is never bundled into game projections.

Online IDs cannot enter local game/AI/analysis/analytics/seek/undo/restart/export
adapters, including for administrators. Separate tables also exclude them from
local collections, tournament replay and training exports. No arbitrary-position
analysis endpoint or online assistance endpoint exists. Safe completed/played-voided
replay is available; online analysis remains a later lifecycle-aware adapter.
Pre-start replay contains only the initial board, not a played line or advice.
Publicly shared board positions cannot prevent external AI assistance.

## Pause, inspect, resolve and resume

Automatic production forfeits are not approved by this work. Until an approved
outage procedure exists, keep the timeout-policy environment flag false and the
persistent gate paused. Deterministic tests explicitly inject an approved fixture.

1. Using a current administrator session, read the protected timeout-gate endpoint.
   Submit PATCH with its current version, `enabled: false` and an incident/maintenance
   justification. Confirm `effective_enabled: false`. This transaction is shared
   across request and background paths and survives restart.
2. Inspect affected game IDs, deadlines and result revisions through safe reads;
   inspect private notes only through authorized operations access. Gate audit,
   counts and timestamps may be inspected locally by the trusted data owner.
   Do not log request bodies, account tables, cookies or audit-note dumps.
3. Overdue play remains rejected while paused. Under a separately approved incident
   decision, an administrator may void affected games with expected versions and
   private justification. Check all games in a linked match. No arbitrary winner
   correction, reopening or deadline-extension operation is provided.
4. Resume only after owner approval of the outage procedure, clock/state review and
   operator reconciliation. Set the policy prerequisite in protected configuration,
   restart consistently, then explicitly PATCH the persistent gate to true with its
   current version and justification. Inspect the bounded sweeper's results. Old
   deadlines still apply; this action does not compensate for downtime automatically.

Pause before planned maintenance, then use the existing deployment freeze/stop.
The freeze blocks HTTP and background writes, including startup reconciliation.
Disabling admission is not a substitute for pausing timeout finalization.

## Migration, backup and recovery

Use the [full stopped-writer backup procedure](TRAINING_DATA.md#consistent-backup-and-restoration),
including protected environment and matching code. Schema 10 adds no interpretation
of local histories and preserves account IDs/security, ownership, sessions, jobs,
workers/models and files during forward migration from schemas 9/8/older fixtures.
Legacy normalization collision checks still run and stop ambiguous upgrades.
No production database was inspected or migrated for development.

Schema-9 code refuses schema 10. Additive tables are not a safe downgrade guarantee.
The schema-10-aware updater refuses automatic recovery across differing online
schemas/columns before replacing the live root; account-schema protections remain.
An empty/pre-database snapshot also refuses replacement of a live database.
Use a forward repair or separately reviewed manual reconciliation, retaining both
roots and matching code snapshots. Do not use older recovery tooling over a live
schema-10 installation or silently discard accepted online moves/results.

For a compatible stopped-writer restore with a live database, the restore candidate
retains **current live** accounts, limiter state and all authoritative online match,
game, move, result and audit rows. It restores other snapshot data, revokes all
sessions/verification-reset tokens/mail and worker credentials, then pauses timeout
finalization with a new private restore audit. Online deadlines/results are retained,
not rewound or extended. Append-only guards are reinstated before commit/cutover.

A snapshot-only disaster restore without live authority cannot recover accepted
post-snapshot moves, account changes or results. It still revokes saved capabilities
and audits/pauses timeouts before startup, but requires explicit operator/owner
reconciliation of lost progress and eligibility before resuming. Never claim this
is equivalent to preserving the current live state. Keep the restore isolated until
matching-code readiness, security revocation, safe replay and gate checks pass.

Startup and periodic work call the same transactional expiry implementation as
gameplay. Startup processes one bounded batch; subsequent batches run periodically.
Restarts do not reset deadlines or forget the persistent gate. Unsupported rules,
clock anomalies or frozen deployment block affected processing; investigate rather
than treating them as successful finalizations. There is no broker or transient
timer authority, and no exactly-once HTTP/mail claim.

## Frontend availability and state ownership

`GET /api/online/availability` requires a current session and `play` permission.
It returns exactly `{enabled, eligible}`: `enabled` combines the configured
admission switch and approved creation policy; `eligible` uses
`security_context.online_eligible`. It does not reserve admission and may change
before a write. An ineligible account can still use its existing seat subject to
normal session/account/turn checks. No administrator monitoring is exposed.

The UI has separate online types/state and abortable reads. A selected game cycle
reads its sibling independently even if the selected version did not change.
Mutation bodies contain only API-defined version/coordinates, or the explicit
creation intent fields. A response's `transition` and `action_accepted` control
feedback, and an effective lifecycle result controls terminal presentation.
Replay positions never enter the mutation path or App's local-game state.

Poll intervals are five seconds after success, ten/twenty/thirty seconds after
failures, with a ten-second read deadline. Focus/online/manual refresh coalesces
to one queued cycle. Writes have a fifteen-second client wait bound; abort is not
a server rollback, so uncertain writes reload and are never automatically retried.
Only the frozen creation request can be deliberately resubmitted.
