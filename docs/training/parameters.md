# Training parameters

This is the canonical reference for training configuration. The backend validator
in [`hnefatafl/training.py`](../../hnefatafl/training.py) is authoritative; the
shared UI contract is
[`frontend/src/trainingContract.json`](../../frontend/src/trainingContract.json)
and the in-product explanations are
[`frontend/src/trainingTooltips.ts`](../../frontend/src/trainingTooltips.ts).
When this page and the code disagree, the code wins and this page should be
updated.

Defaults below are the backend `DEFAULTS` used by the CLI and any omitted field.
The Workshop form starts from its own editable base (currently Tablut, seed 42)
and applies a preset/ruleset plan before submission; the final submitted values
are what the server validates and records.

## General and architecture

| Parameter | Default | Accepted | Effect and interactions | Resume |
| --- | --- | --- | --- | --- |
| `ruleset` | `brandubh` (UI form: `tablut`) | `brandubh`, `tablut`, `copenhagen` | Board size and rules; models do not transfer between rulesets | Immutable |
| `preset` | `recommended` | `recommended`, `smoke`, `beginner`, `balanced` | Base settings → preset → ruleset plan → explicit fields. Changing it in the form overwrites editable fields | Not applicable after creation |
| `channels` | `16` | Integer 4–128 | Network width and capacity; raises memory and checkpoint size | Immutable |
| `blocks` | `1` | Integer 0–8 | Residual depth; 0 keeps only the input convolution stage | Immutable |
| `model_name` | `Self-play learner` (UI: `My first strategist`) | 1–100 characters | Registry label and checkpoint name; identity is separate from the label | Editable |
| `save_all_best` | `false` | Boolean | Registers every strict score improvement; ties create nothing | Editable |
| `seed` | `0` (UI form: `42`) | Integer 0–4,294,967,295 | Model init, self-play sampling, symmetry stream and derived per-iteration streams | Immutable |
| `device` | `auto` | `auto`, `cpu`, `cuda`, `cuda:N`, `mps` | Training compute. `auto` prefers CUDA, then MPS, then CPU; an explicit unavailable device fails | Editable |
| `mixed_precision` | `true` | Boolean | CUDA AMP; no effect on CPU/MPS; not bit-exact | Editable |
| `cpu_threads` | `2` | Integer 1–64 | PyTorch CPU threads and OpenVINO CPU inference threads; CPU search workers pin one thread each | Editable |
| `output_dir` | `data/training` | Non-empty path | CLI-only; web jobs assign an owned directory and reject this field | Immutable per job |
| `registry_dir` | unset | Path | CLI-only model registry override; web jobs use their own model directory | Not a web field |

## Iterations, self-play and replay

| Parameter | Default | Accepted | Effect and interactions | Resume |
| --- | --- | --- | --- | --- |
| `iterations` | `5` | Integer 1–100,000 | Total training iterations. Each generates games, trains, optionally evaluates and checkpoints | Editable as a new target total; cannot discard completed iterations |
| `games` | `4` | Integer 1–10,000 | Self-play games per iteration; cost scales with plies and MCTS budget | Editable |
| `epochs` | `2` | Integer 1–100 | Passes over replay per iteration; update count ≈ epochs × ceil(replay / batch) | Editable |
| `mcts_iterations` | `24` | Integer 1–100,000 | MCTS simulations per self-play move; stopped earlier by the time limit | Editable |
| `mcts_time_limit` | `10.0` s | 0.001–300 | Wall-clock ceiling per self-play move; also the default evaluation limit when that field is omitted | Editable |
| `self_play_mode` | `auto` | `auto`, `sequential`, `batched` | `auto` uses batched on CUDA/OpenVINO, sequential otherwise; batched groups neural evaluations | Editable |
| `self_play_concurrency` | `16` | Integer 1–1,024 | Maximum concurrent games and neural batch fill in batched mode | Editable |
| `inference_batch_size` | `32` | Integer 1–4,096 | Maximum positions per forward pass; never waits to fill a batch; separate from optimization batch | Editable |
| `inference_cache_mode` | `off` | `off`, `exact` | Exact legal-only cache per phase/backend/precision; dropped when model/backend/precision changes | Editable |
| `inference_cache_capacity` | `4096` | Integer 1–65,536 | LRU capacity; ignored when the cache is off | Editable |
| `exploration` | `1.4` | 0–20 | PUCT exploration constant; self-play only | Editable |
| `temperature` | `1.0` | 0.01–10 | Self-play move sampling; effective value drops after 16 plies; evaluation is deterministic | Editable |
| `dirichlet_alpha` | `0.3` | 0.001–100 | Root prior noise concentration; only with noise fraction > 0 | Editable |
| `dirichlet_fraction` | `0.25` | 0–1 | Share of root priors replaced by noise; self-play only | Editable |
| `replay_buffer_size` | `5000` | Integer 1–20,000 | Recent positions retained; bounds per-epoch data and memory | Editable |
| `max_game_plies` | `150` | Integer 2–5,000 | Self-play truncation; truncated games are unfinished and bootstrapped from the value head | Editable |

