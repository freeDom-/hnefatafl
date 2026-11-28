# Earnable portraits and achievement progress

[Documentation index](../../README.md) · [Rated games](./ratings.md) · [Profile](./profile.md) ·
[Accounts and API security](../administration/security.md)

Achievement catalog **4** and portrait catalog **2** define **92 milestones worth
6,770 points**. The server awards milestones automatically from authoritative
events. Players unlock their eligible portraits in the Hall of Renown. Earned
achievements and portrait entitlements are permanent, including after corrections.

## API contract

- `GET /api/portraits/catalog` returns the authenticated catalog in gallery order.
- `GET /api/personal/achievements` returns read-only own-account progress, permanent
  awards, families, score and `unlocked_portrait_ids`, including grandfathered
  entitlements. A portrait is eligible when its `unlock_achievement_id` points to
  an awarded milestone. The catalog identifies the always-available starter.
- `POST /api/portraits/unlock` accepts `{"portrait_id":"shieldmaiden"}` and returns
  `portrait_id` and `newly_unlocked`. The first successful claim returns `true`
  after commit. Repeat claims and the starter portrait return `false`.
- `PATCH /api/profile/portrait` equips an already unlocked portrait by its stable ID.

Both writes use the authenticated session and existing origin, CSRF, deployment
freeze and body checks. Unknown or retired IDs return 422, missing eligibility or
entitlement returns 403, and missing sessions return 401. Claiming a portrait leaves
the equipped avatar and achievement score as they were.

## Hall of Renown and player artwork

The Hall presents the introduction, ordered portrait gallery, then Deeds of Valor.
Locked portraits show grayscale art and their milestone progress. Once the milestone
is awarded, **Unlock portrait** takes the place of the progress bar. The artwork
stays grayscale until the player claims it. The Hall enables **Use portrait** as
soon as the server confirms the claim. A previous claim or a reload opens the
standard colored portrait directly.

A new claim reveals the original artwork through seeded, staggered Canvas 2D
masks over the grayscale image. The first area starts at 120 ms. The areas merge
and fill the frame over 2.4 seconds. A 1.05-second CSS glow and floating runes
follow. The renderer feathers the same mask with a Canvas shadow where Canvas
filters are unavailable. Reduced motion, backgrounding and renderer initialization
failures resolve to the standard portrait. The effect reuses the loaded gallery
image and allocates buffers and animation frames for active reveals. See the
[Shieldmaiden capture sequence](./progression.md).

Deeds of Valor shows the achievement score, percentage and milestone count.
Family cards show current recorded progress, the highest permanently awarded tier,
next target and incremental reward. The API retains threshold and highest-achieved
values for eligibility, awards, scores and progress. The next target is the first
threshold above current progress that has not already been awarded. A correction
can reduce current progress without reducing an earned tier. A completed family has
no further target or progress bar.

The three league families render the authoritative tier names instead of numeric
levels. Each card shows the permanent league tier separately from the current
season's provisional points, qualification and next-tier progress. Unlocked, current
and upcoming tiers each have a distinct state, and the card reuses the existing
league tier artwork. The card never introduces a second seasonal record or a
separate achievement reward.

Engraved, bronze, silver, gold and legendary treatments use shared Arena theme
tokens and stable family runes. Server responses determine all awards. English and
German labels, localized numbers, accessible progress labels, keyboard selection,
account-switch guards and stale-response handling cover the four themes.

Game completion, successful analysis completion, window focus and reconnect trigger
coalesced account-scoped refreshes. One shared `ProgressionProvider` owns the single
`GET /personal/achievements` read for the authenticated shell, so The Great Hall,
the Hall of Renown and the notification baseline never fetch it independently.
The Great Hall already owns the account-wide result-totals poll, so it invalidates
the shared read from that poll without a second timer. Every other route,
including an open local, single-player or online game board, keeps a 15-second
visible-tab backstop: a game route only observes its own game and cannot see a
separate rated completion. The recurring read is suspended while the page is
hidden and resumes on visibility, so a remote completion eventually appears
without navigation or refocus. The first response for each account establishes
the notification baseline. Newly awarded milestones show their points. A new
award with an unclaimed `portrait_id` also contributes to **New portraits ready
to unlock**. Newly observed claims show **New portraits unlocked**, including a
claim from another device or one recovered by refreshing after a lost response.
Both portrait notices count unique IDs. Confirmed local claims update the shared
projection immediately, and older reads cannot repeat an announcement.

