# Completed-trial bookkeeping cost

The [raw report](tuning-bookkeeping.json) records the environment, the measured
per-operation costs and the derived figures.

A tuning study used to re-read its whole Optuna study after every trial to refresh
the completed-trial counter, so a study of N trials touched O(N²) stored trials and
paid N full-history reads. The counter now follows Optuna's own trial callback and
the history is read once when a study is loaded and once when its final result is
aggregated.

Reproduce in a fresh process without other benchmarks or tests:

```sh
OMP_NUM_THREADS=1 .venv/bin/python scripts/benchmark_tuning_bookkeeping.py \
  --output docs/benchmarks/tuning-bookkeeping.json --repeats 5
```

The studies are real Optuna SQLite databases built with a trivial objective, so no
training happens. The per-operation costs are measured: a full-history read
(`study.trials` plus the state scan the reconciliation performed, 42.0 ms for the
1,000-trial study) and one counter update (0.32 µs: the lock plus the increment the callback
performs). The cumulative figures are derived from those measurements rather than
timed from two complete runs of the old and the new code.

| Study | Full-history read | Previous bookkeeping | Current bookkeeping | Stored trials touched |
| --- | ---: | ---: | ---: | ---: |
| 10 trials | 2.5 ms | ≤ 25 ms | 5.1 ms | 55 → 20 |
| 100 trials | 7.4 ms | ≤ 736 ms | 14.8 ms | 5,050 → 200 |
| 1,000 trials | 42.0 ms | ≤ 41.98 s | 84.3 ms | 500,500 → 2,000 |
| Resumed: 1,000 stored + 10 new | 53.1 ms | ≤ 531 ms | 106.2 ms | 10,055 → 2,020 |

The previous figure charges every read the final study's own read cost, so it is an
upper bound: the true cumulative cost is lower, because the reads made while the
study was still small were cheaper. The row counts are exact. The current figure is
two history reads plus one update per trial, and the study's heartbeat keeps
publishing throughout — the handover after a trial publishes before anything that
may block, and the count itself is now updated by Optuna's callback rather than by
a storage read.

These numbers measure bookkeeping cost only. They are not evidence of training
throughput, model strength or GPU behaviour.
