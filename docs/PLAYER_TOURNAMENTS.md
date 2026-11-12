# Player tournaments

[Documentation index](README.md) · [Online play](ONLINE_PLAY.md) · [Rated games](RATINGS.md)

Human player competitions live under `/api/player-tournaments/*` and are stored
in their own schema-18 tables. They are deliberately **separate** from the
workshop [AI tournaments](TOURNAMENTS.md) (`/api/tournaments/*`, the
`tournaments` table, model strength comparisons). Neither namespace reads or
writes the other's storage, and neither can grant the other's entitlements.

## Frozen competition policy

- **Format:** double elimination only.
- **Elimination:** a participant is out after **two match losses**, never after
  one lost leg.
- **Match format:** each match is a series of `match_legs` games (1–5,
  side-swapped, so the first participant attacks in odd legs and defends in even
  legs). Every leg is a real authoritative online game.
- **Grand final reset:** if the lower-bracket finalist wins the first grand
  final, a bracket-reset match is played between the same two participants and
  decides the champion.
- **No random winner:** every unresolved outcome is decided by an explicit rule
  (below). A random winner is never generated.
- **Rating isolation:** tournament games are casual (`rated = false`) and never
  write `rating_events` or Elo applications.

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
- `cancelled`: an administrator stopped the tournament.

`review_state` is `ok` or `disputed`. It becomes `disputed` when a settled leg's
authoritative result revision changes after it was recorded, or when a voided
game invalidates a settled leg. A disputed bracket stops advancing until an
administrator rebuilds it; a tournament that had already completed reopens to
`running` with no official winner so the correction can be reviewed.

Match status: `pending` (sources unresolved) → `ready` (both participants known,
legs materialised) → `complete`, or `bye` (a participant is missing), or
`disputed`.

Leg outcome: `a`, `b`, `draw`, `void`, or unset while the game is unfinished.

## Advancing from authoritative games

When a match becomes ready, the service creates its `match_legs` games through
the online core inside the same transaction and records the returned `game_id`.
Participants then play those games through the normal online endpoints, so
clocks, deadlines, timeouts, concessions, moves and privacy behave exactly as in
ordinary online play.

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
4. the disputed match stops advancing, so every match fed by it stays unresolved
   until a rebuild.

`POST /api/player-tournaments/{id}/rebuild` (administrator, with a private note)
re-derives the bracket from the authoritative results: it clears every derived
participant, winner and settlement, deletes any bracket reset, re-resolves the
bracket and re-settles the legs. Legs whose recorded game no longer matches the
recomputed participants are unlinked, so a game played by the wrong pairing can
never be credited twice. Because match identities are deterministic, legs that
are still valid stay attached.

Rebuild is the only safe path after a dispute, and it clears earlier
administrative overrides, which must be re-applied afterwards. Overriding a
`disputed` match is refused for exactly this reason.

An override is an administrative decision independent of the recorded legs. It
voids the match's outstanding games (recorded in `online_void_audit`) and is never
reopened by a later settlement, so no live game can contradict it and no leg can
credit a second result.

## API

All routes require an authenticated session. Reads are available to any account
with `play`; moderation requires the administrator-only `tournaments` permission
and the audit read requires `accounts`. Every response exposes published fields
only: display name and username, never email addresses, session state, private
notes or another account's private profile.

| Method | Route | Permission | Purpose |
| --- | --- | --- | --- |
| `GET` | `/api/player-tournaments` | play | Cursor-paged list; optional `status` filter |
| `POST` | `/api/player-tournaments` | tournaments | Create a tournament |
| `GET` | `/api/player-tournaments/{id}` | play | Detail with entrants and match counts |
| `GET` | `/api/player-tournaments/{id}/bracket` | play | Upper/lower rounds, grand final and reset |
| `GET` | `/api/player-tournaments/{id}/matches` | play | Cursor-paged match projections |
| `GET` | `/api/player-tournaments/{id}/matches/{match_id}` | play | One match with its legs and games |
| `GET` | `/api/player-tournaments/{id}/audit` | accounts | Role-aware audit trail (administrator) |
| `POST` | `/api/player-tournaments/{id}/register` | play | Register the current account (idempotent) |
| `POST` | `/api/player-tournaments/{id}/withdraw` | play | Withdraw before the start |
| `POST` | `/api/player-tournaments/{id}/settle` | play | Settle and advance from authoritative results |
| `POST` | `/api/player-tournaments/{id}/start` | tournaments | Seed the bracket and start |
| `POST` | `/api/player-tournaments/{id}/cancel` | tournaments | Cancel a tournament |
| `POST` | `/api/player-tournaments/{id}/rebuild` | tournaments | Re-derive a disputed bracket |
| `POST` | `/api/player-tournaments/{id}/matches/{match_id}/override` | tournaments | Force a ready match winner |

Administrative mutations take the tournament `version` from the last read and
return `409` when it is stale. List endpoints use opaque cursor pagination
(`items`, `next_cursor`) and never omit list fields on an empty page.

### Request bodies

```json
{ "name": "Spring cup", "ruleset": "brandubh", "capacity": 8, "match_legs": 1,
  "time_per_move": 259200, "registration_deadline": "2027-02-01T18:00:00Z" }
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

Schema 18 adds `player_tournaments`, `player_tournament_entrants`,
`player_tournament_matches`, `player_tournament_legs` and
`player_tournament_audit`. Schema 17 is reserved for the parallel Arena v2
progression branch and is deliberately skipped. The audit table is append-only
and every participant-visible mutation records an actor. The tournament tables
participate in the online restore/retain boundary, so a restore keeps bracket
state consistent with the online games it references.

## Limitations

- Exact per-match clock policy, side-swap order and leg counts are tournament
  settings, not a fixed product decision; the grand-final reset and two-loss
  elimination invariants are fixed.
- A tournament game that is never finished keeps its match ready until the
  online clock expires or an administrator overrides it.
- Rebuilding after a dispute clears administrative overrides.
- There is no public/anonymous tournament read surface; every route requires an
  authenticated session.
