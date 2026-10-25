# Development and verification

[Documentation index](README.md) · [Architecture](../ARCHITECTURE.md) · [Working conventions](../AGENTS.md)

## Environment and startup

Use the existing checkout; cloud tasks are already isolated. Do not create a Git
worktree unless explicitly requested. Python 3.12 is recommended (3.11+ supported),
Node 22+ is required (CI uses 24), and CI pins uv 0.12.23. From the root:

```sh
make install
make foundation
.venv/bin/python -m hnefatafl.accounts init-admin
bash scripts/dev.sh
```

`make install` uses `uv sync --frozen --extra train --extra dev` and `npm --prefix frontend ci`.
For OpenVINO parity tests, explicitly add `--extra openvino` to that sync command.
CI installs all extras to exercise the portable OpenVINO CPU plugin.
The lock selects CPU PyTorch. The Makefile places uv/npm caches under `.cache`
and defaults OMP/MKL math threads to two. `foundation` checks real PyTorch autograd
and FastAPI/Optuna imports. Development starts a reloadable loopback API on 8000
and Vite on its default port 5173 (Vite binds all interfaces). Vite proxies `/api`
to 8000; `VITE_API_TARGET` can override that destination. Keep dev access trusted.

For a production-shaped local check, run `make build` then
`HNEFATAFL_DATA_DIR=/absolute/disposable/path bash scripts/start.sh`. The script
requires the built frontend and starts one API worker. It honors `HNEFATAFL_HOST`
and `HNEFATAFL_PORT`; `dev.sh` uses its fixed API host/port. `.env` is not loaded
automatically. See [Deployment](DEPLOYMENT.md) for persistent/service configuration.

## Branch and pull request workflow

Never commit to `main` directly. Update `main`, branch off it with a type prefix
(`feat/…`, `fix/…`, `docs/…`, `test/…`, `refactor/…`, `perf/…`, `chore/…`), make the
change, run the checks below, then push the branch and open a pull request:

```sh
git switch main && git pull
git switch -c fix/short-description
# … edit, then run the relevant checks …
git commit -m 'fix: concrete change'
git push -u origin HEAD
gh pr create --base main --fill   # or --title/--body-file for a written description
gh pr checks                       # wait for green before requesting review
```

CI runs on every pull request, so a red check blocks the change. Leave merging to
the user unless they explicitly ask for it; after a merge, update `main` before
branching again. Keep one concern per branch and do not force-push a branch that is
already shared. Milestone releases take the same path and are tagged only after
their pull request is merged ([Releasing](RELEASING.md)).

## Online milestone workflow

The online milestone integrates into `feat/online-multiplayer`; phase PRs must
**not target main**. Phase 0 PR #6 was retargeted and merged there at
`004d21855858f73705261e2db9e5fd6d5f8cf2b7` before Phase 1 began. Phase 1 PR #9
was merged at `44d0ae7159bd8ec5653134adbc02d9b137025c92` before Phase 2 branched.
Do not duplicate phase changes, cherry-pick silently or merge an unreviewed phase.

```sh
git fetch origin '+refs/heads/*:refs/remotes/origin/*'
git switch -c feat/phase2-online-core origin/feat/online-multiplayer
# implement and validate on the phase branch
git push -u origin HEAD
gh pr create --base feat/online-multiplayer --head feat/phase2-online-core \
  --title 'feat: add durable authoritative online games' --body-file work/phase2/pr-body.md
gh pr checks
```

If the integration branch is absent, create it from current `origin/main` without
changing main, and publish only with authorized repository access. Check actual
PR state and Phase 0 ancestry before implementation. Leave merging to the owner.
There are no releases, version bumps, force pushes or production deployments in
this workflow. Verify CI runs for all pull-request bases and for integration-branch
pushes; the release workflow remains explicitly restricted to main/manual tags.

The isolated account browser suite uses a guarded development-only fake mailbox:

```sh
npm --prefix frontend run test:registration
# Or select an installed browser without changing production settings:
PLAYWRIGHT_CHROMIUM_EXECUTABLE=/usr/bin/chromium npm --prefix frontend run test:registration
```

It starts disposable backend/Vite/mailbox services on loopback ports 8003/4175/8004,
uses an in-memory fake SMTP transport, and exercises English/German desktop/mobile
flows. No real email is sent. Trace recording is off to avoid retaining test token
payloads; normal Playwright failure diagnostics may still contain disposable test
form data. The harness's registration-IP budget is 20 to accommodate six browser
cases sharing one peer; the production default remains five. See
[registration operations](REGISTRATION.md) for schema/configuration boundaries.

Backend online integration uses independent disposable SQLite connections/processes,
barriers and controlled UTC clocks. Run
`.venv/bin/python -m pytest tests/test_online_core.py tests/test_online_api.py tests/test_online_matches.py tests/test_online_migrations.py tests/test_lifecycle.py tests/test_security.py tests/test_account_migrations.py tests/test_deployment.py -q`
before `make check`. See [online operations](ONLINE.md) and the
[frontend handoff](designs/online-phase3-handoff.md). There is no lobby frontend
or new browser validation claim in this backend phase.

