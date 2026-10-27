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
All games/My games/My turn tabs, pagination and retained-request recovery. Every
tab excludes completed, cancelled and voided games. Open a waiting game to join,
your game to resume, or a visible game to watch under existing permissions. `/online/games/{id}` remains the dedicated gameplay/reconnect URL.
The authenticated global lobby is intentionally distinct from private history;
this refinement adds no anonymous access or spectator grants. Server admission,
quotas, seats, deadlines and result revisions remain authoritative.

Dashboard statistics are personal and relative to the signed-in account's
authoritative seat. Both panels group by ruleset only:

- **Singleplayer** uses the account-bound human seat and the persisted
  `recorded_result`. Each ruleset shows played games, wins, losses, draws, win
  rate, side-specific win rates and average length. AI-vs-AI games are not recorded
  in personal history or statistics and never appear here; they exist only for
  administrator AI-strength analysis. Legacy records without an account-bound seat
  are excluded from personal statistics; the panel reports how many records are
  unattributed. Missing/null `recorded_result` is unfinished and never
  reconstructed.
- **Online multiplayer** uses the current effective eligible result revision and
  the account's seat, aggregated across match modes per ruleset. Each ruleset
  heading appends the account's available Elo rating (for example
  `Brandubh (1537)`); the rating is omitted when the account has no rated-game
  pool for that ruleset. Cancelled, waiting and voided games do not contribute;
  voids withdraw prior contributions and concession is a loss. `Timed-out games`
  counts only games the account lost because its own clock expired, never wins
  caused by an opponent's timeout.

Win rate is `wins / (wins + losses + draws)`. Side-specific win rates use the same
denominator restricted to games played on that side. Average length uses the same
completed-game population and the existing accepted-ply ("moves") convention.
Rates and averages with no eligible games render as an em dash. Unfinished games
stay out of completed-result denominators. Lifecycle, effective result and the
engine board outcome remain separate. Guest data never enters personal statistics.

Ratings are a separate Elo concept from these personal statistics; see
[Rated games](RATINGS.md). The Dashboard's compact **Leaderboard** panel shows one
unified top-five list (provisional players carry a badge) for the selected
ruleset. The ruleset selector sits in the section's top-right corner; it defaults
to **Copenhagen** and persists the last valid choice in browser storage, restored
after reload and navigation and ignored when storage is unavailable. It requires a
session and exposes only display names, ratings and rated-game counts.

On the Dashboard grid the embedded **Online lobby** takes the slot after the two
statistics panels, the **Leaderboard** panel follows, and **Recorded games**
spans the final row. All cards share the same padding and section gap.

## Shared Recorded games and visibility

Dashboard and Hall share a keyboard-accessible recorded-games table with visible
**Status**, **Ruleset**, **Attacker**, **Defender**, **Winner** and **Date**
headers. The Attacker and Defender cells append the player's immutable start Elo
in parentheses ("Odin (1354)") when a reliable snapshot exists, and show only the
name for older records. The Winner cell names the participant and singular side
("Odin (Attacker)") for a completed recorded result and shows **Draw** for a draw;
unfinished, cancelled and voided rows show an em dash and never gain an invented or
board-inferred winner. A completed rated win also shows the winner's effective
persisted positive Elo change as a small green `+35` with an accessible label;
draws, voids, unrated records and nonpositive changes show no gain.
Each row is clickable: the Date cell holds the single accessible review link
(named "Open review: … vs …") and its overlay makes the whole row activate that
link without adding duplicate links or changing the accessibility tree. The
selected or keyboard-focused row is highlighted as a whole; the date link has no
separate focus ring or hover underline. The date is converted to the viewer's
local timezone and shows a locale-aware date and time with a timezone label; the
`datetime` attribute keeps the stored instant. The trailing Review label is
removed. The table scrolls inside its card on narrow screens without page-level
horizontal overflow. Dashboard shows at most the latest
three eligible completed visible games belonging to the current account, with
**View all** and no pagination. Normal users see their participating online games
and their own account single-player records; administrators can also see their own
account single-player games. An administrator's Dashboard never becomes an
all-account collection.

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

The review restores the single-player presentation: the complete "The saga so far"
move list with move selection, board replay navigation, general game information
(ruleset, matchup mode, participants and lifecycle) and the Analytics/Winning
chances panels. Singleplayer review may use the existing authorized
analysis through a read-only historical path that analyzes the selected recorded
cursor without seeking the authoritative game. Recorded online review keeps those
panels visible but shows them as unavailable, because online analysis stays denied
to every role. Review canonically
validates moves/positions, then uses a viewer-local cursor; previous/next never
writes the authoritative game cursor/version. The replay controls add a
**Play**/**Stop** button that advances one recorded move per second from the
current position, stops at the final position and stops on manual navigation.
Playback is viewer-local: it never requests AI moves or analysis and never writes
authoritative games/results. The board keeps a captured king visible at its final
square, matching the live game. Lifecycle and effective result remain
separate from the board: concessions/timeouts can end a non-terminal board and a
void removes the effective winning side. Existing private
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
