# Validation guidance

This page explains what the project's checks establish and where exact historical
evidence lives. It is not a fresh test report and does not relabel old counts as
current.

## Evidence rules

- Each release note records the checks, counts, hardware and limitations of its own
  tagged release. Those claims remain historical after later edits.
- A green pull-request check proves that revision's CI scope. It is not combined
  release-candidate acceptance or deployment authorization.
- Functional tests prove data generation, real optimizer updates, checkpoint
  restoration and legal play. They do not establish playing strength, calibrated
  winning probabilities or acceleration.
- CPU results do not validate CUDA, MPS, Intel OpenVINO or physical browsers.
  Hardware-gated tests that skip are not passes.
- Browser emulation is not physical iOS Safari/Android evidence. No service worker
  is installed.

## Current suites

| Suite | Command | Scope |
| --- | --- | --- |
| Documentation | `make docs` | Local Markdown links, anchors, images and reachability |
| Backend | `make test` / `pytest` | Engine, AI, training, persistence, API, security, migrations, workers, deployment |
| Frontend unit | `npm --prefix frontend test` | Rendering, state, tables, charts, localization, reuse, deletion races |
| Frontend build | `make build` | TypeScript and production Vite build |
| Browser end to end | `npm --prefix frontend run test:e2e` | Real API desktop/mobile flows, gameplay, workshop, tournaments, themes |
| Public spectator | `npm --prefix frontend run test:public` | Signed-out production build, all themes, EN/DE, multiple viewports, protected-route denial |
| Registration | `npm --prefix frontend run test:registration` | Disposable fake mailbox, verification/reset, EN/DE desktop/mobile |
| Online | `npm --prefix frontend run test:online` | Online creation, play, reconnect and lifecycle |
| Personal | `npm --prefix frontend run test:personal` | Dashboard, statistics, records, profile and progression |
| Deployment | `make deployment-check` | Real lifespan under exclusive lock, updater/rollback/recovery integration |
| Cross-version | `make deployment-cross-version` | Historical release upgrades, schema recovery refusals and backup relocation |
| Browser-analysis | focused pytest/Playwright commands in [setup](../development/setup.md) | Opt-in runtime, publication and parity; not production acceptance |

The deployment/historical checks require a built frontend and pinned Git objects
(`make historical-refs`). See [testing](../development/testing.md) for focused
commands and resource limits.

## Capability coverage

| Capability | Exercised |
| --- | --- |
| Three rulesets | Layouts, movement, captures, special squares, royal captures, Copenhagen shieldwalls/encirclement/forts, repetition, serialization and replay |
| AI and models | Real baseline/alpha-beta/MCTS/neural games in every variant, legal wrappers, tensor-only imports, exact architecture checks and atomic deletion |
| Exact resumption | Split CPU training matches uninterrupted weights; best models survive weaker later iterations and directory moves |
| Optimization | Real Optuna trials, persisted study continuation, evaluation objectives and best-configuration export |
| API and persistence | Real moves/replay, versioned deletion, statistics recalculation, fresh/fixed seeds, completed-game guards, permission boundaries and races |
| Worker lifecycle | Real subprocess training/resume/export/import, study continuation, health during training, cancellation, reaping and cleanup |
| Tournaments | Real isolated workers, balanced round robins, fixed roles, unfinished outcomes, durable results, replay and reference protection |
| Accounts and online | Registration/reset via fake mail, verified/unverified states, ratings, deadlines, reconnect and public projection |
| Progression | Automatic achievements, portrait entitlement/selection, role win rates and league awards |
| Browser flows | Real desktop/mobile Chromium through gameplay, workshop, models, workers, tournaments, profiles, themes and localization |
| Quality/build | Ruff/Prettier, mypy, TypeScript and production Vite build |

## Reproduce locally

```sh
make install
make docs
make check
cd frontend
npx playwright install chromium
npm run test:e2e
```

For a custom browser cache, set `PLAYWRIGHT_BROWSERS_PATH` for both installation
and execution. On constrained hosts, run browser and training checks serially and
cap workers; see [testing](../development/testing.md).

## Limits and pending external validation

- CUDA/MPS execution, GPU throughput, physical GPU memory behaviour and target-host
  systemd/reverse-proxy operation require their own hardware validation.
- Intel OpenVINO requires driver/runtime support and compatible hardware; a GPU
  visible to PyTorch is not sufficient.
- Copenhagen's primary source was inaccessible; the selected interpretation and
  digital repetition convention are documented in the [rules](../rules/copenhagen.md).
- Long training runs, competitive model strength, calibrated winning chances,
  production capacity, combined release-candidate acceptance and commercial
  deployment are outside routine checks.
- Browser analysis has open competitive-checkpoint, physical-device, verified-GPU,
  sustained-memory and distribution-licensing gates; see
  [the roadmap](../development/roadmap/browser-analysis.md).
- The FastAPI/Starlette test client may emit a dependency deprecation warning. It
  does not skip or replace API tests.

## Historical evidence

Exact counts, commit-scoped evidence and known failures belong to the relevant
[release note](README.md). The Arena snapshot is a point-in-time capability matrix,
not current acceptance. Benchmark measurements are preserved under
[Evaluation](../evaluation/README.md) with their original hardware and scope.
