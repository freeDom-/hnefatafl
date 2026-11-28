# ADR 003: Optional in-run learning-rate adaptation

**Status:** accepted, implemented, off by default.

## Context

Self-play is one long nonstationary trajectory. Restarting or running a population
(PBT/PB2/Hyperband) breaks the single-checkpoint model and multiplies compute.
Lower training loss is not a comparable strength signal.

## Decision

Use a conservative HyperController variant that changes only the learning rate at
evaluation boundaries inside the existing job:

- A small candidate set (2–8 rates); the configured rate must be a candidate.
- Ridge-regularized local predictors conditioned on recent reward/action history.
- Reward is the completed-game score against one evaluation opponent, with
  unfinished games excluded. For a fixed opponent this is an absolute balanced
  score; for an evolving Champion it is the genuine change from that Champion's
  observed baseline, clamped to [-1, 1].
- Warm-up, cadence, per-context forced trials and evaluation/iteration dwell gates
  bound decisions.
- The optimizer is never rebuilt: only learning rates are written, preserving Adam
  moments/SGD momentum; Schedule-Free is incompatible.
- State is bounded JSON in the existing full checkpoint; malformed/incompatible
  state resets the controller only, never the model/optimizer/replay.
- The controller runs in the training process (including workers) on CPU and uses
  a dedicated RNG stream.
- It is rejected with Optuna tuning, which varies the learning rate across trials.

## Consequences

The controller adds negligible CPU cost and preserves resume/worker portability. It
may saturate on an easy fixed opponent, is noisy at small evaluation budgets, and
optimizes its proxy rather than Elo/strength. No strength claim follows from a
successful adaptation run. Measurements remain in the
[online-adaptation benchmark](../../evaluation/benchmarks/training/online-adaptation.md).
