# Validation evidence

Validation uses the actual Linux cloud machine, Python 3.12, Node 24 and the
hash-locked CPU dependency environment. Tests use isolated temporary persistence.

| Capability | Evidence |
| --- | --- |
| Three engines | 61 source-specific tests: layouts, movement, captures, special squares, royal captures, Copenhagen shieldwalls/encirclement/forts, outcomes, serialization and replay |
| AI, models and training | 95 tests: real baseline/alpha-beta/MCTS/neural full games in every variant, actual updates, tensor-only compatible imports and atomic model deletion |
| Exact resumption | Split CPU training matches uninterrupted weights exactly; an earlier best model survives weaker later iterations and moving/deleting its original directory |
| Optimization | Actual Optuna trials, persisted study resumption, evaluation-based objectives and exported best configuration |
| API and persistence | 40 tests: real moves, durable replay, racing-writer conflicts, retained deletion history, canonical AI variants, schema upgrades, deletion/new-game races, statistics/rates and controlled model paths |
| Worker lifecycle | Actual subprocess training/resumption/export/import/neural moves, optimization-study resumption, health requests during training, cancellation/exit/reaping and shutdown cleanup |
| Saved tournaments | 35 tests: real isolated workers, balanced scoring, unfinished outcomes, durable partial results, stop/delete, model reference protection, legal replay, migration and failure/shutdown cleanup |
| Browser unit flows | 17 Vitest tests, including legal interaction, stale requests, profile selection/deletion, model confirmations, AI parameters, sorting, name suggestions, appearance persistence and tournament setup/selection races |
| Browser end to end | 13 passed desktop/mobile Chromium checks; 3 duplicate mobile worker checks intentionally skipped |
| Quality/build | Ruff and Prettier formatting/lint, mypy, TypeScript checks and production Vite build |

Browser tests use real API services and cover Tablut interaction, undo/redo, rules,
history, profiles, all board sizes, AI playback and CPU training through registration,
compatible model selection and a neural move. They assert no horizontal mobile
overflow. Desktop and mobile additionally complete a real Brandubh AI game,
verify the disabled final board and archive outcome, and reopen the saved result.
Screenshots were captured from the running application.

The v0.5 full suite passes **196 Python tests**, **14 frontend tests** and **10 browser
checks**. New browser flows exercise separate completed AI variants and readable
parameters, human selection/deletion with retained history, Viking suggestions,
actual CPU training followed by model deletion, and theme changes followed by reload
and a legal move. Appearance assertions check actual SVG artwork and computed colors,
not only preference attributes. Follow-device mode responds to color-scheme changes.
The redundant mobile training checks are skipped; mobile records, gameplay and
appearance flows run.

Pytest isolates the module-level ASGI application's data directory before test
collection. A subprocess collection check verifies that an explicitly supplied
user-data directory is left untouched. The v0.5 production restart served schema 2;
existing saved game JSON, IDs and versions match their pre-restart digest exactly.

The v0.6 full suite passes **231 Python tests**, **17 frontend unit tests** and
**13 real-API browser checks**. Tournament browser flows create balanced comparisons,
verify alternating side assignments and explicit unfinished scores, reopen saved
results after reload, inspect parameters, compare replay boards with engine states,
cancel deletion, confirm deletion and stop an active worker. The new tournament
flows also pass on mobile without horizontal page overflow; cancellation is checked
once on desktop. The targeted tournament browser checks were repeated after the
initial-detail loading fix. Model-backed tournaments run actual neural searches in
the backend tests; they remain replayable after model deletion. Schema-2 upgrade
tests retain saved games, player tombstones and archived model labels.
The compiled interface was also inspected on desktop and mobile with actual
dark-theme colors and no browser errors. Screenshots in `docs/screenshots/`
show the real setup dialog and saved standings. Production startup upgraded the
existing schema-2 database to schema 3, served version 0.6.0 and preserved all seven
existing game records exactly against a pre-upgrade digest. A full data backup was
made before this update; test tournaments stayed in isolated temporary storage.

The documented CLI smoke training and two-trial optimization configurations were
executed. One smoke training run generated one self-play game, performed actual
gradient updates, registered a compact model and completed both balanced evaluation
games. It lost both games (0 wins, 2 losses), so no stronger-play claim is made.
Truncated games elsewhere remain explicit unfinished outcomes, never rules draws.

## Reproduce

```sh
make install
make check
cd frontend
npx playwright install chromium
npm run test:e2e
```

On this cloud machine, browsers are retained at `/workspace/.cache/ms-playwright`;
set `PLAYWRIGHT_BROWSERS_PATH` to that location when running the browser tests.
For a fresh Linux host, install required browser runtime libraries (Playwright's
`install --with-deps chromium` is suitable on supported distributions).

Production validation starts `bash scripts/start.sh`, checks health, serves the
built JavaScript/CSS and exercises gameplay requests against that process. Startup
and locked installation are repeated to check retained-file behavior. Live services
must restart after a cloud snapshot is restored.

## Limits and pending external validation

- This host reports `cuda_available: false` and `mps_available: false`; CPU execution
  passed. CUDA/MPS code paths require compatible hardware and the matching official
  PyTorch distribution. They are not reported as tested.
- The exact linked Tablut and Brandubh leaflets were recovered. Copenhagen's primary
  endpoint returned HTTP 455. Its rule document cites recovered transcriptions and
  explicitly distinguishes shieldwall-completion interpretations and the digital
  repetition threshold. Primary-source reconciliation remains pending access.
- Chromium mobile emulation verifies the responsive/touch flow; physical iOS Safari,
  Android devices and other browser engines require their own compatibility testing.
- systemd/reverse-proxy examples need validation on the target Linux/LXC host.
  No public deployment or cloud publication is implied by local validation.
- Long training runs, competitive strength, distributed GPU throughput, authenticated
  internet multiplayer and PostgreSQL migration tooling are outside these checks.

The current FastAPI/Starlette test client emits a dependency deprecation warning;
the tested requests execute and pass. It does not skip or replace API tests.
