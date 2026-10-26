# Phase 5 Dashboard consolidation validation

[Documentation index](../README.md) · [Dashboard guide](../DASHBOARD.md) ·
[Phase 6 handoff](../designs/online-phase6-handoff.md)

The owner explicitly requested this refinement on the existing
`feat/phase5-personal-dashboard` branch and PR #17, targeting
`feat/online-multiplayer`. Dependency inspection found Phase 5 unmerged and the
integration branch at Phase 4 (`41bf1b2`); the owner then overrode the proposed new
branch/preceding-merge workflow. No merge, schema migration, version/release/tag,
deployment or production admission/timeout enablement is included.

## Coverage and current evidence

New real-session/SQLite tests cover role-scoped Hall filters, counts, independent
typed review, manipulated filters/IDs, private personal APIs, latest-three ordering
and filtering before limit, void withdrawal, cursor context and role revocation.
New frontend/browser tests cover Dashboard navigation/setup, shared rows, retained
selection outside filtered results, refresh/deep links, authorization, delayed reads,
responsive layout and read-only review. Existing gameplay/guest/registration/online
acceptance is adapted to explicit setup and read-only Hall, retaining server APIs.

The consolidation snapshot `adeaa19f5bb9073d1930838d0a787b0cc6086a73` passed
[Verify 105](https://github.com/freeDom-/hnefatafl/actions/runs/40331972044):
1504 backend passes / 6 hardware skips; 316 frontend passes; browser suites
66 legacy passes / 4 intentional duplicate skips, 6 registration, 10 online,
12 personal and 14 guest passes. This predates the additional polish below.
Local `make check` on that work failed after 1473 backend passes / 36 skips:
314 frontend passes / 2 TrainingDashboard timeout-related failures; production build
was not reached. CI success does not replace that local failure report.

Additional owner scope now implements shared auth spacing, automatic guest AI,
account-bound preset-only game creation with administrator-only AI pairs, modal
online duration inputs and automatically recovering lobby presentation. Removed
local-player creation/CRUD and unsupported restart cloning are covered separately
from out-of-band historical read/play fixtures. API inventory now contains 81
registered grants; schema remains 10. New focused and full validation is in progress.

Earlier local results (before the additional scope):

- `.venv/bin/python -m pytest -q tests/test_personal.py tests/test_security.py tests/test_online_api.py tests/test_game_management.py`: **105 passed**, one existing TestClient deprecation warning, **411.15 s**. The final additional mixed-category administrator latest-three case is included in the full run in progress, not this already-collected selection.
- `npm --prefix frontend test -- src/AppOnline.test.tsx src/Features.test.tsx`: **6 passed / 2 files** after fixture corrections.
- `npm --prefix frontend test -- src/App.test.tsx src/ModelSelection.test.tsx`: **15 passed / 2 files** after the resume change.
- `PLAYWRIGHT_CHROMIUM_EXECUTABLE=/usr/bin/chromium npm --prefix frontend run test:personal`: **12 passed**, no skips, **4.0 min**, before the final explicit Dashboard-resume assertions and spacing adjustment. A final rerun remains pending.
- `.venv/bin/mypy hnefatafl`: **47 source files passed**; TypeScript passed.
- `make docs`: **85 Markdown files / 629 local references** before the final evidence links.
- API audit: **84 registered grants accounted for**. The two added grants are read-only history adapters; database schema remains 10.

`make check` and fresh CI are running; final results will be recorded before delivery.
Development attempts are not accepted full passes: two exploratory unit runs were
interrupted while old fixtures were being replaced; an initial browser build
stopped at a TypeScript test-fixture error; the next browser run had **6 passed /
6 failed** (exact-label selectors and login/navigation synchronization). The corrected
12-case run above passed. CI Verify 100 passed 314 frontend cases and failed one
obsolete Hall-deletion test; it now exercises the retained training-cleanup callback
and its late move-response guard. Later CI and local results will identify their
actual source revision. Earlier Phase 5 evidence remains in
[its original record](online-phase5.md) and is not validation of this refinement.

Only Chromium desktop/mobile emulation is planned. No physical device, Safari,
Firefox, production SMTP/deployment, GPU/AI strength, rated policy or offline-startup
validation is claimed. Guest Python replay, worker transport and device storage boundaries remain unchanged.

## Additional-scope snapshot

The first focused creation-policy run had 4 passes and 1 obsolete removed-route
status assertion failure; after correction all **5 passed** (97.05 s, one existing
TestClient deprecation). Ruff and mypy (48 modules) pass. The focused frontend run
had **46 passes / 1 localization timeout** under local load, including all 16 guest,
10 new-game and duration/poll cases; the isolated localization rerun passed **3/3** (25.93 s).
The first broad changed-area native run had **148 passes / 22 failures** (845.39 s),
primarily former creation/CRUD/restart assumptions; corrections are being verified.
These are progress records, not a claim that full acceptance has passed.
