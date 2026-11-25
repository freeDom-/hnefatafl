# Role win rates and compact achievement cards

[Documentation index](../README.md) · [Personal statistics](../DASHBOARD.md) ·
[Progression](../PROGRESSION.md)

Checked locally on 2026-11-25. This presentation change adds the existing
attacker and defender win rates to each Statistics ruleset card and removes the
threshold and highest-achieved lines from the visible achievement cards. No
schema migration, achievement definition, award, score or rating policy changed.

## Delivered behavior

- Every Statistics ruleset card shows the overall, attacker and defender win
  rates together in its headline. The existing detailed per-side rows remain.
  Unavailable rates render as an em dash; values use the existing localized
  one-decimal percentage formatting.
- The role rates keep their existing server-side populations: completed games
  only, draws in the denominator, and wins counted only for the matching seat.
  Ongoing games stay out of the denominator. Online results keep their current
  effective eligible revision, timeout and concession handling.
- Achievement cards retain the family name, state, earned tier, recorded
  progress, next milestone, progress bar, milestone rewards and achievement
  score. They no longer render the threshold or highest-achieved labels.
- The API still returns `threshold`, `awarded_threshold`, `achieved_threshold`,
  milestones and award history. Eligibility, automatic awards, scores, portrait
  entitlements, correction handling and sorting are unchanged.
- The responsive headline switches to three equal columns on narrow screens so
  the three rates stay aligned without creating page overflow.

## Visual acceptance

Statistics screenshots use a controlled personal-statistics fixture (20%
overall, 100% as attacker and 0% as defender for the visible Brandubh card) on
top of the real authenticated page. These synthetic values demonstrate the role
layout; they are not evidence of real game or rating results.

- [King's Table, desktop 1920](../screenshots/role-win-rates/kings-table-desktop.webp)
- [Iron & Ash, desktop 1920](../screenshots/role-win-rates/iron-ash-desktop.webp)
- [Saga Hall, desktop 1920](../screenshots/role-win-rates/saga-hall-desktop.webp)
- [Classic Table, desktop 1920](../screenshots/role-win-rates/classic-table-desktop.webp)
- [King's Table, mobile 390](../screenshots/role-win-rates/kings-table-mobile.webp)
- [Saga Hall, mobile 390](../screenshots/role-win-rates/saga-hall-mobile.webp)

- [Achievement cards, King's Table, desktop 1920](../screenshots/achievement-cards/kings-table-desktop.webp)
- [Achievement cards, Iron & Ash, mobile 390](../screenshots/achievement-cards/iron-ash-mobile.webp)

Browser assertions cover all four themes at 1280, 1920 and 2560 CSS pixels on
desktop and 320, 390 and 768 CSS pixels in mobile emulation. They check the
rendered labels and values, card containment, document width and the preserved
achievement content.

## Checks performed

| Check | Actual local result |
| --- | --- |
| `pytest tests/test_personal.py -q` | 41 passed, 1 existing TestClient deprecation warning; 25.29 s. |
| Focused frontend units (statistics, Hall, badges, Dashboard, progression and notifications) | 75 passed in 8 files; 8.90 s. |
| `make lint` | Ruff, Ruff formatting, appearance-catalog sync and Prettier passed. |
| `make typecheck` | mypy 72 source files and TypeScript passed. |
| `npm --prefix frontend run build` | Production build, 11 bundle-budget tests and the budget check passed. |
| New personal Playwright specs | `role-win-rates`: 2 passed (desktop and mobile projects); `achievement-cards`: 2 passed. |
| Existing Dashboard statistics browser case | 2 passed after the fixture received distinct role rates. |

## Limits

Chromium is the validated engine; physical mobile devices and other browser
engines are not claimed. The screenshot fixtures replace statistics and
achievement projections for visual coverage while the surrounding page, session
and API shapes remain real.
