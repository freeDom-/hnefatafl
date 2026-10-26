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
| `PLAYWRIGHT_CHROMIUM_EXECUTABLE=/usr/bin/chromium npm --prefix frontend run test:personal` | **10 passed**, no skips, production build, desktop/mobile, 4.4 minutes. |
| `.venv/bin/python scripts/audit_api.py` | **82 grants accounted for**, five new personal GET grants. |
| `.venv/bin/ruff check frontend/tests/start_personal_backend.py`; same path with `ruff format --check` | Passed. |

Full `make check` and retained account/registration/online/guest browser results
are pending at this draft snapshot and will be recorded before review readiness.
The successful dashboard run followed corrections to new test setup (201 creation,
Tablut's defender-first turn, filter selection after the existing login redirect,
and accessible combobox/guest-square selectors). Superseded interrupted runs are
not claimed as passes. Both final desktop/mobile screenshots were inspected; the
checks found no horizontal overflow. This evidence makes no production claim.

## Test design and limits

API tests use real authenticated sessions, SQLite, multiple accounts including an
administrator and a deterministic authoritative clock. They exercise ownership,
participant scope, safe fields, cursor bounds/context/ties/combined filters,
concurrent page membership changes, legacy retained-result semantics, both winning
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
still scan account history; no arbitrary-size performance benchmark is claimed.
The database is consistent within a response, not frozen between cursor pages.
