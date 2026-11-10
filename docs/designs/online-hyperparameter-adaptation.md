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
* `adaptation_min_dwell_iterations` — optional training iterations a chosen rate
  is kept before it may change; 0 disables the extra gate.
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
also be the evolving Champion. A fixed opponent keeps the historical absolute
score; an evolving Champion uses genuine within-Champion change against the
observed baseline.

```
# fixed opponent (absolute, historical representation)
reward = (wins + 0.5 * draws) / completed_games

# evolving Champion (genuine change from the observed baseline)
raw = (wins + 0.5 * draws) / completed_games
reward = clamp(raw - baseline_score, -1, 1)
```

* Only completed games count; **unfinished (truncated) games are excluded**, not
  scored as losses. This is deliberately different from the promotion score, which
  keeps truncated games in the denominator.
* An evaluation with no completed game yields no observation.
* For a Champion, the first evaluation sets the observed `baseline_score` and is
  a baseline only: it is counted but never fed to the predictor and never triggers
  a decision. For a fixed opponent there is no baseline and the score is the
  reward directly.
* The reward is genuine measured change: two identical scores against the same
  Champion yield exactly zero improvement, a higher score is positive and a lower
  score is negative. The regularized baseline uncertainty (`baseline_se`) is kept
  separately and only widens the decision margin; it never enters the reward, so a
  neutral-score prior cannot manufacture progress.
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
run. Champion does not: a promotion replaces the incumbent, so a raw score
observed before the promotion is not comparable with one observed after it.
Treating them as one series would make the controller react to the promotion
itself rather than to training progress, and discarding all learned state at
every promotion would throw away valid persistent learning.

The controller therefore separates two kinds of state:

* **Persistent global state** — the candidate set, effective rate, learned
  predictors, reward/action history, lifetime counters, dwell timers and the
  dedicated RNG stream. It describes the one continuing trajectory and is never
discarded at a promotion.
* **Per-Champion evaluation state** — the Champion identity, promotion
  iteration, evaluation protocol, the local baseline, raw-score telemetry and the
  shrunk calibration offset and uncertainty. It is refreshed when a new Champion
  is installed.

The controller consumes the explicit promotion signal produced by
`EvaluationSelection.accept()` (`evaluation["promoted"]`, `champion_iteration` and
the incumbent-suffixed `opponent_identity`), never a score threshold. A promotion
is an **idempotent transition**, not a reset:

* The promoting observation is still ingested and attributed to the outgoing
  Champion, but the decision is suppressed so the rate does not change at the
  boundary.
* The learned predictors, reward/action history and lifetime counters are kept.
  Cross-Champion transfer is deliberate: the observations are expressed as
  within-Champion improvement, so a predictor learned on one incumbent remains
  meaningful on the next.
* The currently effective rate is preserved. The optimizer is not rebuilt, and
  its moments, the model weights and the replay buffer are untouched.
* The dwell timers (`adaptation_min_dwell` and
  `adaptation_min_dwell_iterations`) and the warm-up counter continue across the
  boundary; a promotion cannot unlock an immediate change.
* Only the per-Champion evaluation block is refreshed, so the next evaluation
  establishes the new Champion's local baseline.
* A replayed promotion signal is an inert duplicate: it never ingests the score
  again, advances any counter, moves the controller RNG or records a decision
  event. Promotion iterations strictly increase, so any event at or before the
  most recently applied promotion is rejected as duplicate or stale, even after
  later promotions; a genuinely later promotion is still applied. This makes
  repeated processing of any evaluation boundary safe.
* A failed attempt, a draw or a missing observation never refreshes the regime.

### Comparing scores across Champions

Raw win rates against different Champions are never compared. Only an evolving
Champion uses baseline-relative feedback; a **fixed opponent keeps the historical
absolute score**, because it is already comparable for the whole run. The two
representations are stored separately (`reward_semantics`) and are never mixed in
one predictor.

