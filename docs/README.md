# Documentation

Hnefatafl is a source-available Viking board-game application with a Python
rules engine, a React browser client, search opponents and self-play training.
Choose a section below; every page describes the current implementation unless it
is explicitly labelled historical.

| Section | Use it for |
| --- | --- |
| [Getting started](getting-started/README.md) | Install locally, create the first administrator, open the Arena |
| [Installation and operations](installation/README.md) | Deploy, configure, update, back up, restore and troubleshoot a server |
| [Administration](administration/README.md) | Accounts, permissions, security, online operations and monitoring |
| [Game](game/README.md) | Rulesets, game modes, online play, ratings, leagues, tournaments, achievements, replay and the UI |
| [Training](training/README.md) | Train, evaluate, resume, tune, import and manage neural opponents and workers |
| [Evaluation](evaluation/README.md) | Benchmark methodology, agent comparisons and preserved measurements |
| [Development](development/README.md) | Architecture, setup, testing, API, release preparation, UI rules, decisions and active roadmaps |
| [Releases](releases/README.md) | Immutable version-specific release notes and their index only |
| [Licensing](./LICENSING.md) | PolyForm Noncommercial licence scope, third-party terms and open questions |
| [Donations](./DONATIONS.md) | Voluntary support policy and the Buy Me a Coffee link |
| [Original requirements](./REQUIREMENTS.md) | The initial specification, including goals that remain unimplemented |

Ruleset sources are runtime assets: [Brandubh](./rules/brandubh.md),
[Tablut](./rules/tablut.md) and [Copenhagen](./rules/copenhagen.md). They must stay
under `docs/rules/` because the server reads them at runtime.

## Documentation contract

- Each topic has one canonical page. Link to it instead of copying commands,
  defaults or parameter tables.
- Follow the [audience boundaries](development/documentation-guidelines.md#ownership-by-audience):
  player docs stay player-facing; API/persistence internals live in Development;
  benchmark evidence lives in Evaluation.
- Current guides describe implemented behavior; [release notes](releases/README.md)
  preserve the evidence, counts and limitations of their own release. Releases
  contains only those notes and its index; release procedure/readiness and testing
  live in Development.
- Distinguish implemented, experimental, deferred and planned work. Do not present
  a roadmap item as available.
- Keep complete implementation roadmaps for features that are still under
  development, including completed phases and acceptance criteria. Remove a
  roadmap only after the whole feature is fully developed and accepted, once its
  durable knowledge is in canonical documentation.
- Use [the documentation guidelines](development/documentation-guidelines.md) when
  adding or changing a feature. Run `make docs` before opening a pull request.
