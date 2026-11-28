# Documentation guidelines

Documentation is part of the feature, not an afterthought. A change is incomplete
when the affected current guide is stale, missing or contradicts the code.

## Canonical locations

| Topic | Canonical location |
| --- | --- |
| Entry point and quick start | root `README.md`, `docs/getting-started/` |
| Installation, configuration, operations | `docs/installation/` |
| Accounts, security, online operations, monitoring | `docs/administration/` |
| Rules, modes, online play, ratings, leagues, tournaments, progression, UI | `docs/game/` |
| Training, algorithms, parameters, models, workers, performance | `docs/training/` |
| Agents, tournaments, methodology, benchmarks, research evidence | `docs/evaluation/` |
| Architecture, setup, workflow, tests, API, UI rules, decisions, roadmaps | `docs/development/` |
| Release process, validation guidance, immutable release notes | `docs/releases/` |
| Ruleset source assets | `docs/rules/` (**runtime assets; do not move**) |
| Images | `docs/assets/<game\|training\|administration\|development>/` |

One topic has one canonical page. Link to it; do not copy commands, defaults,
parameter tables, game rules or architecture descriptions into a second page.

## When implementing a feature

1. Update the canonical current guide, not a new implementation report.
2. Add or update configuration and parameter references if behavior is configurable.
   Derive defaults/bounds from the schema/code and say which source is authoritative.
3. Update architecture or API documentation when boundaries, routes, permissions or
   storage change.
4. Update screenshots and the [interface guide](../game/interface/README.md) when a
   documented page, label, control or layout changes.
5. Add tests and record exact validation in the pull request. Do not put a new
   release note in an unreleased PR; release notes are written for an authorized tag.
6. If a feature is partially implemented, label what is delivered and what is not.
   Never present a roadmap item as available.

## Implementation roadmaps

Keep one complete implementation roadmap for each feature that is planned,
partially implemented, under active development or awaiting final acceptance. It
must retain the objective, all phases (completed, current and future),
dependencies, important decisions, acceptance criteria, validation evidence,
outstanding findings and deferred work. Completed phases stay documented; mark
them completed rather than deleting them.

A merged phase, green PR, passing unit tests or a hardware-dependent gate that
cannot be run does **not** mean the feature is fully developed. Keep the roadmap
while any implementation or acceptance requirement remains.

Remove an implementation roadmap only when the **whole feature** is fully
developed and accepted. Before removal:

1. Verify that no relevant implementation steps or findings remain unresolved.
2. Move durable design constraints and evaluation results into the canonical guide,
   architecture decision record or benchmark report.
3. Preserve benchmark results and reproducibility instructions.
4. Update player, administrator and developer documentation.
5. Remove the obsolete roadmap. Git history and release notes preserve the rest.

Do not accumulate per-phase completion reports or replace obsolete documents with
one equally redundant historical document.

## When benchmarking

- Update the existing report for the relevant technical topic.
- Preserve comparable historical measurements and label superseded or
  non-comparable results.
- Include revision, hardware, software, model, ruleset, budget, sample and raw data.
- Do not create a new report for every minor run. Use a compact table and link raw
  evidence.
- Do not invent missing numbers, significance or a strength/GPU claim.

## When changing the UI

- Replace superseded screenshots; do not keep both unless the old one documents a
  historical validation that is still referenced.
- Use descriptive, lower-case filenames grouped by surface, for example
  `assets/game/themes/kings-table-dashboard.jpg`.
- Prefer WebP/JPEG at a legible size; do not upscale. Remove 3840px duplicates
  unless a documented 4K comparison needs one.
- Do not commit screenshots containing real accounts, emails, tokens or production
  data. Use disposable fixtures and label demonstration data.
- Update all references when renaming or moving an image.

## Screenshots

The repository's Playwright acceptance specs can capture current UI screenshots
into `frontend/test-results/<suite>/`. Copy only the images a guide uses into
`docs/assets/`, with a stable filename. Regeneration is an explicit maintenance
step, not part of every CI run.

Recommended workflow:

```sh
cd frontend
npx playwright install chromium
npx playwright test tests/arena-final.spec.ts --project=chromium
npm run test:docs-screenshots
# or target one capture group with the same configuration:
npx playwright test --config playwright.docs.config.ts \
  tests/documentation-screenshots.docs.ts -g "game documentation"
```

`documentation-screenshots.docs.ts` captures Arena pages and Workshop surfaces
with disposable route fixtures. The same opt-in run executes the real disposable
CPU tuning test with `DOCS_SCREENSHOTS=1`, adding Tuning results and Resume
Training captures. Ordinary `npm run test:e2e` does **not** execute the docs spec
and does not write documentation-only images. Images are written to
`frontend/test-results/documentation/`; copy only the files used by a guide into
`docs/assets/`. The tuning and training images are workflow examples, never
performance or model-strength evidence.

The specs start disposable backend/frontend services with fixture data. Review the
output for transient notifications, loading indicators, synthetic personal data
and theme consistency before committing. If a page has no reliable capture path,
document the limitation instead of fabricating a mockup.

## Writing style

- Prefer short paragraphs, tables and executable examples.
- State prerequisites and whether a command runs from the repository root.
- Use present tense for current behavior; label historical counts and evidence.
- Distinguish required from optional configuration.
- Do not paste code that will drift; link to the source or show a short example.
- Remove AI-generated filler, repeated summaries, obsolete acceptance criteria and
  speculative descriptions.
- Do not use arbitrary word limits; optimize information density.
- Never include credentials, secrets, private tokens or production data.

## Validation

Run:

```sh
make docs
```

The dependency-free checker validates local links, heading anchors, images and
reachability from `docs/README.md`. It does not check external URLs, website
availability or shell-command semantics. For documentation changes, also run
`git diff --check` and inspect the rendered diff.

If a document is intentionally not linked from the index, add it to the graph only
when it is a real entry point; otherwise delete it. Historical release notes,
validation evidence and original requirements must remain reachable through their
section index.

## Maintenance rules

- Prefer updating an existing page over creating a new one.
- Delete a superseded page after moving its durable knowledge.
- Keep agent instructions concise and scoped; put detailed reference material here,
  not in `AGENTS.md`.
- Keep `docs/README.md` a task-oriented index, not a duplicate of every page.
- Update the index when adding, moving or removing a section.
- Do not recast an old release's recorded validation as current evidence.
- Do not change historical release notes except to fix a broken repository-local
  link or an objectively wrong path.
