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
losses. It observes the **completed-game score against one fixed, versioned
opponent**, balanced across attacker and defender roles by the existing
evaluation protocol:

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

Configuration validation enforces the honest regime: adaptation requires a
**fixed, non-Champion opponent** and at least **4 evaluation games** so a score is
informative. The configured `learning_rate` must be one of the candidates, so
every observed interval is attributable. Adaptation is rejected inside Optuna
tuning, where the study itself varies the learning rate.

### Known signal limitations

* The fixed opponent can saturate near 1.0 (especially Shield bearer), after which
  the controller cannot distinguish candidates. This is a real limitation, not a
  bug: the score is an honest, comparable proxy, not a strength proof.
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
* Changed candidate set, changed lag or changed evaluation identity resets the
  learned state with a visible `reset_reason` (`candidates_changed`,
  `lag_changed`, `evaluation_changed`); counters restart because the observations
  are incomparable.
* Enabling adaptation on a checkpoint that predates it starts fresh; earlier
  iterations are not invented as observations.
* Disabling adaptation on resume drops the controller state and keeps the
  checkpoint's last effective rate (an explicit `learning_rate` override still
  applies through the normal resume path).
* Old checkpoints without `adaptation_state` load unchanged with adaptation off.

## State validation and recovery

Restored controller state is validated atomically before any of it is adopted.
Every field is checked into locals first; if any check fails, only the controller
state resets (with an explicit `reset_reason`), and the model, optimizer, replay
and training history are never touched. Malformed state is therefore never
silently accepted, and it cannot leak into telemetry or a later checkpoint.

Validation covers the advertised `MAX_CONTEXTS` (512) bound, the `candidate:context`
key structure and legal action identifiers, matrix/vector dimensions, finite
values, symmetry and positive definiteness of each ridge system, history lengths,
non-negative integer counters, the effective index, event types, and the state
version. Version 1 (the same learned contexts, the earlier global exploration
gate) is migrated in place; unknown versions reset with `unsupported_state`.
Loading goes through the existing tensor-only checkpoint loader; no arbitrary
objects are deserialized.

## Remote workers

The controller executes inside the training process, so it works identically on a
remote worker. Workers advertise a `hyper_adaptation` capability; a job with
adaptation enabled is never offered to a worker that lacks it, and an
administrator version override cannot bypass that gate. A transport copy sent to
a legacy worker omits the adaptation keys, so the old binary cannot silently
ignore an enabled controller.

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
