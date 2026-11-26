# Ruleset league achievements validation

[Documentation index](../README.md) · [League policy](../LEAGUES.md) ·
[Achievement progress](../PROGRESSION.md)

Achievement catalog 4 adds fifteen permanent milestones, five for each supported
league ruleset, generated from `league_policy.TIERS`. The catalog now has 92
milestones worth 6,770 points. Schema 24 adds the per-account/ruleset permanent peak
and reconstructs historical peaks once from the authoritative league contribution
ledger.

## Automated coverage

Run from the repository root with the locked Python environment:

```sh
.venv/bin/python -m pytest tests/test_league_achievements.py \
  tests/test_progression.py tests/test_leagues.py \
  tests/test_league_migrations.py tests/test_league_restore.py \
  tests/test_online_migrations.py tests/test_deployment.py -q
```

The focused backend run passed 157 tests. Dedicated league-achievement coverage
asserts generated definitions and tier metadata, exact boundaries for every tier,
Midgard qualification, inactive-account behavior, independent ruleset peaks,
demotion and real season-rollover permanence, duplicate and concurrent settlement,
void-before-settlement behavior, corrections after awards, historical-ledger
backfill of a peak that final standings no longer show, no fabrication from
standings alone, duplicate reward prevention and disabled-league reads.

Frontend regression coverage:

```sh
npm --prefix frontend test
npm --prefix frontend run typecheck
npm --prefix frontend run build
```

The complete Vitest run passed 109 files and 1,609 tests. The Hall test renders the
league card with permanent and current-season progress in all four Arena themes,
checks tier names and states, and verifies that redundant threshold and
highest-achieved labels are absent. TypeScript, Vite build, bundle budget and the
documentation link/anchor check passed. Backend Ruff, formatting and mypy checks
passed.

Targeted browser checks also passed with real Chromium:

```sh
cd frontend
./node_modules/.bin/playwright test --project=chromium   tests/portraits.spec.ts tests/achievement-tiers.spec.ts
./node_modules/.bin/playwright test --project=mobile   tests/portraits.spec.ts tests/achievement-tiers.spec.ts
```

Each project passed 9 tests. They cover the four Arena themes, desktop and mobile
viewports, the thirteen achievement families, league tier states and the absence
of horizontal overflow.

## Remaining validation limits

The complete Playwright suite was not run locally; the targeted Hall coverage and
GitHub Actions run the full browser matrix. No live multi-installation upgrade or
production-size ledger timing is claimed here; GitHub Actions remains the
full-suite gate.
