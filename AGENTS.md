# Working in this repository

Each cloud task already has an isolated workspace. Use the existing checkout; do
not create Git worktrees unless the user explicitly requests one.

## Branch and pull request workflow

Deliver every change through a branch and a pull request. Do not commit directly
to `main`, and never force-push or rewrite a shared branch. Update `main`, branch
with a type prefix (`feat/…`, `fix/…`, `docs/…`, `test/…`, `refactor/…`, `perf/…`,
`chore/…`), run the relevant checks, then push and open a PR against `main`:

```sh
git switch main && git pull
git switch -c docs/short-description
gh pr create --base main --head "$(git branch --show-current)" \
  --title 'docs: concrete change' --body-file /tmp/pr-body.md
gh pr checks
```

A PR description states delivered behavior, exact validation and remaining
limitations; never claim more than the checks support. Online milestone phases
target `feat/online-multiplayer`, not `main`; see
[the milestone workflow](docs/development/contributor-workflow.md#online-milestone-workflow).
Leave merging to the user unless explicitly asked. After a merge, update `main`
before starting the next change.

## Read before changing behavior

1. [Documentation index](docs/README.md) for current guides.
2. [Architecture](docs/development/architecture.md) and the relevant rules document
   under `docs/rules/`.
3. [UI guidelines](docs/development/ui-guidelines.md) for frontend changes.
4. [Documentation guidelines](docs/development/documentation-guidelines.md) when
   adding, moving or removing docs or screenshots.

Rules Markdown paths are runtime assets and must not be moved. Historical releases
retain their original evidence and must not be recast as new validation. The
original user requirements are preserved in `docs/REQUIREMENTS.md`.

## Environment and checks

Use Python 3.11+ and Node.js 22.12+ (or 24+). `make install` installs locked
dependencies; `make check` runs docs, lint, type checks, backend/frontend tests and
the production build. Browser validation uses
`npm --prefix frontend run test:e2e` after installing Playwright Chromium.
Documentation-only changes require `make docs` and relevant lightweight checks,
not a full training or browser rerun.

Respect the shared-host resource rules supplied with the task: run heavyweight
tests serially under `/run/lock/hnefatafl-tests.lock`, cap workers/threads, and
do not leave detached test servers or browsers running.

## Engineering constraints

- Keep the pure engine independent of HTTP, database and PyTorch.
- Test variant-specific rules with explicit positions; Tablut variants are not
  interchangeable.
- Report unfinished games separately from rule draws in search and training.
- Training smoke tests must perform actual updates, checkpoint loading and legal
  play. Do not claim stronger play from a successful run alone.
- Use `HNEFATAFL_DATA_DIR` for isolated test/server storage. Never commit
  databases, checkpoints, model weights, credentials or virtual environments.
- Load models with the validated tensor-only checkpoint loader.
- Preserve optimistic version guards on moves and authorization on every route.
- Reuse existing UI components, styles and themes; support all four themes,
  mobile/desktop and interface scaling. Do not redesign an unrelated surface.

## Documentation maintenance

Update the canonical guide for the feature, not a standalone implementation report.
When a plan completes, preserve its durable decisions/measurements in the guide and
remove the plan; leave only actionable work in
[roadmaps](docs/development/roadmap/README.md). When UI changes, update the
[interface guide](docs/game/interface/README.md) and screenshots. Prefer updating
an existing page over adding another one; link instead of duplicating commands,
defaults or rules.

When publishing an authorized milestone release, follow
[docs/releases/release-process.md](docs/releases/release-process.md). A merged PR
is not a release; tags and GitHub Releases require explicit authorization.
