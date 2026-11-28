# Playing a game

[Game index](README.md) · [Online play](online-play.md) · [Interface guide](interface/README.md)

## Where to play

The **Great Hall** offers three creation modes: **Play vs AI**, **Local 1v1** and
**Play online**. **Battlefield** resumes an unfinished account game when one exists
and otherwise offers the same modes. All modes use the same board and move list.

| Mode | Opponent | Rated | Record category |
| --- | --- | --- | --- |
| Play vs AI | Fixed or custom agent | No | Singleplayer |
| Local 1v1 | Two humans on one device | No | Local 1v1 |
| Online Play | Another verified account | Usually yes; player-tournament legs are unrated | Online multiplayer |

A server connection is required. Browser preferences remember the last setup, but
games live on the server; clearing browser storage does not delete them.

## Battlefields

All pieces move like rooks through vacant orthogonal squares. A capture closes a
sandwich on your move; moving between enemies is safe. The king belongs to the
defenders.

| Ruleset | Board / first side | King escape | Distinctive rules |
| --- | --- | --- | --- |
| [Brandubh](../rules/brandubh.md) | 7×7; attackers | Corner | Two-attacker royal sandwich even on the centre; empty centre is passable and not hostile |
| [Tablut market](../rules/tablut.md) | 9×9; defenders | Any edge | No special squares; not interchangeable with historical/tournament Tablut |
| [Copenhagen](../rules/copenhagen.md) | 11×11; attackers | Corner | Four-sided royal capture, edge immunity, shieldwalls, encirclement, exit forts; defender loss on repetition |

The linked rules pages explain source interpretation and the finite threefold
repetition convention. Brandubh/Tablut use a no-move draw; Copenhagen uses a no-move
loss. A training/tournament move cap produces an **unfinished** game, never a rules
draw. Ordinary games have no experimental move cap.

## Opponents

Normal setup offers four fixed presets. Administrators can additionally configure a
custom opponent or run AI vs AI.

| Preset | Style |
| --- | --- |
| Shield bearer | Baseline tactical heuristic |
| Shield maiden | Rules-aware tactical heuristic |
| Jarl | Alpha-beta search |
| Seer | MCTS search |

The dialog describes style and budget, not a measured strength ranking. A trained
model can be used through advisor, training and AI testing flows when compatible
with the battlefield. Search fields and bounds are in
[agent search parameters](../training/parameters.md#agent-search-settings); measured
comparisons are in [agent evaluation](../evaluation/agents.md).

## Create a single-player game

1. On the Great Hall choose **Play vs AI** (or **New single-player game**).
2. Choose the battlefield and your side.
3. Choose one of the fixed opponents above. Administrators can also configure a
   custom opponent and AI-vs-AI.
4. Select a piece, then a highlighted legal destination. Arrow keys navigate,
   Enter/Space selects and Escape clears selection.
5. The computer plays its turns automatically. Captures, move history and the
   result update from the server.

The last successful battlefield, side and preset are remembered in this browser. A
custom opponent configuration is not saved as a browser preference.

## Local 1v1

**Local 1v1** is same-device pass-and-play for two humans. Label both seats, choose
a battlefield, and alternate turns on one screen. Both seats belong to the creating
account; no second account, lobby, clock, AI or advisor is involved. Local games are
never rated and do not enter personal or online statistics. They remain readable in
the owner-scoped **Local 1v1** history category.

## Play screen and controls

Desktop keeps the board between the game/player summary and a tabbed panel:
**Moves**, **Analytics** (single-player) and **My games**. On phones the board stays
in place while the Moves / Analytics / My games panel switches below it; landscape
places the panel beside the board.

- Mouse and touch select a piece and destination.
- Arrow keys move focus; Enter/Space selects; Escape clears selection.
- **Game options** controls advisor settings on mobile.
- **My games** switches between your running account/online games without leaving
  the board.
- History browsing is read-only and never changes the saved game.
- Completed games cannot be restarted from the play screen. Create a new game on
  the Great Hall or Battlefield.

## History, replay and advice

Selecting an earlier move shows that position read-only. **Return to live board**
or the latest move returns to play. AI turns and player input pause while viewing
history. Completed games remain available for replay; deleting a game is a separate
version-guarded action from its record view.

The **Analytics** tab can run a background move advisor. The advisor suggests a
legal move without playing it and shows an uncalibrated expected-score estimate.
AI players can retain independent opinions; see
[replay and analysis](replay-and-analysis.md).

## Restrictions

- Online moves require a verified, eligible account when admission is enabled.
- Analysis is unavailable in live online games.
- Displayed winning chances are position estimates, not measured probabilities.
- The server rejects stale moves and moves from a rewound position.
- A disconnected browser does not stop an online clock or an opponent's turn.
