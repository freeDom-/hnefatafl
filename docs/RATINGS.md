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
- Only human-vs-human online games are rated. AI games, local
  single-player, self-play and training never enter a pool.
- A Rated game that reaches an eligible terminal result while either participant
  is unverified is recorded as not rated. It is not retried later.
- Cancelled and voided games do not count. Concessions and authoritatively
  finalized timeouts do count.
- Each game of a simultaneous/sequential two-game match is rated independently.
  There is no aggregate match score.
- Repeated-opponent games all count with no hidden weighting.

## Rated-only new games

Every newly created online game is **rated**. There is no Casual selector and no
obvious casual-mode UI. The API accepts an omitted `rated` field and rejects an
explicit `false` (or any non-boolean value), so a legacy or forged request cannot
create an unrated game. The stored `online_matches.rated` flag remains immutable.

Historical casual games are never altered, reclassified or backfilled, and
existing in-progress legacy casual games finish under their original policy with
no invented rating changes. The internal `rated` field is retained for reading
those records even though it is no longer a creation choice.

## Start-rating snapshots

Schema 12 adds `online_game_start_ratings`, one immutable `(game_id, account_id)`
row per participant, written when a game actually activates (both participants
established) rather than when an open challenge is created. Each leg of a
multi-game match captures its own snapshots. The value is the participant's
running Elo for the ruleset at that moment, or the initial `1500` for an unrated
account. Capture is idempotent, so duplicate activation/join requests cannot
duplicate a snapshot.

Start snapshots are deliberately independent of the rating ledger: a pool rebuild
never rewrites them, and they can differ from the pre-result rating of a
concurrent game. They are shown in the Hall as `Name (1354)`. Older records
without a reliable snapshot show only the name; the system never fabricates a
snapshot from the current rating.

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
returns one unified standings list plus the effective policy. Display names use
`display_name` with the username as a fallback; no private account data is
exposed.

- One list contains both provisional and nonprovisional players. Provisional
  players are **not** excluded or placed in a separate ranking section; each row
  carries a `provisional` flag (`fewer than 10` eligible rated games).
- Ordering is Elo descending, then more completed rated games in that ruleset,
  then the immutable account id ascending. Casual games and aggregate two-game
  matches never affect the order. Ordering is applied before pagination.
- **Precision:** ranking and presentation both use Elo rounded to the nearest
  integer, so the displayed value and the order can never disagree. Equal
  displayed Elo is broken by the rated-game count, then the account id.
- The list is paginated (limit `1–100`, offset `0–100000`) with a `total` count.
- The Dashboard embeds a compact leaderboard (top five) and links to the full
  `/leaderboard` view, which adds ruleset selection and pagination. The separate
  "your standing" paragraph was removed; the signed-in player's row is marked in
  place when it is on the page.
- Loading, stale/error and empty states are explicit.

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
