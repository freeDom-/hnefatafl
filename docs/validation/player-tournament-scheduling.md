# Player tournament scheduling and hardening

[Documentation index](../README.md) · [Tournament behavior and API](../PLAYER_TOURNAMENTS.md)

This records the scheduling implementation through `f7be4ef`. The later
[final review refinement](player-tournament-refinement.md) records the compact
popup, two-game creation contract and subsequent validation. Counts and captures
below remain evidence for the earlier implementation.

PR #90 integrates `main` at `e717138` and keeps its shared Portrait component,
central asset mapping/styles, Portrait Hall and achievements, public Watch routes
and navigation. The older portrait implementation from the PR is discarded.

## Behavior and recovery evidence

The backend checks full-before/at/after scheduled time, unfilled registrations,
creator starts, administrator overrides, fewer than two entrants, restarts before
and after due time, concurrent starts, registration/withdrawal versions, stale
custom seeds, withdrawals at due time, disabled entrants, and rollback/retry
without duplicate games. Explicit UTC-offset inputs test spring/fall DST boundaries.
An explicit lock-wait regression ensures newly created return games receive their
full allowance from the server time sampled after acquiring the write lock.

All three match modes share normal online time control. Tests exercise per-move,
total time, increment accounting, side swaps, independent parallel clocks,
sequential restart recovery and timeouts, byes, double elimination, grand finals
and resets. Sequential cancellation, override and rebuild must leave no orphaned
active game. Schema-19 upgrade cases retain every game in legacy 1–5-leg series.
Tournament results must write neither Elo events nor rated achievement awards.

Creator HTTP tests check active/verified admission, ownership, server capabilities,
persisted three-active/five-per-day quotas including cancellations, sanitized
names and administrator-only cancellation, rebuild, overrides and audit access.
Ordinary explicit settlement retries must remain read-only and never traverse the
bracket under a write lock.

Six real-backend Playwright cases run all match modes in desktop/mobile projects:
a verified ordinary creator uses the actual form, both players register, and the
browser closes before the fixture advances server time. The real background
worker starts and completes the tournament through authoritative games. Neither
a browser timer nor manual start/settle requests drive this progression.

## Validation commands

```sh
make docs
make lint
make typecheck
.venv/bin/python -m pytest -q
npm --prefix frontend test -- --maxWorkers=2
npm --prefix frontend run test:benchmarks
npm --prefix frontend audit --audit-level=moderate
npm --prefix frontend run build
PLAYWRIGHT_CHROMIUM_EXECUTABLE=/usr/bin/chromium npm --prefix frontend run test:e2e -- \
  player-tournaments.spec.ts portraits.spec.ts portrait-fallback.spec.ts \
  workspace-navigation.spec.ts authentication.spec.ts stats-leaderboard.spec.ts \
  appearance-first-paint.spec.ts
PLAYWRIGHT_CHROMIUM_EXECUTABLE=/usr/bin/chromium npm --prefix frontend run test:online
```

Use the installed Playwright browser instead of the executable override when
appropriate. Browser configurations use disposable databases. Run configurations
sequentially: their default artifact directory is shared and a later run cleans it.
CI retains tournament/calendar captures before starting other configurations.

The full local Vitest run passed **1,427 tests in 97 files**. A subsequent focused
run passed **66 tests**, including the three added creator/pending-return cases.
Ruff, formatting, mypy (70 files), TypeScript, 12 benchmark-harness tests and the
production build passed. npm audit reported zero vulnerabilities. The authenticated
production chunk is 682.77 kB minified / 201.18 kB gzip; Vite's existing 500 kB
chunk warning remains. Final integration totals are recorded in the PR description.

## Query profiling at the entrant limit

A disposable database held 50 registration/running tournaments with 64 entrants
each. A 64-entrant, two-leg bracket had its opening results committed but not
advanced. SQLite trace callbacks counted statements, including trigger callbacks;
advancement transactions rolled back to keep identical input for both versions.
The baseline is the integrated pre-hardening implementation at `1bbee86`.
Values are medians of 31 calls on the same machine; concurrent validation makes
these timings indicative, not a capacity guarantee.

| Operation | Before statements | After statements | Before ms | After ms |
| --- | ---: | ---: | ---: | ---: |
| Listing 50 tournaments × 64 entrants | 104 | 4 | 1.98 | 1.60 |
| Reading a 64-entrant bracket | 135 | 10 | 4.03 | 2.44 |
| Advancing pending 64-entrant results | 1,414 | 934 | 17.26 | 13.86 |

Listing counts are projected in the listing query; bracket legs are fetched in
one batch; leg/result and participant reads use joins; advancement reuses its
tournament snapshot. There is no cross-request cache. `EXPLAIN QUERY PLAN` for the
worker reports a covering range search on `player_tournaments_start_due`, with
no temporary sort or historical tournament scan. Ordinary settle calls do no
advancement, and administrative no-op retries require a pending outbox entry.

## Calendar and visual checks

The calendar matrix covers four themes, English/German, and 320/390/1920/3840 CSS
pixels. It verifies keyboard day navigation/selection, month navigation, modal
focus containment and return, text contrast of at least 4.5:1 and no horizontal
dialog overflow. A Berlin browser context rejects the spring gap and checks the
two distinct UTC instants in the repeated autumn hour. Component tests reject
invalid dates/times and restore a saved instant to its local date/time.

These calendar surfaces were captured from the scrollable creation modal.
Long forms remain scrollable within its viewport boundary. The selected day
and theme-specific controls are visible in each capture.

| Theme | 390 phone | 1920 desktop |
| --- | --- | --- |
| Classic Table | [Capture](../screenshots/player-tournament-scheduling/classic-table-390.png) | [Capture](../screenshots/player-tournament-scheduling/classic-table-1920.png) |
| King's Table | [Capture](../screenshots/player-tournament-scheduling/kings-table-390.png) | [Capture](../screenshots/player-tournament-scheduling/kings-table-1920.png) |
| Iron and Ash | [Capture](../screenshots/player-tournament-scheduling/iron-ash-390.png) | [Capture](../screenshots/player-tournament-scheduling/iron-ash-1920.png) |
| Saga Hall | [Capture](../screenshots/player-tournament-scheduling/saga-hall-390.png) | [Capture](../screenshots/player-tournament-scheduling/saga-hall-1920.png) |

Physical-device/Safari coverage and true browser zoom are outside this Chromium
desktop/mobile-emulation evidence. No merge, release, deployment or data deletion
is part of this validation.
