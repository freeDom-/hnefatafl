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
- Narrow layouts use three headline columns where they fit. At Large interface
  scale on viewports up to 420px, the headline uses two columns with the third
  metric spanning the row and a slightly smaller value size; the detailed
  metric cards use two columns there as well. This prevents adjacent values or
  long German labels from overlapping at 320px. Other mobile, tablet and desktop
  layouts retain the existing three-column or inline arrangement.

## Automated coverage

The two personal browser specs deliberately use explicit viewports in the
Chromium project and skip the duplicate mobile-emulation project. The matrix is
risk-based rather than a full Cartesian product.

| Dimension | Covered values |
| --- | --- |
| Viewports | 320×640, 390×844, 768×1024, 1280×900, 1920×1080, 2560×1440 and 3840×2160 CSS pixels |
| Interface scales | Explicit Compact, Default and Large; automatic Default at 1920 and automatic Large at 3840 |
| Themes | Classic Table, King's Table, Iron & Ash and Saga Hall |
| Languages | English and German |
| Statistics modes | Single-player and online multiplayer |
| Online states | Brandubh with Elo and a timeout, empty Tablut, mode switches and a restored Brandubh selection |
| Removed achievement details | English `threshold` / `highest achieved` and German `Schwelle` / `höchste erreichte` stay absent |

The scale helper writes the persisted appearance key and resolves automatic
scale from the canonical `appearanceCatalog.largeViewport` query. Every case
asserts the rendered `data-ui-theme`, `data-density` and document language before
reading geometry, so a viewport resize is never assumed to change the scale.

Role-rate geometry checks assert that:

- each percentage value and label stays inside its metric area;
- adjacent metrics do not overlap;
- labels can wrap and remain at a readable size;
- percentages are neither clipped nor ellipsis-truncated;
- Elo stays inside the headline below the metric row;
- the page has no horizontal overflow;
- the detailed table remains visible, keyboard-focusable and inside its own
  horizontal scroll region, with mobile metric cells keeping their labels and
  values inside each cell.

The online case checks overall, attacker and defender rates, Elo, the timeout
count, per-side rows, rule-set switching, mode switching and restoring the
previous values without stale text. German coverage checks translated role
labels, localized decimal percentages and the preserved timeout wording.

The legacy unit cases cover both role fields omitted, only one field omitted,
explicit null rates and a genuine 0% rate next to an unavailable role. Missing
values render as an em dash while 0% remains 0%.

## Visual acceptance

Statistics screenshots use a controlled personal-statistics fixture (20%
overall, 100% as attacker and 0% as defender for the visible Brandubh card) on
top of the real authenticated page. These synthetic values demonstrate the role
layout; they are not evidence of real game or rating results. The fractional
99.9% / 99.9% / em dash case and the German fractional wording are exercised by
browser tests and were inspected from the test artifacts, but are not committed
as additional redundant screenshots.

- Desktop 1920, Default scale:
  [King's Table](../screenshots/role-win-rates/kings-table-desktop.webp),
  [Iron & Ash](../screenshots/role-win-rates/iron-ash-desktop.webp),
  [Saga Hall](../screenshots/role-win-rates/saga-hall-desktop.webp) and
  [Classic Table](../screenshots/role-win-rates/classic-table-desktop.webp).
- [King's Table, 3840×2160, automatic Large](../screenshots/role-win-rates/kings-table-3840-large.webp)
- [Iron & Ash, 390×844 mobile, explicit Compact, English](../screenshots/role-win-rates/iron-ash-compact-mobile.webp)
- [Saga Hall, 320×640 mobile, explicit Large, German](../screenshots/role-win-rates/saga-hall-large-mobile.webp)
- [Achievement cards, King's Table, desktop 1920](../screenshots/achievement-cards/kings-table-desktop.webp)
- [Achievement cards, Iron & Ash, mobile 390](../screenshots/achievement-cards/iron-ash-mobile.webp)

Actual visual inspection covered the 320px German Large and 390px English
Compact statistics cards, the 3840px automatic-Large desktop card and the 320px
German Large Hall of Renown card. The 320px inspection found the real overlap
that prompted the two-column fallback; the repaired screenshots above are from
the final layout.

## Checks performed

| Check | Actual local result |
| --- | --- |
| `pytest tests/test_personal.py -q` | 41 passed, 1 existing TestClient deprecation warning; 25.27 s. |
| Focused frontend units (statistics, Hall, badges, Dashboard, progression and notifications) | 80 passed in 8 files; 11.06 s. |
| `role-win-rates` and `achievement-cards` browser specs | 4 passed in Chromium (explicit viewports), 4 expected mobile-project skips; 54.6 s. |
| Existing Dashboard statistics browser case | 2 passed (desktop and mobile projects). |
| `make lint` | Ruff, Ruff formatting, appearance-catalog sync and Prettier passed. |
| `make typecheck` | mypy 72 source files and TypeScript passed. |
| `npm --prefix frontend run build` | Production build, 11 bundle-budget tests and the budget check passed. |
| `python scripts/check_docs.py` | All local links/images/anchors reachable. |

## Limits

Chromium is the validated engine; physical mobile devices and other browser
engines are not claimed. The screenshot fixtures replace statistics and
achievement projections for visual coverage while the surrounding page, session
and API shapes remain real. The full heavyweight `make check` gate runs in CI;
local validation used the focused suites above.
