# Automatic achievement and portrait review

[Progression](../PROGRESSION.md) · [Artwork provenance](../PORTRAIT_ARTWORK.md)

This document records PR #94's automatic-award revision. Earlier
[tiered achievement evidence](tiered-achievements.md) describes the prior manual
collection implementation and remains historical.

Representative rendered screenshots and exact validation outcomes follow.

## Delivered behavior

Milestones and portrait entitlements are automatically awarded inside authoritative
rating, game-completion and successful-analysis transactions. Schema 21 performs
one historical reconciliation; subsequent reads and portrait selection never award.
Unique append-only rewards preserve points and portraits after result corrections.
Catalogs contain 77 milestones worth 6,290 points. Portrait catalog 2 raises victory
requirements to 10/25/50, retains streak requirements, and orders the gallery centrally.

Shared analysis computation credits both authorized participants, including a late
requester racing with completion. Requester sets are bounded and cleared after
completion, failure, cancellation or shutdown. Existing replay receipts take a
read-only fast path instead of acquiring a SQLite writer lock.

## Visual evidence

The gallery matrix passed in Classic Table, Iron & Ash, King's Table and Saga Hall,
in English and German at 390, 768, 1920 and 3840 CSS pixels. Gallery captures use
DPR 2; achievement matrix tests also exercise the mobile project's DPR 3.
Images decode at 1024 × 1024, preserve aspect ratio and fit within native device
pixels. Checks cover keyboard selection, visible focus, readable contrast, stable
ordering, profile/dashboard avatar reuse, score placement and horizontal overflow.
The gallery uses larger artwork while small avatar contexts retain 320px images.

Representative viewport captures below are reduced from DPR 2 to CSS-pixel dimensions
for repository size. The separate native-pixel crop preserves device pixels for
sharpness inspection. Artwork is a reference-guided redraw, documented in the
[provenance record](../PORTRAIT_ARTWORK.md).

