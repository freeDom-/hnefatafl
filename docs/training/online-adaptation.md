# Optional online learning-rate adaptation

[Training index](README.md) · [Parameters](parameters.md) · [Algorithms](algorithms.md) · [ADR](../development/decisions/adr-003-online-learning-rate-adaptation.md) · [Benchmark](../evaluation/benchmarks/training/online-adaptation.md)

`adaptation: hypercontroller` optionally adjusts one hyperparameter — the learning
rate — inside a continuing job. It is **off by default**, not a strength claim and
not a replacement for Optuna.

## Why this controller

Self-play is a single nonstationary trajectory. Population methods (PBT/PB2/
Hyperband) require multiple copies/restarts; hypergradient methods need a
differentiable validation objective and per-step cost. HyperController fits one
continuing trajectory, needs no population, and has small bounded checkpointable
state. This is an architectural suitability argument; the shipped benchmark measures
overhead/behaviour, not superiority.

## Algorithm

The controller models the next evaluation score from a short recent history of
scores and chosen learning rates. Each candidate rate has a ridge-regularized linear
predictor (`V`, `b`, `n`; `G = V⁻¹b`). At a decision it selects the candidate with the
highest predicted value. There is one discrete candidate set, not a continuous
search.

Configuration:

| Field | Meaning |
| --- | --- |
| `adaptation_lr_candidates` | 2–8 distinct rates; configured `learning_rate` must be included |
| `adaptation_lag` | History window 1–3 |
| `adaptation_cadence` | Completed evaluations between decisions |
| `adaptation_warmup` | Evaluations observed before any decision |
| `adaptation_min_dwell` | Evaluations a chosen rate is kept |
| `adaptation_min_dwell_iterations` | Optional iteration dwell; 0 disables |
| `adaptation_min_trials` | Per-context forced observations of each candidate |

Coverage is per `(candidate, action-history context)`, so an unobserved context
cannot be hidden by good predictions elsewhere. Contexts are bounded (512).

## Feedback signal

The controller never reads training loss. It observes the evaluation score against
the selected opponent, balanced by side-swapped games:

```text
fixed opponent:
  reward = (wins + 0.5 * draws) / completed_games

evolving Champion:
  raw = (wins + 0.5 * draws) / completed_games
  reward = clamp(raw - observed_champion_baseline, -1, 1)
```

- Unfinished/truncated games are excluded, not scored as losses. An evaluation with
  no completed game yields no observation.
- For a Champion, the first evaluation sets the baseline and never drives a decision.
  Identical scores yield exactly zero improvement; the regularized baseline
  uncertainty widens the decision margin but never enters the reward.
- The credited action is the rate active for the interval that produced the score;
  a change applies to the next iteration.
- Missing/non-finite observations do not advance warm-up/cadence/dwell.
- Adaptation requires at least four evaluation games; a small `evaluation_games`
  value is noisy. Sparse evaluations simply produce fewer decisions.
- Adaptation is rejected with Optuna tuning, where the study varies the rate.

## Champion transitions

A fixed opponent keeps absolute scores comparable. A Champion promotion changes the
incumbent, so pre/post raw scores are not comparable. On promotion the controller
keeps persistent predictors, effective rate, counters, dwell timers and RNG, but
refreshes the new Champion's local baseline/evaluation block. A promotion is an
idempotent transition and is not re-applied on resume. Cross-Champion transfer is a
trade-off: a very different incumbent may need a few observations before its
uncertainty justifies a change. It is never judged from scratch.

## Safety and resume

- The optimizer is never rebuilt; only parameter-group learning rates change, so
  Adam moments and SGD momentum are preserved. Schedule-Free is incompatible.
- A selection failure keeps the current rate and records a fallback; updates already
  applied are not rolled back or claimed to be undone.
- State is finite/bounded JSON in the existing full checkpoint: at most 512 contexts,
  bounded history/events, dedicated RNG. It runs in the training process (including
  workers) on CPU.
- Same algorithm/candidates/lag/evaluation identity resumes continuity. Changed
  candidate set, lag or evaluation identity resets only the learned controller state
  with a visible reason; model/optimizer/replay/history are untouched.
- A different active Champion refreshes only the per-Champion block. Incompatible
  reward semantics discard predictors only, preserving the effective rate,
  optimizer, counters, dwell and RNG.
- Malformed state resets the controller atomically before adoption; the tensor-only
  checkpoint loader remains the only loader.
- An explicit resume `learning_rate` override is honoured and restarts the dwell
  timer; it is not silently discarded. Disabling adaptation drops controller state
  and keeps the last effective rate.
- Remote workers must advertise `hyper_adaptation`; a Champion job additionally
  requires `hyper_adaptation_champion_transition` and a supported state version.
  Capability gates cannot be bypassed by an administrator override.

## Limitations

- A fixed opponent can saturate, making candidates indistinguishable.
- Small evaluation samples are noisy; increase `evaluation_games` for finer decisions.
- The proxy is local and opponent-specific, not Elo or tournament strength.
- No run may be called “stronger” from lower loss or a successful adaptation run.
- Detailed schema/version migration and state-validation rules live with the
  implementation (`hnefatafl/hyper_adaptation.py`); the decision is recorded in
  [ADR 003](../development/decisions/adr-003-online-learning-rate-adaptation.md).
