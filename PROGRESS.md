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

Model-backed full games and safe model import pass on every ruleset. One redundant
mobile training browser check is deliberately skipped; actual training runs on desktop
and responsive gameplay runs on mobile. See `docs/VALIDATION.md` for exact evidence.

External verification remains explicit: CUDA/MPS hardware execution, the inaccessible
Copenhagen primary rule page, and target-host systemd/proxy deployment. The selected
Copenhagen interpretation and repetition convention are documented. Online multiplayer
is a later release. Smoke models make no competitive-strength claim.
