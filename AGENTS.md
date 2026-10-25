# Working in this repository

Each cloud task already has an isolated workspace. Use the existing checkout;
do not create Git worktrees unless the user explicitly requests one.

## Branch and pull request workflow

Online milestone phases use `feat/online-multiplayer` as their integration base,
not `main`. Confirm the preceding phase is reviewed/merged there, then branch
from its current remote head and target the phase PR back to that branch. See
[the milestone workflow](docs/DEVELOPMENT.md#online-milestone-workflow).
The default workflow below applies to other changes.

Deliver every change through a branch and a pull request. Do not commit directly
to `main`, and never force-push or rewrite a branch that is already shared.

1. Update the local `main` and branch off it, choosing a type prefix:
   `feat/…` for features, `fix/…` for bug fixes, and `docs/…`, `test/…`,
   `refactor/…`, `perf/…` or `chore/…` for other work.
2. Implement and verify the change on that branch (see `docs/DEVELOPMENT.md`).
3. Commit focused changes with a conventional prefix, push the branch and open a
   pull request against `main`:

```sh
gh pr create --base main --head "$(git branch --show-current)" \
  --title 'fix: concrete change' --body-file /tmp/pr-body.md
```

4. Verify CI on the pull request (`gh pr checks`) and fix failures on the branch.
5. Leave merging to the user unless they explicitly ask for it. After a merge,
   update `main` before starting the next change.

A pull request description states delivered behavior, exact validation and
remaining limitations, and never claims more than the checks support. Keep it
readable without the conversation: the review happens on the pull request, not in
chat. Milestone releases follow the same path and are tagged only after their pull
request is merged; see `docs/RELEASING.md`.

Read `ARCHITECTURE.md` and the relevant rules document before changing behavior.
Use `docs/README.md` to navigate current guides. `make docs` checks local Markdown
links/anchors/images without extra dependencies; run it for documentation changes.
Historical releases retain their original evidence and must not be recast as new
validation. Rules Markdown paths are runtime assets and must not be moved.
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
