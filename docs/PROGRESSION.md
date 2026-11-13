# Earnable portraits and achievement progress

[Documentation index](README.md) · [Rated games](RATINGS.md) · [Profile](PROFILE.md) ·
[Online core](ONLINE.md) · [Accounts and API security](SECURITY.md)

Schema **17** adds the Arena v2 progression foundations: a server-owned portrait
catalog, per-account portrait entitlements and a permanent achievement ledger.
Awards are derived only from authoritative server records, and a client can never
grant itself a reward, supply a portrait image or read another account's progress.

## Scope and non-goals

Delivered:

- `GET /api/portraits/catalog` — stable catalog IDs and metadata
- `GET /api/personal/achievements` — read-only own progress and confirmed awards
- `POST /api/personal/achievements/confirm` — CSRF-protected confirmation of the
  awards the authoritative records already prove
- `PATCH /api/profile/portrait` — CSRF-protected selection of an earned portrait

Deliberately out of scope: paid currency, any XP or soft-currency economy, client
awarded unlocks, login/activity streaks, leagues and player tournaments. Those
reserved systems keep their own future schema numbers (18/19) and namespaces.

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

| ID | Category | Requirement | Threshold | Portrait |
| --- | --- | --- | --- | --- |
| `first-rated-win` | rated | rated online wins | 1 | `shieldmaiden` |
| `rated-wins-10` | rated | rated online wins | 10 | `raven-seer` |
| `rated-wins-25` | rated | rated online wins | 25 | `einherjar` |
| `defender-hold` | rated | rated online wins as defender | 1 | `iron-helm` |
| `defender-wins-10` | rated | rated online wins as defender | 10 | `valkyrie` |
| `rated-win-streak-5` | streak | best rated win streak | 5 | `berserker` |
| `rated-win-streak-10` | streak | best rated win streak | 10 | `one-eyed-jarl` |
| `brandubh-initiate` | ruleset | rated Brandubh wins | 1 | `wanderer` |
| `tablut-initiate` | ruleset | rated Tablut wins | 1 | `skald` |
| `copenhagen-initiate` | ruleset | rated Copenhagen wins | 1 | `fjord-scout` |
| `local-first-game` | local | finished same-device local games | 1 | none |
| `ai-first-victory` | ai | finished single-player games won against a computer | 1 | `forge-keeper` |

An achievement does not have to unlock a portrait: `local-first-game` is a
milestone without one.

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
- the vs-AI milestone counts completed `human-ai` account games the account's own
  human seat won

Unrated/casual games, unfinished games, AI-vs-AI self-play, another account's games
and client-supplied claims never contribute.

Confirmation is explicit, idempotent and permanent:

- the read never writes: `GET /api/personal/achievements` only projects the
  catalog, the account's already-confirmed awards and the progress it derives, so
  it is safe as a repeatable `GET`
- an explicit `POST /api/personal/achievements/confirm` evaluates the same records
  and confirms every milestone they already prove inside one `BEGIN IMMEDIATE`
  transaction, then returns the same projection; the award key is
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

Schema 17 is additive and idempotent. `migrate_online` creates the catalog, award,
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
| `GET /api/personal/achievements` | `play` | read-only own `items` with `progress`, `unlocked`, `awarded_at`, `portrait_id`, plus `summary` and `as_of` |
| `POST /api/personal/achievements/confirm` | `play` | confirms the awards the records already prove; returns the same projection as the read |
| `PATCH /api/profile/portrait` | `profile` | `{"portrait_id": "<catalog id>"}`; returns the profile projection |

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
- `POST /api/personal/achievements/confirm` is the only route that confirms awards,
  so it is the one listed as persistent state in the
  [endpoint inventory](API_PERMISSIONS.json); the read is not

### Empty states

- a fresh account receives the complete achievement list with `progress: 0`,
  `unlocked: false`, `awarded_at: null`, `summary: {"unlocked": 0, "total": 12}` and
  no award, entitlement or selection row
- a fresh account receives the complete catalog and the default portrait as its
  effective `portrait_id`, because the default is always available
- an account with no rated games receives zero-valued rated and streak progress, not
  an omitted section

## Frontend integration

- `GET /api/portraits/catalog` is the gallery: stable IDs, names and the unlocking
  achievement ID
- `GET /api/personal/achievements` is the lock explanation and the entitlement
  source: a portrait is selectable when it is the default or when its unlocking
  achievement reports `unlocked: true`
- F4 must persist a new unlock with `POST /api/personal/achievements/confirm`; the
  read alone never confirms anything, so the UI confirms first (for example when the
  achievements view opens) and renders the projection that comes back
- `PATCH /api/profile/portrait` returns the same projection as `GET /api/profile`, so
  the client can update its profile state from one response
- never derive an unlock from client state or a local "best streak"; the backend
  rechecks entitlement inside the selection transaction

## Known limitations

- awards are confirmed by an explicit `POST /api/personal/achievements/confirm` (or
  when a portrait is first selected), not by a background job, so `awarded_at` is
  the confirmation time and the read never confirms anything
- no unlock notification, history entry or audit trail beyond the confirmed award row
- only the aggregate best rated win streak has achievements; per-ruleset streaks stay
  a progress projection
- `local-first-game` grants no portrait, and neither the catalog nor the profile
  reports an entitlement flag; the client joins the catalog with the achievement list
- the leaderboard and online lobby participant projections are unchanged and do not
  expose `portrait_id` in this change
