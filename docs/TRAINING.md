# Train, evaluate and play a neural opponent

[Documentation index](README.md) · [Performance](PERFORMANCE.md) · [Checkpoints and data](TRAINING_DATA.md)

The application implements self-play policy/value learning from scratch with
PyTorch and MCTS, plus resumable Optuna hyperparameter studies. No external game
dataset is required. Training runs in a separate worker process, so it does not
block the gameplay server. The browser Training page starts, monitors, cancels
and resumes jobs and lists models. The CLI provides the same compute workflow
without writing Python code or running a browser.

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
Adam and momentum SGD are supported. Checkpoints include model/optimizer state,
replay, Python/PyTorch RNG and CUDA/gradient-scaler state for resumption.

## Parameters and presets

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
| `mcts_iterations`, `mcts_time_limit`, `exploration` | Search simulation/time budget and PUCT constant |
| `self_play_mode` | `auto` (default): batched on CUDA, sequential otherwise; or explicitly `sequential`/`batched` on either CPU or CUDA |
| `self_play_concurrency` | Maximum independent active self-play games, 1–1024 (default 16), bounded by games remaining |
| `inference_batch_size` | Maximum positions per neural inference batch, 1–4096 (default 32); separate from optimization `batch_size` |
| `temperature`, `dirichlet_alpha`, `dirichlet_fraction` | Self-play action and root exploration |
| `replay_buffer_size`, `weight_decay` | Replay capacity and regularization |
| `checkpoint_frequency`, `evaluation_frequency` | Cadence measured in training iterations |
| `evaluation_opponent` | Exactly one of `shield_bearer` (default), `shield_maiden`, `jarl`, `champion` |
| `evaluation_games`, `evaluation_iterations`, `evaluation_max_plies` | Balanced evaluation budget; games must be even |
| `evaluation_time_limit` | Neural move deadline; also Shield bearer/Shield maiden deadline; defaults to saved/self-play deadline if omitted |
| `evaluation_jarl_depth`, `evaluation_jarl_time_limit` | Actual alpha-beta depth (default 5) and deadline (default 2 seconds) |
| `champion_confidence`, `champion_min_pairs`, `champion_min_improvement` | Conservative promotion evidence: default 0.95, 12 pairs, 0 margin above 50% |
| `max_game_plies` | Explicit self-play truncation limit |
| `seed`, `device`, `mixed_precision`, `cpu_threads` | Reproducibility and compute selection |
| `output_dir`, `registry_dir`, `model_name` | Persistent outputs and human-readable model name |
| `resume` | Optional safe full checkpoint path |
| `save_all_best` | Keep and register each strict best-score improvement (boolean, default false) |

See [TRAINING_PLAN.md](TRAINING_PLAN.md) for the 7×7, 9×9 and 11×11 recommended
budgets, receptive fields, learning targets and comparison plan. The workshop
applies the matching plan when changing battlefield. Smoke remains an explicit
short software check; recommended runs are substantially longer experiments.

`balanced` increases model width/depth, games and search budgets. CPU/GPU costs
grow with games × plies × MCTS iterations, plus optimization/evaluation. Begin
with an affordable run and inspect timings before increasing budgets. Very
large replay buffers/checkpoints can exceed the enforced 128 MiB safe checkpoint
limit; reduce the buffer or model dimensions rather than bypassing validation.

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
| `optimizer` | `adamw` (default), `adam`, `sgd` |
| `device`; `self_play_mode` | `auto`/`cpu`/`cuda`/`cuda:N`/`mps`; `auto`/`sequential`/`batched` |
| `model_name`; `save_all_best` | 1–100 characters; boolean (default false) |

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
blocks, seed and optimizer type are editable; iterations is a target total.

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
| Jarl | Intermediate/advanced; existing alpha-beta search | Strictly higher score with the same depth/deadline and benchmark protocol |
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
`mcts_time_limit` when omitted; the UI exposes it independently. Consider these
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

## Hyperparameter optimization

```sh
.venv/bin/python -m hnefatafl.cli tune --config configs/tune-smoke.json
```

Optuna uses a persisted SQLite study, seeded TPE proposals and a median pruner.
Each trial runs real self-play/optimization and balanced baseline evaluation;
the objective is the completion-aware playing score above, never training loss.
Intermediate evaluated iteration scores support pruning. Champion/Jarl objectives and evaluation settings in the search space are rejected; the fixed Shield bearer objective and its deadline remain comparable across trials. `trials` is the number
of **additional** trials in the invocation; rerunning the same study resumes it.
`study_name` and storage must remain stable for resumption. Default storage is
`study.sqlite3` within the output directory.

The JSON `search_space` supports `float` (with optional `log`), `int` (with
optional `step`/`log`) and `categorical` (`choices`) distributions. The nested
`training` object supplies the common training configuration. Each trial has its
own output directory/checkpoints; `best-config.json` persists the winning full
training configuration, while `result.json` lists trial states, parameters and
scores. A tiny study verifies plumbing; strength tuning needs substantially
larger evaluation budgets and multiple independent seeds.

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
baseline scores. Optuna retains its fixed Shield bearer objective.

Low decision overhead does not guarantee equal evaluation wall time: safer play
can extend games. Consult the [measured runtime trade-offs](SHIELD_MAIDEN.md).
Update remote workers to the same release before selecting the new opponent;
checkpoint format and portable job archives remain compatible.
