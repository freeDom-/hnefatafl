# Contributing to Hnefatafl

[Repository introduction](./README.md) · [Documentation index](./docs/README.md) ·
[Licensing](./docs/LICENSING.md) · [Contributor workflow](./docs/development/contributor-workflow.md)

Hnefatafl is source-available under the
[PolyForm Noncommercial License 1.0.0](./LICENSE). It is not OSI-approved
open-source software. Contributions are welcome, but every contribution must
respect the project's licensing model and the rights of third parties.

## Before you contribute

- You must have the authority to submit the code, documentation, artwork or
  other material you contribute. Do not submit work that belongs to an employer,
  client, school or another person unless you have permission to do so.
- Do not copy code, assets, model weights, fonts, data or documentation from a
  source whose license is incompatible with this project. In particular, do not
  introduce GPL, AGPL, SSPL, noncommercial-with-extra-restrictions or
  unlicensed material without prior written approval from the project owner.
- Keep existing copyright notices, attribution and third-party license files
  intact. Add new third-party components and their notices to
  [THIRD_PARTY_NOTICES.md](./THIRD_PARTY_NOTICES.md).
- Do not remove or weaken a third-party notice to make a dependency look
  compatible.
- Do not submit credentials, private data, production databases, unpublished
  personal information or model weights you are not authorized to publish.
- Follow the development, testing and resource-safety rules in
  [AGENTS.md](./AGENTS.md), [Development setup](./docs/development/setup.md) and
  [Contributor workflow](./docs/development/contributor-workflow.md). Deliver changes
  on a branch and through a pull request against `main`.

## Contribution licensing status

There is currently **no active Contributor License Agreement (CLA)**, copyright
assignment or DCO enforcement. Contributors retain copyright in their own
contributions unless they separately agree otherwise. Do not assume that
submitting a pull request grants any broader rights than the project's inbound
PolyForm Noncommercial License 1.0.0 terms.

The maintainer does not currently intend to commercially exploit Hnefatafl.
Ordinary noncommercial contributions do not need a CLA or any additional legal
agreement. A deferred, non-binding
[CLA proposal](./docs/releases/contributor-licensing-proposal.md) is retained only as a contingency
if the maintainer ever decides to seek broader rights. It is not active, grants
no additional rights now and would require explicit maintainer approval and
legal review before any activation.

## What to include in a pull request

- A focused change with a conventional commit prefix and a clear description of
  the delivered behavior.
- Exact validation commands and results, including any limitations.
- Confirmation that third-party notices and license metadata were updated for
  any new dependency or asset.
- A description of any ownership, provenance or licensing question that cannot
  be resolved from the repository.
- Documentation updates for changed behavior, using the audience boundaries in the
  [documentation guidelines](docs/development/documentation-guidelines.md): player
  pages stay player-facing, API contracts live in `docs/development/api/`, and
  benchmarks in `docs/evaluation/`. Keep complete roadmaps while a feature is under
  development; remove one only after the whole feature is fully developed and
  accepted.

## Commercial use

The project does not offer a commercial licensing program or sales channel.
Commercial use outside the permission granted by the project license requires
separate authorization from the relevant rights holders; no authorization is
offered or implied through GitHub Issues, contribution discussions or donations.
