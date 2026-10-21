# Validation evidence

Validation uses the actual Linux cloud machine, Python 3.12, Node 24 and the
hash-locked CPU dependency environment. Tests use isolated temporary persistence.

| Capability | Evidence |
| --- | --- |
| Three engines | 61 source-specific tests: layouts, movement, captures, special squares, royal captures, Copenhagen shieldwalls/encirclement/forts, outcomes, serialization and replay |
| AI, models and training | 95 tests: real baseline/alpha-beta/MCTS/neural full games in every variant, actual updates, tensor-only compatible imports and atomic model deletion |
| Exact resumption | Split CPU training matches uninterrupted weights exactly; an earlier best model survives weaker later iterations and moving/deleting its original directory |
| Optimization | Actual Optuna trials, persisted study resumption, evaluation-based objectives and exported best configuration |
| API and persistence | 40 baseline API tests plus 21 game-management cases: real moves/replay, versioned deletion, recalculated statistics, fresh/fixed seeds, hidden model references, consent fingerprints, rollback and in-flight request races |
| Worker lifecycle | Actual subprocess training/resumption/export/import/neural moves, optimization-study resumption, health requests during training, cancellation/exit/reaping and shutdown cleanup |
| Saved tournaments | Real isolated workers, balanced two/three/four-player round robins, participation-aware scoring, unfinished outcomes, durable partial results, stop/delete, model reference protection, legal replay and failure/shutdown cleanup |
| Browser unit flows | 74 Vitest tests, including complete setup/name restoration, reviewed resumption, independent analysis requests, localization, training plans, sorting/filtering and deletion races |
| Browser end to end | 26 passed desktop/mobile Chromium checks; 4 duplicate mobile worker checks intentionally skipped |
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

The v0.7 suite passes **252 Python tests**, **35 frontend unit tests** and
**16 real-API browser checks**. New browser coverage verifies fresh seeds on new
games/restart, current-game pointer cleanup, completed-game deletion with recalculated
counts/rates/averages, statistics before collapsible history and no mobile overflow.
Actual CPU training supplies a model whose training seed differs from its evaluation
seed; the workshop shows the correct value and the new-game form selects that
compatible model. Two unfinished model references, including one created outside
the browser session, require explicit discard consent. Deletion removes them without
results and retains completed replay and the original training checkpoint.

Focused tests cover malformed/comparable model evaluations, unfinished outcomes
receiving zero ranking points, deterministic ties, manual opponent choices,
clearing stale model IDs when switching algorithms, changed-record confirmations,
model-usage races, rollback on registry failure and late AI responses after deletion.
Training and tournament test budgets remain explicit and deterministic. Browser
playback fixtures use an explicit fixed game seed to avoid relying on a random
game finishing within the test timeout; ordinary UI creation exercises fresh seeds.
The compiled statistics panel was visually inspected on desktop and mobile in
light/dark mode, including readable table colors, 12px values and no page overflow.
The model confirmation screenshot shows two real unfinished references with Delete
disabled until explicit consent. All screenshot/training records use isolated
temporary storage. Production startup serves v0.7.0/schema 3 and preserves all seven
existing game records exactly against a pre-update digest; a full data backup was
made before restart. The model-usage endpoints also respond for existing models.

The v0.8 suite passes **264 Python tests**, **48 frontend unit tests** and
**20 real-API browser checks**. Round-robin coverage verifies every unordered
pair, balanced attacker/defender assignments, correct per-opponent participation
and scores, four-player worker progress, cancellation and retained replay.
Existing two-player reports remain readable without rewriting stored data.

Browser flows exercise ascending/descending numeric sorts in both statistics
tables, independent case-insensitive player/history searches, compact AI rows with
full parameter inspection, and four-opponent tournament setup/results/replay on
desktop and mobile. Appearance checks verify the actual wood/walnut defaults,
changed artwork/colors and persistence of valid saved choices. The selected wooden
king design is **1 · Fjord Diadem**, the low three-peak interlaced circlet from the
concept comparison; the other token themes retain their existing king artwork.
The selected SVG was visually inspected on actual 7×7 and 11×11 boards. Desktop
and mobile appearance flows passed again after the selection, including a legal
move, reload and preference changes. Compiled-client screenshots show sortable
records, compact mobile AI entries and a real completed four-player tournament
with 12 replayable matches. Escape closes the parameter dialog and restores focus.

