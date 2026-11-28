# Alpha-beta search benchmarks

Consolidated evidence for the alpha-beta search implementation, symmetry options, move ordering and root-window pruning. Each section preserves the original measurement scope, hardware and revision; see the linked raw reports before comparing results.

<!-- Consolidated from alpha-beta-symmetry.md -->

## Phase 4 evidence: alpha-beta symmetry search

[Documentation index](../../../README.md) · [Performance](../../training/../evaluation/benchmarks/search-alpha-beta.md#phase-4-evidence-alpha-beta-symmetry-search)

Everything here was produced by `python -m hnefatafl.cli benchmark-alpha-beta`
(`hnefatafl/alpha_beta_benchmark.py`) from the checked-in configuration. Raw JSON
is kept beside this file. Reproduce with:

```sh
.venv/bin/python -m hnefatafl.cli benchmark-alpha-beta \
  --config configs/benchmarks/alpha-beta-symmetry.json \
  --output work/alpha-beta-symmetry.json
```

| File | Content |
| --- | --- |
| `configs/benchmarks/alpha-beta-symmetry.json` | Runnable fixed-depth, fixed-time and oracle budget |
| `docs/evaluation/benchmarks/raw/search/alpha-beta-symmetry-cpu-2026-01-01.json` | Raw CPU report (all scenarios, counters and raw samples) |

### Protocol

The benchmark drives the real production `choose_move` path. It compares six
feature ablations of the same search code:

| Scenario | Features |
| --- | --- |
| `legacy` | Original exact-value cache search (reference) |
| `transposition` | Ordinary bounded table, no improved ordering |
| `ordering` | Ordinary bounded table plus PV/table/killer/history ordering |
| `symmetry_transposition` | D4-canonical repetition-safe table keys |
| `symmetry_pruning` | Full-state stabilizer move-orbit pruning |
| `all` | Canonical table plus symmetry pruning plus ordering |

The deterministic corpus covers all three rulesets: openings, seeded random
midgames, genuine repetition histories, immediate royal escapes, king-capture
threats, restricted-square tactics and Copenhagen shieldwall, encirclement and
edge-fort positions. It is filtered to nonterminal positions with legal moves and
hashed into the report.

Fixed-depth scenarios use a 60-second ceiling so every requested depth completes.
All scenarios for a position must report the same defender-perspective minimax
value as the legacy reference; the report lists any mismatch. A separate
independent exhaustive minimax (real engine and evaluator only) validates the
chosen move for positions whose search tree fits the configured node budget.
Fixed-time scenarios use an equal wall-clock budget and report last completed
depth, decision latency, visited nodes and fallbacks. Repetitions alternate
scenario order and report median/min/max. One memory-traced run per scenario
(`tracemalloc`) is measured outside the timed repetitions, so timing is not
distorted; it is a Python-allocation peak, not resident set size.

### Environment

- WSL2 Linux x86-64, Intel Core i7-8750H, 12 logical CPUs, two math threads.
- Python 3.12.15, CPU-only, no GPU execution.
- The benchmark ran alone; no training job or browser suite competed for CPU.
- `canonicalization_seconds` is accumulated inside the search and included in the
  wall-clock comparison, so it is not hidden from the reported cost.

### Results

See the raw report for per-position samples. The tables below summarise medians
across the corpus; ratios compare medians and are not a universal speed estimate.

#### Correctness

All six scenarios reported the same defender-perspective minimax value as the
legacy reference on every fixed-depth position (`minimax_mismatches: []`). The
independent exhaustive oracle validated 37 feasible positions: every scenario
returned an optimal, legal move (`positions: 37`, `value_matches: 37`).

#### Fixed depth (medians across positions)

`nodes` counts visited interior nodes; `evals` counts leaf/terminal evaluations.
`legacy` does not publish node counters. Wall time is the whole search including
ordering and canonicalization.

| Ruleset | Scenario | Nodes | Evals | Wall (s) | Canonicalize (s) | Symmetry skips |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Brandubh | legacy | — | — | 2.507 | — | 0 |
| Brandubh | transposition | 791 | 2151 | 2.472 | 0.000 | 0 |
| Brandubh | ordering | 791 | 2433 | 2.505 | 0.000 | 0 |
| Brandubh | symmetry_transposition | 323 | 1177 | 0.780 | 0.042 | 0 |
| Brandubh | symmetry_pruning | 323 | 1195 | 0.603 | 0.000 | 180 |
| Brandubh | all | 323 | 1195 | 0.608 | 0.035 | 180 |
| Tablut | legacy | — | — | 0.179 | — | 0 |
| Tablut | transposition | 30 | 862 | 0.150 | 0.000 | 0 |
| Tablut | ordering | 30 | 862 | 0.196 | 0.000 | 0 |
| Tablut | symmetry_transposition | 30 | 497 | 0.126 | 0.007 | 0 |
| Tablut | symmetry_pruning | 22 | 489 | 0.125 | 0.000 | 146 |
| Tablut | all | 22 | 489 | 0.115 | 0.004 | 146 |
| Copenhagen | legacy | — | — | 0.581 | — | 0 |
| Copenhagen | transposition | 62 | 1592 | 0.628 | 0.000 | 0 |
| Copenhagen | ordering | 62 | 1592 | 0.568 | 0.000 | 0 |
| Copenhagen | symmetry_transposition | 62 | 994 | 0.462 | 0.007 | 0 |
| Copenhagen | symmetry_pruning | 40 | 864 | 0.484 | 0.000 | 291 |
| Copenhagen | all | 40 | 864 | 0.428 | 0.006 | 291 |

The ordinary transposition table is roughly neutral against the legacy cache
(median wall ratio 0.96–1.03 by ruleset) and reduces evaluations where
subtrees transpose. The symmetry scenarios are much faster on the corpus because
symmetric positions dominate the total node count.

#### Where symmetry helps and where it costs

Grouping by position shows the tradeoff directly. Symmetry canonicalization and
orbit pruning are pure wins on symmetric positions and pure overhead on generic
midgames:

| Position class | `all` / `transposition` wall | Symmetry skips |
| --- | ---: | ---: |
| Openings (all rulesets) | 0.12–0.21× | 98–231 |
| Immediate escape / king threat / attacker turn | 0.41–0.65× | 16–40 |
| Seeded asymmetric midgames | 0.91–1.25× | 0 |
| Repetition-sensitive histories | 0.82–1.78× | 0 |
| Copenhagen shieldwall / encirclement / fort | 0.78–1.04× | 0 |

This is why the symmetry features remain optional defaults: enabling them
globally would trade a large opening gain for a 7–25% regression on ordinary
midgame searches. The ordinary table and move ordering are the safe default.

#### Fixed time

At the configured 0.2-second budget every scenario reached the same median
completed depth (Brandubh 3, Tablut 2, Copenhagen 2), so this budget does not
distinguish strength. A separate 1.0-second probe at Jarl's depth-5 ceiling shows
where the features pay off under a realistic budget:

| Position | legacy | transposition | ordering | all |
| --- | ---: | ---: | ---: | ---: |
| brandubh/opening | depth 2 | depth 2 | depth 2 | **depth 3** |
| tablut/midgame-0 | depth 2 | depth 1 | depth 2 | depth 2 |
| copenhagen/opening | depth 1 | depth 1 | depth 1 | **depth 2** |

Move ordering recovers the depth that the bare table lost on Tablut, and full
symmetry reaches one ply deeper on the symmetric openings. All scenarios still
returned legal moves when the deadline interrupted a deeper iteration.

#### Playing strength (bounded)

Two side-balanced 24-game matches (8 per ruleset, seed 9001, depth 5, equal
0.1-second decision budgets) compared legacy against the optimized default and
against the full feature set. All games completed (no truncations or rule
draws).

| Match | Legacy wins | Optimized wins | Brandubh | Tablut | Copenhagen |
| --- | ---: | ---: | ---: | ---: | ---: |
| legacy vs default | 10 | 14 | 3–5 | 3–5 | 4–4 |
| legacy vs full | 11 | 13 | 4–4 | 3–5 | 4–4 |

The optimized search won slightly more games in both matches, but 24 games per
pair is far too few for a strength claim; treat this as a functional check that
the optimized search plays complete legal games under equal budgets, not as
evidence of a measurable strength difference. A real strength comparison needs
hundreds of side-swapped games per pair and would need to control for the
engine's own variance.

#### Memory

`tracemalloc` peak is a Python-allocation high-water mark measured once per
scenario outside the timed repetitions. Median peaks were 78–702 KiB; the
ordinary table scenario was the largest (up to 702 KiB on Brandubh) because it
retains more bounded entries than the symmetry scenarios, which reach the same
value with fewer stored positions. `table_capacity` bounds the table at 200000
entries for this run.

### Interpretation

The production default keeps the bounded transposition table and improved move
ordering. Both preserve minimax values exactly and are neutral-to-positive across
the rulesets at these budgets. D4-canonical transpositions and full-state
symmetry pruning are implemented, tested and measured but stay opt-in: they are
large wins on symmetric positions and measurable overhead elsewhere, and
canonicalization cost grows with board size and history length. Callers that
know their corpus is symmetric (or that measure a net win on their hardware) can
enable them through the internal `search` configuration.

Limitations: this is a single WSL2 CPU host with two math threads and no GPU. The
corpus is deterministic but not exhaustive, and the fixed-time probe is a small
illustration rather than a statistical strength test. Wall-clock ratios vary
between repetitions; the raw report retains min/median/max.

<!-- Consolidated from alpha-beta-profiling.md -->

## Alpha-beta profiling and comparison (2026-10-26)

[Documentation index](../../../README.md) · [Performance](../../training/../evaluation/benchmarks/search-alpha-beta.md#phase-4-evidence-alpha-beta-symmetry-search)

This report covers the follow-up investigation of the alpha-beta search
bottlenecks reported after the control-refinement work: transposition-table
reuse, move ordering, D4 symmetry overhead and iterative deepening. It records
what reproduced, what did not, and the correctness fixes that came out of it.

Everything here was produced by the production search path (`choose_move` with
the `alpha_beta` algorithm) and the extended benchmark harness. Host: WSL2
(Linux 6.18, x86-64), Python 3.12.15, CPU only, two math threads, single
process. Absolute wall times are host-specific; compare ratios and completed
depths.

### Correctness

The fixed-depth pass reported **zero minimax mismatches** against the legacy
exact-value reference for every scenario, and the independent exhaustive oracle
found an optimal legal move for **37/37** feasible positions. The same held for
the dedicated ordering comparison. The new tests add:

- transposition-bound classification against the window actually searched;
- prepopulated lower/upper/exact entries and forced window tightening;
- compact repetition-key order independence and count sensitivity;
- move-dependent Shield maiden D4 equivariance, including tactical positions;
- repetition-history minimax equivalence and root-window pruning equivalence.

### Finding 1: the root searched every child with a full window

`_alpha_beta` and `_alpha_beta_legacy` both passed `(-inf, +inf)` to every root
child. The root was therefore a pure minimax fan-out at the widest part of the
tree, and move ordering could not prune anything there. The optimized search now
narrows the root window with the best value found so far; the first move to reach
the best value wins, which stays deterministic.

Isolated A/B on the full 37-position corpus (brandubh depth 3, tablut and
copenhagen depth 2, three repetitions, identical seed and evaluator), run with
`configs/benchmarks/alpha-beta-root-window.json` and the benchmark-only
`_root_window` hook:

| Variant | Median interior nodes | Median leaf evaluations | Median wall seconds |
| --- | ---: | ---: | ---: |
| Full window (historical) | 116 | 1,625 | 0.625 |
| Narrowed root window | 62 | 224 | 0.469 |

The per-run medians are over 111 runs each. Every fixed-depth value was identical
between the two variants (zero minimax mismatches) and the oracle found an optimal
move for 37/37 positions. Root-window pruning is the largest measured change in
this investigation: ~1.9× fewer median interior nodes and ~7.3× fewer median leaf
evaluations, with a smaller and noisier ~1.3× median wall-time reduction. It is
always on for the optimized search and is not a user-facing switch.

### Finding 2: the transposition table has no usable hits

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
| legacy | 0.688 | – | – | – | – | – |
| transposition | 0.518 | 63 | 2,169 | 0 | 0 | 0 |
| ordering | 0.503 | 62 | 1,552 | 0 | 0 | 0 |
| symmetry_transposition | 0.418 | 62 | 1,179 | 0 | 0 | 0 |
| symmetry_pruning | 0.376 | 49 | 1,179 | 0 | 0 | 0 |
| all | 0.375 | 49 | 1,179 | 0 | 0 | 0 |

The `transposition` and `ordering` rows differ only by move ordering, and the
difference (0.518 versus 0.503 s) is within run-to-run noise. The medians hide
per-position reuse: on the symmetric `brandubh/opening` the ordinary table again
recorded 40 hits from 147 probes, all depth-incompatible, while
`symmetry_transposition` recorded 75 canonical hits, 70 depth-compatible, 56
bound cutoffs and 74 genuine cross-orientation reuses. `symmetry_transposition`
and `symmetry_pruning` therefore reduce successors (canonical transpositions and
orbit collapsing), which is where their wall-time edge comes from. Fixed-time
completed depth was a median of 2 for **every** scenario, so none of the features
bought extra depth under a 0.2 s budget.

### Finding 3: move ordering is not the bottleneck

Seven ordering strategies were compared on the full corpus with identical
tables, depths and seeds:

| Strategy | Median wall s | Median interior nodes | Median successors | Median sort s |
| --- | ---: | ---: | ---: | ---: |
| full (production) | 0.436 | 62 | 1,552 | 0.0032 |
| static | 0.459 | 63 | 2,169 | 0.0002 |
| PV only | 0.440 | 62 | 1,552 | 0.0022 |
| TT move only | 0.468 | 63 | 2,180 | 0.0031 |
| killer only | 0.449 | 63 | 1,767 | 0.0032 |
| history only | 0.448 | 63 | 1,954 | 0.0034 |
| depth-gated | 0.451 | 62 | 1,552 | 0.0028 |

All strategies completed the same depth and their wall times are within run-to-run
noise (~10%). The principal variation is the only priority that measurably reduces
successors (1,552 versus 2,169 for static); the transposition move contributes
nothing because the ordinary table has no usable hits, and killer/history are not
distinguishable from noise. The sort itself is negligible (≤3 ms). The
production `full` ordering is kept because PV is a real, if modest, reduction and
the added cost is below measurement noise; the benchmark-only strategies are
retained so the comparison stays reproducible.

### Finding 4: eager successor construction dominates

The per-node timing breakdown shows where the wall time actually goes. For the
`ordering` scenario the median `successor_seconds` was ~0.45 s of a ~0.50 s
search; `sort_seconds` was ~0.003 s and `transposition_seconds` ~0.0006 s.
`_ordered` builds and scores every legal child *before* the move loop can cut
off, so the cost tracks the branching factor of visited nodes, not the number of
leaves. This is shared with the legacy search and is the main remaining
optimization opportunity (lazy child generation); it is recorded here rather than
implemented because a safe refactor is larger than this change set.

### Iterative deepening: interrupted work is small

The fixed-time pass records the discarded work of the interrupted iteration.
Across the five optimized scenarios the median `discarded_nodes` was 16–19 nodes,
a small fraction of a completed pass. The per-iteration timings are exposed
as `iteration_timings`, `interrupted_depth`, `interrupted_seconds` and
`discarded_nodes`, and `chosen_from_completed_iteration` records that the returned
move always came from a completed iteration. A conservative early-stop policy was
considered but not enabled: the discarded work is already small, and predicting
whether the next depth will finish risks stopping a search that would have
completed usefully. The instrumentation is in place to revisit that decision with
a larger budget.

### Finding 5: D4 symmetry is correct but situational

The move-dependent Shield maiden score is D4-equivariant for every supported
ruleset, including asymmetric histories and the Copenhagen shieldwall,
encirclement and fort surfaces (new tests). Canonical transpositions and orbit
pruning are therefore value-safe. Their measured effect is limited to genuinely
symmetric positions: median `symmetry_moves_skipped` is 0 because most midgame
positions have a trivial stabilizer, and the fixed-time completed depth is
unchanged. In the combined scenario canonicalization and stabilizer time together
were ~16 ms per search median (canonicalization ~9 ms, stabilizer ~7 ms).
Symmetry features stay optional and off by default.

### Recommended defaults

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

### Reproduction

```sh
## Feature ablation (legacy, table, ordering, symmetry, all)
.venv/bin/python -m hnefatafl.cli benchmark-alpha-beta \
  --config configs/benchmarks/alpha-beta-symmetry.json \
  --output work/alpha-beta-profiling.json \
  --summary-output docs/evaluation/benchmarks/raw/search/alpha-beta-profiling-2026-10-26.json

## Move-ordering strategies
.venv/bin/python -m hnefatafl.cli benchmark-alpha-beta \
  --config configs/benchmarks/alpha-beta-ordering.json \
  --output work/alpha-beta-ordering.json \
  --summary-output docs/evaluation/benchmarks/raw/search/alpha-beta-ordering-2026-10-26.json

## Root-window A/B
.venv/bin/python -m hnefatafl.cli benchmark-alpha-beta \
  --config configs/benchmarks/alpha-beta-root-window.json \
  --output work/alpha-beta-root-window.json \
  --summary-output docs/evaluation/benchmarks/raw/search/alpha-beta-root-window-2026-10-26.json
```

Each configuration records the corpus hash, source hashes and host details in
the report. `--summary-output` writes the compact checked-in summary from the
same run, so its `source_sha256` always names the revision that produced its
numbers. The benchmark's `scenarios` key selects a subset; the per-node timing
fields are populated because the benchmark enables the internal `_profile` hook.
Trimmed summaries (config, host, source hashes, fixed-depth, fixed-time and oracle
aggregates) are kept as
[profiling summary](./raw/search/alpha-beta-profiling-2026-10-26.json),
[ordering summary](./raw/search/alpha-beta-ordering-2026-10-26.json) and
[root-window summary](./raw/search/alpha-beta-root-window-2026-10-26.json).

<!-- Consolidated from alpha-beta-controls.md -->

## Alpha-beta control refinements evidence

[Documentation index](../../../README.md) · [Performance](../../training/../evaluation/benchmarks/search-alpha-beta.md#alpha-beta-profiling-and-comparison-2026-10-26)

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

### Correctness

The fixed-depth pass reported zero minimax mismatches against the legacy
exact-value reference for all six ablations, and the independent exhaustive oracle
found an optimal legal move for all 37 feasible positions. The before/after
comparison against the pre-refinement code reproduced both results exactly.

### Fixed-depth median wall seconds by ruleset

| Ruleset (depth) | legacy | transposition | ordering | symmetry_pruning | all |
| --- | ---: | ---: | ---: | ---: | ---: |
| Brandubh (3) | 1.448 s | 1.496 s | 1.507 s | 0.351 s | 0.377 s |
| Tablut (2) | 0.082 s | 0.077 s | 0.076 s | 0.047 s | 0.050 s |
| Copenhagen (2) | 0.463 s | 0.478 s | 0.486 s | 0.321 s | 0.319 s |

The same-host before/after run moved these medians by less than 7% in either
direction, so the structural pruning change is reported as correctness and
work-avoidance rather than a wall-clock claim.

### Cross-orientation reuse

| Scenario | Positions with reuse | Sum of medians | Maximum |
| --- | ---: | ---: | ---: |
| `symmetry_transposition` | 9 / 37 | 326 | 101 |
| `all` (pruning + canonical table) | 0 / 37 | 0 | 0 |

With canonical transpositions alone, genuine cross-orientation reuse is common.
With move pruning enabled, each orbit is collapsed before a differently oriented
probe can occur, so the counter correctly reports no cross-orientation reuse while
`symmetry_hits` still records same-orientation canonical-table hits.

### Isolated history ordering

Passing an empty history dictionary to the same priority function (otherwise
identical configuration, depth 3–4) produced mixed per-position node counts: two
positions improved and six worsened, with no change to completed depth or value.
The ordering refinement guarantees that history is always consulted in the
documented priority order (PV, table, killers, history, stable static order) and
that sorting is skipped when every history score is zero; the unit tests assert
that priority directly.

### Equal-time strength comparison (legacy vs full symmetry)

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

### Table memory bound

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

### Deadline inside canonicalization and symmetry pruning

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

### Explicit default search identity

A record saved with an explicit nested `search` object that repeats the defaults
resolves to the same behaviour as the same algorithm without one, but is
intentionally a distinct effective identity. This keeps historical records stable:
a game saved before the object existed is never silently merged with a newer
explicit-default record, and their runes and statistics stay separate. The trade-off
is that two behaviourally equivalent configurations can appear as two identities
in the Hall of Records. `tests/test_identities.py` and `tests/test_api.py` assert
the distinct and stable variant IDs and runes; the policy is documented in
[Persistent identity runes](../../game/interface/README.md) and [AI opponents](../agents.md).
