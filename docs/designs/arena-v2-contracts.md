# Arena v2 contracts and integration plan

Status: proposed architecture contract only. This document freezes additive contracts and merge boundaries for the Arena v2 program without changing runtime behavior. It is based on an audit of the current repository head and the handoff pack under `/root/hnefatafl-frontend-handoff-v1`.

## Scope and non-goals

This contract PR covers documentation only:

- preserves current authenticated shell, gameplay, workshop and routing behavior
- freezes additive API, migration and ownership decisions for future agents
- records deferred product decisions instead of inventing shipped capabilities

It does not:

- implement public online spectators, local 1v1, portraits, achievements, player tournaments or leagues
- change `server.py`, migrations, React runtime code, CSS, rules, training or persistence
- approve any compatibility break to existing `/games/{id}`, `/online/games/{id}`, `/archive`, `/dashboard`, `/statistics`, `/leaderboard`, `/training` or `/tournaments`

## Audit baseline

### Frontend entry points audited

- `frontend/src/App.tsx`
  - authenticated shell route selection for `dashboard`, `play`, `statistics`, `leaderboard`, `archive`, `profile`, `training`, `tournaments`, and `/online/games/{id}`
  - persists last play route under `hnefatafl-play-route`
  - keeps `/games/{id}` single-player deep links authoritative
- `frontend/src/SiteHeader.tsx`
  - current nav labels and permission-gated `training` / `tournaments`
- `frontend/src/Appearance.tsx`
  - browser-local appearance provider and `document.documentElement.dataset.*` application
  - persistence key `hnefatafl-appearance`
- `frontend/src/appearanceConfig.ts`
  - typed catalogs for `mode`, `tokens`, `board`, `density`
  - current defaults: `system` + `wood` + `walnut` + `default`
- `frontend/src/PersonalDashboard.tsx`
  - authenticated landing page with embedded online lobby, compact leaderboard and latest recorded games
- `frontend/src/Board.tsx`
  - shared board renderer for live play and review; no online assistance built into board component
- `frontend/src/Tokens.tsx`
  - catalog-backed token art renderers keyed by `TokenTheme`
- `frontend/src/RecordedReview.tsx`
  - shared read-only replay for account single-player and online categories
  - completed online review may use the online-scoped analysis adapter only after authorization
- `frontend/src/Archive.tsx`
  - Hall filter UI and direct selected review routing via query params
- `frontend/src/Leaderboard.tsx`
  - authenticated ruleset leaderboard with browser-local ruleset persistence key `hnefatafl-leaderboard-ruleset`
- `frontend/src/PersonalStatistics.tsx`
  - personal stats view, including per-AI-opponent single-player breakdowns
- `frontend/src/Online.tsx`
  - authenticated online lobby and game UX with match modes, clock modes, increment handling and optimistic mutation semantics
- `frontend/src/Training.tsx`
  - workshop shell and controls for training jobs, tuning, import/export, resume, workers and model management
- `frontend/src/Tournaments.tsx`
  - existing AI test tournament UI; this namespace must not be repurposed for player competitions
- `frontend/src/api.ts`
  - CSRF/session epoch guard and protected request cancellation semantics

### Backend and contract seams audited

- `hnefatafl/server.py`
  - account single-player, analysis, replay, tournaments, models and training routes
  - online analysis adapter under `/api/history/online/{game_id}/analytics` and `/analysis`
- `hnefatafl/online_api.py`
  - authenticated online routes under `/api/online/*` plus authenticated `/api/leaderboard`
- `hnefatafl/online_core.py`
  - participant checks, listing/read/replay, leaderboard, timeout gate, statistics
- `hnefatafl/online_ratings.py`
  - authoritative Elo event/application ledger and leaderboard projection
- `hnefatafl/personal_api.py`
  - authenticated own-account reads under `/api/personal/*` and read-only Hall routes under `/api/history/*`
- `hnefatafl/history.py`
  - Hall listing/review authorization and account-vs-online category split
- `hnefatafl/online_migrations.py`
  - current schema version `16`; online/rating tables and the account-game category column already present