For a Champion, every observation after the first is the **within-Champion
improvement** `clamp(score - baseline_score, -1, 1)`. The first evaluation sets
the observed baseline and is never fed to the predictor, so an uncalibrated score
cannot inject a spurious reward. The reward is genuine change: identical scores
yield exactly zero, so a neutral-score prior or the uncertainty regularization can
never manufacture progress. Because one evaluation is a noisy reference, the
**regularized baseline uncertainty** (`baseline_se`) is kept separately and only
widens the decision margin; it is never subtracted from the reward. Predictor
updates
are **weighted by completed games** (capped), and the candidate comparison is
**confidence-aware**: a different candidate is only adopted when its predicted
improvement exceeds the current rate's by at least one standard error of the
Champion's mean improvement (`HYSTERESIS`). Marginal, noisy differences therefore
keep the current rate (`skipped="uncertain"`), and a baseline-only evaluation is
never a decision point (`skipped="baseline"`).

#### Regularized evaluation uncertainty

The decision uncertainty is a per-evaluation sampling error, not the variance
between successive scores. The per-game score is 1 for a win, 0.5 for a draw and
0 for a loss, so its variance is computed exactly from the outcome counts, and a
weak prior variance (0.25 with a pseudo-count of one game) keeps a degenerate
sample -- all wins or all draws -- from being treated as certain:

```
se_score = sqrt( ((n * var_per_game) + 0.25) / (n + 1) / n )
```

The Champion's uncertainty combines each improvement's `se_score` with the
once-shared baseline error in quadrature:

```
uncertainty = sqrt( baseline_se^2 + sum(se_score_i^2) / count^2 )
```

so two identical four-game evaluations keep a meaningful uncertainty instead of
collapsing to a numerical floor, while the same result over hundreds of games is
more certain. This is a lightweight per-evaluation estimate, not a confidence
interval for playing strength: the protocol's paired, side-swapped games are
correlated, and the weak prior plus the fixed margin acknowledge that rather than
model it exactly. The baseline error and the candidate-prediction error are kept
conceptually separate, and the game count is not double-counted (it weights the
predictor update and, independently, the sampling error of the decision).

#### Conservative, explicit exploration

`adaptation_min_trials` forces every `(candidate, context)` pair to be observed,
but an exploratory change is not an evidence-based improvement and must not bypass
the safety gates. In Champion mode **every** automatic rate change -- exploration
and exploitation alike -- requires at least `EXPLORATION_MIN_EVIDENCE` (2)
comparable post-baseline observations, so a freshly promoted Champion cannot move
the rate even when the shared predictor strongly favours another candidate. The
first post-promotion evaluation only establishes the baseline; the first
comparable evaluation is blocked with `skipped="insufficient_evidence"`, and the
second may act.

An exploratory change additionally requires the ordinary observation-based and
iteration-based dwell and a stricter exploration cooldown (`EXPLORATION_DWELL`, 2
observations) since the last exploratory change, tracked separately from the
ordinary dwell. When there is too little reward history to form a context window,
a Champion no longer explores at random: it picks the least-observed candidate,
with the multiplicative (log) distance to the current rate as the tie-breaker.
When a context exists, the multiplicatively nearest untried candidate is chosen.
Both paths are deterministic (ties break toward the lower index, so no RNG is
needed) and avoid jumping between extreme rates. Candidate-list position is not
rate distance: adjacent candidates can still span orders of magnitude, so the
distance is `|ln(rate_a) - ln(rate_b)|`. No hard multiplicative cap is imposed,
so a far candidate remains reachable when no closer one is untried.

A promotion preserves the cooldown reference and starts the new Champion with no
comparable evidence, so it never restarts exploration. Fixed-opponent selection
keeps the historical random coverage and greedy exploitation. The decision event
records `exploratory` so the log presents a coverage pull as candidate
exploration rather than a confirmed improvement.

