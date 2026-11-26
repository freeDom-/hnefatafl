# Ruleset league achievements validation

[Documentation index](../README.md) · [League policy](../LEAGUES.md) ·
[Achievement progress](../PROGRESSION.md)

Achievement catalog 4 adds fifteen permanent milestones, five for each supported
league ruleset, generated from `league_policy.TIERS`. The catalog has 92 milestones
worth 6,770 points. Schema 24 adds the per-account/ruleset permanent peak and a
durable reconciliation queue. Historical verification resolves effective official
results from `league_games` and `effective_league_result()`; it does not trust
cached contributions or final standings.

## Consistency and recovery behavior

Ordinary settlement compares each queued settlement's official online revision
with its league projection and checks for an earlier unsettled official result in
the same account/season/ruleset. A relevant pending correction, earlier result gap
or administrative rebuild records a candidate peak in
`league_achievement_reconciliation` instead of awarding. The League worker
recomputes the candidate from effective official results after the scope becomes
consistent. Blocked rows remain durable across worker restarts.

Administrative `fold_standings` writes candidates only. Authoritative catch-up
settles corrections first; a bounded reconciliation pass then validates each
account/scope from effective results. This removes premature awards from an
incomplete rebuild while preserving the existing cursor batches and short writer
transactions.

The Hall returns typed seasonal availability. A rebuild keeps permanent tiers and
earned achievements visible, returns null seasonal values and hides the seasonal
progress bar. The existing localized rebuild message is shown. The next-tier label,
accessible value and progress element all use the same tier-relative values.

## Automated coverage

Focused backend run:

```sh
.venv/bin/python -m pytest tests/test_league_achievements.py \
  tests/test_league_policy.py tests/test_progression.py tests/test_leagues.py \
  tests/test_league_migrations.py tests/test_league_restore.py \
  tests/test_online_migrations.py tests/test_league_api.py \
  tests/test_online_core.py tests/test_deployment.py -q
```

Result: **273 passed**.

Coverage includes pending void plus a qualifying-looking later result, pending
tier-boundary corrections, deferred corrections and retries, restart recovery,
schema 23 upgrades with pending corrections, every administrative rebuild phase,
a correction between contribution restoration and folding, interruption/restart,
out-of-order peak evaluation, migration/settlement/recovery peak equivalence,
late-result exclusion, permanent-award preservation after corrections, retained
authority restore, read-only Hall behavior and seasonal re-availability.

Frontend regression coverage:

```sh
npm --prefix frontend test
npm --prefix frontend run typecheck
npm --prefix frontend run build
```

Result: **109 files / 1,610 tests passed**. The Hall tests cover English and German,
all four Arena themes, an intermediate next-tier bar, unavailable seasonal values,
the permanent tier ladder, hidden seasonal controls and recovery after the rebuild
message clears. TypeScript, Vite build, bundle budgets and documentation checks
passed. Backend Ruff, formatting and mypy checks passed.

Targeted real-browser coverage:

```sh
cd frontend
./node_modules/.bin/playwright test --project=chromium --project=mobile \
  tests/portraits.spec.ts tests/achievement-tiers.spec.ts
```

Result: **18 passed** (desktop and mobile, all four themes). The run includes the
availability toggle, permanent-tier retention, return of seasonal progress and
horizontal-overflow checks.

## Performance evidence

A representative three-season fixture with nine official games and six
account/season/ruleset scopes measured:

- schema 24 upgrade/queue creation: **0.0043 s**
- bounded reconciliation batches: **0.0141 s**

These are local fixture measurements, not production throughput claims. Query
plans confirm `league_settlement_season_due` for pending-correction discovery,
`league_achievement_reconciliation_due` for queued work,
`league_standings_account` for Hall progress and an indexed covering scan for the
ordinary contribution tail. Hall achievement reads issue no query against
`league_contributions`. Normal settlement remains bounded by the existing indexed
tail and scope checks; reconciliation processes one account/scope per short writer
transaction.

## Remaining limitations

- A historical peak superseded by an official correction before it was ever
  recorded cannot be reconstructed from later effective revisions.
- The complete Playwright suite was not run locally; targeted Hall browser checks
  and GitHub Actions run the full matrix.
- The migration/reconciliation timings above use a small representative fixture.
  No production-size ledger or live multi-installation upgrade timing is claimed;
  GitHub Actions remains the full-suite gate.
