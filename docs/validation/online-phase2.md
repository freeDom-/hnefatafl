# Phase 2 online-core validation

[Documentation index](../README.md) · [Online operations](../ONLINE.md) ·
[Phase 3 handoff](../designs/online-phase3-handoff.md)

Baseline: `44d0ae7159bd8ec5653134adbc02d9b137025c92` on
`feat/online-multiplayer`. Remote PR #6 and #9 were verified merged there before
creating `feat/phase2-online-core`. The working tree was clean. The Phase 2 PR
targets the integration branch, never main; no deployment, release, version change,
production mail, production database inspection or production recovery is performed.

## Source-based implementation checklist

- `lifecycle.py`, `engine.py`: reuse immutable transitions, engine legality,
  serialization and pinned versions; no parallel rules engine.
- `online_migrations.py`, `storage.py`: schema 10 with independent online match/
  game/ply/result/audit/gate tables; strict types, distinct immutable seats and
  transactionally unique identities. Local ownership/records stay separate.
- `online_core.py`: writer-lock time sampling, current session/account checks,
  expected versions, atomic deadlines/moves/results/return games, shared expiry,
  private void audits and derived effective-result accounting.
- `security_context.py`, `auth.py`, `online_api.py`: trusted session context, exact
  route grants, existing CSRF plus Origin/JSON/body limits, sanitized errors and
  dedicated safe projections.
- `server.py`: bounded lifespan reconciliation/sweeping/shutdown and a common guard
  against online IDs entering existing analysis, AI, analytics, replay/branch/export
  controls. Separate storage keeps tournaments/training/local histories isolated.
- `deployment.py`: retain live security and online authority on compatible restore,
  revoke saved capabilities, audit/pause timeouts, refuse incompatible schemas;
  snapshot-only recovery also pauses/revokes and cannot promise post-backup progress.
- `tests/test_online_core.py`, `test_online_api.py`, `test_online_matches.py`,
  `test_online_migrations.py` and deployment/security regressions: independent
  connections/processes, barriers, controlled UTC clocks and disposable databases.

