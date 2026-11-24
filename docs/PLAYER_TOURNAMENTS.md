# Player tournaments

[Documentation index](README.md) · [Online play](ONLINE_PLAY.md) · [Rated games](RATINGS.md)

Human player competitions live under `/api/player-tournaments/*` and use their
own tournament tables. Workshop [AI tournaments](TOURNAMENTS.md) use a separate
API, database table and permission boundary for model comparisons.

## Frozen competition policy

- **Format:** double elimination only.
- **Elimination:** a participant is out after **two match losses**, never after
  one lost leg.
- **Match format:** new tournaments require two side-swapped games, either
  `sequential` (the form default) or `simultaneous`. Each game receives its own
  clock allocation; advancement waits for both results. Historical `single`
  tournaments and `legacy` configurations with 1–5 parallel legs retain their
  execution behavior, stored modes and recorded outcomes.
- **Grand final reset:** if the lower-bracket finalist wins the first grand
  final, a bracket-reset match is played between the same two participants and
  decides the champion.
- **No random winner:** every unresolved outcome is decided by an explicit rule
  (below). A random winner is never generated.
- **Rating isolation:** tournament games are casual (`rated = false`) and never
  write `rating_events`, Elo applications or rated-only achievement credit.

## Tie and draw handling

| Situation | Decision | Recorded as |
| --- | --- | --- |
| A participant wins more decided legs | that participant wins the match | `decided_by = play` |
| All legs drawn, or decided legs split with no majority | the **higher original seed** advances | `decided_by = higher_seed` |
| Grand final with no leg majority (all legs drawn or split) | the **undefeated upper-bracket finalist** advances; no reset is forced | `decided_by = undefeated_advantage` |
| Bracket-reset match with no leg majority | the higher original seed advances | `decided_by = higher_seed` |
| A match has only one participant because of a bye | that participant advances without a loss | `decided_by = bye` |
| An administrator forces a ready match | the named participant wins; its outstanding games are voided so nothing outlives the decision | `decided_by = admin_override` |

A voided leg counts as neither a win nor a draw, so a match whose legs were all
voided falls back to the seed rule instead of silently disappearing.

## Bracket construction

The bracket size is the smallest power of two at or above the registered entrant
count. Entrants are placed with the standard recursive seed order, so the top
seeds meet as late as possible. Seeds above the entrant count are byes: the
present participant advances without playing or taking a loss, and a match with
no participants propagates as a bye instead of stalling the bracket.

The lower bracket receives upper-bracket losers. Lower-bracket rounds alternate
between "drop" rounds (a survivor meets an upper-bracket loser, in reversed order
to reduce immediate rematches) and "consolidation" rounds. For a bracket of size
`2^U` the lower bracket has `2U - 2` rounds. A two-entrant bracket has no lower
bracket: the loser of the only upper match reaches the grand final directly.

Every match has a deterministic identity
(`<tournament>:<bracket>:<round>:<slot>`), so re-deriving the bracket never
duplicates matches or legs.

### Global tournament rounds

When a modern two-game tournament starts, it records synchronized progression.
Upper bracket round 1 plays in global round 1. Upper bracket round N and lower
bracket round N−1 share global round N whenever both exist. Remaining lower
bracket rounds each play in their own global round. The grand final follows the
last bracket round, and a required reset has the next global round. The
eight-entrant schedule follows.

| Global round | Matches |
| --- | --- |
| 1 | Upper 1 |
| 2 | Upper 2, lower 1 |
| 3 | Upper 3, lower 2 |
| 4 | Lower 3 |
| 5 | Lower 4 |
| 6 | Grand final |
| 7 | Reset, if needed |

All playable matches in a global round open in a single advancement
transaction. Completed matches, administrative decisions and byes count toward
the round's terminal decisions. The next round opens after the last outstanding
match is decided. The same transaction can pass through rounds consisting
entirely of byes. Already-started tournaments from before synchronized
progression and historical single-game or legacy formats retain independent
advancement as their individual feeder matches finish.

## State model

Tournament status:

```text
registration ──start──▶ running ──champion decided──▶ complete
     │                     │
     └──────cancel─────────┴──▶ cancelled
```

