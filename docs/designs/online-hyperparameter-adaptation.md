# Optional online hyperparameter adaptation

Status: implemented, off by default. This record explains why the training loop
adapts a single hyperparameter (the learning rate) with a conservative
HyperController variant, what signal it observes, what it deliberately does not
claim, and what evidence would justify a different choice.

Source code: `hnefatafl/hyper_adaptation.py`. Integration:
`hnefatafl/training.py` (`normalize_config`, `_make_optimizer`,
`_set_optimizer_learning_rate`, the iteration loop and `checkpoint_now`).

## Problem

Self-play training is a single, long, nonstationary trajectory. A fixed learning
rate is often wrong at some point in the run, but restarting or running a
population of jobs is expensive and breaks the single continuing checkpoint. The
goal is an *optional* controller that changes one hyperparameter at safe
boundaries inside the existing job, preserves optimizer momentum, survives a
checkpoint/resume, runs on remote workers, and never makes a stronger-AI claim
from a lower training loss.

## Candidate algorithms

| Algorithm | Fits one continuing job? | Orchestration | Cost | Verdict |
| --- | --- | --- | --- | --- |
| **HyperController** (arXiv:2504.19382) | Yes: adapts online inside the run | None beyond the job | Small bounded linear predictor, `O(s^3)` per update, CPU | **Selected** |
| PBT (arXiv:1711.09846) | No: requires a population with exploit/explore | Population manager, multiple workers | `N` full training copies | Rejected: changes the job model and the single-checkpoint guarantee |
| PB2 (arXiv:2002.02518) | Partly: online bandit, but assumes population/PPO | Population/GP machinery | `O(n^3)` GP over samples | Rejected: heavier and designed around a population |
| Hypergradient descent (arXiv:1703.04782) | Yes in principle | None | Extra gradient through the update rule | Rejected: needs a differentiable validation objective per step; fragile for noisy self-play and adds per-step cost |
| Hyperband / ASHA | No: successive halving needs restarts | Scheduler + many partial runs | Many runs | Rejected: cannot adapt inside one trajectory |
| Optuna (already shipped) | Across trials only | Study process | Full trial per sample | Kept, but explicitly incompatible with in-run adaptation (the study varies the learning rate across trials) |
| Plateau / step LR scheduler | Yes | None | Negligible | Considered as the inexpensive baseline for benchmarks; rejected as the feature because it cannot choose among candidates from evidence and is not an online hyperparameter optimizer |

HyperController is the only candidate that (a) adapts inside one continuing
trajectory, (b) needs no population or restart, and (c) has a small, bounded,
checkpointable state. That is exactly the shape of this training loop.

