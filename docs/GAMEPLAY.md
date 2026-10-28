# Play, replay and keep games

[Documentation index](README.md) · [Quick start](../README.md#quick-start)

The authenticated [personal dashboard](DASHBOARD.md) is the landing page for new single-player/online games, personal statistics and
read-only history. Hall visibility depends on role; see the dashboard guide.

## Choose where to play

**Singleplayer** uses the authenticated server and account history, as described
below. **Online** uses separate authenticated multiplayer routes; see
[online play](ONLINE_PLAY.md). Both render the same play-screen sections, so the
game-details panel, armies panel, board toolbar and move list stay identical
between them; the single-player sidebar additionally offers post-game analysis and
the online room adds its lifecycle and replay controls.

## Mobile play screen

On phones, a compact game header and both players sit above the board. A fixed
panel below it switches between **Moves**, **Analytics** (single-player), and
**Players**. Switching tabs keeps the board in place; the full game screen fits
the available viewport. Landscape places the panel beside the board.

Move history uses **Earlier moves**, **Later moves**, and **Latest** to reach
all recorded moves without scrolling the page. Short phones show one move per
page; taller phones show two. Selecting a move still uses the existing replay
and version-guarded seek behavior.

Open **Game options** (the three dots) for rules, new-game setup, AI playback,
advisor settings, active-game switching, and account/navigation/appearance
controls. The **Players** tab includes full names, roles, and captured pieces.
The **Analytics** tab shows one agent at a time, with an agent selector when
several have evaluated the position. Detailed settings scroll within the options
dialog; the board stays visible when browsing the game tabs. Active online games
keep their remaining time in the header and offer no live assistance.

Desktop keeps the existing three-column presentation.

## Choose a battlefield

All pieces move like rooks through vacant orthogonal squares. Captures require
closing a sandwich on your move; moving between enemies is safe. The king belongs
to the defenders. Special squares, royal captures and outcomes differ by variant.

| Ruleset | Board and first side | Escape and royal capture | Distinctive rules |
| --- | --- | --- | --- |
| [Brandubh](rules/brandubh.md) | 7×7; attackers | Corner escape; two-attacker sandwich even on center | King-only center/corners; empty center passable and never hostile |
| [Tablut market](rules/tablut.md) | 9×9; defenders | Any-edge escape; two-attacker sandwich | All squares ordinary; not interchangeable with historical/tournament Tablut |
| [Copenhagen](rules/copenhagen.md) | 11×11; attackers | Corner escape; four-sided king capture, three plus empty throne adjacent to it; edge immunity | Shieldwalls, encirclement, exit forts; defender loss on repetition |

The linked rules explain layouts, source interpretation and the finite threefold
repetition convention. Brandubh/Tablut use a no-move draw; Copenhagen uses a no-move
loss. Training/tournament move caps produce **unfinished** games, never extra rules
draws. Ordinary games have no experimental move cap.

## Create and control an account game

1. On Dashboard open **New single-player game** and select a battlefield.
   Human vs AI is the only normal-account mode; administrators can also choose AI vs AI.
2. Your human seat uses the signed-in account. Choose your side and Shield bearer,
   Shield maiden, Jarl or Seer. These are fixed server-resolved presets; this dialog
   has no local-player, custom search, seed or trained-model controls. See [AI](AI.md).
3. Select a piece and a highlighted destination. Mouse and touch use the same
   authoritative server move validation. Arrow keys navigate; Enter/Space select;
   Escape clears selection.
4. Follow the active side, captured pieces, move history and game-over result.
   AI vs AI supports pause, playback speed and automatic legal moves.

The last successful battlefield, permitted mode, side and preset choices are saved in the current browser. Cancelled or
failed creation does not replace it. Games live on the server; browser preferences
hold the current selection, language and appearance. Clearing browser storage
is not deletion of saved games. A connection to the server is required.

## Replay, undo and restart

Account gameplay uses `/games/{id}`, including refresh/reconnect. **Resume single-player**
on Dashboard opens an unfinished account game. Seek, undo and redo inspect retained
positions without changing the recorded final outcome or discarding future moves.
Making a different legal continuation branches history. Completed-game controls
disable further play/restart even when viewing an earlier position. **Restart** is
for unfinished games created under the current account/preset policy: it creates a
new game with a fresh seed and preserves the old record. Historical pass-and-play
or custom-model setups remain readable/playable but cannot create another game
through Restart. AI vs AI restart rechecks administrator permission. Use New game for a new match after completion.

Moves include expected versions; a stale concurrent request is rejected rather
than overwriting another move. Replay uses saved moves, so completed histories
remain viewable after deleting source models. The existing version-guarded game deletion API
removes its moves/result and recalculates statistics. Existing local-player
identities/tombstones remain readable in saved games; their CRUD routes are removed.

## Get advice and read results

**Analytics** below move history on desktop, or in **Game options** on mobile,
selects an optional background advisor. When an
advisor is selected, the panel shows the best-move hint and winning chances; the
advisor suggests a legal move without executing it. AI player opinions can
disagree and remain saved in replay. These displayed values are uncalibrated
expected-score assessments, not measured win probabilities.
See [Analysis](ANALYSIS.md) for sources and completed-search diagnostics.

The [Hall of records](DASHBOARD.md) offers compact read-only rows, server filters,
pagination and independent selected review. Normal users see their participating
online records; administrators can inspect all server-recorded categories. Account
single-player gameplay remains available despite being hidden from normal-user Hall.
Equivalent AI configurations still group across seeds in the existing statistics API.
[Records and personalization](PERSONALIZATION.md) explains identities, model
suggestions, themes and local preferences. [Tournaments](TOURNAMENTS.md) describes
a separate saved comparison collection; its matches do not enter ordinary-game
statistics.
