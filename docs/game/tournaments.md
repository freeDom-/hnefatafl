# Player tournaments

[Game index](README.md) · [Leagues](leagues.md) · [Ratings](ratings.md) · [Competition interface](interface/competition.md)

**Player tournaments** are human double-elimination competitions, separate from AI
Testing. Tournament games are **unrated** and never enter Elo, league standings or
rated-only achievements.

## Format

- Double elimination: a participant is eliminated after **two match losses**, never
  after one lost leg.
- New tournaments require a **two-game side-swapped match**, played either
  **sequential** (default) or **simultaneous**. Each game has its own clock;
  advancement waits for both results.
- Historical single-game/legacy tournaments retain their original execution and
  recorded outcomes.
- If the lower-bracket finalist wins the first grand final, a **bracket reset** is
  played between the same participants to decide the champion.
- The bracket is the smallest power of two at or above the entrant count. Seeds above
  the entrant count are byes; a bye advances without playing or taking a loss.

### Tie and draw handling

| Situation | Decision |
| --- | --- |
| One participant wins more decided legs | Match winner |
| All legs drawn, or decided legs split with no majority | **Higher original seed** advances |
| Grand final with no leg majority | **Undefeated upper-bracket finalist** wins; no reset |
| Bracket-reset match with no leg majority | Higher original seed advances |
| Match has one participant because of a bye | That participant advances without a loss |
| Administrator forces a ready match | Named participant wins; outstanding games are voided |
| All legs voided | Falls back to the higher-seed rule |

A voided leg is neither a win nor a draw. A random winner is never generated.

## Seeding and bracket

Seeds are assigned by the server **when the tournament starts**, not at registration.
Every registered eligible participant receives one persistent seed from a uniform
random draw; registration order, account ID, username, rating and previous results
do not determine it. Seeds are immutable after the start commits and are reused by
settlement/corrections/rebuilds. The bracket places higher seeds as far apart as
possible; registration shows only a capacity-based preview with unassigned seed
placeholders.

When a modern two-game tournament starts, synchronized global rounds open together.
Upper round N and lower round N−1 share a round where both exist; the next round
opens after every current match is decided, including byes. Remaining lower rounds,
the grand final and any reset follow. Historical formats retain independent
advancement.

## Registration and start

- There is **no separate registration deadline**. Registration stays open until the
  tournament fills or starts.
- A planned start time is optional. A full tournament waits for that planned time;
  its creator cannot start it early.
- At/after the planned time a full tournament starts automatically without a browser
  or administrator session.
- An unfilled tournament remains in registration. Once the planned start is missed,
  later registrations can fill it; the creator or an administrator can set a new
  future time or start manually with at least two entrants.
- Deployment downtime/gate release resumes due work. A failed start rolls back and
  retries with bounded backoff.
- Creator start requires at least two eligible entrants. Administrators can override
  the planned time. Ordinary creators cannot use administrator-only moderation.
- Active verified accounts may create at most three active tournaments and five in a
  rolling 24-hour window; administrators bypass these quotas. An init-admin
  administrator can create/manage before email verification; ordinary accounts
  require verification. The stored creator can start/manage their own
  registration-phase tournament; administrators can moderate any tournament. Creation
  never grants global Workshop or account-management permission.

A changed entrant set invalidates an outstanding start confirmation. The server
uses its authoritative clock for scheduled/manual start and reports waiting/full/
missed states.

## Results and corrections

Tournament legs are authoritative online games. Both games must settle before a
modern match advances. Results and replays are reachable from the bracket and the
Hall of records. A later result revision that changes a settled leg marks the bracket
disputed and stops advancement until an administrator rebuilds it. Completed
tournaments can reopen if a correction removes the official winner.

Administrators can inspect the audit log, override a ready match under the documented
policy and rebuild a damaged projection. No random winner is generated. Bracket
changes after settlement are corrections, not a second tournament.

## Related

- [Competition interface](interface/competition.md): discovery, registration,
  bracket and match controls.
- [Tournament authority](../development/tournament-authority.md): API, state model,
  schema, scheduling and recovery internals.
