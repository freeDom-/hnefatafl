# Working in this repository

Use the existing isolated workspace. Do not create Git worktrees unless the user
explicitly requests one.

## Branch and pull request workflow

Deliver every change through a branch and a PR. Never commit to `main`, force-push
or rewrite a shared branch. Update `main`, branch with a type prefix (`feat/`,
`fix/`, `docs/`, `test/`, `refactor/`, `perf/`, `chore/`), run the relevant checks,
then push and open the PR against `main`. Online milestone phases target
`feat/online-multiplayer`; see the
[contributor workflow](docs/development/contributor-workflow.md). A PR description
states delivered behavior, exact validation and limitations. Leave merging to the
user unless explicitly asked; update `main` after a merge.

## Read before changing behavior

Check the [documentation index](docs/README.md), [architecture](docs/development/architecture.md),
the relevant runtime rules under `docs/rules/`, [UI guidelines](docs/development/ui-guidelines.md)
for frontend changes and [documentation guidelines](docs/development/documentation-guidelines.md)
when adding, moving or removing docs. Rules Markdown paths are runtime assets and
must not move. Historical releases retain their original evidence; do not recast old
counts as new validation. `docs/REQUIREMENTS.md` preserves the original scope.

## Checks and resources

Use Python 3.11+ and Node.js 22.12+ (or 24+). `make install` installs locked
dependencies; `make check` runs docs, lint, types, backend/frontend tests and the
production build. Run `make docs` for documentation changes. Browser validation
uses `npm --prefix frontend run test:e2e` after installing Playwright Chromium.
Run heavyweight tests serially under `/run/lock/hnefatafl-tests.lock`, cap
workers/threads and stop detached servers, browsers and child processes. Do not
weaken or skip failing tests; diagnose first.

## Engineering constraints

- Keep the pure engine independent of HTTP, database and PyTorch.
- Test variant-specific rules with explicit positions; Tablut variants are not
  interchangeable.
- Report unfinished games separately from rule draws in search and training.
- Training smoke tests must perform real updates, checkpoint loading and legal
  play. Do not claim stronger play from a successful run.
- Use `HNEFATAFL_DATA_DIR` for isolated storage. Never commit databases,
  checkpoints, model weights, credentials or virtual environments.
- Load models with the validated tensor-only checkpoint loader.
- Preserve optimistic version guards and backend authorization on every route.
- Reuse existing UI components, styles, themes and scale behavior. Support all
  four themes and mobile/desktop. Do not redesign an unrelated surface.

## Documentation maintenance

Update the canonical guide, not a standalone implementation report. Keep a complete
roadmap while its feature is still under development, including completed phases;
remove it only after the whole feature is fully developed and accepted, after
preserving durable decisions and measurements. See
[roadmap lifecycle](docs/development/roadmap/README.md). When UI changes, update the
[interface guide](docs/game/interface/README.md) and screenshots. Prefer updating an
existing page and link instead of duplicating commands, defaults or rules.

Releases follow [the release process](docs/releases/release-process.md). A merged PR
is not a release; tags and GitHub Releases require explicit authorization.
