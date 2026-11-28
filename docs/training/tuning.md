# Hyperparameter tuning

[Training index](README.md) · [Parameters](parameters.md) · [Interface](interface.md) · [Benchmark report](../evaluation/benchmarks/training/tuning-bookkeeping.md)

Optuna tuning runs independent full trials and stores them in the job's own study
database. It is not the optional online learning-rate controller; see
[online adaptation](online-adaptation.md).

## Tuning workflow

```sh
.venv/bin/python -m hnefatafl.cli tune --config configs/tune-smoke.json
```

Optuna uses a persisted SQLite study, seeded TPE proposals and a median pruner.
Each trial performs real self-play and optimizer updates. Its objective is the
**best newly evaluated candidate benchmark** from that invocation's additional
training. The inherited source benchmark is a separate reference, even if it is
higher than every candidate. The objective score and `objective.pt` always refer
to the same newly trained model. Ordinary training still retains its overall
best benchmark in `best.pt`, including an inherited best when appropriate.

For a single opponent, the candidate best-so-far drives median pruning. With a
weighted objective, pruning by the different in-training benchmark is disabled;
the selected new candidate is evaluated against every fixed weighted opponent.
`sampler.n_startup_trials` defaults to 3; the median pruner defaults to 3 startup
trials, 1 warmup step and an interval of 1. TPE also accepts `multivariate`,
`group` and `constant_liar`.

`trials` requests **additional** trials, up to 10000 total per study. Storage and
`study_name` identify the owning database. The sampler seed is separate from
`training.seed` (default 0). Fresh trials use `training.seed + trial.number`;
checkpoint trials restore the source seed and RNG state. Evaluation uses the
same fixed protocol seed across trials, independent of the per-trial model seed.
Wall-clock search deadlines and numerical backends still limit bitwise
reproducibility across hardware or under different system load.

### Selecting the evaluation opponent

The nested `training.evaluation_opponent` is selectable among **fixed**
references: `shield_bearer`, `shield_maiden` and `jarl` (with
`evaluation_jarl_depth` and `evaluation_jarl_time_limit`). The evolving
`champion` is rejected for tuning, because its reference moves between trials and
its scores are not comparable; a fixed neural opponent is selected instead
through the `opponents` list (see below). Evaluation protocol settings themselves
cannot be tuned, only the model hyperparameters.

The source checkpoint's historical benchmark, best newly evaluated candidate,
overall retained best, and final iteration are separate metrics. A candidate
that scores 0.25 and one that scores 0.5 receive distinct objectives even when
the source benchmark is 1.0. The source checkpoint is never rewritten. A trial
without a newly evaluated candidate fails rather than attributing a candidate
score to inherited weights.

A fixed reference opponent may be supplied directly to `tune` (for example with
`resume`):

```json
{
  "trials": 8,
  "study_name": "brandubh-search",
  "seed": 0,
  "search_space": {
    "learning_rate": {"type": "float", "low": 0.0001, "high": 0.003, "log": true},
    "mcts_iterations": {"type": "int", "low": 4, "high": 16, "step": 4}
  },
  "training": {
    "preset": "recommended",
    "ruleset": "brandubh",
    "evaluation_opponent": "jarl",
    "evaluation_jarl_depth": 3,
    "iterations": 15,
    "resume": "/absolute/path/to/source/last.pt"
  },
  "verification": {"games": 16, "seed": 4242, "opponent": "shield_bearer"}
}
```

