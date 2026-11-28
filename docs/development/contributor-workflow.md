# Contributor workflow

[Development index](README.md) · [Repository instructions](../../AGENTS.md) · [Testing](testing.md) · [Releasing](releasing/README.md)

## Branch and pull request

Never commit to `main` directly. Update `main`, branch with a type prefix (`feat/`,
`fix/`, `docs/`, `test/`, `refactor/`, `perf/`, `chore/`), run the relevant checks,
then push and open the PR against `main`:

```sh
git switch main && git pull
git switch -c fix/short-description
# edit and run the relevant checks
git commit -m 'fix: concrete change'
git push -u origin HEAD
gh pr create --base main --fill
gh pr checks
```

Keep one concern per branch; do not force-push a shared branch. CI runs on every PR,
so a red check blocks review. Leave merging to the owner unless explicitly asked;
after a merge, update `main` before branching again.

## Pull request description

State delivered behavior, exact validation commands/results and limitations. Never
claim more than the checks support. Include licensing/notice updates for new
dependencies/assets and unresolved provenance questions.

## Review and merge authorization

- A green PR proves that revision's CI scope, not combined production acceptance.
- Merge, tag and deploy are separate owner decisions. Do not tag or deploy from a
  documentation/PR change without explicit authorization.
- Milestone releases follow [Releasing](releasing/README.md); release notes live only
  under `docs/releases/`.

## Tests and CI

Run focused tests first; `make check` runs the full local suite. Browser,
deployment, cross-version and opt-in screenshot commands are in
[Testing](testing.md). Respect shared-host limits: serial heavyweight tests under
`/run/lock/hnefatafl-tests.lock`, bounded workers/threads, no detached services.