- `registration`: accepting entrants until capacity is reached or the tournament starts.
- `running`: the bracket is seeded; matches advance as authoritative results
  settle.
- `complete`: the grand final (or its reset) produced an official winner.
- `cancelled`: an administrator stopped the tournament. Every outstanding leg
  is retired through the authoritative online core in the same transaction, so a
  cancelled tournament has no playable, settleable or expirable game left;
  completed legs and their results stay as history.

`review_state` is `ok` or `disputed`. It becomes `disputed` when a settled leg's
authoritative result revision changes after it was recorded, or when a voided
game invalidates a settled leg. A disputed bracket stops advancing until an
administrator rebuilds it; a tournament that had already completed reopens to
`running` with no official winner so the correction can be reviewed.

A match is `pending` until its participants and round are ready. A `ready`
match has two known participants and can seat games. A decision records
`complete` or `bye`. A correction to a decided match can mark it `disputed`.

Leg outcome: `a`, `b`, `draw`, `void`, or unset while the game is unfinished.

## Arena interface

`/player-tournaments` is the human competition section, linked from the Arena
Trials of Valor sidebar and the full leaderboard on compact screens. The Workshop's
`/tournaments` remains AI evaluation. Discovery supports bounded cursor pages,
status, ruleset and planned upcoming filters. Details expose real participants,
seeding, losses, rules and clocks, champion, playable online legs and results.
Clock summaries use the shared days/hours/minutes/seconds formatter, preserving
historical second precision, and show the time-control type, increment and mode.
The upper/lower bracket, grand final and reset have source-based progression paths,
horizontal touch scrolling and an accessible list alternative. Before starting,
the bracket uses capacity to show a provisional tree with unassigned seed slots.
It creates no persistent matches, games or playable links. Starting replaces it
with the seeded bracket sized for the actual entrant count.

The detail and discovery responses provide `start_state` for registration and
scheduled-start status. The bracket response provides `round_progress` with
the global round number and counts of finished and outstanding matches. It also
shows which future matches are held by the round barrier and which sequential
return games are waiting for the previous result. Game and match reads remain
server-authoritative. Account-specific capabilities govern creator controls and
administrative moderation. The route and calendar load on tournament navigation
through the existing lazy-route/Suspense pattern.

## Scheduling and creator permissions

Active, verified accounts can create at most three active tournaments and five
in any rolling 24-hour window. Counts come from persisted rows, including
cancelled tournaments in the daily budget. Administrators bypass these quotas.
An active administrator created through `init-admin` can create and manage
tournaments before email verification; ordinary accounts still require verification.
Names reject empty values, control characters and bidi formatting controls.
Creation never grants global Workshop or account-management permissions.

The stored creator can manually start their own registration-phase tournament
with at least two eligible entrants. A full tournament with a future planned
start must wait; its creator cannot start it early. An unfilled tournament may
start early with at least two entrants. Administrators can explicitly override
the planned time. The same policy controls both capabilities and mutations. Starting
closes registration and freezes deterministic registration-time/account-ID seeds,
or a reviewed custom seed list. Registration and withdrawal increment the
version; repeated successful registration is a no-op. A changed entrant set
makes reviewed creator/admin starts and custom seed submissions return HTTP 409.

Registration stays open until capacity or start. A planned start is optional and
stored as UTC microseconds. The persistent worker starts a full tournament at or
after that instant without a browser or an administrator session.

| Event at the server's write transaction | Automatic start |
| --- | --- |
| The last place fills before the planned time | Waits for the planned time |
| The last place fills at the exact planned time | Eligible if registration commits before an unfilled start is consumed |
| The last place fills after the planned time | Missed, even if the worker has yet to process the schedule |
| A withdrawal commits at or after the planned time while the tournament is in registration | Missed, including if the place is filled again |
| The server is down at the planned time | On recovery, an armed cup filled by the cutoff can start. An unfilled cup remains in registration. |
| Deployment holds the exclusive gate | Due work resumes once the gate is released |

Once missed, the schedule stays consumed. Later registrations can fill the
tournament. A creator or administrator can set a new future schedule or start
manually with at least two entrants. Discovery and detail use the server clock
for `waiting_for_entrants`, `waiting_for_start`, `start_pending`,
`missed_start` and `manual_start`.

