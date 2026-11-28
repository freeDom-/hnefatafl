# Analysis execution and receipts

[Development index](README.md) · [Replay guide](../game/replay-and-analysis.md) · [API](api/README.md)

## Sources

- The moving AI's estimate comes from its actual search and is saved before the
  game advances. The server does not run a preliminary search to fabricate a second
  opinion.
- Another agent may evaluate the same position independently in the background,
  including during a human turn.
- A selected move advisor runs a bounded search, returns a legal move and an
  expected-score estimate, and never executes the move.

## Revisions, receipts and persistence

Analysis is tied to a game/position revision. Stale requests, deleted positions,
branched replays and changed advisors cannot publish a result for a newer position.
Replay analysis receipts use account/category/game/position identity and are
idempotent; concurrent requesters may share computation while each eligible account
receives credit at most once.

Saved reports retain value, best move, source, effective configuration, derived
seed and available diagnostics. Missing older fields are omitted rather than
invented. Model references remain historical after model deletion; an unavailable
model is reported explicitly.

## Execution policy

The deployment resolves one policy at startup: `browser_only`,
`browser_preferred`, `server_only` or `disabled`. There is no player-facing provider
selector. `browser_only` rejects server analysis; `browser_preferred` keeps the
server as a bounded fallback; `disabled` suppresses optional analysis while
gameplay, replay, AI opponents, training and tournaments continue. The legacy
`HNEFATAFL_BROWSER_ANALYSIS_ENABLED` flag only controls delivery compatibility; an
explicit mode wins.

Browser execution uses the shared Python engine/search through a pinned Pyodide
worker and a validated ONNX model. There is no JavaScript rules/search engine.
Model publication is an explicit administrator action with immutable,
digest-verified artifacts. See [browser analysis](browser-analysis.md) and
[analysis policy ADR](decisions/adr-002-analysis-execution-policy.md).

## Eligibility and limitations

Only successful completed server analysis of a completed replay can count toward
replay achievements. Passive viewing, live/spectator analysis, partial/failed/
cancelled searches and browser-local reports do not. Displayed probabilities are
uncalibrated model/search scores, not measured win rates.