Each Champion also keeps a **calibration offset**, the mean improvement shrunk
toward the cross-Champion prior. A constant offset cannot change the candidate
ranking, so it is retained as diagnostic telemetry rather than as a selection
signal; the uncertainty is what drives the confidence margin. The prior is also
updated per observation and is explicitly diagnostic. The explicit calibration
state (`awaiting_evaluation`, `baseline_established`, `collecting`,
`evidence_available`) is published for monitoring so the UI never reports a
baseline or a certainty that does not exist yet. When older progress lacks the
field, the UI infers the state only from a reported baseline and improvement
count and otherwise shows "Calibration status unavailable" rather than claiming a
baseline.

The controller keeps the same evaluation identity across promotions (the context
hash is unchanged). The boundary is carried by the promotion signal, and the
controller additionally persists the **active Champion regime identity** derived
from the Champion's persisted metadata (promotion iteration, provisional flag and
provenance). On resume the identity is compared with the restored
`EvaluationSelection` Champion: a match restores the learned state unchanged; a
mismatch refreshes only the per-Champion block, preserving the shared predictors,
the effective rate and the optimizer state, with `reset_reason`
`champion_changed`. A state whose reward representation differs from the current
job discards only the learned predictors with `reset_reason`
`reward_semantics_changed`; the effective rate, optimizer state, lifetime
counters, dwell timers and RNG are preserved. The corrected definition is
`raw_relative` (change from the observed baseline). It is compatible with version
5 (`raw_relative`), incompatible with an absolute-score fixed-opponent state and
incompatible with a version-6 Champion state, which measured change from a shrunk
baseline. The active
Champion is shown in monitoring as `adaptation.active_champion_identity`; the
current-regime observation count is `adaptation.regime_observations`, the lifetime
warm-up is `adaptation.observations >= adaptation.warmup`, and
`adaptation.transitions` (`promotion_resets` remains as a backward-compatible
alias) is the lifetime promotion count.

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
* With Champion, every promotion refreshes the new incumbent's local baseline
  while the shared predictors and dwell counters continue. Cross-Champion
  transfer means a new incumbent is not judged from scratch, but its first
  evaluation is still only a baseline and never a decision. Frequent promotions
  therefore no longer reset adaptation; the intended trade is that a very
  different incumbent may need a few observations before its uncertainty is small
  enough to justify a change.
* Evaluation scores from a small game count are noisy. The shrunk baseline, the
  game-count weighting, the confidence margin and `adaptation_min_dwell`,
  `adaptation_min_dwell_iterations` and `adaptation_min_trials` bound how fast
  the controller reacts; a run that needs finer decisions should raise
  `evaluation_games`. Baseline-relative improvement is a local proxy and is never
  an Elo or playing-strength estimate.
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
* A resume after a Champion promotion restores the post-transition state: the
  transition counter and record, the active Champion identity, the preserved
  effective rate and the continued dwell counters. The promotion is not
  re-applied on load.
* A resume whose active Champion identity differs from the restored Champion
  refreshes only the per-Champion evaluation block with `champion_changed`, while
  the shared predictors, the effective rate and the optimizer state are preserved.
* A resume whose saved reward representation differs from the current job
  discards only the learned predictors with `reward_semantics_changed`; the
  effective rate, optimizer state, lifetime
  counters, dwell timers and RNG are preserved, so incompatible rewards never
  reach a predictor. The current definition is compatible with version 5 and
  with a fixed-opponent absolute-score state; it discards an absolute state
  resumed into a Champion and a version-6 Champion state (shrunk baseline).
