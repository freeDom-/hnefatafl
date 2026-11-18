# Win-streak evaluation fast path

[Progression and achievements](../PROGRESSION.md) · [Performance](../PERFORMANCE.md)

Ordinary rated completion previously folded the player's whole applied rating
ledger to evaluate the best win streak, for both participants, inside the writer
transaction. A newly completed loss or draw cannot itself establish a longer
winning streak, so the evaluator now takes a bounded tail probe instead:

- A fresh draw names no account; the streak criterion is skipped for both.
- A fresh win names only the winner; the loser skips the streak criterion.
- The named winner reads at most the next unawarded streak milestone of trailing
  wins. If that bounded run does not reach the milestone, the streak criterion is
  skipped. If it does, the exact full-ledger fold runs, so no milestone is missed.
- Corrections, withdrawals, pool rebuilds and migration always measure exactly,
  because removing an earlier loss can create a longer historical streak.

The probe reads the same joined ledger as the full fold, only with a reverse
sequence order and a `LIMIT`; it is not a parallel, separately maintained counter.

## Measurement

[Raw samples](progression-streaks.json), generated with
`scripts/benchmark_progression_streaks.py --games 2000 --samples 25` on the
repository's Python 3.11 environment. The synthetic ledger holds 2,000 applied
results for the target account, whose newest run is one win with a next milestone
of three. Values are medians of 25 in-process samples on the same connection after
a warm read; they compare one full fold with one tail probe.

| Games | Full fold median | Tail probe median | Rows folded (full / tail) | Speedup |
| ---: | ---: | ---: | ---: | ---: |
| 2,000 | 3.90 ms | 0.045 ms | 2,000 / 3 | 87× |

The measurement is representative of query work, not a production latency budget.
It shows the fast path bounds ordinary completion work by the next milestone rather
than by history length. The full fold still runs for the winner only when a
milestone is actually crossed and always for corrections, withdrawals, rebuilds
and migration, so historical correctness is unchanged.
