# Dashboard, personal statistics and Hall of records

[Documentation index](README.md) · [Account play](GAMEPLAY.md) ·
[Online play](ONLINE_PLAY.md)

Dashboard (`/dashboard`) is the authenticated landing and default post-login page.
`/` redirects there, retaining search parameters and fragments; the authenticated
lobby listing keeps its own `/online` route, and the legacy
`/dashboard?setup=online` deep link redirects to `/play` with the online creation
dialog open. Main navigation contains
Dashboard, Play, Statistics, Leaderboard, Hall of records and the authorized
workshop/tournament controls.
Every surface here requires an authenticated session.

**Play** (`/play`) opens the last selected unfinished game when it is still in the
signed-in account's current games; otherwise it opens another unfinished game.
With no running games, it links to the `/online` lobby listing for finding
opponents and offers the New online game/New local 1v1 game/New single-player game
buttons and dialogs.
The remembered game is checked against current server-owned lists and cleared on
account changes/logout.

## Creating a game: three modes

Dashboard and Play offer the same three equal-start creation modes, each with its
own dialog: **Play online** (rated online multiplayer), **Local 1v1** and
**Play vs AI**.

**Play vs AI** (New single-player game) opens the existing account setup. New and
resumed games use `/games/{id}`; refresh reconnects to that account game.
**Resume single-player** opens your latest unfinished account record. Server-backed
single-player gameplay, AI, analysis, setup preferences and optimistic versions
retain their semantics.

**Local 1v1** (New local 1v1 game) starts same-device pass-and-play with two
labelled human seats. Both seats belong to the signed-in account, so it is never
rated and never enters single-player/vs-AI statistics; the record stays in the
owner-scoped Local 1v1 category and replays read-only without an advisor.

**Play online** (New online game) opens the existing lobby configuration on Play.
The authenticated lobby listing itself lives at `/online`, with its All games/My
games/My turn tabs, pagination and retained-request recovery. Every tab excludes
completed, cancelled and voided games. Open a waiting game to join, your game to
resume, or a visible game to watch under existing permissions. `/online/games/{id}` remains the dedicated gameplay/reconnect URL.
The authenticated global lobby is intentionally distinct from private history;
this refinement adds no anonymous access or spectator grants. Server admission,
quotas, seats, deadlines and result revisions remain authoritative.
Unverified email addresses get a red warning inside the creation hierarchy on
Dashboard and Play, and online creation is disabled there. The lobby omits its
former duplicate verification notice. Existing seats remain playable under the
same server policy.

## Compact badges and detailed personal statistics

Dashboard's **Your performance** strip contains three small ruleset badges per
mode. Singleplayer badges show the win rate; their decoration progresses from
bronze to silver (40%), gold (65%) and royal (85%). These are cosmetic treatments
of the current rate, not earned rewards or ratings. Multiplayer badges show the
win rate and the account's existing Elo for that ruleset. Elo is omitted when no
eligible games have been played there, including an empty rating pool. A ruleset
with no completed results shows an em dash. Badges link to its mode/ruleset on
the Statistics page; detailed tables do not appear on Dashboard.

**Statistics** (`/statistics`) is personal and relative to the signed-in
account's authoritative seat. The Singleplayer/Multiplayer/Local 1v1 tabs and the
ruleset selector are retained in the URL, including through login and reload. Each
ruleset has an outcome distribution and a comparison table for overall, attacker
and defender results: games played, wins, losses, draws, win rate and average
length. On phones the tables become labelled metric cards without horizontal
panning. Singleplayer adds expandable AI-opponent breakdowns with the same metrics
and sides. Opponents use the persisted seed-independent effective configuration
identity, so changing only a random seed does not split an agent's results.

**Local 1v1** is a third, non-Elo tab backed by the owner-scoped
`/api/personal/local` category. Both seats belong to the signed-in account, so it
reports account-wide totals (games played, completed, in progress, attacker wins,
defender wins and draws) with the average game length instead of personal wins.
Its one totals table covers every ruleset, so the tab hides the ruleset selector
and never affects Elo.

