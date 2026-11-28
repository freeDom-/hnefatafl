# Releasing

[Development index](../README.md) · [Release readiness](readiness.md) · [Contributor workflow](../contributor-workflow.md) · [Testing](../testing.md) · [Release notes](../../releases/README.md) · [Updating](../../installation/updating.md) · [Publish workflow](../../../.github/workflows/release.yml)

Release notes live only in `docs/releases/<tag>.md`. Release preparation, exact-commit
tagging and publication procedure live here.

## Identifier rules

- The authorized version is used verbatim: tag, `docs/releases/<tag>.md`,
  `pyproject.toml`, `frontend/package.json` and `hnefatafl-<tag>.tar.gz` must match.
- Two-part (`X.Y`) and three-part (`X.Y.Z`) identifiers are accepted; `X.Y` orders
  like `X.Y.0` without renaming a published tag. A two-part tag requires an installed
  updater that already accepts two-part tags; release notes must state that one-time
  updater refresh.
- Historical `vX.Y.Z.md` filenames remain readable by the publish workflow. Do not
  rename or rewrite published notes/tags.
- Notes start with `# X.Y.Z - Concise summary`; the GitHub release title is
  `Hnefatafl X.Y.Z`. Use absolute links so checked-in notes remain valid when published.

## Complete workflow

Use the normal [contributor workflow](../contributor-workflow.md) for branch/PR and
[testing](../testing.md) for checks. A release needs explicit authorization; this
document does not create a tag/release/deployment.

1. Obtain explicit authorization for the exact version.
2. Identify changes since the previous release; verify merge/review/validation status.
3. Update `main`, create `release/<version>`, and update required metadata consistently.
4. Write `docs/releases/<version>.md` with delivered behavior, exact validation,
   compatibility/migration, updater requirements and limitations.
5. Commit focused release files, push the branch, open a PR against `main` and verify
   required PR CI, including `deployment-cross-version` when deployment changes.
6. Merge only with explicit authorization, then fetch `main`.
7. Identify the exact merged release commit and verify it is the intended revision.
   If `origin/main` advanced, stop; do **not** substitute the new HEAD without new
   authorization.
8. Verify version metadata, notes and packaging inputs in **that commit**, not an
   unrelated working tree:
   ```sh
   git show "$VERIFIED_SHA:pyproject.toml"
   git show "$VERIFIED_SHA:frontend/package.json"
   git show "$VERIFIED_SHA:docs/releases/$VERSION.md"
   ```
9. Verify required CI on `main` for that exact SHA (`security`, `python`, `node22`,
   `browser`, `deployment-cross-version`). A green PR or newer main HEAD is not enough.
10. Run the exact-commit preflight and tag commands below.
11. Publish through the checked-in workflow.
12. Verify the published stable release, notes, archive name and SHA-256 digest, and
    updater compatibility before announcing/installing.

### Preflight and exact-commit tagging

Replace the quoted placeholders, then run the block from a clean checkout. It is a
complete executable example once `VERIFIED_SHA` is a real full commit ID.

```sh
set -euo pipefail
VERSION='X.Y.Z'                 # authorized release identifier
VERIFIED_SHA='REPLACE_WITH_40_CHARACTER_SHA'

[[ "$VERSION" =~ ^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(\.(0|[1-9][0-9]*))?$ ]]
[[ "$VERIFIED_SHA" =~ ^[0-9a-f]{40}$ ]]
git fetch origin main
git merge-base --is-ancestor "$VERIFIED_SHA" origin/main

# Local tag: 0 exists, 1 absent, any other status is an unexpected failure.
local_status=0
git show-ref --verify --quiet "refs/tags/$VERSION" || local_status=$?
case "$local_status" in
  0) echo "Tag already exists locally: $VERSION" >&2; exit 1 ;;
  1) : ;;
  *) echo "Local tag lookup failed (status $local_status)" >&2; exit 1 ;;
esac

# Remote tag: 0 exists, 2 absent, anything else is a failed lookup.
set +e
git ls-remote --exit-code --tags origin "refs/tags/$VERSION" >/dev/null 2>&1
remote_status=$?
set -e
case "$remote_status" in
  0) echo "Tag already exists on origin: $VERSION" >&2; exit 1 ;;
  2) : ;;
  *) echo "Remote tag lookup failed (status $remote_status)" >&2; exit 1 ;;
esac

git tag -a "$VERSION" "$VERIFIED_SHA" -m "Hnefatafl $VERSION"
git push origin "refs/tags/$VERSION"
```

Any failed fetch, ancestry, metadata, CI, local/remote lookup or tag creation aborts;
never force-push or overwrite a tag. After the exact tag is pushed:

```sh
gh workflow run release.yml --ref main -f tag="$VERSION"
```

The workflow requires an annotated tag, exact project version, successful Verify CI
for the tag commit on `main`, and the checked-in notes path. It packages the tagged
clean source, publishes a **stable** release (never draft/prerelease), and verifies
GitHub's asset SHA-256 digest. Check the workflow result and release assets.

## Publisher and updater requirements

| Requirement | Publisher/workflow | Installed updater |
| --- | --- | --- |
| Annotated Git tag | Required; verified by workflow | **Not checked** — the updater consumes a published GitHub release |
| Successful CI on `main` for tag commit | Required; workflow verifies | Not checked |
| Project/tag version match | Required | Requires matching installed-release metadata/version |
| Clean committed source | `package_release.py` refuses dirty/missing license files | Consumes `hnefatafl-<tag>.tar.gz` |
| Stable publication | Workflow publishes stable | Ignores draft/prerelease releases |
| Asset name and SHA-256 digest | Workflow verifies published asset | Verifies GitHub asset digest before extraction |
| Version compatibility | Version rules above | Two-part tags require a compatible updater; ABI/protocol advances need refresh |

The updater selects only published stable releases with a matching archive asset and a
GitHub SHA-256 digest; it does not inspect Git tag annotation or CI. Deployment-related
releases must pass `deployment-cross-version` with pinned historical objects. The
installed updater set (`deployment.py`, `updater_support/`, wrappers) may need
`--refresh-updater-only`/`--refresh-updater` when ABI/retention behavior changes;
state that in the notes. Operational update/rollback commands are in
[updating](../../installation/updating.md).

Manual publication is an exception: package from the clean verified commit, create
the stable release with notes/archive, and verify the asset digest. Never publish a
draft/prerelease artifact as a production input or overwrite an existing release.
