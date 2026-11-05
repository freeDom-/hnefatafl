# Online learning-rate controller overhead and baselines

The [raw report](online-hyperparameter-adaptation.json) records the environment,
every run and the summary, including failures.

This is a **smoke benchmark**, not a strength experiment. It runs a tiny CPU
budget (3 seeds, 4 iterations, 4 evaluation games per iteration, brandubh) and
compares three equal-budget strategies:

| Mode | Strategy |
| --- | --- |
| `fixed` | One learning rate (`0.001`) for the whole run |
| `scheduler` | An inexpensive two-phase step schedule: half the iterations at `0.001`, then half at `0.0005` |
| `hypercontroller` | The optional online controller |

The scheduler is realised with the existing resume path (it is **not** a shipped
training option), so it is the cheapest realistic baseline rather than a new
feature. All three modes spend the same budget here: 16 cumulative optimizer
updates and 16 cumulative evaluation games (4 evaluations × 4 games) per run.

Reproduce in a fresh process without other benchmarks or tests:

```sh
OMP_NUM_THREADS=1 .venv/bin/python scripts/benchmark_hyper_adaptation.py \
  --output docs/benchmarks/online-hyperparameter-adaptation.json
```

## Results

| Mode | Median wall | Cumulative updates | Cumulative eval games | Final eval games | Median evaluation | Median controller |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Fixed | 1.27 s | 16 | 16 | 4 | 581 ms | 0 ms |
| Scheduler | 1.12 s | 16 | 16 | 4 | 447 ms | 0 ms |
| HyperController | 1.24 s | 16 | 16 | 4 | 560 ms | 0.31 ms |

Cumulative evaluation work is the total the run actually paid; the final
evaluation's game count (4) is reported separately so the two are never confused.
The resumed scheduler phase restores the earlier history, so the aggregation counts
only iterations at or after the resume boundary and never double-counts or drops
the boundary evaluation; `tests/test_benchmark_hyper_adaptation.py` pins that with
synthetic phase results and one real tiny scheduler run. The controller's own cost
is `adaptation_seconds`, reported separately from `evaluation_seconds`: ~0.08 ms
per decision here (four decisions per run), three orders of magnitude below the
evaluation it reads.

The effective-rate trajectories confirm each strategy acts:

* Fixed: `0.001 → 0.001 → 0.001 → 0.001`
* Scheduler: `0.001 → 0.001 → 0.0005 → 0.0005`
* HyperController: e.g. `0.001 → 0.0003 → 0.003 → 0.001`

## Limitations and non-claims

* The score used here is the **controller's feedback signal** against a fixed
  opponent, not a held-out strength evaluation. The report records
  `held_out_strength_evaluation: "not executed"`.
* At this budget every mode scored between 0.0 and 0.25, so no strength ranking
  is possible or claimed. The benchmark bounds the overhead and shows the
  decision trace; it does not show that any strategy plays better.
* The scheduler baseline pays one extra resume handover; its wall time is
  reported as the sum of both phases.
* A meaningful comparison needs multiple seeds, a much larger budget and a
  strength metric. The runnable instructions are in
  [the design record](../designs/online-hyperparameter-adaptation.md#reproducible-benchmark-instructions);
  multi-seed and held-out strength experiments are **not executed**.
