# Delivery checklist

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
