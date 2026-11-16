# Tiered achievements and sharp Portrait Hall artwork

[Progression contract and point schedule](../PROGRESSION.md) ·
[Original Portrait Hall evidence](portrait-hall.md)

Nine achievement families contain 76 independently confirmed milestone awards;
Fireside Game remains a separate achievement. Eight ordinary families offer 760
points each, the best rated streak offers 200, and Fireside Game adds 10: **6,290
available points**. One response supplies milestones, family projections and the
score. Confirming one family never confirms unrelated eligible milestones.

The existing award ledger supplies points directly. Claims and portrait grants
commit together under the existing write lock and primary keys. Existing portrait
unlock IDs, corrected-result permanence, account scope, CSRF and read-only GETs
remain intact. Schema 20 adds indexed, append-only account/category/replay receipts
for completed server advisor analyses. Partial display estimates are explicitly
excluded by checking raw retained reports. No historical analysis credit is guessed.

## Visual evidence

These are controlled server-shaped fixtures using the real catalog and bundled
portrait assets. Backend tests separately verify authoritative eligibility. Tier
captures include engraved, bronze, silver, gold, legendary, claimable and completed
cards; legendary appearance follows the highest collected tier. There are no new
bitmap assets in the application and no added animation.

| Theme or viewport | Achievement capture |
| --- | --- |
| Classic Table, desktop | [1920px](../screenshots/tiered-achievements/classic-table-1920.png) |
| Iron and Ash, desktop | [1920px](../screenshots/tiered-achievements/iron-ash-1920.png) |
| King's Table, desktop | [1920px](../screenshots/tiered-achievements/kings-table-1920.png) |
| Saga Hall, desktop | [1920px](../screenshots/tiered-achievements/saga-hall-1920.png) |
| Iron and Ash, mobile | [390px, Chromium phone emulation](../screenshots/tiered-achievements/iron-ash-390.png) |
| Classic Table, 4K | [3840px](../screenshots/tiered-achievements/classic-table-3840.png) |

The tests additionally exercise all four themes at 320/390/1920/3840 CSS pixels,
English and German, keyboard collection/selection, portrait fallback on direct
routes, contrast and horizontal overflow. Image checks compare rendered width
multiplied by device pixel ratio against the native 320px bitmap, and require
`object-fit: contain`. Hall-only sizing leaves profile, dashboard, leaderboard
and player contexts intact. Screenshots are browser emulation, not physical-device
or Safari evidence. CI retains the gallery and tier captures in the Arena visual
acceptance artifact.

## Automated validation

- Frontend: **1,341 tests passed** (`npm --prefix frontend test -- --maxWorkers=2`).
  Two workers avoid local resource contention without changing test time limits.
- Final progression suite: **30 passed**, covering the exact 76-tier schedule,
  every boundary, skipped tiers, concurrent/repeated claims, permanent points,
  legacy portraits, actual rulesets/roles, simultaneous/sequential return legs,
  replay identity/authorization and bounded query counts.
- Migration/progression and new replay worker/partial-report checks: **40 passed**.
- Final tier browser matrix: **8 passed**, all four themes on desktop and phone;
  desktop cases include 1920px and 3840px captures.
- The initial full `make check` backend run reported **3,392 passed, 48 skipped**
  and one failing future-schema fixture, which still used schema 19 as a future
  version. The fixture now uses 21 and passes in the focused migration rerun.
  Hardware/optional-runtime skips remain limitations; this is not a claim that
  that initial `make check` invocation passed.

The final PR checks provide validation of the committed source. The gallery
matrix, final analysis regressions and production build are recorded on the PR
when their runs finish.

## Remaining scope

Replay credit means a completed server advisor analysis of a position in a
completed replay, not a whole-replay batch scan. Cached successful reports may be
reused by explicit analysis POSTs. Views, active games, failed/cancelled/partial
analyses and browser-local analysis are ineligible. Existing reports are not
backfilled because they cannot prove account attribution and completion timing.
No stronger-model or production deployment claim is made.
