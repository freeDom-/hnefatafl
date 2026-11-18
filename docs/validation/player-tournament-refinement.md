# Player tournament final review refinement

[Documentation index](../README.md) · [Tournament behavior and API](../PLAYER_TOURNAMENTS.md) · [Earlier scheduling evidence](player-tournament-scheduling.md)

This records the final review of PR #90 after `f7be4ef`, retaining `main` at
`e717138`. No merge, release, production migration or deployment is performed.

## Delivered behavior

New tournament creation explicitly requires `sequential` or `simultaneous` and
stores exactly two side-swapped games. The form defaults to sequential; ordinary
online games keep all three modes. Missing/null/unsupported modes and every
supplied `match_legs` field are rejected. Test fixtures provision historical
records directly so the existing single/1–5-leg execution tests do not depend on
an escape hatch in new creation. Schema 20, stored modes, games and results stay
unchanged.

Full tournaments cannot be creator-started before their scheduled instant.
Unfilled tournaments with two eligible entrants can start early; administrators
can override. A pure policy function serves both capabilities and the shared
transactional start operation. A real `init-admin --credentials-stdin` regression
verifies creation and moderation before email verification, and rejects the same
session after server-side role/activation changes.

Both scheduling inputs use integrated calendar/clear icons and adjacent date/time
fields. The native popover occupies the top layer while its DOM remains inside
the existing Modal. It stays within the visual viewport without changing form
height. Selection/Escape restore date focus; outside clicks retain their target's
focus. Manual input, invalid date/time rejection, Berlin DST gaps/folds, UTC
serialization and retained sub-minute precision are tested. The form shows one
compact timezone hint. Themes use existing tokens and controls.

The tournament route follows the existing lazy/Suspense pattern. Browser requests
verify that dashboard navigation does not load the page or calendar library.
Configuration summaries use the existing duration formatter and translations,
including historical second precision, time-control type, increment and mode.

## Validation

The selected Python regression run passed **396 tests** (one existing Starlette
TestClient deprecation warning), including all **116 tournament tests**. Full
Vitest passed **1,437 tests in 97 files**; the final retained-instant picker check
also passed all six picker tests, including repeated keyboard-open requests. Ruff and formatting passed for 174 Python files;
mypy passed for 70 source files. TypeScript, frontend formatting, the production
build, documentation links and all 12 benchmark-harness tests passed.
Tournament Playwright passed **26 cases**; the final DST preservation rerun
passed **2 cases**. The complete online browser suite passed **24 cases**, including
four real-backend sequential/simultaneous tournament flows across desktop/phone
projects. They reject the creator's early full start, close the browser, then
start and complete through the server worker and authoritative games.
Historical single/1–5-leg execution remains covered by the Python suite; new
browser tournament creation exercises only the two supported modes. Canonical
full-suite CI results are recorded in the pull request description.

```sh
make docs
.venv/bin/ruff check hnefatafl tests
.venv/bin/ruff format --check hnefatafl tests
.venv/bin/mypy hnefatafl
OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 .venv/bin/python -m pytest \
  tests/test_player_tournaments.py tests/test_online_core.py tests/test_online_api.py \
  tests/test_online_clocks.py tests/test_online_matches.py tests/test_online_admission.py \
  tests/test_online_migrations.py tests/test_ratings.py tests/test_security.py \
  tests/test_progression.py -q
npm --prefix frontend run typecheck
npm --prefix frontend run format:check
npm --prefix frontend test -- --maxWorkers=2
npm --prefix frontend run test:benchmarks
npm --prefix frontend run build
npm --prefix frontend audit --audit-level=moderate
PLAYWRIGHT_CHROMIUM_EXECUTABLE=/usr/bin/chromium npm --prefix frontend run test:e2e -- \
  tests/player-tournaments.spec.ts --workers=1
PLAYWRIGHT_CHROMIUM_EXECUTABLE=/usr/bin/chromium npm --prefix frontend run test:online -- --workers=1
```

