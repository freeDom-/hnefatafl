# Alpha-beta control refinements evidence

[Documentation index](../README.md) · [Performance](../PERFORMANCE.md#move-ordering-and-symmetry-pruning-refinements)

This report covers the follow-up refinements to the Phase 4 alpha-beta search:
history-aware move ordering without a preferred move, precise cross-orientation
transposition statistics and move-orbit pruning before successor construction.

Everything here was produced by the same command as the Phase 4 evidence:

```sh
.venv/bin/python -m hnefatafl.cli benchmark-alpha-beta \
  --config configs/benchmarks/alpha-beta-symmetry.json \
  --output work/alpha-beta-controls.json
```

Host: WSL2, two math threads, CPU. Configuration: brandubh depth 3, tablut depth
2, copenhagen depth 2, two repetitions, 200,000-entry tables, oracle depth 2.

## Correctness

The fixed-depth pass reported zero minimax mismatches against the legacy
exact-value reference for all six ablations, and the independent exhaustive oracle
found an optimal legal move for all 37 feasible positions. The before/after
comparison against the pre-refinement code reproduced both results exactly.

## Fixed-depth median wall seconds by ruleset

| Ruleset (depth) | legacy | transposition | ordering | symmetry_pruning | all |
| --- | ---: | ---: | ---: | ---: | ---: |
| Brandubh (3) | 1.448 s | 1.496 s | 1.507 s | 0.351 s | 0.377 s |
| Tablut (2) | 0.082 s | 0.077 s | 0.076 s | 0.047 s | 0.050 s |
| Copenhagen (2) | 0.463 s | 0.478 s | 0.486 s | 0.321 s | 0.319 s |

The same-host before/after run moved these medians by less than 7% in either
direction, so the structural pruning change is reported as correctness and
work-avoidance rather than a wall-clock claim.

## Cross-orientation reuse

| Scenario | Positions with reuse | Sum of medians | Maximum |
| --- | ---: | ---: | ---: |
| `symmetry_transposition` | 9 / 37 | 326 | 101 |
| `all` (pruning + canonical table) | 0 / 37 | 0 | 0 |

With canonical transpositions alone, genuine cross-orientation reuse is common.
With move pruning enabled, each orbit is collapsed before a differently oriented
probe can occur, so the counter correctly reports no cross-orientation reuse while
`symmetry_hits` still records same-orientation canonical-table hits.

## Isolated history ordering

Passing an empty history dictionary to the same priority function (otherwise
identical configuration, depth 3–4) produced mixed per-position node counts: two
positions improved and six worsened, with no change to completed depth or value.
The ordering refinement guarantees that history is always consulted in the
documented priority order (PV, table, killers, history, stable static order) and
that sorting is skipped when every history score is zero; the unit tests assert
that priority directly.

## Equal-time strength comparison (legacy vs full symmetry)

The shipped comparison configuration was played as a balanced series with both
opponents on equal 2-second budgets:

```sh
.venv/bin/python scripts/alpha_beta_strength.py \
  --config configs/alpha-beta-search-comparison.json \
  --output work/alpha-beta-strength.json
```

Configuration: brandubh, 20 games, seed 20261026, `max_plies` 500,
`time_limit` 2 s, depth cap 3. Player 0 is legacy search; player 1 enables the
transposition table, improved ordering, D4-canonical transpositions and symmetry
move pruning.

| Opponent | W | D | L | Score | 95% interval | Mean depth |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Legacy | 7 | 3 | 10 | 0.425 | [0.220, 0.630] | 2.46 |
| Full symmetry | 10 | 3 | 7 | 0.575 | [0.370, 0.780] | 2.29 |

All 20 games finished (king captured, king escaped or threefold repetition); none
hit the ply limit. The full-symmetry point estimate is higher (0.575 versus
0.425), but the 95% interval for the legacy score comfortably contains 0.5, so
**strength did not measurably improve**. The sample is too small to claim a
strength difference, and full symmetry reached a slightly *shallower* mean
completed depth (2.29 versus 2.46), consistent with canonicalization overhead
consuming part of the fixed budget rather than buying extra depth. Speed alone is
not strength, so no strength claim is made.

## Table memory bound

`table_capacity` is validated to `1–500000` (reduced from 5,000,000). A search
owns one table for its lifetime, but a repetition-aware key grows with the game
history, so the ceiling bounds worst-case memory. Measured with `tracemalloc` on
this host:

| History entries | Bytes per stored entry | 500,000-entry peak |
| --- | ---: | ---: |
| 0 | 457 | 228 MB |
| 50 | 857 | 428 MB |
| 200 | 2,057 | 1,028 MB |

The table is never shared and is discarded when the move ends. On a long game the
search stores very few entries because the fixed time budget and canonicalization
cost dominate, so the actual peak is far below the synthetic full-cap figures:

| Position | `time_limit` | Peak entries | Peak memory |
| --- | ---: | ---: | ---: |
| Brandubh, 200-ply history | 5 s | 125 | 6.3 MB |
| Tablut, 300-ply history | 5 s | 7 | 5.7 MB |
| Copenhagen, 400-ply history | 5 s | 15 | 4.0 MB |
| Brandubh opening | 10 s | 525 | 1.4 MB |

The bound is therefore a safety valve, not a limit the search reaches in practice;
the measured long-game peak stays under 7 MB.

## Deadline inside canonicalization and symmetry pruning

Move ordering, `canonical_state_key`, `state_stabilizer` and the move-orbit loop
all receive a cooperative check callback that raises the search's deadline
exception. A long-history canonicalization or stabilizer therefore aborts when the
budget expires instead of running to completion. On a synthetic symmetric
copenhagen position with a 400-entry history (one full stabilizer ≈ 45 ms, one
canonicalization ≈ 45 ms), a 1 ms budget now returns in ≈ 1.2 ms and a 20 ms
budget in ≈ 20 ms, instead of overrunning by the whole stabilizer. The unit test
`test_tiny_deadline_interrupts_long_history_canonicalization` asserts the search
returns before the uninterrupted stabilizer baseline, and
`test_symmetry_functions_invoke_the_cancellation_check` asserts the callback is
invoked inside each symmetry function.

## Explicit default search identity

A record saved with an explicit nested `search` object that repeats the defaults
resolves to the same behaviour as the same algorithm without one, but is
intentionally a distinct effective identity. This keeps historical records stable:
a game saved before the object existed is never silently merged with a newer
explicit-default record, and their runes and statistics stay separate. The trade-off
is that two behaviourally equivalent configurations can appear as two identities
in the Hall of Records. `tests/test_identities.py` and `tests/test_api.py` assert
the distinct and stable variant IDs and runes; the policy is documented in
[Persistent identity runes](../IDENTITIES.md) and [AI opponents](../AI.md).