## Check the change you made

| Command | Checks |
| --- | --- |
| `make check` | Lint/format, both type checks, backend/frontend tests, production build |
| `make lint` | Ruff lint/format check plus frontend Prettier check; does not rewrite files |
| `make typecheck` | mypy and TypeScript |
| `make test` | pytest and Vitest |
| `make build` | TypeScript and Vite production build |
| `make format` | Rewrites Python/frontend formatting; review the diff |
| `make docs` | Dependency-free local Markdown links, anchors, images and navigation checks |

Run focused tests first when appropriate, for example:

```sh
.venv/bin/python -m pytest tests/test_engine.py
npm --prefix frontend test -- src/Board.test.tsx
```

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
See [Validation](VALIDATION.md) for exact historical scope. Markdown-only changes
need documentation checks and relevant lightweight validation, not expensive
training or a full browser rerun.

## CLI reference

Use `.venv/bin/python -m hnefatafl.cli --help` (or the installed `hnefatafl` entry
point). All commands print JSON; train/tune stream JSON progress before the final
result. Do not parse their entire stdout as a single JSON document. Failures return
nonzero status and a JSON error on stderr. Paths are relative to the current
working directory unless absolute; the server data variable does not override CLI
configuration paths.

| Command | Interface and guide |
| --- | --- |
| `devices` | Report actual PyTorch and optional OpenVINO availability; [hardware](PERFORMANCE.md#installation-and-hardware) |
| `train --config FILE [--resume FILE]` | JSON training config; safe full checkpoint resume; [training](TRAINING.md) |
| `tune --config FILE` | Local Optuna study/trials; [optimization](TRAINING.md#hyperparameter-optimization) |
| `tournament --config FILE` | JSON round robin, final report to stdout; [tournaments](TOURNAMENTS.md) |
| `benchmark-heuristics [--config FILE] [--output FILE]` | CPU heuristic strength, latency and complete-evaluation comparisons; [evidence](SHIELD_MAIDEN.md) |
| `benchmark-training --config FILE [--output FILE]` | Complete CPU/OpenVINO training iterations with repeated seeded runs; [OpenVINO](OPENVINO.md#full-training-benchmark) |
| `benchmark [--config FILE] [--output FILE]` | Fixed-model self-play, optional checkpoint/device/mode selection, no optimizer/model registration; [benchmark protocol](PERFORMANCE.md#benchmark-protocol-and-evidence) |
| `models [--registry DIR]` | List; optional `--import FILE --name NAME` or `--export ID --output FILE`; [models](TRAINING_DATA.md#checkpoints-registry-import-and-export) |
| `worker --server URL [--work-dir DIR] [--concurrency N] [--once] [--max-jobs N]` | Token from `HNEFATAFL_WORKER_TOKEN`, concurrency 1–16; [workers](REMOTE_TRAINING.md) |

Runnable budgets live in [configs](../configs/): smoke training, beginner training,
smoke tuning and tournaments. Inspect a config before running: a full tournament
or recommended plan can be much more expensive than a syntax/interface check.

## API and extension boundaries

The running server exposes OpenAPI at `/openapi.json` and interactive reference at
`/docs`. Game clients use flattened zero-based `source`/`target` indexes and expected
`version` values; [Analysis](ANALYSIS.md) has its separate revision lifecycle.
Web model/job routes resolve owned IDs instead of trusting client filesystem paths.

Keep `engine.py` free of HTTP, storage and PyTorch. Add explicit variant positions
for rules changes, bounded/legal behavior for search changes and real updates,
checkpoint reload and legal play for training changes. Extend `ai_profiles` and
`choose_move` together when adding an opponent. [Architecture](../ARCHITECTURE.md)
maps the current modules; [appearance extension points](PERSONALIZATION.md#adding-another-theme)
cover frontend themes. Preserve deterministic replay and optimistic write guards.

## Documentation workflow

Current guides live behind [the index](README.md). Keep historical requirements and
release evidence labelled; do not recast old counts as fresh validation. Rules
Markdown paths are runtime assets and must not be moved. Run `make docs`, review
commands/defaults against source, and inspect the diff. The link checker validates
local destinations/heading anchors and image files, not remote website availability
or arbitrary shell command semantics. Run `make docs` separately for documentation
changes; it is also part of `make check`. Without installing project dependencies,
run `python3 scripts/check_docs.py` directly. Validate the checker itself with
`python3 -m unittest discover -s scripts -p test_check_docs.py`.

Release tags and GitHub Releases require explicit authorization; a documentation
commit alone is not a milestone release. See [Releasing](RELEASING.md). A merged
pull request is not a release either: version metadata, release notes, the
annotated tag and the GitHub release stay separate, deliberate steps.

## Authentication regression fixtures

Backend regression clients and browser tests log in through the real API with
ordinary disposable accounts and send CSRF tokens. No runtime authentication
bypass exists. The security matrix derives from every registered API route.