## Optimization

| Parameter | Default | Accepted | Effect and interactions | Resume |
| --- | --- | --- | --- | --- |
| `batch_size` | `32` | Integer 1–2,048 | Replay positions per gradient update | Editable |
| `learning_rate` | `0.001` | 1e-7–1 | Optimizer step size / Schedule-Free base rate; with HyperController it must be one of the candidates | Editable |
| `optimizer` | `adamw` | `adamw`, `adam`, `sgd`, `adamw_schedulefree` | Weight-update algorithm; part of checkpoint identity | Immutable |
| `weight_decay` | `0.0001` | 0–1 | Decoupled in AdamW; optimizer-native for Adam/SGD | Editable |
| `momentum` | `0.9` | 0–1 | SGD momentum only; ignored by Adam/AdamW/Schedule-Free | Editable (SGD) |
| `schedulefree_warmup_steps` | `0` | Integer 0–1,000,000 | Actual optimizer updates used for linear warm-up; read-only on resume | Immutable |
| `training_symmetry_mode` | `random_d4` | `off`, `random_d4` | D4 sample/target augmentation with a resumable dedicated RNG; not a rules change. Older checkpoints default `off` until set | Editable |
| `checkpoint_frequency` | `1` | Integer 1–10,000 | Rolling `last.pt` cadence; the final iteration always writes | Editable |
| `evaluation_frequency` | `1` | Integer 1–10,000 | Evaluation cadence; the final iteration is always evaluated | Editable |

## Evaluation and Champion promotion

| Parameter | Default | Accepted | Effect and interactions | Resume |
| --- | --- | --- | --- | --- |
| `evaluation_opponent` | `shield_bearer` | `shield_bearer`, `shield_maiden`, `jarl`, `champion` | Exactly one opponent per series; only Champion can promote. Changing it starts a new series | Editable |
| `evaluation_games` | `2` | Even integer 2–10,000 | Total games, side-swapped pairs sharing a seed | Editable |
| `evaluation_iterations` | `16` | Integer 1–100,000 | MCTS simulations for the neural candidate and Champion; unused by heuristics/Jarl | Editable |
| `evaluation_time_limit` | `mcts_time_limit` when omitted (`10.0`) | 0.001–300 s | Neural candidate and Shield bearer/maiden deadline; Jarl has its own | Editable |
| `evaluation_mode` | `auto` | `auto`, `sequential`, `batched` | Batched on CUDA/OpenVINO, sequential otherwise | Editable |
| `evaluation_concurrency` | `16` | Integer 1–1,024 | Concurrent evaluation games / worker cap in batched mode | Editable |
| `evaluation_batch_size` | `32` | Integer 1–4,096 | Positions per evaluation forward pass | Editable |
| `evaluation_inference_backend` | `pytorch` | `pytorch`, `training` | `training` follows the job backend (relevant for OpenVINO) | Editable |
| `evaluation_max_plies` | `150` | Integer 2–5,000 | Evaluation truncation; unfinished games score zero and can make promotion inconclusive | Editable |
| `evaluation_jarl_depth` | `5` | Integer 1–8 | Jarl alpha-beta depth | Editable |
| `evaluation_jarl_time_limit` | `2.0` s | 0.001–300 | Jarl deadline; Jarl ignores the general evaluation limit | Editable |
| `champion_confidence` | `0.95` | 0.8–0.9999 | One-sided bound strictness for promotion | Editable |
| `champion_min_pairs` | `12` | Integer 2–5,000 | Minimum complete side-swapped pairs | Editable |
| `champion_min_improvement` | `0.0` | 0–0.49 | Required margin above 50% for the lower bound | Editable |

## Hardware backends

| Parameter | Default | Accepted | Effect and interactions | Resume |
| --- | --- | --- | --- | --- |
| `inference_backend` | `pytorch` | `pytorch`, `openvino` | Self-play inference. OpenVINO requires CPU training and supported Intel hardware; evaluation stays PyTorch by default | Editable |
| `inference_device` | `auto` | `auto`, `cpu`, `cuda`, `mps`, `GPU`, `GPU.N` | OpenVINO device selection; PyTorch ignores it | Editable |
| `search_workers` | `0` | Integer 0–32 | Extra CPU MCTS processes; PyTorch CPU models only; 0 disables | Editable |

