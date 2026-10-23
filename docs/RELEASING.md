# Milestone releases

Milestone `0.x.0` Git tags are previews with an explicit scope. Their notes use
`docs/releases/vX.Y.Z.md` paths. `1.0.0` requires
the initial acceptance checklist, working production startup and complete CPU
training smoke tests. A GPU check is reported as unverified if hardware is absent.
Patch releases correct defects; minor releases add compatible features; breaking
changes after 1.0 require a new major version.

For each reached milestone:

1. Complete its implementation and tests. Update `PROGRESS.md` with real results.
2. Run the relevant checks. Before the initial complete release, run `make check`
   and browser end-to-end tests against the actual server. Verify test counts.
3. Write `docs/releases/vX.Y.Z.md` with the milestone outcome, added features,
   changes/fixes, exact validation, installation/update instructions and limitations.
   Describe actual behavior and distinguish functional training from model quality.
4. Commit only the milestone's files. Push the commit to `main` and verify CI.
5. Create and push an annotated tag referencing that tested commit.
6. Publish the GitHub release using the checked-in notes file:

```sh
git tag -a X.Y.Z -m 'Concrete milestone description'
git push origin main X.Y.Z
gh release create X.Y.Z --verify-tag --title 'X.Y.Z — Concrete milestone' \
  --notes-file docs/releases/vX.Y.Z.md --prerelease
```

Omit `--prerelease` for stable versions. Do not move published tags. Include data
migration requirements whenever storage changes. GitHub automatically includes
source archives. Large trained models are separate artifacts; do not commit them
or claim strength without a documented evaluation budget and results.
