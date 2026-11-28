# Training troubleshooting

Check the job's persisted files before changing anything: `progress.json`,
`heartbeat.json`, structured logs, `last.pt` and `result.json` under the job's
directory. CLI stdout is an event stream, not a complete history. The Workshop
reads the same persisted data.

## Device and backend selection

| Symptom | Check |
| --- | --- |
| `auto` stays sequential | `hnefatafl.cli devices`; CPU and MPS intentionally use sequential self-play |
| CUDA requested but unavailable | Official PyTorch CUDA build, driver, `CUDA_VISIBLE_DEVICES`; an explicit unavailable device fails instead of falling back |
| OpenVINO not offered | Install the `openvino` extra and verify `hnefatafl.cli devices`; a GPU visible to Windows or PyTorch is not enough |
| OpenVINO selected but fails | Intel GPU driver/OpenCL/Level Zero runtime; restart the worker after repairing dependencies |
| CUDA out of memory | Reduce network size, inference/optimization batch sizes or worker slots; slots do not partition GPU memory automatically |
| CPU overload | Return `OMP_NUM_THREADS`/`MKL_NUM_THREADS` to 2, reduce `cpu_threads`, `self_play_concurrency` or `search_workers` |

There is no silent algorithm or device fallback. A rejected field should name the
parameter and preserve the entered values.

## Throughput differs from expectations

- Confirm the same work: completed depth, games/plies, MCTS budget, batch sizes,
  device, backend and precision.
- Small batches mean the scheduler did not have enough independent positions; do
  not compare a CPU smoke run with a GPU run.
- CPU batching and cache measurements have shown workload-dependent or no gains.
  See [performance](performance.md).
- Higher throughput caused by hitting the ply cap is not progress. Inspect
  unfinished/truncated games separately from rule draws.

## Checkpoint and resume

| Symptom | Cause and action |
| --- | --- |
| Resume unavailable | Inference-only model, deleted source job, missing/unsafe `last.pt`, or an active job. A model-card resume uses the source job's latest full checkpoint, not the selected best snapshot |
| Resume rejects a field | Ruleset, channels, blocks, seed, optimizer and Schedule-Free warm-up are immutable; `iterations` cannot shrink below completed work |
| Directory exists but job missing | The jobs registry database was not moved. Import a job archive, import the model or resume via the CLI; folders are not scanned automatically |
| Checkpoint exceeds 128 MiB | Reduce replay/network budgets. Do not disable safe-load limits or use unrestricted pickle loading |
| Schedule-Free resume fails | Warm-up duration cannot change after updates; an unsafe live optimizer transition must be discarded in favour of the previous valid checkpoint |
| Adaptation state rejected | Worker lacks the required capability/state version; update the worker or use a fresh job. Never bypass the capability gate |

## Tuning and evaluation

- An Optuna study lives in the job's own study database. A new tuning job does not
  continue an old study by name alone; use the saved job's **Resume study** action.
- A best-configuration export is a configuration artifact, not a promoted model.
- Evaluation unfinished games count in the denominator and can make champion
  promotion inconclusive. Increase `evaluation_games`/`evaluation_max_plies` or
  reduce the search budget if truncation dominates.
- Changing the evaluation opponent starts a new series; do not compare scores
  across opponents as if they shared a baseline.
- Champion promotion requires enough complete side-swapped pairs, a confidence
  bound and any configured margin. Ties do not create best models.

## Remote workers

| Symptom | Check |
| --- | --- |
| Job stays queued | Worker online/free slots, token, enrolled worker selection, requested device and capabilities |
| Worker unauthorized | Correct server URL and current token; revoked credentials need re-enrollment, not a TLS bypass |
| Lease expired | Compare uploaded progress; explicitly resume a new job rather than reusing the old lease |
| Model available but job not resumable | The model is inference-only or the source full checkpoint was not uploaded |
| Version/capability mismatch | Compare both package versions and installed source; restart the worker supervisor after updating; capability gates cannot be bypassed by an admin override |
| Cleanup did not free worker disk | Server cleanup does not delete worker-local artifacts; manage the worker's private directory separately |

See [remote workers](remote-workers.md) for enrollment and transfer details.

## Data and cleanup

- Stop active writers and review shared studies, resume dependencies and model
  references before deleting files.
- Deleting training data does not delete a registered playable model copy.
  Deleting a model does not delete the job that produced it.
- A backup is unverified until restored into a disposable directory and checked.
  Copying a live SQLite database without its WAL is not a consistent backup.
- Do not commit databases, checkpoints, model weights, studies or credentials.

## Reporting a problem

Record the exact command, configuration (without secrets), package version,
device/backend, data directory layout, persisted job state and the failing phase.
Distinguish an implementation failure from an unsupported hardware configuration.
Do not claim a training improvement from a successful smoke run alone.
