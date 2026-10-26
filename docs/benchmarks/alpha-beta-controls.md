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
