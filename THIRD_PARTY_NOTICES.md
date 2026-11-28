# Third-party notices

[Repository introduction](./README.md) · [Licensing](./docs/LICENSING.md) ·
[Contributor licensing](./docs/development/contributor-licensing.md)

This file inventories third-party components and assets used by Hnefatafl. It
does **not** relicense them. Each component keeps its own license and attribution
requirements. The project license in [LICENSE](./LICENSE) applies only to material
the project owner has the right to license.

The inventory was reviewed on 2026-11-22 against the then-current lockfiles.

## Frontend runtime dependencies

These packages are installed from `frontend/package-lock.json` and bundled into
the production browser application by Vite.

| Package | Locked version | License |
| --- | --- | --- |
| `react` | 19.3.1 | MIT |
| `react-dom` | 19.3.1 | MIT |
| `react-day-picker` | 9.14.0 | MIT |
| `react-markdown` | 10.1.0 | MIT |

The production dependency closure is computed from the root runtime dependencies
through the installed package paths in `frontend/package-lock.json`. Nested
dependencies with distinct versions are kept distinct. TypeScript type-only
packages are recorded separately and are not treated as distributed runtime
code; development-only packages are excluded. The current closure contains
MIT-licensed packages and the ISC-licensed `@ungap/structured-clone`. No runtime
frontend dependency uses a copyleft, source-available-only or noncommercial
license.

At build time, `frontend/scripts/licenseArtifacts.mjs` resolves that closure
without network access, reads each installed runtime package's metadata and
license files, and writes them to `frontend/public/THIRD_PARTY_LICENSES.txt`.
It fails the build if a required package, package metadata, license identifier
or license text is missing. Vite copies the generated file into the production
build, where it is available at `/THIRD_PARTY_LICENSES.txt`. Identical license
texts are listed once with a reference from later packages. The generated file
is ignored by Git because it is reproducible from the locked dependencies; it is
not committed as a stale license-text snapshot.

Direct development and test tooling includes MIT, Apache-2.0, ISC, BSD-2-Clause,
BSD-3-Clause and CC-BY-4.0 material (for example Playwright, TypeScript and
browserslist data). That tooling is used to build and validate the application
but is not part of the shipped runtime bundle. Its notices remain in its own
installed packages.

## Python dependencies

The Python application installs dependencies from `uv.lock`; it does not vendor
their source or bundle their distributions in the project wheel. Each installed
package keeps its own license files in the environment.

| Dependency | Locked version | License |
| --- | --- | --- |
| `fastapi` | 0.156.4 | MIT |
| `uvicorn` | 0.54.0 | BSD-3-Clause |
| `pydantic` | 2.14.1 | MIT |
| `python-multipart` | 0.0.32 | Apache-2.0 |
| `torch` (optional `train`/`openvino` extra) | 2.14.1 (+cpu build) | Apache-2.0 with bundled third-party notices |
| `numpy` (optional `train`/`openvino` extra) | 2.4.6 or 2.5.4 by extra | BSD-3-Clause and bundled permissive notices |
| `optuna` (optional `train` extra) | 4.9.0 | MIT |
| `schedulefree` (optional `train` extra) | 1.4.1 | Apache-2.0 |
| `openvino` (optional `openvino` extra) | 2026.4.1 | Apache-2.0 |
| `setuptools` (build backend) | 84.2.1 | MIT |

Runtime-training and GPU packages can include additional third-party components
inside their own distributions. The project does not redistribute those
environments; operators install the locked packages themselves. If the project
ever ships a container, wheelhouse or frozen runtime containing those packages,
the distributor must collect and ship their complete notices at that time.

Test and development dependencies (`pytest`, `httpx`, `ruff`, `mypy` and their
transitive packages) are permissive and are not distributed with the running
application.

## Visual assets

The files under `frontend/public/assets` are first-party project artwork unless a
separate notice states otherwise:

- Theme textures, board previews, knot bands, carved frames, league and portrait
  frame SVGs, and the favicon are project-created or project-generated assets.
- The twelve `*-large.webp` portrait gallery files are generated redraws
  documented in [Portrait artwork provenance](./docs/game/progression.md).
- The original 320 px portrait files predate the recorded provenance work. The
  repository does not identify a third-party stock source, but the generating
  tool and the original source terms are not recorded. The owner must confirm
  provenance before asserting the project license over those files.