Primary sources: Gornet, Kantaros and Sinopoli,
[HyperController](https://arxiv.org/abs/2504.19382); Jaderberg et al.,
[Population Based Training](https://arxiv.org/abs/1711.09846); Parker-Holder,
Nguyen and Roberts, [PB2](https://arxiv.org/abs/2002.02518) (NeurIPS 2020);
Baydin et al., [Hypergradient Descent](https://arxiv.org/abs/1703.04782)
(ICLR 2018); Li et al., [Hyperband](https://arxiv.org/abs/1603.06560)
(JMLR 2018); Akiba et al., [Optuna](https://arxiv.org/abs/1907.10902) (KDD 2019).

This is an **architectural suitability** argument, not a demonstrated
superiority claim. The shipped smoke benchmark shows that the controller's cost
is negligible and that it acts; it does not show that it plays better than a
fixed rate or a scheduler. Such a claim would need the multi-seed, adequate-budget
strength experiment described below, which has not been run.

## How the selected algorithm works

HyperController models the objective as an unknown Linear Gaussian Dynamical
System and learns a small linear predictor of the *next* objective value from a
short window of recent observations, conditioned on the recent sequence of chosen
values. Each candidate value has its own ridge-regularized predictor
(`V`, `b`, `n`); `G = V^{-1} b` is recomputed when needed. At a decision the
candidate with the highest predicted value is selected.

In this implementation there is exactly one hyperparameter (the learning rate)
and a small explicit candidate set, so no continuous space is discretized.

* `adaptation_lr_candidates` — the candidate values (2–8, distinct).
* `adaptation_lag` — the history window `s` (1–3).
* `adaptation_cadence` — evaluations between decisions.
* `adaptation_warmup` — evaluations before any decision.
* `adaptation_min_dwell` — evaluations a chosen rate is kept before it may change.
* `adaptation_min_trials` — forced trials of every candidate **in each
  action-history context** before that context's predictions may be exploited.
  Coverage is per `(candidate, context)`, not global: a predictor that has never
  seen a context cannot be compared with one that has, and a positive prediction
  elsewhere must not hide an unobserved alternative. The number of contexts is
  bounded by the state limit, so the forced-exploration burden is bounded too.

## Honest feedback signal

Lower training loss is not a strength signal, so the controller never reads
losses. It observes the **completed-game score against the selected evaluation
opponent**, balanced across attacker and defender roles by the existing
evaluation protocol. The opponent is normally a fixed, versioned benchmark; it may
also be the evolving Champion, in which case each promotion starts a new regime as
described below.

```
score = (wins + 0.5 * draws) / completed_games
```

* Only completed games count; **unfinished (truncated) games are excluded**, not
  scored as losses. This is deliberately different from the promotion score, which
  keeps truncated games in the denominator.
* An evaluation with no completed game yields no observation.
* The observation is only taken at an evaluation boundary. No extra evaluation is
  added, so the controller never multiplies evaluation cost.
* The credited action is the learning rate that was active for the interval that
  produced the observation; a change applies to the next iteration.
* `None`, `NaN` and `inf` are recorded as `missing_observation` and never advance
  warm-up, cadence or dwell counters.
* Sparse observations (a large `evaluation_frequency`) simply mean fewer
  decisions. Delayed credit is inherent and handled by attributing to the
  completed interval.

Configuration validation enforces the honest regime: adaptation requires at least
**4 evaluation games** so a score is informative, and the configured
`learning_rate` must be one of the candidates, so every observed interval is
attributable. The evaluation opponent may be a fixed benchmark **or the evolving
Champion**. Adaptation is rejected inside Optuna tuning, where the study itself
varies the learning rate.

### Champion evaluation and promotion boundaries

A fixed opponent keeps the controller's observations comparable for the whole
run. Champion does not: a promotion replaces the incumbent, so a score observed
before the promotion is not comparable with one observed after it. Treating them
as one series would make the controller react to the promotion itself rather than
to training progress.

The controller therefore consumes the explicit promotion signal produced by
`EvaluationSelection.accept()` (`evaluation["promoted"]`, `champion_iteration` and
the incumbent-suffixed `opponent_identity`), never a score threshold. On a
promotion:

* The promoting observation is still ingested and attributed to the outgoing
  Champion, but the decision is suppressed so the rate does not change at the
  boundary.
* The currently effective rate is preserved. The optimizer is not rebuilt, and
  its moments, the model weights and the replay buffer are untouched.
* The learned predictors, reward/action history, candidate counts and warm-up
  counters are discarded, and `adaptation_warmup` restarts against the new
  Champion. The next evaluation is the first observation of the new regime.
* A replayed promotion signal is an inert duplicate: it never ingests the score
  again, advances any counter, moves the controller RNG or records a decision
  event. This makes repeated processing of the same evaluation boundary safe.
* A failed attempt, a draw or a missing observation never resets the controller.

The controller keeps the same evaluation identity across promotions (the context
hash is unchanged). The boundary is carried by the promotion signal, and the
controller additionally persists the **active Champion regime identity** derived
from the Champion's persisted metadata (promotion iteration, provisional flag and
provenance). On resume the identity is compared with the restored
`EvaluationSelection` Champion: a match restores the learned state unchanged; a
mismatch (or a pre-v4 state that cannot prove which Champion it belongs to)
discards the observations while preserving the effective rate and the optimizer
state, with `reset_reason` `champion_changed` or `champion_identity_unknown`.
The active Champion is shown in monitoring as
`adaptation.active_champion_identity`; the current-regime observation count is
`adaptation.regime_observations` and `adaptation.warmup_complete` reports whether
warm-up has been satisfied, distinct from the lifetime `promotion_resets`.

With adaptation `off` the controller settings are inert: they never block a run,
so a user can disable the controller after an invalid edit and still start
training. They are still bounded and sanitized to finite, JSON-serializable
defaults, so an unsafe inactive value (non-finite number, oversized or
non-scalar structure) cannot fail a later checkpoint, export or remote
transport. Enabling it validates every supplied candidate strictly.

### Known signal limitations

* The fixed opponent can saturate near 1.0 (especially Shield bearer), after which
  the controller cannot distinguish candidates. This is a real limitation, not a
  bug: the score is an honest, comparable proxy, not a strength proof.
* With Champion, every promotion starts a new regime. The controller deliberately
  does not carry any learned state across the boundary, so it needs at least
  `adaptation_warmup` evaluations after each promotion before it may decide
  again. Frequent promotions therefore slow adaptation; that is the intended
  trade for never mixing incomparable scores.
* Evaluation scores from a small game count are noisy. `adaptation_min_dwell`
  and `adaptation_min_trials` bound how fast the controller reacts; a run that
  needs finer decisions should raise `evaluation_games`.
* The controller optimizes its proxy, not Elo or tournament strength. No run
  should be described as "stronger" from a lower loss or from a successful
  adaptation run alone.

## Safety, fallback and optimizer state

* The optimizer is never rebuilt. Only the parameter groups' learning rate is
  written, so
  Adam moments and SGD momentum are preserved.
* A selection failure keeps the current rate and records `skipped="fallback"`.
  The implementation does not claim to undo updates that already happened; there
  is no rollback of damaged updates.
* State is JSON-finite and bounded: `candidates ** (lag + 1)` contexts is the
  hard bound, capped at 512 and rejected at validation; the event log is bounded
  to 100 entries; history is bounded to 64 rewards.
* The controller runs in the training process (including remote workers), on the
  CPU, with a small pure-Python linear solve and a dedicated `random.Random`
  stream, so it never perturbs the training RNG sequence.

## Resume semantics

The controller state is stored in the existing tensor-only checkpoint under
`extra["adaptation_state"]` and carried by the existing artifact/resume
transport.

* Resume with the same algorithm, candidates, lag and evaluation identity
  restores continuity: counters, effective rate and learned predictors.
* A resume after a Champion promotion restores the post-promotion state: the
  promotion counter and record, the active Champion identity, the preserved
  effective rate and the restarted warm-up counters. The promotion is not
  re-applied on load.
* A resume whose active Champion identity differs from the restored Champion (or
  a pre-v4 state that carries no active-regime identity) resets the learned state
  with `champion_changed` or `champion_identity_unknown` while preserving the
  effective rate; the optimizer, model and replay are untouched.
* Changed candidate set, changed lag or changed evaluation identity resets the
  learned state with a visible `reset_reason` (`candidates_changed`,
  `lag_changed`, `evaluation_changed`); counters restart because the observations
  are incomparable.
* Enabling adaptation on a checkpoint that predates it starts fresh; earlier
  iterations are not invented as observations.
* Disabling adaptation on resume drops the controller state and keeps the
  checkpoint's last effective rate (an explicit `learning_rate` override still
  applies through the normal resume path).
* An explicit `learning_rate` override on resume is **honoured**, never silently
  discarded: the restored controller keeps its learned predictors and counters but
  moves its effective rate to the requested candidate and restarts its dwell timer.
  If the state was reset (for example by a Champion regime mismatch) the override
  still wins over the preserved rate. There is no pending feedback to reconcile
  because the checkpoint is written after each decision. The override is shown in
  monitoring as `adaptation.override_learning_rate`. An unchanged resume (the
  request carries the saved value) keeps the selected rate exactly.
* Old checkpoints without `adaptation_state` load unchanged with adaptation off.
* A checkpoint that predates the controller has no adaptation inputs. The resume
  form fills the shared defaults and derives candidates around the checkpoint's
  **effective optimizer learning rate** (not the global default), keeping any
  explicitly saved values untouched. An ordinary job whose inactive candidate
  list does not contain that rate is regenerated when the controller is enabled,
  so enabling it on resume cannot fail on a stale inactive default. A partial
  CLI/Python resume that enables adaptation is validated against the fully
  resolved checkpoint configuration, so inherited evaluation settings are
  honoured before the cross-field rules run.

## State validation and recovery

Restored controller state is validated atomically before any of it is adopted.
Every field is checked into locals first; if any check fails, only the controller
state resets (with an explicit `reset_reason`), and the model, optimizer, replay
and training history are never touched. Malformed state is therefore never
silently accepted, and it cannot leak into telemetry or a later checkpoint.

Validation covers the advertised `MAX_CONTEXTS` (512) bound, the `candidate:context`
key structure and legal action identifiers, matrix/vector dimensions, numeric
(not string) finite values, symmetry and positive definiteness of each ridge
system, history lengths, non-negative integer counters, the effective index, the
state version, the saved RNG stream, the active Champion identity, the bounded
promotion record (iteration, optional incumbent iteration and identity, and a
preserved rate drawn from the candidate set, consistent with the promotion
counter) and the bounded decision-event schema (accepted fields, finite in-range
numbers, candidate references, legal skip reasons, bounded provenance strings, no
unexpected nested data). The RNG is
validated **before** adoption: a malformed stream resets the controller with
`unreadable_state`, and a state without a saved stream resets with `rng_absent`,
so learned predictors are never adopted under a silently different exploration
sequence. Version 1 (the same learned contexts, the earlier global exploration
gate, and its own saved RNG), version 2 (per-context exploration without the
promotion record) and version 3 (the promotion record without the active-Champion
identity) are migrated in place for fixed-opponent runs; a version-3 Champion run
is conservatively reset with `champion_identity_unknown` because its history
cannot be proven to belong to the restored Champion. Unknown versions reset with
`unsupported_state`. Accepted telemetry is JSON-serializable. Loading goes through
the existing tensor-only checkpoint loader; no arbitrary objects are
deserialized.

## Remote workers

The controller executes inside the training process, so it works identically on a
remote worker. Workers advertise a `hyper_adaptation` capability; a job with
adaptation enabled is never offered to a worker that lacks it, and an
administrator version override cannot bypass that gate. A Champion-adapted job
additionally requires the narrower `hyper_adaptation_champion` capability, so a
worker running an older fixed-opponent-only controller is never offered one;
fixed-opponent adapted jobs remain compatible with those workers. The rejection
names the missing capability (for example, automatic hyperparameter updates, or
those updates with a Champion opponent) instead of blaming the inference backend
or device. A transport copy sent to a legacy worker omits the adaptation keys, so
the old binary cannot silently ignore an enabled controller.

## Differences between the paper, the reference code and this implementation

The reference repository is a research artifact (its README notes it is still
being validated). This implementation is intentionally conservative.

* **Single hyperparameter.** The paper supports `h` hyperparameters; this adapts
  only the learning rate.
* **Discrete candidates.** The paper discretizes a continuous space into `d`
  values; here the user supplies the finite candidate set directly.
* **Per-context exploration.** The paper's Algorithm 1 uses a uniform random
  warm-up for the first `s` steps and is greedy afterwards; the reference's
  uncertainty/perturbation term is present but commented out
  (`self.perturbation[a] = 0`). This implementation deliberately generalizes the
  warm-up into bounded, ongoing **per-context** coverage
  (`adaptation_min_trials`), because predictions are conditioned on the
  action-history context: a global count can finish before a context's predictor
  receives any observation, and an observed positive prediction would then hide
  an unobserved alternative. Counts advance only when an observation actually
  updated a predictor (`learned`), never during warm-up. This is a documented
  deviation, not an exact reproduction of the published algorithm.
* **Reference indexing.** In the reference `learn`, the window slice
  `self.actions[t-s-1:t]` has length `s`, while the guard is
  `if len(a_encoder) > self.s`, which is false for every `s`, so the update block
  is not reached as written. This implementation uses a clean, documented
  formulation instead of reproducing that off-by-one.
* **Signal.** The reference uses the environment reward each step. Here the
  reward is the completed-game evaluation score at evaluation boundaries, which
  is sparse, delayed and saturating.
* **State.** The reference keeps tensors on a device chosen from
  `torch.backends.cuda.is_built()`. This implementation is CPU-only, JSON-native
  and never infers GPU availability from a PyTorch build.

## Runtime and memory cost

* Per decision: a handful of `s x s` solves for `s <= 3`, plus appending one
  event. Measured as `performance.adaptation_seconds`, separate from
  `evaluation_seconds`, so controller overhead is not confused with extra
  evaluation cost.
* State: at most 512 contexts, each `s*s + 2s + 1` floats. The default
  (3 candidates, lag 1) is at most 9 contexts.
* No extra evaluation, no extra model, no extra process, no new dependency.

## Evidence that would change this recommendation

* A reproducible equal-budget benchmark showing a simple scheduler or a fixed
  learning rate matches or beats HyperController on a strength metric (not loss)
  across multiple seeds.
* Evidence that the fixed-opponent score saturates or is too noisy to drive
  decisions at practical evaluation budgets.
* A correctness or feasibility issue in the controller, or a materially cheaper
  algorithm with the same single-trajectory property.

## Reproducible benchmark instructions

The measured controller overhead on a tiny CPU smoke budget is recorded in the
[online adaptation benchmark](../benchmarks/online-hyperparameter-adaptation.md)
(~0.05 ms per decision, negligible against the evaluation it reads).

The controller only adds a bounded decision at evaluation boundaries, so compare
equal-budget runs and report the controller overhead separately:

1. Run the same configuration three ways with identical seeds, iterations and
   evaluation budget: fixed `learning_rate`, an inexpensive two-phase step
   scheduler, and `adaptation="hypercontroller"`. The shipped benchmark
   (`scripts/benchmark_hyper_adaptation.py`) does this; the scheduler is realised
   through the existing resume path and is not a training option.
2. Keep `evaluation_opponent`, `evaluation_games` and `evaluation_iterations`
   identical; otherwise the scores are not comparable.
3. Report wall time, cumulative optimizer updates, **cumulative** evaluation
   games (distinct from the final evaluation's game count), the effective
   learning-rate trajectory (the per-iteration `learning_rate` history field) and
   any failures. Separate `performance.adaptation_seconds` from
   `performance.evaluation_seconds`.
4. Keep the controller's feedback evaluation separate from a held-out strength
   evaluation. The shipped smoke benchmark does **not** run a held-out strength
   evaluation and reports that explicitly.
5. Label smoke runs as smoke tests. Only a multi-seed, adequate-budget run can
   support a strength statement, and even then only about the measured protocol.
