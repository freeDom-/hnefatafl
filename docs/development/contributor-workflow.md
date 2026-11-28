# Contributor workflow

[Development index](README.md) · [Repository instructions](../../AGENTS.md) · [Testing](testing.md)

## Branch and pull request workflow

Never commit to `main` directly. Update `main`, branch off it with a type prefix
(`feat/…`, `fix/…`, `docs/…`, `test/…`, `refactor/…`, `perf/…`, `chore/…`), make the
change, run the checks below, then push the branch and open a pull request:

```sh
git switch main && git pull
git switch -c fix/short-description
# … edit, then run the relevant checks …
git commit -m 'fix: concrete change'
git push -u origin HEAD
gh pr create --base main --fill   # or --title/--body-file for a written description
gh pr checks                       # wait for green before requesting review
```

CI runs on every pull request, so a red check blocks the change. Leave merging to
the user unless they explicitly ask for it; after a merge, update `main` before
branching again. Keep one concern per branch and do not force-push a branch that is
already shared. Milestone releases take the same path and are tagged only after
their pull request is merged ([Releasing](../releases/release-process.md)).

## Online milestone workflow

The online milestone integrates into `feat/online-multiplayer`; phase PRs must
**not target main**. Phase 0 PR #6 was retargeted and merged there at
`004d21855858f73705261e2db9e5fd6d5f8cf2b7` before Phase 1 began. Phase 1 PR #9
was merged at `44d0ae7159bd8ec5653134adbc02d9b137025c92` before Phase 2 branched.
Do not duplicate phase changes, cherry-pick silently or merge an unreviewed phase.

```sh
git fetch origin '+refs/heads/*:refs/remotes/origin/*'
git switch -c feat/phase2-online-core origin/feat/online-multiplayer
# implement and validate on the phase branch
git push -u origin HEAD
gh pr create --base feat/online-multiplayer --head feat/phase2-online-core \
  --title 'feat: add durable authoritative online games' --body-file work/phase2/pr-body.md
gh pr checks
```

If the integration branch is absent, create it from current `origin/main` without
changing main, and publish only with authorized repository access. Check actual
PR state and Phase 0 ancestry before implementation. Leave merging to the owner.
There are no releases, version bumps, force pushes or production deployments in
this workflow. Verify CI runs for all pull-request bases and for integration-branch
pushes; the release workflow remains explicitly restricted to main/manual tags.

The isolated account browser suite uses a guarded development-only fake mailbox:

```sh
npm --prefix frontend run test:registration
# Or select an installed browser without changing production settings:
PLAYWRIGHT_CHROMIUM_EXECUTABLE=/usr/bin/chromium npm --prefix frontend run test:registration
```

It starts disposable backend/Vite/mailbox services on loopback ports 8003/4175/8004,
uses an in-memory fake SMTP transport, and exercises English/German desktop/mobile
flows. No real email is sent. Trace recording is off to avoid retaining test token
payloads; normal Playwright failure diagnostics may still contain disposable test
form data. The harness's registration-IP budget is 20 to accommodate six browser
cases sharing one peer; the production default remains five. See
[registration operations](../administration/user-management.md) for schema/configuration boundaries.

Backend online integration uses independent disposable SQLite connections/processes,
barriers and controlled UTC clocks. Run
`.venv/bin/python -m pytest tests/test_online_core.py tests/test_online_api.py tests/test_online_matches.py tests/test_online_migrations.py tests/test_lifecycle.py tests/test_security.py tests/test_account_migrations.py tests/test_deployment.py tests/test_deployment_storage.py -q`
before `make check`. Production deployment changes additionally run
`make deployment-check`, which starts a real lifespan under a real exclusive
deployment flock and covers disposable update/rollback/recovery integration.
Genuine historical-release coverage needs a built frontend and pinned Git objects:

```sh
npm --prefix frontend ci && npm --prefix frontend run build
make historical-refs
make deployment-cross-version
```

`historical-refs` verifies the commit IDs for `0.40.0`, `0.41.0`, `0.41.1`, `0.42`
and the schema-20 fixture. The CI suite extracts the pinned releases into
disposable directories. It covers authentic upgrades from schemas 15, 20 and 21,
cross-schema recovery refusals, process cleanup, authenticated mutations and
relocation of backup histories created by the released `0.41.1` and `0.42` updaters.
The deployment tests also use process termination at actual filesystem and
journal transitions, two filesystems for migration, and real application lifespans
with an isolated service controller. These checks complement systemd and Proxmox
validation on a target host.
