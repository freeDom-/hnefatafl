# Delivery checklist

Releases are created only after the corresponding implementation and validation.
See GitHub Releases and `docs/releases/` for exact evidence and limitations.

- [x] Foundation: architecture, locked dependencies and development tools (`v0.1.0`)
- [x] Engine: Tablut, Brandubh and Copenhagen, 61 variant-specific tests and documentation (`v0.2.0`)
- [ ] Playable application: responsive local play, authoritative API and persistence
- [x] Traditional AI: baseline, alpha-beta, MCTS, profiles and tournaments (`v0.3.0`)
- [x] Training: self-play updates, exact checkpoint resume, models, Optuna and CPU smoke tests (`v0.3.0`)
- [ ] Statistics/usability: replay, profiles, filters and training management
- [ ] Verification: complete suites, browser flows, production startup and documentation

Initial-release acceptance additionally requires model-backed full games and safe
model import. Online multiplayer is a later release. GPU validation requires suitable
hardware and is tracked separately from CPU functionality.
