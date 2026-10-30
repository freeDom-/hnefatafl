# Batched training evaluation

Baseline: `5a343668411033ed3283ed51e01323454975723a` (0.33.0 main).
The [raw samples](training-evaluation-batching.json) retain source SHA-256 hashes,
environment, budgets, inference counters and outcome hashes. Three fresh-process
samples per mode and ruleset alternate baseline/candidate order; each process
discards one warm-up. No tests or other benchmarks ran concurrently.

CPU PyTorch FP32, one math thread, recommended network sizes, eight evaluation
games, 32 visits, 16-ply limit, seed 42 weights, 300-second search ceiling.
The candidate explicitly requests eight concurrent evaluation games and batches
up to eight. CPU auto remains sequential. Games hitting the cap stay unfinished.

| Ruleset | Sequential median seconds | Batched median seconds | Speedup |
| --- | ---: | ---: | ---: |
| Brandubh | 0.8822 | 0.6217 | 1.42x |
| Tablut | 2.3165 | 1.4828 | 1.56x |
| Copenhagen | 7.6043 | 5.6710 | 1.34x |

Recorded game-result hashes match across all repetitions and modes. These hashes
cover sides, seeds, winners, plies and reasons, not bitwise equality of every
intermediate prediction. Regression tests additionally compare paired results,
route distinguishable candidate/incumbent models, exercise cancellation and real
update/resume, and verify post-update backend refresh and cleanup. CUDA tests
skip explicitly on this CPU-only host. No GPU speedup or playing-strength gain is
established by these timings.

Reproduce from the repository root (use the candidate script for both sources):

```sh
mkdir -p work/evaluation-baseline
git archive 5a343668411033ed3283ed51e01323454975723a hnefatafl | tar -x -C work/evaluation-baseline
.venv/bin/python scripts/benchmark_training_optimization.py --feature evaluation --variant baseline --source work/evaluation-baseline --ruleset brandubh --output work/evaluation-before.json
.venv/bin/python scripts/benchmark_training_optimization.py --feature evaluation --variant candidate --ruleset brandubh --output work/evaluation-after.json
```

Use `--repeats 1` in alternating fresh-process order three times to reproduce the
collection protocol; repeat for Tablut/Copenhagen. On a CUDA host add
`--device cuda`. Lower deadlines and full-length games need their own comparison:
interleaving changes queue wait and floating-point kernels can change close
decisions. Champion comparisons always retain separate model queues/caches and
ordered side-swapped pair results. This optimization does not overlap training
and evaluation or alter promotion thresholds.
