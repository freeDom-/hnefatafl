# Milestone releases

Milestone `0.x.0` Git tags are previews with an explicit scope. Their notes use
`docs/releases/X.Y.Z.md` paths. `1.0.0` requires
the initial acceptance checklist, working production startup and complete CPU
training smoke tests. A GPU check is reported as unverified if hardware is absent.
Release-note filenames are the unprefixed `X.Y.Z` tag in every case. The historical
`vX.Y.Z.md` names were renamed to `X.Y.Z.md` for consistency; their contents are
unchanged, and the already-published releases keep their original notes because
those bodies point into the tagged trees. The publish workflow therefore still
falls back to `vX.Y.Z.md` for tags whose trees predate the rename.

Every release note opens with one H1 of the form `# X.Y.Z — Concise summary`, for
example `# 0.22.1 — Remote worker management fixes`. The summary names the release's
main delivered change, so the notes and the published release stay identifiable
without repeating the product name. The GitHub release title stays the bare
`X.Y.Z`; do not duplicate the H1 summary there.

Patch releases correct defects; minor releases add compatible features; breaking
changes after 1.0 require a new major version.

For each reached milestone:

1. Complete its implementation and tests. Update `PROGRESS.md` with real results.
2. Run the relevant checks. Before the initial complete release, run `make check`
   and browser end-to-end tests against the actual server. Verify test counts.
3. Write `docs/releases/X.Y.Z.md` with the milestone outcome, added features,
   changes/fixes, exact validation, installation/update instructions and limitations.
   Describe actual behavior and distinguish functional training from model quality.
4. Commit only the milestone's files. Push the commit to `main` and verify CI.
5. Create and push an annotated tag referencing that tested commit.
6. Publish the GitHub release using the checked-in notes file:

```sh
git tag -a X.Y.Z -m 'Concrete milestone description'
git push origin main X.Y.Z
gh release create X.Y.Z --verify-tag --title 'X.Y.Z — Concrete milestone' \
  --notes-file docs/releases/X.Y.Z.md --prerelease
```

Omit `--prerelease` for stable versions. Do not move published tags. Include data
migration requirements whenever storage changes. GitHub automatically includes
source archives. Large trained models are separate artifacts; do not commit them
or claim strength without a documented evaluation budget and results.

## Production updater assets

Production installs accept published stable **X.Y.Z** releases, with neither draft
nor prerelease flags. The tag must match `pyproject.toml`. Older preview tags and
GitHub automatic source archives are not production installation inputs.
After committing, validating and tagging a secure deployment release:

```sh
.venv/bin/python scripts/package_release.py
gh release create X.Y.Z work/releases/hnefatafl-X.Y.Z.tar.gz \
  --verify-tag --title 'X.Y.Z' --notes-file docs/releases/X.Y.Z.md
```

Replace X.Y.Z consistently. Packaging refuses dirty checkouts and includes only
clean committed source, excluding runtime data, dependencies and secrets. GitHub's
asset API must report its SHA-256 digest; the updater verifies it before extraction.
The release must include authentication, lockfiles, security tests, frontend and
systemd template. A tag alone is insufficient. Neither installer nor updater
publishes a release.

Alternatively, after pushing the annotated tag, publish through the manually
triggered [release workflow](../.github/workflows/release.yml):

```sh
gh workflow run release.yml --ref main -f tag=X.Y.Z
```

The workflow requires a matching project version and successful `Verify` CI on
`main` for the tagged commit, packages that exact clean source, publishes a stable
release and verifies GitHub's asset digest. It uses runner credentials, including
when local credentials cannot upload to `uploads.github.com`. Existing releases
and assets are not overwritten. Check the workflow result before installation.
