# Player-tournament authority

[Development index](README.md) · [Player tournaments](../game/tournaments.md) · [Architecture](architecture.md)

This page contains internal API, error, schema and migration details for player
tournaments. Player-facing format and moderation policy are in
[player tournaments](../game/tournaments.md).

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
| `POST` | `/api/player-tournaments/{id}/start` | creator / tournaments | Draw seeds randomly and start |
| `POST` | `/api/player-tournaments/{id}/reschedule` | verified creator / tournaments | Replace the planned start during registration |
| `POST` | `/api/player-tournaments/{id}/cancel` | tournaments | Cancel a tournament |
| `POST` | `/api/player-tournaments/{id}/rebuild` | tournaments | Re-derive a disputed bracket |
| `POST` | `/api/player-tournaments/{id}/matches/{match_id}/override` | tournaments | Force a ready match winner |

All three match reads (`/bracket`, `/matches` and `/matches/{match_id}`)
publish the same scheduling fields for a persisted match. `global_round` is the
synchronized tournament round or null for independent progression.
`waiting_for_round` means an earlier round is holding advancement.
`retained_game_state` is `active` when a held match still has a playable
game, `results_held` when its retained games have finished, or null for other
matches. `return_game_state` describes sequential returns in an eligible match.

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

`POST /start` always draws seeds randomly on the server. Older clients may
still send the deprecated `seeds` property; it is accepted for compatibility,
validated only as a list of strings, and ignored. The supplied ordering cannot
influence the assigned seeds. Send only the reviewed `version` in new clients.
`POST /rebuild` and `POST /override` require a private note of 1–1000
characters. `POST /override` requires a `winner` that is one of the match
participants.

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
remain independent. Randomized seeding uses the existing persistent entrant
seed fields, so it needs no additional schema change. Migration leaves game
associations, clocks and completed history intact. Fresh installations
initialize the same schema.

Backups and compatible restores retain both fields and queued work alongside the
online games. Automatic rollback and restore across schema 21 and schema 22 are
refused by the existing schema-authority check. See
[deployment and recovery](../installation/updating.md#cross-schema-automatic-recovery-is-fail-closed).
