# Player tournaments

[Documentation index](README.md) · [Online play](ONLINE_PLAY.md) · [Rated games](RATINGS.md)

Human player competitions live under `/api/player-tournaments/*` and are stored
in their own tournament tables. Schema 20 adds persistent scheduled starts,
creator admission, clock configuration and explicit match modes. They are
deliberately **separate** from the
workshop [AI tournaments](TOURNAMENTS.md) (`/api/tournaments/*`, the
`tournaments` table, model strength comparisons). Neither namespace reads or
writes the other's storage, and neither can grant the other's entitlements.

## Frozen competition policy

- **Format:** double elimination only.
- **Elimination:** a participant is out after **two match losses**, never after
  one lost leg.
- **Match format:** new tournaments select a single game, two sequential games,
  or two parallel games (`single`, `sequential`, `simultaneous`). Return games
  swap sides. Historical `match_legs` values of 1–5 retain their parallel
  behavior and recorded outcomes through the `legacy` compatibility mode.
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

## State model

Tournament status:

```text
registration ──start──▶ running ──champion decided──▶ complete
     │                     │
     └──────cancel─────────┴──▶ cancelled
```

- `registration`: accepting entrants until the capacity is reached or the
  registration deadline passes.
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

Match status: `pending` (sources unresolved) → `ready` (both participants known,
legs materialised) → `complete`, or `bye` (a participant is missing), or
`disputed`.

Leg outcome: `a`, `b`, `draw`, `void`, or unset while the game is unfinished.

## Arena interface

`/player-tournaments` is the human competition section, linked from the Arena
Compete sidebar and the full leaderboard on compact screens. The Workshop's
`/tournaments` remains AI evaluation. Discovery supports bounded cursor pages,
status, ruleset and planned upcoming filters. Details expose real participants,
seeding, losses, rules and clocks, champion, playable online legs and results.
The upper/lower bracket, grand final and reset have source-based progression paths,
horizontal touch scrolling and an accessible list alternative. Registration and
withdrawal use server authority. Details return account-specific capabilities;
creators see their start control, and administrators retain moderation controls.

## Scheduling and creator permissions

Active, verified accounts can create at most three active tournaments and five
in any rolling 24-hour window. Counts come from persisted rows, including
cancelled tournaments in the daily budget. Administrators bypass these quotas.
Names reject empty values, control characters and bidi formatting controls.
Creation never grants global Workshop or account-management permissions.

The stored creator can manually start their own registration-phase tournament
with at least two eligible entrants; an administrator can override. Starting
closes registration and freezes deterministic registration-time/account-ID seeds,
or a reviewed custom seed list. Registration and withdrawal increment the
version; repeated successful registration is a no-op. A changed entrant set
makes reviewed creator/admin starts and custom seed submissions return HTTP 409.

An optional future planned instant is stored as UTC microseconds. The persistent
worker starts a full tournament at or after that instant without any browser,
client polling or administrator session. If unfilled, it remains in registration
for a manual creator/admin start with at least two entrants. A later registration
that fills it re-arms the due row (if registration is still open). Fullness before
the planned instant never starts it early. Optional registration deadlines must
be future instants no later than the planned start; without a deadline,
registration can fill after the planned instant.

The partial due index excludes historical and consumed/unfilled starts. Each
bounded worker pass samples server time after acquiring the deployment gate and
`BEGIN IMMEDIATE`, then shares exactly the same internal start operation as
manual requests. Seeds, bracket, games, start audit and due-row consumption commit
atomically. Concurrent workers, creator starts and administrator starts cannot
produce duplicate games. The audit records `source=automatic`, `creator` or
`administrator`; automatic events have no account actor.

Due work survives shutdown, downtime and compatible restore. Failed starts roll
back fully, retain a sanitized failure code, and retry with exponential backoff
capped at 300 seconds. Disabled/unverified entrants defer a start until they are
eligible again or an administrator resolves the registration. Logs and public
projections never contain private exception payloads or administrator notes.

The form uses the shared online match/clock fields and an accessible inline
React DayPicker calendar inside the existing Modal. Date and time remain local
while editing, serialize to UTC, and restore from a retained instant. Nonexistent
DST wall times are rejected; repeated hours offer both UTC-offset occurrences.
Keyboard navigation, focus return, English/German labels and theme tokens apply
on mobile and desktop.

## Advancing from authoritative games

