# Train, evaluate and play a neural opponent

The application implements self-play policy/value learning from scratch with
PyTorch and MCTS, plus resumable Optuna hyperparameter studies. No external game
dataset is required. Training runs in a separate worker process, so it does not
block the gameplay server. The browser Training page starts, monitors, cancels
and resumes jobs and lists models. The CLI provides the same compute workflow
without writing Python code or running a browser.

## Installation and hardware

Install the project's Python dependencies using the root README commands.
`torch` and `optuna` are required for training; ordinary engine play does not
need to import them. Inspect actual hardware with:

```sh
.venv/bin/python -m hnefatafl.cli devices
```

`device: "auto"` selects CUDA when available, then Apple MPS, then CPU. Explicit
`cpu`, `cuda`, `cuda:N` or `mps` requests fail clearly if unavailable. For NVIDIA
training, install the CUDA-enabled PyTorch wheel matching your driver using the
current command from [PyTorch's official installer](https://pytorch.org/get-started/locally/).
The development lock uses the official CPU wheel to remain runnable on machines
without CUDA; a CPU wheel alone does not provide NVIDIA acceleration.

CUDA training uses mixed precision when `mixed_precision` is true. The model
stays on the chosen device during self-play and optimization; each optimization
minibatch transfers inputs and targets together. There is no forked CUDA context:
the API launches an independent Python process. Self-play is sequential within a
worker, which makes small machines usable and seeded debugging simple; larger
distributed inference/training is future work. CUDA memory usage is included in
progress when available. This environment verified CPU training; CUDA/MPS
execution requires separate verification on matching hardware.

### Separate CUDA environment

Keep the reproducible CPU `.venv` intact and create a separate GPU environment.
Use the wheel index selected by the official PyTorch installer for your driver;
`cu128` below is an example for a compatible CUDA 12.8 installation, not a claim
that this host has that runtime or hardware:

```sh
python3 -m venv .venv-cuda
.venv-cuda/bin/python -m pip install --upgrade pip
.venv-cuda/bin/python -m pip install torch --index-url https://download.pytorch.org/whl/cu128
.venv-cuda/bin/python -m pip install -e '.[train]'
.venv-cuda/bin/python -m hnefatafl.cli devices
.venv-cuda/bin/python -m hnefatafl.cli train --config configs/train-beginner.json
```

The `auto` device chooses CUDA only when PyTorch can actually initialize it. Do not
run the CPU `uv sync` command into this separate environment: the project lock
deliberately selects CPU wheels. Record the chosen PyTorch/driver versions with
your experiment results. This CUDA recipe requires verification on the GPU host.

For web-managed GPU jobs, launch the server with that environment too:
`.venv-cuda/bin/python -m uvicorn hnefatafl.server:app --host 127.0.0.1 --port 8000`.
Workers inherit the server's Python executable. Update the systemd `ExecStart`
similarly. In Proxmox LXC, expose the host NVIDIA devices and matching driver
libraries to the container first; check actual device visibility with `nvidia-smi`.
Driver/device passthrough is host administration, not supplied by installing a wheel.

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

| Parameter | Purpose |
| --- | --- |
| `preset` | `recommended` (default, ruleset-specific), `smoke`, `beginner` or `balanced`; explicit fields override it |
| `ruleset` | `brandubh`, `tablut`, `copenhagen` |
| `channels`, `blocks` | Residual architecture width (4–128) and depth (0–8) |
| `games`, `iterations`, `epochs` | Games per iteration, total iterations, optimization epochs |
| `batch_size`, `learning_rate`, `optimizer` | Optimization settings |
| `mcts_iterations`, `mcts_time_limit`, `exploration` | Search simulation/time budget and PUCT constant |
| `temperature`, `dirichlet_alpha`, `dirichlet_fraction` | Self-play action and root exploration |
| `replay_buffer_size`, `weight_decay` | Replay capacity and regularization |
| `checkpoint_frequency`, `evaluation_frequency` | Cadence measured in training iterations |
| `evaluation_games`, `evaluation_iterations`, `evaluation_max_plies` | Balanced evaluation budget; games must be even |
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

## Monitoring, cancellation and resumption

Workers emit JSON-line progress and atomically update `progress.json`. It reports
stage, device, iteration, games generated, policy/value loss, update count,
replay positions, self-play outcomes, baseline/previous-model evaluation,
durations, and CUDA memory where available. Final `result.json` includes model
registry data and checkpoint locations. Inspect these files directly for a
headless run. The web UI polls the same persisted worker results.

The progress panel plots policy/value losses and completion-aware evaluation
score against completed iterations, including while a worker is running. An
accessible data table contains the same points. History survives reopening and
resumption. Enable **Save every new best model** to keep earlier strict record
improvements as separately selectable models; ties do not create extra snapshots.

Send SIGINT or SIGTERM (or Cancel in the UI) to request a graceful stop. The worker
checks between moves and minibatches, writes a resumable `last.pt`, and marks
the job cancelled. Keep persistent storage; kill -9 cannot save the in-flight
minibatch, though previous atomic checkpoints remain.

In the workshop, select a stopped or completed job and choose **Resume from
checkpoint**. The dialog validates its full `last.pt`, shows the saved completed
iteration, ruleset, network and optimizer, and lets you choose **Target total
iterations**. The target must exceed the saved completed iteration. The other
training settings come from the checkpoint rather than the current form.

Model cards linked to a retained source job also offer **Resume training**.
This continues that job's **latest full checkpoint**, which may be later than
the selected best model's iteration; the dialog makes that iteration explicit.
Imported inference-only models without a retained full training checkpoint are
available for play but cannot restore optimizer/replay/random state.

Resumption starts a new job/output directory and preserves the original run.
The API uses `GET /api/training/jobs/{id}/resume` for reviewed checkpoint metadata
and `POST` to the same route with `{ "iterations": <target total> }` to continue.
It never accepts an arbitrary client checkpoint path or an architecture override.

To resume, keep the same ruleset, architecture and optimizer and increase
`iterations` in a copied JSON configuration. This field is the target **total**
iteration count, not the number of additional iterations:

```sh
.venv/bin/python -m hnefatafl.cli train --config my-resume-config.json --resume data/training-brandubh/last.pt
```

The default tests verify resumed weights exactly match an uninterrupted CPU run
with the same settings. Hardware-dependent numerical determinism is not
guaranteed across CUDA versions or different devices. When a config explicitly
sets a learning rate, it overrides the restored optimizer rate; otherwise the
restored optimizer state is retained.

The full `last.pt` also carries a cloned best-model snapshot and its original
metadata. Moving that one checkpoint to a new output directory preserves the
true best model and previous-model comparisons, even when later iterations are
weaker. `best.pt` and registry inference exports intentionally have no optimizer
state and cannot be used for exact training resumption.

## Evaluation and model selection

Evaluation alternates candidate attacker/defender sides against the heuristic
baseline and, after a previous best exists, against that previous model.
Reports separate wins, losses, rules draws and unfinished cap runs. For tuning,
the conservative score is `(wins + 0.5 * rules_draws) / scheduled_games`.
Truncation earns no credit and does not count as a loss or draw. Increasing
evaluation limits and game counts is essential before interpreting strength.
Small smoke runs can legitimately have zero wins; loss reduction alone does not
demonstrate improved gameplay. No pretrained strong model is bundled.

## Checkpoints, registry, import and export

Each completed job writes:

- `last.pt`: full resumable state from the last checkpoint iteration.
- `best.pt`: compact model snapshot at the highest balanced baseline score
  (later ties win); it has no optimizer/replay state.
- `inference.pt`: compact weights plus model/training/evaluation metadata,
  without optimizer, replay or RNG state.
- `config.json`, `progress.json`, `result.json`: human-readable provenance.

The compact model is automatically registered with name/version, ruleset,
architecture, date, configuration and metrics. Select that model in Human vs AI
or AI vs AI and use neural MCTS. Models are content-addressed so repeated imports
do not produce duplicate entries.

```sh
.venv/bin/python -m hnefatafl.cli models --registry data/models
.venv/bin/python -m hnefatafl.cli models --registry data/models --import data/training-brandubh/inference.pt --name "Brandubh experiment"
.venv/bin/python -m hnefatafl.cli models --registry data/models --export MODEL_ID --output exported-model.pt
```

The registry API/UI supports the same import/list/export workflow. Loading uses
`torch.load(weights_only=True)`, validates format/version, exact tensor shapes
and dtypes, finite values, architecture and ruleset, and rejects arbitrary pickle
objects. Imports have file and expanded-storage bounds; no unsafe loading
fallback exists. Keep full job checkpoints for resumption; compact inference
exports intentionally cannot resume an optimizer.

## Managing models and retained training data

Model cards show the recorded policy/value network width and residual depth,
snapshot iteration, self-play/search budget and optimizer settings. These describe
the saved snapshot rather than the current training form. Earlier imports can lack
training metadata; missing values are identified rather than inferred from today's
recommended plan. Tournament setup and saved reports use the same summary.

**Rename model** changes its catalog label while preserving its model ID, rune,
weights and original training metadata. New selections use the new name; existing
games and tournaments retain their recorded name snapshots. Checkpoint exports
still contain their original embedded name. Reimporting the same checkpoint into
the existing catalog keeps the chosen catalog name.

**Load training parameters** copies recorded, editable training settings into the
workshop form, including architecture and advanced settings. Review them before
starting a new run. This does not restore weights, optimizer or replay state; use
**Resume training** for that. Server paths and resume tokens are never copied into
the form. Unrecorded settings keep their current form values, with an explicit
notice. Models without any usable recorded settings cannot supply this action.

Select a saved job and choose **Delete training data** to remove its job record,
checkpoints, replay state, progress and logs after confirmation. Registered model
copies and existing game/tournament histories remain available. Deleting a source
job removes the ability to resume that source checkpoint. Stop a running worker
before deleting its data. Shared optimization study data remains protected while
another saved job references it; remove dependent jobs first.

The API provides `PATCH /api/models/{id}` with `{ "name": "New name" }` and
`DELETE /api/training/jobs/{id}`. These are separate from model deletion.

## Hyperparameter optimization

```sh
.venv/bin/python -m hnefatafl.cli tune --config configs/tune-smoke.json
```

Optuna uses a persisted SQLite study, seeded TPE proposals and a median pruner.
Each trial runs real self-play/optimization and balanced baseline evaluation;
the objective is the completion-aware playing score above, never training loss.
Intermediate evaluated iteration scores support pruning. `trials` is the number
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

![Reviewing a full checkpoint before resuming](screenshots/resume-dialog.png)

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
