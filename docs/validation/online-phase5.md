# Phase 5 dashboard validation

[Documentation index](../README.md) · [Dashboard guide](../DASHBOARD.md) ·
[API/query contract](../designs/personal-dashboard-phase5.md) ·
[Phase 6 handoff](../designs/online-phase6-handoff.md)

The owner approved a private authenticated dashboard/history/review scope on
2026-10-26. Phase 4 PR #15 was initially open, then merged by the owner into
`feat/online-multiplayer` at `41bf1b2dd683ece1bfc1826de82411d7228490da`.
Independent unshared Phase 5 work was rebased onto that commit before combined
validation. Branch `feat/phase5-personal-dashboard` targets that integration branch;
its PR is left unmerged. Version remains 0.29.0. No production data/configuration,
admission/timeout activation, migration, release, tag or deployment is included.

## Delivered boundaries

The dashboard separates exact-owned account history from participant online
history and query-derived totals. Five authenticated GET routes provide bounded
summary pages and scoped read-only replay/detail; administrators keep personal
scope here while legacy Hall/spectator APIs retain their behavior. There is no
schema change or stored accounting. Online results use only the effective eligible
completed revision, with timeout abandonment for the timed-out player and void
withdrawal. Side-swapped pairs retain separate per-game results.

Review controls change local viewer state only. Guest navigation never reads the
slot, imports, uploads, archives or counts it. Existing setup/selection keys and
Phase 4 guest behavior remain intact. No online assistance, public access, rating,
match scoring or retention policy is added.

## Local evidence

| Command (repository root) | Local result |
| --- | --- |
| `.venv/bin/python -m pytest -q tests/test_personal.py tests/test_online_api.py tests/test_online_core.py tests/test_online_matches.py` | **132 passed**, before dependency rebase and final extra personal cases; one existing TestClient warning. |
| `.venv/bin/python -m pytest -q tests/test_personal.py` | **21 passed**, after Phase 4 rebase; one existing TestClient warning. |
| `npm --prefix frontend test -- --run src/PersonalDashboard.test.tsx src/Online.test.tsx src/AppOnline.test.tsx src/Auth.test.tsx` | **41 passed**, 4 files, before the final terminal-discovery buttons. |
| `PLAYWRIGHT_CHROMIUM_EXECUTABLE=/usr/bin/chromium npm --prefix frontend run test:personal` | **10 passed**, no skips, production build, desktop/mobile, 4.4 minutes, before header wrapping; final-source complete coverage is in CI below. |
| `PLAYWRIGHT_CHROMIUM_EXECUTABLE=/usr/bin/chromium npm --prefix frontend run test:personal -- --grep 'two accounts and administrator'` | **2 passed** after navigation wrapping, including administrator/user overflow assertions. |
| `PLAYWRIGHT_CHROMIUM_EXECUTABLE=/usr/bin/chromium npm --prefix frontend run test:e2e -- tests/analysis-localization.spec.ts tests/appearance.spec.ts --grep 'advisor highlights\|appearance changes'` | **4 passed**, desktop/mobile gameplay, German localization and appearance after navigation wrapping. |
| `PLAYWRIGHT_CHROMIUM_EXECUTABLE=/usr/bin/chromium npm --prefix frontend run test:registration` | **6 passed**, no skips. |
| `make check` | Docs, Ruff, formatting, mypy (46 source files) and TypeScript passed. Backend: **1,460 passed / 36 skipped / 1 failed**, one existing TestClient warning; an existing training-resume smoke exceeded its 60-second deadline under local load. Make stopped before frontend units/build. |
| `OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 .venv/bin/python -m pytest -q tests/test_training_resume_api.py::test_resume_switches_to_maiden_without_resetting_learning_state` | **1 passed**, unchanged, alone in 34.43 seconds; one existing TestClient warning. |
| `npm --prefix frontend test` | **312 passed / 38 files**, after the local aggregate run stopped. |
| `npm --prefix frontend run build` | Passed: TypeScript, production Vite build and pinned guest package; existing large-chunk advisory. |
| `PLAYWRIGHT_CHROMIUM_EXECUTABLE=/usr/bin/chromium npm --prefix frontend run test:guest -- --grep 'terminal results\|a stale tab'` | **4 passed**, no skips, production build, serialized desktop/mobile recheck after the interrupted run, 4.0 minutes. |
| `make docs` | Passed: **84 Markdown files / 618 local links/images/anchors**. |
| `.venv/bin/python scripts/audit_api.py` | **82 grants accounted for**, five new personal GET grants. |
| `.venv/bin/ruff check frontend/tests/start_personal_backend.py`; same path with `ruff format --check` | Passed. |

The successful dashboard run followed corrections to new test setup (201 creation,
Tablut's defender-first turn, filter selection after the existing login redirect,
and accessible combobox/guest-square selectors). Superseded interrupted runs are
not claimed as passes. Both dashboard desktop/mobile screenshots were inspected; the
checks found no horizontal overflow. This evidence makes no production claim.

