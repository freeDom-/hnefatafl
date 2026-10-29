# Playing online

[Documentation index](README.md) · [Rules and board controls](GAMEPLAY.md) ·
[Online operations](ONLINE.md) · [Phase 3 evidence](validation/online-phase3.md)

Online play requires a signed-in account. Open **New online game** on Dashboard. The old `/online` entry redirects to
Dashboard with online setup selected. Admission is disabled by default; the lobby explains when the
operator has disabled new games or your account is not eligible. Creating/joining
requires the backend's current active/verified/play eligibility. An existing seat
can keep playing after verification is removed, while its account/session remains
valid. Visibility is authenticated: this is not an anonymous spectator service.

Single-player is a separate authenticated surface; it never feeds online
results, and online IDs never enter its replay/AI adapter.

The authenticated [personal dashboard](DASHBOARD.md) combines the existing lobby with personal statistics and compact recent records.
Hall provides participant-scoped online history and read-only review.

The lobby shares Dashboard card spacing and loads automatically. Five-second polling,
bounded failure backoff and online/focus/visible-tab resynchronization cover creation,
joining, activation and completion. It retains stale data with an actionable warning
and retry while disconnected. There is no manual lobby Refresh or persistent connected
banner; the live game room retains its own refresh controls.

## Create, find and join

Choose Brandubh, Tablut or Copenhagen, your side (attackers, defenders or random),
a match mode, a **Time control**, and integer
**Days** (0–14), **Hours** (0–23), **Minutes** (0–59)
in a shared modal. Desktop uses two columns for game and clock settings; narrow
screens stack them. A seconds increment is offered only for **Total per player**.
Every newly created game is **rated** and counts toward the
[leaderboard](RATINGS.md); there is no Casual selector. Rated play requires an
active, verified account, checked at creation and joining. Configured time uses the shared duration formatter (`45s`, `2m 05s`, `1h 00m 00s`). The combined base limit is converted to seconds
at the API boundary. The approved aggregate remains 60–1209600 seconds (1 minute
through 14 days), default three days: all-zero and 14 days plus extra hours/minutes
are rejected, never clamped. The component ranges do not widen that aggregate policy.
Existing games/deadlines retain exact seconds. Retrying an already retained request
with a sub-minute remainder keeps its exact original duration and identifies it.
The settings freeze
at creation. To change them, cancel the waiting game and create another.

- **Single game** starts one game when another eligible account joins.
- **Two-game match — simultaneous** starts two games at join, with swapped sides.
  Both can be played at the same time.
- **Two-game match — sequential** starts the return game with swapped sides when
  the first completes. The game links discover it automatically.

Random sides stay unassigned until the second player joins. The rules determine
who moves first; creator identity does not determine the first mover.

The lobby uses three tabs: **All games** shows every nonterminal game visible to
your authenticated account, **My games** includes nonterminal games you created or
in which you have a seat, and **My turn** shows active games where you are the
participant whose move is required. Completed, cancelled and voided games never
appear in any tab. Previous/next page buttons use bounded server pagination. Open
a waiting game to join it; creators can cancel their own waiting game. Players
cannot edit its settings in place. Active cards show the remaining time from the
authoritative deadline using the shared formatter.

If creating reports an uncertain outcome, check **My games** before trying again.
The browser retains that creation request and its frozen settings for the signed-in
account in this tab, including across reloads where session storage is available.
**Retry retained request** deliberately sends the same request ID. A conflict can
mean the original request already committed; it is not a new successful creation
response. Check your games before explicitly discarding the request and starting
another. No request is retried automatically. With browser storage disabled, keep
the tab open and use My games to recover after reload. Logging out/account switching
clears the retained request.

Creation quotas come from the server. A limit response can require cancelling an
outstanding waiting game or waiting for the rolling budget to replenish. Cancelling
a game does not refund the rolling creation budget. The UI does not assume an
operator's quota values.

## Play and finish

