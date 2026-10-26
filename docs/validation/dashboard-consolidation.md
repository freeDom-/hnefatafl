# Phase 5 Dashboard consolidation validation

[Documentation index](../README.md) · [Dashboard guide](../DASHBOARD.md) ·
[Phase 6 handoff](../designs/online-phase6-handoff.md)

The owner directed this refinement onto `feat/phase5-personal-dashboard`, PR #17,
targeting `feat/online-multiplayer`, overriding the usual new-phase branch workflow.
The PR remains unmerged. There is no database schema migration, application version
bump, tag/release, deployment, rating or production admission/timeout activation.

## Delivered scope

- Dashboard is the authenticated landing; obsolete landing URLs retain selection
  through canonical redirects. Dedicated gameplay and review links survive reload.
- Dashboard and Hall share compact rows. Server queries authorize/filter before
  counting/paging; Dashboard stays personal, normal Hall reads are participating
  online records, and trusted current administrators can browse across categories.
- Current account-result metadata determines completion without legacy result
  reconstruction. Canonical single-game replay remains separate. Unexpected
  personal/history errors receive scrubbed logs and generic HTTP 503 messages.
- Auth forms share spacing and put the real guest anchor below primary login.
  Guest AI advances automatically through the existing worker/Python boundary,
  with explicit error retry and cancellation/generation guards.
- New account games bind human identity to the authenticated account and resolve
  four fixed presets on the server. AI vs AI creation/restart requires a current
  administrator. Local-player CRUD, old setup controls/preferences and unused
  write helpers are removed. Historical game modes/configurations/identities
  remain read/play shims; unsupported setups cannot be cloned through restart.
- Online creation uses the shared modal, focus handling and controls. Integer
  days/hours/minutes retain the approved 60–1209600-second aggregate; zero and
  14 days plus extra time are rejected. Retained non-minute requests keep exact
  seconds. Lobby polling recovers on reconnect/focus/visible tabs without a
  persistent connected banner or manual lobby Refresh.

## Current evidence

