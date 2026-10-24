# Documentation

Choose a task below. Current guides describe the implementation; release notes,
validation records and original requirements have separate historical roles.
Commands assume the repository root unless a different directory is shown.

## Getting started and gameplay

- [Repository introduction and quick start](../README.md)
- [Gameplay: ruleset comparison, controls, replay and persistence](GAMEPLAY.md)
- [Profiles, records, model suggestions, language and appearance](PERSONALIZATION.md)

## AI opponents, neural search wrappers and position analysis

- [Profiles, search limits, neural wrappers and reproducibility](AI.md)
- [Background analysis, move advisors and interpretation of winning chances](ANALYSIS.md)
- [Persistent identity runes](IDENTITIES.md)

## Tournaments and statistics

- [Round-robin setup, balanced sides, scoring, matrices and replay](TOURNAMENTS.md)
- [Hall of records and seed-independent AI statistics](PERSONALIZATION.md#ai-configurations-in-the-hall-of-records)

## Training, evaluation and hyperparameter optimization

- [Training dashboard, analysis views, navigation and chart interpretation](TRAINING_DASHBOARD.md)
- [First training run, learning targets, configuration bounds, evaluation and Optuna](TRAINING.md)
- [Recommended board-specific plans and retained learning graphs](TRAINING_PLAN.md)

## GPU optimization, performance and benchmarking

- [Optional OpenVINO Intel GPU inference, setup and complete-iteration benchmarks](OPENVINO.md)

- [Shield maiden strategy, strength and runtime evidence](SHIELD_MAIDEN.md)
- [CPU/CUDA/MPS setup, concurrent MCTS, batch sizes, metric units and benchmark CLI](PERFORMANCE.md)

## Training checkpoints, job storage, model management and migration

- [Storage layout, full versus inference checkpoints, resumption, model lifecycle and migration](TRAINING_DATA.md)
- [Consistent full-instance backup and restoration](TRAINING_DATA.md#consistent-backup-and-restoration)

## Remote training workers

- [Enrollment, worker CLI, parallel slots, transfers, leases and access control](REMOTE_TRAINING.md)

## Installation, deployment, security and backups

- [Accounts, sessions, CSRF and API permissions](SECURITY.md)
- [Local production/development, Linux/LXC, systemd, reverse proxy, upgrades and rollback](DEPLOYMENT.md)
- [Backup and relocation safeguards](TRAINING_DATA.md#consistent-backup-and-restoration)

## Architecture, development and testing

- [Current architecture and module boundaries](../ARCHITECTURE.md)
- [Developer commands, CLI reference and test workflow](DEVELOPMENT.md)
- [Repository working conventions](../AGENTS.md)
- [Validation overview and historical evidence](VALIDATION.md)
- [Release procedure](RELEASING.md) (only for an explicitly authorized release)

## Rulesets, release history and historical requirements

- [Brandubh](rules/brandubh.md), [Tablut market rules](rules/tablut.md),
  [Copenhagen](rules/copenhagen.md): authoritative source citations and digital conventions.
  These paths are also read by the application at runtime.
- [Historical delivery checklist](../PROGRESS.md): milestone summaries, not a second current manual.
- [Original specification](REQUIREMENTS.md): requested scope, including future/unimplemented goals.
- [King-token design record](designs/king-options.md): selected visual concept, not rules behavior.

| Release | Delivered scope and original evidence |
| --- | --- |
| [0.27.0](releases/0.27.0.md) | Node-local MCTS prediction reuse, PUCT traversal and reproducible fixed-model measurements |
| [0.26.0](releases/0.26.0.md) | Exact remote worker version matching, administrator overrides and active-lease continuity |
| [0.25.0](releases/v0.25.0.md) | Editable job parameter reuse and recorded job/model self-play totals |
| [0.24.0](releases/v0.24.0.md) | D4 symmetry foundations, optional exact-input cache and measured inference deduplication |
| [0.23.0](releases/v0.23.0.md) | Optional OpenVINO Intel GPU self-play inference with portable PyTorch learning |
| [0.22.1](releases/v0.22.1.md) | Hide revoked remote workers and correct worker connection commands |
| [0.22.0](releases/v0.22.0.md) | Mandatory accounts, scoped permissions and production Linux/LXC deployment |
| [v0.21.0](releases/v0.21.0.md) | Structured AI workshop training dashboard |
| [v0.20.0](releases/v0.20.0.md) | Shield maiden heuristic, integration and measured trade-offs |
| [v0.19.0](releases/v0.19.0.md) | Single evaluation opponent, portable champion selection and compact worker logs |
| [v0.18.0](releases/v0.18.0.md) | Portable training jobs, contained Optuna studies and faithful checkpoint resumption |
| [v0.17.0](releases/v0.17.0.md) | Concurrent self-play, batched inference and qualified CPU benchmarks |
| [v0.16.0](releases/v0.16.0.md) | Editable resumption and three neural search wrappers |
| [v0.15.0](releases/v0.15.0.md) | Background analytics and faithful replay |
| [v0.14.0](releases/v0.14.0.md) | Battlefield opponent win matrices |
| [v0.13.0](releases/v0.13.0.md) | Optimizer overrides and agent evaluations |
| [v0.12.0](releases/v0.12.0.md) | Remote training workers |
| [v0.11.0](releases/v0.11.0.md) | Model/job management and tournament identities |
| [v0.10.0](releases/v0.10.0.md) | Complete setup, checkpoint review and identity runes |
| [v0.9.0](releases/v0.9.0.md) | Analysis, training plans, localization and wood theme |
| [v0.8.0](releases/v0.8.0.md) | Four-player tournaments and sortable records |
| [v0.7.0](releases/v0.7.0.md) | Fresh seeds and collection cleanup |
| [v0.6.0](releases/v0.6.0.md) | Saved tournaments |
| [v0.5.0](releases/v0.5.0.md) | Personalization and inspectable opponent records |
| [v0.4.0](releases/v0.4.0.md) | Browser play and training workshop |
| [v0.3.0](releases/v0.3.0.md) | Search and self-play learning |
| [v0.2.0](releases/v0.2.0.md) | Three deterministic rulesets |
| [v0.1.0](releases/v0.1.0.md) | Development foundation |

Historical release notes remain unchanged; test counts belong to their stated
scope. See [Validation](VALIDATION.md) before treating a historical check as
current hardware, browser or model-strength evidence.
