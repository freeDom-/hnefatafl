# Getting started with training

[Training index](README.md) · [Workshop interface](interface.md) · [Parameters](parameters.md) · [Training plans](plans.md)

This guide takes a new operator from prerequisites to a first real training job,
an evaluation result and a playable model. Advanced algorithms, tuning, worker
protocols and benchmarks are linked rather than repeated here.

## Prerequisites

- Python 3.12 recommended (3.11+ supported), Node.js 22.12+/24+ and `uv`.
- `make install` installs the locked CPU training/dev environment and frontend.
- Use an isolated `HNEFATAFL_DATA_DIR` for experiments. Never commit jobs,
  checkpoints or models.
- Training and model management require an administrator permission in the web UI;
  the CLI works in the active checkout.

```sh
make install
.venv/bin/python -m hnefatafl.cli devices
```

`devices` reports PyTorch and optional OpenVINO availability. `auto` prefers CUDA,
then Apple MPS, then CPU. An explicitly requested unavailable device fails instead
of silently changing backend. See [hardware](performance.md#choose-hardware).

## First smoke run

A smoke run proves the workflow: self-play data, real optimizer updates, checkpoint
loading and legal model play. It does **not** prove stronger play.

Web: open **AI Workshop → Training**, choose **New training**, keep the **Quick check**
preset, name the model and start. The job appears under **At the forge** with live
phase, losses and evaluation progress.

CLI equivalent:

```sh
.venv/bin/python -m hnefatafl.cli train --config configs/train-smoke.json
```

Inspect the job directory for `progress.json`, `heartbeat.json`, `last.pt` and
`result.json`. CLI stdout is a JSON event stream, not one final document.

## Create a real job

1. Choose the battlefield: Brandubh, Tablut or Copenhagen. Models do not transfer
   between rulesets.
2. Choose a preset: **Recommended** applies the board-specific plan; **Beginner** and
   **Balanced** are progressively longer; **Quick check** is functional only.
3. Review the final values. Precedence is base settings → preset/ruleset plan →
   explicit fields. See [plans](plans.md) and [parameters](parameters.md).
4. Choose one evaluation opponent: Shield bearer, Shield maiden, Jarl or Champion.
   Only Champion can promote the candidate during the run.
5. Start locally or on a compatible remote worker. A remote job waits until a worker
   with the required capabilities has capacity.

The form preserves rejected values and names the offending field. Importing a
`config.json` never starts a job; review it first.

## What one iteration does

1. Generate `games` self-play games with the configured MCTS budget.
2. Append positions to the bounded replay buffer.
3. Train `epochs` passes over the buffer in `batch_size` optimization batches.
4. Optionally evaluate, checkpoint and consider Champion promotion.

Evaluation games never become training data. A ply-limited game is **unfinished**,
not a draw; it is bootstrapped from the value head and can make promotion
inconclusive. See [algorithms](algorithms.md).

## Monitor and resume

The Workshop shows phase, iteration, losses, evaluation and throughput. A long game
or optimization epoch keeps elapsed time advancing; **No recent updates** does not
mean the job is dead, and **Monitoring connection unavailable** blames the browser
connection rather than the worker. See [interface](interface.md) and
[monitoring internals](../development/training-monitoring.md).

Cancellation is cooperative: the job writes a resumable checkpoint between moves and
minibatches. Resume from a full checkpoint, not an inference-only model. Review
editable settings and immutable architecture/optimizer identity in
[models](models.md#monitoring-cancellation-and-resume).

## Evaluate and use the model

Evaluation plays side-swapped pairs against exactly one opponent. A higher score is
evidence for that opponent, budget and sample, not a general strength claim. Champion
promotion requires enough complete pairs and the configured confidence/margin.
See [evaluation](evaluation.md).

A registered model can be selected as an AI opponent with an MCTS, alpha-beta or
policy-only wrapper. Importing a model supplies an opponent but does not create or
resume a training job. Model deletion and training-data deletion are separate actions;
completed games retain their historical labels. See [models](models.md).

## Common next steps

| Goal | Guide |
| --- | --- |
| Understand algorithms and training targets | [Algorithms](algorithms.md) |
| Look up defaults, bounds and resume rules | [Parameters](parameters.md) |
| Choose a board-specific budget | [Training plans](plans.md) |
| Understand evaluation and Champion promotion | [Evaluation](evaluation.md) |
| Run Optuna tuning | [Tuning](tuning.md) |
| Adjust learning rate online | [Online adaptation](online-adaptation.md) |
| Use a remote CPU/GPU worker | [Remote workers](remote-workers.md) |
| Configure CUDA/MPS/OpenVINO or optimize throughput | [Performance](performance.md), [OpenVINO](openvino.md) |
| Diagnose a failed or stalled job | [Troubleshooting](troubleshooting.md) |

## Verification

Functional tests prove real updates, checkpoint restoration and legal play, not
playing strength or calibrated chances. CPU and GPU are not interchangeable. See
[benchmark methodology](../evaluation/methodology.md) and [troubleshooting](troubleshooting.md).
