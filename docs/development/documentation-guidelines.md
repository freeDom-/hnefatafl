# Documentation guidelines

Documentation is part of the feature. A change is incomplete when a canonical page is
stale, missing, contradicts the code, or contains content for the wrong audience.

## Ownership by audience

| Section | Audience and canonical content | Not here |
| --- | --- | --- |
| `docs/game/` | Players: rules/mechanics, visible modes/settings/workflows, UI page guides under `interface/`, ratings/league/tournament policies, privacy, player limitations | API endpoints/schemas, transactions, migrations, polling/caching, frontend/backend implementation |
| `docs/training/` | Training operators/researchers: workshop UI, algorithms/math, parameters, plans, evaluation/tuning workflows, models/workers, hardware/performance, troubleshooting | Exhaustive API/persistence/worker protocol (Development), raw benchmark evidence (Evaluation) |
| `docs/installation/` | Server operators: install/deploy/configure/proxy/update/backup/rollback/recovery runbooks and safety warnings | Updater/transaction/ABI implementation (Development) |
| `docs/administration/` | Administrators: accounts, permissions, security configuration, operational controls, monitoring, incidents | CSRF/session/token internals, locking/transactions, complete API schemas |
| `docs/evaluation/` | Researchers/developers: methodology, environments, model/workload identities, measured results, statistics, reproduction, limitations, raw evidence | Primary UI operating guides or implementation contracts |
| `docs/development/` | Developers/agents: architecture and subsystem references, API, auth/persistence internals, protocols, state machines, testing, releasing/readiness, UI/agent rules, decisions, active roadmaps | Player how-to or benchmark narratives |
| `docs/releases/` | **Only** version-specific release notes and their navigation index | Release process, readiness, validation guidance, historical reports |
| `docs/rules/` | Runtime ruleset sources; paths must not move | General documentation |
| `docs/assets/` | Current screenshots grouped by game/training/administration/development | Runtime assets |

Release preparation/readiness lives in `docs/development/releasing/`. General
testing/validation has one owner: [Testing](testing.md). Historical implementation
reports are not release notes; preserve them only when subject-specific security,
licensing, attribution or automated verification needs them, otherwise use an
immutable commit-pinned reference.

## Canonical sources

- **API:** generated `/openapi.json` + [API reference](api/README.md) (one endpoint
  table per group). `docs/API_PERMISSIONS.json` is the generated permission inventory.
- **UI:** one guide per page under `docs/game/interface/` (players) or the Training
  interface (operators). Feature pages explain concepts, not duplicate page controls.
- **Benchmarks:** methodology in [Evaluation methodology](../evaluation/methodology.md);
  measurements/raw evidence under `docs/evaluation/`.
- **Architecture/internals:** overview + focused subsystem references in Development.
- **Release notes:** immutable `docs/releases/<tag>.md`; release procedure/readiness
  only in Development.

Link instead of copying commands, defaults, parameter tables, rules or workflows. A
section index navigates; it does not repeat linked pages.

## Writing and links

Prefer direct prose, short paragraphs, compact tables, executable examples and
explicit prerequisites. Use present tense for current behavior; label historical
counts. Distinguish required/optional config and implemented/experimental/planned
work. Avoid repeated introductions/conclusions, PR-by-PR narratives, obsolete
acceptance criteria, generic filler and credentials/secrets.

A link is correct only when its destination covers the same topic and audience. Avoid
self-references and redirects to a different topic. Before changing a default, label,
permission, command or supported value, verify it against source/config/UI. If code
and docs disagree, correct the docs and report a genuine defect separately; do not
silently expand a docs PR into behavior changes.

## Feature/documentation workflow

1. Update the canonical page, not a new implementation report.
2. Update configuration/parameter references from the actual validator/schema.
3. Update API/architecture when routes, permissions, boundaries or storage change.
4. Update the affected UI guide and screenshots when labels/layout/controls change.
5. Record exact validation in the PR. Write release notes only for an authorized tag.
6. Label partial/experimental work; never present a roadmap item as available.

Prefer updating an existing page over creating one. Delete superseded pages after
moving durable knowledge. Do not accumulate phase-completion reports.

## Implementation roadmaps

Keep a complete roadmap while a feature is planned, partial, active or awaiting
acceptance: objective, all phases (completed/current/future), dependencies,
decisions, acceptance criteria, evidence, outstanding findings and deferrals.
Completed phases stay documented. A merged phase, green PR, passing tests or an
unavailable hardware gate does not mean the feature is complete.

Remove a roadmap only after the whole feature is fully developed and accepted, after
moving durable decisions/results into canonical docs and preserving reproducibility.
Never shorten the complete browser-analysis roadmap while its gates remain open.

## Benchmarks and UI

- Update the existing topic report; preserve comparable history and label superseded/
  non-comparable results. Include revision, hardware/software, model, ruleset, budget,
  sample and raw data. Do not invent numbers/significance.
- Replace superseded screenshots; use descriptive lower-case filenames, legible
  WebP/JPEG, no upscaling and no real accounts/emails/tokens/production data.
- UI fixtures are examples, not performance/model-strength evidence.

## Screenshots

```sh
cd frontend
npx playwright install chromium
npx playwright test tests/arena-final.spec.ts --project=chromium
npm run test:docs-screenshots
# targeted:
npx playwright test --config playwright.docs.config.ts \
  tests/documentation-screenshots.docs.ts -g "game documentation"
```

`documentation-screenshots.docs.ts` and the `DOCS_SCREENSHOTS=1` tuning run are
opt-in; ordinary `test:e2e` writes no docs images. Output stays in ignored
`frontend/test-results/documentation/` until reviewed/copied to `docs/assets/`.

## Validation

Run `make docs` (link/anchor/image/reachability checks + checker regressions; it also
rejects malformed local links and API tables under `docs/game/`) and
`git diff --check`. It does not validate external URLs, shell semantics or prose.
Every committed Markdown file must be reachable from `docs/README.md`; otherwise
delete it or link it from the correct section index.
