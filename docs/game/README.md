# Game

| Topic | Page |
| --- | --- |
| Rulesets, movement, captures, termination, modes and controls | [Gameplay](gameplay.md) |
| The Great Hall, statistics and the Hall of records | [Dashboard and history](dashboard.md) |
| Online lobby, clocks, joining, reconnecting and finishing | [Online play](online-play.md) |
| Signed-out Watch Hall discovery, live viewing and replay | [Public online](public-online.md) |
| Elo eligibility, leaderboard and rating history | [Ratings](ratings.md) |
| League seasons, points, ranks and league achievements | [Leagues](leagues.md) |
| Double-elimination player tournaments | [Player tournaments](tournaments.md) |
| Achievements, portraits, progression and artwork | [Progression](progression.md) |
| Account profile, privacy and appearance | [Profile](profile.md) |
| Records, model suggestions, language, themes and identity runes | [Personalization](personalization.md) |
| Saved analysis, move advisors and replay | [Replay and analysis](replay-and-analysis.md) |
| AI opponents, wrappers, search settings and custom opponents | [Agents](../evaluation/agents.md) |
| AI-only round-robin comparisons | [AI tournaments](../evaluation/ai-tournaments.md) |
| Current UI page guide and screenshots | [Interface](interface/README.md) |

## Rulesets

The three rulesets are not interchangeable. Brandubh is 7×7 with a corner escape;
Tablut market rules are 9×9 with any-edge escape and no special squares;
Copenhagen is 11×11 with four-sided royal capture, edge immunity, shieldwalls,
encirclement and exit forts. Authoritative source citations and digital
conventions live beside the runtime assets:

- [Brandubh](../rules/brandubh.md)
- [Tablut](../rules/tablut.md)
- [Copenhagen](../rules/copenhagen.md)

Those Markdown files are read by the server for the in-game rules panel and must
not be moved.

## Game modes

| Mode | Opponent | Rated | History |
| --- | --- | --- | --- |
| Play vs AI | Fixed preset or administrator-defined custom agent | No | Account single-player |
| Local 1v1 | Two humans on one device | No | Owner-scoped Local 1v1 category |
| Online | Another verified account | Yes, every new game | Online records and Elo |
| Public Watch Hall | None (signed-out read-only) | n/a | Public eligible online games only |

AI-only tournaments are an evaluation tool, not a player mode; see
[AI tournaments](../evaluation/ai-tournaments.md).

## Time controls

Online games use per-move or total-time controls with optional increments only
for total time. The aggregate limit is 60 seconds to 14 days; invalid combinations
are rejected, not clamped. Official games freeze their settings at creation.
Single-player and local 1v1 do not use rated clocks.
