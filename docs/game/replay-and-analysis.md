# Replay and position analysis

[Game index](README.md) · [Hall of records](interface/records.md#hall-of-records) · [Analysis internals](../development/analysis.md)

## Replay

Completed records open read-only in the Hall of records. Selecting a move changes only
the viewer's position; replay never branches, overwrites or restarts the saved game.
Live play pauses while viewing history, and completed records have no restart/undo
controls. Page controls are in the
[records interface](interface/records.md#replay-and-analysis).

## Analysis tab

Single-player games and completed records can show saved agent estimates and a
background move advisor. Live online games do not offer analysis.

Select an advisor in **Analytics → Move advisor** (desktop) or **Game options**
(mobile). **None** is the default. The advisor:

- suggests a legal move without playing it;
- shows an expected-score estimate from the current side's perspective;
- searches in the background while play continues;
- saves the result for replay.

AI players can retain independent opinions about the same position. The moving AI's
estimate comes from its actual search; other agents may evaluate independently.
The panel identifies the source, effective search settings and available diagnostics.

## Interpreting estimates

Winning chances are **uncalibrated expected scores**, not measured probabilities.
A value of 0.70 means the agent's search prefers that side, not that it wins 70% of
games. Draws count as half a win. A searched terminal win can disagree with a raw
neural value, and that disagreement is not a bug.

A high score from a short search or tiny sample is weak evidence. Use larger
tournaments or equal-time comparisons before claiming playing strength. See
[agent evaluation](../evaluation/agents.md) and
[benchmark methodology](../evaluation/methodology.md).

## Analysis availability

The deployment controls where optional analysis runs. Depending on configuration:

- analysis may execute in the browser;
- the server may provide a bounded fallback;
- analysis may be disabled while gameplay, replay, AI opponents and training
  continue normally.

There is no player-facing provider selector. If analysis is unavailable, the UI
reports that state rather than inventing an estimate. Browser-local execution uses
the existing Python engine/search through a pinned runtime; it is not a second
rules implementation.

## Replay achievement evidence

A successful completed server advisor report on a completed record can award a
distinct-replay achievement milestone. Passive viewing, live-game analysis,
partial/failed searches, spectator views and browser-local results do not count.
Awards and receipts are idempotent; concurrent requesters share work and each
eligible account receives credit once.

## Related

- [Agent settings and wrappers](../evaluation/agents.md): opponent types and search
  configuration.
- [Analysis implementation](../development/analysis.md): execution policy, API,
  receipts and persistence.
- [Browser analysis roadmap](../development/roadmap/browser-analysis.md): active
  feature status and external gates.