## Portrait catalog

The backend exposes the default portrait first, followed by the approved gallery
display order declared once in the code catalog. A portrait without an explicit
position sorts after every listed portrait by stable ID, so introducing one is
deterministic without renumbering existing entries. Ordering is independent of
display language.

| Portrait | Requirement | Milestone ID |
| --- | --- | --- |
| Shieldbearer | Default | none |
| Forge Keeper | 10 human-versus-AI wins | `ai-wins-10` |
| Shieldmaiden | 10 rated online wins | `rated-wins-10` |
| Raven Seer | 25 rated online wins | `rated-wins-25` |
| Einherjar | 50 rated online wins | `rated-wins-50` |
| Iron Helm | 10 rated defender wins | `defender-wins-10` |
| Valkyrie | 10 rated attacker wins | `attacker-wins-10` |
| Wanderer | 10 rated Brandubh wins | `brandubh-wins-10` |
| Skald | 10 rated Tablut wins | `tablut-wins-10` |
| Fjord Scout | 10 rated Copenhagen wins | `copenhagen-wins-10` |
| Berserker | Best rated win streak of 5 | `rated-win-streak-5` |
| One-Eyed Jarl | Best rated win streak of 10 | `rated-win-streak-10` |

Existing portrait entitlements and selections survive increased requirements.
New claims use the current requirements. The client uses durable portrait entitlements
to show which grandfathered rewards remain usable.

## Catalog integrity guarantees

IDs are stable and never reused. Catalog rows are seeded by upsert and never deleted.
Code validates unique IDs, known criteria/scopes, positive thresholds and exactly
one non-retired default. Database keys enforce unique account/achievement awards
and account/portrait entitlements; a partial unique index permits at most one
active default. Append-only triggers prevent reward updates/deletions.

Retired portrait rows and entitlements remain stored, but selection rejects them
and published identities fall back to the default. Replacing a default must retire
the old default first. Published leaderboard and online projections batch effective
portrait IDs without exposing private achievement progress.

## Achievement catalog

The declarative backend catalog generates nine-tier schedules for eight families:
rated online wins; rated Brandubh, Tablut and Copenhagen wins; rated defender and
attacker wins; human-versus-AI wins; distinct completed replays successfully analyzed.

| Threshold | Incremental points |
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

Each ordinary family offers 760 points. Best rated win streak has four milestones:

| Streak | Incremental points |
| --- | --- |
| 1 | 10 |
| 3 | 30 |
| 5 | 60 |
| 10 | 100 |

These nine families contain 76 milestones worth 6,280 points. `local-first-game`
(Fireside Game) adds one milestone worth 10 points. Three league families are
generated from the supported league rulesets and the authoritative league tier
policy: Brandubh, Tablut and Copenhagen. Each has the five league tiers Midgard,
Nidavellir, Alfheim, Asgard and Valhalla, using the first five incremental point
values (10, 20, 30, 40 and 60). They add 15 milestones worth 480 points, for
**92 / 6,770** overall. The schedule and legacy achievement IDs are unchanged.
Scores sum unique permanent award IDs against the code-owned point catalog; no
independently mutable balance exists. Changing point values would change historical
score projections.

The response includes milestone `family_id`, `tier`, `points`, `progress`,
`unlocked` and `awarded_at`. Families include `milestones`, `progress`,
`awarded_tier`, `awarded_threshold`, `achieved_threshold`, `awarded_count`,
`total_tiers`, `next_milestone` and `complete`. Summary includes `unlocked`,
`total`, `earned_points`, `total_points`. New accounts receive zero score and
progress, all definitions and the always-available default; GET creates no rows.

## Award rules