The online game screen uses the same overall layout and shared board/replay
components as single-player: game details and players on the left, the board in
the centre, and the move list on the right. Online-specific differences are
deliberate: there are no move helpers, suggested moves, analysis, win
probabilities, version labels, manual refresh buttons, connection-state label or
"Back to online lobby" button. Remaining time to move is shown in the game
details. A compact switcher lists active online games in which you participate,
marks the current game and flags games awaiting your move; the switcher replaces
the per-match version navigation.

The board always shows the last server-confirmed position. On your turn, select
your piece, then an empty destination. Arrow keys move keyboard focus; Enter or
Space selects a square and Escape clears selection. The server checks the move;
there are no hints, legal-move assistance, AI or evaluation controls online.
When attackers capture the king, the terminal board still shows the king on its
final square; this is a display-only overlay derived from the recorded moves and
never changes the authoritative board, legal moves or stored history.

Pending actions disable duplicate submission. Only the seated player whose turn it
is can move or concede. **Concede** asks for confirmation. The effective result is
shown even when the underlying engine position has no winner, as with concession
or timeout. A deadline can be finalized instead of accepting a requested move or
concession; the UI reports that distinction explicitly.

Match links identify each game's own status/version and navigate to its board.
Both simultaneous games are refreshed. Each has a separate turn, deadline and
result. There is no aggregate score or match winner. In a Rated match each game
is rated independently, and a completed Rated game shows the authoritative
per-player rating change. A void does not imply that the sibling is voided.

## Connection and time

The page refreshes automatically, normally five seconds after a successful read.
It does not stack overlapping refresh cycles. Failures back off up to 30 seconds;
focus, reconnect or tab visibility requests a fresh read. There is no manual
refresh button or persistent connection label. Unavailable responses keep the
last confirmed board, show an actionable retry notice and disable game actions
until recovery. Navigation/logout abort pending work and discard protected online
view state.

A version conflict reloads the game without replaying the action. An uncertain
move/concession outcome also reloads: inspect the board and result before deciding
what to do. Session expiry returns to the existing login flow. Eligibility/turn
errors do not grant a bypass.

Deadlines arrive as UTC instants and display in your browser's local time zone.
The countdown is approximate and uses your device clock. Only the server decides
expiry. When the server deadline is reached, the player to move loses and the
game’s ratings update automatically. **Time expired. Finalizing result…** briefly
indicates that the browser is waiting for the authoritative result; an explicit
operator pause can delay finalization. The browser never declares a result or
extends the deadline.

The online game dialog offers **Per move** (default) or **Total per player**.
Both use a base limit of 1 minute to 14 days. Total mode gives each player a
separate budget; only their own turns consume it. **Increment per move (seconds)**
defaults to 0 and accepts whole seconds from 0 to 1209600. It is shown only in
total mode and is credited to the moving player after each timely move. Switching
to per-move time clears the increment; a new per-move request always submits zero.
Per-move time resets to the base limit without carrying unused time.
Previously saved games and retained uncertain requests keep their exact settings;
the server still reads historical per-move increments without reinterpreting them.
Each leg of a two-game match starts with fresh clocks.
Both clocks appear beside the players on mobile; **Game options** shows the
configured limit and clock details, including the increment for total-time games.

**Disconnecting, closing the browser or logging out does not pause clocks.**

## Viewing and replay

Authenticated viewers can inspect games allowed by the current backend visibility
policy. Spectators cannot move or concede. In **Moves**, choose **Starting position**
or a recorded move to load the validated server replay. There is no separate
View replay button. Selecting history changes only your local view; it does not
change a shared cursor. **Read-only history** marks the board. Choose **Return to
live board** or the latest recorded move before submitting moves. Live polling continues
while viewing history and reports newly available moves.

Online replay provides no seek/undo/branch mutations or exports. **Completed**
online games reuse the existing post-game Analytics/Winning chances experience
through an online-scoped adapter; ongoing online games never expose analysis,
suggested moves, evaluation or win probabilities, and a client countdown reaching
zero is not treated as completion. The backend enforces terminal-state
eligibility in addition to the UI. Administrator roles receive no active-game
advice bypass.
