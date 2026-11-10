# Champion-adaptation controller benchmark

The [raw report](champion-adaptation.json) records every run and the summary.

This is a **synthetic controller benchmark**, not a playing-strength experiment.
It drives `HyperController` with a deterministic evaluation stream whose true best
learning rate alternates between the extreme candidates every 20 evaluations
(a stand-in for frequent Champion promotions), and compares three strategies at
an equal evaluation budget (5 seeds, 400 evaluations, 4 completed games each):

| Mode | Strategy |
| --- | --- |
| `fixed` | One learning rate (`0.001`) for the whole run |
| `forced_reset` | A forced-reset ablation: rebuild the controller at every promotion, keeping only the effective rate. It emulates the observable effect of the earlier reset-based controller, not the historical version-4 algorithm |
| `persistent` | The corrected behavior: an idempotent transition that keeps the learned predictors, dwell timers and effective rate |

The synthetic score is `0.75 - 0.2 * |index - best| + N(0, 0.08)`, so the optimum
is never the fixed rate and only an adaptive strategy can track it. "Regret" is
the mean gap to an oracle that always picks the true best rate.

Reproduce in a fresh process:

```sh
OMP_NUM_THREADS=1 .venv/bin/python scripts/benchmark_champion_adaptation.py \
  --output docs/benchmarks/champion-adaptation.json
```

## Results

| Mode | Median mean regret | Median changes | Median controller CPU | Contexts | Events kept | Reward history |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Fixed | 0.196 | 0 | 0 ms | 0 | 0 | 0 |
| Forced reset | 0.188 | 150 | 7.8 ms | 9 | 19 | 18 |
| Persistent | 0.203 | 16 | 10.6 ms | 9 | 100 | 64 |

The persistent transition changes the rate about nine times less often than the
forced-reset ablation and keeps the full bounded reward history (64) and event log
(100) instead of losing them at every promotion. The controller CPU cost stays tens
of microseconds per evaluation across 400 evaluations, three orders of magnitude
below the evaluation it reads.

The corrected reward is genuine change (`score - baseline_score`). In this
synthetic stream the baseline is a single noisy four-game sample, so measuring
change against it is much weaker than the previous shrunken reference, which
partly reflected the absolute score level and manufactured a spurious signal. The
persistent controller is therefore deliberately conservative here and its
synthetic regret is slightly above the fixed rate; that is the honest cost of not
inventing progress, not a strength result. The evidence gate, the exploration
cooldown and the regularized uncertainty together keep the rate changes bounded.

## Limitations and non-claims

* The score is synthetic. The report records
  `held_out_strength_evaluation: "not executed"`, and **no playing-strength claim
  is made**.
* `forced_reset` is a forced-reset ablation, not a comparison against the
  historical version-4 source: it rebuilds the current controller at each
  promotion and restores only the effective rate.
* The optimum alternates deterministically; a real Champion's difficulty changes
  for reasons this benchmark does not model. The four-game evaluations make the
  baseline noisy, which is precisely the regime where genuine-change measurement
  is hardest.
* `tests/test_benchmark_champion_adaptation.py` pins the accounting and the
  qualitative evidence-retention property, not the exact medians.
