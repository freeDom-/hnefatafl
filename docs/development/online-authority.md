# Online game authority

[Development index](README.md) · [Online guide](../game/online-play.md) · [Operations](../administration/online-operations.md) · [API](api/README.md)

## Lifecycle

Online games are durable server records, not browser sessions. The server owns
creation, admission, seats, deadlines, moves, timeout finalization, result revisions
and replay. A waiting game becomes active when the second eligible account joins;
the rules decide who moves first. Two-game matches create independently versioned
legs, possibly with swapped sides.

Creation and joining require an active, verified, eligible account under the
current online settings. Admission and public discovery are operator-controlled.
Creation quotas and waiting limits are computed server-side; a two-game match counts
as one creation. Cancelling does not refund the rolling budget.

## Moves, deadlines and results

Every move carries the expected `version`. The server validates legality and the
seated player's turn, commits the new immutable state and advances the deadline in a
write transaction. Stale moves fail rather than overwriting newer state.

Deadline finalization is authoritative and runs in the server lifespan. Automatic
timeout losses are enabled by default; an operator can pause finalization during
maintenance/recovery without changing existing games. A pending move/concession and
a deadline are resolved atomically, so a timeout either grants the move or finalizes
the loss, never both.

Result revisions are durable. Corrections/voids create a new effective revision and
drive rating and league settlement; they do not rewrite the original recorded game
JSON. Cancelled/voided games do not count for ratings. Replay is read-only.

## Persistence and authorization

Online tables are additive migrations. Seats reference account IDs; display names
and portraits are resolved at read time from the safe public projection. A client
cannot supply another account's seat, private record or asset path. Administrators
may inspect all records and void results under the documented audit path.

Matchmaking uses unique constraints/requests and serialized writes for creation,
join, queue and settlement. Retries with a retained request ID are idempotent; a
delayed request cannot target a later game or queue entry. Leagues add a separate
seasonal queue/worker snapshot, but use the same online authority for gameplay and
official results.

## API boundary

The public/lobby projection is bounded and paginated. Completed records move to the
Hall; the authenticated lobby lists nonterminal games only. Live online games expose
no analysis. Public spectator reads use a separate allowlisted GET namespace; eligible
completed public replays may expose already-stored terminal analysis, but public
requests never compute/modify it. See
[public API](api/README.md#progression-and-public-spectator), [rating authority](rating-authority.md) and
[league authority](league-authority.md).