When a match becomes ready, the service seats games through the online core
inside the same transaction and records each returned `game_id`. Parallel modes
seat both immediately, each with an independent clock. Sequential mode creates
only game one initially. Its terminal result enqueues advancement, which creates
the side-swapped return game with a fresh authoritative clock. A restart between
games resumes through the persistent outbox and startup reconciliation. Pending
return games have no playable game ID and are shown as pending. A match settles
only when its complete configured set of games has terminal results.

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
re-derives the bracket from the authoritative results: it clears every derived
participant, winner and settlement, deletes any bracket reset, re-resolves the
bracket and re-settles the legs. Legs whose recorded game no longer matches the
recomputed participants are unlinked, so a game played by the wrong pairing can
never be credited twice. A displaced leg whose game is still `waiting` or
`active` is retired in the same transaction through the authoritative online
lifecycle and recorded in `online_void_audit`, so an obsolete game can neither be
played, settled nor expired into a result; a leg that already reached a terminal
state keeps its immutable result. Because match identities are deterministic,
legs that are still valid stay attached.

Rebuild is the only safe path after a dispute, and it clears earlier
administrative overrides, which must be re-applied afterwards. Overriding a
`disputed` match is refused for exactly this reason.

An override is an administrative decision independent of the recorded legs. It
voids the match's outstanding games (recorded in `online_void_audit`) and is never
reopened by a later settlement, so no live game can contradict it and no leg can
credit a second result. Downstream legs it displaces by reseating a match are
retired the same way.

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
| `POST` | `/api/player-tournaments` | verified play | Create within per-account quotas |
| `GET` | `/api/player-tournaments/{id}` | play | Detail with entrants and match counts |
| `GET` | `/api/player-tournaments/{id}/bracket` | play | Upper/lower rounds, grand final and reset |
| `GET` | `/api/player-tournaments/{id}/matches` | play | Cursor-paged match projections |
| `GET` | `/api/player-tournaments/{id}/matches/{match_id}` | play | One match with its legs and games |
| `GET` | `/api/player-tournaments/{id}/audit` | accounts | Role-aware audit trail (administrator) |
| `POST` | `/api/player-tournaments/{id}/register` | play | Register the current account (idempotent) |
| `POST` | `/api/player-tournaments/{id}/withdraw` | play | Withdraw before the start |
| `POST` | `/api/player-tournaments/{id}/settle` | play | Read detail; administrators can retry pending outbox work |
| `POST` | `/api/player-tournaments/{id}/start` | creator / tournaments | Seed the bracket and start |
| `POST` | `/api/player-tournaments/{id}/cancel` | tournaments | Cancel a tournament |
| `POST` | `/api/player-tournaments/{id}/rebuild` | tournaments | Re-derive a disputed bracket |
| `POST` | `/api/player-tournaments/{id}/matches/{match_id}/override` | tournaments | Force a ready match winner |

Creator start and administrative mutations take the tournament `version` from the last read and
return `409` when it is stale. Creation quotas return `429`. List endpoints use opaque cursor pagination
(`items`, `next_cursor`) and never omit list fields on an empty page.

### Request bodies

```json
{ "name": "Spring cup", "ruleset": "brandubh", "capacity": 8, "match_mode": "sequential",
  "time_per_move": 259200, "time_control": "total", "increment_seconds": 30,
  "scheduled_start_at": "2028-02-01T19:00:00Z",
  "registration_deadline": "2028-02-01T18:00:00Z" }
```

`POST /start` accepts an optional `seeds` array that must list every registered
entrant exactly once; otherwise registration order is used. `POST /rebuild` and
`POST /override` require a private note of 1–1000 characters. `POST /override`
requires a `winner` that is one of the match participants.

### Error contract

`404` for missing records, `409` for state or version conflicts (registration
closed, tournament full, stale version, disputed match), `422` for validation
failures, `403` for missing permission and `401` for a missing session.

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
Legacy API requests with only `match_legs` remain supported; explicit modes must
agree with any supplied leg count. Fresh installations initialize the same schema. Keep a backup before upgrading;
restore compatibility still requires matching schemas.

## Limitations

- Exact per-match clock policy, side-swap order and leg counts are tournament
  settings, not a fixed product decision; the grand-final reset and two-loss
  elimination invariants are fixed.
- A tournament game that is never finished keeps its match ready until the
  online clock expires or an administrator overrides it.
- Rebuilding after a dispute clears administrative overrides.
- There is no public/anonymous tournament read surface; every route requires an
  authenticated session.