Browser configurations run sequentially with one worker and disposable backend
data. Captures are copied before another configuration cleans `test-results`.
CI retains the compact-calendar capture directory.

## Production loading measurement

Vite 7.3.7 reports these decimal kB sizes for the production build, compared with
`f7be4ef`. No package or lockfile change is needed.

| Asset | Before minified / gzip kB | After minified / gzip kB |
| --- | ---: | ---: |
| Authenticated Arena JS | 682.77 / 201.18 | 580.24 / 171.51 |
| Tournament/calendar lazy JS | Included above | 106.49 / 31.34 |
| Tournament/calendar lazy CSS | Included in Arena CSS | 7.20 / 1.94 |
| Entry JS | 452.90 / 145.07 | 453.37 / 145.24 |

The authenticated JS transfer falls by 102.53 kB minified (15.02%) and 29.67 kB
gzip (14.75%) on unrelated authenticated routes. The calendar chunk loads when
the tournament page is visited. Vite's existing 500 kB chunk warning remains;
this is a loading-boundary improvement, not a reduction in every route's combined
asset total.

## Dependency audit status

PR #95 merged into `main` and is now part of this branch. Its dedicated
`security` CI job runs `npm run audit:ci` against the committed lockfile. The
repository owner approved a temporary exception for
[GHSA-fc5q-39xc-vjpf](https://github.com/advisories/GHSA-fc5q-39xc-vjpf), a
development-tool-only ReDoS in `picomatch` through 4.0.7. No patched upstream
release exists, so the underlying vulnerability is **not technically resolved**.

The exception is limited to the reviewed advisory, package set, versions and
range, is development-only, expires **2026-12-17**, and requires a clean
production-only audit. Any new, unapproved or runtime-reachable finding still
fails the audit. No advisory was suppressed, no security check was relaxed, and
no dependency or lockfile downgrade was introduced by this PR.

## Match-listing query regression

`matches()` now loads the tournament row once and fetches all legs for the
requested page in one `IN (...)` query, reusing those rows plus the existing
account and game projections. Pagination, ordering, result states and the
sequential `pending_games` value are unchanged.

A regression test seeds **200 matches** with legs in one tournament and traces
every SQL statement for `limit=1` and the maximum `limit=200`. Both pages execute
the same number of statements (11 in the local SQLite fixture, against 410 for
the previous per-match projection). On the same fixture the measured best-of-three
latency was **5.4 ms** after the change and **8.7 ms** before it. The counts, not
the wall-clock figures, are asserted because timing varies with the host.

## Mobile date/time entry

The numeric inputs keep `type="text"` so separators remain valid, but declare
`inputMode="numeric"` for mobile numeric keypads. Digits are formatted into
`YYYY-MM-DD` and `HH:MM` as they are typed. Existing separators are preserved as
segment boundaries, so editing a partial month (`2035-2-01`) or hour (`1:30`)
does not regroup digits into the wrong component. Formatting maps the caret by
digit count, while separator-adjacent Backspace/Delete remove the adjacent digit
instead of getting stuck on an auto-inserted separator. Component selection,
whole-field replacement, paste of complete or partial values and numeric-keypad
entry retain predictable caret positions. Validation, DST gaps/folds, repeated
hours and UTC serialization are unchanged. On coarse-pointer devices tapping the
date field starts manual entry instead of opening the popup, while the integrated
calendar icon, keyboard `ArrowDown`/`Enter`, clear action, focus return, compact
layout, localization and theme styling are unchanged. Existing Chromium phone
emulation covers the touch path; physical-device and Safari keyboard behaviour
remain unverified.

## Leaderboard entry point

The full leaderboard's **Player tournaments** link uses the existing
`.button.secondary` convention rather than the undefined `button-secondary`
class. A focused Playwright check compares its computed foreground, background
and border with the adjacent secondary pagination button at 390px and 1280px in
Classic Table, King's Table, Iron and Ash and Saga Hall, verifies the existing
keyboard focus ring, checks the `/player-tournaments` destination and asserts no
horizontal overflow. No new button class, style or design token was introduced.

## Visual evidence and limits

The calendar matrix covers both deadline/start fields, four themes, EN/DE and
320/390/1920/3840 CSS-pixel viewports. Assertions check popup bounds and hit testing,
unchanged modal height/scroll height, keyboard selection, month navigation,
selection dismissal, first-Escape popup closure, second-Escape modal closure,
focus return, repeated Arrow Down after returning to the date field,
outside-click dismissal, inline keyboard clearing, text contrast
and horizontal overflow.

All **26 tournament Playwright cases** passed, including the matrix above,
real administrator creation/registration/withdrawal, ordinary-player controls,
moderation-conflict retention, lazy navigation and Berlin DST conversion.
The final same-day repeated-hour preservation assertion passed in a separate
two-case DST run. A final two-case desktop/phone calendar matrix rerun passed
after the repeated-keyboard-open fix; its new assertion failed before the fix.

| Theme | Registration 390 | Registration 1920 | Planned start 390 | Planned start 1920 |
| --- | --- | --- | --- | --- |
| Classic Table | [Capture](../screenshots/player-tournament-refinement/classic-table-registration-390.png) | [Capture](../screenshots/player-tournament-refinement/classic-table-registration-1920.png) | [Capture](../screenshots/player-tournament-refinement/classic-table-start-390.png) | [Capture](../screenshots/player-tournament-refinement/classic-table-start-1920.png) |
| King's Table | [Capture](../screenshots/player-tournament-refinement/kings-table-registration-390.png) | [Capture](../screenshots/player-tournament-refinement/kings-table-registration-1920.png) | [Capture](../screenshots/player-tournament-refinement/kings-table-start-390.png) | [Capture](../screenshots/player-tournament-refinement/kings-table-start-1920.png) |
| Iron and Ash | [Capture](../screenshots/player-tournament-refinement/iron-ash-registration-390.png) | [Capture](../screenshots/player-tournament-refinement/iron-ash-registration-1920.png) | [Capture](../screenshots/player-tournament-refinement/iron-ash-start-390.png) | [Capture](../screenshots/player-tournament-refinement/iron-ash-start-1920.png) |
| Saga Hall | [Capture](../screenshots/player-tournament-refinement/saga-hall-registration-390.png) | [Capture](../screenshots/player-tournament-refinement/saga-hall-registration-1920.png) | [Capture](../screenshots/player-tournament-refinement/saga-hall-start-390.png) | [Capture](../screenshots/player-tournament-refinement/saga-hall-start-1920.png) |

Additional extremes: [320 phone](../screenshots/player-tournament-refinement/classic-table-start-320.png)
and [3840 desktop](../screenshots/player-tournament-refinement/classic-table-start-3840.png).
Chromium desktop and phone emulation do not establish physical-device/Safari or
true browser-zoom coverage.

## Follow-up head verification

The merged reviewed head `5c06e20` passed
[Verify run #44311165003](https://github.com/freeDom-/hnefatafl/actions/runs/44311165003)
with successful `security`, `python`, `browser` and `node22` jobs. `main` at
`a8ecb64` includes PR #95's approved audit exception and PR #94's achievement and
portrait work.

The final date/time remediation re-ran these checks locally:

| Check | Result |
| --- | --- |
| Focused `pytest` tournament, online core/API/migration, progression and security files | **286 passed** |
| Vitest | **1,453 passed in 100 files** |
| Player-tournament Playwright suite | **26 passed** |
| Leaderboard and navigation Playwright suites | **34 passed**; the tournament-link matrix covers both projects, four themes, focus and desktop/mobile widths |
| Online Playwright suite, including sequential/simultaneous tournament flows | **24 passed** |
| Ruff, Python formatting, mypy and `make docs` | Passed |
| TypeScript, Prettier and production build | Passed |
| `npm run audit:ci` | Passed with the unexpired PR #95 exception; production audit clean |

The complete repository validation remains the CI `python`, `browser` and
`node22` jobs.
