# Ratings and leaderboard

[Game index](README.md) · [Online play](online-play.md) · [Leagues](leagues.md) · [Interface guide](interface/README.md)

Hnefatafl uses an Elo rating pool per ruleset. Ratings come only from
authoritative results of games created as **Rated**; earlier historical games are
never reclassified or backfilled.

## Policy

| Rule | Value |
| --- | --- |
| System | Elo |
| Initial rating | 1500 |
| K factor | 32 |
| Expected score | `1 / (1 + 10 ^ ((opponent - own) / 400))` |
| Score | Win 1 · Draw 0.5 · Loss 0 |
| Pools | Separate Brandubh, Tablut and Copenhagen pools |
| Provisional | Fewer than 10 eligible rated games |
| Inactivity decay | None |

Each player's new rating is calculated from both pre-game ratings in the same
result update. Displayed ratings are rounded to whole numbers for both ranking and
presentation, so order and display cannot disagree.

## Which games are rated

| Game | Rated? |
| --- | --- |
| New normal online game | Yes |
| League game | Yes, using the normal pool and policy |
| Player-tournament leg | **No** — the server seats these unrated |
| Historical casual game | Uses its original policy; never backfilled |
| Single-player, local 1v1, AI or training game | No |

Every newly created normal online game is rated; there is no Casual selector.
Cancelled and voided games do not count. Concessions and finalized timeouts do.
Each leg of a two-game match is rated independently. Repeated opponents are not
weighted.

Rated participation requires a verified, active account with normal play
permission. If a participant becomes ineligible before an eligible terminal result,
that result is recorded as not rated and is not retried later.

## Leaderboard

The **Leaderboard** shows one standings list per ruleset, including provisional and
qualified players. Columns include rank, player, rating, rated games, wins, losses
and draws. Ordering is rating, then more rated games in that ruleset, then a stable
account tie-breaker. Your own row is shown separately when it falls outside the
visible page.

Displayed identity uses **Display Name (@username)** when a profile display name
differs from the username. Shared display names are not unique identifiers. Private
account data is never published.

## Rating history

**My Profile** and the rating history view show your rating changes over time,
including the result, opponent and new rating for each applied game. Corrections and
voids can revise a displayed result; the history identifies the effective revision
rather than hiding an official correction.

## Limitations

- Elo measures results in this pool, not general Hnefatafl strength.
- Provisional ratings are shown as provisional; they are not a separate ranking
  section.
- The leaderboard does not include human tournaments, leagues as a separate score,
  or unrated historical games.
- Rating implementation, rebuilds and result revisions are documented in the
  [rating authority](../development/rating-authority.md).
