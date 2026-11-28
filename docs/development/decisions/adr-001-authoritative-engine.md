# ADR 001: One authoritative Python engine

**Status:** accepted.

## Context

The application supports local play, server-backed single-player, authoritative
online games, AI search, training, replay, tournaments and optional browser-local
analysis. A second rules implementation in TypeScript would drift on variant
details (royal capture, shieldwalls, repetition, escape squares) and could produce
contradictory results.

## Decision

- `hnefatafl/engine.py` is the only rules authority. It is pure and independent of
  HTTP, database and PyTorch.
- Server routes validate every move and persist the resulting immutable state.
- Replay and AI validation use the same engine.
- The browser receives serialized positions/history and renders them; it does not
  validate moves with a parallel rules engine.
- Browser-local analysis runs the existing Python engine and MCTS through a
  Pyodide worker. There is no JavaScript/Rust/C++ search engine.
- Optimistic `version` guards protect mutations; clients never overwrite a newer
  state.

## Consequences

- Rules tests use explicit variant positions and cover all three rulesets.
- A browser analysis failure cannot fall back to a different engine; it fails or
  uses the bounded server path under the deployment policy.
- The browser runtime must ship a pinned, digest-verified Python runtime and
  encoder, which is why browser analysis remains opt-in and phase-gated.
- Performance work optimizes one implementation rather than maintaining parity
  between two.

## Revisit if

The Python engine cannot meet a measured, accepted latency target on target
devices **and** a second implementation can pass exhaustive differential fixtures
for every ruleset and repetition case. That would still require an explicit
architecture decision; it is not implied by the current roadmap.
