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
Optuna manages persistent search studies. Docker is not required.

The browser never decides legality or game outcomes. Every position and move has a
serializable representation, shared by interactive play, replay, AI and training.
Pieces are `.`, `A`, `D`, `K`; moves use zero-based flattened `source` and `target`
indices. Turns and results use `attackers`, `defenders`, and `draw` for draws.

## Components

| Module | Responsibility and boundary |
| --- | --- |
| [engine.py](hnefatafl/engine.py) | Immutable state, legal moves, captures, terminal rules; no HTTP/database/tensor dependency |
| [symmetry.py](hnefatafl/symmetry.py) | Pure D4 geometry, full-history transforms and typed canonical keys; no search or neural canonicalization |
| [ai.py](hnefatafl/ai.py) | Bounded heuristic/alpha-beta/MCTS, neural wrappers, cooperative MCTS contexts, seeded headless tournaments |
| [shield_maiden.py](hnefatafl/shield_maiden.py) | Versioned one-ply royal geometry, escape routes and piece-safety evaluation; separate from generic search evaluation |
| [heuristic_benchmark.py](hnefatafl/heuristic_benchmark.py) | Paired heuristic strength, matched-position timing and actual training-evaluation runtime |
| [models.py](hnefatafl/models.py) | Ruleset-specific policy/value network, safe checkpoints and content-addressed model registry |
| [training.py](hnefatafl/training.py), [training_presets.py](hnefatafl/training_presets.py) | Learning, replay, optimizer state, evaluation, resume, Optuna and validated budgets |
| [training_augmentation.py](hnefatafl/training_augmentation.py), [symmetry_benchmark.py](hnefatafl/symmetry_benchmark.py) | Tensor-level D4 transforms of replay examples (input, action ids, legal mask), cached action permutations, minibatch collation and evidence tooling; no engine, search or inference dependency |
| [self_play.py](hnefatafl/self_play.py), [inference.py](hnefatafl/inference.py) | Concurrent game scheduling, shared legal-policy inference batches and activity metrics |
| [benchmark.py](hnefatafl/benchmark.py), [cli.py](hnefatafl/cli.py) | Same-device performance comparisons and headless command dispatch |
| [storage.py](hnefatafl/storage.py), [identities.py](hnefatafl/identities.py) | Transactional SQLite records, compare-and-swap moves, normalized identities and runes |
| [online_core.py](hnefatafl/online_core.py), [online_api.py](hnefatafl/online_api.py), [online_migrations.py](hnefatafl/online_migrations.py) | Durable lifecycle/engine integration, authenticated safe adapters, atomic seats/moves/deadlines/results and side-swapped two-game matches |
| [server.py](hnefatafl/server.py), [analysis_service.py](hnefatafl/analysis_service.py) | Owned API paths, catalog/job coordination, bounded background analysis and stale-result guards |
| [tournaments.py](hnefatafl/tournaments.py) | Separate browser tournament process/lifecycle and durable partial reports |
| [job_archives.py](hnefatafl/job_archives.py), [job_studies.py](hnefatafl/job_studies.py) | Bounded, sanitized job ZIP migration, staged publication and contained Optuna study rebasing |
| [remote_training.py](hnefatafl/remote_training.py), [remote_worker.py](hnefatafl/remote_worker.py) | Server leases/artifact ingestion and outbound worker subprocess supervision |
| [frontend/src](frontend/src/) | React board/setup, replay, records, tournament, model/training controls, localization and appearance |

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
Persistent timeout finalization starts paused and has a separate unapproved outage
policy prerequisite; overdue play stays rejected without deadline extension. Current
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

## Training execution and inference

Self-play uses sequential execution or cooperatively interleaves independent games
through one shared model. Each dependent MCTS simulation waits for its own leaf
inference result; independent pending leaves can share a forward pass. Replay commits
remain in game-index order. CUDA auto selects batching; PyTorch CPU/MPS auto stays sequential. Explicit
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
distinct. Evaluation remains sequential with separate frozen models; self-play's
single-model batching is not reused across model identities. Compact stdout events
exclude history; atomic JSON/checkpoints retain it for local and remote polling.

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

