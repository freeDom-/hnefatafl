# Compact Dashboard badges and personal statistics

[Documentation index](../README.md) · [Dashboard and statistics](../DASHBOARD.md) ·
[Rated games](../RATINGS.md)

Checked locally on 2026-10-28. This refinement branches from integration commit `1754e12` on
`feat/online-multiplayer`, after the existing Elo and play-layout work. It adds no
schema migration, rating policy, version bump, release or production enablement.

## Delivered behavior

- Dashboard replaces the detailed statistics disclosures with one compact strip:
  three single-player and three multiplayer ruleset badges. Single-player
  decoration progresses with win rate; multiplayer reads existing Elo and omits
  it for rulesets without played games. Each badge links to its detailed view.
- The authenticated `/statistics` page retains mode/ruleset selection in its URL.
  Ruleset cards show overall/attacker/defender games, wins, losses, draws, win
  rates and average lengths. Single-player also groups by persisted,
  seed-independent AI configuration. Expandable AI rows carry the same metrics.
  Desktop uses comparison tables; mobile uses labelled metric cards.
- Existing private personal reads supply complete aggregates regardless of game
  history pagination. Account seat attribution, unfinished-game denominators,
  eligible online result revisions, void withdrawal and Elo remain authoritative.
  There are no new server routes or permission grants.
- The duplicate visible Online lobby heading is removed. Its accessible region
  name remains, and Open game shares the primary New online game button style.

## Checks performed

| Check | Actual local result |
| --- | --- |
| Personal backend: `.venv/bin/python -m pytest tests/test_personal.py -q` | 39 passed, 1 existing TestClient deprecation warning; 27.08 s. Includes new AI/side aggregation, unfinished-game and pagination independence, current Elo and void withdrawal checks. |
| Focused frontend units (badges, Statistics, detailed results, Dashboard, Online, navigation, header) | 68 passed in 7 files; 16.59 s during implementation. |
| Full personal production browser suite: `npm --prefix frontend run test:personal` | 26 passed; 2.2 min. Desktop Chrome and iPhone 13 Chromium emulation. |
| Full online production browser suite: `npm --prefix frontend run test:online` | 14 passed; 2.4 min. Desktop/mobile Chromium. |
| Final badge/statistics/login browser rerun after the unit-test corrections | 4 passed; 31.2 s. Desktop/mobile Chromium. |

Browser commands used `PLAYWRIGHT_CHROMIUM_EXECUTABLE=/usr/bin/chromium` and
isolated test storage. The full personal suite includes real login/deep-link
protection, own-account empty statistics, mode/ruleset reloads, logout response
isolation, history/review and existing play-density layouts. The rich statistics
case forwards real authenticated API responses and replaces only `statistics`
with controlled visual fixtures (20%, 60%, 90%, and an empty Elo pool). Those
fixture values are not evidence of real games or rating calculations. Native tests
exercise the real aggregation and rating storage separately.

Screenshots were actually inspected at both widths: compact badges, detailed
single-player with expanded AI rows, multiplayer, and lobby loading/empty/populated/
error states. Geometry assertions check shared padding/gaps, containment and no
document overflow. Badge-strip height is asserted below 220 CSS pixels on desktop
and 320 on mobile. An initial focused browser attempt passed the two lobby cases
and failed two statistics cases on an ambiguous Ruleset label. An explicit label
association fixed that failure; visual inspection also prompted the mobile metric
cards. The focused rerun passed all four cases before the full suites above.

The first `make check` reached **1,673 backend passes / 36 skips / 1 warning**
(862.32 s), then failed in frontend units (**423 passed / 4 failed**, with three
related unhandled errors). Three App redirect tests exposed an unsafe Dashboard
heading lookup when their partial API fixtures omitted statistics. The heading
now guards that absent value, consistently with the badge component. The fourth
failure exposed a new German game-count translation overriding shared training
wording; the new catalog now reuses existing shared translations. The six-file
regression selection, including all failing tests, passed **52 tests** (20.00 s).
That initial aggregate invocation is a failure, not an accepted full gate.

The second default `make check` passed the static checks and completed backend
tests with **1,672 passed / 36 skipped / 1 failed / 1 warning** (812.65 s).
The unchanged `test_tiny_deadline_interrupts_long_history_canonicalization`
wall-clock assertion failed: elapsed 29.685 ms versus a calibrated 28.265 ms
baseline. It passed in the first complete backend run above. Search code and that
test are unchanged in this PR. This invocation also failed and did not reach the
frontend/build stages.

The final command **`PYTEST_ADDOPTS=--lf make check` passed, exit 0**. Pytest
selected and passed the one currently failing deadline test (0.90 s, one existing
warning); this was a failed-test retry, not another complete backend pass. Docs,
Ruff/formatting, Prettier, mypy (50 source files) and TypeScript passed, followed by
the **complete frontend suite: 427 passed in 52 files** (131.39 s) and the
production build (3.25 s). The build retains the existing advisory about a chunk
above 500 kB. No default full `make check` pass is claimed for these local runs.

The 36 local backend skips cover unavailable optional OpenVINO dependencies and
accelerator hardware; no GPU validation is claimed. PR CI runs the full suites
with all extras installed, separately from these local results.

## Limits

Browser validation covers Chromium desktop and mobile emulation, not physical
devices or other browser engines. The visual fixture is deliberately synthetic;
no performance, AI strength or rating-policy change is claimed.
