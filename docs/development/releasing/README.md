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

## Preferred workflow

1. Obtain explicit authorization for the exact version.
2. Identify changes since the previous release; verify they are merged and validated.
3. Update version metadata and write `docs/releases/<tag>.md` with delivered
   behavior, exact validation, compatibility/migration, updater requirements and
   limitations. Do not claim stronger models, GPU performance or production
   acceptance beyond the evidence.
4. Create a release branch and PR against `main`; do not push milestone commits
   directly to `main`.
5. Verify required PR checks, including browser, deployment and
   `deployment-cross-version`; fix failures on the branch.
6. Merge only with explicit authorization, then update local `main`.
7. Verify successful Verify CI on the resulting merged `main` commit.
8. Create and push an annotated tag on that exact commit:
   ```sh
   git switch main && git pull
   git tag -a X.Y.Z -m 'Concrete milestone description'
   git push origin X.Y.Z
   ```
9. Publish the stable release through the checked-in workflow:
   ```sh
   gh workflow run release.yml --ref main -f tag=X.Y.Z
   ```
   The workflow verifies the annotated tag, project version, success on `main`,
   packaged source and notes path. It refuses dirty/incomplete source, publishes the
   archive and verifies GitHub's asset digest. Never overwrite an existing release.
10. Verify the published title, notes, asset `hnefatafl-<tag>.tar.gz`, SHA-256 digest
    and updater compatibility; only then announce/install.

A manual publication is an exception: run `python scripts/package_release.py` on a
clean committed tree, create the release with `gh release create` using the archive
and notes, and verify the asset digest. Do not publish draft/prerelease artifacts as
stable production inputs.

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
