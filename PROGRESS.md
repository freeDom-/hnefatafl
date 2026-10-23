# Delivery checklist

Historical milestone record: each section describes its release at delivery,
including limitations later resolved. For current behavior use the
[documentation index](docs/README.md); for exact original evidence use
[release notes](docs/README.md#rulesets-release-history-and-historical-requirements)
and [Validation](docs/VALIDATION.md). This checklist is not a current user manual.

Releases are created only after the corresponding implementation and validation.
See GitHub Releases and `docs/releases/` for exact evidence and limitations.

- [x] Foundation: architecture, locked dependencies and development tools (`v0.1.0`)
- [x] Engine: Tablut, Brandubh and Copenhagen, 61 variant-specific tests and documentation (`v0.2.0`)
- [x] Playable application: responsive local play, authoritative API and persistence (`v0.4.0`)
- [x] Traditional AI: baseline, alpha-beta, MCTS, profiles and tournaments (`v0.3.0`)
- [x] Training: self-play updates, exact checkpoint resume, models, Optuna and CPU smoke tests (`v0.3.0`)
- [x] Statistics/usability: replay, profiles, filtered rates and training/model management (`v0.4.0`)
- [x] Verification: 173 Python tests, 5 frontend unit tests, 5 browser tests, production startup and documentation (`v0.4.0`)

## Records and personalization (`v0.5.0`)

- [x] Delete models through the UI while retaining completed history and training checkpoints
- [x] Separate AI variants by effective parameters and inspect them in records/statistics
- [x] Select existing humans in new-game dropdowns and delete profiles without losing history
- [x] Suggest unused Viking model names in the training workshop
- [x] Order all battlefield selectors by board size
- [x] Persist light/dark/device mode, classic/Viking/wood tokens and oak/walnut/slate boards
- [x] Validate additive schema 2 upgrades, deletion races, legacy defaults and retained replay
- [x] Pass 196 Python, 14 frontend unit and 10 real-API browser checks

Model-backed full games and safe model import pass on every ruleset. Two redundant
mobile training checks are deliberately skipped; actual training runs on desktop
and responsive gameplay/appearance run on mobile. See `docs/VALIDATION.md` for evidence.

External verification remains explicit: CUDA/MPS hardware execution, the inaccessible
Copenhagen primary rule page, and target-host systemd/proxy deployment. The selected
Copenhagen interpretation and repetition convention are documented. Online multiplayer
is a later release. Smoke models make no competitive-strength claim.

## Saved tournaments (`v0.6.0`)

- [x] Set up two named AI opponents, compatible models, battlefields and balanced game budgets
- [x] Run tournaments outside the API process and save progress after each finished match
- [x] View retained standings, per-battlefield scores, parameters, match logs and legal replays
- [x] Stop with partial results and delete inactive tournaments after confirmation
- [x] Protect active model references and preserve regular game/training collections
- [x] Upgrade schema 1/2 to 3 additively and detect interrupted workers
- [x] Pass 231 Python tests, 17 frontend unit tests and 13 real-API browser checks

Three duplicate mobile worker checks are intentionally skipped; mobile setup,
results, replay and deletion run. Tournaments compare two opponents; larger brackets
and automatic resumption are future work. See `docs/TOURNAMENTS.md`.

## Fresh games and manageable records (`v0.7.0`)

- [x] Generate and retain fresh ordinary-game/restart seeds; support explicit fixed seeds
- [x] Show saved training seeds on model cards, distinguishing evaluation seeds
- [x] Suggest compatible models from completion-aware baseline evaluation evidence
- [x] Preserve manual opponent choices and clear hidden model references when switching algorithms
- [x] Place statistics before separate collapsible, paged game history
- [x] Delete games with version guards and recalculate all statistics
- [x] Preview every unfinished model reference and offer explicit discard without results/history
- [x] Preserve completed replay/checkpoints, reject changed usage and roll back failed removals
- [x] Pass 252 Python tests, 35 frontend unit tests and 16 real-API browser checks

Four duplicate mobile worker checks are intentionally skipped. Schema 3 remains
unchanged. See `docs/PERSONALIZATION.md` for collection management and ranking rules.

## Four-player tournaments and clearer records (`v0.8.0`)

- [x] Support two to four tournament opponents with balanced round-robin pairings
- [x] Track both participants, per-player scores and pairing-aware progress; retain old two-player replay
- [x] Sort every column in battlefield and player/AI statistics numerically or by name
- [x] Filter player performance and recorded-game participant names independently
- [x] Keep AI performance to one compact row with a left-side full-parameter toggle
- [x] Default fresh/invalid preferences to Carved wood tokens and Old walnut boards
- [x] Preserve valid saved appearance preferences
- [x] Present three king concepts and implement selected design 1, Fjord Diadem, for the wooden king
- [x] Pass 264 Python tests, 48 frontend unit tests and 20 real-API browser checks

Four duplicate mobile worker checks remain intentionally skipped. The database
remains at schema 3 with no migration. See `docs/designs/king-options.md` for the
selected king and `docs/TOURNAMENTS.md` for round-robin scheduling.

## Agent insight and a northern workshop (`v0.9.0`)

- [x] Retain independent current-player AI estimates, provenance and replay snapshots
- [x] Offer an optional advisor and show its searched legal move on the board
- [x] Protect advisor model references and reject stale/branched analysis writes
- [x] Remember the last successful game mode; default performance to most wins first
- [x] Group equal AI settings across seeds while preserving individual game seeds
- [x] Suggest the strongest compatible tournament model and distinguish equal names by iteration/architecture/ID
- [x] Suggest editable mythological tournament names with reroll and duplicate avoidance
- [x] Localize game, records, workshop, tournaments and rules in English/German
- [x] Add independent wood color mode with northern motifs and preserved preferences
- [x] Supply board-specific training plans and document network/learning tradeoffs
- [x] Plot live retained learning history and optionally keep every strict new best model
- [x] Pass 302 Python, 67 frontend unit and 24 real-API browser checks
- [x] Verify actual running graph growth, CPU best-model retention and schema-4 startup preserving all seven games

Four duplicate mobile worker checks remain intentionally skipped. Recommended
plans and uncalibrated agent estimates do not establish stronger play. CUDA/MPS
and long independent strength comparisons remain unverified. See `docs/ANALYSIS.md`
and `docs/TRAINING_PLAN.md` for interpretation, scope and budgets.

## Reliable setup and persistent identities (`v0.10.0`)

- [x] Synchronize selected model IDs/names and show canonical recorded model names
- [x] Restore the complete last successful game setup, with safe invalid/deleted-option fallbacks
- [x] Review full training checkpoints and choose the total iteration target in the UI
- [x] Resume exact CPU optimizer/replay/random state into a new job without changing the source
- [x] Suggest distinct editable tournament player names with the shared dice reroll control
- [x] Remove decorative heading overlines and group header language/appearance controls
- [x] Persist unique player/model/agent runes and tournament seat identities across views/deletion
- [x] Upgrade schema 4 to 5 while preserving all seven existing game records
- [x] Pass 313 Python, 74 frontend unit and 26 real-API browser checks

Four duplicate mobile worker checks remain intentionally skipped. Model cards
resume the originating job's latest full checkpoint; inference-only imports do
not contain exact training state. Rune combinations extend the 24 single-rune
alphabet without reusing identity assignments. See `docs/IDENTITIES.md` and
`docs/TRAINING.md` for persistence and resumption details.

## A manageable model workshop (`v0.11.0`)

- [x] Rename catalog models while preserving IDs, runes, weights and historical names
- [x] Delete inactive training jobs/checkpoints/logs independently of registered models and saved games
- [x] Protect live workers, checkpoint consumers and referenced optimization studies during deletion
- [x] Load recorded training parameters into the form, with explicit handling of missing values
- [x] Accept valid recorded numeric values in the advanced form without decimal-step errors
- [x] Show recorded network, iteration, self-play, search and learning settings on model cards
- [x] Remove tournament participant aliases and use canonical model/profile names and agent runes
- [x] Retain path-free model provenance in saved tournaments and display it after source removal
- [x] Pass 327 Python tests, 81 frontend unit tests and 26 real-API browser checks

Schema 5 and checkpoint formats remain unchanged. Catalog renames preserve
historical snapshots and exported checkpoint bytes. Deleting a source job
permanently removes its exact resumption state while retaining inference copies.

## Remote training workers (`v0.12.0`)

- [x] Enroll named remote workers with hashed credentials, capacity and hardware reports
- [x] Queue independent remote training jobs while preserving local training behavior
- [x] Run concurrent worker subprocesses and transfer real progress, models and full checkpoints
- [x] Scope expiring leases, make claims/reports/finish retry-safe and reject late results
- [x] Cancel queued/active jobs, revoke workers and retain uploaded resumption state
- [x] Resume on a chosen execution target with an independent input checkpoint copy
- [x] Show simultaneous jobs, worker status and retained charts in English/German
- [x] Verify two concurrent CPU jobs, actual updates, remote resumption and server-only inference
- [x] Pass 355 Python tests, 88 frontend unit tests and 28 real-API browser checks
- [x] Upgrade schema 5 to 6 while preserving seven games, four model entries and eight runes

Schema 6 adds remote-worker storage without rewriting existing games or model
formats. Workers use outbound HTTP(S); private networking or a restricted HTTPS
proxy remains necessary for the existing administrative APIs. Remote Optuna,
automatic GPU allocation and cross-machine optimization of a single model are
outside this milestone. See `docs/REMOTE_TRAINING.md` for setup and lifecycle.

## Adjustable resumption and faithful agent evaluations (`v0.13.0`)

- [x] Prefill optimizer settings from effective checkpoint parameter groups
- [x] Edit learning rate, weight decay and SGD momentum when resuming locally or remotely
- [x] Restore accumulated optimizer buffers before applying reviewed overrides
- [x] Preserve source checkpoints, replay, random state and retained learning history
- [x] Share profile defaults and explicit agent settings between play and analysis
- [x] Report searched neural values and distinguish requested/completed alpha-beta depth
- [x] Verify exact saved model architecture and independent configured agent depths
- [x] Hide Restart for completed records and reject the API action even during replay
- [x] Fix remote-worker panel/card spacing on desktop and mobile, including German
- [x] Verify 369 backend cases, 101 frontend unit tests and 30 real-API browser checks

Schema 6 and checkpoint formats remain unchanged. Momentum applies to SGD;
Adam/AdamW retain their saved moments. Previous immutable analysis reports retain
their original values and source labels. Time limits still bound search, and
agent estimates do not establish calibrated winning probabilities or model strength.

## Battlefield opponent win matrices (`v0.14.0`)

- [x] Show every configured opponent on both axes of each battlefield's win table
- [x] Count row wins against column opponents across both side assignments
- [x] Exclude draws/unfinished matches and show runes on the diagonal
- [x] Keep repeated names/models distinct by numbered tournament seat
- [x] Reconstruct running, partial and reopened results from retained match records
- [x] Preserve legacy two-player side assignments without changing storage
- [x] Localize English/German labels and retain accessible table headers/captions
- [x] Support keyboard/touch scrolling with fixed row headers and a persistent title
- [x] Pass 369 backend cases, 107 frontend tests and seven targeted tournament browser checks

Schema 6, tournament scoring and saved records remain unchanged.

## Background analytics and faithful game playback (`v0.15.0`)

- [x] Capture actual AI move-search estimates atomically with successful moves
- [x] Evaluate other agents without blocking human input or AI requests
- [x] Configure live/replay advisors in a persistent in-game Analytics section
- [x] Publish progressive hints and estimates with advisor-revision guards
- [x] Hide winning chances independently of move advice
- [x] Preserve late reports for retained positions and reject deleted/branched work
- [x] Hide completed-game AI controls, including during history replay
- [x] Dismiss dialogs on an outside pointer press rather than release
- [x] Fix Shield bearer/Jarl/Seer presets across games and tournaments; retain Custom settings
- [x] Preserve captured kings visually without changing engine rules
- [x] Pass 401 backend cases, 126 frontend tests and 38 desktop/mobile browser checks
- [x] Back up and migrate workspace schema 5 to 7 without changing retained records or model files

Schema 7 adds analytics preferences and revisioned advisor reports. Existing player
opinions retain their provenance; explicit new replay-advisor opinions are separate.
Search deadlines remain effective, and displayed estimates are uncalibrated expected
scores. See `docs/ANALYSIS.md` for persistence, sources and API details.

## Editable resumption and neural search wrappers (`0.16.0`)

- [x] Initialize all resume controls from effective saved training settings
- [x] Keep ruleset, channels, residual blocks, random seed and optimizer type read-only
- [x] Apply editable self-play, learning, evaluation, device and checkpoint settings to resumed jobs
- [x] Preserve optimizer buffers, replay, RNG state, history and best-model tracking before applying overrides
- [x] Keep iterations as a validated target total and retain local/remote checkpoint compatibility
- [x] Select MCTS, alpha-beta or direct policy moves for trained models in games and tournaments
- [x] Reuse existing search algorithms and honor the selected wrapper in position analysis
- [x] Default missing `search_wrapper` settings to MCTS and retain hidden UI search values
- [x] Pass 90 targeted backend tests, 68 frontend unit tests, static checks and the production build

Schema 7 and checkpoint/model formats remain unchanged; no migration is required.
Validation covers CPU execution and short real training/resumption checks. The full
browser end-to-end suite was not rerun locally for this milestone. GPU execution,
long training and model-strength comparisons were not performed. See `docs/releases/v0.16.0.md` for scope, compatibility and update commands.

## Concurrent self-play and batched inference (`v0.17.0`)

- [x] Interleave independent games and resumable MCTS contexts through one shared network
- [x] Batch pending neural requests while preserving legal policies, search backups and training targets
- [x] Retain sequential CPU/CUDA execution and select batching automatically only on CUDA
- [x] Configure execution mode, game concurrency and inference batch size in Start/Resume, CLI and remote jobs
- [x] Preserve existing checkpoints, optimizer/replay/RNG state, history and best-model tracking
- [x] Retain throughput, actual batch sizes, outcomes and distinct wall/CPU/optional CUDA timing metrics
- [x] Display live/completed-job performance and throughput history alongside existing learning graphs
- [x] Provide a reproducible, small sequential/batched CPU/CUDA comparison command
- [x] Pass 478 backend tests and 137 frontend tests, static checks and build; skip one unavailable CUDA test
- [x] Complete batched CPU CLI training with four updates, checkpoint reload and legal registered-model play
- [x] Measure equal-work CPU batching: 0.98× (4 channels/0 blocks) and 0.65× (32 channels/2 blocks), with fewer forward passes

Schema 7, network architecture and checkpoint formats remain unchanged. Missing
execution fields retain compatible defaults; updated remote-worker code is needed
to execute new settings. Validation is CPU-only; CUDA/MPS behavior and GPU speedups
remain unverified. Passing short training and throughput checks does not establish
stronger play. The small CPU comparisons did not show a speed improvement; CPU
automatic execution stays sequential. See `docs/releases/v0.17.0.md` for measured
work, scope, update commands and limits.

## Portable training jobs (`v0.18.0`)

- [x] Remove resume-only preset selection while preserving effective editable settings
- [x] Export inactive jobs with versioned/checksummed metadata, checkpoints, history, metrics, logs and models
- [x] Validate archives in staging and restore fresh inactive job IDs with local paths and model associations
- [x] Deduplicate inference models and roll back newly published state on controlled failures
- [x] Migrate single contained Optuna studies and rebase trial checkpoints for Resume study
- [x] Reject unsafe archives, active jobs/trials and shared/external/incomplete study dependencies
- [x] Keep English/German workshop loading, errors, history selection and model refresh
- [x] Pass 527 backend tests (one CUDA skip), 143 frontend tests, static checks and build
- [x] Pass 49 final migration tests and two desktop/mobile browser export/import/resume cases

Exact validation and migration limits are recorded in `docs/releases/v0.18.0.md`.
Schema 7 and tensor checkpoint formats remain unchanged. Archives contain only
server-retained remote artifacts and never restore execution ownership. Functional
CPU migration/resumption establishes further optimizer updates, not model strength.


## Configurable evaluation and model selection (`v0.19.0`)

- [x] Select one Shield bearer, Jarl or Champion evaluator with independent cadence/budgets
- [x] Edit evaluation strategy on full-state resumption with English/German conditional controls
- [x] Keep strict fixed-benchmark records separate by effective protocol; retain ties and flag saturation
- [x] Promote immutable champions only with complete side-balanced pairs and a conservative confidence bound
- [x] Preserve legacy baseline metadata, optimizer/replay/RNG/history and champion state in tensor-only checkpoints
- [x] Separate graph series/side scores and show promotion, incumbent, fixed records and evaluation durations
- [x] Bound CLI log events and stream large remote histories without dropping records
- [x] Report actual requested/completed search work, shortcuts, cutoffs and self-play side outcomes
- [x] Pass `make check`: 562 backend tests, one CUDA skip, 148 frontend tests, static checks and production build
- [x] Pass 40 real-API desktop/mobile browser tests (four existing skips)
- [x] Verify actual CPU optimizer updates while switching Shield bearer → Jarl → Champion

See [release evidence](docs/releases/v0.19.0.md) for exact budgets and measured
log/evaluation sizes. Evaluation remains sequential; no GPU speedup or stronger
model is claimed. The production RTX 3060 checkpoint and CUDA/MPS hardware were
unavailable; representative iteration-100 migration and subsequent learning pass.

## Shield maiden (`v0.20.0`)

- [x] Distinct Intermediate `shield_maiden` one-ply profile, with no model or tensor dependency
- [x] Rules-aware royal capture/escape geometry, clear/contested routes, mobility, material and piece safety
- [x] Fixed/custom games, tournaments, advisors, records and English/German presentation
- [x] One selected training evaluator, versioned effective context, separate fixed benchmarks and preserved resume state
- [x] Held-out 128/64/64-game balanced comparison: 86.7% Brandubh, 87.5% Tablut, 54.7% Copenhagen
- [x] Comparable matched decision latency: mean 0.958× baseline, p95 0.974×
- [ ] Universal evaluation-throughput target: Brandubh neural evaluation was 3.79× slower; Tablut/Copenhagen were 0.91×/0.89×
- [ ] Established Copenhagen strength improvement: paired interval 48.4–62.5% includes 50%; attacking play remains weak

See [strategy and raw benchmark evidence](docs/SHIELD_MAIDEN.md) and
[release notes](docs/releases/v0.20.0.md) for exact validation and limitations.

Validation: `make check` passed (611 Python tests, one CUDA-only skip; 151 frontend
unit tests, formatting/lint, types, build and docs). Full real-server browser
suite: 44 passed, four deliberate mobile skips covered on desktop. Actual CPU
remote-worker maiden evaluation, optimizer/replay-preserving resume and separate
benchmark histories passed; no GPU validation was performed.

## Structured training dashboard (`v0.21.0`)

- [x] Compact workshop header and full-width collapsible creation with retained form state
- [x] Active/recent initial selection, searchable/filterable job rail and mobile selector
- [x] Persistent status/actions and Overview, Learning, Evaluation, Performance and Details tabs
- [x] Shared iteration ranges, keyboard/pointer SVG values and lazy accessible history tables
- [x] Separate effective evaluation contexts/incumbents, sparse observations and promotion events
- [x] Group throughput/MCTS/inference metrics and distinguish sequential phases from overlapping activity
- [x] Preserve tuning/current-trial evidence, model/job management, remote workers and revision guards
- [x] English/German controls, theme contrast and responsive desktop/tablet/mobile layouts
- [x] `make check`: 611 Python passes, one CUDA skip; 164 frontend passes; static checks, docs and build
- [x] Full real-server browser suite: 50 passes, four existing desktop-covered mobile skips
- [x] Visual inspection of all analysis views, legacy/error/empty states and wood/light/dark themes

See the [dashboard guide](docs/TRAINING_DASHBOARD.md) and
[release evidence and screenshots](docs/releases/v0.21.0.md). Training algorithms,
API/checkpoint/job formats and worker protocols are unchanged. Fixture screenshots
illustrate the interface; no GPU validation, long campaign or stronger-model claim
is made. Existing server-bounded log tails remain part of job-list polling.


## Secure accounts and production deployment (`v0.22.0`)

Persistent accounts, mandatory browser/API authentication, centralized USER/ADMIN
permissions, scoped game/player records, local administrator provisioning, schema 8,
Debian/Proxmox installation, protected systemd deployment, stable-release updates,
verified backups and guarded rollback are implemented. See the
[implementation/validation record](docs/validation/security-deployment-2026-10-23.md)
for all changed files, 57 API grants, migration details, exact checks, commands and
remaining hardware/target-host limits. See [v0.22.0 release notes](docs/releases/v0.22.0.md)
for installation and migration requirements. Existing release evidence above remains historical.