- `hnefatafl/profile_migrations.py`
  - profile publication and privacy exception migration
- `hnefatafl/account_migrations.py`
  - account schema migration baseline
- `hnefatafl/auth.py` and `docs/SECURITY.md`
  - route permission model and session/CSRF constraints

## Feature preservation inventory

The following current behavior is contractually preserved unless a later approved implementation PR explicitly adds to it.

### Current pages, routes and deep links

| Surface | Current route(s) | Preservation requirement |
| --- | --- | --- |
| Authenticated landing | `/dashboard` | remains default post-login landing |
| Play landing | `/play` | remains account-game launcher/recovery surface |
| Single-player live game | `/games/{id}` | remains canonical single-player deep link |
| Online live game | `/online/games/{id}` | remains canonical online deep link |
| Statistics | `/statistics` | remains personal authenticated stats surface |
| Leaderboard | `/leaderboard` | remains authenticated leaderboard route |
| Hall / archive | `/archive` and query-based review selection | preserved, including selected review query model |
| Profile | `/profile` | remains account/profile/appearance surface |
| Training workshop | `/training` | remains admin/authorized workshop route |
| AI test tournaments | `/tournaments` | remains existing workshop tournament route; do not reuse for player tournaments |
| Personal dashboard single-player review | `/dashboard/single-player/{id}` | preserve existing personal deep link |
| Legacy online setup entry | `/online` | continues redirecting to Dashboard online setup |

### Current frontend controls to preserve

- Language selector in `SiteHeader.tsx`, browser-local language persistence, English/German presentation only.
- Appearance controls in `Appearance.tsx` and `ProfilePage.tsx` for:
  - `mode`: `system`, `light`, `dark`, `wood`
  - `tokens`: `classic`, `viking`, `wood`
  - `board`: `oak`, `walnut`, `slate`
  - `density`: `compact`, `default`, `large`
- Dashboard embedded online lobby tabs: `all`, `mine`, `my_turn`.
- Game creation actions: `New single-player game`, `New online game`. The standalone
  `Resume single-player` action was intentionally replaced by the `PlayModes` continue list
  (`Continue` per running game).
- Recorded review replay controls and playback.
- Leaderboard ruleset selector and compact/full variants.
- Workshop controls for job creation, tuning, resume, import/export, deletion preview, worker management and model usage/deletion confirmation.
- AI tournament controls in `Tournaments.tsx`, including replay and diagnostics.

### Current API surfaces to preserve

#### Single-player / local account game APIs

From `hnefatafl/server.py`:

- `GET /api/rulesets`
- `GET /api/ai/profiles`
- `POST /api/games`
- `GET /api/games`
- `GET /api/games/{game_id}`
- `GET /api/games/{game_id}/analytics`
- `POST /api/games/{game_id}/analysis`
- `DELETE /api/games/{game_id}`
- `GET /api/games/{game_id}/replay`
- `POST /api/games/{game_id}/moves`
- `POST /api/games/{game_id}/ai-move`
- `POST /api/games/{game_id}/seek`
- `POST /api/games/{game_id}/undo`
- `POST /api/games/{game_id}/redo`
- `POST /api/games/{game_id}/restart`
- `GET /api/stats`

Preserve optimistic `version` semantics, authoritative replay, read-only history review and analysis separation.

#### Personal and Hall read APIs

From `hnefatafl/personal_api.py`:

- `GET /api/history/games`
- `GET /api/history/{category}/{game_id}/replay`
- `GET /api/personal/single-player`
- `GET /api/personal/single-player/{game_id}/replay`
- `GET /api/personal/online`
- `GET /api/personal/online/{game_id}`
- `GET /api/personal/online/{game_id}/replay`

Preserve category split `online` vs `account-single-player`, current Hall cursor shape and existing personal scoping.

#### Online multiplayer APIs

From `hnefatafl/online_api.py`:

