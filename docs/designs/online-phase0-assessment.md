# Phase 0 repository assessment

[Documentation index](../README.md) · [Lifecycle and result design](online-phase0.md) ·
[Registration handoff](registration-phase1.md) · [Validation](../validation/online-phase0.md)

Assessment baseline: `5bd32770f25103d9232537fe63cbeaa162a1952f` (2026-10-25).
The observations below describe that source, not a multiplayer system already
implemented. Phase 0 adds an unintegrated contract and standalone experiment only.

## Accounts and ownership: observed

- [auth.py](../../hnefatafl/auth.py): `password_hash` uses salted scrypt
  (`N=32768, r=8, p=3`, 16 random salt bytes); `verify` compares in constant time.
  Passwords are 12–256 characters with at least four distinct characters.
- `Auth.create_account` NFKC-normalizes usernames, trims them, permits a bounded
  ASCII identifier, and NFKC/trim/casefold-normalizes emails. SQLite enforces
  `username UNIQUE COLLATE NOCASE` and email uniqueness. Usernames cannot contain
  `@`, so the current username/email login namespaces do not overlap.
- Login normalizes identity and uses a precomputed dummy hash for unknown users.
  SQLite login-attempt buckets limit identity and address attempts over 15 minutes.
  This is login protection, not registration/email abuse protection.
- Sessions use 32-byte random opaque tokens; SQLite stores SHA-256 token and CSRF
  digests. Login revokes the presented prior session; logout deletes its session.
  Sessions expire without sliding renewal, survive process restarts, and reload
  role/status for each request. Account updates revoke all that account's sessions.
  Cookies are HttpOnly, SameSite=Lax, and Secure in production.
- `SecurityBoundary` denies undeclared routes, rejects WebSockets, enforces the
  method/path [permission registry](../API_PERMISSIONS.json), host/origin checks,
  CSRF on session mutations and a shared deployment mutation gate. Public login
  requires JSON and a matching Origin. Error handling suppresses password inputs.
- [security_context.py](../../hnefatafl/security_context.py) defines USER and ADMIN.
  Request context propagates into synchronous handlers. `owner_scope` scopes
  ordinary users; administrators have `all_data`. Background code without a
  principal is unscoped: new online code must establish authorization explicitly,
  not interpret absence of a principal as an anonymous public read.
- [accounts.py](../../hnefatafl/accounts.py) is local provisioning/reset/role/status
  tooling. `/api/auth/registration` is an informational GET, not registration.
  There is no email verification, recovery token storage or delivery service.
- `public_account` includes email and security-related account fields for the
  authenticated profile. It must **not** become the online public-player serializer.

[test_security.py](../../tests/test_security.py) checks route coverage, role matrices,
real login/session restoration/expiry, CSRF/origins, revoked roles, last-admin
protection, cross-account game routes, statistics, players and legacy ownership.
[auth_support.py](../../tests/auth_support.py) provisions disposable accounts.

## Persistence and concurrency: observed

[storage.py](../../hnefatafl/storage.py) uses SQLite WAL and short connection-scoped
transactions with commit/rollback and a 15-second busy timeout. Games store a JSON
payload plus indexed identity/version/creation/owner columns. Schema 8 creates
accounts, sessions and an unused `game_participants` scaffold keyed by game/side;
game creation does not populate that scaffold. Ownership is a single account,
not two online participants. Human profile IDs/runes are not account identities.

`Store.update_game` performs `UPDATE ... WHERE id=? AND version=?` and checks one
updated row. This protects against stale/concurrent writes, including AI computed
outside the transaction. [test_api.py](../../tests/test_api.py) tests strict expected
versions and two competing updates. This does not establish atomic joining,
deadline arbitration or exactly-once competitive result processing. The proposed
integration must include state, seats, deadline, result and effects in one transaction.

