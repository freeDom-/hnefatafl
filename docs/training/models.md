# Models, checkpoints and training data

[Training index](README.md) · [Getting started](getting-started.md) · [Training data internals](../development/training-data.md)

## What is stored

The persistent data root contains:

- the account/application SQLite database;
- training jobs (`config.json`, `progress.json`, `heartbeat.json`, logs, checkpoints, `result.json`);
- registered model copies used for play;
- optimization studies and job archives;
- remote-worker state.

A **training job** and a **registered model** are separate. Deleting training data
removes that job's checkpoints/logs but keeps a playable model copy. Deleting a model
removes its inference copy/entry but not its source job. Completed games and
historical model labels remain readable after either action.

Use an isolated `HNEFATAFL_DATA_DIR` for tests. Never commit jobs, checkpoints,
models or the database.

## Checkpoint types

| Type | Can play? | Can resume? |
| --- | --- | --- |
| Full checkpoint (`last.pt`) | Yes (via registry/export) | Yes |
| Registered model copy | Yes | Not by itself |
| Inference-only export/import | Yes | No |

Full checkpoints retain model/optimizer state, replay, RNG and CUDA/scaler state.
Training has a 128 MiB safe checkpoint limit; reduce replay/network budgets if a
checkpoint would exceed it. Do not bypass the validated tensor-only loader.

Resume identity is immutable for ruleset, channels, blocks, seed, optimizer and
Schedule-Free warm-up duration. Iterations are a target total and cannot discard
completed work. Other settings are editable with review. A resumed run keeps
learning state; changing evaluation opponent starts a new comparison series.

See [training data internals](../development/training-data.md) for formats,
archives, migration and recovery invariants.

## Monitoring, cancellation and resume

The Workshop shows live phase, iteration, losses, evaluation and throughput. CLI
stdout is an event stream; complete history is in the job files/checkpoint.

Cancel with **Stop training**, SIGINT or SIGTERM. The worker checks between moves and
minibatches, writes a resumable `last.pt` and marks the job cancelled. `kill -9`
cannot save the in-flight minibatch, though earlier atomic checkpoints remain.

**Resume training** uses the source job's latest full checkpoint, not the selected
best-model snapshot. Review editable settings and immutable fields in the dialog.
A model-card resume is unavailable for inference-only exports, deleted jobs,
missing/unsafe checkpoints or an active job.

See [interface](interface.md) for monitoring states and
[Remote workers](remote-workers.md) for distributed resume.

## Manage models

Model cards support:

- **Rename** — changes the catalog label, keeps model ID/rune and historical labels.
- **Load training parameters** — prefills a new job form; does not resume.
- **Export** — downloads a portable model file.
- **Resume training** — available only when a resumable source job exists.
- **Delete model** — confirmation checks active jobs, studies and unfinished games.

Deleting a model with unfinished references requires explicit discard consent; it
does not delete completed games. Running tournaments/studies must stop first. Shared
optimization studies can block individual job deletion; use the study-deletion flow.

Importing a model supplies an opponent; it does not create or resume a training job.
Older imports without training metadata show **Training iteration: not recorded**.

## Move data between installations

1. Stop writers or take a supported consistent backup.
2. Preserve the complete data root: database, job directories, model registry,
   studies, checkpoints and worker state.
3. Restore into a disposable directory and verify accounts, games, model hashes,
   checkpoint loading and an actual resume.
4. Only then replace live data.

Importing a model or job archive can transfer selected artifacts, but it is not a
full installation migration. Incompatible/corrupt/oversized archives fail closed.
See [training data](../development/training-data.md#job-archives-and-migration).

## Consistent backup and restoration

A backup is unverified until restored and checked. Copying a live SQLite file without
its WAL is not consistent. Stop writers or use the supported backup command; include
the entire data root, not only the database. Verify games/replays, accounts, runes,
progression, tournaments, model hashes and a real training resume. See
[updating and recovery](../installation/updating.md) and
[installation backup notes](../installation/updating.md#manual-backups-and-recovery-transactions).

## Troubleshooting retained data

| Symptom | Check/action |
| --- | --- |
| Directory exists but job missing | Jobs registry was not moved or the run was CLI-only. Import an archive, import the model or resume via CLI; folders are not scanned |
| Model plays but Resume unavailable | Inference-only export, missing/unsafe `last.pt`, deleted source job or active job |
| Resume starts later than selected model | Model-card resume uses the source job's latest full checkpoint |
| Cleanup blocked | Stop active users; review shared-study/resume/model references |
| Checkpoint >128 MiB | Reduce replay/network budgets; do not disable safe loading |
| Restore seems incomplete | Verify the complete data root, not just the database |

See [troubleshooting](troubleshooting.md) and
[training data internals](../development/training-data.md).

## Optional OpenVINO

See [OpenVINO](openvino.md) for setup, portability and measured limitations.
