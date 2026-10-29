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
**My games**. Switching tabs keeps the board in place; the full game screen fits
the available viewport. Landscape places the panel beside the board.

Move history scrolls inside the panel, keeping the board in place. All recorded
moves are available in one list. Selecting a move still uses the existing replay
without changing the saved game. AI turns and player input stop while viewing past positions.

The AI plays automatically at the live position whenever it is its turn in an unfinished game;
there are no pause/resume controls on mobile or desktop.
Open **Game options** (the three dots) for advisor settings.
**My games** lists your unfinished single-player games and active or waiting
online games, including the current game. Select any row to open that game;
additional rows scroll inside the panel while the board stays visible.

The **Analytics** tab shows one agent at a time, with an agent selector when
several have evaluated the position. Detailed settings scroll within the options
dialog. Active online games keep their remaining time in the header and offer no
live assistance. The header's back link returns to Dashboard for new-game setup,
account controls, appearance, language, and other pages.

Desktop keeps the board between a compact game/player summary and a tabbed panel
for **Moves**, **Analytics**, and **My games**. Game mode and random seed appear
with the players; the separate Move detail is removed. The selected tab stays open
when switching games, falling back to Moves when that game does not offer the tab.
Shorter desktop windows
condense the summary according to actual browser viewport height, including the
space lost to browser bars and the operating-system taskbar.

The board fills the available width or height, with a minimum gap between columns
even at compact interface scale. The surrounding panels do not scroll: only the
move list or the active Analytics/My games tab scrolls, avoiding nested scrollbars.
History's read-only notice and return button appear above the move-table headings.

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
   The computer plays its turns automatically at a fixed pace.

The last successful battlefield, permitted mode, side and preset choices are saved in the current browser. Cancelled or
failed creation does not replace it. Games live on the server; browser preferences
hold the current selection, language and appearance. Clearing browser storage
is not deletion of saved games. A connection to the server is required.

## Read-only history and live play

Account gameplay uses `/games/{id}`, including refresh/reconnect. Select a move
in the history list to inspect its position. History is local to your viewer:
browsing never seeks, branches, or overwrites the saved game, and does not start
new AI searches. Analytics shows retained estimates for past positions.

Select **Return to live board**, or the latest move, to continue. Only the account
bound to the active human seat can move; the AI plays automatically on its turn
at the live position. Completed games remain read-only. The play screen has no
previous/next move, undo/redo, restart, or New game buttons. Create new games on
Dashboard instead. On mobile, the space beneath the board goes to the tabs.

Moves include expected versions; stale concurrent requests cannot overwrite a
newer move. The server also rejects human and AI moves from a rewound saved
position. Existing administrative seek/restart APIs remain for compatibility;
the play screen browses history through validated replay reads. Saved histories
remain viewable after deleting source models. Deleting a game through the
existing version-guarded API recalculates statistics.

## Get advice and read results

The **Analytics** tab on desktop, or **Game options** on mobile,
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
Selected reviews use the same board, player summary, and desktop/mobile tabbed
layout as live play. Replay navigation lives in Moves and changes only the viewer's
position. Back to Hall preserves filters and the selected record.
Equivalent AI configurations still group across seeds in the existing statistics API.
[Records and personalization](PERSONALIZATION.md) explains identities, model
suggestions, themes and local preferences. [Tournaments](TOURNAMENTS.md) describes
a separate saved comparison collection; its matches do not enter ordinary-game
statistics.
