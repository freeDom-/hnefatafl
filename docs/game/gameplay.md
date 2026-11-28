# Gameplay and rulesets

[Game index](README.md) · [Arena interface](interface/arena.md) · [Online play](online-play.md) · [Ruleset sources](../rules/brandubh.md)

## Modes

| Mode | Opponent | Rated | Record |
| --- | --- | --- | --- |
| Play vs AI | Fixed/custom agent | No | Singleplayer |
| Local 1v1 | Two humans on one device | No | Local 1v1 |
| Normal online game | Verified account | Yes | Online + Elo |
| League game | Queued online opponent | Yes | League + online |
| Player tournament | Registered human | No | Tournament + online |

A server connection is required. Browser preferences remember setup choices, but
games live on the server. Local 1v1 belongs to the creating account and is excluded
from online/Elo/AI statistics.

## Battlefields

All pieces move like rooks through vacant orthogonal squares. A capture closes a
sandwich on your move; moving between enemies is safe. The king belongs to the
defenders.

| Ruleset | Board / first side | King escape | Distinctive rules |
| --- | --- | --- | --- |
| [Brandubh](../rules/brandubh.md) | 7×7; attackers | Corner | Two-attacker royal sandwich even on the centre; empty centre passable/not hostile |
| [Tablut market](../rules/tablut.md) | 9×9; defenders | Any edge | No special squares; not interchangeable with historical/tournament Tablut |
| [Copenhagen](../rules/copenhagen.md) | 11×11; attackers | Corner | Four-sided royal capture, edge immunity, shieldwalls, encirclement, exit forts; defender loss on repetition |

Brandubh/Tablut use a no-move draw; Copenhagen uses a no-move loss. A
training/tournament ply cap produces an **unfinished** game, never a rules draw.
Ordinary games have no experimental move cap. Source interpretations and the finite
threefold repetition convention are in the linked rules pages.

## Opponents

Normal setup offers **Shield bearer** (baseline tactical heuristic), **Shield
maiden** (rules-aware tactical heuristic), **Jarl** (alpha-beta) and **Seer** (MCTS).
The dialog describes style/budget, not a measured strength ranking. Administrators
can configure a custom opponent or AI vs AI. Compatible trained models are available
through advisor/training/AI testing flows. Search fields and bounds are canonical in
[agent search parameters](../training/parameters.md#agent-search-settings); measured
comparisons are in [agent evaluation](../evaluation/agents.md).

## Moves, turns and termination

Select a piece, then a highlighted legal destination. The engine validates every
move; stale/illegal moves are rejected. AI turns run automatically. A completed game
is read-only; new games use a fresh seed. Online games additionally have server-owned
clocks and result finalization; see [online play](online-play.md).

## Replay and analysis concepts

Completed records can be replayed position by position without changing the saved
game. Single-player and completed records may show saved agent estimates or a
background advisor; live online games do not. Displayed winning chances are
uncalibrated expected scores, not measured probabilities. Page controls are in the
[Arena interface](interface/arena.md); analysis semantics are in
[replay and analysis](replay-and-analysis.md).
