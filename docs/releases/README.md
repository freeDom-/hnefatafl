# Releases

Release notes are immutable historical records. They describe the scope,
compatibility and validation of their own tagged release; they are not the
current manual and are not relabelled after later changes.

## Current release process

- [Release process](release-process.md): tagging, assets, checks and publication
  authorization.
- [Release readiness](readiness.md): outstanding combined-acceptance and deployment
  gates. Merged code or a green pull request is not deployment authorization.
- [Validation guidance](validation.md): how to read historical evidence and what
  current tests cover.
- [Historical Arena completeness snapshot (2026-11-20)](arena-snapshot-2026-11-20.md):
  point-in-time matrix; not current status.

## Release notes

| Release | Delivered scope |
| --- | --- |
| [0.42](0.42.md) | League Arena, randomised/scheduled player tournaments, role win rates, manual portrait unlocking, Online Play navigation, PolyForm licensing and opt-in browser-analysis research |
| [0.41.1](0.41.1.md) | Deployment-gate-safe deferred tournament recovery and readiness diagnostics |
| [0.41.0](0.41.0.md) | Arena v2 four-theme shell, Local 1v1, player tournaments, portraits/achievements, public spectator Arena and performance work |
| [0.40.0](0.40.0.md) | Champion-aware HyperController, selectable learning metrics and configurable analytics budgets |
| [0.39.0](0.39.0.md) | Schedule-Free AdamW, tournament role analytics and legal openings |
| [0.38.0](0.38.0.md) | Account profiles, optional-field publication and current player identities |
| [0.37.0](0.37.0.md) | Champion-aware adaptation, outcome charts and reliable large job transfers |
| [0.36.0](0.36.0.md) | Reliable tuning, online learning-rate adaptation, model reuse and longer analysis windows |
| [0.35.0](0.35.0.md) | Custom single-player opponents, training parameter help and parallel CPU search |
| [0.34.0](0.34.0.md) | Live training monitoring, fixed-role tournaments, batched evaluation and faster search |
| [0.33.0](0.33.0.md) | Responsive play/replay, personal statistics, shared UI styles and online clocks |
| [0.32.0](0.32.0.md) | Verified accounts, rated online multiplayer, personal dashboard and interface scale |
| [0.31.0](0.31.0.md) | Optimized alpha-beta search, optional symmetry and portable tournament setup |
| [0.30.0](0.30.0.md) | Shared notifications and a preserved analysis tab |
| [0.29.0](0.29.0.md) | Portable config import and merged evaluation series |
| [0.28.0](0.28.0.md) | Optional D4 training augmentation |
| [0.27.0](0.27.0.md) | MCTS prediction reuse and PUCT traversal |
| [0.26.0](0.26.0.md) | Exact remote-worker version matching and lease continuity |
| [0.25.0](0.25.0.md) | Editable job parameter reuse and self-play totals |
| [0.24.0](0.24.0.md) | D4 symmetry foundations and exact-input caching |
| [0.23.0](0.23.0.md) | Optional OpenVINO inference |
| [0.22.1](0.22.1.md) | Revoked-worker hiding and connection-command fixes |
| [0.22.0](0.22.0.md) | Mandatory accounts, permissions and production Linux/LXC deployment |
| [0.21.0](0.21.0.md) | Structured training dashboard |
| [0.20.0](0.20.0.md) | Shield maiden heuristic and measured trade-offs |
| [0.19.0](0.19.0.md) | Single evaluation opponent and portable champion selection |
| [0.18.0](0.18.0.md) | Portable training jobs, contained Optuna studies and resumption |
| [0.17.0](0.17.0.md) | Concurrent self-play, batched inference and CPU benchmarks |
| [0.16.0](0.16.0.md) | Editable resumption and neural search wrappers |
| [0.15.0](0.15.0.md) | Background analytics and faithful replay |
| [0.14.0](0.14.0.md) | Battlefield opponent win matrices |
| [0.13.0](0.13.0.md) | Optimizer overrides and agent evaluations |
| [0.12.0](0.12.0.md) | Remote training workers |
| [0.11.0](0.11.0.md) | Model/job management and tournament identities |
| [0.10.0](0.10.0.md) | Complete setup, checkpoint review and identity runes |
| [0.9.0](0.9.0.md) | Analysis, training plans, localization and wood theme |
| [0.8.0](0.8.0.md) | Four-player tournaments and sortable records |
| [0.7.0](0.7.0.md) | Fresh seeds and collection cleanup |
| [0.6.0](0.6.0.md) | Saved tournaments |
| [0.5.0](0.5.0.md) | Personalization and inspectable opponent records |
| [0.4.0](0.4.0.md) | Browser play and training workshop |
| [0.3.0](0.3.0.md) | Search and self-play learning |
| [0.2.0](0.2.0.md) | Three deterministic rulesets |
| [0.1.0](0.1.0.md) | Development foundation |

GitHub Releases is the canonical publication channel for release notes and
verified archives. This directory mirrors the notes for repository-local and
offline use. Do not delete tags or rewrite published release notes.

## Current limitations

See [readiness](readiness.md) and the latest release notes. In particular, CPU
workflows are verified; CUDA/MPS/Intel execution requires suitable hardware and is
not universally validated. No strong pretrained model or playing-strength claim
ships. Browser analysis remains opt-in and not production-accepted.