- [Classic Table, English, desktop 1920](../screenshots/automatic-achievements/classic-table-en-1920.webp)
- [Iron & Ash, German, mobile 390](../screenshots/automatic-achievements/iron-ash-de-390.webp)
- [King's Table, English, tablet 768](../screenshots/automatic-achievements/kings-table-en-768.webp)
- [Saga Hall, German, 4K 3840](../screenshots/automatic-achievements/saga-hall-de-3840.webp)
- [Native device-pixel artwork crop](../screenshots/automatic-achievements/native-pixel-artwork.png)

## Validation

- Frontend: 95 files / 1,333 unit tests passed; final affected views/localization: 41 passed.
- Final progression: 27 passed; replay concurrency: 8 passed; security: 22 passed.
- Combined progression/analysis/migration/security/deployment run: 162 passed.
- Full Python run: 3,401 passed, 48 skipped, one stale permission-inventory failure;
  the final security rerun passes after removing the obsolete confirmation grant.
- Ruff, mypy (69 modules), TypeScript, documentation links, benchmark unit check and
  production build passed. Build retains the existing large-chunk advisory.

The skipped Python cases require optional OpenVINO/CUDA support or unavailable
combined optimization implementations. This run does not establish GPU parity,
competitive model strength or cross-browser rendering beyond Chromium.

Achievement score and evolving family treatments:
[Classic Table](../screenshots/automatic-achievements/classic-table-achievements.webp),
[Iron & Ash](../screenshots/automatic-achievements/iron-ash-achievements.webp),
[King's Table](../screenshots/automatic-achievements/kings-table-achievements.webp),
[Saga Hall](../screenshots/automatic-achievements/saga-hall-achievements.webp).

The full main Playwright suite completed with 374 passed, 19 skipped and five
failures. All five pass on targeted rerun (15 passed, one project skip) after
correcting temporary port mapping, cleaning the new game fixture and avoiding a
hover retry through the oversized tooltip that had already opened. The original
visibility, persistence and close-on-leave assertions remain intact.

## Final review corrections

Achievement progress is now shared, not per-component. One `ProgressionProvider`
owns the single account-scoped `GET /personal/achievements` read for the
authenticated shell; the Dashboard and the Portrait Hall consume it instead of
fetching their own copy. The Dashboard's account-wide result-totals poll
invalidates the shared read, so the Dashboard adds no recurring achievement
traffic. Every other route, including an open local, single-player or online game
board, keeps a 15-second visible-tab backstop because a game route only observes
its own game. Changing the route cadence retunes the existing poll instead of
restarting it, so a current snapshot survives Dashboard/Portrait Hall transitions
without another read. The recurring read suspends while the page is hidden, refreshes
coalesce, a new account mounts a fresh session that aborts the previous read, and
repeated unchanged results do not notify again. The Portrait Hall regression test
renders the `/portraits` route component, simulates a remote completion with no
focus or navigation, and asserts the score, portrait state, one localized milestone
notice and one portrait notice all update. A recovered lost-response portrait selection now also
dispatches the normal `profile-changed` event exactly once after authoritative
confirmation, which a dedicated test verifies through a profile-dependent listener.

Ordinary rated completion now uses a bounded win-streak tail probe instead of
folding the whole applied ledger for both players. A fresh loss or draw skips the
streak criterion, and the winner reads only the trailing wins up to its next
unawarded milestone, falling back to the exact fold when a milestone is reached.
Corrections, withdrawals, pool rebuilds and migration still measure exactly. The
[measured fast path](../benchmarks/progression-streaks.md) reports 3.90 ms for a
full 2,000-game fold versus 0.045 ms for the 3-row tail probe, with deterministic
regressions for the skipped loser, the below-threshold tail, the cross-threshold
award and the corrected historical streak.

Administrative rating rebuilds reconcile only ledger actors in the affected pool,
or all ledger actors for full rebuilds, and only rated criteria. Withdrawn ledger
entries remain in actor selection; current eligibility uses the applied ledger.
Migration reconciliation still covers every account and criterion.

A deterministic query trace with three accounts and ten rated games across all
three pools compares historical reconciliation with a pool rebuild: local/AI
history count queries fall from **6 to 0**. This measures query elimination, not a
wall-clock speedup. Tests cover other-pool-only actors, full rebuilds, corrections,
permanent rewards and repeated reconciliation. No mutable counters or schema
changes are introduced. The earlier manual-claim validation page is explicitly
marked historical and points here.

## Final review correction validation

The correction branch merged the current `main` so the polling and lazy-loading
work (PRs #87, #89 and #91) composes with the shared progression read. Only the
Dashboard owns an account-wide completion poll, so only it disables the recurring
backstop; an open local, single-player or online game route keeps the backstop
because it observes just its own game. Cadence changes retune the existing poll
instead of restarting it, preserving the shared snapshot across
Dashboard/Portrait Hall transitions. The established Dashboard request budget is
unchanged. Vitest was upgraded to the version `main` now locks.

Achievement family cards remain bordered items inside the single Achievements
surface rather than nested `arena-surface` panels; the design-system note was
corrected to describe that intentional treatment and its avoidance of repeated
panel backgrounds, shadows and Saga Hall framing.

Local checks on the correction head:

- Frontend unit suite: 98 files / 1,414 tests passed. TypeScript, Prettier, the
  production build and the documentation link check passed.
- Python: 3,411 passed, 51 skipped. Six `test_mcts_optimization` frozen-reference
  digest cases fail identically at the reviewed base commit `73d2440` in this
  environment; they are unrelated to these changes and pass in CI. A focused
  progression/ratings/replay-concurrency/security/migration/profile run passed
  (213).
- Playwright: Portrait Hall and achievement tiers across four themes and both
  projects, plus portrait fallback, mobile play and notifications (27 passed,
  one project skip).
- Ruff, Ruff format and mypy (69 modules) passed. The win-streak benchmark was
  regenerated at 25 samples: 3.90 ms full fold versus 0.045 ms tail probe for a
  2,000-game ledger (see the [measurement](../benchmarks/progression-streaks.md)).
