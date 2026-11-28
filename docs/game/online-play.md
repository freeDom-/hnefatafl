# Online play

[Game index](README.md) · [Gameplay](gameplay.md) · [Ratings](ratings.md) · [Leagues](leagues.md)

Online play requires a signed-in account. **Online Play** in the Arena navigation
always opens the authenticated lobby at `/online`; it does not auto-resume a game.
Create a game from the lobby or from **Battlefield** with **New online game**.

Admission is controlled by the operator. When disabled, the lobby explains that
new games or joining are unavailable. Rated play requires an active, verified
account with normal play permission.

## Create, find and join

Choose a battlefield, your side (attackers, defenders or random), a match mode and
a **Time control**:

| Setting | Options |
| --- | --- |
| Match | Single game; two-game match, simultaneous; two-game match, sequential |
| Clock | Per move or total time per player |
| Increment | Total-time controls only |
| Limit | 60 seconds to 14 days, default 3 days |

The settings freeze at creation. To change them, cancel a waiting game and create
another. Random sides are assigned when the second player joins. The rules decide
who moves first.

The lobby has three tabs: **All games**, **My games** and **My turn**, plus a
ruleset filter and bounded page navigation. Completed, cancelled and voided games
are not listed. Open a waiting game to join it; creators can cancel their own
waiting game. A **Filter by ruleset** select narrows the list server-side.

Creation quotas and waiting limits are operator settings. Cancelling a waiting game
does not refund the rolling creation budget. If a creation response is uncertain,
check **My games** before retrying; the browser may retain the original request for
an explicit **Retry retained request**.

## Play and finish

The board always shows the last server-confirmed position. On your turn, select a
piece then a legal destination. The server validates the move. There is no move
advisor, suggested move, win probability or AI analysis in a live online game.

- Only the seated player whose turn it is may move or concede.
- **Concede** asks for confirmation.
- A deadline may finalize a timeout instead of accepting a pending move or
  concession; the result explains which happened.
- Remaining time follows the authoritative deadline. Waiting and completed games
  do not show an invented clock. Hiding the browser tab suspends local countdown
  rendering only; the deadline continues to run.
- A compact switcher lists your active online games, marks the current game and
  flags games awaiting your move.

Each leg of a two-game match has its own turn, deadline and result. There is no
aggregate match score. Each leg is rated independently except player-tournament
legs, which are unrated.

## Connection and time

The lobby refreshes automatically and resynchronizes on focus, visibility and
reconnect. A stale lobby shows an actionable warning and retry instead of pretending
data is current. The live game room has its own refresh controls. A browser
disconnect does not pause an online clock or the opponent's turn.

Time controls are inclusive: all-zero durations and more than 14 days are rejected,
never clamped. Existing games keep their exact stored seconds.

## Viewing and replay

Completed online records open read-only in the **Hall of records** with replay and,
where permitted, analysis. Live games can be watched under the existing permissions.
The signed-out public Watch Hall exposes only eligible online games; see
[public online](interface/arena.md#public-watch-hall).

## Related policies

- [Ratings](ratings.md): Elo eligibility, leaderboard and history.
- [Leagues](leagues.md): seasonal queue, points, tiers and grace period.
- [Player tournaments](tournaments.md): unrated tournament legs and scheduling.
- [Interface guide](interface/README.md): online lobby and game screens.
