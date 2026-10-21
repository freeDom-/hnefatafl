# Copenhagen Hnefatafl

## Background and sources

Copenhagen is a modern 11 × 11 ruleset formulated in 2012 by Aage Nielsen,
Adam Bartley, and Tim Millar to address drawish play in earlier corner-escape
variants. It adds shieldwall captures, attacker encirclement victory, and
defender exit-fort victory.

The requested primary reference is
[Aage Nielsen's Copenhagen rules](https://aagenielsen.dk/copenhagen_rules.php).
That endpoint returned HTTP 455 during implementation research on 2026-10-21.
Its modern text and examples were cross-checked through
[the illustrated Copenhagen transcription](https://hnefatafl.org/rules.html)
and [Cyningstan's Copenhagen description](
http://tafl.cyningstan.com/page/768/copenhagen-hnefatafl-rules).

**Source distinctions matter:** the illustrated transcription explicitly
marks its own added move limits, corner-blockade adjudication, and replacement
of perpetual repetition. Those additions are excluded here. Its quoted
original rule, perpetual repetition loses for defenders, is used. The older
Cyningstan page permits shieldwall completion only by a flank; this profile
follows the modern illustrated core, allowing the last inward backing piece
to complete a shieldwall too. These interpretations are covered by tests.

This profile's engine identifier is `copenhagen`, rules version `1`. Direct
verification against the inaccessible primary endpoint remains a source
verification limitation; the mirrors and their divergent rules are identified
here rather than represented as identical primary sources.

## Board and initial positions

There are 24 attackers, 12 defenders, and a king on an 11 × 11 board. `A` is an
attacker, `D` a defender, `K` the king, and `.` empty.

```text
...AAAAA...
.....A.....
...........
.....D.....
....DDD....
AA.DDKDD.AA
....DDD....
.....D.....
...........
.....A.....
...AAAAA...
```

**Attackers move first** and turns alternate. The defenders control the king.

## Movement and restricted squares

Move one own piece any positive number of vacant squares along a row or
column, without jumping, turning, or landing on a piece. The king uses the
same movement.

Only the king may stop on the center (the throne) or four corners. Everyone
may pass through the empty throne. The king may reenter it.

The corners are hostile capture supports. The throne is hostile to attackers
at all times, and to defenders only while empty. Thus a defender beside the
king on his throne cannot be captured against the occupied throne. The king
can participate in capturing an attacker against that same square. The board
edge is **not hostile**.

## Ordinary and royal capture

An ordinary enemy is captured when the moving player closes a sandwich
around it, orthogonally between two own pieces or between an own piece and a
hostile square. One move can capture several men. The king may capture.
Moving between enemies is safe; a capture requires their closing move.

The king is captured only when attackers occupy **all four neighboring
squares**. Next to the throne, three attackers plus the empty throne suffice.
The closing move must be an attacker's move. The king cannot be captured on
the board edge. Two opposite attackers are insufficient away from the edge
too. The king is removed and the attackers win immediately on royal capture.

## Shieldwall capture

A contiguous row of **two or more** enemy pieces along an edge is captured
when:

1. The row is bracketed at both ends by the capturing side's pieces (an empty
   corner may replace one bracket).
2. Every enemy in the row has an own piece immediately inward from it.
3. The current move completes a bracket or one of those inward supports.

The whole row is evaluated simultaneously with ordinary captures. A single
edge man does not qualify as a shieldwall. A missing inward support leaves
the wall uncompleted.

The king can be a bracket or inward supporting piece. He also counts when
standing within a threatened defender row, **but the king survives a
shieldwall** while its ordinary defenders are removed. These captures work
on all four edges for either side.

## Wins, encirclement, and exit forts

- Defenders win as soon as the king reaches a corner.
- Attackers win by capturing the king.
- Attackers also win when **all remaining defenders including the king** are
  enclosed away from every board edge by an unbroken attacker barrier.
  A defender outside the ring prevents this victory. The engine checks this
  by flooding non-attacker cells inward from every edge.
- Defenders also win through an **exit fort**: the king touches an edge, has
  room to move, and a defender wall permanently prevents attacker entry.

Exit forts are recognized structurally. Starting from every attacker, the
engine computes the largest reachable exterior. It repeatedly removes wall
men that could be captured if attackers occupied every accessible exterior
square, using both ordinary and shieldwall capture conditions. If even this
maximal attack cannot reach the king's mobile pocket, the wall is unbreakable.
This includes both standard illustrated fort shapes. An opening, a capturable
wall, a missing inward support, or an immobile king prevents a fort claim.
This intentionally uses a proof of an impregnable wall rather than relying
on a shallow opponent search. A complicated position requiring an active
defender maneuver to become a fort is not already a completed exit fort.

## Repetition and immobilization

Perpetual repetition loses for defenders. This application uses the **third
occurrence of the same board and player to move** as the finite threshold,
documenting that threshold as a digital convention. It does not forbid a
defender's first repeated move.

A player with no legal move loses. There is no automatic 140-move loss and
no extra corner-blockade win: those rules belong to the transcription's
modifications, not this selected profile. Training or tournaments can impose
their own clearly reported game-length budgets.

## Tests and differences

Rules tests cover the exact initial material, restricted squares, occupied and
empty throne hostility, two-sided royal non-capture, four-sided king capture,
three-plus-throne capture, edge immunity, shieldwalls completed by bracket or
support, king shieldwall immunity, incomplete shieldwalls, corner captures,
all-defender encirclement, both illustrated exit forts, open and immobile
non-forts, repetition, no-move loss, serialization, and replay.

Brandubh uses a weak king and a non-hostile center, with no special wall or
fort mechanisms. The selected Tablut market profile starts defenders, permits
all squares, uses ordinary royal capture, and allows any-edge escape.
