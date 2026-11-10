# Train, evaluate and play a neural opponent

[Documentation index](README.md) · [Performance](PERFORMANCE.md) · [Checkpoints and data](TRAINING_DATA.md)

The application implements self-play policy/value learning from scratch with
PyTorch and MCTS, plus resumable Optuna hyperparameter studies. No external game
dataset is required. Training runs in a separate worker process, so it does not
block the gameplay server. The browser Training page starts, monitors, cancels
and resumes jobs and lists models. The CLI provides the same compute workflow
without writing Python code or running a browser.

Use **Use parameters** on any recorded training job to prepare an editable new
run. Model cards retain **Load training parameters**. Neither action starts work
or resumes checkpoints/studies. See [configuration reuse and actual self-play
totals](TRAINING_DASHBOARD.md#recorded-self-play-totals) and
[counter metadata](TRAINING_DATA.md#recorded-self-play-provenance) for the difference
between a job's contribution and its model checkpoint's cumulative lineage.

<a id="separate-cuda-environment"></a>

## Installation and hardware

Install with `make install` from the repository root. CPU PyTorch is locked;
see [hardware and separate CUDA environments](PERFORMANCE.md#installation-and-hardware)
for device selection, GPU wheels and target-host validation limits.

## First session

```sh
.venv/bin/python -m hnefatafl.cli train --config configs/train-smoke.json
```

The smoke preset initializes a small network, generates legal self-play data,
performs real gradient updates, evaluates both sides against a heuristic,
saves/reloads checkpoints and registers an inference model. It is intentionally
tiny and demonstrates functional correctness, **not playing strength**. Its
12-ply generation cap often truncates games. Truncated self-play positions use a
detached current-network value bootstrap; they are marked `outcome: "truncated"`
and never mislabeled as rules draws.

For an actual first experiment:

```sh
.venv/bin/python -m hnefatafl.cli train --config configs/train-beginner.json
```

Change `ruleset` to `tablut` or `copenhagen` and use a new output directory for a
separate model. Each ruleset gets its own model because board dimensions, legal
escape squares and strategic targets differ. This avoids cross-ruleset policy
padding and incompatible imported weights. The versioned architecture leaves
room for a shared rules-aware model later.

## Training iterations and epochs

One **iteration** generates `games` self-play games, appends their positions to
bounded replay, then performs `epochs` passes over the available replay examples
in optimization batches of `batch_size`. An epoch is not another game or MCTS
simulation. The buffer retains recent positions up to `replay_buffer_size`;
training updates and periodic evaluation/checkpointing repeat until the target
**total** iteration count. Evaluation games do not become self-play training data.
Checkpoint/evaluation frequencies are measured in iterations.

## Architecture and targets

`tafl-policy-value-v1` is a residual convolutional network with GroupNorm, a
source/destination pairwise policy head and a bounded value head. It encodes six
planes: attackers, defenders, king, side to move, throne and restricted corners.
The policy index is `source * board_area + target`. Inference masks illegal
actions; optimization masks them when computing policy cross-entropy. The value
is always from the defender perspective, including at attacker nodes.

MCTS root visit distributions provide policy targets. A final defender win
provides +1, attacker win -1, and an actual rules draw 0. Replay samples retain
their legal action masks. Optimization combines policy cross-entropy and value
mean-squared error with configurable weight decay and gradient clipping. AdamW,
Adam, momentum SGD and Schedule-Free AdamW are supported. Checkpoints include model/optimizer state,
replay, Python/PyTorch RNG and CUDA/gradient-scaler state for resumption.

## Schedule-Free AdamW

Select **Schedule-Free AdamW** (`optimizer: "adamw_schedulefree"`) as a separate
optimizer. AdamW remains the default and all existing presets are unchanged.
The official [Schedule-Free algorithm](https://arxiv.org/abs/2405.15682) maintains
training parameters and averaged evaluation parameters, without a conventional
learning-rate decay scheduler. This is not ordinary AdamW with EMA weights.
Self-play targets, every evaluation opponent, Champion snapshots, selected best
models and playable exports use the averaged parameters. Only gradient updates
use the training sequence. GroupNorm has no running BatchNorm statistics to
recompute. Switching happens between completed synchronous inference phases,
before OpenVINO refresh or actor snapshots; it never runs in inference workers.

Install the training extra (`uv sync --frozen --extra train`). It includes the
maintained [schedulefree package](https://pypi.org/project/schedulefree/) with
constraint `>=1.4.1,<2`; the lock selects **1.4.1**, the current PyPI release checked
on 2026-11-09. The [official implementation](https://github.com/facebookresearch/schedule_free)
is Apache-2.0, distributed as a platform-independent Python wheel and requires
PyTorch and typing-extensions. Its declared Python minimum is 3.4; Hnefatafl
requires Python 3.11+ and PyTorch 2.6–<3. No compiled extension or reference
optimizer copy is added. API-only installations do not import schedulefree.
Missing training dependencies produce an explicit error, with no optimizer fallback.
Local API training and Optuna submissions execute the dependency probe before
creating a job or launching its subprocess, reporting a rejected optimizer field.
The probe runs once per Schedule-Free request, including tuning search-space
validation. Ordinary optimizers do not run it. Remote submissions rely on worker
capabilities and do not require schedulefree in the API server's environment.

```json
{
  "preset": "smoke",
  "optimizer": "adamw_schedulefree",
  "learning_rate": 0.001,
  "weight_decay": 0.0001,
  "schedulefree_warmup_steps": 100,
  "adaptation": "off"
}
```

`learning_rate` is the **configured base rate**, preserving the supplied starting
value and existing 1e-7–1 bound. Experiment with it; an AdamW setting need not be
optimal for Schedule-Free. The optimizer does not autonomously choose a base rate.
`schedulefree_warmup_steps` is an integer 0–1,000,000, default **0** (upstream's
default). A nonzero warm-up is recommended for experimentation; its useful length
depends on minibatches and total training duration. It counts actual successful
optimizer updates: approximately `epochs * ceil(replay_positions / batch_size)`
per iteration, excluding GradScaler-skipped steps. It is independent of a desired
100–200 iteration stable-learning phase and HyperController warm-up evaluations.
The other published defaults remain `betas=(0.9, 0.999)`, `eps=1e-8`, `r=0`, and
`weight_lr_power=2`. Release 1.4.1 has no inner momentum (equivalent to the newer
upstream `inner_momentum=0`); SGD's `momentum` has no effect on this optimizer.

Full checkpoints save evaluation weights with the complete upstream optimizer
state: `z`, second moments, `k`, `weight_sum`, `lr_max`, last `scheduled_lr`,
warm-up duration and saved mode. Resume restores these together with replay,
RNG/scaler, selection/Champion and history. The optimizer identity stays immutable.
Warm-up duration is read-only in the resume UI/API; the CLI rejects a duration
change after any successful update. Explicit base-rate and weight-decay overrides
change existing groups without resetting moments or averaging history.
Recoverable optimization errors and cancellation return to evaluation mode before
checkpointing. If upstream mode entry/exit itself raises, its in-place transition
may have switched only part of a parameter group. The adapter preserves the
original exception and diagnostics, marks that live optimizer/model pair unsafe,
and blocks new checkpoint writes and further mode/rate changes. Calling upstream
`eval()` cannot prove recovery when its group flag has not yet been committed.
The previous valid checkpoint stays intact; discard the unsafe live state and
resume into a fresh optimizer from that checkpoint. No new optimizer-state fields
or per-minibatch mode switches are added.

The dashboard and structured `optimizer` diagnostics in progress, metrics and
history show the name, base rate, last warm-up-adjusted rate (zero before the first
update), completed updates, warm-up duration and progress. Existing history
`learning_rate` and `effective_learning_rate` retain their base-rate/next-interval
meanings; the separate warm-up rate is neither a HyperController decision nor a
parameter-independent instantaneous update size. Diagnostics contain only a few
scalars, never per-minibatch histories or optimizer tensors.

CPU FP32, sequential/batched/parallel self-play, OpenVINO CPU inference and local
and remote resume are tested. CUDA AMP/GradScaler follows the existing path,
including clipping and skipped updates; a dedicated hardware-gated test is included.
CUDA and MPS hardware were unavailable for this change, so neither is claimed as
validated. There is no algorithm/device fallback. See the
[measured CPU overhead and checkpoint sizes](benchmarks/schedulefree-adamw.md).
For CUDA FP32/AMP, counter/skip, Champion, continuation, export and GPU memory
checks, use the [RTX 3060 validation procedure](SCHEDULEFREE_CUDA.md). Its commands
and conditional tests are provided for execution on a supported CUDA host; they
are not a claim of GPU validation in the CPU implementation environment.

Use `adaptation: "off"`. Combining this optimizer with HyperController is
explicitly rejected in new/resumed jobs, configuration import, tuning and remote
assignment. Normal Optuna studies can tune learning rate and weight decay with
separate optimizer instances and full checkpoints per trial. A future slow
supervisory controller can use `OptimizerLifecycle.base_learning_rate()` and
`set_base_learning_rate()` independently of selection/Champion logic. Such updates
preserve state, but must be tested with Schedule-Free's rate-history-dependent
averaging (`lr_max`, `weight_sum`). No two-timescale controller is implemented.

## Parameters and presets

Symmetry augmentation (`training_symmetry_mode`, default `"random_d4"` for new
jobs) is described in [Training symmetry augmentation](#training-symmetry-augmentation).
Jobs and checkpoints that predate the setting keep their historical `"off"`
behavior unless you set it explicitly.

The default is `recommended` for the chosen ruleset, not the raw base defaults.
Precedence is base settings → selected preset/ruleset plan → explicit JSON fields.
`beginner` uses the base budget; `balanced` supplies its own overrides. The UI
applies a selected preset to editable fields; review the final settings before
starting. See the complete [configuration bounds](#configuration-bounds).

| Parameter | Purpose |
| --- | --- |
| `preset` | `recommended` (default, ruleset-specific), `smoke`, `beginner` or `balanced`; explicit fields override it |
| `ruleset` | `brandubh`, `tablut`, `copenhagen` |
| `channels`, `blocks` | Residual architecture width (4–128) and depth (0–8) |
| `games`, `iterations`, `epochs` | Games per iteration, total iterations, optimization epochs |
| `batch_size`, `learning_rate`, `optimizer`, `momentum` | Optimization settings; momentum applies to SGD (0–1, default 0.9) |
| `schedulefree_warmup_steps` | Successful Schedule-Free optimizer updates over which to warm up; 0–1,000,000, default 0 |
| `mcts_iterations`, `mcts_time_limit`, `exploration` | Search simulation/time budget and PUCT constant; the time ceiling defaults to 10 seconds for new jobs |
| `self_play_mode` | `auto` (default): batched on CUDA, sequential otherwise; or explicitly `sequential`/`batched` on either CPU or CUDA |
| `self_play_concurrency` | Maximum independent active self-play games, 1–1024 (default 16), bounded by games remaining |
| `inference_batch_size` | Maximum positions per neural inference batch, 1–4096 (default 32); separate from optimization `batch_size` |
| `temperature`, `dirichlet_alpha`, `dirichlet_fraction` | Self-play action and root exploration |
| `replay_buffer_size`, `weight_decay` | Replay capacity and regularization |
| `checkpoint_frequency`, `evaluation_frequency` | Cadence measured in training iterations |
| `evaluation_opponent` | Exactly one of `shield_bearer` (default), `shield_maiden`, `jarl`, `champion` |
| `evaluation_games`, `evaluation_iterations`, `evaluation_max_plies` | Balanced evaluation budget; games must be even |
| `evaluation_time_limit` | Neural move deadline; also Shield bearer/Shield maiden deadline; defaults to the self-play deadline (10 seconds for new jobs) if omitted |
| `evaluation_jarl_depth`, `evaluation_jarl_time_limit` | Actual alpha-beta depth (default 5) and deadline (default 2 seconds) |
| `champion_confidence`, `champion_min_pairs`, `champion_min_improvement` | Conservative promotion evidence: default 0.95, 12 pairs, 0 margin above 50% |
| `max_game_plies` | Explicit self-play truncation limit |
| `seed`, `device`, `mixed_precision`, `cpu_threads` | Reproducibility and compute selection |
| `output_dir`, `registry_dir`, `model_name` | Persistent outputs and human-readable model name |
| `resume` | Optional safe full checkpoint path |
| `save_all_best` | Keep and register each strict best-score improvement (boolean, default false) |
| `adaptation` | `off` (default) or `hypercontroller`; optional online learning-rate adaptation described below |
| `adaptation_lr_candidates` | Comma-separated learning-rate candidates (2–8 distinct, each 1e-7–1); the configured `learning_rate` must be one of them |
| `adaptation_cadence`, `adaptation_warmup`, `adaptation_min_dwell` | Evaluations between decisions; evaluations before any decision; evaluations a chosen rate is kept |
| `adaptation_min_dwell_iterations` | Optional iterations a chosen rate is kept (0–1,000,000; 0 disables the extra gate); both dwell timers are preserved across a Champion promotion |
| `adaptation_lag`, `adaptation_min_trials` | Observation history window (1–3); per-context forced trials of each candidate (1–100) |

See [TRAINING_PLAN.md](TRAINING_PLAN.md) for the 7×7, 9×9 and 11×11 recommended
budgets, receptive fields, learning targets and comparison plan. The workshop
applies the matching plan when changing battlefield. Smoke remains an explicit
short software check; recommended runs are substantially longer experiments.

`balanced` increases model width/depth, games and search budgets. CPU/GPU costs
grow with games × plies × MCTS iterations, plus optimization/evaluation. Begin
with an affordable run and inspect timings before increasing budgets. Very
large replay buffers/checkpoints can exceed the enforced 128 MiB safe checkpoint
limit; reduce the buffer or model dimensions rather than bypassing validation.

### Parameter help in the forms

Every parameter in the workshop's new-job form and resume dialog has an
information button beside its label. Hovering the label or the button, focusing
the button with the keyboard, or tapping it reveals a description of what the
setting controls, which phase it affects (self-play, gradient training or
evaluation) and how it interacts with other settings. Clicking or tapping the
button keeps the description open until Escape or a press elsewhere dismisses it;
a description taller than the visual viewport becomes a focusable, scrollable
region so the whole text stays reachable by mouse, touch and keyboard instead of
being clipped.

One shared registry (`frontend/src/trainingTooltips.ts`) holds the wording, keyed
by the same parameter identity the API and the form controls use, so a parameter
that appears in both forms is described identically and a new control cannot be
added without copy:
`frontend/src/TrainingTooltips.test.tsx` enumerates the fields each form actually
renders — including conditional and advanced ones — and fails when one has no
description. The read-only checkpoint values in the resume summary use the same
affordance through `ReadOnlyFieldLabel`; the coverage test marks them with
`data-training-parameter` and checks that every immutable resume field from the
shared contract is represented, so an immutable parameter cannot be shown without
an explanation either. `frontend/tests/training-tooltips.spec.ts` drives the same
interactions in a real browser, where pointer and focus sequencing is real.

### CLI configurations and the HTTP API

`config.json` is shared by the CLI and the workshop export/import, but
`output_dir` and `registry_dir` belong to the CLI: the API assigns each job its
own output directory inside the server's job store and keeps registered models in
its own model directory. Posting those two fields to `POST /api/training/jobs`
(for example with a shipped `configs/*.json`) is refused with the exact field
names and an explanation rather than a generic error, and importing such a file in
the workshop reports them as ignored instead of sending them.

Every other field is accepted. A rejected field is always named, for both a
top-level and a nested tuning configuration (`training.<field>`), and the workshop
marks the matching input and keeps every entered value so one field can be
corrected instead of the whole form being rebuilt. `frontend/src/trainingContract.json`
records the accepted vocabulary; the backend and frontend test suites both assert
it, so a field added on one side alone fails a test instead of a user's launch.

### Configuration bounds

These are training validator/UI bounds, not suggested experimental budgets.
Recommended values differ by board; see [Training plans](TRAINING_PLAN.md).
Gameplay search has [different defaults and limits](AI.md#configuration).

| Fields | Accepted values |
| --- | --- |
| `channels`; `blocks` | Integers 4–128; 0–8 |
| `games`; `iterations`; `epochs` | Integers 1–10,000; 1–100,000; 1–100 |
| `batch_size`; `replay_buffer_size` | Integers 1–2,048; 1–20,000 |
| `mcts_iterations`; `evaluation_iterations` | Integers 1–100,000 |
| `max_game_plies`; `evaluation_max_plies` | Integers 2–5,000, counting individual moves |
| `checkpoint_frequency`; `evaluation_frequency` | Integers 1–10,000 iterations |
| `evaluation_games` | Even integer 2–10,000, alternating sides |
| `evaluation_opponent` | `shield_bearer`, `shield_maiden`, `jarl`, `champion` |
| `evaluation_time_limit`; `evaluation_jarl_time_limit`; `evaluation_jarl_depth` | 0.001–300 seconds; 0.001–300 seconds; integer 1–8 |
| `champion_confidence`; `champion_min_pairs`; `champion_min_improvement` | 0.8–0.9999; integer 2–5,000; 0–0.49 |
| `cpu_threads`; `self_play_concurrency`; `inference_batch_size` | Integers 1–64; 1–1,024; 1–4,096 |
| `learning_rate`; `mcts_time_limit` | 0.0000001–1; 0.001–300 seconds |
| `weight_decay`; `momentum`; `dirichlet_fraction` | 0–1 |
| `exploration`; `temperature`; `dirichlet_alpha` | 0–20; 0.01–10; 0.001–100 |
| `seed` | Integer 0–4,294,967,295 |
| `optimizer` | `adamw` (default), `adam`, `sgd`, `adamw_schedulefree` |
| `schedulefree_warmup_steps` | Integer 0–1,000,000 actual optimizer updates (default 0) |
| `device`; `self_play_mode` | `auto`/`cpu`/`cuda`/`cuda:N`/`mps`; `auto`/`sequential`/`batched` |
| `model_name`; `save_all_best` | 1–100 characters; boolean (default false) |
| `adaptation` | `off` (default) or `hypercontroller` |
| `adaptation_lr_candidates` | 2–8 distinct values, each 1e-7–1; the configured `learning_rate` must be present |
| `adaptation_cadence`; `adaptation_warmup`; `adaptation_min_dwell` | Integers 1–10,000; 0–10,000; 1–10,000 |
| `adaptation_min_dwell_iterations` | Integer 0–1,000,000 (0 disables the extra iteration gate) |
| `adaptation_lag`; `adaptation_min_trials` | Integer 1–3; integer 1–100 |

Numeric real-valued bounds require finite values. `mixed_precision` defaults to
true and takes effect on CUDA; `cpu_threads` defaults to two. JSON CLI paths are
explicit configuration; web jobs assign owned output/registry paths themselves.

### Concurrent self-play and performance comparisons

See [Performance](PERFORMANCE.md) for cooperative MCTS scheduling, execution modes,
optimization versus inference batches, metric units and reproducible benchmarks.
CPU measurements did not show a batching speedup; CUDA performance is unverified.

## Monitoring, cancellation and resumption

Training can also run on enrolled remote CPU/GPU workers, with simultaneous jobs
and the same saved progress/charts. See [REMOTE_TRAINING.md](REMOTE_TRAINING.md)
for the API, worker CLI, network setup and checkpoint/model transfers. Local
training keeps its original one-job limit and can run alongside remote jobs.

Workers emit compact incremental JSON-line logs and atomically update full `progress.json`. The file reports
stage, device, iteration, games generated, policy/value loss, update count,
replay positions, self-play outcomes, the selected opponent evaluation,
durations, and CUDA memory where available. Final `result.json` includes model
registry data and checkpoint locations. Inspect these files directly for a
headless run. The web UI polls the same persisted worker results. CLI stdout is
an event summary, including the final event; complete history stays in the JSON
files and checkpoint. Logs omit accumulated history, detailed game arrays and
model metadata. Remote workers use the same CLI and retain full polling data.

Between real progress events the worker keeps a bounded `heartbeat.json` beside
`progress.json` with the current phase, phase and iteration elapsed durations,
the time since meaningful progress and the live phase counters. It is refreshed
about once per second, so a long self-play game, optimization epoch or evaluation
match keeps monitoring and timers advancing without rewriting the retained
history. Readers overlay a heartbeat only when it belongs to the same publication
generation. See [the training dashboard](TRAINING_DASHBOARD.md#live-monitoring).

An Optuna study is monitored the same way, with the study's own heartbeat and
timers covering everything outside a trial: loading the study database, sampling
trial parameters, the bookkeeping between trials and the final aggregation. While
a trial trains, that trial's monitor publishes the same files, and the retained
snapshot keeps the last trial's data without presenting its finished phase or
timers as live. Each publisher carries its own run identity, so a heartbeat from
an earlier trial or from the study can never overlay the other's snapshot, and a
terminal state is never revived. The study's elapsed time spans the whole study,
so it never restarts with a trial. See
[hyperparameter optimization](#hyperparameter-optimization).

The progress panel plots policy/value losses and completion-aware evaluation
score against completed iterations, including while a worker is running. An
accessible data table contains the same points. History survives reopening and
resumption. Enable **Save every new best model** to keep earlier strict record
improvements as separately selectable models; ties do not create extra snapshots.

Send SIGINT or SIGTERM (or Cancel in the UI) to request a graceful stop. The worker
checks between moves and minibatches, writes a resumable `last.pt`, and marks
the job cancelled. Keep persistent storage; kill -9 cannot save the in-flight
minibatch, though previous atomic checkpoints remain.

For reviewed UI/API/CLI resume instructions, editable versus immutable parameters
and exact CPU resumption limits, see [checkpoint resumption](TRAINING_DATA.md#monitoring-cancellation-and-resumption).
Model-card resumption uses the source job's latest full checkpoint, not necessarily
the selected model's snapshot. All new-job settings except ruleset, channels,
blocks, seed, optimizer type and Schedule-Free warm-up duration are editable;
iterations is a target total.

## Evaluation and model selection

Choose one opponent in the **Evaluation** section of Start Training or Resume
Training. Resume starts from the latest full learning state; changing the
opponent does not reconstruct a model from inference weights or reset learning.
The Resume dialog preserves recorded preset metadata but offers no preset
selector. New jobs still support presets.

| Opponent | Useful stage | Selection rule |
| --- | --- | --- |
| Shield bearer | Early training; existing inexpensive heuristic | Strictly higher score in the same benchmark configuration |
| Shield maiden | Stronger rules-aware heuristic; low per-move overhead | Strictly higher score in its own versioned benchmark configuration |
| Jarl | Intermediate/advanced; alpha-beta search over the Shield maiden evaluation | Strictly higher score with the same depth/deadline and benchmark protocol |
| Champion | Advanced; saved compatible neural incumbent | Balanced paired head-to-head evidence, described below |

Jarl can be expensive: measure its evaluation duration on your hardware. Evaluation
never automatically adds a second baseline or previous-model match. Independent
benchmarks/tournaments remain available outside training.

Evaluation runs at `evaluation_frequency` iteration multiples and at the final
optimized iteration, independent of checkpoint writes. Skipped iterations have no
evaluation point. The last result remains available with its original iteration,
opponent and settings; it cannot trigger promotion again. A cancelled evaluation
preserves the incumbent and completed optimizer updates, so resumption continues
from those updates rather than evaluating unchanged weights redundantly.

Defaults preserve the existing cadence (every iteration), even game budget
(2 for base/smoke; recommended Brandubh/Tablut/Copenhagen: 12/16/20), neural
simulation budget (16 base, 2 smoke, 64/96/128 recommended) and move limits
(150 base, 12 smoke, 300/500/800 recommended). The neural deadline inherits
`mcts_time_limit` when omitted (10 seconds for new jobs); the UI exposes it
independently. Consider these
less frequent starting experiments, adjusting completion rates and cost:

| Opponent | Evaluate every | Games |
| --- | --- | --- |
| Shield bearer | 5 iterations | 12 |
| Shield maiden | 5 iterations | 12–24 |
| Jarl | 5–10 iterations | 12–24 |
| Champion | 10 iterations | 48–64 |

Example overrides for an existing Brandubh job, in addition to its saved settings:

```json
{
  "iterations": 200,
  "evaluation_opponent": "champion",
  "evaluation_frequency": 10,
  "evaluation_games": 48,
  "evaluation_iterations": 64,
  "evaluation_time_limit": 2.0,
  "evaluation_max_plies": 300,
  "champion_confidence": 0.95,
  "champion_min_pairs": 12,
  "champion_min_improvement": 0.0
}
```

See the [new Jarl job example](../configs/train-evaluation-jarl.json) and
[Champion resume example](../configs/train-evaluation-champion.json). The resume
example requires an existing full checkpoint at its configured path.

### Fixed benchmarks and distinct model states

The candidate is the network being optimized. `last.pt` is its full resumable
state. Each fixed benchmark has its own immutable best snapshot; equal scores
retain the earlier snapshot. The champion is a separate accepted incumbent.
`best.pt`/the final registered inference model contain the selected benchmark's
best snapshot, or the accepted/provisional champion in Champion mode.

Benchmark identity includes ruleset/version, opponent algorithm and effective
search settings, candidate budget, move/game limits, scoring and seeded pairing
protocol. Different identities never compete for a single best score. Changing
only evaluation/checkpoint frequency does not change comparison identity.
A first result in a new fixed benchmark establishes that benchmark's record;
it does not promote or replace an existing champion. Returning to a previous
configuration restores its record. Saturation (score at least 0.95) is flagged;
small samples remain noisy, and strict score improvements alone do not establish
statistical superiority over other neural checkpoints. Reported lower bounds
help expose uncertainty; confirm strength in held-out tournaments.

### Champion evidence and side advantage

The first Champion evaluation uses the previous context's saved best snapshot
(or historical baseline best) as provisional incumbent. A fresh Champion-only
job uses its initial weights provisionally. Initialization is not a promotion
and invents no historical matches. Existing champions survive switching away
and back. Candidate optimization continues whether promotion succeeds or fails.

Both models use equal neural budgets, no exploration noise and alternating
attacker/defender seats. Adjacent games share the same seed sequence and initial
position with swapped model assignments; different pairs use distinct seeds.
Fixed benchmarks reuse the seeded suite between candidates; Champion rounds use
fresh iteration-derived paired seeds. Full search completion is reproducible on
the same device; wall-clock cutoffs can change completed search work and results.

Promotion requires all games to finish, at least `champion_min_pairs` complete
pairs, and a one-sided Hoeffding lower bound strictly above
`0.5 + champion_min_improvement`. For pair scores in [0,1], the lower bound is
`mean_pair_score - sqrt(log(1 / (1 - confidence)) / (2 * pairs))`, clipped at zero.
Treating the pair as the observation accounts for paired side assignments rather
than pretending the individual games are independent evidence. At 95% confidence
and 48 games (24 pairs), the uncertainty subtraction is about 0.25: a 55% score
is inconclusive. This conservative per-comparison rule assumes independently
seeded pairs; it is not a multiple-testing or general-strength guarantee.

Wins earn 1, rules draws 0.5, and unfinished games 0 in the displayed
completion-aware score. Truncations are neither losses nor draws and block
champion promotion. Strong defender advantage can produce a 50% aggregate
without evidence of improvement; attacker/defender scores are shown separately.
Ties, losses, insufficient samples, failed and cancelled evaluations retain the
incumbent. Accepted weights are cloned and never share candidate storage.

### Reading workshop evaluation history

See the [training dashboard guide](TRAINING_DASHBOARD.md) for creation, job selection,
analysis tabs, responsive charts and model-collection management.

The workshop shows the active settings, latest training iteration, incumbent
iteration, last measured result, side scores, rules draws, truncated games,
duration and best fixed benchmark iterations. Curves are separate for each
benchmark identity and each Champion incumbent. Promotion events are shown in
the accessible graph table. Missing evaluations remain absent; old baseline
history stays readable in a separate historical series. Loss is fit to self-play
targets, not proof of improved play. No pretrained strong model is bundled.

## Checkpoints, registry, import and export

See [checkpoint types and model import/export](TRAINING_DATA.md#checkpoints-registry-import-and-export).
Only a full `last.pt` restores optimizer/replay/random state. Compact inference
models work with MCTS, alpha-beta or policy-only selection; see [AI](AI.md#neural-search-wrappers).

## Managing models and retained training data

[Model/job management](TRAINING_DATA.md#managing-models-and-retained-training-data)
covers rename, load settings and separate deletion actions. For installation moves,
read [migration and backup](TRAINING_DATA.md#moving-data-between-installations):
copying a job folder does not register it in the destination SQLite jobs table.

Shared Optuna studies can form cycles between database owners and trial jobs.
Administrators use **Delete entire tuning study** from an inactive member to
preview and confirm all participating jobs. Source training jobs, registered
models and saved games are kept; external checkpoint dependencies or live workers
block deletion. See the same management guide for staging, rollback and restart
recovery when filesystem cleanup is interrupted.

## Automatic hyperparameter updates

`adaptation` (default `off`) optionally adapts the **learning rate** online inside
one continuing job. `hypercontroller` is the only supported algorithm; it is a
small, bounded, CPU-only variant of
[HyperController](designs/online-hyperparameter-adaptation.md) and needs no
population, restart or extra process. It is not the same thing as Optuna: Optuna
searches across separate trials, while the controller adapts inside a single
trajectory. The two are mutually exclusive and Optuna rejects an enabled
controller. With adaptation `off` the controller fields are inert and never
block a run; they are sanitized to bounded, JSON-safe defaults so an unsafe
inactive value cannot fail a later write. Enabling the controller validates every
supplied candidate. Resuming a checkpoint that predates the controller fills the
shared defaults and derives candidates around the checkpoint's effective
optimizer learning rate; an ordinary job whose inactive candidates miss that rate
is regenerated when the controller is enabled.

While adaptation is enabled the controller owns the effective learning rate; the
configured `learning_rate` is the starting value and must appear in
`adaptation_lr_candidates`. The optimizer is never rebuilt, so Adam moments and
SGD momentum are preserved; only the parameter groups' learning rate is written,
at iteration boundaries after an evaluation. Validation requires at least 4
evaluation games, because the observation is the completed-game score. Unfinished
games are excluded from the observation rather than scored as losses, and no
extra evaluation is added.

The evaluation opponent may be fixed or `champion`. With a fixed opponent the
controller's observations are comparable for the whole run, so it keeps the
historical **absolute score** reward and its compatible predictors. With Champion,
a promotion replaces the incumbent and changes what a raw score means, so the
controller never compares raw win rates across Champions. It scores each
observation as the **within-Champion improvement** `score - baseline_score` over
the observed baseline; the first post-promotion
evaluation is a local baseline only, is never fed to the shared predictor and
never triggers a decision. The improvement is genuine measured change: identical
scores yield exactly zero, so no neutral-score prior or uncertainty regularization
can manufacture progress. Champion predictor updates are weighted by completed
games and a different candidate is only adopted when its predicted improvement
exceeds the current rate's by a confidence margin (one standard error of the
Champion's mean improvement); marginal differences keep the current rate. The
uncertainty is a regularized per-evaluation sampling error derived from the
win/draw/loss counts and the baseline error, kept separate from the reward, so
identical small evaluations keep a
meaningful uncertainty. **Every** automatic Champion rate change, exploitation
included, needs two comparable post-baseline observations, so a new Champion's
first comparable evaluation cannot move the rate. Untried candidate/context pairs
are still explored, but under a separate exploration cooldown and with the
multiplicatively nearest candidate preferred (log-rate distance, not candidate-list
position), so exploration never oscillates the rate or jumps between extremes; the
decision log marks it as candidate exploration. The
controller separates persistent global state (candidates, effective rate, learned
predictors, reward/action/weight history, lifetime counters, dwell timers and RNG)
from per-Champion evaluation state (identity, promotion iteration, baseline,
calibration offset, uncertainty and the explicit calibration state).

When `EvaluationSelection.accept()` reports a promotion, the promoting
observation is still attributed to the outgoing Champion, but it never changes
the rate. The promotion is an **idempotent transition**, not a reset: the learned
predictors, the reward/action history, the lifetime counters, the dwell timers
and the effective rate are all kept, and only the per-Champion evaluation block
is refreshed so the next evaluation establishes the new Champion's baseline. The
optimizer, its moments, the model weights and the replay buffer are untouched. A
replayed promotion signal is an inert duplicate: it never re-ingests the score,
advances a counter, moves the controller RNG or records a decision event, so
repeated processing of the same boundary is safe. Promotion iterations strictly
increase, so an event at or before the most recently applied promotion is
rejected as duplicate or stale even after later promotions, while a genuinely
later promotion is still applied. Failed attempts, draws and missing observations
never transition the controller. The controller also persists the active Champion
regime identity derived from the Champion's metadata. On resume it is compared
with the restored Champion; a mismatch refreshes only the per-Champion block while
preserving the shared predictors, the effective rate and the optimizer state
(`champion_changed`). A state whose reward representation differs from the
current job discards only the learned
predictors with `reward_semantics_changed`, preserving the effective rate,
optimizer state, lifetime counters, dwell timers and RNG. The current definition
(`raw_relative`) is compatible with version 5 and with a fixed-opponent
absolute-score state; it discards an absolute state resumed into a Champion and a
version-6 Champion state, whose reward used a shrunk baseline. A legacy state with no
iteration metadata for its last change starts the conservative iteration dwell at
the resume iteration. Monitoring exposes
`adaptation.active_champion_identity`, the current-regime
`adaptation.regime_observations`, the lifetime warm-up
(`adaptation.observations` against `adaptation.warmup`), the iterations since the
last rate change and the lifetime `adaptation.transitions` (`promotion_resets`
remains as an alias) and `adaptation.last_promotion` (iteration, beaten incumbent
iteration, identity and preserved rate); the Learning view shows the active
Champion, lifetime warm-up, Champion calibration progress and the rate kept at the
last promotion, and the decision log marks the boundary with a transition event
and shows the preserved rate as both the used and the selected rate. This
is distinguishable from the `reset_reason` values reported for a changed
configuration (`candidates_changed`, `lag_changed`, `evaluation_changed`) or an
invalid saved state (`unreadable_state`, `rng_absent`, `unsupported_state`).

The current effective rate and a bounded decision history are published in
monitoring (`progress.adaptation`, the per-iteration history fields and
`progress.adaptation_events`); the controller state is stored inside the full
checkpoint and survives resume. Changing the candidate set, the history window or
the evaluation settings resets the learned state with a visible reason instead of
mixing incomparable observations. An explicit `learning_rate` override on resume
is honoured by moving the controller's effective rate while keeping its learned
state (shown as `adaptation.override_learning_rate`); an unchanged resume keeps
the selected rate. Malformed saved controller state (including a malformed or
missing RNG stream) resets only the controller (with a visible reason) after an
atomic validation pass; the model, optimizer and history are never touched. The
full design record, honest signal limits, algorithm comparison and reproducible
benchmark instructions are in
[online hyperparameter adaptation](designs/online-hyperparameter-adaptation.md);
the measured controller overhead and baselines are in the
[smoke benchmark](benchmarks/online-hyperparameter-adaptation.md).

In the history, `learning_rate` is the rate the reported iteration actually
trained with, while `effective_learning_rate` is the rate selected at that
iteration's boundary for the next interval. The dashboard labels them “Used” and
“Selected”. Coverage is per action-history context, so every candidate is tried in
each context before that context's predictions are exploited; a new context is
always explored.

Adaptation is a research feature: a lower training loss is not evidence of
stronger play, and neither is a successful adaptation run by itself. Compare
equal-budget runs and report the measured protocol.

## Hyperparameter optimization

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
or `blocks`, which the checkpoint fixes. The HTTP API takes the source as a
portable `source_checkpoint` token (`<job_id>:last.pt`), resolves it server-side
and records it for archive export; the CLI uses the nested `training.resume`
path. The API also accepts `<job_id>:trial-N/objective.pt` for the full state of the
selected candidate, or `<job_id>:trial-N/last.pt` for the final trial state.

### Study identity and resumption

Identity version 2 records tuning mode; a stable content digest of source
weights, optimizer, replay and RNG/checkpoint state; source iteration; ruleset and
network architecture; all fixed effective training settings (including iterations,
games, epochs, replay size, optimization, numerical backend, concurrency and
search deadlines); model and sampler seeds; search space; evaluation protocol,
game budgets and seeds; immutable frozen-model content; opponent weights;
objective policy; and sampler/pruner settings. Machine-local paths and cosmetic
names are excluded. Source identities are cached in a generation-checked sidecar
when loaded for creation, never hashed during dashboard polls. Replacing a source
file invalidates the cache.

Both the API (before launching a subprocess) and the worker compare identities
and report conflicting fields. The additional trial/failure budgets, verification
request, model label and artifact publication cadence may change. Fixed trial
workload, including iterations per trial, cannot. Previous configuration and
Optuna history/result files are preserved on rejection. A nonblocking file lock
serializes local SQLite CLI invocations through finalization. Each trial checks
its loaded source generation/content before updating, so replaced/tampered source
state cannot silently enter an existing study. External RDB deployments must
serialize study invocations operationally; the server supports owned SQLite only.

Legacy studies are **never assigned the current requested experiment**. A missing
fingerprint can be reconstructed only when every historical trial independently
records the same complete version-2 identity. Version-1 fingerprints lack required
workload/content and use historical scoring semantics. Ambiguous older studies
remain inspectable/exportable with unchanged rankings; further trials require a
new study. Use the old winner's configuration or checkpoint to start that study.

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
[tuning bookkeeping benchmark](benchmarks/tuning-bookkeeping.md).

## Verification and troubleshooting

```sh
.venv/bin/python -m pytest tests/test_ai.py tests/test_models.py tests/test_training.py
```

Tests exercise legal baseline/alpha-beta/MCTS on all variants, tactical wins,
actual baseline and registered-model games to engine terminal outcomes, neural
updates on every board size, safe save/load/import, exact CPU resumption,
cancellation, CLI execution and an Optuna study with real trials and resumption.
The default suite requires no GPU or long training campaign. If CUDA is absent,
inspect `devices`, install the matching official GPU build and driver, or choose
CPU. An incompatible model error means the selected game's ruleset and model
must match. A job with many truncated outcomes needs a larger game-ply budget;
never reinterpret truncation as a rules draw or evidence of strength.

### Shield maiden evaluation

Use `"evaluation_opponent": "shield_maiden"` for new or resumed jobs. It runs
exactly one evaluation using the ordinary standalone algorithm. The shared
`evaluation_time_limit` bounds its decisions. Effective results retain the
algorithm, heuristic version, deadline, candidate budget, side scores and context
identity. Fixed-benchmark best checkpoints and chart series stay separate from
Shield bearer, Jarl and Champion; switching preserves weights, optimizer and replay.
Existing baseline-only model recommendations do not compare maiden scores with
baseline scores. Optuna uses whichever fixed reference the tuning configuration
selects (default Shield bearer); its objective is the trial's best evaluated
checkpoint.

Low decision overhead does not guarantee equal evaluation wall time: safer play
can extend games. Consult the [measured runtime trade-offs](SHIELD_MAIDEN.md).
Update remote workers to the same release before selecting the new opponent;
checkpoint format and portable job archives remain compatible.

## Optional OpenVINO inference

See [Optional Intel GPU self-play inference](OPENVINO.md) for opt-in setup, defaults, checkpoint portability and measured limitations.


## Optional exact-input inference reuse

Start Training and Resume Training expose **Inference cache**, default **Off**.
CLI JSON and local/remote jobs use `inference_cache_mode: "off" | "exact"` and
`inference_cache_capacity` (default 4096, integer 1–65536). Capacity counts positions,
not bytes; large boards and large capacities need more host memory. Exact mode
shares neural results for identical ruleset/board/turn inputs within an iteration
and removes identical requests from each pending batch. Each search keeps its own
tree, history, RNG, root noise and budgets. Rotated/reflected inputs remain distinct.

Caches are discarded before weight updates; backend refresh and parameter version
checks prevent reuse across generations. Terminal results bypass the cache.
This changes neither learning targets nor checkpoint format. Legacy configs and
resumes default off. Update remote workers to 0.24.0 to enable exact mode; older
workers can still claim off jobs (their transport omits unsupported preferences).
An exact job waits for a worker reporting `inference_cache_modes: ["off", "exact"]`.

Use the [performance protocol](PERFORMANCE.md#exact-input-cache-and-symmetry-foundation)
to compare throughput. Lower neural evaluation counts alone do not establish
faster training. New performance fields distinguish requests, distinct cache
lookups, eliminated batch duplicates and actual evaluated positions.

## Training symmetry augmentation

`training_symmetry_mode` is `"off"` or `"random_d4"`; new jobs default to
`"random_d4"`. Start Training and
Resume Training expose it as **Training symmetry augmentation** (German:
*Symmetrie-Datenaugmentation*): **Disabled** or **Random D4 rotations and reflections**.

With `random_d4`, every replay example placed in an optimizer minibatch receives
one independently drawn, uniformly distributed transform from the eight D4
elements (identity included), and is drawn afresh on each later exposure. The
encoded `[6, n, n]` input is permuted by the same square permutation on every
plane; each policy action `(source, target)` becomes `(T(source), T(target))` and
keeps its MCTS probability; the legal mask is mapped elementwise; and the
defender-relative value is unchanged, because a rotation or reflection never
swaps attackers and defenders. The throne and corner planes are D4-invariant.

What it does **not** change: self-play, MCTS and evaluation never see transformed
positions; replay stores the original positions only (it is never multiplied
eightfold); capacity, minibatch size, epochs and optimizer-update counts are
identical to `off`; games, replay and simulation counts keep their meanings.
Augmentation uses its own random stream, so `off` draws no extra random values
and reproduces the previous baseline bit-for-bit on CPU.

Augmentation, [exact inference caching](#optional-exact-input-inference-reuse)
and MCTS node-level prediction reuse are independent. Augmentation teaches the
network that orientations are equivalent, but it does **not** make the network
exactly equivariant, so predictions for rotated positions are never shared or
merged anywhere in inference or search.

**Reproducibility and resume.** The dedicated stream is saved in full checkpoints
as `augmentation_rng`. A resumed job restores it, so an interrupted
`random_d4` CPU run matches an uninterrupted one exactly. Checkpoints without the
field (older, or written by an `off` job) resume with `off` unless you set the mode,
and a deliberate off→`random_d4` resume derives its stream deterministically from
the unchanged seed and completed iteration. The new job's effective configuration
records the choice; the source checkpoint is never rewritten.

**Remote workers.** Workers report `training_symmetry_modes`. A `random_d4` job
waits for (or is refused by) a worker that does not list it; older workers still
run `off` jobs, and the default is omitted from their transport copy.

**Metrics.** Progress `performance` and `metrics.augmentation` report the mode,
`training_exposures` (examples presented to the optimizer, `epochs × replay`),
`augmentation_exposures` (those transformed, 0 when off), identity count, the
eight per-transform counts, augmentation seconds and its share of optimization
time. These count optimizer presentations, not replay positions or games.

The `recommended`, `smoke`, `beginner` and `balanced` presets do not set the mode
explicitly, so new jobs inherit the `random_d4` default. Legacy exported configs
and checkpoints that omit the field keep `off`. Evidence and the suggested
comparison experiment are in the [performance guide](PERFORMANCE.md#phase-3-training-symmetry-augmentation).

