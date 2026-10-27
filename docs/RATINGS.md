# Rated online games and leaderboards

[Documentation index](README.md) · [Online core](ONLINE.md) ·
[Playing online](ONLINE_PLAY.md) · [Dashboard](DASHBOARD.md) ·
[Architecture](../ARCHITECTURE.md)

Phase 6 adds an Elo rating pool and an authenticated leaderboard on top of the
[durable online core](ONLINE.md). Ratings are derived only from authoritative
terminal results of games explicitly created as **Rated**. Every earlier game,
including historical results, remains unrated and is never backfilled.

## Approved policy

| Rule | Value |
| --- | --- |
| System | Elo |
| Initial rating | `1500` |
| K factor | fixed `32` |
| Expected score | `1 / (1 + 10 ** ((opponent_rating - rating) / 400))` |
| Both players | computed from their own pre-game ratings in the same update |
| Precision | full float internally; rounded only for presentation |
| Provisional threshold | fewer than `10` eligible rated games |
| Pools | one per ruleset: `brandubh`, `tablut`, `copenhagen` |
| Separate side/time pools | none |
| Win / draw / loss | `1` / `0.5` / `0` |
| Inactivity decay | none |

The policy is versioned (`rating.RATING_POLICY_VERSION`). A future formula change
must bump the version and rebuild every pool; persisted events and applications
record the policy version that produced them.

## Eligibility

- Only a verified, active account (`online_eligible`) may create or join a Rated
  game. Creation and joining already require this; Rated creation rechecks it and
  returns a useful `403` when it fails.
- Only human-vs-human online games are rated. Guests, AI games, local
  single-player, self-play and training never enter a pool.
- A Rated game that reaches an eligible terminal result while either participant
  is unverified is recorded as not rated. It is not retried later.
- Cancelled and voided games do not count. Concessions and authoritatively
  finalized timeouts do count.
- Each game of a simultaneous/sequential two-game match is rated independently.
  There is no aggregate match score.
- Repeated-opponent games all count with no hidden weighting.

## Rated/Casual selection

The creation form offers an explicit **Rated**/**Casual** choice. The default is
**Casual**, preserving prior behavior. The choice is stored on the match and is
immutable after creation; the API rejects any attempt to change it and clients
cannot forge it. A game's mode is shown in the lobby and in the game room before
joining or playing, and completed Rated games show the authoritative per-player
rating change.

## Persistence, ordering and rebuilds

`online_migrations.py` schema **11** adds:

- `online_matches.rated` — the immutable creation-time flag.
- `rating_events` — one row per applied or withdrawn game, with a durable
  monotonic `sequence`, the effective result revision, the winner and both seats.
- `rating_applications` — the per-player `rating_before`/`rating_after`, `delta`,
  opponent rating, expected score, score, post-game count and policy version.
- `player_ratings` — the current running rating, rated-game count and W/L/D per
  account and pool.

The result revision, its rating event, its application records and the running
ratings commit in the same `BEGIN IMMEDIATE` transaction as the game transition.
A lost response, retry or duplicate notification therefore cannot double-apply a
result. The event `sequence` is assigned under that writer lock and never changes
when a result is corrected. All rating history is read in `sequence` order, never
by request timing or an unordered query.

A void writes a new ineligible result revision, marks the existing event
`withdrawn` (keeping its sequence) and deterministically rebuilds the affected
pool from all `applied` events. Because later games depend on earlier ratings, a
rebuild replays the whole pool rather than subtracting an old delta. Rebuilds run
inside one write transaction, so readers see either the old or the new standings,
never a partially rebuilt pool.

## Leaderboard

`GET /api/leaderboard?ruleset=…&limit=…&offset=…` requires authentication and
returns the qualified standings, provisional players, the signed-in player's own
standing and the effective policy. Display names use `display_name` with the
username as a fallback; no private account data is exposed.

- **Qualified** players have completed at least `10` eligible rated games in the
  pool and are paginated (limit `1–100`, offset `0–100000`).
- **Provisional** players are listed separately, bounded to the top 50.
- Tie handling is deterministic: rating descending, then rated-game count
  descending, then opaque account id ascending. Ranks follow that order.
- The Dashboard embeds a compact leaderboard (top five qualified, provisional
  and the viewer's standing). The full `/leaderboard` view adds ruleset selection
  and pagination.
- Loading, stale/error, empty and unranked states are explicit.

## Reconciliation and recovery

The deterministic rebuild is also exposed to administrators:

```
POST /api/online/ratings/rebuild        # all pools
POST /api/online/ratings/rebuild        # {"ruleset": "tablut"}
```

It requires the `accounts` permission, runs in one writer transaction and is
idempotent. Use it after an out-of-band correction, a suspected partial write, or
a restore that retained rating rows. A compatible stopped-writer restore retains
the **live** rating tables along with the other authoritative online rows, so
standings are not rewound; a snapshot-only disaster restore may require a rebuild
and explicit operator reconciliation. See
[online migration and recovery](ONLINE.md#migration-backup-and-recovery).

## Known limitations

- **Repeated-opponent farming is possible.** Two verified accounts can play many
  Rated games against each other and inflate one rating. There is no hidden
  weighting, match cap, cooldown or anti-farming subsystem in Phase 6. Operators
  must monitor for abuse and use the existing void procedure for incidents.
- Rating eligibility is decided at result time from the stored accounts; a later
  verification change does not retroactively rebuild old events.
- There is no rating decay, rewards, achievements, aggregate match score, public
  or unauthenticated leaderboard, or online analysis/assistance.
- Production online admission and timeout finalization remain disabled. Rated
  games can only be created when admission is enabled, and timeout results count
  only when the separately approved timeout gate is active.
- The leaderboard reflects completed, applied events only; in-progress games do
  not appear.

## Production prerequisites

Before enabling Rated play in production:

1. Complete the existing [online admission and timeout prerequisites](ONLINE.md).
2. Confirm schema 11 migration and a compatible backup/restore on a copy.
3. Decide and document a moderation/anti-farming response using the existing
   private void audit and rebuild procedures.
4. Review leaderboard display-name and account-deletion behavior with the
   operator.
