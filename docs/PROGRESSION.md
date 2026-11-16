# Earnable portraits and achievement progress

[Documentation index](README.md) · [Rated games](RATINGS.md) · [Profile](PROFILE.md) ·
[Accounts and API security](SECURITY.md)

Achievement catalog **3**, portrait catalog **2**, schema **21** automatically
award milestones from authoritative events. There are **77 milestones worth
6,290 points**. No client supplies progress, points or reward amounts. Earned
achievements and portrait entitlements are permanent, including after corrections.

## API contract

- `GET /api/portraits/catalog`: authenticated code-owned catalog, ordered by difficulty.
- `GET /api/personal/achievements`: read-only own-account progress, permanent awards,
  families, score and `unlocked_portrait_ids` (including grandfathered entitlements).
- `PATCH /api/profile/portrait`: explicit selection of an already unlocked portrait,
  protected by the existing session, origin, CSRF, deployment freeze and body checks.

There is no achievement confirmation endpoint or collection action. Selecting a
portrait never awards achievements; unlocking never changes the selected avatar.
Unknown/retired IDs return 422, locked portraits 403, missing sessions 401.

## Portrait Hall and player artwork

The Hall presents the introduction, ordered portrait gallery, then Achievements.
The achievement score, percentage and milestone count sit below that heading.
Family cards show current recorded progress, highest currently achieved threshold,
highest permanently awarded tier, next target and incremental reward. The next
target is the first threshold above current progress that has not already been
awarded. A correction can reduce current progress without reducing an earned tier.
A completed family has no further target or progress bar.

Engraved, bronze, silver, gold and legendary treatments use shared Arena theme
tokens and stable family runes. Server responses determine all awards. English and
German labels, localized numbers, accessible progress labels, keyboard selection,
account-switch guards and stale-response handling cover the four themes.

Game completion, successful analysis completion and window focus trigger coalesced
account-scoped refreshes, without additional polling. A session baseline prevents
historical notification storms; newly observed awards show points and newly
available portraits. Notifications are not a durable notification history.

## Portrait catalog

The backend sorts the default first, victory portraits by threshold then stable
ID, followed by rated streak portraits by streak length. Ordering is independent
of display language.

| Portrait | Requirement | Milestone ID |
| --- | --- | --- |
| Shieldbearer | Default | none |
| Fjord Scout | 10 rated Copenhagen wins | `copenhagen-wins-10` |
| Forge Keeper | 10 human-versus-AI wins | `ai-wins-10` |
| Iron Helm | 10 rated defender wins | `defender-wins-10` |
| Shieldmaiden | 10 rated online wins | `rated-wins-10` |
| Skald | 10 rated Tablut wins | `tablut-wins-10` |
| Valkyrie | 10 rated attacker wins | `attacker-wins-10` |
| Wanderer | 10 rated Brandubh wins | `brandubh-wins-10` |
| Raven Seer | 25 rated online wins | `rated-wins-25` |
| Einherjar | 50 rated online wins | `rated-wins-50` |
| Berserker | Best rated win streak of 5 | `rated-win-streak-5` |
| One-Eyed Jarl | Best rated win streak of 10 | `rated-win-streak-10` |

Existing portrait entitlements and selections survive increased requirements.
New grants use the new requirements. The client uses durable portrait entitlements,
not a join against today's unlock association, so grandfathered rewards remain usable.

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
(Fireside Game) adds one milestone worth 10 points, for **77 / 6,290** overall.
The schedule and legacy achievement IDs are unchanged. Scores sum unique permanent
award IDs against the code-owned point catalog; no independently mutable balance
exists. Changing point values would change historical score projections.

The response includes milestone `family_id`, `tier`, `points`, `progress`,
`unlocked` and `awarded_at`. Families include `milestones`, `progress`,
`awarded_tier`, `awarded_threshold`, `achieved_threshold`, `awarded_count`,
`total_tiers`, `next_milestone` and `complete`. Summary includes `unlocked`,
`total`, `earned_points`, `total_points`. New accounts receive zero score and
progress, all definitions and the always-available default; GET creates no rows.

## Award rules

One centralized `award_achievements` function runs inside the authoritative
caller's existing transaction. Unique keys and `INSERT OR IGNORE` prevent duplicate
awards/entitlements under retries, concurrent completions and ledger rebuilds.
All newly reached thresholds are inserted together. It skips existing awards and
shares counters across criteria/scopes rather than querying every milestone.

- Rated completion applies the rating ledger first, then awards affected accounts
  in that same transaction only when the ledger actually changed. Wins group by
  ruleset and actual attacker/defender seat. Best streak folds the established
  ordered applied ledger. Casual, unfinished and withdrawn results do not count.
  Two-leg matches use the existing rating policy. Later corrections retain past
  awards and evaluate future milestones against currently valid results.
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
portrait entitlements in batches. No per-card requests or frontend reward logic exist.

`awarded_at` is the time of first durable award, not the original game timestamp.
Result correction and void handling never delete earned rewards. Replay identity
includes category, preventing local/online game-ID collisions.

## Migration and existing accounts

Schema 20 introduced durable replay-analysis receipts. Schema 21 seeds the updated
catalogs, then performs a controlled, idempotent reconciliation of historical
account records inside the migration transaction before advancing `user_version`.
It grants all currently proven milestones and portrait entitlements without visits
to the Hall. Already earned awards/portraits remain untouched. Reopening an upgraded
installation does not repeat the reconciliation. Back up before upgrading; a large
history may increase the one-time upgrade duration.

The existing administrator rating-ledger rebuild performs reconciliation at its
transaction boundary. Ordinary GET endpoints and portrait selection never reconcile.
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
See [artwork provenance](PORTRAIT_ARTWORK.md) and
[automatic progression validation](validation/automatic-achievements.md).

Only aggregate rated streaks earn achievements; per-ruleset streaks remain progress
projections. Progress reflects authoritative server records, not arbitrary client
reports. Notifications require an active session or a subsequent focus refresh.