Schema initialization uses additive `BEGIN IMMEDIATE` migration logic and refuses
schemas newer than 8. Legacy owners remain NULL/admin-only. No Phase 0 schema is
introduced. [Deployment](../DEPLOYMENT.md#data-and-configuration) and
[rollback](../DEPLOYMENT.md#rollback-and-recovery) require matching full
data snapshots with code. Rollback preserves compatible current account security
state, invalidates sessions and revokes restored worker credentials; incompatible
account schemas require manual recovery. Future registration changes must update
those explicit compatibility checks and tests. A JSON-only feature can still
create rollback incompatibility; additive columns alone do not make downgrades safe.

## Rules, results and history: observed

- [engine.py](../../hnefatafl/engine.py) imports only the standard library.
  Frozen `State`/`Move`, `new_game`, `legal_moves`, `apply_move` and replay are the
  reusable rules contract. Ruleset first player differs: Tablut defenders;
  Brandubh/Copenhagen attackers. Outcomes use attackers/defenders/draw and technical
  reasons such as king escape/capture, repetition, encirclement and edge fort.
  [test_engine.py](../../tests/test_engine.py) has explicit source-specific positions.
- [server.py](../../hnefatafl/server.py), `append_move` persists accepted plies,
  `ended_at` and `recorded_result`. `recorded_outcome` is independent of the replay
  cursor and derives legacy outcomes when needed. These are engine outcomes;
  there are no current concession/timeout/online cancellation/result-revision APIs.
- Game modes are local human-human, human-AI and AI-AI server sessions, not online
  two-account games. `make_move` checks human versus AI turn, not two account seats.
  Undo/redo/seek can branch retained lines; completed records cannot restart.
  Those existing semantics must remain for current modes and be prohibited or
  read-only as appropriate in the later online adapter.
- `Store.get_game`/`list_games` and player methods owner-scope ordinary users;
  admins can collect all games for training. Lists read all scoped records, with
  no database pagination. `/api/games` filters ruleset/mode/kind in memory;
  `/api/stats` also filters player identity and derives totals from retained results.
  Deletion changes totals. No Elo, competitive ledger or abandoned-games counter
  exists. [test_game_management.py](../../tests/test_game_management.py) covers
  deletion, replay, restart and stale AI results.
- [Archive.tsx](../../frontend/src/Archive.tsx) and
  [recordTables.ts](../../frontend/src/recordTables.ts) filter/sort presentation data
  locally. Future public histories need server-side bounded pagination and
  independently authorized filters; do not reuse an admin-wide list then redact it.
- [identities.py](../../hnefatafl/identities.py) normalizes AI configurations and
  historical display snapshots. [game_statistics.py](../../hnefatafl/game_statistics.py)
  tracks recorded self-play counts, not competitive player accounting.
  Tournament records/standings are separate from ordinary history and future ladders.

## Analysis and connectivity: observed

[ai.py](../../hnefatafl/ai.py) imports standard-library modules plus the engine at
module load. Baseline evaluation/search runs without external dependencies.
PyTorch/model imports are lazy in neural paths. Shield maiden is another pure
module, but is outside the smallest spike. [models.py](../../hnefatafl/models.py)
and training require native tensor dependencies: no browser neural parity is implied.

[api.ts](../../frontend/src/api.ts) sends same-origin session/CSRF-protected fetches.
[App.tsx](../../frontend/src/App.tsx) obtains boards/legal moves, submits moves and
requests AI computation from the server. Browser storage retains preferences and
a game ID, not an executable engine or full offline game. [Auth.tsx](../../frontend/src/Auth.tsx)
restores server sessions and clears game pointers when accounts change. There is
no production offline cache/service worker or anonymous gameplay bypass.

[AgentAnalysis.tsx](../../frontend/src/AgentAnalysis.tsx) polls analytics and requests
game-position analysis, exposing hints and winning chances. Server routes include
`POST /api/games/{id}/analysis`, `GET/PUT .../analytics`; game responses and AI
responses can carry estimates too. `response`, `analysis_response`,
`request_analysis` and `analyze_game` must all be reviewed in the online phase.
The API currently analyzes retained, owned game positions; no public arbitrary-board
analysis route was found in `server.py`/the permission registry. Nevertheless an
online attacker can reconstruct a public line in a separate game and use its
analysis, or use an external engine. Permission design must address those routes
and avoid claiming that withholding an analysis button prevents external cheating.

[analysis_service.py](../../hnefatafl/analysis_service.py) uses two background
threads with position/revision keys and cooperative supersession. Stored reports
survive restart; live task maps do not. Late results cannot overwrite moves or
revive deleted/superseded branches ([test_background_analysis.py](../../tests/test_background_analysis.py),
[test_analysis_api.py](../../tests/test_analysis_api.py)). This executor is not a
durable deadline scheduler.

## Background/restart boundaries: observed and proposed gap

Server lifespan shuts down analysis/tournaments and terminates its local training
subprocesses, retaining checkpoints. [tournaments.py](../../hnefatafl/tournaments.py)
reconciles missing worker processes and retains partial completed matches.
[remote_training.py](../../hnefatafl/remote_training.py) persists leases and handles
expiry/retry for remote workers; it is a training protocol and must remain unchanged.
There is no multiplayer expiry sweep or authoritative move-deadline storage.

Proposed: persist online UTC deadlines, process overdue games on startup and in
bounded periodic batches, and recheck on requests using the same lifecycle
function. Timers are wake-up hints only. Reuse SQLite transactions and expected
versions, not training process state or an in-memory deadline reset.

## Reuse and gaps

Reuse the engine, baseline AI, secure password/session implementation, CSRF and
fail-closed route registry, account IDs, SQLite transaction/CAS patterns and
deterministic replay. Extend ownership into online participant authorization,
without exposing private account-single-player data. Add registration verification,
recovery and throttling in Phase 1; add transactional seats/deadlines/result revisions,
public serializers, analysis policy and expiry reconciliation in a later multiplayer
phase. No engine rewrite, broker, training modification or persistence replacement
is required by the evidence so far. Browser feasibility evidence lives with the
[isolated spike](../../spikes/browser_offline/README.md).