One centralized `award_achievements` function runs inside the authoritative
caller's existing transaction. Unique keys and `INSERT OR IGNORE` prevent duplicate
milestone awards under retries, concurrent completions and ledger rebuilds.
All newly reached thresholds are inserted together. It skips existing awards and
shares counters across criteria/scopes rather than querying every milestone.

The unlock endpoint revalidates the session and checks its awarded milestone inside
an SQLite write transaction. The existing account/portrait primary key and write lock
ensure that concurrent requests yield one new claim. Eligibility follows the permanent
milestone award even when a later result correction lowers the recorded progress.

- Rated completion applies the rating ledger first, then awards affected accounts
  in that same transaction only when the ledger actually changed. Wins group by
  ruleset and actual attacker/defender seat. Best streak folds the established
  ordered applied ledger. A freshly appended result takes a bounded fast path: a
  loss or draw skips the streak criterion, and the winner reads only the trailing
  wins up to its next unawarded milestone, falling back to the exact fold when a
  milestone is reached. Corrections, withdrawals, pool rebuilds and migration
  always fold exactly, because removing an earlier loss can create a longer
  historical streak. Casual, unfinished and withdrawn results do not count.
  Two-leg matches use the existing rating policy. Later corrections retain past
  awards and evaluate future milestones against currently valid results. See the
  [measured fast path](../evaluation/benchmarks/training/progression-streaks.md).
- Official league settlement records the qualified peak in
  `league_achievement_progress` and awards every league tier up to that permanent
  peak in the same transaction. Midgard requires three eligible results, and a
  peak only rises. Season rollover, ordinary demotion and later corrections never
  revoke an award. History-only results, casual games, non-qualifying games and
  client values contribute nothing. If a previously settled result in the same
  account/season/ruleset awaits an official correction, or a season rebuild is
  incomplete, the candidate peak is durably queued instead of awarded. A bounded
  worker validates it from effective official results after the scope is
  consistent. Normal appends therefore remain an indexed tail update, not a
  full-ledger scan.
- Local/vs-AI completion writes the persisted `recorded_result` and awards the
  owner in the same transaction. Only completed local games or human-versus-AI
  human victories qualify. Replaying, reload, undo/redo and duplicate updates
  cannot create a new game identity or duplicate an award.
- Successful complete server advisor reports and unique account/category/game
  receipts are saved with awards in the same report transaction. Authorized
  concurrent requesters share computation and each receives credit. Cached
  successful analysis may be consumed by an explicit authorized analysis request.
  Passive viewing, spectators, active games, partial/failed/canceled searches and
  AI self-play do not count. Browser-local analysis is not eligible. This records
  successful position analysis within a completed replay, not a whole-replay batch.

Replay receipts have an account-leading composite key. Repeated successful
requests validate scope/completion and use a read-only existing-receipt fast path;
first insertions revalidate inside the writer transaction. Reads fetch awards and
portrait entitlements in batches. The Hall sends a write only when the player
acts on an unlock or selection button.

`awarded_at` is the time of first durable award, not the original game timestamp.
Result correction and void handling never delete earned rewards. Replay identity
includes category, preventing local/online game-ID collisions.

## Migration and existing accounts

Schema 20 introduced durable replay-analysis receipts. Schema 21 seeds the updated
catalogs, then performs a controlled, idempotent reconciliation of historical
account records inside the migration transaction before advancing `user_version`.
Schema 24 adds `league_achievement_progress` and
`league_achievement_reconciliation`. The migration queues one bounded verification
row per account, season and ruleset instead of trusting the contribution table or
final standings. Schema 25 adds a stable rating-sequence cursor and accumulated
standing; schema 26 adds account/season/ruleset evidence generations. The League
worker resolves fixed-size pages through indexed `league_games` provenance,
authoritative applied rating events and effective official revisions, folds stable
result order with the zero floor, requires qualification and grants all preceding
tiers. Pending corrections and administrative rebuilds defer the row until
catch-up completes. A relevant settlement or rebuild increments only the affected
scope's generation, while unrelated accounts, rulesets and games in the same
season leave the cursor intact. Any scope-generation change restarts the cursor
before an award can commit. Administrative recovery derives the authoritative
sequence independently of the cached projection, so it can repair a corrupt
cached sequence before reconciliation ordering is evaluated again. Rebuild
finalization invalidates every queued scope of its season in one set-based
statement. The process is idempotent, preserves unrelated awards
and portraits, participates in retained-authority restore, and never fabricates a
peak when authoritative evidence is absent. A historical peak superseded before
it was recorded cannot be recovered from later effective revisions.
That historical step still grants the milestones and portrait entitlements an older
installation would receive on upgrade. Manual portrait unlocking reuses the existing
`achievement_awards`, `account_portraits` and `account_portrait_selection` tables.
Schema 21 and later keep their existing rows and normal upgrade paths. The existing
SQLite backup and same-schema restore carry both claimed portraits and selections.
Reopening an installation preserves unclaimed eligibility. A large pre-schema-21
history may increase the one-time upgrade duration.