- `POST /api/online/games`
- `GET /api/online/games`
- `GET /api/online/availability`
- `GET /api/online/games/{game_id}`
- `POST /api/online/games/{game_id}/join`
- `POST /api/online/games/{game_id}/cancel`
- `POST /api/online/games/{game_id}/moves`
- `POST /api/online/games/{game_id}/concede`
- `POST /api/online/games/{game_id}/void`
- `GET /api/online/games/{game_id}/replay`
- `GET /api/online/stats`
- `GET /api/leaderboard`
- `POST /api/online/ratings/rebuild`
- `GET /api/online/timeout-gate`
- `GET /api/online/monitoring`
- `PATCH /api/online/timeout-gate`

Preserve:

- match modes `single`, `simultaneous`, `sequential`
- time controls `per_move` and `total`
- integer `increment_seconds`
- active-game recovery through listing/read/replay
- optimistic revision guard through `version`
- result correction/void path using result revisions rather than rewriting moves
- no live online analysis or assistance

#### Completed online analysis adapter

From `hnefatafl/server.py`:

- `GET /api/history/online/{game_id}/analytics`
- `POST /api/history/online/{game_id}/analysis`

Preserve completed-only authorization and no active online assistance bypass.

#### Workshop / model / AI tournament APIs

Preserve the current workshop and AI tournament namespaces and semantics under:

- `/api/tournaments*`
- `/api/models*`
- `/api/training/*`

Arena v2 player competitions must not overload existing AI test tournament DTOs or storage.

### Behavior inventory of high-risk preserved features

#### Game clocks and two-leg matches

Current audited behavior in `frontend/src/Online.tsx`, `docs/ONLINE_PLAY.md`, `hnefatafl/online_api.py`, `hnefatafl/online_migrations.py`:

- `per_move` and `total` are persisted modes.
- `increment_seconds` applies only to total-time mode.
- simultaneous and sequential two-game matches swap sides across legs.
- each game retains its own status/version/result and clocks.
- no aggregate match winner is projected today.

Arena v2 additions must not reinterpret stored online clock fields or side-swapped legs.

#### Active-game recovery

Current recovery paths:

- dashboard online lobby reads `/api/online/games`
- personal online reads `/api/personal/online`
- `/play` and `/dashboard` restore account-owned unfinished single-player games
- online intent stored in `sessionStorage` under `hnefatafl-online-intent`
- play route stored in `localStorage` under `hnefatafl-play-route`

Arena v2 shell work must preserve these keys and behaviors.

#### Evaluation and analysis

- single-player retains existing advisor/evaluation controls.
- online review analysis is allowed only for completed games through the online-scoped history adapter.
- active online games must continue to expose no hints, no evaluations, no win probabilities, no move advisor.

#### Completed replay authorization

- single-player replay: owner or admin only.
- personal online replay: participant-scoped.
- Hall online replay: participant-scoped for normal users, all-data admin for authorized admins.
- authenticated online lobby read is broader than personal replay but still authenticated.

#### AI opponent stats

`frontend/src/PersonalStatistics.tsx` currently exposes single-player per-AI-opponent grouped stats. Arena v2 stats work must preserve seed-independent AI grouping and not mix AI/local outcomes into online Elo history.

#### Workshop controls

`frontend/src/Training.tsx` currently owns:

- training creation and validation
- tuning creation and reuse
- resume flows
- job import/export and rename/delete
- model import/export, rename, delete, usage preview
- remote worker listing and management entry

`frontend/src/Tournaments.tsx` currently owns AI-vs-AI benchmark/tournament administration. Arena v2 “Workshop” IA may reskin or reorganize, but cannot drop these controls without an explicit replacement PR.

#### Language and appearance controls

Preserve all current appearance dimensions and language selection independently. Arena v2 theme work is additive, not a reinterpretation of current appearance data.

## Arena + Workshop IA freeze

### Information architecture

Arena v2 freezes two top-level product groupings without changing route authority:

- **Arena**: Dashboard, Play, Online gameplay, Statistics, Leaderboard, Hall, future portraits/competitions
- **Workshop**: Training, models, AI test tournaments, remote workers, future workshop-specific diagnostics

Route preservation rule:

