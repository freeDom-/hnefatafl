# Testing

[Development index](README.md) · [Contributor workflow](contributor-workflow.md) · [Validation history](../releases/validation.md)

| Command | Checks |
| --- | --- |
| `make check` | Lint/format, both type checks, backend/frontend tests, production build |
| `make lint` | Ruff lint/format check plus frontend Prettier check; does not rewrite files |
| `make typecheck` | mypy and TypeScript |
| `make test` | pytest and Vitest |
| `make build` | TypeScript and Vite production build |
| `make format` | Rewrites Python/frontend formatting; review the diff |
| `make docs` | Dependency-free local Markdown links, anchors, images and navigation checks |
| `make deployment-check` | Real lifespan/exclusive-lock startup, deferred settlement recovery and disposable updater integration; mandatory in CI |
| `make deployment-cross-version` | Historical updater, schema and backup migration subprocess coverage. Requires a built frontend and `make historical-refs` |
| `make historical-refs` | Fetch and verify pinned historical Git objects from `origin`; never commits source trees |

Run focused tests first when appropriate, for example:

```sh
.venv/bin/python -m pytest tests/test_engine.py
npm --prefix frontend test -- src/Board.test.tsx
```

Vitest already defaults to one worker per reported CPU minus one. On a loaded or
CPU-limited host, cap it explicitly, for example
`npm --prefix frontend test -- --maxWorkers=4`. Do not raise `testTimeout` to hide
worker contention.

Full browser validation starts actual backend/Vite services with isolated temporary
storage, ports 8001/4173 and one worker. From the root:

```sh
cd frontend
npx playwright install --with-deps chromium
npm run test:e2e
```

On a supported Linux host, Playwright installs system libraries as needed; this may
require administrator privileges and network access. If browsers use a custom
cache, set `PLAYWRIGHT_BROWSERS_PATH` for installation **and** execution. Four
redundant mobile worker checks are intentionally skipped; mobile gameplay and
collection flows run. Chromium mobile emulation is not physical Safari validation.
See [Validation](../releases/validation.md) for exact historical scope. Markdown-only changes
need documentation checks and relevant lightweight validation, not expensive
training or a full browser rerun.

## Authentication regression fixtures

Backend regression clients and browser tests log in through the real API with
ordinary disposable accounts and send CSRF tokens. No runtime authentication
bypass exists. The security matrix derives from every registered API route.

## Browser validation

`npm --prefix frontend run test:public` builds the frontend and runs the signed-out
Watch Hall against FastAPI serving `frontend/dist`, with the real production
security boundary and disposable `/tmp/hnefatafl-public-browser-*` storage.
It covers all four themes, English/German and phone/tablet/desktop/4K viewports,
including computed CSS, geometry, current-build asset bodies/MIME types, history,
replay, credential-free GETs and protected-route denial. The separate loopback
fixture controller exposes record identifiers only. Error/retry cases inject one
503 at the browser; ordinary records and API responses are real. Screenshots and
asset evidence are written to `frontend/test-results/public-production/` and
uploaded by CI before other browser configurations clean their output.
See [the production CSS regression record](../game/interface/arena.md#public-watch-hall).

Install Playwright Chromium first, or set
`PLAYWRIGHT_CHROMIUM_EXECUTABLE=/usr/bin/chromium` when using a system installation.
The browser projects check desktop and mobile emulation; this is not physical-device
evidence. Run heavy browser and backend training checks serially on constrained
local hosts. The suites also run in browser CI. No service worker is installed.
