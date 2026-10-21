# Brandubh — Cyningstan leaflet rules

## Background and source

Brandub (also spelled Brandubh) is the small Irish member of the tafl family.
Irish literary references describe a chief with four guards against eight
attackers; archaeological finds include 7 × 7 boards such as Ballinderry.
The medieval rules did not survive as a complete modern rulebook. This profile
follows Damian Walker's reconstruction in **Traditional Board Game Series,
leaflet 24, second edition (2011)**:
[Brandub leaflet](http://www.cyningstan.com/data-download/395/brandub-leaflet).

The exact linked PDF and its diagram were read on 2026-10-21. Its thirteen
numbered rules govern this profile, engine identifier `brandubh`, rules
version `1`. The supplied HTTP source was accessible; its HTTPS endpoint
returned 503 during research. No downloaded source is executed or installed.
The rules below paraphrase the source; the original leaflet remains
copyright Damian Walker.

## Board and initial position

The board is 7 × 7, with center and four corners marked. `A` is an attacker,
`D` a defender, `K` the king, and `.` an empty square.

```text
...A...
...A...
...D...
AADKDAA
...D...
...A...
...A...
```

Eight attackers oppose four defenders and their king. **Attackers move first**;
turns then alternate. The king belongs to the defenders.

## Movement

Move one own piece any positive clear distance along a row or column. No
diagonal movement, jumping, or landing on another piece is permitted.

Only the king may **stop** on the center or any corner. Other pieces may pass
through an empty center. The king may return to the center after leaving it.

## Captures

Close a sandwich around an enemy on two opposite orthogonal sides to remove
it. The king may take part in captures and is captured by an ordinary
two-attacker sandwich, including when standing on the center. A move may
capture several pieces in different directions.

An empty corner may substitute for one of the capturing pieces. For example,
if an enemy stands one square from a corner, moving an own piece onto the
other side captures it against that corner. **The center does not have this
effect**, a distinction explicitly stated in rule 10 of the leaflet.

Moving into a sandwich is safe: the enemy must close the trap on their own
turn. The board edge is not hostile and cannot substitute for a piece.
There are no shieldwall captures, encirclement wins, or exit-fort wins.

## Victory and draw

- Defenders win when the king reaches one of the **four corners**.
- Attackers win when they capture the king.
- A player with no legal move causes a **draw**, as allowed by leaflet rule 13.
- The source permits a draw when play becomes repetitious. The application
  makes this deterministic at the third occurrence of the same board and
  player to move. This numerical threshold is a documented digital convention.
- A friendly game may also be ended by agreement outside move processing.

Reaching an ordinary edge square is not a victory. Capturing the last attacker
does not bypass rule 13: the next player then has no legal move, causing a draw.
There is no rules-engine turn cap; separate tournament or training budgets
are reported as such.

## Distinctions and examples

Unlike Copenhagen, the king is weak everywhere, the center is never hostile,
and repetition does not award the attackers a win. Unlike the selected Tablut
market profile, Brandubh starts with attackers, has restricted squares, and
requires corner escape.

Tests include the non-hostile center, center pass-through and royal reentry,
hostile corners, royal sandwich capture on the center, edge non-victory,
corner victory, multiple captures, repetition, no-move draw, and deterministic
serialization and replay.
