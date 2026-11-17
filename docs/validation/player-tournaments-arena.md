# Player tournament Arena and durable settlement

[Documentation index](../README.md) · [Tournament policy and API](../PLAYER_TOURNAMENTS.md)

This records the initial Arena implementation at `392219e`, before the scheduling
and creator-permission hardening. Counts and screenshots below are historical.
See [the scheduling validation](player-tournament-scheduling.md) for current
behavior, migration, query profiling and calendar evidence.

Human double-elimination competitions now have `/player-tournaments`, independent
of Workshop AI benchmark tournaments. Discovery, real registration/withdrawal,
planned dates, server seeding/losses/portraits, source-connected upper/lower brackets,
grand final/reset, playable online legs, champion and administrator controls reuse
the existing authority. A scrollable graph and keyboard-accessible list support
compact viewports. Account changes cancel pending mutations and clear dialogs.

Committed authoritative online results enqueue tournament work atomically. The
bounded server worker advances without a browser, retries failed transactions and
reconciles terminal revisions at startup. Corrected results retain the established
dispute freeze and administrator rebuild. Actual advancement invalidates reviewed
versions; no-op settlement does not. Schema 19 adds queue/planned-date fields and
retains them with online authority on restore. No game rules, Elo or models change.

## Exact local validation

- Final tournament backend suite: **47 passed**, one existing Starlette warning.
- Combined tournament/migration/deployment run before the final version regression:
  **78 passed, one failed**. Failure was an obsolete unknown-future-schema fixture;
  updated migration/account suite: **14 passed**. Unsupported versions still fail.
- Planned-date/cursor and retained-outbox restore checks: **2 passed**.
- Actual background thread and registration checks: **2 passed**.
- Tournament component suite: **14 passed**; navigation/shell suite: **28 passed**.
- Final browser suite: **14 passed**, desktop and mobile projects, 1.6 minutes.
  Earlier three new-test failures (detached locator and cleanup origin) were fixed;
  no application permission, geometry, contrast or timeout threshold was weakened.
- Frontend formatting, TypeScript, production build, Ruff, mypy (70 files) and
  documentation links passed. Full repository CI is required separately on the PR.

The production entry grows to 1,045.74 kB minified / 319.30 kB Vite gzip; its existing
chunk warning remains. Route-level splitting is a separate measured workstream.
No claim of a reduced bundle, population capacity or physical-device coverage.

## Browser and visual evidence

All four themes, English/German, 320/390/1920/3840 CSS pixels: **32 captures**.
Assertions cover graph hierarchy and connecting paths, horizontal bracket access,
page overflow, text contrast at least 4.5:1, accessible list, permissions, announced
moderation errors and real create/register/withdraw persistence. Fixtures provide
reproducible bracket geometry; real API/database tests establish result authority.
Inspected [desktop contact sheet](../screenshots/player-tournaments/contact-1920.jpg)
and [phone contact sheet](../screenshots/player-tournaments/contact-390.jpg).

| Theme | 1920 | 3840 |
| --- | --- | --- |
| King's Table | [Capture](../screenshots/player-tournaments/kings-table-1920.webp) | [Capture](../screenshots/player-tournaments/kings-table-3840.webp) |
| Iron & Ash | [Capture](../screenshots/player-tournaments/iron-ash-1920.webp) | [Capture](../screenshots/player-tournaments/iron-ash-3840.webp) |
| Saga Hall | [Capture](../screenshots/player-tournaments/saga-hall-1920.webp) | [Capture](../screenshots/player-tournaments/saga-hall-3840.webp) |
| Classic Table | [Capture](../screenshots/player-tournaments/classic-table-1920.webp) | [Capture](../screenshots/player-tournaments/classic-table-3840.webp) |

CI retains original captures before later browser configurations clear output.
Intermediate breakpoints, real 200% zoom, physical mobile/Safari and final merged
cross-route acceptance remain program-level work. Planned dates do not auto-start
competitions; administrators confirm seeds and start. Anonymous tournaments are
not exposed. No merge, release or tag is performed.

First full CI passed backend checks and all 1,328 unit tests; the main browser
suite had 368 passes and one pre-existing advisor-helper readiness failure. The
board could mount before its advisor catalog added the Analytics tab, and the
helper silently skipped a temporarily missing tab. At the actual tabbed layout
breakpoints it now waits for the control; the plain tablet layout remains inline.
Four real Shield maiden cases and 18 analysis/replay/localization cases passed
with this correction. No application behavior, permission or timeout changed.
