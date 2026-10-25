# Phase 3 online UI validation

[Documentation index](../README.md) · [Online play](../ONLINE_PLAY.md) ·
[Phase 4 handoff](../designs/online-phase4-handoff.md)

Implementation branch: `feat/phase3-online-ui`, based on integration commit
`de0b64b36fe3445b01062fed51e9e3008c1c8e8c` after Phase 2 PR #12 merged.
PR base is `feat/online-multiplayer`. No release/version bump, deployment, admission
activation or production-data operation belongs to this work.

## Delivered behavior and boundaries

English/German authenticated lobby and `/online/games/{id}` views provide bounded
pagination, My games recovery, all three side choices/match modes, creation,
waiting join/cancel, real moves, turn-bound confirmed concession, effective results,
independent sibling boards/deadlines/versions and viewer-local replay. Selection is
presentation only. Active analysis remains absent and legacy adapters retain their
backend online-ID guards. No aggregate scoring, public visibility or production
outage policy is introduced.

Creation retains one account-scoped request UUID/configuration in memory and tab
session storage until accepted or explicitly discarded. A retry is deliberate;
409 is not interpreted as idempotent success. Quota errors retain the request and
offer own-games/cancellation/waiting recovery. Client code does not duplicate
configurable quota limits.

Reads coalesce with abort/revision guards and bounded backoff. Mutations submit
once, then reload on success, conflict or uncertainty. A successful timeout response
with `action_accepted=false` is distinguished from an accepted requested action.
Stale state remains visible with actions disabled. Existing seats do not depend on
admission eligibility. Account-keyed state, request cancellation and Auth generation
guards prevent delayed old-account responses restoring protected views.

The only backend contract extension is authenticated
`GET /api/online/availability` -> `{enabled, eligible}`. Existing contracts exposed
neither the admission switch nor `security_context.online_eligible` to frontend
code. The endpoint uses that predicate directly and reveals no account/private
configuration details. Its route is explicitly granted `play` and inventoried.
No schema/lifecycle/timeout/admission policy changes were needed.

## Validation results

Validated on 2026-10-25. The initial implementation commit is
`3b8f17eaf23d0efe463bd1deb8b31a76e9d9209d`. A final frontend follow-up displays
each game's server-assigned creator side in the configuration summary, including
swapped return games; focused/unit/browser checks assert both two-game modes. [PR #13](https://github.com/freeDom-/hnefatafl/pull/13) targets
`feat/online-multiplayer` and remains unmerged. No production setting was enabled.

| Command | Result |
| --- | --- |
| Focused Vitest command below | **44 passed**, 6 files |
| Backend selection below | **162 passed**, no skips, 1 existing deprecation warning |
| `make check` (initial implementation; backend code unchanged afterward) | **Passed**: **1413 backend passed / 36 skipped**, **269 frontend passed**, 34 frontend files; Ruff, mypy (43 source files), TypeScript, formatting, docs and production build passed |
| `npm --prefix frontend test`; `npm --prefix frontend run build`; `npm --prefix frontend run format:check` (return-side follow-up) | **269 passed**, 34 files; TypeScript, production build and formatting passed |
| `PLAYWRIGHT_CHROMIUM_EXECUTABLE=/usr/bin/chromium npm --prefix frontend run test:online` | **10 passed**, no skips; final run including return-side assertions, 5 desktop + 5 mobile |
| `PLAYWRIGHT_CHROMIUM_EXECUTABLE=/usr/bin/chromium npm --prefix frontend run test:registration` | **6 passed**, no skips; final sequential run |
| `make docs` | **Passed**: 76 Markdown files, 553 local links/images/anchors |
| `.venv/bin/python scripts/audit_api.py` | **Passed**: 77 registered grants |
| `.venv/bin/ruff check frontend/tests/start_online_backend.py frontend/tests/generate_online_fixtures.py` | **Passed** |
| `.venv/bin/ruff format --check frontend/tests/start_online_backend.py frontend/tests/generate_online_fixtures.py` | **Passed**, 2 files |

The focused commands, run from repository root:

```sh
(cd frontend && ./node_modules/.bin/vitest run src/Online.test.tsx src/useOnlinePoll.test.tsx src/Auth.test.tsx src/Board.test.tsx src/api.test.ts src/AppOnline.test.tsx)
.venv/bin/python -m pytest tests/test_online_api.py tests/test_online_core.py tests/test_online_matches.py tests/test_online_admission.py tests/test_security.py -q
```

Real-browser acceptance uses FastAPI/SQLite and isolated ordinary accounts:
single game with a move/concession/replay, simultaneous swapped seats, sequential
return discovery, reconnect, competing stale concession, spectator mutation denial,
legacy analysis denial, disabled admission and paused adjudication with controlled
time. No long real-time timeout wait or production account was used. Local Chromium
154 screenshots were inspected at desktop and 390-pixel mobile width; lobby/game
pages had no horizontal overflow. Long account names now occupy a separate mobile
header row without overlapping the brand.

[Verify run 76](https://github.com/freeDom-/hnefatafl/actions/runs/40237649511)
passed on the implementation commit: both Python and browser jobs succeeded.
Its all-extras backend run passed **1443 tests / 6 skipped**; skips were CUDA and
Intel OpenVINO GPU hardware checks. Optional dependencies account for the difference
from the local backend run. The browser job passed **269 unit tests**, build/types/
formatting, the complete legacy suite (**66 passed / 4 intentional skips**),
registration (**6 passed**) and online acceptance (**10 passed**). Check
[the PR checks](https://github.com/freeDom-/hnefatafl/pull/13/checks) for validation
of the final head, including the return-game summary correction. Backend code
remains the version covered by the full local run.

Two supplemental local legacy-browser development runs were interrupted: the first
had 15 passes, 1 failure after a development reload lost the selected page, 1
interruption and 53 not run; the second had 18 passes, 1 CPU-training completion
timeout while backend tests ran concurrently, 1 interruption and 50 not run. They
are not accepted as full-suite passes. Final local multiplayer/registration runs
were sequential after `make check`, and the clean full legacy CI run passed. One
parallel focused run also exceeded the existing 20-second test bound under that
load; the final focused run passed all 44 tests without raising timeouts or
weakening functional assertions. The test helper flushes initial passive effects
before interacting with a newly loaded position.

Unit fixtures are snapshots from actual `OnlineCore` creation/join/move/concession/
replay/sibling/timeout projections, not invented fields. Regenerate explicitly with
`PYTHONPATH=. .venv/bin/python frontend/tests/generate_online_fixtures.py` using
the [isolated generator](../../frontend/tests/generate_online_fixtures.py). IDs
change when regenerated; no session/password/private audit data is written. Component/hook coverage includes
all side/mode combinations, retained creation conflicts/denials, existing-seat
eligibility, duplicate prevention, no optimistic board, timeout rejection, stale
versions, 401/403/422/429/503, replay, sibling reads, clock uncertainty, German labels,
account switching, logout and late-response/backoff cancellation. A batched
double-submit check verifies that the retained UUID still matches the one submitted
even before pending state renders.

## Limits

Browser results use Chromium desktop and mobile emulation, not physical devices or
other browser engines. Device-clock countdowns are approximate; no undocumented
server-clock field is assumed. Retained requests survive reload only where tab
session storage is available; own-games recovery remains available otherwise.
No production SMTP/deployment, anonymous visibility, retention expiry, rated play,
offline guest play, GPU or strength validation is claimed. See the
[Phase 4 handoff](../designs/online-phase4-handoff.md) for remaining owner decisions.
