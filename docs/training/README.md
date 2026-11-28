# Training and AI evaluation

The Workshop trains policy/value networks from self-play, evaluates them against
fixed opponents and manages the resulting models. Training is administrator-only
by default. The CLI provides the same compute workflow without the web UI.

| Topic | Page |
| --- | --- |
| First job, presets, monitoring, result and model | [Getting started](getting-started.md) |
| Evaluation opponents, Champion selection and score interpretation | [Evaluation](evaluation.md) |
| Optuna tuning, studies and best-parameter application | [Tuning](tuning.md) |
| Workshop pages, charts, job lifecycle, models, workers and AI Testing UI | [Interface](interface.md) |
| Algorithms and how they interact | [Algorithms](algorithms.md) |
| Canonical parameter, default and bound reference | [Parameters](parameters.md) |
| Recommended board-specific budgets | [Training plans](plans.md) |
| Checkpoints, registry, import/export, storage, backup and migration | [Models and data](models.md) |
| CPU/CUDA/MPS configuration, batching, caches and tuning | [Performance](performance.md) |
| Optional Intel OpenVINO inference | [OpenVINO](openvino.md) |
| Schedule-Free AdamW CUDA acceptance procedure | [Development: Schedule-Free CUDA](../development/schedulefree-cuda.md) |
| Optional online learning-rate adaptation | [Online adaptation](online-adaptation.md) |
| Remote worker setup, slots, transfers and recovery | [Remote workers](remote-workers.md) |
| Common failures and diagnostics | [Troubleshooting](troubleshooting.md) |

Start with [Getting started](getting-started.md). A smoke run proves updates/checkpoint
reload/legal play, not playing strength. Jobs and registered models are separate;
see [models](models.md) for storage, deletion, backup and migration.
