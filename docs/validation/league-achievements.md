# Ruleset league achievements validation

[Documentation index](../README.md) · [League policy](../LEAGUES.md) ·
[Achievement progress](../PROGRESSION.md)

Achievement catalog 4 defines fifteen permanent league milestones, five for each
supported ruleset. Schema 26 adds permanent peaks, a durable reconciliation queue,
scope-specific evidence generations and bounded streaming cursors. Historical verification resolves
effective official results from applied rating applications and durable
`league_games`; it does not trust cached contributions or final standings.

## Correctness and recovery semantics

- Ordinary appends are evaluated immediately only when no settled revision
  mismatch or earlier applied result gap exists.
- Every replay (correction, void or out-of-order replacement) uses the durable
  reconciliation path, including voids without an effective contribution sequence.
- Reconciliation waits for all pending settlements in the account/season/ruleset
  scope, then streams applied results in stable rating sequence.
- Each account/season/ruleset owns an evidence generation. Relevant settlements or
  rebuilds increment only that scope; unrelated seasonal activity does not restart
  the cursor. Awards cannot commit from mixed evidence generations.
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

Result: **284 passed**.

Coverage includes the required out-of-order replay scenario (initial loss, four
wins, deferred loss, fifth win, void, deferred correction and 14-point final peak),
multiple pending settlements, earlier/later gaps, replay with a voided sequence,
worker restart, bounded 600-result chunking, evidence-generation resets,
query-count bounds, schema 23 upgrades, every rebuild phase, retained restore,
late results, permanent awards after corrections, disabled leagues and Hall
seasonal availability. New coverage also exercises continuous unrelated
settlements between pages, same-account activity in another ruleset, scope-local
evidence invalidation, and 15,000 unrelated rated results around 600 league
results with early/intermediate/late cursors.

Frontend coverage remains unchanged in design:

- Full Vitest: **109 files / 1,610 tests passed**
- Targeted Chromium desktop/mobile Hall checks: **18 passed**
- TypeScript, Vite build, bundle budgets and documentation checks passed
- Ruff, formatting and mypy passed

## Performance measurements

Small isolated benchmark (`scripts/benchmark_league_arena.py`, 200 players, 300
historical games, 20 samples, 4 writers), representative current run versus
reviewed `a8bbcb7` archive:

| Measure | Current | Reviewed |
| --- | ---: | ---: |
| Mixed league operations/s | 336.8 | 356.5 |
| Ordinary append settlement p95 | 9.80 ms | 4.72 ms |
| Full replay settlement p95 | 8.32 ms | 6.19 ms |
| Append writer hold p95 | 1.93 ms | 0.71 ms |
| Mixed league online move p95 | 3.03 ms | 5.14 ms |
| League drain p95 | 35.77 ms | 82.45 ms |
| Large-queue matchmaking | 170.7 pairs/s | 180.6 pairs/s |
| Rebuild, 340 official games | 0.129 s | 0.115 s |
| Idle worker CPU / 2 s | 5.54 ms | 5.99 ms |

The shared host is noisy: across current runs, mixed throughput ranged
228–337 operations/s and ordinary append p95 ranged 4.94–9.80 ms. These are not
controlled production comparisons.

Small three-season correctness fixture (9 official results, 6 account scopes):
Schema 26 upgrade **4.52 ms**, bounded reconciliation **16.31 ms**.

Synthetic large-history validation:

- 2,700 applied results: 3 seasons × all three rulesets × 300 games per
  account/scope, 18 scopes. Schema 26 upgrade **7.14 ms**; reconciliation
  **132.9 ms** without tracing. With `tracemalloc`, reconciliation was **625 ms**
  and traced Python peak was **0.735 MB**.
- A 600-result scope executes exactly **3** page queries (256/256/88), not 600
  per-game queries; total traced statements remain below 100.
- Continuous unrelated activity: 900 results completed in **142 ms** of drain
  work across other-ruleset and unrelated-account settlements without resetting
  the Brandubh cursor.
- Sparse history: 15,000 unrelated rated results (all three rulesets) plus 600
  two-account league results. A page with 10-instruction progress sampling used
  about **31,370 / 29,710 / 6,030 VM instructions** and **6.53 / 3.34 / 1.44 ms**
  at early, intermediate and late cursors.
- Query plans use `league_games_first_sequence` and
  `league_games_second_sequence`, row-value `(sequence,game_id)` seeks, a
  per-branch limit of 256 and an outer bounded merge. Unrelated rated history is
  not traversed.
- Derived reconciliation page writer work is about **3.7 ms** total wall time
  per page for the 2,700-result fixture (132.9 ms / 36 page transactions),
  excluding unrelated concurrent workloads.

Every figure above is from isolated local fixtures, not production-scale or
multi-installation claims.

## Remaining limitations

- The benchmark above measures normal settlement, move, matchmaking and rebuild
  behavior; it does not run production-size achievement histories.
- The synthetic histories are isolated and not a multi-installation deployment
  benchmark; writer-lock figures vary with host load.
- The full browser suite is a CI gate; only targeted Hall browser checks were run
  locally.
