# Architecture

[Documentation index](docs/README.md) · [Development](docs/DEVELOPMENT.md)

This describes current boundaries and invariants. Schema introduction dates and
release-specific delivery details live in [Deployment](docs/DEPLOYMENT.md#data-and-configuration)
and the [historical release index](docs/README.md#rulesets-release-history-and-historical-requirements).

## Decisions

Python 3.11+ owns the deterministic engine, search, persistence and training. FastAPI
exposes server-authoritative JSON sessions; React, TypeScript and Vite provide one
responsive browser client for desktop and phones. SQLite keeps ordinary Linux and
Proxmox LXC installations simple. PyTorch provides real CPU/CUDA policy/value learning;
Optuna manages persistent search studies. A study ranks trials by the score of
their best newly evaluated candidate checkpoint, supports a selectable fixed evaluation opponent
(Shield bearer, Shield maiden, Jarl or a pinned registered neural model) or an
optional weighted multi-opponent aggregate, records a held-out verification of
the winning checkpoint, and stores a canonical identity that rejects
incompatible resumptions. Docker is not required.

Account and online positions/results are server-authoritative. Every position and
move has a serializable representation, shared by interactive play, replay, AI and
training.
Pieces are `.`, `A`, `D`, `K`; moves use zero-based flattened `source` and `target`
indices. Turns and results use `attackers`, `defenders`, and `draw` for draws.

## Components

| Module | Responsibility and boundary |
| --- | --- |
| [engine.py](hnefatafl/engine.py) | Immutable state, legal moves, captures, terminal rules; no HTTP/database/tensor dependency |
| [symmetry.py](hnefatafl/symmetry.py) | Pure D4 geometry, full-history transforms, board/full-state stabilizers and typed canonical keys; no search or neural dependency |
| [ai.py](hnefatafl/ai.py) | Bounded heuristic/alpha-beta/MCTS with bounded transposition tables and full-state symmetry pruning, neural wrappers, cooperative MCTS contexts, seeded headless tournaments |
| [shield_maiden.py](hnefatafl/shield_maiden.py) | Versioned one-ply royal geometry, escape routes and piece-safety evaluation; reused by the Jarl alpha-beta and Seer MCTS model-free paths |
| [heuristic_benchmark.py](hnefatafl/heuristic_benchmark.py) | Paired heuristic strength, matched-position timing and actual training-evaluation runtime |
| [models.py](hnefatafl/models.py) | Ruleset-specific policy/value network, safe checkpoints and a content-addressed model registry that is idempotent by inference-weight fingerprint |
| [training.py](hnefatafl/training.py), [training_identity.py](hnefatafl/training_identity.py), [training_presets.py](hnefatafl/training_presets.py) | Learning, replay, optimizer state, evaluation, resume, Optuna and validated budgets |
| [training_augmentation.py](hnefatafl/training_augmentation.py), [symmetry_benchmark.py](hnefatafl/symmetry_benchmark.py) | Tensor-level D4 transforms of replay examples (input, action ids, legal mask), cached action permutations, minibatch collation and evidence tooling; no engine, search or inference dependency |
| [self_play.py](hnefatafl/self_play.py), [inference.py](hnefatafl/inference.py) | Concurrent game scheduling, shared legal-policy inference batches and activity metrics |
| [benchmark.py](hnefatafl/benchmark.py), [alpha_beta_benchmark.py](hnefatafl/alpha_beta_benchmark.py), [cli.py](hnefatafl/cli.py) | Same-device performance comparisons, alpha-beta optimization ablations and headless command dispatch |
| [storage.py](hnefatafl/storage.py), [identities.py](hnefatafl/identities.py) | Transactional SQLite records, compare-and-swap moves, normalized identities and runes |
| [profile_api.py](hnefatafl/profile_api.py), [profile_migrations.py](hnefatafl/profile_migrations.py) | Owned optional profiles, automatic publication with legacy privacy protection, private birth dates and additive schema-15 fields |
| [online_core.py](hnefatafl/online_core.py), [online_api.py](hnefatafl/online_api.py), [online_migrations.py](hnefatafl/online_migrations.py) | Durable lifecycle/engine integration, authenticated safe adapters, atomic seats/moves/deadlines/results and side-swapped two-game matches |
| [rating.py](hnefatafl/rating.py), [online_ratings.py](hnefatafl/online_ratings.py) | Pure versioned Elo policy and deterministic replay; durable rating events/applications, transactional rebuilds and leaderboard queries |
| [progression.py](hnefatafl/progression.py), [progression_api.py](hnefatafl/progression_api.py) | Code-owned portrait/achievement catalogs (schema 17), idempotent awards derived only from authoritative records, permanent entitlements and CSRF-protected portrait selection |
| [server.py](hnefatafl/server.py), [analysis_service.py](hnefatafl/analysis_service.py) | Owned API paths, catalog/job coordination, bounded background analysis and stale-result guards |
| [tournaments.py](hnefatafl/tournaments.py), [tournament_statistics.py](hnefatafl/tournament_statistics.py), [openings.py](hnefatafl/openings.py) | Separate tournament lifecycle, incremental role statistics and reproducible legal openings |
| [job_archives.py](hnefatafl/job_archives.py), [job_studies.py](hnefatafl/job_studies.py) | Bounded TAR.GZ job migration with legacy ZIP import, staged publication and contained Optuna study rebasing |
| [remote_training.py](hnefatafl/remote_training.py), [remote_worker.py](hnefatafl/remote_worker.py) | Server leases/artifact ingestion and outbound worker subprocess supervision |
| [frontend/src](frontend/src/) | React board/setup, replay, records, tournament, model/training controls, shared notifications, localization and appearance |

Long-running training runs in separate worker processes, never in the API request
loop. Retained files (SQLite, checkpoints, model metadata and worker logs) are
separate from live processes, which must restart after machine restoration.

## Process ownership and tournaments

Browser tournaments also run in a separate process. SQLite stores a
tournament collection containing opponent/configuration snapshots, lifecycle status,
progress and completed match records. The worker saves after each finished match;
stopping a run preserves that subset. Replay reconstructs positions through the
pure engine and does not require the original model weights. Tournament matches
remain separate from ordinary game statistics. Catalog locking protects the model
references of active tournaments until the worker exits.

Tournament schedules support two to four opponents. Every unordered pair plays
the configured even game count on each battlefield, alternating sides. Progress
uses the number of pairings, and standings update only the two participating
opponents. New matches retain explicit attacker and defender indexes; the browser
can still infer the defender in earlier two-player records without migrating them.
Battlefield win matrices are browser projections of those saved matches: rows
win against columns, indexed by tournament seat rather than model/name identity.
They exclude draws and unfinished games and use existing runes on the diagonal;
no additional aggregate or storage migration is required.

A tournament carries an explicit `mode`: `round_robin` (the default when the
field is absent, preserving every earlier record) or `fixed_roles`. A fixed-role
comparison has exactly two independently configured participants and one battlefield.
It uses the same validated profiles, heuristic agents and neural wrappers as the
round robin. Seat 0 always attacks and seat 1 always defends, so its total is the
plain game count and odd counts are valid. Each seat retains its own effective
search parameters, even when both reference the same model weights. The collection
endpoint projects the summary in SQLite, so listing saved tournaments never decodes
retained match histories.

`tournament_statistics.py` updates overall, per-role and per-battlefield counters
incrementally. Win rates and draw-adjusted score rates divide by wins + losses +
draws; unfinished games remain separate. Missing historical role counters are
reconstructed from complete retained match records only on full-detail/export reads.
Incomplete histories leave the unavailable side counters absent. No migration or
historical rewrite is needed.

Optional `randomize_start_positions` defaults to false. `openings.py` uses versioned
SHA-256 seed derivation and model-independent quiet legal playouts from the standard
board, retaining immutable engine states and their complete repetition histories.
Each round-robin return game reuses the exact same opening as its first leg; a new
pair of games receives a new opening. Fixed-role generation depends on seat order
and game index, never agent identity/settings, so swapping agents reproduces the
same opening sequence. Search seeds keep their prior derivation independently.
Complete move lists contain the generated prefix, identified by `opening_plies`,
`opening_seed` and `opening_version`; replay traverses it through the engine.
The game move cap, decision times and search diagnostics count agent moves only.
See [tournaments](docs/TOURNAMENTS.md#randomized-legal-openings) for the bounded
policy, duplicate fallback and reproducibility limits.

## Rules

One engine applies configurable board sizes, initial arrangements, restricted
squares, first player, king capture and escape rules. Variant-specific mechanisms
are explicit hooks, including Copenhagen shield walls, encirclement and edge forts.
The exact linked market Tablut rules take precedence over other historical Tablut
reconstructions. Dedicated rule documents record interpretation and source access.

## Online game core

Phase 1 extends existing auth with [registration and recovery](docs/REGISTRATION.md).
`account_policy.py` shares validation, `account_migrations.py` owns schema-9
preflight/token/mail state, `account_api.py` declares the public account flows and
`registration.py` coordinates transactional tokens, durable limits and leased SMTP
delivery through `account_mail.py`. Lifespan supervises delivery independently of
request lifetime. Phase 2 reuses its verified-account admission gate.

[Phase 0](docs/designs/online-phase0.md) records the proposed online lifecycle,
deadline, result and permission boundaries, backed by a
[source assessment](docs/designs/online-phase0-assessment.md). The pure
`lifecycle.py` contract is integrated by `online_core.py` for dedicated online records.
The [browser-offline spike](spikes/browser_offline/README.md) stages an isolated
runtime, not a production anonymous mode or authentication exception.

Schema 10 separates online matches/games from mutable local histories. SQLite
`BEGIN IMMEDIATE` serializes session/account rechecks, time sampled after locking,
seat assignment, legal engine actions, expected versions, deadlines and append-only
result revisions. Private administrative notes never enter game projections.
Effective eligible results derive online counts; void revisions withdraw prior
contributions. Random sides resolve once at join. Simultaneous two-game matches
activate both legs atomically; sequential matches activate the side-swapped return
in the first game's completion transaction. Each game retains its own version/result.

Lifespan reconciles one bounded expiry batch and supervises periodic passes. Requests
and sweeps share the same expiry transaction/gate, including deployment freeze.
Timeout finalization is enabled by default, with an audited administrator pause
and an environment override for incidents; overdue play stays rejected without deadline extension.
Schema 13 adds immutable per-move/total clock selection, a seconds increment, and
per-player total balances in integer microseconds. Only the moving player spends
time; a timely move credits their increment before starting the opponent’s clock.
Per-move clocks reset to the base limit plus increment after each accepted move.
The migration enables only the untouched legacy timeout pause; administrator and
restore pauses survive upgrade. Current
sessions are reloaded under the write lock through trusted session context, closing
the gap after middleware authentication. Admission requires active/verified accounts;
existing admitted seats retain play after verification changes while active/session-valid.

The [operations guide](docs/ONLINE.md) documents policy approval, schema/restore
boundaries and safe projections. The [online UI](docs/ONLINE_PLAY.md) owns separate
account-keyed lobby/game state, abortable/coalesced polling and viewer-local replay.
The presentation-only Board receives no online assistance. Auth generation guards
prevent delayed session responses restoring old state after logout. Admission UX
uses the authenticated availability projection backed by `online_eligible`; writes
remain authoritative. [Phase 4 handoff](docs/designs/online-phase4-handoff.md) records
remaining public/rated/operational decisions.
All online endpoints are authenticated, with CSRF/Origin/JSON/body protections.
Legacy analysis/AI/shared-cursor/export adapters reject online IDs, including admins;
no online assistance or WebSocket endpoint is provided. The engine remains shared.

Creation admission also checks per-account waiting and rolling creation quotas in
the game transaction, using indexed persisted rows rather than process-local
limiter state. Bounded aggregate denial counters and rate-limited sanitized logs
survive restart; administrator-only monitoring reports these and database/WAL
growth. Optional schema-10 monitoring state is initialized on compatible restore
without changing authoritative game rows or resetting the live creation budget.

Schema 12 adds the Phase 6 rating pool. `rating.py` owns the pure Elo formula and
deterministic replay; `online_ratings.py` persists one event per applied or
withdrawn Rated game with a stable sequence, per-player applications and running
`player_ratings`. Result revision, event, applications and running ratings commit
in the same write transaction. A void withdraws the event and rebuilds the pool
from applied events in sequence order inside one transaction, so standings are
never partially visible. `online_game_start_ratings` captures immutable per-player
start Elo at activation, independently of pool rebuilds. All newly created games
are rated; historical casual games keep their flag. Leaderboard reads require a
session and return one Elo-ordered list (provisional players carry a badge). See
[Rated games](docs/RATINGS.md).

Completed online games reuse the local analysis engine through an online-scoped
adapter: `server.py` reconstructs a terminal game from `online_games`/`online_moves`,
`analysis_service.py` and `storage.py` accept the online identity, and the advisor
reports persist in the existing `advisor_analysis`/`game_analytics` tables. Ongoing
online games are rejected by the backend and never expose assistance.

## Training execution and inference

Self-play uses sequential execution or cooperatively interleaves independent games
through one shared model. `cooperative.py` owns that scheduler for every path: each
game is a generator that yields its own pending leaf and receives exactly its own
policy/value result, so no simulation advances on another game's inference. Optional
`search_workers` (default 0) adds persistent spawned CPU actors that each run the
same scheduler over many game contexts. It is a cap, never a target: the pool creates
processes on demand, only as far as a phase has concurrent game slots, and keeps them
for later phases, so a pool configured for 32 workers starts two when the phase can
only run two games. `self_play_concurrency` and `evaluation_concurrency` are hard upper
bounds on active games across the whole pool, and each worker receives a static
round-robin share of the game indices plus a capacity from a quotient/remainder split,
so the capacities sum to exactly the requested limit and idle workers are never
started. An explicit `sequential` self-play or evaluation mode overrides the pool
entirely, so a configuration with both phases sequential starts no worker processes at
all.

CPU inference uses frozen actor snapshots evaluated inside the actor; CUDA/OpenVINO
models, tensors, streams and caches stay in the parent, which batches the outstanding
requests of the whole pool into one forward pass and routes each result back by
explicit request identity. Evaluation backends the pool creates are registered on an
exit stack before they are refreshed, so a failed creation or refresh closes every
backend that phase already owns and never replaces the original error.

Each actor owns one inference cache per model identity for as long as a bound weight
generation lives, so it keeps that cache across the slices it is granted and starts a
fresh one on rebind. Cache occupancy is reported as the change since that generation's
previous report and the parent adds the actors' changes, which is why a worker's
entries are never counted twice across slices; `inference_cache_peak_entries` is the
sum of the workers' own high-water marks, an upper bound on simultaneous occupancy.
Worker startup is exception-safe per attempt: the pipe endpoints of an attempt are
closed and any partially started process is stopped before the error propagates, and a
failed pool closes every worker it already started and refuses further use.

Actors also report progress. A game context ticks the same per-position counters the
in-process path ticks, but into an actor-local accumulator that batches them into
bounded progress messages instead of one message per simulation; the parent applies
each batch as a delta and drops a message whose per-worker sequence it has already
seen, so a completion contributes only its own completion counter and no work is
counted twice. Self-play ticks once per played move and keeps that per-move cadence;
evaluation ticks once per neural batch, so it reports on a nominal one-second
wall-clock interval (with a work cap and immediate lifecycle flushes) rather than one
message per forward pass. The
same messages carry the actor's live active-game count, which the parent sums across
workers; self-play announces the first active contexts before its first expensive move. `train()` forwards its `TrainingMonitor` into both parallel entry points, so
a worker pool advances the dashboard's games, positions and simulations while long
games are still running rather than only when they finish.

Replay commits
remain in game-index order, the pool keeps only a completion count rather than a copy
of every result, and it hands out new games only while the started-but-uncommitted
count stays inside the ordered replay window. Failure or cancellation closes the pool.
CUDA auto selects batching; PyTorch CPU/MPS auto stays sequential. Explicit
OpenVINO auto selects batching with CPU optimization. An optional frozen
TorchScript/OpenVINO FP32 graph is refreshed once before each self-play iteration;
bounded dynamic batches reuse one compiled graph. Shared encoding/legal-policy
processing and PyTorch evaluation/checkpoints remain authoritative. Device probing
is crash-isolated and remote matching requires operational reported devices. See
[OpenVINO](docs/OPENVINO.md) for lifecycle, setup and hardware limitations.
This changes scheduling, not engine rules or gameplay/tournament search behavior.
Optimization batches sample replay separately from inference batches. See
[Performance](docs/PERFORMANCE.md) for measured work, timing boundaries and the
CPU evidence that fewer forward passes did not imply a speedup.

Training schedules exactly one selected evaluator (Shield bearer, Jarl or Champion)
at interval multiples and the final optimized iteration, independently of checkpoint
writes. `training_evaluation.py` owns effective protocol identities, immutable
benchmark snapshots and paired conservative champion promotion. Full tensor-format-1
checkpoints add a versioned training selection state while preserving legacy baseline
fields. Candidate, resumable latest state, fixed benchmark records and champion are
distinct. Evaluation retains a sequential reference and can interleave games with separate
candidate/incumbent inference queues. CUDA auto batches in FP32; CPU auto remains
sequential. Opt-in evaluation through the training OpenVINO backend refreshes each
post-optimization snapshot and starts a separate benchmark identity. Compact stdout events
exclude history; atomic JSON/checkpoints retain it for local and remote polling.

`training_optimizers.py` owns the small train/inference lifecycle and state-preserving
base-rate interface for the optional official Schedule-Free AdamW optimizer.
Optimization restores averaged weights in a `finally` block before self-play,
evaluation, Champion snapshots, inference-backend refresh and checkpoint writes.
Optimizer sequences/counters remain in the safe full optimizer state, separately
from adaptation state; ordinary optimizers keep their existing update paths.
An interrupted upstream mode transition invalidates the live optimizer/model pair
and blocks new checkpoint writes; its original error and previous valid checkpoint
are preserved without adding fields to the serialized optimizer state.
Workers advertise execution-tested Schedule-Free support independently of version
overrides. See [Schedule-Free AdamW](docs/TRAINING.md#schedule-free-adamw).

`training_monitoring.py` owns phase-independent progress: the training process
keeps a `TrainingMonitor` whose hot path only accumulates plain counters, while a
daemon thread republishes a bounded `heartbeat.json` (phase, phase/iteration
durations, time since meaningful progress, phase counters) about once per second.
Retained `progress.json` is still rewritten only on real progress events, and a
reader overlays a heartbeat only when its publication revision matches. Worker
liveness, computational progress and publication therefore stay independent, and
a long self-play game, optimization epoch or evaluation match cannot freeze
timers or counters. Durations are monotonic and publication is bounded; the
dashboard anchors them to a local ticker instead of trusting a worker clock. See
[the training dashboard](docs/TRAINING_DASHBOARD.md#live-monitoring).

Exact-input inference reuse is opt-in (`inference_cache_mode: "exact"`, default
`"off"`). One bounded LRU belongs to one self-play generation and is discarded
before optimization; sequential and cooperative search share its legal-policy
implementation. Keys retain ruleset, exact board orientation and turn. Terminal
adjudication precedes lookup, histories remain in independent search states, and
only compact immutable legal probabilities/value are retained. Returned policies
are independent dictionaries. Weight version counters, parameter replacement,
precision/device changes and explicit backend generation tokens additionally
invalidate a reused cache. Backend refresh increments its generation after success.
No global cache, stochastic root noise or dense/GPU logits are retained.

`Symmetry` utilities act on coordinates and the entire repetition history in one
common orientation. Board-only `BoardKey` and full `StateKey` are different types;
full keys retain all metadata and ordered history, using history to resolve
symmetric-board ties. Saved states and move formats are unchanged. Existing models
need not be rotation invariant: none of these canonical keys is used for neural
inference, pruning, transpositions or replay.

Training augmentation is a separate (`training_symmetry_mode`, default `random_d4`
for new jobs; jobs and checkpoints that predate the setting keep their historical
`off`) optimization-stage transform. `training_augmentation.py` reuses
`coordinate_map` to build `[8, n²]` square and `[8, n⁴]` action permutations. For
each minibatch row it permutes all six planes, maps every policy/legal action id,
keeps probabilities paired and leaves the defender-relative value unchanged. It
runs on host tensors before the one device transfer and draws from a dedicated
RNG saved as `augmentation_rng` in full checkpoints. Self-play, MCTS, evaluation,
replay storage and the exact-input cache never see transformed positions.
Augmentation encourages but does not guarantee equivariance, so independent
neural predictions remain unmerged.

## Model scope and trust

Models are ruleset-specific, allowing small networks and straightforward compatibility
checks. Safe tensor-only loading and validated architecture metadata avoid arbitrary
Python object deserialization. Functional training tests establish that the pipeline
works; model strength requires separate, sufficiently sized evaluation tournaments.
CUDA support does not establish that CUDA was tested on this development host.

## History and presentation configuration

AI record identity is a deterministic digest of normalized effective parameters,
including algorithm, relevant budgets, sampling settings and model identity,
while excluding seed-only differences. New AI participants
store resolved parameters and name snapshots. Legacy explicit-algorithm
sessions retain the defaults their original engine used; profile defaults are not
retroactively applied. Variant statistics expose configuration metadata for inspection.

Retained player tombstones and archived model metadata preserve historical identities.
Local-player creation/deletion paths are removed; saved IDs, names and runes stay
readable. New human seats use account IDs and one stable account rune. Deleting an available model removes its registry
copy and entry, retaining historical labels and original training outputs. Catalog
reference creation and deletion are serialized in the supported single API process.

`account_policy.py` resolves public human identities from `accounts.id`, applying
automatic publication with legacy-private exceptions and canonical username attribution. New account game
JSON stores the username and account ID, not the mutable profile name. Full-game
lists batch the trusted owner/account join once per request; history summaries
use SQL joins and the same pure naming policy without decoding whole game blobs.
Only human IDs matching the persisted single-player owner are trusted; online
seats use their database account IDs. No identity is inferred from a saved name.
Linked legacy snapshots resolve dynamically; unlinked humans and AI/model/
tournament snapshots stay historical. Missing linked accounts never resurrect
saved private names. Profile edits write no game rows. The schema-15 transition changes account data only,
so versions, optimistic move guards and statistical identity stay
unchanged. See [profile compatibility](docs/PROFILE.md#names-in-games-and-history).

Appearance is browser-local state, separate from game configuration. A typed catalog
defines color modes, board palettes and token sets; shared token rendering is used
for actual pieces and previews. The provider resolves device color preferences and
persists choices. New artwork and palettes can be added without changing game rules
or persistence. See [Personalization](docs/PERSONALIZATION.md).

Ordinary new games store a server-generated base seed; actual decision seeds combine
the base with the ply. Explicit seeds remain available to authorized training and
tournament workflows, while account creation uses server seeds only. Restart of a
current-policy unfinished game creates a fresh seed; historical records retain their
original read/play seed semantics.
Game deletion uses an expected version and removes the source record; statistics
are derived from retained records, so all counts/rates update without cached totals.
Restart is restricted to unfinished records, using the retained final outcome
independently of the current replay cursor. Both UI and API enforce this rule.

Model deletion previews every unfinished reference on the server rather than only
the browser's current game. Discarding those games requires an explicit opt-in and
a matching usage fingerprint. SQL and registry failure handling preserve references
if deletion fails; completed histories and training checkpoints remain. App-level
deletion callbacks clear matching current-game pointers and reject late AI responses.

Hall filters and counts run on authorized server queries without rewriting stored
results. Selected canonical review is independent of filtering and pagination.
AI parameter details remain inspectable in recorded games; account setup exposes
the four named presets to every player, plus an administrator-only custom
opponent that reuses the tournament participant configuration. New browser preferences default to carved wood and walnut; valid saved
preferences remain authoritative.

## Background analysis

Per-game analytics preferences and revisioned advisor reports are separate from
position-scoped player assessments. Playing-agent reports remain
immutable per role. The moving AI's actual search supplies its estimate; other
agents and advisors use a bounded background executor independent of gameplay
requests. Completed alpha-beta depths and MCTS checkpoints publish progressive
advisor hints. Polling keys include the game position and settings revision;
analysis never gates board input or AI playback. Late work can save against a
retained move prefix without changing gameplay versions, while deleted games,
restarted/removed branches and superseded advisor revisions reject stale writes.
Explicit replay advisor analysis is kept separate from original player evidence.
Advisor models use the same catalog reference protections as playing models.
See [Analysis](docs/ANALYSIS.md) for sources, settings and probability interpretation.

English/German source-key catalogs localize presentation while identities and
stored game/configuration values remain unchanged. Browser preferences select
language, last successful game mode and independent color/token/board themes.
The wood color mode changes UI surfaces rather than engine state.

## Identity, setup and retained learning state

An atomic, non-recycling rune catalog supplies stable entity identities.
Player UUIDs and model IDs reserve distinct runes; ordinary AI configurations
exclude seed from identity. Tournaments display the underlying agent rune; older
seat-rune reservations remain stored. Older response snapshots gain runes without
rewriting game JSON or checkpoint metadata. See [Identities](docs/IDENTITIES.md).

Account setup retains battlefield, permitted mode, side and four preset IDs, plus
an administrator-only custom opponent. The server binds human seats to the
account and resolves preset budgets itself; a custom configuration is validated
through the same `resolve_ai_config` path as a tournament participant, stored
with the game and re-derived on every move, reload and restart. Creation and
restart recheck current sessions/roles in a write transaction. Pass-and-play,
local-player CRUD and non-administrator custom/neural account-game creation are
rejected. Historical player IDs, modes and effective configurations remain
read/play shims, without supporting legacy result reconstruction or cloning old
setups via restart.
Model choices in authorized advisors/tournaments retain canonical names/IDs, and
saved model-name snapshots identify earlier games whose supplied aliases were stale. Training
resumption uses an explicit job checkpoint metadata route and server-derived
configuration, then creates a new worker/output directory for the chosen total
iteration target. The UI never reconstructs a resume request from arbitrary paths.
Resume metadata reads effective learning rate, weight decay and SGD momentum from
saved optimizer parameter groups. Reviewed overrides apply after optimizer-state
restoration on local and remote workers; accumulated buffers remain intact.

Model renames atomically update registry presentation metadata without touching
content-addressed checkpoint bytes, IDs, runes or historical name snapshots.
Training-data deletion removes an inactive job's owned directory and database
record separately from inference model copies. Job lifecycle locking prevents
late worker reconciliation from recreating deleted records, and retained shared
optimization studies are protected while referenced. Tournament model summaries
use path-free saved metadata rather than depending on retained training folders.

Tuning identity version 2 covers source model/full-state content, fixed workload,
seeds, numerical/search settings, fixed evaluation references/protocol and scoring,
search space and sampler/pruner. Source hashing is generation-cached at creation,
not part of polling. API preflight and worker validation reject incompatible or
ambiguous legacy history without relabelling trials. New candidate, inherited
benchmark, overall selection and Optuna objective remain separate. Each trial owns
full `last.pt` and selected `objective.pt` checkpoints without permanent registry
publication. Ordinary training registration is unchanged. Compatible neural
opponents may have different hidden architectures and use independent inference
models/queues. Optimization results are durable before optional cancellable
verification; verification status cannot erase the winner.

Schema-3 TAR.GZ archives separate small metadata from shared bounded JSON chunks;
serialization/compression and import validation run outside lifecycle/catalog locks.
Inactive files are pinned for consistent snapshots without changing training
persistence. Only referenced models are pinned after unlocked reference discovery;
an immutable catalog-index pin distinguishes earlier deletions from races, which
fail with an actionable retry instead of omitting models. Cross-filesystem source
hard links survive job deletion without retaining one descriptor per artifact;
file copies also run outside locks. Archive admission precedes
multipart parsing and returns 409 when busy, retaining capacity through cleanup
and streaming. Imported file reports supply bounded cached navigator summaries
while distinct database histories survive re-export. Schemas 1/2 ZIP imports
retain their guards and are checked against complete historical applications.
Import responses retain the full-job API default. The workshop explicitly requests
`?summary=true` and uses the existing deduplicated detail loader and localized
history-loading state. Navigator projections preserve loaded nested analysis and
history while updating monitoring fields; retained model IDs remain searchable.
Identity fields are reserved before optional imported summary metadata. Trial/run
changes invalidate only their owned reports, and model attributes never merge
across different IDs. The existing detail loader coalesces a bounded retry when
a response belongs to a former identity. Archive log/diagnostic redaction uses a
stateful bounded scanner across read boundaries, including quoted credentials;
arbitrary diagnostic data remains potentially sensitive.
The format-2 study layout still consolidates contributing sibling UUID jobs and a portable
source checkpoint into a fresh owning job on import. All paths, checksums, tensor
payloads and SQLite schema are validated before publication; format-1 import and
historical rankings remain supported. Lifecycle/catalog lock ordering prevents
concurrent launch/deletion of a fixed model. Running dependencies block deletion;
inactive studies with deleted dependencies fail explicit preflight. Dashboard
trial pagination and bounded existing chart components use Optuna objective values;
frequent summary polls exclude trial arrays and retained diagnostics.

## Remote training

Worker credentials/status live in `training_workers`; job JSON retains
queue targets, scoped expiring leases, ordered progress and imported artifacts.
`remote_training.py` coordinates authenticated outbound pull workers, independently
of the local worker-process limit. `remote_worker.py` supervises separate training
processes per slot and transfers reports/checkpoints/models through the API.
Training and engine implementations remain shared with local runs.

The trusted administrator enrolls a machine and receives a one-time bearer token;
the database stores its hash. Worker APIs require that token and job-specific
leases. Public responses strip lease secrets and structured worker file references;
diagnostic log text is retained as a bounded tail.
Claims are idempotent, progress is ordered and stale leases cannot publish results.
New assignments require exact installed application versions and matching known
immutable VCS revisions, independently of protocol and per-job hardware/inference
checks. Compatibility is derived from the current process identity and latest
heartbeat, never persisted as a boolean. One job lock serializes reports,
administrator overrides and claims. Per-worker overrides and actor/time history
live in existing worker JSON; only authenticated administrator sessions with CSRF
can change them. Missing application identity remains ineligible. Job `software`
snapshots preserve actual claim-time identities and override use, including in
portable job archives. Existing leases remain independent of application checks
across server version changes; ownership, expiry and revocation still apply.
The server validates and imports tensor-only artifacts into owned storage rather
than trusting remote model IDs or paths. Remote resume jobs copy their input
checkpoint before entering the queue, so source cleanup cannot break them.

Remote jobs continue across server restarts if their lease can still be renewed.
Expired leases retain uploaded state and require explicit resumption; they are
not automatically dispatched again. Queued cancellation, graceful active-job
cancellation and credential revocation remain distinct from deleting retained
training data. The browser polls the same persisted progress and charts for both
execution targets. Worker tokens authenticate only scoped machine routes and are
bound to an active enrolling administrator. Browser sessions protect the rest of
the application. See [Remote training](docs/REMOTE_TRAINING.md) and
[Authentication and RBAC](docs/SECURITY.md).

## Training workshop presentation

`Training.tsx` owns asynchronous mutations, shared form state and revision guards.
`TrainingCreationPanel` reuses the existing fields/target/evaluation controls.
Every training parameter carries a help button whose description lives in the
single `trainingTooltips` registry keyed by parameter identity, so the creation
form and the resume dialog describe shared settings identically and a coverage
test fails when a rendered field — editable, or an immutable checkpoint value in
the resume summary — has no copy. A description taller than the visual viewport
turns into a focusable, scrollable region (with the full text still named on the
button) instead of a clipped non-interactive tooltip.
`TrainingJobNavigator` owns local search, filtering and sorting;
`TrainingJobDashboard` owns tabs and iteration range without resetting on polling.
`TrainingAnalysisViews` composes Overview, Learning, Evaluation and Details;
`TrainingPerformance` groups computational metrics. `TrainingMetricChart` is a
small dependency-free responsive SVG renderer used by `TrainingHistoryChart`
and the performance view. `trainingDashboardData` normalizes optional legacy
metrics and separates effective evaluation contexts/incumbents. Large tables
and raw diagnostics mount on disclosure. Shared allowlisted configuration reuse
opens an editable fresh-job form for models and jobs; that allowlist is the single
vocabulary shared with the API through `frontend/src/trainingContract.json`, which
both test suites assert, and a refused field is named back to the form.
`training.py` starts its monitor before checkpoint loading, model construction and
inference setup, so initialization publishes its own stages with the heartbeat
running. Each run publishes a `run` identity beside its revision, so a heartbeat
can only refresh the exact run or trial that produced it, and a remote report
carries the child's own publication age next to the supervisor's, so a connected
supervisor forwarding an unchanged snapshot is never read as a live child. A
worker that the server has confirmed merges partial snapshots sends only changed
keys, so an unchanged history, metrics or log tail never crosses the network
again. `game_statistics.py` records
versioned job-local/baseline/cumulative self-play counters in existing JSON and
checkpoint metadata; immutable selection snapshots retain their own totals.
Tensor format and database schema remain unchanged. See the
[dashboard guide](docs/TRAINING_DASHBOARD.md).

## Alpha-beta search

`choose_move` keeps the public contract: immutable `State` in, one legal `Move`
out, with a monotonic deadline and a legal fallback. Iterative deepening searches
alternating defender-maximizing and attacker-minimizing nodes over the
defender-perspective Shield maiden evaluator. A custom alpha-beta or MCTS
configuration may select the generic Shield bearer static evaluation instead
with the `heuristic` field; the `baseline` algorithm is always Shield bearer and
neural search uses its model value head. A search owns one
`_TranspositionTable` for its lifetime; entries store searched depth,
exact/lower/upper bounds, a preferred move, a generation and the canonical
orientation that produced the key. Replacement prefers deeper entries and exact
bounds within a bounded capacity, and keys carry the complete repetition-aware
state, so identical boards with different histories never alias. Bounds tighten
the window, cut off when established and supply the preferred move; an interrupted
search never publishes a partial exact result. A probe whose stored orientation
differs from the query's canonical orientation is counted as
`transposition_cross_orientation_hits`, which separates genuine cross-orientation
reuse from ordinary same-orientation table hits.

An optional internal `search` configuration selects independent optimizations for
regression and ablation: ordinary bounded transpositions, D4-canonical
transpositions, full-state symmetry move pruning and improved move ordering. The
Shield maiden evaluator is D4-invariant and the engine's move generation and
terminal adjudication are equivariant, so canonical keys and move orbits may be
shared. Neural value heads are not guaranteed D4-equivariant even after
augmentation; requesting symmetry reuse with a neural evaluator raises instead of
merging unequal predictions. Ordinary orientation-specific transpositions remain
available to neural alpha-beta.

Full-state canonicalization transforms every historical entry under one global
transformation; the board is canonicalized for all eight symmetries first and
only the tied orientations transform history. Move pruning computes the full-state
stabilizer (a D4 subgroup), partitions the legal moves into orbits and retains one
deterministic representative per orbit *before* any successor state is built, so
discarded moves cost no `apply_move` or static evaluation. The engine legal-move
list is never mutated. `symmetry.py` accepts an optional cooperative check
callback (it never reads a clock), so move ordering, canonicalization, the
stabilizer and the orbit loop all observe the search deadline and abort a
long-history computation promptly. Neither symmetry feature is enabled by default
because canonicalization cost varies by ruleset and history length; the
reproducible benchmark measures the tradeoff. The per-search table capacity is
bounded at 500,000 entries because a repetition-aware key grows with history; the
measured per-entry cost and long-game peak are in the
[control-refinement evidence](docs/benchmarks/alpha-beta-controls.md). Move ordering applies one deterministic priority
(principal variation, transposition move, killers, history, stable static order)
and consults history even when no preferred move exists, without changing minimax
values; root ties still follow the seeded base order. The public `search`
configuration is validated and normalised by `clean_search_config` in `ai.py`, and
both `clean_ai_config` in the API and `effective_ai_config` resolve it to the same
shape so identity and behaviour cannot drift.

## MCTS node lifecycle

A cooperative search owns its tree for one move and one frozen model generation.
Each nonterminal neural node receives one prediction: its defender-relative value
and safely owned legal priors. Deferred leaf policies pack action IDs in host
uint16 arrays and probabilities in double arrays; expansion materializes the
remaining-move dictionary once. The first visit backs up the value;
a later visit consumes the saved priors to widen the node without yielding a
second request. Edge visits and totals always belong to that search. Terminal
children use engine outcomes before inference, including history-dependent
repetition outcomes. Model-free rollouts evaluate their horizon with the Shield
maiden heuristic freshly on each simulation.

Slotted nodes retain no dense logits, device tensors, parents or global tree
references. Expanded-node priors shrink as child states are created lazily. Keeping
leaf priors uses additional host memory; trees disappear when their context ends
or closes. Root noise changes only the root's owned policy. No prediction,
statistics, noisy policy or selected move survives into another move/generation.
The caller must keep its model/backend frozen while a search is pending, as with
the cooperative inference contract before this change.

PUCT computes the parent's square root once per selection, scans children in
insertion order and keeps the first maximum using the original floating-point
operation order. Integer progressive widening is exactly equivalent to the prior
square-root threshold; weighted sampling and RNG consumption are unchanged.
The scheduler still batches one dependent request per independent game. Node
reuse reduces generator transitions before the Phase 1 cache; cache hits and
batch deduplication remain separate from node reuse and completed simulations.

## Account and online play surfaces

`main.tsx` mounts `Auth` around `App`. Account play lives at `/games/{id}` and online
play at `/online/games/{id}`; both render the shared play-screen sections. Session
reads do not modify saved account preferences; explicit logout keeps its existing
cleanup. Protected requests have session epochs and cancellation so delayed
old-account responses cannot update a different account or emit stale authentication
events. Existing account selection and online-intent keys keep their formats and
semantics; account setup preferences retain only the permitted subset of older
values. Browser-local guest play and its device-only worker are removed; the
historical separation evidence stays in
[Phase 4 validation](docs/validation/online-phase4.md).


## Personal dashboard and review

`PersonalDashboard.tsx` is the authenticated landing with existing account setup and
embedded `Online.Lobby`, compact per-mode/ruleset performance badges and at most
three completed visible own records. `StatisticsPage.tsx` owns the authenticated
`/statistics` view, URL-backed mode/ruleset selection and account-keyed polling.
`PersonalStatistics.tsx` renders detailed results by ruleset, AI configuration and
account side, using tables on desktop and labelled metric cards on phones.
`navigation.ts` normalizes obsolete landing URLs while retaining
search/selection; account gameplay has `/games/{id}` and dedicated online/replay paths
remain. `RecordedGames` supplies compact rows (named winner, locale-aware date/time with
Timezone) to Dashboard and `Archive` (Hall). `RecordedReview` restores the full
single-player presentation (saga move list, board replay, general game information,
Analytics/Winning chances) using category-specific validated read adapters and
viewer-local state. Online review permits authorized completed-game analysis; account
single-player review uses the existing authorized analysis. Selected review is
independent of Hall filters/page and survives direct load/refresh.
Dashboard reads only account-scoped records.
Account-keyed poll state, cancellation and session epochs
reject late responses. The Dashboard's compact leaderboard and the full
`/leaderboard` view share authenticated qualified/provisional standings and the
viewer's own standing; see [Rated games](docs/RATINGS.md).

`personal_api.py` retains own-account GET adapters under `/api/personal`, including
administrators. Beyond the existing single-player/online history reads, two rating
projections reuse the immutable ledger: `GET /api/personal/ratings/{ruleset}/history`
returns sequence-ordered applied events with an opaque cursor, and
`GET /api/personal/progress` returns per-ruleset current/best rated win streaks.
Both are strictly self-scoped and add no schema or rating-math change; a durable
login/activity-day streak remains deferred to a future progression migration.
Schema 17 adds the read-only own-progress read `GET /api/personal/achievements`
beside the authenticated `GET /api/portraits/catalog`, an explicit CSRF-protected
`POST /api/personal/achievements/confirm` and the `profile`-scoped
`PATCH /api/profile/portrait`; awards and entitlements are confirmed idempotently
from the applied rating ledger and completed account games only. See
[portraits and achievements](docs/PROGRESSION.md). Two additional read-only `/api/history` routes use `history.py`:
normal-user lists/review cover own account games and participating online games;
trusted current `all_data` sessions can filter/review all server-recorded online/account records. Dashboard
history always applies own-account scope, visibility and eligible completion before
limit three. Summary/count/category totals use set-based SQL and bounded cursors in
one read transaction, without engine calls or schema changes. Detail rechecks role
and object scope in the same transaction as canonical replay. No history grant adds
mutation, analysis or export authority. Global authenticated online lobby/spectator
reads and all-role active-online assistance denial retain their permissions.

Account totals read persisted `recorded_result` only: current writers include it,
seeks preserve it and missing/null results are unfinished. Retained legacy records
remain readable; results are never reconstructed from board state. Personal statistics attribute wins/losses
to the account-bound human seat and exclude `ai-ai` and unattributed legacy records
from personal outcomes, reporting coverage instead. Online statistics use the
account's seat and only current eligible completed revisions, withdrawing voids and
counting own-clock timeouts separately from opponent timeouts. The existing
personal reads add completed counts, side metrics and seed-independent AI-agent
groups, without replay, inference, schema changes or new API grants. Aggregates
cover the complete account population independently of history pagination.
Win-rate/length denominators remain completed games; single-player played counts
also include unfinished games. Multiplayer uses the existing persisted rating,
shown only when the ruleset has played games. Dashboard badge tiers are cosmetic
functions of single-player win rate and do not write rewards or ratings. Unexpected
personal/history API failures log fixed actions, exception types and scrubbed
tracebacks with generic 503 responses. Local players UI and Archive-only profile
queries are removed; preset-only account setup replaces local-player selection/APIs; stored data remains. See the
[dashboard guide](docs/DASHBOARD.md), [Phase 5 contract](docs/designs/personal-dashboard-phase5.md),
[refinement evidence](docs/validation/dashboard-consolidation.md) and
[Rated games](docs/RATINGS.md).

Profile schema 15 retains schema-14 columns and adds an ISO `date_of_birth` plus
a one-time `profile_private_fields` exception list. Migration derives exceptions
from populated legacy fields whose old flags withheld publication. New rows have
no exceptions; public projections do not consult the old flags. Field-only PATCH
requests remove only their explicitly supplied exceptions, including same-value
confirmation, while keeping unrelated private values withheld. The UI exposes no
visibility fieldset: only affected legacy inputs offer temporary confirmation
actions. Full dates stay private; `account_policy.py` supplies completed-years age
on one UTC date per leaderboard response (February 29 advances March 1 in common
years). The compact table, ranking calculations, game records and permission
inventory are unchanged. See [account profile](docs/PROFILE.md).
