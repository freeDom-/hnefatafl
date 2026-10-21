# Working in this repository

Each cloud task already has an isolated workspace. Use the existing checkout;
do not create Git worktrees unless the user explicitly requests one.

Read `ARCHITECTURE.md` and the relevant rules document before changing behavior.
The user's initial implementation requirements are preserved in
`docs/REQUIREMENTS.md`. `PROGRESS.md` and GitHub release notes distinguish verified
features from source, hardware and deployment limitations.

Use Python 3.11+ and Node 22+. `make install` installs locked Python dependencies
and frontend packages. Run `make check` for formatting/lint, type checks, backend
tests, frontend tests and the production build. Browser validation additionally uses
`npm --prefix frontend run test:e2e` (install Playwright Chromium first).

Keep the pure engine independent of HTTP, database and PyTorch. Test variant-specific
rules with explicit positions; do not assume Tablut variants are interchangeable.
Search and training budgets must report unfinished games separately from rule draws.
Training smoke tests must perform actual updates, checkpoint loading and legal play;
do not claim a stronger model from a successful training run alone.

Use `HNEFATAFL_DATA_DIR` for isolated test/server storage. Do not commit databases,
model weights, credentials or local virtual environments. Load models with the
validated tensor-only checkpoint loader. Preserve optimistic version guards on moves.

When publishing an authorized milestone release, follow `docs/RELEASING.md` and
write notes around delivered behavior, exact validation and remaining limitations.
