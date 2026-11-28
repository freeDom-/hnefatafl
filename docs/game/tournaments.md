# Player tournaments

[Game index](README.md) · [Leagues](leagues.md) · [Ratings](ratings.md) · [Interface guide](interface/README.md)

**Player tournaments** are human double-elimination competitions. They are separate
from AI comparison tournaments. Tournament games are **unrated** and never enter
Elo or league standings.

## Format

- Double elimination; one loss moves a player to the lower bracket, two losses
  eliminate them.
- Each match is normally a two-game leg with swapped sides. A drawn or unfinished
  leg follows the tournament's documented tie policy.
- The final follows the bracket policy, including any required reset.
- Tournament results are recorded from authoritative online games, not client
  reports.

## Create and register

From **Trials of Valor → Player tournaments**, discover open tournaments or create
one. Creation chooses the battlefield, entrant limit, schedule, start mode and
round timing. The creator can configure registration and, while permitted, edit or
cancel the tournament.

Registration requires an eligible account. Seeding can be randomised; entrants and
seeds are visible before the bracket starts. Registration and withdrawal close at
the configured time. The creator cannot silently replace a registered player.

## Scheduling and rounds

Tournaments support scheduled starts and synchronized rounds. A round can start
when all required matches are ready or at a scheduled time, depending on the
tournament configuration. Players see the next match, opponent, side and deadline.
A match that cannot be played follows the configured forfeit or administrative
resolution policy.

Creator controls include starting, rescheduling, settling a disputed match and
rebuilding a bracket projection. Every administrative action is auditable. A
player who cannot access the tournament sees its state rather than a fabricated
match.

## Bracket and results

The bracket view shows winners and losers rounds, match links, scores and the
current final. An accessible list alternative exposes the same matches to
screen-reader and narrow-screen users. Selecting a match opens the authoritative
game or its replay. Qualifiers and eliminations update only after settlement.

Tie and draw handling is frozen at tournament creation. Unfinished games are not
silently counted as wins. Corrections use the existing authorized result controls
and retain an audit trail; a rebuild derives standings from authoritative results.

## Moderation and limitations

- Administrators can inspect the audit log, override a match result under the
  documented policy and rebuild a damaged projection.
- A tournament does not change a game's rating or league points.
- Bracket changes after settlement are corrections, not a second tournament.
- Developer implementation and recovery details are in
  [tournament authority](../development/tournament-authority.md).
