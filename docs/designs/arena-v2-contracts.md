# Arena v2 contracts and integration plan

Status: current implementation boundaries after PR #84, audited at
`2579a86b82ed1eca91f39e7d8064196597e873ed` on 2026-11-15. This replaces the
proposed-only contract from PR #73. It distinguishes implemented authority from
planned UI; it does not implement the remaining features. Portrait Hall,
portrait selection originated in the separate Portrait Hall PR
(#86), whose authenticated `/portraits` route reuses the contracts below; the other
remaining deliveries are unchanged.

See the [verified feature matrix and completion plan](arena-completion.md),
[current architecture](../../ARCHITECTURE.md), [design system](../DESIGN_SYSTEM.md)
and [original contract](https://github.com/freeDom-/hnefatafl/blob/c8b99c6fcaf6775e924e6f4eda65328a02f87f3b/docs/designs/arena-v2-contracts.md).
The original contract remains historical evidence, not the current feature list.

## Current boundaries

| Area | Authoritative implementation | Remaining delivery |
| --- | --- | --- |
| Rules, moves and repetition | Pure Python engine; persisted positions, moves and version checks | Browser execution must reuse the same rules, never decide official outcomes |
| Account single-player and local 1v1 | Account-owned games, separate categories, shared board and replay | Preserve private history and exclude local/AI results from online Elo |
| Online play | Online core, real clocks, side-swapped match legs and result revisions | Reuse for spectators, tournament legs and league games |
| Public online discovery | Three GET routes in `public_api.py`, enabled by configurable switch | Dedicated signed-out frontend outside authenticated initialization |
| Ratings | Immutable ledger and applied revisions, three pools | League progress is a separate policy, never an Elo replacement |
| Progression | Catalog, automatic achievements and portrait selection APIs | Portrait Hall and selection originated in PR #86; PR #94 automatically awards milestones; keep awards and selection server-owned |
| Human tournaments | Double elimination, schema 18, real casual online legs | UI, hierarchical bracket and reliable server settlement |
| Leagues | None | Explicit policy, queue, ledger, season lifecycle and UI |
| Workshop | Existing training, tuning, models, workers and AI tournaments | Visual consolidation, progressive disclosure and lazy loading |

## Routes and permissions

Current frontend routes are `/dashboard`, `/play`, `/games/{id}`, `/online`,
`/online/games/{id}`, `/archive`, `/statistics`, `/leaderboard`, `/profile`,
`/portraits`, `/training` and `/tournaments`. `/portraits` is delivered by PR #86.
`/online` is the separate authenticated lobby;
it does **not** redirect to Dashboard. `/tournaments` is administrative AI
benchmarking, never the human tournament page. The public API exists but there
is no signed-out spectator page. At the audited baseline, Portrait Hall, human
tournaments and leagues were sidebar placeholders; Portrait Hall is delivered by
PR #86.

Planned canonical frontend routes are `/player-tournaments`,
`/player-tournaments/{id}`, `/watch`, `/watch/games/{id}`, `/watch/games/{id}/replay` and
`/leagues`. These are reservations, not shipped routes; `/portraits` is no longer
planned and ships in PR #86. Shared navigation,
`safeDestination`, route focus and Browser Back handling have one integration
owner per PR. Public routes must render without mounting authenticated `App` or
making protected startup requests.

Backend permission checks remain authoritative. `/api/personal/*` always means
self; admin access does not turn it into a global query. Private single-player
and local replay require owner or trusted current administrator permission.
Public APIs expose online records and published player fields only. No active
online context, public or authenticated, exposes advisor evaluation, hints,
legal-move overlays or analysis generation. Public completed replay can consume
already-cached analysis only; it cannot request computation.

New lists use bounded opaque cursors and return a stable list field plus
`next_cursor: string | null`; list field names follow actual domain DTOs rather
than a fabricated universal `items` wrapper. Existing offset APIs may remain
until a measured need warrants replacement. Reuse 422 validation, 403 denial,
404 hidden/missing, 409 version conflict and 503 capacity responses.

## Appearance and visual contracts

The selectable UI themes are `kings-table`, `iron-ash`, `saga-hall` and
`classic-table`. King's Table is the default. Color mode and Classic look are
not selectable. Board, tokens, scale and language remain independent.

The current browser-local document is version 2 with `uiTheme`, `tokens`,
`board` and optional `density`. Absent density uses the CSS viewport: Large at
width >=3000 and height >=1600 CSS pixels, otherwise Default. Explicit scales
survive resize, zoom and reload. The shared catalog drives the external
synchronous first-paint script and React. PR #84's legacy-mode normalization
exists today; any later removal must keep first paint and React consistent and
preserve independent valid board/token/scale choices.

Use existing components, semantic variables, asset allowlists and four-theme
frames. Text and controls stay HTML. Maintain keyboard focus, reduced motion,
contrast, English/German labels, square playable boards and the persistent
desktop sidebar during gameplay/replay. Reference screenshots are in
[the acceptance record](../validation/arena-final-acceptance.md).

## Schema and data ownership

Current schema is **18**: 16 introduced account-game categories, 17 progression,
and 18 human tournaments. Schema 19 is reserved for leagues, not implemented.
One owner controls migrations and restore table inventories. Do not allocate
competing versions on parallel branches.

The project has no public installed-version compatibility requirement. This
supersedes PR #73's permanent route/DTO/appearance compatibility requirement.
It does **not** authorize deleting development games, account data, checkpoints,
training jobs or model files. Removal of an old format requires an identified
replacement, explicit break documentation, fresh-install tests and tested
backup/export/restore of valuable current data. Keep tensor-only checkpoint
validation, model-reference protection and optimistic game writes.

## Progression policy

Elo stays at initial 1500, K=32, provisional threshold 10, with separate Brandubh,
Tablut and Copenhagen pools. No inactivity decay; no local, single-player, AI or
casual tournament results contribute. Current and best rated streaks derive from
the applied result ledger; correction and void handling remain authoritative.

Portrait IDs and achievement criteria are code-owned. Authoritative event
transactions automatically award milestones; GET progress never awards. Explicit
portrait selection is CSRF-protected and idempotent. Existing earned awards remain permanent after later
corrections, while current progress may fall below the unlock threshold. Retired
portraits cannot be selected; effective display falls back to the catalog default.
The client uses the local portrait allowlist and never accepts an account URL.

### UTC day login/activity policy

Login/activity streaks are intentionally deferred. No durable UTC-day ledger was
added by schema 17. Do not display invented activity streaks or backfill dates.
Adding one requires an explicit eligibility policy and deduplicated server events.
This is distinct from existing rated win streaks.

## Competition policy

Human tournaments use `/api/player-tournaments/*` and their own tables;
`/api/tournaments/*` remains AI benchmarking. A participant is eliminated after
two **match** losses. Byes propagate without losses, and a lower-bracket finalist
win forces a grand-final reset. Existing higher-seed tie resolution, leg formats,
clocks, cancellation, audited overrides, revision disputes and rebuild semantics
are specified in [Player tournaments](../PLAYER_TOURNAMENTS.md). They are no
longer undecided. Tournament legs are real casual online games and never rated.

Settlement currently requires a start/settle/moderation call. The next tournament
PR must add bounded server-owned result settlement and startup catch-up so it
works with no browser open. Never advance winners in frontend state.

League tier names are Midgard, Nidavellir, Alfheim, Asgard and Valhalla. AI league
matching is disabled. Season/LP/matching defaults must be documented before schema
implementation; see the proposed policy in the completion plan. No fabricated
opponents, ranks, season history or league progress is permitted.

## Ownership and dependency gates

The completion plan assigns files and API seams before implementation. Independent
feature PRs target current `main`; dependencies must be reviewed/merged by the
owner, then dependent branches rebased on current main and revalidated. Agents
must not merge for convenience or create one accumulating integration PR.

Public UI and Portrait Hall can reuse existing APIs independently. Tournament UI
and its settlement service are one delivery boundary. Leagues require a stable
result-consumer contract. Performance work starts from measured bundle/request
baselines and retains account/session cancellation. Browser-local classical
analysis starts as a benchmark spike; production integration follows a separate
evidence gate. Neural execution is optional and must prove real-model correctness,
masking, memory and download costs before inclusion. Neither Pyodide nor neural
runtimes belong in the initial application bundle.

## Acceptance and release gates

Every runtime PR preserves game rules, account isolation, CSRF, permission
rechecks, revision correctness and safe local assets; includes unit/domain,
browser and theme/accessibility regressions; runs the established full CI; and
reports exact results and limitations. Never weaken assertions to obtain green CI.
Documentation-only PRs run local link checks; CI remains the complete pipeline.

Release preparation has its own [readiness checklist](../RELEASE_READINESS.md).
License selection belongs to the owner. No implementation PR automatically merges,
bumps a version, tags or publishes a release. Historical releases and benchmark
records are not recast as validation of new code.
