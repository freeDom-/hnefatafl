# Play, replay and keep games

[Documentation index](README.md) · [Quick start](../README.md#quick-start)

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

## Create and control a game

1. Open **New game** and select a battlefield and mode: pass-and-play Human vs
   Human, Human vs AI or AI vs AI.
2. Choose sides, existing/new human profiles and computer opponents. Models must
   match the battlefield. Named presets have fixed budgets; Custom exposes search
   settings. See [AI profiles and wrappers](AI.md).
3. Select a piece and a highlighted destination. Mouse and touch use the same
   authoritative server move validation. Arrow keys navigate; Enter/Space select;
   Escape clears selection.
4. Follow the active side, captured pieces, move history and game-over result.
   AI vs AI supports pause, playback speed and automatic legal moves.

The complete last successful setup is saved in the current browser. Cancelled or
failed creation does not replace it. Games live on the server; browser preferences
hold the current selection, language and appearance. Clearing browser storage
is not deletion of saved games. A connection to the server is required.

## Replay, undo and restart

**Hall of records** reopens saved games. Seek, undo and redo inspect retained
positions without changing the recorded final outcome or discarding future moves.
Making a different legal continuation branches history. Completed-game controls
disable further play/restart even when viewing an earlier position. **Restart** is
for unfinished records: it creates a new game with a fresh seed and preserves the
old record. Use New game for a new match after completion.

Moves include expected versions; a stale concurrent request is rejected rather
than overwriting another move. Replay uses saved moves, so completed histories
remain viewable after deleting source models. Removing a game after confirmation
permanently removes its moves/result and recalculates statistics. Profile deletion
hides a local player from future selection but preserves historical identity.

## Get advice and read results

**Analytics** below move history selects an optional background advisor and controls
**Show winning chances**. The advisor suggests a legal move without executing it;
AI player opinions can disagree and remain saved in replay. These displayed values
are uncalibrated expected-score assessments, not measured win probabilities.
See [Analysis](ANALYSIS.md) for sources and completed-search diagnostics.

The Hall of records offers global battlefield/mode/player-type filters, sortable
statistics, independent player/history name searches and expandable AI parameters.
Equivalent AI configurations group across seeds while individual games keep their
seeds. [Records and personalization](PERSONALIZATION.md) explains deletion, model
suggestions, themes and local preferences. [Tournaments](TOURNAMENTS.md) describes
a separate saved comparison collection; its matches do not enter ordinary-game
statistics.
