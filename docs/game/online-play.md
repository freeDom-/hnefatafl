# Online play policies

[Game index](README.md) · [Arena interface](interface/arena.md#online-play) · [Ratings](ratings.md) · [Leagues](leagues.md)

Online play requires a signed-in account. The authenticated lobby is at `/online`;
normal online games are rated except player-tournament legs. Admission, quotas and
registration are operator-controlled; rated play requires an active, verified account
with normal play permission.

## Match modes

| Mode | Behavior |
| --- | --- |
| Single game | One game; sides assigned at join |
| Two-game match, simultaneous | Two side-swapped games active together |
| Two-game match, sequential | Return game starts after the first completes |

Each leg has its own turn, deadline, result and rating. There is no aggregate match
winner. Random sides stay unassigned until the second player joins; the rules decide
who moves first.

## Clocks and admission

- Time control is per move or total per player; increments apply only to total time.
- The aggregate limit is 60 seconds to 14 days, default 3 days; invalid combinations
  are rejected, not clamped. Settings freeze at creation.
- The server owns deadlines. Hiding the browser suspends only local countdown
  rendering; the deadline and opponent's turn continue.
- Creation quotas/waiting limits are server settings; a two-game match counts as one
  creation and cancelling does not refund the rolling budget.
- If creation/join is uncertain, check your games before retrying; a retained request
  can be explicitly retried.

## Lifecycle restrictions

Only the seated player whose turn it is may move or concede. Concessions and
authoritative timeout finalization count as results; cancelled/voided games do not.
A deadline may finalize instead of accepting a pending move/concession, and the UI
reports which happened. There is no advisor, hint, win probability or AI analysis in a
live online game.

Reconnect/lobby polling and stale-data warnings are UI behavior documented in the
[Arena interface](interface/arena.md#online-play). Public viewing of eligible games is
in [the public Watch Hall](interface/arena.md#public-watch-hall).
