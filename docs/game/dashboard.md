# The Great Hall, statistics and records

[Game index](README.md) · [Gameplay](gameplay.md) · [Online play](online-play.md) · [Interface guide](interface/README.md)

**The Great Hall** (`/dashboard`) is the authenticated landing page. It brings
together running games, creation actions, your standing, a leaderboard preview and
recent records. **Battlefield** (`/play`) resumes the last unfinished account game
when it still exists and otherwise offers the same creation options.

## Create a game

The Great Hall and Battlefield offer three equal-start modes:

| Action | Opens | Rated |
| --- | --- | --- |
| **Play vs AI** | Single-player setup | No |
| **Local 1v1** | Two-human pass-and-play setup | No |
| **Play online** | Online lobby/creation dialog | Yes, except player-tournament legs |

A selected running game replaces the primary slot with **Continue game**. The
creation dialogs remember the last successful choices in this browser; switching
accounts clears account-bound state.

## The Great Hall overview

- **Games that need you** previews online, local and single-player games awaiting a
  move. When totals exceed the preview, the page says how many are not shown.
- The hero shows the first available running game, prioritizing your turn. The board
  is rendered from server state, not an illustration.
- **YOUR TURN** appears only when the server confirms that you (or a local player)
  must move. Online clocks follow the authoritative deadline and pause while the tab
  is hidden.
- The warrior profile card shows your selected portrait, ruleset standing, online
  win rate, current rated win streak and unlocked portraits.
- **Detailed statistics** opens `/statistics`.
- The compact **Leaderboard** shows the selected ruleset's standings and your own
  rank without inventing an unearned position.
- **Recent Battles** shows at most three completed eligible records with replay links
  and **Game archive →**.

Online Play always opens the authenticated lobby at `/online`; it is not embedded in
the Great Hall and does not depend on active games.

## Personal statistics

**Statistics** (`/statistics`) is relative to your authoritative seat:

- Tabs: **Singleplayer**, **Multiplayer** and **Local 1v1**.
- Ruleset filter and selected tab stay in the URL.
- Per ruleset: outcome distribution plus overall, attacker and defender tables with
  games, wins, losses, draws, win rate and average length.
- Local 1v1 is never mixed into online or account single-player statistics.
- AI configurations can be grouped across seeds without rewriting individual games.

On phones, tables become labelled metric cards. Empty states and stale-data warnings
are explicit; the page does not invent a curve or a completed record.

## Hall of records

**Hall of records** (`/archive`) is the read-only history view. It supports:

- **All records**, **Online multiplayer**, **Singleplayer** and **Local 1v1**.
- Filters for player, ruleset, lifecycle and recorded result.
- Bounded next/previous pagination and visible totals per category.
- An independent selected review that survives filter changes.

Ordinary accounts see records they participated in. Administrators can inspect all
server-recorded categories. Selecting a record opens the shared board and move list;
history navigation changes only the viewer's position. Completed records may offer a
server advisor; live online records do not offer analysis.

Records remain readable after source models or training jobs are deleted. Deleting a
game is separate and version-guarded; it recalculates statistics and never changes
another account's history.

## Privacy and visibility

- Your private single-player and local records are visible only to you and trusted
  administrators.
- Online records are visible to their participants; eligible online games can also
  appear in the public Watch Hall.
- Public pages never expose private history, email, full birth dates, account
  settings or analysis.
- Account identity is the authorization boundary; display names are presentation and
  may repeat.

## Related

- [Replay and analysis](replay-and-analysis.md)
- [Ratings](ratings.md)
- [Profile privacy](profile.md)
- [Public Watch Hall](public-online.md)