Owner refinements are recorded in [the guide](../ONLINE.md#confirmed-behavior-and-owner-decisions):
explicit/random sides at join, simultaneous/sequential two-game matches and retained
existing-seat access after verification changes. Anonymous visibility, waiting-lobby
retention, rated policy and production outage resolution remain unapproved/gated.

## Validation record

The final full local check completed successfully, with narrowly scoped final
recovery/test-fixture checks noted below. Test storage is
isolated through `tests/conftest.py`/`tmp_path`; no real SMTP or browser run is part
of the new backend tests. Concurrency synchronizes with barriers/events/SQLite
locks, not sleep timing. Hardware and browser behavior are not inferred from pure
backend tests. Existing dependency/tool versions and application 0.29.0 are retained.

- `make check`: **exit 0**; backend **1380 passed, 36 skipped** in 1450.39 seconds;
  frontend **233 passed across 30 files**. Ruff lint/format, Prettier, mypy (43 source
  files), TypeScript and the production Vite build passed. The run collected 1416
  backend cases; the subsequently added empty-snapshot regression is covered by
  the final 27-case deployment run below. The strengthened duration fixture was
  also checked separately after collection. PR CI runs the entire final head.
- `make docs`: **73 Markdown files, 526 local links/images/anchors**, all reachable.
- `.venv/bin/python scripts/audit_api.py`: **75 registered grants** accounted for;
  extra Ruff lint for the modified inventory script passed too.
- `.venv/bin/python -m pytest tests/test_online_core.py::test_deadline_time_is_sampled_after_locked_state_reload -q`:
  **one passed** in 7.49 seconds after the final clock-ordering change.
- `.venv/bin/python -m pytest tests/test_online_core.py::test_duration_is_strict_at_service_and_database_boundary -q`:
  **seven passed** in 14.28 seconds with the strengthened independent-match fixture.

Optional CUDA/OpenVINO checks were skipped where unavailable. The existing
third-party TestClient deprecation and Vite large-chunk warning did not fail the
checks. No new browser test was run locally in this backend phase; the unchanged
frontend regression and production build were checked, while the PR workflow runs
the existing browser and account suites. No physical device or GPU claim follows.

Observed intermediate results/corrections:

- `.venv/bin/python -m pytest tests/test_online_core.py tests/test_online_api.py tests/test_online_matches.py tests/test_online_migrations.py tests/test_account_migrations.py tests/test_deployment.py -q`:
  **146 passed** in 216.14 seconds before the final locked-reload timing regression.
- `.venv/bin/python -m pytest tests/test_security.py::test_object_ownership_all_game_routes_statistics_and_players -q`:
  **one passed** after the exact administrator-only denial correction.
- Seven targeted writer-lock/session/status/role timing, return-game rollback and
  unknown-response-outcome cases passed after moving the authoritative sample
  after the locked state/version reload. A first `make check` was deliberately
  stopped early for this source-ordering adjustment; it is not counted as passed
  or as a pre-existing test failure.
- After full-suite collection, the duration fixture was strengthened to use a
  fresh match identity and prove the identical row succeeds with a valid duration;
  this prevents another uniqueness constraint masking a type/range bug. All seven
  targeted duration cases passed (14.28 seconds), including SQLite float/string
  rejection. No production code changed for this fixture improvement.
- Recovery review added a regression for a verified snapshot with no database.
  Its first run failed because the existing restore branch bypassed schema checks
  and replaced the live root. The baseline restore had the same unchecked branch;
  explicit refusal now protects live authority. This recovery guard was added after
  the full local suite had collected. `.venv/bin/python -m pytest tests/test_deployment.py -q`
  then passed **27 tests** in 43.32 seconds, including every prior restore path.
  Ruff lint/format (92 files) and mypy (43 source files) passed afterward too.
- Initial core run: 47 passed, two failed. The failures exposed a stale participant
  retry receiving turn denial (403) after another move, rather than the required
  version conflict. Membership is now checked before expected version, with current
  turn checked afterward; unauthorized actors still cannot trigger expiry.
- The first API/core/migration pass then had 83 passed, one new assertion expecting
  local creation status 200; actual existing behavior is 201 and the assertion was
  corrected without changing the endpoint.
- All 19 initial match-mode tests passed, covering random assignment timing,
  simultaneous/sequential activation, process races, rollback and a standard-board
  engine-terminal line. Further boundary/parallel/restore cases were added afterward.
- A broader focused run had 197 passed and one existing route-derived ownership
  assertion fail: it assumed every `{game_id}` route used ordinary play permission.
  The new administrator-only void route correctly returns 403. The test now checks
  that exact permission-specific denial, retaining its previous 404/422 checks for
  ordinary ownership routes.
- Initial Ruff checks caught unused imports in new test files; removed. Mypy passed
  all 43 source files during integration. The API inventory accounts for 75 grants.
- One additional-permission test invocation was not executed because automatic
  approval review could not complete after a usage limit. After the owner asked to
  continue, the normal permission path succeeded; no review bypass was used.

## Security/concurrency evidence and limitations

### Authenticated creation-abuse follow-up

The subsequent security follow-up adds defaults of five waiting lobbies per
creator and ten new matches per rolling 900 seconds. Checks share the creation
write transaction; cancellation does not refill the rolling budget. Two-game
matches count as one intent and automatic return games do not spend it again.
Current records remain the budget authority across connections/processes/restarts.
Two fixed monitoring rows retain quota-denial counts, with sanitized warnings
limited to one per reason/minute. Administrator-only `/api/online/monitoring`
reports these counters and database/WAL growth; exact route grants now total 76.
Optional telemetry/indexes extend the unreleased schema 10 without rewriting
authority or rejecting older schema-10 snapshots that lack telemetry.

Initial `.venv/bin/python -m pytest tests/test_online_admission.py -q` passed
29 cases in 22.50 seconds. Two subsequent cases cover existing schema-10 startup
and an older snapshot retaining the current budget/counters.

- `.venv/bin/python -m pytest tests/test_online_admission.py tests/test_online_core.py tests/test_online_api.py tests/test_online_matches.py tests/test_online_migrations.py tests/test_security.py tests/test_deployment.py -q`:
  **191 passed** in 223.70 seconds, including the final 31 admission cases.
- `.venv/bin/ruff check hnefatafl tests` and
  `.venv/bin/ruff format --check hnefatafl tests`: passed (93 formatted files).
- `.venv/bin/mypy hnefatafl`: passed (43 source files).
- `.venv/bin/python scripts/audit_api.py`: passed (76 grants).
- `make docs`: passed (73 Markdown files, 527 local references).

The full repository and existing frontend/browser regression suites run in CI
on the follow-up head; no additional local browser run or full `make check` rerun
is claimed for this backend-only follow-up. No frontend, application-version,
release or production configuration change is part of it.

The suites assert durable rows as well as responses: competing joins/moves, creator
self-join, cancellation, request/sweeper and two-sweeper races, microsecond boundaries,
status/session/role changes while blocked on a writer lock, paused/restarted expiry,
rollback after state/move/result/audit/return writes, unknown commit outcomes,
void revision reversal and safe projections. SQL-backed results, not Python locks
or pure lifecycle tests alone, support at-most-one committed contributions.

Schema-9 and representative schema-8 upgrades, collision refusal, newer-schema
rejection and compatible/incompatible/snapshot-only restore are tested on disposable
roots. Production recovery, real users/network conditions, hardware acceleration
and production SMTP are not claims of this phase. No lobby/spectator frontend was
implemented. Phase 3 prerequisites and remaining decisions are in the handoff.