- existing routes remain valid and canonical at rollout
- future navigation labels may group them under Arena/Workshop, but existing URLs continue to resolve
- any new shell-level grouping must be presentation-only until a separately approved compatibility transition exists

### Frozen UI theme catalog

Adopt the handoff theme IDs exactly:

- `kings-table`
- `iron-ash`
- `saga-hall`

These are app-shell themes only. They do not replace or rename current persisted appearance fields.

### Additive `uiTheme` persistence contract

Current persisted browser shape in `frontend/src/appearanceConfig.ts`:

```json
{
  "mode": "system|light|dark|wood",
  "tokens": "classic|viking|wood",
  "board": "oak|walnut|slate",
  "density": "compact|default|large"
}
```

Frozen additive plan:

- keep `hnefatafl-appearance` as the sole browser-local appearance document
- extend the stored JSON shape with optional field `uiTheme`
- do not rename or reinterpret `mode`, `tokens`, `board`, or `density`
- old stored values remain valid and authoritative for those fields

Proposed future type:

```ts
interface AppearanceV2 {
  uiTheme?: 'kings-table' | 'iron-ash' | 'saga-hall'
  mode: 'system' | 'light' | 'dark' | 'wood'
  tokens: 'classic' | 'viking' | 'wood'
  board: 'oak' | 'walnut' | 'slate'
  density: 'compact' | 'default' | 'large'
}
```

Migration rules:

1. Missing `uiTheme` is **not** written and does **not** set `data-ui-theme`. The picker's catalog default is `kings-table`, but with no attribute the existing `mode`/`tokens`/`board`/`density` surface treatment (including `wood`) applies unchanged. This supersedes the earlier wording that a missing value "defaults to `kings-table`" as an always-applied attribute: the additive layer is applied only for an explicit, valid selection.
2. Invalid `uiTheme` is ignored and left unset, so the stored document keeps working field-by-field; it is not silently coerced to a theme.
3. Invalid existing `mode`/`tokens`/`board`/`density` continue to fall back exactly as `parseAppearance()` already does.
4. Stored `mode: 'wood'` remains a valid color mode and is not converted into `saga-hall`.
5. `wood` color mode continues to own current surface treatment semantics until a later implementation deliberately layers `uiTheme` and `mode` together. Selecting a named theme is that deliberate layering: the named theme then owns the shell surface variables while `mode`/`tokens`/`board`/`density` stay independent.
6. Unsupported old future values must be ignored field-by-field, not wipe the entire object.

Compatibility note:

- because appearance is browser-local, no database migration is needed for this preference itself
- if a future server-owned profile mirror is ever added, it must still preserve the independent meaning of `uiTheme` vs `mode/tokens/board/density`

## Public online visibility decision

### Frozen default for Arena v2

Signed-out public visibility is **deferred and not approved in this contract PR**.

Recommended default for implementation planning:

- keep current authenticated-only online read policy as the shipped default
- design future public-read endpoints additively under a distinct namespace so the decision can be enabled later without breaking authenticated clients

Reason:

- the audited current implementation and docs explicitly state authenticated visibility only (`docs/ONLINE_PLAY.md`)
- changing to anonymous public replay now would be a product and security expansion, not a documentation clarification
- the product owner requested that this can be deferred rather than invented

### Contract for future additive public-read work

If public online read-only visibility is later approved, it must follow all of the following:

- online only; never single-player or local 1v1
- read-only; no joins, moves, concede, void, analysis generation or private personal stats
- no active-game assistance, no move legality hints, no win-chance service, no expensive uncached analysis generation
- no exposure of email, account IDs, private notes, unpublished profile fields, full date of birth, session state or CSRF details
- no broadening of `/api/personal/*`

### Visibility matrix frozen for future implementation

