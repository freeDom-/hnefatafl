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

The Dashboard's existing own-account online-history poll now signals progression
refresh when authoritative result totals change. No new timer is added. Repeated
unchanged results do not signal again; notification refreshes coalesce, defer
while hidden, resume on visibility and discard old-account responses. Game Room
completion signals require participation, excluding passive spectators. The
Dashboard regression test runs the real polling and notification hooks together,
observes an opponent completion without focus/navigation, and asserts exactly one
localized milestone notice and one portrait notice.

Administrative rating rebuilds now reconcile only ledger actors in the affected
pool, or all ledger actors for full rebuilds, and only rated criteria. Withdrawn
ledger entries remain in actor selection; current eligibility uses the applied
ledger. Migration reconciliation still covers every account and criterion.

A deterministic query trace with three accounts and ten rated games across all
three pools compares historical reconciliation with a pool rebuild: local/AI
history count queries fall from **6 to 0**. This measures query elimination, not a
wall-clock speedup. Tests cover other-pool-only actors, full rebuilds, corrections,
permanent rewards and repeated reconciliation. No mutable counters or schema
changes are introduced. The earlier manual-claim validation page is explicitly
marked historical and points here.
