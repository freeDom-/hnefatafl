# Phase 0 validation and handoff

[Documentation index](../README.md) · [Assessment](../designs/online-phase0-assessment.md) ·
[Design](../designs/online-phase0.md) · [Registration tasks](../designs/registration-phase1.md) ·
[Offline reproduction](../../spikes/browser_offline/README.md)

Validation date: 2026-10-25. Baseline: `5bd32770f25103d9232537fe63cbeaa162a1952f`.
Tests use disposable directories; `tests/conftest.py` sets an isolated
`HNEFATAFL_DATA_DIR` before the application is imported. Spike artifacts and logs
are under ignored `work/phase0/`, with no application server or data root needed.
No production data, training artifacts, migration, release, deployment, version
bump or hardware inference settings were changed.

## Validation record

- `.venv/bin/python -m pytest tests/test_lifecycle.py tests/test_offline_spike.py -q`:
  40 passed (37 lifecycle cases and three native spike/harness checks).
- `make check`: **passed, exit 0**. Documentation, Ruff lint/format, frontend
  formatting, mypy (34 source files), TypeScript, backend tests, frontend tests
  and production build all succeeded. Backend: **1190 passed, 36 skipped** in
  1135.26 seconds. Frontend: **199 tests passed across 27 files** (Vitest 3.2.7).
  Vite 7.3.7 built successfully. Skips concern unavailable CUDA hardware and
  optional OpenVINO dependency/operational Intel GPU support. No hardware support
  beyond the actually executed CPU/browser checks is inferred from these skips.
- `make lint typecheck`: passed again after the final lifecycle audit-field
  change. The focused 40 tests also passed after that change; the three native
  spike checks passed after the final staging-directory guard adjustment.
- `make docs`: passed separately; 67 Markdown files and 462 local
  links/images/anchors, all reachable from the index.
- `.venv/bin/ruff check spikes/browser_offline` and
  `.venv/bin/ruff format --check spikes/browser_offline`: passed. The standalone
  scripts are outside the Makefile's ordinary Ruff paths.
- `frontend/node_modules/.bin/prettier --check spikes/browser_offline/*.js spikes/browser_offline/*.mjs spikes/browser_offline/index.html`:
  passed. Actual execution below additionally validates browser script syntax.
- `.venv/bin/python spikes/browser_offline/prepare.py`: downloaded five Pyodide
  0.27.7 core assets and staged unchanged Python engine/baseline sources.
- `SPIKE_CHROMIUM=/usr/bin/chromium node spikes/browser_offline/verify.mjs work/phase0/measured`:
  passed Chromium 154.0.8037.57; 34 fixtures on all three rulesets before/after
  network disabled, legal human/local-AI moves, fresh offline reload and a UI
  interaction while the Python worker was busy. Exact measurements and inventory:
  [browser-offline-phase0.json](browser-offline-phase0.json).
- Initial ordinary-sandbox `git fetch origin main` and `curl -I --max-time 15
  https://cdn.jsdelivr.net/pyodide/v0.27.7/full/pyodide.js` failed to connect to the
  proxy; the browser server initially failed `listen EPERM 127.0.0.1`. Repeating
  with the execution tool's supported additional network permission succeeded.
  These environmental restrictions were resolved; they are not an architectural
  blocker. GitHub's connected API also confirmed the baseline matches `main`.
- During development, `.venv/bin/python -m py_compile spikes/browser_offline/bridge.py
  spikes/browser_offline/fixtures.py spikes/browser_offline/prepare.py` caught an
  unmatched closing parenthesis introduced in the bridge. It was corrected;
  native compilation checks and the complete browser execution subsequently passed.
- `git diff --cached --check`: passed. Final scope review found only the 19 intended
  source/documentation files; downloaded runtimes, databases, checkpoints, logs,
  build output, caches and bytecode remain untracked/ignored outside the change.

Non-failing warnings: FastAPI/Starlette's test-client `httpx` deprecation and Vite's
existing application chunk exceeding 500 kB after minification. Dependencies and
existing frontend implementation were not modified to address unrelated warnings.
The full production-app `npm --prefix frontend run test:e2e` suite was not run;
the relevant new browser behavior was exercised through the standalone verifier.
Browser evidence is desktop Chromium only; no physical mobile, browser neural
parity or CUDA/OpenVINO validation is claimed. Development tools were preinstalled:
host Python 3.12.14, pytest 9.1.1, Ruff 0.17.1, mypy 1.20.2, Node 24.19.0 and
Playwright 1.64.1; no dependency or lockfile changes were made.

Delivery is prepared on `feat/phase0-online-offline-foundations`. Automatic approval
review rejected the combined `git commit ... && git push -u origin HEAD` action
before execution because explicit authorization to publish to the remote was
missing. No push, pull request or CI run is claimed; publication requires that
approval. No attempt was made to bypass that rejection through another publishing
tool. Local validation and review were completed independently.

## Delivered boundaries and remaining decisions

The unintegrated immutable domain contract emits proposed result replacements;
it does not write a database, schedule expiry, add handlers or change current
gameplay/history/security behavior. It covers timeout boundaries and abandonment,
concessions, cancellation, actor checks and void revisions. Unit tests do not
establish database exactly-once behavior or race safety; the design lists required
later concurrency/crash integration tests.

The source-backed assessment distinguishes existing owner-scoped local games from
future online participants and results. The design specifies per-move time,
permission/analysis boundaries, UTC persistence, atomic finalization and
revision-aware accounting, plus Elo/ladder/achievement extension boundaries.
Side policy, immutable lobby settings/visibility, outage incident handling,
rated eligibility/Elo parameters, offline reload/replacement and registration
verification/abuse/delivery policy remain recommendations requiring owner approval.

## Ordered next work

1. Approve the [registration policy/prerequisites](../designs/registration-phase1.md#confirmed-boundary-and-approval-checklist)
   and delivery/legacy-account/rollback choices.
2. Implement shared normalization, uniqueness/race handling and public-registration
   throttling with a separately reviewed token/verification migration.
3. Implement purpose-bound digest-only single-use verification/reset tokens;
   atomic password-reset session invalidation; bounded email retry/recovery.
4. Add declared route permissions, registration/verification/recovery browser UI
   and isolated security, browser and deployment/rollback tests.
5. Complete Phase 1 acceptance and review before a separately authorized production
   rollout. Then implement multiplayer persistence/transactions and the approved
   outage policy, with the specified concurrency/crash suite, before exposing online play.
6. Independently extend the offline prototype with approved single-slot replay
   persistence, installation/update UX, licenses and real-device measurements.