| Surface/data | Signed-out | Signed-in nonparticipant | Participant/owner | Trusted admin with current permission |
| --- | --- | --- | --- | --- |
| Single-player game detail/replay | No | No | Owner only | Yes |
| Local 1v1 detail/replay (future) | No | No | Owner only | Yes |
| Active online game detail | Deferred; default No | Current authenticated policy only | Yes | Yes |
| Completed online replay | Deferred; default No | Current authenticated policy only | Yes | Yes |
| Active online analysis | No | No | No | No |
| Completed online analysis generation | No | No unless separately authenticated and authorized | Authorized participant/admin only | Authorized |
| Personal stats/progress | No | No | Self only | No broadened personal scope |
| Workshop/training/model/AI tournament data | No | No | Authorized permission only | Authorized |

### Access checks, pagination and rate-limit freeze for future public endpoints

If public-read endpoints are implemented later:

- use a distinct namespace: `/api/public/online/*`
- cursor pagination only for public index; do not expose offset scans for anonymous browsing
- limit `1..100`
- opaque cursor bound to filter set and sort key
- return 404 for missing/unauthorized records
- apply explicit route/IP rate limits stricter than authenticated lobby polling
- no cache-busting polling cadence lower than current authenticated lobby cadence without separate approval
- no response field may reveal whether a hidden game exists for a guessed ID

### Admin revocation and account switch rules

Preserve existing backend-authoritative session behavior:

- admin role revocation immediately removes wider Hall visibility on subsequent requests
- delayed responses after logout/account switch must remain dropped by the existing session-epoch cancellation in `frontend/src/api.ts`
- frontend role checks remain advisory only; backend permission rechecks stay authoritative

## Frozen additive endpoints and DTO proposal

This section freezes names for future Arena v2 additions while preserving existing routes.

### Naming constraints from audit

- do not reuse `/api/tournaments` because it already means AI test tournaments
- do not overload `/api/history/*` with public anonymous scope because it is currently authenticated Hall/personal review space
- keep `/api/leaderboard` unchanged for the existing authenticated Elo leaderboard
- keep `/api/online/*` unchanged for live authenticated online play

### Additive endpoints reserved for future work

#### Public online browsing

Reserved, not yet implemented:

- `GET /api/public/online/games`
- `GET /api/public/online/games/{game_id}`
- `GET /api/public/online/games/{game_id}/replay`

#### Local 1v1 under account ownership

Reserved, not yet implemented:

- `POST /api/games/local`
- `GET /api/personal/local`
- `GET /api/personal/local/{game_id}/replay`

Rationale: additive alongside existing account single-player routes, avoiding mutation of online namespaces.

#### Personal rating progress

Reserved, not yet implemented:

- `GET /api/personal/ratings/{ruleset}/history`
- `GET /api/personal/progress`

#### Portrait catalog and achievements

Reserved, not yet implemented:

- `GET /api/portraits/catalog`
- `GET /api/personal/achievements`
- `PATCH /api/profile/portrait`

#### Player competitions

Reserved, not yet implemented:

- `GET /api/player-tournaments`
- `POST /api/player-tournaments/{tournament_id}/register`
- `GET /api/player-tournaments/{tournament_id}`
- `GET /api/player-tournaments/{tournament_id}/bracket`
- `GET /api/player-tournaments/{tournament_id}/matches`
- `GET /api/leagues/current`
- `GET /api/leagues/current/standings`
- `GET /api/leagues/matches`
- `POST /api/leagues/queue`

### DTO and optimistic revision rules

#### Existing optimistic semantics preserved

- single-player and online live mutations continue using explicit `version`
- online result correction remains revision-driven in storage; new features must not rewrite accepted move history
- void remains an ineligible correction that can withdraw rating effects

#### New pagination freeze

- existing authenticated online and leaderboard routes may keep current offset pagination
- every new additive list endpoint in Arena v2 must use opaque cursor pagination unless it is an extension of an already-offset endpoint
- cursor responses must use `next_cursor: string | null`
- empty page shape returns `items: []` and `next_cursor: null`, never omitted list fields

#### Error contract freeze

For new endpoints, reuse current API conventions from `frontend/src/api.ts` and server behavior:

- structured JSON errors may include `detail`, optional `code`, optional `fields`
- validation errors return `422`
- permission failures return `403`
- missing/hidden records return `404`
- concurrency conflicts return `409`
- temporary service capacity returns `503` with optional retry guidance

