# Lazy Jarl successor construction

Isolated comparison with main `5a343668411033ed3283ed51e01323454975723a`, without
the other training optimizations. The [raw report](training-lazy-jarl.json)
retains source hashes, actual search counters, sample ranges and work hashes.
Three alternating fresh-process pairs per ruleset/case discard one warm-up each.
No tests or other benchmarks ran concurrently. CPU, one math thread, FP32 model
inference, fixed seeds/weights and generous 300-second ceilings.

Fixed-position search uses four seeded opening/midgame positions, Brandubh depth
3 and Tablut/Copenhagen depth 2. Actual production training evaluation uses four
side-swapped four-ply games, eight candidate neural visits and Jarl depth 2;
recommended networks. Both evaluation variants collect identical diagnostics
through a matched observer, included in timing. Capped games remain unfinished.

| Ruleset | Search before/after median seconds | Search speedup | Evaluation before/after median seconds | Evaluation speedup |
| --- | ---: | ---: | ---: | ---: |
| Brandubh | 1.2923 / 0.6121 | 2.11x | 0.4528 / 0.1812 | 2.50x |
| Tablut | 1.4135 / 0.3594 | 3.93x | 2.8633 / 0.9056 | 3.16x |
| Copenhagen | 9.3687 / 0.8486 | 11.04x | 12.8811 / 2.1326 | 6.04x |

All work hashes match. Fixed-position hashes include values, completed depths and
chosen moves. Evaluation hashes additionally cover every decision's board, turn,
move, value and completed depth. Actual evaluation successor construction falls
7,842→1,579, 35,140→8,241 and 63,337→5,106 respectively; searched nodes and leaf
evaluations are unchanged. Every recorded Jarl interruption count is zero. These
gains avoid unvisited child construction/scoring; they do not reduce search depth,
simulations or game budgets. They are not full-training or GPU speedup claims.

The optimized interior search shuffles once up front, then freezes the same
TT/killer/history priority buckets as eager ordering. Only demanded groups build
and score states, keeping the same stable static order within each group.
Freezing is essential because deeper cutoffs mutate the shared history table
while the iterator is suspended. Iterator cleanup finalizes skipped-successor
counters on cutoff, cancellation and error. Root construction and the legacy
search stay unchanged. `_lazy_successors: false` is an internal diagnostic
ablation, not a new public `search` setting.

367 focused tests pass, including complete lazy/eager child ordering under all
seven ordering strategies, both heuristic modes and near/deep horizons; mutation
of history/RNG while suspended; identical fixed-depth moves/values/node counts;
existing independent minimax oracles, symmetry/history rules, legal deadline
fallback and cancellation. Fixed-time searches can complete more work and choose
different moves; confirm any strength effect separately. Bounded priority-group
storage replaces eagerly materialized unused children, but no memory reduction
measurement is claimed.

Reproduce from the repository root:

```sh
mkdir -p work/jarl-baseline
git archive 5a343668411033ed3283ed51e01323454975723a hnefatafl | tar -x -C work/jarl-baseline
.venv/bin/python scripts/benchmark_training_optimization.py --feature jarl --variant baseline --source work/jarl-baseline --output work/jarl-before.json
.venv/bin/python scripts/benchmark_training_optimization.py --feature jarl --variant candidate --output work/jarl-after.json
```

Repeat with `--feature jarl-evaluation` and each ruleset. Use `--repeats 1` in
three alternating process pairs for the collection protocol. Risks are ordering,
RNG, tie and deadline regressions; exact eager-order tests and the retained eager
ablation are the reference for investigating them.
