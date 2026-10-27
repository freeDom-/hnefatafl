# Phase 5 personal dashboard contract

[Documentation index](../README.md) · [Online core](../ONLINE.md)

The owner approved private authenticated dashboard/history/review in the Phase 5
request on 2026-10-26. Public visibility, ratings, completed-online assistance and
guest retention changes are outside that approval.

## Dependency and branch

Initial inspection found [Phase 4 PR #15](https://github.com/freeDom-/hnefatafl/pull/15)
open at `4342c57bd35f39a77fd75587ec472bef13784f36`, with Verify run 84 successful
and no submitted reviews or inline threads. Its final handoff and all three commits
were inspected. Independent work began on integration tip
`f49f7b659709c53ca694c00a381e8a5e78c744f0` with a documented draft/rebase prerequisite.

The owner subsequently merged PR #15 on 2026-10-26. The unshared
`feat/phase5-personal-dashboard` branch was rebased onto merged integration commit
`41bf1b2dd683ece1bfc1826de82411d7228490da` before combined validation. Its PR targets
`feat/online-multiplayer`, contains only the Phase 5 diff and remains unmerged.
No deployment, production change, release or version bump is authorized here.

## Existing contracts and gaps (before API changes)

- `GET /api/games` and `/api/stats` load existing account-game records and use
  `owner_scope()`. Administrators retain established all-data privileges. These
  contracts are unpaginated and include existing profile/model/analysis behavior;
  changing their defaults would break the Hall of records. They cannot serve a
  strictly personal administrator dashboard as-is.
- `recorded_outcome` in `server.py` separates retained result from shared replay
  cursor. Current writers always store `recorded_result`: null for unfinished
  play, a result object for a completed retained line. Missing or null results
  are unfinished; no legacy account records or result reconstruction are supported.
  Account statistics are side-based;
  record ownership does not reliably identify a human seat for every mode.
- `GET /api/games/{id}/replay` already validates the retained line and stored
  cursor position through the engine. It does not seek or mutate the game. Other
  account controls and their authorization remain available in existing views.
- `GET /api/online/games?mine=true` is bounded but returns full game projections
  with per-record queries, lacks history filters, and mixes active/terminal rows.
  Existing authenticated visibility also allows known-game spectator reads.
- `OnlineCore.statistics` derives current eligible result revisions and correctly
  distinguishes timeout abandonment from concession. Its current implementation
  loads result payloads; this can be replaced with equivalent SQL aggregation.
- Online detail/replay projections omit private correction notes and security
  data. `Online.tsx` already provides safe local replay and dedicated online IDs.
  Personal review can reuse it with strictly participant-scoped read adapters and
  mutation controls disabled, without broadening spectator access.
- Phase 4 places `/guest` outside Auth, owns its IndexedDB slot independently,
  and adds session epochs/late-response guards. The dashboard must neither import
  that store nor read its contents. Guest navigation is only a link.

## Narrow read adapters

Add authenticated `play` grants under `/api/personal/` for account-game pages and
read-only replay, online pages, participant-only detail and participant-only
replay. Every query/detail rechecks the current session and scopes the account in
SQL, including administrators. Existing API permissions and spectator routes keep
their semantics. No schema migration or new stored history/accounting is introduced.

Pages use bounded keyset cursors, exact ruleset/status/outcome filters and a
deterministic ID tie-breaker. Cursor context includes category/filter/account scope
and never grants access. Counts/page rows share a read transaction. There is no
cross-page database snapshot: lifecycle/result changes can alter membership or
ordering; Refresh returns to the first page and current effective results. Account
pages order by creation; terminal online pages prioritize recent terminal updates.
Use set-based projections and one batched sibling query instead of per-card loads.

Account totals use the persisted `recorded_result` independently of the shared
cursor. Listings and totals never replay moves or register a SQLite replay callback.
Missing or null results contribute only to unfinished counts, even if a board is
terminal; no legacy account records are supported. Personal account win/loss is
omitted because ownership is not a seat.
Online totals aggregate only the current effective eligible completed result,
per game. Waiting/cancelled/voided and superseded revisions do not contribute;
concession is loss, and abandonment applies only to the timed-out account.

## UI and boundaries

`/dashboard` exposes separate account and online sections, bounded filters/pages,
loading/empty/error/retry states and independent totals. Online discovery separates
your non-overdue turn, other active/overdue games, creator waiting games and
terminal history. Match links show leg/status without aggregate scoring. Deadlines
and lifecycle remain server authority; countdowns are presentation only.

Account history review reuses existing validated replay with local-only cursor
controls. Online personal review reuses the dedicated online viewer through
participant-only read paths. Neither review performs analysis, seek/undo/branch,
exports or game mutation. Normal play remains in existing account/online surfaces.
Account-keyed state, cancellation, request generations and Phase 4's session epoch
protect navigation/account switches. Existing selection/setup keys remain intact.

## API contract

All five routes require a current authenticated account with `play`. Browser-supplied
owner IDs are not accepted. Personal administrator reads use the same account scope.
Existing `/api/games`, `/api/stats`, and authenticated online spectator routes retain
their prior behavior. Registration and admission gates are unchanged.

| GET route | Scope and parameters |
| --- | --- |
| `/api/personal/single-player` | Own `games.owner_account_id`; `ruleset`, `status=in_progress/completed`, `winner=attackers/defenders/draw`. |
| `/api/personal/single-player/{id}/replay` | Own account record, existing canonical validation, safe summary/moves/states; no stored cursor change. |
| `/api/personal/online` | Creator, seated player or admitted match opponent; `ruleset`, `section=current/your_turn/other_active/waiting/history`, `status=waiting/active/completed/cancelled/voided`, `outcome=win/loss/draw/abandoned`. |
| `/api/personal/online/{id}` | Participant-only existing safe detail projection. |
| `/api/personal/online/{id}/replay` | Participant-only existing validated safe replay. |

Both list routes accept `limit` (default 25, range 1–100) and an optional cursor
(maximum 512 characters). The UI requests ten rows. Responses include `games`,
`matching`, `limit`, `next_cursor`, separate category totals; online also includes
`as_of` and unfiltered overview buckets. Exact filter conjunctions can produce zero
rows (for example, cancelled + win). Invalid filters/cursors return 422; unowned or
nonparticipant details return 404. Missing/revoked sessions return 401; permission
denial is enforced at the existing middleware boundary. Unavailable storage yields
a scrubbed error. List summaries omit boards, moves, analysis/configuration, email,
security metadata and correction notes. Account replay omits engine repetition
history after validation; the existing account replay response is unchanged.

Online summary pages perform one batched sibling query for the bounded page and
no per-card loads. Totals/counts are SQL aggregates over owned/participating rows
and stored result metadata; they never invoke the engine. These aggregates
scan existing SQLite tables with account-scoped conditions; their CPU cost is not
claimed constant on arbitrarily large account or global histories. No materialized counters or new indexes
are added. Existing schema 10 remains the persistence and recovery boundary.

See [validation](../validation/online-phase5.md) and
[Phase 6 policy handoff](online-phase6-handoff.md).

## Current account-record write contract

There are no legacy account records to support. The production writers were
inspected before removing result replay. `server.create_game` and `restart` insert
`recorded_result: null`. Both human and AI moves use `append_move`, which writes a
complete result object on terminal moves and null on unfinished/new branches.
`seek`, undo and redo preserve that retained result. These are the only production
callers of `Store.create_game`/`update_game`, which persist their supplied JSON.
No write-time fallback, migration or database schema change is needed.

Both personal totals and `/api/stats` count completion from stored result metadata
only. A missing/null result is unfinished; no winner is inferred from the cursor,
board or move history. Single-game review still performs canonical engine replay
and validates the stored cursor position; its result label uses stored metadata,
so validation never silently reconstructs a missing result.

Unexpected personal API failures retain the exact generic 503 response. Module
logging records only a fixed action name, the original exception type and a
traceback with a scrubbed exception value. Request/session values, IDs, cursors,
original exception messages and chained exception details are not logged. The
authenticated global online listing/detail remain intentional spectator surfaces;
personal object detail/replay still return 404 for nonparticipants, while their
personal listings are empty. No route or grant changes are introduced.

## Owner-authorized Phase 5 consolidation refinement

The owner explicitly directed implementation on the existing Phase 5 branch/PR,
superseding the proposed new branch and preceding-merge prerequisite for this
refinement. PR #17 remains targeted at `feat/online-multiplayer`, unmerged. No
schema, version, deployment or production gate change is included.

The initial UI contract above describes the first delivery. Current navigation and
Hall behavior are defined in [Dashboard](../DASHBOARD.md): default Dashboard with
existing setup/lobby, shared latest-three/compact rows, independent selected review,
normal-user online participation scope and administrator server-wide Hall filters.
Personal APIs keep their original private scope and canonical validation.
`GET /api/history/games` adds bounded `category`, `player` (literal name substring),
`ruleset`, `status`, `winner`, `limit`, `cursor`, and `dashboard` parameters. Current
trusted `all_data` enables global Hall scope; `dashboard=true` always forces own,
eligible completed scope and limit three. Normal users cannot request account games
or name filters. Counts and side-result totals use the same authorized filter.
`GET /api/history/{category}/{game_id}/replay` independently authorizes participant
online or administrator record access, then canonically validates in that read
transaction. There are no history writes, exports or analysis routes. See
[refinement validation](../validation/dashboard-consolidation.md).

## Post-Phase 5 personal statistics and review refinement

This refinement keeps schema 10, all grants and the server-authoritative identity
boundary unchanged. It adds derived personal statistics and restores the recorded
review presentation.

### Statistics definitions

`GET /api/personal/single-player` and `GET /api/personal/online` each return a
`statistics` object in addition to their existing `totals`:

- `groups` is a list keyed by `ruleset` (`brandubh`, `tablut`, `copenhagen`) and
  `mode`. Account modes are `human-ai` and `ai-ai`; online modes are the match
  modes `single`, `simultaneous` and `sequential`.
- `played`, `wins`, `losses`, `draws` and, online, `timed_out` are integer counts.
- `win_rate = wins / (wins + losses + draws)`;
  `win_rate_attackers` and `win_rate_defenders` use the same denominator
  restricted to attributed completed games played on that side.
- `average_game_length` is the mean accepted-ply count (`moves`) over the same
  completed population, and `average_game_length_unit` names the unit.
- A null rate or average is rendered as an em dash.

Account attribution uses only an account-bound human seat: the stored
`human_side` must identify a `kind: "human"` player whose `account_id` matches the
current account. `ai-ai` games have no personal human result, so their outcome
fields are null even though `played` and `average_game_length` remain. Legacy
`human-human` records and any record without an account-bound seat are excluded
from personal outcomes and reported through `coverage.unattributed_games`. No
ownership or seat is guessed and no stored data is rewritten.

Online attribution uses `online_games.attackers_id`/`defenders_id` and the current
effective eligible `online_results` revision. Waiting, cancelled and superseded
results do not contribute; a void revision withdraws the contribution.
`timed_out` counts only `abandoned_account_id` equal to the current account, so an
opponent's timeout victory is not counted as the account timing out.

### Review presentation

Selecting a Dashboard or Hall row opens the full read-only review. It reuses the
single-player layout: "The saga so far" with the complete move list and move
selection, board replay navigation, general game information (ruleset, matchup
mode, participants and lifecycle) and the Analytics/Winning chances panels.
Account single-player review may use the existing authorized analysis; recorded
online review keeps the panels but shows them as unavailable because all-role
online analysis remains denied. The review remains viewer-local and does not
write authoritative games or results. Canonical `/archive?review=…&selected=…`,
`/dashboard/single-player/{id}` and refresh/back-forward behavior are preserved.

### Recorded-games table and replay playback

`RecordedGames` renders one shared table for Dashboard and Hall with visible
`Status | Ruleset | Attacker | Defender | Winner | Date` headers. The Winner cell
is `Name (Side)` for a completed recorded result, `Draw` for a draw, and an em
dash for unfinished, cancelled and voided rows; a stale winner on a non-completed
record is never shown. The Date cell holds the single review link, named
`Open review: {attacker} vs {defender}`, and its overlay makes the whole row
clickable without duplicate links; navigation stays keyboard-accessible without a
visible Review label. The date is formatted in the viewer's resolved local
timezone, while the backend keeps the stored instant. The table scrolls inside its
card on narrow screens.

`useReplayPlayback` owns a viewer-local cursor and a bounded one-second timer;
`ReplayControls` renders Play/Stop when playback is enabled. Manual move selection
and previous/next/start stop playback before changing position, the final position
stops automatically and disables Play, and the timer is cleared on dependency
change and unmount. Playback never requests AI moves or analysis and never writes
authoritative games or results. The active online spectator controls in
`Online.tsx` do not enable playback.

### Focus, selection and analysis refinements

- The login view focuses the username/email field on mount; the `HNEFATAFL`
  heading remains the accessible page heading and other auth views still focus
  their own heading.
- The selected or keyboard-focused recorded-games row is highlighted as a whole.
  The date link has no separate focus ring or hover underline; the row highlight
  is the visible focus indicator.
- The "Show winning chances" checkbox and label are removed. Selecting a move
  advisor is the single trigger for calculating and displaying winning chances and
  the best-move hint; the frontend keeps the persisted winning-chance gate enabled
  when an advisor is chosen. Deselecting the advisor closes the analysis section.
- Recorded review reconstructs captured pieces by diffing adjacent validated
  states, so a captured king stays visible at its final square exactly as in the
  live game. It does not trust move metadata for the overlay.

### Hall access and read-only historical analysis

`GET /api/history/games` now includes each account's own single-player records for
normal users as well as administrators. Normal users see their participating online
games plus their own account records, never another account's; the administrator
`player` name filter stays restricted to trusted current `all_data`. The
`category=account-single-player` filter is available to normal users because it is
scoped to their own account. `GET /api/history/account-single-player/{id}/replay`
authorizes the owner or an administrator.

`POST /api/games/{id}/analysis` accepts an optional `historical: true` flag. Live
gameplay keeps the exact-cursor contract. A historical request validates the
cursor against the recorded move count, analyzes `state_at(game, cursor)` and
schedules only the advisor (playing-agent evidence is never fabricated for a past
position). The report is stored in the separate advisor table keyed by the
position; the authoritative game cursor, version and result are not touched.
Online IDs remain rejected by `game_or_404`, so the read-only path cannot expose
online analysis.
