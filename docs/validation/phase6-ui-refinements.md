# Phase 6 UI refinements: validation

[Documentation index](../README.md) · [Rated games](../RATINGS.md) ·
[Online play](../ONLINE_PLAY.md) · [Dashboard](../DASHBOARD.md)

Baseline: `41052bf` on `feat/phase6-rated-games-leaderboards` (Phase 6, PR #23),
itself based on `0702ea9` (merged PR #21) on `feat/online-multiplayer`. This
refinement branch is `feat/phase6-ui-refinements` and targets the Phase 6 branch.

## Delivered behavior

- Online game screen reuses the single-player `.game-layout`/`.game-sidebar`/
  `.battlefield`/`.history-panel` structure and shared `Board`/`ReplayControls`.
  Removed assistance, version labels, the manual refresh button, the persistent
  connection label and the back-to-lobby button; remaining time is in the game
  details; a compact active-game switcher marks the current game and your-turn
  games.
- Captured kings stay visible on the terminal online/replay board via a
  display-only `terminal_king_square` derived from recorded moves; stored history
  and rules state are unchanged.
- One shared `remainingTime` formatter for the game screen and lobby, with exact
  60s/60m/24h boundaries and zero clamping.
- Lobby All games/My games/My turn tabs (shared `Tabs` component with the jobs
  view), terminal games excluded from every tab.
- Online statistics group by ruleset only, aggregate match modes without double
  counting and show the account's available Elo in each ruleset heading.
- AI-vs-AI account games are excluded from personal history and statistics.
- One unified leaderboard ordered by rounded Elo, then rated games, then account
  id; the standing paragraph is removed and provisional players carry a badge.

## Automated checks actually run

Complete aggregate:

```
make check
# docs, ruff check + format, prettier, mypy, TypeScript, backend tests,
# frontend tests and production build all passed
```

Full backend (real SQLite, controlled UTC):

```
.venv/bin/python -m pytest -q
# 1518 passed, 36 skipped, 1 warning in 914.02s
```

The 36 skips are the existing OpenVINO/GPU/optional skips; none are new. Focused
backend subset:

```
.venv/bin/python -m pytest tests/test_ratings.py tests/test_personal.py \
  tests/test_analysis_api.py tests/test_online_api.py tests/test_online_core.py \
  tests/test_online_matches.py tests/test_security.py -q
# 223 passed, 1 warning
```

Frontend unit tests:

```
npm --prefix frontend test
# 46 files, 390 passed
npm --prefix frontend run typecheck        # passed
npm --prefix frontend run format:check     # passed
```

Browser tests (bundled Playwright Chromium, desktop + mobile emulation):

```
npm --prefix frontend run test:online      # 14 passed (desktop + mobile)
npm --prefix frontend run test:personal    # 18 passed (desktop + mobile)
```

The new coverage includes duration boundaries (59/60/3599/3600/86399/86400 and
zero/expired), lobby tabs and section requests, active-game switcher/current/
your-turn markers, captured-king prop rendering, ruleset statistics with and
without Elo, AI-vs-AI exclusion, unified leaderboard ordering/ties/pagination and
the absence of online assistance. Browser tests cover reload, reconnect and
replay for the game screen; the captured-king projection and rendering are
covered by backend and frontend unit tests rather than a browser capture.

## Not claimed

- No rating formula, rules, eligibility, production activation or training change.
- No deployment, release, tag or version bump; the PR is left unmerged.
- Repeated-opponent farming remains possible and documented, not mitigated.
- Screenshots are captured by the browser suites but were not visually inspected
  by the agent (no image viewer in this environment); DOM and no-overflow
  assertions are the layout evidence.

## Follow-up refinement

Baseline for this follow-up: `d373005` on `feat/phase6-ui-refinements` (PR #24),
based on `41052bf` on the Phase 6 branch.

Delivered:

- **Rated-only new games.** Omitted `rated` defaults to rated; explicit `false` and
  non-boolean values are rejected by the service and API. Historical casual matches
  keep their flag and are never backfilled or reclassified.
- **Start-rating snapshots.** Schema 12 adds `online_game_start_ratings`, captured
  idempotently at activation (both participants established), per match leg, and
  independent of rating-ledger rebuilds. The Hall shows `Name (rating)` and the
  winner's effective persisted positive delta; older records show no invented Elo.
- **Shared duration formatting** for configured time per move (creation summary,
  lobby entries and game details) using the same `remainingTime` formatter.
- **Single-player layout**: page intro removed, a `play-page` wrapper with a
  "Single player game" heading matching the online screen, and a New game control
  moved into the game details.
- **Completed-online analytics**: the backend reconstructs a terminal online game
  and reuses the existing analysis service, rejecting ongoing games; the recorded
  review enables the Analytics/Winning chances panels.
- **Active-game switcher** across single-player and online games, rendered only
  with more than one accessible active game.
- **Leaderboard preference**: default Copenhagen, persisted in `localStorage`,
  selector in the section's top-right, invalid/unavailable storage handled.
- **Desktop sizing**: the square board is capped by the available viewport height
  so normal play fits a 1920×1080 window.

Commands run for this follow-up:

```
.venv/bin/python -m pytest tests/test_ratings.py tests/test_personal.py \
  tests/test_analysis_api.py tests/test_background_analysis.py \
  tests/test_online_api.py tests/test_online_core.py tests/test_online_matches.py \
  tests/test_security.py tests/test_online_migrations.py -q
npm --prefix frontend test
npm --prefix frontend run typecheck
npm --prefix frontend run format:check
make docs
```

Results:

- `make check` — docs, Ruff/format, Prettier, mypy, TypeScript, backend tests,
  frontend tests and production build all passed.
- Frontend `vitest`: **47 files, 395 passed**.
- Online browsers: **14 passed** (desktop + mobile Chromium).
- Personal browsers: **20 passed** (desktop + mobile Chromium), including the
  Copenhagen default, persisted ruleset preference and the 1920×1080 fit check.
- Guest browsers: **16 passed**.
- Legacy `test:e2e`: **63 passed, 4 skipped**, with the pre-existing
  `appearance.spec.ts` token-count flake (`24` instead of `25`) observed on both
  this branch and the base commit `d373005`; it is not introduced here.
- Full backend `pytest`: **1523 passed, 36 skipped, 1 warning** (959.14 s); the 36
  skips are the existing OpenVINO/GPU/optional skips.
