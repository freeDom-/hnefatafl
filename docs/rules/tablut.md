# Tablut — Aage Nielsen's market leaflet

This implementation deliberately follows the **six-rule market leaflet linked
in the project brief**. It is a simplified game, and differs from historical
Saami Tablut and tournament variants. In particular, defenders move first,
the king is captured by two attackers, and the throne has no special effect.

## Background and source

Tablut belongs to the asymmetric tafl family. Carl Linnaeus recorded a Saami
game in 1732; modern reconstructions disagree on several royal and throne
rules. Those reconstructions must not silently replace this project's chosen
source: [Aage Nielsen's English market rules (PDF)](
https://aagenielsen.dk/tablutmarketregler_engelsk.pdf).
The linked one-page PDF and its initial-position diagram were read during
implementation on 2026-10-21. The diagram and six numbered rules are the
authority for this profile, whose engine identifier is `tablut`, rules version
`1`. A different historical rules profile can be added separately later.

## Board and starting position

The board is 9 × 9. `A` denotes an attacker, `D` a defender, `K` the king, and
`.` an empty square. There are 16 attackers, 8 defenders, and one king.

```text
...AAA...
....A....
....D....
A...D...A
AADDKDDAA
A...D...A
....D....
....A....
...AAA...
```

**White/the defenders move first**, then turns alternate. Each player moves
one of their own pieces. The king belongs to the defenders.

## Movement and captures

Every piece, including the king, moves any positive number of empty squares
in a straight horizontal or vertical line, like a chess rook. Pieces may not
jump, move diagonally, or occupy another piece's square.

An enemy piece is captured when the opponent's move closes a horizontal or
vertical sandwich around it. For example, `A D . A` becomes `A . A .` if the
rightmost attacker moves one square left. Multiple sandwiches closed by the
same move are all captured. The captured pieces are removed immediately.

The king participates in captures and is himself captured by a normal
two-attacker sandwich **even on or next to the center**. A piece may move
between two enemy pieces safely: captures require an opponent's closing move.

All board squares are ordinary. Soldiers may stop on or pass through the
center and corners. Neither the center, corners, nor board edge assists a
capture. This is what the linked market leaflet specifies by giving every
piece the same movement and capture rules without special-square exceptions.

## Winning and digital adjudication

- Defenders win as soon as the king reaches **any edge square**.
- Attackers win as soon as they capture the king.
- The leaflet does not define repetition or immobilization. To terminate
  otherwise indefinite digital games reproducibly, this profile declares a
  draw on the **third occurrence of the same board and player to move**, or
  when the next player has no legal move. These are explicit application
  conventions, not claims about the six-rule leaflet.

There is no built-in move cap in the rules engine. Tournament and training
budgets may adjudicate a separate draw when their configured limit is reached.

## Differences from the other profiles

Brandubh is 7 × 7, starts with attackers, restricts the center and corners to
the king, and requires corner escape. Copenhagen has a strong king, shieldwall
captures, encirclement, exit forts, and defender loss on repetition. None of
those mechanisms applies to this Tablut profile.

The corresponding tests cover the starting side, ordinary center occupation,
non-hostile throne, two-sided royal capture, edge escape, all movement and
capture rules, repetition, immobilization, serialization, and replay.
