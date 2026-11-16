# Earnable portraits and achievement progress

[Documentation index](README.md) · [Rated games](RATINGS.md) · [Profile](PROFILE.md) ·
[Online core](ONLINE.md) · [Accounts and API security](SECURITY.md)

Schema **17** adds the Arena v2 progression foundations: a server-owned portrait
catalog, per-account portrait entitlements and a permanent achievement ledger.
Awards are derived only from authoritative server records, and a client can never
grant itself a reward, supply a portrait image or read another account's progress.

Catalog **2**, schema **20** extend these foundations with nine tiered families,
76 milestones and server-owned achievement points. The unrelated Fireside Game
remains available for 10 points: **77 milestones, 6,290 points** overall. Schema 19
remains reserved for leagues. Portrait catalog version 1 and every existing unlock
ID are unchanged.

## Scope and non-goals

Delivered:

- `GET /api/portraits/catalog` — stable catalog IDs and metadata
- `GET /api/personal/achievements` — read-only own progress and confirmed awards
- `POST /api/personal/achievements/confirm` — CSRF-protected confirmation of the
  awards the authoritative records already prove
- `PATCH /api/profile/portrait` — CSRF-protected selection of an earned portrait

Deliberately out of scope: paid currency, any XP or soft-currency economy, client
awarded unlocks and login/activity streaks. Human player tournaments are a separate
implemented backend using schema 18; leagues remain unimplemented (schema 19
reserved). Portrait Hall, achievement claiming and selection are implemented; the
post-#84 audit baseline predates them.

## Portrait Hall and player artwork

The authenticated `/portraits` page shows every catalog portrait in a locked,
ready-to-claim, unlocked or selected state. Requirements and actual numeric
progress come from the server, including achievements without a portrait reward.
The explicit **Claim eligible achievements** action confirms all currently
eligible awards through the existing idempotent POST; reading progress does not
claim. Earned portraits can be selected using the existing profile PATCH.

Mutation responses update the gallery immediately. Account-switch/unmount guards
reject stale responses and notices; the existing payload-free `profile-changed`
event refreshes owner-scoped views. Profile displays the selected artwork and links
to the gallery; the Dashboard's Unlocked portraits heading links there too. Safe
published portrait IDs are included in online and leaderboard projections, with
batched page lookup and retired/default fallback. No achievement progress or
private profile field is included in those published projections.

Artwork uses the existing local allowlist and original 320px portrait assets;
unknown IDs and failed loads have a safe fallback. Four themes, explicit progress
labels, keyboard controls, visible focus, responsive cards and accessible unlock
notices use the existing design system. There is no XP, currency or client-awarded
entitlement. Existing confirmed awards remain earned after later result correction.

## Portrait catalog

IDs are the contract; display names and artwork are not. The frontend bundles the
artwork mapping and may localize labels, exactly like the ruleset catalog.

| ID | Name | Unlocked by |
| --- | --- | --- |
| `shieldbearer` | Shieldbearer | always available default |
| `shieldmaiden` | Shieldmaiden | `first-rated-win` |
| `iron-helm` | Iron Helm | `defender-hold` |
| `wanderer` | Wanderer | `brandubh-initiate` |
| `skald` | Skald | `tablut-initiate` |
| `fjord-scout` | Fjord Scout | `copenhagen-initiate` |
| `raven-seer` | Raven Seer | `rated-wins-10` |
| `berserker` | Berserker | `rated-win-streak-5` |
| `one-eyed-jarl` | One-Eyed Jarl | `rated-win-streak-10` |
| `valkyrie` | Valkyrie | `defender-wins-10` |
| `einherjar` | Einherjar | `rated-wins-25` |
| `forge-keeper` | Forge Keeper | `ai-first-victory` |

Policy:

