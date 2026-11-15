# Public spectator frontend acceptance

The signed-out Watch Hall implements the existing read-only public online API.
[Public API and UI guide](../PUBLIC_ONLINE.md#public-watch-hall-frontend).
It branches before Auth, lazily separating the protected application graph; public
GETs omit credentials and never request session state, private records or analysis.

## Implemented and verified

- Ruleset/status/result/public-player filtering and bounded cursor pagination.
- Live/waiting spectator board with server-deadline countdown, no move/hint/legal
  overlays or evaluations; completed validated replay with buttons and keyboard.
- Public player attribution and allowlisted portrait IDs only.
- Visible-only adaptive polling, cancellation/generation guards, completed polling
  stop, public-off/empty/private-ID/rate-limit/error states and explicit retry.
- Login/registration links, account-form discovery link, four themes and EN/DE.
- Explicit route-load failure boundary with full reload retry; no automatic loop.
- Replay keyboard focus stays on its control group when Home/End disables a step
  button. This fixes a real failure found by the browser regression.

Local browser checks: **11 passed, 1 intentional skip** (the seven-breakpoint matrix
runs once, not redundantly in both projects). Further real-backend discovery cases
run without mocked public responses. Public/AccountForms unit tests: **21 passed**.
Final full CI results are recorded on the pull request, not inferred from these
focused runs. Existing public backend privacy/rate-limit tests remain unchanged.

The matrix checks theme attributes, actual normal-text contrast >=4.5:1 and no
horizontal overflow at 320,390,768,1024,1366,1920,3840 CSS pixels in both languages.
It also proves protected modules and API requests are absent. The live/replay
checks assert GET-only traffic, no analysis, keyboard navigation and read-only
behavior. Initial test-only name/label locator mismatches were corrected using
separate name markup and accessible combobox roles; no threshold was reduced.

## Inspected screenshots

- [Four desktop themes](../screenshots/public-arena/themes-1920.jpg)
- [Four phone themes](../screenshots/public-arena/themes-390.jpg)

| Theme | 1920×1080 | 3840×2160 |
| --- | --- | --- |
| King's Table | [Capture](../screenshots/public-arena/kings-table-1920.webp) | [Capture](../screenshots/public-arena/kings-table-3840.webp) |
| Iron & Ash | [Capture](../screenshots/public-arena/iron-ash-1920.webp) | [Capture](../screenshots/public-arena/iron-ash-3840.webp) |
| Saga Hall | [Capture](../screenshots/public-arena/saga-hall-1920.webp) | [Capture](../screenshots/public-arena/saga-hall-3840.webp) |
| Classic Table | [Capture](../screenshots/public-arena/classic-table-1920.webp) | [Capture](../screenshots/public-arena/classic-table-3840.webp) |

Full 56-image matrix is retained in the CI `public-spectator-visual-acceptance`
artifact before later configurations clear browser output. These are controlled
populated discovery fixtures, not invented community records in the application.
Real empty discovery and backend privacy are tested separately. Physical Safari
and final all-route 200% zoom review remain separate completion-program gates.

## Build and performance limits

Production build passes. Rounded Vite output: entry 437.81 KB /140.21 KB gzip;
public view 12.43 KB /4.52 KB gzip and shared header 4.73 KB /2.11 KB gzip.
Protected App remains a separate 574.21 KB /169.74 KB gzip chunk with the existing
large-chunk advisory. These figures establish a smaller public startup graph, not
an authenticated Dashboard or server-throughput improvement. No browser Python,
neural library, backend schema, rules or dependency change is included.

## Protected-entry timer regression

Full CI exposed a Suspense timed-reveal deadlock when existing advisor tests pause
application timers before navigation. Repeated failing-worker logins subsequently
hit the unchanged login limiter and produced cascading failures. Entry loading
now resolves its dynamic module directly into component state, preserving code
splitting without relying on a fallback-reveal timer. The new frozen-clock entry
regression fails before this correction and passes afterward, without advancing
the clock. Entry, all advisor/localization cases and public discovery/replay cases
then pass together: **41 passed, 1 intentional skip**. No timer, authentication
limit or existing advisor assertion was weakened.

A subsequent full run passed protected initialization and the new public cases but
found two Workshop regressions. The navigation helper now resolves the visible
rail link or compact button at action time instead of branching before a lazy
shell mounts. Oversized parameter help now scrolls in the available space above
or below its trigger, preserving the trigger and six-pixel gap rather than
covering it and intercepting pointer events. All 49 tooltip unit tests pass, with
stricter trigger-clearance coverage; the four affected desktop/mobile browser
cases pass. Existing viewport, scroll, focus and pointer assertions remain intact.

The subsequent full CI passed backend verification and all 1,323 unit tests; main
browser validation had 366 passed, 20 skipped and two failures in the German zoom
tooltip test. Both failed before zoom: the corrected non-overlap layout already
required a scrollable region at the initial viewport, while the test expected a
tooltip. The regression now asserts the actual accessible region, trigger clearance
and all viewport bounds before/after zoom, preserving scrolling, description,
keyboard focus and Escape checks. Added unit coverage for text that fits the full
viewport but not the available space beside its trigger. Focused verification:
50 tooltip unit tests and six desktop/mobile browser cases passed; formatting and
TypeScript passed. Updated full CI remains required.
