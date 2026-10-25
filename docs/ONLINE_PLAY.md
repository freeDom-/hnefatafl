# Playing online

[Documentation index](README.md) · [Rules and board controls](GAMEPLAY.md) ·
[Online operations](ONLINE.md) · [Phase 3 evidence](validation/online-phase3.md)

Online play requires a signed-in account. Choose **Online** in the main navigation
or open `/online`. Admission is disabled by default; the lobby explains when the
operator has disabled new games or your account is not eligible. Creating/joining
requires the backend's current active/verified/play eligibility. An existing seat
can keep playing after verification is removed, while its account/session remains
valid. Visibility is authenticated: this is not an anonymous spectator service.

## Create, find and join

Choose Brandubh, Tablut or Copenhagen, your side (attackers, defenders or random),
a match mode and a whole number of seconds per move. The supported duration is
60–1209600 seconds; the default is 259200 seconds (three days). The settings freeze
at creation. To change them, cancel the waiting game and create another.

- **Single game** starts one game when another eligible account joins.
- **Two-game match — simultaneous** starts two games at join, with swapped sides.
  Both can be played at the same time.
- **Two-game match — sequential** starts the return game with swapped sides when
  the first completes. The game links discover it automatically.

Random sides stay unassigned until the second player joins. The rules determine
who moves first; creator identity does not determine the first mover.

**All games** shows games visible to your authenticated account. **My games**
includes games you created or in which you have a seat. Previous/next page buttons
use bounded server pagination. Open a waiting game to join it; creators can cancel
their own waiting game. Players cannot edit its settings in place.

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

The board always shows the last server-confirmed position. On your turn, select
your piece, then an empty destination. Arrow keys move keyboard focus; Enter or
Space selects a square and Escape clears selection. The server checks the move;
there are no hints, legal-move assistance, AI or evaluation controls online.

Pending actions disable duplicate submission. Only the seated player whose turn it
is can move or concede. **Concede** asks for confirmation. The effective result is
shown even when the underlying engine position has no winner, as with concession
or timeout. A deadline can be finalized instead of accepting a requested move or
concession; the UI reports that distinction explicitly.

Match links identify each game's own status/version and navigate to its board.
Both simultaneous games are refreshed. Each has a separate turn, deadline and
result. There is no aggregate score, match winner or rating. A void does not imply
that the sibling is voided.

## Connection and time

The page refreshes automatically, normally five seconds after a successful read.
It does not stack overlapping refresh cycles. Failures back off up to 30 seconds;
focus, reconnect or **Refresh** requests a fresh read. Unavailable responses keep
the last confirmed board, mark it stale and disable game actions until recovery.
Navigation/logout abort pending work and discard protected online view state.

A version conflict reloads the game without replaying the action. An uncertain
move/concession outcome also reloads: inspect the board and result before deciding
what to do. Session expiry returns to the existing login flow. Eligibility/turn
errors do not grant a bypass.

Deadlines arrive as UTC instants and display in your browser's local time zone.
The countdown is approximate and uses your device clock. Only the server decides
expiry. **Awaiting adjudication** means your clock has passed the deadline while
the server still reports Active; timeout finalization may be paused. The browser
never declares a result or extends the deadline.

**Disconnecting, closing the browser or logging out does not pause clocks.**

## Viewing and replay

Authenticated viewers can inspect games allowed by the current backend visibility
policy. Spectators cannot move or concede. **View replay** loads the validated
server replay. Previous/next move changes only your local view; it does not change
a shared cursor. **Historical replay · read only** clearly marks the board.
**Return to live board** is required before submitting moves. Live polling continues
while viewing history and reports newly available moves.

Online replay provides no seek/undo/branch mutations, exports or analysis settings.
Administrator roles receive no active-game advice bypass.
