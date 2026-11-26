# Ruleset league achievements validation

[Documentation index](../README.md) · [League policy](../LEAGUES.md) ·
[Achievement progress](../PROGRESSION.md)

Achievement catalog 4 defines fifteen permanent league milestones, five for each
supported ruleset. Schema 25 adds permanent peaks, a durable reconciliation queue
and bounded streaming cursor/evidence state. Historical verification resolves
effective official results from applied rating applications and durable
`league_games`; it does not trust cached contributions or final standings.

## Correctness and recovery semantics

- Ordinary appends are evaluated immediately only when no settled revision
  mismatch or earlier applied result gap exists.
- Every replay (correction, void or out-of-order replacement) uses the durable
  reconciliation path, including voids without an effective contribution sequence.
- Reconciliation waits for all pending settlements in the account/season/ruleset
  scope, then streams applied results in stable rating sequence.
- A season revision change invalidates accumulated evidence and restarts the
  scope. Awards cannot commit from mixed evidence generations.
- Administrative folding writes candidates only; bounded verification runs after
  authoritative catch-up.
- A superseded historical peak that was never recorded cannot be reconstructed.

## Automated coverage

Focused backend run:

```sh
.venv/bin/python -m pytest tests/test_league_achievements.py \
  tests/test_league_policy.py tests/test_progression.py tests/test_leagues.py \
  tests/test_league_migrations.py tests/test_league_restore.py \
  tests/test_online_migrations.py tests/test_league_api.py \
  tests/test_online_core.py tests/test_deployment.py -q
```

Result: **280 passed**.

Coverage includes the required out-of-order replay scenario (initial loss, four
wins, deferred loss, fifth win, void, deferred correction and 14-point final peak),
multiple pending settlements, earlier/later gaps, replay with a voided sequence,
worker restart, bounded 600-result chunking, evidence-generation resets,
query-count bounds, schema 23 upgrades, every rebuild phase, retained restore,
late results, permanent awards after corrections, disabled leagues and Hall
seasonal availability.

Frontend coverage remains unchanged in design:

- Full Vitest: **109 files / 1,610 tests passed**
- Targeted Chromium desktop/mobile Hall checks: **18 passed**
- TypeScript, Vite build, bundle budgets and documentation checks passed
- Ruff, formatting and mypy passed

## Performance measurements

Small isolated benchmark (`scripts/benchmark_league_arena.py`, 200 players, 300
historical games, 20 samples, 4 writers), current working tree versus reviewed
`a8bbcb7` archive:

| Measure | Current | Reviewed |
| --- | ---: | ---: |
| Ordinary append settlement p95 | 5.48 ms | 4.72 ms |
| Full replay settlement p95 | 7.32 ms | 6.19 ms |
| Append writer hold p95 | 0.83 ms | 0.71 ms |
| Mixed league online move p95 | 4.21 ms | 5.14 ms |
| League drain p95 | 53.48 ms | 82.45 ms |
| Large-queue matchmaking | 185.4 pairs/s | 180.6 pairs/s |
| Rebuild, 340 official games | 0.123 s | 0.115 s |
| Idle worker CPU / 2 s | 5.52 ms | 5.99 ms |

These are single small-fixture runs with run-to-run variance, not production
scaling claims.

Small three-season correctness fixture (9 official results, 6 account scopes):
Schema 25 upgrade **4.32 ms**, bounded reconciliation **14.49 ms**.

Synthetic large-history validation:

- 2,700 applied results: 3 seasons × all three rulesets × 300 games per
  account/scope, 18 account scopes.
- Schema 25 upgrade/queue creation: **6.66 ms**.
- Bounded reconciliation for all 2,700 results: **122 ms** without tracing;
  **373 ms** with `tracemalloc`.
- Python traced peak during reconciliation: **0.735 MB**.
- A 600-result scope executes exactly **3** chunk queries (256/256/88), not 600
  per-game queries; total traced statements remain below 100.
- The large-history test verifies durable continuation after restart and resets
  accumulated progress when the season evidence revision changes.

## Remaining limitations

- The benchmark above measures normal settlement/move/matchmaking/rebuild
  behavior; it does not run production-size achievement histories.
- The 2,700-result fixture is synthetic and isolated, not a multi-installation
  deployment benchmark.
- The full browser suite is a CI gate; only targeted Hall browser checks were run
  locally.