Training augmentation is a separate, optional (`training_symmetry_mode`, default
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
while excluding seed-only differences. New sessions
store resolved parameters and display-name snapshots. Legacy explicit-algorithm
sessions retain the defaults their original engine used; profile defaults are not
retroactively applied. Variant statistics expose configuration metadata for inspection.

Player tombstones and archived model metadata preserve deleted identities. Deleting a player
hides it from selection while preserving its ID and saved history; reusing the same
display name creates a new identity. Deleting an available model removes its registry
copy and entry, retaining historical labels and original training outputs. Catalog
reference creation and deletion are serialized in the supported single API process.

Appearance is browser-local state, separate from game configuration. A typed catalog
defines color modes, board palettes and token sets; shared token rendering is used
for actual pieces and previews. The provider resolves device color preferences and
persists choices. New artwork and palettes can be added without changing game rules
or persistence. See [Personalization](docs/PERSONALIZATION.md).

Ordinary new games store a server-generated base seed; actual decision seeds combine
the base with the ply. Explicit game seeds support reproducible requests. Restart
always creates a fresh seed, and legacy records retain their original semantics.
Game deletion uses an expected version and removes the source record; statistics
are derived from retained records, so all counts/rates update without cached totals.
Restart is restricted to unfinished records, using the retained final outcome
independently of the current replay cursor. Both UI and API enforce this rule.

Model deletion previews every unfinished reference on the server rather than only
the browser's current game. Discarding those games requires an explicit opt-in and
a matching usage fingerprint. SQL and registry failure handling preserve references
if deletion fails; completed histories and training checkpoints remain. App-level
deletion callbacks clear matching current-game pointers and reject late AI responses.

Hall tables sort and filter presentation data locally without rewriting stored
results or changing aggregate statistics. Numeric comparisons cover counts, rates
and averages; player/history text searches remain independent. AI parameter dialogs
share the same full-value renderer as recorded-game details while keeping table rows
compact. New browser preferences default to carved wood and walnut; valid saved
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

New-game browser preferences retain a validated complete successful setup. Native
model choices update their canonical names with their IDs, and saved model-name
snapshots identify earlier games whose supplied aliases were stale. Training
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
`TrainingJobNavigator` owns local search, filtering and sorting;
`TrainingJobDashboard` owns tabs and iteration range without resetting on polling.
`TrainingAnalysisViews` composes Overview, Learning, Evaluation and Details;
`TrainingPerformance` groups computational metrics. `TrainingMetricChart` is a
small dependency-free responsive SVG renderer used by `TrainingHistoryChart`
and the performance view. `trainingDashboardData` normalizes optional legacy
metrics and separates effective evaluation contexts/incumbents. Large tables
and raw diagnostics mount on disclosure. Shared allowlisted configuration reuse
opens an editable fresh-job form for models and jobs. `game_statistics.py` records
versioned job-local/baseline/cumulative self-play counters in existing JSON and
checkpoint metadata; immutable selection snapshots retain their own totals.
Tensor format and database schema remain unchanged. See the
[dashboard guide](docs/TRAINING_DASHBOARD.md).

## MCTS node lifecycle

A cooperative search owns its tree for one move and one frozen model generation.
Each nonterminal neural node receives one prediction: its defender-relative value
and safely owned legal priors. Deferred leaf policies pack action IDs in host
uint16 arrays and probabilities in double arrays; expansion materializes the
remaining-move dictionary once. The first visit backs up the value;
a later visit consumes the saved priors to widen the node without yielding a
second request. Edge visits and totals always belong to that search. Terminal
children use engine outcomes before inference, including history-dependent
repetition outcomes. Heuristic rollouts remain fresh on each simulation.

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
