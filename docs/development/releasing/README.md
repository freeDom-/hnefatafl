# Releasing

[Development index](../README.md) · [Release readiness](readiness.md) · [Testing](../testing.md) · [Release notes](../../releases/README.md) · [Updating](../../installation/updating.md) · [Publish workflow](../../../.github/workflows/release.yml)

Release notes live only in `docs/releases/<tag>.md`. Release preparation, readiness
and publication procedure live here.

## Identifier rules

- Use the authorized version verbatim: tag, `docs/releases/<tag>.md`,
  `pyproject.toml`, `frontend/package.json` and `hnefatafl-<tag>.tar.gz` must match.
- Two-part (`X.Y`) and three-part (`X.Y.Z`) identifiers are accepted; ordering treats
  `X.Y` as `X.Y.0` without renaming a published tag. Two-part tags require an updater
  that already accepts them; release notes must state any one-time updater refresh.
- Historical `vX.Y.Z.md` filenames remain readable by the publish workflow. Do not
  rename or rewrite already-published notes/tags.
- Release notes start with `# X.Y.Z - Concise summary`; GitHub release title is
  `Hnefatafl X.Y.Z`. Use absolute links so checked-in notes remain valid when
  published.

## Preferred workflow (exact commit)

Set and record the authorized variables first:

```sh
VERSION=X.Y.Z
VERIFIED_SHA=<full 40-character SHA of the merged main commit>
```

1. Obtain explicit authorization for `$VERSION`.
2. Identify the intended merged `main` commit and verify it is the authorized
   revision. If `origin/main` advanced, do **not** substitute the new HEAD; stop and
   obtain authorization for a new candidate.
3. Verify `pyproject.toml`, `frontend/package.json` and `docs/releases/$VERSION.md`
   match `$VERSION` and start with the required H1.
4. Verify required CI for that exact SHA on `main` (`security`, `python`, `node22`,
   `browser`, `deployment-cross-version`), not merely a green PR or a newer main.
5. Verify the tag does not already exist locally or remotely:
   ```sh
   git fetch origin main --tags
   git merge-base --is-ancestor "$VERIFIED_SHA" origin/main
   test "$(git rev-parse refs/tags/$VERSION 2>/dev/null)" = "" || exit 1
   git ls-remote --exit-code --tags origin "refs/tags/$VERSION" && exit 1
   ```
6. Create an annotated tag explicitly on the verified SHA:
   ```sh
   git tag -a "$VERSION" "$VERIFIED_SHA" -m "Hnefatafl $VERSION"
   git push origin "refs/tags/$VERSION"
   ```
7. Publish through the checked-in workflow:
   ```sh
   gh workflow run release.yml --ref main -f tag="$VERSION"
   ```
   The workflow validates the annotated tag, project version, successful Verify CI
   for the tag commit on `main`, notes filename and clean source; it packages the
   tagged tree, requires the GitHub asset SHA-256 digest and publishes a **stable**
   release. It never overwrites an existing release.
8. Verify the published title, release notes, `hnefatafl-$VERSION.tar.gz` asset and
   digest, and updater compatibility. Only then announce/install.

Manual publication is an exception: run `python scripts/package_release.py` on the
clean verified commit, create the release with the notes/archive, and verify the
asset digest. Do not publish draft/prerelease artifacts as stable production inputs.

The release branch/PR remains required by the normal contributor workflow; merge,
tag and deployment each need the authorization above. A docs PR does not authorize
any of them.

## Packaging/updater requirements

- Packaging includes committed `LICENSE`, `THIRD_PARTY_NOTICES.md`, authentication,
  lockfiles, security tests, frontend and systemd template; it excludes runtime data,
  dependencies, secrets and generated frontend outputs.
- The updater requires the GitHub asset's SHA-256 digest and a compatible annotated
  stable release. It never trusts source archives from arbitrary tags.
- Deployment-related releases must pass `deployment-cross-version` with the pinned
  historical objects.
- The installed updater is a set (`deployment.py`, `updater_support/`, wrappers).
  Refresh the whole set with `--refresh-updater-only`/`--refresh-updater` when the
  updater ABI/retention behavior changes; state this in the release notes.
- Operational update/rollback/backup commands are in
  [updating](../../installation/updating.md), not duplicated here.

These steps do not authorize a tag, release, version bump or deployment by
themselves.
