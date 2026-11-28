# Development

| Task | Page |
| --- | --- |
| Install dependencies, run dev services, use the CLI | [Setup](setup.md) |
| Understand modules, data flow and boundaries | [Architecture](architecture.md) |
| Arena/Workshop shell, themes, tokens and notifications | [Frontend and design system](frontend.md) |
| Mandatory UI reuse, responsiveness, accessibility and terminology | [UI guidelines](ui-guidelines.md) |
| Branch, pull-request and online-milestone workflow | [Contributor workflow](contributor-workflow.md) |
| Focused tests, full checks and browser suites | [Testing](testing.md) |
| Routes, permissions, optimistic versions and extension boundaries | [API](api.md) |
| Documentation lifecycle, screenshots and validation | [Documentation guidelines](documentation-guidelines.md) |
| Important architecture decisions | [Decision records](decisions/README.md) |
| Remaining work | [Roadmaps](roadmap/README.md) |
| Current dependency risk acceptance | [Security advisory](security-advisory.md) |
| Deferred CLA proposal | [Contributor licensing](contributor-licensing.md) |
| League and player-tournament internal authority | [League authority](league-authority.md), [tournament authority](tournament-authority.md) |
| Historical Arena implementation snapshot | [Arena completeness 2026-11-20](../releases/arena-snapshot-2026-11-20.md) |

## Repository principles

- Keep the pure engine independent of HTTP, database and PyTorch.
- Test variant-specific rules with explicit positions; do not assume Tablut
  variants are interchangeable.
- Search and training budgets must report unfinished games separately from rule
  draws.
- Training smoke tests must perform real updates, checkpoint loading and legal play.
  Do not claim stronger play from a successful run.
- Use `HNEFATAFL_DATA_DIR` for isolated test/server storage. Never commit
  databases, checkpoints, model weights, credentials or virtual environments.
- Load models with the validated tensor-only loader. Preserve optimistic version
  guards on moves.
- Use the existing UI components, styles and themes. Do not redesign as part of an
  unrelated change.

Repository-wide agent rules are in [AGENTS.md](../../AGENTS.md). Human contribution
terms are in [CONTRIBUTING.md](../../CONTRIBUTING.md). Licensing details are in
[LICENSING.md](../LICENSING.md).