#### OpenAPI / TypeScript proposal

These are documentation-only frozen proposals for future additive work.

```ts
export type UiTheme = 'kings-table' | 'iron-ash' | 'saga-hall'

export interface PublicOnlineListItem {
  id: string
  ruleset: 'brandubh' | 'tablut' | 'copenhagen'
  status: 'waiting' | 'active' | 'completed' | 'cancelled' | 'voided'
  mode: 'single' | 'simultaneous' | 'sequential'
  leg: 1 | 2
  created_at: string
  recorded_at: string
  participants: {
    attackers: PublicOnlineParticipant | null
    defenders: PublicOnlineParticipant | null
  }
  result: PublicOnlineResult | null
}

export interface PublicOnlineParticipant {
  display_name: string
  username?: string
  portrait_id?: string | null
  start_rating?: number | null
}

export interface PublicOnlineResult {
  winner: 'attackers' | 'defenders' | 'draw'
  termination_reason: string | null
  rated: boolean
  winner_delta?: number | null
}

export interface CursorPage<T> {
  items: T[]
  next_cursor: string | null
}

export interface RatingHistoryEntry {
  sequence: number
  game_id: string
  recorded_at: string
  rating_after: number
  delta: number
  opponent_display_name: string
  opponent_username?: string
  result: 'win' | 'loss' | 'draw'
  corrected: boolean
}

export interface RatingHistoryPage extends CursorPage<RatingHistoryEntry> {
  ruleset: 'brandubh' | 'tablut' | 'copenhagen'
  policy_version: number
  current_rating: number | null
}

export interface PersonalProgress {
  streaks: {
    rated_win_current: number
    rated_win_best: number
  }
  activity: {
    utc_today_active: boolean
    current_login_days: number
    best_login_days: number
  }
  selected_portrait_id?: string | null
}
```

### Migration IDs and database sequencing freeze

Current audited database schema version is `16` in `hnefatafl/online_migrations.py`.

Frozen sequencing for future additive schema work:

- schema `16`: local 1v1 account-owned history (consumed by the B2 local 1v1 branch: the additive `games.category` column plus the `games_category_owner` index)
- schema `17`: Arena v2 progression foundations
  - portrait catalog / selected portrait / achievement ledger / activity-day ledger if approved
- schema `18`: player tournaments
- schema `19`: leagues

Schema `16` was originally reserved for progression foundations but shipped with
the local 1v1 branch first. An already deployed migration is never renumbered, so
progression work takes the next free number (`17`) instead of reclaiming `16`.

Do not create parallel PRs that both claim the same schema number. If branches diverge, later branches must rebase and renumber before merge, and a merged/shipped migration keeps its number regardless of which branch reserved it first.

## Progression policy freeze

### Elo policy preserved

Per `docs/RATINGS.md` and `hnefatafl/online_ratings.py`, preserve exactly:

- Elo
- initial rating `1500`
- K-factor `32`
- provisional threshold `10`
- pools `brandubh`, `tablut`, `copenhagen`
- no inactivity decay
- no local/single-player/AI contribution

Arena v2 may project history or league overlays, but must not change these pools in the same feature set.

### Streak eligibility decision

Freeze for future implementation:

- **rated win streaks** count only completed, competitive-eligible, rated online games
- tracked per ruleset and optionally aggregate-only if a later product decision approves it
- void/correction must recompute streaks authoritatively from effective results, not patch incrementally without rebuild support
- single-player, local 1v1, AI test tournaments and workshop results do not affect rated streaks

### UTC day login/activity policy

Freeze for future implementation:

- login streak is based on one successful authenticated account login per UTC day
- multiple logins on the same UTC day are idempotent
- activity streak, if implemented, must be a separate metric from login streak
- recommended activity default: one completed eligible online game or one started account-owned game action in a UTC day
- do not backfill historical streaks from old sessions without authoritative event evidence

### Portrait catalog policy

Freeze for future implementation:

