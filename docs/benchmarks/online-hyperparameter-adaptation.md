# Online learning-rate controller overhead

The [raw report](online-hyperparameter-adaptation.json) records the environment,
per-run figures and the summary.

This is a **smoke benchmark**, not a strength experiment. It runs a tiny CPU
budget (3 seeds, 4 iterations, 4 evaluation games per iteration, brandubh) and
compares a fixed learning rate with the HyperController. A handful of short runs
cannot show a strength difference, and the fixed-opponent score saturates and is
noisy at this budget.

Reproduce in a fresh process without other benchmarks or tests:

```sh
OMP_NUM_THREADS=1 .venv/bin/python scripts/benchmark_hyper_adaptation.py \
  --output docs/benchmarks/online-hyperparameter-adaptation.json
```

Both modes pay the same evaluation cost; the controller adds only a bounded
decision at each evaluation boundary. The benchmark reports the controller's own
`adaptation_seconds` separately from `evaluation_seconds`, so the two are never
conflated.

| Mode | Median wall | Median updates | Evaluation games | Median evaluation | Median controller |
| --- | ---: | ---: | ---: | ---: | ---: |
| Fixed learning rate | 0.70 s | 6 | 4 | 293 ms | 0 ms |
| HyperController | 0.66 s | 6 | 4 | 284 ms | 0.19 ms |

The controller cost is ~0.05 ms per decision here (four decisions per run), three
orders of magnitude below the evaluation it reads. Wall time and gradient work are
indistinguishable at this budget, so the controller does not meaningfully change
the job's cost profile.

The effective-rate trajectories differ by seed (for example
`0.0003 → 0.003 → 0.0003 → 0.003` for one controller run), which confirms the
controller actually acts; the fixed runs stay at `0.001`. All runs used the same
evaluation budget, so the scores are comparable within this benchmark. At this
budget every mode scored between 0.0 and 0.25, which is why no strength claim is
made: the benchmark's purpose is to bound the overhead and show the decision
trace, not to rank learning rates.

A meaningful comparison would need multiple seeds, a substantially larger budget
and a strength metric; the reproducible instructions are in
[the design record](../designs/online-hyperparameter-adaptation.md#reproducible-benchmark-instructions).