The dashboard's **Tune this checkpoint** action opens a review dialog for a
completed, cancelled or otherwise resumable training job. It reads the
checkpoint's completed iteration from validated backend metadata and lets the
user set **Additional tuning iterations per trial**; every trial targets
`completed_iteration + additional_iterations` (at most 100000). The source
checkpoint is never modified, and the request is rejected before any trial if the
target does not exceed the checkpoint or if the search space samples `channels`
or `blocks`, which the checkpoint fixes. Checkpoint-based tuning uses a portable source token; the server resolves and
validates it, and the CLI uses the nested `training.resume` path. Study identity,
source generation and migration rules are developer details in
[training data](../development/training-data.md#tuning-study-identity).

### Weighted multi-opponent objective

An optional top-level `opponents` list scores the winning checkpoint of every
trial against several fixed references and optimizes their weighted mean:

```json
"opponents": [
  {"opponent": "shield_bearer", "weight": 0.5},
  {"opponent": "shield_maiden", "weight": 0.25},
  {"opponent": "jarl", "weight": 0.25}
]
```

Weights must be greater than 0 and at most 1; the aggregate is normalized by the
sum of weights. This **is the Optuna objective** when configured: it re-evaluates
the best new benchmark candidate of each trial, so it costs extra games. Only fixed references are accepted, because
the evolving Champion needs an incumbent that does not exist outside a trial.
When `opponents` is absent, the objective is the best newly evaluated candidate benchmark
score of `training.evaluation_opponent`.

A **frozen neural opponent** is a registered model pinned for the whole study:

```json
"opponents": [{"opponent": "frozen_model", "model_id": "<24-hex registry id>", "weight": 1}]
```

The model is loaded once before the first trial, checked against the study's
ruleset and board/action representation, and used as the same opponent for every
trial. Hidden channels and residual blocks may differ, including during
architecture search. Each model has separate inference queues/caches/backends;
sequential, batched and supported parallel MCTS evaluation use the same protocol. A
missing or incompatible model fails the study before any trial runs, and the
model cannot be deleted while a running study references it.

### Held-out verification

An optional top-level `verification` object re-evaluates the study's winning
checkpoint with a seed the trials never used:

```json
"verification": {"games": 16, "seed": 4242, "opponent": "shield_bearer"}
```

`games` must be even, `seed` is an unsigned 32-bit integer, and `opponent` is a
fixed reference. The result is persisted as `verification_result` in
`result.json` (opponent, seed, games, score, wins/losses/draws/truncated), so a
strong aggregate score can be checked independently instead of only re-read from
the study objective. Verification runs after the optimization result is written
with `best-config.json` and winning checkpoint references already durable.
`verification_status` is `not_requested`, `pending`, `running`, `completed`,
`failed`, `cancelled` or `skipped_no_winner`. If verification was requested but no
trial completed successfully, it is skipped without loading a checkpoint; the
optimization remains failed and trial errors remain available. Errors from an
attempted verification are retained separately in `verification_error`;
a failed/cancelled verification leaves completed optimization results usable.

### Failure handling

A trial that raises is recorded as `FAIL` and the study continues; `max_failures`
(default 3, at most `trials`) consecutive failures stop the study before the whole
budget is wasted. Failed trials carry their error string and are never counted as
completed. A study with **no completed trial** ends as `failed` with an error
instead of a completed study with a winner, while a study that already has
completed trials keeps them even if later trials fail. Cancellation preserves its
`cancelled` status. The job never stays running after an exception, and the
heartbeat keeps running through finalization and verification.

The JSON `search_space` supports `float` (with optional `log`), `int` (with
optional `step`/`log`) and `categorical` (`choices`) distributions. A `float`
distribution accepts only `type`, `low`, `high` and `log`; an `int` distribution
also accepts a positive integer `step`, and `log` requires `step` 1; a
`categorical` distribution accepts only `type` and `choices` with non-empty
numeric choices. Logarithmic ranges require positive bounds. Every sampled extreme
is checked against the training validator, so an accepted search space can always
produce a valid trial. The nested `training` object supplies the common training
configuration. Each trial owns `last.pt` (full final state), `objective.pt` (full
state of its best new evaluated candidate) and `best.pt` (overall selected best).
Full checkpoints retain optimizer state, replay, RNG and learning history.
Trials do **not** register playable inference models, even with `save_all_best`.
Pruned/failed trials retain any full checkpoint they reached. Continuing the
winner is ordinary training in a new job, which retains normal registration.
No trial model is registered automatically at study completion;

`best-config.json` persists the winning full training configuration, while
`result.json` lists every trial's state, best/final scores, best iteration,
parameters, aggregate scores and error, plus the sampler/source seeds and the
verification result. A tiny study verifies plumbing; strength tuning needs
substantially larger evaluation budgets and multiple independent seeds.

A running study reports study-level phases (`initializing_study`,
`preparing_trial`, `between_trials`, `finalizing_study`, `verification`) with a monotonic study
duration, the completed-trial counter and the last trial's data, so a dashboard
never shows a dead worker between trials or during a slow finalization. The
study's heartbeat stops only at the terminal publication, which carries the
authoritative duration; a study failure is published as a terminal failed state.

Exactly one publisher owns the job's files. While a trial trains, that trial's own
monitor owns them and the study's heartbeat is suppressed; the handover in both
directions happens under the publication lock, so neither side can overwrite the
other's snapshot with a stale stage. After a trial finishes, ownership returns to
the study and the `between_trials` phase is published before anything that may
block — parameter sampling, the Optuna bookkeeping that stores the trial's final
state, and the aggregation that reads the study's trials. A slow storage operation
therefore never leaves the job without a heartbeat.

The completed-trial counter follows Optuna's own record. The study counts a trial
from Optuna's trial callback, which runs once that trial's state is stored, so a
trial that trained but then failed — a missing evaluation, an attribute write that
raises — is recorded as `FAIL` and never counted, and pruned, cancelled and failed
trials are never counted at all. The study's history is read once when it is loaded
and once when its final result is aggregated, so the bookkeeping stays O(1) per
trial instead of re-reading every stored trial after each one; a resumed study
starts from the completed trials Optuna has already stored.

Bookkeeping cost and its measurements are in the
[tuning bookkeeping benchmark](../evaluation/benchmarks/training/tuning-bookkeeping.md).
