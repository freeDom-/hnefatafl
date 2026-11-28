# Training and AI evaluation

The Workshop trains policy/value networks from self-play, evaluates them against
fixed opponents and manages the resulting models. Training is administrator-only
by default. The CLI provides the same compute workflow without the web UI.

| Topic | Page |
| --- | --- |
| First job, presets, monitoring, evaluation, tuning and resume | [Getting started](getting-started.md) |
| Workshop pages, charts, job lifecycle and model cards | [Interface](interface.md) |
| Algorithms and how they interact | [Algorithms](algorithms.md) |
| Canonical parameter, default and bound reference | [Parameters](parameters.md) |
| Recommended board-specific budgets | [Training plans](plans.md) |
| Checkpoints, registry, import/export, storage, backup and migration | [Models and data](models.md) |
| CPU/CUDA/MPS performance, batching, caches, monitoring and benchmark protocol | [Performance](performance.md) |
| Optional Intel OpenVINO inference | [OpenVINO](openvino.md) |
| Schedule-Free AdamW CUDA acceptance procedure | [Schedule-Free CUDA](schedulefree-cuda.md) |
| Optional online learning-rate adaptation | [Online adaptation](online-adaptation.md) |
| Optimization integration notes and independent measurements | [Training optimizations](training-optimizations.md) |
| Remote workers, enrollment, leases and transfers | [Remote workers](remote-workers.md) |
| Common failures and diagnostics | [Troubleshooting](troubleshooting.md) |

## Minimum workflow

```sh
make install
.venv/bin/python -m hnefatafl.cli devices
.venv/bin/python -m hnefatafl.cli train --config configs/train-smoke.json
```

A smoke run proves real updates, checkpoint reload and legal model play. It does
**not** prove stronger play, calibrated winning chances or GPU speedup. Use
[training plans](plans.md) for a real experiment and read
[performance](performance.md) before comparing throughput.

## Where things live

- Training jobs, checkpoints, replay buffers and learning history live in the
  persistent data root.
- Registered models are separate from training jobs; deleting training data does
  not remove a playable model copy.
- Importing a model supplies an opponent. It does not register or resume a job.
- Remote workers receive job copies and upload full checkpoints/models; the
  server remains authoritative for job state and model registration.

Do not commit databases, checkpoints, model weights, studies, credentials or local
virtual environments.
