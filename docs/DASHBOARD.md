# Dashboard and Hall of records

[Documentation index](README.md) · [Account play](GAMEPLAY.md) ·
[Online play](ONLINE_PLAY.md) · [Guest storage](GUEST_PLAY.md)

Dashboard (`/dashboard`) is the authenticated landing and default post-login page.
`/`, `/play` and `/online` redirect there, retaining search parameters and fragments;
old Play/Online entries select the corresponding setup. Main navigation contains
Dashboard, Hall of records and the authorized workshop/tournament controls.
Authenticated navigation omits Guest play. The public `/guest` route remains
available directly, and login screens still offer guest play.

## Play from Dashboard

**New single-player game** opens the existing account setup. New and resumed games
use `/games/{id}`; refresh reconnects to that account game. **Resume single-player**
opens your latest unfinished account record. Server-backed single-player gameplay,
AI, analysis, setup preferences and optimistic versions retain their semantics.

**New online game** opens the existing lobby configuration. Dashboard embeds its
All games/My games discovery, pagination and retained-request recovery. Open a
waiting game to join, your game to resume, or a visible game to watch under existing
permissions. `/online/games/{id}` remains the dedicated gameplay/reconnect URL.
The authenticated global lobby is intentionally distinct from private history;
this refinement adds no anonymous access or spectator grants. Server admission,
quotas, seats, deadlines and result revisions remain authoritative.

Account statistics describe recorded sides, not personal wins/losses. Missing/null
`recorded_result` is unfinished; no legacy account records or result reconstruction
are supported. Online statistics describe your current eligible per-game results;
voids withdraw contributions, and concession is not abandonment. Guest data and
rated results are absent. The small **Leaderboard** panel says **Upcoming in Phase 6**;
it contains no rankings or rating calculation.

## Shared Recorded games and visibility

Dashboard and Hall use the same compact, keyboard-accessible record rows. Dashboard
shows at most the latest three eligible completed visible games belonging to the
current account, with **View all** and no pagination. Normal users see participating
online games; administrators can also see their own account-single-player games.
An administrator's Dashboard never becomes an all-account collection.

Hall (`/archive`) provides bounded server-side category/name/ruleset/lifecycle/result
filters and pagination. Normal users see only participating online records and have
no account-single-player or player-name filter. Administrators with trusted current
`all_data` permission can inspect online and account-single-player records across
accounts. Guest saves are never server records. The Local players section is removed;
new account setup uses the current account and four preset opponents. Local-player
CRUD is removed; historical data is retained for read/play compatibility.

Rows and counts/statistics share the same authorized filter and read transaction.
Hall totals remain separated by category and describe winning sides, not an invented
administrator win/loss perspective. Dashboard applies ownership, role visibility and
completion/eligibility before its limit. Ordering is newest recorded time first
(account end/creation time, online update time), then ID ascending for stable ties.
Current UTC account dates are parsed to exact integer microseconds for cross-category
sorting, avoiding SQLite fractional-date rounding; pages do not promise a frozen snapshot across refreshes. Cursors bind to
account, effective role/scope and filters. Refresh returns to the first page.

## Independent read-only review

A row opens `/archive?review=online&selected={id}` or
`/archive?review=account-single-player&selected={id}`. Review category and ID are
independent of list filters/cursor, so selection survives refresh, empty results
and pages that do not contain the selected game. Direct lookup repeats backend
role/participant authorization. Administrators' history reads grant no mutation,
assistance or exports. Online IDs always use online adapters.

Review canonically validates moves/positions, then uses a viewer-local cursor;
previous/next never writes the authoritative game cursor/version. Lifecycle and
effective result remain separate from the board: concessions/timeouts can end a
non-terminal board and a void removes the effective winning side. Active online
analysis remains denied to every role. Existing private
`/dashboard/single-player/{id}` and `/online/games/{id}/review` deep links retain
their canonical, personal read adapters.

## Session and device boundaries

Safe same-origin post-login destinations retain valid queries and selected review;
external/unknown destinations fall back to Dashboard. Logout/account changes abort
history/statistics/replay reads and discard late responses. The personal APIs remain
strictly own-account even for administrators; broader Hall access uses separate
read-only `/api/history` routes with trusted session rechecks.

Dashboard never imports or reads guest storage. Dashboard cards, including the
full-width lobby, share padding and one grid gap. Guest navigation uses the shared
authenticated header for signed-in visitors and a single login anchor otherwise;
only the header checks the session. Protected destinations retain their auth guard.
The guest worker, one device slot, validated replay, replacement and
clear policy are unchanged. No save upload/import, archive or server accounting is
introduced. See [the Phase 5 refinement evidence](validation/dashboard-consolidation.md).