- exactly one non-retired default portrait is declared in code, so the catalog can
  never lose its always-available default (see
  [catalog integrity guarantees](#catalog-integrity-guarantees))
- a client sends a catalog ID only; there is no image, URL, path or MIME field
- unknown and retired IDs are rejected (`422`); an ID that is not earned yet is
  rejected (`403`)
- re-selecting the current portrait is idempotent and returns the same projection
- a stored selection that the current catalog retires is no longer published; the
  default is published instead, and the portrait disappears from the catalog
- an ID is never renumbered or reused; a retired portrait keeps its entitlement
  history, so a later catalog change cannot silently reinterpret an old grant
- the catalog response excludes retired portraits, so it is never a list of
  unselectable IDs

## Catalog integrity guarantees

Uniqueness and existence are enforced by different layers, and neither can silently
invalidate a stored selection:

- **uniqueness is enforced by the database**: catalog IDs are `PRIMARY KEY`s, the
  award and entitlement tables key on `(account_id, achievement_id)` and
  `(account_id, portrait_id)`, and the partial unique index `portrait_catalog_default`
  allows **at most one** non-retired always-available portrait. It can reject a
  second default, but it cannot by itself guarantee that one exists.
- **existence and validity are enforced by the validated code catalogs plus startup
  seeding**: `_validate_catalogs()` runs at import and fails loudly if IDs are not
  unique, if a criterion/category/scope is unknown, if a threshold is not positive,
  if a portrait unlocks an unknown achievement, or if there is not **exactly one**
  non-retired default. `seed_catalog()` then mirrors both catalogs into the database
  on every startup with `INSERT ... ON CONFLICT DO UPDATE` and **never deletes a row**,
  so a catalog row for a stored reference always exists and IDs are never removed.

What that means for a stored selection (`account_portrait_selection`):

- **evolution** (a renamed portrait, a changed unlock) updates the catalog row in
  place without touching any ID, so the stored selection still resolves and its
  entitlement history is unchanged
- **retirement** only sets `retired=1`; the row and the selection are retained, so
  the stored selection is unpublished while retired (the default is published
  instead) and would resolve again if the portrait were ever un-retired. `select()`
  rejects a retired ID, so it can never be re-selected in the meantime.
- **default replacement** must keep the previous default as a retired entry: if a new
  default were seeded while the old default row is still non-retired, the partial
  unique index aborts the startup loudly instead of leaving the catalog without a
  usable default. `seed_catalog()` writes the default last so the retiring and
  replacement rows never both count as non-retired within one transaction.
- a stored selection whose portrait is somehow absent from the catalog still falls
  back to the default, because `selected_portrait_id` joins `portrait_catalog` rather
  than trusting the raw id.

No schema migration is added for this: the existing constraints and the code
catalog already cover the invariants, so there is no actual failure to repair.

## Achievement catalog

The declarative catalog in `hnefatafl/progression.py` defines incremental points.
Eight families use the ordinary schedule: rated online victories; rated Brandubh,
Tablut and Copenhagen victories; rated victories as defender and attacker;
single-player victories against AI; distinct completed replays analyzed.

| Ordinary threshold | Points for this milestone |
| --- | --- |
| 1 | 10 |
| 3 | 20 |
| 10 | 30 |
| 25 | 40 |
| 50 | 60 |
| 100 | 80 |
| 250 | 120 |
| 500 | 160 |
| 1000 | 240 |

Each ordinary family offers 760 points. Best rated win streak uses:

| Streak threshold | Points for this milestone |
| --- | --- |
| 1 | 10 |
| 3 | 30 |
| 5 | 60 |
| 10 | 100 |

The nine families offer 6,280 points. `local-first-game` adds 10 points and has no
portrait reward. Points are summed from unique confirmed award IDs and the
code-owned catalog, never stored as a separately mutable balance. Point values
are stable rewards; changing them would change the projection for existing awards.

Legacy IDs stay attached to their original thresholds: `first-rated-win` (1),
`rated-wins-10`, `rated-wins-25`, `defender-hold` (1), `defender-wins-10`,
`brandubh-initiate`, `tablut-initiate`, `copenhagen-initiate` (each 1),
`rated-win-streak-5`, `rated-win-streak-10`, and `ai-first-victory` (1). Additional
IDs use `<family>-<threshold>`, e.g. `attacker-wins-100` and `analyzed-replays-3`.

Each family has one evolving card. Collected tier is the highest **confirmed**
tier, independent of current progress or unclaimed eligibility. Engraved, bronze,
silver, gold and legendary treatments use CSS and the existing Arena surfaces;
no new bitmap assets or animations are required. Every milestone and its reward
remain inspectable. An earlier missing milestone stays collectible even if a
higher milestone has already been confirmed.

## Award rules

Awards are derived from authoritative records only:

- rated wins, defender wins and ruleset milestones come from the **applied rating
  ledger** (`rating_events` joined to `rating_applications`), so a withdrawn result
  contributes nothing and a rebuilt or corrected pool is recomputed, never patched
- best rated win streaks are folded from the same ordered ledger, exactly like
  [personal progress](RATINGS.md#personal-rating-history-and-progress); a
  correction therefore changes the streak that a later read derives
- the local milestone counts completed local games in the account's own
  `category='local'` records
- vs-AI milestones count completed `human-ai` account games the account's own
  human seat won, once per game ID; replaying, undoing and redoing that game
  cannot create additional game identities
- replay milestones count durable `replay_analysis_completions` keys:
  `(account_id, category, game_id)`. An explicit analysis POST must successfully
  retain or consume a completed advisor report for an already completed game.
  Merely opening a replay, automatic playing-agent evaluations, partial searches,
  missing models and failed evaluations do not qualify. Background completion
  records the initiating account; subsequent explicit POSTs can consume an
  already retained successful report. The write rechecks the owned game or online
  participant and terminal status. Existing active-online analysis prohibitions
  remain in force. Browser-local analysis is not eligible

Unrated/casual games, unfinished games, AI-vs-AI self-play, another account's games
and client-supplied claims never contribute.

Confirmation is explicit, idempotent and permanent:

- the read never writes: `GET /api/personal/achievements` only projects the
  catalog, the account's already-confirmed awards and the progress it derives, so
  it is safe as a repeatable `GET`
- an explicit `POST /api/personal/achievements/confirm` evaluates the same records
  and confirms every milestone they already prove inside one `BEGIN IMMEDIATE`
  transaction, then returns the same projection; `PATCH /api/profile/portrait`
  materializes the same eligible awards inside its selection write. The award key is
  `(account_id, achievement_id)` with `INSERT OR IGNORE`, so a repeated
  confirmation, a duplicated result update, a replay or a pool rebuild can never
  duplicate a reward
- confirmed awards and granted portraits are append-only: no update or delete path
  exists, so a later void or correction never revokes an unlock
- a result withdrawn **before** the first confirmation never awards anything, so a
  leaked or voided result cannot hand out a reward
- `awarded_at` is the server time of first confirmation, not the source event time;
  the source evidence remains the ledger or the completed game record
- `progress` is the current derived count and may later fall below the threshold
  while the confirmed unlock stays (for example after a void)

## Migration and existing accounts

Schema 20 is additive and idempotent. It adds the replay-completion table with a
composite primary key (also the account-count index) and append-only protection.
Compatible restores retain these receipts alongside awards and portraits; a
schema-18 snapshot requires the existing explicit migration/restore workflow.
No old award or portrait row is rewritten and no points balance is migrated.
Historical position reports cannot prove who analyzed an online replay, or whether
a local game was already complete at analysis time, so there is no speculative
backfill. Explicitly analyzing a completed replay can reuse retained evidence.

Schema 17 remains the original foundation. `migrate_online` creates the catalog, award,
entitlement and selection tables and mirrors the code-owned catalogs on every
startup; schema 16 and the local 1v1 `category` column are untouched.

- the migration itself inserts **no** award, entitlement or selection, so upgrading
  an existing account grants nothing
- a changed name, unlock or retirement is applied to the stored catalog row on the
  next startup without renumbering any ID, and the published catalog version is
  bumped in code when the catalog changes
- an existing account's first confirmation confirms only milestones its
  authoritative records already prove (for example historical rated wins in the
  retained rating ledger or completed owned account games); nothing is invented
  from missing evidence, and no milestone is granted from a claim, a seed or an
  absent record
- the progression tables belong to the retained live online authority, so a
  compatible stopped-writer restore keeps current awards, entitlements and the
  selected portrait rather than rewinding them to the snapshot; a differing online
  schema still refuses automatic root replacement

## API contract

| Method/path | Permission | Behavior |
| --- | --- | --- |
| `GET /api/portraits/catalog` | `play` | `catalog_version`, `default_portrait_id`, `items[{id,name,unlock_achievement_id}]` |
| `GET /api/personal/achievements` | `play` | read-only own milestone `items`, grouped `families`, point `summary` and `as_of` |
| `POST /api/personal/achievements/confirm` | `play` | optional strict body `{"family_id":"rated-wins"}` confirms only that family; absent/empty body confirms all eligible awards inside one `BEGIN IMMEDIATE`; returns the read projection |
| `PATCH /api/profile/portrait` | `profile` | `{"portrait_id": "<catalog id>"}`; materializes eligible awards in the same write, then selects the earned portrait and returns the profile projection |

- all four routes require a current authenticated session and take the account from
  that session; none accepts an account parameter, so a client can never confirm or
  read another account's awards
- `GET /api/personal/achievements` is a pure projection: it opens a read transaction
  and never inserts, updates or deletes, so a top-level cross-site `GET` cannot
  confirm an award
- `POST /api/personal/achievements/confirm` is a non-SAFE mutation, so the security
  boundary requires the matching origin and `X-CSRF-Token`, bounds the request body
  and refuses it (`503`) during a deployment freeze before it reaches the service
- the selection mutation keeps the same origin, CSRF, body-size and deployment
  freeze checks, and strict request allowlists reject unknown fields (`422`)
- unknown, retired and URL-shaped IDs return `422`; a not-yet-earned ID returns
  `403`; a missing session returns `401`
- the profile projection publishes the effective `portrait_id` only — the stored
  selection while the catalog still offers it, otherwise the default. Detailed
  achievement progress stays private to `GET /api/personal/achievements`
- `POST /api/personal/achievements/confirm` is the dedicated confirmation action,
  and `PATCH /api/profile/portrait` also materializes eligible awards inside its
  selection write. Both are persistent-state mutations and both are listed as such
  in the [endpoint inventory](API_PERMISSIONS.json); the read is not

### Empty states

- a fresh account receives the complete achievement list with `progress: 0`,
  `unlocked: false`, `awarded_at: null`, `summary: {"unlocked": 0, "total": 77, "earned_points": 0, "total_points": 6290, "claimable_points": 0}` and
  no award, entitlement or selection row
- a fresh account receives the complete catalog and the default portrait as its
  effective `portrait_id`, because the default is always available
- an account with no rated games receives zero-valued rated and streak progress, not
  an omitted section

Milestone items additionally include `family_id`, one-based `tier` and `points`.
Each family includes `milestones`, `progress`, `collected_tier`,
`collected_threshold`, `achieved_threshold`, `collected_count`, `total_tiers`,
`next_milestone` (null when complete), `claimable_points` and `complete`. Summary
includes `earned_points`, `total_points` and `claimable_points`, in addition to
milestone counts. GET remains read-only; eligibility alone never earns points.
Unknown families and extra request fields return 422. Both all-family and scoped
claims use the same identity recheck, transaction and primary-key protection.

## Frontend integration

- `GET /api/portraits/catalog` supplies the gallery's stable IDs, names and the
  unlocking achievement ID; the client maps IDs to bundled artwork and never accepts
  a URL or path from the account
- `GET /api/personal/achievements` explains each lock and supplies entitlement: a
  portrait is selectable when it is the default or when its unlocking achievement
  reports `unlocked: true`
- the authenticated `/portraits` page renders locked, ready-to-claim, unlocked and
  selected states from the server projection; reading progress never confirms
- **Claim eligible achievements** posts `POST /api/personal/achievements/confirm`
  and renders the returned projection; the action is idempotent and coalesces while
  one request is in flight
- selecting an earned portrait posts `PATCH /api/profile/portrait`; the returned
  profile projection updates the client immediately, and the payload-free
  `profile-changed` event refreshes owner-scoped views. A confirmed selection
  survives a failed follow-up read, and a mutation error is cleared once a later
  authoritative refresh proves the server committed the operation
- the client never derives an unlock from client state or a local "best streak";
  the backend rechecks entitlement inside the selection transaction

Portrait Hall caps bitmap width at `320 / max(1, devicePixelRatio)` CSS pixels,
updates it on display-density/resize changes and uses `object-fit: contain` with
centered artwork. Frames can grow independently. This rule is scoped to the Hall;
profile, leaderboard, dashboard and player-card portraits keep their own sizing.

## Known limitations

- awards are confirmed by an explicit `POST /api/personal/achievements/confirm` (or
  when a portrait is first selected), not by a background job, so `awarded_at` is
  the confirmation time and the read never confirms anything
- unlock notifications are in-session only; there is no persisted notification
  history or audit trail beyond the confirmed award row
- only the aggregate best rated win streak has achievements; per-ruleset streaks stay
  a progress projection
- replay eligibility requires a completed server advisor report, rather than proof
  that every position in the replay was analyzed; no whole-replay batch analysis
  service currently exists
- `local-first-game` grants no portrait, and neither the catalog nor the profile
  reports an entitlement flag; the client joins the catalog with the achievement list
- leaderboard and online lobby participant projections publish the effective
  `portrait_id` only; detailed achievement progress stays private

See [tiered achievement validation and screenshots](validation/tiered-achievements.md)
for current implementation evidence; original Portrait Hall evidence stays historical.
