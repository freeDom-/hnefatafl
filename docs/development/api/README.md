# API reference

[Development index](../README.md) · [Architecture](../architecture.md) · [Security implementation](../security.md) · [OpenAPI endpoint](http://127.0.0.1:8000/openapi.json) · [Permission inventory](../../API_PERMISSIONS.json)

The running server exposes the exhaustive schema at `/openapi.json` and an
interactive reference at `/docs`. This page documents the boundaries, lifecycle
and cross-endpoint semantics that generated schema cannot express. Endpoint paths
below are a navigation aid; OpenAPI is authoritative for fields and status codes.

## Authentication and authorization

- Browser sessions use HttpOnly cookies. Every unsafe browser request requires the
  session CSRF token and an allowed `Origin`; worker requests use a bearer token.
- `docs/API_PERMISSIONS.json` is generated from the registered route table and
  checked by `tests/test_security.py`. It records each method/path's permission:
  `public`, `session`, `accounts`, `profile`, `play`, `training`, `models`,
  `tournaments` or `worker`.
- Object ownership is checked in addition to route permission. A game, job, model,
  worker or tournament is resolved through an owned ID; client filesystem paths are
  never trusted.
- `accounts` is the administrator permission (`all_data` / account management);
  `play` is the normal authenticated player permission. Hiding a UI control is not
  authorization.
- Login, registration, verification, password reset and public spectator reads are
  public. Public routes expose only an allowlisted read-only projection.

See [security implementation](../security.md) and
[security administration](../../administration/security.md).

## Conventions

| Topic | Contract |
| --- | --- |
| Versions | Game and tournament mutations carry the expected `version`; stale writes fail rather than overwrite newer state |
| Idempotency | Online creation/join, league queue operations, worker claims and result finalization use request IDs, leases or unique constraints where retries are expected |
| Pagination | List endpoints use bounded `limit` plus opaque `next_cursor`; offset paging is used only where explicitly documented |
| Errors | Structured JSON with a stable `code` where clients branch on it; validation errors name the offending field |
| Time | Server-owned UTC timestamps and deadlines; clients render in local time |
| Deletion | Version-guarded where required; model/job/tournament references are checked before deletion |
| Compatibility | Database schema, checkpoint/tensor format and archive schema are versioned; incompatible data is rejected, not guessed |

## Accounts, profiles and security

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/auth/registration` | Registration/mail availability |
| POST | `/api/auth/register`, `/api/auth/verify-email`, `/api/auth/verification/resend` | Registration and verification |
| POST | `/api/auth/login`, `/api/auth/logout` | Session lifecycle |
| GET | `/api/auth/me` | Current account, permissions and CSRF token |
| POST | `/api/auth/password-reset/request`, `/api/auth/password-reset/confirm` | Password recovery |
| GET/PATCH | `/api/profile` | Profile fields and publication choices |
| POST | `/api/profile/password` | Password change with current password |
| PATCH | `/api/profile/portrait` | Select an earned portrait |
| GET | `/api/portraits/catalog`, POST `/api/portraits/unlock` | Catalog and manual claim |

Email verification and reset tokens are single-use. Only the newest link works;
consumption and account mutation are transactional. Profile publication is opt-in
per optional field; private birth dates are never returned, and a public age is
derived only when the owner allows it. See [account implementation](../accounts.md).

## Games, replay and analysis

| Method | Path | Purpose |
| --- | --- | --- |
| GET/POST | `/api/games` | List/create account single-player games |
| POST | `/api/games/local` | Create an owner-scoped two-human local game |
| GET/DELETE | `/api/games/{game_id}` | Read or version-guarded delete |
| POST | `/api/games/{game_id}/moves` | Apply a legal move |
| POST | `/api/games/{game_id}/ai-move` | Request the configured AI move |
| POST | `/api/games/{game_id}/undo`, `/redo`, `/seek`, `/restart` | Administrative/compatibility history mutation |
| GET | `/api/games/{game_id}/replay` | Validated read-only positions |
| GET/PUT | `/api/games/{game_id}/analytics` | Saved analysis preferences |
| POST/GET | `/api/games/{game_id}/analysis*` | Background agent analysis and receipts |
| GET | `/api/history/games`, `/api/history/{category}/{game_id}/replay` | Account/admin record history |
| GET | `/api/history/online/{game_id}/analysis*` | Read-only completed-online analysis |

Moves are flattened zero-based `source`/`target` indexes plus `version`. The server
validates legality through the authoritative engine. AI moves and analysis retain
their effective configuration, seed and diagnostics. Replay is read-only and never
branches the saved game. See [game authority](../game-authority.md) and
[analysis execution](../analysis.md).

## Online play, ratings, leagues and human tournaments

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/online/availability` | Admission/eligibility |
| GET/POST | `/api/online/games` | Lobby list/create |
| GET | `/api/online/games/{game_id}` | Authoritative game state |
| POST | `/api/online/games/{game_id}/join`, `/moves`, `/concede`, `/cancel` | Lifecycle actions |
| POST | `/api/online/games/{game_id}/void` | Administrator void |
| GET | `/api/online/games/{game_id}/replay` | Completed replay |
| GET | `/api/leaderboard` | Rated standings and own rank |
| GET | `/api/personal/ratings/{ruleset}/history` | Own rating history |
| GET/POST | `/api/leagues/current`, `/queue`, `/queue/cancel`, `/queue/discard` | League season and queue |
| GET | `/api/leagues/seasons`, `/progress`, `/standings`, `/history`, `/active-game` | League reads |
| GET/POST | `/api/player-tournaments...` | Human tournament discovery, registration, bracket, matches |

Online creation is rated except player-tournament legs, which the server seats
unrated. Two-game matches rate each leg independently. Result revisions and rating
applications commit in one write transaction; a correction/void emits a new
revision and rebuilds the affected pool deterministically. League queue and
matchmaking are serialized with online writes and expire at ten minutes or the UTC
season boundary. See [online authority](../online-authority.md) and
[league authority](../league-authority.md).

## Progression and public spectator

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/personal/achievements`, `/api/personal/progress` | Achievement/portrait progress |
| GET | `/api/public/online/games` | Signed-out discovery |
| GET | `/api/public/online/games/{game_id}` | Public live state |
| GET | `/api/public/online/games/{game_id}/replay` | Public completed replay |

Public reads are GET-only, rate-limited and return an allowlisted projection.
Single-player, local games, private profiles, email and full birth dates are never
included. Public caching uses short-lived public cache headers; private responses
must not be cached. See [public API](public.md).

## Training, models, workers and AI testing

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/training/presets`, `/device`, `/workers` | Capabilities and presets |
| GET/POST | `/api/training/jobs` | List/create training or tuning jobs |
| GET/PATCH/DELETE | `/api/training/jobs/{job_id}` | Detail, rename, delete |
| POST | `/api/training/jobs/{job_id}/cancel`, `/resume` | Lifecycle |
| GET | `/api/training/jobs/{job_id}/export`, `/resume` | Portable export and effective resume values |
| POST | `/api/training/jobs/import` | Import archive/config |
| DELETE | `/api/training/jobs/{job_id}/study` | Remove tuning study and dependants |
| GET/POST | `/api/training/workers`, DELETE `/api/training/workers/{id}` | Worker enrollment/revocation |
| PATCH | `/api/training/workers/{id}/version-override` | Administrator compatibility override |
| GET/POST | `/api/models`, `/models/import`, `/models/{id}/export` | Model catalogue and transfer |
| PATCH/DELETE | `/api/models/{id}` | Rename/delete with usage checks |
| GET/POST | `/api/tournaments...` | Saved AI comparison series and replays |
| GET | `/api/ai/profiles`, `/rulesets`, `/stats` | Game configuration/statistics |

Training job creation distinguishes local and remote targets; web jobs receive
owned output/registry paths and reject CLI-only `output_dir`/`registry_dir`.
Resume rejects immutable architecture/optimizer changes. Uploaded worker artifacts
are validated before registration. Model deletion is blocked while active jobs,
studies or unfinished games reference it. See [training data](../training-data.md),
[worker protocol](../worker-protocol.md) and [AI testing](../ai-testing.md).

## Worker API

Worker routes use a bearer token and an explicit lease:

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/worker/heartbeat` | Capabilities, version and slot status |
| POST | `/api/worker/claim` | Atomically claim compatible work |
| POST | `/api/worker/jobs/{job_id}/progress` | Ordered progress snapshots |
| POST | `/api/worker/jobs/{job_id}/artifacts/{kind}` | Upload checkpoint/model/history artifacts |
| POST | `/api/worker/jobs/{job_id}/finish` | Idempotent completion acknowledgement |
| GET | `/api/worker/jobs/{job_id}/resume` | Resume artifact access |

Worker tokens are stored as hashes, scoped to the worker, and never returned after
the one-time enrollment dialog. Leases expire; a reconnect resumes from uploaded
state, not from server memory. See [worker protocol](../worker-protocol.md).

## Browser-local analysis

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/browser-analysis/availability` | Deployment policy/capabilities |
| GET | `/api/browser-analysis/models` | Published model catalogue |
| GET | `/api/browser-analysis/models/{publication_id}` | Manifest |
| GET | `/api/browser-analysis/models/{publication_id}/artifact` | Verified model bytes |

Delivery is disabled unless the deployment enables it. Publication is an explicit
administrator action; artifacts are immutable and digest-verified. The browser
executes the shared Python engine/search through a pinned runtime and never gets a
second rules implementation. See
[browser analysis architecture](../browser-analysis.md) and the
[active roadmap](../roadmap/browser-analysis.md).

## Compatibility and failure handling

- Unknown routes, unsupported fields and incompatible software versions fail
  explicitly. There is no silent algorithm/device/database fallback.
- The server owns deadlines and timeout finalization; an operator pause is a
  configuration gate, not a client action.
- A retry may be safe only where an idempotency key, lease, version or unique
  constraint is documented. Do not retry a move with a stale version.
- Generated OpenAPI and `docs/API_PERMISSIONS.json` must be updated when routes or
  permissions change. Tests derive the security matrix from the actual route table.

## Extension boundaries

Keep `hnefatafl/engine.py` free of HTTP, storage and PyTorch. Rules changes need
explicit variant positions; search changes need bounded/legal behavior; training
changes need real updates, checkpoint reload and legal play. Extend the AI profile
catalog and `choose_move` together when adding an opponent. Preserve deterministic
replay and optimistic version guards. Frontend theme extension is documented in the
[frontend guide](../frontend.md).