**Rating history** (Multiplayer) renders the applied rating ledger for each pool
from `/api/personal/ratings/{ruleset}/history` and `/api/personal/progress` as a
lightweight SVG progression. Only authoritative `rating_after` values are plotted;
no curve is derived from a game's start-rating snapshot. A ledger entry with no
trusted completion stamp is drawn as an explicit gap marker and a corrected result
as a diamond, and the expandable entry list repeats both labels ("Date
unavailable", "Corrected result") while keeping the ledger sequence. The section
shows the current rating, the pool's rated-game count, the current and best rated
win streaks, and the newest bounded page of entries. A pool with no applied events
stays empty, a loading read is announced, and a failed or stale read offers Retry
without inventing a baseline.

Both views use complete account-scoped aggregates, independent of history
pagination and filters:

- **Singleplayer** uses the account-bound human seat and the persisted
  `recorded_result`. Played includes unfinished games. AI-vs-AI games are not recorded
  in personal history or statistics and never appear here; they exist only for
  administrator AI-strength analysis. Records without an account-bound seat
  are excluded from personal statistics; the page reports how many records are
  unattributed. Missing/null `recorded_result` is unfinished and never
  reconstructed. No legacy account-result replay is supported.
- **Online multiplayer** uses the current effective eligible result revision and
  the account's seat, aggregated across match modes per ruleset. Played counts
  completed eligible results only. The detailed card also shows the existing Elo
  when games have been played in that ruleset. Cancelled, waiting and voided games
  do not contribute; voids withdraw prior contributions and concession is a loss. `Timed-out games`
  counts only games the account lost because its own clock expired, never wins
  caused by an opponent's timeout.

Win rate is `wins / (wins + losses + draws)`. Side-specific win rates use the same
denominator restricted to games played on that side. Average length uses the same
completed-game population and the existing accepted-ply ("moves") convention.
Rates and averages with no eligible games render as an em dash. Unfinished games
stay out of completed-result denominators. Lifecycle, effective result and the
engine board outcome remain separate. Only account-scoped records enter personal
statistics.

Ratings are a separate Elo concept from these personal statistics; see
[Rated games](RATINGS.md). The Dashboard's compact **Leaderboard** panel shows one
unified top-five list (provisional players carry a badge) for the selected
ruleset. The ruleset selector sits in the section's top-right corner; it defaults
to **Copenhagen** and persists the last valid choice in browser storage, restored
after reload and navigation and ignored when storage is unavailable. It requires a
session and exposes public account attribution, ratings and rated-game counts. The
full leaderboard keeps the signed-in **Your rank** summary outside the table, so
the standing (rank, Elo, rated games and any provisional threshold) stays readable
when the account is not on the visible page.

On the Dashboard grid **Recorded games** is a compact strip below the reference
columns: at most three records in one row, each a single link to its replay (or
live board) and a **Game archive →** link, so the whole composition stays on one
desktop screen. The lobby listing is not embedded there any more; it keeps its
accessible region name and its own `/online` page, where **Open game** uses the
same primary button style as **New online game** and all cards share the same
padding and section gap.

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
three eligible completed visible games belonging to the current account as its
compact strip, with a **Game archive →** link and no pagination. Normal users see their participating online games
and their own account single-player records; administrators can also see their own
account single-player games. An administrator's Dashboard never becomes an
all-account collection.

Hall (`/archive`) provides bounded server-side category/ruleset/lifecycle/result
filters and pagination. Every account gets a **Record scope** tab list — **All
records**, **Online multiplayer**, **Singleplayer** and **Local 1v1** — which sets
the server-side category and resets the cursor; the private local scope is never
mixed into the default projection. The scope also owns the available lifecycle
values: a direct or bookmarked URL whose lifecycle the scope cannot return is
canonicalized in place (status and cursor dropped) so the hidden filter is never
applied to the backend query. Normal users see their own single-player,
local 1v1 and participating online records and have no player-name filter.
Administrators with trusted current `all_data` permission can inspect online and
account-single-player records across accounts. A nonterminal online record is
marked **Live** and links to the live board (where no analysis is offered);
completed records link to the read-only replay, which offers analysis only where
the backend authorizes it. Local 1v1 records replay read-only without any
advisor, because local games run no search. Their player-name search accepts literal substrings of public display
names, canonical usernames and the displayed **Display Name (@username)** label,
including its `@username` attribution. Full labels up to 148 Unicode code points
can be typed or pasted without truncation. Search uses the same public formatter
as the result labels; counts, cursor pages and the other filters share that query.
Account-linked human seats use the account's current public name for old and
active games alike. Editing or clearing a display name changes
both the returned labels and search matches on the next read, without rewriting
games or changing statistics. Unlinked historical humans and AI participants
retain their saved names. See [game identity and compatibility](PROFILE.md#names-in-games-and-history).
Only server records are visible here. The Local players section is removed;
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
role/participant authorization. History reads grant no game mutation or exports;
online analysis additionally requires authoritative completion. Online IDs always
use online adapters.

Selected review opens in the shared play-screen layout, with the board, player
summary, and Moves/Analytics/My games tabs. The list and filters remain available
through Back to Hall; returning keeps the filters, page and selected-record
highlight, including a return link when that record is outside the filtered list.
Replay controls sit in Moves, rather than changing the board's layout.
Singleplayer review may use the existing authorized
analysis through a read-only historical path that analyzes the selected recorded
cursor without seeking the authoritative game. Completed online reviews use a
separate online-scoped analysis adapter, available to participants and authorized
administrators. Active, waiting, cancelled and voided games never grant analysis.
Review canonically
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

Dashboard reads only account-scoped records. Dashboard cards share padding and
one grid gap. The shared header shows the
signed-in navigation; only it checks the session. Protected destinations retain
their auth guard. See
[the Phase 5 refinement evidence](validation/dashboard-consolidation.md).

The full leaderboard adds country, city and integer **Age**; the compact Dashboard
keeps its four columns. It paginates with an explicit **Page X of Y** counter and
stops on the last page, and clamps back to the first page when the pool shrinks
below the current offset instead of stranding an empty slice. Populated optional
fields are public after any required one-time legacy confirmation. Birth dates
remain private and age uses completed years on the UTC date; see
[profile publication and migration](PROFILE.md).
