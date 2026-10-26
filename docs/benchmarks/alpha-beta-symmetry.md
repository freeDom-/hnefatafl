# Phase 4 evidence: alpha-beta symmetry search

[Documentation index](../README.md) · [Performance](../PERFORMANCE.md#phase-4-alpha-beta-symmetry-search)

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
| `docs/benchmarks/alpha-beta-symmetry-cpu-2026-01-01.json` | Raw CPU report (all scenarios, counters and raw samples) |

## Protocol

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

## Environment

- WSL2 Linux x86-64, Intel Core i7-8750H, 12 logical CPUs, two math threads.
- Python 3.12.15, CPU-only, no GPU execution.
- The benchmark ran alone; no training job or browser suite competed for CPU.
- `canonicalization_seconds` is accumulated inside the search and included in the
  wall-clock comparison, so it is not hidden from the reported cost.

## Results

See the raw report for per-position samples. The tables below summarise medians
across the corpus; ratios compare medians and are not a universal speed estimate.

### Correctness

All six scenarios reported the same defender-perspective minimax value as the
legacy reference on every fixed-depth position (`minimax_mismatches: []`). The
independent exhaustive oracle validated 37 feasible positions: every scenario
returned an optimal, legal move (`positions: 37`, `value_matches: 37`).

### Fixed depth (medians across positions)

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

### Where symmetry helps and where it costs

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

### Fixed time

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

### Playing strength (bounded)

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

### Memory

`tracemalloc` peak is a Python-allocation high-water mark measured once per
scenario outside the timed repetitions. Median peaks were 78–702 KiB; the
ordinary table scenario was the largest (up to 702 KiB on Brandubh) because it
retains more bounded entries than the symmetry scenarios, which reach the same
value with fewer stored positions. `table_capacity` bounds the table at 200000
entries for this run.

## Interpretation

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