* A legacy state with no iteration metadata for its last change starts the
  conservative iteration dwell at the resume iteration.
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
transition record (iteration, optional incumbent iteration and identity, and a
preserved rate drawn from the candidate set, consistent with the transition
counter), the bounded per-Champion evaluation block (identity, promotion
iteration, protocol, observed baseline score and game count, regularized baseline
error, observation/improvement counters, finite improvement
statistics, the shrunk offset, the regularized uncertainty and its
`score_se_sum_sq` accumulator, and the bounded raw-score telemetry) and the
bounded cross-Champion prior, plus the bounded decision-event schema (accepted
fields, finite in-range numbers, candidate references, legal skip reasons, the
baseline/exploration/transition/margin/weight fields, bounded provenance strings,
no unexpected nested data). Version 7 additionally **requires** the complete
Champion block, the `reward_semantics` field, the serialized prior and a `weights`
history matching `rewards`; every weight must be within
`[1, MAX_GAME_WEIGHT]`, the baseline and score uncertainty accumulators must be
consistent with the improvement count (including the mathematical upper bound
`score_se_sum_sq <= count * MAX_SCORE_SE^2` and
`uncertainty == sqrt(baseline_se^2 + score_se_sum_sq / count^2)`), the exploration
marker must not exceed the lifetime observation count, a positive improvement
count must have a baseline, and a Champion protocol that disagrees
with the active evaluation is rejected. Missing or defaulted current-schema
metadata is a controlled controller-state recovery, never a silent default that
could change learning. The RNG is
validated **before** adoption: a malformed stream resets the controller with
`unreadable_state`, and a state without a saved stream resets with `rng_absent`,
so learned predictors are never adopted under a silently different exploration
sequence. Historical schemas are migrated with explicit reward-semantics rules:
versions 1-4 used absolute scores, so a fixed-opponent state keeps its predictors
and a Champion state discards them (`reward_semantics_changed`); version 5 used
improvement against the raw baseline for every opponent, so a fixed-opponent state
discards them while a Champion state is compatible (its uncertainty is
replaced with the conservative regularized estimate); version 6 used absolute
scores for a fixed opponent but a shrunk baseline for a Champion, so a fixed
state is compatible and a Champion state discards its predictors. A legacy state
with no
iteration metadata for its last change starts the conservative iteration dwell at
the resume iteration. Unknown versions reset with `unsupported_state`. Accepted
telemetry is JSON-serializable. Loading goes through the existing tensor-only
checkpoint loader; no arbitrary objects are deserialized.

## Remote workers

The controller executes inside the training process, so it works identically on a
remote worker. Workers advertise a `hyper_adaptation` capability; a job with
adaptation enabled is never offered to a worker that lacks it, and an
administrator version override cannot bypass that gate. A Champion-adapted job
additionally requires the narrower `hyper_adaptation_champion_transition`
capability, so a worker running an older fixed-opponent-only controller, or the
earlier reset-based Champion controller, is never offered one; fixed-opponent
adapted jobs remain compatible with those workers. Workers additionally advertise
`hyper_adaptation_state_versions`; a resumed adapted job carries the controller
schema version of its checkpoint, and a worker whose advertised list does not
include it is never offered the job, so an older worker cannot silently discard
learned state through its own migration. A worker without the list is assumed to
understand only the historical versions (1-4). A fresh job carries no saved state
and is not gated on a version, so a legacy worker can still run a new
fixed-opponent job and write its own state. Workers that understand the
iteration-based dwell advertise `hyper_adaptation_iteration_dwell`; a job with a
nonzero `adaptation_min_dwell_iterations` is rejected on a worker without it, and
the inert zero default is omitted from that worker's transport so it never sees an
unknown configuration key. The rejection names the
missing capability (for example, automatic hyperparameter updates, those updates
with a persistent Champion opponent, the iteration-based controller dwell, or the
controller state schema version) instead of blaming the inference
backend or device. A transport copy sent to a legacy worker omits the adaptation
keys, so the old binary cannot silently ignore an enabled controller.

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
(~0.05 ms per decision, negligible against the evaluation it reads). The synthetic
[Champion-adaptation benchmark](../benchmarks/champion-adaptation.md) compares a
fixed rate, the original destructive reset and the persistent transition under
frequent promotions; it measures controller CPU overhead, state size, rate churn
and evidence retention, and explicitly makes no strength claim.

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
