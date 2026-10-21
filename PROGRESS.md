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
