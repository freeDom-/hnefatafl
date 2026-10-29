# Shared interface and online creation

These frontend refinements start from `feat/mobile-board-tabs` at `dcb4e43`,
including the total-time clock controls from PR #33. The refinement PR targets
that feature branch. No application version, database schema, clock adjudication
or production configuration changes.

## Delivered behavior

- The online creation dialog separates game settings and clock settings into two
  desktop columns. Days, Hours and Minutes share one row. Phone widths stack the
  columns and retain accessible dialog scrolling when needed.
- Increment input and game metadata appear only for total-time clocks. Switching
  to per-move time clears the input and sends zero for a new request. Existing
  games and retained uncertain requests keep their original authoritative settings
  and request ID; retrying does not silently change a possibly committed match.
- All pages share the same responsive site navigation, including mobile play.
  Phone navigation scrolls horizontally; game-specific back/options controls
  remain a separate toolbar. Page titles/subtitles retain accessible headings and
  focus targets without taking up visible space. Section, dialog and error text
  remains visible.
- [ui.css](../../frontend/src/ui.css) owns common typography, buttons, surfaces,
  dialogs, tabs, form grids and accessibility classes. [siteHeader.css](../../frontend/src/siteHeader.css)
  owns the site menu. Feature headings and form controls use shared type tokens.

## Local verification

| Check | Result |
| --- | --- |
| Frontend unit tests | 55 files, 453 passed |
| Play/layout and shared-page browser regressions | 4 passed, 4 deliberate project-specific skips |
| Final desktop geometry and shared-header rerun | 3 passed |
| Play entry, verification warning and Hall replay regressions | 8 passed |
| Existing online gameplay/clock browsers | 16 passed |
| Added online-dialog browser checks | 2 passed in a focused run |
| Registration/recovery/auth-layout browsers | 8 passed |
| Prettier, TypeScript, production build and Markdown links | Passed |
| Documentation | 96 Markdown files, 737 local links/images/anchors |

The complete online invocation initially rejected the new test's exact wrapped-label
locator. The focused run uses the combobox's accessible name and passes both browser
projects; the 16 existing online cases passed in the complete invocation. The PR's
Verify workflow reruns the complete suites on one committed source revision.

The initial PR browser run also exposed Hall replay overflow in short Large-scale
windows and two assertions tied to removed helper copy/heading markup. The shared
compact header now applies in short phone landscape, summary separators use the
existing condensed spacing, and warning checks verify visible position and
accessible heading presence. All eight play-entry/replay cases then passed,
including 1366×620 desktop and 844×390 phone landscape at all scales.
The landscape inspector spans the player and board rows so its move list remains
usable alongside the shared site menu; compact appearance controls show their icon.

The dialog check covers English/German, Compact/Default/Large, both clock modes,
1920×1080, 1568×785 and 1280×720 desktop viewports, plus a 390×844 phone viewport.
Desktop checks assert two columns, no dialog scrolling and viewport containment.
Phone checks assert one column and no horizontal overflow. The test also creates a
real per-move game after leaving total-time mode and verifies a zero increment.

Shared-page checks compare header geometry/type across Dashboard, Statistics,
Leaderboard, Hall, Workshop, Tournaments and play at 1920×1080, 1568×785 and
390×844, at all three interface scales. They verify accessible page headings with
no visible layout footprint and consistent section typography. Existing play tests
also cover short/larger desktop windows, portrait/landscape phones, replay,
advisor settings and tab switching without board overlap or document overflow.

Visual inspection covered the two-column desktop dialog, its stacked phone form,
and desktop play, including the production build in light, dark and wood modes.
At 1568×785/Large the production dialog measured 602.44 px high with no scrolling
in all three modes. Browser evidence uses Chromium and mobile emulation with
disposable local accounts/storage; it does not establish physical Safari behavior.

See [online browser checks](../../frontend/online-tests/multiplayer.spec.ts),
[shared-page checks](../../frontend/tests/shared-ui.spec.ts),
[desktop geometry](../../frontend/tests/desktop-play.spec.ts),
[mobile play](../../frontend/tests/mobile-play.spec.ts) and
[auth accessibility](../../frontend/registration-tests/auth-layout.spec.ts).
