# Leagues

[Game index](README.md) · [Ratings](ratings.md) · [Online play](online-play.md) · [Interface guide](interface/README.md)

**Leagues** are seasonal online competitions. Seasons are calendar months in UTC,
with separate Brandubh, Tablut and Copenhagen standings. League games use the
normal rated online engine and Elo pool; league points are a separate seasonal
score.

## Points and tiers

| Result | League points |
| --- | ---: |
| Win | +3 |
| Draw | +1 |
| Loss | −1 |
| Floor | 0 after each result |

| Tier | Minimum points |
| --- | ---: |
| Midgard | 0 |
| Nidavellir | 6 |
| Alfheim | 15 |
| Asgard | 30 |
| Valhalla | 50 |

League points measure seasonal progress, not playing strength. Inactivity never
subtracts points. A loss or official correction can demote you. Season rollover
resets league points but never Elo or permanent achievements. Ranking requires
three eligible results; before that, progress is shown as unranked.

Standings sort by points, wins and fewer losses. Equal sporting records share a
rank. An empty population never gets invented opponents, ranks or percentiles.

## League achievements

Each ruleset has a five-tier league achievement family using the same tier names
and thresholds as the standings. Reaching a tier qualifies for its ordinary
achievement points and every preceding tier. A permanent highest tier is retained
after demotion, inactivity, season rollover or later corrections. Reaching the same
tier in a later season does not duplicate an award. Progress in one ruleset never
affects another.

## Queue and games

League play is entered through the **Leagues** queue. Eligible accounts may have
one active league game across all rulesets. Games use a fixed 10-minute total clock
per player with a 5-second increment. Queue entries expire after ten minutes or at
the season boundary, whichever comes first.

- Only active, verified human accounts with normal play permission may queue.
- Administrator accounts are treated as human accounts; admin permission is not an
  eligibility bypass.
- AI opponents and player-tournament games never enter league standings.
- Joining, cancelling and matching are idempotent. Cancellation targets the
  original queue request, so a delayed retry cannot cancel a later entry.
- Admission is rate-limited; an operator can pause matchmaking during an incident.

## Results and grace period

Games count through the end of the 24-hour grace period after the season ends. A
game finishing exactly at the boundary is late. Late games keep their Elo result
but contribute no league points. Season standings remain provisional until grace
ends. Later official corrections or voids can revise archived standings with a
visible revision.

The league administration view reports worker health, queue backlog, pending
settlements, retries and rebuild state. Administrators can pause matchmaking,
cancel stale entries and rebuild a season from authoritative results. Player-facing
progress remains visible while a rebuild keeps permanent achievements and hides
temporary seasonal values.

## Related

- [Ratings](ratings.md): Elo policy and leaderboard.
- [Player tournaments](tournaments.md): separate unrated competition.
- [League authority](../development/league-authority.md): API, queue, settlement
  and rebuild implementation.
- [Installation recovery](../installation/updating.md#league-worker-recovery).