- No third-party photographic, stock or scraped artwork was identified.

The built-in image-generation tool used for the documented redraws is not named
in the repository, and its provider terms are not recorded. That is an open
ownership question, not an assertion that the assets are unencumbered. See
[Licensing](./docs/LICENSING.md#open-questions-for-the-project-owner).

## Fonts

No webfont files are distributed. The interface uses locally available system
fonts and generic fallbacks, including `Segoe UI Historic`, `Noto Sans Runic`,
Georgia, Inter and serif/sans-serif fallbacks. Their rendering depends on the
viewer's system; the project does not redistribute them.

## Rules, source citations and documentation

The rules documents paraphrase rules from external sources and cite those
sources. The cited leaflets, web pages and rulebooks remain under their authors'
terms. The project does not copy or relicense the original source texts. See
[Brandubh](./docs/rules/brandubh.md), [Tablut](./docs/rules/tablut.md) and
[Copenhagen](./docs/rules/copenhagen.md).

## AI models, checkpoints and training data

No project-owned trained model weights, checkpoints, ONNX exports or training
data sets are committed or attached to the published release asset. The model
architecture and training code are first-party project code. Models, checkpoints
and private training data generated by users or operators are not automatically
covered by the project license. See
[AI models and training artifacts](./docs/LICENSING.md#ai-models-checkpoints-and-training-data).

## Browser analysis runtime (optional distribution)

`spikes/browser_offline` remains a development research harness that stages
pinned assets into ignored `work/` storage. The optional Phase 4 production
browser-analysis runtime is distributed separately and only after an explicit
administrator build/activation; it is not part of the ordinary frontend bundle.

| Component | Pinned version | License | Distribution |
| --- | --- | --- | --- |
| Pyodide | 0.27.7 | MPL-2.0 | Same-origin runtime distribution |
| ONNX Runtime Web | 1.22.0 | MIT | Same-origin runtime distribution |
| CPython standard library (inside Pyodide) | 3.12.x | PSF-2.0 | Inside the Pyodide bundle |

The exact asset digests are recorded in `hnefatafl/browser_pins.py`; staging fails
closed on any mismatch, and a self-consistent manifest cannot substitute a
different upstream binary (both the file inventory and declared upstream digests
are re-bound to the pins). The required license texts are source-controlled under
`hnefatafl/browser_licenses/` (MPL-2.0 for Pyodide, MIT for ONNX Runtime, PSF-2.0
for CPython) and are included in every staged runtime under `licenses/`, covered
by the runtime manifest digests. The pinned Pyodide distribution embeds further
third-party components governed by Pyodide's own distribution notices; the project
ships the core MPL/PSF texts and relies on the pinned upstream distribution for its
complete notice set. The inspected distributions and exact sources are recorded in the
[Phase 4 report](./docs/evaluation/browser-analysis/browser-analysis-phase4.md#19-distribution-licensing-review-exact-sources-inspected).
The complete embedded third-party notice inventory is **not** shipped by the pinned
Pyodide core release or the `onnxruntime-web` npm tarball, and could not be
independently retrieved in this environment. This remains an unresolved
distribution blocker before wider redistribution; no component is relicensed under
the project license and the licensing audit is not claimed complete. MPL-2.0 applies to Pyodide itself and does not
relicense first-party project code; the project does not relicense any
third-party component. `onnx`/`onnxruntime` Python packages remain optional
administrator-side export/validation tooling and are not distributed to browsers.

Approved ONNX inference artifacts are first-party or owner-supplied model weights.
Distributing them to a browser necessarily makes those weights accessible to the
receiving client. The project does not commit private competitive checkpoints,
training data or unrelated third-party model weights.

## Compatibility conclusion

All identified runtime dependencies use permissive licenses that are compatible
with distributing the application under the PolyForm Noncommercial License 1.0.0,
provided their notices are preserved. No runtime dependency requires the project
to grant broader rights than the project license, and no dependency imposes a
noncommercial restriction that conflicts with the project license or with any
separate authorization by the relevant rights holders. This is a repository-level
engineering review, not a legal opinion.

## Regenerating the bundled notices

After `npm ci`:

```sh
node frontend/scripts/licenseArtifacts.mjs
```

The production build runs this automatically through `prebuild`. It copies the
project license and this notice into `frontend/public`, then writes
`THIRD_PARTY_LICENSES.txt` from the installed runtime packages.
