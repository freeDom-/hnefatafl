# Game, local-play and replay authority

[Development index](README.md) · [Gameplay guide](../game/gameplay.md) · [API](api/README.md) · [Architecture](architecture.md)

## Rules authority

`hnefatafl/engine.py` is the only rules implementation. It owns legal moves,
captures, royal capture/escape, variant special squares, repetition, outcomes and
immutable state. It has no HTTP, database or PyTorch dependency. Server routes,
replay, AI and browser analysis all use this engine.

## Account games

Single-player and local 1v1 games are stored as account records with a category.
Every mutation carries the expected `version`; a stale write fails. Human seats store
the account ID where linked. The server validates moves, AI turns and terminal state.
Restart is restricted to unfinished games under the current policy and starts a
fresh seed; completed games cannot be restarted through the play API.

Local 1v1 creates two labelled human seats owned by one account. It never creates an
online match, rating event or vs-AI statistic. Local records are owner-scoped and
readable only through the local/history projections.

## Replay and history

Replay returns validated immutable positions for a saved game. It does not seek,
branch or mutate the stored game. History categories are `online`,
`account-single-player` and `local`; admin projections can span categories while
ordinary users see their own participating/owned records.

A terminal online board may display a king on its final square as a presentation
overlay; the authoritative position/result remains the recorded engine state.

## Analysis attachments

Single-player games and completed online records can store analysis preferences,
agent reports and receipts. Live online games do not accept analysis computation.
Analysis reports retain their effective configuration, model identity, seed and
diagnostics. See [analysis authority](analysis.md).

## Adding an agent

Extend the AI profile catalog and `choose_move` resolution together, then add
validation/tests for the new algorithm or wrapper. Presets are resolved server-side;
browser clients must not supply arbitrary code or filesystem paths. Keep a legal
fallback for deadline/cancellation and preserve the configured seed/effective
settings in saved games/analysis. Search bounds are canonical in
[agent search parameters](../training/parameters.md#agent-search-settings).