## CI and follow-up

[Verify run 89](https://github.com/freeDom-/hnefatafl/actions/runs/40286611564)
passed **both jobs** on functional commit
`a45c1de963072c1616228c08d48ef994f712d34a`. All-extras backend: **1,491 passed /
6 skipped**, 25 dependency warnings, plus foundation/docs/Ruff/mypy. Skips are two
CUDA checks and four operational Intel OpenVINO GPU parity checks. Frontend:
**312 passed / 38 files**, formatting/types and production build. Browser stages:
**66 legacy passed / 4 intentional mobile duplicate skips**, **6 registration**,
**10 online**, **10 personal dashboard**, and **14 production-build guest** passed,
with no registration/online/dashboard/guest skips. These are CI results, not a
claim that all those complete suites passed locally. See
[PR #17 checks](https://github.com/freeDom-/hnefatafl/pull/17/checks) for the latest
head; a documentation follow-up does not relabel this functional run.

[Verify run 88](https://github.com/freeDom-/hnefatafl/actions/runs/40285157727)
on functional commit `649fe3fb167be355421d7262adebc6621e606ce0` passed its all-extras
backend job: **1,491 passed / 6 GPU hardware skips**, 25 existing dependency
warnings, plus Ruff/mypy/docs/foundation. Frontend units (**312 / 38 files**),
formatting/types and production build passed. The legacy browser step failed on
administrator header overflow after the new Dashboard tab; later fixture logins
then hit the unchanged login limit during worker restarts. This run is not an
all-green browser result. Header/navigation wrapping and a real administrator/user
overflow assertion address the regression; follow-up checks are recorded below.
Authentication limits were not relaxed.

An initial interrupted local guest run included a shared-artifact collision from
overlapping browser runners and a runtime startup timeout during native test load.
It is not claimed as a passing suite. The affected terminal-replay and stale-tab
cases then passed serially on both projects (four checks above). A local complete
online run was stopped after two passing desktop scenarios while native training
was still active. The full legacy suite was not rerun locally; its four focused
layout/gameplay checks passed. Complete retained legacy/online/guest evidence is
the successful CI run above. No local timeout or interrupted run is relabeled green.

## Test design and limits

API tests use real authenticated sessions, SQLite, multiple accounts including an
administrator and a deterministic authoritative clock. They exercise ownership,
participant scope, safe fields, cursor bounds/context/ties/combined filters,
concurrent page membership changes, stored retained-result semantics, both winning
sides, draw/concession/timeout/cancel/void, effective revisions, restart consistency,
batched query counts, paired grouping, analysis denial and replay without writes.
Frontend tests additionally cover request cancellation/late responses, empty/error/
retry, page/filter requests, translated controls and viewer-only replay.

The personal browser harness builds production assets and serves real FastAPI/CSP,
sessions and disposable SQLite. Fixture creation/admission/timeout enablement and
its separate loopback-only control server are guarded by a temporary development
data-directory prefix. No fixture route is added to the app. The clock is fixed;
controller snapshots fingerprint games, moves and result rows. Browser fault
injection delays real authorized responses or simulates a read outage; it does
not mock multiplayer authorization or result acceptance. Desktop and mobile
Chromium projects run serially. The existing guest suite still checks real local
Python/AI, IDB transactions, network silence and SQLite counts.

No physical phone, Safari, Firefox, GPU, AI strength, production deployment,
durable device storage or full offline startup is claimed. Query-derived counts
use aggregate scans over existing SQLite tables; no arbitrary-size or global-history performance benchmark is claimed.
The database is consistent within a response, not frozen between cursor pages.

## PR #17 review follow-up

The review follow-up removes SQLite account-result replay, its injected outcome
callback and the server's old-record result derivation. No legacy account records
are supported. Current writers already persist `recorded_result`; missing/null
results remain unfinished in personal summaries/review and `/api/stats`. Canonical
single-game validation, route permissions and global authenticated spectator
listing/detail remain intact. Scrubbed action/type/traceback logs accompany the
unchanged generic 503 response. Tests cover current writer invariants, result
absence at either cursor, retained replay, all five scrubbed error paths and
nonparticipant global visibility with personal detail denials.

The earlier command counts above belong to their stated commits, before this
follow-up. Fresh follow-up validation is recorded separately below.

| Follow-up command (repository root) | Result |
| --- | --- |
| `.venv/bin/python -m pytest -q tests/test_personal.py tests/test_game_management.py` | **51 passed**, one existing TestClient warning, 97.67 seconds. |
| `make docs` | Passed: **84 Markdown files / 619 local links/images/anchors**. |

Full `make check` and relevant personal/online browser revalidation are pending
at this follow-up snapshot; no earlier run is claimed as fresh validation.
