# API reference

[Development index](../README.md) · [Architecture](../architecture.md) · [Security](../security.md) · [OpenAPI endpoint](http://127.0.0.1:8000/openapi.json) · [Permission inventory](../../API_PERMISSIONS.json)

The running server exposes the exhaustive schema at `/openapi.json` and interactive
reference at `/docs`. This page documents boundaries, lifecycle and cross-endpoint
semantics. The path tables below are a concise navigation aid; generated OpenAPI and
`docs/API_PERMISSIONS.json` are authoritative.

## Authentication and authorization

- Browser sessions use HttpOnly cookies. Unsafe requests require the session CSRF
  token and an allowed `Origin`. Worker requests use a worker-scoped bearer token.
- `docs/API_PERMISSIONS.json` records every method/path and its permission:
  `public`, `session`, `accounts`, `profile`, `play`, `training`, `models`,
  `tournaments` or `worker`. `tests/test_security.py` checks the real route table.
- Route permission is not sufficient: games, jobs, models, workers, tournaments and
  analyses are resolved through owned IDs. Client filesystem/asset paths are never
  trusted. `accounts` is the administrator permission; `play` is normal authenticated
  play. UI visibility is not authorization.
- Public routes expose only an allowlisted read-only projection. `/api/profile`
  returns the owner's private profile fields to that authenticated account; public
  leaderboard/online/history projections omit private birth dates, email and settings.

## Conventions

| Topic | Contract |
| --- | --- |
| Versions | Game/tournament mutations carry expected `version`; stale writes fail |
| Idempotency | Online creation/join, league queue, worker claims and result finalization use request IDs, leases or unique constraints where retries are expected |
| Pagination | Bounded `limit` + opaque `next_cursor`; offset paging only where documented |
| Errors | Structured JSON with stable `code` where clients branch; validation names the field |
| Time | Server-owned UTC deadlines/timestamps; clients render local time |
| Deletion | Version-guarded where required; model/job/tournament references checked |
| Compatibility | Database, checkpoint/tensor and job-archive formats are versioned; incompatible data is rejected, not guessed |

## Accounts, profiles and security

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/health` | Public service readiness |
| GET | `/api/auth/registration` | Registration/mail availability |
| POST | `/api/auth/register` | Create account |
| POST | `/api/auth/verify-email` | Consume verification token |
| POST | `/api/auth/verification/resend` | Resend newest verification link |
| POST | `/api/auth/login` | Create browser session |
| POST | `/api/auth/logout` | Revoke browser session |
| GET | `/api/auth/me` | Current account, permissions, CSRF token |
| POST | `/api/auth/password-reset/request` | Request reset mail |
| POST | `/api/auth/password-reset/confirm` | Consume reset token and set password |
| GET | `/api/profile` | Owner's profile (including private fields) |
| PATCH | `/api/profile` | Update optional/publication fields |
| POST | `/api/profile/password` | Change password with current password |
| PATCH | `/api/profile/portrait` | Equip an unlocked portrait |
| GET | `/api/portraits/catalog` | Portrait/achievement catalog |
| POST | `/api/portraits/unlock` | Unlock an achievement-eligible portrait |

Verification/reset tokens are single-use, expiring and stored hashed; consumption and
the account mutation are one transaction. Public projections never include email,
full birth dates or private settings. See [account implementation](../accounts.md).

## Games, replay and analysis

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/games` | List account single-player games |
| POST | `/api/games` | Create a single-player game |
| POST | `/api/games/local` | Create owner-scoped local 1v1 |
| GET | `/api/games/{game_id}` | Read authoritative game |
| DELETE | `/api/games/{game_id}` | Version-guarded delete |
| POST | `/api/games/{game_id}/moves` | Apply a legal move |
| POST | `/api/games/{game_id}/ai-move` | Request configured AI move |
| POST | `/api/games/{game_id}/undo` | Compatibility history mutation |
| POST | `/api/games/{game_id}/redo` | Compatibility history mutation |
| POST | `/api/games/{game_id}/seek` | Compatibility history mutation |
| POST | `/api/games/{game_id}/restart` | Version-guarded unfinished restart |
| GET | `/api/games/{game_id}/replay` | Validated read-only positions |
| GET | `/api/games/{game_id}/analytics` | Saved analysis preferences |
| PUT | `/api/games/{game_id}/analytics` | Update analysis preferences |
| POST | `/api/games/{game_id}/analysis` | Run/read background analysis |
| GET | `/api/games/{game_id}/analysis-status` | Analysis progress/status |
| POST | `/api/games/{game_id}/analysis-receipt` | Create/read analysis receipt |
| GET | `/api/games/{game_id}/analysis-context` | Authorized serialized analysis context |
| GET | `/api/analysis/metrics` | Administrator analysis metrics |
| GET | `/api/analysis/receipts/{receipt_id}` | Analysis receipt metadata |
| GET | `/api/analysis/receipts/{receipt_id}/result` | Analysis receipt result |
| GET | `/api/personal/single-player` | Owner's single-player game list |
| GET | `/api/personal/single-player/{game_id}/replay` | Owner's single-player replay |
| GET | `/api/personal/local` | Owner's local 1v1 list |
| GET | `/api/personal/local/{game_id}/replay` | Owner's local replay |
| GET | `/api/personal/online` | Owner's online game list |
| GET | `/api/personal/online/{game_id}` | Owner's online game summary |
| GET | `/api/personal/online/{game_id}/replay` | Owner's online replay |
| GET | `/api/history/games` | Account/admin record list |
| GET | `/api/history/{category}/{game_id}/replay` | Validated history replay |
| GET | `/api/history/online/{game_id}/analytics` | Completed-online saved analytics |
| PUT | `/api/history/online/{game_id}/analytics` | Update completed-online analytics |
| POST | `/api/history/online/{game_id}/analysis` | Run/read completed-online analysis |
| GET | `/api/history/online/{game_id}/analysis-status` | Completed-online analysis status |
| POST | `/api/history/online/{game_id}/analysis-receipt` | Completed-online analysis receipt |
| GET | `/api/history/online/{game_id}/analysis-context` | Completed-online analysis context |

Moves use flattened zero-based `source`/`target` and expected `version`; the engine
validates legality. Replay never branches/mutates. Analysis is tied to a position/
revision and cannot publish for a newer state. See [game authority](../game-authority.md)
and [analysis](../analysis.md).

## Online play, ratings, leagues and human tournaments

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/online/availability` | Admission/eligibility |
| GET | `/api/online/games` | Authenticated lobby list |
| POST | `/api/online/games` | Create online game |
| GET | `/api/online/games/{game_id}` | Authoritative online state |
| POST | `/api/online/games/{game_id}/join` | Join waiting game |
| POST | `/api/online/games/{game_id}/moves` | Apply online move |
| POST | `/api/online/games/{game_id}/concede` | Concede |
| POST | `/api/online/games/{game_id}/cancel` | Cancel waiting game |
| GET | `/api/online/games/{game_id}/replay` | Completed online replay |
| POST | `/api/online/games/{game_id}/void` | Administrator void |
| GET | `/api/online/monitoring` | Administrator online health |
| GET | `/api/online/stats` | Online statistics |
| GET | `/api/online/timeout-gate` | Inspect finalization gate |
| PATCH | `/api/online/timeout-gate` | Pause/restore finalization |
| POST | `/api/online/ratings/rebuild` | Administrator pool rebuild |
| GET | `/api/leaderboard` | Rated standings and own rank |
| GET | `/api/personal/ratings/{ruleset}/history` | Own rating history |
| GET | `/api/leagues/current` | Current season/policy/progress/queue |
| GET | `/api/leagues/seasons` | Persisted seasons |
| GET | `/api/leagues/progress` | Own seasonal progress |
| GET | `/api/leagues/standings` | Qualified standings |
| GET | `/api/leagues/history` | Own official league games |
| GET | `/api/leagues/queue` | Queue/eligibility snapshot |
| POST | `/api/leagues/queue` | Join queue |
| POST | `/api/leagues/queue/cancel` | Cancel/retrieve request outcome |
| POST | `/api/leagues/queue/discard` | Discard pending join intent |
| GET | `/api/leagues/active-game` | Current official league game |
| GET | `/api/leagues/admin/health` | Queue/settlement/rebuild health |
| GET | `/api/leagues/admin/audit` | Private administration audit |
| POST | `/api/leagues/admin/matchmaking` | Pause/resume matchmaking |
| POST | `/api/leagues/admin/queue/cancel` | Cancel stale queue entry |
| POST | `/api/leagues/admin/seasons/{season_id}/rebuild` | Schedule season rebuild |
| GET | `/api/player-tournaments` | List/search tournaments |
| POST | `/api/player-tournaments` | Create tournament |
| GET | `/api/player-tournaments/{tournament_id}` | Detail |
| GET | `/api/player-tournaments/{tournament_id}/bracket` | Bracket |
| GET | `/api/player-tournaments/{tournament_id}/matches` | Matches |
| GET | `/api/player-tournaments/{tournament_id}/matches/{match_id}` | Match detail |
| POST | `/api/player-tournaments/{tournament_id}/register` | Register |
| POST | `/api/player-tournaments/{tournament_id}/withdraw` | Withdraw |
| POST | `/api/player-tournaments/{tournament_id}/start` | Creator start |
| POST | `/api/player-tournaments/{tournament_id}/reschedule` | Creator reschedule |
| POST | `/api/player-tournaments/{tournament_id}/settle` | Settle match |
| POST | `/api/player-tournaments/{tournament_id}/cancel` | Administrator cancel |
| POST | `/api/player-tournaments/{tournament_id}/matches/{match_id}/override` | Administrator override |
| POST | `/api/player-tournaments/{tournament_id}/rebuild` | Administrator rebuild |
| GET | `/api/player-tournaments/{tournament_id}/audit` | Administrator audit |

Normal online/league games are rated except player-tournament legs, which are seated
unrated. Each two-game match leg is rated independently. Result revisions, rating
applications and league settlement are transactional/durable; corrections/voids
rebuild deterministically. See [online authority](../online-authority.md),
[rating authority](../rating-authority.md), [league authority](../league-authority.md)
and [tournament authority](../tournament-authority.md).

## Progression and public spectator

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/personal/achievements` | Achievement/portrait progress |
| GET | `/api/personal/progress` | Account progression summary |
| GET | `/api/public/online/games` | Signed-out discovery |
| GET | `/api/public/online/games/{game_id}` | Public live state |
| GET | `/api/public/online/games/{game_id}/replay` | Public completed replay |

Public reads are GET-only, rate-limited and allowlisted; they exclude private
history, email, full birth dates, session data, moderation data and private analysis
configuration/diagnostics. Live public games expose no analysis. A completed public
replay may include **already-stored terminal-position analysis** through the typed
allowlist; a public request never computes, recomputes or mutates analysis, and the
public UI does not render it. Public discovery is enabled by default and can be
disabled by deployment. Responses use short public cache headers; private responses
are non-cacheable. See [online authority](../online-authority.md).

## Training, models, workers and AI Testing

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/training/presets` | Preset catalog |
| GET | `/api/training/device` | Server device/capabilities |
| GET | `/api/training/workers` | Worker list |
| POST | `/api/training/workers` | Enroll worker |
| DELETE | `/api/training/workers/{worker_id}` | Revoke worker |
| PATCH | `/api/training/workers/{worker_id}/version-override` | Compatibility override |
| GET | `/api/training/jobs` | List jobs |
| POST | `/api/training/jobs` | Create training/tuning job |
| GET | `/api/training/jobs/{job_id}` | Job detail |
| PATCH | `/api/training/jobs/{job_id}` | Rename/update |
| DELETE | `/api/training/jobs/{job_id}` | Delete job data |
| POST | `/api/training/jobs/{job_id}/cancel` | Cancel job |
| GET | `/api/training/jobs/{job_id}/export` | Export job archive |
| GET | `/api/training/jobs/{job_id}/resume` | Effective resume values |
| POST | `/api/training/jobs/{job_id}/resume` | Resume from checkpoint |
| DELETE | `/api/training/jobs/{job_id}/study` | Delete tuning study |
| GET | `/api/training/jobs/{job_id}/study-deletion-preview` | Study deletion preview |
| POST | `/api/training/jobs/import` | Import job archive/config |
| GET | `/api/models` | Model catalog |
| POST | `/api/models/import` | Import model |
| PATCH | `/api/models/{model_id}` | Rename model |
| DELETE | `/api/models/{model_id}` | Delete model |
| GET | `/api/models/{model_id}/export` | Export model |
| GET | `/api/models/{model_id}/usage` | References/usage |
| GET | `/api/tournaments` | List AI comparison series |
| POST | `/api/tournaments` | Create AI comparison |
| GET | `/api/tournaments/{tournament_id}` | AI comparison detail |
| DELETE | `/api/tournaments/{tournament_id}` | Delete AI comparison |
| POST | `/api/tournaments/{tournament_id}/cancel` | Stop AI comparison |
| GET | `/api/tournaments/{tournament_id}/export` | Export comparison |
| GET | `/api/tournaments/{tournament_id}/games/{game_index}/replay` | AI game replay |
| GET | `/api/ai/profiles` | Agent profile catalog |
| GET | `/api/rulesets` | Ruleset catalog |
| GET | `/api/stats` | Account statistics |

Web jobs receive owned output/registry paths and reject CLI-only path fields. Resume
rejects immutable architecture/optimizer changes. Imported/worker artifacts are
validated before registration; model deletion is blocked by live references. See
[training data](../training-data.md), [worker protocol](../worker-protocol.md) and
[AI Testing authority](../ai-testing.md).

## Worker API

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/api/worker/heartbeat` | Capabilities/version/slot status |
| POST | `/api/worker/claim` | Atomically claim compatible work |
| POST | `/api/worker/jobs/{job_id}/progress` | Ordered progress snapshot |
| POST | `/api/worker/jobs/{job_id}/artifacts/{kind}` | Upload checkpoint/model/history artifact |
| POST | `/api/worker/jobs/{job_id}/finish` | Idempotent completion acknowledgement |
| GET | `/api/worker/jobs/{job_id}/resume` | Resume artifact access |

Worker tokens are hashed/scoped and returned once at enrollment. Leases expire;
reconnect resumes from uploaded state. See [worker protocol](../worker-protocol.md).

## Browser-local analysis

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/browser-analysis/availability` | Deployment policy/capabilities |
| GET | `/api/browser-analysis/models` | Published model catalog |
| GET | `/api/browser-analysis/models/{publication_id}` | Manifest |
| GET | `/api/browser-analysis/models/{publication_id}/artifact` | Verified model bytes |

Delivery is disabled unless enabled by deployment. Publication is explicit,
immutable and digest-verified. The browser uses the shared Python engine/search.
See [browser analysis](../browser-analysis.md) and its
[active roadmap](../roadmap/browser-analysis.md).

## Extension boundaries

Keep `hnefatafl/engine.py` free of HTTP, storage and PyTorch. Rules changes need
explicit variant positions; search changes need bounded/legal behavior; training
changes need real updates, checkpoint reload and legal play. Extend AI profiles and
`choose_move` together. Preserve deterministic replay and optimistic versions.
