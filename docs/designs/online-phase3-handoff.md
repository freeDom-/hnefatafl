# Phase 3 backend handoff

[Documentation index](../README.md) · [Online operations](../ONLINE.md) ·
[Phase 2 validation](../validation/online-phase2.md)

The backend exists on the online integration milestone; Phase 3 can build a lobby
and game UI after its PR is reviewed/merged. Do not target main or merge a phase
automatically. Authenticate through the existing shell, restore CSRF in memory,
and clear protected state when accounts change. No WebSocket support was added;
bounded polling of safe reads is the current reconnect mechanism.

Phase 3 UI implementation and current evidence are described in
[online play](../ONLINE_PLAY.md), [validation](../validation/online-phase3.md) and
the [Phase 4 handoff](online-phase4-handoff.md). The API contract below remains
applicable; authenticated `/api/online/availability` now exposes admission
enabled/eligible flags.

## Creation and match modes

Authenticated same-origin JSON, CSRF-protected create:

```http
POST /api/online/games
Content-Type: application/json
Origin: <configured origin>
X-CSRF-Token: <current in-memory token>
```

```json
{
  "request_id": "65870d88-32db-4a40-8c5c-3098d1c10fab",
  "ruleset": "brandubh",
  "side_selection": "random",
  "mode": "sequential",
  "time_per_move": 259200
}
```

Generate one UUID per creation intent; retain it through an uncertain network
outcome. A repeated UUID conflicts rather than silently creating another lobby;
reload `GET /api/online/games?mine=true&limit=25&offset=0` to recover it. Admission
defaults disabled in configuration; display unavailable status from a sanitized
503 without suggesting an anonymous or verification bypass.

Side selection is `attackers`, `defenders` or `random`; modes are `single`,
`simultaneous` and `sequential`. Present the latter as “two-game match — simultaneous”
and “two-game match — sequential”, replacing “full-set”. Random waiting games have
null `creator_side` and unassigned participant sides. Joining resolves them once.
Show who moves first from the returned position/ruleset, not creator identity.
Configuration has no edit endpoint; cancel and create again.

Join with `POST /api/online/games/{id}/join`, body `{"version":0}`. It atomically
admits the other account, pins both seats and starts deadlines. In simultaneous
mode both game IDs appear in `game.match.games`; in sequential mode the return
appears atomically with the first result. Side-swapping is automatic. No third
game, aggregate match winner or rating is generated. The existing-seat verification
policy also covers the already-agreed return game.

## Projection, play and reconnect

Create/join/move/concede/cancel/void return `{game, transition, action_accepted}`.
Game reads return the safe `game` directly; listing returns `{games, limit, offset}`.
The game contains `id`, `category`, creator ID/side, `match`, `status`, `version`,
ruleset/version, integer duration, UTC times, `participants`, safe `position`,
ordered accepted `moves` and effective `result`. Each participant is null or
`{account_id, name}`. Render names as text. There is no private account projection.

Render the canonical current board; submit coordinates only. For example, a
Brandubh attacker's opening can use:

```json
{"version":1,"source":3,"target":2}
```

Send it to `POST /api/online/games/{id}/moves`. Server legality, turn, session,
status, version and deadline control acceptance. Concede uses only `{"version":N}`
and is available only to the side to move. Logout/connection loss changes no clock.
Creators can cancel Waiting; administrators use audited per-game void with a
private justification. Do not send actor IDs, boards, winners, timestamps, rated
flags or rules versions as asserted authority; extra fields are rejected.

On creation 429, show the waiting-cap or creation-rate message and let the user
cancel/wait or retry later; do not generate new UUIDs in an automatic retry loop.
Defaults are five waiting lobbies and ten new matches per fifteen minutes per
account, configurable by the operator. Two-game matches count as one creation and
automatic return games need no new admission. The administrator-only
`GET /api/online/monitoring` exposes aggregate denials/growth, never a public profile.

On 409, reload; do not replay the old mutation automatically with a newer version.
The old version can represent an already-committed move/result or another actor's
move. On 401, restore/login again; inactive/revoked sessions cannot mutate. On 403,
respect eligibility/participant/turn/administrator denial. On 422 correct the bounded
input. On 503 keep the last confirmed state, report unavailability and reload after
recovery. Errors contain sanitized `detail`, not raw validation inputs/SQL/notes.

Versions are per game. `match.games` is a current sibling summary, not a shared
match revision; poll/reload both game IDs in simultaneous mode. A sequential return
has its own version 1 and initial state. A first-game void does not implicitly void
the return; there is no group cancellation/correction API.

All timestamps use UTC ISO 8601 with `Z` and microseconds. A client countdown is
informational. At the server deadline, a late authorized action may finalize
timeout and return `transition: "timeout", action_accepted: false`; its requested
action did not happen. While the operational gate is paused, overdue actions return
409 and the safe GET can still say Active. Show “awaiting adjudication” rather than
inventing a winner or extending a deadline. Never use only `position.winner` to
present results: concession/timeout keep the engine board winner null and publish
the effective lifecycle result separately.

## Replay and analysis boundary

`GET /api/online/games/{id}/replay` returns `{game, validated, states}`. Navigation
is local to the viewer. No seek/undo/branch mutation or analysis settings exist for
online games. Safe positions omit internal history duplication, diagnostics, hints
and chances. Local-game paths reject online IDs for all roles. Current online replay
also supports completed/started-voided lines; pre-start states have no played line.

If adding completed-game advice later, check lifecycle before every computation,
cache read, replay-position analysis and export. Active games must remain blocked,
including administrators; do not add arbitrary-position public assistance.
Withholding same-service advice cannot prevent external AI use of visible positions.

## Decisions required before a public lobby/production rollout

Approve public/anonymous visibility, lobby lifetime/retention, rated eligibility
and any match-level scoring separately. Approve an outage/maintenance resolution
procedure before enabling production timeout forfeits. Existing controls only pause,
inspect, explicitly void and resume original deadlines; no automatic extension or
arbitrary winner correction is implemented. Keep the rollout separately authorized,
and preserve the [schema-10 backup/restore boundary](../ONLINE.md#migration-backup-and-recovery).
