# Validation evidence

[Documentation index](README.md) · [Reproduce checks](#reproduce)

## Remote worker application compatibility (`0.26.0`)

The [0.26.0 validation record](releases/0.26.0.md#validation) records exact-version
and optional commit matching, administrator/CSRF boundaries, persisted overrides,
claim/heartbeat serialization, multi-job lease continuity, portable provenance,
real worker training/resume and English/German desktop/mobile controls.
See [remote worker upgrades](REMOTE_TRAINING.md#application-compatibility-and-upgrades)
for the distinction between ordinary server restarts and production updater preflight.

## Training reuse and self-play totals (`0.25.0`)

The [0.25.0 validation record](releases/0.25.0.md#validation) reports the complete
local checks, optional OpenVINO CPU coverage, real remote-worker training/resume,
58 desktop/mobile browser passes, actual counter persistence and hardware limits.
Job-local and model-lineage totals are described in the
[dashboard guide](TRAINING_DASHBOARD.md#recorded-self-play-totals).

## Optional OpenVINO inference (`0.23.0`)

The [OpenVINO validation record](validation/openvino-2026-10-23.md) records full
regressions, Python 3.12/3.14 numerical and training checks, CPU operation without OpenVINO installed, portable resume and explicit unavailable-hardware limits.

## Secure accounts and deployment (`0.22.0`)

The [2026-10-23 implementation and validation record](validation/security-deployment-2026-10-23.md)
describes accounts/RBAC, schema 8, installers/updater, complete API coverage, exact
checks and remaining target-host validation. The [0.22.0 release notes](releases/0.22.0.md)
record the release scope; older release evidence remains historical.

## Latest release evidence at a glance

Evidence below belongs to the specified release and scope, not to every platform
or a fresh rerun after a documentation edit. Release notes retain their original
claims unchanged.

| Evidence | Latest recorded scope |
| --- | --- |
| [0.17.0](releases/0.17.0.md#validation) | `make foundation check`: 478 backend tests passed, one CUDA case skipped; 137 frontend tests, formatting/type checks and production build passed |
| [0.17.0 CPU experiment](releases/0.17.0.md#performance-evidence) | Four-game batched smoke training with real updates/reload/legal model play; equal-work tiny CPU comparisons showed 0.98× and 0.65× speedups, **no CPU batching improvement** |
| [0.16.0](releases/0.16.0.md#validation) | 90 targeted backend and 68 frontend tests for editable resume, optimizer-state retention and all three neural wrappers; full browser suite not rerun for that milestone |
| [0.15.0](releases/0.15.0.md#validation) | Latest release-recorded full browser suite: 38 desktop/mobile passes, four intentional mobile duplicates skipped |
| Hardware/deployment limits | CUDA/MPS execution, GPU speedup, physical-device/browser-engine compatibility and target-host systemd/proxy operation remain unverified |

Functional tests prove data generation, real optimizer updates, checkpoint
restoration and legal play. They do not establish model strength, calibrated
winning probabilities or acceleration. See [Performance](PERFORMANCE.md) for units
and fair work comparisons; [Development](DEVELOPMENT.md) for current commands.

## Capability coverage

Tests use isolated temporary persistence on Linux/Python 3.12/Node 24 with the
hash-locked CPU environment. The following describes exercised capabilities;
exact counts and subset/full-suite distinctions belong to the release records.

| Capability | Evidence |
| --- | --- |
| Three engines | 61 source-specific tests: layouts, movement, captures, special squares, royal captures, Copenhagen shieldwalls/encirclement/forts, outcomes, serialization and replay |
| AI, models and training | Real baseline/alpha-beta/MCTS/neural full games in every variant, actual updates, tensor-only compatible imports, exact saved architecture and atomic model deletion |
| Exact resumption | Split CPU training matches uninterrupted weights exactly; an earlier best model survives weaker later iterations and moving/deleting its original directory |
| Optimization | Actual Optuna trials, persisted study resumption, evaluation-based objectives and exported best configuration |
| API and persistence | Real moves/replay, versioned deletion, recalculated statistics, fresh/fixed seeds, completed-game restart guards, hidden model references, consent fingerprints, rollback and in-flight request races |
| Worker lifecycle | Actual subprocess training/resumption/export/import/neural moves, optimization-study resumption, health requests during training, cancellation/exit/reaping and shutdown cleanup |
| Saved tournaments | Real isolated workers, balanced two/three/four-player round robins, participation-aware scoring, unfinished outcomes, durable partial results, stop/delete, model reference protection, legal replay and failure/shutdown cleanup |
| Browser unit flows | Vitest cases include tournament win matrices, complete setup/name restoration, editable reviewed resumption, independent analysis requests, localization, training plans, sorting/filtering and deletion races |
| Browser end to end | Actual desktop/mobile Chromium flows; see v0.15.0 above for the latest release-recorded full run and intentional skips |
| Quality/build | Ruff and Prettier formatting/lint, mypy, TypeScript checks and production Vite build |

Browser tests use real API services and cover Tablut interaction, undo/redo, rules,
history, profiles, all board sizes, AI playback and CPU training through registration,
compatible model selection and a neural move. They assert no horizontal mobile
overflow. Desktop and mobile additionally complete a real Brandubh AI game,
verify the disabled final board and archive outcome, and reopen the saved result.
Screenshots were captured from the running application.

## Historical validation by release

These retained observations describe the machine/data at each milestone, not the
current contents of a new installation. See the [release index](README.md#rulesets-release-history-and-historical-requirements)
for every original note.

### Records and personalization (`v0.5.0`)

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

### Saved tournaments (`v0.6.0`)

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

### Fresh games and manageable records (`v0.7.0`)

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

### Four-player tournaments and records (`v0.8.0`)

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

### Agent insight and training plans (`v0.9.0`)

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

### Setup and identities (`v0.10.0`)

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

### Model and training-data management (`v0.11.0`)

The v0.11 suite passes **327 Python tests**, **81 frontend unit tests** and
**26 real-API browser checks**, with four duplicate mobile worker checks skipped.
The expanded CPU browser workflow resumes a run, renames its registered model,
loads original training parameters into the form, confirms/cancels deletion,
removes training data, saves tournament provenance and uses the retained model
in a new game. It verifies unchanged identity/rune, existing history and original
recorded parameters. Decimal inputs accept valid recorded values; paths/resume
tokens are excluded from loaded inputs and tournament snapshots.

API checks cover active/stale worker status, source checkpoints used by active
resumed jobs, shared optimization studies, symlinked directories, cleanup errors
and concurrent job-list/deletion requests. Models remain playable after removing
both source and resumed training data. Rename checks preserve checkpoint hashes,
embedded names, game snapshots and chosen catalog names on reimport.
Saved tournaments retain network/training metadata and canonical model names
after catalog renaming/deletion, with legal replay still available.

Compiled [model cards](screenshots/model-workshop.png),
[mobile cards](screenshots/model-workshop-mobile.png) and
[saved tournament summaries](screenshots/model-tournament.png) were inspected at
desktop and 390px widths. English/German controls and wood appearance remain
usable without overflow or browser errors. Imported networks without training
metadata show an unknown training iteration rather than inferring it from the
registry's default version number.

Startup serves v0.11.0 and compiled assets on schema 5. A complete backup preceded
startup. All seven existing games, four model entries and eight rune assignments
match their pre-update digests. Model usage routes respond for all four entries.
Feature tests and screenshots use separate temporary data directories.

### Remote workers (`v0.12.0`)

The v0.12 suite passes **355 Python tests**, **88 frontend unit tests** and
**28 real-API browser checks**, with four duplicate mobile worker checks skipped.
New tests exercise worker credential hashing and scope, concurrent atomic claims,
retry identifiers, ordered progress, worker revocation, cancellation and expired
leases. Reopening the server against the same data preserves active leases and
progress. Explicit CUDA jobs queue without a server GPU and skip CPU-only workers.

The real HTTP integration launches a two-slot CLI worker with separate storage,
claims two jobs concurrently and performs actual CPU self-play/optimizer updates.
Both jobs upload models, learning history and full optimizer/replay checkpoints.
One resumes through the remote queue to a total of two iterations. After removing
the entire worker directory, the server still loads the imported model and makes
a legal neural move. These checks validate transfers and concurrency, not a cloud
provider, accelerator hardware or stronger model play.

Artifact tests reject incompatible networks, inference-only resume files, corrupt
and oversized data, incomplete final checkpoints and stale reports. Temporary
uploads are cleaned up. Cancelled completion acknowledgements remain idempotent;
final progress reports arrive before finish. Queued resume jobs own their copied
input even after the source job is deleted. CLI tests cover credentials, redirect
protection, private ledgers, safe directories, capacity, cancellation and orphan
recovery, including a supervisor restart or changed credential.

Desktop/mobile browser flows enroll a worker, queue two models, retain independent
charts, cancel queued work and reload saved progress. GPU capability data is
simulated for selection tests; actual remote training is CPU. Compiled
[worker cards](screenshots/remote-workers.png),
[mobile worker cards](screenshots/remote-workers-mobile.png) and
[remote progress](screenshots/remote-progress.png) use copies of actual CPU
integration outputs. English/German views, resume targets and masked enrollment
dialogs have no horizontal overflow or browser errors. Tokens are absent from
command text and browser storage. Terminal progress metrics follow server status.

Startup serves v0.12.0 and compiled assets on schema 6. The additive worker-table
migration preserves exact pre-update digests of all seven games, four model
entries and eight rune assignments. All four model-usage routes respond, and the
production worker collection remains empty: test enrollment/training uses isolated
data. A complete schema-5 backup was taken before migration. The Linux service
example and CUDA/MPS installation still require validation on their target host.

### Adjustable resumption and agent settings (`v0.13.0`)

Backend verification covers **369 cases**; **101 frontend unit tests** pass.
New checks restore actual SGD momentum buffers before applying learning-rate,
weight-decay and momentum overrides, preserve weights/replay/random state/history,
and continue with real optimizer updates. Local API tests prefill from effective
optimizer groups even when older metadata is missing or stale, and reject malformed
optimizer containers without an HTTP 500. The real HTTP worker integration trains
two jobs concurrently, resumes SGD with all three changed settings, and inspects
the uploaded full checkpoint's actual parameter groups.

Search checks verify independent configured depths in AI moves and current-player
estimates, profile-default/explicit-setting resolution, exact saved residual-block
counts, and a searched terminal win that disagrees with the raw neural value head.
The interface distinguishes completed/requested alpha-beta depth and preserves
historical `neural_value` labels while displaying new `neural_search` reports.
Completed-game restart rejection also covers earlier replay positions and legacy
records, without creating games or changing statistics.

All **30 real-API browser checks** pass, with four intentional duplicate mobile
worker checks skipped. The real training/resume flow edits all three SGD settings,
continues to iteration two, and reads back the effective saved values.
Desktop/mobile checks verify that a completed game has no restart control even
after undo, and that the API rejects a direct restart request.

Cold worker-process smoke checks allow 90 seconds for startup on shared runners
while retaining small training/search budgets and live health checks. The first
local full run passed 367 cases and hit two earlier 30-second startup timeouts;
both pass with the revised allowance. Exact CPU resumption comparisons complete
their fixed simulation budget rather than relying on a 0.1-second cutoff.

Desktop/mobile remote-worker cards were visually inspected in English/German:
panel content has consistent insets and text gaps, with no horizontal overflow
or browser errors. Startup serves v0.13.0 and the compiled client on schema 6.
All seven games, four model entries and eight rune assignments match their
pre-update digests; a complete data-directory backup was taken before restart.
Test training and workers use isolated temporary storage. No migration is needed.

### Battlefield win matrices (`v0.14.0`)

The full local check passes **369 backend cases** and **107 frontend unit tests**.
Six matrix cases cover directional wins with swapped sides, battlefield isolation,
draws/truncation, legacy two-player assignments, partial updates, repeated names,
canonical model/profile labels, rune precedence and English/German accessibility.

All **seven targeted desktop/mobile tournament browser checks** pass; one
duplicate mobile cancellation check is intentionally skipped. A real four-player
tournament plays 24 games across Brandubh/Tablut. Every cell is compared with
saved match outcomes and each row total with existing battlefield totals, then
checked again after reload. The mobile flow verifies keyboard scrolling, fixed
row headers and no page overflow. Both new matrix flows pass again after placing
the visible title outside the scroll area while retaining a semantic caption.

The actual [desktop](screenshots/tournament-win-matrix.png) and
[scrolled mobile](screenshots/tournament-win-matrix-mobile.png) views were visually
inspected. No scoring or persistence fields change; matrices are derived from
retained records. Storage remains schema 6.

Startup serves v0.14.0 and both compiled assets. All seven retained games, four
model entries and eight rune assignments match their pre-update digests against
a complete data-directory backup. Test tournaments remain in isolated storage.

### Background analytics and faithful playback (`v0.15.0`)

`make check` passes with **401 Python cases** and **126 frontend unit tests**,
including formatting, mypy, TypeScript and the built browser application. The
full real-API Chromium suite passes **38 desktop/mobile cases**, with four
intentional duplicated mobile skips.

Fifteen dedicated async API tests cover human moves and AI requests while other
searches are held, actual-move single-search evidence, two independent opinions,
progressive hints, advisor revision changes, hidden chances, stale polls,
deleted/branched positions, replay seeks, concurrent AI compare-and-swap and
restart preferences. Search tests verify completed-depth and throttled MCTS
callbacks use independent snapshots and match final diagnostics without a second
search. UI tests exercise delayed/rejected settings saves and recovery.

Browser coverage includes Copenhagen partial depth results while a human moves,
replacing an active advisor, retaining hints with chances hidden, persisted
English/German replay analytics, both AI opinions, fixed/custom game and tournament
presets, dialog drag/release versus outside press, captured kings, and missing
AI playback controls in completed records at both final and starting positions.

A SQLite backup was taken before upgrading this workspace's schema 5 database
to 7. Exact existing rows in games, players, jobs, tournaments, rune assignments
and player analysis remain equal: seven games, four profiles and eight existing
runes were preserved. All model file hashes remain unchanged; integrity checks
pass. Production startup on port 8000 serves the built assets, reports application
version 0.15.0/schema 7 and retains seven games/four registered models.

## Documentation maintenance (2026-10-23)

The documentation-only revision checks all 42 Markdown documents for local links,
heading anchors, images and reachability from the documentation index. Six
standalone link-checker regression cases pass, including broken anchors/images,
fenced examples, reference links and orphaned documents. All earlier heading
anchors in changed documents remain available; the 17 release-note files and
original requirements body are preserved.

Twenty-three current-guide shell blocks pass `bash -n`; CLI help and example
training configurations match the current interfaces. The documented neural
alpha-beta API example creates a game and makes a legal move using untrained
fixture weights. Four focused existing wrapper gameplay/analysis cases pass;
seven other cases in that file are deselected. `make lint`, checker Ruff checks
and `git diff --check` pass. No training job, full application suite or full
browser suite was rerun for this documentation revision. Prior release evidence
above retains its original scope.

## Reproduce

```sh
make install
make docs
make check
cd frontend
npx playwright install chromium
npm run test:e2e
```

If installing browsers in a custom cache, set `PLAYWRIGHT_BROWSERS_PATH` to that
same directory for both installation and execution. A previous machine's retained
cache does not establish that a new environment has browser binaries.
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

Current guest separation evidence is in [Phase 4 validation](validation/online-phase4.md),
including production-CSP Chromium, real IndexedDB and account/online boundaries.
