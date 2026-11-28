# Architecture overview

[Development index](README.md) · [API](api/README.md) · [Decisions](decisions/README.md) · [Subsystem references](#subsystem-references)

Hnefatafl is a self-hosted web application: a Python rules/AI/training core, a
FastAPI backend with SQLite persistence, and a React browser client. The same
authoritative engine serves local play, online games, analysis, training and replay.

## Components

| Component | Responsibility |
| --- | --- |
| `hnefatafl/engine.py` | Pure immutable rules: legal moves, captures, variant special squares, repetition, outcomes |
| `hnefatafl/ai.py`, search modules | Baseline/alpha-beta/MCTS agents, wrappers, transposition/symmetry, analysis |
| `hnefatafl/models.py`, `training*.py` | Policy/value network, self-play, evaluation, checkpoints, tuning |
| FastAPI backend | Accounts, games, online lifecycle, ratings, leagues, tournaments, training, models, workers, public API |
| SQLite storage | Accounts, games, result/rating ledgers, progression, tournaments, jobs/models metadata |
| React client | Arena/Workshop UI, board rendering, replay, forms, themes and accessibility |
| Remote worker | Outbound CLI process executing training jobs and uploading validated artifacts |
| Browser analysis runtime | Opt-in pinned Pyodide/ORT Web execution of the shared engine/search (not a second rules engine) |

## Boundaries

- **Rules:** only the Python engine. HTTP, database and PyTorch stay outside it.
- **Authority:** server routes validate moves/results, own deadlines and persist
  immutable states; clients render and send expected versions.
- **Persistence:** account/game/result ledgers and training metadata share the data
  root but use separate tables/directories. Releases and virtual environments are
  replaceable; persistent data is not.
- **Authorization:** route permission plus owned-resource checks. Display names are
  presentation; account IDs own data.
- **Training:** jobs execute in an isolated process; workers claim work with leases
  and upload full checkpoints/models. The server never trusts worker paths or
  unvalidated artifacts.
- **Analysis:** server and browser execution use the same serialized positions and
  engine/search semantics. Deployment policy chooses execution and fallback.

## Data flow

1. The browser loads the authenticated shell and current account/permissions.
2. A game reads authoritative state; a move carries `version` and is validated by
   the engine before persistence.
3. Terminal online results write a result revision; rating/league settlement and
   achievements commit transactionally or through bounded durable workers.
4. Training jobs write progress/checkpoints to their owned directory; a worker
   uploads artifacts that the server validates before registering a model.
5. Replay/history reads return immutable positions; optional analysis attaches
   reports/receipts without mutating gameplay.

## Frontend/backend communication

- Authenticated JSON API with session cookies; unsafe requests require CSRF and an
  allowed Origin. Workers use bearer tokens.
- Game clients use flattened zero-based `source`/`target` indexes and expected
  versions. Analysis has its own revision lifecycle.
- Public spectator reads use a separate allowlisted GET projection.
- The browser never trusts client-supplied filesystem/asset paths or a client's
  permission claim. See [API](api/README.md).

## Deployment architecture

- One API process serves the built client and API; systemd service + reverse proxy
  terminate TLS.
- Persistent `HNEFATAFL_DATA_DIR` holds SQLite, jobs, checkpoints, models, studies,
  workers and optional published analysis artifacts.
- Stable releases are installed from a verified archive; updates preserve data and
  support rollback/recovery. Backup/restore covers the complete data root.
- Docker is not provided; Proxmox/Debian installers and examples are documented in
  [installation](../installation/README.md).

## Subsystem references

| Topic | Canonical reference |
| --- | --- |
| Rules/game persistence/replay | [Game authority](game-authority.md) |
| Online lifecycle/deadlines/results | [Online authority](online-authority.md) |
| Ratings/rebuilds | [Rating authority](rating-authority.md) |
| Leagues/queue/settlement | [League authority](league-authority.md) |
| Player tournaments | [Tournament authority](tournament-authority.md) |
| Analysis/receipts/execution policy | [Analysis](analysis.md) |
| Browser-local analysis | [Browser analysis](browser-analysis.md) |
| Accounts/tokens/profile persistence | [Accounts](accounts.md) |
| Security/permissions/CSRF | [Security implementation](security.md) |
| Training storage/checkpoints/archives | [Training data](training-data.md) |
| Worker protocol | [Worker protocol](worker-protocol.md) |
| AI Testing authority | [AI Testing](ai-testing.md) |
| Training monitoring internals | [Training monitoring](training-monitoring.md) |
| Frontend shell/design system | [Frontend](frontend.md), [UI guidelines](ui-guidelines.md) |
| Database migrations | Owned by the subsystem; additive migrations preserve existing IDs/data |

## Invariants

- Deterministic replay and optimistic write guards are preserved.
- Historical results/release evidence are not rewritten as current behavior.
- Model checkpoints use the validated tensor-only loader; incompatible data fails.
- Unfinished games are distinguished from rule draws throughout search/training.
- Browser analysis is opt-in; disabled deployment keeps the strict CSP.
- Runtime rules Markdown (`docs/rules/`) is served by the application and must not
  move.

## Historical context

Completed implementation phases and their evidence are preserved in release notes,
the [Arena snapshot](https://github.com/freeDom-/hnefatafl/blob/c1779a133d3199179ae74a50403eab238347b992/docs/releases/arena-snapshot-2026-11-20.md), subsystem documents
and the [browser analysis roadmap](roadmap/browser-analysis.md). They are not a
second current architecture description.