- portraits are server-owned allowlisted IDs, not user URLs
- at least one default portrait is always selectable
- selected portrait mutation is idempotent
- server rejects unknown, retired or unearned IDs
- catalog delivery should expose stable IDs and metadata only; asset mapping stays frontend-controlled and bundled

### Achievement policy

Freeze for future implementation:

- achievements are granted from server-verified events only
- award writes must be idempotent across retries, duplicate notifications and corrected results
- no paid currency and no economy fields
- if a correction invalidates a source event, the default policy is: unlocks remain permanent unless an achievement explicitly documents revocable status
- revocable achievements, if ever added, require explicit per-achievement metadata and auditability

## Competition policy freeze

### Player tournaments

Decision: reserve a **separate** player-tournament system and API namespace from existing workshop AI tournaments.

Frozen rules:

- format family approved: double elimination
- elimination requires two match losses, not a single game loss
- grand final bracket reset is required if the lower-bracket finalist hands the upper-bracket finalist a first match loss
- actual per-match game format, clocks and two-leg tie rules are deferred product decisions
- tournament state transitions must key from settled authoritative match results, not client bracket math

### Leagues

Frozen defaults for future implementation:

- tier names: `Midgard`, `Nidavellir`, `Alfheim`, `Asgard`, `Valhalla`
- leagues are separate from Elo and must not alter Elo K-factor/pools
- AI league participation is disabled by default until calibration and anti-farming policy are approved

Deferred league decisions are listed below instead of invented.

## Frontend ownership and integration points

### F1 shell ownership

F1 owns shared shell and theme integration:

- `frontend/src/App.tsx`
- `frontend/src/SiteHeader.tsx`
- `frontend/src/Appearance.tsx`
- `frontend/src/appearanceConfig.ts`
- global shell CSS/theme token integration points

F1 responsibilities:

- add `uiTheme` parsing/persistence only when implementation is approved
- preserve current routes, account menu, language selector and role gating
- establish shared Arena/Workshop navigation grouping without breaking deep links

### F2 play ownership

F2 owns play and replay surfaces:

- `PersonalDashboard.tsx` embedded game creation affordances as needed
- `Online.tsx`
- `PlayPage.tsx`
- `RecordedReview.tsx`
- `Archive.tsx`
- `Board.tsx` only for presentation-safe additive changes

F2 must preserve online no-assistance rules and replay authorization boundaries.

### F3 stats ownership

F3 owns stats/rankings/progress:

- `PersonalStatistics.tsx`
- `StatisticsPage.tsx`
- `Leaderboard.tsx`
- future Elo history/progress UI

F3 must not change authoritative rating math.

### F4 portraits ownership

F4 owns portrait gallery/profile rendering only after B4 contract merge.

### F5 compete ownership

F5 owns player tournament and league UI only, never workshop AI tournaments.

### F6 workshop ownership

F6 owns workshop reorganization/styling around current training/model/tournament/worker runtime APIs:

- `Training.tsx`
- `Tournaments.tsx`
- shared workshop navigation inside shell

## Backend branch plan and safe merge order

### Concurrent backend branches

To avoid conflicting migrations and auth/router collisions:

1. **B1 public visibility branch**
   - routes only under `/api/public/online/*`
   - no schema change unless a public index requires indexes only
   - must not alter `/api/online/*` authentication behavior by default
2. **B2 local 1v1 branch**
   - add account-owned local mode under game/history APIs
   - shipped as schema `16` (`games.category`); see the migration sequencing freeze above
3. **B3 progress branch**
   - add rating history/progress projections from existing ledger
   - reserved schema `17` only if new activity-day ledger is approved
4. **B4 portraits/achievements branch**
   - builds on the progress schema (`17`)
5. **B5 player tournaments branch**
   - separate namespace and schema `18`
6. **B6 leagues branch**
   - schema `19`

### Merge order

1. This architecture contract PR
2. F1 shell
3. B1/B3 in either order if no schema collision
4. B2 after rebasing on any earlier schema change
5. B4
6. B5
7. B6
8. feature UIs F2/F3/F4/F5/F6 rebased onto merged backend contracts

