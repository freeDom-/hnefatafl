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