[Verify 112](https://github.com/freeDom-/hnefatafl/actions/runs/40368171689) passed
both jobs on implementation/test snapshot `b472c0b`. Counts below distinguish that
complete CI run from focused local checks and the separate local failures.

| Check | Actual result |
| --- | --- |
| Verify 112, complete backend | **1510 passed, 6 hardware skips, 25 warnings**, 572.72 s; Ruff/104 formatted modules, mypy/48 modules, foundation and docs/85 files/633 links passed. Includes personal/history, registration/online authorization, actual local/remote training, model loading and legal play. |
| Verify 112, complete frontend | **338 passed / 39 files**, 33.34 s; formatting, TypeScript and production build passed. |
| Verify 112, all production browser suites | **116 passed / 4 intentional duplicate skips**, desktop/mobile Chromium: legacy **66 passed / 4 skips** (6.2 min), registration **8 passed** (22.2 s), online **14 passed** (59.4 s), personal **12 passed** (35.4 s), guest **16 passed** (2.4 min). |
| New account creation policy, local | **5 passed**, one existing TestClient deprecation, 97.05 s. Covers presets, account identity, forged bodies, transactional role rechecks, restart denial and retained historical play. |
| Shared modal/App/Online/Guest units, local | **56 passed / 3 files**, 70.64 s after shared focus/layout changes. |
| Registration/auth browser suite, local | **8 passed**, desktop/mobile Chromium, 3.3 min; includes keyboard guest action order and shared auth spacing. |
| Focused online browser suite, local | **4 passed**, desktop/mobile Chromium, 2.2 min; automatic lifecycle/reconnect/visibility updates, common modal spacing, Escape/focus, invalid aggregate durations and the 14-day boundary. |
| Advisor/replay browser suite, local | **6 passed**, desktop/mobile Chromium, 3.4 min. Historical fixtures use ordinary real-session reads/moves/analysis; playback time is paused only while opening a retained AI position. Search and recorded moves use the real engine/API. |
| API inventory | **81 registered method/path grants accounted for**. The three local-player CRUD grants/routes are removed; global authenticated online listing/detail and private personal scope retain their permissions. Schema remains 10. |

The local production guest suite finished with **15 passed / 1 failed**, 12.9 min.
The first desktop worker startup did not become ready within its 30-second
expectation and showed the explicit retry UI; that case has not passed locally.
All other cases passed, including the mobile persistence/CSP/no-API case,
automatic AI-first creation/restoration, replacement, invalid histories, terminal
replay, memory-only storage, stale tabs and account switching.
The complete guest suite subsequently passed in Verify 112; this does not turn
the local startup failure into a pass.

The personal acting-seat test now explicitly chooses attackers in Brandubh before
making its versioned human move. The corrected full production personal suite
passed **12/12** locally on desktop/mobile, **2.8 min**, including fresh Dashboard
resume, authorization, canonical read-only review and late account-switch responses.

A prior `make check` attempt passed docs, Ruff/formatting, Prettier, mypy and
TypeScript, then was deliberately interrupted during pytest to fix known obsolete
test assumptions. It is not a full pass. The final local `make check` retry
**passed with exit status 0**, within its 45-minute limit (about 41 min total).
Docs, Ruff/formatting, Prettier, mypy and TypeScript passed, followed by **1480
backend passes / 36 skips / 1 warning** (2030.78 s), **338 frontend passes / 39
files** (284.16 s) and the production build. Local optional-dependency/hardware
skips differ from the complete all-extras CI environment. The build retains its
existing large-chunk advisory; it completed successfully.

## Development attempts and prior consolidation

Earlier additional-scope attempts are not accepted full passes:

- Changed-area native tests: **148 passed / 22 failed**, 845.39 s, while former
  creation/CRUD/restart assumptions were being migrated to historical fixtures.
  An intermediate correction selection had three collection errors while fixture
  imports were being edited; it supplied no accepted test result.
- Verify 106/107: **1507 passed / 3 failed / 6 skipped** in backend runs. Removed
  routes can return 403 or 404 depending on the built shell; tests now require
  denial and route absence. The remote-model scenario now uses the current
  background advisor API. Verify 108 passed the complete backend after correction.
- A local correction selection had **2 passed / 1 HTTP read timeout**, 139.18 s.
  The timeout occurred while polling real remote training before the advisor
  assertion. It was not counted as a pass; the subsequent full CI run passed it.
- Focused frontend attempts exposed removed setup fields and one localization
  timeout; corrected fixture tests passed **14/14**, and isolated localization
  passed **3/3**. Verify 108 then passed all 338 frontend cases.
- An initial online browser run had **9 passed / 3 failed**, 5.9 min: the new
  scenario counted unrelated retained games and later logins exhausted the shared
  fixture login budget. Each disposable case now resets login-attempt state,
  without changing production limits, and tracks its own game. Further focused
  failures used the wrong conceding seat and exact wrapping-label selectors;
  the corrected four-case browser run passed.
- Advisor browser corrections initially had selector/timeouts and an autoplay
  race. The final six-case run above passed, retaining real search and historical
  review assertions.
- Verify 108 passed all backend/frontend checks but had **62 legacy browser
  passes / 4 failures / 4 skips** before the advisor fixture/selector corrections.
  Verify 109 passed the legacy, registration and online suites but had **10
  personal passes / 2 failures** from the obsolete acting-seat assumption, fixed
  by explicitly selecting human attackers in the resume test.
- Verify 111 passed backend/frontend and the first four browser suites; guest
  had **14 passes / 2 failures** because the new AI-first polling assertion
  dereferenced the initially empty save slot. The assertion now waits for a
  persisted move without changing application/worker behavior or timeouts.
  Verify 112 passed all suites after that correction.

The earlier consolidation-only snapshot `adeaa19` passed
[Verify 105](https://github.com/freeDom-/hnefatafl/actions/runs/40331972044):
1504 backend passes / 6 hardware skips, 316 frontend passes, 66 legacy browser
passes / 4 duplicate skips, 6 registration, 10 online, 12 personal and 14 guest
passes. This predates the additional scope. Its local `make check` had 1473 backend
passes / 36 skips, then 314 frontend passes / 2 TrainingDashboard failures related
to local timeouts; the production build was not reached. CI success does not turn
that local failure into a pass. Original Phase 5 evidence remains in
[its historical record](online-phase5.md).

## Limits

Browser evidence uses Chromium desktop and mobile emulation. No physical device,
Safari/Firefox, CUDA hardware execution, AI strength ranking, production SMTP,
deployment or full offline-startup validation is claimed. Guest engine/worker and
storage boundaries remain intact; account and online records stay authoritative on
the server. Ratings and production rollout remain separate owner decisions.