Production startup serves v0.8.0/schema 3 and both compiled assets. All seven
existing games retain their exact IDs, versions and saved JSON against the
pre-update digest. A complete data backup was made before restart; all four
existing model-usage endpoints respond. Test games and tournaments stayed in
isolated temporary storage. No data migration is required.

The v0.9 suite passes **302 Python tests**, **67 frontend unit tests** and
**24 real-API browser checks**. New analysis coverage checks each source and
current-player perspective, legal advisor hints, immutable first reports,
undo/redo/seek, branch isolation, missing models and version/deletion races.
Advisor models use the same unfinished-game deletion protections as playing AIs.
Statistics combine otherwise equal agents across seeds without rewriting games.

Desktop/mobile browser checks exercise actual hints, a legal advised move,
replay/reload preservation, two independent agent opinions and German/wood
preference persistence. Existing full flows still verify gameplay, training,
model cleanup, tables and two/four-player tournament reports. Unit cases cover
model recommendation/manual choices, informative equal-name labels, mythological
name rerolls, language fallbacks and board-specific training presets.

Real isolated CPU training ran three iterations, published three loss/evaluation
points and retained two strict best-score improvements as selectable models.
A bounded longer run showed the compiled UI automatically adding a second graph
point while its worker remained running. Cancellation retained three completed
iterations. Smoke scores are workflow evidence, not competitive-strength results.
Graphs preserve history through resumption and leave skipped evaluations missing.

Compiled views were visually inspected in wood mode at desktop and 390px widths,
with independent numeric estimates, readable colors and no page overflow/errors.
German workshop status text preserves stable CSS identifiers and metrics use
comma decimal formatting. Screenshots include [agent opinions](screenshots/agent-evaluations.png),
[wood on mobile](screenshots/wood-theme-mobile.png), [training graphs](screenshots/training-progress.png)
and [actual running progress](screenshots/training-running.png).

Production startup upgraded schema 3 to 4 additively and serves v0.9.0 plus its
compiled assets and three training plans. All seven pre-existing games retain
exact IDs, versions and JSON against the pre-upgrade digest. All four existing
model-usage endpoints respond. A complete backup was taken before migration;
all training, analysis and screenshot data used separate temporary storage.

The v0.10 suite passes **313 Python tests**, **74 frontend unit tests** and
**26 real-API browser checks**. New setup checks train separate Ulf/Freydis CPU
models and verify selected IDs, names and runes across creation, sidebar, reload,
model dropdowns, workshop and recorded history on desktop/mobile. Agent budgets,
advisor, fixed-seed toggle/value, mode and board restore from the successful setup.
Cancelled/failed creation and removed/corrupt stored options have focused coverage.

The workshop browser flow resumes a real one-iteration job to a total of two,
retaining both graph points. API tests validate checkpoint availability and target
bounds, reject inference/corrupt sources, preserve the source checkpoint hash and
compare resumed CPU weights exactly with uninterrupted training. Dialog inspection
shows actual architecture/optimizer and distinguishes total from additional iterations.

Identity tests verify concurrent allocation, unique combinations beyond 24 entries,
retention after deletion, stable model identity across budgets and consistent
legacy tournament agent runes. Existing game JSON and opinion snapshots are not
rewritten. Compiled [desktop opponents](screenshots/identified-opponents.png),
[mobile header/runes](screenshots/cleaned-header-mobile.png) and
[resume dialog](screenshots/resume-dialog.png) were visually inspected without
page overflow or browser errors, including German identity preservation.

Production startup serves v0.10.0/schema 5 with compiled assets. All seven existing
games retain exact IDs, versions and JSON against the pre-upgrade digest; all four
model-usage endpoints respond. Eight current player/model identities have distinct
persistent runes. A complete backup preceded migration; new training and visual
fixtures stayed in isolated temporary storage.

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