A partial index bounds the due-start query. Under the deployment gate and
`BEGIN IMMEDIATE`, the worker samples server time, reloads the schedule and
checks the planned time and latest registration. Automatic and manual starts
share the transaction that seeds the bracket, seats games, consumes the due
start and records one start audit. The audit source is `automatic`, `creator`
or `administrator`. Automatic events have no account actor.

Failed starts roll back and retry with exponential backoff capped at 300
seconds. Retry writes require the same schedule generation, planned instant and
retry metadata that the failed attempt read under the writer lock. Rescheduling
increments the generation and clears the backoff. A concurrent start or missed
schedule also invalidates the older retry. Disabled or unverified entrants defer
a full cup until the accounts are eligible or the registration changes. Due work
survives downtime and compatible restore.

The reschedule dialog shows the planned start being reviewed and requires a
future replacement. The planned start uses compact adjacent date/time controls. Activating
the date field or its integrated calendar icon opens a themed, anchored React
DayPicker popup. It occupies the native top layer while remaining a DOM descendant
of the existing Modal, escaping the scrolling container without expanding it.
Positioning follows the visual viewport; the calendar stays within its bounds.
Selection, outside clicks and Escape dismiss the popup; keyboard selection and
Escape return focus to the date field. A labelled inline clear icon supports
keyboard use, and one compact timezone hint serves the form. The date and time
inputs use the numeric mobile keyboard and insert `YYYY-MM-DD`/`HH:MM` separators
from digits, while manually typed separators remain valid. On touch screens a tap
on the date field enters text normally; the calendar icon opens the popup.
Date/time errors appear beside their field. Manual entry, retained UTC precision,
DST-gap rejection and both repeated-hour occurrences are preserved. English/German
labels and existing theme tokens apply on mobile and desktop.

## Advancing from authoritative games

For modern two-game modes, all matches in a global round are opened together.
Upper round N and lower round N-1 share a wave; the next wave waits for every
match in the current wave to settle, including both games or byes. Finals and
the reset follow in their own waves. Historical single/legacy formats retain
independent progression. Registration details show a read-only capacity-based
bracket preview with unassigned seeds; no games are created before the start.

When a match becomes ready, the service seats games through the online core
inside the same transaction and records each returned `game_id`. Simultaneous
mode seats both immediately with independent clocks. Sequential mode creates
the first game, then opens the side-swapped return with a fresh clock once that
game is terminal. Each match in the current global round can open its return
independently. A restart between games resumes through the persistent outbox
and startup reconciliation. Pending return games have no playable game ID.
A match settles when its complete configured set of games has terminal results.

Both per-move and total-player clocks use `lifecycle.TimeControl`, the existing
integer-second base/increment bounds (60–1209600 base; 0–1209600 increment),
move accounting, timeout worker, persistence and replay. The UI accepts days,
hours and minutes, with an optional seconds increment for total-player clocks.
There is no separate tournament clock calculation.
Participants then play those games through the normal online endpoints, so
clocks, deadlines, timeouts, concessions, moves and privacy behave exactly as in
ordinary online play.

Each committed result or result revision enqueues its affected tournament in the
same SQLite transaction, deduplicated by tournament ID using the indexed leg game
lookup. A server lifespan worker drains at most 20 due tournaments each second,
serializing each advancement and queue deletion in one write transaction. No
browser or manual settlement request is needed. Timeout results use the same path.
A one-time startup reconciliation finds pre-worker/restored terminal revisions.
Startup attempts it synchronously when possible. During a production activation the
updater deliberately holds the exclusive deployment gate; in that window the
attempt defers without writing, and the existing lifespan worker retries after the
lock is released. It then performs the reconciliation once successfully per worker
lifecycle before normal due-start and queue polling. Expected lock contention is
distinguished from database/integrity/programming failures: the former is deferred,
while unexpected failures are not marked complete and are retried with bounded
backoff. This avoids a startup/readiness deadlock without bypassing the shared
write gate.

