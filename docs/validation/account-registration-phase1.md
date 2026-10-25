# Phase 1 registration validation and handoff

[Documentation index](../README.md) · [Registration operations](../REGISTRATION.md) ·
[Milestone workflow](../DEVELOPMENT.md#online-milestone-workflow)

Phase baseline: integration branch `feat/online-multiplayer`, commit
`004d21855858f73705261e2db9e5fd6d5f8cf2b7`. Remote PR #6 was confirmed merged
into that branch before implementation; its target did not require another
retarget operation. Phase implementation branch: `feat/phase1-account-registration`.
The phase PR targets the integration branch, not main. No PR merge, production
migration, deployment, real email, release, tag or version change is authorized
or performed by this implementation.

## Delivered scope

Shared account policy, schema-9 collision preflight/migration, five explicitly
declared public account POST routes, digest-only purpose/address-bound tokens,
transactional reset/session invalidation, durable global/IP/identity budgets,
leased bounded SMTP delivery and restart/retry behavior. The auth shell has
English/German signup, resend, explicit verification, forgotten-password and reset
forms, with fragment removal, same-tab navigation and recovery guidance.
Existing account IDs, hashes/roles/status, ownership and single-player access
remain. Training, worker protocol, engine and histories are outside this change.

Current operator choices, defaults and compatibility boundaries live in the
[operations guide](../REGISTRATION.md), including the requirement for a manual
security reconciliation before any downgrade across account schemas. Current
code declines a mismatched security restore rather than silently overwriting
live password/role/verification state.

## Validation record

All backend tests use disposable SQLite roots through `tests/conftest.py` and
`tmp_path`; mail is fake and domain retry/expiry clocks are controlled explicitly.
Browser flows use a guarded development-only in-memory fake mailbox, temporary
storage, and loopback services. Real SMTP/provider delivery, hardware GPUs and
physical phones are not claimed by these checks.

The final local `make check` completed successfully. Intermediate failures and
their corrections are retained below so they are not misrepresented as an
initially clean run. Runtime: Python 3.12.14, Node 24.19.0, system Chromium
154.0.8037.57; application/dependency versions were not changed.

- `make check`: exit 0. Backend: **1262 passed, 36 skipped** (1098.96 seconds).
  Frontend: **233 passed across 30 files**. Ruff lint/format, frontend Prettier,
  mypy (39 source files), TypeScript and the production Vite build all passed.
  Skips cover unavailable optional backends/hardware; no CUDA/OpenVINO execution
  is claimed. The existing third-party TestClient deprecation and Vite large-chunk
  warning did not fail the checks.
- `make docs`: 70 Markdown files and 489 local links/images/anchors passed.
- `.venv/bin/python -m pytest tests/test_registration.py tests/test_account_migrations.py tests/test_security.py tests/test_deployment.py -q`:
  106 passed before the final four targeted additions below.
- Three targeted cases for in-flight old-password login/reset, account binding and
  chunked auth bodies passed; the development capture guard test passed separately.
- `.venv/bin/python -m pytest tests/test_registration.py tests/test_security.py -q`:
  82 passed after the final login-budget transaction change, including its new
  storage-bound regression case. Ruff lint and mypy (39 source files) passed too.
- `npm --prefix frontend test -- src/Auth.test.tsx src/AccountForms.test.tsx`:
  10 passed; `npm --prefix frontend run typecheck` passed.
- `PLAYWRIGHT_CHROMIUM_EXECUTABLE=/usr/bin/chromium npm --prefix frontend run test:registration`:
  six cases passed (desktop Chromium and iPhone-13 Chromium emulation) covering
  signup → explicit verification → login, reset → revoked sessions → new-password
  login, German resend/replaced links and safe token navigation. Transport was
  fake/in-memory; no actual provider SMTP was contacted.
- `PLAYWRIGHT_CHROMIUM_EXECUTABLE=/usr/bin/chromium npm --prefix frontend run test:e2e -- authentication.spec.ts --project=chromium`:
  **one passed**, covering existing login, permissions/navigation, reload and logout
  with registration disabled. The rest of the existing browser suite is delegated
  to the PR's required browser CI job; it was not run locally in full.
- `.venv/bin/python -m pytest tests/test_registration.py::test_atomic_password_reset_revokes_all_sessions_across_instances -q`:
  **one passed** after adding an explicit independent-process check to the existing
  reset transaction/rollback test. Both old session tokens are rejected through a
  separate Python process using the same disposable database. This final test-only
  assertion was added after the full suite had collected; its first targeted run
  caught a missing `json` import, which was added before the passing rerun.
- `.venv/bin/python scripts/audit_api.py`: 63 API method/path grants accounted for.
- Extra Ruff lint/format checks passed for
  `frontend/tests/start_registration_backend.py`, outside the Makefile's normal paths.

- An initial focused TestClient run under restricted execution networking hung;
  it was stopped. The supported additional network permission resolved it; the
  first complete new backend suite passed 54 tests. This was environmental, not
  evidence that production networking or real SMTP worked.
- Existing security tests expected registration to be an undeclared route and
  schema version 8. Those assertions were deliberately updated for the declared
  public route/strict privilege rejection and schema 9; the security/migration/
  deployment suite subsequently passed 106 focused cases before final additions.
- Initial TypeScript checks caught invalid Testing Library `exact` options and
  an untyped mock; corrected. An npm formatter command using root-relative paths
  from the wrong directory failed; corrected by running in `frontend`.
- Browser tests exposed same-tab verification fragment navigation; a hash-change
  handler and regression test now keep tokens in memory and require explicit POST.
  The six-case harness also exceeded the production signup-IP budget of five;
  its development-only budget is explicitly 20. Production defaults were retained.
- The disabled-registration unit test now waits for configuration before pressing
  the signup button, matching the new loading/disabled control behavior.
- The first full `make check` found two additional legacy migration assertions
  expecting schema 8 in `test_identities.py` and `test_tournaments.py`. Their
  expected version is now 9; the retained JSON, rune, history and model-metadata
  assertions remain intact. These failures were introduced by the schema-version
  change, not attributed to a pre-existing functional failure.
  That initial run ended with two failed, 1259 passed and 36 skipped; the final
  full rerun above passed both migration cases and the added security coverage.

## Remaining decisions and next phase

Operators must approve sender/public origin/provider credentials, privacy wording,
retention, support/recovery procedures, rate/queue budgets and monitoring before
enabling registration. Live deliverability and SMTPUTF8 support need provider
validation; no real mail or production credentials were used here.

Before multiplayer, integrate `online_eligible` with actual participant actions,
approve outage/rated/side policies and implement the Phase 0 persistence/deadline/
result concurrency and crash tests. No gameplay endpoint, Elo/ladder, achievement,
history redesign or production offline mode is part of Phase 1.