### Required tests per branch family

- B1: security/auth visibility matrix, pagination bounds, no active online analysis leak, public route denial for single-player/local
- B2: owner/admin-only replay, no Elo/stat mixing, migration upgrade coverage
- B3: deterministic Elo history against rating ledger, void/correction recomputation, UTC-day dedupe tests
- B4: idempotent unlock award tests, profile portrait authorization, retired/unknown portrait rejection
- B5: bracket progression, byes, double-loss elimination, reset logic, correction handling
- B6: season rollover, queue/match accounting, promotion/demotion idempotence, AI-disabled default
- F1/F2/F3/F4/F5/F6: route compatibility, storage compatibility, localization and responsive coverage

## Transition and migration plan

### Phase 0: contract merge

- merge this documentation PR only
- no runtime change

### Phase 1: shell/theme groundwork

- add additive `uiTheme` support in browser parsing
- import handoff assets under a project-controlled public path
- preserve existing appearance data and routes

### Phase 2: data/API additions

- implement new endpoints additively, never by silently broadening `/api/personal/*`
- preserve existing authenticated online and Hall behaviors while new public or progress APIs come online

### Phase 3: gradual UI adoption

- Arena/Workshop grouping becomes visible in shell
- existing deep links continue to work
- future public online browsing, local 1v1, progress and portraits consume their own additive APIs

### Phase 4: competitions

- introduce player tournaments and leagues under distinct namespaces and nav labels
- keep workshop AI tournaments untouched except for shell placement

## Acceptance criteria for future implementation PRs

A feature PR in the Arena v2 program is acceptable only if it satisfies all relevant items:

1. No existing route or API listed in the preservation inventory loses compatibility without an explicit approved transition.
2. No implementation reinterprets `hnefatafl-appearance.mode = 'wood'` as a `uiTheme`.
3. Existing authenticated online play keeps backend-enforced no-assistance rules for active games.
4. Single-player and local 1v1 remain owner/admin-only in both list and replay detail paths.
5. Existing Elo pools and K-factor remain unchanged.
6. Existing workshop AI tournament APIs remain separate from player competition APIs.
7. New migrations use the reserved sequence or are renumbered on rebase before merge.
8. Frontend account/session race protections in `frontend/src/api.ts` remain intact.

## Deferred product decisions

These are intentionally not invented here.

1. **Signed-out public online visibility**
   - recommended default: keep authenticated-only until separately approved
2. **Public completed-game analysis policy**
   - recommended default: no anonymous analysis generation; only cached/read-only projections if later approved
3. **Local 1v1 exact stats category naming**
   - recommended default: explicit `local` category separate from `account-single-player`
4. **League season cadence and season reset rules**
   - recommended default: defer until B6 design, do not create schema first
5. **League LP formula and promotion/demotion thresholds**
   - recommended default: defer
6. **AI calibration for league matches**
   - recommended default: disabled until benchmarked per ruleset
7. **Player tournament match format details**
   - recommended default: defer exact leg/clock policy while keeping double-elimination + reset invariant
8. **Achievement catalogue content**
   - recommended default: start with idempotent milestones tied to rated online participation only
9. **Portrait unlock permanence vs revocation**
   - recommended default: permanent unless explicitly documented revocable
10. **Whether rating history should expose exact event timestamps or only recorded result timestamps when sequence order is authoritative**
   - recommended default: expose trusted recorded result timestamps and explicit sequence

## PR graph

```text
A0 docs/arena-v2-contracts
├─ F1 shell/theme groundwork
├─ B1 public online visibility
├─ B2 local 1v1
├─ B3 rating history + streak progress
│  └─ B4 portraits + achievements
├─ B5 player tournaments
│  └─ B6 leagues
├─ F2 play/replay integration (after F1 + B1/B2)
├─ F3 stats/leaderboard/progress (after F1 + B3)
├─ F4 portraits UI (after F1 + B4)
├─ F5 competitions UI (after F1 + B5/B6)
└─ F6 workshop IA (after F1)
```