Administrator rating-ledger rebuilds reconcile only actors present in the affected
pool (including withdrawn events), or all ledger actors for a full rebuild, and
only rated achievement criteria, inside the existing transaction. Migration
reconciliation remains all-account and all-criteria. The Great Hall's account-result
polling signals the existing notification hook when authoritative totals change;
refreshes defer while hidden and resume on visibility. Ordinary GET endpoints and portrait selection never reconcile.
Old analysis without a durable successful receipt is not retroactively inferred
from viewing history; a qualifying explicit analysis request can establish it.

## Artwork and known limitations

The Hall uses optimized **1024 × 1024** gallery redraws guided by the original
320px portraits. Original high-resolution sources were unavailable. The original
320px assets remain in profile, leaderboard, dashboard and player-card contexts.
Gallery images preserve composition, use `object-fit: contain`, and cap rendered
size against each asset's native pixels divided by current device pixel ratio.
Cards/frames can grow independently; unknown IDs and image failures retain the rune
fallback. Assets are lazy loaded, roughly 2.7 MiB combined; no sharpening is applied.
See [artwork provenance](./progression.md) and
[automatic progression validation](./progression.md).

Only aggregate rated streaks earn achievements; per-ruleset streaks remain progress
projections. Progress reflects authoritative server records, not arbitrary client
reports. Notifications require an active session or a subsequent focus refresh.

## Gallery artwork provenance

The repository contained only 320 × 320 derivatives; no higher-resolution original
sources were available. The twelve `*-large.webp` files in
`frontend/public/assets/arena-v2/assets/portraits/` are new identity-guided redraws,
produced with the built-in image generation tool using each existing portrait as
its reference. They preserve the established Viking character, pose, proportions,
wardrobe and dark painterly setting. They are replacements for gallery display,
not recovered originals or interpolated enlargements.

The prompt set used this instruction for each allowlisted character:

> Redraw this Viking portrait at high native resolution. Preserve the character's
> identity, face, pose, composition, costume, palette and established realistic
> painterly Viking style. Render crisp, natural facial, hair, fur and metal detail.
> Square portrait, no text, no border, no extra characters or objects.

Shieldmaiden and Skald received a final reference-guided correction removing
unrequested birds from their hair while preserving the person, hairstyle, wreath,
lighting and composition. All outputs were inspected together before encoding.

[The source manifest](../portrait-gallery-artwork.json) records each generated source
filename, native dimensions, final dimensions, byte size and WebP quality. Native
outputs were at least 1024px square and downsampled to 1024 × 1024, encoded WebP at
quality 82, method 6. The twelve assets total 2,857,036 bytes. No sharpening or
upscaling is applied. The original 320px assets remain byte-for-byte unchanged.

`Portrait.tsx` derives the gallery mapping from the existing local ID allowlist.
Only the Hall requests the lazy-loaded large assets; other avatar contexts retain
the originals. Gallery sizing uses actual asset width divided by device pixel
ratio, capped by card space (up to 440 CSS pixels). Portraits remain centered and
proportional inside independently sized frames. Unknown IDs fall back to the
default; a failed image load displays the existing rune fallback.

See [visual validation](./progression.md) for rendered evidence.