## Optional online adaptation

These fields are ignored when `adaptation` is `off`.

| Parameter | Default | Accepted | Effect |
| --- | --- | --- | --- |
| `adaptation` | `off` | `off`, `hypercontroller` | Enables in-run learning-rate adaptation; incompatible with Schedule-Free |
| `adaptation_lr_candidates` | `0.0003,0.001,0.003` | 2–8 distinct rates in 1e-7–1 | Candidate rates; configured `learning_rate` must be included |
| `adaptation_cadence` | `1` | Integer 1–10,000 | Completed evaluations between decisions |
| `adaptation_warmup` | `2` | Integer 0–10,000 | Evaluations observed before any change |
| `adaptation_min_dwell` | `1` | Integer 1–10,000 | Evaluations a chosen rate is kept |
| `adaptation_min_dwell_iterations` | `0` | Integer 0–1,000,000 | Optional iteration dwell; 0 disables |
| `adaptation_lag` | `1` | Integer 1–3 | Observation history window |
| `adaptation_min_trials` | `1` | Integer 1–100 | Per-context forced trials of each candidate |

## Hyperparameter tuning

Tuning adds `trials`, `study_name`, `seed`, `search_space`, `resume_study`,
`source_checkpoint`, `opponents`, `verification`, `sampler`, `pruner` and
`max_failures`. Each trial trains a full model; only tunable training parameters
may appear in the search space. Evaluation/champion fields cannot be tuned. See
[getting started](tuning.md#tuning-workflow).

## Resume compatibility

Editable fields change the next iterations without resetting model, optimizer,
replay or RNG state. Immutable fields are part of the checkpoint identity and are
rejected if changed: `ruleset`, `channels`, `blocks`, `seed`, `optimizer` and
`schedulefree_warmup_steps`. `iterations` is a target total and cannot be reduced
below completed work. Changing the evaluation opponent starts a new comparison
series; changing the symmetry mode seeds a deterministic augmentation stream.
Schedule-Free base-rate and weight-decay overrides preserve its averaging state;
an unsafe live optimizer transition blocks further checkpoint writes and should be
discarded in favour of the previous valid checkpoint.

Changing an inference backend, cache or execution mode affects throughput, not
the learned algorithm. Keep the same budget and side assignment when comparing
throughput or strength. See [performance](../evaluation/methodology.md).

## Validation notes

- Bounds are validator/UI limits, not recommended experimental budgets. Recommended
  values differ by board; see [plans](plans.md).
- A larger replay buffer or model can exceed the enforced 128 MiB safe checkpoint
  limit. Reduce dimensions instead of bypassing validation.
- `mixed_precision` is not bit-exact. Parallel search processes and GPU reductions
  can produce small numerical differences even with a fixed seed.

## Agent search settings

These are game/AI search fields, not training-job hyperparameters. They apply to
custom opponents, advisors, CLI/headless calls and tournament seats. The core
Python/headless bounds differ from the API/UI validation bounds.

| Field | Core/headless | Game/tournament API | Game UI |
| --- | --- | --- | --- |
| `depth` (alpha-beta) | 1–8 | 1–5 | 1–5 |
| `iterations` (MCTS) | 1–100,000 | 1–5,000 | 1–5,000 |
| `rollout_depth` | 0–128 | 0–50 | Not exposed |
| `time_limit` | >0–300 s | 0.01–15 s | 0.05–15 s |
| `exploration` | 0–20 | 0–10 | 0–10 |
| `temperature` | 0–10 | 0–5 | 0–2 |

Without `profile_id`, core defaults are alpha-beta depth 2, MCTS 80 simulations,
rollout depth 8, exploration 1.4, temperature 0 and a 10-second ceiling; non-MCTS
strategies keep the historical 2-second default. The fixed Seer preset caps at 5,000
simulations/2 seconds. Training plans use their own board-specific MCTS budgets.
Named game presets are fixed in normal setup; **Custom opponent** exposes overrides
to administrators.

Zero temperature selects the most visited root action (or highest policy-only
probability); positive temperature samples. Invalid algorithms, budgets, terminal
states or incompatible/missing models raise errors. A time limit bounds the search
loop, not request latency; an in-progress expansion or network call may overrun.

For reproducible comparisons, use fixed seeds and a budget that completes. Browser
games get a fresh random base seed; the server derives each decision from
`(base + ply) % 2**32`. An explicit API game can supply a top-level `seed`; old
games retain their original behavior. See [agent evaluation](../evaluation/agents.md)
and [algorithms](algorithms.md).
