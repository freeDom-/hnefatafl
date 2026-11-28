# Rating ledger and rebuilds

[Development index](README.md) · [Ratings guide](../game/ratings.md) · [API](api/README.md)

Elo policy is versioned (`rating.RATING_POLICY_VERSION`). Initial rating 1500,
K=32, win/draw/loss 1/0.5/0, separate ruleset pools, provisional threshold 10,
no inactivity decay. A formula change requires a version bump and a pool rebuild.

## Ledger

Applied result revisions write:

- one durable rating event with a monotonic sequence, effective result revision,
  winner and both seats;
- per-player applications with before/after, delta, opponent rating, expected score,
  score, post-game count and policy version;
- running `player_ratings`.

The result revision, event, applications and running ratings commit in the same
`BEGIN IMMEDIATE` transaction. A retry/duplicate notification cannot double-apply.
History is read in sequence order. Start-rating snapshots are immutable rows written
when a game activates; they are independent of a later pool rebuild and are never
fabricated for older records.

A void/correction writes a new ineligible revision, marks the event withdrawn while
keeping its sequence, and rebuilds the affected pool by replaying all applied events.
Rebuilds run in one write transaction; readers see the old or new pool, never a
partial state. The administrator rebuild endpoint is in the API inventory.

## Eligibility and display

Only verified active humans in normal online games are rated. Player-tournament legs
are seated unrated and never enter a pool. Historical casual records keep their
original policy and are not backfilled. A result whose participant became ineligible
is recorded as not rated and not retried.

Leaderboard ordering uses rounded Elo, then rated-game count, then account ID.
Provisional players remain in one list with a flag. Display names resolve from the
account/current public profile; private data is not exposed. See
[ratings](../game/ratings.md) for player behavior.
