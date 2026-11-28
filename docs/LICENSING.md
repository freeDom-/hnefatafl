# Licensing

[Repository introduction](../README.md) · [Third-party notices](../THIRD_PARTY_NOTICES.md) ·
[Contributing](../CONTRIBUTING.md) · [Documentation index](./README.md)

Hnefatafl is **source-available** software licensed under the
[PolyForm Noncommercial License 1.0.0](../LICENSE), SPDX identifier
`PolyForm-Noncommercial-1.0.0`. It is **not** OSI-approved open-source software.

This guide explains the project's licensing scope and the audit behind it. It
summarizes the license; it does not replace or extend the license. If this guide
and the license differ, the license controls. This is not legal advice.

## Rights holder and Required Notice

The root [LICENSE](../LICENSE) file carries the Required Notice:

> Required Notice: Copyright (c) 2026 freeDom- (the Hnefatafl project owner,
> https://github.com/freeDom-)

The verified public identity is the GitHub account
[`freeDom-`](https://github.com/freeDom-), owner of
[`freeDom-/hnefatafl`](https://github.com/freeDom-/hnefatafl). No legal name or
registered organization is published for that account, so this project does not
invent one.

## Audit of the existing situation

The audit was performed on 2026-11-22 against the then-current `main` branch.

- No root `LICENSE`, `COPYING`, `NOTICE` or equivalent project license file
  existed. Published releases and source archives therefore contained no
  project license grant beyond applicable law and the GitHub Terms of Service.
- No contributor license agreement, copyright assignment or DCO requirement was
  present in the repository.
- The GitHub contributors API reported one contributor: `freeDom-`. Commit
  metadata also contains multiple author identities, including automated agent
  and build accounts. This audit does not assume that every recorded Git author
  identity belongs to one legal rights holder; the owner must confirm the
  ownership mapping before relying on the project license for commercial
  authorization.
- No external contributor branch, patch, bundled third-party source tree or
  vendored library was identified. The `spikes/browser_offline` research harness
  references Pyodide/ONNX tooling but fetches development assets into ignored
  `work/` storage; it does not ship a vendored runtime.
- No trained model weights, checkpoints, ONNX files or distributed training data
  set are committed to the repository or attached to the published release
  assets. The published asset is the clean source archive.
- The rules Markdown paraphrases and cites external historical sources. The
  cited source texts remain the property of their authors; they are not
  relicensed by this project. See the source notes in
  [the rules documents](./rules/brandubh.md).
- The original 320 px portrait files predate the recorded artwork-provenance
  work. The 1024 px `*-large.webp` gallery files were generated with the
  built-in image-generation tool, documented in
  [Portrait artwork provenance](./game/progression.md). The generating tool and
  its provider terms are not identified in the repository.

**Conclusion subject to owner confirmation:** the audit found no evidence of an
independent external copyright holder in the first-party application code, AI
training/inference code, documentation or project-created visual assets. The
repository owner should confirm the author-alias mapping and the image-generation
terms before relying on the project license for commercial authorization.

## What the project license covers

Where the project owner holds the necessary rights, the PolyForm Noncommercial
License 1.0.0 applies to first-party material in this repository, including:

- The Python engine, HTTP application, storage, CLI and tests.
- The first-party AI search, training, inference and model-management code.
- The TypeScript/React frontend source, themes, styles and translations.
- First-party SVG/WebP artwork, textures, icons, board previews and portraits
  that were created for the project.
- First-party documentation, configuration examples and deployment tooling.
- Project-authored model-architecture code and training utilities.

The license permits any noncommercial purpose, including private study,
experiments, hobby projects, noncommercial self-hosting, noncommercial
modification and noncommercial redistribution. It also permits use by charitable
organizations, educational institutions, public research organizations, public
safety and health organizations, environmental protection organizations and
government institutions, regardless of funding source, as set out in the license.
Fair-use rights are preserved.

Commercial use outside those terms requires separate explicit authorization from
the relevant rights holders. Hnefatafl does not offer a commercial licensing
program, sales channel or entitlement to commercial terms, and no authorization
is offered or implied through GitHub Issues. Examples that generally require
separate authorization include selling copies, offering commercial managed
hosting, operating a commercial Hnefatafl platform, charging for subscriptions
or premium features, commercial redistribution, and embedding the software in a
commercial product or service. The license's own definitions and exceptions
control over these examples.

## Previous releases and retroactivity

Earlier releases contained no project license file. Adding a license now does
not retroactively revoke permissions already granted by a separate written
agreement or by applicable law, and it does not rewrite the terms attached to an
already-published artifact. It also does not retroactively turn an earlier
unlicensed copy into a licensed copy. Anyone redistributing an older archive
should preserve its existing notices and should not represent this new license
as having applied to that older archive.

## AI models, checkpoints and training data

The project does **not** apply its license automatically to:

- Models independently trained by users.
- User-generated checkpoints, exported ONNX files or training artifacts.
- User gameplay records, self-play data or privately generated training data.
- Third-party model weights or datasets.

The application creates models and checkpoints in the operator's configured data
directory. Those artifacts are controlled by whoever generated them. The project
does not currently distribute project-owned trained weights; the repository and
release asset inventory confirms that no such weights are bundled. If the project
later publishes project-owned weights, checkpoints, datasets or exported models,
it must add a separate, explicit licensing statement and Required Notice to those
artifacts. Model architecture code in this repository remains covered by the
project license, but that does not license independently trained weights.

## Assets and third-party components

Third-party dependencies and assets keep their own licenses. The project does not
relicense them, and this repository does not remove their notices. See
[THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) for the dependency and asset
inventory, and the generated in-application
`/THIRD_PARTY_LICENSES.txt` file for the full texts of bundled frontend runtime
packages.

No separate webfont files are distributed. The interface uses system/browser
fonts with fallbacks. No third-party stock artwork was identified in
`frontend/public/assets`; the artwork provenance documents describe the generated
gallery files.

## Packaging and distribution

- The Python project metadata declares the SPDX license expression
  `PolyForm-Noncommercial-1.0.0` and includes `LICENSE` and
  `THIRD_PARTY_NOTICES.md` in built distributions.
- The frontend package metadata declares the same SPDX identifier.
- The production frontend build copies the root license and notices into
  `frontend/dist` and generates `/THIRD_PARTY_LICENSES.txt`.
- The release packaging script archives the committed clean tree, so the root
  `LICENSE`, `THIRD_PARTY_NOTICES.md`, documentation and source are included in
  `hnefatafl-X.Y.Z.tar.gz`. The generated frontend files
  (`frontend/public/LICENSE.txt`, `THIRD_PARTY_NOTICES.txt` and
  `THIRD_PARTY_LICENSES.txt`) are Git-ignored build outputs and are not part of
  that source archive. The frontend prebuild writes them into `frontend/public`;
  Vite copies them into `frontend/dist` during installation or update.
- No container image is provided. Remote training workers use the same
  application release and therefore receive the same license files.

## Contributor licensing

No CLA, copyright assignment or DCO enforcement is active. Contributors retain
copyright in their own contributions unless they separately agree otherwise.
Contributions are accepted under the project's inbound PolyForm Noncommercial
License 1.0.0 terms. The maintainer does not currently intend to commercially
exploit Hnefatafl, and ordinary noncommercial contributions do not need a CLA or
additional legal agreement. A deferred, non-binding
[CLA proposal](./development/contributor-licensing.md) is retained only as a contingency if the
maintainer ever decides to seek broader rights. It is not active and would
require explicit maintainer approval and legal review before any use.

## Open questions for the project owner

1. Confirm that every Git author identity and automated account used in project
   history belongs to the same rights holder, and confirm the copyright holder
   identity to use in future notices. No personal email address is republished
   in this documentation.
2. Confirm the image-generation tool's terms and the provenance of the original
   320 px portrait files. If the owner cannot establish sufficient rights in a
   specific generated asset, exclude or replace that asset rather than claiming
   it is licensed.
3. Whether to keep the deferred CLA proposal deferred (the current state) or
   explicitly activate it later. Do not activate a CLA or repository-wide
   enforcement settings without maintainer approval and legal review.
4. Decide how project-owned trained weights, if ever published, will be licensed
   and noticed separately.

## Commercial authorization

The project does not provide a commercial licensing service, sales channel or
inquiry process through GitHub Issues. The frontend deliberately has no
"Commercial licensing" link. Commercial use outside the license's permitted scope
requires separate authorization from the relevant rights holders; that
authorization is not offered or implied by this repository, its issue tracker,
donations or contributor discussions. No email address, phone number or commercial
program is invented here.