Failed transactions remain queued across restart, with bounded exponential retries
(up to 300 seconds). Logs and retry status contain no private exception payloads.
Successful automatic settlement records a null system actor in the audit trail.
Disputes retain the existing bracket freeze and require administrator rebuild;
automatic settlement never impersonates an administrator to retire displaced games.
The settlement endpoint preserves an HTTP-compatible detail response for
ordinary players using a read transaction only. Administrator retries advance
only when an outbox entry exists and consume that entry atomically. No-op calls
cannot repeatedly traverse a bracket under the write lock. The queue's due index avoids periodic full
history scans. Shutdown stops between tournament transactions.

Settlement reads the authoritative `online_games.status` and the effective
`online_results` revision:

- `completed` with an effective winner → `a`, `b` or `draw`;
- `voided` (or a void revision) → `void`;
- anything else → the leg stays unset and the match stays ready.

Settlement and advancement are idempotent. Calling `settle` repeatedly commits no
additional matches, legs or games, and concurrent completions are serialized by
the same `BEGIN IMMEDIATE` writer transaction the online core uses.

## Corrections, disputes and rebuilds

A corrected or voided online result must not silently rewrite bracket
advancement, and it must not award a second credit. When a settled leg's
authoritative revision changes (including a void after the match was decided):

1. the match becomes `disputed`,
2. the tournament `review_state` becomes `disputed`,
3. an audit entry with action `disputed` is written,
4. the whole bracket freezes: no new games or downstream matches are created
   and no further advancement happens on any branch (unaffected ones included)
   until a rebuild re-derives the bracket.

`POST /api/player-tournaments/{id}/rebuild` (administrator, with a private note)
clears derived decisions and previous overrides, then reconciles attached legs
in feeder order across the whole bracket. A leg stays attached when its pairing
and attacker/defender assignments still match. Games whose participants cannot
yet be determined, or whose positions in the bracket have changed, are detached.
Displaced live games are voided through the online lifecycle and recorded in
`online_void_audit` in the same transaction. Detached terminal games keep their
immutable move and result history. A valid reset can retain its games. A
disputed reset is replayed if the rebuilt final still requires one.

Reconciliation preserves proven results before normal advancement resumes.
Any replacement games are seated only when their global round is eligible. An
unaffected game already in progress can keep its original clock if its pairing
is still valid. The rebuild, its audit and any retirements roll back together on
failure. Automatic settlement resumes without an administrator actor after a
successful rebuild. Overriding any match while the bracket is disputed returns
HTTP 409.

An override is an administrative decision independent of the recorded legs. It
voids the match's outstanding games and records those voids in
`online_void_audit`. Completed games retain their results. A later settlement
keeps the administrative decision until a rebuild.

## API

All routes require an authenticated session. Reads are available to any account
with `play`; moderation requires the administrator-only `tournaments` permission
and the audit read requires `accounts`. Every response exposes published fields
only: display name, username and selected portrait ID, never email addresses, session state, private
notes or another account's private profile. The creator account ID is public;
capabilities are derived for the current authenticated viewer.

| Method | Route | Permission | Purpose |
| --- | --- | --- | --- |
| `GET` | `/api/player-tournaments` | play | Cursor-paged list; optional `status`, `ruleset`, `phase=upcoming` filters |
| `POST` | `/api/player-tournaments` | verified play / administrator | Create within per-account quotas (administrators exempt) |
| `GET` | `/api/player-tournaments/{id}` | play | Detail with entrants and match counts |
| `GET` | `/api/player-tournaments/{id}/bracket` | play | Upper/lower rounds, grand final and reset |
| `GET` | `/api/player-tournaments/{id}/matches` | play | Cursor-paged match projections |
| `GET` | `/api/player-tournaments/{id}/matches/{match_id}` | play | One match with its legs and games |
| `GET` | `/api/player-tournaments/{id}/audit` | accounts | Role-aware audit trail (administrator) |
| `POST` | `/api/player-tournaments/{id}/register` | play | Register the current account (idempotent) |
| `POST` | `/api/player-tournaments/{id}/withdraw` | play | Withdraw before the start |
| `POST` | `/api/player-tournaments/{id}/settle` | play | Read detail; administrators can retry pending outbox work |
| `POST` | `/api/player-tournaments/{id}/start` | creator / tournaments | Seed the bracket and start |
| `POST` | `/api/player-tournaments/{id}/reschedule` | verified creator / tournaments | Replace the planned start during registration |
| `POST` | `/api/player-tournaments/{id}/cancel` | tournaments | Cancel a tournament |
| `POST` | `/api/player-tournaments/{id}/rebuild` | tournaments | Re-derive a disputed bracket |
| `POST` | `/api/player-tournaments/{id}/matches/{match_id}/override` | tournaments | Force a ready match winner |

