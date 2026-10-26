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
  cursor. It supports old records without `recorded_result` by canonical replay.
  A present null result means unfinished. Account statistics are side-based;
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

Account totals preserve retained-result side-based semantics. A pure SQL callback
may replay one legacy record at a time when its retained end is not represented by
the saved cursor; no list of complete histories or per-row database query is
needed. Personal account win/loss is omitted because ownership is not a seat.
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
history after validation; the legacy replay response is unchanged.

Online summary pages perform one batched sibling query for the bounded page and
no per-card loads. Totals/counts are SQL aggregates over owned/participating rows;
legacy uncertain account results can require pure engine replay. These aggregates
still scan the current account's matching history and are not claimed as constant
CPU work on arbitrarily large histories. No materialized counters or new indexes
are added. Existing schema 10 remains the persistence and recovery boundary.

See [validation](../validation/online-phase5.md) and
[Phase 6 policy handoff](online-phase6-handoff.md).
