# Testing and validation

[Development index](README.md) · [Contributor workflow](contributor-workflow.md) · [Release readiness](releasing/readiness.md) · [Evaluation methodology](../evaluation/methodology.md)

## What evidence means

- Release notes record the checks, counts, hardware and limitations of their tagged
  revision. They remain historical after later edits.
- A green PR check proves that revision's CI scope, not combined production
  acceptance or deployment authorization.
- Functional tests prove data generation, real optimizer updates, checkpoint reload
  and legal play. They do not establish playing strength, calibrated probabilities
  or acceleration.
- CPU results do not validate CUDA/MPS/OpenVINO or physical browsers. Skipped
  hardware tests are not passes. Browser emulation is not physical-device evidence.
- Do not weaken/skip a failing test to pass a change; diagnose and report separately.

## Commands

| Command | Checks |
| --- | --- |
| `make check` | Docs, lint/format, both type checks, backend/frontend tests, production build |
| `make docs` | Markdown links/anchors/images/reachability + checker regression tests |
| `make lint` | Ruff and Prettier checks without rewriting |
| `make typecheck` | mypy and TypeScript |
| `make test` | pytest and Vitest |
| `make build` | TypeScript + production Vite build |
| `make format` | Rewrites Python/frontend formatting; review the diff |
| `make deployment-check` | Real lifespan/exclusive-lock startup, deferred recovery, disposable updater integration |
| `make deployment-cross-version` | Historical updater/schema/backup migration; requires built frontend and `make historical-refs` |
| `make historical-refs` | Fetch/verify pinned historical Git objects from origin |

Focused examples:

```sh
.venv/bin/python -m pytest tests/test_engine.py
npm --prefix frontend test -- src/Board.test.tsx
```

Vitest defaults to one worker per reported CPU minus one. On a constrained host,
cap explicitly (`npm --prefix frontend test -- --maxWorkers=4`); do not raise
`testTimeout` to hide contention.

## Suites

| Suite | Command | Scope |
| --- | --- | --- |
| Backend | `make test` / `pytest` | Engine, AI, training, persistence, API, security, migrations, workers, deployment |
| Frontend unit | `npm --prefix frontend test` | Rendering, state, tables, charts, localization, races |
| Production build | `make build` | TypeScript + Vite |
| Browser end to end | `npm --prefix frontend run test:e2e` | Real API desktop/mobile gameplay, Workshop, tournaments, themes |
| Public spectator | `npm --prefix frontend run test:public` | Signed-out production build, themes, EN/DE, viewports, protected routes |
| Registration | `npm --prefix frontend run test:registration` | Disposable fake mailbox, verification/reset, EN/DE |
| Online | `npm --prefix frontend run test:online` | Creation, play, reconnect, lifecycle |
| Personal | `npm --prefix frontend run test:personal` | Dashboard, statistics, records, profile, progression |
| Browser screenshots | `npm --prefix frontend run test:docs-screenshots` | Opt-in documentation captures only |

Deployment/historical checks require a built frontend and pinned Git objects. Full
browser validation starts disposable backend/Vite services on loopback with isolated
storage. Install Playwright Chromium first; set `PLAYWRIGHT_CHROMIUM_EXECUTABLE` for
a system browser. The browser projects check desktop/mobile emulation; they are not
physical-device evidence. Open `docs/development/setup.md` for browser-analysis
runtime-specific commands.

## Focused integration commands

```sh
npm --prefix frontend run test:registration
npm --prefix frontend run test:online
npm --prefix frontend run test:personal
make deployment-check
npm --prefix frontend ci && npm --prefix frontend run build
make historical-refs
make deployment-cross-version
```

The registration suite uses a disposable fake mailbox and no real email. Online
integration uses disposable SQLite connections/processes, controlled clocks and
barriers. Deployment/cross-version checks use a real lifespan/exclusive lock and
pinned historical releases (schemas 15, 20, 21), including updater/backup migration;
they complement systemd/Proxmox validation on a target host.

## Capability coverage

| Capability | Exercised |
| --- | --- |
| Three rulesets | Layouts, movement, captures, special squares, royal capture, Copenhagen shieldwalls/encirclement/forts, repetition, serialization/replay |
| AI/models | Real baseline/alpha-beta/MCTS/neural games, wrappers, tensor-only imports, architecture checks, atomic deletion |
| Exact resumption | Split CPU training matches uninterrupted weights; best models survive weaker iterations/directory moves |
| Optimization | Real Optuna trials, study continuation, evaluation objectives, best-config export |
| API/persistence | Moves/replay, versioned deletion, statistics, seeds, guards, permission boundaries, races |
| Workers | Real subprocess training/resume/export/import, study continuation, health, cancellation/reaping |
| Tournaments/leagues | Isolated workers, balanced/fixed-role series, unfinished outcomes, durable results, settlement/rebuild |
| Accounts/online | Registration/reset via fake mail, verified states, ratings, deadlines, reconnect, public projection |
| Progression | Automatic achievements, portrait unlock/equip, role rates, league awards |
| UI | Real Chromium desktop/mobile flows across themes/localization/records/Workshop |

## Reproduce

```sh
make install
make docs
make check
cd frontend
npx playwright install chromium
npm run test:e2e
```

For a custom browser cache, set `PLAYWRIGHT_BROWSERS_PATH` for installation and
execution. Run heavy browser/training checks serially on constrained hosts and clean
up services. CI runs the complete matrix.

## Limits

- CUDA/MPS, GPU throughput/memory and target-host systemd/proxy operation require
  target hardware validation; Intel OpenVINO requires driver/runtime support.
- Copenhagen's primary source was inaccessible; the selected interpretation is in
  [rules](../rules/copenhagen.md).
- Long training, competitive strength, calibrated chances, production capacity and
  combined release-candidate acceptance are outside routine checks.
- Browser analysis has open competitive-checkpoint, physical-device, verified-GPU,
  sustained-memory and distribution-licensing gates; see the
  [roadmap](roadmap/browser-analysis.md).
- FastAPI/Starlette test-client deprecation warnings do not replace API tests.

Historical counts and failures belong to the relevant
[release note](../releases/README.md). Benchmark measurements remain in
[Evaluation](../evaluation/README.md) with their original scope.
