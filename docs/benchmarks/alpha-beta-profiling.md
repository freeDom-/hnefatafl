# Alpha-beta profiling and comparison (2026-10-26)

[Documentation index](../README.md) · [Performance](../PERFORMANCE.md#phase-4-alpha-beta-symmetry-search)

This report covers the follow-up investigation of the alpha-beta search
bottlenecks reported after the control-refinement work: transposition-table
reuse, move ordering, D4 symmetry overhead and iterative deepening. It records
what reproduced, what did not, and the correctness fixes that came out of it.

Everything here was produced by the production search path (`choose_move` with
the `alpha_beta` algorithm) and the extended benchmark harness. Host: Linux
(Proxmox kernel), Python 3.13.5, CPU only, two math threads, single process.
Absolute wall times are host-specific; compare ratios and completed depths.

## Correctness

The fixed-depth pass reported **zero minimax mismatches** against the legacy
exact-value reference for every scenario, and the independent exhaustive oracle
found an optimal legal move for **37/37** feasible positions. The same held for
the dedicated ordering comparison. The new tests add:

- transposition-bound classification against the window actually searched;
- prepopulated lower/upper/exact entries and forced window tightening;
- compact repetition-key order independence and count sensitivity;
- move-dependent Shield maiden D4 equivariance, including tactical positions;
- repetition-history minimax equivalence and root-window pruning equivalence.

## Finding 1: the root searched every child with a full window

`_alpha_beta` and `_alpha_beta_legacy` both passed `(-inf, +inf)` to every root
child. The root was therefore a pure minimax fan-out at the widest part of the
tree, and move ordering could not prune anything there. The optimized search now
narrows the root window with the best value found so far; the first move to reach
the best value wins, which stays deterministic.

Isolated A/B on eleven representative positions (brandubh depth 3, tablut and
copenhagen depth 2, three repetitions, identical seed and evaluator):

| Variant | Total interior nodes | Total leaf evaluations | Total wall seconds |
| --- | ---: | ---: | ---: |
| Full window (historical) | 4,602 | 40,370 | 37.86 |
| Narrowed root window | 1,045 | 9,022 | 15.29 |

The values were identical position by position. Root-window pruning is the
largest measured win in this investigation (~4.4× fewer interior nodes, ~4.5×
fewer leaf evaluations, ~2.5× total wall time on this subset). It is always on
for the optimized search and is not a user-facing switch.

## Finding 2: the transposition table has no usable hits

The old key included the full ordered history. The new ordinary key is
`(ruleset, board, turn, repetition signature)`, where the signature is the
order-independent occurrence counts of board/turn keys. This is equivalent for
every future outcome because the engine's threefold rule and the Shield maiden
repetition penalty depend only on counts. Adversarial tests confirm that
different counts keep distinct keys.

The compact key does find entries: on `brandubh/opening`, 40 of 147 probes hit
an existing entry. **But every hit was depth-incompatible** — zero exact hits,
zero bound hits, zero table cutoffs. The reason is structural: across iterative
deepening the same node is re-searched at a *greater* remaining depth, so the
entry stored by the previous iteration is always too shallow. Within a single
iteration, move-order transpositions are absent in the tested corpus
(`transposition_board_history_divergence` was 0). The table is therefore pure
overhead in these positions. It remains available (and is required for
canonical transpositions), but it is not the source of any speedup.

| Scenario (fixed depth) | Median wall s | Median interior nodes | Median successors | Exact hits | Bound hits | Table cutoffs |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| legacy | 0.346 | – | – | – | – | – |
| transposition | 0.259 | 63 | 2,169 | 0 | 0 | 0 |
| ordering | 0.247 | 62 | 1,552 | 0 | 0 | 0 |
| symmetry_transposition | 0.201 | 62 | 1,179 | 0 | 0 | 0 |
| symmetry_pruning | 0.201 | 49 | 1,179 | 0 | 0 | 0 |
| all | 0.206 | 49 | 1,179 | 0 | 0 | 0 |

The `transposition` and `ordering` rows differ only by move ordering, and the
difference is noise. `symmetry_transposition` and `symmetry_pruning` reduce
successors (canonical transpositions and orbit collapsing), which is where their
modest wall-time edge comes from. Fixed-time completed depth was a median of 2
for **every** scenario, so none of the features bought extra depth under a 0.2 s
budget.

## Finding 3: move ordering is not the bottleneck

Seven ordering strategies were compared on the full corpus with identical
tables, depths and seeds:

| Strategy | Median wall s | Median interior nodes | Median successors | Median sort s |
| --- | ---: | ---: | ---: | ---: |
| full (production) | 0.312 | 62 | 1,552 | 0.0013 |
| static | 0.314 | 63 | 2,169 | 0.0001 |
| PV only | 0.329 | 62 | 1,552 | 0.0009 |
| TT move only | 0.318 | 63 | 2,180 | 0.0013 |
| killer only | 0.310 | 63 | 1,767 | 0.0014 |
| history only | 0.316 | 63 | 1,954 | 0.0015 |
| depth-gated | 0.309 | 62 | 1,552 | 0.0012 |

All strategies completed the same depth and their wall times are within run-to-run
noise (~5%). The principal variation is the only priority that measurably reduces
successors (1,552 versus 2,169 for static); the transposition move contributes
nothing because the table has no usable hits, and killer/history are not
distinguishable from noise. The sort itself is negligible (≤1.5 ms). The
production `full` ordering is kept because PV is a real, if modest, reduction and
the added cost is below measurement noise; the benchmark-only strategies are
retained so the comparison stays reproducible.

## Finding 4: eager successor construction dominates

The per-node timing breakdown shows where the wall time actually goes. For the
`ordering` scenario the median `successor_seconds` was ~0.23 s of a ~0.25 s
search; `sort_seconds` was ~0.001 s and `transposition_seconds` ~0.0003 s.
`_ordered` builds and scores every legal child *before* the move loop can cut
off, so the cost tracks the branching factor of visited nodes, not the number of
leaves. This is shared with the legacy search and is the main remaining
optimization opportunity (lazy child generation); it is recorded here rather than
implemented because a safe refactor is larger than this change set.

## Iterative deepening: interrupted work is small

The fixed-time pass records the discarded work of the interrupted iteration.
Across the six scenarios the median `discarded_nodes` was 30–34 nodes (maximum
164), a small fraction of a completed pass. The per-iteration timings are exposed
as `iteration_timings`, `interrupted_depth`, `interrupted_seconds` and
`discarded_nodes`, and `chosen_from_completed_iteration` records that the returned
move always came from a completed iteration. A conservative early-stop policy was
considered but not enabled: the discarded work is already small, and predicting
whether the next depth will finish risks stopping a search that would have
completed usefully. The instrumentation is in place to revisit that decision with
a larger budget.

## Finding 5: D4 symmetry is correct but situational

The move-dependent Shield maiden score is D4-equivariant for every supported
ruleset, including asymmetric histories and the Copenhagen shieldwall,
encirclement and fort surfaces (new tests). Canonical transpositions and orbit
pruning are therefore value-safe. Their measured effect is limited to genuinely
symmetric positions: median `symmetry_moves_skipped` is 0 because most midgame
positions have a trivial stabilizer, and the fixed-time completed depth is
unchanged. Canonicalization and stabilizer time together were ~4 ms per search
median. Symmetry features stay optional and off by default.

## Recommended defaults

- Keep the optimized search as the production Jarl path: with root-window
  pruning it is faster than legacy at equal depth and returns identical values.
- Keep the ordinary transposition table on for compatibility, but document that
  it has no measured benefit under iterative deepening; it is required for the
  optional canonical-transposition feature.
- Keep the `full` move ordering: the principal-variation priority is a small
  measured reduction in successors and the extra cost is negligible.
- Keep D4 canonical transpositions and symmetry pruning optional and off by
  default. They are safe, but they help only symmetric positions and do not
  change the completed depth under a fixed budget.
- Preserve the legacy search as an available reference. It lacks root-window
  pruning, so it is the slower baseline, but it is the historical implementation
  and remains useful for controlled comparisons.

## Reproduction

```sh
# Feature ablation (legacy, table, ordering, symmetry, all)
.venv/bin/python -m hnefatafl.cli benchmark-alpha-beta \
  --config configs/benchmarks/alpha-beta-symmetry.json \
  --output work/alpha-beta-profiling.json

# Move-ordering strategies
.venv/bin/python -m hnefatafl.cli benchmark-alpha-beta \
  --config configs/benchmarks/alpha-beta-ordering.json \
  --output work/alpha-beta-ordering.json
```

Both configurations record the corpus hash, source hashes and host details in
the report. The benchmark's `scenarios` key selects a subset; the per-node timing
fields are populated because the benchmark enables the internal `_profile` hook.
Trimmed summaries (config, host, source hashes, fixed-depth, fixed-time and oracle
aggregates) are kept as
[profiling summary](alpha-beta-profiling-2026-10-26.json) and
[ordering summary](alpha-beta-ordering-2026-10-26.json).
