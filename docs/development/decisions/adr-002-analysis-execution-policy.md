# ADR 002: Deployment-owned analysis execution policy

**Status:** accepted for the opt-in browser-analysis work.

## Context

Earlier proposals exposed a player-facing Local/Server/Auto selector and a browser
analysis page. That creates confusing fallback behavior, lets a client claim
capacity, and makes security/performance evidence depend on a user preference.
The server already owns model publication and runtime delivery.

## Decision

- `HNEFATAFL_ANALYSIS_MODE` resolves once at startup to `browser_only`,
  `browser_preferred`, `server_only` or `disabled`.
- There is no player-facing provider/mode selector and no separate browser-analysis
  page. The existing analysis UI is reused.
- `browser_only` rejects all server analysis computation.
- `browser_preferred` keeps server endpoints only as a strictly bounded
  compatibility fallback; client claims never grant capacity.
- `disabled` suppresses optional analysis while gameplay, validation, AI opponents,
  training and tournaments continue.
- The legacy `HNEFATAFL_BROWSER_ANALYSIS_ENABLED` flag owns delivery compatibility;
  an explicit mode wins.
- Published models are administrator-authorized, validated and immutable. Models
  are never published automatically.
- Browser execution uses only the authoritative serialized positions and the
  existing engine/MCTS semantics.

## Consequences

- A deployment can ship a browser-first experience without exposing a confusing
  user setting.
- Server fallback limits, CSP changes and runtime/model distribution are
  deployment concerns with their own tests.
- The feature remains opt-in because competitive checkpoints, physical devices,
  verified GPU execution, sustained memory and distribution licensing are not
  accepted.
- A future production default requires Phase 6/7 acceptance, not just merged code.

## Revisit if

Operational evidence shows that deployment-level policy cannot express a required
tenant/account boundary. Any player-facing override would need a new authorization
model and cannot reuse this decision unchanged.