Creator start, rescheduling and administrative mutations take the reviewed
tournament `version` and return `409` when it is stale. Creation quotas return
`429`. List endpoints use opaque cursor pagination with `items` and
`next_cursor`, including when a page is empty.

### Request bodies

```json
{ "name": "Spring cup", "ruleset": "brandubh", "capacity": 8, "match_mode": "sequential",
  "time_per_move": 259200, "time_control": "total", "increment_seconds": 30,
  "scheduled_start_at": "2028-02-01T19:00:00Z" }
```

Creation requires an explicit `match_mode` of `sequential` or `simultaneous`.
Missing/null modes, `single`, `legacy`, and every supplied `match_legs` value
(including `2` or `null`) return `422`. The server stores exactly two games.
Ordinary online-game creation still supports `single`. Existing tournament
records with historical modes/counts remain readable and playable.

Registration has no separate deadline. Older clients can temporarily include
`registration_deadline` as null or a datetime in the creation request. The
server ignores it and returns the deprecated field as null. Historical stored
deadlines are retained and have no effect on registration.

`POST /reschedule` returns the updated tournament detail. Send the reviewed
version and a timezone-aware future datetime.

```json
{ "version": 7, "scheduled_start_at": "2028-02-02T19:00:00Z" }
```

`POST /start` accepts an optional `seeds` array that must list every registered
entrant exactly once; otherwise registration order is used. `POST /rebuild` and
`POST /override` require a private note of 1–1000 characters. `POST /override`
requires a `winner` that is one of the match participants.

### Error contract

`404` for missing records, `409` for state or version conflicts (registration
closed, tournament full, stale version, disputed match), `422` for validation
failures, `403` for missing permission and `401` for a missing session.
A creator's early start of a full scheduled tournament also returns `409` without
creating games or changing the tournament version.

Rescheduling returns `403` for a caller other than the eligible creator or an
administrator, `409` for a stale version or a tournament outside registration,
and `422` for a missing, invalid, timezone-free or non-future replacement. The
deployment gate returns `503` while writes are paused.

## Schema

Schema 18 introduced the tournament, entrant, match, leg and append-only audit
tables. Schema 19 adds `player_tournament_settlement_queue` and nullable
`scheduled_start_at`. This additive upgrade preserves games, training jobs,
checkpoints and models. Queue entries participate in the online restore/retain
boundary, keeping bracket work consistent with the retained authoritative games.
Schema 20 adds nullable due-start/retry fields, clock fields and a `match_mode`
column defaulting to `legacy`, with supporting indexes. Existing game rows, leg
counts, brackets and results are not rewritten. Existing registration-phase
scheduled tournaments are queued once on upgrade, including overdue full cups.
Schema 22 adds `progression_policy` and a schedule generation. Existing rows
default to `independent`. At start the service records `synchronized` for a
two-game sequential or simultaneous tournament, including one created before
the upgrade that was still in registration. Stored `single` and legacy formats
remain independent. Migration leaves game associations, clocks and completed
history intact. Fresh installations initialize the same schema.

Backups and compatible restores retain both fields and queued work alongside the
online games. Automatic rollback and restore across schema 21 and schema 22 are
refused by the existing schema-authority check. See
[deployment and recovery](DEPLOYMENT.md#cross-schema-automatic-recovery-is-fail-closed).

## Limitations

- New tournaments always have two side-swapped games per match. Clock allocation
  and sequential/simultaneous execution are selected at creation; historical
  configurations retain their original counts and execution.
- A tournament game that is never finished keeps its match ready until the
  online clock expires or an administrator overrides it.
- Rebuilding after a dispute clears administrative overrides.
- There is no public/anonymous tournament read surface; every route requires an
  authenticated session.
